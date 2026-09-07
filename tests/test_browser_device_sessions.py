from __future__ import annotations

import asyncio
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pytest

from sds200.browser_device_sessions import (
    BrowserDeviceSessions,
    BrowserSessionEnded,
    run_browser_session_request,
)
from sds200.browser_device_store import BrowserDeviceState, BrowserDeviceStore

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Private POSIX authority foundation")


@pytest.fixture
def store(tmp_path: Path) -> BrowserDeviceStore:
    root = tmp_path / "authority"
    root.mkdir(mode=0o700)
    return BrowserDeviceStore.initialize(root / "devices.sqlite")


def test_session_namespace_redaction_and_restart(store: BrowserDeviceStore) -> None:
    device = store.enroll("one")
    sessions = BrowserDeviceSessions(store)
    issued = sessions.issue("one", device.credential)
    assert issued is not None
    assert issued.token not in repr(issued)
    assert issued.token not in repr(sessions._sessions)
    assert device.credential not in repr(sessions._sessions)
    assert sessions.issue("one", issued.token) is None

    async def check() -> None:
        for token in (None, "password", "a" * 43, device.credential):
            assert sessions.acquire(token) is None
        assert BrowserDeviceSessions(store).acquire(issued.token) is None
        lease = sessions.acquire(issued.token)
        assert lease is not None
        assert lease.binding.device_id == "one"
        lease.release()
        lease.release()

    asyncio.run(check())


@pytest.mark.parametrize("change", ["rotate", "pause", "revoke"])
def test_authority_change_signals_only_affected_device(
    store: BrowserDeviceStore, change: str,
) -> None:
    one, two = store.enroll("one"), store.enroll("two")
    sessions = BrowserDeviceSessions(store)
    first, second = sessions.issue("one", one.credential), sessions.issue("two", two.credential)
    assert first is not None and second is not None

    async def check() -> None:
        first_lease, second_lease = sessions.acquire(first.token), sessions.acquire(second.token)
        assert first_lease is not None and second_lease is not None
        external = BrowserDeviceStore(store.path)
        if change == "rotate":
            external.rotate("one")
        else:
            external.transition("one", BrowserDeviceState.PAUSED if change == "pause"
                                else BrowserDeviceState.REVOKED)
        assert await asyncio.to_thread(sessions.reconcile)
        await asyncio.wait_for(first_lease.revoked.wait(), timeout=1)
        assert not second_lease.revoked.is_set()
        assert sessions.acquire(first.token) is None
        assert sessions.acquire(second.token) is not None
        if change == "pause":
            external.transition("one", BrowserDeviceState.ACTIVE)
            assert sessions.acquire(first.token) is None
            assert sessions.issue("one", one.credential) is not None
        first_lease.release()
        second_lease.release()
        sessions.close()

    asyncio.run(check())


def test_idle_deadline_and_absolute_deadline_with_open_stream(store: BrowserDeviceStore) -> None:
    device = store.enroll("one")
    now = [0.0]
    sessions = BrowserDeviceSessions(store, absolute_seconds=10, idle_seconds=3,
                                     clock=lambda: now[0])
    issued = sessions.issue("one", device.credential)
    assert issued is not None

    async def check() -> None:
        lease = sessions.acquire(issued.token)
        assert lease is not None and lease.remaining_seconds == 10
        now[0] = 4
        assert sessions.reconcile()  # Open stream is not idle, but has a hard deadline.
        await asyncio.sleep(0)
        assert not lease.revoked.is_set()
        now[0] = 10
        assert sessions.reconcile()
        await asyncio.wait_for(lease.revoked.wait(), 1)
        lease.release()
        assert sessions.acquire(issued.token) is None
        replacement = sessions.issue("one", device.credential)
        assert replacement is not None
        now[0] = 13
        assert sessions.acquire(replacement.token) is None

    asyncio.run(check())


def test_capacity_and_idempotent_release(store: BrowserDeviceStore) -> None:
    one, two = store.enroll("one"), store.enroll("two")
    sessions = BrowserDeviceSessions(store, max_sessions=2, max_sessions_per_device=1,
                                     max_requests_per_session=1)
    first = sessions.issue("one", one.credential)
    assert first is not None
    assert sessions.issue("one", one.credential) is None
    assert sessions.issue("two", two.credential) is not None
    three = store.enroll("three")
    assert sessions.issue("three", three.credential) is None

    async def check() -> None:
        lease = sessions.acquire(first.token)
        assert lease is not None
        assert sessions.acquire(first.token) is None
        lease.release()
        lease.release()
        assert sessions.acquire(first.token) is not None
        sessions.revoke_session(first.token)
        assert sessions.issue("one", one.credential) is not None
        sessions.close()

    asyncio.run(check())


