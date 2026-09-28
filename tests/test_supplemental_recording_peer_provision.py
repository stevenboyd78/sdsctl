"""Actual exclusive files with synthetic reviewed pins; no installed qualification."""

import importlib.util
import os
import stat
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from . import test_supplemental_recording_peer_inputs as readers

NAME = "supplemental_recording_peer_provision"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(readers.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@pytest.fixture
def supplied(tmp_path, monkeypatch):
    parent = tmp_path / "data"
    parent.mkdir(mode=0o700)
    monkeypatch.setattr(m, "PARENT", parent)
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m.inputs, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(
        m.declarations,
        "declaration_root",
        lambda case: parent / ("sdsctl-recording-startup-" + case),
    )
    monkeypatch.setattr(
        m.inputs, "inputs_root", lambda case: parent / ("sdsctl-recording-peer-inputs-" + case)
    )
    template = m.declarations.codec.decode(readers.declaration_tests.templates.value())
    expected = m.codec.decode(readers.expectation_tests.value())
    return template.raw, template.sha256, expected.raw, expected.sha256


def denied(action):
    with pytest.raises(m.UnconfirmedPublication) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def fds():
    return set(os.listdir("/proc/self/fd"))


def targets(supplied):
    case = m.declarations.codec._read(supplied[0])["plan"]["case"]
    return m.declarations.declaration_root(case), m.inputs.inputs_root(case)


def test_exact_inputs_reopen_with_existing_retained_readers_without_creating_runtime(
    supplied, monkeypatch
):
    monkeypatch.setattr(m.codec.plans.clock, "read", lambda: pytest.fail("No live clock origin"))
    before = fds()
    result = m.publish(*supplied)
    assert fds() == before
    roots = targets(supplied)
    assert set(m.PARENT.iterdir()) == set(roots)
    assert (result.template_sha256, result.expectations_sha256) == (supplied[1], supplied[3])
    with pytest.raises(FrozenInstanceError):
        result.template_sha256 = "0" * 64
    with (
        m.declarations.Declaration(roots[0], supplied[1]) as original,
        closing(m.inputs.Inputs(original, roots[1], supplied[3])) as peer,
    ):
        assert original.recheck().raw == supplied[0]
        assert peer.recheck().raw == supplied[2]
        assert peer.template is original.template
    assert fds() == before
    for root, name, raw in zip(
        roots, (m.declarations.NAME, m.inputs.NAME), (supplied[0], supplied[2]), strict=True
    ):
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert set(root.iterdir()) == {root / name}
        path = root / name
        assert path.read_bytes() == raw and stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.stat().st_nlink == 1
    before_bytes = {p: p.read_bytes() for r in roots for p in r.iterdir()}
    denied(lambda: m.publish(*supplied))
    assert before_bytes == {p: p.read_bytes() for r in roots for p in r.iterdir()}


@pytest.mark.parametrize(
    "fault",
    ["template-pin", "expectation-pin", "template-bytes", "expectation-bytes", "join", "mutable"],
)
def test_whole_pair_validated_before_creating_any_directory(supplied, fault):
    args = list(supplied)
    if fault == "template-pin":
        args[1] = "0" * 64
    elif fault == "expectation-pin":
        args[3] = "0" * 64
    elif fault == "template-bytes":
        args[0] += b"\n"
    elif fault == "expectation-bytes":
        args[2] += b"\n"
    elif fault == "mutable":
        args[0] = bytearray(args[0])
    else:
        value = readers.expectation_tests.value()
        value["template_sha256"] = "0" * 64
        foreign = m.codec.decode(value)
        args[2:] = [foreign.raw, foreign.sha256]
    before = fds()
    denied(lambda: m.publish(*args))
    assert not list(m.PARENT.iterdir()) and fds() == before


@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("kind", ["empty-directory", "file", "symlink", "fifo"])
def test_either_existing_target_refuses_without_modifying_any_existing_state(supplied, which, kind):
    roots = targets(supplied)
    root = roots[which]
    if kind == "empty-directory":
        root.mkdir(mode=0o700)
    elif kind == "file":
        root.write_bytes(b"KEEP PRIVATE EVIDENCE")
    elif kind == "symlink":
        root.symlink_to(m.PARENT / "missing")
    else:
        os.mkfifo(root)
    before = m.files.identity(root.lstat()), fds()
    denied(lambda: m.publish(*supplied))
    assert m.files.identity(root.lstat()) == before[0] and fds() == before[1]
    assert not roots[1 - which].exists()


@pytest.mark.parametrize("mode", [0o777, 0o770, 0o1755, 0o2755, 0o4755])
def test_shared_or_special_mode_parent_is_not_an_installation_destination(supplied, mode):
    m.PARENT.chmod(mode)
    denied(lambda: m.publish(*supplied))
    assert not list(m.PARENT.iterdir())


def test_symlink_parent_is_not_followed_or_repaired(supplied):
    actual = m.PARENT.with_name("actual")
    m.PARENT.rename(actual)
    m.PARENT.symlink_to(actual, target_is_directory=True)
    before = fds()
    denied(lambda: m.publish(*supplied))
    assert not list(actual.iterdir()) and fds() == before


def test_an_existing_writable_case_is_not_reprovisioned(supplied):
    case = m.declarations.codec._read(supplied[0])["plan"]["case"]
    historical = m.PARENT / ("sdsctl-recording-handoff-" + case)
    historical.mkdir(mode=0o700)
    evidence = historical / "CLOSED_NO_RETRY"
    evidence.write_bytes(b"Keep historical evidence")
    denied(lambda: m.publish(*supplied))
    assert evidence.read_bytes() == b"Keep historical evidence"
    assert set(m.PARENT.iterdir()) == {historical}


@pytest.mark.parametrize("deadline", [True, float("nan"), float("inf"), 0, -1, "later"])
def test_invalid_or_expired_deadline_never_creates_inputs(supplied, deadline):
    denied(lambda: m.publish(*supplied, deadline=deadline))
    assert not list(m.PARENT.iterdir())


def test_short_writes_are_completed_and_fsynced_before_success(supplied, monkeypatch):
    write, fsync = m.os.write, m.os.fsync
    calls = []

    def short_write(fd, raw):
        calls.append("write")
        return write(fd, raw[:137])

    def sync(fd):
        calls.append("directory-sync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file-sync")
        fsync(fd)

    monkeypatch.setattr(m.os, "write", short_write)
    monkeypatch.setattr(m.os, "fsync", sync)
    m.publish(*supplied)
    assert calls.count("write") > 2
    assert calls.count("file-sync") == 2 and calls.count("directory-sync") == 3
    assert calls[-1] == "directory-sync"


@pytest.mark.parametrize("fault", ["write", "zero-write", "sync", "second-directory", "interrupt"])
def test_partial_artifacts_survive_and_the_same_case_cannot_be_retried(
    supplied, monkeypatch, fault
):
    original_write, original_mkdir = m.os.write, m.os.mkdir
    before = fds()

    def broken_write(fd, raw):
        original_write(fd, raw[:7])
        if fault == "interrupt":
            raise KeyboardInterrupt
        if fault == "zero-write":
            return 0
        raise OSError("PRIVATE FAILURE DETAILS")

    def broken_sync(fd):
        raise OSError("PRIVATE SYNC DETAILS")

    def broken_mkdir(name, *args, **kwargs):
        if name == targets(supplied)[1].name:
            raise OSError("PRIVATE DIRECTORY DETAILS")
        return original_mkdir(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        if fault in {"write", "zero-write", "interrupt"}:
            patch.setattr(m.os, "write", broken_write)
        elif fault == "sync":
            patch.setattr(m.os, "fsync", broken_sync)
        else:
            patch.setattr(m.os, "mkdir", broken_mkdir)
        if fault == "interrupt":
            with pytest.raises(KeyboardInterrupt):
                m.publish(*supplied)
        else:
            denied(lambda: m.publish(*supplied))
    assert fds() == before
    first = targets(supplied)[0]
    raw = (first / m.declarations.NAME).read_bytes()
    expected_raw = supplied[0][:7] if fault in {"write", "zero-write", "interrupt"} else supplied[0]
    assert raw == expected_raw
    denied(lambda: m.publish(*supplied))
    assert (first / m.declarations.NAME).read_bytes() == raw and fds() == before


def test_original_total_deadline_includes_all_writes_without_renewal(supplied, monkeypatch):
    current = time.monotonic()
    write = m.os.write
    before = fds()
    monkeypatch.setattr(m.time, "monotonic", lambda: current)

    def slow_write(fd, raw):
        nonlocal current
        count = write(fd, raw)
        current += 1.1
        return count

    monkeypatch.setattr(m.os, "write", slow_write)
    denied(lambda: m.publish(*supplied))
    assert fds() == before
    assert all(root.is_dir() for root in targets(supplied))


def test_earlier_enclosing_deadline_also_bounds_publication(supplied, monkeypatch):
    current = time.monotonic()
    write = m.os.write
    monkeypatch.setattr(m.time, "monotonic", lambda: current)

    def advance(fd, raw):
        nonlocal current
        result = write(fd, raw)
        current += 0.2
        return result

    monkeypatch.setattr(m.os, "write", advance)
    denied(lambda: m.publish(*supplied, deadline=current + 0.1))
    assert targets(supplied)[0].is_dir() and not targets(supplied)[1].exists()


def test_expiry_during_cleanup_does_not_acknowledge_a_late_publication(supplied, monkeypatch):
    current = time.monotonic()
    close = m.os.close
    monkeypatch.setattr(m.time, "monotonic", lambda: current)
    before = fds()

    def advance(fd):
        nonlocal current
        close(fd)
        current += 2.1

    monkeypatch.setattr(m.os, "close", advance)
    denied(lambda: m.publish(*supplied))
    assert fds() == before and all(root.is_dir() for root in targets(supplied))


def test_changed_output_descriptor_does_not_nominate_its_replacement_for_cleanup(
    supplied, monkeypatch
):
    replacement = None
    before = fds()

    def replace_output(fd, raw):
        nonlocal replacement
        foreign = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.dup2(foreign, fd, inheritable=False)
        finally:
            os.close(foreign)
        replacement = fd
        return len(raw)

    monkeypatch.setattr(m.os, "write", replace_output)
    try:
        denied(lambda: m.publish(*supplied))
        assert replacement is not None and os.read(replacement, 1) == b""
    finally:
        if replacement is not None:
            os.close(replacement)
    assert fds() == before


@pytest.mark.parametrize("fault", ["bytes", "extra-file", "hardlink", "path-replacement"])
def test_post_write_changes_refuse_a_success_receipt_and_preserve_evidence(
    supplied, monkeypatch, fault
):
    sync = m.os.fsync
    parent_pin = m.files.identity(m.PARENT.stat())[:2]
    changed = False

    def interfere(fd):
        nonlocal changed
        sync(fd)
        if changed or m.files.identity(os.fstat(fd))[:2] != parent_pin:
            return
        changed = True
        root = targets(supplied)[0]
        path = root / m.declarations.NAME
        if fault == "bytes":
            path.write_bytes(b"changed")
        elif fault == "extra-file":
            (root / "foreign").write_bytes(b"preserve")
        elif fault == "hardlink":
            os.link(path, m.PARENT / "foreign-hardlink")
        else:
            root.rename(m.PARENT / "preserved-original")
            root.mkdir(mode=0o700)

    monkeypatch.setattr(m.os, "fsync", interfere)
    before = fds()
    denied(lambda: m.publish(*supplied))
    assert changed and fds() == before
    assert all(root.is_dir() for root in targets(supplied))


def test_old_source_profiles_and_commands_do_not_select_the_provisioner():
    from . import test_supplemental_recording_peer_host_source as sources

    assert NAME not in sources.m.MODULES
    assert NAME not in sources.m.service.MODULES
    assert NAME not in sources.m.source.SERVICE_MODULES


def test_direct_execution_is_not_a_provisioning_or_launch_command(supplied):
    result = subprocess.run(
        [sys.executable, "-B", m.__file__], capture_output=True, timeout=10, check=False
    )
    assert result.returncode != 0 and not result.stdout
    assert result.stderr.strip() == (
        b"Explicit provisioning library only; no command or active launch enabled."
    )
    assert not list(m.PARENT.iterdir())
