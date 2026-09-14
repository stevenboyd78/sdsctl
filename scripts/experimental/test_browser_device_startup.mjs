import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";
import {connectBrowserEntry,connectContinuationEntry,connectBrowserStartupPage} from "../../src/sds200/browser_assets/browser_device_startup.mjs";

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

test("continuation entry only retries its fixed startup document",async()=>{
  const tab={id:7,status:"complete",incognito:false,url:extension+"startup.html"};
  const f=entry(tab);connectContinuationEntry(f.chrome,()=>{});await settle();
  assert.deepEqual(f.queries,[{url:[extension+"startup.html"]}]);
  assert.deepEqual(f.updates,[{id:7}]);
});
for(const change of [{id:-1},{status:"loading"},{incognito:true},
  {url:extension+"setup.html"},{url:extension+"resume.html"},
  {url:extension+"startup.html?other"},{url:origin+"/device-display"},
  {url:"chrome-error://chromewebdata/"},{pendingUrl:origin+"/"}]) {
  test(`continuation entry refuses another target ${JSON.stringify(change)}`,async()=>{
    const tab={id:7,status:"complete",incognito:false,url:extension+"startup.html",...change};
    const f=entry(tab);connectContinuationEntry(f.chrome,()=>{});await settle();
    f.listener(7,{status:"complete"},tab);f.startup();f.installed();await settle();
    assert.deepEqual(f.updates,[]);
  });
}
for(const fault of ['present','arrived','moved','closed','query-failed']) {
  test(`continuation entry does not reload when ${fault}`,async()=>{
    const tab={id:7,status:"complete",incognito:false,url:extension+"startup.html"};
    const f=entry(tab,fault==='present'?[{documentId:'loaded'}]:[]);
    if(fault==='arrived') {let reads=0;f.chrome.runtime.getContexts=async()=>
      ++reads===1?[]:[{documentId:'loaded'}];}
    if(fault==='moved')f.chrome.tabs.get=async()=>({...tab,url:extension+'setup.html'});
    if(fault==='closed')f.chrome.tabs.get=async()=>{throw Error('closed');};
    if(fault==='query-failed')f.chrome.tabs.query=async()=>{throw Error('unavailable');};
    connectContinuationEntry(f.chrome,()=>{});await settle();
    assert.deepEqual(f.updates,[]);
  });
}
test("continuation retries stay bounded across all lifecycle events",async()=>{
  const previousNow=Date.now;let time=10000;Date.now=()=>time;
  try {
    const tab={id:7,status:"complete",incognito:false,url:extension+"startup.html"};
    const f=entry(tab),jobs=[];connectContinuationEntry(f.chrome,(fn,ms)=>jobs.push({fn,ms}));
    await settle();assert.deepEqual(jobs.map(j=>j.ms),[1000,5000]);
    for(let i=0;i<10;i++) {
      time+=1001;f.listener(7,{status:"complete"},tab);f.startup();f.installed();
      for(const job of jobs)job.fn();
      await settle();
    }
    assert.equal(f.updates.length,3);
  } finally {Date.now=previousNow;}
});
function page(response) {
  const f = {notice:{textContent:""}, resume:{hidden:false}, scheduled:[], messages:[], navigations:[]};
  f.document = {getElementById: id => {assert(["notice","resume-link"].includes(id));return id==="notice"?f.notice:f.resume;}};
  f.window = {location:{href:extension+"startup.html", replace:url=>f.navigations.push(url)},
    addEventListener:(name,fn)=>{assert.equal(name,'pagehide');f.pagehide=fn;}};
  f.window.top=f.window;
  f.runtime = {getURL:name=>extension+name, sendMessage:async message=>{
    f.messages.push(message); if(response instanceof Error) throw response; return response;
  }};
  f.schedule = (fn,ms)=>{assert.equal(ms,5000);f.scheduled.push(fn);};
  return f;
}

for (const mode of ["starting","ready","active","waiting","paused","stopping","logout_pending",
  "setup_required","setup_error","tls_error","credential_rejected","protocol_error","administrator_required",
  "continuation_verification_required"]) {
  test(`startup shows ${mode} without starting or initializing recovery`, async()=>{
    const f=page({mode,sessionReady:false}); connectBrowserStartupPage(f,origin); await settle();
    assert(f.notice.textContent.length>10); assert.deepEqual(f.navigations,[]);
    assert.deepEqual(f.messages,[{action:"startup-status"}]); assert.equal(f.scheduled.length,1);
    await f.scheduled.shift()(); assert.equal(f.scheduled.length,1);
  });
}

