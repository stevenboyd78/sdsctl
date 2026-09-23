#!/usr/bin/env python3
"""Journal-to-dispatch bridge with injected, qualified host I/O; NOT installed.

No SSH, Docker socket, Supervisor client, shell or automatic service loop lives
here. A future private host adapter must supply bounded, trustworthy collection
and dispatch, including tracking pending commands in Observation.jobs_idle.
Reopening an existing journal never obtains old actions for dispatch.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from threading import Lock

from supplemental_handoff_policy import (
    CANDIDATE,
    NORMAL,
    Action,
    Journal,
    Machine,
    Observation,
    UnsafeHandoff,
    clock,
    identifier,
    require,
)


@dataclass(frozen=True)
class Sample:
    boot_id: str
    now: float
    observation: Observation

    def __post_init__(self) -> None:
        identifier(self.boot_id)
        Machine.fresh(self.now, self.observation)


@dataclass(frozen=True)
class Result:
    phase: str
    outcome: str
    # An emitted command is not a successful handoff or restoration result.


def cli_arguments(action: Action) -> tuple[str, ...]:
    """Fixed CLI arguments for a qualified adapter's hassio_cli execution only."""
    require(type(action) is Action)
    require(action.slug in (NORMAL, CANDIDATE) and action.operation in ("start", "stop"))
    identifier(action.case_id, case=True)
    return ("ha", "apps", action.operation, action.slug, "--raw-json")


class Executor:
    """Consume each new durable intent once, with a fresh pre-dispatch check.

    read must include *all* pending CLI/daemon jobs even after process restart.
    send submits exactly the provided argv once, without shell expansion, retries
    or a cached acknowledgement. It must return promptly with a tracked job;
    caller timeout never means that the underlying job was cancelled.
    """

    sample_type = Sample

    def __init__(
        self,
        journal: Journal,
        read: Callable[[], Sample],
        send: Callable[[tuple[str, ...], str], None],
    ) -> None:
        require(journal.fd >= 0 and journal.machine is not None)
        self.journal, self.read, self.send = journal, read, send
        self._lock = Lock()

    def poll(self) -> Result:
        # An I/O callback or another local thread cannot overlap this transaction.
        require(self._lock.acquire(blocking=False))
        try:
            return self._poll()
        finally:
            self._lock.release()

    def _poll(self) -> Result:
        machine = self.journal.machine
        if machine is None:
            raise UnsafeHandoff("Prepared handoff journal required.")
        require(self.journal.fd >= 0)
        if machine.state.phase in ("complete", "review"):
            return Result(machine.state.phase, "terminal")
        try:
            first = self.read()
            require(type(first) is self.sample_type)
        except Exception:
            # No observation means no authority to advance or issue any action.
            return Result(machine.state.phase, "observation_unavailable")
        action = self.journal.append(
            {
                "kind": "observe",
                "boot_id": first.boot_id,
                "now": first.now,
                "observation": asdict(first.observation),
            }
        )
        machine = self.journal.machine
        if machine is None:
            raise UnsafeHandoff("Prepared handoff journal required.")
        if action is None:
            return Result(machine.state.phase, "observed")
        # The fsynced journal now owns the intent. Anything below, including a
        # crash, withholds rather than recreates/retries it on the next poll.
        try:
            second = self.read()
            require(type(second) is self.sample_type)
            require(second.boot_id == first.boot_id == machine.boot_id)
            require(0 <= second.now - first.now <= 2)
            clock(second.now)
            machine.fresh(second.now, second.observation)
            require(second.now < min(machine.state.deadline, machine.hard_deadline))
            require(machine.preconditions(second.observation) == action.preconditions_sha256)
            self.journal.check_directory()
            command = cli_arguments(action)
        except Exception:
            return Result(machine.state.phase, "intent_withheld")
        try:
            self.send(command, action.case_id)
        except Exception:
            # Never log exception text: an adapter can include credentials or
            # CLI output in it. Reconciliation, not another command, comes next.
            return Result(machine.state.phase, "dispatch_unconfirmed")
        return Result(machine.state.phase, "dispatch_submitted")


if __name__ == "__main__":
    raise SystemExit("Injected-I/O bridge only; a qualified independent host adapter is required.")
