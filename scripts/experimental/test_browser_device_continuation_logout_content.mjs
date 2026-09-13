import test from 'node:test';
import assert from 'node:assert/strict';
import {connectContinuationLogoutContent,submitDeviceLogout} from '../../src/sds200/browser_assets/browser_device_logout.mjs';

// Real receiver and strict HTTP parser; modeled runtime/document/fetch only.
// This does not prove Chrome MessageSender identity, same-origin browser headers,
// native authority, physical consent, or a selected ordinary worker route.
const id='a'.repeat(32),ticket='b'.repeat(64);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});
  return {promise,resolve,reject};};
const denied={message:'Browser sign-out document is unconfirmed.'};

function fixture(origin='https://192.0.2.18:8443') {
  const f={origin,clock:[1000000,500000],timers:new Set(),sent:[],calls:[],events:new Map(),listener:null,
    outcome:'drained',fetches:0,selectAck:{ok:true},resultAck:{ok:true},uiIntents:[]};
  f.call=async(name,fn)=>{
    const index=f.calls.length;f.calls.push(name);await f.before?.(name,index);
    const result=fn();await f.after?.(name,index);return result;
  };
  f.window={location:{href:origin+'/device-display'},
    dispatchEvent:event=>{assert.equal(event.type,'sdsctl-device-signout-intent');
      assert.equal(event.detail,undefined);f.uiIntents.push(event.type);f.onUIIntent?.();return true;},
    addEventListener:(name,fn)=>{assert.equal(name,'pagehide');assert(!f.events.has(name));f.events.set(name,fn);},
    removeEventListener:(name,fn)=>{assert.equal(f.events.get(name),fn);f.events.delete(name);}};
  f.window.top=f.window;
  f.runtime={id,onMessage:{addListener:fn=>{assert.equal(f.listener,null);f.listener=fn;},
    removeListener:fn=>{assert.equal(f.listener,fn);f.listener=null;}},
    sendMessage:message=>f.call(message.action,()=>{
      assert.deepEqual(Object.keys(message).sort(),message.action==='continuation-logout-selected'?
        ['action','ticket']:['action','outcome','ticket']);
      assert.equal(message.ticket,ticket);f.sent.push(structuredClone(message));
      if(message.action==='continuation-logout-selected')return f.selectAck;
      assert.equal(message.action,'continuation-logout-result');assert.equal(message.outcome,f.outcome);
      return f.resultAck;
    })};
  f.options={window:f.window,runtime:f.runtime,
    fetcher:(url,options)=>f.call('fetch',()=>{
      f.fetches++;f.signal=options.signal;assert.equal(options.signal.aborted,false);
      assert.equal(url,origin+'/auth/logout');assert.equal(options.method,'POST');
      assert.equal(options.mode,'same-origin');assert.equal(options.credentials,'same-origin');
      assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');
      assert.deepEqual(options.headers,{Accept:'application/json'});
      const body=JSON.stringify({version:1,device_logout:true,paused:true,drained:f.outcome==='drained'});
      const response=new Response(body,{status:f.outcome==='drained'?200:f.outcome==='pending'?202:500,
        headers:{'content-type':'application/json'}});
      Object.defineProperty(response,'url',{value:url});return response;
    }),wall:()=>f.clock[0],monotonic:()=>f.clock[1],
    schedule:(fn,ms)=>{assert.equal(ms,20000);const timer={fn};f.timers.add(timer);return timer;},
    cancel:timer=>f.timers.delete(timer)};
  f.start=()=>{f.owner=connectContinuationLogoutContent(f.options,origin);};
  f.ask=(action,value={},sender={id})=>new Promise(resolve=>{
    let replied=false;
    const handled=f.listener({action:'continuation-logout-'+action,ticket,...value},sender,
      result=>{replied=true;resolve(result);});
    if(handled!==true&&!replied)resolve(null);
  });
  f.tick=()=>{assert.equal(f.timers.size,1);for(const timer of f.timers)timer.fn();};
  return f;
}

for(const origin of ['https://display.example.test','https://192.0.2.18:8443','https://[2001:db8::18]:8443'])
  test('selected logout content requests UI quiescence before pending POST '+origin,async()=>{
    const f=fixture(origin);f.outcome='pending';f.start();
    assert.deepEqual(f.uiIntents,[]);
    assert.deepEqual(await f.ask('select'),{selected:true});
    assert.deepEqual(f.uiIntents,['sdsctl-device-signout-intent']);assert.equal(f.fetches,0);
    assert.deepEqual(await f.ask('submit'),{submitted:true});assert.equal(f.fetches,1);
    assert.deepEqual(await f.ask('confirm'),{current:true});
    assert.equal(f.uiIntents.length,1);f.owner.close();
  });

for(const selectAck of [{ok:false},{ok:true,extra:true}])
  test('unaccepted selection cannot emit the UI-only sign-out hint '+JSON.stringify(selectAck),async()=>{
    const f=fixture();f.selectAck=selectAck;f.start();
    assert.deepEqual(await f.ask('select'),{selected:false});
    assert.deepEqual(f.uiIntents,[]);assert.equal(f.fetches,0);f.owner.close();
  });
