import test from 'node:test';
import assert from 'node:assert/strict';
import {createContinuationNativePause} from '../../src/sds200/browser_assets/browser_device_continuation_native.mjs';

const HOST='org.sdsctl.browser_device',id='a'.repeat(32),build='b'.repeat(64),identity='c'.repeat(64),epoch='d'.repeat(64);
const clone=structuredClone,denied={message:'Native pause is unconfirmed; retain saved state and do not retry.'};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function fixture({origin='https://display.example.test',mode='active',step=1}={}) {
  const f={calls:[],clock:[100000,50000],timers:new Set()};
  f.initial={version:1,ok:true,build,role:'continuation',extensionId:id,acknowledge:false,launch:null,
    config:{origin,identity,nativeHost:HOST},continuation:{epoch,mode,
      binding:{fingerprint:'e'.repeat(64),revision:5,generation:mode==='active'?7:null}}};
  f.current=clone(f.initial);
  f.reply={version:1,ok:true,build,identity,epoch,mode:'paused',
    binding:{fingerprint:'f'.repeat(64),revision:5+step,generation:null},
    nativePauseConfirmed:true,serverRevocationConfirmed:false};
  f.options={wall:()=>f.clock[0],monotonic:()=>f.clock[1],schedule:(fn,ms)=>{
    assert.equal(ms,30000);const timer={fn};f.timers.add(timer);return timer;
  },cancel:timer=>f.timers.delete(timer)};
  const forbidden=new Proxy({}, {get:()=>assert.fail('No browser state, cookie, alarm or probe I/O')});
  f.chrome={storage:forbidden,cookies:forbidden,alarms:forbidden,tabs:forbidden,
    runtime:{id,sendNativeMessage:async(host,request)=>{
      assert.equal(host,HOST);f.calls.push(clone(request));const count=f.calls.length;
      await f.before?.(count,request);
      let value=clone(request.action==='continuation-current'?f.current:f.reply);
      if(request.action==='continuation-pause')f.current.continuation={epoch,mode:'paused',binding:clone(f.reply.binding)};
      value=f.change?f.change(count,value):value;
      await f.after?.(count,value);return value;
    }}};
  f.start=()=>{f.owner=createContinuationNativePause(f.chrome,f.initial,build,f.options);return f.owner;};
  return f;
}

for(const origin of ['https://display.example.test','https://192.0.2.18:8443','https://[2001:db8::18]:8443'])
for(const mode of ['active','paused'])for(const step of [1,2])test('native-only pause '+origin+' '+mode+' '+step,async()=>{
  const f=fixture({origin,mode,step}),p=f.start();assert.equal(f.calls.length,0);assert.equal(f.timers.size,0);
  assert.deepEqual(Object.keys(p).sort(),['invalidate','run']);
  const result=await p.run();assert(Object.isFrozen(result));
  assert.deepEqual(result,{mode:'native_paused',nativePauseConfirmed:true,serverRevocationConfirmed:false,sessionReady:false});
  assert.deepEqual(f.calls,[{version:1,action:'continuation-current'},
    {version:1,action:'continuation-pause',epoch,binding:{fingerprint:'e'.repeat(64),revision:5}},
    {version:1,action:'continuation-current'}]);
  await assert.rejects(p.run(),denied);assert.equal(f.calls.length,3);assert.equal(f.timers.size,0);
});

