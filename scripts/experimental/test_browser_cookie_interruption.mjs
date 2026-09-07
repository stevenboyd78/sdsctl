import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import {cookieProbeWorkerSource, processArguments} from "./browser_cookie_interruption.mjs";

test("Chromium NUL arguments and rewritten process titles match exact tokens", () => {
  const args = ["/usr/lib/chromium/chromium", "--user-data-dir=/tmp/fixture/profile", "--headless"];
  assert.deepEqual(processArguments(args.join("\0") + "\0"), args);
  assert.deepEqual(processArguments(args.join(" ") + "\0"), args);
  assert(!processArguments(args.join(" ")).includes("--user-data-dir=/tmp/fixture"));
});

test("fault hook accepts only the two fixed cookie operations", () => {
  for (const value of ["get", "set;throw 1", "", null]) assert.throws(() => cookieProbeWorkerSource(value));
});

for (const operation of ["set", "remove"]) {
  test(`${operation}: hook dispatches the actual API and forwards its eventual result`, async () => {
    let finish, calls = 0;
    const payload = {fixture: true};
    const chrome = {cookies: {
      get: () => null, set: () => null, remove: () => null,
      [operation]: (...args) => { calls++; assert.deepEqual(args, [payload]); return new Promise(resolve => { finish = resolve; }); },
    }};
    const context = vm.createContext({chrome});
    vm.runInContext(cookieProbeWorkerSource(operation), context);
    const adapter = vm.runInContext("recoveryChrome", context);
    context.fixtureCookieProbe.enabled = true;
    const pending = adapter.cookies[operation](payload);
    assert.equal(context.fixtureCookieProbe.stage, "ready"); assert.equal(calls, 0);
    context.fixtureReleaseCookie(); await Promise.resolve();
    assert.equal(calls, 1); assert.equal(context.fixtureCookieProbe.stage, "dispatched");
    finish(payload);
    assert.equal(await pending, payload); assert.equal(context.fixtureCookieProbe.stage, "settled");
    assert.equal(context.fixtureCookieProbe.enabled, false);
  });

  test(`${operation}: hook forwards rejection without inventing a completed operation`, async () => {
    let reject;
    const context = vm.createContext({chrome: {cookies: {
      [operation]: () => new Promise((_, fail) => { reject = fail; }),
    }}});
    vm.runInContext(cookieProbeWorkerSource(operation), context);
    context.fixtureCookieProbe.enabled = true;
    const pending = vm.runInContext(`recoveryChrome.cookies.${operation}({})`, context);
    context.fixtureReleaseCookie(); await Promise.resolve();
    reject(new Error("fixture failure"));
    await assert.rejects(pending, /fixture failure/);
    assert.equal(context.fixtureCookieProbe.stage, "rejected");
  });
}
