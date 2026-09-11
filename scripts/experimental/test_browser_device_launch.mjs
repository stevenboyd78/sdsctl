import test from "node:test";
import assert from "node:assert/strict";
import {connectRecoveryLaunchWorker,connectRecoveryLaunchPage,connectRecoveryLaunchNavigation} from "../../src/sds200/browser_assets/browser_device_launch.mjs";
const c={identity:"b".repeat(64),intent:"c".repeat(64),binding:"d".repeat(64),nativeHost:"org.sdsctl.browser_device"};
const id="a".repeat(32),url="chrome-extension://"+id+"/recovery.html";
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function fixture() {
  const f={calls:[],listeners:[],fail:null};
  f.chrome={runtime:{id,getURL:name=>"chrome-extension://"+id+"/"+name,
    onMessage:{addListener:fn=>f.listeners.push(fn)},
    sendNativeMessage:async(host,body)=>{
      f.calls.push({host,body});
      if(f.fail==="throw")throw Error("private diagnostic");
      if(f.fail)return f.fail;
      return {version:1,ok:true,binding:c.binding};
    }}};
  f.sender={id,url,frameId:0,documentLifecycle:"active",documentId:"doc-1",tab:{id:1,incognito:false}};
  f.send=async(message={action:"recovery-launch",binding:c.binding},sender=f.sender)=>{
    const replies=[];
    const waiting=f.listeners[0](message,sender,r=>replies.push(r));
    await flush();return {waiting,replies};
  };
  f.start=()=>{f.listeners=[];connectRecoveryLaunchWorker(f.chrome,c);};
  f.elements={review:{disabled:false},notice:{textContent:""}};
  f.document={getElementById:key=>f.elements[key]};
  f.window={location:{href:url}};f.window.top=f.window;
  f.runtime={getURL:f.chrome.runtime.getURL,sendMessage:async message=>(await f.send(message)).replies[0]};
  f.page=()=>connectRecoveryLaunchPage({document:f.document,window:f.window,runtime:f.runtime},c);
  return f;
}
test("page and matching worker attest generation; no consent/storage/cookie API exists",async()=>{
  const f=fixture();f.start();assert.equal(f.calls.length,0);
  f.page();assert.equal(f.elements.review.disabled,true);
  await flush();await flush();
  assert.equal(f.elements.review.disabled,false);
  assert.match(f.elements.notice.textContent,/No login was attempted/);
  assert.deepEqual(f.calls,[{host:c.nativeHost,body:{version:1,action:"recovery-launch-ready",
    identity:c.identity,intent:c.intent,binding:c.binding}}]);
  assert.deepEqual((await f.send()).replies,[{mode:"recovery_ready",binding:c.binding}]);
  assert.equal(f.calls.length,1);
});
for(const change of [{id:"e".repeat(32)},{url:"https://example.test/"},{url:url+"?x=1"},
  {frameId:1},{documentLifecycle:"prerender"},{documentId:""},{documentId:"bad:id"},
  {tab:{id:-1,incognito:false}},{tab:{id:1,incognito:true}},{tab:undefined}]) {
  test("readiness denies untrusted sender "+JSON.stringify(change),async()=>{
    const f=fixture();f.start();
    assert.deepEqual(await f.send(undefined,{...f.sender,...change}),{waiting:false,replies:[]});
    assert.equal(f.calls.length,0);
  });
}
for(const message of [{action:"recovery-launch",binding:"e".repeat(64)},
  {action:"recovery-launch",binding:c.binding,pid:1},{action:"recovery-launch"},
  {action:"authenticate"},{action:"retirement-confirm"},{action:"recovery-launch",binding:true}]) {
  test("readiness refuses wrong generation or caller selection "+JSON.stringify(message),async()=>{
    const f=fixture();f.start();
    assert.deepEqual(await f.send(message),{waiting:false,replies:[]});
    assert.equal(f.calls.length,0);
  });
}
for(const failure of ["throw",{}, {version:1,ok:true,binding:"e".repeat(64)},
  {version:true,ok:true,binding:c.binding},{version:1,ok:1,binding:c.binding},
  {version:1,ok:true,binding:c.binding,consent:true}]) {
  test("failed readiness keeps controls disabled and does not repeat native call "+JSON.stringify(failure),async()=>{
    const f=fixture();f.fail=failure;f.start();f.page();await flush();await flush();
    assert.equal(f.elements.review.disabled,true);
    assert.match(f.elements.notice.textContent,/could not be verified/);
    assert(!f.elements.notice.textContent.includes("private diagnostic"));
    await f.send();assert.equal(f.calls.length,1);
  });
}
test("old worker without handshake cannot enable review",async()=>{
  const f=fixture();f.runtime.sendMessage=async()=>undefined;
  f.page();await flush();assert.equal(f.elements.review.disabled,true);assert.equal(f.calls.length,0);
});
test("new worker can request only fixed readiness, never inherit user consent",async()=>{
  const f=fixture();f.start();await f.send();f.start();await f.send();
  assert.equal(f.calls.length,2);
  assert(f.calls.every(v=>v.body.action==="recovery-launch-ready"));
});
for(const settings of [{...c,extra:1},{...c,binding:true},{...c,intent:"bad"},{...c,nativeHost:"bad/host"}]) {
  test("invalid fixed configuration fails before side effects "+JSON.stringify(settings),()=>{
    const f=fixture();
    assert.throws(()=>connectRecoveryLaunchWorker(f.chrome,settings));
    assert.equal(f.calls.length,0);assert.equal(f.listeners.length,0);
  });
}
test("page refuses embedded or wrong document",()=>{
  const f=fixture();f.window.top={};assert.throws(f.page);
  f.window.top=f.window;f.window.location.href=url+"#fragment";assert.throws(f.page);
});

