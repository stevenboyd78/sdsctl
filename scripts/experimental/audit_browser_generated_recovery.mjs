/** Installed candidate CLI/bundle + real first-run form + ASGI/native/Chromium.
 * Loopback fictional authority only. Never seeds recovery storage or cookies.
 * Retains private fixtures; no production service, trust-store or policy writes.
 */
import assert from "node:assert/strict";
import {createHash, generateKeyPairSync} from "node:crypto";
import {execFileSync, spawn} from "node:child_process";
import {mkdtemp, mkdir, writeFile, readFile, chmod, lstat, realpath} from "node:fs/promises";
import {existsSync} from "node:fs";
import {createInterface} from "node:readline";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import {fileURLToPath, pathToFileURL} from "node:url";

if (process.argv[2] === "--help") {
  console.log("Usage: node audit_browser_generated_recovery.mjs STAGE PLAYWRIGHT CHROMIUM CERTUTIL INSTALLED_PYTHON [SCENARIO] [ip|dns|ipv6] [review|startup]");
  console.log("Scenarios: healthy, deadline, truncated, tls-eof, server-restart, worker-restart, revoke, bad-ca, bad-name, resume, resume-stale.");
  console.log("Non-root Linux, five absolute paths, private existing stage, installed candidate wheel with web dependencies.");
  console.log("Actual CLI/generated bundle/setup form; no recovery-state seeding, cookie injection or production access.");
  console.log("Sandbox and verified TLS required; bwrap isolates temporary certificate trust. Retains fictional fixtures.");
  console.log("startup runs the installed foreground CLI with fixture-only headless/CDP flags, not a physical display.");
  process.exit(0);
}
const [stage, playwright, executable, certutil, python, scenario = "healthy", identityKind = "ip",
  flow = "review"] = process.argv.slice(2);
assert(process.platform === "linux" && process.getuid() !== 0);
assert([stage, playwright, executable, certutil, python].every(p => p && path.isAbsolute(p)));
assert(["healthy", "deadline", "truncated", "tls-eof", "server-restart", "worker-restart", "revoke", "bad-ca", "bad-name", "resume", "resume-stale"].includes(scenario));
assert(["ip", "dns", "ipv6"].includes(identityKind));
assert(["review", "startup"].includes(flow));
const info = await lstat(stage);
assert(info.isDirectory() && !info.isSymbolicLink() && info.uid === process.getuid() &&
  (info.mode & 0o777) === 0o700 && await realpath(stage) === stage, "Unsafe stage");
const source = path.dirname(fileURLToPath(import.meta.url));
const root = await mkdtemp(path.join(stage, "generated-"));
const step = name => console.log(JSON.stringify({step: name, scenario, identityKind, flow, fixture: root}));
step("prepare");
for (const name of ["nssdb", "data/pki/nssdb", "config", "cache", "t"])
  await mkdir(path.join(root, name), {recursive: true, mode: 0o700});
const put = (name, value, mode = 0o600) => writeFile(path.join(root, name), value, {mode, flag: "wx"});
const run = (command, args) => {
  try { return execFileSync(command, args, {cwd: root, stdio: "pipe", timeout: 20000}); }
  catch { throw new Error("Fictional fixture command failed; retain stage for review"); }
};
const cli = args => run(python, ["-I", "-c", "from sds200.cli import main; raise SystemExit(main())", ...args]).toString();
const publicKey = generateKeyPairSync("rsa", {modulusLength: 2048}).publicKey.export({type: "spki", format: "pem"});
await put("extension.pub.pem", publicKey);
const id = cli(["browser-device-bundle", "--experimental", "identity", "--public-key", path.join(root, "extension.pub.pem")])
  .match(/^Extension ID: ([a-p]{32})$/m)?.[1];
assert(id, "Installed identity command failed");
run("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
  "-subj", "/CN=Fictional generated-path CA", "-keyout", "ca.key", "-out", "ca.pem",
  "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign"]);
const hostname = {ip: "127.0.0.1", dns: "localhost", ipv6: "[::1]"}[identityKind];
const san = scenario === "bad-name" ? "DNS:wrong.invalid" :
  {ip: "IP:127.0.0.1", dns: "DNS:localhost", ipv6: "IP:::1"}[identityKind];
