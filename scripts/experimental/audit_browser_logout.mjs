/** Isolated real-Chromium fixture, never a production enrollment installer.
 * Usage: node SCRIPT STAGE PLAYWRIGHT_MODULE CHROMIUM CERTUTIL PYTHON [SCENARIO]
 * Scenarios: logout outcomes and real cookie-operation interruption; see --help.
 * Requires Linux bwrap, OpenSSL, working Chromium sandbox. No TLS bypass.
 */
import assert from "node:assert/strict";
import {generateKeyPairSync, createHash, randomBytes} from "node:crypto";
import {execFileSync} from "node:child_process";
import {mkdtemp, mkdir, readFile, writeFile, copyFile, chmod, lstat, realpath} from "node:fs/promises";
import {existsSync} from "node:fs";
import https from "node:https";
import os from "node:os";
import path from "node:path";
import {fileURLToPath, pathToFileURL} from "node:url";
import {initialBrowserRecoveryState} from "./browser_device_recovery.mjs";
import {cookieProbeWorkerSource, testCookieInterruption} from "./browser_cookie_interruption.mjs";

if (process.argv[2] === "--help") {
  console.log("Usage: node audit_browser_logout.mjs STAGE PLAYWRIGHT_MODULE CHROMIUM CERTUTIL PYTHON [SCENARIO]");
  console.log("Private existing mode-0700 stage; five absolute paths. Linux bwrap and OpenSSL required.");
  console.log("Scenarios: drained, pending, lost, worker-stop, cookie-set-stop, cookie-remove-stop. Fictional credentials and loopback HTTPS only.");
  console.log("Also: cookie-set-stop-queued-recovery (restart worker before releasing the network subprocess).");
  console.log("Requires sandbox-enabled Chromium and verified TLS; no bypass or production access.");
  console.log("Retains a unique run directory. Driver never installs authentication cookies.");
  process.exit(0);
}
const [stage, playwright, executable, certutil, python, scenario = "drained"] = process.argv.slice(2);
assert(stage && playwright && executable && certutil && python, "Five explicit paths required");
assert(["drained", "pending", "lost", "worker-stop", "cookie-set-stop", "cookie-remove-stop", "cookie-set-stop-queued-recovery"].includes(scenario), "Unknown scenario");
const cookieOperation = scenario.startsWith("cookie-set-stop") ? "set" : scenario === "cookie-remove-stop" ? "remove" : null;
assert([stage, playwright, executable, certutil, python].every(x => path.isAbsolute(x)), "Absolute paths required");
const stageInfo = await lstat(stage);
assert(stageInfo.isDirectory() && !stageInfo.isSymbolicLink() && stageInfo.uid === process.getuid() &&
  (stageInfo.mode & 0o777) === 0o700 && await realpath(stage) === stage, "Unsafe staging directory");
const {chromium} = await import(pathToFileURL(playwright));
const source = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(source, "../..");
const root = await mkdtemp(path.join(path.resolve(stage), "run-"));
console.log(JSON.stringify({fixture: root, scenario}));
const profile = path.join(root, "profile"), extension = path.join(root, "extension");
for (const dir of ["profile/NativeMessagingHosts", "extension", "nssdb", "t", "data/pki/nssdb"])
  await mkdir(path.join(root, dir), {recursive: true, mode: 0o700});
const put = (name, value, mode = 0o600) => writeFile(path.join(root, name), value, {mode});
const run = (command, args) => execFileSync(command, args, {cwd: root, stdio: "pipe"});
run("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
  "-subj", "/CN=Fictional browser logout fixture", "-keyout", "ca.key", "-out", "ca.pem",
  "-addext", "keyUsage=critical,keyCertSign,cRLSign"]);
await chmod(path.join(root, "ca.pem"), 0o600);
run("openssl", ["req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1",
  "-keyout", "server.key", "-out", "server.csr"]);
await put("server.ext", "subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n");
run("openssl", ["x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
  "-CAcreateserial", "-days", "1", "-extfile", "server.ext", "-out", "server.pem"]);
