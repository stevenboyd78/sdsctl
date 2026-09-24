"""Original service active-read routing; native/qualification edges are synthetic.

Private policy and authorization journals/ledger are real. Actual source/runtime,
pidfds, growing audio files and probe I/O have separate lower-level/process tests.
These tests do not establish installed-host qualification or audible acceptance.
"""

from dataclasses import replace
from threading import Thread

import pytest

from . import test_supplemental_recording_service_recording as recording_tests

m, native_tests = recording_tests.m, recording_tests.native_tests
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
    launch_fixture,
    native,
    phase,
    recording,
) = (
    recording_tests.layout,
    recording_tests.tree,
    recording_tests.routing,
    recording_tests.projection,
    recording_tests.binding,
    recording_tests.directory,
    recording_tests.prepared,
    recording_tests.joined,
    recording_tests.before_handoff,
    recording_tests.transfer,
    recording_tests.service,
    recording_tests.candidate_fixture,
    recording_tests.launch_fixture,
    recording_tests.native,
    recording_tests.phase,
    recording_tests.recording,
)


@pytest.fixture
def active(recording, monkeypatch):
    s = recording
    s.continuities, s.qualifiers, s.active_hosts, s.active_samples = [], [], [], []
    s.active_fault = None
    s.flags = (True, True)
    s.checkpoint_calls = []
    original_started = m.begin.relayed.Relay.started

    def started(relay):
        result = original_started(relay)
        relay.guard = object()  # Explicit synthetic retained-process boundary.
        return result

    def fault(stage):
        if s.active_fault == stage:
            s.run.failed = True  # Real lower-level active failures can poison Launch.
            raise OSError("PRIVATE active boundary failure")

    class Continuity:
        def __init__(self, idle, guard):
            assert idle is s.run.idle and guard is s.service.recording.relay.guard
            assert s.service.recording.started
            fault("continuity")
            self.idle, self.guard, self.closed = idle, guard, False
            s.continuities.append(self)

        def close(self):
            self.closed = True

    class Qualifier:
        def __init__(self, continuity, witness, docker, **profile):
            assert continuity is s.continuities[0] and witness is s.run.witness
            assert docker is s.service.docker and profile == native_tests.resources.PROFILE
            fault("qualifier")
            self.continuity = continuity
            self.__dict__.update(profile)
            s.qualifiers.append(self)

    class Host:
        def __init__(self, start, continuity):
            assert start is s.service.recording.start_attempt and continuity is s.continuities[0]
            fault("host")
            self.start, self.continuity, self.discarded = start, continuity, False
            s.active_hosts.append(self)

        def discard(self):
            self.discarded = True

    class Sample:
        def __init__(self, host, qualifier):
            assert host is s.active_hosts[0] and qualifier is s.qualifiers[0]
            fault("sample")
            self.host, self.qualifier = host, qualifier
            self.used = self.closed = False
            s.active_samples.append(self)

        def read(self):
            assert not self.used and not self.closed
            self.used = True
            fault("read")
            now = s.service._now()
            boot = s.plan.boot
            if s.active_fault == "object":
                return object()
            if s.active_fault == "stale":
                now -= 3
            if s.active_fault == "future":
                now += 3
            if s.active_fault == "boot":
                boot = "f" * 32
            observation = replace(
                s.journal.machine.baseline,
                sampled_at=now,
                normal=m.base.App(s.plan.normal.pin, "stopped"),
                candidate=m.base.App(
                    s.plan.candidate.pin, "running", s.run.pins.generation, *s.flags
                ),
                files=m.launch.bootstrap.recording.Files(
                    s.plan.candidate.contract.sha256,
                    "finalized" if s.active_fault == "stage" else "active",
                    "a" * 64,
                    s.run.pins.generation,
                ),
            )
            if s.active_fault == "journal":
                s.append("finish")  # A no-op tick is intentionally not persisted.
            if s.active_fault == "ledger":
                s.ledger.state = replace(s.ledger.state)
            return m.launch.bootstrap.recovery.Sample(boot, now, observation)

        def close(self):
            if not self.closed:
                self.closed = True
                fault("close")

    def read_files(start):
        assert start is s.service.recording.start_attempt and s.service.recording.finish_attempted
        s.checkpoint_calls.append("read")
        fault("checkpoint_read")
        return m.begin.relayed.local.protected.Collected(
            m.launch.bootstrap.recording.Files(
                s.plan.candidate.contract.sha256,
                "finalizing",
                "a" * 64,
                s.run.pins.generation,
            )
        )

    def append_progress(directory, collector, expected, collected, *, previous_tip):
        assert directory == s.plan.root / "recording-progress"
        assert collector.stored == s.run.projected.host
        assert expected is s.service.recording.relay.expected
        assert collected.files.stage == "finalizing" and previous_tip is None
        assert not s.finish_calls
        s.checkpoint_calls.append("append")
        fault("checkpoint_append")
        return m.begin.relayed.local.checkpoints.Tip(1, "a" * 64)

    def progress(ledger, directory, collector, tip, *, now):
        assert ledger is s.ledger and directory == s.plan.root / "recording-progress"
        assert collector.stored == s.run.projected.host and not s.finish_calls
        s.checkpoint_calls.append("ledger")
        fault("checkpoint_ledger_before")
        # Explicit synthetic progress-ledger boundary (not a byte/custody proof).
        ledger.state = replace(ledger.state, tip=tip)
        fault("checkpoint_ledger_after")

    monkeypatch.setattr(m.begin.relayed.Relay, "started", started)
    monkeypatch.setattr(m.launch.idle_module, "PostBegin", Continuity)
    monkeypatch.setattr(m.launch, "RetainedQualification", Qualifier)
    monkeypatch.setattr(m.begin, "RetainedHost", Host)
    monkeypatch.setattr(m.begin, "ActiveSample", Sample)
    monkeypatch.setattr(m.begin.Start, "read_files", read_files, raising=False)
    monkeypatch.setattr(m.begin.relayed.local.checkpoints, "append_progress", append_progress)
    monkeypatch.setattr(m.launch.binding.Ledger, "progress", progress)
    return s


