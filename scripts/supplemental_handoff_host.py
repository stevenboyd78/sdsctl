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
from pathlib import Path
from typing import Any, cast

from supplemental_handoff_policy import (
    CANDIDATE,
    MAX_BYTES,
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
READER_NETWORK = "reader-only"
AUDIO_NETWORK = "candidate-rtp-50000-v1"
HOST_UDP_TABLES = Path("/opt/sdsctl-host-udp")
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


def network_policy(value: object) -> None:
    require(type(value) is str and value in (READER_NETWORK, AUDIO_NETWORK))


def app_configuration(raw: bytes, *, slug: str, network: str = READER_NETWORK) -> AppConfiguration:
    network_policy(network)
    require(slug in (NORMAL, CANDIDATE))
    data = supervisor_data(raw)
    require(data.get("slug") == slug)
    version = data.get("version")
    require(type(version) is str and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", version) is not None)
    require(data.get("state") in ("started", "stopped", None))
    require(data.get("boot") == "manual" and data.get("protected") is True)
    for key in ("watchdog", "auto_update", "host_network", "host_pid"):
        require(data.get(key) is False)
    ports: dict[str, int | None] = {"50000/udp": None, "50443/tcp": None, "8443/tcp": None}
    if network == AUDIO_NETWORK and slug == CANDIDATE:
        ports["50000/udp"] = 50000
    actual = data.get("network")
    require(type(actual) is dict and actual == ports)
    require(all(type(actual[key]) is type(value) for key, value in ports.items()))
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


def audio_container_network(value: dict[str, Any], *, candidate: bool) -> None:
    """Actual bridge mappings, not just the Supervisor's requested settings."""
    require(type(candidate) is bool)
    config, runtime = value.get("HostConfig"), value.get("NetworkSettings")
    require(type(config) is dict and type(runtime) is dict)
    require(config.get("NetworkMode") == "bridge" and config.get("PublishAllPorts") is False)
    bindings, ports = config.get("PortBindings"), runtime.get("Ports")
    if not candidate:
        require(bindings == {} and ports == {})
        return
    require(type(bindings) is dict and set(bindings) == {"50000/udp"})
    require(
        bindings["50000/udp"]
        in (
            [{"HostIp": "", "HostPort": "50000"}],
            [{"HostIp": "0.0.0.0", "HostPort": "50000"}],
        )
    )
    require(type(ports) is dict and set(ports) == {"50000/udp"})
    active = ports["50000/udp"]
    require(type(active) is list and 1 <= len(active) <= 2)
    addresses = []
    for item in active:
        require(type(item) is dict and set(item) == {"HostIp", "HostPort"})
        require(item["HostPort"] == "50000" and item["HostIp"] in ("0.0.0.0", "::"))
        addresses.append(item["HostIp"])
    require(len(set(addresses)) == len(addresses) and "0.0.0.0" in addresses)


def rtp_port_owners(values: list[dict[str, Any]]) -> tuple[str, ...]:
    """Reject another Docker publication of UDP 50000; retain stable evidence."""
    require(type(values) is list and len(values) <= 256)
    owners = []
    for value in values:
        ports = value.get("Ports")
        require(type(ports) is list and len(ports) <= 256)
        for port in ports:
            require(type(port) is dict)
            require(port.get("Type") in ("tcp", "udp", "sctp"))
            private = port.get("PrivatePort")
            require(type(private) is int and 1 <= private <= 65535)
            if "PublicPort" not in port:
                continue  # Exposed but not published is not a host-port owner.
            public = port["PublicPort"]
            require(type(public) is int and 1 <= public <= 65535)
            if public != 50000 or port["Type"] != "udp":
                continue
            require(value.get("Names") == ["/app_" + CANDIDATE])
            require(value.get("State") == "running" and private == 50000)
            require(port.get("IP") in ("0.0.0.0", "::"))
            digest(value.get("Id"))
            owners.append(value["Id"] + "/" + port["IP"])
    require(len(owners) <= 2 and len(set(owners)) == len(owners))
    return tuple(sorted(owners))


def udp_table_idle(raw: bytes, *, ipv6: bool) -> bool:
    """Bounded proc UDP table check; never binds, reserves or opens a port."""
    require(type(raw) is bytes and 0 < len(raw) <= 512 * 1024 and type(ipv6) is bool)
    lines = raw.decode("ascii").splitlines()
    require(1 <= len(lines) <= 4097 and "local_address" in lines[0].split())
    idle = True
    for line in lines[1:]:
        parts = line.split()
        require(len(parts) >= 10 and re.fullmatch(r"\d+:", parts[0]) is not None)
        require(
            re.fullmatch(r"[0-9A-F]{" + ("32" if ipv6 else "8") + r"}:[0-9A-F]{4}", parts[1])
            is not None
        )
        idle = idle and int(parts[1].split(":")[1], 16) != 50000
    return idle


def require_host_rtp_idle() -> None:
    """Read exact host-init proc files mounted read-only by the sealed launcher.

    Do not use /proc/self/net: the networkless helper has an empty namespace.
    Direct /proc/1 access requires extra privileges on HAOS; the launcher instead
    mounts only /proc/1/net/udp and udp6, without adding SYS_PTRACE or networking.
    Fail closed on missing, inaccessible or oversized tables.
    This is a fresh observation, not an atomic reservation or packet-delivery proof.
    """
    for name, ipv6 in (("udp", False), ("udp6", True)):
        with (HOST_UDP_TABLES / name).open("rb") as stream:
            require(udp_table_idle(stream.read(512 * 1024 + 1), ipv6=ipv6))


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


@dataclass(frozen=True)
class DispatchNotice:
    """Immutable evidence request, NOT an action permit or peer authentication.

    A separately qualified observer must read the original journal/Engine for
    itself. Returning this receipt only acknowledges custody of these exact
    bytes; it cannot replace the existing policy, consent or execution bounds.
    """

    stage: str
    phase: str
    case_id: str
    boot_id: str
    container_id: str
    execution_id: str | None
    history: tuple[bytes, ...]

    def __post_init__(self):
        require(type(self.stage) is str and self.stage in ("before_create", "before_start"))
        require(type(self.phase) is str and self.phase in CONTROL)
        identifier(self.case_id, case=True)
        identifier(self.boot_id)
        digest(self.container_id)
        if self.stage == "before_create":
            require(self.execution_id is None)
        else:
            digest(self.execution_id)
        require(type(self.history) is tuple and 0 < len(self.history) <= 64)
        require(all(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES for raw in self.history))

    @property
    def receipt(self) -> str:
        return checksum(
            [
                self.stage,
                self.phase,
                self.case_id,
                self.boot_id,
                self.container_id,
                self.execution_id,
                [raw.hex() for raw in self.history],
            ]
        )


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
        observe: Callable[[DispatchNotice], str] | None = None,
    ):
        require(cli_image.startswith("sha256:"))
        digest(cli_image[7:])
        digest(cli_generation)
        self.journal, self.docker = journal, docker
        self.image, self.generation, self.now = cli_image, cli_generation, now
        require(observe is None or callable(observe))
        # Optional only for the existing uninstalled adapters. An active command
        # must explicitly select/authenticate an independent observer. This hook
        # does not make arbitrary callbacks trustworthy or select such a command.
        self.observe = self._original_observe = observe
        require(journal.machine is not None)
        assert journal.machine is not None
        # Construct before polling. Reopening a pending phase cannot authorize
        # replay, including an exec/create whose acknowledgement was lost.
        self.used = {p for p, _, _ in journal.machine.state.executions}
        if journal.machine.state.phase in CONTROL:
            self.used.add(journal.machine.state.phase)

    def __call__(self, command: tuple[str, ...], case_id: str) -> None:
        require(self.observe is self._original_observe)
        machine = self.journal.machine
        require(machine is not None and machine.case_id == case_id)
        assert machine is not None
        phase, observed_at = machine.state.phase, machine.last_at
        require(CONTROL.get(phase) == command)
        require(phase not in self.used)
        self.used.add(phase)
        cli = self.docker.container(CLI)
        require(generation(cli, name=CLI, image=self.image) == self.generation)
        self._observed("before_create", phase, cli["Id"], None, observed_at)
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
        self._observed("before_start", phase, cli["Id"], eid, observed_at)
        self.docker.start_execution(eid)  # exactly one attempt; never infer success

    def _observed(self, stage, phase, cid, eid, observed_at):
        require(self.observe is self._original_observe)
        if self.observe is None:
            return  # Preserve the old non-supervised, uninstalled API.
        journal, docker, image, generation_pin, clock = (
            self.journal,
            self.docker,
            self.image,
            self.generation,
            self.now,
        )
        journal.check_directory()
        machine = journal.machine
        require(machine is not None and machine.state.phase == phase)
        notice = DispatchNotice(
            stage,
            phase,
            machine.case_id,
            machine.boot_id,
            cid,
            eid,
            tuple(encode(entry) for entry in journal.entries),
        )
        expected = notice.receipt
        require(0 <= clock() - observed_at <= 2)
        acknowledged = self.observe(notice)
        require(type(acknowledged) is str and acknowledged == expected)
        require(notice.receipt == expected and self.observe is self._original_observe)
        require(self.journal is journal and self.docker is docker and self.now is clock)
        require(self.image == image and self.generation == generation_pin)
        require(journal.machine is machine and machine.state.phase == phase)
        require(tuple(encode(entry) for entry in journal.entries) == notice.history)
        journal.check_directory()
        # The synchronous observer can consume time. Recheck the original CLI
        # generation and interval after its receipt, before either mutation.
        fresh = docker.container(CLI)
        require(fresh["Id"] == cid)
        require(generation(fresh, name=CLI, image=image) == generation_pin)
        if eid is not None:
            require(
                execution_state(
                    docker.inspect_execution(eid),
                    execution_id=eid,
                    container_id=cid,
                    command=CONTROL[phase],
                )
                == "created"
            )
        require(0 <= clock() - observed_at <= 2)

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
