from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = REPOSITORY_ROOT / "src" / "sds200" / "web_assets"
WORKSPACE_PANES = (
    "scanner",
    "controls",
    "waterfall",
    "recordings",
    "diagnostics",
)


@dataclass(frozen=True)
class _Element:
    tag: str
    attributes: dict[str, str | None]
    ancestors: tuple[str, ...]


class _DashboardParser(HTMLParser):
    _VOID_ELEMENTS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__()
        self.elements: dict[str, _Element] = {}
        self.duplicate_ids: set[str] = set()
        self.stack: list[tuple[str, str | None]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        identifier = attributes.get("id")
        if identifier is not None:
            if identifier in self.elements:
                self.duplicate_ids.add(identifier)
            self.elements[identifier] = _Element(
                tag=tag,
                attributes=attributes,
                ancestors=tuple(
                    ancestor_id for _, ancestor_id in self.stack if ancestor_id is not None
                ),
            )
        if tag not in self._VOID_ELEMENTS:
            self.stack.append((tag, identifier))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


def _asset(name: str) -> str:
    return (ASSET_ROOT / name).read_text(encoding="utf-8")


def _dashboard_parser() -> _DashboardParser:
    parser = _DashboardParser()
    parser.feed(_asset("dashboard.html"))
    parser.close()
    return parser


def _classes(element: _Element) -> set[str]:
    return set((element.attributes.get("class") or "").split())


def _node() -> str:
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node.js is required for browser behavior contract checks")
    return executable


def _run_node(program: str) -> None:
    result = subprocess.run(
        [_node(), "-"],
        input=program,
        text=True,
        capture_output=True,
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_workspace_tab_and_panel_semantics_are_complete_and_unique() -> None:
    parser = _dashboard_parser()

    assert not parser.duplicate_ids
    for index, pane in enumerate(WORKSPACE_PANES):
        tab = parser.elements[f"pane-tab-{pane}"]
        panel = parser.elements[f"pane-{pane}"]

        assert tab.tag == "button"
        assert tab.attributes["role"] == "tab"
        assert tab.attributes["aria-controls"] == f"pane-{pane}"
        assert tab.attributes["data-workspace-tab"] == pane
        assert tab.attributes["aria-selected"] == str(index == 0).lower()
        assert tab.attributes["tabindex"] == ("0" if index == 0 else "-1")

        assert panel.tag == "section"
        assert panel.attributes["role"] == "tabpanel"
        assert panel.attributes["aria-labelledby"] == f"pane-tab-{pane}"
        assert panel.attributes["data-workspace-pane"] == pane
        assert ("hidden" in panel.attributes) is (index != 0)
        assert "workspace-pane" in _classes(panel)


def test_workspace_groups_existing_controls_without_changing_live_ids() -> None:
    parser = _dashboard_parser()
    expected_descendants = {
        "pane-scanner": ("radio-activity-panel", "radio-system", "radio-channel"),
        "pane-controls": (
            "scanner-control-status",
            "scanner-reconnect",
            "scanner-current-system",
            "scanner-hold-system",
            "scanner-next-system",
            "scanner-current-channel",
            "scanner-next",
        ),
        "pane-waterfall": (
            "waterfall-spectrum",
            "waterfall-history",
            "waterfall-history-policy",
            "waterfall-pointer",
            "waterfall-pointer-frequency",
            "waterfall-pause",
            "waterfall-fullscreen",
            "waterfall-gwf-timing",
            "waterfall-scheduler",
            "waterfall-status-refresh",
        ),
        "pane-recordings": (
            "recording-start",
            "recordings-list",
            "saved-recording-player",
        ),
        "pane-diagnostics": (
            "scanner-connected",
            "scanner-endpoint",
            "daemon-state",
            "transition-sequence",
            "audio-playback-controls",
            "audio-play",
            "audio-stop",
            "audio-source",
            "audio-packets",
            "audio-queue-loss",
            "audio-rtp-loss",
        ),
    }

    for pane, descendants in expected_descendants.items():
        for identifier in descendants:
            assert pane in parser.elements[identifier].ancestors

    dashboard = _asset("dashboard.html")
    for class_name in (
        "workspace-shell",
        "workspace-tabs",
        "workspace-deck",
        "dashboard-grid",
        "recordings-layout",
        "diagnostics-layout",
        "radio-view-controls",
        "radio-scan-fallback",
        "radio-field-groups",
        "scanner-display-hierarchy",
    ):
        assert re.search(rf'class="[^"]*\b{re.escape(class_name)}\b', dashboard)


def test_recording_pagination_is_a_labeled_group() -> None:
    pagination = _dashboard_parser().elements["recordings-pagination"]

    assert pagination.tag == "div"
    assert pagination.attributes["role"] == "group"
    assert pagination.attributes["aria-label"] == "Recent recording pages"


def test_dashboard_javascript_is_syntactically_valid() -> None:
    result = subprocess.run(
        [_node(), "--check", str(ASSET_ROOT / "dashboard.js")],
        text=True,
        capture_output=True,
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_connected_clients_render_refresh_privacy_and_failure_states() -> None:
    script = _asset("dashboard.js")
    implementation = "let connectedClientsRefreshInProgress = false;" + script.split(
        "let connectedClientsRefreshInProgress = false;", 1,
    )[1].split('document.addEventListener("visibilitychange"', 1)[0]
    harness = r"""
const assert = require("node:assert/strict");
class Node {
  constructor() { this.children = []; this.textContent = ""; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}
const nodes = new Map([
  ["connected-clients-status", new Node()], ["connected-clients-list", new Node()],
]);
const document = {
  hidden: false,
  getElementById: id => nodes.get(id),
  createElement: () => new Node(),
};
const element = id => nodes.get(id);
const webUrl = path => `https://example.test/ingress/${path}`;
let calls = 0;
let payload = {active: true, clients: [{
  client_id: "<img src=x onerror=alert(1)>", scopes: ["observe"],
  services: {api: 1, events: 1}, connections: 2, connected_seconds: 12,
}]};
let success = true;
async function dashboardFetch(url, options) {
  calls++;
  assert.equal(url, "https://example.test/ingress/api/v1/home-assistant/connected-clients");
  assert.equal(options.cache, "no-store");
  assert.ok(options.signal);
  return {ok: success, json: async () => payload};
}
"""
    assertions = r"""
(async () => {
  await refreshConnectedClients();
  assert.equal(calls, 1);
  let row = element("connected-clients-list").children[0];
  assert.equal(row.children[0].textContent, "<img src=x onerror=alert(1)>");
  assert.equal(row.children[0].children.length, 0); // Never HTML insertion.
  assert.match(row.children[1].textContent, /observe.*2 connection/);
  assert.equal(row.children[2].textContent, "api: 1 · events: 1");
  assert.equal(row.children[3].textContent, "Oldest current connection: 00:00:12");
  assert.equal(row.children[3].title, "Elapsed time (days and HH:MM:SS).");
  payload.clients[0].connected_seconds = 183845;
  await refreshConnectedClients();
  row = element("connected-clients-list").children[0];
  assert.equal(row.children[3].textContent, "Oldest current connection: 2d 03:04:05");
  payload.clients[0].connected_seconds = 3;
  await refreshConnectedClients();
  row = element("connected-clients-list").children[0];
  assert.equal(row.children[3].textContent, "Oldest current connection: 00:00:03");
  payload.clients[0].connected_seconds = null;
  await refreshConnectedClients();
  row = element("connected-clients-list").children[0];
  assert.equal(row.children[3].textContent, "Oldest current connection: Unavailable");
  document.hidden = true;
  await refreshConnectedClients();
  assert.equal(calls, 4);
  document.hidden = false;
  success = false;
  await refreshConnectedClients();
  assert.equal(element("connected-clients-list").children.length, 0);
  assert.match(element("connected-clients-status").textContent, /unavailable/);
  assert.equal(connectedClientsRefreshInProgress, false);
  success = true;
  payload = {active: true, clients: []};
  await refreshConnectedClients();
  assert.equal(element("connected-clients-status").textContent, "No remote clients connected.");
  payload.active = false;
  await refreshConnectedClients();
  assert.match(element("connected-clients-status").textContent, /inactive/);
  nodes.delete("connected-clients-status");
  const before = calls;
  await refreshConnectedClients();
  assert.equal(calls, before); // No HA endpoint queries outside Ingress.
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    _run_node(f"{harness}\n{implementation}\n{assertions}")


def test_connection_age_uses_elapsed_days_and_24_hour_units() -> None:
    script = _asset("dashboard.js")
    implementation = "function formatConnectionAge(value) {" + script.split(
        "function formatConnectionAge(value) {", 1,
    )[1].split("async function refreshConnectedClients()", 1)[0]
    assertions = r"""
const assert = require("node:assert/strict");
for (const [value, expected] of [
  [0, "00:00:00"], [0.9, "00:00:00"], [1, "00:00:01"],
  [59, "00:00:59"], [59.9, "00:00:59"], [60, "00:01:00"],
  [3599, "00:59:59"], [3600, "01:00:00"], [86399, "23:59:59"],
  [86400, "1d 00:00:00"], [183845, "2d 03:04:05"],
  [366 * 86400 + 1, "366d 00:00:01"],
]) {
  assert.equal(formatConnectionAge(value), expected);
}
// Missing/bad telemetry must not look like a newly established connection.
for (const value of [null, undefined, "12", true, {}, [], NaN, Infinity,
                    -Infinity, -1, -0.1, Number.MAX_SAFE_INTEGER + 1]) {
  assert.equal(formatConnectionAge(value), "Unavailable");
}
"""
    _run_node(f"{implementation}\n{assertions}")


def test_workspace_radio_and_recording_browser_behaviors() -> None:
    script = _asset("dashboard.js")
    boundary = 'document.addEventListener("visibilitychange"'
    assert boundary in script
    implementation = script.split(boundary, 1)[0]

    harness = r"""
const assert = require("node:assert/strict");

class FakeNode {
  constructor(id = "", dataset = {}) {
    this.id = id;
    this.dataset = {...dataset};
    this.attributes = {};
    this.children = [];
    this.listeners = {};
    this.hidden = false;
    this.disabled = false;
    this.tabIndex = 0;
    this.value = "";
    this.type = "";
    this.checked = false;
    this.open = false;
    this.options = [];
    this._textContent = "";
    this.textContentWrites = 0;
    this.className = "";
    this.parentNode = null;
    this.styleValues = new Map();
    this.style = {
      setProperty: (name, value) => this.styleValues.set(name, String(value)),
    };
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  addEventListener(type, listener) {
    (this.listeners[type] ||= []).push(listener);
  }

  dispatch(type, values = {}) {
    const event = {
      currentTarget: this,
      target: this,
      defaultPrevented: false,
      preventDefault() {
        this.defaultPrevented = true;
      },
      ...values,
    };
    for (const listener of this.listeners[type] || []) {
      listener(event);
    }
    return event;
  }

  focus() {
    document.activeElement = this;
  }

  click() {
    if (!this.disabled) {
      return this.dispatch("click");
    }
    return null;
  }

  get textContent() {
    return this._textContent;
  }

  set textContent(value) {
    this._textContent = String(value);
    this.textContentWrites += 1;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  append(...children) {
    for (const child of children) {
      if (child instanceof FakeNode) child.parentNode = this;
      this.children.push(child);
    }
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) {
      this.children.splice(index, 1);
      child.parentNode = null;
    }
    return child;
  }

  contains(node) {
    return node === this || this.children.some((child) => child.contains(node));
  }

  get firstChild() {
    return this.children[0] || null;
  }
}

const nodes = new Map();
function add(id, dataset = {}) {
  const node = new FakeNode(id, dataset);
  nodes.set(id, node);
  return node;
}

const paneNames = ["scanner", "controls", "waterfall", "recordings", "diagnostics"];
const workspaceTabs = paneNames.map((pane) => add(`pane-tab-${pane}`, {
  workspaceTab: pane,
}));
const workspacePanels = paneNames.map((pane) => add(`pane-${pane}`, {
  workspacePane: pane,
}));

const storageValues = new Map([["sdsctl.web.pane", "audio"]]);
const workingStorage = {
  getItem(key) {
    return storageValues.has(key) ? storageValues.get(key) : null;
  },
  setItem(key, value) {
    storageValues.set(key, String(value));
  },
};

const document = {
  currentScript: {src: "https://example.test/ingress/assets/dashboard.js"},
  documentElement: new FakeNode("document-root"),
  activeElement: null,
  hidden: false,
  listeners: {},
  getElementById(id) {
    return nodes.get(id) || null;
  },
  querySelectorAll(selector) {
    if (selector === "[data-workspace-tab]") {
      return workspaceTabs;
    }
    if (selector === ".workspace-pane[data-workspace-pane]") {
      return workspacePanels;
    }
    if (selector === "[data-workspace-pane]") {
      return [document.documentElement, ...workspacePanels];
    }
    return [];
  },
  createElement() {
    return new FakeNode();
  },
  createTextNode(value) {
    return {textContent: String(value)};
  },
  addEventListener(type, listener) {
    (this.listeners[type] ||= []).push(listener);
  },
};

const themeSelect = add("theme-select");
themeSelect.options = [
  {value: "system", textContent: "System"},
  {value: "lcars", textContent: "LCARS"},
  {value: "pip-boy-inspired", textContent: "Pip-Boy-inspired"},
];
const themeEffectsSelect = add("theme-effects-select");
const systemPalettePicker = add("system-palette-picker");
const systemPaletteSelect = add("system-palette-select");
const rotationSettings = add("theme-rotation-settings");
const rotationEnabled = add("theme-rotation-enabled");
rotationEnabled.type = "checkbox";
const rotationInterval = add("theme-rotation-interval");
const rotationMode = add("theme-rotation-mode");
const rotationThemes = add("theme-rotation-themes");
const rotationThemeList = add("theme-rotation-theme-list");
const rotationStatus = add("theme-rotation-status");
const waterfallAutoSwitch = add("waterfall-auto-switch");
let selectedTheme = "system";
let selectedSystemPalette = "auto";
const eventSources = [];
let nextTimeoutId = 1;
const timeouts = new Map();

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.closed = false;
    this.closeCount = 0;
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    eventSources.push(this);
  }

  close() {
    this.closed = true;
    this.closeCount += 1;
  }
}

const window = {
  localStorage: workingStorage,
  requestAnimationFrame() {
    return 1;
  },
  setTimeout(callback, delay) {
    const id = nextTimeoutId++;
    timeouts.set(id, {callback, delay});
    return id;
  },
  clearTimeout(id) {
    timeouts.delete(id);
  },
  dispatchEvent(event) {
    this.lastDispatchedEvent = event;
    return true;
  },
  addEventListener() {},
  sdsctlTheme: {
    choices: ["system", "lcars", "pip-boy-inspired"],
    current() {
      return selectedTheme;
    },
    currentSystemPalette() {
      return selectedSystemPalette;
    },
    select(value) {
      selectedTheme = value;
      themeSelect.value = value;
      return value;
    },
    preview(value) {
      selectedTheme = value;
      themeSelect.value = value;
      return value;
    },
    selectSystemPalette(value) {
      selectedSystemPalette = value;
    },
  },
};
globalThis.document = document;
globalThis.window = window;
globalThis.EventSource = FakeEventSource;
globalThis.HTMLInputElement = FakeNode;
globalThis.CustomEvent = class CustomEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.detail = options.detail;
  }
};
"""

    assertions = r"""
