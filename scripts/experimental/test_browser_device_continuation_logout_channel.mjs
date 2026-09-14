import test from 'node:test';
import assert from 'node:assert/strict';
import {createContinuationLogout,connectContinuationLogoutContent} from '../../src/sds200/browser_assets/browser_device_logout.mjs';
import {createWorkerEventGate} from '../../src/sds200/browser_assets/browser_device_worker_gate.mjs';
import {createContinuationStopOwner} from '../../src/sds200/browser_assets/browser_device_continuation_stop.mjs';
import {fingerprintContinuationCookie} from '../../src/sds200/browser_assets/browser_device_continuation_cookie.mjs';

// Real event gate, worker channel, receiver and HTTP parser. Chrome's document
// identity, browser fetch and HTTPS response remain modeled, not real-browser
// or physical-consent qualification. No installed role selects this channel.
const id='a'.repeat(32),originDefault='https://192.0.2.18:8443';
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const denied={message:'Browser sign-out channel is unconfirmed.'};

function fixture(origin=originDefault) {
  const f={origin,url:origin+'/device-display',calls:[],timers:new Set(),events:{},clock:[1000000,500000],
    tab:{id:17,incognito:false,status:'complete',url:origin+'/device-display'},document:'document-1',
    removed:[],http:0,outcome:'drained',ordinary:0,content:null,sent:[],uiQuiesced:false};
  const event=name=>({addListener:fn=>{assert(!f.events[name]);f.events[name]=fn;}});
  const forbidden=()=>assert.fail('no cookie/storage/native authority in transport');
  f.call=async(name,fn)=>{
    const index=f.calls.length;f.calls.push(name);await f.before?.(name,index);
    const result=fn();await f.after?.(name,index);return result;
  };
  f.options={wall:()=>f.clock[0],monotonic:()=>f.clock[1],
    schedule:(fn,ms)=>{assert([100,10000,12000,20000,30000,45000].includes(ms));
      const timer={fn,ms};f.timers.add(timer);return timer;},
    cancel:timer=>f.timers.delete(timer)};
  const ask=(listener,message,sender)=>new Promise(resolve=>{
    let replied=false;const held=listener(message,sender,value=>{replied=true;resolve(value);});
    if(held!==true&&!replied)resolve(null);
  });
  f.chrome={runtime:{id,onMessage:event('message'),onStartup:event('startup'),onInstalled:event('installed'),
    sendNativeMessage:forbidden},alarms:{onAlarm:event('alarm'),get:forbidden,clear:forbidden},
    storage:{local:{get:forbidden,set:forbidden}},cookies:{get:forbidden,set:forbidden,remove:forbidden},
    tabs:{onUpdated:event('tab'),create:value=>f.call('create',()=>{
      assert.deepEqual(value,{url:f.url,active:false});f.loadContent();return structuredClone(f.tab);
    }),get:target=>f.call('page',()=>{assert.equal(target,17);return structuredClone(f.tab);}),
    sendMessage:(target,message,selection)=>f.call(message.action,()=>{
      assert.equal(target,17);assert.deepEqual(Object.keys(message).sort(),['action','ticket']);
      assert.match(message.ticket,/^[a-f0-9]{64}$/);f.ticket=message.ticket;f.sent.push(structuredClone(message));
      if(message.action==='continuation-logout-select')assert.deepEqual(selection,{frameId:0});
      else {assert.deepEqual(selection,{documentId:'document-1'});
        if(selection.documentId!==f.document)throw Error('document no longer exists');}
      if(f.fakeReply)return f.fakeReply(message);
      return ask(f.content.listener,message,{id});
    }),remove:target=>f.call('remove',()=>{
      assert.equal(target,17);f.removed.push(target);f.content?.pagehide?.();
    })}};
  f.loadContent=()=>{
    const content={document:f.document,listener:null,pagehide:null};f.content=content;
    const window={location:{href:f.url},addEventListener:(name,fn)=>{assert.equal(name,'pagehide');content.pagehide=fn;},
      dispatchEvent:event=>{assert.equal(event.type,'sdsctl-device-signout-intent');
        assert.equal(event.detail,undefined);f.uiQuiesced=true;return true;},
      removeEventListener:()=>{content.pagehide=null;}};window.top=window;content.window=window;
    const runtime={id,onMessage:{addListener:fn=>{content.listener=fn;},removeListener:()=>{content.listener=null;}},
      sendMessage:message=>f.call(message.action,()=>{
        const sender={id,origin,url:f.url,frameId:0,documentLifecycle:'active',documentId:content.document,
          tab:{id:17,incognito:false}};
        return ask(f.events.message,f.changeMessage?f.changeMessage(message):message,
          f.changeSender?f.changeSender(sender,message):sender);
      })};
    content.owner=connectContinuationLogoutContent({...f.options,window,runtime,
      fetcher:(url,options)=>f.call('fetch',()=>{
        assert.equal(url,origin+'/auth/logout');assert.equal(options.mode,'same-origin');
        assert.equal(options.credentials,'same-origin');assert.equal(options.method,'POST');
        assert.deepEqual(options.headers,{Accept:'application/json'});f.http++;f.fetchSignal=options.signal;
        const result=new Response(JSON.stringify({version:1,device_logout:true,paused:true,
          drained:f.outcome==='drained'}),{status:f.outcome==='drained'?200:202,
          headers:{'content-type':'application/json'}});
        Object.defineProperty(result,'url',{value:url});return result;
      })},origin);
  };
  f.start=()=>{
    f.gate=createWorkerEventGate(f.chrome,f.options.monotonic,f.options.schedule,f.options.cancel);
    assert.deepEqual(Object.keys(f.events),['message','startup','installed','alarm','tab']);
    f.scoped=f.gate.prepareContinuationLogout(origin);
    f.gate.chrome.runtime.onMessage.addListener(()=>{f.ordinary++;return false;});
    if(!f.deferChannel)f.owner=createContinuationLogout(f.scoped,origin,f.options);f.gate.open();
  };
  f.tick=async ms=>{
    const matches=[...f.timers].filter(t=>t.ms===ms);assert(matches.length>0);
    for(const t of matches){f.timers.delete(t);t.fn();}await settle();
  };
  return f;
}

