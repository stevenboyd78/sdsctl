"""One-shot local operator notices, crash/replay and hostile-file fixtures."""

import importlib.util
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from . import test_supplemental_handoff_host as host_tests
from . import test_supplemental_handoff_policy as policy_tests

NAME = "supplemental_handoff_operator"
if NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        NAME, Path(__file__).resolve().parents[1] / "scripts" / (NAME + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = module
    spec.loader.exec_module(module)
o, p = sys.modules[NAME], host_tests.p
directory, journal = policy_tests.directory, policy_tests.journal


@pytest.fixture
def inbox(journal, tmp_path):
    root = tmp_path / "operator"
    root.mkdir(mode=0o700)
    current = [policy_tests.BOOT, 11.0]
    opened = o.OperatorInbox(root, journal, lambda: tuple(current))
    try:
        yield opened, current
    finally:
        opened.close()


def message(inbox, kind="request", **changes):
    opened, current = inbox
    machine = opened.journal.machine
    return (
        o.notice(
            kind, machine.case_id, machine.boot_id, p.checksum(asdict(machine.baseline)), current[1]
        )
        | changes
    )


def test_no_notice_is_no_request(inbox):
    opened, _ = inbox
    assert opened.consume() is False
    assert opened.journal.machine.state.phase == "prepared"
    assert list(opened.path.iterdir()) == []


def test_atomic_request_consumed_once_and_preserved(inbox):
    opened, _ = inbox
    value = message(inbox)
    o.publish(opened.path, value)
    assert opened.consume() is True
    assert opened.journal.machine.state.phase == "requested"
    assert opened.consume() is False
    assert json.loads((opened.path / "request.json").read_bytes()) == value
    with pytest.raises(p.UnsafeHandoff):
        o.publish(opened.path, value)
    assert len(opened.journal.entries) == 2


def test_restart_uses_durable_consumption_receipt(inbox):
    opened, current = inbox
    o.publish(opened.path, message(inbox))
    assert opened.consume()
    path = opened.journal.path
    opened.journal.close()
    with p.Journal(path) as replay:
        reader = o.OperatorInbox(opened.path, replay, lambda: tuple(current))
        try:
            assert not reader.consume()
            assert len(replay.entries) == 2
        finally:
            reader.close()


def test_finish_consumed_once_only_during_running_candidate(inbox):
    opened, current = inbox
    o.publish(opened.path, message(inbox))
    assert opened.consume()
    policy_tests.observe(opened.journal, 12)
    policy_tests.observe(opened.journal, 13, policy_tests.NORMAL_STOPPED)
    current[1] = 13.5
    o.publish(opened.path, message(inbox, "finish"))
    assert opened.consume() is False
    policy_tests.observe(
        opened.journal, 14, policy_tests.NORMAL_STOPPED, policy_tests.CANDIDATE_RUNNING
    )
    current[1] = 15
    assert opened.consume() is True
    assert opened.journal.machine.state.finish_requested
    assert opened.consume() is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("case_id", "f" * 12 + "4" + "f" * 3 + "8" + "f" * 15),
        ("boot_id", "f" * 32),
        ("baseline", "f" * 64),
        ("issued_at", 9),
        ("issued_at", 12),
        ("schema", True),
        ("kind", "start"),
        ("extra", "PRIVATE_SENTINEL"),
    ],
)
def test_foreign_stale_or_unknown_notice_cannot_request(inbox, field, value):
    opened, _ = inbox
    (opened.path / "request.json").write_bytes(p.encode(message(inbox) | {field: value}))
    (opened.path / "request.json").chmod(0o600)
    with pytest.raises(p.UnsafeHandoff):
        opened.consume()
    assert opened.journal.machine.state.phase == "prepared"


@pytest.mark.parametrize("now", [42, 310])
def test_notice_and_request_window_expiry(inbox, now):
    opened, current = inbox
    o.publish(opened.path, message(inbox))
    current[1] = now
    with pytest.raises(p.UnsafeHandoff):
        opened.consume()
    assert opened.journal.machine.state.phase == "prepared"


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "fifo", "permissions", "oversized", "duplicate_json", "partial"]
)
def test_unsafe_input_files_never_consumed(inbox, kind):
    opened, _ = inbox
    path = opened.path / "request.json"
    o.publish(opened.path, message(inbox))
    saved = opened.path.parent / "saved.json"
    if kind == "symlink":
        path.rename(saved)
        path.symlink_to(saved)
    elif kind == "hardlink":
        saved.hardlink_to(path)
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif kind == "permissions":
        path.chmod(0o644)
    elif kind == "oversized":
        path.write_bytes(b"x" * 1025)
    elif kind == "duplicate_json":
        path.write_bytes(b'{"schema":1,"schema":1}')
    else:
        path.write_bytes(b'{"schema":')
    with pytest.raises((p.UnsafeHandoff, OSError)):
        opened.consume()
    assert opened.journal.machine.state.phase == "prepared"


def test_replaced_inbox_path_cannot_deliver_old_notice(inbox):
    opened, _ = inbox
    o.publish(opened.path, message(inbox))
    opened.path.rename(opened.path.with_name("old"))
    opened.path.mkdir(mode=0o700)
    with pytest.raises(p.UnsafeHandoff):
        opened.consume()


def test_unknown_pending_file_requires_review_not_cleanup(inbox):
    opened, _ = inbox
    pending = opened.path / ".pending-interrupted"
    pending.write_bytes(b"retain")
    with pytest.raises(p.UnsafeHandoff):
        o.publish(opened.path, message(inbox))
    with pytest.raises(p.UnsafeHandoff):
        opened.consume()
    assert pending.read_bytes() == b"retain"


def test_failed_journal_publication_does_not_delete_or_dispatch(inbox, monkeypatch):
    opened, _ = inbox
    o.publish(opened.path, message(inbox))

    def fail(_):
        raise OSError("disk full")

    monkeypatch.setattr(opened.journal, "append", fail)
    with pytest.raises(OSError):
        opened.consume()
    assert (opened.path / "request.json").is_file()
    assert opened.journal.machine.state.phase == "prepared"


def test_failed_atomic_publication_preserves_pending_file(inbox, monkeypatch):
    opened, _ = inbox

    def fail(*args, **kwargs):
        raise OSError("disk")

    monkeypatch.setattr(o.os, "link", fail)
    with pytest.raises(OSError):
        o.publish(opened.path, message(inbox))
    assert not (opened.path / "request.json").exists()
    assert len(list(opened.path.iterdir())) == 1
    with pytest.raises(p.UnsafeHandoff):
        opened.consume()


@pytest.mark.parametrize("change", ["chmod", "replace"])
def test_mutation_while_reading_refused(inbox, monkeypatch, change):
    opened, _ = inbox
    o.publish(opened.path, message(inbox))
    original = o.os.read

    def read(fd, size):
        raw = original(fd, size)
        path = opened.path / "request.json"
        if change == "chmod":
            path.chmod(0o644)
        else:
            path.unlink()
            path.write_bytes(raw)
            path.chmod(0o600)
        return raw

    monkeypatch.setattr(o.os, "read", read)
    with pytest.raises(p.UnsafeHandoff):
        opened.consume()


@pytest.mark.parametrize("path", [Path("/"), Path("relative"), Path("/tmp/../tmp")])
def test_no_broad_or_ambiguous_inbox_path(path):
    with pytest.raises(p.UnsafeHandoff):
        o.open_directory(path)
