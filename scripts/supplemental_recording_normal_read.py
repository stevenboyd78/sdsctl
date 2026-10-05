#!/usr/bin/env python3
"""One normal-App cached read bound to an original schema3 recording plan.

This does not qualify the helper/runtime or the App's protected content. The
complete host observer must check those independently before invoking it, and
join original process/CLI exit evidence before authorizing restoration. There
is deliberately no candidate, recording command, or arbitrary-path interface.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from threading import Lock, get_ident

import supplemental_handoff_app_read as cached
import supplemental_recording_host_plan as plans

MESSAGE = "Normal App cached read is unconfirmed; preserve the original recording case."
MAX_SECONDS = 2.0


class UnconfirmedNormalRead(ValueError):
    """A failed or late cache read is not healthy, idle, or restored."""


def require(value):
    if not value:
        raise UnconfirmedNormalRead(MESSAGE)


class Sample:
    """A single fixed read, including after a separately authorized restoration.

    The caller supplies the freshly observed normal generation, which is checked
    against actual Engine metadata before and after the exec. A restored normal
    generation need not equal the pre-handoff one; this reader cannot establish
    that the intervening ownership transfer was safe. Full HostObserver and
    RecoverySession evidence remain necessary. No result or command is retried.
    """

    def __init__(self, plan, docker):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.used = self.failed = False
        try:
            require(type(plan) is plans.Plan and plans.load_bytes(plan.raw, plan.sha256) == plan)
            require(type(docker) is plans.ordinary.Docker and docker.path == "/var/run/docker.sock")
            self.plan, self.docker = plan, docker
            layout = next(item for item in plan.layouts if item.slug == plans.base.NORMAL)
            self.paths = cached.ProbePaths(
                str(Path("/data") / layout.deployment.relative_to(layout.data)),
                str(Path("/media") / layout.recordings.relative_to(layout.media)),
            )
            self.command = cached.probe_command(
                candidate=False,
                case=plan.case,
                source=plan.source,
                firmware=plan.firmware,
                paths=self.paths,
            )
            self.original = plan.raw, self.paths, self.command
            self.original_docker = docker
        except BaseException as error:
            self._fail(error)

    def _guard(self):
        require(not self.failed and self.owner == (os.getpid(), get_ident()))
        require(type(self.plan) is plans.Plan)
        require((self.plan.raw, self.paths, self.command) == self.original)
        require(plans.load_bytes(self.plan.raw, self.plan.sha256) == self.plan)
        require(
            self.docker is self.original_docker
            and type(self.docker) is plans.ordinary.Docker
            and self.docker.path == "/var/run/docker.sock"
        )
        observed = plans.clock.read()
        self.plan.check_clock(observed)
        return observed

    def read(self, slug, incarnation):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(not self.used)
            self.used = True
            began = time.monotonic()
            before = self._guard()
            require(slug == plans.base.NORMAL)
            plans.base.digest(incarnation)
            result = cached._read_probe(self.docker, self.plan.normal, self.command, incarnation)
            after = self._guard()
            before.check_later(after)
            require(0 <= time.monotonic() - began <= MAX_SECONDS)
            require(0 <= (after.boottime_ns - before.boottime_ns) / plans.clock.NS <= MAX_SECONDS)
            require(type(result) is plans.ordinary.NativeState and result.generation == incarnation)
            return result
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedNormalRead(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Fixed normal-App cache reader only; no ownership transfer enabled.")
