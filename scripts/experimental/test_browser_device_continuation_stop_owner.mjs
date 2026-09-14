import test from 'node:test';
import assert from 'node:assert/strict';
import {createContinuationStopOwner} from '../../src/sds200/browser_assets/browser_device_continuation_stop.mjs';
import {fingerprintContinuationCookie} from '../../src/sds200/browser_assets/browser_device_continuation_cookie.mjs';
import {submitDeviceLogout} from '../../src/sds200/browser_assets/browser_device_logout.mjs';
import {createContinuationInstallation} from '../../src/sds200/browser_assets/browser_device_continuation_install.mjs';

// Actual stop fence/native pause/logout parser/install write tracking; modeled
// Chrome, native wire, isolated document and HTTP. NOT physical click evidence,
// a real same-origin fetch, or installed-browser acceptance of this new graph.
const id='a'.repeat(32),build='b'.repeat(64),identity='c'.repeat(64),epoch='d'.repeat(64);
const KEY='sdsctlDeviceRecovery',STOP='sdsctlContinuationStop',COOKIE='__Host-sdsctl-device-session';
const clone=structuredClone,settle=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});
  return {promise,resolve,reject};};
const clean={fenced:true,pendingWrites:0,unconfirmedWrite:false,localWritesDrained:true};
const failure={message:'Browser continuation stop is unconfirmed; retain all saved state.'};

async function fixture(origin='https://192.0.2.18:8443') {
  const f={calls:[],writes:[],clock:[1000000,500000],timers:new Set(),fenced:false,
    drain:clone(clean),serverOutcome:'drained',serverClearsCookie:true,serverCalls:0,pauseCalls:0,
    removed:0,native:{epoch,mode:'active',binding:{fingerprint:'e'.repeat(64),revision:6,generation:7}}};
  f.settings={origin,identity,epoch,build};
  f.cookie={name:COOKIE,value:'sdsctl-browser-session-v1.'+'1'.repeat(64),domain:new URL(origin).hostname,
    hostOnly:true,path:'/',secure:true,httpOnly:true,sameSite:'strict',session:false,
    expirationDate:1300,storeId:'0'};
  f.fingerprint=await fingerprintContinuationCookie(origin,f.cookie);
  f.saved={[KEY]:{version:3,identity,epoch,build,phase:'accepted',binding:clone(f.native.binding),
    intent:'f'.repeat(64),cookieFingerprint:f.fingerprint}};
  f.initial=()=>({version:1,ok:true,build,role:'continuation',
    config:{origin,identity,nativeHost:'org.sdsctl.browser_device'},extensionId:id,acknowledge:false,
    launch:null,continuation:clone(f.native)});
  f.call=async(name,fn)=>{
    const index=f.calls.length;f.calls.push(name);await f.before?.(name,index);
    const value=clone(fn());await f.after?.(name,index);return value;
  };
  const forbidden=()=>assert.fail('unselected authority');
  f.chrome={runtime:{id,sendNativeMessage:(host,request)=>{
    assert.equal(host,'org.sdsctl.browser_device');
    if(request.action==='continuation-current')return f.call('current',()=>{
      assert.deepEqual(request,{version:1,action:'continuation-current'});return f.initial();
    });
    return f.call('pause',()=>{
      assert.equal(request.action,'continuation-pause');
      assert.deepEqual(request,{version:1,action:'continuation-pause',epoch,
        binding:{fingerprint:f.native.binding.fingerprint,revision:f.native.binding.revision}});
      f.pauseCalls++;f.native={epoch,mode:'paused',binding:{fingerprint:'2'.repeat(64),
        revision:f.native.binding.revision+1,generation:null}};
      return {version:1,ok:true,build,identity,...clone(f.native),nativePauseConfirmed:true,
        serverRevocationConfirmed:false};
    });
  }},storage:{local:{setAccessLevel:options=>f.call('access',()=>{
    assert(f.fenced);assert.deepEqual(options,{accessLevel:'TRUSTED_CONTEXTS'});
  }),get:key=>f.call(key===null?'read-all':'read-stop',()=>{
    if(key===null)return f.saved;
    assert.equal(key,STOP);return Object.hasOwn(f.saved,STOP)?{[STOP]:f.saved[STOP]}:{};
  }),set:value=>f.call('write-stop',()=>{
    assert.deepEqual(Object.keys(value),[STOP]);f.saved={...f.saved,...clone(value)};f.writes.push(clone(value));
  }),remove:forbidden,clear:forbidden}},cookies:{get:key=>f.call('cookie',()=>{
    assert.deepEqual(key,{url:origin+'/',name:COOKIE});return f.cookie;
  }),remove:key=>f.call('remove-cookie',()=>{
    assert(f.fenced);assert(f.drain.localWritesDrained);assert(f.serverCalls>0);
    assert.deepEqual(key,{url:origin+'/',name:COOKIE,storeId:'0'});
    f.removed++;f.cookie=null;return key;
  }),set:forbidden},alarms:{getAll:()=>f.call('alarms-all',()=>f.alarms??[]),
    clear:forbidden,create:forbidden},tabs:{create:forbidden,remove:forbidden}};
  f.fetcher=async(url,options)=>{
    assert.equal(url,origin+'/auth/logout');
    assert.equal(options.method,'POST');assert.equal(options.mode,'same-origin');
    assert.equal(options.credentials,'same-origin');assert.equal(options.redirect,'error');
    assert.deepEqual(options.headers,{Accept:'application/json'});
    const outcome=await f.call('logout',()=>{
      f.serverCalls++;if(f.serverClearsCookie)f.cookie=null;return f.serverOutcome;
    });
    const response=new Response(JSON.stringify({version:1,device_logout:true,paused:true,
      drained:outcome==='drained'}),{status:outcome==='drained'?200:outcome==='pending'?202:500,
      headers:{'content-type':'application/json'}});
    Object.defineProperty(response,'url',{value:url});return response;
  };
  f.options={invalidateLanes:()=>{f.calls.push('fence-lanes');f.fenced=true;},
    readWriteDrain:()=>clone(f.drain),cookieFingerprint:f.fingerprint,
    submitLogout:({signal})=>{assert.equal(signal.aborted,false);f.signal=signal;
      return submitDeviceLogout(f.fetcher,origin);},wall:()=>f.clock[0],monotonic:()=>f.clock[1],
    schedule:(fn,ms)=>{assert([100,10000,30000,45000].includes(ms));
      const timer={fn,ms};f.timers.add(timer);return timer;},cancel:timer=>f.timers.delete(timer)};
  f.make=()=>createContinuationStopOwner(f.chrome,f.initial(),build,f.options);
  f.tick=async ms=>{
    const timers=[...f.timers].filter(t=>t.ms===ms);assert(timers.length>0,'expected timer '+ms);
    for(const t of timers){f.timers.delete(t);t.fn();}await settle();
  };
  f.stopRecord=()=>assert.deepEqual(f.saved[STOP],{version:1,identity,epoch,build,stopped:true});
  return f;
}

