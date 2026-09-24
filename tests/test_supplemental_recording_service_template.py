"""Pure clock-free declarations; no process, clock read, publication or service."""

import importlib.util
import json
import sys
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from . import test_supplemental_recording_host_plan as original

NAME = "supplemental_recording_service_template"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(original.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


def value():
    plan = original.value()
    del plan["original_clock"], plan["deadlines"]
    return dict(schema=1, kind=m.KIND, plan=plan, budget=dict(ready_seconds=300, stop_seconds=510))


def clock():
    values = original.value()["original_clock"]
    return m.plans.clock.Window(**(values | {"namespace": tuple(values["namespace"])}))


def final_value():
    values = original.value()
    # BOOTTIME ns -> seconds intentionally uses floats, as a real clock owner
    # does. Integer/float equality must not hide different canonical plan bytes.
    values["deadlines"] = {key: float(item) for key, item in values["deadlines"].items()}
    return values


def denied(action):
    with pytest.raises(m.UnconfirmedTemplate) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def test_canonical_clock_free_template_and_exact_schema3_preview():
    supplied = value()
    template = m.decode(supplied)
    assert template.raw == m.plans.base.encode(supplied)
    assert template.sha256 == m.plans.base.checksum(supplied)
    assert m.load_bytes(template.raw, template.sha256) == template
    assert b'"original_clock"' not in template.raw and b'"deadlines"' not in template.raw
    plan = template.preview(clock())
    assert plan == m.plans.decode(final_value())
    assert template.check_plan(plan, clock()) is None
    assert plan.deadlines.recover_by - plan.deadlines.issued_at == m.plans.base.TOTAL_SECONDS
    assert {key: json.loads(plan.raw)[key] for key in m.PLAN_FIELDS} == supplied["plan"]


def test_input_mutation_cannot_change_template_or_budgets():
    supplied = value()
    template = m.decode(supplied)
    expected = template.preview(clock())
    supplied["plan"]["normal"]["files"]["recordings"] = "f" * 64
    supplied["budget"]["ready_seconds"] = 1
    assert template.preview(clock()) == expected
    with pytest.raises(FrozenInstanceError):
        template.raw = b"changed"
    assert repr(template) == "Template()"


@pytest.mark.parametrize("field", ["original_clock", "deadlines", "unknown"])
def test_final_clock_deadlines_or_unknown_fields_cannot_enter_template(field):
    supplied = value()
    supplied["plan"][field] = original.value().get(field, "private-secret")
    denied(lambda: m.decode(supplied))


@pytest.mark.parametrize("field", sorted(m.PLAN_FIELDS))
def test_every_nonclock_field_is_required(field):
    supplied = value()
    del supplied["plan"][field]
    denied(lambda: m.decode(supplied))


@pytest.mark.parametrize("field", sorted(m.PLAN_FIELDS))
def test_every_nonclock_field_keeps_schema3_type_validation(field):
    supplied = value()
    supplied["plan"][field] = None
    denied(lambda: m.decode(supplied))


@pytest.mark.parametrize("field", ["ready_seconds", "stop_seconds"])
@pytest.mark.parametrize("bad", [True, False, 1.0, "300", None, 0, -1, 781, float("inf")])
def test_budgets_are_bounded_positive_integers(field, bad):
    supplied = value()
    supplied["budget"][field] = bad
    denied(lambda: m.decode(supplied))


@pytest.mark.parametrize(
    "budget",
    [
        dict(ready_seconds=601, stop_seconds=780),
        dict(ready_seconds=300, stop_seconds=300),
        dict(ready_seconds=300, stop_seconds=482),
        dict(ready_seconds=300, stop_seconds=510, recover_seconds=1501),
    ],
)
def test_existing_recording_slack_and_recovery_limits_cannot_change(budget):
    supplied = value() | {"budget": budget}
    denied(lambda: m.decode(supplied))


@pytest.mark.parametrize("field", ["firmware", "source", "core_generation", "normal_generation"])
def test_otherwise_valid_plan_with_changed_nonclock_fields_refuses(field):
    template = m.decode(value())
    changed = final_value()
    template.check_plan(m.plans.decode(changed), clock())
    changed[field] = "Different" if field == "firmware" else "a" * len(changed[field])
    assert changed[field] != original.value()[field]
    denied(lambda: template.check_plan(m.plans.decode(changed), clock()))


def test_nested_protected_inventory_cannot_be_changed():
    template = m.decode(value())
    changed = final_value()
    template.check_plan(m.plans.decode(changed), clock())
    changed["normal"]["files"]["recordings"] = "f" * 64
    denied(lambda: template.check_plan(m.plans.decode(changed), clock()))


def test_no_renewal_or_cross_domain_substitution_in_plan_check():
    template = m.decode(value())
    original_clock = clock()
    later = replace(
        original_clock,
        before_ns=original_clock.before_ns + 1_000_000_000,
        after_ns=original_clock.after_ns + 1_000_000_000,
        boottime_ns=original_clock.boottime_ns + 1_000_000_000,
    )
    other_domain = replace(original_clock, namespace=(4, 999))
    for different in (later, other_domain):
        preview = template.preview(different)  # Pure codec, not a service offer.
        denied(lambda preview=preview: template.check_plan(preview, original_clock))
    denied(lambda: template.preview(replace(original_clock, boot="d" * 32)))


def test_mutated_decoded_plan_is_not_accepted_based_only_on_its_bytes():
    template = m.decode(value())
    plan = template.preview(clock())
    object.__setattr__(plan, "firmware", "Changed")
    denied(lambda: template.check_plan(plan, clock()))


@pytest.mark.parametrize(
    "raw",
    [b"", b"[]", b"{", b'{"schema":1,"schema":1}', b'{"schema":NaN}', b"x" * (m.MAX_BYTES + 1)],
)
def test_invalid_noncanonical_or_oversized_bytes_refuse(raw):
    denied(lambda: m.load_bytes(raw, "a" * 64))


def test_noncanonical_correctly_hashed_bytes_and_wrong_pin_refuse():
    template = m.decode(value())
    raw = json.dumps(value(), indent=2).encode()
    denied(lambda: m.load_bytes(raw, m.hashlib.sha256(raw).hexdigest()))
    denied(lambda: m.load_bytes(template.raw, "a" * 64))
    duplicate = template.raw.replace(
        b'"ready_seconds":300', b'"ready_seconds":300,"ready_seconds":300'
    )
    denied(lambda: m.load_bytes(duplicate, m.hashlib.sha256(duplicate).hexdigest()))


def test_native_input_types_are_checked_before_json_roundtrip():
    supplied = value()
    supplied["plan"]["layouts"] = tuple(supplied["plan"]["layouts"])
    denied(lambda: m.decode(supplied))
    supplied = deepcopy(value())
    supplied["schema"] = True
    denied(lambda: m.decode(supplied))


def test_codec_never_reads_clock_opens_files_or_constructs_a_service(monkeypatch):
    original_clock = clock()

    def forbidden(*args, **kwargs):
        raise AssertionError("Pure codec attempted an external action")

    monkeypatch.setattr(m.plans.clock, "read", forbidden)
    monkeypatch.setattr(m.plans.clock, "ClockWitness", forbidden)
    monkeypatch.setattr(m.plans.clock.os, "open", forbidden)
    monkeypatch.setattr(m.plans.ordinary, "Docker", forbidden)
    template = m.decode(value())
    plan = template.preview(original_clock)
    assert template.check_plan(plan, original_clock) is None
    assert m.load_bytes(template.raw, template.sha256) == template


def test_template_is_not_part_of_previous_helper_command_allowlist():
    source = Path(m.__file__).with_name("supplemental_recording_host_source.py").read_text()
    assert '"service_template"' not in source


@pytest.mark.parametrize("bad", [None, True, {}, 1, "private-secret"])
def test_supplied_clock_must_be_the_exact_validated_window_type(bad):
    template = m.decode(value())
    denied(lambda: template.preview(bad))


@pytest.mark.parametrize("field", ["schema", "kind", "plan", "budget"])
def test_outer_declaration_fields_are_required(field):
    supplied = value()
    del supplied[field]
    denied(lambda: m.decode(supplied))


def test_unknown_outer_fields_and_schema_or_kind_cannot_select_another_protocol():
    for extra in ({"unknown": 1}, {"schema": 3}, {"kind": m.plans.KIND}):
        denied(lambda extra=extra: m.decode(value() | extra))


def test_final_plan_and_clock_free_template_are_never_interchangeable():
    template = m.decode(value())
    plan = template.preview(clock())
    denied(lambda: m.load_bytes(plan.raw, plan.sha256))
    with pytest.raises(m.plans.UnconfirmedPlan):
        m.plans.load_bytes(template.raw, template.sha256)


@pytest.mark.parametrize("field", ["ready_by", "stop_by"])
def test_structurally_valid_extended_deadline_is_not_accepted(field):
    template = m.decode(value())
    supplied = final_value()
    template.check_plan(m.plans.decode(supplied), clock())
    supplied["deadlines"][field] += 1
    denied(lambda: template.check_plan(m.plans.decode(supplied), clock()))


def test_changed_template_cannot_use_old_independent_digest():
    supplied = value()
    original_template = m.decode(supplied)
    supplied["plan"]["firmware"] = "Different"
    changed = m.decode(supplied)
    denied(lambda: m.load_bytes(changed.raw, original_template.sha256))
