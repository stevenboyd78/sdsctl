from __future__ import annotations

import argparse
import stat
from pathlib import Path

from .daemon_ipc import DaemonSocketLocation, DaemonSocketSource
from .daemon_live_audio_client import DaemonLiveAudioClient
from .home_assistant_live_audio_capabilities import (
    HomeAssistantLiveAudioCapabilities,
)
from .home_assistant_live_audio_service import (
    create_home_assistant_live_audio_service,
)
from .web_server import (
    WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST,
    run_web_dashboard_server,
)

HOME_ASSISTANT_LIVE_AUDIO_SERVICE_PORT = 8100
HOME_ASSISTANT_LIVE_AUDIO_CORE_ORIGIN = "home-assistant-core"


class DaemonLiveAudioClientSession:
    """Create one daemon Unix lease for each redeemed HTTP playback."""

    def __init__(self, location: DaemonSocketLocation) -> None:
        if not isinstance(location, DaemonSocketLocation):
            raise TypeError("Home Assistant live-audio daemon location is invalid.")
        self.location = location

    def subscribe(self) -> DaemonLiveAudioClient:
        client = DaemonLiveAudioClient(self.location)
        client.connect()
        return client


def load_home_assistant_live_audio_bridge_secret(path: str | Path) -> str:
    """Load one private regular mode-0600 bridge secret without logging it."""

    selected = Path(path)
    if not selected.is_absolute():
        raise ValueError("Home Assistant live-audio bridge-secret path must be absolute.")
    observed = selected.lstat()
    if not stat.S_ISREG(observed.st_mode):
        raise ValueError("Home Assistant live-audio bridge secret must be a regular file.")
    if stat.S_IMODE(observed.st_mode) & 0o077:
        raise ValueError("Home Assistant live-audio bridge secret must use mode 0600.")
    if observed.st_size > 512:
        raise ValueError("Home Assistant live-audio bridge secret is too large.")
    raw = selected.read_text(encoding="ascii")
    value = raw[:-1] if raw.endswith("\n") else raw
    if (
        len(value) < 43
        or raw not in {value, value + "\n"}
        or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for character in value
        )
    ):
        raise ValueError("Home Assistant live-audio bridge secret is invalid.")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sdsctl-home-assistant-media",
        description="Run the private Home Assistant Core live-audio bridge.",
    )
    parser.add_argument("--daemon-live-audio-socket", type=Path, required=True)
    parser.add_argument("--bridge-secret-file", type=Path, required=True)
    parser.add_argument("--listen-port", type=int, default=HOME_ASSISTANT_LIVE_AUDIO_SERVICE_PORT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    secret = load_home_assistant_live_audio_bridge_secret(args.bridge_secret_file)
    location = DaemonSocketLocation(
        args.daemon_live_audio_socket,
        DaemonSocketSource.EXPLICIT,
    )
    capabilities = HomeAssistantLiveAudioCapabilities(
        secret,
        HOME_ASSISTANT_LIVE_AUDIO_CORE_ORIGIN,
    )
    app = create_home_assistant_live_audio_service(
        capabilities,
        DaemonLiveAudioClientSession(location),
    )
    return run_web_dashboard_server(
        app,
        host=WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST,
        port=args.listen_port,
        access_log=False,
        container_exposure=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "HOME_ASSISTANT_LIVE_AUDIO_CORE_ORIGIN",
    "HOME_ASSISTANT_LIVE_AUDIO_SERVICE_PORT",
    "DaemonLiveAudioClientSession",
    "build_parser",
    "load_home_assistant_live_audio_bridge_secret",
    "main",
]
