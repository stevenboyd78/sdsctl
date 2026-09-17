#!/usr/bin/env python3
"""Temporary bounded shared-reader qualification; no signal means no extra GETs.

Only the existing display worker schedules reads. The trigger thread observes
its results, never calls poll_once/execute, and cannot rearm the window. No
supplemental samples are rendered or exposed over the daemon API.
"""

from __future__ import annotations

import argparse
import signal
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path
from time import monotonic

from research_system_status_daemon import OperatorTrigger

from sds200 import cli, daemon_display_frames
from sds200.daemon_display_read_research import (
    DisplayReadKind,
    DisplayReadResearchPolicy,
    _selection,
)
from sds200.daemon_quick_keys import DaemonQuickKeyCache
from sds200.daemon_runtime import DaemonRuntime, DaemonRuntimeState
from sds200.network import UdpTransport

MAX_OPPORTUNITIES = 6
WINDOW_SECONDS = 8.0
CONTINUITY_MAX_OPPORTUNITIES = 60
CONTINUITY_WINDOW_SECONDS = 64.0
CONTINUITY_MAX_PSI_GAP = 2.0


class ReadWindow:
    """One non-renewable admission budget; no threads or scanner commands."""

    def __init__(self, runtime, firmware, *, clock=monotonic, continuity=False):
        DisplayReadResearchPolicy(firmware, DisplayReadKind.CLOCK)
        if type(continuity) is not bool:
            raise ValueError("An explicit boolean continuity policy is required.")
        self.continuity = continuity
        self.max_opportunities = CONTINUITY_MAX_OPPORTUNITIES if continuity else MAX_OPPORTUNITIES
        self.window_seconds = CONTINUITY_WINDOW_SECONDS if continuity else WINDOW_SECONDS
        self.runtime, self.firmware, self.clock = runtime, firmware, clock
        self.lock = threading.RLock()
        self.attempted = self.closed = False
        self.started = None
        self.failure = None
        self.opportunities = self.inflight = self.psi_count = self.post_read_psi = 0
        self.latest_psi = None
        self.max_psi_gap = 0.0
        self.replies = {"DTM": 0, "FQK": 0}
        self.cache = self.feed = None

    def stop(self, reason=None):
        with self.lock:
            self.closed = True
            self.failure = self.failure or reason

    def observe_connection(self, _connected):
        self.stop("connection_changed")  # Even a rapid false/true pair consumes the trial.

    def observe_psi(self, info):
        with self.lock:
            if self.closed:
                return
            if _selection(info, DisplayReadKind.CLOCK) is None:
                self.stop("scan_context_changed")
                return
            now = self.clock()
            if self.latest_psi is not None:
                self.max_psi_gap = max(self.max_psi_gap, now - self.latest_psi)
                if self.continuity and self.max_psi_gap > CONTINUITY_MAX_PSI_GAP:
                    self.stop("psi_gap_exceeded")
                    return
            self.latest_psi = now
            self.psi_count += 1
            if self.opportunities and not self.inflight:
                self.post_read_psi += 1

    def observe_packet(self, packet):
        with self.lock:
            if not self.closed and packet.command in self.replies:
                self.replies[packet.command] += 1

    def arm(self):
        with self.lock:
            if self.attempted or self.closed:
                raise ValueError("Supplemental qualification cannot be rearmed.")
            self.attempted = True
            snapshot = self.runtime.snapshot()
            if (
                snapshot.state is not DaemonRuntimeState.RUNNING
                or snapshot.scanner_model != "SDS200"
                or snapshot.scanner_firmware != self.firmware
                or not snapshot.scanner_connected
                or not snapshot.psi_active
                or type(self.runtime.scanner.transport) is not UdpTransport
                or self.runtime._system_status_research is not None
                or self.runtime._display_read_research is not None
                or self.cache is None
                or self.feed is None
            ):
                self.stop("preflight_refused")
                return False
            self.started = self.clock()
            return True

    def allow_poll(self):
        with self.lock:
            return (
                self.started is not None
                and not self.closed
                and self.clock() - self.started < self.window_seconds
                and self.opportunities < self.max_opportunities
            )

    @contextmanager
    def scope(self, scanner):
        # Runtime acquisition is outside the window lock so stop/callbacks do
        # not contend with the runtime's lifecycle lock or scanner read.
        with self.runtime._supplemental_read_scope(scanner) as available:
            with self.lock:
                allowed = available and self.allow_poll()
                if allowed:
                    state = self.cache.snapshot()
                    clock = self.cache.clock_snapshot()
                    if (
                        state.blocked_until_reconnect
                        or any(b.failure for b in state.banks)
                        or (clock and clock.failure)
                    ):
                        self.stop("read_unconfirmed")
                        allowed = False
                    elif self.latest_psi is None or self.clock() - self.latest_psi > 1.5:
                        allowed = False
                    else:
                        self.opportunities += 1
                        self.inflight += 1
                        self.post_read_psi = 0
                elif self.allow_poll() and not available:
                    self.stop("runtime_busy")
            try:
                yield allowed
            finally:
                if allowed:
                    with self.lock:
                        self.inflight -= 1
                        self.post_read_psi = 0

    def report(self):
        with self.lock:
            banks = None if self.cache is None else self.cache.snapshot()
            clock = None if self.cache is None else self.cache.clock_snapshot()
            favorites = None if banks is None else banks.banks[0]
            samples_valid = bool(
                banks
                and banks.active
                and not banks.blocked_until_reconnect
                and favorites.states is not None
                and favorites.failure is None
                and clock
                and clock.age_seconds is not None
                and clock.failure is None
                and not clock.blocked_until_reconnect
            )
            passed = (
                samples_valid
                and self.failure is None
                and self.opportunities == self.max_opportunities
                and self.inflight == 0
                and self.replies
                == {"DTM": self.max_opportunities // 2, "FQK": self.max_opportunities // 2}
                and self.post_read_psi >= 2
            )
            return {
                "status": "replies_and_psi_observed" if passed else "qualification_unconfirmed",
                "research_started": self.started is not None,
                "read_opportunities": self.opportunities,
                "inflight": self.inflight,
                "reply_counts": dict(self.replies),
                "normal_psi_count": self.psi_count,
                "normal_psi_after_last_read": self.post_read_psi,
                "max_psi_gap_seconds": round(self.max_psi_gap, 6),
                "failure": self.failure,
                "samples_valid": samples_valid,
                "elapsed_seconds": None
                if self.started is None
                else round(self.clock() - self.started, 6),
                "physical_scanning_acceptance": "operator_required",
            }


class SupplementalTrigger(OperatorTrigger):
    def __init__(self, directory, firmware, *, continuity=False, **kwargs):
        DisplayReadResearchPolicy(firmware, DisplayReadKind.CLOCK)
        if type(continuity) is not bool:
            raise ValueError("An explicit boolean continuity policy is required.")
        self.continuity = continuity
        super().__init__(directory, **kwargs)
        self.firmware, self.window = firmware, None

    def write(self, filename, report):
        super().write(
            filename,
            {
                **report,
                "read_kind": "shared-clock-favorites-continuity"
                if self.continuity
                else "shared-clock-favorites",
                "firmware_pin": self.firmware,
                "max_opportunities": CONTINUITY_MAX_OPPORTUNITIES
                if self.continuity
                else MAX_OPPORTUNITIES,
                "window_seconds": CONTINUITY_WINDOW_SECONDS if self.continuity else WINDOW_SECONDS,
                "max_psi_gap_seconds_allowed": CONTINUITY_MAX_PSI_GAP if self.continuity else None,
            },
        )

    def cancel(self):
        if self.window is not None:
            self.window.stop("cancelled")
        super().cancel()

    def invoke(self, runtime):
        window = self.window
        if window is None or window.runtime is not runtime or window.continuity != self.continuity:
            raise ValueError("Review the exact supplemental reader owner.")
        try:
            with ExitStack() as stack:
                # Passive subscriptions only, registered before preflight/arming.
                for register, callback in (
                    (runtime.scanner.on_connection, window.observe_connection),
                    (runtime.scanner.on_psi, window.observe_psi),
                    (runtime.scanner.on_packet, window.observe_packet),
                ):
                    stack.callback(register(callback))
                if not window.closed and window.arm():
                    while not self._cancel.wait(0.05):
                        # Explicit test demand; snapshot itself never sends a GET.
                        frame = window.feed.snapshot()
                        if frame["failure"] is not None:
                            window.stop("display_context_changed")
                        worker = window.feed.quick_key_worker_status()
                        if worker is None or worker.stopped or worker.failure is not None:
                            window.stop("worker_unavailable")
                        state = window.cache.snapshot()
                        clock = window.cache.clock_snapshot()
                        if (
                            state.blocked_until_reconnect
                            or any(b.failure for b in state.banks)
                            or (clock and clock.failure)
                        ):
                            window.stop("read_unconfirmed")
                        report = window.report()
                        if (
                            window.closed
                            or report["status"] == "replies_and_psi_observed"
                            or window.clock() - window.started >= window.window_seconds
                        ):
                            break
                return window.report()
        finally:
            window.stop()  # Never leave a continuing acquisition loop behind.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-firmware", required=True)
    parser.add_argument("--evidence-directory", type=Path, required=True)
    parser.add_argument("--ready-timeout", type=float, default=600)
    parser.add_argument(
        "--continuity",
        action="store_true",
        help="Separate 60-read/64-second scanning-only case; never rearm the short case.",
    )
    parser.add_argument("daemon_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.daemon_args or args.daemon_args[0] != "--":
        parser.error("Normal daemon arguments must follow --.")
    daemon_args = args.daemon_args[1:]
    parsed = cli.build_parser(suppress_configuration_defaults=True).parse_args(daemon_args)
    if parsed.action != "daemon":
        parser.error("Only the single-owner daemon may use this temporary launcher.")
    if cli.DaemonRuntime is not DaemonRuntime:
        raise RuntimeError("Review the existing daemon owner before enabling research.")
    original_frames = daemon_display_frames.DaemonDisplayFrames
    original_signal = signal.getsignal(signal.SIGUSR1)
    trigger = SupplementalTrigger(
        args.evidence_directory,
        args.expected_firmware,
        ready_timeout=args.ready_timeout,
        continuity=args.continuity,
    )
    constructed = False

    class ResearchRuntime(DaemonRuntime):
        def __init__(self, *runtime_args, **runtime_kwargs):
            nonlocal constructed
            if constructed:
                raise RuntimeError("Research launcher must have exactly one daemon owner.")
            constructed = True
            super().__init__(*runtime_args, **runtime_kwargs)
            trigger.window = ReadWindow(self, args.expected_firmware, continuity=args.continuity)
            self._research_trigger_started = False

        def start(self):
            super().start()
            if not self._research_trigger_started:
                self._research_trigger_started = True
                trigger.start(self)

        def stop(self):
            trigger.cancel()
            super().stop()

    class ResearchFrames(original_frames):
        def __init__(self, profile, scanner, **kwargs):
            window = trigger.window
            if window is None or window.feed is not None or scanner is not window.runtime.scanner:
                raise ValueError("Research requires exactly one feed on the existing owner.")
            if kwargs.get("quick_keys") is not None:
                raise ValueError("An existing supplemental reader cannot be replaced.")
            cached, _, _, _ = profile.frame_context()
            cache = DaemonQuickKeyCache(
                scanner,
                cached.endpoint_id,
                profile.scanner_target,
                include_clock=True,
                allow_scoped_reads=False,
                read_scope=window.scope,
            )
            kwargs["quick_keys"] = cache
            super().__init__(profile, scanner, **kwargs)
            window.cache, window.feed = cache, self
            self._window = window

        def _allow_quick_keys(self):
            # Preserve the final samples for the report after the quota; do not
            # schedule a seventh rejected read which would clear the cache.
            return self._window.allow_poll() and super()._allow_quick_keys()

    try:
        signal.signal(signal.SIGUSR1, trigger.signal)
        cli.DaemonRuntime = ResearchRuntime
        daemon_display_frames.DaemonDisplayFrames = ResearchFrames
        return cli.main(daemon_args)
    finally:
        trigger.cancel()
        try:
            trigger.join()
        finally:
            cli.DaemonRuntime = DaemonRuntime
            daemon_display_frames.DaemonDisplayFrames = original_frames
            signal.signal(signal.SIGUSR1, original_signal)


if __name__ == "__main__":
    raise SystemExit(main())
