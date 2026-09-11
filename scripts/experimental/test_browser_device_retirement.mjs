import test from "node:test";
import assert from "node:assert/strict";
import {createBrowserRecovery, initialBrowserRecoveryState} from
  "../../src/sds200/browser_assets/browser_device_recovery.mjs";

const config={origin:"https://192.0.2.18:8443",identity:"a".repeat(64),nativeHost:"org.sdsctl.browser_device"};
const intent="b".repeat(64), retirement="c".repeat(64);
const proof={identity:config.identity,intent,retirement,mode:"paused",revision:5};
const review={nativeRevision:5,nativeMode:"paused"};
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {resolve,promise};};
const settle=()=>new Promise(r=>setImmediate(r));

function fixture() {
  const f={time:100000,mode:"paused",revision:5,calls:[],saves:[],cookie:"old-session",alarm:999999,
    saved:{version:2,identity:config.identity,paused:true,phase:"resume_pending",nextAt:0,intent}};
  f.ports={
    now:()=>f.time,load:async()=>structuredClone(f.saved),
    save:async value=>{f.saved=structuredClone(value);f.saves.push(structuredClone(value));},
    native:async({action})=>{
      f.calls.push(action);
      assert.notEqual(action,"authenticate");
      if(action==="suspend")f.mode="paused";
      return {version:1,ok:true,mode:f.mode,revision:f.revision,retry_after:0,renew_after:0};
    },
    clearCookie:async()=>{f.calls.push("clear");f.cookie=null;},
    setCookie:async()=>{throw new Error("Retirement must not install a cookie");},
    schedule:async at=>{f.alarm=at;},cancel:async()=>{f.calls.push("cancel");f.alarm=null;},
    retirement:{confirm:async request=>{
      assert.deepEqual(request,{identity:config.identity,intent});
      f.calls.push("confirm");return {...proof,mode:f.mode,revision:f.revision};
    }},
    resume:{review:async()=>{
      f.calls.push("resume-review");
      return {version:1,ok:true,mode:f.mode,revision:f.revision,generation:7};
    }},
  };
  f.make=()=>createBrowserRecovery(f.ports,config);
  return f;
}

test("review is read-only and explicit retirement leaves clean paused state",async()=>{
  const f=fixture(),c=f.make(),before=structuredClone(f.saved);
  assert.deepEqual(await c.retirePending(review),{mode:"retirement_refused"});
  assert.deepEqual(await c.reviewPendingRetirement(),{mode:"retirement_reviewed",...review});
  assert.deepEqual(f.saved,before);assert.deepEqual(f.saves,[]);assert.deepEqual(f.calls,["confirm"]);
  assert.equal(f.cookie,"old-session");
  assert.deepEqual(await c.retirePending(review),{mode:"retired_paused",localPauseSaved:true,sessionReady:false});
  assert.deepEqual(f.calls,["confirm","clear","cancel","confirm"]);
  assert.deepEqual(f.saved,{...initialBrowserRecoveryState(config),paused:true});
  assert.equal(f.cookie,null);assert.equal(f.alarm,null);assert.equal(c.readiness().sessionReady,false);
  assert.equal((await c.reviewResume()).mode,"resume_refused");
  assert.equal((await c.retirePending(review)).mode,"retirement_refused");
  // A fresh worker still doesn't sign in. Only a separate new review is possible.
  f.calls=[];const next=f.make();assert.equal((await next.tick()).mode,"paused");
  assert(!f.calls.includes("authenticate"));assert.equal(f.saved.paused,true);
  assert.equal((await next.reviewResume()).mode,"reviewed");
});

test("no trusted adapter means no retirement methods or side effects",async()=>{
  const f=fixture();delete f.ports.retirement;
  const c=f.make();assert.equal(c.reviewPendingRetirement,undefined);assert.equal(c.retirePending,undefined);
  await settle();assert.deepEqual(f.calls,[]);assert.deepEqual(f.saves,[]);
});

for(const state of [undefined,{}, {...initialBrowserRecoveryState(config),paused:true},
  {...initialBrowserRecoveryState(config),phase:"installing"},
  {version:2,identity:config.identity,paused:false,phase:"resume_pending",nextAt:0,intent},
  {version:2,identity:"f".repeat(64),paused:true,phase:"resume_pending",nextAt:0,intent}]) {
  test("absent/ordinary/invalid state cannot be retired: "+JSON.stringify(state),async()=>{
    const f=fixture();f.saved=state;const c=f.make();
    assert.equal((await c.reviewPendingRetirement()).mode,"retirement_refused");
    assert.equal((await c.retirePending(review)).mode,"retirement_refused");
    assert.deepEqual(f.calls,[]);assert.deepEqual(f.saves,[]);
  });
}

