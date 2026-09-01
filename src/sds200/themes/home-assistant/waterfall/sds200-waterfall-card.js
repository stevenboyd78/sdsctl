"use strict";

const SDS200_WATERFALL_CARD_TYPE = "sds200-waterfall-card";
const SDS200_WATERFALL_CARD_TAG = "sds200-waterfall-card";
const SDS200_WATERFALL_PROTOCOL = "sdsctl.waterfall";
const SDS200_WATERFALL_VERSION = 1;
const SDS200_WATERFALL_BIN_COUNT = 240;
const SDS200_WATERFALL_HISTORY_CAPACITY = 240;
const SDS200_WATERFALL_MAX_LINE_CHARACTERS = 64 * 1024;
const SDS200_WATERFALL_NDJSON_MEDIA_TYPE = "application/x-ndjson";
const SDS200_WATERFALL_SSE_MEDIA_TYPE = "text/event-stream";
const SDS200_WATERFALL_SESSION_REFRESH_MS = 60 * 1000;
const SDS200_WATERFALL_RECONNECT_DELAYS_MS = Object.freeze([
  2000,
  4000,
  8000,
  16000,
  30000,
]);
const SDS200_WATERFALL_DENSITIES = Object.freeze([
  Object.freeze({value: "compact", label: "Compact"}),
  Object.freeze({value: "standard", label: "Standard"}),
  Object.freeze({value: "tall", label: "Tall"}),
]);
const SDS200_WATERFALL_SYSTEM_PALETTES = Object.freeze({
  "ansi-dark": Object.freeze(["#0c0c0c", "#1a1a1a", "#242424", "#cccccc", "#858585", "#3b8eea", "#3b8eea", "#29b8db", "#e5e510", "#e74856", "#16c60c", "#16c60c"]),
  "ansi-light": Object.freeze(["#f5f5f5", "#ffffff", "#e5e5e5", "#0c0c0c", "#767676", "#0037da", "#0037da", "#3a96dd", "#e74856", "#c50f1f", "#13a10e", "#881798"]),
  "atom-one-dark": Object.freeze(["#282C34", "#3B414D", "#4F5666", "#ABB2BF", "#ABB2BF99", "#61AFEF", "#61AFEF", "#C678DD", "#DDB25B", "#EF6262", "#62F062", "#A378C2"]),
  "atom-one-light": Object.freeze(["#FAFAFA", "#E0E0E0", "#CCCCCC", "#383A42", "#383A4299", "#4078F2", "#4078F2", "#A626A4", "#D7D938", "#F13F3F", "#6BF23F", "#BE9232"]),
  "catppuccin-frappe": Object.freeze(["#303446", "#414559", "#51576D", "#C6D0F5", "#C6D0F599", "#BABBF1", "#CA9EE6", "#EE9F76", "#E4C890", "#E68284", "#A6D189", "#F4B8E4"]),
  "catppuccin-latte": Object.freeze(["#EFF1F5", "#E6E9EF", "#CCD0DA", "#4C4F69", "#4C4F6999", "#8839EF", "#8839EF", "#DB8A78", "#DE8E1D", "#D10F39", "#40A02B", "#FD640B"]),
  "catppuccin-macchiato": Object.freeze(["#24273A", "#363A4F", "#494D64", "#CAD3F5", "#CAD3F599", "#B7BDF8", "#C6A0F6", "#F4A97F", "#EED49F", "#ED8796", "#A6DA95", "#F5BDE6"]),
  "catppuccin-mocha": Object.freeze(["#181825", "#313244", "#45475A", "#CDD6F4", "#CDD6F499", "#b4befe", "#F5C2E7", "#CBA6F7", "#FAE3B0", "#F28FAD", "#ABE9B3", "#F9B387"]),
  dracula: Object.freeze(["#282A36", "#2B2E3B", "#313442", "#F8F8F2", "#F8F8F299", "#BD93F9", "#BD93F9", "#6272A4", "#FEB86C", "#FE5555", "#50FA7B", "#FF79C6"]),
  flexoki: Object.freeze(["#100F0F", "#1C1B1A", "#282726", "#FFFCF0", "#FFFCF099", "#205EA6", "#205EA6", "#24837B", "#AC8301", "#AE3029", "#65800B", "#9B76C8"]),
  gruvbox: Object.freeze(["#282828", "#3C3836", "#504945", "#FBF1C7", "#FBF1C799", "#85A598", "#85A598", "#A89A85", "#FD8019", "#FA4934", "#B7BB26", "#F9BD2F"]),
  monokai: Object.freeze(["#272822", "#2E2E2E", "#3E3D32", "#D6D6D6", "#797979", "#AE81FF", "#AE81FF", "#F82672", "#FC971F", "#F82672", "#A5E22E", "#66D9EF"]),
  nord: Object.freeze(["#2E3440", "#3B4252", "#434C5E", "#D8DEE9", "#D8DEE999", "#88C0D0", "#88C0D0", "#81A1C1", "#EACB8B", "#BE616A", "#A3BE8C", "#B48EAD"]),
  "rose-pine": Object.freeze(["#191724", "#1F1D2E", "#26233A", "#E0DEF4", "#E0DEF499", "#524f67", "#C4A7E7", "#31748F", "#F5C177", "#EA6F92", "#9CCFD8", "#EBBCBA"]),
  "rose-pine-dawn": Object.freeze(["#FAF4ED", "#FFFAF3", "#F2E9E1", "#575279", "#57527999", "#cecacd", "#907AA9", "#286983", "#E99D34", "#B4637A", "#56949F", "#D6827E"]),
  "rose-pine-moon": Object.freeze(["#232136", "#2A273F", "#393552", "#E0DEF4", "#E0DEF499", "#56526e", "#C4A7E7", "#3E8FB0", "#F5C177", "#EA6F92", "#9CCFD8", "#EA9A97"]),
  "solarized-dark": Object.freeze(["#002B36", "#073642", "#073642", "#839496", "#83949699", "#268BD2", "#268BD2", "#2AA198", "#CA4B16", "#DB322F", "#849900", "#6C71C4"]),
  "solarized-light": Object.freeze(["#FDF6E3", "#EEE8D5", "#EEE8D5", "#586E75", "#586E7599", "#268BD2", "#268BD2", "#2AA198", "#CA4B16", "#DB322F", "#849900", "#6C71C4"]),
  "textual-dark": Object.freeze(["#121212", "#1E1E1E", "#242F38", "#E0E0E0", "#E0E0E099", "#0178D4", "#0178D4", "#004578", "#FEA62B", "#B93C5B", "#4EBF71", "#FEA62B"]),
  "textual-light": Object.freeze(["#E0E0E0", "#D8D8D8", "#D0D0D0", "#1F1F1F", "#1F1F1F99", "#004578", "#004578", "#0178D4", "#FEA62B", "#B93C5B", "#4EBF71", "#FEA62B"]),
  "tokyo-night": Object.freeze(["#1A1B26", "#24283B", "#414868", "#A9B1D6", "#A9B1D699", "#BB9AF7", "#BB9AF7", "#7AA2F7", "#DFAF68", "#F6768E", "#9ECE6A", "#FE9E64"]),
});
const SDS200_WATERFALL_PALETTES = Object.freeze([
  Object.freeze({value: "theme", label: "Home Assistant theme"}),
  Object.freeze({value: "cyan", label: "Cyan"}),
  Object.freeze({value: "green", label: "Green"}),
  Object.freeze({value: "amber", label: "Amber"}),
  Object.freeze({value: "monochrome", label: "Monochrome"}),
  ...Object.keys(SDS200_WATERFALL_SYSTEM_PALETTES).map((value) =>
    Object.freeze({value, label: value}),
  ),
]);
const SDS200_WATERFALL_HISTORY_OPTIONS = Object.freeze([
  Object.freeze({value: 60, label: "60 frames"}),
  Object.freeze({value: 120, label: "120 frames"}),
  Object.freeze({value: 240, label: "240 frames"}),
]);
const SDS200_WATERFALL_HISTORY_MODES = Object.freeze([
  Object.freeze({value: "frames", label: "Frame count"}),
  Object.freeze({value: "duration", label: "Elapsed time"}),
]);
const SDS200_WATERFALL_HISTORY_DURATION_OPTIONS = Object.freeze([
  Object.freeze({value: 15, label: "15 seconds"}),
  Object.freeze({value: 30, label: "30 seconds"}),
  Object.freeze({value: 60, label: "60 seconds"}),
]);
const SDS200_WATERFALL_INGRESS_PATH_PATTERN =
  /^\/api\/hassio_ingress\/[A-Za-z0-9_-]+\/$/;

