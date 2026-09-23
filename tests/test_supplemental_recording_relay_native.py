"""Actual isolated native returns -> host ledger/files/exit, synthetic Engine.

This is NOT Docker/image/root-namespace qualification. The local Engine fixture
maps its owned ordinary processes to synthetic container/root metadata, and the
declared media aliases to the same private test directory. Native launch/source
checks, private credentials, recorder, PCM, return bytes and process exits are
real. No scanner, installed App or privileged command is used.
"""

import hashlib
import json
import os
import select
import signal
import subprocess
import sys
import time
import tomllib
import wave
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace

import pytest

from sds200.network_audio import NetworkAudioTransport

from . import test_supplemental_recording_exit as exits
from . import test_supplemental_recording_operator as operator

tree, configured, cached, prepared, staged = (
    operator.tree,
    operator.configured,
    operator.cached,
    operator.prepared,
    operator.staged,
)
m, engine = exits.m, exits.engine_tests
returned, ready_module = m.returned, m.returned.begin.received
p, child = operator.p, operator.child


@contextmanager
def peers(monkeypatch):
    # Preserve cleanup even when preparation fails before either thread starts.
    with monkeypatch.context() as patch:
        patch.setattr(Thread, "start", lambda self: None)
        scanner = child.construction.LoopbackScanner()
    rtsp = None
    try:
        rtsp = child.RtspPeer()
        yield scanner, rtsp
    finally:
        if scanner.thread.ident is None:
            scanner.socket.close()
        else:
            scanner.close()
        if rtsp is not None:
            if rtsp.thread.ident is None:
                rtsp.listener.close()
                rtsp.packets.close()
            else:
                rtsp.close()


def close_init(process):
    process.stdin.close()
    process.wait(timeout=3)


