"""Durable process receipts and four-command recovery using local fake host I/O."""

import importlib.util
import json
import sys
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path

import pytest

for name in (
    "supplemental_handoff_policy",
    "supplemental_handoff_executor",
    "supplemental_handoff_host",
    "supplemental_handoff_process",
    "supplemental_handoff_recovery",
):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parents[1] / "scripts" / (name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
p = sys.modules["supplemental_handoff_policy"]
e = sys.modules["supplemental_handoff_executor"]
h = sys.modules["supplemental_handoff_host"]
r = sys.modules["supplemental_handoff_recovery"]
CASE, BOOT = "a" * 12 + "4" + "a" * 3 + "8" + "a" * 15, "b" * 32
IMAGE = "sha256:" + "c" * 64


def container(name, number):
    return {
        "Id": f"{number:064x}",
        "Name": "/" + name,
        "Image": IMAGE,
        "State": {
            "Status": "running",
            "Running": True,
            "Paused": False,
            "Restarting": False,
            "Dead": False,
            "OOMKilled": False,
            "Pid": number + 100,
            "Error": "",
            "StartedAt": "2026-09-22T00:00:00Z",
        },
    }


class Host:
    def __init__(self):
        self.now, self.boot = 10.0, BOOT
        self.containers = {
            h.CLI: container(h.CLI, 1),
            "app_" + p.NORMAL: container("app_" + p.NORMAL, 2),
        }
        self.execs, self.created, self.sent, self.dead, self.handles = {}, [], [], set(), []
        self.read_fault = self.start_error = None
        self.omit_exit = False

    def clock(self):
        return self.boot, self.now

    def container(self, name):
        return deepcopy(self.containers[name])

    def create_execution(self, cid, command):
        eid = f"{1000 + len(self.created):064x}"
        self.created.append((cid, command))
        self.execs[eid] = {
            "ID": eid,
            "ContainerID": cid,
            "Running": False,
            "ExitCode": None,
            "Pid": 0,
            "OpenStdin": False,
            "ProcessConfig": {
                "entrypoint": command[0],
                "arguments": list(command[1:]),
                "privileged": False,
                "tty": False,
                "user": "0",
            },
        }
        return eid

    def inspect_execution(self, eid):
        return deepcopy(self.execs[eid])

    def start_execution(self, eid):
        command = self.execs[eid]["ProcessConfig"]["arguments"]
        _, op, slug, _ = command
        self.sent.append((op, slug))
        name = "app_" + slug
        if op == "stop":
            old = self.containers.pop(name)
            if not self.omit_exit:
                self.dead.add(old["Id"])
        else:
            self.containers[name] = container(name, 3 if slug == p.CANDIDATE else 4)
        self.execs[eid]["ExitCode"] = 0
        if self.start_error:
            raise self.start_error

    def identity(self, pid, cid):
        assert any(v["Id"] == cid and v["State"]["Pid"] == pid for v in self.containers.values())
        return r.ProcessIdentity(pid, 500 + pid, cid)

    def witness(self, identity):
        host = self

        class Witness:
            def __init__(self):
                self.identity, self.closed = identity, False

            def exited(self):
                assert not self.closed
                return self.identity.container_id in host.dead

            def close(self):
                self.closed = True

        value = Witness()
        self.handles.append(value)
        return value

    def read(self):
        def app(slug, pin):
            name = "app_" + slug
            if name not in self.containers:
                # The full observer has separately checked Supervisor/absence.
                # RecoverySession must still withhold this until pidfd proof.
                return p.App(pin, "stopped")
            gen = h.generation(self.containers[name], name=name, image=IMAGE)
            return p.App(pin, "running", gen, True, False)

        value = e.Sample(
            self.boot,
            self.now,
            p.Observation(
                self.now, app(p.NORMAL, "d" * 64), app(p.CANDIDATE, "e" * 64), True, True, True
            ),
        )
        return self.read_fault(value) if self.read_fault else value


@pytest.fixture
def setup(tmp_path, monkeypatch):
    host = Host()
    monkeypatch.setattr(r, "read_identity", host.identity)
    monkeypatch.setattr(r, "ProcessWitness", host.witness)
    path = tmp_path / "case"
    path.mkdir(mode=0o700)
    with p.Journal(path) as journal:
        journal.append(
            {
                "kind": "prepare",
                "case_id": CASE,
                "boot_id": BOOT,
                "now": host.now,
                "observation": asdict(host.read().observation),
            }
        )
        yield host, journal


def session(host, journal):
    processes = r.TrackedProcesses(
        journal, host, images={p.NORMAL: IMAGE, p.CANDIDATE: IMAGE}, read_clock=host.clock
    )
    dispatch = h.TrackedDispatch(
        journal,
        host,
        cli_image=IMAGE,
        cli_generation=h.generation(host.container(h.CLI), name=h.CLI, image=IMAGE),
        now=lambda: host.now,
    )
    return r.RecoverySession(journal, processes, dispatch, host.read)


def request(host, journal):
    host.now += 1
    journal.append({"kind": "request", "boot_id": host.boot, "now": host.now})


def poll(host, run):
    host.now += 1
    return run.poll()


def test_complete_journal_bridge_tracks_exits_before_each_next_owner(setup):
    host, journal = setup
    run = session(host, journal)
    request(host, journal)
    assert poll(host, run).outcome == "dispatch_submitted"  # stop normal
    assert len(journal.machine.state.processes) == 1
    assert not journal.machine.state.exited_processes
    assert poll(host, run).outcome == "dispatch_submitted"  # start candidate
    assert len(journal.machine.state.exited_processes) == 1
    assert poll(host, run).phase == "candidate_running"
    host.now += 1
    journal.append({"kind": "finish", "boot_id": BOOT, "now": host.now})
    assert poll(host, run).outcome == "dispatch_submitted"  # stop candidate
    assert poll(host, run).outcome == "dispatch_submitted"  # start normal
    assert len(journal.machine.state.exited_processes) == 2
    assert poll(host, run).phase == "complete"
    assert host.sent == [
        ("stop", p.NORMAL),
        ("start", p.CANDIDATE),
        ("stop", p.CANDIDATE),
        ("start", p.NORMAL),
    ]
    assert len(journal.entries) <= p.MAX_EVENTS
    assert len(journal.machine.state.completed_executions) == 4
    state, count, path = journal.machine.state, len(journal.entries), journal.path
    run.close()
    assert all(handle.closed for handle in host.handles)
    journal.close()
    with p.Journal(path) as recovered:
        assert recovered.machine.state == state and len(recovered.entries) == count
        # Replaying a finished case cannot submit another start or stop.
        assert session(host, recovered).poll().outcome == "terminal"
    assert len(host.sent) == 4


def test_container_removal_without_process_exit_does_not_start_other_owner(setup):
    host, journal = setup
    run = session(host, journal)
    host.omit_exit = True
    request(host, journal)
    assert poll(host, run).outcome == "dispatch_submitted"
    assert poll(host, run).phase == "stopping_normal"
    assert host.sent == [("stop", p.NORMAL)]
    host.now += 121
    assert poll(host, run).phase == "review"
    assert not journal.machine.state.exited_processes
    run.close()


@pytest.mark.parametrize("record_exit", [True, False])
def test_restart_after_removal_only_uses_already_durable_exit(setup, record_exit):
    host, journal = setup
    run = session(host, journal)
    request(host, journal)
    poll(host, run)
    if record_exit:
        host.now += 1
        run.processes.reconcile()
    run.close()
    path = journal.path
    journal.close()
    with p.Journal(path) as recovered:
        new = session(host, recovered)
        result = poll(host, new)
        assert result.phase == ("starting_candidate" if record_exit else "stopping_normal")
        assert host.sent == (
            [("stop", p.NORMAL), ("start", p.CANDIDATE)] if record_exit else [("stop", p.NORMAL)]
        )
        new.close()


def test_restart_rebinds_exact_still_live_process_without_new_receipt(setup):
    host, journal = setup
    run = session(host, journal)
    assert poll(host, run).phase == "prepared"
    count = len(journal.entries)
    run.close()
    path = journal.path
    journal.close()
    with p.Journal(path) as recovered:
        new = session(host, recovered)
        assert poll(host, new).phase == "prepared"
        assert len(recovered.entries) == count and not recovered.machine.state.exited_processes
        assert not host.sent
        new.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("pid", True),
        ("pid", 0),
        ("pid", 2**31),
        ("start_ticks", False),
        ("start_ticks", 0),
        ("start_ticks", 2**64),
        ("generation", "wrong"),
        ("container_id", "wrong"),
        ("slug", "core"),
    ],
)
def test_record_validation_is_strict(setup, field, value):
    host, journal = setup
    record = dict(
        slug=p.NORMAL,
        generation=journal.machine.baseline.normal.generation,
        container_id="2" * 64,
        pid=123,
        start_ticks=456,
    )
    record[field] = value
    with pytest.raises(p.UnsafeHandoff):
        journal.append(
            {"kind": "bind_process", "boot_id": BOOT, "now": host.now, "process": record}
        )
    assert len(journal.entries) == 1


