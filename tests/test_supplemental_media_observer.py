"""Synthetic projection and bounded local-IPC tests; never contact hardware."""

import importlib.util
import json
import sys
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from .test_supplemental_native_lifecycle import configured as configured
from .test_supplemental_native_lifecycle import rig as rig
from .test_supplemental_native_lifecycle import wait_for
from .test_supplemental_native_pcmu import network_rig as network_rig

PATH = Path(__file__).resolve().parents[1] / "scripts/observe_supplemental_media.py"
SPEC = importlib.util.spec_from_file_location("supplemental_media", PATH)
observer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = observer
SPEC.loader.exec_module(observer)
COUNTERS, FAULTS, RELIABILITY = observer.COUNTERS, observer.FAULTS, observer.RELIABILITY
Checkpoint, ExpectedMedia, summarize = (
    observer.Checkpoint,
    observer.ExpectedMedia,
    observer.summarize,
)


@pytest.fixture
def sample():
    expected = ExpectedMedia(
        "PRIVATE_ENDPOINT", "PRIVATE_RUNTIME_START", "PRIVATE_RECORDING", "PRIVATE_AUDIO"
    )
    runtime = {
        "state": "running",
        "scanner_connected": True,
        "psi_active": True,
        "scanner_endpoint": expected.endpoint,
        "started_at": expected.runtime_started,
        "last_error": None,
        "audio": {
            "running": True,
            "packets": 10,
            "samples": 1600,
            "endpoint": expected.audio_endpoint,
        },
        "arbitrary_private": "MUST_NOT_LEAK",
    }
    recording = {
        "status": "recording",
        "active": True,
        "closed": False,
        "error": None,
        "recording": expected.recording,
        "packets": 9,
        "samples": 1440,
        "sink": {
            "bytes_written": 2880,
            "bytes_dropped": 0,
            "overflows": 0,
            "callback_statuses": 0,
        },
        "reliability": dict.fromkeys(RELIABILITY, 0),
        "credential": "MUST_NOT_LEAK",
    }
    return expected, runtime, recording


def projected(sample, started=10, finished=10.05):
    expected, runtime, recording = sample
    return expected.project(runtime, recording, started=started, finished=finished)


def advanced(row, *, started=11, finished=11.05):
    values = tuple(value + (1 if i < 5 else 0) for i, value in enumerate(row.values))
    return Checkpoint(started, finished, values)


def test_counter_progress_is_not_physical_or_browser_acceptance(sample):
    before = projected(sample)
    report = summarize([before, advanced(before)])
    assert report["status"] == "progress_observed"
    assert report["physical_scanning"] == report["browser_playback"] == "not_observed"
    assert report["recording_audibility"] == "not_observed"
    assert report["research_window_result"] == "separate_evidence_required"
    serialized = json.dumps(report)
    assert "PRIVATE" not in serialized and "MUST_NOT_LEAK" not in serialized
    with pytest.raises(FrozenInstanceError):
        before.started = 0


@pytest.mark.parametrize("value", [True, False, -1, 1.0, "1", None, float("nan"), 2**63])
def test_malformed_counters_are_not_coerced(sample, value):
    sample[1]["audio"]["samples"] = value
    with pytest.raises(ValueError, match="preserve"):
        projected(sample)


@pytest.mark.parametrize(
    "section,key,value",
    [
        (1, "state", "stopped"),
        (1, "scanner_connected", 1),
        (1, "psi_active", False),
        (1, "scanner_endpoint", "OTHER_PRIVATE_ENDPOINT"),
        (1, "started_at", "OTHER_START"),
        (1, "last_error", "PRIVATE_ERROR"),
        (2, "status", "starting"),
        (2, "active", 1),
        (2, "closed", 0),
        (2, "error", "PRIVATE_ERROR"),
        (2, "recording", "OTHER_PRIVATE_RECORDING"),
    ],
)
def test_identity_state_or_error_change_refused_without_echo(sample, section, key, value):
    sample[section][key] = value
    with pytest.raises(ValueError) as error:
        projected(sample)
    assert "PRIVATE" not in str(error.value) and "OTHER" not in str(error.value)


