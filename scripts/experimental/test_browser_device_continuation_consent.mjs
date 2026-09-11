import test from 'node:test';
import assert from 'node:assert/strict';
import {connectContinuationConsentWorker,connectContinuationConfirmationPage} from '../../src/sds200/browser_assets/browser_device_continuation_consent.mjs';
import {createWorkerEventGate} from '../../src/sds200/browser_assets/browser_device_worker_gate.mjs';

const id='a'.repeat(32),build='b'.repeat(64),identity='c'.repeat(64),epoch='d'.repeat(64);
const extension=`chrome-extension://${id}`,url=extension+'/resume.html',KEY='sdsctlDeviceRecovery';
const clone=structuredClone,settle=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const refused={mode:'administrator_required'};
const sender=()=>({id,origin:extension,url,frameId:0,documentLifecycle:'active',
  documentId:'document-1',tab:{id:7,incognito:false}});

function fixture(origin='https://192.0.2.18:8443',legacy=false) {
  const f={calls:[],sinks:[],events:{},timers:new Set(),clock:[100000,50000],cookie:null,alarm:null};
  f.initial={version:1,ok:true,build,role:'continuation',config:{origin,identity,nativeHost:'org.sdsctl.browser_device'},
    extensionId:id,acknowledge:false,launch:null,
    continuation:{epoch,mode:'paused',binding:{fingerprint:'e'.repeat(64),revision:5,generation:null}}};
  f.generation=19;
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
    }),sendNativeMessage:(host,request)=>{
      assert.equal(host,'org.sdsctl.browser_device');assert.deepEqual(Object.keys(request).sort(),['action','version']);
      assert.equal(request.version,1);
      if(request.action==='continuation-current')return f.call('current',()=>f.initial);
      assert.equal(request.action,'continuation-review');return f.call('server-review',()=>({version:1,ok:true,
        build,identity,epoch,mode:'paused',binding:{...f.initial.continuation.binding,generation:f.generation}}));
    }},storage:{local:{setAccessLevel:value=>f.call('access',()=>{
      assert.deepEqual(value,{accessLevel:'TRUSTED_CONTEXTS'});
    }),get:key=>f.call('storage',()=>{assert.equal(key,null);return f.saved;}),
      set:forbidden('storage.set'),clear:forbidden('storage.clear'),remove:forbidden('storage.remove')}},
    cookies:{get:key=>f.call('cookie',()=>{
      assert.deepEqual(key,{url:origin+'/',name:'__Host-sdsctl-device-session'});return f.cookie;
    }),set:forbidden('cookie.set'),remove:forbidden('cookie.remove')},
    alarms:{onAlarm:event('alarm'),get:name=>f.call('alarm',()=>{
      assert.equal(name,'sdsctl-device-recovery');return f.alarm;
    }),create:forbidden('alarm.create'),clear:forbidden('alarm.clear')},
    tabs:{onUpdated:event('tab'),get:tab=>f.call('tab',()=>{assert.equal(tab,7);return f.tab;}),
      create:forbidden('tab.create'),update:forbidden('tab.update'),remove:forbidden('tab.remove')}};
  f.options={onConfirmed:value=>{f.sinks.push(value);},wall:()=>f.clock[0],monotonic:()=>f.clock[1],
    schedule:(fn,ms)=>{assert([12000,60000].includes(ms));const timer={fn,ms};f.timers.add(timer);return timer;},
    cancel:timer=>f.timers.delete(timer)};
  f.start=()=>{
    f.gate=createWorkerEventGate(f.chrome,()=>f.clock[1],f.options.schedule,f.options.cancel);
    f.scoped=f.gate.prepareContinuationConsent();
    f.owner=connectContinuationConsentWorker(f.scoped,f.initial,build,f.options);f.gate.open();
    assert.deepEqual(Object.keys(f.events),['message','startup','installed','alarm','tab']);
  };
  f.ask=(message={action:'resume-review'},document=sender())=>new Promise(resolve=>{
    if(f.events.message(message,document,resolve)!==true)resolve('unhandled');
  });
  f.confirm=(ticket,document=sender())=>f.ask({action:'resume-confirm',ticket},document);
  f.logout=(change={})=>f.ask({action:'logout-begin'},
    {...sender(),origin,url:origin+'/device-display',...change});
  return f;
}

