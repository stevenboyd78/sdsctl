/** Isolated generated-bundle smoke: fictional profile, no dashboard authentication.
 * Usage: node SCRIPT STAGE PLAYWRIGHT_MODULE CHROMIUM INSTALLED_PYTHON [ip|dns|ipv6] [review|first-run]
 * Requires sandbox-enabled Linux Chromium, OpenSSL and an installed candidate wheel.
 * Registration is confined to a unique test browser profile, never a user profile.
 */
import assert from "node:assert/strict";
import {generateKeyPairSync} from "node:crypto";
import {execFileSync} from "node:child_process";
import {mkdtemp, mkdir, writeFile, readFile, chmod, lstat, realpath} from "node:fs/promises";
import path from "node:path";
import {pathToFileURL} from "node:url";

if (process.argv[2] === "--help") {
  console.log("Usage: node audit_browser_bundle.mjs STAGE PLAYWRIGHT_MODULE CHROMIUM INSTALLED_PYTHON [ip|dns|ipv6] [review|first-run]");
  console.log("Existing private mode-0700 stage; four absolute paths. Linux, OpenSSL and sandbox required.");
  console.log("Creates a unique fictional fixture and isolated Chromium profile; retains all outputs.");
  console.log("Tests generated extension ID, empty-state refusal and native status; optional explicit first-run and pause/replay checks.");
  console.log("Fictional credentials only; no dashboard login or production access.");
  process.exit(0);
}
const [stage, playwright, executable, python, identityKind = "ip", mode = "review"] = process.argv.slice(2);
assert(["ip", "dns", "ipv6"].includes(identityKind));
assert(["review", "first-run"].includes(mode));
assert(process.platform === "linux" && process.getuid() !== 0);
assert([stage, playwright, executable, python].every(value => value && path.isAbsolute(value)));
const info = await lstat(stage);
assert(info.isDirectory() && !info.isSymbolicLink() && info.uid === process.getuid() &&
  (info.mode & 0o777) === 0o700 && await realpath(stage) === stage, "Unsafe stage");
const root = await mkdtemp(path.join(stage, "run-"));
console.log(JSON.stringify({fixture: root}));
const put = (name, value) => writeFile(path.join(root, name), value, {mode: 0o600});
const run = (args) => execFileSync(python,
  ["-I", "-c", "from sds200.cli import main; raise SystemExit(main())", ...args], {cwd: root, stdio: "pipe"});
const pub = generateKeyPairSync("rsa", {modulusLength: 2048}).publicKey.export({type: "spki", format: "pem"});
await put("extension.pub.pem", pub);
const inspected = run(["browser-device-bundle", "--experimental", "identity", "--public-key", path.join(root, "extension.pub.pem")]).toString();
const id = inspected.match(/^Extension ID: ([a-p]{32})$/m)?.[1];
assert(id);
execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
  "-subj", "/CN=Fictional bundle smoke", "-keyout", "fixture.key", "-out", "ca.pem",
  "-addext", "basicConstraints=critical,CA:TRUE"], {cwd: root, stdio: "pipe"});
await chmod(path.join(root, "ca.pem"), 0o600);
await chmod(path.join(root, "fixture.key"), 0o600);
await put("enrollment.json", JSON.stringify({version: 1, device_id: "fixture", generation: 1,
  credential: "sdsctl-browser-v1." + "b".repeat(64), outcome: {status: "issued", completed: false}}));
const origin = {ip: "https://127.0.0.1:8443", dns: "https://display.example", ipv6: "https://[::1]:8443"}[identityKind];
run(["browser-device-profile", "--experimental", "create", "--directory", path.join(root, "native"),
  "--enrollment-file", path.join(root, "enrollment.json"), "--ca-file", path.join(root, "ca.pem"),
  "--origin", origin, "--device-id", "fixture", "--extension-id", id]);
run(["browser-device-bundle", "--experimental", "create", "--directory", path.join(root, "bundle"),
  "--profile", path.join(root, "native"), "--public-key", path.join(root, "extension.pub.pem")]);
const extension = path.join(root, "bundle/extension"), profile = path.join(root, "browser");
run(["browser-device-register", "--experimental", "--directory", profile,
  "--bundle", path.join(root, "bundle"), "--profile", path.join(root, "native"),
  "--public-key", path.join(root, "extension.pub.pem")]);
for (const name of ["config", "cache", "data", "tmp"]) await mkdir(path.join(root, name), {mode: 0o700});
const {chromium} = await import(pathToFileURL(playwright));
let context;
const launch = () => chromium.launchPersistentContext(profile, {executablePath: executable,
  chromiumSandbox: true, headless: true, timeout: 20000,
  env: {...process.env, XDG_CONFIG_HOME: path.join(root, "config"), XDG_CACHE_HOME: path.join(root, "cache"),
    XDG_DATA_HOME: path.join(root, "data"), TMPDIR: path.join(root, "tmp")},
  ignoreDefaultArgs: ["--disable-extensions"],
  args: [`--load-extension=${extension}`, `--disable-extensions-except=${extension}`]});
