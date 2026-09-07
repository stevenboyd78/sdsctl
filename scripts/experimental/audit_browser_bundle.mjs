/** Isolated generated-bundle smoke: fictional profile, no dashboard authentication.
 * Usage: node SCRIPT STAGE PLAYWRIGHT_MODULE CHROMIUM INSTALLED_PYTHON [ip|dns|ipv6]
 * Requires sandbox-enabled Linux Chromium, OpenSSL and an installed candidate wheel.
 * Registration is confined to a unique test browser profile, never a user profile.
 */
import assert from "node:assert/strict";
import {generateKeyPairSync} from "node:crypto";
import {execFileSync} from "node:child_process";
import {mkdtemp, mkdir, writeFile, readFile, copyFile, chmod, lstat, realpath} from "node:fs/promises";
import path from "node:path";
import {pathToFileURL} from "node:url";

if (process.argv[2] === "--help") {
  console.log("Usage: node audit_browser_bundle.mjs STAGE PLAYWRIGHT_MODULE CHROMIUM INSTALLED_PYTHON [ip|dns|ipv6]");
  console.log("Existing private mode-0700 stage; four absolute paths. Linux, OpenSSL and sandbox required.");
  console.log("Creates a unique fictional fixture and isolated Chromium profile; retains all outputs.");
  console.log("Tests generated extension ID, empty-state refusal and native status only; no login or production access.");
  process.exit(0);
}
const [stage, playwright, executable, python, identityKind = "ip"] = process.argv.slice(2);
assert(["ip", "dns", "ipv6"].includes(identityKind));
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
await mkdir(path.join(profile, "NativeMessagingHosts"), {recursive: true, mode: 0o700});
await copyFile(path.join(root, "bundle/org.sdsctl.browser_device.json"),
  path.join(profile, "NativeMessagingHosts/org.sdsctl.browser_device.json"));
for (const name of ["config", "cache", "data", "tmp"]) await mkdir(path.join(root, name), {mode: 0o700});
const {chromium} = await import(pathToFileURL(playwright));
let context;
try {
  context = await chromium.launchPersistentContext(profile, {executablePath: executable,
    chromiumSandbox: true, headless: true, timeout: 20000,
    env: {...process.env, XDG_CONFIG_HOME: path.join(root, "config"), XDG_CACHE_HOME: path.join(root, "cache"),
      XDG_DATA_HOME: path.join(root, "data"), TMPDIR: path.join(root, "tmp")},
    ignoreDefaultArgs: ["--disable-extensions"],
    args: [`--load-extension=${extension}`, `--disable-extensions-except=${extension}`]});
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
  const receipt = JSON.parse(await readFile(path.join(root, "bundle/bundle.json")));
  const result = {passed: true, identityKind, browser: context.browser().version(), sandbox: true,
    generatedBundle: true, installedPython: true, extensionIdMatched: true,
    emptyStateRefused: true, nativeStatusPassed: true, loginAttempted: false,
    identity: receipt.identity, files: receipt.files};
  await put("result.json", JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify(result));
} finally {
  await context?.close();
}