@pytest.mark.parametrize(
    "fault", ["duplicate", "wrong_generation", "candidate_early", "extra", "missing"]
)
def test_binding_cannot_adopt_other_or_duplicate_process(setup, fault):
    host, journal = setup
    run = session(host, journal)
    poll(host, run)
    value = asdict(journal.machine.state.processes[0])
    if fault == "wrong_generation":
        value["generation"] = "f" * 64
    elif fault == "candidate_early":
        value.update(slug=p.CANDIDATE, generation="f" * 64, container_id="f" * 64)
    elif fault == "extra":
        value["exit_confirmed"] = True
    elif fault == "missing":
        value.pop("pid")
    with pytest.raises(p.UnsafeHandoff):
        journal.append({"kind": "bind_process", "boot_id": BOOT, "now": host.now, "process": value})
    run.close()


def test_exit_receipt_requires_known_binding_and_is_once_only(setup):
    host, journal = setup
    with pytest.raises(p.UnsafeHandoff):
        journal.append(
            {"kind": "process_exited", "boot_id": BOOT, "now": host.now, "generation": "f" * 64}
        )
    run = session(host, journal)
    request(host, journal)
    poll(host, run)
    host.now += 1
    run.processes.reconcile()
    event = journal.entries[-1]["event"]
    with pytest.raises(p.UnsafeHandoff):
        journal.append(event)
    run.close()


