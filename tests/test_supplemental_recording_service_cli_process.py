"""Actual separate journal writer/observer with fixed synthetic Unix Engine.

Child owns the only Journal and real TrackedDispatch/Docker HTTP adapter; parent
owns read-only App/CliCustody and the other Link. No parent journal publication,
shared writer flock, live Engine, App command execution or recovery authority.
Only test-only source/root/cgroup eligibility and App/exec facts are synthetic.
Both direct-dispatch and original IdleService assembly paths are exercised.
"""

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from . import test_supplemental_recording_service_cli_channel as channel

m, old = channel.m, channel.old
apps, hosts = old.app_tests, old.host_tests
layout, tree, routing, projection = old.layout, old.tree, old.routing, old.projection
pytestmark = apps.pytestmark

CHILD = r"""
import json, os, socket, sys, time
from contextlib import ExitStack
from pathlib import Path
print("ready", flush=True)
line = sys.stdin.buffer.readline()
if not line:
    sys.exit(0)
config = json.loads(line)
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]).parent / "src")]
import supplemental_recording_service_cli_channel as m
m.ROOT_UID = os.geteuid()
def identity(pid, cid):
    return m.processes.process_identity(pid, cid, Path(f"/proc/{pid}/stat").read_text(),
        f"0::/system.slice/docker-{cid}.scope\n")
m.processes.read_identity = identity
plan = m.plans.load_bytes(config["plan"].encode(), config["sha256"])
original = service = None
if config["assembly"] == "service":
    import supplemental_recording_service_operator as operator
    m.plans.Plan.root = property(lambda _: Path(config["root"]))
    original = operator.intake.CasePlan(Path(config["root"]), config["sha256"])
    plan = original.plan
    layout = next(item for item in plan.layouts if item.slug == m.base.CANDIDATE)
    stored = m.plans.projection.recording._decode(config["manifest"].encode())
    projected = m.plans.projection.project(layout, stored)
    plan.check_projection(projected)
timer = m.clock.ClockWitness(plan.original_clock)
peer = m.processes.ProcessWitness(identity(os.getppid(), "9" * 64))
channels = m.Channels(
    socket.socket(fileno=int(sys.argv[2])), socket.socket(fileno=int(sys.argv[3])))
for endpoint in (channels.incoming, channels.outgoing):
    endpoint.setblocking(False)
    endpoint.set_inheritable(False)
link = m.Link(channels, plan, timer, peer, role="writer")
p = m.custody_module.platform
def emit(**values):
    print(json.dumps(values), flush=True)
def now():
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME) / m.clock.NS
def observe(notice):
    # Fixture notification only: the evidence exchange contains no journal data.
    emit(kind="boundary", stage=notice.stage, phase=notice.phase, receipt=notice.receipt)
    return link.observe(notice)
try:
    with ExitStack() as resources:
        journal = resources.enter_context(
            m.plans.bootstrap.Journal(Path(config["root"]) / "journal"))
        journal.append(config["preparation"])
        original_fd = journal.fd
        if config["assembly"] == "service":
            connection = p._UnixConnection
            def fixture_connection(path):
                assert path == "/var/run/docker.sock"
                return connection(config["engine"])
            p._UnixConnection = fixture_connection
            docker = p.Docker()
            service = operator.IdleService(original, projected, journal, docker,
                clock_witness=timer, dispatch_observer=observe)
            resources.callback(service.close)
            writer = service.dispatch
            owners = (service.session, service.session.executor, service.processes, writer)
            assert not service.used and not writer.used and len(journal.entries) == 1
        else:
            docker = p.Docker(config["engine"])
            writer = p.TrackedDispatch(journal, docker, cli_image=plan.cli_image,
                cli_generation=plan.cli_generation, now=now, observe=observe)
        def event(kind, **fields):
            return journal.append(dict(kind=kind, boot_id=plan.boot, now=now(), **fields))
        emit(kind="prepared")
        for line in sys.stdin.buffer:
            command = json.loads(line)
            try:
                if service is not None:
                    service._context()
                    assert owners == (service.session, service.session.executor,
                        service.processes, service.dispatch)
                    assert service.clock_witness is timer and writer is service.dispatch
                mode = command["mode"]
                if mode == "reconcile":
                    writer.reconcile_executions()
                elif mode == "candidate":
                    bound = journal.machine.state.processes[-1]
                    notice = m.custody_module.platform.CandidateNotice(
                        plan.sha256, bound.generation,
                        m.processes.ProcessIdentity(
                            bound.pid, bound.start_ticks, bound.container_id),
                        tuple(m.base.encode(e) for e in journal.entries))
                    if command.get("fault"):
                        from dataclasses import replace
                        fault = command["fault"]
                        if fault == "plan":
                            notice = replace(notice, plan_sha256="0" * 64)
                        elif fault == "generation":
                            notice = replace(notice, generation="0" * 64)
                        elif fault == "identity":
                            notice = replace(notice, process=replace(notice.process,
                                start_ticks=notice.process.start_ticks + 1))
                        elif fault == "history":
                            notice = replace(notice, history=notice.history[:-1])
                    emit(kind="candidate-boundary", receipt=notice.receipt)
                    link.observe_candidate(notice)
                elif mode == "retry":
                    writer(p.CONTROL[journal.machine.state.phase], plan.case)
                elif mode == "observe":
                    for extra in command.get("events", []):
                        event(**extra)
                    sample = command["observation"]
                    stamp = now()
                    sample["sampled_at"] = stamp
                    action = journal.append(dict(kind="observe", boot_id=plan.boot,
                        now=stamp, observation=sample))
                    if action is not None:
                        argv = ("ha", "apps", action.operation, action.slug, "--raw-json")
                        writer(argv, plan.case)
                else:
                    raise AssertionError("Unknown fixture operation")
                failed = False
            except (m.UnconfirmedExchange, m.base.UnsafeHandoff):
                failed = True
            assert journal.fd == original_fd
            state = journal.machine.state
            emit(kind="result", failed=failed, phase=state.phase, used=sorted(writer.used),
                recording=state.recording_outcome, completed=state.completed_executions,
                events=len(journal.entries), link_failed=link.failed)
finally:
    if original is not None:
        original.close()
    link.close()
    timer.close()
    peer.close()
    channels.close()
"""


