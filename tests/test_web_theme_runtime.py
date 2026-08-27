from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sds200.web_dashboard import create_web_dashboard_app
from sds200.web_theme_runtime import (
    build_web_theme_runtime,
    read_web_theme_stylesheet,
)


class ForbiddenDaemonClient:
    def __enter__(self) -> ForbiddenDaemonClient:
        raise AssertionError("theme assets must not connect to the daemon")

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback


def _write_web_theme(
    root: Path,
    *,
    identifier: str = "solarized",
    label: str = "Solarized",
    order: int = 15,
    css: str | None = None,
) -> Path:
    package = root / "web" / identifier
    package.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "interface": "web",
        "id": identifier,
        "label": label,
        "order": order,
        "stylesheet": "theme.css",
        "color_scheme": "dark",
        "theme_colors": {"light": "#002b36", "dark": "#002b36"},
    }
    (package / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (package / "theme.css").write_text(
        css
        or f':root[data-theme="{identifier}"] {{ --surface: #002b36; }}\n',
        encoding="utf-8",
    )
    return package


def _app(root: Path) -> FastAPI:
    return create_web_dashboard_app(
        ForbiddenDaemonClient,
        managed_theme_root=root,
    )


def test_runtime_defaults_to_exact_built_in_registry() -> None:
    runtime = build_web_theme_runtime()

    assert runtime.registry.identifiers == (
        "system",
        "lcars",
        "matrix",
        "first-responder",
        "amateur-radio",
        "pip-boy-inspired",
    )
    assert runtime.managed_identifiers == ()
    assert runtime.ignored_managed_entries == 0
    assert all(asset.origin == "built-in" for asset in runtime.assets)


def test_runtime_rejects_implicit_or_relative_managed_roots() -> None:
    with pytest.raises(TypeError, match="pathlib.Path"):
        build_web_theme_runtime("/tmp/themes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must be absolute"):
        build_web_theme_runtime(Path("themes"))


def test_absent_managed_root_is_an_ordinary_built_in_startup(tmp_path: Path) -> None:
    runtime = build_web_theme_runtime(tmp_path / "missing")

    assert runtime.registry.identifiers[0] == "system"
    assert runtime.managed_identifiers == ()
    assert runtime.ignored_managed_entries == 0
    assert not (tmp_path / "missing").exists()


def test_runtime_merges_valid_web_theme_in_manifest_order(tmp_path: Path) -> None:
    package = _write_web_theme(tmp_path)

    runtime = build_web_theme_runtime(tmp_path)

    assert runtime.registry.identifiers == (
        "system",
        "lcars",
        "solarized",
        "matrix",
        "first-responder",
        "amateur-radio",
        "pip-boy-inspired",
    )
    assert runtime.managed_identifiers == ("solarized",)
    asset = runtime.require_asset("solarized")
    assert asset.origin == "managed"
    assert asset.managed_root == tmp_path
    assert read_web_theme_stylesheet(asset) == (package / "theme.css").read_bytes()


def test_runtime_isolates_malformed_and_cross_interface_entries(tmp_path: Path) -> None:
    _write_web_theme(tmp_path)
    malformed = tmp_path / "web" / "broken"
    malformed.mkdir()
    (malformed / "manifest.json").write_text("{}", encoding="utf-8")
    foreign = tmp_path / "home-assistant" / "untrusted"
    foreign.mkdir(parents=True)
    (foreign / "manifest.json").write_text("{}", encoding="utf-8")

    runtime = build_web_theme_runtime(tmp_path)

    assert runtime.managed_identifiers == ("solarized",)
    assert runtime.ignored_managed_entries == 2


def test_runtime_falls_back_to_built_ins_for_symlink_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    _write_web_theme(real_root)
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)

    runtime = build_web_theme_runtime(linked_root)

    assert runtime.managed_identifiers == ()
    assert runtime.ignored_managed_entries == 1