def test_journal_exit_write_failure_never_advances_to_next_owner(setup, monkeypatch):
    host, journal = setup
    run = session(host, journal)
    request(host, journal)
    poll(host, run)
    original = journal.append

    def failed(event):
        if event["kind"] == "process_exited":
            raise OSError("private disk failure")
        return original(event)

    monkeypatch.setattr(journal, "append", failed)
    assert poll(host, run).phase == "stopping_normal"
    assert host.sent == [("stop", p.NORMAL)]
    assert not journal.machine.state.exited_processes
    run.close()


def test_lost_stop_reply_is_reconciled_without_reissuing(setup):
    host, journal = setup
    run = session(host, journal)
    request(host, journal)
    host.start_error = TimeoutError("private lost reply")
    assert poll(host, run).outcome == "dispatch_unconfirmed"
    host.start_error = None
    assert poll(host, run).outcome == "dispatch_submitted"
    assert host.sent == [("stop", p.NORMAL), ("start", p.CANDIDATE)]
    run.close()


def test_process_receipt_does_not_override_busy_jobs_or_changed_pin(setup):
    host, journal = setup
    run = session(host, journal)
    request(host, journal)
    poll(host, run)
    host.read_fault = lambda s: replace(s, observation=replace(s.observation, jobs_idle=False))
    assert poll(host, run).phase == "stopping_normal"
    assert len(journal.machine.state.exited_processes) == 1
    assert len(host.sent) == 1
    host.read_fault = lambda s: replace(
        s, observation=replace(s.observation, normal=p.App("f" * 64, "stopped"))
    )
    assert poll(host, run).phase == "review"
    assert len(host.sent) == 1
    run.close()


