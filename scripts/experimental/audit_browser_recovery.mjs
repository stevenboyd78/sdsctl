/** Actual dashboard middleware + native helper + sandboxed Chromium on loopback.
 * No scanner, production credentials, user profile/trust changes or TLS bypass.
 * One-shot fault followed by real alarm recovery; no fake clocks or cookie injection.
 */
import assert from "node:assert/strict";
import {createHash, generateKeyPairSync} from "node:crypto";
import {execFileSync, spawn} from "node:child_process";
import {mkdtemp, mkdir, writeFile, readFile, copyFile, chmod, lstat, realpath} from "node:fs/promises";
import {existsSync} from "node:fs";
import {createInterface} from "node:readline";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import {fileURLToPath, pathToFileURL} from "node:url";
import {initialBrowserRecoveryState} from "./browser_device_recovery.mjs";

if (process.argv[2] === "--help") {
  console.log("Usage: node audit_browser_recovery.mjs STAGE PLAYWRIGHT CHROMIUM CERTUTIL PYTHON [SCENARIO] [ip|dns|ipv6]");
  console.log("Scenarios: deadline, truncated, tls-eof, server-restart, worker-restart, revoke, document-probe.");
  console.log("Private mode-0700 stage; five absolute paths. Actual ASGI/native/Chromium on loopback only.");
  console.log("Sandbox and TLS verification enabled. No cookie injection; real clocks and retry alarms.");
  process.exit(0);
}
const [stage, playwright, executable, certutil, python, scenario = "deadline", identityKind = "ip"] = process.argv.slice(2);
assert([stage, playwright, executable, certutil, python].every(p => p && path.isAbsolute(p)));
assert(["deadline", "truncated", "tls-eof", "server-restart", "worker-restart", "revoke", "document-probe"].includes(scenario));
assert(["ip", "dns", "ipv6"].includes(identityKind));
const info = await lstat(stage);
assert(info.isDirectory() && !info.isSymbolicLink() && info.uid === process.getuid() &&
  (info.mode & 0o777) === 0o700 && await realpath(stage) === stage);
const {chromium} = await import(pathToFileURL(playwright));
const source = path.dirname(fileURLToPath(import.meta.url)), repo = path.resolve(source, "../..");
const root = await mkdtemp(path.join(stage, "recovery-"));
const step = name => console.log(JSON.stringify({step: name, scenario, identityKind, fixture: root}));
for (const name of ["profile/NativeMessagingHosts", "extension", "nssdb", "data/pki/nssdb", "t"])
  await mkdir(path.join(root, name), {recursive: true, mode: 0o700});
const put = (name, value, mode = 0o600) => writeFile(path.join(root, name), value, {mode});
const run = (command, args) => execFileSync(command, args, {cwd: root, stdio: "pipe"});
run("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
  "-subj", "/CN=Fictional recovery acceptance CA", "-keyout", "ca.key", "-out", "ca.pem",
  "-addext", "keyUsage=critical,keyCertSign,cRLSign"]);
await chmod(path.join(root, "ca.pem"), 0o600);
const hostname = {ip:"127.0.0.1",dns:"localhost",ipv6:"[::1]"}[identityKind];
run("openssl", ["req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", `/CN=${hostname}`,
  "-keyout", "server.key", "-out", "server.csr"]);
await put("server.ext", `subjectAltName=${{ip:"IP:127.0.0.1",dns:"DNS:localhost",ipv6:"IP:::1"}[identityKind]}\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n`);
run("openssl", ["x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
  "-CAcreateserial", "-days", "1", "-extfile", "server.ext", "-out", "server.pem"]);