initializeWorkspace();
assert.equal(activeWorkspacePane, "diagnostics");
assert.equal(workspaceTabs[4].attributes["aria-selected"], "true");
assert.equal(workspacePanels[4].hidden, false);
assert.equal(workspacePanels[0].hidden, true);
assert.equal(storageValues.get("sdsctl.web.pane"), "diagnostics");
assert.equal(document.documentElement.hidden, false);

initializeThemeEffectsControl();
assert.equal(themeEffectsSelect.value, "full");
assert.equal(document.documentElement.dataset.themeEffects, "full");
assert.equal(document.documentElement.dataset.themeMotion, "enabled");
themeEffectsSelect.value = "subtle";
themeEffectsSelect.dispatch("change");
assert.equal(document.documentElement.dataset.themeEffects, "subtle");
assert.equal(storageValues.get(THEME_EFFECTS_STORAGE_KEY), "subtle");
applyThemeEffectsMode("full");
assert.equal(document.documentElement.dataset.themeEffects, "full");
applyThemeEffectsMode("not-a-mode");
assert.equal(document.documentElement.dataset.themeEffects, "full");

const endEvent = workspaceTabs[4].dispatch("keydown", {key: "End"});
assert.equal(endEvent.defaultPrevented, true);
assert.equal(activeWorkspacePane, "diagnostics");
assert.equal(document.activeElement, workspaceTabs[4]);
assert.equal(storageValues.get("sdsctl.web.pane"), "diagnostics");
assert.equal(document.documentElement.hidden, false);
workspaceTabs[4].dispatch("keydown", {key: "ArrowRight"});
assert.equal(activeWorkspacePane, "scanner");
workspaceTabs[0].dispatch("keydown", {key: "ArrowLeft"});
assert.equal(activeWorkspacePane, "diagnostics");
workspaceTabs[4].dispatch("keydown", {key: "Home"});
assert.equal(activeWorkspacePane, "scanner");
activateWorkspacePane("not-a-pane");
assert.equal(activeWorkspacePane, "scanner");
activateWorkspacePane("home-assistant");
assert.equal(activeWorkspacePane, "scanner");

