"""Original exits + prepared ledger, never fabricated recording authorization.

Real child pidfds/Unix transport/files/journal; synthetic Engine/namespace/App
metadata. These read-only checks neither install nor qualify service recovery.
"""

import json
import os
import time
from dataclasses import asdict, replace
from threading import Lock, Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_reconcile as exits

m, b = exits.m, exits.m.dispatch.binding
p = b.protected
layout, tree, routing, projection, binding, prepared, family, actors, calibration = (
    exits.layout,
    exits.tree,
    exits.routing,
    exits.projection,
    exits.binding,
    exits.prepared,
    exits.family,
    exits.actors,
    exits.calibration,
)
directory, plan, journal = exits.directory, exits.plan, exits.journal


@pytest.fixture
def unstarted(prepared, actors, calibration, plan, journal, family, tree, monkeypatch, request):
    mode = getattr(request, "param", "ready")
    ledger_path, progress_path = plan.root / "recording-ledger", plan.root / "recording-progress"
    ledger_path.mkdir(mode=0o700)
    progress_path.mkdir(mode=0o700)
    with exits.captured(
        prepared, actors, calibration, plan, monkeypatch, terminal_replies=40
    ) as case:
        operator = case.holder

        def event(kind, **values):
            return journal.append(
                dict(kind=kind, boot_id=plan.boot, now=operator._clock(), **values)
            )

        def observation():
            return m.plans.bootstrap.recording.Observation(
                operator._clock(),
                m.plans.base.App(plan.normal.pin, "stopped"),
                m.plans.base.App(
                    plan.candidate.pin, "running", operator.pins.generation, True, False
                ),
                True,
                True,
                True,
                p.Files(
                    plan.candidate.contract.sha256,
                    "pristine",
                    plan.candidate.contract.baseline_sha256,
                ),
            )

        if mode != "unpublished_ready":
            event(
                "operator_ready",
                generation=operator.pins.generation,
                intent_sha256=journal.machine.state.launch_intent_sha256,
                ready_evidence_sha256=operator.ready_sha256,
                received_at=operator._clock(),
                observation=asdict(observation()),
            )
        if mode == "authorized":
            event(
                "authorize_recording",
                generation=operator.pins.generation,
                contract_sha256=plan.candidate.contract.sha256,
                observation=asdict(observation()),
            )
        ledger_now = time.monotonic()
        if mode == "old_preparation":
            ledger_now = plan.original_clock.before_ns / m.plans.clock.NS - 1
        elif mode == "future_preparation":
            ledger_now += 100
        ledger = b.Ledger(ledger_path, operator.pins.host, now=ledger_now)
        if mode in ("intent", "abandoned"):
            now = time.monotonic()
            ledger.start_intent(
                now=now,
                generation=operator.pins.generation,
                authorization_sha256="a" * 64,
                start_by=now + 5,
                finish_by=now + 30,
            )
            if mode == "abandoned":
                ledger.abandon(now=time.monotonic())
        original_open = p.evidence.opened_root
        original_inventory = p.evidence.inventory

        def open_root(path, *, deadline):
            assert path == operator.pins.host.projection.host.baseline.root
            return original_open(tree.root, deadline=deadline)

        def inventory(path, **kwargs):
            assert path == operator.pins.host.projection.host.baseline.root
            return original_inventory(tree.root, **kwargs)

        # Only namespace routing is synthetic. Inventory/file bytes are freshly
        # read from the exact original disposable tree, never a cached result.
        monkeypatch.setattr(p.evidence, "opened_root", open_root)
        monkeypatch.setattr(p.evidence, "inventory", inventory)
        case.ready.close()
        if mode != "workers_live":
            exits.finish(family)
            operator.poll()
            if mode != "exit_unpublished":
                operator.publish(journal)
        if mode != "init_live":
            prepared.process.stdin.close()
            prepared.process.wait(timeout=3)
            assert prepared.witness.exited()
        if mode != "init_unpublished":
            # init_live deliberately lies to policy; real pidfd must still refuse.
            event("process_exited", generation=operator.pins.generation)
        yield SimpleNamespace(
            operator=operator,
            ledger=ledger,
            journal=journal,
            plan=plan,
            tree=tree,
            progress=progress_path,
            case=case,
            event=event,
            observation=observation,
        )


