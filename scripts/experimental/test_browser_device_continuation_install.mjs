import test from 'node:test';
import assert from 'node:assert/strict';
import {createContinuationInstallation} from '../../src/sds200/browser_assets/browser_device_continuation_install.mjs';
import {createContinuationStopFence} from '../../src/sds200/browser_assets/browser_device_continuation_stop.mjs';
import {pausedContinuationRecord,classifyContinuationStartup} from '../../src/sds200/browser_assets/browser_device_continuation_state.mjs';

// Deterministic Chrome/native/probe contract doubles, NOT end-to-end native
// consent or real-Chromium acceptance. The production dispatch remains read-only.
const KEY='sdsctlDeviceRecovery', COOKIE='__Host-sdsctl-device-session';
const STOP='sdsctlContinuationStop';
const config={identity:'a'.repeat(64),epoch:'b'.repeat(64),build:'c'.repeat(64),
  origin:'https://display.example.test'};
const review={identity:config.identity,epoch:config.epoch,mode:'paused',
  binding:{fingerprint:'d'.repeat(64),revision:4,generation:7}};
const offline={...review,binding:{...review.binding,generation:null}};
const active={...review,mode:'active',binding:{fingerprint:'e'.repeat(64),revision:6,generation:7}};
const token='sdsctl-browser-session-v1.'+'1'.repeat(64);
const selection={tabId:17,documentId:'probe-1',ticket:'f'.repeat(64)};
const failure={message:'Browser continuation installation is unconfirmed; retain saved state.'};
const clone=structuredClone;
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const reordered=value=>value!==null&&typeof value==='object'&&!Array.isArray(value)?
  Object.fromEntries(Object.entries(value).reverse().map(([k,v])=>[k,reordered(v)])):value;

function fixture({origin=config.origin,legacy=true}={}) {
  const f={config:{...config,origin},wall:1000000,mono:500000,calls:[],writes:[],issues:0,
    cookie:null,alarm:null,native:clone(offline),timers:new Set(),closed:0,before:null,after:null};
  f.saved={[KEY]:legacy?{version:1,identity:config.identity,paused:true,phase:'clean',nextAt:0}:
    pausedContinuationRecord(f.config)};
  f.call=async(name,fn)=>{
    const index=f.calls.length;f.calls.push(name);
    await f.before?.(name,index);
    const result=fn();await f.after?.(name,index,result);
    return clone(result);
  };
  f.chrome={storage:{local:{
    setAccessLevel:options=>f.call('access',()=>assert.deepEqual(options,{accessLevel:'TRUSTED_CONTEXTS'})),
    get:key=>f.call(key===STOP?'read-stop':'read',()=>{
      assert([null,STOP].includes(key));
      return reordered(key===null?f.saved:Object.hasOwn(f.saved,key)?{[key]:f.saved[key]}:{});
    }),
    set:values=>f.call(Object.hasOwn(values,STOP)?'write-stop':'write-'+values[KEY].phase,()=>{
      // Chrome set updates the provided keys; it does not replace the whole
      // storage area. Preserve unrelated keys, including a concurrent stop.
      f.writes.push(clone(values));f.saved=reordered({...f.saved,...clone(values)});
    }),
  }},cookies:{
    get:query=>f.call('cookie',()=>{
      assert.deepEqual(query,{url:origin+'/',name:COOKIE});return reordered(f.cookie);
    }),
    set:details=>f.call('cookie-set',()=>{
      assert.equal(f.saved[KEY].phase,'initial_pending');
      const {url,...v}=clone(details);
      f.cookie={...v,domain:new URL(url).hostname,hostOnly:true,session:false};
      return reordered(f.cookie);
    }),
    remove:()=>assert.fail('No cookie repair/removal permitted'),
  },alarms:{
    get:name=>f.call('alarm',()=>{assert.equal(name,'sdsctl-device-recovery');return f.alarm;}),
    create:()=>assert.fail('No renewal scheduling permitted'),
    clear:()=>assert.fail('No alarm repair permitted'),
  }};
  f.ports={
    readCurrent:()=>f.call('current',()=>reordered(f.native)),
    issueInitial:request=>f.call('issue',()=>{
      f.issues++;
      assert.equal(f.saved[KEY].phase,'initial_pending');
      assert.equal(request.intent,f.saved[KEY].intent);
      assert.deepEqual(request.binding,review.binding);assert.equal(request.epoch,config.epoch);
      f.native=clone(active);return {binding:clone(active.binding),session:{token,expires_in:300}};
    }),
    createProbe:({signal})=>{
      assert.equal(signal.aborted,false);f.signal=signal;
      return {open:()=>f.call('probe-open',()=>selection),
        verify:()=>f.call('probe-verify',()=>({url:origin+'/device-display',...selection,
          displayOnly:true,deviceEnrolled:true,remainingSeconds:300})),
        close:()=>{f.closed++;}};
    },
    wall:()=>f.wall,monotonic:()=>f.mono,
    schedule:(fn,ms)=>{assert.equal(ms,45000);const t={fn};f.timers.add(t);return t;},
    cancel:timer=>f.timers.delete(timer),
  };
  f.make=()=>createContinuationInstallation(f.chrome,f.config,review,f.ports);
  f.owner=f.make();return f;
}

