from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.classify_ci_changes import (
    ZERO_SHA,
    classify_paths,
    decide_scope,
    is_documentation_path,
    main,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/web-dashboard.md",
        "docs/assets/dashboard preview.png",
        "docs/theme-font-sources.json",
        "wiki/Home.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
    ],
)
def test_documentation_surfaces_are_recognized(path: str) -> None:
    assert is_documentation_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/sds200/web.py",
        "tests/test_web_server.py",
        "scripts/check_docs.py",
        ".github/workflows/ci.yml",
        "pyproject.toml",
        "../README.md",
        "/tmp/README.md",
    ],
)
def test_every_other_surface_remains_full_suite(path: str) -> None:
    assert not is_documentation_path(path)


def test_only_documentation_paths_select_the_bounded_scope() -> None:
    decision = classify_paths(["README.md", "docs/web-dashboard.md", "README.md"])

    assert not decision.full_suite
    assert decision.scope == "documentation"
    assert decision.changed_paths == ("README.md", "docs/web-dashboard.md")


@pytest.mark.parametrize(
    "paths",
    [
        [],
        ["src/sds200/web.py"],
        ["docs/web-dashboard.md", "tests/test_web_server.py"],
        ["docs/web-dashboard.md", ".github/workflows/ci.yml"],
    ],
)
def test_empty_unknown_and_mixed_changes_fail_closed(paths: list[str]) -> None:
    decision = classify_paths(paths)

    assert decision.full_suite
    assert decision.scope == "full"


def test_untrusted_path_text_is_not_copied_into_workflow_output_reason() -> None:
    decision = classify_paths(["src/name\nfull_suite=false.py"])

    assert decision.full_suite
    assert "\n" not in decision.reason
    assert "full_suite" not in decision.reason


@pytest.mark.parametrize("event", ["workflow_dispatch", "schedule", "", "pull_request_target"])
def test_non_diff_events_require_full_ci(tmp_path: Path, event: str) -> None:
    decision = decide_scope(event=event, base="base", head="head", repo=tmp_path)

    assert decision.full_suite
    assert decision.changed_paths == ()


@pytest.mark.parametrize(
    ("base", "head"),
    [("", "head"), ("base", ""), (ZERO_SHA, "head")],
)
def test_missing_or_unusable_revisions_require_full_ci(
    tmp_path: Path, base: str, head: str
) -> None:
    decision = decide_scope(event="push", base=base, head=head, repo=tmp_path)

    assert decision.full_suite
    assert decision.changed_paths == ()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_git_diff_keeps_both_sides_of_runtime_to_documentation_rename(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "CI classifier test")
    _git(repo, "config", "user.email", "ci-classifier@example.invalid")
    source = repo / "src" / "runtime.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "src/runtime.py")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    target = repo / "docs" / "runtime.md"
    target.parent.mkdir()
    source.rename(target)
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", "rename")
    head = _git(repo, "rev-parse", "HEAD")

    decision = decide_scope(event="pull_request", base=base, head=head, repo=repo)

    assert decision.full_suite
    assert decision.changed_paths == ("docs/runtime.md", "src/runtime.py")


def test_cli_writes_stable_github_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "github-output"

    assert (
        main(
            [
                "--event",
                "workflow_dispatch",
                "--github-output",
                str(output),
            ]
        )
        == 0
    )

    assert output.read_text(encoding="utf-8").splitlines() == [
        "full_suite=true",
        "scope=full",
        "changed_count=0",
        "reason=a non-diff event requires full CI",
    ]
    assert "Full suite: yes" in capsys.readouterr().out


def test_ci_workflow_runs_the_classifier_before_any_selective_job() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    classify, ordinary = workflow.split("  test:\n", 1)
    test_job, namespace = ordinary.split("  browser-namespace:\n", 1)
    namespace_job, package = namespace.split("  package:\n", 1)

    assert "  classify:\n" in classify
    assert "fetch-depth: 0" in classify
    assert "python scripts/classify_ci_changes.py" in classify
    assert "github.event.pull_request.base.sha || github.event.before" in classify
    assert "github.event.pull_request.head.sha || github.sha" in classify
    assert "Require recognized validation scope" in classify
    assert "true:full|false:documentation" in classify
    assert "paths-ignore:" not in workflow
    for selective_job, expected_full_guards in ((test_job, 6), (namespace_job, 8)):
        assert "needs: classify" in selective_job
        pre_runner = selective_job.split("runs-on:", 1)[0]
        assert "if: needs.classify.outputs.full_suite" not in pre_runner
        assert "Confirm bounded documentation-only scope" in selective_job
        assert "if: needs.classify.outputs.full_suite == 'false'" in selective_job
        assert (
            selective_job.count("if: needs.classify.outputs.full_suite == 'true'")
            == expected_full_guards
        )
    assert "needs: classify" in package
    assert "if: needs.classify.outputs.full_suite" not in package
