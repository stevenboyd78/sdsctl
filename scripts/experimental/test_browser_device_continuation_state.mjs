import test from 'node:test';
import assert from 'node:assert/strict';
import {pausedContinuationRecord,classifyContinuationStartup,createInitialInstallation} from
  '../../src/sds200/browser_assets/browser_device_continuation_state.mjs';

const config={identity:'a'.repeat(64),epoch:'b'.repeat(64),build:'c'.repeat(64),
  origin:'https://192.0.2.18:8443'};
const before={identity:config.identity,epoch:config.epoch,mode:'paused',
  binding:{fingerprint:'d'.repeat(64),revision:4,generation:7}};
const after={...before,mode:'active',binding:{...before.binding,fingerprint:'e'.repeat(64),revision:6}};
const intent='f'.repeat(64),token='sdsctl-browser-session-v1.'+'1'.repeat(64);
// This pure-core fixture models the trusted cookie adapter's digest. The separate
// cookie tests exercise actual Web Crypto and replacement-cookie comparisons.
const cookieFingerprint='3'.repeat(64);
const selectedProbe={tabId:12,documentId:'document-1',ticket:'2'.repeat(64)};
const proof={...selectedProbe,url:config.origin+'/device-display',displayOnly:true,
  deviceEnrolled:true,remainingSeconds:300};
const stopped={mode:'administrator_required',sessionReady:false};
const verifiedLater={mode:'verification_required',sessionReady:false};
const result=()=>({binding:{...after.binding},session:{token,expires_in:300}});
const clone=structuredClone;
const rejects=fn=>assert.throws(fn,{message:'Browser continuation is unconfirmed; retain saved state.'});

function fixture(settings=config) {
  const f={wall:1000000,mono:500000,settings:clone(settings),saved:pausedContinuationRecord(settings)};
  f.before={...clone(before),identity:settings.identity,epoch:settings.epoch};
  f.after={...clone(after),identity:settings.identity,epoch:settings.epoch};
  f.clocks={wall:()=>f.wall,monotonic:()=>f.mono};
  f.make=()=>createInitialInstallation(f.settings,f.saved,f.before,f.clocks);
  f.attempt=f.make();
  f.steps=[
    ()=>{f.saved=f.attempt.pendingRecord(intent);},
    ()=>{f.request=f.attempt.pendingSaved(f.saved);},
    ()=>{
      f.details=f.attempt.sessionReturned(result(),f.after);
      const {url,...details}=f.details;
      f.cookie={...details,domain:new URL(url).hostname,hostOnly:true,session:false};
    },
    ()=>f.attempt.cookieInstalled(f.cookie),
    ()=>f.attempt.probeStarted(selectedProbe),
    ()=>{f.saved=f.attempt.protectedPageVerified({...proof,url:settings.origin+'/device-display'},f.cookie,f.after,cookieFingerprint);},
    ()=>{f.ready=f.attempt.acceptedSaved(f.saved,f.after,f.cookie,cookieFingerprint);},
  ];
  f.advance=n=>{for(let i=0;i<n;i++)f.steps[i]();return f;};
  return f;
}

test('inert exact ordering: pending precedes issuance; acceptance follows cookie/page/native checks',()=>{
  const f=fixture();assert.deepEqual(classifyContinuationStartup(config,f.saved,before),
    {mode:'paused',sessionReady:false});
  f.steps[0]();assert.equal(f.saved.phase,'initial_pending');
  assert.deepEqual(classifyContinuationStartup(config,f.saved,after),stopped);
  f.steps[1]();assert.deepEqual(f.request,{epoch:config.epoch,intent,binding:before.binding});
  for(let i=2;i<5;i++)f.steps[i]();
  assert.equal(f.saved.phase,'initial_pending');
  f.steps[5]();assert.equal(f.saved.phase,'accepted');
  // A durable clean commit can survive loss of its acknowledgement. It still
  // means only "fresh verification required", never a reusable browser session.
  assert.deepEqual(classifyContinuationStartup(config,f.saved,after),verifiedLater);
  f.steps[6]();assert.deepEqual(f.ready,{mode:'accepted',sessionReady:true});
  assert(!JSON.stringify(f.saved).includes(token));
  assert(!JSON.stringify(f.request).includes(token));
  assert.equal(f.attempt.token,undefined);assert.equal(f.attempt.session,undefined);
  rejects(()=>f.attempt.acceptedSaved(f.saved,after));
});