function expected(mode='complete',server='drained') {
  return {mode:'continuation_stop_'+mode,browserStopSaved:true,nativePauseConfirmed:true,
    localWritesDrained:true,serverRevocation:server,cookieCleared:true,
    serverRevocationConfirmed:true,sessionReady:false};
}

for(const origin of ['https://display.example.test','https://192.0.2.18:8443','https://[2001:db8::18]:8443'])
for(const outcome of ['drained','pending'])test('cold manual stop rechecks accepted ownership '+origin+' '+outcome,async()=>{
  const f=await fixture(origin);f.options.cookieFingerprint=null;f.options.recoverAcceptedCookie=true;
  f.serverOutcome=outcome;const original=clone(f.saved[KEY]);
  const result=await f.make().run();
  assert.deepEqual(result,expected(outcome==='drained'?'complete':'pending',outcome));
  assert.deepEqual(f.saved[KEY],original);f.stopRecord();
  assert.equal(f.serverCalls,1);assert.equal(f.pauseCalls,1);assert.equal(f.cookie,null);
  assert(f.calls.indexOf('read-all')>f.calls.indexOf('write-stop'));
  assert(f.calls.indexOf('read-all')>f.calls.indexOf('pause'));
});

for(const [name,change] of [
  ['missing record',f=>{delete f.saved[KEY];}],['extra record',f=>{f.saved.extra=true;}],
  ['old build',f=>{f.saved[KEY].build='0'.repeat(64);}],
  ['other identity',f=>{f.saved[KEY].identity='0'.repeat(64);}],
  ['other epoch',f=>{f.saved[KEY].epoch='0'.repeat(64);}],
  ['other native binding',f=>{f.saved[KEY].binding.revision++;}],
  ['pending issuance',f=>{f.saved[KEY].phase='initial_pending';f.saved[KEY].cookieFingerprint=null;}],
  ['unknown cookie hash',f=>{f.saved[KEY].cookieFingerprint='0'.repeat(64);}],
  ['missing cookie',f=>{f.cookie=null;}],['replacement cookie',f=>{f.cookie.value='sdsctl-browser-session-v1.'+'0'.repeat(64);}],
  ['expired cookie',f=>{f.clock[0]=1400000;}],['unexpected alarm',f=>{f.alarms=[{name:'unknown'}];}],
  ['pre-existing stop',f=>{f.saved[STOP]={version:1,identity,epoch,build,stopped:true};}],
  ['pause failure',f=>{f.before=name=>{if(name==='pause')throw Error('pause');};}],
  ['stop acknowledgement lost',f=>{f.after=name=>{if(name==='write-stop')throw Error('stop');};}],
  ['whole-storage read lost',f=>{f.before=name=>{if(name==='read-all')throw Error('read');};}],
  ['changed storage after comparison',f=>{let count=0;f.before=name=>{
    if(name==='read-all'&&++count===2)f.saved[KEY].intent='0'.repeat(64);};}],
  ['stop altered after comparison',f=>{f.before=name=>{if(name==='cookie')f.saved[STOP].epoch='0'.repeat(64);};}],
])test('cold manual stop retains uncertain state: '+name,async()=>{
  const f=await fixture();f.options.cookieFingerprint=null;f.options.recoverAcceptedCookie=true;change(f);
  const result=await f.make().run();
  assert.equal(result.mode,'continuation_stop_unconfirmed');
  assert.equal(result.serverRevocation,'unconfirmed');assert.equal(result.cookieCleared,false);
  assert.equal(f.serverCalls,0);assert.equal(f.removed,0);
});