run("openssl", ["req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=Fictional server",
  "-keyout", "server.key", "-out", "server.csr"]);
await put("server.ext", `subjectAltName=${san}\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n`);
run("openssl", ["x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
  "-CAcreateserial", "-days", "1", "-extfile", "server.ext", "-out", "server.pem"]);
let nativeCA = "ca.pem";
if (scenario === "bad-ca") {
  nativeCA = "unrelated-ca.pem";
  run("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
    "-subj", "/CN=Unrelated fictional CA", "-keyout", "unrelated-ca.key", "-out", nativeCA]);
  await chmod(path.join(root, nativeCA), 0o600);
  await chmod(path.join(root, "unrelated-ca.key"), 0o600);
}
for (const name of ["ca.pem", "ca.key", "server.key"]) await chmod(path.join(root, name), 0o600);
const nss = path.join(root, "nssdb");
run(certutil, ["-N", "-d", `sql:${nss}`, "--empty-password"]);
run(certutil, ["-A", "-d", `sql:${nss}`, "-n", "fictional-generated", "-t", "C,,", "-i", "ca.pem"]);
let backendPort, failHandshake = scenario === "tls-eof";
const sockets = new Set();
const proxy = net.createServer(client => {
  sockets.add(client); client.on("close", () => sockets.delete(client)); client.on("error", () => {});
  if (failHandshake) { failHandshake = false; client.once("data", () => client.end()); return; }
  const upstream = net.connect(backendPort, "127.0.0.1");
  sockets.add(upstream); upstream.on("close", () => sockets.delete(upstream));
  upstream.on("error", () => client.destroy()); client.on("close", () => upstream.destroy());
  upstream.on("close", () => client.destroy()); client.pipe(upstream).pipe(client);
});
await new Promise(resolve => proxy.listen(0, identityKind === "ipv6" ? "::1" : "127.0.0.1", resolve));
const origin = `https://${hostname}:${proxy.address().port}`;
const service = spawn(python, ["-I", path.join(source, "browser_recovery_server.py"), root, origin, "generated"],
  {cwd: root, stdio: ["pipe", "pipe", "pipe"]});
