from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from hmac import compare_digest
from math import isfinite

HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_METHOD = "GET"
HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH = "/v1/live-audio/stream"
HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_CAPABILITY_LIFETIME = 30.0
HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_MAX_OUTSTANDING_CAPABILITIES = 16
HOME_ASSISTANT_LIVE_AUDIO_MINIMUM_BRIDGE_SECRET_CHARACTERS = 43
HOME_ASSISTANT_LIVE_AUDIO_MINIMUM_CAPABILITY_TOKEN_CHARACTERS = 43
HOME_ASSISTANT_LIVE_AUDIO_MAXIMUM_CAPABILITY_TOKEN_CHARACTERS = 128

_TOKEN_CHARACTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


class HomeAssistantLiveAudioCapabilityError(RuntimeError):
    """Base class for deliberately redacted capability failures."""


class HomeAssistantLiveAudioAuthenticationError(HomeAssistantLiveAudioCapabilityError):
    """Raised when the private Core-to-App identity is not accepted."""

    def __init__(self) -> None:
        super().__init__("Live-audio authentication failed.")


class HomeAssistantLiveAudioCapacityError(HomeAssistantLiveAudioCapabilityError):
    """Raised when a bounded capability or playback limit is reached."""

    def __init__(self) -> None:
        super().__init__("Live-audio playback capacity is unavailable.")


@dataclass(frozen=True, slots=True)
class HomeAssistantLiveAudioCapability:
    """One opaque, short-lived credential returned only to the Core bridge."""

    token: str = field(repr=False)
    method: str = HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_METHOD
    path: str = HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH
    expires_in: float = HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_CAPABILITY_LIFETIME


@dataclass(frozen=True, slots=True)
class HomeAssistantLiveAudioCapabilitySnapshot:
    """Low-rate capability evidence without authentication material."""

    outstanding: int
    active: int
    maximum_outstanding: int
    maximum_active: int
    issued: int
    redeemed: int
    rejected: int
    expired: int
    revoked: int
    secret_rotations: int


@dataclass(frozen=True, slots=True)
class _CapabilityRecord:
    expires_at: float
    origin: str
    peer: str


class HomeAssistantLiveAudioCapabilityLease:
    """One redeemed playback slot released independently of the audio lease."""

    def __init__(
        self,
        manager: HomeAssistantLiveAudioCapabilities,
    ) -> None:
        self._manager = manager
        self._lock = threading.Lock()
        self._released = False

    @property
    def released(self) -> bool:
        with self._lock:
            return self._released

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._manager._release_active()

    def __enter__(self) -> HomeAssistantLiveAudioCapabilityLease:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.release()


