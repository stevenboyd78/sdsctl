"""Read-only retained recovery history, deliberately NOT current permission.

The completed release and administrator-intent journals anchor the archive plan.
Trusted writers validated that plan against the live ledger before these commits.
This reader never relaxes existing confirm methods, enables launch, consumes an
approval, or inspects Chromium storage. Same-account/root edits remain trusted;
these local records are not signed evidence against a hostile administrator.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import browser_device_continuation_intent as intent_journal
from . import browser_device_guard_release as release_journal
from .browser_device_bundle import NATIVE_HOST, _json
from .browser_device_handoff import BrowserRecoveryHandoff, _lock_file
from .browser_device_native import _private_read, load_browser_native_configuration
from .browser_device_profile_access import browser_profile_access
from .browser_device_recovery import RecoveryMode, _object
from .browser_device_registration import MAINTENANCE_MARKER, _canonical_bundle_files, _receipt
from .browser_device_resume import _hex, _timestamp
from .browser_device_resume_archive import (
    BrowserResumeArchiveBinding,
    _encoded,
    _fingerprint,
    inspect_resume_archive,
)
from .browser_device_resume_boundary import BrowserResumeBoundary
from .browser_device_retirement_bundle import _canonical, _validate
from .browser_device_startup import _launch_lock
from .browser_device_store import BrowserDeviceStore


class BrowserContinuationHistoryError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Retained continuation history is incomplete, changed or unsafe. "
                         "Keep all evidence. No current permission or sign-in was established.")


@dataclass(frozen=True, slots=True)
class BrowserContinuationHistory:
    """Past committed chain only; cannot substitute for a live confirmation result."""

    identity: str = field(repr=False)
    origin: str = field(repr=False)
    device_id: str = field(repr=False)
    release_id: str = field(repr=False)
    intent_id: str = field(repr=False)
    fingerprint: str = field(repr=False)
    native_after: bytes = field(repr=False)
    native_revision: int
    mode: RecoveryMode = RecoveryMode.PAUSED


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _document(raw: bytes, *, newline: bool = False) -> dict[str, Any]:
    value = json.loads(raw, object_pairs_hook=_object)
    if type(value) is not dict or raw != (_encoded(value) if newline else _json(value)):
        raise ValueError()
    return value


class _Reader:
    def __init__(self, handoff: BrowserRecoveryHandoff) -> None:
        self.h = handoff
        self.files: dict[Path, tuple[bytes, int]] = {}
        self.inodes: dict[Path, list[int]] = {}

    def inode(self, path: Path) -> list[int]:
        value = release_journal._inode(path)
        if path in self.inodes and self.inodes[path] != value:
            raise ValueError()
        self.inodes[path] = value
        return value

    def read(self, path: Path, limit: int) -> bytes:
        from .browser_device_registration import _matches

        raw = _private_read(path.parent, path.name, limit)
        self.inode(path)
        _matches(path, raw)
        prior = self.files.get(path)
        if prior is not None and prior != (raw, 0o600):
            raise ValueError()
        self.files[path] = raw, 0o600
        return raw

    def remember(self, path: Path, raw: bytes, *, mode: int = 0o600) -> None:
        from .browser_device_registration import _matches

        self.inode(path)
        _matches(path, raw, mode)
        prior = self.files.get(path)
        if prior is not None and prior != (raw, mode):
            raise ValueError()
        self.files[path] = raw, mode

    def recheck(self) -> None:
        from .browser_device_registration import _matches

        for path, (raw, mode) in self.files.items():
            _matches(path, raw, mode)
        for path, inode in self.inodes.items():
            if release_journal._inode(path) != inode:
                raise ValueError()

    def native(self, boundary: BrowserResumeBoundary) -> tuple[BrowserResumeArchiveBinding, bytes]:
        h, s = self.h, self.h._session
        root = s._archives / h._operation
        review = _document(self.read(root / "review.json", 16384), newline=True)
        if (set(review) != {"version", "kind", "intent", "native", "binding"}
                or type(review["version"]) is not int or review["version"] != 1
                or review["kind"] not in {"retire", "reconcile"}
                or review["intent"] != h._intent or review["binding"] != boundary._binding()
                or _fingerprint(review) != h._operation):
            raise ValueError()
        plan = inspect_resume_archive(self.read(root / "native-history.json", 256 * 1024),
            kind=review["kind"], identity=boundary._configuration.identity, profile=s._profile,
            expected_review=_encoded(review["native"]))
        after = json.loads(plan.after)
        if (plan.mode is not RecoveryMode.PAUSED
                or (plan.kind == "retire" and after["approvals"][-1]["intent"] != h._intent)
                or (plan.kind == "reconcile" and after["intent"] != h._intent)):
            raise ValueError()
        proof = BrowserResumeArchiveBinding(boundary._configuration.identity, h._intent,
            review["native"]["fingerprint"], plan.mode, plan.proposed_revision)
        raw = self.read(s._root / MAINTENANCE_MARKER, 32768)
        guard = _document(raw, newline=True)
        native = review["native"]
        expected = {"version": 1, "operation": "resume-maintenance", "targets": s._targets,
            "browser_binding": s._browser_binding(stopped=True), "identity": proof.identity,
            "intent": h._intent, "operation_id": h._operation,
            "approved_at": guard["approved_at"], "kind": plan.kind, "native_mode": plan.mode,
            "native_revision": plan.prior_revision, "approvals": native["approvals"],
            "pending": native.get("pending", 0), "created_at": native["created_at"],
            "expires_at": native["expires_at"]}
        if (not _timestamp(guard["approved_at"])
                or not native["created_at"] <= guard["approved_at"] < native["expires_at"]
                or raw != _encoded(expected)):
            raise ValueError()
        return proof, plan.after

    def handoff(self, proof: BrowserResumeArchiveBinding) -> dict[str, object]:
        h, s = self.h, self.h._session
        registration, original, _ = _canonical_bundle_files(**s._registration)
        receipt = _receipt(registration, s._registration["bundle"], s._profile)
        self.remember(s._root / ".sdsctl-browser-registration.json", receipt)
        key, artifacts, recovery_receipt = _canonical(h._recovery, s, operation_id=h._operation,
            browser_intent=h._intent, handoff=h._root, proof=proof, supervised=True)
        _validate(h._recovery, artifacts, recovery_receipt)
        self.remember(h._recovery / "bundle.json", recovery_receipt)
        for name, body in artifacts.items():
            self.remember(h._recovery / name, body, mode=0o700 if name == "native-host" else 0o600)
        raw = self.read(h._root / "operation.json", 32768)
        value = _document(raw)
        process = value["owner_process"]
        if (not _hex(value["nonce"]) or type(process) is not list or len(process) != 2
                or any(type(n) is not int or n <= 0 for n in process)):
            raise ValueError()
        fd, owner = _lock_file(h._root / "owner.lock")
        os.close(fd)
        self.inode(h._root / "owner.lock")
        guard = self.read(s._root / MAINTENANCE_MARKER, 32768)
        expected = {"version": 1, "operation": "paused-browser-handoff", "targets": h._targets,
            "supervised": True, "operation_id": h._operation, "browser_intent": h._intent,
            "nonce": value["nonce"], "identity": proof.identity, "extension_id": key.extension_id,
            "evidence": asdict(proof), "handoff_binding": self.inode(h._root),
            "owner_binding": owner, "owner_process": process,
            "browser_binding": s._browser_binding(stopped=True), "guard_sha256": _digest(guard),
            "original_host_sha256": _digest(original), "original_receipt_sha256": _digest(receipt),
            "recovery_receipt_sha256": _digest(recovery_receipt),
            "recovery_host_sha256": _digest(artifacts[NATIVE_HOST + ".json"])}
        if raw != _json(expected) or registration.identity != proof.identity:
            raise ValueError()
        for name, body in (("registration.before.json", receipt),
                           ("native-host.before.json", original),
                           ("guard.before.json", guard), ("switch-started.json", raw),
                           ("ready.json", raw)):
            self.remember(h._root / name, body)
        hosts = s._root / "NativeMessagingHosts"
        self.remember(hosts / (NATIVE_HOST + ".json"), original)
        if {p.name for p in hosts.iterdir()} != {NATIVE_HOST + ".json"}:
            raise ValueError()
        self.read(h._root / "supervisor.json", 8192)
        self.remember(h._root / "browser-launch-ready.json", h._launch_body(raw, live=False))
        h._supervisor_stopped()
        ack = _json({"version": 1, "operation": "browser-paused-acknowledgement",
            "handoff_sha256": _digest(raw), "evidence": asdict(proof),
            "local_pause_saved": True, "session_ready": False})
        self.remember(h._root / "browser-acknowledgement.json", ack)
        restored = _json({"version": 1, "operation": "restore-paused-browser-host",
                          "handoff_sha256": _digest(raw), "ack_sha256": _digest(ack)})
        for name in ("restoration-started.json", "restored.json"):
            self.remember(h._root / name, restored)
        paths = {"guard": s._root / MAINTENANCE_MARKER, "operation": h._root / "operation.json",
            "ack": h._root / "browser-acknowledgement.json",
            "restoration_started": h._root / "restoration-started.json",
            "restored": h._root / "restored.json", "supervisor": h._root / "supervisor.json",
            "native_ledger": s._profile / "recovery.sqlite"}
        # Preserve the private, fixed ledger inode and stopped-file boundary,
        # without parsing its current schema/state or treating history as health.
        ledger = paths["native_ledger"]
        BrowserDeviceStore(ledger)._check()
        if any(release_journal._present(Path(str(ledger) + suffix))
               for suffix in ("-journal", "-wal", "-shm")):
            raise ValueError()
        return {"targets": h._targets, "operation_id": h._operation, "browser_intent": h._intent,
            "identity": proof.identity, "revision": proof.revision, "mode": str(proof.mode),
            "handoff_sha256": _digest(raw), "ack_sha256": _digest(ack),
            "bindings": {name: self.inode(path) for name, path in paths.items()}}

    def journal(self, *, intent: bool, release_id: str, intent_id: str,
                evidence: dict[str, object]) -> bytes:
        s = self.h._session
        name = intent_journal.INTENT_JOURNAL if intent else release_journal.RELEASE_JOURNAL
        path = s._root / name
        read = intent_journal._read if intent else release_journal._read
        # _read checks exact SQLite schema, complete phase, private files, rollback
        # header and absence of sidecars. A canonical prepared body is not enough.
        raw = read(path)
        self.read(path, 65536)
        value = _document(raw)
        fields = {"version", "operation", "release_id", "reviewed_at", "approved_at",
                  "expires_at", "consent_sha256", "journal_binding", "evidence"}
        if intent:
            fields.update({"intent_id", "purpose"})
        operation = ("record-paused-continuation-intent" if intent else
                     "release-paused-browser-guard")
        if (set(value) != fields or type(value["version"]) is not int or value["version"] != 1
                or value["operation"] != operation or value["release_id"] != release_id
                or (intent and (value["intent_id"] != intent_id
                                or value["purpose"] != "enable-fresh-resume-review"))
                or not _hex(value["consent_sha256"])
                or any(not _timestamp(value[k]) for k in
                       ("reviewed_at", "approved_at", "expires_at"))
                or not value["reviewed_at"] <= value["approved_at"] < value["expires_at"]
                or value["expires_at"] != value["reviewed_at"] + 120
                or _json(value["journal_binding"]) != _json(self.inode(path))
                or _json(value["evidence"]) != _json(evidence) or read(path) != raw):
            raise ValueError()
        return raw


def inspect_continuation_history(handoff: BrowserRecoveryHandoff, *, release_id: str,
                                 intent_id: str) -> BrowserContinuationHistory:
    """Read the complete retained chain under stopped ownership, never live authority.

    A later pause may leave history valid while every existing current confirmation
    refuses. There is no revision-order shortcut, writer, activation or CLI here.
    """
    try:
        if (type(handoff) is not BrowserRecoveryHandoff or not handoff._supervised
                or not _hex(release_id) or not _hex(intent_id)):
            raise ValueError()
        s = handoff._session
        with (_launch_lock(s._root, create=False),
              browser_profile_access(s._profile, exclusive=False),
              browser_profile_access(s._archives, exclusive=False)):
            boundary = BrowserResumeBoundary(s._profile, archives=s._archives)
            binding = boundary._binding()
            reader = _Reader(handoff)
            proof, after = reader.native(boundary)
            chain = reader.handoff(proof)
            release = reader.journal(intent=False, release_id=release_id, intent_id=intent_id,
                                     evidence=chain)
            config = load_browser_native_configuration(s._profile)
            release_fingerprint = _digest((_digest(release) + "\0" + str(config.root)).encode())
            evidence = {"identity": config.identity, "origin": config.origin,
                "device_id": config.device_id, "revision": proof.revision,
                "release_fingerprint": release_fingerprint, "targets": dict(handoff._targets),
                "operation_id": handoff._operation}
            intent = reader.journal(intent=True, release_id=release_id, intent_id=intent_id,
                                    evidence=evidence)
            # Reconstruct canonical executable assets once more and compare every
            # retained byte/inode. No final live-state equality is implied here.
            if reader.handoff(proof) != chain or boundary._binding() != binding:
                raise ValueError()
            for is_intent, expected, snapshot in (
                    (False, release, chain), (True, intent, evidence)):
                if reader.journal(intent=is_intent, release_id=release_id, intent_id=intent_id,
                                  evidence=snapshot) != expected:
                    raise ValueError()
            reader.recheck()
            digest = _fingerprint({"release": _digest(release), "intent": _digest(intent),
                "native_after": _digest(after), "private_inputs": binding,
                "files": {str(p): {"sha256": _digest(raw), "inode": reader.inodes[p]}
                          for p, (raw, _) in reader.files.items()}})
            return BrowserContinuationHistory(config.identity, config.origin, config.device_id,
                release_id, intent_id, digest, after, proof.revision)
    except Exception:
        raise BrowserContinuationHistoryError() from None
