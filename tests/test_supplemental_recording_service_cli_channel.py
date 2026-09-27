"""Actual anonymous datagrams, credentials, pidfds and clocks; no live Apps.

The separate child runs the actual writer Link. Observer composition uses real
CliCustody and its authenticated synthetic Engine. Journal events are fixture
publications, not a qualified cross-process active command or recovery handoff.
"""

import importlib.util
import json
import os
import select
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_control as control_tests  # noqa: F401
from . import test_supplemental_recording_service_cli_custody as old

NAME = "supplemental_recording_service_cli_channel"
SPEC = importlib.util.spec_from_file_location(NAME, Path(old.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
layout, tree, routing, projection = old.layout, old.tree, old.routing, old.projection

CHILD = r"""
import json, os, socket, struct, sys, time
from pathlib import Path
print("ready", flush=True)
first = sys.stdin.buffer.readline()
if not first:
    sys.exit(0)
config = json.loads(first)
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]).parent / "src")]
import supplemental_recording_service_cli_channel as m
m.ROOT_UID = os.geteuid()
def identity(pid, cid):
    return m.processes.process_identity(pid, cid, Path(f"/proc/{pid}/stat").read_text(),
        f"0::/system.slice/docker-{cid}.scope\n")
m.processes.read_identity = identity
channel = m.Channels(socket.socket(fileno=int(sys.argv[2])), socket.socket(fileno=int(sys.argv[3])))
for endpoint in (channel.incoming, channel.outgoing):
    endpoint.setblocking(False)
    endpoint.set_inheritable(False)
plan = m.plans.load_bytes(config["plan"].encode(), config["sha256"])
timer = m.clock.ClockWitness(plan.original_clock)
peer = m.processes.ProcessWitness(identity(os.getppid(), "9" * 64))
link = m.Link(channel, plan, timer, peer, role=config["role"])
print("bound", flush=True)
try:
    for line in sys.stdin.buffer:
        command = json.loads(line)
        mode = command.get("mode", "writer")
        try:
            if mode == "writer":
                notice = m.custody_module.platform.DispatchNotice(
                    **command["notice"],
                    history=tuple(bytes.fromhex(x) for x in command["history"]))
                reply = link.observe(notice)
                print(json.dumps(dict(receipt=reply, failed=link.failed)), flush=True)
            elif mode == "raw":
                raw = bytes.fromhex(command["raw"])
                if command.get("rights"):
                    fd = os.open("/dev/null", os.O_RDONLY)
                    try:
                        ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", fd))]
                        channel.outgoing.sendmsg([raw], ancillary)
                    finally:
                        os.close(fd)
                else:
                    channel.outgoing.send(raw)
                if command.get("duplicate"):
                    channel.outgoing.send(raw)
                print("sent", flush=True)
            elif mode == "echo":
                end = time.monotonic() + 2
                request, _ = link._receive(end)
                reply = dict(schema=1, kind=request["kind"], plan=plan.sha256,
                    sequence=request["sequence"],
                    nonce=request["nonce"], request_sha256=m.base.checksum(request),
                    receipt=request["notice"]["receipt"])
                fault = command.get("fault")
                if fault in ("nonce", "request_sha256", "receipt", "plan"):
                    reply[fault] = "f" * 64
                elif fault == "sequence":
                    reply["sequence"] += 1
                elif fault == "kind":
                    reply["kind"] = (m.KIND if request["kind"] == m.CANDIDATE_KIND
                        else m.CANDIDATE_KIND)
                elif fault == "schema":
                    reply["schema"] = True
                elif fault == "extra":
                    reply["extra"] = "PRIVATE"
                elif fault == "late":
                    time.sleep(2.1)
                elif fault == "eof":
                    channel.outgoing.close()
                    print("sent", flush=True)
                    continue
                raw = m.base.encode(reply)
                if fault == "oversize":
                    raw += b" " * 4096
                if fault == "duplicate-key":
                    raw = raw[:-1] + b',"schema":1}'
                if fault == "forwarder":
                    pid = os.fork()
                    if pid == 0:
                        channel.outgoing.send(raw)
                        os._exit(0)
                    os.waitpid(pid, 0)
                elif fault == "rights":
                    fd = os.open("/dev/null", os.O_RDONLY)
                    ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", fd))]
                    channel.outgoing.sendmsg([raw], ancillary)
                    os.close(fd)
                else:
                    channel.outgoing.send(raw)
                if fault == "duplicate":
                    channel.outgoing.send(raw)
                print("sent", flush=True)
        except m.UnconfirmedExchange:
            print(json.dumps(dict(failed=link.failed, message=m.MESSAGE)), flush=True)
finally:
    link.close()
    timer.close()
    peer.close()
    channel.close()
"""


def send(child, value):
    child.stdin.write(m.base.encode(value) + b"\n")
    child.stdin.flush()


def line(child):
    assert select.select([child.stdout], [], [], 5)[0], "Owned fixture did not answer"
    result = child.stdout.readline()
    assert result, "Owned fixture exited without a result"
    return result.strip()


def bind(child, plan, role):
    send(child, dict(plan=plan.raw.decode(), sha256=plan.sha256, role=role))
    assert line(child) == b"bound"


def args(channel):
    return [
        sys.executable,
        "-I",
        "-B",
        "-c",
        CHILD,
        str(Path(m.__file__).parent),
        str(channel.incoming.fileno()),
        str(channel.outgoing.fileno()),
    ]


def notice(plan, history=(b"fixture",), stage="before_create", eid=None):
    return m.custody_module.platform.DispatchNotice(
        stage, "stopping_normal", plan.case, plan.boot, old.host_tests.CID, eid, history
    )


def native_notice(case, projection):
    """Protocol-only synthetic facts; custody tests independently capture actors."""
    admission = m.custody_module.admission
    dispatch, namespace = admission.dispatch, admission.namespace
    init = case.peer.identity
    pins = dispatch.Pins(
        dispatch.binding.Binding(projection, "a" * 64, case.plan.sha256, case.plan.boot),
        dispatch.execution.Command(
            str(case.plan.native_root / "launch/launch.json"),
            "b" * 64,
            "a" * 64,
            case.plan.lease["ready_by"],
        ),
        "c" * 64,
        init,
    )
    domains = tuple((1, i + 1) for i in range(len(namespace.NAMESPACES)))
    actors = tuple(
        namespace.Actor(
            init.pid if i == 0 else 1_000_000 + i,
            i + 1,
            0 if i == 0 else init.pid,
            init.start_ticks + i,
            init.container_id,
            domains,
        )
        for i in range(4)
    )
    return admission.NativeNotice(pins, *(str(i) * 64 for i in range(1, 7)), actors, (b"fixture",))


def writer(child, current):
    value = asdict(current)
    history = value.pop("history")
    send(child, dict(notice=value, history=[raw.hex() for raw in history]))


def denied(action):
    with pytest.raises(m.UnconfirmedExchange) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def fixture_identity(pid, cid):
    return m.processes.process_identity(
        pid, cid, Path(f"/proc/{pid}/stat").read_text(), f"0::/system.slice/docker-{cid}.scope\n"
    )


@contextmanager
def protocol(monkeypatch):
    original = m.clock.read()
    value = old.plan_tests.value()
    issued = original.boottime_ns / m.clock.NS
    value.update(
        boot=original.boot,
        original_clock=asdict(original) | {"namespace": list(original.namespace)},
        deadlines=dict(
            issued_at=issued, ready_by=issued + 60, stop_by=issued + 300, recover_by=issued + 1500
        ),
    )
    plan = m.plans.decode(value)
    left, right = m.pair()
    child = subprocess.Popen(
        args(right),
        pass_fds=(right.incoming.fileno(), right.outgoing.fileno()),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    right.close()
    peer = timer = link = None
    try:
        assert line(child) == b"ready"
        monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
        monkeypatch.setattr(m.processes, "read_identity", fixture_identity)
        peer = m.processes.ProcessWitness(fixture_identity(child.pid, "a" * 64))
        timer = m.clock.ClockWitness(plan.original_clock)
        bind(child, plan, "observer")
        link = m.Link(left, plan, timer, peer, role="writer")
        yield SimpleNamespace(
            link=link, child=child, plan=plan, channel=left, peer=peer, timer=timer
        )
    finally:
        if link:
            link.close()
        left.close()
        child.stdin.close()
        child.wait(timeout=5)
        child.stdout.close()
        if timer:
            timer.close()
        if peer:
            peer.close()
        assert child.returncode == 0


def test_actual_writer_peer_receipt_is_not_an_action_or_restoration(monkeypatch):
    with protocol(monkeypatch) as case:
        current = notice(case.plan)
        send(case.child, dict(mode="echo"))
        assert case.link.observe(current) == current.receipt
        assert line(case.child) == b"sent"
        assert case.link.sequence == 1 and not case.link.failed
        assert not hasattr(case.link, "start") and not hasattr(case.link, "journal")
        borrowed = [
            case.channel.incoming.fileno(),
            case.channel.outgoing.fileno(),
            case.peer.fd,
            case.timer.fd,
        ]
        owned = [case.link.peer_fd, *(fd for _, fd, _ in case.link._owned)]
        case.link.close()
        case.link.close()
        for fd in owned:
            with pytest.raises(OSError):
                os.fstat(fd)
        assert all(os.fstat(fd) for fd in borrowed)


@pytest.mark.parametrize(
    "fault",
    [
        "nonce",
        "request_sha256",
        "receipt",
        "plan",
        "sequence",
        "schema",
        "extra",
        "late",
        "eof",
        "oversize",
        "duplicate-key",
        "duplicate",
        "forwarder",
        "rights",
        "kind",
    ],
)
@pytest.mark.parametrize("kind", ["cli", "candidate", "native"])
def test_wrong_lost_replayed_or_forwarded_reply_latches_failure(
    projection, monkeypatch, fault, kind
):
    with protocol(monkeypatch) as case:
        descriptors = set(os.listdir("/proc/self/fd"))
        if kind != "cli":
            # Protocol-only counter setup; the separate-process custody suite
            # reaches this boundary via all four actual CLI evidence exchanges.
            case.link.sequence = case.link._sequence = 4
        if kind == "native":
            case.link.candidate_attempted = case.link._candidate_attempted = True
            current, observe = native_notice(case, projection), case.link.observe_native
        elif kind == "candidate":
            current = m.custody_module.platform.CandidateNotice(
                case.plan.sha256, "b" * 64, case.peer.identity, (b"fixture",)
            )
            observe = case.link.observe_candidate
        else:
            current, observe = notice(case.plan), case.link.observe
        send(case.child, dict(mode="echo", fault=fault))
        denied(lambda: observe(current))
        assert line(case.child) == b"sent"
        denied(lambda: observe(current))
        assert case.link.failed and case.link.sequence == (1 if kind == "cli" else 4)
        assert set(os.listdir("/proc/self/fd")) == descriptors


@pytest.mark.parametrize("count", [0, 1, 2, 3, 5, 6, 7, 8])
@pytest.mark.parametrize("native", [False, True])
def test_auxiliary_protocol_refuses_any_other_cli_boundary(monkeypatch, count, native):
    with protocol(monkeypatch) as case:
        case.link.sequence = case.link._sequence = count
        current = m.custody_module.platform.CandidateNotice(
            case.plan.sha256, "b" * 64, case.peer.identity, (b"fixture",)
        )
        observe = case.link.observe_native if native else case.link.observe_candidate
        denied(lambda: observe(current))
        assert case.link.sequence == count and not case.link.candidate_attempted
        assert not case.link.native_attempted
        assert case.link.failed


def test_native_protocol_requires_original_candidate_exchange(monkeypatch):
    with protocol(monkeypatch) as case:
        case.link.sequence = case.link._sequence = 4
        denied(lambda: case.link.observe_native(object()))
        assert not case.link.native_attempted and case.link.failed


@pytest.mark.parametrize(
    "fault", ["peer", "plan", "timer", "blocking", "passcred", "sequence", "namespace"]
)
def test_changed_original_context_refuses_before_any_send(monkeypatch, fault):
    with protocol(monkeypatch) as case:
        link = case.link
        if fault == "peer":
            link._peer = case.peer
        elif fault == "plan":
            link.plan = m.plans.load_bytes(case.plan.raw, case.plan.sha256)
        elif fault == "timer":
            case.timer.close()
        elif fault == "blocking":
            fd = case.channel.outgoing.fileno()
            m.fcntl.fcntl(fd, m.fcntl.F_SETFL, m.fcntl.fcntl(fd, m.fcntl.F_GETFL) & ~os.O_NONBLOCK)
        elif fault == "passcred":
            case.channel.incoming.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 0)
        elif fault == "sequence":
            link.sequence = True
        else:
            real = os.stat

            def changed(path, *a, **kw):
                value = real(path, *a, **kw)
                if path == f"/proc/{case.child.pid}/ns/time":
                    return SimpleNamespace(
                        st_dev=value.st_dev,
                        st_ino=value.st_ino + 1,
                        st_mode=value.st_mode,
                        st_uid=value.st_uid,
                        st_gid=value.st_gid,
                    )
                return value

            monkeypatch.setattr(os, "stat", changed)
        denied(lambda: link.observe(notice(case.plan)))
        assert link.failed


def test_foreign_thread_refusal_does_not_release_another_owner_lock(monkeypatch):
    with protocol(monkeypatch) as case:
        assert case.link.lock.acquire(blocking=False)
        outcomes = []

        def run():
            denied(lambda: case.link.observe(notice(case.plan)))
            outcomes.append(True)

        child = Thread(target=run)
        child.start()
        child.join(timeout=3)
        assert not child.is_alive() and outcomes == [True]
        assert case.link.lock.locked() and case.link.failed
        case.link.lock.release()


def test_exact_eight_exchange_cap_does_not_renew_case(monkeypatch):
    with protocol(monkeypatch) as case:
        original = case.plan.raw, case.timer.original
        for _ in range(8):
            send(case.child, dict(mode="echo"))
            assert case.link.observe(notice(case.plan)) == notice(case.plan).receipt
            assert line(case.child) == b"sent"
        denied(lambda: case.link.observe(notice(case.plan)))
        assert case.link.sequence == 8 and original == (case.plan.raw, case.timer.original)


def test_peer_exit_never_becomes_a_cached_acknowledgment(monkeypatch):
    with protocol(monkeypatch) as case:
        case.child.stdin.close()
        assert case.child.wait(timeout=3) == 0
        denied(lambda: case.link.observe(notice(case.plan)))
        assert case.link.sequence == 0


def test_original_recovery_deadline_and_clock_offset_stay_authoritative(monkeypatch):
    with protocol(monkeypatch) as case:
        sample = m.clock.read()
        later = 1600 * m.clock.NS
        sample = replace(
            sample,
            before_ns=sample.before_ns + later,
            after_ns=sample.after_ns + later,
            boottime_ns=sample.boottime_ns + later,
        )
        monkeypatch.setattr(m.clock, "read", lambda: sample)
        denied(lambda: case.link.observe(notice(case.plan)))
        assert case.link.sequence == 0


@pytest.mark.parametrize("position", [1, 2, 4, 6])
def test_partial_namespace_capture_closes_only_new_owned_descriptors(monkeypatch, position):
    with protocol(monkeypatch) as case:
        before = set(os.listdir("/proc/self/fd"))
        real, calls = os.open, []

        def failed(path, flags, *a, **kw):
            if isinstance(path, str) and path.startswith("/proc/") and "/ns/" in path:
                calls.append(path)
                if len(calls) == position:
                    raise OSError("PRIVATE capture fault")
            return real(path, flags, *a, **kw)

        monkeypatch.setattr(os, "open", failed)
        denied(lambda: m.Link(case.channel, case.plan, case.timer, case.peer, role="writer"))
        assert set(os.listdir("/proc/self/fd")) == before and not case.peer.exited()


def test_changed_public_peer_descriptor_cannot_leak_owned_or_close_borrowed(monkeypatch):
    with protocol(monkeypatch) as case:
        owned = case.link.peer_fd
        spare = os.open("/dev/null", os.O_RDONLY)
        try:
            case.link._peer.fd = spare
            denied(lambda: case.link.observe(notice(case.plan)))
            case.link.close()
            with pytest.raises(OSError):
                os.fstat(owned)
            assert os.fstat(spare) and os.fstat(case.peer.fd)
        finally:
            os.close(spare)


@pytest.fixture
def prepared(projection, tmp_path, monkeypatch):
    """Give the existing owned-helper fixture only the private writer endpoints."""
    left, right = m.pair()
    original_popen, used = subprocess.Popen, []

    def start(command, **kw):
        if not used:
            used.append(True)
            assert command[-1] == "import sys;print('ready',flush=True);sys.stdin.read()"
            command = args(right)
            kw["pass_fds"] = (right.incoming.fileno(), right.outgoing.fileno())
        return original_popen(command, **kw)

    monkeypatch.setattr(subprocess, "Popen", start)
    from . import test_supplemental_recording_idle_observer as idle

    iterator = idle.prepared.__wrapped__(tmp_path, monkeypatch)
    try:
        case = next(iterator)
        right.close()
        case.channel = left
        yield case
    finally:
        left.close()
        right.close()
        iterator.close()


@contextmanager
def observed(prepared, projection, tmp_path, monkeypatch):
    with old.setup(prepared, projection, tmp_path, monkeypatch) as case:
        monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
        plan = case.custody.plan
        bind(prepared.child, plan, "writer")
        link = m.Link(prepared.channel, plan, case.link.observer, prepared.witness, role="observer")
        try:
            old.intent(case)
            current = notice(plan, tuple(m.base.encode(e) for e in case.journal.entries))
            yield case, link, current
        finally:
            link.close()


@pytest.mark.skipif(
    not old.m.deadlines.timerfd_available(), reason="Actual custody requires timerfd"
)
def test_both_real_peers_capture_original_intent_and_created_exec(
    prepared, projection, tmp_path, monkeypatch
):
    with observed(prepared, projection, tmp_path, monkeypatch) as (case, link, current):
        journal_fd = case.journal.fd
        writer(prepared.child, current)
        assert link.acknowledge(case.cli_observer) == current.receipt
        assert json.loads(line(prepared.child)) == dict(receipt=current.receipt, failed=False)
        assert case.cli_observer.poll().pending_intent == (current.phase, current.receipt)
        eid = old.host_tests.EID
        case.executions[eid] = old.host_tests.execution() | {"ExitCode": None}
        case.journal.append(
            dict(
                kind="bind_execution",
                boot_id=case.custody.plan.boot,
                now=old.now(),
                container_id=old.host_tests.CID,
                execution_id=eid,
            )
        )
        current = notice(
            case.custody.plan,
            tuple(m.base.encode(e) for e in case.journal.entries),
            "before_start",
            eid,
        )
        writer(prepared.child, current)
        assert link.acknowledge(case.cli_observer) == current.receipt
        assert json.loads(line(prepared.child)) == dict(receipt=current.receipt, failed=False)
        status = case.cli_observer.poll()
        assert status.pending_intent is None and status.executions[0].state == "created"
        assert status.executions[0].exit_code is None
        assert case.journal.fd == journal_fd and not case.docker.created and not case.docker.started


@pytest.mark.skipif(
    not old.m.deadlines.timerfd_available(), reason="Actual custody requires timerfd"
)
@pytest.mark.parametrize(
    "fault", ["history", "plan", "sequence", "stale", "future", "extended", "rights", "duplicate"]
)
def test_observer_does_not_ack_unbound_or_stale_evidence(
    prepared, projection, tmp_path, monkeypatch, fault
):
    with observed(prepared, projection, tmp_path, monkeypatch) as (case, link, current):
        issued = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        request = dict(
            schema=1,
            kind=m.KIND,
            plan=case.custody.plan.sha256,
            sequence=1,
            nonce="d" * 64,
            notice=m._notice(current),
            issued_ns=issued,
            until_ns=issued + m.clock.NS,
        )
        if fault == "history":
            request["notice"]["receipt"] = "f" * 64
        elif fault == "plan":
            request["plan"] = "f" * 64
        elif fault == "sequence":
            request["sequence"] = True
        elif fault == "stale":
            request.update(issued_ns=issued - 2 * m.clock.NS, until_ns=issued - m.clock.NS)
        elif fault == "future":
            request.update(issued_ns=issued + m.clock.NS, until_ns=issued + 2 * m.clock.NS)
        elif fault == "extended":
            request["until_ns"] += 3 * m.clock.NS
        descriptors = set(os.listdir("/proc/self/fd"))
        send(
            prepared.child,
            dict(
                mode="raw",
                raw=m.base.encode(request).hex(),
                rights=fault == "rights",
                duplicate=fault == "duplicate",
            ),
        )
        assert line(prepared.child) == b"sent"
        denied(lambda: link.acknowledge(case.cli_observer))
        denied(lambda: link.acknowledge(case.cli_observer))
        assert case.cli_observer.poll().pending_intent is None
        assert set(os.listdir("/proc/self/fd")) == descriptors
        assert not case.docker.created and not case.docker.started


@pytest.mark.skipif(
    not old.m.deadlines.timerfd_available(), reason="Actual custody requires timerfd"
)
def test_lost_observer_reply_retains_facts_without_retry_or_success(
    prepared, projection, tmp_path, monkeypatch
):
    with observed(prepared, projection, tmp_path, monkeypatch) as (case, link, current):

        def lost(*args):
            raise OSError("PRIVATE lost reply")

        monkeypatch.setattr(link, "_send", lost)
        writer(prepared.child, current)
        denied(lambda: link.acknowledge(case.cli_observer))
        result = json.loads(line(prepared.child))
        assert result == dict(failed=True, message=m.MESSAGE)
        status = case.cli_observer.poll()
        assert status.pending_intent == (current.phase, current.receipt)
        assert not status.executions and not status.capture_failed
        assert not case.docker.created and not case.docker.started
        denied(lambda: link.acknowledge(case.cli_observer))
        writer(prepared.child, current)
        assert json.loads(line(prepared.child)) == result


@pytest.mark.skipif(
    not old.m.deadlines.timerfd_available(), reason="Actual custody requires timerfd"
)
def test_substituted_history_cannot_be_read_before_custody_binding_check(
    prepared, projection, tmp_path, monkeypatch
):
    with observed(prepared, projection, tmp_path, monkeypatch) as (case, link, current):
        reads = []
        monkeypatch.setattr(
            case.cli_observer, "_history", SimpleNamespace(read=lambda end: reads.append(end))
        )
        writer(prepared.child, current)
        denied(lambda: link.acknowledge(case.cli_observer))
        assert json.loads(line(prepared.child)) == dict(failed=True, message=m.MESSAGE)
        assert not reads and not case.docker.created and not case.docker.started
