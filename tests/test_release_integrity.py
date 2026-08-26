from __future__ import annotations

import re
import tomllib
from pathlib import Path

from sds200 import __version__

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIRECTORY = ROOT / ".github" / "workflows"
PYTHON_BASE_DIGEST = (
    "83ff1d245a3d57d04152252d3ef9cb361494d0b3395abd65a5ebe91c401c8e83"
)
APPROVED_ACTIONS = {
    "actions/checkout": (
        "3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/checkout v7.0.1",
    ),
    "actions/setup-python": (
        "5fda3b95a4ea91299a34e894583c3862153e4b97",
        "actions/setup-python v7.0.0",
    ),
    "actions/upload-artifact": (
        "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "actions/upload-artifact v7.0.1",
    ),
    "actions/download-artifact": (
        "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        "actions/download-artifact v8.0.1",
    ),
    "pypa/gh-action-pypi-publish": (
        "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
        "pypa/gh-action-pypi-publish v1.14.2",
    ),
    "home-assistant/builder/actions/prepare-multi-arch-matrix": (
        "4de35182ce1e329181bffcbcc84d33db5e2c7e10",
        "home-assistant/builder 2026.06.0",
    ),
    "home-assistant/builder/actions/build-image": (
        "4de35182ce1e329181bffcbcc84d33db5e2c7e10",
        "home-assistant/builder 2026.06.0",
    ),
    "home-assistant/builder/actions/publish-multi-arch-manifest": (
        "4de35182ce1e329181bffcbcc84d33db5e2c7e10",
        "home-assistant/builder 2026.06.0",
    ),
    "docker/setup-qemu-action": (
        "96fe6ef7f33517b61c61be40b68a1882f3264fb8",
        "docker/setup-qemu-action v4.2.0",
    ),
    "docker/setup-buildx-action": (
        "bb05f3f5519dd87d3ba754cc423b652a5edd6d2c",
        "docker/setup-buildx-action v4.2.0",
    ),
    "docker/login-action": (
        "dbcb813823bdd20940b903addbd779551569679f",
        "docker/login-action v4.6.0",
    ),
    "docker/build-push-action": (
        "f9f3042f7e2789586610d6e8b85c8f03e5195baf",
        "docker/build-push-action v7.2.0",
    ),
}


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_current_status_matches_all_release_metadata() -> None:
    project = tomllib.loads(_read("pyproject.toml"))["project"]
    readme_status = _read("README.md").split("## Project status", 1)[1].split(
        "## Acknowledgments", 1
    )[0]
    status_match = re.search(r"Version `([^`]+)`", readme_status)
    app_match = re.search(
        r'^version: "([^"\n]+)"$',
        _read("home-assistant/sds200/config.yaml"),
        flags=re.MULTILINE,
    )

    assert status_match is not None
    assert app_match is not None
    assert status_match.group(1) == project["version"] == __version__
    assert app_match.group(1) in {__version__, f"{__version__}-dev"}


def test_every_external_workflow_action_uses_a_reviewed_immutable_commit() -> None:
    workflow_paths = sorted(WORKFLOW_DIRECTORY.glob("*.yml")) + sorted(
        WORKFLOW_DIRECTORY.glob("*.yaml")
    )
    observed: set[str] = set()

    for path in workflow_paths:
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            uses_match = re.match(r"^\s*-?\s*uses:\s+(.+?)\s*$", line)
            if uses_match is None:
                continue
            reference = uses_match.group(1)
            if reference.startswith("./"):
                continue

            match = re.fullmatch(r"([^@\s#]+)@([^\s#]+)", reference)
            assert match is not None, (
                f"unsupported external action reference in {path}: {reference}"
            )

            action, commit = match.groups()
            assert action in APPROVED_ACTIONS, f"unreviewed action in {path}: {action}"
            expected_commit, expected_comment = APPROVED_ACTIONS[action]
            assert re.fullmatch(r"[0-9a-f]{40}", commit), (path, action, commit)
            assert commit == expected_commit, (path, action, commit)
            assert index > 0 and lines[index - 1].strip() == f"# {expected_comment}"
            observed.add(action)

    assert observed == set(APPROVED_ACTIONS)


def test_container_bases_share_one_reviewed_multi_architecture_digest() -> None:
    pinned_base = f"FROM python:3.14-slim@sha256:{PYTHON_BASE_DIGEST}"
    expected = [f"{pinned_base} AS build", pinned_base]

    for relative_path in ("Dockerfile", "home-assistant/sds200/Dockerfile"):
        dockerfile = _read(relative_path)
        from_lines = [
            line
            for line in dockerfile.splitlines()
            if line.startswith("FROM ")
        ]
        assert from_lines == expected, relative_path


def test_dependabot_retains_python_actions_and_both_docker_roots() -> None:
    dependabot = _read(".github/dependabot.yml")
    updates = re.findall(
        r'^  - package-ecosystem: ([^\n]+)\n'
        r'^    directory: "([^"\n]+)"\n'
        r"^    schedule:\n"
        r"^      interval: ([^\n]+)$",
        dependabot,
        flags=re.MULTILINE,
    )

    assert updates == [
        ("pip", "/", "monthly"),
        ("github-actions", "/", "monthly"),
        ("docker", "/", "monthly"),
        ("docker", "/home-assistant/sds200", "monthly"),
    ]
    assert dependabot.count("  - package-ecosystem:") == len(updates)


def test_ci_and_release_workflows_share_the_measured_coverage_floor() -> None:
    project = tomllib.loads(_read("pyproject.toml"))
    coverage_command = "pytest --cov=sds200 --cov-report=term-missing"

    assert project["tool"]["coverage"]["report"]["fail_under"] == 86
    assert coverage_command in _read(".github/workflows/ci.yml")
    assert coverage_command in _read(".github/workflows/release.yml")
    assert 'python-version: "3.14"' in _read(".github/workflows/release.yml")
