"""Fixed guardian in isolated staged Python, with real owned child/watchdog.

Only synthetic local peers are used. The staged interpreter/dependency selection
is a test fixture, not installed immutable-image or independent host approval.
"""

import ast
import hashlib
import json
import os
import select
import shutil
import signal
import subprocess
import sysconfig
import tomllib
import venv
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace

import pytest
import serial

from . import test_supplemental_recording_child as child
from . import test_supplemental_recording_source as sources

tree, configured, cached, prepared = child.tree, child.configured, child.cached, child.prepared
p = child.p

# This test driver is not part of the native bundle. It only plays the trusted
# fixture caller, pins locally staged bytes, and returns sanitized assertions.
DRIVER = r"""
import json, os, select, signal, sys, time
from pathlib import Path
native, runtime, plan, case, plan_pin, source_pin, fault = sys.argv[1:]
sys.path.insert(0, native)
import supplemental_recording_guardian as g
layout = g.source.Layout(Path(runtime), Path(native))
if fault == "source_pin": source_pin = "9" * 64
if fault == "plan_pin": plan_pin = "9" * 64
if fault == "runtime_origin": layout = g.source.Layout(Path(native).parent / "other", Path(native))
if fault == "source_drift":
    verify = g.source.Layout.verify
    count = 0
    def drift(self, pin):
        global count
        count += 1
        if count == 2:
            (self.native / "accept_supplemental_recording.py").write_bytes(b"PRIVATE_CHANGED")
        return verify(self, pin)
    g.source.Layout.verify = drift
if fault == "popen":
    def failed(*args, **kwargs): raise OSError("PRIVATE_POPEN")
    g.subprocess.Popen = failed
if fault == "fsync":
    def failed(*args): raise OSError("PRIVATE_FSYNC")
    g.os.fsync = failed
if fault == "arm":
    def failed(*args, **kwargs): raise ValueError("PRIVATE_ARM")
    g.watchdog.arm = failed
if fault == "watcher_death":
    arm = g.watchdog.arm
    def killed(*args, **kwargs):
        watch = arm(*args, **kwargs)
        signal.pidfd_send_signal(watch.fd, signal.SIGKILL)
        assert select.select([watch.fd], [], [], 2)[0]
        return watch
    g.watchdog.arm = killed
if fault in ("constructor", "peer_binding"):
    identity = g.control.returns._identity
    count = 0
    def stale(pid):
        global count
        value = identity(pid)
        if pid != os.getpid():
            count += 1
            if count == (2 if fault == "constructor" else 3):
                raise ValueError("PRIVATE_IDENTITY")
        return value
    g.control.returns._identity = stale
if fault == "gate_write":
    write, guardian_pid = g.os.write, os.getpid()
    def failed(fd, data):
        if os.getpid() == guardian_pid: raise OSError("PRIVATE_GATE_WRITE")
        return write(fd, data)
    g.os.write = failed
session = None
before = len(os.listdir("/proc/self/fd"))
extra = None
if fault == "extra_inherited":
    import socket
    extra = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    extra.set_inheritable(True)
def cancel(*args): raise RuntimeError("fixture cancelled")
signal.signal(signal.SIGTERM, cancel)
try:
    with g.open_session(layout, Path(plan), plan_sha256=plan_pin,
                        source_sha256=source_pin,
                        ready_by=time.monotonic() + 8) as session:
        session.receive_ready()
        print(json.dumps({"phase": "ready"}), flush=True)
        if fault == "ready_twice": session.receive_ready()
        if fault in ("native_exit", "watcher_frozen"):
            if fault == "watcher_frozen":
                signal.pidfd_send_signal(session.watch.fd, signal.SIGSTOP)
            signal.pidfd_send_signal(session.native_fd, signal.SIGKILL)
            exit = session.wait()
            assert exit.returncode == -9 and exit.watchdog.returncode == 0
            print(json.dumps({"phase": "exit", "code": -9}), flush=True)
        elif fault in ("ready_only", "extra_inherited"):
            session.channels.outgoing.close()
            exit = session.wait()
            assert exit.returncode == 70 and exit.watchdog.returncode == 0
        else:
            now = time.monotonic()
            plan = session.context.plan
            binding = g.control.returns.Binding(plan.stored, plan.generation,
                plan.projection_sha256, plan.source_sha256, now + 8,
                session.watch.deadline + 1 if fault == "begin_bad" else now + 20)
            session.begin(binding, intent_at=now, intent_sha256="1" * 64)
            if fault == "begin_twice":
                session.begin(binding, intent_at=now, intent_sha256="1" * 64)
            report = session.receive()
            assert json.loads(report.raw)["phase"] == "started"
            print(json.dumps({"phase": "started"}), flush=True)
            if fault == "watcher_running":
                signal.pidfd_send_signal(session.watch.fd, signal.SIGKILL)
                assert select.select([session.watch.fd], [], [], 2)[0]
            report = session.receive()
            body = json.loads(report.raw)["body"]
            assert body["artifact"]["samples"] == 1280
            exit = session.wait()
            assert exit.returncode == exit.watchdog.returncode == 0
            print(json.dumps({"phase": "completed", "samples": 1280}), flush=True)
    assert fault in ("record", "ready_only", "native_exit", "extra_inherited")
except g.UnconfirmedGuardian:
    assert fault not in ("record", "ready_only", "native_exit", "extra_inherited"), fault
    print(json.dumps({"phase": "refused"}), flush=True)
finally:
    if extra is not None: extra.close()
    if session is not None:
        assert session.closed and session.process.poll() is not None
    # All exact owned children (native + independent watcher) must be reaped.
    try:
        pid, status = os.waitpid(-1, os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        raise AssertionError("guardian left an unreaped child")
    assert len(os.listdir("/proc/self/fd")) == before
"""


