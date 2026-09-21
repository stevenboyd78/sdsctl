// Inlined identically in both first-party Ingress cards by the asset generator.
// The symbol is page-local: duplicate module URLs still share one cookie owner.
const sdsctlCardIngress = (() => {
  const key = Symbol.for("sdsctl.home-assistant.ingress.v1");
  if (globalThis[key]) return globalThis[key];
  let session = null, pending = null, timer = null, generation = 0;
  const leases = new Map();
  const fail = () => new Error("Home Assistant App authentication is unavailable.");
  const record = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  function bounded(promise, signal) {
    return new Promise((resolve, reject) => {
      const abort = () => finish(reject, fail());
      const timeout = window.setTimeout(abort, 8000);
      function finish(callback, value) {
        window.clearTimeout(timeout);
        signal?.removeEventListener("abort", abort);
        callback(value);
      }
      signal?.addEventListener("abort", abort, {once: true});
      if (signal?.aborted) abort();
      Promise.resolve(promise).then(value => finish(resolve, value), () => finish(reject, fail()));
    });
  }
  function invalidate() { generation++; session = null; pending = null; }
  function release(token) {
    leases.delete(token);
    if (leases.size === 0) {
      if (timer !== null) window.clearInterval(timer);
      timer = null;
      invalidate();
    }
  }
  async function ensure(api) {
    if (session !== null) return;
    if (pending === null) {
      const ticket = generation;
      const task = (async () => {
        const result = await bounded(api.callWS({type: "supervisor/api", endpoint: "/ingress/session", method: "post"}));
        if (ticket !== generation || leases.size === 0) throw fail();
        const value = record(result).session;
        if (typeof value !== "string" || !/^[A-Za-z0-9_-]{16,256}$/.test(value)) throw fail();
        document.cookie = `ingress_session=${value};path=/api/hassio_ingress/;SameSite=Strict` +
          (location.protocol === "https:" ? ";Secure" : "");
        session = value;
      })();
      pending = task;
      void task.finally(() => { if (pending === task) pending = null; }).catch(() => {});
    }
    await pending;
  }
  async function refresh() {
    if (leases.size === 0) return;
    const api = leases.values().next().value;
    const ticket = generation, current = session;
    try {
      if (current === null) await ensure(api);
      else await bounded(api.callWS({type: "supervisor/api", endpoint: "/ingress/validate_session", method: "post", data: {session: current}}));
    } catch {
      if (ticket !== generation || leases.size === 0) return;
      invalidate();
      try { await ensure(api); } catch { /* Next lease/refresh retries; no raw errors. */ }
    }
  }
  async function acquire(api, signal) {
    if (!api || typeof api.callWS !== "function" || signal?.aborted) throw fail();
    const token = {};
    leases.set(token, api);
    let released = false;
    const done = () => {
      if (released) return;
      released = true;
      signal?.removeEventListener("abort", done);
      release(token);
    };
    signal?.addEventListener("abort", done, {once: true});
    try {
      await bounded(ensure(api), signal);
      if (released || session === null) throw fail();
      if (timer === null) timer = window.setInterval(() => { void refresh(); }, 60000);
      return done;
    } catch { done(); throw fail(); }
  }
  function panelSlugs(ui) {
    return [...new Set(Object.values(record(record(ui).panels)).flatMap(panel => {
      const value = record(panel), slug = record(value.config).addon;
      return value.component_name === "app" && typeof slug === "string" &&
        /^[a-z0-9_]{1,128}$/.test(slug) && (slug === "sds200" || slug.includes("_sds200") ||
        typeof value.title === "string" && /^sds200(?:\s|$)/i.test(value.title)) ? [slug] : [];
    }))].sort();
  }
  async function resolve(api, ui, route, signal) {
    if (!api || typeof api.callWS !== "function") throw fail();
    if (!["api/v1/display-frame", "api/v1/waterfall", "api/v1/display-supplemental/context"].includes(route)) throw fail();
    const slugs = panelSlugs(ui);
    if (slugs.length === 0) throw new Error("No sds200 Home Assistant App panel is available.");
    if (slugs.length > 16) throw new Error("Too many sds200 Home Assistant App panels.");
    const settled = await Promise.allSettled(
      slugs.map(slug => bounded(Promise.resolve().then(() => api.callWS({type: "supervisor/api", endpoint: `/addons/${slug}/info`, method: "get"})), signal)),
    );
    // An inaccessible candidate cannot be assumed stopped when selecting a peer.
    if (signal?.aborted || settled.some(result => result.status !== "fulfilled")) throw fail();
    const running = [];
    for (const result of settled) {
      const info = record(result.value);
      if (!["started", "stopped"].includes(info.state)) throw fail();
      if (info.state !== "started") continue;
      if (info.ingress !== true || typeof info.ingress_url !== "string") throw fail();
      let url;
      try { url = new URL(info.ingress_url, location.origin); } catch { throw fail(); }
      if (url.origin !== location.origin || !/^\/api\/hassio_ingress\/[A-Za-z0-9_-]+\/$/.test(url.pathname) ||
          url.search || url.hash || url.username || url.password) throw fail();
      running.push(url);
    }
    if (running.length === 0) throw new Error("Start the sds200 Home Assistant App to view scanner data.");
    if (running.length !== 1) throw new Error("More than one sds200 Home Assistant App is running.");
    return new URL(route, running[0]).toString();
  }
  const owner = Object.freeze({acquire, invalidate, resolve, panelSlugs, get _leases() { return leases.size; }});
  Object.defineProperty(globalThis, key, {value: owner});
  return owner;
})();
