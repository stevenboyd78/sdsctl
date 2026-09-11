"""Cross-layer prerequisites, NOT an implemented end-to-end resume workflow.

Real authority/owner/ASGI and native-ledger components, fictional credentials,
and an in-process exchange callback. No TLS, browser, scanner or service control.
The trusted internal ledger reset is called explicitly only to isolate each gate.
"""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from sds200 import browser_device_admin as admin_module
from sds200.browser_device_admin import BrowserAdminStatus, BrowserDeviceAdmin
from sds200.browser_device_http import BROWSER_DEVICE_COOKIE, BrowserDeviceHTTP
from sds200.browser_device_owner import BrowserOwnerError
from sds200.browser_device_recovery import (
    BrowserDeviceRecovery,
    BrowserRecoveryError,
    ExchangeFailure,
    RecoveryMode,
    parse_exchange_response,
)
from sds200.browser_device_sessions import BrowserDeviceSessions
from sds200.browser_device_store import (
    BrowserDeviceConflict,
    BrowserDeviceState,
    BrowserDeviceStore,
    BrowserDeviceStoreError,
)
from sds200.web_auth import WebDashboardAuthentication

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux web owner")


@pytest.fixture(params=[
    "https://192.0.2.18:8443", "https://display.example:8443", "https://[fd00::18]:8443",
], ids=["ipv4", "dns", "ipv6"])
def lab(tmp_path, request):
    authority = tmp_path / "authority"
    authority.mkdir(mode=0o700)
    store = BrowserDeviceStore.initialize(authority / "devices.sqlite")
    admin = BrowserDeviceAdmin(store)
    issued = admin.enroll("display")
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    identity = "a" * 64
    BrowserDeviceRecovery.initialize(native / "recovery.sqlite", identity).claim_browser()
    clock = [time.time()]
    ledger = BrowserDeviceRecovery(native / "recovery.sqlite", identity, clock=lambda: clock[0])
    raw = FastAPI()

    @raw.get("/")
    async def display(request: Request):
        return {"display_only": request.state.sdsctl_display_only}

    wrapper = BrowserDeviceHTTP(
        raw, devices=BrowserDeviceSessions(store),
        authentication=WebDashboardAuthentication("fictional operator password", request.param),
        clock=lambda: clock[0],
    )
    # Starlette's in-process TestClient splits URL netloc at the first colon.
    # Use a transport-only name and the exact configured Host header for ALL
    # cases. This tests IPv6 origin policy, not IPv6 sockets or TLS routing.
    with TestClient(wrapper, base_url="https://transport.invalid:8443",
                    headers={"Host": urlsplit(request.param).netloc}) as client:
        yield SimpleNamespace(
            store=store, admin=admin, issued=issued, ledger=ledger, clock=clock,
            client=client, credential=issued.credential, exchanges=0,
        )


def exchange(lab, *, credential=None, device_id="display"):
    lab.exchanges += 1
    response = lab.client.post(
        "/auth/device/session", json={"device_id": device_id},
        headers={"Authorization": "Bearer " + (credential or lab.credential)},
    )
    return parse_exchange_response(
        response.status_code, response.content,
        content_type=response.headers["content-type"],
        retry_after=response.headers.get("retry-after"),
    )


def protected(lab, session):
    return lab.client.get(
        "/device-display", headers={"Cookie": f"{BROWSER_DEVICE_COOKIE}={session.token}"},
    )


def confirmed(result):
    assert result.status is BrowserAdminStatus.CONFIRMED
    assert result.as_dict()["completed"] is True
    return result.record


def blocked(lab, mode):
    def forbidden():
        pytest.fail("A locally stopped ledger attempted authentication")

    # Reopening the helper must not reset a persistent stop, either.
    reopened = BrowserDeviceRecovery(
        lab.ledger.path, "a" * 64, clock=lambda: lab.clock[0],
    )
    result = reopened.authenticate(forbidden)
    assert result.status.mode is mode and result.session is None


@pytest.mark.parametrize("mode", [
    RecoveryMode.PAUSED, RecoveryMode.REJECTED, RecoveryMode.TLS_ERROR,
    RecoveryMode.SETUP_ERROR, RecoveryMode.PROTOCOL_ERROR,
])
def test_authority_resume_does_not_reset_native_pause_or_terminal_error(lab, mode):
    old_session = exchange(lab)
    assert protected(lab, old_session).json() == {"display_only": True}
    if mode is RecoveryMode.PAUSED:
        lab.ledger.suspend()
    else:
        def failure():
            raise ExchangeFailure(mode)

        assert lab.ledger.authenticate(failure).status.mode is mode
    before = lab.ledger.path.read_bytes()
    paused = confirmed(lab.admin.transition(lab.issued.record, BrowserDeviceState.PAUSED))
    resumed = confirmed(lab.admin.transition(paused, BrowserDeviceState.ACTIVE))
    assert resumed.generation == paused.generation + 1
    assert lab.ledger.path.read_bytes() == before
    blocked(lab, mode)
    assert lab.ledger.path.read_bytes() == before
    assert lab.exchanges == 1
    assert protected(lab, old_session).status_code == 401

    # The server permits a NEW session. That fact is independent of local consent.
    fresh = exchange(lab)
    assert fresh.token != old_session.token
    assert protected(lab, fresh).json() == {"display_only": True}


