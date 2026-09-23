"""Finite owned processes/files; PID1/root are explicit synthetic harness facts.

This does not qualify Docker PID1 teardown or installed source. The real CLI
must refuse an ordinary non-PID1 launch. No socket/daemon/scanner is opened.
"""

import ast
import hashlib
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).parents[1] / "scripts" / "accept_supplemental_recording_idle.py"
SPEC = importlib.util.spec_from_file_location("recording_idle_fixture", SOURCE)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
CASE, BOOT = "a" * 32, "b" * 32


def values(now=10, **changes):
    return {
        "schema": 1,
        "kind": "finite-recording-container-lease",
        "case": CASE,
        "boot": BOOT,
        "clock": "CLOCK_MONOTONIC",
        "issued_at": now,
        "ready_by": now + 30,
        "stop_by": now + 60,
    } | changes


def decode(value, **changes):
    raw = m._encode(value)
    return m.decode(
        raw,
        **(
            {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "now": 10,
                "boot": BOOT,
                "path": Path("/data") / ("sdsctl-recording-" + CASE) / "idle" / "lease.json",
            }
            | changes
        ),
    )


def test_closed_lease_retains_original_ready_and_stop_times():
    value = values()
    assert decode(value) == value
    assert decode(value, now=39.9) == value
    with pytest.raises(ValueError):
        decode(value, now=40)
    assert Path("/data") == m.DATA and m.ROOT_UID == m.ROOT_GID == 0


def test_actual_local_zero_clock_domain_is_read_only():
    fd = os.open("/proc/self/ns/time", os.O_RDONLY | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        m._clock_domain(fd)
        assert os.fstat(fd) == before
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"PRIVATE",
        b"x" * 257,
        b"monotonic 1 0\nboottime 0 0\n",
        b"monotonic 0 0\nboottime -1 0\n",
        b"monotonic 0 0\n",
        b"monotonic -0 0\nboottime 0 0\n",
        b"monotonic 0 0\nboottime 0 0\nextra\n",
    ],
)
def test_native_clock_offset_or_unknown_format_cannot_consume_lease(monkeypatch, raw):
    fd = os.open("/proc/self/ns/time", os.O_RDONLY | os.O_CLOEXEC)
    streams = []

    def opened(path, mode, *, buffering):
        assert path == "/proc/self/timens_offsets" and mode == "rb" and buffering == 0
        stream = io.BytesIO(raw)
        streams.append(stream)
        return stream

    monkeypatch.setattr(m, "open", opened, raising=False)
    try:
        with pytest.raises(ValueError):
            m._clock_domain(fd)
        assert all(stream.closed for stream in streams)
        assert os.fstat(fd)  # This check never takes ownership of its caller's fd.
    finally:
        os.close(fd)


@pytest.mark.parametrize("name", ["time", "time_for_children"])
@pytest.mark.parametrize("when", [1, 2])
def test_native_child_only_or_changed_time_namespace_refused(monkeypatch, name, when):
    fd = os.open("/proc/self/ns/time", os.O_RDONLY | os.O_CLOEXEC)
    original, calls = os.stat, []

    def observed(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == "/proc/self/ns/" + name:
            calls.append(path)
            if len(calls) == when:
                return SimpleNamespace(st_dev=result.st_dev, st_ino=result.st_ino + 1)
        return result

    monkeypatch.setattr(m.os, "stat", observed)
    try:
        with pytest.raises(ValueError):
            m._clock_domain(fd)
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    "change",
    [
        {"schema": True},
        {"schema": 2},
        {"kind": "PRIVATE"},
        {"case": "../PRIVATE"},
        {"boot": "c" * 32},
        {"clock": "CLOCK_BOOTTIME"},
        {"clock": "PRIVATE"},
        {"issued_at": True},
        {"issued_at": -1},
        {"issued_at": 11},
        {"ready_by": 10},
        {"ready_by": 611},
        {"stop_by": 40},
        {"stop_by": 791},
        {"stop_by": float("inf")},
        {"stop_by": float("nan")},
        {"PRIVATE": 1},
    ],
)
def test_invalid_lease_never_becomes_a_new_deadline(change):
    with pytest.raises(ValueError):
        decode(values(**change))


