"""Original-clock passive service assembly; synthetic host/cache transport only."""

import fcntl
import os
import stat
from threading import Thread

import pytest

from . import test_supplemental_recording_startup_baseline as baseline_tests

m = baseline_tests.m
operator = baseline_tests.integration.m
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
    baseline_tests.layout,
    baseline_tests.tree,
    baseline_tests.routing,
    baseline_tests.projection,
    baseline_tests.binding,
    baseline_tests.directory,
    baseline_tests.prepared,
    baseline_tests.joined,
    baseline_tests.before_handoff,
    baseline_tests.service_case,
)


def accept(s, *, read_host=True):
    owner = s.startup
    if read_host:
        owner.prepare_service(s.projected, s.docker)
    else:
        owner.prepare()
    baseline_tests.integration.startups.submit(owner)
    assert owner.poll() is owner.original
    return owner


def preserved(s):
    return {p.name: p.read_bytes() for p in s.root.iterdir() if p.is_file()}


def test_passive_assembly_seals_only_original_journal_and_retains_clock_last(service_case):
    s = service_case
    owner = accept(s)
    original_files = preserved(s)
    links = s.root.stat().st_nlink
    with owner.idle_service(s.docker) as service:
        assert owner.service_used and owner._service_active and owner.lock.locked()
        assert service.original is owner.original and service.clock_witness is owner.clock
        assert service.projected is owner.projected and service.docker is s.docker
        assert service.journal.machine.baseline == owner.baseline
        assert service.journal.machine.created_at == owner.original.plan.deadlines.issued_at
        assert len(service.journal.entries) == 1 and not service.used
        assert not service.processes.witnesses and not list(service.inbox.path.iterdir())
        assert {p.name for p in s.root.iterdir()} == set(original_files) | {"journal", "inbox"}
        for name in ("journal", "inbox"):
            info = (s.root / name).stat()
            assert stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.geteuid()
        assert len(s.cached_calls) == 1
        assert s.root.stat().st_nlink == links + 2
        # Setup's independent lock is retired, not attached to CasePlan's fd.
        with m.publication.protected._private_directory(s.root, exclusive=True):
            pass
    assert service.closed and service.journal.fd == -1
    assert not owner.closed and not owner.clock.closed and not owner.lock.locked()
    assert not owner._service_active and not s.declaration.closed
    os.fstat(owner.clock.fd)
    assert preserved(s) == original_files


def test_explicit_dispatch_observer_is_borrowed_without_calling_or_replacing_owners(service_case):
    s = service_case
    owner = accept(s)

    def observer(_):
        pytest.fail("Passive assembly must not request CLI evidence")

    with owner.idle_service(s.docker, dispatch_observer=observer) as service:
        assert service.dispatch.observe is service.dispatch._original_observe is observer
        assert service._dispatch_observer is observer
        assert service.session.dispatch is service.dispatch
        assert service.session.processes is service.processes
        assert service.session.journal is service.journal
        assert service.clock_witness is owner.clock
        service._context()
        assert not service.used and not service.dispatch.used and len(service.journal.entries) == 1
    assert service.closed and not owner.clock.closed


@pytest.mark.parametrize("observer", [False, True, 0, "observe", object()])
def test_invalid_observer_refuses_before_creating_service_directories(service_case, observer):
    s = service_case
    owner = accept(s)
    before = preserved(s)
    with (
        pytest.raises(m.UnconfirmedStartup),
        owner.idle_service(s.docker, dispatch_observer=observer),
    ):
        pytest.fail("Invalid observer reached assembly")
    assert preserved(s) == before
    assert not (s.root / "journal").exists() and not (s.root / "inbox").exists()
    assert owner.service_used and owner.closed and owner.clock.closed


@pytest.mark.parametrize("member", ["observe", "_original_observe", "both", "all"])
def test_assembled_observer_cannot_be_removed_or_rebound(service_case, member):
    s = service_case
    owner = accept(s)

    def observer(_):
        pytest.fail("No action or evidence should be requested")

    with owner.idle_service(s.docker, dispatch_observer=observer) as service:
        for name in ("observe", "_original_observe") if member in ("both", "all") else (member,):
            setattr(service.dispatch, name, None)
        if member == "all":
            service._dispatch_observer = None
        with pytest.raises(operator.UnconfirmedOperator):
            service._context()
        assert not service.used and not service.dispatch.used and len(service.journal.entries) == 1
    assert service.closed and not owner.clock.closed


