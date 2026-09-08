// Experimental trusted extension resume UI/bridge. No page-supplied destinations,
// credentials or reviews. Native messages remain private to the worker.
const exact = (v, keys) => v !== null && typeof v === "object" && !Array.isArray(v) &&
  Object.keys(v).sort().join(",") === [...keys].sort().join(",");
const eligiblePage = (chrome, sender) => sender.id === chrome.runtime.id &&
  sender.url === chrome.runtime.getURL("resume.html") && sender.frameId === 0 &&
  sender.documentLifecycle === "active" && typeof sender.documentId === "string" &&
  /^[a-zA-Z0-9-]{1,128}$/.test(sender.documentId) && Number.isSafeInteger(sender.tab?.id) &&
  sender.tab.id >= 0 && sender.tab.incognito === false;

export function connectResumeWorker(chrome, controller, clock = Date.now, afterResume = () => true) {
  let pending = null;
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (!eligiblePage(chrome,sender)) return false;
    if (exact(message,["action"]) && message.action === "resume-review") {
      // One document-bound attempt per worker, including refused/lost reviews.
      if (pending) {respond({mode:"resume_refused"});return false;}
      pending = {key:`${sender.tab.id}:${sender.documentId}`,ticket:crypto.randomUUID(),
        deadline:clock()+60000,used:false,review:null};
      const operation = pending;
      void controller.reviewResume().then(result=>{
        if (result.mode !== "reviewed" || clock() >= operation.deadline ||
            operation.deadline > clock()+60000) {respond({mode:"resume_refused"});return;}
        operation.review = {nativeRevision:result.nativeRevision,serverGeneration:result.serverGeneration};
        respond({mode:"reviewed",ticket:operation.ticket,...operation.review});
      }).catch(()=>respond({mode:"resume_refused"}));
      return true;
    }
    if (exact(message,["action","ticket"]) && message.action === "resume-confirm" &&
        pending?.key === `${sender.tab.id}:${sender.documentId}` &&
        message.ticket === pending.ticket && pending.review && !pending.used &&
        clock() < pending.deadline && pending.deadline <= clock()+60000) {
      pending.used = true;
      void controller.resume(pending.review).then(result=>respond({mode:result.mode === "active" &&
        afterResume() === true ? "resumed" : "resume_refused"})).catch(()=>respond({mode:"resume_refused"}));
      return true;
    }
    return false;
  });
}

export function connectResumePage({document,window,runtime}) {
  if (window !== window.top || window.location.href !== runtime.getURL("resume.html")) throw new Error("setup");
  const review = document.getElementById("review"), form = document.getElementById("resume-form");
  const confirm = document.getElementById("confirm"), submit = document.getElementById("resume");
  const notice = document.getElementById("notice"), record = document.getElementById("reviewed");
  let attempted = false, ticket = null, submitted = false;
  review.addEventListener("click",event=>{
    if (!event.isTrusted || attempted) return;
    attempted = true; review.disabled = true;
    notice.textContent = "Verifying this display with the configured server…";
    void runtime.sendMessage({action:"resume-review"}).then(result=>{
      if (!exact(result,["mode","ticket","nativeRevision","serverGeneration"]) || result.mode !== "reviewed" ||
          typeof result.ticket !== "string" || !/^[a-f0-9-]{36}$/.test(result.ticket) ||
          ![result.nativeRevision,result.serverGeneration].every(n=>Number.isSafeInteger(n)&&n>0)) {
        throw new Error("review");
      }
      ticket = result.ticket;
      record.textContent = `Native revision ${result.nativeRevision}; server generation ${result.serverGeneration}.`;
      confirm.disabled = false; submit.disabled = false;
      notice.textContent = "Server permission verified. Confirm below within one minute to resume automatic sign-in on this display.";
    }).catch(()=>{notice.textContent = "Review could not be confirmed. The server must already allow this device. Keep existing state for administrator review; no resume was attempted.";});
  });
  form.addEventListener("submit",event=>{
    event.preventDefault();
    if (!event.isTrusted || !confirm.checked || !ticket || submitted) return;
    submitted = true; confirm.disabled = true; submit.disabled = true;
    notice.textContent = "Saving consent and verifying a fresh display session…";
    void runtime.sendMessage({action:"resume-confirm",ticket}).then(result=>{
      notice.textContent = exact(result,["mode"]) && result.mode === "resumed"
        ? "Automatic sign-in resumed. A fresh display-only session was verified. You may return to the startup page."
        : "Resume could not be confirmed. Do not repeat the approval or delete saved state. Ask an administrator to review this display.";
    }).catch(()=>{notice.textContent = "Resume acknowledgement was lost. Do not repeat the approval or reset saved state. Ask an administrator to review this display.";});
  });
}

