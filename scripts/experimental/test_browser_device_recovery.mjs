import test from "node:test";
import assert from "node:assert/strict";
import {createBrowserRecovery, initialBrowserRecoveryState, connectChromeRecovery,
  BrowserNativeDisconnected, sendChromeNative, DEVICE_COOKIE, RECOVERY_ALARM} from "./browser_device_recovery.mjs";

const config = {origin: "https://192.0.2.18:8443", identity: "a".repeat(64),
  nativeHost: "org.sdsctl.browser_device"};
const token = "sdsctl-browser-session-v1." + "b".repeat(64);
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => {
  resolve = a; reject = b;
}); return {promise, resolve, reject}; };
const settle = () => new Promise(resolve => setImmediate(resolve));

function fixture(saved = initialBrowserRecoveryState(config)) {
  const f = {saved, cookie: null, alarm: null, time: 1000000, calls: [], writes: [],
    revision: 2, nativeMode: "active"};
  const status = () => ({version: 1, ok: true, mode: f.nativeMode, revision: f.revision,
    retry_after: 0, renew_after: 0});
  f.status = status;
  f.ports = {
    now: () => f.time,
    load: async () => structuredClone(f.saved),
    save: async state => { f.saved = structuredClone(state); f.writes.push(structuredClone(state)); },
    native: async request => {
      f.calls.push(request.action);
      assert.deepEqual(Object.keys(request).sort(), ["action", "version"]);
      if (request.action === "suspend") { f.nativeMode = "paused"; f.revision++; }
      if (request.action !== "authenticate") return status();
      return {...status(), renew_after: 225, session: {token, expires_in: 300}};
    },
    clearCookie: async () => { f.cookie = null; },
    setCookie: async cookie => { f.cookie = structuredClone(cookie); },
    schedule: async when => { f.alarm = when; },
    cancel: async () => { f.alarm = null; },
  };
  f.make = () => createBrowserRecovery(f.ports, config);
  return f;
}

test("verified response becomes exact host-only HttpOnly cookie, not storage or UI output", async () => {
  const f = fixture(), c = f.make();
  assert.deepEqual(await c.tick(), {mode: "active"});
  assert.deepEqual(f.cookie, {url: config.origin + "/", name: DEVICE_COOKIE,
    value: token, path: "/", secure: true, httpOnly: true, sameSite: "strict", expirationDate: 1300});
  assert.equal(f.alarm, 1225000);
  assert.equal(f.saved.phase, "clean");
  assert(f.writes.some(state => state.phase === "installing"));
  assert(!JSON.stringify([f.writes, c.status()]).includes(token));
});

test("renewal wait survives worker recreation and a missing alarm", async () => {
  const f = fixture();
  await f.make().tick();
  f.alarm = null;
  assert.deepEqual(await f.make().tick(), {mode: "waiting"});
  assert.equal(f.calls.filter(x => x === "authenticate").length, 1);
  assert.equal(f.alarm, 1225000);
  f.time = 1250000; // Missed alarm fires late.
  await f.make().tick();
  assert.equal(f.calls.filter(x => x === "authenticate").length, 2);
});

test("short backoff uses browser-safe 30-second scheduling", async () => {
  const f = fixture();
  f.ports.native = async () => ({...f.status(), retry_after: 2});
  assert.deepEqual(await f.make().tick(), {mode: "waiting"});
  assert.equal(f.alarm - f.time, 30000);
});

test("clock rollback caps an old persisted wait", async () => {
  const f = fixture();
  f.saved.nextAt = f.time + 9000000;
  await f.make().tick();
  assert.equal(f.alarm - f.time, 300000);
  f.time += 300000;
  assert.deepEqual(await f.make().tick(), {mode: "active"});
});

test("simultaneous alarm/start requests coalesce into one native exchange", async () => {
  const f = fixture(), c = f.make();
  await Promise.all(Array.from({length: 100}, () => c.tick()));
  assert.equal(f.calls.filter(x => x === "authenticate").length, 1);
});

