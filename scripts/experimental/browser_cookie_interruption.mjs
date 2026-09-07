// Test-only fault injection. Never import into a production extension/launcher.
import assert from "node:assert/strict";
import {readFile, readdir, stat} from "node:fs/promises";

// Chromium may rewrite /proc cmdline into a space-separated process title.
// Fixture paths must have no whitespace; ambiguous paths fail closed.
export const processArguments = cmd => cmd.split(/[\0\s]+/).filter(Boolean);

export function cookieProbeWorkerSource(operation) {
  assert(["set", "remove"].includes(operation));
  return `
const probe = globalThis.fixtureCookieProbe = {enabled: ${operation === "set"}, stage: 'idle', clearDispatched: false};
const realCookies = chrome.cookies;
const probeCookies = {
  get: (...args) => realCookies.get(...args),
  set: (...args) => realCookies.set(...args),
  remove: (...args) => { probe.clearDispatched = true; return realCookies.remove(...args); },
};
probeCookies.${operation} = async (...args) => {
  if (!probe.enabled) return realCookies.${operation}(...args);
  probe.enabled = false;
  probe.stage = 'ready';
  await new Promise(resolve => { globalThis.fixtureReleaseCookie = resolve; });
  const pending = realCookies.${operation}(...args); // Actual browser API, no fake result.
  probe.stage = 'dispatched';
  try { const value = await pending; probe.stage = 'settled'; return value; }
  catch (error) { probe.stage = 'rejected'; throw error; }
};
const recoveryChrome = {...chrome, cookies: probeCookies};
`;
}

// Resolve only this CDP browser's network subprocess, and verify Linux process
// identity/ownership/profile/ancestry again immediately before each signal.
async function processRecord(pid) {
  assert(Number.isSafeInteger(pid) && pid > 1);
  const base = `/proc/${pid}`;
  const [cmd, status, stat] = await Promise.all([
    readFile(`${base}/cmdline`, "utf8"), readFile(`${base}/status`, "utf8"), readFile(`${base}/stat`, "utf8")]);
  return {pid, args: processArguments(cmd), parent: Number(status.match(/^PPid:\s+(\d+)/m)?.[1]),
    uid: Number(status.match(/^Uid:\s+(\d+)/m)?.[1]),
    namespacePids: (status.match(/^NSpid:\s+(.+)/m)?.[1] || String(pid)).trim().split(/\s+/).map(Number),
    started: stat.slice(stat.lastIndexOf(")") + 2).split(" ")[19],
    stopped: /^State:\s+T/m.test(status)};
}

async function networkTarget(context, profile, bounded) {
  assert(!/\s/.test(profile), "Unambiguous fixture profile path required");
  const cdp = await context.browser().newBrowserCDPSession();
  try {
    const {processInfo} = await bounded(cdp.send("SystemInfo.getProcessInfo"));
    const browser = processInfo.filter(p => p.type === "browser");
    assert.equal(browser.length, 1);
    // CDP reports IDs inside the browser's namespace. Resolve to host PIDs only
    // after matching the exact private profile; never signal an unscoped CDP ID.
    const records = [];
    for (const entry of await readdir("/proc")) {
      if (!/^[1-9][0-9]*$/.test(entry)) continue;
      try {
        if ((await stat(`/proc/${entry}`)).uid !== process.getuid()) continue;
        const record = await processRecord(Number(entry));
        if (/(?:^|\/)(?:chrome|chromium)$/.test(record.args[0])) records.push(record);
      } catch { /* Exited process, never a target. */ }
    }
    const mains = records.filter(record => record.args.includes(`--user-data-dir=${profile}`) &&
      !record.args.some(arg => arg.startsWith("--type=")) && record.namespacePids.includes(browser[0].id));
    if (mains.length !== 1) console.log(JSON.stringify({step: "fixture-process-identity-mismatch",
      browserPid: browser[0].id, inspectedBrowserProcesses: records.length,
      profileMatches: records.filter(record => record.args.some(arg => arg.includes(profile))).map(record => ({
        pid: record.pid, parent: record.parent, namespacePids: record.namespacePids,
        exactProfile: record.args.includes(`--user-data-dir=${profile}`),
        child: record.args.some(arg => arg.startsWith("--type="))}))}));
    assert.equal(mains.length, 1, "Exact fixture browser/namespace identity required");
    const main = mains[0];
    assert.equal(main.uid, process.getuid());
    assert(main.args.includes(`--user-data-dir=${profile}`));
    const candidates = [];
    for (const record of records) {
      try {
        if (!record.args.includes("--utility-sub-type=network.mojom.NetworkService") ||
            !processInfo.some(p => record.namespacePids.includes(p.id))) continue;
        let ancestor = record.parent;
        for (let depth = 0; ancestor !== main.pid && depth < 8; depth++) ancestor = (await processRecord(ancestor)).parent;
        if (ancestor === main.pid) candidates.push(record);
      } catch { /* Process may have exited; never signal an unresolved candidate. */ }
    }
    assert.equal(candidates.length, 1, "Exactly one fixture network service required");
    const target = candidates[0];
    assert.equal(target.uid, process.getuid());
    let ancestor = target.parent;
    for (let depth = 0; ancestor !== main.pid && depth < 8; depth++) ancestor = (await processRecord(ancestor)).parent;
    assert.equal(ancestor, main.pid);
    return async signal => {
      const current = await processRecord(target.pid), currentMain = await processRecord(main.pid);
      assert.equal(current.started, target.started); assert.equal(current.uid, target.uid);
      assert.equal(current.parent, target.parent); assert.deepEqual(current.args, target.args);
      assert.equal(currentMain.started, main.started); assert.deepEqual(currentMain.args, main.args);
      process.kill(target.pid, signal);
      return target.pid;
    };
  } finally { await cdp.detach(); }
}

