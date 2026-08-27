#!/usr/bin/env node

/**
 * Run the Milestone 27.3 dashboard acceptance matrix in one real Chrome.
 *
 * This intentionally uses only Node built-ins and Chrome DevTools Protocol.
 * It writes no screenshots; the nine-image documentation gallery remains the
 * responsibility of generate_web_dashboard_screenshots.py.
 */

import {spawn} from "node:child_process";
import {constants as fsConstants} from "node:fs";
import {access, mkdtemp, rm, writeFile} from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import {fileURLToPath, pathToFileURL} from "node:url";

export const THEMES = Object.freeze([
  "system",
  "lcars",
  "matrix",
  "first-responder",
  "amateur-radio",
  "pip-boy-inspired",
]);

export const VIEWPORTS = Object.freeze([
  Object.freeze({name: "full-hd", width: 1920, height: 1080, dpr: 1}),
  Object.freeze({name: "laptop", width: 1366, height: 768, dpr: 1}),
  Object.freeze({name: "scanner-landscape", width: 800, height: 480, dpr: 1}),
  Object.freeze({name: "phone", width: 390, height: 844, dpr: 2}),
]);

export const PANES = Object.freeze([
  "scanner",
  "controls",
  "audio",
  "recordings",
  "diagnostics",
]);

const RADIO_FIELD_IDS = Object.freeze([
  "radio-mode",
  "radio-screen-raw",
  "radio-screen",
  "radio-system",
  "radio-department",
  "radio-site",
  "radio-system-index",
  "radio-system-hold",
  "radio-department-index",
  "radio-department-hold",
  "radio-site-index",
  "radio-site-hold",
  "radio-channel",
  "radio-channel-index",
  "radio-channel-number",
  "radio-channel-kind",
  "radio-channel-hold",
  "radio-frequency",
  "radio-modulation",
  "radio-sub-audio-detected",
  "radio-tone-out-tone-a",
  "radio-tone-out-tone-b",
  "radio-weather-mode",
  "radio-weather-same",
  "radio-service-type",
  "radio-talkgroup-id",
  "radio-unit-id",
  "radio-volume",
  "radio-squelch",
  "radio-signal",
  "radio-rssi",
  "radio-battery",
  "radio-p25-status",
  "radio-mute",
  "radio-recording",
]);

const DEFAULT_TIMEOUT_MS = 20_000;
const SCREENSHOT_STATUS_MESSAGE = "Daemon and scanner status are available.";
const HELP = `\
Usage: node scripts/audit_web_dashboard_browser.mjs [options]

Run the deterministic Milestone 27.3 browser acceptance audit in one local
Chrome/Chromium session. The audit writes no PNGs. It covers all 120 built-in
theme × reference CSS viewport × workspace pane cases, plus media-preference,
enlarged-text, pagination-focus, trusted Tab/Shift+Tab traversal, WCAG AA
contrast, complete adaptive-presentation, DPR-transition, and prefixed-URL
probes.

Options:
  --chrome PATH       Chrome/Chromium executable (auto-detected by default)
  --python PATH       Python used for the demo server (repo .venv by default)
  --base-url URL      Audit an already-running demo server instead of starting one
  --timeout-ms N      Startup and CDP operation timeout (default: 20000)
  --list              List the 120 matrix cases without opening Chrome
  -h, --help          Show this help and exit

The default command requires Node.js 24 or newer, starts the existing fictional-data server from
generate_web_dashboard_screenshots.py, launches one isolated headless Chrome,
performs viewport resizes and DPR changes through Chrome DevTools Protocol, and
then removes only its temporary browser profile. No third-party Node package or
new Python runtime dependency is required.
`;

function parseArguments(argv) {
  const options = {
    baseUrl: null,
    chrome: null,
    help: false,
    list: false,
    python: null,
    timeoutMs: DEFAULT_TIMEOUT_MS,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "-h" || argument === "--help") {
      options.help = true;
    } else if (argument === "--list") {
      options.list = true;
    } else if (["--base-url", "--chrome", "--python", "--timeout-ms"].includes(argument)) {
      const value = argv[index + 1];
      if (value === undefined || value.startsWith("--")) {
        throw new Error(`${argument} requires a value`);
      }
      index += 1;
      if (argument === "--base-url") {
        options.baseUrl = value;
      } else if (argument === "--chrome") {
        options.chrome = value;
      } else if (argument === "--python") {
        options.python = value;
      } else {
        options.timeoutMs = Number(value);
      }
    } else {
      throw new Error(`unknown argument: ${argument}`);
    }
  }

  if (!Number.isInteger(options.timeoutMs) || options.timeoutMs <= 0) {
    throw new Error("--timeout-ms must be a positive integer");
  }
  if (options.baseUrl !== null) {
    const parsed = new URL(options.baseUrl);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      throw new Error("--base-url must use http or https");
    }
    if (parsed.username !== "" || parsed.password !== "") {
      throw new Error("--base-url must not contain credentials");
    }
    parsed.hash = "";
    parsed.search = "";
    options.baseUrl = parsed.toString().replace(/\/$/, "");
  }
  return options;
}

function listCases() {
  let count = 0;
  for (const theme of THEMES) {
    for (const viewport of VIEWPORTS) {
      for (const pane of PANES) {
        count += 1;
        console.log(
          `${String(count).padStart(3, "0")} ` +
            `theme=${theme} viewport=${viewport.width}x${viewport.height}` +
            `@${viewport.dpr} pane=${pane}`,
        );
      }
    }
  }
  console.log(`Matrix cases: ${count}; screenshots written: 0`);
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function requireNode24() {
  const majorVersion = Number.parseInt(process.versions.node.split(".", 1)[0], 10);
  if (majorVersion < 24 || typeof WebSocket !== "function") {
    throw new Error(
      "Node.js with the built-in WebSocket API is required (use Node.js 24 or newer)",
    );
  }
}

async function availablePort() {
  const listener = net.createServer();
  await new Promise((resolve, reject) => {
    listener.once("error", reject);
    listener.listen(0, "127.0.0.1", resolve);
  });
  const address = listener.address();
  if (address === null || typeof address === "string") {
    listener.close();
    throw new Error("could not allocate a loopback port");
  }
  await new Promise((resolve, reject) => {
    listener.close((error) => (error === undefined ? resolve() : reject(error)));
  });
  return address.port;
}

async function isExecutable(candidate) {
  try {
    await access(candidate, fsConstants.X_OK);
    return true;
  } catch {
    return false;
  }
}

async function findExecutable(explicit, candidates, description) {
  const requested = explicit === null ? candidates : [explicit];
  const searchDirectories = (process.env.PATH ?? "").split(path.delimiter);

  for (const candidate of requested) {
    if (candidate.includes(path.sep)) {
      const resolved = path.resolve(candidate);
      if (await isExecutable(resolved)) {
        return resolved;
      }
      continue;
    }
    for (const directory of searchDirectories) {
      if (directory === "") {
        continue;
      }
      const resolved = path.join(directory, candidate);
      if (await isExecutable(resolved)) {
        return resolved;
      }
    }
  }
  throw new Error(`${description} was not found; provide its explicit path`);
}

function captureChildOutput(child) {
  const chunks = [];
  const append = (chunk) => {
    chunks.push(String(chunk));
    while (chunks.join("").length > 16_000) {
      chunks.shift();
    }
  };
  child.stdout?.on("data", append);
  child.stderr?.on("data", append);
  return () => chunks.join("");
}

async function waitForHttp(url, timeoutMs, child = null) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    if (child !== null && child.exitCode !== null) {
      throw new Error(`process exited before ${url} became ready`);
    }
    try {
      const response = await fetch(url, {signal: AbortSignal.timeout(750)});
      if (response.ok) {
        return response;
      }
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await delay(100);
  }
  throw new Error(`timed out waiting for ${url}: ${String(lastError)}`);
}

async function stopChild(child) {
  if (child === null || child.exitCode !== null) {
    return;
  }
  child.kill("SIGTERM");
  const exited = new Promise((resolve) => child.once("exit", resolve));
  const timeout = delay(5_000).then(() => "timeout");
  if ((await Promise.race([exited, timeout])) === "timeout") {
    child.kill("SIGKILL");
    await new Promise((resolve) => child.once("exit", resolve));
  }
}

async function websocketText(data) {
  if (typeof data === "string") {
    return data;
  }
  if (data instanceof Blob) {
    return data.text();
  }
  if (data instanceof ArrayBuffer) {
    return Buffer.from(data).toString("utf8");
  }
  return Buffer.from(data).toString("utf8");
}

