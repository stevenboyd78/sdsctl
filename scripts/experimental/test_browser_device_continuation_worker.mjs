import test from 'node:test';
import assert from 'node:assert/strict';
import {connectContinuationBrowserWorker} from '../../src/sds200/browser_assets/browser_device_continuation_worker.mjs';
import {startBrowserWorker} from '../../src/sds200/browser_assets/browser_device_worker.mjs';

const id='a'.repeat(32),build='b'.repeat(64),identity='c'.repeat(64),epoch='d'.repeat(64);
const KEY='sdsctlDeviceRecovery',HOST='org.sdsctl.browser_device';
const deferred=()=>{let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});
  return {promise,resolve,reject};};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const event=()=>({listeners:[],addListener(fn){this.listeners.push(fn);}});
const selected=(origin='https://192.0.2.18:8443')=>({version:1,ok:true,build,role:'continuation',
  config:{origin,identity,nativeHost:HOST},extensionId:id,acknowledge:false,launch:null,
  continuation:{epoch,mode:'paused',binding:{fingerprint:'e'.repeat(64),revision:5,generation:null}}});
const paused=()=>({version:3,identity,epoch,build,phase:'paused',binding:null,intent:null,cookieFingerprint:null});

function fixture(origin,wrap=false) {
  const f={context:selected(origin),saved:{[KEY]:paused()},cookie:null,alarm:undefined,calls:[],
    clock:[100000,50000],timers:new Map(),nextTimer:1,unexpected:[]};
  const forbidden=name=>()=>{f.unexpected.push(name);throw Error('Forbidden mutation');};
  f.review=()=>({version:1,ok:true,build,identity,epoch,mode:'paused',
    binding:{...f.context.continuation.binding,generation:19}});
  f.send=async request=>{
    if(request.action==='worker-context'||request.action==='continuation-current')return structuredClone(f.context);
    if(request.action==='continuation-review')return f.review();
    throw Error('Unexpected request');
  };
  f.chrome={runtime:{id,getURL:page=>`chrome-extension://${id}/${page}`,
    onMessage:event(),onStartup:event(),onInstalled:event(),sendNativeMessage:async(host,request)=>{
      assert.equal(host,HOST);f.calls.push(structuredClone(request));
      if(wrap&&request.action==='worker-request') {
        assert.equal(request.build,build);return f.send(request.request);
      }
      return f.send(request);
    }},storage:{local:{setAccessLevel:async options=>{
      assert.deepEqual(options,{accessLevel:'TRUSTED_CONTEXTS'});
    },get:async key=>{assert.equal(key,null);return structuredClone(f.saved);},
    set:forbidden('storage.set'),remove:forbidden('storage.remove'),clear:forbidden('storage.clear')}},
    cookies:{get:async key=>{
      assert.deepEqual(key,{url:f.context.config.origin+'/',name:'__Host-sdsctl-device-session'});
      return f.cookie;
    },set:forbidden('cookie.set'),remove:forbidden('cookie.remove')},
    alarms:{onAlarm:event(),get:async name=>{assert.equal(name,'sdsctl-device-recovery');return f.alarm;},
      create:forbidden('alarm.create'),clear:forbidden('alarm.clear')},
    tabs:{onUpdated:event(),query:forbidden('tabs.query'),create:forbidden('tabs.create'),
      update:forbidden('tabs.update'),remove:forbidden('tabs.remove')}};
  f.sender=page=>({id,url:f.chrome.runtime.getURL(page),frameId:0,documentLifecycle:'active',
    documentId:'document-1',tab:{id:7,incognito:false}});
  f.start=()=>connectContinuationBrowserWorker(f.chrome,f.context,build,{
    wall:()=>f.clock[0],monotonic:()=>f.clock[1],
    schedule:(fn,ms)=>{assert.equal(ms,12000);const key=f.nextTimer++;f.timers.set(key,fn);return key;},
    cancel:key=>f.timers.delete(key)});
  f.ask=(action='startup-status',sender=f.sender(action==='resume-review'?'resume.html':'startup.html'),
    extra={})=>new Promise(resolve=>{
      const handler=f.chrome.runtime.onMessage.listeners[0];
      if(handler({action,...extra},sender,resolve)!==true)resolve('unhandled');
    });
  return f;
}

for(const origin of ['https://display.example:8443','https://192.0.2.18:8443','https://[::1]:8443']) {
  for(const legacy of [false,true])test(`read-only paused status and explicit review ${origin} legacy=${legacy}`,async()=>{
    const f=fixture(origin);
    if(legacy)f.saved={[KEY]:{version:1,identity,paused:true,phase:'clean',nextAt:0}};
    const saved=structuredClone(f.saved);f.start();await settle();assert.deepEqual(f.calls,[]);
    assert.deepEqual(await f.ask(),{mode:'paused',sessionReady:false});
    assert.deepEqual(f.calls.map(r=>r.action),['continuation-current','continuation-current']);
    assert.deepEqual(await f.ask('resume-review'),{
      mode:'continuation_reviewed',nativeRevision:5,serverGeneration:19});
    assert.equal(f.calls.filter(r=>r.action==='continuation-review').length,1);
    assert.deepEqual(f.saved,saved);assert.deepEqual(f.unexpected,[]);assert.equal(f.timers.size,0);
  });
}