def test_dashboard_picker_bootstrap_and_route_include_managed_theme(tmp_path: Path) -> None:
    css = ':root[data-theme="solarized"] { color: #93a1a1; }\n'
    _write_web_theme(tmp_path, label="Solarized & Night", css=css)
    app = _app(tmp_path)
    asset = build_web_theme_runtime(tmp_path).require_asset("solarized")
    assert asset.package_sha256 is not None

    with TestClient(app) as client:
        shell = client.get("/")
        bootstrap = client.get("/assets/theme-bootstrap.js")
        stylesheet = client.get("/assets/themes/solarized/theme.css")
        stylesheet_source = client.get(
            "/assets/themes/solarized/theme.css"
            f"?sdsctl_source={asset.package_sha256}"
        )
        viewport_stylesheet = client.get("/assets/dashboard-viewport.css")

    assert shell.status_code == 200
    assert '<option value="solarized">Solarized &amp; Night</option>' in shell.text
    managed_link = (
        '<link rel="stylesheet" media="not all" '
        'data-sdsctl-managed-theme="solarized" '
        'data-sdsctl-managed-theme-href="assets/themes/solarized/theme.css">'
    )
    assert managed_link in shell.text
    assert 'data-sdsctl-managed-theme="solarized" href=' not in shell.text
    assert shell.text.index(managed_link) < shell.text.index(
        'href="assets/dashboard-viewport.css"'
    )
    assert shell.text.index('href="assets/dashboard-viewport.css"') < shell.text.index(
        'src="assets/theme-bootstrap.js"'
    )
    assert '"solarized"' in bootstrap.text
    assert "MANAGED_THEME_LINKS" in bootstrap.text
    assert 'const MANAGED_THEMES = new Set(["solarized"])' in bootstrap.text
    assert 'link.setAttribute(' in bootstrap.text
    assert 'link.removeAttribute("href")' in bootstrap.text
    assert bootstrap.text.index('link.addEventListener("error"') < bootstrap.text.index(
        "const storedSelection = readStoredTheme()"
    )
    assert stylesheet.status_code == 200
    assert css not in stylesheet.text
    assert (
        "@layer sdsctl-viewport-contract, sdsctl-shared, "
        "sdsctl-managed-theme;"
    ) in stylesheet.text
    assert (
        f'@import url("theme.css?sdsctl_source={asset.package_sha256}") '
        "layer(sdsctl-managed-theme);"
    ) in stylesheet.text
    assert stylesheet_source.status_code == 200
    assert stylesheet_source.text == css
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert stylesheet.headers["cache-control"] == "no-store"
    assert stylesheet.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in stylesheet.headers["content-security-policy"]
    assert viewport_stylesheet.status_code == 200
    assert "@layer sdsctl-viewport-contract" in viewport_stylesheet.text
    assert "overflow: hidden !important" in viewport_stylesheet.text