class CdpClient {
  constructor(socket, timeoutMs) {
    this.socket = socket;
    this.timeoutMs = timeoutMs;
    this.nextId = 1;
    this.pending = new Map();
    this.eventWaiters = new Map();
    this.listeners = new Map();

    socket.addEventListener("message", (event) => {
      void this.handleMessage(event.data);
    });
    socket.addEventListener("close", () => {
      const error = new Error("Chrome DevTools Protocol connection closed");
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(error);
      }
      this.pending.clear();
    });
  }

  static async connect(url, timeoutMs) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error("timed out connecting to Chrome DevTools Protocol")),
        timeoutMs,
      );
      socket.addEventListener(
        "open",
        () => {
          clearTimeout(timer);
          resolve();
        },
        {once: true},
      );
      socket.addEventListener(
        "error",
        () => {
          clearTimeout(timer);
          reject(new Error("could not connect to Chrome DevTools Protocol"));
        },
        {once: true},
      );
    });
    return new CdpClient(socket, timeoutMs);
  }

  async handleMessage(raw) {
    const message = JSON.parse(await websocketText(raw));
    if (message.id !== undefined) {
      const pending = this.pending.get(message.id);
      if (pending === undefined) {
        return;
      }
      clearTimeout(pending.timer);
      this.pending.delete(message.id);
      if (message.error !== undefined) {
        pending.reject(
          new Error(
            `${pending.method}: ${message.error.message}` +
              (message.error.data === undefined ? "" : ` (${message.error.data})`),
          ),
        );
      } else {
        pending.resolve(message.result ?? {});
      }
      return;
    }

    const method = message.method;
    if (typeof method !== "string") {
      return;
    }
    const waiters = this.eventWaiters.get(method) ?? [];
    this.eventWaiters.delete(method);
    for (const waiter of waiters) {
      clearTimeout(waiter.timer);
      waiter.resolve(message.params ?? {});
    }
    for (const listener of this.listeners.get(method) ?? []) {
      listener(message.params ?? {});
    }
  }

  send(method, params = {}) {
    const id = this.nextId;
    this.nextId += 1;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method}: command timed out`));
      }, this.timeoutMs);
      this.pending.set(id, {method, reject, resolve, timer});
      this.socket.send(JSON.stringify({id, method, params}));
    });
  }

  waitForEvent(method) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const waiters = this.eventWaiters.get(method) ?? [];
        this.eventWaiters.set(
          method,
          waiters.filter((waiter) => waiter.resolve !== resolve),
        );
        reject(new Error(`${method}: event timed out`));
      }, this.timeoutMs);
      const waiters = this.eventWaiters.get(method) ?? [];
      waiters.push({reject, resolve, timer});
      this.eventWaiters.set(method, waiters);
    });
  }

  on(method, listener) {
    const listeners = this.listeners.get(method) ?? [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }

  close() {
    this.socket.close();
  }
}

async function evaluate(cdp, expression, {awaitPromise = true} = {}) {
  const response = await cdp.send("Runtime.evaluate", {
    awaitPromise,
    expression,
    returnByValue: true,
    userGesture: true,
  });
  if (response.exceptionDetails !== undefined) {
    const details = response.exceptionDetails;
    const description =
      details.exception?.description ?? details.text ?? "browser evaluation failed";
    throw new Error(description);
  }
  return response.result?.value;
}

async function frames(cdp, count = 2) {
  await evaluate(
    cdp,
    `new Promise((resolve) => {
      let remaining = ${count};
      const next = () => {
        remaining -= 1;
        if (remaining <= 0) resolve(true);
        else requestAnimationFrame(next);
      };
      requestAnimationFrame(next);
    })`,
  );
}

async function waitForDashboard(cdp, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let state = null;
  while (Date.now() < deadline) {
    state = await evaluate(
      cdp,
      `(() => ({
        readyState: document.readyState,
        theme: document.documentElement.dataset.theme ?? null,
        controller: typeof window.sdsctlTheme?.select === "function",
        status: document.querySelector("#status-badge")?.dataset.state ?? null,
        model: document.querySelector("#scanner-model")?.textContent?.trim() ?? null,
        recordings: document.querySelectorAll("#recordings-list > li").length,
      }))()`,
    );
    if (
      state.readyState === "complete" &&
      state.controller &&
      state.status !== null &&
      state.status !== "loading" &&
      state.model === "SDS200" &&
      state.recordings > 0
    ) {
      await evaluate(cdp, "document.fonts?.ready ?? Promise.resolve(true)");
      await frames(cdp);
      return;
    }
    await delay(100);
  }
  throw new Error(`dashboard did not settle: ${JSON.stringify(state)}`);
}

async function navigate(cdp, url, timeoutMs, {installAuditLibrary = true} = {}) {
  const loaded = cdp.waitForEvent("Page.loadEventFired");
  const result = await cdp.send("Page.navigate", {url});
  if (result.errorText !== undefined) {
    throw new Error(`navigation failed: ${result.errorText}`);
  }
  await loaded;
  await waitForDashboard(cdp, timeoutMs);
  if (installAuditLibrary) {
    await evaluate(
      cdp,
      `window.__sdsctlBrowserAudit = (${browserAuditLibrary.toString()})();`,
    );
  }
}

async function setViewport(cdp, viewport) {
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    deviceScaleFactor: viewport.dpr,
    height: viewport.height,
    mobile: false,
    screenHeight: viewport.height,
    screenWidth: viewport.width,
    width: viewport.width,
  });
  await frames(cdp);
  const actual = await evaluate(
    cdp,
    `({width: innerWidth, height: innerHeight, dpr: devicePixelRatio})`,
  );
  if (
    actual.width !== viewport.width ||
    actual.height !== viewport.height ||
    Math.abs(actual.dpr - viewport.dpr) > 0.001
  ) {
    throw new Error(
      `viewport transition failed: expected ${viewport.width}x${viewport.height}` +
        `@${viewport.dpr}, received ${JSON.stringify(actual)}`,
    );
  }
}

async function pressKey(cdp, key, code, virtualKeyCode, {modifiers = 0} = {}) {
  const common = {
    code,
    key,
    modifiers,
    nativeVirtualKeyCode: virtualKeyCode,
    windowsVirtualKeyCode: virtualKeyCode,
  };
  await cdp.send("Input.dispatchKeyEvent", {type: "rawKeyDown", ...common});
  await cdp.send("Input.dispatchKeyEvent", {type: "keyUp", ...common});
  await frames(cdp);
}

async function clickElement(cdp, selector) {
  const point = await evaluate(
    cdp,
    `(() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!(element instanceof HTMLElement) || element.matches(":disabled")) {
        return null;
      }
      element.scrollIntoView({block: "nearest", inline: "nearest"});
      const rect = element.getBoundingClientRect();
      return {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };
    })()`,
  );
  if (
    point === null ||
    point.x < 0 ||
    point.y < 0 ||
    point.x > Number.MAX_SAFE_INTEGER ||
    point.y > Number.MAX_SAFE_INTEGER
  ) {
    throw new Error(`cannot click ${selector}`);
  }
  await cdp.send("Input.dispatchMouseEvent", {
    button: "left",
    clickCount: 1,
    type: "mousePressed",
    x: point.x,
    y: point.y,
  });
  await cdp.send("Input.dispatchMouseEvent", {
    button: "left",
    clickCount: 1,
    type: "mouseReleased",
    x: point.x,
    y: point.y,
  });
  await frames(cdp);
}

async function activatePane(cdp, pane) {
  await clickElement(cdp, `#pane-tab-${pane}`);
}

