"""Independent service policy; synthetic Engine/source, actual local custody."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_permission_qualification as previous
from . import test_supplemental_recording_service_command as commands  # noqa: F401
from . import test_supplemental_recording_startup_qualification as final_tests  # noqa: F401

NAME = "qualify_supplemental_recording_service"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(previous.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

layout, image_umask, supervised = previous.layout, previous.image_umask, previous.supervised
image, configured, helper = previous.image, previous.configured, previous.helper
observed, denied = previous.observed, previous.denied
pytestmark = pytest.mark.parametrize("helper", ["service"], indirect=True)


def preflight(o, **changes):
    h = o.helper
    return m.ServicePreflightQualification(
        **(
            dict(
                template=h.template,
                template_sha256=h.template.sha256,
                baseline_sha256="d" * 64,
                observer_identity=h.observer_identity,
                observer=o.clock,
                domain=o.domain,
                witness=h.witness,
                docker=h.docker,
            )
            | h.args
            | changes
        )
    )


@pytest.fixture
def prepared(observed):
    o = observed
    p = preflight(o)
    p.review_and_qualify(o.review)
    plan = o.helper.template.preview(m.launch.plans.clock.read())
    link = m.final.clocks.ObserverClock(plan, o.clock, o.domain, o.helper.witness.identity)
    try:
        yield SimpleNamespace(
            observed=o,
            p=p,
            plan=plan,
            link=link,
            make=lambda: m.ServicePreparationQualification(plan, p, link),
        )
    finally:
        link.close()


def test_full_distinct_preflight_and_final_plan_checks_without_actions(prepared):
    s = prepared
    obj = s.make()
    assert obj.MAX_SECONDS == s.p.MAX_SECONDS == 2
    assert obj.plan is s.plan and obj.plan is not s.p.plan
    assert obj.plan.original_clock.before_ns > s.p.origin.after_ns
    assert obj._source_layout(s.observed.helper.root).service_preparation
    assert obj() is None and not obj.failed and 0 < obj.elapsed_seconds < 2
    assert s.observed.helper.reads == s.observed.helper.images == 4
    assert not s.link.closed and not s.observed.clock.closed and not s.observed.domain.closed
    assert s.p.permission_attempted and not s.p.failed
    # Every previous qualifier still rejects this command before host inspection.
    denied(s.observed.make)
    denied(s.observed.helper.make)
    denied(
        lambda: m.final.StartupQualification(
            s.plan,
            s.p.witness,
            s.p.docker,
            template=s.p.template,
            template_sha256=s.p.template_sha256,
            clock_link=s.link,
            **s.observed.helper.args,
        )
    )
    assert s.observed.helper.reads == 4


@pytest.mark.parametrize("index", range(9))
def test_each_service_argument_is_fixed_before_engine_reads(observed, index):
    args = list(observed.helper.args["command"])
    args[index] = "PRIVATE wrong service command"
    denied(lambda: preflight(observed, command=tuple(args)))
    assert observed.helper.reads == observed.helper.images == 0


@pytest.mark.parametrize("mode", ["--run", "--startup-probe", "--permission-probe", "--record"])
def test_no_alias_can_upgrade_or_downgrade_preparation(observed, mode):
    args = observed.helper.args["command"][:-1] + (mode,)
    denied(lambda: preflight(observed, command=args))
    assert observed.helper.reads == 0


@pytest.mark.parametrize(
    "fault", ["not_run", "ordinary_call", "failed", "clock", "peer", "baseline"]
)
def test_final_check_cannot_adopt_missing_or_changed_preflight(observed, fault):
    p = preflight(observed)
    if fault != "not_run":
        if fault == "ordinary_call":
            p()  # Full read alone is NOT the one-use permission callback.
        else:
            p.review_and_qualify(observed.review)
    plan = observed.helper.template.preview(m.launch.plans.clock.read())
    link = m.final.clocks.ObserverClock(plan, observed.clock, observed.domain, p.init)
    try:
        if fault == "failed":
            p.failed = True
        elif fault == "clock":
            p.origin = m.launch.plans.clock.read()
        elif fault == "peer":
            p.observer_identity = p.init
        elif fault == "baseline":
            p.baseline_sha256 = "e" * 64
        reads = observed.helper.reads
        denied(lambda: m.ServicePreparationQualification(plan, p, link))
        assert observed.helper.reads == reads
        assert not observed.clock.closed and not observed.domain.closed
    finally:
        link.close()


def test_observer_temporary_plan_cannot_be_reused_as_service_plan(prepared):
    s = prepared
    link = m.final.clocks.ObserverClock(s.p.plan, s.observed.clock, s.observed.domain, s.p.init)
    try:
        denied(lambda: m.ServicePreparationQualification(s.p.plan, s.p, link))
    finally:
        link.close()


@pytest.mark.parametrize("attribute", ["preflight", "preflight_pins", "clock_link"])
def test_later_original_custody_replacement_is_sticky(prepared, attribute):
    obj = prepared.make()
    reads = prepared.observed.helper.reads
    setattr(obj, attribute, object())
    denied(obj)
    assert obj.failed and obj.elapsed_seconds is None
    denied(obj)
    assert prepared.observed.helper.reads == reads


def test_final_plan_does_not_reuse_the_preflight_wait_cutoff(prepared, monkeypatch):
    s = prepared
    # Deliberately advance only the outer deadline guard; actual clock/domain
    # samples stay original. No real timing qualification is claimed here.
    monkeypatch.setattr(m.launch.time, "monotonic", lambda: s.p.cutoff + 0.000001)
    obj = s.make()
    assert obj() is None and not obj.failed
    assert s.p.cutoff == s.p.original[-4]  # Original tuple also pins this cutoff.


@pytest.mark.parametrize("fault", ["helper_file", "runtime", "command", "config"])
def test_post_permission_drift_is_still_fully_requalified(prepared, fault):
    s, h = prepared, prepared.observed.helper
    obj = s.make()
    if fault == "helper_file":
        path = h.root / obj.HELPER / "supplemental_recording_service_command.py"
        path.write_bytes(b"raise RuntimeError('PRIVATE never import')\n")
    elif fault == "runtime":
        path = h.root / "usr/local/lib/python3.14/site.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"PRIVATE replaced runtime\n")
    elif fault == "command":
        h.command_fault = b"PRIVATE changed command\x00"
    else:
        obj.configuration_sha256 = "f" * 64
    denied(obj)
    assert obj.failed and obj.elapsed_seconds is None
