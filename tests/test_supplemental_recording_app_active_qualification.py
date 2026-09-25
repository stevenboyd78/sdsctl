"""Real private input/socket/receipt inodes; synthetic Start/PostBegin/Engine.

No actual scanner, native begin, service selection or success is supplied here.
Actual private returns, native receipt formats and actor retention have separate
loopback/owned-child tests. This tests the explicit App input-phase join only.
"""

import importlib.util
import os
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from threading import get_ident
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_ready_qualification as r
from . import test_supplemental_recording_idle_continuity as continuity_tests
from . import test_supplemental_recording_receipt_inventory as receipt_tests

candidate, app, native, launch_case, ready_case = (
    r.candidate,
    r.app,
    r.native,
    r.launch_case,
    r.ready_case,
)
layout, image_umask, supervised, image, configured = (
    r.layout,
    r.image_umask,
    r.supervised,
    r.image,
    r.configured,
)
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)

NAME = "qualify_supplemental_recording_app_active"
SPEC = importlib.util.spec_from_file_location(NAME, Path(r.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
assert m.outputs is receipt_tests.m


@pytest.fixture
def active(ready_case, monkeypatch):
    s = ready_case
    s.before = s.make_ready()
    assert s.before() is None
    idle_read = s.idle.read
    original = idle_read()
    s.ready.client.begun = True
    s.begin_reads = 0
    s.start = object.__new__(m.begin.Start)
    s.start.plan, s.start.ready = s.plan, s.ready
    s.start.closed = s.start.failed = False
    s.start.run = object()
    s.start.relay = object.__new__(m.begin.relayed.Relay)
    relay = s.start.relay
    relay.ready, relay.guard = s.ready, object()
    relay.phase = "started"
    relay.intent_at = time.monotonic()
    relay.native_binding = m.outputs.channel.Binding(
        s.projected.native,
        s.before.generation,
        s.plan.projection_sha256,
        s.plan.candidate_runtime.source,
        relay.intent_at + 8,
        relay.intent_at + 170,
    )
    s.start.intent = SimpleNamespace(
        now=relay.intent_at,
        start_by=relay.native_binding.start_by,
        finish_by=relay.native_binding.finish_by,
    )

    class PostBegin:
        def __init__(self):
            self.plan, self.idle = s.plan, s.idle
            self.guard, self.ready = relay.guard, s.ready
            self.finish_by = s.plan.lease["stop_by"]
            self.owner = os.getpid(), get_ident()
            self.closed = self.failed = False
            self.exits = frozenset()

        def _guard(self, deadline):
            m.require(not self.closed and not self.failed)
            m.require(self.owner == (os.getpid(), get_ident()))
            m.require(time.monotonic() < min(deadline, self.finish_by))

        def read(self):
            self._guard(self.finish_by)
            now = idle_read()  # Synthetic kernel/lease adapter; no actual begin.
            return m.launch.idle_module.Continuity(
                replace(now, sampled_at=original.sampled_at),
                now.sampled_at,
                self.exits,
            )

    monkeypatch.setattr(m.launch.idle_module, "PostBegin", PostBegin)
    s.continued = PostBegin()

    def history(start, *, require_live=False):
        assert start is s.start and require_live
        s.begin_reads += 1
        m.require(
            not start.closed
            and not start.failed
            and start.relay.phase in ("started", "completed", "closed")
        )
        m.require(s.continued.exits == frozenset())
        return None

    monkeypatch.setattr(m.begin.Start, "retained_history", history)

    def expired(*args):
        pytest.fail("Post-begin must not renew or recheck pre-begin readiness")

    monkeypatch.setattr(m.launch.received.Ready, "check_before_begin", expired)
    monkeypatch.setattr(m.launch.idle_module.Idle, "read", expired)
    s.make_active = lambda: m.NativeActiveQualification(s.before, s.start, s.continued)
    s.plan_receipts = m.outputs.owner.Plan(
        s.plan.case,
        s.before.generation,
        s.projected.native.contract.audio_endpoint_sha256,
        relay.intent_at,
        relay.intent_at + 2,
        relay.intent_at + 150,
        relay.intent_at + 158,
    )
    s.receipt_values = [
        dict(
            schema=1,
            plan=asdict(s.plan_receipts),
            root_sha256=s.projected.native.contract.root_sha256,
            baseline_sha256=s.projected.native.contract.baseline_sha256,
        ),
        dict(
            schema=1,
            case=s.plan.case,
            generation=s.before.generation,
            at_monotonic=relay.intent_at,
            snapshot=None,
        ),
    ]

    def append(index):
        target = s.case_root / "receipts" / m.outputs.NAMES[index]
        target.write_bytes(m.outputs.channel.encode(s.receipt_values[index]))
        target.chmod(0o600)

    s.append = append
    return s


def test_original_input_custody_survives_ready_expiry_without_expanding_authority(
    active, monkeypatch
):
    s = active
    original = s.plan.raw, s.plan.lease, s.before.consumption, s.startup.clock
    q = s.make_active()
    fds = len(os.listdir("/proc/self/fd"))
    assert s.begin_reads == 1
    assert q() is None
    assert s.begin_reads == 3
    s.append(0)
    assert q.during(lambda: "verified read-only observation") == "verified read-only observation"
    assert s.begin_reads == 5
    assert len(q.receipts.inventory.records) == 1
    with monkeypatch.context() as patch:
        continuity_tests.advance(patch, 125)
        assert time.monotonic() > s.plan.lease["ready_by"]
        assert q() is None
    assert original == (s.plan.raw, s.plan.lease, s.before.consumption, s.startup.clock)
    assert q.consumption is s.before.consumption
    assert len(os.listdir("/proc/self/fd")) == fds and not s.witness.exited()
    assert not any(callable(getattr(q, method, None)) for method in ("start", "begin", "restore"))
    assert type(q) is not m.launch.RetainedQualification
    assert type(q) is not m.launch.CandidateQualification


def test_valid_append_across_source_bracket_is_not_a_changed_configuration_or_ack(active):
    s = active
    q = s.make_active()
    assert q() is None
    assert q.during(lambda: s.append(0)) is None
    assert q.during(lambda: s.append(1)) is None
    assert len(q.receipts.inventory.records) == 2
    assert q.elapsed_seconds < 2 and q.consumption is s.before.consumption


@pytest.mark.parametrize(
    "fault",
    [
        "socket",
        "launch",
        "baseline",
        "receipt",
        "unknown",
        "directory",
        "source",
        "bridge",
        "environment",
    ],
)
def test_post_begin_inputs_cannot_be_replaced_or_extended_arbitrarily(active, fault):
    s = active
    q = s.make_active()
    s.append(0)
    q()
    if fault == "socket":
        (s.case_root / "sockets/api.sock").unlink()
        s.bind("api.sock")
    elif fault in ("launch", "baseline", "receipt"):
        name = {
            "launch": "launch/launch.json",
            "baseline": "baseline/baseline.json",
            "receipt": "receipts/prepared.json",
        }[fault]
        target = s.case_root / name
        other = target.with_name("replacement")
        other.write_bytes(target.read_bytes())
        other.chmod(0o600)
        other.replace(target)
    elif fault == "unknown":
        (s.case_root / "receipts/PRIVATE").write_bytes(b"PRIVATE")
    elif fault == "directory":
        (s.case_root / "receipts").chmod(0o755)
    elif fault == "source":
        target = s.root / m.launch.plans.fixed.PACKAGE / "__init__.py"
        target.write_bytes(target.read_bytes() + b"# changed\n")
    elif fault == "bridge":
        target = s.root / "usr/local/libexec/sdsctl-recording-app-idle.py"
        target.chmod(0o644)
        target.write_bytes(b"PRIVATE")
        target.chmod(0o444)
    else:
        s.container["Config"]["Env"].append("PYTHONPATH=/PRIVATE")
    r.launches.denied(q)
    assert q.failed and q.elapsed_seconds is None
    os.fstat(s.witness.fd)


@pytest.mark.parametrize(
    "fault",
    [
        "prebegin",
        "prebegin_failed",
        "consumption",
        "active_owner",
        "start",
        "start_failed",
        "relay",
        "binding",
        "continuity",
        "guard",
        "finish",
        "receipts",
        "retired",
        "worker_exit",
        "worker_policy",
    ],
)
def test_original_begin_and_owners_cannot_be_swapped_or_renewed(active, fault):
    s = active
    q = s.make_active()
    q()
    if fault == "prebegin":
        q.prebegin = object()
    elif fault == "prebegin_failed":
        s.before.failed = True
    elif fault == "consumption":
        s.before.consumption = None
    elif fault == "active_owner":
        s.before.native_active_owner = object()
    elif fault == "start":
        q.start = object()
    elif fault == "start_failed":
        s.start.failed = True
    elif fault == "relay":
        s.start.relay = object()
    elif fault == "binding":
        s.start.relay.native_binding = replace(s.start.relay.native_binding)
    elif fault == "continuity":
        q.continuity = object()
    elif fault == "guard":
        s.continued.guard = object()
    elif fault == "finish":
        s.continued.finish_by += 1
    elif fault == "receipts":
        q.receipts = object()
    elif fault == "retired":
        s.startup._service_active = False
    elif fault == "worker_policy":
        s.before.runtime_workers = 2
    else:
        s.continued.exits = frozenset({"native"})
    r.launches.denied(q)
    assert q.failed and q.elapsed_seconds is None


def test_second_active_reader_cannot_reacquire_original_receipts(active):
    s = active
    q = s.make_active()
    q()
    r.launches.denied(s.make_active)
    assert q() is None
    assert s.before.native_active_owner is q


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_start",
        "not_qualified",
        "wrong_ready",
        "generation",
        "projection",
        "source",
        "manifest",
        "begin_bounds",
    ],
)
def test_failed_initial_handoff_consumes_original_slot(active, fault):
    s = active
    if fault == "wrong_start":
        s.start = object()
    elif fault == "not_qualified":
        s.before.elapsed_seconds = None
    elif fault == "wrong_ready":
        s.continued.ready = object()
    elif fault == "begin_bounds":
        s.start.intent.finish_by += 1
    else:
        field = {
            "generation": "generation",
            "projection": "projection_sha256",
            "source": "source_sha256",
            "manifest": "stored",
        }[fault]
        value = s.projected.host if field == "stored" else "d" * 64
        s.start.relay.native_binding = replace(s.start.relay.native_binding, **{field: value})
    r.launches.denied(s.make_active)
    assert s.before.native_active_used and s.before.failed
    r.launches.denied(s.make_active)
    os.fstat(s.witness.fd)


@pytest.mark.parametrize("fault", ["receipt", "actor", "owner"])
def test_mid_observation_receipt_or_actor_change_does_not_return_result(active, fault):
    s = active
    q = s.make_active()
    s.append(0)
    q()

    def change():
        if fault == "receipt":
            (s.case_root / "receipts/prepared.json").write_bytes(b"PRIVATE")
        elif fault == "actor":
            s.continued.exits = frozenset({"native"})
        else:
            s.startup._service_active = False
        return "unverified"

    r.launches.denied(lambda: q.during(change))
    assert q.failed and q.elapsed_seconds is None


def test_active_reader_cannot_enter_the_existing_launch_gate(active):
    s = active
    q = s.make_active()
    q()
    run = object.__new__(m.launch.Launch)
    run.qualify = q
    # This is the internal exact-type guard, not the public failure wrapper
    # which suppresses unrelated exception context during an actual operation.
    with pytest.raises(m.launch.UnconfirmedHostLaunch) as error:
        run._candidate_qualifier()
    assert str(error.value) == m.launch.MESSAGE
    assert q() is None
