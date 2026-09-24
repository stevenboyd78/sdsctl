"""Fresh retained exit checks, not completion or restoration permission.

Actual Unix requests, original pidfds and durable dispatch files; Engine,
namespace and installed host facts remain the explicit local test fixtures.
"""

import os
from dataclasses import replace

import pytest

from . import test_supplemental_recording_reconcile as exits

m = exits.m
(
    layout,
    tree,
    routing,
    projection,
    binding,
    directory,
    prepared,
    family,
    actors,
    calibration,
    plan,
) = (
    exits.layout,
    exits.tree,
    exits.routing,
    exits.projection,
    exits.binding,
    exits.directory,
    exits.prepared,
    exits.family,
    exits.actors,
    exits.calibration,
    exits.plan,
)
journal = exits.journal


@pytest.mark.parametrize("code", [0, 70, 137])
@pytest.mark.parametrize("sender", [False, True])
def test_recheck_retains_original_receipt_and_custody_without_republication(
    prepared, actors, calibration, plan, family, journal, monkeypatch, code, sender
):
    with exits.captured(
        prepared,
        actors,
        calibration,
        plan,
        monkeypatch,
        code=code,
        sender=sender,
        terminal_replies=4,
    ) as case:
        holder = case.holder
        case.ready.close()
        exits.finish(family)
        original = holder.poll()
        assert holder.publish(journal) == original.sha256
        handles, identities = dict(holder.handles), dict(holder.identities)
        files = {p: p.read_bytes() for p in prepared.directory.iterdir()}
        entries, state = tuple(journal.entries), journal.machine.state

        def forbidden(*_, **__):
            pytest.fail("Read-only rechecking must not repoll, publish, fsync or signal")

        monkeypatch.setattr(m.Operator, "poll", forbidden)
        monkeypatch.setattr(m.Operator, "publish", forbidden)
        monkeypatch.setattr(m.os, "fsync", forbidden)
        for _ in range(2):
            assert holder.recheck() is original
        # Init may exit later, but the ORIGINAL result is neither refreshed nor
        # promoted to init-exit/restoration evidence by these checks.
        prepared.process.stdin.close()
        prepared.process.wait(timeout=3)
        assert holder.recheck() is original and original.init_exited is False
        assert holder.result is original and holder.result_sha256 == original.sha256
        assert holder.handles == handles and holder.identities == identities
        assert holder.done and holder.publish_attempted and not holder.failed
        assert tuple(journal.entries) == entries and journal.machine.state == state
        assert files == {p: p.read_bytes() for p in prepared.directory.iterdir()}
        assert len(case.requests) == 10
        assert all(item[0].startswith("GET ") and item[2] is None for item in case.requests[6:])


def refused(holder):
    handles = dict(holder.handles)
    with pytest.raises(m.UnconfirmedReconciliation) as caught:
        holder.recheck()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__
    assert holder.failed and holder.handles == handles
    for fd in handles.values():
        os.fstat(fd)
    with pytest.raises(m.UnconfirmedReconciliation):
        holder.recheck()  # Failure cannot renew the observation.


@pytest.mark.parametrize(
    "fault",
    [
        "not_polled",
        "copied",
        "changed",
        "mutated",
        "failed",
        "done",
        "owner",
        "endpoint",
        "history",
        "namespace",
        "clock",
        "expired",
        "handle",
        "plan_deadline",
        "original_clock",
    ],
)
def test_terminal_recheck_refuses_lost_or_replaced_original_provenance(
    prepared, actors, calibration, plan, family, monkeypatch, fault
):
    with exits.captured(
        prepared, actors, calibration, plan, monkeypatch, terminal_replies=2
    ) as case:
        case.ready.close()
        exits.finish(family)
        holder = case.holder
        owner = m.get_ident
        if fault != "not_polled":
            original = holder.poll()
        if fault in ("copied", "changed"):
            holder.result = (
                replace(original, returncode=0) if fault == "changed" else replace(original)
            )
            holder.result_sha256 = holder.result.sha256
        elif fault == "mutated":
            object.__setattr__(original, "returncode", 0)
            holder.result_sha256 = original.sha256
        elif fault in ("failed", "done"):
            setattr(holder, fault, fault == "failed")
        elif fault == "owner":
            monkeypatch.setattr(m, "get_ident", lambda: -1)
        elif fault == "endpoint":
            case.endpoint.close()
        elif fault == "history":
            (prepared.directory / "0002.json").write_bytes(b"{}")
        elif fault == "namespace":
            monkeypatch.setattr(
                m.engine.namespace.Witness, "_host_domains", staticmethod(lambda: ((1, 2), (3, 4)))
            )
        elif fault == "clock":
            calibration.offset += 100
        elif fault == "expired":
            read = m.plans.clock.read

            def late():
                value = read()
                shift = 1600 * m.plans.clock.NS
                return replace(
                    value,
                    before_ns=value.before_ns + shift,
                    after_ns=value.after_ns + shift,
                    boottime_ns=value.boottime_ns + shift,
                )

            monkeypatch.setattr(m.plans.clock, "read", late)
        elif fault == "handle":
            with open(os.devnull, "rb") as other:
                os.dup2(other.fileno(), holder.handles["native"])
        elif fault == "plan_deadline":
            object.__setattr__(plan.deadlines, "recover_by", plan.deadlines.recover_by + 100)
        elif fault == "original_clock":
            holder.clock = replace(plan.original_clock)
        refused(holder)
        assert len(case.requests) == (6 if fault == "not_polled" else 7)
        # Permit only explicit fixture cleanup on its real owning thread.
        monkeypatch.setattr(m, "get_ident", owner)


