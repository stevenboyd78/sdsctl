"""Experimental Ingress administration; enabled only by private server opt-in.

Supervisor authenticates the user. A private server-configured ID allowlist grants
enrollment administration; neither dashboard operator cookies nor forwarded
client addresses grant it. Credentials leave only in POST attachment responses.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from html import escape
from urllib.parse import parse_qsl

from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .browser_device_admin import BrowserAdminResult, BrowserDeviceAdmin
from .browser_device_http import _body, _Busy, _Workers
from .browser_device_store import (
    BrowserDeviceConflict,
    BrowserDeviceRecord,
    BrowserDeviceState,
    BrowserDeviceStoreError,
    validate_browser_device_record,
)
from .web_auth import _normalize_origin

BROWSER_ADMIN_PATH = "/api/v1/home-assistant/browser-devices"
_INGRESS_PEER = "172.30.32.2"
_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; form-action 'self'; "
                               "frame-ancestors 'self'; base-uri 'none'",
}


@dataclass(frozen=True, slots=True)
class BrowserDeviceIngress:
    admin: BrowserDeviceAdmin
    origin: str
    admin_user_ids: frozenset[str]

    def __post_init__(self) -> None:
        if (not isinstance(self.admin, BrowserDeviceAdmin)
                or type(self.admin_user_ids) is not frozenset
                or not 1 <= len(self.admin_user_ids) <= 32
                or any(type(uid) is not str or re.fullmatch(r"[a-f0-9]{32}", uid) is None
                       for uid in self.admin_user_ids)
                or _normalize_origin(self.origin) != self.origin):
            raise ValueError("Explicit private Ingress administrator configuration is required.")


class BrowserDeviceIngressMiddleware:
    def __init__(self, app: ASGIApp, *, configuration: BrowserDeviceIngress) -> None:
        self._app = app
        self._config = configuration
        self._workers = _Workers()
        self._body_slots = threading.BoundedSemaphore(2)
        self._nonces: dict[str, tuple[str, float]] = {}
        self._ready = False

    def _nonce(self, uid: str) -> str:
        now = time.monotonic()
        self._nonces = {key: value for key, value in self._nonces.items() if value[1] > now}
        if len(self._nonces) >= 32:
            raise _Busy()
        token = secrets.token_hex(32)
        self._nonces[token] = (uid, now + 300)
        return token

    def _consume(self, uid: str, token: str) -> None:
        item = self._nonces.get(token)
        if item is None or item[0] != uid or item[1] <= time.monotonic():
            raise ValueError()
        del self._nonces[token]  # Consume before scheduling any mutation; never replay on failure.

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
            return
        if scope.get("path") != BROWSER_ADMIN_PATH:
            await self._app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = Headers(scope=scope)
        peer = scope.get("client")
        users = headers.getlist("x-remote-user-id")
        site = headers.getlist("sec-fetch-site")
        origins = headers.getlist("origin")
        authorized = (peer is not None and peer[0] == _INGRESS_PEER
                      and len(users) == 1 and users[0] in self._config.admin_user_ids)
        if not authorized:
            response: Response = self._error(403)
        elif not self._ready:
            response = self._error(503)
        elif (scope.get("query_string") or headers.getlist("authorization")
              or len(site) > 1 or (site and site[0] not in {"same-origin", "none"})
              or (origins and origins != [self._config.origin])):
            response = self._error(403)
        else:
            try:
                response = await self._request(scope, receive, headers, users[0])
            except BrowserDeviceConflict:
                response = self._error(409, "Device changed. Refresh and review; no change made.")
            except BrowserDeviceStoreError:
                response = self._error(409, "Authority unavailable or action invalid. "
                                       "Inspect current state before retrying; "
                                       "do not assume rollback.")
            except _Busy:
                response = self._error(503)
            except (ValueError, UnicodeError, TimeoutError):
                response = self._error(400)
        response.headers.update(_HEADERS)
        await response(scope, receive, send)

    @staticmethod
    def _error(
        code: int, detail: str = "Browser-device administration request denied.",
    ) -> Response:
        return JSONResponse({"detail": detail}, status_code=code)

    async def _request(
        self, scope: Scope, receive: Receive, headers: Headers, uid: str,
    ) -> Response:
        admin = self._config.admin
        if scope["method"] == "GET":
            records = await self._workers.run(admin.inventory)
            return HTMLResponse(_page(records, self._nonce(uid)))
        if scope["method"] != "POST":
            return self._error(405)
        if (headers.getlist("origin") != [self._config.origin]
                or headers.getlist("content-type") != ["application/x-www-form-urlencoded"]):
            return self._error(403)
        if not self._body_slots.acquire(blocking=False):
            raise _Busy()
        try:
            async with asyncio.timeout(3):
                body = await _body(receive)
        finally:
            self._body_slots.release()
        pairs = parse_qsl(body.decode("ascii"), keep_blank_values=True, strict_parsing=True,
                          encoding="utf-8", errors="strict", max_num_fields=8)
        payload = dict(pairs)
        action = payload.get("action")
        required = {"action", "device_id", "confirm", "csrf"}
        if action != "enroll":
            required |= {"generation", "state"}
        if len(payload) != len(pairs) or set(payload) != required:
            raise ValueError()
        if action not in {"enroll", "rotate", "pause", "resume", "revoke", "confirm"}:
            raise ValueError()
        if payload["confirm"] != payload["device_id"]:
            raise ValueError()
        record = None
        if action != "enroll":
            if re.fullmatch(r"[1-9][0-9]{0,18}", payload["generation"]) is None:
                raise ValueError()
            record = BrowserDeviceRecord(payload["device_id"], int(payload["generation"]),
                                          BrowserDeviceState(payload["state"]))
            validate_browser_device_record(record)
        self._consume(uid, payload["csrf"])
        if action == "enroll":
            issued = await self._workers.run(lambda: admin.enroll(payload["device_id"]))
            return _attachment(issued.record, issued.credential, {"status": "issued",
                                                                "completed": False})
        assert record is not None
        if action == "rotate":
            rotated = await self._workers.run(lambda: admin.rotate(record))
            return _attachment(rotated.result.record, rotated.credential, rotated.result.as_dict())
        if action == "confirm":
            result = await self._workers.run(lambda: admin.confirm(record))
        else:
            target = {"pause": BrowserDeviceState.PAUSED, "resume": BrowserDeviceState.ACTIVE,
                      "revoke": BrowserDeviceState.REVOKED}[action]
            result = await self._workers.run(lambda: admin.transition(record, target))
        try:
            nonce = self._nonce(uid)
        except _Busy:
            nonce = None  # Do not lose a committed outcome just because new forms are at capacity.
        return HTMLResponse(_result_page(result, nonce))


def _attachment(
    record: BrowserDeviceRecord, credential: str, outcome: dict[str, object],
) -> Response:
    # Native browser form download, not a credential rendered into HTML or handled by dashboard JS.
    document = {"version": 1, "device_id": record.device_id, "generation": record.generation,
                "credential": credential, "outcome": outcome}
    return Response(json.dumps(document), media_type="application/json", headers={
        "Content-Disposition": 'attachment; filename="sdsctl-browser-device.json"',
        "X-SDSCTL-Completion": str(outcome["status"]),
    })


def _hidden(key: str, value: object) -> str:
    return f'<input type="hidden" name="{key}" value="{escape(str(value), quote=True)}">'


def _form(action: str, nonce: str, record: BrowserDeviceRecord | None = None) -> str:
    fields = _hidden("action", action) + _hidden("csrf", nonce)
    if record is not None:
        fields += (_hidden("device_id", record.device_id) + _hidden("generation", record.generation)
                   + _hidden("state", record.state.value))
    else:
        fields += '<label>New device ID <input name="device_id" required maxlength="64"></label> '
    fields += '<label>Type exact device ID to confirm <input name="confirm" required></label> '
    return f'<form method="post" action="">{fields}<button>{escape(action.title())}</button></form>'


def _shell(content: str) -> str:
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Browser device administration — experimental</title><body>'
            '<h1>Browser device administration — experimental</h1>'
            '<p>Enrollment status is not connection status. '
            'Actions affect only browser displays.</p>'
            '<p>Each page accepts one action. Refresh inventory after every action or download.</p>'
            '<p>Enroll and Rotate download a secret once. Protect the downloaded file (mode 0600), '
            'transfer privately, and remove extra copies. No web download can guarantee local file '
            'permissions. Check the outcome in the download; issuance is not proof of completed '
            'session shutdown. If delivery fails, refresh inventory before any new action. '
            'Never automatically repeat a rotation.</p>'
            + content + '<p><a href="">Refresh inventory</a></p></body></html>')


def _page(records: tuple[BrowserDeviceRecord, ...], nonce: str) -> str:
    content = '<h2>Enroll a new browser display</h2>' + _form("enroll", nonce)
    for record in records:
        content += (f'<section><h2>{escape(record.device_id)}</h2><p>Generation '
                    f'{record.generation} — {escape(record.state.value)}</p>')
        actions = ["confirm"]
        if record.state is not BrowserDeviceState.REVOKED:
            actions += ["rotate", "revoke", "resume" if record.state is BrowserDeviceState.PAUSED
                        else "pause"]
        for action in actions:
            content += _form(action, nonce, record)
        content += '</section>'
    return _shell(content)


def _result_page(result: BrowserAdminResult, nonce: str | None) -> str:
    return _shell(f'<h2>Outcome: {escape(result.status.value)}</h2><p>'
                  'Only confirmed means the owner acknowledged completion. A saved change '
                  'is not rolled back when confirmation is unavailable. Confirm retries only '
                  'the exact record, without changing credentials or state.</p>'
                  + (_form("confirm", nonce, result.record) if nonce is not None else
                     '<p>New forms are temporarily at capacity; refresh inventory later.</p>'))