window.localStorage = {
  getItem() { throw new Error("blocked"); },
  setItem() { throw new Error("blocked"); },
};
assert.equal(readStoredValue("missing"), null);
assert.doesNotThrow(() => writeStoredValue("key", "value"));
assert.doesNotThrow(() => activateWorkspacePane("controls"));
window.localStorage = workingStorage;

storageValues.set(WATERFALL_AUTO_SWITCH_STORAGE_KEY, "enabled");
initializeAutomaticWaterfallSwitch();
assert.equal(waterfallAutoSwitch.checked, true);

storageValues.set("sdsctl.web.scan-fallback", "simple");
add("radio-activity-panel");
add("activity-title");
const fallbackSelect = add("radio-scan-fallback-select");
for (const view of ["auto", "hierarchy", "rf", "identity", "special"]) {
  add(`radio-view-${view}`, {radioView: view});
}
for (const group of ["hierarchy", "rf", "identity", "special"]) {
  add(`radio-group-${group}`, {radioGroup: group});
}

initializeRadioViewControls();
const activityPanel = nodes.get("radio-activity-panel");
assert.equal(fallbackSelect.value, "simple");
assert.equal(activityPanel.dataset.displayLayout, "simple");
assert.equal(activityPanel.dataset.activeRadioGroup, "none");
renderRadioProfile("search");
assert.equal(activityPanel.dataset.displayLayout, "search");
assert.equal(activityPanel.dataset.activeRadioGroup, "rf");
assert.equal(nodes.get("radio-group-rf").hidden, false);

