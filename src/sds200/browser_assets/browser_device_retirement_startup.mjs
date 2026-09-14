// Internal recovery-only worker composition. Never use connectChromeRecovery:
// its initial tick can authenticate or advance native state before confirmation.
import {createBrowserRecovery,DEVICE_COOKIE,RECOVERY_ALARM} from "./browser_device_recovery.mjs";
import {connectRetirementWorker,createChromeRetirementPorts} from "./browser_device_retirement_ui.mjs";

const STATE_KEY="sdsctlDeviceRecovery";
const unavailable=async()=>{throw new Error("Recovery-only operation unavailable");};
const same=(a,b)=>a !== null && typeof a === "object" && !Array.isArray(a) &&
  Object.keys(a).sort().join(",") === Object.keys(b).sort().join(",") &&
  Object.keys(b).every(key=>a[key] === b[key]);

export function connectChromeRetirementRecovery(chrome, settings, acknowledge = false) {
  if(typeof acknowledge !== "boolean")throw new Error("Recovery setup invalid");
  // Validate/copy trusted settings before constructing the storage adapter.
  const config=Object.freeze({...settings});
  const nativePorts=createChromeRetirementPorts(chrome,
    {nativeHost:config.nativeHost,identity:config.identity});
  let confirmed=null;
  const retirement={confirm:async request=>{
    const proof=await nativePorts.confirm(request);
    confirmed={...proof};return proof;
  }};
  // Reuse the ONE canonical state/queue owner. Ordinary actions have no native,
  // cookie-install, alarm-scheduling or resume capabilities in this composition.
  const privateStorage=Promise.resolve().then(()=>
    chrome.storage.local.setAccessLevel({accessLevel:"TRUSTED_CONTEXTS"}));
  // A bad config may throw synchronously below; never leak an unhandled error.
  privateStorage.catch(()=>{});
  const load=async()=>{
    await privateStorage;
    return (await chrome.storage.local.get(STATE_KEY))[STATE_KEY];
  };
  const cookieKey={url:config.origin+"/",name:DEVICE_COOKIE};
  const controller=createBrowserRecovery({
    retirement,now:Date.now,load,
    save:async state=>{
      await privateStorage;
      const saved={...state};
      await chrome.storage.local.set({[STATE_KEY]:saved});
      // Only the exact read-back can produce a paused browser acknowledgement.
      // A lost write reply is still uncertain and is never automatically retried.
      if(!same(await load(),saved))throw new Error("Recovery state unconfirmed");
    },
    clearCookie:async()=>{
      await chrome.cookies.remove(cookieKey);
      if(await chrome.cookies.get(cookieKey))throw new Error("Cookie cleanup unconfirmed");
    },
    cancel:()=>chrome.alarms.clear(RECOVERY_ALARM),
    native:unavailable,schedule:unavailable,setCookie:unavailable,
  },config);
  // No startup tick, onStartup/onInstalled handler, content script, alarm
  // listener, first-run provisioning or normal control/startup/resume listener.
  // Do not export the full controller to another owner of the same state.
  const controls=Object.freeze({
    reviewPendingRetirement:controller.reviewPendingRetirement,
    retirePending:async review=>{
      const result=await controller.retirePending(review);
      if(!acknowledge || !same(result,
        {mode:"retired_paused",localPauseSaved:true,sessionReady:false}))return result;
      try {
        // Browser commit and exact read-back precede durable LOCAL acknowledgement.
        // No page message selects proof/paths. A new worker has no confirmed proof
        // and cannot synthesize an acknowledgement from generic paused state.
        if(!confirmed || !same(await load(),{version:1,identity:config.identity,
          paused:true,phase:"clean",nextAt:0}))throw new Error("Unconfirmed pause");
        const response=await chrome.runtime.sendNativeMessage(config.nativeHost,
          {version:1,action:"acknowledge-retirement",...confirmed});
        if(!same(response,{version:1,ok:true,mode:"retired_paused",acknowledged:true}))
          throw new Error("Unconfirmed acknowledgement");
        return result;
      } catch {return {mode:"retirement_refused"};}
    },
  });
  connectRetirementWorker(chrome,controls);
}
