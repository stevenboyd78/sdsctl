from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ROADMAP = REPOSITORY_ROOT / "ROADMAP.md"
CURRENT_REPOSITORY_URL = "https://github.com/stevenboyd78/sdsctl"
LEGACY_REPOSITORY_URL = "https://github.com/stevenboyd78/sds200-python"
LIVE_REPOSITORY_OWNED_FILES = (
    "pyproject.toml",
    "Dockerfile",
    "repository.yaml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/workflows/home-assistant-app-image.yml",
    "home-assistant/sds200/config.yaml",
    "home-assistant/sds200/Dockerfile",
    "src/sds200/daemon_mqtt_home_assistant.py",
    "src/sds200/themes/home-assistant/compact/sds200-card.js",
    "src/sds200/themes/home-assistant/sds200-display/sds200-display-card.js",
    "src/sds200/themes/home-assistant/waterfall/sds200-waterfall-card.js",
    "docs/releasing.md",
    "wiki/Home.md",
    "wiki/Web-Dashboard.md",
    "wiki/_Sidebar.md",
)
BRANDING_ASSET_NAMES = (
    "logo.svg",
    "icon.svg",
    "logo-4k.png",
    "icon-2048.png",
    "wallpaper-1080p.png",
    "wallpaper-4k.png",
)


def _read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def _quoted_yaml_scalar(text: str, key: str) -> str:
    match = re.search(
        rf'^[ ]*{re.escape(key)}: "([^"\n]*)"$',
        text,
        flags=re.MULTILINE,
    )
    assert match is not None, f"missing quoted YAML scalar {key!r}"
    return match.group(1)


def test_python_distribution_package_and_entry_point_remain_compatible() -> None:
    project = tomllib.loads(_read("pyproject.toml"))

    assert project["project"]["name"] == "sds200"
    assert (REPOSITORY_ROOT / "src" / "sds200" / "__init__.py").is_file()
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/sds200"]
    assert project["tool"]["mypy"]["packages"] == ["sds200"]
    assert project["project"]["scripts"]["sdsctl"] == "sds200.cli:main"


def test_current_project_urls_use_sdsctl_repository() -> None:
    project = tomllib.loads(_read("pyproject.toml"))
    urls = project["project"]["urls"]

    assert urls["Homepage"] == CURRENT_REPOSITORY_URL
    assert urls["Repository"] == CURRENT_REPOSITORY_URL
    assert urls["Issues"] == f"{CURRENT_REPOSITORY_URL}/issues"
    assert urls["Changelog"] == f"{CURRENT_REPOSITORY_URL}/blob/main/CHANGELOG.md"


def test_home_assistant_compatibility_identity_remains_sds200() -> None:
    app_directory = REPOSITORY_ROOT / "home-assistant" / "sds200"
    manifest = _read("home-assistant/sds200/config.yaml")
    workflow = _read(".github/workflows/home-assistant-app-image.yml")
    lovelace_installer = _read("src/sds200/home_assistant_lovelace.py")

    assert app_directory.is_dir()
    assert _quoted_yaml_scalar(manifest, "name") == "sds200"
    assert _quoted_yaml_scalar(manifest, "slug") == "sds200"
    assert _quoted_yaml_scalar(manifest, "panel_title") == "sds200"
    assert _quoted_yaml_scalar(manifest, "image") == "ghcr.io/stevenboyd78/sds200-home-assistant"
    assert _quoted_yaml_scalar(workflow, "IMAGE_NAME") == "sds200-home-assistant"
    assert 'HOME_ASSISTANT_LOVELACE_CARD_FILENAME = "sds200-card.js"' in lovelace_installer
    assert 'HOME_ASSISTANT_LOVELACE_CARD_RESOURCE_URL' in lovelace_installer
    assert (
        'HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_FILENAME = "sds200-display-card.js"'
        in lovelace_installer
    )
    assert "HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_RESOURCE_URL" in lovelace_installer
    assert (
        'HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_FILENAME = "sds200-waterfall-card.js"'
        in lovelace_installer
    )
    assert "HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_RESOURCE_URL" in lovelace_installer


def test_generic_container_documentation_preserves_local_image_tag() -> None:
    assert "# docker build -t sds200-daemon ." in _read("Dockerfile")


def test_live_repository_owned_files_use_sdsctl_repository_url() -> None:
    for relative_path in LIVE_REPOSITORY_OWNED_FILES:
        contents = _read(relative_path)

        assert CURRENT_REPOSITORY_URL in contents, relative_path
        assert LEGACY_REPOSITORY_URL not in contents, relative_path


