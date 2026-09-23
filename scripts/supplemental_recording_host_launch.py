#!/usr/bin/env python3
"""Schema3 bootstrap policy -> one original idle/Engine/Ready launch.

Uninstalled host adapter segment. The supplied read/qualify functions are trusted
host collectors, not network callbacks: they must independently check installed
source, runtime, environment, mounts, jobs and ownership against the original
plan. This joins their results to actual retained idle/process/Engine objects;
it does not make supplied pins self-authenticating or install independent recovery.
"""

from __future__ import annotations

import hashlib
import math
import os
import time
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from threading import Event, Lock, Thread, current_thread, get_ident

import supplemental_recording_idle_observer as idle_module
import supplemental_recording_probe_exec as probe_exec
import supplemental_recording_ready as received
import supplemental_recording_runtime as runtime

plans = idle_module.host_plan
bootstrap, base = plans.bootstrap, plans.base
engine = received.engine
binding, execution = engine.dispatch.binding, engine.dispatch.execution
MESSAGE = "Recording host launch is unconfirmed; preserve the case and do not retry."


class UnconfirmedHostLaunch(ValueError):
    """No uncertainty permits another policy intent, create, attach or begin."""


def require(value):
    if not value:
        raise UnconfirmedHostLaunch(MESSAGE)


