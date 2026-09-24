"""Full observer/pristine files with synthetic HA/normal cached transport.

No installed source/runtime, live scanner, App handoff or recovery claim.
"""

import json
import os
import time
from dataclasses import replace
from threading import Thread

import pytest

from . import test_supplemental_recording_bootstrap_host as bootstrap

m, b, audio = bootstrap.m, bootstrap.b, bootstrap.audio
layout, tree, routing, projection, binding, directory, prepared, joined = (
    bootstrap.layout,
    bootstrap.tree,
    bootstrap.routing,
    bootstrap.projection,
    bootstrap.binding,
    bootstrap.directory,
    bootstrap.prepared,
    bootstrap.joined,
)


@pytest.fixture
def before_handoff(joined, monkeypatch):
    s = joined
    s.host.values.pop("app_" + b.CANDIDATE)
    normal = audio.ot.recovery_tests.container("app_" + b.NORMAL, 3)
    normal.update(audio.container_network(False))
    s.host.values["app_" + b.NORMAL] = normal
    value = json.loads(s.plan.raw)
    value["normal_generation"] = audio.h.generation(
        normal, name="app_" + b.NORMAL, image=s.plan.normal.image
    )
    s.plan = m.plans.decode(value)
    s.cached_calls = []
    s.after_cached = lambda: None

    def candidate_files(selected, container):
        assert selected == s.projected.layout and container is None
        s.captures.append(b.CANDIDATE)
        return s.static

    def cached(docker, seal, command, generation):
        assert docker is s.docker and seal is s.plan.normal
        assert command == s.before.normal_reader.command
        assert generation == s.plan.normal_generation
        s.cached_calls.append((seal.slug, generation))
        s.after_cached()
        return m.plans.ordinary.NativeState(generation, *s.host.native_state)

    monkeypatch.setattr(m.plans.host.candidate_static, "collect", candidate_files)
    monkeypatch.setattr(m.normal_read.cached, "_read_probe", cached)
    s.before = m.PreHandoffHost(s.plan, s.projected, s.docker)
    s.host.reads.clear()
    return s


def denied(s):
    bootstrap.launch.denied(s.before.read)
    assert s.before.failed and not s.before.lock.locked()
    bootstrap.launch.denied(s.before.read)


def test_complete_current_sample_keeps_original_plan_and_pristine_files(before_handoff):
    s = before_handoff
    original = (s.plan.raw, s.plan.deadlines, s.plan.original_clock, s.projected.sha256)
    fds = len(os.listdir("/proc/self/fd"))
    assert not s.host.reads and not s.captures and not s.cached_calls
    sample = s.before.read()
    assert type(sample) is m.bootstrap.recovery.Sample
    assert sample.boot_id == s.plan.boot
    assert sample.observation.normal == b.App(
        s.plan.normal.pin, "running", s.plan.normal_generation, True, False
    )
    assert sample.observation.candidate == b.App(s.plan.candidate.pin, "stopped")
    assert sample.observation.files == m.bootstrap.recording.Files(
        s.plan.candidate.contract.sha256, "pristine", s.plan.candidate.contract.baseline_sha256
    )
    assert sample.observation.jobs_idle and sample.observation.other_owners_stopped
    assert sample.observation.core_running and sample.observation.sampled_at <= sample.now
    assert original == (s.plan.raw, s.plan.deadlines, s.plan.original_clock, s.projected.sha256)
    assert s.host.reads == ["apps", "jobs", "core", "normal", "candidate", "apps", "jobs"]
    assert s.captures == [b.NORMAL, b.CANDIDATE]
    assert s.cached_calls == [(b.NORMAL, s.plan.normal_generation)]
    assert s.before.normal_reader.used and not s.before.normal_reader.failed
    assert s.idle_reads == 0 and fds == len(os.listdir("/proc/self/fd"))
    denied(s)  # Even a successful instance is one-use, not a polling/retry API.
    assert s.cached_calls == [(b.NORMAL, s.plan.normal_generation)]


@pytest.mark.parametrize("state", ["running", "exited", "created", "paused", "restarting"])
def test_existing_candidate_never_becomes_fresh_case(before_handoff, state):
    s = before_handoff
    container = audio.ot.recovery_tests.container("app_" + b.CANDIDATE, 4)
    container["State"]["Status"] = state
    s.host.values["app_" + b.CANDIDATE] = container
    denied(s)
    assert not s.host.reads and not s.cached_calls and s.before.normal_reader is None
    assert s.host.values["app_" + b.CANDIDATE] is container


@pytest.mark.parametrize(
    "health,recording", [(None, None), (None, False), (False, False), (True, True), (True, None)]
)
def test_unknown_unhealthy_or_recording_normal_cannot_qualify(before_handoff, health, recording):
    s = before_handoff
    s.host.native_state = health, recording
    denied(s)
    assert len(s.cached_calls) == 1


@pytest.mark.parametrize("fault", ["absent", "generation", "options", "files"])
def test_changed_normal_not_probed_or_promoted(before_handoff, fault):
    s = before_handoff
    if fault == "absent":
        s.host.values.pop("app_" + b.NORMAL)
    elif fault == "generation":
        s.host.values["app_" + b.NORMAL]["State"]["Pid"] += 1
    elif fault == "options":
        s.host.private_options["normal"] = "PRIVATE changed"
    else:
        s.host.files[b.NORMAL] = replace(s.host.files[b.NORMAL], recordings="e" * 64)
    denied(s)
    assert not s.cached_calls


