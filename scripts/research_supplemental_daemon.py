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
from collections import Counter
from contextlib import ExitStack, contextmanager
from copy import deepcopy
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
from sds200.models import ScannerInfo
from sds200.network import UdpTransport
from sds200.trace import TrafficTrace

MAX_OPPORTUNITIES = 6
WINDOW_SECONDS = 8.0
CONTINUITY_MAX_OPPORTUNITIES = 60
CONTINUITY_WINDOW_SECONDS = 64.0
CONTINUITY_MAX_PSI_GAP = 2.0
TIMING_EVENT_LIMIT = 512

# Diagnostic labels mirror the CLOCK selector, but never make admission decisions.
# Unknown strings are reduced to fixed categories, never copied into evidence.
SCAN_MODES = {
    "trunk_scan": {"Trunk Scan", "Trunk Scan Hold"},
    "conventional_scan": {"Conventional Scan", "Conventional Scan Hold"},
}
EXCLUDED_SCAN_TAGS = frozenset(
    {
        "PopupScreen",
        "PlainText",
        "ReplayDescription",
        "ReplayMode",
        "Button",
        "SrchFrequency",
        "CcHitsChannel",
        "WxChannel",
        "ToneOutChannel",
        "SystemStatus",
        "Analyze",
        "RfPowerPlot",
    }
)
DIAGNOSTIC_TAGS = EXCLUDED_SCAN_TAGS | {
    "System",
    "MonitorList",
    "Site",
    "Department",
    "TGID",
    "ConvFrequency",
    "Property",
}


def scan_context_shape(info):
    """Bounded structural facts only; not a selector and not proof of a radio fault.

    Counts saturate at their documented caps. No attributes, raw XML, arbitrary
    tags, or unrecognized mode/screen/command strings enter this report.
    """
    if not isinstance(info, ScannerInfo):
        return {"schema": 1, "violations": ["not_scanner_info"]}
    counts = Counter(record.tag for record in info.records)
    modes = SCAN_MODES.get(info.screen)
    excluded = EXCLUDED_SCAN_TAGS | {"ConvFrequency" if info.screen == "trunk_scan" else "TGID"}
    violations = []
    if info.command != "PSI":
        violations.append("not_psi")
    if modes is None:
        violations.append("unsupported_screen")
    elif info.mode not in modes:
        violations.append("mode_mismatch")
    if counts["System"] != 1:
        violations.append("system_record_count")
    if any(n > 1 for n in counts.values()):
        violations.append("duplicate_record_tag")
    if excluded.intersection(counts):
        violations.append("excluded_record_tag")
    known_modes = {mode for choices in SCAN_MODES.values() for mode in choices}
    return {
        "schema": 1,
        "violations": violations,
        "command": info.command if info.command in {"PSI", "GSI"} else "other",
        "screen": info.screen if info.screen in SCAN_MODES else "other",
        "mode": info.mode if info.mode in known_modes else "other",
        "system_record_count": min(counts["System"], 2),
        "system_record_count_cap": 2,
        "record_count": min(len(info.records), 256),
        "record_count_cap": 256,
        "duplicate_known_tags": sorted(tag for tag in DIAGNOSTIC_TAGS if counts[tag] > 1),
        "other_duplicate_tag_count": min(
            sum(n > 1 for tag, n in counts.items() if tag not in DIAGNOSTIC_TAGS), 2
        ),
        "other_duplicate_tag_count_cap": 2,
        "excluded_record_tags": sorted(excluded.intersection(counts)),
    }


class TimingTrace:
    """Delegate the existing trace; retain only allowlisted phase/command/time.

    tx is software intent BEFORE the transport write, not wire delivery. rx is
    after UDP decoding, before typed parsing. Neither is a kernel arrival time.
    """

    def __init__(self, original, window):
        self.original, self.window = original, window

    def tx(self, value):
        if value in {"DTM", "FQK"}:
            self.window.record_timing("tx_intent", value)
        self.original.tx(value)

    def rx(self, value):
        command = value[:4]
        if command in {"DTM,", "FQK,"}:
            self.window.record_timing("rx_line", command[:3])
        elif value.rstrip("\r\n") in {"ERR", "NG"}:
            self.window.record_timing("rx_rejection")
        self.original.rx(value)