for(const change of [{identity:"f".repeat(64)},{intent:"f".repeat(64)},
  {retirement:"bad"},{mode:"active"},{mode:"unknown"},{revision:true},{revision:0},
  {revision:Number.MAX_SAFE_INTEGER},{session:{token:"private"}},{extra:"private"}]) {
  test("invalid native evidence cannot authorize retirement: "+JSON.stringify(change),async()=>{
    const f=fixture();f.ports.retirement.confirm=async()=>({...proof,...change});const c=f.make();
    assert.equal((await c.reviewPendingRetirement()).mode,"retirement_refused");
    assert.equal((await c.retirePending(review)).mode,"retirement_refused");
    assert.deepEqual(f.saves,[]);assert.equal(f.cookie,"old-session");
  });
}

for(const mode of ["paused","credential_rejected","tls_error","setup_error","protocol_error"]) {
  test("retirement preserves stopped native mode: "+mode,async()=>{
    const f=fixture();f.mode=mode;const c=f.make();
    const r=await c.reviewPendingRetirement();assert.equal(r.nativeMode,mode);
    assert.equal((await c.retirePending({nativeRevision:r.nativeRevision,nativeMode:r.nativeMode})).mode,"retired_paused");
    assert.equal(f.mode,mode);assert(f.saved.paused);assert(!f.calls.includes("suspend"));
  });
}

for(const change of ["expired","rollback","revision","mode","retirement","intent","identity","storage"]) {
  test("stale review or changed evidence/storage is refused: "+change,async()=>{
    const f=fixture(),c=f.make();await c.reviewPendingRetirement();
    if(change==="expired")f.time+=60000;
    else if(change==="rollback")f.time--;
    else if(change==="revision")f.revision++;
    else if(change==="mode")f.mode="tls_error";
    else if(change==="storage")f.saved={...f.saved,intent:"f".repeat(64)};
    else f.ports.retirement.confirm=async()=>({...proof,[change]:"f".repeat(64)});
    const result=await c.retirePending(review);
    assert.notEqual(result.mode,"retired_paused");assert.equal(f.saved.phase,"resume_pending");
    assert.equal(f.saved.paused,true);assert.equal(c.readiness().sessionReady,false);
    assert.equal((await c.retirePending(review)).mode,"retirement_refused");
  });
}

for(const bad of [null,{},true,{...review,extra:true},{...review,nativeRevision:true},
  {...review,nativeRevision:6},{...review,nativeMode:"active"}]) {
  test("invalid confirmation cannot mutate: "+JSON.stringify(bad),async()=>{
    const f=fixture(),c=f.make();await c.reviewPendingRetirement();
    assert.equal((await c.retirePending(bad)).mode,"retirement_refused");assert.deepEqual(f.saves,[]);
  });
}

for(const stage of ["review","clear","cancel","confirm","load","save","lost-save-ack"]) {
  test("failure at "+stage+" cannot authenticate, report success or retry automatically",async()=>{
    const f=fixture(),c=f.make();
    const fail=async()=>{throw new Error("private failure");};
    if(stage==="review")f.ports.retirement.confirm=fail;
    await c.reviewPendingRetirement();
    if(stage==="clear")f.ports.clearCookie=fail;
    if(stage==="cancel")f.ports.cancel=fail;
    if(stage==="confirm")f.ports.retirement.confirm=fail;
    if(stage==="load")f.ports.load=fail;
    const save=f.ports.save;
    if(stage==="save")f.ports.save=fail;
    if(stage==="lost-save-ack")f.ports.save=async value=>{await save(value);throw new Error("lost");};
    const result=await c.retirePending(review);assert.notEqual(result.mode,"retired_paused");
    assert.equal(f.saved.paused,true);assert.equal(c.readiness().sessionReady,false);
    assert.equal(f.saved.phase,stage==="lost-save-ack"?"clean":"resume_pending");
    assert.equal((await c.retirePending(review)).mode,"retirement_refused");
    assert.equal((await c.reviewPendingRetirement()).mode,"retirement_refused");
    f.ports.save=save;f.ports.load=async()=>structuredClone(f.saved);
    f.ports.clearCookie=async()=>{f.cookie=null;};f.ports.cancel=async()=>{f.alarm=null;};
    const before=structuredClone(f.saved);assert.equal((await f.make().tick()).mode,"paused");
    assert.deepEqual(f.saved,before);assert(!f.calls.includes("authenticate"));
  });
}

