"""Actual Start/policy/journal; explicitly synthetic completion/exit/file I/O.

The real Finalized wire/files/Operator have separate tests. These ordering tests
do not establish actual native completion, platform facts or recovery authority.
"""

import copy
import os
import time
from dataclasses import asdict, replace
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_retained_history as history

m = history.m
layout, tree, routing, projection, binding, directory, prepared, setup, joined, begun = (
    history.layout,
    history.tree,
    history.routing,
    history.projection,
    history.binding,
    history.directory,
    history.prepared,
    history.setup,
    history.joined,
    history.begun,
)


@pytest.fixture
def completed(begun, monkeypatch):
    s = begun
    operator = object.__new__(m.worker_exit.reconcile.Operator)
    operator.plan, operator.clock, operator.pins = s.plan, s.plan.original_clock, s.run.pins
    operator.owner = s.start.owner
    operator.failed = operator.closed = operator.done = operator.publish_attempted = False
    operator.result_sha256 = None
    s.run.ready.processes = SimpleNamespace(closed=False)
    s.start.relay.phase = "closed"
    # Explicitly synthetic completion layer. Preserve the actual original
    # successful Start and actual host-journal checks underneath this ordering
    # fixture; do not describe it as a completed native recording.
    collected = SimpleNamespace(source="synthetic-finalized-file-fixture")
    trace = []

    def retained():
        assert not s.run.ready.closed and not s.run.client.closed
        return s.run._history_until(min(time.monotonic() + 2, s.plan.lease["stop_by"]))

    monkeypatch.setattr(s.start, "retained_history", retained)
    monkeypatch.setattr(s.start, "read_files", lambda: collected)

    class Finalized:
        def __init__(self, relay, original):
            assert relay is s.start.relay and original is operator
            assert not original.done
            self.relay, self.operator = relay, original
            self.completion = SimpleNamespace(collected=collected)
            self.used = self.failed = self.closed = self.bad_files = False
            self._exit_receipt = None

        def _context(self):
            assert not self.failed and not self.closed
            assert not operator.failed and not operator.closed

        def collect_exit(self):
            self._context()
            assert not self.used
            trace.append("collect")
            self.used, self.relay.phase = True, "exited"
            s.run.client.close()
            result = m.worker_exit.Exited(2, 3, 4, "a" * 64, "b" * 64, "c" * 64, time.monotonic())
            self._exit_receipt = result, m.base.checksum(asdict(result))
            return result

        def read(self):
            self._context()
            assert self.used and s.run.ready.closed and operator.done
            trace.append("files")
            if self.bad_files:
                self.failed = True
                raise ValueError("PRIVATE changed file")
            return collected

        def close(self):
            self.closed = True

    def recheck():
        assert operator.done and not operator.closed and not operator.failed
        trace.append("custody")
        return SimpleNamespace(returncode=0, engine_sha256="c" * 64)

    def publish(journal):
        assert journal is s.journal and operator.done and not operator.publish_attempted
        trace.append("publish")
        operator.publish_attempted = True
        digest = "d" * 64
        now = operator._clock()
        assert (
            journal.append(
                dict(
                    kind="operator_exited",
                    boot_id=s.plan.boot,
                    now=now,
                    generation=operator.pins.generation,
                    intent_sha256=journal.machine.state.launch_intent_sha256,
                    exit_evidence_sha256=digest,
                )
            )
            is None
        )
        operator.result_sha256 = digest
        return digest

    monkeypatch.setattr(m.worker_exit, "Finalized", Finalized)
    monkeypatch.setattr(operator, "recheck", recheck)
    monkeypatch.setattr(operator, "publish", publish)
    reader = m.AuthorizedFinalized(s.start, operator)
    return SimpleNamespace(s=s, operator=operator, reader=reader, trace=trace, collected=collected)


def publish(case):
    exited = case.reader.collect_exit()
    case.s.run.ready.close()
    case.s.run.ready.processes.closed = True
    case.operator.done = True
    digest = case.reader.publish_exit()
    return exited, digest


def denied(reader, method="read"):
    history.begins.denied(getattr(reader, method))
    assert reader.failed


