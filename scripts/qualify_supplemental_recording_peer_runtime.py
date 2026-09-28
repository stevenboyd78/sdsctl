#!/usr/bin/env python3
"""Uninstalled, read-only runtime comparisons for original service peers.

This outer verifier is not in any command-selected helper profile and starts nothing.
It checks either declared role without rewriting the original plan's helper pin
to impersonate its partner. Both peers still need independent original handles,
this full collection, installed provenance, continuing custody and supervision.
An argv match proves only the independently expected runtime command, not review
or admission of an active entrypoint. No existing command selects this adapter.
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock

import supplemental_recording_host_launch as launch
import supplemental_recording_service_runtime_expectations as declarations

require = launch.require


class PeerRuntimeQualification(launch.HelperQualification):
    """Full original two-second runtime/confinement collector, separately pinned.

    The declaration and its independently authenticated digest predate peer
    observation. This is NOT an action-scope grant or a continuing-observer
    readiness protocol. A selected role's successful comparison cannot qualify
    its partner, even when both expected images happen to be identical.

    As with the ordinary collector, the external caller must already qualify
    its own proc/runtime/domain and bound blocked I/O independently. No elapsed
    time check claims to terminate a frozen or blocked process.
    """

    def __init__(
        self,
        plan,
        witness,
        docker,
        *,
        template,
        expectations,
        expectations_sha256,
        role,
        generation,
        command,
        runtime_workers=1,
    ):
        try:
            require(type(self) is PeerRuntimeQualification)
            require(type(expectations) is declarations.Expectations)
            require(type(template) is declarations.templates.Template)
            require(type(role) is str and role in declarations.ROLES)
            require(type(witness) is launch.engine.dispatch.process.ProcessWitness)
            require(witness.identity.pid != launch.os.getpid())
            declarations.load_bytes(expectations.raw, expectations_sha256)
            expectations.check_plan(template, plan, plan.original_clock)
            selected = declarations._read(expectations.raw)[role]
            self.template, self.expectations, self.role = template, expectations, role
            self.expectations_sha256 = expectations_sha256
            self.template_raw, self.expectations_raw = template.raw, expectations.raw
            self.role_runtime = launch.plans.RuntimePin(**selected["runtime"])
            self.role_objects = (
                template,
                expectations,
                role,
                expectations_sha256,
                self.template_raw,
                self.expectations_raw,
                self.role_runtime,
            )
            super().__init__(
                plan,
                witness,
                docker,
                generation=generation,
                command=command,
                runtime_workers=runtime_workers,
                **{
                    key: selected[key]
                    for key in declarations.ROLE_FIELDS
                    if key not in {"runtime", "command_sha256"}
                },
            )
        except BaseException as error:
            self._fail(error)

    def _role_binding(self):
        require(type(self) is PeerRuntimeQualification)
        require(
            all(
                current is original
                for current, original in zip(
                    (
                        self.template,
                        self.expectations,
                        self.role,
                        self.expectations_sha256,
                        self.template_raw,
                        self.expectations_raw,
                        self.role_runtime,
                    ),
                    self.role_objects,
                    strict=True,
                )
            )
        )
        # Full canonical validation and template/plan join occurred at capture;
        # unchanged immutable bytes are checked without repeated JSON parsing at
        # every syscall. The inherited original PinnedPlan still checks all its
        # nested fields and retains the caller's plan object unchanged.
        require(type(self.template.raw) is bytes and self.template.raw == self.template_raw)
        require(
            type(self.expectations.raw) is bytes and self.expectations.raw == self.expectations_raw
        )

    def _runtime_expectation(self, plan):
        self._role_binding()
        return self.role_runtime

    def _container_name(self, plan):
        self._role_binding()
        return (
            "sdsctl-recording-handoff-"
            + plan.case
            + ("-observer" if self.role == "observer" else "")
        )

    def _pins(self):
        return super()._pins() + (
            self.template_raw,
            self.expectations_raw,
            self.expectations_sha256,
            self.role,
        )

    def _command_policy(self, plan, command, zero_domain):
        self._role_binding()
        require(zero_domain is None)
        require(type(command) is tuple and len(command) >= 4)
        require(command[:3] == ("/usr/local/bin/python", "-I", "-B"))
        require(
            command[3]
            in {str(Path("/") / self.HELPER / name) for name in declarations.source.HELPER_FILES}
        )
        self.expectations.check_command(self.role, command)

    def _source_layout(self, root):
        self._role_binding()
        return declarations.source.Layout(root / launch.plans.fixed.PACKAGE, root / self.HELPER)


class PeerRuntimePair:
    """Fresh read-only checks of BOTH original collectors in one shared window.

    This retains separately acquired original container-init witnesses and one
    original plan/template/expectations/Engine client. One role can never stand
    in for both, even with identical image bytes. Every call performs both full
    collections, with one unchanged two-second deadline; earlier successes are
    not reusable results. This is not an atomic filesystem snapshot, live peer
    communication, entrypoint/action admission or independently enforced process
    supervision. Outer image provenance and continuing custody remain required.
    """

    MAX_SECONDS = 2.0

    def __init__(self, writer, observer):
        self.owner, self.lock = (launch.os.getpid(), launch.get_ident()), Lock()
        self.failed, self.elapsed_seconds = False, None
        # An explicitly selected outer supervisor may retain these original
        # peers once, before any action. Comparison itself still signals none.
        self.termination_capture_attempted = False
        self.channel_delivery_attempted = False
        try:
            require(type(self) is PeerRuntimePair)
            require(
                type(writer) is PeerRuntimeQualification
                and type(observer) is PeerRuntimeQualification
            )
            require(writer is not observer and (writer.role, observer.role) == declarations.ROLES)
            require(writer.plan is observer.plan and writer.template is observer.template)
            require(
                writer.expectations is observer.expectations and writer.docker is observer.docker
            )
            require(writer.expectations_sha256 == observer.expectations_sha256)
            require(writer.witness is not observer.witness)
            require(
                writer.init.pid != observer.init.pid
                and writer.init.container_id != observer.init.container_id
            )
            self.writer, self.observer = writer, observer
            self.originals = writer, observer, writer.original, observer.original
            self._guard(
                min(launch.time.monotonic() + self.MAX_SECONDS, writer.plan.lease["ready_by"])
            )
        except BaseException as error:
            self._fail(error)

    def _guard(self, deadline):
        require(type(self) is PeerRuntimePair and not self.failed)
        require(self.owner == (launch.os.getpid(), launch.get_ident()))
        require(
            all(
                current is original
                for current, original in zip(
                    (self.writer, self.observer, self.writer.original, self.observer.original),
                    self.originals,
                    strict=True,
                )
            )
        )
        require((self.writer.role, self.observer.role) == declarations.ROLES)
        self.writer._guard(deadline)
        self.observer._guard(deadline)

    def __call__(self):
        return self._collect_before(None)

    def _collect_before(self, outer_deadline):
        acquired = False
        self.elapsed_seconds = None
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            began = launch.time.monotonic()
            deadline = min(began + self.MAX_SECONDS, self.writer.plan.lease["ready_by"])
            if outer_deadline is not None:
                require(type(outer_deadline) in (int, float))
                require(launch.math.isfinite(outer_deadline))
                deadline = min(deadline, outer_deadline)
            self._guard(deadline)
            self.writer._collect_before(deadline)
            self._guard(deadline)
            self.observer._collect_before(deadline)
            self._guard(deadline)
            self.elapsed_seconds = launch.time.monotonic() - began
            require(0 <= self.elapsed_seconds < self.MAX_SECONDS)
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed, self.elapsed_seconds = True, None
        if not isinstance(error, Exception):
            raise error
        raise launch.UnconfirmedHostLaunch(launch.MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Uninstalled peer runtime comparison only; no peer or App launch enabled.")
