import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkerEventGate} from '../../src/sds200/browser_assets/browser_device_worker_gate.mjs';

const id='a'.repeat(32), url=page=>`chrome-extension://${id}/${page}`;
const sender=()=>({id,url:url('setup.html'),frameId:0,documentLifecycle:'active',
  documentId:'doc-1',tab:{id:7,incognito:false}});
function fixture() {
  const callbacks={}, cancelled=[];let now=100, expiry;
  const event=name=>({addListener:fn=>{assert(!callbacks[name]);callbacks[name]=fn;}});
  const chrome={runtime:{id,onMessage:event('message'),onStartup:event('startup'),
    onInstalled:event('installed')},alarms:{onAlarm:event('alarm')},tabs:{onUpdated:event('tab')}};
  const gate=createWorkerEventGate(chrome,()=>now,fn=>{expiry=fn;return 9;},n=>cancelled.push(n));
  return {gate,callbacks,cancelled,advance:n=>{now+=n;},expire:()=>expiry()};
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
