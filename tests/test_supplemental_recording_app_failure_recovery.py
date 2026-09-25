"""Real original worker/init exits, ledger/files/policy; synthetic App provenance.

The App shell is explicitly a fixture, not actual App qualification. Separate
app_failure tests use the real AppLaunch/input pipeline. Engine/HA metadata and
normal-start replies are synthetic. Neither tier proves installed supervision.
"""

import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_recovery as success
from . import test_supplemental_recording_never_authorized as pristine
from . import test_supplemental_recording_preserved_recovery as platform

m, f, b = success.m, success.m.failure, platform.m
files = platform.files
layout, tree, routing, projection, binding, prepared, family, actors, calibration = (
    files.layout,
    files.tree,
    files.routing,
    files.projection,
    files.binding,
    files.prepared,
    files.family,
    files.actors,
    files.calibration,
)
directory, journal, failure, unstarted = (
    files.directory,
    files.journal,
    files.failure,
    pristine.unstarted,
)
PROFILE = files.exits.ready_tests.PROFILE
RAW = b.base.encode(dict(profile=dict(sha256=PROFILE)))
SHA = hashlib.sha256(RAW).hexdigest()


@pytest.fixture
def plan(prepared, calibration, monkeypatch):
    value = files.plan.__wrapped__(prepared, calibration, monkeypatch)
    # App Ready is given the exact decoded plan clock, not an equal precursor.
    # Select it before capture, with no new timestamp or calibration window.
    assert calibration.original == value.original_clock
    calibration.original = value.original_clock
    # Synthetic publication is pinned BEFORE the actual dispatch/Ready capture.
    prepared.pins = replace(
        prepared.pins,
        command=replace(prepared.pins.command, plan_sha256=SHA),
    )
    return value


def app_shell(c):
    """Only provenance/qualifiers are synthetic; retain actual acquired Ready."""
    old = c.run
    run = object.__new__(f.execution.AppLaunch)
    run.owner, run.used, run.confirm_attempted = old.owner, True, True
    run.failed, run.closed = True, True
    run.plan, run.pins, run.journal = old.plan, old.pins, old.journal
    run.projected, run.read = old.projected, old.read
    run.plan_pin = m.plans.PinnedPlan(run.plan)
    run.ready = run._owned_ready = c.case.ready
    run.client = run._owned_client = run.ready.client
    run.endpoint, run.claim = run.client.endpoint, run.client.claim
    run.command, run.launch_sha256, run.profile_sha256 = run.pins.command, SHA, PROFILE
    run.idle, run.witness = object(), object()
    run.begin_owner = None
    original = object.__new__(f.execution.inputs.NativeLaunchQualification)
    holder = object.__new__(f.execution.inputs.qualification.NativeIdleQualification)
    startup = object.__new__(f.execution.inputs.publication.startup.Startup)
    startup.projected = run.projected
    original.startup, original.candidate, original.plan = startup, holder, run.plan
    original.idle, original.witness, original.docker = run.idle, run.witness, run.read.docker
    original.launch_inputs = f.execution.inputs.LaunchPublished(RAW, SHA, (1,) * 9, (2,) * 9)
    original.launch_pins = original._launch_pins()
    original.original = run.input_pins = ("synthetic source inventory", *original.launch_pins)
    original.consumption = ("synthetic App qualification, not installed proof",)
    run.prelaunch = original
    run.app_objects = (
        original,
        startup,
        holder,
        original.launch_inputs,
        original.consumption,
        run.journal,
        run.endpoint,
        run.read,
        run.plan,
        run.projected,
        run.idle,
        run.witness,
        run.read.docker,
    )
    q = object.__new__(f.execution.readiness.NativeReadyQualification)
    q.prelaunch, q.ready = original, run.ready
    q.plan, q.startup, q.candidate = run.plan, startup, holder
    q.launch_inputs, q.idle, q.witness = original.launch_inputs, run.idle, run.witness
    q.docker, q.prelaunch_pins = run.read.docker, run.input_pins
    q.prelaunch_objects = (original, startup, holder, original.launch_inputs, original.consumption)
    q.ready_objects = (
        run.ready,
        run.client,
        run.ready.processes,
        run.ready.clock,
        run.ready.zero_domain,
    )
    q.ready_pins = (
        run.ready.context_raw,
        run.ready.ready_raw,
        run.ready.received_at,
        run.ready.ready_by,
    )
    q.expected, q.expected_payload, q.expected_context = (
        run.pins,
        run.pins.payload(),
        run.ready.context_raw,
    )
    run.qualify = run.ready_qualification = q
    holder.native_execution_used, holder.native_execution_owner = True, run
    holder.native_ready_used, holder.native_ready_owner = True, q
    holder.native_launch_publication = original.launch_inputs
    run.read.plan, run.read.projected = run.plan, run.projected
    run.read.idle, run.read.witness = run.idle, run.witness
    c.run = run


