"""Authenticated read-only browser shell for Favorites profile summaries."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

from .favorites_web_assets import read_favorites_web_asset

FAVORITES_PROFILE_BROWSER_PATH = "/favorites"

_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; "
        "style-src 'self'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": (
        "camera=(), geolocation=(), microphone=(), payment=(), serial=(), usb=()"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _asset(name: str, media_type: str) -> Response:
    return Response(
        content=read_favorites_web_asset(name),
        media_type=media_type,
        headers=dict(_HEADERS),
    )


def attach_favorites_profile_browser_routes(app: FastAPI) -> None:
    """Attach a summary-only shell after operator admission is configured."""

    @app.get(FAVORITES_PROFILE_BROWSER_PATH, include_in_schema=False)
    def favorites_profile_browser() -> HTMLResponse:
        return HTMLResponse(
            content=read_favorites_web_asset("favorites-workspace.html"),
            headers=dict(_HEADERS),
        )

    @app.get("/assets/favorites-appearance.js", include_in_schema=False)
    def favorites_appearance_script() -> Response:
        return _asset("favorites-appearance.js", "application/javascript")

    @app.get("/assets/favorites-profiles.js", include_in_schema=False)
    def favorites_profiles_script() -> Response:
        return _asset("favorites-profiles.js", "application/javascript")

    @app.get("/assets/favorites-workspace.css", include_in_schema=False)
    def favorites_workspace_stylesheet() -> Response:
        return _asset("favorites-workspace.css", "text/css")


__all__ = ["FAVORITES_PROFILE_BROWSER_PATH", "attach_favorites_profile_browser_routes"]