for(const value of [null,undefined,[],{},true,1,'bad'])test('invalid context '+String(value),()=>{
  const f=fixture();f.initial=value;assert.throws(f.start,denied);assert.equal(f.calls.length,0);
});
for(const [field,value] of [['build','0'.repeat(64)],['role','normal'],['extensionId','p'.repeat(32)],
  ['acknowledge',true],['launch',{}]])test('changed initial '+field,()=>{
  const f=fixture();f.initial[field]=value;assert.throws(f.start,denied);assert.equal(f.calls.length,0);
});
for(const revision of [Number.MAX_SAFE_INTEGER-2,Number.MAX_SAFE_INTEGER-1])test('reserve native revision headroom '+revision,()=>{
  const f=fixture();f.initial.continuation.binding.revision=revision;assert.throws(f.start,denied);
});
test('largest native revision retains correction headroom',async()=>{
  const f=fixture();f.initial.continuation.binding.revision=Number.MAX_SAFE_INTEGER-3;
  f.current=clone(f.initial);f.reply.binding.revision=Number.MAX_SAFE_INTEGER-1;
  await f.start().run();assert.equal(f.calls.length,3);
});
for(const value of [undefined,null,{},true,'pause',{role:'normal'},'PRIVATE'])test('caller payload rejected '+String(value),async()=>{
  const f=fixture(),p=f.start();await assert.rejects(p.run(value),denied);
  await assert.rejects(p.run(),denied);assert.equal(f.calls.length,0);
});

const changes=[['identity',v=>{v.config.identity='0'.repeat(64);}],
  ['origin',v=>{v.config.origin='https://other.example.test';}],['epoch',v=>{v.continuation.epoch='0'.repeat(64);}],
  ['fingerprint',v=>{v.continuation.binding.fingerprint='0'.repeat(64);}],
  ['revision',v=>{v.continuation.binding.revision++;}],['extra',v=>{v.extra='PRIVATE';}],
  ['generation',v=>{v.continuation.binding.generation=99;}],['build',v=>{v.build='0'.repeat(64);}]];
for(const at of [1,3])for(const [name,change] of changes)test('fresh context '+at+' changed '+name,async()=>{
  const f=fixture(),p=f.start();f.change=(count,value)=>{if(count===at)change(value);return value;};
  await assert.rejects(p.run(),denied);assert.equal(f.calls.length,at);
  await assert.rejects(p.run(),denied);assert.equal(f.calls.length,at);assert.equal(f.timers.size,0);
});

const bad=[...['version','ok','build','identity','epoch','mode','binding','nativePauseConfirmed','serverRevocationConfirmed']
  .map(key=>['missing '+key,r=>{delete r[key];return r;}]),
  ...['build','identity','epoch'].map(key=>['wrong '+key,r=>({...r,[key]:'0'.repeat(64)})]),
  ['unconfirmed',r=>({...r,nativePauseConfirmed:false})],['claimed revocation',r=>({...r,serverRevocationConfirmed:true})],
  ['string boolean',r=>({...r,nativePauseConfirmed:'true'})],['unchanged binding',r=>({...r,binding:{...r.binding,fingerprint:'e'.repeat(64)}})],
  ['uppercase fingerprint',r=>({...r,binding:{...r.binding,fingerprint:'F'.repeat(64)}})],
  ['extra binding',r=>({...r,binding:{...r.binding,token:'PRIVATE'}})],
  ['hidden extra',r=>Object.defineProperty(r,'token',{value:'PRIVATE'})],
  ['symbol extra',r=>({...r,[Symbol('private')]:'PRIVATE'})]];
for(const key of ['role','origin','token','session','cookieCleared','browserStopSaved','proof'])
  bad.push(['extra '+key,r=>({...r,[key]:'PRIVATE'})]);
for(const value of [null,[],{},true,1,'PRIVATE'])bad.push(['root '+String(value),()=>value]);
for(const value of [null,true,false,0,-1,5,8,1.5,'6',NaN,Infinity,Number.MAX_SAFE_INTEGER])
  bad.push(['revision '+String(value),r=>({...r,binding:{...r.binding,revision:value}})]);
for(const value of [0,true,7,'null',{},[]])bad.push(['generation '+String(value),r=>({...r,binding:{...r.binding,generation:value}})]);
for(const [name,change] of bad)test('complete pause reply '+name,async()=>{
  const f=fixture(),p=f.start();f.change=(count,value)=>count===2?change(value):value;
  await assert.rejects(p.run(),denied);assert.equal(f.calls.length,2);
  await assert.rejects(p.run(),denied);assert.equal(f.calls.length,2);assert.equal(f.timers.size,0);
});

