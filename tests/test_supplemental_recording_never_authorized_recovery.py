"""Actual never-authorized evidence/policy and explicit fixture platform facts.

Same synthetic Launch/Engine/HA metadata adapter as preserved recovery tests;
actual original exits, pristine files and distinct journal/ledger gates. No
installed service, scanner, native dispatch or recording acceptance claim.
"""

from dataclasses import replace
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_never_authorized as files
from . import test_supplemental_recording_preserved_recovery as preserved

m, h = preserved.m, preserved.h
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
directory, plan, journal, unstarted = files.directory, files.plan, files.journal, files.unstarted


@pytest.fixture
def bridge(unstarted, monkeypatch):
    yield from preserved.setup_bridge(unstarted, monkeypatch, files.reader(unstarted))


def recover(c, wait=None):
    return m.recover_never_authorized(c.reader, c.run, c.session, c.wait if wait is None else wait)


@pytest.mark.parametrize("unstarted", ["ready", "unpublished_ready"], indirect=True)
def test_native_exited_never_authorized_restores_through_same_session(bridge):
    c = bridge
    before, ledger, result = files.originals(c), c.ledger.state, c.operator.result
    objects = c.session.executor, c.session.processes, c.session.dispatch
    assert recover(c).phase == "complete"
    state = c.journal.machine.state
    assert state.phase == "complete" and state.recording_outcome == "not_attempted"
    assert state.authorization_generation is None and state.recording_deadline == 0
    assert state.artifact_sha256 is None and state.files_stage == "pristine"
    assert c.created == [h.CONTROL["starting_normal"]]
    assert c.started == ["3" * 64] and c.native_calls == ["e" * 64]
    assert c.reader.collected.artifact is c.reader.collected.files.generation is None
    assert c.ledger.state is ledger and ledger.count == 1
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert c.operator.result is result
    assert objects == (c.session.executor, c.session.processes, c.session.dispatch)
    assert c.session.processes.closed and not c.operator.closed and not c.reader.closed
    assert not any(e["event"]["kind"] == "authorize_recording" for e in c.journal.entries)
    with pytest.raises(m.UnconfirmedHostBegin):
        recover(c)
    assert len(c.created) == len(c.started) == 1


def test_host_files_are_read_only_and_do_not_invent_current_health(bridge):
    c = bridge
    before, entries = files.originals(c), tuple(c.journal.entries)
    host = m.NeverAuthorizedHost(c.reader, c.run)
    sample = host.read()
    assert sample.observation.candidate.state == "stopped"
    assert sample.observation.candidate.healthy is sample.observation.candidate.recording is None
    assert sample.observation.files.stage == "pristine"
    assert sample.observation.files.generation is None
    assert files.originals(c) == before and tuple(c.journal.entries) == entries
    assert not c.created and not c.native_calls
    host.close()
    with pytest.raises(m.UnconfirmedHostBegin):
        host.read()
    assert not c.operator.closed and not c.reader.closed


def test_lost_normal_start_return_reconciles_without_replay(bridge):
    c = bridge
    c.lost_return = True
    assert recover(c).phase == "complete"
    assert len(c.created) == len(c.started) == 1
    assert c.journal.machine.state.recording_outcome == "not_attempted"


def test_lost_inspection_never_claims_recovery(bridge):
    c = bridge
    c.lost_inspection = True
    assert recover(c, lambda _: preserved.expire(c)).phase == "review"
    assert len(c.created) == 1 and c.ledger.state.count == 1


@pytest.mark.parametrize("after_start", [False, True])
def test_changed_pristine_files_retire_observer_without_fallback(bridge, after_start):
    c = bridge
    if not after_start:
        c.tree.wav.write_bytes(b"PRIVATE unexpected recording")

    def wait(_):
        if after_start and not c.waits:
            c.tree.wav.write_bytes(b"PRIVATE after restoration dispatch")
        else:
            preserved.expire(c)
        c.waits.append(True)

    assert recover(c, wait).phase == "review"
    assert len(c.created) == int(after_start)
    assert c.reader.failed and not c.reader.closed and not c.operator.closed
    assert c.journal.machine.state.recording_outcome == "not_attempted"
    assert c.ledger.state.count == 1


