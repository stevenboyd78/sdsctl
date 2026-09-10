import test from "node:test";
import assert from "node:assert/strict";
import {createBrowserRecovery, initialBrowserRecoveryState} from "./browser_device_recovery.mjs";
import {connectLogoutWorker, connectLogoutContent, submitDeviceLogout} from "./browser_device_logout.mjs";

const origin = "https://192.0.2.18:8443";
const config = {origin, identity: "a".repeat(64), nativeHost: "org.sdsctl.browser_device"};
const token = "sdsctl-browser-session-v1." + "b".repeat(64);
const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; });
  return {promise, resolve}; };
function fixture() {
  const f = {state: initialBrowserRecoveryState(config), cookie: token, time: 100000,
    alarm: 0, actions: [], nativeMode: "active"};
  f.status = () => ({version: 1, ok: true, mode: f.nativeMode, revision: 2,
    retry_after: 0, renew_after: 0});
  f.ports = {now: () => f.time, load: async () => structuredClone(f.state),
    save: async value => { f.state = structuredClone(value); },
    clearCookie: async () => { f.cookie = null; },
    setCookie: async value => { f.cookie = value.value; },
    schedule: async when => { f.alarm = when; }, cancel: async () => { f.alarm = 0; },
    native: async ({action}) => { f.actions.push(action);
      if (action === "suspend") f.nativeMode = "paused";
      return action === "authenticate" ? {...f.status(), renew_after: 225,
        session: {token, expires_in: 300}} : f.status(); },
  };
  f.make = () => createBrowserRecovery(f.ports, config);
  return f;
}

test("local/native pause precedes server POST, cookie remains until completion", async () => {
  const f = fixture(), c = f.make();
  const prepared = await c.beginLogout();
  assert.equal(prepared.mode, "logout_pending"); assert.equal(prepared.localPauseSaved, true);
  assert.equal(prepared.nativePaused, true); assert.equal(f.cookie, token);
  assert.equal(f.state.phase, "logout_pending");
  await c.tick(); assert.equal(f.cookie, token); // Alarm cannot clear before POST.
  const done = await c.finishLogout("drained");
  assert.equal(done.serverRevocation, "drained"); assert.equal(done.cookieCleared, true);
  assert.equal(f.cookie, null); assert.equal(f.state.paused, true);
  await f.make().tick(); assert.equal(f.actions.includes("authenticate"), false);
});

test("lost POST result retains pause but never claims server drain", async () => {
  const f = fixture(), c = f.make(); await c.beginLogout();
  assert.equal((await c.finishLogout("unconfirmed")).serverRevocation, "unconfirmed");
  assert.equal(f.cookie, null); assert.equal(f.state.paused, true);
});

test("native pause and absent cookie can precede the final clean browser save", async () => {
  const f=fixture(),c=f.make(),entered=deferred(),release=deferred();
  await c.beginLogout();
  // The successful same-origin server response expires its authentication
  // cookie before the worker processes logout-finish and saves clean pause.
  f.cookie=null;
  f.ports.save=async value=>{
    if(value.phase==="clean"){entered.resolve();await release.promise;}
    f.state=structuredClone(value);
  };
  let completed=false;
  const finished=c.finishLogout("drained").then(value=>{completed=true;return value;});
  try {
    await entered.promise;
    assert.equal(f.nativeMode,"paused");
    assert.equal(f.cookie,null);
    assert.equal(f.state.paused,true);
    assert.equal(f.state.phase,"logout_pending");
    assert.equal(completed,false);
  } finally {release.resolve();}
  const result=await finished;
  assert.equal(result.serverRevocation,"drained");
  assert.equal(result.cookieCleared,true);
  assert.equal(completed,true);
  assert.deepEqual(f.state,{version:1,identity:config.identity,paused:true,phase:"clean",nextAt:0});
});

test("worker restart during pending logout preserves bounded cookie hold", async () => {
  const f = fixture(); await f.make().beginLogout();
  f.time += 30000; await f.make().tick(); assert.equal(f.cookie, token);
  f.time += 30000; const result = await f.make().tick();
  assert.equal(result.serverRevocation, "unconfirmed"); assert.equal(f.cookie, null);
  assert.equal(f.state.paused, true);
});

test("rollback correction bounds pending logout across repeated workers", async () => {
  const f = fixture(); await f.make().beginLogout(); f.time = 1000;
  await f.make().tick(); assert.equal(f.state.nextAt, 61000);
  f.time = 61000; await f.make().tick(); assert.equal(f.cookie, null);
});

