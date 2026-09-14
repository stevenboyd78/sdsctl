import test from 'node:test';
import assert from 'node:assert/strict';
import {createContinuationLifecycle} from '../../src/sds200/browser_assets/browser_device_continuation_lifecycle.mjs';
import {createWorkerEventGate} from '../../src/sds200/browser_assets/browser_device_worker_gate.mjs';
import {startBrowserWorker} from '../../src/sds200/browser_assets/browser_device_worker.mjs';
import {connectContinuationProbeContent} from '../../src/sds200/browser_assets/browser_device_continuation_probe.mjs';
import {connectContinuationLogoutContent} from '../../src/sds200/browser_assets/browser_device_logout.mjs';
import {fingerprintContinuationCookie} from '../../src/sds200/browser_assets/browser_device_continuation_cookie.mjs';

// Actual canonical lifecycle, gate, lanes, native parsers, probe/logout channels
// and content HTTP parsers. Only Chrome/native/HTTP IO is modeled. This is NOT
// installed-browser, ordinary-route, user-consent or physical Pi acceptance.
const id='a'.repeat(32),build='b'.repeat(64),identity='c'.repeat(64),epoch='d'.repeat(64);
const HOST='org.sdsctl.browser_device',KEY='sdsctlDeviceRecovery',STOP='sdsctlContinuationStop';
const extension=`chrome-extension://${id}`,resume=extension+'/resume.html';
const token='sdsctl-browser-session-v1.'+'1'.repeat(64),clone=structuredClone;
const accepted={mode:'accepted',sessionReady:true},refused={mode:'administrator_required'};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const consentSender=()=>({id,origin:extension,url:resume,frameId:0,documentLifecycle:'active',
  documentId:'consent-1',tab:{id:7,incognito:false}});
const startupSender=()=>({...consentSender(),url:extension+'/startup.html',documentId:'startup-1',
  tab:{id:8,incognito:false}});
const ask=(listener,message,sender)=>new Promise(resolve=>{
  let replied=false;const held=listener(message,sender,value=>{replied=true;resolve(value);});
  if(held!==true&&!replied)resolve(null);
});

