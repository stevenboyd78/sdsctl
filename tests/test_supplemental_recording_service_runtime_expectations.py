"""Separate writer/observer descriptions cannot supply live action authority."""

import importlib.util
import json
import sys
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from . import test_supplemental_recording_peer_host_source as peer_original
from . import test_supplemental_recording_runtime as runtime_original
from . import test_supplemental_recording_service_host_source as source_original
from . import test_supplemental_recording_service_template as original

NAME = "supplemental_recording_service_runtime_expectations"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(original.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

assert m.runtime is runtime_original.m
assert m.source is source_original.m
assert m.peer_source is peer_original.m


def value():
    template = original.m.decode(original.value())
    writer = dict(
        runtime=original.value()["plan"]["helper"],
        command_sha256="1" * 64,
        configuration_sha256="2" * 64,
        image_environment_sha256="3" * 64,
        architecture="amd64",
        timezone="America/Denver",
        hostname="recording-writer",
    )
    observer = deepcopy(writer)
    observer["runtime"]["image"] = "sha256:" + "4" * 64
    observer["runtime"]["interpreter"] = "5" * 64
    observer["runtime"]["environment"] = "6" * 64
    observer["command_sha256"] = "7" * 64
    observer["configuration_sha256"] = "8" * 64
    observer["image_environment_sha256"] = "9" * 64
    observer["hostname"] = "recording-observer"
    return dict(
        schema=1,
        kind=m.KIND,
        template_sha256=template.sha256,
        source_kind=m.source.KIND,
        writer=writer,
        observer=observer,
    )


def denied(action):
    with pytest.raises(m.UnconfirmedExpectations) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def test_separate_runtime_pins_join_exact_template_and_original_final_plan():
    supplied = value()
    expected = m.decode(supplied)
    template = original.m.decode(original.value())
    clock = original.clock()
    plan = template.preview(clock)
    assert expected.raw == m.plans.base.encode(supplied)
    assert expected.sha256 == m.plans.base.checksum(supplied)
    assert m.load_bytes(expected.raw, expected.sha256) == expected
    assert expected.check_template(template) is None
    assert expected.check_plan(template, plan, clock) is None
    assert supplied["writer"]["runtime"] != supplied["observer"]["runtime"]
    assert plan.raw == template.preview(clock).raw
    assert repr(expected) == "Expectations()"
    supplied["observer"]["runtime"]["environment"] = "f" * 64
    assert expected.raw != m.plans.base.encode(supplied)
    with pytest.raises(FrozenInstanceError):
        expected.raw = b"changed"


def test_identical_expected_images_still_require_two_explicit_peer_records():
    supplied = value()
    supplied["observer"] = deepcopy(supplied["writer"])
    expected = m.decode(supplied)
    expected.check_template(original.m.decode(original.value()))
    del supplied["observer"]
    denied(lambda: m.decode(supplied))


@pytest.mark.parametrize("field", sorted(m.FIELDS))
def test_every_top_level_field_is_required_and_closed(field):
    supplied = value()
    del supplied[field]
    denied(lambda: m.decode(supplied))
    supplied = value() | {"unexpected": "PRIVATE"}
    denied(lambda: m.decode(supplied))


@pytest.mark.parametrize("role", m.ROLES)
@pytest.mark.parametrize("field", sorted(m.ROLE_FIELDS))
def test_every_role_field_is_required_and_has_no_implicit_default(role, field):
    supplied = value()
    del supplied[role][field]
    denied(lambda: m.decode(supplied))
    supplied = value()
    supplied[role]["unexpected"] = "PRIVATE"
    denied(lambda: m.decode(supplied))


@pytest.mark.parametrize("role", m.ROLES)
@pytest.mark.parametrize("field", sorted(m.plans.RuntimePin.__dataclass_fields__))
def test_both_runtime_records_are_complete_and_strict(role, field):
    supplied = value()
    del supplied[role]["runtime"][field]
    denied(lambda: m.decode(supplied))
    supplied = value()
    supplied[role]["runtime"][field] = "PRIVATE malformed"
    denied(lambda: m.decode(supplied))
    supplied = value()
    supplied[role]["runtime"]["unexpected"] = "PRIVATE"
    denied(lambda: m.decode(supplied))


@pytest.mark.parametrize("role", m.ROLES)
@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("command_sha256", "F" * 64),
        ("configuration_sha256", "0" * 63),
        ("image_environment_sha256", None),
        ("architecture", "x86_64"),
        ("architecture", True),
        ("timezone", "../PRIVATE"),
        ("timezone", ""),
        ("hostname", "bad name"),
        ("hostname", "a" * 65),
        ("runtime", []),
    ],
)
def test_both_roles_keep_existing_runtime_and_environment_syntax(role, field, bad):
    supplied = value()
    supplied[role][field] = bad
    denied(lambda: m.decode(supplied))


