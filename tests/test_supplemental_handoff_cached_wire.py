"""Bounded five-read IPC wire, real local socket fixture, no scanner or demand."""

import json
import socket
import threading
from pathlib import Path

import pytest

from .test_supplemental_handoff_cached import c


def hello():
    return dict(
        protocol="sdsctl.daemon",
        selected_version=1,
        supported_versions=[1],
        read_only=False,
        operations=list(set(c.CachedClient.SEQUENCE)),
    )


class Stream:
    def __init__(self):
        self.sent, self.raw, self.closed = [], b"", False
        self.modify = lambda value: value

    def sendall(self, data):
        value = json.loads(data)
        self.sent.append(value)
        assert (
            value["params"] == {} and value["protocol"] == "sdsctl.daemon" and value["version"] == 1
        )
        assert value["operation"] in c.CachedClient.SEQUENCE
        response = dict(
            protocol="sdsctl.daemon",
            version=1,
            request_id=value["request_id"],
            ok=True,
            result=hello() if value["operation"] == "hello" else {},
        )
        value = self.modify(response)
        self.raw = value if isinstance(value, bytes) else json.dumps(value).encode() + b"\n"

    def recv(self, size):
        # Fragmentation must not be mistaken for multiple replies.
        take = min(size, 31)
        result, self.raw = self.raw[:take], self.raw[take:]
        return result

    def settimeout(self, timeout):
        assert 0 < timeout <= 0.2

    def close(self):
        self.closed = True


@pytest.fixture
def client():
    reader = c.CachedClient(Path("/tmp/unused.sock"), timeout=0.2)
    stream = Stream()
    reader.socket = stream
    yield reader, stream
    reader.__exit__()
    assert stream.closed


def test_fixed_cached_sequence_and_correlation(client):
    reader, stream = client
    reader.hello()
    reader.runtime_snapshot()
    reader.recording_status()
    reader.request("display.profile")
    reader.runtime_snapshot()
    assert [v["operation"] for v in stream.sent] == list(c.CachedClient.SEQUENCE)
    assert len({v["request_id"] for v in stream.sent}) == 5
    with pytest.raises(c.UnconfirmedCache):
        reader.runtime_snapshot()
    assert len(stream.sent) == 5


@pytest.mark.parametrize(
    "operation", ["display.supplemental.demand", "scanner.state", "recording.stop", "hello"]
)
def test_nonprofile_request_is_refused_before_send(client, operation):
    reader, stream = client
    with pytest.raises(c.UnconfirmedCache):
        reader.request(operation)
    assert not stream.sent


@pytest.mark.parametrize(
    "field,value",
    [
        ("protocol", "wrong"),
        ("version", True),
        ("version", 2),
        ("request_id", "other"),
        ("ok", False),
        ("ok", 1),
        ("result", None),
        ("error", {}),
        ("extra", 1),
    ],
)
def test_wire_envelope_requires_exact_success(client, field, value):
    reader, stream = client
    stream.modify = lambda item: item | {field: value}
    with pytest.raises(c.UnconfirmedCache):
        reader.hello()
    with pytest.raises(c.UnconfirmedCache):
        reader.hello()
    assert len(stream.sent) == 1


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]\n",
        b"{}\n{}\n",
        b'{"ok":true,"ok":true}\n',
        b'{"result":NaN}\n',
        b"\xff\n",
        b"x" * (1024 * 1024 + 1),
    ],
)
def test_bad_or_oversized_framing_is_not_evidence(client, raw):
    reader, stream = client
    stream.modify = lambda _: raw
    with pytest.raises((ValueError, UnicodeError)):
        reader.hello()


@pytest.mark.parametrize(
    "field,value",
    [
        ("protocol", "wrong"),
        ("selected_version", True),
        ("selected_version", 2),
        ("supported_versions", [True]),
        ("supported_versions", []),
        ("read_only", 0),
        ("operations", ["hello"]),
        ("operations", [1]),
    ],
)
def test_hello_must_advertise_all_four_cached_operations(client, field, value):
    reader, stream = client
    stream.modify = lambda item: item | {"result": hello() | {field: value}}
    with pytest.raises(c.UnconfirmedCache):
        reader.hello()


def test_real_unix_peer_five_reads_and_close(tmp_path):
    path = tmp_path / "cache.sock"
    calls = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        listener.listen(1)
        listener.settimeout(2)

        def server():
            peer, _ = listener.accept()
            with peer, peer.makefile("rb") as stream:
                for expected in c.CachedClient.SEQUENCE:
                    request = json.loads(stream.readline(4096))
                    calls.append(request["operation"])
                    assert request["operation"] == expected and request["params"] == {}
                    response = dict(
                        protocol="sdsctl.daemon",
                        version=1,
                        request_id=request["request_id"],
                        ok=True,
                        result=hello() if expected == "hello" else {},
                    )
                    peer.sendall(json.dumps(response).encode() + b"\n")
                assert stream.read(1) == b""

        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        with c.CachedClient(path, timeout=0.2) as reader:
            assert reader.connect().fileno() >= 0
            reader.hello()
            reader.runtime_snapshot()
            reader.recording_status()
            reader.request("display.profile")
            reader.runtime_snapshot()
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert calls == list(c.CachedClient.SEQUENCE)
