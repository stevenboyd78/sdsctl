"""Real idle service/journal/FD custody; synthetic Startup/App publication/idle.

This tests read-only assembly, not accepted Startup or complete App inventories.
Those have separate suites. No native launch, recording, installed service or
actual App provenance is claimed by these synthetic publication identities.
"""

import importlib.util
import os
import sys
from pathlib import Path
from threading import Lock

import pytest

from . import test_supplemental_recording_app_launch as app_inputs
from . import test_supplemental_recording_idle_candidate as direct

(
    layout,
    tree,
    routing,
    projection,
    binding,
    directory,
    prepared,
    joined,
    before_handoff,
    transfer,
    service,
    candidate_fixture,
) = (
    direct.layout,
    direct.tree,
    direct.routing,
    direct.projection,
    direct.binding,
    direct.directory,
    direct.prepared,
    direct.joined,
    direct.before_handoff,
    direct.transfer,
    direct.service,
    direct.candidate_fixture,
)
NAME = "supplemental_recording_app_candidate"
SPEC = importlib.util.spec_from_file_location(
    NAME,
    Path(app_inputs.m.__file__).with_name(NAME + ".py"),
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@pytest.fixture
def app_candidate(candidate_fixture, monkeypatch):
    s = candidate_fixture
    owner = object.__new__(m.inputs.publication.startup.Startup)
    owner.original, owner.clock, owner.projected = (
        s.service.original,
        s.service.clock_witness,
        s.service.projected,
    )
    owner.lock = Lock()
    owner.lock.acquire()
    owner.accepted = owner.service_used = owner._service_active = True
    owner.app_idle_publication_used = True
    owner._service_inputs = object()
    owner._service_invalidate = lambda: None
    owner.closed = False
    checks = []

    def owner_check():
        assert not owner.closed
        checks.append(True)

    monkeypatch.setattr(owner, "_input", owner_check)
    monkeypatch.setattr(owner, "_binding", owner_check)
    s.startup = owner
    s.published = m.inputs.publication.NativePublished(
        s.plan.sha256,
        s.plan.lease_sha256,
        "e" * 64,
        (1,) * 9,
        tuple(
            (name, (2,) * 9) for name in m.inputs.qualification.NativeIdleQualification.INPUT_FILES
        ),
        s.plan.native_baseline_sha256,
        tuple(
            (name, (3,) * 6) for name in m.inputs.qualification.NativeIdleQualification.DIRECTORIES
        ),
    )
    s.owner_checks = checks
    try:
        yield s
    finally:
        owner.lock.release()


def prepare(s, **changes):
    profile = direct.PROFILE | dict(published=s.published, bridge_sha256="c" * 64) | changes
    return m.prepare_candidate(s.startup, s.service, **profile)


def test_app_assembly_borrows_original_service_and_does_not_dispatch(app_candidate):
    s, captured = app_candidate, []
    original_clock, original_session = s.service.clock_witness, s.session

    def action():
        before = (
            tuple(s.journal.entries),
            list(s.engine.sent),
            len(s.host.reads),
            len(s.cached_calls),
        )
        candidate = prepare(s)
        captured.append(candidate)
        assert type(candidate) is m.AppIdleCandidate
        assert candidate.recheck() is None
        assert type(candidate.qualifier) is m.inputs.qualification.NativeIdleQualification
        assert candidate.qualifier.published is s.published
        assert candidate.qualifier.elapsed_seconds is None  # Assembly is not qualification.
        assert candidate.witness is s.witness and candidate.startup is s.startup
        assert candidate.reader.projected is s.projected
        assert before == (
            tuple(s.journal.entries),
            s.engine.sent,
            len(s.host.reads),
            len(s.cached_calls),
        )
        assert original_session is s.service.session and original_clock is s.startup.clock
        direct.services.publish(s, "cancel_idle")

    assert direct.at_idle(s, action).phase == "complete"
    candidate = captured[0]
    assert candidate.closed and candidate.idle.closed and candidate.reader.failed
    assert s.owner_checks and s.service.closed and s.witness.closed
    assert s.journal.fd >= 0 and s.journal.machine.state.recording_outcome == "not_attempted"
    assert s.journal.machine.state.launch_intent_sha256 is None
    assert len(s.engine.sent) == 4  # Only the existing explicit idle request/cancel.


def test_no_preparation_outside_original_running_service(app_candidate):
    s = app_candidate
    direct.denied(lambda: prepare(s))
    assert not s.service.candidate_attempted and not s.idles and not s.engine.sent


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "clock",
        "publication",
        "source",
        "profile",
        "not_accepted",
        "retired",
    ],
)
def test_foreign_or_retired_inputs_do_not_assemble_resources(app_candidate, fault):
    s = app_candidate

    def action():
        changes = {}
        if fault == "owner":
            s.startup.original = object()
        elif fault == "clock":
            s.startup.clock = object()
        elif fault == "publication":
            changes["published"] = object()
        elif fault == "source":
            changes["bridge_sha256"] = "invalid"
        elif fault == "profile":
            changes["runtime_workers"] = True
        elif fault == "not_accepted":
            s.startup.accepted = False
        else:
            s.startup._service_active = False
        prepare(s, **changes)

    direct.denied(lambda: direct.at_idle(s, action))
    assert s.service.failed and s.service.candidate_attempted
    assert s.service.closed and s.witness.closed and s.journal.fd >= 0
    assert len(s.engine.sent) == 2 and all(idle.closed for idle in s.idles)
    assert s.service.candidate is None


