"""Explicit bounded cached-media observer for a separately reviewed research trial.

Not a scanner command client, playback client, recorder, or trial trigger.
Default invocation does nothing. --observe-75s requires a new exclusive evidence
file and an administrator-pinned binding. Only local Unix IPC is supported.
Callers must pin the expected endpoint, runtime start and recording once before
sampling. Only validated counters and timing survive projection. Browser/physical
observations and the research window's own result remain independent gates.
"""

import argparse
import itertools
import json
import os
import socket
import stat
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from time import monotonic, sleep

from sds200.daemon_api import DaemonApiRequest
from sds200.daemon_client import _decode_response, _validate_hello_result

DURATION = 75.0
REQUEST_BUDGET = 0.4
MAX_RESPONSE_BYTES = 262144
MAX_SAMPLES = 75
MAX_REQUESTS = 1 + MAX_SAMPLES * 2
OPERATIONS = frozenset({"hello", "runtime.snapshot", "recording.status"})

RELIABILITY = (
    "packets_lost",
    "duplicate_packets",
    "late_packets",
    "malformed_packets",
    "unexpected_source_packets",
    "ssrc_mismatch_packets",
    "timestamp_discontinuities",
    "receive_errors",
    "callback_errors",
)
PROGRESS = (
    "audio_packets",
    "audio_samples",
    "recording_packets",
    "recording_samples",
    "recording_bytes_written",
)
FAULTS = ("sink_bytes_dropped", "sink_overflows", "sink_callback_statuses") + tuple(
    "network_" + name for name in RELIABILITY
)
COUNTERS = PROGRESS + FAULTS


def refuse():
    raise ValueError("Media evidence is incomplete or inconsistent; preserve it for review.")


def number(value):
    if type(value) not in (int, float) or not 0 <= value <= 2**53 - 1 or not isfinite(value):
        refuse()
    return float(value)


