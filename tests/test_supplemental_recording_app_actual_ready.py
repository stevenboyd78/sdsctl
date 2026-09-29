"""Original App inputs and actual native returns through a local Engine fixture.

Native command/context/pipes, private Unix HTTP, original dispatch claims,
Ready parser, pidfds and App input/source checks are real. Engine replies,
container/cgroup/root-credential mapping and App/Startup/image are synthetic.
The optional recording transport is shared by the actual AppLaunch/Start test;
the readiness tests here always withdraw before begin with media read-only.
This is not full AppService/observer/recovery or installed qualification.
"""

import json
import os
import select
import signal
import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_fixed_operator as fixed
from . import test_supplemental_recording_app_ready_qualification as readiness

bwrap, staged, mapped = fixed.bwrap, fixed.staged, fixed.mapped
layout, image_umask, supervised = fixed.layout, fixed.image_umask, fixed.supervised
image, configured = fixed.image, fixed.configured
candidate, app, native, launch_case = fixed.candidate, fixed.app, fixed.native, fixed.launch_case
pytestmark = fixed.pytestmark
io, m = fixed.native_io, readiness.m
engine = io.engine


def map_actual_workers(state, ready, monkeypatch):
    namespace = engine.m.namespace
    domains = namespace.Witness._host_domains()
    namespaces = engine.namespaces.NS[:3] + domains
    init = state.pins.init
    state.actors[init.pid] = namespace.Actor(
        init.pid, 1, os.getpid(), init.start_ticks, init.container_id, namespaces
    )
    for role in ("guardian", "native", "watchdog"):
        report = ready[role]
        parent, ticks = io.child.c.returns._identity(report["pid"])
        assert ticks == report["start_ticks"]
        state.actors[report["pid"]] = namespace.Actor(
            report["pid"], report["pid"], parent, ticks, init.container_id, namespaces
        )

    def read(pid, cid):
        parent, ticks = io.child.c.returns._identity(pid)
        actor = state.actors[pid]
        namespace.require(
            cid == init.container_id and (parent, ticks) == (actor.parent, actor.start_ticks)
        )
        return actor

    monkeypatch.setattr(namespace, "read", read)


@contextmanager
def native_engine(
    s,
    mapped,
    staged,
    qualified,
    monkeypatch,
    *,
    controller=False,
    recording=False,
    fault=None,
    operator_capture=False,
):
    assert qualified is not None or controller
    receipt = None if qualified is None else qualified.launch_inputs
    with TemporaryDirectory(prefix="app-ready-engine-") as temporary:
        directory = Path(temporary) / "dispatch"
        directory.mkdir(mode=0o700)
        pins = (
            None
            if receipt is None
            else m.launch.engine.dispatch.Pins(
                m.launch.binding.Binding(
                    s.projected, s.plan.candidate_runtime.source, s.plan.sha256, s.plan.boot
                ),
                fixed.command_for(s, receipt),
                s.original.generation,
                s.original.init,
            )
        )
        state = SimpleNamespace(
            directory=directory,
            pins=pins,
            witness=s.witness,
            operator=None,
            worker=None,
            envelopes=[],
            actors={},
            errors=[],
            ready=None,
            run=None,
            begun=False,
            ledger=None,
        )

        def metadata(peer, request):
            value = engine.intents.metadata(state)
            value["ContainerID"] = state.pins.init.container_id
            if state.operator is not None:
                value.update(Running=True, Pid=state.envelopes[0]["guardian"]["pid"])
            peer.sendall(engine.reply(value))

        def created(peer, request):
            if state.pins is None:
                # The actual service publishes and acquires its sole original
                # AppLaunch inside its own running phase, not ahead of time.
                state.run = s.driver.native.run
                state.pins = state.run.pins
                assert state.run.prelaunch is s.driver.native.prelaunch
            assert request[2] == state.pins.command.create_body()
            peer.sendall(engine.reply({"Id": engine.attached.EXEC}, 201))

        def upgrade(peer, request):
            state.operator = fixed.start_operator(
                s, mapped, staged, state.pins.command, recording=recording
            )
            # This helper preserves native raw bytes and maps only synthetic
            # root credential metadata; kernel handles and ticks remain real.
            ready = io.wire_message(state)
            map_actual_workers(state, ready, monkeypatch)
            peer.sendall(
                engine.attached.stream.UPGRADE
                + engine.attached.stream.segment(engine.attached.stream.app(ready))
            )
            retained = peer.dup()

            def withdrawing():
                try:
                    retained.settimeout(12)
                    if recording:
                        begin = engine.attached.begun(retained)
                        assert begin["body"]["intent_sha256"] == state.ledger.state.sha256
                        assert s.journal.entries[-1]["event"]["kind"] == "authorize_recording"
                        state.begun = True
                        raw = io.operator.w.encode(begin)
                        state.operator.stdin.write(io.operator.w.HEADER.pack(len(raw)) + raw)
                        state.operator.stdin.flush()
                        for _ in range(3):
                            message = io.wire_message(state)
                            if fault != "lost_completed" or message["phase"] == "started":
                                retained.sendall(
                                    engine.attached.stream.segment(
                                        engine.attached.stream.app(message)
                                    )
                                )
                        assert select.select([state.operator.stdout], [], [], 6)[0]
                        assert state.operator.stdout.read(1) == b""
                        state.operator.wait(timeout=6)
                    else:
                        assert retained.recv(1) == b""  # Explicit withdrawal before begin.
                        state.operator.stdin.close()
                        state.operator.stdin = None
                        state.operator.wait(timeout=6)
                except BaseException as error:
                    state.errors.append(error)
                finally:
                    retained.close()

            state.worker = Thread(target=withdrawing)
            state.worker.start()

        handlers = [
            created,
            metadata,
            upgrade,
            metadata,
            metadata,
        ]
        state.handlers, state.metadata = handlers, metadata
        if operator_capture:
            handlers.append(metadata)

        def exited(peer, request):
            expected = 0 if state.begun else 70
            assert state.operator.wait(timeout=3) == expected
            assert request[0] == f"GET /v1.47/exec/{engine.attached.EXEC}/json HTTP/1.1"
            assert request[2] is None
            value = engine.intents.metadata(state)
            value.update(
                ContainerID=state.pins.init.container_id,
                Running=False,
                ExitCode=expected,
                Pid=state.envelopes[0]["guardian"]["pid"],
            )
            peer.sendall(engine.reply(value))

        state.exited_metadata = exited
        if recording:
            handlers.append(exited)
        with engine.engine(state, monkeypatch, handlers, make_client=not controller) as (
            client,
            requests,
        ):
            try:
                state.client, state.requests = client, requests
                try:
                    if controller:
                        state.endpoint = client
                    else:
                        client.create()
                        client.attach(finish_by=s.plan.lease["stop_by"])
                        state.ready = m.launch.received.Ready(
                            client,
                            profile_sha256=mapped.profile,
                            original_clock=s.plan.original_clock,
                        )
                    yield state
                finally:
                    # Keep the owning Engine fixture thread alive until its
                    # bwrap child has withdrawn/reaped (--die-with-parent).
                    if state.run is not None:
                        state.run.close()
                    elif state.ready is not None:
                        state.ready.close()
                    else:
                        client.close()
                    if state.worker is not None:
                        state.worker.join(8)
                        assert not state.worker.is_alive()
            finally:
                # Even when the relay failed before begin, keep the thread that
                # created bwrap alive through original stdin EOF and child reap.
                # --die-with-parent is tied to that creator thread, not merely
                # to this pytest process. Do not replace refusal with SIGKILL.
                if state.ready is not None:
                    state.ready.close()
                if state.worker is not None:
                    state.worker.join(8)
                    assert not state.worker.is_alive()
                if state.operator is not None:
                    if state.operator.stdin is not None:
                        state.operator.stdin.close()
                        state.operator.stdin = None
                    try:
                        out, err = state.operator.communicate(timeout=6)
                    except subprocess.TimeoutExpired:
                        assert os.getpgid(state.operator.pid) == state.operator.pid
                        os.killpg(state.operator.pid, signal.SIGKILL)
                        state.operator.communicate(timeout=3)
                        raise
                    assert state.operator.returncode == (0 if state.begun else 70) and not out
                    assert err == (b"" if state.begun else io.operator.MESSAGE)
                assert not state.errors


