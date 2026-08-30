from __future__ import annotations

from collections import deque

from fastapi.testclient import TestClient

from sds200.home_assistant_live_audio import LiveAudioLeaseClosed
from sds200.home_assistant_live_audio_capabilities import (
    HomeAssistantLiveAudioCapabilities,
)
from sds200.home_assistant_live_audio_service import (
    HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_ISSUE_PATH,
    HOME_ASSISTANT_LIVE_AUDIO_COMPATIBILITY_PATH,
    HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER,
    create_home_assistant_live_audio_service,
)

BRIDGE_SECRET = "s" * 43
ORIGIN = "home-assistant-core"


class FakeLease:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = deque(chunks)
        self.closed = False

    def get(self, timeout: float | None = None) -> bytes:
        assert timeout == 0.01
        if self.chunks:
            return self.chunks.popleft()
        raise LiveAudioLeaseClosed("client_closed")

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, chunks: tuple[bytes, ...] = (b"mp3-a", b"mp3-b")) -> None:
        self.chunks = chunks
        self.leases: list[FakeLease] = []
        self.error: RuntimeError | None = None

    def subscribe(self) -> FakeLease:
        if self.error is not None:
            raise self.error
        lease = FakeLease(self.chunks)
        self.leases.append(lease)
        return lease


def _client(
    *,
    session: FakeSession | None = None,
    max_active: int = 4,
) -> tuple[TestClient, HomeAssistantLiveAudioCapabilities, FakeSession]:
    selected_session = session or FakeSession()
    capabilities = HomeAssistantLiveAudioCapabilities(
        BRIDGE_SECRET,
        ORIGIN,
        max_active=max_active,
    )
    app = create_home_assistant_live_audio_service(
        capabilities,
        selected_session,
        read_timeout=0.01,
    )
    return TestClient(app), capabilities, selected_session


def _issue(client: TestClient) -> str:
    response = client.post(
        HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_ISSUE_PATH,
        headers={
            "Authorization": f"Bearer {BRIDGE_SECRET}",
            HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER: ORIGIN,
        },
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    payload = response.json()
    assert payload["format"] == {
        "container": "MP3",
        "codec": "MP3 (MPEG audio layer 3)",
        "mime_type": "audio/mpeg",
        "sample_rate": 44_100,
        "channels": 1,
        "bit_rate": 64_000,
        "seekable": False,
        "duration_seconds": None,
    }
    capability = payload["capability"]
    assert capability["method"] == "GET"
    assert capability["path"] == "/v1/live-audio/stream"
    assert capability["expires_in"] == 30.0
    return capability["token"]


def test_private_service_reports_authenticated_compatibility_without_issuing() -> None:
    client, capabilities, _session = _client()

    response = client.get(
        HOME_ASSISTANT_LIVE_AUDIO_COMPATIBILITY_PATH,
        headers={
            "Authorization": f"Bearer {BRIDGE_SECRET}",
            HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER: ORIGIN,
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    payload = response.json()
    assert payload["version"] == 1
    assert isinstance(payload["application_version"], str)
    assert payload["application_version"]
    assert payload["format"]["mime_type"] == "audio/mpeg"
    assert payload["format"]["seekable"] is False
    assert payload["format"]["duration_seconds"] is None
    assert capabilities.snapshot().issued == 0


def test_compatibility_requires_exact_private_identity() -> None:
    client, _capabilities, _session = _client()

    response = client.get(
        HOME_ASSISTANT_LIVE_AUDIO_COMPATIBILITY_PATH,
        headers={
            "Authorization": "Bearer wrong",
            HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER: ORIGIN,
        },
    )

    assert response.status_code == 401
    assert BRIDGE_SECRET not in response.text


def test_private_service_issues_and_redeems_one_stream() -> None:
    client, capabilities, session = _client()
    token = _issue(client)

    response = client.get(
        "/v1/live-audio/stream",
        headers={
            "Authorization": f"Bearer {token}",
            HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER: ORIGIN,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.content == b"mp3-amp3-b"
    assert len(session.leases) == 1
    assert session.leases[0].closed
    snapshot = capabilities.snapshot()
    assert snapshot.redeemed == 1
    assert snapshot.active == 0
    assert snapshot.outstanding == 0

    replay = client.get(
        "/v1/live-audio/stream",
        headers={
            "Authorization": f"Bearer {token}",
            HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER: ORIGIN,
        },
    )
    assert replay.status_code == 401
    assert token not in replay.text


def test_issue_requires_exact_bearer_scheme_secret_and_origin() -> None:
    client, capabilities, _session = _client()

    for headers in (
        {},
        {
            "Authorization": f"bearer {BRIDGE_SECRET}",
            HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER: ORIGIN,
        },
        {
            "Authorization": "Bearer wrong",
            HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER: ORIGIN,
        },
        {
            "Authorization": f"Bearer {BRIDGE_SECRET}",
            HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER: "other",
        },
    ):
        response = client.post(
            HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_ISSUE_PATH,
            headers=headers,
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert BRIDGE_SECRET not in response.text

    assert capabilities.snapshot().issued == 0


def test_stream_rejects_wrong_origin_and_preserves_capability() -> None:
    client, capabilities, _session = _client()
    token = _issue(client)

    rejected = client.get(
        "/v1/live-audio/stream",
        headers={
            "Authorization": f"Bearer {token}",
            HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER: "other",
        },
    )
    assert rejected.status_code == 401
    assert capabilities.snapshot().outstanding == 1

    accepted = client.get(
        "/v1/live-audio/stream",
        headers={
            "Authorization": f"Bearer {token}",
            HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER: ORIGIN,
        },
    )
    assert accepted.status_code == 200


def test_audio_capacity_failure_releases_redeemed_playback_slot() -> None:
    session = FakeSession()
    session.error = RuntimeError("full")
    client, capabilities, _session = _client(session=session, max_active=1)
    token = _issue(client)

    response = client.get(
        "/v1/live-audio/stream",
        headers={
            "Authorization": f"Bearer {token}",
            HOME_ASSISTANT_LIVE_AUDIO_ORIGIN_HEADER: ORIGIN,
        },
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert capabilities.snapshot().active == 0
    assert capabilities.snapshot().outstanding == 0


def test_private_service_does_not_expose_docs_or_accept_other_methods() -> None:
    client, _capabilities, _session = _client()

    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.post("/v1/live-audio/stream").status_code == 405
    assert client.get(HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_ISSUE_PATH).status_code == 405
