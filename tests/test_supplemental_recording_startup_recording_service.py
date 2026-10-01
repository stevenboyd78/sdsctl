"""Published startup custody through native/recording phase routing.

Startup declaration/publication/acceptance, original clock, files, authorization
policy and ledgers are real. Existing native/Engine/process/recording fixtures
remain explicitly synthetic. No audio artifact, installed service or restoration
is qualified by this assembly regression.
"""

import os

import pytest

from . import test_supplemental_recording_service_abandon as abandon_tests
from . import test_supplemental_recording_startup_service as startup_tests

m = startup_tests.m
recording_tests = abandon_tests.recording_tests
native_tests = abandon_tests.native_tests
active_tests = abandon_tests.active_tests
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
    accepted,
    service,
    candidate_fixture,
    launch_fixture,
    native,
    phase,
    recording,
    active,
    abandoned,
) = (
    startup_tests.layout,
    startup_tests.tree,
    startup_tests.routing,
    startup_tests.projection,
    startup_tests.binding,
    startup_tests.directory,
    startup_tests.prepared,
    startup_tests.joined,
    startup_tests.before_handoff,
    startup_tests.accepted,
    startup_tests.service,
    native_tests.candidate_fixture,
    native_tests.launch_fixture,
    native_tests.native,
    native_tests.phase,
    recording_tests.recording,
    active_tests.active,
    abandon_tests.abandoned,
)


@pytest.fixture(autouse=True)
def original_startup_survives_service_cleanup(service):
    s = service
    clock, baseline, original = s.startup.clock, s.baseline, s.startup.original
    names = ("startup-claim.json", "plan.json", "startup-acceptance.json")
    preserved = {name: (s.plan.root / name).read_bytes() for name in names}
    deadlines, offer_deadline = s.plan.deadlines, s.startup_offer.deadline
    yield
    assert s.service.closed and not s.startup.closed and not s.declaration.closed
    assert s.service.clock_witness is clock and not clock.closed
    os.fstat(clock.fd)
    assert s.service.original is original and original.recheck() is s.plan
    assert s.journal.machine.baseline == baseline
    assert s.journal.machine.created_at == deadlines.issued_at
    assert s.journal.machine.hard_deadline == deadlines.recover_by
    assert s.plan.deadlines == deadlines and s.startup_offer.deadline == offer_deadline
    assert preserved == {name: (s.plan.root / name).read_bytes() for name in names}
    # Native fixtures intentionally expire into review. They cannot manufacture
    # a restored App or a verified artifact from synthetic completion bytes.
    assert s.journal.machine.state.phase == "review"
    assert s.journal.machine.state.artifact_sha256 is None


def test_published_owner_reaches_explicit_native_phase(phase):
    native_tests.test_explicit_handoff_retires_idle_before_launch_and_keeps_original_session(phase)


@pytest.mark.parametrize("fault", ["before", "intent", "ready", "capture"])
def test_native_uncertainty_keeps_published_original_owner(phase, fault):
    native_tests.test_launch_uncertainty_consumes_attempt_but_preserves_original_clock_expiry(
        phase, fault
    )


def test_recording_start_keeps_published_original_owner(recording):
    recording_tests.test_recording_requires_separate_explicit_handoff_and_actual_started_return(
        recording
    )


@pytest.mark.parametrize("init_exits", [False, True])
def test_completion_cannot_replace_original_init_exit(recording, init_exits):
    recording_tests.test_finalized_join_requires_worker_publication_and_original_init_exit(
        recording, init_exits
    )


@pytest.mark.parametrize("stage", ["receive", "capture", "collect", "poll", "publish_after"])
def test_completion_refusal_preserves_startup_bytes_and_clock(recording, stage):
    recording_tests.test_completion_or_exit_failure_never_becomes_success_or_pristine_recovery(
        recording, stage
    )


@pytest.mark.parametrize("stage", ["continuity", "read", "stale", "close"])
def test_active_refusal_preserves_original_deadlines(active, stage):
    active_tests.test_failed_active_read_preserves_original_clock_expiry_not_an_idle_fallback(
        active, stage
    )


@pytest.mark.parametrize("lost_close", [False, True])
@pytest.mark.parametrize("init_exits", [False, True])
def test_abandonment_still_requires_original_exits(abandoned, lost_close, init_exits):
    abandon_tests.test_explicit_abandon_waits_for_original_exits_not_close_ack(
        abandoned, lost_close, init_exits
    )