@pytest.mark.parametrize("key", RELIABILITY)
def test_missing_reliability_is_not_zero(sample, key):
    del sample[2]["reliability"][key]
    with pytest.raises(ValueError):
        projected(sample)


@pytest.mark.parametrize("name", FAULTS)
def test_each_fault_delta_requires_review(sample, name):
    first = projected(sample)
    second = advanced(first)
    values = list(second.values)
    values[COUNTERS.index(name)] += 1
    report = summarize([first, replace(second, values=tuple(values))])
    assert report["status"] == "review_required"
    assert report["fault_deltas"] == {name: 1}


def test_stalled_recording_or_counter_reset_cannot_pass(sample):
    first = projected(sample)
    stalled = replace(first, started=11, finished=11.05)
    assert summarize([first, stalled])["status"] == "review_required"
    reset = replace(stalled, values=(0, *stalled.values[1:]))
    with pytest.raises(ValueError):
        summarize([first, reset])


@pytest.mark.parametrize(
    "start,end",
    [
        (True, 1),
        (1, None),
        (float("nan"), 2),
        (1, float("inf")),
        (-1, 0),
        (2, 1),
        (1, 3.01),
        (10**1000, 10**1000),
        (2**53, 2**53),
        ("PRIVATE_TIME", 1),
    ],
)
def test_bad_or_unbounded_acquisition_timing_refused(sample, start, end):
    with pytest.raises(ValueError):
        projected(sample, start, end)


@pytest.mark.parametrize("kind", ["single", "too_many", "reordered", "overlap", "long"])
def test_ambiguous_or_extended_timeline_refused(sample, kind):
    first = projected(sample)
    second = advanced(first)
    rows = {
        "single": [first],
        "too_many": [first] * 161,
        "reordered": [second, first],
        "overlap": [first, replace(second, started=first.finished)],
        "long": [first, replace(second, started=86, finished=86.05)],
    }[kind]
    with pytest.raises(ValueError):
        summarize(rows)


class Clock:
    now = 100.0

    def __call__(self):
        return self.now

    def wait(self, duration):
        assert 0 < duration <= 1
        self.now += duration


def growing_api(sample):
    from sds200.daemon_api import DaemonReadOnlyApi

    _, runtime, recording = sample

    class Runtime:
        def snapshot(self):
            runtime["audio"]["packets"] += 1
            runtime["audio"]["samples"] += 160
            return SimpleNamespace(as_dict=lambda: deepcopy(runtime))

    class Recording:
        def snapshot(self):
            recording["packets"] += 1
            recording["samples"] += 160
            recording["sink"]["bytes_written"] += 320
            return SimpleNamespace(as_dict=lambda: deepcopy(recording))

        def start_recording(self):
            raise AssertionError("Observer must never start recording")

        def stop_recording(self):
            raise AssertionError("Observer must never stop recording")

        def list_recordings(self):
            raise AssertionError("Observer must never enumerate recordings")

    return DaemonReadOnlyApi(Runtime(), recording_manager=Recording())


class FixtureClient:
    def __init__(self, sample, clock):
        self.api = growing_api(sample)
        self.clock = clock
        self.calls = []
        self.sequence = 0
        self.closed = False

    def request(self, operation, deadline):
        assert self.clock() < deadline and not self.closed
        self.calls.append(operation)
        self.sequence += 1
        result = self.api.handle_payload(
            observer.DaemonApiRequest(str(self.sequence), operation).as_dict()
        )
        assert result.error is None and result.result is not None
        return dict(result.result)

    def close(self):
        self.closed = True


