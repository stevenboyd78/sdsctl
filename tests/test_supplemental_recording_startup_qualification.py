"""Observer join only: real local handles, synthetic Engine/kernel/App/path data."""

import importlib.util
import json
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_helper_qualification as legacy
from . import test_supplemental_recording_service_startup as owners

NAME = "qualify_supplemental_recording_startup"
SPEC = importlib.util.spec_from_file_location(NAME, Path(legacy.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

layout, image_umask, supervised = legacy.layout, legacy.image_umask, legacy.supervised
image, configured, helper = legacy.image, legacy.configured, legacy.helper
pytestmark = pytest.mark.parametrize("helper", [True], indirect=True)


@pytest.fixture
def observed(helper):
    clock = m.clocks.plans.clock.ClockWitness(m.clocks.plans.clock.read())
    domain = link = None
    try:
        domain = m.clocks.domains.ZeroDomain(clock.original, helper.witness)
        link = m.clocks.ObserverClock(helper.plan, clock, domain, helper.witness.identity)
        args = dict(
            template=helper.template, template_sha256=helper.template.sha256, clock_link=link
        )

        def make(**changes):
            return m.StartupQualification(
                helper.plan, helper.witness, helper.docker, **(helper.args | args | changes)
            )

        yield SimpleNamespace(helper=helper, clock=clock, domain=domain, link=link, make=make)
    finally:
        if link:
            link.close()
        if domain:
            domain.close()
        clock.close()


def denied(action):
    with pytest.raises(m.launch.UnconfirmedHostLaunch) as error:
        action()
    assert str(error.value) == m.launch.MESSAGE and error.value.__suppress_context__


def test_full_startup_join_uses_explicit_profile_and_preserves_original_borrowers(observed):
    helper = observed.helper
    obj = observed.make()
    assert obj._source_layout(helper.root).startup is True
    assert obj.MAX_SECONDS == m.launch.HelperQualification.MAX_SECONDS == 2
    assert obj() is None and obj() is None  # Fresh observations, no action/receipt.
    assert helper.reads >= 4 and helper.images >= 2
    assert not obj.failed and 0 < obj.elapsed_seconds < 2
    assert not observed.clock.closed and not observed.domain.closed and not observed.link.closed
    os.fstat(observed.clock.fd)
    os.fstat(observed.domain.pidfd)
    assert not helper.witness.exited()
    # No automatic policy selection in the old collector, even with this plan.
    denied(helper.make)
    assert m.launch.HelperQualification._source_layout(obj, helper.root).startup is False


@pytest.mark.parametrize("field", ["template", "template_sha256", "clock_link", "zero_domain"])
def test_invalid_startup_input_never_closes_caller_handles(observed, field):
    denied(lambda: observed.make(**{field: None}))
    assert not observed.clock.closed and not observed.domain.closed and not observed.link.closed
    os.fstat(observed.domain.pidfd)


@pytest.mark.parametrize("index", range(7))
def test_each_exact_command_field_is_required_before_engine_reads(observed, index):
    helper = observed.helper
    command = list(helper.args["command"])
    command[index] = "private-secret"
    denied(lambda: observed.make(command=tuple(command)))
    assert helper.reads == helper.images == 0


@pytest.mark.parametrize("change", ["list", "short", "long", "final-plan-pin"])
def test_old_or_ambiguous_command_shape_is_not_upgraded_implicitly(observed, change):
    helper = observed.helper
    command = helper.args["command"]
    if change == "list":
        command = list(command)
    elif change == "short":
        command = command[:-1]
    elif change == "long":
        command += ("extra",)
    else:
        command = command[:5] + (helper.plan.sha256, command[6])
    denied(lambda: observed.make(command=command))
    assert helper.reads == 0


def test_independently_pinned_but_different_template_still_refuses_original_plan(observed):
    value = json.loads(observed.helper.template.raw)
    value["budget"]["stop_seconds"] += 1
    template = m.startup.declaration.codec.decode(value)
    command = observed.helper.args["command"]
    command = command[:5] + (template.sha256, command[6])
    denied(
        lambda: observed.make(template=template, template_sha256=template.sha256, command=command)
    )
    assert observed.helper.reads == 0


@pytest.mark.parametrize("field", ["template", "template_sha256", "clock_link"])
def test_replaced_startup_objects_are_not_adopted(observed, field):
    obj = observed.make()
    setattr(obj, field, object())
    denied(obj)
    assert obj.failed and obj.elapsed_seconds is None
    denied(obj)
    assert observed.helper.reads == 0
    assert not observed.clock.closed and not observed.domain.closed


@pytest.mark.parametrize("field", ["failed", "closed"])
@pytest.mark.parametrize("target", ["link", "clock", "domain"])
def test_change_during_clock_read_is_rejected_before_return(observed, monkeypatch, field, target):
    obj = observed.make()
    original = observed.link.read

    def changed():
        result = original()
        setattr(getattr(observed, target), field, True)
        return result

    monkeypatch.setattr(observed.link, "read", changed)
    denied(obj)
    assert obj.failed and observed.helper.reads == 0
    # Deliberately poisoned flags, not actual closes; qualification never owns
    # these handles. Restore ONLY the fixture's flag to let its owner clean up.
    try:
        os.fstat(observed.clock.fd)
        os.fstat(observed.domain.pidfd)
    finally:
        if field == "closed":
            setattr(getattr(observed, target), field, False)


@pytest.mark.parametrize("replace_pin_too", [False, True])
def test_changed_template_bytes_are_not_adopted(observed, replace_pin_too):
    obj = observed.make()
    raw = json.loads(obj.template.raw)
    raw["budget"]["stop_seconds"] += 1
    changed = m.startup.declaration.codec.decode(raw).raw
    object.__setattr__(obj.template, "raw", changed)
    if replace_pin_too:
        obj.template_raw = changed
    denied(obj)
    assert observed.helper.reads == 0


def test_read_from_equal_replacement_plan_is_not_original_clock_link(observed):
    obj = observed.make()
    observed.link.plan = m.launch.plans.load_bytes(obj.plan.raw, obj.plan.sha256)
    denied(obj)
    assert obj.failed and observed.helper.reads == 0


@pytest.mark.parametrize("boundary", ["at", "after"])
def test_probe_cutoff_cannot_be_used_as_long_running_service_qualification(
    observed, monkeypatch, boundary
):
    obj = observed.make()
    # Preserve the offer's actual subtraction order, then test its exact bound
    # and the next representable instant. `(origin + 15) - 2` can be one float
    # ULP later than `origin + 13`; the latter was occasionally still BEFORE
    # the deadline on CI. No production cutoff or comparison is relaxed.
    end = (
        min(
            obj.plan.lease["ready_by"],
            obj.plan.original_clock.after_ns / m.launch.plans.clock.NS
            + m.startup.offers.MAX_OFFER_SECONDS,
        )
        - m.startup.acceptance.MAX_SECONDS
    )
    now = end if boundary == "at" else math.nextafter(end, math.inf)
    monkeypatch.setattr(m.launch.time, "monotonic", lambda: now)
    denied(obj)
    assert observed.helper.reads == 0
    assert not observed.clock.closed and not observed.domain.closed


@pytest.mark.parametrize("key", ["NetworkMode", "Privileged", "ReadonlyRootfs"])
def test_startup_policy_keeps_full_original_confinement_checks(observed, key):
    obj = observed.make()
    observed.helper.fault = lambda value: value["HostConfig"].update(
        {key: "host" if key == "NetworkMode" else key == "Privileged"}
    )
    denied(obj)
    assert obj.failed


def test_missing_startup_module_does_not_fall_back_to_original_source(observed):
    obj = observed.make()
    (observed.helper.root / obj.HELPER / "supplemental_recording_service_startup.py").unlink()
    denied(obj)
    assert obj.failed and not observed.clock.closed


def test_adapter_is_outer_only_and_not_in_observed_helper_bundle(helper):
    assert m.startup is owners.m
    assert NAME + ".py" not in m.launch.helper_source.STARTUP_FILES
    assert "qualify_supplemental_recording_startup" not in Path(m.launch.__file__).read_text()
