"""Real retained pidfds, ledger/journal/checkpoints and partial recording files.

Engine/HA health and namespace routing are explicit fixtures; native start and
lost-start scope inputs are synthetic. No installed recovery or success claim.
"""

import json
import time
from dataclasses import asdict, replace
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_reconcile as exits

m = exits.m
b = m.dispatch.binding
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
def failure(prepared, actors, calibration, plan, journal, family, tree, monkeypatch, request):
    mode = getattr(request, "param", "known")
    ledger_path, progress_path = plan.root / "recording-ledger", plan.root / "recording-progress"
    ledger_path.mkdir(mode=0o700)
    progress_path.mkdir(mode=0o700)
    with exits.captured(
        prepared, actors, calibration, plan, monkeypatch, terminal_replies=30
    ) as case:
        operator = case.holder
        normal = m.plans.base.App(plan.normal.pin, "stopped")
        candidate = m.plans.base.App(
            plan.candidate.pin, "running", operator.pins.generation, True, False
        )
        pristine = p.Files(
            plan.candidate.contract.sha256, "pristine", plan.candidate.contract.baseline_sha256
        )

        def event(kind, **values):
            return journal.append(
                dict(kind=kind, boot_id=plan.boot, now=operator._clock(), **values)
            )

        def observation(files=pristine, *, stopped=False):
            return m.plans.bootstrap.recording.Observation(
                operator._clock(),
                normal,
                m.plans.base.App(plan.candidate.pin, "stopped") if stopped else candidate,
                True,
                True,
                True,
                files,
            )

        event(
            "operator_ready",
            generation=operator.pins.generation,
            intent_sha256=journal.machine.state.launch_intent_sha256,
            ready_evidence_sha256=operator.ready_sha256,
            received_at=operator._clock(),
            observation=asdict(observation()),
        )
        event(
            "authorize_recording",
            generation=operator.pins.generation,
            contract_sha256=plan.candidate.contract.sha256,
            observation=asdict(observation()),
        )
        authorization = journal.entries[-1]["event"]
        ledger = b.Ledger(ledger_path, operator.pins.host, now=time.monotonic())
        began = time.monotonic()
        ledger.start_intent(
            now=began,
            generation=operator.pins.generation,
            authorization_sha256=m.plans.base.checksum(authorization),
            start_by=began + 5,
            finish_by=(
                began + plan.candidate.contract.maximum_recording_seconds - 0.000001
                if mode == "renewed_deadline"
                else began + 30
            ),
        )
        expected = replace(tree.expected, generation=operator.pins.generation)
        # Only the root translation is synthetic. The unchanged baseline,
        # inventory, inode identities, hashes and private-file checks are real.
        original_open = p.evidence.opened_root

        def open_root(path, *, deadline):
            assert path == operator.pins.host.projection.host.baseline.root
            return original_open(tree.root, deadline=deadline)

        monkeypatch.setattr(p.evidence, "opened_root", open_root)
        monkeypatch.setattr(p.monitor, "opened_root", open_root)
        tree.wav.write_bytes(b"PRIVATE incomplete PCM bytes")
        tree.wav.chmod(0o600)
        collector = p.Collector(operator.pins.host.projection.host)
        if mode in ("lost", "missing_scope"):
            if mode == "lost":
                scope = b.preservation.Scope(
                    expected, operator.pins.host.projection.native.contract.sha256, "c" * 64
                )
                ledger.preserve_unconfirmed_start(scope, now=time.monotonic())
            else:
                ledger.abandon(now=time.monotonic())
        else:
            ledger.started(expected, now=time.monotonic(), success_sha256="a" * 64)
            if mode != "no_progress":
                collected = collector.active(expected)
                tip = b.checkpoints.append_progress(
                    progress_path, collector, expected, collected, previous_tip=None
                )
                ledger.progress(progress_path, collector, tip, now=time.monotonic())
            if mode == "completed":
                ledger.completed(
                    p.Acknowledgment(
                        expected.case,
                        expected.generation,
                        plan.candidate.contract.sha256,
                        expected.started_at,
                        "b" * 64,
                        "c" * 64,
                    ),
                    now=time.monotonic(),
                )
            elif mode != "open":
                ledger.abandon(now=time.monotonic())
        case.ready.close()
        exits.finish(family)
        result = operator.poll()
        operator.publish(journal)
        assert not result.init_exited  # The original historical receipt stays unchanged.
        if mode != "init_live":
            prepared.process.stdin.close()
            prepared.process.wait(timeout=3)
            assert prepared.witness.exited()
        if mode != "init_unpublished":
            # For init_live this is deliberately a false policy-only assertion:
            # the Preserved join must independently refuse its live pidfd.
            event("process_exited", generation=operator.pins.generation)
        yield SimpleNamespace(
            operator=operator,
            ledger=ledger,
            journal=journal,
            plan=plan,
            expected=expected,
            tree=tree,
            collector=collector,
            progress=progress_path,
            observation=observation,
            event=event,
            result=result,
            case=case,
        )


