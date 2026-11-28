"""Packaged, route-free assets for the future Favorites browser workspace."""

from __future__ import annotations

from importlib.resources import files

FAVORITES_WEB_ASSET_NAMES = (
    "favorites-appearance.js",
    "favorites-profiles.js",
    "favorites-workspace.css",
    "favorites-workspace.html",
)


def read_favorites_web_asset(name: str) -> bytes:
    """Read one exact packaged asset without accepting a filesystem path."""

    if not isinstance(name, str) or name not in FAVORITES_WEB_ASSET_NAMES:
        raise ValueError("Favorites web asset is unavailable.")
    return files("sds200.web_assets").joinpath(name).read_bytes()


__all__ = ["FAVORITES_WEB_ASSET_NAMES", "read_favorites_web_asset"]