@pytest.mark.parametrize(
    "fault", ["old_recording", "extra_recording", "candidate_source", "jobs", "core", "other_owner"]
)
def test_complete_host_constraints_cannot_be_skipped(before_handoff, fault):
    s = before_handoff
    if fault == "old_recording":
        name = s.projected.host.baseline.files[0][0]
        (s.recording_root / name).write_bytes(b"PRIVATE changed")
    elif fault == "extra_recording":
        (s.recording_root / "unplanned.wav").write_bytes(b"PRIVATE new")
    elif fault == "candidate_source":
        s.static = replace(s.static, package="f" * 64)
    elif fault == "jobs":
        s.host.jobs = False
    elif fault == "core":
        s.host.values[audio.h.CORE]["State"]["Pid"] += 1
    else:
        slug = next(iter(s.plan.other_scanner_apps))
        s.host.values["app_" + slug] = audio.ot.recovery_tests.container("app_" + slug, 8)
    denied(s)


@pytest.mark.parametrize("fault", ["plan", "projection", "docker", "owner", "route"])
def test_original_input_identity_is_retained(before_handoff, fault):
    s = before_handoff
    if fault == "plan":
        s.before.plan = m.plans.load_bytes(s.plan.raw, s.plan.sha256)
    elif fault == "projection":
        s.before.projected = replace(s.projected)
    elif fault == "docker":
        s.before.docker = m.plans.ordinary.Docker()
    elif fault == "owner":
        s.before.owner = (-1, -1)
    else:
        s.docker.path = "/PRIVATE/changed.sock"
    denied(s)
    assert not s.host.reads and not s.cached_calls


@pytest.mark.parametrize("fault", ["plan", "files", "generation", "candidate", "late", "reentrant"])
def test_drift_after_cached_read_cannot_return_sample(before_handoff, monkeypatch, fault):
    s = before_handoff

    def changed():
        if fault == "plan":
            object.__setattr__(s.plan.deadlines, "ready_by", s.plan.deadlines.ready_by + 1)
        elif fault == "files":
            (s.recording_root / "new.wav").write_bytes(b"PRIVATE")
        elif fault == "generation":
            s.host.values["app_" + b.NORMAL]["State"]["Pid"] += 1
        elif fault == "candidate":
            s.host.values["app_" + b.CANDIDATE] = audio.ot.recovery_tests.container(
                "app_" + b.CANDIDATE, 4
            )
        elif fault == "late":
            end = time.monotonic() + 3
            monkeypatch.setattr(m.time, "monotonic", lambda: end)
        else:
            bootstrap.launch.denied(s.before.read)

    s.after_cached = changed
    denied(s)
    assert len(s.cached_calls) == 1


@pytest.mark.parametrize("error", [OSError("PRIVATE error"), KeyboardInterrupt(), SystemExit(71)])
def test_failure_or_interruption_does_not_retry_write_or_hide_interrupt(
    before_handoff, error, capsys
):
    s = before_handoff

    def failed(key):
        raise error

    s.host.hook = failed
    if isinstance(error, Exception):
        denied(s)
    else:
        with pytest.raises(type(error)):
            s.before.read()
    assert s.before.used and s.before.failed and not s.before.lock.locked()
    assert not s.cached_calls and capsys.readouterr() == ("", "")
    assert not s.prepared.witness.exited()


def test_foreign_thread_permanently_consumes_attempt_without_read(before_handoff):
    s, errors = before_handoff, []

    def read():
        try:
            s.before.read()
        except m.UnconfirmedHostLaunch as error:
            errors.append(str(error))

    worker = Thread(target=read)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive() and errors == [m.MESSAGE]
    assert s.before.failed and not s.host.reads and not s.cached_calls
    denied(s)


def test_ready_expiry_cannot_be_renewed_by_current_observation(before_handoff, monkeypatch):
    s = before_handoff
    original = m.plans.clock.read
    deadlines = s.plan.deadlines

    def expired():
        window = original()
        shift = 121 * m.plans.clock.NS
        return replace(
            window,
            before_ns=window.before_ns + shift,
            after_ns=window.after_ns + shift,
            boottime_ns=window.boottime_ns + shift,
        )

    monkeypatch.setattr(m.plans.clock, "read", expired)
    denied(s)
    assert s.plan.deadlines is deadlines and not s.host.reads and not s.cached_calls


@pytest.mark.parametrize("fault", ["plan", "projection", "route"])
def test_invalid_constructor_is_passive_and_sanitized(before_handoff, fault):
    s = before_handoff
    plan, projected, docker = s.plan, s.projected, s.docker
    if fault == "plan":
        plan = object()
    elif fault == "projection":
        projected = None
    else:
        docker.path = "/PRIVATE/socket"
    bootstrap.launch.denied(lambda: m.PreHandoffHost(plan, projected, docker))
    assert not s.host.reads and not s.cached_calls and not s.captures