@pytest.mark.parametrize("fault", ["reader", "qualifier", "interrupt"])
def test_partial_constructor_failure_releases_only_acquired_resources(
    app_candidate, monkeypatch, fault
):
    s = app_candidate

    def broken(*args, **kwargs):
        if fault == "interrupt":
            raise KeyboardInterrupt()
        raise OSError("PRIVATE construction failure")

    target = (
        m.launch.BootstrapHost
        if fault == "reader"
        else m.inputs.qualification.NativeIdleQualification
    )
    monkeypatch.setattr(target, "__init__", broken)
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            direct.at_idle(s, lambda: prepare(s))
    else:
        direct.denied(lambda: direct.at_idle(s, lambda: prepare(s)))
    assert s.idles and all(idle.closed and idle.pidfd == -1 for idle in s.idles)
    assert s.service.closed and s.witness.closed and len(s.engine.sent) == 2
    assert s.journal.fd >= 0


@pytest.mark.parametrize(
    "fault",
    [
        "published",
        "qualifier",
        "clock",
        "session",
        "witness",
        "profile",
        "launch_published",
    ],
)
def test_changed_original_inputs_cannot_be_requalified_in_idle(app_candidate, fault):
    s = app_candidate

    def action():
        candidate = prepare(s)
        if fault in ("published", "qualifier", "witness"):
            setattr(candidate, fault, object())
        elif fault == "clock":
            s.startup.clock = object()
        elif fault == "session":
            s.service.session = object()
        elif fault == "profile":
            candidate.qualifier.runtime_workers = 1
        else:
            candidate.qualifier.native_launch_used = True
        direct.denied(candidate.recheck)
        assert candidate.failed
        raise KeyboardInterrupt()  # Close original fixture custody without recovery inference.

    with pytest.raises(KeyboardInterrupt):
        direct.at_idle(s, action)
    assert s.service.closed and s.witness.closed and len(s.engine.sent) == 2
    assert all(idle.closed for idle in s.idles)


def test_original_direct_launch_gate_still_refuses_app_candidate(app_candidate):
    s = app_candidate

    def action():
        candidate = prepare(s)
        assert type(candidate) is not m.operator.IdleCandidate
        s.service.prepare_launch(object(), launch_sha256="a" * 64, profile_sha256="b" * 64)

    direct.denied(lambda: direct.at_idle(s, action))
    assert s.service.prepared_launch is None and len(s.engine.sent) == 2


def test_second_preparation_keeps_original_cleanup_and_refuses(app_candidate):
    s, captured = app_candidate, []

    def action():
        captured.append(prepare(s))
        prepare(s)

    direct.denied(lambda: direct.at_idle(s, action))
    assert s.service.candidate is captured[0] and captured[0].closed
    assert len(s.idles) == 1 and s.idles[0].pidfd == -1
    assert s.journal.fd >= 0 and len(s.engine.sent) == 2


def test_reader_replacement_cannot_redirect_cleanup(app_candidate):
    s, captured = app_candidate, []

    def action():
        candidate = prepare(s)
        captured.append(candidate.reader)
        candidate.reader = object()
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        direct.at_idle(s, action)
    assert captured[0].failed and s.idles[0].pidfd == -1 and s.witness.closed
    os.fstat(s.journal.fd)