for(const origin of ['https://display.example.test','https://192.0.2.18:8443','https://[2001:db8::18]:8443']) {
  test('pure origin/cookie contract supports canonical selection '+origin,()=>{
    const f=fixture({...config,origin}).advance(7);
    assert(f.ready.sessionReady);
    assert.equal(f.details.url,origin+'/');
  });
}

for(let cut=0;cut<=7;cut++)test('restart cut '+cut+' never treats pending or native ACTIVE as installed',()=>{
  const f=fixture().advance(cut);
  const observed=cut<3?before:after;
  const expected=cut===0?{mode:'paused',sessionReady:false}:cut<6?stopped:verifiedLater;
  assert.deepEqual(classifyContinuationStartup(config,clone(f.saved),observed),expected);
  if(cut>0)rejects(()=>f.make()); // No new initial attempt adopts pending/accepted state.
});

for(let cut=0;cut<7;cut++) {
  test('newer cancellation latches at boundary '+cut,()=>{
    const f=fixture().advance(cut),saved=clone(f.saved);
    f.attempt.invalidate();rejects(f.steps[cut]);
    assert.deepEqual(f.saved,saved);rejects(()=>f.attempt.pendingRecord(intent));
  });
  for(const change of ['wall-back','mono-back','wall-expired','mono-expired','wall-nan','mono-inf']) {
    test(change+' at boundary '+cut+' refuses without changing saved state',()=>{
      const f=fixture().advance(cut),saved=clone(f.saved);
      if(change==='wall-back')f.wall--;
      if(change==='mono-back')f.mono--;
      if(change==='wall-expired')f.wall+=45000;
      if(change==='mono-expired')f.mono+=45000;
      if(change==='wall-nan')f.wall=NaN;
      if(change==='mono-inf')f.mono=Infinity;
      rejects(f.steps[cut]);assert.deepEqual(f.saved,saved);
      f.wall=1000000;f.mono=500000;rejects(f.steps[cut]);
    });
  }
}

for(let cut=0;cut<7;cut++)for(let invoked=0;invoked<7;invoked++) {
  if(cut===invoked)continue;
  test('out-of-order '+invoked+' at '+cut+' consumes the in-memory attempt',()=>{
    const f=fixture().advance(cut),saved=clone(f.saved);
    rejects(f.steps[invoked]);rejects(f.steps[cut]);assert.deepEqual(f.saved,saved);
  });
}

test('elapsed browser time is subtracted conservatively, with no per-phase renewal',()=>{
  const f=fixture().advance(2);f.wall+=9000;f.mono+=10000;
  f.steps[2]();assert.equal(f.details.expirationDate,1300);
  f.steps[3]();f.steps[4]();f.wall+=10000;f.mono+=34999;
  f.steps[5]();f.mono++;rejects(f.steps[6]);
});

test('strict minimum lifetime counts monotonic time even when wall advances less',()=>{
  const f=fixture().advance(2);f.mono+=1000;
  const short=result();short.session.expires_in=31;
  rejects(()=>f.attempt.sessionReturned(short,after));
});

test('browser expiry rounding down is allowed, extension or later replacement is not',()=>{
  const f=fixture().advance(3);f.cookie.expirationDate-=0.5;
  f.steps[3]();f.steps[4]();f.cookie.expirationDate-=0.5;
  rejects(f.steps[5]);
  const g=fixture().advance(3);g.cookie.expirationDate-=0.5;
  for(let i=3;i<7;i++)g.steps[i]();assert(g.ready.sessionReady);
});

