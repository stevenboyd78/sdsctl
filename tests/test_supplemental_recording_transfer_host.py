"""Actual journal/files with synthetic HA/cache/process receipts; no live handoff."""

import os
import time
from dataclasses import asdict, replace
from threading import Thread

import pytest

from . import test_supplemental_recording_pre_handoff_host as pre

m, b, audio = pre.m, pre.b, pre.audio
layout, tree, routing, projection, binding, directory, prepared, joined, before_handoff = (
    pre.layout,
    pre.tree,
    pre.routing,
    pre.projection,
    pre.binding,
    pre.directory,
    pre.prepared,
    pre.joined,
    pre.before_handoff,
)


@pytest.fixture
def transfer(before_handoff, tmp_path, monkeypatch):
    s = before_handoff
    root = tmp_path / "transfer"
    root.mkdir(mode=0o700)
    (root / "journal").mkdir(mode=0o700)
    monkeypatch.setattr(m.plans.Plan, "root", property(lambda self: root))
    baseline = m.bootstrap.recording.Observation(
        s.plan.deadlines.issued_at,
        b.App(s.plan.normal.pin, "running", s.plan.normal_generation, True, False),
        b.App(s.plan.candidate.pin, "stopped"),
        True,
        True,
        True,
        m.bootstrap.recording.Files(
            s.plan.candidate.contract.sha256, "pristine", s.plan.candidate.contract.baseline_sha256
        ),
    )

    def candidate_files(selected, container):
        assert selected == s.projected.layout
        assert container is None or container["Name"] == "/app_" + b.CANDIDATE
        s.captures.append(b.CANDIDATE)
        return s.static

    monkeypatch.setattr(m.plans.host.candidate_static, "collect", candidate_files)
    with m.bootstrap.Journal(root / "journal") as journal:
        journal.append(s.plan.preparation(baseline, s.projected))

        def append(kind, **fields):
            return journal.append(
                dict(
                    kind=kind,
                    now=m.plans.clock.read().boottime_ns / m.plans.clock.NS,
                    boot_id=s.plan.boot,
                    **fields,
                )
            )

        s.journal, s.append = journal, append
        s.before = m.TransferHost(s.plan, s.projected, journal, s.docker)
        yield s


def observed(s):
    sample = s.before.read()
    return s.append("observe", observation=asdict(sample.observation))


def bind_normal(s):
    s.append(
        "bind_process",
        process=asdict(b.ProcessRecord(b.NORMAL, s.plan.normal_generation, "8" * 64, 1234, 100)),
    )


def starting(s):
    """Synthetic process and CLI receipts; no actual App command."""
    bind_normal(s)
    s.append("request")
    assert observed(s).slug == b.NORMAL
    s.append("bind_execution", container_id="a" * 64, execution_id="1" * 64)
    s.append("execution_completed", execution_id="1" * 64, exit_code=0)
    s.append("process_exited", generation=s.plan.normal_generation)
    s.host.values.pop("app_" + b.NORMAL)
    assert observed(s).slug == b.CANDIDATE
    assert s.journal.machine.state.phase == "starting_candidate"


def candidate(s):
    value = audio.ot.recovery_tests.container("app_" + b.CANDIDATE, 4)
    value.update(audio.container_network(True))
    s.host.values["app_" + b.CANDIDATE] = value
    return audio.h.generation(value, name="app_" + b.CANDIDATE, image=s.plan.candidate.image)


def idle(s):
    starting(s)
    generation = candidate(s)
    s.append("bind_execution", container_id="a" * 64, execution_id="2" * 64)
    s.append("execution_completed", execution_id="2" * 64, exit_code=0)
    s.append(
        "bind_process",
        process=asdict(b.ProcessRecord(b.CANDIDATE, generation, "9" * 64, 5678, 100)),
    )
    assert observed(s) is None and s.journal.machine.state.phase == "candidate_idle"
    return generation


def denied(s):
    pre.bootstrap.launch.denied(s.before.read)
    assert s.before.failed and not s.before.lock.locked()
    pre.bootstrap.launch.denied(s.before.read)


