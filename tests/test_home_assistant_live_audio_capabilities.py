from __future__ import annotations

import pytest

from sds200.home_assistant_live_audio_capabilities import (
    HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_METHOD,
    HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
    HomeAssistantLiveAudioAuthenticationError,
    HomeAssistantLiveAudioCapabilities,
    HomeAssistantLiveAudioCapabilityError,
    HomeAssistantLiveAudioCapacityError,
)

BRIDGE_SECRET = "s" * 43
ROTATED_SECRET = "r" * 43
ORIGIN = "home-assistant-core"
PEER = "172.30.32.1"


def _manager(
    *,
    now: list[float] | None = None,
    token: str = "t" * 43,
    **kwargs: object,
) -> HomeAssistantLiveAudioCapabilities:
    clock = [100.0] if now is None else now
    return HomeAssistantLiveAudioCapabilities(
        BRIDGE_SECRET,
        ORIGIN,
        clock=lambda: clock[0],
        token_factory=lambda: token,
        **kwargs,  # type: ignore[arg-type]
    )


def test_capability_is_one_time_and_bound_to_exact_request() -> None:
    manager = _manager()

    issued = manager.issue(
        bridge_secret=BRIDGE_SECRET,
        origin=ORIGIN,
        peer=PEER,
    )

    assert issued.token == "t" * 43
    assert issued.method == HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_METHOD
    assert issued.path == HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH
    assert issued.expires_in == 30.0
    assert issued.token not in repr(issued)
    assert BRIDGE_SECRET not in repr(manager)

    lease = manager.redeem(
        issued.token,
        method="GET",
        path=HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
        origin=ORIGIN,
        peer=PEER,
    )
    snapshot = manager.snapshot()
    assert snapshot.outstanding == 0
    assert snapshot.active == 1
    assert snapshot.issued == 1
    assert snapshot.redeemed == 1

    with pytest.raises(HomeAssistantLiveAudioAuthenticationError):
        manager.redeem(
            issued.token,
            method="GET",
            path=HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
            origin=ORIGIN,
            peer=PEER,
        )

    lease.release()
    lease.release()
    assert manager.snapshot().active == 0


@pytest.mark.parametrize(
    ("method", "path", "origin", "peer"),
    (
        ("POST", HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH, ORIGIN, PEER),
        ("GET", "/v1/live-audio/other", ORIGIN, PEER),
        ("GET", HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH, "other", PEER),
        ("GET", HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH, ORIGIN, "other"),
    ),
)
def test_wrong_request_binding_does_not_consume_capability(
    method: str,
    path: str,
    origin: str,
    peer: str,
) -> None:
    manager = _manager()
    issued = manager.issue(
        bridge_secret=BRIDGE_SECRET,
        origin=ORIGIN,
        peer=PEER,
    )

    with pytest.raises(HomeAssistantLiveAudioAuthenticationError):
        manager.redeem(
            issued.token,
            method=method,
            path=path,
            origin=origin,
            peer=peer,
        )

    assert manager.snapshot().outstanding == 1
    lease = manager.redeem(
        issued.token,
        method="GET",
        path=HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
        origin=ORIGIN,
        peer=PEER,
    )
    lease.release()


def test_bridge_authentication_fails_closed_without_secret_disclosure() -> None:
    manager = _manager()

    for secret, origin in (
        ("wrong", ORIGIN),
        (BRIDGE_SECRET, "other"),
        ("", ""),
    ):
        with pytest.raises(
            HomeAssistantLiveAudioAuthenticationError,
            match="authentication failed",
        ) as error:
            manager.issue(
                bridge_secret=secret,
                origin=origin,
                peer=PEER,
            )
        if secret:
            assert secret not in str(error.value)

    snapshot = manager.snapshot()
    assert snapshot.rejected == 3
    assert snapshot.issued == 0


