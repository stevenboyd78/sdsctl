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
  const MATRIX_GLYPHS = Object.freeze(Array.from(
    "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜ0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
  ));
  const FRAME_INTERVAL_MS = 1000 / 24;
  const MAX_COLUMNS = 144;
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
  let liveGlyphs = [];
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

  function refreshLiveGlyphs() {
    liveSequences = readLiveSequences();
    liveGlyphs = Array.from(liveSequences.join("/"));
    canvas.dataset.matrixRainSources = String(liveSequences.length);
    if (isMatrixActive() && reducedMotion.matches && !forcedColors.matches) {
      configureSurface();
      drawStaticFrame();
    }
  }

  function surfaceSeed() {
    return hashString(`${cssWidth}x${cssHeight}|${liveSequences.join("|")}|sdsctl-matrix-rain-v1`);
  }

  function createColumns() {
    const random = seededRandom(surfaceSeed());
    const count = Math.min(
      MAX_COLUMNS,
      Math.max(12, Math.ceil(cssWidth / Math.max(12, fontSize * 0.88))),
    );
    const spacing = cssWidth / count;
    return Array.from({length: count}, (_, index) => ({
      index,
      x: spacing * (index + 0.5),
      y: -random() * cssHeight,
      speed: fontSize * (1.4 + random() * 2.8),
      glyphOffset: Math.floor(random() * MATRIX_GLYPHS.length),
      liveOffset: liveGlyphs.length > 0 ? Math.floor(random() * liveGlyphs.length) : 0,
      step: Math.floor(random() * 31),
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
    return true;
  }

  function matrixGlyph(column, step) {
    if (liveGlyphs.length > 0 && (column.index * 3 + step) % 11 === 0) {
      return liveGlyphs[(column.liveOffset + step) % liveGlyphs.length];
    }
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

  function drawTelemetryGhosts() {
    if (liveSequences.length === 0) {
      return;
    }
    const smallFont = Math.max(9, Math.round(fontSize * 0.55));
    context.font = `600 ${smallFont}px ui-monospace, monospace`;
    context.shadowBlur = 4;
    context.shadowColor = "rgba(114, 245, 143, 0.4)";
    context.fillStyle = "rgba(114, 245, 143, 0.2)";
    context.textAlign = "left";
    context.textBaseline = "alphabetic";
    liveSequences.slice(0, 6).forEach((sequence, index) => {
      const x = index % 2 === 0 ? fontSize : Math.max(fontSize, cssWidth * 0.57);
      const y = fontSize * (2.2 + index * 2.6);
      context.fillText(sequence, x, y, Math.max(fontSize * 8, cssWidth * 0.38));
    });
    context.textAlign = "center";
    context.textBaseline = "middle";
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
    drawTelemetryGhosts();
    context.shadowBlur = 0;
  }

  function drawAnimatedFrame(elapsedMs) {
    context.shadowBlur = 0;
    context.fillStyle = "rgba(2, 7, 5, 0.12)";
    context.fillRect(0, 0, cssWidth, cssHeight);

    columns.forEach((column) => {
      column.y += column.speed * elapsedMs / 1000;
      if (column.y > cssHeight + fontSize) {
        column.y = -fontSize * (1 + (column.index % 9));
        column.step += 1;
      }
      const step = column.step + Math.floor(column.y / Math.max(1, fontSize));
      prepareText(0.82, (column.index + step) % 9 === 0);
      context.fillText(matrixGlyph(column, step), column.x, column.y);
    });
    context.shadowBlur = 0;
  }

  function isMatrixActive() {
    return root.dataset.theme === "matrix";
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
  refreshLiveGlyphs();

  if (typeof MutationObserver === "function") {
    const themeObserver = new MutationObserver(syncMode);
    themeObserver.observe(root, {attributes: true, attributeFilter: ["data-theme"]});

    const liveObserver = new MutationObserver(refreshLiveGlyphs);
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
