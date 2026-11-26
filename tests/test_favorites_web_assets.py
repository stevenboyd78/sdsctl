from __future__ import annotations

import shutil
import subprocess

import pytest

from sds200.favorites_web_assets import (
    FAVORITES_WEB_ASSET_NAMES,
    read_favorites_web_asset,
)


def _text(name: str) -> str:
    return read_favorites_web_asset(name).decode("utf-8")


def test_asset_loader_is_exact_and_path_free() -> None:
    assert FAVORITES_WEB_ASSET_NAMES == (
        "favorites-appearance.js",
        "favorites-workspace.css",
        "favorites-workspace.html",
    )
    assert all(read_favorites_web_asset(name) for name in FAVORITES_WEB_ASSET_NAMES)
    for unavailable in ("../dashboard.html", "/tmp/favorites", "dashboard.html", ""):
        with pytest.raises(ValueError, match="unavailable"):
            read_favorites_web_asset(unavailable)
    with pytest.raises(ValueError, match="unavailable"):
        read_favorites_web_asset(1)  # type: ignore[arg-type]


def test_shell_applies_appearance_before_styles_and_exposes_no_authority() -> None:
    source = _text("favorites-workspace.html")

    assert '<meta name="color-scheme" content="light dark">' in source
    assert source.index('src="/assets/favorites-appearance.js"') < source.index(
        'href="/assets/favorites-workspace.css"'
    )
    assert '<option value="auto">Auto</option>' in source
    assert '<option value="light">Light</option>' in source
    assert '<option value="dark">Dark</option>' in source
    assert 'body data-workspace-state="unwired"' in source
    assert "no workspace route is connected" in source
    assert "cannot contact a scanner, execute a write, or retain a draft" in source
    assert "Execution is deliberately absent" in source
    assert "filesystem path, credential, or confirmation token" in source
    assert "disabled>Open workspace</button>" in source
    assert "disabled>Review exact plan</button>" in source
    assert "disabled>Discard workspace</button>" in source
    assert "<form" not in source
    assert "password" not in source.casefold()
    assert "confirmation_token" not in source


def test_styles_use_semantic_light_dark_and_accessibility_contracts() -> None:
    source = _text("favorites-workspace.css")

    assert ':root[data-favorites-palette="dark"]' in source
    for token in (
        "--background:",
        "--surface:",
        "--text:",
        "--muted:",
        "--border:",
        "--accent:",
        "--focus:",
        "--success:",
        "--warning:",
        "--danger:",
    ):
        assert source.count(token) >= 2
    assert ":focus-visible" in source
    assert "@media (prefers-reduced-motion: reduce)" in source
    assert "@media (forced-colors: active)" in source
    assert "@media (max-width: 48rem)" in source
    assert '.state-badge[data-state="unavailable"]' in source
    assert '.state-badge[data-state="clean"]' in source


def test_appearance_runtime_is_bounded_and_does_not_fetch() -> None:
    source = _text("favorites-appearance.js")

    assert 'STORAGE_KEY = "sdsctl.favorites.appearance"' in source
    assert 'Object.freeze(["auto", "light", "dark"])' in source
    assert 'matchMedia("(prefers-color-scheme: dark)")' in source
    assert 'colorQuery.addEventListener("change"' in source
    assert "window.sdsctlFavoritesAppearance = Object.freeze" in source
    assert "fetch(" not in source
    assert "XMLHttpRequest" not in source
    assert "WebSocket" not in source
    assert "EventSource" not in source
    assert "sessionStorage" not in source
    assert source.count("localStorage") == 2


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_appearance_runtime_tracks_auto_and_explicit_modes() -> None:
    source = _text("favorites-appearance.js")
    harness = f"""
const source = {source!r};
let stored = null;
let dark = false;
let mediaListener = null;
const pickerListeners = {{}};
class FakeSelect {{}}
global.HTMLSelectElement = FakeSelect;
const picker = new FakeSelect();
picker.value = "";
picker.addEventListener = (name, callback) => {{ pickerListeners[name] = callback; }};
const themeColor = {{
  content: "",
  setAttribute: (name, value) => {{ if (name === "content") themeColor.content = value; }},
}};
global.document = {{
  documentElement: {{dataset: {{}}, style: {{}}}},
  readyState: "complete",
  querySelector: (selector) => selector === "#favorites-appearance-select" ? picker : themeColor,
  addEventListener: () => {{}},
}};
global.window = {{
  localStorage: {{
    getItem: (key) => key === "sdsctl.favorites.appearance" ? stored : null,
    setItem: (key, value) => {{
      if (key !== "sdsctl.favorites.appearance") throw new Error("foreign storage key");
      stored = value;
    }},
  }},
  matchMedia: () => ({{
    get matches() {{ return dark; }},
    addEventListener: (name, callback) => {{
      if (name === "change") mediaListener = callback;
    }},
  }}),
}};
eval(source);
const api = window.sdsctlFavoritesAppearance;
if (api.current() !== "auto" || api.effective() !== "light") throw new Error("auto light");
if (document.documentElement.dataset.favoritesPalette !== "light") throw new Error("light data");
dark = true;
mediaListener();
if (api.effective() !== "dark") throw new Error("auto dark");
if (themeColor.content !== "#10151d") throw new Error("dark theme color");
if (api.select("light") !== "light" || stored !== "light") throw new Error("light select");
dark = true;
mediaListener();
if (api.effective() !== "light") throw new Error("explicit light changed");
picker.value = "dark";
pickerListeners.change();
if (api.current() !== "dark" || stored !== "dark") throw new Error("picker dark");
if (api.select("invalid") !== "auto" || stored !== "auto") throw new Error("normalize");
"""
    subprocess.run(
        ["node", "--input-type=commonjs", "--eval", harness],
        check=True,
        capture_output=True,
        text=True,
    )
