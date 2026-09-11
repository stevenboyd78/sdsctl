import test from 'node:test';
import assert from 'node:assert/strict';
import {connectContinuationOperationWorker} from '../../src/sds200/browser_assets/browser_device_continuation_consent.mjs';
import {createWorkerEventGate} from '../../src/sds200/browser_assets/browser_device_worker_gate.mjs';
import {classifyContinuationStartup} from '../../src/sds200/browser_assets/browser_device_continuation_state.mjs';
import {createContinuationNativePorts} from '../../src/sds200/browser_assets/browser_device_continuation_native.mjs';

// Actual event gate, document consent, native-response adapter, installation and
// independent stop adapter. Chrome/native/probe boundaries are fictional. No
// installed native host, TLS, revocation or physical browser acceptance here.
const id='a'.repeat(32),build='b'.repeat(64),identity='c'.repeat(64),epoch='d'.repeat(64);
const extension=`chrome-extension://${id}`,url=extension+'/resume.html';
const KEY='sdsctlDeviceRecovery',STOP='sdsctlContinuationStop';
const clone=structuredClone,settle=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const refused={mode:'administrator_required'},accepted={mode:'accepted',sessionReady:true};
const stopped={mode:'continuation_stopped',browserStopSaved:true,nativePauseConfirmed:false,
  serverRevocationConfirmed:false,sessionReady:false};
const sender=()=>({id,origin:extension,url,frameId:0,documentLifecycle:'active',
  documentId:'document-1',tab:{id:7,incognito:false}});
const token='sdsctl-browser-session-v1.'+'1'.repeat(64);
const selection={tabId:17,documentId:'probe-1',ticket:'f'.repeat(64)};