@pytest.mark.parametrize("schema", [True, False, 0, 2, 1.0, "1", None])
def test_schema_does_not_coerce(schema):
    denied(lambda: m.decode(value() | {"schema": schema}))


def test_old_profiles_and_different_source_graphs_cannot_be_mixed():
    for kind in (m.templates.KIND, m.plans.KIND, "finite-recording-preflight-permission-v1"):
        denied(lambda kind=kind: m.decode(value() | {"kind": kind}))
    for kind in (m.source.controller.KIND, m.source.source.KIND, m.source.source.SERVICE_KIND):
        denied(lambda kind=kind: m.decode(value() | {"source_kind": kind}))
    supplied = value()
    supplied["observer"]["runtime"]["source"] = "f" * 64
    denied(lambda: m.decode(supplied))


def test_handoff_declaration_requires_distinct_kind_and_explicit_profile_selection():
    legacy = value()
    supplied = legacy | {"kind": m.PEER_KIND, "source_kind": m.peer_source.KIND}
    expected = m.decode_peer_handoff(supplied)
    assert m.load_bytes(expected.raw, expected.sha256) == expected
    assert m.source_profile(expected, peer_handoff=True) is m.peer_source
    assert m.source_profile(m.decode(legacy)) is m.source
    denied(lambda: m.decode(supplied))
    denied(lambda: m.decode_peer_handoff(legacy))
    denied(lambda: m.source_profile(expected))
    denied(lambda: m.source_profile(m.decode(legacy), peer_handoff=True))
    denied(lambda: m.decode_peer_handoff(supplied | {"source_kind": m.source.KIND}))
    denied(lambda: m.decode(legacy | {"source_kind": m.peer_source.KIND}))
    # Changing the graph tag changes the independently authenticated document
    # digest; the old pin cannot authenticate even otherwise equal new fields.
    denied(lambda: m.load_bytes(expected.raw, m.decode(legacy).sha256))


@pytest.mark.parametrize("selection", [0, 1, "peer", None, [], object()])
def test_profile_selection_is_exact_and_never_inferred_from_truthiness(selection):
    expected = m.decode_peer_handoff(
        value() | {"kind": m.PEER_KIND, "source_kind": m.peer_source.KIND}
    )
    denied(lambda: m.source_profile(expected, peer_handoff=selection))


@pytest.mark.parametrize("field", sorted(m.plans.RuntimePin.__dataclass_fields__))
def test_writer_runtime_must_match_independent_original_template(field):
    supplied = value()
    supplied["writer"]["runtime"][field] = ("sha256:" if field == "image" else "") + "f" * 64
    if field == "source":
        supplied["observer"]["runtime"][field] = supplied["writer"]["runtime"][field]
    expected = m.decode(supplied)  # Syntactically valid is not a matching template.
    denied(lambda: expected.check_template(original.m.decode(original.value())))


def test_other_template_or_mutated_plan_or_clock_does_not_join():
    expected = m.decode(value())
    template = original.m.decode(original.value())
    clock = original.clock()
    plan = template.preview(clock)
    changed = original.value()
    changed["plan"]["firmware"] = "Different"
    denied(lambda: expected.check_template(original.m.decode(changed)))
    later = replace(
        clock,
        before_ns=clock.before_ns + 1,
        after_ns=clock.after_ns + 1,
        boottime_ns=clock.boottime_ns + 1,
    )
    denied(lambda: expected.check_plan(template, template.preview(later), clock))
    object.__setattr__(plan, "firmware", "PRIVATE changed")
    denied(lambda: expected.check_plan(template, plan, clock))


def test_native_types_duplicate_fields_and_noncanonical_or_wrong_hash_refuse():
    expected = m.decode(value())
    denied(lambda: m.load_bytes(expected.raw, "f" * 64))
    raw = json.dumps(value(), indent=2).encode()
    denied(lambda: m.load_bytes(raw, m.hashlib.sha256(raw).hexdigest()))
    duplicate = expected.raw.replace(b'"schema":1', b'"schema":1,"schema":1')
    denied(lambda: m.load_bytes(duplicate, m.hashlib.sha256(duplicate).hexdigest()))
    for raw in (b"", b"[]", b"{", b'{"schema":NaN}', b"x" * (m.MAX_BYTES + 1)):
        denied(lambda raw=raw: m.load_bytes(raw, "f" * 64))
    supplied = value()
    supplied["observer"] = tuple(supplied["observer"].items())
    denied(lambda: m.decode(supplied))


