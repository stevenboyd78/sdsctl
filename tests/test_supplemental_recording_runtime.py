"""Actual readonly fixture trees; root ownership is explicitly mapped locally."""

import importlib.util
import os
import sys
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event, Thread, current_thread

import pytest

from . import test_supplemental_handoff_process as process
from . import test_supplemental_recording_source as native

assert sys.modules["supplemental_handoff_process"] is process.w

NAME = "supplemental_recording_runtime"
SPEC = importlib.util.spec_from_file_location(NAME, native.SCRIPTS / (NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


def denied(callback):
    with pytest.raises(m.UnconfirmedRuntime) as caught:
        callback()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


@pytest.fixture
def image_umask():
    # Fixture parents must model image0755, not the developer account's0775.
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.fixture
def layout(tmp_path, monkeypatch, image_umask):
    # These filesystem tests run as the test account, not container root. Real
    # root-owned image qualification is a separate offline container operation.
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m, "ROOT_GID", os.getegid())
    root = tmp_path / "image"
    root.mkdir(mode=0o755)
    for path in m.TREES:
        (root / path).mkdir(parents=True, exist_ok=True)
    for path in (
        *m.FILES,
        "usr/local/bin/python3.14",
        "usr/local/lib/libpython3.14.so.1.0",
        "usr/local/lib/python3.14/os.py",
        "usr/local/lib/python3.14/site.py",
        "usr/local/lib/python3.14/encodings/__init__.py",
        "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
        "usr/share/ca-certificates/example.crt",
        "etc/ssl/openssl.cnf",
    ):
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"raise RuntimeError('PRIVATE_UNTRUSTED_RUNTIME')\n")
        file.chmod(0o644)
    (root / "usr/local/lib/python3.14/site-packages").mkdir()
    for path, target in (
        *m.ALIASES.items(),
        ("usr/local/bin/python", "python3"),
        ("usr/local/bin/python3", "python3.14"),
        ("usr/lib64/ld-linux-x86-64.so.2", "../lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"),
        ("etc/ssl/example.crt", "../../usr/share/ca-certificates/example.crt"),
    ):
        (root / path).symlink_to(target)
    return m.Layout(root)


@pytest.fixture
def env():
    return [
        "PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHON_VERSION=3.14.7",
        "PYTHON_SHA256=" + "a" * 64,
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONUNBUFFERED=1",
    ]


@pytest.mark.parametrize("workers", [1, 2])
def test_full_inventory_without_running_any_observed_code(layout, workers):
    layout = replace(layout, workers=workers)
    before = {p: m.identity(p.lstat()) for p in layout.root.rglob("*")}
    result = layout.observe()
    entries, count, total = layout._snapshot(m.time.monotonic() + m.MAX_SECONDS)
    assert result.sha256 == m.checksum(
        {"schema": 1, "kind": m.KIND, "entries": entries, "absent": m.ABSENT}
    )
    assert (result.entry_count, result.file_count, result.total_bytes) == (
        len(entries),
        count,
        total,
    )
    assert result == layout.verify(result.sha256) == layout.observe()
    assert before == {p: m.identity(p.lstat()) for p in layout.root.rglob("*")}
    assert entries["usr/local/bin/python"]["kind"] == "symlink"
    assert entries["usr/local/lib/python3.14/site-packages"]["kind"] == "directory"


@pytest.mark.parametrize(
    "path",
    [
        "usr/local/lib/python3.14/os.py",
        "usr/local/lib/libpython3.14.so.1.0",
        "usr/local/lib/python3.14/site-packages/package.dat",
        "usr/local/lib/python3.14/site-packages/__pycache__/test.cpython-314.pyc",
        "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
        "etc/ld.so.cache",
        "etc/ld.so.conf",
        "etc/ld.so.conf.d/libc.conf",
        "etc/ssl/openssl.cnf",
        "usr/share/ca-certificates/example.crt",
    ],
)
def test_bytecode_assets_dependencies_loader_and_tls_bytes_are_all_pinned(layout, path):
    target = layout.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"before")
    observed = layout.observe()
    target.write_bytes(b"changed")
    denied(lambda: layout.verify(observed.sha256))


