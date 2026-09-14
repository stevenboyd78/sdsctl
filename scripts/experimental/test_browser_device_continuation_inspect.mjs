import test from 'node:test';
import assert from 'node:assert/strict';
import {createContinuationStartupInspection} from '../../src/sds200/browser_assets/browser_device_continuation_inspect.mjs';
import {pausedContinuationRecord} from '../../src/sds200/browser_assets/browser_device_continuation_state.mjs';

// Deterministic read-only contract doubles, NOT browser or physical acceptance.
const KEY='sdsctlDeviceRecovery',STOP='sdsctlContinuationStop',HOST='org.sdsctl.browser_device';
const id='a'.repeat(32),build='b'.repeat(64),identity='c'.repeat(64),epoch='d'.repeat(64);
const clone=structuredClone,refusal={message:'Browser startup inspection is unconfirmed; retain saved state.'};
const unavailable={mode:'administrator_required',sessionReady:false};
const origins=['https://display.example.test','https://192.0.2.18:8443','https://[2001:db8::18]:8443'];
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const sequence=mode=>mode==='paused'?['storage','cookie','alarms','current','storage','cookie','alarms','current']:
  ['storage','alarms','current','storage','alarms','current'];
const result=mode=>({mode:mode==='paused'?'paused':'verification_required',sessionReady:false});

function fixture(mode='paused',origin=origins[0]) {
  const f={clock:[1000000,500000],calls:[],timers:new Set(),cookie:null,alarms:[]};
  const binding={fingerprint:'e'.repeat(64),revision:7,generation:mode==='paused'?null:19};
  f.initial={version:1,ok:true,build,role:'continuation',extensionId:id,acknowledge:false,launch:null,
    config:{origin,identity,nativeHost:HOST},continuation:{epoch,mode,binding:clone(binding)}};
  f.current=clone(f.initial);
  f.saved={[KEY]:mode==='paused'?pausedContinuationRecord({origin,identity,epoch,build}):
    {version:3,identity,epoch,build,phase:'accepted',binding:clone(binding),
      intent:'f'.repeat(64),cookieFingerprint:'1'.repeat(64)}};
  f.call=async(name,fn)=>{
    const index=f.calls.length;f.calls.push(name);await f.before?.(name,index);
    // Keep unusual values intact: Chrome normally returns plain structured-clone
    // data, but the owner must refuse an unknown container before inspecting it.
    const value=fn();await f.after?.(name,index,value);return value;
  };
  const forbidden=()=>{f.calls.push('FORBIDDEN');assert.fail('startup inspection must not mutate, issue, probe or pause');};
  f.chrome={runtime:{id,onMessage:{addListener:forbidden},sendNativeMessage:(host,request)=>{
    assert.equal(host,HOST);assert.deepEqual(request,{version:1,action:'continuation-current'});
    return f.call('current',()=>clone(f.current));
  }},storage:{local:{get:key=>{
    assert.equal(key,null);return f.call('storage',()=>f.saved);
  },set:forbidden,clear:forbidden,remove:forbidden,setAccessLevel:forbidden}},
  cookies:{get:options=>{
    assert.deepEqual(options,{url:origin+'/',name:'__Host-sdsctl-device-session'});
    return f.call('cookie',()=>f.cookie);
  },set:forbidden,remove:forbidden},alarms:{getAll:()=>f.call('alarms',()=>f.alarms),
    create:forbidden,clear:forbidden},tabs:{create:forbidden,update:forbidden},
  scripting:{executeScript:forbidden}};
  f.options={wall:()=>f.clock[0],monotonic:()=>f.clock[1],schedule:(fn,ms)=>{
    assert.equal(ms,12000);const timer={fn};f.timers.add(timer);return timer;
  },cancel:timer=>{f.timers.delete(timer);f.onCancel?.();}};
  f.make=()=>createContinuationStartupInspection(f.chrome,f.initial,build,f.options);
  f.owner=f.make();return f;
}

