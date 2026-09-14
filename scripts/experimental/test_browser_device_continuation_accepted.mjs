import test from 'node:test';
import assert from 'node:assert/strict';
import {createContinuationAcceptedStartup} from '../../src/sds200/browser_assets/browser_device_continuation_accepted.mjs';
import {fingerprintContinuationCookie} from '../../src/sds200/browser_assets/browser_device_continuation_cookie.mjs';

// Deterministic contract doubles only; not installed-browser or physical proof.
const KEY='sdsctlDeviceRecovery',STOP='sdsctlContinuationStop',HOST='org.sdsctl.browser_device';
const id='a'.repeat(32),build='b'.repeat(64),identity='c'.repeat(64),epoch='d'.repeat(64);
const binding={fingerprint:'e'.repeat(64),revision:7,generation:19};
const token='sdsctl-browser-session-v1.'+'1'.repeat(64),clone=structuredClone;
const selection={tabId:17,documentId:'test-document',ticket:'f'.repeat(64)};
const refusal={message:'Browser continuation startup is unconfirmed; retain saved state.'};
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const reorder=value=>value!==null&&typeof value==='object'&&!Array.isArray(value)?
  Object.fromEntries(Object.entries(value).reverse().map(([k,v])=>[k,reorder(v)])):value;

async function fixture(origin='https://display.example.test') {
  const f={clock:[1000000,500000],calls:[],timers:new Set(),closed:0,alarm:null};
  f.initial={version:1,ok:true,build,role:'continuation',extensionId:id,acknowledge:false,launch:null,
    config:{origin,identity,nativeHost:HOST},continuation:{epoch,mode:'active',binding:clone(binding)}};
  f.current=clone(f.initial);
  f.verified={version:1,ok:true,build,identity,epoch,mode:'active',binding:clone(binding)};
  f.cookie={name:'__Host-sdsctl-device-session',value:token,domain:new URL(origin).hostname,
    hostOnly:true,path:'/',secure:true,httpOnly:true,sameSite:'strict',session:false,
    expirationDate:1300,storeId:'0'};
  f.saved={[KEY]:{version:3,identity,epoch,build,phase:'accepted',binding:clone(binding),
    intent:'2'.repeat(64),cookieFingerprint:await fingerprintContinuationCookie(origin,f.cookie)}};
  f.selection=clone(selection);
  f.proof={url:origin+'/device-display',...selection,displayOnly:true,deviceEnrolled:true,remainingSeconds:300};
  f.call=async(name,fn)=>{
    const index=f.calls.length;f.calls.push(name);await f.before?.(name,index);
    const result=reorder(clone(fn()));await f.after?.(name,index,result);return result;
  };
  const forbidden=()=>assert.fail('read-only accepted verification must not mutate or issue');
  f.chrome={runtime:{id,sendNativeMessage:(host,request)=>{
    assert.equal(host,HOST);assert.deepEqual(Object.keys(request).sort(),['action','version']);
    assert.equal(request.version,1);
    if(request.action==='continuation-current')return f.call('current',()=>f.current);
    assert.equal(request.action,'continuation-verify-active');return f.call('verify',()=>f.verified);
  }},storage:{local:{
    setAccessLevel:options=>f.call('access',()=>assert.deepEqual(options,{accessLevel:'TRUSTED_CONTEXTS'})),
    get:key=>f.call('storage',()=>{assert.equal(key,null);return f.saved;}),
    set:forbidden,clear:forbidden,remove:forbidden,
  }},cookies:{get:options=>f.call('cookie',()=>{
    assert.deepEqual(options,{url:origin+'/',name:'__Host-sdsctl-device-session'});return f.cookie;
  }),set:forbidden,remove:forbidden},alarms:{get:name=>f.call('alarm',()=>{
    assert.equal(name,'sdsctl-device-recovery');return f.alarm;
  }),create:forbidden,clear:forbidden}};
  f.options={wall:()=>f.clock[0],monotonic:()=>f.clock[1],schedule:(fn,ms)=>{
    assert.equal(ms,45000);const timer={fn};f.timers.add(timer);return timer;
  },cancel:timer=>{f.timers.delete(timer);f.onCancel?.();},createProbe:({signal})=>{
    assert.equal(signal.aborted,false);f.signal=signal;
    return {open:()=>f.call('probe-open',()=>f.selection),verify:()=>f.call('probe-verify',()=>f.proof),
      close:()=>{f.closed++;f.onClose?.();}};
  }};
  f.make=()=>createContinuationAcceptedStartup(f.chrome,f.initial,build,f.options);
  f.owner=f.make();return f;
}