def test_complete_bounded_sequence_has_one_ready_and_no_unobserved_acceptance(sample):
    clock = Clock()
    client = FixtureClient(sample, clock)
    output = []
    report = observer.collect(client, sample[0], clock=clock, wait=clock.wait, emit=output.append)
    assert report["complete"] and report["status"] == "progress_observed"
    assert report["samples"] == 75 and report["elapsed_seconds"] == 75
    assert client.sequence == 151 and client.closed
    assert client.calls == ["hello"] + ["runtime.snapshot", "recording.status"] * 75
    assert len([row for row in output if row.get("media_ready")]) == 1
    assert report["browser_consumer_count"] == report["browser_playback"] == "not_observed"
    assert report["psi_freshness"] == "separate_evidence_required"
    assert "PRIVATE" not in json.dumps([output, report]) and "MUST_NOT_LEAK" not in json.dumps(
        output
    )


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 20, 149, 151])
def test_failed_request_never_retries_or_marks_complete(sample, stage):
    clock = Clock()
    client = FixtureClient(sample, clock)
    original = client.request

    def failing(operation, deadline):
        if client.sequence + 1 == stage:
            client.sequence += 1
            raise RuntimeError("PRIVATE_ERROR")
        return original(operation, deadline)

    client.request = failing
    report = observer.collect(client, sample[0], clock=clock, wait=clock.wait)
    assert not report["complete"] and report["status"] == "review_required"
    assert client.sequence == stage and client.closed
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize(
    "change", ["audio_identity", "reset", "stopped", "missing", "clock_overrun"]
)
def test_changes_during_collection_fail_closed_without_new_baseline(sample, change):
    clock = Clock()
    client = FixtureClient(sample, clock)
    original = client.request

    def changed(operation, deadline):
        result = original(operation, deadline)
        if client.sequence == 10:  # Already ready, later runtime snapshot.
            if change == "audio_identity":
                result["audio"]["endpoint"] = "OTHER_PRIVATE_AUDIO"
            elif change == "reset":
                result["audio"]["samples"] = 0
            elif change == "stopped":
                result["state"] = "stopped"
            elif change == "missing":
                del result["audio"]
            else:
                clock.now += 80
        return result

    client.request = changed
    report = observer.collect(client, sample[0], clock=clock, wait=clock.wait)
    assert not report["complete"] and report["status"] == "review_required"
    assert client.closed and client.sequence <= 11
    assert "PRIVATE" not in json.dumps(report)


def test_sink_loss_after_ready_is_retained_and_not_a_passing_result(sample):
    clock = Clock()
    client = FixtureClient(sample, clock)
    original = client.request

    def lossy(operation, deadline):
        if client.sequence == 10:
            sample[2]["sink"]["bytes_dropped"] += 320
        return original(operation, deadline)

    client.request = lossy
    report = observer.collect(client, sample[0], clock=clock, wait=clock.wait)
    assert report["complete"] and report["status"] == "review_required"
    assert report["fault_deltas"] == {"sink_bytes_dropped": 320}


class SocketFixture:
    def __init__(self, clock, chunks, delays=None):
        self.clock, self.chunks = clock, list(chunks)
        self.delays = list(delays or [0] * len(chunks))
        self.sent = []
        self.timeouts = []
        self.closed = False

    def settimeout(self, timeout):
        assert 0 < timeout <= observer.REQUEST_BUDGET
        self.timeouts.append(timeout)

    def connect(self, path):
        assert path == "/fictional/media.sock"

    def sendall(self, data):
        self.sent.append(json.loads(data))

    def recv(self, size):
        assert 0 < size <= 4096
        self.clock.now += self.delays.pop(0)
        chunk = self.chunks.pop(0)
        assert len(chunk) <= size
        return chunk

    def close(self):
        self.closed = True


def envelope(result=None, **changes):
    value = {
        "protocol": "sdsctl.daemon",
        "version": 1,
        "request_id": "media-1",
        "ok": True,
        "result": {} if result is None else result,
    }
    value.update(changes)
    return (json.dumps(value) + "\n").encode()