function waterfallOptionLabel(options, value) {
  return options.find((option) => option.value === value)?.label;
}

function applyWaterfallSystemPalette(element, palette) {
  const names = [
    "background", "surface", "panel", "foreground", "muted", "border",
    "primary", "secondary", "warning", "error", "success", "accent",
  ];
  names.forEach((name) => {
    element.style.removeProperty(`--sds200-waterfall-${name}`);
  });
  const colors = SDS200_WATERFALL_SYSTEM_PALETTES[palette];
  if (colors === undefined) {
    return;
  }
  names.forEach((name, index) => {
    element.style.setProperty(`--sds200-waterfall-${name}`, colors[index]);
  });
}

function requireWaterfallCardConfig(config) {
  if (
    config === null ||
    typeof config !== "object" ||
    Array.isArray(config)
  ) {
    throw new Error(
      "SDS200 waterfall card configuration must be an object.",
    );
  }

  const supported = new Set([
    "type",
    "title",
    "density",
    "palette",
    "history",
    "history_mode",
    "history_seconds",
    "show_scale",
    "show_telemetry",
    "show_pointer",
    "start_paused",
    "grid_options",
  ]);
  for (const key of Object.keys(config)) {
    if (!supported.has(key)) {
      throw new Error(
        `SDS200 waterfall card option "${key}" is not supported.`,
      );
    }
  }

  if (
    config.title !== undefined &&
    typeof config.title !== "string"
  ) {
    throw new Error("SDS200 waterfall card title must be text.");
  }
  const title =
    typeof config.title === "string" && config.title.trim()
      ? config.title.trim()
      : "SDS200 Waterfall";
  const density = config.density ?? "standard";
  const palette = config.palette ?? "theme";
  const historyMode = config.history_mode ?? "frames";
  const requestedHistory = config.history ?? 120;
  const historyOption = SDS200_WATERFALL_HISTORY_OPTIONS.find(
    (option) =>
      option.value === requestedHistory ||
      String(option.value) === requestedHistory,
  );

  if (!waterfallOptionLabel(SDS200_WATERFALL_DENSITIES, density)) {
    throw new Error(
      `SDS200 waterfall card density "${density}" is not supported.`,
    );
  }
  if (!waterfallOptionLabel(SDS200_WATERFALL_PALETTES, palette)) {
    throw new Error(
      `SDS200 waterfall card palette "${palette}" is not supported.`,
    );
  }
  if (historyOption === undefined) {
    throw new Error(
      `SDS200 waterfall card history "${requestedHistory}" is not supported.`,
    );
  }
  const history = historyOption.value;
  if (!waterfallOptionLabel(SDS200_WATERFALL_HISTORY_MODES, historyMode)) {
    throw new Error(
      `SDS200 waterfall card history mode "${historyMode}" is not supported.`,
    );
  }
  const requestedHistorySeconds = config.history_seconds ?? 30;
  const historyDurationOption = SDS200_WATERFALL_HISTORY_DURATION_OPTIONS.find(
    (option) => option.value === requestedHistorySeconds ||
      String(option.value) === requestedHistorySeconds,
  );
  if (historyDurationOption === undefined) {
    throw new Error(
      `SDS200 waterfall card history duration "${requestedHistorySeconds}" is not supported.`,
    );
  }
  const historySeconds = historyDurationOption.value;

  const booleans = {};
  for (const [key, fallback] of [
    ["show_scale", true],
    ["show_telemetry", true],
    ["show_pointer", false],
    ["start_paused", false],
  ]) {
    const value = config[key] ?? fallback;
    if (typeof value !== "boolean") {
      throw new Error(
        `SDS200 waterfall card option "${key}" must be boolean.`,
      );
    }
    booleans[key] = value;
  }

  return Object.freeze({
    title,
    density,
    palette,
    history,
    history_mode: historyMode,
    history_seconds: historySeconds,
    show_scale: booleans.show_scale,
    show_telemetry: booleans.show_telemetry,
    show_pointer: booleans.show_pointer,
    start_paused: booleans.start_paused,
  });
}

function waterfallRecordObject(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value)
  ) ? value : {};
}

function waterfallNonnegativeInteger(value, label) {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`Waterfall ${label} is invalid.`);
  }
  return value;
}

function normalizeWaterfallFrame(values) {
  if (
    !Array.isArray(values) ||
    values.length !== SDS200_WATERFALL_BIN_COUNT
  ) {
    throw new Error("Waterfall frame must contain exactly 240 values.");
  }
  const numeric = values.map((value) => {
    if (
      typeof value !== "string" ||
      !/^[0-9a-f]+$/i.test(value)
    ) {
      throw new Error(
        "Waterfall frame contains an invalid hexadecimal value.",
      );
    }
    return Number.parseInt(value, 16);
  });
  if (!numeric.every(Number.isSafeInteger)) {
    throw new Error("Waterfall frame contains an invalid value.");
  }
  const minimum = Math.min(...numeric);
  const maximum = Math.max(...numeric);
  const span = maximum - minimum;
  return Float32Array.from(
    numeric,
    (value) => span === 0 ? 0.5 : (value - minimum) / span,
  );
}

function validWaterfallFrequencies(status) {
  const source = waterfallRecordObject(status);
  const fields = [
    source.lower_frequency,
    source.center_frequency,
    source.upper_frequency,
    source.marker_frequency,
  ];
  if (!fields.every(
    (value) => typeof value === "string" && /^\d+(?:\.\d+)?$/.test(value),
  )) {
    return null;
  }
  const numbers = fields.map(Number);
  const markerPosition = Number(source.marker_position);
  if (
    !numbers.every(Number.isFinite) ||
    !(numbers[0] < numbers[1] && numbers[1] < numbers[2]) ||
    numbers[3] < numbers[0] ||
    numbers[3] > numbers[2] ||
    !Number.isInteger(markerPosition) ||
    markerPosition < 0 ||
    markerPosition >= SDS200_WATERFALL_BIN_COUNT
  ) {
    return null;
  }
  return Object.freeze({
    fields: Object.freeze(fields),
    numbers: Object.freeze(numbers),
    markerPosition,
  });
}

function waterfallHistoryPolicy(mode, value) {
  if (mode === "frames") {
    const frames = Number(value);
    if (![60, 120, SDS200_WATERFALL_HISTORY_CAPACITY].includes(frames)) {
      throw new Error("Waterfall frame history is invalid.");
    }
    return Object.freeze({mode, frames, seconds: null});
  }
  if (mode === "duration") {
    const seconds = Number(value);
    if (![15, 30, 60].includes(seconds)) {
      throw new Error("Waterfall duration history is invalid.");
    }
    return Object.freeze({
      mode,
      frames: SDS200_WATERFALL_HISTORY_CAPACITY,
      seconds,
    });
  }
  throw new Error("Waterfall history mode is invalid.");
}

