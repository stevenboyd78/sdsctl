"""Pure archive-plan inspection, NOT committed history or current permission.

Archives are synced before native SQLite commits. Valid canonical bytes describe
only the selected transition; a later reader must independently establish its
commit and current authority. This module performs no file, ledger or network I/O.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from .browser_device_recovery import RecoveryMode, _object, _State
from .browser_device_resume import _COLUMNS, _hex, _integer, _timestamp
from .browser_device_store import (
    BrowserDeviceRecord,
    BrowserDeviceState,
    validate_browser_device_record,
)

_OPERATIONS = {
    "retire": "retire-native-resume-history",
    "reconcile": "reconcile-missing-native-resume-intent",
}
_STOPPED = {m.value for m in RecoveryMode if m is not RecoveryMode.ACTIVE}


class BrowserResumeArchiveError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Native resume archive plan is invalid or does not match the selection. "
                         "Retain all evidence. No commit or permission was established.")


@dataclass(frozen=True, slots=True)
class BrowserResumeArchivePlan:
    """Immutable structural result; deliberately not BrowserResumeRetirementEvidence."""

    kind: Literal["retire", "reconcile"]
    prior_revision: int
    proposed_revision: int
    mode: RecoveryMode
    archived: int
    cancelled: int
    archive_sha256: str = field(repr=False)
    after: bytes = field(repr=False)


def _encoded(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n").encode("ascii")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _state(value: object) -> _State:
    if (type(value) is not dict or set(value) != {
            "revision", "mode", "failures", "next_at", "observed_at"}
            or not _integer(value["revision"])
            or type(value["mode"]) is not str
            or value["mode"] not in _STOPPED
            or type(value["failures"]) is not int or not 0 <= value["failures"] <= 32
            or not _timestamp(value["next_at"]) or not _timestamp(value["observed_at"])
            or value["next_at"] > value["observed_at"] + 300):
        raise ValueError()
    return _State(value["revision"], RecoveryMode(value["mode"]), value["failures"],
                  value["next_at"], value["observed_at"])


def _approvals(value: object, *, identity: str, schema: int,
               state: _State) -> list[dict[str, Any]]:
    if (type(value) is not list or len(value) > 128
            or (schema == 1 and value) or (schema == 2 and not value)):
        raise ValueError()
    seen: set[str] = set()
    ordering: list[tuple[int, str]] = []
    pending = 0
    for row in value:
        if (type(row) is not dict or set(row) != set(_COLUMNS)
                or not all(_hex(row[key]) for key in (
                    "digest", "identity", "intent", "credential_hash", "trust_hash"))
                or row["identity"] != identity or row["digest"] in seen
                or type(row["phase"]) is not str
                or row["phase"] not in {"prepared", "claimed", "complete", "cancelled", "failed"}
                or not _integer(row["revision"]) or not _integer(row["generation"])
                or type(row["mode"]) is not str
                or row["mode"] not in _STOPPED
                or not _timestamp(row["created"]) or not _timestamp(row["expires"])
                or row["expires"] - row["created"] != 120):
            raise ValueError()
        validate_browser_device_record(BrowserDeviceRecord(
            row["device"], row["generation"], BrowserDeviceState.ACTIVE))
        if row["phase"] in {"prepared", "claimed"}:
            pending += 1
            if (row["revision"], row["mode"]) != (state.revision, state.mode.value):
                raise ValueError()
        seen.add(row["digest"])
        ordering.append((row["revision"], row["digest"]))
    if pending > 1 or ordering != sorted(ordering):
        raise ValueError()
    return value


def inspect_resume_archive(
    raw: bytes, *, kind: Literal["retire", "reconcile"], identity: str,
    profile: Path, expected_review: bytes,
) -> BrowserResumeArchivePlan:
    """Reconstruct one selected archive without consulting or changing live state.

Even a perfectly valid result may precede a rolled-back native transaction. It
must never substitute for strict confirmation, a committed recovery chain or a
current permission selector. The selected profile is compared lexically only.
"""
    try:
        if (kind not in _OPERATIONS or not _hex(identity)
                or not isinstance(profile, Path) or not profile.is_absolute()
                or ".." in profile.parts or type(raw) is not bytes
                or not 0 < len(raw) <= 256 * 1024 or type(expected_review) is not bytes
                or not 0 < len(expected_review) <= 16384):
            raise ValueError()
        document = json.loads(raw, object_pairs_hook=_object)
        if (type(document) is not dict
                or set(document) != {"version", "operation", "review", "before", "after"}
                or type(document["version"]) is not int or document["version"] != 1
                or document["operation"] != _OPERATIONS[kind] or raw != _encoded(document)):
            raise ValueError()
        review = document["review"]
        review_fields = {"fingerprint", "revision", "mode", "approvals", "created_at", "expires_at"}
        review_fields.add("pending" if kind == "retire" else "intent")
        if (type(review) is not dict or set(review) != review_fields
                or _encoded(review) != expected_review or not _hex(review["fingerprint"])
                or not _integer(review["revision"]) or not _integer(review["revision"] + 1)
                or type(review["approvals"]) is not int or not 0 <= review["approvals"] <= 128
                or not _timestamp(review["created_at"]) or not _timestamp(review["expires_at"])
                or review["expires_at"] - review["created_at"] != 120):
            raise ValueError()
        before, after = document["before"], document["after"]
        fields = {"identity", "profile", "state", "approvals"}
        if kind == "reconcile":
            fields.update({"schema", "intent"})
        if (type(before) is not dict or set(before) != fields
                or before["identity"] != identity or before["profile"] != str(profile)
                or _fingerprint(before) != review["fingerprint"]
                or type(after) is not dict or set(after) != fields):
            raise ValueError()
        schema = 2 if kind == "retire" else before["schema"]
        if type(schema) is not int or schema not in (1, 2):
            raise ValueError()
        state = _state(before["state"])
        proposed = _state(after["state"])
        rows = _approvals(before["approvals"], identity=identity, schema=schema, state=state)
        if ((state.revision, state.mode.value, len(rows)) != (
                review["revision"], review["mode"], review["approvals"])
                or not state.observed_at <= review["created_at"] <= proposed.observed_at
                or not proposed.observed_at < review["expires_at"]):
            raise ValueError()
        expected = {**before, "state": asdict(replace(
            state, revision=state.revision + 1, observed_at=proposed.observed_at))}
        archived = cancelled = 0
        if kind == "retire":
            pending = sum(row["phase"] in {"prepared", "claimed"} for row in rows)
            if type(review["pending"]) is not int or review["pending"] != pending:
                raise ValueError()
            anchor = dict(rows[-1])
            if anchor["phase"] in {"prepared", "claimed"}:
                anchor["phase"] = "cancelled"
            expected["approvals"] = [anchor]
            archived, cancelled = len(rows) - 1, pending
        elif (not _hex(review["intent"]) or before["intent"] != review["intent"]
                or any(row["intent"] == review["intent"]
                       or row["phase"] in {"prepared", "claimed"} for row in rows)):
            raise ValueError()
        if _encoded(after) != _encoded(expected):
            raise ValueError()
        _approvals(after["approvals"], identity=identity, schema=schema, state=proposed)
        return BrowserResumeArchivePlan(kind, state.revision, proposed.revision, proposed.mode,
            archived, cancelled, hashlib.sha256(raw).hexdigest(), _encoded(expected))
    except Exception:
        raise BrowserResumeArchiveError() from None