test('legacy public options cannot select private asynchronous completion or invalidation hooks',async()=>{
  const f=fixture();Object.assign(f.options,{asynchronous:true,
    complete:()=>assert.fail('private completion selected'),
    onInvalidate:()=>assert.fail('private invalidation selected')});
  f.start();const r=await f.ask();
  assert.deepEqual(await f.confirm(r.ticket),{mode:'continuation_confirmation_checked',sessionReady:false});
  f.owner.invalidate();assert.equal(f.sinks.length,1);assert.equal(f.timers.size,0);
});

for(const origin of ['https://192.0.2.18:8443','https://display.example','https://[::1]:8443'])
  for(const legacy of [false,true])test('one document, two fresh reviews, one confirmation-only sink '+origin+' '+legacy,async()=>{
    const f=fixture(origin,legacy),saved=clone(f.saved);f.start();
    const reviewed=await f.ask();assert.match(reviewed.ticket,/^[a-f0-9]{64}$/);
    assert.deepEqual({...reviewed,ticket:null},{mode:'continuation_confirmation_reviewed',ticket:null,
      nativeRevision:5,serverGeneration:19});assert.equal(f.sinks.length,0);
    assert.deepEqual(await f.confirm(reviewed.ticket),{mode:'continuation_confirmation_checked',sessionReady:false});
    assert.equal(f.sinks.length,1);assert.equal(f.calls.filter(n=>n==='server-review').length,2);
    const sink=f.sinks[0];assert(Object.isFrozen(sink));assert(Object.isFrozen(sink.reviewed.binding));
    assert.deepEqual(sink.document,{tabId:7,documentId:'document-1'});
    assert.equal(sink.reviewed.binding.revision,5);assert.equal(sink.reviewed.binding.generation,19);
    assert.equal(sink.settings.origin,origin);assert.equal(sink.signal.aborted,false);
    assert(!JSON.stringify(sink).includes(reviewed.ticket));assert.deepEqual(f.saved,saved);
    assert.equal(f.timers.size,0);const count=f.calls.length;
    assert.deepEqual(await f.confirm(reviewed.ticket),refused);assert.deepEqual(await f.ask(),refused);
    assert.equal(f.calls.length,count);assert.equal(f.sinks.length,1);f.owner.invalidate();
  });

for(const change of [{id:'p'.repeat(32)},{origin:'https://foreign.example'},{url:url+'#fragment'},
  {frameId:1},{documentLifecycle:'cached'},{documentId:''},{documentId:'x'.repeat(129)},
  {tab:{id:7,incognito:true}},{tab:{id:-1,incognito:false}}])
  test('foreign/invalid sender has no native review '+JSON.stringify(change),async()=>{
    const f=fixture();f.start();await settle();const calls=clone(f.calls);
    assert.deepEqual(await f.ask({action:'resume-review'},{...sender(),...change}),
      change.origin?{mode:'setup_error'}:'unhandled');
    assert.deepEqual(f.calls,calls);assert.equal(f.sinks.length,0);f.owner.invalidate();
  });

for(const message of [{action:'resume-review',generation:19},{action:'resume-review',ticket:'f'.repeat(64)},
  {action:'resume-confirm'},{action:'resume-confirm',ticket:'short'},
  {action:'resume-confirm',ticket:'f'.repeat(64),binding:'e'.repeat(64)},
  {action:'initialize'},{action:'start'},{action:'continuation-initial-session'}])
  test('caller cannot supply approval or initialize '+JSON.stringify(message),async()=>{
    const f=fixture();f.start();await settle();const count=f.calls.length;
    await f.ask(message);assert.equal(f.calls.length,count);assert.equal(f.sinks.length,0);f.owner.invalidate();
  });

for(const change of [{documentId:'document-2'},{tab:{id:8,incognito:false}}])
  test('review ticket cannot move to another document/tab '+JSON.stringify(change),async()=>{
    const f=fixture();f.start();const r=await f.ask(),count=f.calls.length;
    assert.deepEqual(await f.confirm(r.ticket,{...sender(),...change}),refused);
    assert.equal(f.calls.length,count);assert.equal(f.sinks.length,0);f.owner.invalidate();
  });

