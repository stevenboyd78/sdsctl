// Ordinary-route read-only startup inspection, NOT a
// readiness lease, consent, native authority or permission to repair a profile.
// The trusted caller supplies fixed native context and an ALREADY build-scoped
// Chrome facade. A terminal result must not construct an authentication lane.
import {validateContinuationContext} from './browser_device_continuation_context.mjs';
import {classifyContinuationStorage} from './browser_device_continuation_state.mjs';

const HOST='org.sdsctl.browser_device',COOKIE='__Host-sdsctl-device-session';
const unavailable=()=>Object.freeze({mode:'administrator_required',sessionReady:false});
const refusal=()=>Error('Browser startup inspection is unconfirmed; retain saved state.');
const finite=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<Number.MAX_SAFE_INTEGER;
const same=(a,b)=>b!==null&&typeof b==='object'?
  a!==null&&typeof a==='object'&&!Array.isArray(a)&&
  Reflect.ownKeys(a).length===Reflect.ownKeys(b).length&&
  Reflect.ownKeys(b).every(k=>Object.hasOwn(a,k)&&same(a[k],b[k])):a===b;

export function createContinuationStartupInspection(chrome,initial,build,
  {wall=Date.now,monotonic=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout}={}) {
  let id,selected;
  try {
    id=chrome.runtime.id;selected=validateContinuationContext(initial,build,id);
    if([wall,monotonic,schedule,cancel,chrome.storage.local.get,chrome.cookies.get,
      chrome.alarms.getAll,chrome.runtime.sendNativeMessage].some(v=>typeof v!=='function'))throw refusal();
  } catch {throw refusal();}
  const {settings,observed}=selected;
  let used=false,failed=false,rejectPending=null;
  const invalidate=()=>{failed=true;rejectPending?.(refusal());};
  return Object.freeze({invalidate,run:async(...args)=>{
    if(args.length||used||failed)throw refusal();
    used=true;let timer,started,last,cleaned=false;
    const sample=()=>{
      const now=[wall(),monotonic()];if(!now.every(finite))throw refusal();return now;
    };
    const check=()=>{
      const now=sample();
      if(failed||chrome.runtime.id!==id||now.some((v,i)=>v<last[i]||v-started[i]>=12000))throw refusal();
      last=now;
    };
    const io=async operation=>{check();const result=await operation();check();return result;};
    const cleanup=()=>{
      if(cleaned)return;cleaned=true;cancel(timer);
    };
    const storage=async()=>{
      const value=await io(()=>chrome.storage.local.get(null));
      const classification=classifyContinuationStorage(settings,value,observed);
      // STOP/unknown state does not even reach a current-native request. No
      // lifecycle is constructed as a side effect of this terminal status.
      if(classification.mode==='administrator_required')throw refusal();
      const saved=structuredClone(value);check();
      if(classifyContinuationStorage(settings,saved,observed).mode!==classification.mode)throw refusal();
      return {saved,classification};
    };
    const localPause=async()=>{
      if(observed.mode==='paused') {
        const cookie=await io(()=>chrome.cookies.get({url:settings.origin+'/',name:COOKIE}));
        if(cookie!==null&&cookie!==undefined)throw refusal();
      }
      const alarms=await io(()=>chrome.alarms.getAll());
      if(!Array.isArray(alarms)||alarms.length!==0)throw refusal();
    };
    const current=async()=>{
      const raw=await io(()=>chrome.runtime.sendNativeMessage(HOST,{version:1,action:'continuation-current'}));
      if(!same(validateContinuationContext(raw,build,id),selected))throw refusal();check();
    };
    try {
      started=sample();last=started;
      const interrupted=new Promise((_,reject)=>{rejectPending=reject;});
      void interrupted.catch(()=>{});
      timer=schedule(invalidate,12000);
      const work=(async()=>{
        const first=await storage();await localPause();await current();
        const second=await storage();
        if(!same(second.saved,first.saved)||!same(second.classification,first.classification))throw refusal();
        await localPause();await current();check();
        return first.classification;
      })();
      const result=await Promise.race([work,interrupted]);cleanup();check();return result;
    } catch {invalidate();return unavailable();}
    finally {
      rejectPending=null;
      try {cleanup();} catch {invalidate();return unavailable();}
    }
  }});
}
