#!/usr/bin/env python3
"""Read-only recording-candidate source/profile checks; no installed host plan.

This is deliberately separate from the legacy idle-only wrapper fingerprint.
Immutable image/interpreter/dependency qualification and exact container identity
are independent prerequisites. Source hashes never authenticate themselves.
The caller MUST also collect every old and new recording file for its stage.
"""

from __future__ import annotations

from pathlib import Path

import supplemental_handoff_protected as legacy
import supplemental_recording_source as source
from supplemental_handoff_policy import CANDIDATE, checksum, require

NATIVE = Path("opt/sdsctl-supplemental-recording")
# Do not permit a mount to replace the pinned interpreter, dependencies or any
# executable/helper beneath these roots. They still need independent immutable
# image proof: this collector hashes the runtime/native bundle, not all of /usr.
CODE_ROOTS = tuple(Path(name) for name in ("/usr", "/bin", "/lib", "/lib64", "/opt"))


def package_fingerprint(root: Path) -> str:
    require(
        type(root) is type(Path())
        and root.is_absolute()
        and root != Path("/")
        and ".." not in root.parts
    )
    return source.Layout(root / legacy.PACKAGE, root / NATIVE).observe().sha256


def collect(layout: legacy.ProtectedLayout, container: dict | None) -> legacy.StaticFiles:
    """Hash actual complete code when running; retain the sealed image when absent.

    No fallback to old wrappers, claimed native reports, mount namespace entry,
    App code imports, Docker operation or changing-recording-root exclusion.
    The enclosing host observer checks image/incarnation before and after this
    observation and compares the resulting pin BEFORE executing cached probes.
    """
    require(type(layout) is legacy.ProtectedLayout and layout.slug == CANDIDATE)
    merged = legacy._merged_root(layout, container, code_roots=CODE_ROOTS)
    package = layout.image_package_sha256 if merged is None else package_fingerprint(merged)
    profile = checksum(
        {
            key: legacy.private_file(path).sha256
            for key, path in zip(
                ("deployment", "configuration", "accepted", "source"),
                layout.profile_paths,
                strict=True,
            )
        }
    )
    return legacy.StaticFiles(checksum(legacy.inventory(layout.context)), package, profile)


if __name__ == "__main__":
    raise SystemExit("Read-only recording source evidence only; no installed host plan enabled.")