for(const kind of ['generation','revision','fingerprint','epoch','saved','cookie','alarm'])
  test('changed '+kind+' between review and confirm cannot reach the sink',async()=>{
    const f=fixture();f.start();const r=await f.ask();
    if(kind==='generation')f.generation++;if(kind==='revision')f.initial.continuation.binding.revision++;
    if(kind==='fingerprint')f.initial.continuation.binding.fingerprint='f'.repeat(64);
    if(kind==='epoch')f.initial.continuation.epoch='f'.repeat(64);
    if(kind==='saved')f.saved[KEY].identity='f'.repeat(64);
    if(kind==='cookie')f.cookie={value:'PRIVATE'};if(kind==='alarm')f.alarm={name:'sdsctl-device-recovery'};
    assert.deepEqual(await f.confirm(r.ticket),refused);const count=f.calls.length;
    assert.deepEqual(await f.confirm(r.ticket),refused);assert.equal(f.calls.length,count);
    assert.equal(f.sinks.length,0);assert.equal(f.timers.size,0);
  });

for(const kind of ['missing','duplicate','document','origin','url','frame','incognito','type','tab',
  'tab-pending','tab-loading','tab-url','tab-incognito'])test('fresh document validation refuses '+kind,async()=>{
    const f=fixture();f.start();const r=await f.ask();
    if(kind==='missing')f.rows=[];if(kind==='duplicate')f.rows.push(clone(f.rows[0]));
    if(kind==='document')f.rows[0].documentId='document-2';if(kind==='origin')f.rows[0].documentOrigin='null';
    if(kind==='url')f.rows[0].documentUrl=url+'?x';if(kind==='frame')f.rows[0].frameId=1;
    if(kind==='incognito')f.rows[0].incognito=true;if(kind==='type')f.rows[0].contextType='POPUP';
    if(kind==='tab')f.rows[0].tabId=8;if(kind==='tab-pending')f.tab.pendingUrl='https://other.example';
    if(kind==='tab-loading')f.tab.status='loading';if(kind==='tab-url')f.tab.url=url+'#x';
    if(kind==='tab-incognito')f.tab.incognito=true;
    assert.deepEqual(await f.confirm(r.ticket),refused);assert.equal(f.sinks.length,0);
    assert.equal(f.timers.size,0);
  });

for(const change of [{status:'loading'},{url:'https://other.example/PRIVATE'},
  {url:url},{url:undefined}])test('navigation cancels even if same URL is restored '+JSON.stringify(change),async()=>{
    const f=fixture();f.start();const r=await f.ask(),count=f.calls.length;
    f.events.tab(7,change,{id:7});assert.deepEqual(await f.confirm(r.ticket),refused);
    assert.equal(f.calls.length,count);assert.equal(f.sinks.length,0);assert.equal(f.timers.size,0);
  });

test('unrelated navigation and foreign logout do not cancel the owned document',async()=>{
  const f=fixture();f.start();const r=await f.ask();
  f.events.tab(8,{url:'https://other.example/PRIVATE'},{id:8});
  await f.logout({origin:'https://other.example',url:'https://other.example/device-display'});
  assert.equal((await f.confirm(r.ticket)).mode,'continuation_confirmation_checked');f.owner.invalidate();
});

for(const kind of ['logout','cancel','invalidate'])test(kind+' consumes the lane before any subsequent confirmation',async()=>{
  const f=fixture();f.start();const r=await f.ask(),count=f.calls.length;
  if(kind==='logout')await f.logout();if(kind==='cancel')await f.ask({action:'continuation-cancel'});
  if(kind==='invalidate')f.owner.invalidate();
  assert.deepEqual(await f.confirm(r.ticket),refused);assert.equal(f.calls.length,count);
  assert.equal(f.sinks.length,0);assert.equal(f.timers.size,0);
});

for(const field of [0,1])for(const change of [-1,60000,NaN,Infinity])
  test('clock '+field+' '+change+' invalidates without relying on timer delivery',async()=>{
    const f=fixture();f.start();const r=await f.ask();f.clock[field]+=change;
    assert.deepEqual(await f.confirm(r.ticket),refused);assert.equal(f.sinks.length,0);assert.equal(f.timers.size,0);
  });

