"""Read-only outer preflight join; synthetic Engine/kernel/App metadata only.

Real local clock/process handles and temporary source/runtime trees are used.
No service-owned plan is supplied or read and no authority/action is produced.
"""

import importlib.util
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_startup_qualification as startup_tests

NAME = "qualify_supplemental_recording_preflight"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(startup_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

layout, image_umask, supervised = (
    startup_tests.layout,
    startup_tests.image_umask,
    startup_tests.supervised,
)
image, configured, helper = startup_tests.image, startup_tests.configured, startup_tests.helper
pytestmark = pytest.mark.parametrize("helper", [True], indirect=True)


@pytest.fixture
def observed(helper):
    clock = m.launch.plans.clock.ClockWitness(m.launch.plans.clock.read())
    domain = None
    try:
        domain = m.launch.time_domain.ZeroDomain(clock.original, helper.witness)

        def make(**changes):
            args = (
                dict(
                    template=helper.template,
                    template_sha256=helper.template.sha256,
                    observer=clock,
                    domain=domain,
                    witness=helper.witness,
                    docker=helper.docker,
                )
                | helper.args
                | changes
            )
            return m.PreflightProbeQualification(**args)

        yield SimpleNamespace(helper=helper, clock=clock, domain=domain, make=make)
    finally:
        if domain:
            domain.close()
        clock.close()


def denied(action):
    with pytest.raises(m.launch.UnconfirmedHostLaunch) as error:
        action()
    assert str(error.value) == m.launch.MESSAGE and error.value.__suppress_context__


def test_template_only_qualification_never_reads_a_service_owned_plan(observed, monkeypatch):
    h = observed.helper

    def forbidden(*_args, **_kwargs):
        pytest.fail("Preflight may not read/publish/accept a service plan")

    monkeypatch.setattr(m.startup.publication.intake.CasePlan, "__init__", forbidden)
    monkeypatch.setattr(m.startup.Startup, "prepare", forbidden)
    monkeypatch.setattr(m.startup.Startup, "prepare_service", forbidden)
    monkeypatch.setattr(m.startup.Startup, "prepare_service_from_baseline", forbidden)
    obj = observed.make()
    assert obj.plan is not h.plan and obj.plan.raw != h.plan.raw
    assert obj.plan.original_clock == observed.clock.original
    h.template.check_plan(obj.plan, observed.clock.original)
    assert obj._source_layout(h.root).startup is True
    assert obj.MAX_SECONDS == m.launch.HelperQualification.MAX_SECONDS == 2
    assert obj() is None and obj() is None
    assert 0 < obj.elapsed_seconds < 2 and h.reads >= 4 and h.images >= 2
    assert not observed.clock.closed and not observed.domain.closed
    os.fstat(observed.clock.fd)
    os.fstat(observed.domain.pidfd)
    denied(h.make)  # No implicit policy expansion in the legacy helper.


@pytest.mark.parametrize("field", ["template", "template_sha256", "observer", "domain"])
def test_independently_supplied_original_inputs_are_required(observed, field):
    denied(lambda: observed.make(**{field: None}))
    assert observed.helper.reads == observed.helper.images == 0
    assert not observed.clock.closed and not observed.domain.closed


def test_domain_keyword_cannot_override_the_original_explicit_binding(observed):
    denied(lambda: observed.make(zero_domain=None))
    assert observed.helper.reads == 0


@pytest.mark.parametrize("index", range(7))
def test_every_command_field_is_still_fixed_before_engine_reads(observed, index):
    command = list(observed.helper.args["command"])
    command[index] = "PRIVATE command"
    denied(lambda: observed.make(command=tuple(command)))
    assert observed.helper.reads == observed.helper.images == 0


@pytest.mark.parametrize("mode", ["--finite-service", "--preflight", "--zero-offset-probe"])
def test_no_real_or_invented_service_command_is_admitted(observed, mode):
    command = observed.helper.args["command"][:-1] + (mode,)
    denied(lambda: observed.make(command=command))
    assert observed.helper.reads == 0


@pytest.mark.parametrize("name", ["template", "template_sha256", "observer", "domain", "origin"])
def test_original_objects_cannot_be_swapped_and_refusal_is_sticky(observed, name):
    obj = observed.make()
    original = getattr(obj, name)
    setattr(obj, name, object())
    denied(obj)
    setattr(obj, name, original)
    denied(obj)
    assert obj.failed and obj.elapsed_seconds is None
    assert observed.helper.reads == 0
    assert not observed.clock.closed and not observed.domain.closed


@pytest.mark.parametrize("field", ["failed", "closed"])
@pytest.mark.parametrize("resource", ["clock", "domain"])
def test_retired_borrowed_owner_during_read_cannot_return_success(
    observed, monkeypatch, resource, field
):
    obj = observed.make()
    read = observed.clock.read

    def changed():
        sample = read()
        setattr(getattr(observed, resource), field, True)
        return sample

    monkeypatch.setattr(observed.clock, "read", changed)
    try:
        denied(obj)
        assert obj.failed and observed.helper.reads == 0
        os.fstat(observed.clock.fd)
        os.fstat(observed.domain.pidfd)
    finally:
        if field == "closed":
            # Fixture flag only; the underlying original resource stayed open.
            setattr(getattr(observed, resource), field, False)


@pytest.mark.parametrize("change", ["target_exit", "changed_domain_proof"])
def test_final_clock_read_cannot_hide_retired_process_or_changed_domain(
    observed, monkeypatch, change
):
    obj = observed.make()
    read = observed.clock.read

    def changed():
        sample = read()
        if change == "target_exit":
            monkeypatch.setattr(observed.helper.witness, "exited", lambda: True)
        else:
            proof = observed.domain.evidence
            observed.domain.evidence = replace(proof, native_time=(0, 1))
        return sample

    monkeypatch.setattr(observed.clock, "read", changed)
    denied(obj)
    assert obj.failed and observed.helper.reads == 0
    os.fstat(observed.clock.fd)
    os.fstat(observed.domain.pidfd)


@pytest.mark.parametrize("kind", ["original_cutoff", "changed_cutoff"])
def test_preflight_window_cannot_renew_or_expand(observed, monkeypatch, kind):
    obj = observed.make()
    end = obj.cutoff
    if kind == "original_cutoff":
        monkeypatch.setattr(m.launch.time, "monotonic", lambda: end)
    else:
        obj.cutoff += 100
    denied(obj)
    assert observed.helper.reads == 0
    assert not observed.clock.closed and not observed.domain.closed


def test_second_verifier_does_not_grant_a_new_observer_window(observed, monkeypatch):
    obj = observed.make()
    monkeypatch.setattr(m.launch.time, "monotonic", lambda: obj.cutoff)
    denied(observed.make)
    assert observed.helper.reads == 0


@pytest.mark.parametrize("key", ["NetworkMode", "Privileged", "ReadonlyRootfs"])
def test_template_only_path_keeps_complete_confinement_checks(observed, key):
    obj = observed.make()
    observed.helper.fault = lambda value: value["HostConfig"].update(
        {key: "host" if key == "NetworkMode" else key == "Privileged"}
    )
    denied(obj)
    assert obj.failed


def test_wrong_template_digest_never_reads_engine(observed):
    denied(lambda: observed.make(template_sha256="f" * 64))
    assert observed.helper.reads == 0


@pytest.mark.parametrize("replace_pin_too", [False, True])
def test_changed_template_bytes_cannot_change_the_reviewed_probe(observed, replace_pin_too):
    obj = observed.make()
    value = json.loads(obj.template.raw)
    value["budget"]["stop_seconds"] += 1
    raw = m.startup.declaration.codec.decode(value).raw
    object.__setattr__(obj.template, "raw", raw)
    if replace_pin_too:
        obj.template_raw = raw
    denied(obj)
    assert obj.failed and observed.helper.reads == 0


def test_other_clock_origin_cannot_reuse_the_original_domain_proof(observed):
    other = m.launch.plans.clock.ClockWitness(m.launch.plans.clock.read())
    try:
        assert other.original != observed.clock.original
        denied(lambda: observed.make(observer=other))
        assert observed.helper.reads == 0
        assert not other.closed and not observed.domain.closed
    finally:
        other.close()


def test_missing_startup_source_never_falls_back_to_the_ordinary_graph(observed):
    obj = observed.make()
    (observed.helper.root / obj.HELPER / "supplemental_recording_service_startup.py").unlink()
    denied(obj)
    assert obj.failed and not observed.clock.closed and not observed.domain.closed


def test_preflight_adapter_stays_outside_observed_helper_and_entrypoints(helper):
    assert NAME + ".py" not in m.launch.helper_source.STARTUP_FILES
    assert NAME not in Path(m.launch.__file__).read_text()
    assert NAME not in Path(m.startup.__file__).read_text()
