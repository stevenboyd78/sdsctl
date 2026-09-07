// Fixed trusted extension page. No credential/token reads, repair, claim or resume.
export function connectBrowserEntry(chrome, schedule = setTimeout) {
  // Chromium can navigate a command-line extension URL before loading that
  // unpacked extension. Retry only an explicitly selected own entry that has
  // finished without an extension document; never create tabs or reset state.
  const urls = [chrome.runtime.getURL("startup.html"), chrome.runtime.getURL("setup.html")];
  const attempted = new Map(), busy = new Set();
  const eligible = tab => Number.isSafeInteger(tab?.id) && tab.id >= 0 &&
    tab.incognito === false && tab.status === "complete" && urls.includes(tab.url) &&
    (!tab.pendingUrl || tab.pendingUrl === tab.url);
  const present = tab => chrome.runtime.getContexts({contextTypes:["TAB"],
    tabIds:[tab.id], documentUrls:[tab.url], frameIds:[0], incognito:false});
  async function retry(tab) {
    const previous = attempted.get(tab?.id);
    if (!eligible(tab) || busy.has(tab.id) || attempted.size >= 128 ||
        (previous && (previous.count >= 3 || previous.nextAt > Date.now()))) return;
    busy.add(tab.id);
    try {
      if ((await present(tab)).length) return;
      const current = await chrome.tabs.get(tab.id);
      if (!eligible(current) || current.url !== tab.url || (await present(current)).length) return;
      attempted.set(tab.id,{count:(previous?.count || 0)+1,nextAt:Date.now()+1000});
      await chrome.tabs.reload(tab.id);
    } catch { /* A closed tab or failed query never selects another tab or URL. */ }
    finally { busy.delete(tab.id); }
  }
  chrome.tabs.onUpdated.addListener((id, change, tab) => {
    if (change.status === "complete") void retry(tab);
  });
  const scan = () => {
    void chrome.tabs.query({url:urls}).then(tabs => Promise.all(tabs.map(retry))).catch(() => {});
  };
  chrome.runtime.onStartup.addListener(scan);
  chrome.runtime.onInstalled.addListener(scan);
  scan();
  // Cold-start navigation/extension registration complete in separate browser
  // tasks. Bounded rescans cover missed early events without an endless reload
  // loop. Every scan still requires the exact URL and absence of a real document.
  schedule(scan,1000);
  schedule(scan,5000);
}

export function connectBrowserStartupPage({document, window, runtime, schedule = setTimeout}, origin) {
  if (window !== window.top || window.location.href !== runtime.getURL("startup.html") ||
      new URL(origin).origin !== origin || !origin.startsWith("https://")) throw new Error("startup");
  const notice = document.getElementById("notice");
  const messages = {
    starting: "Starting managed display…",
    ready: "Waiting for a verified device session…",
    active: "Waiting for a verified device session…",
    waiting: "Server not ready or renewal is waiting. Automatic recovery remains enabled.",
    paused: "Automatic sign-in is paused. Ask your administrator to review before resuming.",
    stopping: "Saving pause intent…",
    logout_pending: "Sign-out cleanup is pending. Automatic sign-in remains paused.",
    setup_required: "First-run setup is required. Use the explicitly selected setup page; nothing was initialized.",
    setup_error: "Setup is invalid, interrupted or unavailable. Keep this profile for administrator review.",
    tls_error: "Certificate verification failed. Review server identity and trust; no bypass is available.",
    credential_rejected: "This display credential was rejected or revoked. Administrator action is required.",
    protocol_error: "The server response was invalid. Administrator review is required.",
  };
  async function poll() {
    try {
      const result = await runtime.sendMessage({action: "startup-status"});
      if (!result || Object.keys(result).sort().join(",") !== "mode,sessionReady" ||
          !Object.hasOwn(messages, result.mode) || typeof result.sessionReady !== "boolean") throw new Error("status");
      if (result.sessionReady && ["active", "waiting"].includes(result.mode)) {
        notice.textContent = "Device session installed. Opening the server-verified display…";
        window.location.replace(origin + "/device-display");
        return;
      }
      notice.textContent = messages[result.mode];
    } catch {
      notice.textContent = "Managed startup could not be confirmed. Keep this profile for administrator review.";
    }
    schedule(poll, 5000); // Sequential, redacted status only; never drives authentication.
  }
  void poll();
}
