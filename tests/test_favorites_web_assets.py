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
        "favorites-profiles.js",
        "favorites-workspace.css",
        "favorites-workspace.html",
    )
    assert all(read_favorites_web_asset(name) for name in FAVORITES_WEB_ASSET_NAMES)
    for unavailable in ("../dashboard.html", "/tmp/favorites", "dashboard.html", ""):
        with pytest.raises(ValueError, match="unavailable"):
            read_favorites_web_asset(unavailable)
    with pytest.raises(ValueError, match="unavailable"):
        read_favorites_web_asset(1)  # type: ignore[arg-type]


def test_shell_applies_appearance_before_styles_and_exposes_no_write_authority() -> None:
    source = _text("favorites-workspace.html")

    assert '<meta name="color-scheme" content="light dark">' in source
    assert source.index('src="assets/favorites-appearance.js"') < source.index(
        'href="assets/favorites-workspace.css"'
    )
    assert '<option value="auto">Auto</option>' in source
    assert '<option value="light">Light</option>' in source
    assert '<option value="dark">Dark</option>' in source
    assert 'body data-workspace-state="profiles-loading"' in source
    assert 'src="assets/favorites-profiles.js" defer' in source
    assert 'src="/assets/' not in source
    assert 'href="/assets/' not in source
    assert "editor workspaces remain disconnected" in source
    assert "cannot expose Favorites bytes, contact a scanner, execute a write" in source
    assert "Execution is deliberately absent" in source
    assert "filesystem path, Favorites bytes, credential, or confirmation token" in source
    assert "disabled>Review exact plan</button>" in source
    assert "disabled>Discard workspace</button>" in source
    assert 'aria-labelledby="favorites-selected-name"' in source
    assert 'aria-labelledby="editor-heading"' not in source
    assert "<form" not in source
    assert "password" not in source.casefold()
    assert "confirmation_token" not in source


def test_profile_runtime_is_same_origin_read_only_and_uses_safe_dom_updates() -> None:
    source = _text("favorites-profiles.js")

    assert 'ENDPOINT = "api/v1/favorites/profiles"' in source
    assert 'credentials: "same-origin"' in source
    assert 'cache: "no-store"' in source
    assert "fetch(ENDPOINT" in source
    assert "textContent" in source
    assert "innerHTML" not in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "WebSocket" not in source
    assert "EventSource" not in source
    for mutation in ("POST", "PUT", "PATCH", "DELETE"):
        assert mutation not in source


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_profile_runtime_loads_and_selects_validated_summaries() -> None:
    source = _text("favorites-profiles.js")
    harness = f"""
const source = {source!r};
class FakeElement {{
  constructor() {{
    this.children = [];
    this.dataset = {{}};
    this.disabled = false;
    this.listeners = {{}};
    this.textContent = "";
    this.value = "";
  }}
  addEventListener(name, callback) {{ this.listeners[name] = callback; }}
  append(child) {{ this.children.push(child); }}
  replaceChildren() {{ this.children = []; }}
}}
class FakeSelect extends FakeElement {{}}
global.HTMLSelectElement = FakeSelect;
const picker = new FakeSelect();
const nodes = {{
  "favorites-profile-select": picker,
  "favorites-profile-count": new FakeElement(),
  "favorites-source-state": new FakeElement(),
  "favorites-selection-state": new FakeElement(),
  "favorites-selection-message": new FakeElement(),
  "favorites-selected-name": new FakeElement(),
  "favorites-selected-revision": new FakeElement(),
  "favorites-metric-compatibility": new FakeElement(),
  "favorites-metric-documents": new FakeElement(),
  "favorites-metric-revision": new FakeElement(),
  "favorites-metric-storage": new FakeElement(),
}};
global.document = {{
  documentElement: {{dataset: {{}}}},
  querySelector: (selector) => nodes[selector.slice(1)] ?? null,
  createElement: () => new FakeElement(),
}};
const profile = (id, name, compatibility, revision) => ({{
  profile_id: id,
  name,
  compatibility,
  revision,
  revision_count: revision,
  document_count: revision + 1,
  snapshot_bytes: revision * 2048,
  snapshot_sha256: "a".repeat(64),
  created_at_ns: 1795806760892377000,
  updated_at_ns: 1795806760892378000,
}});
let request = null;
global.fetch = async (url, options) => {{
  request = {{url, options}};
  return {{
    ok: true,
    json: async () => ({{
      protocol: "sdsctl.favorites-profiles",
      version: 1,
      profiles: [
        profile("profile_identifier_000001", "Portable", "sds100", 1),
        profile("profile_identifier_000002", "Base", "sds200", 2),
      ],
    }}),
  }};
}};
eval(source);
setImmediate(() => {{
  if (request.url !== "api/v1/favorites/profiles") throw new Error("endpoint");
  if (request.options.credentials !== "same-origin") throw new Error("credentials");
  if (request.options.cache !== "no-store") throw new Error("cache");
  if (picker.disabled || picker.children.length !== 2) throw new Error("picker");
  if (nodes["favorites-profile-count"].textContent !== "2") throw new Error("count");
  if (Number.isSafeInteger(1795806760892377000)) throw new Error("timestamp fixture");
  if (nodes["favorites-selected-name"].textContent !== "Portable") throw new Error("first");
  if (nodes["favorites-metric-compatibility"].textContent !== "SDS100") {{
    throw new Error("compatibility");
  }}
  picker.value = "1";
  picker.listeners.change();
  if (nodes["favorites-selected-name"].textContent !== "Base") throw new Error("second");
  if (nodes["favorites-metric-revision"].textContent !== "2 of 2") throw new Error("revision");
  if (document.documentElement.dataset.workspaceState !== "profile-summary") {{
    throw new Error("state");
  }}
}});
"""
    subprocess.run(
        ["node", "--input-type=commonjs", "--eval", harness],
        check=True,
        capture_output=True,
        text=True,
    )


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