test("repeated begins do not extend cookie hold", async () => {
  const f = fixture(), c = f.make(); await c.beginLogout();
  const deadline = f.state.nextAt; f.time += 20000;
  await c.beginLogout(); assert.equal(f.state.nextAt, deadline);
});

test("cold startup racing begin preserves cookie and never authenticates", async () => {
  const f = fixture(), c = f.make();
  const [run, prepared] = await Promise.all([c.tick(), c.beginLogout()]);
  assert.equal(prepared.mode, "logout_pending"); assert.equal(f.cookie, token);
  assert.equal(f.actions.includes("authenticate"), false);
});

test("begin during pending set preserves resulting cookie for server authentication", async () => {
  const f = fixture(), entered = deferred(), release = deferred();
  f.ports.setCookie = async value => { entered.resolve(); await release.promise; f.cookie = value.value; };
  const c = f.make(), run = c.tick(); await entered.promise;
  const begin = c.beginLogout(); await settle(); assert.equal(f.state.paused, true);
  release.resolve(); await run;
  assert.equal((await begin).mode, "logout_pending"); assert.equal(f.cookie, token);
  await c.finishLogout("pending"); assert.equal(f.cookie, null);
});

test("persistence failure never permits server submission", async () => {
  const f = fixture(); f.ports.save = async () => { throw new Error(token); };
  assert.deepEqual(await f.make().beginLogout(), {mode: "setup_error"});
  assert.equal(f.actions.length, 0);
});

test("already paused state cannot request another authenticated logout", async () => {
  const f = fixture(); f.state.paused = true;
  assert.equal((await f.make().beginLogout()).mode, "paused");
  assert.equal(f.cookie, null);
});

const body = drained => JSON.stringify({version: 1, device_logout: true, paused: true, drained});
function response(status = 200, text = body(true), overrides = {}) {
  const raw = new Response(text, {status, headers: {"content-type": "application/json"}});
  return {status, url: origin + "/auth/logout", redirected: false, headers: raw.headers,
    body: raw.body, ...overrides};
}
for (const [status, drained, expected] of [[200, true, "drained"], [202, false, "pending"]]) {
  test(`same-origin POST classifies ${status} as ${expected}`, async () => {
    const result = await submitDeviceLogout(async (url, options) => {
      assert.equal(url, origin + "/auth/logout"); assert.equal(options.method, "POST");
      assert.equal(options.credentials, "same-origin"); assert.equal(options.mode, "same-origin");
      assert.equal(options.redirect, "error");
      assert.deepEqual(options.headers, {Accept: "application/json"});
      return response(status, body(drained));
    }, origin);
    assert.equal(result, expected);
  });
}
for (const make of [() => response(401), () => response(503), () => response(200, body(false)),
  () => response(202, body(true)), () => response(200, "x".repeat(1025)),
  () => response(200, '{"version":1,"device_logout":true,"paused":true,"drained":false,"drained":true}'),
  () => response(200, body(true), {redirected: true}),
  () => response(200, body(true), {url: "https://evil.example/auth/logout"}),
  () => response(200, body(true), {headers: new Headers({"content-type": "text/html"})}),
  () => { throw new Error(token); }]) {
  test(`unconfirmed/malformed server response is never drain acknowledgement: ${make}`, async () => {
    assert.equal(await submitDeviceLogout(async () => make(), origin), "unconfirmed");
  });
}

function worker(controller) {
  let listener;
  const chrome = {runtime: {id: "a".repeat(32), onMessage: {addListener(fn) { listener = fn; }}}};
  const sender = {id: chrome.runtime.id, url: origin + "/", origin, frameId: 0,
    documentId: "fictional-document", documentLifecycle: "active", tab: {id: 7, incognito: false}};
  const lifecycle=connectLogoutWorker(chrome, controller, origin);
  const send = (message, from = sender) => new Promise(resolve => {
    let called = false;
    const accepted = listener(message, from, value => { called = true; resolve(value); });
    if (!accepted && !called) resolve(null);
  });
  return {send, sender, lifecycle};
}

