"""Private temporary receipts and native PCM writers, never a live handoff."""

import hashlib
import importlib.util
import json
import math
import os
import stat
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from sds200.daemon_recording import DaemonRecordingManager

from .test_daemon_recording import FakeClock, FakeRuntime, FakeWallClock
from .test_supplemental_native_lifecycle import configured as configured
from .test_supplemental_native_lifecycle import demand, settled, wait_for
from .test_supplemental_native_lifecycle import rig as rig
from .test_supplemental_native_pcmu import network_rig as network_rig
from .test_supplemental_recording_evidence import r, wav_bytes

NAME = "supplemental_recording_owner"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(__file__).resolve().parents[1] / "scripts" / (NAME + ".py")
)
o = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = o
SPEC.loader.exec_module(o)


@pytest.fixture
def native(tmp_path, monkeypatch):
    root = tmp_path / "PRIVATE_RECORDINGS"
    root.mkdir()
    (root / "older.wav").write_bytes(wav_bytes())
    journal = tmp_path / "PRIVATE_RECEIPTS"
    journal.mkdir(mode=0o700)
    runtime = FakeRuntime()
    clock = FakeClock()
    wall = FakeWallClock(datetime(2026, 9, 22, 12, tzinfo=UTC))
    case = "c" * 32
    baseline = r.capture_baseline(root, case)
    manager = DaemonRecordingManager(
        runtime, root, template=r.template(case), clock=clock, now=wall
    )
    plan = o.Plan(
        case,
        "a" * 64,
        hashlib.sha256(runtime.audio.stream.endpoint.encode()).hexdigest(),
        100,
        105,
        164,
        170,
    )
    owners = []

    def build(**kwargs):
        owner = o.FiniteRecordingOwner(manager, baseline, plan, journal, monotonic=clock, **kwargs)
        owners.append(owner)
        return owner

    value = SimpleNamespace(
        root=root,
        journal=journal,
        runtime=runtime,
        clock=clock,
        wall=wall,
        case=case,
        baseline=baseline,
        manager=manager,
        plan=plan,
        build=build,
    )
    try:
        yield value
    finally:
        monkeypatch.undo()
        for owner in owners:
            owner.close()
        manager.close()
        runtime.close()


def due(native):
    native.clock.value = native.plan.stop_at
    native.wall.value += timedelta(seconds=64)


def refused(action):
    with pytest.raises(o.UnconfirmedOwner) as caught:
        action()
    assert str(caught.value) == o.MESSAGE
    assert "PRIVATE" not in str(caught.value)


def test_real_pcm_writer_receipts_then_separate_file_proof(native):
    owner = native.build()
    expected = owner.start()
    native.runtime.router.submit_pcm(b"\x12\x34" * 160)
    due(native)
    stopped = owner.stop()
    assert owner.phase == "stopped"
    assert native.runtime.attach_calls == native.runtime.detach_calls == 1
    proof = r.verify_finalized(
        native.baseline, expected, generation=native.plan.generation, stopped=stopped
    )
    assert proof.samples == 160 and proof.packets == 1 and proof.old_files == 1
    records = {p.stem: json.loads(p.read_bytes()) for p in native.journal.iterdir()}
    assert set(records) == {"prepared", "start-intent", "started", "stop-intent", "stopped"}
    assert records["prepared"]["plan"] == o.asdict(native.plan)
    assert records["stopped"]["snapshot"] == stopped
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in native.journal.iterdir())
    assert all("PRIVATE" not in p.read_text() for p in native.journal.iterdir())
    assert (native.root / "older.wav").read_bytes() == wav_bytes()
    assert native.runtime.running
    refused(owner.start)
    refused(owner.stop)
    assert native.runtime.attach_calls == native.runtime.detach_calls == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("case", "../PRIVATE"),
        ("generation", "x" * 64),
        ("audio_endpoint_sha256", ""),
        ("prepared_at", True),
        ("prepared_at", -1),
        ("start_by", float("nan")),
        ("start_by", 100),
        ("start_by", 111),
        ("stop_at", 105),
        ("finish_by", 164),
        ("finish_by", 175),
        ("stop_at", "164"),
        ("finish_by", float("inf")),
    ],
)
def test_plan_refuses_invalid_identity_or_unbounded_times(native, field, value):
    refused(lambda: replace(native.plan, **{field: value}))