@pytest.fixture
def staged(tmp_path):
    environment = tmp_path / "python"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    purelib = next(environment.glob("lib/python*/site-packages"))
    # Reuse dependencies already installed in this test environment, without
    # processing its editable-project .pth file or adding an ordinary CLI path.
    dependencies = dict.fromkeys(
        (sysconfig.get_path("purelib"), str(Path(serial.__file__).parents[1]))
    )
    (purelib / "fixture-dependencies.pth").write_text("\n".join(dependencies) + "\n")
    runtime = purelib / "sds200"
    shutil.copytree(sources.SCRIPTS.parent / "src" / "sds200", runtime)
    native = tmp_path / "native"
    native.mkdir()
    for name in sources.m.NATIVE_FILES:
        shutil.copyfile(sources.SCRIPTS / name, native / name)
    for root in (runtime, native):
        for path in root.rglob("*"):
            if path.is_file():
                path.chmod(0o644)
    layout = sources.m.Layout(runtime, native)
    return SimpleNamespace(
        python=environment / "bin" / "python", layout=layout, pin=layout.observe().sha256
    )


def run_driver(staged, prepared, case, fault, *, flags=("-I", "-B")):
    return subprocess.Popen(
        [
            str(staged.python),
            *flags,
            "-c",
            DRIVER,
            str(staged.layout.native),
            str(staged.layout.runtime),
            str(prepared.path),
            str(case),
            hashlib.sha256(p.encode(prepared.value)).hexdigest(),
            staged.pin,
            fault,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        env={"PATH": "/usr/bin:/bin"},
    )


def finish(driver):
    try:
        out, err = driver.communicate(timeout=25)
    except subprocess.TimeoutExpired:
        # Give the exact driver a chance to unwind and reap both owned children.
        driver.send_signal(signal.SIGTERM)
        out, err = driver.communicate(timeout=5)
        pytest.fail("isolated guardian exceeded fixture deadline: " + err.decode())
    assert driver.returncode == 0, err.decode()
    assert err == b""
    return [json.loads(line) for line in out.splitlines()]


def prepare_source_pin(staged, prepared):
    prepared.value["source_sha256"] = staged.pin
    prepared.path.write_bytes(p.encode(prepared.value))


@pytest.mark.parametrize(
    "fault",
    [
        "source_pin",
        "plan_pin",
        "runtime_origin",
        "existing_case",
        "fsync",
        "popen",
        "arm",
        "source_drift",
        "watcher_death",
        "constructor",
        "peer_binding",
        "gate_write",
        "isolated",
        "bytecode",
    ],
)
def test_refusals_consume_any_created_claim_and_reap_exact_children(
    staged, prepared, tmp_path, fault
):
    prepare_source_pin(staged, prepared)
    case = prepared.path.parent / "guardian"
    case.mkdir(mode=0o700)
    if fault == "existing_case":
        (case / "launch-claimed.json").write_bytes(b"PRIVATE_OLD_CLAIM")
    flags = ("-B",) if fault == "isolated" else (("-I",) if fault == "bytecode" else ("-I", "-B"))
    result = finish(run_driver(staged, prepared, case, fault, flags=flags))
    assert result == [{"phase": "refused"}]
    assert not list(prepared.spec.receipts.iterdir())
    assert p.Collector(prepared.stored).pristine().files.stage == "pristine"
    if fault in (
        "fsync",
        "popen",
        "arm",
        "source_drift",
        "watcher_death",
        "existing_case",
        "constructor",
        "peer_binding",
        "gate_write",
    ):
        assert (case / "launch-claimed.json").exists()
    else:
        assert not list(case.iterdir())


@pytest.mark.parametrize("fault", ["fsync", "popen", "arm"])
def test_partial_or_complete_claim_cannot_be_reused(staged, prepared, tmp_path, fault):
    prepare_source_pin(staged, prepared)
    case = prepared.path.parent / "guardian"
    case.mkdir(mode=0o700)
    assert finish(run_driver(staged, prepared, case, fault)) == [{"phase": "refused"}]
    marker = case / "launch-claimed.json"
    original, identity = marker.read_bytes(), p.identity(marker.stat())
    assert finish(run_driver(staged, prepared, case, fault)) == [{"phase": "refused"}]
    assert marker.read_bytes() == original and p.identity(marker.stat()) == identity
    assert not list(prepared.spec.receipts.iterdir())
    assert p.Collector(prepared.stored).pristine().files.stage == "pristine"


@pytest.mark.parametrize(
    "fault",
    [
        "record",
        "ready_only",
        "ready_twice",
        "begin_bad",
        "begin_twice",
        "watcher_running",
        "native_exit",
        "extra_inherited",
        "watcher_frozen",
    ],
)
def test_fixed_guardian_real_native_loopback(staged, prepared, monkeypatch, fault):
    with monkeypatch.context() as patch:
        patch.setattr(Thread, "start", lambda self: None)
        scanner = child.construction.LoopbackScanner()
    rtsp = child.RtspPeer()
    with TemporaryDirectory(prefix="finite-guardian-") as local:
        base = Path(local)
        sockets, receipts, baseline = (base / n for n in ("sockets", "receipts", "baseline"))
        case = prepared.path.parent / "guardian"
        for directory in (sockets, receipts, baseline, case):
            directory.mkdir(mode=0o700)
        target = f"udp://127.0.0.1:{scanner.socket.getsockname()[1]}"
        spec = child.plans.m.construction.Specification(
            "127.0.0.1",
            scanner.socket.getsockname()[1],
            rtsp.port,
            "127.0.0.1",
            0,
            sockets,
            receipts,
            "Version 1.26.01",
            1,
            2,
            10,
        )
        deployment = Path(prepared.value["profile"]["deployment"])
        configuration = Path(tomllib.loads(deployment.read_text())["profile_config"])
        configuration.write_text(
            configuration.read_text().replace(prepared.config.scanner_target, target)
        )
        mode = base / "writer-mode"
        mode.touch(mode=0o666)
        writer = p.monitor.Writer(os.geteuid(), os.getegid(), mode.stat().st_mode & 0o777)
        endpoint = child.plans.m.construction.NetworkAudioTransport(
            "127.0.0.1", rtsp_port=rtsp.port
        ).endpoint
        stored = p.save_baseline(
            baseline, prepared.tree.baseline, writer, hashlib.sha256(endpoint.encode()).hexdigest()
        )
        prepared.value["specification"] = asdict(spec) | {
            "sockets": str(sockets),
            "receipts": str(receipts),
        }
        prepared.value["baseline"] = dict(
            directory=str(baseline), contract=asdict(stored.contract), sha256=stored.manifest_sha256
        )
        prepared.value["profile"]["sha256"] = child.plans.m.cached.profile_files(
            deployment, prepared.tree.root
        )[0]
        prepare_source_pin(staged, prepared)
        scanner.thread.start()
        rtsp.thread.start()
        driver = None
        try:
            driver = run_driver(staged, prepared, case, fault)
            if fault == "record":
                for phase in ("ready", "started"):
                    assert select.select([driver.stdout], [], [], 12)[0]
                    assert json.loads(driver.stdout.readline()) == {"phase": phase}
                for i in range(8):
                    rtsp.packets.sendto(
                        child.construction.make_rtp(
                            bytes(range(160)), sequence=100 + i, timestamp=1000 + i * 160
                        ),
                        rtsp.target,
                    )
            result = finish(driver)
            expected = {
                "record": [{"phase": "completed", "samples": 1280}],
                "ready_only": [{"phase": "ready"}],
                "extra_inherited": [{"phase": "ready"}],
                "native_exit": [{"phase": "ready"}, {"phase": "exit", "code": -9}],
                "watcher_running": [{"phase": "ready"}, {"phase": "started"}, {"phase": "refused"}],
            }
            assert result == expected.get(fault, [{"phase": "ready"}, {"phase": "refused"}])
            assert not scanner.reads and not scanner.errors
            if fault in ("record", "ready_only", "extra_inherited"):
                assert rtsp.methods[-1] == "TEARDOWN"
            assert (case / "launch-claimed.json").is_file()
            if fault in (
                "ready_only",
                "ready_twice",
                "begin_bad",
                "native_exit",
                "extra_inherited",
                "watcher_frozen",
            ):
                assert not list(receipts.iterdir())
                assert p.Collector(stored).pristine().files.stage == "pristine"
        finally:
            if driver is not None and driver.poll() is None:
                driver.send_signal(signal.SIGTERM)
                driver.communicate(timeout=5)
            scanner.close()
            rtsp.close()


def test_claim_directory_is_not_a_caller_selected_namespace():
    parsed = ast.parse((sources.SCRIPTS / "supplemental_recording_guardian.py").read_text())
    function = next(
        n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name == "open_session"
    )
    assert [a.arg for a in function.args.args] == ["layout", "path"]
    assert [a.arg for a in function.args.kwonlyargs] == ["plan_sha256", "source_sha256", "ready_by"]
    assert function.args.vararg is None and function.args.kwarg is None
