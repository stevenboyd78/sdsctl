from __future__ import annotations

import asyncio
import importlib.util
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ARTIFACT_ROOT = (
    Path(__file__).parents[1]
    / "src"
    / "sds200"
    / "home_assistant_integration"
    / "custom_components"
    / "sdsctl"
)
PACKAGE = "_sdsctl_artifact_runtime_test"


def _reset_package() -> None:
    for name in tuple(sys.modules):
        if name == PACKAGE or name.startswith(f"{PACKAGE}."):
            del sys.modules[name]
    package = ModuleType(PACKAGE)
    package.__path__ = [str(ARTIFACT_ROOT)]  # type: ignore[attr-defined]
    sys.modules[PACKAGE] = package


def _load(name: str) -> ModuleType:
    qualified = f"{PACKAGE}.{name}"
    spec = importlib.util.spec_from_file_location(qualified, ARTIFACT_ROOT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


def _install_aiohttp_stub() -> None:
    aiohttp = ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientResponse:
        pass

    class ClientSession:
        pass

    class ClientTimeout:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    aiohttp.ClientError = ClientError  # type: ignore[attr-defined]
    aiohttp.ClientResponse = ClientResponse  # type: ignore[attr-defined]
    aiohttp.ClientSession = ClientSession  # type: ignore[attr-defined]
    aiohttp.ClientTimeout = ClientTimeout  # type: ignore[attr-defined]
    sys.modules["aiohttp"] = aiohttp


def test_client_accepts_only_one_app_alias_and_exact_protocol_shape() -> None:
    _reset_package()
    _install_aiohttp_stub()
    const = _load("const")
    client = _load("client")

    assert client.normalize_app_host("local-sds200-29-2") == "local-sds200-29-2"
    for rejected in (
        "http://local-sds200",
        "192.168.0.18",
        "local_sds200",
        " Local-sds200",
        "local-sds200/path",
    ):
        with pytest.raises(ValueError):
            client.normalize_app_host(rejected)

    compatibility = client._parse_compatibility(
        {
            "version": 1,
            "application_version": "0.24.0",
            "format": dict(const.EXPECTED_FORMAT),
        }
    )
    assert compatibility.protocol_version == 1
    assert compatibility.mime_type == "audio/mpeg"

    token = "a" * 43
    assert (
        client._parse_capability(
            {
                "version": 1,
                "format": dict(const.EXPECTED_FORMAT),
                "capability": {
                    "token": token,
                    "method": "GET",
                    "path": "/v1/live-audio/stream",
                    "expires_in": 30,
                },
            }
        )
        == token
    )
    for mutation in (
        {"version": 2},
        {"format": {**const.EXPECTED_FORMAT, "mime_type": "audio/wav"}},
        {"capability": {"token": token, "method": "POST"}},
    ):
        payload: dict[str, Any] = {
            "version": 1,
            "format": dict(const.EXPECTED_FORMAT),
            "capability": {
                "token": token,
                "method": "GET",
                "path": "/v1/live-audio/stream",
                "expires_in": 30,
            },
        }
        payload.update(mutation)
        with pytest.raises(client.SdsctlCompatibilityError):
            client._parse_capability(payload)


def _install_media_source_stubs() -> None:
    homeassistant = ModuleType("homeassistant")
    components = ModuleType("homeassistant.components")
    http = ModuleType("homeassistant.components.http")
    http_auth = ModuleType("homeassistant.components.http.auth")
    media_player = ModuleType("homeassistant.components.media_player")
    media_source = ModuleType("homeassistant.components.media_source")
    core = ModuleType("homeassistant.core")

    class BrowseError(RuntimeError):
        pass

    class Unresolvable(RuntimeError):
        pass

    class MediaClass:
        APP = "app"
        CHANNEL = "channel"

    class MediaType:
        APP = "app"

    class MediaSource:
        def __init__(self, domain: str) -> None:
            self.domain = domain

    @dataclass
    class PlayMedia:
        url: str
        mime_type: str

    @dataclass
    class MediaSourceItem:
        identifier: str

    class BrowseMediaSource:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)
            domain = kwargs["domain"]
            identifier = kwargs.get("identifier")
            self.media_content_id = f"media-source://{domain}"
            if identifier:
                self.media_content_id += f"/{identifier}"

    class HomeAssistant:
        pass

    def async_sign_path(hass: object, path: str, expires: timedelta) -> str:
        assert hass is not None
        assert path == "/api/sdsctl/media-source-artwork"
        assert expires.total_seconds() == 86_400
        return f"{path}?authSig=test-signature"

    media_player.BrowseError = BrowseError  # type: ignore[attr-defined]
    media_player.MediaClass = MediaClass  # type: ignore[attr-defined]
    media_player.MediaType = MediaType  # type: ignore[attr-defined]
    for name, value in (
        ("BrowseMediaSource", BrowseMediaSource),
        ("MediaSource", MediaSource),
        ("MediaSourceItem", MediaSourceItem),
        ("PlayMedia", PlayMedia),
        ("Unresolvable", Unresolvable),
    ):
        setattr(media_source, name, value)
    core.HomeAssistant = HomeAssistant  # type: ignore[attr-defined]
    http_auth.async_sign_path = async_sign_path  # type: ignore[attr-defined]
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.http": http,
            "homeassistant.components.http.auth": http_auth,
            "homeassistant.components.media_player": media_player,
            "homeassistant.components.media_source": media_source,
            "homeassistant.core": core,
        }
    )


