#!/usr/bin/env python3
"""Read-only live post-begin App qualification, not an installed/action policy.

Original Ready/socket/input custody crosses begin exactly once. Only the native
receipt prefix may advance. Full source/runtime checks and original stop limits
remain; no expired readiness poll, new baseline, returned success or recovery.
"""

from __future__ import annotations

import time

import qualify_supplemental_recording_app_ready as readiness
import supplemental_recording_host_begin as begin
import supplemental_recording_receipt_inventory as outputs

inputs, launch, require = readiness.inputs, readiness.launch, readiness.require


class NativeActiveQualification(inputs.NativeLaunchQualification, launch.RetainedQualification):
    """A distinct one-use handoff, borrowing actual Start/PostBegin evidence.

    MRO deliberately retains NativeLaunch's fixed input/bridge/source policy,
    with RetainedQualification's original stop-bound/process-evidence policy.
    Construction calls only RetainedQualification, never an idle constructor or
    a new readiness acquisition. Existing exact-type action gates do not admit
    this reader; a separately reviewed service/profile must explicitly select it.

    Socket/input identities remain fixed. At each source/runtime boundary the
    original receipt reader may observe an append-only prefix, but its disk
    contents never stand in for actual native returns. Native worker exit is not
    an active sample; independent exit/file/recovery paths remain separate.
    """

    def __init__(self, original, start, continuity):
        self.failed, self.elapsed_seconds = False, None
        claimed = None
        try:
            began = time.monotonic()
            require(type(original) is readiness.NativeReadyQualification)
            require(original.native_active_used is False)
            original.native_active_used = True
            claimed = original
            require(original.native_active_owner is None)
            original.native_active_owner = self
            require(not original.failed and original.elapsed_seconds is not None)
            require(original.consumption is not None)
            require(type(start) is begin.Start)
            require(type(continuity) is launch.idle_module.PostBegin)
            require(start.plan is original.plan and continuity.plan is original.plan)
            require(continuity.idle is original.idle)
            require(start.ready is original.ready and continuity.ready is original.ready)
            start.retained_history(require_live=True)
            relay = start.relay
            require(type(relay) is begin.relayed.Relay and relay.ready is original.ready)
            require(continuity.guard is relay.guard)
            require(relay.intent_at == start.intent.now)
            binding = relay.native_binding
            require(type(binding) is outputs.channel.Binding)
            require(binding.stored == original.startup.projected.native)
            require(binding.generation == original.generation)
            require(binding.projection_sha256 == original.plan.projection_sha256)
            require(binding.source_sha256 == original.plan.candidate_runtime.source)
            require(
                (binding.start_by, binding.finish_by)
                == (start.intent.start_by, start.intent.finish_by)
            )
            require(binding.start_by <= original.ready.ready_by)
            require(binding.finish_by <= continuity.finish_by)
            self.prebegin, self.start, self.continuity = original, start, continuity
            self.prebegin_objects = (
                original,
                original.ready,
                original.consumption,
                original.startup,
                original.candidate,
                original.launch_inputs,
            )
            self.prebegin_pins = original.original
            self.begin_objects = (
                start,
                start.run,
                relay,
                relay.guard,
                relay.native_binding,
                continuity,
            )
            self.begin_pins = (
                outputs.channel.encode(relay.native_binding.payload()),
                relay.intent_at,
            )
            # These are all original objects, not reconstructed profiles or
            # replacement NativeLaunch/Ready wrappers over old disk evidence.
            for name in (
                "startup",
                "candidate",
                "launch_inputs",
                "original_objects",
                "candidate_pins",
                "launch_pins",
            ):
                setattr(self, name, getattr(original, name))
            self._publication(original.plan, original.published, original.bridge_sha256)
            self.consumption = self._original_consumption = original.consumption
            self.receipt_directory = next(
                item[1] for item in original.consumption[-1] if item[0] == "receipts"
            )
            self.receipts = self._original_receipts = outputs.ReceiptInventory(
                relay.native_binding,
                intent_at=relay.intent_at,
                directory_identity=self.receipt_directory[:6],
            )
            launch.RetainedQualification.__init__(
                self,
                continuity,
                original.witness,
                original.docker,
                **{
                    name: getattr(original, name)
                    for name in inputs.qualification.AppRetainedQualification.PROFILE
                },
            )
            require(time.monotonic() < min(began + self.MAX_SECONDS, continuity.finish_by))
        except BaseException as error:
            if claimed is not None:
                claimed.failed, claimed.elapsed_seconds = True, None
            self._fail(error)

    def _binding(self):
        super()._binding()
        original, start = self.prebegin, self.start
        require(type(original) is readiness.NativeReadyQualification)
        require(not original.failed and not original.lock.locked())
        require(original.native_active_used is True and original.native_active_owner is self)
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        original,
                        original.ready,
                        original.consumption,
                        original.startup,
                        original.candidate,
                        original.launch_inputs,
                    ),
                    self.prebegin_objects,
                    strict=True,
                )
            )
        )
        require(original._pins() == original.original == self.prebegin_pins)
        require(self.consumption is original.consumption)
        require(type(start) is begin.Start and not start.closed and not start.failed)
        relay = start.relay
        require(type(relay) is begin.relayed.Relay)
        require(
            all(
                a is b
                for a, b in zip(
                    (start, start.run, relay, relay.guard, relay.native_binding, self.continuity),
                    self.begin_objects,
                    strict=True,
                )
            )
        )
        require(
            (outputs.channel.encode(relay.native_binding.payload()), relay.intent_at)
            == self.begin_pins
        )
        require(start.ready is original.ready and self.continuity.ready is original.ready)
        require(start.plan is original.plan and self.continuity.guard is relay.guard)
        require(self.receipts is self._original_receipts and not self.receipts.failed)
        require(self.receipts.binding is relay.native_binding)

    def _evidence(self):
        # Do not call Ready.check_before_begin/Idle.read after an actual begin.
        # Both complete collections still require original live worker handles
        # and original durable start/authorization history, not receipt files.
        self.start.retained_history(require_live=True)
        return super()._evidence()

    def _directory_input(self, fd, name, deadline):
        if name == "sockets":
            return readiness._socket_inputs(fd, deadline, self._guard)
        if name == "receipts":
            self.receipts.read(fd, deadline=deadline)
            return None
        return super()._directory_input(fd, name, deadline)

    def _additional_inputs(self, directory, deadline):
        observed = super()._additional_inputs(directory, deadline)
        normalized = []
        for name, identity, leaf in observed:
            if name == "receipts":
                require(identity[:6] == self.receipt_directory[:6] and leaf is None)
                require(self.receipts.inventory.directory == identity)
                # Only the validated append-only receipt prefix may advance
                # across the source/runtime bracket. It is not configuration,
                # source or an acknowledgment, so exclude its changing parent
                # timestamps from the IMMUTABLE input fingerprint. Every byte
                # previously observed is still rechecked by the same reader.
                identity = self.receipt_directory
            normalized.append((name, identity, leaf))
        return tuple(normalized)
