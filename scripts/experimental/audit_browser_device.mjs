/** Disposable Linux native-messaging experiment, NOT an enrollment installer.
 * Usage: node THIS_FILE STAGE_DIR PLAYWRIGHT_MODULE CERTUTIL
 * Requires test Chromium via PLAYWRIGHT_BROWSERS_PATH, bwrap, openssl, Python3.
 * Retains its uniquely named fixture for inspection; no production connections.
 */
import assert from "node:assert/strict";
import {createHash, generateKeyPairSync, randomBytes} from "node:crypto";
import {execFile, execFileSync} from "node:child_process";
import {mkdtemp, mkdir, readFile, writeFile, copyFile, chmod, rename, symlink} from "node:fs/promises";
import https from "node:https";
import path from "node:path";
import os from "node:os";
import {existsSync} from "node:fs";
import {fileURLToPath, pathToFileURL} from "node:url";

const [stage, playwrightModule, certutil] = process.argv.slice(2);
const helperOnly = process.argv[5] === "--helper-only";
assert(stage && playwrightModule && certutil, "Three explicit paths required");
const {chromium} = await import(pathToFileURL(playwrightModule));
const source = path.dirname(fileURLToPath(import.meta.url));
const root = await mkdtemp(path.join(path.resolve(stage), "run-"));
const profile = path.join(root, "profile");
const extension = path.join(root, "extension");
const unapproved = path.join(root, "unapproved-extension");
const nss = path.join(root, "nssdb");
for (const dir of [profile, extension, unapproved, nss, path.join(root, "t"), path.join(root, "data/pki"), path.join(profile, "NativeMessagingHosts")])
  await mkdir(dir, {recursive: true, mode: 0o700});
const put = (name, value, mode = 0o600) => writeFile(path.join(root, name), value, {mode});
const run = (command, args) => execFileSync(command, args, {cwd: root, stdio: "pipe"});
run("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
  "-subj", "/CN=SDSCTL fictional fixture CA", "-keyout", "ca.key", "-out", "ca.pem",
  "-addext", "keyUsage=critical,keyCertSign,cRLSign"]);
run("openssl", ["req", "-new", "-newkey", "rsa:2048", "-nodes",
  "-subj", "/CN=127.0.0.1", "-keyout", "server.key", "-out", "server.csr"]);
await put("server.ext", "subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n");
run("openssl", ["x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
  "-CAcreateserial", "-days", "1", "-extfile", "server.ext", "-out", "server.pem"]);
run(certutil, ["-N", "-d", `sql:${nss}`, "--empty-password"]);
run(certutil, ["-A", "-d", `sql:${nss}`, "-n", "fictional-fixture", "-t", "C,,", "-i", "ca.pem"]);

