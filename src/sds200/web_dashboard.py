"""Daemon-backed HTTP application and browser dashboard shell."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from functools import cache
from html import escape
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal, Protocol, TypeAlias

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .daemon_api import DaemonApiOperation
from .daemon_events import DaemonEvent
from .daemon_recording_file_client import (
    DaemonRecordingFileDownload,
    DaemonRecordingFileRequestError,
)
from .daemon_recording_file_protocol import RecordingFileResponseStatus
from .daemon_waterfall_protocol import DaemonWaterfallRecord
from .exceptions import DaemonRequestError, SDS200Error
from .home_assistant_integration_ingress import (
    HomeAssistantIntegrationAction,
    execute_home_assistant_integration_ingress_action,
    home_assistant_integration_ingress_status,
    reveal_home_assistant_integration_bridge_key,
    rotate_home_assistant_integration_ingress_bridge_key,
)
from .pcmu_protocol import encode_pcmu_delivery
from .pcmu_subscriptions import PcmuPacketDelivery
from .state import RadioStateSnapshot
from .tui_controls import HoldScope, hold_selection
from .web_auth import (
    WebDashboardAuthentication,
    WebDashboardAuthenticationMiddleware,
)
from .web_theme_runtime import (
    WebThemeRuntimeRegistry,
    build_web_theme_runtime,
    read_web_theme_stylesheet,
)
from .web_themes import WebThemeError

WEB_DASHBOARD_API_PROTOCOL = "sdsctl.web"
WEB_DASHBOARD_API_VERSION = 1
WEB_DASHBOARD_UNAVAILABLE_DETAIL = "The scanner daemon is unavailable."
WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT = "172.30.32.2"
WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_FORBIDDEN_DETAIL = (
    "Home Assistant Ingress access is required."
)

_WEB_ASSET_PACKAGE = "sds200.web_assets"
_WEB_STYLESHEET_CASCADE_LAYERS = (
    "sdsctl-viewport-contract, sdsctl-shared, sdsctl-managed-theme"
)
_WEB_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; "
        "style-src 'self'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "media-src 'self'; "
        "img-src 'self'; "
        "font-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
_API_DOCS_RESPONSE_HEADERS = {
    **_WEB_RESPONSE_HEADERS,
    "Content-Security-Policy": (
        "default-src 'none'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
}
_EVENT_STREAM_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "X-Content-Type-Options": "nosniff",
}
_AUDIO_STREAM_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "X-Content-Type-Options": "nosniff",
}
_WATERFALL_STREAM_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "X-Content-Type-Options": "nosniff",
}
_RECORDING_FILE_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}

WEB_DASHBOARD_RECORDING_BUSY_DETAIL = (
    "A daemon recording is already active or awaiting finalization."
)
WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL = (
    "Daemon recording is currently unavailable."
)
WEB_DASHBOARD_RECORDING_FAILED_DETAIL = (
    "The daemon recording operation could not be completed."
)
WEB_DASHBOARD_RECORDING_INVALID_IDENTIFIER_DETAIL = (
    "The recording identifier is invalid."
)
WEB_DASHBOARD_RECORDING_NOT_FOUND_DETAIL = "The recording was not found."
WEB_DASHBOARD_RECORDING_NOT_PLAYABLE_DETAIL = (
    "The recording is not playable."
)
WEB_DASHBOARD_CONTROL_BUSY_DETAIL = (
    "Another scanner control is already in progress."
)
WEB_DASHBOARD_CONTROL_UNAVAILABLE_DETAIL = (
    "Scanner control is currently unavailable."
)
WEB_DASHBOARD_CONTROL_SELECTION_UNAVAILABLE_DETAIL = (
    "The current scanner selection is unavailable for this control."
)
WEB_DASHBOARD_CONTROL_TIMEOUT_DETAIL = "The scanner control timed out."
WEB_DASHBOARD_CONTROL_REJECTED_DETAIL = "The scanner rejected the control."
WEB_DASHBOARD_CONTROL_INVALID_DETAIL = "The scanner control request is invalid."
WEB_DASHBOARD_CONTROL_FAILED_DETAIL = (
    "The scanner control could not be completed."
)

logger = logging.getLogger(__name__)


class DaemonApiClientLike(Protocol):
    """Minimum daemon API client contract required by the web service."""

    def hello(self) -> Mapping[str, object]:
        """Return negotiated daemon capabilities."""

    def runtime_snapshot(self) -> Mapping[str, object]:
        """Return one authoritative daemon runtime snapshot."""

    def hold_state(
        self,
        scope: str,
        held: bool,
        *,
        timeout: float = 4.0,
    ) -> Mapping[str, object]:
        """Set one daemon-owned semantic scanner hold state."""

    def next(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> Mapping[str, object]:
        """Move forward through one daemon-owned scanner selection list."""

    def previous(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> Mapping[str, object]:
        """Move backward through one daemon-owned scanner selection list."""

    def reconnect(
        self,
        *,
        timeout: float = 2.0,
    ) -> Mapping[str, object]:
        """Complete one bounded daemon-owned scanner reconnect."""

    def recording_status(self) -> Mapping[str, object]:
        """Return the daemon-owned recording state."""

    def recording_start(self) -> Mapping[str, object]:
        """Start one daemon-owned recording."""

    def recording_stop(self) -> Mapping[str, object]:
        """Stop and finalize the active daemon-owned recording."""

    def recordings_list(self) -> Mapping[str, object]:
        """Return the finalized daemon recording inventory."""


class DaemonApiClientContext(Protocol):
    """Context-managed daemon API client."""

    def __enter__(self) -> DaemonApiClientLike:
        """Open and return the daemon client."""

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        """Close the daemon client."""


class DaemonEventClientLike(Protocol):
    """Minimum ordered daemon event-client contract required by the web service."""

    def receive(self) -> DaemonEvent:
        """Receive one validated ordered daemon event."""

    def close(self) -> None:
        """Close the daemon event connection."""


class DaemonPcmuClientLike(Protocol):
    """Minimum daemon PCMU client contract required by the web service."""

    max_endpoint_bytes: int
    max_frame_bytes: int

    def connect(self) -> object:
        """Connect to the daemon PCMU service without waiting for audio."""

    def receive(self) -> PcmuPacketDelivery:
        """Receive one validated PCMU delivery."""

    def close(self) -> None:
        """Close the daemon PCMU connection."""


class DaemonRecordingFileClientLike(Protocol):
    """Minimum daemon recording-file client contract for the web service."""

    def open(self, identifier: str) -> DaemonRecordingFileDownload:
        """Open one finalized recording by inventory-relative identifier."""


class DaemonWaterfallClientLike(Protocol):
    """Minimum validated daemon waterfall client contract for the web service."""

    def receive(self) -> DaemonWaterfallRecord:
        """Receive one validated, ordered daemon waterfall record."""

    def close(self) -> None:
        """Close the daemon waterfall connection and release its demand."""


DaemonApiClientFactory: TypeAlias = Callable[[], DaemonApiClientContext]
DaemonEventClientFactory: TypeAlias = Callable[[], DaemonEventClientLike]
DaemonPcmuClientFactory: TypeAlias = Callable[[], DaemonPcmuClientLike]
DaemonRecordingFileClientFactory: TypeAlias = Callable[
    [],
    DaemonRecordingFileClientLike,
]
DaemonWaterfallClientFactory: TypeAlias = Callable[
    [],
    DaemonWaterfallClientLike,
]
_DaemonQuery: TypeAlias = Callable[
    [DaemonApiClientLike],
    Mapping[str, object],
]


class _HomeAssistantIngressMiddleware:
    "Restrict and frame-enable the explicit Home Assistant Ingress mode."

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        client = scope.get("client")
        if (
            client is None
            or client[0] != WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT
        ):
            response = JSONResponse(
                {
                    "detail": (
                        WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_FORBIDDEN_DETAIL
                    )
                },
                status_code=403,
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
            await response(scope, receive, send)
            return

        async def ingress_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                if "x-frame-options" in headers:
                    del headers["x-frame-options"]
                content_security_policy = headers.get(
                    "content-security-policy"
                )
                if content_security_policy is not None:
                    headers["content-security-policy"] = (
                        content_security_policy.replace(
                            "frame-ancestors 'none'",
                            "frame-ancestors 'self'",
                        )
                    )
            await send(message)

        await self._app(scope, receive, ingress_send)


def create_web_dashboard_app(
    api_client_factory: DaemonApiClientFactory,
    event_client_factory: DaemonEventClientFactory | None = None,
    pcmu_client_factory: DaemonPcmuClientFactory | None = None,
    recording_file_client_factory: (
        DaemonRecordingFileClientFactory | None
    ) = None,
    waterfall_client_factory: DaemonWaterfallClientFactory | None = None,
    *,
    home_assistant_ingress: bool = False,
    lan_authentication: WebDashboardAuthentication | None = None,
    managed_theme_root: Path | None = None,
) -> FastAPI:
    """Create the daemon-backed web application without scanner ownership."""

    if not callable(api_client_factory):
        raise TypeError("Daemon API client factory must be callable.")
    if event_client_factory is not None and not callable(event_client_factory):
        raise TypeError("Daemon event client factory must be callable or None.")
    if pcmu_client_factory is not None and not callable(pcmu_client_factory):
        raise TypeError("Daemon PCMU client factory must be callable or None.")
    if (
        recording_file_client_factory is not None
        and not callable(recording_file_client_factory)
    ):
        raise TypeError(
            "Daemon recording-file client factory must be callable or None."
        )
    if (
        waterfall_client_factory is not None
        and not callable(waterfall_client_factory)
    ):
        raise TypeError(
            "Daemon waterfall client factory must be callable or None."
        )
    if type(home_assistant_ingress) is not bool:
        raise TypeError(
            "Home Assistant Ingress setting must be boolean."
        )
    if lan_authentication is not None and not isinstance(
        lan_authentication,
        WebDashboardAuthentication,
    ):
        raise TypeError(
            "LAN authentication must be WebDashboardAuthentication or None."
        )

    web_theme_runtime = build_web_theme_runtime(managed_theme_root)
    if web_theme_runtime.ignored_managed_entries:
        logger.warning(
            "Ignored %d invalid or unavailable managed theme entries.",
            web_theme_runtime.ignored_managed_entries,
        )
    if home_assistant_ingress and lan_authentication is not None:
        raise ValueError(
            "Home Assistant Ingress and LAN authentication are mutually exclusive."
        )

    app = FastAPI(
        title="sdsctl web dashboard",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    if home_assistant_ingress:
        app.add_middleware(_HomeAssistantIngressMiddleware)
    if lan_authentication is not None:
        app.add_middleware(
            WebDashboardAuthenticationMiddleware,
            authentication=lan_authentication,
        )

    @app.get(
        "/",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def index() -> HTMLResponse:
        return HTMLResponse(
            content=_dashboard_shell(
                web_theme_runtime,
                home_assistant_ingress,
            ),
            headers=dict(_WEB_RESPONSE_HEADERS),
        )

    @app.get(
        "/assets/dashboard.css",
        include_in_schema=False,
        response_class=Response,
    )
    def stylesheet(sdsctl_source: str | None = None) -> Response:
        if sdsctl_source is None:
            return Response(
                content=(
                    f"@layer {_WEB_STYLESHEET_CASCADE_LAYERS};\n"
                    '@import url("dashboard.css?sdsctl_source=1") '
                    "layer(sdsctl-shared);\n"
                ),
                media_type="text/css",
                headers=dict(_WEB_RESPONSE_HEADERS),
            )
        if sdsctl_source != "1":
            raise HTTPException(
                status_code=404,
                detail="Stylesheet source not found.",
                headers=dict(_WEB_RESPONSE_HEADERS),
            )
        return _asset_response("dashboard.css", media_type="text/css")

    @app.get(
        "/assets/dashboard-viewport.css",
        include_in_schema=False,
        response_class=Response,
    )
    def viewport_stylesheet() -> Response:
        return _asset_response("dashboard-viewport.css", media_type="text/css")

    @app.get(
        "/assets/theme-bootstrap.js",
        include_in_schema=False,
        response_class=Response,
    )
    def theme_bootstrap_script() -> Response:
        return Response(
            content=_theme_bootstrap_script(web_theme_runtime),
            media_type="application/javascript",
            headers=dict(_WEB_RESPONSE_HEADERS),
        )

    @app.get(
        "/assets/themes/{theme_id}/{asset_name}",
        include_in_schema=False,
        response_class=Response,
    )
    def theme_stylesheet(
        theme_id: str,
        asset_name: str,
        sdsctl_source: str | None = None,
    ) -> Response:
        try:
            asset = web_theme_runtime.require_asset(theme_id)
        except WebThemeError as exc:
            raise HTTPException(
                status_code=404,
                detail="Theme not found.",
                headers=dict(_WEB_RESPONSE_HEADERS),
            ) from exc
        if asset_name != asset.manifest.stylesheet:
            raise HTTPException(
                status_code=404,
                detail="Theme asset not found.",
                headers=dict(_WEB_RESPONSE_HEADERS),
            )
        try:
            content = read_web_theme_stylesheet(asset)
        except WebThemeError as exc:
            raise HTTPException(
                status_code=404,
                detail="Theme asset not found.",
                headers=dict(_WEB_RESPONSE_HEADERS),
            ) from exc
        response_content: bytes | str
        if asset.origin == "built-in":
            if sdsctl_source is not None:
                raise HTTPException(
                    status_code=404,
                    detail="Theme asset not found.",
                    headers=dict(_WEB_RESPONSE_HEADERS),
                )
            response_content = content
        else:
            if asset.package_sha256 is None:
                raise HTTPException(
                    status_code=404,
                    detail="Theme asset not found.",
                    headers=dict(_WEB_RESPONSE_HEADERS),
                )
            if sdsctl_source is None:
                response_content = (
                    f"@layer {_WEB_STYLESHEET_CASCADE_LAYERS};\n"
                    f'@import url("{asset_name}?sdsctl_source='
                    f'{asset.package_sha256}") layer(sdsctl-managed-theme);\n'
                )
            elif sdsctl_source == asset.package_sha256:
                response_content = content
            else:
                raise HTTPException(
                    status_code=404,
                    detail="Theme asset not found.",
                    headers=dict(_WEB_RESPONSE_HEADERS),
                )
        return Response(
            content=response_content,
            media_type="text/css",
            headers=dict(_WEB_RESPONSE_HEADERS),
        )

    @app.get(
        "/assets/dashboard.js",
        include_in_schema=False,
        response_class=Response,
    )
    def script() -> Response:
        return _asset_response(
            "dashboard.js",
            media_type="application/javascript",
        )

    @app.get(
        "/assets/audio-worklet.js",
        include_in_schema=False,
        response_class=Response,
    )
    def audio_worklet_script() -> Response:
        return _asset_response(
            "audio-worklet.js",
            media_type="application/javascript",
        )

    @app.get(
        "/assets/favicon.svg",
        include_in_schema=False,
        response_class=Response,
    )
    def favicon() -> Response:
        return _asset_response(
            "favicon.svg",
            media_type="image/svg+xml",
        )

    @app.get(
        "/api/v1/docs",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def swagger_ui() -> HTMLResponse:
        return _api_docs_response("api-docs-swagger.html")

    @app.get(
        "/api/v1/redoc",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def redoc() -> HTMLResponse:
        return _api_docs_response("api-docs-redoc.html")

    @app.get(
        "/assets/api-docs/swagger-ui.css",
        include_in_schema=False,
        response_class=Response,
    )
    def swagger_ui_stylesheet() -> Response:
        return _asset_response(
            "vendor/swagger-ui-5.32.11/swagger-ui.css",
            media_type="text/css",
        )

    @app.get(
        "/assets/api-docs/swagger-ui-bundle.js",
        include_in_schema=False,
        response_class=Response,
    )
    def swagger_ui_bundle() -> Response:
        return _asset_response(
            "vendor/swagger-ui-5.32.11/swagger-ui-bundle.js",
            media_type="application/javascript",
        )

    @app.get(
        "/assets/api-docs/swagger-ui-init.js",
        include_in_schema=False,
        response_class=Response,
    )
    def swagger_ui_init() -> Response:
        return _asset_response(
            "api-docs-swagger.js",
            media_type="application/javascript",
        )

    @app.get(
        "/assets/api-docs/redoc.standalone.js",
        include_in_schema=False,
        response_class=Response,
    )
    def redoc_bundle() -> Response:
        return _asset_response(
            "vendor/redoc-2.5.3/redoc.standalone.js",
            media_type="application/javascript",
        )

    @app.get(
        "/assets/api-docs/redoc-init.js",
        include_in_schema=False,
        response_class=Response,
    )
    def redoc_init() -> Response:
        return _asset_response(
            "api-docs-redoc.js",
            media_type="application/javascript",
        )

    @app.get("/api/v1")
    def api_index() -> dict[str, object]:
        links = {
            "audio": "/api/v1/audio",
            "dashboard": "/",
            "docs": "/api/v1/docs",
            "events": "/api/v1/events",
            "health": "/healthz",
            "openapi": "/api/v1/openapi.json",
            "recording": "/api/v1/recording",
            "recordings": "/api/v1/recordings",
            "recording_file": "/api/v1/recordings/file/{identifier}",
            "redoc": "/api/v1/redoc",
            "scanner_hold": "/api/v1/scanner/hold/{scope}",
            "scanner_next": "/api/v1/scanner/next",
            "scanner_next_scope": "/api/v1/scanner/next/{scope}",
            "scanner_previous": "/api/v1/scanner/previous",
            "scanner_previous_scope": "/api/v1/scanner/previous/{scope}",
            "scanner_reconnect": "/api/v1/scanner/reconnect",
            "snapshot": "/api/v1/snapshot",
            "status": "/api/v1/status",
            "waterfall": "/api/v1/waterfall",
        }
        if home_assistant_ingress:
            links["home_assistant_integration"] = (
                "/api/v1/home-assistant/integration"
            )
        return {
            "service": _service_metadata(),
            "links": links,
        }

    @app.get("/healthz")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": _service_metadata(),
        }

    if home_assistant_ingress:

        @app.get("/api/v1/home-assistant/integration")
        def home_assistant_integration_status() -> JSONResponse:
            return _home_assistant_integration_response(
                home_assistant_integration_ingress_status,
            )

        @app.post("/api/v1/home-assistant/integration/{action}")
        def home_assistant_integration_action(
            action: HomeAssistantIntegrationAction,
            payload: Annotated[object, Body()],
        ) -> JSONResponse:
            confirmation = _home_assistant_integration_confirmation(payload)
            return _home_assistant_integration_response(
                lambda: execute_home_assistant_integration_ingress_action(
                    action,
                    confirmation_digest=confirmation,
                ),
            )

        @app.post("/api/v1/home-assistant/integration/bridge-key/reveal")
        def home_assistant_integration_bridge_key_reveal() -> JSONResponse:
            return _home_assistant_integration_response(
                reveal_home_assistant_integration_bridge_key,
            )

        @app.post("/api/v1/home-assistant/integration/bridge-key/rotate")
        def home_assistant_integration_bridge_key_rotate(
            payload: Annotated[object, Body()],
        ) -> JSONResponse:
            confirmation = _home_assistant_integration_confirmation(payload)
            return _home_assistant_integration_response(
                lambda: rotate_home_assistant_integration_ingress_bridge_key(
                    confirmation_digest=confirmation,
                ),
            )

    @app.get("/api/v1/status")
    def status() -> dict[str, object]:
        return {
            **_api_envelope(),
            "daemon": _query_daemon(api_client_factory, _daemon_status),
        }

    @app.get("/api/v1/snapshot")
    def snapshot() -> dict[str, object]:
        return {
            **_api_envelope(),
            "snapshot": _query_daemon(api_client_factory, _daemon_snapshot),
        }

    @app.post("/api/v1/scanner/hold/{scope}")
    def scanner_hold(
        scope: Literal["system", "department", "site", "channel"],
        payload: Annotated[object | None, Body()] = None,
    ) -> dict[str, object]:
        held = _web_hold_state_held(payload)
        return {
            **_api_envelope(),
            "control": _query_scanner_control(
                api_client_factory,
                DaemonApiOperation.SCANNER_HOLD_STATE,
                lambda client: client.hold_state(scope, held),
            ),
        }

    @app.post("/api/v1/scanner/next")
    def scanner_next() -> dict[str, object]:
        return _scanner_navigation_response(
            api_client_factory,
            "next",
            "channel",
        )

    @app.post("/api/v1/scanner/next/{scope}")
    def scanner_next_scope(scope: HoldScope) -> dict[str, object]:
        return _scanner_navigation_response(api_client_factory, "next", scope)

    @app.post("/api/v1/scanner/previous")
    def scanner_previous() -> dict[str, object]:
        return _scanner_navigation_response(
            api_client_factory,
            "previous",
            "channel",
        )

    @app.post("/api/v1/scanner/previous/{scope}")
    def scanner_previous_scope(scope: HoldScope) -> dict[str, object]:
        return _scanner_navigation_response(api_client_factory, "previous", scope)

    @app.post("/api/v1/scanner/reconnect")
    def scanner_reconnect() -> dict[str, object]:
        return {
            **_api_envelope(),
            "control": _query_scanner_control(
                api_client_factory,
                DaemonApiOperation.SCANNER_RECONNECT,
                lambda client: client.reconnect(),
            ),
        }

    @app.get("/api/v1/recording")
    def recording_status() -> dict[str, object]:
        return {
            **_api_envelope(),
            "recording": _query_recording_daemon(
                api_client_factory,
                lambda client: client.recording_status(),
            ),
        }

    @app.post("/api/v1/recording/start")
    def recording_start() -> dict[str, object]:
        return {
            **_api_envelope(),
            "recording": _query_recording_daemon(
                api_client_factory,
                lambda client: client.recording_start(),
            ),
        }

    @app.post("/api/v1/recording/stop")
    def recording_stop() -> dict[str, object]:
        return {
            **_api_envelope(),
            "recording": _query_recording_daemon(
                api_client_factory,
                lambda client: client.recording_stop(),
            ),
        }

    @app.get("/api/v1/recordings")
    def recordings() -> dict[str, object]:
        return {
            **_api_envelope(),
            "recordings": _query_recording_daemon(
                api_client_factory,
                lambda client: client.recordings_list(),
            ),
        }

    @app.get(
        "/api/v1/recordings/file/{identifier:path}",
        responses={
            200: {
                "content": {"audio/wav": {}},
                "description": "Finalized daemon recording",
            },
        },
    )
    def recording_file(identifier: str) -> StreamingResponse:
        return _recording_file_response(
            recording_file_client_factory,
            identifier,
        )

    @app.get(
        "/api/v1/events",
        responses={
            200: {
                "content": {"text/event-stream": {}},
                "description": "Ordered daemon events",
            },
        },
    )
    def events() -> StreamingResponse:
        return _event_stream_response(event_client_factory)

    @app.get(
        "/api/v1/audio",
        responses={
            200: {
                "content": {"application/octet-stream": {}},
                "description": "Daemon-owned PCMU audio frames",
            },
        },
    )
    def audio() -> StreamingResponse:
        return _audio_stream_response(pcmu_client_factory)

    @app.get(
        "/api/v1/waterfall",
        responses={
            200: {
                "content": {
                    "application/x-ndjson": {},
                    "text/event-stream": {},
                },
                "description": "Validated ordered daemon waterfall records",
            },
        },
    )
    def waterfall(request: Request) -> StreamingResponse:
        return _waterfall_stream_response(
            waterfall_client_factory,
            server_sent_events=(
                "text/event-stream"
                in request.headers.get("accept", "").lower()
            ),
        )

    return app


@cache
def _read_web_asset(name: str) -> str:
    return (
        files(_WEB_ASSET_PACKAGE)
        .joinpath(*name.split("/"))
        .read_text(encoding="utf-8")
    )


def _asset_response(name: str, *, media_type: str) -> Response:
    return Response(
        content=_read_web_asset(name),
        media_type=media_type,
        headers=dict(_WEB_RESPONSE_HEADERS),
    )


def _home_assistant_integration_confirmation(payload: object) -> str:
    if not isinstance(payload, dict) or set(payload) != {"confirm"}:
        raise HTTPException(
            status_code=422,
            detail="An exact confirmation digest is required.",
            headers=dict(_WEB_RESPONSE_HEADERS),
        )
    confirmation = payload.get("confirm")
    if not isinstance(confirmation, str):
        raise HTTPException(
            status_code=422,
            detail="An exact confirmation digest is required.",
            headers=dict(_WEB_RESPONSE_HEADERS),
        )
    return confirmation


def _home_assistant_integration_response(
    operation: Callable[[], dict[str, object]],
) -> JSONResponse:
    try:
        payload = operation()
    except (OSError, SDS200Error, ValueError) as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
            headers=dict(_WEB_RESPONSE_HEADERS),
        ) from error
    return JSONResponse(
        payload,
        headers=dict(_WEB_RESPONSE_HEADERS),
    )


@cache
def _home_assistant_integration_tab() -> str:
    return """        <button
          id="pane-tab-home-assistant"
          type="button"
          role="tab"
          aria-selected="false"
          aria-controls="pane-home-assistant"
          tabindex="-1"
          data-workspace-tab="home-assistant"
        >Home Assistant</button>"""


@cache
def _home_assistant_integration_panel() -> str:
    return """      <section
        class="panel home-assistant-integration-panel"
        aria-labelledby="home-assistant-integration-title"
      >
        <header class="panel-header">
          <p class="panel-kicker">Home Assistant Core</p>
          <h2 id="home-assistant-integration-title">Live-audio integration</h2>
        </header>

        <p
          id="home-assistant-integration-message"
          class="home-assistant-integration-message"
          role="status"
          aria-live="polite"
        >Loading integration lifecycle status.</p>

        <dl class="status-list status-list-compact">
          <div>
            <dt>Packaged version</dt>
            <dd id="home-assistant-integration-artifact-version">Checking</dd>
          </div>
          <div>
            <dt>Packaged digest</dt>
            <dd
              id="home-assistant-integration-artifact-digest"
              class="technical-value"
            >Checking</dd>
          </div>
          <div>
            <dt>Installed version</dt>
            <dd id="home-assistant-integration-current-version">Checking</dd>
          </div>
          <div>
            <dt>Installed digest</dt>
            <dd
              id="home-assistant-integration-current-digest"
              class="technical-value"
            >Checking</dd>
          </div>
          <div>
            <dt>Rollback version</dt>
            <dd id="home-assistant-integration-rollback-version">Checking</dd>
          </div>
          <div>
            <dt>Rollback digest</dt>
            <dd
              id="home-assistant-integration-rollback-digest"
              class="technical-value"
            >Checking</dd>
          </div>
          <div>
            <dt>Bridge-key digest</dt>
            <dd
              id="home-assistant-integration-bridge-digest"
              class="technical-value"
            >Checking</dd>
          </div>
        </dl>

        <div class="home-assistant-integration-confirmation">
          <label for="home-assistant-integration-confirm">
            Exact SHA-256 confirmation
          </label>
          <input
            id="home-assistant-integration-confirm"
            class="technical-value"
            type="text"
            inputmode="text"
            autocomplete="off"
            autocapitalize="none"
            spellcheck="false"
          >
          <div class="home-assistant-integration-confirmation-choices">
            <button
              id="home-assistant-integration-use-artifact"
              type="button"
              disabled
            >Use packaged</button>
            <button
              id="home-assistant-integration-use-current"
              type="button"
              disabled
            >Use installed</button>
            <button
              id="home-assistant-integration-use-rollback"
              type="button"
              disabled
            >Use rollback</button>
            <button
              id="home-assistant-integration-use-bridge"
              type="button"
              disabled
            >Use bridge key</button>
          </div>
        </div>

        <div
          class="home-assistant-integration-actions"
          role="group"
          aria-label="Home Assistant integration lifecycle"
        >
          <button id="home-assistant-integration-refresh" type="button">
            Refresh
          </button>
          <button
            id="home-assistant-integration-install"
            type="button"
            data-home-assistant-integration-action="install"
          >Install</button>
          <button
            id="home-assistant-integration-update"
            type="button"
            data-home-assistant-integration-action="update"
          >Update</button>
          <button
            id="home-assistant-integration-rollback"
            type="button"
            data-home-assistant-integration-action="rollback"
          >Rollback</button>
          <button
            id="home-assistant-integration-remove"
            type="button"
            data-home-assistant-integration-action="remove"
          >Remove</button>
          <button
            id="home-assistant-integration-discard-rollback"
            type="button"
            data-home-assistant-integration-action="discard-rollback"
          >Discard rollback</button>
        </div>

        <div class="home-assistant-integration-secret">
          <label for="home-assistant-integration-bridge-key">Bridge key</label>
          <input
            id="home-assistant-integration-bridge-key"
            class="technical-value"
            type="password"
            value=""
            readonly
            autocomplete="off"
          >
          <div class="home-assistant-integration-actions">
            <button id="home-assistant-integration-reveal-key" type="button">
              Reveal key
            </button>
            <button
              id="home-assistant-integration-show-key"
              type="button"
              disabled
            >Show</button>
            <button
              id="home-assistant-integration-copy-key"
              type="button"
              disabled
            >Copy</button>
            <button
              id="home-assistant-integration-rotate-key"
              type="button"
            >Rotate key</button>
          </div>
        </div>

        <p class="home-assistant-integration-guidance">
          Core is never restarted automatically. Restart Core after an install,
          update, rollback, or removal. Restart this App immediately after key
          rotation, then complete integration reauthentication.
        </p>
      </section>"""


@cache
def _home_assistant_integration_pane() -> str:
    return f"""        <section
          id="pane-home-assistant"
          class="workspace-pane"
          role="tabpanel"
          aria-labelledby="pane-tab-home-assistant"
          data-workspace-pane="home-assistant"
          hidden
        >
{_home_assistant_integration_panel()}
        </section>"""


@cache
def _dashboard_shell(
    runtime: WebThemeRuntimeRegistry,
    home_assistant_ingress: bool,
) -> str:
    theme_stylesheet_links = "\n".join(
        (
            f'  <link rel="stylesheet" href="{asset.manifest.stylesheet_url}">'
            if asset.origin == "built-in"
            else (
                '  <link rel="stylesheet" media="not all" '
                f'data-sdsctl-managed-theme="{asset.manifest.identifier}" '
                'data-sdsctl-managed-theme-href="'
                f'{asset.manifest.stylesheet_url}">'
            )
        )
        for asset in runtime.assets
    )
    stylesheet_links = (
        f"{theme_stylesheet_links}\n"
        '  <link rel="stylesheet" href="assets/dashboard-viewport.css">'
    )
    options = "\n".join(
        (
            f'          <option value="{theme.identifier}">'
            f"{escape(theme.label)}</option>"
        )
        for theme in runtime.registry.themes
    )
    return (
        _read_web_asset("dashboard.html")
        .replace(
            'class="workspace-tabs"',
            (
                'class="workspace-tabs workspace-tabs-with-home-assistant"'
                if home_assistant_ingress
                else 'class="workspace-tabs"'
            ),
            1,
        )
        .replace("  <!-- SDSCTL_THEME_STYLES -->", stylesheet_links)
        .replace("          <!-- SDSCTL_THEME_OPTIONS -->", options)
        .replace(
            "        <!-- SDSCTL_HOME_ASSISTANT_TAB -->",
            (
                _home_assistant_integration_tab()
                if home_assistant_ingress
                else ""
            ),
        )
        .replace(
            "        <!-- SDSCTL_HOME_ASSISTANT_PANE -->",
            (
                _home_assistant_integration_pane()
                if home_assistant_ingress
                else ""
            ),
        )
    )


@cache
def _theme_bootstrap_script(runtime: WebThemeRuntimeRegistry) -> str:
    return (
        _read_web_asset("theme-bootstrap.js")
        .replace(
            "__SDSCTL_WEB_THEME_MANIFESTS__",
            runtime.registry.browser_json(),
        )
        .replace(
            "__SDSCTL_MANAGED_WEB_THEME_IDS__",
            json.dumps(
                runtime.managed_identifiers,
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        )
    )


def _api_docs_response(name: str) -> HTMLResponse:
    return HTMLResponse(
        content=_read_web_asset(name),
        headers=dict(_API_DOCS_RESPONSE_HEADERS),
    )


def _service_metadata() -> dict[str, object]:
    return {
        "name": "sdsctl-web",
        "package_version": __version__,
        "protocol": WEB_DASHBOARD_API_PROTOCOL,
        "version": WEB_DASHBOARD_API_VERSION,
    }


def _api_envelope() -> dict[str, object]:
    return {
        "protocol": WEB_DASHBOARD_API_PROTOCOL,
        "version": WEB_DASHBOARD_API_VERSION,
    }


def _query_daemon(
    api_client_factory: DaemonApiClientFactory,
    query: _DaemonQuery,
) -> dict[str, object]:
    try:
        with api_client_factory() as client:
            return dict(query(client))
    except (SDS200Error, OSError) as error:
        logger.warning(
            "web dashboard daemon request failed error_type=%s",
            error.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_UNAVAILABLE_DETAIL,
        ) from None


class _WebControlUnavailableError(RuntimeError):
    pass


class _WebControlSelectionError(RuntimeError):
    pass


def _query_scanner_control(
    api_client_factory: DaemonApiClientFactory,
    operation: DaemonApiOperation,
    query: _DaemonQuery,
) -> dict[str, object]:
    try:
        with api_client_factory() as client:
            _require_web_control_capability(client.hello(), operation)
            return dict(query(client))
    except _WebControlSelectionError:
        raise HTTPException(
            status_code=409,
            detail=WEB_DASHBOARD_CONTROL_SELECTION_UNAVAILABLE_DETAIL,
        ) from None
    except _WebControlUnavailableError:
        raise HTTPException(
            status_code=409,
            detail=WEB_DASHBOARD_CONTROL_UNAVAILABLE_DETAIL,
        ) from None
    except DaemonRequestError as error:
        status_code, detail = _control_request_error_response(error)
        logger.warning(
            "web dashboard scanner control request failed "
            "error_type=%s code=%s",
            error.__class__.__name__,
            error.code,
        )
        raise HTTPException(
            status_code=status_code,
            detail=detail,
        ) from None
    except (SDS200Error, OSError) as error:
        logger.warning(
            "web dashboard scanner control connection failed error_type=%s",
            error.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_CONTROL_UNAVAILABLE_DETAIL,
        ) from None


def _web_hold_state_held(payload: object) -> bool:
    if not isinstance(payload, Mapping) or set(payload) != {"held"}:
        raise HTTPException(
            status_code=400,
            detail=WEB_DASHBOARD_CONTROL_INVALID_DETAIL,
        )
    held = payload.get("held")
    if held is True:
        return True
    if held is False:
        return False
    raise HTTPException(
        status_code=400,
        detail=WEB_DASHBOARD_CONTROL_INVALID_DETAIL,
    )


def _require_web_control_capability(
    hello: Mapping[str, object],
    operation: DaemonApiOperation,
) -> None:
    operations = hello.get("control_operations")
    if (
        hello.get("read_only") is not False
        or not isinstance(operations, list)
        or operation.value not in operations
    ):
        raise _WebControlUnavailableError


def _control_radio_state(
    snapshot: Mapping[str, object],
) -> RadioStateSnapshot:
    radio = snapshot.get("radio_state")
    if not isinstance(radio, Mapping):
        raise _WebControlSelectionError
    return RadioStateSnapshot(
        system_index=_control_index(radio.get("system_index")),
        department_index=_control_index(radio.get("department_index")),
        site_index=_control_index(radio.get("site_index")),
        channel_index=_control_index(radio.get("channel_index")),
        channel_kind=_control_text(radio.get("channel_kind")),
    )


def _control_index(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise _WebControlSelectionError
    return value


def _control_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise _WebControlSelectionError
    return value


def _next_current_selection(
    client: DaemonApiClientLike,
    scope: HoldScope,
) -> Mapping[str, object]:
    selection = hold_selection(
        _control_radio_state(client.runtime_snapshot()),
        scope,
    )
    if selection is None:
        raise _WebControlSelectionError
    return client.next(selection.target, selection.first, selection.second)


def _previous_current_selection(
    client: DaemonApiClientLike,
    scope: HoldScope,
) -> Mapping[str, object]:
    selection = hold_selection(
        _control_radio_state(client.runtime_snapshot()),
        scope,
    )
    if selection is None:
        raise _WebControlSelectionError
    return client.previous(selection.target, selection.first, selection.second)


def _scanner_navigation_response(
    api_client_factory: DaemonApiClientFactory,
    direction: Literal["next", "previous"],
    scope: HoldScope,
) -> dict[str, object]:
    if direction == "next":
        operation = DaemonApiOperation.SCANNER_NEXT
        resolver = _next_current_selection
    else:
        operation = DaemonApiOperation.SCANNER_PREVIOUS
        resolver = _previous_current_selection

    def control(client: DaemonApiClientLike) -> Mapping[str, object]:
        return resolver(client, scope)

    return {
        **_api_envelope(),
        "control": _query_scanner_control(
            api_client_factory,
            operation,
            control,
        ),
    }


def _control_request_error_response(
    error: DaemonRequestError,
) -> tuple[int, str]:
    if error.code == "control_busy":
        return 409, WEB_DASHBOARD_CONTROL_BUSY_DETAIL
    if error.code in {"control_unavailable", "unsupported_operation"}:
        return 409, WEB_DASHBOARD_CONTROL_UNAVAILABLE_DETAIL
    if error.code == "control_timeout":
        return 504, WEB_DASHBOARD_CONTROL_TIMEOUT_DETAIL
    if error.code == "control_rejected":
        return 409, WEB_DASHBOARD_CONTROL_REJECTED_DETAIL
    if error.code == "invalid_parameters":
        return 400, WEB_DASHBOARD_CONTROL_INVALID_DETAIL
    return 503, WEB_DASHBOARD_CONTROL_FAILED_DETAIL


def _query_recording_daemon(
    api_client_factory: DaemonApiClientFactory,
    query: _DaemonQuery,
) -> dict[str, object]:
    try:
        with api_client_factory() as client:
            return dict(query(client))
    except DaemonRequestError as error:
        status_code, detail = _recording_request_error_response(error)
        logger.warning(
            "web dashboard daemon recording request failed error_type=%s code=%s",
            error.__class__.__name__,
            error.code,
        )
        raise HTTPException(
            status_code=status_code,
            detail=detail,
        ) from None
    except (SDS200Error, OSError) as error:
        logger.warning(
            "web dashboard daemon recording connection failed error_type=%s",
            error.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL,
        ) from None


def _recording_request_error_response(
    error: DaemonRequestError,
) -> tuple[int, str]:
    if error.code == "recording_busy":
        return 409, WEB_DASHBOARD_RECORDING_BUSY_DETAIL
    if error.code == "recording_unavailable":
        return 503, WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL
    if error.code == "recording_failed":
        return 503, WEB_DASHBOARD_RECORDING_FAILED_DETAIL
    return 503, WEB_DASHBOARD_RECORDING_FAILED_DETAIL


def _recording_file_response(
    recording_file_client_factory: (
        DaemonRecordingFileClientFactory | None
    ),
    identifier: str,
) -> StreamingResponse:
    if recording_file_client_factory is None:
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL,
        )

    try:
        client = recording_file_client_factory()
        download = client.open(identifier)
    except DaemonRecordingFileRequestError as error:
        status_code, detail = _recording_file_error_response(error.status)
        logger.warning(
            "web dashboard recording-file request failed error_type=%s status=%s",
            error.__class__.__name__,
            error.status.name,
        )
        raise HTTPException(status_code=status_code, detail=detail) from None
    except (SDS200Error, OSError) as error:
        logger.warning(
            "web dashboard recording-file connection failed error_type=%s",
            error.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL,
        ) from None

    return StreamingResponse(
        content=_iter_recording_file(download),
        media_type="audio/wav",
        headers={
            **_RECORDING_FILE_RESPONSE_HEADERS,
            "Content-Length": str(download.content_length),
        },
    )


def _recording_file_error_response(
    status: RecordingFileResponseStatus,
) -> tuple[int, str]:
    if status is RecordingFileResponseStatus.INVALID_IDENTIFIER:
        return 400, WEB_DASHBOARD_RECORDING_INVALID_IDENTIFIER_DETAIL
    if status is RecordingFileResponseStatus.NOT_FOUND:
        return 404, WEB_DASHBOARD_RECORDING_NOT_FOUND_DETAIL
    if status is RecordingFileResponseStatus.NOT_PLAYABLE:
        return 409, WEB_DASHBOARD_RECORDING_NOT_PLAYABLE_DETAIL
    if status is RecordingFileResponseStatus.UNAVAILABLE:
        return 409, WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL
    return 503, WEB_DASHBOARD_RECORDING_FAILED_DETAIL


def _event_stream_response(
    event_client_factory: DaemonEventClientFactory | None,
) -> StreamingResponse:
    if event_client_factory is None:
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_UNAVAILABLE_DETAIL,
        )

    client: DaemonEventClientLike | None = None
    try:
        client = event_client_factory()
        first_event = client.receive()
    except (SDS200Error, OSError) as error:
        if client is not None:
            client.close()
        logger.warning(
            "web dashboard daemon event connection failed error_type=%s",
            error.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_UNAVAILABLE_DETAIL,
        ) from None

    return StreamingResponse(
        content=_iter_daemon_events(client, first_event),
        media_type="text/event-stream",
        headers=dict(_EVENT_STREAM_RESPONSE_HEADERS),
    )


def _audio_stream_response(
    pcmu_client_factory: DaemonPcmuClientFactory | None,
) -> StreamingResponse:
    if pcmu_client_factory is None:
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_UNAVAILABLE_DETAIL,
        )

    client: DaemonPcmuClientLike | None = None
    try:
        client = pcmu_client_factory()
        client.connect()
    except (SDS200Error, OSError) as error:
        if client is not None:
            client.close()
        logger.warning(
            "web dashboard daemon PCMU connection failed error_type=%s",
            error.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_UNAVAILABLE_DETAIL,
        ) from None

    return StreamingResponse(
        content=_iter_daemon_audio(client),
        media_type="application/octet-stream",
        headers=dict(_AUDIO_STREAM_RESPONSE_HEADERS),
    )


def _waterfall_stream_response(
    waterfall_client_factory: DaemonWaterfallClientFactory | None,
    *,
    server_sent_events: bool = False,
) -> StreamingResponse:
    if waterfall_client_factory is None:
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_UNAVAILABLE_DETAIL,
        )

    client: DaemonWaterfallClientLike | None = None
    try:
        client = waterfall_client_factory()
        first_record = client.receive()
    except (SDS200Error, OSError) as error:
        if client is not None:
            client.close()
        logger.warning(
            "web dashboard daemon waterfall connection failed error_type=%s",
            error.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_UNAVAILABLE_DETAIL,
        ) from None

    return StreamingResponse(
        content=_iter_daemon_waterfall(
            client,
            first_record,
            server_sent_events=server_sent_events,
        ),
        media_type=(
            "text/event-stream"
            if server_sent_events
            else "application/x-ndjson"
        ),
        headers=dict(_WATERFALL_STREAM_RESPONSE_HEADERS),
    )


async def _iter_recording_file(
    download: DaemonRecordingFileDownload,
) -> AsyncIterator[bytes]:
    try:
        while not download.closed:
            payload = await asyncio.to_thread(download.read, 64 * 1024)
            if not payload:
                return
            yield payload
    except (SDS200Error, OSError, ValueError) as error:
        logger.warning(
            "web dashboard recording-file stream ended error_type=%s",
            error.__class__.__name__,
        )
    finally:
        download.close()


async def _iter_daemon_events(
    client: DaemonEventClientLike,
    first_event: DaemonEvent,
) -> AsyncIterator[bytes]:
    try:
        yield _encode_server_sent_event(first_event)
        while True:
            event = await asyncio.to_thread(client.receive)
            yield _encode_server_sent_event(event)
    except (SDS200Error, OSError) as error:
        logger.warning(
            "web dashboard daemon event stream ended error_type=%s",
            error.__class__.__name__,
        )
    finally:
        client.close()


async def _iter_daemon_audio(
    client: DaemonPcmuClientLike,
) -> AsyncIterator[bytes]:
    try:
        while True:
            delivery = await asyncio.to_thread(client.receive)
            yield encode_pcmu_delivery(
                delivery,
                max_endpoint_bytes=client.max_endpoint_bytes,
                max_frame_bytes=client.max_frame_bytes,
            )
    except (SDS200Error, OSError) as error:
        logger.warning(
            "web dashboard daemon PCMU stream ended error_type=%s",
            error.__class__.__name__,
        )
    finally:
        client.close()


async def _iter_daemon_waterfall(
    client: DaemonWaterfallClientLike,
    first_record: DaemonWaterfallRecord,
    *,
    server_sent_events: bool = False,
) -> AsyncIterator[bytes]:
    def encode(record: DaemonWaterfallRecord) -> bytes:
        if not server_sent_events:
            return record.to_json_line()
        return (
            f"id: {record.sequence}\ndata: ".encode()
            + record.to_json_line()
            + b"\n"
        )

    try:
        yield encode(first_record)
        while True:
            record = await asyncio.to_thread(client.receive)
            yield encode(record)
    except (SDS200Error, OSError) as error:
        logger.warning(
            "web dashboard daemon waterfall stream ended error_type=%s",
            error.__class__.__name__,
        )
    finally:
        client.close()


def _encode_server_sent_event(event: DaemonEvent) -> bytes:
    payload = json.dumps(
        event.as_dict(),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"id: {event.sequence}\ndata: {payload}\n\n".encode()


def _daemon_status(client: DaemonApiClientLike) -> Mapping[str, object]:
    return {
        "hello": dict(client.hello()),
        "snapshot": dict(client.runtime_snapshot()),
    }


def _daemon_snapshot(client: DaemonApiClientLike) -> Mapping[str, object]:
    client.hello()
    return client.runtime_snapshot()


__all__ = [
    "DaemonApiClientContext",
    "DaemonApiClientFactory",
    "DaemonApiClientLike",
    "DaemonEventClientFactory",
    "DaemonEventClientLike",
    "DaemonPcmuClientFactory",
    "DaemonPcmuClientLike",
    "DaemonRecordingFileClientFactory",
    "DaemonRecordingFileClientLike",
    "DaemonWaterfallClientFactory",
    "DaemonWaterfallClientLike",
    "WEB_DASHBOARD_API_PROTOCOL",
    "WEB_DASHBOARD_API_VERSION",
    "WEB_DASHBOARD_UNAVAILABLE_DETAIL",
    "create_web_dashboard_app",
]
