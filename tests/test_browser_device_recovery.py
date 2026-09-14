from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sds200.browser_device_recovery as recovery_module
from sds200.browser_device_http import BrowserDeviceHTTP
from sds200.browser_device_protocol import parse_browser_device_request
from sds200.browser_device_recovery import (
    BrowserDeviceRecovery,
    BrowserRecoveryError,
    ExchangeFailure,
    ExchangeSession,
    RecoveryMode,
    parse_exchange_response,
)
from sds200.browser_device_sessions import BrowserDeviceSessions
from sds200.browser_device_store import BrowserDeviceState, BrowserDeviceStore
from sds200.web_auth import WebDashboardAuthentication

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Private POSIX recovery state")
TOKEN = "sdsctl-browser-session-v1." + "a" * 64
IDENTITY = "b" * 64


@pytest.fixture
def ledger(tmp_path):
    root = tmp_path / "helper"
    root.mkdir(mode=0o700)
    original = BrowserDeviceRecovery.initialize(root / "recovery.sqlite", IDENTITY)
    clock = [time.time() + 1]
    ledger = BrowserDeviceRecovery(original.path, IDENTITY,
                                   clock=lambda: clock[0], jitter=lambda: 1)
    return ledger, clock


def session():
    return ExchangeSession(TOKEN, 300)


def test_browser_claim_is_one_time_serialized_and_never_exchanges(ledger):
    state, clock = ledger
    request = parse_browser_device_request(b'{"version":1,"action":"claim-browser"}')

    def claim(_):
        try:
            return reopen(state, clock).handle(request, lambda: pytest.fail("No exchange"))
        except BrowserRecoveryError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(20)))
    accepted = [result for result in results if result is not None]
    assert len(accepted) == 1
    assert accepted[0].status.revision == 2
    assert accepted[0].status.mode is RecoveryMode.ACTIVE
    assert accepted[0].status.retry_after == 0
    assert accepted[0].session is None and accepted[0].renew_after == 0
    before = state.path.read_bytes()
    assert claim(None) is None  # Lost acknowledgement cannot replay the claim.
    assert state.path.read_bytes() == before


@pytest.mark.parametrize("used", ["pause", "resumed", "success", "retry", "terminal", "rollback"])
def test_browser_claim_never_resets_used_or_rolled_back_state(ledger, used):
    state, clock = ledger
    if used == "pause":
        state.suspend()
    elif used == "resumed":
        state.resume(state.suspend().revision)
    elif used == "success":
        state.authenticate(session)
    elif used == "retry":
        state.authenticate(fail())
    elif used == "terminal":
        state.authenticate(fail(RecoveryMode.REJECTED))
    else:
        clock[0] -= 60
    before = state.path.read_bytes()
    with pytest.raises(BrowserRecoveryError):
        reopen(state, clock).claim_browser()
    assert state.path.read_bytes() == before


def fail(mode=RecoveryMode.ACTIVE, retry_after=0):
    def exchange():
        raise ExchangeFailure(mode, retry_after=retry_after)
    return exchange


def reopen(ledger, clock):
    return BrowserDeviceRecovery(ledger.path, IDENTITY, clock=lambda: clock[0], jitter=lambda: 1)


def test_success_supplies_renewal_hint_without_persisting_token(ledger):
    state, clock = ledger
    result = state.authenticate(session)
    assert result.session.token == TOKEN
    assert 299 <= result.session.expires_in <= 300
    assert 224 <= result.renew_after <= 225
    assert result.status.retry_after == 10
    assert TOKEN not in repr(result)
    assert TOKEN not in repr(result.session)
    assert TOKEN.encode() not in state.path.read_bytes()
    assert reopen(state, clock).status() == result.status
    assert state.path.stat().st_mode & 0o777 == 0o600


def test_callback_returning_after_deadline_discards_token_but_can_retry(ledger, monkeypatch):
    state, clock = ledger
    times = iter([0, 10.01, 11, 11.01])
    monkeypatch.setattr(recovery_module, "time", SimpleNamespace(monotonic=lambda: next(times)))
    result = state.authenticate(session)
    assert result.session is None and result.status.mode is RecoveryMode.ACTIVE
    assert result.status.retry_after > 0
    clock[0] += 61
    assert reopen(state, clock).authenticate(session).session is not None


@pytest.mark.parametrize("mode", [RecoveryMode.TLS_ERROR, RecoveryMode.REJECTED,
                                 RecoveryMode.PROTOCOL_ERROR, RecoveryMode.SETUP_ERROR])
