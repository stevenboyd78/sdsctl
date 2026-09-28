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
import supplemental_recording_peer_inputs as input_files
import supplemental_recording_service_command as command
import supplemental_recording_service_runtime_expectations as declarations

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


class PeerWriterPreflightQualification(preceding.PermissionProbeQualification):
    """Original outer's full read-only check before the writer owns a plan.

    The retained independently pinned Inputs select ONLY the fixed passive
    preparation command and 104-module graph. The inherited temporary plan
    belongs to the outer's original clock, never the later accepted writer.
    Neither this comparison nor preflight permission admits an App action,
    qualifies the other peer/outer or replaces final paired qualification.
    """

    def __init__(
        self,
        inputs,
        observer_identity,
        observer,
        domain,
        witness,
        docker,
        *,
        baseline_sha256,
        generation,
        command,
        runtime_workers=1,
    ):
        try:
            require(type(self) is PeerWriterPreflightQualification)
            require(type(inputs) is input_files.Inputs)
            require(type(observer) is launch.plans.clock.ClockWitness)
            end = min(
                launch.time.monotonic() + self.MAX_SECONDS,
                observer.original.after_ns / launch.plans.clock.NS
                + preceding.probe.permission.WAIT_SECONDS
                - preceding.probe.permission.IO_SECONDS,
            )
            expected = inputs.recheck(deadline=end)
            self.inputs, self.expectations = inputs, expected
            self.expectations_raw, self.expectations_sha256 = expected.raw, inputs.expected
            self.source = declarations.source_profile(expected, preparation=True)
            self.input_objects = (
                inputs,
                inputs.declaration,
                inputs.template,
                expected,
                self.expectations_raw,
                self.expectations_sha256,
                self.source,
            )
            selected = declarations._read(expected.raw)["writer"]
            super().__init__(
                inputs.template,
                inputs.template.sha256,
                observer,
                domain,
                witness,
                docker,
                baseline_sha256=baseline_sha256,
                observer_identity=observer_identity,
                generation=generation,
                command=command,
                runtime_workers=runtime_workers,
                **{
                    key: selected[key]
                    for key in declarations.ROLE_FIELDS
                    if key not in {"runtime", "command_sha256"}
                },
            )
            self._guard(end)  # Initial private input reads share the original 2s bound.
        except BaseException as error:
            self._fail(error)

    def _input_binding(self):
        require(type(self) is PeerWriterPreflightQualification)
        require(
            all(
                current is original
                for current, original in zip(
                    (
                        self.inputs,
                        self.inputs.declaration,
                        self.inputs.template,
                        self.expectations,
                        self.expectations_raw,
                        self.expectations_sha256,
                        self.source,
                    ),
                    self.input_objects,
                    strict=True,
                )
            )
        )
        require(self.inputs.template is self.template)
        require(self.inputs.expectations is self.expectations)
        require(self.inputs.expected is self.expectations_sha256)
        require(
            type(self.expectations.raw) is bytes and self.expectations.raw == self.expectations_raw
        )
        require(not self.inputs.closed and not self.inputs.failed)

    def _preflight_binding(self):
        self._input_binding()
        super()._preflight_binding()

    def _guard(self, deadline):
        self._input_binding()
        end = min(deadline, self.cutoff)
        self.inputs._paths(end)
        super()._guard(end)
        self.inputs._paths(end)

    def _collect_before(self, outer_deadline):
        # Full canonical reads bracket the complete collection. Intermediate
        # guards retain/check the same private descriptors and paths; repeatedly
        # decoding identical immutable templates at every proc syscall would
        # spend the finite budget on redundant parsing, not additional evidence.
        try:
            began = launch.time.monotonic()
            end = min(began + self.MAX_SECONDS, self.cutoff)
            if outer_deadline is not None:
                launch.base.clock(outer_deadline)
                end = min(end, outer_deadline)
            self._guard(end)
            require(self.inputs.recheck(deadline=end) is self.expectations)
            super()._collect_before(end)
            require(self.inputs.recheck(deadline=end) is self.expectations)
            self._guard(end)
            self.elapsed_seconds = launch.time.monotonic() - began
            require(0 <= self.elapsed_seconds < self.MAX_SECONDS)
        except BaseException as error:
            self._fail(error)

    def _pins(self):
        return super()._pins() + (self.expectations_raw, self.expectations_sha256)

    def _command_policy(self, plan, command, zero_domain):
        self._input_binding()
        require(zero_domain is self.domain)
        require(type(command) is tuple and all(type(part) is str for part in command))
        require(
            command
            == (
                "/usr/local/bin/python",
                "-I",
                "-B",
                "/opt/sdsctl-recording-host/supplemental_recording_peer_preparation.py",
                plan.case,
                self.template_sha256,
                self.baseline_sha256,
                self.observer_argument,
                "--prepare-idle-peer-writer",
            )
        )
        self.expectations.check_command("writer", command)

    def _source_layout(self, root):
        self._preflight_binding()
        return self.source.Layout(root / launch.plans.fixed.PACKAGE, root / self.HELPER)

    def review_and_qualify(self, review):
        # Input rechecks and the post-review check belong to this same bounded
        # attempt, not an extra window after the inherited full collection.
        try:
            end = min(launch.time.monotonic() + self.MAX_SECONDS, self.cutoff)
            require(not self.permission_attempted)
            self.permission_attempted = True
            require(type(review) is preceding.reviews.Review)
            require(review.template is self.template and review.timer is self.observer)
            require(review.domain is self.domain and review.target is self.witness)
            require(review.observer is self.observer_identity)
            require(review.template_sha256 == self.template_sha256)
            require(review.baseline_sha256 == self.baseline_sha256)
            end = min(end, review.cutoff)
            self._guard(end)
            review.read()
            # Pass the SAME callback deadline into the full inherited
            # collector, not merely check lateness after a fresh 2s collection.
            self._collect_before(end)
            review.read()
            require(self.inputs.recheck(deadline=end) is self.expectations)
            self._guard(end)
        except BaseException as error:
            self._fail(error)


if __name__ == "__main__":
    raise SystemExit(
        "Uninstalled finite preparation verifier; no permission or acceptance submitted."
    )
