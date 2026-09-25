#!/usr/bin/env python3
"""Independent read-only policies for the finite PASSIVE preparation command.

The preflight and final-plan checks are distinct; neither sends permission,
acceptance or an action request. Both preserve the full two-second qualifier.
They are outside the observed helper inventory. Actual input/observer/source
provenance and independent supervision are prerequisites, not self-attestation.
"""

from __future__ import annotations

import os
from threading import get_ident

import qualify_supplemental_recording_permission as preceding
import qualify_supplemental_recording_startup as final
import supplemental_recording_service_command as command

launch, require = preceding.launch, preceding.require


def _command(case, template_sha256, baseline_sha256, observer_argument):
    return (
        "/usr/local/bin/python",
        "-I",
        "-B",
        command.ENTRYPOINT,
        str(command.peer.declaration.declaration_root(case)),
        template_sha256,
        baseline_sha256,
        observer_argument,
        command.MODE,
    )


class ServicePreflightQualification(preceding.PermissionProbeQualification):
    """Before baseline/host reads: only this exact command and 65-file profile.

    The inherited one-attempt Review callback checks original peer/template/
    baseline/clock/domain pins. Unlike the permission probe, this policy permits
    guarded baseline/host preparation, NOT final acceptance or a service run.
    """

    def _command_policy(self, plan, supplied, zero_domain):
        require(zero_domain is self.domain)
        require(type(supplied) is tuple and all(type(part) is str for part in supplied))
        require(
            supplied
            == _command(
                plan.case, self.template_sha256, self.baseline_sha256, self.observer_argument
            )
        )

    def _source_layout(self, root):
        self._preflight_binding()
        return launch.helper_source.Layout(
            root / launch.plans.fixed.PACKAGE, root / self.HELPER, service_preparation=True
        )


class ServicePreparationQualification(final.StartupQualification):
    """Final-plan check borrowing the SAME successful preflight's originals.

    This does not reuse the temporary observer plan as the service plan, rerun
    preflight permission or extend its cutoff. The later service-origin plan is
    separately checked in full, within its original offer. No acceptance is
    submitted here, and matching source does not authenticate installed inputs.
    """

    def __init__(self, plan, preflight, clock_link):
        try:
            require(type(preflight) is ServicePreflightQualification)
            require(type(clock_link) is final.clocks.ObserverClock)
            self.preflight, self._preflight = preflight, preflight
            self.preflight_pins = preflight.original
            self._preflight_completed()
            require(clock_link.observer is preflight.observer)
            require(clock_link.domain is preflight.domain)
            require(clock_link.target is preflight.init)
            require(plan.original_clock.before_ns > preflight.origin.after_ns)
            super().__init__(
                plan,
                preflight.witness,
                preflight.docker,
                template=preflight.template,
                template_sha256=preflight.template_sha256,
                clock_link=clock_link,
                **{
                    key: getattr(preflight, key)
                    for key in (
                        "generation",
                        "command",
                        "configuration_sha256",
                        "image_environment_sha256",
                        "timezone",
                        "hostname",
                        "architecture",
                        "runtime_workers",
                    )
                },
            )
        except BaseException as error:
            self._fail(error)

    def _preflight_completed(self):
        p = self.preflight
        require(p is self._preflight and type(p) is ServicePreflightQualification)
        require(p.owner == (os.getpid(), get_ident()))
        require(not p.failed and p.permission_attempted)
        require(type(p.elapsed_seconds) is float and 0 <= p.elapsed_seconds < p.MAX_SECONDS)
        require(p.original is self.preflight_pins and p._pins() == self.preflight_pins)
        # Check custody without calling _preflight_binding(), whose OLD wait
        # cutoff must not be renewed or confused with this new service offer.
        for current, originals in (
            ((p.plan, p.witness, p.docker, p.zero_domain), p.objects),
            (
                (p.template, p.template_sha256, p.observer, p.domain, p.template_raw, p.origin),
                p.preflight_objects,
            ),
            ((p.baseline_sha256, p.observer_identity), p.permission_inputs),
        ):
            require(all(a is b for a, b in zip(current, originals, strict=True)))
        require(p.domain is p.zero_domain and p.origin is p.observer.original)
        require(p.observer_identity.pid == os.getpid())
        require(
            command.peer.process.read_identity(
                p.observer_identity.pid, p.observer_identity.container_id
            )
            == p.observer_identity
        )

    def _startup_binding(self):
        self._preflight_completed()
        p = self.preflight
        require(
            self.template is p.template and self.witness is p.witness and self.docker is p.docker
        )
        require(self.clock_link.observer is p.observer and self.clock_link.domain is p.domain)
        require(self.clock_link.target is p.init)
        super()._startup_binding()

    def _command_policy(self, plan, supplied, zero_domain):
        require(zero_domain is None)
        p = self.preflight
        require(type(supplied) is tuple and all(type(part) is str for part in supplied))
        require(
            supplied
            == _command(plan.case, self.template_sha256, p.baseline_sha256, p.observer_argument)
        )

    def _pins(self):
        return super()._pins() + (self.preflight_pins,)

    def _source_layout(self, root):
        self._startup_binding()
        return launch.helper_source.Layout(
            root / launch.plans.fixed.PACKAGE, root / self.HELPER, service_preparation=True
        )


if __name__ == "__main__":
    raise SystemExit(
        "Uninstalled finite preparation verifier; no permission or acceptance submitted."
    )