@pytest.mark.parametrize("path", m.ABSENT)
@pytest.mark.parametrize("kind", ["file", "link", "directory"])
def test_forbidden_loader_venv_zip_locations_must_be_absent(layout, path, kind):
    target = layout.root / path
    if kind == "file":
        target.write_bytes(b"PRIVATE")
    elif kind == "link":
        target.symlink_to("missing")
    else:
        target.mkdir()
    denied(layout.observe)


@pytest.mark.parametrize(
    "path",
    [
        "sitecustomize.py",
        "site-packages/usercustomize.py",
        "site-packages/inject.pth",
        "site-packages/sitecustomize/__init__.py",
        "__pycache__/sitecustomize.cpython-314.pyc",
        "site-packages/usercustomize.pyc",
    ],
)
def test_python_startup_hooks_are_refused_even_when_inventoried(layout, path):
    target = layout.root / "usr/local/lib/python3.14" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"PRIVATE_STARTUP_HOOK")
    denied(layout.observe)


@pytest.mark.parametrize("character", ["\x01", "\t", "\n", "\r", "\x1f"])
@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "target"])
def test_inventory_names_and_link_targets_keep_control_character_rejection(layout, character, kind):
    root = layout.root / "usr/local"
    unsafe = "before" + character + "after"
    if kind == "file":
        (root / unsafe).write_bytes(b"untrusted")
    elif kind == "directory":
        (root / unsafe).mkdir()
    elif kind == "symlink":
        (root / unsafe).symlink_to("bin/python3.14")
    else:
        (root / "ordinary-link").symlink_to(unsafe)
    denied(layout.observe)


@pytest.mark.parametrize("name", ["a.b", "space name", "caf\u00e9", "\u6e2c\u8a66", "\x7f"])
def test_legal_non_hook_names_keep_identical_inventory_semantics(layout, name):
    relative = "usr/local/lib/python3.14/site-packages/" + name
    (layout.root / relative).write_bytes(b"unchanged bytes")
    observed = layout.observe()
    entries, _, _ = layout._snapshot(m.time.monotonic() + m.MAX_SECONDS)
    assert entries[relative]["kind"] == "file"
    assert entries[relative]["sha256"] == m.hashlib.sha256(b"unchanged bytes").hexdigest()
    assert observed == layout.verify(observed.sha256)


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "fifo",
        "hardlink",
        "group_write",
        "special_bits",
        "wrong_uid",
        "wrong_gid",
        "root_write",
        "parent_write",
        "selected_tree_link",
        "ancestor_link",
    ],
)
def test_unsafe_or_incomplete_runtime_is_refused(layout, monkeypatch, fault):
    target = layout.root / "usr/local/bin/python3.14"
    if fault in ("missing", "fifo", "hardlink"):
        target.unlink()
        if fault == "fifo":
            os.mkfifo(target)
        elif fault == "hardlink":
            target.hardlink_to(layout.root / "usr/local/lib/python3.14/os.py")
    elif fault in ("group_write", "special_bits"):
        target.chmod(0o664 if fault == "group_write" else 0o4644)
    elif fault in ("wrong_uid", "wrong_gid"):
        attr = "ROOT_UID" if fault == "wrong_uid" else "ROOT_GID"
        monkeypatch.setattr(m, attr, getattr(m, attr) + 1)
    elif fault in ("root_write", "parent_write"):
        (layout.root if fault == "root_write" else layout.root / "etc").chmod(0o777)
    else:
        tree = layout.root / ("usr/local" if fault == "selected_tree_link" else "usr")
        moved = tree.with_name("saved")
        tree.rename(moved)
        tree.symlink_to(moved)
    denied(layout.observe)