test('undetectable lost gate reply cannot retry; its unused review expires without calling the sink',async()=>{
  const f=fixture();f.start();
  f.events.message({action:'resume-review'},sender(),()=>{throw Error('closed document');});await settle();
  assert.deepEqual(await f.ask(),refused);assert.equal(f.sinks.length,0);
  // Chrome/the outer gate may swallow a closed response channel. There is no
  // acknowledgement-of-delivery API; the bounded unused selection expires.
  assert.equal(f.timers.size,1);[...f.timers][0].fn();assert.equal(f.timers.size,0);
});

test('queued sender is copied; overlapping reviews and confirms have a single owner',async()=>{
  const f=fixture(),held=deferred(),entered=deferred();f.start();
  f.before=async name=>{if(name==='server-review'){entered.resolve();await held.promise;}};
  const s=sender(),first=f.ask({action:'resume-review'},s);s.documentId='changed';s.tab.id=88;
  await entered.promise;assert.deepEqual(await f.ask(),refused);held.resolve();
  const r=await first;f.before=null;
  const p=f.confirm(r.ticket);assert.deepEqual(await f.confirm(r.ticket),refused);
  assert.equal((await p).mode,'continuation_confirmation_checked');assert.equal(f.sinks.length,1);f.owner.invalidate();
});

// Every awaited read boundary is interrupted under the real synchronous gate.
const baseline=fixture();baseline.start();const reviewed=await baseline.ask();
const firstLength=baseline.calls.length;await baseline.confirm(reviewed.ticket);baseline.owner.invalidate();
for(const stage of ['review','confirm'])test('same-URL document replaced during final tab read '+stage,async()=>{
  const f=fixture();f.start();
  const index=baseline.calls.lastIndexOf('tab',stage==='review'?firstLength-1:baseline.calls.length-1);
  f.after=(_name,i)=>{if(i===index)f.rows[0].documentId='document-2';};
  const r=await f.ask();
  if(stage==='review')assert.deepEqual(r,refused);
  else assert.deepEqual(await f.confirm(r.ticket),refused);
  assert.equal(f.sinks.length,0);f.owner.invalidate();
});
for(const stage of ['review','confirm'])for(const [index,name] of baseline.calls.entries()) {
  if((stage==='review')!==(index<firstLength)||name==='access')continue;
  for(const interruption of ['timer','logout','navigation'])test(stage+' '+interruption+' while '+index+' '+name+' is held',async()=>{
    const f=fixture(),entered=deferred(),held=deferred();f.start();
    if(stage==='review')f.before=async(_name,i)=>{if(i===index){entered.resolve();await held.promise;}};
    let running;
    if(stage==='review')running=f.ask();else {
      const r=await f.ask();f.before=async(_name,i)=>{if(i===index){entered.resolve();await held.promise;}};
      running=f.confirm(r.ticket);
    }
    await entered.promise;
    if(interruption==='timer')[...f.timers].find(t=>t.ms===60000).fn();
    if(interruption==='logout')await f.logout();
    if(interruption==='navigation')f.events.tab(7,{status:'loading'},{id:7});
    assert.deepEqual(await running,refused);const count=f.calls.length;
    held.resolve();await settle();assert.equal(f.calls.length,count);assert.equal(f.sinks.length,0);
    assert.deepEqual(await f.ask(),refused);assert.equal(f.calls.length,count);assert.equal(f.timers.size,0);
  });
}

for(const sink of [()=>{throw Error('PRIVATE');},()=>({mode:'accepted'}),()=>Promise.reject(Error('PRIVATE'))])
  test('fixture sink failure or async result never means session-ready '+sink.toString(),async()=>{
    const f=fixture();let count=0;f.options.onConfirmed=value=>{count++;return sink(value);};f.start();
    const r=await f.ask();assert.deepEqual(await f.confirm(r.ticket),refused);
    assert.deepEqual(await f.confirm(r.ticket),refused);assert.equal(count,1);assert.equal(f.timers.size,0);
  });

test('sink is consumed before reentrant confirmation, and signal is invalidated on later sign-out',async()=>{
  const f=fixture();let r,reentrant;f.options.onConfirmed=value=>{f.sinks.push(value);reentrant=f.confirm(r.ticket);};
  f.start();r=await f.ask();assert.equal((await f.confirm(r.ticket)).mode,'continuation_confirmation_checked');
  assert.deepEqual(await reentrant,refused);assert.equal(f.sinks.length,1);
  await f.logout();assert.equal(f.sinks[0].signal.aborted,true);
});

