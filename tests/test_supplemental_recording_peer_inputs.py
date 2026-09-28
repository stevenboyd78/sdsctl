"""Private real files; synthetic template/pins, not installed input provenance."""

import fcntl
import hashlib
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_service_declaration as declaration_tests
from . import test_supplemental_recording_service_runtime_expectations as expectation_tests

NAME = "supplemental_recording_peer_inputs"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(declaration_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
assert m.declarations is declaration_tests.m and m.codec is expectation_tests.m
case = declaration_tests.case


@pytest.fixture
def provisioned(case, monkeypatch):
    startup_root, template = case
    root = startup_root.parent / "peer-inputs"
    root.mkdir(mode=0o700)
    expected = m.codec.decode(expectation_tests.value())
    path = root / m.NAME
    path.write_bytes(expected.raw)
    path.chmod(0o600)
    case_id = declaration_tests.templates.value()["plan"]["case"]
    actual = m.inputs_root
    monkeypatch.setattr(m, "inputs_root", lambda value: root if value == case_id else actual(value))
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    with m.declarations.Declaration(startup_root, template.sha256) as original:
        yield SimpleNamespace(root=root, path=path, original=original, expected=expected)


def construct(p, **kwargs):
    return m.Inputs(p.original, p.root, p.expected.sha256, **kwargs)


def denied(action):
    with pytest.raises(m.UnconfirmedInputs) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def fds():
    return set(os.listdir("/proc/self/fd"))


def test_fixed_read_only_sibling_is_neither_startup_nor_writable_case():
    template = declaration_tests.templates.m.decode(declaration_tests.templates.value())
    plan = template.preview(declaration_tests.templates.clock())
    path = m.inputs_root(plan.case)
    assert path == Path("/mnt/data/sdsctl-recording-peer-inputs-" + plan.case)
    assert path.parent == plan.root.parent and path != plan.root
    assert path != m.declarations.declaration_root(plan.case)


def test_original_file_and_template_retained_and_freshly_read_without_mutation(
    provisioned, monkeypatch
):
    p = provisioned
    before, metadata = fds(), m.files.identity(p.path.stat())
    monkeypatch.setattr(m.os, "write", lambda *_: pytest.fail("No input writes"))
    monkeypatch.setattr(m.os, "fsync", lambda *_: pytest.fail("No input fsync"))
    monkeypatch.setattr(m.codec.plans.clock, "read", lambda: pytest.fail("No clock origin"))
    owner = construct(p)
    try:
        expectation = owner.expectations
        assert expectation.raw == p.expected.raw and owner.template is p.original.template
        reads, pread = [], m.os.pread

        def observe(fd, size, offset):
            reads.append(fd)
            return pread(fd, size, offset)

        monkeypatch.setattr(m.os, "pread", observe)
        for _ in range(3):
            reads.clear()
            assert owner.recheck() is expectation
            assert set(reads) == {owner.file, p.original.file}
        assert m.files.identity(p.path.stat()) == metadata
    finally:
        owner.close()
    assert not p.original.closed and fds() == before
    owner.close()
    denied(owner.recheck)


@pytest.mark.parametrize(
    "fault",
    ["digest", "noncanonical", "empty", "large", "template", "writer", "mutable", "duplicate"],
)
def test_invalid_expectations_do_not_become_self_authorizing_inputs(provisioned, fault):
    p = provisioned
    pin = p.expected.sha256
    if fault == "digest":
        pin = "0" * 64
    elif fault == "noncanonical":
        p.path.write_bytes(p.expected.raw + b"\n")
    elif fault == "empty":
        p.path.write_bytes(b"")
    elif fault == "large":
        p.path.write_bytes(b"x" * (m.codec.MAX_BYTES + 1))
    elif fault == "duplicate":
        p.path.write_bytes(b'{"schema":1,' + p.expected.raw[1:])
    elif fault == "mutable":
        pin = bytearray(pin.encode())
    else:
        value = expectation_tests.value()
        if fault == "template":
            value["template_sha256"] = "0" * 64
        else:
            value["writer"]["runtime"]["image"] = "sha256:" + "0" * 64
        p.path.write_bytes(m.codec.decode(value).raw)
    if fault not in ("digest", "mutable"):
        pin = hashlib.sha256(p.path.read_bytes()).hexdigest()
    before = fds()
    denied(lambda: m.Inputs(p.original, p.root, pin))
    assert fds() == before and not p.original.closed


@pytest.mark.parametrize(
    "fault",
    [
        "symlink",
        "hardlink",
        "fifo",
        "directory",
        "missing",
        "extra",
        "file-mode",
        "root-mode",
        "ancestor",
        "case",
    ],
)
def test_aliased_nonprivate_or_foreign_case_inputs_refuse(provisioned, fault):
    p = provisioned
    root = p.root
    if fault in ("symlink", "hardlink", "fifo", "directory", "missing"):
        saved = p.root.parent / "preserved"
        p.path.rename(saved)
        if fault == "symlink":
            p.path.symlink_to(saved)
        elif fault == "hardlink":
            os.link(saved, p.path)
        elif fault == "fifo":
            os.mkfifo(p.path, 0o600)
        elif fault == "directory":
            p.path.mkdir(mode=0o700)
    elif fault == "extra":
        (root / "foreign").touch()
    elif fault == "file-mode":
        p.path.chmod(0o640)
    elif fault == "root-mode":
        root.chmod(0o750)
    elif fault == "case":
        root = root.with_name("different-case")
    else:
        saved = root.with_name("preserved-root")
        root.rename(saved)
        root.symlink_to(saved, target_is_directory=True)
    before = fds()
    denied(lambda: m.Inputs(p.original, root, p.expected.sha256))
    assert fds() == before and not p.original.closed


@pytest.mark.parametrize(
    "fault",
    [
        "bytes",
        "inode",
        "permissions",
        "parent",
        "entries",
        "template",
        "object",
        "raw",
        "pin",
        "flags",
        "inheritable",
    ],
)
def test_post_capture_changes_permanently_refuse_without_reopening(provisioned, monkeypatch, fault):
    p = provisioned
    before = fds()
    owner = construct(p)
    if fault == "bytes":
        p.path.write_bytes(b"PRIVATE")
    elif fault == "inode":
        p.path.rename(p.root.parent / "preserved")
        p.path.write_bytes(p.expected.raw)
        p.path.chmod(0o600)
    elif fault == "permissions":
        p.path.chmod(0o640)
    elif fault == "parent":
        p.root.rename(p.root.with_name("preserved-root"))
        p.root.mkdir(mode=0o700)
    elif fault == "entries":
        (p.root / "foreign").touch()
    elif fault == "template":
        owner.template = m.declarations.codec.load_bytes(p.original.raw, p.original.expected)
    elif fault == "object":
        owner.expectations = m.codec.load_bytes(p.expected.raw, p.expected.sha256)
    elif fault == "raw":
        owner.raw = b"PRIVATE"
    elif fault == "pin":
        owner.expected = "0" * 64
    elif fault == "flags":
        fcntl.fcntl(owner.file, fcntl.F_SETFL, os.O_APPEND)
    else:
        os.set_inheritable(owner.file, True)
    monkeypatch.setattr(m.os, "open", lambda *_args, **_kwargs: pytest.fail("No reopen"))
    denied(owner.recheck)
    assert owner.failed and owner.closed and not p.original.closed
    assert fds() == before
    denied(owner.recheck)


@pytest.mark.parametrize(
    ("target", "flag"),
    [("file", os.O_NOATIME), ("directory", os.O_NOATIME), ("directory", os.O_NONBLOCK)],
)
def test_complete_original_status_flags_are_retained(provisioned, target, flag):
    before = fds()
    owner = construct(provisioned)
    fd = getattr(owner, target)
    original = fcntl.fcntl(fd, fcntl.F_GETFL)
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, original ^ flag)
        assert fcntl.fcntl(fd, fcntl.F_GETFL) != original
        denied(owner.recheck)
        assert owner.failed and owner.closed and not provisioned.original.closed
        assert fds() == before
    finally:
        owner.close()


