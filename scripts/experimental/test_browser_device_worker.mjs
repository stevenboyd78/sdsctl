import test from 'node:test';
import assert from 'node:assert/strict';
import {startBrowserWorker,validateWorkerContext} from '../../src/sds200/browser_assets/browser_device_worker.mjs';

const build='b'.repeat(64), id='a'.repeat(32), identity='c'.repeat(64);
const context=()=>({version:1,ok:true,build,extensionId:id,role:'recovery',
  config:{origin:'https://127.0.0.1:8443',identity,nativeHost:'org.sdsctl.browser_device'},
  acknowledge:false,launch:null});

function event(){const listeners=[];return {listeners,addListener:fn=>listeners.push(fn)};}
function receivers(runtime={}) {
  return {runtime:{id,getURL:name=>`chrome-extension://${id}/${name}`,
    onMessage:event(),onStartup:event(),onInstalled:event(),...runtime},
    alarms:{onAlarm:event()},tabs:{onUpdated:event()}};
}
const sender=page=>({id,url:`chrome-extension://${id}/${page}`,frameId:0,
  documentLifecycle:'active',documentId:'doc-1',tab:{id:1,incognito:false}});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});
  return {promise,resolve,reject};};

for(const [name,change] of [
  ['missing',()=>null],['failed',v=>({...v,ok:false})],['old build',v=>({...v,build:'d'.repeat(64)})],
  ['unknown role',v=>({...v,role:'operator'})],['another extension',v=>({...v,extensionId:'p'.repeat(32)})],
  ['extra path',v=>({...v,directory:'/tmp/elsewhere'})],['numeric version',v=>({...v,version:'1'})],
  ['normal ACK',v=>({...v,role:'normal',acknowledge:true})],
  ['bad origin',v=>({...v,config:{...v.config,origin:'http://127.0.0.1'}})],
  ['extra config',v=>({...v,config:{...v.config,mode:'normal'}})],
  ['wrong host',v=>({...v,config:{...v.config,nativeHost:'other.host'}})],
  ['unbound launch',v=>({...v,launch:{identity,intent:build,binding:build,nativeHost:v.config.nativeHost}})],
  ['bad launch',v=>({...v,acknowledge:true,launch:{binding:build}})],
]) test(`reject ${name} without any controller side effects`,async()=>{
  const calls=[],value=change(context());
  const chrome=receivers({sendNativeMessage:async(host,request)=>{
    calls.push(request);return value;
  }});
  await assert.rejects(startBrowserWorker(chrome,build));
  assert.deepEqual(calls,[{version:1,action:'worker-context',build}]);
});

test('waiting for native context initializes nothing',async()=>{
  let resolve;const result=new Promise(r=>{resolve=r;});
  const chrome=receivers({sendNativeMessage:()=>result});
  const start=startBrowserWorker(chrome,build);
  assert.equal(chrome.runtime.onMessage.listeners.length,1); // BEFORE the await.
  const response=deferred();
  assert.equal(chrome.runtime.onMessage.listeners[0]({action:'initialize'},sender('setup.html'),
    response.resolve),true);
  await new Promise(r=>setImmediate(r));
  resolve({ok:false});await assert.rejects(start);
  assert.deepEqual(await response.promise,{mode:'setup_error'});
});

test('context copied and mode consistency enforced',()=>{
  const value=context();const checked=validateWorkerContext(value,build,id);
  value.config.origin='https://other.example';value.role='normal';
  assert.equal(checked.role,'recovery');assert.equal(checked.config.origin,'https://127.0.0.1:8443');
  assert(Object.isFrozen(checked));assert(Object.isFrozen(checked.config));
});

test('recovery installs only confirmation controls, with no normal startup I/O',async()=>{
  const calls=[],listeners=[];
  const forbidden=()=>assert.fail('normal capability used');
  const chrome=receivers({onMessage:{addListener:fn=>listeners.push(fn)},
    sendNativeMessage:async(host,request)=>{
      calls.push(request);return context();
    }});
  chrome.storage={local:{setAccessLevel:async()=>{},get:forbidden,set:forbidden}};
  chrome.cookies={get:forbidden,remove:forbidden,set:forbidden};
  Object.assign(chrome.alarms,{clear:forbidden,create:forbidden});
  Object.assign(chrome.tabs,{query:forbidden,create:forbidden});
  await startBrowserWorker(chrome,build);
  assert.equal(listeners.length,1);assert.equal(calls.length,1);
  // Inert ingress may observe events, but recovery has NO normal handlers.
  chrome.runtime.onStartup.listeners[0]();chrome.runtime.onInstalled.listeners[0]();
  chrome.alarms.onAlarm.listeners[0]({name:'sdsctl-device-recovery'});
  for(const action of ['initialize','startup-status','status','suspend','prepare-resume','resume']) {
    assert.equal(listeners[0]({action},{id,url:chrome.runtime.getURL('setup.html')},forbidden),false);
  }
});

