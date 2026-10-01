"""Pure byte-boundary tests; no Docker socket, exec, scanner or host operation."""

import importlib.util
import struct
import sys
from pathlib import Path

import pytest

from . import test_supplemental_recording_wire as framing

NAME = "supplemental_recording_exec_stream"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(framing.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
w = framing.m
UPGRADE = (
    b"HTTP/1.1 101 UPGRADED\r\nContent-Type: application/vnd.docker.multiplexed-stream\r\n"
    b"Connection: Upgrade\r\nUpgrade: tcp\r\n\r\n"
)
MESSAGES = [{"phase": phase} for phase in ("ready", "started", "completed", "exited")]


def app(value):
    payload = w.encode(value)
    return w.HEADER.pack(len(payload)) + payload


def segment(payload, channel=1, reserved=b"\0\0\0"):
    return struct.pack("!B3sI", channel, reserved, len(payload)) + payload


def collect(raw, *, chunk=4096):
    decoder, messages = m.Decoder(), []
    for start in range(0, len(raw), chunk):
        messages.extend(decoder.feed(raw[start : start + chunk]))
    decoder.finish()
    return decoder, messages


def denied(decoder, raw):
    with pytest.raises(m.UnconfirmedExecStream) as caught:
        decoder.feed(raw)
    assert str(caught.value) == m.MESSAGE and "PRIVATE" not in str(caught.value)
    for action in (lambda: decoder.feed(UPGRADE), decoder.finish):
        with pytest.raises(m.UnconfirmedExecStream):
            action()


@pytest.mark.parametrize("chunk", [1, 2, 7, 4096])
@pytest.mark.parametrize("segmented", [1, 3, 8192])
def test_docker_and_operator_boundaries_are_independent(chunk, segmented):
    payload = b"".join(app(value) for value in MESSAGES)
    raw = UPGRADE + b"".join(
        segment(payload[i : i + segmented]) for i in range(0, len(payload), segmented)
    )
    decoder, messages = collect(raw, chunk=chunk)
    assert messages == MESSAGES and decoder.used and decoder.messages == 4
    assert not decoder.pending and not decoder.stdout
    denied(decoder, segment(app({"phase": "extra"})))


def test_four_maximum_frames_fit_without_unbounded_accumulation():
    values = [{"x": str(i) * (w.MAX_BYTES - 8)} for i in range(4)]
    payload = b"".join(app(value) for value in values)
    assert len(payload) == m.MAX_STDOUT
    raw = UPGRADE + segment(payload[:32000]) + segment(payload[32000:])
    decoder, messages = collect(raw, chunk=1234)
    assert messages == values and decoder.output_bytes == m.MAX_STDOUT


@pytest.mark.parametrize(
    "old,new",
    [
        (b"101 UPGRADED", b"200 OK"),
        (b"HTTP/1.1", b"HTTP/1.0"),
        (b"Upgrade: tcp", b"Upgrade: websocket"),
        (b"Connection: Upgrade", b"Connection: keep-alive"),
        (b"multiplexed-stream", b"raw-stream"),
        (b"Connection: Upgrade", b"connection: Upgrade\r\nConnection: Upgrade"),
        (b"Upgrade: tcp", b"Upgrade: tcp\r\nContent-Length: 0"),
        (b"Upgrade: tcp", b"Upgrade: tcp\r\nTransfer-Encoding: chunked"),
        (b"Upgrade: tcp", b"Upgrade: tcp\r\nContent-Encoding: gzip"),
        (b"Upgrade: tcp", b"Upgrade: tcp\r\nTrailer: PRIVATE"),
        (b"Upgrade: tcp", b"Upgrade: tcp\r\n PRIVATE: folded"),
        (b"Upgrade: tcp", b"Upgrade: tcp\r\nX: PRIVATE\x00"),
        (b"Upgrade: tcp", b"Upgrade: tcp\r\nNoColon"),
    ],
)
def test_invalid_or_ambiguous_upgrade_never_enters_stream(old, new):
    denied(m.Decoder(), UPGRADE.replace(old, new))


@pytest.mark.parametrize("channel", [0, 2, 3, 4, 255])
def test_only_stdout_is_admitted_even_for_empty_segments(channel):
    denied(m.Decoder(), UPGRADE + segment(b"", channel=channel))


@pytest.mark.parametrize("reserved", [b"\1\0\0", b"\0\1\0", b"\0\0\1"])
def test_reserved_bytes_are_not_ignored(reserved):
    denied(m.Decoder(), UPGRADE + segment(b"PRIVATE", reserved=reserved))


@pytest.mark.parametrize("size", [m.MAX_STDOUT + 1, 2**32 - 1])
def test_announced_segment_size_is_checked_before_reading_payload(size):
    denied(m.Decoder(), UPGRADE + struct.pack("!B3sI", 1, b"\0" * 3, size))


@pytest.mark.parametrize(
    "payload", [b"{} ", b'{"x":1,"x":2}', b'{"x":NaN}', b"[]", b"null", b"\xff", b"PRIVATE"]
)
def test_noncanonical_or_invalid_inner_json_is_not_a_return(payload):
    denied(m.Decoder(), UPGRADE + segment(w.HEADER.pack(len(payload)) + payload))


@pytest.mark.parametrize("cut", [0, 1, 4, 8, 40, len(UPGRADE), len(UPGRADE) + 1, len(UPGRADE) + 9])
def test_incomplete_eof_consumes_decoder_without_success(cut):
    raw = UPGRADE + segment(b"".join(app(value) for value in MESSAGES))
    decoder = m.Decoder()
    if cut:
        decoder.feed(raw[:cut])
    with pytest.raises(m.UnconfirmedExecStream):
        decoder.finish()
    denied(decoder, raw)


@pytest.mark.parametrize("suffix", [b"\1", segment(b"a"), segment(app({"phase": "fifth"}))])
def test_four_messages_do_not_allow_trailing_output(suffix):
    decoder = m.Decoder()
    assert decoder.feed(UPGRADE + segment(b"".join(app(value) for value in MESSAGES))) == MESSAGES
    with pytest.raises(m.UnconfirmedExecStream):
        decoder.feed(suffix)
        decoder.finish()
    assert decoder.used


def test_empty_docker_segments_have_finite_count(monkeypatch):
    monkeypatch.setattr(m, "MAX_SEGMENTS", 3)
    denied(m.Decoder(), UPGRADE + segment(b"") * 4)


def test_total_input_limit_includes_framing_not_just_stdout(monkeypatch):
    monkeypatch.setattr(m, "MAX_INPUT", len(UPGRADE))
    decoder = m.Decoder()
    assert decoder.feed(UPGRADE) == []
    denied(decoder, segment(b""))


@pytest.mark.parametrize(
    "raw", [b"", "PRIVATE", bytearray(b"PRIVATE"), None, b"a" * (m.MAX_CHUNK + 1)]
)
def test_chunk_type_and_size_are_closed(raw):
    denied(m.Decoder(), raw)


def test_upgrade_header_limit_before_or_after_delimiter():
    for raw in (
        b"x" * (m.MAX_HEADER + 1),
        UPGRADE[:-2] + b"X: " + b"x" * m.MAX_HEADER + b"\r\n\r\n",
    ):
        denied(m.Decoder(), raw)


def test_interrupted_decode_is_consumed_even_when_cancellation_propagates(monkeypatch):
    raw = UPGRADE + segment(app(MESSAGES[0]))

    def cancelled(*_):
        raise KeyboardInterrupt

    monkeypatch.setattr(w, "encode", cancelled)
    decoder = m.Decoder()
    with pytest.raises(KeyboardInterrupt):
        decoder.feed(raw)
    assert decoder.used and not decoder.pending and not decoder.stdout
    denied(decoder, raw)