for(const origin of ['https://display.example.test',originDefault,'https://[2001:db8::18]:8443'])
  test('delayed pending response retains its quiesced owned logout document '+origin,async()=>{
    const f=fixture(origin);f.outcome='pending';f.start();
    f.before=async name=>{
      if(name==='fetch'&&!f.uiQuiesced)f.events.tab(17,{status:'loading'});
    };
    assert.equal(await f.owner.run(),'pending');await settle();
    assert.equal(f.http,1);assert.equal(f.uiQuiesced,true);assert.deepEqual(f.removed,[17]);
  });

for(const origin of ['https://display.example.test',originDefault,'https://[2001:db8::18]:8443'])
  for(const outcome of ['drained','pending'])test('actual modeled gate/channel/content '+origin+' '+outcome,async()=>{
    const f=fixture(origin);f.outcome=outcome;f.start();assert.deepEqual(f.calls,[]);assert(Object.isFrozen(f.owner));
    assert.equal(await f.owner.run(),outcome);await settle();assert.equal(f.http,1);assert.deepEqual(f.removed,[17]);
    assert.equal(f.ordinary,0);assert.equal(f.timers.size,0);
    assert.deepEqual(f.sent.map(m=>m.action),['continuation-logout-select','continuation-logout-submit','continuation-logout-confirm']);
    const count=f.calls.length;await assert.rejects(f.owner.run(),denied);assert.equal(f.calls.length,count);
  });

for(const value of [undefined,{},true,'drained'])test('one-use channel rejects caller payload '+String(value),async()=>{
  const f=fixture();f.start();await assert.rejects(f.owner.run(value),denied);
  assert.deepEqual(f.calls,[]);await assert.rejects(f.owner.run(),denied);assert.equal(f.timers.size,0);
});

for(const origin of ['http://display.example.test','https://display.example.test/',
  'https://user:pass@display.example.test','https://display.example.test?x=1','not an origin'])
  test('channel rejects invalid fixed origin '+origin,()=>{
    const f=fixture();assert.throws(()=>createContinuationLogout(f.chrome,origin,f.options),denied);
    assert.deepEqual(f.calls,[]);assert.deepEqual(f.events,{});
  });

for(const signal of [null,{},true,AbortSignal.abort()])test('channel refuses invalid/pre-aborted signal',()=>{
  const f=fixture();assert.throws(()=>createContinuationLogout(f.chrome,f.origin,{...f.options,signal}),denied);
  assert.deepEqual(f.events,{});assert.deepEqual(f.calls,[]);
});

