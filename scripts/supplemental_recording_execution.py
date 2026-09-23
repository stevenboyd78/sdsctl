#!/usr/bin/env python3
"""Fixed private exec description and strict read-only inspection, not dispatch.

No Docker/socket/file/process action is performed. Original inputs and the
returned inspect document need independent host provenance. A matching command
or a not-running exec is not proof of native/container-init exit or authority
to launch again. Legacy idle-only command/inspection policy is unchanged.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

MESSAGE = "Finite recording execution is unconfirmed; preserve the case and do not dispatch."
PYTHON = "/usr/local/bin/python"
ENTRY = "/opt/sdsctl-supplemental-recording/accept_supplemental_recording_operator.py"
PROBE_ENTRY = "/opt/sdsctl-supplemental-recording/accept_supplemental_recording_probe.py"
RUNTIME = "/usr/local/lib/python3.14/site-packages/sds200"


class UnconfirmedExecution(ValueError):
    """An inspection result is neither an authorization nor an exit witness."""


def require(value):
    if not value:
        raise UnconfirmedExecution(MESSAGE)


def _digest(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)


@dataclass(frozen=True)
class Command:
    plan: str
    plan_sha256: str
    source_sha256: str
    ready_by: float

    def argv(self):
        require(type(self.plan) is str and 1 <= len(self.plan) <= 1024)
        require(all(32 <= ord(char) < 127 for char in self.plan))
        path = PurePosixPath(self.plan)
        require(str(path) == self.plan and ".." not in path.parts)
        require(path.is_relative_to("/data") and path.name == "launch.json")
        require(path.parent != PurePosixPath("/data"))
        _digest(self.plan_sha256)
        _digest(self.source_sha256)
        require(type(self.ready_by) in (int, float) and math.isfinite(self.ready_by))
        require(self.ready_by > 0)
        # Temporal authorization is checked separately at dispatch. Inspection
        # after completion must still compare the SAME original argv/deadline.
        return (
            PYTHON,
            "-I",
            "-B",
            ENTRY,
            "--plan",
            self.plan,
            "--plan-sha256",
            self.plan_sha256,
            "--source-sha256",
            self.source_sha256,
            "--runtime-root",
            RUNTIME,
            "--ready-by",
            str(self.ready_by),
        )

    def create_body(self):
        """Pure description, no request. No arbitrary argv, cwd or env option.

        Engine exec inherits other container environment values. This PATH
        override does NOT sanitize those; independently pinned container/image
        environment and interpreter/dependency checks remain mandatory.
        """
        return {
            "AttachStdin": True,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
            "Privileged": False,
            "User": "0",
            "WorkingDir": "/",
            "Env": ["PATH=/usr/local/bin:/usr/bin:/bin"],
            "Cmd": list(self.argv()),
        }


@dataclass(frozen=True)
class State:
    phase: str
    pid: int
    returncode: int | None


@dataclass(frozen=True)
class ProbeCommand:
    """Fixed read-only exec metadata; deliberately not an operator Command.

    No create/attach/begin ledger can accept this type. Fresh sampling bounds
    and the original actor deadline must be checked before each new read; this
    description does not renew either and cannot authenticate its own inputs.
    """

    plan: str
    plan_sha256: str
    source_sha256: str
    probe_by: float

    def argv(self):
        args = list(Command(self.plan, self.plan_sha256, self.source_sha256, self.probe_by).argv())
        args[3], args[-2] = PROBE_ENTRY, "--probe-by"
        return tuple(args)

    def create_body(self):
        body = Command(self.plan, self.plan_sha256, self.source_sha256, self.probe_by).create_body()
        body["Cmd"] = list(self.argv())
        return body


def _inspect(value, *, execution_id, container_id, command, command_type):
    """Check exact fixed exec metadata; never infer process exit from a reply.

    running with PID 0 is the Engine's possible pre-process startup interval,
    not ready or healthy. not_running with an exit code may be failed startup,
    not successful execution. A retained live-bound pidfd and independent
    operator/native reports are still required, even for returncode 0.
    """
    try:
        _digest(execution_id)
        _digest(container_id)
        require(type(command) is command_type)
        argv = command.argv()
        require(
            type(value) is dict
            and set(value)
            == {
                "ID",
                "ContainerID",
                "ProcessConfig",
                "OpenStdin",
                "OpenStdout",
                "OpenStderr",
                "Running",
                "Pid",
                "ExitCode",
                "CanRemove",
                "DetachKeys",
            }
        )
        require(value["ID"] == execution_id and value["ContainerID"] == container_id)
        process = value["ProcessConfig"]
        require(
            type(process) is dict
            and set(process)
            == {
                "entrypoint",
                "arguments",
                "privileged",
                "tty",
                "user",
            }
        )
        require(process["entrypoint"] == argv[0] and process["arguments"] == list(argv[1:]))
        require(
            process["privileged"] is False and process["tty"] is False and process["user"] == "0"
        )
        require(all(value[name] is True for name in ("OpenStdin", "OpenStdout", "OpenStderr")))
        require(type(value["Running"]) is bool and type(value["CanRemove"]) is bool)
        require(
            value["DetachKeys"] is None
            or type(value["DetachKeys"]) is str
            and value["DetachKeys"] == ""
        )
        pid, code = value["Pid"], value["ExitCode"]
        require(type(pid) is int and 0 <= pid < 2**31)
        require(code is None or type(code) is int and 0 <= code <= 255)
        if value["Running"]:
            require(code is None and value["CanRemove"] is False and pid != 1)
            return State("starting" if pid == 0 else "running", pid, None)
        if code is None:
            require(pid == 0 and value["CanRemove"] is False)
            return State("created", 0, None)
        require(pid != 1)
        return State("not_running", pid, code)
    except Exception:
        raise UnconfirmedExecution(MESSAGE) from None


def inspect(value, *, execution_id, container_id, command):
    """Operator-only inspection; a passive probe cannot gain its authority."""
    return _inspect(
        value,
        execution_id=execution_id,
        container_id=container_id,
        command=command,
        command_type=Command,
    )


def inspect_probe(value, *, execution_id, container_id, command):
    """Probe-only metadata, not cached health or a retained process-exit proof."""
    return _inspect(
        value,
        execution_id=execution_id,
        container_id=container_id,
        command=command,
        command_type=ProbeCommand,
    )


if __name__ == "__main__":
    raise SystemExit("Fixed private exec metadata only; no host action enabled.")
