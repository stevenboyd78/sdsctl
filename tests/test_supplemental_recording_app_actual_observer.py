"""Original actual-native service with separate authenticated read-only observer.

Synthetic App/CLI/root/cgroup/image/publication facts remain explicit. Separate
observer and writer own their real clocks, journal read/write descriptors and
pidfds; actual Link acknowledgments bracket original dispatch and native begin.
No installed supervision, runtime provenance or live scanner authority follows.
"""

import json
import os
import socket
import subprocess
import sys
from contextlib import contextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_actual_dispatch as dispatch
from . import test_supplemental_recording_service_cli_channel as channel

service, recovery = dispatch.service, dispatch.recovery
engine = service.actual.actual.engine
m, launch = channel.m, service.launch
bwrap, staged, mapped = service.bwrap, service.staged, service.mapped
layout, image_umask, supervised = service.layout, service.image_umask, service.supervised
image, configured = service.image, service.configured
candidate, app, native = service.candidate, service.app, service.native
driver_case, dispatched = dispatch.driver_case, dispatch.dispatched
pytestmark = [
    service.pytestmark,
    pytest.mark.skipif(
        not m.custody_module.deadlines.timerfd_available(),
        reason="Real observer needs Linux timerfd",
    ),
]


@pytest.fixture
def launch_case(native, monkeypatch, mapped):
    def observe(kind, notice):
        return native.observer.exchange(kind, notice)

    native.driver_dispatch_observer = lambda notice: observe("dispatch", notice)
    native.driver_candidate_observer = lambda notice: observe("candidate", notice)
    native.driver_native_observer = lambda notice: observe("native", notice)
    yield from dispatch.launch_case.__wrapped__(native, monkeypatch, mapped)


@contextmanager
def observer_server(s, io):
    """Owned read-only metadata server, separate from native transport endpoint."""
    with TemporaryDirectory(prefix="app-observer-") as temporary:
        path = Path(temporary) / "engine.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        path.chmod(0o600)
        listener.listen(4)
        listener.settimeout(0.05)
        stop, requests, errors = Event(), [], []

        def serve():
            while not stop.is_set():
                try:
                    peer, _ = listener.accept()
                except TimeoutError:
                    continue
                try:
                    peer.settimeout(3)
                    request = engine.read_request(peer)
                    if request is None:
                        continue
                    requests.append(request)
                    assert len(requests) <= 64 and request[2] is None
                    method, route, protocol = request[0].split()
                    assert method == "GET" and protocol == "HTTP/1.1"
                    if route.startswith("/v1.47/containers/") and route.endswith("/json"):
                        name = route.removeprefix("/v1.47/containers/").removesuffix("/json")
                        assert name in (
                            "app_" + service.m.base.NORMAL,
                            "app_" + service.m.base.CANDIDATE,
                            service.driver.platform.h.CLI,
                        )
                        value = s.docker.container(name)
                    elif route.startswith("/v1.47/exec/") and route.endswith("/json"):
                        eid = route.removeprefix("/v1.47/exec/").removesuffix("/json")
                        if eid == engine.attached.EXEC:
                            assert io.operator is not None and io.operator.poll() is None
                            value = engine.intents.metadata(io)
                            value.update(
                                ContainerID=io.pins.init.container_id,
                                Running=True,
                                Pid=io.envelopes[0]["guardian"]["pid"],
                            )
                        else:
                            value = s.docker.inspect_execution(eid)
                            if value["Running"] is False and value["ExitCode"] is not None:
                                value["Pid"] = 0  # Explicit terminal CLI fixture metadata.
                    else:
                        raise AssertionError("Unexpected observer route")
                    peer.sendall(engine.reply(value))
                except BaseException as error:
                    errors.append(error)
                finally:
                    peer.close()

        worker = Thread(target=serve)
        worker.start()
        try:
            yield path, requests
        finally:
            stop.set()
            worker.join(timeout=4)
            listener.close()
            assert not worker.is_alive() and not errors, errors


