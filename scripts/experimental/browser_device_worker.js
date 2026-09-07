// LOCAL PROBE ONLY. The harness substitutes a loopback fixture origin.
const ORIGIN = "FIXTURE_ORIGIN";
const COOKIE = "__Host-sdsctl-device-probe";

async function authenticate() {
  try {
    const result = await chrome.runtime.sendNativeMessage(
      "org.sdsctl.local_probe", {version: 1, action: "authenticate"});
    if (result.ok !== true || !/^[a-f0-9]{64}$/.test(result.token) ||
        !Number.isInteger(result.expires) ||
        result.expires <= Date.now() / 1000 ||
        result.expires > Date.now() / 1000 + 120) throw new Error("response");
    await chrome.cookies.set({url: ORIGIN, name: COOKIE, value: result.token,
      path: "/", secure: true, httpOnly: true, sameSite: "strict",
      expirationDate: result.expires});
    return {ok: true};
  } catch {
    return {ok: false};
  }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id ||
      !["control.html", "startup.html"].some(name => sender.url === chrome.runtime.getURL(name)) ||
      JSON.stringify(message) !== '{"action":"authenticate"}') return false;
  // Only a boolean crosses back into the extension's control page.
  void authenticate().then(respond);
  return true;
});