def test_browser_bootstrap_enables_only_selected_managed_theme_and_falls_back(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not available")
    _write_web_theme(tmp_path)
    with TestClient(_app(tmp_path)) as client:
        source = client.get("/assets/theme-bootstrap.js").text
    harness = f"""
"use strict";
const listeners = {{}};
const attributes = new Map();
const lifecycle = [];
const link = {{
  dataset: {{
    sdsctlManagedTheme: "solarized",
    sdsctlManagedThemeHref: "assets/themes/solarized/theme.css",
  }},
  media: "not all",
  addEventListener: (event, handler) => {{
    listeners[event] = handler;
    lifecycle.push(`listen-${{event}}`);
  }},
  hasAttribute: (name) => attributes.has(name),
  setAttribute: (name, value) => {{
    attributes.set(name, value);
    lifecycle.push(`set-${{name}}`);
  }},
  removeAttribute: (name) => {{
    attributes.delete(name);
    lifecycle.push(`remove-${{name}}`);
  }},
}};
const colorScheme = {{ content: "" }};
const themeColor = {{ content: "" }};
const picker = {{ value: "solarized" }};
let stored = "solarized";
global.document = {{
  documentElement: {{ dataset: {{}} }},
  querySelectorAll: (selector) =>
    selector === "link[data-sdsctl-managed-theme]" ? [link] : [],
  querySelector: (selector) => {{
    if (selector === 'meta[name="color-scheme"]') return colorScheme;
    if (selector === 'meta[name="theme-color"]') return themeColor;
    if (selector === "#theme-select") return picker;
    return null;
  }},
}};
global.window = {{
  localStorage: {{
    getItem: () => stored,
    setItem: (_key, value) => {{ stored = value; }},
  }},
  matchMedia: () => ({{ matches: false, addEventListener: () => {{}} }}),
}};
eval({json.dumps(source)});
if (window.sdsctlTheme.current() !== "solarized") throw new Error("stored theme");
if (document.documentElement.dataset.theme !== "solarized") throw new Error("dataset");
if (link.media !== "all") throw new Error("managed link not enabled");
if (!attributes.has("href")) throw new Error("managed href not activated");
if (lifecycle.indexOf("listen-error") > lifecycle.indexOf("set-href")) {{
  throw new Error("href activated before error listener");
}}
if (picker.value !== "solarized") throw new Error("initial picker");
if (window.sdsctlTheme.select("missing") !== "system") throw new Error("fallback");
if (link.media !== "not all") throw new Error("managed link not disabled");
if (stored !== "system") throw new Error("fallback not persisted");
if (picker.value !== "system") throw new Error("fallback picker");
listeners.error();
if (window.sdsctlTheme.current() !== "system") throw new Error("stale error changed theme");
if (attributes.has("href")) throw new Error("stale failed managed href retained");
if (window.sdsctlTheme.select("solarized") !== "solarized") throw new Error("reselect");
if (!attributes.has("href")) throw new Error("reselect did not request stylesheet");
if (picker.value !== "solarized") throw new Error("reselected picker");
listeners.error();
if (window.sdsctlTheme.current() !== "system") throw new Error("load error fallback");
if (document.documentElement.dataset.theme !== "system") throw new Error("error dataset");
if (link.media !== "not all") throw new Error("failed managed link not disabled");
if (attributes.has("href")) throw new Error("failed managed href retained");
if (stored !== "system") throw new Error("load error not persisted");
if (picker.value !== "system") throw new Error("load error picker not repaired");
if (window.sdsctlTheme.select("solarized") !== "solarized") throw new Error("retry");
if (!attributes.has("href")) throw new Error("retry href not restored");
if (link.media !== "all") throw new Error("retry link not enabled");
listeners.error();
if (window.sdsctlTheme.current() !== "system") throw new Error("retry fallback");
if (attributes.has("href")) throw new Error("retry href retained");
"""

    completed = subprocess.run(
        [node, "-e", harness],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr


def test_browser_bootstrap_repairs_stored_managed_theme_with_missing_link(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not available")
    _write_web_theme(tmp_path)
    with TestClient(_app(tmp_path)) as client:
        source = client.get("/assets/theme-bootstrap.js").text
    harness = f"""
"use strict";
const colorScheme = {{ content: "" }};
const themeColor = {{ content: "" }};
let stored = "solarized";
global.document = {{
  documentElement: {{ dataset: {{}} }},
  querySelectorAll: () => [],
  querySelector: (selector) => {{
    if (selector === 'meta[name="color-scheme"]') return colorScheme;
    if (selector === 'meta[name="theme-color"]') return themeColor;
    return null;
  }},
}};
global.window = {{
  localStorage: {{
    getItem: () => stored,
    setItem: (_key, value) => {{ stored = value; }},
  }},
  matchMedia: () => ({{ matches: false, addEventListener: () => {{}} }}),
}};
eval({json.dumps(source)});
if (window.sdsctlTheme.current() !== "system") throw new Error("missing link fallback");
if (document.documentElement.dataset.theme !== "system") throw new Error("dataset");
if (stored !== "system") throw new Error("stored selection not repaired");
"""

    completed = subprocess.run(
        [node, "-e", harness],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr


def test_managed_stylesheet_cannot_escape_shared_viewport_cascade(
    tmp_path: Path,
) -> None:
    css = """
@layer sdsctl-viewport-contract {
  html, body { height: auto !important; overflow: auto !important; }
}
:root[data-theme="solarized"] .workspace-shell {
  display: block !important;
  height: 200vh !important;
  overflow: auto !important;
}
@media (forced-colors: active) {
  :root[data-theme="solarized"] button {
    forced-color-adjust: none !important;
    color: #fff !important;
    background: #fff !important;
    -webkit-text-fill-color: #fff !important;
    transition: color 2s !important;
  }
}
"""
    _write_web_theme(tmp_path, css=css)
    asset = build_web_theme_runtime(tmp_path).require_asset("solarized")
    assert asset.package_sha256 is not None

    with TestClient(_app(tmp_path)) as client:
        shell = client.get("/")
        shared = client.get("/assets/dashboard.css")
        managed = client.get("/assets/themes/solarized/theme.css")
        managed_source = client.get(
            "/assets/themes/solarized/theme.css"
            f"?sdsctl_source={asset.package_sha256}"
        )
        viewport = client.get("/assets/dashboard-viewport.css")

    assert '@import url("dashboard.css?sdsctl_source=1") layer(sdsctl-shared)' in (
        shared.text
    )
    assert css not in managed.text
    assert "layer(sdsctl-managed-theme)" in managed.text
    assert managed_source.text == css
    assert (
        "@layer sdsctl-viewport-contract, sdsctl-shared, "
        "sdsctl-managed-theme;"
    ) in viewport.text
    for protected in (
        "height: 100dvh !important",
        "overflow: hidden !important",
        "grid-template-rows: auto minmax(0, 1fr) auto !important",
        "display: none !important",
        "height: auto !important",
        "overflow: visible !important",
        "forced-color-adjust: auto !important",
        "-webkit-text-fill-color: currentColor !important",
        "color: ButtonText !important",
        "background: ButtonFace !important",
        "color: FieldText !important",
        "background: Field !important",
        "color: HighlightText !important",
        "background: Highlight !important",
        "transition: none !important",
    ):
        assert protected in viewport.text
    assert shell.text.index('data-sdsctl-managed-theme="solarized"') < (
        shell.text.index('href="assets/dashboard-viewport.css"')
    )


def test_layer_source_routes_require_exact_internal_tokens(tmp_path: Path) -> None:
    _write_web_theme(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        invalid_shared = client.get("/assets/dashboard.css?sdsctl_source=invalid")
        built_in_source = client.get(
            "/assets/themes/system/theme.css?sdsctl_source=1"
        )
        invalid_managed = client.get(
            "/assets/themes/solarized/theme.css?sdsctl_source=invalid"
        )

    assert invalid_shared.status_code == 404
    assert invalid_shared.json() == {"detail": "Stylesheet source not found."}
    for response in (built_in_source, invalid_managed):
        assert response.status_code == 404
        assert response.json() == {"detail": "Theme asset not found."}
        assert response.headers["x-content-type-options"] == "nosniff"


def test_dashboard_does_not_live_discover_new_package(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _write_web_theme(tmp_path)

    with TestClient(app) as client:
        shell = client.get("/")
        stylesheet = client.get("/assets/themes/solarized/theme.css")

    assert "solarized" not in shell.text
    assert stylesheet.status_code == 404
    assert stylesheet.json() == {"detail": "Theme not found."}


@pytest.mark.parametrize("target", ["manifest.json", "other.css", "theme.js"])
def test_managed_route_rejects_every_undeclared_asset(
    tmp_path: Path,
    target: str,
) -> None:
    _write_web_theme(tmp_path)
    app = _app(tmp_path)

    with TestClient(app) as client:
        response = client.get(f"/assets/themes/solarized/{target}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Theme asset not found."}
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("filename", ["manifest.json", "theme.css"])
def test_managed_route_fails_closed_after_file_mutation(
    tmp_path: Path,
    filename: str,
) -> None:
    package = _write_web_theme(tmp_path)
    app = _app(tmp_path)
    with (package / filename).open("ab") as stream:
        stream.write(b"\nchanged")

    with TestClient(app) as client:
        response = client.get("/assets/themes/solarized/theme.css")

    assert response.status_code == 404
    assert response.json() == {"detail": "Theme asset not found."}


def test_managed_route_fails_closed_after_package_removal(tmp_path: Path) -> None:
    package = _write_web_theme(tmp_path)
    app = _app(tmp_path)
    for child in package.iterdir():
        child.unlink()
    package.rmdir()

    with TestClient(app) as client:
        response = client.get("/assets/themes/solarized/theme.css")

    assert response.status_code == 404


def test_managed_route_fails_closed_after_same_content_replacement(tmp_path: Path) -> None:
    package = _write_web_theme(tmp_path)
    manifest = (package / "manifest.json").read_bytes()
    stylesheet = (package / "theme.css").read_bytes()
    app = _app(tmp_path)
    saved = tmp_path / "web" / "saved"
    package.rename(saved)
    package.mkdir()
    (package / "manifest.json").write_bytes(manifest)
    (package / "theme.css").write_bytes(stylesheet)

    with TestClient(app) as client:
        response = client.get("/assets/themes/solarized/theme.css")

    assert response.status_code == 404


def test_managed_route_fails_closed_after_symlink_substitution(tmp_path: Path) -> None:
    package = _write_web_theme(tmp_path)
    app = _app(tmp_path)
    stylesheet = package / "theme.css"
    replacement = tmp_path / "replacement.css"
    replacement.write_text("body { display: none; }", encoding="utf-8")
    stylesheet.unlink()
    stylesheet.symlink_to(replacement)

    with TestClient(app) as client:
        response = client.get("/assets/themes/solarized/theme.css")

    assert response.status_code == 404


def test_managed_route_fails_closed_after_directory_symlink_substitution(
    tmp_path: Path,
) -> None:
    package = _write_web_theme(tmp_path)
    app = _app(tmp_path)
    replacement = tmp_path / "replacement"
    package.rename(replacement)
    package.symlink_to(replacement, target_is_directory=True)

    with TestClient(app) as client:
        response = client.get("/assets/themes/solarized/theme.css")

    assert response.status_code == 404


def test_managed_route_fails_closed_after_undeclared_file_appears(tmp_path: Path) -> None:
    package = _write_web_theme(tmp_path)
    app = _app(tmp_path)
    (package / "surprise.css").write_text("body {}", encoding="utf-8")

    with TestClient(app) as client:
        response = client.get("/assets/themes/solarized/theme.css")

    assert response.status_code == 404


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is not available")
def test_managed_route_rejects_special_file_without_blocking(tmp_path: Path) -> None:
    package = _write_web_theme(tmp_path)
    app = _app(tmp_path)
    stylesheet = package / "theme.css"
    stylesheet.unlink()
    os.mkfifo(stylesheet)

    with TestClient(app) as client:
        response = client.get("/assets/themes/solarized/theme.css")

    assert response.status_code == 404
