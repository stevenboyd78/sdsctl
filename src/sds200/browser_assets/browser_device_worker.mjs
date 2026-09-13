// Both bundle roles load this SAME complete static graph. Imports are inert.
import {connectChromeRecovery} from './browser_device_recovery.mjs';
import {connectLogoutWorker} from './browser_device_logout.mjs';
import {connectBrowserEntry,connectContinuationEntry} from './browser_device_startup.mjs';
import {createChromeResumePorts,connectResumeWorker} from './browser_device_resume.mjs';
import {connectChromeRetirementRecovery} from './browser_device_retirement_startup.mjs';
import {connectRecoveryLaunchWorker,connectRecoveryLaunchNavigation} from './browser_device_launch.mjs';
import {createWorkerEventGate} from './browser_device_worker_gate.mjs';
import {connectPausedBrowserWorker} from './browser_device_paused.mjs';
import {validateContinuationContext} from './browser_device_continuation_context.mjs';
import {createContinuationStartupInspection} from './browser_device_continuation_inspect.mjs';
import {createContinuationLifecycle} from './browser_device_continuation_lifecycle.mjs';

const HOST='org.sdsctl.browser_device';
const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Object.keys(v).sort().join(',')===[...keys].sort().join(',');

export function validateWorkerContext(value,build,id) {
  if(!hex(build)||!exact(value,['version','ok','build','role','config','extensionId','acknowledge','launch'])||
    value.version!==1||value.ok!==true||value.build!==build||value.extensionId!==id||
    typeof id!=='string'||!/^[a-p]{32}$/.test(id)||
    !['normal','paused','recovery'].includes(value.role)||typeof value.acknowledge!=='boolean'||
    !exact(value.config,['origin','identity','nativeHost'])||!hex(value.config.identity)||
    value.config.nativeHost!==HOST||typeof value.config.origin!=='string'||
    value.config.origin.length>2048||!value.config.origin.startsWith('https://')||
    new URL(value.config.origin).origin!==value.config.origin)throw Error('Worker context refused');
  if(value.role!=='recovery'&&(value.acknowledge||value.launch!==null))throw Error('Worker context refused');
  if(value.launch!==null&&(!value.acknowledge||value.role!=='recovery'||
    !exact(value.launch,['identity','intent','binding','nativeHost'])||
    value.launch.identity!==value.config.identity||value.launch.nativeHost!==HOST||
    !hex(value.launch.intent)||!hex(value.launch.binding)))throw Error('Worker context refused');
  return Object.freeze({...value,config:Object.freeze({...value.config}),
    launch:value.launch===null?null:Object.freeze({...value.launch})});
}