def test_passive_constructor_and_fresh_reads_do_not_advance_original_journal(transfer):
    s = transfer
    assert not s.captures and not s.host.reads and not s.cached_calls
    original = (tuple(s.journal.entries), s.plan.raw, s.plan.deadlines, s.projected.sha256)
    fds = len(os.listdir("/proc/self/fd"))
    first = s.before.read()
    second = s.before.read()
    assert first.observation.normal.healthy and not first.observation.normal.recording
    assert first.observation.candidate.state == "stopped"
    assert first.observation.sampled_at < second.observation.sampled_at
    assert len(s.cached_calls) == 2 and s.idle_reads == 0
    assert len(s.host.reads) == 14 and len(s.captures) == 4
    assert original == (tuple(s.journal.entries), s.plan.raw, s.plan.deadlines, s.projected.sha256)
    assert fds == len(os.listdir("/proc/self/fd"))


def test_one_journal_transitions_to_idle_without_inventing_native_readiness(transfer):
    s = transfer
    deadlines = s.plan.deadlines
    starting(s)
    assert len(s.cached_calls) == 1
    generation = candidate(s)
    sample = s.before.read()
    assert sample.observation.candidate == b.App(s.plan.candidate.pin, "running", generation)
    # Running alone cannot advance the policy without original receipts.
    assert observed(s) is None and s.journal.machine.state.phase == "starting_candidate"
    s.append("bind_execution", container_id="a" * 64, execution_id="2" * 64)
    s.append("execution_completed", execution_id="2" * 64, exit_code=0)
    s.append(
        "bind_process",
        process=asdict(b.ProcessRecord(b.CANDIDATE, generation, "9" * 64, 5678, 100)),
    )
    assert observed(s) is None and s.journal.machine.state.phase == "candidate_idle"
    assert s.before.read().observation.candidate == sample.observation.candidate
    assert len(s.cached_calls) == 1 and s.idle_reads == 0
    assert s.journal.machine.state.launch_intent_sha256 is None
    assert s.journal.machine.state.authorization_generation is None
    assert s.plan.deadlines is deadlines


def test_pre_dispatch_second_read_keeps_identical_preconditions(transfer):
    s, sent = transfer, []
    s.append("request")
    executor = m.bootstrap.Executor(s.journal, s.before.read, lambda *args: sent.append(args))
    # Fresh health does not replace the original process binding.
    result = executor.poll()
    assert result.phase == "requested" and result.outcome == "observed" and not sent
    bind_normal(s)
    result = executor.poll()
    assert result.phase == "stopping_normal" and result.outcome == "dispatch_submitted"
    assert sent == [(("ha", "apps", "stop", b.NORMAL, "--raw-json"), s.plan.case)]
    assert len(s.cached_calls) == 3
    assert executor.poll().outcome == "observed" and len(sent) == 1


@pytest.mark.parametrize("fault", ["file", "permissions", "cached_state", "cached_entry", "extra"])
def test_changed_journal_refuses_before_host_collection(transfer, fault):
    s = transfer
    first = s.journal.path / s.journal.name(0)
    if fault == "file":
        first.write_bytes(first.read_bytes().replace(b'"pristine"', b'"changed!"'))
    elif fault == "permissions":
        first.chmod(0o644)
    elif fault == "cached_state":
        s.journal.machine.state = replace(s.journal.machine.state, phase="requested")
    elif fault == "cached_entry":
        s.journal.entries[0]["event"]["now"] += 1
    else:
        (s.journal.path / "unplanned").write_bytes(b"PRIVATE")
    denied(s)
    assert not s.host.reads and not s.cached_calls


@pytest.mark.parametrize("fault", ["journal", "fd", "plan", "projection", "docker", "owner"])
def test_original_objects_remain_required(transfer, fault):
    s = transfer
    if fault == "journal":
        s.before.journal = object.__new__(m.bootstrap.Journal)
    elif fault == "fd":
        s.before.journal_fd = -1
    elif fault == "plan":
        s.before.plan = m.plans.load_bytes(s.plan.raw, s.plan.sha256)
    elif fault == "projection":
        s.before.projected = replace(s.projected)
    elif fault == "docker":
        s.before.docker = m.plans.ordinary.Docker()
    else:
        s.before.owner = (-1, -1)
    denied(s)
    assert not s.host.reads and not s.cached_calls


@pytest.mark.parametrize("phase", ["prepared", "requested", "stopping_normal"])
def test_existing_candidate_cannot_be_adopted_before_start_intent(transfer, phase):
    s = transfer
    if phase != "prepared":
        s.append("request")
    if phase == "stopping_normal":
        bind_normal(s)
        assert observed(s).slug == b.NORMAL
    candidate(s)
    s.host.reads.clear()
    denied(s)
    assert not s.host.reads