for (const mode of ["paused", "credential_rejected", "tls_error", "setup_error", "protocol_error"]) {
  test(`native ${mode} clears cookie and does not authenticate`, async () => {
    const f = fixture(); f.nativeMode = mode; f.cookie = {value: token};
    const c = f.make();
    assert.deepEqual(await c.tick(), {mode});
    assert.deepEqual(c.status(), {mode});
    assert.deepEqual(f.calls, ["status"]);
    assert.equal(f.cookie, null); assert.equal(f.alarm, null);
  });
}

for (const saved of [undefined, null, {}, {...initialBrowserRecoveryState(config), identity: "c".repeat(64)},
  {...initialBrowserRecoveryState(config), paused: "false"},
  {...initialBrowserRecoveryState(config), token},
  {...initialBrowserRecoveryState(config), nextAt: Infinity}]) {
  test(`missing/unsafe state fails closed (${JSON.stringify(saved)?.slice(0, 45)})`, async () => {
    const f = fixture(); f.saved = saved;
    assert.deepEqual(await f.make().tick(), {mode: "setup_error"});
    assert.deepEqual(f.calls, []);
    assert.equal(f.cookie, null);
  });
}

test("interrupted cookie installation is cleared before any new native exchange", async () => {
  const f = fixture(); f.saved.phase = "installing"; f.cookie = {value: token};
  const native = f.ports.native;
  f.ports.native = async request => { if (f.calls.length === 0) assert.equal(f.cookie, null);
    return native(request); };
  assert.deepEqual(await f.make().tick(), {mode: "active"});
});

test("suspend persists while authenticate is blocked, discards late success", async () => {
  const f = fixture(), response = deferred(), native = f.ports.native;
  f.ports.native = request => request.action === "authenticate" ? response.promise : native(request);
  const c = f.make(), run = c.tick(); await settle();
  const stop = c.suspend(); await settle();
  assert.equal(f.saved.paused, true);
  response.resolve({...f.status(), renew_after: 225, session: {token, expires_in: 300}});
  await run;
  assert.deepEqual(await stop, {mode: "paused", localPauseSaved: true,
    nativePaused: true, cookieCleared: true, serverRevocation: "unconfirmed"});
  assert.equal(f.cookie, null);
  assert.equal(f.writes.some(state => state.phase === "installing"), false);
  await f.make().tick();
  assert.equal(f.cookie, null); assert.equal(f.saved.paused, true);
});

test("suspend during pending cookie set clears only after set settles", async () => {
  const f = fixture(), release = deferred(), entered = deferred();
  f.ports.setCookie = async cookie => { entered.resolve(); await release.promise; f.cookie = cookie; };
  const c = f.make(), run = c.tick(); await entered.promise;
  let acknowledged = false;
  const stop = c.suspend().then(result => { acknowledged = true; return result; });
  await settle();
  assert.equal(f.saved.paused, true); assert.equal(acknowledged, false);
  release.resolve(); await run; await stop;
  assert.equal(f.cookie, null); assert.equal(f.alarm, null);
  assert.equal(f.saved.paused, true);
});

test("ordered storage writes cannot overwrite a newer pause", async () => {
  const f = fixture(), release = deferred(), entered = deferred(), save = f.ports.save;
  f.ports.save = async state => {
    if (state.phase === "installing" && !state.paused) { entered.resolve(); await release.promise; }
    await save(state);
  };
  const c = f.make(), run = c.tick(); await entered.promise;
  const stop = c.suspend(); release.resolve();
  await Promise.all([run, stop]);
  assert.equal(f.saved.paused, true); assert.equal(f.cookie, null);
  const firstPause = f.writes.findIndex(x => x.paused);
  assert(f.writes.slice(firstPause).every(x => x.paused));
});

test("lost native suspend acknowledgement persists pause and schedules reconciliation", async () => {
  const f = fixture();
  f.ports.native = async () => { throw new Error(token); };
  const result = await f.make().suspend();
  assert.equal(result.nativePaused, false); assert.equal(result.cookieCleared, true);
  assert.equal(f.saved.paused, true); assert.equal(f.alarm, f.time + 30000);
  const next = await f.make().tick();
  assert.equal(next.mode, "paused"); assert.equal(next.nativePaused, false);
  assert(!JSON.stringify(next).includes(token));
});