def counter(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        refuse()
    return value


@dataclass(frozen=True)
class Checkpoint:
    started: float
    finished: float
    values: tuple[int, ...]

    def __post_init__(self):
        number(self.started)
        number(self.finished)
        if not 0 <= self.finished - self.started <= 2 or type(self.values) is not tuple:
            refuse()
        if len(self.values) != len(COUNTERS):
            refuse()
        for value in self.values:
            counter(value)


@dataclass(frozen=True)
class ExpectedMedia:
    endpoint: str
    runtime_started: str
    recording: str
    audio_endpoint: str

    def __post_init__(self):
        if any(
            type(value) is not str or not value
            for value in (self.endpoint, self.runtime_started, self.recording, self.audio_endpoint)
        ):
            refuse()

    def project(self, runtime, recording, *, started, finished):
        """Reject mismatches without including input text in errors or output."""
        try:
            audio = runtime["audio"]
            if not (
                runtime["state"] == "running"
                and runtime["scanner_connected"] is True
                and runtime["psi_active"] is True
                and runtime["scanner_endpoint"] == self.endpoint
                and runtime["started_at"] == self.runtime_started
                and runtime["last_error"] is None
                and audio["running"] is True
                and audio["endpoint"] == self.audio_endpoint
                and recording["status"] == "recording"
                and recording["active"] is True
                and recording["closed"] is False
                and recording["error"] is None
                and recording["recording"] == self.recording
            ):
                refuse()
            values = (
                audio["packets"],
                audio["samples"],
                recording["packets"],
                recording["samples"],
                recording["sink"]["bytes_written"],
                recording["sink"]["bytes_dropped"],
                recording["sink"]["overflows"],
                recording["sink"]["callback_statuses"],
                *(recording["reliability"][name] for name in RELIABILITY),
            )
            return Checkpoint(number(started), number(finished), tuple(values))
        except (KeyError, TypeError, AttributeError):
            refuse()


def summarize(checkpoints):
    """Summarize one passive <=75s sequence; never pronounce hardware acceptance.

    No equality of instantaneous fanout/recording counters is assumed: separate
    cached API calls are not atomic. All counters must remain nondecreasing;
    browser playback, recording audibility and physical scanning are unobserved.
    Total progress does not establish uninterrupted delivery between samples.
    """
    if type(checkpoints) not in (tuple, list) or not 2 <= len(checkpoints) <= 160:
        refuse()
    if not all(type(row) is Checkpoint for row in checkpoints):
        refuse()
    first, last = checkpoints[0], checkpoints[-1]
    duration = last.finished - first.started
    if not 0 < duration <= 75:
        refuse()
    max_gap = 0.0
    for before, after in itertools.pairwise(checkpoints):
        if after.started <= before.finished:
            refuse()
        max_gap = max(max_gap, after.started - before.finished)
        if any(b < a for a, b in zip(before.values, after.values, strict=True)):
            refuse()  # Counter reset is not zero loss or a new implicit trial.
    delta = dict(
        zip(COUNTERS, (b - a for a, b in zip(first.values, last.values, strict=True)), strict=True)
    )
    progressed = all(delta[name] > 0 for name in PROGRESS)
    faults = {name: delta[name] for name in FAULTS if delta[name]}
    return {
        "schema": 1,
        "status": "progress_observed" if progressed and not faults else "review_required",
        "samples": len(checkpoints),
        "duration_seconds": duration,
        "max_unsampled_gap_seconds": max_gap,
        "progress_observed": progressed,
        "counter_deltas": delta,
        "fault_deltas": faults,
        "browser_playback": "not_observed",
        "recording_audibility": "not_observed",
        "physical_scanning": "not_observed",
        "research_window_result": "separate_evidence_required",
        "browser_consumer_count": "not_observed",
    }


class CachedMediaClient:
    """One local connection, fixed read-only operations, no retries or threads."""

    def __init__(self, path, *, clock=monotonic, socket_factory=socket.socket):
        if not Path(path).is_absolute() or "\x00" in str(path):
            refuse()
        self.path = str(path)
        self.clock = clock
        self.socket_factory = socket_factory
        self.sock = None
        self.sequence = 0
        self.closed = False

    def close(self):
        self.closed = True
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _remaining(self, deadline):
        remaining = deadline - self.clock()
        if not 0 < remaining <= REQUEST_BUDGET + 1e-8:
            raise TimeoutError
        return min(remaining, REQUEST_BUDGET)

    def request(self, operation, window_deadline):
        try:
            if self.closed or operation not in OPERATIONS or self.sequence >= MAX_REQUESTS:
                refuse()
            deadline = min(number(window_deadline), self.clock() + REQUEST_BUDGET)
            self._remaining(deadline)
            if self.sock is None:
                self.sock = self.socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(self._remaining(deadline))
                self.sock.connect(self.path)
            self.sequence += 1
            request_id = f"media-{self.sequence}"
            request = DaemonApiRequest(request_id, operation).as_dict()
            self.sock.settimeout(self._remaining(deadline))
            self.sock.sendall((json.dumps(request) + "\n").encode())
            received = bytearray()
            while True:
                self.sock.settimeout(self._remaining(deadline))
                chunk = self.sock.recv(min(4096, MAX_RESPONSE_BYTES + 1 - len(received)))
                self._remaining(deadline)  # Late completion is not timely evidence.
                if not chunk:
                    refuse()
                received.extend(chunk)
                if len(received) > MAX_RESPONSE_BYTES:
                    refuse()
                newline = received.find(b"\n")
                if newline >= 0:
                    if newline != len(received) - 1:
                        refuse()
                    result = _decode_response(
                        bytes(received[:newline]), expected_request_id=request_id
                    )
                    self._remaining(deadline)
                    return result
        except Exception:
            self.close()  # Never reconnect after an uncertain exchange.
            raise ValueError("Cached media request failed; no retry was attempted.") from None


def collect(client, expected, *, clock=monotonic, wait=sleep, emit=lambda _row: None):
    """Observe one fixed window; readiness is informational, never a trigger."""
    started = number(clock())
    deadline = started + DURATION
    rows = []
    ready = False
    reason = None
    try:
        hello = client.request("hello", deadline)
        _validate_hello_result(hello)
        if not set(hello["read_only_operations"]) >= OPERATIONS:
            refuse()
        while clock() + 2 * REQUEST_BUDGET < deadline and len(rows) < MAX_SAMPLES:
            acquired = number(clock())
            runtime = client.request("runtime.snapshot", deadline)
            recording = client.request("recording.status", deadline)
            row = expected.project(runtime, recording, started=acquired, finished=clock())
            if row.finished >= deadline:
                refuse()
            rows.append(row)
            report = summarize(rows) if len(rows) > 1 else None
            emit(
                {
                    "checkpoint": {
                        "started": row.started,
                        "finished": row.finished,
                        "values": list(row.values),
                    }
                }
            )
            if not ready and report is not None:
                if report["status"] != "progress_observed":
                    refuse()
                ready = True
                emit(
                    {
                        "media_ready": True,
                        "monotonic_seconds": row.finished,
                        "automatically_triggers_trial": False,
                    }
                )
            remaining = deadline - clock()
            if remaining > 0:
                wait(min(1.0, remaining))
        if not ready:
            refuse()
    except Exception:
        reason = "observation_incomplete"
    finally:
        client.close()
    try:
        report = summarize(rows)
    except ValueError:
        report = {"status": "review_required", "samples": len(rows)}
        reason = "observation_incomplete"
    elapsed = number(clock()) - started
    if elapsed > DURATION + 0.1:
        reason = "observer_overrun"
    report.update(
        {
            "schema": 1,
            "complete": reason is None,
            "stop_reason": reason,
            "start_monotonic_seconds": started,
            "elapsed_seconds": elapsed,
            "clock": "time.monotonic; compare only in the same time namespace",
            "cache_requests_attempted": client.sequence,
            "scanner_commands_sent": 0,
            "media_started_or_stopped": False,
            "browser_playback": "not_observed",
            "browser_consumer_count": "not_observed",
            "recording_audibility": "not_observed",
            "physical_scanning": "not_observed",
            "research_window_result": "separate_evidence_required",
            "psi_freshness": "separate_evidence_required",
        }
    )
    if reason is not None:
        report["status"] = "review_required"
    return report


def read_binding(path):
    """A small private regular file; no credentials, inference, or auto-enrollment."""
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            refuse()
        data = source.read(8193)
    if len(data) > 8192:
        refuse()
    binding = json.loads(data)
    if type(binding) is not dict or set(binding) != {
        "endpoint",
        "runtime_started",
        "recording",
        "audio_endpoint",
    }:
        refuse()
    return ExpectedMedia(**binding)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe-75s", action="store_true", required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not all(path.is_absolute() for path in (args.socket, args.binding, args.evidence)):
            refuse()
        binding = read_binding(args.binding)
        # The containing directory is a freshly reviewed private case directory.
        parent = args.evidence.parent.stat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o077
        ):
            refuse()
        fd = os.open(args.evidence, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as output:

            def emit(row):
                output.write(json.dumps(row, allow_nan=False) + "\n")
                output.flush()

            emit(
                {"schema": 1, "intent": "one_cached_media_observation", "duration_limit": DURATION}
            )
            os.fsync(output.fileno())  # Persist intent before any connection.
            result = collect(CachedMediaClient(args.socket), binding, emit=emit)
            emit({"summary": result})
            os.fsync(output.fileno())
        print(json.dumps({"complete": result["complete"], "status": result["status"]}))
        return 0 if result["complete"] and result["status"] == "progress_observed" else 1
    except Exception:
        print('{"complete": false, "status": "review_required", "reason": "observer_refused"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