def test_plan_is_frozen_and_total_window_capped(native):
    with pytest.raises(FrozenInstanceError):
        native.plan.stop_at = 400
    refused(lambda: replace(native.plan, stop_at=280, finish_by=281))


def test_plan_exact_absolute_deadline_does_not_gain_subtraction_time(native):
    prepared = 2.1
    start = prepared + 3
    stop = start + 1
    finish = stop + 10
    assert finish - stop > 10
    plan = replace(
        native.plan,
        prepared_at=prepared,
        start_by=start,
        stop_at=stop,
        finish_by=finish,
    )
    assert plan.finish_by == plan.stop_at + 10
    refused(lambda: replace(plan, finish_by=math.nextafter(finish, math.inf)))


@pytest.mark.parametrize("fault", ["file", "symlink", "fifo", "directory"])
def test_nonempty_case_never_dispatches_or_removes_evidence(native, fault):
    target = native.journal / "unexpected"
    if fault == "symlink":
        target.symlink_to(native.root)
    elif fault == "fifo":
        os.mkfifo(target)
    elif fault == "directory":
        target.mkdir()
    else:
        target.write_bytes(b"PRIVATE_EVIDENCE")
    refused(native.build)
    assert target.exists() and native.runtime.attach_calls == 0


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o777])
def test_journal_must_be_private(native, mode):
    native.journal.chmod(mode)
    refused(native.build)
    assert native.runtime.attach_calls == 0