def test_original_startup_file_change_is_not_replaced(provisioned):
    p = provisioned
    owner = construct(p)
    (p.original.root / m.declarations.NAME).write_bytes(b"changed")
    denied(owner.recheck)
    assert p.original.failed and p.original.closed and owner.closed


@pytest.mark.parametrize("deadline", [True, "1", float("nan"), float("inf"), -1])
def test_invalid_or_expired_cutoffs_do_not_open_inputs(provisioned, monkeypatch, deadline):
    monkeypatch.setattr(m.os, "open", lambda *_args, **_kwargs: pytest.fail("No open"))
    denied(lambda: construct(provisioned, deadline=deadline))
    assert not provisioned.original.closed


def test_one_cutoff_reaches_every_nested_declaration_read(provisioned, monkeypatch):
    reads, check = [], m.declarations.Declaration.recheck

    def observed(owner, *, deadline=None):
        assert deadline is not None
        reads.append(deadline)
        return check(owner, deadline=deadline)

    monkeypatch.setattr(m.declarations.Declaration, "recheck", observed)
    end = time.monotonic() + 1
    owner = construct(provisioned, deadline=end)
    try:
        owner.recheck(deadline=end)
        assert len(reads) == 4 and set(reads) == {end}
    finally:
        owner.close()


def test_expiry_inside_original_declaration_stops_before_expectation_file_read(
    provisioned, monkeypatch
):
    owner = construct(provisioned)
    end = time.monotonic() + 1
    pread = m.os.pread
    now = time.monotonic()

    def read(fd, size, offset):
        assert fd != owner.file
        result = pread(fd, size, offset)
        monkeypatch.setattr(m.time, "monotonic", lambda: end)
        return result

    monkeypatch.setattr(m.time, "monotonic", lambda: now)
    monkeypatch.setattr(m.os, "pread", read)
    denied(lambda: owner.recheck(deadline=end))
    assert owner.closed and provisioned.original.closed


