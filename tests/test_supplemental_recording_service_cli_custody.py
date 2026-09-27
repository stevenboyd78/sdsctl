"""Real journal/Unix Engine/pidfd custody, synthetic CLI operations and metadata.

The observer is called in-process at the actual TrackedDispatch boundaries.
This is not an authenticated cross-process active command or installed trial.
"""

import importlib.util
import json
import os
import select
import subprocess
import sys
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_handoff_host as host_tests
from . import test_supplemental_recording_host_plan as plan_tests
from . import test_supplemental_recording_projection as projection_tests
from . import test_supplemental_recording_service_app_custody as app_tests

NAME = "supplemental_recording_service_cli_custody"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(app_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
prepared = app_tests.prepared
layout, tree, routing, projection = (
    projection_tests.layout,
    projection_tests.tree,
    projection_tests.routing,
    projection_tests.projection,
)
REAL_OPEN = os.open
pytestmark = app_tests.pytestmark


def now():
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME) / 1_000_000_000


def exit_helper(case):
    case.prepared.child.stdin.close()
    assert case.prepared.child.wait(timeout=3) == 0


def denied(action):
    with pytest.raises(m.UnconfirmedCliCustody) as caught:
        action()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


@contextmanager
def setup(prepared, projection, tmp_path, monkeypatch, *, sender=False):
    _, raw = plan_tests.projected_plan(projection)
    original = json.loads(prepared.plan.raw)
    raw.update({key: original[key] for key in ("boot", "original_clock", "deadlines")})
    cli = host_tests.container()
    raw.update(
        cli_image=cli["Image"],
        cli_generation=m.platform.generation(cli, name=m.platform.CLI, image=cli["Image"]),
    )
    prepared.plan = plan_tests.m.decode(raw)
    monkeypatch.setattr(os, "open", REAL_OPEN)
    root = tmp_path / "cli-host"
    root.mkdir(mode=0o700)
    directory = root / "journal"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(plan_tests.m.Plan, "root", property(lambda _: root))
    executions, containers = {}, {m.platform.CLI: cli}
    with app_tests.setup(
        prepared, tmp_path, monkeypatch, sender=sender, executions=executions, containers=containers
    ) as case:
        custody = case.create()
        plan = custody.plan
        baseline = m.bootstrap.recording.Observation(
            plan.deadlines.issued_at,
            m.base.App(plan.normal.pin, "running", plan.normal_generation, True, False),
            m.base.App(plan.candidate.pin, "stopped"),
            True,
            True,
            True,
            m.bootstrap.recording.Files(
                plan.candidate.contract.sha256, "pristine", plan.candidate.contract.baseline_sha256
            ),
        )
        with m.bootstrap.Journal(directory) as journal:
            journal.append(plan.preparation(baseline, projection))
            observer = m.CliCustody(custody, projection)
            receipts = []

            def observe(notice):
                receipts.append(notice)
                return observer.observe(notice)

            class Docker(host_tests.FakeDocker):
                def __init__(self):
                    super().__init__()
                    self.cli = cli

                def create_execution(self, cid, command):
                    assert len(receipts) % 2 == 1 and observer._pending is receipts[-1]
                    super().create_execution(cid, command)
                    eid = f"{int(host_tests.EID, 16) + len(self.created) - 1:064x}"
                    self.exec = host_tests.execution(command)
                    self.exec.update(ID=eid, ExitCode=None)
                    executions[eid] = self.exec
                    return eid

                def inspect_execution(self, eid):
                    return deepcopy(executions[eid])

                def start_execution(self, eid):
                    assert len(receipts) % 2 == 0
                    assert observer._executions[-1].binding.execution_id == eid
                    assert observer._executions[-1].state == "created"
                    super().start_execution(eid)

            docker = Docker()
            dispatch = m.platform.TrackedDispatch(
                journal,
                docker,
                cli_image=plan.cli_image,
                cli_generation=plan.cli_generation,
                now=now,
                observe=observe,
            )
            case.custody, case.cli_observer, case.journal = custody, observer, journal
            case.docker, case.dispatch, case.receipts, case.executions = (
                docker,
                dispatch,
                receipts,
                executions,
            )
            case.baseline, case.projection = baseline, projection
            try:
                yield case
            finally:
                observer.close()