for(const origin of ['https://display.example.test','https://192.0.2.18:8443','https://[2001:db8::18]:8443'])
test('fresh complete read-only accepted verification '+origin,async()=>{
  const f=await fixture(origin),saved=clone(f.saved),cookie=clone(f.cookie);
  assert.deepEqual(f.calls,[]);assert.equal(f.timers.size,0);
  assert.deepEqual(await f.owner.run(),{mode:'accepted',sessionReady:true});
  assert.deepEqual(f.saved,saved);assert.deepEqual(f.cookie,cookie);
  assert.equal(f.calls.filter(n=>n==='verify').length,1);
  assert(f.calls.indexOf('storage')<f.calls.indexOf('current'));
  assert(f.calls.indexOf('verify')<f.calls.indexOf('probe-open'));
  assert.equal(f.calls.at(-1),'storage');assert.equal(f.closed,1);assert.equal(f.timers.size,0);
  const count=f.calls.length;await assert.rejects(f.owner.run(),refusal);assert.equal(f.calls.length,count);
  // A new explicit owner performs a new verification, never cached readiness.
  assert.equal((await f.make().run()).sessionReady,true);
  assert.equal(f.calls.filter(n=>n==='verify').length,2);
});

const changes=[['missing',f=>{f.saved={};}],['extra',f=>{f.saved.extra=true;}],
  ['null',f=>{f.saved[KEY]=null;}],['pending',f=>{f.saved[KEY].phase='initial_pending';f.saved[KEY].cookieFingerprint=null;}],
  ['legacy',f=>{f.saved[KEY]={version:1,identity,paused:true,phase:'clean',nextAt:0};}],
  ['paused',f=>{Object.assign(f.saved[KEY],{phase:'paused',binding:null,intent:null,cookieFingerprint:null});}]];
for(const value of [null,false,0,'',{},[],{stopped:true}])changes.push(['stop '+JSON.stringify(value),f=>{f.saved[STOP]=value;}]);
for(const key of ['identity','epoch','build','intent','cookieFingerprint'])changes.push([key,f=>{f.saved[KEY][key]='0'.repeat(64);}]);
// A new arbitrary intent remains shape-valid but is detected on subsequent
// exact snapshots; identity/epoch/build/fingerprint are independently bound.
const initialChanges=changes.filter(([name])=>name!=='intent');
for(const [name,mutate] of initialChanges)test('unclean initial storage '+name+' never verifies or repairs',async()=>{
  const f=await fixture();mutate(f);const before=clone(f.saved);
  await assert.rejects(f.owner.run(),refusal);assert.deepEqual(f.saved,before);
  assert(!f.calls.includes('verify'));assert.equal(f.closed,0);assert.equal(f.timers.size,0);
});

const baseline=await fixture();await baseline.owner.run();
for(const when of ['before','after'])for(const [cut,name] of baseline.calls.entries())
test(when+' failed acknowledgement '+cut+' '+name+' cannot become ready or retry',async()=>{
  const f=await fixture();f[when]=(_name,index)=>{if(index===cut)throw Error('private '+token);};
  await assert.rejects(f.owner.run(),refusal);const count=f.calls.length;
  f[when]=null;await assert.rejects(f.owner.run(),refusal);
  assert.equal(f.calls.length,count);assert.equal(f.timers.size,0);
  assert.equal(f.saved[KEY].phase,'accepted');assert.equal(f.cookie.value,token);
});

for(const method of ['invalidate','timer'])for(const [cut,name] of baseline.calls.entries())
test(method+' during outstanding '+cut+' '+name+' rejects late settlement',async()=>{
  const f=await fixture(),entered=deferred(),release=deferred();
  f.before=async(_name,index)=>{if(index===cut){entered.resolve();await release.promise;}};
  const run=f.owner.run();await entered.promise;
  if(method==='invalidate')f.owner.invalidate();else [...f.timers][0].fn();
  await assert.rejects(run,refusal);const count=f.calls.length;
  release.resolve();await settle();await assert.rejects(f.owner.run(),refusal);
  assert.equal(f.calls.length,count);assert.equal(f.timers.size,0);
});

for(const boundary of ['verify','probe-open','probe-verify'])
for(const [name,mutate] of [...changes,['cookie',f=>{f.cookie.value=token.slice(0,-1)+'9';}],
  ['cookie-expiry',f=>{f.cookie.expirationDate++;}],['alarm',f=>{f.alarm={name:'unexpected'};}],
  ['native',f=>{f.current.continuation.binding.generation++;}]])
test(name+' changes during '+boundary+' are retained and refused',async()=>{
  const f=await fixture();let saved,cookie;
  f.after=action=>{if(action===boundary){mutate(f);saved=clone(f.saved);cookie=clone(f.cookie);}};
  await assert.rejects(f.owner.run(),refusal);assert.deepEqual(f.saved,saved);assert.deepEqual(f.cookie,cookie);
});

