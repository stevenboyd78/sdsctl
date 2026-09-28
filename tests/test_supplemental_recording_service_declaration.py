"""Actual private declaration reads; temporary paths and synthetic App metadata."""

import fcntl
import importlib.util
import os
import sys
import time
from contextlib import suppress
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_recording_service_template as templates

NAME = "supplemental_recording_service_declaration"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(templates.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / "startup-input"
    root.mkdir(mode=0o700)
    template = m.codec.decode(templates.value())
    path = root / m.NAME
    path.write_bytes(template.raw)
    path.chmod(0o600)
    case_id = templates.value()["plan"]["case"]
    actual = m.declaration_root
    monkeypatch.setattr(
        m, "declaration_root", lambda case: root if case == case_id else actual(case)
    )
    return root, template


def denied(action):
    with pytest.raises(m.UnconfirmedDeclaration) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def fds():
    return set(os.listdir("/proc/self/fd"))


def test_fixed_input_directory_is_separate_from_empty_publication_case():
    template = m.codec.decode(templates.value())
    plan = template.preview(templates.clock())
    root = m.declaration_root(plan.case)
    assert root == Path("/mnt/data/sdsctl-recording-startup-" + plan.case)
    assert root.parent == plan.root.parent and root != plan.root
    assert not root.is_relative_to(plan.root)


def test_original_private_handles_bytes_and_template_retained_without_writes(case, monkeypatch):
    root, template = case
    before = fds()
    identities = {p.name: m.files.identity(p.stat()) for p in root.iterdir()}
    monkeypatch.setattr(m.codec.plans.clock, "read", lambda: pytest.fail("No clock capture"))
    monkeypatch.setattr(m.os, "write", lambda *_: pytest.fail("No write"))
    monkeypatch.setattr(m.os, "fsync", lambda *_: pytest.fail("No fsync"))
    with m.Declaration(root, template.sha256) as original:
        result = original.recheck()
        assert result.raw == template.raw and original.recheck() is result
        for fd in original.handles:
            assert fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
            assert fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        assert identities == {p.name: m.files.identity(p.stat()) for p in root.iterdir()}
    assert fds() == before and original.closed
    original.close()
    denied(original.recheck)


@pytest.mark.parametrize("which", ["anchor", "directory", "file"])
@pytest.mark.parametrize("action", ["close", "recheck"])
def test_reused_descriptor_is_refused_without_closing_replacement(case, which, action):
    root, template = case
    before = fds()
    original = m.Declaration(root, template.sha256)
    target = getattr(original, which)
    replacement = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.dup2(replacement, target, inheritable=False)
    finally:
        os.close(replacement)
    try:
        denied(getattr(original, action))
        assert original.failed and original.closed
        assert os.read(target, 1) == b""
        original.close()
        assert os.read(target, 1) == b""
    finally:
        original.close()
        with suppress(OSError):
            os.close(target)  # Only the fixture owns the replacement.
    assert fds() == before


@pytest.mark.parametrize("action", ["close", "recheck"])
def test_same_type_replacement_with_different_inode_is_not_closed(case, tmp_path, action):
    root, template = case
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_bytes(b"retained")
    unrelated.chmod(0o600)
    before = fds()
    original = m.Declaration(root, template.sha256)
    target = original.file
    replacement = os.open(unrelated, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        os.dup2(replacement, target, inheritable=False)
    finally:
        os.close(replacement)
    try:
        denied(getattr(original, action))
        assert original.failed and original.closed
        original.close()
        assert os.pread(target, 8, 0) == b"retained"
    finally:
        original.close()
        with suppress(OSError):
            os.close(target)
    assert fds() == before


@pytest.mark.parametrize("action", ["close", "recheck"])
def test_foreign_handle_in_mutated_list_is_never_adopted_or_closed(case, action):
    root, template = case
    before = fds()
    original = m.Declaration(root, template.sha256)
    unrelated = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
    try:
        original.handles.append(unrelated)
        denied(getattr(original, action))
        assert original.failed and original.closed and original.handle_pins == {}
        original.close()
        assert os.read(unrelated, 1) == b""
    finally:
        original.close()
        os.close(unrelated)
    assert fds() == before


@pytest.mark.parametrize("which", ["anchor", "directory", "file"])
@pytest.mark.parametrize("fault", ["inheritable", "append", "blocking"])
def test_changed_original_descriptor_flags_refuse_without_repair(case, which, fault):
    root, template = case
    before = fds()
    original = m.Declaration(root, template.sha256)
    target = getattr(original, which)
    flags = fcntl.fcntl(target, fcntl.F_GETFL)
    if fault == "inheritable":
        os.set_inheritable(target, True)
    elif fault == "append":
        fcntl.fcntl(target, fcntl.F_SETFL, flags | os.O_APPEND)
    else:
        fcntl.fcntl(target, fcntl.F_SETFL, flags ^ os.O_NONBLOCK)
    try:
        denied(original.recheck)
        assert original.failed and original.closed and fds() == before
    finally:
        original.close()


def test_recheck_reads_original_file_without_redecoding_unchanged_bytes(case, monkeypatch):
    root, template = case
    reads = []
    actual = m.os.pread
    with m.Declaration(root, template.sha256) as original:

        def reading(fd, size, offset):
            reads.append(fd)
            return actual(fd, size, offset)

        monkeypatch.setattr(m.os, "pread", reading)
        monkeypatch.setattr(
            m.codec, "_read", lambda *_: pytest.fail("Unchanged template decoded again")
        )
        for _ in range(3):
            before = len(reads)
            assert original.recheck() is original.original_template
            assert len(reads) > before and set(reads) == {original.file}
        # A fresh file read remains mandatory; a cached digest is not evidence
        # of the current file, even after multiple successful observations.
        (root / m.NAME).write_bytes(template.raw.replace(b'"schema":1', b'"schema":2', 1))
        denied(original.recheck)
        assert original.failed and original.closed


@pytest.mark.parametrize("deadline", [True, "1", float("inf"), float("nan"), -1])
def test_invalid_or_expired_outer_deadline_refuses_before_read(case, monkeypatch, deadline):
    root, template = case
    with m.Declaration(root, template.sha256) as original:
        monkeypatch.setattr(m.os, "pread", lambda *_: pytest.fail("No read after invalid cutoff"))
        denied(lambda: original.recheck(deadline=deadline))
        assert original.failed and original.closed


def test_outer_deadline_only_narrows_original_complete_read_budget(case, monkeypatch):
    root, template = case
    with m.Declaration(root, template.sha256) as original:
        seen, context = [], m.Declaration._context

        def check(value, end):
            seen.append(end)
            context(value, end)

        monkeypatch.setattr(m.Declaration, "_context", check)
        end = time.monotonic() + 0.5
        assert original.recheck(deadline=end) is original.template
        assert seen and set(seen) == {end}
        seen.clear()
        began = time.monotonic()
        original.recheck(deadline=began + 100)
        assert seen and max(seen) < began + m.MAX_SECONDS + 0.1


@pytest.mark.parametrize("fault", ["valid", "invalid", "mutable", "retained-bytes", "pin", "root"])
def test_retained_template_mutation_cannot_bypass_initial_validation(case, fault):
    root, template = case
    original = m.Declaration(root, template.sha256)
    if fault == "valid":
        supplied = templates.value()
        supplied["plan"]["firmware"] = "Different"
        object.__setattr__(original.template, "raw", m.codec.decode(supplied).raw)
    elif fault == "invalid":
        object.__setattr__(original.template, "raw", b"PRIVATE")
    elif fault == "mutable":
        object.__setattr__(original.template, "raw", bytearray(template.raw))
    elif fault == "retained-bytes":
        original.raw = b"PRIVATE"
        object.__setattr__(original.template, "raw", original.raw)
    elif fault == "pin":
        original.expected = "0" * 64
    else:
        original.root = root.with_name("different-root")
    denied(original.recheck)
    assert original.failed and original.closed
    denied(original.recheck)


@pytest.mark.parametrize(
    "fault", ["hash", "noncanonical", "empty", "large", "clock", "missing", "other-case"]
)
def test_invalid_or_changed_inputs_refuse_and_release_handles(case, fault):
    root, template = case
    path, pin = root / m.NAME, template.sha256
    if fault == "hash":
        pin = "0" * 64
    elif fault == "noncanonical":
        path.write_bytes(template.raw + b"\n")
    elif fault == "empty":
        path.write_bytes(b"")
    elif fault == "large":
        path.write_bytes(b"x" * (m.codec.MAX_BYTES + 1))
    elif fault == "missing":
        path.unlink()
    else:
        value = templates.value()
        if fault == "clock":
            value["plan"]["original_clock"] = {}
        else:
            value["plan"]["case"] = "c" * 32
        path.write_bytes(m.codec.plans.base.encode(value))
    if path.exists() and fault != "hash":
        pin = m.codec.hashlib.sha256(path.read_bytes()).hexdigest()
    before = fds()
    denied(lambda: m.Declaration(root, pin))
    assert fds() == before


@pytest.mark.parametrize(
    "fault",
    [
        "symlink",
        "hardlink",
        "fifo",
        "directory",
        "extra",
        "file-mode",
        "directory-mode",
        "ancestor",
    ],
)
def test_aliased_or_nonprivate_declaration_never_accepted(case, fault):
    root, template = case
    path = root / m.NAME
    if fault in ("symlink", "hardlink", "fifo", "directory"):
        saved = root.parent / "preserved"
        path.rename(saved)
        if fault == "symlink":
            path.symlink_to(saved)
        elif fault == "hardlink":
            os.link(saved, path)
        elif fault == "fifo":
            os.mkfifo(path, 0o600)
        else:
            path.mkdir(mode=0o700)
    elif fault == "extra":
        (root / "unexpected").touch()
    elif fault == "file-mode":
        path.chmod(0o640)
    elif fault == "directory-mode":
        root.chmod(0o750)
    else:
        alias = root.parent / "alias"
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    before = fds()
    denied(lambda: m.Declaration(root, template.sha256))
    assert fds() == before


@pytest.mark.parametrize(
    "fault", ["bytes", "inode", "file-mode", "directory-mode", "ancestor", "entries", "template"]
)
def test_original_input_change_is_sticky_and_cannot_be_reopened(case, fault):
    root, template = case
    before = fds()
    original = m.Declaration(root, template.sha256)
    path = root / m.NAME
    if fault == "bytes":
        path.write_bytes(template.raw.replace(b'"schema":1', b'"schema":2', 1))
    elif fault == "inode":
        path.rename(root / "preserved")
        path.write_bytes(template.raw)
        path.chmod(0o600)
    elif fault == "file-mode":
        path.chmod(0o644)
    elif fault == "directory-mode":
        root.chmod(0o755)
    elif fault == "ancestor":
        root.rename(root.with_name("preserved-root"))
        root.mkdir(mode=0o700)
    elif fault == "entries":
        (root / "extra").touch()
    else:
        original.template = m.codec.load_bytes(template.raw, template.sha256)
    denied(original.recheck)
    assert original.closed and original.failed and fds() == before
    denied(original.recheck)


@pytest.mark.parametrize("when", ["open", "read", "decode"])
def test_mutation_during_initial_read_is_checked_after_boundary(case, monkeypatch, when):
    root, template = case
    path = root / m.NAME

    def mutate():
        path.chmod(0o644)

    if when == "open":
        actual = m.os.open

        def changed(name, *args, **kwargs):
            result = actual(name, *args, **kwargs)
            if name == m.NAME:
                mutate()
            return result

        monkeypatch.setattr(m.os, "open", changed)
    elif when == "read":
        actual = m.os.pread

        def changed(*args):
            result = actual(*args)
            mutate()
            return result

        monkeypatch.setattr(m.os, "pread", changed)
    else:
        actual = m.codec.load_bytes

        def changed(*args):
            result = actual(*args)
            mutate()
            return result

        monkeypatch.setattr(m.codec, "load_bytes", changed)
    before = fds()
    denied(lambda: m.Declaration(root, template.sha256))
    assert fds() == before


def test_partial_reads_join_but_never_extend_the_original_bound(case, monkeypatch):
    root, template = case
    actual = m.os.pread
    with m.Declaration(root, template.sha256) as original:
        monkeypatch.setattr(
            m.os, "pread", lambda fd, size, offset: actual(fd, min(size, 67), offset)
        )
        assert original.recheck().raw == template.raw
        now = [100.0]
        monkeypatch.setattr(m.time, "monotonic", lambda: now[0])

        def slow(fd, size, offset):
            now[0] += 1
            return actual(fd, min(size, 67), offset)

        monkeypatch.setattr(m.os, "pread", slow)
        denied(original.recheck)
        assert now[0] == 102


@pytest.mark.parametrize("when", ["construct", "recheck"])
@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt, SystemExit])
def test_failed_read_preserves_input_closes_handles_and_does_not_retry(
    case, monkeypatch, when, error
):
    root, template = case
    before = fds()
    original = None if when == "construct" else m.Declaration(root, template.sha256)

    def failed(*_):
        raise error("private-secret")

    with monkeypatch.context() as patch:
        patch.setattr(m.os, "pread", failed)

        def action():
            return m.Declaration(root, template.sha256) if original is None else original.recheck()

        if error is OSError:
            denied(action)
        else:
            with pytest.raises(error):
                action()
    assert fds() == before and (root / m.NAME).read_bytes() == template.raw
    if original is not None:
        denied(original.recheck)


