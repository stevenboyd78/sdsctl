#!/usr/bin/env python3
"""Trusted outer qualification ONLY for the action-free permission probe.

This is deliberately absent from the observed helper's closed source profile.
Original observer custody/setup/provenance must be independently established;
matching challenge bytes or a successful socket write are not that provenance.
No real service command, baseline read, acceptance or App action is authorized.
"""

from __future__ import annotations

import os

import qualify_supplemental_recording_preflight as preflight
import supplemental_recording_permission_probe as probe
import supplemental_recording_permission_review as reviews

launch, require = preflight.launch, preflight.require


class PermissionProbeQualification(preflight.PreflightProbeQualification):
    """Exact nine-argument command and separate closed 64-module inventory.

    baseline_sha256 is an independently supplied pin, not a file read or a
    permission to use the baseline. observer_identity is the actual current
    peer, not a numeric PID acquired from the target's message. Both are bound
    before any Engine read and checked through the full unchanged 2s qualifier.
    """

    def __init__(self, *args, baseline_sha256, observer_identity, **kwargs):
        self.permission_attempted = False
        try:
            require(type(baseline_sha256) is str)
            launch.base.digest(baseline_sha256)
            require(type(observer_identity) is probe.process.ProcessIdentity)
            require(observer_identity.pid == os.getpid())
            self.baseline_sha256, self.observer_identity = baseline_sha256, observer_identity
            self.permission_inputs = baseline_sha256, observer_identity
            self.observer_argument = probe.identity_argument(observer_identity)
            super().__init__(*args, **kwargs)
        except BaseException as error:
            self._fail(error)

    def _preflight_binding(self):
        require(
            all(
                a is b
                for a, b in zip(
                    (self.baseline_sha256, self.observer_identity),
                    self.permission_inputs,
                    strict=True,
                )
            )
        )
        require(probe.identity_argument(self.observer_identity) == self.observer_argument)
        require(self.observer_identity.pid == os.getpid())
        require(
            probe.process.read_identity(
                self.observer_identity.pid, self.observer_identity.container_id
            )
            == self.observer_identity
        )
        super()._preflight_binding()

    def _pins(self):
        return super()._pins() + (
            self.baseline_sha256,
            self.observer_identity,
            self.observer_argument,
        )

    def _command_policy(self, plan, command, zero_domain):
        require(zero_domain is self.domain)
        require(type(command) is tuple and all(type(part) is str for part in command))
        require(
            command
            == (
                "/usr/local/bin/python",
                "-I",
                "-B",
                probe.ENTRYPOINT,
                str(preflight.startup.declaration.declaration_root(plan.case)),
                self.template_sha256,
                self.baseline_sha256,
                self.observer_argument,
                "--permission-probe",
            )
        )

    def _source_layout(self, root):
        self._preflight_binding()
        return launch.helper_source.Layout(
            root / launch.plans.fixed.PACKAGE, root / self.HELPER, permission_probe=True
        )

    def review_and_qualify(self, review):
        """One-attempt Sender callback, no send or target action of its own."""
        try:
            require(not self.permission_attempted)
            self.permission_attempted = True
            require(type(review) is reviews.Review)
            require(review.template is self.template and review.timer is self.observer)
            require(review.domain is self.domain and review.target is self.witness)
            require(review.observer is self.observer_identity)
            require(review.template_sha256 == self.template_sha256)
            require(review.baseline_sha256 == self.baseline_sha256)
            self._preflight_binding()
            review.read()
            self()  # Complete exact-command/source/runtime/confinement checks.
            review.read()
            self._preflight_binding()
        except BaseException as error:
            self._fail(error)


if __name__ == "__main__":
    raise SystemExit("Uninstalled permission-probe verifier; no service or action enabled.")
