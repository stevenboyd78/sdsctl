"""Real private files/descriptors; PID1/root/image/exec are synthetic, no HA/scanner.

This bridge is uninstalled and not admitted by current qualification profiles.
Tests do not assert native acceptance, a successful exec, or installed authority.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).parents[1] / "scripts/accept_supplemental_recording_app_idle.py"
SPEC = importlib.util.spec_from_file_location("app_idle_fixture", SOURCE)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
CASE = "ecbc3d906b574dc7a2fb121894c9b091"


def value():
    return dict(
        schema=1,
        kind="finite-recording-app-idle-launch-v1",
        case=CASE,
        plan_sha256="1" * 64,
        lease_sha256="2" * 64,
    )


class WouldExec(BaseException):
    pass


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    case = data / ("sdsctl-recording-" + CASE)
    case.mkdir(mode=0o700)
    root = case / "app-start"
    root.mkdir(mode=0o700)
    path = root / "launch.json"
    path.write_bytes(m._encode(value()))
    path.chmod(0o600)
    monkeypatch.setattr(m, "DATA", data)
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m, "ROOT_GID", os.getegid())
    monkeypatch.setattr(m, "_gate", lambda: None)
    attempts = []

    def execute(path, argv):
        attempts.append((path, argv))
        raise WouldExec

    monkeypatch.setattr(m.os, "execv", execute)
    return root, attempts


def test_closed_receipt_and_fixed_idle_exec_without_reading_lease(prepared):
    root, attempts = prepared
    assert not (root.parent / "idle").exists()
    with pytest.raises(WouldExec):
        m.run(["--case", CASE])
    assert attempts == [
        (
            m.PYTHON,
            [
                m.PYTHON,
                "-I",
                "-B",
                m.IDLE,
                "--lease",
                str(root.parent / "idle/lease.json"),
                "--lease-sha256",
                "2" * 64,
            ],
        )
    ]
    claim = json.loads((root / "consumed.json").read_bytes())
    assert claim["plan_sha256"] == "1" * 64 and claim["lease_sha256"] == "2" * 64
    assert claim["case"] == CASE and claim["kind"] == "finite-recording-app-idle-consumed-v1"
    assert stat_mode(root / "consumed.json") == 0o600
    with pytest.raises(ValueError):
        m.run(["--case", CASE])
    assert len(attempts) == 1  # No restart, even after a simulated successful exec.


def stat_mode(path):
    return path.stat().st_mode & 0o7777


@pytest.mark.parametrize(
    "change",
    [
        {"schema": True},
        {"schema": 2},
        {"kind": "other"},
        {"case": "0" * 32},
        {"plan_sha256": "x" * 64},
        {"lease_sha256": "A" * 64},
        {"command": ["sh"]},
        {"lease_sha256": None},
        {"case": CASE.upper()},
    ],
)
def test_receipt_refuses_unknown_or_wrong_fields(change):
    with pytest.raises(ValueError):
        m.decode(m._encode(value() | change), CASE)


@pytest.mark.parametrize(
    "raw", [b"", b"null", b"[]", b"NaN", b"x" * 2049, b'{"schema":1,"schema":1}', b"\xff"]
)
def test_receipt_refuses_bad_framing(raw):
    with pytest.raises(ValueError):
        m.decode(raw, CASE)


def test_receipt_rejects_noncanonical_bytes_and_missing_fields():
    with pytest.raises(ValueError):
        m.decode(json.dumps(value()).encode(), CASE)
    changed = value()
    del changed["plan_sha256"]
    with pytest.raises(ValueError):
        m.decode(m._encode(changed), CASE)


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--help"],
        ["--case", "0" * 32],
        ["--case", CASE, "--retry"],
        ["--case", "../private"],
        ("--case", CASE),
        ["--path", CASE],
    ],
)
def test_invalid_arguments_do_not_open_paths(prepared, monkeypatch, args):
    root, attempts = prepared
    monkeypatch.setattr(m.os, "open", lambda *a, **k: pytest.fail("Path opened"))
    with pytest.raises(ValueError):
        m.run(args)
    assert attempts == [] and not (root / "consumed.json").exists()


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "mode",
        "symlink",
        "hardlink",
        "extra",
        "directory",
        "parent_mode",
        "oversized",
        "claimed",
    ],
)
def test_bad_receipt_or_directory_cannot_exec(prepared, fault):
    root, attempts = prepared
    path = root / "launch.json"
    if fault == "missing":
        path.unlink()
    elif fault == "mode":
        path.chmod(0o644)
    elif fault == "symlink":
        target = root.parent / "original"
        path.rename(target)
        path.symlink_to(target)
    elif fault == "hardlink":
        os.link(path, root.parent / "linked")
    elif fault == "extra":
        (root / "unrecognized").mkdir()
    elif fault == "directory":
        path.unlink()
        path.mkdir()
    elif fault == "parent_mode":
        root.chmod(0o755)
    elif fault == "oversized":
        path.write_bytes(b"x" * 2049)
    else:
        (root / "consumed.json").write_bytes(b"old")
    with pytest.raises((OSError, ValueError)):
        m.run(["--case", CASE])
    assert attempts == []


@pytest.mark.parametrize("fault", ["write", "fsync", "exec", "return", "receipt_swap"])
def test_uncertain_claim_or_exec_preserves_residue_and_forbids_restart(
    prepared, monkeypatch, fault
):
    root, attempts = prepared
    with monkeypatch.context() as patch:
        if fault in ("write", "fsync"):
            patch.setattr(m.os, fault, lambda *a: (_ for _ in ()).throw(OSError("PRIVATE")))
        elif fault in ("exec", "return"):

            def failed_exec(*args):
                if fault == "exec":
                    raise OSError("PRIVATE")

            patch.setattr(m.os, "execv", failed_exec)
        else:
            original = m.os.fsync

            def swapped(fd):
                original(fd)
                path = root / "launch.json"
                replacement = root / "changed"
                replacement.write_bytes(path.read_bytes())
                replacement.chmod(0o600)
                replacement.replace(path)

            patch.setattr(m.os, "fsync", swapped)
        with pytest.raises((OSError, ValueError)):
            m.run(["--case", CASE])
    assert (root / "consumed.json").exists() and attempts == []
    with pytest.raises(ValueError):
        m.run(["--case", CASE])
    assert attempts == []


def test_original_receipt_deadline_expires_without_exec(prepared, monkeypatch):
    root, attempts = prepared
    ticks = iter([0, 3])
    monkeypatch.setattr(m.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ValueError):
        m.run(["--case", CASE])
    assert attempts == [] and not (root / "consumed.json").exists()


def test_real_uninstalled_command_refuses_sanitized_without_stdout():
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(SOURCE), "--case", CASE],
        capture_output=True,
        timeout=3,
        check=False,
    )
    assert result.returncode == 70 and result.stdout == b""
    assert result.stderr == (m.MESSAGE + "\n").encode()


def test_claim_cleanup_closes_all_created_descriptors(prepared):
    before = set(os.listdir("/proc/self/fd"))
    with pytest.raises(WouldExec):
        m.run(["--case", CASE])
    assert set(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("fault", ["flags", "pid", "uid", "gid", "cwd", "location"])
def test_installed_gate_requires_exact_runtime(monkeypatch, fault):
    monkeypatch.setattr(
        m, "sys", SimpleNamespace(flags=SimpleNamespace(isolated=1, dont_write_bytecode=1))
    )
    monkeypatch.setattr(
        m,
        "os",
        SimpleNamespace(getpid=lambda: 1, geteuid=lambda: 0, getegid=lambda: 0, getcwd=lambda: "/"),
    )
    monkeypatch.setattr(m, "__file__", str(m.ENTRYPOINT))
    m._gate()
    if fault == "flags":
        monkeypatch.setattr(m.sys, "flags", SimpleNamespace(isolated=0, dont_write_bytecode=1))
    elif fault == "location":
        monkeypatch.setattr(m, "__file__", "/PRIVATE/copied.py")
    else:
        method, value = {
            "pid": ("getpid", 2),
            "uid": ("geteuid", 10),
            "gid": ("getegid", 10),
            "cwd": ("getcwd", "/tmp"),
        }[fault]
        monkeypatch.setattr(m.os, method, lambda: value)
    with pytest.raises(ValueError):
        m._gate()


def test_partial_write_preserves_failed_claim(prepared, monkeypatch):
    root, attempts = prepared
    original = m.os.write
    monkeypatch.setattr(m.os, "write", lambda fd, raw: original(fd, raw[:1]))
    with pytest.raises(ValueError):
        m.run(["--case", CASE])
    assert (root / "consumed.json").read_bytes() == b"{" and attempts == []


def test_early_fstat_failure_closes_new_descriptor(prepared, monkeypatch):
    before = set(os.listdir("/proc/self/fd"))
    monkeypatch.setattr(m.os, "fstat", lambda fd: (_ for _ in ()).throw(OSError("PRIVATE")))
    with pytest.raises(OSError):
        m.run(["--case", CASE])
    assert set(os.listdir("/proc/self/fd")) == before


def test_failed_close_still_retires_other_original_descriptors(prepared, monkeypatch):
    before, closed = set(os.listdir("/proc/self/fd")), []
    original = m.os.close

    def failed_close(fd):
        original(fd)
        closed.append(fd)
        if len(closed) == 3:
            raise OSError("PRIVATE lost close acknowledgement")

    monkeypatch.setattr(m.os, "close", failed_close)
    # Third file-reader close fails before exec; outer finally must still close
    # its claim and every retained ancestor, without retrying the failed close.
    with pytest.raises(OSError):
        m.run(["--case", CASE])
    assert prepared[1] == [] and set(os.listdir("/proc/self/fd")) == before


def test_error_output_does_not_expose_private_failure(monkeypatch, capsys):
    monkeypatch.setattr(m, "run", lambda args: (_ for _ in ()).throw(OSError("PRIVATE")))
    assert m.main([]) == 70
    result = capsys.readouterr()
    assert result.out == "" and result.err == m.MESSAGE + "\n"