class ReadWindow:
    """One non-renewable admission budget; no threads or scanner commands."""

    def __init__(self, runtime, firmware, *, clock=monotonic, continuity=False, timing=False):
        DisplayReadResearchPolicy(firmware, DisplayReadKind.CLOCK)
        if type(continuity) is not bool:
            raise ValueError("An explicit boolean continuity policy is required.")
        if type(timing) is not bool or (timing and not continuity):
            raise ValueError("Timing requires an explicit continuity policy.")
        self.continuity = continuity
        self.timing = timing
        self.timing_events = []
        self.timing_overflow = False
        self.timing_poll_active = False
        self.max_opportunities = CONTINUITY_MAX_OPPORTUNITIES if continuity else MAX_OPPORTUNITIES
        self.window_seconds = CONTINUITY_WINDOW_SECONDS if continuity else WINDOW_SECONDS
        self.runtime, self.firmware, self.clock = runtime, firmware, clock
        self.lock = threading.RLock()
        self.attempted = self.closed = False
        self.started = None
        self.failure = None
        self.read_failure = None
        self.scan_rejection = None
        self.opportunities = self.inflight = self.psi_count = self.post_read_psi = 0
        self.latest_psi = None
        self.max_psi_gap = 0.0
        self.replies = {"DTM": 0, "FQK": 0}
        self.cache = self.feed = None

    def stop(self, reason=None):
        with self.lock:
            if not self.closed:
                self.record_timing("closed")
            self.closed = True
            self.failure = self.failure or reason

    def record_timing(self, event, command=None):
        if event not in {
            "armed",
            "closed",
            "scope_enter",
            "scope_exit",
            "cache_complete",
            "context_rejected",
            "tx_intent",
            "rx_line",
            "rx_rejection",
            "parsed_packet",
        } or command not in {None, "DTM", "FQK"}:
            raise ValueError("Timing accepts only reviewed metadata labels.")
        if not self.timing or self.started is None:
            return
        now = self.clock()  # Timestamp before waiting for the short metadata lock.
        with self.lock:
            if len(self.timing_events) >= TIMING_EVENT_LIMIT:
                self.timing_overflow = self.closed = True
                self.failure = self.failure or "timing_overflow"
                return
            self.timing_events.append(
                {"event": event, "command": command, "monotonic_seconds": now}
            )

    @contextmanager
    def trace_scope(self):
        scanner = self.runtime.scanner
        original = scanner.trace
        if type(original) is not TrafficTrace:
            raise ValueError("Timing requires the existing unmodified trace.")
        probe = TimingTrace(original, self)
        scanner.trace = probe
        try:
            yield
        finally:
            if scanner.trace is not probe:
                raise RuntimeError("Timing trace ownership changed; preserve for review.")
            scanner.trace = original

    def timing_report(self):
        with self.lock:
            return {
                "schema": 1,
                "clock": "time.monotonic (same container required for comparison)",
                "origin_monotonic_seconds": self.started,
                "limit": TIMING_EVENT_LIMIT,
                "overflow": self.timing_overflow,
                "poll_active_at_snapshot": self.timing_poll_active,
                "events": [dict(event) for event in self.timing_events],
                "scan_rejection": deepcopy(self.scan_rejection),
                "outgoing_wire_delivery_established": False,
            }

    def observe_connection(self, _connected):
        self.stop("connection_changed")  # Even a rapid false/true pair consumes the trial.

    def observe_psi(self, info):
        with self.lock:
            if self.closed:
                return
            if _selection(info, DisplayReadKind.CLOCK) is None:
                if self.timing:
                    self.scan_rejection = {
                        **scan_context_shape(info),
                        "monotonic_seconds": self.clock(),
                        "before_arm": self.started is None,
                    }
                    # A future selector change must not be explained by stale labels.
                    if not self.scan_rejection["violations"]:
                        self.scan_rejection["violations"] = ["unclassified_rejection"]
                    self.record_timing("context_rejected")
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
            if packet.command in self.replies:
                self.record_timing("parsed_packet", packet.command)
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
            self.record_timing("armed")
            return True

    def allow_poll(self):
        with self.lock:
            return (
                self.started is not None
                and not self.closed
                and self.clock() - self.started < self.window_seconds
                and self.opportunities < self.max_opportunities
            )

    def close_on_read_failure(self):
        """Retain first sanitized cache fault before later scope invalidation.

        This is passive evidence, not another read or permission to retry. In
        particular, a timeout category cannot distinguish wire delay from local
        completion delay and must not be described as either without evidence.
        """
        sample = self.cache.supplemental_snapshot()
        banks, clock = sample.quick_keys, sample.clock
        if not (
            banks.blocked_until_reconnect
            or any(bank.failure for bank in banks.banks)
            or (clock and (clock.failure or clock.blocked_until_reconnect))
        ):
            return False
        with self.lock:
            if self.read_failure is None:
                self.read_failure = {
                    "cache_quarantine": banks.blocked_until_reconnect,
                    "bank_failures": {bank.kind: bank.failure for bank in banks.banks},
                    "clock_failure": None if clock is None else clock.failure,
                    "clock_quarantine": None if clock is None else clock.blocked_until_reconnect,
                }
            self.stop("read_unconfirmed")
        return True

    @contextmanager
    def scope(self, scanner):
        # Runtime acquisition is outside the window lock so stop/callbacks do
        # not contend with the runtime's lifecycle lock or scanner read.
        with self.runtime._supplemental_read_scope(scanner) as available:
            with self.lock:
                allowed = available and self.allow_poll()
                if allowed:
                    if (
                        self.close_on_read_failure()
                        or self.latest_psi is None
                        or self.clock() - self.latest_psi > 1.5
                    ):
                        allowed = False
                    else:
                        self.opportunities += 1
                        self.inflight += 1
                        self.post_read_psi = 0
                        self.record_timing("scope_enter")
                        if self.timing_overflow:
                            self.opportunities -= 1
                            self.inflight -= 1
                            allowed = False
                elif self.allow_poll() and not available:
                    self.stop("runtime_busy")
            try:
                yield allowed
            finally:
                if allowed:
                    with self.lock:
                        self.record_timing("scope_exit")
                        self.inflight -= 1
                        self.post_read_psi = 0

    def report(self):
        with self.lock:
            sample = None if self.cache is None else self.cache.supplemental_snapshot()
            banks = None if sample is None else sample.quick_keys
            clock = None if sample is None else sample.clock
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
                and not self.timing_poll_active
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
                "read_failure": self.read_failure,
                "scan_rejection": deepcopy(self.scan_rejection),
                "timing_overflow": self.timing_overflow,
                "timing_poll_active": self.timing_poll_active,
                "samples_valid": samples_valid,
                "elapsed_seconds": None
                if self.started is None
                else round(self.clock() - self.started, 6),
                "physical_scanning_acceptance": "operator_required",
            }


