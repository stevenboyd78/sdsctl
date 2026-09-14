// Experimental document-bound browser I/O. Not imported by the active worker.
// Owns only a temporary probe tab and its messages: never credentials, native
// actions, cookies or browser acceptance storage. A verdict is point-in-time
// evidence, not a permission lease or a replacement for native/server checks.
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Object.keys(v).sort().join(',')===[...keys].sort().join(',');
const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const documentId=v=>typeof v==='string'&&/^[a-zA-Z0-9-]{1,128}$/.test(v);
const tabId=v=>Number.isSafeInteger(v)&&v>=0;
const life=v=>typeof v==='number'&&Number.isFinite(v)&&v>30&&v<=3600;
const refusal=()=>Error('Browser display verification is unconfirmed.');
function originOf(value) {
  if(typeof value!=='string'||!/^[\x21-\x7e]{1,2048}$/.test(value))throw refusal();
  const url=new URL(value);
  if(url.protocol!=='https:'||url.origin!==value||url.username||url.password||
    url.pathname!=='/'||url.search||url.hash)throw refusal();
  return value;
}

// Use in an ISOLATED, top-frame content script at the fixed selected origin.
// The worker obtains documentId from Chrome's MessageSender, never from this
// message's body, page DOM, caller-supplied proof or an outbound Port.sender.
export function connectContinuationProbeContent({window,runtime,fetcher}, selectedOrigin) {
  const origin=originOf(selectedOrigin), route=origin+'/device-display';
  let ticket=null, used=false, alive=true;
  const locationOK=()=>alive&&window===window.top&&window.location.href===route;
  window.addEventListener('pagehide',()=>{alive=false;});
  runtime.onMessage.addListener((message,sender,respond)=>{
    if(sender?.id!==runtime.id||!locationOK()||!exact(message,['action','ticket'])||
      !hex(message.ticket))return false;
    if(message.action==='continuation-probe-select'&&ticket===null) {
      ticket=message.ticket;
      void runtime.sendMessage({action:'continuation-probe-selected',ticket}).then(reply=>{
        respond({selected:locationOK()&&exact(reply,['ok'])&&reply.ok===true});
      }).catch(()=>respond({selected:false}));
      return true;
    }
    if(message.ticket!==ticket)return false;
    if(message.action==='continuation-probe-confirm'&&used) {
      respond({current:locationOK()});return false;
    }
    if(message.action!=='continuation-probe-verify'||used)return false;
    used=true;
    void (async()=>{
      let reader;
      try {
        const response=await fetcher(origin+'/auth/session',{method:'GET',mode:'same-origin',
          credentials:'same-origin',cache:'no-store',redirect:'error',
          headers:{Accept:'application/json'},signal:AbortSignal.timeout(5000)});
        if(response.status!==200||response.redirected||response.url!==origin+'/auth/session'||
          response.headers.get('content-type')!=='application/json')throw refusal();
        reader=response.body.getReader();
        const decoder=new TextDecoder('utf-8',{fatal:true});let body='',count=0;
        while(true) {
          const chunk=await reader.read();if(chunk.done)break;
          count+=chunk.value.byteLength;if(count>1024)throw refusal();
          body+=decoder.decode(chunk.value,{stream:true});
        }
        body+=decoder.decode();
        const value=JSON.parse(body);
        if(!locationOK()||(body.match(/"(?:\\.|[^"\\])*"\s*:/g)||[]).length!==3||
          !exact(value,['display_only','device_enrolled','remaining_seconds'])||
          value.display_only!==true||value.device_enrolled!==true||!life(value.remaining_seconds))
          throw refusal();
        const reply=await runtime.sendMessage({action:'continuation-probe-result',ticket,
          displayOnly:true,deviceEnrolled:true,remainingSeconds:value.remaining_seconds});
        respond({verified:locationOK()&&exact(reply,['ok'])&&reply.ok===true});
      } catch {respond({verified:false});}
      finally {if(reader)void reader.cancel().catch(()=>{});}
    })();
    return true;
  });
}