// This function is bundled into an ISOLATED top-frame content script. A dashboard
// script cannot manufacture its response or read the HttpOnly cookie it checks.
export function connectResumeContent({window,runtime,fetcher}, origin) {
  const locationOK = () => window === window.top && window.location.href === origin+"/device-display";
  runtime.onMessage.addListener((message,sender,respond)=>{
    if (sender.id !== runtime.id || !locationOK() || !exact(message,["action","ticket"]) ||
        message.action !== "resume-probe" || typeof message.ticket !== "string" ||
        !/^[a-f0-9-]{36}$/.test(message.ticket)) return false;
    void (async()=>{
      try {
        const response = await fetcher(origin+"/auth/session",{method:"GET",mode:"same-origin",
          credentials:"same-origin",cache:"no-store",redirect:"error",
          headers:{Accept:"application/json"},signal:AbortSignal.timeout(5000)});
        if (response.status !== 200 || response.redirected || response.url !== origin+"/auth/session" ||
            response.headers.get("content-type") !== "application/json") throw new Error("session");
        const reader=response.body.getReader(), decoder=new TextDecoder("utf-8",{fatal:true});
        let body="", count=0;
        try {
          while(true) {
            const chunk=await reader.read(); if(chunk.done)break;
            count+=chunk.value.byteLength;if(count>1024)throw new Error("size");
            body+=decoder.decode(chunk.value,{stream:true});
          }
          body+=decoder.decode();
        } finally {await reader.cancel();}
        const value=JSON.parse(body);
        if (!locationOK() || (body.match(/"(?:\\.|[^"\\])*"\s*:/g)||[]).length !== 3 ||
            !exact(value,["display_only","device_enrolled","remaining_seconds"]) ||
            value.display_only !== true || value.device_enrolled !== true ||
            typeof value.remaining_seconds !== "number" || !Number.isFinite(value.remaining_seconds) ||
            value.remaining_seconds <= 30 || value.remaining_seconds > 3600) throw new Error("session");
        respond({ticket:message.ticket,verified:true});
      } catch {respond({ticket:message.ticket,verified:false});}
    })();
    return true;
  });
}

export function createChromeResumePorts(chrome, config) {
  const send = request => chrome.runtime.sendNativeMessage(config.nativeHost,{version:1,...request});
  return Object.freeze({
    review:()=>send({action:"review-resume"}),
    prepare:async ({intent,nativeRevision,serverGeneration})=>{
      const value=await send({action:"prepare-resume",intent,revision:nativeRevision,generation:serverGeneration});
      if (!exact(value,["version","ok","approval"]) || value.version !== 1 || value.ok !== true) throw new Error("prepare");
      return value.approval;
    },
    commit:({intent,approval})=>send({action:"commit-resume",intent,...approval}),
    verifySession:async()=>{
      const key={url:config.origin+"/",name:"__Host-sdsctl-device-session"};
      const deadline=performance.now()+20000;
      const bounded=async promise=>{
        let timer;
        try {return await Promise.race([promise,new Promise((_,reject)=>{
          timer=setTimeout(()=>reject(new Error("deadline")),Math.max(0,deadline-performance.now()));
        })]);} finally {clearTimeout(timer);}
      };
      const validCookie=c=>c && c.name===key.name && c.secure && c.httpOnly && c.hostOnly && c.path==="/" &&
        c.sameSite==="strict" && !c.session && Number.isFinite(c.expirationDate) && c.expirationDate>Date.now()/1000+30;
      let tab, finished=false;
      try {
        const cookie=await bounded(chrome.cookies.get(key));
        if (!validCookie(cookie)) return false;
        tab=await bounded(chrome.tabs.create({url:config.origin+"/device-display",active:false}).then(created=>{
          // A late create after deadline still owns exactly this temporary tab.
          if (finished && Number.isSafeInteger(created?.id) && created.id>=0)
            void chrome.tabs.remove(created.id).catch(()=>{});
          return created;
        }));
        if (!Number.isSafeInteger(tab.id) || tab.id<0 || tab.incognito) return false;
        const ticket=crypto.randomUUID();
        for (let attempt=0;attempt<20 && performance.now()<deadline;attempt++) {
          const current=await bounded(chrome.tabs.get(tab.id));
          if (current.url && current.url !== config.origin+"/device-display") return false;
          if (current.status === "complete" && !current.pendingUrl) {
            try {
              const result=await bounded(chrome.tabs.sendMessage(tab.id,{action:"resume-probe",ticket},{frameId:0}));
              if (exact(result,["ticket","verified"]) && result.ticket === ticket && result.verified === true) {
                const after=await bounded(chrome.cookies.get(key)), page=await bounded(chrome.tabs.get(tab.id));
                return performance.now()<deadline && validCookie(after) && after.value === cookie.value &&
                  page.url === current.url && !page.pendingUrl;
              }
            } catch { /* A navigation may replace the document; bounded retry only. */ }
          }
          await new Promise(resolve=>setTimeout(resolve,500));
        }
        return false;
      } catch {return false;
      } finally {
        finished=true;
        if (Number.isSafeInteger(tab?.id) && tab.id>=0) {
          // Cleanup may fail, but cannot hold the consent queue indefinitely.
          await bounded(chrome.tabs.remove(tab.id)).catch(()=>{});
        }
      }
    },
  });
}