function browserAuditLibrary() {
  const tolerance = 1.5;
  // WCAG 2.x AA requires 4.5:1 for ordinary text and permits 3:1 only for
  // large text (24 CSS px, or 18.66 CSS px at bold weight). These thresholds
  // are deliberately applied to browser-computed colors after opacity and
  // ancestor-background compositing; they are not theme-specific tolerances.
  const minimumTextContrast = 4.5;
  const minimumLargeTextContrast = 3;
  const semanticInteractiveSelector = [
    "a[href]",
    "button",
    'input:not([type="hidden"])',
    "select",
    "textarea",
    "audio[controls]",
    "summary",
    '[contenteditable]:not([contenteditable="false"])',
    '[role="button"]',
    '[role="link"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="switch"]',
    '[role="slider"]',
    '[role="spinbutton"]',
    '[role="combobox"]',
    '[role="tab"]',
  ].join(",");

  function label(element) {
    if (element.id !== "") return `#${element.id}`;
    const classes = Array.from(element.classList).slice(0, 2).join(".");
    const text = (element.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 42);
    return `${element.localName}${classes === "" ? "" : `.${classes}`}` +
      `${text === "" ? "" : ` (${JSON.stringify(text)})`}`;
  }

  function rendered(element) {
    if (!(element instanceof Element)) return false;
    for (let current = element; current !== null; current = current.parentElement) {
      const style = getComputedStyle(current);
      if (
        current.hidden ||
        style.display === "none" ||
        style.visibility === "hidden" ||
        style.visibility === "collapse"
      ) {
        return false;
      }
    }
    const rect = element.getBoundingClientRect();
    return rect.width > tolerance && rect.height > tolerance;
  }

  function outside(inner, outer) {
    return (
      inner.left < outer.left - tolerance ||
      inner.top < outer.top - tolerance ||
      inner.right > outer.right + tolerance ||
      inner.bottom > outer.bottom + tolerance
    );
  }

  function viewportRect() {
    return {bottom: innerHeight, left: 0, right: innerWidth, top: 0};
  }

  function directText(element) {
    return Array.from(element.childNodes).some(
      (node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim() !== "",
    );
  }

  function textRect(element) {
    const range = document.createRange();
    range.selectNodeContents(element);
    const rect = range.getBoundingClientRect();
    range.detach();
    return rect;
  }

  function clippingAncestor(element, rect) {
    for (let current = element; current !== null; current = current.parentElement) {
      const style = getComputedStyle(current);
      const currentRect = current.getBoundingClientRect();
      const clipsX = ["clip", "hidden", "scroll", "auto"].includes(style.overflowX);
      const clipsY = ["clip", "hidden", "scroll", "auto"].includes(style.overflowY);
      if (
        (clipsX && (rect.left < currentRect.left - tolerance || rect.right > currentRect.right + tolerance)) ||
        (clipsY && (rect.top < currentRect.top - tolerance || rect.bottom > currentRect.bottom + tolerance))
      ) {
        return current;
      }
    }
    return null;
  }

  function disabledOrInert(element) {
    if (element.closest("[inert]") !== null) return true;
    if (element.getAttribute("aria-disabled") === "true") return true;
    return "disabled" in element && element.disabled;
  }

  function semanticInteractiveElements() {
    return Array.from(document.querySelectorAll(semanticInteractiveSelector)).filter(
      (element) =>
        element instanceof HTMLElement &&
        rendered(element) &&
        !disabledOrInert(element) &&
        element.getAttribute("aria-hidden") !== "true",
    );
  }

  function intentionalRovingTabExclusion(element) {
    return (
      element.matches('[role="tab"][data-workspace-tab]') &&
      element.getAttribute("aria-selected") === "false"
    );
  }

  function parsedComputedColor(value) {
    const normalized = value.trim().toLocaleLowerCase("en-US");
    if (normalized === "transparent") {
      return {alpha: 0, blue: 0, green: 0, red: 0};
    }
    const tokens = normalized.match(/[+-]?(?:\d+\.?\d*|\.\d+)%?/g) ?? [];
    if (normalized.startsWith("rgb") && tokens.length >= 3) {
      const channel = (token) =>
        token.endsWith("%")
          ? (Number.parseFloat(token) * 255) / 100
          : Number.parseFloat(token);
      const alpha =
        tokens.length >= 4
          ? tokens[3].endsWith("%")
            ? Number.parseFloat(tokens[3]) / 100
            : Number.parseFloat(tokens[3])
          : 1;
      return {
        alpha: Math.min(1, Math.max(0, alpha)),
        blue: Math.min(255, Math.max(0, channel(tokens[2]))),
        green: Math.min(255, Math.max(0, channel(tokens[1]))),
        red: Math.min(255, Math.max(0, channel(tokens[0]))),
      };
    }
    if (normalized.startsWith("color(srgb") && tokens.length >= 3) {
      return {
        alpha: tokens.length >= 4 ? Number.parseFloat(tokens[3]) : 1,
        blue: Number.parseFloat(tokens[2]) * 255,
        green: Number.parseFloat(tokens[1]) * 255,
        red: Number.parseFloat(tokens[0]) * 255,
      };
    }
    return null;
  }

  function colorOver(foreground, background) {
    const alpha = foreground.alpha + background.alpha * (1 - foreground.alpha);
    if (alpha <= 0) return {alpha: 0, blue: 0, green: 0, red: 0};
    const channel = (name) =>
      (foreground[name] * foreground.alpha +
        background[name] * background.alpha * (1 - foreground.alpha)) /
      alpha;
    return {
      alpha,
      blue: channel("blue"),
      green: channel("green"),
      red: channel("red"),
    };
  }

  function effectiveBackground(element) {
    let background = {alpha: 0, blue: 0, green: 0, red: 0};
    for (let current = element; current !== null; current = current.parentElement) {
      const layer = parsedComputedColor(getComputedStyle(current).backgroundColor);
      if (layer !== null) background = colorOver(background, layer);
    }
    if (background.alpha < 0.999) {
      background = colorOver(background, {alpha: 1, blue: 255, green: 255, red: 255});
    }
    return background;
  }

  function effectiveOpacity(element) {
    let opacity = 1;
    for (let current = element; current !== null; current = current.parentElement) {
      const style = getComputedStyle(current);
      const ownOpacity = Number.parseFloat(style.opacity);
      if (Number.isFinite(ownOpacity)) opacity *= ownOpacity;
      for (const match of style.filter.matchAll(/opacity\(\s*([\d.]+)(%)?\s*\)/g)) {
        const filterOpacity = Number.parseFloat(match[1]);
        opacity *= match[2] === "%" ? filterOpacity / 100 : filterOpacity;
      }
    }
    return opacity;
  }

  function relativeLuminance(color) {
    const channel = (value) => {
      const srgb = value / 255;
      return srgb <= 0.04045 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
    };
    return (
      0.2126 * channel(color.red) +
      0.7152 * channel(color.green) +
      0.0722 * channel(color.blue)
    );
  }

  function contrastRatio(first, second) {
    const firstLuminance = relativeLuminance(first);
    const secondLuminance = relativeLuminance(second);
    return (
      (Math.max(firstLuminance, secondLuminance) + 0.05) /
      (Math.min(firstLuminance, secondLuminance) + 0.05)
    );
  }

  function readabilityFailures(element, description) {
    const failures = [];
    const style = getComputedStyle(element);
    const opacity = effectiveOpacity(element);
    if (!Number.isFinite(opacity) || opacity <= 0.01) {
      failures.push(`${description} has effective opacity ${opacity}`);
      return failures;
    }
    const textFillColor = style.webkitTextFillColor;
    const foreground = parsedComputedColor(
      typeof textFillColor === "string" && textFillColor !== ""
        ? textFillColor
        : style.color,
    );
    if (foreground === null) {
      failures.push(`${description} has an unparseable computed text color ${style.color}`);
      return failures;
    }
    const background = effectiveBackground(element);
    const paintedForeground = colorOver(
      {...foreground, alpha: foreground.alpha * opacity},
      background,
    );
    const ratio = contrastRatio(paintedForeground, background);
    const fontSize = Number.parseFloat(style.fontSize);
    const parsedWeight = Number.parseInt(style.fontWeight, 10);
    const fontWeight = Number.isFinite(parsedWeight)
      ? parsedWeight
      : style.fontWeight === "bold"
        ? 700
        : 400;
    const largeText =
      fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 700);
    const threshold = largeText ? minimumLargeTextContrast : minimumTextContrast;
    if (!Number.isFinite(ratio) || ratio + 0.001 < threshold) {
      const colorSummary = (color) =>
        `rgb(${color.red.toFixed(0)} ${color.green.toFixed(0)} ${color.blue.toFixed(0)})`;
      failures.push(
        `${description} contrast is ${ratio.toFixed(2)}:1, below the WCAG AA ` +
          `${threshold.toFixed(1)}:1 threshold ` +
          `(computed foreground ${colorSummary(paintedForeground)}, ` +
          `background ${colorSummary(background)}, opacity ${opacity.toFixed(3)})`,
      );
    }
    return failures;
  }

  function interactiveReadabilityFailures() {
    const failures = [];
    for (const control of semanticInteractiveElements()) {
      if (control.matches(".skip-link:not(:focus)")) continue;
      const accessibleText =
        control.getAttribute("aria-label") ??
        control.innerText ??
        control.textContent ??
        "";
      if (accessibleText.trim() === "" && !control.matches("audio[controls]")) {
        failures.push(`${label(control)} has no readable control text`);
        continue;
      }
      failures.push(...readabilityFailures(control, `${label(control)} control text`));
    }
    return failures;
  }

  function radioValueFailures(field) {
    const failures = [];
    if ((field.textContent ?? "").trim() === "") {
      failures.push(`#${field.id} has no authoritative text`);
    }
    const fontSize = Number.parseFloat(getComputedStyle(field).fontSize);
    if (!Number.isFinite(fontSize) || fontSize < 10.5) {
      failures.push(`#${field.id} is unreadably small at ${fontSize}px`);
    }
    failures.push(...readabilityFailures(field, `#${field.id}`));
    const rect = textRect(field);
    const clipping = clippingAncestor(field, rect);
    if (clipping !== null) {
      failures.push(`#${field.id} is clipped by ${label(clipping)}`);
    }
    if (outside(rect, viewportRect())) {
      failures.push(`#${field.id} is outside the viewport`);
    }
    return failures;
  }

  function focusInventory(expectedPane) {
    const failures = paneState(expectedPane);
    for (const element of document.querySelectorAll("[data-sdsctl-audit-focus-key]")) {
      delete element.dataset.sdsctlAuditFocusKey;
    }
    const expected = [];
    for (const [index, control] of semanticInteractiveElements().entries()) {
      if (intentionalRovingTabExclusion(control)) continue;
      const key = control.id === "" ? `semantic-${index}` : `id:${control.id}`;
      control.dataset.sdsctlAuditFocusKey = key;
      if (control.tabIndex < 0) {
        failures.push(
          `${label(control)} is a rendered enabled semantic control excluded from Tab order`,
        );
        continue;
      }
      expected.push({key, label: label(control)});
    }
    if (expected.length === 0) {
      failures.push(`no sequential focus targets were found for ${expectedPane}`);
    }
    return {expected, failures};
  }

  function clearSequentialFocus() {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    scrollTo(0, 0);
    return true;
  }

  function sequentialFocusState() {
    const failures = [];
    const active = document.activeElement;
    if (!(active instanceof HTMLElement) || active === document.body) {
      return {failures, key: null, label: "document body"};
    }
    const key = active.dataset.sdsctlAuditFocusKey ?? null;
    const activeLabel = label(active);
    const rect = active.getBoundingClientRect();
    if (outside(rect, viewportRect())) {
      failures.push(`${activeLabel} is outside the viewport during trusted Tab traversal`);
    }
    const clipping = clippingAncestor(active, rect);
    if (clipping !== null) {
      failures.push(`${activeLabel} is clipped during trusted Tab traversal`);
    }
    failures.push(...readabilityFailures(active, `${activeLabel} focused control text`));
    if (scrollX !== 0 || scrollY !== 0) {
      failures.push(
        `${activeLabel} moved the fixed workspace to ${scrollX},${scrollY} during Tab traversal`,
      );
      scrollTo(0, 0);
    }
    return {failures, key, label: activeLabel};
  }

  function semanticClipping({allowVerticalDocumentScroll = false} = {}) {
    const failures = [];
    const selectors = [
      "h1",
      "h2",
      "h3",
      "p",
      "dt",
      "dd",
      "label",
      "button",
      "a",
      "strong",
      ".recording-item-meta",
      ".status-badge",
    ].join(",");
    for (const element of document.querySelectorAll(selectors)) {
      if (!rendered(element) || element.matches(".skip-link:not(:focus)")) continue;
      if (!directText(element) && element.children.length > 0) continue;
      const rect = textRect(element);
      if (rect.width <= 0 || rect.height <= 0) continue;

      if (
        element.clientWidth > 0 &&
        element.scrollWidth > element.clientWidth + tolerance
      ) {
        failures.push(
          `${label(element)} clips or ellipsizes horizontal semantic text ` +
            `(${element.scrollWidth.toFixed(1)} > ${element.clientWidth.toFixed(1)})`,
        );
      }
      const clipping = clippingAncestor(element, rect);
      if (clipping !== null) {
        failures.push(`${label(element)} text is clipped by ${label(clipping)}`);
      }
      if (!allowVerticalDocumentScroll && outside(rect, viewportRect())) {
        failures.push(`${label(element)} text is outside the CSS viewport`);
      } else if (
        allowVerticalDocumentScroll &&
        (rect.left < -tolerance || rect.right > innerWidth + tolerance)
      ) {
        failures.push(`${label(element)} text causes horizontal document escape`);
      }
    }
    return failures;
  }

  function focusFailures({allowScroll = false} = {}) {
    const failures = [];
    const selector = [
      "a[href]",
      "button",
      "input",
      "select",
      "textarea",
      "audio[controls]",
      "[tabindex]",
    ].join(",");
    const controls = Array.from(document.querySelectorAll(selector)).filter(
      (element) =>
        rendered(element) &&
        !("disabled" in element && element.disabled) &&
        element.tabIndex >= 0 &&
        element.getAttribute("aria-hidden") !== "true",
    );
    for (const control of controls) {
      if (allowScroll) {
        control.scrollIntoView({block: "center", inline: "nearest"});
      }
      control.focus({preventScroll: !allowScroll});
      if (document.activeElement !== control) {
        failures.push(`${label(control)} cannot receive focus`);
        continue;
      }
      const rect = control.getBoundingClientRect();
      if (outside(rect, viewportRect())) {
        failures.push(`${label(control)} cannot be brought inside the viewport`);
      }
      const clipping = clippingAncestor(control, rect);
      if (clipping !== null) {
        failures.push(`${label(control)} is clipped by ${label(clipping)}`);
      }
    }
    return {count: controls.length, failures};
  }

  function paneState(expectedPane) {
    const failures = [];
    const tabs = Array.from(document.querySelectorAll("[data-workspace-tab]"));
    const panes = Array.from(
      document.querySelectorAll(".workspace-pane[data-workspace-pane]"),
    );
    const selected = tabs.filter((tab) => tab.getAttribute("aria-selected") === "true");
    const visible = panes.filter((pane) => !pane.hidden && rendered(pane));
    if (tabs.length !== 5 || panes.length !== 5) {
      failures.push(`expected five tabs and panes; found ${tabs.length} and ${panes.length}`);
    }
    if (selected.length !== 1 || selected[0]?.dataset.workspaceTab !== expectedPane) {
      failures.push(`selected tab does not match ${expectedPane}`);
    }
    if (visible.length !== 1 || visible[0]?.dataset.workspacePane !== expectedPane) {
      failures.push(`visible pane does not match ${expectedPane}`);
    }
    if (document.documentElement.dataset.workspacePane !== expectedPane) {
      failures.push(`document pane state does not match ${expectedPane}`);
    }
    for (const tab of tabs) {
      const expected = tab.dataset.workspaceTab === expectedPane;
      if (tab.tabIndex !== (expected ? 0 : -1)) {
        failures.push(`${label(tab)} has an inconsistent roving tabindex`);
      }
      if ((tab.getAttribute("aria-selected") === "true") !== expected) {
        failures.push(`${label(tab)} has an inconsistent aria-selected state`);
      }
      if (tab.getAttribute("aria-controls") !== `pane-${tab.dataset.workspaceTab}`) {
        failures.push(`${label(tab)} does not control its matching pane`);
      }
    }
    for (const pane of panes) {
      const expected = pane.dataset.workspacePane === expectedPane;
      if (pane.hidden === expected) {
        failures.push(`${label(pane)} has an inconsistent hidden state`);
      }
      if (pane.getAttribute("aria-labelledby") !== `pane-tab-${pane.dataset.workspacePane}`) {
        failures.push(`${label(pane)} is not labelled by its matching tab`);
      }
    }
    return failures;
  }

  function normal(expectedPane, expectedTheme) {
    const failures = paneState(expectedPane);
    const html = document.documentElement;
    const body = document.body;
    const activePane = document.querySelector(
      `.workspace-pane[data-workspace-pane="${expectedPane}"]`,
    );
    if (html.dataset.theme !== expectedTheme) {
      failures.push(`document theme is ${html.dataset.theme}, expected ${expectedTheme}`);
    }
    if (window.sdsctlTheme?.current() !== expectedTheme) {
      failures.push(`theme controller does not report ${expectedTheme}`);
    }
    if (document.querySelector("#theme-select")?.value !== expectedTheme) {
      failures.push(`theme selector does not report ${expectedTheme}`);
    }

    if (html.scrollWidth > html.clientWidth + tolerance) {
      failures.push(`document scrolls horizontally (${html.scrollWidth} > ${html.clientWidth})`);
    }
    if (html.scrollHeight > html.clientHeight + tolerance) {
      failures.push(`document scrolls vertically (${html.scrollHeight} > ${html.clientHeight})`);
    }
    if (body.scrollWidth > body.clientWidth + tolerance) {
      failures.push(`body scrolls horizontally (${body.scrollWidth} > ${body.clientWidth})`);
    }
    if (body.scrollHeight > body.clientHeight + tolerance) {
      failures.push(`body scrolls vertically (${body.scrollHeight} > ${body.clientHeight})`);
    }
    if (activePane instanceof Element) {
      if (activePane.scrollWidth > activePane.clientWidth + tolerance) {
        failures.push(`${label(activePane)} scrolls horizontally`);
      }
      if (activePane.scrollHeight > activePane.clientHeight + tolerance) {
        failures.push(`${label(activePane)} scrolls vertically`);
      }
      if (outside(activePane.getBoundingClientRect(), viewportRect())) {
        failures.push(`${label(activePane)} is outside the viewport`);
      }
    }
    failures.push(...semanticClipping());
    failures.push(...interactiveReadabilityFailures());
    const focus = focusFailures();
    failures.push(...focus.failures);
    const selectedTab = document.querySelector(`#pane-tab-${expectedPane}`);
    selectedTab?.focus({preventScroll: true});
    if (scrollX !== 0 || scrollY !== 0) {
      failures.push(`focus traversal moved the fixed workspace to ${scrollX},${scrollY}`);
      scrollTo(0, 0);
    }
    return {failures, focusableCount: focus.count};
  }

  function switchTheme(theme) {
    const failures = [];
    const audioTab = document.querySelector("#pane-tab-audio");
    audioTab?.click();
    const sentinel = document.querySelector("#audio-play");
    sentinel?.focus({preventScroll: true});
    const select = document.querySelector("#theme-select");
    if (!(select instanceof HTMLSelectElement)) {
      return {failures: ["theme select is unavailable"]};
    }
    select.value = theme;
    select.dispatchEvent(new Event("change", {bubbles: true}));
    if (document.documentElement.dataset.theme !== theme) {
      failures.push(`theme switch did not apply ${theme}`);
    }
    if (window.sdsctlTheme?.current() !== theme || select.value !== theme) {
      failures.push(`theme controller and selector disagree for ${theme}`);
    }
    if (document.documentElement.dataset.workspacePane !== "audio") {
      failures.push(`theme switch changed the active workspace pane`);
    }
    if (document.activeElement !== sentinel) {
      failures.push(`theme switch displaced focus from the active pane`);
    }
    const themeLink = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).find(
      (link) => new URL(link.href).pathname.endsWith(`/themes/${theme}/theme.css`),
    );
    if (!(themeLink instanceof HTMLLinkElement) || themeLink.sheet === null) {
      failures.push(`theme stylesheet for ${theme} is not loaded`);
    }
    return {failures};
  }

  function presentationGeometryFailures(fieldIds, scenario) {
    const failures = paneState("scanner");
    const html = document.documentElement;
    const body = document.body;
    const pane = document.querySelector(
      '.workspace-pane[data-workspace-pane="scanner"]',
    );
    if (html.scrollWidth > html.clientWidth + tolerance) {
      failures.push("document scrolls horizontally");
    }
    if (html.scrollHeight > html.clientHeight + tolerance) {
      failures.push("document scrolls vertically");
    }
    if (body.scrollWidth > body.clientWidth + tolerance) {
      failures.push("body scrolls horizontally");
    }
    if (body.scrollHeight > body.clientHeight + tolerance) {
      failures.push("body scrolls vertically");
    }
    if (pane instanceof Element) {
      if (pane.scrollWidth > pane.clientWidth + tolerance) {
        failures.push(`${label(pane)} scrolls horizontally`);
      }
      if (pane.scrollHeight > pane.clientHeight + tolerance) {
        failures.push(`${label(pane)} scrolls vertically`);
      }
      if (outside(pane.getBoundingClientRect(), viewportRect())) {
        failures.push(`${label(pane)} is outside the viewport`);
      }
    }
    failures.push(...semanticClipping());
    failures.push(...interactiveReadabilityFailures());
    failures.push(...hierarchyFieldAlignmentFailures());
    const visibleFields = fieldIds
      .map((id) => document.getElementById(id))
      .filter((field) => field instanceof HTMLElement && rendered(field));
    if (visibleFields.length === 0) {
      failures.push("automatic presentation exposes no authoritative radio values");
    }
    for (const field of visibleFields) {
      failures.push(...radioValueFailures(field));
    }
    return failures.map((message) => `${scenario}: ${message}`);
  }

  function hierarchyFieldAlignmentFailures() {
    const failures = [];
    for (const field of document.querySelectorAll(
      ".scanner-display-hierarchy .secondary-value",
    )) {
      const minHeight = Number.parseFloat(getComputedStyle(field).minHeight);
      if (!Number.isFinite(minHeight) || minHeight > tolerance) {
        failures.push(
          `${label(field)} retains ${getComputedStyle(field).minHeight} of ` +
            "minimum height and cannot center with the System field",
        );
      }
    }
    return failures;
  }

  function radioFields(fieldIds) {
    const failures = [];
    const seen = new Set();
    const views = ["hierarchy", "rf", "identity", "special"];
    if (fieldIds.length !== 35 || new Set(fieldIds).size !== 35) {
      failures.push(`audit field inventory is not exactly 35 unique targets`);
    }
    for (const id of fieldIds) {
      if (!(document.getElementById(id) instanceof HTMLElement)) {
        failures.push(`#${id} is missing`);
      }
    }

    for (const view of views) {
      const button = document.querySelector(`#radio-view-${view}`);
      button?.focus({preventScroll: true});
      button?.click();
      const pressed = Array.from(document.querySelectorAll("[data-radio-view][aria-pressed]"))
        .filter((candidate) => candidate.getAttribute("aria-pressed") === "true");
      if (pressed.length !== 1 || pressed[0]?.dataset.radioView !== view) {
        failures.push(`radio inspection button state does not match ${view}`);
      }
      const groups = Array.from(document.querySelectorAll("[data-radio-group]"));
      const visibleGroups = groups.filter((group) => !group.hidden && rendered(group));
      if (visibleGroups.length !== 1 || visibleGroups[0]?.dataset.radioGroup !== view) {
        failures.push(`radio field group state does not match ${view}`);
      }

      for (const id of fieldIds) {
        const field = document.getElementById(id);
        if (!(field instanceof HTMLElement) || !rendered(field)) continue;
        seen.add(id);
        failures.push(...radioValueFailures(field));
      }
    }
    const missing = fieldIds.filter((id) => !seen.has(id));
    if (missing.length > 0) {
      failures.push(`radio view controls cannot reveal: ${missing.join(", ")}`);
    }

    const profiles = [
      ["simple", "scanning", "scanning", "Now scanning", "simple", "none"],
      ["detail", "scanning", "scanning", "Now scanning", "detail", "hierarchy"],
      [null, "search", "search", "Quick Search", "search", "rf"],
      [null, "close_call", "close_call", "Close Call", "search", "rf"],
      [null, "weather", "weather", "Weather", "weather", "special"],
      [null, "tone_out", "tone_out", "Tone-Out", "tone_out", "special"],
      [null, "toString", "unknown", "Scanner activity", "detail", "hierarchy"],
    ];
    document.querySelector("#radio-view-auto")?.click();
    for (const [fallback, input, kind, title, layout, group] of profiles) {
      if (fallback !== null) {
        const fallbackSelect = document.querySelector("#radio-scan-fallback-select");
        if (!(fallbackSelect instanceof HTMLSelectElement)) {
          failures.push("scan fallback selector is unavailable");
        } else {
          fallbackSelect.value = fallback;
          fallbackSelect.dispatchEvent(new Event("change", {bubbles: true}));
          if (fallbackSelect.value !== fallback) {
            failures.push(`scan fallback did not select ${fallback}`);
          }
        }
      }
      renderRadioProfile(input);
      const panel = document.querySelector("#radio-activity-panel");
      const actualTitle = document.querySelector("#activity-title")?.textContent?.trim();
      if (
        panel?.dataset.screenKind !== kind ||
        actualTitle !== title ||
        panel?.dataset.displayLayout !== layout ||
        panel?.dataset.activeRadioGroup !== group
      ) {
        failures.push(
          `adaptive screen mapping failed for ${input}: ` +
            JSON.stringify({
              group: panel?.dataset.activeRadioGroup,
              kind: panel?.dataset.screenKind,
              layout: panel?.dataset.displayLayout,
              title: actualTitle,
            }),
        );
      }
      failures.push(
        ...presentationGeometryFailures(
          fieldIds,
          `${fallback ?? "detail"}/${input}`,
        ),
      );
    }
    const fallbackSelect = document.querySelector("#radio-scan-fallback-select");
    if (fallbackSelect instanceof HTMLSelectElement) {
      fallbackSelect.value = "detail";
      fallbackSelect.dispatchEvent(new Event("change", {bubbles: true}));
    }
    renderRadioProfile("scanning");
    document.querySelector("#radio-view-auto")?.click();
    return {failures, revealedFieldCount: seen.size};
  }

  function paginationState(expected) {
    const previous = document.querySelector("#recordings-previous-page");
    const next = document.querySelector("#recordings-next-page");
    const state = {
      active: document.activeElement?.id ?? null,
      items: document.querySelectorAll("#recordings-list > li").length,
      nextDisabled: next?.disabled ?? null,
      previousDisabled: previous?.disabled ?? null,
      status: document.querySelector("#recordings-page-status")?.textContent?.trim() ?? null,
    };
    const failures = [];
    for (const [key, value] of Object.entries(expected)) {
      if (state[key] !== value) {
        failures.push(`pagination ${key} is ${JSON.stringify(state[key])}, expected ${JSON.stringify(value)}`);
      }
    }
    return {failures, state};
  }

  function keyboardTabState(expectedPane) {
    const failures = paneState(expectedPane);
    const expectedTab = document.querySelector(`#pane-tab-${expectedPane}`);
    if (document.activeElement !== expectedTab) {
      failures.push(`keyboard navigation did not focus the ${expectedPane} tab`);
    }
    return {failures};
  }

  function resetPagination() {
    const previous = document.querySelector("#recordings-previous-page");
    let guard = 10;
    while (previous instanceof HTMLButtonElement && !previous.disabled && guard > 0) {
      previous.click();
      guard -= 1;
    }
    return paginationState({
      items: 3,
      nextDisabled: false,
      previousDisabled: true,
      status: "Page 1 of 3",
    });
  }

  function reducedMotion() {
    const failures = [];
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
      failures.push("prefers-reduced-motion did not become active");
    }
    function seconds(value) {
      return value.split(",").reduce((maximum, token) => {
        const trimmed = token.trim();
        const numeric = Number.parseFloat(trimmed);
        if (!Number.isFinite(numeric)) return maximum;
        const converted = trimmed.endsWith("ms") ? numeric / 1000 : numeric;
        return Math.max(maximum, converted);
      }, 0);
    }
    for (const element of document.querySelectorAll("*")) {
      if (!rendered(element)) continue;
      const style = getComputedStyle(element);
      if (
        style.animationName !== "none" &&
        (seconds(style.animationDuration) > 0.0001 ||
          Number.parseFloat(style.animationIterationCount) > 1)
      ) {
        failures.push(`${label(element)} retains motion animation`);
      }
      if (seconds(style.transitionDuration) > 0.0001) {
        failures.push(`${label(element)} retains a visible transition`);
      }
    }
    return {failures};
  }

  function forcedColors(fieldIds) {
    const failures = [];
    if (!matchMedia("(forced-colors: active)").matches) {
      failures.push("forced-colors did not become active");
    }
    if (!matchMedia("(prefers-contrast: more)").matches) {
      failures.push("prefers-contrast: more did not become active");
    }
    const stage = document.querySelector(".theme-stage");
    if (!(stage instanceof Element) || getComputedStyle(stage).display !== "none") {
      failures.push("decorative theme stage remains visible in forced colors");
    }
    const selected = document.querySelector('[role="tab"][aria-selected="true"]');
    if (selected instanceof Element) {
      const style = getComputedStyle(selected);
      if (style.color === style.backgroundColor) {
        failures.push("selected tab has identical forced foreground and background colors");
      }
    }
    failures.push(...interactiveReadabilityFailures());
    for (const id of fieldIds) {
      const field = document.getElementById(id);
      if (field instanceof HTMLElement && rendered(field)) {
        failures.push(...radioValueFailures(field));
      }
    }
    return {failures};
  }

  function enlargedText(expectedPane) {
    const failures = paneState(expectedPane);
    const html = document.documentElement;
    const body = document.body;
    if (innerWidth > 320 || devicePixelRatio < 2) {
      failures.push("the 200%-zoom CSS viewport proxy is not active");
    }
    if (html.scrollWidth > html.clientWidth + tolerance) {
      failures.push(`enlarged text creates horizontal document scroll`);
    }
    if (html.scrollHeight <= html.clientHeight + tolerance) {
      failures.push(`enlarged text did not activate vertical scrolling escape`);
    }
    if (!["auto", "scroll", "visible"].includes(getComputedStyle(html).overflowY)) {
      failures.push(`document scrolling escape remains ${getComputedStyle(html).overflowY}`);
    }
    if (getComputedStyle(body).overflowY === "hidden") {
      failures.push("body still hides overflow during enlarged-text escape");
    }
    failures.push(...semanticClipping({allowVerticalDocumentScroll: true}));
    const focus = focusFailures({allowScroll: true});
    failures.push(...focus.failures);
    return {failures, focusableCount: focus.count};
  }

  function prefixedUrls(expectedPrefix) {
    const failures = [];
    const sameOriginResources = performance
      .getEntriesByType("resource")
      .map((entry) => new URL(entry.name))
      .filter((url) => url.origin === location.origin);
    const relevant = sameOriginResources.filter((url) =>
      ["/assets/", "/api/v1/"].some((marker) => url.pathname.includes(marker)),
    );
    for (const resource of relevant) {
      if (!resource.pathname.startsWith(expectedPrefix)) {
        failures.push(`resource escaped URL prefix: ${resource.pathname}`);
      }
    }
    for (const suffix of [
      "assets/dashboard.css",
      "assets/dashboard.js",
      "api/v1/status",
      "api/v1/recording",
      "api/v1/recordings",
    ]) {
      if (!relevant.some((url) => url.pathname === `${expectedPrefix}${suffix}`)) {
        failures.push(`prefixed resource was not requested: ${suffix}`);
      }
    }
    const recordingLink = document.querySelector("#recordings-list a[href]");
    if (
      !(recordingLink instanceof HTMLAnchorElement) ||
      !new URL(recordingLink.href).pathname.startsWith(`${expectedPrefix}api/v1/`)
    ) {
      failures.push("recording action URL does not preserve the prefix");
    }
    if (document.querySelector("#scanner-model")?.textContent?.trim() !== "SDS200") {
      failures.push("prefixed API requests did not populate scanner state");
    }
    return {failures, resourceCount: relevant.length};
  }

  return Object.freeze({
    clearSequentialFocus,
    enlargedText,
    focusInventory,
    forcedColors,
    keyboardTabState,
    normal,
    paginationState,
    prefixedUrls,
    radioFields,
    reducedMotion,
    resetPagination,
    sequentialFocusState,
    switchTheme,
  });
}

