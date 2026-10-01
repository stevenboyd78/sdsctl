"""Real private intent files/owned pidfd; synthetic container identity, no Docker."""

import importlib.util
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_handoff_process as processes
from . import test_supplemental_recording_binding as host
from . import test_supplemental_recording_execution as execution

NAME = "supplemental_recording_dispatch"
SPEC = importlib.util.spec_from_file_location(NAME, Path(host.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
layout, tree, routing, projection, binding, directory = (
    host.layout,
    host.tree,
    host.routing,
    host.projection,
    host.binding,
    host.directory,
)


@pytest.fixture
def prepared(binding, directory, monkeypatch):
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys;sys.stdin.read()"], stdin=subprocess.PIPE
    )
    witness = None
    try:
        ticks = int(Path(f"/proc/{process.pid}/stat").read_text().rpartition(") ")[2].split()[19])
        identity = processes.w.ProcessIdentity(process.pid, ticks, execution.CONTAINER)
        # The kernel pidfd is real and retained. The fixture does not claim its
        # harmless process actually belongs to a Docker cgroup.
        monkeypatch.setattr(processes.w, "read_identity", lambda *_: identity)
        witness = processes.w.ProcessWitness(identity)
        command = execution.m.Command(
            "/data/finite-case/launch.json", "a" * 64, binding.source_sha256, time.monotonic() + 90
        )
        pins = m.Pins(binding, command, "d" * 64, identity)
        yield SimpleNamespace(process=process, witness=witness, pins=pins, directory=directory)
    finally:
        if witness is not None:
            witness.close()
        process.stdin.close()
        process.wait(timeout=3)
        assert process.returncode == 0


def create(prepared):
    return m.Claim(prepared.directory, prepared.pins, prepared.witness)


def metadata(prepared):
    value = execution.metadata()
    argv = prepared.pins.command.argv()
    value["ProcessConfig"].update(entrypoint=argv[0], arguments=list(argv[1:]))
    return value


def refused(action):
    with pytest.raises(m.UnconfirmedDispatch) as caught:
        action()
    assert str(caught.value) == m.MESSAGE and "PRIVATE" not in str(caught.value)


def test_only_three_ordered_durable_records_and_read_only_reload(prepared):
    claim = create(prepared)
    assert claim.state.phase == "create_intent" and claim.state.execution_id is None
    assert m.load(prepared.directory, prepared.pins) == claim.state
    claim.created(execution.EXEC)
    assert claim.state.phase == "created" and claim.state.execution_id == execution.EXEC
    assert m.load(prepared.directory, prepared.pins) == claim.state
    result = claim.attach_intent(metadata(prepared))
    assert result.phase == "attach_intent" and result.count == 3
    assert m.load(prepared.directory, prepared.pins) == result
    assert sorted(item.name for item in prepared.directory.iterdir()) == [
        "0000.json",
        "0001.json",
        "0002.json",
    ]
    assert all(item.stat().st_mode & 0o7777 == 0o600 for item in prepared.directory.iterdir())
    for _ in range(2):
        refused(lambda: create(prepared))  # Even a good reload is not an action capability.
    assert not hasattr(m, "start_execution") and not hasattr(m.State, "attach_intent")


@pytest.mark.parametrize("phase", ["create_intent", "created", "attach_intent"])
def test_reopen_cannot_replay_any_persisted_stage(prepared, phase):
    claim = create(prepared)
    if phase != "create_intent":
        claim.created(execution.EXEC)
    if phase == "attach_intent":
        claim.attach_intent(metadata(prepared))
    before = {item.name: item.read_bytes() for item in prepared.directory.iterdir()}
    refused(lambda: create(prepared))
    assert {item.name: item.read_bytes() for item in prepared.directory.iterdir()} == before
    assert m.load(prepared.directory, prepared.pins).phase == phase


@pytest.mark.parametrize(
    "fault", ["attach_before_created", "created_twice", "attach_twice", "created_after_attach"]
)
def test_wrong_order_consumes_controller_without_extra_files(prepared, fault):
    claim = create(prepared)
    if fault != "attach_before_created":
        claim.created(execution.EXEC)
    if fault in ("attach_twice", "created_after_attach"):
        claim.attach_intent(metadata(prepared))
    before = sorted(prepared.directory.iterdir())
    action = (
        (lambda: claim.created(execution.EXEC))
        if fault.startswith("created")
        else (lambda: claim.attach_intent(metadata(prepared)))
    )
    refused(action)
    assert claim.poisoned and sorted(prepared.directory.iterdir()) == before
    refused(lambda: claim.created(execution.EXEC))


@pytest.mark.parametrize("fault", ["id", "container", "command", "stdin", "running", "stopped"])
def test_only_exact_created_inspection_can_precede_attach(prepared, fault):
    claim = create(prepared)
    claim.created(execution.EXEC)
    value = metadata(prepared)
    if fault == "id":
        value["ID"] = "b" * 64
    if fault == "container":
        value["ContainerID"] = "b" * 64
    if fault == "command":
        value["ProcessConfig"]["arguments"].append("--PRIVATE")
    if fault == "stdin":
        value["OpenStdin"] = False
    if fault == "running":
        value.update(Running=True, Pid=1234)
    if fault == "stopped":
        value.update(ExitCode=0)
    refused(lambda: claim.attach_intent(value))
    assert m.load(prepared.directory, prepared.pins).phase == "created"
    refused(lambda: claim.attach_intent(metadata(prepared)))


@pytest.mark.parametrize("stage", ["create_intent", "created", "attach_intent"])
@pytest.mark.parametrize("at", [1, 2])
def test_lost_file_or_directory_fsync_return_preserves_consumed_intent(
    prepared, monkeypatch, stage, at
):
    claim = None if stage == "create_intent" else create(prepared)
    if stage == "attach_intent":
        claim.created(execution.EXEC)
    original, calls = m.os.fsync, []

    def lost(fd):
        original(fd)
        calls.append(fd)
        if len(calls) == at:
            raise OSError("PRIVATE fsync acknowledgment lost")

    with monkeypatch.context() as patch:
        patch.setattr(m.os, "fsync", lost)
        action = (
            (lambda: create(prepared))
            if stage == "create_intent"
            else (
                (lambda: claim.created(execution.EXEC))
                if stage == "created"
                else (lambda: claim.attach_intent(metadata(prepared)))
            )
        )
        refused(action)
    assert m.load(prepared.directory, prepared.pins).phase == stage
    refused(lambda: create(prepared))
    if claim is not None:
        assert claim.poisoned
        refused(lambda: claim.created(execution.EXEC))


def test_incomplete_file_is_preserved_not_reinitialized(prepared):
    path = prepared.directory / "0000.json"
    path.write_bytes(b'{"PRIVATE":')
    path.chmod(0o600)
    refused(lambda: create(prepared))
    refused(lambda: m.load(prepared.directory, prepared.pins))
    assert path.read_bytes() == b'{"PRIVATE":'


@pytest.mark.parametrize("phase", ["create_intent", "created", "attach_intent"])
def test_actual_init_exit_refuses_next_operation(prepared, phase):
    claim = None if phase == "create_intent" else create(prepared)
    if phase == "attach_intent":
        claim.created(execution.EXEC)
    before = sorted(prepared.directory.iterdir())
    prepared.process.stdin.close()
    prepared.process.wait(timeout=3)
    action = (
        (lambda: create(prepared))
        if phase == "create_intent"
        else (
            (lambda: claim.created(execution.EXEC))
            if phase == "created"
            else (lambda: claim.attach_intent(metadata(prepared)))
        )
    )
    refused(action)
    assert sorted(prepared.directory.iterdir()) == before


@pytest.mark.parametrize("field", ["generation", "command", "host", "init"])
def test_changed_original_pins_cannot_be_adopted_on_read(prepared, field):
    create(prepared)
    replacements = dict(
        generation="f" * 64,
        command=replace(prepared.pins.command, plan_sha256="f" * 64),
        host=replace(prepared.pins.host, plan_sha256="f" * 64),
        init=replace(prepared.pins.init, start_ticks=prepared.pins.init.start_ticks + 1),
    )
    refused(
        lambda: m.load(prepared.directory, replace(prepared.pins, **{field: replacements[field]}))
    )


def test_source_pin_must_agree_with_original_host_binding(prepared):
    pins = replace(prepared.pins, command=replace(prepared.pins.command, source_sha256="f" * 64))
    refused(lambda: m.Claim(prepared.directory, pins, prepared.witness))
    assert not list(prepared.directory.iterdir())


def test_expired_ready_bound_cannot_be_refreshed(prepared, monkeypatch):
    claim = create(prepared)
    monkeypatch.setattr(m.time, "monotonic", lambda: prepared.pins.command.ready_by + 1)
    refused(lambda: claim.created(execution.EXEC))
    assert m.load(prepared.directory, prepared.pins).phase == "create_intent"


def test_late_publication_is_not_returned_as_dispatch_permission(prepared, monkeypatch):
    claim = create(prepared)
    original, clock = m.os.fsync, m.time.monotonic
    late = False

    def fsync(fd):
        nonlocal late
        original(fd)
        late = True

    with monkeypatch.context() as patch:
        patch.setattr(m.os, "fsync", fsync)
        patch.setattr(
            m.time, "monotonic", lambda: prepared.pins.command.ready_by + 1 if late else clock()
        )
        refused(lambda: claim.created(execution.EXEC))
    assert claim.poisoned and m.load(prepared.directory, prepared.pins).phase == "created"


@pytest.mark.parametrize("fault", ["mode", "symlink", "hardlink", "fifo", "directory", "oversize"])
def test_unsafe_entry_preserved_and_controller_consumed(prepared, fault):
    claim = create(prepared)
    path = prepared.directory / "0000.json"
    original = path.read_bytes()
    if fault == "mode":
        path.chmod(0o640)
    elif fault == "oversize":
        path.write_bytes(b"x" * (m.MAX_BYTES + 1))
    else:
        saved = prepared.directory.parent / "PRIVATE_preserved.json"
        path.rename(saved)
        if fault == "symlink":
            path.symlink_to(saved)
        elif fault == "hardlink":
            os.link(saved, path)
        elif fault == "fifo":
            os.mkfifo(path, 0o600)
        else:
            path.mkdir(mode=0o700)
        assert saved.read_bytes() == original
    refused(lambda: m.load(prepared.directory, prepared.pins))
    refused(lambda: claim.created(execution.EXEC))
    assert claim.poisoned and sorted(p.name for p in prepared.directory.iterdir()) == ["0000.json"]


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate",
        "noncanonical",
        "extra",
        "schema_bool",
        "previous",
        "kind",
        "negative",
        "nonfinite",
        "deadline",
        "time_bool",
        "extra_event",
        "source",
    ],
)
def test_corrupt_durable_create_intent_cannot_be_replayed(prepared, fault):
    claim = create(prepared)
    path = prepared.directory / "0000.json"
    value = json.loads(path.read_bytes())
    if fault == "duplicate":
        raw = b'{"schema":1,' + path.read_bytes()[1:]
    elif fault == "noncanonical":
        raw = b" " + path.read_bytes()
    else:
        if fault == "extra":
            value["PRIVATE"] = True
        elif fault == "schema_bool":
            value["schema"] = True
        elif fault == "previous":
            value["previous"] = "f" * 64
        elif fault == "kind":
            value["kind"] = "PRIVATE"
        elif fault == "extra_event":
            value["event"]["PRIVATE"] = True
        elif fault == "source":
            value["event"]["pins"]["command"]["source_sha256"] = "f" * 64
        else:
            value["event"]["at"] = {
                "negative": -1,
                "nonfinite": float("nan"),
                "deadline": prepared.pins.command.ready_by,
                "time_bool": True,
            }[fault]
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    refused(lambda: m.load(prepared.directory, prepared.pins))
    refused(lambda: claim.created(execution.EXEC))
    assert path.read_bytes() == raw and claim.poisoned