def intent(case):
    plan, journal = case.custody.plan, case.journal
    original = case.custody._retained[0][0]
    event = dict(
        kind="bind_process",
        boot_id=plan.boot,
        now=now(),
        process=asdict(
            m.base.ProcessRecord(
                m.base.NORMAL,
                original.generation,
                original.process.container_id,
                original.process.pid,
                original.process.start_ticks,
            )
        ),
    )
    journal.append(event)
    journal.append(dict(kind="request", boot_id=plan.boot, now=now()))
    stamp = now()
    action = journal.append(
        dict(
            kind="observe",
            boot_id=plan.boot,
            now=stamp,
            observation=asdict(replace(case.baseline, sampled_at=stamp)),
        )
    )
    assert type(action) is m.base.Action and action.slug == m.base.NORMAL
    return m.platform.CONTROL["stopping_normal"], plan.case


def send(case):
    case.dispatch(*intent(case))


@pytest.mark.parametrize("sender", [False, True])
def test_real_dispatch_both_boundaries_independent_reads_and_terminal_retention(
    projection, prepared, tmp_path, monkeypatch, sender
):
    with setup(prepared, projection, tmp_path, monkeypatch, sender=sender) as case:
        original_fd = case.journal.fd
        # Observer did not adopt, share, replace or acquire the writer's flock.
        with pytest.raises(BlockingIOError):
            m.bootstrap.Journal(case.journal.path)
        send(case)
        assert [n.stage for n in case.receipts] == ["before_create", "before_start"]
        assert case.journal.fd == original_fd
        assert case.docker.started == [host_tests.EID]
        assert case.journal.machine.state.phase == "stopping_normal"
        assert case.journal.machine.state.completed_executions == ()
        status = case.cli_observer.poll()
        assert status.executions[0].state == "not_running"
        assert status.executions[0].exit_code == 0
        assert not status.capture_failed and not status.inspection_failed
        count = len(case.state.requests)
        case.executions.clear()  # Expired metadata does not erase the captured exit.
        app_tests.exit_normal(case)
        exit_helper(case)
        case.endpoint.close()
        case.journal.close()
        status = case.cli_observer.poll()
        assert status.apps.deadline.helper_exited and status.apps.normal_exited
        assert status.executions[0].exit_code == 0 and not status.inspection_failed
        assert len(case.state.requests) == count


