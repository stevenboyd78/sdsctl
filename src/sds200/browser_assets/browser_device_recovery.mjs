// Experimental coordination only; bundle preparation does not install or start it.
// One controller per MV3 worker; installation supplies fixed, reviewed config.
const MODES = new Set(["active", "paused", "credential_rejected", "tls_error",
  "setup_error", "protocol_error"]);
export const DEVICE_COOKIE = "__Host-sdsctl-device-session";
export const RECOVERY_ALARM = "sdsctl-device-recovery";
const STATE_KEY = "sdsctlDeviceRecovery";

// Only a broken native pipe is retryable. Missing/forbidden hosts, malformed
// framing and invalid native results remain setup errors; never expose messages.
export class BrowserNativeDisconnected extends Error {
  constructor() { super("Native connection interrupted"); }
}

export async function sendChromeNative(chrome, host, request) {
  try { return await chrome.runtime.sendNativeMessage(host, request); }
  catch (error) {
    // Documented Chromium native-messaging error (not a server-supplied string).
    if (error?.message === "Native host has exited.") throw new BrowserNativeDisconnected();
    throw new Error("Native setup or protocol failure");
  }
}
const finite = (n, max) => typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= max;
const exact = (value, keys) => value !== null && typeof value === "object" &&
  !Array.isArray(value) && Object.keys(value).sort().join(",") === [...keys].sort().join(",");

function configuration(value) {
  if (!exact(value, ["origin", "identity", "nativeHost"]) ||
      typeof value.origin !== "string" || !/^[\x21-\x7e]+$/.test(value.origin) ||
      !/^[a-f0-9]{64}$/.test(value.identity) ||
      !/^[a-z0-9_]+(?:\.[a-z0-9_]+)+$/.test(value.nativeHost)) throw new Error("setup");
  const url = new URL(value.origin);
  if (url.protocol !== "https:" || url.origin !== value.origin || url.username ||
      url.password || url.pathname !== "/" || url.search || url.hash) throw new Error("setup");
  return Object.freeze({...value});
}

function nativeResult(value) {
  const keys = ["version", "ok", "mode", "revision", "retry_after", "renew_after"];
  if (value?.session !== undefined) keys.push("session");
  if (!exact(value, keys) || value.version !== 1 || value.ok !== true ||
      !MODES.has(value.mode) || !Number.isSafeInteger(value.revision) || value.revision < 1 ||
      !finite(value.retry_after, 300) || !finite(value.renew_after, 3600)) throw new Error("native");
  if (value.session !== undefined && (!exact(value.session, ["token", "expires_in"]) ||
      typeof value.session.token !== "string" ||
      !/^sdsctl-browser-session-v1\.[a-f0-9]{64}$/.test(value.session.token) ||
      !finite(value.session.expires_in, 3600) || value.session.expires_in < 1 ||
      value.mode !== "active" || value.renew_after <= 0 ||
      value.renew_after >= value.session.expires_in)) throw new Error("native");
  return value;
}

// Provisioning is deliberate. Missing/corrupt state is not silently initialized.
export function initialBrowserRecoveryState(config) {
  return {version: 1, identity: configuration(config).identity, paused: false,
    phase: "clean", nextAt: 0};
}

