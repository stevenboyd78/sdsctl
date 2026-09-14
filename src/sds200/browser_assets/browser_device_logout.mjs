// Opt-in experiment: no automatic registration or production dashboard edits.
// Install in an ISOLATED, top-frame, exact-origin content-script world only.
const exact = (value, keys) => value && typeof value === "object" && !Array.isArray(value) &&
  Object.keys(value).sort().join(",") === [...keys].sort().join(",");
const OUTCOMES = new Set(["drained", "pending", "unconfirmed"]);

export function connectLogoutWorker(chrome, controller, origin, clock = Date.now) {
  if (new URL(origin).origin !== origin || !origin.startsWith("https://")) throw new Error("setup");
  let pending = null;
  const eligible = sender => sender.id === chrome.runtime.id &&
    ["/", "/device-display"].some(p => sender.url === origin + p) &&
    sender.origin === origin && sender.frameId === 0 && sender.documentLifecycle === "active" &&
    typeof sender.documentId === "string" && /^[a-zA-Z0-9-]{1,128}$/.test(sender.documentId) &&
    Number.isSafeInteger(sender.tab?.id) && sender.tab.id >= 0 && sender.tab.incognito === false;
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (!eligible(sender)) return false;
    const key = `${sender.tab.id}:${sender.documentId}`;
    if (exact(message, ["action"]) && message.action === "logout-begin") {
      if (pending) {
        respond({mode: "busy"}); // Other documents/repeated begins get no second submission ticket.
        return false;
      }
      pending = {key, ticket: crypto.randomUUID(), deadline: clock() + 60000, finished: null};
      const operation = pending;
      void controller.beginLogout().then(result => {
        if (result.mode !== "logout_pending" || result.localPauseSaved !== true || clock() >= operation.deadline) {
          respond({mode: "paused", submit: false});
          return;
        }
        respond({mode: "logout_pending", submit: true, ticket: operation.ticket});
      }).catch(() => respond({mode: "setup_error", submit: false}));
      return true;
    }
    if (exact(message, ["action", "ticket", "outcome"]) && message.action === "logout-finish" &&
        OUTCOMES.has(message.outcome) && pending?.key === key && message.ticket === pending.ticket &&
        clock() < pending.deadline) {
      // Cache the first completion promise: replay cannot upgrade an uncertain result.
      if (!pending.finished) pending.finished = controller.finishLogout(message.outcome);
      void pending.finished.then(respond).catch(() => respond({mode: "setup_error"}));
      return true;
    }
    return false;
  });
  return Object.freeze({retireAfterResume: () => {
    // Trusted worker wiring only, never a dashboard message. A fresh verified
    // resume starts a new logout cycle; old document tickets cannot finish it.
    const state=controller.readiness();
    if (state.mode !== "active" || state.sessionReady !== true) return false;
    pending=null;
    return true;
  }});
}

export async function submitDeviceLogout(fetcher, origin, {signal} = {}) {
  // Fetch must be the isolated content world's browser fetch. It supplies the
  // real same-origin Origin/Fetch Metadata and HttpOnly cookie, not extension fetch.
  try {
    const deadline = AbortSignal.timeout(10000);
    const boundedSignal = signal === undefined ? deadline : AbortSignal.any([signal, deadline]);
    // Cancellation before selection must not even invoke an injected fetcher.
    // Browser fetch also uses this signal during HTTP/body processing.
    if (boundedSignal.aborted) return "unconfirmed";
    const response = await fetcher(origin + "/auth/logout", {method: "POST",
      mode: "same-origin", credentials: "same-origin", cache: "no-store", redirect: "error",
      headers: {Accept: "application/json"}, signal: boundedSignal});
    if (![200, 202].includes(response.status) || response.redirected ||
        response.url !== origin + "/auth/logout" ||
        response.headers.get("content-type") !== "application/json") return "unconfirmed";
    const reader = response.body.getReader();
    let text = "", total = 0;
    const decoder = new TextDecoder("utf-8", {fatal: true});
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        total += chunk.value.byteLength;
        if (total > 1024) return "unconfirmed";
        text += decoder.decode(chunk.value, {stream: true});
      }
      text += decoder.decode();
    } finally { await reader.cancel(); }
    const result = JSON.parse(text);
    // Flat response only; count all JSON key tokens to reject duplicate/escaped duplicates.
    if ((text.match(/"(?:\\.|[^"\\])*"\s*:/g) || []).length !== 4 ||
        !exact(result, ["version", "device_logout", "paused", "drained"]) ||
        result.version !== 1 || result.device_logout !== true || result.paused !== true ||
        typeof result.drained !== "boolean" || (response.status === 200) !== result.drained) {
      return "unconfirmed";
    }
    return boundedSignal.aborted ? "unconfirmed" : result.drained ? "drained" : "pending";
  } catch { return "unconfirmed"; } // Never expose body/exception data to page or logs.
}

