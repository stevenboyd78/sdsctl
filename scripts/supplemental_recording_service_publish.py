#!/usr/bin/env python3
"""Uninstalled one-attempt startup-plan publication, never plan acceptance.

Only a caller-owned, still-unaccepted original Offer may publish into its already
created EMPTY private case directory. A durable exclusive claim precedes the
plan. Both files are preserved on every outcome, including partial writes and
lost acknowledgments. No helper, directory, journal, App or recording is started.
This module is deliberately outside the qualified helper image/command graph.
"""

from __future__ import annotations

import os
import stat
import time
from threading import Lock, get_ident

import supplemental_recording_protected as protected
import supplemental_recording_service_input as intake
import supplemental_recording_service_offer as offers

MAX_SECONDS = 2
CLAIM = "startup-claim.json"
MESSAGE = "Recording startup publication is unconfirmed; preserve all files and do not retry."


class UnconfirmedPublication(ValueError):
    """No private paths, raw plans or inner exception details are exposed."""


def require(value):
    if not value:
        raise UnconfirmedPublication(MESSAGE)


def _names(fd, expected):
    found = set()
    with os.scandir(fd) as entries:
        for entry in entries:
            require(len(found) < len(expected) and entry.name in expected)
            found.add(entry.name)
    require(found == expected)


class Publisher:
    """One bounded exclusive write; success returns caller-owned CasePlan.

    The caller must create/authenticate the empty case directory independently,
    keep its original Offer/ClockWitness alive, and obtain acceptance separately.
    A returned CasePlan is only retained read-only file evidence. It neither
    consumes the Offer nor proves independent review, readiness or permission.
    Any failure poisons this publisher and closes the borrowed offer MODEL, but
    never closes its original caller-owned clock. File I/O still needs independent
    outer supervision against kernel stalls. A new object is not retry authority.
    """

    def __init__(self, offer):
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.lock = Lock()
        self.used = self.failed = False
        self.published = None
        self.acceptance_owner = None
        try:
            require(type(offer) is offers.Offer)
            self.offer = self.original_offer = offer
            self.plan = self.original_plan = offer.inspect()
            self.path = self.plan.root
            self.raw = self.plan.raw
            self.claim = intake.plans.base.encode(
                {
                    "schema": 1,
                    "kind": "finite-recording-startup-claim-v1",
                    "case": self.plan.case,
                    "boot": self.plan.boot,
                    "template_sha256": offer.template_sha256,
                    "plan_sha256": self.plan.sha256,
                }
            )
            self.values = self.path, self.raw, self.claim
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        # Only the actual original owner may invalidate the borrowed model.
        # A foreign caller still poisons this Publisher; it cannot close handles.
        offer = getattr(self, "original_offer", None)
        if type(offer) is offers.Offer and offer.owner == (os.getpid(), get_ident()):
            offers.Offer.close(offer)
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedPublication(MESSAGE) from None

    def publish(self):
        acquired = False
        output = -1
        original = None
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(not self.used and not self.failed)
            self.used = True
            end = time.monotonic() + MAX_SECONDS

            def check():
                require(not self.failed and time.monotonic() < end)
                require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
                require(self.offer is self.original_offer and self.plan is self.original_plan)
                require((self.path, self.raw, self.claim) == self.values)
                require(self.offer.inspect() is self.plan)
                require(self.path == self.plan.root and self.raw == self.plan.raw)
                require(time.monotonic() < end)

            check()
            with protected._private_directory(self.path, exclusive=True) as directory:
                info = os.fstat(directory)
                require(info.st_gid == os.getegid())
                directory_id = intake.files.identity(info)[:6]

                def guarded():
                    check()
                    require(intake.files.identity(os.fstat(directory))[:6] == directory_id)
                    require(
                        intake.files.identity(os.stat(self.path, follow_symlinks=False))[:6]
                        == directory_id
                    )
                    require(time.monotonic() < end)

                guarded()
                _names(directory, set())
                records = []
                for name, raw in ((CLAIM, self.claim), ("plan.json", self.raw)):
                    guarded()
                    output = os.open(
                        name,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                        0o600,
                        dir_fd=directory,
                    )
                    opened = intake.files.identity(os.fstat(output))
                    require(stat.S_ISREG(opened[2]) and stat.S_IMODE(opened[2]) == 0o600)
                    require(opened[3:6] == (*self.owner[2:], 1))
                    require(os.write(output, raw) == len(raw))
                    os.fsync(output)
                    guarded()
                    require(os.pread(output, len(raw) + 1, 0) == raw)
                    complete = intake.files.identity(os.fstat(output))
                    require(complete[:6] == opened[:6] and complete[6] == len(raw))
                    require(
                        intake.files.identity(
                            os.stat(name, dir_fd=directory, follow_symlinks=False)
                        )
                        == complete
                    )
                    os.fsync(directory)
                    guarded()
                    records.append((name, complete))
                    closing, output = output, -1
                    # An uncertain close must not be retried: the descriptor
                    # may already have been released and reused by the kernel.
                    os.close(closing)
                    _names(directory, {entry[0] for entry in records})
                # Reopen only into the existing read-only retained intake. This
                # is the initial adoption, not reopening a failed CasePlan.
                original = intake.CasePlan(self.path, self.plan.sha256)
                require(original.recheck().raw == self.raw)
                for name, complete in records:
                    require(
                        intake.files.identity(
                            os.stat(name, dir_fd=directory, follow_symlinks=False)
                        )
                        == complete
                    )
                guarded()
                _names(directory, {CLAIM, "plan.json"})
            # The private-directory context verifies every original ancestor on
            # exit. No durable file, claim or partial payload is ever removed.
            check()
            self.published = original
            return original
        except BaseException as error:
            try:
                if original is not None:
                    original.close()
            finally:
                self._fail(error)
        finally:
            try:
                if output >= 0:
                    try:
                        os.close(output)
                    except BaseException as error:
                        self._fail(error)
            finally:
                if acquired:
                    self.lock.release()


if __name__ == "__main__":
    raise SystemExit("Offline startup publication library only; no installed service enabled.")
