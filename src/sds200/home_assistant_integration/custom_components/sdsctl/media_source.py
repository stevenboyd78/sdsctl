"""Expose daemon-owned live scanner audio as one media-source item."""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from .const import (
    DATA_RUNTIME,
    DOMAIN,
    LIVE_IDENTIFIER,
    LIVE_MIME_TYPE,
    LIVE_PROXY_PATH_PREFIX,
)
from .playback import PlaybackUnavailable


async def async_get_media_source(hass: HomeAssistant) -> SdsctlMediaSource:
    """Set up the first-party sdsctl media source."""

    return SdsctlMediaSource(hass)


class SdsctlMediaSource(MediaSource):
    """Provide one non-seekable live scanner stream."""

    name = "sdsctl live scanner audio"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self._hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Create one bounded Home Assistant URL; never return the App address."""

        if item.identifier != LIVE_IDENTIFIER:
            raise Unresolvable("Unknown sdsctl media item.")
        try:
            runtime = self._runtime()
        except BrowseError as error:
            raise Unresolvable("Live scanner audio is unavailable.") from error
        try:
            playback_id = runtime.playbacks.issue()
        except PlaybackUnavailable as error:
            raise Unresolvable("Live scanner audio is unavailable.") from error
        return PlayMedia(
            f"{LIVE_PROXY_PATH_PREFIX}/{playback_id}",
            LIVE_MIME_TYPE,
        )

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Return the root or the exact playable live item."""

        self._runtime()
        if item.identifier not in ("", LIVE_IDENTIFIER):
            raise BrowseError("Unknown sdsctl media item.")
        if item.identifier == LIVE_IDENTIFIER:
            return _live_item()
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.APP,
            media_content_type=MediaType.APP,
            title="sdsctl",
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.CHANNEL,
            children=[_live_item()],
        )

    def _runtime(self) -> Any:
        domain_data = self._hass.data.get(DOMAIN)
        runtime = domain_data.get(DATA_RUNTIME) if isinstance(domain_data, dict) else None
        if runtime is None:
            raise BrowseError("The sdsctl integration is not loaded.")
        return runtime


def _live_item() -> BrowseMediaSource:
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=LIVE_IDENTIFIER,
        media_class=MediaClass.CHANNEL,
        media_content_type=LIVE_MIME_TYPE,
        title="Live scanner audio",
        can_play=True,
        can_expand=False,
    )