def test_bound_candidate_generation_cannot_change(transfer):
    s = transfer
    idle(s)
    s.host.values["app_" + b.CANDIDATE]["State"]["Pid"] += 1
    denied(s)


@pytest.mark.parametrize("fault", ["journal", "extra_recording", "late", "normal_generation"])
def test_mutation_during_observation_is_never_published(transfer, monkeypatch, fault):
    s = transfer

    def changed():
        if fault == "journal":
            s.append("request")
        elif fault == "extra_recording":
            (s.recording_root / "unexpected.wav").write_bytes(b"PRIVATE")
        elif fault == "late":
            end = time.monotonic() + 3
            monkeypatch.setattr(m.time, "monotonic", lambda: end)
        else:
            s.host.values["app_" + b.NORMAL]["State"]["Pid"] += 1

    s.after_cached = changed
    denied(s)
    assert len(s.cached_calls) == 1


def test_operator_authorization_hands_off_to_a_different_reader(transfer):
    s = transfer
    generation = idle(s)
    sample = s.before.read()
    s.append(
        "authorize_operator",
        generation=generation,
        bootstrap_sha256=s.plan.bootstrap.sha256,
        launch_plan_sha256="d" * 64,
        idle_evidence_sha256="e" * 64,
        observation=asdict(sample.observation),
    )
    s.host.reads.clear()
    denied(s)
    assert not s.host.reads and s.journal.machine.state.phase == "starting_operator"


@pytest.mark.parametrize("error", [OSError("PRIVATE"), KeyboardInterrupt(), SystemExit(71)])
def test_uncertain_transport_does_not_retry_or_swallow_interrupts(transfer, error, capsys):
    s = transfer

    def fail(_):
        raise error

    s.host.hook = fail
    if isinstance(error, Exception):
        denied(s)
    else:
        with pytest.raises(type(error)):
            s.before.read()
    assert s.before.failed and not s.before.lock.locked()
    assert capsys.readouterr() == ("", "") and not s.prepared.witness.exited()


def test_foreign_thread_permanently_refuses_without_host_request(transfer):
    s, errors = transfer, []

    def read():
        try:
            s.before.read()
        except m.UnconfirmedHostLaunch as error:
            errors.append(str(error))

    worker = Thread(target=read)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive() and errors == [m.MESSAGE]
    assert not s.host.reads
    denied(s)


@pytest.mark.parametrize("healthy,recording", [(None, None), (False, False), (True, True)])
def test_normal_native_uncertainty_is_not_replaced_with_healthy_idle(transfer, healthy, recording):
    s = transfer
    s.host.native_state = healthy, recording
    sample = s.before.read()
    assert (sample.observation.normal.healthy, sample.observation.normal.recording) == (
        healthy,
        recording,
    )
    assert s.journal.machine.state.phase == "prepared"


@pytest.mark.parametrize("fault", ["normal_files", "options", "jobs", "candidate_files"])
def test_changed_observed_host_facts_are_forwarded_to_policy(transfer, fault):
    s = transfer
    if fault == "normal_files":
        s.host.files[b.NORMAL] = replace(s.host.files[b.NORMAL], recordings="e" * 64)
    elif fault == "options":
        s.host.private_options["normal"] = "PRIVATE changed"
    elif fault == "jobs":
        s.host.jobs = False
    else:
        s.static = replace(s.static, package="f" * 64)
    sample = s.before.read()
    obs = sample.observation
    if fault in ("normal_files", "options"):
        assert obs.normal.pin != s.plan.normal.pin and not s.cached_calls
    elif fault == "jobs":
        assert obs.jobs_idle is False
    else:
        assert obs.candidate.pin != s.plan.candidate.pin
    assert s.journal.machine.state.phase == "prepared"


def test_failed_cache_query_cannot_be_repeated(transfer, monkeypatch):
    s, calls = transfer, []

    def fail(*_):
        calls.append(True)
        raise OSError("PRIVATE")

    monkeypatch.setattr(m.normal_read.cached, "_read_probe", fail)
    denied(s)
    assert calls == [True] and s.before.normal_reader.failed


