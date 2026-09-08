// Internal confirmation-only recovery controls. Not installed by bundle generation.
// Native maintenance has already committed; this page only resolves browser intent
// to clean-but-paused. It cannot select archives, mutate native state or sign in.
const exact = (v, keys) => v !== null && typeof v === "object" && !Array.isArray(v) &&
  Object.keys(v).sort().join(",") === [...keys].sort().join(",");
const hex = v => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const stopped = v => ["paused","credential_rejected","tls_error","setup_error","protocol_error"].includes(v);
const revision = v => Number.isSafeInteger(v) && v > 0 && v < Number.MAX_SAFE_INTEGER;
const reviewed = v => exact(v,["mode","nativeRevision","nativeMode"]) &&
  v.mode === "retirement_reviewed" && revision(v.nativeRevision) && stopped(v.nativeMode);
const eligible = (chrome,sender) => sender?.id === chrome.runtime.id &&
  sender.url === chrome.runtime.getURL("recovery.html") && sender.frameId === 0 &&
  sender.documentLifecycle === "active" && typeof sender.documentId === "string" &&
  /^[a-zA-Z0-9-]{1,128}$/.test(sender.documentId) && Number.isSafeInteger(sender.tab?.id) &&
  sender.tab.id >= 0 && sender.tab.incognito === false;
const refused = () => ({mode:"retirement_refused"});

export function connectRetirementWorker(chrome, controller, clock = Date.now) {
  let pending = null;
  const current = operation => Number.isFinite(clock()) && clock() >= operation.started &&
    clock() < operation.started+60000;
  chrome.runtime.onMessage.addListener((message,sender,respond)=>{
    if (!eligible(chrome,sender)) return false;
    if (exact(message,["action"]) && message.action === "retirement-review") {
      if (pending) {respond(refused());return false;}
      const operation = pending = {key:`${sender.tab.id}:${sender.documentId}`,
        ticket:crypto.randomUUID(),started:clock(),used:false,review:null};
      // Deferring also captures a synchronous adapter failure without releasing
      // the one-use attempt. No caller-supplied reviews or operation IDs.
      void Promise.resolve().then(()=>controller.reviewPendingRetirement()).then(value=>{
        if (!reviewed(value) || !current(operation)) throw new Error("review");
        operation.review = {nativeRevision:value.nativeRevision,nativeMode:value.nativeMode};
        respond({mode:"retirement_reviewed",ticket:operation.ticket,...operation.review});
      }).catch(()=>respond(refused()));
      return true;
    }
    if (exact(message,["action","ticket"]) && message.action === "retirement-confirm" &&
        pending?.key === `${sender.tab.id}:${sender.documentId}` && message.ticket === pending.ticket &&
        pending.review && !pending.used && current(pending)) {
      pending.used = true;
      const review = {...pending.review};
      void Promise.resolve().then(()=>controller.retirePending(review)).then(value=>{
        respond(exact(value,["mode","localPauseSaved","sessionReady"]) &&
          value.mode === "retired_paused" && value.localPauseSaved === true && value.sessionReady === false
          ? {mode:"retired_paused"} : refused());
      }).catch(()=>respond(refused()));
      return true;
    }
    return false;
  });
}

export function connectRetirementPage({document,window,runtime}) {
  if (window !== window.top || window.location.href !== runtime.getURL("recovery.html")) throw new Error("setup");
  const review=document.getElementById("review"), form=document.getElementById("recovery-form");
  const confirm=document.getElementById("confirm"), submit=document.getElementById("resolve");
  const notice=document.getElementById("notice"), record=document.getElementById("reviewed");
  let attempted=false, submitted=false, ticket=null;
  confirm.checked=false;confirm.disabled=true;submit.disabled=true;
  review.addEventListener("click",event=>{
    if (!event.isTrusted || attempted) return;
    attempted=true;review.disabled=true;
    notice.textContent="Checking the administrator-selected maintenance record…";
    void Promise.resolve().then(()=>runtime.sendMessage({action:"retirement-review"})).then(value=>{
      if (!exact(value,["mode","ticket","nativeRevision","nativeMode"]) ||
          !reviewed({mode:value.mode,nativeRevision:value.nativeRevision,nativeMode:value.nativeMode}) ||
          typeof value.ticket !== "string" || !/^[a-f0-9-]{36}$/.test(value.ticket)) throw new Error("review");
      ticket=value.ticket;
      record.textContent=`Native revision ${value.nativeRevision}; state ${value.nativeMode}.`;
      confirm.checked=false;confirm.disabled=false;submit.disabled=false;
      notice.textContent="Maintenance verified. Confirm within one minute to resolve the interrupted browser record. Automatic sign-in will remain paused; native errors are not repaired.";
    }).catch(()=>{notice.textContent="Maintenance could not be verified. No browser recovery record was changed. Retain existing files and ask an administrator to review them.";});
  });
  form.addEventListener("submit",event=>{
    event.preventDefault();
    if (!event.isTrusted || !confirm.checked || !ticket || submitted) return;
    submitted=true;confirm.disabled=true;submit.disabled=true;
    notice.textContent="Rechecking maintenance and saving the paused browser state…";
    void Promise.resolve().then(()=>runtime.sendMessage({action:"retirement-confirm",ticket})).then(value=>{
      notice.textContent=exact(value,["mode"]) && value.mode === "retired_paused"
        ? "Interrupted recovery resolved. Automatic sign-in remains paused. No login was attempted. Resuming requires a separate fresh review."
        : "Resolution could not be confirmed. Keep automatic sign-in paused and retain all evidence. Do not repeat the approval or reset saved state.";
    }).catch(()=>{notice.textContent="Resolution acknowledgement was lost. Keep automatic sign-in paused. Do not repeat the approval or reset saved state; ask an administrator to review the evidence.";});
  });
}

export function createChromeRetirementPorts(chrome, settings) {
  // Trusted construction only: separate preselected native host, never page data.
  if (!exact(settings,["nativeHost","identity"]) || !hex(settings.identity) ||
      typeof settings.nativeHost !== "string" || settings.nativeHost.length > 253 ||
      !/^[a-z0-9_]+(?:\.[a-z0-9_]+)*$/.test(settings.nativeHost)) throw new Error("setup");
  const {nativeHost,identity}=settings;
  return Object.freeze({confirm:async request=>{
    if (!exact(request,["identity","intent"]) || request.identity !== identity || !hex(request.intent)) throw new Error("confirmation");
    const intent=request.intent;
    const value=await chrome.runtime.sendNativeMessage(nativeHost,
      {version:1,action:"confirm-retirement",identity,intent});
    if (!exact(value,["version","ok","evidence"]) || value.version !== 1 || value.ok !== true ||
        !exact(value.evidence,["identity","intent","retirement","mode","revision"])) throw new Error("confirmation");
    const proof=value.evidence;
    if (proof.identity !== identity || proof.intent !== intent || !hex(proof.retirement) ||
        !stopped(proof.mode) || !revision(proof.revision)) throw new Error("confirmation");
    return {identity,intent,retirement:proof.retirement,mode:proof.mode,revision:proof.revision};
  }});
}
