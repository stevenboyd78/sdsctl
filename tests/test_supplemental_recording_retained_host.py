"""Real original journals/pidfd; synthetic continuity, files and worker metadata.

Separate tests below exercise the complete actual ordinary HostObserver with
explicitly synthetic Supervisor/Engine routes. No installed-platform claim.
"""

from dataclasses import replace
from threading import Event, Thread, get_ident
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_bootstrap_host as bootstrap
from . import test_supplemental_recording_host_phase_files as phase

m, begins, continuity = phase.m, phase.begins, phase.continuity
layout, tree, routing, projection, binding, directory, prepared, setup, joined, begun, observing = (
    phase.layout,
    phase.tree,
    phase.routing,
    phase.projection,
    phase.binding,
    phase.directory,
    phase.prepared,
    phase.setup,
    phase.joined,
    phase.begun,
    phase.observing,
)
static_joined = bootstrap.joined


@pytest.fixture
def host(observing, monkeypatch):
    s = observing
    owner = get_ident()
    s.thread_calls = []
    original = m.launch.idle_module.Evidence(
        s.plan.sha256,
        s.run.pins.generation,
        s.run.idle.init,
        "a" * 64,
        "b" * 64,
        s.plan.lease_sha256,
        "c" * 64,
        m.time.monotonic(),
        m.plans.clock.read().boottime_ns / m.plans.clock.NS,
    )

    class PostBegin:
        def __init__(self):
            self.idle, self.plan = s.run.idle, s.plan
            self.guard, self.ready = s.start.relay.guard, s.run.ready
            self.finish_by = s.plan.lease["stop_by"]

        def read(self):
            assert get_ident() == owner
            s.thread_calls.append(("continuity", get_ident()))
            return m.launch.idle_module.Continuity(
                original, m.plans.clock.read().boottime_ns / m.plans.clock.NS, frozenset()
            )

    monkeypatch.setattr(m.launch.idle_module, "PostBegin", PostBegin)
    s.continued = PostBegin()
    s.reader = m.RetainedHost(s.start, s.continued)
    s.worker_fault = None

    def observer(clock):
        def read():
            assert get_ident() != owner
            s.thread_calls.append(("metadata", get_ident()))
            boot, began_at = clock()
            if s.worker_fault is not None:
                raise s.worker_fault
            return m._StaticHostSnapshot(
                boot,
                began_at,
                clock()[1],
                m.base.App(s.plan.normal.pin, "stopped"),
                m.base.App(s.plan.candidate.pin, "running", s.run.pins.generation, None, None),
                True,
                True,
            )

        return SimpleNamespace(read=read)

    monkeypatch.setattr(s.reader, "_observer", observer)
    files = s.start.read_files

    def read_files():
        assert get_ident() == owner
        s.thread_calls.append(("files", get_ident()))
        return files()

    monkeypatch.setattr(s.start, "read_files", read_files)
    yield s
    if s.reader.pending is not None:
        s.reader.pending.cancelled.set()
        s.reader.pending.worker.join(3)
        assert not s.reader.pending.worker.is_alive()


@pytest.mark.parametrize("stage", ["active", "finalizing", "finalized"])
def test_retained_host_joins_files_on_owner_thread_without_fake_health(host, stage):
    s = host
    if stage == "finalized":
        s.mark_closed()
    else:
        s.result = replace(s.result, files=replace(s.result.files, stage=stage))
    before = s.start.ledger.state, tuple(s.journal.entries), s.plan.raw
    for _ in range(2):
        assert s.reader.prepare() is None
        pending = s.reader.pending
        sample = s.reader()
        assert s.reader.pending is None and not pending.worker.is_alive()
        assert type(sample) is m.launch.bootstrap.recovery.Sample
        assert sample.observation.files == s.result.files
        assert sample.observation.sampled_at == pending.began
        assert sample.observation.candidate.healthy is None
        assert sample.observation.candidate.recording is None
        assert sample.observation.normal.state == "stopped"
        assert sample.observation.jobs_idle and sample.observation.other_owners_stopped
        assert before == (s.start.ledger.state, tuple(s.journal.entries), s.plan.raw)
    assert [name for name, _ in s.thread_calls].count("metadata") == 2
    assert [name for name, _ in s.thread_calls].count("files") == 2
    assert len(s.relays) == 1 and not s.prepared.witness.exited()


