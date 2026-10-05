#!/usr/bin/env python3
"""Explicit read-only App candidate assembly in the original idle service.

Uninstalled. This supplies App-aware resources, not a native/service transition.
The existing direct Launch/IdleService action gates deliberately still refuse
this leaf. Startup, service, journal, session and init witness stay borrowed.
No publication, source profile selection or recording permission occurs here.
"""

from __future__ import annotations

import os

import supplemental_recording_app_launch as inputs
import supplemental_recording_service_operator as operator

launch, require = operator.launch, operator.require


class AppIdleCandidate(operator.IdleCandidate):
    """Original service-owned readers with the exact native App input policy.

    Construction acquires only Idle's duplicate descriptors and read-only host
    and native-input readers. Qualification itself remains a separate bounded
    operation. Partial failures close only these acquired resources; original
    service custody and the accepted Startup clock are never reacquired.
    """

    def __init__(
        self,
        owner,
        service,
        *,
        published,
        bridge_sha256,
        image_environment_sha256,
        timezone,
        hostname,
        architecture,
        runtime_workers=1,
        zero_domain=None,
    ):
        self.owner = (os.getpid(), operator.get_ident())
        self.failed = self.closed = False
        self._cleanup = []
        try:
            require(type(self) is AppIdleCandidate and type(service) is operator.IdleService)
            require(type(owner) is inputs.publication.startup.Startup)
            require(service.candidate_attempted and service.candidate is None)
            self.startup, self.service, self.published = owner, service, published
            self.bridge_sha256 = bridge_sha256
            self.app_objects = owner, service, published, owner.clock
            self._app_custody()
            service._context()
            self.coordinator = service.coordinator
            machine = self._phase()
            self.witness = self.coordinator.cancel.candidate_witness
            require(type(self.witness) is launch.engine.dispatch.process.ProcessWitness)
            self.record = self.coordinator.cancel.candidate_record
            self.fd = self.witness.fd
            self.fd_identity = operator.intake.files.identity(os.fstat(self.fd))
            self.zero_domain = zero_domain
            entries = tuple(operator.base.encode(e) for e in service.journal.entries)
            self._custody(machine)
            self.idle = launch.idle_module.Idle(
                service.plan,
                self.witness,
                self.record.generation,
                zero_domain=zero_domain,
            )
            self._cleanup.append(self.idle.close)
            self.reader = launch.BootstrapHost(
                service.plan,
                service.projected,
                self.idle,
                self.witness,
                service.docker,
            )
            self._cleanup.append(self.reader.discard)
            self.qualifier = inputs.qualification.NativeIdleQualification(
                service.plan,
                self.idle,
                self.witness,
                service.docker,
                published=published,
                bridge_sha256=bridge_sha256,
                image_environment_sha256=image_environment_sha256,
                timezone=timezone,
                hostname=hostname,
                architecture=architecture,
                runtime_workers=runtime_workers,
            )
            self.objects = (
                service,
                self.coordinator,
                self.witness,
                self.record,
                self.idle,
                self.reader,
                self.qualifier,
                zero_domain,
            )
            self.profile = self.qualifier._pins()
            self.recheck()
            require(tuple(operator.base.encode(e) for e in service.journal.entries) == entries)
        except BaseException as error:
            self.close()
            self._fail(error)

    def _app_custody(self):
        require(not self.failed and not self.closed)
        require(self.owner == (os.getpid(), operator.get_ident()))
        owner, service = self.startup, self.service
        require(type(self) is AppIdleCandidate and type(service) is operator.IdleService)
        require(type(owner) is inputs.publication.startup.Startup)
        require(
            all(
                a is b
                for a, b in zip(
                    (owner, service, self.published, owner.clock),
                    self.app_objects,
                    strict=True,
                )
            )
        )
        require(owner.original is service.original and owner.projected is service.projected)
        require(owner.clock is service.clock_witness)
        require(type(self.published) is inputs.publication.NativePublished)
        inputs._service_custody(owner, service.plan)

    def recheck(self):
        try:
            self._app_custody()
            require(type(self.qualifier) is inputs.qualification.NativeIdleQualification)
            require(self.qualifier.published is self.published)
            require(self.qualifier.bridge_sha256 == self.bridge_sha256)
            # The original idle phase ends once launch inputs are published.
            # A later explicit phase must retain custody without repolling it.
            require(self.qualifier.native_launch_used is False)
            require(self.qualifier.native_launch_publication is None)
            require(self.qualifier.native_execution_used is False)
            require(self.qualifier.native_execution_owner is None)
            return super().recheck()
        except BaseException as error:
            self._fail(error)


def prepare_candidate(owner, service, **profile):
    """Consume the original service's one preparation slot, with no dispatch.

    This is explicit development assembly only, never selected by a command or
    a notice. A failure consumes the slot and preserves the original evidence;
    the running service still owns all successfully acquired cleanup callbacks.
    """
    require(type(service) is operator.IdleService)
    try:
        service._context()
        require(service.used and service.lock.locked() and not service.candidate_attempted)
        service.candidate_attempted = True
        candidate = AppIdleCandidate(owner, service, **profile)
        service._cleanup.append(candidate.close)
        service.candidate = service._original_candidate = candidate
        return candidate
    except BaseException as error:
        service._fail(error)


if __name__ == "__main__":
    raise SystemExit("Uninstalled App candidate readers only; no native launch enabled.")
