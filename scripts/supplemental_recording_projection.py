#!/usr/bin/env python3
"""Explicit host/native baseline projection; offline plan preparation only.

No namespace entry, native exec, filesystem capture or lifecycle authority. Both
manifests retain the SAME original inventory; only the declared root-path alias
differs. Fresh complete host and native observations, source authentication and
independent process checks are still required before using the projection live.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import supplemental_handoff_host as host
import supplemental_handoff_protected as fixed
import supplemental_recording_protected as recording
from supplemental_handoff_policy import CANDIDATE, checksum, require

HOST_MEDIA = Path("/mnt/data/supervisor/media")
NATIVE_MEDIA = Path("/media")


def _validated(value):
    # Round-trip strict canonical data without recapturing or adopting any files.
    require(type(value) is recording.StoredBaseline)
    raw = recording.manifest_bytes(
        value.baseline,
        value.writer,
        value.contract.audio_endpoint_sha256,
        maximum_recording_seconds=value.contract.maximum_recording_seconds,
    )
    require(recording._decode(raw) == value)
    return raw


@dataclass(frozen=True)
class Projection:
    layout: fixed.ProtectedLayout
    host: recording.StoredBaseline
    native: recording.StoredBaseline

    def __post_init__(self):
        require(type(self.layout) is fixed.ProtectedLayout and self.layout.slug == CANDIDATE)
        require(self.layout.media == HOST_MEDIA)
        _validated(self.host)
        _validated(self.native)
        require(self.host.baseline.root == self.layout.recordings)
        alias = NATIVE_MEDIA / self.layout.recordings.relative_to(HOST_MEDIA)
        require(self.native.baseline.root == alias and alias != NATIVE_MEDIA)
        require(self.host.baseline.case == self.native.baseline.case)
        require(self.host.baseline.root_identity == self.native.baseline.root_identity)
        require(self.host.baseline.files == self.native.baseline.files)
        require(self.host.writer == self.native.writer)
        require(
            replace(self.host.contract, root_sha256=self.native.contract.root_sha256)
            == self.native.contract
        )
        require(self.host.contract.root_sha256 != self.native.contract.root_sha256)

    @property
    def sha256(self):
        """Pin both canonical manifests and exact media aliases, not source pins."""
        self.__post_init__()
        return checksum(
            {
                "kind": "finite-recording-media-projection-v1",
                "slug": self.layout.slug,
                "host_media": str(HOST_MEDIA),
                "native_media": str(NATIVE_MEDIA),
                "host_root": str(self.host.baseline.root),
                "native_root": str(self.native.baseline.root),
                "host_manifest": self.host.manifest_sha256,
                "native_manifest": self.native.manifest_sha256,
                "host_contract": self.host.contract.sha256,
                "native_contract": self.native.contract.sha256,
            }
        )

    @property
    def native_manifest(self):
        self.__post_init__()
        return _validated(self.native)

    def check_container(self, value, *, image: str, generation: str):
        """Check an independently collected live incarnation and its media mount.

        This does not collect a fresh inspect, verify package/options/profile,
        authenticate a native reply or prove process exit. The full host observer
        and exact-process witness remain mandatory. It is NOT a mount operation.
        """
        self.__post_init__()
        require(host.generation(value, name="app_" + CANDIDATE, image=image) == generation)
        mounts = value.get("Mounts")
        require(type(mounts) is list and len(mounts) <= 32)
        seen, media = set(), None
        for mount in mounts:
            require(type(mount) is dict)
            destination = mount.get("Destination")
            require(type(destination) is str and 0 < len(destination) <= 4096)
            path = Path(destination)
            require(str(path) == destination and path.is_absolute() and ".." not in path.parts)
            require(not any(ord(c) < 32 or ord(c) == 127 for c in destination))
            require(destination not in seen)
            seen.add(destination)
            if path == NATIVE_MEDIA:
                require(mount.get("Type") == "bind" and mount.get("RW") is True)
                require(mount.get("Source") == str(HOST_MEDIA))
                media = mount
            else:
                # Neither a parent replacement nor a nested mount may change
                # which inode the native recording root denotes.
                require(not path.is_relative_to(NATIVE_MEDIA))
                require(not NATIVE_MEDIA.is_relative_to(path))
        require(media is not None)

    def check_native_manifest(self, raw: bytes, *, pinned_projection: str):
        """Compare pinned declared bytes; not a native observation or acknowledgment.

        A qualified native process must separately load these exact bytes and
        verify Collector(native).pristine() in its own namespace. Host callers
        must not open the native alias in the host namespace as a substitute.
        """
        require(self.sha256 == pinned_projection)
        require(type(raw) is bytes and raw == self.native_manifest)
        return self.native


def project(layout: fixed.ProtectedLayout, original: recording.StoredBaseline) -> Projection:
    """Derive distinct manifest bytes, never overwrite/reseal the original.

    This preparation is passive and does not assert that an App is installed,
    running or mounted. check_container and independently authenticated complete
    native/host observations must precede any later use of this binding.
    """
    require(type(layout) is fixed.ProtectedLayout and layout.slug == CANDIDATE)
    _validated(original)
    require(original.baseline.root == layout.recordings)
    native_root = NATIVE_MEDIA / layout.recordings.relative_to(HOST_MEDIA)
    baseline = replace(original.baseline, root=native_root)
    raw = recording.manifest_bytes(
        baseline,
        original.writer,
        original.contract.audio_endpoint_sha256,
        maximum_recording_seconds=original.contract.maximum_recording_seconds,
    )
    return Projection(layout, original, recording._decode(raw))


if __name__ == "__main__":
    raise SystemExit("Offline baseline projection only; no native or host action is enabled.")