test("cookie removal failure never acknowledges completed cleanup", async () => {
  const f = fixture(); f.ports.clearCookie = async () => { throw new Error(token); };
  const result = await f.make().suspend();
  assert.equal(result.cookieCleared, false); assert.equal(result.nativePaused, true);
  assert.equal(f.alarm, f.time + 30000);
});

test("failed persistence reports setup error, never a saved pause", async () => {
  const f = fixture(); f.ports.save = async () => { throw new Error(token); };
  const c = f.make();
  assert.deepEqual(await c.suspend(), {mode: "setup_error"});
  assert.deepEqual(await c.tick(), {mode: "setup_error"});
  assert.equal(f.cookie, null);
});

test("cold startup racing suspend persists intent before native I/O", async () => {
  const f = fixture(), loaded = deferred(), release = deferred();
  f.ports.load = () => loaded.promise;
  const save = f.ports.save, native = f.ports.native;
  f.ports.save = async state => { await release.promise; await save(state); };
  f.ports.native = request => { assert.equal(f.saved.paused, true); return native(request); };
  const c = f.make(), tick = c.tick(), stop = c.suspend();
  loaded.resolve(f.saved); await settle();
  assert.deepEqual(f.calls, []); assert.equal(c.status().mode, "stopping");
  release.resolve(); await Promise.all([tick, stop]);
  assert.equal(f.saved.paused, true); assert.equal(f.cookie, null);
});

test("native generation change before install rejects the token", async () => {
  const f = fixture(), native = f.ports.native;
  let statuses = 0;
  f.ports.native = request => {
    if (request.action === "status" && ++statuses === 2) f.revision++;
    return native(request);
  };
  assert.deepEqual(await f.make().tick(), {mode: "waiting"});
  assert.equal(f.cookie, null);
});

test("native pause after installation removes token", async () => {
  const f = fixture(), set = f.ports.setCookie;
  f.ports.setCookie = async details => { await set(details); f.nativeMode = "paused"; f.revision++; };
  assert.deepEqual(await f.make().tick(), {mode: "waiting"});
  assert.equal(f.cookie, null);
});

for (const response of [null, {ok: false},
  {version: 1, ok: true, mode: "active", revision: 2, retry_after: 0, renew_after: 225,
    session: {token: "operator-secret", expires_in: 300}},
  {version: 1, ok: true, mode: "active", revision: 2, retry_after: Infinity, renew_after: 0}]) {
  test(`malformed native response is redacted (${JSON.stringify(response)?.slice(0, 40)})`, async () => {
    const f = fixture(); f.ports.native = async () => response;
    assert.deepEqual(await f.make().tick(), {mode: "setup_error"});
    assert.equal(f.cookie, null);
  });
}

test("too-short remaining lifetime is not installed", async () => {
  const f = fixture(), native = f.ports.native;
  f.ports.native = async request => request.action === "authenticate" ?
    {...f.status(), renew_after: 15, session: {token, expires_in: 20}} : native(request);
  assert.deepEqual(await f.make().tick(), {mode: "waiting"});
  assert.equal(f.cookie, null); assert.equal(f.alarm, f.time + 30000);
});

test("cookie expiry conservatively includes native response latency", async () => {
  const f = fixture(), native = f.ports.native;
  f.ports.native = async request => { const result = await native(request);
    if (request.action === "authenticate") f.time += 5000;
    return result;
  };
  await f.make().tick();
  assert.equal(f.cookie.expirationDate, 1300);
});

test("resume is absent and repeated starts cannot override durable pause", async () => {
  const f = fixture(), c = f.make(); await c.suspend();
  assert.equal(c.resume, undefined);
  await Promise.all([c.tick(), c.tick(), f.make().tick()]);
  assert.equal(f.calls.includes("authenticate"), false);
});

