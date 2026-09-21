"""Loopback RTP and actual daemon PCMU delivery beside one native optional GET.

RTSP negotiation is synthetic; no physical scanner, browser playback, HTTP or
encoder is claimed. Both network sockets are localhost-only. The real RTP
receiver fans out to the PCMU Unix client and a decoded WAV recording.
"""

import os
import socket
import wave
from types import SimpleNamespace

import pytest

from sds200.audio import AudioStream
from sds200.audio_recording import decode_mulaw
from sds200.audio_sinks import AudioFanoutSession, PcmSinkRouter
from sds200.daemon_ipc import DaemonSocketListener, DaemonSocketLocation, DaemonSocketSource
from sds200.daemon_pcmu_client import DaemonPcmuClient
from sds200.daemon_pcmu_server import DaemonPcmuServer
from sds200.daemon_recording import DaemonRecordingManager
from sds200.daemon_runtime import DaemonRuntime
from sds200.network_audio import NetworkAudioTransport
from sds200.pcmu_stream import PcmuStream
from sds200.rtsp import RtpTransportInfo

from . import test_supplemental_native_lifecycle as lifecycle
from .test_network_audio import FakeRtspClient, make_rtp
from .test_supplemental_native_lifecycle import configured as configured
from .test_supplemental_native_lifecycle import demand, settled, wait_for
from .test_supplemental_native_lifecycle import rig as rig

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Native POSIX UDP/Unix candidate")


@pytest.fixture
def network_rig(monkeypatch, request):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
        peer.bind(("127.0.0.1", 0))

        class Rtsp(FakeRtspClient):
            def start(self, client_port):
                self.started_ports.append(client_port)
                return RtpTransportInfo(
                    source="127.0.0.1", server_port=peer.getsockname()[1], ssrc=5678
                )

        rtsp = Rtsp()
        transport = NetworkAudioTransport(
            "127.0.0.1",
            local_host="127.0.0.1",
            read_timeout=0.01,
            rtsp_client_factory=lambda *_: rtsp,
        )

        def runtime_for(scanner):
            router = PcmSinkRouter(name="network-test")
            audio = AudioFanoutSession(AudioStream(transport), (router,))
            return DaemonRuntime(scanner, audio, router)

        monkeypatch.setattr(lifecycle, "runtime_for", runtime_for)
        native = request.getfixturevalue("rig")
        try:
            yield SimpleNamespace(rig=native, peer=peer, transport=transport, rtsp=rtsp)
        finally:
            # Idempotent with rig cleanup; finish the actual transport before
            # closing its peer and assert no surviving RTSP/RTP session.
            native.runtime.stop()
            assert not transport.running
            assert rtsp.teardowns == 1 and rtsp.closed


@pytest.mark.parametrize("command", ["FQK", "DTM"])
@pytest.mark.parametrize("reply", [True, False])
def test_native_read_allows_rtp_pcmu_delivery_and_recording(network_rig, tmp_path, command, reply):
    media = network_rig
    native = media.rig
    stream = PcmuStream(media.transport)
    location = DaemonSocketLocation(tmp_path / "p.sock", DaemonSocketSource.EXPLICIT)
    server = DaemonPcmuServer(DaemonSocketListener(location), stream, accept_poll_interval=0.01)
    client = DaemonPcmuClient(location, timeout=1)
    manager = DaemonRecordingManager(native.runtime, tmp_path / "recordings")
    try:
        server.start()
        client.connect().settimeout(1)  # Bound this test consumer's receive only.
        wait_for(lambda: server.connected_clients == 1 and stream.subscriber_count == 1)
        manager.start_recording()
        with native.peer.hold(command) as gate:
            demand(native, command)
            assert gate.entered.wait(1)
            before = native.cache.supplemental_snapshot().sequence
            payload = bytes(range(160))
            for index in range(8):
                media.peer.sendto(
                    make_rtp(payload, sequence=100 + index, timestamp=1000 + index * 160),
                    ("127.0.0.1", media.rtsp.started_ports[0]),
                )
            deliveries = [client.receive() for _ in range(8)]
            wait_for(lambda: manager.snapshot().samples == 1280)
            wait_for(lambda: native.cache.supplemental_snapshot().sequence > before)
            assert [delivery.packet.payload for delivery in deliveries] == [payload] * 8
            assert [delivery.stream_sequence for delivery in deliveries] == list(range(1, 9))
            assert all(delivery.health == "healthy" for delivery in deliveries)
            if reply:
                gate.release.set()
            settled(native)
            assert native.cache.snapshot().blocked_until_reconnect == (None if reply else "timeout")
        health = client.snapshot()
        assert health.packets_received == 8 and health.samples_received == 1280
        assert health.stream_packets_skipped == health.packets_dropped == health.overflows == 0
        assert health.rtp_missing_packets == health.rtp_missing_samples == 0
        assert health.rtp_timestamp_backwards == 0
        stopped = manager.stop_recording()
        assert stopped.error is None and stopped.samples == 1280
        assert all(value == 0 for value in stopped.reliability.as_dict().values())
        with wave.open(str(stopped.recording_path), "rb") as wav:
            assert wav.getnframes() == 1280
            assert wav.readframes(1280) == decode_mulaw(payload) * 8
        assert media.transport.statistics.packets_delivered == 8
        assert native.peer.reads == (["FQK"] if command == "FQK" else ["FQK", "DTM"])
        assert len(media.rtsp.started_ports) == 1  # One existing audio owner only.
    finally:
        client.close()
        manager.close()
        server.stop()
        assert stream.closed and not server.active
