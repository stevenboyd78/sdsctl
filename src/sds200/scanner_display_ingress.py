"""Private opt-in Ingress administration, separate from operator/display logins."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass
from html import escape

from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .browser_device_http import _Busy, _Workers
from .scanner_display_admin import (
    DisplayAdminBusy,
    DisplayAdminConflict,
    ScannerDisplayProfileAdmin,
)
from .scanner_display_profile import MAX_PROFILE_BYTES
from .scanner_display_profile_storage import DisplayProfileStorageError
from .scanner_display_upload import DisplayUploadUnconfirmed
from .web_auth import _normalize_origin

DISPLAY_PROFILE_ADMIN_PATH = "/api/v1/home-assistant/scanner-display-profile"
_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@dataclass(frozen=True, slots=True)
class ScannerDisplayIngress:
    admin: ScannerDisplayProfileAdmin
    origin: str
    admin_user_ids: frozenset[str]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.admin, ScannerDisplayProfileAdmin)
            or type(self.admin_user_ids) is not frozenset
            or not 1 <= len(self.admin_user_ids) <= 32
            or any(
                type(uid) is not str or re.fullmatch(r"[a-f0-9]{32}", uid) is None
                for uid in self.admin_user_ids
            )
            or _normalize_origin(self.origin) != self.origin
        ):
            raise ValueError(
                "Explicit private profile-administrator Ingress configuration required."
            )


async def _body(receive: Receive, limit: int) -> bytes:
    body = bytearray()
    async with asyncio.timeout(5):
        while True:
            message = await receive()
            if message["type"] != "http.request":
                raise ValueError()
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                raise ValueError()
            body.extend(chunk)
            if not message.get("more_body", False):
                return bytes(body)


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


class ScannerDisplayIngressMiddleware:
    def __init__(self, app: ASGIApp, *, configuration: ScannerDisplayIngress) -> None:
        self._app, self._config = app, configuration
        self._workers = _Workers()
        self._bodies = 0
        self._nonces: dict[str, tuple[str, float]] = {}
        self._ready = False

    def _nonce(self, uid: str) -> str:
        now = time.monotonic()
        self._nonces = {key: item for key, item in self._nonces.items() if item[1] > now}
        if len(self._nonces) >= 32:
            raise _Busy()
        token = secrets.token_hex(32)
        self._nonces[token] = (uid, now + 300)
        return token

    def _consume(self, uid: str, values: list[str]) -> None:
        if len(values) != 1:
            raise ValueError()
        item = self._nonces.get(values[0])
        if item is None or item[0] != uid or item[1] <= time.monotonic():
            raise ValueError()
        del self._nonces[values[0]]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":

            async def lifecycle(message: Message) -> None:
                if message["type"] == "lifespan.startup.complete":
                    self._ready = True
                elif message["type"] in {"lifespan.shutdown.complete", "lifespan.shutdown.failed"}:
                    self._ready = False
                await send(message)

            try:
                await self._app(scope, receive, lifecycle)
            finally:
                self._ready = False
                self._nonces.clear()
                self._workers.close()
                await asyncio.to_thread(self._config.admin.close)
            return
        path = scope.get("path", "")
        if path != DISPLAY_PROFILE_ADMIN_PATH and not path.startswith(
            DISPLAY_PROFILE_ADMIN_PATH + "/"
        ):
            await self._app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = Headers(scope=scope)
        users, sites, origins = (
            headers.getlist(key) for key in ("x-remote-user-id", "sec-fetch-site", "origin")
        )
        peer = scope.get("client")
        authorized = (
            peer is not None
            and peer[0] == "172.30.32.2"
            and len(users) == 1
            and users[0] in self._config.admin_user_ids
        )
        response: Response
        if not authorized:
            response = self._error(403)
        elif not self._ready:
            response = self._error(503)
        elif (
            scope.get("query_string")
            or headers.getlist("authorization")
            or len(sites) > 1
            or (sites and sites[0] not in {"same-origin", "none"})
            or (origins and origins != [self._config.origin])
        ):
            response = self._error(403)
        else:
            try:
                response = await self._request(scope, receive, headers, users[0])
            except DisplayUploadUnconfirmed as error:
                response = self._error(409, str(error))
            except (DisplayProfileStorageError, DisplayAdminConflict):
                response = self._error(
                    409,
                    "Profile review or save could not be completed. "
                    "Inspect source and accepted status before any new review. "
                    "Do not assume rollback or repeat a submitted confirmation.",
                )
            except (DisplayAdminBusy, _Busy):
                response = self._error(503, "Profile administration is busy. Inspect status later.")
            except PermissionError:
                response = self._error(403)
            except (ValueError, UnicodeError, TimeoutError, RecursionError):
                response = self._error(400)
            except Exception:
                response = self._error(
                    500,
                    "Profile administration failed. Preserve state for "
                    "administrator review; do not repeat the submitted action.",
                )
        response.headers.update(_HEADERS)
        if "content-security-policy" not in response.headers:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'self'"
            )
        await response(scope, receive, send)

    @staticmethod
    def _error(code: int, detail: str = "Profile administration request denied.") -> JSONResponse:
        return JSONResponse({"detail": detail}, status_code=code)

    async def _request(
        self, scope: Scope, receive: Receive, headers: Headers, uid: str
    ) -> Response:
        suffix = scope["path"][len(DISPLAY_PROFILE_ADMIN_PATH) :]
        if scope["method"] == "GET":
            if suffix == "/status":
                return JSONResponse(await self._workers.run(self._config.admin.status))
            if suffix:
                return self._error(404)
            nonce, script_nonce = self._nonce(uid), secrets.token_hex(32)
            return HTMLResponse(
                _page(nonce, script_nonce),
                headers={
                    "Content-Security-Policy": "default-src 'none'; connect-src 'self'; "
                    f"script-src 'nonce-{script_nonce}'; style-src 'nonce-{script_nonce}'; "
                    "frame-ancestors 'self'; base-uri 'none'; form-action 'self'",
                },
            )
        if scope["method"] != "POST":
            return self._error(405)
        if suffix not in {"/upload", "/refresh", "/commit", "/cancel", "/reload"}:
            return self._error(404)
        content_type = "application/octet-stream" if suffix == "/upload" else "application/json"
        if (
            headers.getlist("origin") != [self._config.origin]
            or headers.getlist("content-type") != [content_type]
            or headers.getlist("content-encoding")
        ):
            return self._error(403)
        if suffix == "/upload" and not self._config.admin.allow_upload:
            return self._error(403)
        limit = MAX_PROFILE_BYTES if suffix == "/upload" else 2048
        lengths = headers.getlist("content-length")
        if lengths and (
            len(lengths) != 1
            or re.fullmatch(r"[0-9]{1,8}", lengths[0]) is None
            or int(lengths[0]) > limit
        ):
            return self._error(413)
        self._consume(uid, headers.getlist("x-sdsctl-profile-csrf"))
        if self._bodies >= 2:
            raise _Busy()
        self._bodies += 1
        try:
            body = await _body(receive, limit)
        finally:
            self._bodies -= 1
        if lengths and len(body) != int(lengths[0]):
            raise ValueError()
        payload = {} if suffix == "/upload" else json.loads(body, object_pairs_hook=_object)
        expected = (
            {"review_id", "confirm_source_change"}
            if suffix == "/commit"
            else {"review_id"}
            if suffix == "/cancel"
            else set()
        )
        if type(payload) is not dict or set(payload) != expected:
            raise ValueError()
        if "review_id" in payload and (
            type(payload["review_id"]) is not str
            or re.fullmatch(r"[a-f0-9]{64}", payload["review_id"]) is None
        ):
            raise ValueError()
        if suffix == "/commit" and type(payload["confirm_source_change"]) is not bool:
            raise ValueError()
        next_nonce = self._nonce(
            uid
        )  # Allocate before mutation; never lose an outcome to capacity.
        admin = self._config.admin
        if suffix in {"/upload", "/refresh"}:
            result = await self._workers.run(
                lambda: admin.preview(uid, body if suffix == "/upload" else None)
            )
        elif suffix == "/commit":
            result = await self._workers.run(
                lambda: admin.commit(
                    uid,
                    payload["review_id"],
                    confirm_source_change=payload["confirm_source_change"],
                )
            )
        elif suffix == "/cancel":
            result = await self._workers.run(lambda: admin.cancel(uid, payload["review_id"]))
        else:
            result = await self._workers.run(admin.reload)
        return JSONResponse({**result, "csrf": next_nonce})


def _page(csrf: str, nonce: str) -> str:
    from importlib.resources import files

    template = files("sds200.web_assets").joinpath("scanner-display-admin.html").read_text("utf-8")
    return template.replace("__CSP_NONCE__", escape(nonce, quote=True)).replace(
        "__CSRF_TOKEN__", escape(csrf, quote=True)
    )
