#!/usr/bin/env python3
"""Uninstalled finite-web Unix clients, bound to original native actors.

The caller must obtain Expected and the socket directory from a separately
authenticated original Ready/launch plan in this SAME native namespace. Caller
PIDs are not an authorization mechanism. This component neither authenticates
that launch/source/runtime nor replaces the independent init/recovery deadlines.

No listener, scanner request, retry, signal, recording start/stop or restoration.
Native clients retain their existing wire validators and media cleanup. Only
their four fixed Unix destinations are supported. A private monitor shuts down
these client connections on original expiry or lost continuity; it does not
claim that an HTTP worker, guardian or native process has exited.
"""

from __future__ import annotations

import math
import os
import select
import socket
import stat
import struct
import threading
import time
from contextlib import suppress
from pathlib import Path

from supplemental_recording_probe import Expected, _identity

from sds200.daemon_client import DaemonApiClient
from sds200.daemon_event_client import DaemonEventClient
from sds200.daemon_pcmu_client import DaemonPcmuClient
from sds200.daemon_recording_file_client import DaemonRecordingFileClient
from sds200.exceptions import DaemonUnavailableError

MESSAGE = "The original finite dashboard peer is unavailable."
NAMES = ("api.sock", "events.sock", "pcmu.sock", "recordings.sock")
NAMESPACES = ("pid", "mnt", "net", "user", "time")
MAX_CONNECTIONS = 64


def require(value):
    if not value:
        raise DaemonUnavailableError(MESSAGE)