function pruneWaterfallHistory(history, policy, now) {
  if (!Array.isArray(history) || !Number.isFinite(now)) {
    throw new Error("Waterfall history state is invalid.");
  }
  let retained = history.slice(-policy.frames);
  if (policy.mode === "duration") {
    const cutoff = now - policy.seconds * 1000;
    retained = retained.filter(
      (entry) => Number.isFinite(entry.receivedAt) && entry.receivedAt >= cutoff,
    );
  }
  return retained;
}

function waterfallHistoryRows(history, policy, height, now) {
  if (!Number.isFinite(height) || height <= 0) {
    return [];
  }
  const retained = pruneWaterfallHistory(history, policy, now);
  if (policy.mode === "frames") {
    const rowHeight = height / policy.frames;
    const startRow = policy.frames - retained.length;
    return retained.map((entry, index) => ({
      entry,
      y: (startRow + index) * rowHeight,
      height: Math.max(1, rowHeight),
    }));
  }
  const duration = policy.seconds * 1000;
  const cutoff = now - duration;
  return retained.map((entry, index) => {
    const nextAt = retained[index + 1]?.receivedAt ?? now;
    const start = Math.max(0, Math.min(1, (entry.receivedAt - cutoff) / duration));
    const end = Math.max(start, Math.min(1, (nextAt - cutoff) / duration));
    const y = Math.min(height - 1, start * height);
    return {
      entry,
      y,
      height: Math.max(1, Math.min(height - y, (end - start) * height)),
    };
  });
}

function waterfallPointerFrequency(status, ratio) {
  const frequencies = validWaterfallFrequencies(status);
  if (frequencies === null || !Number.isFinite(ratio)) {
    return null;
  }
  const boundedRatio = Math.max(0, Math.min(1, ratio));
  const raw = frequencies.numbers[0] +
    (frequencies.numbers[2] - frequencies.numbers[0]) * boundedRatio;
  return Object.freeze({
    ratio: boundedRatio,
    raw,
    label: `${(raw / 10000).toFixed(4)} MHz`,
  });
}

function requestHomeAssistantContext(
  target,
  context,
  callback,
  {subscribe = false} = {},
) {
  const event = new CustomEvent("context-request", {
    bubbles: true,
    composed: true,
    cancelable: true,
  });
  event.context = context;
  event.subscribe = subscribe;
  event.callback = callback;
  target.dispatchEvent(event);
}

class WaterfallCardError extends Error {
  constructor(message) {
    super(message);
    this.name = "WaterfallCardError";
  }
}