for(const value of [null,1,'yes',{},()=>true])test('cold comparison policy rejects non-boolean '+String(value),async()=>{
  const f=await fixture();f.options.recoverAcceptedCookie=value;assert.throws(f.make,failure);
  assert.deepEqual(f.calls,[]);
});

for(const origin of ['https://display.example.test','https://192.0.2.18:8443','https://[2001:db8::18]:8443'])
  for(const server of ['drained','pending'])for(const clears of [true,false])
    test('one-use stop '+origin+' '+server+' response-clears='+clears,async()=>{
      const f=await fixture(origin);f.serverOutcome=server;f.serverClearsCookie=clears;
      const saved=clone(f.saved[KEY]),owner=f.make();assert.deepEqual(f.calls,[]);
      assert(Object.isFrozen(owner));const running=owner.run();assert.equal(f.calls[0],'fence-lanes');
      assert.deepEqual(await running,expected(server==='drained'?'complete':'pending',server));
      f.stopRecord();assert.deepEqual(f.saved[KEY],saved);assert.equal(f.cookie,null);
      assert.equal(f.removed,clears?0:1);assert.equal(f.serverCalls,1);assert.equal(f.pauseCalls,1);
      assert.equal(f.timers.size,0);const count=f.calls.length;
      await assert.rejects(owner.run(),failure);assert.equal(f.calls.length,count);
    });

test('known server revocation without drain remains pending, never complete',async()=>{
  const f=await fixture();f.serverOutcome='pending';const r=await f.make().run();
  assert.equal(r.mode,'continuation_stop_pending');assert.equal(r.serverRevocationConfirmed,true);
  assert(Object.isFrozen(r));
});

for(const field of ['invalidateLanes','readWriteDrain','submitLogout','wall','monotonic','schedule','cancel'])
  for(const value of [null,1])test('inert constructor rejects invalid '+field+' '+value,async()=>{
    const f=await fixture();f.options[field]=value;assert.throws(f.make,failure);assert.deepEqual(f.calls,[]);
  });
for(const value of ['',1,'A'.repeat(64),'a'.repeat(63),{},undefined])
  test('invalid ownership comparison '+String(value),async()=>{
    const f=await fixture();f.options.cookieFingerprint=value;
    if(value===undefined) {const r=await f.make().run();assert.equal(r.serverRevocation,'unconfirmed');}
    else assert.throws(f.make,failure);
    assert.equal(f.serverCalls,0);assert.equal(f.removed,0);
  });

