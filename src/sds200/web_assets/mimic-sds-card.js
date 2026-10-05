// Appended inside the shared Mimic renderer closure. No profile upload or controls.
const TAG = "sds200-mimic-card";
const layouts = [{value: "preferred", label: "Profile preference"}, {value: "simple", label: "Simple"}, {value: "detail", label: "Detail"}];
const treatments = [{value: "strips", label: "Top and bottom strips"}, {value: "border", label: "Surrounding border"}];
const densities = [{value: "compact", label: "Compact"}, {value: "standard", label: "Standard"}, {value: "tall", label: "Tall"}];
const FRONT_PANEL_VERSION = 1;
const FRONT_PANEL_CODES = Object.freeze([
  "M", "F", "L", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
  ".", "E", ">", "<", "^", "V", "Q", "Y", "A", "B", "C", "Z", "T", "R",
]);
function frontPanelText(value) {
  return typeof value === "string" && value.length > 0 && value.length <= 256 && /^[\x20-\x7e]+$/.test(value);
}
function decodeFrontPanel(payload) {
  require(keys(payload, ["protocol", "version", "front_panel"]) && payload.protocol === "sdsctl.web" && payload.version === 1);
  const value = payload.front_panel;
  require(keys(value, ["version", "controls_available", "keys"]) &&
    value.version === FRONT_PANEL_VERSION && typeof value.controls_available === "boolean" &&
    Array.isArray(value.keys) && value.keys.length === FRONT_PANEL_CODES.length);
  const fields = ["code", "label", "context_note", "reference_status", "control_status", "available", "unavailable_reason"];
  value.keys.forEach((entry, index) => {
    require(keys(entry, fields) && entry.code === FRONT_PANEL_CODES[index] &&
      frontPanelText(entry.label) && frontPanelText(entry.context_note) && frontPanelText(entry.unavailable_reason) &&
      ["listed", "absent_for_model", "model_not_listed"].includes(entry.reference_status) &&
      typeof entry.available === "boolean");
    const qualifiedMenu = entry.code === "M" && entry.reference_status === "model_not_listed" &&
      entry.control_status === "qualified" && entry.available === true;
    require(qualifiedMenu || (entry.available === false &&
      entry.control_status === (entry.reference_status === "absent_for_model" ? "unsupported" : "unqualified")));
  });
  require(value.controls_available === value.keys.some(entry => entry.available));
  return value;
}
function configValue(input) {
  const allowed = ["type", "title", "layout", "led_treatment", "density", "show_details", "grid_options"];
  if (input === null || typeof input !== "object" || Array.isArray(input) || Object.keys(input).some(key => !allowed.includes(key))) throw new Error("Unsupported Mimic-SDS card option.");
  const value = {title: "Mimic-SDS", layout: "preferred", led_treatment: "strips", density: "standard", show_details: true, ...input};
  if (value.type !== undefined && value.type !== `custom:${TAG}` || !text(value.title, 128) || typeof value.show_details !== "boolean" ||
      !layouts.some(item => item.value === value.layout) || !treatments.some(item => item.value === value.led_treatment) || !densities.some(item => item.value === value.density)) throw new Error("Invalid Mimic-SDS card configuration.");
  const grid = value.grid_options ?? {};
  if (typeof grid !== "object" || Array.isArray(grid) || Object.keys(grid).some(key => !["rows", "columns"].includes(key)) ||
      grid.rows !== undefined && grid.rows !== "auto" && !(Number.isInteger(grid.rows) && grid.rows >= 1 && grid.rows <= 100) ||
      grid.columns !== undefined && grid.columns !== "full" && !(Number.isInteger(grid.columns) && grid.columns >= 1 && grid.columns <= 12)) throw new Error("Invalid Mimic-SDS grid options.");
  return Object.freeze({...value, grid_options: Object.freeze({...grid})});
}
function context(target, name, callback, subscribe = false) {
  const event = new CustomEvent("context-request", {bubbles: true, composed: true, cancelable: true});
  event.context = name; event.subscribe = subscribe; event.callback = callback;
  target.dispatchEvent(event);
}
class Sds200MimicCard extends HTMLElement {
  static getStubConfig() { return {layout: "preferred", led_treatment: "strips", density: "standard", show_details: true}; }
  static getConfigForm() {
    return {
      schema: [{name: "title", selector: {text: {}}},
        {name: "layout", selector: {select: {options: layouts}}},
        {name: "led_treatment", selector: {select: {options: treatments}}},
        {name: "density", selector: {select: {options: densities}}},
        {name: "show_details", selector: {boolean: {}}}],
      computeLabel: item => ({title: "Title", layout: "Scanner layout", led_treatment: "Alert LED treatment", density: "Card height", show_details: "Show profile and field details"})[item.name],
      computeHelper: item => ({layout: "A local presentation choice; does not change the physical scanner.", led_treatment: "Reported LED color only; no inferred blink timing.", density: "Intrinsic height for automatic rows. Explicit dashboard rows control fixed height."})[item.name],
      assertConfig: configValue,
    };
  }
  constructor({supplemental = false, supplementalDemand = false} = {}) {
    super();
    // Internal candidate only. Normal HA construction/config never selects it.
    require(typeof supplemental === "boolean");
    require(typeof supplementalDemand === "boolean" && (!supplementalDemand || supplemental));
    this._supplementalDemand = supplementalDemand; this._renewalPending = false;
    this._supplemental = supplemental; this._auxGuard = null; this._needNegotiation = true;
    this._terminal = false; this._retiredConnections = new Set(); this._retiredProfiles = new Set();
    this._requestTimer = null; this._auxExpiry = null;
    this._frontPanelController = null; this._frontPanelTimer = null; this._frontPanelAttempted = false;
    this._config = configValue({});
    this._connected = false; this._visible = false; this._mount = 0; this._epoch = 0;
    this._api = null; this._ui = null; this._panelsKey = null; this._unsubscribe = null; this._observer = null;
    this._controller = null; this._timer = null; this._expiry = null;
    this._latest = null; this._deadline = null; this._sequenceDeadline = null;
    this._endpoint = null; this._identity = null; this._sequence = null; this._url = null;
    this._visibility = () => this._reconcile();
    this.attachShadow({mode: "open"});
    const css = make("style");
    css.textContent = `
      :host { display:block; min-width:0; }
      :host([data-fixed="true"]) { height:100%; min-height:0; }
      * { box-sizing:border-box; }
      ha-card { display:flex; flex-direction:column; gap:8px; padding:12px; min-width:0;
        background:var(--ha-card-background,var(--card-background-color,#111820)); color:var(--primary-text-color,#edf4fc); }
      :host([data-fixed="true"]) ha-card { height:100%; overflow:auto; }
      h2 { margin:0; font:500 20px/1.2 var(--paper-font-headline_-_font-family,system-ui); overflow-wrap:anywhere; }
      .mimic-status { margin:0; border-left:4px solid #f29b9b; padding:5px 8px; font:600 13px/1.3 system-ui; }
      ha-card[data-state="current"] .mimic-status { border-color:#37b88f; }
      .mimic-surround { --mimic-led:#3b4654; --mimic-led-width:3cqmin;
        height:360px; flex:0 0 auto; min-width:0; background:#000;
        position:relative; border:0; padding:0; container-type:size; }
      :host([data-density="compact"]) .mimic-surround { height:280px; }
      :host([data-density="tall"]) .mimic-surround { height:480px; }
      :host([data-fixed="true"]) .mimic-surround { flex:1 0 240px; height:0; }
      :host([data-led-treatment="border"]) .mimic-grid { border-color:var(--mimic-led); }
      /* Query this card's surrounding panel, reserving the same frame for either treatment. */
      .mimic-grid { display:grid; grid-template-columns:repeat(30,minmax(0,1fr)); grid-template-rows:repeat(20,minmax(0,1fr));
        position:absolute; inset:0; width:100%; height:100%; min-height:0; container-type:size;
        border:var(--mimic-led-width) solid transparent; border-block-color:var(--mimic-led); overflow:hidden; }
      .mimic-cell { min-width:0; min-height:0; padding:0 3px; overflow:hidden; display:flex; align-items:center;
        /* Leave ascent/descent room within each of the 20 scanner rows. */
        color:#cbd5e1; background:#18212d; font:clamp(9px,min(1.7cqw,4cqh),24px)/1 monospace; }
      .mimic-cell[data-alignment="center"] { justify-content:center; text-align:center; }
      .mimic-cell[data-alignment="left"] { justify-content:start; text-align:left; }
      .mimic-cell[data-region="signal"][data-indicator^="level_"] { padding:0; font-size:clamp(6px,min(1.2cqw,4.5cqh),24px); }
      .mimic-cell span { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; min-width:0; }
      .mimic-cell[data-kind="name"] span { width:100%; font:clamp(16px,min(3.7cqw,9cqh),48px)/1 monospace; }
      .mimic-cell[data-kind="name"][data-lines="2"] span { display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; white-space:normal; overflow-wrap:anywhere; }
      .mimic-empty { display:flex; align-items:center; justify-content:center; padding:12px; color:#cbd5e1; font:14px/1.4 system-ui; }
      details { font:12px/1.4 system-ui; overflow-wrap:anywhere; }
      pre { max-width:100%; margin:4px 0; white-space:pre-wrap; overflow-wrap:anywhere; font:12px/1.4 monospace; }
      summary { cursor:pointer; min-height:28px; }
      .front-panel { border-top:1px solid rgba(148,163,184,.35); padding-top:4px; }
      .front-panel-status { margin:4px 0 8px; }
      .front-panel-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr)); gap:6px; min-width:0; }
      .front-panel-key { appearance:none; width:100%; min-width:0; padding:7px; border:1px solid rgba(148,163,184,.4);
        border-radius:6px; background:rgba(148,163,184,.08); color:inherit; opacity:1;
        text-align:left; font:12px/1.35 system-ui; overflow-wrap:anywhere; }
      .front-panel-code { display:inline-block; min-width:3ch; font:700 13px/1.35 monospace; }
      .front-panel-label { font-weight:700; }
      .front-panel-meta, .front-panel-context, .front-panel-reason { display:block; margin-top:3px; }
      .front-panel-meta { font-family:monospace; }
      details p { margin:4px 0; } ul { padding-left:20px; } [hidden] { display:none !important; }
    `;
    this._card = make("ha-card"); this._title = make("h2", this._config.title);
    this._status = make("p", "Waiting for Home Assistant…", "mimic-status"); this._status.setAttribute("role", "status");
    this._surround = make("div", undefined, "mimic-surround");
    this._details = make("details"); this._details.append(make("summary", "Profile, LED and field details"));
    this._note = make("p"); this._fields = make("ul"); this._details.append(this._note, this._fields);
    this._auxNote = make("p"); this._favorites = make("pre");
    if (supplemental) this._details.append(this._auxNote, this._favorites);
    this._frontPanel = null; this._frontPanelStatus = null; this._frontPanelGrid = null;
    if (!supplemental) {
      this._frontPanel = make("details", undefined, "front-panel");
      this._frontPanel.append(make("summary", "Front panel (read-only inventory)"));
      this._frontPanelStatus = make("p", undefined, "front-panel-status");
      this._frontPanelStatus.id = "front-panel-status";
      this._frontPanelGrid = make("div", undefined, "front-panel-grid");
      this._frontPanelGrid.setAttribute("aria-label", "Scanner front-panel key inventory");
      this._frontPanel.append(this._frontPanelStatus, this._frontPanelGrid);
    }
    this._card.append(this._title, this._status, this._surround, this._details);
    if (this._frontPanel !== null) this._card.append(this._frontPanel);
    this.shadowRoot.append(css, this._card);
    this._resetFrontPanel("Front-panel inventory is unavailable; all controls remain disabled.");
    this._clear("Waiting for Home Assistant…");
  }
  setConfig(input) {
    this._config = configValue(input);
    this._title.textContent = this._config.title; this._title.hidden = this._config.title === "";
    this.dataset.density = this._config.density; this.dataset.ledTreatment = this._config.led_treatment;
    this.dataset.fixed = String(Number.isInteger(this._config.grid_options.rows));
    this._details.hidden = !this._config.show_details;
    this._render();
  }
  getCardSize() { return ({compact: 7, standard: 9, tall: 11})[this._config.density]; }
  getGridOptions() { return {rows: this.getCardSize(), columns: 12, min_rows: 6, min_columns: 6}; }
  connectedCallback() {
    if (this._connected) return;
    this._connected = true; const mount = ++this._mount;
    document.addEventListener("visibilitychange", this._visibility);
    if (typeof IntersectionObserver === "function") {
      this._observer = new IntersectionObserver(entries => {
        if (!this._connected || mount !== this._mount) return;
        this._visible = Boolean(entries.at(-1)?.isIntersecting); this._reconcile();
      });
      this._observer.observe(this);
    } else this._visible = true;
    context(this, "hassApi", api => {
      if (!this._connected || mount !== this._mount) return;
      if (api !== this._api) this._stop();
      this._api = api; this._reconcile();
    });
    context(this, "hassUi", (ui, unsubscribe) => {
      if (!this._connected || mount !== this._mount) { unsubscribe?.(); return; }
      if (typeof unsubscribe === "function" && unsubscribe !== this._unsubscribe) { this._unsubscribe?.(); this._unsubscribe = unsubscribe; }
      // Panel changes invalidate discovery, even while a polling lease is healthy.
      const panelsKey = JSON.stringify(ui?.panels);
      const changed = this._panelsKey !== panelsKey;
      this._panelsKey = panelsKey;
      this._ui = ui;
      if (changed) this._stop();
      this._reconcile();
    }, true);
    this._reconcile();
  }
  disconnectedCallback() {
    this._connected = false; this._mount++; this._visible = false;
    document.removeEventListener("visibilitychange", this._visibility);
    this._observer?.disconnect(); this._observer = null;
    this._unsubscribe?.(); this._unsubscribe = null; this._api = this._ui = null;
    this._stop();
  }
  _demanded() { return !this._terminal && this._connected && this._visible && !document.hidden && this._api !== null && this._ui !== null; }
  _clear(message) {
    this._latest = null; this._deadline = null;
    window.clearTimeout(this._expiry); this._expiry = null;
    window.clearTimeout(this._auxExpiry); this._auxExpiry = null;
    this._auxNote.textContent = ""; this._favorites.textContent = "";
    this._status.textContent = message; this._card.dataset.state = "unavailable";
    this._surround.replaceChildren(make("div", "No current scanner values are shown.", "mimic-grid mimic-empty"));
    this._surround.style.setProperty("--mimic-led", "#3b4654"); this._surround.dataset.led = "unknown";
    this._note.textContent = "Read-only scanner presentation. No scanner commands are sent."; this._fields.replaceChildren();
  }
  _resetFrontPanel(message) {
    this._frontPanelController?.abort(); this._frontPanelController = null;
    window.clearTimeout(this._frontPanelTimer); this._frontPanelTimer = null;
    this._frontPanelAttempted = false;
    if (this._frontPanelStatus !== null) this._frontPanelStatus.textContent = message;
    this._frontPanelGrid?.replaceChildren();
  }
  _renderFrontPanel(value) {
    const buttons = value.keys.map(entry => {
      const button = make("button", undefined, "front-panel-key");
      button.type = "button"; button.disabled = true;
      button.dataset.referenceStatus = entry.reference_status;
      button.dataset.controlStatus = entry.control_status;
      button.setAttribute("aria-describedby", "front-panel-status");
      button.title = entry.unavailable_reason;
      button.append(
        make("span", entry.code, "front-panel-code"),
        make("span", entry.label, "front-panel-label"),
        make("span", `reference: ${entry.reference_status}; control: ${entry.control_status}`, "front-panel-meta"),
        make("span", entry.context_note, "front-panel-context"),
        make("span", entry.unavailable_reason, "front-panel-reason"),
      );
      return button;
    });
    const unsupported = value.keys.filter(entry => entry.control_status === "unsupported").length;
    const qualified = value.keys.filter(entry => entry.available).length;
    this._frontPanelGrid.replaceChildren(...buttons);
    this._frontPanelStatus.textContent =
      `Inventory v${value.version} — ${value.keys.length} keys shown; ` +
      (qualified === 1 ? "Menu is qualified in the operator Web dashboard; this Home Assistant card remains read-only" : "all controls remain unavailable") +
      (unsupported > 0 ? ` (${unsupported} unsupported for this model).` : ".");
  }
  async _loadFrontPanel(displayUrl, route, parentSignal, epoch) {
    if (this._frontPanel === null || this._frontPanelAttempted) return;
    this._frontPanelAttempted = true;
    this._frontPanelStatus.textContent = "Checking front-panel inventory; all controls remain disabled.";
    const controller = new AbortController(); this._frontPanelController = controller;
    const abort = () => controller.abort();
    parentSignal.addEventListener("abort", abort, {once: true});
    const timeout = window.setTimeout(abort, 2000); this._frontPanelTimer = timeout;
    try {
      require(typeof displayUrl === "string" && displayUrl.endsWith(route));
      const url = displayUrl.slice(0, -route.length) + "api/v1/scanner/front-panel";
      const response = await fetch(url, {headers: {Accept: "application/json"}, signal: controller.signal,
        credentials: "same-origin", cache: "no-store", redirect: "error"});
      if (epoch !== this._epoch || !this._demanded() || parentSignal.aborted || controller.signal.aborted) return;
      const value = await readResponse(response, () => {}, () => {}, decodeFrontPanel, 64 * 1024);
      if (epoch !== this._epoch || !this._demanded() || parentSignal.aborted || controller.signal.aborted) return;
      require(this._frontPanelController === controller); this._renderFrontPanel(value);
    } catch {
      if (epoch === this._epoch && this._demanded() && this._frontPanelController === controller) {
        this._frontPanelGrid.replaceChildren();
        this._frontPanelStatus.textContent = "Front-panel inventory is unavailable; all controls remain disabled.";
      }
    } finally {
      parentSignal.removeEventListener("abort", abort);
      window.clearTimeout(timeout);
      if (this._frontPanelTimer === timeout) this._frontPanelTimer = null;
      if (this._frontPanelController === controller) this._frontPanelController = null;
    }
  }
  _render() {
    if (this._latest === null) return;
    if (this._deadline !== null && performance.now() >= this._deadline) { this._clear(states.stale); return; }
    const data = this._latest;
    let frame = data.frames[this._config.layout];
    if (this._auxGuard !== null) {
      const values = this._auxGuard.snapshot(performance.now() / 1000);
      frame = sdsctlCardSupplemental.present(frame, values, contract.supplemental_clock_regions);
      this._auxNote.textContent = `Scanner-local clock: ${values.clock.status}. Global Favorites quick keys (00–99, not LCD F0/S0/D0): ${values.favorites.status}.`;
      this._favorites.textContent = sdsctlCardSupplemental.favoritesRows(values).join("\n");
      window.clearTimeout(this._auxExpiry); this._auxExpiry = null;
      const remaining = Object.values(values).filter(value => value.status === "current").map(value => (5 - value.age_seconds) * 1000);
      if (remaining.length) this._auxExpiry = window.setTimeout(() => this._render(), Math.max(1, Math.min(...remaining)));
    }
    this._status.textContent = states[frame.status]; this._card.dataset.state = frame.status;
    draw(this._surround, frame);
    const color = frame.indicators.alert_led;
    this._surround.style.setProperty("--mimic-led", `#${contract.leds[color] ?? "3b4654"}`); this._surround.dataset.led = color ?? "unknown";
    const basis = layoutBasisText(frame);
    this._note.textContent = `${basis} Alert LED: ${color ?? "unavailable"}; reported color only, blink timing is not reproduced. Profile: ${frame.profile_status}; source: ${data.source_status ?? "unavailable"}${frame.profile_refresh_pending ? "; refresh pending" : ""}. Revision: ${frame.profile_revision ?? "none"}. Source notation is preserved. Neutral colors indicate missing/unqualified mapping; BLACK/WHITE transforms and icon glyphs are not yet qualified.`;
    this._fields.replaceChildren(...(frame.screen?.regions ?? []).filter(region => !["empty", "blank"].includes(region.value_status)).map(region => make("li", `${region.token ?? region.id}: ${region.value_status}`)));
  }
  _accept(data, started) {
    require(this._endpoint === null || this._endpoint === data.endpoint_id);
    const identity = `${data.stream_id}/${data.session_id}`, incoming = data.frames.preferred.sequence;
    require(identity !== this._identity || incoming === null || this._sequence === null || incoming >= this._sequence);
    let deadline = incoming === null ? null : started + (5 - data.frames.preferred.age_seconds) * 1000;
    if (identity === this._identity && incoming === this._sequence && deadline !== null && this._sequenceDeadline !== null) deadline = Math.min(deadline, this._sequenceDeadline);
    this._endpoint = data.endpoint_id;
    if (identity !== this._identity) { this._sequence = null; this._sequenceDeadline = null; }
    this._identity = identity;
    if (incoming !== null) { this._sequence = incoming; this._sequenceDeadline = deadline; }
    this._latest = data; this._deadline = data.frames.preferred.status === "current" ? this._sequenceDeadline : null;
    window.clearTimeout(this._expiry);
    if (this._deadline !== null) this._expiry = window.setTimeout(() => this._clear(states.stale), Math.max(0, this._deadline - performance.now()));
    if (data.failure !== null) this._clear("Display configuration unavailable — administrator review required.");
    else this._render();
  }
  _stop() {
    const uncertain = this._renewalPending;
    if (uncertain) { this._terminal = true; this._renewalPending = false; this._auxGuard?.close(); }
    this._epoch++; this._controller?.abort(); this._controller = null;
    window.clearTimeout(this._requestTimer); this._requestTimer = null;
    this._resetFrontPanel("Front-panel inventory is unavailable; all controls remain disabled.");
    this._auxGuard?.suspend(); this._needNegotiation = true;
    window.clearTimeout(this._timer); this._timer = null;
    this._clear(uncertain ? "Supplemental demand unconfirmed — renewal stopped; reads may have occurred."
      : "Mimic-SDS inactive — values cleared.");
  }
  _reconcile() {
    if (!this._demanded()) { this._stop(); return; }
    if (this._controller === null && this._timer === null) void this._start();
  }
  _endSupplemental() {
    this._terminal = true; this._auxGuard?.close(); this._stop();
    this._clear("Display access or context ended — reopen through an authorized Home Assistant session.");
  }
  _bindSupplemental(binding) {
    const old = this._auxGuard?.context;
    if (old && same(old, binding)) return;
    const identity = value => `${value.stream_id}/${value.session_id}`;
    const changed = old && identity(old) !== identity(binding);
    const epoch = old && (binding.context_revision > old.context_revision || binding.profile_invalidation > old.profile_invalidation);
    const retired = this._retiredConnections.has(identity(binding)) || old && (
      binding.endpoint_id !== old.endpoint_id || !changed && (
        binding.context_revision < old.context_revision || binding.profile_invalidation < old.profile_invalidation ||
        !epoch && this._retiredProfiles.has(binding.profile_revision)));
    const overflow = old && (changed ? this._retiredConnections.size >= 64 : !epoch && this._retiredProfiles.size >= 64);
    if (retired || overflow) { this._endSupplemental(); return; }
    const replacement = sdsctlCardSupplemental.create(binding);
    if (changed) this._retiredConnections.add(identity(old));
    if (changed || epoch) this._retiredProfiles.clear();
    else if (old) this._retiredProfiles.add(old.profile_revision);
    this._auxGuard?.close(); this._auxGuard = replacement;
    this._identity = this._sequence = this._sequenceDeadline = null;
    this._clear("New display context verified — waiting for current scanner data…");
  }
  async _start() {
    if (!this._demanded()) return;
    const epoch = this._epoch, controller = new AbortController(); this._controller = controller;
    let release = null;
    try {
      this._clear("Connecting through Home Assistant…");
      release = await sdsctlCardIngress.acquire(this._api, controller.signal);
      if (epoch !== this._epoch || !this._demanded() || controller.signal.aborted) return;
      const route = this._supplemental ? "api/v1/display-supplemental/context" : "api/v1/display-frame";
      const url = await sdsctlCardIngress.resolve(this._api, this._ui, route, controller.signal);
      if (epoch !== this._epoch || !this._demanded() || controller.signal.aborted) return;
      if (this._url !== url) {
        // Supplemental endpoint pin/history belongs to the card instance, not
        // its transient Ingress key. Negotiation must authorize every new cut.
        if (!this._supplemental) this._endpoint = this._identity = this._sequence = this._sequenceDeadline = null;
        this._needNegotiation = true; this._url = url;
      }
      const poll = async () => {
        if (epoch !== this._epoch || !this._demanded()) return;
        let started = performance.now();
        const cycleStarted = started;
        const timeout = window.setTimeout(() => {
          controller.abort();
          if (this._renewalPending && this._controller === controller) this._stop();
        }, this._supplemental ? 5000 : 2000);
        this._requestTimer = timeout;
        try {
          const current = () => epoch === this._epoch && this._demanded() && !controller.signal.aborted;
          const admitted = response => {
            if (![401, 403].includes(response.status)) return true;
            sdsctlCardIngress.invalidate();
            if (!this._supplemental) return true;
            this._endSupplemental(); return false;
          };
          const options = {headers: {Accept: "application/json"}, signal: controller.signal, credentials: "same-origin", cache: "no-store", redirect: "error"};
          if (this._supplemental && this._needNegotiation) {
            const response = await fetch(url, {...options, headers: {...options.headers, "X-SDSCTL-Supplemental-Version": "1"}});
            if (epoch !== this._epoch || !this._demanded()) return;
            require(current());
            if (!admitted(response)) return;
            const binding = await readResponse(response, () => {}, () => {}, sdsctlCardSupplemental.contextResponse, 2048);
            if (epoch !== this._epoch || !this._demanded()) return;
            require(current()); this._bindSupplemental(binding);
            if (this._terminal) return;
            this._needNegotiation = false;
          }
          if (this._supplementalDemand) {
            const binding = this._auxGuard.context;
            const nonce = sdsctlCardSupplemental.renewalId(Array.from(crypto.getRandomValues(new Uint8Array(16))));
            this._renewalPending = true;
            const response = await fetch(new URL("demand", url).href, {...options, method: "POST", headers: {
              ...options.headers, "X-SDSCTL-Supplemental-Version": "1",
              "X-SDSCTL-Supplemental-Context": JSON.stringify(binding), "X-SDSCTL-Supplemental-Renewal": nonce}});
            if (epoch !== this._epoch || !this._demanded()) return;
            require(current() && performance.now() - cycleStarted < 5000);
            if (response.status === 409) { this._renewalPending = false; throw new Error("Context changed."); }
            if (!admitted(response)) return;
            const renewed = await readResponse(response, () => {}, () => {},
              value => sdsctlCardSupplemental.demandResponse(value, binding, nonce), 2048);
            if (epoch !== this._epoch || !this._demanded()) return;
            require(current() && performance.now() - cycleStarted < 5000);
            this._renewalPending = false; this._bindSupplemental(renewed);
            if (this._terminal) return;
          }
          started = performance.now();
          const ticket = this._auxGuard?.begin(started / 1000);
          if (this._supplemental) options.headers = {...options.headers,
            "X-SDSCTL-Supplemental-Version": "1", "X-SDSCTL-Supplemental-Context": JSON.stringify(this._auxGuard.context)};
          const frameUrl = this._supplemental ? new URL("frame", url).href : url;
          const response = await fetch(frameUrl, options);
          if (epoch !== this._epoch || !this._demanded()) return;
          require(current());
          if (!admitted(response)) return;
          const decoder = this._supplemental ? payload => sdsctlCardSupplemental.bundle(payload, decode, this._auxGuard.context) : decode;
          const decoded = await readResponse(response, () => {}, () => {}, decoder);
          if (epoch !== this._epoch || !this._demanded()) return;
          require(current());
          if (this._supplemental) require(this._auxGuard.accept(ticket, decoded.supplemental, performance.now() / 1000));
          const data = this._supplemental ? decoded.display : decoded;
          this._accept(data, started);
          if (!this._supplemental) void this._loadFrontPanel(url, route, controller.signal, epoch);
          this._timer = window.setTimeout(() => { this._timer = null; void poll(); }, 250);
        } catch { if (this._renewalPending) this._stop(); else failed(); }
        finally {
          if (this._renewalPending && this._controller === controller) this._stop();
          window.clearTimeout(timeout); if (this._requestTimer === timeout) this._requestTimer = null;
        }
      };
      const failed = () => {
        if (epoch !== this._epoch) return;
        this._controller = null; controller.abort(); release?.();
        this._resetFrontPanel("Front-panel inventory is unavailable; all controls remain disabled.");
        this._auxGuard?.suspend(); this._needNegotiation = true;
        this._clear("Mimic-SDS data unavailable — retrying safely.");
        if (this._demanded()) this._timer = window.setTimeout(() => { this._timer = null; void this._start(); }, 2000);
      };
      await poll();
      // Successful poll retains its lease until abort or error; no hidden session.
    } catch {
      if (epoch !== this._epoch) return;
      controller.abort(); release?.(); this._controller = null;
      this._resetFrontPanel("Front-panel inventory is unavailable; all controls remain disabled.");
      this._auxGuard?.suspend(); this._needNegotiation = true;
      this._clear("Home Assistant App unavailable or ambiguous — retrying safely.");
      if (this._demanded()) this._timer = window.setTimeout(() => { this._timer = null; void this._start(); }, 2000);
    } finally {
      if (epoch !== this._epoch || !this._demanded()) { controller.abort(); release?.(); }
    }
  }
}
if (!customElements.get(TAG)) customElements.define(TAG, Sds200MimicCard);
window.customCards = window.customCards || [];
if (!window.customCards.some(card => card.type === TAG)) window.customCards.push({type: TAG, name: "Mimic-SDS", description: "Read-only scanner profile layout through the sdsctl App.", preview: true});
