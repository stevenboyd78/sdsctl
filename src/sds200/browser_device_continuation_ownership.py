"""Internal scoped ownership for historical reads, never continuation permission.

Stopped administration and a selected live native child have separate entrypoints.
Neither caller can select a role through a browser message or an ignore-lock flag.
Existing normal startup/request paths do not use this groundwork yet.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from .browser_device_handoff import BrowserRecoveryHandoff, _busy
from .browser_device_native import BrowserNativeConfiguration, load_browser_native_configuration
from .browser_device_profile_access import browser_profile_access
from .browser_device_startup import _launch_lock
from .browser_device_worker import BrowserWorkerSelection, _browser_directory

if TYPE_CHECKING:
    from .browser_device_resume_workflow import BrowserResumeWorkflow


class BrowserContinuationOwnershipError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Continuation history ownership is unavailable or changed. "
                         "Keep all state; no current permission was established.")


class _HistoryOwnership:
    """Same-process, same-handoff scope; cannot be cached after its context exits."""

    def __init__(self, handoff: BrowserRecoveryHandoff,
                 verify: Callable[[], list[list[int]]]) -> None:
        self._handoff, self._verify = handoff, verify
        self._process = (os.getpid(), os.getppid())
        self._binding = verify()
        self._active = True

    def binding(self, handoff: BrowserRecoveryHandoff) -> list[list[int]]:
        try:
            if (not self._active or handoff is not self._handoff
                    or self._process != (os.getpid(), os.getppid())
                    or self._verify() != self._binding):
                raise ValueError()
            return [row.copy() for row in self._binding]
        except Exception:
            # An observed loss of ownership cannot be repaired within this scope.
            self._active = False
            raise BrowserContinuationOwnershipError() from None


def _session(handoff: BrowserRecoveryHandoff) -> BrowserResumeWorkflow:
    if type(handoff) is not BrowserRecoveryHandoff or not handoff._supervised:
        raise BrowserContinuationOwnershipError()
    return handoff._session


@contextmanager
def _profile_scope(handoff: BrowserRecoveryHandoff,
                   browser: Callable[[], list[list[int]]]) -> Iterator[_HistoryOwnership]:
    s = _session(handoff)
    owner = None
    try:
        with (browser_profile_access(s._profile, exclusive=False) as profile_inode,
              browser_profile_access(s._archives, exclusive=False) as archive_inode):
            configuration = load_browser_native_configuration(s._profile)

            def verify() -> list[list[int]]:
                # Check the named private directories inside the scope as well
                # as on context exit; callers must recheck before using evidence.
                with (browser_profile_access(s._profile, exclusive=False) as current_profile,
                      browser_profile_access(s._archives, exclusive=False) as current_archive):
                    if ((current_profile, current_archive) != (profile_inode, archive_inode)
                            or load_browser_native_configuration(s._profile) != configuration):
                        raise ValueError()
                    return browser()

            owner = _HistoryOwnership(handoff, verify)
            yield owner
            owner.binding(handoff)
    except Exception:
        raise BrowserContinuationOwnershipError() from None
    finally:
        if owner is not None:
            owner._active = False


@contextmanager
def _stopped_history_ownership(handoff: BrowserRecoveryHandoff) -> Iterator[_HistoryOwnership]:
    """Acquire stopped launcher ownership; never adopt a caller's busy lock."""
    try:
        s = _session(handoff)
        with _launch_lock(s._root, create=False):
            def browser() -> list[list[int]]:
                binding = s._browser_binding(stopped=True)
                _busy(s._root / ".sdsctl-device-launch.lock", binding[1])
                return binding

            with _profile_scope(handoff, browser) as owner:
                yield owner
    except Exception:
        raise BrowserContinuationOwnershipError() from None


@contextmanager
def _worker_history_ownership(
    handoff: BrowserRecoveryHandoff, *, configuration: BrowserNativeConfiguration,
    selection: BrowserWorkerSelection,
) -> Iterator[_HistoryOwnership]:
    """Internal native-child boundary, after fixed-wrapper selection, never a grant.

    Revalidate the actual Chromium ancestor and busy launcher inode on each check.
    Chromium's current Singleton markers are allowed only through this boundary.
    Same-account/root code remains trusted, as in the ordinary worker owner check.
    """
    try:
        s = _session(handoff)
        if (type(configuration) is not BrowserNativeConfiguration
                or type(selection) is not BrowserWorkerSelection
                or any(value is not None for value in (
                    selection.directory, selection.normal_bundle, selection.intent))
                or selection.bundle != s._registration["bundle"]
                or selection.public_key != s._registration["public_key"]
                or configuration != load_browser_native_configuration(s._profile)):
            raise ValueError()

        def browser() -> list[list[int]]:
            if (configuration != load_browser_native_configuration(s._profile)
                    or _browser_directory(selection.bundle,
                                          configuration.extension_origin) != s._root):
                raise ValueError()
            binding = s._browser_binding(stopped=False)
            _busy(s._root / ".sdsctl-device-launch.lock", binding[1])
            return binding

        with _profile_scope(handoff, browser) as owner:
            yield owner
    except Exception:
        raise BrowserContinuationOwnershipError() from None
