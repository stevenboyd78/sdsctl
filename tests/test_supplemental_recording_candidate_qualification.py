"""Actual files/startup bytes/owned pidfd; synthetic Engine/idle/HAOS routing.

The synthetic container cgroup, metadata and path alias are explicit. This does
not qualify an installed App, independently supervised helper or scanner test.
"""

import copy
import os
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_environment as env
from . import test_supplemental_recording_host_launch as launch
from . import test_supplemental_recording_timezone_runtime as zones

m = launch.m
layout, image_umask, supervised = zones.layout, zones.image_umask, zones.supervised
image, configured = env.image, env.configured
CID = "a" * 64


@pytest.fixture
def candidate(supervised, image, configured, monkeypatch):
    native = m.plans.host.candidate_static
    product, helpers = supervised.root / m.plans.fixed.PACKAGE, supervised.root / native.NATIVE
    for root, names in (
        (product, native.source.REQUIRED_RUNTIME),
        (helpers, native.source.NATIVE_FILES),
    ):
        root.mkdir(parents=True, exist_ok=True)
        for name in names:
            (root / name).write_bytes(b"raise RuntimeError('PRIVATE-NEVER-IMPORT-CANDIDATE')\n")
            (root / name).chmod(0o644)
    source_pin = native.source.Layout(product, helpers).observe().sha256
    clock = m.plans.clock.read()
    issued = clock.boottime_ns / m.plans.clock.NS
    value = launch.plans.value()
    value.update(
        boot=clock.boot,
        original_clock=asdict(clock) | {"namespace": list(clock.namespace)},
        deadlines=dict(
            issued_at=issued, ready_by=issued + 120, stop_by=issued + 400, recover_by=issued + 1500
        ),
    )
    value["candidate_runtime"].update(
        source=source_pin,
        interpreter=supervised.observe_supervised(env.TIMEZONE).sha256,
        environment=env.config_pin(configured, image),
    )
    value["candidate"]["files"]["package"] = source_pin
    for item in value["layouts"]:
        if item["slug"] == m.base.CANDIDATE:
            item["image_package_sha256"] = source_pin
    plan = m.plans.decode(value)
    values = dict(entry.split("=", 1) for entry in configured)
    values.update(HOME="/root", HOSTNAME=env.HOSTNAME)
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        env=values,
    )
    witness = None
    state = SimpleNamespace(containers=0, images=0, idles=0, fault=None, when=1, events=[])

    def identity(pid, cid):
        assert (pid, cid) == (child.pid, CID)
        return m.engine.dispatch.process.process_identity(
            pid,
            cid,
            Path(f"/proc/{pid}/stat").read_text(),
            f"0::/system.slice/docker-{cid}.scope\n",
        )

    monkeypatch.setattr(m.engine.dispatch.process, "read_identity", identity)
    try:
        witness = m.engine.dispatch.process.ProcessWitness(identity(child.pid, CID))
        protected = next(item for item in plan.layouts if item.slug == m.base.CANDIDATE)
        container = dict(
            Id=CID,
            Name="/app_" + m.base.CANDIDATE,
            Image=plan.candidate_runtime.image,
            State=dict(
                Status="running",
                Running=True,
                Paused=False,
                Restarting=False,
                Dead=False,
                OOMKilled=False,
                Pid=child.pid,
                Error="",
                StartedAt="2026-09-23T00:00:00Z",
            ),
            Path=plan.idle_argv[0],
            Args=list(plan.idle_argv[1:]),
            Config=dict(
                User="0:0",
                Hostname=env.HOSTNAME,
                WorkingDir="/",
                Env=configured,
                Entrypoint=list(plan.idle_argv),
                Cmd=None,
            ),
            HostConfig=dict(RestartPolicy=dict(Name="no", MaximumRetryCount=0)),
            Mounts=[
                dict(Type="bind", Source=str(protected.data), Destination="/data", RW=True),
                dict(Type="bind", Source=str(protected.media), Destination="/media", RW=True),
            ],
            GraphDriver=dict(
                Name="overlay2",
                Data=dict(ID=CID, MergedDir="/mnt/data/docker/overlay2/qualification/merged"),
            ),
        )
        generation = m.plans.ordinary.generation(
            container, name="app_" + m.base.CANDIDATE, image=plan.candidate_runtime.image
        )
        idle = object.__new__(m.idle_module.Idle)
        idle.plan, idle.init, idle.generation = plan, witness.identity, generation

        def idle_read(self):
            assert self is idle
            state.idles += 1
            state.events.append("idle")
            result = m.idle_module.Evidence(
                plan.sha256,
                generation,
                witness.identity,
                "1" * 64,
                "2" * 64,
                plan.lease_sha256,
                "3" * 64,
                issued,
                time.monotonic(),
            )
            if state.fault == "idle_files" and state.idles == 2:
                result = replace(result, files_sha256="4" * 64)
            return result

        def inspect(self, name):
            assert self is docker and name == "app_" + m.base.CANDIDATE
            state.containers += 1
            state.events.append("container")
            result = copy.deepcopy(container)
            if state.containers == state.when and callable(state.fault):
                state.fault(result)
            return result

        def inspect_image(self, name):
            assert self is docker and name == plan.candidate_runtime.image
            state.images += 1
            state.events.append("image")
            result = dict(Id=name, Os="linux", Architecture="amd64", Config=dict(Env=image))
            if state.fault == "image" and state.images == state.when:
                result["Architecture"] = "arm64"
            return result

        resolver = m.plans.fixed._merged_root

        def mapped(layout, metadata, *, code_roots):
            # Exercise the genuine closed mount resolver BEFORE aliasing its
            # HAOS path to this test's actual file tree. No installed-path claim.
            assert resolver(layout, metadata, code_roots=code_roots) == Path(
                container["GraphDriver"]["Data"]["MergedDir"]
            )
            return supervised.root

        monkeypatch.setattr(m.idle_module.Idle, "read", idle_read)
        monkeypatch.setattr(m.plans.ordinary.Docker, "container", inspect)
        monkeypatch.setattr(m.plans.ordinary.Docker, "image", inspect_image)
        monkeypatch.setattr(m.plans.fixed, "_merged_root", mapped)
        docker = m.plans.ordinary.Docker()
        state.make = lambda **changes: m.CandidateQualification(
            plan,
            idle,
            witness,
            docker,
            **(
                dict(
                    image_environment_sha256=m.runtime.environment(image),
                    timezone=env.TIMEZONE,
                    hostname=env.HOSTNAME,
                    architecture="amd64",
                )
                | changes
            ),
        )
        state.plan, state.idle, state.witness, state.child = plan, idle, witness, child
        state.root, state.container, state.docker = supervised.root, container, docker
        yield state
    finally:
        if witness is not None:
            witness.close()
        child.stdin.close()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()  # Only this test's owned harmless child.
            child.wait(timeout=3)


