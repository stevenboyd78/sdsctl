import test from 'node:test';
import assert from 'node:assert/strict';
import {createContinuationNativePorts} from '../../src/sds200/browser_assets/browser_device_continuation_native.mjs';

const HOST='org.sdsctl.browser_device',id='a'.repeat(32),build='b'.repeat(64),identity='c'.repeat(64),epoch='d'.repeat(64);
const token='sdsctl-browser-session-v1.'+'1'.repeat(64),clone=structuredClone;
const refusal={message:'Browser continuation native response is unconfirmed; retain saved state.'};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
function fixture(origin='https://display.example.test') {
  const f={calls:[],clock:[100000,50000],timers:new Set(),binding:{fingerprint:'e'.repeat(64),revision:5,generation:19}};
  f.initial={version:1,ok:true,build,role:'continuation',extensionId:id,acknowledge:false,launch:null,
    config:{origin,identity,nativeHost:HOST},continuation:{epoch,mode:'paused',binding:{...f.binding,generation:null}}};
  f.request={epoch,intent:'f'.repeat(64),binding:clone(f.binding)};
  f.reply={version:1,ok:true,build,identity,epoch,mode:'active',
    binding:{fingerprint:'2'.repeat(64),revision:7,generation:19},session:{token,expires_in:300}};
  f.current=clone(f.initial);
  f.options={wall:()=>f.clock[0],monotonic:()=>f.clock[1],schedule:(fn,ms)=>{
    assert.equal(ms,12000);const timer={fn,ms};f.timers.add(timer);return timer;
  },cancel:timer=>f.timers.delete(timer)};
  const forbidden=new Proxy({}, {get:()=>assert.fail('adapter must not use browser storage, cookies, alarms or tabs')});
  f.chrome={storage:forbidden,cookies:forbidden,alarms:forbidden,tabs:forbidden,
    runtime:{id,sendNativeMessage:async(host,request)=>{
      assert.equal(host,HOST);assert(!Object.hasOwn(request,'request'));assert(!Object.hasOwn(request,'build'));
      f.calls.push(clone(request));await f.before?.(request);
      const reply=clone(request.action==='continuation-current'?f.current:f.reply);
      await f.after?.(request,reply);return reply;
    }}};
  f.start=()=>{f.ports=createContinuationNativePorts(f.chrome,f.initial,build,f.options);return f.ports;};
  return f;
}

for(const origin of ['https://display.example.test','https://192.0.2.18:8443','https://[2001:db8::18]:8443'])
test('exact scoped inner request, full envelope, independent current read '+origin,async()=>{
  const f=fixture(origin),p=f.start();assert.equal(f.calls.length,0);assert.equal(f.timers.size,0);
  assert.deepEqual(await p.readCurrent(),{identity,...f.initial.continuation});
  const result=await p.issueInitial(f.request);
  assert.deepEqual(result,{binding:f.reply.binding,session:f.reply.session});
  assert(Object.isFrozen(result)&&Object.isFrozen(result.binding)&&Object.isFrozen(result.session));
  assert.deepEqual(f.calls,[{version:1,action:'continuation-current'},
    {version:1,action:'continuation-initial-session',...f.request}]);
  f.current.continuation={epoch,mode:'active',binding:clone(f.reply.binding)};
  assert.deepEqual(await p.readCurrent(),{identity,...f.current.continuation});
  const count=f.calls.length;await assert.rejects(p.issueInitial(f.request),refusal);
  assert.equal(f.calls.length,count);assert.equal(f.timers.size,0);
  assert.deepEqual(Object.keys(p).sort(),['invalidate','issueInitial','readCurrent','settings']);
});

const invalid=[null,undefined,[],{},true,1,'text'];
const mutationFields=[['version',2],['ok',false],['build','0'.repeat(64)],['role','normal'],
  ['extensionId','p'.repeat(32)],['acknowledge',true],['launch',{}]];
for(const [key,value] of mutationFields)test('constructor refuses changed context '+key,()=>{
  const f=fixture();f.initial[key]=value;assert.throws(f.start,refusal);assert.equal(f.calls.length,0);
});
for(const value of invalid)test('constructor refuses malformed context '+String(value),()=>{
  const f=fixture();f.initial=value;assert.throws(f.start,refusal);assert.equal(f.calls.length,0);
});
for(const key of ['origin','identity','nativeHost'])test('constructor refuses changed configuration '+key,()=>{
  const f=fixture();f.initial.config[key]='invalid';assert.throws(f.start,refusal);
});
test('constructor refuses active authority instead of reconstructing an initial session',()=>{
  const f=fixture();f.initial.continuation={epoch,mode:'active',binding:clone(f.reply.binding)};
  assert.throws(f.start,refusal);assert.equal(f.calls.length,0);
});

