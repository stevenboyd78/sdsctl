"use strict";

const FALLBACK_REFRESH_INTERVAL_MS = 2000;
const RECONCILE_INTERVAL_MS = 30000;
const RECORDING_REFRESH_INTERVAL_MS = 1000;
const PCMU_HEADER_BYTES = 82;
const PCMU_MAX_FRAME_BYTES = 128 * 1024;
const PCMU_VERSION = 1;
const PCMU_KNOWN_FLAGS = 0x0f;
const PCMU_TIMESTAMP_BACKWARDS = 1 << 3;
const PCMU_EXPECTED_SEQUENCE = 1 << 1;
const PCMU_EXPECTED_TIMESTAMP = 1 << 2;
const MAX_GAP_SAMPLES = 8000;
const PCMU_SAMPLE_RATE = 8000;
const AUDIO_FALLBACK_BUFFER_CAPACITY_SAMPLES = 16000;
const AUDIO_FALLBACK_START_THRESHOLD_SAMPLES = 480;
const AUDIO_FALLBACK_SCRIPT_BUFFER_SIZE = 1024;
const WORKSPACE_PANE_STORAGE_KEY = "sdsctl.web.pane";
const RADIO_SCAN_FALLBACK_STORAGE_KEY = "sdsctl.web.scan-fallback";
const RECORDINGS_PAGE_SIZE = 3;
const WATERFALL_PROTOCOL = "sdsctl.waterfall";
const WATERFALL_VERSION = 1;
const WATERFALL_NDJSON_MEDIA_TYPE = "application/x-ndjson";
const WATERFALL_SSE_MEDIA_TYPE = "text/event-stream";
const WATERFALL_BIN_COUNT = 240;
const WATERFALL_MAX_LINE_CHARACTERS = 64 * 1024;
const WATERFALL_HISTORY_CAPACITY = 240;
const WATERFALL_RECONNECT_DELAY_MS = 2000;
const WORKSPACE_PANES = Object.freeze([
  "scanner",
  "controls",
  "waterfall",
  "audio",
  "recordings",
  "diagnostics",
]);
const RADIO_INSPECTION_VIEWS = Object.freeze([
  "auto",
  "hierarchy",
  "rf",
  "identity",
  "special",
]);
const RADIO_FIELD_GROUPS = Object.freeze([
  "hierarchy",
  "rf",
  "identity",
  "special",
]);

let currentSnapshot = {};
let currentDaemonHello = {};
let eventSource = null;
let eventStreamRestartTimer = null;
let eventStreamConnected = false;
let lastEventSequence = null;
let daemonEventGeneration = 0;
let refreshInProgress = false;
let currentRecording = {};
let recordingStatusAvailable = false;
let recordingRefreshInProgress = false;
let recordingsRefreshInProgress = false;
let recordingMutationInProgress = false;
let scannerControlMutationInProgress = false;
let activeWorkspacePane = "scanner";
let radioScanFallback = "detail";
let radioInspectionView = "auto";
let currentScreenKind = "unknown";
let recordingEntries = [];
let recordingTotalEntries = 0;
let recordingPageIndex = 0;
let recordingInventorySignature = "";
let recordingPaginationFocusId = null;

let waterfallGeneration = 0;
let waterfallAbortController = null;
let waterfallReader = null;
let waterfallReconnectTimer = null;
let waterfallConnected = false;
let waterfallPaused = false;
let waterfallLastSequence = null;
let waterfallLastFrameAt = null;
let waterfallFrameTimes = [];
let waterfallHistory = [];
let waterfallLatestFrame = null;
let waterfallCheckpoint = {};
let waterfallQueueLoss = 0;
let waterfallOverflows = 0;
let waterfallTransitions = 0;

let audioPlaybackGeneration = 0;
let audioPlaybackActive = false;
let audioAbortController = null;
const dashboardScriptUrl = document.currentScript?.src;
if (!dashboardScriptUrl) {
  throw new Error("Dashboard script URL is unavailable.");
}
const webRootUrl = new URL("../", dashboardScriptUrl);

function webUrl(path) {
  return new URL(path, webRootUrl).toString();
}

let audioReader = null;
let audioContext = null;
let audioWorkletNode = null;
let audioScriptProcessor = null;
let audioLastStreamSequence = null;
let audioLastPacketsDropped = null;
let audioLastPayloadBytesDropped = null;
let audioLastOverflows = null;
let audioPacketsReceived = 0;
let audioRtpMissingPackets = 0;
let audioLastTelemetryUpdate = 0;

function element(id) {
  const node = document.getElementById(id);
  if (node === null) {
    throw new Error(`Dashboard element not found: ${id}`);
  }
  return node;
}

function readStoredValue(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStoredValue(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage may be unavailable in privacy modes or embedded WebViews.
  }
}

function normalizedWorkspacePane(value) {
  return typeof value === "string" && WORKSPACE_PANES.includes(value)
    ? value
    : "scanner";
}

function activateWorkspacePane(value, {focus = false, persist = true} = {}) {
  const pane = normalizedWorkspacePane(value);
  activeWorkspacePane = pane;

  for (const tab of document.querySelectorAll("[data-workspace-tab]")) {
    const selected = tab.dataset.workspaceTab === pane;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
  for (const panel of document.querySelectorAll(
    ".workspace-pane[data-workspace-pane]",
  )) {
    panel.hidden = panel.dataset.workspacePane !== pane;
  }

  document.documentElement.dataset.workspacePane = pane;
  if (persist) {
    writeStoredValue(WORKSPACE_PANE_STORAGE_KEY, pane);
  }
  if (focus) {
    element(`pane-tab-${pane}`).focus();
  }
  reconcileWaterfallDemand();
}

function initializeWorkspace() {
  const tabs = Array.from(
    document.querySelectorAll("[data-workspace-tab]"),
  );
  activateWorkspacePane(readStoredValue(WORKSPACE_PANE_STORAGE_KEY), {
    persist: false,
  });

  for (const tab of tabs) {
    tab.addEventListener("click", () => {
      activateWorkspacePane(tab.dataset.workspaceTab);
    });
    tab.addEventListener("keydown", (event) => {
      const currentIndex = tabs.indexOf(tab);
      let nextIndex = currentIndex;
      if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
      } else if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        nextIndex = (currentIndex + 1) % tabs.length;
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = tabs.length - 1;
      } else {
        return;
      }

      event.preventDefault();
      activateWorkspacePane(tabs[nextIndex].dataset.workspaceTab, {
        focus: true,
      });
    });
  }
}

function initializeThemeControl() {
  const select = element("theme-select");
  const controller = window.sdsctlTheme;

  if (
    controller === undefined ||
    typeof controller.current !== "function" ||
    typeof controller.select !== "function"
  ) {
    select.disabled = true;
    return;
  }

  select.value = controller.current();
  select.addEventListener("change", () => {
    controller.select(select.value);
    select.value = controller.current();
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(renderWaterfallCanvases);
    });
  });
}

function waterfallDemanded() {
  return activeWorkspacePane === "waterfall" && !document.hidden;
}

function setWaterfallStatus(state, message) {
  const status = element("waterfall-status");
  status.dataset.state = state;
  status.textContent = message;
}

function clearWaterfallReconnectTimer() {
  if (waterfallReconnectTimer !== null) {
    window.clearTimeout(waterfallReconnectTimer);
    waterfallReconnectTimer = null;
  }
}

function resetWaterfallLiveState({clearHistory = true} = {}) {
  waterfallConnected = false;
  waterfallLastSequence = null;
  waterfallLastFrameAt = null;
  waterfallFrameTimes = [];
  waterfallLatestFrame = null;
  waterfallCheckpoint = {};
  waterfallQueueLoss = 0;
  waterfallOverflows = 0;
  waterfallTransitions = 0;
  if (clearHistory) {
    waterfallHistory = [];
  }
  element("waterfall-session-state").textContent = "Idle";
  for (const name of ["lower", "center", "upper", "marker"]) {
    element(`waterfall-frequency-${name}`).textContent = "Unavailable";
  }
  element("waterfall-frame-rate").textContent = "0.0 fps";
  element("waterfall-frame-age").textContent = "Unavailable";
  element("waterfall-sequence").textContent = "Unavailable";
  element("waterfall-queue-loss").textContent = "0 records";
  element("waterfall-overflows").textContent = "0";
  element("waterfall-poll-failures").textContent = "0";
  element("waterfall-transitions").textContent = "0";
  element("waterfall-raw-values").textContent = "Unavailable";
  renderWaterfallCanvases();
}

function stopWaterfallStream({clearHistory = true, status = "Idle"} = {}) {
  waterfallGeneration += 1;
  clearWaterfallReconnectTimer();
  const controller = waterfallAbortController;
  waterfallAbortController = null;
  if (controller !== null) {
    controller.abort();
  }
  const reader = waterfallReader;
  waterfallReader = null;
  if (reader !== null) {
    void reader.cancel().catch(() => {});
  }
  resetWaterfallLiveState({clearHistory});
  setWaterfallStatus("idle", status);
}

function scheduleWaterfallReconnect(generation) {
  if (!waterfallDemanded() || generation !== waterfallGeneration) {
    return;
  }
  clearWaterfallReconnectTimer();
  waterfallReconnectTimer = window.setTimeout(() => {
    waterfallReconnectTimer = null;
    if (waterfallDemanded() && generation === waterfallGeneration) {
      void startWaterfallStream();
    }
  }, WATERFALL_RECONNECT_DELAY_MS);
}

function nonnegativeInteger(value, name) {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`Waterfall ${name} is invalid.`);
  }
  return value;
}

