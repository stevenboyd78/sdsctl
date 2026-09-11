import test from 'node:test';
import assert from 'node:assert/strict';
import {connectContinuationProbeContent,createContinuationProbe} from
  '../../src/sds200/browser_assets/browser_device_continuation_probe.mjs';

const id='a'.repeat(32),origin='https://192.0.2.18:8443',doc='12345678-1234-1234-1234-123456789abc';
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {resolve,promise};};
function event() {
  const listeners=new Set();
  return {listeners,addListener:f=>listeners.add(f),removeListener:f=>listeners.delete(f),
    emit:(...args)=>{for(const f of [...listeners])f(...args);}};
}
function message(event,payload,sender) {
  return new Promise((resolve,reject)=>{
    let held=false,answered=false;
    for(const listener of event.listeners) {
      held=listener(structuredClone(payload),structuredClone(sender),value=>{
        if(!answered){answered=true;resolve(structuredClone(value));}
      })===true||held;
    }
    if(!held&&!answered)reject(Error('No receiving document'));
  });
}
function fixture(selectedOrigin=origin) {
  const f={now:100000,mono:1000,removed:[],requests:[],sends:[],created:[],contentSends:[],
    meta:{},body:{display_only:true,device_enrolled:true,remaining_seconds:300}};
  f.tab={id:12,url:selectedOrigin+'/device-display',status:'complete',incognito:false};
  f.window={location:{href:f.tab.url},handlers:{},addEventListener(name,fn){this.handlers[name]=fn;}};
  f.window.top=f.window;
  const workerEvents=event(),contentEvents=event(),updated=event();
  f.chrome={runtime:{id,onMessage:workerEvents},tabs:{onUpdated:updated,
    create:async options=>{f.created.push(options);return {...f.tab};},
    get:async target=>{assert.equal(target,12);return {...f.tab};},
    remove:async target=>{f.removed.push(target);},
    sendMessage:async(target,payload,options)=>{
      f.sends.push({target,payload,options});assert.equal(target,12);
      if(options.documentId&&options.documentId!==doc)throw Error('Different document');
      return message(contentEvents,payload,{id});
    }}};
  f.runtime={id,onMessage:contentEvents,sendMessage:async payload=>{
    f.contentSends.push(payload);
    return message(workerEvents,{...payload,...f.payload},
      {id,frameId:0,documentId:doc,documentLifecycle:'active',origin:selectedOrigin,
        url:f.tab.url,tab:{...f.tab},...f.meta});
  }};
  f.fetcher=async(...args)=>{
    f.requests.push(args);return {status:200,redirected:false,url:selectedOrigin+'/auth/session',
      headers:new Headers({'content-type':'application/json'}),
      body:new Response(typeof f.body==='string'||f.body instanceof Uint8Array?f.body:JSON.stringify(f.body)).body,
      ...f.response};
  };
  f.abort=new AbortController();
  connectContinuationProbeContent({window:f.window,runtime:f.runtime,fetcher:(...args)=>f.fetcher(...args)},selectedOrigin);
  f.probe=createContinuationProbe(f.chrome,selectedOrigin,
    {signal:f.abort.signal,wall:()=>f.now,monotonic:()=>f.mono});
  f.updated=updated;f.workerEvents=workerEvents;f.contentEvents=contentEvents;
  return f;
}

