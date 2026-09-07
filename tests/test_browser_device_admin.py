from __future__ import annotations

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

import sds200.browser_device_admin as admin_module
from sds200.browser_device_admin import BrowserAdminStatus, BrowserDeviceAdmin
from sds200.browser_device_owner import BrowserDeviceOwner, BrowserOwnerError, BrowserOwnerReceipt
from sds200.browser_device_sessions import BrowserDeviceSessions
from sds200.browser_device_store import (
    BrowserDeviceConflict,
    BrowserDeviceRecord,
    BrowserDeviceState,
    BrowserDeviceStore,
    BrowserDeviceStoreError,
)

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux web owner")


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "authority"
    root.mkdir(mode=0o700)
    return BrowserDeviceStore.initialize(root / "devices.sqlite")


def _receipt(record, completed=True):
    return BrowserOwnerReceipt(record, "a" * 32, 123, completed)


def test_committed_pause_stays_paused_without_owner(store):
    admin = BrowserDeviceAdmin(store)
    issued = admin.enroll("display")
    result = admin.transition(issued.record, BrowserDeviceState.PAUSED)
    assert result.status is BrowserAdminStatus.OWNER_UNAVAILABLE
    assert result.as_dict() == {
        "device_id": "display", "generation": 2, "state": "paused",
        "status": "owner_unavailable", "completed": False,
    }
    assert store.authenticate("display", issued.credential) is None
    assert admin.confirm(result.record).status is BrowserAdminStatus.OWNER_UNAVAILABLE
    assert admin.inventory() == (result.record,)


@pytest.mark.parametrize("target", list(BrowserDeviceState))
def test_ack_happens_after_committed_transition(store, monkeypatch, target):
    admin = BrowserDeviceAdmin(store)
    issued = admin.enroll("display")

    def ack(path, record):
        assert BrowserDeviceStore(path).inventory() == (record,)
        assert record.state is target
        return _receipt(record)

    monkeypatch.setattr(admin_module, "request_browser_owner_ack", ack)
    result = admin.transition(issued.record, target)
    assert result.status is BrowserAdminStatus.CONFIRMED
    assert result.as_dict()["completed"] is True


@pytest.mark.parametrize("completed", [True, False])
def test_rotation_handoff_survives_ack_outcome_without_rerotation(store, monkeypatch, completed):
    admin = BrowserDeviceAdmin(store)
    issued = admin.enroll("display")
    other = admin.enroll("other")
    monkeypatch.setattr(admin_module, "request_browser_owner_ack",
                        lambda path, record: _receipt(record, completed))
    rotation = admin.rotate(issued.record)
    assert rotation.result.status is (BrowserAdminStatus.CONFIRMED if completed
                                     else BrowserAdminStatus.PENDING)
    assert rotation.credential not in repr(rotation)
    assert rotation.credential not in str(rotation.result.as_dict())
    assert rotation.credential.encode() not in store.path.read_bytes()
    assert store.authenticate("display", issued.credential) is None
    assert store.authenticate("display", rotation.credential) is not None
    assert store.authenticate("other", other.credential) is not None
    assert admin.confirm(rotation.result.record).status is rotation.result.status
    assert store.inventory()[0].generation == 2
    with pytest.raises(BrowserDeviceConflict):
        admin.rotate(issued.record)
    assert store.authenticate("display", rotation.credential) is not None


def test_lost_ack_rotation_keeps_replacement_and_retries_only_confirmation(store, monkeypatch):
    admin = BrowserDeviceAdmin(store)
    issued = admin.enroll("display")

    def lost(path, record):
        raise BrowserOwnerError()

    monkeypatch.setattr(admin_module, "request_browser_owner_ack", lost)
    rotated = admin.rotate(issued.record)
    assert rotated.result.status is BrowserAdminStatus.OWNER_UNAVAILABLE
    monkeypatch.setattr(admin_module, "request_browser_owner_ack",
                        lambda path, record: _receipt(record))
    assert admin.confirm(rotated.result.record).status is BrowserAdminStatus.CONFIRMED
    assert store.authenticate("display", rotated.credential).generation == 2


def test_rotation_of_paused_device_never_resumes_it(store):
    admin = BrowserDeviceAdmin(store)
    issued = admin.enroll("display")
    paused = admin.transition(issued.record, BrowserDeviceState.PAUSED)
    rotated = admin.rotate(paused.record)
    assert rotated.result.record.state is BrowserDeviceState.PAUSED
    assert store.authenticate("display", rotated.credential) is None


