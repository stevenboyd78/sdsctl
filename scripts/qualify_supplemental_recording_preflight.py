#!/usr/bin/env python3
"""Template-only trusted-observer qualification of the ACTION-FREE startup probe.

This separate outer adapter requires no service publication or service-origin
clock. It derives a temporary observer-domain plan for read-only qualification,
not a plan to publish, accept, journal or use as a continuing service lease.
No command, helper graph, permission, process, file or App action is enabled.
The observer's independent provenance remains a separate prerequisite.
"""

from __future__ import annotations

import supplemental_recording_host_launch as launch
import supplemental_recording_service_startup as startup

require = launch.require


class PreflightProbeQualification(launch.HelperQualification):
    """Full original two-second checks before requiring a service-owned plan.

    Only the already action-free --startup-probe command is recognized. The
    caller supplies an independently pinned template, its OWN continuing clock,
    and live original ZeroDomain/process evidence. A temporary plan is derived
    in that observer's actual namespace and remains private to this verifier.
    It is never a claim that the target captured or accepted this clock.

    The short original observer window cannot be renewed by repeating this
    verifier. This does not authorize preflight host/cache reads, a service
    command, acceptance, readiness, recording or restoration. A future command
    needs its own explicit policy and separately reviewed authorization gate.
    """

    def __init__(self, template, template_sha256, observer, domain, witness, docker, **kwargs):
        try:
            require(type(template) is startup.declaration.codec.Template)
            require(type(template_sha256) is str)
            launch.base.digest(template_sha256)
            require(template.sha256 == template_sha256)
            require(type(observer) is launch.plans.clock.ClockWitness)
            require(type(domain) is launch.time_domain.ZeroDomain)
            require("zero_domain" not in kwargs)
            self.template, self.template_sha256 = template, template_sha256
            self.observer, self.domain = observer, domain
            self.template_raw, self.origin = template.raw, observer.original
            self.preflight_objects = (
                template,
                template_sha256,
                observer,
                domain,
                self.template_raw,
                self.origin,
            )
            plan = template.preview(self.origin)
            self.cutoff = (
                min(
                    plan.lease["ready_by"],
                    self.origin.after_ns / launch.plans.clock.NS + startup.offers.MAX_OFFER_SECONDS,
                )
                - startup.acceptance.MAX_SECONDS
            )
            super().__init__(plan, witness, docker, zero_domain=domain, **kwargs)
        except BaseException as error:
            self._fail(error)

    def _preflight_binding(self):
        require(
            all(
                current is original
                for current, original in zip(
                    (
                        self.template,
                        self.template_sha256,
                        self.observer,
                        self.domain,
                        self.template_raw,
                        self.origin,
                    ),
                    self.preflight_objects,
                    strict=True,
                )
            )
        )
        require(type(self.template.raw) is bytes and self.template.raw == self.template_raw)
        require(not self.observer.closed and not self.observer.failed)
        require(self.observer.original is self.origin)
        require(not self.domain.closed and not self.domain.failed)
        require(self.domain is self.zero_domain)
        # Recheck the borrowed proof and target after the final returned clock
        # sample too. A callback may retire either without poisoning the other
        # object's flags; the preceding base guard alone would not see that.
        proof = self.domain.evidence
        require(type(proof) is launch.time_domain.Evidence)
        require(proof.sha256 == self.domain_sha256)
        require(proof.init == self.init and proof.original_clock == self.origin)
        require(not self.witness.exited())
        require(launch.time.monotonic() < self.cutoff)

    def _command_policy(self, plan, command, zero_domain):
        require(zero_domain is self.domain)
        require(type(command) is tuple and all(type(part) is str for part in command))
        require(
            command
            == (
                "/usr/local/bin/python",
                "-I",
                "-B",
                "/opt/sdsctl-recording-host/supplemental_recording_service_startup.py",
                str(startup.declaration.declaration_root(plan.case)),
                self.template_sha256,
                "--startup-probe",
            )
        )

    def _pins(self):
        return super()._pins() + (self.template_raw, self.template_sha256, self.cutoff)

    def _clock_sample(self):
        self._preflight_binding()
        observed = self.observer.read()
        self.plan.check_clock(observed)  # Strict same OBSERVER-domain check.
        self._preflight_binding()
        return observed

    def _source_layout(self, root):
        self._preflight_binding()
        return launch.helper_source.Layout(
            root / launch.plans.fixed.PACKAGE, root / self.HELPER, startup=True
        )

    def _guard(self, deadline):
        self._preflight_binding()
        super()._guard(min(deadline, self.cutoff))
        self._preflight_binding()


if __name__ == "__main__":
    raise SystemExit("Uninstalled template-only probe verifier; no service or action enabled.")
