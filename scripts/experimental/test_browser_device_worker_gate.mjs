import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkerEventGate} from '../../src/sds200/browser_assets/browser_device_worker_gate.mjs';
import {createContinuationProbe} from '../../src/sds200/browser_assets/browser_device_continuation_probe.mjs';

const id='a'.repeat(32), url=page=>`chrome-extension://${id}/${page}`;
const sender=()=>({id,url:url('setup.html'),frameId:0,documentLifecycle:'active',
  documentId:'doc-1',tab:{id:7,incognito:false}});
function fixture() {
  const callbacks={}, cancelled=[];let now=100, expiry;
  const event=name=>({addListener:fn=>{assert(!callbacks[name]);callbacks[name]=fn;}});
  const chrome={runtime:{id,onMessage:event('message'),onStartup:event('startup'),
    onInstalled:event('installed')},alarms:{onAlarm:event('alarm')},tabs:{onUpdated:event('tab')}};
  const gate=createWorkerEventGate(chrome,()=>now,fn=>{expiry=fn;return 9;},n=>cancelled.push(n));
  return {gate,chrome,callbacks,cancelled,advance:n=>{now+=n;},expire:()=>expiry()};
}

test('all five inert ingress listeners register synchronously',()=>{
  const f=fixture();
  assert.deepEqual(Object.keys(f.callbacks),['message','startup','installed','alarm','tab']);
  f.gate.fail();
});

test('queued message and sender are bounded snapshots, not mutable caller objects',()=>{
  const f=fixture(), message={action:'initialize'}, document=sender(), replies=[], seen=[];
  assert.equal(f.callbacks.message(message,document,r=>replies.push(r)),true);
  message.action='suspend';document.url=url('control.html');document.tab.id=99;
  f.gate.chrome.runtime.onMessage.addListener((m,s,r)=>{seen.push([m,s]);r({mode:'ready'});return false;});
  assert.deepEqual(seen,[]);f.gate.check();f.gate.open();
  assert.equal(seen[0][0].action,'initialize');assert.equal(seen[0][1].url,url('setup.html'));
  assert.equal(seen[0][1].tab.id,7);assert.deepEqual(replies,[{mode:'ready'}]);
});

test('one accepted message is dispatched and replied to at most once',()=>{
  const f=fixture(), replies=[];let later, second=0;
  f.gate.chrome.runtime.onMessage.addListener((m,s,r)=>{later=r;return true;});
  f.gate.chrome.runtime.onMessage.addListener(()=>{second++;return true;});
  f.callbacks.message({action:'initialize'},sender(),r=>replies.push(r));
  f.gate.open();later({mode:'ready'});later({mode:'different'});
  assert.deepEqual(replies,[{mode:'ready'}]);assert.equal(second,0);
});

test('closed response channel is not retried and does not block following events',()=>{
  const f=fixture();let count=0;
  f.callbacks.message({action:'initialize'},sender(),()=>{throw Error('closed');});
  f.callbacks.startup();
  f.gate.chrome.runtime.onMessage.addListener((m,s,r)=>{r({mode:'ready'});return false;});
  f.gate.chrome.runtime.onStartup.addListener(()=>{count++;});
  assert.doesNotThrow(()=>f.gate.open());assert.equal(count,1);
});

test('pending events replay in arrival order only through selected handlers',()=>{
  const f=fixture(), order=[];
  f.callbacks.startup();f.callbacks.alarm({name:'sdsctl-device-recovery'});
  f.callbacks.installed();
  f.callbacks.tab(3,{status:'complete'},{id:3,incognito:false,status:'complete',url:url('startup.html')});
  f.gate.chrome.runtime.onStartup.addListener(()=>order.push('start'));
  f.gate.chrome.alarms.onAlarm.addListener(()=>order.push('alarm'));
  f.gate.chrome.runtime.onInstalled.addListener(()=>order.push('install'));
  f.gate.chrome.tabs.onUpdated.addListener(()=>order.push('tab'));
  assert.deepEqual(order,[]);f.gate.open();
  assert.deepEqual(order,['start','alarm','install','tab']);
});