def socket_client(clock, sock):
    def factory(family, kind):
        assert family == observer.socket.AF_UNIX and kind == observer.socket.SOCK_STREAM
        return sock

    return observer.CachedMediaClient("/fictional/media.sock", clock=clock, socket_factory=factory)


def test_partial_response_uses_one_absolute_deadline():
    clock = Clock()
    sock = SocketFixture(clock, [b"{", b" ", b" "], [0.15, 0.15, 0.15])
    client = socket_client(clock, sock)
    with pytest.raises(ValueError, match="no retry"):
        client.request("runtime.snapshot", clock() + 75)
    assert clock.now == pytest.approx(100.45)  # Fixture deliberately completes a late recv.
    assert sock.timeouts[-1] == pytest.approx(0.1)
    assert client.closed and sock.closed and len(sock.sent) == 1
    with pytest.raises(ValueError):
        client.request("runtime.snapshot", clock() + 75)
    assert len(sock.sent) == 1


def test_request_respects_shorter_remaining_window_and_late_complete_frame():
    clock = Clock()
    sock = SocketFixture(clock, [envelope()], [0.11])
    client = socket_client(clock, sock)
    with pytest.raises(ValueError):
        client.request("runtime.snapshot", clock() + 0.1)
    assert max(sock.timeouts) <= 0.1 + 1e-8 and sock.closed


@pytest.mark.parametrize(
    "operation",
    [
        "recording.start",
        "recording.stop",
        "display.frame",
        "scanner.state",
        "scanner.volume_set",
        "AST",
        "FQK",
        "DTM",
    ],
)
def test_client_refuses_every_non_observer_operation_before_connect(operation):
    clock = Clock()
    sock = SocketFixture(clock, [])
    client = socket_client(clock, sock)
    with pytest.raises(ValueError):
        client.request(operation, clock() + 75)
    assert not sock.sent and client.sock is None


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"\xff\n",
        b"{}\n",
        b"[]\n",
        b"{}\n{}\n",
        envelope(request_id="wrong"),
        envelope(version=True),
        envelope(ok="true"),
        envelope(protocol="PRIVATE_PROTOCOL"),
        envelope(result=[]),
    ],
)
def test_malformed_or_mismatched_response_closes_without_echo(body):
    clock = Clock()
    sock = SocketFixture(clock, [body])
    client = socket_client(clock, sock)
    with pytest.raises(ValueError) as error:
        client.request("runtime.snapshot", clock() + 75)
    assert sock.closed and "PRIVATE" not in str(error.value)


def test_actual_local_api_uses_one_connection_and_no_mutating_operations(sample, tmp_path):
    from sds200.daemon_ipc import DaemonSocketListener, DaemonSocketLocation, DaemonSocketSource
    from sds200.daemon_server import DaemonApiServer

    api = growing_api(sample)
    calls = []

    class TracedApi:
        def handle_json_line(self, data):
            request = json.loads(data)
            calls.append(request["operation"])
            assert request["params"] == {}
            assert request["operation"] in observer.OPERATIONS
            return api.handle_json_line(data)

    path = tmp_path / "media.sock"
    server = DaemonApiServer(
        DaemonSocketListener(DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)),
        TracedApi(),
        accept_poll_interval=0.01,
    )
    clock = Clock()
    client = observer.CachedMediaClient(path, clock=clock)
    with server:
        report = observer.collect(client, sample[0], clock=clock, wait=clock.wait)
    assert report["complete"] and report["status"] == "progress_observed"
    assert calls == ["hello"] + ["runtime.snapshot", "recording.status"] * 75
    assert server.snapshot().accepted_clients == 1
    assert not server.active and client.closed


@pytest.mark.parametrize("value", [None, "", 1, True, [], {}])
def test_invalid_expected_identity_refused(value):
    with pytest.raises(ValueError):
        ExpectedMedia("endpoint", "start", value, "audio")


