import test from 'node:test';
import assert from 'node:assert/strict';
import {startBrowserWorker,validateWorkerContext} from '../../src/sds200/browser_assets/browser_device_worker.mjs';

const build='b'.repeat(64), id='a'.repeat(32), identity='c'.repeat(64);
const context=()=>({version:1,ok:true,build,extensionId:id,role:'recovery',
  config:{origin:'https://127.0.0.1:8443',identity,nativeHost:'org.sdsctl.browser_device'},
  acknowledge:false,launch:null});

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
  const chrome={runtime:{id,sendNativeMessage:async(host,request)=>{
    calls.push(request);return value;
  }}};
  await assert.rejects(startBrowserWorker(chrome,build));
  assert.deepEqual(calls,[{version:1,action:'worker-context',build}]);
});

test('waiting for native context initializes nothing',async()=>{
  let resolve;const result=new Promise(r=>{resolve=r;});
  const chrome={runtime:{id,sendNativeMessage:()=>result}};
  const start=startBrowserWorker(chrome,build);
  await new Promise(r=>setImmediate(r));
  resolve({ok:false});await assert.rejects(start);
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
  const chrome={runtime:{id,getURL:name=>`chrome-extension://${id}/${name}`,
    onMessage:{addListener:fn=>listeners.push(fn)},onStartup:{addListener:forbidden},
    onInstalled:{addListener:forbidden},sendNativeMessage:async(host,request)=>{
      calls.push(request);return context();
    }},storage:{local:{setAccessLevel:async()=>{},get:forbidden,set:forbidden}},
    cookies:{get:forbidden,remove:forbidden,set:forbidden},
    alarms:{clear:forbidden,create:forbidden,onAlarm:{addListener:forbidden}},
    tabs:{onUpdated:{addListener:forbidden},query:forbidden,create:forbidden}};
  await startBrowserWorker(chrome,build);
  assert.equal(listeners.length,1);assert.equal(calls.length,1);
  for(const action of ['initialize','startup-status','status','suspend','prepare-resume','resume']) {
    assert.equal(listeners[0]({action},{id,url:chrome.runtime.getURL('setup.html')},forbidden),false);
  }
});

test('recovery native proof is bound to the executing build',async()=>{
  const calls=[],listeners=[];const nativeHost='org.sdsctl.browser_device';
  const chrome={runtime:{id,getURL:name=>`chrome-extension://${id}/${name}`,
    onMessage:{addListener:fn=>listeners.push(fn)},sendNativeMessage:async(host,request)=>{
      calls.push(request);return request.action==='worker-context'?context():{version:1,ok:false};
    }},storage:{local:{setAccessLevel:async()=>{},get:async()=>({sdsctlDeviceRecovery:{
      version:2,identity,paused:true,phase:'resume_pending',nextAt:0,intent:build}})}},
    cookies:{},alarms:{}};
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
