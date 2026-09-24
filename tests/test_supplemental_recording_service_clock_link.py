"""Explicit observer/remote domains; fake domains do NOT certify kernel offsets."""

import importlib.util
import os
import sys
from dataclasses import replace
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_recording_service_template as templates
from . import test_supplemental_recording_time_domain as domain_tests

NAME = "supplemental_recording_service_clock_link"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(templates.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
prepared = domain_tests.prepared


class FakeClock:
    def __init__(self, original):
        self.original = original
        self.closes = self.reads = 0
        self.closed = self.failed = False
        self.hook = None
        self.observed = replace(
            original,
            before_ns=original.before_ns + 2 * m.plans.clock.NS,
            after_ns=original.after_ns + 2 * m.plans.clock.NS,
            boottime_ns=original.boottime_ns + 2 * m.plans.clock.NS,
        )

    def read(self):
        self.reads += 1
        self.observed = replace(
            self.observed,
            before_ns=self.observed.before_ns + 100,
            after_ns=self.observed.after_ns + 100,
            boottime_ns=self.observed.boottime_ns + 100,
        )
        if self.hook:
            return self.hook(self.observed)
        return self.observed

    def close(self):
        self.closes += 1


class FakeDomain:
    def __init__(self, proof):
        self.evidence = proof
        self.closes = self.reads = 0
        self.closed = self.failed = False
        self.hook = None

    def refresh(self):
        self.reads += 1
        if self.hook:
            self.hook()
        return self.evidence

    def close(self):
        self.closes += 1


@pytest.fixture
def case(monkeypatch):
    local = templates.clock()
    remote = replace(
        local,
        namespace=(1, 403),
        before_ns=local.before_ns + m.plans.clock.NS,
        after_ns=local.after_ns + m.plans.clock.NS,
        boottime_ns=local.boottime_ns + m.plans.clock.NS,
    )
    plan = templates.m.decode(templates.value()).preview(remote)
    observer = FakeClock(local)
    target = m.domains.process.ProcessIdentity(100, 10, "a" * 64)
    proof = m.domains.Evidence(target, local.namespace, remote.namespace, (1, 405), local)
    domain = FakeDomain(proof)
    now = [12.1]
    monkeypatch.setattr(m.plans.clock, "ClockWitness", FakeClock)
    monkeypatch.setattr(m.domains, "ZeroDomain", FakeDomain)
    monkeypatch.setattr(m.time, "monotonic", lambda: now[0])
    return plan, observer, domain, target, now


def create(case):
    return m.ObserverClock(*case[:4])


def denied(action):
    with pytest.raises(m.UnconfirmedClockLink) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def test_different_domains_compare_only_through_live_proof_without_relabeling(case):
    plan, observer, domain, _, _ = case
    link = create(case)
    original = plan.raw, plan.original_clock, plan.deadlines
    observed = link.read()
    assert observed is observer.observed
    assert observed.namespace == observer.original.namespace != plan.original_clock.namespace
    assert domain.reads == 7 and observer.reads == 4
    assert original == (plan.raw, plan.original_clock, plan.deadlines)
    with pytest.raises(m.plans.UnconfirmedPlan):
        plan.check_clock(observed)  # The ordinary strict boundary is unchanged.
    link.close()
    link.close()
    denied(link.read)
    assert observer.closes == domain.closes == 0


def test_zero_timens_offsets_do_not_require_boottime_equals_monotonic(case):
    plan, observer, *_ = case
    link = create(case)
    observed = link.read()
    assert plan.original_clock.boottime_ns - plan.original_clock.before_ns == 20 * m.plans.clock.NS
    assert observed.boottime_ns - observed.before_ns == 20 * m.plans.clock.NS
    assert observer.closes == 0


@pytest.mark.parametrize("field", ["host_time", "native_time", "original_clock", "init"])
def test_wrong_domain_origin_or_target_refuses_before_any_comparison(case, field):
    plan, observer, domain, target, _ = case
    changes = {
        "host_time": (1, 900),
        "native_time": observer.original.namespace,
        "original_clock": replace(observer.original),
        "init": replace(target, start_ticks=target.start_ticks + 1),
    }
    domain.evidence = replace(domain.evidence, **{field: changes[field]})
    denied(lambda: create(case))
    assert observer.reads == 0 and observer.closes == domain.closes == 0


@pytest.mark.parametrize("field", ["boot", "namespace", "backwards", "suspend", "expired"])
def test_bad_or_expired_observer_samples_never_refresh_service_origin(case, field):
    plan, observer, domain, _, _ = case
    link = create(case)
    original = plan.raw

    def bad(sample):
        if field == "boot":
            return replace(sample, boot="c" * 32)
        if field == "namespace":
            return replace(sample, namespace=plan.original_clock.namespace)
        if field == "backwards":
            return observer.original
        if field == "suspend":
            return replace(sample, boottime_ns=sample.boottime_ns + m.plans.clock.NS)
        delta = int(plan.deadlines.ready_by * m.plans.clock.NS) - sample.boottime_ns
        return replace(
            sample,
            before_ns=sample.before_ns + delta,
            after_ns=sample.after_ns + delta,
            boottime_ns=sample.boottime_ns + delta,
        )

    observer.hook = bad
    denied(link.read)
    assert link.failed and plan.raw == original and observer.closes == domain.closes == 0
    observer.hook = None
    denied(link.read)


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_missing_live_domain_evidence_at_any_bracket_stage_refuses(case, stage):
    _, observer, domain, _, _ = case
    link = create(case)
    stop = domain.reads + stage

    def gone():
        if domain.reads == stop:
            raise m.domains.UnconfirmedDomain("private-secret")

    domain.hook = gone
    denied(link.read)
    assert link.failed and observer.closes == domain.closes == 0


@pytest.mark.parametrize("field", ["plan", "observer", "domain", "target", "local_original"])
def test_replaced_objects_are_not_adopted_or_closed(case, field):
    _, observer, domain, *_ = case
    link = create(case)
    setattr(link, field, object())
    denied(link.read)
    link.close()
    assert observer.closes == domain.closes == 0


@pytest.mark.parametrize("during", [False, True])
def test_changed_domain_proof_cannot_be_resealed(case, during):
    _, _, domain, *_ = case
    link = create(case)

    def change():
        domain.evidence = replace(domain.evidence, user=(1, 901))

    if during:
        domain.hook = change
    else:
        change()
    denied(link.read)
    assert link.failed and domain.closes == 0


def test_mutated_frozen_plan_refuses_before_new_clock_observation(case):
    plan, observer, *_ = case
    link = create(case)
    reads = observer.reads
    object.__setattr__(plan, "firmware", "private-secret")
    denied(link.read)
    assert observer.reads == reads


def test_bounded_comparison_refuses_a_late_domain_refresh(case):
    _, observer, domain, _, now = case
    link = create(case)
    domain.hook = lambda: now.__setitem__(0, now[0] + m.MAX_SECONDS)
    denied(link.read)
    assert link.failed and observer.closes == domain.closes == 0


@pytest.mark.parametrize("field", ["plan", "observer", "domain", "target", "local_original"])
def test_original_binding_changed_during_final_live_guard_is_rejected(case, field):
    _, observer, domain, *_ = case
    link = create(case)
    final = domain.reads + 3

    def changed():
        if domain.reads == final:
            setattr(link, field, object())

    domain.hook = changed
    denied(link.read)
    assert link.failed and observer.closes == domain.closes == 0


def test_plan_mutated_during_final_live_guard_is_rejected(case):
    plan, observer, domain, *_ = case
    link = create(case)
    final = domain.reads + 3

    def changed():
        if domain.reads == final:
            object.__setattr__(plan, "firmware", "private-secret")

    domain.hook = changed
    denied(link.read)
    assert link.failed and observer.closes == domain.closes == 0


@pytest.mark.parametrize("resource", ["observer", "domain"])
@pytest.mark.parametrize("state", ["closed", "failed"])
def test_witness_invalidated_during_last_live_guard_is_not_returned(case, resource, state):
    _, observer, domain, *_ = case
    link = create(case)
    final = domain.reads + 3

    def invalidated():
        if domain.reads == final:
            setattr(getattr(link, resource), state, True)

    domain.hook = invalidated
    denied(link.read)
    assert link.failed and observer.closes == domain.closes == 0


def test_original_readiness_expiring_during_final_guard_is_not_extended(case):
    plan, _, domain, _, now = case
    now[0] = plan.lease["ready_by"] - 0.5
    link = create(case)
    final = domain.reads + 3

    def expired():
        if domain.reads == final:
            now[0] += 0.5  # Still inside the local one-second per-read cap.

    domain.hook = expired
    denied(link.read)
    assert link.failed


def test_contended_comparison_is_consumed_without_touching_witnesses(case):
    _, observer, domain, *_ = case
    link = create(case)
    before = observer.reads, domain.reads
    assert link.lock.acquire(blocking=False)
    try:
        denied(link.read)
    finally:
        link.lock.release()
    denied(link.read)
    assert link.failed and before == (observer.reads, domain.reads)
    assert observer.closes == domain.closes == 0


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_interrupted_observation_does_not_close_original_witnesses(case, error_type):
    _, observer, domain, *_ = case
    link = create(case)

    def interrupted():
        raise error_type()

    domain.hook = interrupted
    with pytest.raises(error_type):
        link.read()
    assert link.failed and observer.closes == domain.closes == 0
    domain.hook = None
    denied(link.read)


def test_foreign_thread_cannot_read_or_close_link(case):
    _, observer, domain, *_ = case
    link, errors = create(case), []

    def foreign():
        for action in (link.read, link.close):
            try:
                action()
            except BaseException as error:
                errors.append(error)

    thread = Thread(target=foreign)
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive() and len(errors) == 2
    assert all(type(error) is m.UnconfirmedClockLink for error in errors)
    assert link.failed and not link.closed and observer.closes == domain.closes == 0
    denied(link.read)


def test_actual_local_handles_stay_caller_owned_with_synthetic_cgroup(prepared, monkeypatch):
    # Real child, pidfd and namespace descriptors; synthetic Docker cgroup and
    # same host namespace. This is NOT distinct-container/installed qualification.
    monkeypatch.setattr(m.domains, "ROOT_UID", os.geteuid())
    observer = m.plans.clock.ClockWitness(prepared.plan.original_clock)
    domain = m.domains.ZeroDomain(observer.original, prepared.witness)
    link = None
    try:
        link = m.ObserverClock(prepared.plan, observer, domain, prepared.identity)
        sample = link.read()
        assert sample.namespace == observer.original.namespace
        assert domain.evidence.native_time == prepared.plan.original_clock.namespace
        link.close()
        assert not observer.closed and not domain.closed and not prepared.witness.exited()
        os.fstat(observer.fd)
        os.fstat(domain.pidfd)
    finally:
        if link is not None:
            link.close()
        domain.close()
        observer.close()


def test_observer_link_is_outside_existing_helper_policy():
    source = Path(m.__file__).with_name("supplemental_recording_host_source.py").read_text()
    launch = Path(m.__file__).with_name("supplemental_recording_host_launch.py").read_text()
    assert '"service_clock_link"' not in source
    assert "service_clock_link" not in launch