def test_native_reset_cannot_override_server_pause_or_automatically_retry_after_resume(lab):
    paused = confirmed(lab.admin.transition(lab.issued.record, BrowserDeviceState.PAUSED))
    local_pause = lab.ledger.suspend()
    lab.ledger.resume(local_pause.revision)  # Trusted primitive, not a public native action.
    denied = lab.ledger.authenticate(lambda: exchange(lab))
    assert denied.session is None and denied.status.mode is RecoveryMode.REJECTED
    confirmed(lab.admin.transition(paused, BrowserDeviceState.ACTIVE))
    blocked(lab, RecoveryMode.REJECTED)
    assert lab.exchanges == 1

    # Both native + authority gates now allow a fresh exchange. This does NOT
    # clear browser storage or demonstrate coordinated browser resume.
    lab.ledger.resume(denied.status.revision)
    accepted = lab.ledger.authenticate(lambda: exchange(lab))
    assert accepted.session is not None
    assert protected(lab, accepted.session).json() == {"display_only": True}


def test_rotation_preserves_both_pauses_and_invalidates_only_the_exact_device(lab):
    other = lab.admin.enroll("other")
    other_session = exchange(lab, credential=other.credential, device_id="other")
    old_session = exchange(lab)
    paused = confirmed(lab.admin.transition(lab.issued.record, BrowserDeviceState.PAUSED))
    local_pause = lab.ledger.suspend()
    before = lab.ledger.path.read_bytes()
    rotated = lab.admin.rotate(paused)
    record = confirmed(rotated.result)
    assert record.state is BrowserDeviceState.PAUSED
    assert record.generation == paused.generation + 1
    assert lab.ledger.path.read_bytes() == before
    for credential in (lab.credential, rotated.credential):
        with pytest.raises(ExchangeFailure) as error:
            exchange(lab, credential=credential)
        assert error.value.mode is RecoveryMode.REJECTED
    confirmed(lab.admin.transition(record, BrowserDeviceState.ACTIVE))
    lab.credential = rotated.credential  # Test callback only; NOT a credential-file installer.
    blocked(lab, RecoveryMode.PAUSED)
    assert lab.ledger.path.read_bytes() == before
    with pytest.raises(ExchangeFailure) as error:
        exchange(lab, credential=lab.issued.credential)
    assert error.value.mode is RecoveryMode.REJECTED
    assert protected(lab, old_session).status_code == 401
    assert protected(lab, other_session).json() == {"display_only": True}
    assert lab.store.authenticate("other", other.credential) is not None

    # Five deliberate exchange attempts exhausted the real peer budget. Advance
    # only its injected clock; do not disable admission or sleep in the test.
    lab.clock[0] += 61
    lab.ledger.resume(local_pause.revision)
    accepted = lab.ledger.authenticate(lambda: exchange(lab))
    assert accepted.session is not None and accepted.session.token != old_session.token
    assert protected(lab, accepted.session).json() == {"display_only": True}


def test_lost_rotation_ack_requires_confirmation_not_replay_or_local_resume(lab, monkeypatch):
    old_session = exchange(lab)
    lab.ledger.suspend()
    before = lab.ledger.path.read_bytes()
    with monkeypatch.context() as patch:
        def lost(path, record):
            raise BrowserOwnerError()

        patch.setattr(admin_module, "request_browser_owner_ack", lost)
        rotated = lab.admin.rotate(lab.issued.record)
    assert rotated.result.status is BrowserAdminStatus.OWNER_UNAVAILABLE
    assert rotated.result.as_dict()["completed"] is False
    assert rotated.credential not in repr(rotated)
    assert rotated.credential not in str(rotated.result.as_dict())
    with pytest.raises(BrowserDeviceConflict):
        lab.admin.rotate(lab.issued.record)
    assert confirmed(lab.admin.confirm(rotated.result.record)) == rotated.result.record
    assert lab.ledger.path.read_bytes() == before
    blocked(lab, RecoveryMode.PAUSED)
    assert protected(lab, old_session).status_code == 401
    assert lab.store.authenticate("display", lab.issued.credential) is None
    assert lab.store.authenticate("display", rotated.credential) is not None


def test_stale_review_cannot_resume_a_new_server_generation_or_native_pause(lab):
    paused = confirmed(lab.admin.transition(lab.issued.record, BrowserDeviceState.PAUSED))
    local_pause = lab.ledger.suspend()
    rotated = lab.admin.rotate(paused)
    current = confirmed(rotated.result)
    with pytest.raises(BrowserDeviceConflict):
        lab.admin.transition(paused, BrowserDeviceState.ACTIVE)
    assert current.state is BrowserDeviceState.PAUSED and current in lab.admin.inventory()

    lab.ledger.resume(local_pause.revision)
    newer_pause = lab.ledger.suspend()
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserRecoveryError):
        lab.ledger.resume(local_pause.revision)
    assert lab.ledger.inspect() == newer_pause
    assert lab.ledger.path.read_bytes() == before
    blocked(lab, RecoveryMode.PAUSED)
    assert lab.exchanges == 0


def test_terminal_revocation_never_becomes_replacement_under_the_old_identifier(lab):
    old_session = exchange(lab)
    revoked = confirmed(lab.admin.transition(lab.issued.record, BrowserDeviceState.REVOKED))
    for operation in (
        lambda: lab.admin.transition(revoked, BrowserDeviceState.ACTIVE),
        lambda: lab.admin.rotate(revoked),
        lambda: lab.admin.enroll("display"),
    ):
        with pytest.raises(BrowserDeviceStoreError):
            operation()
    assert lab.ledger.authenticate(lambda: exchange(lab)).status.mode is RecoveryMode.REJECTED
    replacement = lab.admin.enroll("replacement")
    fresh = exchange(lab, credential=replacement.credential, device_id="replacement")
    assert protected(lab, fresh).json() == {"display_only": True}
    blocked(lab, RecoveryMode.REJECTED)
    assert protected(lab, old_session).status_code == 401
    assert revoked in lab.admin.inventory()
