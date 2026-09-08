import test from "node:test";
import assert from "node:assert/strict";
import {connectChromeRetirementRecovery} from "../../src/sds200/browser_assets/browser_device_retirement_startup.mjs";
import {DEVICE_COOKIE,RECOVERY_ALARM} from "../../src/sds200/browser_assets/browser_device_recovery.mjs";

const identity="b".repeat(64),intent="c".repeat(64),id="a".repeat(32);
const config={identity,origin:"https://127.0.0.1:8443",nativeHost:"org.sdsctl.browser_device"};
const key="sdsctlDeviceRecovery",pending={version:2,identity,paused:true,phase:"resume_pending",nextAt:0,intent};
const paused={version:1,identity,paused:true,phase:"clean",nextAt:0};
const proof={identity,intent,retirement:"d".repeat(64),revision:4,mode:"paused"};
const settle=()=>new Promise(resolve=>setImmediate(resolve));

function fixture() {
  const f={state:structuredClone(pending),calls:[],listeners:[],cookie:null,failure:null};
  const record=(name,args)=>{f.calls.push([name,structuredClone(args)]);if(f.failure===name)throw Error("private failure");};
  f.chrome={
    runtime:{id,getURL:name=>`chrome-extension://${id}/${name}`,
      onMessage:{addListener:fn=>f.listeners.push(fn)},
      onStartup:{addListener:()=>assert.fail("startup listener")},
      onInstalled:{addListener:()=>assert.fail("installation listener")},
      sendNativeMessage:async(host,body)=>{record("native",{host,body});return {version:1,ok:true,evidence:{...proof}};}},
    storage:{local:{
      setAccessLevel:async options=>record("access",options),
      get:async requested=>{record("load",requested);assert.equal(requested,key);return {[key]:structuredClone(f.state)};},
      set:async value=>{record("save",value);f.state=structuredClone(value[key]);
        if(f.failure==="lost-save")throw Error("private failure");
        if(f.failure==="incorrect-save")f.state=structuredClone(pending);
        if(f.failure==="readback")f.failure="load";
      }}},
    cookies:{
      remove:async options=>{record("remove-cookie",options);if(f.failure!=="cookie-remains")f.cookie=null;},
      get:async options=>{record("get-cookie",options);return f.cookie;},
      set:()=>assert.fail("cookie installation")},
    alarms:{clear:async name=>{record("cancel-alarm",name);return true;},
      create:()=>assert.fail("alarm scheduled"),onAlarm:{addListener:()=>assert.fail("alarm listener")}},
  };
  f.sender={id,url:f.chrome.runtime.getURL("recovery.html"),frameId:0,
    documentLifecycle:"active",documentId:"doc-1",tab:{id:1,incognito:false}};
  f.start=()=>{f.listeners=[];assert.equal(connectChromeRetirementRecovery(f.chrome,config),undefined);};
  f.send=async(message,sender=f.sender)=>{
    const replies=[];assert.equal(f.listeners.length,1);
    const waiting=f.listeners[0](message,sender,r=>replies.push(r));
    await settle();return {waiting,replies};
  };
  f.review=async()=>(await f.send({action:"retirement-review"})).replies[0];
  f.resolve=async ticket=>(await f.send({action:"retirement-confirm",ticket})).replies[0];
  return f;
}