test('unselected events never become recovery or normal side effects',()=>{
  const f=fixture(), replies=[];
  f.callbacks.startup();f.callbacks.installed();f.callbacks.alarm({name:'sdsctl-device-recovery'});
  f.callbacks.message({action:'initialize'},sender(),r=>replies.push(r));
  f.gate.open();assert.deepEqual(replies,[{mode:'setup_error'}]);
});

test('a throwing selected message handler refuses once without losing later events',()=>{
  const f=fixture(), replies=[];let alternate=0, startup=0;
  f.callbacks.message({action:'initialize'},sender(),r=>replies.push(r));
  f.callbacks.startup();
  f.gate.chrome.runtime.onMessage.addListener(()=>{throw Error('private diagnostic');});
  f.gate.chrome.runtime.onMessage.addListener(()=>{alternate++;return true;});
  f.gate.chrome.runtime.onStartup.addListener(()=>{startup++;});
  f.gate.open();
  assert.deepEqual(replies,[{mode:'setup_error'}]);
  assert.equal(alternate,0);assert.equal(startup,1);
});

test('reply followed by a throw is neither answered again nor retried',()=>{
  const f=fixture(), replies=[];let alternate=0;
  f.gate.chrome.runtime.onMessage.addListener((m,s,r)=>{
    r({mode:'ready'});throw Error('after response');
  });
  f.gate.chrome.runtime.onMessage.addListener(()=>{alternate++;return true;});
  f.gate.open();
  assert.equal(f.callbacks.message({action:'initialize'},sender(),r=>replies.push(r)),false);
  assert.deepEqual(replies,[{mode:'ready'}]);assert.equal(alternate,0);
});

test('unrelated alarms and tabs are ignored instead of filling the queue',()=>{
  const f=fixture();let count=0;
  for(let i=0;i<100;i++) {
    f.callbacks.alarm({name:'other'});
    f.callbacks.tab(i,{status:'complete'},{id:i,incognito:false,status:'complete',url:'https://other.example/'});
  }
  f.gate.chrome.runtime.onStartup.addListener(()=>{count++;});
  f.callbacks.startup();f.gate.open();assert.equal(count,1);
});

for(const [name,change] of [
  ['wrong extension',s=>s.id='b'.repeat(32)],['subframe',s=>s.frameId=1],
  ['inactive document',s=>s.documentLifecycle='cached'],['missing document',s=>delete s.documentId],
  ['oversized document',s=>s.documentId='a'.repeat(129)],['bad document',s=>s.documentId='a/b'],
  ['incognito',s=>s.tab.incognito=true],['missing tab',s=>delete s.tab],
  ['negative tab',s=>s.tab.id=-1],['query suffix',s=>s.url=url('setup.html?x')],
  ['fragment suffix',s=>s.url=url('setup.html#x')],['another page',s=>s.url=url('secret.html')],
  ['another scheme',s=>s.url='http://example.test/'],['oversized url',s=>s.url='a'.repeat(2049)],
])test(`reject ${name} before queueing`,()=>{
  const f=fixture(), s=sender();change(s);
  for(let n=0;n<100;n++)assert.equal(f.callbacks.message({action:'initialize'},s,()=>assert.fail()),false);
  f.gate.check();f.gate.fail();
});

for(const message of [null,[],{}, {action:'a'.repeat(65)}, {action:'initialize',credential:'secret'},
  {action:'initialize',ticket:{nested:'data'}},{action:'initialize',ticket:'a'.repeat(129)},
  {action:'initialize',origin:'https://other.example'}])test(`reject bounded schema ${JSON.stringify(message)}`,()=>{
  const f=fixture();assert.equal(f.callbacks.message(message,sender(),()=>assert.fail()),false);f.gate.fail();
});

test('potential logout is held but server authority is still selected by downstream handler',()=>{
  const f=fixture(), replies=[], s={...sender(),url:'https://other.example/device-display',origin:'https://other.example'};
  assert.equal(f.callbacks.message({action:'logout-begin'},s,r=>replies.push(r)),true);
  f.gate.chrome.runtime.onMessage.addListener((m,s)=>{assert.equal(s.origin,'https://other.example');return false;});
  f.gate.open();assert.deepEqual(replies,[{mode:'setup_error'}]);
});