def test_generic_docker_hub_identity_is_isolated_from_python_and_home_assistant() -> None:
    workflow = _read(".github/workflows/docker-hub-image.yml")

    assert 'IMAGE_NAME: "theboyd78/sdsctl"' in workflow
    assert 'IMAGE_NAME: "sds200-home-assistant"' in _read(
        ".github/workflows/home-assistant-app-image.yml"
    )
    assert 'name = "sds200"' in _read("pyproject.toml")
    assert '"sds200[mqtt,web]"' in _read("Dockerfile")


def test_roadmap_records_completed_milestone_28_release_boundary() -> None:
    roadmap = ROADMAP.read_text(encoding="utf-8")
    active_milestone = roadmap.split("## Active milestone", 1)[1].split(
        "## Deferred hardware validation", 1
    )[0]
    normalized_active_milestone = " ".join(active_milestone.split())
    normalized_roadmap = " ".join(roadmap.split())

    assert "### Milestone 29.3 — v0.25.0 release and publication closure" in (
        active_milestone
    )
    for required in (
        "Milestones 29.1 and 29.2 are closed",
        "Python distribution, import version, and Home Assistant App at 0.25.0",
        "Home Assistant Core integration at 0.1.5",
        "media-source://sdsctl/live",
        "does not model the scanner as an output `media_player` entity",
        "daemon remains the only SDS200 RTSP/RTP owner",
        "Only one genuine matching `v0.25.0` tag",
        "Workflow success alone is not release acceptance",
        "pull request 205",
        "No new runtime capability enters Milestone 29.3",
        "public or anonymous live-audio URLs",
    ):
        assert required in normalized_active_milestone

    for required in (
        "### Milestone 28 complete — v0.24.0 release candidate",
        "Milestones 28.1 through 28.4 are closed at the pre-tag boundary",
        "exact read-only preview",
        "Private operator acceptance completed on August 29, 2026",
        "`getTrsTalkgroups` refresh against public RadioReference system `12042`",
        "controlled injected post-mutation failure",
        "SDS100 running firmware 1.26.01 in USB Mass Storage mode",
        "118-field `F-List` record",
        "All credentials, provider payloads, local programming values",
        "synchronized for 0.24.0",
        "shared 86 percent coverage floor",
        "Only one genuine matching `v0.24.0` tag may publish",
        "Workflow success alone is not release acceptance",
        "Milestone 29.1",
        "waiting dependency-update pull requests remain separate",
    ):
        assert required in roadmap or required in normalized_roadmap


def test_roadmap_preserves_closed_milestone_27_3_physical_acceptance() -> None:
    roadmap = ROADMAP.read_text(encoding="utf-8")
    normalized_roadmap = " ".join(roadmap.split())

    for required in (
        "Milestone 27.3 completed one responsive viewport-owned web workspace",
        "all six built-in themes, all five panes",
        "SDS200 running firmware 1.26.01",
        "exact merged commit `db2e6c0`",
        "Exact closure commit `dca445e`",
        "all four holds Off",
        "normal scanning active",
    ):
        assert required in roadmap or required in normalized_roadmap


def test_home_assistant_guide_records_milestone_27_4_physical_acceptance() -> None:
    guide = _read("docs/home-assistant-app.md")
    normalized_guide = " ".join(guide.split())

    for required in (
        "### Milestone 27.4 authenticated waterfall development acceptance",
        "`223303b2bec9a42b48641d90d4f39bc962bcdc0b`",
        "Home Assistant OS 18.2",
        "SDS200 running firmware 1.26.01",
        "exactly 240 hexadecimal source strings",
        "same-origin event-stream framing",
        "pause/resume froze only display updates",
        "restored the repository-managed App as the sole daemon",
        "No scanner identifiers, programmed frequencies, raw waterfall captures",
        "published v0.23.0 image still requires the separate repository-managed release acceptance",
    ):
        assert required in guide or required in normalized_guide


def test_roadmap_records_completed_milestone_27_4_web_waterfall_boundary() -> None:
    roadmap = ROADMAP.read_text(encoding="utf-8")
    milestone_group = roadmap.split(
        "### Milestone 27 — Adaptive scanner screens, hardening, and waterfall workspace",
        1,
    )[1].split("## Completed milestone groups", 1)[0]
    normalized_group = " ".join(milestone_group.split())

    for required in (
        "Milestone 27.4: responsive theme-aware web spectrum",
        "same-origin direct and Home Assistant Ingress streaming",
        "exact hexadecimal 240-value validation",
        "relative and uncalibrated labeling",
        "lifecycle controls",
        "deterministic cleanup",
        "physical branch-image acceptance",
    ):
        assert required in milestone_group or required in normalized_group
    assert "#### Planned Milestone 27.4 contract" not in milestone_group


