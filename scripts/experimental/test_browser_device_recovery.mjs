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

function freshFixture() {
  const f = fixture(); f.saved = undefined; f.revision = 1;
  const native = f.ports.native;
  f.ports.native = async request => {
    if (request.action !== "claim-browser") return native(request);
    f.calls.push(request.action);
    assert.equal(f.saved.phase, "setup_pending");
    assert.equal(f.saved.paused, true);
    if (f.revision !== 1 || f.nativeMode !== "active") return {version:1,ok:false,mode:"setup_error"};
    f.revision = 2;
    return f.status();
  };
  return f;
}

test("first-run is explicit, records pending before claim, and does not authenticate", async () => {
  const f = freshFixture(), c = f.make();
  assert.equal((await c.tick()).mode, "setup_error");
  assert.equal(f.saved, undefined); assert.deepEqual(f.calls, []);
  assert.deepEqual(await c.initialize(), {mode:"ready"});
  assert.deepEqual(f.calls, ["claim-browser"]);
  assert.deepEqual(f.saved, initialBrowserRecoveryState(config));
  assert.equal(f.cookie,null); assert.equal(f.alarm,null);
  assert.equal((await c.tick()).mode,"active");
  const snapshot = structuredClone([f.saved,f.cookie,f.alarm]);
  assert.equal((await c.initialize()).mode,"setup_refused");
  assert.deepEqual([f.saved,f.cookie,f.alarm],snapshot);
});

test("simultaneous setup requests consume one native claim", async () => {
  const f = freshFixture(), c = f.make();
  const results = await Promise.all(Array.from({length:20},()=>c.initialize()));
  assert.equal(results.filter(x=>x.mode === "ready").length,1);
  assert.equal(results.filter(x=>x.mode === "setup_refused").length,19);
  assert.deepEqual(f.calls,["claim-browser"]);
});

for (const saved of [null, {}, {...initialBrowserRecoveryState(config), paused:true},
  {...initialBrowserRecoveryState(config), identity:"d".repeat(64)},
  {...initialBrowserRecoveryState(config), phase:"setup_pending", paused:true},
  initialBrowserRecoveryState(config)]) {
  test(`setup refuses existing state without modifying it: ${JSON.stringify(saved)}`, async () => {
    const f=freshFixture(); f.saved=structuredClone(saved);
    const before=structuredClone(f.saved);
    assert.equal((await f.make().initialize()).mode,"setup_refused");
    assert.deepEqual(f.saved,before); assert.deepEqual(f.calls,[]);
  });
}

for (const stage of ["read", "pending-save", "clear", "cancel", "claim", "final-save"]) {
  test(`interrupted first-run stays blocked: ${stage}`, async () => {
    const f=freshFixture();
    if (stage === "read") f.ports.load=async()=>{throw new Error("private");};
    if (stage === "clear") f.ports.clearCookie=async()=>{throw new Error("private");};
    if (stage === "cancel") f.ports.cancel=async()=>{throw new Error("private");};
    if (stage === "claim") f.ports.native=async()=>{throw new BrowserNativeDisconnected();};
    const save=f.ports.save;
    if (stage.endsWith("save")) f.ports.save=async state=> {
      if ((stage === "pending-save") === (state.phase === "setup_pending")) throw new Error("private");
      return save(state);
    };
    const c=f.make();
    const result=await c.initialize();
    assert(["setup_refused","setup_error"].includes(result.mode));
    assert(!JSON.stringify(result).includes("private"));
    assert.equal((await c.initialize()).mode,"setup_refused");
    assert(!f.calls.includes("authenticate"));
    if (["clear","cancel","claim","final-save"].includes(stage)) {
      assert.equal(f.saved.phase,"setup_pending");
      assert.equal((await f.make().initialize()).mode,"setup_refused");
    }
  });
}

test("worker loss after native claim cannot turn a pending marker into recovery", async () => {
  const f=freshFixture(), native=f.ports.native;
  f.ports.native=async request=>{await native(request);throw new BrowserNativeDisconnected();};
  assert.equal((await f.make().initialize()).mode,"setup_error");
  assert.equal(f.revision,2); assert.equal(f.saved.phase,"setup_pending");
  assert.equal((await f.make().tick()).mode,"setup_error");
  assert.equal((await f.make().initialize()).mode,"setup_refused");
});