test('page loss during the UI-only hint refuses selection before POST',async()=>{
  const f=fixture();f.start();f.onUIIntent=()=>f.events.get('pagehide')();
  assert.deepEqual(await f.ask('select'),{selected:false});assert.equal(f.fetches,0);
  assert.equal(await f.ask('submit'),null);f.owner.close();
});
test('UI event return value is never a server or native acknowledgement',async()=>{
  const f=fixture();f.window.dispatchEvent=()=>false;f.start();
  assert.deepEqual(await f.ask('select'),{selected:true});assert.equal(f.fetches,0);
  assert.deepEqual(f.sent,[{action:'continuation-logout-selected',ticket}]);
  f.owner.close();
});
test('UI dispatch failure remains terminal with no POST',async()=>{
  const f=fixture();f.window.dispatchEvent=()=>{throw Error('fictional UI failure');};f.start();
  assert.deepEqual(await f.ask('select'),{selected:false});assert.equal(f.fetches,0);
  assert.equal(await f.ask('submit'),null);f.owner.close();
});

for(const origin of ['https://display.example.test','https://192.0.2.18:8443','https://[2001:db8::18]:8443'])
  for(const outcome of ['drained','pending'])test('single document handshake '+origin+' '+outcome,async()=>{
    const f=fixture(origin);f.outcome=outcome;f.start();assert.deepEqual(f.calls,[]);assert(Object.isFrozen(f.owner));
    assert.equal(await f.ask('submit'),null);assert.equal(f.fetches,0);
    assert.deepEqual(await f.ask('select'),{selected:true});assert.equal(f.fetches,0);
    assert.deepEqual(await f.ask('submit'),{submitted:true});assert.equal(f.fetches,1);
    assert.deepEqual(f.sent,[{action:'continuation-logout-selected',ticket},
      {action:'continuation-logout-result',ticket,outcome}]);
    assert.deepEqual(await f.ask('confirm'),{current:true});assert.equal(f.timers.size,0);
    const count=f.calls.length;for(const action of ['select','submit','confirm'])assert.equal(await f.ask(action),null);
    assert.equal(f.calls.length,count);f.owner.close();assert.equal(f.listener,null);assert.equal(f.events.size,0);
  });

for(const origin of ['http://display.example.test','https://display.example.test/',
  'https://user:password@display.example.test','https://display.example.test?q=1','not an origin'])
  test('invalid fixed origin '+origin+' has no registration/I/O',()=>{
    const f=fixture(origin);assert.throws(f.start,denied);assert.equal(f.listener,null);assert.equal(f.calls.length,0);
  });

for(const change of [f=>{f.window.top={};},f=>{f.window.location.href=f.origin+'/';},
  f=>{f.window.location.href+='?query';},f=>{f.runtime.id='z'.repeat(32);},
  f=>{f.options.fetcher=null;},f=>{f.options.wall=null;}])
  test('invalid document or port cannot register '+change,()=>{
    const f=fixture();change(f);assert.throws(f.start,denied);assert.equal(f.listener,null);assert.equal(f.calls.length,0);
  });

for(const value of [{ticket:'A'.repeat(64)},{ticket:'x'},{ticket:null},{extra:true},
  {origin:'https://attacker.test'},{documentId:'body-supplied-id'},
  {outcome:'drained'},{action:'logout-begin'}])
  test('page-like/extra payload rejected '+JSON.stringify(value),async()=>{
    const f=fixture();f.start();assert.equal(await f.ask('select',value),null);
    assert.deepEqual(f.calls,[]);f.owner.close();
  });

for(const sender of [{},null,{id:'b'.repeat(32)}])test('wrong sender cannot select '+JSON.stringify(sender),async()=>{
  const f=fixture();f.start();assert.equal(await f.ask('select',{},sender),null);assert.deepEqual(f.calls,[]);f.owner.close();
});

for(const action of ['select','submit','confirm','cancel'])test('wrong ticket cannot steal '+action,async()=>{
  const f=fixture();f.start();await f.ask('select');const count=f.calls.length;
  assert.equal(await f.ask(action,{ticket:'c'.repeat(64)}),null);assert.equal(f.calls.length,count);
  assert.deepEqual(await f.ask('submit'),{submitted:true});f.owner.close();
});

for(const field of ['selectAck','resultAck'])for(const value of [null,{},false,{ok:false},{ok:true,extra:true},
  Object.defineProperty({ok:true},'hidden',{value:true}),{ok:true,[Symbol('extra')]:true}])
  test(field+' malformed/uncertain acknowledgement '+String(value),async()=>{
    const f=fixture();f[field]=value;f.start();const selected=await f.ask('select');
    if(field==='selectAck')assert.deepEqual(selected,{selected:false});
    else {assert.deepEqual(selected,{selected:true});assert.deepEqual(await f.ask('submit'),{submitted:false});}
    assert.equal(f.fetches,field==='selectAck'?0:1);const count=f.calls.length;
    assert.equal(await f.ask('select'),null);assert.equal(await f.ask('submit'),null);
    assert.equal(f.calls.length,count);assert.equal(f.timers.size,0);f.owner.close();
  });