async function fixture(mode='paused',origin='https://192.0.2.18:8443') {
  const f={mode,origin,clock:[1000000,500000],events:{},timers:new Set(),calls:[],writes:[],
    tabs:new Map(),removed:[],issues:0,verifies:0,pauses:0,posts:0,gets:0,outcome:'drained',cookie:null};
  const binding={fingerprint:'e'.repeat(64),revision:5,generation:null},
    active={fingerprint:'2'.repeat(64),revision:7,generation:19};
  f.initial={version:1,ok:true,build,role:'continuation',extensionId:id,acknowledge:false,launch:null,
    config:{origin,identity,nativeHost:HOST},continuation:{epoch,mode,
      binding:clone(mode==='paused'?binding:active)}};
  f.current=clone(f.initial);
  f.saved={[KEY]:{version:3,identity,epoch,build,phase:'paused',binding:null,intent:null,cookieFingerprint:null}};
  if(mode==='active') {
    f.cookie={name:'__Host-sdsctl-device-session',value:token,domain:new URL(origin).hostname,
      hostOnly:true,path:'/',secure:true,httpOnly:true,sameSite:'strict',session:false,
      expirationDate:1300,storeId:'0'};
    f.saved[KEY]={...f.saved[KEY],phase:'accepted',binding:clone(active),intent:'4'.repeat(64),
      cookieFingerprint:await fingerprintContinuationCookie(origin,f.cookie)};
  }
  f.call=async(name,fn)=>{
    const index=f.calls.length;f.calls.push(name);await f.before?.(name,index);
    const result=await fn();await f.after?.(name,index,result);return result;
  };
  f.options={wall:()=>f.clock[0],monotonic:()=>f.clock[1],schedule:(fn,ms)=>{
    const timer={fn,ms};f.timers.add(timer);return timer;
  },cancel:timer=>{f.timers.delete(timer);f.onCancel?.();}};
  const event=name=>({addListener:fn=>{assert(!f.events[name]);f.events[name]=fn;}});
  const forbidden=()=>assert.fail('unowned mutation');
  f.chrome={runtime:{id,getURL:page=>extension+'/'+page,onMessage:event('message'),
    onStartup:event('startup'),onInstalled:event('installed'),getContexts:filter=>f.call('contexts',()=>{
      if(filter.documentIds?.[0]==='startup-1') {
        assert.deepEqual(filter,{contextTypes:['TAB'],documentIds:['startup-1'],tabIds:[8],frameIds:[0],incognito:false});
        return [{contextId:'startup-context-1',contextType:'TAB',documentId:'startup-1',documentOrigin:extension,
          documentUrl:extension+'/startup.html',frameId:0,tabId:8,windowId:1,incognito:false}];
      }
      assert.deepEqual(filter,{contextTypes:['TAB'],documentIds:['consent-1'],tabIds:[7],frameIds:[0],incognito:false});
      return [{contextId:'context-1',contextType:'TAB',documentId:'consent-1',documentOrigin:extension,
        documentUrl:resume,frameId:0,tabId:7,windowId:1,incognito:false}];
    }),sendNativeMessage:(host,envelope)=>{
      if(envelope.action==='worker-context') {
        assert.equal(host,HOST);assert.deepEqual(envelope,{version:1,action:'worker-context',build});
        return f.call('worker-context',()=>clone(f.initial));
      }
      assert.equal(host,HOST);assert.deepEqual(Object.keys(envelope).sort(),['action','build','request','version']);
      assert.equal(envelope.version,1);assert.equal(envelope.action,'worker-request');assert.equal(envelope.build,build);
      const request=envelope.request;assert.equal(request.version,1);
      const action=request.action;
      return f.call(action,()=>{
        if(action==='continuation-current')return clone(f.current);
        if(action==='continuation-review')return {version:1,ok:true,build,identity,epoch,mode:'paused',
          binding:{...binding,generation:19}};
        if(action==='continuation-initial-session') {
          assert.equal(f.saved[KEY].phase,'initial_pending');
          assert.deepEqual(request,{version:1,action,epoch,intent:f.saved[KEY].intent,
            binding:{...binding,generation:19}});
          f.issues++;f.current.continuation={epoch,mode:'active',binding:clone(active)};
          return {version:1,ok:true,build,identity,epoch,mode:'active',binding:clone(active),
            session:{token,expires_in:300}};
        }
        if(action==='continuation-verify-active') {
          f.verifies++;return {version:1,ok:true,build,identity,...clone(f.current.continuation)};
        }
        assert.equal(action,'continuation-pause');
        const b=f.current.continuation.binding;
        assert.deepEqual(request,{version:1,action,epoch,binding:{fingerprint:b.fingerprint,revision:b.revision}});
        f.pauses++;f.current.continuation={epoch,mode:'paused',binding:{fingerprint:'3'.repeat(64),
          revision:b.revision+1,generation:null}};
        return {version:1,ok:true,build,identity,...clone(f.current.continuation),
          nativePauseConfirmed:true,serverRevocationConfirmed:false};
      });
    }},storage:{local:{setAccessLevel:value=>f.call('access',()=>{
      assert.deepEqual(value,{accessLevel:'TRUSTED_CONTEXTS'});
    }),get:key=>f.call(key===STOP?'read-stop':'read',()=>{
      assert([null,STOP].includes(key));return clone(key===null?f.saved:Object.hasOwn(f.saved,key)?{[key]:f.saved[key]}:{});
    }),set:value=>f.call(Object.hasOwn(value,STOP)?'write-stop':'write-'+value[KEY].phase,()=>{
      f.writes.push(clone(value));Object.assign(f.saved,clone(value));
    }),clear:forbidden,remove:forbidden}},cookies:{get:options=>f.call('cookie',()=>{
      assert.deepEqual(options,{url:origin+'/',name:'__Host-sdsctl-device-session'});return clone(f.cookie);
    }),set:options=>f.call('cookie-set',()=>{
      const {url,...value}=options;assert.equal(url,origin+'/');
      f.cookie={...clone(value),domain:new URL(url).hostname,hostOnly:true,session:false};return clone(f.cookie);
    }),remove:options=>f.call('cookie-remove',()=>{
      assert.deepEqual(options,{url:origin+'/',name:'__Host-sdsctl-device-session',storeId:'0'});
      f.cookie=null;return clone(options);
    })},alarms:{onAlarm:event('alarm'),getAll:()=>f.call('alarms',()=>[]),get:name=>f.call('alarm',()=>{
      assert.equal(name,'sdsctl-device-recovery');return null;
    }),create:forbidden,clear:forbidden},tabs:{onUpdated:event('tab'),update:forbidden,
      query:filter=>f.call('entry-query',()=>{
        assert.deepEqual(filter,{url:[extension+'/startup.html']});return [];
      }),
      create:options=>f.call('create',()=>{
        assert.deepEqual(options,{url:origin+'/device-display',active:false});
        const target=17+f.tabs.size;f.loadContent(target);return clone(f.tabs.get(target).tab);
      }),get:target=>f.call(target===8?'startup-tab':target===7?'consent-tab':'page',()=>clone(target===8?
        {id:8,incognito:false,status:'complete',url:extension+'/startup.html'}:target===7?
        {id:7,incognito:false,status:'complete',url:resume}:f.tabs.get(target).tab)),
      sendMessage:(target,message,selection)=>f.call(message.action,()=>{
        const content=f.tabs.get(target);assert(content&&!content.closed);
        assert.deepEqual(selection,message.action.endsWith('-select')?{frameId:0}:{documentId:content.document});
        const listener=message.action.startsWith('continuation-probe-')?content.probe:content.logout;
        return ask(listener,message,{id});
      }),remove:target=>f.call('remove',()=>{
        const content=f.tabs.get(target);assert(content);f.removed.push(target);content.closed=true;
        for(const fn of content.pagehide)fn();
      })}};
  f.loadContent=target=>{
    const content={tab:{id:target,incognito:false,status:'complete',url:origin+'/device-display'},
      document:'document-'+target,pagehide:new Set(),closed:false};f.tabs.set(target,content);
    const window={location:{href:content.tab.url},addEventListener:(name,fn)=>{
      assert.equal(name,'pagehide');content.pagehide.add(fn);
    },removeEventListener:(name,fn)=>content.pagehide.delete(fn),dispatchEvent:event=>{
      assert.equal(event.type,'sdsctl-device-signout-intent');assert.equal(event.detail,undefined);
      content.uiQuiesced=true;return true;
    }};window.top=window;
    const runtime=kind=>({id,onMessage:{addListener:fn=>{content[kind]=fn;},removeListener:fn=>{
      if(content[kind]===fn)content[kind]=null;
    }},sendMessage:message=>f.call(message.action,()=>{
      const sender={id,origin,url:content.tab.url,frameId:0,documentLifecycle:'active',
        documentId:content.document,tab:{id:target,incognito:false}};
      return ask(f.events.message,message,f.changeSender?f.changeSender(sender,message):sender);
    })});
    const fetcher=(url,options)=>f.call(options.method==='GET'?'fetch-session':'fetch-logout',()=>{
      assert.equal(options.mode,'same-origin');assert.equal(options.credentials,'same-origin');
      assert.deepEqual(options.headers,{Accept:'application/json'});
      let body,status=200;
      if(options.method==='GET') {
        assert.equal(url,origin+'/auth/session');f.gets++;
        body={display_only:true,device_enrolled:true,remaining_seconds:300};
      } else {
        assert.equal(content.uiQuiesced,true);
        assert.equal(options.method,'POST');assert.equal(url,origin+'/auth/logout');f.posts++;
        status=f.outcome==='pending'?202:200;
        body={version:1,device_logout:true,paused:true,drained:f.outcome==='drained'};
      }
      const response=new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json'}});
      Object.defineProperty(response,'url',{value:url});return response;
    });
    connectContinuationProbeContent({window,runtime:runtime('probe'),fetcher},origin);
    connectContinuationLogoutContent({...f.options,window,runtime:runtime('logout'),fetcher},origin);
  };
  f.start=(open=true)=>{
    f.gate=createWorkerEventGate(f.chrome,f.options.monotonic,f.options.schedule,f.options.cancel);
    f.owner=createContinuationLifecycle(f.gate,f.initial,build,f.options);
    if(open)f.gate.open();assert.deepEqual(Object.keys(f.events),['message','startup','installed','alarm','tab']);
  };
  f.ask=message=>ask(f.events.message,message,consentSender());
  f.startOrdinary=()=>startBrowserWorker(f.chrome,build,f.options);
  f.startup=()=>ask(f.events.message,{action:'startup-status'},startupSender());
  f.accept=async()=>{
    if(mode==='active')return f.owner.startAccepted();
    const review=await f.ask({action:'resume-review'});
    return review.ticket?f.ask({action:'resume-confirm',ticket:review.ticket}):review;
  };
  f.logout=(change={})=>ask(f.events.message,{action:'logout-begin'},
    {...consentSender(),origin,url:origin+'/device-display',...change});
  f.tick=async ms=>{
    const timers=[...f.timers].filter(t=>t.ms===ms);assert(timers.length);
    for(const timer of timers){f.timers.delete(timer);timer.fn();}await settle();
  };
  return f;
}

