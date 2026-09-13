// Canonical one-use accepted-startup verifier. An accepted record is only a
// prerequisite: obtain fresh native/server and actual cookie/document evidence.
// No issuance, storage/cookie repair, renewal, native pause or sign-out here.
import {validateContinuationContext} from './browser_device_continuation_context.mjs';
import {classifyContinuationStartup} from './browser_device_continuation_state.mjs';
import {fingerprintContinuationCookie} from './browser_device_continuation_cookie.mjs';
import {createContinuationProbe} from './browser_device_continuation_probe.mjs';

const KEY='sdsctlDeviceRecovery',HOST='org.sdsctl.browser_device';
const COOKIE='__Host-sdsctl-device-session',ALARM='sdsctl-device-recovery';
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Reflect.ownKeys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const same=(actual,expected)=>expected!==null&&typeof expected==='object'?
  exact(actual,Object.keys(expected))&&Object.keys(expected).every(k=>same(actual[k],expected[k])):
  actual===expected;
const finite=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<Number.MAX_SAFE_INTEGER;
const refusal=()=>Error('Browser continuation startup is unconfirmed; retain saved state.');

export function createContinuationAcceptedStartup(chrome,initial,build,
  {wall=Date.now,monotonic=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout,
    createProbe=null}={}) {
  let selected,id;
  try {
    id=chrome.runtime.id;selected=validateContinuationContext(initial,build,id);
    if(selected.observed.mode!=='active'||typeof chrome.runtime.sendNativeMessage!=='function'||
      [wall,monotonic,schedule,cancel].some(v=>typeof v!=='function')||
      (createProbe!==null&&typeof createProbe!=='function'))throw refusal();
  } catch {throw refusal();}
  const {settings,observed}=selected;
  const probeFactory=createProbe??(options=>createContinuationProbe(chrome,settings.origin,options));
  let used=false,failed=false,rejectPending=null,probe=null,pendingWrites=0,
    unconfirmedWrite=false,retainedFingerprint=null;
  const abort=new AbortController();
  const stop=()=>{failed=true;abort.abort();rejectPending?.(refusal());};
  return Object.freeze({
    invalidate:stop,
    writeDrain:()=>Object.freeze({fenced:failed,pendingWrites,unconfirmedWrite,
      localWritesDrained:failed&&pendingWrites===0&&!unconfirmedWrite}),
    // Stored state alone is not ownership. Populate only after fresh native,
    // document and cookie verification through the final checked return.
    cookieFingerprint:()=>retainedFingerprint,
    run:async()=>{
      if(used||failed)throw refusal();
      used=true;let timer,started,last,cleaned=false;
      const cleanup=()=>{
        if(cleaned)return;cleaned=true;
        let unconfirmed=false;
        try {cancel(timer);} catch {unconfirmed=true;}
        try {probe?.close();} catch {unconfirmed=true;}
        if(unconfirmed)throw refusal();
      };
      const sample=()=>{
        const pair=[wall(),monotonic()];if(!pair.every(finite))throw refusal();return pair;
      };
      const check=()=>{
        const now=sample();
        if(failed||chrome.runtime.id!==id||now.some((v,i)=>v<last[i]||v-started[i]>=45000))throw refusal();
        last=now;return Math.max(...now.map((v,i)=>v-started[i]));
      };
      // Invoke only after checking. Timeout/invalidation cannot cancel Chrome or
      // native promises, but their late completion cannot select another action.
      const io=async operation=>{check();const result=await operation();check();return result;};
      const storage=async()=>{
        const values=await io(()=>chrome.storage.local.get(null));
        // Check WHOLE storage before projecting KEY: every stop marker (even
        // malformed), unknown key and missing record is terminal for this owner.
        if(!exact(values,[KEY]))throw refusal();return structuredClone(values);
      };
      const savedAs=async saved=>{if(!same(await storage(),saved))throw refusal();};
      const current=async()=>{
        const raw=await io(()=>chrome.runtime.sendNativeMessage(HOST,{version:1,action:'continuation-current'}));
        const fresh=validateContinuationContext(raw,build,id);
        if(!same(fresh,selected))throw refusal();check();
      };
      const verifyActive=async()=>{
        const value=await io(()=>chrome.runtime.sendNativeMessage(HOST,{version:1,action:'continuation-verify-active'}));
        if(!same(value,{version:1,ok:true,build,identity:settings.identity,epoch:settings.epoch,
          mode:'active',binding:observed.binding}))throw refusal();check();
      };
      const cookie=async()=>structuredClone(await io(()=>chrome.cookies.get({url:settings.origin+'/',name:COOKIE})));
      const noAlarm=async()=>{if(await io(()=>chrome.alarms.get(ALARM))!=null)throw refusal();};
      const life=value=>{
        const elapsed=check(),remaining=value.expirationDate*1000-started[0]-elapsed;
        if(!finite(value.expirationDate)||!finite(remaining)||remaining<=30000||remaining>3600000)throw refusal();
      };
      const cookieAs=async expected=>{
        const value=await cookie();if(!same(value,expected))throw refusal();life(value);return value;
      };
      try {
        started=sample();last=started;
        const interrupted=new Promise((_,reject)=>{rejectPending=reject;});
        void interrupted.catch(()=>{});
        timer=schedule(stop,45000);
        const work=(async()=>{
          await io(async()=>{
            pendingWrites++;
            try {
              const actual=chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'});
              if(actual===null||typeof actual!=='object'||typeof actual.then!=='function')throw refusal();
              await actual;
            } catch {unconfirmedWrite=true;throw refusal();}
            finally {pendingWrites--;}
          });
          const saved=await storage(),record=saved[KEY];
          const state=classifyContinuationStartup(settings,record,observed);
          if(state.mode!=='verification_required'||state.sessionReady!==false)throw refusal();
          await current();await noAlarm();
          const installed=await cookie();
          const fingerprint=await io(()=>fingerprintContinuationCookie(settings.origin,installed));
          if(fingerprint!==record.cookieFingerprint)throw refusal();life(installed);
          await cookieAs(installed);await savedAs(saved);
          await verifyActive();
          await current();await cookieAs(installed);await savedAs(saved);await noAlarm();
          check();probe=probeFactory({signal:abort.signal,wall,monotonic,schedule,cancel});check();
          const selection=structuredClone(await io(()=>probe.open()));
          if(!exact(selection,['tabId','documentId','ticket'])||
            !Number.isSafeInteger(selection.tabId)||selection.tabId<0||
            typeof selection.documentId!=='string'||!/^[a-zA-Z0-9-]{1,128}$/.test(selection.documentId)||
            typeof selection.ticket!=='string'||!/^[a-f0-9]{64}$/.test(selection.ticket))throw refusal();
          await savedAs(saved);await cookieAs(installed);
          const proof=structuredClone(await io(()=>probe.verify()));
          if(!exact(proof,['url','tabId','documentId','ticket','displayOnly','deviceEnrolled','remainingSeconds'])||
            proof.url!==settings.origin+'/device-display'||proof.tabId!==selection.tabId||
            proof.documentId!==selection.documentId||proof.ticket!==selection.ticket||
            proof.displayOnly!==true||proof.deviceEnrolled!==true||
            !finite(proof.remainingSeconds)||proof.remainingSeconds>3600)throw refusal();
          await savedAs(saved);await current();
          const confirmed=await cookieAs(installed);
          const finalHash=await io(()=>fingerprintContinuationCookie(settings.origin,confirmed));
          if(finalHash!==fingerprint)throw refusal();
          await cookieAs(installed);await noAlarm();await savedAs(saved);
          // Deduct through the final return, not just the probe's earlier reply.
          // This point-in-time verdict is not a cached lease or renewal grant.
          return {installed,remainingSeconds:proof.remainingSeconds,fingerprint};
        })();
        const result=await Promise.race([work,interrupted]);cleanup();
        const elapsed=check(),remaining=result.installed.expirationDate*1000-started[0]-elapsed;
        if(!finite(remaining)||remaining<=30000||remaining>3600000||
          result.remainingSeconds-elapsed/1000<=30)throw refusal();
        retainedFingerprint=result.fingerprint;
        return Object.freeze({mode:'accepted',sessionReady:true});
      } catch {stop();throw refusal();}
      finally {
        rejectPending=null;
        try {cleanup();} catch {stop();throw refusal();}
      }
    },
  });
}
