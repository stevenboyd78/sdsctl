"""Bounded reconnect policy for authenticated remote daemon consumers."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .daemon_remote_client import (
    DaemonRemoteClientError,
    DaemonRemoteClientErrorReason,
)
from .exceptions import DaemonDisconnectedError

DAEMON_REMOTE_RECONNECT_DEFAULT_ATTEMPTS = 5
DAEMON_REMOTE_RECONNECT_DEFAULT_INITIAL_DELAY = 0.25
DAEMON_REMOTE_RECONNECT_DEFAULT_MAX_DELAY = 2.0


@dataclass(frozen=True, slots=True)
class DaemonRemoteReconnectPolicy:
    """Describe one finite exponential reconnect sequence."""

    attempts: int = DAEMON_REMOTE_RECONNECT_DEFAULT_ATTEMPTS
    initial_delay: float = DAEMON_REMOTE_RECONNECT_DEFAULT_INITIAL_DELAY
    max_delay: float = DAEMON_REMOTE_RECONNECT_DEFAULT_MAX_DELAY

    def __post_init__(self) -> None:
        if type(self.attempts) is not int:
            raise TypeError("Remote daemon reconnect attempts must be an integer.")
        if self.attempts < 0:
            raise ValueError("Remote daemon reconnect attempts must not be negative.")
        initial = _nonnegative_finite_delay(
            self.initial_delay,
            label="initial",
        )
        maximum = _nonnegative_finite_delay(
            self.max_delay,
            label="maximum",
        )
        if maximum < initial:
            raise ValueError(
                "Remote daemon maximum reconnect delay must not be less than "
                "the initial delay."
            )
        object.__setattr__(self, "initial_delay", initial)
        object.__setattr__(self, "max_delay", maximum)

    def delay(self, attempt: int) -> float:
        """Return the bounded delay before one one-based reconnect attempt."""

        if type(attempt) is not int:
            raise TypeError("Remote daemon reconnect attempt must be an integer.")
        if attempt <= 0 or attempt > self.attempts:
            raise ValueError(
                "Remote daemon reconnect attempt is outside the configured bound."
            )
        multiplier = float(2 ** (attempt - 1))
        return min(self.initial_delay * multiplier, self.max_delay)


def daemon_remote_error_is_reconnectable(error: BaseException) -> bool:
    """Return whether a remote stream may retry one transport-only failure."""

    if isinstance(error, DaemonDisconnectedError):
        return True
    return (
        isinstance(error, DaemonRemoteClientError)
        and error.reason is DaemonRemoteClientErrorReason.CONNECT_FAILED
    )


def _nonnegative_finite_delay(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"Remote daemon {label} reconnect delay must be a number."
        )
    normalized = float(value)
    if not isfinite(normalized) or normalized < 0:
        raise ValueError(
            f"Remote daemon {label} reconnect delay must be finite and "
            "non-negative."
        )
    return normalized


__all__ = [
    "DAEMON_REMOTE_RECONNECT_DEFAULT_ATTEMPTS",
    "DAEMON_REMOTE_RECONNECT_DEFAULT_INITIAL_DELAY",
    "DAEMON_REMOTE_RECONNECT_DEFAULT_MAX_DELAY",
    "DaemonRemoteReconnectPolicy",
    "daemon_remote_error_is_reconnectable",
]
