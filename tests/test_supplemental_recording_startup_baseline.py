"""Real startup/clock/files and complete host reader, synthetic host/cache I/O.

No installed helper, source, App, scanner, recording or recovery qualification.
The preflight plan is derived from the retained clock-free declaration, not an
inherited fixture's already clock-bound plan.
"""

import json
import os
from dataclasses import replace
from threading import Thread

import pytest

from . import test_supplemental_recording_startup_service as integration
from ._supplemental_fixture_budget import integer_budget

m, launch = integration.startup, integration.m.launch
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
) = (
    integration.layout,
    integration.tree,
    integration.routing,
    integration.projection,
    integration.binding,
    integration.directory,
    integration.prepared,
    integration.joined,
    integration.before_handoff,
)


@pytest.fixture
def service_case(before_handoff, tmp_path, monkeypatch):
    s = before_handoff
    s.root, source = tmp_path / "service-case", tmp_path / "service-declaration"
    s.root.mkdir(mode=0o700)
    source.mkdir(mode=0o700)
    value = json.loads(s.plan.raw)
    times = value.pop("deadlines")
    value.pop("original_clock")
    s.template = m.declaration.codec.decode(
        dict(
            schema=1,
            kind=m.declaration.codec.KIND,
            plan=value,
            budget=integer_budget(times),
        )
    )
    path = source / m.declaration.NAME
    path.write_bytes(s.template.raw)
    path.chmod(0o600)
    monkeypatch.setattr(m.declaration, "declaration_root", lambda _: source)
    monkeypatch.setattr(m.plans.Plan, "root", property(lambda _: s.root))
    s.clocks, s.preflights, s.samples = [], [], []
    capture_clock = m.plans.clock.ClockWitness.__init__
    capture_reader = launch.PreHandoffHost.__init__
    read = launch.PreHandoffHost.read
    s.after_read = lambda sample: sample

    def clock_init(clock, original):
        capture_clock(clock, original)
        s.clocks.append(clock)

    def reader_init(reader, *args):
        capture_reader(reader, *args)
        s.preflights.append(reader)

    def observed(reader):
        sample = read(reader)
        s.samples.append(sample)
        return s.after_read(sample)

    def cached(docker, seal, command, generation):
        reader = s.preflights[-1]
        assert docker is s.docker and seal is reader.plan.normal
        assert command == reader.normal_reader.command
        assert generation == s.plan.normal_generation
        assert s.startup.clock is None and not list(s.root.iterdir())
        s.cached_calls.append((seal.slug, generation))
        s.after_cached()
        return m.plans.ordinary.NativeState(generation, *s.host.native_state)

    monkeypatch.setattr(m.plans.clock.ClockWitness, "__init__", clock_init)
    monkeypatch.setattr(launch.PreHandoffHost, "__init__", reader_init)
    monkeypatch.setattr(launch.PreHandoffHost, "read", observed)
    monkeypatch.setattr(launch.normal_read.cached, "_read_probe", cached)
    with m.declaration.Declaration(source, s.template.sha256) as original:
        s.declaration = original
        s.startup = m.Startup(original)
        try:
            yield s
        finally:
            s.startup.close()
            assert all(clock.closed for clock in s.clocks)


def denied(s, call=None):
    with pytest.raises(m.UnconfirmedStartup) as error:
        (call or (lambda: s.startup.prepare_service(s.projected, s.docker)))()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__
    assert s.startup.closed and s.startup.failed
    assert all(clock.closed for clock in s.clocks)