function pageFixture() {
  const f=fixture();f.start();f.pageCalls=[];f.dom={};
  for(const name of ['review','resume-form','confirm','resume','notice','reviewed'])f.dom[name]={
    events:{},disabled:false,checked:false,textContent:'',addEventListener(name,fn){this.events[name]=fn;}};
  f.window={location:{href:url},events:{},addEventListener(name,fn){this.events[name]=fn;}};f.window.top=f.window;
  f.ports={document:{getElementById:name=>f.dom[name]},window:f.window,runtime:{
    getURL:f.chrome.runtime.getURL,sendMessage:message=>{f.pageCalls.push(clone(message));return f.ask(message);}}};
  f.click=(trusted=true)=>f.dom.review.events.click({isTrusted:trusted});
  f.submit=(trusted=true)=>f.dom['resume-form'].events.submit({isTrusted:trusted,preventDefault(){}});
  return f;
}

test('trusted page review and explicit checked submit complete only the confirmation handshake',async()=>{
  const f=pageFixture();connectContinuationConfirmationPage(f.ports);
  assert.deepEqual(f.pageCalls,[]);assert.equal(f.dom.confirm.disabled,true);
  f.click(false);f.dom.confirm.checked=true;f.submit();assert.deepEqual(f.pageCalls,[]);
  f.click();await settle();assert.equal(f.dom.confirm.checked,false);
  assert.equal(f.dom.confirm.disabled,false);assert.match(f.dom.reviewed.textContent,/revision 5; server generation 19/);
  f.submit();f.dom.confirm.checked=true;f.submit(false);assert.equal(f.pageCalls.length,1);
  f.submit();f.submit();await settle();assert.equal(f.pageCalls.length,2);assert.equal(f.sinks.length,1);
  assert.match(f.dom.notice.textContent,/Sign-in remains disabled; no session was issued/);
  assert.equal(f.dom.confirm.disabled,true);f.owner.invalidate();
});

for(const stage of ['review','confirm'])test('page navigation during '+stage+' refuses late reply and cancels selection',async()=>{
  const f=pageFixture(),held=deferred();connectContinuationConfirmationPage(f.ports);
  if(stage==='confirm'){f.click();await settle();f.dom.confirm.checked=true;}
  f.ports.runtime.sendMessage=message=>{
    f.pageCalls.push(clone(message));
    return message.action==='continuation-cancel'?f.ask(message):held.promise;
  };
  if(stage==='review')f.click();else f.submit();
  f.window.events.pagehide();held.resolve(stage==='review'?{
    mode:'continuation_confirmation_reviewed',ticket:'f'.repeat(64),nativeRevision:5,serverGeneration:19}:
    {mode:'continuation_confirmation_checked',sessionReady:false});await settle();
  assert.equal(f.dom.confirm.disabled,true);assert.equal(f.dom.resume.disabled,true);assert.equal(f.sinks.length,0);
  assert.equal(f.pageCalls.at(-1).action,'continuation-cancel');f.owner.invalidate();
});

for(const reply of [null,{}, {mode:'reviewed',ticket:'f'.repeat(64),nativeRevision:5,serverGeneration:19},
  {mode:'continuation_confirmation_reviewed',ticket:'f'.repeat(64),nativeRevision:true,serverGeneration:19},
  {mode:'continuation_confirmation_reviewed',ticket:'f'.repeat(64),nativeRevision:5,serverGeneration:19,extra:true}])
  test('page refuses malformed/ordinary review '+JSON.stringify(reply),async()=>{
    const f=pageFixture();f.ports.runtime.sendMessage=async()=>reply;
    connectContinuationConfirmationPage(f.ports);f.click();await settle();
    assert.equal(f.dom.confirm.disabled,true);assert.equal(f.sinks.length,0);f.owner.invalidate();
  });

test('confirmation page requires the exact top-level extension document',()=>{
  const f=pageFixture();f.window.location.href=url+'#x';assert.throws(()=>connectContinuationConfirmationPage(f.ports));
  f.window.location.href=url;f.window.top={};assert.throws(()=>connectContinuationConfirmationPage(f.ports));
  assert.deepEqual(f.pageCalls,[]);f.owner.invalidate();
});