for(const origin of origins)test('recovery clean pause read-only inspection '+origin,async()=>{
  const f=fixture('paused',origin);
  f.saved={[KEY]:{version:1,identity,paused:true,phase:'clean',nextAt:0}};
  const saved=clone(f.saved),current=clone(f.current);
  assert.deepEqual(await f.owner.run(),result('paused'));
  assert.deepEqual(f.saved,saved);assert.deepEqual(f.current,current);
  assert.deepEqual(f.calls,sequence('paused'));assert.equal(f.timers.size,0);
});

for(const mode of ['paused','active'])for(const origin of origins)
test(mode+' read-only startup selection '+origin,async()=>{
  const f=fixture(mode,origin),saved=clone(f.saved),current=clone(f.current);
  assert.deepEqual(f.calls,[]);assert.equal(f.timers.size,0);assert(Object.isFrozen(f.owner));
  const actual=await f.owner.run();assert.deepEqual(actual,result(mode));assert(Object.isFrozen(actual));
  assert.deepEqual(f.calls,sequence(mode));assert.equal(f.timers.size,0);
  assert.deepEqual(f.saved,saved);assert.deepEqual(f.current,current);
  const count=f.calls.length;await assert.rejects(f.owner.run(),refusal);assert.equal(f.calls.length,count);
});

for(const mode of ['paused','active']) {
  const changes=[['missing',f=>{f.saved={};}],['null',f=>{f.saved=null;}],
    ['array',f=>{f.saved=[];}],['primitive',f=>{f.saved=true;}],
    ['date',f=>{f.saved=new Date(0);}],['map',f=>{f.saved=new Map();}],
    ['extra',f=>{f.saved.extra=true;}],['symbol',f=>{f.saved[Symbol('extra')]=true;}],
    ['hidden',f=>{Object.defineProperty(f.saved,'extra',{value:true});}],
    ['inherited',f=>{f.saved=Object.create(f.saved);}],
    ['accessor',f=>{Object.defineProperty(f.saved,KEY,{get:()=>assert.fail('no accessor reads')});}],
    ['hidden-record',f=>{Object.defineProperty(f.saved,KEY,{enumerable:false});}],
    ['bad-record',f=>{f.saved[KEY]={};}],
    ['pending',f=>{f.saved[KEY]={...f.saved[KEY],phase:'initial_pending',cookieFingerprint:null};}]];
  for(const value of [undefined,null,false,0,'',{},[],{stopped:true}])
    changes.push(['stop '+String(JSON.stringify(value)),f=>{f.saved[STOP]=value;}]);
  changes.push(['stop accessor',f=>{Object.defineProperty(f.saved,STOP,{get:()=>assert.fail('no STOP reads')});}]);
  for(const key of ['identity','epoch','build'])changes.push([key,f=>{f.saved[KEY]={...f.saved[KEY],[key]:'0'.repeat(64)};}]);
  for(const [name,change] of changes)test(mode+' '+name+' is terminal before native or browser work',async()=>{
    const f=fixture(mode);change(f);const retained=f.saved;
    const actual=await f.owner.run();assert.deepEqual(actual,unavailable);assert(Object.isFrozen(actual));
    assert.equal(f.saved,retained);assert.deepEqual(f.calls,['storage']);assert.equal(f.timers.size,0);
  });
}