test('imports/construction are inert; one actual-I/O lane orders pending, issue, cookie, probe, acceptance',async()=>{
  const f=fixture();assert.deepEqual(f.calls,[]);
  assert.deepEqual(await f.owner.run(),{mode:'accepted',sessionReady:true});
  assert.equal(f.issues,1);assert.equal(f.closed,1);assert.equal(f.timers.size,0);
  assert.deepEqual(f.writes.map(v=>v[KEY].phase),['initial_pending','accepted']);
  assert(f.calls.indexOf('write-initial_pending')<f.calls.indexOf('issue'));
  assert(f.calls.indexOf('issue')<f.calls.indexOf('cookie-set'));
  assert(f.calls.indexOf('cookie-set')<f.calls.indexOf('probe-verify'));
  assert(f.calls.indexOf('probe-verify')<f.calls.indexOf('write-accepted'));
  assert.equal(f.saved[KEY].cookieFingerprint.length,64);
  assert(!JSON.stringify(f.saved).includes(token));
  assert(!JSON.stringify(f.writes).includes(token));
  assert.deepEqual(classifyContinuationStartup(f.config,f.saved[KEY],active),
    {mode:'verification_required',sessionReady:false});
  const count=f.calls.length;await assert.rejects(f.owner.run(),failure);
  assert.equal(f.calls.length,count);await assert.rejects(f.make().run(),failure);
  assert.equal(f.issues,1);
});

for(const origin of ['https://display.example.test','https://192.0.2.18:8443','https://[2001:db8::18]:8443'])
  for(const legacy of [true,false])test('exact clean '+(legacy?'legacy':'v3')+' pause at '+origin,async()=>{
    const f=fixture({origin,legacy});await f.owner.run();
    assert.equal(f.issues,1);assert.equal(f.saved[KEY].phase,'accepted');
    assert.equal(f.cookie.domain,new URL(origin).hostname);
  });

const baseline=fixture();await baseline.owner.run();
for(const when of ['before','after'])for(const [cut,name] of baseline.calls.entries()) {
  test(when+' failed API acknowledgement at '+cut+' '+name+' never resumes or repairs',async()=>{
    const f=fixture();f[when]=(_name,index)=>{if(index===cut)throw Error('private underlying detail');};
    await assert.rejects(f.owner.run(),failure);
    const count=f.calls.length, saved=clone(f.saved), installed=clone(f.cookie);
    f[when]=null;await assert.rejects(f.owner.run(),failure);
    assert.equal(f.calls.length,count);assert.deepEqual(f.saved,saved);assert.deepEqual(f.cookie,installed);
    assert(f.issues<=1);assert.equal(f.timers.size,0);
    if(name==='write-initial_pending')assert.equal(f.issues,0);
    if(when==='after'&&name==='write-accepted') {
      assert.equal(f.saved[KEY].phase,'accepted');
      assert.equal(classifyContinuationStartup(f.config,f.saved[KEY],f.native).sessionReady,false);
    }
    if(f.writes.length) {
      // A restart does not adopt either initial_pending or accepted as fresh consent.
      await assert.rejects(f.make().run(),failure);assert(f.issues<=1);
    }
  });
}