def test_parent_symlink_refused_and_descriptors_released(native, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(native.journal.parent)
    count = len(list(Path("/proc/self/fd").iterdir()))
    refused(
        lambda: o.FiniteRecordingOwner(
            native.manager,
            native.baseline,
            native.plan,
            alias / native.journal.name,
            monotonic=native.clock,
        )
    )
    assert len(list(Path("/proc/self/fd").iterdir())) == count


def test_recording_root_cannot_hold_journal(native):
    journal = native.root / "receipts"
    journal.mkdir(mode=0o700)
    refused(
        lambda: o.FiniteRecordingOwner(
            native.manager, native.baseline, native.plan, journal, monotonic=native.clock
        )
    )


def test_reopen_does_not_dispatch_even_when_no_start_was_sent(native):
    first = native.build()
    refused(native.build)  # Advisory lock held.
    first.close()
    refused(native.build)  # Prepared evidence is retained and cannot be replayed.
    assert native.runtime.attach_calls == 0
    assert sorted(p.name for p in native.journal.iterdir()) == ["prepared.json"]


@pytest.mark.parametrize(
    "fault",
    ["endpoint", "runtime", "template", "overwrite", "root", "active", "old_file", "case_file"],
)
def test_changed_binding_or_inventory_prevents_dispatch(native, monkeypatch, fault):
    owner = native.build()
    if fault == "endpoint":
        monkeypatch.setattr(native.runtime.audio.stream.transport, "endpoint", "PRIVATE_CHANGED")
    elif fault == "runtime":
        monkeypatch.setattr(native.manager, "runtime", SimpleNamespace(running=True))
    elif fault == "template":
        monkeypatch.setattr(
            native.manager,
            "path_policy",
            replace(native.manager.path_policy, template="unrelated-{timestamp}.wav"),
        )
    elif fault == "overwrite":
        monkeypatch.setattr(
            native.manager, "path_policy", replace(native.manager.path_policy, overwrite=True)
        )
    elif fault == "root":
        monkeypatch.setattr(native.manager, "directory", native.root / "unrelated")
    elif fault == "active":
        native.manager.start_recording()
    elif fault == "old_file":
        (native.root / "older.wav").write_bytes(b"CHANGED")
    else:
        (native.root / r.filename(native.case, native.wall.value.isoformat())).write_bytes(
            b"collision"
        )
    before = native.runtime.attach_calls
    refused(owner.start)
    assert owner.phase == "unconfirmed" and native.runtime.attach_calls == before
    refused(owner.start)


@pytest.mark.parametrize(
    "target,point", [("start", "before"), ("start", "after"), ("stop", "before"), ("stop", "after")]
)
def test_dispatch_failure_or_lost_ack_is_never_retried(native, monkeypatch, target, point):
    owner = native.build()
    if target == "stop":
        owner.start()
        due(native)
    method = target + "_recording"
    original = getattr(native.manager, method)
    calls = []

    def lost():
        calls.append(target)
        if point == "after":
            original()
        raise TimeoutError("PRIVATE_RESPONSE_CONTENT")

    with monkeypatch.context() as patch:
        patch.setattr(native.manager, method, lost)
        refused(getattr(owner, target))
        refused(owner.start)
        refused(owner.stop)
    assert calls == [target] and owner.phase == "unconfirmed"
    assert (native.journal / (target + "-intent.json")).exists()
    assert not (native.journal / ("started.json" if target == "start" else "stopped.json")).exists()
    owner.close()
    refused(native.build)


@pytest.mark.parametrize("target", ["start", "stop"])
def test_late_success_is_unconfirmed_without_retry_or_deadline_extension(
    native, monkeypatch, target
):
    owner = native.build()
    if target == "stop":
        owner.start()
        due(native)
    original = getattr(native.manager, target + "_recording")

    def late():
        result = original()
        native.clock.value += 2 if target == "start" else 5
        return result

    with monkeypatch.context() as patch:
        patch.setattr(native.manager, target + "_recording", late)
        refused(getattr(owner, target))
    assert owner.phase == "unconfirmed"
    refused(owner.start)
    refused(owner.stop)


@pytest.mark.parametrize("target", ["start", "stop"])
@pytest.mark.parametrize(
    "stage", ["intent_file", "intent_directory", "receipt_file", "receipt_directory"]
)
def test_fsync_failure_never_reissues_dispatch(native, monkeypatch, target, stage):
    owner = native.build()
    if target == "stop":
        owner.start()
        due(native)
    before = native.runtime.attach_calls if target == "start" else native.runtime.detach_calls
    original = os.fsync
    count = 0
    fail_at = {"intent_file": 1, "intent_directory": 2, "receipt_file": 3, "receipt_directory": 4}[
        stage
    ]

    def fail(fd):
        nonlocal count
        count += 1
        if count == fail_at:
            raise OSError("PRIVATE_DISK_ERROR")
        return original(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail)
        refused(getattr(owner, target))
        refused(getattr(owner, target))
    after = native.runtime.attach_calls if target == "start" else native.runtime.detach_calls
    assert after - before == (1 if stage.startswith("receipt") else 0)
    owner.close()
    refused(native.build)


def test_both_intent_fsyncs_happen_before_native_dispatch(native, monkeypatch):
    owner = native.build()
    events = []
    fsync = os.fsync
    start = native.manager.start_recording

    def syncing(fd):
        events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        fsync(fd)

    def starting():
        assert events == ["file", "directory"]
        assert (native.journal / "start-intent.json").exists()
        events.append("dispatch")
        return start()

    monkeypatch.setattr(os, "fsync", syncing)
    monkeypatch.setattr(native.manager, "start_recording", starting)
    owner.start()
    assert events == ["file", "directory", "dispatch", "file", "directory"]


@pytest.mark.parametrize(
    "fault", ["edit", "extra", "hardlink", "replace_directory", "chmod", "symlink"]
)
def test_journal_tampering_consumes_case_without_dispatch(native, fault):
    owner = native.build()
    record = native.journal / "prepared.json"
    if fault == "edit":
        record.write_bytes(b"modified")
    elif fault == "extra":
        (native.journal / "extra").write_bytes(b"unrelated")
    elif fault == "hardlink":
        (native.journal.parent / "alias").hardlink_to(record)
    elif fault == "replace_directory":
        native.journal.rename(native.journal.with_name("preserved"))
        native.journal.mkdir(mode=0o700)
    elif fault == "chmod":
        record.chmod(0o644)
    else:
        saved = native.journal.parent / "saved"
        record.rename(saved)
        record.symlink_to(saved)
    refused(owner.start)
    assert native.runtime.attach_calls == 0


@pytest.mark.parametrize("instant", [99, 105, float("nan"), float("inf"), True])
def test_start_clock_refusals_are_terminal(native, instant):
    owner = native.build()
    native.clock.value = instant
    refused(owner.start)
    native.clock.value = 101
    refused(owner.start)
    assert native.runtime.attach_calls == 0


@pytest.mark.parametrize("instant", [163.999, 170])
def test_stop_must_be_in_fixed_window(native, instant):
    owner = native.build()
    owner.start()
    native.clock.value = instant
    refused(owner.stop)
    due(native)
    refused(owner.stop)
    assert native.runtime.detach_calls == 0


def test_close_releases_only_journal_never_dispatches_hidden_stop(native):
    owner = native.build()
    owner.start()
    owner.close()
    assert owner.phase == "closed" and native.manager.snapshot().active
    assert native.runtime.detach_calls == 0
    refused(owner.start)
    refused(owner.stop)


def test_reentrant_operation_and_close_are_refused(native, monkeypatch):
    owner = native.build()
    start = native.manager.start_recording

    def reentrant():
        refused(owner.start)
        refused(owner.stop)
        refused(owner.close)
        return start()

    monkeypatch.setattr(native.manager, "start_recording", reentrant)
    owner.start()
    assert owner.phase == "recording" and native.runtime.attach_calls == 1


def test_zero_samples_receipt_is_not_misreported_as_artifact_or_audible_pass(native):
    owner = native.build()
    expected = owner.start()
    due(native)
    stopped = owner.stop()
    assert stopped["samples"] == 0 and owner.phase == "stopped"
    with pytest.raises(r.UnconfirmedRecording):
        r.verify_finalized(
            native.baseline, expected, generation=native.plan.generation, stopped=stopped
        )


def test_existing_handoff_bundle_and_idle_guards_are_not_changed():
    scripts = Path(o.__file__).parent
    assert len(list(scripts.glob("supplemental_handoff_*.py"))) == 14
    assert "recording_active" in (scripts / "supplemental_handoff_policy.py").read_text()
    assert (
        "supplemental_recording_owner"
        not in (scripts / "accept_supplemental_daemon.py").read_text()
    )


@pytest.mark.parametrize("target", ["start", "stop"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "failed"),
        ("active", None),
        ("closed", True),
        ("error", "PRIVATE"),
        ("recording", "different.wav"),
        ("started_at", "not a timestamp"),
        ("completed_recordings", True),
        ("samples", -1),
        ("packets", False),
        ("elapsed_seconds", float("nan")),
        ("audio_duration_seconds", 999),
        ("sink", {}),
        ("reliability", {}),
        ("extra", "PRIVATE"),
    ],
)
def test_malformed_native_acknowledgment_consumes_operation(
    native, monkeypatch, target, field, value
):
    owner = native.build()
    if target == "stop":
        owner.start()
        due(native)
    original = getattr(native.manager, target + "_recording")

    def malformed():
        result = original().as_dict()
        result[field] = value
        return SimpleNamespace(as_dict=lambda: result)

    monkeypatch.setattr(native.manager, target + "_recording", malformed)
    refused(getattr(owner, target))
    assert owner.phase == "unconfirmed"
    refused(owner.start)
    refused(owner.stop)


