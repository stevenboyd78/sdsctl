// Private fixture only: inspect actual fixed native context, never browser state
// or credentials. Exiting this observer disconnects CDP without closing Chromium.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const [modulePath, port, extensionId, build] = process.argv.slice(2);
assert(modulePath.startsWith('/') && /^\d{1,5}$/.test(port) && Number(port) > 0 &&
  Number(port) < 65536 && /^[a-p]{32}$/.test(extensionId) && /^[a-f0-9]{64}$/.test(build));
const {chromium} = await import(pathToFileURL(modulePath));
const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`, {timeout: 15000});
const context = browser.contexts()[0];
assert(context);
const expected = `chrome-extension://${extensionId}/worker.mjs`;
const worker = context.serviceWorkers().find(w => w.url() === expected) ||
  await context.waitForEvent('serviceworker', {predicate: w => w.url() === expected, timeout: 15000});
const result = await worker.evaluate(async expectedBuild => {
  const value = await chrome.runtime.sendNativeMessage('org.sdsctl.browser_device',
    {version: 1, action: 'worker-context', build: expectedBuild});
  return {ok: value?.ok === true, role: value?.role ?? null,
    mode: value?.continuation?.mode ?? value?.mode ?? null,
    buildMatches: value?.build === expectedBuild,
    extensionIdMatches: value?.extensionId === chrome.runtime.id};
}, build);
const report = {readOnlyDiagnostic: true, nativeContext: result,
  pageURLs: context.pages().map(p => p.url())};
process.stdout.write(JSON.stringify(report) + '\n', () => process.exit(0));