const credential = randomBytes(32).toString("hex");
await put("fictional-device.secret", credential);
const sessions = new Map();
let origin;
let authCount = 0;
let enrollmentMode = "normal";
let redirectHits = 0;
const cookieName = "__Host-sdsctl-device-probe";
const server = https.createServer({key: await readFile(path.join(root, "server.key")),
  cert: await readFile(path.join(root, "server.pem"))}, (request, response) => {
  response.setHeader("Cache-Control", "no-store");
  const send = (status, body) => {response.writeHead(status); response.end(body);};
  if (request.headers.host !== new URL(origin).host) return send(403, "wrong host");
  if (request.url === "/redirect-target") {
    redirectHits++;
    return send(500, "must not follow");
  }
  if (request.url === "/fixture/enroll" && request.method === "POST") {
    if (request.headers.authorization !== `Bearer ${credential}`) return send(401, "denied");
    const token = randomBytes(32).toString("hex");
    const expires = Math.floor(Date.now() / 1000) + 60;
    if (enrollmentMode === "redirect") {
      response.setHeader("Location", origin + "/redirect-target");
      return send(307, "redirect forbidden");
    }
    if (enrollmentMode === "oversized") return send(200, " ".repeat(4097));
    if (enrollmentMode === "duplicate") return send(200,
      `{"token":"${token}","token":"${token}","expires":${expires}}`);
    if (enrollmentMode === "token") return send(200, JSON.stringify({token: "Z".repeat(64), expires}));
    if (enrollmentMode === "expired") return send(200, JSON.stringify({token, expires: 1}));
    if (enrollmentMode === "long-lived") return send(200, JSON.stringify({token, expires: expires + 3600}));
    if (enrollmentMode === "role") return send(200, JSON.stringify({token, expires, role: "operator"}));
    sessions.set(token, expires);
    authCount++;
    return send(200, JSON.stringify({token, expires}));
  }
  const token = request.headers.cookie?.split("; ").find(x => x.startsWith(cookieName + "="))?.split("=")[1];
  const authorized = sessions.has(token) && sessions.get(token) > Date.now() / 1000;
  if (request.url === "/operator") return send(authorized ? 403 : 401, "denied");
  if (request.url === "/session") return send(authorized ? 200 : 401,
    JSON.stringify({display_only: authorized}));
  response.setHeader("Content-Type", "text/html");
  send(200, "<!doctype html><title>Fictional display</title><p>Fixture only</p>");
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
origin = `https://127.0.0.1:${server.address().port}`;

const publicKey = generateKeyPairSync("rsa", {modulusLength: 2048}).publicKey
  .export({type: "spki", format: "der"});
const extensionId = [...createHash("sha256").update(publicKey).digest("hex").slice(0, 32)]
  .map(c => String.fromCharCode(97 + parseInt(c, 16))).join("");
await put("extension/manifest.json", JSON.stringify({manifest_version: 3,
  name: "SDSCTL LOCAL FIXTURE ONLY", version: "0.0.1", key: publicKey.toString("base64"),
  permissions: ["nativeMessaging", "cookies"], host_permissions: ["https://127.0.0.1/*"],
  background: {service_worker: "worker.js"}}));
await put("extension/worker.js", (await readFile(path.join(source, "browser_device_worker.js"), "utf8"))
  .replace("FIXTURE_ORIGIN", origin));
await put("unapproved-extension/manifest.json", JSON.stringify({manifest_version: 3,
  name: "UNAPPROVED FIXTURE", version: "0.0.1", permissions: ["nativeMessaging"],
  background: {service_worker: "worker.js"}}));
await put("unapproved-extension/worker.js", "globalThis.fixtureReady = true;");
await put("extension/control.html", '<!doctype html><button>Authenticate fixture</button><output></output><script src="control.js"></script>');
await put("extension/control.js", `document.querySelector('button').onclick = async () => {
  const result = await chrome.runtime.sendMessage({action:'authenticate'});
  document.querySelector('output').textContent = result.ok ? 'passed' : 'failed';
};`);
await put("extension/startup.html", '<!doctype html><p>Starting fictional display</p><script src="startup.js"></script>');
await put("extension/startup.js", `chrome.runtime.sendMessage({action:'authenticate'}).then(result => {
  if (result.ok) location.replace(${JSON.stringify(origin)});
  else document.querySelector('p').textContent = 'Fixture authentication unavailable';
});`);
await copyFile(path.join(source, "browser_device_host.py"), path.join(root, "host.py"));
await copyFile(path.join(source, "../../src/sds200/browser_device_protocol.py"), path.join(root, "browser_device_protocol.py"));
await put("fixture.json", JSON.stringify({origin, extension_origin: `chrome-extension://${extensionId}/`}));
await put("native-host", `#!/usr/bin/python3\nimport os, sys\nos.execv('/usr/bin/python3', ['/usr/bin/python3', ${JSON.stringify(path.join(root, "host.py"))}, *sys.argv[1:]])\n`, 0o700);
await put("profile/NativeMessagingHosts/org.sdsctl.local_probe.json", JSON.stringify({
  name: "org.sdsctl.local_probe", description: "Fictional isolated probe", type: "stdio",
  path: path.join(root, "native-host"), allowed_origins: [`chrome-extension://${extensionId}/`]}));
// Mount only the fixture's NSS DB over the browser's legacy lookup path. No user
// certificate/profile files are changed. Root is read-only; only fixture is writable.
const legacyNSS = path.join(os.homedir(), ".pki/nssdb");
const nssMount = existsSync(legacyNSS) ? legacyNSS : path.join(root, "data/pki/nssdb");
await mkdir(path.join(root, "data/pki/nssdb"), {recursive: true, mode: 0o700});
const browserExecutable = process.env.SDSCTL_PROBE_CHROMIUM || chromium.executablePath();
await put("browser", `#!/usr/bin/python3\nimport os, sys\nos.execvp('bwrap', ['bwrap', '--ro-bind', '/', '/', '--bind', ${JSON.stringify(root)}, ${JSON.stringify(root)}, '--bind', ${JSON.stringify(nss)}, ${JSON.stringify(nssMount)}, '--dev-bind', '/dev', '/dev', '--proc', '/proc', '--', ${JSON.stringify(browserExecutable)}, *sys.argv[1:]])\n`, 0o700);

let context;
let alternateServer;
try {
  async function callHelper(request, caller = `chrome-extension://${extensionId}/`, stalled = false) {
    const payload = Buffer.from(JSON.stringify(request));
    const header = Buffer.alloc(4);
    if (process.arch === "x64" || process.arch === "arm64") header.writeUInt32LE(payload.length);
    else throw new Error("Probe framing only supports little-endian x64/arm64");
    return await new Promise((resolve, reject) => {
      const child = execFile(path.join(root, "native-host"), [caller],
        {timeout: 12000, maxBuffer: 8192, encoding: "buffer",
          env: {...process.env, HTTPS_PROXY: "http://127.0.0.1:1"}},
        (error, stdout, stderr) => {
          if (error) return reject(new Error("Helper process failed"));
          try {
            assert.equal(stderr.length, 0);
            assert.equal(stdout.readUInt32LE(0), stdout.length - 4);
            assert(!stdout.includes(Buffer.from(credential)));
            resolve(JSON.parse(stdout.subarray(4)));
          } catch {reject(new Error("Helper output assertion failed"));}
        });
      child.stdin.on("error", () => {});
      if (stalled) child.stdin.write(header.subarray(0, 1));
      else child.stdin.end(Buffer.concat([header, payload]));
    });
  }
  const request = {version: 1, action: "authenticate"};
  const first = await callHelper(request);
  assert.equal(first.ok, true, first.failure_type);
  assert.equal((await callHelper({...request, url: "https://example.invalid"})).ok, false);
  assert.equal((await callHelper(request, "chrome-extension://unapproved/")).ok, false);
  await chmod(path.join(root, "fictional-device.secret"), 0o644);
  assert.equal((await callHelper(request)).ok, false);
  await chmod(path.join(root, "fictional-device.secret"), 0o600);
  await put("fictional-device.secret", "fictional-revoked-value");
  assert.equal((await callHelper(request)).ok, false);
  await put("fictional-device.secret", credential);
  // A valid but unrelated CA must fail; malformed PEM alone is not a TLS test.
  const trustedCA = await readFile(path.join(root, "ca.pem"));
  run("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
    "-subj", "/CN=Unrelated fictional CA", "-keyout", "unrelated.key", "-out", "ca.pem",
    "-addext", "keyUsage=critical,keyCertSign,cRLSign"]);
  assert.equal((await callHelper(request)).ok, false);
  await put("ca.pem", trustedCA);
  assert.equal((await callHelper(request)).ok, true);
  for (const mode of ["redirect", "oversized", "duplicate", "token", "expired", "long-lived", "role"]) {
    enrollmentMode = mode;
    assert.equal((await callHelper(request)).ok, false, `Unexpected acceptance: ${mode}`);
  }
  enrollmentMode = "normal";
  assert.equal(redirectHits, 0);
  const config = await readFile(path.join(root, "fixture.json"));
  await put("fixture.json", JSON.stringify({origin: origin.replace("127.0.0.1", "localhost"),
    extension_origin: `chrome-extension://${extensionId}/`}));
  assert.equal((await callHelper(request)).ok, false);
  await put("fixture.json", config);
  await rename(path.join(root, "fictional-device.secret"), path.join(root, "saved.secret"));
  await symlink(path.join(root, "saved.secret"), path.join(root, "fictional-device.secret"));
  assert.equal((await callHelper(request)).ok, false);
  await rename(path.join(root, "fictional-device.secret"), path.join(root, "rejected-symlink"));
  await rename(path.join(root, "saved.secret"), path.join(root, "fictional-device.secret"));
  const start = Date.now();
  assert.equal((await callHelper(request, undefined, true)).ok, false);
  assert(Date.now() - start >= 9000 && Date.now() - start < 12000);
  assert.equal((await callHelper(request)).ok, true);
  console.log(JSON.stringify({helper: "PASS", cases: 18, secret_in_stdout: false,
    stderr_empty: true, proxy_ignored: true, retained_fixture: root}));
  if (helperOnly) {
    console.log("Browser checks NOT RUN (--helper-only); no browser handoff acceptance.");
  } else {
  authCount = 0;
  const launch = () => chromium.launchPersistentContext(profile, {
    executablePath: path.join(root, "browser"), headless: true, chromiumSandbox: true,
    env: {...process.env, XDG_CONFIG_HOME: path.join(root, "config"),
      XDG_CACHE_HOME: path.join(root, "cache"), XDG_DATA_HOME: path.join(root, "data"),
      TMPDIR: path.join(root, "t")},
    ignoreDefaultArgs: ["--disable-extensions"],
    args: [`--disable-extensions-except=${extension},${unapproved}`, `--load-extension=${extension},${unapproved}`],
  });
  context = await launch();
  const page = await context.newPage();
  await page.goto(origin); // Real trust and hostname validation; no ignore flags.
  assert.equal(await page.evaluate(async () => (await fetch('/session')).status), 401);
  const control = await context.newPage();
  await control.goto(`chrome-extension://${extensionId}/control.html`);
  await control.getByRole("button").click();
  await control.waitForFunction(() => document.querySelector('output').textContent !== '', {timeout: 15000});
  assert.equal(await control.locator("output").textContent(), "passed");
  assert.equal(authCount, 1);
  assert.equal(await page.evaluate(async () => (await fetch('/session')).status), 200);
  assert.equal(await page.evaluate(async () => (await fetch('/operator', {method:'POST'})).status), 403);
  assert.equal(await page.evaluate(() => document.cookie), "");
  assert.equal(await page.evaluate(() => typeof chrome.runtime?.sendNativeMessage), "undefined");
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  const cookies = await context.cookies(origin);
  assert.equal(cookies.length, 1);
  assert(cookies[0].httpOnly && cookies[0].secure && cookies[0].sameSite === "Strict");
  assert.equal(cookies[0].domain, "127.0.0.1");
  assert(!context.pages().some(p => p.url().includes(credential)));
  let crossPortCookieReceived = false;
  alternateServer = https.createServer({key: await readFile(path.join(root, "server.key")),
    cert: await readFile(path.join(root, "server.pem"))}, (request, response) => {
    crossPortCookieReceived ||= request.headers.cookie?.includes(cookieName + "=") || false;
    response.end("Fictional second HTTPS service");
  });
  await new Promise(resolve => alternateServer.listen(0, "127.0.0.1", resolve));
  await page.goto(`https://127.0.0.1:${alternateServer.address().port}/`);
  assert(crossPortCookieReceived, "Document the lack of TCP-port cookie isolation");
  await page.goto(origin);
  let outsider = context.serviceWorkers().find(w => !w.url().includes(extensionId));
  if (!outsider) outsider = await context.waitForEvent("serviceworker",
    {predicate: w => !w.url().includes(extensionId), timeout: 10000});
  const forbidden = await outsider.evaluate(async () => {
    try {
      await chrome.runtime.sendNativeMessage("org.sdsctl.local_probe", {version:1, action:"authenticate"});
      return false;
    } catch (error) {return error.message.includes("forbidden");}
  });
  assert(forbidden, "Browser must reject the unapproved extension before native launch");
  sessions.clear(); // Fixture authority invalidation; not a production revocation test.
  assert.equal(await page.evaluate(async () => (await fetch('/session')).status), 401);
  const previousAuthCount = authCount;
  await context.close();
  context = await launch();
  // Navigate the fixed startup entry as a future launcher would. Playwright
  // disallows page URLs in launch args; this is navigation, not cookie injection.
  const bootstrap = await context.newPage();
  await bootstrap.goto(`chrome-extension://${extensionId}/startup.html`);
  const recovered = await context.newPage();
  await recovered.goto(origin);
  const recoveryDeadline = Date.now() + 15000;
  while (authCount === previousAuthCount && Date.now() < recoveryDeadline)
    await new Promise(resolve => setTimeout(resolve, 100));
  assert(authCount > previousAuthCount, "Restart must authenticate anew, not reuse an invalid cookie");
  await recovered.waitForFunction(async () => (await fetch('/session')).status === 200,
    undefined, {timeout: 15000});
  assert.equal(await recovered.evaluate(async () => (await fetch('/session')).status), 200);
  assert.equal(await recovered.evaluate(() => document.cookie), "");
  console.log(JSON.stringify({result: "PASS", browser: context.browser()?.version(),
    checks: ["verified HTTPS", "native helper pipe", "display-only fixture session",
      "operator denied", "HttpOnly", "Secure/Strict/host-only", "empty page storage",
      "no URL credential", "fixture invalidation", "native host allowlist",
      "restart + fixed entry reauthentication"], cookie_port_isolation: false,
    retained_fixture: root}, null, 2));
  }
} finally {
  await context?.close();
  if (alternateServer) await new Promise(resolve => alternateServer.close(resolve));
  await new Promise(resolve => server.close(resolve));
}
