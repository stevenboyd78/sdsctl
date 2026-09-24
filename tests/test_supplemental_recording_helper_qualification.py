"""Actual files/environ/pidfd; synthetic Engine, cgroup, command and HAOS paths.

No helper is launched or installed. No App/Engine/scanner operations. Declared
confinement and effective kernel enforcement remain distinct evidence.
"""

import builtins
import copy
import io
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_helper_environment as env
from . import test_supplemental_recording_host_launch as launch
from . import test_supplemental_recording_timezone_runtime as zones

m = launch.m
layout, image_umask, supervised = zones.layout, zones.image_umask, zones.supervised
image, configured = env.image, env.configured
CID = "9" * 64


def configuration_pin(container, environment):
    return m.base.checksum(
        dict(
            schema=1,
            kind="finite-recording-helper-configuration-v1",
            config=container["Config"] | {"Env": environment},
            host=container["HostConfig"],
            mounts=container["Mounts"],
        )
    )


@pytest.fixture
def helper(supervised, image, configured, monkeypatch):
    product = supervised.root / m.plans.fixed.PACKAGE
    helpers = supervised.root / m.HelperQualification.HELPER
    for root, names in (
        (product, m.helper_source.REQUIRED_RUNTIME),
        (helpers, m.helper_source.HELPER_FILES),
    ):
        root.mkdir(parents=True, exist_ok=True)
        for name in names:
            (root / name).write_bytes(b"raise RuntimeError('PRIVATE-NEVER-IMPORT')\n")
            (root / name).chmod(0o644)
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
    value["helper"].update(
        source=m.helper_source.Layout(product, helpers).observe().sha256,
        interpreter=supervised.observe_supervised(env.env.TIMEZONE).sha256,
        environment=env.pin(configured, image),
    )
    plan = m.plans.decode(value)
    command = (
        "/usr/local/bin/python",
        "-I",
        "-B",
        "/opt/sdsctl-recording-host/supplemental_recording_service_operator.py",
        str(plan.root),
        plan.sha256,
    )
    values = dict(entry.split("=", 1) for entry in configured)
    values.update(HOME="/root", HOSTNAME=env.env.HOSTNAME)
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        env=values,
    )
    witness = None
    state = SimpleNamespace(
        reads=0, images=0, fault=None, when=1, command_fault=None, command_reads=0
    )

    def identity(pid, cid):
        assert (pid, cid) == (child.pid, CID)
        return m.engine.dispatch.process.process_identity(
            pid,
            cid,
            Path(f"/proc/{pid}/stat").read_text(),
            f"0::/system.slice/docker-{cid}.scope\n",
        )

    monkeypatch.setattr(m.engine.dispatch.process, "read_identity", identity)
    original_open = builtins.open

    def open_file(path, *args, **kwargs):
        if path == f"/proc/{child.pid}/cmdline":
            state.command_reads += 1
            raw = b"\0".join(part.encode() for part in command) + b"\0"
            return io.BytesIO(state.command_fault if state.command_fault is not None else raw)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_file)
    try:
        witness = m.engine.dispatch.process.ProcessWitness(identity(child.pid, CID))
        mounts = [
            dict(
                Type="bind", Source=source, Destination=target, RW=writable, Propagation="rprivate"
            )
            for source, target, writable in (
                ("/var/run/docker.sock", "/var/run/docker.sock", False),
                ("/mnt/data", "/mnt/data", False),
                (str(plan.root), str(plan.root), True),
                ("/proc/1/net/udp", "/opt/sdsctl-host-udp/udp", False),
                ("/proc/1/net/udp6", "/opt/sdsctl-host-udp/udp6", False),
            )
        ]
        container = dict(
            Id=CID,
            Name="/sdsctl-recording-handoff-" + plan.case,
            Image=plan.helper.image,
            State=dict(
                Status="running",
                Running=True,
                Paused=False,
                Restarting=False,
                Dead=False,
                OOMKilled=False,
                Pid=child.pid,
                Error="",
                StartedAt="2026-09-24T00:00:00Z",
            ),
            Path=command[0],
            Args=list(command[1:]),
            Config=dict(
                User="0:0",
                Hostname=env.env.HOSTNAME,
                WorkingDir="/",
                Env=configured,
                Entrypoint=list(command),
                Cmd=None,
                Tty=False,
                OpenStdin=False,
                Volumes=None,
            ),
            HostConfig=dict(
                RestartPolicy=dict(Name="no", MaximumRetryCount=0),
                ReadonlyRootfs=True,
                Privileged=False,
                AutoRemove=False,
                NetworkMode="none",
                PidMode="host",
                CgroupnsMode="host",
                IpcMode="private",
                UTSMode="",
                UsernsMode="",
                Runtime="runc",
                PublishAllPorts=False,
                NanoCpus=1_000_000_000,
                Memory=512 * 1024 * 1024,
                MemorySwap=1024 * 1024 * 1024,
                PidsLimit=64,
                CapDrop=["ALL"],
                CapAdd=["DAC_READ_SEARCH", "SYS_PTRACE"],
                SecurityOpt=["no-new-privileges"],
                Mounts=[
                    dict(
                        Type="bind",
                        Source=item["Source"],
                        Target=item["Destination"],
                        ReadOnly=not item["RW"],
                    )
                    for item in mounts
                ],
            ),
            Mounts=mounts,
            GraphDriver=dict(
                Name="overlay2",
                Data=dict(ID=CID, MergedDir="/mnt/data/docker/overlay2/helperfixture/merged"),
            ),
        )
        docker = m.plans.ordinary.Docker()

        def inspect(self, name):
            assert self is docker and name == CID
            state.reads += 1
            result = copy.deepcopy(container)
            if state.reads == state.when and state.fault:
                state.fault(result)
            return result

        def inspect_image(self, selected):
            assert self is docker and selected == plan.helper.image
            state.images += 1
            return dict(Id=selected, Os="linux", Architecture="amd64", Config=dict(Env=image))

        monkeypatch.setattr(type(docker), "container", inspect)
        monkeypatch.setattr(type(docker), "image", inspect_image)
        actual_metadata = m.HelperQualification._metadata

        def metadata(self, deadline):
            root, stamp, values = actual_metadata(self, deadline)
            assert root == Path("/mnt/data/docker/overlay2/helperfixture/merged")
            return supervised.root, stamp, values

        monkeypatch.setattr(m.HelperQualification, "_metadata", metadata)
        args = dict(
            generation=m.plans.ordinary.generation(
                container, name=container["Name"][1:], image=plan.helper.image
            ),
            command=command,
            configuration_sha256=configuration_pin(container, plan.helper.environment),
            image_environment_sha256=m.runtime.environment(image),
            timezone=env.env.TIMEZONE,
            hostname=env.env.HOSTNAME,
            architecture="amd64",
        )
        state.plan, state.witness, state.docker = plan, witness, docker
        state.container, state.args, state.child, state.root = (
            container,
            args,
            child,
            supervised.root,
        )
        state.make = lambda **overrides: m.HelperQualification(
            plan, witness, docker, **(args | overrides)
        )
        state.obj = state.make()
        yield state
    finally:
        child.stdin.close()
        child.wait(timeout=3)
        if witness:
            witness.close()


