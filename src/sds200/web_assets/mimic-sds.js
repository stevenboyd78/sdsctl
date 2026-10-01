"use strict";

// Source-code geometry, not profile-controlled code. No HTML injection or commands.
(() => {
  const contract = __SDSCTL_MIMIC_CONTRACT__;
  const MAX_BYTES = 256 * 1024;
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const HASH = /^[0-9a-f]{64}$/;
  const BAD_TEXT = /\p{C}/u;
  const keys = (value, expected) => value !== null && typeof value === "object" &&
    !Array.isArray(value) && Object.keys(value).length === expected.length &&
    expected.every(key => Object.hasOwn(value, key));
  // Match Python's bounded Unicode scalar count, not UTF-16 code-unit length.
  const text = (value, limit) => typeof value === "string" && Array.from(value).length <= limit && !BAD_TEXT.test(value);
  const nullable = (value, predicate) => value === null || predicate(value);
  const number = value => Number.isFinite(value) && value >= 0;
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  function require(condition) { if (!condition) throw new Error("Invalid Mimic-SDS frame."); }

  function decode(payload) {
    require(keys(payload, ["protocol", "version", "display"]) && payload.protocol === "sdsctl.web" && payload.version === 1);
    const data = payload.display;
    require(keys(data, ["schema_version", "endpoint_id", "stream_id", "session_id", "failure", "source_status", "frames"]));
    require(data.schema_version === 1 && UUID.test(data.endpoint_id) && UUID.test(data.stream_id));
    require(nullable(data.session_id, value => typeof value === "string" && UUID.test(value)));
    require(nullable(data.failure, value => text(value, 64) && /^[a-z_]+$/.test(value)));
    require(nullable(data.source_status, value => text(value, 64) && /^[a-z_]+$/.test(value)));
    require(keys(data.frames, ["preferred", "simple", "detail"]));
    for (const frame of Object.values(data.frames)) {
      require(keys(frame, ["status", "layout_basis", "profile_status", "profile_refresh_pending", "profile_revision", "source", "sequence", "age_seconds", "screen", "indicators"]));
      require(contract.statuses.includes(frame.status) && contract.bases.includes(frame.layout_basis) && contract.profiles.includes(frame.profile_status));
      require(typeof frame.profile_refresh_pending === "boolean");
      require(nullable(frame.profile_revision, value => typeof value === "string" && HASH.test(value)));
      require(nullable(frame.sequence, value => Number.isSafeInteger(value) && value >= 0));
      require(nullable(frame.age_seconds, value => number(value) && value <= 1e15));
      require((frame.sequence === null) === (frame.age_seconds === null));
      require((frame.profile_revision === null) === (frame.source === null));
      if (frame.source !== null) {
        require(keys(frame.source, ["source_id", "source_kind", "acquired_at", "imported_at"]));
        require(UUID.test(frame.source.source_id) && contract.sources.includes(frame.source.source_kind));
        for (const key of ["acquired_at", "imported_at"]) {
          require(text(frame.source[key], 40) && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?\+00:00$/.test(frame.source[key]) && Number.isFinite(Date.parse(frame.source[key])));
        }
      }
      require(keys(frame.indicators, ["alert_led", "system_hold", "department_hold", "channel_hold", "site_hold"]));
      require(nullable(frame.indicators.alert_led, value => Object.hasOwn(contract.leds, value)));
      for (const name of ["system_hold", "department_hold", "channel_hold", "site_hold"]) require(nullable(frame.indicators[name], value => typeof value === "boolean"));
      if (frame.status !== "current") require(Object.values(frame.indicators).every(value => value === null));
      if (frame.status === "current") require(data.session_id !== null && frame.sequence !== null);
      if (frame.status === "disconnected") require(data.session_id === null && frame.screen === null);
      if (frame.screen === null) continue;
      const screen = frame.screen;
      require(frame.source !== null && frame.profile_status !== "unavailable");
      require(keys(screen, ["mode", "rows", "columns", "color_mode", "regions", "issues"]));
      require(Object.hasOwn(contract.layouts, screen.mode) && ["COLOR", "BLACK", "WHITE"].includes(screen.color_mode));
      const layout = contract.layouts[screen.mode];
      require(screen.rows === layout.rows && screen.columns === layout.columns);
      require(Array.isArray(screen.regions) && screen.regions.length === layout.regions.length);
      screen.regions.forEach((region, index) => {
        const canonical = layout.regions[index];
        require(keys(region, [...Object.keys(canonical), "selection", "token", "stored_color", "value_status", "text"]));
        for (const [key, value] of Object.entries(canonical)) require(region[key] === value);
        require(contract.selections.includes(region.selection) && contract.values.includes(region.value_status));
        require(nullable(region.token, value => text(value, 64) && /^[\x20-\x7e]*$/.test(value)));
        require(nullable(region.text, value => text(value, 256)));
        require(region.value_status === "raw_source" ? region.text !== null && frame.status === "current" : region.text === null);
        if (region.stored_color !== null) {
          require(keys(region.stored_color, ["text", "background"]));
          for (const color of Object.values(region.stored_color)) require(typeof color === "string" && /^[0-9a-fA-F]{6}$/.test(color));
        }
      });
      require(Array.isArray(screen.issues) && screen.issues.length <= 64);
      for (const issue of screen.issues) require(keys(issue, ["namespace", "group", "kind"]) && ["option", "color"].includes(issue.namespace) && Number.isInteger(issue.group) && issue.group >= 0 && issue.group <= 64 && contract.issues.includes(issue.kind));
    }
    for (const other of [data.frames.simple, data.frames.detail]) {
      for (const key of ["status", "profile_status", "profile_refresh_pending", "profile_revision", "source", "sequence", "age_seconds", "indicators"]) require(same(data.frames.preferred[key], other[key]));
    }
    const mode = data.frames.preferred.screen?.mode;
    if (mode) {
      const family = mode.replace(/^(simple|detail)_/, "");
      for (const style of ["simple", "detail"]) require(data.frames[style].screen?.mode === (["trunk", "conventional"].includes(family) ? `${style}_${family}` : mode));
    } else require(data.frames.simple.screen === null && data.frames.detail.screen === null);
    return data;
  }

  const states = {
    current: "Current scanner data", waiting: "Connected — waiting for a new scanner frame",
    disconnected: "Disconnected — retrying", stale: "Stale scanner data — values cleared",
    unsupported_screen: "This scanner screen is not supported by Mimic-SDS",
    override: "Scanner menu, popup or replay — screen values cleared",
    ambiguous_records: "Conflicting scanner data — values cleared",
  };
  const make = (tag, value, className) => {
    const node = document.createElement(tag);
    if (value !== undefined) node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  function caption(region) {
    if (region.id.startsWith("option_") && (/[abc]_/.test(region.id) || ["Volume", "Squelch", "Volume&Squelch"].includes(region.token))) return contract.captions[region.token] ?? null;
    return null;
  }
  function presentValue(region) {
    // PSI frequency text already includes units; no inferred raw-digit conversion.
    const prefix = caption(region);
    const value = region.text ?? "—";
    return prefix && !value.toLowerCase().startsWith(prefix.toLowerCase() + ":") ? `${prefix}: ${value}` : value;
  }
  function presentIndicator(region, current = true) {
    if (!["fixed", "configured"].includes(region.selection)) return null;
    const spec = contract.indicator_presentation;
    const status = current ? region.value_status : "not_current";
    const unknown = {text: "?", state: "unknown", foreground: spec.unknown_color, background: "000000"};
    if (region.id === "signal") {
      return status === "raw_source" && /^[0-5]$/.test(region.text ?? "")
        ? {text: spec.signal_bars.slice(0, Number(region.text)), state: `level_${region.text}`}
        : unknown;
    }
    const label = Object.hasOwn(spec.regions, region.id) ? spec.regions[region.id]
      : region.id.startsWith("icon_") && Object.hasOwn(spec.tokens, region.token) ? spec.tokens[region.token] : null;
    if (label === null) return null;
    if (status === "raw_source" && (region.text === label || (label === "AVOID" && region.text === "T-AVOID")))
      return {text: region.text, state: region.text === "T-AVOID" ? "temporary" : "on"};
    if (status === "blank")
      return {text: region.id === "function" ? "" : label, state: "off", foreground: spec.inactive_color, background: "000000"};
    return unknown;
  }
  function draw(target, frame) {
    const grid = make("div", undefined, "mimic-grid");
    if (frame.screen === null) {
      grid.classList.add("mimic-empty");
      grid.textContent = frame.profile_revision === null ? contract.missing_profile_message : contract.empty_messages[frame.status];
      target.replaceChildren(grid);
      return;
    }
    grid.dataset.mode = frame.screen.mode;
    for (const region of frame.screen.regions) {
      const cell = make("div", undefined, "mimic-cell");
      cell.dataset.region = region.id;
      cell.dataset.kind = region.kind;
      cell.dataset.lines = String(region.name_lines);
      cell.dataset.alignment = region.alignment;
      cell.dataset.valueStatus = region.value_status;
      const indicator = presentIndicator(region, frame.status === "current");
      if (indicator !== null) cell.dataset.indicator = indicator.state;
      cell.style.gridArea = `${region.row + 1} / ${region.column + 1} / span ${region.rows} / span ${region.columns}`;
      const held = frame.indicators[region.token === "SiteName" ? "site_hold" : `${region.id}_hold`];
      cell.dataset.hold = held === true ? "on" : held === false ? "off" : "unknown";
      if (frame.screen.color_mode === "COLOR" && region.stored_color !== null) {
        let {text: foreground, background} = region.stored_color;
        const reverse = region.reverse_colors && (region.id !== "function" || indicator?.state === "on");
        if (reverse || held === true) [foreground, background] = [background, foreground];
        cell.style.color = `#${foreground}`;
        cell.style.backgroundColor = `#${background}`;
      }
      if (indicator?.foreground) cell.style.color = `#${indicator.foreground}`;
      if (indicator?.background) cell.style.backgroundColor = `#${indicator.background}`;
      const empty = ["blank", "empty"].includes(region.value_status) || region.kind === "spacer";
      if (indicator !== null) {
        if (indicator.text) cell.append(make("span", indicator.text));
      } else if (!empty) cell.append(make("span", presentValue(region)));
      cell.title = `${region.token ?? region.id}: ${region.value_status}${indicator === null ? "" : `; indicator: ${indicator.state}`}`;
      cell.setAttribute("aria-label", cell.title);
      grid.append(cell);
    }
    target.replaceChildren(grid);
  }

  function layoutBasisText(frame) {
    if (frame.screen === null) return "Scanner screen layout is not currently available.";
    return frame.layout_basis === "profile_preference_unconfirmed" ? "Profile preference — physical Simple/Detail toggle is not reported." : frame.layout_basis === "explicit_presentation_choice" ? "Local Simple/Detail choice — scanner unchanged." : "Documented scanner screen family.";
  }

  // Only these local phase identifiers enter diagnostics; never exception text,
  // response bodies, URLs, credentials or scanner/profile values.
  async function readResponse(response, phase = () => {}, progress = () => {}, decoder = decode, maxBytes = MAX_BYTES) {
    phase("http_status");
    require(response.ok);
    phase("content_type");
    require((response.headers.get("content-type") ?? "").includes("application/json"));
    phase("response_body");
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    try {
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > maxBytes) { phase("response_size"); require(false); }
        progress(size);
        chunks.push(value);
      }
    } catch (error) { await reader.cancel().catch(() => {}); throw error; }
    finally { reader.releaseLock(); }
    const bytes = new Uint8Array(size);
    let position = 0;
    for (const value of chunks) { bytes.set(value, position); position += value.byteLength; }
    phase("utf8_decode");
    const source = new TextDecoder("utf-8", {fatal: true}).decode(bytes);
    phase("json_decode");
    const payload = JSON.parse(source);
    phase("frame_validation");
    return decoder(payload);
  }

  function create({host, standard, url, request, supplementalContext = null, supplementalRoot = null, supplementalDemand = false}) {
    // Internal opt-ins only; the normal shell supplies neither. A fixed-context
    // caller cannot rebind. Negotiated callers use only the authenticated fixed
    // routes under their same-origin (possibly HA Ingress-prefixed) web root.
    const negotiated = supplementalRoot !== null;
    require(typeof supplementalDemand === "boolean" && (!supplementalDemand || negotiated));
    require(!negotiated || supplementalContext === null);
    const supplemental = supplementalContext !== null || negotiated;
    const auxiliary = supplemental ? window.sdsctlSupplemental : null;
    require(!supplemental ||
      ["create", "bundle", "present", "favoritesRows"].every(key => typeof auxiliary?.[key] === "function"));
    require(!supplementalDemand || ["renewalId", "demandResponse"].every(key => typeof auxiliary?.[key] === "function"));
    let contextUrl = null, frameUrl = url, negotiationNeeded = negotiated;
    if (negotiated) {
      require(typeof supplementalRoot === "string" && supplementalRoot.length <= 2048 &&
        typeof auxiliary.contextResponse === "function");
      const root = new URL(supplementalRoot);
      require(["http:", "https:"].includes(root.protocol) && root.origin === window.location.origin &&
        !root.username && !root.password && !root.search && !root.hash && root.pathname.endsWith("/"));
      contextUrl = new URL("api/v1/display-supplemental/context", root).href;
      frameUrl = new URL("api/v1/display-supplemental/frame", root).href;
    }
    let auxiliaryGuard = supplementalContext === null ? null : auxiliary.create(supplementalContext);
    const retiredConnections = new Set(), retiredProfiles = new Set();
    const decoder = auxiliary === null ? decode
      : payload => auxiliary.bundle(payload, decode, auxiliaryGuard.context);
    let available = false, active = false, selected = false, stopped = false, closed = false;
    let generation = 0, controller = null, timer = null, expiryTimer = null, auxiliaryTimer = null;
    let requestTimer = null, renewalPending = false;
    const demandUnconfirmed = "Supplemental demand unconfirmed — renewal stopped; reads may have occurred. Reopen for administrator review.";
    let latest = null, deadline = null, sequenceDeadline = null, endpoint = null, session = null, sequence = null;
    let style = "preferred", treatment = "border";
    const toolbar = make("div", undefined, "mimic-toolbar");
    toolbar.hidden = true;
    const picker = (title, items) => {
      const label = make("label", title);
      const select = make("select");
      for (const [value, title] of items) { const option = make("option", title); option.value = value; select.append(option); }
      label.append(select); toolbar.append(label); return select;
    };
    const layout = picker("Scanner presentation", [["standard", "Dashboard"], ["mimic", "Mimic-SDS"]]);
    layout.id = "mimic-presentation";
    const mode = picker("Mimic layout", [["preferred", "Profile preference"], ["simple", "Simple"], ["detail", "Detail"]]);
    mode.id = "mimic-mode";
    const led = picker("Alert LED treatment", [["border", "Surrounding border"], ["strips", "Top and bottom strips"]]);
    led.id = "mimic-led";
    led.value = treatment;
    const pane = make("section", undefined, "mimic-display");
    pane.id = "mimic-display";
    pane.hidden = true;
    pane.dataset.ledTreatment = treatment;
    const status = make("p", "Waiting for scanner data", "mimic-status");
    status.setAttribute("role", "status");
    const basis = make("p", undefined, "mimic-basis");
    const surround = make("div", undefined, "mimic-surround");
    const ledStatus = make("p", "Alert LED unavailable", "mimic-note");
    const details = make("details", undefined, "mimic-details");
    details.append(make("summary", "Profile, LED and field details"));
    const detailText = make("p");
    const failureNote = make("p");
    failureNote.id = "mimic-last-failure";
    failureNote.hidden = true;
    let failures = 0;
    const traceButton = make("button", "Trace request timing for 2 minutes");
    traceButton.type = "button"; traceButton.id = "mimic-trace-start";
    const traceNote = make("p", "Request tracing is off. It records opaque IDs and timing metadata in the App log, never scanner values or credentials.");
    traceNote.id = "mimic-trace-status";
    let tracePrefix = null, traceNumber = 0, traceUntil = 0, traceTimer = null;
    function stopTrace(message) {
      tracePrefix = null; traceUntil = 0;
      window.clearTimeout(traceTimer); traceTimer = null;
      traceButton.textContent = "Trace request timing for 2 minutes";
      traceNote.textContent = message;
    }
    traceButton.addEventListener("click", () => {
      if (tracePrefix !== null) { stopTrace("Request tracing stopped."); return; }
      if (!demanded()) return;
      try {
        const bytes = window.crypto.getRandomValues(new Uint8Array(8));
        tracePrefix = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
      } catch { stopTrace("Request tracing is unavailable in this browser; display updates are unchanged."); return; }
      traceNumber = 0; traceUntil = performance.now() + 120000;
      traceButton.textContent = "Stop request timing trace";
      traceNote.textContent = "Request tracing enabled for 2 minutes. The App has a bounded trace budget; missing entries do not prove a request never arrived.";
      traceTimer = window.setTimeout(() => stopTrace("Request tracing finished. Existing App log entries remain; tracing is off."), 120000);
    });
    const rows = make("ul");
    details.append(detailText, failureNote, traceButton, traceNote, rows);
    const auxiliaryNote = make("p"), favorites = make("pre");
    if (auxiliary !== null) {
      auxiliaryNote.id = "mimic-supplemental-status";
      favorites.id = "mimic-favorites-states";
      details.append(auxiliaryNote, favorites);
    }
    pane.append(status, basis, surround, ledStatus, details);
    host.prepend(toolbar, pane);
    function clear(message) {
      latest = null; deadline = null;
      window.clearTimeout(expiryTimer); expiryTimer = null;
      window.clearTimeout(auxiliaryTimer); auxiliaryTimer = null;
      auxiliaryNote.textContent = ""; favorites.textContent = "";
      status.textContent = message; pane.dataset.state = "unavailable";
      basis.textContent = "No current scanner values are shown.";
      surround.replaceChildren(make("div", "Waiting for current scanner data…", "mimic-grid mimic-empty"));
      surround.style.setProperty("--mimic-led", "#3b4654");
      surround.dataset.led = "unknown";
      ledStatus.textContent = "Alert LED unavailable";
      rows.replaceChildren(); detailText.textContent = "";
    }
    function render() {
      if (latest === null) return;
      if (deadline !== null && performance.now() >= deadline) { clear(states.stale); return; }
      let frame = latest.frames[style];
      if (auxiliaryGuard !== null) {
        const values = auxiliaryGuard.snapshot(performance.now() / 1000);
        frame = auxiliary.present(frame, values, contract.supplemental_clock_regions);
        auxiliaryNote.textContent = `Scanner clock: ${values.clock.status}. Global Favorites quick keys (00–99, not LCD F0/S0/D0): ${values.favorites.status}.`;
        favorites.textContent = auxiliary.favoritesRows(values).join("\n");
        window.clearTimeout(auxiliaryTimer); auxiliaryTimer = null;
        const remaining = Object.values(values).filter(value => value.status === "current")
          .map(value => (5 - value.age_seconds) * 1000);
        if (remaining.length) auxiliaryTimer = window.setTimeout(render, Math.max(1, Math.min(...remaining)));
      }
      const message = states[frame.status];
      if (status.textContent !== message) status.textContent = message;
      pane.dataset.state = frame.status;
      basis.textContent = layoutBasisText(frame);
      draw(surround, frame);
      const color = frame.indicators.alert_led;
      surround.style.setProperty("--mimic-led", `#${contract.leds[color] ?? "3b4654"}`);
      surround.dataset.led = color ?? "unknown";
      ledStatus.textContent = `Scanner alert LED: ${color ?? "unavailable"}. Reported color only; blink timing is not reproduced.`;
      detailText.textContent = `${basis.textContent} ${ledStatus.textContent} Profile: ${frame.profile_status}; source: ${latest.source_status ?? "unavailable"}${frame.profile_refresh_pending ? "; refresh pending" : ""}. Accepted revision: ${frame.profile_revision ?? "none"}. Source notation is preserved. Neutral colors indicate missing/unqualified color mapping; BLACK/WHITE transforms and icon glyphs are not yet qualified.`;
      rows.replaceChildren(...(frame.screen?.regions ?? []).filter(region => !["empty", "blank"].includes(region.value_status)).map(region => make("li", `${region.token ?? region.id}: ${region.value_status}`)));
    }
    function demanded() { return selected && available && active && !stopped && !document.hidden; }
    function cancel(message) {
      if (renewalPending) {
        closed = stopped = true; renewalPending = false; auxiliaryGuard?.close();
        message = demandUnconfirmed;
      }
      generation++; controller?.abort(); controller = null;
      window.clearTimeout(requestTimer); requestTimer = null;
      auxiliaryGuard?.suspend();
      stopTrace("Request tracing is off.");
      window.clearTimeout(timer); timer = null; clear(message);
    }
    function stopSession(message) {
      closed = stopped = true; auxiliaryGuard?.close();
      failureNote.hidden = true; failureNote.textContent = "";
      delete failureNote.dataset.reason; delete failureNote.dataset.phase;
      cancel(message);
    }
    function admitted(response) {
      if (negotiated && [401, 403].includes(response.status)) {
        // dashboardFetch handles native sign-in. Ingress denial must not invent
        // a native login flow, and neither admission failure may auto-retry.
        stopSession("Display access ended — values cleared. Reopen through the authorized dashboard entry.");
        return false;
      }
      return true;
    }
    function bindContext(binding) {
      const old = auxiliaryGuard?.context;
      if (old && same(old, binding)) return;
      const identity = value => `${value.stream_id}/${value.session_id}`;
      const connectionChanged = old && identity(old) !== identity(binding);
      let epochChanged = false;
      require(!retiredConnections.has(identity(binding)));
      if (old) {
        require(binding.endpoint_id === old.endpoint_id);
        if (!connectionChanged) {
          require(binding.context_revision >= old.context_revision &&
            binding.profile_invalidation >= old.profile_invalidation);
          epochChanged = binding.context_revision > old.context_revision ||
            binding.profile_invalidation > old.profile_invalidation;
          if (!epochChanged) require(!retiredProfiles.has(binding.profile_revision));
        }
        // Monotonic epochs retire their predecessors without retaining each one.
        // Bound only incomparable connection IDs / same-epoch profile changes.
        if ((connectionChanged && retiredConnections.size >= 64) ||
            (!connectionChanged && !epochChanged && retiredProfiles.size >= 64)) {
          stopSession("Display connection history limit reached — reopen the authorized dashboard entry.");
          return;
        }
      }
      const replacement = auxiliary.create(binding);
      if (connectionChanged) retiredConnections.add(identity(old));
      if (connectionChanged || epochChanged) retiredProfiles.clear();
      else if (old) retiredProfiles.add(old.profile_revision);
      auxiliaryGuard?.close(); auxiliaryGuard = replacement;
      session = sequence = sequenceDeadline = null;
      clear("New display connection verified — waiting for current scanner data…");
    }
    async function poll(ticket) {
      if (!demanded() || ticket !== generation) return;
      controller = new AbortController();
      const current = controller;
      const started = performance.now();
      let frameStarted = started;
      const traceId = tracePrefix !== null && started < traceUntil && traceNumber < 999999
        ? `${tracePrefix}-${++traceNumber}` : null;
      let traceAcknowledged = false;
      let timedOut = false, phase = "request", timeoutPhase = null;
      let headersMs = null, firstByteMs = null, bodyMs = null, bytes = 0;
      const elapsedMs = () => Math.min(300000, Math.max(0, Math.round(performance.now() - started)));
      // Slow HA ingress responses may arrive after two seconds. The request
      // budget is separate from the existing observation's expiry timer: a
      // pending request never renews the display's five-second freshness lease.
      const timeout = window.setTimeout(() => {
        timedOut = true; timeoutPhase = phase; current.abort();
        if (renewalPending && controller === current) stopSession(demandUnconfirmed);
      }, 5000);
      requestTimer = timeout;
      let delay = 250;
      try {
        const options = {signal: current.signal, credentials: "same-origin", cache: "no-store", redirect: "error"};
        if (negotiated && negotiationNeeded) {
          phase = "context_request";
          const response = await request(contextUrl, {...options, headers: {"X-SDSCTL-Supplemental-Version": "1"}});
          if (ticket !== generation || !demanded()) return;
          require(!timedOut);
          if (!admitted(response)) return;
          const binding = await readResponse(response, value => {
            if (!timedOut) phase = `context_${value}`;
          }, () => {}, auxiliary.contextResponse, 2048);
          if (ticket !== generation || !demanded()) return;
          require(!timedOut);
          phase = "context_binding";
          bindContext(binding);
          if (closed) return;
          negotiationNeeded = false;
        }
        if (supplementalDemand) {
          const binding = auxiliaryGuard.context;
          const nonce = auxiliary.renewalId(Array.from(crypto.getRandomValues(new Uint8Array(16))));
          phase = "demand_request"; renewalPending = true;
          const response = await request(new URL("demand", contextUrl).href, {...options, method: "POST", headers: {
            "X-SDSCTL-Supplemental-Version": "1", "X-SDSCTL-Supplemental-Context": JSON.stringify(binding),
            "X-SDSCTL-Supplemental-Renewal": nonce}});
          if (ticket !== generation || !demanded()) return;
          require(!timedOut && performance.now() - started < 5000);
          if (response.status === 409) {
            renewalPending = false; negotiationNeeded = true; phase = "context_changed"; require(false);
          }
          if (!admitted(response)) return;
          const renewed = await readResponse(response, () => {}, () => {},
            value => auxiliary.demandResponse(value, binding, nonce), 2048);
          if (ticket !== generation || !demanded()) return;
          require(!timedOut && performance.now() - started < 5000);
          renewalPending = false; bindContext(renewed);
          if (closed) return;
        }
        frameStarted = performance.now();
        const auxiliaryTicket = auxiliaryGuard?.begin(frameStarted / 1000);
        phase = "request";
        if (traceId !== null) options.headers = {"X-SDSCTL-Mimic-Trace": traceId};
        if (negotiated) options.headers = {...options.headers,
          "X-SDSCTL-Supplemental-Version": "1",
          "X-SDSCTL-Supplemental-Context": JSON.stringify(auxiliaryGuard.context)};
        const response = await request(frameUrl, options);
        // A transport that ignores abort must not read a late body or overwrite
        // the phase/timings recorded at the deadline.
        if (ticket !== generation || !demanded()) return;
        require(!timedOut);
        if (!admitted(response)) return;
        if (negotiated && response.status === 409) {
          negotiationNeeded = true; phase = "context_changed"; require(false);
        }
        headersMs = elapsedMs();
        traceAcknowledged = traceId !== null && response.headers.get("x-sdsctl-mimic-trace") === traceId;
        const decoded = await readResponse(response, value => {
          if (!timedOut) {
            phase = value;
            if (value === "utf8_decode") bodyMs = elapsedMs();
          }
        }, size => {
          if (!timedOut) {
            bytes = size;
            if (firstByteMs === null && size > 0) firstByteMs = elapsedMs();
          }
        }, decoder);
        if (ticket !== generation || !demanded()) return;
        // Even a transport/body reader that ignores abort cannot publish a late
        // response as a successful update after this request's deadline.
        require(!timedOut);
        const data = auxiliaryGuard === null ? decoded : decoded.display;
        phase = "endpoint_identity";
        require(endpoint === null || endpoint === data.endpoint_id);
        const identity = `${data.stream_id}/${data.session_id}`;
        const incoming = data.frames.preferred.sequence;
        phase = "frame_sequence";
        require(identity !== session || incoming === null || sequence === null || incoming >= sequence);
        if (auxiliaryGuard !== null)
          auxiliaryGuard.accept(auxiliaryTicket, decoded.supplemental, performance.now() / 1000);
        // Retain the freshness limit even while the screen is cleared or hidden.
        // A repeated sequence cannot renew its lease by reporting a younger age.
        let incomingDeadline = incoming === null ? null : frameStarted + (5 - data.frames.preferred.age_seconds) * 1000;
        if (identity === session && incoming === sequence && sequenceDeadline !== null && incomingDeadline !== null) incomingDeadline = Math.min(sequenceDeadline, incomingDeadline);
        endpoint = data.endpoint_id;
        if (identity !== session) { sequence = null; sequenceDeadline = null; }
        session = identity;
        if (incoming !== null) { sequence = incoming; sequenceDeadline = incomingDeadline; }
        latest = data; deadline = data.frames.preferred.status === "current" ? sequenceDeadline : null;
        window.clearTimeout(expiryTimer);
        if (deadline !== null) expiryTimer = window.setTimeout(() => clear(states.stale), Math.max(0, deadline - performance.now()));
        phase = "rendering";
        if (data.failure !== null) clear("Display configuration unavailable — administrator review required.");
        else render();
        if (traceId !== null && tracePrefix !== null) traceNote.textContent = `Last traced update: ${traceId}; App acknowledgement: ${traceAcknowledged ? "received" : "not confirmed"}; elapsed ${elapsedMs()} ms. Tracing stops automatically after 2 minutes. IDs confer no access; missing entries or acknowledgements do not prove non-arrival.`;
      } catch {
        if (renewalPending) { stopSession(demandUnconfirmed); return; }
        if (ticket === generation && demanded()) {
          auxiliaryGuard?.suspend();
          const reason = timedOut ? "request_timeout" : phase;
          const interruptedPhase = timeoutPhase ?? phase;
          failures = Math.min(failures + 1, 999999);
          const elapsed = elapsedMs();
          // Kept across recovery so a short interruption can be inspected later.
          // In-memory only; a page reload/session stop removes this diagnostic.
          failureNote.hidden = false;
          failureNote.dataset.reason = reason;
          failureNote.dataset.phase = interruptedPhase;
          const timing = value => value === null ? "not reached" : `${value} ms`;
          failureNote.textContent = `Last interrupted update: ${reason}; phase: ${interruptedPhase}; elapsed ${elapsed} ms; headers: ${timing(headersMs)}; first byte: ${timing(firstByteMs)}; body complete: ${timing(bodyMs)}; bytes read: ${bytes}; interruptions this page: ${failures}. Timings are since request start, not proof of a particular network hop. No response content was retained.`;
          if (traceId !== null) failureNote.textContent += ` Trace ID: ${traceId}; App acknowledgement: ${traceAcknowledged ? "received" : "not confirmed"}.`;
          clear(`Mimic-SDS data unavailable — retrying safely (${reason}).`);
        }
        delay = 2000;
      } finally {
        // Visibility may change before its event handler runs. An early return
        // must not leave an unresolved mutation eligible for a later retry.
        if (renewalPending && controller === current) stopSession(demandUnconfirmed);
        window.clearTimeout(timeout);
        if (requestTimer === timeout) requestTimer = null;
        if (controller === current) controller = null;
        if (ticket === generation && demanded()) timer = window.setTimeout(() => poll(ticket), delay);
      }
    }
    function reconcile() {
      toolbar.hidden = !available && !selected;
      host.dataset.mimicAvailable = String(!toolbar.hidden);
      mode.parentElement.hidden = led.parentElement.hidden = !selected;
      pane.hidden = !selected; standard.hidden = selected;
      if (demanded()) {
        if (controller === null && timer === null) { clear("Waiting for current scanner data…"); void poll(generation); }
      } else cancel(stopped ? "Session stopped — scanner values cleared." : "Mimic-SDS is inactive or unavailable — values cleared.");
    }
    layout.addEventListener("change", () => { selected = layout.value === "mimic"; reconcile(); });
    mode.addEventListener("change", () => { style = ["preferred", "simple", "detail"].includes(mode.value) ? mode.value : "preferred"; render(); });
    led.addEventListener("change", () => { treatment = led.value === "border" ? "border" : "strips"; pane.dataset.ledTreatment = treatment; });
    return Object.freeze({
      context(value) { if (closed) return; available = value.available === true; active = value.active === true; stopped = value.stopped === true; reconcile(); },
      stop() { stopSession("Session stopped — scanner values cleared."); },
    });
  }
  window.sdsctlMimic = Object.freeze({create, decode, presentValue, presentIndicator});
})();