class FailureCollector {
  constructor() {
    this.failures = [];
    this.seen = new Set();
  }

  add(context, result) {
    for (const message of result?.failures ?? []) {
      const key = `${context}\u0000${message}`;
      if (this.seen.has(key)) continue;
      this.seen.add(key);
      this.failures.push({context, message});
    }
  }
}

async function auditAccessibility(cdp, collector, context, expectedPane) {
  const {nodes = []} = await cdp.send("Accessibility.getFullAXTree");
  const exposed = nodes.filter((node) => node.ignored !== true);
  const role = (node) => node.role?.value ?? null;
  const name = (node) => node.name?.value ?? "";
  const normalizedName = (node) => name(node).trim().toLocaleLowerCase("en-US");
  const property = (node, propertyName) =>
    node.properties?.find((candidate) => candidate.name === propertyName)?.value?.value;
  const failures = [];
  const tabs = exposed.filter((node) => role(node) === "tab");
  const expectedTabNames = ["scanner", "controls", "audio", "recordings", "diagnostics"];
  if (tabs.length !== 5) {
    failures.push(`accessibility tree exposes ${tabs.length} tabs instead of five`);
  }
  if (
    JSON.stringify(tabs.map(normalizedName).sort()) !==
    JSON.stringify(expectedTabNames.sort())
  ) {
    failures.push(`accessibility tab names are incomplete: ${JSON.stringify(tabs.map(name))}`);
  }
  const selectedTabs = tabs.filter((tab) => property(tab, "selected") === true);
  const expectedName = expectedPane[0].toUpperCase() + expectedPane.slice(1);
  if (selectedTabs.length !== 1 || normalizedName(selectedTabs[0]) !== expectedPane) {
    failures.push(`accessibility selected tab does not match ${expectedName}`);
  }
  if (property(selectedTabs[0] ?? {}, "focused") !== true) {
    failures.push(`accessibility selected tab is not the restored focus target`);
  }
  const tabPanels = exposed.filter((node) => role(node) === "tabpanel");
  if (tabPanels.length !== 1 || normalizedName(tabPanels[0]) !== expectedPane) {
    failures.push(
      `accessibility tree does not expose exactly the active ${expectedName} tabpanel`,
    );
  }
  const themeSelect = exposed.find(
    (node) => role(node) === "combobox" && normalizedName(node) === "theme",
  );
  if (themeSelect === undefined) {
    failures.push("accessibility tree does not expose the named Theme selector");
  }
  const unnamedButtons = exposed.filter(
    (node) => role(node) === "button" && name(node).trim() === "",
  );
  if (unnamedButtons.length > 0) {
    failures.push(`accessibility tree exposes ${unnamedButtons.length} unnamed buttons`);
  }
  collector.add(context, {failures});
}