def denied(obj):
    launch.denied(obj)
    assert obj.failed and obj.elapsed_seconds is None
    launch.denied(obj)


def test_fresh_full_helper_join_returns_no_authority_and_keeps_original_handle(helper, capsys):
    fd = helper.witness.fd
    assert helper.obj() is None
    assert 0 <= helper.obj.elapsed_seconds < 2
    assert (helper.reads, helper.images, helper.command_reads) == (2, 2, 2)
    assert helper.obj() is None
    assert (helper.reads, helper.images, helper.command_reads) == (4, 4, 4)
    assert helper.witness.fd == fd and not helper.witness.exited()
    assert capsys.readouterr() == ("", "")


def test_engine_omitted_false_mount_flag_is_only_valid_for_case(helper):
    mounts = helper.container["HostConfig"]["Mounts"]
    mounts[2].pop("ReadOnly")
    obj = helper.make(
        configuration_sha256=configuration_pin(helper.container, helper.plan.helper.environment)
    )
    assert obj() is None
    mounts[0].pop("ReadOnly")
    denied(
        helper.make(
            configuration_sha256=configuration_pin(helper.container, helper.plan.helper.environment)
        )
    )


def test_platform_label_disable_is_explicitly_pinned_not_implicitly_adopted(helper):
    helper.container["HostConfig"]["SecurityOpt"].append("label=disable")
    denied(helper.obj)
    assert (
        helper.make(
            configuration_sha256=configuration_pin(helper.container, helper.plan.helper.environment)
        )()
        is None
    )


def test_original_worker_choice_is_immutable(helper):
    assert helper.obj.runtime_workers == 1
    helper.obj.runtime_workers = 2
    denied(helper.obj)
    assert helper.reads == 0


