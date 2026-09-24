"""Real full HostObserver/pristine files/pidfd; explicit synthetic HA and idle.

These fixtures qualify the join, not installed Supervisor/Engine/mounts or the
combined Ready timing. No App/scanner/network operation is performed here.
"""

import time
from dataclasses import asdict, replace
from threading import Event, Thread, get_ident
from types import SimpleNamespace

import pytest

from . import test_supplemental_handoff_audio_network as audio
from . import test_supplemental_recording_host_launch as launch

m, b = launch.m, launch.b
layout, tree, routing, projection, binding, directory, prepared = (
    launch.layout,
    launch.tree,
    launch.routing,
    launch.projection,
    launch.binding,
    launch.directory,
    launch.prepared,
)


@pytest.fixture
def joined(prepared, tree, monkeypatch):
    projected = prepared.pins.host.projection
    capture = m.plans.host.recording.evidence.capture_baseline

    def local_capture(root, case):
        # Explicit host-path alias only; every byte/identity is actually read
        # from the same original temporary recording root, never /mnt/data.
        assert root == projected.host.baseline.root
        return replace(capture(tree.root, case), root=root)

    monkeypatch.setattr(m.plans.host.recording.evidence, "capture_baseline", local_capture)
    host, old, _ = audio.audio_host(monkeypatch, running=True)
    docker = m.plans.ordinary.Docker()
    for name in ("container", "containers", "image"):
        monkeypatch.setattr(docker, name, getattr(host, name))
    candidate = host.values["app_" + b.CANDIDATE]
    candidate["Id"] = prepared.witness.identity.container_id
    candidate["State"]["Pid"] = prepared.witness.identity.pid
    generation = audio.h.generation(candidate, name="app_" + b.CANDIDATE, image=audio.ot.IMAGE)
    _, value = launch.plans.projected_plan(projected)
    original = m.plans.clock.read()
    issued = original.boottime_ns / m.plans.clock.NS
    static = replace(
        m.plans.host.static.StaticFiles(*("a" * 64,) * 3),
        package=projected.layout.image_package_sha256,
    )
    seal = m.plans.host.CandidateSeal(
        host.versions[b.CANDIDATE],
        audio.ot.IMAGE,
        old.seals[b.CANDIDATE].settings,
        static,
        projected.host.contract,
    )
    value.update(
        normal=asdict(old.seals[b.NORMAL]),
        candidate=asdict(seal),
        candidate_runtime=value["candidate_runtime"] | {"image": seal.image},
        installed_versions=host.versions,
        other_scanner_apps=sorted(old.other),
        core_image=old.core_image,
        core_generation=old.core_generation,
        core_version=old.core_version,
        cli_image=audio.ot.IMAGE,
        cli_generation=audio.h.generation(
            host.values[audio.h.CLI], name=audio.h.CLI, image=audio.ot.IMAGE
        ),
        boot=original.boot,
        original_clock=asdict(original) | {"namespace": list(original.namespace)},
        deadlines=dict(
            issued_at=issued, ready_by=issued + 120, stop_by=issued + 400, recover_by=issued + 1500
        ),
    )
    plan = m.plans.decode(value)
    idle = object.__new__(m.idle_module.Idle)
    idle.plan, idle.init, idle.generation = plan, prepared.witness.identity, generation
    state = SimpleNamespace(idle_reads=0, captures=[], fault=None)

    def idle_read():
        state.idle_reads += 1
        result = m.idle_module.Evidence(
            plan.sha256,
            generation,
            idle.init,
            "a" * 64,
            "b" * 64,
            plan.lease_sha256,
            "c" * 64,
            issued,
            m.plans.clock.read().boottime_ns / m.plans.clock.NS,
        )
        if state.fault == "idle_changed" and state.idle_reads == 2:
            return replace(result, claim_sha256="d" * 64)
        if state.fault == "stale_idle":
            return replace(result, sampled_at=issued - 1)
        return result

    idle.read = idle_read

    def supervisor(self, key):
        assert self.docker is docker
        assert self.image == plan.cli_image and self.incarnation == plan.cli_generation
        return host.read(key)

    def normal_files(selected, container):
        assert selected == next(item for item in plan.layouts if item.slug == b.NORMAL)
        state.captures.append(b.NORMAL)
        return host.files[b.NORMAL]

    def candidate_files(selected, container):
        assert selected == projected.layout
        assert container["Id"] == idle.init.container_id
        state.captures.append(b.CANDIDATE)
        return state.static

    state.static = static
    monkeypatch.setattr(m.plans.ordinary.SupervisorReads, "read", supervisor)
    monkeypatch.setattr(m.plans.host.static, "collect", normal_files)
    monkeypatch.setattr(m.plans.host.candidate_static, "collect", candidate_files)
    obj = m.BootstrapHost(plan, projected, idle, prepared.witness, docker)
    host.reads.clear()
    state.obj, state.plan, state.projected = obj, plan, projected
    state.host, state.idle, state.prepared, state.docker = host, idle, prepared, docker
    state.recording_root = tree.root
    return state