for(const stop of ["suspend","beginLogout"])for(const stage of ["review","clear","confirm","load","save"]) {
  test("newer "+stop+" wins during "+stage,async()=>{
    const f=fixture(),c=f.make(),entered=deferred(),release=deferred();
    if(stage!=="review")await c.reviewPendingRetirement();
    let owner=f.ports,key=stage==="clear"?"clearCookie":stage;
    if(stage==="review"||stage==="confirm"){owner=f.ports.retirement;key="confirm";}
    const original=owner[key];let blocked=false;
    owner[key]=async(...args)=>{
      if(!blocked){blocked=true;entered.resolve();await release.promise;}
      return original(...args);
    };
    const operation=stage==="review"?c.reviewPendingRetirement():c.retirePending(review);
    await entered.promise;const stopping=c[stop]();await settle();release.resolve();
    const result=await operation;await stopping;
    assert.notEqual(result.mode,"retired_paused");assert.notEqual(result.mode,"retirement_reviewed");
    assert.equal(f.saved.paused,true);assert.equal(c.readiness().sessionReady,false);
    if(stage!=="save")assert.equal(f.saved.phase,"resume_pending");
    assert.equal(f.cookie,null);assert(!f.calls.includes("authenticate"));
  });
}

test("queued wake cannot change native revision halfway through explicit retirement",async()=>{
  const f=fixture(),c=f.make(),entered=deferred(),release=deferred();await c.reviewPendingRetirement();
  const confirm=f.ports.retirement.confirm;
  f.ports.retirement.confirm=async request=>{entered.resolve();await release.promise;return confirm(request);};
  const operation=c.retirePending(review);await entered.promise;
  const waking=c.tick();assert(!f.calls.includes("suspend"));release.resolve();
  assert.equal((await operation).mode,"retired_paused");assert.equal((await waking).mode,"paused");
});

test("two confirmations consume the reviewed operation only once",async()=>{
  const f=fixture(),c=f.make();await c.reviewPendingRetirement();
  const values=await Promise.all([c.retirePending(review),c.retirePending(review)]);
  assert.deepEqual(values.map(v=>v.mode).sort(),["retired_paused","retirement_refused"].sort());
  assert.equal(f.calls.filter(v=>v==="confirm").length,2);assert.equal(f.saves.length,1);
});

test("review is not persisted or reused after worker restart",async()=>{
  const f=fixture(),c=f.make();await c.reviewPendingRetirement();
  const next=f.make();assert.equal((await next.retirePending(review)).mode,"retirement_refused");
  assert.equal(f.saved.phase,"resume_pending");assert.equal(f.saved.intent,intent);
  assert(!JSON.stringify(f.saved).includes(retirement));
});

test("failed earlier resume requires a fresh worker before retirement review",async()=>{
  const f=fixture();f.ports.load=async()=>{throw new Error("unsafe storage");};
  const c=f.make();assert.equal((await c.tick()).mode,"setup_error");
  assert.equal((await c.reviewPendingRetirement()).mode,"retirement_refused");
  assert(!f.calls.includes("confirm"));
});

for(const stage of ["review","save"])for(const delta of [-1,60000]) {
  test("a delayed "+stage+" cannot acknowledge across clock expiry/rollback: "+delta,async()=>{
    const f=fixture(),c=f.make();
    if(stage==="save")await c.reviewPendingRetirement();
    const owner=stage==="review"?f.ports.retirement:f.ports;
    const key=stage==="review"?"confirm":"save",original=owner[key];
    owner[key]=async(...args)=>{const result=await original(...args);f.time+=delta;return result;};
    const result=stage==="review"?await c.reviewPendingRetirement():await c.retirePending(review);
    assert.equal(result.mode,"retirement_refused");assert.equal(f.saved.paused,true);
    assert.equal(c.readiness().sessionReady,false);assert(!f.calls.includes("authenticate"));
  });
}
