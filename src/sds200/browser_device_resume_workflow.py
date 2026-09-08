"""Internal stopped-browser review/apply and read-only lost-reply confirmation.

No CLI or installed UI. A trusted local adapter supplies explicit consent within
this process; never call from a page or reconstruct an old execution review.
Keep the launch guard on every post-consent outcome, including success. Releasing
it for a separately reviewed browser-acknowledgement handoff is future work.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .browser_device_native import _private_read, load_browser_native_configuration
from .browser_device_profile import _platform, _write
from .browser_device_recovery import _object
from .browser_device_registration import (
    MAINTENANCE_MARKER,
    _inspect_registration_files,
    _matches,
    inspect_browser_registration,
)
from .browser_device_resume import _hex, _integer, _timestamp
from .browser_device_resume_boundary import BrowserResumeBoundary, BrowserResumeBoundaryReview
from .browser_device_resume_maintenance import BrowserResumeRetirementEvidence, _encoded
from .browser_device_startup import _launch_lock

_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_MARKER_BYTES = 32768
_LOCK = ".sdsctl-device-launch.lock"
_SECONDS = 120


class BrowserResumeWorkflowError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Stopped-browser maintenance was refused or could not be confirmed. Retain all "
            "files and guards. Do not repeat a mutation, remove locks, reset browser state or "
            "resume sign-in; use exact read-only confirmation for an uncertain result."
        )


@dataclass(frozen=True, slots=True)
class BrowserResumeLocalReview:
    """Display only to the trusted local operator, never a browser/native request."""

    directory: Path = field(repr=False)
    profile: Path = field(repr=False)
    archives: Path = field(repr=False)
    origin: str = field(repr=False)
    device_id: str = field(repr=False)
    operation_id: str = field(repr=False)
    confirmation: str = field(repr=False)
    kind: Literal["retire", "reconcile"]
    native_mode: str
    native_revision: int
    approvals: int
    pending: int
    expires_at: float


class BrowserResumeWorkflow:
    """Fixed installation, same-process consent, retained stopped launch guard.

    Only current canonical registrations with intact private profile inputs are
    eligible. A launcher lock is coordination, not proof against direct Chromium
    launches or arbitrary same-account/root edits. Do not mix old runtimes or
    bypass their launchers. This does not stop a running browser or a service.
    """

    def __init__(self, directory: Path, *, profile: Path, bundle: Path, public_key: Path,
                 archives: Path, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        try:
            _platform()
            roots = (directory, profile, bundle, archives)
            if (any(not path.is_absolute() or path.resolve() != path for path in roots)
                    or any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
                           for i, a in enumerate(roots) for b in roots[i + 1:])):
                raise ValueError()
            self._root, self._profile, self._archives = directory, profile, archives
            self._registration = {"bundle": bundle, "profile": profile, "public_key": public_key}
            self._targets = {"directory": str(directory), "profile": str(profile),
                             "bundle": str(bundle), "public_key": str(public_key),
                             "archives": str(archives)}
            self._clock, self._monotonic = clock, monotonic
            self._attempted = False
        except Exception:
            raise BrowserResumeWorkflowError() from None

    def _stopped_binding(self) -> list[list[int]]:
        if self._root.resolve() != self._root or any(
            (self._root / name).exists() or (self._root / name).is_symlink()
            for name in ("SingletonLock", "SingletonSocket", "SingletonCookie")
        ):
            raise ValueError()
        directory, lock = self._root.lstat(), (self._root / _LOCK).lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid()
                or stat.S_IMODE(directory.st_mode) != 0o700 or not stat.S_ISREG(lock.st_mode)
                or lock.st_uid != os.geteuid() or stat.S_IMODE(lock.st_mode) != 0o600
                or lock.st_nlink != 1 or lock.st_size != 0):
            raise ValueError()
        return [[info.st_dev, info.st_ino] for info in (directory, lock)]

    def _guard(self, body: bytes) -> None:
        if len(body) > _MARKER_BYTES:
            raise ValueError()
        directory = os.open(self._root, _FLAGS)
        try:
            _write(directory, MAINTENANCE_MARKER, body)  # Exclusive; retain any partial write.
            os.fsync(directory)  # Durable launch refusal BEFORE native mutation.
        finally:
            os.close(directory)
        _matches(self._root / MAINTENANCE_MARKER, body)

    @staticmethod
    def _native_fields(review: BrowserResumeBoundaryReview) -> dict[str, object]:
        return {"kind": review.kind, "native_mode": review.native.mode,
                "native_revision": review.native.revision, "approvals": review.native.approvals,
                "pending": getattr(review.native, "pending", 0),
                "created_at": review.native.created_at, "expires_at": review.native.expires_at}

    def apply(
        self, *, kind: Literal["retire", "reconcile"], browser_intent: str,
        confirmation: Callable[[BrowserResumeLocalReview], str | None],
    ) -> BrowserResumeRetirementEvidence | None:
        """One session; callback None cancels, exact displayed phrase consents.

        Callback is trusted LOCAL UI, not consent inferred from a page message.
        While it runs the native review is read-only and launcher ownership is
        held. No guard/native mutation occurs on cancellation or expired consent.
        A missing launch-lock file may be created for coordination and retained.
        """
        try:
            if self._attempted:
                raise ValueError()
            self._attempted = True
            inspect_browser_registration(self._root, **self._registration)
            with _launch_lock(self._root):
                registered = inspect_browser_registration(self._root, **self._registration)
                binding = self._stopped_binding()
                boundary = BrowserResumeBoundary(self._profile, archives=self._archives,
                                                 clock=self._clock)
                started = self._monotonic()
                if not _timestamp(started):
                    raise ValueError()
                selected = boundary.review(kind=kind, browser_intent=browser_intent)
                config = load_browser_native_configuration(self._profile)
                # Even two identical read-only reviews need fresh local consent.
                # The volatile challenge is not persisted or accepted after restart.
                phrase = "MAINTAIN " + selected.operation_id + ":" + secrets.token_hex(16)
                prompt = BrowserResumeLocalReview(
                    self._root, self._profile, self._archives, config.origin, config.device_id,
                    selected.operation_id, phrase, selected.kind, selected.native.mode,
                    selected.native.revision, selected.native.approvals,
                    getattr(selected.native, "pending", 0), selected.native.expires_at)
                answer = confirmation(prompt)
                if answer is None:
                    return None

                def consent_time() -> float:
                    now, elapsed = self._clock(), self._monotonic()
                    if (not _timestamp(now) or not _timestamp(elapsed)
                            or not 0 <= elapsed - started < _SECONDS
                            or not selected.native.created_at <= now < selected.native.expires_at):
                        raise ValueError()
                    return now

                now = consent_time()
                if (type(answer) is not str or answer != phrase
                        or inspect_browser_registration(
                            self._root, **self._registration) != registered
                        or self._stopped_binding() != binding):
                    raise ValueError()
                body = _encoded({"version": 1, "operation": "resume-maintenance",
                    "targets": self._targets, "browser_binding": binding,
                    "identity": registered.identity, "intent": browser_intent,
                    "operation_id": selected.operation_id, "approved_at": now,
                    **self._native_fields(selected)})
                self._guard(body)
                if self._stopped_binding() != binding:
                    raise ValueError()
                _matches(self._root / MAINTENANCE_MARKER, body)
                consent_time()  # A stalled guard write cannot extend the consent window.
                boundary.execute(selected)  # Exact private in-memory selection, consumed once.
                # Recheck retained guard, installation and committed native evidence
                # before success. Never remove the marker, including after success.
                return self._confirm(selected.operation_id, browser_intent, expected=body)
        except Exception:
            raise BrowserResumeWorkflowError() from None

    def _confirm(self, operation_id: str, intent: str, *, expected: bytes | None = None,
                 ) -> BrowserResumeRetirementEvidence:
        if not _hex(operation_id) or not _hex(intent):
            raise ValueError()
        raw = _private_read(self._root, MAINTENANCE_MARKER, _MARKER_BYTES)
        data = json.loads(raw, object_pairs_hook=_object)
        if (not isinstance(data, dict) or set(data) != {
                "version", "operation", "targets", "browser_binding", "identity", "intent",
                "operation_id", "approved_at", "kind", "native_mode", "native_revision",
                "approvals", "pending", "created_at", "expires_at"}
                or type(data["version"]) is not int or data["version"] != 1
                or data["operation"] != "resume-maintenance" or raw != _encoded(data)
                or (expected is not None and raw != expected)
                or data["targets"] != self._targets or data["operation_id"] != operation_id
                or data["intent"] != intent
                or _encoded(data["browser_binding"]) != _encoded(self._stopped_binding())
                or not all(_timestamp(data[key]) for key in
                           ("created_at", "expires_at", "approved_at"))
                or not data["created_at"] <= data["approved_at"] < data["expires_at"]
                or not _integer(data["native_revision"])
                or type(data["approvals"]) is not int or not 0 <= data["approvals"] <= 128
                or type(data["pending"]) is not int or data["pending"] not in (0, 1)):
            raise ValueError()
        registered = _inspect_registration_files(self._root, **self._registration)
        if registered.identity != data["identity"]:
            raise ValueError()
        boundary = BrowserResumeBoundary(self._profile, archives=self._archives, clock=self._clock)
        proof = boundary.confirm(operation_id=operation_id, browser_intent=intent)
        # The native operation has been independently confirmed. Bind displayed
        # review fields to its exact recorded review, not merely the guard file.
        record = json.loads(_private_read(self._archives / operation_id, "review.json", 16384),
                            object_pairs_hook=_object)
        native = record["native"]
        if (data["kind"] != record["kind"] or data["native_mode"] != proof.mode
                or data["native_revision"] + 1 != proof.revision
                or data["approvals"] != native["approvals"]
                or data["pending"] != native.get("pending", 0)
                or data["created_at"] != native["created_at"]
                or data["expires_at"] != native["expires_at"]):
            raise ValueError()
        _matches(self._root / MAINTENANCE_MARKER, raw)
        if data["browser_binding"] != self._stopped_binding():
            raise ValueError()
        return proof

    def confirm(self, *, operation_id: str, browser_intent: str) -> BrowserResumeRetirementEvidence:
        """Read-only after process loss; never creates a lock/marker or replays execution."""
        try:
            # Canonical native/bundle checks occur behind this exact guard, not
            # through a general public startup override that ignores markers.
            with _launch_lock(self._root, create=False):
                return self._confirm(operation_id, browser_intent)
        except Exception:
            raise BrowserResumeWorkflowError() from None
