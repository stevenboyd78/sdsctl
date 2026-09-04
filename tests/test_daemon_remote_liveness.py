from __future__ import annotations

import errno
import socket
import threading
from typing import cast
from unittest.mock import Mock

import pytest

from sds200.daemon_remote_liveness import configure_remote_tcp_liveness


def test_healthy_quiet_tcp_peer_survives_keepalive_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sds200 import daemon_remote_liveness as liveness

    monkeypatch.setattr(liveness, "REMOTE_KEEPALIVE_IDLE_SECONDS", 1)
    monkeypatch.setattr(liveness, "REMOTE_KEEPALIVE_INTERVAL_SECONDS", 1)
    monkeypatch.setattr(liveness, "REMOTE_KEEPALIVE_PROBES", 1)
    monkeypatch.setattr(liveness, "REMOTE_USER_TIMEOUT_MS", 2000)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        with socket.create_connection(listener.getsockname(), timeout=5.0) as client:
            server, _ = listener.accept()
            with server:
                configure_remote_tcp_liveness(client)
                configure_remote_tcp_liveness(server)
                # No application messages for longer than the tuned detection
                # period. TCP answers probes without scanner events/heartbeats.
                sender = threading.Timer(3.0, server.sendall, args=(b"still live",))
                sender.start()
                try:
                    assert client.recv(32) == b"still live"
                finally:
                    sender.cancel()
                    sender.join(5.0)


def test_remote_tcp_liveness_sets_per_socket_deadlines() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.settimeout(3.5)
        configure_remote_tcp_liveness(stream)
        assert stream.gettimeout() == 3.5
        assert stream.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
        for name, expected in (
            ("TCP_KEEPIDLE", 10),
            ("TCP_KEEPINTVL", 5),
            ("TCP_KEEPCNT", 3),
            ("TCP_USER_TIMEOUT", 20_000),
        ):
            option = getattr(socket, name, None)
            if option is not None:
                try:
                    actual = stream.getsockopt(socket.IPPROTO_TCP, option)
                except OSError as error:
                    if error.errno in (errno.ENOPROTOOPT, errno.EOPNOTSUPP, errno.EINVAL):
                        continue
                    raise
                assert actual == expected


def test_remote_liveness_leaves_unix_streams_untouched() -> None:
    stream = Mock(family=socket.AF_UNIX)
    configure_remote_tcp_liveness(cast(socket.socket, stream))
    stream.setsockopt.assert_not_called()


@pytest.mark.parametrize("error_number", (errno.ENOPROTOOPT, errno.EOPNOTSUPP, errno.EINVAL))
def test_remote_liveness_tolerates_unsupported_kernel_options(error_number: int) -> None:
    stream = Mock(family=socket.AF_INET)
    stream.setsockopt.side_effect = OSError(error_number, "unsupported")
    configure_remote_tcp_liveness(cast(socket.socket, stream))


def test_remote_liveness_does_not_hide_transport_errors() -> None:
    stream = Mock(family=socket.AF_INET)
    stream.setsockopt.side_effect = OSError(errno.EBADF, "closed")
    with pytest.raises(OSError, match="closed"):
        configure_remote_tcp_liveness(cast(socket.socket, stream))


def test_remote_liveness_handles_missing_optional_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "TCP_KEEPIDLE", "TCP_KEEPALIVE", "TCP_KEEPINTVL", "TCP_KEEPCNT", "TCP_USER_TIMEOUT",
    ):
        monkeypatch.delattr(socket, name, raising=False)
    stream = Mock(family=socket.AF_INET6)
    configure_remote_tcp_liveness(cast(socket.socket, stream))
    stream.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)


def test_remote_liveness_uses_macos_idle_option(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(socket, "TCP_KEEPIDLE", raising=False)
    monkeypatch.setattr(socket, "TCP_KEEPALIVE", 0x10, raising=False)
    stream = Mock(family=socket.AF_INET6)
    configure_remote_tcp_liveness(cast(socket.socket, stream))
    stream.setsockopt.assert_any_call(socket.IPPROTO_TCP, 0x10, 10)
