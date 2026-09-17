"""Real radio/UDP framing on fake sockets, never a network scanner."""

import threading

import pytest

from sds200.daemon_display_read_research import (
    DisplayReadKind,
    DisplayReadResearchAttempt,
    DisplayReadResearchPolicy,
)
from sds200.exceptions import UnsupportedScannerFeatureError
from sds200.radio import SDS200

from .fakes import FakeDatagramSocket, FakeDatagramSocketFactory, FakeTransport
from .test_daemon_display_read_research import FIRMWARE, STATES


class ReplySocket(FakeDatagramSocket):
    def __init__(self, *, timeout=False):
        super().__init__()
        self.no_reply = timeout
        self.quit = threading.Event()
        self.producer = None

    def send(self, data):
        size = super().send(data)
        if data == b"MDL\r":
            self.feed(b"MDL,SDS200\r")

            def publish():
                while not self.quit.wait(0.003):
                    self.feed(
                        b'PSI,<XML>,<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">'
                        b'<MonitorList Index="700" Q_Key="01"/><System Index="120" Q_Key="23"/>'
                        b'<Site Index="240"/><Footer No="1" EOT="1"/></ScannerInfo>'
                    )

            self.producer = threading.Thread(target=publish, daemon=True)
            self.producer.start()
        elif data == b"VER\r":
            self.feed(f"VER,{FIRMWARE}\r".encode())
        elif not self.no_reply:
            name = data.decode().split(",")[0].strip()
            fields = (
                ("0", "2026", "09", "17", "09", "45", "02", "1")
                if name == "DTM"
                else STATES
                if name == "FQK"
                else ("1", "23", *STATES)
            )
            self.feed((name + "," + ",".join(fields) + "\r").encode())
        return size

    def close(self):
        self.quit.set()
        if self.producer is not None:
            self.producer.join(timeout=1)
            assert not self.producer.is_alive()
        super().close()


@pytest.mark.parametrize(
    "kind,wire",
    [
        (DisplayReadKind.CLOCK, b"DTM\r"),
        (DisplayReadKind.FAVORITES, b"FQK\r"),
        (DisplayReadKind.SYSTEM, b"SQK,1\r"),
        (DisplayReadKind.DEPARTMENT, b"DQK,1,23\r"),
    ],
)
@pytest.mark.parametrize("timeout", [False, True])
def test_one_selected_get_uses_only_existing_udp_socket_and_never_retries(kind, wire, timeout):
    socket = ReplySocket(timeout=timeout)
    factory = FakeDatagramSocketFactory(socket)
    radio = SDS200.network("192.0.2.10", reconnect=False, socket_factory=factory)
    with radio:
        probe = DisplayReadResearchAttempt(DisplayReadResearchPolicy(FIRMWARE, kind))
        result = probe.run(radio, operator_ready=True, timeout=0.15)
        assert result.status == ("read_unconfirmed" if timeout else "reply_and_psi_observed")
        assert result.response_validated is not timeout
        assert socket.sent == [b"MDL\r", b"VER\r", wire]
        assert len(factory.calls) == 1
        for event in ("psi", "packet", "connection"):
            assert len(radio.events._callbacks[event]) == 0
    assert socket.closed


def test_read_scope_does_not_implicitly_connect_udp():
    factory = FakeDatagramSocketFactory()
    radio = SDS200.network("192.0.2.10", socket_factory=factory)
    with (
        pytest.raises(UnsupportedScannerFeatureError, match="existing connection"),
        radio._display_read_research_scope(timeout=0.1),
    ):
        pytest.fail("Unexpected scope")
    assert factory.calls == [] and factory.socket.sent == []


def test_read_scope_refuses_custom_transport_even_with_udp_name():
    transport = FakeTransport("udp://192.0.2.10:50536")
    radio = SDS200.from_transport(transport)
    with (
        pytest.raises(UnsupportedScannerFeatureError, match="directly owned UDP"),
        radio._display_read_research_scope(timeout=0.1),
    ):
        pytest.fail("Unexpected scope")
    assert transport.writes == []
