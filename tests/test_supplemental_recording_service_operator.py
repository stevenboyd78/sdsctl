"""Fresh schema3 notices with real files/journal and synthetic host fixtures."""

import errno
import fcntl
import importlib.util
import os
import sys
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_recording_never_launched_host as cancellation
from . import test_supplemental_recording_service_input as inputs

NAME = "supplemental_recording_service_operator"
SPEC = importlib.util.spec_from_file_location(NAME, Path(inputs.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

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
    transfer,
    cancel,
) = (
    cancellation.layout,
    cancellation.tree,
    cancellation.routing,
    cancellation.projection,
    cancellation.binding,
    cancellation.directory,
    cancellation.prepared,
    cancellation.joined,
    cancellation.before_handoff,
    cancellation.transfer,
    cancellation.cancel,
)


@contextmanager
def opened(s):
    path = s.plan.root / "plan.json"
    path.write_bytes(s.plan.raw)
    path.chmod(0o600)
    (s.plan.root / "inbox").mkdir(mode=0o700)
    with inputs.m.CasePlan(s.plan.root, s.plan.sha256) as original:
        inbox = m.Inbox(original, s.projected, s.journal)
        try:
            yield inbox
        finally:
            inbox.close()


def submit(inbox, route="request", **changes):
    value = (
        m.notice(
            route,
            inbox.plan,
            m.base.checksum(inbox.journal.entries[0]["event"]),
            m.plans.clock.read().boottime_ns / m.plans.clock.NS,
        )
        | changes
    )
    path = inbox.path / (route + ".json")
    path.write_bytes(m.base.encode(value))
    path.chmod(0o600)
    return path


def denied(inbox):
    with pytest.raises(m.UnconfirmedOperator) as caught:
        inbox.consume()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__
    assert inbox.failed and not inbox.lock.locked()
    with pytest.raises(m.UnconfirmedOperator):
        inbox.consume()


def test_missing_notice_does_not_request_or_read_the_host(transfer):
    s = transfer
    with opened(s) as inbox:
        entries = tuple(s.journal.entries)
        s.host.reads.clear()
        assert not inbox.consume() and not inbox.consume()
        assert tuple(s.journal.entries) == entries and not s.host.reads
        assert list(inbox.path.iterdir()) == []


def test_retained_inbox_does_not_hold_publisher_lock_between_reads(transfer):
    with opened(transfer) as inbox:
        for _ in range(2):
            with m.launch.binding.protected._private_directory(inbox.path, exclusive=True):
                assert fcntl.fcntl(inbox.fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
            assert not inbox.consume()


@pytest.mark.parametrize("member", ["original", "plan", "projected", "journal"])
def test_original_members_cannot_be_substituted(transfer, member):
    with opened(transfer) as inbox:
        submit(inbox)
        setattr(inbox, member, object())
        denied(inbox)
        assert transfer.journal.machine.state.phase == "prepared"


@pytest.mark.parametrize("fault", ["notice", "pending", "plan", "late", "reentrant"])
def test_changes_during_read_are_refused_before_journaling(transfer, monkeypatch, fault):
    with opened(transfer) as inbox:
        path = submit(inbox)
        original, changed = m.launch.binding.protected.evidence.read_bytes, []

        def reading(fd, name, **kwargs):
            raw = original(fd, name, **kwargs)
            if name == "request.json" and not changed:
                changed.append(True)
                if fault == "notice":
                    path.write_bytes(b"PRIVATE replacement")
                elif fault == "pending":
                    (inbox.path / ".pending").write_bytes(b"preserve")
                elif fault == "plan":
                    (inbox.plan.root / "plan.json").write_bytes(b"PRIVATE")
                elif fault == "late":
                    monotonic = m.time.monotonic
                    monkeypatch.setattr(m.time, "monotonic", lambda: monotonic() + 3)
                else:
                    denied(inbox)
            return raw

        monkeypatch.setattr(m.launch.binding.protected.evidence, "read_bytes", reading)
        denied(inbox)
        assert changed == [True]
        assert transfer.journal.machine.state.phase == "prepared"


def test_unrelated_sibling_activity_cannot_invalidate_notice(transfer):
    with opened(transfer) as inbox:
        submit(inbox)
        (inbox.plan.root / "unrelated-subdirectory").mkdir(mode=0o700)
        assert inbox.consume()


def test_fresh_bound_request_is_consumed_once_and_never_dispatched(transfer):
    s = transfer
    before_fds = set(os.listdir("/proc/self/fd"))
    with opened(s) as inbox:
        path = submit(inbox)
        raw, identity = path.read_bytes(), m.intake.files.identity(path.stat())
        s.host.reads.clear()
        assert inbox.consume()
        assert s.journal.machine.state.phase == "requested"
        assert not inbox.consume() and not s.host.reads
        assert path.read_bytes() == raw and m.intake.files.identity(path.stat()) == identity
        assert sum(e["event"]["kind"] == "request" for e in s.journal.entries) == 1
        assert not s.journal.machine.state.executions
        inbox.close()
        assert inbox.original.recheck() is inbox.plan and s.journal.fd >= 0
    assert set(os.listdir("/proc/self/fd")) == before_fds


def test_idle_cancel_only_appends_finish_on_original_journal(cancel):
    s = cancel
    with opened(s) as inbox:
        path = submit(inbox, "cancel_idle")
        assert inbox.consume() and path.is_file()
        assert s.journal.machine.state.finish_requested
        assert s.journal.machine.state.phase == "candidate_idle"
        assert not inbox.consume() and len(s.engine.sent) == 2
        # Dispatch remains the original explicit recovery continuation's job.
        result = cancellation.m.recover_never_launched(s.cancel, lambda _: None)
        assert result.phase == "complete" and len(s.engine.sent) == 4
        assert s.journal.machine.state.recording_outcome == "not_attempted"


def test_cancel_is_not_a_request_and_request_is_not_a_cancel(transfer, cancel):
    # These aliases share the same underlying fixture: test wrong-phase
    # request at candidate idle, where it must not act as cancellation.
    s = cancel
    assert transfer is s
    with opened(s) as inbox:
        path = submit(inbox, "request")
        entries = tuple(s.journal.entries)
        assert not inbox.consume() and tuple(s.journal.entries) == entries
        assert path.is_file() and not s.journal.machine.state.finish_requested


def test_early_cancel_does_not_request_transfer(transfer):
    with opened(transfer) as inbox:
        submit(inbox, "cancel_idle")
        assert not inbox.consume() and transfer.journal.machine.state.phase == "prepared"


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("schema", 2),
        ("kind", "request"),
        ("action", "begin"),
        ("case_id", "f" * 32),
        ("boot_id", "f" * 32),
        ("plan_sha256", "f" * 64),
        ("preparation_sha256", "f" * 64),
        ("issued_at", 0),
        ("issued_at", 1e30),
        ("PRIVATE", "SECRET"),
    ],
)
def test_foreign_malformed_or_old_notice_never_grants_a_request(transfer, field, value):
    with opened(transfer) as inbox:
        path = submit(inbox, **{field: value})
        raw = path.read_bytes()
        denied(inbox)
        assert path.read_bytes() == raw
        assert transfer.journal.machine.state.phase == "prepared"


