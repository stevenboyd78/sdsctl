import test from "node:test";
import assert from "node:assert/strict";
import {createBrowserRecovery, initialBrowserRecoveryState, DEVICE_COOKIE} from
  "../../src/sds200/browser_assets/browser_device_recovery.mjs";

const config = {origin:"https://192.0.2.18:8443", identity:"a".repeat(64),
  nativeHost:"org.sdsctl.browser_device"};
const token = "sdsctl-browser-session-v1." + "b".repeat(64);
const ticket = "c".repeat(64), intent = "d".repeat(64);
const review = {nativeRevision:3, serverGeneration:4};
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {
  resolve=a; reject=b;
}); return {promise,resolve,reject}; };
const settle = () => new Promise(r => setImmediate(r));

function fixture() {
  const f = {time:1000000, revision:3, mode:"paused", cookie:null, calls:[], saves:[],
    saved:{...initialBrowserRecoveryState(config),paused:true}, alarm:null};
  f.status = () => ({version:1, ok:true, mode:f.mode, revision:f.revision,
    retry_after:0, renew_after:0});
  f.ports = {
    now:()=>f.time, intent:()=>intent,
    load:async()=>structuredClone(f.saved),
    save:async state=>{f.saved=structuredClone(state); f.saves.push(structuredClone(state));},
    native:async ({action})=>{
      f.calls.push(action);
      if (action === "suspend") {f.mode="paused"; f.revision++;}
      assert.notEqual(action,"authenticate", "Resume must not use unconstrained ordinary exchange");
      return f.status();
    },
    clearCookie:async()=>{f.cookie=null;},
    setCookie:async details=>{f.calls.push("cookie"); f.cookie=details;},
    cancel:async()=>{f.alarm=null;}, schedule:async at=>{f.alarm=at;},
    resume:{
      prepare:async request=>{
        f.calls.push("prepare");
        assert.equal(f.saved.phase,"resume_pending");
        assert.equal(f.saved.paused,true);
        assert.deepEqual(request,{intent,nativeRevision:3,serverGeneration:4});
        f.revision++;
        return {ticket,revision:f.revision,expires_at:f.time/1000+120};
      },
      commit:async request=>{
        f.calls.push("commit");
        assert.equal(request.intent,intent); assert.equal(request.approval.ticket,ticket);
        assert.equal(f.saved.phase,"resume_pending");
        f.revision+=2; f.mode="active";
        return {...f.status(),renew_after:225,session:{token,expires_in:300}};
      },
      verifySession:async()=>{
        f.calls.push("verify");
        assert.equal(f.saved.phase,"resume_pending");
        assert.equal(f.saved.paused,true);
        assert.equal(f.cookie.value,token);
        return true;
      },
    },
  };
  f.make=()=>createBrowserRecovery(f.ports,config);
  return f;
}

test("explicit consent stays pending until fresh cookie and protected access verified", async()=>{
  const f=fixture(), c=f.make();
  assert.deepEqual(await c.resume(review),{mode:"active"});
  assert.deepEqual(f.calls,["prepare","commit","status","cookie","verify","status"]);
  assert.equal(c.readiness().sessionReady,true);
  assert.equal(f.saved.version,1); assert.equal(f.saved.paused,false);
  assert.equal(f.saved.phase,"clean");
  assert.equal(f.cookie.name,DEVICE_COOKIE); assert.equal(f.cookie.httpOnly,true);
  assert.equal(f.cookie.sameSite,"strict"); assert.equal(f.cookie.secure,true);
  assert(f.alarm > f.time);
  for (const saved of f.saves) {
    assert(!JSON.stringify(saved).includes(ticket)); assert(!JSON.stringify(saved).includes(token));
  }
  assert.deepEqual(await c.resume(review),{mode:"resume_refused"});
});

test("same-worker sign-out can be explicitly resumed without bypassing native review", async()=>{
  const f=fixture(), c=f.make();
  await c.suspend(); f.revision=3;
  assert.equal((await c.resume(review)).mode,"active");
  assert.equal(c.readiness().sessionReady,true);
  await c.suspend();
  assert.equal(c.readiness().sessionReady,false); assert.equal(f.saved.paused,true);
});

test("no adapter means no resume, persistence, native action or cookie change", async()=>{
  const f=fixture(); delete f.ports.resume;
  const before=structuredClone(f.saved);
  assert.equal(f.make().resume,undefined);
  assert.deepEqual(f.saved,before); assert.deepEqual(f.calls,[]);
});

