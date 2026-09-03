from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
WIKI_SOURCE_DIRECTORY = ROOT / "wiki"

REQUIRED_FILES = (
    Path("README.md"),
    Path("ACKNOWLEDGMENTS.md"),
    Path("CHANGELOG.md"),
    Path("ROADMAP.md"),
    Path("CONTRIBUTING.md"),
    Path("CODE_OF_CONDUCT.md"),
    Path("SECURITY.md"),
    Path("SUPPORT.md"),
    Path("docs/configuration.md"),
    Path("docs/daemon-deployment.md"),
    Path("docs/managed-pi-display.md"),
    Path("docs/releasing.md"),
    Path("docs/supported-models.md"),
    Path("wiki/Home.md"),
    Path("wiki/_Sidebar.md"),
    Path("wiki/Audio-and-Recordings.md"),
    Path("wiki/Containers.md"),
    Path("wiki/Favorites-and-RadioReference.md"),
    Path("wiki/First-Connection.md"),
    Path("wiki/Home-Assistant.md"),
    Path("wiki/Operations-and-Diagnostics.md"),
    Path("wiki/Python-API.md"),
    Path("wiki/Using-sdsctl.md"),
    Path("wiki/Web-Dashboard.md"),
    Path("wiki/Installation.md"),
    Path("wiki/Raspberry-Pi-Display.md"),
    Path("wiki/Troubleshooting.md"),
    Path("wiki/Publishing.md"),
    Path(".github/PULL_REQUEST_TEMPLATE.md"),
    Path(".github/ISSUE_TEMPLATE/bug_report.yml"),
    Path(".github/ISSUE_TEMPLATE/feature_request.yml"),
)

MARKDOWN_LINK = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
PYTHON_EXTRA_REFERENCE = re.compile(r"(?:sds200|\.)\[([a-z0-9_, -]+)]")
README_LINE_LIMIT = 350


def markdown_files() -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in ROOT.rglob("*.md")
            if not any(part.startswith(".") and part != ".github" for part in path.parts)
            and ".venv" not in path.parts
            and "build" not in path.parts
            and "dist" not in path.parts
        )
    )


def markdown_link_path(raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split(maxsplit=1)[0].strip("\"'")
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return None

    target = unquote(target.split("#", 1)[0])
    return target or None


def local_link_target(markdown: Path, raw_target: str) -> Path | None:
    target = markdown_link_path(raw_target)
    if target is None:
        return None

    resolved = (markdown.parent / target).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"{markdown.relative_to(ROOT)} links outside the repository") from exc

    if markdown.parent == WIKI_SOURCE_DIRECTORY and not resolved.suffix:
        wiki_source = resolved.with_suffix(".md")
        if wiki_source.is_file():
            return wiki_source

    return resolved


def uses_raw_wiki_markdown_route(markdown: Path, raw_target: str) -> bool:
    if markdown.parent != WIKI_SOURCE_DIRECTORY:
        return False

    target = markdown_link_path(raw_target)
    if target is None or not target.endswith(".md"):
        return False

    resolved = (markdown.parent / target).resolve()
    return resolved.parent == WIKI_SOURCE_DIRECTORY


def main() -> int:
    errors: list[str] = []

    for required in REQUIRED_FILES:
        if not (ROOT / required).exists():
            errors.append(f"Missing required project file: {required}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_lines = len(readme.splitlines())
    if readme_lines > README_LINE_LIMIT:
        errors.append(
            f"README has {readme_lines} lines; the beginner landing page limit is "
            f"{README_LINE_LIMIT}. Move detailed instructions to the wiki."
        )
    if "Milestone 1 provides:" in readme:
        errors.append("README still leads with obsolete Milestone 1 documentation.")
    if "docs/transports.md" not in readme:
        errors.append("README does not link to transport documentation.")
    if "SECURITY.md" not in readme:
        errors.append("README does not link to the security policy.")
    if "ROADMAP.md" not in readme:
        errors.append("README does not link to the project roadmap.")
    if "docs/configuration.md" not in readme:
        errors.append("README does not link to layered configuration documentation.")

    with (ROOT / "pyproject.toml").open("rb") as stream:
        optional_extras = set(
            tomllib.load(stream)["project"]["optional-dependencies"]
        )

    for markdown in markdown_files():
        text = markdown.read_text(encoding="utf-8")
        for reference in PYTHON_EXTRA_REFERENCE.findall(text):
            referenced_extras = {
                extra.strip() for extra in reference.split(",") if extra.strip()
            }
            unknown_extras = referenced_extras - optional_extras
            if unknown_extras:
                errors.append(
                    f"{markdown.relative_to(ROOT)} references unknown Python extras: "
                    f"{', '.join(sorted(unknown_extras))}"
                )
        for raw_target in MARKDOWN_LINK.findall(text):
            if uses_raw_wiki_markdown_route(markdown, raw_target):
                errors.append(
                    f"{markdown.relative_to(ROOT)} uses a raw Markdown wiki route: "
                    f"{raw_target}; use an extensionless wiki page link"
                )
            try:
                target = local_link_target(markdown, raw_target)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if target is not None and not target.exists():
                errors.append(
                    f"{markdown.relative_to(ROOT)} has a broken link: {raw_target}"
                )

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Documentation checks passed for {len(markdown_files())} Markdown files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