for(const origin of ['https://display.example.test','https://192.0.2.18:8443','https://[2001:db8::18]:8443'])
test('ordinary recovery-produced pause stays unchanged until explicit consent '+origin,async()=>{
  const f=await fixture('paused',origin);
  f.saved={[KEY]:{version:1,identity,paused:true,phase:'clean',nextAt:0}};
  const saved=clone(f.saved);await f.startOrdinary();
  assert.deepEqual(await f.startup(),{mode:'paused',sessionReady:false});
  assert.deepEqual(f.saved,saved);assert.deepEqual(f.writes,[]);assert.equal(f.issues,0);
  const reviewed=await f.ask({action:'resume-review'});
  assert.equal(reviewed.mode,'continuation_confirmation_reviewed');
  assert.deepEqual(f.saved,saved);assert.deepEqual(f.writes,[]);assert.equal(f.issues,0);
  assert.deepEqual(await f.ask({action:'resume-confirm',ticket:reviewed.ticket}),accepted);
  assert.deepEqual(f.writes.filter(v=>v[KEY]).map(v=>v[KEY].phase),['initial_pending','accepted']);
  assert.equal(f.issues,1);assert.equal(f.gets,1);
  assert.equal((await f.logout()).mode,'continuation_stop_complete');
  assert.equal(f.posts,1);assert.equal(f.pauses,1);assert.equal(f.cookie,null);
});

for(const origin of ['https://display.example.test','https://192.0.2.18:8443','https://[2001:db8::18]:8443'])
test('ordinary accepted startup waits for same document initial load '+origin,async()=>{
  const f=await fixture('active',origin);let reads=0;
  const schedule=f.options.schedule;
  f.options.schedule=(fn,ms)=>ms===100?setImmediate(fn):schedule(fn,ms);
  f.after=(name,_index,value)=>{if(name==='startup-tab'&&++reads<=2)value.status='loading';};
  await f.startOrdinary();
  assert.deepEqual(await f.startup(),{mode:'active',sessionReady:true});
  assert.equal(reads,3);assert.equal(f.verifies,1);assert.equal(f.issues,0);assert.equal(f.pauses,0);
  assert.equal(f.gets,1);assert.equal(f.posts,0);
});

