#!/usr/bin/env python3
"""Join finite HTTP scope to original native peers; no executable/listener.

This is not the source-qualified launcher or independent process/recovery
supervisor. Cancellation and closed client sockets are NOT worker/process exit
proof. The launcher still must use the unchanged original finite lifetime and
separately qualify exact web/native exits before restoring any scanner owner.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack

from fastapi.responses import JSONResponse
from supplemental_recording_web_peer import Peers
from supplemental_recording_web_scope import Factory

from sds200.exceptions import DaemonUnavailableError

MESSAGE = "The finite test dashboard is no longer available."


class Service:
    """Exactly the four bound native factories behind the closed route gate.

    HTTP requests and outgoing response chunks recheck original continuity.
    The original deadline/peer monitor also ends a blocked body receive or an
    in-flight native stream; request activity never renews this lifetime.
    Already delivered bytes cannot be withdrawn. A truncated streaming response
    is closed, not rewritten into a successful final chunk.
    """

    def __init__(self, peers, *, home_assistant_ingress=False, lan_authentication=None):
        if type(peers) is not Peers:
            raise ValueError(MESSAGE)
        self._peers = peers
        self._active = set()
        try:
            peers.check()
            self._surface = Factory()(
                peers.api,
                peers.events,
                peers.pcmu,
                peers.recordings,
                home_assistant_ingress=home_assistant_ingress,
                lan_authentication=lan_authentication,
            )
        except Exception:
            peers.close()
            raise ValueError(MESSAGE) from None

    async def _unavailable(self):
        try:
            while True:
                remaining = self._peers.check()
                await asyncio.sleep(min(0.1, remaining))
        except DaemonUnavailableError:
            return

    def _finished(self, task):
        self._active.discard(task)
        if not task.cancelled():
            task.exception()

    async def _reject(self, scope, receive, send, started):
        if started:
            # An ASGI error after headers forces server transport closure.
            # Sending a normal empty final body would falsely complete a stream.
            raise RuntimeError(MESSAGE)
        await JSONResponse(
            {"detail": MESSAGE},
            status_code=503,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )(scope, receive, send)

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind == "lifespan":
            try:
                self._peers.check()
                await self._surface(scope, receive, send)
            finally:
                self._peers.close()
            return
        if kind != "http":
            # The scope gate always rejects WebSockets and unknown scope types.
            await self._surface(scope, receive, send)
            return
        with ExitStack() as cleanup:
            try:
                ticket = cleanup.enter_context(self._peers.request_scope())
            except DaemonUnavailableError:
                await self._reject(scope, receive, send, False)
                return
            await self._http(scope, receive, send, ticket)

    async def _http(self, scope, receive, send, ticket):
        started = False
        disconnected = False

        async def guarded_receive():
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
                self._peers.release_request(ticket)
            return message

        async def guarded_send(message):
            nonlocal started
            if disconnected:
                raise asyncio.CancelledError
            self._peers.check_request(ticket)
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        native = asyncio.create_task(self._surface(scope, guarded_receive, guarded_send))
        watch = asyncio.create_task(self._unavailable())
        self._active.add(native)
        native.add_done_callback(self._finished)
        try:
            done, _ = await asyncio.wait((native, watch), return_when=asyncio.FIRST_COMPLETED)
            if native in done:
                try:
                    await native
                except asyncio.CancelledError:
                    if not disconnected:
                        raise
                except DaemonUnavailableError:
                    await self._reject(scope, receive, send, started)
            else:
                await self._reject(scope, receive, send, started)
        finally:
            # Wake exactly this request's blocked socket I/O BEFORE awaiting
            # native cancellation/finalizers (which may wait on a read lock).
            self._peers.release_request(ticket)
            native.cancel()
            watch.cancel()
            # Bound cleanup without equating cancellation with actual worker
            # exit. A refusing task remains retained; native peers are revoked.
            done, pending = await asyncio.wait((native, watch), timeout=0.5)
            for task in done:
                if not task.cancelled():
                    task.exception()
            if pending:
                self._peers.close()
                raise RuntimeError("Finite dashboard task cleanup is unconfirmed.")


if __name__ == "__main__":
    raise SystemExit("Uninstalled finite WebUI service only; no listener or launch enabled.")
