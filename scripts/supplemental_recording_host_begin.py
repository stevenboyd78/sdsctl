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
from dataclasses import asdict, dataclass, replace
from threading import Event, Lock, Thread, current_thread, get_ident

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
        self.files_lock = Lock()
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

    def retained_history(self, *, require_live=False):
        """Read the original authorization after begin, without renewed readiness.

        Requires this Start's actual returned Relay and its original retained
        Ready/process capability. Both complete journals and their original
        authorized prefixes are checked. No native health, recording return,
        file verdict, App action or new policy event is produced by this read.
        Only the live candidate phase before independent exit/recovery is in
        scope; cancellation, lost begin or original stop expiry refuses.
        Active reads may require no exited workers at both fresh checks. This
        uses their actual returns, never the guard's cached exit field.
        """
        acquired = False
        try:
            began = time.monotonic()  # Include the initial plan/process checks in freshness.
            require(type(require_live) is bool)
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
            exited = relay.guard.check()
            require(not require_live or exited == frozenset())
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
            exited = relay.guard.check()
            require(not require_live or exited == frozenset())
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

    def read_files(self):
        """Read the original recording files under both retained host journals.

        Uses this Start's actual returned Relay and fixed host progress path.
        Before completion, Relay selects active/finalizing from its authenticated
        owner schedule; afterward only its actual acknowledged completion may
        supply the finalized read. This is read-only file evidence, not a full
        host/source/runtime/native-health sample or permission to restore.
        """
        acquired = False
        try:
            began = time.monotonic()
            require(self.files_lock.acquire(blocking=False))
            acquired = True
            self.retained_history()
            relay, ledger = self.relay, self.ledger
            phase, expected, owner_plan = relay.phase, relay.expected, relay.plan
            require(phase in ("completed", "closed") and expected is not None)
            end = min(began + 2, self.plan.lease["stop_by"], relay.guard.finish_by)
            if phase == "completed":
                end = min(end, relay.native_binding.finish_by, owner_plan.finish_by)
            history = tuple(base.encode(entry) for entry in self.run.journal.entries)
            state = ledger.state
            require(time.monotonic() < end)
            if phase == "completed":
                result = relay.read_progress(self.plan.root / "recording-progress")
                require(type(result) is binding.protected.Collected)
                require(result.files.stage in ("active", "finalizing"))
                require(result.artifact is None)
            else:
                result = relay.recheck_completed()
                require(type(result) is binding.protected.Collected)
                require(result.files.stage == "finalized" and result.artifact is not None)
            require(result.files.contract_sha256 == self.plan.candidate.contract.sha256)
            require(result.files.generation == self.run.pins.generation == expected.generation)
            self.retained_history()
            require(self.relay is relay and self.ledger is ledger and ledger.state == state)
            require(
                relay.phase == phase and relay.expected is expected and relay.plan is owner_plan
            )
            require(tuple(base.encode(entry) for entry in self.run.journal.entries) == history)
            ended = time.monotonic()
            require(began <= ended < end)
            require(result.files.stage != "active" or ended < owner_plan.stop_at)
            return result
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.files_lock.release()

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


@dataclass(frozen=True)
class _StaticHostSnapshot:
    """Incomplete internal evidence: explicitly no recording or health result."""

    boot: str
    began: float
    ended: float
    normal: base.App
    candidate: base.App
    other_stopped: bool
    jobs_idle: bool


class _StaticHostObserver(plans.ordinary.HostObserver):
    """The complete ordinary metadata checks, without an invented file stage."""

    validate_seals = staticmethod(plans.host.HostObserver.validate_seals)

    def file_pin(self, slug, config, files):
        if slug == base.NORMAL:
            return super().file_pin(slug, config, files)
        require(slug == base.CANDIDATE and type(files) is plans.host.static.StaticFiles)
        seal = self.seals[slug]
        return plans.host.CandidateSeal(
            config.version, seal.image, config.settings_sha256, files, seal.contract
        ).pin

    def sample(self, boot, now, began, observed, other_stopped, jobs_idle):
        return _StaticHostSnapshot(
            boot,
            began,
            now,
            observed[base.NORMAL],
            observed[base.CANDIDATE],
            other_stopped,
            jobs_idle,
        )