test('queue overflow refuses the whole pending worker and drains all messages',async()=>{
  const f=fixture(), replies=[];let calls=0;
  f.gate.chrome.runtime.onMessage.addListener(()=>{calls++;});
  for(let i=0;i<64;i++)assert.equal(f.callbacks.message({action:'initialize'},sender(),r=>replies.push(r)),true);
  assert.equal(f.callbacks.message({action:'initialize'},sender(),r=>replies.push(r)),false);
  await assert.rejects(f.gate.deadline);assert.throws(()=>f.gate.open());
  assert.equal(calls,0);assert.equal(replies.length,65);
  assert(replies.every(r=>r.mode==='setup_error'));
});

test('deadline fails all held messages and ignores late events',async()=>{
  const f=fixture(), replies=[];let calls=0;
  f.callbacks.message({action:'initialize'},sender(),r=>replies.push(r));
  f.gate.chrome.runtime.onMessage.addListener(()=>{calls++;});
  f.expire();await assert.rejects(f.gate.deadline);
  f.callbacks.message({action:'initialize'},sender(),r=>replies.push(r));
  assert.throws(()=>f.gate.check());assert.throws(()=>f.gate.open());
  assert.equal(calls,0);assert.deepEqual(replies,[{mode:'setup_error'},{mode:'setup_error'}]);
});

for(const delta of [-1,12000,13000,NaN,Infinity])test(`clock ${delta} refuses late/native result even without timer`,()=>{
  const f=fixture();f.advance(delta);assert.throws(()=>f.gate.check());assert.throws(()=>f.gate.open());
});

test('validated startup cancels its timer; active receiver still holds asynchronous replies',()=>{
  const f=fixture(), replies=[];let done;
  f.gate.chrome.runtime.onMessage.addListener((m,s,r)=>{done=r;return true;});
  f.gate.check();f.gate.open();assert(f.cancelled.includes(9));f.advance(99999);
  assert.equal(f.callbacks.message({action:'initialize'},sender(),r=>replies.push(r)),true);
  done({mode:'ready'});assert.deepEqual(replies,[{mode:'ready'}]);
});

const probeOrigin='https://192.0.2.18:8443', ticket='b'.repeat(64);
const probeSender=(origin=probeOrigin)=>({...sender(),url:origin+'/device-display',origin});
const selectedMessage=()=>({action:'continuation-probe-selected',ticket});
const resultMessage=()=>({action:'continuation-probe-result',ticket,
  displayOnly:true,deviceEnrolled:true,remainingSeconds:300});

test('probe ingress is disabled by default, without consuming the ordinary queue',()=>{
  const f=fixture();
  for(let n=0;n<100;n++)for(const payload of [selectedMessage(),resultMessage()])
    assert.equal(f.callbacks.message(payload,probeSender(),()=>assert.fail()),false);
  f.gate.check();f.gate.open();
  assert.throws(()=>f.gate.prepareContinuationProbe(probeOrigin));
  assert.throws(()=>createContinuationProbe(f.gate.chrome,probeOrigin));
});

for(const origin of [probeOrigin,'https://display.example','https://[::1]:8443'])
test('one fixed native-selected probe lane: '+origin,()=>{
  const f=fixture(), scoped=f.gate.prepareContinuationProbe(origin), seen=[],ordinary=[];
  f.gate.chrome.runtime.onMessage.addListener(m=>ordinary.push(m));
  f.gate.open();
  scoped.runtime.onMessage.addListener((m,s,r)=>{seen.push([m,s]);r({ok:true});});
  const replies=[];
  f.callbacks.message(resultMessage(),probeSender(origin),r=>replies.push(r));
  assert.deepEqual(replies,[{ok:true}]);assert.equal(ordinary.length,0);
  assert.deepEqual(seen[0][0],resultMessage());assert.equal(seen[0][1].origin,origin);
  assert.deepEqual(Object.keys(f.callbacks),['message','startup','installed','alarm','tab']);
  assert.throws(()=>f.gate.prepareContinuationProbe('https://elsewhere.example'));
  f.gate.fail();
});

for(const origin of [null,'http://display.example','https://display.example/',
  'https://DISPLAY.example','https://user:PRIVATE@display.example','https://display.example/path',
  'https://display.example?x','https://display.example#x','not a url','x'.repeat(2049)])
