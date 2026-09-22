#!/usr/bin/env python3
"""Private finite handoff service assembly; never a public/admin web endpoint.

Explicitly launched only with a separately reviewed, pinned private plan and
read-only code bundle. Preparation alone cannot stop/start an App: fresh local
operator input is still required. No acquisition/arming operation exists here.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import stat
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from supplemental_handoff_app_read import AppReads, ProbePaths
from supplemental_handoff_files import DIRECTORY, inventory
from supplemental_handoff_host import Docker, TrackedDispatch, object_json
from supplemental_handoff_observer import (
    AppSeal,
    HostObserver,
    ProtectedFiles,
    SupervisorReads,
    image_id,
)
from supplemental_handoff_operator import OperatorInbox, directory_unchanged, open_directory
from supplemental_handoff_policy import (
    CANDIDATE,
    NORMAL,
    TOTAL_SECONDS,
    Journal,
    checksum,
    digest,
    encode,
    identifier,
    require,
)
from supplemental_handoff_protected import ProtectedLayout, collect
from supplemental_handoff_recovery import RecoverySession, TrackedProcesses

BUNDLE = Path("/opt/sdsctl-handoff")
MODULES = frozenset(
    "supplemental_handoff_" + name + ".py"
    for name in (
        "app_read",
        "cached",
        "executor",
        "files",
        "guard_state",
        "host",
        "observer",
        "operator",
        "policy",
        "process",
        "protected",
        "recovery",
        "runtime",
        "service",
    )
)


@dataclass(frozen=True)
class Plan:
    case: str
    boot: str
    source: str
    firmware: str
    helper_image: str
    cli_image: str
    cli_generation: str
    core_image: str
    core_generation: str
    core_version: str
    normal_generation: str
    code: dict[str, str]
    seals: tuple[AppSeal, AppSeal]
    layouts: tuple[ProtectedLayout, ProtectedLayout]
    installed_versions: dict[str, str]
    other_scanner_apps: frozenset[str]

    @property
    def root(self) -> Path:
        return Path("/mnt/data/sdsctl-handoff-" + self.case)

    @property
    def bundle(self) -> Path:
        return Path("/mnt/data/sdsctl-handoff-code-" + self.case)

    @property
    def probes(self) -> dict[str, ProbePaths]:
        return {
            layout.slug: ProbePaths(
                str(Path("/data") / layout.deployment.relative_to(layout.data)),
                str(Path("/media") / layout.recordings.relative_to(layout.media)),
            )
            for layout in self.layouts
        }


def decode_plan(value: dict[str, Any]) -> Plan:
    require(type(value) is dict and set(value) == {"schema", *Plan.__dataclass_fields__})
    require(type(value["schema"]) is int and value["schema"] == 1)
    identifier(value["case"], case=True)
    identifier(value["boot"])
    require(
        type(value["source"]) is str and re.fullmatch(r"[a-f0-9]{40}", value["source"]) is not None
    )
    require(
        type(value["firmware"]) is str
        and re.fullmatch(r"[A-Za-z0-9._ -]{1,64}", value["firmware"]) is not None
    )
    for key in ("helper_image", "cli_image", "core_image"):
        image_id(value[key])
    for key in ("cli_generation", "core_generation", "normal_generation"):
        digest(value[key])
    require(
        type(value["core_version"]) is str
        and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value["core_version"]) is not None
    )
    require(type(value["code"]) is dict and set(value["code"]) == MODULES)
    for hashed in value["code"].values():
        digest(hashed)
    require(type(value["seals"]) is list and len(value["seals"]) == 2)
    seals = []
    for item in value["seals"]:
        require(type(item) is dict and set(item) == set(AppSeal.__dataclass_fields__))
        files = item["files"]
        require(type(files) is dict and set(files) == set(ProtectedFiles.__dataclass_fields__))
        seals.append(AppSeal(**{**item, "files": ProtectedFiles(**files)}))
    require({seal.slug for seal in seals} == {NORMAL, CANDIDATE})
    require(type(value["layouts"]) is list and len(value["layouts"]) == 2)
    layouts = []
    for item in value["layouts"]:
        require(type(item) is dict and set(item) == set(ProtectedLayout.__dataclass_fields__))
        require(all(type(v) is str for v in item.values()))
        kwargs: dict[str, Any] = {
            k: v if k in ("slug", "image_package_sha256") else Path(v) for k, v in item.items()
        }
        layouts.append(ProtectedLayout(**kwargs))
    require({layout.slug for layout in layouts} == {NORMAL, CANDIDATE})
    require(
        type(value["installed_versions"]) is dict and 2 <= len(value["installed_versions"]) <= 256
    )
    for slug, version in value["installed_versions"].items():
        require(type(slug) is str and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", slug) is not None)
        require(
            type(version) is str and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", version) is not None
        )
    require(
        type(value["other_scanner_apps"]) is list
        and all(type(slug) is str for slug in value["other_scanner_apps"])
    )
    other = frozenset(value["other_scanner_apps"])
    require(
        len(other) == len(value["other_scanner_apps"])
        and other <= value["installed_versions"].keys()
    )
    require(not other & {NORMAL, CANDIDATE})
    require(
        {slug for slug in value["installed_versions"] if "sds200" in slug or "sdsctl" in slug}
        - {NORMAL, CANDIDATE}
        <= other
    )
    for seal in seals:
        require(value["installed_versions"].get(seal.slug) == seal.version)
        layout = next(layout for layout in layouts if layout.slug == seal.slug)
        require(layout.image_package_sha256 == seal.files.package)
        require(layout.deployment.is_relative_to(layout.data))
    # The two services must never share writable recording or profile state.
    a, b = layouts
    require(
        not a.recordings.is_relative_to(b.recordings)
        and not b.recordings.is_relative_to(a.recordings)
    )
    require(not set(a.profile_paths) & set(b.profile_paths))
    require(
        not any(path.is_relative_to(b.recordings) for path in a.profile_paths)
        and not any(path.is_relative_to(a.recordings) for path in b.profile_paths)
    )
    plan = Plan(
        **{
            k: v
            for k, v in value.items()
            if k not in ("schema", "seals", "layouts", "other_scanner_apps")
        },
        seals=(seals[0], seals[1]),
        layouts=(layouts[0], layouts[1]),
        other_scanner_apps=other,
    )
    _ = plan.probes
    return plan


def load_plan(root: Path, expected: str) -> Plan:
    digest(expected)
    fd = open_directory(root)
    file = -1
    try:
        file = os.open("plan.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        before = os.fstat(file)
        require(
            stat.S_ISREG(before.st_mode)
            and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_uid == os.getuid()
            and before.st_nlink == 1
            and 0 < before.st_size <= 65536
        )
        raw = os.read(file, 65537)
        after, named = os.fstat(file), os.stat("plan.json", dir_fd=fd, follow_symlinks=False)
        require(
            len(raw) == before.st_size
            and all(
                getattr(before, k) == getattr(after, k) == getattr(named, k)
                for k in (
                    "st_dev",
                    "st_ino",
                    "st_size",
                    "st_uid",
                    "st_gid",
                    "st_mode",
                    "st_nlink",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
            )
        )
        require(hashlib.sha256(raw).hexdigest() == expected)
        directory_unchanged(root, fd)
        plan = decode_plan(object_json(raw))
        require(plan.root == root)
        return plan
    finally:
        if file >= 0:
            os.close(file)
        os.close(fd)


def verify_bundle(plan: Plan) -> None:
    values = inventory(BUNDLE)
    require(set(values) == MODULES)
    for name, metadata in values.items():
        require(metadata["mode"] == 0o444 and metadata["uid"] == 0 and metadata["gid"] == 0)
        require(metadata["sha256"] == plan.code[name])


def read_clock() -> tuple[str, float]:
    with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as stream:
        value = stream.read(38).strip()
    return UUID(value).hex, time.clock_gettime(time.CLOCK_BOOTTIME)


def publish_report(root: Path, name: str, value: dict[str, Any]) -> None:
    """One exclusive durable report; uncertainty remains for review, no overwrite."""
    require(name in ("ready.json", "outcome.json"))
    raw = encode(value)
    require(len(raw) <= 4096)
    fd = open_directory(root)
    try:
        file = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(file, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
        directory_unchanged(root, fd)
    finally:
        os.close(fd)


def fresh_candidate(plan: Plan, docker: Docker) -> None:
    # Never initialize a case using an old exited container or retained guard
    # state. FileNotFoundError is accepted only for this final new case entry.
    require(all("/app_" + CANDIDATE not in item.get("Names", []) for item in docker.containers()))
    layout = next(item for item in plan.layouts if item.slug == CANDIDATE)
    fd = data_directory(layout.data)
    try:
        name = "sdsctl-supplemental-acceptance-" + plan.case
        try:
            os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("Candidate case already exists; preserve it for review.")
        check = data_directory(layout.data)
        try:
            before, after = os.fstat(fd), os.fstat(check)
            require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino))
        finally:
            os.close(check)
    finally:
        os.close(fd)


def data_directory(path: Path) -> int:
    """Supervisor owns an existing 0755 App data root; do not chmod/adopt it.

    The private case beneath it and its reports retain their separate 0700/0600
    requirements. Only this existing ancestor permits 0755 as observed on HAOS.
    """
    require(type(path) is type(Path()) and path.is_absolute() and ".." not in path.parts)
    fd = os.open("/", DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) in (0o700, 0o755))
        return fd
    except BaseException:
        os.close(fd)
        raise


def run(plan: Plan) -> dict[str, Any]:
    verify_bundle(plan)
    require(read_clock()[0] == plan.boot)
    docker = Docker()
    layouts = {layout.slug: layout for layout in plan.layouts}
    cached = AppReads(
        docker,
        seals=plan.seals,
        case=plan.case,
        source=plan.source,
        firmware=plan.firmware,
        paths=plan.probes,
    )
    supervisor = SupervisorReads(docker, image=plan.cli_image, incarnation=plan.cli_generation)
    observer = HostObserver(
        docker,
        supervisor,
        seals=plan.seals,
        installed_versions=plan.installed_versions,
        other_scanner_apps=plan.other_scanner_apps,
        core_image=plan.core_image,
        core_generation=plan.core_generation,
        core_version=plan.core_version,
        read_clock=read_clock,
        collect_files=lambda slug, container: collect(layouts[slug], container),
        read_native=cached.read,
    )
    with Journal(plan.root / "journal") as journal:
        fresh = journal.machine is None
        if fresh:
            inbox_fd = open_directory(plan.root / "inbox")
            try:
                require(not os.listdir(inbox_fd))
            finally:
                os.close(inbox_fd)
            require(not os.path.lexists(plan.root / "ready.json"))
            require(not os.path.lexists(plan.root / "outcome.json"))
            fresh_candidate(plan, docker)
            sample = observer.read()
            require(
                sample.boot_id == plan.boot
                and sample.observation.normal.generation == plan.normal_generation
            )
            require(
                sample.observation.normal.pin == next(s.pin for s in plan.seals if s.slug == NORMAL)
            )
            require(
                sample.observation.candidate.pin
                == next(s.pin for s in plan.seals if s.slug == CANDIDATE)
            )
            journal.append(
                dict(
                    kind="prepare",
                    case_id=plan.case,
                    boot_id=sample.boot_id,
                    now=sample.now,
                    observation=asdict(sample.observation),
                )
            )
        require(journal.machine is not None)
        assert journal.machine is not None
        require(journal.machine.case_id == plan.case and journal.machine.boot_id == plan.boot)
        require(journal.machine.baseline.normal.generation == plan.normal_generation)
        require(
            journal.machine.baseline.normal.pin
            == next(s.pin for s in plan.seals if s.slug == NORMAL)
        )
        require(
            journal.machine.baseline.candidate.pin
            == next(s.pin for s in plan.seals if s.slug == CANDIDATE)
        )
        processes = TrackedProcesses(
            journal, docker, images={s.slug: s.image for s in plan.seals}, read_clock=read_clock
        )
        dispatch = TrackedDispatch(
            journal,
            docker,
            cli_image=plan.cli_image,
            cli_generation=plan.cli_generation,
            now=lambda: read_clock()[1],
        )
        inbox = OperatorInbox(plan.root / "inbox", journal, read_clock)
        session = RecoverySession(
            journal, processes, dispatch, observer.read, consume_operator=inbox.consume
        )
        try:
            if fresh:
                # Prepared state cannot dispatch without a request. Bind normal
                # first, then publish the baseline the operator notice must use.
                session.consume_operator = None
                initial = session.poll()
                require(initial.phase == "prepared" and processes.record(NORMAL) is not None)
                publish_report(
                    plan.root,
                    "ready.json",
                    dict(
                        schema=1,
                        case_id=plan.case,
                        boot_id=plan.boot,
                        baseline=checksum(asdict(journal.machine.baseline)),
                        request_deadline=journal.machine.state.deadline,
                        no_app_handoff_attempted=True,
                    ),
                )
                session.consume_operator = inbox.consume
            result = session.run(time.sleep)
            value = dict(
                schema=1,
                case_id=plan.case,
                phase=result.phase,
                outcome=result.outcome,
                restoration_verified=result.phase == "complete",
            )
            publish_report(plan.root, "outcome.json", value)
            return value
        finally:
            session.close()
            inbox.close()


def launch(plan: Plan, plan_sha256: str) -> tuple[str, ...]:
    """Build only; caller must qualify and launch once, never retry uncertain SSH."""
    digest(plan_sha256)
    name = "sdsctl-handoff-" + plan.case
    program = (
        "import sys;sys.path.insert(0,'/opt/sdsctl-handoff');"
        "from supplemental_handoff_service import main;main()"
    )
    command = (
        "/usr/bin/systemd-run",
        "--unit=" + name,
        "--no-ask-password",
        "--expand-environment=no",
        "--service-type=exec",
        "--property=RemainAfterExit=yes",
        "--property=Restart=no",
        "--property=RuntimeMaxSec=" + str(TOTAL_SECONDS + 10) + "s",
        "--property=TimeoutStopSec=5s",
        "--property=ExecStopPost=-/usr/bin/docker stop --time=2 " + name,
        "/usr/bin/docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        name,
        "--network=none",
        "--read-only",
        "--pid=host",
        "--cgroupns=host",
        "--cap-drop=ALL",
        "--cap-add=DAC_READ_SEARCH",
        "--security-opt=no-new-privileges",
        "--user=0:0",
        "--pids-limit=32",
        "--memory=256m",
        "--cpus=1",
        "--mount",
        "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock,readonly",
        "--mount",
        "type=bind,source=/mnt/data,target=/mnt/data,readonly",
        "--mount",
        f"type=bind,source={plan.root},target={plan.root}",
        "--mount",
        f"type=bind,source={plan.bundle},target={BUNDLE},readonly",
        "--entrypoint=/usr/local/bin/python",
        plan.helper_image,
        "-I",
        "-B",
        "-c",
        program,
        str(plan.root),
        plan_sha256,
    )
    require(len(shlex.join(command).encode()) < 8000)
    return command


def main() -> None:
    try:
        require(len(sys.argv) == 3)
        plan = load_plan(Path(sys.argv[1]), sys.argv[2])
        result = run(plan)
        print(encode(result).decode(), flush=True)
    except Exception:
        print(
            "Private handoff outcome unconfirmed; preserve the case for review.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