def test_original_authority_joins_delegated_exit_and_files_without_more_writes(completed):
    c = completed
    original = c.s.plan.raw, c.s.start.intent, c.s.start._begin_result
    exited, digest = publish(c)
    originals = {path: path.read_bytes() for path in c.s.journal.path.iterdir()}
    machine = c.s.journal.machine
    assert c.reader.read() is c.collected
    assert c.reader.read() is c.collected
    assert c.trace == ["collect", "custody", "publish", "files", "files"]
    assert c.reader.exited[0] is exited
    assert c.reader.publication[0] == digest
    assert c.s.journal.machine is machine
    assert {path: path.read_bytes() for path in c.s.journal.path.iterdir()} == originals
    assert original == (c.s.plan.raw, c.s.start.intent, c.s.start._begin_result)
    assert c.s.journal.machine.state.recording_outcome == "unconfirmed"
    assert not c.s.prepared.witness.exited()


@pytest.mark.parametrize(
    "fault", ["start", "intent", "authorization", "prefix", "before", "journal", "clock"]
)
def test_changed_original_context_never_collects_an_exit(completed, fault):
    c, r = completed, completed.reader
    if fault == "start":
        c.s.start._begin_result = tuple(list(c.s.start._begin_result))
    elif fault == "intent":
        c.s.start.intent = replace(c.s.start.intent)
    elif fault == "authorization":
        c.s.start.authorization["now"] += 1
    elif fault == "prefix":
        r.history = r.history[:-1]
    elif fault == "before":
        object.__setattr__(r.before, "recording_outcome", "verified")
    elif fault == "journal":
        c.s.journal.path.rename(c.s.journal.path.with_name("retained-journal"))
        c.s.journal.path.mkdir(mode=0o700)
    else:
        c.operator.clock = replace(c.operator.clock)
    denied(r, "collect_exit")
    assert "collect" not in c.trace and c.s.journal.fd >= 0
    assert not c.s.prepared.witness.exited()


@pytest.mark.parametrize("method", ["read", "publish_exit"])
def test_no_post_exit_operation_before_actual_collector(completed, method):
    denied(completed.reader, method)
    assert not completed.trace


@pytest.mark.parametrize("where", ["collect", "publish"])
def test_lost_actual_return_cannot_be_adopted_or_retried(completed, monkeypatch, where):
    c, r = completed, completed.reader
    if where == "collect":
        target, name, operation = r.files, "collect_exit", "collect_exit"
    else:
        r.collect_exit()
        c.s.run.ready.close()
        c.s.run.ready.processes.closed = c.operator.done = True
        target, name, operation = c.operator, "publish", "publish_exit"
    real = getattr(target, name)

    def lost(*args):
        real(*args)
        raise OSError("PRIVATE lost return")

    monkeypatch.setattr(target, name, lost)
    denied(r, operation)
    denied(r, operation)
    assert c.trace.count(where) == 1 and c.s.journal.fd >= 0
    assert not c.operator.closed and not c.s.prepared.witness.exited()
    denied(r)


def test_file_failure_does_not_destroy_independent_exit_or_journal(completed):
    c = completed
    c.reader.files.bad_files = True
    _, digest = publish(c)  # Exit publication does not depend on a good WAV.
    denied(c.reader)
    assert c.s.journal.machine.state.operator_exit_sha256 == digest
    assert c.s.journal.fd >= 0 and not c.operator.closed and not c.operator.failed
    assert not c.s.prepared.witness.exited()


def test_valid_later_policy_history_retains_original_prefix(completed):
    c = completed
    publish(c)
    prefix = c.reader.publication[1]
    # A pure policy fixture receipt, NOT a claim our actual disposable init
    # exited. Actual process observation is a separate RecoverySession gate.
    c.s.journal.append(
        dict(
            kind="process_exited",
            boot_id=c.s.plan.boot,
            now=c.operator._clock(),
            generation=c.s.run.pins.generation,
        )
    )
    assert c.reader.read() is c.collected
    assert tuple(m.base.encode(entry) for entry in c.s.journal.entries[: len(prefix)]) == prefix
    assert not c.s.prepared.witness.exited()