class HomeAssistantLiveAudioCapabilities:
    """Issue and redeem bounded, one-time Core-to-App playback capabilities."""

    def __init__(
        self,
        bridge_secret: str,
        origin: str,
        *,
        lifetime: float = HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_CAPABILITY_LIFETIME,
        max_outstanding: int = (HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_MAX_OUTSTANDING_CAPABILITIES),
        max_active: int = 4,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._bridge_secret_digest = _bridge_secret_digest(bridge_secret)
        self._origin = _require_identity(origin, label="live-audio origin")
        self._lifetime = _positive_finite(
            lifetime,
            label="Live-audio capability lifetime",
        )
        self._max_outstanding = _positive_integer(
            max_outstanding,
            label="Maximum outstanding live-audio capabilities",
        )
        self._max_active = _positive_integer(
            max_active,
            label="Maximum active live-audio playbacks",
        )
        if not callable(clock):
            raise TypeError("Live-audio capability clock must be callable.")
        if token_factory is not None and not callable(token_factory):
            raise TypeError("Live-audio capability token factory must be callable or None.")
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._records: dict[bytes, _CapabilityRecord] = {}
        self._active = 0
        self._issued = 0
        self._redeemed = 0
        self._rejected = 0
        self._expired = 0
        self._revoked = 0
        self._secret_rotations = 0
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(origin={self._origin!r}, "
            f"lifetime={self._lifetime!r}, "
            f"max_outstanding={self._max_outstanding!r}, "
            f"max_active={self._max_active!r})"
        )

    @property
    def origin(self) -> str:
        return self._origin

    def authenticate_bridge(self, secret: str, origin: str) -> None:
        candidate = _candidate_bridge_secret_digest(secret)
        accepted = (
            candidate is not None
            and compare_digest(self._bridge_secret_digest, candidate)
            and compare_digest(self._origin, origin)
        )
        if accepted:
            return
        with self._lock:
            self._rejected += 1
        raise HomeAssistantLiveAudioAuthenticationError

    def issue(
        self,
        *,
        bridge_secret: str,
        origin: str,
        peer: str,
    ) -> HomeAssistantLiveAudioCapability:
        self.authenticate_bridge(bridge_secret, origin)
        bound_peer = _require_identity(peer, label="live-audio peer")
        now = self._clock()

        with self._lock:
            self._prune_locked(now)
            if len(self._records) >= self._max_outstanding:
                self._rejected += 1
                raise HomeAssistantLiveAudioCapacityError

            token = self._token_factory()
            digest = _token_digest(token)
            if digest is None or digest in self._records:
                self._rejected += 1
                raise HomeAssistantLiveAudioCapabilityError(
                    "Live-audio capability creation failed."
                )
            self._records[digest] = _CapabilityRecord(
                expires_at=now + self._lifetime,
                origin=self._origin,
                peer=bound_peer,
            )
            self._issued += 1

        return HomeAssistantLiveAudioCapability(
            token=token,
            expires_in=self._lifetime,
        )

    def redeem(
        self,
        token: str,
        *,
        method: str,
        path: str,
        origin: str,
        peer: str,
    ) -> HomeAssistantLiveAudioCapabilityLease:
        digest = _token_digest(token)
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            record = None if digest is None else self._records.get(digest)
            accepted = (
                record is not None
                and method == HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_METHOD
                and path == HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH
                and compare_digest(record.origin, origin)
                and compare_digest(record.peer, peer)
            )
            if not accepted:
                self._rejected += 1
                raise HomeAssistantLiveAudioAuthenticationError
            if self._active >= self._max_active:
                self._rejected += 1
                raise HomeAssistantLiveAudioCapacityError

            assert digest is not None
            del self._records[digest]
            self._active += 1
            self._redeemed += 1
        return HomeAssistantLiveAudioCapabilityLease(self)

    def revoke(self, token: str) -> bool:
        digest = _token_digest(token)
        if digest is None:
            return False
        with self._lock:
            if self._records.pop(digest, None) is None:
                return False
            self._revoked += 1
            return True

    def rotate_bridge_secret(self, bridge_secret: str) -> None:
        digest = _bridge_secret_digest(bridge_secret)
        with self._lock:
            self._bridge_secret_digest = digest
            self._revoked += len(self._records)
            self._records.clear()
            self._secret_rotations += 1

    def snapshot(self) -> HomeAssistantLiveAudioCapabilitySnapshot:
        with self._lock:
            self._prune_locked(self._clock())
            return HomeAssistantLiveAudioCapabilitySnapshot(
                outstanding=len(self._records),
                active=self._active,
                maximum_outstanding=self._max_outstanding,
                maximum_active=self._max_active,
                issued=self._issued,
                redeemed=self._redeemed,
                rejected=self._rejected,
                expired=self._expired,
                revoked=self._revoked,
                secret_rotations=self._secret_rotations,
            )

    def _prune_locked(self, now: float) -> None:
        expired = tuple(
            digest for digest, record in self._records.items() if now >= record.expires_at
        )
        for digest in expired:
            del self._records[digest]
        self._expired += len(expired)

    def _release_active(self) -> None:
        with self._lock:
            if self._active <= 0:
                raise RuntimeError("Live-audio capability lease underflow.")
            self._active -= 1


def _bridge_secret_digest(value: str) -> bytes:
    if not isinstance(value, str):
        raise TypeError("Home Assistant bridge secret must be a string.")
    if len(value) < HOME_ASSISTANT_LIVE_AUDIO_MINIMUM_BRIDGE_SECRET_CHARACTERS:
        raise ValueError(
            "Home Assistant bridge secret must contain at least "
            f"{HOME_ASSISTANT_LIVE_AUDIO_MINIMUM_BRIDGE_SECRET_CHARACTERS} "
            "characters."
        )
    if value.strip() != value or "\x00" in value:
        raise ValueError("Home Assistant bridge secret has an invalid shape.")
    return hashlib.sha256(value.encode("utf-8")).digest()


def _candidate_bridge_secret_digest(value: object) -> bytes | None:
    if not isinstance(value, str):
        return None
    if not value or len(value) > 512 or "\x00" in value:
        return None
    return hashlib.sha256(value.encode("utf-8")).digest()


def _token_digest(value: object) -> bytes | None:
    if not isinstance(value, str):
        return None
    if not (
        HOME_ASSISTANT_LIVE_AUDIO_MINIMUM_CAPABILITY_TOKEN_CHARACTERS
        <= len(value)
        <= HOME_ASSISTANT_LIVE_AUDIO_MAXIMUM_CAPABILITY_TOKEN_CHARACTERS
    ):
        return None
    if any(character not in _TOKEN_CHARACTERS for character in value):
        return None
    return hashlib.sha256(value.encode("ascii")).digest()


def _require_identity(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string.")
    if not value or value.strip() != value or "\x00" in value:
        raise ValueError(f"{label} has an invalid shape.")
    if len(value) > 512:
        raise ValueError(f"{label} is too long.")
    return value


def _positive_finite(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{label} must be finite and greater than zero.")
    return normalized


def _positive_integer(value: object, *, label: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return value


__all__ = [
    "HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_METHOD",
    "HOME_ASSISTANT_LIVE_AUDIO_CAPABILITY_PATH",
    "HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_CAPABILITY_LIFETIME",
    "HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_MAX_OUTSTANDING_CAPABILITIES",
    "HOME_ASSISTANT_LIVE_AUDIO_MAXIMUM_CAPABILITY_TOKEN_CHARACTERS",
    "HOME_ASSISTANT_LIVE_AUDIO_MINIMUM_BRIDGE_SECRET_CHARACTERS",
    "HOME_ASSISTANT_LIVE_AUDIO_MINIMUM_CAPABILITY_TOKEN_CHARACTERS",
    "HomeAssistantLiveAudioAuthenticationError",
    "HomeAssistantLiveAudioCapabilities",
    "HomeAssistantLiveAudioCapability",
    "HomeAssistantLiveAudioCapabilityError",
    "HomeAssistantLiveAudioCapabilityLease",
    "HomeAssistantLiveAudioCapabilitySnapshot",
    "HomeAssistantLiveAudioCapacityError",
]