for(const changed of ['pagehide','location'])test('startup '+changed+' fences a pending ready reply and polling',async()=>{
  const f=page({mode:'active',sessionReady:true});let resolve;
  f.runtime.sendMessage=()=>new Promise(r=>{resolve=r;});
  connectBrowserStartupPage(f,origin);
  if(changed==='pagehide')f.pagehide();else f.window.location.href=extension+'resume.html';
  resolve({mode:'active',sessionReady:true});await settle();
  assert.deepEqual(f.navigations,[]);assert.deepEqual(f.scheduled,[]);assert(f.resume.hidden);
});
test('an old scheduled startup poll cannot start another check after pagehide',async()=>{
  const f=page({mode:'paused',sessionReady:false});connectBrowserStartupPage(f,origin);await settle();
  f.pagehide();await f.scheduled.shift()();
  assert.deepEqual(f.messages,[{action:'startup-status'}]);assert.deepEqual(f.scheduled,[]);
});
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
  dashboard.indexOf("function syncDisplayNavigation()"));
function dashboardFixture(managed, reply) {
  const node=tag=>({tagName:tag.toUpperCase(),children:[],hidden:true,
    append(...children){this.children.push(...children);},prepend(...children){this.children.unshift(...children);},
    setAttribute(){}});
  const f={timers:[],navigations:[],requests:[],listeners:[],cleared:[],stops:[],closed:0}, banner=node('div');
  const scope={managedDeviceEntry:managed,nativeAccessMode:"display",displayOnly:true,
    authenticationRequired:false,nativeSessionTimer:null,currentDaemonHello:{},
    document:{documentElement:{dataset:{}},getElementById:id=>id==='native-menu'?{close(){f.closed++;}}:null,
      createElement:node},
    window:{setTimeout:(fn,ms)=>{f.timers.push({fn,ms});return 1;},clearTimeout:id=>f.cleared.push(id),
      addEventListener:(type,listener,capture)=>f.listeners.push({type,listener,capture}),
      location:{replace:url=>f.navigations.push(url)}},
    webUrl:path=>origin+"/"+path, element:id=>id==="saved-recording-player"?{pause(){f.stops.push('recording');}}:banner,
    stopEventStream(){f.stops.push('events');},stopWaterfallStream(){f.stops.push('waterfall');},
    stopAudioPlayback(){f.stops.push('audio');},setScannerControls(){f.stops.push('controls');},
    initializeDisplayNavigation:form=>{f.form=form;},setOverallStatus(){},AbortSignal,
    fetch:async(url,options)=>{f.requests.push({url,options});
      if(reply instanceof Error) throw reply;
      if(typeof reply==='function')return reply(url,options);
      return {status:reply.status??200,ok:(reply.status??200)===200,json:async()=>reply};},
  };
  f.submit=(event={isTrusted:true,target:f.form})=>{
    for(const {type,listener,capture} of f.listeners)if(type==='submit') {
      assert.equal(capture,true);listener(event);
    }
  };
  f.transportIntent=(target=scope.window)=>{
    for(const {type,listener} of f.listeners)if(type==='sdsctl-device-signout-intent')listener({target});
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

const enrolledSession={device_enrolled:true,display_only:true,remaining_seconds:150};
for(const status of [401,500,202]) {
  test(`owned logout document quiesces UI before delayed acknowledgement ${status}`,async()=>{
    const f=dashboardFixture(true,enrolledSession);await f.scope.initializeNativeSession();
    let release;
    f.scope.fetch=url=>{f.requests.push({url});return new Promise(resolve=>{release=resolve;});};
    const background=f.scope.dashboardFetch(origin+'/api/status');
    f.transportIntent();
    // A revoked background read must not navigate the selected logout document
    // away while its separate, real same-origin POST awaits a drain response.
    release({status,ok:status<400});
    await assert.rejects(background,{message:'Login required.'});
    f.scope.requireNativeLogin();
    assert.deepEqual(f.navigations,[]);
    assert.equal(f.scope.authenticationRequired,true);
    assert.equal(f.scope.document.documentElement.dataset.sessionState,'signing-out');
    assert.equal(f.scope.nativeSessionTimer,null);assert.equal(f.closed,0);
    assert.deepEqual(f.stops,['events','waterfall','audio','recording','controls']);
    assert.equal(f.requests.length,2); // Two GETs only; no POST, native call or cookie operation.
    f.transportIntent();assert.equal(f.stops.length,5);
  });
}
test('transport UI hint cannot quiesce manual login or unrelated event targets',async()=>{
  for(const managed of [false,true]) {
    const f=dashboardFixture(managed,enrolledSession);await f.scope.initializeNativeSession();
    if(managed)f.transportIntent({});else f.transportIntent();
    assert.equal(f.scope.authenticationRequired,false);assert.equal(f.stops.length,0);
  }
});
for(const phase of ['complete','pending','unconfirmed']) {
  test(`managed sign-out preserves its ${phase} result document after server revocation`,async()=>{
    const f=dashboardFixture(true,enrolledSession);await f.scope.initializeNativeSession();
    f.submit(); // Window capture must precede the extension's document capture.
    f.scope.requireNativeLogin(); // The same call made when a concurrent read returns 401.
    assert.deepEqual(f.navigations,[]);
    assert.equal(f.closed,0); // Keep the menu/result visible; do not close or reload it.
    assert.equal(f.scope.authenticationRequired,true);
    assert.equal(f.scope.document.documentElement.dataset.sessionState,'signing-out');
    assert.deepEqual(f.stops,['events','waterfall','audio','recording','controls']);
    assert.deepEqual(f.cleared,[1]);
    assert.equal(f.scope.nativeSessionTimer,null);
    assert.equal(f.requests.length,1); // Session read only; no dashboard-owned POST.
    assert.equal(f.requests[0].url,origin+'/auth/session');
    const before=f.timers.length;await f.scope.refreshManagedNativeSession();
    assert.equal(f.timers.length,before);assert.equal(f.requests.length,1);
    f.submit();assert.equal(f.stops.length,5); // Repeated UI intent is not another operation.
  });
}
for(const change of [e=>{e.isTrusted=false;},e=>{e.target={...e.target};},
  e=>{e.target.action=origin+'/other';},e=>{e.target.method='get';}]) {
  test('synthetic, unrelated or changed forms cannot freeze managed recovery',async()=>{
    const f=dashboardFixture(true,enrolledSession);await f.scope.initializeNativeSession();
    const event={isTrusted:true,target:f.form};change(event);f.submit(event);
    assert.equal(f.scope.authenticationRequired,false);assert.equal(f.stops.length,0);
    f.scope.requireNativeLogin();assert.deepEqual(f.navigations,[origin+'/device-display']);
  });
}
test('manual display sign-out keeps normal form submission and expiry behavior',async()=>{
  const f=dashboardFixture(false,enrolledSession);await f.scope.initializeNativeSession();
  f.submit();assert.equal(f.scope.authenticationRequired,false);
  f.scope.requireNativeLogin();assert.equal(f.closed,1);assert.deepEqual(f.navigations,[]);
});
for(const call of ['initializeNativeSession','refreshManagedNativeSession']) {
  test(`late ${call} response cannot re-arm activity after trusted sign-out`,async()=>{
    let release;const reply=new Promise(resolve=>{release=resolve;});
    const f=dashboardFixture(true,()=>reply);
    const initialization=f.scope.initializeNativeSession();
    let running=initialization;
    if(call!=='initializeNativeSession') {
      release({status:200,ok:true,json:async()=>enrolledSession});await initialization;
      f.scope.fetch=()=>new Promise(resolve=>{release=resolve;});
      running=f.scope.refreshManagedNativeSession();
    }
    f.submit();const timers=f.timers.length;
    release({status:200,ok:true,json:async()=>enrolledSession});await running;
    assert.equal(f.timers.length,timers);assert.equal(f.scope.nativeSessionTimer,null);
    assert.deepEqual(f.navigations,[]);
  });
}
test('late session JSON cannot install a new expiry timer after sign-out',async()=>{
  let release;const body=new Promise(resolve=>{release=resolve;});
  const f=dashboardFixture(true,()=>({status:200,ok:true,json:()=>body}));
  const running=f.scope.initializeNativeSession();await settle();f.submit();
  release(enrolledSession);await running;
  assert.equal(f.timers.length,0);assert.equal(f.scope.nativeSessionTimer,null);
});
for(const reject of [false,true]) {
  test(`late status ${reject?'failure':'payload'} cannot replace sign-out presentation`,async()=>{
    let resolve,rejection;const payload=new Promise((yes,no)=>{resolve=yes;rejection=no;});
    const f=dashboardFixture(true,enrolledSession);await f.scope.initializeNativeSession();
    let rendered=0,changed=0;
    Object.assign(f.scope,{refreshInProgress:false,fetchStatusPayload:()=>payload,
      renderStatus:()=>{rendered++;},setOverallStatus:()=>{changed++;},
      setText:()=>{changed++;},scannerControlMutationInProgress:false});
    vm.runInContext(dashboard.slice(dashboard.indexOf('async function refreshStatus()'),
      dashboard.indexOf('function clearEventStreamRestartTimer()')),f.scope);
    const running=f.scope.refreshStatus();f.submit();const before=changed;
    if(reject)rejection(new Error('fixture unavailable'));else resolve({});
    await running;
    assert.equal(rendered,0);assert.equal(changed,before);
    assert.equal(f.scope.refreshInProgress,false);
    assert.equal(f.scope.document.documentElement.dataset.sessionState,'signing-out');
  });
}
