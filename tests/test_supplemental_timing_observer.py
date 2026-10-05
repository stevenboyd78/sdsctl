"""Pure datagram replay; no socket creation or hardware discovery."""

import importlib.util
import json
import socket
import struct
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/observe_supplemental_timing.py"
SPEC = importlib.util.spec_from_file_location("passive_timing", PATH)
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)


def datagram(body=b"FQK,PRIVATE", source="192.0.2.25", port=50536):
    header = bytearray(20)
    header[0], header[9] = 0x45, 17
    header[2:4] = struct.pack("!H", 28 + len(body))
    header[12:16] = socket.inet_aton(source)
    header[16:20] = socket.inet_aton("192.0.2.30")
    return bytes(header) + struct.pack("!HHHH", port, 50001, 8 + len(body), 0) + body


def test_every_allowed_reply_and_late_seventh_pair_is_timestamped():
    capture = observer.Observation("192.0.2.25")
    for i in range(60):
        capture.feed(datagram(b"FQK,PRIVATE" if i % 2 == 0 else b"DTM,PRIVATE"), 100 + i)
    result = capture.report()
    assert result["incoming_reply_counts"] == {"FQK": 30, "DTM": 30}
    assert len(result["replies"]) == 60 and result["replies"][12]["monotonic_seconds"] == 112
    assert not result["stop_reason"]
    assert not result["outgoing_wire_delivery_established"]
    assert "PRIVATE" not in json.dumps(result) and "192.0.2" not in json.dumps(result)
    result["replies"].clear()
    assert len(capture.replies) == 60


def test_unexpected_reply_flood_closes_at_fixed_bound():
    capture = observer.Observation("192.0.2.25")
    for i in range(100):
        capture.feed(datagram(), i)
    assert len(capture.replies) == 80 and capture.stopped == "reply_limit"


def test_packet_limit_is_independent_of_matching_reply_limit():
    capture = observer.Observation("192.0.2.25")
    for i in range(observer.PACKET_LIMIT + 2):
        capture.feed(b"", i)
    assert capture.packets == observer.PACKET_LIMIT and capture.stopped == "packet_limit"


@pytest.mark.parametrize(
    "case", ["short", "ipv6", "tcp", "ihl", "total", "fragment", "length", "source", "port"]
)
def test_malformed_or_unrelated_datagrams_are_not_retained(case):
    packet = bytearray(datagram())
    if case == "short":
        packet = packet[:20]
    elif case == "ipv6":
        packet[0] = 0x65
    elif case == "tcp":
        packet[9] = 6
    elif case == "ihl":
        packet[0] = 0x44
    elif case == "total":
        packet[2:4] = b"\xff\xff"
    elif case == "fragment":
        packet[6:8] = b"\x20\x00"
    elif case == "length":
        packet[24:26] = b"\xff\xff"
    elif case == "source":
        packet = datagram(source="192.0.2.99")
    else:
        packet = datagram(port=1)
    capture = observer.Observation("192.0.2.25")
    capture.feed(bytes(packet), 1)
    assert not capture.replies and not capture.psi


def test_parser_failures_are_bounded_counts_not_exception_messages():
    capture = observer.Observation("192.0.2.25")

    def fail(_body):
        raise RuntimeError("PRIVATE_EXCEPTION")

    capture.decoder.feed = fail
    capture.feed(datagram(b"unparsed"), 1)
    assert capture.errors == 1 and "PRIVATE" not in json.dumps(capture.report())


def test_normal_psi_decodes_and_has_an_independent_bound():
    capture = observer.Observation("192.0.2.25")
    body = (
        b'PSI,<XML>,<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">'
        b'<System Name="PRIVATE_SYSTEM"/><Footer No="1" EOT="1" /></ScannerInfo>'
    )
    for i in range(observer.PSI_LIMIT + 2):
        capture.feed(datagram(body), i)
    assert capture.psi == observer.PSI_LIMIT and capture.stopped == "psi_limit"
    assert capture.screens == {("trunk_scan", False): observer.PSI_LIMIT}
    assert not capture.errors and "PRIVATE" not in json.dumps(capture.report())


def test_main_closes_receive_socket_at_deadline_without_sends(monkeypatch, capsys):
    clock = [10.0]

    class ReceiveOnly:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def bind(self, address):
            assert address == ("eth0", 0)

        def settimeout(self, duration):
            assert 0 < duration <= 0.5
            self.duration = duration

        def recv(self, size):
            assert size == 65535
            clock[0] += self.duration
            raise TimeoutError

    sock = ReceiveOnly()
    monkeypatch.setattr(observer.socket, "socket", lambda *_args: sock)
    monkeypatch.setattr(observer, "monotonic", lambda: clock[0])
    monkeypatch.setattr(observer.Path, "read_text", lambda *_args: '{"scanner_host":"192.0.2.25"}')
    monkeypatch.setattr(observer.logging, "disable", lambda *_args: None)
    monkeypatch.setattr("sys.argv", [str(PATH), "--observe-75s"])
    observer.main()
    rows = [json.loads(row) for row in capsys.readouterr().out.splitlines()]
    assert rows[0]["capture_ready"] and rows[-1]["summary"]["complete"]
    assert rows[-1]["summary"]["elapsed_seconds"] == 75
    assert rows[-1]["summary"]["collector_commands_sent"] == 0 and sock.closed