@contextmanager
def setup(staged, prepared, tmp_path, monkeypatch):
    monkeypatch.setattr(operator.probing.m.cached, "CachedClient", operator.REAL_CACHED_CLIENT)
    with (
        peers(monkeypatch) as (scanner, rtsp),
        TemporaryDirectory(prefix="native-relay-") as temporary,
        ExitStack() as cleanup,
    ):
        base = Path(temporary)
        sockets, receipts, baseline = (base / name for name in ("sockets", "receipts", "baseline"))
        dispatch, ledger_path = tmp_path / "dispatch", tmp_path / "host-ledger"
        for path in (
            sockets,
            receipts,
            baseline,
            dispatch,
            ledger_path,
            prepared.path.parent / "guardian",
        ):
            path.mkdir(mode=0o700)
        spec = child.plans.m.construction.Specification(
            "127.0.0.1",
            scanner.socket.getsockname()[1],
            rtsp.port,
            "127.0.0.1",
            0,
            sockets,
            receipts,
            "Version 1.26.01",
            1,
            2,
            10,
        )
        deployment = Path(prepared.value["profile"]["deployment"])
        config = Path(tomllib.loads(deployment.read_text())["profile_config"])
        target = f"udp://127.0.0.1:{spec.control_port}"
        config.write_text(config.read_text().replace(prepared.config.scanner_target, target))
        mode = base / "writer-mode"
        mode.touch(mode=0o666)
        writer = p.monitor.Writer(os.geteuid(), os.getegid(), mode.stat().st_mode & 0o777)
        endpoint = NetworkAudioTransport("127.0.0.1", rtsp_port=rtsp.port).endpoint
        native = p.save_baseline(
            baseline, prepared.tree.baseline, writer, hashlib.sha256(endpoint.encode()).hexdigest()
        )
        projection_module = returned.host.projection
        # Explicit synthetic mount mapping. The native process uses its actual
        # private root; only host collection routes the sealed host alias.
        monkeypatch.setattr(projection_module, "NATIVE_MEDIA", native.baseline.root.parent)
        alias = projection_module.HOST_MEDIA / native.baseline.root.name
        data = Path("/mnt/data/supervisor/apps/data") / projection_module.CANDIDATE
        layout = projection_module.fixed.ProtectedLayout(
            projection_module.CANDIDATE,
            Path("/mnt/data/supervisor/apps/local/sds200_supplemental_acceptance"),
            data,
            projection_module.HOST_MEDIA,
            alias,
            *(
                data / name
                for name in ("display.toml", "configuration.toml", "accepted.json", "profile.cfg")
            ),
            "1" * 64,
        )
        host = p._decode(
            p.manifest_bytes(
                replace(native.baseline, root=alias), writer, native.contract.audio_endpoint_sha256
            )
        )
        projection = projection_module.Projection(layout, host, native)

        def routed(function):
            def run(root, *args, **kwargs):
                return function(native.baseline.root if root == alias else root, *args, **kwargs)

            return run

        for module, name in (
            (p.evidence, "opened_root"),
            (p.evidence, "inventory"),
            (p.monitor, "opened_root"),
        ):
            monkeypatch.setattr(module, name, routed(getattr(module, name)))
        prepared.value["specification"] = asdict(spec) | {
            "sockets": str(sockets),
            "receipts": str(receipts),
        }
        prepared.value["baseline"] = dict(
            directory=str(baseline), contract=asdict(native.contract), sha256=native.manifest_sha256
        )
        prepared.value["profile"]["sha256"] = child.plans.m.cached.profile_files(
            deployment, native.baseline.root
        )[0]
        prepared.value["projection_sha256"] = projection.sha256
        operator.guard.prepare_source_pin(staged, prepared)
        original_clock = ready_module.clock.read()
        host_binding = returned.host.Binding(
            projection, staged.pin, prepared.value["host_plan_sha256"], original_clock.boot
        )
        init = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", "import sys;sys.stdin.read()"], stdin=subprocess.PIPE
        )
        cleanup.callback(close_init, init)
        process_module = engine.intents.m.process
        ticks = int(Path(f"/proc/{init.pid}/stat").read_text().rpartition(") ")[2].split()[19])
        identity = process_module.ProcessIdentity(
            init.pid, ticks, engine.intents.execution.CONTAINER
        )
        monkeypatch.setattr(process_module, "read_identity", lambda *_: identity)
        witness = process_module.ProcessWitness(identity)
        cleanup.callback(witness.close)
        ready_by = time.monotonic() + 8
        command = engine.intents.m.execution.Command(
            "/data/native-relay/launch.json",
            hashlib.sha256(p.encode(prepared.value)).hexdigest(),
            staged.pin,
            ready_by,
        )
        pins = engine.intents.m.Pins(host_binding, command, prepared.value["generation"], identity)
        state = SimpleNamespace(
            pins=pins,
            witness=witness,
            directory=dispatch,
            process=init,
            ledger=returned.host.Ledger(ledger_path, host_binding, now=time.monotonic()),
            original_clock=original_clock,
            spec=spec,
            stored=native,
            rtsp=rtsp,
            profile=prepared.value["profile"]["sha256"],
            envelopes=[],
            errors=[],
            actors={},
            operator=None,
            ready=None,
            relay=None,
            worker=None,
        )
        scanner.thread.start()
        rtsp.thread.start()
        try:
            yield state
        finally:
            if state.ready is not None:
                state.ready.close()
            if state.operator is not None:
                process = state.operator
                if process.stdin is not None:
                    process.stdin.close()
                    process.stdin = None
                try:
                    process.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    process.send_signal(signal.SIGINT)
                    process.wait(timeout=5)
            if state.worker is not None:
                state.worker.join(6)
                assert not state.worker.is_alive()
            if state.operator is not None:
                # The relay thread is the sole stdout reader until it exits.
                # Do not race communicate() with its framed reads during cleanup.
                state.operator.communicate(timeout=1)
            assert not scanner.reads and not scanner.errors and not state.errors, state.errors