for(const selectedOrigin of [origin,'https://display.example','https://[::1]:8443']) {
  test('select actual sender document then fixed protected fetch: '+selectedOrigin,async()=>{
    const f=fixture(selectedOrigin);
    assert.equal(f.created.length,0);assert.equal(f.requests.length,0);
    const selected=await f.probe.open();
    assert.equal(selected.documentId,doc);assert.equal(selected.tabId,12);
    assert.match(selected.ticket,/^[a-f0-9]{64}$/);assert(Object.isFrozen(selected));
    assert.equal(f.requests.length,0);
    const proof=await f.probe.verify();
    assert.deepEqual(proof,{url:selectedOrigin+'/device-display',...selected,
      displayOnly:true,deviceEnrolled:true,remainingSeconds:300});
    assert(Object.isFrozen(proof));
    assert.deepEqual(f.sends.map(x=>x.options),[{frameId:0},{documentId:doc},{documentId:doc}]);
    assert.equal(f.requests.length,1);assert.equal(f.requests[0][0],selectedOrigin+'/auth/session');
    assert.deepEqual({...f.requests[0][1],signal:null},{method:'GET',mode:'same-origin',
      credentials:'same-origin',cache:'no-store',redirect:'error',headers:{Accept:'application/json'},signal:null});
    assert(f.requests[0][1].signal instanceof AbortSignal);
    await assert.rejects(f.probe.verify(),/unconfirmed/);
    f.probe.close();assert.deepEqual(f.removed,[12]);assert.equal(f.workerEvents.listeners.size,0);
    assert.equal(f.updated.listeners.size,0);assert.equal(f.requests.length,1);
  });
}
for(const value of ['http://192.0.2.18','https://display.example/','https://DISPLAY.example',
  'https://user:password@display.example','https://display.example/path','https://display.example#x',null]) {
  test('invalid selected origin is inert: '+value,()=>{
    assert.throws(()=>createContinuationProbe({},value));
    assert.throws(()=>connectContinuationProbeContent({},value));
  });
}
for(const meta of [{id:'b'.repeat(32)},{frameId:1},{documentLifecycle:'prerender'},
  {documentId:''},{documentId:null},{documentId:'bad:document'},{origin:'https://elsewhere.example'},
  {url:origin+'/device-display?x=1'},{tab:{id:13,incognito:false}},{tab:{id:12,incognito:true}}]) {
  test('selection rejects mismatched Chrome sender '+JSON.stringify(meta),async()=>{
    const f=fixture();f.meta=meta;
    await assert.rejects(f.probe.open(),/unconfirmed/);
    assert.deepEqual(f.removed,[12]);assert.equal(f.requests.length,0);
  });
}
for(const meta of [{documentId:'another-document'},{frameId:1},{documentLifecycle:'cached'},
  {tab:{id:999,incognito:false}},{url:origin+'/login'}]) {
  test('result cannot replace selected document '+JSON.stringify(meta),async()=>{
    const f=fixture();await f.probe.open();f.meta=meta;
    await assert.rejects(f.probe.verify(),/unconfirmed/);
    assert.equal(f.requests.length,1);assert.deepEqual(f.removed,[12]);
  });
}
for(const body of [{display_only:false,device_enrolled:true,remaining_seconds:300},
  {display_only:true,device_enrolled:false,remaining_seconds:300},
  ...[0,30,3601,'300',null].map(remaining_seconds=>({display_only:true,device_enrolled:true,remaining_seconds})),
  {display_only:true,device_enrolled:true,remaining_seconds:300,token:'PRIVATE'},
  '{"display_only":false,"display_only":true,"device_enrolled":true,"remaining_seconds":300}',
  'null','[]','<html>login</html>',' '.repeat(1025),new Uint8Array([255,254])]) {
  test('invalid protected body refuses one attempt '+JSON.stringify(body).slice(0,90),async()=>{
    const f=fixture();f.body=body;await f.probe.open();
    await assert.rejects(f.probe.verify(),/unconfirmed/);
    await assert.rejects(f.probe.verify(),/unconfirmed/);
    assert.equal(f.requests.length,1);assert.deepEqual(f.removed,[12]);
  });
}
for(const response of [{status:401},{redirected:true},{url:origin+'/login'},
  {headers:new Headers({'content-type':'text/html'})},{body:null}]) {
  test('invalid protected response '+JSON.stringify(response),async()=>{
    const f=fixture();f.response=response;await f.probe.open();
    await assert.rejects(f.probe.verify(),/unconfirmed/);assert.equal(f.requests.length,1);
  });
}
for(const kind of ['abort','navigate','pagehide','wall-back','mono-back','wall-expiry','mono-expiry',
  'wall-nan','mono-string','life-expiry']) {
  test('in-flight cancellation and elapsed checks: '+kind,async()=>{
    const f=fixture();await f.probe.open();const original=f.fetcher;
    f.fetcher=async(...args)=>{
      if(kind==='abort')f.abort.abort();
      if(kind==='navigate')f.updated.emit(12,{status:'loading'});
      if(kind==='pagehide')f.window.handlers.pagehide();
      if(kind==='wall-back')f.now--;
      if(kind==='mono-back')f.mono--;
      if(kind==='wall-expiry')f.now+=15000;
      if(kind==='mono-expiry')f.mono+=15000;
      if(kind==='wall-nan')f.now=NaN;
      if(kind==='mono-string')f.mono='bad';
      if(kind==='life-expiry'){f.body.remaining_seconds=31;f.mono+=1000;}
      return original(...args);
    };
    await assert.rejects(f.probe.verify(),/unconfirmed/);
    assert.equal(f.requests.length,1);assert.deepEqual(f.removed,[12]);
  });
}
test('abort rejects a pending creation; its late tab is closed exactly once',async()=>{
  const f=fixture(),held=deferred();f.chrome.tabs.create=()=>held.promise;
  const result=assert.rejects(f.probe.open(),/unconfirmed/);
  f.abort.abort();await result;assert.deepEqual(f.removed,[]);
  held.resolve({...f.tab});await settle();assert.deepEqual(f.removed,[12]);
  f.probe.close();assert.deepEqual(f.removed,[12]);
});
test('creation completing after elapsed deadline is still cleaned up',async()=>{
  const f=fixture();f.chrome.tabs.create=async()=>{f.mono+=15000;return {...f.tab};};
  await assert.rejects(f.probe.open(),/unconfirmed/);assert.deepEqual(f.removed,[12]);
});
test('close interrupts a pending document response without awaiting Chrome completion',async()=>{
  const f=fixture(),held=deferred();await f.probe.open();f.chrome.tabs.sendMessage=()=>held.promise;
  const result=assert.rejects(f.probe.verify(),/unconfirmed/);await settle();
  f.probe.close();await result;held.resolve({verified:true});await settle();
  assert.deepEqual(f.removed,[12]);assert.equal(f.requests.length,0);
});
test('a fabricated sendMessage return is not a document-bound result',async()=>{
  const f=fixture();await f.probe.open();f.chrome.tabs.sendMessage=async()=>({verified:true});
  await assert.rejects(f.probe.verify(),/unconfirmed/);assert.equal(f.requests.length,0);
});
test('a replaced exact-URL document after fetch is never adopted',async()=>{
  const f=fixture();await f.probe.open();const send=f.chrome.tabs.sendMessage;
  f.chrome.tabs.sendMessage=async(...args)=>{
    if(args[1].action==='continuation-probe-confirm')throw Error('Document replaced');
    return send(...args);
  };
  await assert.rejects(f.probe.verify(),/unconfirmed/);assert.equal(f.requests.length,1);
});
test('other tabs cannot cancel the selected probe',async()=>{
  const f=fixture();await f.probe.open();f.updated.emit(99,{status:'loading'});
  assert.equal((await f.probe.verify()).displayOnly,true);f.probe.close();
});
test('one-use probe refuses a second open and unselected verification',async()=>{
  const f=fixture();await assert.rejects(f.probe.verify(),/unconfirmed/);
  await assert.rejects(f.probe.open(),/unconfirmed/);assert.equal(f.created.length,0);
  const g=fixture();await g.probe.open();await assert.rejects(g.probe.open(),/unconfirmed/);
  assert.equal(g.created.length,1);assert.deepEqual(g.removed,[12]);
});