for(const [name,change] of [
  ['missing',f=>{f.saved={};}],['extra key',f=>{f.saved.other=true;}],
  ['pending',f=>{f.saved[KEY].phase='initial_pending';}],
  ['accepted',f=>{f.saved[KEY].phase='accepted';}],
  ['another build',f=>{f.saved[KEY].build='f'.repeat(64);}],
  ['another epoch',f=>{f.saved[KEY].epoch='f'.repeat(64);}],
  ['another identity',f=>{f.saved[KEY].identity='f'.repeat(64);}],
  ['bound pause',f=>{f.saved[KEY].binding=f.context.continuation.binding;}],
  ['old incomplete pause',f=>{f.saved={[KEY]:{version:1,identity,paused:true,phase:'logout_pending',nextAt:0}};}],
  ['cookie',f=>{f.cookie={value:'foreign'};}],['alarm',f=>{f.alarm={name:'sdsctl-device-recovery'};}],
  ['active native',f=>{f.context.continuation.mode='active';f.context.continuation.binding.generation=19;}],
])test(`uncertain ${name} stays read-only without migration or server review`,async()=>{
  const f=fixture();change(f);f.start();const before=structuredClone(f.saved);
  assert.deepEqual(await f.ask('resume-review'),{mode:'administrator_required'});
  assert.equal(f.calls.filter(r=>r.action==='continuation-review').length,0);
  assert.deepEqual(f.saved,before);assert.deepEqual(f.unexpected,[]);
});

for(const change of [
  {id:'p'.repeat(32)},{frameId:1},{documentLifecycle:'cached'},{documentId:''},
  {documentId:'invalid:document'},{tab:{id:7,incognito:true}},{tab:{id:-1,incognito:false}},
  {url:'https://192.0.2.18:8443/device-display'},
  {url:`chrome-extension://${id}/resume.html?path=other`},
])test(`no reads for foreign document ${JSON.stringify(change)}`,async()=>{
  const f=fixture();f.start();
  assert.equal(await f.ask('resume-review',{...f.sender('resume.html'),...change}),'unhandled');
  assert.deepEqual(f.calls,[]);assert.deepEqual(f.unexpected,[]);
});
for(const action of ['initialize','start','suspend','resume-confirm','authenticate','continuation-renew'])
  test(`no ${action} capability`,async()=>{
    const f=fixture();f.start();
    assert.equal(await f.ask(action,f.sender('resume.html')),'unhandled');assert.deepEqual(f.calls,[]);
  });
for(const extra of [{ticket:'f'.repeat(64)},{origin:'https://elsewhere.invalid'},{generation:19}])
  test(`review refuses supplied fields ${JSON.stringify(extra)}`,async()=>{
    const f=fixture();f.start();assert.equal(await f.ask('resume-review',f.sender('resume.html'),extra),'unhandled');
    assert.deepEqual(f.calls,[]);
  });

for(const fault of ['stored','revision','fingerprint','epoch','cookie','alarm','wall-backstep',
  'mono-backstep','wall-deadline','mono-deadline','generation','proof-extra','lost'])
  test(`changed ${fault} cannot become a successful review`,async()=>{
    const f=fixture(),original=f.review;
    f.review=()=>{
      const result=original();
      if(fault==='stored')f.saved[KEY].intent='f'.repeat(64);
      if(fault==='revision')f.context.continuation.binding.revision++;
      if(fault==='fingerprint')f.context.continuation.binding.fingerprint='f'.repeat(64);
      if(fault==='epoch')f.context.continuation.epoch='f'.repeat(64);
      if(fault==='cookie')f.cookie={value:'foreign'};
      if(fault==='alarm')f.alarm={name:'sdsctl-device-recovery'};
      if(fault.startsWith('wall'))f.clock[0]+=fault.endsWith('backstep')?-1:12000;
      if(fault.startsWith('mono'))f.clock[1]+=fault.endsWith('backstep')?-1:12000;
      if(fault==='generation')result.binding.generation=true;
      if(fault==='proof-extra')result.token='PRIVATE';
      if(fault==='lost')throw Error('PRIVATE response');
      return result;
    };
    f.start();assert.deepEqual(await f.ask('resume-review'),{mode:'administrator_required'});
    const count=f.calls.length;
    assert.deepEqual(await f.ask('resume-review'),{mode:'administrator_required'});
    assert.equal(f.calls.length,count);assert.deepEqual(f.unexpected,[]);
  });

test('timeout retains the undrained lane; late native completion cannot revive it',async()=>{
  const f=fixture(),held=deferred();f.review=()=>held.promise;f.start();
  const result=f.ask('resume-review');await settle();
  assert.equal(f.calls.filter(r=>r.action==='continuation-review').length,1);
  assert.equal(f.timers.size,1);[...f.timers.values()][0]();
  assert.deepEqual(await result,{mode:'administrator_required'});
  const count=f.calls.length;
  assert.deepEqual(await f.ask(),{mode:'administrator_required',sessionReady:false});
  held.resolve({version:1,ok:true,build,identity,epoch,mode:'paused',
    binding:{...f.context.continuation.binding,generation:19}});await settle();
  assert.deepEqual(await f.ask('resume-review'),{mode:'administrator_required'});
  assert.equal(f.calls.length,count);assert.deepEqual(f.unexpected,[]);
});

