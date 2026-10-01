"""Full qualifier join with synthetic Engine/cgroup/source, actual local handles."""

import importlib.util
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_permission_probe as probe_tests  # noqa: F401
from . import test_supplemental_recording_permission_review as review_tests  # noqa: F401
from . import test_supplemental_recording_preflight_qualification as previous

NAME = "qualify_supplemental_recording_permission"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(previous.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

layout, image_umask, supervised = previous.layout, previous.image_umask, previous.supervised
image, configured, helper = previous.image, previous.configured, previous.helper
denied = previous.denied
pytestmark = pytest.mark.parametrize("helper", ["permission"], indirect=True)


@pytest.fixture
def observed(helper, monkeypatch):
    monkeypatch.setattr(m.probe.permission, "ROOT_UID", os.geteuid())
    clock = m.launch.plans.clock.ClockWitness(m.launch.plans.clock.read())
    domain = review = None
    try:
        domain = m.launch.time_domain.ZeroDomain(clock.original, helper.witness)

        def make(**changes):
            return m.PermissionProbeQualification(
                **(
                    dict(
                        template=helper.template,
                        template_sha256=helper.template.sha256,
                        baseline_sha256="d" * 64,
                        observer_identity=helper.observer_identity,
                        observer=clock,
                        domain=domain,
                        witness=helper.witness,
                        docker=helper.docker,
                    )
                    | helper.args
                    | changes
                )
            )

        remote = m.launch.plans.clock.read()
        proof = domain.evidence
        reverse = m.launch.time_domain.Evidence(
            helper.observer_identity, proof.native_time, proof.host_time, proof.user, remote
        )
        raw = (
            m.launch.base.encode(
                dict(
                    schema=1,
                    kind=m.probe.permission.CHALLENGE_KIND,
                    case=helper.plan.case,
                    template_sha256=helper.template.sha256,
                    baseline_sha256="d" * 64,
                    target=asdict(helper.witness.identity),
                    observer=asdict(helper.observer_identity),
                    original_clock=asdict(remote),
                    domain_sha256=reverse.sha256,
                    deadline=remote.after_ns / m.probe.clock.NS + 15,
                    wait_by=remote.after_ns / m.probe.clock.NS + 13,
                    nonce="e" * 64,
                )
            )
            + b"\n"
        )
        review = m.reviews.Review(
            raw,
            helper.template,
            helper.template.sha256,
            "d" * 64,
            helper.observer_identity,
            helper.witness,
            clock,
            domain,
        )
        yield SimpleNamespace(helper=helper, clock=clock, domain=domain, make=make, review=review)
    finally:
        if review:
            review.close()
        if domain:
            domain.close()
        clock.close()


def test_full_one_attempt_callback_keeps_old_policies_closed_and_borrowers_live(observed):
    h = observed.helper
    obj = observed.make()
    profile = obj._source_layout(h.root)
    assert profile.permission_probe and not profile.startup
    assert obj.MAX_SECONDS == 2
    assert obj.review_and_qualify(observed.review) is None
    assert obj.permission_attempted and not obj.failed and 0 < obj.elapsed_seconds < 2
    assert h.reads == 2 and h.images == 2
    assert not observed.clock.closed and not observed.domain.closed and not h.witness.exited()
    assert obj.plan.original_clock == observed.clock.original
    assert obj.origin is observed.clock.original
    denied(lambda: obj.review_and_qualify(observed.review))
    assert obj.failed and h.reads == 2
    denied(h.make)
    denied(
        lambda: previous.m.PreflightProbeQualification(
            h.template,
            h.template.sha256,
            observed.clock,
            observed.domain,
            h.witness,
            h.docker,
            **h.args,
        )
    )


@pytest.mark.parametrize("index", range(9))
def test_each_command_field_is_independently_fixed_before_engine_reads(observed, index):
    command = list(observed.helper.args["command"])
    command[index] = "PRIVATE invalid command"
    denied(lambda: observed.make(command=tuple(command)))
    assert observed.helper.reads == observed.helper.images == 0


@pytest.mark.parametrize("fault", ["list", "extra", "short", "service", "startup"])
def test_no_command_alias_or_upgrade_is_admitted(observed, fault):
    command = observed.helper.args["command"]
    if fault == "list":
        command = list(command)
    elif fault == "extra":
        command += ("extra",)
    elif fault == "short":
        command = command[:-1]
    else:
        command = command[:-1] + ("--finite-service" if fault == "service" else "--startup-probe",)
    denied(lambda: observed.make(command=command))
    assert observed.helper.reads == 0


@pytest.mark.parametrize(
    "field", ["baseline_sha256", "observer_identity", "template", "observer", "domain"]
)
def test_missing_independent_inputs_do_not_read_or_adopt_a_challenge(observed, field):
    denied(lambda: observed.make(**{field: None}))
    assert observed.helper.reads == 0


def test_other_live_peer_cannot_be_declared_as_current_observer(observed):
    denied(lambda: observed.make(observer_identity=observed.helper.witness.identity))
    assert observed.helper.reads == 0


@pytest.mark.parametrize(
    "field", ["baseline_sha256", "observer_identity", "observer_argument", "cutoff"]
)
def test_changed_original_input_cannot_renew_or_retarget_permission(observed, field):
    obj = observed.make()
    old = getattr(obj, field)
    setattr(obj, field, object())
    denied(obj)
    setattr(obj, field, old)
    denied(obj)
    assert observed.helper.reads == 0


@pytest.mark.parametrize(
    "field",
    ["template", "timer", "domain", "target", "observer", "template_sha256", "baseline_sha256"],
)
def test_callback_rejects_substituted_review_input_before_engine_reads(observed, field):
    obj = observed.make()
    setattr(observed.review, field, object())
    denied(lambda: obj.review_and_qualify(observed.review))
    assert obj.failed and obj.permission_attempted and observed.helper.reads == 0


def test_noop_or_report_object_cannot_replace_a_retained_review(observed):
    obj = observed.make()
    denied(lambda: obj.review_and_qualify(SimpleNamespace()))
    assert obj.permission_attempted and observed.helper.reads == 0


@pytest.mark.parametrize("key", ["NetworkMode", "ReadonlyRootfs", "Privileged"])
def test_full_confinement_remains_mandatory_after_challenge_review(observed, key):
    observed.helper.fault = lambda value: value["HostConfig"].update(
        {key: "host" if key == "NetworkMode" else key == "Privileged"}
    )
    obj = observed.make()
    denied(lambda: obj.review_and_qualify(observed.review))
    assert obj.failed


@pytest.mark.parametrize(
    "module",
    ["supplemental_recording_permission_probe.py", "supplemental_recording_service_permission.py"],
)
def test_incomplete_permission_inventory_cannot_fall_back_to_startup(observed, module):
    obj = observed.make()
    (observed.helper.root / obj.HELPER / module).unlink()
    denied(lambda: obj.review_and_qualify(observed.review))
    assert obj.failed


def test_changed_current_observer_is_caught_after_final_clock_sample(observed, monkeypatch):
    obj = observed.make()
    read = observed.clock.read
    identity = m.probe.process.read_identity

    def changed():
        value = read()
        monkeypatch.setattr(
            m.probe.process,
            "read_identity",
            lambda pid, cid: (
                replace(observed.helper.observer_identity, start_ticks=1)
                if pid == os.getpid()
                else identity(pid, cid)
            ),
        )
        return value

    monkeypatch.setattr(observed.clock, "read", changed)
    denied(obj)
    assert observed.helper.reads == 0


def test_callback_failure_keeps_all_original_resources_caller_owned(observed, monkeypatch):
    obj = observed.make()
    monkeypatch.setattr(observed.review, "read", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        obj.review_and_qualify(observed.review)
    assert obj.failed and obj.permission_attempted
    assert not observed.clock.closed and not observed.domain.closed
    os.fstat(observed.helper.witness.fd)
    denied(lambda: obj.review_and_qualify(observed.review))


def test_observer_only_modules_are_not_selected_by_observed_helper(helper):
    for name in (
        NAME,
        "supplemental_recording_permission_review",
        "supplemental_recording_permission_sender",
    ):
        assert name not in m.launch.helper_source.PERMISSION_MODULES
