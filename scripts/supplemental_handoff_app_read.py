#!/usr/bin/env python3
"""Fixed read-only App probe in an already qualified container incarnation.

The private service bundle must itself be sealed and read-only. Only the two
reviewed collectors beside this module become code; input cannot select a path,
command or scanner operation. No arbitrary-command method or probe replay exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import supplemental_handoff_cached as cached
import supplemental_handoff_guard_state as guard_state
from supplemental_handoff_host import (
    Docker,
    docker_output,
    execution_state,
    generation,
    object_json,
)
from supplemental_handoff_observer import AppSeal, NativeState
from supplemental_handoff_policy import CANDIDATE, NORMAL, digest, identifier, require


@dataclass(frozen=True)
class ProbePaths:
    """Container paths reconstructed from sealed options and protected layout.

    No heavyweight App startup import during a health read. The observer already
    checks settings/profile bytes before invoking this program; cached state is
    additionally matched to the accepted profile and scanner endpoint inside it.
    """

    deployment: str
    recordings: str

    def __post_init__(self) -> None:
        for value, base in ((self.deployment, "/data"), (self.recordings, "/media")):
            require(type(value) is str and 1 <= len(value) <= 1024)
            require(all(ord(ch) >= 32 and ord(ch) != 127 for ch in value))
            path = Path(value)
            require(str(path) == value and ".." not in path.parts)
            require(path != Path(base) and path.is_relative_to(base))


def probe_command(
    *, candidate: bool, case: str, source: str, firmware: str, paths: ProbePaths
) -> tuple[str, ...]:
    require(type(candidate) is bool)
    identifier(case, case=True)
    require(type(source) is str and re.fullmatch(r"[0-9a-f]{40}", source) is not None)
    require(type(firmware) is str and re.fullmatch(r"[A-Za-z0-9._ -]{1,64}", firmware) is not None)
    require(type(paths) is ProbePaths)
    program = "import sys,types,json\nfrom pathlib import Path\n"
    # Static imports close the helper graph; only these two sealed sibling files
    # become the fixed program. Neither import performs IPC or process work.
    for name in (cached.__name__, guard_state.__name__):
        code = Path(__file__).with_name(name + ".py").read_text()
        require(0 < len(code.encode()) <= 32768)
        program += (
            f"m=types.ModuleType({name!r});sys.modules[m.__name__]=m;"
            f"exec(compile({code!r},'<sealed-read-only-collector>','exec'),m.__dict__)\n"
        )
    program += f"candidate,case,source,firmware={candidate!r},{case!r},{source!r},{firmware!r}\n"
    program += f"deployment,recordings={paths.deployment!r},{paths.recordings!r}\n"
    program += """from supplemental_handoff_cached import collect_cached
from supplemental_handoff_guard_state import guardian_live
try:
    evidence=collect_cached(Path(deployment),Path(recordings),Path('/run/sdsctl/daemon.sock'),firmware=firmware,supplemental=candidate)
    healthy=evidence.healthy
    if candidate:
        try:
            guarded=guardian_live(evidence,case=case,source=source)
            healthy=healthy and guarded
        except Exception:
            healthy=None
    print(json.dumps(dict(schema=1,profile=evidence.profile_sha256,healthy=healthy,recording=evidence.recording,supplemental=evidence.supplemental_advertised),sort_keys=True))
except Exception:
    print('Cached App probe unconfirmed.',file=sys.stderr)
    raise SystemExit(1)
"""
    return "/usr/local/bin/python", "-I", "-B", "-c", program


class AppReads:
    """One new bounded exec per read; missing reply is unconfirmed, never retried."""

    def __init__(
        self,
        docker: Docker,
        *,
        seals: tuple[AppSeal, AppSeal],
        case: str,
        source: str,
        firmware: str,
        paths: dict[str, ProbePaths],
    ):
        require(type(seals) is tuple and len(seals) == 2 and all(type(s) is AppSeal for s in seals))
        require({s.slug for s in seals} == {NORMAL, CANDIDATE})
        require(type(paths) is dict and set(paths) == {NORMAL, CANDIDATE})
        require(all(type(path) is ProbePaths for path in paths.values()))
        self.docker, self.seals = docker, {s.slug: s for s in seals}
        self.commands = {
            slug: probe_command(
                candidate=slug == CANDIDATE,
                case=case,
                source=source,
                firmware=firmware,
                paths=paths[slug],
            )
            for slug in self.seals
        }

    def read(self, slug: str, incarnation: str) -> NativeState:
        require(slug in (NORMAL, CANDIDATE))
        return _read_probe(self.docker, self.seals[slug], self.commands[slug], incarnation)


def _read_probe(
    docker: Docker, seal: AppSeal, command: tuple[str, ...], incarnation: str
) -> NativeState:
    """Shared fixed transport; callers construct the reviewed command locally."""
    require(type(seal) is AppSeal and seal.slug in (NORMAL, CANDIDATE))
    slug = seal.slug
    digest(incarnation)
    name = "app_" + slug
    before = docker.container(name)
    require(generation(before, name=name, image=seal.image) == incarnation)
    # Fixed reviewed program; never a caller-provided argv or shell. Direct
    # transport is private to this collector, not an expanded command API.
    value = object_json(
        docker._request(
            "POST",
            f"/containers/{before['Id']}/exec",
            {
                "AttachStdin": False,
                "AttachStdout": True,
                "AttachStderr": True,
                "Tty": False,
                "Privileged": False,
                "User": "0",
                "Cmd": command,
            },
            expected=201,
        )
    )
    execution = value.get("Id")
    digest(execution)
    execution = cast(str, execution)
    require(
        execution_state(
            docker.inspect_execution(execution),
            execution_id=execution,
            container_id=before["Id"],
            command=command,
        )
        == "created"
    )
    require(generation(docker.container(name), name=name, image=seal.image) == incarnation)
    raw = docker.start_execution(execution, attach=True)
    after = docker.inspect_execution(execution)
    require(
        execution_state(after, execution_id=execution, container_id=before["Id"], command=command)
        == "not_running"
        and after["ExitCode"] == 0
    )
    require(generation(docker.container(name), name=name, image=seal.image) == incarnation)
    evidence: dict[str, Any] = object_json(docker_output(raw))
    require(set(evidence) == {"schema", "profile", "healthy", "recording", "supplemental"})
    require(type(evidence["schema"]) is int and evidence["schema"] == 1)
    require(evidence["profile"] == seal.files.profile)
    require(evidence["supplemental"] is (slug == CANDIDATE))
    require(type(evidence["recording"]) is bool)
    return NativeState(incarnation, evidence["healthy"], evidence["recording"])


if __name__ == "__main__":
    raise SystemExit("Fixed read-only App collector; no App or scanner operation started.")
