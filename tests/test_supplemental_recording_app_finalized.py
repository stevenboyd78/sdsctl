"""Real App launch/begin/journal/ledger; explicit synthetic completion/exit I/O.

This tests the closed App join, not a recorded WAV or installed recovery. Actual
private native returns, pidfd duplication/exit and immutable files retain their
separate lower-level tests. No live endpoint or scanner is contacted here.
"""

import copy
import importlib.util
import os
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_observation as observations

candidate, app, native, launch_case, execution, joined, observing = (
    observations.candidate,
    observations.app,
    observations.native,
    observations.launch_case,
    observations.execution,
    observations.joined,
    observations.observing,
)
layout, image_umask, supervised, image, configured = (
    observations.layout,
    observations.image_umask,
    observations.supervised,
    observations.image,
    observations.configured,
)
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)
NAME = "supplemental_recording_app_finalized"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(observations.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
begin, launch, denied = m.begin, m.launch, observations.denied


@pytest.fixture
def completed(observing, monkeypatch):
    s = observing
    s.mark_closed()
    operator = object.__new__(m.worker_exit.reconcile.Operator)
    operator.plan, operator.clock, operator.pins = s.plan, s.plan.original_clock, s.run.pins
    operator.owner = s.start.owner
    operator.failed = operator.closed = operator.done = operator.publish_attempted = False
    operator.result_sha256 = None
    assert not s.run.ready.processes.closed  # Retain the originally pinned fixture owner.
    collected, trace = s.files, []

    class Finalized:
        """Synthetic completion/exit layer, NOT a receipt emitted by a worker."""

        def __init__(self, relay, original):
            assert relay is s.relay and original is operator and not original.done
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
        assert (
            journal.append(
                dict(
                    kind="operator_exited",
                    boot_id=s.plan.boot,
                    now=operator._clock(),
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
    reader = m.AppAuthorizedFinalized(s.start, operator)
    try:
        yield SimpleNamespace(
            s=s, operator=operator, reader=reader, trace=trace, collected=collected
        )
    finally:
        reader.close()


def publish(case):
    exited = case.reader.collect_exit()
    case.s.run.ready.close()
    case.s.run.ready.processes.closed = True
    case.operator.done = True
    digest = case.reader.publish_exit()
    return exited, digest


def test_app_authority_keeps_actual_journal_and_ledger_across_separate_exit(completed):
    c = completed
    original = c.s.plan.raw, c.s.start.intent, c.s.start._begin_result
    exited, digest = publish(c)
    entries = tuple(c.s.journal.entries)
    assert c.reader.read() is c.collected
    assert c.reader.read() is c.collected
    assert c.trace == ["collect", "custody", "publish", "files", "files"]
    assert c.reader.exited[0] is exited and c.reader.publication[0] == digest
    assert tuple(c.s.journal.entries) == entries
    assert original == (c.s.plan.raw, c.s.start.intent, c.s.start._begin_result)
    assert c.s.journal.machine.state.recording_outcome == "unconfirmed"
    assert not c.s.witness.exited()  # Completion never implies init exit.


@pytest.mark.parametrize("method", ["read", "publish_exit"])
def test_no_post_exit_operation_before_actual_collector(completed, method):
    c = completed
    denied(getattr(c.reader, method))
    assert c.reader.failed and not c.trace
    assert not c.operator.failed and not c.operator.closed and c.s.journal.fd >= 0


@pytest.mark.parametrize(
    "fault",
    [
        "start",
        "intent",
        "authorization",
        "prefix",
        "before",
        "journal",
        "clock",
        "operator",
        "execution_owner",
        "ready_owner",
        "begin_owner",
        "prelaunch",
        "owned_ready",
    ],
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
    elif fault == "clock":
        c.operator.clock = replace(c.operator.clock)
    elif fault == "operator":
        r.operator = copy.copy(c.operator)
    elif fault == "execution_owner":
        c.s.run.prelaunch.candidate.native_execution_owner = object()
    elif fault == "ready_owner":
        c.s.run.prelaunch.candidate.native_ready_owner = object()
    elif fault == "begin_owner":
        c.s.run.begin_owner = object()
    elif fault == "prelaunch":
        c.s.run.prelaunch = copy.copy(c.s.run.prelaunch)
    else:
        c.s.run._owned_ready = object()
    denied(r.collect_exit)
    assert r.failed and "collect" not in c.trace
    assert not c.operator.failed and not c.operator.closed and c.s.journal.fd >= 0
    os.fstat(c.s.witness.fd)
    if fault == "owned_ready":
        c.s.run._owned_ready = c.s.run.ready  # Only restore fixture cleanup binding.


@pytest.mark.parametrize("operation", ["collect_exit", "publish_exit"])
def test_lost_return_is_not_adopted_or_retried(completed, monkeypatch, operation):
    c, r = completed, completed.reader
    if operation == "collect_exit":
        target, name, label = r.files, "collect_exit", "collect"
    else:
        r.collect_exit()
        c.s.run.ready.close()
        c.s.run.ready.processes.closed = c.operator.done = True
        target, name, label = c.operator, "publish", "publish"
    real = getattr(target, name)

    def lost(*args):
        real(*args)
        raise OSError("PRIVATE lost return")

    monkeypatch.setattr(target, name, lost)
    denied(getattr(r, operation))
    denied(getattr(r, operation))
    assert r.failed and c.trace.count(label) == 1
    assert not c.operator.closed and c.s.journal.fd >= 0
    denied(r.read)


def test_bad_finalized_file_does_not_destroy_independent_exit_evidence(completed):
    c = completed
    c.reader.files.bad_files = True
    _, digest = publish(c)
    denied(c.reader.read)
    assert c.s.journal.machine.state.operator_exit_sha256 == digest
    assert not c.operator.closed and not c.operator.failed
    assert c.s.journal.fd >= 0 and not c.s.witness.exited()


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
    denied(c.reader.read)
    assert c.reader.failed and "files" not in c.trace and not c.operator.closed


@pytest.mark.parametrize("fault", ["close", "phase", "publication", "history"])
def test_mid_read_changes_cannot_publish_an_app_file_sample(completed, monkeypatch, fault):
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
    denied(c.reader.read)
    assert c.reader.failed and not c.operator.closed and c.s.journal.fd >= 0


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
        observations.a.continuity_tests.advance(monkeypatch, 3)
        return result

    monkeypatch.setattr(target, name, late)
    denied(getattr(c.reader, method))
    assert c.reader.failed and c.s.journal.fd >= 0 and not c.operator.closed


def test_external_publication_cannot_replace_this_readers_return(completed):
    c = completed
    c.reader.collect_exit()
    c.s.run.ready.close()
    c.s.run.ready.processes.closed = c.operator.done = True
    c.operator.publish(c.s.journal)
    denied(c.reader.publish_exit)
    denied(c.reader.read)
    assert c.trace.count("publish") == 1 and c.s.journal.fd >= 0


def test_nested_read_fails_without_disabling_independent_exit_owner(completed, monkeypatch):
    c = completed
    publish(c)
    real = c.reader.files.read

    def nested():
        result = real()
        denied(c.reader.read)
        return result

    monkeypatch.setattr(c.reader.files, "read", nested)
    denied(c.reader.read)
    assert c.reader.failed and not c.operator.closed and c.s.journal.fd >= 0


def test_policy_review_cannot_be_reopened_as_successful_files(completed):
    c = completed
    publish(c)
    c.s.journal.append(dict(kind="tick", now=c.operator._clock(), boot_id="f" * 32))
    assert c.s.journal.machine.state.phase == "review"
    denied(c.reader.read)
    assert c.reader.failed and "files" not in c.trace and c.s.journal.fd >= 0


def test_read_after_expired_ready_never_refreshes_live_qualification(completed, monkeypatch):
    c = completed
    publish(c)

    def forbidden(*args, **kwargs):
        pytest.fail("Finalized file evidence must not renew live Ready/input qualification")

    monkeypatch.setattr(c.s.run, "_app_context", forbidden)
    monkeypatch.setattr(type(c.s.run.qualify), "__call__", forbidden)
    monkeypatch.setattr(c.s.run.qualify, "_binding", forbidden)
    monkeypatch.setattr(launch.received.Ready, "check_before_begin", forbidden)
    with monkeypatch.context() as patch:
        observations.a.continuity_tests.advance(patch, 125)
        assert time.monotonic() > c.s.run.ready.ready_by
        assert c.reader.read() is c.collected


def test_foreign_thread_cannot_consume_exit_or_close_independent_custody(completed):
    c, errors = completed, []

    def attempt():
        try:
            c.reader.collect_exit()
        except Exception as error:
            errors.append(error)

    thread = Thread(target=attempt)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive() and len(errors) == 1
    assert str(errors[0]) == begin.MESSAGE and c.reader.failed and not c.trace
    assert not c.operator.closed and c.s.journal.fd >= 0


def test_old_gates_and_unreviewed_subclasses_refuse_without_poisoning_owner(completed):
    c = completed

    class Other(m.AppAuthorizedFinalized):
        pass

    denied(lambda: Other(c.s.start, c.operator))
    denied(lambda: begin.AuthorizedFinalized(c.s.start, c.operator))
    denied(lambda: begin.FinalizedHost(c.reader))
    with pytest.raises(begin.UnconfirmedHostBegin, match="Recording host begin is unconfirmed"):
        begin.recover_finalized(c.reader, object(), lambda _: None)
    assert not c.reader.failed and not c.s.start.failed and not c.operator.closed
    publish(c)
    assert c.reader.read() is c.collected


@pytest.fixture
def host(completed, monkeypatch):
    c = completed
    publish(c)
    c.host = m.AppFinalizedHost(c.reader)
    c.identities, c.metadata = [], []

    def container(name):
        c.identities.append(name)
        return dict(Id=c.s.run.pins.init.container_id)

    def observer():
        def read():
            c.metadata.append("read")
            boot, began = c.host._clock()
            return begin._StaticHostSnapshot(
                boot,
                began,
                c.host._clock()[1],
                begin.base.App(c.s.plan.normal.pin, "stopped"),
                begin.base.App(c.s.plan.candidate.pin, "running", c.s.run.pins.generation),
                True,
                True,
            )

        return SimpleNamespace(read=read)

    monkeypatch.setattr(c.host.docker, "container", container)
    monkeypatch.setattr(c.host, "_observer", observer)
    try:
        yield c
    finally:
        c.host.close()


def test_full_host_bracket_does_not_infer_native_health_from_completed_files(host):
    c = host
    original = tuple(c.s.journal.entries), c.s.plan.raw
    for _ in range(2):
        sample = c.host.read()
        assert sample.observation.files is c.collected.files
        assert sample.observation.files.stage == "finalized"
        assert sample.observation.candidate.healthy is None
        assert sample.observation.candidate.recording is None
        assert sample.observation.sampled_at <= sample.now
    assert c.identities == ["app_" + begin.base.CANDIDATE] * 4
    assert c.metadata == ["read", "read"]
    assert original == (tuple(c.s.journal.entries), c.s.plan.raw)


@pytest.mark.parametrize("fault", ["generation", "health", "recording", "late", "boot"])
def test_bad_metadata_does_not_return_app_sample(host, monkeypatch, fault):
    c, original = host, host.host._observer

    def observer():
        def read():
            value = original().read()
            if fault == "late":
                observations.a.continuity_tests.advance(monkeypatch, 3)
                return value
            if fault == "boot":
                return replace(value, boot="a" * 32)
            key = "healthy" if fault == "health" else fault
            return replace(
                value,
                candidate=replace(
                    value.candidate,
                    **{
                        key: "a" * 64 if key == "generation" else False,
                    },
                ),
            )

        return SimpleNamespace(read=read)

    monkeypatch.setattr(c.host, "_observer", observer)
    denied(c.host.read)
    assert c.host.failed and not c.operator.closed and c.s.journal.fd >= 0


@pytest.mark.parametrize("where", ["before", "after"])
def test_same_name_candidate_replacement_is_never_accepted(host, monkeypatch, where):
    c, calls = host, []
    replacement = "b" * 64
    assert replacement != c.s.run.pins.init.container_id

    def changed(name):
        calls.append(name)
        return dict(
            Id=replacement
            if where == "before" or len(calls) == 2
            else c.s.run.pins.init.container_id
        )

    monkeypatch.setattr(c.host.docker, "container", changed)
    denied(c.host.read)
    assert c.host.failed and not c.operator.closed
    assert len(calls) == (1 if where == "before" else 2)


def test_host_requires_publication_and_close_does_not_close_borrowed_owners(completed):
    c = completed
    denied(lambda: m.AppFinalizedHost(c.reader))
    assert not c.reader.failed
    publish(c)
    host = m.AppFinalizedHost(c.reader)
    host.close()
    host.close()
    denied(host.read)
    assert c.reader.read() is c.collected
    c.reader.close()
    c.reader.close()
    assert not c.operator.closed and not c.s.start.closed and c.s.journal.fd >= 0