test("deleted browser state cannot reuse an already consumed native claim", async () => {
  const f=freshFixture();
  assert.equal((await f.make().initialize()).mode,"ready");
  f.saved=undefined;
  assert.equal((await f.make().initialize()).mode,"setup_error");
  assert.equal(f.saved.phase,"setup_pending");
  assert(!f.calls.includes("authenticate"));
});

for (const stop of ["suspend","beginLogout"]) {
  test(`${stop} wins during a first-run native claim`, async () => {
    const f=freshFixture(), gate=deferred(), native=f.ports.native;
    f.ports.native=async request=>{
      const value=await native(request);
      if(request.action === "claim-browser") await gate.promise;
      return value;
    };
    const c=f.make(), initialized=c.initialize();
    await settle();
    const stopped=c[stop](); await settle();
    assert.equal(f.saved.paused,true);
    gate.resolve(); await initialized; await stopped;
    assert.equal(f.saved.paused,true); assert.equal(f.nativeMode,"paused");
    assert(!f.calls.includes("authenticate"));
    assert.equal((await f.make().tick()).mode,"paused");
  });
}

test("native pause after claiming is not cleared by browser initialization", async () => {
  const f=freshFixture(), native=f.ports.native;
  f.ports.native=async request=>{
    const value=await native(request);
    if(request.action === "claim-browser") f.nativeMode="paused";
    return value;
  };
  const c=f.make(); assert.equal((await c.initialize()).mode,"ready");
  assert.equal((await c.tick()).mode,"paused");
  assert(!f.calls.includes("authenticate"));
});

test("committed first-run state survives worker loss without claiming twice", async () => {
  const f=freshFixture();
  assert.equal((await f.make().initialize()).mode,"ready");
  assert.equal((await f.make().tick()).mode,"active");
  assert.equal(f.calls.filter(x=>x === "claim-browser").length,1);
});

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

test("a native administrator reset cannot override persisted browser sign-out", async () => {
  const f = fixture();
  await f.make().suspend();
  const paused = structuredClone(f.saved);
  // Model a separately authorized native-only reset. No browser consent changed.
  f.nativeMode = "active"; f.revision++;
  const before = f.calls.length, restarted = f.make();
  assert.equal((await restarted.tick()).mode, "paused");
  assert.deepEqual(f.calls.slice(before), ["suspend"]);
  assert.equal(f.nativeMode, "paused");
  assert.deepEqual(f.saved, paused);
  assert.equal(f.cookie, null); assert.equal(f.alarm, null);
  assert.deepEqual(restarted.readiness(), {mode:"paused",sessionReady:false});
  assert.equal((await restarted.initialize()).mode, "setup_refused");
  assert(!f.calls.includes("authenticate"));
});

test("a replacement installation identity cannot inherit a paused browser record", async () => {
  const f = fixture();
  await f.make().suspend();
  const saved = structuredClone(f.saved), before = f.calls.length;
  const replacement = createBrowserRecovery(f.ports, {...config, identity:"d".repeat(64)});
  assert.equal((await replacement.tick()).mode, "setup_error");
  assert.equal((await replacement.initialize()).mode, "setup_refused");
  assert.deepEqual(f.saved, saved);
  assert.deepEqual(f.calls.slice(before), []);
  assert.equal(f.cookie, null);
  assert.equal(replacement.readiness().sessionReady, false);
});

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

test("readiness distinguishes recovery permission from installed session and expires locally",async()=>{
  const f=fixture(),c=f.make();assert.equal(c.readiness().sessionReady,false);
  await c.tick();assert.deepEqual(c.readiness(),{mode:"active",sessionReady:true});
  assert(!JSON.stringify(c.readiness()).includes(token));
  const restarted=f.make();await restarted.tick();
  assert.deepEqual(restarted.readiness(),{mode:"waiting",sessionReady:false});
  f.time+=300001;assert.equal(c.readiness().sessionReady,false);
  await c.suspend();assert.deepEqual(c.readiness(),{mode:"paused",sessionReady:false});
  const retry=fixture();retry.ports.native=async()=>({...retry.status(),retry_after:60});
  const allowed=retry.make();await allowed.tick();assert.equal(allowed.readiness().sessionReady,false);
});

