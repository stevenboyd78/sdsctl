import test from 'node:test';
import assert from 'node:assert/strict';
import {connectPausedBrowserWorker} from '../../src/sds200/browser_assets/browser_device_paused.mjs';

const id='a'.repeat(32),identity='b'.repeat(64),origin='https://192.0.2.18:8443';
const key='sdsctlDeviceRecovery';
const pause=()=>({version:1,identity,paused:true,phase:'clean',nextAt:0});
const sender=(page='startup.html')=>({id,url:`chrome-extension://${id}/${page}`,frameId:0,
  documentLifecycle:'active',documentId:'doc-1',tab:{id:1,incognito:false}});
const native=()=>({version:1,ok:true,mode:'paused',revision:4,retry_after:0,renew_after:0});
const settle=()=>new Promise(r=>setImmediate(r));
function fixture() {
  const f={calls:[],unexpected:[],saved:{[key]:pause()},native:native(),cookie:null,alarm:undefined};
  const fail=name=>()=>{f.unexpected.push(name);throw Error('forbidden');};
  f.chrome={runtime:{id,getURL:name=>`chrome-extension://${id}/${name}`,
    onMessage:{addListener:fn=>{f.handler=fn;}},sendNativeMessage:async(host,body)=>{
      f.calls.push(['native',host,body]);return f.native;
    }},storage:{local:{setAccessLevel:async value=>{f.calls.push(['access',value]);},
      get:async value=>{f.calls.push(['get',value]);return structuredClone(f.saved);},set:fail('storage.set')}},
    cookies:{get:async value=>{f.calls.push(['cookie',value]);return f.cookie;},
      set:fail('cookie.set'),remove:fail('cookie.remove')},
    alarms:{get:async name=>{f.calls.push(['alarm',name]);return f.alarm;},
      create:fail('alarm.create'),clear:fail('alarm.clear')},
    tabs:{create:fail('tabs.create'),update:fail('tabs.update')}};
  f.start=(...timers)=>connectPausedBrowserWorker(f.chrome,
    {origin,identity,nativeHost:'org.sdsctl.browser_device'},...timers);
  f.send=(action='startup-status',from=sender(),extra={})=>new Promise(resolve=>{
    if(!f.handler({action,...extra},from,resolve))resolve(undefined);
  });
  return f;
}
test('construction restricts storage but never runs normal recovery',async()=>{
  const f=fixture();f.start();await settle();
  assert.deepEqual(f.calls,[['access',{accessLevel:'TRUSTED_CONTEXTS'}]]);
  assert.deepEqual(f.unexpected,[]);
});
for(const [action,page,result] of [
  ['startup-status','startup.html',{mode:'administrator_required',sessionReady:false}],
  ['resume-review','resume.html',{mode:'administrator_required'}],
])test(`read-only ${action} confirms clean pause without granting permission`,async()=>{
  const f=fixture(),before=structuredClone(f.saved);f.start();
  assert.deepEqual(await f.send(action,sender(page)),result);
  assert.deepEqual(f.calls.filter(c=>c[0]==='native'),[
    ['native','org.sdsctl.browser_device',{version:1,action:'status'}]]);
  assert.deepEqual(f.saved,before);assert.deepEqual(f.unexpected,[]);
});
for(const saved of [{},null,[],{[key]:{}},{[key]:{...pause(),paused:false}},
  {[key]:{...pause(),version:2,phase:'resume_pending',intent:'c'.repeat(64)}},
  {[key]:{...pause(),identity:'c'.repeat(64)}},{[key]:{...pause(),nextAt:1}},
  {[key]:pause(),other:'private'}])test(`unsafe saved state is not repaired: ${JSON.stringify(saved)}`,async()=>{
  const f=fixture();f.saved=saved;f.start();
  assert.deepEqual(await f.send(),{mode:'setup_error',sessionReady:false});
  assert.equal(f.calls.filter(c=>c[0]==='native').length,0);assert.deepEqual(f.unexpected,[]);
});
for(const result of [null,{}, {...native(),mode:'active'}, {...native(),revision:true},
  {...native(),session:{token:'private'}},{...native(),retry_after:1},{...native(),renew_after:1}]) {
  test(`invalid native status cannot grant paused-only readiness ${JSON.stringify(result)}`,async()=>{
    const f=fixture();f.native=result;f.start();
    assert.deepEqual(await f.send(),{mode:'setup_error',sessionReady:false});
    assert.deepEqual(f.unexpected,[]);
  });
}
for(const field of ['cookie','alarm'])test(`unexpected ${field} is retained and refused`,async()=>{
  const f=fixture();f[field]={value:'private'};f.start();
  assert.deepEqual(await f.send(),{mode:'setup_error',sessionReady:false});
  assert.deepEqual(f[field],{value:'private'});assert.deepEqual(f.unexpected,[]);
});
for(const action of ['initialize','start','suspend','status','authenticate','resume-confirm',
  'logout-begin','retirement-review'])test(`no ${action} handler exists in paused-only role`,async()=>{
  const f=fixture();f.start();await settle();const before=[...f.calls];
  for(const page of ['startup.html','setup.html','control.html','resume.html','recovery.html'])
    assert.equal(await f.send(action,sender(page)),undefined);
  assert.deepEqual(f.calls,before);assert.deepEqual(f.unexpected,[]);
});
for(const change of [{id:'p'.repeat(32)},{frameId:1},{documentLifecycle:'prerender'},
  {documentId:'bad:id'},{tab:{id:1,incognito:true}},{tab:{id:-1,incognito:false}},
  {url:origin+'/'},{url:sender().url+'?x'}])test(`untrusted document refused ${JSON.stringify(change)}`,async()=>{
  const f=fixture();f.start();assert.equal(await f.send('startup-status',{...sender(),...change}),undefined);
  assert.equal(f.calls.filter(c=>c[0]==='native').length,0);
});
test('extra authority fields cannot select permission',async()=>{
  const f=fixture();f.start();assert.equal(await f.send('startup-status',sender(),{role:'normal'}),undefined);
});
test('a changed browser record during inspection is refused',async()=>{
  const f=fixture();f.chrome.alarms.get=async()=>{f.saved[key].paused=false;};f.start();
  assert.deepEqual(await f.send(),{mode:'setup_error',sessionReady:false});
  assert.deepEqual(f.unexpected,[]);
});
test('read failure is redacted without cleanup',async()=>{
  const f=fixture();f.chrome.cookies.get=async()=>{throw Error('private');};f.start();
  assert.deepEqual(await f.send(),{mode:'setup_error',sessionReady:false});
  assert.deepEqual(f.unexpected,[]);
});
for(const boundary of ['access','storage','native','alarm'])test(`${boundary} rejection stays read-only`,async()=>{
  const f=fixture(),failed=async()=>{throw Error('private transport or state detail');};
  if(boundary==='access')f.chrome.storage.local.setAccessLevel=failed;
  if(boundary==='storage')f.chrome.storage.local.get=failed;
  if(boundary==='native')f.chrome.runtime.sendNativeMessage=failed;
  if(boundary==='alarm')f.chrome.alarms.get=failed;
  f.start();await settle();
  assert.deepEqual(await f.send(),{mode:'setup_error',sessionReady:false});
  assert.deepEqual(f.unexpected,[]);
});
test('trusted pages cannot exchange their permitted actions',async()=>{
  const f=fixture();f.start();
  assert.equal(await f.send('startup-status',sender('resume.html')),undefined);
  assert.equal(await f.send('resume-review',sender('startup.html')),undefined);
  assert.equal(f.calls.filter(c=>c[0]==='native').length,0);
});
test('concurrent requests share a bounded read; late result cannot change the reply',async()=>{
  const f=fixture();let expire,finish;
  f.chrome.runtime.sendNativeMessage=()=>new Promise(resolve=>{finish=resolve;});
  f.start(fn=>{expire=fn;return 1;},()=>{});
  const first=f.send(),second=f.send();await settle();expire();
  assert.deepEqual(await first,{mode:'setup_error',sessionReady:false});
  assert.deepEqual(await second,{mode:'setup_error',sessionReady:false});
  finish(native());await settle();assert.deepEqual(f.unexpected,[]);
});