def test_complete_preflight_precedes_original_service_origin_and_publication(service_case):
    s = service_case
    assert not s.clocks and not s.preflights and not s.cached_calls
    original = s.startup.prepare_service(s.projected, s.docker)
    owner, plan, preflight = s.startup, original.plan, s.preflights[0]
    assert len(s.clocks) == 2 and len(s.preflights) == len(s.samples) == 1
    temporary, continuing = s.clocks
    assert temporary.closed and not continuing.closed and owner.clock is continuing
    os.fstat(continuing.fd)
    assert preflight.used and not preflight.failed
    assert m.plans._same_plan_value(preflight.plan.original_clock, temporary.original)
    assert plan.original_clock == continuing.original
    assert preflight.plan.raw != plan.raw and preflight.plan is not plan
    s.template.check_plan(preflight.plan, temporary.original)
    s.template.check_plan(plan, continuing.original)
    assert preflight.plan.deadlines.issued_at <= owner.baseline.sampled_at
    assert 0 <= plan.deadlines.issued_at - owner.baseline.sampled_at <= 2
    assert owner.baseline is s.samples[0].observation and owner.projected is s.projected
    assert len(s.cached_calls) == 1 and s.idle_reads == 0
    assert s.host.reads == ["apps", "jobs", "core", "normal", "candidate", "apps", "jobs"]
    assert {p.name for p in s.root.iterdir()} == {"startup-claim.json", "plan.json"}
    assert owner.poll() is None
    integration.startups.submit(owner)
    assert owner.poll() is original and owner.accepted_input() is original
    assert len(s.cached_calls) == 1 and len(s.clocks) == 2
    assert not (s.root / "journal").exists() and not (s.root / "inbox").exists()


@pytest.mark.parametrize("fault", ["jobs", "health", "recording", "candidate", "core"])
def test_bad_preflight_never_creates_service_clock_or_files(service_case, fault):
    s = service_case
    if fault == "jobs":
        s.host.jobs = False
    elif fault == "health":
        s.host.native_state = False, False
    elif fault == "recording":
        s.host.native_state = True, True
    elif fault == "candidate":
        s.host.values["app_" + m.plans.base.CANDIDATE] = {}
    else:
        s.host.values[m.plans.ordinary.CORE]["State"]["Pid"] += 1
    denied(s)
    assert len(s.clocks) == 1 and s.startup.clock is None and not list(s.root.iterdir())
    denied(s, s.startup.prepare)
    assert len(s.clocks) == 1  # No fallback to the action-free prepare path.


@pytest.mark.parametrize("offset", [-3, 3])
def test_stale_or_future_baseline_cannot_be_rebased_on_service_origin(service_case, offset):
    s = service_case
    s.after_read = lambda sample: replace(
        sample,
        now=sample.now + offset,
        observation=replace(sample.observation, sampled_at=sample.observation.sampled_at + offset),
    )
    denied(s)
    assert len(s.cached_calls) == 1 and len(s.clocks) == 1
    assert not list(s.root.iterdir())


@pytest.mark.parametrize("fault", ["declaration", "reentrant", "reader_failed", "boot"])
def test_read_boundary_drift_fails_without_service_publication(service_case, fault):
    s = service_case

    def changed(sample):
        if fault == "declaration":
            (s.declaration.root / m.declaration.NAME).write_bytes(b"PRIVATE changed")
        elif fault == "reentrant":
            # The outer read still owns its temporary clock until it unwinds.
            with pytest.raises(m.UnconfirmedStartup):
                s.startup.prepare()
        elif fault == "reader_failed":
            s.preflights[0].failed = True
        else:
            return replace(sample, boot_id="f" * 32)
        return sample

    s.after_read = changed
    denied(s)
    assert len(s.cached_calls) == 1 and not list(s.root.iterdir())
    assert s.startup.clock is None


@pytest.mark.parametrize("problem", [OSError, KeyboardInterrupt, SystemExit])
def test_failed_read_closes_temporary_clock_without_replacement(service_case, problem):
    s = service_case

    def failed():
        raise problem("PRIVATE")

    s.after_cached = failed
    if issubclass(problem, Exception):
        denied(s)
    else:
        with pytest.raises(problem):
            s.startup.prepare_service(s.projected, s.docker)
    assert s.startup.closed and s.startup.failed
    assert len(s.clocks) == 1 and s.clocks[0].closed
    assert not list(s.root.iterdir())


