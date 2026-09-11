// Native-selected continuation reader. No storage/cookie writes, initialization,
// consent, session issuance, renewal or migration. Only explicit review goes online.
import {validateContinuationContext} from './browser_device_continuation_context.mjs';
const KEY='sdsctlDeviceRecovery', HOST='org.sdsctl.browser_device';
const COOKIE='__Host-sdsctl-device-session', ALARM='sdsctl-device-recovery';
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Object.keys(v).sort().join(',')===[...keys].sort().join(',');
const integer=v=>Number.isSafeInteger(v)&&v>0&&v<Number.MAX_SAFE_INTEGER;
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const refusal=()=>Error('Browser continuation review is unconfirmed; retain saved state.');

function cleanPaused(values,settings) {
  if(!exact(values,[KEY]))return false;
  const v=values[KEY];
  if(exact(v,['version','identity','paused','phase','nextAt']))return v.version===1&&
    v.identity===settings.identity&&v.paused===true&&v.phase==='clean'&&v.nextAt===0;
  return exact(v,['version','identity','epoch','build','phase','binding','intent','cookieFingerprint'])&&
    v.version===3&&v.identity===settings.identity&&v.epoch===settings.epoch&&
    v.build===settings.build&&v.phase==='paused'&&v.binding===null&&v.intent===null&&
    v.cookieFingerprint===null;
}

export function createContinuationPausedReader(chrome,initial,build,
  {wall=Date.now,monotonic=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout}={}) {
  // Copy/validate even this internal entry; ordinary start obtains it only from
  // the fixed native context behind the synchronous MV3 event gate.
  const selected=validateContinuationContext(initial,build,chrome.runtime.id);
  const settings=selected.settings;
  let busy=false,failed=false;
  const access=Promise.resolve().then(()=>
    chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'}));
  access.catch(()=>{failed=true;});
  const send=action=>chrome.runtime.sendNativeMessage(HOST,{version:1,action});
  const context=async()=>{
    const result=validateContinuationContext(await send('continuation-current'),build,chrome.runtime.id);
    if(!same(result.settings,settings)||result.observed.mode!=='paused')throw refusal();
    return result.observed;
  };
  const inspect=async(online)=>{
    if(failed||busy)throw refusal();
    busy=true;
    let timer;
    const sample=()=>{
      const pair=[wall(),monotonic()];
      if(pair.some(v=>typeof v!=='number'||!Number.isFinite(v)||v<0||
        v>=Number.MAX_SAFE_INTEGER))throw refusal();
      return pair;
    };
    let started,last;
    try {started=sample();last=started;} catch {failed=true;busy=false;throw refusal();}
    const check=()=>{
      const now=sample();
      if(failed||now.some((n,i)=>n<last[i]||n-started[i]>=12000))throw refusal();
      last=now;
    };
    const snapshot=async()=>{
      const values=structuredClone(await chrome.storage.local.get(null));check();
      if(!cleanPaused(values,settings))throw refusal();
      return values;
    };
    const noSession=async()=>{
      const cookie=await chrome.cookies.get({url:settings.origin+'/',name:COOKIE});check();
      if(cookie!==null&&cookie!==undefined)throw refusal();
      const alarm=await chrome.alarms.get(ALARM);check();
      if(alarm!==null&&alarm!==undefined)throw refusal();
    };
    const work=(async()=>{
      await access;check();
      const saved=await snapshot(), before=await context();check();
      await noSession();
      let generation=null;
      if(online) {
        const value=await send('continuation-review');check();
        if(!exact(value,['version','ok','build','identity','epoch','mode','binding'])||
          value.version!==1||value.ok!==true||value.build!==build||
          value.identity!==settings.identity||value.epoch!==settings.epoch||value.mode!=='paused'||
          !exact(value.binding,['fingerprint','revision','generation'])||
          value.binding.fingerprint!==before.binding.fingerprint||
          value.binding.revision!==before.binding.revision||!integer(value.binding.generation))throw refusal();
        generation=value.binding.generation;
      }
      const after=await context();check();
      if(!same(after,before)||!same(await snapshot(),saved))throw refusal();
      await noSession();check();
      return {saved,observed:{...before,binding:{...before.binding,generation}}};
    })();
    // A timeout is NOT cancellation. Never release an undrained lane or let a
    // late reply revive it. This worker stays failed; saved bytes are untouched.
    void work.then(()=>{busy=false;},()=>{busy=false;});
    try {
      const result=await Promise.race([work,new Promise((_,reject)=>{
        timer=schedule(()=>{failed=true;reject(refusal());},12000);
      })]);
      check();return result;
    } catch {failed=true;throw refusal();}
    finally {cancel(timer);}
  };
  return Object.freeze({settings,inspect,invalidate:()=>{failed=true;}});
}

export function connectContinuationBrowserWorker(chrome,initial,build,options={}) {
  const reader=createContinuationPausedReader(chrome,initial,build,options);
  chrome.runtime.onMessage.addListener((message,sender,respond)=>{
    if(sender?.id!==chrome.runtime.id||sender.frameId!==0||sender.documentLifecycle!=='active'||
      typeof sender.documentId!=='string'||!/^[a-zA-Z0-9-]{1,128}$/.test(sender.documentId)||
      !Number.isSafeInteger(sender.tab?.id)||sender.tab.id<0||sender.tab.incognito!==false||
      !exact(message,['action']))return false;
    const startup=sender.url===chrome.runtime.getURL('startup.html')&&message.action==='startup-status';
    const review=sender.url===chrome.runtime.getURL('resume.html')&&message.action==='resume-review';
    if(!startup&&!review)return false;
    void reader.inspect(review).then(value=>{try {respond(review?
      {mode:'continuation_reviewed',nativeRevision:value.observed.binding.revision,
        serverGeneration:value.observed.binding.generation}:{mode:'paused',sessionReady:false});
    } catch {/* Closed document; no replay. */}},
      ()=>{try {respond(startup?{mode:'administrator_required',sessionReady:false}:
        {mode:'administrator_required'});} catch {/* No mutation or late retry. */}});
    return true;
  });
}