def test_frozen_subclass_or_forced_raw_mutation_is_not_accepted():
    class Other(m.Expectations):
        pass

    denied(lambda: Other(m.decode(value()).raw))
    expected = m.decode(value())
    object.__setattr__(expected, "raw", b"PRIVATE invalid")
    denied(lambda: expected.sha256)
    denied(lambda: expected.check_template(original.m.decode(original.value())))


@pytest.mark.parametrize("role", m.ROLES)
def test_role_commands_are_separate_tagged_argv_pins_not_shell_commands(role):
    command = ("/usr/local/bin/python", "-I", "-B", "/reviewed.py", "explicit fixture argument")
    supplied = value()
    supplied[role]["command_sha256"] = m.command_digest(command)
    expected = m.decode(supplied)
    assert expected.check_command(role, command) is None
    assert m.command_digest(command) != m.plans.base.checksum(list(command))
    denied(lambda: expected.check_command(role, command[:-1]))
    denied(lambda: expected.check_command(role, (" ".join(command),)))
    denied(lambda: expected.check_command(next(item for item in m.ROLES if item != role), command))
    denied(lambda: expected.check_command("candidate", command))


@pytest.mark.parametrize(
    "bad",
    [
        (),
        [],
        ("",),
        ("x\x00y",),
        ("\n",),
        ("é",),
        ("x" * 4097,),
        ("x",) * 33,
        ("x" * 4096,) * 2,
        (True,),
        (1,),
        (None,),
    ],
)
def test_command_fingerprint_is_bounded_and_does_not_coerce(bad):
    denied(lambda: m.command_digest(bad))


@pytest.mark.parametrize("role", m.ROLES)
@pytest.mark.parametrize("field", sorted(m.ROLE_FIELDS - {"runtime"}))
def test_role_values_do_not_roundtrip_string_subclasses_into_valid_inputs(role, field):
    class Other(str):
        pass

    supplied = value()
    supplied[role][field] = Other(supplied[role][field])
    denied(lambda: m.decode(supplied))


@pytest.mark.parametrize("role", m.ROLES)
@pytest.mark.parametrize("field", sorted(m.plans.RuntimePin.__dataclass_fields__))
def test_runtime_values_are_exact_strings_before_json_encoding(role, field):
    class Other(str):
        pass

    supplied = value()
    supplied[role]["runtime"][field] = Other(supplied[role]["runtime"][field])
    denied(lambda: m.decode(supplied))


def test_subclass_template_and_plan_cannot_replace_original_comparisons():
    expected = m.decode(value())

    class Other(original.m.Template):
        def check_plan(self, *args):
            raise AssertionError("Should not call foreign comparison")

    template, clock = original.m.decode(original.value()), original.clock()
    plan = template.preview(clock)
    denied(lambda: expected.check_template(Other(template.raw)))
    denied(lambda: expected.check_plan(Other(template.raw), plan, clock))


def test_valid_role_swap_cannot_join_writer_runtime_to_the_template():
    supplied = value()
    supplied["writer"], supplied["observer"] = supplied["observer"], supplied["writer"]
    expected = m.decode(supplied)
    denied(lambda: expected.check_template(original.m.decode(original.value())))


def test_comparison_performs_no_clock_filesystem_process_or_app_action(monkeypatch):
    import socket
    import subprocess

    template, clock = original.m.decode(original.value()), original.clock()
    plan = template.preview(clock)
    supplied = value()

    def forbidden(*args, **kwargs):
        raise AssertionError("Pure expectations attempted an external action")

    monkeypatch.setattr(m.plans.clock, "read", forbidden)
    monkeypatch.setattr(m.plans.clock, "ClockWitness", forbidden)
    monkeypatch.setattr(m.plans.clock.os, "open", forbidden)
    monkeypatch.setattr(m.plans.ordinary, "Docker", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    expected = m.decode(supplied)
    expected.check_plan(template, plan, clock)
    assert m.load_bytes(expected.raw, expected.sha256) == expected


def test_existing_plan_template_and_source_profiles_remain_closed():
    expected = m.decode(value())
    original.denied(lambda: original.m.load_bytes(expected.raw, expected.sha256))
    with pytest.raises(m.plans.UnconfirmedPlan):
        m.plans.decode(json.loads(expected.raw))
    assert NAME not in m.source.MODULES
    assert NAME not in m.source.controller.MODULES
    assert NAME not in m.source.source.SERVICE_MODULES
    assert NAME not in m.source.source.MODULES