def test_media_source_resolves_only_live_to_a_core_relative_url() -> None:
    _reset_package()
    _install_media_source_stubs()
    _load("const")
    playback = _load("playback")
    media_source = _load("media_source")

    registry = playback.PlaybackRegistry(token_factory=lambda: "p" * 43)
    hass = SimpleNamespace(data={"sdsctl": {"runtime": SimpleNamespace(playbacks=registry)}})
    source = media_source.SdsctlMediaSource(hass)

    resolved = asyncio.run(
        source.async_resolve_media(SimpleNamespace(identifier="live"))
    )
    assert resolved.url == f"/api/sdsctl/live/{'p' * 43}"
    assert resolved.mime_type == "audio/mpeg"
    assert "local-sds200" not in resolved.url
    assert "8100" not in resolved.url

    with pytest.raises(media_source.Unresolvable, match="Unknown sdsctl media item"):
        asyncio.run(source.async_resolve_media(SimpleNamespace(identifier=None)))

    for root_identifier in (None, ""):
        root = asyncio.run(
            source.async_browse_media(
                SimpleNamespace(identifier=root_identifier)
            )
        )
        assert root.can_play is False
        assert root.thumbnail == (
            "/api/sdsctl/media-source-artwork?authSig=test-signature"
        )
        assert root.children[0].media_content_id == "media-source://sdsctl/live"
        assert root.children[0].can_play is True
        assert root.children[0].thumbnail == (
            "/api/sdsctl/media-source-artwork?authSig=test-signature"
        )

    with pytest.raises(media_source.BrowseError, match="Unknown sdsctl media item"):
        asyncio.run(source.async_browse_media(SimpleNamespace(identifier="other")))


def _install_http_stubs() -> tuple[type[Exception], type[Exception]]:
    class ClientError(Exception):
        pass

    class StreamResponse:
        def __init__(self, headers: dict[str, str] | None = None) -> None:
            self.headers = headers or {}
            self.content_type: str | None = None
            self.prepared = False
            self.chunks: list[bytes] = []

        async def prepare(self, request: object) -> None:
            del request
            self.prepared = True

        async def write(self, chunk: bytes) -> None:
            self.chunks.append(chunk)

    class FileResponse:
        def __init__(self, path: Path, headers: dict[str, str] | None = None) -> None:
            self.path = path
            self.headers = headers or {}

    class HttpError(Exception):
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class HTTPServiceUnavailable(HttpError):
        pass

    class HTTPGone(HttpError):
        pass

    aiohttp = ModuleType("aiohttp")
    aiohttp.ClientError = ClientError  # type: ignore[attr-defined]
    aiohttp.web = SimpleNamespace(  # type: ignore[attr-defined]
        Request=object,
        FileResponse=FileResponse,
        StreamResponse=StreamResponse,
        HTTPServiceUnavailable=HTTPServiceUnavailable,
        HTTPGone=HTTPGone,
    )

    homeassistant = ModuleType("homeassistant")
    components = ModuleType("homeassistant.components")
    http_package = ModuleType("homeassistant.components.http")
    http_view = ModuleType("homeassistant.components.http.view")

    class HomeAssistantView:
        pass

    http_package.KEY_HASS = "hass"  # type: ignore[attr-defined]
    http_view.HomeAssistantView = HomeAssistantView  # type: ignore[attr-defined]
    sys.modules.update(
        {
            "aiohttp": aiohttp,
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.http": http_package,
            "homeassistant.components.http.view": http_view,
        }
    )
    return HTTPGone, HTTPServiceUnavailable


