"""Actual journals/pidfd, synthetic probe/inputs.

This tests composition/ordering only. Separate runtime fixtures qualify actual
Engine/native/inputs. No installed-host or independent recovery claim.
"""

from dataclasses import replace
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_retained_host as retained

m, begins, continuity = retained.m, retained.begins, retained.continuity
layout, tree, routing, projection, binding, directory, prepared, setup, joined, begun, observing = (
    retained.layout,
    retained.tree,
    retained.routing,
    retained.projection,
    retained.binding,
    retained.directory,
    retained.prepared,
    retained.setup,
    retained.joined,
    retained.begun,
    retained.observing,
)
host = retained.host


@pytest.fixture
def active(host, monkeypatch):
    s = host
    s.events, s.probes = [], []
    s.native = m.plans.ordinary.NativeState(s.run.pins.generation, True, True)
    s.active_fault = None

    def mark(event):
        s.events.append(event)
        if s.active_fault == event:
            raise OSError("PRIVATE uncertain read")

    class Qualifier:
        def __init__(self):
            self.continuity, self.plan, self.idle = s.continued, s.plan, s.run.idle
            self.witness, self.docker, self.failed = s.run.witness, s.reader.docker, False

        def __call__(self):
            mark("input_prequalification")

        def during(self, callback):
            mark("inputs_before")
            result = callback()
            mark("inputs_after")
            return result

    class Probe:
        def __init__(self, original):
            assert original is s.start.relay.guard
            self.original, self.closed = original, False
            self.used = self.prepared = False
            self.channel = SimpleNamespace(close=lambda: mark("probe_transport_closed"))
            s.probes.append(self)
            mark("probe_created")

        def prepare(self):
            assert not self.prepared
            self.prepared = True
            mark("probe_prepared")

        def read(self):
            assert self.prepared and not self.used
            self.used = True
            mark("probe_read")
            return s.native

        def close(self):
            self.closed = True
            mark("probe_closed")

    monkeypatch.setattr(m.launch, "RetainedQualification", Qualifier)
    monkeypatch.setattr(m.launch.probe_exec, "Sample", Probe)
    original = m.RetainedHost.__call__

    def host_read(self):
        mark("host_join")
        return original(self)

    monkeypatch.setattr(m.RetainedHost, "__call__", host_read)
    s.qualify = Qualifier()
    s.active = m.ActiveSample(s.reader, s.qualify)
    yield s
    s.active.close()


@pytest.mark.parametrize(
    "healthy,recording", [(True, True), (False, True), (True, False), (False, False)]
)
def test_fresh_native_flags_not_inferred_from_active_files(active, healthy, recording):
    s = active
    s.native = m.plans.ordinary.NativeState(s.run.pins.generation, healthy, recording)
    original = s.ledger.state, tuple(s.journal.entries), s.plan.raw, s.start.relay.plan
    result = s.active.read()
    assert type(result) is m.launch.bootstrap.recovery.Sample
    assert result.observation.candidate.healthy is healthy
    assert result.observation.candidate.recording is recording
    assert result.observation.files == s.result.files
    assert result.observation.sampled_at == s.active.began
    assert original == (s.ledger.state, tuple(s.journal.entries), s.plan.raw, s.start.relay.plan)
    assert s.events == [
        "input_prequalification",
        "probe_created",
        "probe_prepared",
        "inputs_before",
        "host_join",
        "probe_read",
        "inputs_after",
    ]
    assert len(s.relays) == len(s.probes) == 1 and not s.prepared.witness.exited()
    assert not s.probes[0].closed


@pytest.mark.parametrize(
    "point",
    [
        "input_prequalification",
        "probe_created",
        "probe_prepared",
        "inputs_before",
        "host_join",
        "probe_read",
        "inputs_after",
    ],
)
def test_uncertain_component_consumes_sample_without_retry_or_losing_original_handles(
    active, point
):
    s = active
    before = s.ledger.state, tuple(s.journal.entries)
    s.active_fault = point
    begins.denied(s.active.read)
    assert s.active.failed and s.reader.failed
    assert before == (s.ledger.state, tuple(s.journal.entries))
    assert not s.prepared.witness.exited() and len(s.relays) == 1
    count = len(s.probes)
    begins.denied(s.active.read)
    assert len(s.probes) == count


@pytest.mark.parametrize("fault", ["generation", "healthy_unknown", "recording_unknown", "type"])
def test_probe_must_return_current_generation_and_known_booleans(active, fault):
    if fault == "generation":
        active.native = replace(active.native, generation="f" * 64)
    elif fault == "type":
        active.native = SimpleNamespace(
            generation=active.run.pins.generation, healthy=True, recording=True
        )
    else:
        active.native = replace(active.native, **{fault.removesuffix("_unknown"): None})
    begins.denied(active.active.read)


@pytest.mark.parametrize("fault", ["continuity", "plan", "idle", "witness", "docker", "failed"])
def test_qualification_must_remain_exact_original_binding(active, fault):
    setattr(active.qualify, fault, True if fault == "failed" else object())
    begins.denied(active.active.read)
    assert not active.probes


