"""Actual fixture files/environ/pidfd, synthetic Engine/root/kernel metadata.

No installed runtime, live Engine, peer launch policy or action authority. The
borrowed original helper fixture owns one disposable child for each role test.
"""

import builtins
import copy
import importlib.util
import io
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from . import test_supplemental_recording_helper_qualification as original
from . import test_supplemental_recording_service_runtime_expectations as declarations
from ._supplemental_fixture_budget import integer_budget

layout, image_umask, supervised = original.layout, original.image_umask, original.supervised
image, configured, helper = original.image, original.configured, original.helper
NAME = "qualify_supplemental_recording_peer_runtime"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(original.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
assert m.declarations is declarations.m


@pytest.fixture(params=m.declarations.ROLES)
def peer(helper, image, monkeypatch, request, graph_mode):
    role = request.param
    root = helper.root
    preparation = graph_mode == "preparation"
    graph = (
        m.declarations.peer_source.PreparationProfile
        if preparation
        else m.declarations.peer_source
        if graph_mode
        else m.declarations.source
    )
    for name in graph.HELPER_FILES - m.launch.helper_source.HELPER_FILES:
        path = root / m.launch.HelperQualification.HELPER / name
        path.write_bytes(b"raise RuntimeError('PRIVATE_OBSERVED_CODE_MUST_NOT_RUN')\n")
        path.chmod(0o644)
    observed = asdict(helper.plan.helper)
    observed["source"] = (
        graph.Layout(
            root / m.launch.plans.fixed.PACKAGE, root / m.launch.HelperQualification.HELPER
        )
        .observe()
        .sha256
    )
    other = observed | dict(image="sha256:" + "c" * 64, interpreter="d" * 64, environment="e" * 64)
    supplied = json.loads(helper.plan.raw)
    supplied["helper"] = observed if role == "writer" else other
    plan = m.launch.plans.decode(supplied)
    template = m.declarations.templates.decode(
        dict(
            schema=1,
            kind=m.declarations.templates.KIND,
            plan={
                key: item
                for key, item in supplied.items()
                if key not in {"original_clock", "deadlines"}
            },
            budget=integer_budget(supplied["deadlines"]),
        )
    )
    # This inert library command is independently pinned for runtime comparison
    # only. The actual fixture child's cmdline/Engine command are synthetic, as
    # in the base fixture. There is deliberately NO active entrypoint selected.
    command = (
        "/usr/local/bin/python",
        "-I",
        "-B",
        "/opt/sdsctl-recording-host/"
        + ("supplemental_recording_peer_preparation" if preparation else graph.__name__)
        + ".py",
        template.sha256,
    )
    container = helper.container
    name = "sdsctl-recording-handoff-" + plan.case + ("-observer" if role == "observer" else "")
    container.update(
        Name="/" + name, Image=observed["image"], Path=command[0], Args=list(command[1:])
    )
    container["Config"]["Entrypoint"] = list(command)
    selected = dict(
        runtime=observed,
        command_sha256=m.declarations.command_digest(command),
        configuration_sha256=original.configuration_pin(container, observed["environment"]),
        **{
            key: helper.args[key]
            for key in (
                "image_environment_sha256",
                "timezone",
                "hostname",
                "architecture",
            )
        },
    )
    envelope = dict(
        schema=1,
        kind=m.declarations.PREPARATION_KIND
        if preparation
        else (m.declarations.PEER_KIND if graph_mode else m.declarations.KIND),
        template_sha256=template.sha256,
        source_kind=graph.KIND,
        writer=copy.deepcopy(selected),
        observer=copy.deepcopy(selected),
    )
    envelope["observer" if role == "writer" else "writer"]["runtime"] = other
    decode = (
        m.declarations.decode_peer_preparation
        if preparation
        else m.declarations.decode_peer_handoff
        if graph_mode
        else m.declarations.decode
    )
    expected = decode(envelope)
    original_open = builtins.open

    def open_file(path, *args, **kwargs):
        if path == f"/proc/{helper.child.pid}/cmdline":
            helper.command_reads += 1
            raw = b"\0".join(part.encode() for part in command) + b"\0"
            return io.BytesIO(helper.command_fault if helper.command_fault is not None else raw)
        return original_open(path, *args, **kwargs)

    def inspect_image(docker, selected):
        assert docker is helper.docker and selected == observed["image"]
        helper.images += 1
        return dict(Id=selected, Os="linux", Architecture="amd64", Config=dict(Env=helper.image))

    # Base fixture does not export image; all five image fields are independently
    # supplied by its image fixture, not read back from observed process env.
    monkeypatch.setattr(builtins, "open", open_file)
    helper.image = image
    monkeypatch.setattr(type(helper.docker), "image", inspect_image)
    arguments = dict(
        template=template,
        expectations=expected,
        expectations_sha256=expected.sha256,
        role=role,
        generation=m.launch.plans.ordinary.generation(
            container, name=name, image=observed["image"]
        ),
        command=command,
        peer_handoff=graph_mode is True,
        preparation=preparation,
    )
    helper.make = lambda **overrides: m.PeerRuntimeQualification(
        plan, helper.witness, helper.docker, **(arguments | overrides)
    )
    helper.plan, helper.observed, helper.template, helper.expectations = (
        plan,
        observed,
        template,
        expected,
    )
    helper.peer_arguments, helper.role, helper.envelope = arguments, role, envelope
    helper.obj = helper.make()
    return helper


@pytest.fixture
def graph_mode(request):
    # Existing fixture consumers keep the old graph and closed codec. Only
    # explicitly marked newer-profile tests construct the separate declaration.
    return getattr(request, "param", False)


@pytest.mark.parametrize("graph_mode", ["preparation"], indirect=True)
def test_explicit_preparation_profile_checks_complete_runtime_without_enabling_old_profile(peer):
    test_role_uses_its_own_complete_runtime_without_replacing_original_plan(peer)
    assert peer.obj.source is m.declarations.peer_source.PreparationProfile
    assert peer.obj.preparation is True and peer.obj.peer_handoff is False
    assert len(peer.obj.source.MODULES) == 104
    for options in ({"preparation": False}, {"peer_handoff": True}, {"preparation": 1}):
        with pytest.raises(m.launch.UnconfirmedHostLaunch):
            peer.make(**options)


@pytest.mark.parametrize("graph_mode", ["preparation"], indirect=True)
@pytest.mark.parametrize("fault", ["source", "missing", "selection"])
def test_preparation_source_and_selection_remain_pinned(peer, fault):
    if fault == "selection":
        peer.obj.preparation = False
    else:
        path = (
            peer.root
            / m.launch.HelperQualification.HELPER
            / "supplemental_recording_peer_preparation.py"
        )
        if fault == "missing":
            path.unlink()  # Only the disposable observed-source fixture.
        else:
            path.write_bytes(b"PRIVATE changed source; must not execute")
    original.launch.denied(peer.obj)


@pytest.mark.parametrize("graph_mode", [True], indirect=True)
def test_explicit_handoff_profile_uses_complete_source_and_same_original_runtime(peer):
    test_role_uses_its_own_complete_runtime_without_replacing_original_plan(peer)
    assert peer.obj.source is m.declarations.peer_source
    assert peer.obj.peer_handoff is True
    assert len(peer.obj.source.MODULES) == 101 and len(m.declarations.source.MODULES) == 90
    assert peer.obj._source_layout(peer.root)._profile()[1] == m.declarations.peer_source.KIND
    # New pinned declarations cannot silently select the larger graph through
    # the old collector default, even when every other runtime pin matches.
    with pytest.raises(m.launch.UnconfirmedHostLaunch):
        peer.make(peer_handoff=False)


@pytest.mark.parametrize("graph_mode", [True], indirect=True)
@pytest.mark.parametrize("fault", ["missing-file", "changed-file", "profile", "selection"])
def test_handoff_profile_drift_refuses_without_fallback_or_import(peer, fault):
    if fault in ("missing-file", "changed-file"):
        path = (
            peer.root
            / m.launch.HelperQualification.HELPER
            / "supplemental_recording_writer_channel.py"
        )
        if fault == "missing-file":
            path.unlink()  # Disposable source inventory only; never an installed file.
        else:
            path.write_bytes(b"raise RuntimeError('MUST_NOT_EXECUTE_OBSERVED_BYTES')\n")
    elif fault == "profile":
        peer.obj.source = m.declarations.source
    else:
        peer.obj.peer_handoff = False
    original.launch.denied(peer.obj)


@pytest.mark.parametrize("selection", [True, 1, "peer", None])
def test_old_declaration_cannot_select_new_profile_or_coerce_selector(peer, selection):
    with pytest.raises(m.launch.UnconfirmedHostLaunch):
        peer.make(peer_handoff=selection)


def test_role_uses_its_own_complete_runtime_without_replacing_original_plan(peer):
    plan, raw, witness, fd = peer.plan, peer.plan.raw, peer.witness, peer.witness.fd
    assert peer.obj() is None
    assert 0 <= peer.obj.elapsed_seconds < 2
    assert (peer.reads, peer.images, peer.command_reads, peer.kernels) == (2, 2, 2, 2)
    assert peer.obj.plan is plan and plan.raw == raw
    assert asdict(peer.obj.runtime_pin) == peer.observed
    assert peer.obj.witness is witness and witness.fd == fd and not witness.exited()
    if peer.role == "observer":
        assert peer.obj.runtime_pin != plan.helper
    else:
        assert peer.obj.runtime_pin == plan.helper
    assert peer.obj() is None  # A new full read, not cached evidence.
    assert (peer.reads, peer.images, peer.command_reads, peer.kernels) == (4, 4, 4, 4)


@pytest.mark.parametrize("change", ["source", "runtime", "image", "environment", "command", "name"])
def test_changed_runtime_or_declared_confinement_consumes_original_collector(peer, change):
    if change in ("source", "runtime"):
        path = peer.root / (
            m.launch.HelperQualification.HELPER / "supplemental_recording_service_host_source.py"
            if change == "source"
            else Path("usr/local/lib/python3.14/os.py")
        )
        path.write_bytes(b"raise RuntimeError('CHANGED_PRIVATE_BYTES_NEVER_EXECUTE')\n")
    elif change == "image":
        replacement = "sha256:" + "0" * 64
        assert replacement != peer.container["Image"]
        peer.container["Image"] = replacement
    elif change == "environment":
        peer.container["Config"]["Env"].append("PRIVATE_UNDECLARED=value")
    elif change == "command":
        peer.command_fault = b"unreviewed\x00"
    else:
        peer.container["Name"] += "-replacement"
    original.denied(peer.obj)
    assert not peer.witness.exited()  # The observer never owns child shutdown.


@pytest.mark.parametrize(
    "change", ["role", "runtime_pin", "role_runtime", "template", "expectations", "plan"]
)
def test_original_role_objects_cannot_be_swapped_even_for_equal_values(peer, change):
    if change == "role":
        peer.obj.role = "writer" if peer.role == "observer" else "observer"
    elif change in ("runtime_pin", "role_runtime"):
        setattr(peer.obj, change, replace(getattr(peer.obj, change)))
    elif change == "template":
        peer.obj.template = replace(peer.template)
    elif change == "expectations":
        peer.obj.expectations = replace(peer.expectations)
    else:
        peer.obj.plan = m.launch.plans.decode(json.loads(peer.plan.raw))
    original.denied(peer.obj)
    assert peer.reads == peer.images == 0


def test_valid_forced_declaration_mutation_is_not_a_new_pin(peer):
    changed = copy.deepcopy(peer.envelope)
    changed["observer"]["hostname"] = "different-observer"
    object.__setattr__(peer.expectations, "raw", m.declarations.decode(changed).raw)
    original.denied(peer.obj)
    assert peer.reads == peer.images == 0


def test_foreign_or_self_reported_declaration_hash_is_not_accepted(peer):
    original.launch.denied(lambda: peer.make(expectations_sha256="f" * 64))
    assert peer.reads == peer.images == 0


def test_outer_verifier_cannot_be_its_own_observed_role(peer, monkeypatch):
    with monkeypatch.context() as changed:
        changed.setattr(
            peer.witness, "identity", replace(peer.witness.identity, pid=m.launch.os.getpid())
        )
        original.launch.denied(peer.make)
    assert peer.reads == peer.images == 0


@pytest.mark.parametrize(
    "command",
    [
        ("/usr/local/bin/python", "-I", "-B", "-c", "print('PRIVATE')"),
        ("/bin/sh", "-c", "PRIVATE"),
        ("/usr/local/bin/python", "-I", "-B", "/tmp/unreviewed.py"),
    ],
)
def test_argv_digest_does_not_admit_code_outside_joint_file_graph(peer, command):
    changed = copy.deepcopy(peer.envelope)
    changed[peer.role]["command_sha256"] = m.declarations.command_digest(command)
    expected = m.declarations.decode(changed)
    original.launch.denied(
        lambda: peer.make(
            expectations=expected,
            expectations_sha256=expected.sha256,
            command=command,
        )
    )
    assert peer.reads == peer.images == 0


def test_joint_inventory_does_not_enlarge_existing_command_or_source_policy(peer):
    original.launch.denied(
        lambda: m.launch.HelperQualification(
            peer.plan,
            peer.witness,
            peer.docker,
            **(peer.args | {"command": peer.peer_arguments["command"]}),
        )
    )
    assert NAME not in m.declarations.source.MODULES
    assert declarations.NAME not in m.declarations.source.MODULES


def test_actual_original_process_exit_consumes_collector_without_rediscovery(peer):
    assert peer.obj() is None
    peer.child.terminate()
    peer.child.wait(timeout=3)
    assert peer.witness.exited()
    before = peer.reads, peer.images
    original.denied(peer.obj)
    assert (peer.reads, peer.images) == before


def test_role_collection_keeps_original_two_second_budget(peer, monkeypatch):
    began = m.launch.time.monotonic()
    before = peer.reads, peer.images
    real_metadata = peer.obj._metadata

    def late(deadline):
        result = real_metadata(deadline)
        assert began <= deadline <= began + 2.1
        monkeypatch.setattr(m.launch.time, "monotonic", lambda: deadline + 0.01)
        return result

    monkeypatch.setattr(peer.obj, "_metadata", late)
    original.denied(peer.obj)
    assert (peer.reads, peer.images) == (before[0] + 1, before[1] + 1)


@pytest.mark.parametrize("field", ["runtime_pin", "container_name"])
def test_default_helper_retains_legacy_runtime_and_name_identity(helper, field):
    if field == "runtime_pin":
        assert helper.obj.runtime_pin is helper.plan.helper
        helper.obj.runtime_pin = replace(helper.plan.helper)
    else:
        assert helper.obj.container_name == "sdsctl-recording-handoff-" + helper.plan.case
        helper.obj.container_name += "-observer"
    original.denied(helper.obj)
    assert helper.reads == helper.images == 0


def test_selected_runtime_is_not_an_action_or_installed_entrypoint(peer, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only runtime comparison attempted an action")

    monkeypatch.setattr(m.launch.plans.ordinary.Docker, "create_execution", forbidden)
    monkeypatch.setattr(m.launch.plans.ordinary.Docker, "start_execution", forbidden)
    monkeypatch.setattr(m.launch.plans.ordinary.Docker, "_request", forbidden)
    assert peer.obj() is None
    assert peer.obj.witness is peer.witness and not peer.witness.exited()


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -1, "later"])
def test_outer_deadline_cannot_bypass_existing_time_validation(helper, bad):
    original.launch.denied(lambda: helper.obj._collect_before(bad))
    assert helper.obj.failed and helper.obj.elapsed_seconds is None
    assert helper.reads == helper.images == 0


def test_future_outer_deadline_cannot_extend_two_second_collector_bound(helper, monkeypatch):
    began = m.launch.time.monotonic()
    seen = []
    metadata = helper.obj._metadata

    def observe(deadline):
        seen.append(deadline)
        return metadata(deadline)

    monkeypatch.setattr(helper.obj, "_metadata", observe)
    assert helper.obj._collect_before(began + 60) is None
    assert len(seen) == 2 and seen[0] == seen[1] <= began + 2.1


def test_expired_outer_deadline_refuses_before_any_inspection(helper):
    original.launch.denied(lambda: helper.obj._collect_before(m.launch.time.monotonic() - 1))
    assert helper.obj.failed and helper.reads == helper.images == 0