// Inert document receiver registered by the generated isolated entrypoint. This
// import-free module is also used to generate a classic ISOLATED content script.
// Registration and selection never submit HTTP or prove physical consent. The
// worker must obtain tab/document identity from Chrome's MessageSender, not a
// body field. No ordinary worker currently selects this receiver's channel.
export function connectContinuationLogoutContent(
  {window,runtime,fetcher,wall=Date.now,monotonic=()=>performance.now(),
    schedule=setTimeout,cancel=clearTimeout}, selectedOrigin) {
  const denied=()=>Error('Browser sign-out document is unconfirmed.');
  const strict=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
    Reflect.ownKeys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
  let origin,id;
  try {
    if(typeof selectedOrigin!=='string'||!/^[\x21-\x7e]{1,2048}$/.test(selectedOrigin))throw denied();
    const url=new URL(selectedOrigin);id=runtime.id;
    if(url.protocol!=='https:'||url.origin!==selectedOrigin||url.username||url.password||
      url.pathname!=='/'||url.search||url.hash||typeof id!=='string'||!/^[a-p]{32}$/.test(id)||
      [fetcher,wall,monotonic,schedule,cancel].some(value=>typeof value!=='function'))throw denied();
    origin=selectedOrigin;
    if(window!==window.top||window.location.href!==origin+'/device-display')throw denied();
  } catch {throw denied();}
  let phase='new',ticket=null,timer=null,started=null,last=null,rejectPending=null,interrupted=null;
  const abort=new AbortController();
  const stop=()=>{
    phase='failed';abort.abort();rejectPending?.(denied());
    try {cancel(timer);} catch { /* The operation remains terminal. */ }
    timer=null;
  };
  const check=()=>{
    const now=[wall(),monotonic()];
    if(phase==='failed'||runtime.id!==id||window!==window.top||
      window.location.href!==origin+'/device-display'||now.some((v,i)=>
        typeof v!=='number'||!Number.isFinite(v)||v<0||v>=Number.MAX_SAFE_INTEGER||
        (last&&v<last[i])||(started&&v-started[i]>=20000)))throw denied();
    last=now;return now;
  };
  const io=async operation=>{
    check();const value=await Promise.race([operation(),interrupted]);check();return value;
  };
  const reply=(respond,value)=>{try {respond(value);} catch {stop();}};
  const receive=(message,sender,respond)=>{
    if(sender?.id!==id||!strict(message,['action','ticket'])||typeof message.ticket!=='string'||
      !/^[a-f0-9]{64}$/.test(message.ticket))return false;
    try {check();} catch {stop();return false;}
    if(message.action==='continuation-logout-select'&&phase==='new') {
      phase='selecting';ticket=message.ticket;started=last;
      interrupted=new Promise((_,reject)=>{rejectPending=reject;});void interrupted.catch(()=>{});
      try {timer=schedule(stop,20000);} catch {stop();reply(respond,{selected:false});return false;}
      void (async()=>{
        try {
          const ack=await io(()=>runtime.sendMessage({action:'continuation-logout-selected',ticket}));
          if(!strict(ack,['ok'])||ack.ok!==true)throw denied();
          // UI-only hint in this same document, after the worker accepts Chrome's
          // document identity and before any POST. It carries no ticket, result
          // or authority and its return value is never an acknowledgement. The
          // main-world dashboard must not navigate on a revoked background read
          // while this isolated fetch awaits the server's bounded drain result.
          check();window.dispatchEvent(new Event('sdsctl-device-signout-intent'));
          check();phase='selected';reply(respond,{selected:true});
        } catch {stop();reply(respond,{selected:false});}
      })();
      return true;
    }
    if(ticket===null||message.ticket!==ticket)return false;
    if(message.action==='continuation-logout-cancel') {
      stop();reply(respond,{cancelled:true});return false; // Not a server cancellation acknowledgement.
    }
    if(message.action==='continuation-logout-confirm'&&phase==='submitted') {
      phase='confirmed';
      try {cancel(timer);timer=null;check();reply(respond,{current:true});}
      catch {stop();reply(respond,{current:false});}
      return false;
    }
    if(message.action!=='continuation-logout-submit'||phase!=='selected')return false;
    phase='submitting'; // Consume before fetch, response parsing or any await.
    void (async()=>{
      try {
        const outcome=await io(()=>submitDeviceLogout(fetcher,origin,{signal:abort.signal}));
        if(!['drained','pending'].includes(outcome))throw denied();
        const ack=await io(()=>runtime.sendMessage({action:'continuation-logout-result',ticket,outcome}));
        if(!strict(ack,['ok'])||ack.ok!==true)throw denied();
        check();phase='submitted';reply(respond,{submitted:true});
      } catch {stop();reply(respond,{submitted:false});}
    })();
    return true;
  };
  const close=()=>{
    stop();
    try {runtime.onMessage.removeListener(receive);} catch { /* Closed context. */ }
    try {window.removeEventListener('pagehide',stop);} catch { /* Closed document. */ }
  };
  try {window.addEventListener('pagehide',stop);runtime.onMessage.addListener(receive);}
  catch {close();throw denied();}
  return Object.freeze({close});
}

