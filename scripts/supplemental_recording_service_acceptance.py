#!/usr/bin/env python3
"""Uninstalled one-use startup acceptance intake; no service or recording action.

A private file can carry independently reviewed digests; it cannot establish
that review occurred. The external launcher must authenticate the original
template, offer, owner and runtime separately before submitting those digests.
This reader requires continuing original publication/plan/clock custody and
cannot resume an existing case after restart or reconstruct a lost owner.
"""

from __future__ import annotations

import os
import stat
import time
from contextlib import ExitStack, contextmanager, suppress
from threading import Lock, get_ident

import supplemental_recording_service_publish as publication
from supplemental_handoff_host import object_json

intake, offers = publication.intake, publication.offers
MAX_BYTES, MAX_SECONDS = 1024, 2
NAME = "startup-acceptance.json"
KIND = "finite-recording-startup-acceptance-v1"
MESSAGE = (
    "Recording startup acceptance is unconfirmed; preserve the original case and do not resubmit."
)


class UnconfirmedAcceptance(ValueError):
    """Fixed errors do not reveal submitted content or private paths."""


def require(value):
    if not value:
        raise UnconfirmedAcceptance(MESSAGE)


def acceptance_bytes(expected_template_sha256, independently_reviewed_plan_sha256):
    """Pure private message bytes, not review, publication, or action permission."""
    try:
        for value in (expected_template_sha256, independently_reviewed_plan_sha256):
            require(type(value) is str)
            intake.plans.base.digest(value)
        return intake.plans.base.encode(
            {
                "schema": 1,
                "kind": KIND,
                "template_sha256": expected_template_sha256,
                "plan_sha256": independently_reviewed_plan_sha256,
            }
        )
    except Exception:
        raise UnconfirmedAcceptance(MESSAGE) from None


@contextmanager
def _reading_directory(path):
    with ExitStack() as stack:
        try:
            directory = stack.enter_context(
                publication.protected._private_directory(path, exclusive=False)
            )
        except publication.protected.DirectoryBusy:
            yield None
        else:
            yield directory


class Acceptance:
    """Read one private submission against the original successful Publisher.

    Missing input or an actual cooperating publication lock means 'not yet',
    still bounded by the original offer. Once input exists, this model consumes
    one attempt even if malformed, uncertain or expired. The input stays in
    place, and no durable success acknowledgment is fabricated. A returned plan
    is structural acceptance only; journal/service/recording gates are separate.
    Closing this model never closes caller-owned CasePlan or ClockWitness.
    """

    def __init__(self, publisher):
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.lock = Lock()
        self.failed = self.closed = self.used = self.accepted = False
        try:
            require(type(publisher) is publication.Publisher)
            require(publisher.owner == self.owner and publisher.acceptance_owner is None)
            require(publisher.used and not publisher.failed)
            require(type(publisher.published) is intake.CasePlan)
            publisher.acceptance_owner = self
            self.publisher, self.original = publisher, publisher.published
            self.offer = self._original_offer = publisher.original_offer
            self.plan = self.original.recheck()
            self.objects = publisher, self.original, self.offer, self.plan
            self._guard(time.monotonic() + MAX_SECONDS)
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        offer = getattr(self, "_original_offer", None)
        if type(offer) is offers.Offer and offer.owner == (os.getpid(), get_ident()):
            offers.Offer.close(offer)
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedAcceptance(MESSAGE) from None

    def _guard(self, end, *, accepted=False):
        require(not self.failed and not self.closed and time.monotonic() < end)
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require(
            all(
                a is b
                for a, b in zip(
                    (self.publisher, self.original, self.offer, self.plan),
                    self.objects,
                    strict=True,
                )
            )
        )
        require(self.publisher.owner == self.owner)
        require(self.publisher.used and not self.publisher.failed)
        require(self.publisher.acceptance_owner is self)
        require(self.publisher.published is self.original)
        require(self.publisher.original_offer is self.offer)
        require(self.publisher.offer is self.offer)
        require(self.offer is self._original_offer)
        require(self.original.recheck() is self.plan)
        require(type(self.offer) is offers.Offer)
        if accepted:
            require(self.offer.used and self.offer.accepted)
            self.offer._check()
            proposed = self.offer.plan
        else:
            proposed = self.offer.inspect()
        require(proposed is self.publisher.original_plan and proposed is self.publisher.plan)
        require(proposed.raw == self.plan.raw and time.monotonic() < end)

    def poll(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(not self.used)
            end = time.monotonic() + MAX_SECONDS
            self._guard(end)
            result = None
            with _reading_directory(self.plan.root) as directory:
                self._guard(end)
                before = None
                if directory is not None:
                    with suppress(FileNotFoundError):
                        before = os.stat(NAME, dir_fd=directory, follow_symlinks=False)
                    publication._names(
                        directory,
                        {publication.CLAIM, "plan.json"}
                        | ({NAME} if before is not None else set()),
                    )
                if before is not None:
                    result = self._consume(directory, before, end)
            self._guard(end, accepted=self.used)
            self.accepted = self.used
            return result
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _consume(self, directory, before, end):
        fd = -1
        self.used = True
        try:
            require(
                stat.S_ISREG(before.st_mode)
                and stat.S_IMODE(before.st_mode) == 0o600
                and (before.st_uid, before.st_gid) == self.owner[2:]
                and before.st_nlink == 1
                and 0 < before.st_size <= MAX_BYTES
            )
            expected = intake.files.identity(before)
            fd = os.open(
                NAME,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=directory,
            )

            def check_file():
                publication._names(directory, {publication.CLAIM, "plan.json", NAME})
                require(intake.files.identity(os.fstat(fd)) == expected)
                require(
                    intake.files.identity(os.stat(NAME, dir_fd=directory, follow_symlinks=False))
                    == expected
                )
                require(time.monotonic() < end)

            check_file()
            raw = os.pread(fd, MAX_BYTES + 1, 0)
            require(len(raw) == before.st_size)
            value = object_json(raw)
            require(raw == acceptance_bytes(self.offer.template_sha256, self.plan.sha256))
            # Exact canonical equality excludes unknown/duplicate fields,
            # alternate types, wrong case/clock pins and noncanonical JSON.
            require(value["plan_sha256"] == self.plan.sha256)
            check_file()
            self._guard(end)
            result = self.offer.accept(value["plan_sha256"])
            check_file()
            self._guard(end, accepted=True)
            closing, fd = fd, -1
            os.close(closing)  # Never retry an uncertain descriptor close.
            return result
        finally:
            if fd >= 0:
                os.close(fd)

    def close(self):
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        self.closed = True


if __name__ == "__main__":
    raise SystemExit("Offline startup intake only; no service or recording action enabled.")