test('reject invalid probe origin without selecting a lane: '+String(origin).slice(0,45),()=>{
  const f=fixture();
  assert.throws(()=>f.gate.prepareContinuationProbe(origin),e=>!e.message.includes('PRIVATE'));
  assert.equal(f.callbacks.message(resultMessage(),probeSender(),()=>assert.fail()),false);
  f.gate.fail();
});

test('a second origin cannot replace the selection before gate opening',()=>{
  const f=fixture(),scoped=f.gate.prepareContinuationProbe(probeOrigin),seen=[];
  assert.throws(()=>f.gate.prepareContinuationProbe('https://elsewhere.example'));
  scoped.runtime.onMessage.addListener((m,s,r)=>{seen.push(s.origin);r({ok:true});});f.gate.open();
  f.callbacks.message(resultMessage(),probeSender(),()=>{});
  assert.equal(f.callbacks.message(resultMessage(),probeSender('https://elsewhere.example'),()=>assert.fail()),false);
  assert.deepEqual(seen,[probeOrigin]);
});

for(const change of [s=>s.id='b'.repeat(32),s=>s.frameId=1,s=>s.documentLifecycle='cached',
  s=>s.documentId='',s=>s.documentId='x'.repeat(129),s=>s.documentId='bad:document',
  s=>s.tab.id=-1,s=>s.tab.id=NaN,s=>s.tab.incognito=true,s=>delete s.tab,
  s=>s.url=probeOrigin+'/device-display?x',s=>s.url=probeOrigin+'/device-display#x',
  s=>s.url=url('resume.html'),s=>s.origin='https://elsewhere.example'])
test('probe sender must match selected origin and active top document: '+String(change),()=>{
  const f=fixture();f.gate.prepareContinuationProbe(probeOrigin);const s=probeSender();change(s);
  // The broader ordinary local-page route may see a string-only select, but it
  // cannot select a probe document; result packets never enter that route.
  assert.equal(f.callbacks.message(resultMessage(),s,()=>assert.fail()),false);f.gate.fail();
});

for(const payload of [{...resultMessage(),displayOnly:false},{...resultMessage(),deviceEnrolled:false},
  ...[30,3601,'300',NaN,Infinity,null,true].map(remainingSeconds=>({...resultMessage(),remainingSeconds})),
  {...resultMessage(),token:'PRIVATE'}, {...resultMessage(),ticket:'c'.repeat(65)},
  {...selectedMessage(),remainingSeconds:300},{...resultMessage(),action:'logout-begin'}])
test('probe payload is exact and bounded: '+JSON.stringify(payload),()=>{
  const f=fixture();f.gate.prepareContinuationProbe(probeOrigin);
  assert.equal(f.callbacks.message(payload,probeSender(),()=>assert.fail()),false);f.gate.fail();
});

test('queued probe packets copy caller state and share the existing capacity',async()=>{
  const f=fixture(),scoped=f.gate.prepareContinuationProbe(probeOrigin),payload=resultMessage(),s=probeSender();
  const seen=[];f.callbacks.message(payload,s,()=>{});
  payload.remainingSeconds=2;s.documentId='changed';s.tab.id=8;
  scoped.runtime.onMessage.addListener((m,s,r)=>{seen.push([m,s]);r({ok:true});});f.gate.open();
  assert.equal(seen[0][0].remainingSeconds,300);assert.equal(seen[0][1].documentId,'doc-1');
  assert.equal(seen[0][1].tab.id,7);
  const g=fixture();g.gate.prepareContinuationProbe(probeOrigin);let refused=0;
  for(let i=0;i<65;i++)g.callbacks.message(resultMessage(),probeSender(),r=>{
    assert.equal(r.mode,'setup_error');refused++;
  });
  await assert.rejects(g.gate.deadline);assert.equal(refused,65);assert.throws(()=>g.gate.open());
});