for(const broken of ['tab-url','pending-url','tab-state','document','context','closed',
  'navigation','same-url-reload','wall-expired','mono-expired','wall-back','mono-back','deadline','stop'])
test('ordinary accepted initial-load wait refuses '+broken+' without authentication',async()=>{
  const f=await fixture('active');let changed=false;
  f.before=name=>{if(changed&&broken==='closed'&&name==='startup-tab')throw Error('closed');};
  f.after=(name,_index,value)=>{
    if(name==='startup-tab') {
      value.status='loading';
      if(changed&&broken==='tab-url')value.url=resume;
      if(changed&&broken==='pending-url')value.pendingUrl=resume;
      if(changed&&broken==='tab-state')value.status='unknown';
    }
    if(changed&&name==='contexts') {
      if(broken==='document')value[0].documentId='replacement';
      if(broken==='context')value[0].contextId='replacement';
    }
  };
  await f.startOrdinary();const running=f.startup();
  for(let count=0;count<100&&![...f.timers].some(t=>t.ms===100);count++)await settle();
  assert([...f.timers].some(t=>t.ms===100));
  assert.equal(f.verifies,0);assert.equal(f.pauses,0);changed=true;
  if(broken==='navigation'||broken==='same-url-reload') {
    const url=broken==='navigation'?'https://foreign.test/':extension+'/startup.html';
    f.events.tab(8,{status:'loading',url},{id:8,incognito:false,status:'loading',url});
  }
  if(broken==='wall-expired')f.clock[0]+=10000;
  if(broken==='mono-expired')f.clock[1]+=10000;
  if(broken==='wall-back')f.clock[0]-=1;
  if(broken==='mono-back')f.clock[1]-=1;
  if(broken==='stop')await f.logout();
  await f.tick(broken==='deadline'?45000:100);
  assert.deepEqual(await running,{mode:'administrator_required',sessionReady:false});
  // Explicit manual stop may retire the pre-existing accepted session; a load
  // failure alone may not. Neither path verifies readiness or issues a login.
  assert.equal(f.verifies,0);assert.equal(f.issues,0);assert.equal(f.posts,broken==='stop'?1:0);
  assert.equal(f.pauses,1);assert(![...f.timers].some(t=>t.ms===100||t.ms===45000));
});

test('ordinary accepted initial-load wait has a fixed read bound even if clocks stall',async()=>{
  const f=await fixture('active');let reads=0;
  const schedule=f.options.schedule;
  f.options.schedule=(fn,ms)=>ms===100?setImmediate(fn):schedule(fn,ms);
  f.after=(name,_index,value)=>{if(name==='startup-tab'){reads++;value.status='loading';}};
  await f.startOrdinary();
  assert.deepEqual(await f.startup(),{mode:'administrator_required',sessionReady:false});
  assert.equal(reads,101);assert.equal(f.verifies,0);assert.equal(f.issues,0);assert.equal(f.posts,0);
});

test('ordinary accepted loading cannot start a second verifier from repeated status',async()=>{
  const f=await fixture('active');let loading=true;
  f.after=(name,_index,value)=>{if(name==='startup-tab'&&loading)value.status='loading';};
  await f.startOrdinary();const running=f.startup();
  for(let count=0;count<100&&![...f.timers].some(t=>t.ms===100);count++)await settle();
  assert([...f.timers].some(t=>t.ms===100));
  assert.deepEqual(await f.startup(),{mode:'continuation_verification_required',sessionReady:false});
  assert.equal(f.verifies,0);loading=false;await f.tick(100);
  assert.deepEqual(await running,{mode:'active',sessionReady:true});
  assert.equal(f.verifies,1);assert.equal(f.pauses,0);assert.equal(f.issues,0);
});

for(const mode of ['paused','active'])for(const origin of ['https://display.example.test',
  'https://192.0.2.18:8443','https://[2001:db8::18]:8443'])
test('ordinary native-selected '+mode+' route uses real canonical adapters '+origin,async()=>{
  const f=await fixture(mode,origin);await f.startOrdinary();
  assert.equal(f.issues,0);assert.equal(f.verifies,0);assert.equal(f.gets,0);
  if(mode==='paused') {
    assert.deepEqual(await f.startup(),{mode:'paused',sessionReady:false});
    const reviewed=await f.ask({action:'resume-review'});
    assert.equal(reviewed.mode,'continuation_confirmation_reviewed');assert.match(reviewed.ticket,/^[a-f0-9]{64}$/);
    assert.deepEqual(await f.ask({action:'resume-confirm',ticket:reviewed.ticket}),accepted);
    f.events.tab(7,{status:'loading',url:origin+'/device-display'},
      {id:7,incognito:false,status:'loading',url:origin+'/device-display'});await settle();
    assert.equal(f.pauses,0);
  } else {
    assert.deepEqual(await f.startup(),{mode:'active',sessionReady:true});
    const calls=f.calls.length;
    assert.deepEqual(await f.startup(),{mode:'continuation_verification_required',sessionReady:false});
    assert.equal(f.calls.length,calls);assert.equal(f.verifies,1);
  }
  assert.equal(f.issues,mode==='paused'?1:0);assert.equal(f.gets,1);
  const result=await f.logout();assert.equal(result.mode,'continuation_stop_complete');
  assert.equal(f.posts,1);assert.equal(f.pauses,1);assert.equal(f.cookie,null);
  assert.equal(Object.hasOwn(result,'submit'),false);assert.equal(Object.hasOwn(result,'ticket'),false);
  assert.equal(f.writes.filter(v=>v[STOP]).length,1);
  const calls=f.calls.length;assert.deepEqual(await f.startup(),{mode:'administrator_required',sessionReady:false});
  assert.equal(f.calls.length,calls);assert(f.timers.size<=2); // Only bounded entry rescans remain.
});