for(const value of [undefined,{},false,'stop'])test('run accepts no caller payload '+String(value),async()=>{
  const f=await fixture(),owner=f.make(),r=await owner.run(value);
  assert.equal(r.mode,'continuation_stop_unconfirmed');assert.deepEqual(f.calls,['fence-lanes']);
  await assert.rejects(owner.run(),failure);
});

test('pending old write does not block STOP or native pause; no early logout/cleanup',async()=>{
  const f=await fixture();f.drain={...clean,pendingWrites:1,localWritesDrained:false};
  const owner=f.make(),run=owner.run();await settle();f.stopRecord();
  assert.equal(f.native.mode,'paused');assert.equal(f.serverCalls,0);assert.equal(f.removed,0);
  f.saved[KEY].phase='initial_pending'; // Late key update cannot erase STOP.
  f.drain=clone(clean);await f.tick(100);assert.deepEqual(await run,expected());f.stopRecord();
});

test('never-settled write produces fixed unconfirmed result and no cleanup after timeout',async()=>{
  const f=await fixture();f.drain={...clean,pendingWrites:1,localWritesDrained:false};
  const run=f.make().run();await settle();await f.tick(45000);const r=await run;
  assert.equal(r.mode,'continuation_stop_unconfirmed');assert.equal(r.browserStopSaved,true);
  assert.equal(r.nativePauseConfirmed,true);assert.equal(r.localWritesDrained,false);
  assert.equal(f.serverCalls,0);assert.equal(f.removed,0);assert.equal(f.timers.size,0);
  f.drain=clone(clean);await settle();assert.equal(f.serverCalls,0);f.stopRecord();
});

for(const value of [null,{},Promise.resolve(clean),{...clean,extra:true},{...clean,fenced:false},
  {...clean,pendingWrites:-1},{...clean,pendingWrites:0.5},{...clean,pendingWrites:Infinity},
  {...clean,pendingWrites:1},{...clean,localWritesDrained:false},
  {...clean,unconfirmedWrite:true},{...clean,unconfirmedWrite:'false'},
  Object.defineProperty({...clean},'hidden',{value:true}),{...clean,[Symbol('extra')]:true}])
  test('malformed/uncertain drain cannot authorize logout '+String(value?.pendingWrites),async()=>{
    const f=await fixture();f.options.readWriteDrain=()=>value;
    const r=await f.make().run();assert.equal(r.mode,'continuation_stop_unconfirmed');
    assert.equal(r.localWritesDrained,false);assert.equal(f.serverCalls,0);assert.equal(f.removed,0);
    assert.equal(r.browserStopSaved,true);assert.equal(r.nativePauseConfirmed,true);
  });

for(const when of ['before','after'])for(const name of ['access','read-stop','write-stop','current','pause'])
  test(when+' '+name+' failure does not suppress independent logout/stop lanes',async()=>{
    const f=await fixture();f[when]=current=>{if(current===name)throw Error('private failure');};
    const r=await f.make().run();assert.equal(r.mode,'continuation_stop_unconfirmed');
    assert.equal(r.serverRevocation,'drained');assert.equal(r.cookieCleared,true);
    assert.equal(r.browserStopSaved,['current','pause'].includes(name));
    assert.equal(r.nativePauseConfirmed,['access','read-stop','write-stop'].includes(name));
  });

for(const invalidate of [()=>Promise.resolve(),()=>Promise.reject(Error('private')),()=>true,
  ()=>{throw Error('private');}])
  test('no synchronous lane fence means no browser logout/cleanup',async()=>{
    const f=await fixture();f.options.invalidateLanes=()=>{f.fenced=true;return invalidate();};
    const r=await f.make().run();assert.equal(r.mode,'continuation_stop_unconfirmed');
    assert.equal(f.serverCalls,0);assert.equal(f.removed,0);assert.equal(r.localWritesDrained,false);
    f.stopRecord();assert.equal(r.nativePauseConfirmed,true);
  });