test('recovery native proof is bound to the executing build',async()=>{
  const calls=[],listeners=[];const nativeHost='org.sdsctl.browser_device';
  const chrome=receivers({onMessage:{addListener:fn=>listeners.push(fn)},sendNativeMessage:async(host,request)=>{
      calls.push(request);return request.action==='worker-context'?context():{version:1,ok:false};
    }});
  chrome.storage={local:{setAccessLevel:async()=>{},get:async()=>({sdsctlDeviceRecovery:{
      version:2,identity,paused:true,phase:'resume_pending',nextAt:0,intent:build}})}};
  await startBrowserWorker(chrome,build);
  const sender={id,url:chrome.runtime.getURL('recovery.html'),frameId:0,
    documentLifecycle:'active',documentId:'doc-1',tab:{id:1,incognito:false}};
  await new Promise(resolve=>{
    assert.equal(listeners[0]({action:'retirement-review'},sender,resolve),true);
  });
  assert.deepEqual(calls[1],{version:1,action:'worker-request',build,
    request:{version:1,action:'confirm-retirement',identity,intent:build}});
  assert.equal(nativeHost,context().config.nativeHost);
});

function normalChrome(initial) {
  const validation=deferred(), calls=[], io=[];let saved=initial;
  const chrome=receivers({sendNativeMessage:async(host,request)=>{
    calls.push(request);
    if(request.action==='worker-context')return validation.promise;
    assert.equal(request.action,'worker-request');assert.equal(request.build,build);
    const action=request.request.action;
    assert(['claim-browser','suspend'].includes(action),`Unexpected native action ${action}`);
    return {version:1,ok:true,mode:action==='suspend'?'paused':'active',revision:2,
      retry_after:0,renew_after:0};
  }});
  chrome.storage={local:{setAccessLevel:async()=>{io.push('access');},
    get:async()=>{io.push('load');return saved===undefined?{}:{sdsctlDeviceRecovery:{...saved}};},
    set:async value=>{io.push('save');saved={...value.sdsctlDeviceRecovery};}}};
  chrome.cookies={get:async()=>null,remove:async()=>{io.push('clear');},
    set:()=>assert.fail('Authentication/cookie installation forbidden')};
  Object.assign(chrome.alarms,{clear:async()=>true,create:async()=>{io.push('schedule');}});
  Object.assign(chrome.tabs,{query:async()=>[],get:async()=>{},reload:async()=>{io.push('reload');}});
  return {chrome,calls,io,validation,saved:()=>saved,
    allow:()=>validation.resolve({...context(),role:'normal'})};
}

test('cold initialize is held synchronously, then dispatched exactly once after validation',async()=>{
  const f=normalChrome(), response=deferred();
  const start=startBrowserWorker(f.chrome,build);
  assert.equal(f.chrome.runtime.onMessage.listeners.length,1);
  assert.equal(f.chrome.runtime.onMessage.listeners[0]({action:'initialize'},sender('setup.html'),
    response.resolve),true);
  assert.deepEqual(f.io,[]);assert.equal(f.calls.length,1);
  f.allow();await start;
  assert.deepEqual(await response.promise,{mode:'ready'});
  assert.equal(f.calls.filter(c=>c.request?.action==='claim-browser').length,1);
  assert.deepEqual(f.saved(),{version:1,identity,paused:false,phase:'clean',nextAt:0});
});

test('queued logout records pause before startup tick can authenticate',async()=>{
  const f=normalChrome({version:1,identity,paused:false,phase:'clean',nextAt:0});
  const response=deferred(), start=startBrowserWorker(f.chrome,build);
  const document={...sender('unused'),url:context().config.origin+'/device-display',
    origin:context().config.origin};
  assert.equal(f.chrome.runtime.onMessage.listeners[0]({action:'logout-begin'},document,
    response.resolve),true);
  assert.deepEqual(f.io,[]);f.allow();await start;
  const result=await response.promise;
  assert.equal(result.mode,'logout_pending');assert.equal(result.submit,true);
  assert.equal(f.saved().paused,true);
  assert(!f.calls.some(c=>c.request?.action==='authenticate'));
});

test('queued normal initialization cannot acquire recovery authority',async()=>{
  const f=normalChrome(), response=deferred(), start=startBrowserWorker(f.chrome,build);
  f.chrome.runtime.onMessage.listeners[0]({action:'initialize'},sender('setup.html'),response.resolve);
  f.validation.resolve(context());await start;
  assert.deepEqual(await response.promise,{mode:'setup_error'});
  assert.equal(f.calls.length,1);assert(!f.io.includes('load'));assert(!f.io.includes('save'));
});

for(const outcome of ['reject','old-build','invalid-origin'])test(`queued initialize refused on ${outcome}`,async()=>{
  const f=normalChrome(), response=deferred(), start=startBrowserWorker(f.chrome,build);
  f.chrome.runtime.onMessage.listeners[0]({action:'initialize'},sender('setup.html'),response.resolve);
  if(outcome==='reject')f.validation.reject(Error('private diagnostic'));
  else f.validation.resolve(outcome==='old-build'?{...context(),build:'d'.repeat(64)}:
    {...context(),config:{...context().config,origin:'http://bad.example'}});
  await assert.rejects(start,/Worker context refused/);
  assert.deepEqual(await response.promise,{mode:'setup_error'});
  assert.deepEqual(f.io,[]);assert.equal(f.calls.length,1);
});

test('overflow poisons validation; a late valid native response initializes nothing',async()=>{
  const f=normalChrome(), replies=[], start=startBrowserWorker(f.chrome,build);
  for(let i=0;i<65;i++)f.chrome.runtime.onMessage.listeners[0]({action:'initialize'},sender('setup.html'),
    r=>replies.push(r));
  await assert.rejects(start);f.allow();await new Promise(r=>setImmediate(r));
  assert.equal(replies.length,65);assert.deepEqual(f.io,[]);assert.equal(f.calls.length,1);
});