class BootstrapHost:
    """Full read-only host sample for one original idle candidate, before begin.

    Reuses the fixed Supervisor reader, complete host observer and pristine
    recording collector. It never selects a native exec/cache query: PID1 idle
    evidence leaves both health flags unknown, even after the operator starts.
    Launch joins its separately authenticated one-use probe only after this
    sample. This is not the normal-App readiness or active-recording collector.

    All images/files/options/versions/jobs/Core/network/other owners are freshly
    collected by HostObserver. Original manifests are never recaptured as a new
    baseline. Installed source/runtime qualification, original pidfd provenance,
    independent recovery and outer I/O supervision remain separate obligations.
    Construction does not dispatch or perform a host request. A failed read
    permanently consumes this instance; caller-owned handles are never closed.
    """

    def __init__(self, plan, projected, idle, witness, docker):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = False
        self.pending = None
        try:
            require(type(plan) is plans.Plan and plans.load_bytes(plan.raw, plan.sha256) == plan)
            plan.check_projection(projected)
            require(type(idle) is idle_module.Idle and idle.plan is plan)
            require(type(witness) is engine.dispatch.process.ProcessWitness)
            require(idle.init == witness.identity)
            require(type(docker) is plans.ordinary.Docker and docker.path == "/var/run/docker.sock")
            base.digest(idle.generation)
            self.plan, self.projected, self.idle = plan, projected, idle
            self.witness, self.docker = witness, docker
            self.init, self.generation = witness.identity, idle.generation
            self.fd, self.fd_identity = witness.fd, runtime.identity(os.fstat(witness.fd))
            self.original = (plan.raw, projected.sha256, self.init, self.generation)
        except BaseException as error:
            self._fail(error)

    def _clock(self):
        require(not self.failed and self.owner == (os.getpid(), get_ident()))
        observed = plans.clock.read()
        self.plan.check_clock(observed)
        now = observed.boottime_ns / plans.clock.NS
        require(now < self.plan.deadlines.ready_by)
        return observed.boot, now

    def _guard(self):
        require(type(self.plan) is plans.Plan)
        require(plans.load_bytes(self.plan.raw, self.plan.sha256) == self.plan)
        self.plan.check_projection(self.projected)
        require((self.plan.raw, self.projected.sha256, self.init, self.generation) == self.original)
        require(type(self.idle) is idle_module.Idle and self.idle.plan is self.plan)
        require(type(self.witness) is engine.dispatch.process.ProcessWitness)
        require(self.witness.identity == self.idle.init == self.init)
        require(self.idle.generation == self.generation and self.witness.fd == self.fd)
        require(runtime.identity(os.fstat(self.fd)) == self.fd_identity)
        require(not self.witness.exited())
        require(
            engine.dispatch.process.read_identity(self.init.pid, self.init.container_id)
            == self.init
        )
        require(
            type(self.docker) is plans.ordinary.Docker
            and self.docker.path == "/var/run/docker.sock"
        )
        return self._clock()

    @staticmethod
    def _unknown_native(slug, generation):
        require(slug in (base.NORMAL, base.CANDIDATE))
        return plans.ordinary.NativeState(generation, None, None)

    def _observer(self, read_clock):
        plan = self.plan
        layouts = {item.slug: item for item in plan.layouts}
        collector = plans.host.FilesCollector(
            layouts[base.NORMAL],
            layouts[base.CANDIDATE],
            plans.host.recording.Collector(self.projected.host),
            lambda: plans.host.Capture("pristine"),
        )
        supervisor = plans.ordinary.SupervisorReads(
            self.docker, image=plan.cli_image, incarnation=plan.cli_generation
        )
        return plans.host.HostObserver(
            self.docker,
            supervisor,
            seals=(plan.normal, plan.candidate),
            installed_versions=dict(plan.installed_versions),
            other_scanner_apps=frozenset(plan.other_scanner_apps),
            core_image=plan.core_image,
            core_generation=plan.core_generation,
            core_version=plan.core_version,
            read_clock=read_clock,
            collect_files=collector,
            read_native=self._unknown_native,
            network=plans.ordinary.AUDIO_NETWORK,
        )

    def prepare(self):
        """Start only a fixed read-only host sample, without returning evidence.

        This permits Supervisor/file I/O to overlap independent runtime hashing.
        Idle/namespace/pidfd ownership stays on the original calling thread. The
        worker never performs a native probe, journal append, App control or
        recording action. Its original start and two-second deadline are held
        across collection/join; finishing never renews freshness. A failed outer
        qualification must discard this object, even if its worker later finishes.

        A blocked kernel/Engine read still needs the independent outer process
        supervisor. discard() invalidates a pending result, not that outstanding
        read; its thread handle is retained and never supplies process-exit proof.
        """
        acquired = False
        try:
            require(not self.failed and self.owner == (os.getpid(), get_ident()))
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(self.pending is None)
            started = time.monotonic()
            boot, began = self._clock()
            self._guard()
            before = self.idle.read()
            require(type(before) is idle_module.Evidence)
            pending = _PreparedHostRead(
                self, boot, began, before, min(started + 2, self.plan.lease["ready_by"])
            )
            self.pending = pending
            pending.worker.start()
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def discard(self):
        """Permanently consume pending evidence; never signal or close pidfds."""
        require(self.owner == (os.getpid(), get_ident()))
        self.failed = True
        if self.pending is not None:
            self.pending.cancelled.set()

    def __call__(self):
        acquired = False
        try:
            require(not self.failed and self.owner == (os.getpid(), get_ident()))
            require(self.lock.acquire(blocking=False))
            acquired = True
            if self.pending is not None:
                pending = self.pending
                self._guard()
                sample = pending.finish()
                result = self._complete(pending.boot, pending.began, pending.before, sample)
                require(self.pending is pending and not self.failed)
                self.pending = None
                return result
            boot, began = self._clock()
            self._guard()  # Include the first identity/manifest checks in freshness.
            before = self.idle.read()
            require(type(before) is idle_module.Evidence)
            return self._complete(boot, began, before, self._observer(self._clock).read())
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _complete(self, boot, began, before, sample):
        require(type(sample) is bootstrap.recovery.Sample)
        after = self.idle.read()
        require(type(after) is idle_module.Evidence)
        require(replace(after, sampled_at=before.sampled_at) == before)
        require(after.sampled_at >= before.sampled_at)
        end_boot, ended = self._guard()
        require(boot == end_boot == sample.boot_id and began <= sample.now <= ended)
        require(0 <= ended - began <= 2)
        require(began <= before.sampled_at <= after.sampled_at <= ended)
        plan = self.plan
        require(
            (before.plan_sha256, before.generation, before.init, before.lease_sha256)
            == (plan.sha256, self.generation, self.init, plan.lease_sha256)
        )
        observation = sample.observation
        require(observation.normal.state == "stopped")
        candidate = observation.candidate
        require(candidate.state == "running" and candidate.generation == self.generation)
        require(candidate.healthy is None and candidate.recording is None)
        require(
            observation.files
            == bootstrap.recording.Files(
                plan.candidate.contract.sha256, "pristine", plan.candidate.contract.baseline_sha256
            )
        )
        return bootstrap.recovery.Sample(
            boot, ended, replace(observation, sampled_at=min(began, observation.sampled_at))
        )

    def _fail(self, error):
        self.failed = True
        if self.pending is not None:
            self.pending.cancelled.set()
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedHostLaunch(MESSAGE) from None


