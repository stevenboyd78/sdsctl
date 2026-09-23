#!/usr/bin/env python3
"""Original confirmed host launch -> durable authorization -> one Relay begin.

Uninstalled adapter. It consumes actual in-process Launch/Ledger objects, not
replayed readiness, supplied authorization hashes or renewed deadlines. Active
polling, return/checkpoint verification, independent exits and supervised host
recovery remain separate obligations. No installed service invokes this class.
"""

from __future__ import annotations

import hashlib
import math
import os
import time
from contextlib import suppress
from dataclasses import asdict, replace
from threading import Lock, get_ident

import supplemental_recording_host_launch as launch
import supplemental_recording_relay as relayed

plans, base, binding = launch.plans, launch.base, launch.binding
MESSAGE = "Recording host begin is unconfirmed; retain the original case and do not retry."


class UnconfirmedHostBegin(ValueError):
    """Neither durable permission nor a sent frame is a successful recording."""


def require(value):
    if not value:
        raise UnconfirmedHostBegin(MESSAGE)


class Start:
    """One pre-begin join; construction performs only original evidence checks.

    Requires the actual full BootstrapHost and CandidateQualification, with a
    new passive probe inside their fresh source/runtime bracket. Durable policy
    authorization precedes the original Ledger intent; only its returned fsync
    permits construction of the existing Relay (which sends begin exactly once).
    Failure closes transport but retains all original/probe process descriptors.
    close() releases only this object's new probe; Launch still owns its actors.
    """

    def __init__(self, run, ledger):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.used = self.failed = self.closed = False
        self.probe = self.authorization = self.intent = self.relay = None
        self._begin_result = None
        self.run = None
        try:
            require(type(run) is launch.Launch and type(ledger) is binding.Ledger)
            require(run.used and run.confirm_attempted and not run.failed and not run.closed)
            require(type(run.ready) is launch.received.Ready)
            require(type(run.probe) is launch.probe_exec.Sample and run.probe.ready is run.ready)
            require(type(run.read) is launch.BootstrapHost)
            require(type(run.qualify) is launch.CandidateQualification)
            require(run.read.docker is run.qualify.docker)
            require(ledger.directory == run.plan.root / "recording-ledger")
            require(ledger.binding == run.pins.host)
            self.run, self.ledger = run, ledger
            self.plan, self.ready = run.plan, run.ready
            self.objects = (
                run.plan,
                run.projected,
                run.journal,
                run.ready,
                run.read,
                run.qualify,
                run.idle,
                run.witness,
                run.client,
                run.probe,
                ledger,
            )
            self.original = (
                self.plan.raw,
                run.pins,
                run.command,
                run.launch_sha256,
                run.profile_sha256,
                self.ready.ready_raw,
                self.ready.context_raw,
                self.ready.received_at,
                self.ready.ready_by,
                self.ready.watch_deadline,
                ledger.directory,
                ledger.binding,
                ledger._directory_identity,
            )
            self.history = tuple(base.encode(entry) for entry in run.journal.entries)
            self.prepared = ledger.state
            self.proof = self._ready_proof()
            self._guard()
            self._ledger()
        except BaseException as error:
            self._fail(error)

    def _ready_proof(self):
        run = self.run
        entries = [
            e["event"] for e in run.journal.entries if e["event"]["kind"] == "operator_ready"
        ]
        require(len(entries) == 1)
        event = entries[0]
        observation = launch.bootstrap.recording.decode_observation(event["observation"])
        candidate = observation.candidate
        native = plans.ordinary.NativeState(
            candidate.generation, candidate.healthy, candidate.recording
        )
        run._check_native(native)
        proof = base.checksum(
            dict(
                ready_sha256=hashlib.sha256(self.ready.ready_raw).hexdigest(),
                probe_execution_id=run.probe.execution_id,
                probe_request_sha256=run.probe.request_sha256,
                native=asdict(native),
                observation=asdict(observation),
            )
        )
        require(event["ready_evidence_sha256"] == proof)
        require(event["generation"] == run.pins.generation)
        require(event["intent_sha256"] == run.action.intent_sha256)
        return proof

    def _identity(self):
        """Original object/plan bindings only; deliberately no phase authority."""
        require(not self.failed and not self.closed and self.owner == (os.getpid(), get_ident()))
        run, plan, ready, ledger = self.run, self.plan, self.ready, self.ledger
        current = (
            run.plan,
            run.projected,
            run.journal,
            run.ready,
            run.read,
            run.qualify,
            run.idle,
            run.witness,
            run.client,
            run.probe,
            ledger,
        )
        require(all(a is b for a, b in zip(current, self.objects, strict=True)))
        require(plan is run.plan and ready is run.ready and ready.client is run.client)
        require(ready.clock is plan.original_clock and ready.zero_domain is run.idle.zero_domain)
        require(ready.ready_by == run.command.ready_by)
        require(
            ready.watch_deadline
            == run.command.ready_by + plan.candidate.contract.maximum_recording_seconds
        )
        require(
            ready.context_raw
            == binding.encode(launch.received._context(run.pins, run.profile_sha256))
        )
        require(not run.failed and not run.closed and run.used and run.confirm_attempted)
        require(type(plan) is plans.Plan and plans.load_bytes(plan.raw, plan.sha256) == plan)
        plan.check_projection(run.projected)
        require(
            (
                plan.raw,
                run.pins,
                run.command,
                run.launch_sha256,
                run.profile_sha256,
                ready.ready_raw,
                ready.context_raw,
                ready.received_at,
                ready.ready_by,
                ready.watch_deadline,
                ledger.directory,
                ledger.binding,
                ledger._directory_identity,
            )
            == self.original
        )
        require(run._candidate_qualifier() is run.qualify)
        require(run.read.docker is run.qualify.docker)
        require(run.client.claim.pins == run.pins and run.pins.host == ledger.binding)
        require(run.witness.identity == run.idle.init == run.pins.init and not run.witness.exited())
        return run, plan, ready, ledger

    def _guard(self):
        run, plan, ready, _ = self._identity()
        ready.check_before_begin()
        machine = run._history()
        state = machine.state
        require(
            tuple(base.encode(e) for e in run.journal.entries[: len(self.history)]) == self.history
        )
        require(len(run.journal.entries) == len(self.history) + (self.authorization is not None))
        require(state.phase == "candidate_running" and not state.finish_requested)
        require(state.operator_exit_sha256 is None and state.ready_evidence_sha256 == self.proof)
        require(state.launch_intent_sha256 == run.action.intent_sha256)
        require(state.launch_plan_sha256 == run.command.plan_sha256)
        require(state.candidate_generation == run.pins.generation)
        require(machine.process_bound(base.CANDIDATE, exited=False))
        require(machine.execution_closed("starting_candidate"))
        if self.authorization is None:
            require(state.authorization_generation is None and state.recording_deadline == 0)
        else:
            require(
                base.encode(run.journal.entries[-1]["event"]) == base.encode(self.authorization)
            )
            require(state.authorization_generation == run.pins.generation)
            require(
                state.recording_deadline
                == self.authorization["now"] + machine.contract.maximum_recording_seconds
            )
            require(state.recording_outcome == "unconfirmed")
        observed = plans.clock.read()
        plan.check_clock(observed)
        now = observed.boottime_ns / plans.clock.NS
        require(machine.last_at <= now < min(state.deadline, plan.deadlines.ready_by))
        require(time.monotonic() < run.command.ready_by)
        if self.authorization is not None:
            machine.fresh(
                now,
                launch.bootstrap.recording.decode_observation(self.authorization["observation"]),
            )
        return machine, now

    def _read_ledger(self, end):
        base.clock(end)
        require(time.monotonic() < end <= min(time.monotonic() + 2, self.plan.lease["stop_by"]))
        ledger = self.ledger
        require(type(ledger) is binding.Ledger and not ledger._poisoned)
        binding._location(ledger.directory, ledger.binding)
        with binding.protected._private_directory(ledger.directory, exclusive=False) as fd:
            require(binding.identity(os.fstat(fd))[:6] == ledger._directory_identity)
            state = binding._read(fd, ledger.binding, end)
            require(state == ledger.state and time.monotonic() < end)
            if self.intent is not None:
                raw = binding.protected.evidence.read_bytes(
                    fd, "0001.json", limit=binding.MAX_BYTES, deadline=end
                )
                require(hashlib.sha256(raw).hexdigest() == self.intent.sha256)
                require(time.monotonic() < end)
        return state

    def _ledger(self):
        state = self._read_ledger(min(time.monotonic() + 2, self.ready.ready_by))
        require(state.expected is None and state.tip is None and state.acknowledgment is None)
        require(not state.closed and state.preservation is None)
        if self.intent is None:
            require(state == self.prepared and state.count == 1 and state.generation is None)
            require(state.start_by is None and state.finish_by is None)
            require(state.now <= self.ready.received_at)
        else:
            require(state == self.intent and state.count == 2)
        return state

    def retained_history(self):
        """Read the original authorization after begin, without renewed readiness.

        Requires this Start's actual returned Relay and its original retained
        Ready/process capability. Both complete journals and their original
        authorized prefixes are checked. No native health, recording return,
        file verdict, App action or new policy event is produced by this read.
        Only the live candidate phase before independent exit/recovery is in
        scope; cancellation, lost begin or original stop expiry refuses.
        """
        acquired = False
        try:
            began = time.monotonic()  # Include the initial plan/process checks in freshness.
            require(self.lock.acquire(blocking=False))
            acquired = True
            run, plan, ready, ledger = self._identity()
            require(self.used and self.authorization is not None and self.intent is not None)
            relay = self.relay
            returned_relay, authorization_raw, intent = self._begin_result
            require(relay is returned_relay and type(relay) is relayed.Relay)
            require(base.encode(self.authorization) == authorization_raw and self.intent is intent)
            require(relay.ready is ready and relay.ledger is ledger)
            require(type(relay.guard) is relayed.retained.Retained and relay.guard.ready is ready)
            require(relay.phase in ("started", "completed", "closed"))
            require(relay.guard.finish_by == plan.lease["stop_by"])
            end = min(began + 2, plan.lease["stop_by"])
            relay.guard.check()
            machine = run._history_until(end)
            entries = run.journal.entries
            require(tuple(base.encode(e) for e in entries[: len(self.history)]) == self.history)
            require(len(entries) > len(self.history))
            require(
                base.encode(entries[len(self.history)]["event"]) == base.encode(self.authorization)
            )
            state = machine.state
            require(state.phase == "candidate_running" and not state.finish_requested)
            require(
                state.operator_exit_sha256 is None and state.ready_evidence_sha256 == self.proof
            )
            require(state.launch_intent_sha256 == run.action.intent_sha256)
            require(state.launch_plan_sha256 == run.command.plan_sha256)
            require(
                state.candidate_generation == state.authorization_generation == run.pins.generation
            )
            require(machine.process_bound(base.CANDIDATE, exited=False))
            require(machine.execution_closed("starting_candidate"))
            require(
                state.recording_deadline
                == self.authorization["now"] + machine.contract.maximum_recording_seconds
            )
            current = self._read_ledger(end)
            require(current.count >= self.intent.count == 2 and current.preservation is None)
            require(relay.intent_sha256 == self.intent.sha256)
            require(current.generation == self.intent.generation == run.pins.generation)
            require(
                (current.start_by, current.finish_by)
                == (self.intent.start_by, self.intent.finish_by)
            )
            require(
                (relay.native_binding.start_by, relay.native_binding.finish_by)
                == (current.start_by, current.finish_by)
            )
            require(current.expected == relay.expected)
            require(current.closed is (relay.phase == "closed"))
            require((current.acknowledgment is not None) is current.closed)
            relay.guard.check()
            self._identity()
            observed = plans.clock.read()
            plan.check_clock(observed)
            now = observed.boottime_ns / plans.clock.NS
            require(
                machine.last_at
                <= now
                < min(
                    state.trial_deadline,
                    state.recording_deadline + base.COMMAND_SECONDS,
                    plan.deadlines.stop_by,
                )
            )
            require(began <= time.monotonic() < end)
            return machine
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _observe(self):
        self._guard()
        sample = self.run.read()
        require(type(sample) is launch.bootstrap.recovery.Sample)
        require(sample.boot_id == self.plan.boot)
        native = self.probe.read()
        self.run._check_native(native)
        machine, now = self._guard()
        require(sample.now <= now)
        candidate = sample.observation.candidate
        require(candidate.healthy is None and candidate.recording is None)
        observation = replace(
            sample.observation,
            candidate=replace(
                candidate,
                healthy=native.healthy,
                recording=native.recording,
            ),
        )
        machine.fresh(now, observation)
        return observation

    def start_once(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(not self.used)
            self.used = True
            _, began = self._guard()
            self._ledger()
            self.probe = launch.probe_exec.Sample(self.ready)
            require(self.probe.prepare() is None)
            require(self.run.read.prepare() is None)
            observation = self.run.qualify.during(self._observe)
            machine, now = self._guard()
            observation = replace(observation, sampled_at=min(began, observation.sampled_at))
            machine.fresh(now, observation)
            self._ledger()
            machine, now = self._guard()
            machine.fresh(now, observation)
            event = dict(
                kind="authorize_recording",
                boot_id=self.plan.boot,
                now=now,
                generation=self.run.pins.generation,
                contract_sha256=self.plan.candidate.contract.sha256,
                observation=asdict(observation),
            )
            require(self.run.journal.append(event) is None)
            self.authorization = event
            machine, _ = self._guard()
            self._ledger()
            # Convert the returned policy deadline with the ORIGINAL clock
            # interval. No later sample creates another recording budget.
            finish_by = min(
                self.plan.original_clock.native_deadline(machine.state.recording_deadline),
                self.plan.lease["stop_by"],
                self.ready.watch_deadline,
            )
            intent_at = time.monotonic()
            start_by = min(math.nextafter(intent_at + 10, -math.inf), self.ready.ready_by)
            require(intent_at + 3 < start_by < finish_by)
            self.intent = self.ledger.start_intent(
                now=intent_at,
                generation=self.run.pins.generation,
                authorization_sha256=base.checksum(event),
                start_by=start_by,
                finish_by=finish_by,
            )
            self._guard()
            self._ledger()
            # Relay owns the only send_once call. A returned Relay is not a
            # received start/completion or a worker/process exit acknowledgment.
            self.relay = relayed.Relay(self.ledger, self.ready)
            self._begin_result = (self.relay, base.encode(self.authorization), self.intent)
            return self.relay
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed = True
        if type(self.run) is launch.Launch:
            with suppress(Exception):
                self.run._fail(error)  # Closes transport, retains all original pidfds.
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedHostBegin(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        if not self.closed:
            self.closed = True
            if self.probe is not None:
                self.probe.close()


if __name__ == "__main__":
    raise SystemExit("Uninstalled original-policy recording join only; no service enabled.")
