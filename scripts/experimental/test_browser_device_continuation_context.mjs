import test from 'node:test';
import assert from 'node:assert/strict';
import {validateContinuationContext} from '../../src/sds200/browser_assets/browser_device_continuation_context.mjs';
import {validateWorkerContext} from '../../src/sds200/browser_assets/browser_device_worker.mjs';
import {classifyContinuationStartup,pausedContinuationRecord} from '../../src/sds200/browser_assets/browser_device_continuation_state.mjs';

const build='a'.repeat(64),id='b'.repeat(32);
const document=(mode='paused',origin='https://192.0.2.18:8443')=>({
  version:1,ok:true,build,role:'continuation',config:{origin,identity:'c'.repeat(64),
    nativeHost:'org.sdsctl.browser_device'},extensionId:id,acknowledge:false,launch:null,
  continuation:{epoch:'d'.repeat(64),mode,binding:{fingerprint:'e'.repeat(64),
    revision:4,generation:mode==='active'?7:null}},
});
const refused=value=>assert.throws(()=>validateContinuationContext(value,build,id),
  {message:'Browser continuation context is unavailable or changed.'});
for(const origin of ['https://display.example','https://192.0.2.18:8443','https://[::1]:8443'])
for(const mode of ['paused','active'])test('fixed context is only a read point: '+mode+' '+origin,()=>{
  const input=document(mode,origin),value=validateContinuationContext(input,build,id);
  assert.deepEqual(value.settings,{identity:input.config.identity,origin,epoch:input.continuation.epoch,build});
  assert.deepEqual(value.observed,{identity:input.config.identity,...input.continuation});
  const status=classifyContinuationStartup(value.settings,pausedContinuationRecord(value.settings),value.observed);
  assert.deepEqual(status,{mode:mode==='paused'?'paused':'administrator_required',sessionReady:false});
  assert.throws(()=>validateWorkerContext(input,build,id)); // Current dispatch remains inactive.
  input.continuation.binding.revision++;
  assert.equal(value.observed.binding.revision,4);
  for(const object of [value,value.settings,value.observed,value.observed.binding])assert(Object.isFrozen(object));
  assert(!('session' in value)&&!('token' in value)&&!('permission' in value));
});
const changes={
  absent:()=>null,
  version:v=>({...v,version:true}),
  refused:v=>({...v,ok:false}),
  build:v=>({...v,build:'f'.repeat(64)}),
  extension:v=>({...v,extensionId:'a'.repeat(32)}),
  role:v=>({...v,role:'normal'}),
  acknowledgement:v=>({...v,acknowledge:true}),
  launch:v=>({...v,launch:{}}),
  extra:v=>({...v,session:{token:'PRIVATE'}}),
  config:v=>({...v,config:{...v.config,extra:true}}),
  host:v=>({...v,config:{...v.config,nativeHost:'other'}}),
  identity:v=>({...v,config:{...v.config,identity:'wrong'}}),
  epoch:v=>({...v,continuation:{...v.continuation,epoch:'wrong'}}),
  mode:v=>({...v,continuation:{...v.continuation,mode:'waiting'}}),
  envelope:v=>({...v,continuation:{...v.continuation,accepted:true}}),
  binding:v=>({...v,continuation:{...v.continuation,binding:{...v.continuation.binding,permission:true}}}),
  missing:v=>{delete v.continuation.binding.generation;return v;},
};
for(const [name,change] of Object.entries(changes))test('reject '+name,()=>refused(change(document())));
for(const origin of ['http://192.0.2.18','https://user:password@example.com',
  'https://display.example/','https://DISPLAY.example','https://display.example/path',
  'https://display.example#fragment',null,'https://display.example\n'])
  test('reject noncanonical origin '+origin,()=>refused(document('paused',origin)));
for(const [field,values] of Object.entries({
  fingerprint:[null,'x',true],
  revision:[0,-1,1.5,true,'4',Number.MAX_SAFE_INTEGER],
  generation:[0,-1,1.5,true,'7',null,Number.MAX_SAFE_INTEGER],
}))for(const value of values)test('reject active binding '+field+' '+value,()=>{
  const v=document('active');v.continuation.binding[field]=value;refused(v);
});
for(const generation of [0,1,true,'7'])test('paused context has no reviewed generation '+generation,()=>{
  const v=document();v.continuation.binding.generation=generation;refused(v);
});
for(const [badBuild,badId] of [['wrong',id],[build,'wrong']])
  test('executing identity cannot be caller context '+badBuild+' '+badId,()=>{
    assert.throws(()=>validateContinuationContext(document(),badBuild,badId));
  });
