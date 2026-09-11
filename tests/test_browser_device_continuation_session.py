"""Initial issuance fixtures; real files/locks/SQL, simulated consent and ancestry.

Selected cases use real verified TLS. No cookie is installed or ordinary browser
role enabled; a native ACTIVE result is not browser readiness.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import closing, contextmanager, suppress
from dataclasses import FrozenInstanceError

import pytest

from sds200 import browser_device_continuation_approval as approval
from sds200 import browser_device_continuation_current as current
from sds200 import browser_device_continuation_epoch as epoch
from sds200 import browser_device_continuation_ownership as ownership
from sds200 import browser_device_continuation_session as session
from sds200 import browser_device_continuation_verification as verification
from sds200 import browser_device_verification as transport
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from sds200.browser_device_recovery import ExchangeFailure, ExchangeSession, RecoveryMode
from sds200.browser_device_startup import _launch_lock
from sds200.browser_device_worker import BrowserWorkerSelection
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_continuation_approval import expected, unrelated
from tests.test_browser_device_continuation_current import (
    activation_candidate as activation_candidate,
)
from tests.test_browser_device_continuation_current import candidate as candidate
from tests.test_browser_device_continuation_current import native_step
from tests.test_browser_device_continuation_ownership import handoff as handoff
from tests.test_browser_device_continuation_verification import INTENT, phase, proof
from tests.test_browser_device_continuation_verification import profile as profile
from tests.test_browser_device_guard_release import blocked
from tests.test_browser_device_native import TOKEN
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import root as root
from tests.test_browser_device_native import server as server
from tests.test_browser_device_profile import CREDENTIAL, private
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_resume_workflow import snap
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux owned initial-session fixtures")
ERROR = session.BrowserContinuationSessionError


@pytest.fixture
def access(lab, candidate, monkeypatch):
    lab.clock[0] += 2
    selection = BrowserWorkerSelection(candidate.args["bundle"], candidate.args["public_key"])
    monkeypatch.setattr(current, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root)

    @contextmanager
    def scope():
        obj = session._BrowserWorkerInitialSession(lab.configuration, selection,
            clock=lambda: lab.clock[0], monotonic=lambda: lab.elapsed[0])
        with _launch_lock(candidate.root, create=False):
            yield obj

    return scope


def run(obj, state, **kwargs):
    return obj.run(state, **{**dict(intent=INTENT, reviewed_generation=7,
                                   consent=lambda review: review), **kwargs})


def no_replay(obj, state, lab):
    before = snap(lab)
    with pytest.raises(ERROR):
        run(obj, state)
    assert snap(lab) == before


@pytest.mark.parametrize("delay", [0, 1, 9.9])
@pytest.mark.parametrize("clocks", ["wall", "mono", "both"])
def test_one_initial_session_exact_owned_state_and_conservative_lifetime(
        lab, candidate, access, monkeypatch, delay, clocks):
    state = expected(candidate)
    before = unrelated(lab)
    calls, after = [], []

    def verify(config, record):
        calls.append("verify")
        assert phase(lab) == [("claimed",)] and record.generation == 7
        return proof(config, record)

    def exchange(config, generation):
        calls.append("exchange")
        assert config == lab.configuration and generation == 7
        assert phase(lab) == [("complete",)]
        with closing(sqlite3.connect(lab.ledger.path, timeout=0)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.rollback()
        for name in ("profile", "archives"):
            with pytest.raises(BrowserProfileAccessError), browser_profile_access(
                    lab.args[name], exclusive=True):
                pytest.fail("Private writer acquired during issuance")
        with pytest.raises(BlockingIOError), _launch_lock(candidate.root, create=False):
            pytest.fail("Competing launcher")
        after.append(lab.ledger.path.read_bytes())
        if clocks in {"wall", "both"}:
            lab.clock[0] += delay
        if clocks in {"mono", "both"}:
            lab.elapsed[0] += delay
        return ExchangeSession(TOKEN, 300)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation", exchange)
    with access() as obj:
        result = run(obj, state)
        assert result.state.mode is RecoveryMode.ACTIVE
        assert result.state.native_revision == state.native_revision + 2
        assert result.session.token == TOKEN
        assert result.session.expires_in == pytest.approx(300 - delay)
        assert calls == ["verify", "exchange"] and unrelated(lab) == before
        assert lab.ledger.path.read_bytes() == after[0]
        assert obj.confirm().state == result.state
        assert not hasattr(obj.confirm(), "session")
        assert not hasattr(obj, "_session")
        assert TOKEN not in repr(result) + repr(obj) + repr(obj.__dict__)
        assert CREDENTIAL not in repr(result)
        with pytest.raises(FrozenInstanceError):
            result.session = None
        no_replay(obj, state, lab)
        lab.clock[0] += 1000
        lab.elapsed[0] += 1000
        assert obj.confirm().state == result.state and calls == ["verify", "exchange"]
    for root_dir in (lab.args["profile"], candidate.root):
        for path in root_dir.rglob("*"):
            if path.is_file() and not path.is_symlink():
                assert TOKEN.encode() not in path.read_bytes()
    with access() as other, pytest.raises(ERROR):
        run(other, result.state)  # Completed grant is not a reusable issuance ticket.
    blocked(lab)


@pytest.mark.parametrize("failure", ["exception", "interrupt", "exit", "none", "dict", "subclass",
    "bad-token", "bool-lifetime", "nan-lifetime", "expired", "too-long", "short-lifetime"])
def test_uncertain_or_malformed_issuance_keeps_native_state_and_never_retries(
        lab, candidate, access, monkeypatch, failure):
    state = expected(candidate)
    calls, after = [], []

    def exchange(*args):
        calls.append(1)
        after.append(lab.ledger.path.read_bytes())
        if failure in {"exception", "interrupt", "exit"}:
            raise {"exception": RuntimeError, "interrupt": KeyboardInterrupt,
                   "exit": SystemExit}[failure]("PRIVATE " + TOKEN)
        if failure == "none":
            return None
        if failure == "dict":
            return {"token": TOKEN, "expires_in": 300}
        if failure == "subclass":
            class Subclass(ExchangeSession):
                pass
            return Subclass(TOKEN, 300)
        issued = object.__new__(ExchangeSession)
        object.__setattr__(issued, "token", "PRIVATE" if failure == "bad-token" else TOKEN)
        object.__setattr__(issued, "expires_in", {"bool-lifetime": True,
            "nan-lifetime": float("nan"), "expired": 0, "too-long": 3601,
            "short-lifetime": 1}.get(failure, 300))
        if failure == "short-lifetime":
            lab.elapsed[0] += 0.1
        return issued

    monkeypatch.setattr(transport, "verify_browser_device", proof)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation", exchange)
    with access() as obj:
        with pytest.raises({"interrupt": KeyboardInterrupt, "exit": SystemExit}.get(
                failure, ERROR)) as error:
            run(obj, state)
        if failure not in {"interrupt", "exit"}:
            assert TOKEN not in str(error.value) and "PRIVATE" not in str(error.value)
        assert obj.confirm().phase == "complete"
        assert obj.confirm().state.mode is RecoveryMode.ACTIVE
        assert lab.ledger.path.read_bytes() == after[0]
        no_replay(obj, state, lab)
        assert calls == [1]
    blocked(lab)


@pytest.mark.parametrize("lost", ["prepare", "claim", "proof", "complete", "return"])
@pytest.mark.parametrize("committed", [False, True])
def test_failed_or_lost_verification_acknowledgement_never_issues(
        lab, candidate, access, monkeypatch, lost, committed):
    state = expected(candidate)
    commits, proofs = [], []

    def commit(db):
        commits.append(1)
        if lost == "claim" and len(commits) == 1:
            db.commit()
            return
        if committed:
            db.commit()
        raise RuntimeError("PRIVATE lost acknowledgement")

    def verify(config, record):
        proofs.append(record)
        if lost == "proof":
            raise RuntimeError("PRIVATE refusal")
        return proof(config, record)

    original = verification._BrowserWorkerVerification.run

    def lost_return(obj, *args, **kwargs):
        original(obj, *args, **kwargs)
        raise RuntimeError("PRIVATE lost successful return")

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation",
                        lambda *a: pytest.fail("Issuance after unacknowledged verification"))
    if lost in {"prepare", "claim"}:
        monkeypatch.setattr(approval, "_commit", commit)
    elif lost == "complete":
        monkeypatch.setattr(verification, "_commit", commit)
    elif lost == "return":
        monkeypatch.setattr(verification._BrowserWorkerVerification, "run", lost_return)
    with access() as obj:
        with pytest.raises(ERROR):
            run(obj, state)
        if lost == "return" or (lost == "complete" and committed):
            assert obj.confirm().phase == "complete"  # Readback cannot restore the gate.
        else:
            with suppress(ERROR):
                assert obj.confirm().phase == "failed"
        no_replay(obj, state, lab)
        assert len(proofs) == (0 if lost in {"prepare", "claim"} else 1)
    blocked(lab)


def mutate(change, lab, candidate, monkeypatch):
    if change == "pause":
        native_step(lab, candidate, "pause")
    elif change == "same-revision":
        selected = epoch._EpochSelection(candidate.paths["manifest"].read_bytes(),
            current.activation._binding(candidate.paths["manifest"]), candidate.history,
            lab.args["profile"], current.activation._binding(lab.ledger.path))
        with current.activation._ledger_transaction(lab.args["profile"], readonly=False) as pair:
            db, _ = pair
            view = epoch._read(db, selected, readonly=False)
            epoch._write(db, selected, view, view.approvals,
                {**view.state, "observed_at": lab.clock[0] + 1}, view.grant)
            db.commit()
    elif change in {"ledger-inode", "owner-inode"}:
        path = lab.ledger.path if change == "ledger-inode" else (
            candidate.root / ".sdsctl-device-launch.lock")
        path.rename(path.with_name(path.name + ".retained"))
        private(path, path.with_name(path.name + ".retained").read_bytes())
    elif change == "owner":
        candidate.captures[-1][1]._active = False
    elif change in {"parent", "process"}:
        monkeypatch.setattr(session.os, "getppid" if change == "parent" else "getpid", lambda: -1)
    elif change.startswith(("wall", "mono")) or change in {"nan", "bool"}:
        target = lab.elapsed if change.startswith("mono") else lab.clock
        target[0] = float("nan") if change == "nan" else True if change == "bool" else (
            target[0] + (-1 if change.endswith("backstep") else 10))
    else:
        path = candidate.paths[change]
        path.write_bytes(path.read_bytes() + b" ")


@pytest.mark.parametrize("when", ["before", "during"])
@pytest.mark.parametrize("change", ["pause", "same-revision", "credential", "trust", "bundle",
    "manifest", "release", "intent", "ledger-inode", "owner-inode", "owner", "parent", "process",
    "wall-backstep", "mono-backstep", "wall-expired", "mono-expired", "nan", "bool"])
def test_changed_state_inputs_owner_or_time_never_returns_session(
        lab, candidate, access, monkeypatch, when, change):
    state = expected(candidate)
    calls, after = [], []
    original = verification._BrowserWorkerVerification.run

    def completed(obj, *args, **kwargs):
        result = original(obj, *args, **kwargs)
        if when == "before":
            if change == "owner":
                obj._approval._owner_binding = None  # Lost original owner binding.
            else:
                mutate(change, lab, candidate, monkeypatch)
            after.append(lab.ledger.path.read_bytes())
        return result

    def exchange(*args):
        calls.append(1)
        if when == "during":
            mutate(change, lab, candidate, monkeypatch)
            after.append(lab.ledger.path.read_bytes())
        return ExchangeSession(TOKEN, 300)

    monkeypatch.setattr(verification._BrowserWorkerVerification, "run", completed)
    monkeypatch.setattr(transport, "verify_browser_device", proof)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation", exchange)
    with access() as obj:
        with pytest.raises(ERROR):
            run(obj, state)
        assert lab.ledger.path.read_bytes() == after[0]
        assert len(calls) == (when == "during")
        no_replay(obj, state, lab)


@pytest.mark.parametrize("clock", ["wall", "mono"])
@pytest.mark.parametrize("phase_name", ["consent", "proof"])
def test_total_budget_spans_verification_and_session(lab, candidate, access, monkeypatch,
                                                     clock, phase_name):
    state = expected(candidate)
    target = lab.clock if clock == "wall" else lab.elapsed
    calls = []

    def consent(review):
        if phase_name == "consent":
            target[0] += 6
        return review

    def verify(config, record):
        if phase_name == "proof":
            target[0] += 6
        return proof(config, record)

    def exchange(*args):
        calls.append(1)
        target[0] += 4
        return ExchangeSession(TOKEN, 300)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation", exchange)
    with access() as obj:
        with pytest.raises(ERROR):
            run(obj, state, consent=consent)
        assert obj.confirm().phase == "complete"
        no_replay(obj, state, lab)
        assert calls == [1]


@pytest.mark.parametrize("profile", ["dns", "ip"], indirect=True)
def test_actual_verified_tls_issues_exact_generation_once(lab, candidate, access, server,
                                                        monkeypatch):
    _, response, observed = server
    response["body"] = json.dumps(dict(version=1, device_id="display", generation=7,
        state="active", drained=True)).encode()
    original = transport.verify_browser_device

    def verify(*args):
        result = original(*args)
        response["body"] = json.dumps(dict(token=TOKEN, expires_in=300, generation=7)).encode()
        return result

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("SSL_CERT_FILE", "/fictional/unreadable")
    state = expected(candidate)
    with access() as obj:
        result = run(obj, state)
        assert result.session.token == TOKEN
        assert obj.confirm().state == result.state
        no_replay(obj, result.state, lab)
    assert [call[0] for call in observed] == ["/auth/device/verify", "/auth/device/session"]
    for _, headers, body in observed:
        assert json.loads(body) == dict(device_id="display", generation=7)
        assert headers["Authorization"] == "Bearer " + CREDENTIAL
        assert not {"Cookie", "Origin"} & headers.keys()
    blocked(lab)


@pytest.mark.parametrize("profile", ["dns"], indirect=True)
@pytest.mark.parametrize("failure", ["generation", "bool-generation", "malformed", "duplicate",
    "redirect", "cookie", "compressed", "401", "429", "503"])
def test_actual_tls_issuance_refusal_not_retried(lab, candidate, access, server, monkeypatch,
                                              failure):
    _, response, observed = server
    response["body"] = json.dumps(dict(version=1, device_id="display", generation=7,
        state="active", drained=True)).encode()
    original = transport.verify_browser_device

    def verify(*args):
        result = original(*args)
        value = dict(token=TOKEN, expires_in=300, generation=7)
        if failure in {"generation", "bool-generation"}:
            value["generation"] = 8 if failure == "generation" else True
        response["body"] = json.dumps(value).encode()
        if failure == "malformed":
            response["body"] = b"PRIVATE invalid"
        elif failure == "duplicate":
            response["body"] = response["body"][:-1] + b',"generation":7}'
        elif failure == "redirect":
            response.update(status=302, headers=[("Location", "https://example.invalid/private")])
        elif failure == "cookie":
            response["headers"] = [("Set-Cookie", "private=fictional")]
        elif failure == "compressed":
            response["headers"] = [("Content-Encoding", "gzip")]
        elif failure.isdigit():
            response.update(status=int(failure), headers=[("Retry-After", "60")])
        return result

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    state = expected(candidate)
    with access() as obj:
        with pytest.raises(ERROR) as error:
            run(obj, state)
        assert TOKEN not in str(error.value) and "PRIVATE" not in str(error.value)
        assert obj.confirm().phase == "complete"
        no_replay(obj, state, lab)
    assert [call[0] for call in observed] == ["/auth/device/verify", "/auth/device/session"]
    blocked(lab)


@pytest.mark.parametrize("failure", ["none", "copied", "failed", "inputs", "selection"])
def test_unsuccessful_or_altered_verification_result_is_not_a_ticket(
        lab, candidate, access, monkeypatch, failure):
    from dataclasses import replace

    original = verification._BrowserWorkerVerification.run

    def altered(obj, *args, **kwargs):
        result = original(obj, *args, **kwargs)
        if failure == "none":
            return None
        if failure == "copied":
            return replace(result)
        if failure == "failed":
            obj._after = replace(result, phase="failed")
            return obj._after
        if failure == "inputs":
            obj._approval._inputs = None
        else:
            obj._approval._selected = None
        return result

    monkeypatch.setattr(verification._BrowserWorkerVerification, "run", altered)
    monkeypatch.setattr(transport, "verify_browser_device", proof)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation",
                        lambda *a: pytest.fail("Issued for altered verification result"))
    state = expected(candidate)
    with access() as obj:
        with pytest.raises(ERROR):
            run(obj, state)
        no_replay(obj, state, lab)


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf")])
@pytest.mark.parametrize("clock", ["wall", "mono"])
def test_invalid_initial_clock_consumes_attempt_without_consent_or_network(
        lab, candidate, access, monkeypatch, value, clock):
    state = expected(candidate)
    monkeypatch.setattr(transport, "verify_browser_device", lambda *a: pytest.fail("Proof"))
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation",
                        lambda *a: pytest.fail("Issuance"))
    with access() as obj:
        target = lab.clock if clock == "wall" else lab.elapsed
        old = target[0]
        before = snap(lab)
        target[0] = value
        with pytest.raises(ERROR):
            run(obj, state, consent=lambda *_: pytest.fail("Consent"))
        target[0] = old
        assert snap(lab) == before
        no_replay(obj, state, lab)
        with pytest.raises(ERROR):
            obj.confirm()


@pytest.mark.parametrize("clock", ["wall", "mono"])
def test_success_lifetime_also_includes_prior_verification_time(
        lab, candidate, access, monkeypatch, clock):
    target = lab.clock if clock == "wall" else lab.elapsed

    def verify(config, record):
        target[0] += 3
        return proof(config, record)

    def exchange(*args):
        target[0] += 2
        return ExchangeSession(TOKEN, 300)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation", exchange)
    state = expected(candidate)
    with access() as obj:
        result = run(obj, state)
        assert result.session.expires_in == 295


@pytest.mark.parametrize("failure", ["lost", "interrupt", "expired", "parent"])
def test_post_issuance_readback_or_owner_exit_failure_does_not_return_session(
        lab, candidate, access, monkeypatch, failure):
    original = verification._BrowserWorkerVerification._confirm_owned
    after, calls = [], []

    def exchange(*args):
        calls.append(1)
        after.append(lab.ledger.path.read_bytes())

        def failed_read(*args):
            original(*args)
            if failure in {"lost", "interrupt"}:
                raise (KeyboardInterrupt if failure == "interrupt" else RuntimeError)(
                    "PRIVATE " + TOKEN)
            if failure == "expired":
                lab.elapsed[0] += 10
            else:
                monkeypatch.setattr(session.os, "getppid", lambda: -1)
            return original(*args)

        monkeypatch.setattr(verification._BrowserWorkerVerification, "_confirm_owned", failed_read)
        return ExchangeSession(TOKEN, 300)

    monkeypatch.setattr(transport, "verify_browser_device", proof)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation", exchange)
    state = expected(candidate)
    with access() as obj:
        with pytest.raises(KeyboardInterrupt if failure == "interrupt" else ERROR):
            run(obj, state)
        assert lab.ledger.path.read_bytes() == after[0]
        assert calls == [1]


@pytest.mark.parametrize("outcome", [
    "issued", "lost", "rotated", "paused", "revoked", "aba", "full"])
def test_real_server_authority_rechecks_generation_and_preserves_other_display(
        lab, candidate, access, tmp_path, monkeypatch, outcome):
    import asyncio

    from sds200 import browser_device_store as store_module
    from sds200.browser_device_sessions import BrowserDeviceSessions

    authority = tmp_path / "fictional-server-authority"
    authority.mkdir(mode=0o700)
    store = store_module.BrowserDeviceStore.initialize(authority / "devices.sqlite")
    with monkeypatch.context() as fixed:
        fixed.setattr(store_module.secrets, "token_hex", lambda n: "b" * 64)
        device = store.enroll("display")
    assert device.credential == CREDENTIAL
    for _ in range(3):
        store.transition("display", store_module.BrowserDeviceState.PAUSED)
        store.transition("display", store_module.BrowserDeviceState.ACTIVE)
    other_device = store.enroll("other-display")
    manager = BrowserDeviceSessions(store, max_sessions=1 if outcome == "full" else 2,
                                     max_sessions_per_device=1)
    other = manager.issue_or_raise("other-display", other_device.credential)
    calls, issued = [], []

    def verify(config, record):
        actual = manager.verify_for_resume(config.device_id, CREDENTIAL,
                                            expected_generation=record.generation)
        assert actual == record
        return transport.BrowserVerifiedRecord(config.identity, actual, True)

    def exchange(config, generation):
        calls.append(generation)
        if outcome == "rotated":
            store.rotate("display")
        elif outcome in {"paused", "revoked", "aba"}:
            store.transition("display", store_module.BrowserDeviceState(
                "paused" if outcome == "aba" else outcome))
            if outcome == "aba":
                store.transition("display", store_module.BrowserDeviceState.ACTIVE)
        token = manager.issue_or_raise(config.device_id, CREDENTIAL,
                                        expected_generation=generation)
        if token is None:
            raise ExchangeFailure(RecoveryMode.REJECTED)
        issued.append(token)
        if outcome == "lost":
            raise RuntimeError("PRIVATE response lost after issuance")
        return ExchangeSession(token.token, token.lifetime_seconds)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation", exchange)
    state = expected(candidate)
    with access() as obj:
        if outcome == "issued":
            result = run(obj, state)
            assert result.session.token == issued[0].token
        else:
            with pytest.raises(ERROR):
                run(obj, state)
        assert obj.confirm().phase == "complete"
        no_replay(obj, state, lab)
        assert calls == [7] and len(issued) == (outcome in {"issued", "lost"})

    async def still_valid(token):
        lease = manager.acquire(token)
        assert lease is not None
        lease.release()

    asyncio.run(still_valid(other.token))
    if outcome == "lost":
        asyncio.run(still_valid(issued[0].token))  # Lost reply is NOT remote revocation.
    for token in issued:
        assert token.token.encode() not in lab.ledger.path.read_bytes()
    blocked(lab)