for(const mode of ['paused','active'])for(const [name,change] of [
  ['stop',f=>{f.saved[STOP]={stopped:true};}],['null stop',f=>{f.saved[STOP]=null;}],
  ['false stop',f=>{f.saved[STOP]=false;}],['missing',f=>{f.saved={};}],
  ['extra',f=>{f.saved.other=true;}],['legacy',f=>{f.saved[KEY]={version:1,identity,paused:true};}],
  ['pending',f=>{f.saved[KEY].phase='initial_pending';}],
  ['old build',f=>{f.saved[KEY].build='0'.repeat(64);}],
])test('ordinary '+mode+' '+name+' startup never constructs mutating lanes',async()=>{
  const f=await fixture(mode);change(f);const saved=clone(f.saved),cookie=clone(f.cookie);
  await f.startOrdinary();const calls=f.calls.length;
  assert.deepEqual(await f.startup(),{mode:'administrator_required',sessionReady:false});
  assert.deepEqual(await f.ask({action:'resume-review'}),{mode:'administrator_required'});
  assert.equal(f.calls.length,calls);assert.deepEqual(f.saved,saved);assert.deepEqual(f.cookie,cookie);
  assert.deepEqual(f.writes,[]);assert.equal(f.issues,0);assert.equal(f.verifies,0);assert.equal(f.pauses,0);
  assert.equal(f.posts,0);assert.equal(f.gets,0);assert(!f.calls.includes('access'));
  assert(f.calls.every(action=>['worker-context','read','entry-query'].includes(action)));
});

for(const mode of ['paused','active'])test('ordinary queued '+mode+' sign-out wins before any online acceptance',async()=>{
  const f=await fixture(mode),entered=deferred(),release=deferred();
  f.before=async name=>{if(name==='worker-context'){entered.resolve();await release.promise;}};
  const start=f.startOrdinary();await entered.promise;
  const reply=f.logout();release.resolve();await start;
  assert.equal((await reply).mode,mode==='active'?'continuation_stop_complete':'continuation_stop_unconfirmed');
  assert.equal(f.issues,0);assert.equal(f.verifies,0);assert.equal(f.posts,mode==='active'?1:0);assert.equal(f.pauses,1);
  assert.equal(f.writes.filter(v=>v[STOP]).length,1);
  assert.deepEqual(await f.startup(),{mode:'administrator_required',sessionReady:false});
});

for(const boundary of ['contexts','startup-tab','continuation-verify-active','fetch-session'])
test('ordinary active navigation during '+boundary+' never hands off a stale result',async()=>{
  const f=await fixture('active'),entered=deferred(),release=deferred();await f.startOrdinary();
  f.before=async name=>{if(name===boundary){entered.resolve();await release.promise;}};
  const running=f.startup();await entered.promise;
  f.events.tab(8,{status:'loading',url:'https://foreign.test/'},
    {id:8,incognito:false,status:'loading',url:'https://foreign.test/'});
  release.resolve();assert.deepEqual(await running,{mode:'administrator_required',sessionReady:false});
  assert.equal(f.issues,0);assert.equal(f.posts,0);
});

test('ordinary inspection cannot renew the original gate deadline',async()=>{
  const f=await fixture();let currents=0;
  f.after=name=>{
    if(name==='worker-context')f.clock[1]+=5000;
    if(name==='continuation-current'&&++currents===1)f.clock[1]+=8000;
  };
  await assert.rejects(f.startOrdinary(),/Worker context refused/);
  assert.deepEqual(f.writes,[]);assert(!f.calls.includes('access'));assert.equal(f.issues,0);
  assert.equal(f.pauses,0);assert.equal(f.posts,0);assert.equal(f.gets,0);
});
for(const boundary of ['worker-context','read','continuation-current'])
test('STOP appears during ordinary '+boundary+' preflight without starting a new Stop owner',async()=>{
  const f=await fixture('active');let changed=false;
  f.after=name=>{if(name===boundary&&!changed){changed=true;f.saved[STOP]={stopped:true};}};
  await f.startOrdinary();assert.equal(changed,true);
  assert.deepEqual(await f.startup(),{mode:'administrator_required',sessionReady:false});
  assert.deepEqual(f.writes,[]);assert.equal(f.pauses,0);assert.equal(f.posts,0);assert.equal(f.verifies,0);
});
for(const broken of ['document','origin','url','context-replaced','tab-url','pending-url'])
test('ordinary accepted startup refuses changed '+broken+' before server verification',async()=>{
  const f=await fixture('active');await f.startOrdinary();let reads=0;
  f.after=(name,_index,value)=>{
    if(name==='contexts') {
      reads++;
      if(broken==='document')value[0].documentId='other';
      if(broken==='origin')value[0].documentOrigin='https://foreign.test';
      if(broken==='url')value[0].documentUrl=resume;
      if(broken==='context-replaced'&&reads===2)value[0].contextId='replacement';
    }
    if(name==='startup-tab') {
      if(broken==='tab-url')value.url=resume;
      if(broken==='pending-url')value.pendingUrl=resume;
    }
  };
  assert.deepEqual(await f.startup(),{mode:'administrator_required',sessionReady:false});
  assert.equal(f.verifies,0);assert.equal(f.issues,0);assert.equal(f.posts,0);
});