for(const cookie of [null,undefined,{name:COOKIE}])test('missing/unrecognized cookie is not owned',async()=>{
  const f=await fixture();f.cookie=cookie;const r=await f.make().run();
  assert.equal(r.mode,'continuation_stop_unconfirmed');assert.equal(f.serverCalls,0);assert.equal(f.removed,0);
});
for(const field of ['value','domain','path','storeId','sameSite'])test('changed cookie '+field+' is not adopted',async()=>{
  const f=await fixture();f.cookie[field]='changed';const r=await f.make().run();
  assert.equal(r.serverRevocation,'unconfirmed');assert.equal(f.serverCalls,0);assert.equal(f.removed,0);
});
for(const seconds of [-1,0,3601])test('unusable selected cookie lifetime '+seconds,async()=>{
  const f=await fixture();f.cookie.expirationDate=f.clock[0]/1000+seconds;
  f.options.cookieFingerprint=await fingerprintContinuationCookie(f.settings.origin,f.cookie);
  const r=await f.make().run();assert.equal(r.mode,'continuation_stop_unconfirmed');assert.equal(f.serverCalls,0);
});

for(const outcome of ['unconfirmed','complete',true,null,{},'pending '])
  test('unrecognized/lost server outcome '+String(outcome)+' never authorizes cleanup',async()=>{
    const f=await fixture();f.options.submitLogout=async()=>outcome;
    const r=await f.make().run();assert.equal(r.serverRevocation,'unconfirmed');
    assert.equal(r.serverRevocationConfirmed,false);assert.equal(r.cookieCleared,false);assert.equal(f.removed,0);
  });

test('server unknown even with cookie absent is not successful sign-out',async()=>{
  const f=await fixture();f.serverOutcome='unconfirmed';const r=await f.make().run();
  assert.equal(f.cookie,null);assert.equal(r.cookieCleared,false);assert.equal(r.serverRevocation,'unconfirmed');
  assert.equal(r.mode,'continuation_stop_unconfirmed');
});

test('replacement cookie after acknowledged logout is retained',async()=>{
  const f=await fixture();f.serverClearsCookie=false;
  f.after=name=>{if(name==='logout')f.cookie.value='sdsctl-browser-session-v1.'+'9'.repeat(64);};
  const r=await f.make().run();assert.equal(r.serverRevocation,'drained');
  assert.equal(r.cookieCleared,false);assert.equal(f.removed,0);assert.equal(r.mode,'continuation_stop_unconfirmed');
});

test('a later non-drained observation forbids cleanup despite server acknowledgement',async()=>{
  const f=await fixture();f.serverClearsCookie=false;
  f.after=name=>{if(name==='logout')f.drain={...clean,pendingWrites:1,localWritesDrained:false};};
  const r=await f.make().run();assert.equal(r.serverRevocation,'drained');
  assert.equal(r.cookieCleared,false);assert.equal(r.localWritesDrained,false);assert.equal(f.removed,0);
});

const baseline=await fixture();baseline.serverClearsCookie=false;assert.deepEqual(await baseline.make().run(),expected());
for(const when of ['before','after'])for(const [cut,name] of baseline.calls.entries()) {
  if(name==='fence-lanes')continue;
  test(when+' invalidation at '+cut+' '+name+' prevents late follow-on work/claims',async()=>{
    const f=await fixture();f.serverClearsCookie=false;
    const entered=deferred(),release=deferred();
    f[when]=async(_name,index)=>{if(index===cut){entered.resolve();await release.promise;}};
    const owner=f.make(),run=owner.run();await entered.promise;owner.invalidate();
    const r=await run;assert.equal(r.mode,'continuation_stop_unconfirmed');const result=clone(r),count=f.calls.length;
    release.resolve();await settle();await settle();assert.equal(f.calls.length,count);assert.deepEqual(r,result);
    await assert.rejects(owner.run(),failure);assert.equal(f.timers.size,0);
    if(f.signal)assert.equal(f.signal.aborted,true);
  });
  test(when+' failed acknowledgement at '+cut+' '+name+' never upgrades to complete',async()=>{
    const f=await fixture();f.serverClearsCookie=false;
    f[when]=(_name,index)=>{if(index===cut)throw Error('private endpoint detail');};
    const owner=f.make(),r=await owner.run();assert.equal(r.mode,'continuation_stop_unconfirmed');
    assert.equal(JSON.stringify(r).includes('private'),false);assert.equal(f.timers.size,0);
    f[when]=null;const count=f.calls.length;await assert.rejects(owner.run(),failure);assert.equal(f.calls.length,count);
  });
}