def test_successful_check_is_not_cached_after_file_change(helper):
    assert helper.obj() is None
    (helper.root / m.HelperQualification.HELPER / "supplemental_recording_runtime.py").write_bytes(
        b"PRIVATE-CHANGED"
    )
    denied(helper.obj)
    assert helper.reads == 3


@pytest.mark.parametrize("fault", ["command", "environment", "image", "root"])
def test_after_read_changes_are_not_adopted(helper, monkeypatch, fault):
    actual = m.runtime.Layout.verify_supervised

    def change(self, *args, **kwargs):
        result = actual(self, *args, **kwargs)
        if fault == "command":
            helper.command_fault = b"PRIVATE\0"
        elif fault == "environment":
            helper.container["Config"]["Env"].append("LD_PRELOAD=PRIVATE")
        elif fault == "image":
            monkeypatch.setattr(type(helper.docker), "image", lambda *_: {})
        else:
            helper.container["GraphDriver"]["Data"]["MergedDir"] = (
                "/mnt/data/docker/overlay2/different/merged"
            )
        return result

    monkeypatch.setattr(m.runtime.Layout, "verify_supervised", change)
    denied(helper.obj)


@pytest.mark.parametrize(
    "key,value",
    [
        ("ReadonlyRootfs", False),
        ("Privileged", True),
        ("AutoRemove", True),
        ("NetworkMode", "host"),
        ("PidMode", ""),
        ("CgroupnsMode", "private"),
        ("IpcMode", "host"),
        ("UTSMode", "host"),
        ("UsernsMode", "private"),
        ("Runtime", "PRIVATE"),
        ("PublishAllPorts", True),
        ("NanoCpus", 2_000_000_000),
        ("Memory", 0),
        ("MemorySwap", -1),
        ("PidsLimit", 0),
        ("CapDrop", []),
        ("CapAdd", ["SYS_ADMIN"]),
        ("SecurityOpt", ["seccomp=unconfined"]),
        ("Binds", ["/:/host"]),
        ("VolumesFrom", ["other"]),
        ("Devices", [{}]),
        ("DeviceRequests", [{}]),
        ("DeviceCgroupRules", ["a *:* rwm"]),
        ("GroupAdd", ["root"]),
        ("ExtraHosts", ["host:1.2.3.4"]),
        ("Tmpfs", {"/tmp": "rw"}),
        ("Sysctls", {"key": "value"}),
        ("PortBindings", {"1/tcp": []}),
        ("StorageOpt", {"size": "1G"}),
    ],
)
@pytest.mark.parametrize("when", [1, 2])
def test_every_confinement_mutation_refuses_even_with_new_configuration_pin(
    helper, key, value, when
):
    # Validate the policy itself, not only the complete independent fingerprint.
    changed = copy.deepcopy(helper.container)
    changed["HostConfig"][key] = value
    obj = helper.make(
        configuration_sha256=configuration_pin(
            changed if when == 1 else helper.container, helper.plan.helper.environment
        )
    )
    helper.when = when
    helper.fault = lambda c: c["HostConfig"].update({key: value})
    denied(obj)
    assert helper.reads == when
    assert helper.witness.fd >= 0


@pytest.mark.parametrize("when", [1, 2])
@pytest.mark.parametrize(
    "fault",
    [
        lambda c: c.update(Id="8" * 64),
        lambda c: c.update(Name="/another-helper"),
        lambda c: c.update(Image="sha256:" + "8" * 64),
        lambda c: c["State"].update(StartedAt="2026-09-24T00:00:01Z"),
        lambda c: c["State"].update(Pid=1),
        lambda c: c["HostConfig"]["RestartPolicy"].update(Name="always"),
        lambda c: c["Config"].update(User="root"),
        lambda c: c["Config"].update(Hostname="other"),
        lambda c: c["Config"].update(WorkingDir="/tmp"),
        lambda c: c["Config"].update(Tty=True),
        lambda c: c["Config"].update(OpenStdin=True),
        lambda c: c["Config"].update(Volumes={"/extra": {}}),
        lambda c: c.update(Args=["PRIVATE"]),
        lambda c: c["Config"].update(Cmd=["PRIVATE"]),
        lambda c: c["Config"]["Env"].append("SUPERVISOR_TOKEN=PRIVATE"),
        lambda c: c["GraphDriver"].update(Name="other"),
        lambda c: c["GraphDriver"]["Data"].update(ID="8" * 64),
        lambda c: c["GraphDriver"]["Data"].update(MergedDir="/mnt/data/docker/overlay2/../merged"),
    ],
)
def test_identity_and_command_refuse_on_either_side(helper, when, fault):
    helper.when, helper.fault = when, fault
    denied(helper.obj)


