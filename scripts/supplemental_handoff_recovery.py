#!/usr/bin/env python3
"""Private process receipts and journal/executor integration; not installed.

The full host observer remains responsible for fresh source/image/profile/options,
other-owner, Supervisor/job and native-cache evidence. These gates add process
proof; they never turn a partial observation into a complete host observation.
No automatic start, CLI, deployment, PID signaling or scanner import is provided.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, replace

from supplemental_handoff_executor import Executor, Result, Sample
from supplemental_handoff_host import CONTROL, Docker, TrackedDispatch, generation
from supplemental_handoff_policy import (
    CANDIDATE,
    NORMAL,
    TOTAL_SECONDS,
    App,
    Journal,
    ProcessRecord,
    clock,
    digest,
    identifier,
    require,
)
from supplemental_handoff_process import ProcessIdentity, ProcessWitness, read_identity


class TrackedProcesses:
    """Bind live init processes and fsync confirmed exits before using them.

    Construct in both host PID and cgroup namespaces before any App handoff. On
    restart, exact still-live processes may be witnessed again; missing/dead processes
    cannot create a new exit receipt. Previously durable exits remain usable,
    but are never sufficient without fresh independent host observations.
    """

    def __init__(
        self,
        journal: Journal,
        docker: Docker,
        *,
        images: dict[str, str],
        read_clock: Callable[[], tuple[str, float]],
    ):
        require(journal.machine is not None and journal.fd >= 0)
        require(set(images) == {NORMAL, CANDIDATE})
        for image in images.values():
            require(type(image) is str and image.startswith("sha256:"))
            digest(image[7:])
        self.journal, self.docker = journal, docker
        self.images, self.read_clock = dict(images), read_clock
        self.witnesses: dict[str, ProcessWitness] = {}
        self.closed = False

    def _clock(self) -> tuple[str, float]:
        require(not self.closed and self.journal.fd >= 0)
        machine = self.journal.machine
        require(machine is not None)
        assert machine is not None
        boot, now = self.read_clock()
        identifier(boot)
        clock(now)
        require(boot == machine.boot_id and machine.last_at <= now < machine.hard_deadline)
        require(machine.state.phase not in ("review", "complete"))
        self.journal.check_directory()
        return boot, now

    def record(self, slug: str) -> ProcessRecord | None:
        require(slug in self.images)
        machine = self.journal.machine
        require(machine is not None)
        assert machine is not None
        return next((r for r in machine.state.processes if r.slug == slug), None)

    def exit_confirmed(self, slug: str) -> bool:
        self._clock()
        record = self.record(slug)
        assert self.journal.machine is not None
        return (
            record is not None and record.generation in self.journal.machine.state.exited_processes
        )

    def bind_running(self, slug: str, expected_generation: str) -> ProcessRecord:
        boot, started = self._clock()
        require(slug in self.images)
        digest(expected_generation)
        old = self.record(slug)
        assert self.journal.machine is not None
        require(not self.exit_confirmed(slug))
        before = self.docker.container("app_" + slug)
        observed = generation(before, name="app_" + slug, image=self.images[slug])
        require(observed == expected_generation)
        identity = read_identity(before["State"]["Pid"], before["Id"])
        record = ProcessRecord(
            slug, observed, identity.container_id, identity.pid, identity.start_ticks
        )
        require(old is None or old == record)
        witness = self.witnesses.get(slug)
        fresh = witness is None
        if fresh:
            witness = ProcessWitness(identity)
        assert witness is not None
        try:
            require(witness.identity == identity and not witness.exited())
            after = self.docker.container("app_" + slug)
            require(generation(after, name="app_" + slug, image=self.images[slug]) == observed)
            end_boot, now = self._clock()
            require(end_boot == boot and 0 <= now - started <= 2)
            if old is None:
                self.journal.append(
                    {
                        "kind": "bind_process",
                        "boot_id": boot,
                        "now": now,
                        "process": asdict(record),
                    }
                )
                require(self.record(slug) == record)
            self.witnesses[slug] = witness
            # If exit races the durable bind, keep this handle for reconciliation
            # rather than discarding the only independent process-exit evidence.
            require(not witness.exited())
            return record
        except BaseException:
            if fresh and self.witnesses.get(slug) is not witness:
                witness.close()
            raise

    def reconcile(self) -> None:
        """Retain actual readable pidfd exits, never infer from Docker absence."""
        self._clock()
        assert self.journal.machine is not None
        for record in self.journal.machine.state.processes:
            if self.exit_confirmed(record.slug):
                continue
            witness = self.witnesses.get(record.slug)
            if witness is None:
                # A process may exit during this reopen. That is unconfirmed;
                # it cannot retroactively establish a lost in-memory witness.
                self.bind_running(record.slug, record.generation)
                witness = self.witnesses[record.slug]
            require(
                witness.identity
                == ProcessIdentity(record.pid, record.start_ticks, record.container_id)
            )
            if witness.exited():
                boot, now = self._clock()
                self.journal.append(
                    {
                        "kind": "process_exited",
                        "boot_id": boot,
                        "now": now,
                        "generation": record.generation,
                    }
                )
                require(self.exit_confirmed(record.slug))
                witness.close()
                del self.witnesses[record.slug]

    def close(self) -> None:
        for witness in self.witnesses.values():
            witness.close()
        self.witnesses.clear()
        self.closed = True


class RecoverySession:
    """One bounded poll bridge requiring process receipts as an additional gate.

    Does not collect the full host observation or run a background service. A
    qualified caller supplies read and fresh boot-relative read_clock. Create
    once before polling; closing preserves journal evidence and signals nothing.
    """

    sample_type = Sample
    executor_type = Executor

    def __init__(
        self,
        journal: Journal,
        processes: TrackedProcesses,
        dispatch: TrackedDispatch,
        read: Callable[[], Sample],
        *,
        consume_operator: Callable[[], bool] | None = None,
    ):
        require(processes.journal is journal and dispatch.journal is journal)
        self.journal, self.processes, self.dispatch, self.read = journal, processes, dispatch, read
        self.consume_operator = consume_operator
        self.executor = self.executor_type(journal, self._read, self._send)

    def _read(self) -> Sample:
        require(self.journal.machine is not None)
        process_uncertain = False
        executions_idle = False
        try:
            self.processes.reconcile()
        except Exception:
            process_uncertain = True
        try:
            self.dispatch.reconcile_executions()
            executions_idle = self.dispatch.executions_idle()
        except Exception:
            pass
        sample = self.read()
        require(type(sample) is self.sample_type)
        machine = self.journal.machine
        assert machine is not None
        apps = {}
        for slug, app in (
            (NORMAL, sample.observation.normal),
            (CANDIDATE, sample.observation.candidate),
        ):
            uncertain = process_uncertain
            try:
                if app.state == "running":
                    # The restored normal process must be independently checked
                    # by read(). It is not one of the original processes stopped
                    # in this case and must not replace that durable binding.
                    restored = slug == NORMAL and machine.state.phase == "starting_normal"
                    if not restored:
                        assert app.generation is not None
                        self.processes.bind_running(slug, app.generation)
                elif app.state == "stopped":
                    record = self.processes.record(slug)
                    if slug == NORMAL or record is not None:
                        uncertain = uncertain or not self.processes.exit_confirmed(slug)
            except Exception:
                uncertain = True
            apps[slug] = App(app.pin, "unknown") if uncertain else app
        boot, now = self.processes.read_clock()
        require(boot == sample.boot_id)
        return self.sample_type(
            boot,
            now,
            replace(
                sample.observation,
                normal=apps[NORMAL],
                candidate=apps[CANDIDATE],
                jobs_idle=sample.observation.jobs_idle and executions_idle,
            ),
        )

    def _send(self, command: tuple[str, ...], case_id: str) -> None:
        machine = self.journal.machine
        require(machine is not None)
        assert machine is not None
        require(CONTROL.get(machine.state.phase) == command)
        if command[2] == "stop":
            record = self.processes.record(command[3])
            require(record is not None)
            assert record is not None
            self.processes.bind_running(record.slug, record.generation)
        else:
            owner = NORMAL if command[3] == CANDIDATE else CANDIDATE
            require(self.processes.exit_confirmed(owner))
        self.dispatch(command, case_id)

    def poll(self) -> Result:
        machine = self.journal.machine
        require(machine is not None)
        assert machine is not None
        if machine.state.phase not in ("complete", "review"):
            # Clock-only expiry is independent of a healthy Docker/Supervisor
            # response. A read timeout cannot indefinitely preserve a live case.
            boot, now = self.processes.read_clock()
            self.journal.append({"kind": "tick", "boot_id": boot, "now": now})
            if self.consume_operator is not None:
                # Bad/missing/expired operator input cannot authorize an
                # action or disable the independent expiry/recovery path.
                # Journal I/O failure still fails closed inside Executor.
                with suppress(Exception):
                    self.consume_operator()
        return self.executor.poll()

    def run(self, wait: Callable[[float], None]) -> Result:
        """Finite loop for an independently supervised caller with bounded I/O.

        Caller must not block wait beyond the supplied 0.25s. Host runtime must
        enforce an outer deadline in case an I/O callback/kernel stops returning.
        No result except a policy-complete phase certifies normal-App restoration.
        A separate operator interface must persist request/finish through the
        locked service owner; this loop does not invent an automatic request.
        """
        try:
            for _ in range(TOTAL_SECONDS * 4 + 1):
                result = self.poll()
                if result.phase in ("complete", "review"):
                    return result
                wait(0.25)
            # Defensive finite ceiling for a non-advancing injected clock. Never
            # publish a successful restoration or create another journal/case.
            assert self.journal.machine is not None
            return Result(self.journal.machine.state.phase, "poll_limit_unconfirmed")
        finally:
            self.close()

    def close(self) -> None:
        self.processes.close()


if __name__ == "__main__":
    raise SystemExit("Private recovery components only; no service or handoff was started.")