for (const origin of ["http://192.0.2.1", "https://ha.example/", "https://user@ha.example",
  "https://ha.example/path", "https://ha.example?x=y"]) {
  test(`invalid trusted origin rejected: ${origin}`, () => {
    assert.throws(() => createBrowserRecovery({}, {...config, origin}));
  });
}

test("Chrome adapter restricts messages, private storage, fixed alarm and cookie flags", async () => {
  const f = fixture(); let listener, alarmListener, access;
  const chrome = {
    runtime: {id: "a".repeat(32), getURL: page => `chrome-extension://${"a".repeat(32)}/${page}`,
      sendNativeMessage: (host, request) => { assert.equal(host, config.nativeHost); return f.ports.native(request); },
      onMessage: {addListener: callback => { listener = callback; }}},
    storage: {local: {
      setAccessLevel: async options => { access = options; },
      get: async key => ({[key]: f.saved}), set: async value => f.ports.save(Object.values(value)[0]),
    }},
    alarms: {create: async (name, info) => { assert.equal(name, RECOVERY_ALARM); f.alarm = info.when; },
      clear: f.ports.cancel, onAlarm: {addListener: callback => { alarmListener = callback; }}},
    cookies: {remove: f.ports.clearCookie, get: async () => f.cookie,
      set: async details => { await f.ports.setCookie(details);
        return {...details, hostOnly: true, session: false}; }},
  };
  const c = connectChromeRecovery(chrome, config); await settle();
  assert.deepEqual(access, {accessLevel: "TRUSTED_CONTEXTS"});
  const sender = {id: chrome.runtime.id, url: chrome.runtime.getURL("control.html")};
  const deny = () => assert.fail("Unauthorized message responded");
  assert.equal(listener({action: "suspend"}, {...sender, url: config.origin}, deny), false);
  assert.equal(listener({action: "suspend"}, {...sender, id: "other"}, deny), false);
  assert.equal(listener({action: "resume"}, sender, deny), false);
  assert.equal(listener({action: "start", origin: config.origin}, sender, deny), false);
  const calls = f.calls.length;
  alarmListener({name: "unrelated"}); await settle(); assert.equal(f.calls.length, calls);
  const response = deferred();
  assert.equal(listener({action: "suspend"}, sender, response.resolve), true);
  assert.equal((await response.promise).cookieCleared, true);
  assert.equal(c.status().mode, "paused"); assert.equal(f.cookie, null);
});

function adapterFixture(f, changeCookie = value => value) {
  const ignore = {addListener() {}};
  return {
    runtime: {id: "a".repeat(32), getURL: page => `chrome-extension://${"a".repeat(32)}/${page}`,
      sendNativeMessage: (_, request) => f.ports.native(request), onMessage: ignore},
    storage: {local: {setAccessLevel: async () => {},
      get: async key => ({[key]: f.saved}), set: async value => f.ports.save(Object.values(value)[0])}},
    alarms: {create: async (_, info) => { f.alarm = info.when; },
      clear: f.ports.cancel, onAlarm: ignore},
    cookies: {remove: () => f.ports.clearCookie(), get: async () => f.cookie,
      set: async details => { await f.ports.setCookie(details);
        return changeCookie({...details, hostOnly: true, session: false}); }},
  };
}

for (const change of [() => null, x => ({...x, name: "operator"}),
  x => ({...x, httpOnly: false}), x => ({...x, secure: false}),
  x => ({...x, hostOnly: false}), x => ({...x, path: "/wrong"}),
  x => ({...x, sameSite: "lax"}), x => ({...x, session: true}),
  x => ({...x, expirationDate: 0}), x => ({...x, expirationDate: x.expirationDate + 10})]) {
  test(`Chrome cookie installation validation fails closed: ${change.toString()}`, async () => {
    const f = fixture();
    const c = connectChromeRecovery(adapterFixture(f, change), config);
    assert.deepEqual(await c.tick(), {mode: "setup_error"});
    assert.equal(f.cookie, null); assert.equal(f.alarm, null);
  });
}