def wire_message(state):
    source = state.operator.stdout.fileno()

    def exact(size):
        value, end = bytearray(), time.monotonic() + 6
        while len(value) < size:
            assert select.select([source], [], [], max(0, end - time.monotonic()))[0]
            raw = os.read(source, size - len(value))
            assert raw
            value.extend(raw)
        return bytes(value)

    size = operator.w.HEADER.unpack(exact(4))[0]
    assert 0 < size <= operator.w.MAX_BYTES
    actual = json.loads(exact(size))
    state.envelopes.append(deepcopy(actual))
    # Only synthetic root metadata changes; native control/recording raw bytes,
    # times, context, PIDs/ticks and the returned file evidence remain untouched.
    mapped = deepcopy(actual)
    for role in ("guardian", "native", "watchdog"):
        assert (actual[role]["uid"], actual[role]["gid"]) == (os.geteuid(), os.getegid())
        mapped[role].update(uid=0, gid=0)
    if "received" in mapped["body"]:
        mapped["body"]["received"].update(uid=0, gid=0)
    return mapped


def install_actors(state, ready, monkeypatch):
    namespace = engine.m.namespace
    # Real stable user/time namespaces and actual proc ticks/parents; the first
    # three namespace identifiers and container membership are synthetic.
    domains = namespace.Witness._host_domains()
    namespaces = engine.namespaces.NS[:3] + domains
    init = state.pins.init
    state.actors[init.pid] = namespace.Actor(
        init.pid, 1, os.getpid(), init.start_ticks, init.container_id, namespaces
    )
    for role in ("guardian", "native", "watchdog"):
        report = ready[role]
        state.actors[report["pid"]] = namespace.Actor(
            report["pid"],
            report["pid"],
            os.getpid() if role == "guardian" else ready["guardian"]["pid"],
            report["start_ticks"],
            init.container_id,
            namespaces,
        )

    def read(pid, cid):
        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()
        actor = state.actors[pid]
        namespace.require(cid == init.container_id and fields[0] in ("R", "S", "I"))
        namespace.require(int(fields[1]) == actor.parent and int(fields[19]) == actor.start_ticks)
        return actor

    monkeypatch.setattr(namespace, "read", read)


def handlers(state, staged, prepared, monkeypatch, fault):
    def metadata(*, exited=False):
        value = engine.intents.metadata(state)
        if state.operator is not None:
            value.update(Running=True, Pid=state.operator.pid)
        if exited:
            assert state.operator.poll() == 0
            value.update(Running=False, ExitCode=70 if fault == "engine_exit" else 0)
        return value

    def upgrade(peer, _request):
        state.operator = operator.start(
            staged,
            prepared,
            change=lambda args: child.changed(args, "ready-by", str(state.pins.command.ready_by)),
        )
        ready = wire_message(state)
        install_actors(state, ready, monkeypatch)
        peer.sendall(
            engine.attached.stream.UPGRADE
            + engine.attached.stream.segment(engine.attached.stream.app(ready))
        )
        retained = peer.dup()

        def forward():
            try:
                begin = engine.attached.begun(retained)
                assert begin["body"]["intent_sha256"] == state.ledger.state.sha256
                raw = operator.w.encode(begin)
                wire = operator.w.HEADER.pack(len(raw)) + raw
                state.operator.stdin.write(wire)
                state.operator.stdin.flush()
                for _ in range(3):
                    message = wire_message(state)
                    discard = (fault == "lost_completed" and message["phase"] != "started") or (
                        fault == "lost_exited" and message["phase"] == "exited"
                    )
                    if not discard:
                        retained.sendall(
                            engine.attached.stream.segment(engine.attached.stream.app(message))
                        )
                assert select.select([state.operator.stdout], [], [], 5)[0]
                assert state.operator.stdout.read(1) == b""
            except BaseException as error:
                state.errors.append(error)
            finally:
                retained.close()

        state.worker = Thread(target=forward)
        state.worker.start()

    return [
        engine.reply({"Id": engine.attached.EXEC}, 201),
        lambda peer, _: peer.sendall(engine.reply(metadata())),
        upgrade,
        lambda peer, _: peer.sendall(engine.reply(metadata())),
        lambda peer, _: peer.sendall(engine.reply(metadata())),
        lambda peer, _: peer.sendall(engine.reply(metadata(exited=True))),
    ]


