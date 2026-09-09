import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";
import {connectBrowserEntry,connectBrowserStartupPage} from "../../src/sds200/browser_assets/browser_device_startup.mjs";

const origin = "https://192.0.2.18:8443", extension = `chrome-extension://${"a".repeat(32)}/`;
const settle = () => new Promise(resolve => setImmediate(resolve));
const startEntry = chrome => connectBrowserEntry(chrome,()=>{});
function entry(tab, contexts=[]) {
  const f={updates:[],queries:[]};
  f.chrome={runtime:{getURL:name=>extension+name,getContexts:async filter=>{
    assert.equal(filter.frameIds[0],0);return contexts;
  },onStartup:{addListener:fn=>{f.startup=fn;}},onInstalled:{addListener:fn=>{f.installed=fn;}}},
    tabs:{onUpdated:{addListener:fn=>{f.listener=fn;}},
    query:async filter=>{f.queries.push(filter);return [tab];},get:async()=>tab,
    reload:async id=>f.updates.push({id})}};
  return f;
}
for(const name of ["startup.html","setup.html"]) {
  test(`retries only the selected ${name} after unpacked startup race`,async()=>{
    const tab={id:7,status:"complete",incognito:false,url:extension+name};
    const f=entry(tab);startEntry(f.chrome);await settle();
    assert.deepEqual(f.updates,[{id:7}]);
    assert.deepEqual(f.queries,[{url:[extension+"startup.html",extension+"setup.html"]}]);
    f.listener(7,{status:"complete"},tab);await settle();assert.equal(f.updates.length,1);
  });
}
for(const change of [{id:-1},{status:"loading"},{incognito:true},{url:origin+"/device-display"},
  {url:extension+"setup.html?extra"},{url:"chrome-error://chromewebdata/"},
  {pendingUrl:origin+"/"}]) {
  test(`entry retry ignores unrelated or moving tab ${JSON.stringify(change)}`,async()=>{
    const f=entry({id:1,status:"complete",incognito:false,url:extension+"startup.html",...change});
    startEntry(f.chrome);await settle();assert.equal(f.updates.length,0);
  });
}
test("entry retry never reloads a valid document or a tab changed during inspection",async()=>{
  const tab={id:1,status:"complete",incognito:false,url:extension+"startup.html"};
  const loaded=entry(tab,[{documentId:"valid"}]);startEntry(loaded.chrome);await settle();
  assert.equal(loaded.updates.length,0);
  const moved=entry(tab);moved.chrome.tabs.get=async()=>({...tab,url:origin+"/"});
  startEntry(moved.chrome);await settle();assert.equal(moved.updates.length,0);
  const closed=entry(tab);closed.chrome.tabs.get=async()=>{throw new Error("private");};
  startEntry(closed.chrome);await settle();assert.equal(closed.updates.length,0);
});
test("browser lifecycle scans find a launcher page absent during initial worker load",async()=>{
  const tab={id:9,status:"complete",incognito:false,url:extension+"startup.html"};
  const f=entry(tab);let visible=false;
  f.chrome.tabs.query=async()=>visible?[tab]:[];
  startEntry(f.chrome);await settle();assert.equal(f.updates.length,0);
  visible=true;f.startup();f.installed();await settle();assert.deepEqual(f.updates,[{id:9}]);
});
test("entry retries are bounded even when the document never becomes available",async()=>{
  const previousNow=Date.now;let time=10000;Date.now=()=>time;
  try {
    const tab={id:10,status:"complete",incognito:false,url:extension+"startup.html"};
    const f=entry(tab),scheduled=[];connectBrowserEntry(f.chrome,(fn,ms)=>scheduled.push({fn,ms}));
    await settle();assert.deepEqual(scheduled.map(x=>x.ms),[1000,5000]);
    for(let i=0;i<10;i++){time+=1001;f.listener(10,{status:"complete"},tab);await settle();}
    assert.equal(f.updates.length,3);
    for(const job of scheduled){job.fn();await settle();}
    assert.equal(f.updates.length,3);
  } finally {Date.now=previousNow;}
});
function page(response) {
  const f = {notice:{textContent:""}, resume:{hidden:false}, scheduled:[], messages:[], navigations:[]};
  f.document = {getElementById: id => {assert(["notice","resume-link"].includes(id));return id==="notice"?f.notice:f.resume;}};
  f.window = {location:{href:extension+"startup.html", replace:url=>f.navigations.push(url)}};
  f.window.top=f.window;
  f.runtime = {getURL:name=>extension+name, sendMessage:async message=>{
    f.messages.push(message); if(response instanceof Error) throw response; return response;
  }};
  f.schedule = (fn,ms)=>{assert.equal(ms,5000);f.scheduled.push(fn);};
  return f;
}