function fixture(origin='https://192.0.2.18:8443',legacy=false) {
  const f={calls:[],writes:[],issues:0,closed:0,events:{},timers:new Set(),clock:[100000,50000],
    cookie:null,alarm:null};
  f.settings={origin,identity,epoch,build};
  f.initial={version:1,ok:true,build,role:'continuation',config:{origin,identity,nativeHost:'org.sdsctl.browser_device'},
    extensionId:id,acknowledge:false,launch:null,
    continuation:{epoch,mode:'paused',binding:{fingerprint:'e'.repeat(64),revision:5,generation:null}}};
  f.review={identity,epoch,mode:'paused',binding:{...f.initial.continuation.binding,generation:19}};
  f.native={...clone(f.review),binding:{...f.review.binding,generation:null}};
  f.active={...clone(f.review),mode:'active',binding:{fingerprint:'2'.repeat(64),revision:7,generation:19}};
  f.saved={[KEY]:legacy?{version:1,identity,paused:true,phase:'clean',nextAt:0}:
    {version:3,identity,epoch,build,phase:'paused',binding:null,intent:null,cookieFingerprint:null}};
  f.rows=[{contextId:'context-1',contextType:'TAB',documentId:'document-1',documentOrigin:extension,
    documentUrl:url,frameId:0,tabId:7,windowId:1,incognito:false}];
  f.tab={id:7,incognito:false,status:'complete',url};
  f.call=async(name,run)=>{
    const index=f.calls.length;f.calls.push(name);await f.before?.(name,index);
    const value=clone(run());await f.after?.(name,index);return value;
  };
  const event=name=>({addListener:fn=>{assert(!f.events[name]);f.events[name]=fn;}});
  const forbidden=name=>()=>assert.fail('forbidden '+name);
  f.chrome={runtime:{id,getURL:page=>extension+'/'+page,onMessage:event('message'),onStartup:event('startup'),
    onInstalled:event('installed'),getContexts:filter=>f.call('contexts',()=>{
      assert.deepEqual(filter,{contextTypes:['TAB'],documentIds:['document-1'],tabIds:[7],frameIds:[0],incognito:false});
      return f.rows;
    }),sendNativeMessage:(host,envelope)=>{
      assert.equal(host,'org.sdsctl.browser_device');
      assert.deepEqual(Object.keys(envelope).sort(),['action','build','request','version']);
      assert.equal(envelope.version,1);assert.equal(envelope.action,'worker-request');assert.equal(envelope.build,build);
      const request=envelope.request;
      assert.equal(request.version,1);
      if(request.action==='continuation-initial-session')return f.call('issue',()=>{
        f.issues++;assert.equal(f.saved[KEY].phase,'initial_pending');
        assert.deepEqual(request,{version:1,action:'continuation-initial-session',
          epoch,intent:f.saved[KEY].intent,binding:f.review.binding});
        f.native=clone(f.active);return {version:1,ok:true,build,...f.active,session:{token,expires_in:300}};
      });
      assert.deepEqual(Object.keys(request).sort(),['action','version']);
      if(request.action==='continuation-current')return f.call('current',()=>({...f.initial,
        continuation:{epoch:f.native.epoch,mode:f.native.mode,binding:clone(f.native.binding)}}));
      assert.equal(request.action,'continuation-review');return f.call('server-review',()=>({version:1,ok:true,
        build,...f.review}));
    }},storage:{local:{setAccessLevel:value=>f.call('access',()=>{
      assert.deepEqual(value,{accessLevel:'TRUSTED_CONTEXTS'});
    }),get:key=>f.call(key===STOP?'read-stop':'read',()=>{
      assert([null,STOP].includes(key));
      return key===null?f.saved:Object.hasOwn(f.saved,key)?{[key]:f.saved[key]}:{};
    }),set:values=>f.call(Object.hasOwn(values,STOP)?'write-stop':'write-'+values[KEY].phase,()=>{
      f.writes.push(clone(values));f.saved={...f.saved,...clone(values)};
    }),clear:forbidden('storage.clear'),remove:forbidden('storage.remove')}},
    cookies:{get:key=>f.call('cookie',()=>{
      assert.deepEqual(key,{url:origin+'/',name:'__Host-sdsctl-device-session'});return f.cookie;
    }),set:details=>f.call('cookie-set',()=>{
      assert.equal(f.saved[KEY].phase,'initial_pending');
      const {url,...value}=clone(details);
      f.cookie={...value,domain:new URL(url).hostname,hostOnly:true,session:false};return f.cookie;
    }),remove:forbidden('cookie.remove')},
    alarms:{onAlarm:event('alarm'),get:name=>f.call('alarm',()=>{
      assert.equal(name,'sdsctl-device-recovery');return f.alarm;
    }),create:forbidden('alarm.create'),clear:forbidden('alarm.clear')},
    tabs:{onUpdated:event('tab'),get:tab=>f.call('tab',()=>{assert.equal(tab,7);return f.tab;}),
      create:forbidden('tab.create'),update:forbidden('tab.update'),remove:forbidden('tab.remove')}};
  f.options={createProbe:({signal})=>{
      assert.equal(signal.aborted,false);f.signal=signal;
      return {open:()=>f.call('probe-open',()=>selection),
        verify:()=>f.call('probe-verify',()=>({url:origin+'/device-display',...selection,
          displayOnly:true,deviceEnrolled:true,remainingSeconds:300})),close:()=>{f.closed++;}};
    },wall:()=>f.clock[0],monotonic:()=>f.clock[1],
    schedule:(fn,ms)=>{assert([10000,12000,45000,60000].includes(ms));
      const timer={fn,ms};f.timers.add(timer);return timer;},cancel:timer=>f.timers.delete(timer)};
  f.start=()=>{
    f.gate=createWorkerEventGate(f.chrome,()=>f.clock[1],f.options.schedule,f.options.cancel);
    const gated=f.gate.prepareContinuationConsent(),runtime=Object.create(gated.runtime);
    Object.defineProperty(runtime,'sendNativeMessage',{value:(host,request)=>
      f.chrome.runtime.sendNativeMessage(host,{version:1,action:'worker-request',build,request})});
    f.scoped=Object.create(gated);Object.defineProperty(f.scoped,'runtime',{value:runtime});
    f.nativePorts=createContinuationNativePorts(f.scoped,f.initial,build,f.options);
    f.owner=connectContinuationOperationWorker(f.scoped,f.initial,build,{...f.options,
      readCurrent:f.nativePorts.readCurrent,issueInitial:f.nativePorts.issueInitial,
      invalidateNative:f.nativePorts.invalidate});f.gate.open();
    assert.deepEqual(Object.keys(f.events),['message','startup','installed','alarm','tab']);
  };
  f.ask=(message={action:'resume-review'},document=sender())=>new Promise(resolve=>{
    if(f.events.message(message,document,resolve)!==true)resolve('unhandled');
  });
  f.confirm=(ticket,document=sender())=>f.ask({action:'resume-confirm',ticket},document);
  f.logout=(change={})=>f.ask({action:'logout-begin'},
    {...sender(),origin,url:origin+'/device-display',...change});
  f.stopRecord=()=>assert.deepEqual(f.saved[STOP],{version:1,identity,epoch,build,stopped:true});
  return f;
}