class RetainedHost:
    """Full host/file sample after actual begin; native flags remain unknown.

    Only fixed Supervisor/static-file reads run in the background. Actual
    authorization, Relay file selection, PostBegin lease and actor checks remain
    on the owning thread. The earliest original two-second window includes all
    contributing reads, and no worker result can supply a false pristine stage.

    The caller must surround the join with the actual RetainedQualification and
    separately join any cached native probe. This class never starts a probe,
    receives a native return, publishes progress, changes policy or grants exit
    or restoration. It is invalid after the Relay exit collector closes transport.
    """

    def __init__(self, start, continuity):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = False
        self.pending = None
        self.start = None
        try:
            require(type(start) is Start and type(continuity) is launch.idle_module.PostBegin)
            self.start, self.continuity = start, continuity
            self.run, self.plan, self.relay = start.run, start.plan, start.relay
            self.docker = self.run.read.docker
            self.objects = (
                start,
                self.run,
                self.plan,
                self.relay,
                continuity,
                self.docker,
                self.run.read,
                self.run.witness,
                self.run.qualify,
            )
            self._guard()
        except BaseException as error:
            self._fail(error)

    def _guard(self, *, require_live=False):
        require(type(require_live) is bool)
        require(not self.failed and self.owner == (os.getpid(), get_ident()))
        start, run, plan, relay, continuity = (
            self.start,
            self.run,
            self.plan,
            self.relay,
            self.continuity,
        )
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        start,
                        run,
                        plan,
                        relay,
                        continuity,
                        self.docker,
                        run.read,
                        run.witness,
                        run.qualify,
                    ),
                    self.objects,
                    strict=True,
                )
            )
        )
        require(type(start) is Start and start.run is run and start.plan is plan)
        require(start.relay is relay and relay.phase in ("completed", "closed"))
        require(type(continuity) is launch.idle_module.PostBegin)
        require(continuity.idle is run.idle and continuity.guard is relay.guard)
        require(continuity.plan is plan and continuity.ready is run.ready)
        require(continuity.finish_by == plan.lease["stop_by"])
        require(
            type(self.docker) is plans.ordinary.Docker
            and self.docker.path == "/var/run/docker.sock"
        )
        require(self.docker is run.read.docker is run.qualify.docker)
        start.retained_history(require_live=require_live)

    def _context(self):
        return (
            self.relay.phase,
            self.relay.expected,
            self.relay.plan,
            self.start.ledger.state,
            tuple(base.encode(entry) for entry in self.run.journal.entries),
        )

    def _clock(self):
        require(not self.failed and self.owner == (os.getpid(), get_ident()))
        observed = plans.clock.read()
        self.plan.check_clock(observed)
        now = observed.boottime_ns / plans.clock.NS
        require(now < self.plan.deadlines.stop_by)
        return observed.boot, now

    def _observer(self, clock):
        plan = self.plan
        layouts = {item.slug: item for item in plan.layouts}

        def collect_files(slug, container):
            require(slug in (base.NORMAL, base.CANDIDATE))
            if slug == base.NORMAL:
                return plans.host.static.collect(layouts[slug], container)
            return plans.host.candidate_static.collect(layouts[slug], container)

        supervisor = plans.ordinary.SupervisorReads(
            self.docker, image=plan.cli_image, incarnation=plan.cli_generation
        )
        return _StaticHostObserver(
            self.docker,
            supervisor,
            seals=(plan.normal, plan.candidate),
            installed_versions=dict(plan.installed_versions),
            other_scanner_apps=frozenset(plan.other_scanner_apps),
            core_image=plan.core_image,
            core_generation=plan.core_generation,
            core_version=plan.core_version,
            read_clock=clock,
            collect_files=collect_files,
            read_native=launch.BootstrapHost._unknown_native,
            network=plans.ordinary.AUDIO_NETWORK,
        )

    def prepare(self):
        acquired = False
        try:
            require(not self.failed and self.owner == (os.getpid(), get_ident()))
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(self.pending is None)
            started = time.monotonic()
            boot, began = self._clock()
            self._guard()
            context = self._context()
            before = self.continuity.read()
            require(type(before) is launch.idle_module.Continuity)
            require(self._context() == context)
            pending = _RetainedHostRead(
                self, boot, began, before, context, min(started + 2, self.plan.lease["stop_by"])
            )
            self.pending = pending
            pending.worker.start()
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def __call__(self):
        acquired = False
        try:
            require(not self.failed and self.owner == (os.getpid(), get_ident()))
            require(self.lock.acquire(blocking=False))
            acquired = True
            pending = self.pending
            require(type(pending) is _RetainedHostRead)
            # Await the fixed read-only worker before decoding the owner's
            # journals. Otherwise that CPU work can starve its real clock
            # sampler at a GIL handoff. Keep both original sampling limits;
            # history/context still bracket the worker and precede file reads.
            snapshot = pending.finish()
            self._guard()
            require(self._context() == pending.context)
            collected = self.start.read_files()
            after = self.continuity.read()
            require(type(after) is launch.idle_module.Continuity)
            require(replace(after, sampled_at=pending.before.sampled_at) == pending.before)
            require(after.sampled_at >= pending.before.sampled_at)
            self._guard()
            boot, ended = self._clock()
            require(self.pending is pending and self._context() == pending.context)
            require(boot == pending.boot == snapshot.boot)
            require(pending.began <= snapshot.began <= snapshot.ended <= ended)
            require(pending.began <= pending.before.sampled_at <= after.sampled_at <= ended)
            require(0 <= ended - pending.began < 2 and time.monotonic() < pending.deadline)
            require(snapshot.normal.state == "stopped")
            candidate = snapshot.candidate
            require(
                candidate.state == "running" and candidate.generation == self.run.pins.generation
            )
            require(candidate.healthy is None and candidate.recording is None)
            require(collected.files.stage != "active" or time.monotonic() < self.relay.plan.stop_at)
            observation = launch.bootstrap.recording.Observation(
                pending.began,
                snapshot.normal,
                candidate,
                snapshot.other_stopped,
                snapshot.jobs_idle,
                True,
                collected.files,
            )
            result = launch.bootstrap.recovery.Sample(boot, ended, observation)
            self.pending = None
            return result
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def discard(self):
        require(self.owner == (os.getpid(), get_ident()))
        self.failed = True
        if self.pending is not None:
            self.pending.cancelled.set()

    def _fail(self, error):
        self.failed = True
        if self.pending is not None:
            self.pending.cancelled.set()
        if type(self.start) is Start:
            self.start._fail(error)  # Retain original actor/probe descriptors.
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedHostBegin(MESSAGE) from None


