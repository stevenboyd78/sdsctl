"""Trusted local selection/ownership for stopped resume maintenance candidates.

No CLI, native action or installed browser adapter. Callers fix both directories
out of band. Browser intent and opaque operation IDs can never select file paths.
This does not install/rotate credentials, stop a browser or grant resume consent.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from .browser_device_native import _private_read, load_browser_native_configuration
from .browser_device_profile import _platform, _write
from .browser_device_profile_access import browser_profile_access
from .browser_device_recovery import RecoveryMode, _object
from .browser_device_resume import _hex
from .browser_device_resume_maintenance import (
    BrowserResumeMaintenance,
    BrowserResumeMaintenanceReview,
    BrowserResumeRetirementEvidence,
    _encoded,
    _fingerprint,
)
from .browser_device_resume_reconciliation import (
    BrowserResumeReconciliation,
    BrowserResumeReconciliationReview,
)

_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_REVIEW_BYTES = 16384
_Kind = Literal["retire", "reconcile"]
_NativeReview = BrowserResumeMaintenanceReview | BrowserResumeReconciliationReview


class BrowserResumeBoundaryError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Resume maintenance is busy, stale, unsafe or unconfirmed. Retain all evidence; "
            "do not retry a mutation, replace private inputs or clear browser pause."
        )


@dataclass(frozen=True, slots=True)
class BrowserResumeBoundaryReview:
    """One instance's private, one-use selection; not page consent or a file path."""

    operation_id: str = field(repr=False)
    kind: _Kind
    intent: str = field(repr=False)
    native: _NativeReview = field(repr=False)
    binding: str = field(repr=False)