test('shortened cookie life is still checked after the protected-page and storage acknowledgements',()=>{
  const f=fixture().advance(3);f.cookie.expirationDate=1031;
  f.steps[3]();f.steps[4]();f.steps[5]();f.mono+=1000;
  rejects(f.steps[6]);
  // The core never rewrites a possibly committed accepted record. On restart
  // even exact accepted state requires new server/browser verification.
  assert.deepEqual(classifyContinuationStartup(config,f.saved,after),verifiedLater);
});

const stateChanges=[
  s=>null,s=>[],s=>({...s,version:1}),s=>({...s,version:'3'}),s=>({...s,extra:true}),
  s=>({...s,identity:'9'.repeat(64)}),s=>({...s,epoch:'9'.repeat(64)}),
  s=>({...s,build:'9'.repeat(64)}),s=>({...s,phase:'clean'}),
  s=>({...s,intent:null}),s=>({...s,intent:'bad'}),s=>({...s,binding:null}),
  s=>({...s,binding:{...s.binding,revision:true}}),s=>({...s,binding:{...s.binding,generation:0}}),
  s=>({...s,binding:{...s.binding,fingerprint:'bad'}}),
  s=>({...s,binding:{...s.binding,extra:true}}),
];
for(const [index,change] of stateChanges.entries()) {
  test('corrupt pending/accepted record '+index+' is not repaired or accepted',()=>{
    for(const cut of [1,6]) {
      const f=fixture().advance(cut),bad=change(clone(f.saved));
      assert.deepEqual(classifyContinuationStartup(config,bad,after),stopped);
      rejects(()=>createInitialInstallation(config,bad,before,f.clocks));
      rejects(()=>cut===1?f.attempt.pendingSaved(bad):
        f.attempt.acceptedSaved(bad,after,f.cookie,cookieFingerprint));
    }
  });
}

const nativeChanges=[v=>null,v=>({...v,extra:true}),v=>({...v,mode:'paused'}),
  v=>({...v,identity:'9'.repeat(64)}),v=>({...v,epoch:'9'.repeat(64)}),
  v=>({...v,binding:{...v.binding,revision:v.binding.revision+1}}),
  v=>({...v,binding:{...v.binding,generation:v.binding.generation+1}}),
  v=>({...v,binding:{...v.binding,fingerprint:'9'.repeat(64)}}),
];
for(const [index,change] of nativeChanges.entries())for(const cut of [2,5,6]) {
  test('native mismatch '+index+' at '+cut+' cannot complete browser acceptance',()=>{
    const f=fixture().advance(cut),bad=change(clone(after));
    rejects(()=>cut===2?f.attempt.sessionReturned(result(),bad):cut===5?
      f.attempt.protectedPageVerified(proof,f.cookie,bad,cookieFingerprint):
      f.attempt.acceptedSaved(f.saved,bad,f.cookie,cookieFingerprint));
    if(cut===6)assert.deepEqual(classifyContinuationStartup(config,f.saved,bad),stopped);
  });
}

const resultChanges=[v=>null,v=>({...v,extra:token}),v=>({...v,binding:null}),
  v=>({...v,binding:{...v.binding,revision:7}}),v=>({...v,binding:{...v.binding,generation:8}}),
  v=>({...v,binding:{...v.binding,fingerprint:before.binding.fingerprint}}),v=>({...v,session:null}),
  v=>({...v,session:{...v.session,token:'private-invalid'}}),
  v=>({...v,session:{...v.session,extra:true}}),
  ...[null,true,'300',NaN,Infinity,0,30,3601].map(expires_in=>v=>({...v,session:{...v.session,expires_in}})),
];
for(const [index,change] of resultChanges.entries())test('malformed issued session '+index+' remains pending',()=>{
  const f=fixture().advance(2),saved=clone(f.saved);
  rejects(()=>f.attempt.sessionReturned(change(result()),after));
  assert.deepEqual(f.saved,saved);rejects(()=>f.attempt.sessionReturned(result(),after));
});