nodes.get("radio-view-identity").dispatch("click");
renderRadioProfile("weather");
assert.equal(activityPanel.dataset.displayLayout, "weather");
assert.equal(activityPanel.dataset.activeRadioGroup, "identity");
assert.equal(nodes.get("radio-group-special").hidden, true);
nodes.get("radio-view-auto").dispatch("click");
assert.equal(activityPanel.dataset.activeRadioGroup, "special");
assert.equal(nodes.get("radio-group-special").hidden, false);

fallbackSelect.value = "detail";
fallbackSelect.dispatch("change");
renderRadioProfile("unknown-value");
assert.equal(activityPanel.dataset.screenKind, "unknown");
assert.equal(activityPanel.dataset.displayLayout, "detail");
assert.equal(activityPanel.dataset.activeRadioGroup, "hierarchy");
assert.equal(storageValues.get("sdsctl.web.scan-fallback"), "detail");
for (const inheritedName of ["constructor", "toString", "__proto__"]) {
  renderRadioProfile(inheritedName);
  assert.equal(activityPanel.dataset.screenKind, "unknown");
  assert.equal(activityPanel.dataset.displayLayout, "detail");
  assert.equal(activityPanel.dataset.activeRadioGroup, "hierarchy");
}

// A stable scanner-reported Waterfall mode opens the existing pane without
// overwriting the operator's persisted workspace choice. Leaving the mode
// returns only an automatically opened pane to Scanner.
activateWorkspacePane("scanner");
assert.equal(storageValues.get(WORKSPACE_PANE_STORAGE_KEY), "scanner");
renderRadioProfile("waterfall");
assert.equal(timeouts.size, 1);
let waterfallTimerId = [...timeouts.keys()][0];
assert.equal(timeouts.get(waterfallTimerId).delay, WATERFALL_AUTO_SWITCH_DELAY_MS);
let waterfallTimer = timeouts.get(waterfallTimerId).callback;
timeouts.delete(waterfallTimerId);
waterfallTimer();
assert.equal(activeWorkspacePane, "waterfall");
assert.equal(waterfallAutoActive, true);
assert.equal(storageValues.get(WORKSPACE_PANE_STORAGE_KEY), "scanner");

