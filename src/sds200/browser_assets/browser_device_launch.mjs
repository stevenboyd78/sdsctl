// Fixed canonical readiness and one session-scoped page opening. Never consent/login.
const exact=(v,keys)=>v!==null&&typeof v==="object"&&!Array.isArray(v)&&
  Object.keys(v).sort().join(",") === [...keys].sort().join(",");
const hex=v=>typeof v==="string"&&/^[a-f0-9]{64}$/.test(v);
const config=value=>{
  if(!exact(value,["identity","intent","binding","nativeHost"])||
    ![value.identity,value.intent,value.binding].every(hex)||
    typeof value.nativeHost!=="string"||value.nativeHost.length>253||
    !/^[a-z0-9_]+(?:\.[a-z0-9_]+)*$/.test(value.nativeHost))throw Error("Recovery launch invalid");
  return Object.freeze({...value});
};
const eligible=(chrome,sender)=>sender?.id===chrome.runtime.id&&sender.frameId===0&&
  sender.url===chrome.runtime.getURL("recovery.html")&&sender.documentLifecycle==="active"&&
  typeof sender.documentId==="string"&&/^[a-zA-Z0-9-]{1,128}$/.test(sender.documentId)&&
  Number.isSafeInteger(sender.tab?.id)&&sender.tab.id>=0&&sender.tab.incognito===false;

export function connectRecoveryLaunchNavigation(chrome,settings) {
  const c=config(settings),key="sdsctlRecoveryLaunchPage";let attempt=null;
  const open=()=>attempt??=Promise.resolve().then(async()=>{
    const storage=chrome.storage.session;
    await storage.setAccessLevel({accessLevel:"TRUSTED_CONTEXTS"});
    const previous=(await storage.get(key))[key];
    if(previous!==undefined) {
      if(!exact(previous,["binding"])||previous.binding!==c.binding)throw Error("Page launch refused");
      return; // Worker restart is not another page opening or inherited consent.
    }
    const marker={binding:c.binding};
    // Claim before opening. An uncertain write/open is not replayed on worker restart.
    await storage.set({[key]:marker});
    const saved=(await storage.get(key))[key];
    if(!exact(saved,["binding"])||saved.binding!==c.binding)throw Error("Page launch refused");
    await chrome.tabs.create({url:chrome.runtime.getURL("recovery.html"),active:true});
  }).catch(()=>{}); // The independent native readiness deadline still fails closed.
  chrome.runtime.onStartup.addListener(open);
  chrome.runtime.onInstalled.addListener(open);
  void open(); // Installed/replaced worker owns navigation after its assets load.
}

export function connectRecoveryLaunchWorker(chrome,settings) {
  const c=config(settings);let attempt=null;
  chrome.runtime.onMessage.addListener((message,sender,reply)=>{
    if(!eligible(chrome,sender)||!exact(message,["action","binding"])||
      message.action!=="recovery-launch"||message.binding!==c.binding)return false;
    // At most one native call per worker; a lost result is never rewritten.
    attempt??=Promise.resolve().then(()=>chrome.runtime.sendNativeMessage(c.nativeHost,
      {version:1,action:"recovery-launch-ready",identity:c.identity,intent:c.intent,binding:c.binding}))
      .then(value=>{
        if(!exact(value,["version","ok","binding"])||value.version!==1||value.ok!==true||
          value.binding!==c.binding)throw Error("Recovery launch unconfirmed");
        return {mode:"recovery_ready",binding:c.binding};
      }).catch(()=>({mode:"recovery_launch_refused"}));
    void attempt.then(reply);return true;
  });
}

export function connectRecoveryLaunchPage({document,window,runtime},settings) {
  const c=config(settings);
  if(window!==window.top||window.location.href!==runtime.getURL("recovery.html"))
    throw Error("Recovery launch invalid");
  const review=document.getElementById("review"),notice=document.getElementById("notice");
  review.disabled=true;
  notice.textContent="Verifying the supervised recovery extension… No recovery approval has been submitted.";
  void Promise.resolve().then(()=>runtime.sendMessage({action:"recovery-launch",binding:c.binding}))
    .then(value=>{
      if(!exact(value,["mode","binding"])||value.mode!=="recovery_ready"||value.binding!==c.binding)
        throw Error("Recovery launch unconfirmed");
      review.disabled=false;
      notice.textContent="Recovery extension verified. Review and confirm below to keep automatic sign-in paused. No login was attempted.";
    }).catch(()=>{
      notice.textContent="Recovery extension could not be verified. Keep the maintenance guard and all existing files. Do not retry or reset saved state.";
    });
}
