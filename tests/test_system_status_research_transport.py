from __future__ import annotations

import threading

import pytest

from sds200.daemon_system_status_research import (
    SystemStatusResearchAttempt,
    SystemStatusResearchPolicy,
    SystemStatusResearchStatus,
)
from sds200.exceptions import CommandTimeoutError, UnsupportedScannerFeatureError
from sds200.radio import SDS200

from .fakes import FakeDatagramSocket, FakeDatagramSocketFactory, FakeTransport


def xml_reply(command: str, *, analysis: bool = False) -> bytes:
    records = (
        '<SystemStatus SystemID="00123h"/>'
        if analysis
        else '<System Index="120"/><Site Index="240"/>'
    )
    screen = "analyze_system_status" if analysis else "trunk_scan"
    return (
        f'{command},<XML>,<ScannerInfo Mode="Trunk Scan" V_Screen="{screen}">'
        f'{records}<Footer No="1" EOT="1"/></ScannerInfo>'
    ).encode()


class RespondingSocket(FakeDatagramSocket):
    def __init__(self, *, ack: bool) -> None:
        super().__init__()
        self.ack = ack
        self.quit = threading.Event()
        self.producer: threading.Thread | None = None

    def send(self, data: bytes) -> int:
        result = super().send(data)
        if data == b"MDL\r":
            self.feed(b"MDL,SDS200\r")
        elif data == b"VER\r":
            self.feed(b"VER,Version 1.00.00\r")
        elif data == b"GSI\r":
            self.feed(xml_reply("GSI"))
        elif data == b"AST,SYSTEM_STATUS,240\r" and self.ack:
            self.feed(b"AST,OK\r")

            def publish() -> None:
                while not self.quit.wait(0.005):
                    self.feed(xml_reply("PSI", analysis=True))

            self.producer = threading.Thread(target=publish, daemon=True)
            self.producer.start()
        return result

    def close(self) -> None:
        self.quit.set()
        if self.producer is not None:
            self.producer.join(timeout=1)
        super().close()


@pytest.mark.parametrize("ack", [True, False])
def test_real_udp_radio_framing_single_ast_and_no_reconnect(ack: bool) -> None:
    socket = RespondingSocket(ack=ack)
    factory = FakeDatagramSocketFactory(socket)
    radio = SDS200.network("192.0.2.10", reconnect=False, socket_factory=factory)
    with radio:
        probe = SystemStatusResearchAttempt(SystemStatusResearchPolicy("Version 1.00.00"))
        result = probe.run(radio, operator_ready=True, timeout=0.15)
        expected = (
            SystemStatusResearchStatus.ANALYSIS_OBSERVED
            if ack
            else SystemStatusResearchStatus.START_UNCONFIRMED
        )
        assert result.status is expected
        assert result.acknowledged is ack
        assert socket.sent == [
            b"MDL\r",
            b"VER\r",
            b"GSI\r",
            b"GSI\r",
            b"AST,SYSTEM_STATUS,240\r",
        ]
        assert len(factory.calls) == 1
        assert len(radio.events._callbacks["psi"]) == 0
        assert len(radio.events._callbacks["connection"]) == 0
    assert socket.closed


def test_scope_does_not_open_a_disconnected_udp_transport() -> None:
    factory = FakeDatagramSocketFactory()
    radio = SDS200.network("192.0.2.10", socket_factory=factory)
    with (
        pytest.raises(UnsupportedScannerFeatureError, match="existing connection"),
        radio._system_status_research_scope(timeout=0.1),
    ):
        pytest.fail("Disconnected scope was accepted")
    assert factory.calls == [] and factory.socket.sent == []


def test_scope_refuses_fake_udp_endpoint_before_write() -> None:
    transport = FakeTransport("udp://192.0.2.10:50536")
    radio = SDS200.from_transport(transport)
    with (
        pytest.raises(UnsupportedScannerFeatureError, match="directly owned UDP"),
        radio._system_status_research_scope(timeout=0.1),
    ):
        pytest.fail("Unqualified transport was accepted")
    assert transport.writes == []


def test_scope_command_lock_wait_is_bounded() -> None:
    factory = FakeDatagramSocketFactory()
    radio = SDS200.network("192.0.2.10", socket_factory=factory, reconnect=False)
    held = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with radio._command_lock:
            held.set()
            assert release.wait(1)

    worker = threading.Thread(target=hold)
    worker.start()
    try:
        assert held.wait(1)
        with (
            pytest.raises(CommandTimeoutError, match="scope timed out"),
            radio._system_status_research_scope(timeout=0.01),
        ):
            pytest.fail("Busy command scope was accepted")
    finally:
        release.set()
        worker.join(timeout=1)
    assert not worker.is_alive()
    assert factory.calls == [] and factory.socket.sent == []
