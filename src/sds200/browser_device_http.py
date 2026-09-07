"""Opt-in experimental ASGI adapter; not enabled by any production launcher.

Native credential exchange is distinct from cookie/form authentication. Operator
and manual-display requests still use the existing authentication middleware.
Enrolled-device cookies never select operator access, even when cookies conflict.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .browser_device_owner import BrowserDeviceOwner, BrowserOwnerError
from .browser_device_sessions import (
    BrowserDeviceSessions,
    BrowserSessionEnded,
    BrowserSessionLease,
    BrowserSessionUnavailable,
    run_browser_session_request,
)
from .browser_device_store import BrowserDeviceStore
from .web_auth import (
    _DISPLAY_READ_PATHS,
    WEB_DASHBOARD_AUTH_COOKIE,
    WEB_DASHBOARD_LOGOUT_PATH,
    WEB_DASHBOARD_SESSION_PATH,
    WebDashboardAuthentication,
    WebDashboardAuthenticationMiddleware,
    _fetch_site_allowed,
    _origin_matches,
    _request_origin,
    _secure_message,
    _secure_response,
)

BROWSER_DEVICE_COOKIE = "__Host-sdsctl-device-session"
BROWSER_DEVICE_EXCHANGE_PATH = "/auth/device/session"
BROWSER_DEVICE_DISPLAY_PATH = "/device-display"
_BODY_TIMEOUT_SECONDS = 3
_Result = TypeVar("_Result")


class _Busy(Exception):
    pass


class _Workers:
    """Two running jobs, no waiting queue. Cancellation never frees a live job slot."""

    def __init__(self) -> None:
        self._slots = threading.BoundedSemaphore(2)
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="browser-device")
        self._closed = False

    async def run(
        self, operation: Callable[[], _Result],
        abandoned: Callable[[_Result], None] | None = None,
    ) -> _Result:
        if self._closed or not self._slots.acquire(blocking=False):
            raise _Busy()
        try:
            future = self._pool.submit(operation)
        except RuntimeError:
            self._slots.release()
            raise _Busy() from None
        future.add_done_callback(lambda _: self._slots.release())
        wrapped = asyncio.wrap_future(future)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            # A cancelled request must not leave an acquired lease or issued token.
            def finish(done: Future[_Result]) -> None:
                if not done.cancelled() and done.exception() is None and abandoned is not None:
                    abandoned(done.result())
            future.add_done_callback(finish)
            wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            raise

    def close(self) -> None:
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=False)


class _Admission:
    """Bound all exchange attempts before body reads and database work.

    Raw ASGI peer only, never a forwarded header. A shared proxy shares its budget.
    This bounds work, not availability against a distributed denial of service.
    """

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._global: deque[float] = deque()
        self._peers: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def admit(self, peer: str) -> bool:
        with self._lock:
            now = self._clock()
            cutoff = now - 60
            while self._global and self._global[0] <= cutoff:
                self._global.popleft()
            for key, values in tuple(self._peers.items()):
                while values and values[0] <= cutoff:
                    values.popleft()
                if not values:
                    del self._peers[key]
            if len(self._global) >= 60 or len(self._peers.get(peer, ())) >= 5:
                return False
            if peer not in self._peers and len(self._peers) >= 256:
                return False
            self._global.append(now)
            self._peers.setdefault(peer, deque()).append(now)
            return True


def _cookies(headers: Headers) -> tuple[str | None, bool]:
    """Return device value and conflict flag; malformed auth cookies fail closed."""
    values: dict[str, list[str]] = {BROWSER_DEVICE_COOKIE: [], WEB_DASHBOARD_AUTH_COOKIE: []}
    raw = headers.getlist("cookie")
    if sum(map(len, raw)) > 8192:
        return None, True
    for header in raw:
        for pair in header.split(";"):
            key, sep, value = pair.strip().partition("=")
            if key in values:
                values[key].append(value if sep else "")
    device, manual = values[BROWSER_DEVICE_COOKIE], values[WEB_DASHBOARD_AUTH_COOKIE]
    conflict = (len(device) > 1 or len(manual) > 1 or bool(device and manual)
                or bool(device and not device[0]) or bool(manual and not manual[0]))
    return device[0] if device else None, conflict


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


async def _body(receive: Receive) -> bytes:
    value = bytearray()
    while True:
        message = await receive()
        if message["type"] != "http.request":
            raise ValueError()
        value.extend(message.get("body", b""))
        if len(value) > 1024:
            raise ValueError()
        if not message.get("more_body", False):
            return bytes(value)


class BrowserDeviceHTTP:
    def __init__(
        self, app: ASGIApp, *, authentication: WebDashboardAuthentication,
        devices: BrowserDeviceSessions, display_theme_paths: frozenset[str] = frozenset(),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._app = app
        self._manual = WebDashboardAuthenticationMiddleware(
            app, authentication=authentication, display_theme_paths=display_theme_paths,
        )
        self._origin = authentication.origin
        self._devices = devices
        self._paths = _DISPLAY_READ_PATHS | display_theme_paths
        self._workers = _Workers()
        self._admission = _Admission(clock)
        self._body_slots = threading.BoundedSemaphore(2)
        self._owner = BrowserDeviceOwner(BrowserDeviceStore(devices.authority_path))
        self._ready = False

    async def _watch_owner(self) -> None:
        try:
            while True:
                self._owner.validate()
                await asyncio.sleep(1)
        finally:
            self._ready = False
            self._devices.close()

    async def _error(self, code: int, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope["path"] == BROWSER_DEVICE_DISPLAY_PATH and scope["method"] == "GET"
                and not scope.get("query_string") and code in {401, 503}):
            # This dedicated entry never selects manual/operator login, even if
            # a cookie expires between extension readiness and navigation.
            waiting = HTMLResponse(
                "<!doctype html><html lang='en'><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<meta http-equiv='refresh' content='5'>"
                "<title>SDSCTL managed display waiting</title><h1>Waiting for a device session</h1>"
                "<p>This entry requires a valid enrolled display session. It will retry. "
                "If automatic sign-in is paused or blocked, ask your administrator to review "
                "the managed startup screen.</p></html>", status_code=code,
            )
            _secure_response(waiting)
            await waiting(scope, receive, send)
            return
        response = JSONResponse({"detail": "Browser-device request denied."}, status_code=code)
        if code in {429, 503}:
            response.headers["Retry-After"] = "60" if code == 429 else "5"
        _secure_response(response)
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            self._owner.acquire()
            monitors: list[asyncio.Task[None]] = []
            shutdown_complete = False

            async def lifespan_send(message: Message) -> None:
                nonlocal shutdown_complete
                if message["type"] == "lifespan.shutdown.complete":
                    shutdown_complete = True  # Delay success until owner cleanup/drain finishes.
                else:
                    await send(message)
            try:
                await self._owner.start(lambda record: self._workers.run(
                    lambda: self._devices.acknowledge(record),
                ))
                monitors = [asyncio.create_task(self._devices.run_reconciler()),
                            asyncio.create_task(self._watch_owner())]
                self._ready = True
                await self._manual(scope, receive, lifespan_send)
            finally:
                self._ready = False
                self._devices.close()
                for monitor in monitors:
                    monitor.cancel()
                try:
                    await asyncio.gather(*monitors, return_exceptions=True)
                    await self._owner.stop()
                    if not await asyncio.to_thread(self._devices.drain_closed):
                        # Retain the OS lock until process exit rather than permit split ownership.
                        raise BrowserOwnerError()
                    self._owner.release()
                except Exception:
                    if shutdown_complete:
                        await send({"type": "lifespan.shutdown.failed",
                                    "message": "Browser-device owner shutdown was not confirmed."})
                    raise
                finally:
                    self._workers.close()
            if shutdown_complete:
                await send({"type": "lifespan.shutdown.complete"})
            return
        if scope["type"] != "http":
            await self._manual(scope, receive, send)
            return
        try:
            if not self._ready:
                raise BrowserOwnerError()
            self._owner.validate()
        except Exception:
            self._ready = False
            self._devices.close()
            await self._error(503, scope, receive, send)
            return
        headers = Headers(scope=scope)
        if _request_origin(scope, headers) != self._origin:
            await self._error(400, scope, receive, send)
            return
        token, conflict = _cookies(headers)
        if conflict:
            await self._error(400, scope, receive, send)
            return
        if scope["path"] == BROWSER_DEVICE_EXCHANGE_PATH:
            await self._exchange(scope, receive, send, headers)
            return
        managed = scope["path"] == BROWSER_DEVICE_DISPLAY_PATH
        if managed and (scope["method"] != "GET" or scope.get("query_string")
                        or headers.getlist("authorization")):
            await self._error(403, scope, receive, send)
            return
        if managed and not _fetch_site_allowed(headers):
            # An extension-origin navigation is cross-site. Never serve private
            # data on that request, even if it carries a valid device cookie.
            # Only a top-level navigation may load this public waiting shell;
            # its same-origin refresh must pass all normal authorization checks.
            navigation = (headers.getlist("sec-fetch-site") == ["cross-site"]
                          and headers.getlist("sec-fetch-mode") == ["navigate"]
                          and headers.getlist("sec-fetch-dest") == ["document"])
            await self._error(401 if navigation else 403, scope, receive, send)
            return
        if token is None:
            if managed:
                await self._error(401, scope, receive, send)
                return
            await self._manual(scope, receive, send)
            return
        logout = scope["path"] == WEB_DASHBOARD_LOGOUT_PATH and scope["method"] == "POST"
        if (not _fetch_site_allowed(headers) or headers.getlist("authorization")
                or (logout and (not _origin_matches(headers, self._origin)
                                or scope.get("query_string")))
                or (not logout and (scope["method"] != "GET"
                                    or (scope["path"] not in self._paths and not managed)))):
            await self._error(403, scope, receive, send)
            return
        loop = asyncio.get_running_loop()
        try:
            lease = await self._workers.run(
                lambda: self._devices.acquire_for_loop(token, loop),
                lambda value: value.release() if value is not None else None,
            )
        except _Busy:
            await self._error(503, scope, receive, send)
            return
        if lease is None:
            await self._error(401, scope, receive, send)
            return
        if logout:
            # Logout must not wait for itself in the older-generation drain set.
            lease.release()
            await self._logout(lease, scope, receive, send)
            return
        # Serve the root shell under a top-level device-only URL (relative assets
        # still resolve at /assets). Do not redirect through a manual login route.
        await self._serve(lease, scope, receive, send)

    async def _logout(
        self, lease: BrowserSessionLease, scope: Scope, receive: Receive, send: Send,
    ) -> None:
        def pause_and_drain() -> tuple[bool, bool]:
            record = self._devices.pause(lease.binding)
            return (record is not None,
                    record is not None and self._devices.acknowledge(record))
        try:
            paused, drained = await self._workers.run(pause_and_drain)
        except (_Busy, BrowserSessionUnavailable):
            await self._error(503, scope, receive, send)
            return
        if not paused:
            await self._error(401, scope, receive, send)
            return
        message = (
            "Automatic sign-in is paused for this device. An administrator must resume it."
            if drained else
            "The pause was saved, but shutdown could not be confirmed. "
            "An administrator should check this device before resuming it."
        )
        response = JSONResponse({
            "version": 1, "device_logout": True, "paused": True, "drained": drained,
        }, status_code=200 if drained else 202) if Headers(scope=scope).getlist("accept") == [
            "application/json"
        ] else HTMLResponse(
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Display sign-out</title><h1>Display sign-out</h1><p>'
            + message + '</p></html>', status_code=200 if drained else 202,
        )
        response.delete_cookie(BROWSER_DEVICE_COOKIE, path="/", secure=True,
                               httponly=True, samesite="strict")
        _secure_response(response)
        await response(scope, receive, send)

    async def _exchange(self, scope: Scope, receive: Receive, send: Send, headers: Headers) -> None:
        # Native helper only: browser Origin/Fetch Metadata/cookies are not accepted.
        if (scope["method"] != "POST" or scope.get("query_string")
                or headers.getlist("origin") or headers.getlist("cookie")
                or any(key.lower().startswith("sec-fetch-") for key in headers)
                or headers.getlist("content-type") != ["application/json"]
                or len(headers.getlist("authorization")) != 1):
            await self._error(403, scope, receive, send)
            return
        authorization = headers["authorization"]
        if not authorization.startswith("Bearer ") or len(authorization) > 128:
            await self._error(401, scope, receive, send)
            return
        peer = str((scope.get("client") or ("unknown",))[0])[:256]
        if not self._admission.admit(peer) or not self._body_slots.acquire(blocking=False):
            await self._error(429, scope, receive, send)
            return
        try:
            try:
                body = await asyncio.wait_for(_body(receive), timeout=_BODY_TIMEOUT_SECONDS)
                payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
                if (type(payload) is not dict or set(payload) != {"device_id"}
                        or type(payload["device_id"]) is not str):
                    raise ValueError()
            except (ValueError, UnicodeError, RecursionError, TimeoutError):
                await self._error(400, scope, receive, send)
                return
            try:
                issued = await self._workers.run(
                    lambda: self._devices.issue_or_raise(payload["device_id"], authorization[7:]),
                    lambda value: self._devices.revoke_session(value.token)
                    if value is not None else None,
                )
            except (_Busy, BrowserSessionUnavailable):
                await self._error(503, scope, receive, send)
                return
            if issued is None:
                await self._error(401, scope, receive, send)
                return
            response = JSONResponse({"token": issued.token, "expires_in": issued.lifetime_seconds})
            _secure_response(response)
            try:
                await response(scope, receive, send)
            except BaseException:
                self._devices.revoke_session(issued.token)
                raise
        finally:
            self._body_slots.release()

    async def _serve(
        self, lease: BrowserSessionLease, scope: Scope, receive: Receive, send: Send,
    ) -> None:
        started = False
        complete = False

        async def tracked_send(message: Message) -> None:
            nonlocal started, complete
            if message["type"] == "http.response.start":
                started = True
                _secure_message(message)
            elif message["type"] == "http.response.body":
                complete = not message.get("more_body", False)
            await send(message)

        async def request() -> None:
            scope.setdefault("state", {})["sdsctl_display_only"] = True
            if scope["path"] == WEB_DASHBOARD_SESSION_PATH:
                await JSONResponse({"display_only": True, "device_enrolled": True,
                                    "remaining_seconds": lease.remaining_seconds})(
                    scope, receive, tracked_send,
                )
            else:
                app_scope = ({**scope, "path": "/", "raw_path": b"/"}
                             if scope["path"] == BROWSER_DEVICE_DISPLAY_PATH else scope)
                await self._app(app_scope, receive, tracked_send)
        try:
            await run_browser_session_request(lease, request, release=False)
        except BrowserSessionEnded:
            if not started:
                await self._error(401, scope, receive, send)
            elif not complete:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            # Completion ACK includes final ASGI response send, not just app cancellation.
            lease.release()