for (const mode of ["starting","ready","active","waiting","paused","stopping","logout_pending",
  "setup_required","setup_error","tls_error","credential_rejected","protocol_error","administrator_required"]) {
  test(`startup shows ${mode} without starting or initializing recovery`, async()=>{
    const f=page({mode,sessionReady:false}); connectBrowserStartupPage(f,origin); await settle();
    assert(f.notice.textContent.length>10); assert.deepEqual(f.navigations,[]);
    assert.deepEqual(f.messages,[{action:"startup-status"}]); assert.equal(f.scheduled.length,1);
    await f.scheduled.shift()(); assert.equal(f.scheduled.length,1);
  });
}
test("paused-only startup explains administrator boundary and keeps resume hidden",async()=>{
  const f=page({mode:"administrator_required",sessionReady:false});
  connectBrowserStartupPage(f,origin);assert(f.resume.hidden);await settle();
  assert(f.resume.hidden);assert.match(f.notice.textContent,/separate administrator continuation/);
  assert.deepEqual(f.navigations,[]);
});
test("ordinary pause exposes resume only after a valid status; failures hide it again",async()=>{
  const f=page({mode:"paused",sessionReady:false});connectBrowserStartupPage(f,origin);
  assert(f.resume.hidden);await settle();assert.equal(f.resume.hidden,false);
  f.runtime.sendMessage=async()=>{throw Error('private');};
  await f.scheduled.shift()();assert(f.resume.hidden);
});
for (const mode of ["active","waiting"]) {
  test(`only installed ${mode} session opens fixed device entry`,async()=>{
    const f=page({mode,sessionReady:true});connectBrowserStartupPage(f,origin);await settle();
    assert.deepEqual(f.navigations,[origin+"/device-display"]);assert.equal(f.scheduled.length,0);
  });
}
for (const result of [null,{}, {mode:"paused",sessionReady:true}, {mode:"unknown",sessionReady:true},
  {mode:"active",sessionReady:"true"},{mode:"active",sessionReady:true,token:"private-do-not-echo"},
  new Error("private-do-not-echo")]) {
  test(`malformed or paused readiness never navigates ${JSON.stringify(result)}`,async()=>{
    const f=page(result);connectBrowserStartupPage(f,origin);await settle();
    assert.equal(f.navigations.length,0);assert(!f.notice.textContent.includes("private-do-not-echo"));
  });
}
for(const change of [f=>{f.window.top={};},f=>{f.window.location.href+="?other";}]) {
  test("startup refuses an unintended document",()=>{
    const f=page({});change(f);assert.throws(()=>connectBrowserStartupPage(f,origin));
    assert.equal(f.messages.length,0);
  });
}
for(const invalid of ["http://192.0.2.1","https://example.com/path","https://user@example.com"]) {
  test(`startup rejects noncanonical origin ${invalid}`,()=>{
    assert.throws(()=>connectBrowserStartupPage(page({}),invalid));
  });
}

// Execute the actual classic dashboard functions without loading its unrelated
// scanner/graph rendering code. The real browser harness checks full-page wiring.
const dashboard = await readFile(new URL("../../src/sds200/web_assets/dashboard.js",import.meta.url),"utf8");
const functions = dashboard.slice(dashboard.indexOf("function requireNativeLogin()"),
  dashboard.indexOf("async function initializeNativeSession()"));
function dashboardFixture(managed, reply) {
  const f={timers:[],navigations:[],requests:[]}, banner={append(){},hidden:true};
  const scope={managedDeviceEntry:managed,nativeAccessMode:"display",displayOnly:true,
    authenticationRequired:false,nativeSessionTimer:null,currentDaemonHello:{},
    document:{documentElement:{dataset:{}},getElementById:()=>null,createElement:()=>({})},
    window:{setTimeout:(fn,ms)=>{f.timers.push({fn,ms});return 1;},clearTimeout(){},
      location:{replace:url=>f.navigations.push(url)}},
    webUrl:path=>origin+"/"+path, element:id=>id==="saved-recording-player"?{pause(){}}:banner,
    stopEventStream(){},stopWaterfallStream(){},stopAudioPlayback(){},setScannerControls(){},
    setOverallStatus(){},AbortSignal,
    fetch:async(url,options)=>{f.requests.push({url,options});
      if(reply instanceof Error) throw reply;
      return {status:reply.status??200,ok:(reply.status??200)===200,json:async()=>reply};},
  };
  f.scope=scope;vm.createContext(scope);vm.runInContext(functions,scope);return f;
}
test("managed renewal checks current session and does not replay login or stale expiry",async()=>{
  const f=dashboardFixture(true,{device_enrolled:true,display_only:true,remaining_seconds:150});
  await f.scope.refreshManagedNativeSession();
  assert.equal(f.requests[0].url,origin+"/auth/session");assert.equal(f.timers[0].ms,112500);
  assert.equal(f.timers[0].fn,f.scope.refreshManagedNativeSession);
  assert.equal(f.navigations.length,0);
});
for(const reply of [{status:401},{device_enrolled:false,display_only:true,remaining_seconds:150},
  {device_enrolled:true,display_only:false,remaining_seconds:150},
  {device_enrolled:true,display_only:true,remaining_seconds:0}]) {
  test("managed invalid session returns to guarded device entry, never password login",async()=>{
    const f=dashboardFixture(true,reply);await f.scope.refreshManagedNativeSession();
    assert.deepEqual(f.navigations,[origin+"/device-display"]);assert.equal(f.timers.length,0);
  });
}
for(const reply of [{status:503},new Error("private-do-not-echo")]) {
  test("managed transient read retries finitely without authentication",async()=>{
    const f=dashboardFixture(true,reply);await f.scope.refreshManagedNativeSession();
    assert.equal(f.timers[0].ms,5000);assert.equal(f.navigations.length,0);
  });
}
test("manual display session retains its login flow",async()=>{
  const f=dashboardFixture(false,{});await f.scope.refreshManagedNativeSession();
  assert.equal(f.requests.length,0);f.scope.requireNativeLogin();
  assert.equal(f.navigations.length,0);assert.equal(f.scope.authenticationRequired,true);
});