def test_late_terminal_failure_is_never_converted_to_retry(ledger, monkeypatch, mode):
    state, clock = ledger
    times = iter([0, 10.01])
    monkeypatch.setattr(recovery_module, "time", SimpleNamespace(monotonic=lambda: next(times)))
    result = state.authenticate(fail(mode))
    assert result.session is None and result.status.mode is mode
    clock[0] += 301
    assert reopen(state, clock).authenticate(session).status.mode is mode


def test_outage_backoff_survives_helper_restarts_and_recovers(ledger):
    state, clock = ledger
    for delay in [2, 4, 8, 16, 32, 60, 60]:
        state = reopen(state, clock)
        result = state.authenticate(fail())
        assert result.status.mode is RecoveryMode.ACTIVE
        assert result.status.retry_after == delay
        assert result.session is None
        assert state.authenticate(lambda: pytest.fail("Backoff bypass")).session is None
        clock[0] += delay
    assert state.authenticate(session).session is not None
    clock[0] += 10
    assert state.authenticate(fail()).status.retry_after == 2


@pytest.mark.parametrize("jitter,delay", [(0, 1.5), (0.5, 1.75), (1, 2)])
def test_jitter_is_bounded(ledger, jitter, delay):
    state, clock = ledger
    state = BrowserDeviceRecovery(state.path, IDENTITY, clock=lambda: clock[0],
                                   jitter=lambda: jitter)
    assert state.authenticate(fail()).status.retry_after == delay


def test_retry_after_is_honored_across_restart(ledger):
    state, clock = ledger
    result = state.authenticate(fail(retry_after=300))
    assert result.status.retry_after == 300
    clock[0] += 299
    early = reopen(state, clock).authenticate(lambda: pytest.fail("Too early"))
    assert early.status.retry_after == 1
    clock[0] += 1
    assert reopen(state, clock).authenticate(session).session is not None


@pytest.mark.parametrize("mode", [RecoveryMode.REJECTED, RecoveryMode.TLS_ERROR,
                                  RecoveryMode.SETUP_ERROR, RecoveryMode.PROTOCOL_ERROR])
def test_terminal_errors_do_not_retry_or_reset_when_reopened(ledger, mode):
    state, clock = ledger
    assert state.authenticate(fail(mode)).status.mode is mode
    clock[0] += 86400
    result = reopen(state, clock).authenticate(lambda: pytest.fail("Terminal state retried"))
    assert result.status.mode is mode
    assert result.session is None
    state.resume(result.status.revision)
    assert state.authenticate(session).session is not None


def test_offline_pause_persists_and_native_actions_cannot_resume(ledger):
    state, clock = ledger
    request = parse_browser_device_request(b'{"version":1,"action":"suspend"}')
    paused = state.handle(request, lambda: pytest.fail("Suspend attempted network"))
    assert paused.status.mode is RecoveryMode.PAUSED
    clock[0] += 86400
    reopened = reopen(state, clock)
    for action in ["status", "authenticate", "suspend"]:
        request = parse_browser_device_request(
            json.dumps({"version": 1, "action": action}).encode())
        result = reopened.handle(request, lambda: pytest.fail("Paused helper attempted network"))
        assert result.status.mode is RecoveryMode.PAUSED
        assert result.session is None
        assert result.status.revision == paused.status.revision


def test_stale_administrator_resume_cannot_clear_new_pause(ledger):
    state, clock = ledger
    stale = state.status().revision
    paused = state.suspend()
    with pytest.raises(BrowserRecoveryError):
        state.resume(stale)
    with pytest.raises(BrowserRecoveryError):
        state.resume(True)
    assert state.status() == paused


@pytest.mark.parametrize("outcome", ["success", "reject", "unexpected"])
def test_pause_wins_over_inflight_exchange_without_waiting_for_network(ledger, outcome):
    state, clock = ledger
    entered, release = threading.Event(), threading.Event()

    def exchange():
        entered.set()
        assert release.wait(3)
        if outcome == "reject":
            raise ExchangeFailure(RecoveryMode.REJECTED)
        if outcome == "unexpected":
            raise RuntimeError("fictional secret must not be printed")
        return session()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(state.authenticate, exchange)
        try:
            assert entered.wait(2)
            paused = reopen(state, clock).suspend()
        finally:
            release.set()
        result = future.result(timeout=2)
    assert result.status == paused
    assert result.session is None
    assert reopen(state, clock).status().mode is RecoveryMode.PAUSED