@pytest.mark.parametrize("native", [(False, False), (None, None), (True, True)])
def test_unhealthy_or_recording_normal_never_completes(bridge, native):
    c = bridge
    c.native_values = native

    def wait(_):
        if c.native_calls:
            preserved.expire(c)

    assert recover(c, wait).phase == "review"
    assert c.journal.machine.state.recording_outcome == "not_attempted"


@pytest.mark.parametrize(
    "fault",
    [
        "running",
        "replacement",
        "unknown",
        "retained",
        "finalized",
        "generation",
        "artifact",
        "progress",
        "health",
        "recording",
    ],
)
def test_candidate_or_file_stage_cannot_cross_branch(bridge, monkeypatch, fault):
    c = bridge
    host = m.NeverAuthorizedHost(c.reader, c.run)
    if fault == "running":
        c.candidate_container["State"].update(Status="running", Running=True, Pid=123)
    elif fault == "replacement":
        c.candidate_container["Id"] = "e" * 64
    elif fault == "unknown":
        c.candidate = m.base.App(c.plan.candidate.pin, "unknown")
    elif fault in ("health", "recording"):
        # Deliberately corrupt the synthetic metadata result: constructing a
        # normal stopped App already rejects these invented native flags.
        object.__setattr__(c.candidate, "healthy" if fault == "health" else fault, False)
    else:
        original = c.reader.read

        def altered():
            value = original()
            if fault in ("retained", "finalized", "generation"):
                return replace(
                    value,
                    files=replace(
                        value.files,
                        **(
                            {"generation": c.run.pins.generation}
                            if fault == "generation"
                            else {"stage": fault}
                        ),
                    ),
                )
            return replace(value, **{fault: SimpleNamespace(fabricated=True)})

        monkeypatch.setattr(c.reader, "read", altered)
    with pytest.raises(m.UnconfirmedHostBegin):
        host.read()
    assert host.failed and not c.created and not c.native_calls


@pytest.mark.parametrize(
    "fault",
    [
        "plan",
        "pins",
        "journal",
        "docker",
        "read",
        "qualify",
        "unused",
        "unconfirmed",
        "thread",
        "session",
    ],
)
def test_changed_original_custody_refuses_continuation(bridge, fault):
    c = bridge
    if fault == "plan":
        c.run.plan = m.plans.load_bytes(c.plan.raw, c.plan.sha256)
    elif fault == "pins":
        c.run.pins = replace(c.run.pins)
    elif fault == "journal":
        c.run.journal = object()
    elif fault == "docker":
        c.run.qualify.docker = m.plans.ordinary.Docker()
    elif fault in ("read", "qualify"):
        setattr(c.run, fault, SimpleNamespace(docker=c.run.read.docker))
    elif fault == "unused":
        c.run.used = False
    elif fault == "unconfirmed":
        c.run.confirm_attempted = False
    elif fault == "session":
        c.session.consume_operator = lambda _: None
    failures = []

    def attempt():
        try:
            recover(c)
        except m.UnconfirmedHostBegin:
            failures.append(True)

    if fault == "thread":
        thread = Thread(target=attempt)
        thread.start()
        thread.join(timeout=3)
        assert not thread.is_alive()
    else:
        attempt()
    assert failures == [True] and not c.created and not c.native_calls


def test_old_entrypoints_refuse_never_authorized_evidence(bridge):
    c = bridge
    for call in (
        lambda: m.recover_preserved(c.reader, c.run, c.session, c.wait),
        lambda: m.recover_finalized(c.reader, c.session, c.wait),
        lambda: m.PreservedHost(c.reader, c.run),
        lambda: m.FinalizedHost(c.reader),
    ):
        with pytest.raises(m.UnconfirmedHostBegin):
            call()
        assert not c.reader.recovery_attempted and not c.created