@pytest.mark.parametrize(
    "fault",
    [
        "symlink",
        "hardlink",
        "fifo",
        "directory",
        "mode",
        "empty",
        "large",
        "whitespace",
        "duplicate",
        "missing_field",
        "pending",
        "replaced_inbox",
        "plan",
        "journal",
    ],
)
def test_hostile_files_and_namespaces_never_grant_a_request(transfer, fault):
    s = transfer
    with opened(s) as inbox:
        path = submit(inbox)
        if fault in ("symlink", "hardlink", "fifo", "directory"):
            old = s.plan.root / "saved-notice"
            path.rename(old)
            if fault == "symlink":
                path.symlink_to(old)
            elif fault == "hardlink":
                os.link(old, path)
            elif fault == "fifo":
                os.mkfifo(path, 0o600)
            else:
                path.mkdir(mode=0o700)
        elif fault == "mode":
            path.chmod(0o644)
        elif fault in ("empty", "large", "whitespace", "duplicate", "missing_field"):
            raw = path.read_bytes()
            path.write_bytes(
                {
                    "empty": b"",
                    "large": b"x" * 1025,
                    "whitespace": b" " + raw,
                    "duplicate": b'{"schema":1,' + raw[1:],
                    "missing_field": b'{"schema":1}',
                }[fault]
            )
        elif fault == "pending":
            (inbox.path / ".pending").write_bytes(b"preserve")
        elif fault == "replaced_inbox":
            inbox.path.rename(inbox.path.with_name("saved-inbox"))
            inbox.path.mkdir(mode=0o700)
        elif fault == "plan":
            (s.plan.root / "plan.json").write_bytes(b"PRIVATE")
        else:
            (s.journal.path / s.journal.name(0)).chmod(0o644)
        denied(inbox)
        assert s.journal.machine.state.phase == "prepared"