def reader(case):
    return m.Preserved(case.operator, case.ledger, case.journal)


def originals(case):
    return {
        path: path.read_bytes()
        for directory in (case.journal.path, case.ledger.directory, case.progress, case.tree.root)
        for path in directory.iterdir()
        if path.is_file()
    }


def denied(call):
    with pytest.raises(m.UnconfirmedReconciliation) as caught:
        call()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


@pytest.mark.parametrize("failure", ["known", "lost", "no_progress"], indirect=True)
def test_original_exit_and_closed_failure_never_promote_completion(failure):
    c = failure
    before, state = originals(c), c.ledger.state
    r = reader(c)
    actual = r.read()
    assert actual.files.stage == "retained" and actual.artifact is None
    assert actual.files.generation == c.expected.generation
    assert r.read() == actual
    assert c.ledger.state == state and state.acknowledgment is None
    assert (state.expected is None) == (state.preservation is not None)
    assert originals(c) == before
    assert c.operator.result is c.result and not c.result.init_exited
    assert c.journal.machine.state.recording_outcome == "unconfirmed"
    r.close()
    denied(r.read)
    assert c.journal.fd >= 0 and not c.operator.closed and not c.operator.failed


@pytest.mark.parametrize(
    "failure",
    ["open", "missing_scope", "completed", "init_live", "init_unpublished", "renewed_deadline"],
    indirect=True,
)
def test_missing_failure_or_independent_exit_evidence_refuses(failure):
    before = originals(failure)
    denied(lambda: reader(failure))
    assert originals(failure) == before and not failure.operator.closed


@pytest.mark.parametrize(
    "fault",
    [
        "ledger_poisoned",
        "ledger_directory",
        "ledger_bytes",
        "authorization",
        "progress_directory",
        "progress_extra",
        "progress_rehashed",
        "original_state",
        "plan",
        "expected",
        "endpoint",
        "journal_extra",
        "journal_cache",
        "operator_closed",
        "receipt",
        "expired",
        "thread",
    ],
)
def test_changed_custody_or_history_cannot_supply_retained_files(failure, monkeypatch, fault):
    c, r = failure, reader(failure)
    if fault == "ledger_poisoned":
        c.ledger._poisoned = True
    elif fault.endswith("directory"):
        path = c.progress if fault.startswith("progress") else c.ledger.directory
        path.rename(path.with_name(path.name + "-retained"))
        path.mkdir(mode=0o700)
    elif fault == "ledger_bytes":
        (c.ledger.directory / "0001.json").write_bytes(b"PRIVATE invalid")
    elif fault == "authorization":
        # Validly rehashed ledger, but not this journal's actual authorization.
        paths = sorted(c.ledger.directory.iterdir())
        previous = None
        for path in paths:
            item = json.loads(path.read_bytes())
            if item["event"]["kind"] == "start_intent":
                item["event"]["authorization_sha256"] = "f" * 64
            if previous is not None:
                item["previous"] = previous
            raw = b.encode(item)
            path.write_bytes(raw)
            previous = m.hashlib.sha256(raw).hexdigest()
        c.ledger.state = b.load(c.ledger.directory, c.ledger.binding)
        denied(lambda: reader(c))  # Not just the first reader's cached-state comparison.
    elif fault == "progress_extra":
        (c.progress / "0001.json").write_bytes(b"PRIVATE unacknowledged tail")
    elif fault == "progress_rehashed":
        path = c.progress / "0000.json"
        item = json.loads(path.read_bytes())
        item["progress"]["progress"]["wav"]["size_before"] = 1
        item["progress"]["progress"]["wav"]["size_after"] = 1
        item["files"]["evidence_sha256"] = b.checksum(item["progress"])
        path.write_bytes(b.encode(item))
    elif fault == "original_state":
        r.original_state = replace(r.original_state, tip=None)
    elif fault == "plan":
        c.operator.plan = m.plans.load_bytes(c.plan.raw, c.plan.sha256)
    elif fault == "expected":
        r.expected = replace(r.expected, started_at="2026-09-23T09:00:00-06:00")
    elif fault == "endpoint":
        r.origins = (*r.origins[:-1], object())
    elif fault == "journal_extra":
        (c.journal.path / "unexpected").write_bytes(b"PRIVATE")
    elif fault == "journal_cache":
        c.journal.machine.state = replace(c.journal.machine.state, recording_outcome="verified")
    elif fault == "operator_closed":
        c.operator.close()
    elif fault == "receipt":
        c.operator.result = replace(c.result, returncode=0)
    elif fault == "expired":
        monkeypatch.setattr(c.operator, "_clock", lambda: c.plan.deadlines.recover_by)
    elif fault == "thread":
        errors = []

        def other():
            try:
                r.read()
            except m.UnconfirmedReconciliation as error:
                errors.append(error)

        thread = Thread(target=other)
        thread.start()
        thread.join(timeout=3)
        assert len(errors) == 1 and not thread.is_alive()
    denied(r.read)
    assert r.failed and c.journal.fd >= 0


