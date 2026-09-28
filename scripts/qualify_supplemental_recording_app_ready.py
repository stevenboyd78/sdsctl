#!/usr/bin/env python3
"""Read-only original App/native-ready join; no begin or service selection.

This is a distinct, uninstalled phase. The exact authenticated Ready object
permits observation of the four declared native socket leaves and original
guardian claim, not arbitrary output or an already started recording. Existing
action gates remain unchanged.
"""

from __future__ import annotations

import json
import os
import stat
import time

import supplemental_recording_app_launch as inputs

q = inputs.qualification
launch, publication, files = q.launch, inputs.publication, inputs.files
require = q.require
SOCKETS = ("api.sock", "events.sock", "pcmu.sock", "recordings.sock")


def _socket_inputs(fd, deadline, guard):
    guard(deadline)
    require(tuple(sorted(os.listdir(fd))) == SOCKETS)
    result = []
    for leaf in SOCKETS:
        info = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
        require(stat.S_ISSOCK(info.st_mode) and info.st_nlink == 1)
        require(stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == 0)
        require((info.st_uid, info.st_gid) == (publication.ROOT_UID, publication.ROOT_GID))
        result.append((leaf, files.identity(info)))
    require(tuple(sorted(os.listdir(fd))) == SOCKETS)
    for leaf, identity in result:
        require(files.identity(os.stat(leaf, dir_fd=fd, follow_symlinks=False)) == identity)
    guard(deadline)
    return tuple(result)