// Native-selected continuation only. STOP/unknown state never constructs a
// lifecycle; inspection is read-only and the original MV3 gate budget applies.
async function connectOrdinaryContinuation(gate,scoped,initial,build,clocks) {
  const id=scoped.runtime.id,selected=validateContinuationContext(initial,build,id);
  const fixed=Object.freeze({version:1,ok:true,build,role:'continuation',extensionId:id,
    acknowledge:false,launch:null,config:Object.freeze({origin:selected.settings.origin,
      identity:selected.settings.identity,nativeHost:HOST}),continuation:Object.freeze({
      epoch:selected.settings.epoch,mode:selected.observed.mode,binding:selected.observed.binding})});
  const inspection=createContinuationStartupInspection(scoped,fixed,build,clocks);
  let disposition;
  try {disposition=await Promise.race([inspection.run(),gate.deadline]);gate.check();}
  catch {inspection.invalidate();throw Error('Worker context refused');}
  const startup=gate.prepareContinuationStartup(),extension=`chrome-extension://${id}`,
    startupURL=scoped.runtime.getURL('startup.html'),resumeURL=scoped.runtime.getURL('resume.html');
  const terminal=()=>({mode:'administrator_required',sessionReady:false});
  let lifecycle=null,used=false,target=null,delivered=false,interrupted=false;
  const wall=clocks.wall??Date.now,monotonic=clocks.monotonic??(()=>performance.now()),
    schedule=clocks.schedule??setTimeout,cancel=clocks.cancel??clearTimeout;
  const stop=()=>{
    interrupted=true;
    if(lifecycle)void lifecycle.stop().catch(()=>{});
  };
  const eligible=(sender,url)=>sender?.id===id&&sender.origin===extension&&sender.url===url&&
    sender.frameId===0&&sender.documentLifecycle==='active'&&
    typeof sender.documentId==='string'&&/^[a-zA-Z0-9-]{1,128}$/.test(sender.documentId)&&
    Number.isSafeInteger(sender.tab?.id)&&sender.tab.id>=0&&sender.tab.incognito===false;
  startup.tabs.onUpdated.addListener((tabId,change)=>{
    if(target!==null&&!delivered&&target.tabId===tabId&&change?.navigating===true)stop();
  });
  const openAccepted=async sender=>{
    // Consume BEFORE context readback. Neither another page nor repeated polls
    // can reuse the point-in-time proof or drive another verification/issuance.
    used=true;target=Object.freeze({tabId:sender.tab.id,documentId:sender.documentId});
    let started,last,timer,loadTimer=null,loadWake=null;
    const sample=()=>{
      const now=[wall(),monotonic()];
      if(now.some(v=>typeof v!=='number'||!Number.isFinite(v)||v<0||v>=Number.MAX_SAFE_INTEGER))throw Error('startup');
      return now;
    };
    const check=()=>{
      const now=sample();
      if(interrupted||lifecycle.isStopped()||scoped.runtime.id!==id||
        now.some((v,i)=>v<last[i]||v-started[i]>=45000))throw Error('startup');
      last=now;
      return Math.max(...now.map((value,index)=>value-started[index]));
    };
    const contexts=async()=>{
      check();const rows=await scoped.runtime.getContexts({contextTypes:['TAB'],
        documentIds:[target.documentId],tabIds:[target.tabId],frameIds:[0],incognito:false});check();
      if(!Array.isArray(rows)||rows.length!==1)throw Error('startup');
      const row=rows[0];
      if(row.contextType!=='TAB'||row.documentId!==target.documentId||row.tabId!==target.tabId||
        row.frameId!==0||row.incognito!==false||row.documentOrigin!==extension||row.documentUrl!==startupURL||
        typeof row.contextId!=='string'||!/^[a-zA-Z0-9-]{1,128}$/.test(row.contextId))throw Error('startup');
      return row.contextId;
    };
    try {
      started=sample();last=started;
      const deadline=new Promise((_,reject)=>{timer=schedule(()=>{stop();reject(Error('startup'));},45000);});
      void deadline.catch(()=>{});
      const work=(async()=>{
        const context=await contexts();
        let waits=0;
        while(true) {
          const tab=await scoped.tabs.get(target.tabId);check();
          if(tab?.id!==target.tabId||tab.incognito!==false||
            !['loading','complete'].includes(tab.status)||tab.url!==startupURL||tab.pendingUrl)
            throw Error('startup');
          if(await contexts()!==context)throw Error('startup');check();
          if(tab.status==='complete')break;
          // The committed startup module may run before tab load completes.
          // Wait only for this unchanged document, before any server request;
          // navigation events still fence it and the original deadline remains.
          if(check()>=10000||waits++>=100)throw Error('startup');
          await new Promise(resolve=>{
            loadWake=resolve;loadTimer=schedule(()=>{loadTimer=null;loadWake=null;resolve();},100);
          });check();
        }
        const result=await lifecycle.startAccepted();check();
        if(!exact(result,['mode','sessionReady'])||result.mode!=='accepted'||result.sessionReady!==true)
          throw Error('startup');
        return {mode:'active',sessionReady:true};
      })();
      const result=await Promise.race([work,deadline]);cancel(timer);timer=null;check();
      delivered=true;return result; // No await or cached readiness after the fresh verifier.
    } catch {stop();return terminal();}
    finally {
      try {cancel(timer);if(loadTimer!==null)cancel(loadTimer);}
      catch {stop();return terminal();}
      finally {loadTimer=null;const wake=loadWake;loadWake=null;wake?.();}
    }
  };
  scoped.runtime.onMessage.addListener((message,sender,respond)=>{
    if(!exact(message,['action']))return false;
    if(disposition.mode==='administrator_required'&&eligible(sender,resumeURL)&&message.action==='resume-review') {
      respond({mode:'administrator_required'});return false;
    }
    if(!eligible(sender,startupURL)||message.action!=='startup-status')return false;
    if(disposition.mode==='administrator_required'||interrupted||lifecycle?.isStopped()) {
      respond(terminal());return false;
    }
    if(selected.observed.mode==='active') {
      if(used){respond({mode:'continuation_verification_required',sessionReady:false});return false;}
      void openAccepted(sender).then(value=>{try {respond(value);} catch {stop();}});return true;
    }
    // Paused status remains a fresh read-only observation, not cached permission
    // to confirm. The document lane independently rechecks before any issuance.
    void createContinuationStartupInspection(scoped,fixed,build,clocks).run().then(value=>{
      try {respond(interrupted||lifecycle.isStopped()?terminal():value);} catch {/* No readiness or mutation. */}
    });return true;
  });
  try {
    gate.check();
    if(disposition.mode!=='administrator_required')lifecycle=createContinuationLifecycle(gate,fixed,build,clocks);
    connectContinuationEntry(scoped,clocks.schedule);
    // No await between owner construction and replay of the queued stop/events.
    gate.open();
  } catch {stop();throw Error('Worker context refused');}
}