@pytest.mark.parametrize("after", [False, True])
def test_lost_append_return_is_never_retried_or_deleted(transfer, monkeypatch, after):
    with opened(transfer) as inbox:
        path = submit(inbox)
        append = inbox.journal.append

        def failing(event):
            if after:
                append(event)
            raise OSError("PRIVATE lost append reply")

        monkeypatch.setattr(inbox.journal, "append", failing)
        denied(inbox)
        assert path.is_file()
        assert sum(e["event"]["kind"] == "request" for e in inbox.journal.entries) == int(after)
        assert not inbox.journal.machine.state.executions


@pytest.mark.parametrize("elapsed", [31, 121])
def test_notice_age_and_original_ready_deadline_remain_bounded(transfer, monkeypatch, elapsed):
    with opened(transfer) as inbox:
        submit(inbox)
        original = m.plans.clock.read

        def later():
            value = original()
            offset = elapsed * m.plans.clock.NS
            return replace(
                value,
                before_ns=value.before_ns + offset,
                after_ns=value.after_ns + offset,
                boottime_ns=value.boottime_ns + offset,
            )

        monkeypatch.setattr(m.plans.clock, "read", later)
        denied(inbox)
        assert inbox.journal.machine.state.phase == "prepared"


def test_operator_authorization_disables_idle_cancellation(cancel):
    s = cancel
    sample = s.before.read()
    s.append(
        "authorize_operator",
        generation=s.journal.machine.state.candidate_generation,
        bootstrap_sha256=s.plan.bootstrap.sha256,
        launch_plan_sha256="d" * 64,
        idle_evidence_sha256="e" * 64,
        observation=asdict(sample.observation),
    )
    with opened(s) as inbox:
        submit(inbox, "cancel_idle")
        assert not inbox.consume() and not s.journal.machine.state.finish_requested
        assert len(s.engine.sent) == 2


def test_wrong_thread_consumes_no_notice(transfer):
    with opened(transfer) as inbox:
        submit(inbox)
        errors = []

        def other():
            try:
                inbox.consume()
            except m.UnconfirmedOperator as error:
                errors.append(str(error))

        worker = Thread(target=other)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive() and errors == [m.MESSAGE]
        denied(inbox)
        assert inbox.journal.machine.state.phase == "prepared"


def publisher(inbox, action="request", **changes):
    values = dict(
        original=inbox.original,
        action=action,
        preparation_sha256=m.base.checksum(inbox.journal.entries[0]["event"]),
        issued_at=m.plans.clock.read().boottime_ns / m.plans.clock.NS,
    )
    return m.Publisher(**(values | changes))


def publication_denied(value):
    with pytest.raises(m.UnconfirmedOperator) as caught:
        value.publish()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__
    assert value.failed and not value.lock.locked()
    with pytest.raises(m.UnconfirmedOperator):
        value.publish()