function waterfallRecord(value) {
  if (record(value) !== value) {
    throw new Error("Waterfall record is not an object.");
  }
  if (value.protocol !== WATERFALL_PROTOCOL || value.version !== WATERFALL_VERSION) {
    throw new Error("Waterfall protocol is unsupported.");
  }
  if (!Number.isSafeInteger(value.sequence) || value.sequence <= 0) {
    throw new Error("Waterfall sequence is invalid.");
  }
  if (typeof value.observed_at !== "string" || !Number.isFinite(Date.parse(value.observed_at))) {
    throw new Error("Waterfall timestamp is invalid.");
  }
  if (typeof value.kind !== "string" || record(value.payload) !== value.payload) {
    throw new Error("Waterfall record shape is invalid.");
  }
  if (waterfallLastSequence === null) {
    if (value.kind !== "session.checkpoint") {
      throw new Error("Waterfall stream did not begin with a checkpoint.");
    }
  } else if (value.sequence !== waterfallLastSequence + 1) {
    throw new Error("Waterfall record sequence is not contiguous.");
  }
  waterfallLastSequence = value.sequence;
  return value;
}

function validFrequencyMetadata(status) {
  const fields = [
    status.lower_frequency,
    status.center_frequency,
    status.upper_frequency,
    status.marker_frequency,
  ];
  if (!fields.every((value) => typeof value === "string" && /^\d+(?:\.\d+)?$/.test(value))) {
    return null;
  }
  const numbers = fields.map(Number);
  const markerPosition = Number(status.marker_position);
  if (
    !numbers.every(Number.isFinite) ||
    !(numbers[0] < numbers[1] && numbers[1] < numbers[2]) ||
    numbers[3] < numbers[0] ||
    numbers[3] > numbers[2] ||
    !Number.isInteger(markerPosition) ||
    markerPosition < 0 ||
    markerPosition >= WATERFALL_BIN_COUNT
  ) {
    return null;
  }
  return {fields, markerPosition};
}

function applyWaterfallSnapshot(snapshot) {
  if (record(snapshot) !== snapshot) {
    throw new Error("Waterfall session snapshot is invalid.");
  }
  waterfallCheckpoint = snapshot;
  const state = typeof snapshot.state === "string" ? snapshot.state : "Unavailable";
  element("waterfall-session-state").textContent = state;
  const failures = nonnegativeInteger(snapshot.gwf_poll_failures, "poll failures");
  element("waterfall-poll-failures").textContent = String(failures);
  const status = record(snapshot.waterfall_status);
  const frequencies = validFrequencyMetadata(status);
  const frequencyValues = frequencies === null
    ? ["Unavailable", "Unavailable", "Unavailable", "Unavailable"]
    : frequencies.fields;
  for (const [index, name] of ["lower", "center", "upper", "marker"].entries()) {
    element(`waterfall-frequency-${name}`).textContent = frequencyValues[index];
  }
}

function normalizeWaterfallValues(values) {
  if (!Array.isArray(values) || values.length !== WATERFALL_BIN_COUNT) {
    throw new Error("Waterfall frame must contain exactly 240 values.");
  }
  const raw = values.map((value) => {
    if (typeof value !== "string") {
      throw new Error("Waterfall values must remain source strings.");
    }
    return value;
  });
  const numeric = raw.map((value) => (
    /^[0-9a-f]+$/i.test(value) ? Number.parseInt(value, 16) : Number.NaN
  ));
  if (!numeric.every(Number.isSafeInteger)) {
    throw new Error("Waterfall frame contains a non-hexadecimal value.");
  }
  const minimum = Math.min(...numeric);
  const maximum = Math.max(...numeric);
  const span = maximum - minimum;
  return {
    raw,
    normalized: numeric.map((value) => span === 0 ? 0.5 : (value - minimum) / span),
  };
}

function applyWaterfallRecord(value) {
  const envelope = waterfallRecord(value);
  element("waterfall-sequence").textContent = String(envelope.sequence);
  if (envelope.kind === "session.checkpoint") {
    applyWaterfallSnapshot(envelope.payload);
    return;
  }
  if (envelope.kind === "session.transition") {
    const snapshot = record(envelope.payload.snapshot);
    applyWaterfallSnapshot(snapshot);
    waterfallTransitions += 1;
    element("waterfall-transitions").textContent = String(waterfallTransitions);
    return;
  }
  if (envelope.kind === "waterfall.pwf") {
    return;
  }
  if (envelope.kind !== "waterfall.gwf") {
    throw new Error("Waterfall record kind is unsupported.");
  }

  const frame = normalizeWaterfallValues(envelope.payload.values);
  waterfallQueueLoss = nonnegativeInteger(envelope.payload.responses_dropped, "queue loss");
  waterfallOverflows = nonnegativeInteger(envelope.payload.overflows, "overflow count");
  waterfallLastFrameAt = Date.parse(envelope.payload.source_received_at);
  if (!Number.isFinite(waterfallLastFrameAt)) {
    throw new Error("Waterfall source timestamp is invalid.");
  }
  const now = performance.now();
  waterfallFrameTimes.push(now);
  waterfallFrameTimes = waterfallFrameTimes.filter((time) => now - time <= 5000);
  const duration = waterfallFrameTimes.length > 1
    ? (waterfallFrameTimes.at(-1) - waterfallFrameTimes[0]) / 1000
    : 0;
  const rate = duration > 0 ? (waterfallFrameTimes.length - 1) / duration : 0;
  element("waterfall-frame-rate").textContent = `${rate.toFixed(1)} fps`;
  element("waterfall-queue-loss").textContent = `${waterfallQueueLoss} records`;
  element("waterfall-overflows").textContent = String(waterfallOverflows);
  element("waterfall-raw-values").textContent = frame.raw.join(",");
  updateWaterfallFrameAge();

  if (!waterfallPaused) {
    waterfallLatestFrame = frame.normalized;
    waterfallHistory.push(frame.normalized);
    if (waterfallHistory.length > WATERFALL_HISTORY_CAPACITY) {
      waterfallHistory.shift();
    }
    renderWaterfallCanvases();
  }
}

function waterfallPayloadLine(line, mediaType) {
  if (mediaType === WATERFALL_NDJSON_MEDIA_TYPE) {
    if (line.length === 0) {
      throw new Error("Waterfall record size is invalid.");
    }
    return line;
  }
  if (mediaType !== WATERFALL_SSE_MEDIA_TYPE) {
    throw new Error("Waterfall response media type is unsupported.");
  }
  if (line.length === 0 || line.startsWith(":")) {
    return null;
  }
  if (line.startsWith("id:")) {
    return null;
  }
  if (!line.startsWith("data:")) {
    throw new Error("Waterfall event field is unsupported.");
  }
  return line.slice(5).trimStart();
}

async function consumeWaterfallBody(reader, generation, mediaType) {
  const decoder = new TextDecoder("utf-8", {fatal: true});
  let pending = "";
  while (generation === waterfallGeneration) {
    const result = await reader.read();
    if (result.done) {
      pending += decoder.decode();
      if (pending.length !== 0) {
        throw new Error("Waterfall stream ended with an incomplete record.");
      }
      throw new Error("Waterfall stream ended.");
    }
    pending += decoder.decode(result.value, {stream: true});
    if (pending.length > WATERFALL_MAX_LINE_CHARACTERS && !pending.includes("\n")) {
      throw new Error("Waterfall record exceeds the browser size limit.");
    }
    let newline = pending.indexOf("\n");
    while (newline >= 0) {
      const line = pending.slice(0, newline);
      pending = pending.slice(newline + 1);
      if (line.length > WATERFALL_MAX_LINE_CHARACTERS) {
        throw new Error("Waterfall record size is invalid.");
      }
      const payload = waterfallPayloadLine(line, mediaType);
      if (payload !== null) {
        if (payload.length === 0 || payload.length > WATERFALL_MAX_LINE_CHARACTERS) {
          throw new Error("Waterfall record size is invalid.");
        }
        applyWaterfallRecord(JSON.parse(payload));
      }
      newline = pending.indexOf("\n");
    }
  }
}

async function startWaterfallStream() {
  if (!waterfallDemanded()) {
    return;
  }
  stopWaterfallStream({status: "Connecting to the waterfall stream…"});
  const generation = waterfallGeneration;
  const controller = new AbortController();
  waterfallAbortController = controller;
  try {
    const response = await fetch(webUrl("api/v1/waterfall"), {
      headers: {Accept: WATERFALL_SSE_MEDIA_TYPE},
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal,
    });
    if (!response.ok || response.body === null) {
      throw new Error(response.ok ? "Waterfall response omitted its body." : `Waterfall stream unavailable (${response.status}).`);
    }
    if (generation !== waterfallGeneration) {
      return;
    }
    const mediaType = (response.headers.get("content-type") || "")
      .split(";", 1)[0]
      .trim()
      .toLowerCase();
    if (![WATERFALL_SSE_MEDIA_TYPE, WATERFALL_NDJSON_MEDIA_TYPE].includes(mediaType)) {
      throw new Error("Waterfall response media type is unsupported.");
    }
    waterfallConnected = true;
    setWaterfallStatus(
      waterfallPaused ? "paused" : "live",
      waterfallPaused
        ? "Display paused; scanner data continues to be consumed."
        : "Live relative waterfall data.",
    );
    const reader = response.body.getReader();
    waterfallReader = reader;
    await consumeWaterfallBody(reader, generation, mediaType);
  } catch (error) {
    if (generation !== waterfallGeneration || controller.signal.aborted) {
      return;
    }
    resetWaterfallLiveState();
    const message = error instanceof Error ? error.message : "Waterfall stream failed.";
    setWaterfallStatus("error", `${message} Reconnecting…`);
    scheduleWaterfallReconnect(generation);
  } finally {
    if (generation === waterfallGeneration) {
      waterfallReader = null;
      waterfallAbortController = null;
      waterfallConnected = false;
    }
  }
}

