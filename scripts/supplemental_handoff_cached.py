#!/usr/bin/env python3
"""Read installed profile files and cached daemon IPC only, without acquisition.

Run inside the separately source/image-qualified App. This does not inspect the
image, case guardian, other owners, recording files or host process incarnation;
the complete host observer must join those independent checks. Never expose this
private collector as a public API or accept user-supplied paths over a network.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import socket
import struct
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


class UnconfirmedCache(ValueError):
    def __init__(self) -> None:
        super().__init__("Cached App evidence is unconfirmed.")


def require(value: bool) -> None:
    if not value:
        raise UnconfirmedCache()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checksum(value: Any) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def reject_constant(_value: str) -> None:
    raise UnconfirmedCache()


class CachedClient:
    """Five fixed cached IPC requests, without importing the full server graph.

    This private one-shot reader is not a general daemon client. Sequence, wire
    version, size, correlation and elapsed bounds are strict; no command/demand
    method, reconnect, retry or caller-selected operation is provided.
    """

    SEQUENCE = (
        "hello",
        "runtime.snapshot",
        "recording.status",
        "display.profile",
        "runtime.snapshot",
    )

    def __init__(self, path: Path, *, timeout: float):
        require(timeout == 0.2 and type(timeout) is float)
        require(type(path) is type(Path()) and path.is_absolute() and ".." not in path.parts)
        self.path, self.timeout = path, timeout
        self.socket: socket.socket | None = None
        self.sequence = 0

    def __enter__(self) -> CachedClient:
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.socket.settimeout(self.timeout)
            self.socket.connect(str(self.path))
            return self
        except BaseException:
            self.socket.close()
            self.socket = None
            raise

    def __exit__(self, *_: object) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def connect(self) -> socket.socket:
        require(self.socket is not None)
        assert self.socket is not None
        return self.socket

    def _read(self, operation: str) -> dict[str, Any]:
        require(self.sequence < len(self.SEQUENCE) and operation == self.SEQUENCE[self.sequence])
        self.sequence += 1  # Failure consumes this request; it cannot be replayed.
        request_id = f"handoff-cache-{self.sequence}"
        stream = self.connect()
        deadline = time.monotonic() + self.timeout
        stream.settimeout(self.timeout)
        stream.sendall(
            (
                json.dumps(
                    dict(
                        protocol="sdsctl.daemon",
                        version=1,
                        request_id=request_id,
                        operation=operation,
                        params={},
                    ),
                    separators=(",", ":"),
                )
                + "\n"
            ).encode()
        )
        raw = bytearray()
        while not raw.endswith(b"\n"):
            remaining = deadline - time.monotonic()
            require(remaining > 0 and len(raw) <= 1024 * 1024)
            stream.settimeout(remaining)
            chunk = stream.recv(min(65536, 1024 * 1024 + 1 - len(raw)))
            require(bool(chunk))
            raw.extend(chunk)
        require(len(raw) <= 1024 * 1024 and raw.count(b"\n") == 1)
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
        require(
            type(value) is dict
            and set(value) == {"protocol", "version", "request_id", "ok", "result"}
        )
        require(
            value["protocol"] == "sdsctl.daemon"
            and type(value["version"]) is int
            and value["version"] == 1
        )
        require(
            value["request_id"] == request_id
            and value["ok"] is True
            and type(value["result"]) is dict
        )
        require(time.monotonic() <= deadline)
        return cast(dict[str, Any], value["result"])

    def hello(self) -> dict[str, Any]:
        value = self._read("hello")
        require(
            value.get("protocol") == "sdsctl.daemon"
            and type(value.get("selected_version")) is int
            and value["selected_version"] == 1
        )
        versions = value.get("supported_versions")
        require(type(versions) is list and all(type(v) is int for v in versions) and 1 in versions)
        require(type(value.get("read_only")) is bool)
        operations = value.get("operations")
        require(type(operations) is list and all(type(v) is str for v in operations))
        require(set(self.SEQUENCE) <= set(cast(list[str], operations)))
        return value

    def runtime_snapshot(self) -> dict[str, Any]:
        return self._read("runtime.snapshot")

    def recording_status(self) -> dict[str, Any]:
        return self._read("recording.status")

    def request(self, operation: str) -> dict[str, Any]:
        require(operation == "display.profile")
        return self._read("display.profile")


@dataclass(frozen=True)
class CachedEvidence:
    profile_sha256: str
    healthy: bool
    recording: bool
    supplemental_advertised: bool
    peer_pid: int
    peer_start_ticks: str


def process_ticks(pid: int) -> str:
    require(type(pid) is int and pid > 1)
    with open(f"/proc/{pid}/stat", "rb", buffering=0) as stream:
        raw = stream.read(4097)
    require(len(raw) <= 4096)
    first, delimiter, last = raw.decode("ascii").rpartition(") ")
    require(bool(delimiter) and first.startswith(str(pid) + " ("))
    fields = last.split()
    require(len(fields) >= 20 and fields[0] in ("R", "S", "D", "T", "t", "I"))
    ticks = fields[19]
    require(ticks.isascii() and ticks.isdigit() and int(ticks) > 0)
    return ticks


def profile_files(deployment_path: Path, recordings: Path) -> tuple[str, dict[str, Any]]:
    """Validate accepted/source binding and hash private bytes without exporting them."""
    from sds200.scanner_display_configuration import (
        _read_configuration,
        load_scanner_display_configuration,
    )
    from sds200.scanner_display_profile_storage import _access, _read_state
    from sds200.scanner_display_upload import _observe_source

    # Bound readers already reject symlinks, hardlinks, unsafe owners/permissions,
    # oversized files and observed replacement. No profile import or reload.
    deployment_bytes = _read_configuration(deployment_path)
    # The independently sealed deployment bytes have already passed deployment
    # validation. We need only their profile path here, not the web upload/origin
    # validators (which import the entire web stack on every short-lived probe).
    deployment = tomllib.loads(deployment_bytes.decode("utf-8"))
    require(
        set(deployment)
        == {"version", "profile_config", "ingress_origin", "admin_user_ids", "allow_upload"}
    )
    require(type(deployment["version"]) is int and deployment["version"] == 1)
    require(type(deployment["profile_config"]) is str)
    config_path = Path(deployment["profile_config"])
    require(config_path.is_absolute() and ".." not in config_path.parts)
    config_bytes = _read_configuration(config_path)
    config = load_scanner_display_configuration(config_path)
    config.require_separate_recordings(recordings)
    require(not config_path.is_relative_to(recordings.resolve()))
    with _access(config.state_directory, exclusive=False) as directory:
        accepted = _read_state(directory, config.binding.endpoint_id)
        source = _observe_source(config.source_path)
        require(source is not None)
        assert source is not None
        require(accepted.accepted is not None)
        assert accepted.accepted is not None
        require(accepted.accepted.provenance.binding == config.binding)
        require(source.data == accepted.source)
        fingerprint = checksum(
            {
                "deployment": sha(deployment_bytes),
                "configuration": sha(config_bytes),
                "accepted": sha(accepted.file.data),
                "source": sha(source.data),
            }
        )
        # Private matching values stay inside this process, never in its report.
        matching = {
            "target": config.scanner_target,
            "endpoint_id": str(config.binding.endpoint_id),
            "source_id": str(config.binding.source_id),
            "source_kind": config.binding.source_kind.value,
            "revision": accepted.accepted.profile.revision,
        }
    require(_read_configuration(deployment_path) == deployment_bytes)
    require(_read_configuration(config_path) == config_bytes)
    return fingerprint, matching


def collect_cached(
    deployment_path: Path,
    recordings: Path,
    daemon_socket: Path,
    *,
    firmware: str,
    supplemental: bool,
) -> CachedEvidence:
    """New IPC connection, five cached reads, 1.5s total collection budget.

    Supplemental CONTEXT/FRAME/DEMAND, display reload, scanner probes and controls
    are deliberately never requested. Advertised support does not arm anything.
    Candidate health still additionally requires the independent guardian proof.
    """
    began = time.monotonic()
    pidfd = -1
    try:
        require(type(supplemental) is bool)
        require(type(firmware) is str and 0 < len(firmware) <= 64)
        fingerprint, matching = profile_files(deployment_path, recordings)
        with CachedClient(daemon_socket, timeout=0.2) as client:
            pid, uid, _gid = struct.unpack(
                "3i", client.connect().getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            require(pid > 1 and uid == os.geteuid())
            peer_ticks = process_ticks(pid)
            pidfd = os.pidfd_open(pid)
            require(process_ticks(pid) == peer_ticks)
            hello = client.hello()
            runtime = client.runtime_snapshot()
            recording = client.recording_status()
            profile = client.request("display.profile")
            latest = client.runtime_snapshot()
        require(type(hello.get("operations")) is list)
        operations = cast(list[str], hello["operations"])
        require(all(type(op) is str for op in operations))
        advertised = {op for op in operations if op.startswith("display.supplemental.")}
        require(
            advertised
            == (
                {
                    "display.supplemental.context",
                    "display.supplemental.frame",
                    "display.supplemental.demand",
                }
                if supplemental
                else set()
            )
        )
        require(type(recording.get("active")) is bool)
        require(type(profile.get("accepted")) is dict)
        accepted = cast(dict[str, Any], profile["accepted"])
        require(profile.get("configured") is True and profile.get("failure") is None)
        require(profile.get("endpoint_id") == matching["endpoint_id"])
        require(
            all(
                accepted.get(key) == matching[key]
                for key in ("source_id", "source_kind", "revision")
            )
        )
        healthy = True
        for value in (runtime, latest):
            require(value.get("scanner_endpoint") == matching["target"])
            require(
                value.get("scanner_model") == "SDS200" and value.get("scanner_firmware") == firmware
            )
            require(
                type(value.get("scanner_connected")) is bool
                and type(value.get("psi_active")) is bool
            )
            require(value.get("state") in ("running", "starting", "stopping", "stopped", "failed"))
            healthy = healthy and (
                value["state"] == "running"
                and value["scanner_connected"] is True
                and value["psi_active"] is True
            )
        # Disk state must also stay the same while cached in-memory state is read.
        require(profile_files(deployment_path, recordings) == (fingerprint, matching))
        poller = select.poll()
        poller.register(pidfd, select.POLLIN)
        require(not poller.poll(0) and process_ticks(pid) == peer_ticks)
        require(0 <= time.monotonic() - began <= 1.5)
        return CachedEvidence(
            fingerprint, healthy, cast(bool, recording["active"]), bool(advertised), pid, peer_ticks
        )
    except Exception:
        raise UnconfirmedCache() from None
    finally:
        if pidfd >= 0:
            os.close(pidfd)


if __name__ == "__main__":
    raise SystemExit("Read-only cached evidence component; no probe or acquisition started.")