for (const bad of [null,{},true,{...review,extra:true},{...review,nativeRevision:true},
  {...review,serverGeneration:0},{...review,serverGeneration:Number.MAX_SAFE_INTEGER}]) {
  test(`malformed review cannot touch state: ${JSON.stringify(bad)}`, async()=>{
    const f=fixture(); assert.equal((await f.make().resume(bad)).mode,"resume_refused");
    assert.deepEqual(f.calls,[]); assert.deepEqual(f.saves,[]);
  });
}

for (const stage of ["pending-save","clear","cancel","prepare","commit","status-before",
  "cookie","verify","status-after","final-save"]) {
  test(`interrupted ${stage} never reports readiness or replays approval`, async()=>{
    const f=fixture(); let statuses=0;
    const save=f.ports.save, native=f.ports.native;
    f.ports.save=async state=>{
      if ((stage === "pending-save" && state.phase === "resume_pending") ||
          (stage === "final-save" && state.paused === false)) throw new Error("private");
      await save(state);
    };
    if (stage === "clear") f.ports.clearCookie=async()=>{throw new Error("private");};
    if (stage === "cancel") f.ports.cancel=async()=>{throw new Error("private");};
    for (const key of ["prepare","commit","verifySession"]) {
      if (stage === (key === "verifySession" ? "verify" : key)) f.ports.resume[key]=async()=>{throw new Error("private");};
    }
    if (stage === "cookie") f.ports.setCookie=async()=>{throw new Error("private");};
    f.ports.native=async request=>{
      if (request.action === "status") {
        statuses++;
        if ((stage === "status-before" && statuses===1) || (stage === "status-after" && statuses===2))
          throw new Error("private");
      }
      return native(request);
    };
    const c=f.make(); assert.equal((await c.resume(review)).mode,"setup_error");
    assert.equal(c.readiness().sessionReady,false);
    assert.equal((await c.resume(review)).mode,"resume_refused");
    if (stage !== "pending-save") assert.equal(f.saved.phase,"resume_pending");
    assert.equal(f.saved.paused,true);
    assert.equal(f.cookie,null);
  });
}

for (const stop of ["suspend","beginLogout"]) {
for (const stage of ["prepare","commit","setCookie","verifySession"]) {
  test(`newer ${stop} wins while ${stage} is outstanding`, async()=>{
    const f=fixture(), gate=deferred();
    const port=stage === "setCookie" ? f.ports : f.ports.resume;
    const original=port[stage];
    port[stage]=async args=>{gate.resolve(); await finish.promise; return original(args);};
    const finish=deferred(), c=f.make();
    const pending=c.resume(review); await gate.promise;
    const pause=c[stop](); await settle();
    assert.equal(f.saved.paused,true); assert.equal(c.readiness().sessionReady,false);
    finish.resolve(); await pending; await pause;
    assert.equal(f.saved.paused,true); assert.equal(f.cookie,null);
    assert.equal(f.mode,"paused"); assert.equal(c.readiness().sessionReady,false);
    if (stage === "prepare") assert(!f.calls.includes("commit"));
  });
}
}

test("a queued alarm cannot reassert pause halfway through approved operation", async()=>{
  const f=fixture(), gate=deferred(), finish=deferred(), original=f.ports.resume.commit;
  f.ports.resume.commit=async args=>{gate.resolve(); await finish.promise; return original(args);};
  const c=f.make(), pending=c.resume(review); await gate.promise;
  const tick=c.tick(); finish.resolve();
  assert.equal((await pending).mode,"active"); await tick;
  assert(!f.calls.includes("suspend")); assert.equal(c.readiness().sessionReady,true);
});

test("worker restart keeps pending consent, cancels native approval, never authenticates", async()=>{
  const f=fixture();
  f.ports.resume.commit=async()=>{f.mode="active";throw new Error("lost native response");};
  await f.make().resume(review);
  const pending=structuredClone(f.saved);
  assert.equal(pending.version,2); assert.equal(pending.intent,intent);
  f.calls=[];
  const next=f.make(); assert.equal((await next.tick()).mode,"paused");
  assert.deepEqual(f.saved,pending);
  assert.deepEqual(f.calls,["suspend"]);
  assert.equal((await next.resume(review)).mode,"resume_refused");
  assert.equal(next.readiness().sessionReady,false);
});

