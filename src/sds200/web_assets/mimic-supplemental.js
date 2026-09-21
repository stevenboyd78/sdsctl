/* Internal delivery candidate only. Not loaded or served by any live client.
 * Pure decoder/expiry guard: no DOM, requests, timers, storage or clock reads.
 * Callers retain one guard per explicitly verified owner context.
 */
(() => {
  "use strict";
  const ttl = 5, max = Number.MAX_SAFE_INTEGER;
  const states = new Set(["current", "unavailable", "disabled", "stale",
    "blocked", "invalid_rtc", "invalid_source"]);
  const contextKeys = ["endpoint_id", "stream_id", "session_id", "profile_revision",
    "profile_invalidation", "context_revision"];
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const require = condition => { if (!condition) throw new Error("Invalid supplemental delivery."); };
  function object(value, keys) {
    require(value !== null && typeof value === "object" && !Array.isArray(value));
    const found = Object.keys(value);
    require(found.length === keys.length && keys.every(key => Object.hasOwn(value, key)));
    return value;
  }
  function seconds(value) {
    require(typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1e15);
    return value;
  }
  function sequence(value, min = 0) {
    require(Number.isSafeInteger(value) && value >= min && value <= max);
    return value;
  }
  function context(value) {
    const data = object(value, contextKeys), result = {};
    for (const key of ["endpoint_id", "stream_id", "session_id"]) {
      require(typeof data[key] === "string" && data[key].length === 36 && uuid.test(data[key]));
      result[key] = data[key];
    }
    require(typeof data.profile_revision === "string" &&
      data.profile_revision.length === 64 && /^[0-9a-f]{64}$/.test(data.profile_revision));
    result.profile_revision = data.profile_revision;
    for (const key of ["profile_invalidation", "context_revision"]) result[key] = sequence(data[key]);
    return Object.freeze(result);
  }
  function validClock(value) {
    if (typeof value !== "string" || value.length !== 19 ||
        !/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$/.test(value)) return false;
    const [y,m,d,h,n,s] = value.split(/[-T:]/).map(Number);
    const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
    const days = [31,leap ? 29 : 28,31,30,31,30,31,31,30,31,30,31];
    return y >= 1 && m >= 1 && m <= 12 && d >= 1 && d <= days[m-1] &&
      h < 24 && n < 60 && s < 60;
  }
  function source(value, clock) {
    const data = object(value, ["status", "sample_sequence", "age_seconds", "value"]);
    require(typeof data.status === "string" && states.has(data.status));
    if (data.status !== "current") {
      require(data.sample_sequence === null && data.age_seconds === null && data.value === null);
    } else {
      sequence(data.sample_sequence, 1);
      require(seconds(data.age_seconds) < ttl);
      require(clock ? validClock(data.value) : typeof data.value === "string" &&
        data.value.length === 100 && /^[012]{100}$/.test(data.value));
    }
    return Object.freeze({...data});
  }
  function decode(payload) {
    const data = object(payload, ["protocol", "version", "context", "psi", "clock", "favorites"]);
    require(data.protocol === "sdsctl.supplemental" && data.version === 1);
    const psi = object(data.psi, ["sequence", "age_seconds"]);
    const result = {
      context: context(data.context),
      psi: Object.freeze({sequence: sequence(psi.sequence), age_seconds: seconds(psi.age_seconds)}),
      clock: source(data.clock, true), favorites: source(data.favorites, false),
    };
    require(result.psi.age_seconds < ttl ||
      (result.clock.status !== "current" && result.favorites.status !== "current"));
    return Object.freeze(result);
  }
  function empty() { return {sequence: 0, value: null, deadline: 0, status: "unavailable"}; }
  function clear(item, status) { item.value = null; item.deadline = 0; item.status = status; }
  function expire(item, now) {
    if (item.value !== null && now >= item.deadline) clear(item, "stale");
  }
  function retire(item, sample) {
    if (sample.sample_sequence !== null) item.sequence = Math.max(item.sequence, sample.sample_sequence);
    clear(item, "stale");
  }
  function acceptSource(item, sample, start, now) {
    expire(item, now);
    if (sample.status !== "current") { clear(item, sample.status); return; }
    const id = sample.sample_sequence, deadline = start + ttl - sample.age_seconds;
    if (id < item.sequence) { clear(item, "invalid_source"); return; }
    if (id === item.sequence) {
      if (item.value === null) return;
      if (item.value !== sample.value) { clear(item, "invalid_source"); return; }
      item.deadline = Math.min(item.deadline, deadline);
    } else {
      item.sequence = id; item.value = sample.value;
      item.deadline = deadline; item.status = "current";
    }
    expire(item, now);
  }
  function view(item, now) {
    return {status: item.status, sample_sequence: item.value === null ? null : item.sequence,
      age_seconds: item.value === null ? null : Math.max(0, ttl - (item.deadline - now)),
      value: item.value};
  }
  function create(binding) {
    const bound = context(binding), clock = empty(), favorites = empty();
    let latest = 0, pending = null, closed = false, psiSequence = -1, psiDeadline = 0, psiRetired = false;
    const clearBoth = status => { clear(clock, status); clear(favorites, status); };
    function suspend() { pending = null; psiRetired = psiSequence >= 0; clearBoth("unavailable"); }
    function close() { suspend(); closed = true; }
    function time(now) {
      try { seconds(now); require(now >= latest); }
      catch (_) { close(); throw new Error("A bounded, nondecreasing consumer clock is required."); }
      latest = now; return now;
    }
    function expireAll(now) {
      expire(clock, now); expire(favorites, now);
      if (psiSequence >= 0 && now >= psiDeadline) { psiRetired = true; clearBoth("stale"); }
    }
    return Object.freeze({
      context: bound,
      begin(now) {
        time(now); if (closed) throw new Error("Supplemental consumer is closed.");
        expireAll(now); pending = Object.freeze({startedAt: now}); return pending;
      },
      accept(ticket, payload, now) {
        time(now); if (closed || pending === null || ticket !== pending) return false;
        pending = null; expireAll(now);
        let data;
        try {
          data = decode(payload);
          require(contextKeys.every(key => data.context[key] === bound[key]));
          require(data.psi.sequence >= psiSequence);
        } catch (_) { clearBoth("invalid_source"); return false; }
        const deadline = ticket.startedAt + ttl - data.psi.age_seconds;
        if (data.psi.sequence === psiSequence) psiDeadline = Math.min(psiDeadline, deadline);
        else { psiSequence = data.psi.sequence; psiDeadline = deadline; psiRetired = false; }
        if (psiRetired || now >= psiDeadline) {
          psiRetired = true; retire(clock, data.clock); retire(favorites, data.favorites); return false;
        }
        acceptSource(clock, data.clock, ticket.startedAt, now);
        acceptSource(favorites, data.favorites, ticket.startedAt, now);
        return true;
      },
      snapshot(now) {
        time(now); expireAll(now);
        return {clock: view(clock, now), favorites: view(favorites, now)};
      },
      suspend, close,
    });
  }
  window.sdsctlSupplemental = Object.freeze({decode, create});
})();