@pytest.mark.parametrize("fault", [None, "lost_completed", "lost_exited", "engine_exit"])
def test_actual_native_recording_returns_join_host_evidence_and_separate_exit(
    staged, prepared, tmp_path, monkeypatch, fault
):
    with (
        setup(staged, prepared, tmp_path, monkeypatch) as state,
        engine.engine(
            state, monkeypatch, handlers(state, staged, prepared, monkeypatch, fault)
        ) as (client, requests),
    ):
        client.create()
        client.attach(
            finish_by=state.pins.command.ready_by
            + state.stored.contract.maximum_recording_seconds
            + 3
        )
        state.ready = ready_module.Ready(
            client, profile_sha256=state.profile, original_clock=state.original_clock
        )
        now = time.monotonic()
        state.ledger.start_intent(
            now=now,
            generation=state.pins.generation,
            authorization_sha256="1" * 64,
            start_by=now + 8,
            finish_by=now + 20,
        )
        state.relay = returned.Relay(state.ledger, state.ready)
        state.relay.started()
        assert state.ledger.state.expected is not None and not state.ledger.state.closed
        for index in range(8):
            state.rtsp.packets.sendto(
                child.construction.make_rtp(
                    bytes(range(160)), sequence=100 + index, timestamp=1000 + index * 160
                ),
                state.rtsp.target,
            )
        if fault == "lost_completed":
            with pytest.raises(returned.UnconfirmedRelay):
                state.relay.completed()
            assert not state.ledger.state.closed and state.ledger.state.acknowledgment is None
            assert len(requests) == 5
        else:
            result = state.relay.completed()
            assert result.collected.artifact.samples == 1280
            # Receipt identity/timing are part of its checksum, not only the
            # raw message. This fixture maps only namespace credentials above.
            expected_receipt = state.envelopes[2]["body"]["received"] | {"uid": 0, "gid": 0}
            assert result.native_return_sha256 == returned.host.checksum(expected_receipt)
            stopped = json.loads(expected_receipt["raw"])["body"]["stopped"]
            assert result.stopped_raw == returned.host.encode(stopped)
            assert state.relay.completion is result
            if fault in ("lost_exited", "engine_exit"):
                with pytest.raises(m.UnconfirmedExit):
                    m.collect(state.relay)
                assert state.relay.phase == "unconfirmed"
                assert state.ledger.state.closed and state.ledger.state.acknowledgment is not None
            else:
                exited = m.collect(state.relay)
                assert exited.guardian_pid == state.operator.pid and state.relay.phase == "exited"
            assert len(requests) == (5 if fault == "lost_exited" else 6)
            assert not state.ready.processes.exited("init")
        state.operator.wait(timeout=5)
        state.worker.join(5)
        assert state.operator.returncode == 0 and not state.worker.is_alive()
        assert [item["phase"] for item in state.envelopes] == [
            "ready",
            "started",
            "completed",
            "exited",
        ]
        # Good files and actual native exit do not repair a missing host
        # completion or an independently failed final Engine inspection.
        recordings = list(state.stored.baseline.root.glob("sdsctl-acceptance-*.wav"))
        assert len(recordings) == 1
        with wave.open(str(recordings[0]), "rb") as saved:
            assert saved.getnframes() == 1280
        assert (
            state.stored.baseline.root / "older/old.wav"
        ).read_bytes() == b"old evidence unchanged"
