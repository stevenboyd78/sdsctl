#!/usr/bin/env python3
"""Explicit trusted-observer qualification for the action-free startup probe.

Not installed and never selected by an existing helper command. This outer
adapter borrows original observer/domain/process custody and authenticates a
service-owned plan against an independently supplied clock-free declaration.
It does not start a process, submit acceptance, write a file or grant authority.
The observer's own code/runtime/proc provenance remains a separate prerequisite.
"""

from __future__ import annotations

import supplemental_recording_host_launch as launch
import supplemental_recording_service_clock_link as clocks
import supplemental_recording_service_startup as startup

require = launch.require


class StartupQualification(launch.HelperQualification):
    """Same full read-only confinement/source/runtime checks, distinct policy.

    Only the fixed seven-argument --startup-probe command is supported. Its
    argument pins the independently reviewed template, not the final plan or
    a hash discovered from the observed process. All non-clock plan fields and
    original budgets must match that template. Only the explicit 62-module
    source profile is selected; ordinary HelperQualification still requires54.

    The exact caller-owned ObserverClock supplies a sample in the OBSERVER
    namespace. Live original zero-domain proof explicitly compares it with the
    SERVICE-owned plan; no Window is relabeled or passed to strict same-domain
    Plan.check_clock. Source/runtime/command/mount/proc checks, two-second bound,
    original pidfd, immutable configuration and kernel/environment checks remain.
    There is no acceptance/readiness/recording/restore claim or new lease.
    """

    def __init__(self, plan, witness, docker, *, template, template_sha256, clock_link, **kwargs):
        try:
            require(type(template) is startup.declaration.codec.Template)
            require(type(template_sha256) is str)
            launch.base.digest(template_sha256)
            require(template.sha256 == template_sha256)
            template.check_plan(plan, plan.original_clock)
            require(type(clock_link) is clocks.ObserverClock)
            require("zero_domain" not in kwargs)
            self.template, self.template_sha256, self.clock_link = (
                template,
                template_sha256,
                clock_link,
            )
            self.template_raw = template.raw
            self.startup_objects = template, template_sha256, clock_link, self.template_raw
            super().__init__(plan, witness, docker, **kwargs)
        except BaseException as error:
            self._fail(error)

    def _startup_binding(self):
        require(
            all(
                current is original
                for current, original in zip(
                    (self.template, self.template_sha256, self.clock_link, self.template_raw),
                    self.startup_objects,
                    strict=True,
                )
            )
        )
        require(type(self.template) is startup.declaration.codec.Template)
        # Exact immutable bytes were fully decoded, independently pinned and
        # matched to the entire plan in construction. Compare those same bytes
        # here, rather than re-decoding an unchanged template at every syscall
        # guard. Base PinnedPlan and ObserverClock still recheck the final plan.
        require(type(self.template.raw) is bytes and self.template.raw == self.template_raw)
        require(type(self.clock_link) is clocks.ObserverClock)
        require(not self.clock_link.closed and not self.clock_link.failed)
        require(self.clock_link.plan is self.plan and self.clock_link.target is self.init)
        require(self.zero_domain is None)
        # This adapter is for the short observation command only. It cannot
        # qualify a late retained process as an installed long-running service.
        end = (
            min(
                self.plan.lease["ready_by"],
                self.plan.original_clock.after_ns / launch.plans.clock.NS
                + startup.offers.MAX_OFFER_SECONDS,
            )
            - startup.acceptance.MAX_SECONDS
        )
        require(launch.time.monotonic() < end)
        # Bracket the returned sample with original pure object/state checks.
        # A final-read callback cannot hide a retired borrowed observer/domain
        # behind a link whose own flags have not changed.
        self.clock_link._binding(end)
        require(self.clock_link.proof is self.clock_link.domain.evidence)
        require(self.clock_link.proof.sha256 == self.clock_link.proof_sha256)
        require(not self.witness.exited())

    def _command_policy(self, plan, command, zero_domain):
        require(zero_domain is None)
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
        return super()._pins() + (self.template_raw, self.template_sha256)

    def _clock_sample(self):
        self._startup_binding()
        observed = self.clock_link.read()
        self._startup_binding()
        return observed

    def _source_layout(self, root):
        self._startup_binding()
        return launch.helper_source.Layout(
            root / launch.plans.fixed.PACKAGE, root / self.HELPER, startup=True
        )

    def _guard(self, deadline):
        self._startup_binding()
        super()._guard(deadline)
        self._startup_binding()


if __name__ == "__main__":
    raise SystemExit("Uninstalled read-only observer adapter; no launch or service enabled.")