class NativeReadyQualification(inputs.NativeLaunchQualification):
    """Fresh inventories plus original readiness, never a serialized substitute.

    All launch/lease/baseline/App identities remain original. Only the socket
    directory metadata, exactly four private socket entries and the guardian's
    single claim can differ from the acknowledged launch phase. Their first
    qualified identities are then retained; replacement, extra output or any
    recording receipt is refused.
    No socket is opened or connected by this reader. It cannot prove recording
    success or authorize begin, and it never extends the original ready bound.
    """

    def __init__(self, original, ready):
        self.failed, self.elapsed_seconds = False, None
        self.native_active_used, self.native_active_owner = False, None
        claimed = None
        try:
            began = time.monotonic()
            require(type(original) is inputs.NativeLaunchQualification)
            holder = original.candidate
            require(type(holder) is q.NativeIdleQualification)
            require(holder.native_ready_used is False)
            holder.native_ready_used = True
            claimed = holder
            require(holder.native_ready_owner is None)
            holder.native_ready_owner = self
            require(type(ready) is launch.received.Ready)
            require(original.elapsed_seconds is not None and original.consumption is not None)
            self.prelaunch, self.ready = original, ready
            self.prelaunch_pins = original.original
            self.prelaunch_objects = (
                original,
                original.startup,
                original.candidate,
                original.launch_inputs,
                original.consumption,
            )
            self.ready_objects = (
                ready,
                ready.client,
                ready.processes,
                ready.clock,
                ready.zero_domain,
            )
            self.ready_pins = ready.context_raw, ready.ready_raw, ready.received_at, ready.ready_by
            profile = json.loads(original.launch_inputs.raw)["profile"]["sha256"]
            plan = original.plan
            self.expected = launch.engine.dispatch.Pins(
                launch.binding.Binding(
                    original.startup.projected,
                    plan.candidate_runtime.source,
                    plan.sha256,
                    plan.boot,
                ),
                launch.execution.Command(
                    str(plan.native_root / "launch/launch.json"),
                    original.launch_inputs.sha256,
                    plan.candidate_runtime.source,
                    plan.lease["ready_by"],
                ),
                original.generation,
                original.init,
            )
            self.expected_payload = self.expected.payload()
            self.expected_context = inputs.base.encode(
                launch.received._context(self.expected, profile)
            )
            super().__init__(original.startup, original.candidate, original.launch_inputs)
            ready.check_before_begin()
            require(time.monotonic() < min(began + self.MAX_SECONDS, ready.ready_by))
            # The ready-specific directory reader checks every unchanged input
            # against prelaunch before permitting this one new socket inventory.
            self.consumption = self._original_consumption = None
        except BaseException as error:
            if claimed is not None:
                claimed.failed, claimed.elapsed_seconds = True, None
            self._fail(error)

    def _binding(self):
        super()._binding()
        original, ready = self.prelaunch, self.ready
        require(type(original) is inputs.NativeLaunchQualification)
        require(not original.failed and not original.lock.locked())
        require(original.candidate.native_ready_used is True)
        require(original.candidate.native_ready_owner is self)
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        original,
                        original.startup,
                        original.candidate,
                        original.launch_inputs,
                        original.consumption,
                    ),
                    self.prelaunch_objects,
                    strict=True,
                )
            )
        )
        require(original._pins() == original.original == self.prelaunch_pins)
        require(self.startup is original.startup and self.candidate is original.candidate)
        require(self.launch_inputs is original.launch_inputs)
        require(type(ready) is launch.received.Ready)
        require(not ready.failed and not ready.closed)
        require(
            all(
                a is b
                for a, b in zip(
                    (ready, ready.client, ready.processes, ready.clock, ready.zero_domain),
                    self.ready_objects,
                    strict=True,
                )
            )
        )
        require(
            (ready.context_raw, ready.ready_raw, ready.received_at, ready.ready_by)
            == self.ready_pins
        )
        require(ready.clock is original.plan.original_clock)
        require(ready.ready_by == original.plan.lease["ready_by"])
        require(
            ready.watch_deadline
            == ready.ready_by + original.plan.candidate.contract.maximum_recording_seconds
        )
        require(ready.context_raw == self.expected_context)
        require(self.expected.payload() == self.expected_payload)
        require(ready.client.claim.pins.payload() == self.expected_payload)

    def _evidence(self):
        # Fresh actor/Engine/readiness checks bracket each complete collection;
        # immutable bindings above are still checked at every intervening guard.
        # Do not multiply expensive process-tree reads at every file boundary.
        self.ready.check_before_begin()
        return super()._evidence()

    def _directory_input(self, fd, name, deadline):
        if name != "sockets":
            return super()._directory_input(fd, name, deadline)
        return _socket_inputs(fd, deadline, self._guard)

    def _guardian_contents(self, fd, identity, deadline):
        """Join the sole claim to original authenticated Ready, not file authority.

        Source/runtime are independently checked around the whole collection.
        The claim's informational component hashes/counts are NOT a replacement
        source inventory. Only its original source pin participates in this join;
        all bytes and inode identities are retained after this one transition.
        """
        self._guard(deadline)
        require(os.listdir(fd) == ["launch-claimed.json"])
        raw, leaf = q._file(fd, "launch-claimed.json", deadline, limit=8192)
        decoder = inputs.native.protected.evidence
        value = json.loads(
            raw, object_pairs_hook=decoder.unique, parse_constant=decoder.reject_constant
        )
        require(type(value) is dict)
        evidence = value.get("source")
        require(
            type(evidence) is dict
            and set(evidence)
            == {"sha256", "runtime_sha256", "native_sha256", "file_count", "total_bytes"}
        )
        for field in ("sha256", "runtime_sha256", "native_sha256"):
            inputs.base.digest(evidence[field])
        require(evidence["sha256"] == self.plan.candidate_runtime.source)
        limits = (("file_count", files.MAX_FILES), ("total_bytes", files.MAX_TOTAL_BYTES))
        for field, limit in limits:
            require(type(evidence[field]) is int and 0 < evidence[field] <= limit)
        processes = self.ready.processes
        require(type(processes) is launch.engine.namespace.Witness)
        require(type(processes.actors) is tuple and len(processes.actors) == 4)
        guardian = processes.actors[1]
        require(type(guardian) is launch.engine.namespace.Actor)
        guardian.__post_init__()
        expected = dict(
            schema=1,
            kind="finite-recording-guardian-claim",
            context=json.loads(self.expected_context),
            source=evidence,
            guardian_pid=guardian.local_pid,
            guardian_start_ticks=guardian.start_ticks,
            hard_deadline=self.ready.watch_deadline,
            grace_seconds=launch.received.GRACE_SECONDS,
        )
        require(raw == inputs.base.encode(expected))
        require(os.listdir(fd) == ["launch-claimed.json"])
        self._guard(deadline)
        return raw.decode("ascii"), leaf

    def _additional_inputs(self, directory, deadline):
        observed = super()._additional_inputs(directory, deadline)
        original = {
            name: (identity, leaf) for name, identity, leaf in self.prelaunch.consumption[-1]
        }
        require(set(original) == set(self.DIRECTORIES))
        for name, identity, leaf in observed:
            previous, previous_leaf = original[name]
            if name == "sockets":
                require(identity[:6] == previous[:6] and previous_leaf is None)
            elif name == "launch":
                require(identity == previous and leaf[0] == previous_leaf[0])
                require(leaf[1][0][:6] == previous_leaf[1][0][:6])
                require(previous_leaf[1][1] is None)
            else:
                require((identity, leaf) == (previous, previous_leaf))
        return observed

    def _inputs(self, deadline):
        result = super()._inputs(deadline)
        require(self.consumption[:-1] == self.prelaunch.consumption[:-1])
        return result
