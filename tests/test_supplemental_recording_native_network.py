"""Complete native process with localhost RTP/Unix PCMU, synthetic RTSP and PSI."""

import socket
import wave
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sds200.audio import AudioStream
from sds200.audio_recording import decode_mulaw
from sds200.audio_sinks import AudioFanoutSession, PcmSinkRouter
from sds200.daemon_api import DaemonApiOperation as Op
from sds200.daemon_ipc import resolve_daemon_socket_location
from sds200.daemon_pcmu_client import DaemonPcmuClient
from sds200.daemon_supplemental_acquisition import SupplementalAcquisitionPolicy
from sds200.network_audio import NetworkAudioTransport
from sds200.rtsp import RtpTransportInfo

from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_key_worker import wait_for
from .test_daemon_server import connect
from .test_network_audio import FakeRtspClient, make_rtp
from .test_supplemental_acceptance_launcher import native as native
from .test_supplemental_recording_assembly import native_bundle, query, run_observed


@pytest.mark.parametrize("command", ("FQK", "DTM"))
@pytest.mark.parametrize("reply", (True, False))
def test_one_native_rtp_owner_serves_pcmu_and_recording_through_shutdown(
    native, tmp_path, command, reply
):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.bind(("127.0.0.1", 0))

        class Rtsp(FakeRtspClient):
            def start(self, client_port):
                self.started_ports.append(client_port)
                return RtpTransportInfo(
                    source="127.0.0.1", server_port=sender.getsockname()[1], ssrc=5678
                )

        rtsp = Rtsp()
        transport = NetworkAudioTransport(
            "127.0.0.1",
            local_host="127.0.0.1",
            read_timeout=0.01,
            rtsp_client_factory=lambda *_: rtsp,
        )
        router = PcmSinkRouter()
        source = SimpleNamespace(
            scanner=native.scanner,
            peer=native.peer,
            profile=native.profile,
            router=router,
            audio=AudioFanoutSession(AudioStream(transport), (router,)),
        )
        policy = SupplementalAcquisitionPolicy("Version 1.26.01", 1.5, 2)
        with native_bundle(source, tmp_path, pcmu=True, policy=policy) as rig:
            trial = rig.build()
            location = resolve_daemon_socket_location(rig.socket.with_name("pcmu"))
            client = DaemonPcmuClient(location, timeout=1)
            payload = bytes(range(160))

            def operator(finished):
                wait_for(lambda: trial.ready)
                client.connect().settimeout(1)
                wait_for(lambda: rig.packets.connected_clients == 1)
                with connect(rig.socket) as peer, rig.peer.hold(command) as gate:
                    trial.request_start()
                    wait_for(lambda: rig.acquisition.status().armed)
                    context = query(peer, Op.DISPLAY_SUPPLEMENTAL_CONTEXT)["result"]["context"]
                    assert query(
                        peer,
                        Op.DISPLAY_SUPPLEMENTAL_DEMAND,
                        {"context": context, "renewal_id": str(uuid4())},
                    )["ok"]
                    assert gate.entered.wait(2)
                    psi_before = rig.peer.psi_sent
                    for index in range(8):
                        sender.sendto(
                            make_rtp(payload, sequence=100 + index, timestamp=1000 + 160 * index),
                            ("127.0.0.1", rtsp.started_ports[0]),
                        )
                    packets = [client.receive() for _ in range(8)]
                    assert [item.packet.payload for item in packets] == [payload] * 8
                    assert all(item.health == "healthy" for item in packets)
                    wait_for(lambda: rig.manager.snapshot().samples == 1280)
                    wait_for(lambda: rig.peer.psi_sent > psi_before)
                    if reply:
                        gate.release.set()
                    wait_for(lambda: rig.acquisition._cache._pending is None)
                    assert rig.acquisition._cache.snapshot().blocked_until_reconnect == (
                        None if reply else "timeout"
                    )
                    finished.wait(8)

            try:
                result = run_observed(rig, trial, operator)
                assert result.artifact.samples == 1280 and result.artifact.packets == 8
                assert trial.cleanup_complete and not transport.running
                assert len(rtsp.started_ports) == rtsp.teardowns == 1 and rtsp.closed
                assert rig.packets.stream.closed and not rig.packets.active
                assert not location.path.exists()
                assert rig.peer.reads == (
                    ["FQK"] if command == "FQK" and not reply else ["FQK", "DTM"]
                )
                health = client.snapshot()
                assert health.packets_received == 8 and health.samples_received == 1280
                assert (
                    health.rtp_missing_packets
                    == health.rtp_missing_samples
                    == health.packets_dropped
                    == 0
                )
                assert transport.statistics.packets_delivered == 8
                with wave.open(str(next(rig.root.glob("*.wav"))), "rb") as wav:
                    assert wav.readframes(1280) == decode_mulaw(payload) * 8
            finally:
                client.close()