renderRadioProfile("scanning");
assert.equal(timeouts.size, 1);
waterfallTimerId = [...timeouts.keys()][0];
assert.equal(timeouts.get(waterfallTimerId).delay, WATERFALL_AUTO_RETURN_DELAY_MS);
renderRadioProfile("scanning");
assert.equal(timeouts.size, 1);
assert.equal(waterfallAutoActive, true);
waterfallTimer = timeouts.get(waterfallTimerId).callback;
timeouts.delete(waterfallTimerId);
waterfallTimer();
assert.equal(activeWorkspacePane, "scanner");
assert.equal(waterfallAutoActive, false);

// A manual view change wins until the scanner leaves Waterfall mode, avoiding
// a view that fights the operator while the mode remains active.
renderRadioProfile("waterfall");
waterfallTimerId = [...timeouts.keys()][0];
waterfallTimer = timeouts.get(waterfallTimerId).callback;
timeouts.delete(waterfallTimerId);
waterfallTimer();
activateWorkspacePane("scanner");
assert.equal(waterfallAutoSuppressed, true);
renderRadioProfile("waterfall");
assert.equal(timeouts.size, 0);
renderRadioProfile("scanning");
assert.equal(waterfallAutoSuppressed, false);
renderRadioProfile("waterfall");
assert.equal(timeouts.size, 1);
clearAutomaticWaterfallTimer("switch");

waterfallAutoSwitch.checked = false;
waterfallAutoSwitch.dispatch("change");
assert.equal(
  storageValues.get(WATERFALL_AUTO_SWITCH_STORAGE_KEY),
  "disabled",
);
renderRadioProfile("waterfall");
assert.equal(timeouts.size, 0);

assert.equal(toneOutDisplayValue(0), "Detect");
assert.equal(toneOutDisplayValue(-0), "Detect");
assert.equal(toneOutDisplayValue("0"), "Detect");
assert.equal(toneOutDisplayValue("0.000 Hz"), "Detect");
assert.equal(toneOutDisplayValue("67.0"), "67.0");
assert.equal(toneOutDisplayValue(67), 67);