@pytest.mark.parametrize(
    "target",
    ["missing", "python", "../../../../PRIVATE", "/tmp/PRIVATE", "//usr/local/bin/python3.14"],
)
def test_links_cannot_escape_to_uninventoried_code_or_cycle(layout, target):
    path = layout.root / "usr/local/bin/python"
    path.unlink()
    path.symlink_to(target)
    denied(layout.observe)


def test_absolute_links_are_resolved_in_image_namespace_not_host(layout):
    path = layout.root / "usr/local/bin/python"
    path.unlink()
    path.symlink_to("/usr/local/bin/python3.14")
    entries, _, _ = layout._snapshot(m.time.monotonic() + m.MAX_SECONDS)
    assert m._resolve("usr/local/bin/python", entries) == "usr/local/bin/python3.14"
    assert layout.observe().sha256


@pytest.mark.parametrize("fault", ["bytes", "metadata", "directory", "target", "absent"])
@pytest.mark.parametrize("workers", [1, 2])
def test_second_full_observation_must_remain_identical(layout, monkeypatch, fault, workers):
    layout = replace(layout, workers=workers)
    original, calls = m.Layout._snapshot, []

    def changed(self, deadline):
        value = original(self, deadline)
        if not calls:
            path = self.root / "usr/local/bin/python3.14"
            if fault == "bytes":
                path.write_bytes(b"changed")
            elif fault == "metadata":
                path.chmod(0o600)
            elif fault == "directory":
                (self.root / "usr/local/empty").mkdir()
            elif fault == "absent":
                (self.root / m.ABSENT[0]).write_bytes(b"PRIVATE")
            else:
                link = self.root / "usr/local/bin/python"
                link.unlink()
                link.symlink_to("python3.14")
        calls.append(True)
        return value

    monkeypatch.setattr(m.Layout, "_snapshot", changed)
    denied(layout.observe)


def test_default_worker_and_explicit_parallel_selection_have_identical_evidence(layout):
    assert layout.workers == 1
    serial = layout.observe()
    assert replace(layout, workers=2).observe() == serial
    assert layout.observe() == serial


@pytest.mark.parametrize("workers", [False, True, None, 0, -1, 3, 10000, 1.0, "1"])
def test_invalid_worker_choice_refuses_before_filesystem_access(layout, monkeypatch, workers):
    opened = []
    original = m.os.open

    def opening(*args, **kwargs):
        opened.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(m.os, "open", opening)
    denied(replace(layout, workers=workers).observe)
    assert opened == []


def test_changed_file_during_read_is_refused(layout, monkeypatch):
    original, changed = m.os.read, []
    target = layout.root / "usr/local/bin/python3.14"
    expected = target.stat().st_ino

    def read(fd, size):
        value = original(fd, size)
        if not changed and os.fstat(fd).st_ino == expected:
            target.write_bytes(b"changed")
            changed.append(True)
        return value

    monkeypatch.setattr(m.os, "read", read)
    denied(layout.observe)
    assert changed == [True]


@pytest.mark.parametrize(
    "field", ["MAX_ENTRIES", "MAX_DEPTH", "MAX_FILE_BYTES", "MAX_TOTAL_BYTES", "MAX_SECONDS"]
)
def test_collection_is_bounded(layout, monkeypatch, field):
    monkeypatch.setattr(m, field, 0)
    denied(layout.observe)


@pytest.mark.parametrize("pin", [None, True, "PRIVATE", "a" * 64])
def test_pin_is_not_self_authenticating(layout, pin):
    denied(lambda: layout.verify(pin))


@pytest.mark.parametrize("path", ["/tmp", Path("relative"), Path("/tmp/../tmp"), Path("//tmp")])
def test_root_requires_canonical_absolute_path(layout, path):
    denied(replace(layout, root=path).observe)