@pytest.mark.parametrize("phase", ["connect", "send"])
def test_connect_and_send_share_reply_deadline(phase):
    clock = Clock()
    sock = SocketFixture(clock, [envelope()], [0.15])
    original = sock.connect if phase == "connect" else sock.sendall

    def slow(value):
        clock.now += 0.3
        return original(value)

    if phase == "connect":
        sock.connect = slow
    else:
        sock.sendall = slow
    client = socket_client(clock, sock)
    with pytest.raises(ValueError):
        client.request("runtime.snapshot", clock() + 75)
    assert sock.timeouts[-1] == pytest.approx(0.1)
    assert sock.closed and len(sock.sent) == 1


def test_oversized_response_and_request_limit_are_independently_bounded():
    clock = Clock()
    chunks = [b"x" * 4096] * 64 + [b"x"]
    sock = SocketFixture(clock, chunks)
    client = socket_client(clock, sock)
    with pytest.raises(ValueError):
        client.request("runtime.snapshot", clock() + 75)
    assert sock.closed and not sock.chunks and len(sock.sent) == 1
    limited = socket_client(clock, SocketFixture(clock, []))
    limited.sequence = observer.MAX_REQUESTS
    with pytest.raises(ValueError):
        limited.request("runtime.snapshot", clock() + 75)
    assert limited.sock is None and limited.sequence == observer.MAX_REQUESTS


@pytest.mark.parametrize("kind", ["missing_capability", "stalled", "preflight_loss"])
def test_no_readiness_without_healthy_established_media(sample, kind):
    clock = Clock()
    client = FixtureClient(sample, clock)
    original = client.request

    def changed(operation, deadline):
        result = original(operation, deadline)
        if kind == "missing_capability" and operation == "hello":
            result["read_only_operations"].remove("recording.status")
        if client.sequence == 5:
            if kind == "stalled":
                result["sink"]["bytes_written"] -= 320
            elif kind == "preflight_loss":
                result["sink"]["overflows"] += 1
        return result

    client.request = changed
    output = []
    report = observer.collect(client, sample[0], clock=clock, wait=clock.wait, emit=output.append)
    assert not report["complete"] and not any(row.get("media_ready") for row in output)
    assert client.closed and client.sequence <= 5


def binding_file(sample, tmp_path):
    path = tmp_path / "binding.json"
    path.write_text(json.dumps(vars(sample[0])))
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("kind", ["readable", "symlink", "fifo", "oversized", "extra", "invalid"])
def test_private_binding_rejects_unsafe_or_unexpected_input(sample, tmp_path, kind):
    path = binding_file(sample, tmp_path)
    if kind == "readable":
        path.chmod(0o644)
    elif kind == "symlink":
        alternate = tmp_path / "link"
        alternate.symlink_to(path)
        path = alternate
    elif kind == "fifo":
        import os

        path = tmp_path / "fifo"
        os.mkfifo(path, 0o600)
    elif kind == "oversized":
        path.write_bytes(b" " * 8193)
    elif kind == "extra":
        path.write_text(json.dumps({**vars(sample[0]), "credential": "PRIVATE_CREDENTIAL"}))
    else:
        path.write_text("{PRIVATE_INVALID")
    with pytest.raises((ValueError, OSError)):
        observer.read_binding(path)


def test_cli_requires_explicit_invocation_and_help_is_inert(monkeypatch, capsys):
    monkeypatch.setattr(observer, "CachedMediaClient", lambda *_: pytest.fail("Must not connect"))
    with pytest.raises(SystemExit) as error:
        observer.main([])
    assert error.value.code == 2
    with pytest.raises(SystemExit) as error:
        observer.main(["--help"])
    assert error.value.code == 0
    assert "not a" in capsys.readouterr().out.lower()