def test_complete_host_join_keeps_real_pristine_files_and_unknown_native_flags(joined):
    s = joined
    s.host.native_state = (True, True)  # Cached health is not an idle claim.
    assert not s.captures and s.idle_reads == 0 and not s.host.reads
    for _ in range(2):
        sample = s.obj()
        assert type(sample) is m.bootstrap.recovery.Sample
        assert sample.boot_id == s.plan.boot
        observed = sample.observation
        assert observed.normal.state == "stopped" and observed.normal.pin == s.plan.normal.pin
        assert observed.candidate.state == "running"
        assert observed.candidate.generation == s.idle.generation
        assert observed.candidate.pin == s.plan.candidate.pin
        assert observed.candidate.healthy is None and observed.candidate.recording is None
        assert observed.jobs_idle and observed.core_running and observed.other_owners_stopped
        assert observed.files == m.bootstrap.recording.Files(
            s.plan.candidate.contract.sha256, "pristine", s.plan.candidate.contract.baseline_sha256
        )
        assert sample.now >= observed.sampled_at
    assert s.host.reads == ["apps", "jobs", "core", "normal", "candidate", "apps", "jobs"] * 2
    assert s.captures == [b.NORMAL, b.CANDIDATE] * 2 and s.idle_reads == 4
    assert not s.obj.failed and not s.obj.lock.locked() and not s.prepared.witness.exited()


def test_foreign_thread_cannot_reuse_original_host_collector(joined):
    s, errors = joined, []

    def run():
        try:
            s.obj()
        except m.UnconfirmedHostLaunch as error:
            errors.append(str(error))

    worker = Thread(target=run)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive() and errors == [m.MESSAGE]
    assert s.obj.failed and not s.host.reads and s.idle_reads == 0
    launch.denied(s.obj)
    assert not s.prepared.witness.exited()


@pytest.mark.parametrize("error", [OSError("PRIVATE failure"), KeyboardInterrupt()])
def test_host_failure_keeps_original_handles_and_releases_lock(joined, error):
    s = joined

    def fail(_):
        raise error

    s.host.hook = fail
    if isinstance(error, Exception):
        launch.denied(s.obj)
    else:
        with pytest.raises(type(error)):
            s.obj()
    assert s.obj.failed and not s.obj.lock.locked() and not s.prepared.witness.exited()
    launch.denied(s.obj)


@pytest.mark.parametrize(
    "part", ["normal_recordings", "candidate_source", "candidate_options", "jobs"]
)
def test_policy_sees_changed_pins_and_busy_jobs_without_fabricated_health(joined, part):
    s = joined
    s.obj()
    if part == "normal_recordings":
        s.host.files[b.NORMAL] = replace(s.host.files[b.NORMAL], recordings="f" * 64)
    elif part == "candidate_source":
        s.static = replace(s.static, package="f" * 64)
    elif part == "candidate_options":
        s.host.private_options["candidate"] = "changed"
    else:
        s.host.jobs = False
    observed = s.obj().observation
    assert observed.candidate.healthy is None and observed.candidate.recording is None
    if part == "normal_recordings":
        assert observed.normal.pin != s.plan.normal.pin
    elif part == "jobs":
        assert not observed.jobs_idle
    else:
        assert observed.candidate.pin != s.plan.candidate.pin