def test_explicit_atomic_publication_changes_only_notice_not_journal_or_host(transfer):
    with opened(transfer) as inbox:
        value = publisher(inbox)
        old = tuple(transfer.journal.entries)
        transfer.host.reads.clear()
        descriptors = len(os.listdir("/proc/self/fd"))
        pin = value.publish()
        path = inbox.path / "request.json"
        assert pin == m.base.checksum(m.object_json(path.read_bytes()))
        assert path.read_bytes() == value.raw
        assert path.stat().st_nlink == 1 and path.stat().st_mode & 0o777 == 0o600
        assert {p.name for p in inbox.path.iterdir()} == {"request.json"}
        assert tuple(transfer.journal.entries) == old and not transfer.host.reads
        assert len(os.listdir("/proc/self/fd")) == descriptors
        publication_denied(value)
        assert inbox.consume() and transfer.journal.machine.state.phase == "requested"
        assert not inbox.consume()


def test_published_cancel_uses_original_pristine_restoration_session(cancel):
    with opened(cancel) as inbox:
        value = publisher(inbox, "cancel_idle")
        value.publish()
        assert len(cancel.engine.sent) == 2 and not cancel.journal.machine.state.finish_requested
        assert inbox.consume()
        result = cancellation.m.recover_never_launched(cancel.cancel, lambda _: None)
        assert result.phase == "complete" and len(cancel.engine.sent) == 4
        assert cancel.journal.machine.state.recording_outcome == "not_attempted"


def test_busy_publisher_is_no_notice_yet_not_a_permanent_consumer_failure(transfer):
    with opened(transfer) as inbox:
        value = publisher(inbox)
        with m.launch.binding.protected._private_directory(inbox.path, exclusive=True):
            assert not inbox.consume() and not inbox.failed
        value.publish()
        assert inbox.consume()


@pytest.mark.parametrize("where", ["open", "read"])
def test_eagain_outside_flock_is_not_misreported_as_busy(transfer, monkeypatch, where):
    with opened(transfer) as inbox:
        submit(inbox)
        if where == "read":

            def failed(*args, **kwargs):
                raise BlockingIOError(errno.EAGAIN, "PRIVATE read")

            monkeypatch.setattr(m.launch.binding.protected.evidence, "read_bytes", failed)
        else:
            original = m.os.open

            def failed(path, *args, **kwargs):
                if path == "inbox":
                    raise BlockingIOError(errno.EAGAIN, "PRIVATE open")
                return original(path, *args, **kwargs)

            monkeypatch.setattr(m.os, "open", failed)
        denied(inbox)
        assert transfer.journal.machine.state.phase == "prepared"


@pytest.mark.parametrize(
    "fault", ["existing", "pending", "replaced_inbox", "plan", "raw", "clock", "lock"]
)
def test_publisher_preflight_refusals_preserve_every_existing_file(transfer, monkeypatch, fault):
    with opened(transfer) as inbox:
        value = publisher(inbox)
        if fault == "existing":
            submit(inbox)
        elif fault == "pending":
            (inbox.path / ".pending-request").write_bytes(b"PRIVATE original")
        elif fault == "replaced_inbox":
            inbox.path.rename(inbox.path.with_name("saved-inbox"))
            inbox.path.mkdir(mode=0o700)
        elif fault == "plan":
            (value.plan.root / "plan.json").write_bytes(b"PRIVATE original")
        elif fault == "raw":
            value.raw = b"PRIVATE replacement"
        elif fault == "clock":
            original = m.plans.clock.read

            def late():
                stamp = original()
                shift = 31 * m.plans.clock.NS
                return replace(
                    stamp,
                    before_ns=stamp.before_ns + shift,
                    after_ns=stamp.after_ns + shift,
                    boottime_ns=stamp.boottime_ns + shift,
                )

            monkeypatch.setattr(m.plans.clock, "read", late)
        before = {p.name: p.read_bytes() for p in inbox.path.iterdir()}
        if fault == "lock":
            with m.launch.binding.protected._private_directory(inbox.path, exclusive=False):
                publication_denied(value)
        else:
            publication_denied(value)
        assert before == {p.name: p.read_bytes() for p in inbox.path.iterdir()}
        assert transfer.journal.machine.state.phase == "prepared"