let errors = false, exited = false;
service.stderr.on("data", () => { errors = true; }); // Never print exception payloads or secrets.
const messages = [], waiters = [];
const lines = createInterface({input: service.stdout});
lines.on("line", line => {
  let value;
  try { value = JSON.parse(line); } catch { value = null; }
  if (waiters.length) waiters.shift()(value); else messages.push(value);
});
service.on("exit", () => { exited = true; while (waiters.length) waiters.shift()(null); });
const receive = async () => {
  if (exited) throw new Error("Acceptance server exited");
  let timer;
  try {
    const value = messages.length ? messages.shift() : await Promise.race([
      new Promise(resolve => waiters.push(resolve)),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Fixture reply deadline")), 15000); }),
    ]);
    assert(value, "Acceptance server acknowledgement missing"); return value;
  } finally { clearTimeout(timer); }
};
const command = async action => { service.stdin.write(JSON.stringify({action}) + "\n"); return receive(); };
const until = async (check, timeout = 15000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (await check()) return; await new Promise(resolve => setTimeout(resolve, 200)); }
  throw new Error("Acceptance condition timed out");
};
// Installed foreground Chromium can take ~23 seconds to initialize its cookie
// store in this isolated cold-start fixture. Terminal reporting waits for verified
// cleanup, so use a bounded startup deadline rather than weakening the assertion
// or selecting a different password-store/security mode for the test browser.
const terminalStartupTimeout = 60000;
let context, launcher;
try {
  const ready = await receive(); assert(ready.ready); backendPort = ready.port;
  cli(["browser-device-profile", "--experimental", "create", "--directory", path.join(root, "native"),
    "--enrollment-file", path.join(root, "enrollment.json"), "--ca-file", path.join(root, nativeCA),
    "--origin", origin, "--device-id", "fixture", "--extension-id", id]);
  cli(["browser-device-bundle", "--experimental", "create", "--directory", path.join(root, "bundle"),
    "--profile", path.join(root, "native"), "--public-key", path.join(root, "extension.pub.pem")]);
  cli(["browser-device-register", "--experimental", "--directory", path.join(root, "browser-data"),
    "--bundle", path.join(root, "bundle"), "--profile", path.join(root, "native"),
    "--public-key", path.join(root, "extension.pub.pem")]);
  const receipt = JSON.parse(await readFile(path.join(root, "bundle/bundle.json")));
  const legacy = path.join(os.homedir(), ".pki/nssdb"), mount = existsSync(legacy) ? legacy : path.join(root, "data/pki/nssdb");
  const fixtureFlags = flow === "startup" ? ["--headless", "--remote-debugging-port=0"] : [];
  await put("browser", `#!/usr/bin/python3\nimport os,sys\nextra=[] if sys.argv[1:]==['--version'] else ${JSON.stringify(fixtureFlags)}\nos.execvp('bwrap',['bwrap','--ro-bind','/','/','--bind',${JSON.stringify(root)},${JSON.stringify(root)},'--bind',${JSON.stringify(nss)},${JSON.stringify(mount)},'--dev-bind','/dev','/dev','--proc','/proc','--',${JSON.stringify(executable)},*extra,*sys.argv[1:]])\n`, 0o700);
  const {chromium} = await import(pathToFileURL(playwright));
  const extension = path.join(root, "bundle/extension");
  const environment = {...process.env, XDG_CONFIG_HOME: path.join(root, "config"),
    XDG_CACHE_HOME: path.join(root, "cache"), XDG_DATA_HOME: path.join(root, "data"),
    TMPDIR: path.join(root, "t")};
  const launchArguments = ["browser-device-start", "--experimental", "--directory", path.join(root,"browser-data"),
    "--bundle",path.join(root,"bundle"),"--profile",path.join(root,"native"),
    "--public-key",path.join(root,"extension.pub.pem"),"--browser",path.join(root,"browser")];
  const reviewLaunch = () => chromium.launchPersistentContext(path.join(root, "browser-data"), {
    executablePath: path.join(root, "browser"), chromiumSandbox: true, headless: true, timeout: 20000,
    env: environment,
    ignoreDefaultArgs: ["--disable-extensions"],
    args: [`--load-extension=${extension}`, `--disable-extensions-except=${extension}`]});
  const launch = async (setup = false) => {
    if(flow === "review") return reviewLaunch();
    assert(cli([...launchArguments,"--check"]).includes("valid offline"));
    const activePort=path.join(root,"browser-data/DevToolsActivePort");
    const previous=await lstat(activePort).then(s=>s.mtimeMs).catch(()=>null);
    const child=spawn(python,["-I","-c","from sds200.cli import main; raise SystemExit(main())",
      ...launchArguments,...(setup?["--setup"]:[])],
    {cwd:root,env:{...environment,DISPLAY:environment.DISPLAY||":fixture"},stdio:["ignore","pipe","pipe"]});
    launcher=child;child.stdout.resume();child.stderr.resume();
    let ended=false,code;const ending=new Promise(resolve=>child.once("exit",value=>{ended=true;code=value;resolve();}));
    let port;
    await until(async()=>{
      assert(!ended,"Managed launcher exited before CDP readiness");
      const info=await lstat(activePort).catch(()=>null);
      if(!info||info.mtimeMs===previous)return false;
      port=Number((await readFile(activePort,"utf8")).split("\n")[0]);
      return Number.isSafeInteger(port)&&port>0&&port<65536;
    },25000);
    const browser=await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
    const result=browser.contexts()[0];assert(result);
    result.close=async()=>{
      if(!ended){const session=await browser.newBrowserCDPSession();
        await session.send("Browser.close").catch(()=>{});}
      await until(()=>ended,15000);await ending;await browser.close();
      assert.equal(code,0,"Managed launcher did not observe a clean close");launcher=null;
    };
    return result;
  };
  let control;
  const openControl = async () => {
    control = await context.newPage(); await control.goto(`chrome-extension://${id}/control.html`);
  };
  const mode = async () => (await control.evaluate(() => chrome.runtime.sendMessage({action: "status"})))?.mode;
  const stored = () => control.evaluate(() => chrome.storage.local.get(null));
  const hasCookie = async () => (await context.cookies(origin)).some(c => c.name === "__Host-sdsctl-device-session");
  const nativeRetry = () => JSON.parse(run(python, ["-I", "-c", `
import json, sqlite3, sys
from pathlib import Path
from contextlib import closing
with closing(sqlite3.connect(Path(sys.argv[1]).as_uri() + '?mode=ro', uri=True)) as db:
    mode, failures = db.execute('SELECT mode,failures FROM recovery').fetchone()
print(json.dumps({'mode': mode, 'failures': failures}))
`, path.join(root, "native/recovery.sqlite")]).toString());
  const worker = async () => context.serviceWorkers()[0] || context.waitForEvent("serviceworker", {timeout: 15000});
  step("launch-unprovisioned"); context = await launch(); await openControl();
  assert.equal((await worker()).url(), `chrome-extension://${id}/worker.mjs`);
  await until(async () => await mode() === "setup_error");
  assert.equal((await command("status")).exchanges, 0);
  assert.equal(Object.keys(await stored()).length, 0); assert(!await hasCookie());
  if(flow === "startup") {
    await until(()=>context.pages().some(p=>p.url()===`chrome-extension://${id}/startup.html`));
    const startup=context.pages().find(p=>p.url()===`chrome-extension://${id}/startup.html`);
    assert(startup,"Launcher did not open its fixed startup page");
    await startup.waitForFunction(()=>document.getElementById("notice").textContent.startsWith("First-run setup"));
    await startup.screenshot({path:path.join(root,"startup-required.png")});
    await context.close();context=await launch(true);await openControl();
    await until(()=>context.pages().some(p=>p.url()===`chrome-extension://${id}/setup.html`));
  }
  const setup = flow === "startup" ? context.pages().find(p=>p.url()===`chrome-extension://${id}/setup.html`)
    : await context.newPage();
  assert(setup,"Explicit setup launch did not open its fixed page");
  if(flow === "review") await setup.goto(`chrome-extension://${id}/setup.html`);
  assert.equal(await setup.locator("dd").nth(0).textContent(), origin);
  assert.equal(await setup.locator("dd").nth(1).textContent(), "fixture");
  assert.equal(await setup.locator("dd").nth(2).textContent(), id);
  await setup.locator("#confirm").check(); await setup.locator("#initialize").click();
  await setup.waitForFunction(() => document.getElementById("notice").textContent.startsWith("Setup saved."));
  assert.equal((await command("status")).exchanges, 0); assert(!await hasCookie());
  const initial = (await stored()).sdsctlDeviceRecovery;
  assert(initial.identity === receipt.identity && initial.phase === "clean" && !initial.paused && initial.nextAt === 0);
  step("first-run-committed-without-login");
  if (["deadline", "truncated"].includes(scenario)) await command(scenario);
  await context.close(); context = await launch(); await openControl();
  const trustFailure = ["bad-ca", "bad-name"].includes(scenario);
  if (trustFailure) {
    await until(async () => await mode() === "tls_error", terminalStartupTimeout);
    assert.equal((await command("status")).exchanges, 0); assert(!await hasCookie());
    step("untrusted-peer-rejected-before-credential");
  } else {
    if (["deadline", "truncated", "tls-eof"].includes(scenario)) {
      if (scenario === "deadline") {
        await until(async () => (await stored()).sdsctlDeviceRecovery.phase === "native_retry", 20000);
      } else {
        // A retryable native result's mode is ACTIVE (recovery allowed), not
        // evidence of login. Inspect only non-secret persisted failure fields;
        // neither this read nor the server counter wakes or drives the extension.
        await until(() => { const state = nativeRetry();
          return state.mode === "active" && state.failures === 1; }, 20000);
      }
      assert(!await hasCookie(), "Interrupted exchange installed a cookie");
      const alarms = await control.evaluate(() => chrome.alarms.getAll());
      assert(alarms.some(a => a.name === "sdsctl-device-recovery" && a.scheduledTime > Date.now()),
        "Interrupted exchange did not schedule recovery");
      step("interrupted-await-real-alarm");
    }
    await until(async () => await mode() === "active" && await hasCookie(), 100000);
    const cookies = await context.cookies(origin);
    assert(cookies.some(c => c.name === "__Host-sdsctl-device-session" && c.httpOnly && c.secure && c.sameSite === "Strict"));
    let page;
    if(flow === "startup") {
      await until(()=>context.pages().some(p=>p.url()===origin+"/device-display"));
      page=context.pages().find(p=>p.url()===origin+"/device-display");
      await page.waitForLoadState("domcontentloaded");
      await page.bringToFront();
    } else {page = await context.newPage(); await page.goto(origin);}
    const sessionStatus = async () => {
      try {return await page.evaluate(async () => (await fetch("/auth/session")).status);}
      catch {return 0;} // A managed entry may navigate during a read; never count it as a pass.
    };
    await until(async()=>await sessionStatus()===200);
    assert.equal(await page.evaluate(() => document.cookie), "");
    if(flow === "startup") {
      await page.getByRole("button",{name:"Open dashboard menu",exact:true}).waitFor();
      await page.screenshot({path:path.join(root,"managed-display.png")});
    }
    const denied = await page.evaluate(async () => Promise.all([
      ["/api/v1/control", "POST"], ["/api/v1/audio", "GET"], ["/api/v1/recordings", "GET"],
      ["/api/v1/recordings/file/fixture", "GET"], ["/api/v1/admin", "POST"],
    ].map(async ([url, method]) => (await fetch(url, {method})).status)));
    assert(denied.every(status => status === 403), "Display-only privilege boundary failed");
    step("authenticated-display-only");
    if (scenario === "server-restart") {
      await command("restart"); await until(async()=>await sessionStatus()===401);
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
      const before = (await command("status")).exchanges;
      step("worker-stopped-await-real-renewal-alarm");
      // Do not query extension status here: that could itself wake the worker.
      await until(async () => (await command("status")).exchanges > before, 100000);
      await until(async () => await sessionStatus() === 200); await cdp.detach();
    }
    if (scenario === "revoke") {
      await command("revoke"); await until(async()=>await sessionStatus()===401);
      await until(async () => await mode() === "credential_rejected", 100000);
      await until(async () => !await hasCookie()); step("revocation-rejected");
    } else {
      step("explicit-sign-out");
      await page.getByRole("button", {name: "Open dashboard menu", exact: true}).click();
      await page.getByRole("button", {name: "Sign out and pause automatic login", exact: true}).click();
      await until(async () => (await command("status")).state === "paused");
      await until(async () => !await hasCookie());
      await until(async()=>await sessionStatus()===401);
      if (scenario.startsWith("resume")) {
        step("administrator-allows-device-without-local-resume");
        await command("resume");
        assert(!await hasCookie());
        const resuming=await context.newPage();
        await resuming.goto(`chrome-extension://${id}/resume.html`);
        assert.equal(await resuming.locator("dd").nth(0).textContent(),origin);
        assert.equal(await resuming.locator("dd").nth(1).textContent(),"fixture");
        assert.equal(await resuming.locator('input[type="password"]').count(),0);
        assert(await resuming.locator("#resume").isDisabled());
        await resuming.locator("#review").click();
        await resuming.waitForFunction(()=>document.getElementById("notice").textContent.startsWith("Server permission verified."));
        assert(!await hasCookie());
        await resuming.screenshot({path:path.join(root,"resume-reviewed.png")});
        if (scenario === "resume-stale") await command("pause");
        await resuming.locator("#confirm").check(); await resuming.locator("#resume").click();
        if (scenario === "resume-stale") {
          await resuming.waitForFunction(()=>document.getElementById("notice").textContent.startsWith("Resume could not be confirmed."));
          assert(!await hasCookie());
          const saved=(await stored()).sdsctlDeviceRecovery;
          assert(saved.paused && saved.phase === "resume_pending");
          step("stale-review-stayed-paused");
        } else {
          await resuming.waitForFunction(()=>document.getElementById("notice").textContent.startsWith("Automatic sign-in resumed."),null,{timeout:40000});
          assert(await hasCookie());
          await resuming.screenshot({path:path.join(root,"resume-complete.png")});
          const saved=(await stored()).sdsctlDeviceRecovery;
          assert(!saved.paused && saved.phase === "clean");
          const temporary=context.pages().filter(p=>p.url()===origin+"/device-display");
          assert.equal(temporary.length,0,"Resume verification tab was not retired");
          await page.goto(origin); await until(async()=>await sessionStatus()===200);
          assert.equal(await page.evaluate(()=>document.cookie),"");
          const actual=await page.evaluate(async()=>await (await fetch("/auth/session")).json());
          assert(actual.display_only && actual.device_enrolled);
          step("real-resume-form-and-protected-session-passed");
          await page.getByRole("button",{name:"Open dashboard menu",exact:true}).click();
          await page.getByRole("button",{name:"Sign out and pause automatic login",exact:true}).click();
          await until(async()=>(await command("status")).state === "paused" && !await hasCookie());
        }
      }
    }
  }
  const beforeRestart = (await command("status")).exchanges;
  await context.close(); context = await launch(); await openControl();
  const expected = trustFailure ? "tls_error" : scenario === "revoke" ? "credential_rejected" : "paused";
  await until(async () => await mode() === expected, terminalStartupTimeout);
  assert.equal((await command("status")).exchanges, beforeRestart); assert(!await hasCookie());
  if(flow === "startup") {
    const startup=context.pages().find(p=>p.url()===`chrome-extension://${id}/startup.html`);assert(startup);
    const expectedNotice = trustFailure ? "Certificate verification failed." : scenario === "revoke"
      ? "This display credential was rejected or revoked." : "Automatic sign-in is paused.";
    await startup.waitForFunction(prefix=>document.getElementById("notice").textContent.startsWith(prefix),
      expectedNotice, {timeout:terminalStartupTimeout});
    await startup.screenshot({path:path.join(root,"startup-terminal.png")});
    assert(context.pages().every(p=>!p.url().includes("/auth/login")&&!p.url().includes("/auth/display/login")));
  }
  const browserState = JSON.stringify(await stored());
  const secret = (await readFile(path.join(root, "native/device.secret"), "utf8")).trim();
  assert(!browserState.includes(secret) && !browserState.includes("sdsctl-browser-session-v1."), "Secret persisted in browser storage");
  const result = {passed: true, scenario, identityKind, flow, managedLauncher:flow==="startup",
    physicalDisplay:false,browser: context.browser().version(),
    installedRuntime: true, generatedBundle: true, realSetupForm: true, realASGI: true,
    realNative: true, sandbox: true, verifiedTLS: true, stateSeeding: false, cookieInjection: false,
    realResumeForm: scenario.startsWith("resume"),
    firstRunExchanges: 0, exchanges: beforeRestart, restartMode: expected, realAlarms: true,
    identity: receipt.identity, bundleHashes: receipt.files};
  result.harnessHashes = Object.fromEntries(await Promise.all([
    "audit_browser_generated_recovery.mjs", "browser_recovery_server.py",
  ].map(async name => [name, createHash("sha256").update(await readFile(path.join(source, name))).digest("hex")])));
  await put("result.json", JSON.stringify(result, null, 2) + "\n");
  step("passed"); console.log(JSON.stringify(result));
} finally {
  try { await context?.close(); } finally {
    if(launcher&&launcher.exitCode===null) launcher.kill("SIGTERM");
    if (!exited) {
      service.stdin.end(JSON.stringify({action: "stop"}) + "\n");
      await new Promise(resolve => {
        const timer = setTimeout(() => service.kill("SIGKILL"), 5000);
        service.once("exit", () => { clearTimeout(timer); resolve(); });
      });
    }
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => proxy.close(resolve));
    lines.close();
    if (errors) step("fixture-emitted-redacted-diagnostics");
  }
}
