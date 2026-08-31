"""Private, fail-closed Home Assistant Core-to-App live-audio client."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout

from .const import (
    CAPABILITY_ISSUE_PATH,
    CAPABILITY_STREAM_PATH,
    COMPATIBILITY_PATH,
    CORE_ORIGIN,
    EXPECTED_FORMAT,
    ORIGIN_HEADER,
    PROTOCOL_VERSION,
)

_APP_HOST_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.ASCII,
)
_MAX_CONTROL_RESPONSE_BYTES = 16_384
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$", re.ASCII)


class SdsctlClientError(RuntimeError):
    """Base class for redacted App bridge errors."""

    def __init__(self) -> None:
        super().__init__("The sdsctl App live-audio service is unavailable.")


class SdsctlAuthenticationError(SdsctlClientError):
    """The configured bridge credential was rejected."""


class SdsctlCompatibilityError(SdsctlClientError):
    """The App and custom integration do not share the exact protocol."""


class SdsctlConnectionError(SdsctlClientError):
    """The exact configured App endpoint did not answer in time."""


@dataclass(frozen=True, slots=True)
class AppCompatibility:
    """Strict, redacted compatibility evidence returned by the App."""

    application_version: str
    protocol_version: int
    mime_type: str


def normalize_app_host(value: object) -> str:
    """Accept one explicit Home Assistant App DNS alias, never a URL or IP."""

    if not isinstance(value, str):
        raise ValueError("App hostname must be a string.")
    normalized = value.strip().lower()
    if normalized != value or not _APP_HOST_PATTERN.fullmatch(normalized):
        raise ValueError("App hostname must be one Home Assistant internal DNS label.")
    return normalized


def normalize_app_port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("App port must be an integer.")
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("App port must be an integer.") from error
    if not 1 <= port <= 65_535:
        raise ValueError("App port must be between 1 and 65535.")
    return port


def normalize_bridge_key(value: object) -> str:
    if not isinstance(value, str) or not 43 <= len(value) <= 512:
        raise ValueError("Bridge key has an invalid shape.")
    if value.strip() != value or "\x00" in value or not value.isascii():
        raise ValueError("Bridge key has an invalid shape.")
    return value


class SdsctlAppClient:
    """Use only one operator-selected App alias and its private capability API."""

    def __init__(
        self,
        session: ClientSession,
        app_host: str,
        app_port: int,
        bridge_key: str,
    ) -> None:
        self._session = session
        self.app_host = normalize_app_host(app_host)
        self.app_port = normalize_app_port(app_port)
        self._bridge_key = normalize_bridge_key(bridge_key)
        self._base_url = f"http://{self.app_host}:{self.app_port}"
        self._active: set[ClientResponse] = set()
        self._closed = False

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(app_host={self.app_host!r}, "
            f"app_port={self.app_port!r})"
        )

    async def async_check_compatibility(self) -> AppCompatibility:
        response = await self._request_control("GET", COMPATIBILITY_PATH)
        try:
            payload = await _read_json_object(response)
        finally:
            response.release()
        return _parse_compatibility(payload)

    async def async_open_stream(self) -> ClientResponse:
        if self._closed:
            raise SdsctlConnectionError
        issue = await self._request_control("POST", CAPABILITY_ISSUE_PATH)
        try:
            payload = await _read_json_object(issue)
        finally:
            issue.release()
        token = _parse_capability(payload)
        try:
            response = await self._session.get(
                f"{self._base_url}{CAPABILITY_STREAM_PATH}",
                headers={
                    "Authorization": f"Bearer {token}",
                    ORIGIN_HEADER: CORE_ORIGIN,
                    "Accept": str(EXPECTED_FORMAT["mime_type"]),
                },
                allow_redirects=False,
                timeout=ClientTimeout(total=None, connect=5, sock_read=30),
            )
        except (ClientError, TimeoutError) as error:
            raise SdsctlConnectionError from error
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
        if response.status == 401:
            response.release()
            raise SdsctlAuthenticationError
        if response.status != 200 or content_type != EXPECTED_FORMAT["mime_type"]:
            response.release()
            raise SdsctlConnectionError
        self._active.add(response)
        return response

    def release_stream(self, response: ClientResponse) -> None:
        self._active.discard(response)
        response.release()

    def close(self) -> None:
        self._closed = True
        for response in tuple(self._active):
            response.close()
        self._active.clear()

    async def _request_control(self, method: str, path: str) -> ClientResponse:
        if self._closed:
            raise SdsctlConnectionError
        try:
            response = await self._session.request(
                method,
                f"{self._base_url}{path}",
                headers={
                    "Authorization": f"Bearer {self._bridge_key}",
                    ORIGIN_HEADER: CORE_ORIGIN,
                    "Accept": "application/json",
                },
                allow_redirects=False,
                timeout=ClientTimeout(total=5),
            )
        except (ClientError, TimeoutError) as error:
            raise SdsctlConnectionError from error
        if response.status == 401:
            response.release()
            raise SdsctlAuthenticationError
        if response.status != 200:
            response.release()
            raise SdsctlConnectionError
        return response


async def _read_json_object(response: ClientResponse) -> dict[str, Any]:
    declared = response.content_length
    if declared is not None and declared > _MAX_CONTROL_RESPONSE_BYTES:
        raise SdsctlCompatibilityError
    body = await response.content.read(_MAX_CONTROL_RESPONSE_BYTES + 1)
    if len(body) > _MAX_CONTROL_RESPONSE_BYTES:
        raise SdsctlCompatibilityError
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SdsctlCompatibilityError from error
    if not isinstance(payload, dict):
        raise SdsctlCompatibilityError
    return payload


def _parse_compatibility(payload: dict[str, Any]) -> AppCompatibility:
    if payload.get("version") != PROTOCOL_VERSION:
        raise SdsctlCompatibilityError
    application_version = payload.get("application_version")
    if not isinstance(application_version, str) or not application_version:
        raise SdsctlCompatibilityError
    if payload.get("format") != EXPECTED_FORMAT:
        raise SdsctlCompatibilityError
    return AppCompatibility(
        application_version=application_version,
        protocol_version=PROTOCOL_VERSION,
        mime_type=str(EXPECTED_FORMAT["mime_type"]),
    )


def _parse_capability(payload: dict[str, Any]) -> str:
    _parse_compatibility(
        {
            "version": payload.get("version"),
            "application_version": "capability-response",
            "format": payload.get("format"),
        }
    )
    capability = payload.get("capability")
    if not isinstance(capability, dict):
        raise SdsctlCompatibilityError
    token = capability.get("token")
    expires_in = capability.get("expires_in")
    if (
        not isinstance(token, str)
        or not _TOKEN_PATTERN.fullmatch(token)
        or capability.get("method") != "GET"
        or capability.get("path") != CAPABILITY_STREAM_PATH
        or isinstance(expires_in, bool)
        or not isinstance(expires_in, (int, float))
        or not 0 < float(expires_in) <= 30
    ):
        raise SdsctlCompatibilityError
    return token