const nss = path.join(root, "nssdb");
run(certutil, ["-N", "-d", `sql:${nss}`, "--empty-password"]);
run(certutil, ["-A", "-d", `sql:${nss}`, "-n", "fictional-recovery", "-t", "C,,", "-i", "ca.pem"]);
let backendPort, failHandshake = scenario === "tls-eof";
const sockets = new Set();
const proxy = net.createServer(client => {
  sockets.add(client); client.on("close", () => sockets.delete(client));
  client.on("error", () => {});
  if (failHandshake) {
    failHandshake = false;
    client.once("data", () => client.end());
    return;
  }
  const upstream = net.connect(backendPort, "127.0.0.1");
  sockets.add(upstream); upstream.on("close", () => sockets.delete(upstream));
  upstream.on("error", () => client.destroy());
  client.on("close", () => upstream.destroy());
  upstream.on("close", () => client.destroy());
  client.pipe(upstream).pipe(client); // TLS terminates only at the real Python server.
});
await new Promise(resolve => proxy.listen(0, identityKind === "ipv6" ? "::1" : "127.0.0.1", resolve));
const origin = `https://${hostname}:${proxy.address().port}`;
const key = generateKeyPairSync("rsa", {modulusLength: 2048}).publicKey.export({type: "spki", format: "der"});
const id = [...createHash("sha256").update(key).digest("hex").slice(0, 32)]
  .map(c => String.fromCharCode(97 + parseInt(c, 16))).join("");
await put("client.json", JSON.stringify({version: 1, origin, device_id: "fixture", extension_origin: `chrome-extension://${id}/`}));
const service = spawn(python, [path.join(source, "browser_recovery_server.py"), root, origin],
  {cwd: repo, env: {...process.env, PYTHONPATH: path.join(repo, "src")}, stdio: ["pipe", "pipe", "pipe"]});