test('unknown HTTP outcome sends no result and cannot be upgraded',async()=>{
  const f=fixture();f.outcome='unconfirmed';f.start();await f.ask('select');
  assert.deepEqual(await f.ask('submit'),{submitted:false});assert.equal(f.sent.length,1);
  f.outcome='drained';assert.equal(await f.ask('submit'),null);assert.equal(f.fetches,1);f.owner.close();
});

for(const change of [f=>{f.events.get('pagehide')();},f=>{f.window.location.href=f.origin+'/login';},
  f=>{f.window.top={};},f=>{f.runtime.id='b'.repeat(32);},f=>f.tick()])
  test('lost/expired document before submit cannot fetch '+change,async()=>{
    const f=fixture();f.start();await f.ask('select');change(f);
    assert.equal(await f.ask('submit'),null);assert.equal(f.fetches,0);assert.equal(f.timers.size,0);f.owner.close();
  });

for(const [when,name] of ['before','after'].flatMap(w=>
  ['continuation-logout-selected','fetch','continuation-logout-result'].map(n=>[w,n])))
  for(const interruption of ['cancel','pagehide','timeout','close'])
    test(when+' '+name+' '+interruption+' cannot act on late completion',async()=>{
      const f=fixture(),entered=deferred(),held=deferred();f.start();
      f[when]=async current=>{if(current===name){entered.resolve();await held.promise;}};
      const run=(async()=>{const r=await f.ask('select');return r.selected?f.ask('submit'):r;})();
      await entered.promise;
      if(interruption==='cancel')assert.deepEqual(await f.ask('cancel'),{cancelled:true});
      if(interruption==='pagehide')f.events.get('pagehide')();
      if(interruption==='timeout')f.tick();
      if(interruption==='close')f.owner.close();
      assert.deepEqual(await run,name==='continuation-logout-selected'?{selected:false}:{submitted:false});
      const count=f.calls.length;held.resolve();await settle();await settle();assert.equal(f.calls.length,count);
      assert(f.fetches<=1);if(f.signal)assert.equal(f.signal.aborted,true);assert.equal(f.timers.size,0);
      if(interruption!=='close'){assert.equal(await f.ask('submit'),null);f.owner.close();}
    });

for(const field of [0,1])for(const value of [-1,NaN,Infinity,Number.MAX_SAFE_INTEGER,20000])
  test('selection-to-submit budget clock '+field+' '+value+' is never renewed',async()=>{
    const f=fixture();f.start();await f.ask('select');
    f.clock[field]=value===20000?f.clock[field]+value:value;
    assert.equal(await f.ask('submit'),null);assert.equal(f.fetches,0);assert.equal(f.timers.size,0);f.owner.close();
  });

for(const field of [0,1])test('late result at whole deadline on clock '+field+' cannot acknowledge',async()=>{
  const f=fixture();f.start();await f.ask('select');f.clock[field]+=19000;
  f.after=name=>{if(name==='fetch')f.clock[field]+=1000;};
  assert.deepEqual(await f.ask('submit'),{submitted:false});assert.equal(f.sent.length,1);f.owner.close();
});

test('outstanding submission is consumed before any await',async()=>{
  const f=fixture(),entered=deferred(),held=deferred();f.start();await f.ask('select');
  f.before=async name=>{if(name==='fetch'){entered.resolve();await held.promise;}};
  const first=f.ask('submit');await entered.promise;const count=f.calls.length;
  assert.equal(await f.ask('submit'),null);assert.equal(await f.ask('select'),null);assert.equal(f.calls.length,count);
  held.resolve();assert.deepEqual(await first,{submitted:true});assert.equal(f.fetches,1);f.owner.close();
});

test('failed final timer cleanup refuses document confirmation',async()=>{
  const g=fixture();let fail=false;
  g.options.cancel=timer=>{g.timers.delete(timer);if(fail)throw Error('private timer');};
  g.start();await g.ask('select');await g.ask('submit');fail=true;
  assert.deepEqual(await g.ask('confirm'),{current:false});assert.equal(g.timers.size,0);g.owner.close();
});

test('pre-aborted logout signal never calls fetch',async()=>{
  const abort=new AbortController();abort.abort();let called=false;
  assert.equal(await submitDeviceLogout(async()=>{called=true;},'https://display.example.test',
    {signal:abort.signal}),'unconfirmed');assert.equal(called,false);
});

test('parser refuses a late completed reply after external abort',async()=>{
  const f=fixture(),abort=new AbortController();f.after=name=>{if(name==='fetch')abort.abort();};
  assert.equal(await submitDeviceLogout(f.options.fetcher,f.origin,{signal:abort.signal}),'unconfirmed');
  assert.equal(f.signal.aborted,true);assert.equal(f.fetches,1);
});