function reconcileWaterfallDemand() {
  if (typeof document === "undefined" || document.getElementById("waterfall-status") === null) {
    return;
  }
  if (waterfallDemanded()) {
    if (!waterfallConnected && waterfallAbortController === null && waterfallReconnectTimer === null) {
      void startWaterfallStream();
    }
  } else if (waterfallConnected || waterfallAbortController !== null || waterfallReconnectTimer !== null) {
    stopWaterfallStream({status: "Open this pane to start the waterfall stream."});
  }
}

function waterfallPalette() {
  const styles = getComputedStyle(element("waterfall-panel"));
  return {
    background: styles.getPropertyValue("--waterfall-background").trim() || "#07111c",
    grid: styles.getPropertyValue("--waterfall-grid").trim() || "#31516a",
    spectrum: styles.getPropertyValue("--waterfall-spectrum").trim() || "#42d7ff",
    marker: styles.getPropertyValue("--waterfall-marker").trim() || "#ffcf4a",
    history: styles.getPropertyValue("--waterfall-history").trim() || "#42d7ff",
  };
}

function resizeCanvas(canvas) {
  const ratio = Math.max(1, window.devicePixelRatio || 1);
  const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
  const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  return {width, height, ratio};
}

function renderWaterfallCanvases() {
  const spectrum = element("waterfall-spectrum");
  const history = element("waterfall-history");
  if (spectrum.clientWidth === 0 || history.clientWidth === 0) {
    return;
  }
  const palette = waterfallPalette();
  const spectrumSize = resizeCanvas(spectrum);
  const spectrumContext = spectrum.getContext("2d");
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
  if (waterfallLatestFrame !== null) {
    spectrumContext.strokeStyle = palette.spectrum;
    spectrumContext.lineWidth = 2 * spectrumSize.ratio;
    spectrumContext.beginPath();
    waterfallLatestFrame.forEach((value, index) => {
      const x = index * spectrumSize.width / (WATERFALL_BIN_COUNT - 1);
      const y = spectrumSize.height - value * (spectrumSize.height - 2 * spectrumSize.ratio);
      if (index === 0) {
        spectrumContext.moveTo(x, y);
      } else {
        spectrumContext.lineTo(x, y);
      }
    });
    spectrumContext.stroke();
  }

  const historySize = resizeCanvas(history);
  const historyContext = history.getContext("2d");
  historyContext.fillStyle = palette.background;
  historyContext.fillRect(0, 0, historySize.width, historySize.height);
  if (waterfallHistory.length !== 0) {
    const rowHeight = historySize.height / WATERFALL_HISTORY_CAPACITY;
    const startRow = WATERFALL_HISTORY_CAPACITY - waterfallHistory.length;
    historyContext.fillStyle = palette.history;
    waterfallHistory.forEach((frame, rowIndex) => {
      const y = (startRow + rowIndex) * rowHeight;
      frame.forEach((value, binIndex) => {
        historyContext.globalAlpha = 0.08 + value * 0.92;
        const x1 = binIndex * historySize.width / WATERFALL_BIN_COUNT;
        const x2 = (binIndex + 1) * historySize.width / WATERFALL_BIN_COUNT;
        historyContext.fillRect(x1, y, Math.max(1, x2 - x1), Math.max(1, rowHeight));
      });
    });
    historyContext.globalAlpha = 1;
  }
  const frequencies = validFrequencyMetadata(record(waterfallCheckpoint.waterfall_status));
  if (frequencies !== null) {
    for (const [canvas, size] of [[spectrum, spectrumSize], [history, historySize]]) {
      const context = canvas.getContext("2d");
      const x = frequencies.markerPosition * size.width / (WATERFALL_BIN_COUNT - 1);
      context.strokeStyle = palette.marker;
      context.lineWidth = size.ratio;
      context.beginPath();
      context.moveTo(x, 0);
      context.lineTo(x, size.height);
      context.stroke();
    }
  }
}

function updateWaterfallFrameAge() {
  const target = element("waterfall-frame-age");
  if (waterfallLastFrameAt === null) {
    target.textContent = "Unavailable";
    return;
  }
  target.textContent = `${Math.max(0, (Date.now() - waterfallLastFrameAt) / 1000).toFixed(1)} s`;
}

function initializeWaterfallWorkspace() {
  element("waterfall-pause").addEventListener("click", () => {
    waterfallPaused = !waterfallPaused;
    const button = element("waterfall-pause");
    button.setAttribute("aria-pressed", String(waterfallPaused));
    button.textContent = waterfallPaused ? "Resume display" : "Pause display";
    setWaterfallStatus(
      waterfallPaused ? "paused" : (waterfallConnected ? "live" : "idle"),
      waterfallPaused
        ? "Display paused; scanner data continues to be consumed."
        : (waterfallConnected ? "Live relative waterfall data." : "Waterfall display resumed."),
    );
  });
  element("waterfall-clear").addEventListener("click", () => {
    waterfallHistory = [];
    waterfallLatestFrame = null;
    renderWaterfallCanvases();
  });
  const fullscreenButton = element("waterfall-fullscreen");
  if (
    typeof element("waterfall-panel").requestFullscreen !== "function" ||
    typeof document.exitFullscreen !== "function"
  ) {
    fullscreenButton.disabled = true;
    fullscreenButton.title = "Full-screen display is unavailable in this browser.";
  }
  fullscreenButton.addEventListener("click", async () => {
    const panel = element("waterfall-panel");
    if (document.fullscreenElement === panel) {
      await document.exitFullscreen();
    } else {
      await panel.requestFullscreen();
    }
  });
  document.addEventListener("fullscreenchange", () => {
    element("waterfall-fullscreen").textContent = document.fullscreenElement === element("waterfall-panel")
      ? "Exit full screen"
      : "Full screen";
    renderWaterfallCanvases();
  });
  if (typeof ResizeObserver === "function") {
    const observer = new ResizeObserver(renderWaterfallCanvases);
    observer.observe(element("waterfall-panel"));
  } else {
    window.addEventListener("resize", renderWaterfallCanvases);
  }
  if (typeof MutationObserver === "function") {
    const themeObserver = new MutationObserver(renderWaterfallCanvases);
    themeObserver.observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
  }
  if (typeof window.matchMedia === "function") {
    for (const query of [
      "(prefers-color-scheme: dark)",
      "(forced-colors: active)",
      "(prefers-contrast: more)",
    ]) {
      const media = window.matchMedia(query);
      if (typeof media.addEventListener === "function") {
        media.addEventListener("change", renderWaterfallCanvases);
      }
    }
  }
  window.setInterval(updateWaterfallFrameAge, 1000);
  resetWaterfallLiveState();
}

function record(value) {
  if (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value)
  ) {
    return value;
  }
  return {};
}

function displayValue(value, fallback = "Unavailable") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

function setText(id, value, fallback = "Unavailable") {
  element(id).textContent = displayValue(value, fallback);
}

const RADIO_STATE_FIELD_TARGETS = Object.freeze({
  mode: "radio-mode",
  screen: "radio-screen-raw",
  screen_kind: "radio-screen",
  system: "radio-system",
  department: "radio-department",
  site: "radio-site",
  system_index: "radio-system-index",
  system_hold: "radio-system-hold",
  department_index: "radio-department-index",
  department_hold: "radio-department-hold",
  site_index: "radio-site-index",
  site_hold: "radio-site-hold",
  channel: "radio-channel",
  channel_index: "radio-channel-index",
  channel_number: "radio-channel-number",
  channel_kind: "radio-channel-kind",
  channel_hold: "radio-channel-hold",
  frequency: "radio-frequency",
  modulation: "radio-modulation",
  sub_audio_detected: "radio-sub-audio-detected",
  tone_out_tone_a: "radio-tone-out-tone-a",
  tone_out_tone_b: "radio-tone-out-tone-b",
  weather_mode: "radio-weather-mode",
  weather_same: "radio-weather-same",
  service_type: "radio-service-type",
  talkgroup_id: "radio-talkgroup-id",
  unit_id: "radio-unit-id",
  volume: "radio-volume",
  squelch: "radio-squelch",
  signal: "radio-signal",
  rssi: "radio-rssi",
  battery: "radio-battery",
  p25_status: "radio-p25-status",
  mute: "radio-mute",
  recording: "radio-recording",
});

const RADIO_SCREEN_PROFILES = Object.freeze({
  scanning: "Now scanning",
  search: "Quick Search",
  close_call: "Close Call",
  weather: "Weather",
  tone_out: "Tone-Out",
  unknown: "Scanner activity",
});

const RADIO_SCREEN_PRESENTATIONS = Object.freeze({
  search: Object.freeze({layout: "search", group: "rf"}),
  close_call: Object.freeze({layout: "search", group: "rf"}),
  weather: Object.freeze({layout: "weather", group: "special"}),
  tone_out: Object.freeze({layout: "tone_out", group: "special"}),
});

function normalizedScreenKind(value) {
  return typeof value === "string" && Object.hasOwn(RADIO_SCREEN_PROFILES, value)
    ? value
    : "unknown";
}

function normalizedRadioScanFallback(value) {
  return value === "simple" || value === "detail" ? value : "detail";
}

function normalizedRadioInspectionView(value) {
  return typeof value === "string" && RADIO_INSPECTION_VIEWS.includes(value)
    ? value
    : "auto";
}

function automaticRadioPresentation(screenKind = currentScreenKind) {
  const adaptive = Object.hasOwn(RADIO_SCREEN_PRESENTATIONS, screenKind)
    ? RADIO_SCREEN_PRESENTATIONS[screenKind]
    : undefined;
  if (adaptive !== undefined) {
    return adaptive;
  }
  return {
    layout: radioScanFallback,
    group: radioScanFallback === "detail" ? "hierarchy" : null,
  };
}