waterfallLastSequence = null;
assert.equal(waterfallRecord({
  protocol: "sdsctl.waterfall",
  version: 1,
  sequence: 7,
  observed_at: "2026-08-28T00:00:00Z",
  kind: "session.checkpoint",
  payload: {},
}).sequence, 7);
assert.equal(
  waterfallPayloadLine('{"sequence":7}', WATERFALL_NDJSON_MEDIA_TYPE),
  '{"sequence":7}',
);
assert.equal(
  waterfallPayloadLine('data: {"sequence":7}', WATERFALL_SSE_MEDIA_TYPE),
  '{"sequence":7}',
);
assert.equal(waterfallPayloadLine('id: 7', WATERFALL_SSE_MEDIA_TYPE), null);
assert.equal(waterfallPayloadLine('', WATERFALL_SSE_MEDIA_TYPE), null);
assert.throws(
  () => waterfallPayloadLine('retry: 2000', WATERFALL_SSE_MEDIA_TYPE),
  /event field is unsupported/,
);
assert.throws(
  () => waterfallRecord({
    protocol: "sdsctl.waterfall",
    version: 1,
    sequence: 9,
    observed_at: "2026-08-28T00:00:01Z",
    kind: "waterfall.gwf",
    payload: {},
  }),
  /sequence is not contiguous/,
);
const exactWaterfallValues = Array.from({length: 240}, (_, index) => String(index));
const normalizedWaterfall = normalizeWaterfallValues(exactWaterfallValues);
assert.deepEqual(normalizedWaterfall.raw, exactWaterfallValues);
assert.equal(normalizedWaterfall.normalized.length, 240);
assert.equal(normalizedWaterfall.normalized[0], 0);
assert.equal(normalizedWaterfall.normalized[239], 1);
const physicalHexWaterfall = normalizeWaterfallValues([
  "6c", "58", "3a", ...Array.from({length: 237}, () => "3a"),
]);
assert.equal(physicalHexWaterfall.raw[0], "6c");
assert.equal(physicalHexWaterfall.normalized[0], 1);
assert.equal(physicalHexWaterfall.normalized[2], 0);
assert.throws(
  () => normalizeWaterfallValues(exactWaterfallValues.slice(1)),
  /exactly 240 values/,
);
assert.throws(
  () => normalizeWaterfallValues([...exactWaterfallValues.slice(0, 239), "NaN"]),
  /non-hexadecimal value/,
);
assert.equal(validFrequencyMetadata({
  lower_frequency: "1540000",
  center_frequency: "1550000",
  upper_frequency: "1560000",
  marker_frequency: "1555500",
  marker_position: "120",
}).markerPosition, 120);
assert.equal(validFrequencyMetadata({
  lower_frequency: "1540000",
  center_frequency: "not-a-frequency",
  upper_frequency: "1560000",
  marker_frequency: "1555500",
  marker_position: "120",
}), null);
const frameHistoryPolicy = waterfallHistoryPolicy("frames", "120");
const durationHistoryPolicy = waterfallHistoryPolicy("duration", "30");
assert.deepEqual(frameHistoryPolicy, {mode: "frames", frames: 120, seconds: null});
assert.deepEqual(durationHistoryPolicy, {mode: "duration", frames: 240, seconds: 30});
const timedHistory = [
  {values: [0.1], receivedAt: 1000},
  {values: [0.2], receivedAt: 15000},
  {values: [0.3], receivedAt: 31000},
];
assert.deepEqual(
  pruneWaterfallHistory(timedHistory, durationHistoryPolicy, 40000),
  timedHistory.slice(1),
);
const durationRows = waterfallHistoryRows(
  timedHistory,
  durationHistoryPolicy,
  300,
  40000,
);
assert.equal(durationRows.length, 2);
assert.ok(durationRows[0].y < durationRows[1].y);
assert.ok(durationRows.every((row) => row.height >= 1));
assert.equal(waterfallPointerFrequency({
  lower_frequency: "945000",
  center_frequency: "949000",
  upper_frequency: "952000",
  marker_frequency: "949000",
  marker_position: "120",
}, 0.5).label, "94.8500 MHz");
assert.equal(waterfallPointerFrequency({}, 0.5), null);
assert.throws(
  () => waterfallHistoryPolicy("duration", 3600),
  /duration history is invalid/,
);

for (const identifier of [
  "waterfall-session-state",
  "waterfall-poll-failures",
  "waterfall-gwf-timing",
  "waterfall-scheduler",
  "waterfall-status-refresh",
  "waterfall-frequency-lower",
  "waterfall-frequency-center",
  "waterfall-frequency-upper",
  "waterfall-frequency-marker",
]) {
  add(identifier);
}
applyWaterfallSnapshot({
  state: "running",
  gwf_poll_failures: 1,
  average_gwf_round_trip_seconds: 0.042,
  maximum_gwf_round_trip_seconds: 0.075,
  last_gwf_scheduler_lag_seconds: 0.012,
  gwf_skipped_poll_deadlines: 2,
  waterfall_status_revision: 3,
  gst_poll_failures: 1,
  waterfall_status: {
    lower_frequency: "9450000",
    center_frequency: "9490000",
    upper_frequency: "9520000",
    marker_frequency: "9490000",
    marker_position: "120",
  },
});
assert.equal(nodes.get("waterfall-gwf-timing").textContent, "42 / 75 ms avg/max");
assert.equal(nodes.get("waterfall-scheduler").textContent, "12 ms lag · 2 skipped");
assert.equal(nodes.get("waterfall-status-refresh").textContent, "revision 3 · 1 failures");
assert.equal(nodes.get("waterfall-frequency-lower").textContent, "9450000");
assert.equal(nodes.get("waterfall-frequency-center").textContent, "9490000");
assert.equal(nodes.get("waterfall-frequency-upper").textContent, "9520000");
assert.equal(nodes.get("waterfall-frequency-marker").textContent, "9490000");

for (const target of Object.values(RADIO_STATE_FIELD_TARGETS)) {
  if (!nodes.has(target)) {
    add(target);
  }
}
renderRadioState({
  screen_kind: "tone_out",
  signal: 5,
  rssi: -60,
  tone_out_tone_a: "0.0",
  tone_out_tone_b: "67.0",
});
assert.equal(nodes.get("radio-tone-out-tone-a").textContent, "Detect");
assert.equal(nodes.get("radio-tone-out-tone-b").textContent, "67.0");
assert.equal(document.documentElement.dataset.themeSignal, "strong");
assert.equal(document.documentElement.dataset.themeRssi, "good");
assert.equal(
  document.documentElement.styleValues.get("--theme-signal-normalized"),
  "1.0000",
);
assert.equal(window.lastDispatchedEvent.type, "sdsctl:theme-telemetry");
assert.equal(window.lastDispatchedEvent.detail.version, 1);
assert.equal(window.lastDispatchedEvent.detail.signal, 1);
const liveTelemetryEvent = window.lastDispatchedEvent;
document.hidden = true;
renderRadioState({screen_kind: "scanning", signal: 0, rssi: -120});
assert.equal(document.documentElement.dataset.themeTelemetry, "paused");
assert.equal(window.lastDispatchedEvent, liveTelemetryEvent);
assert.notEqual(pendingThemeTelemetry, null);
document.hidden = false;
applyThemeTelemetry(pendingThemeTelemetry);
assert.equal(document.documentElement.dataset.themeTelemetry, "live");
assert.notEqual(window.lastDispatchedEvent, liveTelemetryEvent);