const badRequests=invalid.map((v,i)=>['root '+i,()=>v]);
for(const key of ['epoch','intent','binding']) {
  badRequests.push(['missing '+key,r=>{delete r[key];return r;}]);
  for(const [i,value] of invalid.entries())badRequests.push([key+' malformed '+i,r=>({...r,[key]:value})]);
}
for(const key of ['fingerprint','revision','generation'])badRequests.push(['binding missing '+key,r=>{delete r.binding[key];return r;}]);
for(const key of ['host','build','role','credential','token','ticket','origin','version','action'])
  badRequests.push(['extra '+key,r=>({...r,[key]:'caller-choice'})]);
for(const key of ['epoch','intent'])badRequests.push([key+' uppercase',r=>({...r,[key]:'A'.repeat(64)})]);
badRequests.push(['epoch changed',r=>({...r,epoch:'0'.repeat(64)})],
  ['fingerprint changed',r=>({...r,binding:{...r.binding,fingerprint:'0'.repeat(64)}})],
  ['binding extra',r=>({...r,binding:{...r.binding,extra:1}})],
  ['hidden extra',r=>Object.defineProperty(r,'extra',{value:1})],
  ['symbol extra',r=>({...r,[Symbol('extra')]:1})]);
for(const key of ['revision','generation'])for(const value of [null,true,false,0,-1,1.5,'5',NaN,Infinity,Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER+1])
  badRequests.push([key+' invalid '+String(value),r=>({...r,binding:{...r.binding,[key]:value}})]);
for(const value of [6,Number.MAX_SAFE_INTEGER-2,Number.MAX_SAFE_INTEGER-1])
  badRequests.push(['revision changed/headroom '+value,r=>({...r,binding:{...r.binding,revision:value}})]);
for(const [name,mutate] of badRequests)test('request '+name+' consumes without dispatch',async()=>{
  const f=fixture(),p=f.start();await assert.rejects(p.issueInitial(mutate(clone(f.request))),refusal);
  await assert.rejects(p.issueInitial(f.request),refusal);await assert.rejects(p.readCurrent(),refusal);
  assert.equal(f.calls.length,0);assert.equal(f.timers.size,0);
});

const badReplies=invalid.map((value,i)=>['root '+i,()=>value]);
for(const key of ['version','ok','build','identity','epoch','mode','binding','session']) {
  badReplies.push(['missing '+key,r=>{delete r[key];return r;}]);
  for(const [i,value] of invalid.entries())if(!((key==='version'&&value===1)||(key==='ok'&&value===true)))
    badReplies.push([key+' malformed '+i,r=>({...r,[key]:value})]);
}
for(const key of ['build','identity','epoch'])badReplies.push([key+' changed',r=>({...r,[key]:'0'.repeat(64)})]);
for(const key of ['token','credential','origin','role','ticket','intent','expires_in','sessionReady'])
  badReplies.push(['extra '+key,r=>({...r,[key]:'unexpected'})]);
for(const [key,value] of [['fingerprint','e'.repeat(64)],['fingerprint','A'.repeat(64)],
  ['revision',8],['revision','7'],['generation',20],['generation','19']])
  badReplies.push(['binding '+key+' '+value,r=>({...r,binding:{...r.binding,[key]:value}})]);
for(const key of ['fingerprint','revision','generation'])badReplies.push(['binding missing '+key,r=>{delete r.binding[key];return r;}]);
for(const key of ['token','expires_in'])badReplies.push(['session missing '+key,r=>{delete r.session[key];return r;}]);
for(const value of [null,0,true,'invalid',token+'x',token.toUpperCase()])
  badReplies.push(['token malformed '+String(value),r=>({...r,session:{...r.session,token:value}})]);
for(const value of [null,true,'300',-1,0,30,3600.1,NaN,Infinity,Number.MAX_SAFE_INTEGER])
  badReplies.push(['lifetime malformed '+String(value),r=>({...r,session:{...r.session,expires_in:value}})]);
