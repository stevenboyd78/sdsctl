from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sds200.exceptions import SDS200Error
from sds200.home_assistant_lovelace import (
    HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_FILENAME,
    HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_RESOURCE_URL,
    install_home_assistant_lovelace_waterfall_card,
)
from sds200.home_assistant_themes import (
    built_in_home_assistant_theme_registry,
    read_built_in_home_assistant_theme_module,
)


def waterfall_target(tmp_path: Path) -> Path:
    return (
        tmp_path
        / "homeassistant"
        / "www"
        / "sds200"
        / HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_FILENAME
    )


def waterfall_card_text() -> str:
    theme = built_in_home_assistant_theme_registry().require("waterfall")
    return read_built_in_home_assistant_theme_module(theme).decode("utf-8")


def run_waterfall_card_javascript(body: str) -> object:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for waterfall-card runtime validation.")

    harness = f"""
global.HTMLElement = class {{}};
global.CustomEvent = class {{}};
global.customElements = {{get: () => null, define: () => undefined}};
global.location = {{origin: "http://homeassistant.test", protocol: "http:"}};
global.document = {{cookie: ""}};
global.window = {{
  customCards: [],
  setInterval: () => 1,
  clearInterval: () => undefined,
  setTimeout,
  clearTimeout,
  requestAnimationFrame: (callback) => callback(),
  devicePixelRatio: 1,
}};
{waterfall_card_text()}
{body}
"""
    completed = subprocess.run(
        [node, "-e", harness],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_waterfall_card_resource_url_uses_home_assistant_local_path() -> None:
    assert HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_RESOURCE_URL == (
        "/local/sds200/sds200-waterfall-card.js"
    )


def test_waterfall_card_packaged_asset_is_importable() -> None:
    text = waterfall_card_text()

    assert 'const SDS200_WATERFALL_CARD_TYPE = "sds200-waterfall-card";' in text
    assert 'const SDS200_WATERFALL_CARD_TAG = "sds200-waterfall-card";' in text
    assert "window.customCards" in text
    assert "customElements.define" in text
    assert 'name: "SDS200 Waterfall"' in text


def test_waterfall_card_configuration_is_bounded_and_transport_free() -> None:
    result = run_waterfall_card_javascript(
        """
const accepted = requireWaterfallCardConfig({
  title: "  Dispatch  ",
  density: "tall",
  palette: "green",
  history: 240,
  show_scale: false,
  show_telemetry: true,
  start_paused: true,
});
const rejected = [];
for (const config of [
  {endpoint: "http://scanner.test"},
  {density: "fullscreen"},
  {palette: "custom"},
  {history: 1000000},
  {show_scale: "yes"},
]) {
  try {
    requireWaterfallCardConfig(config);
  } catch (error) {
    rejected.push(error.message);
  }
}
process.stdout.write(JSON.stringify({accepted, rejected}));
"""
    )

    assert result["accepted"] == {
        "title": "Dispatch",
        "density": "tall",
        "palette": "green",
        "history": 240,
        "show_scale": False,
        "show_telemetry": True,
        "start_paused": True,
    }
    assert len(result["rejected"]) == 5
    assert "not supported" in result["rejected"][0]


def test_waterfall_card_normalizes_exact_hexadecimal_frames() -> None:
    result = run_waterfall_card_javascript(
        """
const source = Array.from({length: 240}, (_, index) =>
  index.toString(16).padStart(2, "0")
);
const frame = normalizeWaterfallFrame(source);
let invalid = null;
try {
  normalizeWaterfallFrame([...source.slice(0, 239), "not-hex"]);
} catch (error) {
  invalid = error.message;
}
process.stdout.write(JSON.stringify({
  length: frame.length,
  first: frame[0],
  last: frame[239],
  invalid,
}));
"""
    )

    assert result == {
        "length": 240,
        "first": 0,
        "last": 1,
        "invalid": "Waterfall frame contains an invalid hexadecimal value.",
    }


def test_waterfall_card_parses_ingress_safe_server_sent_events() -> None:
    result = run_waterfall_card_javascript(
        """
const parser = Sds200WaterfallCard.prototype._payloadLine;
const target = {};
const accepted = [
  parser.call(target, "id: 42", SDS200_WATERFALL_SSE_MEDIA_TYPE),
  parser.call(target, ": keepalive", SDS200_WATERFALL_SSE_MEDIA_TYPE),
  parser.call(target, "", SDS200_WATERFALL_SSE_MEDIA_TYPE),
  parser.call(target, "data: {\\\"sequence\\\":42}", SDS200_WATERFALL_SSE_MEDIA_TYPE),
  parser.call(target, "{\\\"sequence\\\":42}", SDS200_WATERFALL_NDJSON_MEDIA_TYPE),
];
const rejected = [];
for (const [line, mediaType] of [
  ["event: waterfall", SDS200_WATERFALL_SSE_MEDIA_TYPE],
  ["", SDS200_WATERFALL_NDJSON_MEDIA_TYPE],
  ["data: {}", "application/json"],
]) {
  try {
    parser.call(target, line, mediaType);
  } catch (error) {
    rejected.push(error.message);
  }
}
process.stdout.write(JSON.stringify({accepted, rejected}));
"""
    )

    assert result == {
        "accepted": [None, None, None, '{"sequence":42}', '{"sequence":42}'],
        "rejected": [
            "Waterfall event field is unsupported.",
            "Waterfall record size is invalid.",
            "Waterfall stream returned an unsupported format.",
        ],
    }


def test_waterfall_card_expires_stale_frame_rate_samples() -> None:
    result = run_waterfall_card_javascript(
        """
const card = Object.create(Sds200WaterfallCard.prototype);
const now = performance.now();
card._checkpoint = {state: "running"};
card._frameTimes = [now - 7000, now - 6000];
card._lastFrameAt = null;
card._queueLoss = 0;
card._overflows = 0;
card._lastSequence = null;
card._telemetryValues = Object.fromEntries(
  ["session", "rate", "age", "loss", "sequence"].map(
    (key) => [key, {textContent: ""}],
  ),
);
card._renderTelemetry();
process.stdout.write(JSON.stringify({
  retainedSamples: card._frameTimes.length,
  rate: card._telemetryValues.rate.textContent,
}));
"""
    )

    assert result == {"retainedSamples": 0, "rate": "0.0 fps"}


def test_waterfall_card_discovers_only_sds200_app_panels() -> None:
    result = run_waterfall_card_javascript(
        """
const slugs = sds200PanelSlugs({panels: {
  production: {
    component_name: "app",
    title: "sds200",
    config: {addon: "6fc0784f_sds200"},
  },
  local: {
    component_name: "app",
    title: "sds200 Milestone 29.1",
    config: {addon: "local_sds200_29_1"},
  },
  unrelated: {
    component_name: "app",
    title: "Terminal",
    config: {addon: "core_ssh"},
  },
  dashboard: {
    component_name: "lovelace",
    title: "sds200",
    config: {addon: "not_an_app"},
  },
}});
process.stdout.write(JSON.stringify(slugs));
"""
    )

    assert result == ["6fc0784f_sds200", "local_sds200_29_1"]


def test_waterfall_card_resolves_one_running_authenticated_ingress_app() -> None:
    result = run_waterfall_card_javascript(
        """
(async () => {
  const calls = [];
  const api = {callWS: async (request) => {
    calls.push(request);
    return {
      state: "started",
      ingress: true,
      ingress_url: "/api/hassio_ingress/valid_ingress_key/",
    };
  }};
  const ui = {panels: {sds200: {
    component_name: "app",
    title: "sds200",
    config: {addon: "6fc0784f_sds200"},
  }}};
  const url = await resolveSds200IngressUrl(api, ui);
  process.stdout.write(JSON.stringify({url, calls}));
})().catch((error) => {
  process.stderr.write(error.stack);
  process.exitCode = 1;
});
"""
    )

    assert result["url"] == (
        "http://homeassistant.test/api/hassio_ingress/"
        "valid_ingress_key/api/v1/waterfall"
    )
    assert result["calls"] == [
        {
            "type": "supervisor/api",
            "endpoint": "/addons/6fc0784f_sds200/info",
            "method": "get",
        }
    ]


def test_waterfall_card_fails_closed_for_ambiguous_running_apps() -> None:
    result = run_waterfall_card_javascript(
        """
(async () => {
  const api = {callWS: async (request) => ({
    state: "started",
    ingress: true,
    ingress_url: request.endpoint.includes("local")
      ? "/api/hassio_ingress/local_key_123456/"
      : "/api/hassio_ingress/repository_key/",
  })};
  const ui = {panels: {
    repository: {
      component_name: "app",
      title: "sds200",
      config: {addon: "6fc0784f_sds200"},
    },
    local: {
      component_name: "app",
      title: "sds200 Milestone 29.1",
      config: {addon: "local_sds200_29_1"},
    },
  }};
  let message = null;
  try {
    await resolveSds200IngressUrl(api, ui);
  } catch (error) {
    message = error.message;
  }
  process.stdout.write(JSON.stringify(message));
})().catch((error) => {
  process.stderr.write(error.stack);
  process.exitCode = 1;
});
"""
    )

    assert result == "More than one sds200 Home Assistant App is running."


def test_waterfall_card_shares_ingress_authentication_across_card_leases() -> None:
    result = run_waterfall_card_javascript(
        """
(async () => {
  let sessions = 0;
  let cleared = 0;
  window.setInterval = () => 19;
  window.clearInterval = () => {cleared += 1;};
  const api = {callWS: async (request) => {
    if (request.endpoint === "/ingress/session") {
      sessions += 1;
      return {session: "valid_session_value_1234"};
    }
    return {};
  }};
  const first = await sds200WaterfallIngressSession.acquire(api);
  const second = await sds200WaterfallIngressSession.acquire(api);
  first();
  const leasesAfterFirst = sds200WaterfallIngressSession._leases;
  second();
  process.stdout.write(JSON.stringify({
    sessions,
    leasesAfterFirst,
    leases: sds200WaterfallIngressSession._leases,
    cleared,
    cookie: document.cookie.startsWith("ingress_session="),
  }));
})().catch((error) => {
  process.stderr.write(error.stack);
  process.exitCode = 1;
});
"""
    )

    assert result == {
        "sessions": 1,
        "leasesAfterFirst": 1,
        "leases": 0,
        "cleared": 1,
        "cookie": True,
    }


def test_waterfall_card_uses_current_home_assistant_context_and_ingress_contract() -> None:
    text = waterfall_card_text()

    for required in (
        'event.context = context;',
        '"hassApi"',
        '"hassUi"',
        'type: "supervisor/api"',
        'endpoint: "/ingress/session"',
        'endpoint: "/ingress/validate_session"',
        'credentials: "same-origin"',
        "headers: {Accept: SDS200_WATERFALL_SSE_MEDIA_TYPE}",
        'SDS200_WATERFALL_SSE_MEDIA_TYPE = "text/event-stream"',
        'SDS200_WATERFALL_NDJSON_MEDIA_TYPE = "application/x-ndjson"',
        "IntersectionObserver",
        'document.addEventListener("visibilitychange"',
        "controller.abort();",
        "reader.cancel()",
        "Promise.allSettled(",
    ):
        assert required in text

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "XMLHttpRequest",
        "WebSocket",
        "console.",
        "scanner_host",
    ):
        assert forbidden not in text