for(const method of ['timeout','invalidate'])for(const name of ['write-initial_pending','issue','cookie-set','write-accepted'])
  test(method+' while '+name+' is outstanding allows late commit but no follow-on I/O',async()=>{
    const f=fixture(),entered=deferred(),release=deferred();
    f.before=async action=>{if(action===name){entered.resolve();await release.promise;}};
    const running=f.owner.run();await entered.promise;
    if(method==='timeout')for(const t of f.timers)t.fn();else f.owner.invalidate();
    await assert.rejects(running,failure);const count=f.calls.length;
    await assert.rejects(f.owner.run(),failure);release.resolve();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(f.calls.length,count);assert.equal(f.timers.size,0);
    if(name==='write-initial_pending') {assert.equal(f.saved[KEY].phase,'initial_pending');assert.equal(f.issues,0);}
    if(name==='issue') {assert.equal(f.native.mode,'active');assert.equal(f.cookie,null);}
    if(name==='cookie-set') {assert.equal(f.cookie.value,token);assert.equal(f.saved[KEY].phase,'initial_pending');}
    if(name==='write-accepted')assert.equal(f.saved[KEY].phase,'accepted');
    assert.equal(classifyContinuationStartup(f.config,f.saved[KEY],f.native).sessionReady,false);
  });

test('second concurrent run is refused without cancelling or duplicating the first',async()=>{
  const f=fixture(),entered=deferred(),release=deferred();
  f.before=async name=>{if(name==='issue'){entered.resolve();await release.promise;}};
  const running=f.owner.run();await entered.promise;await assert.rejects(f.owner.run(),failure);
  release.resolve();assert.equal((await running).sessionReady,true);assert.equal(f.issues,1);
});

for(const change of ['wall-back','mono-back','wall-expired','mono-expired','wall-nan','mono-inf'])
  for(const name of ['write-initial_pending','issue','cookie-set','probe-verify','write-accepted'])
    test(change+' after '+name+' is refused even without a timer callback',async()=>{
      const f=fixture();f.after=action=>{if(action!==name)return;
        if(change==='wall-back')f.wall--;if(change==='mono-back')f.mono--;
        if(change==='wall-expired')f.wall+=45000;if(change==='mono-expired')f.mono+=45000;
        if(change==='wall-nan')f.wall=NaN;if(change==='mono-inf')f.mono=Infinity;
      };
      await assert.rejects(f.owner.run(),failure);
      assert.equal(f.calls.at(-1),name);assert(f.issues<=1);assert.equal(f.timers.size,0);
    });

for(const change of [s=>({}),s=>({...s,other:true}),s=>({[KEY]:null}),
  s=>({[KEY]:{...s[KEY],identity:'9'.repeat(64)}}),s=>({[KEY]:{...s[KEY],paused:false}}),
  s=>({[KEY]:{...s[KEY],phase:'pending'}}),s=>({[KEY]:{...s[KEY],nextAt:100}})])
  test('unknown/unclean saved record is not initialized or rewritten '+change.toString(),async()=>{
    const f=fixture();f.saved=change(f.saved);const saved=clone(f.saved);
    await assert.rejects(f.owner.run(),failure);assert.equal(f.issues,0);
    assert.equal(f.writes.length,0);assert.deepEqual(f.saved,saved);
  });

for(const kind of ['cookie','alarm','native-revision','native-fingerprint','native-generation','saved'])
  test('changed '+kind+' during pending write prevents session issuance',async()=>{
    const f=fixture();f.after=name=>{if(name!=='write-initial_pending')return;
      if(kind==='cookie')f.cookie={value:'foreign-cookie'};
      if(kind==='alarm')f.alarm={name:'sdsctl-device-recovery'};
      if(kind==='native-revision')f.native.binding.revision++;
      if(kind==='native-fingerprint')f.native.binding.fingerprint='9'.repeat(64);
      if(kind==='native-generation')f.native.binding.generation=7;
      if(kind==='saved')f.saved[KEY].intent='9'.repeat(64);
    };
    await assert.rejects(f.owner.run(),failure);assert.equal(f.issues,0);
    assert(!f.calls.includes('cookie-set'));assert.equal(f.saved[KEY].phase,'initial_pending');
  });

for(const name of ['probe-open','probe-verify','write-accepted'])
  for(const kind of ['cookie','native','saved','alarm'])test(kind+' replacement after '+name+' cannot become ready',async()=>{
    const f=fixture();f.after=action=>{if(action!==name)return;
      if(kind==='cookie')f.cookie.value='sdsctl-browser-session-v1.'+'9'.repeat(64);
      if(kind==='native')f.native.binding.fingerprint='9'.repeat(64);
      if(kind==='saved')f.saved[KEY].intent='9'.repeat(64);
      if(kind==='alarm')f.alarm={name:'sdsctl-device-recovery'};
    };
    await assert.rejects(f.owner.run(),failure);assert.equal(f.issues,1);
    assert.equal(f.closed,1);assert.equal(f.writes.length,name==='write-accepted'?2:1);
  });