for(const change of ['frame','origin','url','extension','document','tab','incognito','lifecycle'])
  for(const at of ['selected','result'])test('rejects '+change+' in Chrome sender for '+at,async()=>{
    const f=fixture();f.start();
    f.changeSender=(sender,message)=>{
      if(message.action!=='continuation-logout-'+at)return sender;
      const v=structuredClone(sender);
      if(change==='frame')v.frameId=1;
      if(change==='origin')v.origin='https://foreign.test';
      if(change==='url')v.url=f.origin+'/';
      if(change==='extension')v.id='b'.repeat(32);
      if(change==='document')v.documentId=at==='result'?'replacement-document':'';
      if(change==='tab')v.tab.id=99;
      if(change==='incognito')v.tab.incognito=true;
      if(change==='lifecycle')v.documentLifecycle='cached';
      return v;
    };
    await assert.rejects(f.owner.run(),denied);await settle();assert.equal(f.http,at==='selected'?0:1);
    assert.deepEqual(f.removed,[17]);assert.equal(f.ordinary,0);
  });

for(const body of [m=>({...m,proof:true}),m=>({...m,ticket:'f'.repeat(64)}),
  m=>({...m,outcome:'drained'}),m=>({...m,documentId:'document-1'})])
  test('message bodies cannot supply selected document proof '+body,async()=>{
    const f=fixture();f.start();f.changeMessage=body;
    await assert.rejects(f.owner.run(),denied);await settle();assert.equal(f.http,0);assert.equal(f.ordinary,0);
  });

test('selection reply without actual Chrome sender selection is refused',async()=>{
  const f=fixture();f.start();f.fakeReply=()=>({selected:true});
  await assert.rejects(f.owner.run(),denied);await settle();assert.equal(f.http,0);assert.deepEqual(f.removed,[17]);
});

test('submitted reply without actual document-bound result is refused',async()=>{
  const f=fixture();f.start();const send=f.chrome.tabs.sendMessage;
  f.chrome.tabs.sendMessage=(target,message,selection)=>message.action==='continuation-logout-submit'?
    Promise.resolve({submitted:true}):send(target,message,selection);
  await assert.rejects(f.owner.run(),denied);await settle();assert.equal(f.http,0);
});

for(const action of ['select','submit','confirm'])test('unknown fields in '+action+' reply are not stripped',async()=>{
  const f=fixture();f.start();const send=f.chrome.tabs.sendMessage;
  f.chrome.tabs.sendMessage=async(target,message,selection)=>{
    const value=await send(target,message,selection);
    return message.action==='continuation-logout-'+action?{...value,extra:true}:value;
  };
  await assert.rejects(f.owner.run(),denied);await settle();assert.equal(f.http,action==='select'?0:1);
});

test('same-URL document replacement without a delivered navigation event cannot be adopted',async()=>{
  const f=fixture();f.start();f.after=name=>{if(name==='continuation-logout-result')f.document='document-2';};
  await assert.rejects(f.owner.run(),denied);await settle();assert.equal(f.http,1);assert.deepEqual(f.removed,[17]);
});

for(const stage of ['continuation-logout-selected','fetch','continuation-logout-result'])
  test('navigation at '+stage+' aborts selection/result without another POST',async()=>{
    const f=fixture();f.start();f.after=name=>{if(name===stage)
      f.events.tab(17,{url:'https://secret-other-site.test/path'},{});};
    await assert.rejects(f.owner.run(),denied);await settle();assert(f.http<=1);assert.deepEqual(f.removed,[17]);
    assert.equal(f.ordinary,0);assert.equal(f.timers.size,0);
  });

test('unrelated tab navigation does not cancel the owned document',async()=>{
  const f=fixture();f.start();f.after=name=>{if(name==='fetch')f.events.tab(99,{status:'loading'},{});};
  assert.equal(await f.owner.run(),'drained');assert.deepEqual(f.removed,[17]);
});

test('initial page loading may wait but never renews whole deadline',async()=>{
  const f=fixture();f.tab.status='loading';f.start();const run=f.owner.run();await settle();
  assert.equal(f.http,0);f.tab.status='complete';await f.tick(100);
  assert.equal(await run,'drained');await settle();assert.equal(f.timers.size,0);
});

test('initial page never completes: timeout closes only owned tab and cancels poll',async()=>{
  const f=fixture();f.tab.status='loading';f.start();const run=f.owner.run();void run.catch(()=>{});await settle();
  await f.tick(20000);await assert.rejects(run,denied);await settle();assert.deepEqual(f.removed,[17]);
  assert.equal(f.http,0);assert.equal(f.timers.size,0);
});