for(const stage of [1,2,3])for(const kind of ['invalidate','timer','rejection','runtime-id'])
test('late native settlement '+stage+' '+kind,async()=>{
  const f=fixture(),p=f.start(),held=deferred();f.before=count=>count===stage?held.promise:undefined;
  const running=p.run();await settle();assert.equal(f.calls.length,stage);
  if(kind==='invalidate')p.invalidate();
  if(kind==='timer')[...f.timers][0].fn();
  if(kind==='rejection')held.reject(Error('PRIVATE'));
  if(kind==='runtime-id'){f.chrome.runtime.id='p'.repeat(32);held.resolve();}
  await assert.rejects(running,denied);held.resolve();await settle();
  await assert.rejects(p.run(),denied);assert.equal(f.calls.length,stage);assert.equal(f.timers.size,0);
});
for(const clock of [0,1])for(const value of [NaN,Infinity,-1,Number.MAX_SAFE_INTEGER,'1',null])
test('invalid clock '+clock+' '+String(value),async()=>{
  const f=fixture(),p=f.start();f.clock[clock]=value;await assert.rejects(p.run(),denied);assert.equal(f.calls.length,0);
});
for(const stage of [1,2,3])for(const clock of [0,1])for(const elapsed of [-1,30000,40000])
test('whole deadline or backstep '+stage+' '+clock+' '+elapsed,async()=>{
  const f=fixture(),p=f.start();f.after=count=>{if(count===stage)f.clock[clock]+=elapsed;};
  await assert.rejects(p.run(),denied);assert.equal(f.calls.length,stage);assert.equal(f.timers.size,0);
});
test('one deadline includes all three native calls',async()=>{
  const f=fixture(),p=f.start();f.after=()=>{f.clock[0]+=10000;};
  await assert.rejects(p.run(),denied);assert.equal(f.calls.length,3);
});
test('duplicate attempt cannot replace an outstanding native owner',async()=>{
  const f=fixture(),p=f.start(),held=deferred();f.before=count=>count===2?held.promise:undefined;
  const running=p.run();await settle();await assert.rejects(p.run(),denied);
  held.resolve();await running;assert.equal(f.calls.length,3);
});
test('invalidation before first I/O',async()=>{
  const f=fixture(),p=f.start();p.invalidate();await assert.rejects(p.run(),denied);assert.equal(f.calls.length,0);
});
for(const stage of [1,2,3])test('invalidation queued with native reply '+stage,async()=>{
  const f=fixture(),p=f.start();f.after=count=>{if(count===stage)queueMicrotask(p.invalidate);};
  await assert.rejects(p.run(),denied);assert.equal(f.calls.length,stage);
});
test('timer expiry before dispatch',async()=>{
  const f=fixture();f.options.schedule=fn=>{fn();return 1;};await assert.rejects(f.start().run(),denied);
  assert.equal(f.calls.length,0);
});
for(const method of ['wall','monotonic','schedule','cancel'])test('injected '+method+' error sanitized',async()=>{
  const f=fixture();f.options[method]=()=>{throw Error('PRIVATE');};await assert.rejects(f.start().run(),denied);
  assert.equal(f.calls.length,method==='cancel'?3:0);
});
test('cleanup invalidation cannot return confirmed pause',async()=>{
  const f=fixture();f.options.cancel=timer=>{f.timers.delete(timer);f.owner.invalidate();};
  await assert.rejects(f.start().run(),denied);assert.equal(f.calls.length,3);
});
test('input copied at construction, no later origin or binding substitution',async()=>{
  const f=fixture(),p=f.start();f.initial.config.origin='https://other.example.test';
  f.initial.continuation.binding.revision=66;await p.run();assert.equal(f.calls[1].binding.revision,5);
});
