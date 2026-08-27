#!/usr/bin/env node

/** Internal exact-viewport CDP capture bridge used by the Python gallery helper. */

import path from "node:path";
import process from "node:process";

import {
  THEMES,
  captureDashboardScreenshot,
} from "./audit_web_dashboard_browser.mjs";

const HELP = `\
Usage: node scripts/capture_web_dashboard_screenshot.mjs [options]

Capture one deterministic dashboard PNG through Chrome DevTools Protocol. This
internal bridge requires Node.js 24 or newer and uses exact CSS width, height,
and DPR device metrics rather than Chrome outer-window sizing.

Required options:
  --base-url URL       Running loopback demo-server base URL
  --chrome PATH        Chrome/Chromium executable
  --profile-dir PATH   Empty isolated Chrome profile directory
  --theme ID           Built-in dashboard theme ID
  --width N            Exact CSS viewport width
  --height N           Exact CSS viewport height
  --dpr N              Exact device scale factor
  --output PATH        Same-directory staging PNG path

Optional options:
  --settle-ms N        Additional post-readiness settling time (default: 0)
  --timeout-ms N       Startup and CDP operation timeout (default: 20000)
  -h, --help           Show this help and exit

Successful stdout is one JSON object containing the authoritative outerHTML,
measured viewport geometry, and PNG byte count. Diagnostics are written to
stderr. No third-party Node package is required.
`;

function parseNumber(argument, value, {integer = false, minimum = 0} = {}) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || (integer && !Number.isInteger(parsed)) || parsed < minimum) {
    throw new Error(`${argument} must be ${integer ? "an integer" : "a number"} >= ${minimum}`);
  }
  return parsed;
}

function parseArguments(argv) {
  const values = new Map();
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "-h" || argument === "--help") {
      return {help: true};
    }
    const allowed = new Set([
      "--base-url",
      "--chrome",
      "--dpr",
      "--height",
      "--output",
      "--profile-dir",
      "--settle-ms",
      "--theme",
      "--timeout-ms",
      "--width",
    ]);
    if (!allowed.has(argument)) {
      throw new Error(`unknown argument: ${argument}`);
    }
    if (values.has(argument)) {
      throw new Error(`duplicate argument: ${argument}`);
    }
    const value = argv[index + 1];
    if (value === undefined || value.startsWith("--")) {
      throw new Error(`${argument} requires a value`);
    }
    values.set(argument, value);
    index += 1;
  }

  const required = [
    "--base-url",
    "--chrome",
    "--dpr",
    "--height",
    "--output",
    "--profile-dir",
    "--theme",
    "--width",
  ];
  const missing = required.filter((argument) => !values.has(argument));
  if (missing.length > 0) {
    throw new Error(`missing required argument(s): ${missing.join(", ")}`);
  }

  const baseUrl = new URL(values.get("--base-url"));
  if (!["http:", "https:"].includes(baseUrl.protocol)) {
    throw new Error("--base-url must use http or https");
  }
  if (baseUrl.username !== "" || baseUrl.password !== "") {
    throw new Error("--base-url must not contain credentials");
  }
  baseUrl.hash = "";
  baseUrl.search = "";

  const theme = values.get("--theme");
  if (!THEMES.includes(theme)) {
    throw new Error(`--theme must name one built-in theme: ${THEMES.join(", ")}`);
  }
  const outputPath = path.resolve(values.get("--output"));
  if (path.extname(outputPath).toLowerCase() !== ".png") {
    throw new Error("--output must end in .png");
  }
  return {
    baseUrl: baseUrl.toString().replace(/\/$/, ""),
    chrome: path.resolve(values.get("--chrome")),
    deviceScaleFactor: parseNumber("--dpr", values.get("--dpr"), {minimum: 0.01}),
    height: parseNumber("--height", values.get("--height"), {
      integer: true,
      minimum: 1,
    }),
    outputPath,
    profileDirectory: path.resolve(values.get("--profile-dir")),
    settleMs: parseNumber("--settle-ms", values.get("--settle-ms") ?? "0", {
      integer: true,
      minimum: 0,
    }),
    theme,
    timeoutMs: parseNumber("--timeout-ms", values.get("--timeout-ms") ?? "20000", {
      integer: true,
      minimum: 1,
    }),
    width: parseNumber("--width", values.get("--width"), {
      integer: true,
      minimum: 1,
    }),
  };
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
  try {
    const result = await captureDashboardScreenshot(options);
    process.stdout.write(JSON.stringify(result));
  } catch (error) {
    console.error(`ERROR: ${error.stack ?? error.message}`);
    process.exitCode = 1;
  }
}

await main();