const baseline=fixture();baseline.start();assert.equal(await baseline.owner.run(),'drained');await settle();
for(const when of ['before','after'])for(const [cut,name] of baseline.calls.entries()) {
  if(name==='remove')continue;
  test(when+' abort at '+cut+' '+name+' leaves no late non-cleanup operation',async()=>{
    const f=fixture(),entered=deferred(),held=deferred(),abort=new AbortController();
    f.options.signal=abort.signal;f.start();
    f[when]=async(_name,index)=>{if(index===cut){entered.resolve();await held.promise;}};
    const run=f.owner.run();void run.catch(()=>{});await entered.promise;abort.abort();await assert.rejects(run,denied);
    const count=f.calls.length;held.resolve();await settle();await settle();
    assert(f.calls.slice(count).every(n=>n==='remove'));assert(f.http<=1);assert.equal(f.timers.size,0);
    assert.deepEqual(f.removed,[17]);await assert.rejects(f.owner.run(),denied);
  });
  test(when+' missing acknowledgement at '+cut+' '+name+' is terminal',async()=>{
    const f=fixture();f.start();f[when]=(_name,index)=>{if(index===cut)throw Error('private response');};
    await assert.rejects(f.owner.run(),denied);await settle();const count=f.calls.length;
    f[when]=null;await assert.rejects(f.owner.run(),denied);assert.equal(f.calls.length,count);
    assert(f.http<=1);assert.equal(f.timers.size,0);
  });
}

for(const field of [0,1])for(const change of [-1,NaN,Infinity,Number.MAX_SAFE_INTEGER,20000])
  test('whole deadline/clock fault '+field+' '+change+' refuses final receipt',async()=>{
    const f=fixture();f.start();f.after=name=>{if(name==='fetch')
      f.clock[field]=change===20000?f.clock[field]+change:change;};
    await assert.rejects(f.owner.run(),denied);await settle();assert.equal(f.http,1);
    assert.deepEqual(f.removed,[17]);assert.equal(f.timers.size,0);
  });

test('duplicate run while submit is pending cannot cancel or repeat the original',async()=>{
  const f=fixture(),entered=deferred(),held=deferred();f.start();
  f.before=async name=>{if(name==='fetch'){entered.resolve();await held.promise;}};
  const run=f.owner.run();await entered.promise;const count=f.calls.length;
  await assert.rejects(f.owner.run(),denied);assert.equal(f.calls.length,count);
  held.resolve();assert.equal(await run,'drained');assert.equal(f.http,1);
});

test('delivered whole deadline during POST aborts content without waiting for HTTP',async()=>{
  const f=fixture(),entered=deferred(),held=deferred();f.start();
  f.before=async name=>{if(name==='fetch'){entered.resolve();await held.promise;}};
  const run=f.owner.run();void run.catch(()=>{});await entered.promise;await f.tick(20000);
  await assert.rejects(run,denied);const count=f.calls.length;held.resolve();await settle();await settle();
  assert(f.calls.slice(count).every(n=>n==='remove'));assert.equal(f.timers.size,0);
});

test('gate snapshots private logout messages without leaking navigation destinations',()=>{
  const f=fixture(),gate=createWorkerEventGate(f.chrome,f.options.monotonic,f.options.schedule,f.options.cancel);
  const scoped=gate.prepareContinuationLogout(f.origin),messages=[],tabs=[];
  scoped.runtime.onMessage.addListener((m,s,r)=>{messages.push([m,s]);r({ok:true});});
  scoped.tabs.onUpdated.addListener((...args)=>tabs.push(args));
  gate.chrome.runtime.onMessage.addListener(()=>{f.ordinary++;});
  const message={action:'continuation-logout-selected',ticket:'b'.repeat(64)};
  const sender={id,url:f.url,origin:f.origin,frameId:0,documentLifecycle:'active',documentId:'document-1',
    tab:{id:17,incognito:false}};
  let ack;assert.equal(f.events.message(message,sender,r=>{ack=r;}),true);
  message.ticket='c'.repeat(64);sender.documentId='changed';sender.tab.id=99;
  f.events.tab(17,{url:'https://do-not-copy.test/private'},{});gate.open();
  assert.equal(messages[0][0].ticket,'b'.repeat(64));assert.equal(messages[0][1].documentId,'document-1');
  assert.equal(messages[0][1].tab.id,17);assert.deepEqual(ack,{ok:true});assert.equal(f.ordinary,0);
  assert.deepEqual(tabs,[[17,{url:''}]]);
});

