from __future__ import annotations

import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .exceptions import SDS200Error
from .home_assistant_themes import (
    HOME_ASSISTANT_CARD_AGGREGATE_MODULE_FILENAME,
    HomeAssistantThemeError,
    built_in_home_assistant_theme_registry,
    read_built_in_home_assistant_card_aggregate_module,
    read_built_in_home_assistant_theme_module,
)

HOME_ASSISTANT_LOVELACE_CARD_FILENAME = "sds200-card.js"
HOME_ASSISTANT_LOVELACE_CARD_DIRECTORY = Path("/homeassistant/www/sds200")
HOME_ASSISTANT_LOVELACE_CARD_PATH = (
    HOME_ASSISTANT_LOVELACE_CARD_DIRECTORY / HOME_ASSISTANT_LOVELACE_CARD_FILENAME
)
HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_FILENAME = "sds200-display-card.js"
HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_PATH = (
    HOME_ASSISTANT_LOVELACE_CARD_DIRECTORY
    / HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_FILENAME
)
HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_FILENAME = "sds200-waterfall-card.js"
HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_PATH = (
    HOME_ASSISTANT_LOVELACE_CARD_DIRECTORY
    / HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_FILENAME
)
HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME = (
    HOME_ASSISTANT_CARD_AGGREGATE_MODULE_FILENAME
)
HOME_ASSISTANT_LOVELACE_AGGREGATE_PATH = (
    HOME_ASSISTANT_LOVELACE_CARD_DIRECTORY
    / HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME
)
_HOME_ASSISTANT_LOVELACE_CARD_MODE = 0o644

_BUILT_IN_HOME_ASSISTANT_THEMES = built_in_home_assistant_theme_registry()
HOME_ASSISTANT_LOVELACE_CARD_RESOURCE_URL = (
    _BUILT_IN_HOME_ASSISTANT_THEMES.require("compact").resource_url
)
HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_RESOURCE_URL = (
    _BUILT_IN_HOME_ASSISTANT_THEMES.require("sds200-display").resource_url
)
HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_RESOURCE_URL = (
    _BUILT_IN_HOME_ASSISTANT_THEMES.require("waterfall").resource_url
)
_HOME_ASSISTANT_LOVELACE_AGGREGATE_BYTES = (
    read_built_in_home_assistant_card_aggregate_module()
)
HOME_ASSISTANT_LOVELACE_AGGREGATE_RESOURCE_URL = (
    f"/local/sds200/{HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME}?v="
    f"{hashlib.sha256(_HOME_ASSISTANT_LOVELACE_AGGREGATE_BYTES).hexdigest()}"
)


def _asset_bytes(filename: str) -> bytes:
    if filename == HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME:
        return read_built_in_home_assistant_card_aggregate_module()
    registry = built_in_home_assistant_theme_registry()
    for theme in registry.themes:
        if theme.installed_filename == filename:
            return read_built_in_home_assistant_theme_module(theme)
    raise HomeAssistantThemeError(
        f"unknown built-in Home Assistant module filename: {filename}"
    )


def _install_home_assistant_lovelace_asset(
    destination: str | Path,
    *,
    filename: str,
) -> Path:
    target = Path(destination)

    if not target.is_absolute():
        raise ValueError("Home Assistant Lovelace card destination must be absolute.")
    if target.name != filename:
        raise ValueError(
            "Home Assistant Lovelace card destination must use "
            f"{filename!r}."
        )

    parent = target.parent
    www = parent.parent

    for path in (www, parent, target):
        if path.is_symlink():
            raise SDS200Error(f"Home Assistant Lovelace card installation refuses symlinks: {path}")

    if www.exists() and not www.is_dir():
        raise SDS200Error(f"Home Assistant www path is not a directory: {www}")
    if parent.exists() and not parent.is_dir():
        raise SDS200Error(f"Home Assistant SDS200 card path is not a directory: {parent}")
    if target.exists() and not target.is_file():
        raise SDS200Error(f"Home Assistant SDS200 card target is not a file: {target}")

    parent.mkdir(parents=True, exist_ok=True)

    payload = _asset_bytes(filename)

    if target.exists() and target.read_bytes() == payload:
        target.chmod(_HOME_ASSISTANT_LOVELACE_CARD_MODE)
        return target

    temporary: Path | None = None

    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        assert temporary is not None
        temporary.chmod(_HOME_ASSISTANT_LOVELACE_CARD_MODE)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    target.chmod(_HOME_ASSISTANT_LOVELACE_CARD_MODE)

    if target.read_bytes() != payload:
        raise SDS200Error("Home Assistant Lovelace card installation verification failed.")

    return target


def install_home_assistant_lovelace_card(
    destination: str | Path = HOME_ASSISTANT_LOVELACE_CARD_PATH,
) -> Path:
    """Atomically install the packaged read-only card into Home Assistant www."""
    return _install_home_assistant_lovelace_asset(
        destination,
        filename=HOME_ASSISTANT_LOVELACE_CARD_FILENAME,
    )


def install_home_assistant_lovelace_display_card(
    destination: str | Path = HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_PATH,
) -> Path:
    """Atomically install the packaged scanner-display card asset."""
    return _install_home_assistant_lovelace_asset(
        destination,
        filename=HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_FILENAME,
    )


def install_home_assistant_lovelace_waterfall_card(
    destination: str | Path = HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_PATH,
) -> Path:
    """Atomically install the authenticated waterfall card asset."""
    return _install_home_assistant_lovelace_asset(
        destination,
        filename=HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_FILENAME,
    )


def install_home_assistant_lovelace_aggregate(
    destination: str | Path = HOME_ASSISTANT_LOVELACE_AGGREGATE_PATH,
) -> Path:
    """Atomically install the aggregate first-party card entry point."""
    return _install_home_assistant_lovelace_asset(
        destination,
        filename=HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME,
    )


def install_home_assistant_lovelace_cards() -> tuple[Path, Path, Path, Path]:
    """Install all first-party Home Assistant Lovelace card assets."""
    card_modules = tuple(
        _install_home_assistant_lovelace_asset(
            HOME_ASSISTANT_LOVELACE_CARD_DIRECTORY / theme.installed_filename,
            filename=theme.installed_filename,
        )
        for theme in built_in_home_assistant_theme_registry().themes
    )
    if len(card_modules) != 3:
        raise HomeAssistantThemeError(
            "built-in Home Assistant compatibility set must contain three modules"
        )
    aggregate = install_home_assistant_lovelace_aggregate(
        HOME_ASSISTANT_LOVELACE_CARD_DIRECTORY
        / HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME
    )
    return card_modules[0], card_modules[1], card_modules[2], aggregate


__all__ = [
    "HOME_ASSISTANT_LOVELACE_CARD_DIRECTORY",
    "HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME",
    "HOME_ASSISTANT_LOVELACE_AGGREGATE_PATH",
    "HOME_ASSISTANT_LOVELACE_AGGREGATE_RESOURCE_URL",
    "HOME_ASSISTANT_LOVELACE_CARD_FILENAME",
    "HOME_ASSISTANT_LOVELACE_CARD_PATH",
    "HOME_ASSISTANT_LOVELACE_CARD_RESOURCE_URL",
    "HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_FILENAME",
    "HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_PATH",
    "HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_RESOURCE_URL",
    "HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_FILENAME",
    "HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_PATH",
    "HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_RESOURCE_URL",
    "install_home_assistant_lovelace_card",
    "install_home_assistant_lovelace_cards",
    "install_home_assistant_lovelace_aggregate",
    "install_home_assistant_lovelace_display_card",
    "install_home_assistant_lovelace_waterfall_card",
]