@pytest.mark.parametrize(
    "path",
    [
        "usr/local/bin/python._pth",
        "usr/local/bin/python3.14._pth",
        "usr/local/lib/libpython3.14._pth",
        "usr/local/lib/python3.14/libpython3.14._pth",
    ],
)
def test_isolated_path_override_files_are_also_refused(layout, path):
    (layout.root / path).write_bytes(b"PRIVATE_PATH_OVERRIDE")
    denied(layout.observe)


@pytest.mark.parametrize("failure_at", [1, 2, 8, 20])
def test_fallible_descriptor_reads_do_not_leak_open_handles(layout, monkeypatch, failure_at):
    before = len(os.listdir("/proc/self/fd"))
    original, calls = m.os.fstat, []

    def failed(fd):
        calls.append(fd)
        if len(calls) == failure_at:
            raise OSError("PRIVATE_DESCRIPTOR_ERROR")
        return original(fd)

    monkeypatch.setattr(m.os, "fstat", failed)
    denied(layout.observe)
    assert len(calls) >= failure_at
    assert len(os.listdir("/proc/self/fd")) == before


def test_disjoint_roots_overlap_but_both_snapshots_keep_the_original_digest(layout, monkeypatch):
    layout = replace(layout, workers=2)
    expected = layout.observe()
    targets = {
        (layout.root / path).stat().st_ino
        for path in (
            "usr/local/bin/python3.14",
            "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
        )
    }
    original, barrier, reads = m.os.read, Barrier(2), []

    def read(fd, size):
        value = original(fd, size)
        inode = os.fstat(fd).st_ino
        if value and inode in targets:
            reads.append((inode, current_thread().name))
            barrier.wait(timeout=3)
        return value

    monkeypatch.setattr(m.os, "read", read)
    assert layout.verify(expected.sha256) == expected
    assert len(reads) == 4  # Both files were actually read in BOTH snapshots.
    assert {inode for inode, _ in reads} == targets
    assert len({worker for _, worker in reads}) == 2
    assert all(worker.startswith("runtime-inventory") for _, worker in reads)


@pytest.mark.parametrize("budget", ["bytes", "entries"])
def test_workers_share_budget_before_opening_files(layout, monkeypatch, budget):
    layout = replace(layout, workers=2)
    for name in ("tree-a", "tree-b"):
        (layout.root / name).mkdir()
        (layout.root / name / "data").write_bytes(b"1234")
    monkeypatch.setattr(m, "TREES", ("tree-a", "tree-b"))
    for field, value in (("FILES", ()), ("ALIASES", {}), ("ABSENT", ()), ("REQUIRED", ())):
        monkeypatch.setattr(m, field, value)
    monkeypatch.setattr(
        m, "MAX_TOTAL_BYTES" if budget == "bytes" else "MAX_ENTRIES", 6 if budget == "bytes" else 3
    )
    original, reads, barrier, synchronized = m.os.read, [], Barrier(2), set()
    stat_original = m.os.stat

    def stated(path, *args, **kwargs):
        result = stat_original(path, *args, **kwargs)
        if budget == "bytes" and path == "data" and result.st_ino not in synchronized:
            # Both workers have seen a four-byte file before reserving bytes.
            # The second reservation must fail before that file is opened.
            synchronized.add(result.st_ino)
            barrier.wait(timeout=3)
        return result

    def read(fd, size):
        value = original(fd, size)
        reads.append(len(value))
        return value

    monkeypatch.setattr(m.os, "stat", stated)
    monkeypatch.setattr(m.os, "read", read)
    denied(layout.observe)
    assert sum(reads) <= 4
    assert not barrier.broken
    assert budget != "bytes" or len(synchronized) == 2


