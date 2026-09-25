"""Accepted original Startup/clock plus real private publication files.

Existing host/cache/Engine fixtures and host-data ancestor alias are synthetic;
this does not qualify an installed App, helper, scanner or restoration. No
historical case is replayed. Each pytest case has its own temporary directory.
"""

import importlib.util
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_recording_app_idle as bridge_tests
from . import test_supplemental_recording_startup_assembly as assembly

(
    layout,
    tree,
    routing,
    projection,
    binding,
    directory,
    prepared,
    joined,
    before_handoff,
    service_case,
) = (
    assembly.layout,
    assembly.tree,
    assembly.routing,
    assembly.projection,
    assembly.binding,
    assembly.directory,
    assembly.prepared,
    assembly.joined,
    assembly.before_handoff,
    assembly.service_case,
)
NAME = "supplemental_recording_app_publish"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(assembly.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@pytest.fixture
def publishing(service_case, tmp_path, monkeypatch):
    s = service_case
    s.data = tmp_path / "app-data"
    s.data.mkdir(mode=0o700)
    s.publisher_owner = assembly.accept(s)
    s.native = s.data / s.publisher_owner.original.plan.native_root.name
    s.before_publication = assembly.preserved(s)
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m, "ROOT_GID", os.getegid())
    monkeypatch.setattr(m, "_data", lambda plan: s.data)

    @contextmanager
    def private_alias(path, guard):
        # Only the fixed HAOS ancestry is synthetic; file/directory descriptors,
        # writes, ownership/mode, hardlinks, fsync and all startup guards are real.
        assert path == s.data
        fd = os.open(path, m.files.DIRECTORY)
        original = m.files.identity(os.fstat(fd))[:5]

        def unchanged():
            guard()
            assert m.files.identity(os.fstat(fd))[:5] == original
            assert m.files.identity(path.stat())[:5] == original

        try:
            unchanged()
            yield fd, unchanged
            unchanged()
        finally:
            os.close(fd)

    monkeypatch.setattr(m, "_chain", private_alias)
    yield s
    assert not s.publisher_owner.clock.closed
    os.fstat(s.publisher_owner.clock.fd)
    assert assembly.preserved(s) == s.before_publication


def denied(owner):
    with pytest.raises(m.UnconfirmedPublication) as caught:
        m.publish(owner)
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__
    assert not owner.lock.locked()


def test_original_lease_then_receipt_published_once_without_app_action(publishing):
    s, owner = publishing, publishing.publisher_owner
    plan = owner.original.plan
    original = plan.raw, plan.lease, plan.deadlines, owner.clock
    result = m.publish(owner)
    assert type(result) is m.Published
    assert result.plan_sha256 == plan.sha256 and result.lease_sha256 == plan.lease_sha256
    assert json.loads((s.native / "idle/lease.json").read_bytes()) == plan.lease
    receipt = json.loads((s.native / "app-start/launch.json").read_bytes())
    assert receipt == dict(
        schema=1,
        kind="finite-recording-app-idle-launch-v1",
        case=plan.case,
        plan_sha256=plan.sha256,
        lease_sha256=plan.lease_sha256,
    )
    assert sorted(path.name for path in s.native.iterdir()) == ["app-start", "idle"]
    assert len(result.file_identities) == 2
    assert original == (plan.raw, plan.lease, plan.deadlines, owner.clock)
    assert owner.app_idle_publication_used and not owner.service_used
    assert len(s.cached_calls) == 1  # Preparation only, publication issues no API call.
    snapshots = {str(p.relative_to(s.native)): p.read_bytes() for p in s.native.rglob("*.json")}
    denied(owner)
    assert snapshots == {
        str(p.relative_to(s.native)): p.read_bytes() for p in s.native.rglob("*.json")
    }
    assert not owner.closed and not owner.failed


