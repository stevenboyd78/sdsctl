"""Actual Unix/Ready/retained pidfds; synthetic Engine/host/namespace facts.

No installed Docker/HA recovery or recording completion is claimed. Test signals
target only original pidfds of our disposable child-process fixture.
"""

import hashlib
import importlib.util
import json
import os
import select
import signal
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_host_plan as plan_tests
from . import test_supplemental_recording_ready as ready_tests

NAME = "supplemental_recording_reconcile"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(ready_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
joined = ready_tests.joined
transport = joined.engine
layout, tree, routing, projection, binding, prepared, family, actors, calibration = (
    ready_tests.layout,
    ready_tests.tree,
    ready_tests.routing,
    ready_tests.projection,
    ready_tests.binding,
    ready_tests.prepared,
    ready_tests.family,
    ready_tests.actors,
    ready_tests.calibration,
)


@pytest.fixture
def directory(tmp_path):
    path = tmp_path / "operator-exec"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def plan(prepared, calibration, monkeypatch):
    _, value = plan_tests.projected_plan(prepared.pins.host.projection)
    original = calibration.original
    now = original.boottime_ns / m.plans.clock.NS
    value.update(
        boot=original.boot,
        original_clock=asdict(original) | {"namespace": list(original.namespace)},
        deadlines=dict(issued_at=now, ready_by=now + 120, stop_by=now + 400, recover_by=now + 1500),
    )
    plan = m.plans.decode(value)
    monkeypatch.setattr(m.plans.Plan, "root", property(lambda _: prepared.directory.parent))
    (plan.root / "journal").mkdir(mode=0o700)
    prepared.pins = replace(
        prepared.pins,
        host=replace(
            prepared.pins.host,
            plan_sha256=plan.sha256,
            source_sha256=plan.candidate_runtime.source,
        ),
        command=replace(
            prepared.pins.command,
            plan=str(plan.native_root / "launch/launch.json"),
            source_sha256=plan.candidate_runtime.source,
            ready_by=plan.lease["ready_by"],
        ),
    )
    return plan


@pytest.fixture
def journal(prepared, plan):
    """Real journal3 bytes; baseline/App/Core/CLI/idle observations are fixtures."""
    base, policy = m.plans.base, m.plans.bootstrap.recording
    normal = base.App(plan.normal.pin, "running", plan.normal_generation, True, False)
    stopped_normal = base.App(plan.normal.pin, "stopped")
    candidate = base.App(plan.candidate.pin, "stopped")
    idle = base.App(plan.candidate.pin, "running", prepared.pins.generation, None, None)
    files = policy.Files(
        plan.candidate.contract.sha256, "pristine", plan.candidate.contract.baseline_sha256
    )

    def now():
        return m.plans.clock.read().boottime_ns / m.plans.clock.NS

    def observation(a=stopped_normal, c=idle, at=None):
        return policy.Observation(now() if at is None else at, a, c, True, True, True, files)

    with m.plans.bootstrap.Journal(plan.root / "journal") as journal:
        baseline = observation(normal, candidate, plan.deadlines.issued_at)
        journal.append(plan.preparation(baseline, prepared.pins.host.projection))

        def append(kind, **values):
            return journal.append(dict(kind=kind, boot_id=plan.boot, now=now(), **values))

        append(
            "bind_process",
            process=asdict(base.ProcessRecord(base.NORMAL, normal.generation, "8" * 64, 1234, 100)),
        )
        append("request")
        assert (
            append("observe", observation=asdict(observation(normal, candidate))).slug
            == base.NORMAL
        )
        append("bind_execution", container_id="a" * 64, execution_id="1" * 64)
        append("execution_completed", execution_id="1" * 64, exit_code=0)
        append("process_exited", generation=normal.generation)
        assert (
            append("observe", observation=asdict(observation(c=candidate))).slug == base.CANDIDATE
        )
        append("bind_execution", container_id="a" * 64, execution_id="2" * 64)
        append("execution_completed", execution_id="2" * 64, exit_code=0)
        init = prepared.pins.init
        append(
            "bind_process",
            process=asdict(
                base.ProcessRecord(
                    base.CANDIDATE, idle.generation, init.container_id, init.pid, init.start_ticks
                )
            ),
        )
        append("observe", observation=asdict(observation()))
        assert journal.machine.state.phase == "candidate_idle"
        append(
            "authorize_operator",
            generation=idle.generation,
            bootstrap_sha256=plan.bootstrap.sha256,
            launch_plan_sha256=prepared.pins.command.plan_sha256,
            idle_evidence_sha256="f" * 64,
            observation=asdict(observation()),
        )
        assert journal.machine.state.phase == "starting_operator"
        yield journal


@pytest.mark.parametrize("code", [0, 70, 137])
def test_actual_exit_publication_changes_only_finish_and_operator_exit(
    prepared, actors, calibration, plan, journal, family, monkeypatch, code
):
    with captured(prepared, actors, calibration, plan, monkeypatch, code=code) as case:
        before = journal.machine.state
        originals = {path: path.read_bytes() for path in journal.path.iterdir()}
        case.ready.close()
        finish(family)
        result = case.holder.poll()
        assert case.holder.publish(journal) == result.sha256
        assert journal.machine.state == replace(
            before, operator_exit_sha256=result.sha256, finish_requested=True
        )
        assert journal.machine.state.authorization_generation is None
        assert journal.machine.state.ready_evidence_sha256 is None
        assert len(list(journal.path.iterdir())) == len(originals) + 1
        assert all(path.read_bytes() == raw for path, raw in originals.items())
        count = len(journal.entries)
        with pytest.raises(m.UnconfirmedReconciliation):
            case.holder.publish(journal)
        assert len(journal.entries) == count and len(case.requests) == 7


@pytest.mark.parametrize(
    "fault", ["still_live", "receipt", "history", "directory", "review", "late"]
)
def test_no_uncertainty_or_review_can_publish_or_revive_the_case(
    prepared, actors, calibration, plan, journal, family, monkeypatch, fault
):
    with captured(prepared, actors, calibration, plan, monkeypatch) as case:
        if fault != "still_live":
            case.ready.close()
            finish(family)
            case.holder.poll()
        if fault == "receipt":
            case.holder.result = replace(case.holder.result, returncode=0)
        elif fault == "history":
            (journal.path / "0000.json").write_bytes(b"{}")
        elif fault == "directory":
            original = journal.path.with_name("retained-journal")
            journal.path.rename(original)
            journal.path.mkdir(mode=0o700)
            for path in original.iterdir():
                destination = journal.path / path.name
                destination.write_bytes(path.read_bytes())
                destination.chmod(0o600)
        elif fault in ("review", "late"):
            read = m.plans.clock.read
            shift = (121 if fault == "review" else 3) * m.plans.clock.NS

            def later():
                value = read()
                return replace(
                    value,
                    before_ns=value.before_ns + shift,
                    after_ns=value.after_ns + shift,
                    boottime_ns=value.boottime_ns + shift,
                )

            if fault == "review":
                journal.append(
                    dict(kind="tick", boot_id=plan.boot, now=later().boottime_ns / m.plans.clock.NS)
                )
                assert journal.machine.state.phase == "review"
            else:
                monkeypatch.setattr(m.plans.clock, "read", later)
        count, state = len(journal.entries), journal.machine.state
        with pytest.raises(m.UnconfirmedReconciliation):
            case.holder.publish(journal)
        assert case.holder.failed and not case.holder.closed
        assert journal.machine.state == state and len(journal.entries) == count
        assert journal.machine.state.operator_exit_sha256 is None


def test_lost_exit_journal_fsync_return_preserves_consumed_case_no_retry(
    prepared, actors, calibration, plan, journal, family, monkeypatch
):
    with captured(prepared, actors, calibration, plan, monkeypatch) as case:
        case.ready.close()
        finish(family)
        evidence = case.holder.poll()
        real_fsync, calls = os.fsync, []

        def lost(fd):
            real_fsync(fd)
            calls.append(fd)
            if len(calls) == 2:
                raise OSError("PRIVATE exit publication acknowledgement lost")

        with monkeypatch.context() as patch:
            patch.setattr(m.os, "fsync", lost)
            with pytest.raises(m.UnconfirmedReconciliation):
                case.holder.publish(journal)
        assert journal.fd == -1 and case.holder.publish_attempted and case.holder.failed
        assert not case.holder.closed
        with m.plans.bootstrap.Journal(plan.root / "journal") as replay:
            assert replay.machine.state.operator_exit_sha256 == evidence.sha256
            count = len(replay.entries)
            with pytest.raises(m.UnconfirmedReconciliation):
                case.holder.publish(replay)
            assert len(replay.entries) == count
        assert len(case.requests) == 7


@contextmanager
def captured(
    prepared, actors, calibration, plan, monkeypatch, *, code=70, fault=None, sender=False
):
    live = joined.live(prepared, actors)
    final = dict(live, Running=False, ExitCode=code)
    if fault is not None:
        fault(final)
    handlers = [transport.reply(live)] * 3 + [transport.reply(final)]
    with joined.ready(
        prepared,
        actors,
        monkeypatch,
        consume=False,
        message=lambda: ready_tests.envelope(prepared, actors),
        finish_extra=prepared.pins.host.projection.native.contract.maximum_recording_seconds + 3,
        responses=handlers,
        sender_credentials=sender,
    ) as (client, requests):
        ready = ready_tests.capture(client, calibration)
        endpoint = m.engine.Endpoint(sender_credentials=sender)
        holder = None
        try:
            holder = m.Operator(plan, ready, endpoint)
            yield SimpleNamespace(ready=ready, endpoint=endpoint, holder=holder, requests=requests)
        finally:
            if holder is not None:
                holder.close()
            endpoint.close()
            ready.close()


def finish(family):
    family.process.stdin.close()
    family.process.wait(timeout=3)


def refused(holder):
    handles = dict(holder.handles)
    with pytest.raises(m.UnconfirmedReconciliation) as caught:
        holder.poll()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__
    assert holder.failed and not holder.closed and holder.handles == handles
    for fd in handles.values():
        os.fstat(fd)  # Refusal retains all evidence until explicit cleanup.


@pytest.mark.parametrize("sender", [False, True])
@pytest.mark.parametrize("code", [0, 70, 137])
def test_lost_transport_original_exits_separate_engine_result_never_completion(
    prepared, actors, calibration, plan, family, monkeypatch, sender, code
):
    with captured(
        prepared, actors, calibration, plan, monkeypatch, code=code, sender=sender
    ) as case:
        holder = case.holder
        assert len(case.requests) == 6 and holder.poll() is None
        original_files = {path: path.read_bytes() for path in prepared.directory.iterdir()}
        assert all(holder.handles[role] != case.ready.processes.handles[role] for role in m.ROLES)
        case.ready.failed = True
        case.ready.close()  # No longer owns/keeps ANY of the reconciliation handles.
        assert holder.poll() is None and len(case.requests) == 6
        finish(family)
        result = holder.poll()
        assert type(result) is m.Evidence and result.returncode == code
        assert result.init_exited is False and not prepared.witness.exited()
        assert result.plan_sha256 == plan.sha256 and result.generation == prepared.pins.generation
        assert result.execution_id == transport.attached.EXEC and len(result.sha256) == 64
        assert not hasattr(result, "completion") and not hasattr(result, "restored")
        assert len(case.requests) == 7 and not case.endpoint.closed
        assert case.requests[-1][0] == f"GET /v1.47/exec/{result.execution_id}/json HTTP/1.1"
        assert case.requests[-1][2] is None
        assert original_files == {path: path.read_bytes() for path in prepared.directory.iterdir()}
        refused(holder)  # No replay or second terminal inspect.
        assert len(case.requests) == 7


def test_init_exit_cannot_substitute_for_any_worker_exit(
    prepared, actors, calibration, plan, family, monkeypatch
):
    with captured(prepared, actors, calibration, plan, monkeypatch) as case:
        case.ready.close()
        prepared.process.stdin.close()
        prepared.process.wait(timeout=3)
        assert prepared.witness.exited()
        assert case.holder.poll() is None and len(case.requests) == 6
        finish(family)
        result = case.holder.poll()
        assert result.init_exited is True and result.returncode == 70


def test_frozen_live_worker_never_becomes_exit_evidence(
    prepared, actors, calibration, plan, family, monkeypatch
):
    with captured(prepared, actors, calibration, plan, monkeypatch) as case:
        case.ready.close()
        fd = case.holder.handles["native"]
        signal.pidfd_send_signal(fd, signal.SIGSTOP)
        # Wait for the exact test child to enter T without changing its identity.
        end = time.monotonic() + 2
        while (
            Path(f"/proc/{actors.values[2].host_pid}/stat")
            .read_text()
            .rpartition(") ")[2]
            .split()[0]
            != "T"
        ):
            assert time.monotonic() < end
            time.sleep(0.005)
        assert not select.select([fd], [], [], 0)[0]
        refused(case.holder)
        assert len(case.requests) == 6


@pytest.mark.parametrize(
    "fault", ["shared_endpoint", "not_running", "wrong_guardian", "plan_digest"]
)
def test_capture_refusal_releases_only_duplicates_and_keeps_original_ready(
    prepared, actors, calibration, plan, monkeypatch, fault
):
    live = joined.live(prepared, actors)
    wrong = dict(live)
    if fault == "not_running":
        wrong.update(Running=False, ExitCode=70)
    elif fault == "wrong_guardian":
        wrong["Pid"] = actors.values[2].host_pid
    with joined.ready(
        prepared,
        actors,
        monkeypatch,
        consume=False,
        message=lambda: ready_tests.envelope(prepared, actors),
        finish_extra=prepared.pins.host.projection.native.contract.maximum_recording_seconds + 3,
        responses=[transport.reply(live)] * 2 + [transport.reply(wrong)],
    ) as (client, requests):
        ready = ready_tests.capture(client, calibration)
        endpoint = m.engine.Endpoint()
        duplicates = []
        duplicate = os.dup

        def tracked(fd):
            copied = duplicate(fd)
            duplicates.append(copied)
            return copied

        try:
            monkeypatch.setattr(m.os, "dup", tracked)
            argument = plan
            if fault == "plan_digest":
                # A different valid original plan is still the wrong launch.
                supplied = m.plans.base.encode(json.loads(plan.raw) | {"source": "f" * 40})
                argument = m.plans.load_bytes(supplied, hashlib.sha256(supplied).hexdigest())
            with pytest.raises(m.UnconfirmedReconciliation):
                m.Operator(
                    argument, ready, client.endpoint if fault == "shared_endpoint" else endpoint
                )
            for fd in duplicates:
                with pytest.raises(OSError):
                    os.fstat(fd)
            assert not endpoint.closed and not ready.failed and not ready.closed
            ready.check_before_begin()
            assert len(requests) == (6 if fault in ("not_running", "wrong_guardian") else 5)
        finally:
            endpoint.close()
            ready.close()


@pytest.mark.parametrize("fault", ["id", "container", "pid", "running", "code_bool", "argv"])
def test_exact_terminal_engine_metadata_is_still_required(
    prepared, actors, calibration, plan, family, monkeypatch, fault
):
    def change(value):
        if fault == "id":
            value["ID"] = "f" * 64
        elif fault == "container":
            value["ContainerID"] = "f" * 64
        elif fault == "pid":
            value["Pid"] = actors.values[2].host_pid
        elif fault == "running":
            value.update(Running=True, ExitCode=None)
        elif fault == "code_bool":
            value["ExitCode"] = False
        else:
            value["ProcessConfig"] = dict(value["ProcessConfig"], arguments=["PRIVATE"])

    with captured(prepared, actors, calibration, plan, monkeypatch, fault=change) as case:
        case.ready.close()
        finish(family)
        refused(case.holder)
        assert len(case.requests) == 7


@pytest.mark.parametrize("fault", ["history", "directory", "clock", "namespace", "peer"])
def test_original_provenance_not_replaced_after_transport_loss(
    prepared, actors, calibration, plan, family, monkeypatch, fault
):
    with captured(prepared, actors, calibration, plan, monkeypatch) as case:
        case.ready.close()
        finish(family)
        if fault == "history":
            (prepared.directory / "0002.json").write_bytes(b"{}\n")
        elif fault == "directory":
            original = prepared.directory.with_name("retained-original")
            prepared.directory.rename(original)
            prepared.directory.mkdir(mode=0o700)
            for path in original.iterdir():
                copy = prepared.directory / path.name
                copy.write_bytes(path.read_bytes())
                copy.chmod(0o600)
        elif fault == "clock":
            calibration.offset += 100
        elif fault == "namespace":
            monkeypatch.setattr(
                m.engine.namespace.Witness, "_host_domains", staticmethod(lambda: ((1, 2), (3, 4)))
            )
        else:
            case.holder.peer = ("changed",)
        refused(case.holder)
        assert len(case.requests) == 6


def test_poll_after_original_recovery_deadline_cannot_refresh_budget(
    prepared, actors, calibration, plan, family, monkeypatch
):
    with captured(prepared, actors, calibration, plan, monkeypatch) as case:
        case.ready.close()
        finish(family)
        original = m.plans.clock.read

        def late():
            value = original()
            shift = 1600 * m.plans.clock.NS
            return replace(
                value,
                before_ns=value.before_ns + shift,
                after_ns=value.after_ns + shift,
                boottime_ns=value.boottime_ns + shift,
            )

        monkeypatch.setattr(m.plans.clock, "read", late)
        refused(case.holder)
        assert len(case.requests) == 6