function navigation() {
  const f=fixture();f.values={};f.opens=[];f.events=[];f.writes=0;f.failure=null;
  f.chrome.runtime.onStartup=f.chrome.runtime.onInstalled={addListener:fn=>f.events.push(fn)};
  f.chrome.storage={session:{setAccessLevel:async value=>{
    assert.deepEqual(value,{accessLevel:"TRUSTED_CONTEXTS"});if(f.failure==="access")throw Error();},
  get:async()=>{if(f.failure==="read")throw Error();return structuredClone(f.values);},
  set:async value=>{f.writes++;if(f.failure==="write")throw Error();
    f.values=structuredClone(value);if(f.failure==="readback")f.values={};
    if(f.failure==="lost-write")throw Error();}}};
  f.chrome.tabs={create:async options=>{f.opens.push(options);if(f.failure==="open")throw Error();}};
  f.startNavigation=()=>connectRecoveryLaunchNavigation(f.chrome,c);
  return f;
}
test("worker owns one exact page opening; startup/install overlap and restart cannot duplicate",async()=>{
  const f=navigation();f.startNavigation();f.events.forEach(fn=>fn());await flush();
  assert.deepEqual(f.opens,[{url,active:true}]);assert.equal(f.writes,1);assert.equal(f.calls.length,0);
  f.startNavigation();await flush();assert.equal(f.opens.length,1);assert.equal(f.writes,1);
});
test("new browser session may open a fresh review page, but never automatic consent",async()=>{
  const f=navigation();f.startNavigation();await flush();f.values={};f.startNavigation();await flush();
  assert.equal(f.opens.length,2);assert.equal(f.calls.length,0);
});
for(const failure of ["access","read","write","readback","lost-write","open"]) {
  test("navigation uncertainty is not retried in this worker: "+failure,async()=>{
    const f=navigation();f.failure=failure;f.startNavigation();await flush();
    f.failure=null;f.events.forEach(fn=>fn());await flush();
    assert.equal(f.opens.length,failure==="open"?1:0);assert.equal(f.calls.length,0);
    if(["lost-write","open"].includes(failure)) {
      f.startNavigation();await flush();assert.equal(f.opens.length,failure==="open"?1:0);
    }
  });
}
for(const marker of [{binding:"e".repeat(64)},{binding:c.binding,consent:true},null,true]) {
  test("stale or malformed page claim never gets replaced: "+JSON.stringify(marker),async()=>{
    const f=navigation();f.values={sdsctlRecoveryLaunchPage:marker};f.startNavigation();await flush();
    assert.equal(f.opens.length,0);assert.equal(f.writes,0);assert.equal(f.calls.length,0);
  });
}
