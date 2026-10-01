#!/usr/bin/env python3
"""Explicit one-attempt observer protocol, uninstalled and not self-authorizing.

The trusted caller supplies an independently qualified original peer channel
and a trusted read-only qualification function. That function must check the
exact intended finite command, complete source/runtime/confinement, original
target and input pins. The legacy action-free probe policy is NOT sufficient.
No installed caller, finite command or new helper source profile selects this.
"""

from __future__ import annotations

import select
import socket
import time

import supplemental_recording_permission_review as reviews
import supplemental_recording_service_permission as permission

MESSAGE = "Recording permission delivery is unconfirmed; preserve the case and do not send again."


class UnconfirmedDelivery(ValueError):
    """A local completed write is not a receiver acknowledgment."""


def require(value):
    if not value:
        raise UnconfirmedDelivery(MESSAGE)


class Sender(permission._Peer):
    """Read one challenge, then explicitly qualify once and write one reply.

    All descriptors remain caller-owned, including after interruption. qualify
    is trusted observer CODE, not a network callback or approval boolean; it
    receives the original Review and must return None after full independent
    checks. The caller is responsible for that code's provenance and policy.
    This library cannot infer that arbitrary callback code has done the review.

    receive() sends nothing. send() never loops qualification or retries a
    logical response. Partial nonblocking writes finish only that same bounded
    frame. write_attempted/write_complete describe LOCAL dispatch, never remote
    acceptance, a continuing service clock, readiness, or recording authority.
    """

    def __init__(
        self,
        template,
        template_sha256,
        baseline_sha256,
        observer,
        target,
        domain,
        timer,
        channel,
        qualify,
    ):
        self.review = self.original_review = None
        self.receive_attempted = self.received = self.send_attempted = False
        self.qualification_attempted = self.write_attempted = self.write_complete = False
        try:
            require(callable(qualify))
            self.qualify = self.original_qualify = qualify
            # _Peer's role names are receiver-relative: here the local process
            # is the observer and its peer is the target waiting for permission.
            super().__init__(
                template,
                template_sha256,
                baseline_sha256,
                observer,
                target,
                domain,
                timer,
                channel,
                incoming=True,
            )
        except BaseException as error:
            self._fail(error)

    def receive(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(not self.receive_attempted)
            self.receive_attempted = True
            raw = bytearray()
            while b"\n" not in raw:
                self._check(self.wait_by)
                try:
                    chunk = self.channel.recv(
                        permission.MAX_BYTES + 1 - len(raw), socket.MSG_DONTWAIT
                    )
                    require(bool(chunk))
                    raw.extend(chunk)
                    require(len(raw) <= permission.MAX_BYTES)
                except BlockingIOError:
                    self._poll(select.POLLIN, self.wait_by)
                self._check(self.wait_by)
            self._quiet()
            self.review = reviews.Review(
                bytes(raw),
                self.template,
                self.template_sha256,
                self.baseline_sha256,
                self.target,
                self.observer,
                self.timer,
                self.domain,
            )
            self.original_review = self.review
            self.frame = self.review.raw
            self.digest = self.review.challenge_sha256
            self.end = min(self.wait_by, self.review.cutoff)
            self.received = True
            self._guard(self.end)
            return self.review
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _guard(self, end):
        require(self.received and end <= min(self.wait_by, self.review.cutoff))
        self._check(end)
        require(self.received and self.review is self.original_review)
        require(self.qualify is self.original_qualify)
        require(self.review.raw is self.frame and self.review.challenge_sha256 == self.digest)
        require(self.review.template is self.template and self.review.target is self.observer)
        require(self.review.observer is self.target and self.review.timer is self.timer)
        require(self.review.domain is self.domain)
        self.review.read()
        self._quiet()
        self._check(end)
        require(not self.review.failed and not self.review.closed)
        require(self.qualify is self.original_qualify)

    def send(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(self.received and not self.send_attempted)
            self.send_attempted = True
            self._guard(self.end)
            end = min(self.end, time.monotonic() + permission.IO_SECONDS)
            self.qualification_attempted = True
            result = self.qualify(self.review)
            require(result is None)  # A truthy approval token is not this code contract.
            self._guard(end)
            raw = permission.permission_bytes(self.digest)
            offset = 0
            end = min(self.end, time.monotonic() + permission.IO_SECONDS)
            self.write_attempted = True
            while offset < len(raw):
                self._guard(end)
                try:
                    count = self.channel.send(
                        raw[offset:], socket.MSG_DONTWAIT | socket.MSG_NOSIGNAL
                    )
                    require(count > 0)
                    offset += count
                    self.write_complete = offset == len(raw)
                except BlockingIOError:
                    self._poll(select.POLLOUT, end)
                self._guard(end)
            self._guard(end)
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedDelivery(MESSAGE) from None

    def close(self):
        super().close()
        if self.review is not None:
            self.review.close()  # Only owns its descriptor-free comparison wrapper.


if __name__ == "__main__":
    raise SystemExit("Uninstalled permission sender; no observer policy or service selected.")
