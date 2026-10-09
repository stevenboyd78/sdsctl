"""Scheduler-independent clock bracket for broad synthetic compositions.

The supplemental direct deadline suites exercise the production wall-clock
limits.  Larger composition fixtures instead combine many real filesystem and
process checks while supplying synthetic phase boundaries.  A busy shared CI
worker must not turn those non-timing fixtures into accidental deadline tests.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def compressed_scheduler_time(
    monkeypatch: Any,
    *,
    clock_module: Any | None = None,
    witness: Any | None = None,
) -> Iterator[None]:
    """Compress scheduler delay while preserving ordered synthetic timestamps.

    This test-only bracket does not change a production timeout or freshness
    limit.  Each visible monotonic read advances by one representable float.
    When a composition also uses the retained host clock, both its direct read
    and the witness read return the already-qualified last window.  No clock
    state survives the bracket, so later explicit expiry tests still advance
    and exercise the real guards.
    """

    current = time.monotonic()

    def monotonic() -> float:
        nonlocal current
        current = math.nextafter(current, math.inf)
        return current

    with monkeypatch.context() as patch:
        patch.setattr(time, "monotonic", monotonic)
        if (clock_module is None) != (witness is None):
            raise AssertionError("clock module and witness must be supplied together")
        if clock_module is not None:
            stable = witness._last
            patch.setattr(clock_module, "read", lambda: stable)
            patch.setattr(witness, "read", lambda: stable)
        yield