@pytest.mark.parametrize("fault", ["missing", "permissions", "corrupt"])
def test_authority_failure_closes_all_and_never_resurrects(
    store: BrowserDeviceStore, fault: str,
) -> None:
    one = store.enroll("one")
    sessions = BrowserDeviceSessions(store)
    issued = sessions.issue("one", one.credential)
    assert issued is not None
    original = store.path.read_bytes()

    async def check() -> None:
        lease = sessions.acquire(issued.token)
        assert lease is not None
        if fault == "missing":
            store.path.rename(store.path.with_suffix(".saved"))
        elif fault == "permissions":
            store.path.chmod(0o644)
        else:
            store.path.write_bytes(b"invalid database")
        assert not sessions.reconcile()
        await asyncio.wait_for(lease.revoked.wait(), 1)
        if fault == "missing":
            store.path.with_suffix(".saved").rename(store.path)
        else:
            store.path.write_bytes(original)
            store.path.chmod(0o600)
        assert sessions.issue("one", one.credential) is None
        assert sessions.acquire(issued.token) is None
        assert BrowserDeviceSessions(store).issue("one", one.credential) is not None
        lease.release()

    asyncio.run(check())


def test_change_between_issue_and_acquire_is_rejected(store: BrowserDeviceStore) -> None:
    one = store.enroll("one")
    sessions = BrowserDeviceSessions(store)
    issued = sessions.issue("one", one.credential)
    assert issued is not None
    store.rotate("one")

    async def check() -> None:
        assert sessions.acquire(issued.token) is None

    asyncio.run(check())


def test_reconciler_detects_change_without_new_request_and_closes_on_cancel(
    store: BrowserDeviceStore,
) -> None:
    one, two = store.enroll("one"), store.enroll("two")
    sessions = BrowserDeviceSessions(store)
    first, second = sessions.issue("one", one.credential), sessions.issue("two", two.credential)
    assert first is not None and second is not None

    async def check() -> None:
        lease, other = sessions.acquire(first.token), sessions.acquire(second.token)
        assert lease is not None and other is not None
        monitor = asyncio.create_task(sessions.run_reconciler(interval_seconds=0.01))
        try:
            store.transition("one", BrowserDeviceState.REVOKED)
            await asyncio.wait_for(lease.revoked.wait(), 2)
            assert not other.revoked.is_set()
            with pytest.raises(RuntimeError):
                await sessions.run_reconciler()
        finally:
            monitor.cancel()
            with pytest.raises(asyncio.CancelledError):
                await monitor
        await asyncio.wait_for(other.revoked.wait(), 1)
        assert sessions.issue("two", two.credential) is None

    asyncio.run(check())


@pytest.mark.parametrize("options", [
    {"absolute_seconds": 0}, {"idle_seconds": float("nan")},
    {"absolute_seconds": float("inf")}, {"idle_seconds": True},
    {"max_sessions": True}, {"max_sessions": 0}, {"max_requests_per_session": 257},
    {"max_sessions": 1, "max_sessions_per_device": 2},
    {"absolute_seconds": 10, "idle_seconds": 11},
])
def test_invalid_limits(store: BrowserDeviceStore, options: dict) -> None:
    with pytest.raises(ValueError):
        BrowserDeviceSessions(store, **options)


def _pause_in_process(path: Path) -> None:
    BrowserDeviceStore(path).transition("one", BrowserDeviceState.PAUSED)


def test_separate_process_pause_cancels_attached_stream(store: BrowserDeviceStore) -> None:
    one = store.enroll("one")
    sessions = BrowserDeviceSessions(store)
    issued = sessions.issue("one", one.credential)
    assert issued is not None
    with ProcessPoolExecutor(max_workers=1) as executor:
        async def check() -> None:
            lease = sessions.acquire(issued.token)
            assert lease is not None
            await asyncio.wrap_future(executor.submit(_pause_in_process, store.path))
            assert sessions.reconcile()
            await asyncio.wait_for(lease.revoked.wait(), 1)
            assert sessions.acquire(issued.token) is None
        asyncio.run(check())