@pytest.mark.parametrize("step", ["write", "file_sync", "link", "unlink", "directory_sync"])
@pytest.mark.parametrize("after", [False, True])
def test_lost_publication_reply_is_not_retried_or_cleaned(transfer, monkeypatch, step, after):
    with opened(transfer) as inbox:
        value = publisher(inbox)
        name = {"file_sync": "fsync", "directory_sync": "fsync"}.get(step, step)
        original, calls = getattr(m.os, name), []

        def lost(*args, **kwargs):
            if name == "fsync":
                directory = m.stat.S_ISDIR(os.fstat(args[0]).st_mode)
                if directory != (step == "directory_sync"):
                    return original(*args, **kwargs)
            calls.append(True)
            if after:
                original(*args, **kwargs)
            raise OSError("PRIVATE lost publication reply")

        descriptors = len(os.listdir("/proc/self/fd"))
        with monkeypatch.context() as patch:
            patch.setattr(m.os, name, lost)
            publication_denied(value)
        assert calls == [True] and len(os.listdir("/proc/self/fd")) == descriptors
        names = {p.name for p in inbox.path.iterdir()}
        assert names  # No error-path cleanup of partial or committed evidence.
        assert transfer.journal.machine.state.phase == "prepared"
        before = {p.name: p.read_bytes() for p in inbox.path.iterdir()}
        replacement = publisher(inbox)
        publication_denied(replacement)
        assert before == {p.name: p.read_bytes() for p in inbox.path.iterdir()}
        if names == {"request.json"}:
            assert inbox.consume() and not inbox.consume()
        else:
            denied(inbox)


@pytest.mark.parametrize(
    "fault", ["short_write", "late", "reentrant", "replace_temp", "replace_destination"]
)
def test_changes_during_publication_leave_evidence_and_no_dispatch(transfer, monkeypatch, fault):
    with opened(transfer) as inbox:
        value = publisher(inbox)
        original, changes = m.os.write, []
        if fault == "replace_destination":
            original_link = m.os.link

            def linked(*args, **kwargs):
                original_link(*args, **kwargs)
                changes.append(True)
                path = inbox.path / "request.json"
                path.rename(path.with_name("saved-request"))
                path.symlink_to("saved-request")

            monkeypatch.setattr(m.os, "link", linked)
        else:

            def wrote(fd, data):
                changes.append(True)
                written = original(fd, data[:-1] if fault == "short_write" else data)
                if fault == "late":
                    monotonic = m.time.monotonic
                    monkeypatch.setattr(m.time, "monotonic", lambda: monotonic() + 3)
                elif fault == "reentrant":
                    with pytest.raises(m.UnconfirmedOperator):
                        value.publish()
                elif fault == "replace_temp":
                    path = inbox.path / ".pending-request"
                    path.rename(path.with_name("saved-pending"))
                    path.symlink_to("saved-pending")
                return written

            monkeypatch.setattr(m.os, "write", wrote)
        publication_denied(value)
        assert changes == [True] and list(inbox.path.iterdir())
        assert transfer.journal.machine.state.phase == "prepared"
        assert not transfer.journal.machine.state.executions


def test_wrong_thread_cannot_publish_and_cannot_reset_the_attempt(transfer):
    with opened(transfer) as inbox:
        value, errors = publisher(inbox), []

        def other():
            try:
                value.publish()
            except m.UnconfirmedOperator as error:
                errors.append(str(error))

        worker = Thread(target=other)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive() and errors == [m.MESSAGE]
        publication_denied(value)
        assert list(inbox.path.iterdir()) == []
