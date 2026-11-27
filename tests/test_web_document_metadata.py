from __future__ import annotations

from html.parser import HTMLParser
from importlib.resources import files

import pytest


class _DescriptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.descriptions: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.casefold() != "meta":
            return
        values = {name.casefold(): value for name, value in attrs if value is not None}
        if values.get("name", "").casefold() == "description":
            self.descriptions.append(values.get("content", ""))


@pytest.mark.parametrize(
    ("filename", "expected"),
    (
        (
            "dashboard.html",
            "Monitor supported Uniden SDS scanners with live activity, audio, "
            "recordings, diagnostics, and responsive display themes.",
        ),
        (
            "favorites-workspace.html",
            "Review and prepare Uniden SDS100 and SDS200 Favorites Lists in an "
            "accessible light, dark, or automatic browser workspace.",
        ),
        (
            "api-docs-swagger.html",
            "Explore the sdsctl API for scanner status, control, recordings, "
            "diagnostics, and integrations with Swagger UI.",
        ),
        (
            "api-docs-redoc.html",
            "Explore the sdsctl API for scanner status, control, recordings, "
            "diagnostics, and integrations with ReDoc.",
        ),
        (
            "scanner-display-admin.html",
            "Configure private sdsctl scanner display profiles, managed themes, "
            "typography, and kiosk presentation settings.",
        ),
    ),
)
def test_web_document_has_one_bounded_description(
    filename: str,
    expected: str,
) -> None:
    source = files("sds200.web_assets").joinpath(filename).read_text(encoding="utf-8")
    parser = _DescriptionParser()
    parser.feed(source)

    assert parser.descriptions == [expected]
    assert 50 <= len(expected) <= 160