const sds200WaterfallIngressSession = {
  _api: null,
  _session: null,
  _sessionPromise: null,
  _leases: 0,
  _refreshTimer: null,

  async acquire(api) {
    this._leases += 1;
    this._api = api;
    let released = false;
    try {
      await this._ensure(api);
    } catch (error) {
      this._leases -= 1;
      this._stopRefreshIfUnused();
      throw error;
    }
    return () => {
      if (released) {
        return;
      }
      released = true;
      this._leases = Math.max(0, this._leases - 1);
      this._stopRefreshIfUnused();
    };
  },

  invalidate() {
    this._session = null;
    this._sessionPromise = null;
  },

  async _ensure(api) {
    if (this._session !== null) {
      this._startRefresh();
      return;
    }
    if (this._sessionPromise === null) {
      this._sessionPromise = this._create(api).finally(() => {
        this._sessionPromise = null;
      });
    }
    await this._sessionPromise;
    this._startRefresh();
  },

  async _create(api) {
    if (api === null || typeof api.callWS !== "function") {
      throw new WaterfallCardError(
        "Home Assistant authentication is unavailable.",
      );
    }
    let response;
    try {
      response = await api.callWS({
        type: "supervisor/api",
        endpoint: "/ingress/session",
        method: "post",
      });
    } catch {
      throw new WaterfallCardError(
        "Home Assistant App authentication is unavailable.",
      );
    }
    const session = waterfallRecordObject(response).session;
    if (
      typeof session !== "string" ||
      !/^[A-Za-z0-9_-]{16,256}$/.test(session)
    ) {
      throw new WaterfallCardError(
        "Home Assistant returned an invalid App session.",
      );
    }
    document.cookie =
      `ingress_session=${session};path=/api/hassio_ingress/;SameSite=Strict` +
      (location.protocol === "https:" ? ";Secure" : "");
    this._session = session;
  },

  _startRefresh() {
    if (this._leases === 0 || this._refreshTimer !== null) {
      return;
    }
    this._refreshTimer = window.setInterval(() => {
      void this._refresh();
    }, SDS200_WATERFALL_SESSION_REFRESH_MS);
  },

  async _refresh() {
    if (
      this._leases === 0 ||
      this._api === null ||
      this._session === null
    ) {
      return;
    }
    try {
      await this._api.callWS({
        type: "supervisor/api",
        endpoint: "/ingress/validate_session",
        method: "post",
        data: {session: this._session},
      });
    } catch {
      this.invalidate();
      try {
        await this._ensure(this._api);
      } catch {
        this.invalidate();
      }
    }
  },

  _stopRefreshIfUnused() {
    if (this._leases !== 0) {
      return;
    }
    if (this._refreshTimer !== null) {
      window.clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
    this._api = null;
    this._session = null;
    this._sessionPromise = null;
  },
};

function sds200PanelSlugs(ui) {
  const panels = waterfallRecordObject(
    waterfallRecordObject(ui).panels,
  );
  const slugs = [];
  for (const panel of Object.values(panels)) {
    const value = waterfallRecordObject(panel);
    const config = waterfallRecordObject(value.config);
    const slug = config.addon;
    const title = value.title;
    if (
      value.component_name === "app" &&
      typeof slug === "string" &&
      /^[a-z0-9_]+$/.test(slug) &&
      (
        slug === "sds200" ||
        slug.includes("_sds200") ||
        (typeof title === "string" && /^sds200(?:\s|$)/i.test(title))
      )
    ) {
      slugs.push(slug);
    }
  }
  return [...new Set(slugs)].sort();
}

async function resolveSds200IngressUrl(api, ui) {
  if (api === null || typeof api.callWS !== "function") {
    throw new WaterfallCardError(
      "Home Assistant authentication is unavailable.",
    );
  }
  const slugs = sds200PanelSlugs(ui);
  if (slugs.length === 0) {
    throw new WaterfallCardError(
      "No sds200 Home Assistant App panel is available.",
    );
  }

  const settled = await Promise.allSettled(
    slugs.map((slug) => api.callWS({
      type: "supervisor/api",
      endpoint: `/addons/${slug}/info`,
      method: "get",
    })),
  );
  const running = [];
  for (const result of settled) {
    if (result.status !== "fulfilled") {
      continue;
    }
    const info = waterfallRecordObject(result.value);
    if (
      info.state === "started" &&
      info.ingress === true &&
      typeof info.ingress_url === "string"
    ) {
      const url = new URL(info.ingress_url, location.origin);
      if (
        url.origin === location.origin &&
        SDS200_WATERFALL_INGRESS_PATH_PATTERN.test(url.pathname) &&
        url.search === "" &&
        url.hash === ""
      ) {
        running.push(url);
      }
    }
  }
  if (running.length === 0) {
    throw new WaterfallCardError(
      "Start the sds200 Home Assistant App to view its waterfall.",
    );
  }
  if (running.length !== 1) {
    throw new WaterfallCardError(
      "More than one sds200 Home Assistant App is running.",
    );
  }
  return new URL("api/v1/waterfall", running[0]).toString();
}

function waterfallNode(documentObject, tag, className, text = null) {
  const node = documentObject.createElement(tag);
  node.className = className;
  if (text !== null) {
    node.textContent = text;
  }
  return node;
}

class Sds200WaterfallCard extends HTMLElement {
  static getStubConfig() {
    return {
      density: "standard",
      palette: "theme",
      history: 120,
      history_mode: "duration",
      history_seconds: 30,
      show_scale: true,
      show_telemetry: true,
      show_pointer: false,
      start_paused: false,
    };
  }

  static getConfigForm() {
    return {
      schema: [
        {name: "title", selector: {text: {}}},
        {
          name: "density",
          required: true,
          selector: {select: {options: SDS200_WATERFALL_DENSITIES}},
        },
        {
          name: "palette",
          required: true,
          selector: {select: {options: SDS200_WATERFALL_PALETTES}},
        },
        {
          name: "history",
          required: true,
          selector: {select: {options: SDS200_WATERFALL_HISTORY_OPTIONS}},
        },
        {
          name: "history_mode",
          required: true,
          selector: {select: {options: SDS200_WATERFALL_HISTORY_MODES}},
        },
        {
          name: "history_seconds",
          required: true,
          selector: {
            select: {options: SDS200_WATERFALL_HISTORY_DURATION_OPTIONS},
          },
        },
        {name: "show_scale", selector: {boolean: {}}},
        {name: "show_telemetry", selector: {boolean: {}}},
        {name: "show_pointer", selector: {boolean: {}}},
        {name: "start_paused", selector: {boolean: {}}},
      ],
      computeLabel: (schema) => ({
        title: "Title",
        density: "Card height",
        palette: "Palette",
        history: "History depth",
        history_mode: "History mode",
        history_seconds: "Elapsed history",
        show_scale: "Show relative frequency scale",
        show_telemetry: "Show lifecycle telemetry",
        show_pointer: "Enable display-only frequency pointer",
        start_paused: "Start with display paused",
      })[schema.name],
      computeHelper: (schema) => ({
        palette: "Follow Home Assistant, use a Waterfall preset, or choose a System web palette.",
        history: "Frame-count mode keeps this bounded number of frames.",
        history_mode: "Existing cards default to frame count; elapsed time is an explicit alternative.",
        history_seconds: "Elapsed-time mode is also capped at 240 frames.",
        show_pointer: "Inspect frequency without tuning or changing the scanner.",
        start_paused: "The authenticated stream still connects while paused.",
      })[schema.name],
      assertConfig: (config) => {
        requireWaterfallCardConfig(config);
      },
    };
  }

  constructor() {
    super();
    this._config = requireWaterfallCardConfig({});
    this._historyPolicy = waterfallHistoryPolicy(
      this._config.history_mode,
      this._config.history,
    );
    this._connected = false;
    this._intersecting = false;
    this._api = null;
    this._ui = null;
    this._uiUnsubscribe = null;
    this._intersectionObserver = null;
    this._resizeObserver = null;
    this._generation = 0;
    this._controller = null;
    this._reader = null;
    this._releaseAuthentication = null;
    this._retryTimer = null;
    this._retryIndex = 0;
    this._streaming = false;
    this._paused = false;
    this._history = [];
    this._latestFrame = null;
    this._pointerRatio = null;
    this._lastSequence = null;
    this._lastFrameAt = null;
    this._frameTimes = [];
    this._checkpoint = {};
    this._queueLoss = 0;
    this._overflows = 0;
    this._transitions = 0;
    this._paintRequest = null;
    this._frameAgeTimer = null;
    this._onVisibilityChange = this._onVisibilityChange.bind(this);
    this.attachShadow({mode: "open"});
    this._build();
  }

  connectedCallback() {
    if (this._connected) {
      return;
    }
    this._connected = true;
    document.addEventListener("visibilitychange", this._onVisibilityChange);
    if (typeof IntersectionObserver === "function") {
      this._intersectionObserver = new IntersectionObserver((entries) => {
        const entry = entries.at(-1);
        this._intersecting = Boolean(
          entry && entry.isIntersecting && entry.intersectionRatio > 0,
        );
        this._reconcileDemand();
      });
      this._intersectionObserver.observe(this);
    } else {
      this._intersecting = true;
    }
    if (typeof ResizeObserver === "function") {
      this._resizeObserver = new ResizeObserver(() => {
        this._schedulePaint();
      });
      this._resizeObserver.observe(this._surface);
    }
    this._frameAgeTimer = window.setInterval(() => {
      this._renderTelemetry();
    }, 1000);
    requestHomeAssistantContext(
      this,
      "hassApi",
      (api) => {
        this._api = api;
        this._reconcileDemand();
      },
    );
    requestHomeAssistantContext(
      this,
      "hassUi",
      (ui, unsubscribe) => {
        this._ui = ui;
        if (typeof unsubscribe === "function") {
          this._uiUnsubscribe = unsubscribe;
        }
        this._schedulePaint();
        this._reconcileDemand();
      },
      {subscribe: true},
    );
    this._render();
    this._reconcileDemand();
  }

  disconnectedCallback() {
    if (!this._connected) {
      return;
    }
    this._connected = false;
    document.removeEventListener("visibilitychange", this._onVisibilityChange);
    if (this._intersectionObserver !== null) {
      this._intersectionObserver.disconnect();
      this._intersectionObserver = null;
    }
    if (this._resizeObserver !== null) {
      this._resizeObserver.disconnect();
      this._resizeObserver = null;
    }
    if (typeof this._uiUnsubscribe === "function") {
      this._uiUnsubscribe();
    }
    this._uiUnsubscribe = null;
    if (this._frameAgeTimer !== null) {
      window.clearInterval(this._frameAgeTimer);
      this._frameAgeTimer = null;
    }
    this._stopStream({status: "Disconnected from the dashboard."});
  }

  setConfig(config) {
    this._config = requireWaterfallCardConfig(config);
    this._historyPolicy = waterfallHistoryPolicy(
      this._config.history_mode,
      this._config.history_mode === "duration"
        ? this._config.history_seconds
        : this._config.history,
    );
    this._paused = this._config.start_paused;
    this._history = pruneWaterfallHistory(
      this._history,
      this._historyPolicy,
      performance.now(),
    );
    if (!this._config.show_pointer) {
      this._pointerRatio = null;
    }
    this._render();
    this._schedulePaint();
  }

  getCardSize() {
    return ({compact: 5, standard: 7, tall: 9})[this._config.density];
  }

  getGridOptions() {
    return {
      rows: ({compact: 5, standard: 7, tall: 9})[this._config.density],
      columns: 12,
      min_rows: 4,
      min_columns: 3,
    };
  }

  _build() {
    const root = this.shadowRoot;
    const style = document.createElement("style");
    style.textContent = `
      :host {
        display: block;
        min-width: 0;
        container-type: inline-size;
      }
      ha-card {
        display: grid;
        grid-template-rows: auto minmax(0, 1fr) auto auto auto;
        gap: 0.75rem;
        height: var(--sds200-waterfall-card-height);
        min-height: 0;
        padding: 1rem;
        overflow: hidden;
        box-sizing: border-box;
        background: var(--sds200-waterfall-surface, var(--ha-card-background, var(--card-background-color)));
        color: var(--sds200-waterfall-foreground, var(--primary-text-color));
      }
      ha-card[data-density="compact"] {
        --sds200-waterfall-card-height: min(22rem, calc(100dvh - 5rem));
      }
      ha-card[data-density="standard"] {
        --sds200-waterfall-card-height: min(31rem, calc(100dvh - 5rem));
      }
      ha-card[data-density="tall"] {
        --sds200-waterfall-card-height: min(42rem, calc(100dvh - 5rem));
      }
      .header,
      .actions,
      .scale,
      .telemetry {
        display: flex;
        align-items: center;
        gap: 0.65rem;
        min-width: 0;
      }
      .header {
        justify-content: space-between;
      }
      .heading {
        min-width: 0;
      }
      h2,
      p,
      dl,
      dd,
      dt {
        margin: 0;
      }
      h2 {
        overflow: hidden;
        font-size: 1.05rem;
        line-height: 1.25;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .status {
        margin-top: 0.15rem;
        color: var(--sds200-waterfall-muted, var(--secondary-text-color));
        font-size: 0.78rem;
        line-height: 1.25;
      }
      .status[data-state="live"] {
        color: var(--sds200-waterfall-success, var(--success-color, #2e7d32));
      }
      .status[data-state="error"] {
        color: var(--sds200-waterfall-error, var(--error-color, #b71c1c));
      }
      .actions {
        flex: 0 0 auto;
      }
      button {
        min-height: 2.25rem;
        border: 1px solid var(--sds200-waterfall-border, var(--divider-color));
        border-radius: 0.45rem;
        padding: 0.35rem 0.7rem;
        background: transparent;
        color: inherit;
        font: inherit;
        cursor: pointer;
      }
      button:focus-visible {
        outline: 2px solid var(--sds200-waterfall-primary, var(--primary-color));
        outline-offset: 2px;
      }
      .surface {
        display: grid;
        grid-template-rows: minmax(3rem, 1fr) minmax(5rem, 2.2fr);
        min-height: 0;
        overflow: hidden;
        border: 1px solid var(--sds200-waterfall-border, var(--divider-color));
        border-radius: 0.45rem;
        background: #07111c;
      }
      canvas {
        display: block;
        width: 100%;
        height: 100%;
        min-height: 0;
        touch-action: pan-y;
      }
      canvas[data-pointer-enabled="true"] {
        touch-action: none;
      }
      canvas:focus-visible {
        outline: 2px solid var(--sds200-waterfall-accent, var(--accent-color));
        outline-offset: -2px;
      }
      .history {
        border-top: 1px solid var(--sds200-waterfall-border, var(--divider-color));
      }
      .scale {
        justify-content: space-between;
        color: var(--sds200-waterfall-muted, var(--secondary-text-color));
        font-size: 0.72rem;
      }
      .scale[hidden],
      .telemetry[hidden] {
        display: none;
      }
      .scale span {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .scale .center {
        text-align: center;
      }
      .scale .upper {
        text-align: right;
      }
      .pointer-readout {
        min-width: 0;
        overflow: hidden;
        color: var(--sds200-waterfall-foreground, var(--primary-text-color));
        font-size: 0.72rem;
        line-height: 1.2;
        text-align: center;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .pointer-readout[hidden] {
        display: none;
      }
      .telemetry {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        color: var(--sds200-waterfall-muted, var(--secondary-text-color));
        font-size: 0.7rem;
      }
      .telemetry div {
        min-width: 0;
      }
      .telemetry dt,
      .telemetry dd {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .telemetry dt {
        font-size: 0.62rem;
        text-transform: uppercase;
      }
      .telemetry dd {
        color: var(--sds200-waterfall-foreground, var(--primary-text-color));
      }
      @container (max-width: 32rem) {
        ha-card {
          gap: 0.5rem;
          padding: 0.75rem;
        }
        .header {
          align-items: flex-start;
        }
        .actions {
          gap: 0.35rem;
        }
        button {
          min-height: 2rem;
          padding: 0.25rem 0.45rem;
          font-size: 0.75rem;
        }
        .telemetry {
          grid-template-columns: repeat(3, minmax(0, 1fr));
        }
        .telemetry div:nth-child(n + 4) {
          display: none;
        }
      }
    `;
    this._card = document.createElement("ha-card");
    this._header = waterfallNode(document, "header", "header");
    const heading = waterfallNode(document, "div", "heading");
    this._title = waterfallNode(document, "h2", "title");
    this._status = waterfallNode(
      document,
      "p",
      "status",
      "Waiting for Home Assistant context.",
    );
    this._status.setAttribute("role", "status");
    this._status.setAttribute("aria-live", "polite");
    heading.append(this._title, this._status);
    this._actions = waterfallNode(document, "div", "actions");
    this._pauseButton = waterfallNode(document, "button", "pause", "Pause");
    this._pauseButton.type = "button";
    this._pauseButton.addEventListener("click", () => {
      this._paused = !this._paused;
      if (!this._paused) {
        this._history = pruneWaterfallHistory(
          this._history,
          this._historyPolicy,
          performance.now(),
        );
        this._schedulePaint();
      }
      this._render();
    });
    this._clearButton = waterfallNode(document, "button", "clear", "Clear");
    this._clearButton.type = "button";
    this._clearButton.addEventListener("click", () => {
      this._history = [];
      this._latestFrame = null;
      this._schedulePaint();
    });
    this._actions.append(this._pauseButton, this._clearButton);
    this._header.append(heading, this._actions);

    this._surface = waterfallNode(document, "div", "surface");
    this._spectrum = waterfallNode(document, "canvas", "spectrum");
    this._spectrum.setAttribute(
      "aria-label",
      "Current relative 240-bin spectrum. Enable the frequency pointer to inspect with pointer or arrow keys.",
    );
    this._historyCanvas = waterfallNode(document, "canvas", "history");
    this._historyCanvas.setAttribute(
      "aria-label",
      "Rolling relative waterfall history. Enable the frequency pointer to inspect with pointer or arrow keys.",
    );
    this._initializePointerCanvas(this._spectrum);
    this._initializePointerCanvas(this._historyCanvas);
    this._surface.append(this._spectrum, this._historyCanvas);

    this._pointerReadout = waterfallNode(
      document,
      "output",
      "pointer-readout",
      "Frequency pointer unavailable.",
    );
    this._pointerReadout.hidden = true;

    this._scale = waterfallNode(document, "div", "scale");
    this._scaleLower = waterfallNode(document, "span", "lower", "Unavailable");
    this._scaleCenter = waterfallNode(document, "span", "center", "Unavailable");
    this._scaleUpper = waterfallNode(document, "span", "upper", "Unavailable");
    this._scale.append(
      this._scaleLower,
      this._scaleCenter,
      this._scaleUpper,
    );

    this._telemetry = waterfallNode(document, "dl", "telemetry");
    this._telemetryValues = {};
    for (const [key, label] of [
      ["session", "Session"],
      ["rate", "Frame rate"],
      ["age", "Frame age"],
      ["loss", "Queue loss"],
      ["sequence", "Sequence"],
    ]) {
      const item = waterfallNode(document, "div", "telemetry-item");
      const term = waterfallNode(document, "dt", "telemetry-label", label);
      const value = waterfallNode(document, "dd", "telemetry-value", "—");
      item.append(term, value);
      this._telemetry.append(item);
      this._telemetryValues[key] = value;
    }

    this._card.append(
      this._header,
      this._surface,
      this._pointerReadout,
      this._scale,
      this._telemetry,
    );
    root.append(style, this._card);
  }

  _initializePointerCanvas(canvas) {
    canvas.setAttribute("role", "img");
    const updateFromPointer = (event) => {
      if (!this._config.show_pointer) {
        return;
      }
      const bounds = canvas.getBoundingClientRect();
      if (!Number.isFinite(bounds.width) || bounds.width <= 0) {
        this._setPointerRatio(null);
        return;
      }
      this._setPointerRatio(
        (Number(event.clientX) - bounds.left) / bounds.width,
      );
    };
    canvas.addEventListener("pointerdown", updateFromPointer);
    canvas.addEventListener("pointermove", updateFromPointer);
    canvas.addEventListener("pointerleave", () => {
      if (this.shadowRoot.activeElement !== canvas) {
        this._setPointerRatio(null);
      }
    });
    canvas.addEventListener("blur", () => {
      this._setPointerRatio(null);
    });
    canvas.addEventListener("keydown", (event) => {
      if (!this._config.show_pointer) {
        return;
      }
      const step = 1 / (SDS200_WATERFALL_BIN_COUNT - 1);
      const current = this._pointerRatio ?? 0.5;
      let next = current;
      if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
        next = current - step;
      } else if (event.key === "ArrowRight" || event.key === "ArrowUp") {
        next = current + step;
      } else if (event.key === "Home") {
        next = 0;
      } else if (event.key === "End") {
        next = 1;
      } else if (event.key === "Escape") {
        next = null;
      } else {
        return;
      }
      event.preventDefault();
      this._setPointerRatio(next);
    });
  }

  _setPointerRatio(value) {
    this._pointerRatio = Number.isFinite(value)
      ? Math.max(0, Math.min(1, value))
      : null;
    this._renderPointer();
    this._schedulePaint();
  }

  _renderPointer() {
    if (this._pointerReadout === undefined) {
      return;
    }
    this._pointerReadout.hidden = !this._config.show_pointer;
    if (!this._config.show_pointer) {
      this._pointerReadout.textContent = "Frequency pointer disabled.";
      return;
    }
    const pointer = waterfallPointerFrequency(
      waterfallRecordObject(this._checkpoint).waterfall_status,
      this._pointerRatio,
    );
    this._pointerReadout.textContent = pointer === null
      ? "Frequency pointer unavailable."
      : `Frequency pointer: ${pointer.label}`;
  }

  _onVisibilityChange() {
    this._reconcileDemand();
  }

  _demanded() {
    return (
      this._connected &&
      this._intersecting &&
      !document.hidden &&
      this._api !== null &&
      this._ui !== null
    );
  }

  _reconcileDemand() {
    if (this._demanded()) {
      if (
        !this._streaming &&
        this._controller === null &&
        this._retryTimer === null
      ) {
        void this._startStream();
      }
      return;
    }
    if (
      this._streaming ||
      this._controller !== null ||
      this._retryTimer !== null
    ) {
      this._stopStream({
        clearHistory: true,
        status: "Waterfall waits until this card is visible.",
      });
    } else if (this._connected) {
      this._setStatus(
        "idle",
        "Waterfall waits until this card is visible.",
      );
    }
  }

  _clearRetry() {
    if (this._retryTimer !== null) {
      window.clearTimeout(this._retryTimer);
      this._retryTimer = null;
    }
  }

  _resetLiveState({clearHistory = true} = {}) {
    this._streaming = false;
    this._lastSequence = null;
    this._lastFrameAt = null;
    this._frameTimes = [];
    this._latestFrame = null;
    this._checkpoint = {};
    this._queueLoss = 0;
    this._overflows = 0;
    this._transitions = 0;
    this._pointerRatio = null;
    if (clearHistory) {
      this._history = [];
    }
    this._renderPointer();
    this._renderTelemetry();
    this._schedulePaint();
  }

  _stopStream({clearHistory = true, status = "Idle"} = {}) {
    this._generation += 1;
    this._clearRetry();
    const controller = this._controller;
    this._controller = null;
    if (controller !== null) {
      controller.abort();
    }
    const reader = this._reader;
    this._reader = null;
    if (reader !== null) {
      void reader.cancel().catch(() => {});
    }
    const release = this._releaseAuthentication;
    this._releaseAuthentication = null;
    if (release !== null) {
      release();
    }
    this._resetLiveState({clearHistory});
    this._setStatus("idle", status);
  }

  _scheduleReconnect(generation) {
    if (!this._demanded() || generation !== this._generation) {
      return;
    }
    this._clearRetry();
    const index = Math.min(
      this._retryIndex,
      SDS200_WATERFALL_RECONNECT_DELAYS_MS.length - 1,
    );
    const delay = SDS200_WATERFALL_RECONNECT_DELAYS_MS[index];
    this._retryIndex = Math.min(
      this._retryIndex + 1,
      SDS200_WATERFALL_RECONNECT_DELAYS_MS.length - 1,
    );
    this._retryTimer = window.setTimeout(() => {
      this._retryTimer = null;
      if (this._demanded() && generation === this._generation) {
        void this._startStream();
      }
    }, delay);
  }

  async _startStream() {
    if (!this._demanded()) {
      return;
    }
    this._stopStream({
      clearHistory: true,
      status: "Authenticating the waterfall stream…",
    });
    const generation = this._generation;
    const controller = new AbortController();
    let releaseAuthentication = null;
    this._controller = controller;
    try {
      releaseAuthentication =
        await sds200WaterfallIngressSession.acquire(this._api);
      if (generation !== this._generation || controller.signal.aborted) {
        return;
      }
      this._releaseAuthentication = releaseAuthentication;
      const streamUrl = await resolveSds200IngressUrl(this._api, this._ui);
      const response = await fetch(streamUrl, {
        headers: {Accept: SDS200_WATERFALL_SSE_MEDIA_TYPE},
        cache: "no-store",
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!response.ok || response.body === null) {
        if (response.status === 401 || response.status === 403) {
          sds200WaterfallIngressSession.invalidate();
          throw new WaterfallCardError(
            "Home Assistant App authentication expired.",
          );
        }
        throw new WaterfallCardError("Waterfall stream is unavailable.");
      }
      const mediaType = (response.headers.get("content-type") || "")
        .split(";", 1)[0]
        .trim()
        .toLowerCase();
      if (![SDS200_WATERFALL_SSE_MEDIA_TYPE, SDS200_WATERFALL_NDJSON_MEDIA_TYPE]
        .includes(mediaType)) {
        throw new WaterfallCardError(
          "Waterfall stream returned an unsupported format.",
        );
      }
      if (generation !== this._generation) {
        return;
      }
      this._streaming = true;
      this._retryIndex = 0;
      this._setStatus(
        this._paused ? "paused" : "live",
        this._paused
          ? "Display paused; live data continues to be consumed."
          : "Live relative waterfall data.",
      );
      const reader = response.body.getReader();
      this._reader = reader;
      await this._consume(reader, generation, mediaType);
    } catch (error) {
      if (generation !== this._generation || controller.signal.aborted) {
        return;
      }
      this._resetLiveState({clearHistory: true});
      const message = error instanceof WaterfallCardError
        ? error.message
        : "Waterfall data was rejected.";
      this._setStatus("error", `${message} Reconnecting…`);
      this._scheduleReconnect(generation);
    } finally {
      if (this._releaseAuthentication === releaseAuthentication) {
        this._releaseAuthentication = null;
      }
      if (releaseAuthentication !== null) {
        releaseAuthentication();
      }
      if (generation === this._generation) {
        this._reader = null;
        this._controller = null;
        this._streaming = false;
      }
    }
  }

  _payloadLine(line, mediaType) {
    if (mediaType === SDS200_WATERFALL_NDJSON_MEDIA_TYPE) {
      if (line.length === 0) {
        throw new WaterfallCardError(
          "Waterfall record size is invalid.",
        );
      }
      return line;
    }
    if (mediaType !== SDS200_WATERFALL_SSE_MEDIA_TYPE) {
      throw new WaterfallCardError(
        "Waterfall stream returned an unsupported format.",
      );
    }
    if (line.length === 0 || line.startsWith(":")) {
      return null;
    }
    if (line.startsWith("id:")) {
      return null;
    }
    if (!line.startsWith("data:")) {
      throw new WaterfallCardError(
        "Waterfall event field is unsupported.",
      );
    }
    return line.slice(5).trimStart();
  }

  async _consume(reader, generation, mediaType) {
    const decoder = new TextDecoder("utf-8", {fatal: true});
    let pending = "";
    while (generation === this._generation) {
      const result = await reader.read();
      if (result.done) {
        pending += decoder.decode();
        if (pending.length !== 0) {
          throw new WaterfallCardError(
            "Waterfall stream ended with an incomplete record.",
          );
        }
        throw new WaterfallCardError("Waterfall stream ended.");
      }
      pending += decoder.decode(result.value, {stream: true});
      if (
        pending.length > SDS200_WATERFALL_MAX_LINE_CHARACTERS &&
        !pending.includes("\n")
      ) {
        throw new WaterfallCardError(
          "Waterfall record exceeds the size limit.",
        );
      }
      let newline = pending.indexOf("\n");
      while (newline >= 0) {
        const line = pending.slice(0, newline);
        pending = pending.slice(newline + 1);
        if (line.length > SDS200_WATERFALL_MAX_LINE_CHARACTERS) {
          throw new WaterfallCardError(
            "Waterfall record size is invalid.",
          );
        }
        const payload = this._payloadLine(line, mediaType);
        if (payload !== null) {
          if (
            payload.length === 0 ||
            payload.length > SDS200_WATERFALL_MAX_LINE_CHARACTERS
          ) {
            throw new WaterfallCardError(
              "Waterfall record size is invalid.",
            );
          }
          this._applyRecord(JSON.parse(payload));
        }
        newline = pending.indexOf("\n");
      }
    }
  }

  _validatedRecord(value) {
    if (waterfallRecordObject(value) !== value) {
      throw new Error("Waterfall record is not an object.");
    }
    if (
      value.protocol !== SDS200_WATERFALL_PROTOCOL ||
      value.version !== SDS200_WATERFALL_VERSION
    ) {
      throw new Error("Waterfall protocol is unsupported.");
    }
    if (!Number.isSafeInteger(value.sequence) || value.sequence <= 0) {
      throw new Error("Waterfall sequence is invalid.");
    }
    if (
      typeof value.observed_at !== "string" ||
      !Number.isFinite(Date.parse(value.observed_at)) ||
      typeof value.kind !== "string" ||
      waterfallRecordObject(value.payload) !== value.payload
    ) {
      throw new Error("Waterfall record shape is invalid.");
    }
    if (this._lastSequence === null) {
      if (value.kind !== "session.checkpoint") {
        throw new Error(
          "Waterfall stream did not begin with a checkpoint.",
        );
      }
    } else if (value.sequence !== this._lastSequence + 1) {
      throw new Error("Waterfall record sequence is not contiguous.");
    }
    this._lastSequence = value.sequence;
    return value;
  }

  _applySnapshot(snapshot) {
    if (waterfallRecordObject(snapshot) !== snapshot) {
      throw new Error("Waterfall session snapshot is invalid.");
    }
    waterfallNonnegativeInteger(
      snapshot.gwf_poll_failures,
      "poll failures",
    );
    this._checkpoint = snapshot;
    this._renderTelemetry();
    this._renderScale();
    this._renderPointer();
  }

  _applyRecord(value) {
    const record = this._validatedRecord(value);
    if (record.kind === "session.checkpoint") {
      this._applySnapshot(record.payload);
      return;
    }
    if (record.kind === "session.transition") {
      this._applySnapshot(
        waterfallRecordObject(record.payload).snapshot,
      );
      this._transitions += 1;
      return;
    }
    if (record.kind === "waterfall.pwf") {
      return;
    }
    if (record.kind !== "waterfall.gwf") {
      throw new Error("Waterfall record kind is unsupported.");
    }

    const payload = waterfallRecordObject(record.payload);
    if (payload.session !== undefined) {
      this._applySnapshot(waterfallRecordObject(payload.session));
    }
    const frame = normalizeWaterfallFrame(payload.values);
    this._queueLoss = waterfallNonnegativeInteger(
      payload.responses_dropped,
      "queue loss",
    );
    this._overflows = waterfallNonnegativeInteger(
      payload.overflows,
      "overflow count",
    );
    this._lastFrameAt = Date.parse(payload.source_received_at);
    if (!Number.isFinite(this._lastFrameAt)) {
      throw new Error("Waterfall source timestamp is invalid.");
    }
    const now = performance.now();
    this._frameTimes.push(now);
    this._frameTimes = this._frameTimes.filter(
      (time) => now - time <= 5000,
    );
    if (!this._paused) {
      this._latestFrame = frame;
      this._history.push({values: frame, receivedAt: now});
      this._history = pruneWaterfallHistory(
        this._history,
        this._historyPolicy,
        now,
      );
      this._schedulePaint();
    }
    this._renderTelemetry();
  }

  _setStatus(state, message) {
    this._status.dataset.state = state;
    this._status.textContent = message;
  }

  _render() {
    this._card.dataset.density = this._config.density;
    this._card.dataset.palette = this._config.palette;
    applyWaterfallSystemPalette(this._card, this._config.palette);
    this._title.textContent = this._config.title;
    this._scale.hidden = !this._config.show_scale;
    this._telemetry.hidden = !this._config.show_telemetry;
    this._spectrum.tabIndex = this._config.show_pointer ? 0 : -1;
    this._historyCanvas.tabIndex = this._config.show_pointer ? 0 : -1;
    this._spectrum.dataset.pointerEnabled = String(this._config.show_pointer);
    this._historyCanvas.dataset.pointerEnabled = String(
      this._config.show_pointer,
    );
    this._pauseButton.textContent = this._paused ? "Resume" : "Pause";
    this._pauseButton.setAttribute("aria-pressed", String(this._paused));
    if (this._streaming) {
      this._setStatus(
        this._paused ? "paused" : "live",
        this._paused
          ? "Display paused; live data continues to be consumed."
          : "Live relative waterfall data.",
      );
    }
    this._renderPointer();
    this._renderScale();
    this._renderTelemetry();
  }

  _renderScale() {
    const frequencies = validWaterfallFrequencies(
      waterfallRecordObject(this._checkpoint).waterfall_status,
    );
    const values = frequencies === null
      ? ["Unavailable", "Unavailable", "Unavailable"]
      : frequencies.fields.slice(0, 3);
    this._scaleLower.textContent = values[0];
    this._scaleCenter.textContent = values[1];
    this._scaleUpper.textContent = values[2];
  }

  _renderTelemetry() {
    const snapshot = waterfallRecordObject(this._checkpoint);
    this._telemetryValues.session.textContent =
      typeof snapshot.state === "string" ? snapshot.state : "Idle";
    const now = performance.now();
    if (this._paused === false && this._historyPolicy?.mode === "duration") {
      const before = this._history.length;
      this._history = pruneWaterfallHistory(
        this._history,
        this._historyPolicy,
        now,
      );
      if (this._history.length !== before || this._history.length !== 0) {
        this._schedulePaint();
      }
    }
    this._frameTimes = this._frameTimes.filter(
      (time) => now - time <= 5000,
    );
    const duration = this._frameTimes.length > 1
      ? (this._frameTimes.at(-1) - this._frameTimes[0]) / 1000
      : 0;
    const rate = duration > 0
      ? (this._frameTimes.length - 1) / duration
      : 0;
    this._telemetryValues.rate.textContent = `${rate.toFixed(1)} fps`;
    this._telemetryValues.age.textContent = this._lastFrameAt === null
      ? "Unavailable"
      : `${Math.max(0, (Date.now() - this._lastFrameAt) / 1000).toFixed(1)} s`;
    this._telemetryValues.loss.textContent =
      `${this._queueLoss} / ${this._overflows}`;
    this._telemetryValues.sequence.textContent =
      this._lastSequence === null ? "Unavailable" : String(this._lastSequence);
  }

  _schedulePaint() {
    if (this._paintRequest !== null) {
      return;
    }
    this._paintRequest = window.requestAnimationFrame(() => {
      this._paintRequest = null;
      this._paint();
    });
  }

  _palette() {
    const presets = {
      cyan: {
        background: "#07111c",
        grid: "#31516a",
        spectrum: "#42d7ff",
        marker: "#ffcf4a",
        history: "#42d7ff",
        pointer: "#ffffff",
      },
      green: {
        background: "#031108",
        grid: "#205b32",
        spectrum: "#66ff8a",
        marker: "#f6ff73",
        history: "#37e56d",
        pointer: "#ffffff",
      },
      amber: {
        background: "#160e02",
        grid: "#6b4b1d",
        spectrum: "#ffbf47",
        marker: "#fff176",
        history: "#ff9f1a",
        pointer: "#ffffff",
      },
      monochrome: {
        background: "#080808",
        grid: "#454545",
        spectrum: "#f2f2f2",
        marker: "#ffffff",
        history: "#d0d0d0",
        pointer: "#ffffff",
      },
    };
    const systemColors = SDS200_WATERFALL_SYSTEM_PALETTES[this._config.palette];
    if (systemColors !== undefined) {
      return {
        background: systemColors[0],
        grid: systemColors[5],
        spectrum: systemColors[6],
        marker: systemColors[11],
        history: systemColors[7],
        pointer: systemColors[3],
      };
    }
    if (this._config.palette !== "theme") {
      return presets[this._config.palette];
    }
    const styles = getComputedStyle(this);
    return {
      background:
        styles.getPropertyValue("--card-background-color").trim() || "#07111c",
      grid:
        styles.getPropertyValue("--divider-color").trim() || "#31516a",
      spectrum:
        styles.getPropertyValue("--primary-color").trim() || "#42d7ff",
      marker:
        styles.getPropertyValue("--accent-color").trim() || "#ffcf4a",
      history:
        styles.getPropertyValue("--primary-color").trim() || "#42d7ff",
      pointer:
        styles.getPropertyValue("--primary-text-color").trim() || "#ffffff",
    };
  }

  _canvasSize(canvas) {
    const ratio = Math.min(
      2,
      Math.max(1, Number(window.devicePixelRatio) || 1),
    );
    const width = Math.min(
      2048,
      Math.max(1, Math.round(canvas.clientWidth * ratio)),
    );
    const height = Math.min(
      1024,
      Math.max(1, Math.round(canvas.clientHeight * ratio)),
    );
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    return {width, height, ratio};
  }

  _paint() {
    if (
      this._spectrum.clientWidth === 0 ||
      this._historyCanvas.clientWidth === 0
    ) {
      return;
    }
    const palette = this._palette();
    const spectrumSize = this._canvasSize(this._spectrum);
    const spectrumContext = this._spectrum.getContext("2d");
    spectrumContext.fillStyle = palette.background;
    spectrumContext.fillRect(0, 0, spectrumSize.width, spectrumSize.height);
    spectrumContext.strokeStyle = palette.grid;
    spectrumContext.lineWidth = spectrumSize.ratio;
    for (let index = 1; index < 4; index += 1) {
      const y = spectrumSize.height * index / 4;
      spectrumContext.beginPath();
      spectrumContext.moveTo(0, y);
      spectrumContext.lineTo(spectrumSize.width, y);
      spectrumContext.stroke();
    }
    if (this._latestFrame !== null) {
      spectrumContext.strokeStyle = palette.spectrum;
      spectrumContext.lineWidth = 2 * spectrumSize.ratio;
      spectrumContext.beginPath();
      this._latestFrame.forEach((value, index) => {
        const x = index * spectrumSize.width /
          (SDS200_WATERFALL_BIN_COUNT - 1);
        const y = spectrumSize.height - value *
          (spectrumSize.height - 2 * spectrumSize.ratio);
        if (index === 0) {
          spectrumContext.moveTo(x, y);
        } else {
          spectrumContext.lineTo(x, y);
        }
      });
      spectrumContext.stroke();
    }

    const historySize = this._canvasSize(this._historyCanvas);
    const historyContext = this._historyCanvas.getContext("2d");
    historyContext.fillStyle = palette.background;
    historyContext.fillRect(0, 0, historySize.width, historySize.height);
    if (this._history.length !== 0) {
      const historyNow = this._paused
        ? (this._history.at(-1)?.receivedAt ?? performance.now())
        : performance.now();
      this._history = pruneWaterfallHistory(
        this._history,
        this._historyPolicy,
        historyNow,
      );
      historyContext.fillStyle = palette.history;
      for (const row of waterfallHistoryRows(
        this._history,
        this._historyPolicy,
        historySize.height,
        historyNow,
      )) {
        row.entry.values.forEach((value, binIndex) => {
          historyContext.globalAlpha = 0.08 + value * 0.92;
          const x1 = binIndex * historySize.width /
            SDS200_WATERFALL_BIN_COUNT;
          const x2 = (binIndex + 1) * historySize.width /
            SDS200_WATERFALL_BIN_COUNT;
          historyContext.fillRect(
            x1,
            row.y,
            Math.max(1, x2 - x1),
            row.height,
          );
        });
      }
      historyContext.globalAlpha = 1;
    }

    const frequencies = validWaterfallFrequencies(
      waterfallRecordObject(this._checkpoint).waterfall_status,
    );
    if (frequencies !== null) {
      for (const [canvas, size] of [
        [this._spectrum, spectrumSize],
        [this._historyCanvas, historySize],
      ]) {
        const context = canvas.getContext("2d");
        const x = frequencies.markerPosition * size.width /
          (SDS200_WATERFALL_BIN_COUNT - 1);
        context.strokeStyle = palette.marker;
        context.lineWidth = size.ratio;
        context.beginPath();
        context.moveTo(x, 0);
        context.lineTo(x, size.height);
        context.stroke();
      }
    }
    const pointer = this._config.show_pointer
      ? waterfallPointerFrequency(
        waterfallRecordObject(this._checkpoint).waterfall_status,
        this._pointerRatio,
      )
      : null;
    if (pointer !== null) {
      for (const [canvas, size] of [
        [this._spectrum, spectrumSize],
        [this._historyCanvas, historySize],
      ]) {
        const context = canvas.getContext("2d");
        const x = pointer.ratio * size.width;
        context.save();
        context.strokeStyle = palette.pointer;
        context.lineWidth = 2 * size.ratio;
        context.setLineDash([4 * size.ratio, 3 * size.ratio]);
        context.beginPath();
        context.moveTo(x, 0);
        context.lineTo(x, size.height);
        context.stroke();
        context.restore();
      }
    }
  }
}

window.customCards = window.customCards || [];
if (!window.customCards.some(
  (card) => card.type === SDS200_WATERFALL_CARD_TYPE,
)) {
  window.customCards.push({
    type: SDS200_WATERFALL_CARD_TYPE,
    name: "SDS200 Waterfall",
    description: "Authenticated responsive relative waterfall from the sds200 App.",
    preview: true,
    documentationURL:
      "https://github.com/stevenboyd78/sdsctl/blob/main/docs/home-assistant-app.md",
  });
}

if (!customElements.get(SDS200_WATERFALL_CARD_TAG)) {
  customElements.define(
    SDS200_WATERFALL_CARD_TAG,
    Sds200WaterfallCard,
  );
}