for(const field of ['binding','session'])badReplies.push([field+' extra',r=>({...r,[field]:{...r[field],extra:1}})]);
badReplies.push(['hidden extra',r=>Object.defineProperty(r,'extra',{value:1})],
  ['symbol extra',r=>({...r,[Symbol('extra')]:1})]);
for(const [name,mutate] of badReplies)test('complete response '+name+' fails closed',async()=>{
  const f=fixture(),p=f.start();f.chrome.runtime.sendNativeMessage=async()=>{f.calls.push('issue');return mutate(clone(f.reply));};
  await assert.rejects(p.issueInitial(f.request),refusal);await assert.rejects(p.issueInitial(f.request),refusal);
  await assert.rejects(p.readCurrent(),refusal);assert.equal(f.calls.length,1);assert.equal(f.timers.size,0);
});

for(const clock of [0,1])for(const value of [NaN,Infinity,-1,Number.MAX_SAFE_INTEGER,'100',null])
test('invalid clock '+clock+' '+String(value)+' never dispatches',async()=>{
  const f=fixture(),p=f.start();f.clock[clock]=value;
  await assert.rejects(p.issueInitial(f.request),refusal);assert.equal(f.calls.length,0);
});
for(const clock of [0,1])for(const elapsed of [-1,12000,20000])test('clock rollback/deadline '+clock+' '+elapsed,async()=>{
  const f=fixture(),p=f.start();f.after=()=>{f.clock[clock]+=elapsed;};
  await assert.rejects(p.issueInitial(f.request),refusal);assert.equal(f.calls.length,1);assert.equal(f.timers.size,0);
});
for(const pair of [[1000,7500],[7500,1000],[11999,11999]])test('deduct larger whole adapter elapsed '+pair,async()=>{
  const f=fixture(),p=f.start();f.after=()=>{f.clock=f.clock.map((v,i)=>v+pair[i]);};
  const result=await p.issueInitial(f.request);
  assert.equal(result.session.expires_in,300-Math.max(...pair)/1000);
});
test('expiry margin includes adapter elapsed and cannot be revived',async()=>{
  const f=fixture(),p=f.start();f.reply.session.expires_in=31;f.after=()=>{f.clock[0]+=1000;};
  await assert.rejects(p.issueInitial(f.request),refusal);f.clock[0]-=1000;
  await assert.rejects(p.issueInitial(f.request),refusal);assert.equal(f.calls.length,1);
});