@contextmanager
def separate_observer(s, io, monkeypatch, *, fault=None):
    with observer_server(s, io) as (path, requests):
        left, right = m.pair()
        child = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).with_name("_app_service_observer_fixture.py")),
                str(right.incoming.fileno()),
                str(right.outgoing.fileno()),
            ],
            pass_fds=(right.incoming.fileno(), right.outgoing.fileno()),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        right.close()
        witness = link = None
        exchanges = []
        retired = []
        try:
            current = m.processes.read_identity

            def identity(pid, cid):
                if (pid, cid) == (child.pid, "7" * 64):
                    return channel.fixture_identity(pid, cid)
                return current(pid, cid)

            monkeypatch.setattr(m.processes, "read_identity", identity)
            monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
            witness = m.processes.ProcessWitness(identity(child.pid, "7" * 64))
            helper = channel.fixture_identity(os.getpid(), "9" * 64)
            t = s.transfer_io
            channel.send(
                child,
                dict(
                    fault=fault,
                    plan=json.loads(s.plan.raw),
                    root=str(s.plan.root),
                    socket=str(path),
                    helper=asdict(helper),
                    normal=t.normal["Id"],
                    cli=t.cli["Id"],
                    candidate_pid=s.child.pid,
                    candidate_cid=s.witness.identity.container_id,
                    identities={
                        os.getpid(): helper.container_id,
                        t.normal["State"]["Pid"]: t.normal["Id"],
                        s.child.pid: s.witness.identity.container_id,
                    },
                    native_media=str(launch.binding.projection.NATIVE_MEDIA),
                    host_manifest=launch.binding.projection._validated(s.projected.host).hex(),
                    native_manifest=s.projected.native_manifest.hex(),
                    container_namespaces=engine.namespaces.NS[:3],
                ),
            )
            ready = json.loads(channel.line(child))
            assert ready == dict(ready=True, owner=child.pid, events=1)
            link = m.Link(left, s.plan, s.startup.clock, witness, role="writer")

            def exchange(kind, notice):
                fn = dict(
                    dispatch=link.observe,
                    candidate=link.observe_candidate,
                    native=link.observe_native,
                )[kind]
                if retired:
                    assert kind == "dispatch" and child.returncode == 0 and witness.exited()
                    # The original production Link, not the fixture control
                    # pipe, must refuse its dead original peer before mutation.
                    return fn(notice)
                channel.send(child, dict(operation=kind))
                try:
                    receipt = fn(notice)
                except m.UnconfirmedExchange:
                    assert fault in ("lost_native_ack", "exit_native_ack") and kind == "native"
                    answer = json.loads(channel.line(child))
                    if fault == "exit_native_ack":
                        assert answer == dict(observer_exit_after_native_capture=True)
                        assert child.wait(timeout=3) == 73 and witness.exited()
                    else:
                        assert answer == dict(receipt=notice.receipt, sequence=link.sequence)
                    exchanges.append(kind)
                    raise
                answer = json.loads(channel.line(child))
                assert answer == dict(receipt=receipt, sequence=link.sequence)
                exchanges.append(kind)
                return receipt

            def snapshot():
                assert not retired
                channel.send(child, dict(operation="verify"))
                return json.loads(channel.line(child))

            def retire():
                assert not retired and link.sequence == 4
                assert exchanges == ["dispatch"] * 4 + ["candidate", "native"]
                channel.send(child, dict(operation="close"))
                assert json.loads(channel.line(child)) == dict(closed=True)
                assert child.wait(timeout=3) == 0 and witness.exited()
                retired.append(True)

            def verify(*, fault=None):
                if fault == "observer_exit":
                    assert retired == [True] and child.returncode == 0
                    assert witness.exited() and link.failed and link.sequence == 4
                    assert exchanges == ["dispatch"] * 4 + ["candidate", "native"]
                    return  # No fresh custody facts can be read from a dead observer.
                result = snapshot()
                assert result["owner"] == child.pid and not result["failed"]
                assert result["sequence"] == link.sequence == (6 if fault is None else 4)
                assert result["candidate"] and result["native"]
                assert result["normal_exited"]
                assert result["candidate_exited"] is (fault != "live_init")
                assert result["workers"] == (
                    ["guardian", "native", "watchdog"]
                    if fault == "live_init"
                    else ["guardian", "init", "native", "watchdog"]
                )
                assert not result["helper_exited"]
                assert len(result["executions"]) == (3 if fault is None else 2)
                assert all(
                    item["state"] == "not_running" and item["exit_code"] == 0
                    for item in result["executions"]
                )
                assert exchanges == (
                    ["dispatch"] * 4
                    + ["candidate", "native"]
                    + (["dispatch"] * 2 if fault is None else [])
                )
                assert requests and all(item[0].startswith("GET ") for item in requests)

            s.observer = SimpleNamespace(
                exchange=exchange,
                verify=verify,
                snapshot=snapshot,
                link=link,
                exchanges=exchanges,
                retire=retire,
            )
            yield s.observer
        finally:
            if link is not None:
                link.close()
            if child.poll() is None:
                with suppress(BrokenPipeError):
                    channel.send(child, dict(operation="close"))
            child.stdin.close()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # This handle belongs only to the disposable child created
                # above. Never search for or signal any external process.
                child.kill()
                child.wait(timeout=3)
                raise
            output, error = child.stdout.read(), child.stderr.read()
            child.stdout.close()
            child.stderr.close()
            left.close()
            if witness is not None:
                witness.close()
            assert child.returncode == (73 if fault == "exit_native_ack" else 0), error.decode()
            assert output == (
                b"" if fault == "exit_native_ack" or retired else b'{"closed":true}\n'
            )
            assert not error