// Unselected worker-owned transport. Construct only behind the synchronous
// event gate's prepared logout facade after validating the fixed native role.
// It owns one temporary document; messages do not grant cookie/native authority.
export function createContinuationLogout(chrome,selectedOrigin,
  {signal,wall=Date.now,monotonic=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout}={}) {
  const denied=()=>Error('Browser sign-out channel is unconfirmed.');
  const strict=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
    Reflect.ownKeys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
  const documentId=v=>typeof v==='string'&&/^[a-zA-Z0-9-]{1,128}$/.test(v);
  const tabId=v=>Number.isSafeInteger(v)&&v>=0;
  const finite=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<Number.MAX_SAFE_INTEGER;
  let origin,id;
  try {
    if(typeof selectedOrigin!=='string'||!/^[\x21-\x7e]{1,2048}$/.test(selectedOrigin))throw denied();
    const parsed=new URL(selectedOrigin);id=chrome.runtime.id;
    if(parsed.protocol!=='https:'||parsed.origin!==selectedOrigin||parsed.username||parsed.password||
      parsed.pathname!=='/'||parsed.search||parsed.hash||typeof id!=='string'||!/^[a-p]{32}$/.test(id)||
      [wall,monotonic,schedule,cancel].some(v=>typeof v!=='function')||
      (signal!==undefined&&(typeof signal?.aborted!=='boolean'||signal.aborted||
        typeof signal.addEventListener!=='function'||typeof signal.removeEventListener!=='function')))
      throw denied();
    origin=selectedOrigin;
  } catch {throw denied();}
  const url=origin+'/device-display',nonce=new Uint8Array(32);crypto.getRandomValues(nonce);
  const ticket=Array.from(nonce,n=>n.toString(16).padStart(2,'0')).join('');
  let phase='new',used=false,failed=false,retired=false,owned=null,selected=null,outcome=null;
  let timer=null,poll=null,wake=null,rejectPending=null,started=null,last=null;
  const sample=()=>{const now=[wall(),monotonic()];if(!now.every(finite))throw denied();return now;};
  const check=()=>{
    const now=sample();
    if(failed||signal?.aborted||chrome.runtime.id!==id||
      now.some((v,i)=>(last&&v<last[i])||(started&&v-started[i]>=20000)))throw denied();
    last=now;
  };
  const remove=target=>{
    try {void chrome.tabs.remove(target).catch(()=>{});} catch { /* Only this returned owned tab. */ }
  };
  const cleanup=()=>{
    if(retired)return;retired=true;let uncertain=false;
    try {cancel(timer);} catch {uncertain=true;}
    if(poll!==null)try {cancel(poll);} catch {uncertain=true;}
    poll=null;const resolve=wake;wake=null;resolve?.();
    try {chrome.runtime.onMessage.removeListener(receive);} catch {uncertain=true;}
    try {chrome.tabs.onUpdated.removeListener(updated);} catch {uncertain=true;}
    try {signal?.removeEventListener('abort',stop);} catch {uncertain=true;}
    if(owned!==null){const target=owned;owned=null;remove(target);}
    if(uncertain)throw denied();
  };
  const stop=()=>{
    failed=true;phase='closed';rejectPending?.(denied());
    try {cleanup();} catch { /* Fixed terminal refusal, never retry cleanup/POST. */ }
  };
  const senderOK=sender=>sender?.id===id&&sender.frameId===0&&sender.documentLifecycle==='active'&&
    sender.origin===origin&&sender.url===url&&documentId(sender.documentId)&&
    owned!==null&&sender.tab?.id===owned&&sender.tab.incognito===false;
  const receive=(message,sender,respond)=>{
    if(retired||phase==='new')return false;
    try {
      check();if(!senderOK(sender)||message?.ticket!==ticket)return false;
      if(phase==='selecting'&&selected===null&&strict(message,['action','ticket'])&&
        message.action==='continuation-logout-selected') {
        selected=Object.freeze({tabId:owned,documentId:sender.documentId});respond({ok:true});return false;
      }
      if(phase==='submitting'&&outcome===null&&sender.documentId===selected?.documentId&&
        strict(message,['action','ticket','outcome'])&&message.action==='continuation-logout-result'&&
        ['drained','pending'].includes(message.outcome)) {
        outcome=message.outcome;respond({ok:true});return false;
      }
    } catch {stop();}
    return false;
  };
  const updated=(target,change)=>{
    if(selected&&target===owned&&(change?.status==='loading'||Object.hasOwn(change||{},'url')))stop();
  };
  try {
    chrome.runtime.onMessage.addListener(receive);chrome.tabs.onUpdated.addListener(updated);
    signal?.addEventListener('abort',stop,{once:true});
  } catch {stop();throw denied();}
  return Object.freeze({close:stop,run:async(...args)=>{
    if(used||failed||retired)throw denied();used=true;
    let interrupted;
    const io=async operation=>{check();const value=await Promise.race([operation(),interrupted]);check();return value;};
    const page=async()=>{
      const value=await io(()=>chrome.tabs.get(owned));
      if(value?.id!==owned||value.incognito!==false||value.url!==url||value.pendingUrl||
        value.status!=='complete')throw denied();
    };
    const send=(action,selection)=>io(()=>chrome.tabs.sendMessage(owned,{action,ticket},selection));
    try {
      if(args.length)throw denied();started=sample();last=started;
      interrupted=new Promise((_,reject)=>{rejectPending=reject;});void interrupted.catch(()=>{});
      timer=schedule(stop,20000);phase='creating';
      const created=await io(()=>chrome.tabs.create({url,active:false}).then(value=>{
        // A rejected race cannot cancel tabs.create. Close only its eventual
        // returned tab, without adopting it or running a selection afterward.
        if(tabId(value?.id)){if(failed||retired)remove(value.id);else owned=value.id;}
        return value;
      }));
      if(!tabId(created?.id)||created.incognito!==false)throw denied();
      while(true) {
        const value=await io(()=>chrome.tabs.get(owned));
        if(value?.id!==owned||value.incognito!==false||(value.url&&value.url!==url)||
          (value.pendingUrl&&value.pendingUrl!==url))throw denied();
        if(value.status==='complete'&&value.url===url&&!value.pendingUrl)break;
        await io(()=>new Promise(resolve=>{wake=resolve;poll=schedule(()=>{
          poll=null;wake=null;resolve();},100);}));
      }
      phase='selecting';
      const selection=await send('continuation-logout-select',{frameId:0});
      if(!strict(selection,['selected'])||selection.selected!==true||selected===null)throw denied();
      await page();phase='submitting';
      const submission=await send('continuation-logout-submit',{documentId:selected.documentId});
      if(!strict(submission,['submitted'])||submission.submitted!==true||outcome===null)throw denied();
      await page();phase='confirming';
      const confirmed=await send('continuation-logout-confirm',{documentId:selected.documentId});
      if(!strict(confirmed,['current'])||confirmed.current!==true)throw denied();
      await page();cleanup();check();phase='complete';return outcome;
    } catch {stop();throw denied();}
    finally {rejectPending=null;try {cleanup();} catch {stop();throw denied();}}
  }});
}

