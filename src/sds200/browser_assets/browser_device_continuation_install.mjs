// Experimental one-shot browser I/O composition. NOT connected to worker/native
// dispatch. Only a future fixed-role owner with fresh document-bound consent may
// construct this adapter; supplied review/native/probe functions are not grants.
import {createInitialInstallation,pausedContinuationRecord} from './browser_device_continuation_state.mjs';
import {fingerprintContinuationCookie} from './browser_device_continuation_cookie.mjs';

const KEY='sdsctlDeviceRecovery', COOKIE='__Host-sdsctl-device-session', ALARM='sdsctl-device-recovery';
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Object.keys(v).sort().join(',')===[...keys].sort().join(',');
// Expected values are fixed, bounded records. Chrome may reorder object keys;
// serialization order is not a state change or an acceptance requirement.
const same=(actual,expected)=>expected!==null&&typeof expected==='object'?
  exact(actual,Object.keys(expected))&&Object.keys(expected).every(k=>same(actual[k],expected[k])):
  actual===expected;
const refusal=()=>Error('Browser continuation installation is unconfirmed; retain saved state.');

export function createContinuationInstallation(chrome,settings,reviewed,
  {readCurrent,issueInitial,createProbe,wall=Date.now,monotonic=()=>performance.now(),
    schedule=setTimeout,cancel=clearTimeout}) {
  // Validate/copy before any asynchronous work or browser access. The state core
  // also validates the reviewed positive generation and allowable revision.
  const config=Object.freeze({...settings}), review=structuredClone(reviewed);
  const paused=pausedContinuationRecord(config);
  createInitialInstallation(config,paused,review,{wall,monotonic}).invalidate();
  const offline={...review,binding:{...review.binding,generation:null}};
  let used=false,failed=false,attempt=null,probe=null,rejectPending=null;
  const abort=new AbortController();
  const stop=()=>{
    failed=true;attempt?.invalidate();abort.abort();
    // No cookie deletion, native pause, browser-state repair or issuance retry.
    // The future owner must separately serialize durable sign-out/cancellation.
    rejectPending?.(refusal());
  };
  return Object.freeze({
    invalidate:stop,
    run:async()=>{
      if(used||failed)throw refusal();
      used=true;let timer,started,last;
      const sample=()=>{
        const pair=[wall(),monotonic()];
        if(pair.some(v=>typeof v!=='number'||!Number.isFinite(v)||v<0||
          v>=Number.MAX_SAFE_INTEGER))throw refusal();
        return pair;
      };
      const check=()=>{
        const now=sample();
        if(failed||now.some((v,i)=>v<last[i]||v-started[i]>=45000))throw refusal();
        last=now;
      };
      // Invoke only AFTER checking the lane. A timeout does not cancel a Chrome
      // promise: late completion may commit, but may not invoke its next step.
      const io=async operation=>{check();const value=await operation();check();return value;};
      const read=async()=>structuredClone(await io(()=>chrome.storage.local.get(null)));
      const savedAs=async record=>{
        const values=await read();
        if(!same(values,{[KEY]:record}))throw refusal();
        return values[KEY];
      };
      const currentAs=async expected=>{
        const observed=structuredClone(await io(readCurrent));
        if(!same(observed,expected))throw refusal();
        return observed;
      };
      const cookie=()=>io(()=>chrome.cookies.get({url:config.origin+'/',name:COOKIE}));
      const noSession=async()=>{
        if(await cookie()!=null)throw refusal();
        if(await io(()=>chrome.alarms.get(ALARM))!=null)throw refusal();
      };
      const save=async(before,after)=>{
        await savedAs(before);
        await io(()=>chrome.storage.local.set({[KEY]:structuredClone(after)}));
        return savedAs(after); // A resolved set alone is NOT acceptance.
      };
      try {
        started=sample();last=started;
        const interrupted=new Promise((_,reject)=>{rejectPending=reject;
          timer=schedule(stop,45000);});
        const work=(async()=>{
          await io(()=>chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'}));
          const values=await read();
          if(!exact(values,[KEY]))throw refusal();
          const before=values[KEY];
          const legacy={version:1,identity:config.identity,paused:true,phase:'clean',nextAt:0};
          if(!same(before,legacy)&&!same(before,paused))throw refusal();
          await currentAs(offline);await noSession();
          await savedAs(before);await currentAs(offline);await noSession();
          attempt=createInitialInstallation(config,paused,review,{wall,monotonic});
          const nonce=new Uint8Array(32);crypto.getRandomValues(nonce);
          const intent=Array.from(nonce,n=>n.toString(16).padStart(2,'0')).join('');
          const pending=attempt.pendingRecord(intent);
          // Legacy clean pause is replaced directly by initial_pending, never by
          // an intermediate clean schema-3 record that could authorize a retry.
          const request=attempt.pendingSaved(await save(before,pending));
          await currentAs(offline);await noSession();await savedAs(pending);
          let issued=await io(()=>issueInitial(request));
          const active=structuredClone(await io(readCurrent));
          let details=attempt.sessionReturned(issued,active);issued=null;
          await savedAs(pending);await noSession();
          // Refuse an unexpected cookie rather than overwrite/adopt/delete it.
          await io(()=>chrome.cookies.set(details));details=null;
          const installed=structuredClone(await cookie());
          attempt.cookieInstalled(installed);
          const installedHash=await io(()=>fingerprintContinuationCookie(config.origin,installed));
          if(!same(await cookie(),installed))throw refusal();
          await currentAs(active);await savedAs(pending);
          check();probe=createProbe({signal:abort.signal});check();
          attempt.probeStarted(await io(()=>probe.open()));
          const proof=await io(()=>probe.verify());
          await savedAs(pending);
          const observed=await currentAs(active), confirmed=structuredClone(await cookie());
          if(!same(confirmed,installed))throw refusal();
          const hash=await io(()=>fingerprintContinuationCookie(config.origin,confirmed));
          if(hash!==installedHash||!same(await cookie(),installed))throw refusal();
          if(await io(()=>chrome.alarms.get(ALARM))!=null)throw refusal();
          const accepted=attempt.protectedPageVerified(proof,confirmed,observed,hash);
          await save(pending,accepted);
          const finalNative=await currentAs(active), finalCookie=structuredClone(await cookie());
          const finalHash=await io(()=>fingerprintContinuationCookie(config.origin,finalCookie));
          if(!same(await cookie(),finalCookie))throw refusal();
          if(await io(()=>chrome.alarms.get(ALARM))!=null)throw refusal();
          const finalSaved=await savedAs(accepted);
          check();return attempt.acceptedSaved(finalSaved,finalNative,finalCookie,finalHash);
        })();
        // One owner, no queue of new initial attempts. Failed/late work cannot
        // revive this object; an accepted restart still needs fresh verification.
        const result=await Promise.race([work,interrupted]);check();return result;
      } catch {stop();throw refusal();}
      finally {
        cancel(timer);rejectPending=null;
        try {probe?.close();} catch { /* Only the owned probe; never state repair. */ }
      }
    },
  });
}