def test_running_cli_survives_helper_loss_but_missing_metadata_is_never_exit(
    projection, prepared, tmp_path, monkeypatch
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        send(case)
        case.docker.exec.update(Running=True, Pid=1234, ExitCode=None)
        exit_helper(case)
        assert case.cli_observer.poll().executions[0].state == "running"
        case.state.hook = lambda value, _: {} if "ProcessConfig" in value else value
        status = case.cli_observer.poll()
        assert status.inspection_failed and status.executions[0].exit_code is None
        assert status.executions[0].state == "running"
        count = len(case.state.requests)
        case.state.hook = None
        case.docker.exec.update(Running=False, Pid=0, ExitCode=0)
        assert case.cli_observer.poll() == status
        assert len(case.state.requests) == count  # No retry after an unknown result.


@pytest.mark.parametrize("stage", ["before_create", "before_start"])
def test_lost_receipt_consumes_dispatch_without_repeating_mutation(
    projection, prepared, tmp_path, monkeypatch, stage
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        original = case.dispatch.observe

        def lost(notice):
            result = original(notice)
            if notice.stage == stage:
                raise OSError("private-ack-lost")
            return result

        case.dispatch.observe = case.dispatch._original_observe = lost
        arguments = intent(case)
        with pytest.raises(OSError, match="private-ack-lost"):
            case.dispatch(*arguments)
        assert len(case.docker.created) == (stage == "before_start")
        assert not case.docker.started
        status = case.cli_observer.poll()
        if stage == "before_create":
            assert status.pending_intent == ("stopping_normal", case.receipts[0].receipt)
        else:
            assert status.executions[0].state == "created"
            assert status.executions[0].exit_code is None
        with pytest.raises(m.base.UnsafeHandoff):
            case.dispatch(*arguments)
        # Even reconstructing dispatch cannot reissue the pending intent.
        fresh = m.platform.TrackedDispatch(
            case.journal,
            case.docker,
            cli_image=case.custody.plan.cli_image,
            cli_generation=case.custody.plan.cli_generation,
            now=now,
            observe=case.cli_observer.observe,
        )
        with pytest.raises(m.base.UnsafeHandoff):
            fresh(*arguments)
        assert not case.docker.started


@pytest.mark.parametrize("fault", ["receipt", "helper", "history", "generation", "created"])
def test_refusal_at_second_boundary_does_not_start_or_claim_exit(
    projection, prepared, tmp_path, monkeypatch, fault
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        original = case.dispatch.observe

        def altered(notice):
            if notice.stage == "before_start":
                if fault == "receipt":
                    return "f" * 64
                if fault == "helper":
                    exit_helper(case)
                if fault == "history":
                    return original(replace(notice, history=notice.history[:-1]))
                if fault == "generation":
                    case.docker.cli["State"]["Pid"] += 1
                if fault == "created":
                    case.docker.exec.update(Running=True, Pid=777, ExitCode=None)
            return original(notice)

        case.dispatch.observe = case.dispatch._original_observe = altered
        with pytest.raises((m.UnconfirmedCliCustody, m.base.UnsafeHandoff)):
            send(case)
        assert case.docker.created and not case.docker.started
        assert not case.cli_observer._executions
        assert case.journal.machine.state.completed_executions == ()


@pytest.mark.parametrize("fault", ["unobserved_exit", "unobserved_command"])
def test_later_intent_cannot_adopt_missing_observer_history(
    projection, prepared, tmp_path, monkeypatch, fault
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        send(case)
        case.dispatch.reconcile_executions()
        # Deliberately DO NOT independently inspect the terminal CLI here.
        if fault == "unobserved_command":
            case.cli_observer._retain(())  # Simulate entirely missing earlier custody.
        app_tests.exit_normal(case)
        plan, journal = case.custody.plan, case.journal
        journal.append(
            dict(
                kind="process_exited",
                boot_id=plan.boot,
                now=now(),
                generation=plan.normal_generation,
            )
        )
        stamp = now()
        action = journal.append(
            dict(
                kind="observe",
                boot_id=plan.boot,
                now=stamp,
                observation=asdict(
                    replace(
                        case.baseline,
                        sampled_at=stamp,
                        normal=m.base.App(plan.normal.pin, "stopped"),
                    )
                ),
            )
        )
        assert action.slug == m.base.CANDIDATE
        denied(lambda: case.dispatch(m.platform.CONTROL["starting_candidate"], plan.case))
        assert len(case.docker.created) == len(case.docker.started) == 1


@pytest.mark.parametrize("fault", ["rename", "permissions", "directory", "descriptor"])
def test_history_cleanup_keeps_original_writer_and_closes_only_its_own_fd(
    projection, prepared, tmp_path, monkeypatch, fault
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        observer = case.cli_observer
        history = observer._history
        fd, writer = history.fd, case.journal.fd
        args = intent(case)
        directory = case.journal.path
        spare = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            if fault == "rename":
                directory.rename(tmp_path / "saved-journal")
                directory.mkdir(mode=0o700)
            elif fault == "permissions":
                directory.chmod(0o755)
            elif fault == "directory":
                (directory / "unknown").mkdir(mode=0o700)
            else:
                history.fd = spare
            # Call the observer directly: the original dispatcher separately
            # rejects replaced/changed directories before making a request.
            notice = m.platform.DispatchNotice(
                "before_create",
                "stopping_normal",
                args[1],
                case.custody.plan.boot,
                host_tests.CID,
                None,
                tuple(m.base.encode(e) for e in case.journal.entries),
            )
            denied(lambda: observer.observe(notice))
            observer.close()
            with pytest.raises(OSError):
                os.fstat(fd)
            os.fstat(writer)
            os.fstat(spare)
            assert not case.docker.created
        finally:
            os.close(spare)


def test_stale_boundary_is_refused_without_retiming(projection, prepared, tmp_path, monkeypatch):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        args = intent(case)
        actual = time.clock_gettime_ns
        # Simulate elapsed BOOTTIME without changing the original kernel timer
        # or plan. The original readiness bound is earlier than command expiry.
        end = case.custody.plan.deadlines.ready_by + 0.01
        monkeypatch.setattr(
            m.time,
            "clock_gettime_ns",
            lambda clock: (
                int(end * 1_000_000_000) if clock == time.CLOCK_BOOTTIME else actual(clock)
            ),
        )
        notice = m.platform.DispatchNotice(
            "before_create",
            "stopping_normal",
            args[1],
            case.custody.plan.boot,
            host_tests.CID,
            None,
            tuple(m.base.encode(e) for e in case.journal.entries),
        )
        denied(lambda: case.cli_observer.observe(notice))
        assert not case.docker.created


def test_observer_never_polls_engine_after_original_recovery_expiry(
    projection, prepared, tmp_path, monkeypatch
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        send(case)
        before = len(case.state.requests)
        # Status seam only; actual timerfd expiry is covered by DeadlineWatch's
        # real-kernel suite. This test verifies that this consumer honors it.
        original = case.cli_observer._guard
        monkeypatch.setattr(
            case.cli_observer,
            "_guard",
            lambda: replace(original(), deadline=m.deadlines.Status(False, True)),
        )
        status = case.cli_observer.poll()
        assert status.apps.deadline.recovery_deadline_expired
        assert status.executions[0].state == "created" and status.executions[0].exit_code is None
        assert len(case.state.requests) == before


@pytest.mark.parametrize("fault", ["partial", "extra", "mode", "symlink", "replace", "tail"])
def test_readonly_history_rejects_damage_without_repair(
    projection, prepared, tmp_path, monkeypatch, fault
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        args = intent(case)
        directory = case.journal.path
        target = directory / "0000.json"
        if fault == "partial":
            target.write_bytes(b'{"secret":')
        elif fault == "extra":
            (directory / "unexpected").touch()
        elif fault == "mode":
            target.chmod(0o644)
        elif fault == "symlink":
            target.rename(directory / "saved")
            target.symlink_to(directory / "saved")
        elif fault == "replace":
            raw = target.read_bytes()
            target.rename(directory / "saved")
            target.write_bytes(raw)
            target.chmod(0o600)
            (directory / "saved").rename(tmp_path / "saved-original")
        else:
            tail = directory / case.journal.name(len(case.journal.entries) - 1)
            entry = json.loads(tail.read_bytes())
            entry["previous"] = "0" * 64
            tail.write_bytes(m.base.encode(entry))
        before = sorted(os.listdir(directory))
        denied(lambda: case.dispatch(*args))
        assert sorted(os.listdir(directory)) == before
        assert not case.docker.created and not case.docker.started
        assert case.cli_observer.capture_failed
        assert case.custody.poll().normal_exited is False


def test_duplicate_capture_and_foreign_thread_cannot_acknowledge(
    projection, prepared, tmp_path, monkeypatch
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        denied(lambda: m.CliCustody(case.custody, projection))
        arguments = intent(case)
        notice = m.platform.DispatchNotice(
            "before_create",
            "stopping_normal",
            arguments[1],
            case.custody.plan.boot,
            host_tests.CID,
            None,
            tuple(m.base.encode(e) for e in case.journal.entries),
        )
        errors = []

        def wrong_owner():
            try:
                case.cli_observer.observe(notice)
            except m.UnconfirmedCliCustody as error:
                errors.append(error)

        worker = Thread(target=wrong_owner)
        worker.start()
        worker.join(timeout=3)
        assert not worker.is_alive() and len(errors) == 1
        denied(lambda: case.cli_observer.observe(notice))
        assert not case.docker.created


def test_observation_does_not_write_journal_or_acquire_lock(
    projection, prepared, tmp_path, monkeypatch
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        args = intent(case)
        notice = m.platform.DispatchNotice(
            "before_create",
            "stopping_normal",
            args[1],
            case.custody.plan.boot,
            host_tests.CID,
            None,
            tuple(m.base.encode(e) for e in case.journal.entries),
        )
        initial = tuple(case.journal.entries)

        def forbidden(*_args, **_kwargs):
            pytest.fail("Observer attempted mutation or journal lock acquisition")

        monkeypatch.setattr(m.bootstrap.Journal, "__init__", forbidden)
        monkeypatch.setattr(m.bootstrap.Journal, "append", forbidden)
        monkeypatch.setattr(m.base.fcntl, "flock", forbidden)
        assert case.cli_observer.observe(notice) == notice.receipt
        assert tuple(case.journal.entries) == initial
        assert not case.docker.created


def test_same_writer_and_observer_cover_all_four_fixed_app_actions(
    projection, prepared, tmp_path, monkeypatch
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        journal, observer, writer = case.journal, case.cli_observer, case.dispatch
        original_fd = journal.fd
        plan, baseline = case.custody.plan, case.baseline

        def event(kind, **fields):
            return journal.append(dict(kind=kind, boot_id=plan.boot, now=now(), **fields))

        def observed(normal, candidate):
            stamp = now()
            return journal.append(
                dict(
                    kind="observe",
                    boot_id=plan.boot,
                    now=stamp,
                    observation=asdict(
                        replace(baseline, sampled_at=stamp, normal=normal, candidate=candidate)
                    ),
                )
            )

        def dispatch_action(action):
            assert type(action) is m.base.Action
            command = ("ha", "apps", action.operation, action.slug, "--raw-json")
            writer(command, action.case_id)
            status = observer.poll()
            assert all(e.state == "not_running" for e in status.executions)
            writer.reconcile_executions()  # The same original writer, not the observer.
            assert journal.fd == original_fd

        send(case)
        observer.poll()
        writer.reconcile_executions()
        app_tests.exit_normal(case)
        event("process_exited", generation=plan.normal_generation)
        stopped = m.base.App(plan.normal.pin, "stopped")
        dispatch_action(observed(stopped, baseline.candidate))
        candidate = case.custody.capture_candidate(case.generations[m.base.CANDIDATE])
        event(
            "bind_process",
            process=asdict(
                m.base.ProcessRecord(
                    m.base.CANDIDATE,
                    candidate.generation,
                    candidate.process.container_id,
                    candidate.process.pid,
                    candidate.process.start_ticks,
                )
            ),
        )
        idle = m.base.App(plan.candidate.pin, "running", candidate.generation, None, None)
        assert observed(stopped, idle) is None
        assert journal.machine.state.phase == "candidate_idle"
        event("finish")
        dispatch_action(observed(stopped, idle))
        case.candidate.stdin.close()
        assert case.candidate.wait(timeout=3) == 0
        event("process_exited", generation=candidate.generation)
        dispatch_action(observed(stopped, baseline.candidate))
        assert [n.phase for n in case.receipts[::2]] == list(m.platform.CONTROL)
        assert len(set(e.binding.execution_id for e in observer.poll().executions)) == 4
        assert len(case.docker.started) == 4
        assert journal.machine.state.phase == "starting_normal"  # No invented restoration.
        assert journal.machine.state.recording_outcome == "not_attempted"
        assert len(journal.machine.state.completed_executions) == 4


@pytest.mark.parametrize("code", [-1, 256, False, "0"])
def test_invalid_terminal_exit_code_stays_unknown(
    projection, prepared, tmp_path, monkeypatch, code
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        send(case)
        case.docker.exec["ExitCode"] = code
        status = case.cli_observer.poll()
        assert status.inspection_failed
        assert status.executions[0].state == "created"
        assert status.executions[0].exit_code is None


@pytest.mark.parametrize("fault", ["tuple", "binding", "receipt"])
def test_retained_receipts_cannot_be_substituted(
    projection, prepared, tmp_path, monkeypatch, fault
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        send(case)
        observer = case.cli_observer
        if fault == "tuple":
            observer._executions = ()
        elif fault == "binding":
            object.__setattr__(observer._executions[0].binding, "execution_id", "e" * 64)
        else:
            object.__setattr__(observer._executions[0], "state", "not_running")
        denied(observer.poll)
        assert case.journal.machine.state.completed_executions == ()


@pytest.mark.parametrize("deadline", [0, True, "PRIVATE", float("nan"), float("inf")])
def test_containing_exchange_invalid_or_expired_deadline_refuses_without_engine_reads(
    projection, prepared, tmp_path, monkeypatch, deadline
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        intent(case)
        plan = case.custody.plan
        notice = m.platform.DispatchNotice(
            "before_create",
            "stopping_normal",
            plan.case,
            plan.boot,
            host_tests.CID,
            None,
            tuple(m.base.encode(e) for e in case.journal.entries),
        )
        requests = len(case.state.requests)
        denied(lambda: case.cli_observer.observe(notice, deadline=deadline))
        assert case.cli_observer.capture_failed and len(case.state.requests) == requests
        assert not case.docker.created and not case.docker.started


def test_containing_exchange_budget_is_never_extended(projection, prepared, tmp_path, monkeypatch):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        intent(case)
        plan = case.custody.plan
        notice = m.platform.DispatchNotice(
            "before_create",
            "stopping_normal",
            plan.case,
            plan.boot,
            host_tests.CID,
            None,
            tuple(m.base.encode(e) for e in case.journal.entries),
        )
        end = time.monotonic() + 0.5
        seen = []
        original = case.cli_observer._get

        def get(path, cutoff):
            seen.append(cutoff)
            return original(path, cutoff)

        monkeypatch.setattr(case.cli_observer, "_get", get)
        assert case.cli_observer.observe(notice, deadline=end) == notice.receipt
        assert seen and all(cutoff == end for cutoff in seen)


def test_readonly_directory_in_separate_process_never_shares_writer_lock(tmp_path):
    from . import test_supplemental_recording_bootstrap as bootstrap_tests

    directory = tmp_path / "separate-writer"
    directory.mkdir(mode=0o700)
    code = """
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import supplemental_recording_bootstrap as b
with b.Journal(pathlib.Path(sys.argv[2])) as journal:
    journal.append(json.loads(sys.stdin.readline()))
    print('prepared', flush=True)
    journal.append(json.loads(sys.stdin.readline()))
    print('requested', flush=True)
    sys.stdin.read()
"""
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", code, str(Path(m.__file__).parent), str(directory)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    history = None
    try:
        process.stdin.write(m.base.encode(bootstrap_tests.prepared()) + b"\n")
        process.stdin.flush()
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline() == b"prepared\n"
        history = m._History(directory)
        original = history.read(time.monotonic() + 2)
        assert len(original) == 1
        with pytest.raises(BlockingIOError):
            m.bootstrap.Journal(directory)
        process.stdin.write(
            m.base.encode(dict(kind="request", boot_id=bootstrap_tests.recording.old.BOOT, now=11))
            + b"\n"
        )
        process.stdin.flush()
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline() == b"requested\n"
        updated = history.read(time.monotonic() + 2)
        assert len(updated) == 2 and updated[:1] == original
        process.stdin.close()
        assert process.wait(timeout=5) == 0
        assert history.read(time.monotonic() + 2) == updated
        # The observer retained no writer lock across actual writer exit. This
        # test-only reopen proves that fact; product custody never reopens one.
        with m.bootstrap.Journal(directory) as reopened:
            assert reopened.machine.state.phase == "requested"
    finally:
        if history is not None:
            history.close()
        if process.poll() is None:
            process.kill()  # Only this owned disposable test writer.
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