export function createBrowserRecovery(ports, settings) {
  const config = configuration(settings);
  let state, stopRequested = false, epoch = 0, view = "starting";
  let operations = Promise.resolve(), writes = Promise.resolve();
  let failure = false, inFlight = null, pauseSaved = false;
  let holdForLogout = false, logoutStart = null;
  const now = () => {
    const value = ports.now();
    if (!finite(value, Number.MAX_SAFE_INTEGER)) throw new Error("clock");
    return value;
  };
  const save = () => {
    const snapshot = {...state}; // Never contains a token or native response.
    const result = writes.then(() => ports.save(snapshot));
    writes = result.catch(() => {});
    return result;
  };
  const native = async action => nativeResult(await ports.native({version: 1, action}));
  const arm = async seconds => ports.schedule(now() + Math.max(30, seconds) * 1000);
  const holding = () => holdForLogout || (state?.phase === "logout_pending" && state.nextAt > now());
  const clear = async (force = false) => {
    if (!force && holding()) return; // Preserve authentication until server logout is submitted.
    await ports.clearCookie(); // Adapter must verify absence, not just request removal.
  };
  const ready = (async () => {
    state = await ports.load();
    if (!exact(state, ["version", "identity", "paused", "phase", "nextAt"]) ||
        state.version !== 1 || state.identity !== config.identity ||
        typeof state.paused !== "boolean" || !["clean", "installing", "logout_pending", "native_retry"].includes(state.phase) ||
        (state.phase === "logout_pending" && !state.paused) ||
        !finite(state.nextAt, Number.MAX_SAFE_INTEGER)) throw new Error("state");
    state = {...state};
    pauseSaved = state.paused;
    view = "ready";
  })();
  // Keep API failures fixed and secret-free. Never log exception/native payloads.
  ready.catch(() => {});
  const queue = work => {
    const result = operations.then(work).catch(async error => {
      if (error instanceof BrowserNativeDisconnected && state && !failure) {
        try {
          if (stopRequested || state.paused) return await pausedCleanup();
          // Persist before cleanup so worker loss cannot cause a rapid retry loop.
          state.phase = "native_retry";
          state.nextAt = now() + 60000;
          await save();
          if (stopRequested || state.paused) return await pausedCleanup();
          await clear();
          if (stopRequested || state.paused) return await pausedCleanup();
          await arm(60);
          return {mode: "waiting"};
        } catch { /* Unsafe persistence/cookie cleanup still fails closed below. */ }
      }
      failure = true;
      holdForLogout = false;
      try { await clear(true); } catch { /* Report failure, not a false cleanup success. */ }
      try { await ports.cancel(); } catch { /* No automatic retry on unsafe setup. */ }
      return {mode: "setup_error"};
    });
    operations = result.then(value => { view = value.mode; });
    return result;
  };

  async function pausedCleanup() {
    // Also used after worker/browser restart. A lost suspend response is retryable.
    state.paused = true;
    if (!pauseSaved) {
      await save(); // Even a startup/suspend race must persist intent before native I/O.
      pauseSaved = true;
    }
    let nativePaused = false, cookieCleared = false;
    try { nativePaused = (await native("suspend")).mode === "paused"; } catch { /* Retry later. */ }
    if (holding()) {
      // A clock rollback must not retain this cookie indefinitely on repeated wakes.
      if (state.nextAt > now() + 60000) { state.nextAt = now() + 60000; await save(); }
      await arm(30);
      return {mode: "logout_pending", localPauseSaved: true, nativePaused,
        cookieCleared: false, serverRevocation: "unconfirmed"};
    }
    try { await clear(); cookieCleared = true; } catch { /* Retry later. */ }
    state.paused = true;
    state.phase = "clean";
    state.nextAt = 0;
    await save();
    pauseSaved = true;
    await ports.cancel();
    if (!nativePaused || !cookieCleared) await arm(30);
    return {mode: "paused", localPauseSaved: true, nativePaused, cookieCleared,
      serverRevocation: "unconfirmed"};
  }

  async function tick() {
    await ready;
    if (failure) return {mode: "setup_error"};
    if (holdForLogout) return {mode: "stopping"};
    if (stopRequested || state.paused) return pausedCleanup();
    const generation = epoch;
    const current = () => epoch === generation && !stopRequested && !state.paused;
    if (state.phase === "native_retry") {
      // Bound rollback and suppress native I/O even if repeated start events or
      // a new worker arrive before the saved retry deadline.
      if (state.nextAt > now() + 60000) {
        state.nextAt = now() + 60000;
        await save();
      }
      if (!current()) return {mode: "paused"};
      await clear();
      if (!current()) return {mode: "paused"};
      if (state.nextAt > now()) {
        await arm((state.nextAt - now()) / 1000);
        return {mode: "waiting"};
      }
      state.phase = "clean";
      state.nextAt = 0;
      await save();
      if (!current()) return {mode: "paused"};
    }
    if (state.phase === "installing") {
      await clear(); // A previous worker may have died during cookie installation.
      if (!current()) return {mode: "paused"};
      state.phase = "clean";
      state.nextAt = 0;
      await save();
    }
    const status = await native("status");
    if (!current()) return {mode: "paused"};
    if (status.session) throw new Error("status session");
    if (status.mode !== "active") {
      await clear();
      await ports.cancel();
      return {mode: status.mode};
    }
    // Bound wall-clock rollback; never wait indefinitely for an old renewal date.
    if (state.nextAt > now() + 300000) {
      state.nextAt = now() + 300000;
      await save(); // Persist the correction so repeated wakes don't defer forever.
      if (!current()) return {mode: "paused"};
    }
    const wait = Math.max(status.retry_after, (state.nextAt - now()) / 1000);
    if (wait > 0) {
      await arm(wait);
      return {mode: "waiting"};
    }
    // Wake again even if the worker is lost while a native request is outstanding.
    await arm(30);
    if (!current()) return {mode: "paused"};
    const started = now();
    const result = await native("authenticate");
    if (!current()) return {mode: "paused"};
    if (!result.session) {
      if (result.mode !== "active") {
        await clear();
        await ports.cancel();
      } else await arm(result.retry_after);
      return {mode: result.mode};
    }
    // Start expiry at request dispatch (conservative, never extend a server lease).
    const expiry = started + result.session.expires_in * 1000;
    if (expiry <= now() + 30000) {
      await clear();
      await arm(Math.max(result.retry_after, 30));
      return {mode: "waiting"};
    }
    const before = await native("status");
    if (!current()) return {mode: "paused"};
    if (before.session || before.mode !== "active" || before.revision !== result.revision) {
      await clear();
      await arm(30);
      return {mode: "waiting"};
    }
    state.phase = "installing";
    await save();
    if (!current()) return {mode: "paused"};
    await ports.setCookie({url: config.origin + "/", name: DEVICE_COOKIE,
      value: result.session.token, path: "/", secure: true, httpOnly: true,
      sameSite: "strict", expirationDate: expiry / 1000});
    // A suspend can arrive while cookies.set is outstanding. Cleanup is ordered
    // after that operation, never raced against it and then falsely acknowledged.
    const after = await native("status");
    if (!current() || after.session || after.mode !== "active" ||
        after.revision !== result.revision || expiry <= now()) {
      await clear();
      if (current()) await arm(30);
      return {mode: "waiting"};
    }
    state.phase = "clean";
    state.nextAt = Math.min(started + result.renew_after * 1000, expiry - 30000);
    await save();
    if (!current()) { await clear(); return {mode: "paused"}; }
    await arm(Math.max(0, (state.nextAt - now()) / 1000));
    return {mode: "active"};
  }

  return Object.freeze({
    tick: () => {
      if (!inFlight) {
        inFlight = queue(tick);
        void inFlight.then(() => { inFlight = null; });
      }
      return inFlight;
    },
    suspend: () => {
      stopRequested = true; // Invalidate any in-flight completion synchronously.
      epoch++;
      // Persist intent without waiting for an outstanding authenticate/cookie call.
      const persisted = ready.then(async () => {
        state.paused = true;
        state.nextAt = 0;
        await save();
        pauseSaved = true;
      });
      persisted.catch(() => {});
      return queue(async () => { await persisted; return pausedCleanup(); });
    },
    status: () => ({mode: failure ? "setup_error" :
      stopRequested && !pauseSaved ? "stopping" :
        pauseSaved ? "paused" : view}),
    beginLogout: () => {
      if (logoutStart) return logoutStart; // Repeated clicks cannot extend the hold.
      stopRequested = true;
      epoch++;
      holdForLogout = true;
      const persisted = ready.then(async () => {
        if (state.paused && state.phase !== "logout_pending") return false;
        if (state.phase !== "logout_pending") state.nextAt = now() + 60000;
        state.paused = true;
        state.phase = "logout_pending";
        await save();
        pauseSaved = true;
        await arm(30); // Recovery must not depend on the initiating document surviving.
        return true;
      });
      persisted.catch(() => {});
      logoutStart = queue(async () => {
        await persisted;
        holdForLogout = false;
        return pausedCleanup(); // Native pause, but cookie deliberately retained.
      });
      return logoutStart;
    },
    finishLogout: outcome => queue(async () => {
      await ready;
      if (!state.paused || state.phase !== "logout_pending") return {mode: "setup_error"};
      holdForLogout = false;
      state.phase = "clean";
      state.nextAt = 0;
      await save(); // A restart after this point must clean up, never hold or authenticate.
      const result = await pausedCleanup();
      return {...result, serverRevocation: ["drained", "pending"].includes(outcome) ? outcome : "unconfirmed"};
    }),
  });
}

