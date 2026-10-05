#!/usr/bin/env python3
"""Read-only observer review of one preflight challenge, not a permission sender.

The caller must receive the exact frame through an independently authenticated
original peer channel. Live process/domain checks here do not establish frame
provenance, source/runtime/confinement qualification or an observer decision.
No socket operation, permission response, plan publication or App action occurs.
This outer-only library is not in either existing observed helper profile.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import asdict
from threading import Lock, get_ident

import supplemental_recording_service_clock_link as clocks
import supplemental_recording_service_permission as permission

templates, domains, clock = permission.templates, permission.domains, permission.clock
MESSAGE = (
    "Recording preflight challenge is unconfirmed; preserve the case and do not send permission."
)
FIELDS = frozenset(
    {
        "schema",
        "kind",
        "case",
        "template_sha256",
        "baseline_sha256",
        "target",
        "observer",
        "original_clock",
        "domain_sha256",
        "wait_by",
        "deadline",
        "nonce",
    }
)


class UnconfirmedReview(ValueError):
    """A syntactically valid challenge is not independent approval."""


def require(value):
    if not value:
        raise UnconfirmedReview(MESSAGE)


class Review:
    """Bind challenge bytes to original independently known inputs and peers.

    Only the ORIGINAL observer's continuing local clock/domain/pidfd are used.
    The remote preflight sample remains in its own verified namespace; a private
    temporary plan lets the existing ObserverClock compare it without relabeling
    or adopting it as a service clock. Either original wait cutoff may expire
    this review. Reconstructing a reviewer cannot renew those original clocks.

    read() only returns an observer-domain clock observation. It never sends a
    reply or means that either process's source has been qualified. A future
    sender must bind the authenticated original channel, original reviewed raw
    bytes, full independent qualification and one write attempt separately.
    """

    def __init__(
        self, raw, template, template_sha256, baseline_sha256, observer, target, timer, domain
    ):
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.lock = Lock()
        self.closed = self.failed = False
        self.link = None
        try:
            require(self.owner[2] == permission.ROOT_UID)
            require(type(raw) is bytes and 1 < len(raw) <= permission.MAX_BYTES)
            require(raw.endswith(b"\n"))
            value = templates.object_json(raw[:-1])
            templates.plans.mapping(value, FIELDS)
            require(templates.plans.base.encode(value) + b"\n" == raw)
            require(type(template) is templates.Template and template.sha256 == template_sha256)
            for digest in (template_sha256, baseline_sha256, value["nonce"]):
                require(type(digest) is str)
                templates.plans.base.digest(digest)
            require(type(observer) is domains.process.ProcessIdentity)
            require(observer.pid == self.owner[0])
            require(type(target) is domains.process.ProcessWitness)
            require(target.identity.pid != self.owner[0])
            require(type(timer) is clock.ClockWitness and type(domain) is domains.ZeroDomain)
            self.raw, self.template, self.observer, self.target = raw, template, observer, target
            self.timer, self.domain = timer, domain
            self.objects = raw, template, observer, target, timer, domain
            self.template_raw, self.local_origin = template.raw, timer.original
            self.template_sha256, self.baseline_sha256 = template_sha256, baseline_sha256
            self.target_identity = target.identity
            self.observer_pin = templates.plans.base.encode(asdict(observer))
            self.pidfd = target.fd
            self.pidfd_pin = permission.Permission._fd_identity(self.pidfd)
            self.proof = domain.refresh()
            require(
                self.proof.init == target.identity and self.proof.original_clock is timer.original
            )
            self.proof_sha256 = self.proof.sha256
            fields = templates.plans.mapping(
                value["original_clock"],
                {"boot", "namespace", "before_ns", "after_ns", "boottime_ns"},
            )
            require(type(fields["namespace"]) is list)
            remote = clock.Window(**(fields | {"namespace": tuple(fields["namespace"])}))
            require(remote.namespace == self.proof.native_time)
            self.remote_origin = remote
            deadline = remote.after_ns / clock.NS + permission.WAIT_SECONDS
            wait_by = deadline - permission.IO_SECONDS
            # Receiver and observer see the same two proven domains in opposite
            # order. This recomputation checks the remote statement's consistency;
            # it is NOT a reconstruction of its live ZeroDomain or ClockWitness.
            reverse = domains.Evidence(
                observer, self.proof.native_time, self.proof.host_time, self.proof.user, remote
            )
            expected = dict(
                schema=1,
                kind=permission.CHALLENGE_KIND,
                case=templates._read(template.raw)["plan"]["case"],
                template_sha256=template_sha256,
                baseline_sha256=baseline_sha256,
                observer=asdict(observer),
                target=asdict(target.identity),
                original_clock=asdict(remote),
                domain_sha256=reverse.sha256,
                deadline=deadline,
                wait_by=wait_by,
                nonce=value["nonce"],
            )
            # Canonical bytes distinguish equal-but-wrong types (True/1, int/float).
            require(templates.plans.base.encode(expected) + b"\n" == raw)
            self.cutoff = min(
                wait_by,
                timer.original.after_ns / clock.NS
                + permission.WAIT_SECONDS
                - permission.IO_SECONDS,
            )
            self.challenge_sha256 = hashlib.sha256(raw[:-1]).hexdigest()
            self.values = self._values()
            self.plan = template.preview(remote)
            self.link = clocks.ObserverClock(self.plan, timer, domain, target.identity)
            self.original_link = self.link
            self.read()
        except BaseException as error:
            self._fail(error)

    def _values(self):
        return (
            self.template_raw,
            self.template_sha256,
            self.baseline_sha256,
            self.observer_pin,
            self.target_identity,
            self.local_origin,
            self.remote_origin,
            self.pidfd,
            self.pidfd_pin,
            self.proof_sha256,
            self.cutoff,
            self.challenge_sha256,
        )

    def _binding(self, end):
        require(not self.failed and not self.closed and time.monotonic() < end <= self.cutoff)
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require(
            all(
                a is b
                for a, b in zip(
                    (self.raw, self.template, self.observer, self.target, self.timer, self.domain),
                    self.objects,
                    strict=True,
                )
            )
        )
        require(self._values() == self.values)
        require(self.template.raw == self.template_raw)
        require(templates.plans.base.encode(asdict(self.observer)) == self.observer_pin)
        require(
            self.timer.original is self.local_origin
            and not self.timer.closed
            and not self.timer.failed
        )
        require(
            self.domain.evidence is self.proof and not self.domain.closed and not self.domain.failed
        )
        require(self.proof.sha256 == self.proof_sha256)
        require(self.link is self.original_link and not self.link.closed and not self.link.failed)
        require(self.link.plan is self.plan and self.link.plan.original_clock == self.remote_origin)
        require(self.link.observer is self.timer and self.link.domain is self.domain)
        require(self.link.target is self.target_identity)
        self.link._binding(end)  # Also reject final-read plan/target substitutions.
        require(self.target.fd == self.pidfd and self.target.identity is self.target_identity)
        require(permission.Permission._fd_identity(self.pidfd) == self.pidfd_pin)
        require(not self.target.exited())
        require(
            domains.process.read_identity(self.observer.pid, self.observer.container_id)
            == self.observer
        )
        require(
            domains.process.read_identity(
                self.target_identity.pid, self.target_identity.container_id
            )
            == self.target_identity
        )
        require(time.monotonic() < end)

    def read(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            end = min(self.cutoff, time.monotonic() + clocks.MAX_SECONDS)
            self._binding(end)
            observed = self.link.read()
            self._binding(end)
            return observed  # Observer sample, never a permission or remote clock.
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedReview(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        self.closed = True
        if self.link is not None:
            self.link.close()  # Link owns no borrowed clock/domain/process handles.


if __name__ == "__main__":
    raise SystemExit("Read-only preflight challenge review; no permission or service enabled.")