for(const mode of ['paused','active'])for(const origin of ['https://display.example.test',
  'https://192.0.2.18:8443','https://[2001:db8::18]:8443'])for(const outcome of ['drained','pending'])
test('canonical '+mode+' to one '+outcome+' Stop '+origin,async()=>{
  const f=await fixture(mode,origin);f.outcome=outcome;f.start();assert.deepEqual(f.calls,[]);
  assert.deepEqual(await f.accept(),accepted);assert.equal(f.gets,1);assert.equal(f.tabs.size,1);
  const result=await f.owner.stop();await settle();
  assert.equal(result.mode,outcome==='drained'?'continuation_stop_complete':'continuation_stop_pending');
  assert.equal(result.cookieCleared,true);assert.equal(result.nativePauseConfirmed,true);
  assert.equal(f.posts,1);assert.equal(f.pauses,1);assert.equal(f.issues,mode==='paused'?1:0);
  assert.equal(f.verifies,mode==='active'?1:0);assert.equal(f.tabs.size,2);
  assert.deepEqual(f.removed,[17,18]);assert.equal(f.timers.size,0);
  assert.equal(f.writes.filter(v=>v[STOP]).length,1);assert.equal(f.cookie,null);
  assert(!JSON.stringify(f.saved).includes(token));assert(!JSON.stringify(result).includes(token));
  const count=f.calls.length;assert.strictEqual(await f.owner.stop(),result);
  await assert.rejects(f.owner.startAccepted());assert.equal(f.calls.length,count);
});

for(const mode of ['paused','active'])test('reentrant '+mode+' Stop shares its selected promise',async()=>{
  const f=await fixture(mode);f.start();await f.accept();
  const joins=[];f.onCancel=()=>{joins.push(f.owner.stop());};
  const first=f.owner.stop();assert.strictEqual(f.owner.stop(),first);
  await f.logout();const result=await first;await settle();
  assert(joins.length);assert(joins.every(p=>p===first));assert.equal(result.mode,'continuation_stop_complete');
  assert.equal(f.posts,1);assert.equal(f.pauses,1);assert.equal(f.writes.filter(v=>v[STOP]).length,1);
});

for(const mode of ['paused','active'])test('queued logout fences '+mode+' before startup IO',async()=>{
  const f=await fixture(mode);f.start(false);const response=f.logout();f.gate.open();await response;
  const result=await f.owner.stop();await settle();
  assert.equal(result.serverRevocation,mode==='active'?'drained':'unconfirmed');
  assert.equal(result.cookieCleared,mode==='active');
  assert.equal(result.localWritesDrained,true);assert.equal(f.issues,0);assert.equal(f.verifies,0);
  assert.equal(f.posts,mode==='active'?1:0);assert.equal(f.gets,0);
  assert.equal(f.tabs.size,mode==='active'?1:0);
  assert([...f.tabs.values()].every(tab=>tab.closed));
  assert.equal(f.writes.filter(v=>v[STOP]).length,1);await assert.rejects(f.owner.startAccepted());
});

for(const mode of ['paused','active'])test('fixed '+mode+' context does not retain caller aliases',async()=>{
  const f=await fixture(mode);f.start();f.initial.config.origin='https://foreign.test';
  f.initial.continuation.binding.revision=99;assert.deepEqual(await f.accept(),accepted);
  assert.equal((await f.owner.stop()).mode,'continuation_stop_complete');
});

test('acknowledged initial acceptance hands off navigation without selecting Stop',async()=>{
  const f=await fixture();f.start();assert.deepEqual(await f.accept(),accepted);
  f.events.tab(7,{status:'loading',url:f.origin+'/device-display'},
    {id:7,incognito:false,status:'loading',url:f.origin+'/device-display'});
  await f.ask({action:'continuation-cancel'});await settle();
  assert(!Object.hasOwn(f.saved,STOP));assert.equal(f.pauses,0);assert.equal(f.posts,0);
  assert.equal(f.saved[KEY].phase,'accepted');assert.notEqual(f.cookie,null);
  // The retired consent document does not retire the lifecycle's sign-out owner.
  assert.equal((await f.owner.stop()).mode,'continuation_stop_complete');assert.equal(f.posts,1);
});