for(const field of [0,1])for(const value of [-1,NaN,Infinity,Number.MAX_SAFE_INTEGER,45000])
  test('both-clock fault/deadline '+field+' '+value+' refuses final success',async()=>{
    const f=await fixture();f.after=name=>{if(name==='logout')
      f.clock[field]=value===45000?f.clock[field]+45000:value;};
    const r=await f.make().run();assert.equal(r.mode,'continuation_stop_unconfirmed');assert.equal(f.removed,0);
    assert.equal(r.cookieCleared,false);assert.equal(f.timers.size,0);
  });

test('native/STOP independent deadlines do not cancel verified browser logout',async()=>{
  const f=await fixture(),held=deferred(),loggedOut=deferred();
  f.before=name=>['write-stop','pause'].includes(name)?held.promise:undefined;
  f.after=name=>{if(name==='logout')loggedOut.resolve();};
  const run=f.make().run();await loggedOut.promise;assert.equal(f.serverCalls,1);
  await f.tick(10000);await f.tick(30000);const r=await run;
  assert.equal(r.browserStopSaved,false);assert.equal(r.nativePauseConfirmed,false);
  assert.equal(r.serverRevocation,'drained');assert.equal(r.cookieCleared,true);
  const count=f.calls.length;held.resolve();await settle();assert.equal(f.calls.length,count);
});

test('duplicate stop cannot steal or cancel the one outstanding owner',async()=>{
  const f=await fixture(),held=deferred(),entered=deferred();
  f.before=async name=>{if(name==='logout'){entered.resolve();await held.promise;}};
  const owner=f.make(),run=owner.run();await entered.promise;const count=f.calls.length;
  await assert.rejects(owner.run(),failure);assert.equal(f.calls.length,count);
  held.resolve();assert.deepEqual(await run,expected());assert.equal(f.serverCalls,1);
});

test('failed timer cleanup cannot claim complete',async()=>{
  const f=await fixture(),cancel=f.options.cancel;
  f.options.cancel=timer=>{cancel(timer);if(timer?.ms===45000)throw Error('private timer');};
  assert.equal((await f.make().run()).mode,'continuation_stop_unconfirmed');
});

test('failed main timer cancellation still cancels the separate drain poll',async()=>{
  const f=await fixture(),cancel=f.options.cancel;
  f.drain={...clean,pendingWrites:1,localWritesDrained:false};
  f.options.cancel=timer=>{cancel(timer);if(timer?.ms===45000)throw Error('private timer');};
  const owner=f.make(),run=owner.run();await settle();owner.invalidate();
  assert.equal((await run).mode,'continuation_stop_unconfirmed');assert.equal(f.timers.size,0);
  assert.equal(f.serverCalls,0);
});

test('late logout after whole-operation timeout cannot start cookie cleanup or upgrade result',async()=>{
  const f=await fixture(),held=deferred(),entered=deferred();f.serverClearsCookie=false;
  f.before=async name=>{if(name==='logout'){entered.resolve();await held.promise;}};
  const owner=f.make(),run=owner.run();await entered.promise;await f.tick(45000);
  const r=await run;assert.equal(r.serverRevocation,'unconfirmed');assert.equal(r.cookieCleared,false);
  const count=f.calls.length;held.resolve();await settle();await settle();
  assert.equal(f.calls.length,count);assert.equal(f.removed,0);assert.equal(r.serverRevocation,'unconfirmed');
  assert.equal(f.signal.aborted,true);await assert.rejects(owner.run(),failure);
});

test('runtime identity replacement cannot use a late logout acknowledgement',async()=>{
  const f=await fixture();f.after=name=>{if(name==='logout')f.chrome.runtime.id='p'.repeat(32);};
  const r=await f.make().run();assert.equal(r.mode,'continuation_stop_unconfirmed');
  assert.equal(r.serverRevocation,'unconfirmed');assert.equal(f.removed,0);
});

for(const reply of [null,undefined,{},true,{url:'https://wrong.test/',name:COOKIE,storeId:'0'}])
  test('missing/wrong removal acknowledgement is not adopted as cookie-cleared '+String(reply),async()=>{
    const f=await fixture();f.serverClearsCookie=false;
    f.chrome.cookies.remove=async()=>{f.cookie=null;return reply;};
    const r=await f.make().run();assert.equal(r.cookieCleared,false);
    assert.equal(r.serverRevocation,'drained');assert.equal(r.mode,'continuation_stop_unconfirmed');
  });