@pytest.fixture(params=["dispatch", "service"])
def prepared(projection, tmp_path, monkeypatch, request):
    left, right = m.pair()
    popen, used = subprocess.Popen, []

    def start(command, **kw):
        if not used:
            used.append(True)
            assert command[-1] == "import sys;print('ready',flush=True);sys.stdin.read()"
            command = [
                sys.executable,
                "-I",
                "-B",
                "-c",
                CHILD,
                str(Path(m.__file__).parent),
                str(right.incoming.fileno()),
                str(right.outgoing.fileno()),
            ]
            kw["pass_fds"] = (right.incoming.fileno(), right.outgoing.fileno())
            # line() waits on the pipe fd. Buffered readline can prefetch the
            # next immediate failure result and hide it from select().
            kw["bufsize"] = 0
        return popen(command, **kw)

    monkeypatch.setattr(subprocess, "Popen", start)
    from . import test_supplemental_recording_idle_observer as idle

    iterator = idle.prepared.__wrapped__(tmp_path, monkeypatch)
    try:
        case = next(iterator)
        right.close()
        case.channel = left
        case.assembly = request.param
        yield case
    finally:
        left.close()
        right.close()
        iterator.close()


def receive(case):
    return json.loads(channel.line(case.prepared.child))


def record(binding):
    return asdict(
        m.base.ProcessRecord(
            binding.slug,
            binding.generation,
            binding.process.container_id,
            binding.process.pid,
            binding.process.start_ticks,
        )
    )


