"""Set up the first-party sdsctl live scanner audio integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import (
    SdsctlAppClient,
    SdsctlAuthenticationError,
    SdsctlClientError,
)
from .const import (
    CONF_APP_HOST,
    CONF_APP_PORT,
    CONF_BRIDGE_KEY,
    DATA_RUNTIME,
    DATA_VIEW_REGISTERED,
    DOMAIN,
)
from .http import SdsctlLiveAudioView, SdsctlMediaSourceArtworkView
from .playback import PlaybackRegistry


@dataclass(slots=True)
class SdsctlRuntimeData:
    """One loaded Core bridge with no entity or browser-visible credential."""

    client: SdsctlAppClient
    playbacks: PlaybackRegistry
    application_version: str


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Validate the exact App endpoint and load the Core-only bridge."""

    client = SdsctlAppClient(
        async_get_clientsession(hass),
        entry.data[CONF_APP_HOST],
        entry.data[CONF_APP_PORT],
        entry.data[CONF_BRIDGE_KEY],
    )
    try:
        compatibility = await client.async_check_compatibility()
    except SdsctlAuthenticationError as error:
        raise ConfigEntryAuthFailed(
            "The configured sdsctl App bridge key was rejected."
        ) from error
    except SdsctlClientError as error:
        raise ConfigEntryNotReady(
            "The configured sdsctl App live-audio service is unavailable."
        ) from error

    runtime = SdsctlRuntimeData(
        client=client,
        playbacks=PlaybackRegistry(),
        application_version=compatibility.application_version,
    )
    entry.runtime_data = runtime
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data[DATA_RUNTIME] = runtime
    if not domain_data.get(DATA_VIEW_REGISTERED):
        hass.http.register_view(SdsctlLiveAudioView())
        hass.http.register_view(SdsctlMediaSourceArtworkView())
        domain_data[DATA_VIEW_REGISTERED] = True
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Revoke unresolved URLs and close only integration-owned upstreams."""

    runtime = getattr(entry, "runtime_data", None)
    if isinstance(runtime, SdsctlRuntimeData):
        runtime.playbacks.close()
        runtime.client.close()
    domain_data = hass.data.get(DOMAIN)
    if isinstance(domain_data, dict) and domain_data.get(DATA_RUNTIME) is runtime:
        domain_data.pop(DATA_RUNTIME, None)
    return True