@pytest.mark.parametrize("fault", [None, "running_metadata", "new_file"])
def test_original_service_actual_native_and_separate_observer_share_one_lifetime(
    dispatched, mapped, staged, monkeypatch, fault
):
    recovery.run_recovery(
        dispatched,
        mapped,
        staged,
        monkeypatch,
        initial=True,
        fault=fault,
        observer_factory=separate_observer,
    )


@pytest.mark.parametrize("route", ["record", "cancel"])
@pytest.mark.parametrize("fault", ["lost_native_ack", "exit_native_ack"])
def test_lost_separate_native_ack_preserves_facts_but_never_authorizes_recording(
    dispatched, mapped, staged, monkeypatch, route, fault
):
    s = dispatched
    from . import test_supplemental_recording_app_driver_dispatch as initial

    with service.actual.actual.native_engine(
        s, mapped, staged, None, monkeypatch, controller=True, operator_capture=True
    ) as io:
        audit = launch.engine.Endpoint()
        s.endpoint = io.endpoint
        waits, attempts, errors = [], [], []

        def advance(seconds):
            assert seconds == 0.25 and len(waits) < 5
            phase = s.journal.machine.state.phase
            waits.append(phase)
            if phase != "candidate_idle":
                return
            attempts.append(True)
            assert len(attempts) == 1
            s.witness = s.session.processes.witnesses[service.m.base.CANDIDATE]
            service.start_native(s, mapped, io, audit)
            if route == "record":
                assert not s.driver.start_recording()
                assert not s.driver.recording.recovery_attempted
            else:
                assert not s.driver.cancel_native()
                assert s.driver.native.cancel_attempted and s.driver.native.uncertain
                assert s.driver.recording is s.run.begin_owner is None
                assert not s.run.begin_attempted
            assert s.observer.link.failed
            if fault == "lost_native_ack":
                facts = s.observer.snapshot()
                assert facts["sequence"] == 4 and facts["candidate"] and facts["native"]
                assert not facts["failed"] and facts["normal_exited"]
                assert not facts["candidate_exited"] and not facts["helper_exited"]
                assert len(facts["executions"]) == 2
            else:
                # Dead observer handles cannot be replaced or called factual
                # recovery evidence. Only the original writer's handles remain.
                assert s.observer.link._peer.exited()
            assert s.observer.exchanges == ["dispatch"] * 4 + ["candidate", "native"]
            assert s.ledger.state.count == 1 and s.ledger.state.expected is None
            assert s.journal.machine.state.authorization_generation is None
            assert s.journal.machine.state.recording_outcome == "not_attempted"
            assert not io.run.client.attachment.begun and len(io.requests) == 6
            assert not tuple((s.case_root / "receipts").iterdir())
            assert not mapped.scanner.reads
            service.driver.expire(s, monkeypatch, hard=True)

        def wait(seconds):
            try:
                return advance(seconds)
            except BaseException as error:
                errors.append(error)
                raise

        try:
            with separate_observer(s, io, monkeypatch, fault=fault):
                initial.request(s)
                with pytest.raises(service.m.operator.UnconfirmedOperator):
                    s.driver.run(wait)
                assert not errors, errors
                assert len(attempts) == 1 and s.service.failed and s.service.closed
                assert s.transfer_io.started == ["1" * 64, "2" * 64]
                assert not s.driver.native.recovery_attempted
                assert not s.startup.clock.closed
                assert (
                    mapped.recordings / "previous.wav"
                ).read_bytes() == b"old evidence unchanged"
                service.driver.assert_owners(s)
        finally:
            audit.close()