@pytest.mark.parametrize("target", ["start", "stop"])
def test_configuration_change_after_durable_intent_withholds_dispatch(native, monkeypatch, target):
    owner = native.build()
    if target == "stop":
        owner.start()
        due(native)
    publish = owner._publish
    before = native.runtime.attach_calls if target == "start" else native.runtime.detach_calls

    def changed(name, now, snapshot=None):
        publish(name, now, snapshot)
        if name == target + "-intent":
            monkeypatch.setattr(native.manager, "directory", native.root / "unrelated")

    monkeypatch.setattr(owner, "_publish", changed)
    refused(getattr(owner, target))
    after = native.runtime.attach_calls if target == "start" else native.runtime.detach_calls
    assert before == after


def test_replaced_recording_root_refused_before_native_stop(native):
    owner = native.build()
    owner.start()
    due(native)
    preserved = native.root.with_name("preserved")
    native.root.rename(preserved)
    native.root.mkdir()
    try:
        refused(owner.stop)
        assert native.runtime.detach_calls == 0
        assert not list(native.root.iterdir())
    finally:
        native.root.rmdir()
        preserved.rename(native.root)


def test_concurrent_attempt_cannot_start_a_second_writer(native, monkeypatch):
    owner = native.build()
    entered, release = Event(), Event()
    original = native.manager.start_recording
    results, errors = [], []

    def blocked():
        entered.set()
        assert release.wait(2)
        return original()

    def run():
        try:
            results.append(owner.start())
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(native.manager, "start_recording", blocked)
    worker = Thread(target=run)
    worker.start()
    try:
        assert entered.wait(2)
        refused(owner.start)
        refused(owner.stop)
        refused(owner.close)
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and not errors and len(results) == 1
    assert native.runtime.attach_calls == 1