activateWorkspacePane("audio");
nodes.get("radio-view-special").focus();
audioPlaybackActive = true;
const preservedFocus = document.activeElement;
const preservedFallback = fallbackSelect.value;
initializeThemeControl();
assert.equal(systemPalettePicker.hidden, false);
assert.equal(rotationThemeList.children.length, 3);
assert.equal(window.sdsctlThemeRotation.policy().enabled, false);
let rotationPolicy = window.sdsctlThemeRotation.setPolicy({
  enabled: true,
  intervalMinutes: 5,
  mode: "sequential",
  themes: ["system", "lcars", "pip-boy-inspired"],
});
assert.equal(rotationPolicy.enabled, true);
assert.equal(timeouts.size, 1);
assert.equal([...timeouts.values()][0].delay, 300000);
assert.equal(window.sdsctlThemeRotation.advance(), "lcars");
assert.equal(selectedTheme, "lcars");
assert.equal(timeouts.size, 1);
systemPaletteSelect.value = "nord";
systemPaletteSelect.dispatch("change");
assert.equal(selectedSystemPalette, "nord");
themeSelect.value = "pip-boy-inspired";
themeSelect.dispatch("change");
assert.equal(selectedTheme, "pip-boy-inspired");
assert.equal(window.sdsctlThemeRotation.manualTheme(), "pip-boy-inspired");
assert.equal(window.sdsctlThemeRotation.advance(), "system");
rotationPolicy = window.sdsctlThemeRotation.setPolicy({
  enabled: false,
  intervalMinutes: 5,
  mode: "sequential",
  themes: ["system", "lcars", "pip-boy-inspired"],
});
assert.equal(rotationPolicy.enabled, false);
assert.equal(selectedTheme, "pip-boy-inspired");
assert.equal(timeouts.size, 0);
rotationPolicy = window.sdsctlThemeRotation.setPolicy({
  enabled: true,
  intervalMinutes: 1,
  mode: "sequential",
  themes: ["system"],
});
assert.equal(rotationPolicy.enabled, false);
assert.equal(rotationPolicy.intervalMinutes, 30);
assert.equal(systemPalettePicker.hidden, true);
assert.equal(activeWorkspacePane, "diagnostics");
assert.equal(radioInspectionView, "auto");
assert.equal(fallbackSelect.value, preservedFallback);
assert.equal(audioPlaybackActive, true);
assert.equal(document.activeElement, preservedFocus);

const dashboardMessage = add("dashboard-message");
const statusBadge = add("status-badge");
setOverallStatus(
  "online",
  "Connected",
  "Daemon and scanner status are available.",
);
assert.equal(statusBadge.dataset.state, "online");
assert.equal(dashboardMessage.dataset.state, "online");

setOverallStatus(
  "offline",
  "Disconnected",
  "The daemon is available, but the scanner is disconnected.",
);
assert.equal(statusBadge.dataset.state, "offline");
assert.equal(dashboardMessage.dataset.state, "offline");

authenticationRequired = true;
setOverallStatus("online", "Connected", "This must be replaced.");
assert.equal(statusBadge.dataset.state, "offline");
assert.equal(statusBadge.textContent, "Login required");
assert.equal(dashboardMessage.dataset.state, "offline");
assert.equal(dashboardMessage.textContent, "Sign in again. Displayed data is stale.");
authenticationRequired = false;

startEventStream();
assert.equal(eventSources.length, 1);
const firstEventSource = eventSources[0];
assert.equal(
  firstEventSource.url,
  "https://example.test/ingress/api/v1/events",
);
assert.equal(eventSource, firstEventSource);
assert.equal(eventStreamConnected, false);
assert.equal(eventSources.filter((source) => !source.closed).length, 1);

firstEventSource.onopen();
assert.equal(eventStreamConnected, true);
lastEventSequence = 417;
firstEventSource.onerror();
assert.equal(firstEventSource.closed, true);
assert.equal(firstEventSource.closeCount, 1);
assert.equal(dashboardMessage.dataset.state, "attention");
assert.equal(
  dashboardMessage.textContent,
  "Live events are reconnecting; status polling remains active.",
);
assert.equal(eventSource, null);
assert.equal(eventStreamConnected, false);
assert.equal(lastEventSequence, null);
assert.equal(timeouts.size, 1);
const firstRestartId = [...timeouts.keys()][0];
assert.equal(timeouts.get(firstRestartId).delay, FALLBACK_REFRESH_INTERVAL_MS);
firstEventSource.onerror();
firstEventSource.onmessage({data: "not-json"});
assert.equal(timeouts.size, 1);
assert.equal(firstEventSource.closeCount, 1);

const firstRestart = timeouts.get(firstRestartId).callback;
timeouts.delete(firstRestartId);
firstRestart();
assert.equal(eventSources.length, 2);
const secondEventSource = eventSources[1];
assert.equal(eventSource, secondEventSource);
assert.equal(eventSources.filter((source) => !source.closed).length, 1);
firstEventSource.onopen();
assert.equal(eventStreamConnected, false);