test("only verified active resume retires the old logout ticket",async()=>{
  let ready=false,begins=0,finishes=0;
  const c={readiness:()=>({mode:ready?"active":"paused",sessionReady:ready}),
    beginLogout:async()=>{ready=false;begins++;return {mode:"logout_pending",localPauseSaved:true};},
    finishLogout:async()=>{finishes++;return {mode:"paused"};}};
  const w=worker(c), first=await w.send({action:"logout-begin"});
  const oldFinish={action:"logout-finish",ticket:first.ticket,outcome:"unconfirmed"};
  await w.send(oldFinish);
  assert.equal(w.lifecycle.retireAfterResume(),false);
  assert.deepEqual(await w.send({action:"logout-begin"}),{mode:"busy"});
  ready=true;assert.equal(w.lifecycle.retireAfterResume(),true);
  assert.equal(await w.send({...oldFinish,outcome:"drained"}),null);
  const second=await w.send({action:"logout-begin"},{...w.sender,documentId:"new-document"});
  assert(second.submit);assert.notEqual(second.ticket,first.ticket);
  assert.equal(await w.send(oldFinish),null);
  await w.send({action:"logout-finish",ticket:second.ticket,outcome:"drained"},
    {...w.sender,documentId:"new-document"});
  assert.equal(begins,2);assert.equal(finishes,2);
});
for(const state of [{mode:"active",sessionReady:false},{mode:"paused",sessionReady:true},
  {mode:"waiting",sessionReady:true},{mode:"setup_error",sessionReady:false}]) {
  test("incomplete or superseded readiness cannot retire logout: "+JSON.stringify(state),()=>{
    assert.equal(worker({readiness:()=>state}).lifecycle.retireAfterResume(),false);
  });
}

test("document-bound single-use completion cannot be replayed to upgrade outcome", async () => {
  const f = fixture(), c = f.make(), w = worker(c);
  const prepared = await w.send({action: "logout-begin"});
  assert.equal(prepared.submit, true);
  assert.deepEqual(await w.send({action: "logout-begin"}), {mode: "busy"});
  const finish = {action: "logout-finish", ticket: prepared.ticket, outcome: "unconfirmed"};
  assert.equal(await w.send(finish, {...w.sender, documentId: "another"}), null);
  const first = await w.send(finish);
  const replay = await w.send({...finish, outcome: "drained"});
  assert.equal(first.serverRevocation, "unconfirmed"); assert.deepEqual(replay, first);
});

test("device-only entry keeps the same exact-document sign-out protocol",async()=>{
  const f=fixture(),c=f.make(),w=worker(c);
  const sender={...w.sender,url:origin+"/device-display"};
  const prepared=await w.send({action:"logout-begin"},sender);
  assert.equal(prepared.submit,true);
  const done=await w.send({action:"logout-finish",ticket:prepared.ticket,outcome:"unconfirmed"},sender);
  assert.equal(done.mode,"paused");assert.equal(f.cookie,null);
});

for (const change of [{frameId: 1}, {origin: "https://evil.example"}, {url: origin + "/other"},
  {id: "other"}, {documentLifecycle: "prerender"}, {documentId: undefined},
  {tab: {id: 7, incognito: true}}]) {
  test(`unapproved sender rejected ${JSON.stringify(change)}`, async () => {
    const w = worker({beginLogout() { assert.fail("Unauthorized begin"); }});
    assert.equal(await w.send({action: "logout-begin"}, {...w.sender, ...change}), null);
  });
}

test("content handler stops repeated submits and waits for pause before fetch", async () => {
  let handler, fetches = 0; const prepared = deferred(), notices = [];
  const win = {location: {href: origin + "/"}}; win.top = win;
  const document = {addEventListener(_, fn, capture) { assert.equal(capture, true); handler = fn; },
    createElement() { return {setAttribute() {}, textContent: ""}; }};
  const runtime = {sendMessage: async message => {
    if (message.action === "logout-begin") return prepared.promise;
    assert.equal(message.outcome, "drained");
    return {mode: "paused", localPauseSaved: true, cookieCleared: true, serverRevocation: "drained"};
  }};
  connectLogoutContent({document, window: win, runtime, fetcher: async () => {
    fetches++; return response();
  }}, origin);
  const event = {isTrusted: true, preventDefault() {}, stopImmediatePropagation() {},
    target: {tagName: "FORM", method: "post", action: origin + "/auth/logout", append(n) { notices.push(n); }}};
  handler(event); handler(event); await settle(); assert.equal(fetches, 0);
  prepared.resolve({mode: "logout_pending", submit: true, ticket: "a".repeat(36)});
  await settle(); await settle();
  assert.equal(fetches, 1); assert.equal(notices.length, 1);
  assert.match(notices[0].textContent, /Server shutdown confirmed/);
});