function continuationStopNotice(value) {
  if(!exact(value,['mode','browserStopSaved','nativePauseConfirmed','localWritesDrained',
    'serverRevocation','cookieCleared','serverRevocationConfirmed','sessionReady'])||
    Reflect.ownKeys(value).length!==8||value.sessionReady!==false||
    !['continuation_stop_complete','continuation_stop_pending','continuation_stop_unconfirmed'].includes(value.mode)||
    ['browserStopSaved','nativePauseConfirmed','localWritesDrained','cookieCleared','serverRevocationConfirmed']
      .some(key=>typeof value[key]!=='boolean')||!OUTCOMES.has(value.serverRevocation)||
    value.serverRevocationConfirmed!==(value.serverRevocation!=='unconfirmed'))return null;
  const local=value.browserStopSaved&&value.nativePauseConfirmed&&value.localWritesDrained&&value.cookieCleared;
  if(value.mode==='continuation_stop_complete')return local&&value.serverRevocation==='drained'?
    'Sign-out complete. Browser stop saved; native pause confirmed; local writes drained; server sessions closed; local session cookie cleared. Automatic sign-in remains paused.':null;
  if(value.mode==='continuation_stop_pending')return local&&value.serverRevocation==='pending'?
    'Sign-out pending. Browser stop saved; native pause confirmed; local writes drained; local session cookie cleared. The server confirmed pause, but sessions are still draining. Do not repeat sign-out; retain this profile for administrator review.':null;
  return 'Sign-out could not be fully confirmed. '+
    `Browser stop: ${value.browserStopSaved?'saved':'unconfirmed'}. `+
    `Native pause: ${value.nativePauseConfirmed?'confirmed':'unconfirmed'}. `+
    `Local writes: ${value.localWritesDrained?'drained':'unconfirmed'}. `+
    `Server sessions: ${value.serverRevocation==='drained'?'closed':value.serverRevocation==='pending'?'draining':'unconfirmed'}. `+
    `Local session cookie: ${value.cookieCleared?'cleared':'unconfirmed'}. `+
    'Keep this profile for administrator review; do not retry sign-out or remove saved state.';
}