export async function testCookieInterruption({context, worker, profile, id, origin, operation, recoveryBeforeResume = false, bounded, until, step}) {
  assert(["set", "remove"].includes(operation));
  const control = await context.newPage();
  await control.goto(`chrome-extension://${id}/control.html`);
  if (operation === "remove") {
    await until(async () => await bounded(worker.evaluate(() => fixtureController.status().mode)) === "active");
    assert((await context.cookies(origin)).some(c => c.name === "__Host-sdsctl-device-session"));
    await bounded(worker.evaluate(() => { fixtureCookieProbe.enabled = true; void fixtureController.suspend(); }));
  }
  await until(async () => await bounded(worker.evaluate(() => fixtureCookieProbe.stage)) === "ready");
  const cdp = await context.newCDPSession(control);
  let version, resume, networkStopped = false;
  try {
    cdp.on("ServiceWorker.workerVersionUpdated", event => {
      version = event.versions.find(v => v.scriptURL === `chrome-extension://${id}/worker.mjs`) || version;
    });
    await bounded(cdp.send("ServiceWorker.enable"));
    await until(() => version?.runningStatus === "running");
    resume = await networkTarget(context, profile, bounded);
    const pid = await resume("SIGSTOP"); networkStopped = true;
    await until(async () => (await processRecord(pid)).stopped);
    step("fixture-network-stopped");
    await bounded(worker.evaluate(() => { fixtureReleaseCookie(); }));
    await until(async () => await bounded(worker.evaluate(() => fixtureCookieProbe.stage)) === "dispatched");
    if (operation === "set") await bounded(worker.evaluate(() => { void fixtureController.suspend(); }));
    await until(async () => await bounded(worker.evaluate(async () =>
      (await chrome.storage.local.get("sdsctlDeviceRecovery")).sdsctlDeviceRecovery.paused)) === true);
    assert.equal(await bounded(worker.evaluate(() => fixtureCookieProbe.stage)), "dispatched");
    assert((await processRecord(pid)).stopped);
    step("real-cookie-operation-pending-with-durable-pause");
    await bounded(cdp.send("ServiceWorker.stopWorker", {versionId: version.versionId}));
    await until(() => version?.runningStatus === "stopped");
    step("worker-stopped-before-cookie-completion");
    if (recoveryBeforeResume) {
      await bounded(control.evaluate(() => chrome.runtime.sendMessage({action: "status"})));
      // Playwright may reuse its Worker wrapper for a restarted CDP target.
      // The observed stopped event, not JS wrapper identity, proves termination.
      const recoveringWorker = context.serviceWorkers().find(w => w.url() === `chrome-extension://${id}/worker.mjs`) ||
        await context.waitForEvent("serviceworker", {timeout: 10000});
      await until(async () => await bounded(recoveringWorker.evaluate(() => fixtureCookieProbe.clearDispatched)) === true);
      assert((await processRecord(pid)).stopped);
      step("recovery-cleanup-queued-before-network-resume");
    }
    await resume("SIGCONT"); networkStopped = false;
    // Observe the browser cookie jar before waking a replacement worker. An old
    // request may complete after worker death; no cookie is supplied by the driver.
    const cookieAfterDeath = recoveryBeforeResume ? null :
      (await context.cookies(origin)).some(c => c.name === "__Host-sdsctl-device-session");
    await bounded(control.evaluate(() => chrome.runtime.sendMessage({action: "status"})));
    await until(async () => !(await context.cookies(origin)).some(c => c.name === "__Host-sdsctl-device-session"));
    const page = await context.newPage();
    await page.goto(origin);
    assert.equal(await page.evaluate(async () => (await fetch("/auth/session")).status), 401);
    return {operation, actualBrowserAPIPending: true, durablePauseBeforeTermination: true,
      stoppedWorkerObserved: true, recoveryBeforeResume, cookieAfterDeath,
      cookieAbsentAfterRecovery: true, protectedReadAfterRecovery: 401};
  } finally {
    if (networkStopped) await resume("SIGCONT");
    await cdp.detach();
  }
}
