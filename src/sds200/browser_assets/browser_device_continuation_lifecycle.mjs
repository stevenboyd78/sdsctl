// Trusted canonical composition. The candidate worker selects this owner only
// after fixed native role validation and whole-storage startup inspection.
// The gate must be the synchronous, still-pending createWorkerEventGate result;
// its native method is the raw Chrome method, not an already wrapped request.
import {validateContinuationContext} from './browser_device_continuation_context.mjs';
import {createContinuationNativePorts} from './browser_device_continuation_native.mjs';
import {connectContinuationOperationLanes} from './browser_device_continuation_consent.mjs';
import {createContinuationAcceptedStartup} from './browser_device_continuation_accepted.mjs';
import {createContinuationProbe} from './browser_device_continuation_probe.mjs';
import {createContinuationLogout} from './browser_device_logout.mjs';
import {createContinuationStopOwner} from './browser_device_continuation_stop.mjs';

const HOST='org.sdsctl.browser_device';
const refusal=()=>Error('Browser continuation lifecycle is unconfirmed; retain saved state.');
const unconfirmed=()=>Object.freeze({mode:'continuation_stop_unconfirmed',browserStopSaved:false,
  nativePauseConfirmed:false,localWritesDrained:false,serverRevocation:'unconfirmed',
  cookieCleared:false,serverRevocationConfirmed:false,sessionReady:false});

export function createContinuationLifecycle(gate,initial,build,
  {wall=Date.now,monotonic=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout}={}) {
  const chrome=gate.chrome,id=chrome.runtime.id,
    selected=validateContinuationContext(initial,build,id),{settings,observed}=selected;
  if([wall,monotonic,schedule,cancel].some(v=>typeof v!=='function'))throw refusal();
  // Keep no mutable caller alias. An active binding comes only from this fixed
  // native selection, or the native adapter's fully acknowledged issue receipt.
  const fixed=Object.freeze({version:1,ok:true,build,role:'continuation',extensionId:id,
    acknowledge:false,launch:null,config:Object.freeze({origin:settings.origin,
      identity:settings.identity,nativeHost:HOST}),continuation:Object.freeze({
      epoch:settings.epoch,mode:observed.mode,binding:observed.binding})});
  gate.check();
  const probeChrome=gate.prepareContinuationProbe(settings.origin),
    logoutChrome=gate.prepareContinuationLogout(settings.origin),
    base=observed.mode==='paused'?gate.prepareContinuationConsent():chrome;
  const runtime=Object.create(base.runtime),scoped=Object.create(base);
  Object.defineProperty(runtime,'sendNativeMessage',{value:(host,request)=>{
    if(host!==HOST||chrome.runtime.id!==id)throw refusal();
    return chrome.runtime.sendNativeMessage(HOST,{version:1,action:'worker-request',build,request});
  }});
  Object.defineProperty(scoped,'runtime',{value:runtime});
  const clocks={wall,monotonic,schedule,cancel};
  let lane,native=null,probe=null,logout=null,terminal=null,fenced=false,fenceUnconfirmed=false,
    acceptedStarted=false;
  const invalidateLanes=()=>{
    if(!fenced) {
      fenced=true;
      // Memory-only ports: never call the legacy operation worker's invalidate,
      // which would select a second durable STOP writer.
      for(const operation of [()=>lane?.invalidate(),()=>native?.invalidate(),()=>probe?.close()]) {
        try {
          const result=operation();
          if(result!==undefined) {
            fenceUnconfirmed=true;
            if(result&&typeof result.then==='function')void Promise.resolve(result).catch(()=>{});
          }
        } catch {fenceUnconfirmed=true;}
      }
    }
    if(fenceUnconfirmed)throw refusal();
  };
  const createProbe=({signal})=>{
    if(terminal||fenced||probe!==null)throw refusal();
    probe=createContinuationProbe(probeChrome,settings.origin,{...clocks,signal});return probe;
  };
  const selectStop=recoverAcceptedCookie=>{
    if(terminal)return terminal;
    // Select the promise BEFORE any callback can reenter. Every subsequent Stop
    // joins this exact attempt; no retry, state adoption or replacement token.
    let finish;terminal=new Promise(resolve=>{finish=resolve;});
    try {
      const owner=createContinuationStopOwner(scoped,native?.pauseContext()??fixed,build,{
        ...clocks,invalidateLanes,cookieFingerprint:lane.cookieFingerprint(),recoverAcceptedCookie,
        readWriteDrain:()=>{
          if(fenceUnconfirmed)throw refusal();return lane.writeDrain();
        },
        submitLogout:({signal})=>{
          if(logout!==null)throw refusal();
          logout=createContinuationLogout(logoutChrome,settings.origin,{...clocks,signal});
          return logout.run();
        },
      });
      // run() fences every lane synchronously before its first browser/native IO.
      void owner.run().then(finish,()=>finish(unconfirmed()));
    } catch {
      try {invalidateLanes();} catch { /* Retain an unconfirmed terminal result. */ }
      finish(unconfirmed());
    }
    return terminal;
  };
  // Only the validated manual logout receiver may reconstruct a cold-worker
  // comparison. Startup errors/cancellation keep the existing no-adoption path.
  const stop=(...args)=>args.length?Promise.reject(refusal()):selectStop(false);
  try {
    // One response channel, bound to the first exact Chrome-supplied requesting
    // document. It joins stop(), never grants the legacy content its own POST.
    // Chrome keeps respond attached to this request's document; the content
    // adapter additionally fences pagehide and its original location.
    let stopDocument=null;
    scoped.runtime.onMessage.addListener((message,sender,respond)=>{
      if(message?.action!=='logout-begin'||Reflect.ownKeys(message).length!==1||
        sender?.id!==id||sender.frameId!==0||sender.documentLifecycle!=='active'||
        typeof sender.documentId!=='string'||!/^[a-zA-Z0-9-]{1,128}$/.test(sender.documentId)||
        !Number.isSafeInteger(sender.tab?.id)||sender.tab.id<0||sender.tab.incognito!==false||
        sender.origin!==settings.origin||
        ![settings.origin+'/',settings.origin+'/device-display'].includes(sender.url))return false;
      const key=`${sender.tab.id}:${sender.documentId}:${sender.url}`;
      if(stopDocument!==null&&stopDocument!==key){respond({mode:'administrator_required'});return false;}
      stopDocument=key;
      void selectStop(observed.mode==='active'&&!acceptedStarted).then(value=>{
        try {respond(value);} catch {/* Closed document; no new Stop or POST. */}});
      return true;
    });
    if(observed.mode==='paused') {
      native=createContinuationNativePorts(scoped,fixed,build,clocks);
      lane=connectContinuationOperationLanes(scoped,fixed,build,{...clocks,createProbe,
        readCurrent:native.readCurrent,issueInitial:native.issueInitial,
        invalidateNative:native.invalidate,onStopRequested:stop});
    } else {
      lane=createContinuationAcceptedStartup(scoped,fixed,build,{...clocks,createProbe});
    }
  } catch {
    try {invalidateLanes();} catch { /* Failed construction never opens the gate. */ }
    gate.fail();throw refusal();
  }
  return Object.freeze({settings,mode:observed.mode,stop,isStopped:()=>terminal!==null||fenced,
    startAccepted:async(...args)=>{
      if(args.length||observed.mode!=='active'||acceptedStarted||terminal||fenced)throw refusal();
      acceptedStarted=true;
      try {
        const result=await lane.run();
        if(terminal||fenced)throw refusal();return result;
      } catch {void stop();throw refusal();}
    },
  });
}