async function auditKeyboardTabs(cdp, collector, theme) {
  await activatePane(cdp, "scanner");
  await evaluate(cdp, 'document.querySelector("#pane-tab-scanner").focus()');
  await pressKey(cdp, "ArrowRight", "ArrowRight", 39);
  collector.add(
    `${theme}/keyboard-tabs`,
    await evaluate(
      cdp,
      'window.__sdsctlBrowserAudit.keyboardTabState("controls")',
    ),
  );
  await pressKey(cdp, "End", "End", 35);
  collector.add(
    `${theme}/keyboard-tabs`,
    await evaluate(
      cdp,
      'window.__sdsctlBrowserAudit.keyboardTabState("diagnostics")',
    ),
  );
  await pressKey(cdp, "Home", "Home", 36);
  collector.add(
    `${theme}/keyboard-tabs`,
    await evaluate(cdp, 'window.__sdsctlBrowserAudit.keyboardTabState("scanner")'),
  );
}

async function auditTrustedTabDirection(
  cdp,
  collector,
  context,
  expected,
  {reverse = false} = {},
) {
  await evaluate(cdp, "window.__sdsctlBrowserAudit.clearSequentialFocus()");
  const expectedByKey = new Map(expected.map((item) => [item.key, item.label]));
  const seen = new Set();
  const failures = [];
  const direction = reverse ? "Shift+Tab" : "Tab";
  const maximumSteps = Math.max(24, expected.length * 5 + 12);

  for (let step = 0; step < maximumSteps && seen.size < expected.length; step += 1) {
    await pressKey(cdp, "Tab", "Tab", 9, {modifiers: reverse ? 8 : 0});
    const state = await evaluate(
      cdp,
      "window.__sdsctlBrowserAudit.sequentialFocusState()",
    );
    for (const message of state.failures ?? []) {
      failures.push(`${direction}: ${message}`);
    }
    if (state.key === null) {
      if (state.label !== "document body") {
        failures.push(`${direction}: reached an untracked focus target ${state.label}`);
      }
      continue;
    }
    if (!expectedByKey.has(state.key)) {
      failures.push(`${direction}: reached unexpected focus target ${state.label}`);
      continue;
    }
    seen.add(state.key);
  }

  const missing = expected
    .filter((item) => !seen.has(item.key))
    .map((item) => item.label);
  if (missing.length > 0) {
    failures.push(
      `${direction} traversal skipped or trapped before: ${missing.join(", ")}`,
    );
  }
  collector.add(context, {failures});
}