@pytest.mark.parametrize("extra", ["unexpected", "journal/unexpected"])
def test_unplanned_directory_change_refuses_and_preserves_evidence(
    service_case, monkeypatch, extra
):
    s = service_case
    owner = accept(s)
    mkdir = m.os.mkdir

    def changed(name, *args, **kwargs):
        result = mkdir(name, *args, **kwargs)
        if name == "journal":
            mkdir(extra, mode=0o700, dir_fd=kwargs["dir_fd"])
        return result

    monkeypatch.setattr(m.os, "mkdir", changed)
    with pytest.raises(m.UnconfirmedStartup), owner.idle_service(s.docker):
        pytest.fail("Accepted an unplanned directory change")
    assert (s.root / extra).is_dir() and not (s.root / "inbox").exists()
    assert owner.closed and owner.clock.closed


def test_busy_original_directory_refuses_without_creating_children(service_case):
    s = service_case
    owner = accept(s)
    fd = os.open(s.root, m.declaration.files.DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(m.UnconfirmedStartup), owner.idle_service(s.docker):
            pytest.fail("Bypassed the existing directory owner")
        assert not (s.root / "journal").exists()
        assert not (s.root / "inbox").exists()
    finally:
        os.close(fd)
    assert owner.closed and owner.clock.closed


def test_successful_assembly_is_not_reusable(service_case):
    s = service_case
    owner = accept(s)
    with owner.idle_service(s.docker) as service:
        pass
    entry = (s.root / "journal/0000.json").read_bytes()
    with pytest.raises(m.UnconfirmedStartup), owner.idle_service(s.docker):
        pytest.fail("Assembled the same startup twice")
    assert (s.root / "journal/0000.json").read_bytes() == entry
    assert service.closed and owner.closed and owner.clock.closed


@pytest.mark.parametrize("interrupt", [False, True])
def test_setup_descriptor_close_is_not_retried_or_allowed_to_hide_interruption(
    service_case, monkeypatch, interrupt
):
    s = service_case
    owner = accept(s)
    open_fd, close_fd, mkdir = m.os.open, m.os.close, m.os.mkdir
    opened, closed = [], []

    def opening(path, *args, **kwargs):
        fd = open_fd(path, *args, **kwargs)
        if path == "." and kwargs.get("dir_fd") == owner.original._directories[-1][2]:
            opened.append(fd)
        return fd

    def closing(fd):
        if fd in opened:
            closed.append(fd)
            close_fd(fd)
            raise OSError("PRIVATE close acknowledgment")
        return close_fd(fd)

    def creating(name, *args, **kwargs):
        if interrupt and name == "inbox":
            raise KeyboardInterrupt("PRIVATE interruption")
        return mkdir(name, *args, **kwargs)

    monkeypatch.setattr(m.os, "open", opening)
    monkeypatch.setattr(m.os, "close", closing)
    monkeypatch.setattr(m.os, "mkdir", creating)
    expected = KeyboardInterrupt if interrupt else m.UnconfirmedStartup
    with pytest.raises(expected), owner.idle_service(s.docker):
        pytest.fail("Accepted uncertain descriptor cleanup")
    assert len(opened) == 1 and closed == opened
    assert owner.closed and owner.clock.closed and (s.root / "journal").is_dir()
    assert not (s.root / "journal/0000.json").exists()


@pytest.mark.parametrize("name", ["journal", "inbox"])
def test_replaced_created_directory_is_not_adopted(service_case, monkeypatch, name):
    s = service_case
    owner = accept(s)
    journal_init = operator.launch.bootstrap.Journal.__init__

    def changed(journal, path):
        (s.root / name).rename(s.root / (name + "-original"))
        (s.root / name).mkdir(mode=0o700)
        journal_init(journal, path)

    monkeypatch.setattr(operator.launch.bootstrap.Journal, "__init__", changed)
    with pytest.raises(m.UnconfirmedStartup), owner.idle_service(s.docker):
        pytest.fail("Adopted a replacement directory")
    assert (s.root / name).is_dir() and (s.root / (name + "-original")).is_dir()
    assert owner.closed and owner.clock.closed


def test_reentrant_construction_cannot_close_the_clock_before_journal_cleanup(
    service_case, monkeypatch
):
    s = service_case
    owner = accept(s)
    cleaned = []
    close_journal = operator.launch.bootstrap.Journal.close

    def constructing(*_args, **_kwargs):
        with pytest.raises(m.UnconfirmedStartup):
            owner.accepted_input()
        assert owner.failed and not owner.closed and not owner.clock.closed
        raise OSError("PRIVATE construction aborted")

    def closing(journal):
        if journal.path == s.root / "journal":
            assert not owner.clock.closed
            cleaned.append(True)
        return close_journal(journal)

    monkeypatch.setattr(operator.launch.TransferHost, "__init__", constructing)
    monkeypatch.setattr(operator.launch.bootstrap.Journal, "close", closing)
    with pytest.raises(m.UnconfirmedStartup), owner.idle_service(s.docker):
        pytest.fail("Returned a failed borrower")
    assert cleaned == [True] and owner.closed and owner.clock.closed


@pytest.mark.parametrize("missing", ["acceptance", "baseline"])
def test_no_assembly_from_unaccepted_or_probe_only_startup(service_case, missing):
    s = service_case
    owner = s.startup
    if missing == "acceptance":
        owner.prepare_service(s.projected, s.docker)
    else:
        accept(s, read_host=False)
    original_files = preserved(s)
    with pytest.raises(m.UnconfirmedStartup), owner.idle_service(s.docker):
        pytest.fail("Assembled without prerequisites")
    assert owner.failed and owner.closed and owner.clock.closed
    assert preserved(s) == original_files
    assert not (s.root / "journal").exists() and not (s.root / "inbox").exists()


@pytest.mark.parametrize("name", ["journal", "inbox", "unknown", "request.json"])
def test_residue_is_preserved_not_reinitialized(service_case, name):
    s = service_case
    owner = accept(s)
    path = s.root / name
    path.write_bytes(b"PRIVATE prior case")
    before = preserved(s)
    with pytest.raises(m.UnconfirmedStartup), owner.idle_service(s.docker):
        pytest.fail("Adopted prior evidence")
    assert preserved(s) == before and {p.name for p in s.root.iterdir()} == set(before)
    assert owner.closed and owner.clock.closed


@pytest.mark.parametrize(
    "stage", ["mkdir", "sync", "append_before", "append_after", "service", "late"]
)
def test_partial_assembly_closes_handles_without_removing_files(service_case, monkeypatch, stage):
    s = service_case
    owner = accept(s)
    original_files, journals = preserved(s), []
    journal_init = operator.launch.bootstrap.Journal.__init__

    def opened(journal, path):
        journal_init(journal, path)
        journals.append(journal)

    monkeypatch.setattr(operator.launch.bootstrap.Journal, "__init__", opened)
    if stage == "mkdir":
        mkdir = m.os.mkdir

        def failed(name, *args, **kwargs):
            if name == "inbox":
                raise OSError("PRIVATE mkdir")
            return mkdir(name, *args, **kwargs)

        monkeypatch.setattr(m.os, "mkdir", failed)
    elif stage == "sync":
        sync = m.os.fsync
        root_id = os.stat(s.root).st_ino

        def failed(fd):
            sync(fd)
            if os.fstat(fd).st_ino == root_id:
                raise OSError("PRIVATE sync acknowledgement")

        monkeypatch.setattr(m.os, "fsync", failed)
    elif stage == "service":

        def failed(*_args, **_kwargs):
            raise OSError("PRIVATE transfer construction")

        monkeypatch.setattr(operator.launch.TransferHost, "__init__", failed)
    else:
        append = operator.launch.bootstrap.Journal.append
        monotonic = m.time.monotonic

        def failed(journal, event):
            if stage == "append_before":
                raise OSError("PRIVATE append")
            result = append(journal, event)
            if stage == "late":
                monkeypatch.setattr(m.time, "monotonic", lambda: monotonic() + 3)
                return result
            raise OSError("PRIVATE append acknowledgement")

        monkeypatch.setattr(operator.launch.bootstrap.Journal, "append", failed)
    with pytest.raises(m.UnconfirmedStartup) as error, owner.idle_service(s.docker):
        pytest.fail("Returned partial assembly")
    assert str(error.value) == m.MESSAGE and owner.failed and owner.closed
    assert owner.clock.closed and not owner._service_active and not owner.lock.locked()
    assert not s.declaration.closed and all(journal.fd == -1 for journal in journals)
    assert preserved(s) == original_files and (s.root / "journal").is_dir()
    if stage != "mkdir":
        assert (s.root / "inbox").is_dir()
    if stage in ("append_after", "service", "late"):
        assert (s.root / "journal/0000.json").is_file()


@pytest.mark.parametrize("problem", [OSError, KeyboardInterrupt, SystemExit])
def test_body_failure_closes_service_then_journal_then_startup_clock(
    service_case, monkeypatch, problem
):
    s = service_case
    owner = accept(s)
    order = []
    close_service, close_journal = (
        operator.IdleService.close,
        operator.launch.bootstrap.Journal.close,
    )

    def service_close(service):
        assert not owner.clock.closed and service.journal.fd >= 0
        close_service(service)
        order.append("service")
        raise OSError("PRIVATE service close acknowledgement")

    def journal_close(journal):
        if journal.path != s.root / "journal":
            return close_journal(journal)
        assert not owner.clock.closed and order == ["service"]
        close_journal(journal)
        order.append("journal")

    monkeypatch.setattr(operator.IdleService, "close", service_close)
    monkeypatch.setattr(operator.launch.bootstrap.Journal, "close", journal_close)
    expected = m.UnconfirmedStartup if issubclass(problem, Exception) else problem
    with pytest.raises(expected), owner.idle_service(s.docker) as service:
        raise problem("PRIVATE body")
    assert order == ["service", "journal"]
    assert service.closed and service.journal.fd == -1 and owner.clock.closed
    assert owner.closed and owner.failed and not s.declaration.closed
    owner.close()
    assert order == ["service", "journal"]


def test_startup_cannot_close_clock_while_original_service_is_borrowing_it(service_case):
    s = service_case
    owner = accept(s)
    with owner.idle_service(s.docker) as service:
        with pytest.raises(m.UnconfirmedStartup):
            owner.close()
        assert not owner.closed and not owner.clock.closed and not service.closed
    assert service.closed and not owner.clock.closed
    owner.close()
    assert owner.clock.closed


def test_reentrant_assembly_preserves_original_borrower_until_unwinding(service_case):
    s = service_case
    owner = accept(s)
    with pytest.raises(m.UnconfirmedStartup), owner.idle_service(s.docker) as service:
        with pytest.raises(m.UnconfirmedStartup), owner.idle_service(s.docker):
            pytest.fail("Created a second borrower")
        assert owner.failed and not owner.closed and not owner.clock.closed
        assert not service.closed and service.journal.fd >= 0
        # Losing startup custody denies further actions immediately, without
        # closing its clock ahead of the borrower's original cleanup.
        assert service.failed
        with pytest.raises(operator.UnconfirmedOperator):
            service._context()
    assert service.closed and service.journal.fd == -1 and owner.clock.closed


def test_return_does_not_refresh_original_acceptance_window(service_case, monkeypatch):
    s = service_case
    owner = accept(s)
    deadline, plan = owner.offer.deadline, owner.original.plan
    with owner.idle_service(s.docker) as service:
        monkeypatch.setattr(m.time, "monotonic", lambda: deadline + 1)
    assert service.closed and not owner.closed and not owner.clock.closed
    assert owner.original.plan is plan and owner.offer.deadline == deadline
    assert len(s.cached_calls) == 1


def test_expired_acceptance_cannot_begin_assembly(service_case, monkeypatch):
    s = service_case
    owner = accept(s)
    original_files = preserved(s)
    monkeypatch.setattr(m.time, "monotonic", lambda: owner.offer.deadline)
    with pytest.raises(m.UnconfirmedStartup), owner.idle_service(s.docker):
        pytest.fail("Assembled after original offer expiry")
    assert owner.closed and owner.clock.closed and preserved(s) == original_files
    assert not (s.root / "journal").exists()


def test_changed_startup_clock_closes_original_callbacks_not_replacement(service_case):
    s = service_case
    owner = accept(s)
    original_clock = owner.clock
    with pytest.raises(m.UnconfirmedStartup), owner.idle_service(s.docker) as service:
        owner.clock = object()
    assert service.closed and service.journal.fd == -1 and original_clock.closed
    assert owner.closed


def test_foreign_assembly_cannot_close_original_borrower(service_case):
    s = service_case
    owner = accept(s)
    failures = []

    def foreign():
        try:
            with owner.idle_service(s.docker):
                pytest.fail("Foreign context acquired service")
        except m.UnconfirmedStartup:
            failures.append(True)

    with pytest.raises(m.UnconfirmedStartup), owner.idle_service(s.docker) as service:
        thread = Thread(target=foreign)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive() and failures == [True]
        assert owner.failed and not owner.closed and not owner.clock.closed
        assert not service.closed
        assert service.failed
        with pytest.raises(operator.UnconfirmedOperator):
            service._context()
    assert service.closed and owner.clock.closed
