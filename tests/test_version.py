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
        "[0.26.1]: https://github.com/stevenboyd78/sdsctl/compare/"
        "v0.26.0...v0.26.1"
        in changelog
    )
    assert (
        "[0.26.0]: https://github.com/stevenboyd78/sdsctl/compare/"
        "v0.25.0...v0.26.0"
        in changelog
    )
    assert (
        "[0.25.0]: https://github.com/stevenboyd78/sdsctl/compare/"
        "v0.24.0...v0.25.0"
        in changelog
    )
    assert (
        "[0.24.0]: https://github.com/stevenboyd78/sdsctl/compare/"
        "v0.23.0...v0.24.0"
        in changelog
    )
    assert (
        "[0.23.0]: https://github.com/stevenboyd78/sdsctl/compare/"
        "v0.22.0...v0.23.0"
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


def test_current_release_changelog_covers_v023_feature_groups() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("## [0.23.0] - ")
    end = changelog.index("\n## [0.22.0]", start)
    release = changelog[start:end]
    normalized = " ".join(release.split())

    for required in (
        "adaptive scanner screen-profile parity",
        "qualified text-waterfall data plane",
        "audio-lifecycle and release-integrity hardening",
        "responsive web workspace",
        "Pip-Boy-inspired",
        "managed-theme validation and installation",
        "authenticated, theme-aware Waterfall workspace",
        "hexadecimal 240-value GWF records",
        "explicitly uncalibrated spectrum and rolling history",
    ):
        assert required in release or required in normalized


def test_current_release_changelog_covers_v024_feature_groups() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("## [0.24.0] - ")
    end = changelog.index("\n## [0.23.0]", start)
    release = changelog[start:end]
    normalized = " ".join(release.split())

    for required in (
        "explicit read-only preview",
        "reviewed assisted decisions",
        "full confirmation token",
        "guarded execution path",
        "conditional provenance publication",
        "exact reverse recovery",
        "physical SDS100 USB qualification",
        "absent versus empty provenance",
        "safe unmount",
        "constrained Textual layout",
        "System, Department, Site, and Channel scope cards",
    ):
        assert required in release or required in normalized


def test_current_release_changelog_covers_v025_feature_groups() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("## [0.25.0] - ")
    end = changelog.index("\n## [0.24.0]", start)
    release = changelog[start:end]
    normalized = " ".join(release.split())

    for required in (
        "SDS200 Waterfall",
        "media-source://sdsctl/live",
        "audio/mpeg",
        "shared daemon-owned MP3 encoder",
        "Ingress-only lifecycle workspace",
        "21 Textual-derived color schemes",
        "SHA-256-qualified resource URLs",
        "two-step in-page confirmation",
        "Home Assistant OS 18.2",
        "Core 2026.8.3",
        "SDS200 firmware 1.26.01",
        "single RTSP/RTP ownership",
        "internal_url",
        "docker/setup-buildx-action` 4.3.0",
        "docker/build-push-action` 7.3.0",
    ):
        assert required in release or required in normalized


def test_current_release_changelog_covers_v026_feature_groups() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("## [0.26.0] - ")
    end = changelog.index("\n## [0.25.0]", start)
    release = changelog[start:end]
    normalized = " ".join(release.split())

    for required in (
        "bounded Waterfall timing and status telemetry",
        "digest-qualified `sds200-cards.js`",
        "bounded exact-byte GW2 research substrate",
        "SDS200 firmware 1.26.01 LAN",
        "exact `ERR\\r`",
        "phase-stable text `PWF`/`GWF` path",
        "250 ms text-GWF schedule",
        "Refresh typed GST metadata",
        "60, 120, and 240-frame history selections",
        "Home Assistant's host-owned `grid_options`",
        "Show in sidebar",
    ):
        assert required in release or required in normalized


def test_current_release_changelog_covers_v0261_installation_release() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("## [0.26.1] - ")
    end = changelog.index("\n## [0.26.0]", start)
    release = changelog[start:end]
    normalized = " ".join(release.split())

    for required in (
        "`all` optional Python runtime extra",
        "exact union",
        "`tui`, `web`, `mqtt`, and `playback`",
        "beginner-oriented wiki guides",
        "1,278-line package README",
        "211-line project landing page",
        "PyPI now renders that same concise account",
        "350-line README ceiling",
        "validate documented Python extras against package metadata",
    ):
        assert required in release or required in normalized