async function auditSequentialFocus(cdp, collector, theme) {
  await setViewport(cdp, {width: 800, height: 480, dpr: 1});
  for (const pane of PANES) {
    await activatePane(cdp, pane);
    const context = `${theme}/800x480@1/trusted-focus/${pane}`;
    const inventory = await evaluate(
      cdp,
      `window.__sdsctlBrowserAudit.focusInventory(${JSON.stringify(pane)})`,
    );
    collector.add(context, inventory);
    if ((inventory.expected ?? []).length === 0) continue;
    await auditTrustedTabDirection(cdp, collector, context, inventory.expected);
    await auditTrustedTabDirection(cdp, collector, context, inventory.expected, {
      reverse: true,
    });
  }
}

async function auditPagination(cdp, collector, context) {
  collector.add(
    context,
    await evaluate(cdp, "window.__sdsctlBrowserAudit.resetPagination()"),
  );
  await clickElement(cdp, "#recordings-next-page");
  collector.add(
    context,
    await evaluate(
      cdp,
      `window.__sdsctlBrowserAudit.paginationState(${JSON.stringify({
        active: "recordings-next-page",
        items: 3,
        nextDisabled: false,
        previousDisabled: false,
        status: "Page 2 of 3",
      })})`,
    ),
  );
  await clickElement(cdp, "#recordings-next-page");
  collector.add(
    context,
    await evaluate(
      cdp,
      `window.__sdsctlBrowserAudit.paginationState(${JSON.stringify({
        active: "recordings-previous-page",
        items: 1,
        nextDisabled: true,
        previousDisabled: false,
        status: "Page 3 of 3",
      })})`,
    ),
  );
  await clickElement(cdp, "#recordings-previous-page");
  collector.add(
    context,
    await evaluate(
      cdp,
      `window.__sdsctlBrowserAudit.paginationState(${JSON.stringify({
        active: "recordings-previous-page",
        items: 3,
        nextDisabled: false,
        previousDisabled: false,
        status: "Page 2 of 3",
      })})`,
    ),
  );
  await clickElement(cdp, "#recordings-previous-page");
  collector.add(
    context,
    await evaluate(
      cdp,
      `window.__sdsctlBrowserAudit.paginationState(${JSON.stringify({
        active: "recordings-next-page",
        items: 3,
        nextDisabled: false,
        previousDisabled: true,
        status: "Page 1 of 3",
      })})`,
    ),
  );

  // Exercise the terminal focus fallback after a real paging interaction.
  // A fresh one-page inventory disables both sibling paging buttons, so the
  // production renderer must move focus to the stable Refresh control.
  await clickElement(cdp, "#recordings-next-page");
  const inventory = await evaluate(
    cdp,
    `fetch(new URL("api/v1/recordings", document.baseURI))
      .then((response) => response.json())`,
  );
  await evaluate(
    cdp,
    `renderRecordings(${JSON.stringify({
      entries: inventory.recordings.entries.slice(0, 1),
      total_entries: 1,
    })}); true`,
  );
  collector.add(
    context,
    await evaluate(
      cdp,
      `window.__sdsctlBrowserAudit.paginationState(${JSON.stringify({
        active: "recordings-refresh",
        items: 1,
        nextDisabled: true,
        previousDisabled: true,
        status: "Page 1 of 1",
      })})`,
    ),
  );
  await evaluate(
    cdp,
    `renderRecordings(${JSON.stringify(inventory.recordings)}); true`,
  );
}