@contextmanager
def setup(prepared, projection, tmp_path, monkeypatch):
    _, raw = old.plan_tests.projected_plan(projection)
    previous = json.loads(prepared.plan.raw)
    raw.update({key: previous[key] for key in ("boot", "original_clock", "deadlines")})
    cli = hosts.container()
    raw.update(
        cli_image=cli["Image"],
        cli_generation=old.m.platform.generation(cli, name=old.m.platform.CLI, image=cli["Image"]),
    )
    prepared.plan = m.plans.decode(raw)
    monkeypatch.setattr(os, "open", old.REAL_OPEN)
    root = tmp_path / "separate-writer"
    root.mkdir(mode=0o700)
    (root / "journal").mkdir(mode=0o700)
    (root / "inbox").mkdir(mode=0o700)
    monkeypatch.setattr(m.plans.Plan, "root", property(lambda _: root))
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    executions, created, started = {}, [], []
    observer = None

    def mutate(peer, request):
        route, _, body = request
        if not route.startswith("POST "):
            return False
        assert observer is not None
        if route == f"POST /v1.47/containers/{hosts.CID}/exec HTTP/1.1":
            pending = observer._pending
            assert (
                pending is not None and tuple(body["Cmd"]) == old.m.platform.CONTROL[pending.phase]
            )
            assert body == dict(
                AttachStdin=False,
                AttachStdout=False,
                AttachStderr=False,
                Tty=False,
                Privileged=False,
                User="0",
                Cmd=list(old.m.platform.CONTROL[pending.phase]),
            )
            eid = f"{int(hosts.EID, 16) + len(created):064x}"
            executions[eid] = hosts.execution(tuple(body["Cmd"])) | {"ID": eid, "ExitCode": None}
            created.append((pending.phase, eid))
            peer.sendall(apps.engine_tests.reply({"Id": eid}, status=201))
        else:
            assert observer._pending is None and observer._executions
            execution = observer._executions[-1]
            eid = execution.binding.execution_id
            assert execution.state == "created" and eid not in started
            assert route == f"POST /v1.47/exec/{eid}/start HTTP/1.1"
            assert body == dict(Detach=True, Tty=False)
            started.append(eid)
            executions[eid].update(ExitCode=0)
            peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        return True

    with apps.setup(
        prepared,
        tmp_path,
        monkeypatch,
        executions=executions,
        containers={old.m.platform.CLI: cli},
        request_handler=mutate,
    ) as case:
        custody = case.create()
        plan = custody.plan
        (root / "plan.json").write_bytes(plan.raw)
        (root / "plan.json").chmod(0o600)
        baseline = old.m.bootstrap.recording.Observation(
            plan.deadlines.issued_at,
            m.base.App(plan.normal.pin, "running", plan.normal_generation, True, False),
            m.base.App(plan.candidate.pin, "stopped"),
            True,
            True,
            True,
            old.m.bootstrap.recording.Files(
                plan.candidate.contract.sha256, "pristine", plan.candidate.contract.baseline_sha256
            ),
        )
        channel.send(
            prepared.child,
            dict(
                plan=plan.raw.decode(),
                sha256=plan.sha256,
                root=str(root),
                engine=str(apps.m.engine.SOCKET),
                preparation=plan.preparation(baseline, projection),
                assembly=prepared.assembly,
                manifest=m.plans.projection._validated(projection.host).decode(),
            ),
        )
        assert receive(case) == dict(kind="prepared")
        link = None
        try:
            observer = old.m.CliCustody(custody, projection)
            link = m.Link(
                prepared.channel, plan, case.link.observer, prepared.witness, role="observer"
            )
            case.observer, case.exchange, case.custody = observer, link, custody
            case.baseline, case.root = baseline, root
            case.created, case.started, case.executions = created, started, executions
            case.cli = cli
            yield case
        finally:
            if link:
                link.close()
            if observer:
                observer.close()


def request(case, *, normal=None, candidate=None, events=()):
    baseline = asdict(case.baseline)
    if normal is not None:
        baseline["normal"] = asdict(normal)
    if candidate is not None:
        baseline["candidate"] = asdict(candidate)
    channel.send(
        case.prepared.child, dict(mode="observe", observation=baseline, events=list(events))
    )


def first(case):
    normal = case.custody._retained[0][0]
    request(case, events=[dict(kind="bind_process", process=record(normal)), dict(kind="request")])


def acknowledge(case, stage, phase):
    boundary = receive(case)
    assert boundary["kind"] == "boundary" and boundary["stage"] == stage
    assert boundary["phase"] == phase
    assert case.exchange.acknowledge(case.observer) == boundary["receipt"]


def complete_dispatch(case, phase):
    acknowledge(case, "before_create", phase)
    acknowledge(case, "before_start", phase)
    result = receive(case)
    assert result["kind"] == "result" and not result["failed"] and result["phase"] == phase
    assert result["recording"] == "not_attempted" and not result["link_failed"]
    assert all(e.state == "not_running" for e in case.observer.poll().executions)
    channel.send(case.prepared.child, dict(mode="reconcile"))
    reconciled = receive(case)
    assert not reconciled["failed"]
    assert len(reconciled["completed"]) == len(case.created) == len(case.started)


