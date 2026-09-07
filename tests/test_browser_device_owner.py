from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import socket
import struct
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sds200.browser_device_owner as owner_module
from sds200.browser_device_admin import BrowserAdminStatus, BrowserDeviceAdmin
from sds200.browser_device_http import BrowserDeviceHTTP
from sds200.browser_device_owner import (
    BrowserDeviceOwner,
    BrowserOwnerError,
    request_browser_owner_ack,
)
from sds200.browser_device_sessions import BrowserDeviceSessions
from sds200.browser_device_store import BrowserDeviceState, BrowserDeviceStore
from sds200.web_auth import WebDashboardAuthentication

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux owner channel")


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "authority"
    root.mkdir(mode=0o700)
    return BrowserDeviceStore.initialize(root / "devices.sqlite")


def _child_owner(path, credential, pipe):
    async def run():
        store = BrowserDeviceStore(Path(path))
        sessions = BrowserDeviceSessions(store)
        issued = sessions.issue("display", credential)
        assert issued is not None
        lease = sessions.acquire(issued.token)
        assert lease is not None
        owner = BrowserDeviceOwner(store)
        owner.acquire()
        await owner.start(lambda record: asyncio.to_thread(sessions.acknowledge,
                                                           record, timeout=0.05))
        pipe.send("ready")
        try:
            while True:
                command = await asyncio.to_thread(pipe.recv)
                if command == "release":
                    lease.release()
                    pipe.send("released")
                elif command == "stop":
                    break
        finally:
            lease.release()
            sessions.close()
            await owner.stop()
            owner.release()
    asyncio.run(run())


def test_separate_process_ack_refuses_until_old_request_finishes(store) -> None:
    device = store.enroll("display")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_child_owner, args=(store.path, device.credential, child))
    process.start()
    try:
        assert parent.poll(5) and parent.recv() == "ready"
        competitor = BrowserDeviceOwner(store)
        with pytest.raises(BrowserOwnerError):
            competitor.acquire()
        admin = BrowserDeviceAdmin(store)
        result = admin.transition(device.record, BrowserDeviceState.PAUSED)
        assert result.status is BrowserAdminStatus.PENDING
        paused = result.record
        first = result.receipt
        assert first is not None
        assert first.owner_pid == process.pid != os.getpid()
        assert not first.completed
        parent.send("release")
        assert parent.poll(2) and parent.recv() == "released"
        result = admin.confirm(paused)
        assert result.status is BrowserAdminStatus.CONFIRMED
        second = result.receipt
        assert second is not None
        assert second.completed and second.owner_id == first.owner_id
        store.transition("display", BrowserDeviceState.ACTIVE)
        assert not request_browser_owner_ack(store.path, paused).completed
        parent.send("stop")
        process.join(5)
        assert process.exitcode == 0
        with pytest.raises(BrowserOwnerError):
            request_browser_owner_ack(store.path, paused)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
        child.close()


def test_crashed_owner_lock_and_stale_socket_recover(store) -> None:
    device = store.enroll("display")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_child_owner, args=(store.path, device.credential, child))
    process.start()
    try:
        assert parent.poll(5) and parent.recv() == "ready"
        record = store.transition("display", BrowserDeviceState.PAUSED)
        old = request_browser_owner_ack(store.path, record)
        process.terminate()  # Deliberate crash: no socket or lock cleanup runs in child.
        process.join(5)
        with pytest.raises(BrowserOwnerError):
            request_browser_owner_ack(store.path, record)

        async def restart():
            owner = BrowserDeviceOwner(store)
            sessions = BrowserDeviceSessions(store)
            owner.acquire()
            await owner.start(lambda record: asyncio.to_thread(sessions.acknowledge, record))
            try:
                receipt = await asyncio.to_thread(request_browser_owner_ack, store.path, record)
                assert receipt.completed and receipt.owner_id != old.owner_id
                assert receipt.owner_pid == os.getpid()
            finally:
                sessions.close()
                await owner.stop()
                owner.release()
        asyncio.run(restart())
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
        child.close()


@pytest.mark.parametrize("kind", ["file", "symlink", "wrong-mode"])
def test_unsafe_socket_target_is_preserved(store, kind) -> None:
    async def check():
        owner = BrowserDeviceOwner(store)
        owner.acquire()
        target = store.path.parent / (owner._base + ".sock")
        if kind == "file":
            target.write_text("keep this file")
            target.chmod(0o600)
        elif kind == "symlink":
            target.symlink_to(store.path)
        else:
            connection = socket.socket(socket.AF_UNIX)
            connection.bind(f"/proc/self/fd/{owner._directory_fd}/{target.name}")
            connection.close()
            target.chmod(0o666)
        try:
            with pytest.raises(BrowserOwnerError):
                await owner.start(lambda record: asyncio.sleep(0, result=True))
            assert target.exists()
            if kind == "file":
                assert target.read_text() == "keep this file"
        finally:
            await owner.stop()
            owner.release()
    asyncio.run(check())


@pytest.mark.parametrize("kind", ["symlink", "mode", "hardlink"])
def test_unsafe_lock_refused(store, kind) -> None:
    owner = BrowserDeviceOwner(store)
    target = store.path.parent / (owner._base + ".lock")
    if kind == "symlink":
        target.symlink_to(store.path)
    elif kind == "hardlink":
        os.link(store.path, target)
    else:
        target.write_text("do not modify")
        target.chmod(0o644)
    with pytest.raises(BrowserOwnerError):
        owner.acquire()
    assert target.exists()


