"""Unwired experimental native resume approvals; not a CLI or native action.

Trusted callers supply exact browser intent and reviewed state. Low-level prepare/
commit accepts trusted evidence; verified wrappers obtain private TLS proof and a
generation-bound session. No browser storage or server authority is mutated. A
future bridge must bound process lifetime and establish trusted browser consent.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from .browser_device_admin import BrowserAdminResult, BrowserAdminStatus
from .browser_device_native import (
    BrowserNativeConfiguration,
    _private_read,
    load_browser_native_configuration,
)
from .browser_device_profile import _credential, _platform, _trust
from .browser_device_recovery import (
    BrowserDeviceRecovery,
    BrowserRecoveryError,
    ExchangeSession,
    RecoveryMode,
    RecoveryResult,
    RecoveryStatus,
    _State,
)
from .browser_device_store import (
    BrowserDeviceRecord,
    BrowserDeviceState,
    validate_browser_device_record,
)
from .browser_device_verification import (
    BrowserVerifiedRecord,
    exchange_browser_device_at_generation,
    verify_browser_device,
)

_TABLE = "browser_resume"
_COLUMNS = (
    "digest",
    "identity",
    "phase",
    "revision",
    "mode",
    "intent",
    "device",
    "generation",
    "credential_hash",
    "trust_hash",
    "created",
    "expires",
)
_PHASES = {"prepared", "claimed", "complete", "cancelled", "failed"}
_MAX_APPROVALS = 128
_LIFETIME = 120


class BrowserResumeError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Resume was refused or could not be confirmed. Retain state for review; "
            "do not replay an approval or reset recovery files."
        )


@dataclass(frozen=True, slots=True)
class BrowserResumeApproval:
    """One-use private handoff. Never generically serialize or log this object."""

    ticket: str = field(repr=False)
    revision: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class BrowserResumeEvidence:
    """Trusted adapter's context-bound evidence, NOT a signed/network credential."""

    identity: str
    result: BrowserAdminResult