@pytest.mark.parametrize("part", ["baseline", "projection", "sampled_at"])
def test_accepted_input_cannot_replace_or_mutate_original_preparation(service_case, part):
    s = service_case
    owner = s.startup
    owner.prepare_service(s.projected, s.docker)
    integration.startups.submit(owner)
    assert owner.poll() is owner.original
    preserved = {p.name: p.read_bytes() for p in s.root.iterdir()}
    if part == "baseline":
        owner.baseline = replace(owner.baseline)
    elif part == "projection":
        owner.projected = replace(owner.projected)
    else:
        object.__setattr__(owner.baseline, "sampled_at", owner.baseline.sampled_at - 0.01)
    denied(s, owner.accepted_input)
    assert preserved == {p.name: p.read_bytes() for p in s.root.iterdir()}
    assert len(s.cached_calls) == 1


def test_foreign_thread_cannot_observe_or_capture_service_origin(service_case):
    s = service_case
    failures = []

    def wrong_owner():
        try:
            s.startup.prepare_service(s.projected, s.docker)
        except m.UnconfirmedStartup:
            failures.append(True)

    thread = Thread(target=wrong_owner)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive() and failures == [True]
    assert s.startup.failed and not s.clocks and not s.cached_calls
    denied(s)
    assert not list(s.root.iterdir())


@pytest.mark.parametrize("part", ["projection", "docker"])
def test_invalid_service_inputs_do_not_invoke_host_read(service_case, part):
    s = service_case
    projected, docker = s.projected, s.docker
    if part == "projection":
        projected = None
    else:
        docker = object()
    denied(s, lambda: s.startup.prepare_service(projected, docker))
    assert not s.host.reads and not s.cached_calls and not list(s.root.iterdir())
    assert s.startup.clock is None


def test_second_clock_failure_does_not_adopt_temporary_clock(service_case, monkeypatch):
    s = service_case
    original_init = m.plans.clock.ClockWitness.__init__

    def fail_second(clock, original):
        if s.clocks:
            raise OSError("PRIVATE second clock failure")
        original_init(clock, original)

    monkeypatch.setattr(m.plans.clock.ClockWitness, "__init__", fail_second)
    denied(s)
    assert len(s.clocks) == 1 and s.startup.clock is None
    assert len(s.cached_calls) == 1 and not list(s.root.iterdir())


@pytest.mark.parametrize("problem", [None, KeyboardInterrupt, SystemExit])
def test_uncertain_temporary_close_preserves_interruption_and_attempts_each_close_once(
    service_case, monkeypatch, problem
):
    s = service_case
    closed = []
    close = m.plans.clock.ClockWitness.close

    def uncertain(clock):
        closed.append(clock)
        close(clock)
        if clock is s.clocks[0]:
            raise OSError("PRIVATE lost close acknowledgement")

    def interrupted():
        raise problem("PRIVATE read interrupted")

    monkeypatch.setattr(m.plans.clock.ClockWitness, "close", uncertain)
    if problem is None:
        denied(s)
        assert len(s.clocks) == 2
    else:
        s.after_cached = interrupted
        with pytest.raises(problem):
            s.startup.prepare_service(s.projected, s.docker)
        assert s.startup.failed and s.startup.closed and len(s.clocks) == 1
    assert closed == s.clocks and all(clock.closed for clock in closed)
    s.startup.close()
    assert closed == s.clocks and not list(s.root.iterdir())


def test_original_publication_refuses_existing_case_and_preserves_bytes(service_case, monkeypatch):
    s = service_case
    path = s.root / "prior-case-evidence"
    path.write_bytes(b"PRIVATE preserve")
    # This reader is still read-only. An existing case is never repaired and
    # the original publication refuses; this test does not authorize a retry.
    s.after_cached = lambda: None
    # The standard fixture asserts that the case is empty at the read boundary.
    # Override only its synthetic cached reply for this preservation scenario.
    monkeypatch.setattr(
        launch.normal_read.cached,
        "_read_probe",
        lambda *_: m.plans.ordinary.NativeState(s.plan.normal_generation, True, False),
    )
    denied(s)
    assert {p.name: p.read_bytes() for p in s.root.iterdir()} == {path.name: b"PRIVATE preserve"}