def test_concurrent_helper_does_not_issue_second_exchange(ledger):
    state, clock = ledger
    entered, release = threading.Event(), threading.Event()

    def exchange():
        entered.set()
        assert release.wait(3)
        return session()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(state.authenticate, exchange)
        try:
            assert entered.wait(2)
            result = reopen(state, clock).authenticate(lambda: pytest.fail("Second exchange"))
            assert result.status.retry_after == 15
        finally:
            release.set()
        assert future.result(timeout=2).session is not None


def test_replaced_expired_claim_discards_old_response(ledger):
    state, clock = ledger
    entered, release = threading.Event(), threading.Event()

    def old():
        entered.set()
        assert release.wait(3)
        return session()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(state.authenticate, old)
        try:
            assert entered.wait(2)
            clock[0] += 16
            replacement = reopen(state, clock).authenticate(session)
            assert replacement.session is not None
        finally:
            release.set()
        assert future.result(timeout=2).session is None


def test_unexpected_error_is_redacted_and_does_not_retry(ledger, capsys):
    state, clock = ledger

    def error():
        raise RuntimeError(TOKEN)

    result = state.authenticate(error)
    assert result.status.mode is RecoveryMode.SETUP_ERROR
    assert TOKEN not in repr(result)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("paused", [False, True])
def test_clock_rollback_bounds_wait_without_clearing_pause(ledger, paused):
    state, clock = ledger
    before = state.suspend() if paused else state.authenticate(fail(retry_after=300)).status
    clock[0] -= 86400
    result = reopen(state, clock).status()
    assert result.revision > before.revision
    assert result.mode is (RecoveryMode.PAUSED if paused else RecoveryMode.ACTIVE)
    assert result.retry_after == (0 if paused else 10)


def test_missing_or_wrong_identity_never_creates_or_resets_state(ledger):
    state, clock = ledger
    with pytest.raises(BrowserRecoveryError):
        BrowserDeviceRecovery(state.path, "c" * 64).status()
    missing = state.path.parent / "missing.sqlite"
    with pytest.raises(BrowserRecoveryError):
        BrowserDeviceRecovery(missing, IDENTITY).status()
    assert not missing.exists()
    with pytest.raises(BrowserRecoveryError):
        BrowserDeviceRecovery.initialize(state.path, IDENTITY)


@pytest.mark.parametrize("change", ["mode", "symlink", "hardlink"])
def test_unsafe_state_refused(ledger, change):
    state, clock = ledger
    if change == "mode":
        state.path.chmod(0o644)
    elif change == "symlink":
        alias = state.path.parent / "alias.sqlite"
        alias.symlink_to(state.path)
        state = BrowserDeviceRecovery(alias, IDENTITY)
    else:
        os.link(state.path, state.path.parent / "alias.sqlite")
    with pytest.raises(BrowserRecoveryError):
        state.authenticate(lambda: pytest.fail("Unsafe state attempted network"))


@pytest.mark.parametrize("sql", ["UPDATE recovery SET revision=0", "UPDATE recovery SET mode='bad'",
    "UPDATE recovery SET failures=33", "UPDATE recovery SET next_at=observed_at+100000",
    "DELETE FROM recovery", "PRAGMA user_version=2"])
def test_corrupt_state_fails_closed(ledger, sql):
    state, clock = ledger
    with closing(sqlite3.connect(state.path)) as db, db:
        db.execute(sql)
    with pytest.raises(BrowserRecoveryError):
        state.authenticate(lambda: pytest.fail("Corrupt state attempted network"))


@pytest.mark.parametrize("status,mode", [
    (401, RecoveryMode.REJECTED), (403, RecoveryMode.PROTOCOL_ERROR),
    (302, RecoveryMode.PROTOCOL_ERROR), (429, RecoveryMode.ACTIVE), (503, RecoveryMode.ACTIVE)])
def test_http_error_classification_never_exposes_body(status, mode):
    with pytest.raises(ExchangeFailure) as error:
        parse_exchange_response(status, TOKEN.encode(), content_type="application/json",
                                retry_after="60")
    assert error.value.mode is mode
    assert TOKEN not in str(error.value)
    assert error.value.retry_after == (60 if mode is RecoveryMode.ACTIVE else 0)


@pytest.mark.parametrize("body", [b'{}', b'[]', b'\xff', b'{"token":"x","token":"y"}',
    json.dumps({"token": TOKEN, "expires_in": True}).encode(),
    json.dumps({"token": TOKEN, "expires_in": 3601}).encode(),
    json.dumps({"token": TOKEN, "expires_in": float("inf")}).encode(),
    json.dumps({"token": TOKEN, "expires_in": 300, "role": "operator"}).encode(), b'x' * 4097])