@pytest.mark.parametrize("which", [1, 2])
def test_preparation_fsync_failure_preserves_partial_case_and_closes_descriptors(
    native, monkeypatch, which
):
    original = os.fsync
    count = 0
    descriptors = len(list(Path("/proc/self/fd").iterdir()))

    def fail(fd):
        nonlocal count
        count += 1
        if count == which:
            raise OSError("PRIVATE_DISK_FAILURE")
        return original(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail)
        refused(native.build)
    assert len(list(Path("/proc/self/fd").iterdir())) == descriptors
    assert (native.journal / "prepared.json").exists()
    refused(native.build)
    assert native.runtime.attach_calls == 0


@pytest.mark.parametrize("stage", ["start-intent", "started", "stop-intent", "stopped"])
def test_late_journal_publication_cannot_grant_success(native, monkeypatch, stage):
    owner = native.build()
    if stage.startswith("stop"):
        owner.start()
        due(native)
    publish = owner._publish

    def late(name, now, snapshot=None):
        publish(name, now, snapshot)
        if name == stage:
            native.clock.value += 10

    monkeypatch.setattr(owner, "_publish", late)
    refused(owner.stop if stage.startswith("stop") else owner.start)
    assert owner.phase == "unconfirmed"
    assert native.runtime.attach_calls == (0 if stage == "start-intent" else 1)
    assert native.runtime.detach_calls == (1 if stage == "stopped" else 0)
    refused(owner.start)
    refused(owner.stop)


def test_failed_metadata_is_preserved_without_wrapper_retry(native, monkeypatch):
    import sds200.daemon_recording as module

    owner = native.build()
    owner.start()
    native.runtime.router.submit_pcm(b"\x12\x34" * 160)
    due(native)

    def fail(_metadata):
        raise OSError("PRIVATE_METADATA_FAILURE")

    with monkeypatch.context() as patch:
        patch.setattr(module, "write_recording_metadata", fail)
        refused(owner.stop)
        refused(owner.stop)
        assert native.runtime.detach_calls == 1
        assert native.manager.snapshot().status.value == "failed"
        assert native.manager.recording_path.exists()
        assert not native.manager.recording_path.with_suffix(".wav.json").exists()
    # Native recovery/close is deliberately separate from this controller.
    assert owner.phase == "unconfirmed"