let errors = "", exited = false;
service.stderr.on("data", chunk => { errors = (errors + chunk.toString()).slice(-8192); });
const messages = [], waiters = [];
createInterface({input: service.stdout}).on("line", line => {
  const value = JSON.parse(line);
  if (waiters.length) waiters.shift()(value); else messages.push(value);
});
service.on("exit", () => { exited = true; while (waiters.length) waiters.shift()(null); });
const receive = async () => {
  if (exited) throw new Error("Acceptance server exited");
  const value = messages.length ? messages.shift() : await new Promise(resolve => waiters.push(resolve));
  assert(value, "Acceptance server exited before acknowledgement");
  return value;
};
const command = async action => { service.stdin.write(JSON.stringify({action}) + "\n"); return receive(); };
let context;
try {
  const ready = await receive(); assert(ready.ready); backendPort = ready.port;
  const config = {origin, identity: ready.identity, nativeHost: "org.sdsctl.browser_device"};
  if (["deadline", "truncated"].includes(scenario)) await command(scenario);
  for (const name of ["browser_device_recovery.mjs", "browser_device_logout.mjs",
    ...(scenario === "document-probe" ? ["browser_device_continuation_probe.mjs"] : [])])
    await copyFile(path.join(repo, "src/sds200/browser_assets", name), path.join(root, "extension", name));
  await put("extension/manifest.json", JSON.stringify({manifest_version: 3, version: "0.0.1",
    name: "SDSCTL FICTIONAL INTEGRATED RECOVERY", key: key.toString("base64"),
    permissions: ["nativeMessaging", "storage", "cookies", "alarms",
      ...(scenario === "document-probe" ? ["tabs"] : [])], host_permissions: [`https://${hostname}/*`],
    background: {service_worker: "worker.mjs", type: "module"},
    content_scripts: [{matches: [`https://${hostname}/*`], js: ["content.js"], run_at: "document_start"}]}));
  await put("extension/worker.mjs", `import {connectChromeRecovery} from './browser_device_recovery.mjs';
import {connectLogoutWorker} from './browser_device_logout.mjs';
globalThis.fixtureController=connectChromeRecovery(chrome, ${JSON.stringify(config)});
connectLogoutWorker(chrome, fixtureController, ${JSON.stringify(origin)});\n`+
    (scenario === "document-probe" ? `import {createContinuationProbe} from './browser_device_continuation_probe.mjs';
globalThis.openFixtureProbe=async()=>{globalThis.fixtureProbe=createContinuationProbe(chrome,${JSON.stringify(origin)});return fixtureProbe.open();};\n` : ""));
  await put("extension/content.js", (await readFile(path.join(repo, "src/sds200/browser_assets/browser_device_logout.mjs"), "utf8"))
    .replaceAll("export ", "") + `\nif(location.href===${JSON.stringify(origin + "/")})connectLogoutContent({document,window,runtime:chrome.runtime,fetcher:fetch.bind(globalThis)},${JSON.stringify(origin)});\n`+
    (scenario === "document-probe" ? '\n{\n'+(await readFile(path.join(repo,
      "src/sds200/browser_assets/browser_device_continuation_probe.mjs"),"utf8")).replaceAll("export ","")+
      `\nconnectContinuationProbeContent({window,runtime:chrome.runtime,fetcher:fetch.bind(globalThis)},${JSON.stringify(origin)});\n}\n` : ""));
  await put("extension/control.html", "<!doctype html><title>Fictional recovery control</title>");
  await put("native-host", `#!${python}\nimport os,sys\nfrom pathlib import Path\nsys.path.insert(0,${JSON.stringify(path.join(repo, "src"))})\nfrom sds200.browser_device_native import run_browser_native\nraise SystemExit(run_browser_native(Path(${JSON.stringify(root)}),sys.argv[1:],os.fdopen(os.dup(0),'rb',buffering=0),os.fdopen(os.dup(1),'wb',buffering=0)))\n`, 0o700);
  await put("profile/NativeMessagingHosts/org.sdsctl.browser_device.json", JSON.stringify({name: config.nativeHost,
    description: "Fictional acceptance only", type: "stdio", path: path.join(root, "native-host"), allowed_origins: [`chrome-extension://${id}/`]}));
  const legacy = path.join(os.homedir(), ".pki/nssdb"), mount = existsSync(legacy) ? legacy : path.join(root, "data/pki/nssdb");
  await put("browser", `#!/usr/bin/python3\nimport os,sys\nos.execvp('bwrap',['bwrap','--ro-bind','/','/','--bind',${JSON.stringify(root)},${JSON.stringify(root)},'--bind',${JSON.stringify(nss)},${JSON.stringify(mount)},'--dev-bind','/dev','/dev','--proc','/proc','--',${JSON.stringify(executable)},*sys.argv[1:]])\n`, 0o700);
  const launch = () => chromium.launchPersistentContext(path.join(root, "profile"), {
    executablePath: path.join(root, "browser"), chromiumSandbox: true, headless: true, timeout: 15000,
    env: {...process.env, XDG_CONFIG_HOME: path.join(root, "config"), XDG_CACHE_HOME: path.join(root, "cache"),
      XDG_DATA_HOME: path.join(root, "data"), TMPDIR: path.join(root, "t")},
    ignoreDefaultArgs: ["--disable-extensions"],
    args: [`--load-extension=${path.join(root, "extension")}`, `--disable-extensions-except=${path.join(root, "extension")}`]});
  const until = async (check, timeout = 15000) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) { if (await check()) return; await new Promise(resolve => setTimeout(resolve, 200)); }
    throw new Error("Acceptance condition timed out");
  };
  const worker = async () => context.serviceWorkers()[0] || context.waitForEvent("serviceworker", {timeout: 15000});
  const mode = async () => (await worker()).evaluate(() => fixtureController.status().mode);
  step("launch-unprovisioned"); context = await launch();
  await until(async () => await mode() === "setup_error");
  assert.equal((await command("status")).exchanges, 0);
  const control = await context.newPage(); await control.goto(`chrome-extension://${id}/control.html`);
  await control.evaluate(state => chrome.storage.local.set({sdsctlDeviceRecovery: state}), initialBrowserRecoveryState(config));
  await context.close(); context = await launch();
  if (["deadline", "truncated", "tls-eof"].includes(scenario)) {
    step("await-interruption");
    await until(async () => (scenario === "tls-eof" && !failHandshake) || await mode() === "waiting" ||
      (scenario !== "deadline" && (await command("status")).exchanges > 0), 20000);
    if (scenario === "deadline") {
      assert.equal(await (await worker()).evaluate(async () =>
        (await chrome.storage.local.get("sdsctlDeviceRecovery")).sdsctlDeviceRecovery.phase), "native_retry");
    }
    step("await-real-alarm-recovery");
  }
  await until(async () => await mode() === "active" && (await context.cookies(origin)).some(c =>
    c.name === "__Host-sdsctl-device-session" && c.httpOnly && c.secure && c.sameSite === "Strict"), 100000);
  step("authenticated");
  let page = await context.newPage(); await page.goto(origin);
  const sessionStatus = () => page.evaluate(async () => (await fetch("/auth/session")).status);
  assert.equal(await sessionStatus(), 200);
  assert.equal(await page.evaluate(() => document.cookie), "");
  assert.equal(await page.evaluate(async () => (await fetch("/api/v1/control", {method: "POST"})).status), 403);
  let documentProbe=null;
  if(scenario === "document-probe") {
    step("document-probe-real-isolated-message-sender");
    const w=await worker(),before=await w.evaluate(()=>chrome.storage.local.get(null));
    const ledger=await readFile(path.join(root,"recovery.sqlite"));
    const cookies=await context.cookies(origin),exchanges=(await command("status")).exchanges;
    const selected=await w.evaluate(()=>openFixtureProbe());
    assert.match(selected.documentId,/^[a-zA-Z0-9-]{1,128}$/);
    assert.match(selected.ticket,/^[a-f0-9]{64}$/);
    const verdict=await w.evaluate(()=>fixtureProbe.verify());
    assert.deepEqual({...verdict,remainingSeconds:null},{url:origin+'/device-display',...selected,
      displayOnly:true,deviceEnrolled:true,remainingSeconds:null});
    assert(verdict.remainingSeconds>30&&verdict.remainingSeconds<=80);
    await w.evaluate(()=>fixtureProbe.close());
    await until(()=>!context.pages().some(p=>p.url()===origin+'/device-display'));
    step("document-probe-replacement-refusal");
    await w.evaluate(()=>openFixtureProbe());
    const replaced=context.pages().find(p=>p.url()===origin+'/device-display');assert(replaced);
    await replaced.reload().catch(()=>{}); // A loading replacement closes only this owned tab.
    assert.equal(await w.evaluate(async()=>{try{await fixtureProbe.verify();return false;}catch{return true;}}),true);
    await until(()=>!context.pages().some(p=>p.url()===origin+'/device-display'));
    step("document-probe-pending-close");
    await w.evaluate(()=>openFixtureProbe());
    const held=context.pages().find(p=>p.url()===origin+'/device-display');assert(held);
    let entered,release;
    const reached=new Promise(resolve=>{entered=resolve;}),unblock=new Promise(resolve=>{release=resolve;});
    await held.route(origin+'/auth/session',async route=>{entered();await unblock;await route.continue().catch(()=>{});});
    const stopped=w.evaluate(async()=>{try{await fixtureProbe.verify();return false;}catch{return true;}});
    try {
      await Promise.race([reached,new Promise((_,reject)=>setTimeout(()=>reject(Error('Probe request missing')),5000))]);
      await w.evaluate(()=>fixtureProbe.close());assert.equal(await stopped,true);
    } finally {release();await stopped;}
    await until(()=>!context.pages().some(p=>p.url()===origin+'/device-display'));
    assert.deepEqual(await w.evaluate(()=>chrome.storage.local.get(null)),before);
    assert.deepEqual(await readFile(path.join(root,"recovery.sqlite")),ledger);
    assert.deepEqual(await context.cookies(origin),cookies);
    assert.equal((await command("status")).exchanges,exchanges);
    documentProbe={realSenderDocument:true,exactDocumentTarget:true,protectedFetch:true,
      replacementRefused:true,pendingCloseRefused:true,ownedTabsClosed:true,
      storageUnchanged:true,nativeUnchanged:true,cookieUnchanged:true,noSessionIssuance:true,
      notContinuationControllerAcceptance:true,cookieDomain:cookies.find(c=>
        c.name==='__Host-sdsctl-device-session').domain};
  }
  if (scenario === "server-restart") {
    await command("restart");
    assert.equal(await sessionStatus(), 401);
    step("await-server-restart-recovery");
    await until(async () => await sessionStatus() === 200, 100000);
  }
  if (scenario === "worker-restart") {
    const cdp = await context.newCDPSession(page); let version;
    cdp.on("ServiceWorker.workerVersionUpdated", e => {
      version = e.versions.find(v => v.scriptURL === `chrome-extension://${id}/worker.mjs`) || version;
    });
    await cdp.send("ServiceWorker.enable"); await until(() => version?.runningStatus === "running");
    await cdp.send("ServiceWorker.stopWorker", {versionId: version.versionId});
    await until(() => version?.runningStatus === "stopped");
    step("worker-stopped-await-renewal");
    const before = (await command("status")).exchanges;
    await until(async () => (await command("status")).exchanges > before, 100000);
    assert.equal(await sessionStatus(), 200); await cdp.detach();
  }
  if (scenario === "revoke") {
    await command("revoke");
    assert.equal(await sessionStatus(), 401);
    await until(async () => await mode() === "credential_rejected", 100000);
  } else {
    step("explicit-sign-out");
    await page.getByRole("button", {name: "Open dashboard menu", exact: true}).click();
    await page.getByRole("button", {name: "Sign out and pause automatic login", exact: true}).click();
    await until(async () => (await command("status")).state === "paused");
    await until(async () => !(await context.cookies(origin)).some(c => c.name === "__Host-sdsctl-device-session"));
  }
  const beforeRestart = (await command("status")).exchanges;
  await context.close(); context = await launch();
  await until(async () => await mode() === (scenario === "revoke" ? "credential_rejected" : "paused"));
  assert.equal((await command("status")).exchanges, beforeRestart);
  assert(!(await context.cookies(origin)).some(c => c.name === "__Host-sdsctl-device-session"));
  const stored = await (await worker()).evaluate(() => chrome.storage.local.get(null));
  const secret = await readFile(path.join(root, "device.secret"), "utf8");
  assert(!JSON.stringify(stored).includes(secret) && !JSON.stringify(stored).includes("sdsctl-browser-session-v1."));
  const result = {passed: true, scenario, identityKind, realASGI: true, realNative: true,
    sandbox: true, verifiedTLS: true, cookieInjection: false, realAlarms: true,
    browser: context.browser().version(), exchanges: beforeRestart,documentProbe};
  result.sourceHashes = Object.fromEntries(await Promise.all([
    "src/sds200/browser_device_native.py", "src/sds200/browser_device_recovery.py",
    "src/sds200/browser_assets/browser_device_recovery.mjs", "src/sds200/browser_assets/browser_device_logout.mjs",
    "scripts/experimental/browser_recovery_server.py", "scripts/experimental/audit_browser_recovery.mjs",
    ...(scenario === "document-probe" ? ["src/sds200/browser_assets/browser_device_continuation_probe.mjs"] : []),
  ].map(async name => [name, createHash("sha256").update(await readFile(path.join(repo, name))).digest("hex")])));
  await put("result.json", JSON.stringify(result, null, 2) + "\n"); console.log(JSON.stringify(result));
} finally {
  await context?.close();
  if (!exited) { service.stdin.end(JSON.stringify({action: "stop"}) + "\n");
    await new Promise(resolve => { const timer = setTimeout(() => service.kill(), 5000);
      service.once("exit", () => { clearTimeout(timer); resolve(); }); }); }
  for (const socket of sockets) socket.destroy();
  await new Promise(resolve => proxy.close(resolve));
  if (errors) step("server-diagnostics-retained-in-memory-not-printed");
}