@pytest.mark.parametrize("fault", ["code", "pid", "running", "id", "container", "argv"])
def test_fresh_engine_read_cannot_change_terminal_execution(
    prepared, actors, calibration, plan, family, monkeypatch, fault
):
    def change(value):
        if fault == "code":
            value["ExitCode"] = 0
        elif fault == "pid":
            value["Pid"] = actors.values[2].host_pid
        elif fault == "running":
            value.update(Running=True, ExitCode=None)
        elif fault in ("id", "container"):
            value["ID" if fault == "id" else "ContainerID"] = "f" * 64
        else:
            value["ProcessConfig"] = dict(value["ProcessConfig"], arguments=["PRIVATE"])

    with exits.captured(
        prepared, actors, calibration, plan, monkeypatch, terminal_replies=2, recheck_fault=change
    ) as case:
        case.ready.close()
        exits.finish(family)
        original = case.holder.poll()
        refused(case.holder)
        assert case.holder.result is original and len(case.requests) == 8


@pytest.mark.parametrize("fault", ["history", "receipt", "clock", "slow", "workers", "peer"])
def test_recheck_brackets_terminal_inspection_and_keeps_original_time_limit(
    prepared, actors, calibration, plan, family, monkeypatch, fault
):
    with exits.captured(
        prepared, actors, calibration, plan, monkeypatch, terminal_replies=2
    ) as case:
        case.ready.close()
        exits.finish(family)
        holder = case.holder
        original = holder.poll()
        inspect = holder._inspect

        def changed(end):
            value = inspect(end)
            if fault == "history":
                (prepared.directory / "0002.json").write_bytes(b"{}")
            elif fault == "receipt":
                holder.result = replace(original)
            elif fault == "clock":
                calibration.offset += 100
            elif fault == "slow":
                read = holder._clock
                monkeypatch.setattr(holder, "_clock", lambda: read() + 3)
            elif fault == "workers":
                monkeypatch.setattr(holder, "_actors", lambda: frozenset({"native", "watchdog"}))
            else:
                holder.peer = ("changed",)
            return value

        monkeypatch.setattr(holder, "_inspect", changed)
        refused(holder)
        assert len(case.requests) == 8


def test_rechecking_old_exit_never_refreshes_publication_window(
    prepared, actors, calibration, plan, family, journal, monkeypatch
):
    with exits.captured(
        prepared, actors, calibration, plan, monkeypatch, terminal_replies=2
    ) as case:
        case.ready.close()
        exits.finish(family)
        holder = case.holder
        original = holder.poll()
        read = m.plans.clock.read

        def later():
            value = read()
            shift = 3 * m.plans.clock.NS
            return replace(
                value,
                before_ns=value.before_ns + shift,
                after_ns=value.after_ns + shift,
                boottime_ns=value.boottime_ns + shift,
            )

        monkeypatch.setattr(m.plans.clock, "read", later)
        assert holder.recheck() is original
        assert not holder.publish_attempted
        before, count = journal.machine.state, len(journal.entries)
        with pytest.raises(m.UnconfirmedReconciliation):
            holder.publish(journal)
        assert holder.publish_attempted and holder.failed
        assert journal.machine.state == before and len(journal.entries) == count
        assert len(case.requests) == 8


def test_equal_copied_exit_cannot_publish_even_with_matching_digest(
    prepared, actors, calibration, plan, family, journal, monkeypatch
):
    with exits.captured(prepared, actors, calibration, plan, monkeypatch) as case:
        case.ready.close()
        exits.finish(family)
        holder = case.holder
        original = holder.poll()
        holder.result = replace(original)
        assert holder.result == original and holder.result_sha256 == original.sha256
        before, count = journal.machine.state, len(journal.entries)
        with pytest.raises(m.UnconfirmedReconciliation):
            holder.publish(journal)
        assert holder.failed and holder.publish_attempted
        assert journal.machine.state == before and len(journal.entries) == count
        assert len(case.requests) == 7


def test_closed_observer_cannot_reacquire_custody_or_query_engine(
    prepared, actors, calibration, plan, family, monkeypatch
):
    with exits.captured(prepared, actors, calibration, plan, monkeypatch) as case:
        case.ready.close()
        exits.finish(family)
        case.holder.poll()
        case.holder.close()
        refused(case.holder)
        assert case.holder.closed and case.holder.handles == {}
        assert len(case.requests) == 7
