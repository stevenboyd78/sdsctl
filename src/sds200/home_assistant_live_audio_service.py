from __future__ import annotations

import queue
from collections.abc import Iterator
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from . import __version__
from .home_assistant_live_audio import (
    HOME_ASSISTANT_LIVE_AUDIO_FORMAT,
    LiveAudioLeaseClosed,
)
from .home_assistant_live_audio_capabilities import (
    HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
    HomeAssistantLiveAudioAuthenticationError,
    HomeAssistantLiveAudioCapabilities,
    HomeAssistantLiveAudioCapabilityLease,
    HomeAssistantLiveAudioCapacityError,
)

HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_ISSUE_PATH = "/v1/live-audio/capabilities"
HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER = "x-sdsctl-origin"
HOME_ASSISTANT_LIVE_AUDIO_STREAM_READ_TIMEOUT = 1.0

_PRIVATE_RESPONSE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class _LiveAudioLease(Protocol):
    def get(self, timeout: float | None = None) -> bytes: ...

    def close(self) -> None: ...


class _LiveAudioSession(Protocol):
    def subscribe(self) -> _LiveAudioLease: ...


def create_home_assistant_live_audio_service(
    capabilities: HomeAssistantLiveAudioCapabilities,
    session: _LiveAudioSession,
    *,
    read_timeout: float = HOME_ASSISTANT_LIVE_AUDIO_STREAM_READ_TIMEOUT,
) -> FastAPI:
    """Create the private Core-facing service without an Ingress route."""

    if not isinstance(capabilities, HomeAssistantLiveAudioCapabilities):
        raise TypeError("Home Assistant live-audio service requires capabilities.")
    if not callable(getattr(session, "subscribe", None)):
        raise TypeError("Home Assistant live-audio service requires a subscribable session.")
    if isinstance(read_timeout, bool) or not isinstance(
        read_timeout,
        (int, float),
    ):
        raise TypeError("Live-audio stream read timeout must be a number.")
    normalized_read_timeout = float(read_timeout)
    if normalized_read_timeout <= 0:
        raise ValueError("Live-audio stream read timeout must be greater than zero.")

    app = FastAPI(
        title="sdsctl Home Assistant live audio",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.post(HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_ISSUE_PATH)
    def issue_capability(
        request: Request,
        response: Response,
    ) -> dict[str, object]:
        secret = _bearer_token(request.headers.get("authorization"))
        origin = request.headers.get(HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER, "")
        try:
            capability = capabilities.issue(
                bridge_secret=secret,
                origin=origin,
                peer=_request_peer(request),
            )
        except HomeAssistantLiveAudioAuthenticationError as error:
            raise _authentication_failure() from error
        except HomeAssistantLiveAudioCapacityError as error:
            raise _capacity_failure() from error

        for name, value in _PRIVATE_RESPONSE_HEADERS.items():
            response.headers[name] = value

        return {
            "version": 1,
            "capability": {
                "token": capability.token,
                "method": capability.method,
                "path": capability.path,
                "expires_in": capability.expires_in,
            },
            "format": {
                "container": HOME_ASSISTANT_LIVE_AUDIO_FORMAT.container,
                "codec": HOME_ASSISTANT_LIVE_AUDIO_FORMAT.codec,
                "mime_type": HOME_ASSISTANT_LIVE_AUDIO_FORMAT.mime_type,
                "sample_rate": HOME_ASSISTANT_LIVE_AUDIO_FORMAT.sample_rate,
                "channels": HOME_ASSISTANT_LIVE_AUDIO_FORMAT.channels,
                "bit_rate": HOME_ASSISTANT_LIVE_AUDIO_FORMAT.bit_rate,
                "seekable": HOME_ASSISTANT_LIVE_AUDIO_FORMAT.seekable,
                "duration_seconds": (HOME_ASSISTANT_LIVE_AUDIO_FORMAT.duration_seconds),
            },
        }

    @app.get(
        HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
        responses={
            200: {
                "content": {HOME_ASSISTANT_LIVE_AUDIO_FORMAT.mime_type: {}},
                "description": "Non-seekable daemon-owned live scanner audio",
            }
        },
    )
    def stream(request: Request) -> StreamingResponse:
        token = _bearer_token(request.headers.get("authorization"))
        origin = request.headers.get(HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER, "")
        try:
            capability_lease = capabilities.redeem(
                token,
                method=request.method,
                path=request.url.path,
                origin=origin,
                peer=_request_peer(request),
            )
        except HomeAssistantLiveAudioAuthenticationError as error:
            raise _authentication_failure() from error
        except HomeAssistantLiveAudioCapacityError as error:
            raise _capacity_failure() from error

        try:
            audio_lease = session.subscribe()
        except RuntimeError as error:
            capability_lease.release()
            raise _capacity_failure() from error
        except BaseException:
            capability_lease.release()
            raise

        return StreamingResponse(
            _stream_audio(
                audio_lease,
                capability_lease,
                read_timeout=normalized_read_timeout,
            ),
            media_type=HOME_ASSISTANT_LIVE_AUDIO_FORMAT.mime_type,
            headers=dict(_PRIVATE_RESPONSE_HEADERS),
        )

    return app


def _stream_audio(
    audio_lease: _LiveAudioLease,
    capability_lease: HomeAssistantLiveAudioCapabilityLease,
    *,
    read_timeout: float,
) -> Iterator[bytes]:
    try:
        while True:
            try:
                data = audio_lease.get(read_timeout)
            except queue.Empty:
                continue
            except LiveAudioLeaseClosed:
                return
            if data:
                yield data
    finally:
        try:
            audio_lease.close()
        finally:
            capability_lease.release()


def _bearer_token(header: str | None) -> str:
    if not isinstance(header, str) or len(header) > 1024:
        return ""
    scheme, separator, token = header.partition(" ")
    if separator != " " or scheme != "Bearer" or not token:
        return ""
    if token.strip() != token or " " in token or "\x00" in token:
        return ""
    return token


def _request_peer(request: Request) -> str:
    client = request.client
    if client is None or not client.host:
        return "unknown"
    return client.host


def _authentication_failure() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Live-audio authentication failed.",
        headers={
            **_PRIVATE_RESPONSE_HEADERS,
            "WWW-Authenticate": "Bearer",
        },
    )


def _capacity_failure() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Live-audio playback capacity is unavailable.",
        headers={
            **_PRIVATE_RESPONSE_HEADERS,
            "Retry-After": "1",
        },
    )


__all__ = [
    "HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_ISSUE_PATH",
    "HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER",
    "HOME_ASSISTANT_LIVE_AUDIO_STREAM_READ_TIMEOUT",
    "create_home_assistant_live_audio_service",
]