try {
  context = await launch();
  const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker", {timeout: 15000});
  assert.equal(worker.url(), `chrome-extension://${id}/worker.mjs`);
  const control = await context.newPage();
  await control.goto(`chrome-extension://${id}/control.html`);
  const until = Date.now() + 10000;
  let status;
  do {
    status = await control.evaluate(() => chrome.runtime.sendMessage({action: "status"}));
    if (status?.mode === "setup_error") break;
    await new Promise(resolve => setTimeout(resolve, 100));
  } while (Date.now() < until);
  assert.equal(status?.mode, "setup_error");
  assert.deepEqual(await control.evaluate(() => chrome.storage.local.get(null)), {});
  assert.deepEqual(await context.cookies(origin), []);
  const permissions = await worker.evaluate(() => chrome.permissions.getAll());
  // Chrome returns the root content-script pattern as well as the broader
  // explicit host permission; neither adds another host, scheme or port.
  const pattern = identityKind === "dns" ? origin + ":443" : origin;
  assert.deepEqual(permissions.origins.sort(), [pattern + "/", pattern + "/*"].sort());
  const nativeStatus = await worker.evaluate(() => chrome.runtime.sendNativeMessage(
    "org.sdsctl.browser_device", {version: 1, action: "status"}));
  assert.equal(nativeStatus.ok, true);
  assert.equal(nativeStatus.mode, "active");
  assert.equal(nativeStatus.revision, 1);
  const receipt = JSON.parse(await readFile(path.join(root, "bundle/bundle.json")));
  let firstRun = null;
  if (mode === "first-run") {
    const setup = await context.newPage();
    await setup.goto(`chrome-extension://${id}/setup.html`);
    await setup.setViewportSize({width: 1280, height: 720});
    await setup.screenshot({path: path.join(root, "setup-desktop.png")});
    await setup.setViewportSize({width: 600, height: 400});
    assert(await setup.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await setup.screenshot({path: path.join(root, "setup-small.png"), fullPage: true});
    assert.equal(await setup.locator('input[type="password"]').count(), 0);
    await setup.locator("#confirm").check();
    await setup.locator("#initialize").click();
    await setup.waitForFunction(() => document.getElementById("notice").textContent.startsWith("Setup saved."));
    const saved = await control.evaluate(() => chrome.storage.local.get(null));
    assert.deepEqual(saved, {sdsctlDeviceRecovery: {version: 1, identity: receipt.identity,
      paused: false, phase: "clean", nextAt: 0}});
    const claimed = await worker.evaluate(() => chrome.runtime.sendNativeMessage(
      "org.sdsctl.browser_device", {version: 1, action: "status"}));
    assert.equal(claimed.revision, 2); assert.equal(claimed.mode, "active");
    assert.deepEqual(await context.cookies(origin), []);
    // Pause before restarting: this smoke never asks the helper to authenticate.
    const paused = await control.evaluate(() => chrome.runtime.sendMessage({action: "suspend"}));
    assert.equal(paused.mode, "paused"); assert.equal(paused.cookieCleared, true);
    const pausedState = await control.evaluate(() => chrome.storage.local.get(null));
    assert.deepEqual(await setup.evaluate(() => chrome.runtime.sendMessage({action: "initialize"})),
      {mode: "setup_refused"});
    assert.deepEqual(await control.evaluate(() => chrome.storage.local.get(null)), pausedState);
    await context.close(); context = await launch();
    const resumedControl = await context.newPage();
    await resumedControl.goto(`chrome-extension://${id}/control.html`);
    await resumedControl.waitForFunction(async () =>
      (await chrome.runtime.sendMessage({action: "status"})).mode === "paused");
    assert.deepEqual(await context.cookies(origin), []);
    // Isolated destructive-state fault injection, never a setup instruction:
    // remove only this fictional browser's recovery key, then recreate the worker.
    await resumedControl.evaluate(() => chrome.storage.local.remove("sdsctlDeviceRecovery"));
    await context.close(); context = await launch();
    const replay = await context.newPage();
    await replay.goto(`chrome-extension://${id}/setup.html`);
    await replay.locator("#confirm").check(); await replay.locator("#initialize").click();
    await replay.waitForFunction(() => document.getElementById("notice").textContent.startsWith("Setup could not be confirmed."));
    const blocked = await replay.evaluate(() => chrome.storage.local.get("sdsctlDeviceRecovery"));
    assert.equal(blocked.sdsctlDeviceRecovery.phase, "setup_pending");
    assert.equal(blocked.sdsctlDeviceRecovery.paused, true);
    assert.deepEqual(await context.cookies(origin), []);
    firstRun = {explicitSetup: true, noPasswordField: true, noCookieOnSetup: true,
      nativeClaimOnce: true, pausePreserved: true, pausedRestart: true, deletedStateBlocked: true,
      desktopAndSmallScreenshots: true};
  }
  const result = {passed: true, identityKind, mode, browser: context.browser().version(), sandbox: true,
    generatedBundle: true, installedPython: true, extensionIdMatched: true,
    controlledRegistration: true, emptyStateRefused: true, nativeStatusPassed: true,
    loginAttempted: false, firstRun,
    identity: receipt.identity, files: receipt.files};
  await put("result.json", JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify(result));
} finally {
  await context?.close();
}
