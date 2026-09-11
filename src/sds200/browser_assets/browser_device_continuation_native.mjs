// Unselected browser/native ports. Construct only behind the installed worker's
// fixed build-bound scoped Chrome facade and document-consent owner. Request
// fields are comparisons, not authority, and this module never proves a click.
import {validateContinuationContext} from './browser_device_continuation_context.mjs';

const HOST='org.sdsctl.browser_device',BUDGET=12000;
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Reflect.ownKeys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const integer=v=>Number.isSafeInteger(v)&&v>0&&v<Number.MAX_SAFE_INTEGER;
const finite=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<Number.MAX_SAFE_INTEGER;
const same=(a,b)=>exact(a,Object.keys(b))&&Object.keys(b).every(k=>
  b[k]!==null&&typeof b[k]==='object'?same(a[k],b[k]):a[k]===b[k]);
const refusal=()=>Error('Browser continuation native response is unconfirmed; retain saved state.');

export function createContinuationNativePorts(chrome,initial,build,
  {wall=Date.now,monotonic=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout}={}) {
  let selected,id;
  try {
    id=chrome.runtime.id;
    selected=validateContinuationContext(initial,build,id);
    if(selected.observed.mode!=='paused'||
      typeof chrome.runtime.sendNativeMessage!=='function'||
      [wall,monotonic,schedule,cancel].some(v=>typeof v!=='function'))throw refusal();
  } catch {throw refusal();}
  const {settings}=selected;
  let expected=selected.observed,used=false,busy=false,failed=false,last=null,rejectPending=null;
  const stop=()=>{failed=true;rejectPending?.(refusal());};
  const sample=()=>{
    const pair=[wall(),monotonic()];
    if(!pair.every(finite)||(last&&pair.some((v,i)=>v<last[i])))throw refusal();
    last=pair;return pair;
  };
  // Each native call has this outer 12s bound; native retains its own independent
  // 10s process supervisor. The owning installation/review keeps its original
  // 45s/60s whole-operation deadline. None is extended by a new or late response.
  const run=async(request,parse,finish=value=>value)=>{
    if(failed||busy)throw refusal();
    busy=true;let timer;
    try {
      const started=sample();
      const check=()=>{
        const now=sample();
        if(failed||chrome.runtime.id!==id||now.some((v,i)=>v-started[i]>=BUDGET))throw refusal();
        return Math.max(...now.map((v,i)=>v-started[i]));
      };
      const interrupted=new Promise((_,reject)=>{rejectPending=reject;});
      void interrupted.catch(()=>{}); // Also handled if scheduling fails before work starts.
      timer=schedule(stop,BUDGET);
      const work=(async()=>{
        check();
        // The supplied scoped facade already adds worker-request/build. Never
        // double-wrap or accept a caller-selected native host, role or build.
        const raw=await chrome.runtime.sendNativeMessage(HOST,request);
        check();return raw;
      })();
      let raw=await Promise.race([work,interrupted]);check();
      const value=parse(raw);raw=null;return finish(value,check());
    } catch {stop();throw refusal();}
    finally {
      rejectPending=null;busy=false;
      try {cancel(timer);} catch {stop();throw refusal();}
    }
  };
  return Object.freeze({
    settings,invalidate:stop,
    readCurrent:()=>run({version:1,action:'continuation-current'},value=>{
      const current=validateContinuationContext(value,build,id);
      if(!same(current.settings,settings)||!same(current.observed,expected))throw refusal();
      return current.observed;
    }),
    issueInitial:value=>{
      if(used||failed)return Promise.reject(refusal());
      // Consume before validation/await: malformed inputs, lost acknowledgements
      // and native errors cannot authorize a second attempt on this instance.
      used=true;
      try {
        if(busy||!exact(value,['epoch','intent','binding'])||
          !exact(value.binding,['fingerprint','revision','generation']))throw refusal();
        const request=structuredClone(value),binding=request.binding;
        if(!exact(request,['epoch','intent','binding'])||
          !exact(binding,['fingerprint','revision','generation'])||
          request.epoch!==settings.epoch||!hex(request.intent)||!hex(binding.fingerprint)||
          !integer(binding.revision)||!integer(binding.revision+2)||!integer(binding.generation)||
          binding.fingerprint!==expected.binding.fingerprint||
          binding.revision!==expected.binding.revision)throw refusal();
        Object.freeze(binding);Object.freeze(request);
        return run(Object.freeze({version:1,action:'continuation-initial-session',...request}),
          result=>{
            // Validate the COMPLETE response before projecting the installer's
            // narrower binding/session shape; never strip unrecognized fields.
            if(!exact(result,['version','ok','build','identity','epoch','mode','binding','session'])||
              result.version!==1||result.ok!==true||result.build!==build||
              result.identity!==settings.identity||result.epoch!==settings.epoch||result.mode!=='active'||
              !exact(result.binding,['fingerprint','revision','generation'])||
              !hex(result.binding.fingerprint)||result.binding.fingerprint===binding.fingerprint||
              result.binding.revision!==binding.revision+2||result.binding.generation!==binding.generation||
              !exact(result.session,['token','expires_in'])||typeof result.session.token!=='string'||
              !/^sdsctl-browser-session-v1\.[a-f0-9]{64}$/.test(result.session.token)||
              !finite(result.session.expires_in)||result.session.expires_in>3600)throw refusal();
            const after=Object.freeze({...result.binding});
            expected=Object.freeze({identity:settings.identity,epoch:settings.epoch,mode:'active',binding:after});
            return Object.freeze({binding:after,session:Object.freeze({...result.session})});
          },(result,elapsed)=>{
            // Deduct through the final checked return boundary, AFTER response
            // validation and the promise race. Never return the earlier lifetime
            // merely because an outer owner will independently check again.
            const remaining=result.session.expires_in-Math.ceil(elapsed)/1000;
            if(remaining<=30)throw refusal();
            return Object.freeze({binding:result.binding,
              session:Object.freeze({token:result.session.token,expires_in:remaining})});
          });
      } catch {stop();return Promise.reject(refusal());}
    },
  });
}