@pytest.mark.parametrize("fault", ["gap", "extra", "directory_replaced", "changed_tip"])
def test_missing_or_substituted_history_is_not_adopted(prepared, fault):
    claim = create(prepared)
    if fault == "gap":
        (prepared.directory / "0000.json").rename(prepared.directory / "0001.json")
    elif fault == "extra":
        (prepared.directory / "PRIVATE").write_bytes(b"preserve")
    elif fault == "directory_replaced":
        saved = prepared.directory.with_name("PRIVATE_original")
        prepared.directory.rename(saved)
        prepared.directory.mkdir(mode=0o700)
        copied = prepared.directory / "0000.json"
        copied.write_bytes((saved / "0000.json").read_bytes())
        copied.chmod(0o600)
        # Matching bytes are readable history, not the original live directory.
        assert m.load(prepared.directory, prepared.pins) == claim.state
    else:
        path = prepared.directory / "0000.json"
        value = json.loads(path.read_bytes())
        value["event"]["at"] -= 0.001
        path.write_bytes(m.binding.encode(value))
        assert m.load(prepared.directory, prepared.pins).sha256 != claim.state.sha256
    refused(lambda: claim.created(execution.EXEC))
    assert claim.poisoned


@pytest.mark.parametrize("fault", ["wrong_witness", "closed_witness", "identity_drift", "thread"])
def test_original_live_owner_is_required_for_every_write(prepared, monkeypatch, fault):
    claim = create(prepared)
    if fault == "wrong_witness":
        claim.witness = SimpleNamespace(identity=prepared.pins.init, exited=lambda: False)
    elif fault == "closed_witness":
        prepared.witness.close()
    elif fault == "identity_drift":
        monkeypatch.setattr(
            m.process, "read_identity", lambda *_: replace(prepared.pins.init, start_ticks=1)
        )
    else:
        errors = []

        def other_thread():
            try:
                claim.created(execution.EXEC)
            except Exception as error:
                errors.append(error)

        worker = Thread(target=other_thread)
        worker.start()
        worker.join(timeout=3)
        assert not worker.is_alive() and len(errors) == 1
        assert type(errors[0]) is m.UnconfirmedDispatch
    refused(lambda: claim.created(execution.EXEC))
    assert claim.poisoned and m.load(prepared.directory, prepared.pins).phase == "create_intent"


def test_cancellation_after_durable_write_cannot_be_retried(prepared, monkeypatch):
    claim = create(prepared)
    original = m.os.fsync

    def interrupted(fd):
        original(fd)
        raise KeyboardInterrupt()

    with monkeypatch.context() as patch:
        patch.setattr(m.os, "fsync", interrupted)
        with pytest.raises(KeyboardInterrupt):
            claim.created(execution.EXEC)
    assert claim.poisoned and m.load(prepared.directory, prepared.pins).phase == "created"
    refused(lambda: claim.created(execution.EXEC))


def test_read_rejects_directory_mutation_during_enumeration(prepared, monkeypatch):
    create(prepared)
    original = m.binding.protected.evidence.read_bytes

    def changed(fd, name, **kwargs):
        result = original(fd, name, **kwargs)
        (prepared.directory / "PRIVATE_late").write_bytes(b"preserve")
        return result

    monkeypatch.setattr(m.binding.protected.evidence, "read_bytes", changed)
    refused(lambda: m.load(prepared.directory, prepared.pins))
    assert (prepared.directory / "PRIVATE_late").read_bytes() == b"preserve"