test('unprepared logout messages never enter ordinary handlers',()=>{
  const f=fixture(),gate=createWorkerEventGate(f.chrome,f.options.monotonic,f.options.schedule,f.options.cancel);
  gate.chrome.runtime.onMessage.addListener(()=>{f.ordinary++;});gate.open();
  for(const origin of [f.origin,`chrome-extension://${id}`]) {
    const sender={id,url:origin===f.origin?f.url:origin+'/resume.html',origin,frameId:0,
      documentLifecycle:'active',documentId:'document-1',tab:{id:17,incognito:false}};
    assert.equal(f.events.message({action:'continuation-logout-selected',ticket:'b'.repeat(64)},sender,()=>{}),false);
  }
  assert.equal(f.ordinary,0);
});

test('private gate does not permit a second simultaneous logout listener owner',()=>{
  const f=fixture(),gate=createWorkerEventGate(f.chrome,f.options.monotonic,f.options.schedule,f.options.cancel);
  const scoped=gate.prepareContinuationLogout(f.origin);scoped.runtime.onMessage.addListener(()=>{});gate.open();
  assert.throws(()=>scoped.runtime.onMessage.addListener(()=>{}));
  assert.throws(()=>gate.prepareContinuationLogout(f.origin));
});

// Actual Stop owner -> actual scoped worker channel -> actual content receiver
// -> strict HTTP parser. Only Chrome/HTTP/native I/O remains modeled here. The
// comparison is fixture-owned, not a page-supplied grant or installed acceptance.
async function stopFixture(origin=originDefault) {
  const f=fixture(origin),build='b'.repeat(64),identity='c'.repeat(64),epoch='d'.repeat(64);
  const STOP='sdsctlContinuationStop',COOKIE='__Host-sdsctl-device-session';
  f.deferChannel=true;f.fenced=false;f.pendingWrites=0;f.unconfirmedWrite=false;f.stopWrites=0;f.pauseCalls=0;
  f.cookieRemoved=0;f.saved={};f.cookie={name:COOKIE,value:'sdsctl-browser-session-v1.'+'1'.repeat(64),
    domain:new URL(origin).hostname,hostOnly:true,path:'/',secure:true,httpOnly:true,sameSite:'strict',
    session:false,expirationDate:1300,storeId:'0'};
  f.fingerprint=await fingerprintContinuationCookie(origin,f.cookie);
  f.native={epoch,mode:'active',binding:{fingerprint:'e'.repeat(64),revision:6,generation:7}};
  f.current=()=>({version:1,ok:true,build,role:'continuation',
    config:{origin,identity,nativeHost:'org.sdsctl.browser_device'},extensionId:id,acknowledge:false,
    launch:null,continuation:structuredClone(f.native)});
  f.chrome.runtime.sendNativeMessage=async(host,request)=>{
    assert.equal(host,'org.sdsctl.browser_device');
    if(request.action==='continuation-current')return f.call('native-current',()=>f.current());
    assert.deepEqual(request,{version:1,action:'continuation-pause',epoch,
      binding:{fingerprint:f.native.binding.fingerprint,revision:f.native.binding.revision}});
    return f.call('native-pause',()=>{
      f.pauseCalls++;f.native={epoch,mode:'paused',binding:{fingerprint:'2'.repeat(64),
        revision:f.native.binding.revision+1,generation:null}};
      return {version:1,ok:true,build,identity,...structuredClone(f.native),nativePauseConfirmed:true,
        serverRevocationConfirmed:false};
    });
  };
  f.chrome.storage.local.setAccessLevel=async options=>{
    assert(f.fenced);assert.deepEqual(options,{accessLevel:'TRUSTED_CONTEXTS'});
  };
  f.chrome.storage.local.get=async key=>{
    assert.equal(key,STOP);return Object.hasOwn(f.saved,key)?{[key]:structuredClone(f.saved[key])}:{};
  };
  f.chrome.storage.local.set=async values=>{
    f.stopWrites++;assert.deepEqual(values,{[STOP]:{version:1,identity,epoch,build,stopped:true}});
    f.saved={...f.saved,...structuredClone(values)};
  };
  f.chrome.cookies.get=async key=>{
    assert.deepEqual(key,{url:origin+'/',name:COOKIE});return structuredClone(f.cookie);
  };
  f.chrome.cookies.remove=async key=>{
    assert(f.fenced);assert.equal(f.pendingWrites,0);assert.equal(f.http,1);
    assert.deepEqual(key,{url:origin+'/',name:COOKIE,storeId:'0'});
    f.cookieRemoved++;f.cookie=null;return key;
  };
  f.start();f.stopOptions={...f.options,cookieFingerprint:f.fingerprint,
    invalidateLanes:()=>{f.fenced=true;},
    readWriteDrain:()=>({fenced:f.fenced,pendingWrites:f.pendingWrites,unconfirmedWrite:f.unconfirmedWrite,
      localWritesDrained:f.fenced&&!f.unconfirmedWrite&&f.pendingWrites===0}),
    submitLogout:({signal})=>{
      assert(f.fenced);assert.equal(f.pendingWrites,0);assert(!f.unconfirmedWrite);
      f.owner=createContinuationLogout(f.scoped,origin,{...f.options,signal});return f.owner.run();
    }};
  f.makeStop=()=>createContinuationStopOwner(f.chrome,f.current(),build,f.stopOptions);
  return f;
}

