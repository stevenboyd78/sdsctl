"""Internal per-read runtime reservation; no worker, startup hook or scanner I/O.

Lock order is control -> lifecycle -> brief state check -> idle Waterfall
reservation -> the caller's existing scanner command lane. Acquisition yields
immediately to contention. Neither runtime state nor Waterfall state locks are
held while the caller reads; PSI/connection callbacks remain free to run.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

from .daemon_runtime import DaemonRuntime, DaemonRuntimeState
from .waterfall_session import WaterfallIdleReservationError, WaterfallSession


def _reserve(runtime: DaemonRuntime, scanner: object, stack: ExitStack) -> bool:
    if scanner is not runtime.scanner:
        raise ValueError("Supplemental reads require this exact runtime scanner owner.")
    for lock in (runtime._control_lock, runtime._lifecycle_lock):
        if not lock.acquire(blocking=False):
            return False
        stack.callback(lock.release)

    if not runtime._state_lock.acquire(blocking=False):
        return False
    try:
        ready = (
            runtime._state is DaemonRuntimeState.RUNNING
            and runtime._system_status_research is None
            and runtime._display_read_research is None
            and runtime._front_panel_research is None
        )
    finally:
        runtime._state_lock.release()
    if not ready or not runtime.scanner.connected or not runtime.scanner.psi_active:
        return False

    session = getattr(runtime.scanner, "waterfall_session", None)
    if not isinstance(session, WaterfallSession):
        return False  # No guessed/custom reservation implementation.
    try:
        stack.enter_context(session.reserve_idle_for_research())
    except WaterfallIdleReservationError:
        return False  # Busy/reserved/non-idle; never stop Waterfall to make room.
    return True


@contextmanager
def supplemental_read_scope(runtime: DaemonRuntime, scanner: object) -> Iterator[bool]:
    """Reserve exactly one optional GET, or yield False with all locks released.

    The cache still requires current qualified PSI, demand and a matching session
    after this scope is entered. Foreground operations arriving after reservation
    retain their existing busy behavior. This is not transport-write preemption.
    The reservation never starts/stops/reconnects the scanner or changes its mode.
    """
    with ExitStack() as stack:
        ready = _reserve(runtime, scanner, stack)
        if not ready:
            stack.close()
        yield ready