export function createContinuationProbe(chrome, selectedOrigin,
  {signal,wall=Date.now,monotonic=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout}={}) {
  const origin=originOf(selectedOrigin), url=origin+'/device-display', id=chrome.runtime.id;
  if(typeof id!=='string'||!/^[a-p]{32}$/.test(id))throw refusal();
  const nonce=new Uint8Array(32);crypto.getRandomValues(nonce);
  const ticket=Array.from(nonce,n=>n.toString(16).padStart(2,'0')).join('');
  let phase='new',owned=null,selected=null,proof=null,last=null,started=null,finished=false;
  const pending=new Map();
  const check=()=>{
    const now=[wall(),monotonic()];
    if(finished||signal?.aborted||now.some((v,i)=>typeof v!=='number'||!Number.isFinite(v)||
      v<0||v>=Number.MAX_SAFE_INTEGER||(last&&v<last[i])||(started&&v-started[i]>=15000)))
      throw refusal();
    if(!started)started=now;last=now;
  };
  const bounded=async promise=>{
    check();let timer;
    try {
      const timeout=new Promise((_,reject)=>{
        timer=schedule(()=>reject(refusal()),Math.max(0,15000-Math.max(...last.map((v,i)=>v-started[i]))));
        pending.set(timer,reject);
      });
      const result=await Promise.race([promise,timeout]);check();return result;
    } finally {cancel(timer);pending.delete(timer);}
  };
  const remove=target=>{
    try {void chrome.tabs.remove(target).catch(()=>{});} catch { /* Closed browser context. */ }
  };
  const stop=()=>{
    if(finished)return;
    finished=true;phase='closed';proof=null;
    for(const [timer,reject] of pending){cancel(timer);reject(refusal());}pending.clear();
    try {chrome.runtime.onMessage.removeListener(receive);} catch { /* Context closed. */ }
    try {chrome.tabs.onUpdated.removeListener(updated);} catch { /* Context closed. */ }
    signal?.removeEventListener('abort',stop);
    if(owned!==null){const target=owned;owned=null;remove(target);}
  };
  const senderOK=sender=>sender?.id===id&&sender.frameId===0&&sender.documentLifecycle==='active'&&
    sender.url===url&&sender.origin===origin&&documentId(sender.documentId)&&
    sender.tab?.id===owned&&owned!==null&&sender.tab.incognito===false;
  const receive=(message,sender,respond)=>{
    if(phase==='new'||finished)return false;
    try {
      check();
      if(!senderOK(sender)||message?.ticket!==ticket)return false;
      if(phase==='selecting'&&selected===null&&exact(message,['action','ticket'])&&
        message.action==='continuation-probe-selected') {
        selected=Object.freeze({tabId:owned,documentId:sender.documentId,ticket});
        respond({ok:true});return false;
      }
      if(phase==='verifying'&&proof===null&&sender.documentId===selected?.documentId&&
        exact(message,['action','ticket','displayOnly','deviceEnrolled','remainingSeconds'])&&
        message.action==='continuation-probe-result'&&message.displayOnly===true&&
        message.deviceEnrolled===true&&life(message.remainingSeconds)) {
        proof=Object.freeze({url,...selected,displayOnly:true,deviceEnrolled:true,
          remainingSeconds:message.remainingSeconds});respond({ok:true});return false;
      }
    } catch {stop();}
    return false;
  };
  const updated=(target,change)=>{
    if(selected&&target===owned&&(change.status==='loading'||change.url!==undefined))stop();
  };
  const page=async()=>{
    const value=await bounded(chrome.tabs.get(owned));
    if(value.id!==owned||value.incognito!==false||value.url!==url||value.pendingUrl||
      value.status!=='complete')throw refusal();
  };
  // Construct after the fixed native role is validated; never bypass the
  // synchronous worker gate. No listener invokes a native or authority action.
  try {
    chrome.runtime.onMessage.addListener(receive);
    chrome.tabs.onUpdated.addListener(updated);
    signal?.addEventListener('abort',stop,{once:true});
  } catch {stop();throw refusal();}
  return Object.freeze({
    open:async()=>{
      try {
        if(phase!=='new')throw refusal();check();phase='creating';
        const created=await bounded(chrome.tabs.create({url,active:false}).then(value=>{
          // Promise races cannot cancel Chrome work. A late create still owns
          // exactly its returned tab, which must be closed without adoption.
          if(tabId(value?.id)) {
            if(finished)remove(value.id);else owned=value.id;
          }
          return value;
        }));
        if(!tabId(created?.id))throw refusal();owned=created.id;
        if(created.incognito!==false)throw refusal();
        // Wait only for this initial page load; never retry a verification fetch
        // or adopt a replacement document after selection.
        for(let n=0;n<30;n++) {
          const value=await bounded(chrome.tabs.get(owned));
          if(value.id!==owned||value.incognito!==false||
            (value.url&&value.url!==url)||(value.pendingUrl&&value.pendingUrl!==url))throw refusal();
          if(value.status==='complete'&&value.url===url&&!value.pendingUrl)break;
          if(n===29)throw refusal();
          await bounded(new Promise(resolve=>schedule(resolve,100)));
        }
        phase='selecting';
        const reply=await bounded(chrome.tabs.sendMessage(owned,
          {action:'continuation-probe-select',ticket},{frameId:0}));
        if(!exact(reply,['selected'])||reply.selected!==true||selected===null)throw refusal();
        await page();phase='selected';return selected;
      } catch {stop();throw refusal();}
    },
    verify:async()=>{
      try {
        if(phase!=='selected')throw refusal();check();phase='verifying';await page();
        const reply=await bounded(chrome.tabs.sendMessage(owned,
          {action:'continuation-probe-verify',ticket},{documentId:selected.documentId}));
        if(!exact(reply,['verified'])||reply.verified!==true||proof===null)throw refusal();
        await page();
        const current=await bounded(chrome.tabs.sendMessage(owned,
          {action:'continuation-probe-confirm',ticket},{documentId:selected.documentId}));
        if(!exact(current,['current'])||current.current!==true)throw refusal();
        await page();check();
        // Subtract the entire probe duration conservatively, not just time
        // since the response. The server's remaining lifetime is not a lease.
        const remainingSeconds=proof.remainingSeconds-Math.max(...last.map((v,i)=>v-started[i]))/1000;
        if(!life(remainingSeconds))throw refusal();
        phase='verified';return Object.freeze({...proof,remainingSeconds});
      } catch {stop();throw refusal();}
    },
    close:stop,
  });
}
