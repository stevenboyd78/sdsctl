"""Actual App launch/input bindings; explicitly synthetic failure-exit evidence.

These tests cover admission/identity after failure, not actual native exits or
installed recovery. Real retained exits/files are exercised separately by the
App failure-recovery tests (whose App provenance is explicitly synthetic).
"""

import hashlib
import importlib.util
import sys
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_begin as starts

candidate, app, native, launch_case, execution, joined = (
    starts.candidate,
    starts.app,
    starts.native,
    starts.launch_case,
    starts.execution,
    starts.joined,
)
layout, image_umask, supervised, image, configured = (
    starts.layout,
    starts.image_umask,
    starts.supervised,
    starts.image,
    starts.configured,
)
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)
NAME = "supplemental_recording_app_failure"
SPEC = importlib.util.spec_from_file_location(NAME, Path(starts.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@pytest.fixture(params=["preserved", "never"])
def failed_app(joined, monkeypatch, request):
    s = joined
    cls = m.reconcile.Preserved if request.param == "preserved" else m.reconcile.NeverAuthorized
    reader = object.__new__(cls)
    # Explicit fake lower evidence; no claim that the original native actors
    # have actually exited or that this object is valid to run recovery.
    reader.plan, reader.pins, reader.journal = s.plan, s.run.pins, s.journal
    reader.owner = s.run.owner
    reader.operator = SimpleNamespace(
        plan=s.plan,
        clock=s.plan.original_clock,
        ready_sha256=hashlib.sha256(s.run.ready.ready_raw).hexdigest(),
    )
    calls = []
    monkeypatch.setattr(reader, "_context", lambda: calls.append("independent context"))
    s.run.failed = s.run.ready.failed = s.run.ready_qualification.failed = True
    s.run.prelaunch.failed = s.run.prelaunch.candidate.failed = True
    s.run.ready.close()
    host_type = m.AppPreservedHost if request.param == "preserved" else m.AppNeverAuthorizedHost
    restored_type = (
        m.AppPreservedRestoredHost
        if request.param == "preserved"
        else m.AppNeverAuthorizedRestoredHost
    )
    yield SimpleNamespace(s=s, reader=reader, cls=host_type, restored=restored_type, calls=calls)


def test_failed_original_app_can_supply_only_static_bindings(failed_app, monkeypatch):
    c, s = failed_app, failed_app.s

    def forbidden(*args, **kwargs):
        pytest.fail("Exited App must not repoll pre-begin qualification")

    for obj, name in (
        (s.run, "_app_context"),
        (s.run, "_candidate_qualifier"),
        (s.run.ready, "check_before_begin"),
        (s.run.prelaunch, "_pins"),
        (s.run.prelaunch, "_binding"),
        (s.run.ready_qualification, "_binding"),
        (s.idle, "read"),
    ):
        monkeypatch.setattr(obj, name, forbidden)
    before = tuple(s.journal.entries), s.creates, s.ledger.state
    host = c.cls(c.reader, s.run)
    restored = c.restored(c.reader, s.run)
    host._guard()
    restored._guard()
    assert len(c.calls) == 4
    assert before == (tuple(s.journal.entries), s.creates, s.ledger.state)
    host.close()
    restored.close()
    assert not s.journal.fd < 0


@pytest.mark.parametrize(
    "field",
    [
        "prelaunch",
        "candidate",
        "publication",
        "consumption",
        "ready",
        "client",
        "processes",
        "clock",
        "qualify",
        "ready_owner",
        "execution_owner",
        "objects",
        "pins",
        "plan_pin",
        "profile",
        "launch_hash",
        "command",
        "ready_raw",
        "context_raw",
        "received_at",
        "ready_by",
        "read_projected",
        "operator_hash",
        "input_pins",
        "docker",
        "begin_owner",
    ],
)
def test_substitution_or_mutation_cannot_be_adopted_after_failure(failed_app, field):
    c, run = failed_app, failed_app.s.run
    host = c.cls(c.reader, run)
    if field == "candidate":
        run.prelaunch.candidate = object()
    elif field == "publication":
        run.prelaunch.launch_inputs = object()
    elif field == "consumption":
        run.prelaunch.consumption = object()
    elif field == "processes":
        run.ready.processes = object()
    elif field == "clock":
        run.ready.clock = object()
    elif field in ("ready_owner", "execution_owner"):
        setattr(run.prelaunch.candidate, "native_" + field, object())
    elif field == "objects":
        run.app_objects = tuple(list(run.app_objects))
    elif field == "profile":
        run.profile_sha256 = "b" * 64
    elif field == "launch_hash":
        run.launch_sha256 = "b" * 64
    elif field in ("ready_raw", "context_raw"):
        setattr(run.ready, field, b"PRIVATE changed bytes")
    elif field in ("received_at", "ready_by"):
        setattr(run.ready, field, getattr(run.ready, field) + 1)
    elif field == "read_projected":
        run.read.projected = object()
    elif field == "operator_hash":
        c.reader.operator.ready_sha256 = "b" * 64
    elif field == "docker":
        run.read.docker = m.plans.ordinary.Docker()
    else:
        setattr(run, field, object())
    before = tuple(c.s.journal.entries)
    with pytest.raises(m.begin.UnconfirmedHostBegin):
        host.read()
    assert host.failed and tuple(c.s.journal.entries) == before
    host.close()


def test_missing_original_ready_qualification_is_not_pristine_permission(failed_app):
    c = failed_app
    c.s.run.ready_qualification = None
    with pytest.raises(m.begin.UnconfirmedHostBegin):
        c.cls(c.reader, c.s.run)


def test_direct_and_other_branch_gates_remain_closed(failed_app):
    c = failed_app
    wrong = m.AppNeverAuthorizedHost if c.cls is m.AppPreservedHost else m.AppPreservedHost
    direct = m.begin.PreservedHost if c.cls is m.AppPreservedHost else m.begin.NeverAuthorizedHost
    for cls in (wrong, direct):
        with pytest.raises(m.begin.UnconfirmedHostBegin):
            cls(c.reader, c.s.run)

    class Derived(c.cls):
        pass

    with pytest.raises(m.begin.UnconfirmedHostBegin):
        Derived(c.reader, c.s.run)


def test_host_failure_does_not_close_original_evidence_or_journal(failed_app):
    c = failed_app
    host = c.cls(c.reader, c.s.run)
    failures = []

    def other_thread():
        try:
            host.read()
        except m.begin.UnconfirmedHostBegin:
            failures.append(True)

    thread = Thread(target=other_thread)
    thread.start()
    thread.join(timeout=2)
    assert failures == [True] and host.failed and c.s.journal.fd >= 0
    host.close()
