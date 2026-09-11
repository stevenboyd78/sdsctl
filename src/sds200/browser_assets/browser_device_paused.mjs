// Native-selected paused-only role. No normal controller, authentication,
// resume, initialization, logout, alarm scheduling or browser-state repair.
import {DEVICE_COOKIE,RECOVERY_ALARM} from './browser_device_recovery.mjs';

const KEY='sdsctlDeviceRecovery';
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Object.keys(v).sort().join(',')===[...keys].sort().join(',');
const savedPause=(values,identity)=>exact(values,[KEY])&&
  exact(values[KEY],['version','identity','paused','phase','nextAt'])&&
  values[KEY].version===1&&values[KEY].identity===identity&&values[KEY].paused===true&&
  values[KEY].phase==='clean'&&values[KEY].nextAt===0;
const statusOK=value=>exact(value,['version','ok','mode','revision','retry_after','renew_after'])&&
  value.version===1&&value.ok===true&&value.mode==='paused'&&
  Number.isSafeInteger(value.revision)&&value.revision>0&&
  value.retry_after===0&&value.renew_after===0;

export function connectPausedBrowserWorker(chrome,config,schedule=setTimeout,cancel=clearTimeout) {
  // Restrict access without changing saved values. Failure stays read-only.
  const access=Promise.resolve().then(()=>
    chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'}));
  access.catch(()=>{});
  let checking=null;
  const inspect=()=>{
    if(checking)return checking;
    let timer;
    const timeout=new Promise((_,reject)=>{timer=schedule(()=>reject(Error('unavailable')),12000);});
    const read=(async()=>{
      await access;
      if(!savedPause(await chrome.storage.local.get(null),config.identity))throw Error('state');
      const value=await chrome.runtime.sendNativeMessage(config.nativeHost,{version:1,action:'status'});
      if(!statusOK(value))throw Error('native');
      const cookie=await chrome.cookies.get({url:config.origin+'/',name:DEVICE_COOKIE});
      const alarm=await chrome.alarms.get(RECOVERY_ALARM);
      if(cookie||alarm||!savedPause(await chrome.storage.local.get(null),config.identity))throw Error('state');
      return 'administrator_required';
    })();
    checking=Promise.race([read,timeout]).catch(()=>'setup_error').finally(()=>{
      cancel(timer);checking=null;
    });
    return checking;
  };
  chrome.runtime.onMessage.addListener((message,sender,respond)=>{
    if(sender?.id!==chrome.runtime.id||sender.frameId!==0||
       sender.documentLifecycle!=='active'||typeof sender.documentId!=='string'||
       !/^[a-zA-Z0-9-]{1,128}$/.test(sender.documentId)||
       !Number.isSafeInteger(sender.tab?.id)||sender.tab.id<0||sender.tab.incognito!==false||
       !exact(message,['action']))return false;
    const startup=sender.url===chrome.runtime.getURL('startup.html')&&message.action==='startup-status';
    const review=sender.url===chrome.runtime.getURL('resume.html')&&message.action==='resume-review';
    if(!startup&&!review)return false;
    void inspect().then(mode=>{
      try {respond(startup?{mode,sessionReady:false}:{mode});}
      catch { /* Closed page: do not replay a reply or change any state. */ }
    });
    return true;
  });
}