test('resolved storage set with wrong readback is insufficient for either persistence boundary',async()=>{
  for(const phase of ['initial_pending','accepted']) {
    const f=fixture();f.after=name=>{if(name==='write-'+phase)f.saved[KEY].intent='9'.repeat(64);};
    await assert.rejects(f.owner.run(),failure);assert.equal(f.issues,phase==='initial_pending'?0:1);
    assert.equal(f.saved[KEY].intent,'9'.repeat(64));
  }
});

for(const kind of ['cookie','alarm','saved','native'])
  test('changed '+kind+' during issuance is retained, not overwritten by cookie installation',async()=>{
    const f=fixture();f.after=name=>{if(name!=='issue')return;
      if(kind==='cookie')f.cookie={value:'foreign-cookie'};
      if(kind==='alarm')f.alarm={name:'sdsctl-device-recovery'};
      if(kind==='saved')f.saved[KEY].intent='9'.repeat(64);
      if(kind==='native')f.native.binding.fingerprint='9'.repeat(64);
    };
    await assert.rejects(f.owner.run(),failure);assert.equal(f.issues,1);
    assert(!f.calls.includes('cookie-set'));assert.equal(f.saved[KEY].phase,'initial_pending');
    if(kind==='cookie')assert.deepEqual(f.cookie,{value:'foreign-cookie'});
  });

test('external invalidation before run does no browser I/O',async()=>{
  const f=fixture();f.owner.invalidate();await assert.rejects(f.owner.run(),failure);
  assert.deepEqual(f.calls,[]);
});

test('probe abort/cleanup does not remove a cookie or rewrite uncertain accepted storage',async()=>{
  const f=fixture();f.after=name=>{if(name==='probe-open')f.owner.invalidate();};
  await assert.rejects(f.owner.run(),failure);assert.equal(f.signal.aborted,true);
  assert.equal(f.closed,1);assert.equal(f.cookie.value,token);assert.equal(f.saved[KEY].phase,'initial_pending');
});

const stopFailure={message:'Browser continuation stop is unconfirmed; retain all saved state.'};
function stopFence(f) {
  f.stopTimers??=new Set();
  return createContinuationStopFence(f.chrome,f.config,{wall:()=>f.wall,monotonic:()=>f.mono,
    schedule:(fn,ms)=>{assert.equal(ms,10000);const t={fn};f.stopTimers.add(t);return t;},
    cancel:t=>f.stopTimers.delete(t)});
}
const stopped={mode:'continuation_stopped',browserStopSaved:true,
  nativePauseConfirmed:false,serverRevocationConfirmed:false,sessionReady:false};

test('stop fence is inert until selected and never touches the original recovery record',async()=>{
  const f=fixture(),before=clone(f.saved),fence=stopFence(f);
  assert.deepEqual(f.calls,[]);
  assert.deepEqual(await fence.save(),stopped);
  assert.deepEqual(f.saved[KEY],before[KEY]);
  assert.deepEqual(f.saved[STOP],{version:1,identity:config.identity,epoch:config.epoch,
    build:config.build,stopped:true});
  assert.deepEqual(f.calls,['access','read-stop','write-stop','read-stop']);
  assert(!JSON.stringify(f.saved).includes(token));assert.equal(f.stopTimers.size,0);
  const count=f.calls.length;await assert.rejects(fence.save(),stopFailure);
  assert.equal(f.calls.length,count);
  await assert.rejects(f.make().run(),failure);assert.equal(f.issues,0);
});

for(const name of ['write-initial_pending','issue','cookie-set','write-accepted'])
  test('separate stop survives late '+name+' and blocks a new installer',async()=>{
    const f=fixture(),entered=deferred(),release=deferred();
    f.before=async action=>{if(action===name){entered.resolve();await release.promise;}};
    const running=f.owner.run();await entered.promise;
    // The trusted future owner must do this synchronously BEFORE awaiting save.
    f.owner.invalidate();await assert.rejects(running,failure);
    assert.deepEqual(await stopFence(f).save(),stopped);
    const marker=clone(f.saved[STOP]),count=f.calls.length;
    release.resolve();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(f.calls.length,count);assert.deepEqual(f.saved[STOP],marker);
    assert.equal(f.saved[KEY].phase,name==='write-accepted'?'accepted':'initial_pending');
    const nativeBefore=clone(f.native),cookieBefore=clone(f.cookie),issues=f.issues;
    f.before=null;await assert.rejects(f.make().run(),failure);
    assert.equal(f.issues,issues);assert.deepEqual(f.native,nativeBefore);
    assert.deepEqual(f.cookie,cookieBefore);assert.deepEqual(f.saved[STOP],marker);
  });