@pytest.mark.parametrize(
    "path",
    [
        "/data/lease.json",
        "/data/sdsctl-recording-" + CASE + "/lease.json",
        "/data/sdsctl-recording-" + CASE + "/idle/OTHER.json",
        "/data/sdsctl-recording-" + "c" * 32 + "/idle/lease.json",
        "/tmp/sdsctl-recording-" + CASE + "/idle/lease.json",
    ],
)
def test_lease_cannot_select_another_case_or_namespace(path):
    with pytest.raises(ValueError):
        decode(values(), path=Path(path))


@pytest.mark.parametrize("fault", ["hash", "duplicate", "format", "extra", "oversize"])
def test_lease_bytes_are_original_closed_and_bounded(fault):
    raw = m._encode(values())
    if fault == "duplicate":
        raw = b'{"schema":1,' + raw[1:]
    elif fault == "format":
        raw = b" " + raw
    elif fault == "extra":
        raw += b"{}"
    elif fault == "oversize":
        raw += b" " * m.MAX_BYTES
    pin = "0" * 64 if fault == "hash" else hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        m.decode(
            raw,
            sha256=pin,
            now=10,
            boot=BOOT,
            path=m.DATA / ("sdsctl-recording-" + CASE) / "idle" / "lease.json",
        )


@pytest.fixture
def lease(tmp_path):
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    case = data / ("sdsctl-recording-" + CASE)
    case.mkdir(mode=0o700)
    idle = case / "idle"
    idle.mkdir(mode=0o700)
    path = idle / "lease.json"
    now = time.monotonic()
    value = values(now, boot=m._boot())
    raw = m._encode(value)
    path.write_bytes(raw)
    path.chmod(0o600)
    return SimpleNamespace(
        data=data, path=path, value=value, raw=raw, pin=hashlib.sha256(raw).hexdigest()
    )


HARNESS = """
import importlib.util,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('idle_fixture',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
# Only test namespaces/credentials are substituted. The actual executable
# accepts no such options. All I/O, deadlines, signals and claim fsync are real.
m.DATA=Path(sys.argv[2]);m.ROOT_UID=os.geteuid();m.ROOT_GID=os.getegid()
m.os.getpid=lambda:1
if sys.argv[5]=='clock_refused':
    def refused(*args): raise ValueError('PRIVATE')
    m._clock_domain=refused
if sys.argv[5]=='lost':
    original=os.fsync
    def lost(fd):
        original(fd);raise OSError('PRIVATE')
    m.os.fsync=lost
sys.exit(m.main(['--lease',sys.argv[3],'--lease-sha256',sys.argv[4]]))
"""


def start(lease, *, lost=False, clock_refused=False):
    return subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            HARNESS,
            str(SOURCE),
            str(lease.data),
            str(lease.path),
            lease.pin,
            "clock_refused" if clock_refused else "lost" if lost else "normal",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def finished(child, expected):
    stdout, stderr = child.communicate(timeout=3)
    assert child.returncode == expected and stdout == b""
    assert stderr == ((m.MESSAGE + "\n").encode() if expected == 70 else b"")


def claimed(child, lease):
    until = time.monotonic() + 2
    path = lease.path.with_name("consumed.json")
    while time.monotonic() < until:
        if path.exists():
            try:
                claim = json.loads(path.read_bytes())
                assert claim["lease_sha256"] == lease.pin and claim["case"] == CASE
                return path
            except json.JSONDecodeError:
                pass  # Claim is not used as ready/ack evidence by the host.
        if child.poll() is not None:
            finished(child, 0)  # Explain unexpected fixed-error early exit.
            pytest.fail("Idle fixture exited before publishing its claim")
        time.sleep(0.005)
    pytest.fail("Idle fixture failed to publish its claim within test bound")


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_real_signal_stops_only_owned_idle_fixture_and_case_cannot_restart(lease, sig):
    child = start(lease)
    try:
        path = claimed(child, lease)
        assert child.poll() is None
        os.kill(child.pid, sig)  # Exact new owned Popen; not a system process.
        finished(child, 0)
        before = path.read_bytes()
        finished(start(lease), 70)
        assert path.read_bytes() == before and lease.path.read_bytes() == lease.raw
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=3)


