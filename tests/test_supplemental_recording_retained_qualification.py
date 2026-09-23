"""Actual source/runtime trees and init pidfd; synthetic Docker/PostBegin inputs.

The original PostBegin file/actor join is tested separately. These fixtures do
not qualify an installed namespace, image, real native begin or host service.
"""

import os
import time
from dataclasses import replace
from threading import get_ident

import pytest

from . import test_supplemental_recording_candidate_qualification as candidate_tests
from . import test_supplemental_recording_idle_continuity as continuity_tests

m = candidate_tests.m
layout, image_umask = candidate_tests.layout, candidate_tests.image_umask
candidate, supervised, image, configured = (
    candidate_tests.candidate,
    candidate_tests.supervised,
    candidate_tests.image,
    candidate_tests.configured,
)


@pytest.fixture
def joined(candidate, image, monkeypatch):
    s = candidate
    idle_read = s.idle.read
    initial = idle_read()
    s.events.clear()
    s.idles = 0

    class PostBegin:
        def __init__(self):
            self.plan, self.idle = s.plan, s.idle
            self.guard, self.ready = object(), object()
            self.finish_by = s.plan.lease["stop_by"]
            self.owner = os.getpid(), get_ident()
            self.closed = self.failed = False
            self.exits = frozenset()

        def _guard(self, deadline):
            assert not self.closed and not self.failed
            assert self.owner == (os.getpid(), get_ident())
            assert time.monotonic() < min(deadline, self.finish_by)

        def read(self):
            self._guard(self.finish_by)
            now = idle_read()  # Explicit synthetic lease/proc evidence from source fixture.
            return m.idle_module.Continuity(
                replace(now, sampled_at=initial.sampled_at), now.sampled_at, self.exits
            )

    monkeypatch.setattr(m.idle_module, "PostBegin", PostBegin)
    continued = PostBegin()

    # The new qualifier must never use the actual pre-begin Idle API.
    def expired(*args):
        raise AssertionError("Pre-begin Idle.read was reused")

    monkeypatch.setattr(m.idle_module.Idle, "read", expired)
    s.continued = continued
    s.qualify = m.RetainedQualification(
        continued,
        s.witness,
        s.docker,
        image_environment_sha256=m.runtime.environment(image),
        timezone=candidate_tests.env.TIMEZONE,
        hostname=candidate_tests.env.HOSTNAME,
        architecture="amd64",
    )
    return s


def test_original_readiness_expiry_does_not_prevent_fixed_post_begin_qualification(
    joined, monkeypatch
):
    s = joined
    old = s.plan.raw, s.plan.lease, s.continued.finish_by
    with monkeypatch.context() as patch:
        continuity_tests.advance(patch, 125)
        assert time.monotonic() > s.plan.lease["ready_by"]
        assert s.qualify() is None
        assert s.qualify.during(lambda: "read-only observation") == "read-only observation"
    assert old == (s.plan.raw, s.plan.lease, s.continued.finish_by)
    assert not s.witness.exited() and s.idles == 4
    assert s.containers == s.images == 4


def test_source_and_runtime_are_fresh_around_actual_callback(joined):
    s = joined
    observed = []

    def inspect():
        observed.append(s.root.is_dir())
        return 42

    assert s.qualify.during(inspect) == 42 and observed == [True]
    assert s.qualify.elapsed_seconds < s.qualify.MAX_SECONDS
    assert not any(hasattr(s.qualify, name) for name in ("begin", "start", "restore"))