for(const method of ['readCurrent','issueInitial'])for(const kind of ['invalidate','timer','reject','runtime-id'])
test(method+' '+kind+' refuses a late native settlement without retry',async()=>{
  const f=fixture(),p=f.start(),held=deferred();f.before=()=>held.promise;
  const run=p[method](f.request);await settle();assert.equal(f.calls.length,1);
  if(kind==='invalidate')p.invalidate();
  if(kind==='timer')[...f.timers][0].fn();
  if(kind==='reject')held.reject(Error('private '+token));
  if(kind==='runtime-id'){f.chrome.runtime.id='p'.repeat(32);held.resolve();}
  await assert.rejects(run,refusal);held.resolve();await settle();
  await assert.rejects(p.issueInitial(f.request),refusal);await assert.rejects(p.readCurrent(),refusal);
  assert.equal(f.calls.length,1);assert.equal(f.timers.size,0);
});
test('invalidation before the first call has no I/O',async()=>{
  const f=fixture(),p=f.start();p.invalidate();await assert.rejects(p.issueInitial(f.request),refusal);
  await assert.rejects(p.readCurrent(),refusal);assert.equal(f.calls.length,0);
});
test('duplicate issuance cannot replace an outstanding owner',async()=>{
  const f=fixture(),p=f.start(),held=deferred();f.before=()=>held.promise;
  const run=p.issueInitial(f.request);await settle();await assert.rejects(p.issueInitial(f.request),refusal);
  await assert.rejects(p.readCurrent(),refusal);held.resolve();assert.deepEqual(await run,{binding:f.reply.binding,session:f.reply.session});
  assert.equal(f.calls.length,1);assert.equal(f.timers.size,0);
});
test('overlap with a read consumes issuance and fences the outstanding read',async()=>{
  const f=fixture(),p=f.start(),held=deferred();f.before=()=>held.promise;
  const run=p.readCurrent();await settle();await assert.rejects(p.issueInitial(f.request),refusal);
  await assert.rejects(run,refusal);held.resolve();await settle();assert.equal(f.calls.length,1);
});
test('copies comparison values before awaiting, never follows caller mutation',async()=>{
  const f=fixture(),p=f.start(),held=deferred();f.before=()=>held.promise;
  const request=clone(f.request),run=p.issueInitial(request);request.intent='0'.repeat(64);request.binding.revision=10;
  f.initial.config.identity='0'.repeat(64);held.resolve();await run;
  assert.deepEqual(f.calls,[{version:1,action:'continuation-initial-session',...f.request}]);
});
test('clock rollback between independent reads remains a failure',async()=>{
  const f=fixture(),p=f.start();await p.readCurrent();f.clock[1]--;
  await assert.rejects(p.readCurrent(),refusal);assert.equal(f.calls.length,1);
});
test('complete context read validates before returning an observation',async()=>{
  const f=fixture(),p=f.start();f.current.extra=token;
  await assert.rejects(p.readCurrent(),refusal);await assert.rejects(p.issueInitial(f.request),refusal);
  assert.equal(f.calls.length,1);
});
for(const key of ['fingerprint','revision','generation'])test('current read refuses changed '+key,async()=>{
  const f=fixture(),p=f.start();f.current.continuation.binding[key]=key==='fingerprint'?'0'.repeat(64):17;
  await assert.rejects(p.readCurrent(),refusal);assert.equal(f.calls.length,1);
});
test('JSON key ordering is not an authority change',async()=>{
  const reverse=value=>value&&typeof value==='object'?Object.fromEntries(Object.entries(value).reverse().map(([k,v])=>[k,reverse(v)])):value;
  const f=fixture();f.initial=reverse(f.initial);f.reply=reverse(f.reply);const p=f.start();
  assert.deepEqual(await p.issueInitial(reverse(f.request)),{binding:f.reply.binding,session:f.reply.session});
});
for(const port of ['wall','monotonic','schedule','cancel'])test('internal '+port+' exception is sanitized',async()=>{
  const f=fixture();f.options[port]=()=>{throw Error('private '+token);};const p=f.start();
  await assert.rejects(p.issueInitial(f.request),refusal);
  assert.equal(f.calls.length,port==='cancel'?1:0);
});
test('synchronously expired timer cannot dispatch',async()=>{
  const f=fixture();f.options.schedule=fn=>{fn();return 1;};const p=f.start();
  await assert.rejects(p.issueInitial(f.request),refusal);assert.equal(f.calls.length,0);
});

test('largest permitted revision and generation match native safe-integer headroom',async()=>{
  const f=fixture();f.initial.continuation.binding.revision=Number.MAX_SAFE_INTEGER-3;
  f.request.binding.revision=Number.MAX_SAFE_INTEGER-3;f.request.binding.generation=Number.MAX_SAFE_INTEGER-1;
  f.reply.binding.revision=Number.MAX_SAFE_INTEGER-1;f.reply.binding.generation=Number.MAX_SAFE_INTEGER-1;
  const p=f.start();assert.deepEqual(await p.issueInitial(f.request),{binding:f.reply.binding,session:f.reply.session});
});
for(const action of ['continuation-current','continuation-initial-session'])
test('invalidation queued with '+action+' settlement prevents any response projection',async()=>{
  const f=fixture(),p=f.start();f.after=()=>queueMicrotask(p.invalidate);
  await assert.rejects(action==='continuation-current'?p.readCurrent():p.issueInitial(f.request),refusal);
  assert.equal(f.calls.length,1);assert.equal(f.timers.size,0);
});
test('current response from a different origin, epoch or build is never projected',async()=>{
  for(const change of [f=>{f.current.config.origin='https://foreign.example.test';},
    f=>{f.current.continuation.epoch='0'.repeat(64);},f=>{f.current.build='0'.repeat(64);}]) {
    const f=fixture(),p=f.start();change(f);await assert.rejects(p.readCurrent(),refusal);
    await assert.rejects(p.issueInitial(f.request),refusal);assert.equal(f.calls.length,1);
  }
});

test('elapsed lifetime includes the final validated return boundary',async()=>{
  const f=fixture();let wall=0,mono=0;
  f.options.wall=()=>100000+wall++*1000;f.options.monotonic=()=>50000+mono++*1000;
  const p=f.start(),result=await p.issueInitial(f.request);
  assert.equal(wall,mono);assert(result.session.expires_in<=300-(wall-1));
});