for(const mode of ['paused','active'])for(const outcome of ['drained','pending'])
test(mode+' manual sign-out joins canonical '+outcome+' result without a second owner',async()=>{
  const f=await fixture(mode);f.outcome=outcome;f.start();await f.accept();
  assert.equal(f.owner.isStopped(),false);
  const requested=f.logout();assert.equal(f.owner.isStopped(),true);
  const joined=f.owner.stop(),reply=await requested;
  assert.strictEqual(reply,await joined);assert.equal(reply.sessionReady,false);
  assert.equal(Object.hasOwn(reply,'submit'),false);assert.equal(Object.hasOwn(reply,'ticket'),false);
  assert.strictEqual(await f.logout(),reply);assert.equal(f.posts,1);assert.equal(f.pauses,1);
  assert.equal(f.writes.filter(v=>v[STOP]).length,1);
  assert.deepEqual(await f.logout({documentId:'replacement-document'}),{mode:'administrator_required'});
  assert.deepEqual(await f.logout({url:f.origin+'/'}),{mode:'administrator_required'});
  assert.equal(f.posts,1);assert.equal(f.timers.size,0);
});

for(const mode of ['paused','active'])test('foreign logout cannot select '+mode+' Stop',async()=>{
  const f=await fixture(mode);f.start();await f.accept();const count=f.calls.length;
  await f.logout({origin:'https://foreign.test',url:'https://foreign.test/device-display'});
  await f.logout({frameId:1});await f.logout({documentLifecycle:'cached'});
  assert.equal(f.calls.length,count);assert(!f.saved[STOP]);await f.owner.stop();
});

const drive=async(f,promise)=>{
  let finished=false;void promise.finally(()=>{finished=true;}).catch(()=>{});
  for(let n=0;n<250&&!finished;n++) {
    // WebCrypto runs on a real thread pool. Yield wall time as well as the event
    // loop; a tight setImmediate spin can finish before a valid digest resolves.
    await new Promise(resolve=>setTimeout(resolve,2));
    for(const timer of [...f.timers].filter(t=>t.ms===100)) {f.timers.delete(timer);timer.fn();}
  }
  assert(finished,'operation must settle without waiting for its whole deadline');return promise;
};

for(const mode of ['paused','active']) {
  const baseline=await fixture(mode);baseline.start();await baseline.accept();
  const acceptanceCalls=[...baseline.calls],start=acceptanceCalls.length;
  await baseline.owner.stop();await settle();
  const stopCalls=baseline.calls.slice(start);
  for(const when of ['before','after'])for(const [cut,name] of acceptanceCalls.entries()) {
    test(mode+' '+when+' failed acknowledgement at '+cut+' '+name,async()=>{
      const f=await fixture(mode);f.start();
      f[when]=(_name,index)=>{if(index===cut)throw Error('modeled lost acknowledgement');};
      const result=await f.accept().catch(()=>refused);
      assert.deepEqual(result,name==='remove'?accepted:refused);
      const stopped=await drive(f,f.owner.stop());await settle();
      // Initial installation retains ownership after both checked cookie reads,
      // BEFORE its later probe/accepted write. A failure there can still sign out.
      const owned=mode==='paused'?cut>acceptanceCalls.indexOf('cookie-set')+2:name==='remove';
      assert.equal(stopped.mode,owned&&name!=='write-accepted'?
        'continuation_stop_complete':'continuation_stop_unconfirmed');assert(f.issues<=1);assert(f.pauses<=1);
      assert(f.posts<=1);assert.equal(f.writes.filter(v=>v[STOP]).length,1);
      const count=f.calls.length;assert.strictEqual(await f.owner.stop(),stopped);
      assert.equal(f.calls.length,count);assert.equal(f.timers.size,0);
    });
    test(mode+' '+when+' Stop during '+cut+' '+name+' fences late result',async()=>{
      const f=await fixture(mode),entered=deferred(),held=deferred();f.start();
      f[when]=async(_name,index)=>{if(index===cut){entered.resolve();await held.promise;}};
      const run=f.accept().catch(()=>refused);await entered.promise;
      const stop=f.owner.stop();assert.strictEqual(f.owner.stop(),stop);
      const readiness=await run;
      assert.deepEqual(readiness,mode==='active'&&when==='after'&&name==='remove'?accepted:refused);
      held.resolve();
      const result=await drive(f,stop);await settle();
      // Owned-tab removal is fire-and-forget; the accepted verifier has already
      // retained its comparison even if the outer readiness reply is fenced.
      const owned=mode==='paused'?cut>acceptanceCalls.indexOf('cookie-set')+2:name==='remove';
      assert.equal(result.mode,owned?'continuation_stop_complete':'continuation_stop_unconfirmed');
      assert(f.issues<=1);assert(f.pauses<=1);
      assert(f.posts<=1);assert.equal(f.writes.filter(v=>v[STOP]).length,1);
      assert.equal(f.timers.size,0);
      const count=f.calls.length;assert.strictEqual(await f.owner.stop(),result);assert.equal(f.calls.length,count);
    });
  }
  for(const when of ['before','after'])for(const [cut,name] of stopCalls.entries())
  test(mode+' terminal '+when+' failure at '+cut+' '+name+' cannot retry',async()=>{
    const f=await fixture(mode);f.start();await f.accept();const start=f.calls.length;
    f[when]=(_name,index)=>{if(index===start+cut)throw Error('modeled terminal failure');};
    const result=await drive(f,f.owner.stop());await settle();
    // Removing an owned temporary tab is best-effort cleanup, not authority.
    if(name!=='remove')assert.equal(result.mode,'continuation_stop_unconfirmed');
    assert(f.posts<=1);assert(f.pauses<=1);assert(f.writes.filter(v=>v[STOP]).length<=1);
    f[when]=null;const count=f.calls.length;
    assert.strictEqual(await f.owner.stop(),result);assert.equal(f.calls.length,count);
    assert.equal(f.timers.size,0);
  });
}