def denied(obj):
    launch.denied(obj)
    assert obj.failed and obj.elapsed_seconds is None
    launch.denied(obj)  # Failure is permanently consumed, never refreshed.


def test_complete_fresh_join_returns_no_authority_and_retains_no_credentials(candidate, capsys):
    obj = candidate.make()
    original = (candidate.plan.raw, candidate.plan.lease, candidate.witness.fd)
    before = len(os.listdir("/proc/self/fd"))
    assert obj() is None and 0 <= obj.elapsed_seconds < 2
    assert candidate.events == ["idle", "container", "image", "container", "image", "idle"]
    assert obj() is None and candidate.containers == 4 and candidate.idles == 4
    assert original == (candidate.plan.raw, candidate.plan.lease, candidate.witness.fd)
    assert not candidate.witness.exited() and before == len(os.listdir("/proc/self/fd"))
    assert env.TOKEN not in repr(vars(obj)) and capsys.readouterr() == ("", "")


def test_actual_full_files_and_original_process_reads_surround_observation(candidate, monkeypatch):
    obj, trace = candidate.make(), []
    source = m.plans.host.candidate_static.source
    source_snapshot, runtime_snapshot = source.Layout._snapshot, m.runtime.Layout._snapshot
    environment = m.runtime.collect_supervised_process_environment

    def read_source(self):
        trace.append("source")
        return source_snapshot(self)

    def read_runtime(self, deadline, **kwargs):
        trace.append("runtime")
        return runtime_snapshot(self, deadline, **kwargs)

    def read_environment(*args, **kwargs):
        trace.append("environment")
        return environment(*args, **kwargs)

    def observe():
        trace.append("observe")
        assert not obj.failed and not candidate.witness.exited()
        return result

    result = object()
    monkeypatch.setattr(source.Layout, "_snapshot", read_source)
    monkeypatch.setattr(m.runtime.Layout, "_snapshot", read_runtime)
    monkeypatch.setattr(m.runtime, "collect_supervised_process_environment", read_environment)
    assert obj.during(observe) is result
    assert trace == [
        "environment",
        "source",
        "runtime",
        "observe",
        "runtime",
        "source",
        "environment",
    ]
    assert candidate.events == ["idle", "container", "image", "container", "image", "idle"]
    assert 0 <= obj.elapsed_seconds < 2 and not candidate.witness.exited()


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "runtime",
        "environment",
        "metadata",
        "idle",
        "exit",
        "late",
        "raise",
        "reentrant",
        "plan_nested",
        "plan_raw",
        "plan_replaced",
    ],
)
def test_bracket_permanently_refuses_every_post_observation_failure(candidate, monkeypatch, fault):
    obj = candidate.make()
    end = time.monotonic() + 3
    calls = []

    def observe():
        calls.append(True)
        if fault == "source":
            (
                candidate.root
                / m.plans.host.candidate_static.NATIVE
                / "accept_supplemental_recording.py"
            ).write_bytes(b"PRIVATE_DRIFT")
        elif fault == "runtime":
            (candidate.root / m.runtime.ZONEINFO / "tzdata.zi").write_bytes(b"PRIVATE_DRIFT")
        elif fault == "environment":
            original = m.runtime.collect_supervised_process_environment
            monkeypatch.setattr(
                m.runtime,
                "collect_supervised_process_environment",
                lambda *a, **k: replace(original(*a, **k), sha256="f" * 64),
            )
        elif fault == "metadata":
            candidate.when = 2
            candidate.fault = lambda c: c["Config"].update(Hostname="changed")
        elif fault == "idle":
            candidate.fault = "idle_files"
        elif fault == "exit":
            candidate.child.stdin.close()
            candidate.child.wait(timeout=3)
        elif fault == "late":
            monkeypatch.setattr(m.time, "monotonic", lambda: end)
        elif fault == "raise":
            raise OSError("PRIVATE callback failed")
        elif fault == "reentrant":
            launch.denied(obj)
        elif fault == "plan_nested":
            object.__setattr__(candidate.plan.candidate_runtime, "source", "f" * 64)
        elif fault == "plan_raw":
            object.__setattr__(candidate.plan, "raw", candidate.plan.raw + b" ")
        elif fault == "plan_replaced":
            obj.plan = m.plans.load_bytes(candidate.plan.raw, candidate.plan.sha256)
            candidate.idle.plan = obj.plan
        return object()

    launch.denied(lambda: obj.during(observe))
    assert obj.failed and obj.elapsed_seconds is None and calls == [True]
    launch.denied(lambda: obj.during(observe))
    assert calls == [True]
    os.fstat(candidate.witness.fd)  # Failure never closes the caller's handle.