@pytest.mark.parametrize("fault", [None, "live_init", "new_file"])
def test_separate_observer_retains_original_abandoned_recording_lifetime(
    dispatched, mapped, staged, monkeypatch, fault
):
    from . import test_supplemental_recording_app_actual_pristine as closed

    closed.run_closed(
        dispatched,
        mapped,
        staged,
        monkeypatch,
        fault=fault,
        preserved=True,
        observer_factory=separate_observer,
    )


@pytest.mark.parametrize("fault", [None, "live_init", "new_file"])
def test_separate_observer_retains_original_pristine_cancellation_lifetime(
    dispatched, mapped, staged, monkeypatch, fault
):
    from . import test_supplemental_recording_app_actual_pristine as closed

    closed.run_closed(
        dispatched,
        mapped,
        staged,
        monkeypatch,
        fault=fault,
        observer_factory=separate_observer,
    )


@pytest.mark.parametrize("route", ["finalized", "abandoned", "pristine"])
def test_observer_exit_after_native_work_prevents_every_restoration_route(
    dispatched, mapped, staged, monkeypatch, route
):
    from . import test_supplemental_recording_app_actual_pristine as closed

    @contextmanager
    def retiring_observer(s, io, patch):
        with separate_observer(s, io, patch) as observer:
            if route == "finalized":
                original = service.record_and_finalize

                def finished(*args, **kwargs):
                    result = original(*args, **kwargs)
                    observer.retire()
                    return result

                patch.setattr(service, "record_and_finalize", finished)
            else:
                name = "abandon_recording" if route == "abandoned" else "cancel_native"
                original = getattr(s.driver, name)

                def finished():
                    result = original()
                    assert result is True
                    observer.retire()
                    return result

                patch.setattr(s.driver, name, finished)
            yield observer

    if route == "finalized":
        recovery.run_recovery(
            dispatched,
            mapped,
            staged,
            monkeypatch,
            initial=True,
            fault="observer_exit",
            observer_factory=retiring_observer,
        )
    else:
        closed.run_closed(
            dispatched,
            mapped,
            staged,
            monkeypatch,
            fault="observer_exit",
            preserved=route == "abandoned",
            observer_factory=retiring_observer,
        )
