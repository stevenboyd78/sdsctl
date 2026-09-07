// Opt-in experiment: no content script registration or production dashboard edits.
// Install in an ISOLATED, top-frame, exact-origin content-script world only.
const exact = (value, keys) => value && typeof value === "object" && !Array.isArray(value) &&
  Object.keys(value).sort().join(",") === [...keys].sort().join(",");
const OUTCOMES = new Set(["drained", "pending", "unconfirmed"]);

export function connectLogoutWorker(chrome, controller, origin, clock = Date.now) {
  if (new URL(origin).origin !== origin || !origin.startsWith("https://")) throw new Error("setup");
  let pending = null;
  const eligible = sender => sender.id === chrome.runtime.id && sender.url === origin + "/" &&
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
}

export async function submitDeviceLogout(fetcher, origin) {
  // Fetch must be the isolated content world's browser fetch. It supplies the
  // real same-origin Origin/Fetch Metadata and HttpOnly cookie, not extension fetch.
  try {
    const response = await fetcher(origin + "/auth/logout", {method: "POST",
      mode: "same-origin", credentials: "same-origin", cache: "no-store", redirect: "error",
      headers: {Accept: "application/json"}, signal: AbortSignal.timeout(10000)});
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
    return result.drained ? "drained" : "pending";
  } catch { return "unconfirmed"; } // Never expose body/exception data to page or logs.
}

export function connectLogoutContent({document, window, runtime, fetcher}, origin) {
  if (window !== window.top || window.location.href !== origin + "/" ||
      new URL(origin).origin !== origin || !origin.startsWith("https://")) throw new Error("setup");
  let busy = false;
  document.addEventListener("submit", event => {
    const form = event.target;
    if (form?.tagName !== "FORM" || form.method.toLowerCase() !== "post" ||
        form.action !== origin + "/auth/logout") return;
    // Never let a programmatic submit event bypass persisted intent on this managed page.
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!event.isTrusted || busy) return;
    busy = true;
    const notice = document.createElement("p");
    notice.setAttribute("role", "status");
    notice.textContent = "Saving sign-out intent…";
    form.append(notice);
    void (async () => {
      let message = "Sign-out could not be confirmed. Automatic sign-in may require administrator attention.";
      try {
        const prepared = await runtime.sendMessage({action: "logout-begin"});
        if (prepared?.mode !== "logout_pending" || prepared.submit !== true ||
            typeof prepared.ticket !== "string" ||
            !/^[a-f0-9-]{36}$/.test(prepared.ticket)) throw new Error("prepare");
        notice.textContent = "Automatic sign-in paused. Contacting the server…";
        const outcome = await submitDeviceLogout(fetcher, origin);
        const result = await runtime.sendMessage({action: "logout-finish", ticket: prepared.ticket, outcome});
        if (result?.mode === "paused" && result.localPauseSaved === true) {
          message = result.serverRevocation === "drained" && result.cookieCleared === true ?
            "Signed out. Server shutdown confirmed; automatic sign-in remains paused." :
            "Automatic sign-in is paused. Server shutdown or local cleanup is unconfirmed; ask an administrator to check before resuming.";
        }
      } catch { /* Fixed text only; never insert a server response as HTML. */ }
      notice.textContent = message;
      // Do not navigate/reload or clear unrelated cookies. Stale dashboard rendering
      // and managed recovery UI remain a separate integration gate.
    })();
  }, true);
}
