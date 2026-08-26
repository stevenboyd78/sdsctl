from __future__ import annotations

from collections.abc import Iterable

import pytest

import sds200
from sds200.exceptions import ScannerConnectionError
from sds200.rtsp import (
    DEFAULT_RTSP_MAX_RESPONSE_BODY_BYTES,
    DEFAULT_RTSP_MAX_RESPONSE_HEADER_BYTES,
    RtspClient,
    RtspProtocolError,
    RtspStatusError,
    parse_rtp_transport,
    parse_sdp_audio,
)


class FakeStreamSocket:
    def __init__(self, chunks: Iterable[bytes]) -> None:
        self.chunks = list(chunks)
        self.sent: list[bytes] = []
        self.receive_sizes: list[int] = []
        self.timeout: float | None = None
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, size: int) -> bytes:
        self.receive_sizes.append(size)
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > size:
            self.chunks.insert(0, chunk[size:])
            return chunk[:size]
        return chunk

    def close(self) -> None:
        self.closed = True


def response(
    cseq: int,
    *,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    status: str = "200 OK",
) -> bytes:
    values = {
        "CSeq": str(cseq),
        "Content-Length": str(len(body)),
    }
    if headers:
        values.update(headers)
    return (
        f"RTSP/1.0 {status}\r\n"
        + "".join(f"{name}: {value}\r\n" for name, value in values.items())
        + "\r\n"
    ).encode("ascii") + body


def make_client(
    stream: FakeStreamSocket,
    *,
    max_response_header_bytes: int = DEFAULT_RTSP_MAX_RESPONSE_HEADER_BYTES,
    max_response_body_bytes: int = DEFAULT_RTSP_MAX_RESPONSE_BODY_BYTES,
) -> RtspClient:
    def factory(address: tuple[str, int], timeout: float) -> FakeStreamSocket:
        assert address == ("192.0.2.25", 554)
        assert timeout == 2.0
        return stream

    return RtspClient(
        "192.0.2.25",
        timeout=2.0,
        connection_factory=factory,
        max_response_header_bytes=max_response_header_bytes,
        max_response_body_bytes=max_response_body_bytes,
    )


def test_rtsp_response_limit_defaults_are_public() -> None:
    assert (
        sds200.DEFAULT_RTSP_MAX_RESPONSE_HEADER_BYTES
        == DEFAULT_RTSP_MAX_RESPONSE_HEADER_BYTES
    )
    assert (
        sds200.DEFAULT_RTSP_MAX_RESPONSE_BODY_BYTES
        == DEFAULT_RTSP_MAX_RESPONSE_BODY_BYTES
    )
    assert "DEFAULT_RTSP_MAX_RESPONSE_HEADER_BYTES" in sds200.__all__
    assert "DEFAULT_RTSP_MAX_RESPONSE_BODY_BYTES" in sds200.__all__