test('unknown issuance retains original native comparison and never fabricates logout ownership',async()=>{
  const f=await fixture();f.start();f.after=name=>{
    if(name==='continuation-initial-session')throw Error('issue committed, reply lost');
  };
  assert.deepEqual(await f.accept(),refused);const result=await drive(f,f.owner.stop());
  assert.equal(f.issues,1);assert.equal(f.current.continuation.mode,'active');
  assert.equal(result.nativePauseConfirmed,false);assert.equal(result.browserStopSaved,true);
  assert.equal(result.serverRevocation,'unconfirmed');assert.equal(f.pauses,0);assert.equal(f.posts,0);
  assert.equal(f.saved[KEY].phase,'initial_pending');assert.equal(f.cookie,null);
});

for(const mode of ['paused','active'])test(mode+' pending storage blocks POST but not STOP/native pause',async()=>{
  const f=await fixture(mode),entered=deferred(),held=deferred();f.start();
  f.after=async(name,index)=>{
    if(mode==='paused'?name==='write-accepted':name==='access'&&index===0) {
      entered.resolve();await held.promise;
    }
  };
  const run=f.accept().catch(()=>refused);await entered.promise;const stop=f.owner.stop();
  assert.deepEqual(await run,refused);await settle();
  assert.equal(f.writes.filter(v=>v[STOP]).length,1);assert.equal(f.pauses,1);assert.equal(f.posts,0);
  held.resolve();const result=await drive(f,stop);
  assert.equal(result.localWritesDrained,true);
  assert.equal(result.mode,mode==='paused'?'continuation_stop_complete':'continuation_stop_unconfirmed');
  assert.equal(f.posts,mode==='paused'?1:0);assert.equal(f.timers.size,0);
});

for(const mode of ['paused','active'])test(mode+' lost POST acknowledgement never removes cookie or repeats POST',async()=>{
  const f=await fixture(mode);f.start();await f.accept();const cookie=clone(f.cookie);
  f.after=name=>{if(name==='fetch-logout')throw Error('POST committed, response lost');};
  const result=await drive(f,f.owner.stop());assert.equal(f.posts,1);
  assert.equal(result.browserStopSaved,true);assert.equal(result.nativePauseConfirmed,true);
  assert.equal(result.serverRevocation,'unconfirmed');assert.equal(result.cookieCleared,false);
  assert.deepEqual(f.cookie,cookie);assert.equal(f.timers.size,0);
});

for(const mode of ['paused','active'])test(mode+' whole Stop deadline refuses late POST proof',async()=>{
  const f=await fixture(mode),entered=deferred(),held=deferred();f.start();await f.accept();
  f.after=async name=>{if(name==='fetch-logout'){entered.resolve();await held.promise;}};
  const run=f.owner.stop();await entered.promise;await f.tick(45000);
  const result=await run;assert.equal(result.mode,'continuation_stop_unconfirmed');
  const snapshot=clone(result);held.resolve();await settle();await settle();
  assert.deepEqual(result,snapshot);assert.equal(result.cookieCleared,false);assert.equal(f.posts,1);
  assert.equal(f.timers.size,0);
});

for(const mode of ['paused','active'])test(mode+' rejects caller-selected Stop/start payload without IO',async()=>{
  const f=await fixture(mode);f.start();await settle();const count=f.calls.length;
  for(const payload of [undefined,null,{},true,{outcome:'drained',cookieFingerprint:'f'.repeat(64)}]) {
    await assert.rejects(f.owner.stop(payload));await assert.rejects(f.owner.startAccepted(payload));
  }
  assert.equal(f.calls.length,count);assert.deepEqual(await f.accept(),accepted);
  assert.equal((await f.owner.stop()).mode,'continuation_stop_complete');
});

for(const mode of ['paused','active'])for(const change of ['origin','build','role','binding'])
test('refuses invalid fixed '+mode+' '+change+' before selecting IO',async()=>{
  const f=await fixture(mode);
  f.gate=createWorkerEventGate(f.chrome,f.options.monotonic,f.options.schedule,f.options.cancel);
  if(change==='origin')f.initial.config.origin='http://foreign.test';
  if(change==='build')f.initial.build='f'.repeat(64);
  if(change==='role')f.initial.role='normal';
  if(change==='binding')f.initial.continuation.binding.revision=0;
  assert.throws(()=>createContinuationLifecycle(f.gate,f.initial,build,f.options));
  assert.deepEqual(f.calls,[]);f.gate.fail();assert.equal(f.timers.size,0);
});
