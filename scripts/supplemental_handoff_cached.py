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
import time
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


@dataclass(frozen=True)
class CachedEvidence:
    profile_sha256: str
    healthy: bool
    recording: bool
    supplemental_advertised: bool


def profile_files(deployment_path: Path, recordings: Path) -> tuple[str, dict[str, Any]]:
    """Validate accepted/source binding and hash private bytes without exporting them."""
    from sds200.scanner_display_configuration import _read_configuration
    from sds200.scanner_display_deployment import load_scanner_display_deployment
    from sds200.scanner_display_profile_storage import _access, _read_state, _source

    # Bound readers already reject symlinks, hardlinks, unsafe owners/permissions,
    # oversized files and observed replacement. No profile import or reload.
    deployment_bytes = _read_configuration(deployment_path)
    deployment = load_scanner_display_deployment(deployment_path)
    config_bytes = _read_configuration(deployment.profile_config)
    config = deployment.preflight(recordings)
    with _access(config.state_directory, exclusive=False) as directory:
        accepted = _read_state(directory, config.binding.endpoint_id)
        source = _source(config.source_path)
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
    require(_read_configuration(deployment.profile_config) == config_bytes)
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
    from sds200.daemon_api import DaemonApiOperation
    from sds200.daemon_client import DaemonApiClient
    from sds200.daemon_ipc import resolve_daemon_socket_location

    began = time.monotonic()
    try:
        require(type(supplemental) is bool)
        require(type(firmware) is str and 0 < len(firmware) <= 64)
        fingerprint, matching = profile_files(deployment_path, recordings)
        with DaemonApiClient(resolve_daemon_socket_location(daemon_socket), timeout=0.2) as client:
            hello = client.hello()
            runtime = client.runtime_snapshot()
            recording = client.recording_status()
            profile = client.request(DaemonApiOperation.DISPLAY_PROFILE)
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
        require(0 <= time.monotonic() - began <= 1.5)
        return CachedEvidence(
            fingerprint, healthy, cast(bool, recording["active"]), bool(advertised)
        )
    except Exception:
        raise UnconfirmedCache() from None


if __name__ == "__main__":
    raise SystemExit("Read-only cached evidence component; no probe or acquisition started.")
