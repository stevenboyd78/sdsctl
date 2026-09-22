#!/usr/bin/env python3
"""Fixed host-path content evidence for independently sealed HA App installations.

No Docker operations, namespace entry, imports from an App or filesystem writes.
Paths, mounts and stopped-image package proof are separately reviewed/sealed;
the observer checks image/container identity before and after this collection.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from supplemental_handoff_files import executable_file, inventory, private_file
from supplemental_handoff_observer import ProtectedFiles
from supplemental_handoff_policy import CANDIDATE, NORMAL, checksum, digest, require

PACKAGE = Path("usr/local/lib/python3.14/site-packages/sds200")
LAUNCHERS = Path("opt/sdsctl-supplemental-acceptance")
ENTRIES = ("sdsctl-supplemental-acceptance", "sdsctl-supplemental-acceptance-web")


def package_fingerprint(root: Path, *, candidate: bool) -> str:
    runtime = inventory(root / PACKAGE)
    if not candidate:
        return checksum(runtime)
    launchers = inventory(root / LAUNCHERS)
    require(
        set(launchers)
        == {
            "accept_supplemental_daemon.py",
            "guard_supplemental_acceptance.py",
            "accept_supplemental_web.py",
        }
    )
    entries = {name: asdict(executable_file(root / "usr/local/bin" / name)) for name in ENTRIES}
    return checksum({"runtime": runtime, "launchers": launchers, "entries": entries})


@dataclass(frozen=True)
class ProtectedLayout:
    slug: str
    context: Path
    data: Path
    media: Path
    recordings: Path
    deployment: Path
    configuration: Path
    accepted: Path
    source: Path
    image_package_sha256: str

    def __post_init__(self) -> None:
        require(self.slug in (NORMAL, CANDIDATE))
        digest(self.image_package_sha256)
        for path in (self.context, self.data, self.media, self.recordings, *self.profile_paths):
            require(type(path) is type(Path()) and path.is_absolute() and path != Path("/"))
            require(all(part not in (".", "..") for part in path.parts))
        require(
            self.context
            == Path("/mnt/data/supervisor/apps/local") / self.slug.removeprefix("local_")
        )
        require(self.data == Path("/mnt/data/supervisor/apps/data") / self.slug)
        require(self.media == Path("/mnt/data/supervisor/media"))
        require(self.recordings.is_relative_to(self.media) and self.recordings != self.media)
        require(len(set(self.profile_paths)) == 4)
        for path in self.profile_paths:
            require(path.is_relative_to(self.data) or path.is_relative_to(self.media))
            require(not path.is_relative_to(self.recordings))

    @property
    def profile_paths(self) -> tuple[Path, ...]:
        return self.deployment, self.configuration, self.accepted, self.source


def collect(layout: ProtectedLayout, container: dict[str, Any] | None) -> ProtectedFiles:
    require(type(layout) is ProtectedLayout)
    package = layout.image_package_sha256
    if container is not None:
        require(type(container) is dict and container.get("Name") == "/app_" + layout.slug)
        mounts = container.get("Mounts")
        require(type(mounts) is list and len(mounts) <= 32)
        destinations = set()
        for mount in cast(list[Any], mounts):
            require(type(mount) is dict and type(mount.get("Destination")) is str)
            destination = Path(mount["Destination"])
            require(destination.is_absolute() and ".." not in destination.parts)
            require(str(destination) not in destinations)
            destinations.add(str(destination))
            # A bind over any source package ancestor or descendant could hide
            # modified runtime bytes outside the independently inspected image.
            code_roots = (
                Path("/") / PACKAGE,
                Path("/") / LAUNCHERS,
                *(Path("/usr/local/bin") / name for name in ENTRIES),
            )
            for code in code_roots:
                require(
                    not destination.is_relative_to(code) and not code.is_relative_to(destination)
                )
            require(
                not any(
                    destination != root and destination.is_relative_to(root)
                    for root in (Path("/data"), Path("/media"))
                )
            )
            if str(destination) in ("/data", "/media"):
                expected = layout.data if str(destination) == "/data" else layout.media
                require(
                    mount.get("Type") == "bind"
                    and mount.get("Source") == str(expected)
                    and mount.get("RW") is True
                )
        require({"/data", "/media"} <= destinations)
        driver = container.get("GraphDriver")
        require(type(driver) is dict and driver.get("Name") == "overlay2")
        data = cast(dict[str, Any], driver).get("Data")
        require(type(data) is dict and data.get("ID") == container.get("Id"))
        merged = cast(dict[str, Any], data).get("MergedDir")
        require(
            type(merged) is str
            and re.fullmatch(r"/mnt/data/docker/overlay2/[a-z0-9]{1,128}/merged", merged)
            is not None
        )
        package = package_fingerprint(Path(cast(str, merged)), candidate=layout.slug == CANDIDATE)
    profile = checksum(
        {
            key: private_file(path).sha256
            for key, path in zip(
                ("deployment", "configuration", "accepted", "source"),
                layout.profile_paths,
                strict=True,
            )
        }
    )
    return ProtectedFiles(
        checksum(inventory(layout.context)),
        package,
        profile,
        checksum(inventory(layout.recordings, max_file_bytes=16 * 1024 * 1024)),
    )


if __name__ == "__main__":
    raise SystemExit("Read-only protected-file collector; no App or scanner operation started.")