def run(s, action, after=None):
    def started():
        assert s.service.start_recording()
        action()

    return recording_tests.run(s, started, after)


@pytest.mark.parametrize("flags", [(True, True), (True, False), (False, True), (False, False)])
def test_explicit_active_reads_keep_original_resources_and_fresh_one_use_samples(active, flags):
    s = active
    s.flags = flags

    def action():
        original = s.session, s.plan.raw, s.ledger.state, tuple(s.journal.entries)
        result = s.service.observe_recording()
        next_result = s.service.observe_recording()
        assert result is not next_result
        assert result.observation.candidate.healthy is flags[0]
        assert result.observation.candidate.recording is flags[1]
        assert result.observation.files.stage == "active"
        assert result.observation.sampled_at <= result.now <= next_result.observation.sampled_at
        assert original == (s.session, s.plan.raw, s.ledger.state, tuple(s.journal.entries))
        assert len(s.active_samples) == 2 and all(sample.closed for sample in s.active_samples)
        assert len(s.continuities) == len(s.qualifiers) == len(s.active_hosts) == 1
        assert not s.service.recording.uncertain and not s.native_captures[0].closed
        with pytest.raises(m.UnconfirmedOperator):
            s.session.read()  # The diagnostic result is NOT cached as session evidence.
        assert s.journal.machine.state.recording_outcome == "unconfirmed"
        assert not s.finish_calls and not s.finalized_recoveries

    assert run(s, action).phase == "review"
    assert not s.service.failed
    assert s.continuities[0].closed and s.active_hosts[0].discarded
    assert len(s.begin_calls) == 1 and len(s.engine.sent) == 2


@pytest.mark.parametrize(
    "stage",
    [
        "continuity",
        "qualifier",
        "host",
        "sample",
        "read",
        "close",
        "object",
        "stale",
        "future",
        "boot",
        "stage",
        "journal",
        "ledger",
    ],
)
def test_failed_active_read_preserves_original_clock_expiry_not_an_idle_fallback(active, stage):
    s = active
    s.active_fault = stage

    def action():
        assert s.service.observe_recording() is None
        assert s.service.recording.uncertain and not s.service.failed

    assert run(s, action).phase == "review"
    assert not s.service.failed and not s.finish_calls and not s.finalized_recoveries
    assert len(s.begin_calls) == 1 and len(s.engine.sent) == 2
    assert all(sample.closed for sample in s.active_samples)
    assert all(continued.closed for continued in s.continuities)
    assert all(host.discarded for host in s.active_hosts)
    assert not s.recoveries and s.journal.machine.state.recording_outcome == "unconfirmed"