export async function startBrowserWorker(chrome,build,clocks={}) {
  if(!hex(build))throw Error('Worker context refused');
  const gate=createWorkerEventGate(chrome,clocks.monotonic,clocks.schedule,clocks.cancel);
  try {
  // Inert receivers are synchronous; no role-specific handler, browser-state
  // access, startup tick or recovery navigation precedes validated context.
  const raw=await Promise.race([
    chrome.runtime.sendNativeMessage(HOST,{version:1,action:'worker-context',build}),
    gate.deadline]);
  const continuation=raw?.role==='continuation';
  const context=continuation?validateContinuationContext(raw,build,chrome.runtime.id):
    validateWorkerContext(raw,build,chrome.runtime.id);
  gate.check(); // Also reject a late result when a throttled timer has not fired.
  // Every subsequent native action carries the executing graph's identity. A
  // cached old worker cannot bypass the new host's build check with old envelopes.
  const runtime=Object.create(gate.chrome.runtime);
  Object.defineProperty(runtime,'sendNativeMessage',{value:(host,request)=>{
    if(host!==HOST)throw Error('Worker host refused');
    return chrome.runtime.sendNativeMessage(HOST,{version:1,action:'worker-request',build,request});
  }});
  const scoped=Object.create(gate.chrome);Object.defineProperty(scoped,'runtime',{value:runtime});
  if(continuation) {
    await connectOrdinaryContinuation(gate,scoped,raw,build,clocks);
    return;
  }
  if(context.role==='paused') {
    connectPausedBrowserWorker(scoped,context.config);
    connectBrowserEntry(scoped);
    gate.open();return;
  }
  if(context.role==='recovery') {
    connectChromeRetirementRecovery(scoped,context.config,context.acknowledge);
    if(context.launch!==null) {
      connectRecoveryLaunchWorker(scoped,context.launch);
      connectRecoveryLaunchNavigation(scoped,context.launch);
    }
    gate.open();return;
  }
  const controller=connectChromeRecovery(scoped,context.config,createChromeResumePorts(scoped,context.config));
  const logout=connectLogoutWorker(scoped,controller,context.config.origin);
  connectResumeWorker(scoped,controller,Date.now,logout.retireAfterResume);
  connectBrowserEntry(scoped);
  gate.open();
  } catch {gate.fail();throw Error('Worker context refused');}
}