@pytest.mark.parametrize(
    "fault",
    [
        "original_file",
        "new_recording",
        "core_generation",
        "version",
        "network",
        "candidate_generation",
        "candidate_stopped",
        "normal_started",
        "idle_changed",
        "stale_idle",
        "idle_identity",
        "docker_route",
        "closed_pidfd",
        "plan",
        "projection",
        "late",
        "late_initial_guard",
        "reentrant",
    ],
)
def test_uncertainty_poisons_original_collector_without_handle_close_or_fallback(
    joined, monkeypatch, fault
):
    s = joined
    s.obj()  # Every fault starts from a genuinely working joined collector.
    s.idle_reads = 0
    s.host.reads.clear()
    if fault == "original_file":
        name = s.projected.host.baseline.files[0][0]
        (s.recording_root / name).write_bytes(b"PRIVATE_CHANGED")
    elif fault == "new_recording":
        (s.recording_root / "new.wav").write_bytes(b"PRIVATE_UNACKNOWLEDGED")
    elif fault == "core_generation":
        s.host.values[audio.h.CORE]["State"]["Pid"] += 1
    elif fault == "version":
        s.host.versions = dict(s.host.versions) | {b.CANDIDATE: "changed"}
    elif fault == "network":
        s.host.values["app_" + b.CANDIDATE]["HostConfig"]["NetworkMode"] = "host"
    elif fault == "candidate_generation":
        s.host.values["app_" + b.CANDIDATE]["State"]["Pid"] += 1
    elif fault == "candidate_stopped":
        s.host.values.pop("app_" + b.CANDIDATE)
    elif fault == "normal_started":
        normal = audio.ot.recovery_tests.container("app_" + b.NORMAL, 3)
        normal.update(audio.container_network(False))
        s.host.values["app_" + b.NORMAL] = normal
    elif fault in ("idle_changed", "stale_idle"):
        s.fault = fault
    elif fault == "idle_identity":
        s.idle.generation = "f" * 64
    elif fault == "docker_route":
        s.docker.path = "/PRIVATE/socket"
    elif fault == "closed_pidfd":
        s.prepared.witness.close()
    elif fault == "plan":
        object.__setattr__(s.plan, "core_generation", "f" * 64)
    elif fault == "projection":
        s.obj.projected = None
    elif fault in ("late", "late_initial_guard"):
        original = m.plans.clock.read
        reads = 0

        def later():
            nonlocal reads
            reads += 1
            observed = original()
            if s.host.reads or (fault == "late_initial_guard" and reads > 1):
                return replace(
                    observed,
                    before_ns=observed.before_ns + 3 * m.plans.clock.NS,
                    after_ns=observed.after_ns + 3 * m.plans.clock.NS,
                    boottime_ns=observed.boottime_ns + 3 * m.plans.clock.NS,
                )
            return observed

        monkeypatch.setattr(m.plans.clock, "read", later)
    else:

        def nested(_):
            launch.denied(s.obj)

        s.host.hook = nested
    launch.denied(s.obj)
    assert s.obj.failed and not s.obj.lock.locked()
    read_count = len(s.host.reads)
    launch.denied(s.obj)
    assert len(s.host.reads) == read_count
    if fault != "closed_pidfd":
        assert not s.prepared.witness.exited()


@pytest.mark.parametrize("field", ["plan", "projected", "idle", "witness", "docker"])
def test_no_serialized_report_can_replace_bound_collector_inputs(joined, field):
    s = joined
    values = dict(
        plan=s.plan, projected=s.projected, idle=s.idle, witness=s.prepared.witness, docker=s.docker
    )
    values[field] = {"looks_valid": True}
    launch.denied(lambda: m.BootstrapHost(**values))
    assert not s.host.reads


def test_prepared_complete_host_read_joins_original_thread_and_oldest_time(joined):
    s, idle_threads = joined, []
    original_idle = s.idle.read

    def idle_read():
        idle_threads.append(get_ident())
        return original_idle()

    s.idle.read = idle_read
    assert s.obj.prepare() is None
    pending = s.obj.pending
    assert pending.done.wait(2)
    assert pending.worker.ident != get_ident()
    sample = s.obj()
    assert s.obj.pending is None and not pending.worker.is_alive()
    assert idle_threads == [get_ident(), get_ident()]
    assert sample.observation.sampled_at == pending.began
    assert sample.observation.candidate.healthy is None
    assert sample.observation.candidate.recording is None
    assert s.host.reads == ["apps", "jobs", "core", "normal", "candidate", "apps", "jobs"]
    assert s.idle_reads == 2 and not s.prepared.witness.exited()
    s.obj()  # Following reads are fresh; the prepared sample is not a cache.
    assert len(s.host.reads) == 14


@pytest.mark.parametrize("error", [OSError("PRIVATE error"), KeyboardInterrupt()])
def test_prepared_worker_failure_cannot_escape_as_success_or_close_original_pidfd(joined, error):
    s = joined

    def fail(_):
        raise error

    s.host.hook = fail
    s.obj.prepare()
    pending = s.obj.pending
    assert pending.done.wait(2)
    if isinstance(error, Exception):
        launch.denied(s.obj)
    else:
        with pytest.raises(type(error)):
            s.obj()
    assert s.obj.failed and pending.cancelled.is_set() and not s.obj.lock.locked()
    assert not pending.worker.is_alive() and not s.prepared.witness.exited()
    launch.denied(s.obj.prepare)


