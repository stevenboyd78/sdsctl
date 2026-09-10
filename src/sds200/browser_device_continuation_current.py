"""Internal owned current-state reads; no runtime role, write, network or sign-in.

Fixed installed paths select complete journals and the immutable manifest. Full
history, actual owner/private files and current epoch SQL are verified together.
Results describe one read point, never a permission lease. Normal startup/native
dispatch still refuse continuation markers and schema 3. Do not use this reader
as a substitute for the unfinished owned mutation and online proof adapters.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import browser_device_continuation_activation as activation
from . import browser_device_continuation_epoch as epoch
from . import browser_device_continuation_history as history
from . import browser_device_continuation_intent as intent
from . import browser_device_continuation_ownership as ownership
from . import browser_device_guard_release as release
from .browser_device_handoff import BrowserRecoveryHandoff
from .browser_device_native import (
    BrowserNativeConfiguration,
    _private_read,
    load_browser_native_configuration,
)
from .browser_device_profile import _credential, _trust
from .browser_device_recovery import RecoveryMode
from .browser_device_resume import _hex
from .browser_device_worker import BrowserWorkerSelection, _browser_directory


class BrowserContinuationCurrentError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Current continuation state is unavailable, changed or unsafe. "
                         "Retain the profile and evidence for review; nothing was repaired "
                         "and no sign-in permission was established.")


@dataclass(frozen=True, slots=True)
class BrowserCurrentContinuation:
    """One verified current read point; not history, consent or cached authority."""

    identity: str = field(repr=False)
    origin: str = field(repr=False)
    device_id: str = field(repr=False)
    epoch: str = field(repr=False)
    manifest_sha256: str = field(repr=False)
    state_fingerprint: str = field(repr=False)
    native_revision: int
    mode: RecoveryMode


@dataclass(frozen=True, slots=True)
class BrowserContinuationObservation:
    """Owned read point only, NOT browser acceptance, consent or online authority."""

    state: BrowserCurrentContinuation = field(repr=False)
    generation: int | None = field(repr=False)


class _SelectedFiles:
    """Bootstrap only: pins fixed selection bytes before owned full reconstruction."""

    def __init__(self, root: Path, *, bundle: Path, profile: Path, public_key: Path) -> None:
        selected = dict(directory=root, bundle=bundle, profile=profile, public_key=public_key)
        if any(not isinstance(p, Path) or not p.is_absolute() or p.resolve() != p
               for p in selected.values()):
            raise ValueError()
        self.files: dict[Path, tuple[bytes, tuple[int, int]]] = {}
        release_path, intent_path = root / release.RELEASE_JOURNAL, root / intent.INTENT_JOURNAL
        self._capture(release_path, 65536)
        release_value = history._document(release._read(release_path))
        self._capture(intent_path, 65536)
        intent_value = history._document(intent._read(intent_path))
        targets = release_value["evidence"]["targets"]
        if (type(targets) is not dict or set(targets) != {
                "directory", "bundle", "profile", "public_key", "archives", "handoff",
                "recovery_bundle"} or any(type(v) is not str for v in targets.values())
                or any(targets[k] != str(p) for k, p in selected.items())
                or not _hex(release_value["release_id"]) or not _hex(intent_value["intent_id"])
                or intent_value["release_id"] != release_value["release_id"]):
            raise ValueError()
        self.handoff = BrowserRecoveryHandoff(Path(targets["handoff"]),
            **{k: Path(v) for k, v in targets.items() if k != "handoff"},
            operation_id=release_value["evidence"]["operation_id"],
            browser_intent=release_value["evidence"]["browser_intent"], supervised=True)
        self.release_id, self.intent_id = release_value["release_id"], intent_value["intent_id"]
        self.manifest_path = root / intent.ACTIVATION_MANIFEST
        self._capture(self.manifest_path, 256 * 1024)
        self.recheck()

    def _capture(self, path: Path, limit: int) -> None:
        activation._no_sidecars(path)
        binding = activation._binding(path)
        raw = _private_read(path.parent, path.name, limit)
        activation._manifest_check(path, raw, binding)
        self.files[path] = raw, binding

    def recheck(self) -> None:
        for path, (raw, binding) in self.files.items():
            activation._manifest_check(path, raw, binding)


class _CurrentRead:
    """Lexical read-only scope. Failure latches; it cannot survive scope/process exit."""

    def __init__(self, selected: _SelectedFiles, owner: ownership._HistoryOwnership,
                 db: sqlite3.Connection, ledger: activation._LedgerFile) -> None:
        self._selected, self._owner, self._db, self._ledger = selected, owner, db, ledger
        self._process = (os.getpid(), os.getppid())
        retained, self._inputs = history._capture_owned_history(selected.handoff, owner,
            release_id=selected.release_id, intent_id=selected.intent_id)
        raw, binding = selected.files[selected.manifest_path]
        self._selection = epoch._EpochSelection(raw, binding, retained,
            selected.handoff._session._profile, ledger.binding)
        self._active = True
        self._expected: epoch._EpochSnapshot | None = None

    def _files(self) -> None:
        self._owner.binding(self._selected.handoff)
        self._inputs.recheck()
        self._selected.recheck()
        self._ledger.check_identity()
        # Unlike a writer, this scope may NEVER adopt an active native journal.
        activation._no_sidecars(self._ledger.path)

    def inspect(self) -> BrowserCurrentContinuation:
        try:
            if not self._active or self._process != (os.getpid(), os.getppid()):
                raise ValueError()
            self._files()
            current = epoch._inspect_epoch(self._db, self._selection)
            self._files()
            if self._expected is not None and current != self._expected:
                raise ValueError()
            self._expected = current
            h = self._selection.history
            return BrowserCurrentContinuation(h.identity, h.origin, h.device_id, current.epoch,
                current.manifest_sha256, current.fingerprint, current.revision, current.mode)
        except Exception:
            self._active = False
            raise BrowserContinuationCurrentError() from None

    def observe(self) -> BrowserContinuationObservation:
        """Read active approval generation against original private inputs.

        Stopped modes expose no reviewed generation and perform no network I/O.
        Active does NOT mean this browser accepted/installed a session. This
        result cannot be supplied instead of reacquiring an actual owned scope.
        """
        try:
            state = self.inspect()
            view = epoch._read(self._db, self._selection, readonly=True)
            if view.snapshot != self._expected:
                raise ValueError()
            generation = (self._active_generation(view) if state.mode is RecoveryMode.ACTIVE
                          else None)
            self._files()
            if self.inspect() != state:
                raise ValueError()
            return BrowserContinuationObservation(state, generation)
        except Exception:
            self._active = False
            raise BrowserContinuationCurrentError() from None

    def _active_generation(self, view: epoch._View) -> int:
        # _read validated the exact current epoch, complete last approval, active
        # grant and full consistency fingerprint. Do not use generation ordering.
        row = view.approvals[-1]
        if row["digest"] != view.grant or row["phase"] != "complete":
            raise ValueError()
        profile = self._selection.profile
        config = load_browser_native_configuration(profile)
        retained = self._selection.history
        if (config.identity, config.origin, config.device_id) != (
                retained.identity, retained.origin, retained.device_id):
            raise ValueError()
        private: dict[str, tuple[bytes, tuple[int, int]]] = {}
        for name, limit in (("client.json", 4096), ("device.secret", 128), ("ca.pem", 128 * 1024)):
            path = profile / name
            binding = activation._binding(path)
            raw = _private_read(profile, name, limit)
            activation._manifest_check(path, raw, binding)
            private[name] = raw, binding
        _credential(private["device.secret"][0])
        generation = row["generation"]
        if (type(generation) is not int or not 1 <= generation < 2**53 - 1
                or row["device"] != config.device_id
                or row["credential_hash"] != hashlib.sha256(private["device.secret"][0]).hexdigest()
                or row["trust_hash"] != _trust(private["ca.pem"][0])):
            raise ValueError()
        # History rechecks include the ORIGINAL private bytes and file attributes,
        # not just whatever was present at entry to this observation.
        self._files()
        for name, (raw, binding) in private.items():
            activation._manifest_check(profile / name, raw, binding)
        if load_browser_native_configuration(profile) != config:
            raise ValueError()
        return generation


@contextmanager
def _read_owned(selected: _SelectedFiles, owner: ownership._HistoryOwnership
                ) -> Iterator[_CurrentRead]:
    reader = None
    try:
        owner.binding(selected.handoff)
        selected.recheck()
        with activation._ledger_transaction(selected.handoff._session._profile,
                                              readonly=True) as (db, ledger):
            reader = _CurrentRead(selected, owner, db, ledger)
            reader.inspect()  # No incomplete selection is ever yielded.
            yield reader
            reader.inspect()  # A caller cannot hide a caught, latched failure.
    finally:
        if reader is not None:
            reader._active = False


@contextmanager
def _stopped_current_scope(root: Path, *, bundle: Path, profile: Path, public_key: Path
                           ) -> Iterator[_CurrentRead]:
    """Internal stopped reader. Caller supplies installed paths, never saved evidence."""
    try:
        selected = _SelectedFiles(root, bundle=bundle, profile=profile, public_key=public_key)
        with (ownership._stopped_history_ownership(selected.handoff) as owner,
              _read_owned(selected, owner) as reader):
            yield reader
    except Exception:
        raise BrowserContinuationCurrentError() from None


@contextmanager
def _worker_current_scope(configuration: BrowserNativeConfiguration,
                          selection: BrowserWorkerSelection) -> Iterator[_CurrentRead]:
    """Internal live native child only; derive root from its actual browser ancestor."""
    try:
        selected = _select_worker_files(configuration, selection)
        with (ownership._worker_history_ownership(selected.handoff,
                configuration=configuration, selection=selection) as owner,
              _read_owned(selected, owner) as reader):
            yield reader
    except Exception:
        raise BrowserContinuationCurrentError() from None


def _select_worker_files(configuration: BrowserNativeConfiguration,
                          selection: BrowserWorkerSelection) -> _SelectedFiles:
    """Fixed bootstrap only, shared by separately owned internal readers/writers."""
    if (type(configuration) is not BrowserNativeConfiguration
            or type(selection) is not BrowserWorkerSelection
            or any(v is not None for v in (
                selection.directory, selection.normal_bundle, selection.intent))
            or configuration != load_browser_native_configuration(configuration.root)):
        raise ValueError()
    root = _browser_directory(selection.bundle, configuration.extension_origin)
    return _SelectedFiles(root, bundle=selection.bundle, profile=configuration.root,
                          public_key=selection.public_key)


def inspect_stopped_continuation(root: Path, *, bundle: Path, profile: Path,
                                 public_key: Path) -> BrowserCurrentContinuation:
    """Read-only current advisory result, not a runnable migration or permission lease."""
    with _stopped_current_scope(root, bundle=bundle, profile=profile,
                                public_key=public_key) as reader:
        return reader.inspect()


def _inspect_worker_continuation(configuration: BrowserNativeConfiguration,
                                 selection: BrowserWorkerSelection) -> BrowserCurrentContinuation:
    with _worker_current_scope(configuration, selection) as reader:
        return reader.inspect()


def _observe_worker_continuation(
    configuration: BrowserNativeConfiguration, selection: BrowserWorkerSelection,
) -> BrowserContinuationObservation:
    """Internal owned observation only; no action, role, startup or renewal wiring."""
    with _worker_current_scope(configuration, selection) as reader:
        return reader.observe()