class _RetainedHostRead:
    """One fixed incomplete snapshot; never contains a recording file verdict."""

    def __init__(self, host, boot, began, before, context, deadline):
        self.host, self.boot, self.began, self.before = host, boot, began, before
        self.context, self.deadline = context, deadline
        self.done, self.cancelled = Event(), Event()
        self.snapshot = self.error = None
        self.worker = Thread(target=self._run, name="sdsctl-retained-host-read", daemon=True)
        self.original = (host, host.plan, boot, began, before, context, deadline, self.worker)

    def _fixed(self):
        require(
            (
                self.host,
                self.host.plan,
                self.boot,
                self.began,
                self.before,
                self.context,
                self.deadline,
                self.worker,
            )
            == self.original
        )
        require(self.host.pending is self)

    def _clock(self):
        self._fixed()
        require(current_thread() is self.worker and os.getpid() == self.host.owner[0])
        require(not self.cancelled.is_set() and not self.host.failed)
        require(time.monotonic() < self.deadline)
        observed = plans.clock.read()
        self.host.plan.check_clock(observed)
        now = observed.boottime_ns / plans.clock.NS
        require(observed.boot == self.boot and self.began <= now < self.host.plan.deadlines.stop_by)
        return observed.boot, now

    def _run(self):
        try:
            self._clock()
            self.snapshot = self.host._observer(self._clock).read()
            self._clock()
        except BaseException as error:
            self.error = error
        finally:
            self.done.set()

    def finish(self):
        self._fixed()
        require(self.host.owner == (os.getpid(), get_ident()))
        require(not self.cancelled.is_set() and not self.host.failed)
        require(time.monotonic() < self.deadline)
        require(self.done.wait(max(0, self.deadline - time.monotonic())))
        self.worker.join(max(0, self.deadline - time.monotonic()))
        require(not self.worker.is_alive() and time.monotonic() < self.deadline)
        require(not self.cancelled.is_set() and not self.host.failed)
        if self.error is not None:
            raise self.error
        require(type(self.snapshot) is _StaticHostSnapshot)
        return self.snapshot