for(const origin of ['https://display.example.test','https://192.0.2.18:8443','https://[2001:db8::18]:8443'])
  for(const legacy of [true,false])test('composed owner accepts once with exact '+origin+' legacy='+legacy,async()=>{
    const f=fixture(origin,legacy),before=clone(f.saved);f.start();assert.equal(f.calls.length,0);
    const r=await f.ask();assert.deepEqual(f.saved,before);assert.equal(f.writes.length,0);
    assert.equal(f.issues,0);assert.deepEqual(await f.confirm(r.ticket),accepted);
    assert.equal(f.issues,1);assert.equal(f.closed,1);assert.equal(f.timers.size,0);
    assert.deepEqual(f.writes.map(v=>v[KEY].phase),['initial_pending','accepted']);
    for(const [a,b] of [['write-initial_pending','issue'],['issue','cookie-set'],
      ['cookie-set','probe-verify'],['probe-verify','write-accepted']])assert(f.calls.indexOf(a)<f.calls.indexOf(b));
    assert(!JSON.stringify(f.saved).includes(token));assert(!JSON.stringify(f.writes).includes(r.ticket));
    assert.deepEqual(classifyContinuationStartup(f.settings,f.saved[KEY],f.native),
      {mode:'verification_required',sessionReady:false});
    const count=f.calls.length;
    assert.deepEqual(await f.confirm(r.ticket),refused);assert.deepEqual(await f.ask(),refused);
    assert.equal(f.calls.length,count);assert(!Object.hasOwn(f.saved,STOP));
    const saved=clone(f.saved[KEY]),cookie=clone(f.cookie);
    assert.deepEqual(await f.owner.stop(),stopped);f.stopRecord();
    assert.deepEqual(f.saved[KEY],saved);assert.deepEqual(f.cookie,cookie);
    await assert.rejects(f.owner.stop());assert.equal(f.issues,1);
  });

const baseline=fixture();baseline.start();const reviewed=await baseline.ask();
const confirmStart=baseline.calls.length;assert.deepEqual(await baseline.confirm(reviewed.ticket),accepted);

// Every asynchronous boundary in the complete flow is suspended on each side
// of its possible side effect. STOP must win without pretending to undo it.
for(const when of ['before','after'])for(const [cut,name] of baseline.calls.entries()) {
  test(when+' cancellation at '+cut+' '+name+' cannot advance the operation',async()=>{
    const f=fixture(),entered=deferred(),held=deferred();f.start();
    f[when]=async(_name,index)=>{if(index===cut){entered.resolve();await held.promise;}};
    const run=(async()=>{const r=await f.ask();return r.ticket?f.confirm(r.ticket):r;})();
    await entered.promise;
    assert.deepEqual(await f.owner.stop(),stopped);f.stopRecord();
    assert.deepEqual(await run,refused);
    const count=f.calls.length;held.resolve();await settle();await settle();
    assert.equal(f.calls.length,count);f.stopRecord();assert(f.issues<=1);
    assert.equal(f.timers.size,0);if(f.signal)assert.equal(f.signal.aborted,true);
    assert.equal(f.writes.filter(v=>Object.hasOwn(v,STOP)).length,1);
    assert.deepEqual(await f.ask(),refused);assert.equal(f.calls.length,count);
  });
  test(when+' failed acknowledgement at '+cut+' '+name+' is terminal',async()=>{
    const f=fixture();f.start();f[when]=(_name,index)=>{if(index===cut)throw Error('private fixture failure');};
    const r=await f.ask();assert.deepEqual(r.ticket?await f.confirm(r.ticket):r,refused);
    await settle();f.stopRecord();assert(f.issues<=1);assert.equal(f.timers.size,0);
    f[when]=null;const count=f.calls.length;
    assert.deepEqual(await f.ask(),refused);await assert.rejects(f.owner.stop());
    assert.equal(f.calls.length,count);
  });
}

for(const name of ['write-initial_pending','issue','cookie-set','probe-verify','write-accepted'])
  for(const event of ['cancel','navigation','logout'])test(event+' while '+name+' is outstanding fences its late reply',async()=>{
    const f=fixture(),entered=deferred(),held=deferred();f.start();const r=await f.ask();
    f.after=async current=>{if(current===name){entered.resolve();await held.promise;}};
    const run=f.confirm(r.ticket);await entered.promise;
    if(event==='cancel')await f.ask({action:'continuation-cancel'});
    if(event==='navigation')f.events.tab(7,{status:'loading'},{id:7});
    if(event==='logout')await f.logout();
    assert.deepEqual(await run,refused);await settle();f.stopRecord();
    const count=f.calls.length;held.resolve();await settle();assert.equal(f.calls.length,count);
    assert.equal(f.timers.size,0);if(f.signal)assert.equal(f.signal.aborted,true);
  });