for (const change of [{revision:9},{ticket:"x"},{expires_at:0},{expires_at:2000},{extra:true}]) {
  test(`invalid native approval is not committed: ${JSON.stringify(change)}`, async()=>{
    const f=fixture(), prepare=f.ports.resume.prepare;
    f.ports.resume.prepare=async args=>({...await prepare(args),...change});
    assert.equal((await f.make().resume(review)).mode,"setup_error");
    assert(!f.calls.includes("commit")); assert.equal(f.saved.paused,true);
  });
}

for (const change of [{revision:10},{mode:"paused",session:undefined},{session:undefined},
  {session:{token,expires_in:5}},{unknown:token}]) {
  test(`invalid or stale fresh session never installs: ${JSON.stringify(change)}`, async()=>{
    const f=fixture(), commit=f.ports.resume.commit;
    f.ports.resume.commit=async args=>({...await commit(args),...change});
    assert.equal((await f.make().resume(review)).mode,"setup_error");
    assert(!f.calls.includes("cookie")); assert.equal(f.saved.paused,true);
  });
}

test("protected-session denial clears cookie and preserves pending pause", async()=>{
  const f=fixture(); f.ports.resume.verifySession=async()=>false;
  assert.equal((await f.make().resume(review)).mode,"setup_error");
  assert.equal(f.cookie,null); assert.equal(f.saved.phase,"resume_pending");
});

test("new sign-out during final persistence is last durable writer", async()=>{
  const f=fixture(), gate=deferred(), finish=deferred(), save=f.ports.save;
  f.ports.save=async state=>{
    if (!state.paused) {gate.resolve(); await finish.promise;}
    await save(state);
  };
  const c=f.make(), pending=c.resume(review); await gate.promise;
  const pause=c.suspend(); finish.resolve(); await pending; await pause;
  assert.equal(f.saved.paused,true); assert.equal(f.mode,"paused");
  assert.equal(f.cookie,null); assert.equal(c.readiness().sessionReady,false);
});

test("concurrent approval requests consume only one", async()=>{
  const f=fixture(), c=f.make();
  const results=await Promise.all(Array.from({length:20},()=>c.resume(review)));
  assert.equal(results.filter(x=>x.mode === "active").length,1);
  assert.equal(results.filter(x=>x.mode === "resume_refused").length,19);
  assert.equal(f.calls.filter(x=>x === "commit").length,1);
});

test("mutable caller review is snapshotted before any asynchronous work", async()=>{
  const f=fixture(), c=f.make(), changed={...review};
  const operation=c.resume(changed); changed.serverGeneration=9; changed.nativeRevision=20;
  assert.equal((await operation).mode,"active");
});

test("clock rollback during commit leaves pending pause without installing a cookie", async()=>{
  const f=fixture(), commit=f.ports.resume.commit;
  f.ports.resume.commit=async args=>{const result=await commit(args);f.time-=10000;return result;};
  assert.equal((await f.make().resume(review)).mode,"setup_error");
  assert.equal(f.cookie,null); assert.equal(f.saved.phase,"resume_pending");
});

test("lost final storage acknowledgement is distinct from uncommitted consent", async()=>{
  const f=fixture(), save=f.ports.save;
  f.ports.save=async state=>{
    await save(state);
    if (!state.paused) throw new Error("lost final acknowledgement");
  };
  const c=f.make();
  assert.equal((await c.resume(review)).mode,"setup_error");
  assert.equal(c.readiness().sessionReady,false); assert.equal(f.cookie,null);
  assert(f.calls.includes("verify"));
  assert.equal(f.saved.paused,false); // The verified FINAL consent commit actually succeeded.
  assert.equal((await c.resume(review)).mode,"resume_refused");
  // A later worker may perform ordinary recovery under that saved consent;
  // it must not replay the consumed preparation/commit to manufacture another ACK.
});

for (const corrupt of [{version:1}, {identity:"e".repeat(64)}, {paused:false},
  {intent:"x"}, {nextAt:1}, {phase:"clean"}, {extra:true}]) {
  test(`corrupt pending consent does not authenticate or reset: ${JSON.stringify(corrupt)}`, async()=>{
    const f=fixture(); f.saved={version:2, identity:config.identity, paused:true,
      phase:"resume_pending", nextAt:0, intent,...corrupt};
    const before=structuredClone(f.saved), c=f.make();
    assert.equal((await c.tick()).mode,"setup_error");
    assert.equal((await c.resume(review)).mode,"setup_error");
    assert.deepEqual(f.saved,before); assert.deepEqual(f.calls,[]);
  });
}
