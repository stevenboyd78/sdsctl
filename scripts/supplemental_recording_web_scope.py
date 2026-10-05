#!/usr/bin/env python3
"""Uninstalled finite-test dashboard scope, not an executable or lifecycle guard.

Reuse native authentication, rendering, protocol validation and media streaming;
admit only an explicit set of observations, browser-session operations and the
one bounded supplemental-demand route. In particular, the ordinary HA dashboard
also has host-side administrative routes; a restricted daemon API alone cannot
guard those. No CLI service stripping, global patches or route auto-discovery.

The future launcher still must qualify source/runtime, bind all client factories
to the original native actor, and enforce the independent original lifetime.
This surface neither authenticates its supplied factories nor proves readiness,
records audio, arms acquisition, renews that lifetime or restores an App.
"""

from __future__ import annotations

from threading import Lock

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from sds200 import web_dashboard
from sds200.web_auth import WebDashboardAuthentication
from sds200.web_supplemental import CONTEXT_PATH, DEMAND_PATH, FRAME_PATH

MESSAGE = "This operation is unavailable in the finite test dashboard."
_FACTORY = web_dashboard.create_web_dashboard_app
_THEMES = frozenset(
    {"system", "lcars", "pip-boy-inspired", "matrix", "first-responder", "amateur-radio"}
)
_READS = frozenset(
    {
        "/",
        "/healthz",
        "/api/v1/status",
        "/api/v1/snapshot",
        "/api/v1/display-frame",
        "/api/v1/recording",
        "/api/v1/recordings",
        "/api/v1/events",
        "/api/v1/audio",
        "/api/v1/home-assistant/connected-clients",
        CONTEXT_PATH,
        FRAME_PATH,
        "/assets/dashboard.css",
        "/assets/dashboard-viewport.css",
        "/assets/dashboard.js",
        "/assets/mimic-sds.css",
        "/assets/mimic-sds.js",
        "/assets/mimic-supplemental.js",
        "/assets/system-palettes.css",
        "/assets/theme-bootstrap.js",
        "/assets/audio-worklet.js",
        "/assets/favicon.svg",
    }
) | frozenset(f"/assets/themes/{theme}/theme.css" for theme in _THEMES)
_LOGIN = frozenset({"/auth/login", "/auth/display/login"})
_FILE = "/api/v1/recordings/file/"


def _route(scope):
    """Use the same decoded path/root-path relationship as the native router.

    Dot segments, empty interior segments, controls and backslashes refuse.
    Recording identifiers remain inventory-relative and independently validated
    by the actual daemon file service; this is only an HTTP route decision.
    """
    path, root = scope.get("path"), scope.get("root_path", "")
    if type(path) is not str or type(root) is not str:
        return None
    if not 0 < len(path) <= 4096 or len(root) > 4096:
        return None
    if root and (path == root or path.startswith(root + "/")):
        path = path[len(root) :] or "/"
    if not path.startswith("/") or "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        return None
    if path != "/" and any(part in ("", ".", "..") for part in path[1:].split("/")):
        return None
    return path


class _Surface:
    """Closed outer route gate; permitted traffic still crosses native auth.

    No lifespan/deadline or peer-identity claim. An allowlisted path is not
    authorization: native HA Ingress or HTTPS session/origin checks still apply,
    including the narrower permissions of display-only browser sessions.
    """

    def __init__(self, app, *, native_auth):
        if type(app) is not FastAPI or type(native_auth) is not bool:
            raise ValueError(MESSAGE)
        self._app, self._native_auth = app, native_auth

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind == "lifespan":
            await self._app(scope, receive, send)
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if kind != "http":
            raise ValueError(MESSAGE)
        path, method = _route(scope), scope.get("method")
        allowed = (
            method == "GET"
            and path is not None
            and (path in _READS or path.startswith(_FILE) and len(path) > len(_FILE))
        ) or (method == "POST" and path == DEMAND_PATH)
        if self._native_auth:
            allowed = allowed or (
                path in _LOGIN
                and method in ("GET", "POST")
                or path == "/auth/session"
                and method == "GET"
                or path == "/auth/logout"
                and method == "POST"
            )
        if not allowed:
            # Do not receive/buffer a rejected body or invoke ANY native handler.
            await JSONResponse(
                {"detail": MESSAGE},
                status_code=404,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )(scope, receive, send)
            return
        await self._app(scope, receive, send)


class Factory:
    """One explicit construction, no listener and no ordinary CLI interception.

    No arbitrary kwargs or managed-theme/admin/browser-device/waterfall hooks.
    The four separately bound native factories remain required launcher work;
    accepting callables here does not qualify their destinations or lifecycle.
    """

    def __init__(self):
        self.used = False
        self._lock = Lock()

    def __call__(
        self,
        api_client_factory,
        event_client_factory=None,
        pcmu_client_factory=None,
        recording_file_client_factory=None,
        *,
        home_assistant_ingress=False,
        lan_authentication=None,
    ):
        with self._lock:
            if self.used:
                raise ValueError(MESSAGE)
            self.used = True
        if web_dashboard.create_web_dashboard_app is not _FACTORY:
            raise ValueError(MESSAGE)
        if type(home_assistant_ingress) is not bool:
            raise ValueError(MESSAGE)
        native = lan_authentication is not None
        if home_assistant_ingress == native:
            raise ValueError(MESSAGE)
        if native and type(lan_authentication) is not WebDashboardAuthentication:
            raise ValueError(MESSAGE)
        if not callable(api_client_factory) or any(
            value is not None and not callable(value)
            for value in (event_client_factory, pcmu_client_factory, recording_file_client_factory)
        ):
            raise ValueError(MESSAGE)
        app = _FACTORY(
            api_client_factory,
            event_client_factory,
            pcmu_client_factory,
            recording_file_client_factory,
            home_assistant_ingress=home_assistant_ingress,
            lan_authentication=lan_authentication,
            supplemental_delivery=True,
            supplemental_demand=True,
            supplemental_consumer=True,
        )
        return _Surface(app, native_auth=native)


if __name__ == "__main__":
    raise SystemExit("Uninstalled finite WebUI scope only; no listener or lifecycle enabled.")