const cookieChanges=[v=>null,v=>({...v,extra:true}),v=>({...v,name:'other'}),
  v=>({...v,value:'sdsctl-browser-session-v1.'+'9'.repeat(64)}),v=>({...v,domain:'evil.test'}),
  v=>({...v,hostOnly:false}),v=>({...v,path:'/device-display'}),v=>({...v,secure:false}),
  v=>({...v,httpOnly:false}),v=>({...v,sameSite:'lax'}),v=>({...v,session:true}),
  v=>({...v,storeId:'1'}),v=>({...v,partitionKey:{topLevelSite:config.origin}}),
  v=>({...v,expirationDate:v.expirationDate+1}),v=>({...v,expirationDate:1030}),
  v=>({...v,expirationDate:NaN}),v=>({...v,expirationDate:'1300'}),
];
for(const [index,change] of cookieChanges.entries())for(const cut of [3,5,6]) {
  test('wrong cookie '+index+' at '+cut+' never reports readiness',()=>{
    const f=fixture().advance(cut),bad=change(clone(f.cookie));
    rejects(()=>cut===3?f.attempt.cookieInstalled(bad):cut===5?
      f.attempt.protectedPageVerified(proof,bad,after,cookieFingerprint):
      f.attempt.acceptedSaved(f.saved,after,bad,cookieFingerprint));
    assert.equal(f.saved.phase,cut===6?'accepted':'initial_pending');
    assert.equal(classifyContinuationStartup(config,f.saved,after).sessionReady,false);
  });
}

const proofChanges=[v=>null,v=>({...v,extra:true}),v=>({...v,url:'https://evil.test/device-display'}),
  v=>({...v,url:config.origin+'/device-display?extra'}),v=>({...v,tabId:13}),
  v=>({...v,documentId:'document-2'}),v=>({...v,ticket:'9'.repeat(64)}),
  v=>({...v,displayOnly:false}),v=>({...v,deviceEnrolled:false}),
  ...[null,true,'300',NaN,Infinity,0,30,3601].map(remainingSeconds=>v=>({...v,remainingSeconds})),
];
for(const [index,change] of proofChanges.entries())test('protected-page mismatch '+index+' stays pending',()=>{
  const f=fixture().advance(5);
  rejects(()=>f.attempt.protectedPageVerified(change(clone(proof)),f.cookie,after,cookieFingerprint));
  assert.equal(f.saved.phase,'initial_pending');
});

for(const change of [p=>null,p=>({...p,extra:true}),p=>({...p,tabId:-1}),p=>({...p,tabId:true}),
  p=>({...p,documentId:''}),p=>({...p,documentId:'x'.repeat(129)}),p=>({...p,ticket:'bad'})]) {
  test('invalid probe selection is not a protected-page acknowledgement',()=>{
    const f=fixture().advance(4);rejects(()=>f.attempt.probeStarted(change(clone(selectedProbe))));
    rejects(()=>f.attempt.protectedPageVerified(proof,f.cookie,after,cookieFingerprint));
  });
}

test('snapshots are immutable and later caller mutation cannot change the in-memory comparison',()=>{
  const f=fixture(),original=clone(f.before);
  f.before.binding.generation=99;f.settings.epoch='9'.repeat(64);
  f.steps[0]();f.steps[1]();assert.deepEqual(f.request.binding,original.binding);
  assert(Object.isFrozen(f.saved)&&Object.isFrozen(f.saved.binding)&&Object.isFrozen(f.request));
  f.steps[2]();f.steps[3]();f.steps[4]();f.steps[5]();
  assert(Object.isFrozen(f.saved)&&Object.isFrozen(f.saved.binding));
  assert.throws(()=>{f.saved.binding.revision=999;},TypeError);
});