test("missing first-run state is distinguished from corrupt setup without claiming",async()=>{
  const f=freshFixture(),c=f.make();await c.tick();
  assert.deepEqual(c.readiness(),{mode:"setup_required",sessionReady:false});
  assert.deepEqual(f.calls,[]);
  const corrupt=fixture(null).make();await corrupt.tick();
  assert.deepEqual(corrupt.readiness(),{mode:"setup_error",sessionReady:false});
});

test("startup status is exact-document-only, redacted and never triggers native polling",async()=>{
  const f=fixture();f.time=Date.now();const chrome=adapterFixture(f);let listener;
  chrome.runtime.onMessage={addListener:fn=>{listener=fn;}};
  const c=connectChromeRecovery(chrome,config);await c.tick();
  // The mock set result and real cookie get share Chromium's verified shape.
  f.cookie={...f.cookie,hostOnly:true,session:false};
  const sender={id:chrome.runtime.id,url:chrome.runtime.getURL("startup.html"),frameId:0,
    documentId:"active-startup",documentLifecycle:"active",tab:{id:7,incognito:false}};
  const denied=()=>assert.fail("Untrusted startup message responded");
  for(const change of [{id:"other"},{url:sender.url+"?x"},{url:config.origin+"/"},
    {url:chrome.runtime.getURL("control.html")},{frameId:1},{documentId:""},
    {documentLifecycle:"cached"},{tab:undefined},{tab:{id:7,incognito:true}}]) {
    assert.equal(listener({action:"startup-status"},{...sender,...change},denied),false);
  }
  for(const message of [{action:"start"},{action:"suspend"},{action:"initialize"},
    {action:"startup-status",token}]) assert.equal(listener(message,sender,denied),false);
  const actions=[...f.calls];
  const status=()=>new Promise(resolve=>assert.equal(listener({action:"startup-status"},sender,resolve),true));
  assert.deepEqual(await status(),{mode:"active",sessionReady:true});
  f.cookie=null;assert.deepEqual(await status(),{mode:"active",sessionReady:false});
  chrome.cookies.get=async()=>{throw new Error(token);};
  assert.deepEqual(await status(),{mode:"setup_error",sessionReady:false});
  assert.deepEqual(f.calls,actions);
});

test("pause racing readiness cookie read never acknowledges a ready session",async()=>{
  const f=fixture();f.time=Date.now();const chrome=adapterFixture(f);let listener;
  chrome.runtime.onMessage={addListener:fn=>{listener=fn;}};
  const c=connectChromeRecovery(chrome,config);await c.tick();
  const pending=deferred(),cookie={...f.cookie,hostOnly:true,session:false};
  chrome.cookies.get=()=>pending.promise;
  const answer=new Promise(resolve=>listener({action:"startup-status"},
    {id:chrome.runtime.id,url:chrome.runtime.getURL("startup.html"),frameId:0,
      documentId:"active",documentLifecycle:"active",tab:{id:1,incognito:false}},resolve));
  const stop=c.suspend();pending.resolve(cookie);
  assert.equal((await answer).sessionReady,false);await stop;
});

test("first-run messages require the exact active top-level setup document", async () => {
  const f = freshFixture(), chrome = adapterFixture(f);
  let listener;
  chrome.runtime.onMessage = {addListener: callback => { listener = callback; }};
  connectChromeRecovery(chrome, config); await settle();
  const sender = {id: chrome.runtime.id, url: chrome.runtime.getURL("setup.html"),
    frameId: 0, documentId: "current-document", documentLifecycle: "active", tab: {id: 1, incognito: false}};
  const deny = () => assert.fail("Unauthorized setup responded");
  for (const change of [{id: "other"}, {url: config.origin + "/"},
    {url: sender.url + "?extra"}, {url: chrome.runtime.getURL("control.html")},
    {frameId: 1}, {documentId: ""}, {documentLifecycle: "cached"}, {tab: undefined},
    {tab: {id: -1}}, {tab: {id: "1"}}, {tab: {id: 1, incognito: true}}]) {
    assert.equal(listener({action: "initialize"}, {...sender, ...change}, deny), false);
  }
  assert.equal(listener({action: "initialize", origin: config.origin}, sender, deny), false);
  assert.deepEqual(f.calls, []); assert.equal(f.saved, undefined);
  const response = deferred();
  assert.equal(listener({action: "initialize"}, sender, response.resolve), true);
  assert.deepEqual(await response.promise, {mode: "ready"});
  assert.deepEqual(f.calls, ["claim-browser"]);
});

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
