"""Bounded Home Assistant-owned live-audio playback URLs."""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from .const import (
    PLAYBACK_LIFETIME_SECONDS,
    PLAYBACK_MAX_ACTIVE,
    PLAYBACK_MAX_OUTSTANDING,
)


class PlaybackUnavailable(RuntimeError):
    """Raised with a deliberately redacted bounded-playback failure."""

    def __init__(self) -> None:
        super().__init__("Live scanner audio is unavailable.")


@dataclass(frozen=True, slots=True)
class PlaybackSnapshot:
    """Low-rate lifecycle evidence without URL credentials or audio data."""

    outstanding: int
    active: int
    issued: int
    redeemed: int
    rejected: int
    expired: int
    closed: bool


class PlaybackLease:
    """One independently releasable Core proxy playback slot."""

    def __init__(self, registry: PlaybackRegistry) -> None:
        self._registry = registry
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._registry._release_active()


class PlaybackRegistry:
    """Issue one-time expiring identifiers used only in Home Assistant URLs."""

    def __init__(
        self,
        *,
        lifetime: float = PLAYBACK_LIFETIME_SECONDS,
        max_outstanding: int = PLAYBACK_MAX_OUTSTANDING,
        max_active: int = PLAYBACK_MAX_ACTIVE,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if lifetime <= 0 or max_outstanding <= 0 or max_active <= 0:
            raise ValueError("Playback registry limits must be greater than zero.")
        self._lifetime = float(lifetime)
        self._max_outstanding = int(max_outstanding)
        self._max_active = int(max_active)
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._records: dict[bytes, float] = {}
        self._active = 0
        self._issued = 0
        self._redeemed = 0
        self._rejected = 0
        self._expired = 0
        self._closed = False

    def issue(self) -> str:
        now = self._clock()
        self._prune(now)
        if self._closed or len(self._records) >= self._max_outstanding:
            self._rejected += 1
            raise PlaybackUnavailable
        token = self._token_factory()
        digest = _token_digest(token)
        if digest is None or digest in self._records:
            self._rejected += 1
            raise PlaybackUnavailable
        self._records[digest] = now + self._lifetime
        self._issued += 1
        return token

    def redeem(self, token: str) -> PlaybackLease:
        now = self._clock()
        self._prune(now)
        digest = _token_digest(token)
        expires_at = None if digest is None else self._records.get(digest)
        if (
            self._closed
            or expires_at is None
            or now >= expires_at
            or self._active >= self._max_active
        ):
            self._rejected += 1
            raise PlaybackUnavailable
        assert digest is not None
        del self._records[digest]
        self._active += 1
        self._redeemed += 1
        return PlaybackLease(self)

    def close(self) -> None:
        self._closed = True
        self._records.clear()

    def snapshot(self) -> PlaybackSnapshot:
        self._prune(self._clock())
        return PlaybackSnapshot(
            outstanding=len(self._records),
            active=self._active,
            issued=self._issued,
            redeemed=self._redeemed,
            rejected=self._rejected,
            expired=self._expired,
            closed=self._closed,
        )

    def _prune(self, now: float) -> None:
        expired = tuple(
            digest for digest, expires_at in self._records.items() if now >= expires_at
        )
        for digest in expired:
            del self._records[digest]
        self._expired += len(expired)

    def _release_active(self) -> None:
        if self._active <= 0:
            raise RuntimeError("Playback lease underflow.")
        self._active -= 1


def _token_digest(value: object) -> bytes | None:
    if not isinstance(value, str) or not 43 <= len(value) <= 128:
        return None
    if value.strip() != value or not value.isascii():
        return None
    if any(not (character.isalnum() or character in "-_") for character in value):
        return None
    return hashlib.sha256(value.encode("ascii")).digest()