export function connectLogoutContent({document, window, runtime, fetcher,
  schedule=setTimeout,cancel=clearTimeout}, origin) {
  if (window !== window.top || !["/", "/device-display"].some(p => window.location.href === origin + p) ||
      new URL(origin).origin !== origin || !origin.startsWith("https://")) throw new Error("setup");
  const selectedURL=window.location.href;
  let busy=false,alive=true;
  window.addEventListener('pagehide',()=>{alive=false;});
  const current=()=>alive&&window===window.top&&window.location.href===selectedURL;
  document.addEventListener("submit", event => {
    const form = event.target;
    if (form?.tagName !== "FORM" || form.method.toLowerCase() !== "post" ||
        form.action !== origin + "/auth/logout") return;
    // Never let a programmatic submit event bypass persisted intent on this managed page.
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!event.isTrusted || busy || !current()) return;
    busy = true;
    const notice = document.createElement("p");
    notice.setAttribute("role", "status");
    notice.textContent = "Saving sign-out intent…";
    form.append(notice);
    void (async () => {
      let message = "Sign-out could not be confirmed. Automatic sign-in may require administrator attention.";
      let timer;
      try {
        const prepared=await Promise.race([runtime.sendMessage({action:'logout-begin'}),
          new Promise((_,reject)=>{timer=schedule(()=>reject(Error('sign-out')),60000);})]);
        cancel(timer);timer=null;
        if(!current())return;
        const continuation=continuationStopNotice(prepared);
        if(continuation!==null){notice.textContent=continuation;return;}
        if (prepared?.mode !== "logout_pending" || prepared.submit !== true ||
            typeof prepared.ticket !== "string" ||
            !/^[a-f0-9-]{36}$/.test(prepared.ticket)) throw new Error("prepare");
        notice.textContent = "Automatic sign-in paused. Contacting the server…";
        const outcome = await submitDeviceLogout(fetcher, origin);
        const result = await runtime.sendMessage({action: "logout-finish", ticket: prepared.ticket, outcome});
        if(!current())return;
        if (result?.mode === "paused" && result.localPauseSaved === true) {
          message = result.serverRevocation === "drained" && result.cookieCleared === true ?
            "Signed out. Server shutdown confirmed; automatic sign-in remains paused." :
            "Automatic sign-in is paused. Server shutdown or local cleanup is unconfirmed; ask an administrator to check before resuming.";
        }
      } catch { /* Fixed text only; never insert a server response as HTML. */ }
      finally {try {cancel(timer);} catch {/* No retry or second POST. */}}
      if(current())notice.textContent = message;
      // Do not navigate/reload or clear unrelated cookies. Stale dashboard rendering
      // and managed recovery UI remain a separate integration gate.
    })();
  }, true);
}