test('overlapping confirmations and unrelated messages cannot replace or stop the owner',async()=>{
  const f=fixture(),entered=deferred(),held=deferred();f.start();const r=await f.ask();
  f.before=async name=>{if(name==='issue'){entered.resolve();await held.promise;}};
  const run=f.confirm(r.ticket);await entered.promise;const count=f.calls.length;
  assert.deepEqual(await f.confirm(r.ticket),refused);
  assert.deepEqual(await f.confirm(r.ticket,{...sender(),documentId:'document-2'}),refused);
  await f.ask({action:'continuation-cancel'},{...sender(),documentId:'document-2'});
  await f.logout({origin:'https://foreign.example',url:'https://foreign.example/device-display'});
  f.events.tab(8,{status:'loading'},{id:8});assert.equal(f.calls.length,count);
  assert(!Object.hasOwn(f.saved,STOP));held.resolve();assert.deepEqual(await run,accepted);
  assert.equal(f.issues,1);assert.equal(f.timers.size,0);
});

for(const phase of ['initial_pending','accepted'])test('document replaced after '+phase+' persistence never reports readiness',async()=>{
  const f=fixture();f.start();const r=await f.ask();
  f.after=name=>{if(name==='write-'+phase)f.rows[0].documentId='document-2';};
  assert.deepEqual(await f.confirm(r.ticket),refused);await settle();f.stopRecord();
  assert.equal(f.saved[KEY].phase,phase);assert.equal(f.issues,phase==='accepted'?1:0);
  assert.equal(f.timers.size,0);
});

for(const field of [0,1])for(const budget of ['review','installation'])
  test('unrenewed '+budget+' budget on clock '+field+' applies through asynchronous installation',async()=>{
    const f=fixture();f.start();const r=await f.ask();
    if(budget==='review')f.clock[field]+=59000;
    f.after=name=>{if(name==='write-initial_pending')f.clock[field]+=budget==='review'?1000:45000;};
    assert.deepEqual(await f.confirm(r.ticket),refused);await settle();f.stopRecord();
    assert.equal(f.issues,0);assert.equal(f.timers.size,0);
  });

for(const field of [0,1])test('installation deadline crossed during final pre-issuance document check on clock '+field,async()=>{
  const f=fixture();f.start();const r=await f.ask();let delayed=false;
  f.after=name=>{
    if(!delayed&&name==='contexts'&&f.saved[KEY].phase==='initial_pending') {
      delayed=true;f.clock[field]+=45000;
    }
  };
  assert.deepEqual(await f.confirm(r.ticket),refused);await settle();f.stopRecord();
  assert(delayed);assert.equal(f.issues,0);assert.equal(f.cookie,null);assert.equal(f.timers.size,0);
});

for(const duration of [45000,60000])test('delivered '+duration+' timer invalidates a hung issuance without waiting',async()=>{
  const f=fixture(),entered=deferred(),held=deferred();f.start();const r=await f.ask();
  f.before=async name=>{if(name==='issue'){entered.resolve();await held.promise;}};
  const run=f.confirm(r.ticket);await entered.promise;
  [...f.timers].find(t=>t.ms===duration).fn();assert.deepEqual(await run,refused);
  await settle();f.stopRecord();const count=f.calls.length;held.resolve();await settle();
  assert.equal(f.calls.length,count);assert.equal(f.issues,1);assert.equal(f.cookie,null);
  assert.equal(f.timers.size,0);
});

for(const when of ['before','after'])test('failed stop write '+when+' commit cannot return saved acknowledgement or replay',async()=>{
  const f=fixture();f.start();await f.ask();
  f[when]=name=>{if(name==='write-stop')throw Error('private storage error');};
  await assert.rejects(f.owner.stop());assert.equal(f.issues,0);
  if(when==='after')f.stopRecord();else assert(!Object.hasOwn(f.saved,STOP));
  const count=f.calls.length;f[when]=null;await assert.rejects(f.owner.stop());
  assert.deepEqual(await f.ask(),refused);assert.equal(f.calls.length,count);assert.equal(f.timers.size,0);
});

test('late stop persistence after timeout stays unconfirmed without retry',async()=>{
  const f=fixture(),entered=deferred(),held=deferred();f.start();await f.ask();
  f.before=async name=>{if(name==='write-stop'){entered.resolve();await held.promise;}};
  const run=f.owner.stop();await entered.promise;
  [...f.timers].find(t=>t.ms===10000).fn();await assert.rejects(run);
  assert(!Object.hasOwn(f.saved,STOP));const count=f.calls.length;
  held.resolve();await settle();f.stopRecord();assert.equal(f.calls.length,count);
  await assert.rejects(f.owner.stop());assert.equal(f.issues,0);assert.equal(f.timers.size,0);
});

test('test matrix covers document checks after installer acceptance, not only its inner I/O',()=>{
  assert(confirmStart>0);assert(baseline.calls.length>confirmStart);
  assert.deepEqual(baseline.calls.slice(-3),['contexts','tab','contexts']);
});