def reader(c):
    return m.NeverAuthorized(c.operator, c.ledger, c.journal)


def originals(c):
    return {
        path: path.read_bytes()
        for directory in (c.ledger.directory, c.journal.path, c.progress, c.tree.root)
        for path in directory.iterdir()
        if path.is_file()
    }


def denied(call):
    with pytest.raises(m.UnconfirmedReconciliation) as caught:
        call()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


@pytest.mark.parametrize("unstarted", ["ready", "unpublished_ready"], indirect=True)
def test_exited_native_without_recording_permission_only_proves_pristine_files(unstarted):
    c = unstarted
    before, state, result = originals(c), c.ledger.state, c.operator.result
    r = reader(c)
    collected = r.read()
    assert collected.files.stage == "pristine"
    assert collected.files.generation is collected.progress is collected.artifact is None
    assert r.read() == collected
    assert originals(c) == before and c.ledger.state is state
    assert c.operator.result is result and not result.init_exited
    assert c.journal.machine.state.recording_outcome == "not_attempted"
    assert c.journal.machine.state.authorization_generation is None
    r.close()
    denied(r.read)
    assert not c.operator.closed and not c.ledger._poisoned and c.journal.fd >= 0
    assert not c.case.endpoint.closed


@pytest.mark.parametrize(
    "unstarted",
    [
        "authorized",
        "intent",
        "abandoned",
        "workers_live",
        "exit_unpublished",
        "init_live",
        "init_unpublished",
        "old_preparation",
        "future_preparation",
    ],
    indirect=True,
)
def test_missing_actual_exits_or_any_recording_intent_refuses(unstarted):
    before = originals(unstarted)
    denied(lambda: reader(unstarted))
    assert originals(unstarted) == before and not unstarted.operator.closed