def test_retained_completed_read_does_not_reuse_expired_startup_readiness(host, monkeypatch):
    host.mark_closed()
    continuity.advance(monkeypatch, 125)
    assert m.time.monotonic() > host.run.command.ready_by
    host.reader.prepare()
    assert host.reader().observation.files.stage == "finalized"


@pytest.mark.parametrize("fault", ["missing_prepare", "second_prepare", "changed_phase", "discard"])
def test_wrong_or_consumed_preparation_has_no_reusable_sample(host, fault):
    s = host
    if fault != "missing_prepare":
        s.reader.prepare()
    if fault == "changed_phase":
        s.mark_closed()
    elif fault == "discard":
        s.reader.discard()
    callback = s.reader.prepare if fault == "second_prepare" else s.reader
    begins.denied(callback)
    assert not s.file_calls and s.reader.failed and not s.prepared.witness.exited()


@pytest.mark.parametrize("error", [OSError("PRIVATE"), KeyboardInterrupt()])
def test_worker_failure_is_sanitized_without_closing_original_handles(host, error):
    s = host
    s.worker_fault = error
    s.reader.prepare()
    if isinstance(error, Exception):
        begins.denied(s.reader)
    else:
        with pytest.raises(KeyboardInterrupt):
            s.reader()
    assert s.reader.failed and s.run.client.closed and not s.prepared.witness.exited()
    assert not s.file_calls


@pytest.mark.parametrize("where", ["before_prepare", "worker", "after_worker"])
def test_clock_refusal_consumes_sample_without_retry_or_releasing_handles(host, monkeypatch, where):
    s = host
    before = s.start.ledger.state, tuple(s.journal.entries), s.plan.raw
    original = m.plans.clock.read
    rejected = []

    def unconfirmed():
        rejected.append(True)
        raise m.plans.clock.UnconfirmedClock(m.plans.clock.MESSAGE)

    if where == "before_prepare":
        monkeypatch.setattr(m.plans.clock, "read", unconfirmed)
        action = s.reader.prepare
    elif where == "worker":

        def worker_clock():
            if get_ident() != s.reader.owner[1]:
                return unconfirmed()
            return original()

        monkeypatch.setattr(m.plans.clock, "read", worker_clock)
        s.reader.prepare()
        action = s.reader
    else:
        s.reader.prepare()
        assert s.reader.pending.done.wait(1)
        monkeypatch.setattr(m.plans.clock, "read", unconfirmed)
        action = s.reader
    begins.denied(action)
    assert rejected and s.reader.failed and s.start.failed
    assert s.run.client.closed and not s.prepared.witness.exited()
    assert before == (s.start.ledger.state, tuple(s.journal.entries), s.plan.raw)
    assert not s.file_calls and len(s.relays) == 1
    begins.denied(s.reader.prepare)
    begins.denied(s.reader)


def test_changed_continuity_during_join_cannot_publish_files(host, monkeypatch):
    s = host
    original = s.continued.read
    s.reader.prepare()

    def changed():
        return replace(original(), exited=frozenset({"daemon"}))

    monkeypatch.setattr(s.continued, "read", changed)
    begins.denied(s.reader)
    assert s.reader.failed and s.start.failed and not s.prepared.witness.exited()


