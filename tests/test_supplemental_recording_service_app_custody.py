"""Actual observer pidfds/Unix Engine transport; no live Docker or Apps.

Owned harmless children represent helper and App init processes. Only Docker
metadata and cgroup membership are synthetic. No source/runtime admission,
App dispatch, native lifetime, journal failover or installed recovery is claimed.
"""

import ast
import importlib.util
import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_engine as engine_tests
from . import test_supplemental_recording_service_deadline as deadline_tests

NAME = "supplemental_recording_service_app_custody"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(deadline_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
prepared = deadline_tests.prepared
pytestmark = pytest.mark.skipif(
    not m.deadlines.timerfd_available(),
    reason="Actual Linux timerfd custody requires Python 3.13+ APIs",
)


def denied(action):
    with pytest.raises(m.UnconfirmedCustody) as caught:
        action()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


@contextmanager
def child():
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys;print('ready',flush=True);sys.stdin.read()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert select.select([process.stdout], [], [], 3)[0]
        assert process.stdout.readline() == b"ready\n"
        yield process
    finally:
        process.stdin.close()
        process.wait(timeout=3)
        process.stdout.close()
        assert process.returncode in (0, -signal.SIGKILL)


def metadata(process, slug, image, cid):
    return dict(
        Id=cid,
        Name="/app_" + slug,
        Image=image,
        State=dict(
            Status="running",
            Running=True,
            Paused=False,
            Restarting=False,
            Dead=False,
            OOMKilled=False,
            Error="",
            Pid=process.pid,
            StartedAt="2026-09-27T01:02:03.000Z",
        ),
    )


@contextmanager
def server(
    tmp_path,
    monkeypatch,
    values,
    *,
    sender=False,
    authenticated=True,
    executions=None,
    containers=None,
    request_handler=None,
):
    path = tmp_path / "engine.sock"
    tmp_path.chmod(0o700)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    path.chmod(0o600)
    listener.listen(2)
    listener.settimeout(0.05)
    for module in (m.engine, m.engine.senders):
        monkeypatch.setattr(module, "ROOT_UID", os.geteuid())
        monkeypatch.setattr(module, "ROOT_GID", os.getegid())
    monkeypatch.setattr(m.engine, "SOCKET", path)
    stop = Event()
    state = SimpleNamespace(requests=[], errors=[], hook=None, idle=Event())

    def serve():
        while not stop.is_set():
            try:
                peer, _ = listener.accept()
            except TimeoutError:
                continue
            state.idle.clear()
            try:
                peer.settimeout(3)
                request = engine_tests.read_request(peer)
                if sender and request is not None:
                    assert request[0] == "GET /v1.47/_ping HTTP/1.1"
                    peer.sendall(engine_tests.attached.senders.REPLY)
                    request = engine_tests.read_request(peer)
                if request is None:
                    continue
                state.requests.append(request)
                if request_handler is not None and request_handler(peer, request):
                    continue
                assert request[2] is None
                paths = {
                    f"GET /v1.47/containers/app_{slug}/json HTTP/1.1": value
                    for slug, value in values.items()
                }
                paths.update(
                    {
                        f"GET /v1.47/containers/{key}/json HTTP/1.1": value
                        for key, value in (containers or {}).items()
                    }
                )
                paths.update(
                    {
                        f"GET /v1.47/exec/{key}/json HTTP/1.1": value
                        for key, value in (executions or {}).items()
                    }
                )
                assert request[0] in paths  # No mutations, signals, execs or arbitrary paths.
                value = deepcopy(paths[request[0]])
                if state.hook:
                    value = state.hook(value, len(state.requests))
                peer.sendall(engine_tests.reply(value))
            except BaseException as error:
                state.errors.append(error)
            finally:
                peer.close()
                state.idle.set()

    worker = Thread(target=serve)
    worker.start()
    endpoint = None
    try:
        endpoint = m.engine.Endpoint(sender_credentials=sender)
        if authenticated:
            # Authenticate the real local socket peer; no container operation.
            endpoint.connect(deadline=time.monotonic() + 1).close()
            assert state.idle.wait(timeout=3)
        yield endpoint, state
    finally:
        if endpoint:
            endpoint.close()
        stop.set()
        worker.join(timeout=4)
        listener.close()
        assert not worker.is_alive() and not state.errors, state.errors


@contextmanager
def setup(
    prepared,
    tmp_path,
    monkeypatch,
    *,
    sender=False,
    authenticated=True,
    short=False,
    executions=None,
    containers=None,
    request_handler=None,
):
    with child() as normal, child() as candidate:
        values = {
            m.NORMAL: metadata(normal, m.NORMAL, prepared.plan.normal.image, "b" * 64),
            m.CANDIDATE: metadata(candidate, m.CANDIDATE, prepared.plan.candidate.image, "c" * 64),
        }
        generations = {
            slug: m.platform.generation(value, name="app_" + slug, image=value["Image"])
            for slug, value in values.items()
        }
        raw = json.loads(prepared.plan.raw)
        raw["normal_generation"] = generations[m.NORMAL]
        prepared.plan = m.deadlines.links.plans.decode(raw)
        actual_ids = {
            prepared.child.pid: prepared.identity.container_id,
            normal.pid: values[m.NORMAL]["Id"],
            candidate.pid: values[m.CANDIDATE]["Id"],
        }

        def identity(pid, cid):
            assert actual_ids[pid] == cid
            return m.processes.process_identity(
                pid,
                cid,
                Path(f"/proc/{pid}/stat").read_text(),
                f"0::/system.slice/docker-{cid}.scope\n",
            )

        monkeypatch.setattr(m.processes, "read_identity", identity)
        with deadline_tests.original_link(prepared, monkeypatch, short=short) as link:
            watch = m.deadlines.DeadlineWatch(link)
            try:
                with server(
                    tmp_path,
                    monkeypatch,
                    values,
                    sender=sender,
                    authenticated=authenticated,
                    executions=executions,
                    containers=containers,
                    request_handler=request_handler,
                ) as (endpoint, state):
                    case = SimpleNamespace(
                        link=link,
                        watch=watch,
                        endpoint=endpoint,
                        state=state,
                        values=values,
                        generations=generations,
                        normal=normal,
                        candidate=candidate,
                        prepared=prepared,
                        observers=[],
                    )

                    def create():
                        observer = m.AppCustody(watch, endpoint)
                        case.observers.append(observer)
                        return observer

                    case.create = create
                    try:
                        yield case
                    finally:
                        for observer in case.observers:
                            observer.close()
            finally:
                watch.close()


@pytest.fixture
def case(prepared, tmp_path, monkeypatch):
    with setup(prepared, tmp_path, monkeypatch) as result:
        yield result


def exit_normal(case):
    case.normal.stdin.close()
    assert case.normal.wait(timeout=3) == 0


def count_fds(case):
    # The server is a fixture thread in this process. Its accepted socket must
    # finish closing before comparing owned descriptor counts; that transport
    # teardown is independent of the client's completed bounded response read.
    assert case.state.idle.wait(timeout=3)
    return len(os.listdir("/proc/self/fd"))


def capture_both(case):
    observer = case.create()
    exit_normal(case)
    candidate = observer.capture_candidate(case.generations[m.CANDIDATE])
    assert candidate is observer.poll().candidate
    return observer


@pytest.mark.parametrize("sender", [False, True])
def test_actual_original_generations_and_pidfds_survive_full_borrowed_cleanup(
    prepared, tmp_path, monkeypatch, sender
):
    with setup(prepared, tmp_path, monkeypatch, sender=sender) as case:
        observer = case.create()
        status = observer.poll()
        assert status.normal.generation == case.link.plan.normal_generation
        assert status.normal.process.pid == case.normal.pid
        assert not status.normal_exited and status.candidate is status.candidate_exited is None
        assert not status.capture_failed and len(case.state.requests) == 2
        exit_normal(case)
        original = observer.capture_candidate(case.generations[m.CANDIDATE])
        assert original.process.pid == case.candidate.pid
        assert len(case.state.requests) == 4
        case.endpoint.close()
        case.link.close()
        case.link.domain.close()
        case.link.observer.close()
        case.prepared.witness.close()
        case.prepared.child.stdin.close()
        assert case.prepared.child.wait(timeout=3) == 0
        signal.pidfd_send_signal(observer._retained[1][1], signal.SIGKILL)
        assert case.candidate.wait(timeout=3) == -signal.SIGKILL
        monkeypatch.setattr(m.processes, "read_identity", lambda *_: pytest.fail("PID reopened"))
        monkeypatch.setattr(
            m.engine, "_json_request", lambda *_a, **_k: pytest.fail("Engine polled")
        )
        monkeypatch.setattr(case.link, "read", lambda: pytest.fail("startup reused"))
        status = observer.poll()
        assert status.normal_exited and status.candidate_exited
        assert status.candidate is original
        assert status.deadline == m.deadlines.Status(True, False)
        assert observer.poll() == status and len(case.state.requests) == 4


def test_unconfirmed_endpoint_does_not_learn_a_peer_or_allow_second_capture(
    prepared, tmp_path, monkeypatch
):
    with setup(prepared, tmp_path, monkeypatch, authenticated=False) as case:
        denied(case.create)
        assert case.watch.app_custody_attempted and not case.endpoint.closed
        denied(case.create)
        assert not case.state.requests


def test_candidate_cannot_be_captured_while_original_normal_is_live(case):
    observer = case.create()
    denied(lambda: observer.capture_candidate(case.generations[m.CANDIDATE]))
    assert observer.candidate_attempted and observer.capture_failed and not observer.failed
    assert len(case.state.requests) == 2
    exit_normal(case)
    denied(lambda: observer.capture_candidate(case.generations[m.CANDIDATE]))
    status = observer.poll()
    assert status.normal_exited and status.candidate_exited is None and status.capture_failed


def test_helper_loss_refuses_new_capture_but_keeps_existing_app_witness(case):
    observer = case.create()
    exit_normal(case)
    case.prepared.child.stdin.close()
    assert case.prepared.child.wait(timeout=3) == 0
    denied(lambda: observer.capture_candidate(case.generations[m.CANDIDATE]))
    status = observer.poll()
    assert status.deadline.helper_exited and status.normal_exited and status.candidate is None
    assert status.capture_failed and len(case.state.requests) == 2


@pytest.mark.parametrize("field", ["Id", "Image", "Name", "Pid", "StartedAt", "Running"])
@pytest.mark.parametrize("at", [1, 2, 3, 4])
def test_wrong_or_changed_generation_refuses_without_adopting_new_process(case, field, at):
    def changed(value, count):
        if count == at:
            target = value["State"] if field in ("Pid", "StartedAt", "Running") else value
            target[field] = {
                "Id": "d" * 64,
                "Image": "sha256:" + "e" * 64,
                "Name": "/app_unrelated",
                "Pid": os.getpid(),
                "StartedAt": "2026-09-27T01:02:04.000Z",
                "Running": False,
            }[field]
        return value

    case.state.hook = changed
    before = count_fds(case)
    if at <= 2:
        denied(case.create)
        assert case.watch.app_custody_attempted
        assert count_fds(case) == before
    else:
        observer = case.create()
        exit_normal(case)
        before = count_fds(case)
        denied(lambda: observer.capture_candidate(case.generations[m.CANDIDATE]))
        assert observer.poll().normal_exited and observer.poll().candidate is None
        assert count_fds(case) == before
    assert len(case.state.requests) == at


def test_wrong_process_pidfd_cannot_be_hidden_by_correct_proc_text(case, monkeypatch):
    actual = m.processes.os.pidfd_open
    monkeypatch.setattr(
        m.processes.os,
        "pidfd_open",
        lambda pid, flags=0: actual(case.candidate.pid if pid == case.normal.pid else pid, flags),
    )
    denied(case.create)
    assert len(case.state.requests) == 1
    assert case.normal.poll() is case.candidate.poll() is None


@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize("stage", ["dup", "metadata", "pidinfo", "interrupt"])
def test_partial_acquisition_failure_releases_only_new_descriptors(
    case, monkeypatch, candidate, stage
):
    observer = case.create() if candidate else None
    if candidate:
        exit_normal(case)
    before = count_fds(case)
    original_dup, original_identity, original_info = (
        os.dup,
        m.deadlines._identity,
        m.deadlines._fdinfo,
    )
    new_fds = []

    def duplicate(fd):
        if stage == "dup":
            raise OSError("PRIVATE")
        result = original_dup(fd)
        new_fds.append(result)
        return result

    def identity(fd):
        if fd in new_fds and stage in ("metadata", "interrupt"):
            if stage == "interrupt":
                raise KeyboardInterrupt
            raise OSError("PRIVATE")
        return original_identity(fd)

    def info(fd):
        if fd in new_fds and stage == "pidinfo":
            raise OSError("PRIVATE")
        return original_info(fd)

    with monkeypatch.context() as faults:
        faults.setattr(os, "dup", duplicate)
        faults.setattr(m.deadlines, "_identity", identity)
        faults.setattr(m.deadlines, "_fdinfo", info)
        action = (
            (lambda: observer.capture_candidate(case.generations[m.CANDIDATE]))
            if candidate
            else case.create
        )
        if stage == "interrupt":
            with pytest.raises(KeyboardInterrupt):
                action()
        else:
            denied(action)
    assert count_fds(case) == before
    if candidate:
        status = observer.poll()
        assert status.normal_exited and status.candidate is None and status.capture_failed
    else:
        assert case.watch.app_custody_attempted
        denied(case.create)


def test_final_helper_loss_cannot_complete_candidate_capture(case):
    observer = case.create()
    exit_normal(case)

    def lost(value, count):
        if count == 4:
            case.prepared.child.stdin.close()
            assert case.prepared.child.wait(timeout=3) == 0
        return value

    case.state.hook = lost
    before = count_fds(case)
    denied(lambda: observer.capture_candidate(case.generations[m.CANDIDATE]))
    assert count_fds(case) == before - 1  # Owned helper stdin closed.
    status = observer.poll()
    assert status.deadline.helper_exited and status.normal_exited
    assert status.candidate is None and status.capture_failed


def test_no_pid_reconstruction_after_uncaptured_candidate_has_already_exited(case):
    observer = case.create()
    exit_normal(case)
    case.candidate.stdin.close()
    assert case.candidate.wait(timeout=3) == 0
    denied(lambda: observer.capture_candidate(case.generations[m.CANDIDATE]))
    status = observer.poll()
    assert status.candidate is status.candidate_exited is None
    assert status.capture_failed and status.normal_exited


def test_capture_generation_argument_cannot_pick_an_arbitrary_container(case):
    observer = case.create()
    exit_normal(case)
    denied(lambda: observer.capture_candidate("/containers/unrelated/json"))
    assert observer.poll().candidate is None and len(case.state.requests) == 2


def test_endpoint_peer_replacement_refuses_before_candidate_request(case):
    observer = case.create()
    exit_normal(case)
    case.endpoint.peer = (*case.endpoint.peer[:2], -1, -1)
    denied(lambda: observer.capture_candidate(case.generations[m.CANDIDATE]))
    assert observer.poll().normal_exited and len(case.state.requests) == 2


@pytest.mark.parametrize("name", m.NAMESPACES)
def test_changed_observer_namespace_refuses_instead_of_adopting_new_proc_view(
    case, monkeypatch, name
):
    observer = case.create()
    actual = os.stat

    def changed(path, *args, **kwargs):
        value = actual(path, *args, **kwargs)
        if path == f"/proc/self/ns/{name}":
            return SimpleNamespace(
                st_dev=value.st_dev, st_ino=value.st_ino + 1, st_mode=value.st_mode
            )
        return value

    monkeypatch.setattr(os, "stat", changed)
    denied(observer.poll)
    assert len(case.state.requests) == 2


@pytest.mark.parametrize("name", m.NAMESPACES)
def test_partial_namespace_capture_does_not_leak_or_close_borrowed_watch(case, monkeypatch, name):
    actual = os.open
    before = count_fds(case)

    def fail(path, *args, **kwargs):
        if path == f"/proc/self/ns/{name}":
            raise OSError("PRIVATE")
        return actual(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", fail)
    denied(case.create)
    assert count_fds(case) == before and not case.state.requests
    assert case.watch.poll() == m.deadlines.Status(False, False)


def test_candidate_cannot_reuse_original_normal_container_even_with_changed_name(case, monkeypatch):
    observer = case.create()
    exit_normal(case)
    case.values[m.CANDIDATE]["Id"] = case.values[m.NORMAL]["Id"]
    generation = m.platform.generation(
        case.values[m.CANDIDATE],
        name="app_" + m.CANDIDATE,
        image=case.link.plan.candidate.image,
    )
    actual = m.processes.read_identity

    def renamed(pid, cid):
        if pid == case.candidate.pid:
            identity = actual(pid, "c" * 64)
            return replace(identity, container_id=cid)
        return actual(pid, cid)

    monkeypatch.setattr(m.processes, "read_identity", renamed)
    denied(lambda: observer.capture_candidate(generation))
    assert len(case.state.requests) == 3 and observer.poll().candidate_exited is None


def test_frozen_original_normal_is_not_an_exited_app(case):
    observer = case.create()
    fd = observer._retained[0][1]
    signal.pidfd_send_signal(fd, signal.SIGSTOP)
    try:
        assert not observer.poll().normal_exited
        denied(lambda: observer.capture_candidate(case.generations[m.CANDIDATE]))
        assert observer.poll().candidate_exited is None
    finally:
        signal.pidfd_send_signal(fd, signal.SIGCONT)


def test_capture_budget_includes_final_binding_guards(case, monkeypatch):
    observer = case.create()
    exit_normal(case)
    actual = m.time.monotonic
    offset = [0]
    monkeypatch.setattr(m.time, "monotonic", lambda: actual() + offset[0])

    def slow(value, count):
        if count == 4:
            offset[0] = m.MAX_SECONDS + 1
        return value

    case.state.hook = slow
    denied(lambda: observer.capture_candidate(case.generations[m.CANDIDATE]))
    offset[0] = 0
    assert observer.poll().normal_exited and observer.poll().candidate is None


def test_process_exit_during_capture_never_becomes_successful_live_binding(case):
    def exit_during(value, count):
        if count == 2:
            exit_normal(case)
        return value

    case.state.hook = exit_during
    denied(case.create)
    assert case.normal.returncode == 0
    denied(case.create)
    assert len(case.state.requests) == 2


def test_second_candidate_capture_keeps_first_receipt_and_cannot_rebind(case):
    observer = capture_both(case)
    original = observer.poll().candidate
    denied(lambda: observer.capture_candidate("f" * 64))
    status = observer.poll()
    assert status.candidate is original and status.capture_failed
    assert not status.candidate_exited and len(case.state.requests) == 4


def test_poll_survives_engine_disappearance_without_asserting_app_exit(case):
    observer = case.create()
    case.endpoint.close()
    assert not observer.poll().normal_exited
    exit_normal(case)
    denied(lambda: observer.capture_candidate(case.generations[m.CANDIDATE]))
    assert observer.poll().normal_exited and observer.poll().candidate is None


def test_original_timer_keeps_expired_custody_observable_without_new_capture(
    prepared, tmp_path, monkeypatch
):
    with setup(prepared, tmp_path, monkeypatch, short=True) as case:
        observer = case.create()
        assert select.select([case.watch.timer_fd], [], [], 7)[0] == [case.watch.timer_fd]
        status = observer.poll()
        assert status.deadline.recovery_deadline_expired and not status.normal_exited
        exit_normal(case)
        denied(lambda: observer.capture_candidate(case.generations[m.CANDIDATE]))
        assert observer.poll().normal_exited
        assert observer.poll().candidate_exited is None and len(case.state.requests) == 2


def test_original_readiness_not_renewed_even_when_hard_deadline_is_live(case, monkeypatch):
    observer = case.create()
    exit_normal(case)
    original = case.watch.poll
    monkeypatch.setattr(case.watch, "poll", lambda: m.deadlines.Status(False, False))
    monkeypatch.setattr(
        m.time, "clock_gettime_ns", lambda _: int(case.link.plan.deadlines.ready_by * 1e9) + 1
    )
    denied(lambda: observer.capture_candidate(case.generations[m.CANDIDATE]))
    assert len(case.state.requests) == 2 and observer.capture_failed
    monkeypatch.setattr(case.watch, "poll", original)


@pytest.mark.parametrize("field", ["watch", "endpoint", "plan", "endpoint_pin", "_retained"])
def test_replaced_bindings_poison_poll_and_original_cleanup_only(case, field):
    observer = case.create()
    handles = [fd for _, fd, _ in observer._retained]
    setattr(observer, field, object())
    denied(observer.poll)
    observer.close()
    for fd in handles:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert not case.watch.closed and not case.endpoint.closed


def test_changed_frozen_receipt_is_not_accepted(case):
    observer = case.create()
    object.__setattr__(observer.poll().normal.process, "start_ticks", 1)
    denied(observer.poll)


def test_equal_receipt_replacement_is_not_original_custody(case):
    observer = case.create()
    binding, fd, identity = observer._retained[0]
    observer._retained = ((replace(binding), fd, identity),)
    denied(observer.poll)


def test_closed_or_substituted_pidfd_does_not_become_exit(case):
    observer = case.create()
    fd = observer._retained[0][1]
    other = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.dup2(other, fd, inheritable=False)
        denied(observer.poll)
        denied(observer.close)
        assert os.fstat(fd) == os.fstat(other)  # A foreign replacement was not closed.
    finally:
        os.close(fd)
        os.close(other)


def test_cross_thread_poll_is_not_independent_process_custody(case):
    observer = case.create()
    errors = []

    def wrong_owner():
        try:
            observer.poll()
        except m.UnconfirmedCustody as error:
            errors.append(error)

    thread = Thread(target=wrong_owner)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive() and len(errors) == 1
    assert observer.failed


def test_close_does_not_stop_children_or_close_borrowed_watch_or_endpoint(case):
    observer = case.create()
    original = observer.poll().normal
    observer.close()
    observer.close()
    assert case.normal.poll() is None and original.process.pid == case.normal.pid
    assert case.watch.poll() == m.deadlines.Status(False, False)
    case.endpoint.check()
    denied(observer.poll)
    denied(case.create)


def test_only_explicit_readonly_joint_inventory_names_custody_library():
    root = Path(m.__file__).parent
    for path in (*root.glob("*source*.py"), *root.glob("supplemental_recording*command.py")):
        text = path.read_text()
        if path.name == "supplemental_recording_service_host_source.py":
            assert f'"{NAME}"' in text
            imports = set()
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.add(node.module)
            assert imports == {"__future__", "supplemental_recording_app_host_source"}
        else:
            assert NAME not in text, path.name