function applyRadioPresentation() {
  const panel = element("radio-activity-panel");
  const automatic = automaticRadioPresentation();
  const group =
    radioInspectionView === "auto" ? automatic.group : radioInspectionView;

  panel.dataset.displayLayout = automatic.layout;
  panel.dataset.radioView = radioInspectionView;
  panel.dataset.activeRadioGroup = group === null ? "none" : group;

  for (const view of RADIO_INSPECTION_VIEWS) {
    element(`radio-view-${view}`).setAttribute(
      "aria-pressed",
      String(view === radioInspectionView),
    );
  }
  for (const fieldGroup of RADIO_FIELD_GROUPS) {
    element(`radio-group-${fieldGroup}`).hidden = fieldGroup !== group;
  }
}

function initializeRadioViewControls() {
  radioScanFallback = normalizedRadioScanFallback(
    readStoredValue(RADIO_SCAN_FALLBACK_STORAGE_KEY),
  );
  const fallbackSelect = element("radio-scan-fallback-select");
  fallbackSelect.value = radioScanFallback;
  fallbackSelect.addEventListener("change", () => {
    radioScanFallback = normalizedRadioScanFallback(fallbackSelect.value);
    fallbackSelect.value = radioScanFallback;
    writeStoredValue(RADIO_SCAN_FALLBACK_STORAGE_KEY, radioScanFallback);
    applyRadioPresentation();
  });

  for (const view of RADIO_INSPECTION_VIEWS) {
    element(`radio-view-${view}`).addEventListener("click", () => {
      radioInspectionView = normalizedRadioInspectionView(view);
      applyRadioPresentation();
    });
  }
  applyRadioPresentation();
}

function toneOutDisplayValue(value) {
  if (typeof value === "number" && Number.isFinite(value) && value === 0) {
    return "Detect";
  }
  if (typeof value === "string") {
    const numeric = value.trim().match(
      /^([+-]?(?:\d+(?:\.\d*)?|\.\d+))(?:\s*hz)?$/i,
    );
    if (numeric !== null && Number(numeric[1]) === 0) {
      return "Detect";
    }
  }
  return value;
}

function renderRadioProfile(value) {
  const screenKind = normalizedScreenKind(value);
  currentScreenKind = screenKind;
  element("radio-activity-panel").dataset.screenKind = screenKind;
  element("activity-title").textContent = RADIO_SCREEN_PROFILES[screenKind];
  applyRadioPresentation();
}

function renderRadioState(radio) {
  for (const [field, target] of Object.entries(RADIO_STATE_FIELD_TARGETS)) {
    let value = radio[field];
    let fallback = "Unavailable";

    if (field === "system") {
      fallback = "No active system";
    } else if (field === "channel") {
      fallback = "No active channel";
    } else if (field === "signal") {
      value = signalLabel(value);
    } else if (field === "rssi") {
      value = rssiLabel(value);
    } else if (field === "tone_out_tone_a" || field === "tone_out_tone_b") {
      value = toneOutDisplayValue(value);
    }

    setText(target, value, fallback);
  }
  renderRadioProfile(radio.screen_kind);
}

function booleanLabel(value, trueLabel, falseLabel) {
  if (value === true) {
    return trueLabel;
  }
  if (value === false) {
    return falseLabel;
  }
  return "Unavailable";
}

function signalLabel(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return `${value} / 5`;
  }
  return "Unavailable";
}

function finiteNumber(value, fallback = 0) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  return fallback;
}

function wholeNumber(value, fallback = 0) {
  const number = finiteNumber(value, fallback);
  return Math.max(0, Math.trunc(number));
}