for(const when of ['before','after'])for(let cut=0;cut<4;cut++)
  test('stop '+when+' API failure '+cut+' is unconfirmed and never replayed',async()=>{
    const f=fixture(),fence=stopFence(f);f[when]=(_,index)=>{if(index===cut)throw Error('private detail');};
    await assert.rejects(fence.save(),stopFailure);
    const saved=clone(f.saved),count=f.calls.length;f[when]=null;
    await assert.rejects(fence.save(),stopFailure);assert.deepEqual(f.saved,saved);
    assert.equal(f.calls.length,count);assert.equal(f.stopTimers.size,0);assert.equal(f.issues,0);
    if(when==='after'&&cut>=2)assert.equal(f.saved[STOP].stopped,true);
  });

for(const key of ['wall','mono'])for(const change of ['back','expired','invalid'])
  for(let cut=0;cut<4;cut++)test('stop '+key+' '+change+' after API '+cut+' refuses readiness',async()=>{
    const f=fixture(),fence=stopFence(f);
    f.after=(_,index)=>{if(index===cut)f[key]=change==='back'?f[key]-1:
      change==='expired'?f[key]+10000:NaN;};
    await assert.rejects(fence.save(),stopFailure);assert.equal(f.calls.length,cut+1);
    assert.equal(f.issues,0);assert.equal(f.stopTimers.size,0);
  });

for(const method of ['timeout','invalidate'])
  test(method+' does not undo an outstanding stop write or adopt its late completion',async()=>{
    const f=fixture(),fence=stopFence(f),entered=deferred(),release=deferred();
    f.before=async name=>{if(name==='write-stop'){entered.resolve();await release.promise;}};
    const pending=fence.save();await entered.promise;
    if(method==='timeout')for(const t of f.stopTimers)t.fn();else fence.invalidate();
    await assert.rejects(pending,stopFailure);assert.equal(f.saved[STOP],undefined);
    const count=f.calls.length;release.resolve();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(f.saved[STOP].stopped,true);assert.equal(f.calls.length,count);
    await assert.rejects(fence.save(),stopFailure);assert.equal(f.calls.length,count);
    assert.equal(f.stopTimers.size,0);
  });

for(const marker of [null,false,{}, {version:1,identity:config.identity,epoch:config.epoch,
  build:config.build,stopped:true}])test('pre-existing stop is retained without adoption '+JSON.stringify(marker),async()=>{
    const f=fixture();f.saved[STOP]=clone(marker);const saved=clone(f.saved);
    await assert.rejects(stopFence(f).save(),stopFailure);
    assert.deepEqual(f.saved,saved);assert.equal(f.writes.length,0);
    await assert.rejects(f.make().run(),failure);assert.equal(f.issues,0);
  });

for(const mutate of [v=>null,v=>({...v,stopped:false}),v=>({...v,epoch:'e'.repeat(64)}),
  v=>({...v,extra:true})])test('stop write requires exact readback '+mutate.toString(),async()=>{
    const f=fixture();f.after=name=>{if(name==='write-stop')f.saved[STOP]=mutate(f.saved[STOP]);};
    await assert.rejects(stopFence(f).save(),stopFailure);assert.equal(f.writes.length,1);
  });

for(const change of [v=>({...v,build:'invalid'}),v=>({...v,role:'continuation'}),
  v=>({...v,origin:'http://display.example.test'}),v=>({...v,epoch:null})])
  test('invalid stop selection is refused before storage '+change.toString(),()=>{
    const f=fixture();assert.throws(()=>createContinuationStopFence(f.chrome,change(f.config)));
    assert.deepEqual(f.calls,[]);
  });

test('stop invalidation before save is inert; mutation of supplied settings cannot retarget it',async()=>{
  const f=fixture(),fence=stopFence(f);fence.invalidate();
  await assert.rejects(fence.save(),stopFailure);assert.deepEqual(f.calls,[]);
  const next=stopFence(f);f.config.identity='f'.repeat(64);
  assert.deepEqual(await next.save(),stopped);assert.equal(f.saved[STOP].identity,config.identity);
});
