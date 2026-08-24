from __future__ import annotations

import tomllib
from pathlib import Path

from sds200 import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_version_matches_project_metadata() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]

    assert project["version"] == __version__


def test_changelog_contains_current_release_heading() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert f"## [{__version__}] - " in changelog


def test_changelog_comparison_links_start_at_current_release() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert (
        f"[Unreleased]: https://github.com/stevenboyd78/sdsctl/compare/"
        f"v{__version__}...HEAD"
        in changelog
    )
    assert (
        "[0.22.0]: https://github.com/stevenboyd78/sdsctl/compare/"
        "v0.21.0...v0.22.0"
        in changelog
    )


def test_current_release_changelog_covers_v021_feature_groups() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("## [0.21.0] - ")
    end = changelog.index("\n## [0.20.2]", start)
    release = changelog[start:end]
    normalized = " ".join(release.split())

    for required in (
        "Favorites Workspace",
        "verified-storage",
        "RadioReference",
        "user-initiated and assisted",
        "`GLT,FL`",
        "`FQK`",
        "`URC`",
        "`AST`/`APR`",
        "`PWF`/`GWF`/`GW2`",
        "`MNU`",
        "`MSI`",
        "sds200-python",
        "`sdsctl`",
        "PyPI distribution `sds200`",
        "`src/sds200`",
        "Home Assistant",
        "generic container deployment foundation",
        "Docker Compose",
        "rootless Podman",
        "remote runtime boundaries",
    ):
        assert required in release or required in normalized


def test_current_release_changelog_covers_v022_feature_groups() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("## [0.22.0] - ")
    end = changelog.index("\n## [0.21.0]", start)
    release = changelog[start:end]
    normalized = " ".join(release.split())

    for required in (
        "authenticated LAN web-dashboard foundation",
        "Favorites Workspace editor",
        "capability and interface field-parity audit",
        "all 34 renderer-neutral",
        "SDS100 GSI/PSI battery telemetry",
        "Home Assistant MQTT Discovery sensors",
        "responsive `SDS200 Display`",
        "exact semantic controls",
        "Tone-Out Tone A and Tone B",
        "modular web-theme packaging",
        "modular Home Assistant theme packaging",
        "modular TUI theme packaging",
        "managed third-party theme discovery",
        "managed web-theme activation",
        "managed terminal-theme activation",
        "managed Home Assistant theme activation",
    ):
        assert required in release or required in normalized