@pytest.mark.parametrize("records", [1, 2, 3, 4, 5])
def test_process_exit_leaves_nonreplayable_durable_receipts(native, records):
    program = """
import os, sys
from pathlib import Path
from supplemental_recording_owner import _Receipts
journal = _Receipts(Path(sys.argv[1]))
for name in ('prepared', 'start-intent', 'started', 'stop-intent', 'stopped')[:int(sys.argv[2])]:
    journal.publish(name, {'fixture': True})
os._exit(23)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(o.__file__).parent)
    result = subprocess.run(
        [sys.executable, "-c", program, str(native.journal), str(records)],
        env=environment,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 23, result.stderr
    assert len(list(native.journal.iterdir())) == records
    refused(native.build)
    assert native.runtime.attach_calls == 0


@pytest.mark.parametrize("target", ["start", "stop"])
def test_interrupted_operation_remains_consumed(native, monkeypatch, target):
    owner = native.build()
    if target == "stop":
        owner.start()
        due(native)
    original = getattr(native.manager, target + "_recording")

    def interrupted():
        original()
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(native.manager, target + "_recording", interrupted)
        with pytest.raises(KeyboardInterrupt):
            getattr(owner, target)()
    assert owner.phase == "unconfirmed"
    refused(owner.start)
    refused(owner.stop)


@pytest.mark.parametrize("command", ["FQK", "DTM"])
@pytest.mark.parametrize("reply", [True, False])
def test_journaled_native_recording_coexists_with_single_rtp_pcmu_and_optional_read(
    network_rig, native, tmp_path, command, reply
):
    from sds200.daemon_ipc import DaemonSocketListener, DaemonSocketLocation, DaemonSocketSource
    from sds200.daemon_pcmu_client import DaemonPcmuClient
    from sds200.daemon_pcmu_server import DaemonPcmuServer
    from sds200.pcmu_stream import PcmuStream

    from .test_network_audio import make_rtp

    media = network_rig
    manager = DaemonRecordingManager(
        media.rig.runtime,
        native.root,
        template=r.template(native.case),
        clock=native.clock,
        now=native.wall,
    )
    plan = replace(
        native.plan,
        audio_endpoint_sha256=hashlib.sha256(
            media.rig.runtime.audio.stream.endpoint.encode()
        ).hexdigest(),
    )
    owner = o.FiniteRecordingOwner(
        manager, native.baseline, plan, native.journal, monotonic=native.clock
    )
    stream = PcmuStream(media.transport)
    location = DaemonSocketLocation(tmp_path / "p.sock", DaemonSocketSource.EXPLICIT)
    server = DaemonPcmuServer(DaemonSocketListener(location), stream, accept_poll_interval=0.01)
    client = DaemonPcmuClient(location, timeout=1)
    try:
        server.start()
        client.connect().settimeout(1)
        wait_for(lambda: server.connected_clients == 1 and stream.subscriber_count == 1)
        expected = owner.start()
        with media.rig.peer.hold(command) as gate:
            demand(media.rig, command)
            assert gate.entered.wait(1)
            for index in range(4):
                media.peer.sendto(
                    make_rtp(b"\xff" * 160, sequence=100 + index, timestamp=1000 + index * 160),
                    ("127.0.0.1", media.rtsp.started_ports[0]),
                )
            assert all(client.receive().packet.payload == b"\xff" * 160 for _ in range(4))
            wait_for(lambda: manager.snapshot().samples == 640)
            if reply:
                gate.release.set()
            settled(media.rig)
            assert media.rig.cache.snapshot().blocked_until_reconnect == (
                None if reply else "timeout"
            )
        due(native)
        stopped = owner.stop()
        assert (
            r.verify_finalized(
                native.baseline, expected, generation=plan.generation, stopped=stopped
            ).samples
            == 640
        )
        assert owner.phase == "stopped" and len(media.rtsp.started_ports) == 1
        assert media.rig.runtime.running
        media.peer.sendto(
            make_rtp(b"\xff" * 160, sequence=104, timestamp=1640),
            ("127.0.0.1", media.rtsp.started_ports[0]),
        )
        assert client.receive().packet.payload == b"\xff" * 160
        assert manager.snapshot().as_dict() == stopped
    finally:
        client.close()
        owner.close()
        manager.close()
        server.stop()