for(const origin of ['https://display.example.test',originDefault,'https://[2001:db8::18]:8443'])
  for(const outcome of ['drained','pending'])test('full modeled Stop/document composition '+origin+' '+outcome,async()=>{
    const f=await stopFixture(origin);f.outcome=outcome;const stop=f.makeStop();
    assert.deepEqual(f.calls,[]);const result=await stop.run();await settle();
    assert.equal(result.mode,'continuation_stop_'+(outcome==='drained'?'complete':'pending'));
    for(const key of ['browserStopSaved','nativePauseConfirmed','localWritesDrained','cookieCleared',
      'serverRevocationConfirmed'])assert.equal(result[key],true,key);
    assert.equal(result.serverRevocation,outcome);assert.equal(result.sessionReady,false);
    assert.equal(f.http,1);assert.equal(f.stopWrites,1);assert.equal(f.pauseCalls,1);
    assert.equal(f.cookieRemoved,1);assert.equal(f.cookie,null);assert.deepEqual(f.removed,[17]);
    assert.equal(f.timers.size,0);assert.equal(f.ordinary,0);await assert.rejects(stop.run());
  });

test('whole Stop timeout cancels owned logout document while preserving completed independent facts',async()=>{
  const f=await stopFixture(),entered=deferred(),held=deferred();
  f.before=async name=>{if(name==='fetch'){entered.resolve();await held.promise;}};
  const stop=f.makeStop(),run=stop.run();await entered.promise;await f.tick(45000);
  const result=await run;assert.equal(result.mode,'continuation_stop_unconfirmed');
  assert.equal(result.browserStopSaved,true);assert.equal(result.nativePauseConfirmed,true);
  assert.equal(result.serverRevocation,'unconfirmed');assert.equal(f.cookieRemoved,0);
  assert.deepEqual(f.removed,[17]);const count=f.calls.length;
  held.resolve();await settle();await settle();assert.equal(f.calls.length,count);
  assert.equal(result.serverRevocation,'unconfirmed');assert.equal(f.stopWrites,1);assert.equal(f.timers.size,0);
});

for(const stage of ['continuation-logout-selected','continuation-logout-result'])
  test('lost document acknowledgement at '+stage+' cannot become a Stop success',async()=>{
    const f=await stopFixture();f.after=name=>{if(name===stage)throw Error('private response');};
    const result=await f.makeStop().run();await settle();
    assert.equal(result.mode,'continuation_stop_unconfirmed');assert.equal(result.serverRevocation,'unconfirmed');
    assert.equal(result.browserStopSaved,true);assert.equal(result.nativePauseConfirmed,true);
    assert.equal(f.http,stage==='continuation-logout-selected'?0:1);assert.equal(f.cookieRemoved,0);
    assert.equal(f.stopWrites,1);assert.deepEqual(f.removed,[17]);assert.equal(f.timers.size,0);
  });

for(const uncertain of [false,true])test('actual channel waits for known Chrome write drain; uncertain='+uncertain,async()=>{
  const f=await stopFixture();f.pendingWrites=1;const run=f.makeStop().run();await settle();
  assert.equal(f.http,0);assert(!f.owner);assert.equal(f.stopWrites,1);
  f.pendingWrites=0;f.unconfirmedWrite=uncertain;await f.tick(100);
  const result=await run;await settle();
  assert.equal(result.mode,uncertain?'continuation_stop_unconfirmed':'continuation_stop_complete');
  assert.equal(f.http,uncertain?0:1);assert.equal(f.cookieRemoved,uncertain?0:1);
  assert.equal(f.timers.size,0);
});