for(const mode of ['paused','active'])for(const [cut,name] of sequence(mode).entries()) {
  for(const when of ['before','after'])test(mode+' rejected '+when+' '+cut+' '+name+' is fixed and terminal',async()=>{
    const f=fixture(mode),saved=clone(f.saved);
    f[when]=(_name,index)=>{if(index===cut)throw Error('private simulated failure');};
    assert.deepEqual(await f.owner.run(),unavailable);assert.deepEqual(f.saved,saved);
    const count=f.calls.length;await assert.rejects(f.owner.run(),refusal);
    assert.equal(f.calls.length,count);assert.equal(count,cut+1);assert.equal(f.timers.size,0);
  });
  for(const boundary of ['invalidate','timer'])test(mode+' '+boundary+' pending '+cut+' '+name+' fences late settlement',async()=>{
    const f=fixture(mode),entered=deferred(),release=deferred(),saved=clone(f.saved);
    f.before=async(_name,index)=>{if(index===cut){entered.resolve();await release.promise;}};
    const run=f.owner.run();await entered.promise;
    if(boundary==='invalidate')f.owner.invalidate();else [...f.timers][0].fn();
    assert.deepEqual(await run,unavailable);const count=f.calls.length;
    release.resolve();await settle();assert.equal(f.calls.length,count);assert.equal(count,cut+1);
    assert.equal(f.timers.size,0);assert.deepEqual(f.saved,saved);await assert.rejects(f.owner.run(),refusal);
  });
  for(const [label,change] of [
    ['wall backward',f=>f.clock[0]--],['mono backward',f=>f.clock[1]--],
    ['wall expired',f=>f.clock[0]+=12000],['mono expired',f=>f.clock[1]+=12000],
    ['wall nonfinite',f=>{f.clock[0]=NaN;}],['mono nonfinite',f=>{f.clock[1]=Infinity;}],
    ['extension changed',f=>{f.chrome.runtime.id='b'.repeat(32);}],
  ])test(mode+' '+label+' during '+cut+' '+name+' prevents following work',async()=>{
    const f=fixture(mode);f.after=(_name,index)=>{if(index===cut)change(f);};
    assert.deepEqual(await f.owner.run(),unavailable);assert.equal(f.calls.length,cut+1);
    assert.equal(f.timers.size,0);
  });
}

for(const mode of ['paused','active'])for(const side of ['first','second']) {
  for(const [name,change] of [
    ['origin',f=>{f.current.config.origin=origins[1];}],
    ['identity',f=>{f.current.config.identity='0'.repeat(64);}],
    ['epoch',f=>{f.current.continuation.epoch='0'.repeat(64);}],
    ['revision',f=>{f.current.continuation.binding.revision++;}],
    ['fingerprint',f=>{f.current.continuation.binding.fingerprint='0'.repeat(64);}],
    ['build',f=>{f.current.build='0'.repeat(64);}],
    ['role',f=>{f.current.role='normal';}],
    ['unknown response',f=>{f.current=null;}],
  ])test(mode+' '+side+' current '+name+' cannot reselect context',async()=>{
    const f=fixture(mode);let count=0;
    f.before=action=>{if(action==='current'&&++count===(side==='first'?1:2))change(f);};
    assert.deepEqual(await f.owner.run(),unavailable);assert.equal(f.timers.size,0);
  });
  for(const value of [null,{},[{}]])test(mode+' '+side+' alarms '+JSON.stringify(value)+' require empty exact observation',async()=>{
    const f=fixture(mode);let count=0;
    f.before=action=>{if(action==='alarms'&&++count===(side==='first'?1:2))f.alarms=value;};
    assert.deepEqual(await f.owner.run(),unavailable);assert.equal(f.timers.size,0);
  });
}

for(const side of ['first','second'])for(const value of [false,0,'',{}, {name:'present'}])
test(side+' paused cookie '+JSON.stringify(value)+' is not proven absent',async()=>{
  const f=fixture();let count=0;
  f.before=action=>{if(action==='cookie'&&++count===(side==='first'?1:2))f.cookie=value;};
  assert.deepEqual(await f.owner.run(),unavailable);assert.equal(f.timers.size,0);
});