secondEventSource.onerror();
assert.equal(secondEventSource.closed, true);
assert.equal(timeouts.size, 1);
const secondRestartId = [...timeouts.keys()][0];
const secondRestart = timeouts.get(secondRestartId).callback;
timeouts.delete(secondRestartId);
secondRestart();
assert.equal(eventSources.length, 3);
const thirdEventSource = eventSources[2];
thirdEventSource.onopen();
assert.equal(eventStreamConnected, true);
assert.equal(eventSources.filter((source) => !source.closed).length, 1);

const originalRefreshStatus = refreshStatus;
refreshStatus = async () => {};
thirdEventSource.onmessage({data: "not-json"});
assert.equal(thirdEventSource.closed, true);
assert.equal(eventSource, null);
assert.equal(eventStreamConnected, false);
assert.equal(timeouts.size, 1);
refreshStatus = originalRefreshStatus;

startEventStream();
assert.equal(timeouts.size, 0);
assert.equal(eventSources.length, 4);
const fourthEventSource = eventSources[3];
assert.equal(eventSource, fourthEventSource);
assert.equal(eventSources.filter((source) => !source.closed).length, 1);
fourthEventSource.onerror();
assert.equal(timeouts.size, 1);
stopEventStream();
assert.equal(timeouts.size, 0);
assert.equal(eventSources.filter((source) => !source.closed).length, 0);
document.hidden = true;
startEventStream();
assert.equal(eventSources.length, 4);
document.hidden = false;
startEventStream();
assert.equal(eventSources.length, 5);
assert.equal(eventSources.filter((source) => !source.closed).length, 1);
stopEventStream();
assert.equal(eventSources.filter((source) => !source.closed).length, 0);

add("recordings-list");
add("recordings-message");
add("recordings-page-status");
add("recordings-previous-page");
add("recordings-next-page");
add("recordings-refresh");
add("recording-start");
add("recording-stop");
initializeRecordingPaginationControls();
const entries = Array.from({length: 7}, (_, index) => ({
  audio: `2026/example-${index}.wav`,
  recorded_at: "2026-08-26T00:00:00Z",
  duration_seconds: index,
  audio_size_bytes: index * 100,
  playable: true,
}));

assert.equal(recordingsPageCount(entries), 3);
assert.equal(recordingPageEntries(entries, 0).length, 3);
assert.equal(recordingPageEntries(entries, 1)[0].audio, "2026/example-3.wav");
assert.equal(normalizedRecordingPageIndex(20, entries), 2);
renderRecordings({entries, total_entries: 7});
assert.equal(nodes.get("recordings-list").children.length, 3);
assert.equal(nodes.get("recordings-page-status").textContent, "Page 1 of 3");
assert.equal(nodes.get("recordings-previous-page").disabled, true);
assert.equal(nodes.get("recordings-next-page").disabled, false);
const pageStatusWrites = nodes.get("recordings-page-status").textContentWrites;
setRecordingControls(currentRecording);
assert.equal(
  nodes.get("recordings-page-status").textContentWrites,
  pageStatusWrites,
);

const previousPage = nodes.get("recordings-previous-page");
const nextPage = nodes.get("recordings-next-page");
const recordingsRefresh = nodes.get("recordings-refresh");
nextPage.focus();
nextPage.click();
assert.equal(recordingPageIndex, 1);
assert.equal(document.activeElement, nextPage);
nextPage.click();
assert.equal(recordingPageIndex, 2);
assert.equal(nodes.get("recordings-list").children.length, 1);
assert.equal(nodes.get("recordings-page-status").textContent, "Page 3 of 3");
assert.equal(nextPage.disabled, true);
assert.equal(document.activeElement, previousPage);
previousPage.click();
assert.equal(recordingPageIndex, 1);
assert.equal(document.activeElement, previousPage);
previousPage.click();
assert.equal(recordingPageIndex, 0);
assert.equal(previousPage.disabled, true);
assert.equal(document.activeElement, nextPage);

nextPage.click();
assert.equal(recordingPageIndex, 1);
renderRecordings({entries, total_entries: 7});
assert.equal(recordingPageIndex, 1);
previousPage.focus();
renderRecordings({entries: entries.slice(0, 4), total_entries: 4});
assert.equal(recordingPageIndex, 0);
assert.equal(nodes.get("recordings-list").children.length, 3);
assert.equal(previousPage.disabled, true);
assert.equal(document.activeElement, nextPage);
nextPage.click();
assert.equal(recordingPageIndex, 1);
assert.equal(document.activeElement, previousPage);
renderRecordings({entries: [], total_entries: 0});
assert.equal(recordingPageIndex, 0);
assert.equal(nodes.get("recordings-list").children.length, 1);
assert.equal(nodes.get("recordings-next-page").disabled, true);
assert.equal(document.activeElement, recordingsRefresh);
assert.equal(
  recordingFileUrl("2026/example name.wav"),
  "https://example.test/ingress/api/v1/recordings/file/2026/example%20name.wav",
);
"""

    _run_node(f"{harness}\n{implementation}\n{assertions}")
