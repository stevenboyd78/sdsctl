// Both bundle roles load this SAME complete static graph. Imports are inert.
import {connectChromeRecovery} from './browser_device_recovery.mjs';
import {connectLogoutWorker} from './browser_device_logout.mjs';
import {connectBrowserEntry} from './browser_device_startup.mjs';
import {createChromeResumePorts,connectResumeWorker} from './browser_device_resume.mjs';
import {connectChromeRetirementRecovery} from './browser_device_retirement_startup.mjs';
import {connectRecoveryLaunchWorker,connectRecoveryLaunchNavigation} from './browser_device_launch.mjs';

const HOST='org.sdsctl.browser_device';
const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Object.keys(v).sort().join(',')===[...keys].sort().join(',');

export function validateWorkerContext(value,build,id) {
  if(!hex(build)||!exact(value,['version','ok','build','role','config','extensionId','acknowledge','launch'])||
    value.version!==1||value.ok!==true||value.build!==build||value.extensionId!==id||
    typeof id!=='string'||!/^[a-p]{32}$/.test(id)||
    !['normal','recovery'].includes(value.role)||typeof value.acknowledge!=='boolean'||
    !exact(value.config,['origin','identity','nativeHost'])||!hex(value.config.identity)||
    value.config.nativeHost!==HOST||typeof value.config.origin!=='string'||
    value.config.origin.length>2048||!value.config.origin.startsWith('https://')||
    new URL(value.config.origin).origin!==value.config.origin)throw Error('Worker context refused');
  if(value.role==='normal'&&(value.acknowledge||value.launch!==null))throw Error('Worker context refused');
  if(value.launch!==null&&(!value.acknowledge||value.role!=='recovery'||
    !exact(value.launch,['identity','intent','binding','nativeHost'])||
    value.launch.identity!==value.config.identity||value.launch.nativeHost!==HOST||
    !hex(value.launch.intent)||!hex(value.launch.binding)))throw Error('Worker context refused');
  return Object.freeze({...value,config:Object.freeze({...value.config}),
    launch:value.launch===null?null:Object.freeze({...value.launch})});
}

export async function startBrowserWorker(chrome,build) {
  if(!hex(build))throw Error('Worker context refused');
  // No listener, browser-state access, normal tick or recovery navigation until
  // the fixed selected native endpoint validates its current canonical context.
  const context=validateWorkerContext(await chrome.runtime.sendNativeMessage(HOST,
    {version:1,action:'worker-context',build}),build,chrome.runtime.id);
  // Every subsequent native action carries the executing graph's identity. A
  // cached old worker cannot bypass the new host's build check with old envelopes.
  const runtime=Object.create(chrome.runtime);
  Object.defineProperty(runtime,'sendNativeMessage',{value:(host,request)=>{
    if(host!==HOST)throw Error('Worker host refused');
    return chrome.runtime.sendNativeMessage(HOST,{version:1,action:'worker-request',build,request});
  }});
  const scoped=Object.create(chrome);Object.defineProperty(scoped,'runtime',{value:runtime});
  if(context.role==='recovery') {
    connectChromeRetirementRecovery(scoped,context.config,context.acknowledge);
    if(context.launch!==null) {
      connectRecoveryLaunchWorker(scoped,context.launch);
      connectRecoveryLaunchNavigation(scoped,context.launch);
    }
    return;
  }
  const controller=connectChromeRecovery(scoped,context.config,createChromeResumePorts(scoped,context.config));
  const logout=connectLogoutWorker(scoped,controller,context.config.origin);
  connectResumeWorker(scoped,controller,Date.now,logout.retireAfterResume);
  connectBrowserEntry(scoped);
}