def test_cli_persists_exclusive_intent_before_connect_and_never_reuses_evidence(
    sample, tmp_path, monkeypatch, capsys
):
    binding = binding_file(sample, tmp_path)
    evidence = tmp_path / "media.ndjson"
    args = [
        "--observe-75s",
        "--socket",
        str(tmp_path / "unused.sock"),
        "--binding",
        str(binding),
        "--evidence",
        str(evidence),
    ]
    calls = []

    def collect(client, expected, *, emit):
        calls.append(expected)
        assert json.loads(evidence.read_text())["intent"] == "one_cached_media_observation"
        assert evidence.stat().st_mode & 0o777 == 0o600
        assert client.sock is None
        emit({"checkpoint": {"values": [1, 2]}})
        client.close()
        return {"complete": True, "status": "progress_observed"}

    monkeypatch.setattr(observer, "collect", collect)
    assert observer.main(args) == 0 and len(calls) == 1
    before = evidence.read_bytes()
    assert observer.main(args) == 1 and len(calls) == 1
    assert evidence.read_bytes() == before
    assert "PRIVATE" not in capsys.readouterr().out


def test_failed_collection_retains_consumed_intent_and_sanitizes_error(
    sample, tmp_path, monkeypatch, capsys
):
    binding = binding_file(sample, tmp_path)
    evidence = tmp_path / "failed.ndjson"
    args = [
        "--observe-75s",
        "--socket",
        str(tmp_path / "unused.sock"),
        "--binding",
        str(binding),
        "--evidence",
        str(evidence),
    ]

    def fail(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_CREDENTIAL")

    monkeypatch.setattr(observer, "collect", fail)
    assert observer.main(args) == 1
    assert "intent" in evidence.read_text() and "PRIVATE" not in evidence.read_text()
    before = evidence.read_bytes()
    assert observer.main(args) == 1 and evidence.read_bytes() == before
    assert "PRIVATE" not in capsys.readouterr().out


def test_observer_reads_real_rtp_and_recording_snapshots_without_creating_scanner_demand(
    network_rig, tmp_path
):
    import wave

    from sds200.daemon_api import DaemonReadOnlyApi
    from sds200.daemon_ipc import DaemonSocketListener, DaemonSocketLocation, DaemonSocketSource
    from sds200.daemon_recording import DaemonRecordingManager
    from sds200.daemon_server import DaemonApiServer

    from .test_network_audio import make_rtp

    media = network_rig
    runtime = media.rig.runtime
    manager = DaemonRecordingManager(runtime, tmp_path / "recordings")
    count = 0

    def packet():
        nonlocal count
        count += 1
        media.peer.sendto(
            make_rtp(b"\xff" * 160, sequence=count, timestamp=count * 160),
            ("127.0.0.1", media.rtsp.started_ports[0]),
        )
        wait_for(lambda: manager.snapshot().samples == count * 160)

    path = tmp_path / "actual.sock"
    api = DaemonReadOnlyApi(runtime, recording_manager=manager)
    server = DaemonApiServer(
        DaemonSocketListener(DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)),
        api,
        accept_poll_interval=0.01,
    )
    clock = Clock()
    client = observer.CachedMediaClient(path, clock=clock)
    try:
        manager.start_recording()
        packet()
        initial = runtime.snapshot().as_dict()
        recorded = manager.snapshot().as_dict()
        expected = ExpectedMedia(
            initial["scanner_endpoint"],
            initial["started_at"],
            recorded["recording"],
            initial["audio"]["endpoint"],
        )

        def wait(duration):
            packet()
            clock.wait(duration)

        with server:
            report = observer.collect(client, expected, clock=clock, wait=wait)
        assert report["complete"] and report["status"] == "progress_observed"
        assert report["counter_deltas"]["recording_samples"] == 74 * 160
        assert report["fault_deltas"] == {}
        assert media.rig.peer.reads == []  # Even display-frame demand was never created.
        stopped = manager.stop_recording()
        assert stopped.error is None and stopped.samples == count * 160
        with wave.open(str(stopped.recording_path), "rb") as wav:
            assert wav.getnframes() == count * 160
            assert wav.readframes(count * 160) == b"\x00\x00" * count * 160
        assert len(media.rtsp.started_ports) == 1
    finally:
        client.close()
        server.stop()
        manager.close()