for(const key of ['version','ok','build','identity','epoch','mode','binding'])
test('native verification missing '+key+' fails closed',async()=>{
  const f=await fixture();delete f.verified[key];await assert.rejects(f.owner.run(),refusal);
  assert(!f.calls.includes('probe-open'));
});
for(const [key,value] of [['version',true],['ok',1],['mode','paused'],['build','0'.repeat(64)],
  ['identity','0'.repeat(64)],['epoch','0'.repeat(64)],['extra',true],['session',{token}]])
test('native verification changed/extra '+key+' fails closed',async()=>{
  const f=await fixture();f.verified[key]=value;await assert.rejects(f.owner.run(),refusal);
});
for(const key of ['fingerprint','revision','generation'])test('native binding changed '+key,async()=>{
  const f=await fixture();f.verified.binding[key]=key==='fingerprint'?'0'.repeat(64):20;
  await assert.rejects(f.owner.run(),refusal);
});
for(const key of ['url','tabId','documentId','ticket','displayOnly','deviceEnrolled','remainingSeconds'])
test('protected proof missing '+key+' refuses readiness',async()=>{
  const f=await fixture();delete f.proof[key];await assert.rejects(f.owner.run(),refusal);assert.equal(f.closed,1);
});
for(const [key,value] of [['url','https://other.example/device-display'],['tabId',18],['documentId','replacement'],
  ['ticket','0'.repeat(64)],['displayOnly',false],['deviceEnrolled',false],['extra',true]])
test('protected proof changed '+key,async()=>{
  const f=await fixture();f.proof[key]=value;await assert.rejects(f.owner.run(),refusal);
});
for(const value of [null,true,'300',0,30,NaN,Infinity,3601])test('invalid proof lifetime '+String(value),async()=>{
  const f=await fixture();f.proof.remainingSeconds=value;await assert.rejects(f.owner.run(),refusal);
});
for(const value of [null,{},[],{...selection,tabId:-1},{...selection,documentId:''},{...selection,ticket:''},
  {...selection,extra:true}])test('invalid selected document '+JSON.stringify(value),async()=>{
  const f=await fixture();f.selection=value;await assert.rejects(f.owner.run(),refusal);
  assert(!f.calls.includes('probe-verify'));
});

for(const key of ['name','value','domain','hostOnly','path','secure','httpOnly','sameSite','session','expirationDate','storeId'])
test('cookie missing '+key+' is not usable',async()=>{
  const f=await fixture();delete f.cookie[key];await assert.rejects(f.owner.run(),refusal);
  assert(!f.calls.includes('verify'));
});
for(const remaining of [-1,0,30,3601])test('cookie lifetime '+remaining+' checked independently of matching hash',async()=>{
  const f=await fixture();f.cookie.expirationDate=f.clock[0]/1000+remaining;
  f.saved[KEY].cookieFingerprint=await fingerprintContinuationCookie(f.initial.config.origin,f.cookie);
  await assert.rejects(f.owner.run(),refusal);assert(!f.calls.includes('verify'));
});
for(const clock of [0,1])for(const elapsed of [-1,45000,NaN,Infinity])
test('bad clock '+clock+' '+String(elapsed)+' without timer dispatch',async()=>{
  const f=await fixture();f.after=name=>{if(name==='verify')f.clock[clock]+=elapsed;};
  await assert.rejects(f.owner.run(),refusal);assert.equal(f.calls.at(-1),'verify');
});
for(const clock of [0,1])for(const boundary of ['cancel','close'])
test('final lifetime deducts '+clock+' elapsed through '+boundary,async()=>{
  const f=await fixture();f.proof.remainingSeconds=31;
  f[boundary==='cancel'?'onCancel':'onClose']=()=>{f.clock[clock]+=1000;};
  await assert.rejects(f.owner.run(),refusal);assert.equal(f.timers.size,0);assert.equal(f.closed,1);
});
test('stop during final cleanup prevents readiness',async()=>{
  const f=await fixture();f.onClose=()=>f.owner.invalidate();await assert.rejects(f.owner.run(),refusal);
});
test('timer cleanup failure still closes the owned probe and refuses readiness',async()=>{
  const f=await fixture();f.onCancel=()=>{throw Error('private '+token);};
  await assert.rejects(f.owner.run(),refusal);assert.equal(f.closed,1);assert.equal(f.timers.size,0);
});
test('runtime identity change fences outstanding work',async()=>{
  const f=await fixture();f.after=name=>{if(name==='verify')f.chrome.runtime.id='p'.repeat(32);};
  await assert.rejects(f.owner.run(),refusal);assert.equal(f.calls.at(-1),'verify');
});
test('concurrent/duplicate run does not duplicate requests',async()=>{
  const f=await fixture(),entered=deferred(),release=deferred();
  f.before=async name=>{if(name==='verify'){entered.resolve();await release.promise;}};
  const run=f.owner.run();await entered.promise;await assert.rejects(f.owner.run(),refusal);
  release.resolve();assert.equal((await run).sessionReady,true);
  assert.equal(f.calls.filter(n=>n==='verify').length,1);
});
test('invalidation before start has no browser I/O',async()=>{
  const f=await fixture();f.owner.invalidate();await assert.rejects(f.owner.run(),refusal);assert.deepEqual(f.calls,[]);
});
test('constructor refuses paused selection without I/O',async()=>{
  const f=await fixture();f.initial.continuation.mode='paused';f.initial.continuation.binding.generation=null;
  assert.throws(f.make,refusal);assert.deepEqual(f.calls,[]);
});
test('constructor captures native selection before awaiting',async()=>{
  const f=await fixture();f.initial.config.origin='https://changed.example';
  f.initial.continuation.binding.generation++;assert.equal((await f.owner.run()).sessionReady,true);
});