def test_malformed_success_is_terminal_protocol_failure(body):
    with pytest.raises(ExchangeFailure) as error:
        parse_exchange_response(200, body, content_type="application/json")
    assert error.value.mode is RecoveryMode.PROTOCOL_ERROR


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux owner adapter")
def test_actual_server_contract_renews_and_revoked_device_stops(ledger, tmp_path):
    state, clock = ledger
    root = tmp_path / "server"
    root.mkdir(mode=0o700)
    store = BrowserDeviceStore.initialize(root / "devices.sqlite")
    device = store.enroll("display")
    server_clock = [100.0]
    sessions = BrowserDeviceSessions(store, clock=lambda: server_clock[0])
    origin = "https://192.168.0.18:8443"
    authentication = WebDashboardAuthentication("fictional operator", origin)
    wrapper = BrowserDeviceHTTP(FastAPI(), devices=sessions, authentication=authentication)
    with TestClient(wrapper, base_url=origin) as client:
        def exchange():
            response = client.post("/auth/device/session", json={"device_id": "display"},
                                   headers={"Authorization": "Bearer " + device.credential})
            return parse_exchange_response(response.status_code, response.content,
                                           content_type=response.headers["content-type"],
                                           retry_after=response.headers.get("retry-after"))

        first = state.authenticate(exchange)
        assert first.session is not None
        clock[0] += 301
        server_clock[0] += 301
        second = reopen(state, clock).authenticate(exchange)
        assert second.session is not None and second.session.token != first.session.token
        store.transition("display", BrowserDeviceState.REVOKED)
        clock[0] += 11
        assert state.authenticate(exchange).status.mode is RecoveryMode.REJECTED
        assert state.authenticate(lambda: pytest.fail("Revoked device retried")).session is None


def _process_exchange(path, now, pipe):
    ledger = BrowserDeviceRecovery(path, IDENTITY, clock=lambda: now)

    def exchange():
        pipe.send("claimed")
        assert pipe.poll(5) and pipe.recv() == "finish"
        return session()

    result = ledger.authenticate(exchange)
    pipe.send((result.status.mode.value, result.session is not None))


def test_separate_helper_process_observes_persisted_pause_before_handoff(ledger):
    state, clock = ledger
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_process_exchange, args=(state.path, clock[0], child))
    process.start()
    try:
        assert parent.poll(5) and parent.recv() == "claimed"
        state.suspend()
        parent.send("finish")
        assert parent.poll(5) and parent.recv() == ("paused", False)
        process.join(5)
        assert process.exitcode == 0
        assert reopen(state, clock).status().mode is RecoveryMode.PAUSED
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
        child.close()


def test_crashed_helper_claim_expires_without_resetting_authority(ledger):
    state, clock = ledger
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_process_exchange, args=(state.path, clock[0], child))
    process.start()
    try:
        assert parent.poll(5) and parent.recv() == "claimed"
        process.terminate()  # Deliberate failure injection while the persisted claim is active.
        process.join(5)
        blocked = reopen(state, clock).authenticate(lambda: pytest.fail("Live claim bypass"))
        assert blocked.session is None
        clock[0] += 16
        assert reopen(state, clock).authenticate(session).session is not None
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
        child.close()


@pytest.mark.parametrize("retry,delay", [("999999", 300), ("-1", 0), ("tomorrow", 0), (None, 0)])
def test_untrusted_retry_after_is_bounded(retry, delay):
    with pytest.raises(ExchangeFailure) as error:
        parse_exchange_response(503, b"ignored", content_type="text/plain", retry_after=retry)
    assert error.value.retry_after == delay


def test_tls_failure_is_persisted_without_calling_retry_jitter(ledger):
    state, clock = ledger
    state = BrowserDeviceRecovery(state.path, IDENTITY, clock=lambda: clock[0],
                                   jitter=lambda: pytest.fail("Terminal failure needs no jitter"))
    assert state.authenticate(fail(RecoveryMode.TLS_ERROR)).status.mode is RecoveryMode.TLS_ERROR


def test_unsafe_state_after_exchange_prevents_token_handoff(ledger):
    state, clock = ledger

    def exchange():
        state.path.chmod(0o644)
        return session()

    with pytest.raises(BrowserRecoveryError) as error:
        state.authenticate(exchange)
    assert TOKEN not in str(error.value)