for(const mode of ['paused','active'])for(const [label,change] of [
  ['STOP',f=>{f.saved[STOP]={stopped:true};}],
  ['unknown key',f=>{f.saved.extra=true;}],
  ['unknown record',f=>{f.saved[KEY]={};}],
  ['epoch',f=>{f.saved[KEY]={...f.saved[KEY],epoch:'0'.repeat(64)};}],
])test(mode+' second storage '+label+' remains retained',async()=>{
  const f=fixture(mode);let count=0;
  f.before=action=>{if(action==='storage'&&++count===2)change(f);};
  assert.deepEqual(await f.owner.run(),unavailable);assert.equal(count,2);assert.equal(f.timers.size,0);
  // Initial snapshots must not alias a changed storage object.
  if(label==='STOP')assert.deepEqual(f.saved[STOP],{stopped:true});
});

for(const field of ['intent','cookieFingerprint'])test('second accepted '+field+' shape-valid mutation rejects snapshot drift',async()=>{
  const f=fixture('active');let count=0;
  f.before=action=>{if(action==='storage'&&++count===2)f.saved[KEY][field]='9'.repeat(64);};
  assert.deepEqual(await f.owner.run(),unavailable);assert.equal(f.saved[KEY][field],'9'.repeat(64));
});

test('caller context aliases do not change fixed selection',async()=>{
  const f=fixture();f.initial.config.origin=origins[1];f.initial.continuation.binding.revision++;
  assert.deepEqual(await f.owner.run(),result('paused'));
});
test('cloned null-prototype whole storage is allowed without readiness',async()=>{
  const f=fixture();f.saved=Object.assign(Object.create(null),f.saved);
  assert.deepEqual(await f.owner.run(),result('paused'));
});
test('accepted classification does not read cookie or prove it usable',async()=>{
  const f=fixture('active');f.chrome.cookies.get=()=>assert.fail('accepted owner must verify separately');
  assert.deepEqual(await f.owner.run(),result('active'));
});

for(const [label,change] of [
  ['timer throws',f=>{f.options.schedule=()=>{throw Error('private scheduler');};}],
  ['timer immediately fires',f=>{f.options.schedule=fn=>{fn();return {};};}],
  ['initial clock invalid',f=>{f.clock[0]=-1;}],
])test(label+' starts no read',async()=>{
  const f=fixture();change(f);f.owner=f.make();
  assert.deepEqual(await f.owner.run(),unavailable);assert.deepEqual(f.calls,[]);
});
for(const label of ['throws','invalidates'])test('timer cancellation '+label+' cannot yield success',async()=>{
  const f=fixture();f.onCancel=()=>{if(label==='throws')throw Error('private cancel');f.owner.invalidate();};
  assert.deepEqual(await f.owner.run(),unavailable);assert.equal(f.timers.size,0);
});
test('arguments and pre-start invalidation do no work',async()=>{
  const f=fixture();await assert.rejects(f.owner.run({mode:'active'}),refusal);
  f.owner.invalidate();await assert.rejects(f.owner.run(),refusal);assert.deepEqual(f.calls,[]);
});
test('concurrent runs cannot create two startup inspections',async()=>{
  const f=fixture(),entered=deferred(),release=deferred();
  f.before=async(_name,index)=>{if(index===0){entered.resolve();await release.promise;}};
  const first=f.owner.run();await entered.promise;await assert.rejects(f.owner.run(),refusal);
  release.resolve();assert.deepEqual(await first,result('paused'));assert.deepEqual(f.calls,sequence('paused'));
});
for(const path of ['runtime.sendNativeMessage','storage.local.get','cookies.get','alarms.getAll'])
test('missing '+path+' refuses construction without IO',()=>{
  const f=fixture(),keys=path.split('.'),key=keys.pop();
  let value=f.chrome;for(const part of keys)value=value[part];value[key]=null;
  assert.throws(f.make,refusal);assert.deepEqual(f.calls,[]);
});
for(const key of ['wall','monotonic','schedule','cancel'])test('missing clock function '+key+' refuses construction',()=>{
  const f=fixture();f.options[key]=null;assert.throws(f.make,refusal);assert.deepEqual(f.calls,[]);
});
