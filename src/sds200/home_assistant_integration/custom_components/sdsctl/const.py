"""Constants for the sdsctl live scanner audio integration."""

from __future__ import annotations

DOMAIN = "sdsctl"

CONF_APP_HOST = "app_host"
CONF_APP_PORT = "app_port"
CONF_BRIDGE_KEY = "bridge_key"

DEFAULT_APP_PORT = 8100
CORE_ORIGIN = "home-assistant-core"
PROTOCOL_VERSION = 1

LIVE_IDENTIFIER = "live"
LIVE_MEDIA_SOURCE_ID = "media-source://sdsctl/live"
LIVE_MIME_TYPE = "audio/mpeg"
LIVE_PROXY_ROUTE = "/api/sdsctl/live/{playback_id}"
LIVE_PROXY_PATH_PREFIX = "/api/sdsctl/live"
MEDIA_SOURCE_ARTWORK_ROUTE = "/api/sdsctl/media-source-artwork"

PLAYBACK_LIFETIME_SECONDS = 30.0
PLAYBACK_MAX_OUTSTANDING = 16
PLAYBACK_MAX_ACTIVE = 4

COMPATIBILITY_PATH = "/v1/live-audio/compatibility"
CAPABILITY_ISSUE_PATH = "/v1/live-audio/capabilities"
CAPABILITY_STREAM_PATH = "/v1/live-audio/stream"
ORIGIN_HEADER = "x-sdsctl-origin"

EXPECTED_FORMAT = {
    "container": "MP3",
    "codec": "MP3 (MPEG audio layer 3)",
    "mime_type": LIVE_MIME_TYPE,
    "sample_rate": 44_100,
    "channels": 1,
    "bit_rate": 64_000,
    "seekable": False,
    "duration_seconds": None,
}

DATA_RUNTIME = "runtime"
DATA_VIEW_REGISTERED = "view_registered"