def test_invalid_callback_is_a_consumed_failure(candidate):
    obj = candidate.make()
    launch.denied(lambda: obj.during(None))
    assert obj.failed
    launch.denied(obj)


@pytest.mark.parametrize("when", [1, 2])
@pytest.mark.parametrize(
    "fault",
    [
        lambda c: c.update(Id="b" * 64),
        lambda c: c.update(Image="sha256:" + "b" * 64),
        lambda c: c["State"].update(Pid=1),
        lambda c: c["State"].update(StartedAt="2026-09-23T00:00:01Z"),
        lambda c: c["State"].update(Running=False),
        lambda c: c["Config"].update(User="root"),
        lambda c: c["Config"].update(WorkingDir="/data"),
        lambda c: c["Config"].update(Hostname="different"),
        lambda c: c["Config"].update(Cmd=["extra"]),
        lambda c: c["Config"].update(Env=[*c["Config"]["Env"], "LD_PRELOAD=/PRIVATE"]),
        lambda c: c["HostConfig"]["RestartPolicy"].update(Name="always"),
        lambda c: c["Args"].append("extra"),
        lambda c: c["Mounts"][0].update(Source="/PRIVATE"),
        "image",
    ],
)
def test_original_identity_environment_command_and_mounts_rechecked(candidate, when, fault):
    obj = candidate.make()
    candidate.fault, candidate.when = fault, when
    denied(obj)
    assert candidate.containers <= when and not candidate.witness.exited()


