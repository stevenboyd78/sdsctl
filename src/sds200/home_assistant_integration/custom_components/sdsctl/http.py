"""Home Assistant-owned authenticated proxy for private App audio."""

from __future__ import annotations

import logging

from aiohttp import ClientError, web
from homeassistant.components.http import KEY_HASS
from homeassistant.components.http.view import HomeAssistantView

from .client import SdsctlClientError
from .const import DATA_RUNTIME, DOMAIN, LIVE_MIME_TYPE, LIVE_PROXY_ROUTE
from .playback import PlaybackUnavailable

_LOGGER = logging.getLogger(__name__)

_RESPONSE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class SdsctlLiveAudioView(HomeAssistantView):
    """Redeem one signed Home Assistant path and stream one private App lease."""

    url = LIVE_PROXY_ROUTE
    name = "api:sdsctl:live-audio"
    requires_auth = True

    async def get(
        self,
        request: web.Request,
        playback_id: str,
    ) -> web.StreamResponse:
        hass = request.app[KEY_HASS]
        domain_data = hass.data.get(DOMAIN)
        runtime = domain_data.get(DATA_RUNTIME) if isinstance(domain_data, dict) else None
        if runtime is None:
            raise web.HTTPServiceUnavailable(
                text="Live scanner audio is unavailable.",
                headers=_RESPONSE_HEADERS,
            )
        try:
            lease = runtime.playbacks.redeem(playback_id)
        except PlaybackUnavailable as error:
            raise web.HTTPGone(
                text="Live scanner audio URL is unavailable.",
                headers=_RESPONSE_HEADERS,
            ) from error

        upstream = None
        response = web.StreamResponse(headers=_RESPONSE_HEADERS)
        response.content_type = LIVE_MIME_TYPE
        try:
            upstream = await runtime.client.async_open_stream()
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(16_384):
                if chunk:
                    await response.write(chunk)
        except (SdsctlClientError, ClientError, ConnectionError, TimeoutError) as error:
            _LOGGER.debug(
                "sdsctl live-audio proxy ended error=%s",
                error.__class__.__name__,
            )
            if not response.prepared:
                raise web.HTTPServiceUnavailable(
                    text="Live scanner audio is unavailable.",
                    headers=_RESPONSE_HEADERS,
                ) from error
        finally:
            if upstream is not None:
                runtime.client.release_stream(upstream)
            lease.release()
        return response
