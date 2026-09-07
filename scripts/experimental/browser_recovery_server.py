"""Loopback-only acceptance driver for the actual dashboard/auth server.

Private fictional staging supplied by audit_browser_recovery.mjs. Not a product
entrypoint. Stdin controls test faults; stdout contains readiness/ACKs, no secrets.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import ssl
import sys
from pathlib import Path

import uvicorn

from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_recovery import BrowserDeviceRecovery
from sds200.browser_device_sessions import BrowserDeviceSessions
from sds200.browser_device_store import BrowserDeviceState, BrowserDeviceStore
from sds200.web_auth import WebDashboardAuthentication
from sds200.web_dashboard import create_web_dashboard_app


async def main(root: Path, origin: str) -> None:
    authority = root / "authority"
    authority.mkdir(mode=0o700)
    store = BrowserDeviceStore.initialize(authority / "devices.sqlite")
    device = store.enroll("fixture")
    secret = root / "device.secret"
    fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(device.credential)
    configuration = load_browser_native_configuration(root)
    BrowserDeviceRecovery.initialize(root / "recovery.sqlite", configuration.identity)
    fault = "healthy"
    exchanges = 0

    def no_daemon():
        raise RuntimeError("No scanner daemon in this loopback authentication test")

    def application():
        sessions = BrowserDeviceSessions(store, absolute_seconds=80, idle_seconds=80)
        app = create_web_dashboard_app(
            no_daemon, no_daemon, no_daemon, no_daemon, no_daemon,
            lan_authentication=WebDashboardAuthentication("fictional operator password", origin),
            browser_device_sessions=sessions,
        )

        async def faults(scope, receive, send):
            nonlocal exchanges, fault
            if scope["type"] != "http" or scope["path"] != "/auth/device/session":
                return await app(scope, receive, send)
            exchanges += 1
            selected, fault = fault, "healthy"  # A single interrupted attempt.

            async def interrupted(message):
                if message["type"] == "http.response.body" and message.get("body"):
                    body = message["body"]
                    if selected == "truncated":
                        # Preserve the real declared length; Uvicorn closes this
                        # intentionally incomplete response, not a malformed full JSON body.
                        await send({**message, "body": body[:8]})
                        return
                    if selected == "deadline":
                        for byte in body:
                            await send({"type": "http.response.body", "body": bytes([byte]),
                                        "more_body": True})
                            await asyncio.sleep(0.2)
                        await send({"type": "http.response.body", "body": b"", "more_body": False})
                        return
                await send(message)
            await app(scope, receive, interrupted)
        return faults

    async def start(port):
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.setblocking(False)
        config = uvicorn.Config(application(), log_config=None, log_level="critical",
                                access_log=False, lifespan="on", timeout_graceful_shutdown=3,
                                ssl_certfile=str(root / "server.pem"),
                                ssl_keyfile=str(root / "server.key"))
        config.load()
        config.ssl.minimum_version = ssl.TLSVersion.TLSv1_2
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve(sockets=[listener]))
        for _ in range(500):
            if task.done():
                await task
                raise RuntimeError("Fixture failed to start")
            if server.started:
                return server, task, listener.getsockname()[1]
            await asyncio.sleep(0.01)
        raise RuntimeError("Fixture startup deadline")

    server, task, port = await start(0)
    print(json.dumps({"ready": True, "port": port, "identity": configuration.identity}), flush=True)
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            command = json.loads(line)["action"]
            if command == "stop":
                break
            if command in {"truncated", "deadline"}:
                fault = command
            elif command == "restart":
                server.should_exit = True
                await task
                server, task, _ = await start(port)
            elif command == "revoke":
                store.transition("fixture", BrowserDeviceState.REVOKED)
            elif command != "status":
                raise ValueError("Unknown fixture action")
            print(json.dumps({"action": command, "exchanges": exchanges,
                              "state": store.inventory()[0].state.value}), flush=True)
    finally:
        server.should_exit = True
        await task


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