test('pre-existing STOP marker is retained but not adopted as acknowledgement',async()=>{
  const f=await fixture();f.saved[STOP]={version:1,identity,epoch,build,stopped:true};
  const r=await f.make().run();assert.equal(r.browserStopSaved,false);assert.equal(r.nativePauseConfirmed,true);
  assert.equal(r.serverRevocation,'drained');assert.equal(r.mode,'continuation_stop_unconfirmed');
  assert.equal(f.writes.length,0);f.stopRecord();
});

test('unknown native issuance with no owned-cookie comparison cannot cause reissuance/logout',async()=>{
  const f=await fixture();f.options.cookieFingerprint=null;const r=await f.make().run();
  f.stopRecord();assert.equal(r.nativePauseConfirmed,true);assert.equal(r.localWritesDrained,true);
  assert.equal(r.serverRevocation,'unconfirmed');assert.equal(r.cookieCleared,false);
  assert(!f.calls.includes('cookie'));assert.equal(f.serverCalls,0);assert.equal(f.removed,0);
});

// Exercise real installation write tracking, including a cookie/accepted write
// that COMMITs only after Stop. No Promise.race settlement is treated as a drain.
for(const stage of ['cookie-set','write-accepted'])for(const when of ['before','after'])
  test('actual installer '+when+' late '+stage+' settles before logout/removal',async()=>{
    const f=await fixture();f.cookie=null;
    const active=clone(f.native),review={identity,epoch,mode:'paused',
      binding:{fingerprint:'3'.repeat(64),revision:4,generation:7}};
    f.native={epoch,mode:'paused',binding:{...review.binding,generation:null}};
    f.saved={[KEY]:{version:1,identity,paused:true,phase:'clean',nextAt:0}};
    const get=f.chrome.storage.local.get,set=f.chrome.storage.local.set,access=f.chrome.storage.local.setAccessLevel;
    f.chrome.storage.local.get=key=>key===STOP?get(key):f.call('read-installer',()=>clone(f.saved));
    f.chrome.storage.local.set=values=>Object.hasOwn(values,STOP)?set(values):
      f.call('write-'+values[KEY].phase,()=>{f.saved={...f.saved,...clone(values)};});
    f.chrome.storage.local.setAccessLevel=options=>f.fenced?access(options):f.call('installer-access',()=>{});
    f.chrome.cookies.set=details=>f.call('cookie-set',()=>{
      const {url,...value}=clone(details);f.cookie={...value,domain:new URL(url).hostname,hostOnly:true,session:false};
      return f.cookie;
    });
    f.chrome.alarms.get=()=>f.call('alarm',()=>null);
    const proof={tabId:17,documentId:'probe-1',ticket:'f'.repeat(64)};
    const installer=createContinuationInstallation(f.chrome,f.settings,review,{...f.options,
      readCurrent:()=>f.call('installer-current',()=>({identity,...clone(f.native)})),
      issueInitial:()=>f.call('initial-issue',()=>{f.native=clone(active);
        return {binding:clone(active.binding),session:{token:'sdsctl-browser-session-v1.'+'1'.repeat(64),expires_in:300}};}),
      createProbe:()=>({open:async()=>proof,verify:async()=>({...proof,url:f.settings.origin+'/device-display',
        displayOnly:true,deviceEnrolled:true,remainingSeconds:300}),close:()=>{}})});
    const held=deferred(),entered=deferred();
    f[when]=async name=>{if(name===stage){entered.resolve();await held.promise;}};
    const installing=installer.run();void installing.catch(()=>{});await entered.promise;
    // Known owned cookie comparison from this fixture's issuance, not a token
    // reissued/read back after uncertainty. An actual composing lane must retain it.
    f.options.invalidateLanes=()=>{f.fenced=true;installer.invalidate();};
    f.options.readWriteDrain=installer.writeDrain;
    const stopping=f.make().run();await assert.rejects(installing);await settle();
    f.stopRecord();assert.equal(f.serverCalls,0);assert.equal(f.removed,0);
    assert.equal(installer.writeDrain().pendingWrites,1);
    held.resolve();await settle();assert.equal(installer.writeDrain().localWritesDrained,true);
    await f.tick(100);assert.deepEqual(await stopping,expected());f.stopRecord();
    assert.equal(f.calls.filter(n=>n==='initial-issue').length,1);assert.equal(f.serverCalls,1);
  });