const nss = path.join(root, "nssdb");
run(certutil, ["-N", "-d", `sql:${nss}`, "--empty-password"]);
run(certutil, ["-A", "-d", `sql:${nss}`, "-n", "fictional-logout", "-t", "C,,", "-i", "ca.pem"]);
const credential = "sdsctl-browser-v1." + randomBytes(32).toString("hex");
await put("device.secret", credential);
const sessions = new Set();
let origin, authCount = 0, logoutCount = 0, pauseCount = 0, paused = false, heldResponse;
const observed = [];
const server = https.createServer({key: await readFile(path.join(root, "server.key")),
  cert: await readFile(path.join(root, "server.pem"))}, (request, response) => {
  response.setHeader("Cache-Control", "no-store");
  const send = (status, value) => {response.writeHead(status); response.end(value);};
  if (request.headers.host !== new URL(origin).host) return send(403, "host");
  if (request.url === "/auth/device/session" && request.method === "POST") {
    if (paused || request.headers.authorization !== `Bearer ${credential}` || request.headers.cookie ||
        request.headers.origin) return send(401, "denied");
    const token = "sdsctl-browser-session-v1." + randomBytes(32).toString("hex");
    sessions.add(token); authCount++;
    response.setHeader("Content-Type", "application/json");
    return send(200, JSON.stringify({token, expires_in: 300}));
  }
  const cookie = request.headers.cookie?.split("; ").find(x => x.startsWith("__Host-sdsctl-device-session="));
  const authorized = !paused && sessions.has(cookie?.split("=")[1]);
  if (request.url === "/auth/logout" && request.method === "POST") {
    logoutCount++;
    observed.push({authorized, origin: request.headers.origin === origin,
      fetchSite: request.headers["sec-fetch-site"], accept: request.headers.accept});
    if (!authorized || request.headers.origin !== origin || request.headers["sec-fetch-site"] !== "same-origin")
      return send(403, "denied");
    paused = true; pauseCount++;
    if (scenario === "lost") { response.destroy(); return; }
    if (scenario === "worker-stop") { heldResponse = response; return; }
    response.setHeader("Content-Type", "application/json");
    response.setHeader("Set-Cookie", "__Host-sdsctl-device-session=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict");
    return send(scenario === "drained" ? 200 : 202,
      JSON.stringify({version: 1, device_logout: true, paused: true, drained: scenario === "drained"}));
  }
  if (request.url === "/auth/session") {
    response.setHeader("Content-Type", "application/json");
    return send(authorized ? 200 : 401, JSON.stringify({device_enrolled: authorized}));
  }
  response.setHeader("Content-Type", "text/html");
  send(200, '<!doctype html><title>Fictional display logout</title><h1>Fixture only</h1>' +
    '<form method="post" action="/auth/logout"><button type="submit">Sign out</button></form>');
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
origin = `https://127.0.0.1:${server.address().port}`;
let context;
const step = name => console.log(JSON.stringify({step: name, scenario}));
try {
  const publicKey = generateKeyPairSync("rsa", {modulusLength: 2048}).publicKey.export({type: "spki", format: "der"});
  const id = [...createHash("sha256").update(publicKey).digest("hex").slice(0, 32)]
    .map(c => String.fromCharCode(97 + parseInt(c, 16))).join("");
  await put("client.json", JSON.stringify({version: 1, origin, device_id: "fixture",
    extension_origin: `chrome-extension://${id}/`}));
  const initialize = `import sys\nfrom pathlib import Path\nsys.path.insert(0, ${JSON.stringify(path.join(repo, "src"))})\n` +
    `from sds200.browser_device_native import load_browser_native_configuration\n` +
    `from sds200.browser_device_recovery import BrowserDeviceRecovery\n` +
    `c=load_browser_native_configuration(Path(${JSON.stringify(root)}))\n` +
    `BrowserDeviceRecovery.initialize(c.root/'recovery.sqlite', c.identity)\nprint(c.identity)\n`;
  const identity = run(python, ["-c", initialize]).toString().trim();
  assert(/^[a-f0-9]{64}$/.test(identity));
  const config = {origin, identity, nativeHost: "org.sdsctl.browser_device"};
  for (const name of ["browser_device_recovery.mjs", "browser_device_logout.mjs"])
    await copyFile(path.join(source, name), path.join(extension, name));
  await put("extension/manifest.json", JSON.stringify({manifest_version: 3,
    name: "SDSCTL FICTIONAL LOGOUT ACCEPTANCE", version: "0.0.1", key: publicKey.toString("base64"),
    permissions: ["nativeMessaging", "cookies", "storage", "alarms"], host_permissions: ["https://127.0.0.1/*"],
    background: {service_worker: "worker.mjs", type: "module"},
    content_scripts: [{matches: ["https://127.0.0.1/*"], js: ["content.js"], run_at: "document_start", all_frames: false}]}));
  await put("extension/worker.mjs", `import {connectChromeRecovery} from './browser_device_recovery.mjs';
import {connectLogoutWorker} from './browser_device_logout.mjs';
const config = ${JSON.stringify(config)};
${cookieOperation ? cookieProbeWorkerSource(cookieOperation) : "const recoveryChrome = chrome;"}
globalThis.fixtureController = connectChromeRecovery(recoveryChrome, config);
connectLogoutWorker(chrome, fixtureController, config.origin);
`);
  await put("extension/content.js", (await readFile(path.join(source, "browser_device_logout.mjs"), "utf8"))
    .replaceAll("export ", "") + `\nif (location.href === ${JSON.stringify(origin + "/")})
connectLogoutContent({document,window,runtime:chrome.runtime,fetcher:fetch.bind(globalThis)}, ${JSON.stringify(origin)});\n`);
  await put("extension/control.html", "<!doctype html><title>Fixture control</title>");
  await put("native-host", `#!${python}\nimport os, sys\nfrom pathlib import Path\n` +
    `sys.path.insert(0, ${JSON.stringify(path.join(repo, "src"))})\n` +
    `from sds200.browser_device_native import run_browser_native\n` +
    `raise SystemExit(run_browser_native(Path(${JSON.stringify(root)}),sys.argv[1:],` +
    `os.fdopen(os.dup(0),'rb',buffering=0),os.fdopen(os.dup(1),'wb',buffering=0)))\n`, 0o700);
  await put("profile/NativeMessagingHosts/org.sdsctl.browser_device.json", JSON.stringify({
    name: config.nativeHost, description: "Fictional fixture only", type: "stdio", path: path.join(root, "native-host"),
    allowed_origins: [`chrome-extension://${id}/`]}));
  const legacyNSS = path.join(os.homedir(), ".pki/nssdb");
  const nssMount = existsSync(legacyNSS) ? legacyNSS : path.join(root, "data/pki/nssdb");
  await put("browser", `#!/usr/bin/python3\nimport os,sys\nos.execvp('bwrap', ['bwrap','--ro-bind','/','/',` +
    `'--bind',${JSON.stringify(root)},${JSON.stringify(root)},'--bind',${JSON.stringify(nss)},${JSON.stringify(nssMount)},` +
    `'--dev-bind','/dev','/dev','--proc','/proc','--',${JSON.stringify(executable)},*sys.argv[1:]])\n`, 0o700);
  const launch = () => chromium.launchPersistentContext(profile, {executablePath: path.join(root, "browser"),
    headless: true, chromiumSandbox: true, timeout: 15000,
    env: {...process.env, XDG_CONFIG_HOME: path.join(root, "config"), XDG_CACHE_HOME: path.join(root, "cache"),
      XDG_DATA_HOME: path.join(root, "data"), TMPDIR: path.join(root, "t")},
    ignoreDefaultArgs: ["--disable-extensions"],
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]});
  step("launch");
  context = await launch();
  context.setDefaultTimeout(15000);
  step("worker-discovery");
  let worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker", {timeout: 15000});
  const bounded = async operation => {
    let timer;
    try { return await Promise.race([operation, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("Browser operation deadline")), 10000);
    })]); } finally { clearTimeout(timer); }
  };
  const until = async (check, timeout = 15000) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) { if (await check()) return; await new Promise(resolve => setTimeout(resolve, 100)); }
    throw new Error("Fixture condition deadline");
  };
  // Explicit trusted fixture provisioning, not worker auto-initialization. The
  // unprovisioned worker must first fail closed; a fresh worker reads the record.
  step("unprovisioned-fail-closed");
  await until(async () => await bounded(worker.evaluate(() => globalThis.fixtureController?.status().mode)) === "setup_error");
  assert.equal(authCount, 0);
  const provision = await context.newPage();
  await provision.goto(`chrome-extension://${id}/control.html`);
  await provision.evaluate(state => chrome.storage.local.set({sdsctlDeviceRecovery: state}), initialBrowserRecoveryState(config));
  await context.close(); context = await launch();
  context.setDefaultTimeout(15000);
  worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker", {timeout: 15000});
  step("native-authentication");
  let cookieEvidence = null, completedLogoutCount = 0;
  const browserVersion = context.browser().version();
  if (cookieOperation) {
    cookieEvidence = await testCookieInterruption({context, worker, profile, id, origin,
      operation: cookieOperation, recoveryBeforeResume: scenario === "cookie-set-stop-queued-recovery", bounded, until, step});
    assert.equal(authCount, 1); assert.equal(logoutCount, 0); assert.equal(pauseCount, 0);
  } else {
  await until(async () => await bounded(worker.evaluate(() => globalThis.fixtureController?.status().mode)) === "active");
  step("cookie-installed");
  const cookies = await context.cookies(origin);
  assert(cookies.some(c => c.name === "__Host-sdsctl-device-session" && c.httpOnly && c.secure && c.sameSite === "Strict"));
  assert.equal(authCount, 1);
  const page = await context.newPage();
  step("verified-navigation");
  await page.goto(origin); // Verified TLS; no ignoreHTTPSErrors or certificate bypass.
  assert.equal(await page.evaluate(() => document.cookie), "");
  step("logout-click");
  await page.getByRole("button", {name: "Sign out"}).click();
  await until(() => logoutCount >= 1);
  console.log(JSON.stringify({step: "logout-observed", scenario, authCount, logoutCount, observed}));
  if (scenario === "worker-stop") {
    step("worker-stop-discovery");
    const cdp = await context.newCDPSession(page);
    let version;
    cdp.on("ServiceWorker.workerVersionUpdated", event => {
      version = event.versions.find(v => v.scriptURL === `chrome-extension://${id}/worker.mjs`) || version;
    });
    await bounded(cdp.send("ServiceWorker.enable"));
    await until(() => version?.runningStatus === "running");
    assert(heldResponse && !heldResponse.destroyed);
    assert(await bounded(worker.evaluate(async () => {
      const state = (await chrome.storage.local.get("sdsctlDeviceRecovery")).sdsctlDeviceRecovery;
      return state.paused === true && state.phase === "logout_pending";
    })));
    step("worker-stop-request");
    await bounded(cdp.send("ServiceWorker.stopWorker", {versionId: version.versionId}));
    await until(() => version?.runningStatus === "stopped");
    step("worker-stopped");
    heldResponse?.destroy();
    const control = await context.newPage();
    await control.goto(`chrome-extension://${id}/control.html`);
    await bounded(control.evaluate(() => chrome.runtime.sendMessage({action: "status"})));
    step("pending-logout-cookie-recovery");
    await until(async () => !(await context.cookies(origin)).some(c => c.name === "__Host-sdsctl-device-session"), 75000);
    await cdp.detach();
  } else {
    const expected = scenario === "drained" ? "Server shutdown confirmed" : "unconfirmed";
    await page.getByRole("status").filter({hasText: expected}).waitFor({timeout: 15000});
    assert(!(await context.cookies(origin)).some(c => c.name === "__Host-sdsctl-device-session"));
  }
  assert.equal(authCount, 1);
  assert.equal(pauseCount, 1);
  // Chromium can retry a request when the socket closes before response headers.
  // The already-paused server must reject every replay; never count it as a
  // second authorized mutation or conceal it as a single wire request.
  assert(logoutCount >= 1 && logoutCount <= (["lost", "worker-stop"].includes(scenario) ? 2 : 1));
  assert.equal(observed.length, logoutCount);
  observed.forEach((request, index) => assert.deepEqual(request, {
    authorized: index === 0, origin: true, fetchSite: "same-origin", accept: "application/json"}));
  completedLogoutCount = logoutCount;
  }
  // Full browser restart must not re-enroll. No driver cookie injection or resume.
  step("restart");
  await context.close(); context = await launch();
  const control = await context.newPage(); await control.goto(`chrome-extension://${id}/control.html`);
  await control.evaluate(() => chrome.runtime.sendMessage({action: "status"}));
  const nextWorker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker", {timeout: 15000});
  await until(async () => await bounded(nextWorker.evaluate(() => globalThis.fixtureController?.status().mode)) === "paused");
  assert.equal(authCount, 1); assert.equal(logoutCount, completedLogoutCount); assert.equal(pauseCount, cookieOperation ? 0 : 1);
  assert(!(await context.cookies(origin)).some(c => c.name === "__Host-sdsctl-device-session"));
  const stored = await nextWorker.evaluate(() => chrome.storage.local.get(null));
  assert(!JSON.stringify(stored).includes(credential) && !JSON.stringify(stored).includes("sdsctl-browser-session-v1."));
  const nativeMode = run(python, ["-c", `import sys\nfrom pathlib import Path\n` +
    `sys.path.insert(0, ${JSON.stringify(path.join(repo, "src"))})\n` +
    `from sds200.browser_device_recovery import BrowserDeviceRecovery\n` +
    `print(BrowserDeviceRecovery(Path(${JSON.stringify(path.join(root, "recovery.sqlite"))}), ${JSON.stringify(identity)}).status().mode.value)`]).toString().trim();
  assert.equal(nativeMode, "paused");
  const result = {passed: true, scenario, browserVersion, sandbox: true, verifiedTLS: true,
    realNativeHelper: true, server: "fictional Node HTTPS contract", cookieInjection: false,
    sameOriginLogout: !cookieOperation, cookieEvidence, authCount, logoutCount, pauseCount, nativeMode, restartRemainedPaused: true};
  await put("result.json", JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify(result));
} catch (error) {
  console.log(JSON.stringify({step: "failed", scenario, authCount, logoutCount, observed}));
  throw error;
} finally {
  step("cleanup");
  heldResponse?.destroy();
  try { await context?.close(); } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}
