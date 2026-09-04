"""Detect lost remote TCP peers without timing out quiet scanner streams.

Apply before TLS wrapping on both ends. These are per-connection settings, not
system-wide sysctls. No application heartbeat or wire-format change is needed.
Platforms lacking a tuning option retain their supported TCP defaults.
"""

from __future__ import annotations

import errno
import socket

REMOTE_KEEPALIVE_IDLE_SECONDS = 10
REMOTE_KEEPALIVE_INTERVAL_SECONDS = 5
REMOTE_KEEPALIVE_PROBES = 3
REMOTE_USER_TIMEOUT_MS = 20_000


def configure_remote_tcp_liveness(stream: socket.socket) -> None:
    """Bound silent TCP failure detection where the OS supports tuning.

    Linux's user timeout also bounds unacknowledged buffered writes. Keepalive
    handles quiet receive-only connections. Neither imposes an application-data
    deadline: a healthy idle peer continues answering TCP probes indefinitely.
    Unix sockets (including local daemon connections) are deliberately untouched.
    """

    if stream.family not in (socket.AF_INET, socket.AF_INET6):
        return
    _set_option(stream, socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    idle_option = getattr(socket, "TCP_KEEPIDLE", None)
    if idle_option is None:
        # macOS uses TCP_KEEPALIVE for the idle interval.
        idle_option = getattr(socket, "TCP_KEEPALIVE", None)
    for option, value in (
        (idle_option, REMOTE_KEEPALIVE_IDLE_SECONDS),
        (getattr(socket, "TCP_KEEPINTVL", None), REMOTE_KEEPALIVE_INTERVAL_SECONDS),
        (getattr(socket, "TCP_KEEPCNT", None), REMOTE_KEEPALIVE_PROBES),
        (getattr(socket, "TCP_USER_TIMEOUT", None), REMOTE_USER_TIMEOUT_MS),
    ):
        if option is not None:
            _set_option(stream, socket.IPPROTO_TCP, option, value)


def _set_option(stream: socket.socket, level: int, option: int, value: int) -> None:
    try:
        stream.setsockopt(level, option, value)
    except OSError as error:
        # A constant can exist in Python even if the running kernel does not
        # implement it. Other errors (e.g. a closed socket) must still propagate.
        if error.errno not in (errno.ENOPROTOOPT, errno.EOPNOTSUPP, errno.EINVAL):
            raise