def test_waterfall_card_has_bounded_canvas_and_reconnect_work() -> None:
    text = waterfall_card_text()

    assert "SDS200_WATERFALL_BIN_COUNT = 240" in text
    assert "Object.freeze({value: 240, label: \"240 frames\"})" in text
    assert "Math.min(\n      2," in text
    assert "Math.min(\n      2048," in text
    assert "Math.min(\n      1024," in text
    assert "SDS200_WATERFALL_RECONNECT_DELAYS_MS" in text
    assert "30000," in text
    assert "window.requestAnimationFrame" in text
    assert "while (this._history.length > this._config.history)" in text
    assert "overflow: auto" not in text
    assert "overflow: scroll" not in text


def test_waterfall_card_install_is_atomic_idempotent_and_preserves_unrelated(
    tmp_path: Path,
) -> None:
    target = waterfall_target(tmp_path)
    target.parent.mkdir(parents=True)
    unrelated = target.parent / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")
    target.write_text("stale", encoding="utf-8")

    first = install_home_assistant_lovelace_waterfall_card(target)
    before = target.stat()
    second = install_home_assistant_lovelace_waterfall_card(target)
    after = target.stat()

    assert first == second == target
    assert target.read_text(encoding="utf-8") == waterfall_card_text()
    assert target.stat().st_mode & 0o777 == 0o644
    assert before.st_ino == after.st_ino
    assert before.st_mtime_ns == after.st_mtime_ns
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert list(target.parent.glob(".*.tmp")) == []


