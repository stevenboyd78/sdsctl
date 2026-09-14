// Experimental terminal stop fence; NOT selected by the active worker/page.
// The future owner must synchronously invalidate its consent/installation lane
// BEFORE calling save. This is no native pause, session revocation or cleanup.
import {pausedContinuationRecord,classifyContinuationStartup} from './browser_device_continuation_state.mjs';
import {validateContinuationContext} from './browser_device_continuation_context.mjs';
import {createContinuationNativePause} from './browser_device_continuation_native.mjs';
import {fingerprintContinuationCookie} from './browser_device_continuation_cookie.mjs';
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

// Separate, unselected one-use Stop composition. Supplied callbacks are trusted
// worker ports, NOT page messages or evidence of a click. submitLogout must be
// bound to one owned, isolated, same-origin document using submitDeviceLogout;
// extension fetch, caller-provided outcomes and newly issued logout tokens are
// forbidden. The active worker/page does not construct this owner.
export function createContinuationStopOwner(chrome,initial,build,
  {invalidateLanes,readWriteDrain,submitLogout,cookieFingerprint=null,
    recoverAcceptedCookie=false,
    wall=Date.now,monotonic=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout}={}) {
  const strict=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
    Reflect.ownKeys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
  const same=(a,b)=>strict(a,Object.keys(b))&&Object.keys(b).every(k=>a[k]===b[k]);
  const finite=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<Number.MAX_SAFE_INTEGER;
  let selected,id,fence,native;
  try {
    id=chrome.runtime.id;selected=validateContinuationContext(initial,build,id);
    if([invalidateLanes,readWriteDrain,submitLogout,wall,monotonic,schedule,cancel]
      .some(v=>typeof v!=='function')||(cookieFingerprint!==null&&
      (typeof cookieFingerprint!=='string'||!/^[a-f0-9]{64}$/.test(cookieFingerprint)))||
      typeof recoverAcceptedCookie!=='boolean')throw refusal();
    const clocks={wall,monotonic,schedule,cancel};
    fence=createContinuationStopFence(chrome,selected.settings,clocks);
    native=createContinuationNativePause(chrome,initial,build,clocks);
  } catch {throw refusal();}
  const cookieKey=Object.freeze({url:selected.settings.origin+'/',name:'__Host-sdsctl-device-session'});
  let used=false,failed=false,rejectPending=null;
  const abort=new AbortController();
  const stop=()=>{
    failed=true;abort.abort();fence.invalidate();native.invalidate();rejectPending?.(refusal());
  };
  return Object.freeze({
    invalidate:stop,
    run:async(...args)=>{
      if(used||failed)throw refusal();
      used=true;
      const facts={browserStopSaved:false,nativePauseConfirmed:false,localWritesDrained:false,
        serverRevocation:'unconfirmed',cookieCleared:false};
      let pauseAcknowledgements=null,comparison=cookieFingerprint,coldSnapshot=null;
      let timer,poll=null,wake=null,started,last,lanesFenced=false,drainFailed=false,cleaned=false;
      const sample=()=>{
        const now=[wall(),monotonic()];if(!now.every(finite))throw refusal();return now;
      };
      const check=()=>{
        const now=sample();
        if(failed||chrome.runtime.id!==id||now.some((v,i)=>v<last[i]||v-started[i]>=45000))throw refusal();
        last=now;return Math.max(...now.map((v,i)=>v-started[i]));
      };
      const io=async operation=>{check();const value=await operation();check();return value;};
      const drain=()=>{
        check();
        if(drainFailed||!lanesFenced)throw refusal();
        try {
          const v=readWriteDrain();
          if(!strict(v,['fenced','pendingWrites','unconfirmedWrite','localWritesDrained'])||
            v.fenced!==true||!Number.isSafeInteger(v.pendingWrites)||v.pendingWrites<0||
            typeof v.unconfirmedWrite!=='boolean'||typeof v.localWritesDrained!=='boolean'||
            v.localWritesDrained!==(v.pendingWrites===0&&!v.unconfirmedWrite)||v.unconfirmedWrite)
            throw refusal();
          check();facts.localWritesDrained=v.localWritesDrained;return v.localWritesDrained;
        } catch {drainFailed=true;facts.localWritesDrained=false;throw refusal();}
      };
      const drained=()=>{if(!drain())throw refusal();};
      const waitDrain=async()=>{
        while(!drain())await io(()=>new Promise(resolve=>{
          wake=resolve;poll=schedule(()=>{poll=null;wake=null;resolve();},100);
        }));
      };
      const cookie=()=>io(()=>chrome.cookies.get(cookieKey));
      const equalSnapshot=(a,b)=>b!==null&&typeof b==='object'?
        strict(a,Reflect.ownKeys(b))&&Reflect.ownKeys(b).every(key=>equalSnapshot(a[key],b[key])):a===b;
      const coldRecord=async()=>{
        drained();
        const saved=structuredClone(await io(()=>chrome.storage.local.get(null)));
        const stopped={version:1,identity:selected.settings.identity,epoch:selected.settings.epoch,
          build:selected.settings.build,stopped:true};
        if(!strict(saved,['sdsctlDeviceRecovery',KEY])||!same(saved[KEY],stopped)||
          classifyContinuationStartup(selected.settings,saved.sdsctlDeviceRecovery,
            selected.observed).mode!=='verification_required')throw refusal();
        const alarms=await io(()=>chrome.alarms.getAll());
        if(!Array.isArray(alarms)||alarms.length!==0)throw refusal();
        if(coldSnapshot!==null&&!equalSnapshot(saved,coldSnapshot))throw refusal();
        drained();return saved;
      };
      const owned=async value=>{
        drained();
        if(coldSnapshot!==null)await coldRecord();
        const hash=await io(()=>fingerprintContinuationCookie(selected.settings.origin,value));
        if(hash!==comparison)throw refusal();
        if(coldSnapshot!==null)await coldRecord();
        drained();
        return value;
      };
      const browser=async()=>{
        await waitDrain();
        if(comparison===null) {
          // A sleeping MV3 worker loses its in-memory comparison. Reconstruct
          // only a STOP-only comparison for a fully accepted, native-bound
          // record, after this attempt's own durable STOP and checked native
          // pause acknowledge. Never use it for readiness, issuance or repair.
          // Paused/unknown issuance, a pre-existing STOP or changed state cannot
          // enter this path. The live cookie must still match the recorded hash.
          if(!recoverAcceptedCookie||selected.observed.mode!=='active')throw refusal();
          await io(()=>pauseAcknowledgements);
          if(!facts.browserStopSaved||!facts.nativePauseConfirmed)throw refusal();
          coldSnapshot=await coldRecord();
          comparison=coldSnapshot.sdsctlDeviceRecovery.cookieFingerprint;
          await coldRecord();
        }
        const before=await owned(await cookie());
        const life=before.expirationDate*1000-started[0]-check();
        if(!finite(life)||life<=0||life>3600000)throw refusal();
        await owned(await cookie());drained();
        const outcome=await io(()=>submitLogout({signal:abort.signal}));
        if(!['drained','pending'].includes(outcome))throw refusal();
        facts.serverRevocation=outcome;drained();
        const after=await cookie();drained();
        if(after!==null) {
          await owned(after);await owned(await cookie());drained();
          const removed=await io(()=>chrome.cookies.remove({...cookieKey,storeId:'0'}));
          if(!same(removed,{...cookieKey,storeId:'0'}))throw refusal();drained();
          if(await cookie()!==null)throw refusal();drained();
        }
        facts.cookieCleared=true;
      };
      const cleanup=()=>{
        if(cleaned)return;cleaned=true;
        let uncertain=false;
        try {cancel(timer);} catch {uncertain=true;}
        if(poll!==null)try {cancel(poll);} catch {uncertain=true;}
        poll=null;const resolve=wake;wake=null;resolve?.();
        if(uncertain){stop();throw refusal();}
      };
      try {
        // Fence synchronously before any await, storage write or native request.
        // A thenable is NOT a synchronous acknowledgement of invalidation.
        try {
          const result=invalidateLanes();lanesFenced=result===undefined;
          // Wrong asynchronous contracts stay unconfirmed; consume their
          // rejection without adopting it as a later fencing acknowledgement.
          if(result&&typeof result.then==='function')void Promise.resolve(result).catch(()=>{});
        } catch {lanesFenced=false;}
        if(args.length)throw refusal();
        started=sample();last=started;
        const interrupted=new Promise((_,reject)=>{rejectPending=reject;});
        void interrupted.catch(()=>{});
        timer=schedule(stop,45000);
        // Independent stop/native lanes are not queued behind installer writes,
        // the logout document, or one another. Each retains its own deadline.
        const localTasks=[
          io(()=>fence.save()).then(value=>{
            if(!same(value,{mode:'continuation_stopped',browserStopSaved:true,nativePauseConfirmed:false,
              serverRevocationConfirmed:false,sessionReady:false}))throw refusal();
            facts.browserStopSaved=true;
          }),
          io(()=>native.run()).then(value=>{
            if(!same(value,{mode:'native_paused',nativePauseConfirmed:true,
              serverRevocationConfirmed:false,sessionReady:false}))throw refusal();
            facts.nativePauseConfirmed=true;
          }),
        ];
        pauseAcknowledgements=Promise.all(localTasks);
        void pauseAcknowledgements.catch(()=>{});
        const tasks=[...localTasks,browser()].map(task=>task.catch(()=>{}));
        await Promise.race([Promise.all(tasks),interrupted]);
        cleanup();check();
        if(lanesFenced)try {drain();} catch { /* Preserve separate acknowledgements. */ }
      } catch {stop();}
      finally {
        rejectPending=null;
        try {cleanup();} catch {stop();}
      }
      const confirmed=!failed&&lanesFenced&&facts.browserStopSaved&&facts.nativePauseConfirmed&&
        facts.localWritesDrained&&facts.cookieCleared;
      return Object.freeze({mode:confirmed&&facts.serverRevocation==='drained'?'continuation_stop_complete':
        confirmed&&facts.serverRevocation==='pending'?'continuation_stop_pending':'continuation_stop_unconfirmed',
        ...facts,serverRevocationConfirmed:facts.serverRevocation!=='unconfirmed',sessionReady:false});
    },
  });
}