@pytest.mark.parametrize(
    "field", ["image_environment_sha256", "timezone", "hostname", "architecture", "runtime_workers"]
)
def test_active_preparation_uses_original_profile_not_new_caller_choices(
    active, monkeypatch, field
):
    s = active

    def action():
        monkeypatch.setattr(s.run.qualify, field, "changed")
        assert s.service.observe_recording() is None
        assert not s.continuities and s.service.recording.uncertain

    assert run(s, action).phase == "review"


@pytest.mark.parametrize("field", ["continuity", "active_qualifier", "active_host"])
def test_replaced_active_resources_are_not_rebound_or_used_for_cleanup(active, monkeypatch, field):
    s = active

    def action():
        assert s.service.observe_recording() is not None
        monkeypatch.setattr(s.service.recording, field, object())
        assert s.service.observe_recording() is None
        assert s.service.recording.uncertain and len(s.active_samples) == 1

    assert run(s, action).phase == "review"
    assert s.continuities[0].closed and s.active_hosts[0].discarded


@pytest.mark.parametrize("attempt", ["unstarted", "finish", "failed", "closed"])
def test_wrong_phase_cannot_create_or_repeat_active_read(active, attempt):
    s = active

    def action():
        if attempt == "finish":
            assert s.service.finish_recording()
        elif attempt == "failed":
            s.active_fault = "read"
            assert s.service.observe_recording() is None
            s.active_fault = None
        s.service.observe_recording()

    if attempt == "unstarted":
        native_tests.resources.denied(lambda: recording_tests.run(s, action))
    elif attempt == "closed":
        assert run(s, lambda: None).phase == "review"
        native_tests.resources.denied(s.service.observe_recording)
    else:
        native_tests.resources.denied(lambda: run(s, action))
    assert len(s.active_samples) == int(attempt == "failed")
    assert s.service.failed and not s.finalized_recoveries


def test_active_read_does_not_interleave_the_finalized_publication_window(active):
    s = active

    def action():
        assert s.service.observe_recording() is not None
        assert s.service.finish_recording()
        assert s.readers[0].phase == "published"
        assert len(s.active_samples) == 1 and not s.service.recording.uncertain
        assert s.checkpoint_calls == ["read", "append", "ledger"]
        assert s.ledger.state.tip == m.begin.relayed.local.checkpoints.Tip(1, "a" * 64)

    assert run(s, action).phase == "review"
    assert len(s.finish_calls) == 1 and not s.finalized_recoveries


@pytest.mark.parametrize(
    "point",
    ["checkpoint_read", "checkpoint_append", "checkpoint_ledger_before", "checkpoint_ledger_after"],
)
def test_progress_publication_failure_cannot_skip_to_completion_or_retry(active, point):
    s = active

    def action():
        assert s.service.observe_recording() is not None
        s.active_fault = point
        assert not s.service.finish_recording()
        assert s.service.recording.uncertain and s.service.recording.finish_attempted
        assert not s.finish_calls and not s.readers

    assert run(s, action).phase == "review"
    assert not s.service.failed and not s.finalized_recoveries and not s.recoveries
    assert s.checkpoint_calls.count("read") == 1
    assert s.checkpoint_calls.count("append") <= 1 and s.checkpoint_calls.count("ledger") <= 1
    assert (s.ledger.state.tip is not None) is (point == "checkpoint_ledger_after")
    assert s.journal.machine.state.recording_outcome == "unconfirmed"


@pytest.mark.parametrize("point", ["construct", "read", "close"])
def test_interruption_closes_original_handles_and_never_retries(active, monkeypatch, point):
    s = active

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("PRIVATE active interruption")

    def action():
        monkeypatch.setattr(
            m.begin.ActiveSample, "__init__" if point == "construct" else point, interrupt
        )
        s.service.observe_recording()

    with pytest.raises(KeyboardInterrupt):
        run(s, action)
    assert s.service.closed and s.service.failed and not s.finish_calls
    assert s.native_captures[0].closed and s.start_calls[0].closed
    assert s.continuities[0].closed and s.active_hosts[0].discarded


def test_other_thread_cannot_read_or_close_original_recording_resources(active):
    s = active
    errors = []

    def other():
        try:
            s.service.recording.observe()
        except m.UnconfirmedOperator as error:
            errors.append(str(error))

    def action():
        thread = Thread(target=other)
        thread.start()
        thread.join(2)
        assert not thread.is_alive() and errors == [m.MESSAGE]
        assert not s.active_samples and not s.service.recording.uncertain

    assert run(s, action).phase == "review"
