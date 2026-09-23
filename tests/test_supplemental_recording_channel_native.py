"""Exact child + native construction + localhost RTP; no physical scanner."""

import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from . import test_supplemental_recording_channel as channel
from . import test_supplemental_recording_construction as construction

m = channel.m
tree, configured, prepared = construction.tree, construction.configured, construction.prepared


def native_run(prepared, binding, send, fault):
    # Only this child owns the synthetic scanner and all native runtime objects.
    # Its two reporting call sites are the actual post-start and post-cleanup
    # success returns, not polling or reading native receipt files.
    scanner = construction.LoopbackScanner()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as packets:
            packets.bind(("127.0.0.1", 0))

            class Rtsp(construction.FakeRtspClient):
                def start(self, client_port):
                    self.started_ports.append(client_port)
                    return construction.RtpTransportInfo(
                        source="127.0.0.1", server_port=packets.getsockname()[1], ssrc=5678
                    )

            rtsp = Rtsp()
            spec = replace(prepared.spec, control_port=scanner.socket.getsockname()[1])
            config = replace(prepared.config, scanner_target=f"udp://127.0.0.1:{spec.control_port}")
            done = Event()
            with construction.build(prepared, specification=spec, configuration=config) as trial:
                trial.runtime.audio.stream.transport._rtsp_client_factory = lambda *_: rtsp
                if fault == "lost_started_return":
                    original = m.owner.FiniteRecordingOwner._publish

                    def lost(owner, name, now, snapshot=None):
                        original(owner, name, now, snapshot)
                        if name == "started":
                            raise TimeoutError(
                                "Native receipt exists, but successful return was lost"
                            )

                    m.owner.FiniteRecordingOwner._publish = lost
                if fault == "cleanup_failure":
                    original = trial.process.run

                    def failed():
                        original()
                        raise RuntimeError("Native run must not claim successful cleanup")

                    trial.process.run = failed

                def operator():
                    try:
                        construction.wait_for(lambda: trial.ready)
                        assert not trial.manager.snapshot().active
                        reporter = m.Sender(send, binding)
                        trial.request_start(returns=reporter)
                        if fault == "lost_started_return":
                            done.wait(6)
                            return
                        construction.wait_for(lambda: trial.acquisition.status().armed)
                        for i in range(8):
                            packets.sendto(
                                construction.make_rtp(
                                    bytes(range(160)), sequence=100 + i, timestamp=1000 + i * 160
                                ),
                                ("127.0.0.1", rtsp.started_ports[0]),
                            )
                        construction.wait_for(lambda: trial.manager.snapshot().samples == 1280)
                        done.wait(6)
                    except BaseException:
                        trial.cancel()
                        raise

                with ThreadPoolExecutor(max_workers=1) as pool:
                    task = pool.submit(operator)
                    try:
                        result = trial.run()
                        assert fault is None and result.artifact.samples == 1280
                    except construction.assembly.n.UnconfirmedAssembly:
                        assert fault is not None
                    finally:
                        done.set()
                    task.result(timeout=3)
                assert trial.cleanup_complete and not trial.manager.snapshot().active
            assert len(rtsp.started_ports) == rtsp.teardowns == 1
            assert not scanner.reads and not scanner.errors
    finally:
        scanner.close()


@pytest.mark.parametrize("fault", [None, "lost_started_return", "cleanup_failure"])
def test_exact_child_reports_only_actual_native_returns(prepared, fault):
    now = time.monotonic()
    binding = m.Binding(prepared.stored, prepared.generation, "1" * 64, "2" * 64, now + 8, now + 20)
    with channel.child(
        binding, lambda send: native_run(prepared, binding, send, fault)
    ) as receiver:
        if fault == "lost_started_return":
            channel.refused(receiver.receive)
        else:
            first = receiver.receive()
            assert (
                json.loads(first.raw)["body"]["expected"]["case"] == prepared.stored.baseline.case
            )
            if fault == "cleanup_failure":
                channel.refused(receiver.receive)
            else:
                final = receiver.receive()
                body = json.loads(final.raw)["body"]
                assert body["artifact"]["samples"] == 1280
                assert body["stopped"]["samples"] == 1280
                assert body["stopped"]["active"] is False
    assert receiver.fixture_returncode == 0
    assert (prepared.tree.root / "older/old.wav").read_bytes() == b"old evidence unchanged"
    if fault == "lost_started_return":
        assert (prepared.spec.receipts / "started.json").exists()
    else:
        assert (prepared.spec.receipts / "stopped.json").exists()


@pytest.mark.parametrize(
    "fault",
    [
        "type",
        "baseline",
        "writer",
        "endpoint",
        "generation",
        "short_start",
        "short_finish",
        "short_contract",
    ],
)
def test_unqualified_sender_refused_before_native_request(prepared, fault):
    stored = prepared.stored
    if fault in ("baseline", "writer", "endpoint", "short_contract"):
        stored = m.protected._decode(
            m.protected.manifest_bytes(
                replace(stored.baseline, files=()) if fault == "baseline" else stored.baseline,
                replace(stored.writer, uid=stored.writer.uid + 1)
                if fault == "writer"
                else stored.writer,
                "f" * 64 if fault == "endpoint" else stored.contract.audio_endpoint_sha256,
                maximum_recording_seconds=12 if fault == "short_contract" else 180,
            )
        )
    now = time.monotonic()
    binding = m.Binding(
        stored,
        "f" * 64 if fault == "generation" else prepared.generation,
        "1" * 64,
        "2" * 64,
        now + (1 if fault == "short_start" else 8),
        now + (10 if fault == "short_finish" else 18),
    )
    left, right = m.pair()
    try:
        with construction.build(prepared) as trial:
            sender = object() if fault == "type" else m.Sender(right, binding)
            trial._ready.set()  # Isolate request validation without network I/O.
            with pytest.raises(construction.assembly.n.UnconfirmedAssembly):
                trial.request_start(returns=sender)
            assert not trial._requested.is_set()
            assert not trial.manager.snapshot().active and not trial.runtime.running
            assert not trial.runtime.scanner.connected
            assert not list(prepared.spec.receipts.iterdir())
    finally:
        left.close()
        right.close()