for(const state of [{url:origin+'/login'},{pendingUrl:origin+'/login'},{incognito:true},
  {id:44},{url:origin+'/device-display',status:'loading'}]) {
  test('selected page change fails closed '+JSON.stringify(state),async()=>{
    const f=fixture();await f.probe.open();Object.assign(f.tab,state);
    await assert.rejects(f.probe.verify(),/unconfirmed/);assert.equal(f.requests.length,0);
    assert.deepEqual(f.removed,[12]);
  });
}
test('initial loading wait selects one document and makes no premature fetch',async()=>{
  const f=fixture();let reads=0;
  f.chrome.tabs.get=async()=>({...f.tab,status:++reads===1?'loading':'complete'});
  await f.probe.open();assert(reads>=2);assert.equal(f.requests.length,0);
  assert.equal((await f.probe.verify()).displayOnly,true);f.probe.close();
});
test('in-flight open cancellation cannot admit its late selected response',async()=>{
  const f=fixture(),held=deferred();f.chrome.tabs.sendMessage=()=>held.promise;
  const result=assert.rejects(f.probe.open(),/unconfirmed/);await settle();
  f.probe.close();await result;held.resolve({selected:true});await settle();
  assert.deepEqual(f.removed,[12]);assert.equal(f.requests.length,0);
});
test('synchronous cleanup failure stays sanitized and cannot repeat removal',async()=>{
  const f=fixture();await f.probe.open();let removes=0;
  f.chrome.tabs.remove=()=>{removes++;throw Error('PRIVATE platform detail');};
  f.chrome.runtime.onMessage.removeListener=()=>{throw Error('PRIVATE platform detail');};
  f.chrome.tabs.onUpdated.removeListener=()=>{throw Error('PRIVATE platform detail');};
  f.probe.close();f.probe.close();assert.equal(removes,1);
  await assert.rejects(f.probe.verify(),error=>error.message==='Browser display verification is unconfirmed.');
});
test('receiver installation failure unregisters the already-installed receiver',()=>{
  const f=fixture();f.probe.close();
  f.chrome.tabs.onUpdated.addListener=()=>{throw Error('PRIVATE platform detail');};
  assert.throws(()=>createContinuationProbe(f.chrome,origin),/unconfirmed/);
  assert.equal(f.workerEvents.listeners.size,0);assert.equal(f.created.length,0);
});
test('short server lifetime is reduced by whole probe duration',async()=>{
  const f=fixture();await f.probe.open();f.body.remaining_seconds=50;f.now+=1000;f.mono+=2000;
  assert.equal((await f.probe.verify()).remainingSeconds,48);f.probe.close();
});
test('supervising browser deadline rejects a hung API and cleans its late tab',async()=>{
  const f=fixture(),held=deferred(),timers=new Set();f.probe.close();
  f.chrome.tabs.create=()=>held.promise;
  f.probe=createContinuationProbe(f.chrome,origin,{wall:()=>f.now,monotonic:()=>f.mono,
    schedule:(callback,ms)=>{assert(ms<=15000);timers.add(callback);return callback;},
    cancel:callback=>timers.delete(callback)});
  const result=assert.rejects(f.probe.open(),/unconfirmed/);
  assert.equal(timers.size,1);[...timers][0]();await result;
  assert.equal(timers.size,0);held.resolve({...f.tab});await settle();
  assert.deepEqual(f.removed,[12]);
});