class SupplementalTrigger(OperatorTrigger):
    def __init__(self, directory, firmware, *, continuity=False, timing=False, **kwargs):
        DisplayReadResearchPolicy(firmware, DisplayReadKind.CLOCK)
        if type(continuity) is not bool:
            raise ValueError("An explicit boolean continuity policy is required.")
        if type(timing) is not bool or (timing and not continuity):
            raise ValueError("Timing requires an explicit continuity policy.")
        self.continuity = continuity
        self.timing = timing
        super().__init__(directory, **kwargs)
        self.firmware, self.window = firmware, None

    def write(self, filename, report):
        super().write(
            filename,
            {
                **report,
                "read_kind": "shared-clock-favorites-timing"
                if self.timing
                else "shared-clock-favorites-continuity"
                if self.continuity
                else "shared-clock-favorites",
                "firmware_pin": self.firmware,
                "max_opportunities": CONTINUITY_MAX_OPPORTUNITIES
                if self.continuity
                else MAX_OPPORTUNITIES,
                "window_seconds": CONTINUITY_WINDOW_SECONDS if self.continuity else WINDOW_SECONDS,
                "max_psi_gap_seconds_allowed": CONTINUITY_MAX_PSI_GAP if self.continuity else None,
                "timing_event_limit": TIMING_EVENT_LIMIT if self.timing else None,
            },
        )

    def cancel(self):
        if self.window is not None:
            self.window.stop("cancelled")
        super().cancel()

    def invoke(self, runtime):
        window = self.window
        if (
            window is None
            or window.runtime is not runtime
            or window.continuity != self.continuity
            or window.timing != self.timing
        ):
            raise ValueError("Review the exact supplemental reader owner.")
        try:
            with ExitStack() as stack:
                if self.timing:
                    stack.enter_context(window.trace_scope())
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
                        window.close_on_read_failure()
                        report = window.report()
                        if (
                            window.closed
                            or report["status"] == "replies_and_psi_observed"
                            or window.clock() - window.started >= window.window_seconds
                        ):
                            break
        finally:
            window.stop()  # Never leave a continuing acquisition loop behind.
            if self.timing:
                self.write("timing.json", window.timing_report())
        return window.report()


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
    parser.add_argument("--timing", action="store_true")
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
        timing=args.timing,
    )
    constructed = False

    class ResearchRuntime(DaemonRuntime):
        def __init__(self, *runtime_args, **runtime_kwargs):
            nonlocal constructed
            if constructed:
                raise RuntimeError("Research launcher must have exactly one daemon owner.")
            constructed = True
            super().__init__(*runtime_args, **runtime_kwargs)
            trigger.window = ReadWindow(
                self, args.expected_firmware, continuity=args.continuity, timing=args.timing
            )
            self._research_trigger_started = False

        def start(self):
            super().start()
            if not self._research_trigger_started:
                self._research_trigger_started = True
                trigger.start(self)

        def stop(self):
            trigger.cancel()
            super().stop()

    class TimingCache(DaemonQuickKeyCache):
        def poll_once(self):
            window = trigger.window
            with window.lock:
                before = window.opportunities
                window.timing_poll_active = True
            try:
                return super().poll_once()
            finally:
                with window.lock:
                    if window.opportunities != before:
                        window.record_timing("cache_complete")
                    window.timing_poll_active = False

    class ResearchFrames(original_frames):
        def __init__(self, profile, scanner, **kwargs):
            window = trigger.window
            if window is None or window.feed is not None or scanner is not window.runtime.scanner:
                raise ValueError("Research requires exactly one feed on the existing owner.")
            if kwargs.get("quick_keys") is not None:
                raise ValueError("An existing supplemental reader cannot be replaced.")
            cached, _, _, _ = profile.frame_context()
            cache_type = TimingCache if args.timing else DaemonQuickKeyCache
            cache = cache_type(
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