function formatDuration(value) {
  const seconds = Math.max(0, finiteNumber(value));
  const wholeSeconds = Math.floor(seconds);
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const remainder = wholeSeconds % 60;

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(
      remainder,
    ).padStart(2, "0")}`;
  }
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function formatBytes(value) {
  const bytes = wholeNumber(value);
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KiB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function formatRecordedAt(value) {
  if (typeof value !== "string" || value === "") {
    return "Unknown time";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Unknown time";
  }
  return parsed.toLocaleString();
}

function recordingName(identifier) {
  const parts = identifier.split("/");
  return parts[parts.length - 1] || identifier;
}

function recordingFileUrl(identifier) {
  const encoded = identifier
    .split("/")
    .map((component) => encodeURIComponent(component))
    .join("/");
  return webUrl(`api/v1/recordings/file/${encoded}`);
}

function setRecordingControls(recording) {
  const status = displayValue(recording.status, "unavailable").toLowerCase();
  const active = recording.active === true;
  const canStart =
    recordingStatusAvailable &&
    !recordingMutationInProgress &&
    recording.closed !== true &&
    !active &&
    ["idle", "stopped", "failed"].includes(status);
  const canStop =
    recordingStatusAvailable &&
    !recordingMutationInProgress &&
    active &&
    status === "recording";

  element("recording-start").disabled = !canStart;
  element("recording-stop").disabled = !canStop;
  element("recordings-refresh").disabled = recordingsRefreshInProgress;
  updateRecordingPaginationControls();
}

function renderRecording(recording, available = true) {
  currentRecording = record(recording);
  recordingStatusAvailable = available;
  const reliability = record(currentRecording.reliability);
  const status = displayValue(currentRecording.status, "Unavailable");

  element("recording-status").textContent = status;
  setText(
    "recording-elapsed",
    formatDuration(currentRecording.elapsed_seconds),
    "0:00",
  );
  setText("recording-packets", wholeNumber(currentRecording.packets), "0");
  setText("recording-samples", wholeNumber(currentRecording.samples), "0");
  setText(
    "recording-audio-duration",
    formatDuration(currentRecording.audio_duration_seconds),
    "0:00",
  );
  setText(
    "recording-rtp-loss",
    `${wholeNumber(reliability.packets_lost)} packets`,
  );
  setText(
    "recording-rtp-order",
    `${wholeNumber(reliability.duplicate_packets)} / ${wholeNumber(
      reliability.late_packets,
    )}`,
  );
  setText(
    "recording-rtp-invalid",
    `${wholeNumber(reliability.malformed_packets)} / ${wholeNumber(
      reliability.unexpected_source_packets,
    )}`,
  );
  setText(
    "recording-discontinuities",
    wholeNumber(reliability.timestamp_discontinuities),
    "0",
  );
  setText("recording-file", currentRecording.recording, "None");
  setRecordingControls(currentRecording);
}

function clearChildren(node) {
  while (node.firstChild !== null) {
    node.removeChild(node.firstChild);
  }
}

function makeRecordingActionLink(label, identifier, download = false) {
  const link = document.createElement("a");
  link.className = "recording-action";
  link.textContent = label;
  link.href = recordingFileUrl(identifier);
  if (download) {
    link.download = recordingName(identifier);
  }
  return link;
}

function playSavedRecording(identifier) {
  const player = element("saved-recording-player");
  const name = recordingName(identifier);
  player.src = recordingFileUrl(identifier);
  player.load();
  element("saved-playback-status").textContent = `Loading ${name}.`;
  void player.play().catch((error) => {
    const message =
      error instanceof Error ? error.message : "Saved recording playback failed.";
    element("saved-playback-status").textContent = message;
  });
}

function recordingsPageCount(entries = recordingEntries) {
  return Math.max(1, Math.ceil(entries.length / RECORDINGS_PAGE_SIZE));
}

function normalizedRecordingPageIndex(value, entries = recordingEntries) {
  const numeric = Number.isFinite(value) ? Math.trunc(value) : 0;
  return Math.min(Math.max(0, numeric), recordingsPageCount(entries) - 1);
}

function recordingPageEntries(entries, pageIndex) {
  const normalized = normalizedRecordingPageIndex(pageIndex, entries);
  const start = normalized * RECORDINGS_PAGE_SIZE;
  return entries.slice(start, start + RECORDINGS_PAGE_SIZE);
}

function updateRecordingPaginationControls() {
  const pageCount = recordingsPageCount();
  recordingPageIndex = normalizedRecordingPageIndex(recordingPageIndex);
  const pageStatus = element("recordings-page-status");
  const pageStatusText = `Page ${recordingPageIndex + 1} of ${pageCount}`;
  if (pageStatus.textContent !== pageStatusText) {
    pageStatus.textContent = pageStatusText;
  }

  const previous = element("recordings-previous-page");
  const next = element("recordings-next-page");
  if (document.activeElement === previous || document.activeElement === next) {
    recordingPaginationFocusId = document.activeElement.id;
  }

  previous.disabled = recordingPageIndex === 0;
  next.disabled =
    recordingEntries.length === 0 || recordingPageIndex >= pageCount - 1;

  if (recordingPaginationFocusId === null) {
    return;
  }
  const focusedControl = element(recordingPaginationFocusId);
  if (!focusedControl.disabled) {
    recordingPaginationFocusId = null;
    return;
  }

  const alternate = focusedControl === previous ? next : previous;
  const refresh = element("recordings-refresh");
  const focusTarget = !alternate.disabled
    ? alternate
    : !refresh.disabled
      ? refresh
      : null;
  if (focusTarget !== null) {
    focusTarget.focus();
    recordingPaginationFocusId = null;
  }
}

function initializeRecordingPaginationControls() {
  element("recordings-previous-page").addEventListener("click", () => {
    recordingPageIndex = normalizedRecordingPageIndex(recordingPageIndex - 1);
    renderRecordingsPage();
  });
  element("recordings-next-page").addEventListener("click", () => {
    recordingPageIndex = normalizedRecordingPageIndex(recordingPageIndex + 1);
    renderRecordingsPage();
  });
}

function appendRecordingEntry(list, entry) {
  const identifier = entry.audio;
  const item = document.createElement("li");
  item.className = "recording-item";

  const details = document.createElement("div");
  details.className = "recording-item-details";

  const name = document.createElement("strong");
  name.className = "technical-value";
  name.textContent = recordingName(identifier);
  details.appendChild(name);

  const metadata = document.createElement("span");
  metadata.className = "recording-item-meta";
  metadata.textContent =
    `${formatRecordedAt(entry.recorded_at)} · ${
      entry.duration_seconds === null || entry.duration_seconds === undefined
        ? "Unknown duration"
        : formatDuration(entry.duration_seconds)
    } · ${formatBytes(entry.audio_size_bytes)}`;
  details.appendChild(metadata);
  item.appendChild(details);

  const actions = document.createElement("div");
  actions.className = "recording-item-actions";

  if (entry.playable === true) {
    const play = document.createElement("button");
    play.type = "button";
    play.className = "recording-action";
    play.textContent = "Play";
    play.addEventListener("click", () => {
      playSavedRecording(identifier);
    });
    actions.appendChild(play);
    actions.appendChild(makeRecordingActionLink("Download", identifier, true));
  } else {
    const unavailable = document.createElement("span");
    unavailable.className = "recording-unavailable";
    unavailable.textContent = "Not playable";
    actions.appendChild(unavailable);
  }

  item.appendChild(actions);
  list.appendChild(item);
}

function renderRecordingsPage() {
  const list = element("recordings-list");
  if (list.contains(document.activeElement)) {
    element("recordings-refresh").focus();
  }
  clearChildren(list);

  if (recordingEntries.length === 0) {
    const empty = document.createElement("li");
    empty.className = "recording-empty";
    empty.textContent = "No finalized recordings are available.";
    list.appendChild(empty);
    element("recordings-message").textContent = "No finalized recordings.";
    updateRecordingPaginationControls();
    return;
  }

  element("recordings-message").textContent =
    `${recordingEntries.length} recent of ${recordingTotalEntries} finalized ` +
    `recording${recordingTotalEntries === 1 ? "" : "s"}.`;
  for (const entry of recordingPageEntries(recordingEntries, recordingPageIndex)) {
    appendRecordingEntry(list, entry);
  }
  updateRecordingPaginationControls();
}

function renderRecordings(inventory) {
  const entries = (Array.isArray(inventory.entries) ? inventory.entries : [])
    .map((entry) => record(entry))
    .filter((entry) => typeof entry.audio === "string" && entry.audio !== "");
  const signature = JSON.stringify(entries);
  if (signature !== recordingInventorySignature) {
    recordingPageIndex = 0;
    recordingInventorySignature = signature;
  }
  recordingEntries = entries;
  recordingTotalEntries = wholeNumber(inventory.total_entries);
  recordingPageIndex = normalizedRecordingPageIndex(recordingPageIndex);
  renderRecordingsPage();
}

async function refreshRecordingStatus() {
  if (recordingRefreshInProgress || document.hidden) {
    return;
  }

  recordingRefreshInProgress = true;
  try {
    const response = await fetch(webUrl("api/v1/recording"), {
      method: "GET",
      headers: {Accept: "application/json"},
      cache: "no-store",
      credentials: "same-origin",
    });

    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok) {
      throw new Error(errorMessage(payload, response));
    }
    renderRecording(record(payload).recording);
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Daemon recording status is unavailable.";
    element("recording-status").textContent = message;
    recordingStatusAvailable = false;
    setRecordingControls(currentRecording);
  } finally {
    recordingRefreshInProgress = false;
  }
}

async function refreshRecordings() {
  if (recordingsRefreshInProgress || document.hidden) {
    return;
  }

  recordingsRefreshInProgress = true;
  setRecordingControls(currentRecording);
  try {
    const response = await fetch(webUrl("api/v1/recordings"), {
      method: "GET",
      headers: {Accept: "application/json"},
      cache: "no-store",
      credentials: "same-origin",
    });

    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok) {
      throw new Error(errorMessage(payload, response));
    }
    renderRecordings(record(payload).recordings);
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Finalized recordings are unavailable.";
    element("recordings-message").textContent = message;
  } finally {
    recordingsRefreshInProgress = false;
    setRecordingControls(currentRecording);
  }
}

async function performRecordingAction(action) {
  if (recordingMutationInProgress) {
    return;
  }

  recordingMutationInProgress = true;
  setRecordingControls(currentRecording);
  element("recording-status").textContent =
    action === "start" ? "Starting…" : "Stopping…";

  try {
    const response = await fetch(webUrl(`api/v1/recording/${action}`), {
      method: "POST",
      headers: {Accept: "application/json"},
      cache: "no-store",
      credentials: "same-origin",
    });

    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok) {
      throw new Error(errorMessage(payload, response));
    }

    const wasActive = currentRecording.active === true;
    renderRecording(record(payload).recording);
    if (
      action === "stop" ||
      (wasActive && currentRecording.active !== true)
    ) {
      await refreshRecordings();
    }
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Daemon recording operation failed.";
    element("recording-status").textContent = message;
    await refreshRecordingStatus();
  } finally {
    recordingMutationInProgress = false;
    setRecordingControls(currentRecording);
  }
}

function daemonControlSupported(operation) {
  const operations = Array.isArray(currentDaemonHello.control_operations)
    ? currentDaemonHello.control_operations
    : [];
  return (
    currentDaemonHello.read_only === false &&
    operations.includes(operation)
  );
}

function scannerIndexAvailable(value) {
  return Number.isInteger(value) && value >= 0 && value < 0xffffffff;
}

function setScannerHoldControl(id, scope, available, held) {
  const button = element(id);
  const indicator = element(`${id}-state`);
  button.disabled = !available;
  button.textContent = held ? `Release ${scope}` : `Hold ${scope}`;
  button.setAttribute("aria-pressed", held ? "true" : "false");
  indicator.hidden = !held;
}

function setScannerControls() {
  const radio = record(currentSnapshot.radio_state);
  const idle = !scannerControlMutationInProgress;
  const running = currentSnapshot.state === "running";
  const connected = currentSnapshot.scanner_connected === true;
  const canSelect = idle && running && connected;
  const canHold = daemonControlSupported("scanner.hold_state");
  const canNavigateForward = daemonControlSupported("scanner.next");
  const canNavigateBackward = daemonControlSupported("scanner.previous");
  const channelAvailable =
    scannerIndexAvailable(radio.channel_index) &&
    ["TGID", "ConvFrequency"].includes(radio.channel_kind);
  const systemHeld = radio.system_hold === "On";
  const departmentHeld = radio.department_hold === "On";
  const siteHeld = radio.site_hold === "On";
  const channelHeld = radio.channel_hold === "On";
  const systemHoldKnown = systemHeld || radio.system_hold === "Off";
  const departmentHoldKnown =
    departmentHeld || radio.department_hold === "Off";
  const siteHoldKnown = siteHeld || radio.site_hold === "Off";
  const channelHoldKnown = channelHeld || radio.channel_hold === "Off";

  setScannerHoldControl(
    "scanner-hold-system",
    "system",
    canSelect &&
      canHold &&
      systemHoldKnown &&
      (systemHeld || scannerIndexAvailable(radio.system_index)),
    systemHeld,
  );
  setScannerHoldControl(
    "scanner-hold-department",
    "department",
    canSelect &&
      canHold &&
      departmentHoldKnown &&
      (departmentHeld || scannerIndexAvailable(radio.department_index)),
    departmentHeld,
  );
  setScannerHoldControl(
    "scanner-hold-site",
    "site",
    canSelect &&
      canHold &&
      siteHoldKnown &&
      (siteHeld || scannerIndexAvailable(radio.site_index)),
    siteHeld,
  );
  setScannerHoldControl(
    "scanner-hold-channel",
    "channel",
    canSelect &&
      canHold &&
      channelHoldKnown &&
      (channelHeld || channelAvailable),
    channelHeld,
  );
  element("scanner-next").disabled = !(
    canSelect &&
    canNavigateForward &&
    channelAvailable
  );
  element("scanner-previous").disabled = !(
    canSelect &&
    canNavigateBackward &&
    channelAvailable
  );
  element("scanner-reconnect").disabled = !(
    idle &&
    running &&
    daemonControlSupported("scanner.reconnect")
  );
}

function scannerControlAvailabilityMessage() {
  const operations = Array.isArray(currentDaemonHello.control_operations)
    ? currentDaemonHello.control_operations
    : [];
  if (
    currentDaemonHello.read_only !== false ||
    operations.length === 0
  ) {
    return "Scanner controls are unavailable.";
  }
  if (currentSnapshot.state !== "running") {
    return "Scanner controls require a running daemon runtime.";
  }
  if (currentSnapshot.scanner_connected !== true) {
    return daemonControlSupported("scanner.reconnect")
      ? "Scanner disconnected; reconnect remains available."
      : "Scanner controls are unavailable while disconnected.";
  }
  return "Scanner controls ready.";
}

async function performScannerControl(path, label, body = null) {
  if (scannerControlMutationInProgress) {
    return;
  }

  const eventGenerationAtStart = daemonEventGeneration;
  scannerControlMutationInProgress = true;
  setScannerControls();
  element("scanner-control-status").textContent = `${label}…`;

  try {
    const headers = {Accept: "application/json"};
    const options = {
      method: "POST",
      headers,
      cache: "no-store",
      credentials: "same-origin",
    };
    if (body !== null) {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(webUrl(`api/v1/scanner/${path}`), options);

    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok) {
      throw new Error(errorMessage(payload, response));
    }

    const control = record(record(payload).control);
    const snapshot = record(control.snapshot);
    if (Object.keys(snapshot).length === 0) {
      throw new Error("Scanner control omitted its authoritative snapshot.");
    }

    if (daemonEventGeneration === eventGenerationAtStart) {
      currentSnapshot = snapshot;
      renderSnapshot(currentSnapshot, `${label} completed.`);
    } else {
      await reconcileStatusAfterControl();
    }
    element("scanner-control-status").textContent = `Completed: ${label}.`;
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Scanner control failed.";
    await refreshStatus();
    element("scanner-control-status").textContent = message;
  } finally {
    scannerControlMutationInProgress = false;
    setScannerControls();
  }
}

function rssiLabel(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return `${value} dBm`;
  }
  return "Unavailable";
}

function psiLabel(snapshot) {
  if (snapshot.psi_active === true) {
    const interval = displayValue(snapshot.psi_interval_ms, "?");
    return `Active · ${interval} ms`;
  }
  if (snapshot.psi_active === false) {
    return "Inactive";
  }
  return "Unavailable";
}

function setOverallStatus(state, label, message) {
  const badge = element("status-badge");
  badge.dataset.state = state;
  badge.textContent = label;
  element("dashboard-message").textContent = message;
}

function renderSnapshot(snapshot, message = null) {
  const radio = record(snapshot.radio_state);
  const audio = record(snapshot.audio);
  const router = record(snapshot.router);
  const recording = record(snapshot.recording);

  const connected = snapshot.scanner_connected === true;
  const defaultMessage = connected
    ? "Daemon and scanner status are available."
    : "The daemon is available, but the scanner is disconnected.";

  setOverallStatus(
    connected ? "online" : "offline",
    connected ? "Connected" : "Disconnected",
    message ?? defaultMessage,
  );

  setText(
    "scanner-connected",
    booleanLabel(snapshot.scanner_connected, "Connected", "Disconnected"),
  );
  setText("scanner-model", snapshot.scanner_model, "Unknown model");
  setText("scanner-firmware", snapshot.scanner_firmware, "Unknown firmware");
  setText("scanner-endpoint", snapshot.scanner_endpoint);

  renderRadioState(radio);

  setText("daemon-state", snapshot.state);
  setText("psi-state", psiLabel(snapshot));
  setText("audio-state", booleanLabel(audio.running, "Running", "Stopped"));
  setText("router-state", booleanLabel(router.running, "Running", "Stopped"));
  setText("transition-sequence", snapshot.transition_sequence);

  if (Object.keys(recording).length > 0) {
    renderRecording(recording);
  }

  setScannerControls();

  const updatedAt = new Date();
  const updateNode = element("last-update");
  updateNode.dateTime = updatedAt.toISOString();
  updateNode.textContent = updatedAt.toLocaleString();
}

function renderStatus(payload) {
  const daemon = record(payload.daemon);
  currentDaemonHello = record(daemon.hello);
  currentSnapshot = record(daemon.snapshot);
  renderSnapshot(currentSnapshot);
  if (!scannerControlMutationInProgress) {
    element("scanner-control-status").textContent =
      scannerControlAvailabilityMessage();
  }
}

function eventSequence(envelope, message) {
  if (
    typeof envelope.sequence === "number" &&
    Number.isInteger(envelope.sequence)
  ) {
    return envelope.sequence;
  }

  const parsed = Number.parseInt(message.lastEventId, 10);
  if (Number.isInteger(parsed)) {
    return parsed;
  }

  throw new Error("Daemon event omitted a valid sequence.");
}

function applyDaemonEvent(envelope, message) {
  const kind = envelope.kind;
  const payload = record(envelope.payload);
  const sequence = eventSequence(envelope, message);

  if (kind === "stream.snapshot") {
    currentSnapshot = payload;
    lastEventSequence = sequence;
    daemonEventGeneration += 1;
    renderSnapshot(currentSnapshot, "Live daemon events are connected.");
    return;
  }

  if (lastEventSequence !== null && sequence !== lastEventSequence + 1) {
    throw new Error(
      `Daemon event sequence gap: expected ${
        lastEventSequence + 1
      }, received ${sequence}.`,
    );
  }
  lastEventSequence = sequence;
  daemonEventGeneration += 1;

  if (kind === "daemon.transition") {
    currentSnapshot = record(payload.snapshot);
  } else if (kind === "scanner.connection") {
    currentSnapshot = {
      ...currentSnapshot,
      scanner_connected: payload.connected,
      scanner_endpoint: payload.endpoint ?? currentSnapshot.scanner_endpoint,
    };
  } else if (kind === "scanner.psi") {
    currentSnapshot = {
      ...currentSnapshot,
      psi_active: true,
      radio_state: record(payload.state),
    };
  } else if (kind === "radio.state") {
    currentSnapshot = {
      ...currentSnapshot,
      radio_state: record(payload.current),
    };
  } else if (kind === "audio.state") {
    currentSnapshot = {
      ...currentSnapshot,
      audio: payload,
    };
  } else if (kind === "recording.state") {
    const wasActive = currentRecording.active === true;
    currentSnapshot = {
      ...currentSnapshot,
      recording: payload,
    };
    renderRecording(payload);
    if (wasActive && currentRecording.active !== true) {
      void refreshRecordings();
    }
    return;
  } else if (kind === "destination.health") {
    void refreshStatus();
    return;
  } else {
    return;
  }

  renderSnapshot(currentSnapshot, "Live daemon events are connected.");
}

function errorMessage(payload, response) {
  const detail = record(payload).detail;
  if (typeof detail === "string" && detail !== "") {
    return detail;
  }
  return `Status request failed with HTTP ${response.status}.`;
}

async function fetchStatusPayload() {
  const response = await fetch(webUrl("api/v1/status"), {
    method: "GET",
    headers: {Accept: "application/json"},
    cache: "no-store",
    credentials: "same-origin",
  });

  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }

  if (!response.ok) {
    throw new Error(errorMessage(payload, response));
  }
  return payload;
}

async function reconcileStatusAfterControl() {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const generation = daemonEventGeneration;
    let payload;
    try {
      payload = await fetchStatusPayload();
    } catch {
      // The scanner control has already completed successfully. Preserve the
      // event-derived projection and let normal status refresh reconcile later.
      return;
    }
    if (daemonEventGeneration === generation) {
      renderStatus(payload);
      return;
    }
  }

  // A busy event stream remained authoritative throughout reconciliation.
  // Preserve its ordered projection; periodic status refresh supplies the
  // next complete authoritative snapshot boundary.
}

async function refreshStatus() {
  if (refreshInProgress || document.hidden) {
    return;
  }

  refreshInProgress = true;

  try {
    renderStatus(await fetchStatusPayload());
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "The scanner daemon is unavailable.";

    setOverallStatus("offline", "Unavailable", message);
    setText("scanner-connected", "Unavailable");
    setText("daemon-state", "Unavailable");
    setText("psi-state", "Unavailable");
    setText("audio-state", "Unavailable");
    setText("router-state", "Unavailable");
    currentDaemonHello = {};
    setScannerControls();
    if (!scannerControlMutationInProgress) {
      element("scanner-control-status").textContent =
        "Scanner controls are unavailable while daemon status is unavailable.";
    }
  } finally {
    refreshInProgress = false;
  }
}

function clearEventStreamRestartTimer() {
  if (eventStreamRestartTimer !== null) {
    window.clearTimeout(eventStreamRestartTimer);
    eventStreamRestartTimer = null;
  }
}

function scheduleEventStreamRestart(source) {
  if (eventSource !== source) {
    return;
  }

  eventSource = null;
  eventStreamConnected = false;
  lastEventSequence = null;
  source.close();

  if (
    document.hidden ||
    typeof EventSource === "undefined" ||
    eventStreamRestartTimer !== null
  ) {
    return;
  }

  eventStreamRestartTimer = window.setTimeout(() => {
    eventStreamRestartTimer = null;
    startEventStream();
  }, FALLBACK_REFRESH_INTERVAL_MS);
}

function stopEventStream() {
  clearEventStreamRestartTimer();

  const source = eventSource;
  eventSource = null;
  eventStreamConnected = false;
  lastEventSequence = null;
  if (source !== null) {
    source.close();
  }
}

function startEventStream() {
  stopEventStream();

  if (document.hidden || typeof EventSource === "undefined") {
    return;
  }

  const source = new EventSource(webUrl("api/v1/events"));
  eventSource = source;

  source.onopen = () => {
    if (eventSource !== source) {
      return;
    }
    eventStreamConnected = true;
  };

  source.onmessage = (message) => {
    if (eventSource !== source) {
      return;
    }
    try {
      const envelope = JSON.parse(message.data);
      if (record(envelope) !== envelope) {
        throw new Error("Daemon event envelope is not an object.");
      }
      eventStreamConnected = true;
      applyDaemonEvent(envelope, message);
    } catch {
      scheduleEventStreamRestart(source);
      void refreshStatus();
    }
  };

  source.onerror = () => {
    if (eventSource !== source) {
      return;
    }
    element("dashboard-message").textContent =
      "Live events are reconnecting; status polling remains active.";
    scheduleEventStreamRestart(source);
  };
}

function setAudioControls(status, active) {
  element("audio-playback-status").textContent = status;
  element("audio-play").disabled = active;
  element("audio-stop").disabled = !active;
}

function resetAudioTelemetry() {
  audioLastStreamSequence = null;
  audioLastPacketsDropped = null;
  audioLastPayloadBytesDropped = null;
  audioLastOverflows = null;
  audioPacketsReceived = 0;
  audioRtpMissingPackets = 0;
  audioLastTelemetryUpdate = 0;
  setText("audio-source", "Unavailable");
  setText("audio-packets", 0);
  setText("audio-queue-loss", "0 packets · 0 overflows");
  setText("audio-rtp-loss", "0 packets");
}

function pcmuMagicMatches(view) {
  return (
    view.getUint8(0) === 0x53 &&
    view.getUint8(1) === 0x44 &&
    view.getUint8(2) === 0x53 &&
    view.getUint8(3) === 0x50
  );
}

function parsePcmuFrame(frame) {
  if (!(frame instanceof Uint8Array) || frame.byteLength < PCMU_HEADER_BYTES) {
    throw new Error("PCMU frame is shorter than its fixed header.");
  }

  const view = new DataView(
    frame.buffer,
    frame.byteOffset,
    frame.byteLength,
  );

  if (!pcmuMagicMatches(view)) {
    throw new Error("PCMU frame magic is incompatible.");
  }

  const version = view.getUint8(4);
  const flags = view.getUint8(5);
  const headerSize = view.getUint16(6, false);
  const frameSize = view.getUint32(8, false);

  if (version !== PCMU_VERSION) {
    throw new Error(`Unsupported PCMU stream version: ${version}.`);
  }
  if ((flags & ~PCMU_KNOWN_FLAGS) !== 0) {
    throw new Error("PCMU frame contains unsupported flags.");
  }
  if (headerSize !== PCMU_HEADER_BYTES) {
    throw new Error("PCMU frame header size is invalid.");
  }
  if (frameSize !== frame.byteLength) {
    throw new Error("PCMU frame size is inconsistent.");
  }
  if (frameSize > PCMU_MAX_FRAME_BYTES) {
    throw new Error("PCMU frame exceeds the browser maximum.");
  }

  const streamSequence = view.getBigUint64(12, false);
  const expectedSequence = view.getUint16(38, false);
  const missingPackets = view.getUint32(40, false);
  const expectedTimestamp = view.getUint32(44, false);
  const missingSamples = view.getUint32(48, false);
  const packetsDropped = view.getBigUint64(52, false);
  const payloadBytesDropped = view.getBigUint64(60, false);
  const overflows = view.getBigUint64(68, false);
  const endpointSize = view.getUint16(76, false);
  const payloadSize = view.getUint32(78, false);

  if (streamSequence === 0n) {
    throw new Error("PCMU stream sequence must be greater than zero.");
  }
  if (
    (flags & PCMU_EXPECTED_SEQUENCE) === 0 &&
    expectedSequence !== 0
  ) {
    throw new Error("PCMU frame has an unexpected sequence value.");
  }
  if (missingPackets > 0 && (flags & PCMU_EXPECTED_SEQUENCE) === 0) {
    throw new Error("PCMU packet loss omitted its expected sequence.");
  }
  if (
    (flags & PCMU_EXPECTED_TIMESTAMP) === 0 &&
    expectedTimestamp !== 0
  ) {
    throw new Error("PCMU frame has an unexpected timestamp value.");
  }
  if (
    (missingSamples > 0 || (flags & PCMU_TIMESTAMP_BACKWARDS) !== 0) &&
    (flags & PCMU_EXPECTED_TIMESTAMP) === 0
  ) {
    throw new Error("PCMU timestamp discontinuity omitted its expectation.");
  }
  if (
    missingSamples > 0 &&
    (flags & PCMU_TIMESTAMP_BACKWARDS) !== 0
  ) {
    throw new Error("PCMU timestamp loss and backwards movement conflict.");
  }
  if (headerSize + endpointSize + payloadSize !== frameSize) {
    throw new Error("PCMU frame body sizes are inconsistent.");
  }

  const endpointStart = headerSize;
  const endpointEnd = endpointStart + endpointSize;
  const endpointBytes = frame.slice(endpointStart, endpointEnd);
  const payload = frame.slice(endpointEnd);
  const endpoint = new TextDecoder("utf-8", {fatal: true}).decode(
    endpointBytes,
  );

  if (endpoint.trim() === "") {
    throw new Error("PCMU packet endpoint is empty.");
  }

  return {
    streamSequence,
    flags,
    missingPackets,
    missingSamples,
    packetsDropped,
    payloadBytesDropped,
    overflows,
    endpoint,
    payload,
  };
}

class PcmuFrameParser {
  constructor(onFrame) {
    this.buffer = new Uint8Array(0);
    this.onFrame = onFrame;
  }

  push(chunk) {
    if (!(chunk instanceof Uint8Array)) {
      throw new Error("PCMU HTTP stream yielded non-binary data.");
    }

    const combined = new Uint8Array(this.buffer.byteLength + chunk.byteLength);
    combined.set(this.buffer, 0);
    combined.set(chunk, this.buffer.byteLength);
    this.buffer = combined;

    while (this.buffer.byteLength >= PCMU_HEADER_BYTES) {
      const view = new DataView(
        this.buffer.buffer,
        this.buffer.byteOffset,
        this.buffer.byteLength,
      );

      if (!pcmuMagicMatches(view)) {
        throw new Error("PCMU HTTP stream lost frame alignment.");
      }

      const frameSize = view.getUint32(8, false);
      if (
        frameSize < PCMU_HEADER_BYTES ||
        frameSize > PCMU_MAX_FRAME_BYTES
      ) {
        throw new Error("PCMU HTTP stream advertised an invalid frame size.");
      }

      if (this.buffer.byteLength < frameSize) {
        break;
      }

      const frame = this.buffer.slice(0, frameSize);
      this.buffer = this.buffer.slice(frameSize);
      this.onFrame(parsePcmuFrame(frame));
    }

    if (this.buffer.byteLength > PCMU_MAX_FRAME_BYTES) {
      throw new Error("PCMU HTTP stream exceeded the pending-frame limit.");
    }
  }

  finish() {
    if (this.buffer.byteLength !== 0) {
      throw new Error("PCMU HTTP stream ended with an incomplete frame.");
    }
  }
}

function decodePcmuSample(value) {
  const inverted = (~value) & 0xff;
  const sign = inverted & 0x80;
  const exponent = (inverted >> 4) & 0x07;
  const mantissa = inverted & 0x0f;
  let sample = ((mantissa << 3) + 0x84) << exponent;
  sample -= 0x84;
  if (sign !== 0) {
    sample = -sample;
  }
  return Math.max(-1, Math.min(1, sample / 32768));
}

class PcmuScriptProcessor {
  constructor(context) {
    this.buffer = new Float32Array(
      AUDIO_FALLBACK_BUFFER_CAPACITY_SAMPLES,
    );
    this.readIndex = 0;
    this.writeIndex = 0;
    this.queuedSamples = 0;
    this.phase = 0;
    this.started = false;
    this.sourceStep = PCMU_SAMPLE_RATE / context.sampleRate;

    this.node = context.createScriptProcessor(
      AUDIO_FALLBACK_SCRIPT_BUFFER_SIZE,
      0,
      1,
    );
    this.node.onaudioprocess = (event) => {
      this.process(event.outputBuffer.getChannelData(0));
    };
  }

  connect(destination) {
    this.node.connect(destination);
  }

  disconnect() {
    this.node.onaudioprocess = null;
    this.node.disconnect();
  }

  reset() {
    this.readIndex = 0;
    this.writeIndex = 0;
    this.queuedSamples = 0;
    this.phase = 0;
    this.started = false;
  }

  enqueueSample(sample) {
    if (this.queuedSamples === this.buffer.length) {
      this.readIndex = (this.readIndex + 1) % this.buffer.length;
      this.queuedSamples -= 1;
      this.phase = 0;
    }

    this.buffer[this.writeIndex] = sample;
    this.writeIndex = (this.writeIndex + 1) % this.buffer.length;
    this.queuedSamples += 1;
  }

  enqueueSilence(count) {
    const bounded = Math.min(count, this.buffer.length);
    for (let index = 0; index < bounded; index += 1) {
      this.enqueueSample(0);
    }
  }

  enqueuePayload(payload) {
    for (let index = 0; index < payload.length; index += 1) {
      this.enqueueSample(decodePcmuSample(payload[index]));
    }
  }

  enqueuePacket(payload, gapSamples, reset) {
    if (reset) {
      this.reset();
    }

    this.enqueueSilence(gapSamples);
    this.enqueuePayload(payload);
  }

  peek(offset) {
    const index = (this.readIndex + offset) % this.buffer.length;
    return this.buffer[index];
  }

  consumeOne() {
    if (this.queuedSamples === 0) {
      return;
    }
    this.readIndex = (this.readIndex + 1) % this.buffer.length;
    this.queuedSamples -= 1;
  }

  process(output) {
    if (
      !this.started &&
      this.queuedSamples >= AUDIO_FALLBACK_START_THRESHOLD_SAMPLES
    ) {
      this.started = true;
    }

    for (let index = 0; index < output.length; index += 1) {
      if (!this.started || this.queuedSamples < 2) {
        output[index] = 0;
        this.started = false;
        this.phase = 0;
        continue;
      }

      const first = this.peek(0);
      const second = this.peek(1);
      output[index] = first + (second - first) * this.phase;

      this.phase += this.sourceStep;
      while (this.phase >= 1 && this.queuedSamples > 1) {
        this.consumeOne();
        this.phase -= 1;
      }
    }
  }
}

function renderAudioTelemetry(frame) {
  const now = performance.now();
  if (now - audioLastTelemetryUpdate < 500 && audioPacketsReceived !== 1) {
    return;
  }

  audioLastTelemetryUpdate = now;
  setText("audio-source", frame.endpoint);
  setText("audio-packets", audioPacketsReceived);
  setText(
    "audio-queue-loss",
    `${frame.packetsDropped} packets · ${frame.overflows} overflows`,
  );
  setText("audio-rtp-loss", `${audioRtpMissingPackets} packets`);
}

function deliverPcmuFrame(frame) {
  if (audioWorkletNode === null && audioScriptProcessor === null) {
    throw new Error("Browser audio processor is unavailable.");
  }

  if (
    audioLastStreamSequence !== null &&
    frame.streamSequence <= audioLastStreamSequence
  ) {
    throw new Error("PCMU stream sequence did not advance.");
  }

  if (
    audioLastPacketsDropped !== null &&
    frame.packetsDropped < audioLastPacketsDropped
  ) {
    throw new Error("PCMU dropped-packet counter regressed.");
  }

  if (
    audioLastStreamSequence !== null &&
    audioLastPacketsDropped !== null
  ) {
    const skippedPublications =
      frame.streamSequence - audioLastStreamSequence - 1n;
    const newlyDroppedPackets =
      frame.packetsDropped - audioLastPacketsDropped;
    if (skippedPublications !== newlyDroppedPackets) {
      throw new Error(
        "PCMU stream gap does not match daemon queue-loss counters.",
      );
    }
  }
  if (
    audioLastPayloadBytesDropped !== null &&
    frame.payloadBytesDropped < audioLastPayloadBytesDropped
  ) {
    throw new Error("PCMU dropped-byte counter regressed.");
  }
  if (
    audioLastOverflows !== null &&
    frame.overflows < audioLastOverflows
  ) {
    throw new Error("PCMU overflow counter regressed.");
  }

  const localDroppedSamples =
    audioLastPayloadBytesDropped === null
      ? 0n
      : frame.payloadBytesDropped - audioLastPayloadBytesDropped;
  let gapSamples = BigInt(frame.missingSamples) + localDroppedSamples;
  let reset = (frame.flags & PCMU_TIMESTAMP_BACKWARDS) !== 0;

  if (gapSamples > BigInt(MAX_GAP_SAMPLES)) {
    reset = true;
    gapSamples = 0n;
  }

  audioLastStreamSequence = frame.streamSequence;
  audioLastPacketsDropped = frame.packetsDropped;
  audioLastPayloadBytesDropped = frame.payloadBytesDropped;
  audioLastOverflows = frame.overflows;
  audioPacketsReceived += 1;
  audioRtpMissingPackets += frame.missingPackets;

  if (audioWorkletNode !== null) {
    const payloadBuffer = frame.payload.buffer;
    audioWorkletNode.port.postMessage(
      {
        type: "packet",
        payload: payloadBuffer,
        gapSamples: Number(gapSamples),
        reset,
      },
      [payloadBuffer],
    );
  } else {
    audioScriptProcessor.enqueuePacket(
      frame.payload,
      Number(gapSamples),
      reset,
    );
  }

  if (audioPacketsReceived === 1) {
    setAudioControls("Playing", true);
  }
  renderAudioTelemetry(frame);
}

function releaseAudioResources() {
  if (audioAbortController !== null) {
    audioAbortController.abort();
    audioAbortController = null;
  }

  if (audioReader !== null) {
    void audioReader.cancel().catch(() => {});
    audioReader = null;
  }

  if (audioWorkletNode !== null) {
    audioWorkletNode.disconnect();
    audioWorkletNode = null;
  }

  if (audioScriptProcessor !== null) {
    audioScriptProcessor.disconnect();
    audioScriptProcessor = null;
  }

  if (audioContext !== null) {
    void audioContext.close().catch(() => {});
    audioContext = null;
  }
}

function stopAudioPlayback() {
  audioPlaybackGeneration += 1;
  audioPlaybackActive = false;
  releaseAudioResources();
  setAudioControls("Stopped", false);
}

async function startAudioPlayback() {
  if (audioPlaybackActive) {
    return;
  }

  if (typeof AudioContext === "undefined") {
    setAudioControls("Web Audio is not supported by this browser.", false);
    element("audio-play").disabled = true;
    return;
  }

  audioPlaybackActive = true;
  const generation = audioPlaybackGeneration + 1;
  audioPlaybackGeneration = generation;
  resetAudioTelemetry();
  setAudioControls("Connecting…", true);

  try {
    const context = new AudioContext({latencyHint: "interactive"});
    audioContext = context;

    if (
      context.audioWorklet !== undefined &&
      typeof AudioWorkletNode !== "undefined"
    ) {
      await context.audioWorklet.addModule(
        webUrl("assets/audio-worklet.js"),
      );
      if (generation !== audioPlaybackGeneration) {
        return;
      }

      const worklet = new AudioWorkletNode(context, "sds200-pcmu", {
        numberOfInputs: 0,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      });
      audioWorkletNode = worklet;
      worklet.connect(context.destination);
    } else if (typeof context.createScriptProcessor === "function") {
      const processor = new PcmuScriptProcessor(context);
      audioScriptProcessor = processor;
      processor.connect(context.destination);
    } else {
      throw new Error(
        "Browser audio playback requires HTTPS or a compatible " +
        "Web Audio fallback.",
      );
    }

    await context.resume();

    if (generation !== audioPlaybackGeneration) {
      return;
    }

    const controller = new AbortController();
    audioAbortController = controller;

    const response = await fetch(webUrl("api/v1/audio"), {
      method: "GET",
      headers: {Accept: "application/octet-stream"},
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal,
    });

    if (!response.ok) {
      let payload = {};
      try {
        payload = await response.json();
      } catch {
        payload = {};
      }
      throw new Error(errorMessage(payload, response));
    }

    if (response.body === null) {
      throw new Error("Audio response omitted its streaming body.");
    }

    setAudioControls("Buffering…", true);
    const reader = response.body.getReader();
    audioReader = reader;
    const parser = new PcmuFrameParser(deliverPcmuFrame);

    while (generation === audioPlaybackGeneration) {
      const result = await reader.read();
      if (result.done) {
        parser.finish();
        throw new Error("Audio stream ended.");
      }
      parser.push(result.value);
    }
  } catch (error) {
    if (generation !== audioPlaybackGeneration) {
      return;
    }

    const message =
      error instanceof Error
        ? error.message
        : "Browser audio playback failed.";
    setAudioControls(message, false);
  } finally {
    if (generation === audioPlaybackGeneration) {
      audioPlaybackActive = false;
      releaseAudioResources();
      element("audio-play").disabled = false;
      element("audio-stop").disabled = true;
    }
  }
}

function initializeAudioPlayback() {
  resetAudioTelemetry();

  if (
    typeof AudioContext === "undefined" ||
    typeof AudioWorkletNode === "undefined"
  ) {
    setAudioControls("AudioWorklet is not supported by this browser.", false);
    element("audio-play").disabled = true;
    return;
  }

  setAudioControls("Stopped", false);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopEventStream();
    stopWaterfallStream({status: "Open this pane to start the waterfall stream."});
    return;
  }

  void refreshStatus();
  void refreshRecordingStatus();
  void refreshRecordings();
  startEventStream();
  reconcileWaterfallDemand();
});

window.addEventListener("pagehide", () => {
  stopEventStream();
  stopWaterfallStream({status: "Waterfall stream closed."});
  stopAudioPlayback();
  element("saved-recording-player").pause();
});

element("audio-play").addEventListener("click", () => {
  void startAudioPlayback();
});
element("audio-stop").addEventListener("click", stopAudioPlayback);
element("recording-start").addEventListener("click", () => {
  void performRecordingAction("start");
});
element("recording-stop").addEventListener("click", () => {
  void performRecordingAction("stop");
});
element("recordings-refresh").addEventListener("click", () => {
  void refreshRecordings();
});
initializeRecordingPaginationControls();
function performScannerHoldState(scope) {
  const radio = record(currentSnapshot.radio_state);
  const current = radio[`${scope}_hold`];
  if (current !== "On" && current !== "Off") {
    return;
  }
  const held = current !== "On";
  const action = held ? "Hold" : "Release";
  void performScannerControl(
    `hold/${scope}`,
    `${action} ${scope}`,
    {held},
  );
}

element("scanner-hold-system").addEventListener("click", () => {
  performScannerHoldState("system");
});
element("scanner-hold-department").addEventListener("click", () => {
  performScannerHoldState("department");
});
element("scanner-hold-site").addEventListener("click", () => {
  performScannerHoldState("site");
});
element("scanner-hold-channel").addEventListener("click", () => {
  performScannerHoldState("channel");
});
element("scanner-previous").addEventListener("click", () => {
  void performScannerControl("previous", "Previous channel");
});
element("scanner-next").addEventListener("click", () => {
  void performScannerControl("next", "Next channel");
});
element("scanner-reconnect").addEventListener("click", () => {
  void performScannerControl("reconnect", "Reconnect scanner");
});

const savedRecordingPlayer = element("saved-recording-player");
savedRecordingPlayer.addEventListener("play", () => {
  element("saved-playback-status").textContent =
    "Playing finalized recording.";
});
savedRecordingPlayer.addEventListener("ended", () => {
  element("saved-playback-status").textContent =
    "Saved recording playback finished.";
});
savedRecordingPlayer.addEventListener("error", () => {
  element("saved-playback-status").textContent =
    "Saved recording playback failed.";
});

initializeWaterfallWorkspace();
initializeWorkspace();
initializeThemeControl();
initializeRadioViewControls();
initializeAudioPlayback();
renderRecording({}, false);
setScannerControls();
void refreshStatus();
void refreshRecordingStatus();
void refreshRecordings();
startEventStream();

window.setInterval(() => {
  if (!eventStreamConnected) {
    void refreshStatus();
  }
}, FALLBACK_REFRESH_INTERVAL_MS);

window.setInterval(() => {
  void refreshStatus();
  if (currentRecording.active !== true) {
    void refreshRecordingStatus();
  }
}, RECONCILE_INTERVAL_MS);

window.setInterval(() => {
  if (currentRecording.active === true) {
    void refreshRecordingStatus();
  }
}, RECORDING_REFRESH_INTERVAL_MS);