@pytest.mark.parametrize(
    "fault",
    [
        "ledger_poisoned",
        "ledger_directory",
        "ledger_bytes",
        "ledger_tail",
        "ledger_state",
        "ledger_lock_replaced",
        "ledger_binding",
        "progress_directory",
        "progress_tail",
        "journal_directory",
        "journal_cache",
        "journal_tail",
        "history",
        "endpoint",
        "plan",
        "collector",
        "operator_closed",
        "operator_receipt",
        "collected_receipt",
        "media_added",
        "media_changed",
        "ledger_locked",
        "reader_locked",
        "thread",
        "expired",
    ],
)
def test_changed_inputs_preserve_evidence_and_refuse_without_retry(unstarted, monkeypatch, fault):
    c, r = unstarted, reader(unstarted)
    r.read()
    if fault == "ledger_poisoned":
        c.ledger._poisoned = True
    elif fault.endswith("directory"):
        path = {
            "ledger_directory": c.ledger.directory,
            "progress_directory": c.progress,
            "journal_directory": c.journal.path,
        }[fault]
        path.rename(path.with_name(path.name + "-retained"))
        path.mkdir(mode=0o700)
    elif fault == "ledger_bytes":
        (c.ledger.directory / "0000.json").write_bytes(b"PRIVATE invalid")
    elif fault.endswith("tail"):
        path = {
            "ledger_tail": c.ledger.directory,
            "progress_tail": c.progress,
            "journal_tail": c.journal.path,
        }[fault]
        (path / "unacknowledged").write_bytes(b"PRIVATE unacknowledged")
    elif fault == "ledger_state":
        c.ledger.state = replace(c.ledger.state)
    elif fault == "ledger_lock_replaced":
        c.ledger._lock = Lock()
    elif fault == "ledger_binding":
        c.ledger.binding = replace(c.ledger.binding, source_sha256="f" * 64)
    elif fault == "journal_cache":
        c.journal.machine.state = replace(c.journal.machine.state, recording_outcome="verified")
    elif fault == "history":
        r.history = ()
    elif fault == "endpoint":
        c.operator.endpoint = c.case.ready.client.endpoint
    elif fault == "plan":
        c.operator.plan = replace(c.plan)
    elif fault == "collector":
        r.collector._stored = replace(r.collector.stored)
    elif fault == "operator_closed":
        c.operator.close()
    elif fault == "operator_receipt":
        c.operator.result = replace(c.operator.result)
    elif fault == "collected_receipt":
        r.collected = replace(r.collected)
    elif fault == "media_added":
        c.tree.wav.write_bytes(b"PRIVATE unexpected recording")
    elif fault == "media_changed":
        assert c.tree.baseline.files  # This is an actual original baseline file.
        next(path for path in c.tree.root.iterdir() if path.is_file()).write_bytes(
            b"PRIVATE changed"
        )
    elif fault == "ledger_locked":
        c.ledger._lock.acquire()
    elif fault == "reader_locked":
        r.lock.acquire()
    elif fault == "expired":
        read = m.plans.clock.read

        def later():
            value = read()
            shift = 1600 * m.plans.clock.NS
            return replace(
                value,
                before_ns=value.before_ns + shift,
                after_ns=value.after_ns + shift,
                boottime_ns=value.boottime_ns + shift,
            )

        monkeypatch.setattr(m.plans.clock, "read", later)
    before = originals(c)
    if fault == "thread":
        failures = []

        def other():
            try:
                r.read()
            except m.UnconfirmedReconciliation:
                failures.append(True)

        thread = Thread(target=other)
        thread.start()
        thread.join(timeout=3)
        assert not thread.is_alive() and failures == [True]
    else:
        denied(r.read)
    assert r.failed and originals(c) == before
    denied(r.read)
    if fault == "ledger_locked":
        c.ledger._lock.release()
    elif fault == "reader_locked":
        r.lock.release()


@pytest.mark.parametrize("fault", ["ledger", "progress", "journal", "deadline", "result", "lock"])
def test_changes_during_collection_cannot_escape_final_recheck(unstarted, monkeypatch, fault):
    c, r = unstarted, reader(unstarted)
    pristine = r.collector.pristine
    original_lock = c.ledger._lock

    def changed():
        value = pristine()
        if fault == "ledger":
            # Actual durable tail without cached state adoption must refuse.
            item = json.loads((c.ledger.directory / "0000.json").read_bytes())
            item["previous"] = c.ledger.state.sha256
            item["event"] = dict(kind="abandoned", boot_id=c.plan.boot, now=time.monotonic())
            path = c.ledger.directory / "0001.json"
            path.write_bytes(b.encode(item))
            path.chmod(0o600)
        elif fault == "progress":
            (c.progress / "unacknowledged").write_bytes(b"PRIVATE")
        elif fault == "journal":
            c.journal.machine.last_at -= 1
        elif fault == "deadline":
            monotonic = time.monotonic
            monkeypatch.setattr(m.time, "monotonic", lambda: monotonic() + 3)
        elif fault == "lock":
            assert original_lock.locked()
            c.ledger._lock = Lock()
        else:
            return replace(value, progress="not pristine")
        return value

    monkeypatch.setattr(r.collector, "pristine", changed)
    denied(r.read)
    assert r.failed and r.collected is None
    assert not c.ledger._lock.locked() and not original_lock.locked() and not r.lock.locked()


def test_keyboard_interrupt_is_preserved_and_borrowed_handles_remain_open(unstarted, monkeypatch):
    c, r = unstarted, reader(unstarted)

    def interrupt():
        raise KeyboardInterrupt()

    monkeypatch.setattr(r.collector, "pristine", interrupt)
    with pytest.raises(KeyboardInterrupt):
        r.read()
    assert r.failed and not c.operator.closed
    assert not c.ledger._lock.locked() and not r.lock.locked()
    for fd in c.operator.handles.values():
        os.fstat(fd)