// Adapter is opt-in. Do not register until the dashboard sign-out bridge, trusted
// configuration provisioning and real MV3 lifecycle tests have passed review.
export function connectChromeRecovery(chrome, settings) {
  const config = configuration(settings);
  const cookieKey = {url: config.origin + "/", name: DEVICE_COOKIE};
  const privateStorage = chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});
  const controller = createBrowserRecovery({
    now: Date.now,
    load: async () => { await privateStorage; return (await chrome.storage.local.get(STATE_KEY))[STATE_KEY]; },
    save: async state => { await privateStorage; await chrome.storage.local.set({[STATE_KEY]: state}); },
    native: request => sendChromeNative(chrome, config.nativeHost, request),
    schedule: when => chrome.alarms.create(RECOVERY_ALARM, {when}),
    cancel: () => chrome.alarms.clear(RECOVERY_ALARM),
    clearCookie: async () => {
      await chrome.cookies.remove(cookieKey);
      if (await chrome.cookies.get(cookieKey)) throw new Error("cleanup");
    },
    setCookie: async details => {
      const cookie = await chrome.cookies.set(details);
      if (!cookie || cookie.name !== DEVICE_COOKIE || cookie.value !== details.value || !cookie.secure || !cookie.httpOnly ||
          !cookie.hostOnly || cookie.path !== "/" || cookie.sameSite !== "strict" ||
          cookie.session || !finite(cookie.expirationDate, details.expirationDate) ||
          cookie.expirationDate <= Date.now() / 1000) {
        throw new Error("cookie");
      }
    },
  }, config);
  chrome.alarms.onAlarm.addListener(alarm => {
    if (alarm.name === RECOVERY_ALARM) void controller.tick();
  });
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender.id !== chrome.runtime.id ||
        !["startup.html", "control.html"].some(page => sender.url === chrome.runtime.getURL(page)) ||
        !exact(message, ["action"]) || !["start", "status", "suspend"].includes(message.action)) return false;
    if (message.action === "status") { respond(controller.status()); return false; }
    void (message.action === "suspend" ? controller.suspend() : controller.tick()).then(respond);
    return true;
  });
  // A future module worker must call this once at top level on every incarnation.
  // Reconcile persisted intent and recreate missing alarms, not only onStartup.
  void controller.tick();
  return controller;
}