def test_expired_and_revoked_capabilities_cannot_be_redeemed() -> None:
    now = [100.0]
    manager = _manager(now=now, lifetime=2.0)
    expired = manager.issue(
        bridge_secret=BRIDGE_SECRET,
        origin=ORIGIN,
        peer=PEER,
    )
    now[0] = 102.0

    with pytest.raises(HomeAssistantLiveAudioAuthenticationError):
        manager.redeem(
            expired.token,
            method="GET",
            path=HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
            origin=ORIGIN,
            peer=PEER,
        )
    assert manager.snapshot().expired == 1

    manager = _manager(token="u" * 43)
    revoked = manager.issue(
        bridge_secret=BRIDGE_SECRET,
        origin=ORIGIN,
        peer=PEER,
    )
    assert manager.revoke(revoked.token)
    assert not manager.revoke(revoked.token)
    with pytest.raises(HomeAssistantLiveAudioAuthenticationError):
        manager.redeem(
            revoked.token,
            method="GET",
            path=HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
            origin=ORIGIN,
            peer=PEER,
        )


def test_outstanding_and_active_capacity_are_independently_bounded() -> None:
    tokens = iter(("a" * 43, "b" * 43, "c" * 43))
    manager = HomeAssistantLiveAudioCapabilities(
        BRIDGE_SECRET,
        ORIGIN,
        max_outstanding=2,
        max_active=1,
        token_factory=lambda: next(tokens),
    )
    first = manager.issue(
        bridge_secret=BRIDGE_SECRET,
        origin=ORIGIN,
        peer=PEER,
    )
    second = manager.issue(
        bridge_secret=BRIDGE_SECRET,
        origin=ORIGIN,
        peer=PEER,
    )
    with pytest.raises(HomeAssistantLiveAudioCapacityError):
        manager.issue(
            bridge_secret=BRIDGE_SECRET,
            origin=ORIGIN,
            peer=PEER,
        )

    active = manager.redeem(
        first.token,
        method="GET",
        path=HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
        origin=ORIGIN,
        peer=PEER,
    )
    with pytest.raises(HomeAssistantLiveAudioCapacityError):
        manager.redeem(
            second.token,
            method="GET",
            path=HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
            origin=ORIGIN,
            peer=PEER,
        )
    assert manager.snapshot().outstanding == 1
    active.release()
    second_active = manager.redeem(
        second.token,
        method="GET",
        path=HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
        origin=ORIGIN,
        peer=PEER,
    )
    second_active.release()


def test_secret_rotation_revokes_outstanding_but_not_active_lease() -> None:
    tokens = iter(("a" * 43, "b" * 43, "c" * 43))
    manager = HomeAssistantLiveAudioCapabilities(
        BRIDGE_SECRET,
        ORIGIN,
        token_factory=lambda: next(tokens),
    )
    active_token = manager.issue(
        bridge_secret=BRIDGE_SECRET,
        origin=ORIGIN,
        peer=PEER,
    )
    active = manager.redeem(
        active_token.token,
        method="GET",
        path=HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
        origin=ORIGIN,
        peer=PEER,
    )
    outstanding = manager.issue(
        bridge_secret=BRIDGE_SECRET,
        origin=ORIGIN,
        peer=PEER,
    )

    manager.rotate_bridge_secret(ROTATED_SECRET)

    assert manager.snapshot().active == 1
    assert manager.snapshot().outstanding == 0
    with pytest.raises(HomeAssistantLiveAudioAuthenticationError):
        manager.issue(
            bridge_secret=BRIDGE_SECRET,
            origin=ORIGIN,
            peer=PEER,
        )
    with pytest.raises(HomeAssistantLiveAudioAuthenticationError):
        manager.redeem(
            outstanding.token,
            method="GET",
            path=HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
            origin=ORIGIN,
            peer=PEER,
        )
    manager.issue(
        bridge_secret=ROTATED_SECRET,
        origin=ORIGIN,
        peer=PEER,
    )
    active.release()


@pytest.mark.parametrize("token", ("short", "x" * 129, "!" * 43))
def test_malformed_tokens_fail_without_entering_storage(token: str) -> None:
    manager = _manager()
    with pytest.raises(HomeAssistantLiveAudioAuthenticationError):
        manager.redeem(
            token,
            method="GET",
            path=HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH,
            origin=ORIGIN,
            peer=PEER,
        )


def test_duplicate_or_malformed_factory_tokens_fail_closed() -> None:
    manager = _manager(token="short")
    with pytest.raises(
        HomeAssistantLiveAudioCapabilityError,
        match="creation failed",
    ):
        manager.issue(
            bridge_secret=BRIDGE_SECRET,
            origin=ORIGIN,
            peer=PEER,
        )
