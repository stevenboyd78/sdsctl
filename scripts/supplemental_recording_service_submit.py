#!/usr/bin/env python3
"""Uninstalled independent startup-message publication; no service action.

The caller authenticates the original template and plan digests independently.
This writer transports those explicit digests, never discovers approval from a
file or self-supplied hash. The continuing service owner alone enforces its
original clock/domain/acceptance deadline. No cross-domain clock is relabeled.
"""

from __future__ import annotations

import os
import stat
import time
from contextlib import ExitStack, contextmanager
from threading import Lock, get_ident

import supplemental_recording_service_acceptance as acceptance

intake, publication = acceptance.intake, acceptance.publication
PENDING = ".pending-startup-acceptance"
LOCK_POLL_SECONDS, MAX_LOCK_POLLS = 0.01, 200
MESSAGE = "Recording startup submission is unconfirmed; preserve the case and do not resubmit."


class UnconfirmedSubmission(ValueError):
    """Delivery uncertainty neither authorizes retry nor proves service acceptance."""


def require(value):
    if not value:
        raise UnconfirmedSubmission(MESSAGE)


@contextmanager
def _submission_directory(path, check):
    """Wait only for a known PRE-WRITE flock contention inside one attempt.

    Catch DirectoryBusy only from entering the private-directory context,
    never from its body or exit. Once acquired, no operation may be repeated.
    The caller's original two-second bound is checked throughout; no exception
    from a write, publication, acknowledgment or other I/O is retryable here.
    """
    with ExitStack() as stack:
        for _ in range(MAX_LOCK_POLLS):
            check()
            try:
                directory = stack.enter_context(
                    publication.protected._private_directory(path, exclusive=True)
                )
            except publication.protected.DirectoryBusy:
                check()
                time.sleep(LOCK_POLL_SECONDS)
            else:
                break
        else:
            require(False)
        check()
        yield directory


class Submission:
    """One exclusive durable message in a borrowed original private CasePlan.

    The caller supplies the independently reviewed template AND final-plan
    digest; this object cannot authenticate their provenance. It performs no
    original-clock capture, acceptance, journal, App, or recording operation.
    A successful message hash proves only this publication path completed, not
    that a service accepted it. Lost acknowledgments and remaining pending files
    forbid resubmission. Caller-owned plan handles and all evidence are preserved.
    Initial known directory-lock contention waits within this SAME bounded
    attempt, before any file creation. It cannot retry publication or an error.
    """

    def __init__(self, original, expected_template_sha256, independently_reviewed_plan_sha256):
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.lock = Lock()
        self.used = self.failed = False
        try:
            require(type(original) is intake.CasePlan)
            self.original, self.plan = original, original.recheck()
            self.raw = acceptance.acceptance_bytes(
                expected_template_sha256, independently_reviewed_plan_sha256
            )
            require(self.plan.sha256 == independently_reviewed_plan_sha256)
            self.path = self.plan.root
            self.objects = original, self.plan, self.path, self.raw
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedSubmission(MESSAGE) from None

    def submit(self):
        acquired = False
        output = -1
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(not self.used and not self.failed)
            self.used = True
            end = time.monotonic() + acceptance.MAX_SECONDS

            def check():
                require(not self.failed and time.monotonic() < end)
                require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
                require(
                    all(
                        a is b
                        for a, b in zip(
                            (self.original, self.plan, self.path, self.raw),
                            self.objects,
                            strict=True,
                        )
                    )
                )
                require(self.original.recheck() is self.plan and self.path == self.plan.root)
                require(time.monotonic() < end)

            check()
            with _submission_directory(self.path, check) as directory:
                require(os.fstat(directory).st_gid == self.owner[3])
                identity = intake.files.identity(os.fstat(directory))[:6]

                def guarded():
                    check()
                    require(intake.files.identity(os.fstat(directory))[:6] == identity)
                    require(
                        intake.files.identity(os.stat(self.path, follow_symlinks=False))[:6]
                        == identity
                    )
                    require(time.monotonic() < end)

                guarded()
                base = {publication.CLAIM, "plan.json"}
                publication._names(directory, base)
                output = os.open(
                    PENDING,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=directory,
                )
                opened = intake.files.identity(os.fstat(output))
                require(stat.S_ISREG(opened[2]) and stat.S_IMODE(opened[2]) == 0o600)
                require(opened[3:6] == (*self.owner[2:], 1))
                require(os.write(output, self.raw) == len(self.raw))
                os.fsync(output)
                guarded()
                complete = intake.files.identity(os.fstat(output))
                require(complete[:6] == opened[:6] and complete[6] == len(self.raw))
                require(os.pread(output, acceptance.MAX_BYTES + 1, 0) == self.raw)
                require(
                    intake.files.identity(os.stat(PENDING, dir_fd=directory, follow_symlinks=False))
                    == complete
                )
                publication._names(directory, base | {PENDING})
                os.link(
                    PENDING,
                    acceptance.NAME,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
                linked = intake.files.identity(os.fstat(output))
                require(linked[:5] == complete[:5] and linked[5] == 2)
                require(linked[6:8] == complete[6:8])
                for name in (PENDING, acceptance.NAME):
                    require(
                        intake.files.identity(
                            os.stat(name, dir_fd=directory, follow_symlinks=False)
                        )
                        == linked
                    )
                guarded()
                # Delete only our verified temporary hardlink; the submitted
                # message and every failed/uncertain file are never overwritten.
                os.unlink(PENDING, dir_fd=directory)
                os.fsync(directory)
                guarded()
                final = intake.files.identity(os.fstat(output))
                require(final[:6] == complete[:6] and final[6:8] == complete[6:8])
                require(os.pread(output, acceptance.MAX_BYTES + 1, 0) == self.raw)
                require(intake.files.identity(os.fstat(output)) == final)
                require(
                    intake.files.identity(
                        os.stat(acceptance.NAME, dir_fd=directory, follow_symlinks=False)
                    )
                    == final
                )
                publication._names(directory, base | {acceptance.NAME})
                guarded()
                closing, output = output, -1
                os.close(closing)
            check()
            return intake.plans.base.checksum(acceptance.object_json(self.raw))
        except BaseException as error:
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
    raise SystemExit("Offline startup submission library only; no installed service enabled.")