def test_concurrent_issuance_cannot_exceed_device_limit(store: BrowserDeviceStore) -> None:
    one = store.enroll("one")
    sessions = BrowserDeviceSessions(store, max_sessions_per_device=2)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: sessions.issue("one", one.credential), range(20)))
    assert sum(result is not None for result in results) == 2


def test_rotation_during_issuance_snapshot_cannot_authorize_old_token(
    store: BrowserDeviceStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    one = store.enroll("one")
    sessions = BrowserDeviceSessions(store)
    original = store.is_current

    def check_then_rotate(binding):
        valid = original(binding)
        store.rotate("one")  # Deliberately after the last issuance snapshot.
        return valid

    monkeypatch.setattr(store, "is_current", check_then_rotate)
    issued = sessions.issue("one", one.credential)
    assert issued is not None  # Issuance is not an authorization lease.

    async def check() -> None:
        assert sessions.acquire(issued.token) is None
    asyncio.run(check())


def test_duplicate_release_cannot_extend_idle_deadline(store: BrowserDeviceStore) -> None:
    one = store.enroll("one")
    now = [0.0]
    sessions = BrowserDeviceSessions(store, idle_seconds=3, clock=lambda: now[0])
    issued = sessions.issue("one", one.credential)
    assert issued is not None

    async def check() -> None:
        lease = sessions.acquire(issued.token)
        assert lease is not None
        lease.release()
        now[0] = 2
        lease.release()
        now[0] = 3
        assert sessions.acquire(issued.token) is None
    asyncio.run(check())


def test_unexpected_monitor_failure_closes_leases(
    store: BrowserDeviceStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    one = store.enroll("one")
    sessions = BrowserDeviceSessions(store)
    issued = sessions.issue("one", one.credential)
    assert issued is not None

    def fail():
        raise RuntimeError("fixture failure")

    async def check() -> None:
        lease = sessions.acquire(issued.token)
        assert lease is not None
        monkeypatch.setattr(store, "inventory", fail)
        with pytest.raises(RuntimeError, match="fixture failure"):
            await sessions.run_reconciler()
        await asyncio.wait_for(lease.revoked.wait(), 1)
        assert sessions.acquire(issued.token) is None
    asyncio.run(check())


@pytest.mark.parametrize("interval", [0, -1, True, 6, float("nan"), float("inf")])
def test_invalid_monitor_interval(store: BrowserDeviceStore, interval: float) -> None:
    async def check() -> None:
        with pytest.raises(ValueError):
            await BrowserDeviceSessions(store).run_reconciler(interval_seconds=interval)
    asyncio.run(check())


@pytest.mark.parametrize("ending", ["revoked", "expired", "cancelled"])
def test_request_guard_waits_for_stream_cleanup(store: BrowserDeviceStore, ending: str) -> None:
    one = store.enroll("one")
    lifetime = 0.05 if ending == "expired" else 10
    sessions = BrowserDeviceSessions(store, absolute_seconds=lifetime, idle_seconds=lifetime,
                                     max_requests_per_session=1)
    issued = sessions.issue("one", one.credential)
    assert issued is not None

    async def check() -> None:
        started, cleaned = asyncio.Event(), asyncio.Event()

        async def stream() -> None:
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        lease = sessions.acquire(issued.token)
        assert lease is not None
        task = asyncio.create_task(run_browser_session_request(lease, stream))
        await started.wait()
        if ending == "revoked":
            store.transition("one", BrowserDeviceState.REVOKED)
            sessions.reconcile()
        elif ending == "cancelled":
            task.cancel()
        expected = asyncio.CancelledError if ending == "cancelled" else BrowserSessionEnded
        with pytest.raises(expected):
            await asyncio.wait_for(task, 1)
        assert cleaned.is_set()  # Not just "revocation was queued".
        assert all(not session.watchers for session in sessions._sessions.values())
        sessions.close()

    asyncio.run(check())


@pytest.mark.parametrize("ending", ["success", "failure", "already-revoked"])
def test_request_guard_releases_on_every_exit(store: BrowserDeviceStore, ending: str) -> None:
    one = store.enroll("one")
    sessions = BrowserDeviceSessions(store, max_requests_per_session=1)
    issued = sessions.issue("one", one.credential)
    assert issued is not None

    async def check() -> None:
        called = False

        async def request() -> int:
            nonlocal called
            called = True
            if ending == "failure":
                raise ValueError("fixture")
            return 42

        lease = sessions.acquire(issued.token)
        assert lease is not None
        if ending == "already-revoked":
            sessions.revoke_session(issued.token)
            await lease.revoked.wait()
            with pytest.raises(BrowserSessionEnded):
                await run_browser_session_request(lease, request)
            assert not called
        elif ending == "failure":
            with pytest.raises(ValueError, match="fixture"):
                await run_browser_session_request(lease, request)
        else:
            assert await run_browser_session_request(lease, request) == 42
        assert all(not session.watchers for session in sessions._sessions.values())
        sessions.close()
    asyncio.run(check())


def test_delayed_request_dispatch_does_not_extend_deadline(store: BrowserDeviceStore) -> None:
    one = store.enroll("one")
    sessions = BrowserDeviceSessions(store, absolute_seconds=0.02, idle_seconds=0.02)
    issued = sessions.issue("one", one.credential)
    assert issued is not None

    async def check() -> None:
        lease = sessions.acquire(issued.token)
        assert lease is not None
        called = False

        async def request() -> None:
            nonlocal called
            called = True

        await asyncio.sleep(0.03)
        with pytest.raises(BrowserSessionEnded):
            await run_browser_session_request(lease, request)
        assert not called
        assert all(not session.watchers for session in sessions._sessions.values())
    asyncio.run(check())


def test_acknowledgement_waits_for_release_after_invalidation(store: BrowserDeviceStore) -> None:
    device = store.enroll("one")
    sessions = BrowserDeviceSessions(store)
    issued = sessions.issue("one", device.credential)
    assert issued is not None

    async def check() -> None:
        lease = sessions.acquire(issued.token)
        assert lease is not None
        paused = sessions.pause(lease.binding)
        assert paused is not None
        await lease.revoked.wait()
        assert not await asyncio.to_thread(sessions.acknowledge, paused, timeout=0.01)
        assert sessions._outstanding  # Revocation alone must not discard completion tracking.
        lease.release()
        assert await asyncio.to_thread(sessions.acknowledge, paused)
        store.transition("one", BrowserDeviceState.ACTIVE)
        assert not await asyncio.to_thread(sessions.acknowledge, paused)
        assert sessions.acquire(issued.token) is None
    asyncio.run(check())


def test_undrained_requests_stay_bounded_across_session_replacement(store) -> None:
    one = store.enroll("one")
    sessions = BrowserDeviceSessions(store, max_sessions=1, max_sessions_per_device=1,
                                     max_requests_per_session=1)
    issued = sessions.issue("one", one.credential)
    assert issued is not None

    async def check() -> None:
        lease = sessions.acquire(issued.token)
        assert lease is not None
        sessions.revoke_session(issued.token)
        replacement = sessions.issue("one", one.credential)
        assert replacement is not None
        assert sessions.acquire(replacement.token) is None
        lease.release()
        next_lease = sessions.acquire(replacement.token)
        assert next_lease is not None
        next_lease.release()
    asyncio.run(check())


def test_ack_does_not_wait_for_unrelated_device_and_fails_when_authority_missing(store) -> None:
    one, two = store.enroll("one"), store.enroll("two")
    sessions = BrowserDeviceSessions(store)
    first, second = sessions.issue("one", one.credential), sessions.issue("two", two.credential)
    assert first is not None and second is not None

    async def check() -> None:
        old, other = sessions.acquire(first.token), sessions.acquire(second.token)
        assert old is not None and other is not None
        paused = sessions.pause(old.binding)
        assert paused is not None
        old.release()
        assert await asyncio.to_thread(sessions.acknowledge, paused, timeout=0.01)
        assert not other.revoked.is_set()
        store.path.rename(store.path.with_suffix(".saved"))
        assert not await asyncio.to_thread(sessions.acknowledge, paused)
        await other.revoked.wait()
        other.release()
    asyncio.run(check())


def test_shutdown_drain_requires_close_and_finished_requests(store) -> None:
    device = store.enroll("one")
    sessions = BrowserDeviceSessions(store)
    issued = sessions.issue("one", device.credential)
    assert issued is not None

    async def check():
        lease = sessions.acquire(issued.token)
        assert lease is not None
        assert not sessions.drain_closed(timeout=0.01)
        sessions.close()
        await lease.revoked.wait()
        assert not await asyncio.to_thread(sessions.drain_closed, timeout=0.01)
        lease.release()
        assert await asyncio.to_thread(sessions.drain_closed, timeout=0.01)
    asyncio.run(check())