def test_actual_original_lease_expiration_not_renewed(lease):
    now = time.monotonic()
    lease.value.update(issued_at=now, ready_by=now + 1, stop_by=now + 1.3)
    raw = m._encode(lease.value)
    lease.path.write_bytes(raw)
    lease.pin = hashlib.sha256(raw).hexdigest()
    child = start(lease)
    try:
        claimed(child, lease)
        finished(child, m.LEASE_EXPIRED)
        assert time.monotonic() - now < 2.8
        finished(start(lease), 70)
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=3)


def test_lost_claim_fsync_is_consumed_not_repaired(lease):
    finished(start(lease, lost=True), 70)
    path = lease.path.with_name("consumed.json")
    before = path.read_bytes()
    assert json.loads(before)["lease_sha256"] == lease.pin
    finished(start(lease), 70)
    assert path.read_bytes() == before


def test_native_clock_refusal_publishes_no_claim(lease):
    finished(start(lease, clock_refused=True), 70)
    assert not lease.path.with_name("consumed.json").exists()
    assert lease.path.read_bytes() == lease.raw


@pytest.mark.parametrize("fault", ["lease", "claim", "extra", "directory"])
def test_live_file_or_namespace_changes_end_fixture_without_reinitializing(lease, fault):
    child = start(lease)
    try:
        claim = claimed(child, lease)
        if fault == "lease":
            lease.path.write_bytes(b"PRIVATE")
        elif fault == "claim":
            claim.write_bytes(b"PRIVATE")
        elif fault == "extra":
            lease.path.with_name("PRIVATE").write_bytes(b"preserve")
        else:
            lease.path.parent.rename(lease.path.parent.with_name("PRIVATE_original"))
            lease.path.parent.mkdir(mode=0o700)
        finished(child, 70)
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=3)


@pytest.mark.parametrize(
    "fault", ["mode", "symlink", "hardlink", "fifo", "expired", "already_claimed"]
)
def test_unsafe_start_creates_no_new_claim(lease, fault):
    claim = lease.path.with_name("consumed.json")
    if fault == "mode":
        lease.path.chmod(0o640)
    elif fault in ("symlink", "hardlink", "fifo"):
        saved = lease.data / "PRIVATE_saved"
        lease.path.rename(saved)
        if fault == "symlink":
            lease.path.symlink_to(saved)
        elif fault == "hardlink":
            os.link(saved, lease.path)
        else:
            os.mkfifo(lease.path, 0o600)
    elif fault == "expired":
        lease.value.update(issued_at=1, ready_by=2, stop_by=3)
        raw = m._encode(lease.value)
        lease.path.write_bytes(raw)
        lease.pin = hashlib.sha256(raw).hexdigest()
    else:
        claim.write_bytes(b"PRIVATE")
        claim.chmod(0o600)
    finished(start(lease), 70)
    if fault == "already_claimed":
        assert claim.read_bytes() == b"PRIVATE"
    else:
        assert not claim.exists()


def test_actual_cli_refuses_non_pid1_and_imports_no_scanner_services(lease):
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            str(SOURCE),
            "--lease",
            str(lease.path),
            "--lease-sha256",
            lease.pin,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    finished(child, 70)
    assert not lease.path.with_name("consumed.json").exists()
    imported = set()
    for node in ast.walk(ast.parse(SOURCE.read_text())):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module.split(".")[0])
    assert imported <= {
        "__future__",
        "hashlib",
        "json",
        "math",
        "os",
        "re",
        "signal",
        "stat",
        "sys",
        "time",
        "pathlib",
        "threading",
        "uuid",
    }
