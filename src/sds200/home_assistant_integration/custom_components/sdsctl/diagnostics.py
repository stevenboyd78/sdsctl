"""Redacted diagnostics for the sdsctl live-audio bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_APP_HOST, CONF_APP_PORT, DOMAIN, LIVE_MIME_TYPE, PROTOCOL_VERSION


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return low-rate state without the bridge key, URL tokens, or audio."""

    del hass
    runtime = getattr(entry, "runtime_data", None)
    snapshot = runtime.playbacks.snapshot() if runtime is not None else None
    return {
        "integration": DOMAIN,
        "protocol_version": PROTOCOL_VERSION,
        "mime_type": LIVE_MIME_TYPE,
        "app_host": entry.data.get(CONF_APP_HOST),
        "app_port": entry.data.get(CONF_APP_PORT),
        "application_version": (
            runtime.application_version if runtime is not None else None
        ),
        "playback": (
            {
                "outstanding": snapshot.outstanding,
                "active": snapshot.active,
                "issued": snapshot.issued,
                "redeemed": snapshot.redeemed,
                "rejected": snapshot.rejected,
                "expired": snapshot.expired,
                "closed": snapshot.closed,
            }
            if snapshot is not None
            else None
        ),
    }
