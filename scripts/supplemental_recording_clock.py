#!/usr/bin/env python3
"""Read-only host BOOTTIME / native MONOTONIC deadline binding.

No host plan is enabled by this component. The host helper and native actors
must independently have the same qualified time namespace. A serialized clock
sample is not a fresh observation, and neither conversion nor a native timer
replaces the independent suspend-aware host recovery deadline.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from uuid import UUID

NS = 1_000_000_000
MAX_SKEW_NS = 5_000_000  # Reject a descheduled/wide sampling interval.
MAX_LEASE_NS = 780 * NS
BOOT = Path("/proc/sys/kernel/random/boot_id")
DOMAIN = "/proc/self/ns/time"
MESSAGE = "Finite recording clock binding is unconfirmed; preserve the original deadlines."


class UnconfirmedClock(ValueError):
    """Clock changes never authorize renewed readiness or a later native lease."""


def require(value):
    if not value:
        raise UnconfirmedClock(MESSAGE)


def _boot():
    with BOOT.open("rb", buffering=0) as stream:
        raw = stream.read(38)
    require(len(raw) == 37 and raw[-1:] == b"\n")
    value = UUID(raw[:-1].decode("ascii"))
    require(str(value).encode() == raw[:-1])
    return value.hex


def _domain():
    value = os.stat(DOMAIN)
    return value.st_dev, value.st_ino


@dataclass(frozen=True)
class Window:
    boot: str
    namespace: tuple[int, int]
    before_ns: int
    boottime_ns: int
    after_ns: int

    def __post_init__(self):
        try:
            require(type(self.boot) is str and UUID(self.boot).hex == self.boot)
            require(type(self.namespace) is tuple and len(self.namespace) == 2)
            require(all(type(value) is int for value in self.namespace))
            require(self.namespace[0] >= 0 and self.namespace[1] > 0)
            require(
                all(
                    type(value) is int and value > 0
                    for value in (
                        self.before_ns,
                        self.boottime_ns,
                        self.after_ns,
                    )
                )
            )
            require(0 <= self.after_ns - self.before_ns <= MAX_SKEW_NS)
        except Exception:
            raise UnconfirmedClock(MESSAGE) from None

    @property
    def offset(self):
        """Interval containing BOOTTIME minus MONOTONIC, not a guessed offset."""
        return self.boottime_ns - self.after_ns, self.boottime_ns - self.before_ns

    def check_later(self, observed):
        """Reject boot/namespace changes, reversed time or a suspend offset jump."""
        try:
            require(type(observed) is Window)
            self.__post_init__()
            observed.__post_init__()
            require((self.boot, self.namespace) == (observed.boot, observed.namespace))
            require(
                observed.before_ns >= self.after_ns and observed.boottime_ns >= self.boottime_ns
            )
            require(
                max(self.offset[0], observed.offset[0]) <= min(self.offset[1], observed.offset[1])
            )
        except Exception:
            raise UnconfirmedClock(MESSAGE) from None

    def native_deadline(self, boottime_deadline):
        """Conservative conversion of an ORIGINAL future host deadline, once.

        Use the maximum possible offset and round downward. Never move the host
        deadline, infer a new budget from a later sample or use this to re-arm.
        A later offset check is required before any subsequent native dispatch.
        """
        try:
            self.__post_init__()
            require(type(boottime_deadline) in (int, float) and math.isfinite(boottime_deadline))
            exact = Decimal(boottime_deadline) * NS
            host_ns = int(exact.to_integral_value(rounding=ROUND_FLOOR))
            require(0 < host_ns - self.boottime_ns <= MAX_LEASE_NS)
            native_ns = host_ns - self.offset[1]
            require(native_ns > self.after_ns)
            result = math.nextafter(native_ns / NS, -math.inf)
            require(result > self.after_ns / NS)
            return result
        except Exception:
            raise UnconfirmedClock(MESSAGE) from None


def read():
    """Read both real clocks between stable boot and time-namespace observations."""
    try:
        boot, domain = _boot(), _domain()
        before = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        sampled = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        after = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        require(_boot() == boot and _domain() == domain)
        result = Window(boot, domain, before, sampled, after)
        now = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        require(after <= now and now - before <= MAX_SKEW_NS)
        return result
    except Exception:
        raise UnconfirmedClock(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Read-only clock binding only; no live host plan enabled.")