@pytest.mark.parametrize(
    "path",
    [
        "/etc",
        "/etc/ssl",
        "/etc/ssl/subdir",
        "/etc/ld.so.conf",
        "/etc/ld.so.cache",
        "/etc/ld.so.conf.d",
        "/etc/ld.so.preload",
        "/etc/localtime",
        "/etc/timezone",
        "/usr/share/zoneinfo",
        "/usr/share/zoneinfo/America/Denver",
        "/opt",
        "/data/nested",
        "/media/nested",
    ],
)
def test_shadow_mounts_cannot_bypass_actual_file_hashes(candidate, path):
    obj = candidate.make()
    candidate.container["Mounts"].append(
        dict(Type="bind", Source="/PRIVATE", Destination=path, RW=False)
    )
    denied(obj)
    assert candidate.images == 0


@pytest.mark.parametrize(
    "path",
    [
        "opt/sdsctl-supplemental-recording/accept_supplemental_recording.py",
        "usr/local/lib/python3.14/site-packages/sds200/daemon_runtime.py",
        "usr/local/lib/python3.14/os.py",
        "usr/share/zoneinfo/America/Denver",
        "etc/ssl/openssl.cnf",
    ],
)
def test_changed_native_product_runtime_or_timezone_bytes_refused(candidate, path):
    obj = candidate.make()
    (candidate.root / path).write_bytes(b"PRIVATE_CHANGED")
    denied(obj)
    assert candidate.containers == 1


def test_after_read_configuration_drift_is_not_recaptured(candidate):
    obj = candidate.make()
    candidate.when = 2
    candidate.fault = lambda c: c["HostConfig"].update(ExtraHosts=["PRIVATE"])
    denied(obj)


def test_idle_files_must_stay_original(candidate):
    obj = candidate.make()
    candidate.fault = "idle_files"
    denied(obj)


def test_rotated_config_credential_is_never_adopted(candidate):
    obj = candidate.make()
    candidate.container["Config"]["Env"] = env.replace_value(
        candidate.container["Config"]["Env"], "SUPERVISOR_TOKEN", "y" * 112
    )
    denied(obj)


def test_actual_proc_bytes_must_match_independently_pinned_configuration(candidate, monkeypatch):
    obj = candidate.make()
    original = m.runtime.supervised_process_environment

    def mismatched(raw, **kwargs):
        assert kwargs["fixed_exec"] is False
        return original(raw.replace(env.TOKEN.encode(), b"y" * len(env.TOKEN)), **kwargs)

    monkeypatch.setattr(m.runtime, "supervised_process_environment", mismatched)
    denied(obj)
    assert candidate.containers == candidate.images == 1


def test_lost_original_handle_during_collection_never_rebinds(candidate, monkeypatch):
    obj = candidate.make()
    original = m.runtime.Layout.verify_supervised

    def lost(*args):
        result = original(*args)
        candidate.witness.close()
        return result

    monkeypatch.setattr(m.runtime.Layout, "verify_supervised", lost)
    denied(obj)
    assert candidate.containers == 1 and candidate.child.poll() is None


@pytest.mark.parametrize(
    "fault", ["timezone", "hostname", "architecture", "witness", "owner", "reader", "lock"]
)
def test_original_inputs_owner_and_transport_cannot_be_replaced(candidate, fault):
    obj = candidate.make()
    if fault in ("timezone", "hostname", "architecture"):
        setattr(obj, fault, "different")
    elif fault == "witness":
        candidate.witness.close()
    elif fault == "owner":
        obj.owner = (-1, -1)
    elif fault == "reader":
        candidate.docker.path = "/PRIVATE/socket"
    else:
        obj.lock.acquire()
    denied(obj)


def test_runtime_overrun_cannot_refresh_original_deadline(candidate, monkeypatch):
    obj = candidate.make()
    original = m.runtime.Layout.verify_supervised

    def slow(*args):
        result = original(*args)
        time.sleep(0.025)
        return result

    monkeypatch.setattr(m.CandidateQualification, "MAX_SECONDS", 0.02)
    monkeypatch.setattr(m.runtime.Layout, "verify_supervised", slow)
    deadline = candidate.plan.lease["ready_by"]
    denied(obj)
    assert candidate.plan.lease["ready_by"] == deadline and candidate.containers <= 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"image_environment_sha256": "bad"},
        {"timezone": "../etc/passwd"},
        {"hostname": "with spaces"},
        {"architecture": "unknown"},
    ],
)
def test_invalid_independent_profile_fails_before_read(candidate, kwargs):
    launch.denied(lambda: candidate.make(**kwargs))
    assert not candidate.events