def test_published_original_bytes_feed_the_real_bridge_parser(publishing, monkeypatch):
    s, bridge = publishing, bridge_tests.m
    result = m.publish(s.publisher_owner)
    monkeypatch.setattr(bridge, "DATA", s.data)
    monkeypatch.setattr(bridge, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(bridge, "ROOT_GID", os.getegid())
    monkeypatch.setattr(bridge, "_gate", lambda: None)
    commands = []

    def executing(executable, args):
        commands.append((executable, args))
        raise bridge_tests.WouldExec

    monkeypatch.setattr(bridge.os, "execv", executing)
    with pytest.raises(bridge_tests.WouldExec):
        bridge.run(["--case", s.publisher_owner.original.plan.case])
    assert len(commands) == 1 and commands[0][1][-1] == result.lease_sha256
    claim = json.loads((s.native / "app-start/consumed.json").read_bytes())
    assert claim["plan_sha256"] == result.plan_sha256
    assert claim["receipt_sha256"] == result.receipt_sha256
    denied(s.publisher_owner)


@pytest.mark.parametrize(
    "fault", ["not_accepted", "no_baseline", "assembled", "clock", "wrong_thread"]
)
def test_missing_original_custody_refuses_before_mutation(publishing, monkeypatch, fault):
    s, owner = publishing, publishing.publisher_owner
    original_clock = owner.clock
    if fault == "not_accepted":
        monkeypatch.setattr(owner, "accepted", False)
    elif fault == "no_baseline":
        monkeypatch.setattr(owner, "_service_inputs", None)
    elif fault == "assembled":
        monkeypatch.setattr(owner, "service_used", True)
    elif fault == "clock":
        monkeypatch.setattr(owner, "clock", object())
    if fault == "wrong_thread":
        errors = []

        def attempt():
            try:
                m.publish(owner)
            except BaseException as error:
                errors.append(error)

        thread = Thread(target=attempt)
        thread.start()
        thread.join(timeout=3)
        assert not thread.is_alive() and len(errors) == 1
        assert type(errors[0]) is m.UnconfirmedPublication
    else:
        denied(owner)
    assert not s.native.exists() and owner.app_idle_publication_used
    # Restore the deliberately substituted reference before fixture teardown;
    # the original clock itself was never closed or replaced by publication.
    if fault == "clock":
        monkeypatch.setattr(owner, "clock", original_clock)


@pytest.mark.parametrize(
    "fault",
    ["existing", "symlink", "data_mode", "write", "partial", "fsync", "replace", "directory_mode"],
)
def test_uncertain_output_is_preserved_and_never_republished(publishing, monkeypatch, fault):
    s, owner = publishing, publishing.publisher_owner
    with monkeypatch.context() as patch:
        if fault == "existing":
            s.native.mkdir(mode=0o700)
        elif fault == "symlink":
            target = s.data / "target"
            target.mkdir(mode=0o700)
            s.native.symlink_to(target)
        elif fault == "data_mode":
            s.data.chmod(0o777)
        elif fault in ("write", "fsync"):
            patch.setattr(m.os, fault, lambda *args: (_ for _ in ()).throw(OSError("PRIVATE")))
        elif fault == "partial":
            write = m.os.write
            patch.setattr(m.os, "write", lambda fd, raw: write(fd, raw[:1]))
        else:
            write = m.os.write

            def changed(fd, raw):
                result = write(fd, raw)
                if b"finite-recording-app-idle-launch-v1" in raw:
                    if fault == "replace":
                        path = s.native / "idle/lease.json"
                        replacement = path.with_name("replacement")
                        replacement.write_bytes(path.read_bytes())
                        replacement.chmod(0o600)
                        replacement.replace(path)
                    else:
                        (s.native / "idle").chmod(0o777)
                return result

            patch.setattr(m.os, "write", changed)
        denied(owner)
    assert not owner.closed and owner.app_idle_publication_used
    if fault != "data_mode":
        assert s.native.exists()
    if fault == "partial":
        assert (s.native / "idle/lease.json").read_bytes() == b"{"
        assert not (s.native / "app-start").exists()
    denied(owner)


def test_close_ack_failure_does_not_retry_owned_descriptor(publishing, monkeypatch):
    owner = publishing.publisher_owner
    before, closed = set(os.listdir("/proc/self/fd")), []
    actual_close = os.close

    def closing(fd):
        actual_close(fd)
        closed.append(fd)
        # Input readers close before the publication's private handles. Fail
        # only once on an owned output fd recognized by the kernel link target.

    actual_open = os.open
    outputs = []

    def opening(name, flags, *args, **kwargs):
        fd = actual_open(name, flags, *args, **kwargs)
        if name in ("lease.json", "launch.json") and flags & os.O_WRONLY:
            outputs.append(fd)
        return fd

    def uncertain(fd):
        closing(fd)
        if outputs and fd == outputs[-1]:
            raise OSError("PRIVATE close acknowledgement lost")

    monkeypatch.setattr(m.os, "open", opening)
    monkeypatch.setattr(m.os, "close", uncertain)
    denied(owner)
    assert len(outputs) == 2 and closed.count(outputs[-1]) == 1
    assert set(os.listdir("/proc/self/fd")) == before


def test_real_host_ancestry_refuses_writable_tmp_without_creating_anything(tmp_path):
    called = []
    before = set(os.listdir("/proc/self/fd"))
    with pytest.raises(m.UnconfirmedPublication), m._chain(tmp_path, lambda: called.append(1)):
        pytest.fail("Accepted world-writable ancestor")
    assert list(tmp_path.iterdir()) == []
    assert set(os.listdir("/proc/self/fd")) == before


def test_zero_window_refuses_before_creating_a_case(publishing, monkeypatch):
    monkeypatch.setattr(m, "MAX_SECONDS", 0)
    denied(publishing.publisher_owner)
    assert not publishing.native.exists()


def test_busy_data_directory_does_not_wait_or_publish(publishing):
    fd = os.open(publishing.data, m.files.DIRECTORY)
    try:
        m.fcntl.flock(fd, m.fcntl.LOCK_EX | m.fcntl.LOCK_NB)
        denied(publishing.publisher_owner)
    finally:
        os.close(fd)
    assert not publishing.native.exists()


def test_interruption_preserves_claim_and_borrowed_clock(publishing, monkeypatch):
    owner = publishing.publisher_owner
    before = set(os.listdir("/proc/self/fd"))
    monkeypatch.setattr(m.os, "write", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        m.publish(owner)
    assert (publishing.native / "idle/lease.json").is_file()
    assert not owner.lock.locked() and not owner.closed and not owner.clock.closed
    assert set(os.listdir("/proc/self/fd")) == before
    denied(owner)


def test_refuses_wrong_owner_without_reading_any_path(monkeypatch):
    monkeypatch.setattr(m.os, "open", lambda *a, **k: pytest.fail("Opened a path"))
    with pytest.raises(m.UnconfirmedPublication):
        m.publish(object())