def _hex(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _integer(value: object) -> bool:
    return type(value) is int and 1 <= value < 2**53 - 1


def _timestamp(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value < 2**53
    )


def validate_resume_state(db: sqlite3.Connection) -> None:
    """Validate version-2 ledger extension on EVERY open, including read-only check."""
    if tuple(row[1] for row in db.execute(f"PRAGMA table_info({_TABLE})")) != _COLUMNS:
        raise BrowserRecoveryError()
    rows = db.execute(f"SELECT * FROM {_TABLE} LIMIT ?", (_MAX_APPROVALS + 1,)).fetchall()
    if not 1 <= len(rows) <= _MAX_APPROVALS:
        raise BrowserRecoveryError()
    pending = 0
    seen = set()
    installation = db.execute("SELECT identity,revision,mode FROM recovery WHERE id=1").fetchone()
    if installation is None:
        raise BrowserRecoveryError()
    for (
        digest,
        identity,
        phase,
        revision,
        mode,
        intent,
        device,
        generation,
        secret,
        trust,
        created,
        expires,
    ) in rows:
        if (
            not all(_hex(value) for value in (digest, identity, intent, secret, trust))
            or identity != installation[0]
            or digest in seen
            or phase not in _PHASES
            or not _integer(revision)
            or not _integer(generation)
            or mode
            not in {value.value for value in RecoveryMode if value is not RecoveryMode.ACTIVE}
            or not _timestamp(created)
            or not _timestamp(expires)
            or expires - created != _LIFETIME
        ):
            raise BrowserRecoveryError()
        validate_browser_device_record(
            BrowserDeviceRecord(device, generation, BrowserDeviceState.ACTIVE)
        )
        if phase in {"prepared", "claimed"} and (revision, mode) != installation[1:]:
            raise BrowserRecoveryError()
        seen.add(digest)
        pending += phase in {"prepared", "claimed"}
    if pending > 1:
        raise BrowserRecoveryError()


def cancel_resume_approvals(db: sqlite3.Connection) -> bool:
    """Called inside the native pause/reset transaction; no schema creation."""
    if db.execute("PRAGMA user_version").fetchone()[0] != 2:
        return False
    return bool(
        db.execute(
            "UPDATE browser_resume SET phase='cancelled' WHERE phase IN ('prepared','claimed')"
        ).rowcount
    )


class BrowserDeviceResume:
    """Native transaction core only. Same-account/root processes are trusted.

    Explicit prepare upgrades this one ledger to schema 2. Old helpers fail closed
    instead of ignoring cancellation. No ordinary status/start call migrates it.
    Only grant digests and input fingerprints are stored, never credentials,
    private ticket text, server cookies or browser state contents.
    """

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        try:
            _platform()
            self._configuration = load_browser_native_configuration(root)
            self._recovery = BrowserDeviceRecovery(
                root / "recovery.sqlite",
                self._configuration.identity,
                clock=clock,
            )
            self._monotonic = monotonic
        except Exception:
            raise BrowserResumeError() from None

    def _inputs(self) -> tuple[str, str]:
        root = self._configuration.root
        if load_browser_native_configuration(root).identity != self._configuration.identity:
            raise BrowserResumeError()
        secret = _private_read(root, "device.secret", 128)
        _credential(secret)
        trust = _trust(_private_read(root, "ca.pem", 128 * 1024))
        return hashlib.sha256(secret).hexdigest(), trust

    def _proof(
        self,
        evidence: BrowserResumeEvidence | BrowserVerifiedRecord,
        expected: BrowserDeviceRecord | None = None,
    ) -> BrowserDeviceRecord:
        if isinstance(evidence, BrowserVerifiedRecord):
            record = evidence.record
            validate_browser_device_record(record)
            if (evidence.identity != self._configuration.identity or evidence.drained is not True
                    or record.device_id != self._configuration.device_id
                    or record.state is not BrowserDeviceState.ACTIVE
                    or (expected is not None and record != expected)):
                raise BrowserResumeError()
            return record
        if (
            not isinstance(evidence, BrowserResumeEvidence)
            or evidence.identity != self._configuration.identity
            or not isinstance(evidence.result, BrowserAdminResult)
        ):
            raise BrowserResumeError()
        proof = evidence.result
        record = proof.record
        validate_browser_device_record(record)
        if (
            record.device_id != self._configuration.device_id
            or record.state is not BrowserDeviceState.ACTIVE
            or (expected is not None and record != expected)
            or proof.status is not BrowserAdminStatus.CONFIRMED
            or proof.receipt is None
            or proof.receipt.record != record
            or proof.receipt.completed is not True
        ):
            raise BrowserResumeError()
        return record

    def prepare(
        self, *, expected_revision: int, browser_intent: str,
        reviewed_server: BrowserResumeEvidence | BrowserVerifiedRecord,
    ) -> BrowserResumeApproval:
        """Explicit trusted approval: retain stopped mode; create one short-lived grant.

        Authentication/consent are the caller's job. A first-run profile, another
        pending approval, unsafe inputs, stale revision or absent ACK is refused.
        Preparation never contacts a server or starts/clears browser recovery.
        """
        try:
            if not _integer(expected_revision) or not _hex(browser_intent):
                raise BrowserResumeError()
            record = self._proof(reviewed_server)
            secret, trust = self._inputs()
            ticket = secrets.token_hex(32)
            digest = hashlib.sha256(ticket.encode("ascii")).hexdigest()
            now = self._recovery._now()
            if not _timestamp(now) or not _timestamp(now + _LIFETIME):
                raise BrowserResumeError()
            with self._recovery._connection() as db:
                state = self._recovery._load(db, now, correct_clock=False)
                if (
                    state.mode is RecoveryMode.ACTIVE
                    or state.revision != expected_revision
                    or now < state.observed_at
                    or not _integer(state.revision + 1)
                ):
                    raise BrowserResumeError()
                if db.execute("PRAGMA user_version").fetchone()[0] == 1:
                    db.execute(
                        "CREATE TABLE browser_resume (digest TEXT PRIMARY KEY, "
                        "identity TEXT, phase TEXT, "
                        "revision INTEGER, mode TEXT, intent TEXT, device TEXT, "
                        "generation INTEGER, credential_hash TEXT, trust_hash TEXT, "
                        "created REAL, expires REAL)"
                    )
                    db.execute("PRAGMA user_version=2")
                if (
                    db.execute("SELECT COUNT(*) FROM browser_resume").fetchone()[0]
                    >= _MAX_APPROVALS
                    or db.execute(
                        "SELECT 1 FROM browser_resume WHERE phase IN ('prepared','claimed')"
                    ).fetchone()
                    is not None
                ):
                    raise BrowserResumeError()
                revised = replace(state, revision=state.revision + 1, observed_at=now)
                self._recovery._save(db, revised)
                db.execute(
                    "INSERT INTO browser_resume VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        digest,
                        self._configuration.identity,
                        "prepared",
                        revised.revision,
                        state.mode.value,
                        browser_intent,
                        record.device_id,
                        record.generation,
                        secret,
                        trust,
                        now,
                        now + _LIFETIME,
                    ),
                )
                validate_resume_state(db)
            return BrowserResumeApproval(ticket, revised.revision, now + _LIFETIME)
        except Exception:
            raise BrowserResumeError() from None

    def _failed(self, digest: str) -> None:
        # Never overwrite a concurrent cancellation or confirmed completion.
        try:
            with self._recovery._connection() as db:
                db.execute(
                    "UPDATE browser_resume SET phase='failed' WHERE digest=? AND phase='claimed'",
                    (digest,),
                )
        except Exception:
            pass  # An uncertain claimed operation is still consumed, never retryable.

    def prepare_verified(
        self, *, expected_revision: int, browser_intent: str, expected_generation: int,
    ) -> BrowserResumeApproval:
        """Trusted consent adapter: verify EXACT reviewed generation using private TLS.

        Requires an independent process deadline. No protocol/CLI/page exposes it.
        Do not infer consent from browser starts, a credential or a successful GET.
        """
        try:
            current = self._recovery.inspect()
            if (not _integer(expected_generation) or not _integer(expected_revision)
                    or not _hex(browser_intent) or current.mode is RecoveryMode.ACTIVE
                    or current.revision != expected_revision):
                raise BrowserResumeError()
            self._inputs()
            proof = verify_browser_device(self._configuration, BrowserDeviceRecord(
                self._configuration.device_id, expected_generation, BrowserDeviceState.ACTIVE))
            return self.prepare(expected_revision=expected_revision, browser_intent=browser_intent,
                                reviewed_server=proof)
        except Exception:
            raise BrowserResumeError() from None

    def commit_session(
        self, approval: BrowserResumeApproval, *, browser_intent: str,
    ) -> RecoveryResult:
        """Verify, consume native approval, then separately issue a generation-bound session.

        No browser state/cookie changes here. On ANY uncertain result the browser
        must retain its pending pause and never replay this operation. Run under
        an independent process deadline, not merely per-socket timeouts.
        """
        try:
            inputs = self._inputs()
            generation: int | None = None

            def prove(
                config: BrowserNativeConfiguration, record: BrowserDeviceRecord,
            ) -> BrowserVerifiedRecord:
                nonlocal generation
                proof = verify_browser_device(config, record)
                generation = record.generation
                return proof

            status = self.commit(approval, browser_intent=browser_intent, prove_server=prove)
            if generation is None or self._inputs() != inputs:
                raise BrowserResumeError()

            def exchange() -> ExchangeSession:
                if self._inputs() != inputs or generation is None:
                    raise BrowserResumeError()
                session = exchange_browser_device_at_generation(self._configuration, generation)
                if self._inputs() != inputs:
                    raise BrowserResumeError()
                return session

            return self._recovery.authenticate(exchange, expected_revision=status.revision)
        except Exception:
            raise BrowserResumeError() from None

    def commit(
        self,
        approval: BrowserResumeApproval,
        *,
        browser_intent: str,
        prove_server: Callable[
            [BrowserNativeConfiguration, BrowserDeviceRecord],
            BrowserResumeEvidence | BrowserVerifiedRecord,
        ],
    ) -> RecoveryStatus:
        """Consume BEFORE external proof, then atomically authorize the native ledger.

        A lost response cannot replay the operation. Proof must authenticate its
        transport and return an exact current, drained server record. This method
        checks a ten-second elapsed bound but cannot kill a blocked callback: a
        future native supervisor must enforce its independent process deadline.
        Success means native permission only, never browser/session readiness.
        """
        digest = None
        claimed = False
        try:
            if (
                not isinstance(approval, BrowserResumeApproval)
                or not _hex(approval.ticket)
                or not _integer(approval.revision)
                or not _timestamp(approval.expires_at)
                or not _hex(browser_intent)
                or not callable(prove_server)
            ):
                raise BrowserResumeError()
            digest = hashlib.sha256(approval.ticket.encode("ascii")).hexdigest()
            secret, trust = self._inputs()
            now = self._recovery._now()
            with self._recovery._connection() as db:
                row = db.execute(
                    "SELECT * FROM browser_resume WHERE digest=?", (digest,)
                ).fetchone()
                state = self._recovery._load(db, now, correct_clock=False)
                if row is None:
                    raise BrowserResumeError()
                (
                    _,
                    identity,
                    phase,
                    revision,
                    mode,
                    intent,
                    device,
                    generation,
                    old_secret,
                    old_trust,
                    start,
                    end,
                ) = row
                if (
                    identity != self._configuration.identity
                    or phase != "prepared"
                    or revision != approval.revision
                    or end != approval.expires_at
                    or browser_intent != intent
                    or state.revision != revision
                    or state.mode.value != mode
                    or (secret, trust) != (old_secret, old_trust)
                ):
                    raise BrowserResumeError()
                expired = now < state.observed_at or not start <= now < end
                db.execute(
                    "UPDATE browser_resume SET phase=? WHERE digest=?",
                    ("failed" if expired else "claimed", digest),
                )
            if expired:
                # Persist refusal so a later wall-clock rollback cannot revive it.
                raise BrowserResumeError()
            claimed = True
            expected = BrowserDeviceRecord(device, generation, BrowserDeviceState.ACTIVE)
            started = self._monotonic()
            self._proof(prove_server(self._configuration, expected), expected)
            elapsed = self._monotonic() - started
            if not math.isfinite(elapsed) or not 0 <= elapsed <= 10:
                raise BrowserResumeError()
            if self._inputs() != (secret, trust):
                raise BrowserResumeError()
            now = self._recovery._now()
            with self._recovery._connection() as db:
                current = self._recovery._load(db, now, correct_clock=False)
                phase = db.execute(
                    "SELECT phase FROM browser_resume WHERE digest=?", (digest,)
                ).fetchone()
                if (
                    phase != ("claimed",)
                    or current.revision != revision
                    or current.mode.value != mode
                    or now < current.observed_at
                    or not start <= now < end
                    or not _integer(current.revision + 1)
                ):
                    raise BrowserResumeError()
                resumed = _State(current.revision + 1, RecoveryMode.ACTIVE, 0, 0, now)
                self._recovery._save(db, resumed)
                db.execute("UPDATE browser_resume SET phase='complete' WHERE digest=?", (digest,))
            return self._recovery._status(resumed, now)
        except Exception:
            if claimed and digest is not None:
                self._failed(digest)
            raise BrowserResumeError() from None