@pytest.mark.parametrize(
    "symlink_part",
    ["www", "sds200", "sds200-waterfall-card.js"],
)
def test_waterfall_card_install_refuses_symlink_paths(
    tmp_path: Path,
    symlink_part: str,
) -> None:
    homeassistant = tmp_path / "homeassistant"
    www = homeassistant / "www"
    card_directory = www / "sds200"
    target = card_directory / HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_FILENAME
    outside = tmp_path / "outside"

    homeassistant.mkdir()
    outside.mkdir()
    if symlink_part == "www":
        www.symlink_to(outside, target_is_directory=True)
    elif symlink_part == "sds200":
        www.mkdir()
        card_directory.symlink_to(outside, target_is_directory=True)
    else:
        card_directory.mkdir(parents=True)
        outside_file = outside / "outside.js"
        outside_file.write_text("outside", encoding="utf-8")
        target.symlink_to(outside_file)

    with pytest.raises(SDS200Error, match="refuses symlinks"):
        install_home_assistant_lovelace_waterfall_card(target)


def test_waterfall_card_install_rejects_wrong_destination() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        install_home_assistant_lovelace_waterfall_card(
            Path("www/sds200/sds200-waterfall-card.js")
        )
    with pytest.raises(ValueError, match="must use"):
        install_home_assistant_lovelace_waterfall_card(
            Path("/tmp/sds200-card.js")
        )


def test_waterfall_card_has_no_embedded_ingress_identifier() -> None:
    text = waterfall_card_text()

    assert re.search(r"/api/hassio_ingress/[A-Za-z0-9_-]{16,}/", text) is None
