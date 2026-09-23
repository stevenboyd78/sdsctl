"""Actual owned pidfds/files/clocks; explicit synthetic HAOS proc/path mapping.

These tests do NOT certify Docker root namespaces, init argv or installed mounts.
The fixture maps the fixed /mnt tree into a private root and maps a harmless
owned process to the expected container PID1 identity. No scanner/App is used.
"""

import builtins
import importlib.util
import io
import json
import os
import select
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_host_plan as plans
from . import test_supplemental_recording_namespace as namespaces  # noqa: F401

NAME = "supplemental_recording_idle_observer"
SPEC = importlib.util.spec_from_file_location(NAME, Path(plans.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


def denied(callback):
    with pytest.raises(m.UnconfirmedIdle) as caught:
        callback()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    original = m.host_plan.clock.read()
    supplied = plans.value()
    issued = original.boottime_ns / m.host_plan.clock.NS
    supplied.update(
        boot=original.boot,
        original_clock=asdict(original) | {"namespace": list(original.namespace)},
        deadlines=dict(
            issued_at=issued, ready_by=issued + 60, stop_by=issued + 300, recover_by=issued + 1500
        ),
    )
    plan = m.host_plan.decode(supplied)
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys;print('ready',flush=True);sys.stdin.read()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    witness = None
    objects = []
    try:
        # Do not bind a supposedly quiescent fixture during interpreter startup
        # I/O. This is only a bounded test-process barrier, not native readiness.
        poller = select.poll()
        poller.register(child.stdout.fileno(), select.POLLIN | select.POLLHUP)
        assert poller.poll(3000), "Owned fixture startup did not complete"
        assert child.stdout.readline() == b"ready\n"
        ticks = int(Path(f"/proc/{child.pid}/stat").read_text().rpartition(") ")[2].split()[19])
        identity = m.process.ProcessIdentity(child.pid, ticks, "a" * 64)

        def read_identity(pid, cid):
            assert (pid, cid) == (child.pid, identity.container_id)
            fields = Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()
            assert fields[0] in ("R", "S", "I"), f"Fixture process state: {fields[0]}"
            assert int(fields[19]) == ticks
            return identity

        monkeypatch.setattr(m.process, "read_identity", read_identity)
        witness = m.process.ProcessWitness(identity)
        domains = tuple(
            (info.st_dev, info.st_ino)
            for info in (os.stat(f"/proc/self/ns/{name}") for name in m.namespace.NAMESPACES)
        )
        actor = m.namespace.Actor(child.pid, 1, os.getpid(), ticks, identity.container_id, domains)
        monkeypatch.setattr(
            m.namespace, "read", lambda pid, cid: actor if read_identity(pid, cid) else None
        )
        argv = b"\0".join(part.encode() for part in plan.idle_argv) + b"\0"
        real_open, real_builtin = os.open, builtins.open

        def proc_open(path, *args, **kwargs):
            if path == f"/proc/{child.pid}/cmdline":
                return io.BytesIO(argv)
            return real_builtin(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", proc_open)
        root = tmp_path / "host"
        root.mkdir(mode=0o700)
        layout = next(item for item in plan.layouts if item.slug == m.host_plan.base.CANDIDATE)
        relative = (layout.data / plan.native_root.relative_to("/data") / "idle").relative_to("/")
        directory = root / relative
        directory.mkdir(parents=True, mode=0o700)
        for path in (directory, *directory.parents):
            if path.is_relative_to(root):
                path.chmod(0o700)
        lease = directory / "lease.json"
        lease.write_bytes(m.host_plan.base.encode(plan.lease))
        claim = directory / "consumed.json"
        claim.write_bytes(
            m.host_plan.base.encode(
                dict(
                    schema=1,
                    kind="finite-recording-idle-claim",
                    case=plan.case,
                    lease_sha256=plan.lease_sha256,
                    started_at=time.monotonic(),
                )
            )
        )
        lease.chmod(0o600)
        claim.chmod(0o600)
        monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
        monkeypatch.setattr(m, "ROOT_GID", os.getegid())

        def mapped_open(path, flags, *args, **kwargs):
            if path == "/" and "dir_fd" not in kwargs:
                return real_open(root, flags, *args, **kwargs)
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(m.os, "open", mapped_open)

        def observe(**kwargs):
            result = m.Idle(plan, witness, "d" * 64, **kwargs)
            objects.append(result)
            return result

        yield SimpleNamespace(
            plan=plan,
            child=child,
            witness=witness,
            actor=actor,
            root=root,
            directory=directory,
            lease=lease,
            claim=claim,
            observe=observe,
            identity=identity,
            proc_open=proc_open,
        )
    finally:
        for item in objects:
            item.close()
        if witness is not None:
            witness.close()
        child.stdin.close()
        child.wait(timeout=3)
        child.stdout.close()
        assert child.returncode == 0


def test_real_claim_read_is_never_daemon_health_or_recording_idle(prepared):
    before = {
        p: (p.read_bytes(), m.files.identity(p.stat())) for p in (prepared.lease, prepared.claim)
    }
    observer = prepared.observe()
    first, second = observer.initial, observer.read()
    assert first.native == second.native == m.host_plan.ordinary.NativeState("d" * 64, None, None)
    assert first.init == second.init == prepared.identity
    assert first.plan_sha256 == prepared.plan.sha256
    assert first.lease_sha256 == prepared.plan.lease_sha256
    assert first.claim_sha256 == m.hashlib.sha256(prepared.claim.read_bytes()).hexdigest()
    assert first.started_at <= time.monotonic()
    assert second.sampled_at >= first.sampled_at
    assert first.files_sha256 == second.files_sha256 and first.actor_sha256 == second.actor_sha256
    assert before == {p: (p.read_bytes(), m.files.identity(p.stat())) for p in before}
    assert not hasattr(observer, "start") and not hasattr(observer, "begin")
    duplicate = observer.pidfd
    observer.close()
    observer.close()
    with pytest.raises(OSError):
        os.fstat(duplicate)
    assert not prepared.witness.exited()  # Caller still owns its original handle.
    denied(observer.read)


@pytest.mark.parametrize(
    "field, value",
    [
        ("schema", True),
        ("schema", 2),
        ("kind", "PRIVATE"),
        ("case", "b" * 32),
        ("lease_sha256", "b" * 64),
        ("started_at", True),
        ("started_at", -1),
        ("started_at", float("nan")),
        ("started_at", float("inf")),
    ],
)
def test_invalid_claim_never_becomes_ready(prepared, field, value):
    claim = json.loads(prepared.claim.read_bytes())
    claim[field] = value
    prepared.claim.write_bytes(json.dumps(claim, sort_keys=True, separators=(",", ":")).encode())
    denied(prepared.observe)


@pytest.mark.parametrize(
    "fault",
    [
        "future",
        "before_lease",
        "duplicate",
        "extra",
        "missing",
        "whitespace",
        "oversize",
        "trailing",
        "empty",
    ],
)
def test_ambiguous_or_stale_claim_preserved(prepared, fault):
    raw = prepared.claim.read_bytes()
    value = json.loads(raw)
    if fault == "future":
        value["started_at"] = prepared.plan.lease["ready_by"] + 1
    elif fault == "before_lease":
        value["started_at"] = prepared.plan.lease["issued_at"] - 1
    elif fault == "extra":
        value["PRIVATE"] = "data"
    elif fault == "missing":
        del value["case"]
    else:
        raw = {
            "duplicate": b'{"schema":1,' + raw[1:],
            "whitespace": b" " + raw,
            "oversize": b"x" * 4097,
            "trailing": raw + b"{}",
            "empty": b"",
        }[fault]
    if fault in ("future", "before_lease", "extra", "missing"):
        raw = m.host_plan.base.encode(value)
    prepared.claim.write_bytes(raw)
    denied(prepared.observe)
    assert prepared.claim.read_bytes() == raw


@pytest.mark.parametrize("target", ["lease", "claim"])
@pytest.mark.parametrize("fault", ["mode", "symlink", "hardlink", "fifo", "content", "missing"])
def test_original_idle_files_are_regular_private_bounded(prepared, target, fault):
    path = getattr(prepared, target)
    if fault == "mode":
        path.chmod(0o640)
    elif fault == "content":
        path.write_bytes(b"PRIVATE")
    else:
        saved = prepared.root / "saved"
        path.rename(saved)
        if fault == "symlink":
            path.symlink_to(saved)
        elif fault == "hardlink":
            os.link(saved, path)
        elif fault == "fifo":
            os.mkfifo(path, 0o600)
    denied(prepared.observe)
    assert not prepared.witness.exited()


@pytest.mark.parametrize(
    "fault", ["extra", "directory_mode", "parent_mode", "symlink_parent", "wrong_uid", "wrong_gid"]
)
def test_namespace_or_ownership_not_adopted(prepared, monkeypatch, fault):
    if fault == "extra":
        (prepared.directory / "PRIVATE").write_bytes(b"preserve")
    elif fault == "directory_mode":
        prepared.directory.chmod(0o750)
    elif fault == "parent_mode":
        prepared.directory.parent.chmod(0o770)
    elif fault == "symlink_parent":
        saved = prepared.directory.with_name("saved")
        prepared.directory.rename(saved)
        prepared.directory.symlink_to(saved)
    else:
        monkeypatch.setattr(m, "ROOT_UID" if fault == "wrong_uid" else "ROOT_GID", os.geteuid() + 1)
    denied(prepared.observe)


@pytest.mark.parametrize(
    "fault",
    [
        "lease",
        "claim",
        "same_bytes_replaced",
        "same_bytes_rewritten",
        "directory_replaced",
        "extra",
        "new_sibling_directory",
    ],
)
def test_continuity_failure_poisoned_without_rebaselining(prepared, fault):
    observer = prepared.observe()
    if fault in ("lease", "claim"):
        getattr(prepared, fault).write_bytes(b"PRIVATE")
    elif fault.startswith("same_bytes"):
        raw = prepared.claim.read_bytes()
        if fault == "same_bytes_replaced":
            prepared.claim.rename(prepared.root / "retained_claim")
        prepared.claim.write_bytes(raw)
        prepared.claim.chmod(0o600)
    elif fault == "directory_replaced":
        prepared.directory.rename(prepared.directory.with_name("retained_idle"))
        prepared.directory.mkdir(mode=0o700)
    elif fault == "extra":
        (prepared.directory / "PRIVATE").write_bytes(b"preserve")
    else:
        (prepared.directory.parent / "late_child").mkdir(mode=0o700)
    denied(observer.read)
    assert observer.failed and observer.pidfd >= 0 and not observer.closed
    denied(observer.read)
    assert not prepared.witness.exited()


def test_original_process_exit_is_not_healthy_or_reconstructed(prepared):
    observer = prepared.observe()
    prepared.child.stdin.close()
    prepared.child.wait(timeout=3)
    assert prepared.witness.exited()
    denied(observer.read)
    denied(prepared.observe)


@pytest.mark.parametrize(
    "fault", ["ticks", "local_pid", "time_domain", "user_domain", "argv", "argv_extra"]
)
def test_process_identity_cannot_change_during_observation(prepared, monkeypatch, fault):
    observer = prepared.observe()
    actor = prepared.actor
    if fault == "ticks":
        actor = replace(actor, start_ticks=actor.start_ticks + 1)
    elif fault == "local_pid":
        actor = replace(actor, local_pid=2)
    elif fault.endswith("domain"):
        domains = list(actor.namespaces)
        domains[4 if fault == "time_domain" else 3] = (1, 123)
        actor = replace(actor, namespaces=tuple(domains))
    else:

        def altered(path, *args, **kwargs):
            result = prepared.proc_open(path, *args, **kwargs)
            if path == f"/proc/{prepared.child.pid}/cmdline":
                raw = result.read()
                return io.BytesIO(b"PRIVATE\0" if fault == "argv" else raw + b"--PRIVATE\0")
            return result

        monkeypatch.setattr(builtins, "open", altered)
    monkeypatch.setattr(m.namespace, "read", lambda *_: actor)
    denied(observer.read)


@pytest.mark.parametrize("fault", ["boot", "suspend", "deadline", "reverse", "namespace"])
def test_original_clock_and_deadlines_not_renewed(prepared, monkeypatch, fault):
    observer = prepared.observe()
    window = m.host_plan.clock.read()
    if fault == "boot":
        window = replace(window, boot="c" * 32)
    elif fault == "namespace":
        window = replace(window, namespace=(1, 123))
    elif fault == "suspend":
        window = replace(window, boottime_ns=window.boottime_ns + 10 * m.host_plan.clock.NS)
    elif fault == "reverse":
        window = prepared.plan.original_clock
    else:
        offset = 61 * m.host_plan.clock.NS
        window = replace(
            window,
            before_ns=window.before_ns + offset,
            after_ns=window.after_ns + offset,
            boottime_ns=window.boottime_ns + offset,
        )
    monkeypatch.setattr(m.host_plan.clock, "read", lambda: window)
    denied(observer.read)
    assert prepared.plan.deadlines.ready_by == observer.plan.deadlines.ready_by


def test_wrong_thread_poisoning_does_not_close_callers_witness(prepared):
    observer = prepared.observe()
    outcomes = []

    def wrong_thread():
        try:
            observer.read()
        except m.UnconfirmedIdle:
            outcomes.append("refused")

    task = Thread(target=wrong_thread)
    task.start()
    task.join(timeout=3)
    assert not task.is_alive() and outcomes == ["refused"] and observer.failed
    assert not prepared.witness.exited()
    denied(observer.read)


def test_partial_open_failure_closes_owned_descriptors(prepared, monkeypatch):
    real = m.os.fstat
    before = set(os.listdir("/proc/self/fd"))
    calls = []

    def broken(fd):
        calls.append(fd)
        if len(calls) == 2:
            raise OSError("PRIVATE")
        return real(fd)

    monkeypatch.setattr(m.os, "fstat", broken)
    denied(prepared.observe)
    assert set(os.listdir("/proc/self/fd")) == before
    assert not prepared.witness.exited()


def test_proc_start_ticks_compare_in_original_boottime_not_monotonic(monkeypatch):
    plan = m.host_plan.decode(plans.value())  # BOOTTIME30, MONOTONIC10.
    raw = m.host_plan.base.encode(
        dict(
            schema=1,
            kind="finite-recording-idle-claim",
            case=plan.case,
            lease_sha256=plan.lease_sha256,
            started_at=11,
        )
    )
    monkeypatch.setattr(m.os, "sysconf", lambda name: 100 if name == "SC_CLK_TCK" else None)
    assert m._claim(raw, plan, 12, 3050) == 11
    # Claim BOOTTIME31 cannot belong to a process starting at BOOTTIME31.1.
    with pytest.raises(m.UnconfirmedIdle):
        m._claim(raw, plan, 12, 3110)


@pytest.mark.parametrize("ticks", [0, -1, True, None, 100.0])
def test_unqualified_kernel_tick_rate_fails(prepared, monkeypatch, ticks):
    monkeypatch.setattr(m.os, "sysconf", lambda _: ticks)
    denied(prepared.observe)


def test_concurrent_call_does_not_release_someone_elses_lock(prepared):
    observer = prepared.observe()
    assert observer.lock.acquire(blocking=False)
    denied(observer.read)
    assert observer.lock.locked() and observer.failed
    observer.lock.release()


@pytest.mark.parametrize("fault", ["process_during_files", "files_during_process", "slow_result"])
def test_between_sample_changes_are_not_accepted(prepared, monkeypatch, fault):
    observer = prepared.observe()
    if fault == "process_during_files":
        original = observer._files

        def changed():
            result = original()
            prepared.child.stdin.close()
            prepared.child.wait(timeout=3)
            return result

        monkeypatch.setattr(observer, "_files", changed)
    elif fault == "files_during_process":
        original = observer._process
        calls = []

        def changed():
            result = original()
            calls.append(True)
            if len(calls) == 2:
                prepared.claim.write_bytes(b"PRIVATE")
            return result

        monkeypatch.setattr(observer, "_process", changed)
    else:
        monkeypatch.setattr(m, "MAX_SECONDS", 0)
    denied(observer.read)
    assert observer.failed


@pytest.mark.parametrize("index", [1, 2, 3])
def test_root_and_ancestor_descriptor_failure_no_leak(prepared, monkeypatch, index):
    real = m.os.fstat
    before, calls = set(os.listdir("/proc/self/fd")), []

    def changed(fd):
        calls.append(fd)
        if len(calls) == index:
            raise OSError("PRIVATE")
        return real(fd)

    monkeypatch.setattr(m.os, "fstat", changed)
    denied(prepared.observe)
    assert set(os.listdir("/proc/self/fd")) == before


def test_midflight_poison_never_returns_a_fresh_result(prepared, monkeypatch):
    observer = prepared.observe()
    original = observer._files

    def poisoned():
        result = original()
        observer.failed = True
        return result

    monkeypatch.setattr(observer, "_files", poisoned)
    denied(observer.read)


@pytest.mark.parametrize("field, value", [("host_pid", 42), ("container_id", "f" * 64)])
def test_actor_must_match_exact_retained_init(prepared, monkeypatch, field, value):
    observer = prepared.observe()
    monkeypatch.setattr(m.namespace, "read", lambda *_: replace(prepared.actor, **{field: value}))
    denied(observer.read)