def candidate_idle(case):
    first(case)
    complete_dispatch(case, "stopping_normal")
    apps.exit_normal(case)
    plan = case.custody.plan
    stopped = m.base.App(plan.normal.pin, "stopped")
    request(
        case,
        normal=stopped,
        events=[dict(kind="process_exited", generation=plan.normal_generation)],
    )
    complete_dispatch(case, "starting_candidate")
    # Fixture writer's candidate hint, not observer acquisition. Only the new
    # authenticated exchange may acquire the observer's candidate pidfd.
    candidate = apps.m.Binding(
        m.base.CANDIDATE,
        case.generations[m.base.CANDIDATE],
        apps.m.processes.read_identity(case.candidate.pid, case.values[m.base.CANDIDATE]["Id"]),
    )
    idle = m.base.App(plan.candidate.pin, "running", candidate.generation, None, None)
    request(
        case,
        normal=stopped,
        candidate=idle,
        events=[dict(kind="bind_process", process=record(candidate))],
    )
    result = receive(case)
    assert result["phase"] == "candidate_idle" and not result["failed"]
    assert case.custody.poll().candidate is None and not case.custody.candidate_attempted
    return result


def test_separate_candidate_capture_acknowledges_original_process_before_native(
    prepared, projection, tmp_path, monkeypatch
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        before = candidate_idle(case)
        channel.send(case.prepared.child, dict(mode="candidate"))
        notice = receive(case)
        assert notice["kind"] == "candidate-boundary"
        assert case.exchange.acknowledge_candidate(case.observer) == notice["receipt"]
        after = receive(case)
        assert after == before  # Evidence alone never writes/launches/authorizes.
        status = case.custody.poll()
        assert status.candidate.process.pid == case.candidate.pid and not status.candidate_exited
        assert status.normal_exited and not status.deadline.helper_exited
        assert case.exchange.sequence == 4 and case.exchange.candidate_attempted
        assert len(case.created) == len(case.started) == 2
        case.prepared.child.stdin.close()
        assert case.prepared.child.wait(timeout=3) == 0
        retained = case.custody.poll()
        assert retained.candidate == status.candidate and not retained.candidate_exited
        assert retained.deadline.helper_exited


@pytest.mark.parametrize(
    "fault",
    [
        "plan",
        "generation",
        "identity",
        "history",
        "exited",
        "not_terminal",
        "inspection_failure",
        "lost",
    ],
)
def test_candidate_capture_failure_never_allows_retry_or_new_action(
    prepared, projection, tmp_path, monkeypatch, fault
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        before = candidate_idle(case)
        if fault == "exited":
            case.candidate.stdin.close()
            assert case.candidate.wait(timeout=3) == 0
        elif fault == "not_terminal":
            values = case.observer._executions
            case.observer._retain(
                (*values[:-1], replace(values[-1], state="running", exit_code=None))
            )
        elif fault == "inspection_failure":

            def changed(value, count):
                case.observer.inspection_failed = True
                return value

            case.state.hook = changed
        elif fault == "lost":

            def lost(*args):
                raise OSError("PRIVATE lost candidate custody receipt")

            monkeypatch.setattr(case.exchange, "_send", lost)
        channel.send(case.prepared.child, dict(mode="candidate", fault=fault))
        boundary = receive(case)
        assert boundary["kind"] == "candidate-boundary"
        channel.denied(lambda: case.exchange.acknowledge_candidate(case.observer))
        result = receive(case)
        assert result == before | dict(failed=True, link_failed=True)
        captured = case.custody.poll().candidate
        assert (captured is not None) is (fault in ("lost", "inspection_failure"))
        channel.send(case.prepared.child, dict(mode="candidate"))
        assert receive(case)["kind"] == "candidate-boundary"
        assert receive(case) == result
        channel.denied(lambda: case.exchange.acknowledge_candidate(case.observer))
        assert len(case.created) == len(case.started) == 2
        assert case.custody.poll().candidate == captured


@pytest.mark.parametrize("observed_candidate", [False, True])
def test_separate_original_writer_and_observer_cover_all_four_fixed_commands(
    prepared, projection, tmp_path, monkeypatch, observed_candidate
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        # Real cross-process flock exclusion: observer has never acquired it.
        with pytest.raises(BlockingIOError):
            old.m.bootstrap.Journal(case.root / "journal")
        first(case)
        complete_dispatch(case, "stopping_normal")
        apps.exit_normal(case)
        plan = case.custody.plan
        stopped = m.base.App(plan.normal.pin, "stopped")
        request(
            case,
            normal=stopped,
            events=[dict(kind="process_exited", generation=plan.normal_generation)],
        )
        complete_dispatch(case, "starting_candidate")
        if observed_candidate:
            candidate = apps.m.Binding(
                m.base.CANDIDATE,
                case.generations[m.base.CANDIDATE],
                apps.m.processes.read_identity(
                    case.candidate.pid, case.values[m.base.CANDIDATE]["Id"]
                ),
            )
        else:
            candidate = case.custody.capture_candidate(case.generations[m.base.CANDIDATE])
        idle = m.base.App(plan.candidate.pin, "running", candidate.generation, None, None)
        request(
            case,
            normal=stopped,
            candidate=idle,
            events=[dict(kind="bind_process", process=record(candidate))],
        )
        result = receive(case)
        assert result["phase"] == "candidate_idle" and not result["failed"]
        if observed_candidate:
            channel.send(case.prepared.child, dict(mode="candidate"))
            boundary = receive(case)
            assert boundary["kind"] == "candidate-boundary"
            assert case.exchange.acknowledge_candidate(case.observer) == boundary["receipt"]
            assert receive(case) == result
            assert case.custody.poll().candidate == candidate
        request(case, normal=stopped, candidate=idle, events=[dict(kind="finish")])
        complete_dispatch(case, "stopping_candidate")
        case.candidate.stdin.close()
        assert case.candidate.wait(timeout=3) == 0
        request(
            case,
            normal=stopped,
            events=[dict(kind="process_exited", generation=candidate.generation)],
        )
        complete_dispatch(case, "starting_normal")
        assert [phase for phase, _ in case.created] == list(old.m.platform.CONTROL)
        assert case.exchange.sequence == 8 and not case.exchange.failed
        assert case.exchange.candidate_attempted is observed_candidate
        assert len({e.binding.execution_id for e in case.observer.poll().executions}) == 4
        # The fixture has not restored an App; no fabricated success is asserted.
        assert len(case.started) == 4


@pytest.mark.parametrize("stage", ["before_create", "before_start"])
def test_lost_cross_process_reply_blocks_mutation_and_original_writer_retry(
    prepared, projection, tmp_path, monkeypatch, stage
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        first(case)
        if stage == "before_start":
            acknowledge(case, "before_create", "stopping_normal")
        boundary = receive(case)
        assert boundary["stage"] == stage

        def lost(*args):
            raise OSError("PRIVATE fixture reply lost")

        monkeypatch.setattr(case.exchange, "_send", lost)
        channel.denied(lambda: case.exchange.acknowledge(case.observer))
        result = receive(case)
        assert result["failed"] and result["link_failed"] and not result["completed"]
        assert result["phase"] == "stopping_normal" and not case.started
        assert len(case.created) == (stage == "before_start")
        status = case.observer.poll()
        if stage == "before_create":
            assert status.pending_intent == ("stopping_normal", boundary["receipt"])
            assert not status.executions
        else:
            assert status.pending_intent is None and status.executions[0].state == "created"
            assert status.executions[0].exit_code is None
        channel.send(case.prepared.child, dict(mode="retry"))
        retried = receive(case)
        assert retried == result  # No new journal event or boundary request.
        assert not case.started and len(case.created) == (stage == "before_start")
        # End only the owned fixture writer. Observer retains its original
        # facts after writer/journal cleanup, without taking the vacated lock.
        case.prepared.child.stdin.close()
        assert case.prepared.child.wait(timeout=3) == 0
        after = case.observer.poll()
        assert after.apps.deadline.helper_exited and not after.apps.normal_exited
        assert after.pending_intent == status.pending_intent
        assert after.executions == status.executions
        assert not case.started and len(case.created) == (stage == "before_start")


@pytest.mark.parametrize("fault", ["generation", "already_running"])
def test_writer_rechecks_original_cli_and_created_exec_after_real_peer_ack(
    prepared, projection, tmp_path, monkeypatch, fault
):
    with setup(prepared, projection, tmp_path, monkeypatch) as case:
        first(case)
        acknowledge(case, "before_create", "stopping_normal")
        send = case.exchange._send

        def changed_after_capture(raw, end):
            # Deterministic seam AFTER independent capture, BEFORE the real
            # peer acknowledgment. Writer must re-read, not trust that receipt.
            if fault == "generation":
                case.cli["State"]["Pid"] += 1
            else:
                case.executions[hosts.EID].update(Running=True, Pid=444, ExitCode=None)
            send(raw, end)

        monkeypatch.setattr(case.exchange, "_send", changed_after_capture)
        acknowledge(case, "before_start", "stopping_normal")
        result = receive(case)
        assert result["failed"] and not result["link_failed"]
        assert not result["completed"] and result["recording"] == "not_attempted"
        assert len(case.created) == 1 and not case.started
        channel.send(case.prepared.child, dict(mode="retry"))
        assert receive(case) == result
        # A valid evidence reply is neither App permission nor exec/start.
        assert case.exchange.sequence == 2 and not case.exchange.failed