async function auditMediaPreferences(cdp, collector, theme) {
  const viewport = {width: 800, height: 480, dpr: 1};
  await setViewport(cdp, viewport);
  await activatePane(cdp, "scanner");

  await cdp.send("Emulation.setEmulatedMedia", {
    features: [{name: "prefers-reduced-motion", value: "reduce"}],
    media: "screen",
  });
  await frames(cdp);
  collector.add(
    `${theme}/reduced-motion`,
    await evaluate(cdp, "window.__sdsctlBrowserAudit.reducedMotion()"),
  );

  await cdp.send("Emulation.setEmulatedMedia", {
    features: [
      {name: "forced-colors", value: "active"},
      {name: "prefers-contrast", value: "more"},
    ],
    media: "screen",
  });
  await frames(cdp);
  for (const pane of PANES) {
    await activatePane(cdp, pane);
    const context = `${theme}/forced-colors/${pane}`;
    collector.add(
      context,
      await evaluate(
        cdp,
        `window.__sdsctlBrowserAudit.forcedColors(${JSON.stringify(RADIO_FIELD_IDS)})`,
      ),
    );
    collector.add(
      context,
      await evaluate(
        cdp,
        `window.__sdsctlBrowserAudit.normal(${JSON.stringify(pane)}, ${JSON.stringify(theme)})`,
      ),
    );
  }

  await cdp.send("Emulation.setEmulatedMedia", {features: [], media: "screen"});
  await frames(cdp);
}

async function auditEnlargedText(cdp, collector, theme) {
  await setViewport(cdp, {width: 320, height: 420, dpr: 2});
  await frames(cdp);
  for (const pane of PANES) {
    await evaluate(cdp, "scrollTo(0, 0); true");
    await activatePane(cdp, pane);
    collector.add(
      `${theme}/320x420@2/enlarged/${pane}`,
      await evaluate(
        cdp,
        `window.__sdsctlBrowserAudit.enlargedText(${JSON.stringify(pane)})`,
      ),
    );
  }
  await evaluate(cdp, "scrollTo(0, 0); true");
  await frames(cdp);
}

async function runMatrix(cdp, baseUrl, timeoutMs, pageFailures) {
  const collector = new FailureCollector();
  await setViewport(cdp, VIEWPORTS[0]);
  await navigate(cdp, `${baseUrl}/`, timeoutMs);

  let caseCount = 0;
  for (const theme of THEMES) {
    collector.add(
      `${theme}/theme-switch`,
      await evaluate(
        cdp,
        `window.__sdsctlBrowserAudit.switchTheme(${JSON.stringify(theme)})`,
      ),
    );
    await frames(cdp);
    await auditKeyboardTabs(cdp, collector, theme);
    await auditSequentialFocus(cdp, collector, theme);

    for (const viewport of VIEWPORTS) {
      await setViewport(cdp, viewport);
      for (const pane of PANES) {
        caseCount += 1;
        await activatePane(cdp, pane);
        const context =
          `${theme}/${viewport.width}x${viewport.height}@${viewport.dpr}/${pane}`;
        if (pane === "scanner") {
          collector.add(
            context,
            await evaluate(
              cdp,
              `window.__sdsctlBrowserAudit.radioFields(${JSON.stringify(RADIO_FIELD_IDS)})`,
            ),
          );
        } else if (pane === "recordings") {
          await auditPagination(cdp, collector, context);
        }
        collector.add(
          context,
          await evaluate(
            cdp,
            `window.__sdsctlBrowserAudit.normal(${JSON.stringify(pane)}, ${JSON.stringify(theme)})`,
          ),
        );
        await auditAccessibility(cdp, collector, context, pane);
      }
      console.log(
        `Audited ${theme.padEnd(16)} ${viewport.width}x${viewport.height}` +
          ` DPR ${viewport.dpr}: ${PANES.length} panes`,
      );
    }

    await auditMediaPreferences(cdp, collector, theme);
    await auditEnlargedText(cdp, collector, theme);
  }

  await cdp.send("Emulation.setEmulatedMedia", {features: [], media: "screen"});
  await setViewport(cdp, {width: 800, height: 480, dpr: 1});
  await navigate(cdp, `${baseUrl}/__demo/prefix/`, timeoutMs);
  collector.add(
    "prefixed-url/theme-switch",
    await evaluate(
      cdp,
      `window.__sdsctlBrowserAudit.switchTheme("pip-boy-inspired")`,
    ),
  );
  await activatePane(cdp, "recordings");
  collector.add(
    "prefixed-url/recordings",
    await evaluate(
      cdp,
      `window.__sdsctlBrowserAudit.prefixedUrls("/__demo/prefix/")`,
    ),
  );
  collector.add(
    "prefixed-url/recordings",
    await evaluate(
      cdp,
      `window.__sdsctlBrowserAudit.normal("recordings", "pip-boy-inspired")`,
    ),
  );

  for (const failure of pageFailures) {
    collector.add("browser-runtime", {failures: [failure]});
  }
  return {caseCount, failures: collector.failures};
}

async function openChrome(chrome, profileDirectory, remotePort) {
  const child = spawn(
    chrome,
    [
      "--headless=new",
      "--disable-dev-shm-usage",
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      "--remote-allow-origins=*",
      `--remote-debugging-port=${remotePort}`,
      `--user-data-dir=${profileDirectory}`,
      "--hide-scrollbars",
      "about:blank",
    ],
    {stdio: ["ignore", "pipe", "pipe"]},
  );
  return {child, output: captureChildOutput(child)};
}

async function pageWebSocketUrl(remotePort, timeoutMs, chrome) {
  await waitForHttp(`http://127.0.0.1:${remotePort}/json/version`, timeoutMs, chrome);
  const response = await fetch(`http://127.0.0.1:${remotePort}/json/list`);
  const targets = await response.json();
  const page = targets.find(
    (target) => target.type === "page" && typeof target.webSocketDebuggerUrl === "string",
  );
  if (page === undefined) {
    throw new Error("Chrome did not expose a page DevTools target");
  }
  return page.webSocketDebuggerUrl;
}