def test_binding_is_saved_before_stop_submission(setup):
    host, journal = setup
    run = session(host, journal)
    request(host, journal)
    original = host.start_execution

    def checked(eid):
        assert journal.machine.state.processes
        entries = [json.loads(path.read_bytes())["event"] for path in journal.path.glob("*.json")]
        assert any(event["kind"] == "bind_process" for event in entries)
        original(eid)

    host.start_execution = checked
    assert poll(host, run).outcome == "dispatch_submitted"
    run.close()


def test_exit_racing_durable_bind_keeps_witness_for_reconciliation(setup, monkeypatch):
    host, journal = setup
    run = session(host, journal)
    original = journal.append

    def exiting(event):
        result = original(event)
        if event["kind"] == "bind_process":
            host.dead.add(event["process"]["container_id"])
            host.containers.pop("app_" + p.NORMAL)
        return result

    monkeypatch.setattr(journal, "append", exiting)
    assert poll(host, run).phase == "prepared"
    assert len(journal.machine.state.processes) == 1
    assert p.NORMAL in run.processes.witnesses
    host.now += 1
    run.processes.reconcile()
    assert run.processes.exit_confirmed(p.NORMAL)
    assert not host.sent
    run.close()


def test_bounded_loop_completes_and_closes_handles(setup):
    host, journal = setup
    run = session(host, journal)
    request(host, journal)
    waits = []

    def wait(seconds):
        assert seconds == 0.25
        waits.append(seconds)
        host.now += 1
        if journal.machine.state.phase == "candidate_running":
            journal.append({"kind": "finish", "boot_id": BOOT, "now": host.now})

    assert run.run(wait).phase == "complete"
    assert len(waits) == 5
    assert run.processes.closed and all(handle.closed for handle in host.handles)
    assert len(host.sent) == 4 and len(journal.entries) <= p.MAX_EVENTS


def test_unavailable_remote_observer_still_reaches_durable_deadline(setup):
    host, journal = setup
    run = session(host, journal)

    def unavailable():
        raise TimeoutError("private host response")

    run.read = unavailable
    request(host, journal)
    results = []

    def wait(seconds):
        results.append(seconds)
        host.now += 100

    result = run.run(wait)
    assert result.phase == "review"
    assert journal.machine.state.reason == "phase_deadline"
    assert len(journal.entries) == 3  # prepare, request, deadline; no polling spam
    assert not host.sent and results == [0.25] * 3


def test_changed_boot_rejected_before_any_host_read(setup):
    host, journal = setup
    run = session(host, journal)

    def forbidden():
        pytest.fail("No host observation is allowed after boot mismatch")

    run.read = forbidden
    host.boot = "c" * 32
    assert run.run(lambda _: pytest.fail("No wait needed")).phase == "review"
    assert journal.machine.state.reason == "host_boot_changed"
    assert not host.sent


def test_fixed_clock_loop_ceiling_is_not_success(setup, monkeypatch):
    host, journal = setup
    run = session(host, journal)
    monkeypatch.setattr(r, "TOTAL_SECONDS", 1)
    result = run.run(lambda _: None)
    assert result == e.Result("prepared", "poll_limit_unconfirmed")
    assert not host.sent and run.processes.closed


def test_loop_exception_closes_witnesses_and_preserves_journal(setup):
    host, journal = setup
    run = session(host, journal)

    def stopped(_):
        raise InterruptedError("local wait interrupted")

    with pytest.raises(InterruptedError):
        run.run(stopped)
    assert run.processes.closed and all(handle.closed for handle in host.handles)
    assert journal.fd >= 0 and journal.machine.state.processes
    assert not host.sent


def test_candidate_observation_failure_waits_only_to_fixed_recovery_deadline(setup):
    host, journal = setup
    run = session(host, journal)
    request(host, journal)
    poll(host, run)
    poll(host, run)
    assert poll(host, run).phase == "candidate_running"
    deadline = journal.machine.state.trial_deadline
    journal.append({"kind": "tick", "boot_id": BOOT, "now": deadline})
    assert journal.machine.state.phase == "candidate_running"
    host.now = deadline + p.COMMAND_SECONDS
    assert run.poll().phase == "review"
    assert journal.machine.state.reason == "recovery_observation_deadline"
    assert len(host.sent) == 2
    run.close()
