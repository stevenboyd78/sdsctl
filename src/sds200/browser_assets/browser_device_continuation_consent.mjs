// Experimental document-bound confirmation. NOT selected by the active worker
// or page. The sole completion sink is an in-process fixture boundary, not native
// authority, durable approval, session issuance or a reusable serialized grant.
import {createContinuationPausedReader} from './browser_device_continuation_worker.mjs';
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Object.keys(v).sort().join(',')===[...keys].sort().join(',');
const same=(a,b)=>b!==null&&typeof b==='object'?
  exact(a,Object.keys(b))&&Object.keys(b).every(k=>same(a[k],b[k])):a===b;
const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const refusal=()=>Error('Browser continuation confirmation is unconfirmed; retain saved state.');
const documentId=v=>typeof v==='string'&&/^[a-zA-Z0-9-]{1,128}$/.test(v);

// Unselected page adapter for isolated qualification. Its wording deliberately
// promises confirmation checking only, never sign-in. Ordinary resume.html
// still uses connectResumePage and leaves continuation confirmation disabled.
export function connectContinuationConfirmationPage({document,window,runtime}) {
  if(window!==window.top||window.location.href!==runtime.getURL('resume.html'))throw refusal();
  const review=document.getElementById('review'),form=document.getElementById('resume-form'),
    confirm=document.getElementById('confirm'),submit=document.getElementById('resume'),
    notice=document.getElementById('notice'),record=document.getElementById('reviewed');
  let alive=true,attempted=false,submitted=false,ticket=null;
  confirm.disabled=true;submit.disabled=true;
  const fail=()=>{
    ticket=null;confirm.disabled=true;submit.disabled=true;
    notice.textContent='Continuation confirmation could not be verified. Keep saved state; no sign-in is enabled.';
  };
  window.addEventListener('pagehide',()=>{
    alive=false;ticket=null;confirm.disabled=true;submit.disabled=true;
    try {void runtime.sendMessage({action:'continuation-cancel'}).catch(()=>{});} catch {/* Closing context. */}
  });
  review.addEventListener('click',event=>{
    if(!event.isTrusted||!alive||attempted)return;
    attempted=true;review.disabled=true;
    notice.textContent='Checking the current server permission for this document…';
    void runtime.sendMessage({action:'resume-review'}).then(value=>{
      if(!alive)return;
      if(!exact(value,['mode','ticket','nativeRevision','serverGeneration'])||
        value.mode!=='continuation_confirmation_reviewed'||!hex(value.ticket)||
        [value.nativeRevision,value.serverGeneration].some(n=>
          !Number.isSafeInteger(n)||n<=0||n>=Number.MAX_SAFE_INTEGER))throw refusal();
      ticket=value.ticket;confirm.checked=false;confirm.disabled=false;submit.disabled=false;
      record.textContent=`Native revision ${value.nativeRevision}; server generation ${value.serverGeneration}.`;
      notice.textContent='Confirm this reviewed state within one minute. This experimental check does not enable sign-in.';
    }).catch(()=>{if(alive)fail();});
  });
  form.addEventListener('submit',event=>{
    event.preventDefault();
    if(!event.isTrusted||!alive||submitted||confirm.checked!==true||!ticket)return;
    submitted=true;const selected=ticket;ticket=null;confirm.disabled=true;submit.disabled=true;
    void runtime.sendMessage({action:'resume-confirm',ticket:selected}).then(value=>{
      if(!alive)return;
      if(!exact(value,['mode','sessionReady'])||value.mode!=='continuation_confirmation_checked'||
        value.sessionReady!==false)throw refusal();
      notice.textContent='Document-bound confirmation was checked. Sign-in remains disabled; no session was issued.';
    }).catch(()=>{if(alive)fail();});
  });
}