@pytest.mark.parametrize("fault", ["file", "cached_state", "copied_exit", "copied_publication"])
def test_changed_evidence_after_publication_never_returns_files(completed, fault):
    c = completed
    publish(c)
    if fault == "file":
        (c.s.journal.path / "0000.json").write_bytes(b"{}")
    elif fault == "cached_state":
        c.s.journal.machine.state = replace(c.s.journal.machine.state, recording_outcome="verified")
    elif fault == "copied_exit":
        result, digest = c.reader.files._exit_receipt
        c.reader.files._exit_receipt = replace(result), digest
    else:
        c.reader.publication = tuple(list(c.reader.publication))
    denied(c.reader)
    assert "files" not in c.trace and not c.operator.closed


def test_closing_reader_keeps_callers_journal_and_recovery_owner(completed):
    c = completed
    publish(c)
    c.reader.close()
    c.reader.close()
    assert c.reader.files.closed and not c.operator.closed and not c.s.start.closed
    assert c.s.journal.fd >= 0 and not c.s.prepared.witness.exited()
    denied(c.reader)


@pytest.mark.parametrize("fault", ["close", "phase", "publication", "history"])
def test_mid_read_changes_refuse(completed, monkeypatch, fault):
    c = completed
    publish(c)
    real = c.reader.files.read

    def changed():
        result = real()
        if fault == "close":
            c.reader.close()
        elif fault == "phase":
            c.reader.phase = "exited"
        elif fault == "publication":
            c.reader.publication = tuple(list(c.reader.publication))
        else:
            c.s.journal.machine = copy.deepcopy(c.s.journal.machine)
        return result

    monkeypatch.setattr(c.reader.files, "read", changed)
    denied(c.reader)
    assert not c.operator.closed and c.s.journal.fd >= 0


def test_foreign_thread_cannot_collect(completed):
    errors = []

    def read():
        try:
            completed.reader.collect_exit()
        except m.UnconfirmedHostBegin:
            errors.append(True)

    thread = Thread(target=read)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive() and errors == [True]
    assert completed.reader.failed and not completed.trace
    assert completed.s.journal.fd >= 0
    os.fstat(completed.s.prepared.witness.fd)


def test_external_publication_cannot_supply_this_adapters_lost_return(completed):
    c = completed
    c.reader.collect_exit()
    c.s.run.ready.close()
    c.s.run.ready.processes.closed = c.operator.done = True
    c.operator.publish(c.s.journal)
    denied(c.reader, "publish_exit")
    denied(c.reader)
    assert c.trace.count("publish") == 1 and c.s.journal.fd >= 0


def test_nonblocking_nested_read_consumes_outer_read(completed, monkeypatch):
    c = completed
    publish(c)
    real = c.reader.files.read

    def nested():
        result = real()
        denied(c.reader)
        return result

    monkeypatch.setattr(c.reader.files, "read", nested)
    denied(c.reader)
    assert not c.operator.closed and c.s.journal.fd >= 0


@pytest.mark.parametrize("method", ["read", "publish_exit"])
def test_entire_original_read_or_publication_bound_is_final(completed, monkeypatch, method):
    c = completed
    if method == "read":
        publish(c)
        target, name = c.reader.files, "read"
    else:
        c.reader.collect_exit()
        c.s.run.ready.close()
        c.s.run.ready.processes.closed = c.operator.done = True
        target, name = c.operator, "publish"
    real = getattr(target, name)

    def late(*args):
        result = real(*args)
        history.continuity.advance(monkeypatch, 3)
        return result

    monkeypatch.setattr(target, name, late)
    denied(c.reader, method)
    assert c.s.journal.fd >= 0 and not c.operator.closed


def test_policy_review_cannot_be_reopened_as_successful_files(completed):
    c = completed
    publish(c)
    now = c.operator._clock()
    c.s.journal.append(dict(kind="tick", now=now, boot_id="f" * 32))
    assert c.s.journal.machine.state.phase == "review"
    denied(c.reader)
    assert "files" not in c.trace and c.s.journal.fd >= 0
