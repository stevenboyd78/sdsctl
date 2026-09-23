"""Actual durable files/owned init pidfd, explicitly synthetic container facts."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_engine as original

m = original.intents.m
layout, tree, routing, projection, binding, directory, prepared = (
    original.layout,
    original.tree,
    original.routing,
    original.projection,
    original.binding,
    original.directory,
    original.prepared,
)
EXEC = "9" * 64


@pytest.fixture
def web(prepared):
    command = prepared.pins.command
    pins = m.WebPins(
        prepared.pins,
        m.execution.WebCommand(
            command.plan, command.plan_sha256, command.source_sha256, command.ready_by, "f" * 64
        ),
        "e" * 64,
    )
    return SimpleNamespace(pins=pins, directory=prepared.directory, witness=prepared.witness)


def create(web):
    return m.WebClaim(web.directory, web.pins, web.witness)


def metadata(web):
    value = original.intents.execution.metadata()
    argv = web.pins.command.argv()
    value.update(ID=EXEC)
    value["ProcessConfig"].update(entrypoint=argv[0], arguments=list(argv[1:]))
    return value


def denied(callback):
    with pytest.raises(m.UnconfirmedDispatch) as error:
        callback()
    assert str(error.value) == m.MESSAGE


def test_separate_web_kind_pins_and_three_durable_stages(web):
    claim = create(web)
    assert type(claim) is m.WebClaim and type(claim) is not m.Claim
    assert m.load_web(web.directory, web.pins) == claim.state
    claim.check()
    claim.created(EXEC)
    assert m.load_web(web.directory, web.pins).phase == "created"
    claim.attach_intent(metadata(web))
    claim.check()
    assert m.load_web(web.directory, web.pins) == claim.state
    files = sorted(web.directory.iterdir())
    assert [p.name for p in files] == ["0000.json", "0001.json", "0002.json"]
    assert all(p.stat().st_mode & 0o7777 == 0o600 for p in files)
    values = [json.loads(p.read_bytes()) for p in files]
    assert all(v["kind"] == "finite-recording-web-exec-intents" for v in values)
    assert values[0]["event"]["pins"] == web.pins.payload()
    assert not any(hasattr(claim, n) for n in ("begin", "start", "stop", "restore"))


@pytest.mark.parametrize("phase", ["create_intent", "created", "attach_intent"])
def test_reopen_never_reissues_web_create_or_attach(web, phase):
    claim = create(web)
    if phase != "create_intent":
        claim.created(EXEC)
    if phase == "attach_intent":
        claim.attach_intent(metadata(web))
    before = {p.name: p.read_bytes() for p in web.directory.iterdir()}
    denied(lambda: create(web))
    assert m.load_web(web.directory, web.pins).phase == phase
    assert before == {p.name: p.read_bytes() for p in web.directory.iterdir()}


@pytest.mark.parametrize("phase", ["create_intent", "created", "attach_intent"])
@pytest.mark.parametrize("which", [1, 2])
def test_lost_fsync_return_preserves_consumed_web_intent(web, monkeypatch, phase, which):
    claim = None if phase == "create_intent" else create(web)
    if phase == "attach_intent":
        claim.created(EXEC)
    fsync, calls = m.os.fsync, []

    def lost(fd):
        fsync(fd)
        calls.append(fd)
        if len(calls) == which:
            raise OSError("PRIVATE lost return")

    with monkeypatch.context() as patch:
        patch.setattr(m.os, "fsync", lost)
        denied(
            lambda: (
                create(web)
                if phase == "create_intent"
                else claim.created(EXEC)
                if phase == "created"
                else claim.attach_intent(metadata(web))
            )
        )
    assert m.load_web(web.directory, web.pins).phase == phase
    denied(lambda: create(web))
    if claim is not None:
        assert claim.poisoned
        denied(claim.check)


@pytest.mark.parametrize(
    "changed", ["plan", "plan_sha256", "source_sha256", "ready_by", "request_sha256"]
)
def test_web_command_cannot_change_original_launch_or_deadline(web, changed):
    replacement = {
        "plan": "/data/other/launch.json",
        "plan_sha256": "c" * 64,
        "source_sha256": "c" * 64,
        "ready_by": web.pins.command.ready_by + 1,
        "request_sha256": "invalid",
    }[changed]
    pins = replace(web.pins, command=replace(web.pins.command, **{changed: replacement}))
    denied(lambda: m.WebClaim(web.directory, pins, web.witness))
    assert not list(web.directory.iterdir())


@pytest.mark.parametrize("changed", ["ready", "request", "generation", "init", "host"])
def test_changed_original_web_pins_never_adopt_existing_history(web, changed):
    create(web)
    if changed == "ready":
        pins = replace(web.pins, ready_sha256="a" * 64)
    elif changed == "request":
        pins = replace(web.pins, command=replace(web.pins.command, request_sha256="a" * 64))
    else:
        old = web.pins.original
        values = {
            "generation": "a" * 64,
            "init": replace(old.init, start_ticks=old.init.start_ticks + 1),
            "host": replace(old.host, plan_sha256="a" * 64),
        }
        pins = replace(web.pins, original=replace(old, **{changed: values[changed]}))
    denied(lambda: m.load_web(web.directory, pins))


@pytest.mark.parametrize(
    "fault", ["operator", "probe", "running", "exited", "id", "container", "extra"]
)
def test_only_exact_created_web_inspection_can_precede_attach(web, fault):
    claim = create(web)
    claim.created(EXEC)
    value = metadata(web)
    if fault in ("operator", "probe"):
        value["ProcessConfig"]["arguments"][2] = (
            m.execution.ENTRY if fault == "operator" else m.execution.PROBE_ENTRY
        )
    elif fault == "running":
        value.update(Running=True, Pid=1234)
    elif fault == "exited":
        value.update(ExitCode=70)
    elif fault == "id":
        value["ID"] = "a" * 64
    elif fault == "container":
        value["ContainerID"] = "a" * 64
    else:
        value["ProcessConfig"]["arguments"].append("--PRIVATE")
    denied(lambda: claim.attach_intent(value))
    assert claim.poisoned and m.load_web(web.directory, web.pins).phase == "created"


def test_web_and_operator_apis_cannot_cross_authorities(web, prepared):
    denied(lambda: m.Claim(web.directory, web.pins, web.witness))
    denied(lambda: m.WebClaim(web.directory, prepared.pins, web.witness))
    assert not list(web.directory.iterdir())
    claim = create(web)
    denied(lambda: m.load(web.directory, web.pins))
    denied(lambda: m.load(web.directory, prepared.pins))
    # Exact Endpoint type isolates the claim-type gate. It is deliberately
    # uninitialized: rejection must happen before any endpoint I/O or use.
    endpoint = object.__new__(original.m.Endpoint)
    with pytest.raises(original.m.UnconfirmedEngine):
        original.m.Client(endpoint, claim)
    assert m.load_web(web.directory, web.pins) == claim.state


def test_old_operator_files_are_not_web_history(web, prepared):
    original.intents.create(prepared)
    before = {p.name: p.read_bytes() for p in web.directory.iterdir()}
    denied(lambda: m.load_web(web.directory, web.pins))
    denied(lambda: create(web))
    assert before == {p.name: p.read_bytes() for p in web.directory.iterdir()}


def test_subclass_cannot_choose_a_new_dispatch_policy(web):
    class Changed(m.WebClaim):
        pass

    denied(lambda: Changed(web.directory, web.pins, web.witness))
    assert not list(web.directory.iterdir())


@pytest.mark.parametrize("fault", ["deadline", "init_exit", "directory", "kind", "ready_hash"])
def test_original_liveness_and_unchanged_web_files_required(web, prepared, monkeypatch, fault):
    claim = create(web)
    if fault == "deadline":
        monkeypatch.setattr(m.time, "monotonic", lambda: web.pins.command.ready_by + 1)
    elif fault == "init_exit":
        prepared.process.stdin.close()
        prepared.process.wait(timeout=3)
    elif fault == "directory":
        saved = web.directory.with_name("retained_original_web")
        web.directory.rename(saved)
        web.directory.mkdir(mode=0o700)
        path = web.directory / "0000.json"
        path.write_bytes((saved / "0000.json").read_bytes())
        path.chmod(0o600)
        assert m.load_web(web.directory, web.pins) == claim.state
    else:
        path = web.directory / "0000.json"
        value = json.loads(path.read_bytes())
        if fault == "kind":
            value["kind"] = "finite-recording-exec-intents"
        else:
            value["event"]["pins"]["ready_sha256"] = "b" * 64
        path.write_bytes(m.binding.encode(value))
    denied(claim.check)
    assert claim.poisoned
    denied(lambda: claim.created(EXEC))
