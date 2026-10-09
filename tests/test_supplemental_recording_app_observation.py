"""Real App controllers/journals/inventories; synthetic host/probe/file returns.

Tests use a real bounded worker thread and original owner-thread joins. No
installed App, live scanner, authenticated native returns or recovery claim.
"""

import importlib.util
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from threading import get_ident
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_active_qualification as phases
from ._supplemental_fixture_clock import compressed_scheduler_time

a = phases.app_begins
candidate, app, native, launch_case, execution, joined = (
    a.candidate,
    a.app,
    a.native,
    a.launch_case,
    a.execution,
    a.joined,
)
layout, image_umask, supervised, image, configured = (
    a.layout,
    a.image_umask,
    a.supervised,
    a.image,
    a.configured,
)
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)
NAME = "supplemental_recording_app_observation"
SPEC = importlib.util.spec_from_file_location(NAME, Path(a.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
begin, launch, denied = m.begin, m.launch, a.denied


@pytest.fixture
def observing(joined, monkeypatch):
    s = joined
    s.start = s.make_start()
    # The direct begin suites retain the exact two-second observation-age
    # oracle.  This deeper worker/finalization composition needs an ordered
    # synthetic phase, independent of an unrelated CI scheduler pause between
    # its real journal and inventory operations.
    with compressed_scheduler_time(
        monkeypatch,
        clock_module=begin.plans.clock,
        witness=s.startup.clock,
    ):
        s.relay = s.start.start_once()
    expected = begin.binding.protected.evidence.RecordingExpectation(
        s.plan.case,
        s.idle.generation,
        s.projected.host.contract.audio_endpoint_sha256,
        "2026-09-25T08:00:00-06:00",
    )
    s.ledger.started(expected, now=time.monotonic(), success_sha256="5" * 64)
    s.relay.phase, s.relay.expected = "completed", expected
    s.relay.plan = SimpleNamespace(stop_at=time.monotonic() + 150, finish_by=time.monotonic() + 158)
    s.continued = object.__new__(launch.idle_module.PostBegin)
    c = s.continued
    c.plan, c.idle, c.guard, c.ready = s.plan, s.idle, s.relay.guard, s.run.ready
    c.finish_by = s.plan.lease["stop_by"]
    c.closed = c.failed = False
    owner = get_ident()
    s.file_reads, s.worker_reads, s.probes = [], [], []
    s.worker_fault, s.probe_fault = None, None

    def continued_guard(current, deadline):
        assert get_ident() == owner and current is c
        m.require(not current.failed and not current.closed)
        m.require(time.monotonic() < min(deadline, current.finish_by))

    def continued_read(current):
        continued_guard(current, current.finish_by)
        exits = s.relay.guard.check()
        stamp = begin.plans.clock.read().boottime_ns / begin.plans.clock.NS
        return launch.idle_module.Continuity(s.prebegin_idle, stamp, exits)

    monkeypatch.setattr(launch.idle_module.PostBegin, "_guard", continued_guard)
    monkeypatch.setattr(launch.idle_module.PostBegin, "read", continued_read)
    s.qualify = m.active.NativeActiveQualification(s.run.qualify, s.start, c)
    s.host = m.AppRetainedHost(s.start, c)

    def result(stage):
        return begin.binding.protected.Collected(
            begin.binding.protected.Files(
                s.plan.candidate.contract.sha256,
                stage,
                "6" * 64,
                expected.generation,
            ),
            artifact=object() if stage == "finalized" else None,
        )

    s.files = result("active")

    def progress(path):
        assert get_ident() == owner and path == s.plan.root / "recording-progress"
        s.file_reads.append("progress")
        return s.files

    def finalized():
        assert get_ident() == owner
        s.file_reads.append("finalized")
        return s.files

    s.relay.read_progress, s.relay.recheck_completed = progress, finalized

    def closed():
        acknowledgment = begin.binding.protected.Acknowledgment(
            expected.case,
            expected.generation,
            s.plan.candidate.contract.sha256,
            expected.started_at,
            "7" * 64,
            "8" * 64,
        )
        s.ledger.completed(acknowledgment, now=time.monotonic())
        s.relay.phase, s.files = "closed", result("finalized")

    s.mark_closed = closed

    def observer(clock):
        def read():
            assert get_ident() != owner
            s.worker_reads.append(get_ident())
            if s.worker_fault:
                raise s.worker_fault
            boot, began = clock()
            return begin._StaticHostSnapshot(
                boot,
                began,
                clock()[1],
                begin.base.App(s.plan.normal.pin, "stopped"),
                begin.base.App(s.plan.candidate.pin, "running", s.idle.generation, None, None),
                True,
                True,
            )

        return SimpleNamespace(read=read)

    monkeypatch.setattr(s.host, "_observer", observer)
    s.native = begin.plans.ordinary.NativeState(s.idle.generation, True, True)

    class Probe:
        def __init__(self, original):
            assert original is s.relay.guard
            self.original, self.closed = original, False
            self.channel = SimpleNamespace(close=lambda: s.trace.append("passive_closed"))
            self.prepared = self.used = False
            s.probes.append(self)

        def prepare(self):
            assert not self.prepared
            self.prepared = True

        def read(self):
            assert self.prepared and not self.used
            self.used = True
            if s.probe_fault == "source":
                s.container["Config"]["Cmd"][-1] = "0" * 32
            elif s.probe_fault == "receipt":
                (s.case_root / "receipts/unknown.json").write_bytes(b"PRIVATE")
            return s.native

        def close(self):
            self.closed = True

    monkeypatch.setattr(launch.probe_exec, "Sample", Probe)
    s.samplers = []

    def sampler():
        sample = m.AppActiveSample(s.host, s.qualify)
        s.samplers.append(sample)
        return sample

    s.sampler = sampler
    try:
        yield s
    finally:
        for sample in s.samplers:
            sample.close()
        if s.host.pending is not None:
            s.host.pending.cancelled.set()
            s.host.pending.worker.join(3)
            assert not s.host.pending.worker.is_alive()


@pytest.mark.parametrize(
    "healthy,recording", [(True, True), (False, True), (True, False), (False, False)]
)
def test_complete_app_observation_joins_fresh_status_not_inferred_from_files(
    observing, healthy, recording
):
    s = observing
    s.native = begin.plans.ordinary.NativeState(s.idle.generation, healthy, recording)
    sample = s.sampler()
    original = s.plan.raw, s.plan.lease, s.ledger.state, tuple(s.journal.entries)
    result = sample.read()
    assert result.observation.files == s.files.files
    assert result.observation.candidate.healthy is healthy
    assert result.observation.candidate.recording is recording
    assert result.observation.sampled_at == sample.began
    assert len(s.worker_reads) == len(s.file_reads) == len(s.probes) == 1
    assert s.qualify.elapsed_seconds < 2
    assert original == (s.plan.raw, s.plan.lease, s.ledger.state, tuple(s.journal.entries))
    assert not s.run.failed and not s.witness.exited()


def test_read_only_app_host_leaves_health_unknown_and_accepts_finalized_files(observing):
    s = observing
    s.mark_closed()
    assert s.host.prepare() is None
    result = s.host()
    assert result.observation.files.stage == "finalized"
    assert result.observation.candidate.healthy is None
    assert result.observation.candidate.recording is None
    assert s.file_reads == ["finalized"] and not s.probes


def test_two_distinct_samples_share_original_inputs_but_not_results(observing):
    s = observing
    first = s.sampler().read()
    second = s.sampler().read()
    assert first is not second and second.now >= first.now
    assert len(s.probes) == 2 and len(s.relays) == 1
    assert s.run.qualify is s.qualify.prebegin and s.qualify.start is s.start


def test_active_app_read_continues_after_ready_expiry_not_after_recording_stop(
    observing, monkeypatch
):
    s = observing

    def expired(*args):
        pytest.fail("App observation must not reuse pre-begin readiness")

    monkeypatch.setattr(launch.received.Ready, "check_before_begin", expired)
    monkeypatch.setattr(launch.idle_module.Idle, "read", expired)
    with monkeypatch.context() as patch:
        a.continuity_tests.advance(patch, 125)
        assert time.monotonic() > s.run.ready.ready_by
        assert s.sampler().read().observation.files.stage == "active"
    with monkeypatch.context() as patch:
        a.continuity_tests.advance(patch, 155)
        denied(s.sampler)


@pytest.mark.parametrize(
    "fault",
    ["worker", "source_before", "source", "receipt", "generation", "unknown_health", "file_stage"],
)
def test_failed_app_observation_closes_original_transport_and_cannot_be_retried(observing, fault):
    s = observing
    sample = s.sampler()
    if fault == "worker":
        s.worker_fault = ValueError("PRIVATE")
    elif fault == "source_before":
        s.container["Config"]["Cmd"][-1] = "0" * 32
    elif fault in ("source", "receipt"):
        s.probe_fault = fault
    elif fault == "generation":
        s.native = replace(s.native, generation="f" * 64)
    elif fault == "unknown_health":
        s.native = replace(s.native, healthy=None)
    else:
        s.files = replace(s.files, files=replace(s.files.files, stage="retained"))
    denied(sample.read)
    assert sample.failed and s.host.failed and s.start.failed
    assert s.run.client.closed and s.run.ready.failed and not s.run.ready.closed
    assert not s.witness.exited()
    os.fstat(s.witness.fd)
    number = len(s.probes)
    denied(sample.read)
    denied(s.sampler)
    assert len(s.probes) == number
    if fault == "source_before":
        assert number == 0 and not s.worker_reads


@pytest.mark.parametrize("field", ["host", "qualify", "run", "continuity"])
def test_sample_cannot_adopt_changed_original_owners(observing, field):
    s = observing
    sample = s.sampler()
    setattr(sample, field, object())
    denied(sample.read)
    assert s.host.failed and s.run.client.closed and not s.probes


def test_direct_admissions_still_reject_app_observation_types(observing):
    s = observing
    denied(lambda: begin.RetainedHost(s.start, s.continued))
    denied(lambda: begin.ActiveSample(s.host, s.qualify))
    assert not s.start.failed and not s.host.failed and not s.run.failed
    assert s.sampler().read().observation.files.stage == "active"


def test_no_unreviewed_observation_subclass(observing):
    class Other(m.AppActiveSample):
        pass

    s = observing
    denied(lambda: Other(s.host, s.qualify))
    assert not s.host.failed and not s.run.failed