def test_reentrant_read_permanently_refuses(transfer):
    s = transfer
    s.after_cached = lambda: pre.bootstrap.launch.denied(s.before.read)
    denied(s)
    assert len(s.cached_calls) == 1


def test_original_ready_clock_is_not_extended(transfer, monkeypatch):
    s = transfer
    original = m.plans.clock.read
    deadline = s.plan.deadlines

    def expired():
        value = original()
        shift = 121 * m.plans.clock.NS
        return replace(
            value,
            before_ns=value.before_ns + shift,
            after_ns=value.after_ns + shift,
            boottime_ns=value.boottime_ns + shift,
        )

    monkeypatch.setattr(m.plans.clock, "read", expired)
    denied(s)
    assert not s.host.reads and s.plan.deadlines is deadline


def test_successful_read_never_hides_later_pristine_file_mutation(transfer):
    s = transfer
    s.before.read()
    name = s.projected.host.baseline.files[0][0]
    (s.recording_root / name).write_bytes(b"PRIVATE changed")
    denied(s)


@pytest.mark.parametrize("fault", [None, "lost_stop_reply", "missing_normal_exit"])
def test_original_recovery_session_joins_transfer_and_exact_dispatch(transfer, monkeypatch, fault):
    """Actual journal/observer/dispatcher; explicit fake Engine and pidfd receipts."""
    s = transfer
    previous = audio.ot.recovery_tests
    engine_host = previous.Host()
    engine_host.containers = s.host.values
    engine_host.omit_exit = fault == "missing_normal_exit"
    if fault == "lost_stop_reply":
        engine_host.start_error = OSError("PRIVATE lost stop response")
    original_start = engine_host.start_execution

    def start_execution(eid):
        original_start(eid)
        if engine_host.sent[-1] == ("start", b.CANDIDATE):
            candidate(s)

    monkeypatch.setattr(previous.r, "read_identity", engine_host.identity)
    monkeypatch.setattr(previous.r, "ProcessWitness", engine_host.witness)
    monkeypatch.setattr(s.docker, "create_execution", engine_host.create_execution)
    monkeypatch.setattr(s.docker, "start_execution", start_execution)
    monkeypatch.setattr(s.docker, "inspect_execution", engine_host.inspect_execution)

    def clock():
        window = m.plans.clock.read()
        s.plan.check_clock(window)
        return window.boot, window.boottime_ns / m.plans.clock.NS

    processes = previous.r.TrackedProcesses(
        s.journal,
        s.docker,
        images={b.NORMAL: s.plan.normal.image, b.CANDIDATE: s.plan.candidate.image},
        read_clock=clock,
    )
    dispatch = previous.h.TrackedDispatch(
        s.journal,
        s.docker,
        cli_image=s.plan.cli_image,
        cli_generation=s.plan.cli_generation,
        now=lambda: clock()[1],
    )
    session = m.bootstrap.RecoverySession(s.journal, processes, dispatch, s.before.read)
    original_deadlines = s.plan.deadlines
    try:
        s.append("request")
        first = session.poll()
        assert first.phase == "stopping_normal"
        assert first.outcome == (
            "dispatch_unconfirmed" if fault == "lost_stop_reply" else "dispatch_submitted"
        )
        assert engine_host.sent == [("stop", b.NORMAL)]
        engine_host.start_error = None
        if fault == "missing_normal_exit":
            for _ in range(2):
                assert session.poll().phase == "stopping_normal"
            assert engine_host.sent == [("stop", b.NORMAL)]
            assert not s.journal.machine.state.exited_processes
        else:
            assert session.poll().outcome == "dispatch_submitted"
            assert session.poll().phase == "candidate_idle"
            assert session.poll().phase == "candidate_idle"
            assert engine_host.sent == [("stop", b.NORMAL), ("start", b.CANDIDATE)]
            assert s.journal.machine.state.exited_processes == (s.plan.normal_generation,)
            assert len(s.journal.machine.state.completed_executions) == 2
            assert len(s.journal.machine.state.processes) == 2
            assert s.journal.machine.state.launch_intent_sha256 is None
            assert s.journal.machine.state.authorization_generation is None
        assert s.plan.deadlines is original_deadlines
        assert session.journal is s.journal and session.processes is processes
        assert session.dispatch is dispatch
    finally:
        session.close()
    assert all(handle.closed for handle in engine_host.handles)