@pytest.mark.parametrize("field", ["normal", "generation", "health", "recording", "boot"])
def test_wrong_metadata_cannot_supply_a_host_file_sample(host, monkeypatch, field):
    s = host
    original = s.reader._observer

    def changed(clock):
        def read():
            result = original(clock).read()
            if field == "normal":
                return replace(result, normal=m.base.App(s.plan.normal.pin, "running", "a" * 64))
            if field == "boot":
                return replace(result, boot="a" * 32)
            changes = {
                "generation": {"generation": "a" * 64},
                "health": {"healthy": True},
                "recording": {"recording": False},
            }[field]
            return replace(result, candidate=replace(result.candidate, **changes))

        return SimpleNamespace(read=read)

    monkeypatch.setattr(s.reader, "_observer", changed)
    s.reader.prepare()
    begins.denied(s.reader)
    assert s.reader.failed and not s.prepared.witness.exited()


@pytest.mark.parametrize("when", ["initial_history", "files", "last_continuity"])
def test_entire_join_uses_the_earliest_two_second_budget(host, monkeypatch, when):
    s = host
    target, name = {
        "initial_history": (s.start, "retained_history"),
        "files": (s.start, "read_files"),
        "last_continuity": (s.continued, "read"),
    }[when]
    original = getattr(target, name)
    calls = []

    def late(*args):
        result = original(*args)
        calls.append(True)
        if when != "last_continuity" or len(calls) == 2:
            continuity.advance(monkeypatch, 3)
        return result

    monkeypatch.setattr(target, name, late)
    if when == "initial_history":
        # The worker may record the elapsed failure; prepare itself need not
        # return evidence, and finishing must still consume it as unconfirmed.
        s.reader.prepare()
        begins.denied(s.reader)
    else:
        s.reader.prepare()
        begins.denied(s.reader)
    assert s.reader.failed and not s.prepared.witness.exited()


def test_blocked_worker_is_retained_and_cannot_publish_after_discard(host, monkeypatch):
    s = host
    entered, release = Event(), Event()
    original = s.reader._observer

    def blocked(clock):
        def read():
            entered.set()
            assert release.wait(2)
            return original(clock).read()

        return SimpleNamespace(read=read)

    monkeypatch.setattr(s.reader, "_observer", blocked)
    s.reader.prepare()
    pending = s.reader.pending
    try:
        assert entered.wait(1)
        s.reader.discard()
        begins.denied(s.reader)
        assert s.reader.pending is pending and pending.worker.is_alive()
    finally:
        release.set()
        pending.worker.join(2)
    assert not pending.worker.is_alive() and not s.file_calls
    assert not s.prepared.witness.exited()


def test_join_waits_for_clock_sampling_before_cpu_heavy_owner_history(host, monkeypatch):
    s = host
    entered, release = Event(), Event()
    original_observer = s.reader._observer

    def blocked(clock):
        def read():
            entered.set()
            assert release.wait(2)
            return original_observer(clock).read()

        return SimpleNamespace(read=read)

    monkeypatch.setattr(s.reader, "_observer", blocked)
    s.reader.prepare()
    pending = s.reader.pending
    guard, finish = s.reader._guard, pending.finish
    ordering = []

    def checked_guard():
        # Journal decoding on the owner must not contend for the GIL with the
        # worker's strict real-clock sampler. Do not widen that sampler's limit.
        assert not pending.worker.is_alive()
        ordering.append("history")
        return guard()

    def joined():
        ordering.append("join")
        release.set()
        return finish()

    monkeypatch.setattr(s.reader, "_guard", checked_guard)
    monkeypatch.setattr(pending, "finish", joined)
    try:
        assert entered.wait(1)
        sample = s.reader()
        assert sample.observation.files.stage == "active"
        assert ordering == ["join", "history", "history"]
    finally:
        release.set()
        pending.worker.join(2)
        assert not pending.worker.is_alive()


def test_wrong_thread_cannot_prepare_a_new_host_read(host):
    errors = []

    def other():
        try:
            host.reader.prepare()
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=other)
    worker.start()
    worker.join(2)
    assert not worker.is_alive() and len(errors) == 1
    assert isinstance(errors[0], m.UnconfirmedHostBegin)
    assert host.reader.pending is None and not host.thread_calls
    assert not host.prepared.witness.exited()