@pytest.mark.parametrize("action", ["repeat", "discard", "foreign_thread", "timeout"])
def test_pending_read_invalidated_without_abandoning_or_reusing_result(joined, action):
    s, entered, release = joined, Event(), Event()

    def block(_):
        entered.set()
        assert release.wait(4)

    s.host.hook = block
    s.obj.prepare()
    pending = s.obj.pending
    try:
        assert entered.wait(1)
        assert pending.worker.is_alive() and not pending.done.is_set()
        if action == "repeat":
            launch.denied(s.obj.prepare)
        elif action == "discard":
            assert s.obj.discard() is None
        elif action == "foreign_thread":
            failures = []

            def read_elsewhere():
                try:
                    s.obj()
                except m.UnconfirmedHostLaunch:
                    failures.append(True)

            worker = Thread(target=read_elsewhere)
            worker.start()
            worker.join(1)
            assert failures == [True] and not worker.is_alive()
        else:
            began = time.monotonic()
            launch.denied(s.obj)
            assert 0 < time.monotonic() - began < 2.2
        assert s.obj.failed and pending.cancelled.is_set()
        launch.denied(s.obj)
        assert s.obj.pending is pending and not s.obj.lock.locked()
    finally:
        release.set()
        pending.worker.join(2)
    assert not pending.worker.is_alive() and not s.prepared.witness.exited()
    # Completion of a discarded worker can never make the reader usable again.
    launch.denied(s.obj)
    launch.denied(s.obj.prepare)


@pytest.mark.parametrize("field", ["deadline", "boot", "began", "before", "worker"])
def test_prepared_evidence_cannot_renew_or_replace_original_context(joined, field):
    s = joined
    s.obj.prepare()
    pending = s.obj.pending
    assert pending.done.wait(2)
    pending.worker.join(1)
    if field in ("deadline", "began"):
        setattr(pending, field, getattr(pending, field) + 1)
    else:
        setattr(pending, field, None)
    launch.denied(s.obj)
    assert s.obj.failed and not s.prepared.witness.exited()


def test_prepared_sample_completion_does_not_refresh_original_freshness(joined, monkeypatch):
    s = joined
    s.obj.prepare()
    pending = s.obj.pending
    assert pending.done.wait(2)
    pending.worker.join(1)
    original = m.plans.clock.read

    def later():
        value = original()
        return replace(
            value,
            before_ns=value.before_ns + 3 * m.plans.clock.NS,
            after_ns=value.after_ns + 3 * m.plans.clock.NS,
            boottime_ns=value.boottime_ns + 3 * m.plans.clock.NS,
        )

    monkeypatch.setattr(m.plans.clock, "read", later)
    launch.denied(s.obj)
    assert s.obj.failed and pending.cancelled.is_set()
    assert not s.prepared.witness.exited()


@pytest.mark.parametrize(
    "fault",
    ["idle", "identity", "source", "plan_nested", "plan_raw", "plan_replaced", "new_recording"],
)
def test_original_checks_still_bracket_prepared_read(joined, fault):
    s = joined
    s.obj.prepare()
    pending = s.obj.pending
    assert pending.done.wait(2)
    pending.worker.join(1)
    if fault == "idle":
        s.fault = "idle_changed"
    elif fault == "identity":
        s.prepared.witness.close()
    elif fault == "source":
        object.__setattr__(s.plan, "core_generation", "f" * 64)
    elif fault == "plan_nested":
        object.__setattr__(s.plan.helper, "source", "f" * 64)
    elif fault == "plan_raw":
        object.__setattr__(s.plan, "raw", s.plan.raw + b" ")
    elif fault == "plan_replaced":
        s.obj.plan = m.plans.load_bytes(s.plan.raw, s.plan.sha256)
        s.idle.plan = s.obj.plan
    else:
        # A file introduced before a NEW prepared read must not adopt that root.
        assert s.obj()
        (s.recording_root / "new.wav").write_bytes(b"PRIVATE_UNACKNOWLEDGED")
        s.obj.prepare()
        pending = s.obj.pending
        assert pending.done.wait(2)
    launch.denied(s.obj)
    pending.worker.join(1)
    assert s.obj.failed and pending.cancelled.is_set()