class BrowserResumeBoundary:
    """Fix installation/archive ownership, bind inputs, and retain lost-ACK evidence.

    Selection is local and trusted. Review is read-only; execute consumes the
    exact in-memory review once. After a process loss only confirm is supported
    for its opaque operation ID, with immutable review.json/native-history.json
    filenames. Native cores remain internal and require these ownership rules
    if called directly. Advisory locking cannot control manual/root edits.
    """

    def __init__(
        self, root: Path, *, archives: Path, clock: Callable[[], float] = time.time,
    ) -> None:
        try:
            _platform()
            if (root == archives or root.is_relative_to(archives)
                    or archives.is_relative_to(root)):
                raise ValueError()
            self._root, self._archives, self._clock = root, archives, clock
            with (browser_profile_access(root, exclusive=False) as profile_inode,
                  browser_profile_access(archives, exclusive=False) as archive_inode):
                self._configuration = load_browser_native_configuration(root)
                self._inodes = (profile_inode, archive_inode)
            self._reviewed: BrowserResumeBoundaryReview | None = None
            self._review_attempted = False
        except Exception:
            raise BrowserResumeBoundaryError() from None

    @contextmanager
    def _access(self, *, exclusive: bool) -> Iterator[None]:
        # Consistent order: profile, archives, then the core's SQLite transaction.
        with (browser_profile_access(self._root, exclusive=exclusive) as profile_inode,
              browser_profile_access(self._archives, exclusive=exclusive) as archive_inode):
            if ((profile_inode, archive_inode) != self._inodes
                    or load_browser_native_configuration(self._root) != self._configuration):
                raise ValueError()
            yield

    def _binding(self) -> str:
        inputs: dict[str, object] = {}
        for name, limit in (("client.json", 4096), ("device.secret", 128), ("ca.pem", 128 * 1024)):
            path = self._root / name
            try:
                before = path.lstat()
            except FileNotFoundError:
                if name == "client.json":
                    raise
                inputs[name] = None  # Missing credentials/trust never get repaired here.
                continue
            data = _private_read(self._root, name, limit)
            after = path.lstat()
            attributes = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size",
                          "st_mtime_ns", "st_ctime_ns")
            original = [getattr(before, key) for key in attributes]
            if original != [getattr(after, key) for key in attributes]:
                raise ValueError()
            inputs[name] = {"file": original, "sha256": hashlib.sha256(data).hexdigest()}
        return _fingerprint({"profile": str(self._root), "archives": str(self._archives),
                             "inodes": self._inodes, "identity": self._configuration.identity,
                             "inputs": inputs})

    @staticmethod
    def _document(
        kind: _Kind, intent: str, native: _NativeReview, binding: str,
    ) -> dict[str, object]:
        return {"version": 1, "kind": kind, "intent": intent,
                "native": asdict(native), "binding": binding}

    def review(self, *, kind: _Kind, browser_intent: str) -> BrowserResumeBoundaryReview:
        """Read-only exact selection, including unchanged private input fingerprints."""
        try:
            if self._review_attempted:
                raise ValueError()
            self._review_attempted = True
            if kind not in {"retire", "reconcile"} or not _hex(browser_intent):
                raise ValueError()
            with self._access(exclusive=False):
                binding = self._binding()
                if kind == "retire":
                    history = BrowserResumeMaintenance(self._root, clock=self._clock)
                    native: _NativeReview = history.review()
                    # Never retire an unrelated latest operation to resolve this browser.
                    with history._recovery._inspection() as db:
                        snapshot = history._snapshot(db, self._clock())
                    rows = snapshot["approvals"]
                    assert isinstance(rows, list)
                    if (rows[-1]["intent"] != browser_intent
                            or history._review(snapshot, native.created_at) != native):
                        raise ValueError()
                else:
                    native = BrowserResumeReconciliation(self._root, clock=self._clock).review(
                        browser_intent=browser_intent)
                if self._binding() != binding:
                    raise ValueError()
                document = self._document(kind, browser_intent, native, binding)
                selected = BrowserResumeBoundaryReview(
                    _fingerprint(document), kind, browser_intent, native, binding)
                self._reviewed = selected
                return selected
        except Exception:
            raise BrowserResumeBoundaryError() from None

    def _stage(self, review: BrowserResumeBoundaryReview) -> Path:
        path = self._archives / review.operation_id
        parent = os.open(self._archives, _FLAGS)
        directory = None
        try:
            os.mkdir(review.operation_id, mode=0o700, dir_fd=parent)  # Never reuse partial output.
            os.fsync(parent)
            directory = os.open(review.operation_id, _FLAGS, dir_fd=parent)
            opened, named = os.fstat(directory), path.lstat()
            if ((opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
                    or not stat.S_ISDIR(named.st_mode) or stat.S_IMODE(named.st_mode) != 0o700):
                raise ValueError()
            _write(directory, "review.json", _encoded(self._document(
                review.kind, review.intent, review.native, review.binding)))
            os.fsync(directory)
            return path
        finally:
            if directory is not None:
                os.close(directory)
            os.close(parent)

    def execute(self, review: BrowserResumeBoundaryReview) -> BrowserResumeRetirementEvidence:
        """Explicit one-use mutation; any exception requires confirmation, not replay."""
        try:
            if review is not self._reviewed or self._reviewed is None:
                raise ValueError()
            self._reviewed = None  # Consume before lock, files, or other fallible work.
            with self._access(exclusive=True):
                if self._binding() != review.binding:
                    raise ValueError()
                path = self._stage(review)
                if review.kind == "retire":
                    assert isinstance(review.native, BrowserResumeMaintenanceReview)
                    BrowserResumeMaintenance(self._root, clock=self._clock).retire(
                        review.native, archive=path / "native-history.json")
                else:
                    assert isinstance(review.native, BrowserResumeReconciliationReview)
                    BrowserResumeReconciliation(self._root, clock=self._clock).reconcile(
                        review.native, archive=path / "native-history.json")
                return self._confirm(review.operation_id, review.intent)
        except Exception:
            raise BrowserResumeBoundaryError() from None

    def _confirm(self, operation_id: str, intent: str) -> BrowserResumeRetirementEvidence:
        if not _hex(operation_id) or not _hex(intent):
            raise ValueError()
        path = self._archives / operation_id
        document = json.loads(_private_read(path, "review.json", _REVIEW_BYTES),
                              object_pairs_hook=_object)
        if (not isinstance(document, dict)
                or set(document) != {"version", "kind", "intent", "native", "binding"}
                or type(document["version"]) is not int or document["version"] != 1
                or document["kind"] not in {"retire", "reconcile"}
                or document["intent"] != intent or document["binding"] != self._binding()
                or _fingerprint(document) != operation_id):
            raise ValueError()
        values = document["native"]
        values["mode"] = RecoveryMode(values["mode"])
        if document["kind"] == "retire":
            return BrowserResumeMaintenance(self._root, clock=self._clock).confirm_browser_intent(
                BrowserResumeMaintenanceReview(**values), archive=path / "native-history.json",
                browser_intent=intent)
        return BrowserResumeReconciliation(self._root, clock=self._clock).confirm(
            BrowserResumeReconciliationReview(**values), archive=path / "native-history.json",
            browser_intent=intent)

    def confirm(self, *, operation_id: str, browser_intent: str) -> BrowserResumeRetirementEvidence:
        """Read-only after restart/lost reply; caller cannot supply review or archive paths."""
        try:
            with self._access(exclusive=False):
                return self._confirm(operation_id, browser_intent)
        except Exception:
            raise BrowserResumeBoundaryError() from None