class ActiveSample:
    """One active host/files/native sample under original input qualification.

    No caller callback, health flag, replacement Capture or new deadline. The
    original Start/RetainedHost/PostBegin and full RetainedQualification must
    already be bound together. Creates only one passive cached-status exec;
    authorization, checkpoint publication, completion, worker exits and recovery
    stay separate. This is not an installed polling loop or automatic retry.
    """

    def __init__(self, host, qualify):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.used = self.failed = self.closed = False
        self.probe = self.host = None
        try:
            require(type(host) is RetainedHost)
            require(type(qualify) is launch.RetainedQualification)
            self.host, self.qualify = host, qualify
            self.start, self.run, self.plan = host.start, host.run, host.plan
            self.relay, self.continuity = host.relay, host.continuity
            self.objects = (
                host,
                qualify,
                self.start,
                self.run,
                self.plan,
                self.relay,
                self.continuity,
                host.docker,
            )
            self.context = host._context()
            self._guard()
        except BaseException as error:
            self._fail(error)

    def _bindings(self):
        require(not self.failed and not self.closed)
        require(self.owner == (os.getpid(), get_ident()))
        host, qualify = self.host, self.qualify
        require(type(host) is RetainedHost and type(qualify) is launch.RetainedQualification)
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        host,
                        qualify,
                        host.start,
                        host.run,
                        host.plan,
                        host.relay,
                        host.continuity,
                        host.docker,
                    ),
                    self.objects,
                    strict=True,
                )
            )
        )
        require(
            all(
                a is b
                for a, b in zip(
                    (self.start, self.run, self.plan, self.relay, self.continuity),
                    (host.start, host.run, host.plan, host.relay, host.continuity),
                    strict=True,
                )
            )
        )
        require(qualify.continuity is self.continuity and qualify.plan is self.plan)
        require(qualify.idle is self.run.idle and qualify.witness is self.run.witness)
        require(qualify.docker is host.docker is self.run.qualify.docker)
        require(not qualify.failed and host._context() == self.context)
        require(self.relay.phase == "completed" and self.relay.expected is not None)
        require(time.monotonic() < self.relay.plan.stop_at)

    def _guard(self):
        self._bindings()
        # The original history already checks actors on both sides. Require
        # those fresh returns to be live instead of repeating a third full
        # process/journal traversal inside the same two-second sample.
        self.host._guard(require_live=True)

    def _join(self):
        # RetainedHost joins its worker before CPU-heavy owner checks. Do not
        # put another journal traversal in front of that join.
        sample = self.host()
        require(type(sample) is launch.bootstrap.recovery.Sample)
        require(sample.boot_id == self.plan.boot)
        self._bindings()
        observation = sample.observation
        boot, observed_now = self.host._clock()
        require(boot == sample.boot_id)
        require(self.began <= observation.sampled_at <= sample.now <= observed_now)
        require(observation.files.stage == "active")
        candidate = observation.candidate
        require(candidate.generation == self.run.pins.generation)
        require(candidate.healthy is None and candidate.recording is None)
        native = self.probe.read()
        require(type(native) is plans.ordinary.NativeState)
        require(native.generation == candidate.generation)
        require(type(native.healthy) is bool and type(native.recording) is bool)
        self._bindings()
        return replace(
            observation,
            sampled_at=self.began,
            candidate=replace(candidate, healthy=native.healthy, recording=native.recording),
        )

    def read(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            self._guard()
            require(not self.used and self.host.pending is None)
            self.used = True
            # Complete inputs are checked before a passive process is created.
            # This check gives no health and is not reused for the actual read.
            require(self.qualify() is None)
            self._guard()
            began = time.monotonic()
            first = plans.clock.read()
            self.plan.check_clock(first)
            self.began = first.boottime_ns / plans.clock.NS
            end = min(began + 2, self.relay.plan.stop_at, self.plan.lease["stop_by"])
            self.probe = launch.probe_exec.Sample(self.relay.guard)
            require(self.probe.original is self.relay.guard)
            require(self.probe.prepare() is None)
            require(self.host.prepare() is None)
            observation = self.qualify.during(self._join)
            self._guard()
            last = plans.clock.read()
            self.plan.check_clock(last)
            now = last.boottime_ns / plans.clock.NS
            require(first.boot == last.boot == self.plan.boot)
            require(self.began <= now and began <= time.monotonic() < end)
            launch.bootstrap.recording.Machine.fresh(now, observation)
            return launch.bootstrap.recovery.Sample(last.boot, now, observation)
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed = True
        if self.probe is not None and self.probe.channel is not None:
            with suppress(Exception):
                self.probe.channel.close()
        if type(self.host) is RetainedHost:
            self.host._fail(error)  # Original process/probe descriptors stay retained.
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedHostBegin(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        if not self.closed:
            self.closed = True
            if self.probe is not None:
                self.probe.close()  # No original actor, lease or host ownership transfer.


if __name__ == "__main__":
    raise SystemExit("Uninstalled original-policy recording join only; no service enabled.")