export async function captureDashboardScreenshot({
  baseUrl,
  chrome,
  deviceScaleFactor,
  height,
  outputPath,
  profileDirectory,
  settleMs = 0,
  theme,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  width,
}) {
  requireNode24();
  const viewport = {
    dpr: deviceScaleFactor,
    height,
    width,
  };
  const remotePort = await availablePort();
  const opened = await openChrome(chrome, profileDirectory, remotePort);
  const chromeProcess = opened.child;
  let cdp = null;

  try {
    let websocketUrl;
    try {
      websocketUrl = await pageWebSocketUrl(remotePort, timeoutMs, chromeProcess);
    } catch (error) {
      throw new Error(`${error.message}\nChrome output:\n${opened.output()}`);
    }
    cdp = await CdpClient.connect(websocketUrl, timeoutMs);
    await Promise.all([
      cdp.send("Page.enable"),
      cdp.send("Runtime.enable"),
      cdp.send("Network.enable"),
    ]);

    // The screenshot demo intentionally has no daemon event client. Letting the
    // production EventSource reach that 503 would race its two-second restart
    // against fallback status polling and give the visual capture two valid
    // status messages. This capture-only inert source opens asynchronously,
    // performs no network I/O, and leaves the initial REST status as the sole
    // writer of the deterministic screenshot state. The full browser audit and
    // dashboard behavior tests continue to use the real EventSource lifecycle.
    await cdp.send("Page.addScriptToEvaluateOnNewDocument", {
      source: `(() => {
        const state = {closes: 0, instances: 0, opens: 0, urls: []};
        Object.defineProperty(globalThis, "__sdsctlScreenshotEventSource", {
          configurable: false,
          enumerable: false,
          value: state,
          writable: false,
        });

        class ScreenshotEventSource {
          constructor(url) {
            this.onerror = null;
            this.onmessage = null;
            this.onopen = null;
            this.readyState = 0;
            this.url = String(url);
            this.withCredentials = false;
            state.instances += 1;
            state.urls.push(this.url);
            queueMicrotask(() => {
              if (this.readyState !== 0) return;
              this.readyState = 1;
              state.opens += 1;
              if (typeof this.onopen === "function") {
                this.onopen(new Event("open"));
              }
            });
          }

          close() {
            if (this.readyState === 2) return;
            this.readyState = 2;
            state.closes += 1;
          }
        }

        Object.defineProperty(globalThis, "EventSource", {
          configurable: true,
          value: ScreenshotEventSource,
          writable: true,
        });
      })();`,
    });
    const pageFailures = [];
    cdp.on("Runtime.exceptionThrown", (event) => {
      const description =
        event.exceptionDetails?.exception?.description ??
        event.exceptionDetails?.text ??
        "uncaught page exception";
      pageFailures.push(description);
    });

    await setViewport(cdp, viewport);
    await cdp.send("Emulation.setEmulatedMedia", {
      features: [{name: "prefers-reduced-motion", value: "reduce"}],
      media: "screen",
    });
    const captureUrl = `${baseUrl}/__demo/theme/${encodeURIComponent(theme)}`;
    await navigate(cdp, captureUrl, timeoutMs, {installAuditLibrary: false});
    await setViewport(cdp, viewport);
    const initialMessage = await evaluate(
      cdp,
      `(() => {
        const target = document.querySelector("#dashboard-message");
        if (target === null) return null;
        const state = {
          initialMessage: target.textContent.trim(),
          mutations: 0,
        };
        const observer = new MutationObserver((records) => {
          state.mutations += records.length;
        });
        observer.observe(target, {characterData: true, childList: true, subtree: true});
        Object.defineProperty(globalThis, "__sdsctlScreenshotMessageStability", {
          configurable: false,
          enumerable: false,
          value: {observer, state},
          writable: false,
        });
        return state.initialMessage;
      })()`,
    );
    if (initialMessage !== SCREENSHOT_STATUS_MESSAGE) {
      throw new Error(
        `capture reached an unexpected initial dashboard message: ${initialMessage}`,
      );
    }
    if (settleMs > 0) {
      await delay(settleMs);
      await waitForDashboard(cdp, timeoutMs);
    }
    await evaluate(cdp, "document.fonts?.ready ?? Promise.resolve(true)");
    await frames(cdp);

    const geometry = await evaluate(
      cdp,
      `(() => ({
        width: innerWidth,
        height: innerHeight,
        dpr: devicePixelRatio,
        visualWidth: visualViewport?.width ?? null,
        visualHeight: visualViewport?.height ?? null,
        visualScale: visualViewport?.scale ?? null,
        reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
      }))()`,
    );
    if (
      geometry.width !== width ||
      geometry.height !== height ||
      Math.abs(geometry.dpr - deviceScaleFactor) > 0.001 ||
      Math.abs(geometry.visualWidth - width) > 0.01 ||
      Math.abs(geometry.visualHeight - height) > 0.01 ||
      Math.abs(geometry.visualScale - 1) > 0.001 ||
      geometry.reducedMotion !== true
    ) {
      throw new Error(
        `capture viewport/media mismatch: expected ${width}x${height}` +
          `@${deviceScaleFactor} reduced motion, received ${JSON.stringify(geometry)}`,
      );
    }

    const captureState = await evaluate(
      cdp,
      `(() => {
        const eventSource = globalThis.__sdsctlScreenshotEventSource;
        const message = globalThis.__sdsctlScreenshotMessageStability?.state;
        return {
          eventSource: eventSource === undefined ? null : {
            closes: eventSource.closes,
            instances: eventSource.instances,
            opens: eventSource.opens,
            urls: [...eventSource.urls],
          },
          message: message === undefined ? null : {
            current: document.querySelector("#dashboard-message")?.textContent?.trim() ?? null,
            initial: message.initialMessage,
            mutations: message.mutations,
          },
        };
      })()`,
    );
    const expectedEventUrl = `${baseUrl.replace(/\/+$/, "")}/api/v1/events`;
    if (
      captureState.eventSource?.instances !== 1 ||
      captureState.eventSource?.opens !== 1 ||
      captureState.eventSource?.closes !== 0 ||
      captureState.eventSource?.urls?.length !== 1 ||
      captureState.eventSource.urls[0] !== expectedEventUrl
    ) {
      throw new Error(
        `capture EventSource shim lifecycle mismatch: ${JSON.stringify(captureState.eventSource)}`,
      );
    }
    if (
      captureState.message?.initial !== SCREENSHOT_STATUS_MESSAGE ||
      captureState.message?.current !== SCREENSHOT_STATUS_MESSAGE ||
      captureState.message?.mutations !== 0
    ) {
      throw new Error(
        `capture dashboard message was not stable: ${JSON.stringify(captureState.message)}`,
      );
    }

    const outerHTML = await evaluate(cdp, "document.documentElement.outerHTML");
    const screenshot = await cdp.send("Page.captureScreenshot", {
      captureBeyondViewport: false,
      format: "png",
      fromSurface: true,
      optimizeForSpeed: false,
    });
    const finalOuterHTML = await evaluate(cdp, "document.documentElement.outerHTML");
    if (outerHTML !== finalOuterHTML) {
      throw new Error("dashboard DOM changed while its screenshot frame was captured");
    }
    if (pageFailures.length > 0) {
      throw new Error(`dashboard raised browser exceptions: ${pageFailures.join(" | ")}`);
    }
    if (typeof screenshot.data !== "string" || screenshot.data.length === 0) {
      throw new Error("Page.captureScreenshot returned no PNG data");
    }
    const png = Buffer.from(screenshot.data, "base64");
    await writeFile(outputPath, png);
    return {
      outerHTML: finalOuterHTML,
      pngBytes: png.length,
      viewport: geometry,
    };
  } finally {
    cdp?.close();
    await stopChild(chromeProcess);
  }
}

async function run(options) {
  requireNode24();
  const scriptPath = fileURLToPath(import.meta.url);
  const repositoryRoot = path.dirname(path.dirname(scriptPath));
  const chrome = await findExecutable(
    options.chrome,
    ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
    "Chrome/Chromium",
  );
  let demoServer = null;
  let demoOutput = () => "";
  let baseUrl = options.baseUrl;
  let temporaryRoot = null;
  let chromeProcess = null;
  let chromeOutput = () => "";
  let cdp = null;

  try {
    if (baseUrl === null) {
      const python = await findExecutable(
        options.python,
        [path.join(repositoryRoot, ".venv", "bin", "python"), "python3", "python"],
        "Python",
      );
      const serverPort = await availablePort();
      baseUrl = `http://127.0.0.1:${serverPort}`;
      const environment = {...process.env};
      delete environment.PYTHONPATH;
      demoServer = spawn(
        python,
        [
          path.join(repositoryRoot, "scripts", "generate_web_dashboard_screenshots.py"),
          "--serve",
          "--port",
          String(serverPort),
        ],
        {cwd: repositoryRoot, env: environment, stdio: ["ignore", "pipe", "pipe"]},
      );
      demoOutput = captureChildOutput(demoServer);
      try {
        await waitForHttp(`${baseUrl}/healthz`, options.timeoutMs, demoServer);
      } catch (error) {
        throw new Error(`${error.message}\nDemo server output:\n${demoOutput()}`);
      }
    } else {
      await waitForHttp(`${baseUrl}/healthz`, options.timeoutMs);
    }

    temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "sdsctl-browser-audit-"));
    const remotePort = await availablePort();
    const opened = await openChrome(chrome, path.join(temporaryRoot, "profile"), remotePort);
    chromeProcess = opened.child;
    chromeOutput = opened.output;
    let websocketUrl;
    try {
      websocketUrl = await pageWebSocketUrl(remotePort, options.timeoutMs, chromeProcess);
    } catch (error) {
      throw new Error(`${error.message}\nChrome output:\n${chromeOutput()}`);
    }

    cdp = await CdpClient.connect(websocketUrl, options.timeoutMs);
    await Promise.all([
      cdp.send("Page.enable"),
      cdp.send("Runtime.enable"),
      cdp.send("Network.enable"),
      cdp.send("Accessibility.enable"),
    ]);
    const pageFailures = [];
    cdp.on("Runtime.exceptionThrown", (event) => {
      const description =
        event.exceptionDetails?.exception?.description ??
        event.exceptionDetails?.text ??
        "uncaught page exception";
      pageFailures.push(description);
    });

    console.log(`Chrome: ${chrome}`);
    console.log(`Demo server: ${baseUrl}`);
    console.log("Screenshots: disabled (the nine-image gallery is unchanged)");
    const result = await runMatrix(cdp, baseUrl, options.timeoutMs, pageFailures);
    if (result.caseCount !== THEMES.length * VIEWPORTS.length * PANES.length) {
      throw new Error(`internal matrix count mismatch: ${result.caseCount}`);
    }
    if (result.failures.length > 0) {
      console.error(`\nBrowser acceptance failed with ${result.failures.length} finding(s):`);
      const reported = result.failures.slice(0, 200);
      for (const [index, failure] of reported.entries()) {
        console.error(
          `${String(index + 1).padStart(3, " ")}. [${failure.context}] ${failure.message}`,
        );
      }
      if (reported.length < result.failures.length) {
        console.error(`... ${result.failures.length - reported.length} additional findings omitted`);
      }
      process.exitCode = 1;
      return;
    }
    console.log(
      `PASS: ${result.caseCount} matrix cases plus theme switching, all 35 radio ` +
        "fields, Simple/Detail and adaptive screens, trusted Tab/Shift+Tab and " +
        "pagination focus, WCAG AA normal/forced-color contrast, reduced motion, " +
        "enlarged-text scrolling escape, DPR changes, and prefixed URLs.",
    );
  } finally {
    cdp?.close();
    await stopChild(chromeProcess);
    await stopChild(demoServer);
    if (temporaryRoot !== null) {
      await rm(temporaryRoot, {force: true, recursive: true});
    }
  }
}

async function main() {
  let options;
  try {
    options = parseArguments(process.argv.slice(2));
  } catch (error) {
    console.error(`ERROR: ${error.message}`);
    console.error("Use --help for usage.");
    process.exitCode = 2;
    return;
  }
  if (options.help) {
    process.stdout.write(HELP);
    return;
  }
  if (options.list) {
    listCases();
    return;
  }
  try {
    await run(options);
  } catch (error) {
    console.error(`ERROR: ${error.stack ?? error.message}`);
    process.exitCode = 1;
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  await main();
}