class _PreparedHostRead:
    """Internal, one fixed read-only worker; not a caller-supplied result API."""

    def __init__(self, host, boot, began, before, deadline):
        self.host, self.boot, self.began, self.before = host, boot, began, before
        self.deadline = deadline
        self.done, self.cancelled = Event(), Event()
        self.sample = self.error = None
        self.worker = Thread(target=self._run, name="sdsctl-bootstrap-host-read", daemon=True)
        self.original = (host, boot, began, before, deadline, self.worker)

    def _fixed(self):
        require(
            (self.host, self.boot, self.began, self.before, self.deadline, self.worker)
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
        require(
            observed.boot == self.boot and self.began <= now < self.host.plan.deadlines.ready_by
        )
        return observed.boot, now

    def _run(self):
        try:
            self._clock()
            self.sample = self.host._observer(self._clock).read()
            self._clock()
        except BaseException as error:
            self.error = error
        finally:
            self.done.set()

    def finish(self):
        self._fixed()
        require(self.host.owner == (os.getpid(), get_ident()))
        require(self.host.pending is self and not self.cancelled.is_set())
        require(time.monotonic() < self.deadline)
        require(self.done.wait(max(0, self.deadline - time.monotonic())))
        self.worker.join(max(0, self.deadline - time.monotonic()))
        require(not self.worker.is_alive() and time.monotonic() < self.deadline)
        require(not self.cancelled.is_set() and not self.host.failed)
        if self.error is not None:
            raise self.error
        require(type(self.sample) is bootstrap.recovery.Sample)
        return self.sample


class CandidateQualification:
    """Fresh source/runtime/environment join for one retained original idle init.

    Read-only, uninstalled adapter segment. Independently reviewed image, source,
    runtime and environment pins are inputs, never learned from this observation.
    The environment pin is explicitly the supervised Config.Env profile, not the
    legacy five-key image profile. No credentials are retained in this object.

    Full host files/jobs/Core/network/other owners, helper qualification, native
    cached health and independent recovery remain separate obligations. A call
    returns None only after fresh checks; elapsed_seconds is diagnostic, never
    reusable authority. Failure permanently consumes this collector. It never
    closes caller-owned handles. Kernel/Engine stalls still require independent
    outer supervision; elapsed checks cannot interrupt blocked kernel I/O.
    """

    CODE_ROOTS = plans.host.candidate_static.CODE_ROOTS + tuple(
        Path(name)
        for name in (
            "/etc/ssl",
            "/etc/ld.so.conf",
            "/etc/ld.so.cache",
            "/etc/ld.so.conf.d",
            "/etc/ld.so.preload",
            "/etc/localtime",
            "/etc/timezone",
        )
    )
    MAX_SECONDS = 2.0
    EVIDENCE = idle_module.Evidence

    def __init__(
        self,
        plan,
        idle,
        witness,
        docker,
        *,
        image_environment_sha256,
        timezone,
        hostname,
        architecture,
    ):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed, self.elapsed_seconds = False, None
        try:
            require(type(plan) is plans.Plan and plans.load_bytes(plan.raw, plan.sha256) == plan)
            require(type(idle) is idle_module.Idle and idle.plan == plan)
            require(type(witness) is engine.dispatch.process.ProcessWitness)
            require(idle.init == witness.identity)
            require(type(docker) is plans.ordinary.Docker and docker.path == "/var/run/docker.sock")
            base.digest(idle.generation)
            base.digest(image_environment_sha256)
            runtime._timezone_name(timezone)
            plans.text(hostname, r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}")
            require(architecture in ("amd64", "arm64"))
            self.plan, self.idle, self.witness, self.docker = plan, idle, witness, docker
            self.image_environment_sha256 = image_environment_sha256
            self.timezone, self.hostname, self.architecture = timezone, hostname, architecture
            self.init, self.generation = witness.identity, idle.generation
            self.fd = witness.fd
            self.fd_identity = runtime.identity(os.fstat(self.fd))
            self.original = self._pins()
            self.layout = next(item for item in plan.layouts if item.slug == base.CANDIDATE)
            self._guard(min(time.monotonic() + self.MAX_SECONDS, self._bounds()[1]))
        except BaseException as error:
            self._fail(error)

    def _pins(self):
        return (
            self.plan.raw,
            self.init,
            self.generation,
            self.image_environment_sha256,
            self.timezone,
            self.hostname,
            self.architecture,
        )

    def _bounds(self):
        return self.plan.deadlines.ready_by, self.plan.lease["ready_by"]

    def _evidence(self):
        return self.idle.read()

    @staticmethod
    def _original(evidence):
        return evidence

    def _guard(self, deadline):
        require(not self.failed and self.owner == (os.getpid(), get_ident()))
        require(os.geteuid() == runtime.ROOT_UID and time.monotonic() < deadline)
        require(self._pins() == self.original)
        require(
            type(self.plan) is plans.Plan
            and plans.load_bytes(self.plan.raw, self.plan.sha256) == self.plan
        )
        require(
            type(self.docker) is plans.ordinary.Docker
            and self.docker.path == "/var/run/docker.sock"
        )
        require(type(self.idle) is idle_module.Idle and self.idle.plan == self.plan)
        require(type(self.witness) is engine.dispatch.process.ProcessWitness)
        require(self.idle.init == self.witness.identity == self.init)
        require(self.idle.generation == self.generation and self.witness.fd == self.fd)
        require(runtime.identity(os.fstat(self.fd)) == self.fd_identity)
        require(not self.witness.exited())
        require(
            engine.dispatch.process.read_identity(self.init.pid, self.init.container_id)
            == self.init
        )
        require(
            self.layout == next(item for item in self.plan.layouts if item.slug == base.CANDIDATE)
        )
        observed = plans.clock.read()
        self.plan.check_clock(observed)
        policy_end, native_end = self._bounds()
        require(observed.boottime_ns / plans.clock.NS < policy_end)
        require(time.monotonic() < min(deadline, native_end))

    def _metadata(self, deadline):
        self._guard(deadline)
        plan = self.plan
        container = self.docker.container("app_" + base.CANDIDATE)
        require(container.get("Id") == self.init.container_id)
        require(
            plans.ordinary.generation(
                container, name="app_" + base.CANDIDATE, image=plan.candidate_runtime.image
            )
            == self.generation
        )
        require(container["State"]["Pid"] == self.init.pid)
        plans.ordinary.manual_container(container)
        require(
            container.get("Path") == plan.idle_argv[0]
            and container.get("Args") == list(plan.idle_argv[1:])
        )
        config = container.get("Config")
        require(type(config) is dict and config.get("User") in ("0", "0:0"))
        require(config.get("Hostname") == self.hostname and config.get("WorkingDir") in ("", "/"))
        command = []
        for key in ("Entrypoint", "Cmd"):
            parts = config.get(key)
            require(parts is None or type(parts) is list)
            command.extend(parts or [])
        require(command == list(plan.idle_argv))
        require(
            runtime.supervised_environment(
                config.get("Env"),
                image_environment_sha256=self.image_environment_sha256,
                timezone=self.timezone,
            )
            == plan.candidate_runtime.environment
        )
        merged = plans.fixed._merged_root(self.layout, container, code_roots=self.CODE_ROOTS)
        require(merged is not None)
        self._guard(deadline)
        image = self.docker.image(plan.candidate_runtime.image)
        require(image.get("Id") == plan.candidate_runtime.image and image.get("Os") == "linux")
        require(
            image.get("Architecture") == self.architecture and type(image.get("Config")) is dict
        )
        require(runtime.environment(image["Config"].get("Env")) == self.image_environment_sha256)
        # Compare all configuration/mount inputs, not changing counters or health.
        # Tokens are represented only by the separately tagged one-way digest.
        stamp = base.checksum(
            {
                "config": config | {"Env": plan.candidate_runtime.environment},
                "host": container["HostConfig"],
                "mounts": container["Mounts"],
                "driver": container["GraphDriver"],
                "generation": self.generation,
            }
        )
        self._guard(deadline)
        return merged, stamp, config["Env"]

    def __call__(self):
        """Complete fresh qualification without a contributing observation."""
        return self._collect(None)

    def during(self, observe):
        """Return one trusted read-only observation only after fresh qualification.

        Full source and runtime inventories surround the callback, which may
        read cached status/host state but must not authorize or perform writes.
        Both original process-environment reads, metadata and idle evidence also
        bracket it. Nothing is cached for a later call; failures poison this
        instance. The entire collection keeps the same two-second maximum.
        """
        try:
            require(callable(observe))
        except BaseException as error:
            self._fail(error)
        return self._collect(observe)

    def _environment(self, configured, deadline):
        self._guard(deadline)
        evidence = runtime.collect_supervised_process_environment(
            self.witness,
            deadline=min(deadline, time.monotonic() + 1),
            configured=configured,
            configured_sha256=self.plan.candidate_runtime.environment,
            image_environment_sha256=self.image_environment_sha256,
            timezone=self.timezone,
            hostname=self.hostname,
            fixed_exec=False,
        )
        require(type(evidence) is runtime.ProcessEnvironment and evidence.process == self.init)
        self._guard(deadline)
        return evidence

    def _collect(self, observe):
        acquired = False
        self.elapsed_seconds = None
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            began = time.monotonic()
            deadline = min(began + self.MAX_SECONDS, self._bounds()[1])
            self._guard(deadline)
            before = self._evidence()
            require(type(before) is self.EVIDENCE)
            original = self._original(before)
            require(type(original) is idle_module.Evidence and original.init == self.init)
            require(
                (original.plan_sha256, original.generation, original.lease_sha256)
                == (self.plan.sha256, self.generation, self.plan.lease_sha256)
            )
            merged, stamp, configured = self._metadata(deadline)
            native = plans.host.candidate_static
            source = native.source.Layout(merged / plans.fixed.PACKAGE, merged / native.NATIVE)
            result = None
            if observe is None:
                source.verify(self.plan.candidate_runtime.source)
                self._guard(deadline)
                runtime.Layout(merged).verify_supervised(
                    self.plan.candidate_runtime.interpreter, self.timezone
                )
            else:
                environment_before = self._environment(configured, deadline)

                def guarded_observation():
                    self._guard(deadline)
                    value = observe()
                    self._guard(deadline)
                    return value

                result = source.verify_during(
                    self.plan.candidate_runtime.source,
                    lambda: runtime.Layout(merged).verify_supervised_during(
                        self.plan.candidate_runtime.interpreter,
                        self.timezone,
                        guarded_observation,
                        deadline=deadline,
                    ),
                    deadline=deadline,
                )
            evidence = self._environment(configured, deadline)
            require(began <= evidence.observed_at < deadline)
            if observe is not None:
                require(
                    (evidence.sha256, evidence.process)
                    == (environment_before.sha256, environment_before.process)
                )
                require(began <= environment_before.observed_at <= evidence.observed_at)
            after_root, after_stamp, _ = self._metadata(deadline)
            require((after_root, after_stamp) == (merged, stamp))
            after = self._evidence()
            require(type(after) is self.EVIDENCE)
            # Evidence timestamps must advance; immutable process/claim/files
            # and original clock domain must not change beneath that freshness.
            require(replace(after, sampled_at=before.sampled_at) == before)
            require(after.sampled_at >= before.sampled_at)
            self._guard(deadline)
            self.elapsed_seconds = time.monotonic() - began
            require(0 <= self.elapsed_seconds < self.MAX_SECONDS)
            return result
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed, self.elapsed_seconds = True, None
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedHostLaunch(MESSAGE) from None


class RetainedQualification(CandidateQualification):
    """Same fresh source/runtime bracket, only after an actual retained begin.

    Uses PostBegin's ORIGINAL init/lease/actor continuity and already fixed stop
    bounds, never an expired Idle.read or renewed readiness. Source, environment,
    mounts, image and original process reads are identical to the bootstrap
    qualifier; nothing is cached from a previous successful call. A worker exit
    during collection refuses that sample. No health, recording completion,
    independent host recovery or permission to begin is produced by this class.
    Launch/Start continue requiring the exact pre-begin qualification type.
    """

    EVIDENCE = idle_module.Continuity

    def __init__(self, continuity, witness, docker, **profile):
        self.failed, self.elapsed_seconds = False, None
        try:
            require(type(continuity) is idle_module.PostBegin)
            require(not continuity.closed and not continuity.failed)
            self.continuity = continuity
            self.continuity_objects = (
                continuity,
                continuity.plan,
                continuity.idle,
                continuity.guard,
                continuity.ready,
            )
            self.continuity_finish = continuity.finish_by
            super().__init__(continuity.plan, continuity.idle, witness, docker, **profile)
        except BaseException as error:
            self._fail(error)

    def _bounds(self):
        continued = self.continuity
        require(type(continued) is idle_module.PostBegin)
        require(continued.plan is self.plan and continued.idle is self.idle)
        require(
            all(
                a is b
                for a, b in zip(
                    (continued, self.plan, self.idle, continued.guard, continued.ready),
                    self.continuity_objects,
                    strict=True,
                )
            )
        )
        require(continued.finish_by == self.continuity_finish == self.plan.lease["stop_by"])
        continued._guard(min(time.monotonic() + self.MAX_SECONDS, self.continuity_finish))
        return self.plan.deadlines.stop_by, self.continuity_finish

    def _evidence(self):
        return self.continuity.read()

    @staticmethod
    def _original(evidence):
        return evidence.original


class Launch:
    """One returned durable authorization, fixed exec, actual original Ready.

    Requires the already prepared journal3 in candidate_idle, with original init
    and CLI receipts. Directories must be precreated BEFORE idle PID1 starts.
    Reopening a consumed journal cannot recreate this launch. Source/protection
    qualification surrounds create, attach and Ready; full host observations
    bracket the durable intent. No recording authorization/begin, web listener,
    App command, signal, native success, process exit or restoration is implied.

    On failure, retain any acquired Ready actor handles for independent recovery.
    close() releases those handles explicitly, never certifies their exit. The
    original idle/witness/journal remain caller-owned. The Engine Client takes
    endpoint ownership when constructed, as in its existing contract.
    """

    def __init__(
        self,
        plan,
        projected,
        journal,
        idle,
        witness,
        endpoint,
        *,
        launch_sha256,
        profile_sha256,
        read,
        qualify,
    ):
        self.owner = os.getpid(), get_ident()
        self.used = self.failed = self.closed = False
        self.confirm_attempted = False
        self.probe = None
        self.action = self.claim = self.client = self.ready = None
        try:
            require(type(plan) is plans.Plan and type(journal) is bootstrap.Journal)
            require(plans.load_bytes(plan.raw, plan.sha256) == plan)
            plan.check_projection(projected)
            require(type(idle) is idle_module.Idle and idle.plan == plan)
            require(type(witness) is engine.dispatch.process.ProcessWitness)
            require(idle.init == witness.identity and type(endpoint) is engine.Endpoint)
            require(callable(read) and callable(qualify))
            base.digest(launch_sha256)
            base.digest(profile_sha256)
            self.plan, self.projected, self.journal = plan, projected, journal
            self.idle, self.witness, self.endpoint = idle, witness, endpoint
            self.read, self.qualify = read, qualify
            self.launch_sha256 = launch_sha256
            self.profile_sha256 = profile_sha256
            self.command = execution.Command(
                str(plan.native_root / "launch/launch.json"),
                launch_sha256,
                plan.candidate_runtime.source,
                plan.lease["ready_by"],
            )
            self.command.argv()
            self.pins = engine.dispatch.Pins(
                binding.Binding(projected, plan.candidate_runtime.source, plan.sha256, plan.boot),
                self.command,
                idle.generation,
                witness.identity,
            )
            self.pins.payload()
            self._guard("candidate_idle")
        except BaseException as error:
            self._fail(error)

    def _history(self):
        """Recheck actual original journal bytes, not only a cached intent."""
        journal, plan = self.journal, self.plan
        require(type(journal) is bootstrap.Journal and journal.path == plan.root / "journal")
        journal.check_directory()
        require(0 < len(journal.entries) <= journal.max_events)
        require(
            sorted(os.listdir(journal.fd)) == [journal.name(i) for i in range(len(journal.entries))]
        )
        end = min(time.monotonic() + 2, self.command.ready_by)
        for index, entry in enumerate(journal.entries):
            name = journal.name(index)
            info = os.stat(name, dir_fd=journal.fd, follow_symlinks=False)
            require(info.st_uid == os.geteuid() and info.st_mode & 0o7777 == 0o600)
            raw = binding.protected.evidence.read_bytes(
                journal.fd, name, limit=base.MAX_BYTES, deadline=end
            )
            require(raw == base.encode(entry))
            require(
                binding.identity(os.stat(name, dir_fd=journal.fd, follow_symlinks=False))
                == binding.identity(info)
            )
        journal.check_directory()
        require(time.monotonic() < end)
        machine = journal.machine
        require(type(machine) is bootstrap.Machine)
        require(
            base.encode(journal.entries[0]["event"])
            == base.encode(plan.preparation(machine.baseline, self.projected))
        )
        require((machine.case_id, machine.boot_id) == (plan.case, plan.boot))
        require(machine.created_at == plan.deadlines.issued_at)
        require(machine.hard_deadline == plan.deadlines.recover_by)
        require(machine.bootstrap == plan.bootstrap and machine.contract == plan.candidate.contract)
        return machine

    def _guard(self, phase):
        require(not self.closed and not self.failed and self.owner == (os.getpid(), get_ident()))
        require(
            self.idle.plan == self.plan
            and self.idle.init == self.witness.identity == self.pins.init
        )
        require(not self.witness.exited())
        require(self.pins.generation == self.idle.generation)
        require(
            self.command
            == execution.Command(
                str(self.plan.native_root / "launch/launch.json"),
                self.launch_sha256,
                self.plan.candidate_runtime.source,
                self.plan.lease["ready_by"],
            )
        )
        require(self.pins.command == self.command and self.pins.host.projection == self.projected)
        require(self.pins.host.boot_id == self.plan.boot)
        require(self.pins.host.plan_sha256 == self.plan.sha256)
        require(self.pins.host.source_sha256 == self.plan.candidate_runtime.source)
        machine = self._history()
        state = machine.state
        require(state.phase == phase and state.candidate_generation == self.pins.generation)
        require(not state.finish_requested and state.operator_exit_sha256 is None)
        require(state.authorization_generation is None and state.ready_evidence_sha256 is None)
        require(machine.process_bound(base.CANDIDATE, exited=False))
        require(machine.execution_closed("starting_candidate"))
        record = next(item for item in state.processes if item.slug == base.CANDIDATE)
        require(
            (record.container_id, record.pid, record.start_ticks)
            == (self.pins.init.container_id, self.pins.init.pid, self.pins.init.start_ticks)
        )
        if phase == "candidate_idle":
            require(state.launch_intent_sha256 is None and self.action is None)
        else:
            require(type(self.action) is bootstrap.OperatorAction)
            require(state.launch_intent_sha256 == self.action.intent_sha256)
            require(state.launch_plan_sha256 == self.command.plan_sha256)
        observed = plans.clock.read()
        self.plan.check_clock(observed)
        now = observed.boottime_ns / plans.clock.NS
        require(machine.last_at <= now < min(state.deadline, self.plan.deadlines.ready_by))
        require(time.monotonic() < self.command.ready_by)
        self.endpoint.check()
        return machine, now

    def _qualified(self, phase):
        self._guard(phase)
        # The independent installed qualifier must raise on any uncertainty.
        # A boolean/serialized report is not an alternate authority interface.
        require(self.qualify() is None)
        self._guard(phase)
        evidence = self.idle.read()
        require(type(evidence) is idle_module.Evidence)
        require(
            (evidence.plan_sha256, evidence.generation, evidence.init, evidence.lease_sha256)
            == (self.plan.sha256, self.pins.generation, self.pins.init, self.plan.lease_sha256)
        )
        self._guard(phase)
        return evidence

    def _sample(self, phase):
        evidence = self._qualified(phase)
        sample, machine, now = self._read_sample(phase)
        return evidence, sample, machine, now

    def _read_sample(self, phase):
        """Fresh full host read; qualification belongs to the enclosing path."""
        self._guard(phase)
        sample = self.read()
        require(type(sample) is bootstrap.recovery.Sample)
        machine, now = self._guard(phase)
        require(sample.boot_id == self.plan.boot and sample.now <= now)
        machine.fresh(now, sample.observation)
        # This is the truthful idle PID1 collection, not healthy/recording-idle.
        candidate = sample.observation.candidate
        require(candidate.healthy is None and candidate.recording is None)
        return sample, machine, now

    def start(self):
        """Consume a fresh policy intent before any fixed Engine write, once."""
        return self._start(combined=False)

    def start_confirmed(self):
        """Single-use combined launch/Ready collection; no recording begin.

        Requires the actual original CandidateQualification, not an arbitrary
        boolean/report or previous successful qualification. The passive probe
        is prepared immediately after Ready. Its one current status read and a
        fresh full host observation are bracketed by complete new source/runtime
        inventories. The original Ready timestamp and two-second journal bound
        remain unchanged. An actual bound BootstrapHost may prepare its fixed
        read-only I/O concurrently with those inventories, then join on this
        original thread. The separate start()/confirm_ready() path is unchanged.
        """
        return self._start(combined=True)

    def _candidate_qualifier(self):
        qualifier = self.qualify
        require(type(qualifier) is CandidateQualification)
        require(
            qualifier.plan is self.plan
            and qualifier.idle is self.idle
            and qualifier.witness is self.witness
        )
        require(qualifier.generation == self.pins.generation and qualifier.init == self.pins.init)
        if type(self.read) is BootstrapHost:
            require(
                self.read.plan is self.plan
                and self.read.idle is self.idle
                and self.read.witness is self.witness
                and self.read.projected == self.projected
            )
        return qualifier

    def _start(self, *, combined):
        try:
            require(not self.used)
            self.used = True
            if combined:
                self._candidate_qualifier()
            evidence, sample, machine, now = self._sample("candidate_idle")
            event = dict(
                kind="authorize_operator",
                boot_id=self.plan.boot,
                now=now,
                generation=self.pins.generation,
                bootstrap_sha256=self.plan.bootstrap.sha256,
                launch_plan_sha256=self.command.plan_sha256,
                idle_evidence_sha256=evidence.sha256,
                observation=asdict(sample.observation),
            )
            self.action = self.journal.append(event)
            require(type(self.action) is bootstrap.OperatorAction)
            require(self.action.intent_sha256 == base.checksum(event))
            _, fresh, machine, second = self._sample("starting_operator")
            require(0 <= second - now <= 2)
            require(machine.preconditions(fresh.observation) == self.action.preconditions_sha256)
            self.claim = engine.dispatch.Claim(
                self.plan.root / "operator-exec", self.pins, self.witness
            )
            self.client = engine.Client(self.endpoint, self.claim)
            self._qualified("starting_operator")
            self.client.create()
            self._qualified("starting_operator")
            self.client.attach(finish_by=self.plan.lease["stop_by"])
            self.ready = received.Ready(
                self.client,
                profile_sha256=self.profile_sha256,
                original_clock=self.plan.original_clock,
                zero_domain=self.idle.zero_domain,
            )
            if combined:
                return self._confirm_ready(combined=True)
            self._qualified("starting_operator")
            self.ready.check_before_begin()
            return self.ready
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        collector = getattr(self, "read", None)
        if type(collector) is BootstrapHost and collector.owner == (os.getpid(), get_ident()):
            collector.discard()
        if self.ready is not None:
            self.ready.failed = True
        if self.client is not None:
            self.client.close()
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedHostLaunch(MESSAGE) from None

    def confirm_ready(self):
        """Join one actual passive probe and fresh host state to journal3 Ready.

        The original received timestamp is converted conservatively using the
        ORIGINAL clock interval; no later successful check refreshes its age.
        Import/Engine/probe/host delays must all fit the unchanged policy window.
        Failure consumes this attempt and closes the original transport, retaining
        probe/actor pidfds for independent reconciliation. This is not recording
        authorization and never starts the recorder or a browser listener.
        """
        return self._confirm_ready(combined=False)

    def _confirm_ready(self, *, combined):
        try:
            require(self.used and self.ready is not None and not self.confirm_attempted)
            self.confirm_attempted = True
            self.ready.check_before_begin()
            if combined:
                qualifier = self._candidate_qualifier()
                _, began = self._guard("starting_operator")
                self.probe = probe_exec.Sample(self.ready)
                require(self.probe.prepare() is None)
                if type(self.read) is BootstrapHost:
                    require(self.read.prepare() is None)

                def observe():
                    self.ready.check_before_begin()
                    sample, _, _ = self._read_sample("starting_operator")
                    native = self.probe.read()
                    self._check_native(native)
                    self.ready.check_before_begin()
                    return native, sample

                native, sample = qualifier.during(observe)
                require(self._candidate_qualifier() is qualifier)
            else:
                self._qualified("starting_operator")
                _, began = self._guard("starting_operator")
                self.probe = probe_exec.Sample(self.ready)
                native = self.probe.read()
                self._check_native(native)
                _, sample, _, _ = self._sample("starting_operator")
            self.ready.check_before_begin()
            machine, now = self._guard("starting_operator")
            # Preserve the oldest contributing observation, not the timestamp
            # of the most recent successful recheck.
            observation = replace(
                sample.observation,
                sampled_at=min(began, sample.observation.sampled_at),
                candidate=replace(
                    sample.observation.candidate, healthy=native.healthy, recording=native.recording
                ),
            )
            machine.fresh(now, observation)
            lower_ns = (
                Decimal(self.ready.received_at) * plans.clock.NS
                + self.plan.original_clock.offset[0]
            )
            received_at = math.nextafter(float(lower_ns / plans.clock.NS), -math.inf)
            proof = base.checksum(
                dict(
                    ready_sha256=hashlib.sha256(self.ready.ready_raw).hexdigest(),
                    probe_execution_id=self.probe.execution_id,
                    probe_request_sha256=self.probe.request_sha256,
                    native=asdict(native),
                    observation=asdict(observation),
                )
            )
            self.journal.append(
                dict(
                    kind="operator_ready",
                    boot_id=self.plan.boot,
                    now=now,
                    generation=self.pins.generation,
                    intent_sha256=self.action.intent_sha256,
                    ready_evidence_sha256=proof,
                    received_at=received_at,
                    observation=asdict(observation),
                )
            )
            machine = self._history()
            require(machine.state.phase == "candidate_running")
            require(machine.state.ready_evidence_sha256 == proof)
            require(machine.state.authorization_generation is None)
            self.plan.check_clock(plans.clock.read())
            self.ready.check_before_begin()
            return native
        except BaseException as error:
            self._fail(error)

    def _check_native(self, native):
        require(type(native) is plans.ordinary.NativeState)
        require(native.generation == self.pins.generation)
        require(native.healthy is True and native.recording is False)

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        self.closed = True
        collector = getattr(self, "read", None)
        if type(collector) is BootstrapHost:
            collector.discard()
        if self.probe is not None:
            self.probe.close()
        if self.ready is not None:
            self.ready.close()
        elif self.client is not None:
            self.client.close()


if __name__ == "__main__":
    raise SystemExit("Uninstalled host bootstrap join only; no live handoff enabled.")