@pytest.mark.parametrize("operation", ["transition", "rotate"])
def test_stale_review_cannot_mutate_new_generation_or_contact_owner(store, monkeypatch, operation):
    admin = BrowserDeviceAdmin(store)
    issued = admin.enroll("display")
    current = store.rotate("display")
    monkeypatch.setattr(admin_module, "request_browser_owner_ack",
                        lambda *args: pytest.fail("Must not contact owner after conflict"))
    with pytest.raises(BrowserDeviceConflict):
        if operation == "transition":
            admin.transition(issued.record, BrowserDeviceState.REVOKED)
        else:
            admin.rotate(issued.record)
    assert store.inventory() == (current.record,)
    assert store.authenticate("display", current.credential) is not None


@pytest.mark.parametrize("bad", [None, "display", BrowserDeviceRecord("display", True,
                                BrowserDeviceState.ACTIVE),
                                BrowserDeviceRecord("display", 1, "active")])
def test_malformed_review_is_refused_without_mutation(store, bad):
    admin = BrowserDeviceAdmin(store)
    issued = admin.enroll("display")
    for action in [admin.rotate, admin.confirm,
                   lambda record: admin.transition(record, BrowserDeviceState.REVOKED)]:
        with pytest.raises(BrowserDeviceStoreError):
            action(bad)
    assert store.inventory() == (issued.record,)


def test_concurrent_reviewed_rotations_have_exactly_one_winner(store):
    issued = store.enroll("display")

    def rotate():
        try:
            return BrowserDeviceAdmin(BrowserDeviceStore(store.path)).rotate(issued.record)
        except BrowserDeviceConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: rotate(), range(2)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert store.authenticate("display", winners[0].credential).generation == 2


@pytest.mark.parametrize("ack_completed", [True, False])
def test_resume_during_ack_supersedes_pause_even_if_ack_says_completed(
    store, monkeypatch, ack_completed,
):
    admin = BrowserDeviceAdmin(store)
    issued = admin.enroll("display")

    def ack(path, record):
        store.transition("display", BrowserDeviceState.ACTIVE)
        return _receipt(record, ack_completed)

    monkeypatch.setattr(admin_module, "request_browser_owner_ack", ack)
    result = admin.transition(issued.record, BrowserDeviceState.PAUSED)
    assert result.status is BrowserAdminStatus.SUPERSEDED
    assert result.as_dict()["completed"] is False
    assert store.inventory()[0].generation == 3


def test_stale_confirmation_does_not_contact_owner(store, monkeypatch):
    issued = store.enroll("display")
    store.rotate("display")
    monkeypatch.setattr(admin_module, "request_browser_owner_ack",
                        lambda *args: pytest.fail("Stale confirmation must not contact owner"))
    result = BrowserDeviceAdmin(store).confirm(issued.record)
    assert result.status is BrowserAdminStatus.SUPERSEDED


def test_wrong_record_receipt_is_not_confirmation(store, monkeypatch):
    issued = store.enroll("display")
    monkeypatch.setattr(admin_module, "request_browser_owner_ack",
                        lambda path, record: _receipt(replace(record, generation=9)))
    result = BrowserDeviceAdmin(store).confirm(issued.record)
    assert result.status is BrowserAdminStatus.OWNER_UNAVAILABLE
    assert result.receipt is None


def test_authority_loss_after_ack_is_not_confirmation(store, monkeypatch):
    issued = store.enroll("display")

    def unavailable():
        raise BrowserDeviceStoreError()

    def ack(path, record):
        monkeypatch.setattr(store, "inventory", unavailable)
        return _receipt(record)

    monkeypatch.setattr(admin_module, "request_browser_owner_ack", ack)
    result = BrowserDeviceAdmin(store).transition(issued.record, BrowserDeviceState.REVOKED)
    assert result.status is BrowserAdminStatus.AUTHORITY_UNAVAILABLE
    assert result.as_dict()["completed"] is False
    assert BrowserDeviceStore(store.path).inventory()[0].state is BrowserDeviceState.REVOKED


def test_actual_owner_pending_then_confirmed_after_old_request_releases(store):
    async def run():
        admin = BrowserDeviceAdmin(store)
        issued = admin.enroll("display")
        sessions = BrowserDeviceSessions(store)
        token = sessions.issue("display", issued.credential)
        lease = sessions.acquire(token.token)
        owner = BrowserDeviceOwner(store)
        owner.acquire()
        await owner.start(lambda record: asyncio.to_thread(sessions.acknowledge,
                                                           record, timeout=0.05))
        try:
            result = await asyncio.to_thread(admin.transition, issued.record,
                                             BrowserDeviceState.REVOKED)
            assert result.status is BrowserAdminStatus.PENDING
            assert lease.revoked.is_set()
            lease.release()
            confirmed = await asyncio.to_thread(admin.confirm, result.record)
            assert confirmed.status is BrowserAdminStatus.CONFIRMED
            assert confirmed.receipt.owner_id == result.receipt.owner_id
            assert store.inventory() == (result.record,)
        finally:
            lease.release()
            sessions.close()
            await owner.stop()
            owner.release()

    asyncio.run(run())