test('accepted owner retains no cookie comparison from storage alone',async()=>{
  const f=await fixture();assert.equal(f.owner.cookieFingerprint(),null);assert.deepEqual(f.calls,[]);
  await f.owner.run();const retained=f.owner.cookieFingerprint();assert.equal(retained,f.saved[KEY].cookieFingerprint);
  assert.match(retained,/^[a-f0-9]{64}$/);const count=f.calls.length;
  f.owner.invalidate();f.saved[KEY].cookieFingerprint='0'.repeat(64);f.cookie=null;
  assert.equal(f.owner.cookieFingerprint(),retained);assert.equal(f.calls.length,count);
  assert.deepEqual(f.owner.writeDrain(),{fenced:true,pendingWrites:0,unconfirmedWrite:false,localWritesDrained:true});
});

for(const [cut,name] of baseline.calls.entries())test('failed '+cut+' '+name+' never retains accepted ownership',async()=>{
  const f=await fixture();f.after=(_name,index)=>{if(index===cut)throw Error('lost response');};
  await assert.rejects(f.owner.run(),refusal);assert.equal(f.owner.cookieFingerprint(),null);
});

for(const stage of ['access','verify','probe-verify'])test('late '+stage+' cannot populate accepted comparison',async()=>{
  const f=await fixture(),entered=deferred(),held=deferred();
  f.before=async name=>{if(name===stage){entered.resolve();await held.promise;}};
  const run=f.owner.run();await entered.promise;f.owner.invalidate();await assert.rejects(run,refusal);
  assert.equal(f.owner.cookieFingerprint(),null);const count=f.calls.length;
  held.resolve();await settle();assert.equal(f.owner.cookieFingerprint(),null);assert.equal(f.calls.length,count);
});

for(const stage of ['cancel','close'])test('failure in '+stage+' cleanup cannot retain accepted ownership',async()=>{
  const f=await fixture();f[stage==='cancel'?'onCancel':'onClose']=()=>{throw Error('private');};
  await assert.rejects(f.owner.run(),refusal);assert.equal(f.owner.cookieFingerprint(),null);
});

test('accepted access-level write remains pending after enclosing owner rejects',async()=>{
  const f=await fixture(),entered=deferred(),held=deferred();
  assert.deepEqual(f.owner.writeDrain(),{fenced:false,pendingWrites:0,unconfirmedWrite:false,localWritesDrained:false});
  f.before=async name=>{if(name==='access'){entered.resolve();await held.promise;}};
  const run=f.owner.run();await entered.promise;f.owner.invalidate();await assert.rejects(run,refusal);
  assert.deepEqual(f.owner.writeDrain(),{fenced:true,pendingWrites:1,unconfirmedWrite:false,localWritesDrained:false});
  const count=f.calls.length;held.resolve();await settle();
  assert.deepEqual(f.owner.writeDrain(),{fenced:true,pendingWrites:0,unconfirmedWrite:false,localWritesDrained:true});
  assert.equal(f.calls.length,count);assert.equal(f.owner.cookieFingerprint(),null);
});

for(const when of ['before','after'])test('uncertain accepted access-level '+when+' acknowledgement stays sticky',async()=>{
  const f=await fixture();f[when]=name=>{if(name==='access')throw Error('unknown write');};
  await assert.rejects(f.owner.run(),refusal);f[when]=null;f.owner.invalidate();
  assert.deepEqual(f.owner.writeDrain(),{fenced:true,pendingWrites:0,unconfirmedWrite:true,localWritesDrained:false});
  assert.equal(f.owner.cookieFingerprint(),null);assert.equal(f.calls.length,1);
});