export function connectContinuationConsentWorker(chrome,initial,build,
  {onConfirmed,wall=Date.now,monotonic=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout}) {
  if(typeof onConfirmed!=='function')throw refusal();
  const reader=createContinuationPausedReader(chrome,initial,build,{wall,monotonic,schedule,cancel});
  const id=chrome.runtime.id,origin=`chrome-extension://${id}`,url=chrome.runtime.getURL('resume.html');
  const settings=reader.settings,abort=new AbortController();
  let phase='new',selected=null,reviewed=null,ticket=null,started=null,last=null,timer=null,rejectPending=null;
  const stop=()=>{
    phase='failed';ticket=null;reviewed=null;reader.invalidate();abort.abort();cancel(timer);
    rejectPending?.(refusal());
  };
  const sample=()=>{
    const pair=[wall(),monotonic()];
    if(pair.some(v=>typeof v!=='number'||!Number.isFinite(v)||v<0||v>=Number.MAX_SAFE_INTEGER))throw refusal();
    return pair;
  };
  const check=()=>{
    const now=sample();
    if(phase==='failed'||abort.signal.aborted||
      now.some((v,i)=>(last&&v<last[i])||(started&&v-started[i]>=60000)))throw refusal();
    last=now;
  };
  const senderOK=sender=>sender?.id===id&&sender.origin===origin&&sender.url===url&&
    sender.frameId===0&&sender.documentLifecycle==='active'&&documentId(sender.documentId)&&
    Number.isSafeInteger(sender.tab?.id)&&sender.tab.id>=0&&sender.tab.incognito===false;
  const target=sender=>Object.freeze({tabId:sender.tab.id,documentId:sender.documentId});
  const contextId=async()=>{
    check();
    const rows=await chrome.runtime.getContexts({contextTypes:['TAB'],documentIds:[selected.documentId],
      tabIds:[selected.tabId],frameIds:[0],incognito:false});check();
    if(!Array.isArray(rows)||rows.length!==1)throw refusal();
    const row=rows[0];
    if(row.contextType!=='TAB'||row.documentId!==selected.documentId||row.tabId!==selected.tabId||
      row.frameId!==0||row.incognito!==false||row.documentOrigin!==origin||row.documentUrl!==url||
      !documentId(row.contextId))throw refusal();
    return row.contextId;
  };
  const currentDocument=async()=>{
    const before=await contextId();
    const tab=await chrome.tabs.get(selected.tabId);check();
    if(tab?.id!==selected.tabId||tab.incognito!==false||tab.status!=='complete'||
      tab.url!==url||tab.pendingUrl)throw refusal();
    // A tab ID/URL survives same-URL document replacement. Recheck the actual
    // extension document after the independent tab API, not just before it.
    if(await contextId()!==before)throw refusal();
  };
  const run=async work=>{
    try {
      const pending=new Promise((_,reject)=>{rejectPending=reject;});
      const result=await Promise.race([work(),pending]);check();return result;
    } catch {stop();throw refusal();}
    finally {rejectPending=null;}
  };
  const review=sender=>{
    if(phase!=='new')return Promise.reject(refusal());
    // Copy browser-supplied identity before the first await. Another document
    // cannot overwrite this lane, even while the native review is outstanding.
    selected=target(sender);phase='reviewing';
    return run(async()=>{
      check();started=last;timer=schedule(stop,60000);
      await currentDocument();
      const value=await reader.inspect(true);check();await currentDocument();
      reviewed=structuredClone(value);
      const nonce=new Uint8Array(32);crypto.getRandomValues(nonce);
      ticket=Array.from(nonce,n=>n.toString(16).padStart(2,'0')).join('');
      check();phase='reviewed';
      return {mode:'continuation_confirmation_reviewed',ticket,
        nativeRevision:reviewed.observed.binding.revision,
        serverGeneration:reviewed.observed.binding.generation};
    });
  };
  const confirm=(sender,value)=>{
    if(phase!=='reviewed'||!same(target(sender),selected)||!hex(value)||value!==ticket)
      return Promise.reject(refusal());
    // Consume before any asynchronous readback or fixture sink call. A lost
    // response, duplicate packet or failure can never invoke it a second time.
    phase='confirming';ticket=null;
    return run(async()=>{
      check();await currentDocument();
      const fresh=await reader.inspect(true);check();
      if(!same(fresh,reviewed))throw refusal();
      await currentDocument();check();
      const observed=Object.freeze({...fresh.observed,binding:Object.freeze({...fresh.observed.binding})});
      phase='consumed';
      const result=onConfirmed(Object.freeze({settings,reviewed:observed,document:selected,signal:abort.signal}));
      // This seam is deliberately synchronous and confirmation-only. A future
      // async mutation owner needs its own durable cancellation/issuance wiring;
      // a Promise or an "accepted" result cannot quietly enable that here.
      if(result!==undefined) {
        if(result&&typeof result.then==='function')void Promise.resolve(result).catch(()=>{});
        throw refusal();
      }
      check();cancel(timer);reviewed=null;
      return {mode:'continuation_confirmation_checked',sessionReady:false};
    });
  };
  const respondSafely=(respond,value)=>{try {respond(value);} catch {stop();}};
  chrome.tabs.onUpdated.addListener((tabId,change)=>{
    if(selected&&tabId===selected.tabId&&
      (change?.navigating===true||change?.status==='loading'||Object.hasOwn(change||{},'url')))stop();
  });
  chrome.runtime.onMessage.addListener((message,sender,respond)=>{
    // Sign-out only fences this in-memory confirmation. It is NOT a durable
    // sign-out acknowledgement, native pause or server-session revocation.
    if(sender?.id===id&&sender.frameId===0&&sender.documentLifecycle==='active'&&
      documentId(sender.documentId)&&Number.isSafeInteger(sender.tab?.id)&&sender.tab.id>=0&&
      sender.tab.incognito===false&&sender.origin===settings.origin&&
      [settings.origin+'/',settings.origin+'/device-display'].includes(sender.url)&&
      exact(message,['action'])&&message.action==='logout-begin') {stop();return false;}
    if(!senderOK(sender))return false;
    if(exact(message,['action'])&&message.action==='continuation-cancel') {
      if(selected&&same(target(sender),selected))stop();return false;
    }
    let operation;
    if(exact(message,['action'])&&message.action==='resume-review')operation=()=>review(sender);
    else if(exact(message,['action','ticket'])&&message.action==='resume-confirm'&&hex(message.ticket))
      operation=()=>confirm(sender,message.ticket);
    else return false;
    // Starting the operation synchronously captures identity/consumes the
    // ticket before another event. Only sanitized status goes back to the page.
    void operation().then(value=>respondSafely(respond,value),
      ()=>respondSafely(respond,{mode:'administrator_required'}));
    return true;
  });
  return Object.freeze({invalidate:stop});
}