test("opening and repeated worker incarnations only restrict/read storage",async()=>{
  const f=fixture();for(let i=0;i<3;i++){f.start();await settle();}
  assert.deepEqual(f.state,pending);
  assert.deepEqual(f.calls,Array.from({length:3},()=>[
    ["access",{accessLevel:"TRUSTED_CONTEXTS"}],["load",key]]).flat());
});
for(const action of ["start","startup-status","status","suspend","authenticate","initialize",
  "review-resume","resume-review","resume-confirm","logout","confirm-retirement"]) {
  test("ordinary or native action is not registered: "+action,async()=>{
    const f=fixture();f.start();await settle();const before=structuredClone(f.calls);
    for(const page of ["recovery.html","control.html","startup.html","setup.html","resume.html"])
      assert.deepEqual(await f.send({action},{...f.sender,url:f.chrome.runtime.getURL(page)}),{waiting:false,replies:[]});
    assert.deepEqual(f.calls,before);assert.deepEqual(f.state,pending);
  });
}
test("review is read-only; trusted confirmation reads back clean-but-paused",async()=>{
  const f=fixture();f.start();await settle();const r=await f.review();
  assert.equal(r.mode,"retirement_reviewed");assert.deepEqual(f.state,pending);
  assert.deepEqual(f.calls.map(c=>c[0]),["access","load","native"]);
  assert.deepEqual(await f.resolve(r.ticket),{mode:"retired_paused"});
  assert.deepEqual(f.state,paused);
  assert.deepEqual(f.calls.map(c=>c[0]),["access","load","native","remove-cookie","get-cookie",
    "cancel-alarm","native","load","save","load"]);
  for(const [name,args] of f.calls) {
    if(name==="native")assert.deepEqual(args,{host:config.nativeHost,
      body:{version:1,action:"confirm-retirement",identity,intent}});
    if(name==="remove-cookie"||name==="get-cookie")assert.deepEqual(args,{url:config.origin+"/",name:DEVICE_COOKIE});
    if(name==="cancel-alarm")assert.equal(args,RECOVERY_ALARM);
  }
  f.calls=[];f.start();await settle();assert.equal((await f.review()).mode,"retirement_refused");
  assert.deepEqual(f.calls.map(c=>c[0]),["access","load"]);assert.deepEqual(f.state,paused);
});
for(const failure of ["access","load","native","remove-cookie","get-cookie","cancel-alarm",
  "cookie-remains","save","lost-save","incorrect-save","readback"]) {
  test("failed or uncertain adapter cannot acknowledge or retry: "+failure,async()=>{
    const f=fixture();
    if(["access","load"].includes(failure))f.failure=failure;
    f.start();await settle();
    if(f.failure===null)f.failure=failure;
    f.cookie={name:DEVICE_COOKIE};
    const r=await f.review();
    if(r.mode==="retirement_reviewed")assert.notEqual((await f.resolve(r.ticket))?.mode,"retired_paused");
    else assert.equal(r.mode,"retirement_refused");
    const before=structuredClone(f.calls);await f.review();await settle();assert.deepEqual(f.calls,before);
    assert(f.calls.filter(c=>c[0]==="save").length<=1);
    assert.equal(f.state.paused,true);assert(!JSON.stringify(r).includes("private failure"));
    assert(f.calls.filter(c=>c[0]==="native").every(c=>c[1].body.action==="confirm-retirement"));
  });
}
for(const state of [undefined,null,{}, {...paused,paused:false}, {...pending,identity:"f".repeat(64)},
  {...pending,nextAt:10}, {...pending,intent:"invalid"}, {...pending,extra:1}]) {
  test("missing, unpaused or corrupt browser state is not initialized: "+JSON.stringify(state),async()=>{
    const f=fixture();f.state=state;f.start();await settle();
    assert.equal((await f.review()).mode,"retirement_refused");assert.deepEqual(f.state,state);
    assert.deepEqual(f.calls.map(c=>c[0]),["access","load"]);
  });
}
test("newer browser intent after review cannot be overwritten",async()=>{
  const f=fixture();f.start();const r=await f.review();f.state={...pending,intent:"e".repeat(64)};
  assert.notEqual((await f.resolve(r.ticket))?.mode,"retired_paused");
  assert.equal(f.state.intent,"e".repeat(64));assert(!f.calls.some(c=>c[0]==="save"));
});
test("newer document and worker do not inherit a consent ticket",async()=>{
  const f=fixture();f.start();const r=await f.review();
  assert.deepEqual(await f.send({action:"retirement-confirm",ticket:r.ticket},
    {...f.sender,documentId:"doc-2"}),{waiting:false,replies:[]});
  f.start();await settle();assert.equal(await f.resolve(r.ticket),undefined);
  assert.deepEqual(f.state,pending);assert(!f.calls.some(c=>c[0]==="save"));
});
