"""Real original journals/ledger/pidfd, explicitly synthetic host/Ready/Relay.

This ordering fixture does not claim actual Engine, cached IPC, source/runtime
qualification or a native recording. Those mechanisms have separate tests.
"""

import importlib.util
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_recording_exit as exit_tests  # noqa: F401
from . import test_supplemental_recording_host_launch as launches
from . import test_supplemental_recording_normal_read as normal_reads  # noqa: F401
from . import test_supplemental_recording_relay as relay_tests  # noqa: F401

NAME = "supplemental_recording_host_begin"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(launches.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
layout, tree, routing, projection, binding, directory, prepared, setup = (
    launches.layout,
    launches.tree,
    launches.routing,
    launches.projection,
    launches.binding,
    launches.directory,
    launches.prepared,
    launches.setup,
)


@pytest.fixture
def joined(setup, monkeypatch):
    s = setup
    qualifier = launches.combined_qualifier(s, monkeypatch)
    host = launches.prepared_host_ordering(s, monkeypatch)
    host.docker = qualifier.docker = m.plans.ordinary.Docker()
    run = s.launch(qualify=qualifier, read=host)
    path = s.plan.root / "recording-ledger"
    path.mkdir(mode=0o700)
    ledger = m.binding.Ledger(path, run.pins.host, now=time.monotonic())
    assert run.start_confirmed().healthy is True
    run.ready.context_raw = m.binding.encode(
        m.launch.received._context(run.pins, run.profile_sha256)
    )
    run.ready.clock, run.ready.zero_domain = s.plan.original_clock, s.idle.zero_domain
    run.ready.ready_by = run.command.ready_by
    run.ready.watch_deadline = (
        run.command.ready_by + s.plan.candidate.contract.maximum_recording_seconds
    )
    s.trace.clear()
    s.run, s.ledger, s.relays = run, ledger, []

    class Relay:
        def __init__(self, received_ledger, ready):
            assert received_ledger is ledger and ready is run.ready
            assert s.journal.machine.state.authorization_generation == s.idle.generation
            event = s.journal.entries[-1]["event"]
            assert event["kind"] == "authorize_recording"
            assert m.binding.load(ledger.directory, ledger.binding) == ledger.state
            raw = json.loads((path / "0001.json").read_bytes())
            assert raw["event"]["authorization_sha256"] == m.base.checksum(event)
            assert ledger.state.expected is None and ledger.state.count == 2
            s.trace.append("relay")
            s.relays.append(self)
            if s.fault == "relay":
                raise OSError("PRIVATE lost begin return")

    monkeypatch.setattr(m.relayed, "Relay", Relay)
    s.start = m.Start(run, ledger)
    assert s.trace and "probe_prepare" not in s.trace and "relay" not in s.trace
    s.trace.clear()
    try:
        yield s
    finally:
        s.start.close()


def denied(callback):
    with pytest.raises(m.UnconfirmedHostBegin) as caught:
        callback()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


def test_fresh_health_then_policy_fsync_ledger_fsync_and_one_existing_relay(joined):
    s = joined
    original = s.plan.raw, s.run.command, s.run.ready.received_at, s.run.ready.watch_deadline
    initial = len(s.journal.entries)
    result = s.start.start_once()
    assert result is s.start.relay is s.relays[0]
    assert len(s.journal.entries) == initial + 1
    order = [
        "probe_prepare",
        "host_prepare",
        "qualification_before",
        "host_join",
        "probe",
        "qualification_after",
        "relay",
    ]
    assert [s.trace.index(x) for x in order] == sorted(s.trace.index(x) for x in order)
    assert original == (
        s.plan.raw,
        s.run.command,
        s.run.ready.received_at,
        s.run.ready.watch_deadline,
    )
    policy = s.journal.machine.state
    intent = s.ledger.state
    assert intent.finish_by == s.plan.original_clock.native_deadline(policy.recording_deadline)
    assert intent.finish_by <= s.run.ready.watch_deadline
    assert 3 < intent.start_by - intent.now <= 10
    assert policy.recording_outcome == "unconfirmed" and intent.expected is None
    assert s.start.probe is not s.run.probe  # Never reuse bootstrap cached health.
    assert not s.prepared.witness.exited()
    denied(s.start.start_once)
    assert len(s.relays) == 1 and s.run.ready.failed and s.run.client.closed


@pytest.mark.parametrize(
    "fault",
    [
        "probe_prepare",
        "probe",
        "probe_generation",
        "unhealthy",
        "recording",
        "qualification_before",
        "qualification_after",
    ],
)
def test_uncertain_probe_or_source_never_authorizes_or_begins(joined, fault):
    s = joined
    s.fault = fault
    before = len(s.journal.entries)
    denied(s.start.start_once)
    assert len(s.journal.entries) == before and s.ledger.state.count == 1
    assert not s.relays and s.start.failed and s.run.ready.failed
    assert not s.prepared.witness.exited()
    denied(s.start.start_once)


@pytest.mark.parametrize(
    "fault",
    [
        "jobs",
        "core",
        "owners",
        "normal_running",
        "pin",
        "generation",
        "files",
        "stale",
        "wrong_boot",
        "future",
    ],
)
def test_complete_fresh_original_observation_is_required(joined, monkeypatch, fault):
    s = joined
    original = type(s.run.read).__call__

    def changed(self):
        sample = original(self)
        obs = sample.observation
        if fault in ("jobs", "core", "owners"):
            obs = replace(
                obs,
                **{
                    {"jobs": "jobs_idle", "core": "core_running", "owners": "other_owners_stopped"}[
                        fault
                    ]: False
                },
            )
        elif fault == "normal_running":
            obs = replace(
                obs, normal=m.base.App(s.plan.normal.pin, "running", "a" * 64, True, False)
            )
        elif fault in ("pin", "generation"):
            obs = replace(obs, candidate=replace(obs.candidate, **{fault: "f" * 64}))
        elif fault == "files":
            obs = replace(obs, files=replace(obs.files, evidence_sha256="f" * 64))
        elif fault == "stale":
            obs = replace(obs, sampled_at=obs.sampled_at - 3)
        elif fault == "wrong_boot":
            return replace(sample, boot_id="f" * 32)
        else:
            return replace(sample, now=sample.now + 3)
        return replace(sample, observation=obs)

    monkeypatch.setattr(type(s.run.read), "__call__", changed)
    denied(s.start.start_once)
    assert not s.relays and s.ledger.state.count == 1
    assert s.journal.machine.state.authorization_generation is None


@pytest.mark.parametrize(
    "fault",
    [
        "finish",
        "journal_extra",
        "ledger_corrupt",
        "ledger_replace",
        "ledger_poisoned",
        "original_ready",
        "ready_deadline",
        "plan",
        "plan_nested",
        "plan_raw",
        "reader",
        "qualifier",
        "docker",
    ],
)
def test_changed_original_context_refuses_before_new_probe(joined, monkeypatch, fault):
    s = joined
    if fault == "finish":
        s.append("finish")
    elif fault == "journal_extra":
        (s.journal.path / "unreviewed").touch()
    elif fault == "ledger_corrupt":
        (s.ledger.directory / "0000.json").write_bytes(b"PRIVATE")
    elif fault == "ledger_replace":
        old = s.ledger.directory.with_name("retained-original-ledger")
        s.ledger.directory.rename(old)
        s.ledger.directory.mkdir(mode=0o700)
        for path in old.iterdir():
            target = s.ledger.directory / path.name
            target.write_bytes(path.read_bytes())
            target.chmod(0o600)
    elif fault == "ledger_poisoned":
        s.ledger._poisoned = True
    elif fault == "original_ready":
        s.run.ready.ready_raw = b"other"
    elif fault == "ready_deadline":
        s.run.ready.ready_by += 10
    elif fault == "plan":
        monkeypatch.setattr(s.run, "plan", m.plans.load_bytes(s.plan.raw, s.plan.sha256))
    elif fault == "plan_nested":
        object.__setattr__(s.plan.normal.files, "recordings", "f" * 64)
    elif fault == "plan_raw":
        object.__setattr__(s.plan, "raw", s.plan.raw + b" ")
    elif fault == "reader":
        s.run.read = lambda: None
    elif fault == "qualifier":
        s.run.qualify = lambda: None
    else:
        s.run.read.docker = m.plans.ordinary.Docker()
    denied(s.start.start_once)
    assert s.start.probe is None and not s.relays


@pytest.mark.parametrize("boundary", ["policy", "intent", "relay"])
def test_lost_write_return_never_retries_or_claims_started(joined, monkeypatch, boundary):
    s = joined
    if boundary == "relay":
        s.fault = "relay"
    else:
        obj, name = (s.journal, "append") if boundary == "policy" else (s.ledger, "start_intent")
        original = getattr(obj, name)

        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("PRIVATE lost durable return")

        monkeypatch.setattr(obj, name, lost)
    denied(s.start.start_once)
    assert s.journal.machine.state.authorization_generation == s.idle.generation
    assert m.binding.load(s.ledger.directory, s.ledger.binding).count == (
        1 if boundary == "policy" else 2
    )
    assert len(s.relays) == (1 if boundary == "relay" else 0)
    assert s.run.client.closed and not s.prepared.witness.exited()
    assert s.ledger.state.expected is None
    denied(s.start.start_once)
    assert len(s.relays) == (1 if boundary == "relay" else 0)


@pytest.mark.parametrize("boundary", ["policy", "intent"])
def test_cancellation_between_durable_writes_prevents_begin(joined, monkeypatch, boundary):
    s = joined
    obj, name = (s.journal, "append") if boundary == "policy" else (s.ledger, "start_intent")
    original = getattr(obj, name)

    def cancelled(*args, **kwargs):
        result = original(*args, **kwargs)
        if boundary == "policy":
            original(
                dict(
                    kind="finish",
                    boot_id=s.plan.boot,
                    now=s.plan.original_clock.boottime_ns / m.plans.clock.NS + 1,
                )
            )
        else:
            s.append("finish")
        return result

    monkeypatch.setattr(obj, name, cancelled)
    denied(s.start.start_once)
    assert s.journal.machine.state.finish_requested and not s.relays
    assert s.ledger.state.count == (1 if boundary == "policy" else 2)


@pytest.mark.parametrize("at", [1, 2, 3, 4])
def test_actual_fsync_return_loss_never_sends_begin(joined, monkeypatch, at):
    s = joined
    calls, original = [], os.fsync

    def lost(fd):
        original(fd)
        calls.append(fd)
        if len(calls) == at:
            raise OSError("PRIVATE fsync uncertainty")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", lost)
        denied(s.start.start_once)
    assert not s.relays and s.run.client.closed
    assert (s.ledger.directory / "0001.json").exists() is (at >= 3)


def test_foreign_thread_cannot_probe_or_begin(joined):
    errors = []
    worker = Thread(
        target=lambda: errors.append(pytest.raises(m.UnconfirmedHostBegin, joined.start.start_once))
    )
    worker.start()
    worker.join(2)
    assert not worker.is_alive() and len(errors) == 1
    assert joined.start.probe is None and not joined.relays


def test_close_releases_only_new_probe_and_never_certifies_actor_exit(joined):
    s = joined
    s.start.start_once()
    s.start.close()
    assert s.start.probe.closed and not s.run.probe.closed
    assert not s.run.ready.closed and not s.prepared.witness.exited()
    s.start.close()


def test_reconstructing_after_authorization_cannot_create_a_new_start(joined):
    s = joined
    s.start.start_once()
    denied(lambda: m.Start(s.run, s.ledger))
    assert len(s.relays) == 1 and s.ledger.state.count == 2


def test_two_preconstructed_controllers_cannot_authorize_twice(joined):
    s = joined
    second = m.Start(s.run, s.ledger)
    try:
        s.start.start_once()
        denied(second.start_once)
        assert second.probe is None and len(s.relays) == 1
    finally:
        second.close()


@pytest.mark.parametrize(
    "fault",
    [
        "ready_clock",
        "ready_context",
        "ready_limit",
        "watch_limit",
        "bootstrap_probe",
        "ledger_path",
        "ledger_binding",
        "ledger_newer_than_ready",
    ],
)
def test_constructor_rejects_reinterpreted_original_evidence(joined, fault):
    s = joined
    if fault == "ready_clock":
        s.run.ready.clock = m.plans.clock.read()
    elif fault == "ready_context":
        s.run.ready.context_raw = b"{}"
    elif fault == "ready_limit":
        s.run.ready.ready_by += 1
    elif fault == "watch_limit":
        s.run.ready.watch_deadline += 1
    elif fault == "bootstrap_probe":
        s.run.probe.execution_id = "f" * 64
    elif fault == "ledger_path":
        s.ledger.directory = s.ledger.directory.with_name("other-ledger")
    elif fault == "ledger_binding":
        s.ledger.binding = replace(s.ledger.binding, plan_sha256="f" * 64)
    else:
        # Actual newly prepared ledger, deliberately after the original Ready.
        old = s.ledger.directory.with_name("retained-first-ledger")
        s.ledger.directory.rename(old)
        s.ledger.directory.mkdir(mode=0o700)
        s.ledger = m.binding.Ledger(s.ledger.directory, s.run.pins.host, now=time.monotonic())
    denied(lambda: m.Start(s.run, s.ledger))
    assert not s.relays


@pytest.mark.parametrize("boundary", ["collection", "policy", "intent"])
def test_late_collection_or_publication_cannot_refresh_oldest_health(joined, monkeypatch, boundary):
    s = joined
    actual_clock = m.plans.clock.read

    def later():
        value = actual_clock()
        step = 3 * m.plans.clock.NS
        return replace(
            value,
            before_ns=value.before_ns + step,
            boottime_ns=value.boottime_ns + step,
            after_ns=value.after_ns + step,
        )

    if boundary == "collection":
        original = type(s.run.qualify).during

        def delayed(self, observe):
            result = original(self, observe)
            monkeypatch.setattr(m.plans.clock, "read", later)
            return result

        monkeypatch.setattr(type(s.run.qualify), "during", delayed)
    else:
        obj, name = (s.journal, "append") if boundary == "policy" else (s.ledger, "start_intent")
        original = getattr(obj, name)

        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            monkeypatch.setattr(m.plans.clock, "read", later)
            return result

        monkeypatch.setattr(obj, name, delayed)
    denied(s.start.start_once)
    assert not s.relays
    assert s.ledger.state.count == (2 if boundary == "intent" else 1)


@pytest.mark.parametrize("boundary", ["policy", "intent"])
def test_a_fabricated_return_without_actual_publication_cannot_begin(joined, monkeypatch, boundary):
    s = joined
    if boundary == "policy":
        monkeypatch.setattr(s.journal, "append", lambda *_: None)
    else:
        monkeypatch.setattr(s.ledger, "start_intent", lambda **_: s.ledger.state)
    denied(s.start.start_once)
    assert not s.relays and s.ledger.state.count == 1


def test_keyboard_interrupt_consumes_without_swallowing_interrupt(joined, monkeypatch):
    s = joined

    def interrupted(self, observe):
        raise KeyboardInterrupt

    monkeypatch.setattr(type(s.run.qualify), "during", interrupted)
    with pytest.raises(KeyboardInterrupt):
        s.start.start_once()
    assert s.start.failed and s.run.client.closed and not s.relays
    denied(s.start.start_once)