@pytest.mark.parametrize("fault", ["finalized", "closed", "foreign_thread", "repeat"])
def test_inactive_or_consumed_instance_cannot_create_another_probe(active, fault):
    s = active
    if fault == "finalized":
        s.mark_closed()
    elif fault == "closed":
        s.active.close()
    elif fault == "repeat":
        s.active.read()
    if fault == "foreign_thread":
        errors = []

        def run():
            try:
                s.active.read()
            except m.UnconfirmedHostBegin as error:
                errors.append(error)

        thread = Thread(target=run)
        thread.start()
        thread.join(2)
        assert not thread.is_alive() and len(errors) == 1
    else:
        begins.denied(s.active.read)
    assert len(s.probes) == (fault == "repeat")


def test_late_after_source_recheck_cannot_refresh_the_observation(active, monkeypatch):
    original = type(active.qualify).during

    def late(self, callback):
        result = original(self, callback)
        continuity.advance(monkeypatch, 3)
        return result

    monkeypatch.setattr(type(active.qualify), "during", late)
    begins.denied(active.active.read)
    assert not active.prepared.witness.exited()


def test_close_only_releases_new_probe_not_original_actor_custody(active):
    active.active.read()
    active.active.close()
    assert active.probes[0].closed and not active.prepared.witness.exited()
    assert not active.run.closed and not active.start.closed


@pytest.mark.parametrize("fault", ["known_health", "wrong_boot", "future", "old", "stage"])
def test_host_result_cannot_supply_or_refresh_cached_native_evidence(active, monkeypatch, fault):
    original = m.RetainedHost.__call__

    def changed(self):
        result = original(self)
        observation = result.observation
        if fault == "known_health":
            observation = replace(
                observation, candidate=replace(observation.candidate, healthy=True, recording=True)
            )
        elif fault == "wrong_boot":
            return replace(result, boot_id="f" * 32)
        elif fault == "future":
            return replace(
                result,
                now=result.now + 1,
                observation=replace(observation, sampled_at=result.now + 1),
            )
        elif fault == "old":
            observation = replace(observation, sampled_at=active.active.began - 0.1)
        else:
            observation = replace(observation, files=replace(observation.files, stage="finalizing"))
        return replace(result, observation=observation)

    monkeypatch.setattr(m.RetainedHost, "__call__", changed)
    begins.denied(active.active.read)
    assert "probe_read" not in active.events


@pytest.mark.parametrize("fault", ["stop", "finish", "ledger", "cancel"])
def test_native_read_does_not_mask_late_or_changed_original_context(active, monkeypatch, fault):
    probe_type = m.launch.probe_exec.Sample
    original = probe_type.read

    def changed(self):
        result = original(self)
        if fault == "stop":
            remaining = active.start.relay.plan.stop_at - m.time.monotonic()
            continuity.advance(monkeypatch, remaining + 0.1)
        elif fault == "finish":
            now = m.plans.clock.read().boottime_ns / m.plans.clock.NS
            active.journal.append(dict(kind="finish", now=now, boot_id=active.plan.boot))
        elif fault == "ledger":
            active.ledger.state = replace(active.ledger.state, sha256="f" * 64)
        else:
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(probe_type, "read", changed)
    if fault == "cancel":
        with pytest.raises(KeyboardInterrupt):
            active.active.read()
    else:
        begins.denied(active.active.read)
    assert active.active.failed and not active.prepared.witness.exited()
    assert not active.probes[0].closed  # Retain the failed probe until explicit close.


def test_complete_host_fault_flags_are_returned_truthfully_for_policy(active, monkeypatch):
    original = m.RetainedHost.__call__

    def changed(self):
        result = original(self)
        observation = replace(result.observation, jobs_idle=False, other_owners_stopped=False)
        return replace(result, observation=observation)

    monkeypatch.setattr(m.RetainedHost, "__call__", changed)
    result = active.active.read()
    assert result.observation.jobs_idle is False
    assert result.observation.other_owners_stopped is False
    assert result.observation.candidate.healthy is True
    assert active.journal.machine.state.recording_outcome == "unconfirmed"


def test_active_guard_uses_existing_fresh_history_bracket_without_third_traversal(active):
    active.trace.clear()
    active.active._guard()
    assert active.trace == ["retained", "retained"]


@pytest.mark.parametrize("check_number", [1, 2])
def test_active_guard_requires_live_actors_on_both_history_boundaries(
    active, monkeypatch, check_number
):
    guard = active.start.relay.guard
    original = guard.check
    calls = []

    def changed():
        result = original()
        calls.append(True)
        return frozenset({"native"}) if len(calls) == check_number else result

    monkeypatch.setattr(guard, "check", changed)
    begins.denied(active.active.read)
    assert len(calls) == check_number
    assert not active.probes and not active.prepared.witness.exited()