def test_roadmap_preserves_completed_milestone_26_1_security_boundary() -> None:
    roadmap = ROADMAP.read_text(encoding="utf-8")
    active_milestone = roadmap.split("## Active milestone", 1)[1].split(
        "## Deferred hardware validation", 1
    )[0]
    milestone_group = roadmap.split(
        "### Milestone 26 — Authenticated access and post-v0.21 interface work",
        1,
    )[1].split("## Completed milestone groups", 1)[0]
    normalized_group = " ".join(milestone_group.split())

    assert "### Milestone 26.1 —" not in active_milestone
    for required in (
        "Milestone 26.1 completed explicit authenticated direct-TLS LAN access",
        "loopback-only operation as the default",
        "private Unix-domain socket boundary",
        "Home Assistant Ingress",
        "two authenticated concurrent sessions",
        "remote daemon-backed CLI/TUI transport",
    ):
        assert required in milestone_group or required in normalized_group


def test_web_guide_records_milestone_26_1_physical_acceptance() -> None:
    guide = _read("docs/web-dashboard.md")
    normalized_guide = " ".join(guide.split())

    for required in (
        "## Milestone 26.1 physical validation",
        "August 22, 2026",
        "https://192.168.0.40:8443",
        "two independent authenticated HTTPS sessions",
        "one daemon-owned UDP scanner-control connection",
        "one TCP RTSP session",
        "HTTP chunk counts demonstrate live delivery",
        "Authenticated LAN middleware deliberately omits",
        "no browser or operating-system trust store was changed",
    ):
        assert required in guide or required in normalized_guide


def test_branding_asset_paths_use_sdsctl_identity() -> None:
    for asset_name in BRANDING_ASSET_NAMES:
        assert (REPOSITORY_ROOT / "docs" / "assets" / f"sdsctl-{asset_name}").is_file()
        assert not (REPOSITORY_ROOT / "docs" / "assets" / f"sds200-python-{asset_name}").exists()


def test_readme_uses_sdsctl_logo_path() -> None:
    readme = _read("README.md")

    assert "docs/assets/sdsctl-logo.svg" in readme
    assert "docs/assets/sds200-python-logo.svg" not in readme


def test_branding_documentation_uses_sdsctl_asset_names() -> None:
    branding_readme = _read("docs/assets/README.md")

    for asset_name in BRANDING_ASSET_NAMES:
        assert f"sdsctl-{asset_name}" in branding_readme
        assert f"sds200-python-{asset_name}" not in branding_readme


def test_horizontal_logo_uses_sdsctl_identity() -> None:
    logo = _read("docs/assets/sdsctl-logo.svg")

    assert "sdsctl neon logo" in logo
    assert "SDSCTL" in logo
    assert "SDS200-PYTHON" not in logo
    assert "sds200-python neon logo" not in logo


def test_icon_uses_sdsctl_identity() -> None:
    icon = _read("docs/assets/sdsctl-icon.svg")

    assert "sdsctl neon icon" in icon
    assert "sds200-python neon icon" not in icon


def test_v024_release_documentation_names_current_generic_image() -> None:
    readme = _read("README.md")
    deployment = _read("docs/container-deployment.md")
    installation = _read("wiki/Installation.md")

    for document in (readme, deployment):
        assert "v0.24.0" in document
        assert "theboyd78/sdsctl:0.24.0" in document
        assert "theboyd78/sdsctl:latest" in document
        assert "future matching release tags" not in document

    assert "## Upgrade to v0.24.0" in installation
    assert 'python -m pip install --upgrade "sds200==0.24.0"' in installation
    assert "docker pull theboyd78/sdsctl:0.24.0" in installation
    assert (
        "Repository-root `compose.yaml` and `compose.usb.yaml` remain source-built" in installation
    )
    assert "compatibility-sensitive `sds200` name, slug, GHCR image identity" in installation


def test_milestone_28_4_records_sanitized_physical_acceptance() -> None:
    roadmap = ROADMAP.read_text(encoding="utf-8")
    guide = _read("docs/favorites-workspace-editor.md")
    research = _read("docs/favorites-format-research.md")
    normalized = " ".join((roadmap + guide + research).split())

    for required in (
        "Milestone 28 complete — v0.24.0 release candidate",
        "August 29, 2026",
        "SDS100 running firmware 1.26.01",
        "USB Mass Storage mode",
        "Ubuntu 26.04.1 LTS",
        "Python 3.14.4",
        "Docker 29.7.2",
        "forward/inverse digest chain closed",
        "original absent state",
        "safely unmounted",
        "118-field `F-List`",
        "catalog bytes unchanged",
        "does not claim separate physical SDS200 USB acceptance",
    ):
        assert required in roadmap or required in guide or required in normalized
