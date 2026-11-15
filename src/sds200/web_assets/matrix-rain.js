(() => {
  "use strict";

  if (typeof document === "undefined") {
    return;
  }

  const root = document.documentElement;
  const stage = document.querySelector(".theme-stage");
  const canvas = stage?.querySelector(".matrix-rain-canvas");
  const context = canvas?.getContext?.("2d", {alpha: true});

  if (!root || !stage || !canvas || !context) {
    return;
  }

  const LIVE_FIELD_IDS = Object.freeze([
    "radio-system",
    "radio-department",
    "radio-channel",
    "radio-site",
    "radio-signal",
    "radio-rssi",
  ]);
  const LIVE_FIELD_LABELS = Object.freeze([
    "SYS",
    "DEPT",
    "CHAN",
    "SITE",
    "SIG",
    "RSSI",
  ]);
  const RAIN_SETTING_STORAGE_PREFIX = "sdsctl.web.matrix-rain.";
  const RAIN_SETTING_DEFINITIONS = Object.freeze({
    speed: Object.freeze({
      controlId: "matrix-rain-speed",
      outputId: "matrix-rain-speed-value",
      minimum: 25,
      maximum: 200,
      defaultValue: 100,
    }),
    density: Object.freeze({
      controlId: "matrix-rain-density",
      outputId: "matrix-rain-density-value",
      minimum: 25,
      maximum: 200,
      defaultValue: 100,
    }),
    transparency: Object.freeze({
      controlId: "matrix-rain-transparency",
      outputId: "matrix-rain-transparency-value",
      minimum: 20,
      maximum: 100,
      defaultValue: 72,
    }),
    trail: Object.freeze({
      controlId: "matrix-rain-trail",
      outputId: "matrix-rain-trail-value",
      minimum: 10,
      maximum: 100,
      defaultValue: 60,
    }),
  });
  const MATRIX_GLYPHS = Object.freeze(Array.from(
    "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜ0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
  ));
  const FRAME_INTERVAL_MS = 1000 / 24;
  const MAX_COLUMNS = 192;
  const MAX_DEVICE_PIXEL_RATIO = 2;
  const STATIC_CELL_BUDGET = 4200;

  const reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)") ?? {
    matches: false,
  };
  const forcedColors = globalThis.matchMedia?.("(forced-colors: active)") ?? {
    matches: false,
  };

  let cssWidth = 0;
  let cssHeight = 0;
  let fontSize = 18;
  let columns = [];
  let liveSequences = [];
  let telemetryPackets = [];
  let rainSettings = readRainSettings();
  let animationFrame = 0;
  let lastFrameAt = 0;

  function hashString(value) {
    let hash = 2166136261;
    for (const character of value) {
      hash ^= character.codePointAt(0) ?? 0;
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function seededRandom(seed) {
    let state = seed >>> 0;
    return () => {
      state = (state + 0x6d2b79f5) >>> 0;
      let value = state;
      value = Math.imul(value ^ (value >>> 15), value | 1);
      value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
      return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
    };
  }

  function normalizedRainSetting(value, definition) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return definition.defaultValue;
    }
    return Math.min(definition.maximum, Math.max(definition.minimum, Math.round(numeric)));
  }

  function readStoredRainSetting(name, definition) {
    try {
      const stored = globalThis.localStorage?.getItem(
        `${RAIN_SETTING_STORAGE_PREFIX}${name}`,
      );
      if (stored === null || stored === undefined || stored === "") {
        return definition.defaultValue;
      }
      return normalizedRainSetting(
        stored,
        definition,
      );
    } catch {
      return definition.defaultValue;
    }
  }

  function writeStoredRainSetting(name, value) {
    try {
      globalThis.localStorage?.setItem(`${RAIN_SETTING_STORAGE_PREFIX}${name}`, String(value));
    } catch {
      // Storage may be unavailable in privacy modes or embedded WebViews.
    }
  }

  function removeStoredRainSetting(name) {
    try {
      globalThis.localStorage?.removeItem(`${RAIN_SETTING_STORAGE_PREFIX}${name}`);
    } catch {
      // Storage may be unavailable in privacy modes or embedded WebViews.
    }
  }

  function readRainSettings() {
    return Object.fromEntries(Object.entries(RAIN_SETTING_DEFINITIONS).map(
      ([name, definition]) => [name, readStoredRainSetting(name, definition)],
    ));
  }

  function sanitizeLiveValue(value) {
    return String(value ?? "")
      .normalize("NFKD")
      .toUpperCase()
      .replace(/[^A-Z0-9 .:/_+-]/g, "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 48);
  }

  function readLiveSequences() {
    return LIVE_FIELD_IDS.flatMap((identifier, index) => {
      const value = sanitizeLiveValue(document.getElementById(identifier)?.textContent);
      return value ? [`${LIVE_FIELD_LABELS[index]}:${value}`] : [];
    });
  }

  function refreshLiveSequences() {
    liveSequences = readLiveSequences();
    canvas.dataset.matrixRainSources = String(liveSequences.length);
    if (cssWidth > 0 && cssHeight > 0) {
      telemetryPackets = createTelemetryPackets();
    }
    if (isMatrixActive() && reducedMotion.matches && !forcedColors.matches) {
      configureSurface();
      drawStaticFrame();
    }
  }

  function surfaceSeed() {
    return hashString(
      `${cssWidth}x${cssHeight}|${liveSequences.join("|")}|`
      + `${rainSettings.density}|${rainSettings.trail}|sdsctl-matrix-rain-v2`,
    );
  }

  function createColumns() {
    const random = seededRandom(surfaceSeed());
    const densityScale = rainSettings.density / 100;
    const count = Math.min(
      MAX_COLUMNS,
      Math.max(
        8,
        Math.ceil(cssWidth / Math.max(12, fontSize * 0.88) * densityScale),
      ),
    );
    const spacing = cssWidth / count;
    return Array.from({length: count}, (_, index) => ({
      index,
      x: spacing * (index + 0.5),
      y: -random() * cssHeight,
      speed: fontSize * (1.4 + random() * 2.8),
      glyphOffset: Math.floor(random() * MATRIX_GLYPHS.length),
      step: Math.floor(random() * 31),
    }));
  }

  function createTelemetryPackets() {
    if (liveSequences.length === 0) {
      return [];
    }
    const densityScale = rainSettings.density / 100;
    const count = Math.min(
      liveSequences.length,
      Math.max(1, Math.round(cssWidth / 320 * densityScale)),
    );
    const random = seededRandom(surfaceSeed() ^ 0x51d5c7a1);
    const packetFontSize = Math.max(10, Math.round(fontSize * 0.64));
    const margin = Math.max(8, fontSize);
    const maxWidth = Math.max(
      1,
      Math.min(cssWidth - margin * 2, Math.max(fontSize * 10, cssWidth * 0.38)),
    );
    const availableX = Math.max(0, cssWidth - margin * 2 - maxWidth);
    return Array.from({length: count}, (_, index) => ({
      sequenceIndex: index % liveSequences.length,
      x: margin + random() * availableX,
      y: random() * cssHeight,
      speed: fontSize * (0.48 + random() * 0.38),
      fontSize: packetFontSize,
      maxWidth,
    }));
  }

  function configureSurface() {
    const bounds = stage.getBoundingClientRect();
    const nextWidth = Math.max(1, Math.round(bounds.width || globalThis.innerWidth || 1));
    const nextHeight = Math.max(1, Math.round(bounds.height || globalThis.innerHeight || 1));
    const ratio = Math.min(
      MAX_DEVICE_PIXEL_RATIO,
      Math.max(1, Number(globalThis.devicePixelRatio) || 1),
    );
    const physicalWidth = Math.max(1, Math.round(nextWidth * ratio));
    const physicalHeight = Math.max(1, Math.round(nextHeight * ratio));

    if (
      cssWidth === nextWidth
      && cssHeight === nextHeight
      && canvas.width === physicalWidth
      && canvas.height === physicalHeight
      && columns.length > 0
    ) {
      return false;
    }

    cssWidth = nextWidth;
    cssHeight = nextHeight;
    fontSize = Math.max(12, Math.min(22, Math.round(cssWidth / 88)));
    canvas.width = physicalWidth;
    canvas.height = physicalHeight;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.textAlign = "center";
    context.textBaseline = "middle";
    columns = createColumns();
    telemetryPackets = createTelemetryPackets();
    return true;
  }

  function matrixGlyph(column, step) {
    return MATRIX_GLYPHS[(column.glyphOffset + step * 7) % MATRIX_GLYPHS.length];
  }

  function prepareText(alpha, bright = false) {
    context.font = `${bright ? 700 : 500} ${fontSize}px ui-monospace, monospace`;
    context.fillStyle = bright
      ? `rgba(214, 255, 222, ${alpha})`
      : `rgba(67, 217, 104, ${alpha})`;
    context.shadowColor = bright ? "rgba(114, 245, 143, 0.9)" : "rgba(67, 217, 104, 0.55)";
    context.shadowBlur = bright ? 9 : 4;
  }

  function drawTelemetryPacket(packet, y, {staticFrame = false} = {}) {
    const sequence = liveSequences[packet.sequenceIndex];
    if (!sequence) {
      return;
    }
    const text = `> ${sequence}`;
    const trailCopies = Math.max(1, Math.round(rainSettings.trail / 18));
    const trailSpacing = packet.fontSize * 1.15;
    context.save();
    context.font = `700 ${packet.fontSize}px ui-monospace, monospace`;
    context.textAlign = "left";
    context.textBaseline = "middle";
    context.shadowColor = "rgba(114, 245, 143, 0.65)";
    context.shadowBlur = staticFrame ? 4 : 7;
    for (let copy = trailCopies; copy >= 1; copy -= 1) {
      const closeness = (trailCopies - copy + 1) / trailCopies;
      const alpha = 0.015 + closeness * (staticFrame ? 0.07 : 0.1);
      context.fillStyle = `rgba(67, 217, 104, ${alpha})`;
      context.fillText(text, packet.x, y - copy * trailSpacing, packet.maxWidth);
    }
    context.fillStyle = staticFrame
      ? "rgba(180, 255, 196, 0.58)"
      : "rgba(214, 255, 222, 0.9)";
    context.shadowBlur = staticFrame ? 6 : 10;
    context.fillText(text, packet.x, y, packet.maxWidth);
    context.restore();
  }

  function drawStaticTelemetryPackets() {
    telemetryPackets.forEach((packet, index) => {
      const y = cssHeight * (index + 1) / (telemetryPackets.length + 1);
      drawTelemetryPacket(packet, y, {staticFrame: true});
    });
  }

  function drawAnimatedTelemetryPackets(elapsedMs) {
    const speedScale = rainSettings.speed / 100;
    const trailCopies = Math.max(1, Math.round(rainSettings.trail / 18));
    telemetryPackets.forEach((packet) => {
      packet.y += packet.speed * speedScale * elapsedMs / 1000;
      if (packet.y > cssHeight + packet.fontSize) {
        packet.y = -packet.fontSize * (trailCopies + 1);
        packet.sequenceIndex = (
          packet.sequenceIndex + telemetryPackets.length
        ) % liveSequences.length;
      }
      drawTelemetryPacket(packet, packet.y);
    });
  }

  function drawStaticFrame() {
    context.clearRect(0, 0, cssWidth, cssHeight);
    const random = seededRandom(surfaceSeed());
    const rows = Math.max(1, Math.ceil(cssHeight / fontSize));
    const rowStride = Math.max(1, Math.ceil((columns.length * rows) / STATIC_CELL_BUDGET));

    columns.forEach((column) => {
      const head = Math.floor(random() * rows);
      for (let row = column.index % rowStride; row < rows; row += rowStride) {
        const distance = (head - row + rows) % rows;
        const alpha = Math.max(0.07, 0.58 - distance * 0.028);
        const bright = distance === 0;
        prepareText(bright ? 0.92 : alpha, bright);
        context.fillText(
          matrixGlyph(column, row + column.step),
          column.x,
          row * fontSize + fontSize / 2,
        );
      }
    });
    drawStaticTelemetryPackets();
    context.shadowBlur = 0;
  }

  function drawAnimatedFrame(elapsedMs) {
    context.shadowBlur = 0;
    const fadeAlpha = Math.max(0.035, 0.222 - rainSettings.trail * 0.00185);
    context.fillStyle = `rgba(2, 7, 5, ${fadeAlpha})`;
    context.fillRect(0, 0, cssWidth, cssHeight);

    columns.forEach((column) => {
      column.y += column.speed * (rainSettings.speed / 100) * elapsedMs / 1000;
      if (column.y > cssHeight + fontSize) {
        column.y = -fontSize * (1 + (column.index % 9));
        column.step += 1;
      }
      const step = column.step + Math.floor(column.y / Math.max(1, fontSize));
      prepareText(0.82, (column.index + step) % 9 === 0);
      context.fillText(matrixGlyph(column, step), column.x, column.y);
    });
    drawAnimatedTelemetryPackets(elapsedMs);
    context.shadowBlur = 0;
  }

  function isMatrixActive() {
    return root.dataset.theme === "matrix";
  }

  function synchronizeRainControlVisibility() {
    const controls = document.getElementById("matrix-rain-controls");
    if (controls instanceof HTMLElement) {
      controls.hidden = !isMatrixActive();
    }
  }

  function synchronizeRainControls() {
    Object.entries(RAIN_SETTING_DEFINITIONS).forEach(([name, definition]) => {
      const control = document.getElementById(definition.controlId);
      const output = document.getElementById(definition.outputId);
      if (control instanceof HTMLInputElement) {
        control.value = String(rainSettings[name]);
        control.setAttribute("aria-valuetext", `${rainSettings[name]}%`);
      }
      if (output instanceof HTMLOutputElement) {
        output.value = `${rainSettings[name]}%`;
      }
    });
    synchronizeRainControlVisibility();
  }

  function applyRainSettings({rebuild = false} = {}) {
    const opacity = rainSettings.transparency / 100;
    root.style.setProperty("--matrix-rain-opacity", opacity.toFixed(2));
    root.style.setProperty(
      "--matrix-rain-reduced-opacity",
      Math.max(0.12, opacity * 2 / 3).toFixed(2),
    );
    canvas.dataset.matrixRainSpeed = String(rainSettings.speed);
    canvas.dataset.matrixRainDensity = String(rainSettings.density);
    canvas.dataset.matrixRainTransparency = String(rainSettings.transparency);
    canvas.dataset.matrixRainTrail = String(rainSettings.trail);
    synchronizeRainControls();

    if (rebuild && cssWidth > 0 && cssHeight > 0) {
      columns = createColumns();
      telemetryPackets = createTelemetryPackets();
    }
    if (isMatrixActive() && reducedMotion.matches && !forcedColors.matches) {
      configureSurface();
      drawStaticFrame();
    }
  }

  function selectRainSetting(name, value, {persist = true} = {}) {
    const definition = RAIN_SETTING_DEFINITIONS[name];
    if (!definition) {
      return;
    }
    rainSettings = {
      ...rainSettings,
      [name]: normalizedRainSetting(value, definition),
    };
    if (persist) {
      writeStoredRainSetting(name, rainSettings[name]);
    }
    applyRainSettings({rebuild: name === "density"});
  }

  function resetRainSettings() {
    Object.keys(RAIN_SETTING_DEFINITIONS).forEach(removeStoredRainSetting);
    rainSettings = Object.fromEntries(Object.entries(RAIN_SETTING_DEFINITIONS).map(
      ([name, definition]) => [name, definition.defaultValue],
    ));
    applyRainSettings({rebuild: true});
  }

  function bindRainControls() {
    Object.entries(RAIN_SETTING_DEFINITIONS).forEach(([name, definition]) => {
      const control = document.getElementById(definition.controlId);
      if (!(control instanceof HTMLInputElement)) {
        return;
      }
      control.addEventListener("input", () => {
        selectRainSetting(name, control.value);
      });
    });
    document.getElementById("matrix-rain-reset")?.addEventListener(
      "click",
      resetRainSettings,
    );
    applyRainSettings();
  }

  function stopAnimation() {
    if (animationFrame !== 0) {
      globalThis.cancelAnimationFrame(animationFrame);
      animationFrame = 0;
    }
    lastFrameAt = 0;
  }

  function scheduleAnimation() {
    if (animationFrame === 0) {
      animationFrame = globalThis.requestAnimationFrame(drawFrame);
    }
  }

  function drawFrame(timestamp) {
    animationFrame = 0;
    if (
      !isMatrixActive()
      || reducedMotion.matches
      || forcedColors.matches
      || document.hidden
    ) {
      syncMode();
      return;
    }

    configureSurface();
    if (lastFrameAt === 0) {
      lastFrameAt = timestamp;
    }
    const elapsed = Math.min(100, Math.max(0, timestamp - lastFrameAt));
    if (elapsed >= FRAME_INTERVAL_MS) {
      drawAnimatedFrame(elapsed);
      lastFrameAt = timestamp;
    }
    scheduleAnimation();
  }

  function syncMode() {
    stopAnimation();
    synchronizeRainControlVisibility();

    if (!isMatrixActive() || forcedColors.matches) {
      canvas.dataset.matrixRainMode = "off";
      context.clearRect(0, 0, cssWidth || canvas.width, cssHeight || canvas.height);
      return;
    }

    configureSurface();
    if (reducedMotion.matches) {
      canvas.dataset.matrixRainMode = "static";
      drawStaticFrame();
      return;
    }

    if (document.hidden) {
      canvas.dataset.matrixRainMode = "paused";
      return;
    }

    canvas.dataset.matrixRainMode = "animated";
    context.clearRect(0, 0, cssWidth, cssHeight);
    scheduleAnimation();
  }

  function handleResize() {
    if (!isMatrixActive() || forcedColors.matches) {
      return;
    }
    if (configureSurface() && reducedMotion.matches) {
      drawStaticFrame();
    }
  }

  function listenForMediaChange(query) {
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", syncMode);
    } else if (typeof query.addListener === "function") {
      query.addListener(syncMode);
    }
  }

  canvas.dataset.matrixRain = "ready";
  bindRainControls();
  refreshLiveSequences();

  if (typeof MutationObserver === "function") {
    const themeObserver = new MutationObserver(syncMode);
    themeObserver.observe(root, {attributes: true, attributeFilter: ["data-theme"]});

    const liveObserver = new MutationObserver(refreshLiveSequences);
    LIVE_FIELD_IDS.forEach((identifier) => {
      const field = document.getElementById(identifier);
      if (field) {
        liveObserver.observe(field, {childList: true, characterData: true, subtree: true});
      }
    });
  }

  if (typeof ResizeObserver === "function") {
    new ResizeObserver(handleResize).observe(stage);
  } else {
    globalThis.addEventListener("resize", handleResize, {passive: true});
  }

  document.addEventListener("visibilitychange", syncMode);
  listenForMediaChange(reducedMotion);
  listenForMediaChange(forcedColors);
  syncMode();
})();