test('probe delegates are single-owner, removable, and cannot revive after gate failure',()=>{
  const f=fixture(),scoped=f.gate.prepareContinuationProbe(probeOrigin),seen=[];
  const first=()=>seen.push('first'),second=()=>seen.push('second');f.gate.open();
  scoped.runtime.onMessage.addListener(first);scoped.runtime.onMessage.addListener(first);
  assert.throws(()=>scoped.runtime.onMessage.addListener(second));
  scoped.runtime.onMessage.removeListener(second);
  f.callbacks.message(selectedMessage(),probeSender(),()=>{});
  scoped.runtime.onMessage.removeListener(first);scoped.runtime.onMessage.addListener(second);
  f.callbacks.message(selectedMessage(),probeSender(),()=>{});assert.deepEqual(seen,['first','second']);
  scoped.tabs.onUpdated.addListener(first);assert.throws(()=>scoped.tabs.onUpdated.addListener(second));
  scoped.tabs.onUpdated.removeListener(first);scoped.tabs.onUpdated.addListener(second);
  f.gate.fail();assert.throws(()=>scoped.runtime.onMessage.addListener(first));
  f.callbacks.message(resultMessage(),probeSender(),()=>{});assert.deepEqual(seen,['first','second']);
});

test('probe navigation is separate from startup tabs and does not retain other URLs',()=>{
  const f=fixture(),scoped=f.gate.prepareContinuationProbe(probeOrigin),probe=[],ordinary=[];
  f.gate.chrome.tabs.onUpdated.addListener((...args)=>ordinary.push(args));f.gate.open();
  scoped.tabs.onUpdated.addListener((...args)=>probe.push(args));
  f.callbacks.tab(7,{status:'loading',url:'https://elsewhere.example/PRIVATE'},{});
  assert.deepEqual(probe,[[7,{status:'loading',url:''}]]);assert.deepEqual(ordinary,[]);
  f.callbacks.tab(7,{title:'PRIVATE'},{});f.callbacks.tab(-1,{status:'loading'},{});
  assert.equal(probe.length,1);
  f.callbacks.tab(4,{status:'complete'},{id:4,incognito:false,status:'complete',url:url('startup.html')});
  assert.equal(probe.length,2);assert.equal(ordinary.length,1);
});

function joinedProbe(origin=probeOrigin) {
  const f=fixture(),scoped=f.gate.prepareContinuationProbe(origin);
  f.removed=[];f.sends=[];
  const tab={id:7,incognito:false,status:'complete',url:origin+'/device-display'};
  Object.assign(f.chrome.tabs,{create:async()=>({...tab}),get:async()=>({...tab}),
    remove:async n=>f.removed.push(n),sendMessage:async(n,payload,options)=>{
      f.sends.push([n,payload,options]);let response;
      if(payload.action==='continuation-probe-select') {
        f.callbacks.message({action:'continuation-probe-selected',ticket:payload.ticket},
          probeSender(origin),v=>{response=v;});
        return {selected:response?.ok===true};
      }
      if(payload.action==='continuation-probe-verify') {
        f.duringVerify?.();
        f.callbacks.message({...resultMessage(),ticket:payload.ticket},
          probeSender(origin),v=>{response=v;});
        return {verified:response?.ok===true};
      }
      return {current:true};
    }});
  f.gate.open();
  f.probe=createContinuationProbe(scoped,origin);
  return f;
}

for(const origin of [probeOrigin,'https://display.example','https://[::1]:8443'])
test('actual probe can register after gate opening and complete through fixed ingress: '+origin,async()=>{
  const f=joinedProbe(origin), selected=await f.probe.open(),proof=await f.probe.verify();
  assert.equal(selected.documentId,'doc-1');assert.equal(proof.url,origin+'/device-display');
  assert.equal(proof.displayOnly,true);assert.equal(f.sends.length,3);
  f.probe.close();assert.deepEqual(f.removed,[7]);
  assert.deepEqual(Object.keys(f.callbacks),['message','startup','installed','alarm','tab']);
});

for(const kind of ['loading','url','failed-gate','other-tab'])
test('actual in-flight probe reacts to gate navigation and failure: '+kind,async()=>{
  const f=joinedProbe();await f.probe.open();
  f.duringVerify=()=>{
    if(kind==='failed-gate')f.gate.fail();
    else f.callbacks.tab(kind==='other-tab'?44:7,
      kind==='url'?{url:'https://elsewhere.example/'}:{status:'loading'},{});
  };
  if(kind==='other-tab')assert.equal((await f.probe.verify()).displayOnly,true);
  else await assert.rejects(f.probe.verify(),/unconfirmed/);
  f.probe.close();assert.deepEqual(f.removed,[7]);
});
