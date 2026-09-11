// Experimental terminal stop fence; NOT selected by the active worker/page.
// The future owner must synchronously invalidate its consent/installation lane
// BEFORE calling save. This is no native pause, session revocation or cleanup.
import {pausedContinuationRecord} from './browser_device_continuation_state.mjs';
const KEY='sdsctlContinuationStop';
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Object.keys(v).sort().join(',')===[...keys].sort().join(',');
const refusal=()=>Error('Browser continuation stop is unconfirmed; retain all saved state.');

export function createContinuationStopFence(chrome,settings,
  {wall=Date.now,monotonic=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout}={}) {
  // Validate selection without browser I/O. No caller-selected key or payload,
  // credential, token, ticket, path, URL or copied acknowledgement is accepted.
  const selected=pausedContinuationRecord(settings);
  const record=Object.freeze({version:1,identity:selected.identity,epoch:selected.epoch,
    build:selected.build,stopped:true});
  let used=false,failed=false,rejectPending=null;
  const stop=()=>{failed=true;rejectPending?.(refusal());};
  return Object.freeze({
    invalidate:stop,
    save:async()=>{
      if(used||failed)throw refusal();
      used=true;let timer,started,last;
      const sample=()=>{
        const pair=[wall(),monotonic()];
        if(pair.some(n=>typeof n!=='number'||!Number.isFinite(n)||n<0||
          n>=Number.MAX_SAFE_INTEGER))throw refusal();
        return pair;
      };
      const check=()=>{
        const now=sample();
        if(failed||now.some((n,i)=>n<last[i]||n-started[i]>=10000))throw refusal();
        last=now;
      };
      const io=async operation=>{check();const result=await operation();check();return result;};
      try {
        started=sample();last=started;
        const interrupted=new Promise((_,reject)=>{rejectPending=reject;timer=schedule(stop,10000);});
        const work=(async()=>{
          await io(()=>chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'}));
          // Even an identical existing marker is retained, not adopted as this
          // attempt's acknowledgement. No readback/retry/clear method exists.
          if(!exact(await io(()=>chrome.storage.local.get(KEY)),[]))throw refusal();
          await io(()=>chrome.storage.local.set({[KEY]:record}));
          const saved=await io(()=>chrome.storage.local.get(KEY));
          if(!exact(saved,[KEY])||!exact(saved[KEY],Object.keys(record))||
            Object.keys(record).some(key=>saved[KEY][key]!==record[key]))throw refusal();
          return Object.freeze({mode:'continuation_stopped',browserStopSaved:true,
            nativePauseConfirmed:false,serverRevocationConfirmed:false,sessionReady:false});
        })();
        const result=await Promise.race([work,interrupted]);check();return result;
      } catch {stop();throw refusal();}
      finally {cancel(timer);rejectPending=null;}
    },
  });
}
