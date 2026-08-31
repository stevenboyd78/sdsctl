from __future__ import annotations

import re
import tomllib
from pathlib import Path

from sds200 import __version__

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIRECTORY = ROOT / ".github" / "workflows"
PYTHON_BASE_DIGEST = "83ff1d245a3d57d04152252d3ef9cb361494d0b3395abd65a5ebe91c401c8e83"
APPROVED_ACTIONS = {
    "actions/checkout": (
        "3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/checkout v7.0.1",
    ),
    "actions/setup-python": (
        "5fda3b95a4ea91299a34e894583c3862153e4b97",
        "actions/setup-python v7.0.0",
    ),
    "actions/setup-node": (
        "820762786026740c76f36085b0efc47a31fe5020",
        "actions/setup-node v7.0.0",
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
        "37fe631027851001ddb9b187196cc803df7f5f0e",
        "docker/setup-buildx-action v4.3.0",
    ),
    "docker/login-action": (
        "dbcb813823bdd20940b903addbd779551569679f",
        "docker/login-action v4.6.0",
    ),
    "docker/build-push-action": (
        "53b7df96c91f9c12dcc8a07bcb9ccacbed38856a",
        "docker/build-push-action v7.3.0",
    ),
}


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _workflow_executables(contents: str) -> tuple[str, ...]:
    """Collect executable run lines and action references, excluding comments."""

    lines = contents.splitlines()
    executables: list[str] = []
    for index, line in enumerate(lines):
        uses_match = re.match(r"^\s*-?\s*uses:\s+([^#\s]+)", line)
        if uses_match is not None:
            executables.append(uses_match.group(1))

        run_match = re.match(r"^(\s*)-?\s*run:\s*(.*?)\s*$", line)
        if run_match is None:
            continue
        indentation, value = run_match.groups()
        if value not in {"|", "|-", ">", ">-"}:
            if value and not value.startswith("#"):
                executables.append(value)
            continue

        run_indentation = len(indentation)
        for command_line in lines[index + 1 :]:
            stripped = command_line.strip()
            if stripped and len(command_line) - len(command_line.lstrip()) <= run_indentation:
                break
            if stripped and not stripped.startswith("#"):
                executables.append(stripped)
    return tuple(executables)


def _single_executable_index(executables: tuple[str, ...], command: str) -> int:
    matches = tuple(index for index, observed in enumerate(executables) if observed == command)
    assert len(matches) == 1, (command, matches, executables)
    return matches[0]


def test_current_status_matches_all_release_metadata() -> None:
    project = tomllib.loads(_read("pyproject.toml"))["project"]
    readme_status = (
        _read("README.md").split("## Project status", 1)[1].split("## Acknowledgments", 1)[0]
    )
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
        from_lines = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
        assert from_lines == expected, relative_path


def test_dependabot_retains_python_actions_and_both_docker_roots() -> None:
    dependabot = _read(".github/dependabot.yml")
    updates = re.findall(
        r"^  - package-ecosystem: ([^\n]+)\n"
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
    gallery_command = "python scripts/generate_web_dashboard_screenshots.py --verify-gallery"
    audit_command = "node scripts/audit_web_dashboard_browser.mjs --timeout-ms 30000"
    repeatability_command = (
        "python scripts/generate_web_dashboard_screenshots.py --verify-repeatability "
        "--only theme-system-1920x1080.png "
        "--only theme-system-waterfall-1920x1080.png "
        "--only theme-pip-boy-inspired-waterfall-800x480.png"
    )
    build_command = "python -m build"
    sdist_gallery_command = (
        "python scripts/generate_web_dashboard_screenshots.py --verify-sdist dist"
    )
    twine_command = "python -m twine check dist/*"

    assert project["tool"]["coverage"]["report"]["fail_under"] == 86
    for workflow in (".github/workflows/ci.yml", ".github/workflows/release.yml"):
        executables = _workflow_executables(_read(workflow))
        assert coverage_command in executables
        ordered_commands = (
            gallery_command,
            audit_command,
            repeatability_command,
            build_command,
            sdist_gallery_command,
            twine_command,
        )
        ordered_indices = tuple(
            _single_executable_index(executables, command) for command in ordered_commands
        )
        assert ordered_indices == tuple(sorted(ordered_indices)), (workflow, ordered_indices)

    release_executables = _workflow_executables(_read(".github/workflows/release.yml"))
    upload_action = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
    assert _single_executable_index(release_executables, twine_command) < (
        _single_executable_index(release_executables, upload_action)
    )
    assert 'python-version: "3.14"' in _read(".github/workflows/release.yml")

    releasing = _read("docs/releasing.md")
    normalized_releasing = " ".join(releasing.replace("\\\n", " ").split())
    assert gallery_command in releasing
    assert audit_command in releasing
    assert repeatability_command in normalized_releasing
    assert sdist_gallery_command in releasing


def test_workflow_execution_parser_ignores_comments_and_labels() -> None:
    contents = """\
steps:
  # run: python fake-comment.py
  - name: python fake-label.py
    run: |
      # python fake-block-comment.py
      python real-block.py
  - run: python real-inline.py
  # uses: fake/comment@123
  - uses: actions/example@123
"""

    assert _workflow_executables(contents) == (
        "python real-block.py",
        "python real-inline.py",
        "actions/example@123",
    )