@pytest.mark.parametrize("fault", [None, "claim", "helper_mode"])
def test_actual_original_ready_joins_app_source_and_claim_without_begin(
    launch_case, mapped, staged, monkeypatch, fault
):
    s = launch_case
    original = s.plan.raw, s.plan.lease, s.native_baseline
    qualified = s.publish()
    with native_engine(s, mapped, staged, qualified, monkeypatch) as state:
        ready = state.ready
        assert json.loads(ready.context_raw)["launch"] == qualified.launch_inputs.sha256
        q = m.NativeReadyQualification(qualified, ready)
        assert q() is None and q.during(lambda: "observed") == "observed"
        assert q.ready is ready and ready.clock is s.plan.original_clock
        assert not ready.client.attachment.begun
        assert not tuple((s.case_root / "receipts").iterdir())
        assert original == (s.plan.raw, s.plan.lease, s.native_baseline)
        if fault == "claim":
            target = s.case_root / "launch/guardian/launch-claimed.json"
            value = json.loads(target.read_bytes())
            value["guardian_start_ticks"] += 1
            target.write_bytes(m.inputs.base.encode(value))
        elif fault == "helper_mode":
            (s.root / m.launch.plans.host.candidate_static.NATIVE).chmod(0o775)
        if fault is not None:
            fixed.preflight.launches.denied(q)
            assert q.failed and q.elapsed_seconds is None
        assert not mapped.scanner.reads
        ready.close()  # Explicit original transport withdrawal, not fake native exit.
    assert (mapped.recordings / "previous.wav").read_bytes() == b"old evidence unchanged"


def test_unexpected_withdrawal_before_begin_reaps_native_before_engine_owner_exits(
    launch_case, mapped, staged, monkeypatch
):
    s = launch_case
    original = s.plan.raw, s.plan.lease, s.native_baseline
    qualified = s.publish()
    with (
        pytest.raises(AssertionError),
        native_engine(s, mapped, staged, qualified, monkeypatch, recording=True) as state,
    ):
        assert state.ready.ready_raw and not state.begun
        assert not state.ready.client.attachment.begun
        # Unexpected original-host withdrawal while the fixture expects a
        # begin is still a failed test exchange. It must not kill the Engine
        # creator thread before its --die-with-parent child is retired.
        state.ready.close()
    assert not state.begun and [item["phase"] for item in state.envelopes] == ["ready"]
    assert state.operator.returncode == 70  # Clean native refusal, not SIGKILL.
    assert state.operator.stdin is None and not state.worker.is_alive()
    assert len(state.errors) == 1 and isinstance(state.errors[0], AssertionError)
    assert state.ready.closed and not s.witness.exited()
    assert original == (s.plan.raw, s.plan.lease, s.native_baseline)
    assert not mapped.scanner.reads
    assert list(mapped.recordings.iterdir()) == [mapped.recordings / "previous.wav"]
    assert (mapped.recordings / "previous.wav").read_bytes() == b"old evidence unchanged"
