#!/usr/bin/env python3
"""Read-only Linux process-exit witness, not an App controller or service.

Must run in both the host PID and host cgroup namespaces. A host PID view alone
does not make /proc/PID/cgroup paths host-relative. The caller must independently
verify Docker image/container generation before AND after binding its init
process. This does not prove Supervisor completion, absence of a replacement
owner or restoration.
No signals are sent; loss of a witness is uncertainty, not proof of exit.
"""

from __future__ import annotations

import os
import re
import select
from dataclasses import dataclass

from supplemental_handoff_policy import digest, require


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    container_id: str

    def __post_init__(self) -> None:
        require(type(self.pid) is int and self.pid > 1)
        require(type(self.start_ticks) is int and self.start_ticks > 0)
        digest(self.container_id)


def process_identity(pid: int, container_id: str, stat_text: str, cgroup: str) -> ProcessIdentity:
    """Decode bounded host /proc evidence; only the observed HAOS v2 layout."""
    require(type(pid) is int and pid > 1)
    digest(container_id)
    require(type(stat_text) is str and len(stat_text) <= 4096)
    require(type(cgroup) is str and len(cgroup) <= 4096)
    # comm may contain spaces, parentheses and newlines. The final ')' precedes
    # the fixed stat fields, including field 22 (starttime), not arbitrary text.
    first, closing, rest = stat_text.rpartition(") ")
    require(bool(closing) and first.startswith(str(pid) + " ("))
    fields = rest.split()
    require(len(fields) >= 20 and fields[0] in {"R", "S", "D", "T", "t", "I"})
    require(re.fullmatch(r"[1-9][0-9]*", fields[19]) is not None)
    # An exact unified Docker scope, not substring containment or a namespace's
    # '/' or '/../...' view. Unknown layouts require review, not normalization
    # of a relative cgroup path or acceptance of another PID.
    require(cgroup == f"0::/system.slice/docker-{container_id}.scope\n")
    return ProcessIdentity(pid, int(fields[19]), container_id)


def read_identity(pid: int, container_id: str) -> ProcessIdentity:
    require(type(pid) is int and pid > 1)
    digest(container_id)

    def read(name: str) -> str:
        with open(f"/proc/{pid}/{name}", "rb", buffering=0) as stream:
            raw = stream.read(4097)
        require(len(raw) <= 4096)
        return raw.decode("ascii")

    return process_identity(pid, container_id, read("stat"), read("cgroup"))


class ProcessWitness:
    """Bind a currently live incarnation, then poll its pidfd without signaling.

    The fd cannot be reconstructed from a saved numeric PID after a restart.
    Reopening is allowed only with the exact previously retained identity while
    that process is still live; an absent/recycled PID cannot manufacture an exit
    receipt. Independently persisted exits need the policy journal integration.
    """

    def __init__(self, expected: ProcessIdentity):
        require(type(expected) is ProcessIdentity)
        self.identity, self.fd = expected, -1
        try:
            require(read_identity(expected.pid, expected.container_id) == expected)
            self.fd = os.pidfd_open(expected.pid, 0)
            require(read_identity(expected.pid, expected.container_id) == expected)
            require(not self.exited())
        except Exception:
            self.close()
            # Never print proc text, local paths or exception messages.
            from supplemental_handoff_policy import UnsafeHandoff

            raise UnsafeHandoff("Process witness unavailable; exit remains unconfirmed.") from None

    def exited(self) -> bool:
        require(self.fd >= 0)
        poller = select.poll()
        poller.register(self.fd, select.POLLIN)
        events = poller.poll(0)
        if not events:
            return False
        require(len(events) == 1 and events[0][0] == self.fd)
        # HUP may accompany readability after reaping. ERR/NVAL without a
        # readable pidfd must never be interpreted as the target exiting.
        require(events[0][1] in (select.POLLIN, select.POLLIN | select.POLLHUP))
        return True

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> ProcessWitness:
        require(self.fd >= 0)
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


if __name__ == "__main__":
    raise SystemExit("Read-only process witness only; no App, process or scanner was stopped.")