@pytest.mark.parametrize("fault", ["shrink", "replace", "old_file", "different_name"])
def test_pinned_progress_and_original_inventory_reject_changed_files(failure, fault):
    c, r = failure, reader(failure)
    if fault == "shrink":
        c.tree.wav.write_bytes(b"less")
    elif fault == "replace":
        c.tree.wav.rename(c.tree.wav.with_name("retained-original.wav"))
        c.tree.wav.write_bytes(b"PRIVATE incomplete PCM bytes")
        c.tree.wav.chmod(0o600)
    elif fault == "old_file":
        (c.tree.root / "old.json").write_bytes(b"PRIVATE changed")
    else:
        (c.tree.root / "different.wav").write_bytes(b"PRIVATE unrelated")
    before = originals(c)
    denied(r.read)
    assert originals(c) == before and not c.operator.closed


def test_growth_from_last_acknowledged_checkpoint_can_be_preserved_once(failure):
    c, r = failure, reader(failure)
    with c.tree.wav.open("ab") as stream:
        stream.write(b"last buffered bytes")
    result = r.read()
    assert result.progress.wav.size_after > r.previous.wav.size_after
    with c.tree.wav.open("ab") as stream:
        stream.write(b"changed after preservation")
    denied(r.read)


def test_policy_pinned_retained_hash_cannot_be_rebased_by_a_new_reader(failure):
    c, r = failure, reader(failure)
    result = r.read()
    action = c.event("observe", observation=asdict(c.observation(result.files, stopped=True)))
    assert action.slug == m.plans.base.NORMAL
    assert c.journal.machine.state.phase == "starting_normal"
    assert c.journal.machine.state.recording_outcome == "unconfirmed"
    assert c.journal.machine.state.preserved_sha256 == result.files.evidence_sha256
    assert r.read() == result  # Legitimate policy advancement preserves original history.
    with c.tree.wav.open("ab") as stream:
        stream.write(b"not a new baseline")
    fresh = reader(c)
    denied(fresh.read)
    assert c.journal.machine.state.recording_outcome == "unconfirmed"


def test_slow_file_read_cannot_renew_the_two_second_join_window(failure, monkeypatch):
    c, r = failure, reader(failure)
    real = r.collector.retained
    monotonic = time.monotonic

    def slow(*args, **kwargs):
        result = real(*args, **kwargs)
        monkeypatch.setattr(m.time, "monotonic", lambda: monotonic() + 3)
        return result

    monkeypatch.setattr(r.collector, "retained", slow)
    denied(r.read)
    assert r.collected is None and c.ledger.state.acknowledgment is None


@pytest.mark.parametrize("failure", ["lost", "no_progress"], indirect=True)
def test_no_pinned_progress_cannot_adopt_an_unacknowledged_tail(failure):
    c = failure
    result = c.collector.active(c.expected)
    b.checkpoints.append_progress(c.progress, c.collector, c.expected, result, previous_tip=None)
    assert c.ledger.state.tip is None
    denied(lambda: reader(c))


def test_first_retained_result_cannot_be_cleared_for_a_new_baseline(failure):
    c, r = failure, reader(failure)
    r.read()
    r.collected = None
    denied(r.read)
    assert c.ledger.state.acknowledgment is None


def test_constructor_joins_progress_and_custody_under_one_deadline(failure, monkeypatch):
    real, monotonic = m.Preserved._progress, time.monotonic

    def slow(reader, end):
        result = real(reader, end)
        monkeypatch.setattr(m.time, "monotonic", lambda: monotonic() + 3)
        return result

    monkeypatch.setattr(m.Preserved, "_progress", slow)
    denied(lambda: reader(failure))