@pytest.fixture(params=["preserved", "never"])
def bridge(request, monkeypatch):
    if request.param == "preserved":
        c = request.getfixturevalue("failure")
        reader = files.reader(c)
        c.host_type, c.restore_type = f.AppPreservedHost, f.AppPreservedRestoredHost
        c.recover = m.recover_preserved
        c.originals, c.outcome, c.stage = files.originals, "unconfirmed", "retained"
    else:
        c = request.getfixturevalue("unstarted")
        reader = pristine.reader(c)
        c.host_type, c.restore_type = f.AppNeverAuthorizedHost, f.AppNeverAuthorizedRestoredHost
        c.recover = m.recover_never_authorized
        c.originals, c.outcome, c.stage = pristine.originals, "not_attempted", "pristine"
    for value in platform.setup_bridge(c, monkeypatch, reader):
        app_shell(value)
        yield value


def recover(c, wait=None):
    return c.recover(c.reader, c.run, c.session, c.wait if wait is None else wait)


def test_original_session_restores_without_changing_recording_outcome(bridge):
    c = bridge
    original = c.originals(c), c.plan.raw, c.operator.result, c.ledger.state
    owners = c.session.executor, c.session.processes, c.session.dispatch
    deadline = c.journal.machine.hard_deadline
    assert recover(c).phase == "complete"
    assert c.created == [platform.h.CONTROL["starting_normal"]]
    assert c.started == ["3" * 64] and c.native_calls == ["e" * 64]
    assert c.journal.machine.state.recording_outcome == c.outcome
    assert c.journal.machine.state.files_stage == c.stage
    assert c.ledger.state is original[3] and c.reader.collected.artifact is None
    assert all(path.read_bytes() == raw for path, raw in original[0].items())
    assert (c.plan.raw, c.operator.result) == original[1:3]
    assert owners == (c.session.executor, c.session.processes, c.session.dispatch)
    assert c.journal.machine.hard_deadline == deadline
    assert c.session.processes.closed and not c.operator.closed and not c.reader.closed
    if c.stage == "pristine":
        assert c.ledger.state.count == 1
        assert c.journal.machine.state.authorization_generation is None
        assert c.reader.collected.files.generation is None
    with pytest.raises(b.UnconfirmedHostBegin):
        recover(c)
    assert c.started == ["3" * 64]


@pytest.mark.parametrize("failure", ["lost", "no_progress"], indirect=True)
@pytest.mark.parametrize("bridge", ["preserved"], indirect=True)
def test_explicit_lost_start_and_no_progress_preserve_original_files(bridge, failure):
    test_original_session_restores_without_changing_recording_outcome(bridge)


@pytest.mark.parametrize("unstarted", ["unpublished_ready"], indirect=True)
@pytest.mark.parametrize("bridge", ["never"], indirect=True)
def test_unpublished_ready_route_does_not_invent_authorization(bridge, unstarted):
    assert bridge.journal.machine.state.phase == "starting_operator"
    test_original_session_restores_without_changing_recording_outcome(bridge)


def test_readonly_host_and_restored_route_cannot_dispatch(bridge):
    c = bridge
    before, entries = c.originals(c), tuple(c.journal.entries)
    host = c.host_type(c.reader, c.run)
    result = host.read().observation
    assert result.files.stage == c.stage and result.candidate.state == "stopped"
    assert result.normal.healthy is result.candidate.healthy is None
    restored = c.restore_type(c.reader, c.run)
    with pytest.raises(b.UnconfirmedHostBegin):
        restored.read()  # No original restoration intent yet.
    assert c.originals(c) == before and tuple(c.journal.entries) == entries
    assert not c.created and not c.native_calls and not c.reader.failed
    host.close()
    restored.close()


def test_lost_return_is_inspected_never_replayed(bridge):
    c = bridge
    c.lost_return = True
    assert recover(c).phase == "complete"
    assert len(c.created) == len(c.started) == 1
    assert c.journal.machine.state.recording_outcome == c.outcome


def test_lost_inspection_expires_original_policy(bridge):
    c = bridge
    c.lost_inspection = True
    assert recover(c, lambda _: platform.expire(c)).phase == "review"
    assert len(c.created) == 1 and c.ledger.state.acknowledgment is None


@pytest.mark.parametrize("after_start", [False, True])
def test_changed_files_do_not_fallback_or_renew_recovery(bridge, after_start):
    c = bridge
    if not after_start:
        c.tree.wav.write_bytes(b"PRIVATE changed fixture")

    def wait(_):
        if after_start and not c.waits:
            c.tree.wav.write_bytes(b"PRIVATE changed fixture after start")
        else:
            platform.expire(c)
        c.waits.append(True)

    assert recover(c, wait).phase == "review"
    assert len(c.created) == int(after_start)
    assert c.reader.failed and not c.reader.closed and not c.operator.closed
    assert c.journal.machine.state.recording_outcome == c.outcome