@pytest.mark.parametrize("target", ["file", "directory"])
def test_foreign_descriptor_reuse_is_not_closed(provisioned, target):
    owner = construct(provisioned)
    old = getattr(owner, target)
    foreign = os.open(provisioned.root.parent, m.files.DIRECTORY)
    os.close(old)
    os.dup2(foreign, old, inheritable=False)
    try:
        denied(owner.recheck)
        assert os.fstat(old).st_ino == os.fstat(foreign).st_ino
        assert owner.closed and not provisioned.original.closed
    finally:
        os.close(old)
        os.close(foreign)


@pytest.mark.parametrize("target", ["file", "directory"])
def test_even_equal_duplicate_descriptor_cannot_replace_original_owned_slot(provisioned, target):
    owner = construct(provisioned)
    duplicate = os.dup(getattr(owner, target))
    setattr(owner, target, duplicate)
    try:
        denied(owner.recheck)
        os.fstat(duplicate)  # Caller still owns this replacement descriptor.
        assert owner.closed and not provisioned.original.closed
    finally:
        os.close(duplicate)


def test_other_thread_cannot_close_original_descriptors(provisioned):
    owner = construct(provisioned)
    errors = []

    def close():
        try:
            owner.close()
        except m.UnconfirmedInputs as error:
            errors.append(error)

    thread = Thread(target=close)
    thread.start()
    thread.join()
    assert len(errors) == 1 and not owner.closed
    assert owner.recheck() is owner.expectations
    owner.close()


def test_interrupt_retires_owned_handles_without_closing_original_declaration(
    provisioned, monkeypatch
):
    before = fds()
    pread = m.os.pread
    original_fd = provisioned.original.file

    def interrupt(fd, size, offset):
        if fd != original_fd:
            raise KeyboardInterrupt
        return pread(fd, size, offset)

    monkeypatch.setattr(m.os, "pread", interrupt)
    with pytest.raises(KeyboardInterrupt):
        construct(provisioned)
    assert fds() == before and not provisioned.original.closed


def test_uninstalled_module_has_no_direct_launch_or_observed_profile_admission():
    result = subprocess.run([sys.executable, m.__file__], capture_output=True, timeout=10)
    assert result.returncode != 0 and b"no active launch enabled" in result.stderr
    assert NAME not in m.codec.source.MODULES
