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


def test_all_optional_dependency_extra_is_exact_runtime_union() -> None:
    project = tomllib.loads(_read("pyproject.toml"))["project"]
    optional = project["optional-dependencies"]
    runtime_extras = ("tui", "web", "mqtt", "playback")
    runtime_union = {
        dependency
        for extra in runtime_extras
        for dependency in optional[extra]
    }

    assert set(optional["all"]) == runtime_union
    assert len(optional["all"]) == len(runtime_union)


def test_roadmap_records_active_milestone_and_completed_release_boundaries() -> None:
    roadmap = ROADMAP.read_text(encoding="utf-8")
    active_milestone = roadmap.split("## Active milestone", 1)[1].split(
        "## Deferred hardware validation", 1
    )[0]
    normalized_active_milestone = " ".join(active_milestone.split())
    normalized_roadmap = " ".join(roadmap.split())

    assert (
        "### Milestone 32.4 — Advanced Home Assistant App remote access and multi-display deployment"
        in active_milestone
    )
    for required in (
        "Milestone 32.3 is closed through reviewed pull request 222",
        "bbe9054407344ccf4ac11f79119e24728eeb86ae",
        "authenticated remote-daemon and native-dashboard boundaries",
        "installation or upgrade must continue to expose no daemon-client or "
        "native-dashboard TCP port",
        "authenticated daemon-client listener on container port 50443",
        "native HTTPS dashboard on container port 8443",
        "query `/addons/self/info` at startup",
        "Supervisor-assigned private container address",
        "creates host-wide Docker port bindings",
        "trusted private LAN with host firewall rules restricted to intended client "
        "addresses",
        "Store the persistent server certificate, mode-`0600` private key",
        "without the Home Assistant Ingress context",
        "Home Assistant management tab, bridge-key workflow, and Core-integration "
        "routes are absent",
        "Ingress-only advanced-access workspace",
        "multiple named remote-client identities",
        "App must never restart Home Assistant Core automatically",
        "at least two independent private-LAN consumers",
        "separate Raspberry Pi display host",
        "#### Closed Milestone 32.3 — Isolated container remote-daemon and "
        "thin-client deployment",
        "Milestone 32.2 is closed through reviewed pull request 221",
        "ebc987926b846dc62c83478341a0c6b2ef250603",
        "one explicit native-Linux Docker Engine deployment",
        "standalone remote Compose manifest",
        "fixed private container address",
        "Publish exactly two host mappings",
        "UDP 50000 for the scanner's existing RTP input",
        "non-mutating deployment-preflight command",
        "one operator-owned configuration tree read-only",
        "ordinary `sds200[tui,playback]` installation",
        "Raspberry Pi TUI runs",
        "containerized native dashboard publication is not introduced here",
        "Physical acceptance must use a native-Linux Docker Engine host",
        "Advanced Home Assistant App native-dashboard and daemon-client port options",
        "belong to Milestone 32.4",
        "#### Closed Milestone 32.2 — Packaged remote daemon startup and client profiles",
        "Milestone 32.1 is closed through reviewed pull requests 219 and 220",
        "f2783eb06e901cb05a37bcb06446db951aa658a7",
        "db6720ad9e42d05b63b9c6eedf02f8186163a260",
        "ordinary supported Python installation",
        "daemon-remote.toml",
        "An absent or explicitly disabled document",
        "open no TCP listener",
        "one scanner command transport, one PSI loop, one demand-driven Waterfall "
        "session, and one RTSP/RTP audio input",
        "all-or-nothing credential reload boundary",
        "Bind address, port, certificate, and private-key changes continue to require "
        "a daemon restart",
        "dedicated, strict, versioned remote-client profile document",
        "Credential bytes remain in the referenced secret file",
        "sdsctl daemon-client",
        "sdsctl tui --daemon-client",
        "mutually exclusive with local socket overrides",
        "Without the selector, all commands retain their current local",
        "socket defaults byte for byte",
        "bounded reconnect and ordered resynchronization behavior",
        "beginner-oriented setup for an ordinary daemon host and one Raspberry Pi "
        "TUI client",
        "does not publish a Docker, Compose, systemd, Home Assistant App",
        "#### Closed Milestone 32.1 — Authenticated remote daemon client/server foundation",
        "Versioned challenge/proof authentication",
        "one shared local-or-remote transport boundary",
        "#### Closed Milestone 31.2 — v0.27.0 release and publication closure",
        "synchronized the Python package, import version, and Home Assistant App at 0.27.0",
        "Home Assistant Core integration at 0.1.5",
        "d24897dcd4ea8a43d762a46fb48fe44bbea1ad8e",
        "normal GitHub Release was created from the genuine tag and marked Latest",
        "three legacy frame-count cards",
        "removed its exact temporary resource-registry rollback copy",
        "#### Closed Milestone 31.1 — Duration-based Waterfall history and frequency pointer",
        "Milestone 30.2 is closed through reviewed pull request 213",
        "9e693474e9bbd7c551a302b3c66df450a86011f9",
        "immutable `v0.26.1` tag",
        "Public PyPI now exposes the reviewed `sds200[all]` dependency union",
        "Add bounded duration-based history to the web dashboard",
        "Existing 60-, 120-, and 240-frame card configurations remain valid",
        "duration mode is an explicit alternative rather than a silent migration",
        "ordered capture or receipt time",
        "cap both elapsed duration and frame count",
        "Paused rendering may preserve the bounded visible history",
        "one shared scanner-side Waterfall session contract",
        "optional display-only frequency pointer",
        "linear interpolation across the current typed lower and upper span",
        "work with mouse, keyboard, and touch input",
        "hide the frequency value rather than reuse stale bounds",
        "must not tune, hold, search, change center frequency or span",
        "Waterfall samples remain relative and uncalibrated",
        "one renderer-neutral duration and pointer model",
        "physical SDS200 and Home Assistant OS acceptance",
        "TUI Waterfall rendering, GUI work, scanner tuning",
        "alternative GW2 syntax",
        "#### Closed Milestone 30.2 — v0.26.1 installation release and publication closure",
        "retained the separately versioned Home Assistant Core integration at 0.1.5",
        "#### Closed Milestone 30.1 — Installation experience and beginner documentation",
        "performed no package, container, App, tag, release, or wiki publication",
        "#### Closed Milestone 29.7 — v0.26.0 release and publication closure",
        "Home Assistant Core integration at 0.1.5",
        "Exact `GW2,1,ON` returned `ERR\\r` and no binary frame",
        "qualified phase-stable text `PWF`/`GWF` path remains authoritative",
        "aggregate Home Assistant card resource",
        "removed completion-relative text-GWF drift",
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


def test_v0270_release_documentation_names_current_generic_image() -> None:
    readme = _read("README.md")
    deployment = _read("docs/container-deployment.md")
    containers = _read("wiki/Containers.md")
    installation = _read("wiki/Installation.md")
    normalized_installation = " ".join(installation.split())

    assert "Version `0.27.0`" in readme
    assert "bounded elapsed-time" in readme
    assert "display-only frequency pointer" in readme
    assert "theboyd78/sdsctl:0.27.0" in readme
    assert "theboyd78/sdsctl:latest" in readme

    for document in (deployment, containers):
        assert "theboyd78/sdsctl:0.27.0" in document
        assert "theboyd78/sdsctl:latest" in document
        assert "future matching release tags" not in document

    assert "## Upgrade to v0.27.0" in installation
    assert 'python -m pip install --upgrade "sds200==0.27.0"' in installation
    assert 'python -m pip install --upgrade "sds200[all]==0.27.0"' in installation
    assert "docker pull theboyd78/sdsctl:0.27.0" in installation
    assert "15-, 30-, and 60-second Waterfall history modes" in normalized_installation
    assert "compatible 60-, 120-, and 240-frame configurations" in normalized_installation
    assert "does not tune, hold, search, change scanner span" in normalized_installation
    assert (
        "Repository-root `compose.yaml` and `compose.usb.yaml` remain source-built" in installation
    )
    assert "compatibility-sensitive `sds200` name, slug, GHCR image identity" in installation
    assert "Core integration remains independently versioned at 0.1.5" in installation


def test_v0270_home_assistant_release_gate_is_explicit() -> None:
    guide = _read("docs/home-assistant-app.md")
    start = guide.index("### v0.27.0 release acceptance gate")
    end = guide.index("\n### v0.26.1 release acceptance gate", start)
    release_gate = guide[start:end]
    normalized = " ".join(release_gate.split())

    for required in (
        "genuine v0.27.0 tag",
        "without a Local App, Local integration, retained share, private capture",
        "all twenty-four fixed MQTT Discovery components",
        "aggregate plus individual card resources",
        "60-, 120-, and 240-frame Waterfall cards",
        "15-, 30-, and 60-second choices",
        "one shared scanner-side Waterfall session",
        "pointer works across spectrum and history",
        "does not tune, hold, search, change span",
        "independently versioned at 0.1.5",
        "does not require installation, replacement, key rotation",
        "only runtime owner of scanner control",
        "Remove any deliberately named release-validation component",
    ):
        assert required in release_gate or required in normalized


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