@pytest.mark.parametrize("values", [(False, False), (None, None), (True, True)])
def test_normal_requires_fresh_healthy_nonrecording_state(bridge, values):
    c = bridge
    c.native_values = values

    def wait(_):
        if c.native_calls:
            platform.expire(c)

    assert recover(c, wait).phase == "review"
    assert c.journal.machine.state.recording_outcome == c.outcome


@pytest.mark.parametrize(
    "fault", ["running", "replacement", "bad_metadata", "finalized", "artifact"]
)
def test_candidate_and_file_stage_cannot_cross_failure_boundary(bridge, monkeypatch, fault):
    c = bridge
    host = c.host_type(c.reader, c.run)
    if fault == "running":
        c.candidate_container["State"].update(Status="running", Running=True, Pid=123)
    elif fault == "replacement":
        different = "e" * 64
        assert different != c.run.pins.init.container_id
        c.candidate_container["Id"] = different
    elif fault == "bad_metadata":
        c.candidate = b.base.App(c.plan.candidate.pin, "unknown")
    else:
        original = c.reader.read

        def changed():
            value = original()
            if fault == "finalized":
                return replace(value, files=replace(value.files, stage="finalized"))
            return replace(value, artifact=SimpleNamespace(fabricated=True))

        monkeypatch.setattr(c.reader, "read", changed)
    with pytest.raises(b.UnconfirmedHostBegin):
        host.read()
    assert host.failed and not c.created and not c.native_calls


@pytest.mark.parametrize("fault", ["executor", "dispatch", "processes", "operator"])
def test_foreign_recovery_owner_cannot_gain_another_attempt(bridge, fault):
    c = bridge
    field = "consume_operator" if fault == "operator" else fault
    old = getattr(c.session, field)
    setattr(c.session, field, object())
    with pytest.raises(b.UnconfirmedHostBegin):
        recover(c)
    assert c.reader.recovery_attempted and not c.created and not c.native_calls
    setattr(c.session, field, old)  # Original fixture custody for cleanup only.
    with pytest.raises(b.UnconfirmedHostBegin):
        recover(c)


def test_changed_app_binding_midread_is_sticky_and_original_ticks_expire(bridge, monkeypatch):
    c = bridge
    original = c.reader.read

    def changed():
        value = original()
        c.run.prelaunch = object()
        return value

    monkeypatch.setattr(c.reader, "read", changed)
    assert recover(c, lambda _: platform.expire(c)).phase == "review"
    assert not c.created and not c.native_calls


def test_entry_cannot_downgrade_or_adopt_success_or_direct_policy(bridge):
    c = bridge
    wrong = m.recover_never_authorized if c.stage == "retained" else m.recover_preserved
    direct = b.recover_preserved if c.stage == "retained" else b.recover_never_authorized
    with pytest.raises(b.UnconfirmedHostBegin):
        wrong(c.reader, c.run, c.session, c.wait)
    with pytest.raises(b.UnconfirmedHostBegin):
        m.recover_finalized(c.reader, c.session, c.wait)
    assert not c.reader.recovery_attempted
    with pytest.raises(b.UnconfirmedHostBegin):
        direct(c.reader, c.run, c.session, c.wait)
    assert c.reader.recovery_attempted and not c.created


def test_late_host_result_cannot_refresh_the_outer_two_second_window(bridge, monkeypatch):
    c = bridge
    original = c.host_type.read

    def late(host):
        result = original(host)
        success.evidence.observations.a.continuity_tests.advance(monkeypatch, 3)
        return result

    monkeypatch.setattr(c.host_type, "read", late)
    assert recover(c, lambda _: platform.expire(c)).phase == "review"
    assert not c.created and not c.native_calls


def test_first_read_error_is_sticky_even_if_a_later_attempt_would_work(bridge, monkeypatch):
    c = bridge
    calls = []

    def once(host):
        calls.append(True)
        assert len(calls) == 1, "A failed observer must never be called again"
        raise OSError("PRIVATE simulated one-time host failure")

    def wait(_):
        c.waits.append(True)
        if len(c.waits) == 2:
            platform.expire(c)

    monkeypatch.setattr(c.host_type, "read", once)
    assert recover(c, wait).phase == "review"
    assert calls == [True] and len(c.waits) >= 2
    assert not c.created and not c.native_calls


def test_read_callback_cannot_be_replaced_during_wait(bridge):
    c = bridge

    def wait(_):
        c.session.read = lambda: pytest.fail("Replacement observer cannot run")

    with pytest.raises(b.UnconfirmedHostBegin):
        recover(c, wait)
    assert c.session.processes.closed and len(c.created) == 1
    assert not c.native_calls and not c.reader.closed and not c.operator.closed