test("Chrome cookie removal acknowledgement is checked against actual absence", async () => {
  const f = fixture(); f.saved.paused = true; f.cookie = {value: token};
  f.ports.clearCookie = async () => {}; // API resolves but cookie still exists.
  const c = connectChromeRecovery(adapterFixture(f), config);
  const result = await c.tick();
  assert.equal(result.cookieCleared, false); assert.equal(result.localPauseSaved, true);
});

test("Chrome storage access restriction failure never launches native authentication", async () => {
  const f = fixture(), chrome = adapterFixture(f);
  chrome.storage.local.setAccessLevel = async () => { throw new Error(token); };
  const c = connectChromeRecovery(chrome, config);
  assert.deepEqual(await c.tick(), {mode: "setup_error"});
  assert.deepEqual(f.calls, []);
});

test("native disconnect clears cookie and persists a retry across worker recreation", async () => {
  const f = fixture(), native = f.ports.native;
  f.cookie = {value: token};
  f.ports.native = async request => {
    if (request.action === "authenticate") throw new BrowserNativeDisconnected();
    return native(request);
  };
  const c = f.make();
  assert.deepEqual(await c.tick(), {mode: "waiting"});
  assert.equal(f.cookie, null);
  assert.equal(f.saved.phase, "native_retry");
  assert.equal(f.alarm, f.time + 60000);
  const calls = f.calls.length;
  for (let i = 0; i < 5; i++) await f.make().tick();
  assert.equal(f.calls.length, calls); // Early/manual starts cannot bypass the retry budget.
  f.ports.native = native;
  f.time += 60000;
  assert.deepEqual(await f.make().tick(), {mode: "active"});
  assert.equal(f.cookie.value, token);
  assert.equal(f.saved.phase, "clean");
});

test("native retry rollback is bounded and saved, not extended on every wake", async () => {
  const f = fixture();
  f.saved.phase = "native_retry"; f.saved.nextAt = f.time + 9000000;
  assert.deepEqual(await f.make().tick(), {mode: "waiting"});
  assert.equal(f.saved.nextAt, f.time + 60000);
  assert.equal(f.calls.length, 0);
  f.time += 60000;
  assert.deepEqual(await f.make().tick(), {mode: "active"});
});

for (const action of ["suspend", "beginLogout"]) {
  test(`${action} wins over an outstanding native disconnect`, async () => {
    const f = fixture(), blocked = deferred(), native = f.ports.native;
    f.ports.native = request => request.action === "authenticate" ? blocked.promise : native(request);
    const c = f.make(), run = c.tick(); await settle();
    const stop = c[action](); await settle();
    blocked.reject(new BrowserNativeDisconnected());
    await run; await stop;
    assert.equal(f.saved.paused, true);
    assert.notEqual(f.saved.phase, "native_retry");
    f.time += 60001;
    assert.equal((await f.make().tick()).mode, "paused");
    assert.equal(f.cookie, null);
    assert.equal(f.nativeMode, "paused");
  });
}

test("native retry never conceals unsafe persistence or failed cookie removal", async () => {
  for (const broken of ["save", "clearCookie"]) {
    const f = fixture();
    f.ports.native = async () => { throw new BrowserNativeDisconnected(); };
    f.ports[broken] = async () => { throw new Error(token); };
    const c = f.make();
    assert.deepEqual(await c.tick(), {mode: "setup_error"});
    assert.equal(f.alarm, null);
  }
});

test("Chrome adapter distinguishes broken pipes from permanent native setup/protocol errors", async () => {
  for (const message of ["Native host has exited.", "Specified native messaging host not found.",
    "Access to the specified native messaging host is forbidden.",
    "Error when communicating with the native messaging host.", token]) {
    const chrome = {runtime: {sendNativeMessage: async () => { throw new Error(message); }}};
    await assert.rejects(sendChromeNative(chrome, config.nativeHost, {version: 1, action: "status"}),
      error => (error instanceof BrowserNativeDisconnected) === (message === "Native host has exited.") &&
        !error.message.includes(token));
  }
});