@pytest.mark.parametrize("body", [
    b'{"version":1,"version":1}', b'{"version":true}', b'[]',
    b'{"action":"rotate"}', b'\xff', b'x' * 1025,
    *[json.dumps({"version": 1, "action": "acknowledge", "request_id": "a" * 32,
                  "device_id": "display", "generation": 2, "state": "paused", **invalid}).encode()
      for invalid in ({"generation": True}, {"generation": 0}, {"state": "invalid"},
                      {"request_id": "bad"}, {"role": "operator"})],
])
def test_bad_control_requests_do_not_call_barrier(store, body) -> None:
    async def check():
        called = False

        async def barrier(record):
            nonlocal called
            called = True
            return True

        owner = BrowserDeviceOwner(store)
        owner.acquire()
        await owner.start(barrier)
        try:
            address = f"/proc/self/fd/{owner._directory_fd}/{owner._base}.sock"
            reader, writer = await asyncio.open_unix_connection(address)
            writer.write(struct.pack("!I", len(body)) + body)
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 1) == b""
            writer.close()
            await writer.wait_closed()
            assert not called
        finally:
            await owner.stop()
            owner.release()
    asyncio.run(check())


def test_stalled_control_input_is_bounded(store, monkeypatch) -> None:
    monkeypatch.setattr(owner_module, "_TIMEOUT", 0.02)

    async def check():
        owner = BrowserDeviceOwner(store)
        owner.acquire()
        await owner.start(lambda record: asyncio.sleep(0, result=True))
        try:
            reader, writer = await asyncio.open_unix_connection(
                f"/proc/self/fd/{owner._directory_fd}/{owner._base}.sock")
            writer.write(b"\x00")
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 1) == b""
            writer.close()
            await writer.wait_closed()
        finally:
            await owner.stop()
            owner.release()
    asyncio.run(check())


def test_closed_owner_validation_fails_and_lock_inode_is_kept(store) -> None:
    owner = BrowserDeviceOwner(store)
    owner.acquire()
    target = store.path.parent / (owner._base + ".lock")
    inode = target.stat().st_ino
    owner.release()
    with pytest.raises(BrowserOwnerError):
        owner.validate()
    replacement = BrowserDeviceOwner(store)
    replacement.acquire()
    assert target.stat().st_ino == inode
    replacement.release()


def test_http_shutdown_keeps_owner_lock_when_drain_is_unconfirmed(store, monkeypatch) -> None:
    sessions = BrowserDeviceSessions(store)
    auth = WebDashboardAuthentication("fictional operator password", "https://192.0.2.1:8443")
    wrapper = BrowserDeviceHTTP(FastAPI(), authentication=auth, devices=sessions)
    try:
        with (pytest.raises(BrowserOwnerError),
              TestClient(wrapper, base_url="https://192.0.2.1:8443")):
            monkeypatch.setattr(sessions, "drain_closed", lambda: False)
        with pytest.raises(BrowserOwnerError):
            BrowserDeviceOwner(store).acquire()
    finally:
        wrapper._owner.release()  # Fixture has no actual requests; release its intentional guard.


def test_client_checks_peer_uid(store, monkeypatch) -> None:
    class WrongPeer:
        def getsockopt(self, *args):
            return struct.pack("3i", 123, os.geteuid() + 1, 0)
    with pytest.raises(BrowserOwnerError):
        owner_module._peer(WrongPeer())


@pytest.mark.parametrize("invalid", [{"request_id": "b" * 32}, {"version": True},
                                    {"completed": "true"}, {"owner_id": "invalid"}])
def test_client_rejects_invalid_or_mismatched_receipts(store, monkeypatch, invalid) -> None:
    record = store.enroll("display").record
    original = owner_module._frame

    def corrupt_response(value):
        return original({**value, **invalid} if "completed" in value else value)

    monkeypatch.setattr(owner_module, "_frame", corrupt_response)

    async def check():
        owner = BrowserDeviceOwner(store)
        owner.acquire()
        await owner.start(lambda record: asyncio.sleep(0, result=True))
        try:
            with pytest.raises(BrowserOwnerError):
                await asyncio.to_thread(request_browser_owner_ack, store.path, record)
        finally:
            await owner.stop()
            owner.release()
    asyncio.run(check())


def test_control_handlers_are_bounded(store) -> None:
    async def check():
        owner = BrowserDeviceOwner(store)
        owner.acquire()
        await owner.start(lambda record: asyncio.sleep(0, result=True))
        connections = []
        try:
            address = f"/proc/self/fd/{owner._directory_fd}/{owner._base}.sock"
            for _ in range(2):
                connections.append(await asyncio.open_unix_connection(address))
            for _ in range(100):
                if len(owner._tasks) == 2:
                    break
                await asyncio.sleep(0.001)
            assert len(owner._tasks) == 2
            reader, writer = await asyncio.open_unix_connection(address)
            connections.append((reader, writer))
            assert await asyncio.wait_for(reader.read(), 1) == b""
        finally:
            for _, writer in connections:
                writer.close()
                await writer.wait_closed()
            await owner.stop()
            owner.release()
    asyncio.run(check())
