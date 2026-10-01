"""Real local files/pidfd; explicitly synthetic App and PostBegin evidence.

This does not qualify native launch assets, an actual begin, installed App
inputs or recovery. The current publisher deliberately publishes idle-only
inputs; a complete native-tree publication is a separate integration gate.
"""

import os
import time
from dataclasses import replace
from threading import get_ident

import pytest

from . import test_supplemental_recording_app_qualification as apps
from . import test_supplemental_recording_idle_continuity as continuity_tests

m, candidates = apps.m, apps.candidates
candidate, app = apps.candidate, apps.app
layout, image_umask, supervised = apps.layout, apps.image_umask, apps.supervised
image, configured = apps.image, apps.configured
pytestmark = pytest.mark.parametrize("candidate", ["app_bridge"], indirect=True)


@pytest.fixture(params=[1, 2])
def retained(app, monkeypatch, request):
    s = app
    s.before = s.make_app()
    # Choose the original worker policy before rebuilding its constructor pins.
    s.before = m.AppCandidateQualification(
        s.plan,
        s.idle,
        s.witness,
        s.docker,
        published=s.published,
        bridge_sha256=s.before.bridge_sha256,
        **{
            name: request.param if name == "runtime_workers" else getattr(s.before, name)
            for name in m.AppRetainedQualification.PROFILE
        },
    )
    assert s.before() is None
    s.original_consumption = s.before.consumption
    idle_read = s.idle.read
    initial = idle_read()

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
            now = idle_read()  # Synthetic process/lease evidence, not an actual native begin.
            return m.launch.idle_module.Continuity(
                replace(now, sampled_at=initial.sampled_at), now.sampled_at, self.exits
            )

    monkeypatch.setattr(m.launch.idle_module, "PostBegin", PostBegin)
    s.continued = PostBegin()

    def expired(*args):
        raise AssertionError("Pre-begin Idle.read must not be reused")

    monkeypatch.setattr(m.launch.idle_module.Idle, "read", expired)
    s.qualify = m.AppRetainedQualification(s.before, s.continued)
    return s


def test_original_app_pins_survive_ready_expiry_without_renewal(retained, monkeypatch):
    s = retained
    original = s.plan.raw, s.plan.lease, s.continued.finish_by
    with monkeypatch.context() as patch:
        continuity_tests.advance(patch, 125)
        assert time.monotonic() > s.plan.lease["ready_by"]
        assert s.qualify() is None
        assert s.qualify.during(lambda: "read-only") == "read-only"
        follow = m.AppRetainedQualification(s.before, s.continued)
        assert follow() is None
        assert follow.consumption is s.original_consumption
    assert original == (s.plan.raw, s.plan.lease, s.continued.finish_by)
    assert s.qualify.consumption is s.before.consumption is s.original_consumption
    assert s.qualify.runtime_workers == s.before.runtime_workers
    assert not s.witness.exited()


@pytest.mark.parametrize(
    "fault",
    ["claim", "receipt", "lease", "image", "command", "bridge", "source", "environment"],
)
def test_post_begin_does_not_adopt_changed_app_inputs(retained, monkeypatch, fault):
    s = retained
    if fault in ("claim", "receipt", "lease"):
        name = {
            "claim": "app-start/consumed.json",
            "receipt": "app-start/launch.json",
            "lease": "idle/lease.json",
        }[fault]
        target = s.case_root / name
        replacement = target.with_name("replacement")
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(target)
    elif fault == "image":
        original = m.launch.plans.ordinary.Docker.image

        def changed(*args):
            value = original(*args)
            value["Config"]["Cmd"][-1] = "0" * 32
            return value

        monkeypatch.setattr(m.launch.plans.ordinary.Docker, "image", changed)
    elif fault == "command":
        s.container["Config"]["Cmd"][-1] = "0" * 32
    elif fault == "bridge":
        target = s.root / "usr/local/libexec/sdsctl-recording-app-idle.py"
        target.chmod(0o644)
        target.write_bytes(b"PRIVATE")
        target.chmod(0o444)
    elif fault == "source":
        target = s.root / m.launch.plans.fixed.PACKAGE / "__init__.py"
        target.write_bytes(target.read_bytes() + b"# changed\n")
    else:
        s.container["Config"]["Env"].append("PYTHONPATH=/PRIVATE")
    candidates.denied(s.qualify)
    os.fstat(s.witness.fd)


@pytest.mark.parametrize(
    "fault",
    [
        "candidate",
        "failed",
        "locked",
        "consumption",
        "published",
        "pin",
        "plan",
        "continuity",
        "deadline",
        "guard",
        "ready",
        "own_consumption",
    ],
)
def test_original_reader_and_continuity_cannot_be_replaced(retained, fault):
    s = retained
    if fault == "candidate":
        s.qualify.candidate = object()
    elif fault == "failed":
        s.before.failed = True
    elif fault == "locked":
        s.before.lock.acquire()
    elif fault == "consumption":
        s.before.consumption = None
    elif fault == "published":
        s.before.published = replace(s.published)
    elif fault == "pin":
        s.before.bridge_sha256 = "f" * 64
    elif fault == "plan":
        s.before.plan = m.launch.plans.load_bytes(s.plan.raw, s.plan.sha256)
    elif fault == "own_consumption":
        s.qualify.consumption = None
    elif fault == "continuity":
        s.continued.failed = True
    elif fault == "deadline":
        s.continued.finish_by += 1
    else:
        setattr(s.continued, fault, object())
    try:
        candidates.denied(s.qualify)
        os.fstat(s.witness.fd)
    finally:
        if fault == "locked":
            s.before.lock.release()


@pytest.mark.parametrize("fault", ["claim", "late", "exit"])
def test_mid_observation_change_does_not_return_a_result(retained, monkeypatch, fault):
    s = retained

    def observe():
        if fault == "claim":
            (s.case_root / "app-start/consumed.json").write_bytes(b"{}")
        elif fault == "late":
            continuity_tests.advance(monkeypatch, 3)
        else:
            s.continued.exits = frozenset({"native"})
        return "unverified"

    candidates.launch.denied(lambda: s.qualify.during(observe))
    assert s.qualify.failed and s.qualify.elapsed_seconds is None


def test_original_stop_deadline_remains_final(retained, monkeypatch):
    with monkeypatch.context() as patch:
        continuity_tests.advance(patch, 401)
        candidates.denied(retained.qualify)
    assert not retained.witness.exited()


@pytest.mark.parametrize(
    "fault", ["wrong_type", "unobserved", "failed", "consumption_reset", "wrong_continuity"]
)
def test_constructor_cannot_accept_replayed_or_missing_prebegin_reader(retained, fault):
    original, continued = retained.before, retained.continued
    if fault == "wrong_type":
        original = object()
    elif fault == "wrong_continuity":
        continued = object()
    elif fault == "unobserved":
        original.elapsed_seconds = None
    elif fault == "failed":
        original.failed = True
    elif fault == "consumption_reset":
        original.consumption = None
    candidates.launch.denied(lambda: m.AppRetainedQualification(original, continued))


def test_read_only_type_has_no_action_authority(retained):
    obj = retained.qualify
    assert type(obj) is m.AppRetainedQualification
    assert type(obj) is not m.launch.CandidateQualification
    assert type(obj) is not m.launch.RetainedQualification
    assert not any(hasattr(obj, name) for name in ("start", "begin", "restore"))