def test_docker_route_cannot_change_after_original_begin(host):
    host.reader.docker.path = "/PRIVATE/other.sock"
    begins.denied(host.reader.prepare)
    assert host.reader.pending is None and not host.thread_calls
    assert not host.prepared.witness.exited()


def static_observer(s):
    existing = s.obj._observer(s.obj._clock)

    def files(slug, container):
        selected = next(item for item in s.plan.layouts if item.slug == slug)
        if slug == m.base.NORMAL:
            return m.plans.host.static.collect(selected, container)
        return m.plans.host.candidate_static.collect(selected, container)

    return m._StaticHostObserver(
        existing.docker,
        existing.supervisor,
        seals=(s.plan.normal, s.plan.candidate),
        installed_versions=existing.versions,
        other_scanner_apps=existing.other,
        core_image=existing.core_image,
        core_generation=existing.core_generation,
        core_version=existing.core_version,
        read_clock=existing.read_clock,
        collect_files=files,
        read_native=m.launch.BootstrapHost._unknown_native,
        network=m.plans.ordinary.AUDIO_NETWORK,
    )


def test_complete_static_snapshot_has_no_fake_recording_stage(static_joined):
    s = static_joined
    s.host.native_state = (True, True)
    snapshot = static_observer(s).read()
    assert type(snapshot) is m._StaticHostSnapshot
    assert not hasattr(snapshot, "files") and not hasattr(snapshot, "observation")
    assert snapshot.candidate.healthy is None and snapshot.candidate.recording is None
    assert s.host.reads == ["apps", "jobs", "core", "normal", "candidate", "apps", "jobs"]
    assert s.captures == [m.base.NORMAL, m.base.CANDIDATE]


@pytest.mark.parametrize("change", ["normal_recordings", "candidate_static", "options", "jobs"])
def test_complete_static_observer_preserves_changed_evidence_for_policy(static_joined, change):
    s = static_joined
    if change == "normal_recordings":
        s.host.files[m.base.NORMAL] = replace(s.host.files[m.base.NORMAL], recordings="f" * 64)
    elif change == "candidate_static":
        s.static = replace(s.static, package="f" * 64)
    elif change == "options":
        s.host.private_options["candidate"] = "changed"
    else:
        s.host.jobs = False
    snapshot = static_observer(s).read()
    if change == "jobs":
        assert not snapshot.jobs_idle
    elif change == "normal_recordings":
        assert snapshot.normal.pin != s.plan.normal.pin
    else:
        assert snapshot.candidate.pin != s.plan.candidate.pin
    assert snapshot.candidate.healthy is None and snapshot.candidate.recording is None


@pytest.mark.parametrize("fault", ["core_generation", "version", "network"])
def test_static_worker_still_requires_full_original_platform_checks(static_joined, fault):
    s = static_joined
    if fault == "core_generation":
        s.host.values[bootstrap.audio.h.CORE]["State"]["Pid"] += 1
    elif fault == "version":
        s.host.versions = dict(s.host.versions) | {m.base.CANDIDATE: "changed"}
    else:
        s.host.values["app_" + m.base.CANDIDATE]["HostConfig"]["NetworkMode"] = "host"
    with pytest.raises(ValueError):
        static_observer(s).read()


def test_active_phase_cannot_cross_stop_during_final_continuity(host, monkeypatch):
    s = host
    s.start.relay.plan.stop_at = m.time.monotonic() + 0.5
    original = s.continued.read
    calls = []

    def crossing():
        calls.append(True)
        if len(calls) == 2:
            continuity.advance(monkeypatch, 1)
        return original()

    monkeypatch.setattr(s.continued, "read", crossing)
    s.reader.prepare()
    begins.denied(s.reader)
    assert len(s.file_calls) == 1 and not s.prepared.witness.exited()