def _file(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _ns(pid):
    result = []
    for name in NAMESPACES:
        info = os.stat(f"/proc/{pid}/ns/{name}")
        result.append((info.st_dev, info.st_ino))
    return tuple(result)


class _Peer(socket.socket):
    """Real socket for the exact I/O methods used by the four native clients.

    Native event/PCMU clients ask for blocking reads. Those remain bounded by
    the ORIGINAL deadline, with independent shutdown waking in-flight reads.
    This is not a general-purpose socket or an HTTP worker-exit witness.
    """

    def __init__(self, owner, raw, timeout):
        self._owner, self._limit = owner, timeout
        super().__init__(socket.AF_UNIX, socket.SOCK_STREAM, fileno=raw.detach())

    def _before(self):
        remaining = self._remaining()
        socket.socket.settimeout(
            self, remaining if self._limit is None else min(self._limit, remaining)
        )

    def _remaining(self):
        try:
            return self._owner.check()
        except DaemonUnavailableError:
            # Preserve the native socket-error cleanup/disconnect path.
            raise OSError(MESSAGE) from None

    def settimeout(self, value):
        require(value is None or type(value) in (int, float) and math.isfinite(value) and value > 0)
        self._limit = value
        self._before()

    def recv(self, size, flags=0):
        self._before()
        result = super().recv(size, flags)
        self._remaining()
        return result

    def sendall(self, data, flags=0):
        self._before()
        super().sendall(data, flags)
        self._remaining()

    def close(self):
        super().close()
        with self._owner._lock:
            self._owner._connections.discard(self)

    def _abort(self):
        # Shutdown wakes native blocking receives before closing this exact
        # socket object. No pathname lookup or externally supplied descriptor.
        with suppress(OSError):
            socket.socket.shutdown(self, socket.SHUT_RDWR)
        socket.socket.close(self)


class _Transport:
    def __init__(self, owner, name):
        require(type(owner) is Peers and name in NAMES)
        self._owner, self._name = owner, name

    def connect(self, *, timeout):
        return self._owner._connect(self._name, timeout)


class Peers:
    """Retained original pidfds, socket inodes, namespaces and one deadline.

    Construct only after actual Ready. Construction opens no data connection;
    each client connect checks kernel SO_PEERCRED BEFORE protocol bytes are sent.
    A failed identity/connection check consumes this instance and closes all its
    streams. New object construction cannot be used to renew a consumed case.
    The later qualified launcher, not this class, must enforce that case rule.
    """

    def __init__(self, expected, directory):
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._connections = set()
        self._handles, self._namespace_handles = [], []
        self._directory_fd = None
        self._thread = None
        self._closed = False
        try:
            require(type(expected) is Expected)
            expected.validate()
            require(type(directory) is type(Path()) and directory.is_absolute())
            require(str(directory) == str(directory.resolve(strict=True)))
            self._pid, self._uid, self._gid = os.getpid(), os.geteuid(), os.getegid()
            self._actors = (expected.guardian, expected.native, expected.watchdog)
            self._deadline = float(expected.deadline)  # Never recomputed from 'now'.
            for actor in self._actors:
                self._handles.append(os.pidfd_open(actor.pid))
            for name in NAMESPACES:
                self._namespace_handles.append(
                    os.open(f"/proc/self/ns/{name}", os.O_RDONLY | os.O_CLOEXEC)
                )
            self._namespaces = tuple(
                (os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in self._namespace_handles
            )
            self._directory = directory
            self._directory_fd = os.open(
                directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
            info = os.fstat(self._directory_fd)
            require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700)
            require((info.st_uid, info.st_gid) == (self._uid, self._gid))
            self._directory_info = _file(info)
            self._socket_info = {}
            for name in NAMES:
                info = os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
                require(stat.S_ISSOCK(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600)
                require((info.st_uid, info.st_gid, info.st_nlink) == (self._uid, self._gid, 1))
                self._socket_info[name] = _file(info)
            self.check()
            self._thread = threading.Thread(target=self._watch, name="finite-web-peer", daemon=True)
            self._thread.start()
        except Exception:
            self.close()
            raise DaemonUnavailableError(MESSAGE) from None

    def _check_locked(self):
        require(not self._closed)
        require((os.getpid(), os.geteuid(), os.getegid()) == (self._pid, self._uid, self._gid))
        require(time.monotonic() < self._deadline)
        require(not select.select(self._handles, [], [], 0)[0])
        parents = [_identity(actor) for actor in self._actors]
        require(parents[1:] == [self._actors[0].pid] * 2)
        for pid in (self._pid, *(actor.pid for actor in self._actors)):
            require(_ns(pid) == self._namespaces)
        require(_file(os.fstat(self._directory_fd)) == self._directory_info)
        require(_file(self._directory.lstat()) == self._directory_info)
        for name, original in self._socket_info.items():
            require(
                _file(os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)) == original
            )
        require(not select.select(self._handles, [], [], 0)[0])
        remaining = self._deadline - time.monotonic()
        require(remaining > 0)
        return remaining

    def check(self):
        with self._lock:
            try:
                return self._check_locked()
            except Exception:
                self._close_locked()
                raise DaemonUnavailableError(MESSAGE) from None

    def _connect(self, name, timeout):
        raw = peer = None
        with self._lock:
            try:
                require(name in NAMES)
                require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0)
                remaining = self._check_locked()
                require(len(self._connections) < MAX_CONNECTIONS)
                raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                raw.settimeout(min(float(timeout), remaining, 0.5))
                # Retained directory fd excludes a pathname-ancestor swap.
                raw.connect(f"/proc/self/fd/{self._directory_fd}/{name}")
                actual = struct.unpack(
                    "3i", raw.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                )
                native = self._actors[1]
                require(actual == (native.pid, native.uid, native.gid))
                require(not raw.get_inheritable())
                self._check_locked()
                peer = _Peer(self, raw, float(timeout))
                self._connections.add(peer)
                peer.settimeout(timeout)
                return peer
            except Exception:
                if peer is not None:
                    peer._abort()
                if raw is not None:
                    raw.close()
                self._close_locked()
                raise DaemonUnavailableError(MESSAGE) from None

    def _watch(self):
        try:
            while not self._stop.is_set():
                remaining = self.check()
                self._stop.wait(min(0.1, remaining))
        except Exception:
            with self._lock:
                self._close_locked()

    def _close_locked(self):
        self._closed = True
        self._stop.set()
        for peer in self._connections:
            peer._abort()
        self._connections.clear()
        for handles in (self._handles, self._namespace_handles):
            while handles:
                os.close(handles.pop())
        if self._directory_fd is not None:
            os.close(self._directory_fd)
            self._directory_fd = None

    def close(self):
        with self._lock:
            self._close_locked()
        if (
            self._thread is not None
            and self._thread.ident is not None
            and self._thread is not threading.current_thread()
        ):
            self._thread.join(timeout=1)
            require(not self._thread.is_alive())

    def api(self):
        self.check()
        return DaemonApiClient(_Transport(self, "api.sock"), timeout=0.5)

    def events(self):
        self.check()
        return DaemonEventClient(_Transport(self, "events.sock"), timeout=0.5)

    def pcmu(self):
        self.check()
        return DaemonPcmuClient(_Transport(self, "pcmu.sock"), timeout=0.5)

    def recordings(self):
        self.check()
        return DaemonRecordingFileClient(_Transport(self, "recordings.sock"), timeout=0.5)


if __name__ == "__main__":
    raise SystemExit("Uninstalled finite WebUI peer component; no listener or launch enabled.")