@pytest.mark.parametrize("requested", [False, True])
@pytest.mark.parametrize(
    "fault", ["missing", "extra", "duplicate", "source", "rw", "null", "type", "shadow"]
)
def test_mounts_are_closed_and_only_case_is_writable(helper, requested, fault):
    c = helper.container
    mounts = c["HostConfig"]["Mounts"] if requested else c["Mounts"]
    destination, access = ("Target", "ReadOnly") if requested else ("Destination", "RW")
    if fault == "missing":
        mounts.pop()
    elif fault == "extra":
        mounts.append(copy.deepcopy(mounts[0]))
    elif fault == "duplicate":
        mounts[1] = copy.deepcopy(mounts[0])
    elif fault == "source":
        mounts[0]["Source"] = "/PRIVATE"
    elif fault == "rw":
        mounts[0][access] = not mounts[0][access]
    elif fault == "null":
        mounts[0][access] = None
    elif fault == "type":
        mounts[0]["Type"] = "volume"
    else:
        mounts[0][destination] = "/usr/local"
    obj = helper.make(configuration_sha256=configuration_pin(c, helper.plan.helper.environment))
    denied(obj)


@pytest.mark.parametrize(
    "path",
    [
        "opt/sdsctl-recording-host/supplemental_recording_runtime.py",
        "usr/local/lib/python3.14/site-packages/sds200/daemon_recording.py",
        "usr/local/lib/libpython3.14.so.1.0",
        "usr/share/zoneinfo/America/Denver",
    ],
)
def test_changed_source_product_interpreter_or_zone_refuses(helper, path):
    (helper.root / path).write_bytes(b"PRIVATE-CHANGED")
    denied(helper.obj)


def test_source_is_rechecked_after_runtime_read(helper, monkeypatch):
    original = m.runtime.Layout.verify_supervised

    def change(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        (
            helper.root
            / m.HelperQualification.HELPER
            / "supplemental_recording_service_operator.py"
        ).write_bytes(b"PRIVATE-CHANGED")
        return result

    monkeypatch.setattr(m.runtime.Layout, "verify_supervised", change)
    denied(helper.obj)


@pytest.mark.parametrize("raw", [b"", b"PRIVATE\0", b"x" * 8193])
def test_actual_process_command_is_not_replaced_by_engine_declaration(helper, raw):
    helper.command_fault = raw
    denied(helper.obj)


def test_unknown_engine_setting_is_bound_to_independent_pin(helper):
    helper.container["HostConfig"]["FutureSetting"] = "PRIVATE"
    denied(helper.obj)


@pytest.mark.parametrize(
    "key,value",
    [
        ("generation", "8" * 64),
        ("configuration_sha256", "8" * 64),
        ("image_environment_sha256", "8" * 64),
        ("timezone", "Etc/UTC"),
        ("hostname", "other"),
        ("architecture", "arm64"),
    ],
)
def test_expected_pins_are_not_learned_from_observation(helper, key, value):
    denied(helper.make(**{key: value}))


@pytest.mark.parametrize("workers", [None, True, 0, 3, 1.0, "1"])
def test_worker_choice_is_exact_and_not_adaptive(helper, workers):
    launch.denied(lambda: helper.make(runtime_workers=workers))
    assert helper.reads == 0


def test_original_witness_cannot_be_replaced_even_with_identical_pid(helper):
    replacement = m.engine.dispatch.process.ProcessWitness(helper.witness.identity)
    try:
        helper.obj.witness = replacement
        denied(helper.obj)
        assert helper.witness.fd >= 0 and replacement.fd >= 0
    finally:
        replacement.close()


def test_lost_handle_cannot_be_reacquired(helper):
    helper.witness.close()
    denied(helper.obj)


def test_exited_original_child_is_not_a_successful_helper(helper):
    helper.child.stdin.close()
    assert helper.child.wait(timeout=3) == 0
    denied(helper.obj)


def test_overrun_keeps_original_limit_and_poisoned_state(helper, monkeypatch):
    actual = m.runtime.Layout.verify_supervised

    def late(self, *args, **kwargs):
        result = actual(self, *args, **kwargs)
        helper.obj.plan.original_clock.check_later(m.plans.clock.read())
        # Test clock only, not a sleep or a relaxed implementation budget.
        old = time.monotonic
        current = old()
        monkeypatch.setattr(time, "monotonic", lambda: current + 3)
        return result

    monkeypatch.setattr(m.runtime.Layout, "verify_supervised", late)
    denied(helper.obj)