for(const field of ['identity','epoch','build','origin'])for(const value of [null,true,'',token]) {
  test('invalid fixed selection '+field+' '+String(value).slice(0,8)+' is redacted',()=>{
    const settings={...config,[field]:value};
    rejects(()=>pausedContinuationRecord(settings));
    assert.deepEqual(classifyContinuationStartup(settings,pausedContinuationRecord(config),before),stopped);
  });
}
for(const origin of ['http://192.0.2.18','https://user:pass@example.test','https://example.test/',
  'https://example.test/path','https://example.test?x','https://example.test#x','https://EXAMPLE.test']) {
  test('noncanonical origin refused '+origin,()=>rejects(()=>pausedContinuationRecord({...config,origin})));
}
for(const clock of ['wall','monotonic'])test('clock exceptions never expose a private adapter message '+clock,()=>{
  const f=fixture();f.clocks[clock]=()=>{throw Error(token);};
  rejects(()=>f.make());rejects(()=>f.attempt.pendingRecord(intent));
});

test('ordinary recovery schema is never upgraded or initialized by classification',()=>{
  const saved={version:1,identity:config.identity,paused:true,phase:'clean',nextAt:0};
  const beforeBytes=JSON.stringify(saved);
  assert.deepEqual(classifyContinuationStartup(config,saved,before),stopped);
  assert.equal(JSON.stringify(saved),beforeBytes);
  const f=fixture();rejects(()=>createInitialInstallation(config,saved,before,f.clocks));
});

test('clean paused status works offline with no reviewed server generation',()=>{
  const f=fixture(),offline={...before,binding:{...before.binding,generation:null}};
  assert.deepEqual(classifyContinuationStartup(config,f.saved,offline),{mode:'paused',sessionReady:false});
  // Read-only offline status is not fresh review or consent to issue a session.
  rejects(()=>createInitialInstallation(config,f.saved,offline,f.clocks));
});

test('unknown generation never qualifies accepted state or an initial session',()=>{
  const f=fixture().advance(2),unknown=result();unknown.binding.generation=null;
  rejects(()=>f.attempt.sessionReturned(unknown,after));
  const g=fixture().advance(6),observed={...after,binding:{...after.binding,generation:null}};
  assert.deepEqual(classifyContinuationStartup(config,g.saved,observed),stopped);
  const saved={...g.saved,binding:{...g.saved.binding,generation:null}};
  assert.deepEqual(classifyContinuationStartup(config,saved,after),stopped);
});

for(const generation of [false,0,'7',undefined,NaN,Infinity])test('offline status refuses malformed generation '+String(generation),()=>{
  const f=fixture(),observed={...before,binding:{...before.binding,generation}};
  assert.deepEqual(classifyContinuationStartup(config,f.saved,observed),stopped);
});

for(const delta of [-1,0,1,3,4])test('initial completion requires exactly two native revision advances: '+delta,()=>{
  const f=fixture().advance(2),changed=clone(after);
  changed.binding.revision=before.binding.revision+delta;
  // Mutate BOTH observations so their equality cannot mask the revision guard.
  const issued={...result(),binding:changed.binding};
  rejects(()=>f.attempt.sessionReturned(issued,changed));
  assert.equal(f.saved.phase,'initial_pending');
});

for(const value of [undefined,null,true,'',token,'z'.repeat(64)])
test('unbound cookie cannot produce an accepted record '+String(value).slice(0,8),()=>{
  const f=fixture().advance(5),saved=clone(f.saved);
  rejects(()=>f.attempt.protectedPageVerified(proof,f.cookie,after,value));
  assert.deepEqual(f.saved,saved);rejects(f.steps[5]);
});
for(const value of [undefined,null,true,'','9'.repeat(64)])
test('final cookie digest must still match installed acceptance '+String(value).slice(0,8),()=>{
  const f=fixture().advance(6),saved=clone(f.saved);
  rejects(()=>f.attempt.acceptedSaved(f.saved,after,f.cookie,value));
  assert.deepEqual(f.saved,saved);rejects(f.steps[6]);
});
for(const cut of [0,1,6])test('cookie binding has an exact phase-specific schema '+cut,()=>{
  const f=fixture().advance(cut),missing=clone(f.saved);
  delete missing.cookieFingerprint;
  for(const bad of [missing,{...f.saved,cookieFingerprint:cut===6?null:cookieFingerprint}])
    assert.deepEqual(classifyContinuationStartup(config,bad,cut===6?after:before),stopped);
});