def test_artwork_view_is_authenticated_and_serves_the_canonical_packaged_logo() -> None:
    _reset_package()
    _install_http_stubs()
    _load("const")

    client_module = ModuleType(f"{PACKAGE}.client")
    client_module.SdsctlClientError = RuntimeError  # type: ignore[attr-defined]
    sys.modules[f"{PACKAGE}.client"] = client_module
    http = _load("http")

    view = http.SdsctlMediaSourceArtworkView()
    response = asyncio.run(view.get(SimpleNamespace()))

    assert view.url == "/api/sdsctl/media-source-artwork"
    assert view.requires_auth is True
    assert response.path == ARTIFACT_ROOT / "sdsctl-logo.svg"
    assert response.path.read_bytes() == (
        Path(__file__).parents[1] / "docs" / "assets" / "sdsctl-logo.svg"
    ).read_bytes()
    assert response.headers["Cache-Control"] == "private, max-age=86400"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_http_view_redeems_once_streams_and_releases_only_its_lease() -> None:
    _reset_package()
    http_gone, _http_unavailable = _install_http_stubs()
    _load("const")
    playback = _load("playback")

    client_module = ModuleType(f"{PACKAGE}.client")

    class SdsctlClientError(RuntimeError):
        pass

    client_module.SdsctlClientError = SdsctlClientError  # type: ignore[attr-defined]
    sys.modules[f"{PACKAGE}.client"] = client_module
    http = _load("http")

    class Content:
        async def iter_chunked(self, size: int):
            assert size == 16_384
            for chunk in (b"mp3-a", b"", b"mp3-b"):
                yield chunk

    upstream = SimpleNamespace(content=Content())

    class Client:
        def __init__(self) -> None:
            self.opens = 0
            self.releases = 0

        async def async_open_stream(self):
            self.opens += 1
            return upstream

        def release_stream(self, response: object) -> None:
            assert response is upstream
            self.releases += 1

    client = Client()
    registry = playback.PlaybackRegistry(token_factory=lambda: "q" * 43)
    token = registry.issue()
    hass = SimpleNamespace(
        data={"sdsctl": {"runtime": SimpleNamespace(playbacks=registry, client=client)}}
    )
    request = SimpleNamespace(app={"hass": hass}, transport=None)

    response = asyncio.run(http.SdsctlLiveAudioView().get(request, token))
    assert response.content_type == "audio/mpeg"
    assert response.chunks == [b"mp3-a", b"mp3-b"]
    assert client.opens == 1
    assert client.releases == 1
    assert registry.snapshot().active == 0

    with pytest.raises(http_gone):
        asyncio.run(http.SdsctlLiveAudioView().get(request, token))
    assert client.opens == 1


def test_http_view_stops_upstream_when_downstream_transport_is_closing() -> None:
    _reset_package()
    _install_http_stubs()
    _load("const")
    playback = _load("playback")

    client_module = ModuleType(f"{PACKAGE}.client")

    class SdsctlClientError(RuntimeError):
        pass

    client_module.SdsctlClientError = SdsctlClientError  # type: ignore[attr-defined]
    sys.modules[f"{PACKAGE}.client"] = client_module
    http = _load("http")

    class Content:
        async def iter_chunked(self, size: int):
            assert size == 16_384
            yield b"mp3-a"

    upstream = SimpleNamespace(content=Content())

    class Client:
        def __init__(self) -> None:
            self.releases = 0

        async def async_open_stream(self):
            return upstream

        def release_stream(self, response: object) -> None:
            assert response is upstream
            self.releases += 1

    client = Client()
    registry = playback.PlaybackRegistry(token_factory=lambda: "r" * 43)
    token = registry.issue()
    hass = SimpleNamespace(
        data={"sdsctl": {"runtime": SimpleNamespace(playbacks=registry, client=client)}}
    )
    transport = SimpleNamespace(is_closing=lambda: True)
    request = SimpleNamespace(app={"hass": hass}, transport=transport)

    response = asyncio.run(http.SdsctlLiveAudioView().get(request, token))

    assert response.chunks == []
    assert client.releases == 1
    assert registry.snapshot().active == 0