def test_scanner_specific_session_sequence_with_fragmented_responses() -> None:
    sdp = (
        b"v=0\r\n"
        b"m=audio 0 RTP/AVP 0\r\n"
        b"a=control:trackID=1\r\n"
    )
    all_responses = b"".join(
        (
            response(1),
            response(
                2,
                headers={
                    "Content-Type": "application/sdp",
                    "Content-Base": "rtsp://192.0.2.25/au:scanner.au/",
                },
                body=sdp,
            ),
            response(
                3,
                headers={
                    "Session": "30026000",
                    "Transport": (
                        "RTP/AVP;unicast;client_port=48607;"
                        "source=192.0.2.25;server_port=56002;ssrc=1449463210"
                    ),
                },
            ),
            response(4, headers={"Session": "30026000"}),
            response(5, headers={"Session": "30026000"}),
            response(6, headers={"Session": "30026000"}),
        )
    )
    stream = FakeStreamSocket(
        all_responses[index : index + 7] for index in range(0, len(all_responses), 7)
    )
    client = make_client(stream)

    transport = client.start(48607)
    client.get_parameter()
    client.teardown()
    client.close()

    assert transport.source == "192.0.2.25"
    assert transport.server_port == 56002
    assert transport.ssrc == 1449463210

    requests = [value.decode("ascii") for value in stream.sent]
    assert requests[0].startswith("OPTIONS ")
    assert requests[1].startswith("DESCRIBE ")
    assert requests[2].startswith(
        "SETUP rtsp://192.0.2.25/au:scanner.au/trackID=1 RTSP/1.0"
    )
    assert "Transport: RTP/AVP;unicast;client_port=48607\r\n" in requests[2]
    assert "RTP/AVP/UDP" not in requests[2]
    assert "client_port=48607-" not in requests[2]
    assert requests[3].startswith("PLAY ")
    assert "Session: 30026000\r\n" in requests[3]
    assert "Range: npt=0.000-\r\n" in requests[3]
    assert requests[4].startswith("GET_PARAMETER ")
    assert requests[5].startswith("TEARDOWN ")
    for cseq, request in enumerate(requests, start=1):
        assert f"CSeq: {cseq}\r\n" in request
    assert stream.closed


def test_multiple_responses_in_one_read_are_preserved() -> None:
    stream = FakeStreamSocket([response(1) + response(2)])
    client = make_client(stream)
    client.connect()

    client.options()
    client.options()

    assert len(stream.sent) == 2


def test_response_header_exact_limit_accepts_split_terminator() -> None:
    packet = response(1)
    assert packet.endswith(b"\r\n\r\n")
    stream = FakeStreamSocket([packet[:-2], packet[-2:]])
    client = make_client(stream, max_response_header_bytes=len(packet))
    client.connect()

    parsed = client.options()

    assert parsed.status_code == 200
    assert not stream.closed


def test_response_header_one_over_limit_closes_connection_and_redacts() -> None:
    secret = "private-scanner-header-value"
    packet = response(1, headers={"X-Private": secret})
    stream = FakeStreamSocket([packet])
    client = make_client(stream, max_response_header_bytes=len(packet) - 1)
    client.connect()

    with pytest.raises(RtspProtocolError, match="headers exceed") as captured:
        client.options()

    assert secret not in str(captured.value)
    assert stream.closed
    with pytest.raises(ScannerConnectionError, match="not connected"):
        client.options()


def test_response_body_exact_limit_can_arrive_with_headers() -> None:
    body = b"exact-body"
    stream = FakeStreamSocket([response(1, body=body)])
    client = make_client(stream, max_response_body_bytes=len(body))
    client.connect()

    parsed = client.options()

    assert parsed.body == body
    assert not stream.closed


def test_response_body_one_over_limit_closes_connection_and_redacts() -> None:
    secret = b"private-scanner-body"
    stream = FakeStreamSocket([response(1, body=secret)])
    client = make_client(stream, max_response_body_bytes=len(secret) - 1)
    client.connect()

    with pytest.raises(RtspProtocolError, match="body exceeds") as captured:
        client.options()

    assert secret.decode() not in str(captured.value)
    assert stream.closed


def test_invalid_content_length_closes_connection_and_redacts() -> None:
    secret = "private-content-length"
    packet = (
        b"RTSP/1.0 200 OK\r\n"
        b"CSeq: 1\r\n"
        + f"Content-Length: {secret}\r\n\r\n".encode("ascii")
    )
    stream = FakeStreamSocket([packet])
    client = make_client(stream)
    client.connect()

    with pytest.raises(RtspProtocolError, match="invalid Content-Length") as captured:
        client.options()

    assert secret not in str(captured.value)
    assert stream.closed


