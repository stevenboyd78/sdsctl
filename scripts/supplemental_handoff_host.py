#!/usr/bin/env python3
"""Private bounded Docker/HA evidence and tracked dispatch; not a live service.

A Docker socket grants host administration, even with a read-only mount. Never
expose this module through an App, browser, public endpoint or arbitrary-command
API. Instantiation/import does not connect. A separately qualified service,
sealed identities and full observation adapter are still required for handoff.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from supplemental_handoff_policy import (
    CANDIDATE,
    NORMAL,
    Journal,
    UnsafeHandoff,
    checksum,
    digest,
    encode,
    identifier,
    reject_constant,
    require,
    unique,
)

MAX_RESPONSE = 1024 * 1024
API = "/v1.47"
CLI = "hassio_cli"
CORE = "homeassistant"
CONTROL = {
    "stopping_normal": ("ha", "apps", "stop", NORMAL, "--raw-json"),
    "starting_candidate": ("ha", "apps", "start", CANDIDATE, "--raw-json"),
    "stopping_candidate": ("ha", "apps", "stop", CANDIDATE, "--raw-json"),
    "starting_normal": ("ha", "apps", "start", NORMAL, "--raw-json"),
}
READS = {
    "apps": ("ha", "apps", "list", "--raw-json"),
    "jobs": ("ha", "jobs", "info", "--raw-json"),
    "core": ("ha", "core", "info", "--raw-json"),
    "normal": ("ha", "apps", "info", NORMAL, "--raw-json"),
    "candidate": ("ha", "apps", "info", CANDIDATE, "--raw-json"),
}


def object_json(raw: bytes) -> dict[str, Any]:
    try:
        require(type(raw) is bytes and 0 < len(raw) <= MAX_RESPONSE)
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
        require(type(value) is dict)
        return cast(dict[str, Any], value)
    except (ValueError, TypeError, RecursionError):
        raise UnsafeHandoff("Invalid bounded host response.") from None


def supervisor_data(raw: bytes) -> dict[str, Any]:
    value = object_json(raw)
    require(value.get("result") == "ok" and type(value.get("data")) is dict)
    return cast(dict[str, Any], value["data"])  # Never print options or error text.


def supervisor_jobs_idle(raw: bytes) -> bool:
    data = supervisor_data(raw)
    require(data.get("ignore_conditions") == [])
    roots = data.get("jobs")
    require(type(roots) is list and len(roots) <= 256)
    pending = [(node, 0) for node in cast(list[Any], roots)]
    seen: set[str] = set()
    idle = True
    while pending:
        node, depth = pending.pop()
        require(type(node) is dict and depth <= 16 and len(seen) < 256)
        uid = node.get("uuid")
        identifier(uid)
        require(uid not in seen and type(node.get("done")) is bool)
        require(type(node.get("errors")) is list)
        children = node.get("child_jobs")
        require(type(children) is list and len(children) <= 256)
        seen.add(uid)
        idle = idle and node["done"]
        pending.extend((child, depth + 1) for child in children)
    return idle


@dataclass(frozen=True)
class AppConfiguration:
    slug: str
    version: str
    supervisor_state: str
    settings_sha256: str
    # This is only Supervisor configuration evidence, not the complete protected
    # App pin (image/installed source/profile and process/cache state are separate).


def app_configuration(raw: bytes, *, slug: str) -> AppConfiguration:
    require(slug in (NORMAL, CANDIDATE))
    data = supervisor_data(raw)
    require(data.get("slug") == slug)
    version = data.get("version")
    require(type(version) is str and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", version) is not None)
    require(data.get("state") in ("started", "stopped", None))
    require(data.get("boot") == "manual" and data.get("protected") is True)
    for key in ("watchdog", "auto_update", "host_network", "host_pid"):
        require(data.get(key) is False)
    require(data.get("network") == {"50000/udp": None, "50443/tcp": None, "8443/tcp": None})
    require(type(data.get("options")) is dict and bool(data["options"]))
    # A redacted empty options object is not proof that private options match.
    protected = {
        key: data[key]
        for key in (
            "slug",
            "version",
            "boot",
            "protected",
            "watchdog",
            "auto_update",
            "host_network",
            "host_pid",
            "network",
            "options",
        )
    }
    return AppConfiguration(
        slug, cast(str, version), data["state"] or "unknown", checksum(protected)
    )


def docker_output(raw: bytes) -> bytes:
    """Decode non-TTY Docker stdout frames; stderr makes evidence unusable."""
    require(type(raw) is bytes and len(raw) <= MAX_RESPONSE)
    output = bytearray()
    while raw:
        require(len(raw) >= 8 and raw[0] == 1 and raw[1:4] == b"\0\0\0")
        size = int.from_bytes(raw[4:8], "big")
        require(size <= len(raw) - 8)
        output.extend(raw[8 : 8 + size])
        raw = raw[8 + size :]
    return bytes(output)


def generation(value: dict[str, Any], *, name: str, image: str) -> str:
    """Bind a healthy Docker process incarnation; no App/cache health assertion."""
    require(type(value) is dict)
    digest(value.get("Id"))
    require(value.get("Name") == "/" + name and value.get("Image") == image)
    require(type(value.get("State")) is dict)
    state = cast(dict[str, Any], value["State"])
    require(state.get("Status") == "running" and state.get("Running") is True)
    require(all(state.get(key) is False for key in ("Paused", "Restarting", "Dead", "OOMKilled")))
    require(type(state.get("Pid")) is int and state["Pid"] > 0 and state.get("Error") == "")
    started = state.get("StartedAt")
    require(type(started) is str and 20 <= len(started) <= 40 and started.endswith("Z"))
    try:
        parsed = datetime.fromisoformat(cast(str, started))
        require(parsed.tzinfo == UTC and parsed.year > 1970)
    except ValueError:
        raise UnsafeHandoff("Invalid host process start time.") from None
    return checksum({"id": value["Id"], "pid": state["Pid"], "started": started, "image": image})


def execution_state(
    value: dict[str, Any], *, execution_id: str, container_id: str, command: tuple[str, ...]
) -> str:
    """Not-running is NOT App exit, nor proof that a never-started exec ran."""
    digest(execution_id)
    digest(container_id)
    require(value.get("ID") == execution_id and value.get("ContainerID") == container_id)
    require(type(value.get("ProcessConfig")) is dict)
    process = cast(dict[str, Any], value["ProcessConfig"])
    require(process.get("entrypoint") == command[0])
    require(process.get("arguments") == list(command[1:]))
    require(process.get("privileged") is False and process.get("tty") is False)
    require(process.get("user") == "0")
    require(value.get("OpenStdin") is False and type(value.get("Running")) is bool)
    code = value.get("ExitCode")
    require("ExitCode" in value and (code is None or type(code) is int))
    require(type(value.get("Pid")) is int)
    require(value["Pid"] >= 0)
    if value["Running"]:
        return "running"
    if code is None:
        require(value["Pid"] == 0)
        return "created"
    return "not_running"


class _DeadlineSocket(socket.socket):
    """An absolute read deadline also covers slow headers and chunked bodies."""

    deadline: float

    def recv_into(self, buffer: Any, nbytes: int = 0, flags: int = 0) -> int:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Host response deadline.")
        self.settimeout(remaining)
        return super().recv_into(buffer, nbytes, flags)


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str):
        super().__init__("localhost", timeout=1.0)
        self.path = path

    def connect(self) -> None:
        sock = _DeadlineSocket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.deadline = time.monotonic() + 1.0
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class Docker:
    """Fixed local Unix transport. Never retries or follows redirects."""

    def __init__(self, path: str = "/var/run/docker.sock"):
        require(path.startswith("/") and "\0" not in path)
        self.path = path

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, expected: int = 200
    ) -> bytes:
        connection = _UnixConnection(self.path)
        try:
            connection.request(
                method,
                API + path,
                body=None if body is None else encode(body),
                headers={"Content-Type": "application/json", "Connection": "close"},
            )
            response = connection.getresponse()
            require(response.status == expected)
            deadline = time.monotonic() + 1.0
            chunks = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                require(remaining > 0)
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_RESPONSE + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
                require(len(chunks) <= MAX_RESPONSE)
            return bytes(chunks)
        except Exception:
            raise UnsafeHandoff("Host request unconfirmed; do not retry dispatch.") from None
        finally:
            connection.close()

    def container(self, identity: str) -> dict[str, Any]:
        if identity not in (CLI, CORE, "app_" + NORMAL, "app_" + CANDIDATE):
            digest(identity)
        return object_json(self._request("GET", f"/containers/{identity}/json"))

    def containers(self) -> list[dict[str, Any]]:
        raw = self._request("GET", "/containers/json?all=1")
        # Reuse strict duplicate-key/size decoding without accepting a free-form
        # URL, filter or caller-selected Docker endpoint.
        value = object_json(b'{"containers":' + raw + b"}")["containers"]
        require(type(value) is list and len(value) <= 256)
        require(all(type(item) is dict for item in value))
        return cast(list[dict[str, Any]], value)

    def image(self, identity: str) -> dict[str, Any]:
        require(type(identity) is str and identity.startswith("sha256:"))
        digest(identity[7:])
        return object_json(self._request("GET", f"/images/{identity}/json"))

    def inspect_execution(self, identity: str) -> dict[str, Any]:
        digest(identity)
        return object_json(self._request("GET", f"/exec/{identity}/json"))

    def create_execution(
        self, cli_id: str, command: tuple[str, ...], *, attach: bool = False
    ) -> str:
        digest(cli_id)
        require(type(command) is tuple and command in (*CONTROL.values(), *READS.values()))
        require(type(attach) is bool and (not attach or command in READS.values()))
        value = object_json(
            self._request(
                "POST",
                f"/containers/{cli_id}/exec",
                {
                    "AttachStdin": False,
                    "AttachStdout": attach,
                    "AttachStderr": attach,
                    "Tty": False,
                    "Privileged": False,
                    "User": "0",
                    "Cmd": command,
                },
                expected=201,
            )
        )
        digest(value.get("Id"))
        return cast(str, value["Id"])

    def start_execution(self, identity: str, *, attach: bool = False) -> bytes:
        digest(identity)
        require(type(attach) is bool)
        raw = self._request("POST", f"/exec/{identity}/start", {"Detach": not attach, "Tty": False})
        if not attach:
            require(raw == b"")
        return raw


class TrackedDispatch:
    """Fixed control-command adapter for Executor.send, with durable exec IDs.

    A sealed CLI image/generation is required. Only a newly persisted App intent
    authorizes creation. A Docker exec is created but not started until its ID is
    fsynced into that same case journal. Unknown/lost start outcomes are inspected
    read-only. There is deliberately no retry/start-existing method here.
    """

    def __init__(
        self,
        journal: Journal,
        docker: Docker,
        *,
        cli_image: str,
        cli_generation: str,
        now: Callable[[], float],
    ):
        require(cli_image.startswith("sha256:"))
        digest(cli_image[7:])
        digest(cli_generation)
        self.journal, self.docker = journal, docker
        self.image, self.generation, self.now = cli_image, cli_generation, now
        require(journal.machine is not None)
        assert journal.machine is not None
        # Construct before polling. Reopening a pending phase cannot authorize
        # replay, including an exec/create whose acknowledgement was lost.
        self.used = {p for p, _, _ in journal.machine.state.executions}
        if journal.machine.state.phase in CONTROL:
            self.used.add(journal.machine.state.phase)

    def __call__(self, command: tuple[str, ...], case_id: str) -> None:
        machine = self.journal.machine
        require(machine is not None and machine.case_id == case_id)
        assert machine is not None
        phase, observed_at = machine.state.phase, machine.last_at
        require(CONTROL.get(phase) == command)
        require(phase not in self.used)
        self.used.add(phase)
        cli = self.docker.container(CLI)
        require(generation(cli, name=CLI, image=self.image) == self.generation)
        eid = self.docker.create_execution(cli["Id"], command)
        self.journal.append(
            {
                "kind": "bind_execution",
                "boot_id": machine.boot_id,
                "now": self.now(),
                "container_id": cli["Id"],
                "execution_id": eid,
            }
        )
        require(self.journal.machine is not None and self.journal.machine.state.phase == phase)
        require(
            execution_state(
                self.docker.inspect_execution(eid),
                execution_id=eid,
                container_id=cli["Id"],
                command=command,
            )
            == "created"
        )
        fresh = self.docker.container(CLI)
        require(generation(fresh, name=CLI, image=self.image) == self.generation)
        require(0 <= self.now() - observed_at <= 2)
        self.journal.check_directory()
        self.docker.start_execution(eid)  # exactly one attempt; never infer success

    def executions_idle(self) -> bool:
        machine = self.journal.machine
        require(machine is not None)
        assert machine is not None
        idle = True
        completed = {eid for eid, _ in machine.state.completed_executions}
        for phase, cid, eid in machine.state.executions:
            if eid in completed:
                continue
            state = execution_state(
                self.docker.inspect_execution(eid),
                execution_id=eid,
                container_id=cid,
                command=CONTROL[phase],
            )
            # A start request could still be queued when inspect says created.
            # Only confirmed exit, not a not-yet-started exec, clears pending work.
            idle = idle and state == "not_running"
        return idle  # Must ALSO inspect Supervisor jobs and independent App state.

    def reconcile_executions(self) -> None:
        """Before collecting a new observation, durably retain confirmed exits.

        Docker can expire completed exec metadata. Historical, independently
        inspected exits survive that expiry; an unknown/unobserved exit does not.
        Never infer App success from the CLI exit code, including zero.
        """
        machine = self.journal.machine
        require(machine is not None)
        assert machine is not None
        if machine.state.phase in ("complete", "review"):
            return
        completed = {eid for eid, _ in machine.state.completed_executions}
        for phase, cid, eid in machine.state.executions:
            if eid in completed:
                continue
            value = self.docker.inspect_execution(eid)
            if (
                execution_state(value, execution_id=eid, container_id=cid, command=CONTROL[phase])
                == "not_running"
            ):
                self.journal.append(
                    {
                        "kind": "execution_completed",
                        "boot_id": machine.boot_id,
                        "now": self.now(),
                        "execution_id": eid,
                        "exit_code": value["ExitCode"],
                    }
                )


if __name__ == "__main__":
    raise SystemExit("Private host components only; no service, handoff or retry was started.")