for(const field of ['cookie','alarm'])for(const value of [false,0,''])
  test(`malformed absent ${field} ${JSON.stringify(value)} is refused`,async()=>{
    const f=fixture();f[field]=value;f.start();
    assert.deepEqual(await f.ask('resume-review'),{mode:'administrator_required'});
    assert.equal(f.calls.filter(r=>r.action==='continuation-review').length,0);
  });

for(const stage of ['access','storage','cookie'])
  test(`late ${stage} result cannot restart a timed-out worker`,async()=>{
    const f=fixture(),held=deferred();
    if(stage==='access')f.chrome.storage.local.setAccessLevel=()=>held.promise;
    if(stage==='storage')f.chrome.storage.local.get=()=>held.promise;
    if(stage==='cookie')f.chrome.cookies.get=()=>held.promise;
    f.start();const pending=f.ask('resume-review');await settle();
    assert.equal(f.timers.size,1);[...f.timers.values()][0]();
    assert.deepEqual(await pending,{mode:'administrator_required'});
    const count=f.calls.length;
    assert.deepEqual(await f.ask(),{mode:'administrator_required',sessionReady:false});
    held.resolve(stage==='storage'?structuredClone(f.saved):null);await settle();
    assert.deepEqual(await f.ask('resume-review'),{mode:'administrator_required'});
    assert.equal(f.calls.length,count);assert.deepEqual(f.unexpected,[]);
  });

test('overlapping explicit review cannot start a second native operation',async()=>{
  const f=fixture(),held=deferred(),reply=f.review();f.review=()=>held.promise;f.start();
  const first=f.ask('resume-review');await settle();
  assert.deepEqual(await f.ask('resume-review'),{mode:'administrator_required'});
  assert.equal(f.calls.filter(r=>r.action==='continuation-review').length,1);
  held.resolve(reply);assert.equal((await first).mode,'continuation_reviewed');
  assert.deepEqual(f.unexpected,[]);
});

test('actual worker gate selects only readonly continuation capabilities and envelopes',async()=>{
  const f=fixture(undefined,true);
  f.chrome.tabs.query=async filter=>{
    assert.deepEqual(filter,{url:[f.chrome.runtime.getURL('startup.html')]});return [];
  };
  await startBrowserWorker(f.chrome,build);
  assert.deepEqual(await f.ask(),{mode:'paused',sessionReady:false});
  assert.equal((await f.ask('resume-review')).mode,'continuation_reviewed');
  for(const action of ['initialize','start','suspend','resume-confirm'])
    assert.deepEqual(await f.ask(action,f.sender('resume.html')),{mode:'setup_error'});
  for(const event of [f.chrome.runtime.onStartup,f.chrome.runtime.onInstalled])event.listeners[0]();
  f.chrome.alarms.onAlarm.listeners[0]({name:'sdsctl-device-recovery'});await settle();
  assert.deepEqual(f.unexpected,[]);
  assert.equal(f.calls[0].action,'worker-context');
  assert(f.calls.slice(1).every(r=>r.action==='worker-request'&&r.build===build&&
    ['continuation-current','continuation-review'].includes(r.request.action)));
});

test('continuation entry is inert until native validation and never opens setup',async()=>{
  const f=fixture(undefined,true),held=deferred(),send=f.send,reloads=[];
  const target={id:7,status:'complete',incognito:false,url:f.chrome.runtime.getURL('startup.html')};
  f.send=request=>request.action==='worker-context'?held.promise:send(request);
  let queries=0;
  f.chrome.tabs.query=async filter=>{
    queries++;assert.deepEqual(filter,{url:[target.url]});return [target];
  };
  f.chrome.tabs.get=async id=>{assert.equal(id,7);return target;};
  f.chrome.runtime.getContexts=async()=>[];
  f.chrome.tabs.reload=async id=>{reloads.push(id);};
  const started=startBrowserWorker(f.chrome,build);
  f.chrome.runtime.onStartup.listeners[0]();
  f.chrome.tabs.onUpdated.listeners[0](7,{status:'complete'},target);
  await settle();assert.equal(queries,0);assert.deepEqual(reloads,[]);
  held.resolve(f.context);await started;await settle();
  assert.deepEqual(reloads,[7]);assert.deepEqual(f.unexpected,[]);
  assert.deepEqual(f.calls.map(r=>r.action),['worker-context']);
});

test('refused continuation context never starts local entry retry',async()=>{
  const f=fixture(undefined,true);f.context.continuation.epoch='invalid';
  await assert.rejects(startBrowserWorker(f.chrome,build));
  f.chrome.runtime.onStartup.listeners[0]();await settle();
  assert.deepEqual(f.unexpected,[]);
});