@pytest.mark.parametrize(
    "fault", ["source", "runtime", "environment", "mount", "generation", "init"]
)
def test_post_begin_does_not_weaken_original_protection(joined, fault):
    s = joined
    if fault == "source":
        target = s.root / m.plans.fixed.PACKAGE / "__init__.py"
        target.write_bytes(target.read_bytes() + b"# changed\n")
    elif fault == "runtime":
        target = s.root / "usr/local/lib/python3.14/os.py"
        target.write_bytes(target.read_bytes() + b"# changed\n")
    elif fault == "environment":
        s.container["Config"]["Env"] = [*s.container["Config"]["Env"], "PYTHONPATH=/PRIVATE"]
    elif fault == "mount":
        s.container["Mounts"].append(
            dict(Type="bind", Source="/PRIVATE", Destination="/usr", RW=False)
        )
    elif fault == "generation":
        s.container["State"]["StartedAt"] = "2026-09-24T00:00:00Z"
    else:
        s.container["State"]["Pid"] += 1
    candidate_tests.denied(s.qualify)


@pytest.mark.parametrize(
    "fault", ["closed", "failed", "deadline", "guard", "ready", "idle", "plan"]
)
def test_continuity_cannot_be_replaced_or_renewed(joined, fault):
    s = joined
    if fault in ("closed", "failed"):
        setattr(s.continued, fault, True)
    elif fault == "deadline":
        s.continued.finish_by += 10
    elif fault in ("guard", "ready", "idle"):
        setattr(s.continued, fault, object())
    else:
        s.continued.plan = m.plans.load_bytes(s.plan.raw, s.plan.sha256)
    candidate_tests.denied(s.qualify)


@pytest.mark.parametrize(
    "fault", ["source", "runtime", "lease", "actor_exit", "continuity", "returned_late"]
)
def test_mutation_during_read_only_observation_never_returns_evidence(joined, monkeypatch, fault):
    s = joined

    def observe():
        if fault == "source":
            target = s.root / m.plans.fixed.PACKAGE / "__init__.py"
            target.write_bytes(target.read_bytes() + b"# changed\n")
        elif fault == "runtime":
            target = s.root / "usr/local/lib/python3.14/os.py"
            target.write_bytes(target.read_bytes() + b"# changed\n")
        elif fault == "lease":
            s.fault = "idle_files"
        elif fault == "actor_exit":
            s.continued.exits = frozenset({"native"})
        elif fault == "continuity":
            s.continued.failed = True
        else:
            continuity_tests.advance(monkeypatch, 3)
        return "PRIVATE unverified result"

    candidate_tests.launch.denied(lambda: s.qualify.during(observe))
    assert s.qualify.failed and s.qualify.elapsed_seconds is None


def test_original_stop_deadline_remains_final(joined, monkeypatch):
    with monkeypatch.context() as patch:
        continuity_tests.advance(patch, 401)
        candidate_tests.denied(joined.qualify)
    assert not joined.witness.exited()


def test_source_qualification_does_not_invent_worker_health(joined):
    joined.continued.exits = frozenset({"native", "guardian", "watchdog"})
    assert joined.qualify() is None
    assert not joined.witness.exited()
    # No native-health/recording/owner-restored evidence is returned.


def test_missing_post_begin_capability_refuses_before_io(candidate):
    candidate_tests.launch.denied(
        lambda: m.RetainedQualification(object(), candidate.witness, candidate.docker)
    )
    assert not candidate.events


def test_construction_after_ready_expiry_keeps_exact_original_bounds(joined, monkeypatch):
    s = joined
    with monkeypatch.context() as patch:
        continuity_tests.advance(patch, 125)
        follow = m.RetainedQualification(
            s.continued,
            s.witness,
            s.docker,
            image_environment_sha256=s.qualify.image_environment_sha256,
            timezone=s.qualify.timezone,
            hostname=s.qualify.hostname,
            architecture=s.qualify.architecture,
        )
        assert follow.during(lambda: "retained observation") == "retained observation"
        assert follow.continuity_finish == s.plan.lease["stop_by"]
        candidate_tests.launch.denied(s.make)  # Bootstrap still expires at ready_by.


def test_retained_qualification_cannot_replace_exact_bootstrap_type(joined):
    assert type(joined.qualify) is m.RetainedQualification
    assert type(joined.qualify) is not m.CandidateQualification
