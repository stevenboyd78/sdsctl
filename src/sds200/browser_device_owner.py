"""Linux-only private web-owner lock and acknowledgement socket (experimental).

The socket only confirms a committed record; it cannot mutate authority or issue
credentials. Same-UID/root processes are trusted. Do not replace the authority
directory/lock file while a service owns it. No production launcher enables this.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import struct
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .browser_device_store import (
    BrowserDeviceBinding,
    BrowserDeviceRecord,
    BrowserDeviceState,
    BrowserDeviceStore,
)

_MAX = 1024
_TIMEOUT = 8


class BrowserOwnerError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Browser-device web owner is unavailable or unsafe.")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _decode(body: bytes) -> dict[str, Any]:
    if not 0 < len(body) <= _MAX:
        raise ValueError()
    value = json.loads(body.decode("utf-8"), object_pairs_hook=_object)
    if type(value) is not dict:
        raise ValueError()
    return value


def _frame(value: dict[str, Any]) -> bytes:
    body = json.dumps(value, separators=(",", ":")).encode("utf-8")
    if not 0 < len(body) <= _MAX:
        raise ValueError()
    return struct.pack("!I", len(body)) + body


def _record(value: dict[str, Any]) -> BrowserDeviceRecord:
    binding = BrowserDeviceBinding(value["device_id"], value["generation"])
    return BrowserDeviceRecord(binding.device_id, binding.generation,
                               BrowserDeviceState(value["state"]))


def _peer(connection: socket.socket) -> int:
    pid, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET,
                                                           socket.SO_PEERCRED, 12))
    if uid != os.geteuid():
        raise BrowserOwnerError()
    return int(pid)


def _name(path: Path) -> str:
    return "web-" + hashlib.sha256(path.name.encode()).hexdigest()[:16]


def _private(info: os.stat_result, kind: str) -> None:
    check = stat.S_ISSOCK if kind == "socket" else stat.S_ISREG
    if (not check(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise BrowserOwnerError()


def _directory(store: BrowserDeviceStore) -> int:
    if not sys.platform.startswith("linux"):
        raise BrowserOwnerError()
    store._check()
    return os.open(store.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


@dataclass(frozen=True, slots=True)
class BrowserOwnerReceipt:
    record: BrowserDeviceRecord
    owner_id: str
    owner_pid: int
    completed: bool


class BrowserDeviceOwner:
    def __init__(self, store: BrowserDeviceStore) -> None:
        self._store = store
        self._directory_fd = -1
        self._lock_fd = -1
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._socket_inode: tuple[int, int] | None = None
        self._epoch = secrets.token_hex(16)
        self._base = _name(store.path)
        self._pid = os.getpid()
        self._accepting = False

    def acquire(self) -> None:
        """Acquire one persistent lock per authority; never unlink the lock file."""
        if self._lock_fd != -1:
            raise BrowserOwnerError()
        try:
            import fcntl

            self._epoch = secrets.token_hex(16)
            self._directory_fd = _directory(self._store)
            self._lock_fd = os.open(self._base + ".lock",
                                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    0o600, dir_fd=self._directory_fd)
            _private(os.fstat(self._lock_fd), "file")
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.validate()
        except Exception:
            self.release()
            raise BrowserOwnerError() from None

    def validate(self) -> None:
        try:
            self._validate()
        except Exception:
            raise BrowserOwnerError() from None

    def _validate(self) -> None:
        if self._lock_fd == -1 or self._pid != os.getpid():
            raise BrowserOwnerError()
        self._store._check()
        for actual, expected in (
            (os.fstat(self._directory_fd), self._store.path.parent.stat()),
            (os.fstat(self._lock_fd), os.stat(self._base + ".lock", dir_fd=self._directory_fd,
                                             follow_symlinks=False)),
        ):
            if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise BrowserOwnerError()
        _private(os.fstat(self._lock_fd), "file")

    async def start(self, acknowledge: Callable[[BrowserDeviceRecord], Awaitable[bool]]) -> None:
        self.validate()
        name = self._base + ".sock"
        try:
            stale = os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _private(stale, "socket")
            os.unlink(name, dir_fd=self._directory_fd)  # Only stale socket under held owner lock.
        # /proc/self/fd avoids AF_UNIX's short pathname limit without changing process cwd.
        address = f"/proc/self/fd/{self._directory_fd}/{name}"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(address)
            os.chmod(name, 0o600, dir_fd=self._directory_fd, follow_symlinks=False)
            info = os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
            self._socket_inode = (info.st_dev, info.st_ino)
            listener.listen(2)
            listener.setblocking(False)

            async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                task = asyncio.current_task()
                assert task is not None
                if not self._accepting or len(self._tasks) >= 2:
                    writer.close()
                    await writer.wait_closed()
                    return
                self._tasks.add(task)
                try:
                    async with asyncio.timeout(_TIMEOUT):
                        self.validate()
                        _peer(writer.get_extra_info("socket"))
                        size = struct.unpack("!I", await reader.readexactly(4))[0]
                        if not 0 < size <= _MAX:
                            raise ValueError()
                        value = _decode(await reader.readexactly(size))
                        if (set(value) != {"version", "action", "request_id", "device_id",
                                          "generation", "state"}
                                or type(value["version"]) is not int or value["version"] != 1
                                or value["action"] != "acknowledge"
                                or type(value["request_id"]) is not str
                                or re.fullmatch("[a-f0-9]{32}", value["request_id"]) is None):
                            raise ValueError()
                        record = _record(value)
                        completed = await acknowledge(record)
                        self.validate()
                        writer.write(_frame({"version": 1, "request_id": value["request_id"],
                                             "owner_id": self._epoch, "completed": completed}))
                        await writer.drain()
                except Exception:
                    pass  # Fixed closed-channel failure; never log request contents.
                finally:
                    self._tasks.discard(task)
                    writer.close()
                    await writer.wait_closed()
            self._accepting = True
            self._server = await asyncio.start_unix_server(handle, sock=listener, limit=_MAX + 4)
        except BaseException:
            listener.close()
            raise

    async def stop(self) -> None:
        """Stop control connections. Release lock separately only after HTTP requests drain."""
        self._accepting = False
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._directory_fd != -1 and self._socket_inode is not None:
            try:
                name = self._base + ".sock"
                info = os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
                if (info.st_dev, info.st_ino) == self._socket_inode:
                    os.unlink(name, dir_fd=self._directory_fd)
            except FileNotFoundError:
                pass

    def release(self) -> None:
        for name in ("_lock_fd", "_directory_fd"):
            descriptor = getattr(self, name)
            if descriptor != -1:
                os.close(descriptor)
                setattr(self, name, -1)


def request_browser_owner_ack(path: Path, record: BrowserDeviceRecord) -> BrowserOwnerReceipt:
    """Admin-process client: check live owner's response; never assume success on failure."""
    directory_fd = -1
    try:
        store = BrowserDeviceStore(path)
        directory_fd = _directory(store)
        name = _name(path) + ".sock"
        _private(os.stat(name, dir_fd=directory_fd, follow_symlinks=False), "socket")
        request_id = secrets.token_hex(16)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            deadline = time.monotonic() + _TIMEOUT + 1

            def remaining() -> None:
                seconds = deadline - time.monotonic()
                if seconds <= 0:
                    raise TimeoutError()
                connection.settimeout(seconds)

            remaining()
            connection.connect(f"/proc/self/fd/{directory_fd}/{name}")
            pid = _peer(connection)
            request = {"version": 1, "action": "acknowledge", "request_id": request_id,
                       "device_id": record.device_id, "generation": record.generation,
                       "state": record.state.value}
            _record(request)
            remaining()
            connection.sendall(_frame(request))

            def read(count: int) -> bytes:
                result = bytearray()
                while len(result) < count:
                    remaining()
                    chunk = connection.recv(count - len(result))
                    if not chunk:
                        raise ValueError()
                    result.extend(chunk)
                return bytes(result)

            size = struct.unpack("!I", read(4))[0]
            if not 0 < size <= _MAX:
                raise ValueError()
            value = _decode(read(size))
            if (set(value) != {"version", "request_id", "owner_id", "completed"}
                    or type(value["version"]) is not int or value["version"] != 1
                    or value["request_id"] != request_id or type(value["completed"]) is not bool
                    or type(value["owner_id"]) is not str
                    or re.fullmatch("[a-f0-9]{32}", value["owner_id"]) is None):
                raise ValueError()
            return BrowserOwnerReceipt(record, value["owner_id"], pid, value["completed"])
    except Exception:
        raise BrowserOwnerError() from None
    finally:
        if directory_fd != -1:
            os.close(directory_fd)
