"""Real private-file intake, no service launch, App mutation or live authority."""

import fcntl
import importlib.util
import os
import sys
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_host_plan as fixtures
from . import test_supplemental_recording_namespace as namespaces  # noqa: F401

NAME = "supplemental_recording_service_input"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(fixtures.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / "private-case"
    root.mkdir(mode=0o700)
    raw = m.plans.base.encode(fixtures.value())
    path = root / "plan.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    # The actual installed path is never created by a unit test. This explicit
    # local alias exercises all real no-follow reads, not installed provenance.
    monkeypatch.setattr(m.plans.Plan, "root", property(lambda _: root))
    return root, m.plans.load_bytes(raw, m.plans.base.checksum(fixtures.value()))


def denied(callback):
    with pytest.raises(m.UnconfirmedInput) as caught:
        callback()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


def descriptors():
    return set(os.listdir("/proc/self/fd"))


def test_exact_original_bytes_object_and_deadlines_are_retained_read_only(case):
    root, plan = case
    before = {p.name: m.files.identity(p.stat()) for p in root.iterdir()}
    start_fds = descriptors()
    with m.CasePlan(root, plan.sha256) as original:
        read = original.plan
        assert read == plan and original.recheck() is read
        assert read.raw == plan.raw and read.deadlines == plan.deadlines
        assert read.lease == plan.lease and read.bootstrap == plan.bootstrap
        held = [original._anchor, original._file, *(d[2] for d in original._directories)]
        for fd in held:
            assert fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
            assert fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        assert before == {p.name: m.files.identity(p.stat()) for p in root.iterdir()}
    assert descriptors() == start_fds
    original.close()
    denied(original.recheck)
    assert descriptors() == start_fds


def test_recheck_reads_file_every_time_and_allows_unrelated_journal_growth(case, monkeypatch):
    root, plan = case
    read = os.pread
    calls = []

    def observed(*args):
        calls.append(args)
        return read(*args)

    with m.CasePlan(root, plan.sha256) as original:
        monkeypatch.setattr(m.os, "pread", observed)
        same = original.recheck()
        count = len(calls)
        assert count > 0
        (root / "journal").mkdir(mode=0o700)
        (root / "journal" / "0000.json").write_bytes(b"not interpreted by intake")
        assert original.recheck() is same and len(calls) > count


@pytest.mark.parametrize(
    "fault", ["hash", "schema1", "schema2", "extra", "noncanonical", "empty", "large", "missing"]
)
def test_bad_plan_is_sanitized_and_all_open_descriptors_close(case, fault):
    root, plan = case
    path, expected = root / "plan.json", plan.sha256
    if fault == "hash":
        expected = "0" * 64
    elif fault in ("schema1", "schema2", "extra"):
        value = fixtures.value()
        value["schema" if fault != "extra" else "PRIVATE"] = (
            int(fault[-1]) if fault != "extra" else "SECRET"
        )
        path.write_bytes(m.plans.base.encode(value))
        expected = m.plans.base.checksum(value)
    elif fault == "noncanonical":
        path.write_bytes(plan.raw + b"\n")
        expected = m.plans.hashlib.sha256(path.read_bytes()).hexdigest()
    elif fault == "empty":
        path.write_bytes(b"")
    elif fault == "large":
        path.write_bytes(b"x" * (m.plans.MAX_BYTES + 1))
    elif fault == "missing":
        path.unlink()
    before = descriptors()
    denied(lambda: m.CasePlan(root, expected))
    assert descriptors() == before


@pytest.mark.parametrize("fault", ["symlink", "hardlink", "fifo", "directory", "socket"])
def test_nonregular_or_aliased_plan_refused_before_read(case, fault, monkeypatch):
    import socket

    root, plan = case
    path = root / "plan.json"
    old = root / "retained-plan"
    path.rename(old)
    listener = None
    try:
        if fault == "symlink":
            path.symlink_to(old)
        elif fault == "hardlink":
            os.link(old, path)
        elif fault == "fifo":
            os.mkfifo(path, 0o600)
        elif fault == "directory":
            path.mkdir(mode=0o700)
        else:
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(str(path))
        monkeypatch.setattr(m.os, "pread", lambda *_: pytest.fail("Unsafe plan was read"))
        before = descriptors()
        denied(lambda: m.CasePlan(root, plan.sha256))
        assert descriptors() == before
    finally:
        if listener is not None:
            listener.close()


@pytest.mark.parametrize(
    "target,mode",
    [("root", 0o755), ("root", 0o1700), ("file", 0o644), ("file", 0o660), ("file", 0o4600)],
)
def test_private_modes_required(case, target, mode):
    root, plan = case
    (root if target == "root" else root / "plan.json").chmod(mode)
    before = descriptors()
    denied(lambda: m.CasePlan(root, plan.sha256))
    assert descriptors() == before


@pytest.mark.parametrize(
    "fault",
    [
        "bytes",
        "identical_replacement",
        "file_mode",
        "root_mode",
        "case_replacement",
        "ancestor_replacement",
        "in_memory",
        "raw",
        "effective_uid",
        "effective_gid",
        "thread",
    ],
)
def test_retained_input_refuses_changes_and_never_rebases(case, fault, monkeypatch):
    root, plan = case
    before = descriptors()
    original = m.CasePlan(root, plan.sha256)
    if fault == "bytes":
        (root / "plan.json").write_bytes(b"PRIVATE CHANGED PLAN")
    elif fault == "identical_replacement":
        replacement = root / "replacement"
        replacement.write_bytes(plan.raw)
        replacement.chmod(0o600)
        replacement.replace(root / "plan.json")
    elif fault == "file_mode":
        (root / "plan.json").chmod(0o644)
    elif fault == "root_mode":
        root.chmod(0o755)
    elif fault == "case_replacement":
        root.rename(root.with_name("retained-case"))
        root.mkdir(mode=0o700)
    elif fault == "ancestor_replacement":
        moved = root.parent.with_name(root.parent.name + "-retained")
        root.parent.rename(moved)
        root.parent.mkdir()
        # Even moving the ORIGINAL case inode into a new ancestor must refuse.
        (moved / root.name).rename(root)
    elif fault == "in_memory":
        object.__setattr__(original._plan.deadlines, "stop_by", 9999)
    elif fault == "raw":
        object.__setattr__(original._plan, "raw", b"PRIVATE CHANGED PLAN")
    elif fault in ("effective_uid", "effective_gid"):
        function = "geteuid" if fault == "effective_uid" else "getegid"
        previous = getattr(os, function)()
        monkeypatch.setattr(m.os, function, lambda: previous + 1)
    else:
        result = []

        def foreign():
            try:
                original.recheck()
            except m.UnconfirmedInput:
                result.append("refused")

        worker = Thread(target=foreign)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive() and result == ["refused"]
    denied(original.recheck)
    assert original._failed and original._closed and descriptors() == before


def test_temporary_read_failure_is_sticky_even_when_file_is_unchanged(case, monkeypatch):
    root, plan = case
    original = m.CasePlan(root, plan.sha256)

    def interrupted(*_):
        raise OSError("PRIVATE MESSAGE")

    with monkeypatch.context() as changed:
        changed.setattr(m.os, "pread", interrupted)
        denied(original.recheck)
    assert (root / "plan.json").read_bytes() == plan.raw
    denied(original.recheck)


@pytest.mark.parametrize("field", ["st_uid", "st_gid"])
def test_foreign_file_owner_is_rejected_before_open(case, monkeypatch, field):
    root, plan = case
    real = os.stat

    def changed(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        if path == "plan.json":
            values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            values[field] += 1
            return SimpleNamespace(**values)
        return info

    monkeypatch.setattr(m.os, "stat", changed)
    before = descriptors()
    denied(lambda: m.CasePlan(root, plan.sha256))
    assert descriptors() == before


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("when", ["construct", "recheck"])
def test_external_interruption_is_not_swallowed_and_descriptors_close(
    case, monkeypatch, error, when
):
    root, plan = case
    before = descriptors()
    original = None if when == "construct" else m.CasePlan(root, plan.sha256)

    def interrupted(*_):
        raise error()

    monkeypatch.setattr(m.os, "pread", interrupted)
    with pytest.raises(error):
        m.CasePlan(root, plan.sha256) if original is None else original.recheck()
    assert descriptors() == before
    if original is not None:
        denied(original.recheck)


@pytest.mark.parametrize("when", ["open", "read", "decode"])
def test_changed_during_input_read_never_escapes(case, monkeypatch, when):
    root, plan = case
    path = root / "plan.json"
    before = descriptors()

    def mutate():
        replacement = root / "replacement"
        replacement.write_bytes(plan.raw)
        replacement.chmod(0o600)
        replacement.replace(path)

    if when == "open":
        real = os.open

        def wrapped(name, *args, **kwargs):
            if name == "plan.json":
                mutate()
            return real(name, *args, **kwargs)

        monkeypatch.setattr(m.os, "open", wrapped)
    elif when == "read":
        real = os.pread

        def wrapped(*args):
            result = real(*args)
            mutate()
            return result

        monkeypatch.setattr(m.os, "pread", wrapped)
    else:
        real = m.plans.load_bytes

        def wrapped(*args):
            result = real(*args)
            mutate()
            return result

        monkeypatch.setattr(m.plans, "load_bytes", wrapped)
    denied(lambda: m.CasePlan(root, plan.sha256))
    assert descriptors() == before


def test_short_reads_are_joined_without_extending_original_bound(case, monkeypatch):
    root, plan = case
    real = os.pread
    monkeypatch.setattr(m.os, "pread", lambda fd, size, offset: real(fd, min(size, 67), offset))
    with m.CasePlan(root, plan.sha256) as original:
        assert original.plan.raw == plan.raw


@pytest.mark.parametrize("when", ["construct", "recheck"])
def test_original_read_deadline_is_not_renewed_on_partial_reads(case, monkeypatch, when):
    root, plan = case
    original = None if when == "construct" else m.CasePlan(root, plan.sha256)
    real = os.pread
    tick = [100.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: tick[0])

    def slow(fd, size, offset):
        tick[0] += 1
        return real(fd, min(size, 67), offset)

    monkeypatch.setattr(m.os, "pread", slow)
    denied(lambda: m.CasePlan(root, plan.sha256) if original is None else original.recheck())
    assert tick[0] == 102


def test_wrong_case_path_and_symlink_ancestor_never_accepted(case, monkeypatch):
    root, plan = case
    monkeypatch.setattr(m.plans.Plan, "root", property(lambda _: root.with_name("different")))
    denied(lambda: m.CasePlan(root, plan.sha256))
    link = root.parent / "link"
    link.symlink_to(root, target_is_directory=True)
    monkeypatch.setattr(m.plans.Plan, "root", property(lambda _: link))
    denied(lambda: m.CasePlan(link, plan.sha256))


@pytest.mark.parametrize(
    "root,expected",
    [
        ("/mnt/data", "0" * 64),
        (Path("relative"), "0" * 64),
        (Path("/"), "0" * 64),
        (Path("/a/../b"), "0" * 64),
        (Path("/private"), None),
        (Path("/private"), "PRIVATE"),
    ],
)
def test_invalid_arguments_do_not_open_anything(monkeypatch, root, expected):
    monkeypatch.setattr(m.os, "open", lambda *_a, **_k: pytest.fail("Invalid input opened a file"))
    denied(lambda: m.CasePlan(root, expected))


@pytest.mark.parametrize("target", ["file", "directory", "anchor"])
@pytest.mark.parametrize("after", [False, True])
def test_uncertain_close_attempts_every_original_handle_once(case, monkeypatch, target, after):
    root, plan = case
    before = descriptors()
    original = m.CasePlan(root, plan.sha256)
    handles = [original._file, *(d[2] for d in reversed(original._directories)), original._anchor]
    bad = {
        "file": original._file,
        "directory": original._directories[-1][2],
        "anchor": original._anchor,
    }[target]
    actual, calls = os.close, []

    def uncertain(fd):
        calls.append(fd)
        if fd == bad:
            if after:
                actual(fd)
            raise OSError("PRIVATE close acknowledgment")
        actual(fd)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(m.os, "close", uncertain)
            denied(original.close)
            original.close()
        assert calls == handles and original._file == original._anchor == -1
        assert original._directories == [] and original._failed and original._closed
    finally:
        if not after:
            actual(bad)  # Only test cleanup knows this fake failed before close.
    assert descriptors() == before


@pytest.mark.parametrize("primary", [OSError, KeyboardInterrupt, SystemExit])
def test_cleanup_error_does_not_hide_original_interrupt_or_leak_remaining_handles(
    case, monkeypatch, primary
):
    root, plan = case
    before = descriptors()
    original = m.CasePlan(root, plan.sha256)
    actual, bad = os.close, original._file

    def uncertain(fd):
        actual(fd)
        if fd == bad:
            raise OSError("PRIVATE cleanup error")

    def failed(*_):
        raise primary("PRIVATE read error")

    with monkeypatch.context() as patch:
        patch.setattr(m.os, "pread", failed)
        patch.setattr(m.os, "close", uncertain)
        if primary is OSError:
            denied(original.recheck)
        else:
            with pytest.raises(primary):
                original.recheck()
    assert original._closed and descriptors() == before
    assert (root / "plan.json").read_bytes() == plan.raw