@pytest.mark.parametrize(
    ("declared_length", "message"),
    (
        ("-1", "invalid Content-Length"),
        ("11", "body exceeds"),
    ),
)
def test_rejected_content_length_does_not_read_more_body_bytes(
    declared_length: str,
    message: str,
) -> None:
    private_body = b"private-body-must-remain-unread"
    packet = (
        b"RTSP/1.0 200 OK\r\n"
        b"CSeq: 1\r\n"
        + f"Content-Length: {declared_length}\r\n\r\n".encode("ascii")
    )
    stream = FakeStreamSocket([packet, private_body])
    client = make_client(stream, max_response_body_bytes=10)
    client.connect()

    with pytest.raises(RtspProtocolError, match=message) as captured:
        client.options()

    assert stream.receive_sizes == [4096]
    assert stream.chunks == [private_body]
    assert declared_length not in str(captured.value)
    assert private_body.decode() not in str(captured.value)
    assert stream.closed


@pytest.mark.parametrize("value", [0, -1])
def test_response_limits_must_be_positive(value: int) -> None:
    with pytest.raises(ValueError, match="header size must be greater than zero"):
        RtspClient("192.0.2.25", max_response_header_bytes=value)
    with pytest.raises(ValueError, match="body size must be greater than zero"):
        RtspClient("192.0.2.25", max_response_body_bytes=value)


@pytest.mark.parametrize("value", [True, 1.5, "1", None])
def test_response_limits_reject_non_integer_values(value: object) -> None:
    with pytest.raises(TypeError, match="header size must be an integer"):
        RtspClient("192.0.2.25", max_response_header_bytes=value)
    with pytest.raises(TypeError, match="body size must be an integer"):
        RtspClient("192.0.2.25", max_response_body_bytes=value)


def test_mismatched_cseq_closes_connection_and_redacts_peer_value() -> None:
    private_cseq = "private-cseq-value"
    stream = FakeStreamSocket([response(1, headers={"CSeq": private_cseq})])
    client = make_client(stream)
    client.connect()

    with pytest.raises(RtspProtocolError, match="CSeq does not match") as captured:
        client.options()

    assert private_cseq not in str(captured.value)
    assert stream.closed


def test_non_success_response_raises_status_error() -> None:
    stream = FakeStreamSocket([response(1, status="400 Bad Request")])
    client = make_client(stream)
    client.connect()

    with pytest.raises(RtspStatusError, match="400 Bad Request"):
        client.options()


def test_parse_sdp_requires_pcmu_audio_track() -> None:
    description = parse_sdp_audio(
        b"v=0\r\nm=audio 0 RTP/AVP 0\r\na=control:trackID=1\r\n"
    )
    assert description.control == "trackID=1"
    assert description.payload_types == (0,)

    with pytest.raises(RtspProtocolError, match="PCMU"):
        parse_sdp_audio(
            b"v=0\r\nm=audio 0 RTP/AVP 8\r\na=control:trackID=1\r\n"
        )


def test_parse_rtp_transport_requires_matching_single_port_and_source() -> None:
    transport = parse_rtp_transport(
        "RTP/AVP;unicast;client_port=48607;source=192.0.2.25;"
        "server_port=56002;ssrc=0x56650daa",
        client_port=48607,
    )

    assert transport.source == "192.0.2.25"
    assert transport.server_port == 56002
    assert transport.ssrc == 0x56650DAA

    with pytest.raises(RtspProtocolError, match="does not match"):
        parse_rtp_transport(
            "RTP/AVP;unicast;client_port=40000;source=192.0.2.25;"
            "server_port=56002",
            client_port=48607,
        )

    with pytest.raises(RtspProtocolError, match="RTP source"):
        parse_rtp_transport(
            "RTP/AVP;unicast;client_port=48607;server_port=56002",
            client_port=48607,
        )

    with pytest.raises(RtspProtocolError, match="single server_port"):
        parse_rtp_transport(
            "RTP/AVP;unicast;client_port=48607;source=192.0.2.25;"
            "server_port=56002-56003",
            client_port=48607,
        )