@pytest.mark.parametrize("workers", [1, 2])
def test_pool_size_and_queued_work_are_fixed_by_roots_not_file_count(layout, monkeypatch, workers):
    layout = replace(layout, workers=workers)
    original, submitted, widths = m.ThreadPoolExecutor, [], []

    class Pool(original):
        def __init__(self, *, max_workers, thread_name_prefix):
            widths.append(max_workers)
            super().__init__(max_workers=max_workers, thread_name_prefix=thread_name_prefix)

        def submit(self, function, *args, **kwargs):
            submitted.append(args[-1])
            return super().submit(function, *args, **kwargs)

    for number in range(50):
        (layout.root / "usr/local" / f"file-{number}").write_bytes(b"one")
    monkeypatch.setattr(m, "ThreadPoolExecutor", Pool)
    assert layout.observe().file_count >= 50
    assert widths == [workers, workers]
    assert submitted == [*m.TREES, *m.FILES, *m.ALIASES] * 2


def test_failed_worker_is_joined_before_parent_descriptors_close(layout, monkeypatch):
    layout = replace(layout, workers=2)
    first = (layout.root / "usr/local/bin/python3.14").stat().st_ino
    second = (layout.root / "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2").stat().st_ino
    original = m.os.read
    entered, failed, release, finished = (Event() for _ in range(4))
    before = len(os.listdir("/proc/self/fd"))

    def read(fd, size):
        inode = os.fstat(fd).st_ino
        if inode == first:
            assert entered.wait(3)
            failed.set()
            raise OSError("PRIVATE_WORKER_FAILURE")
        if inode == second and not finished.is_set():
            entered.set()
            assert release.wait(3)
            value = original(fd, size)
            finished.set()
            return value
        return original(fd, size)

    def unblock():
        assert failed.wait(3)
        release.set()

    monkeypatch.setattr(m.os, "read", read)
    helper = Thread(target=unblock)
    helper.start()
    try:
        denied(layout.observe)
        assert finished.is_set()
        assert len(os.listdir("/proc/self/fd")) == before
    finally:
        release.set()
        helper.join(timeout=3)
    assert not helper.is_alive()


def test_environment_is_pinned_without_printing_values(env):
    original = list(env)
    result = m.environment(env)
    assert result == m.environment(list(reversed(env)))
    assert env == original
    env[1] = "PYTHON_VERSION=3.14.8"
    assert m.environment(env) != result


@pytest.mark.parametrize(
    "item",
    [
        "LD_PRELOAD=PRIVATE",
        "LD_LIBRARY_PATH=PRIVATE",
        "PYTHONPATH=PRIVATE",
        "PYTHONHOME=PRIVATE",
        "PYTHONINSPECT=1",
        "GCONV_PATH=PRIVATE",
        "LOCPATH=PRIVATE",
        "OPENSSL_CONF=PRIVATE",
        "SUPERVISOR_TOKEN=PRIVATE",
        "BASH_ENV=PRIVATE",
    ],
)
def test_unknown_environment_must_not_be_filtered(env, item):
    denied(lambda: m.environment([*env, item]))


@pytest.mark.parametrize(
    "item",
    [
        "PATH=",
        "PATH=/tmp:/usr/local/bin",
        "PATH=/usr/local/bin:",
        "PATH=/usr/local/bin:.",
        "PATH=/usr/local/bin:/usr/bin\nPRIVATE",
        "PYTHON_VERSION=3.13.7",
        "PYTHON_VERSION=3.14.07",
        "PYTHON_VERSION=3.14.7+injected",
        "PYTHON_SHA256=PRIVATE",
        "PYTHONDONTWRITEBYTECODE=0",
        "PYTHONUNBUFFERED=false",
    ],
)
def test_bad_environment_values_refused(env, item):
    key = item.split("=", 1)[0]
    values = [entry if not entry.startswith(key + "=") else item for entry in env]
    denied(lambda: m.environment(values))


@pytest.mark.parametrize("fault", ["duplicate", "missing", "object", "null", "invalid_item"])
def test_environment_has_closed_noncoerced_shape(env, fault):
    if fault == "duplicate":
        env[-1] = env[0]
    elif fault == "missing":
        env.pop()
    elif fault == "object":
        env = dict(item.split("=", 1) for item in env)
    elif fault == "null":
        env = None
    else:
        env[-1] = True
    denied(lambda: m.environment(env))