@pytest.mark.parametrize("after", [False, True])
def test_uncertain_close_retires_each_original_descriptor_exactly_once(case, monkeypatch, after):
    root, template = case
    original = m.Declaration(root, template.sha256)
    held, closed = tuple(original.handles), []
    actual = m.os.close

    def uncertain(fd):
        closed.append(fd)
        if fd == original.file:
            if after:
                actual(fd)
            raise OSError("private-secret")
        actual(fd)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(m.os, "close", uncertain)
            denied(original.close)
            original.close()
        assert closed == list(reversed(held)) and original.handles == []
        assert original.closed and original.failed
    finally:
        if not after:
            actual(original.file)  # Test owns cleanup of the deliberately unclosed fake failure.


def test_foreign_thread_poisoning_does_not_close_owner_handles(case):
    root, template = case
    with m.Declaration(root, template.sha256) as original:
        held, errors = tuple(original.handles), []

        def foreign():
            for action in (original.recheck, original.close):
                try:
                    action()
                except BaseException as error:
                    errors.append(error)

        thread = Thread(target=foreign)
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive() and len(errors) == 2
        assert all(type(error) is m.UnconfirmedDeclaration for error in errors)
        assert original.failed and not original.closed and tuple(original.handles) == held
        for fd in held:
            os.fstat(fd)
        denied(original.recheck)


def test_invalid_arguments_never_open_anything(monkeypatch):
    monkeypatch.setattr(
        m.os, "open", lambda *_a, **_k: pytest.fail("Invalid arguments opened a file")
    )
    for root, pin in [
        ("/private", "0" * 64),
        (Path("/"), "0" * 64),
        (Path("relative"), "0" * 64),
        (Path("/a/../b"), "0" * 64),
        (Path("/private"), "private-secret"),
    ]:
        denied(lambda root=root, pin=pin: m.Declaration(root, pin))
