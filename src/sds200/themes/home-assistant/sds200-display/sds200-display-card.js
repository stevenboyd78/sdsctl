const SDS200_DISPLAY_CARD_TYPE = "sds200-display-card";
const SDS200_DISPLAY_CARD_TAG = "sds200-display-card";

const SDS200_DISPLAY_LAYOUTS = Object.freeze([
  Object.freeze({ value: "simple", label: "Simple" }),
  Object.freeze({ value: "detail", label: "Detail" }),
  Object.freeze({ value: "search", label: "Search / Close Call" }),
  Object.freeze({ value: "weather", label: "Weather" }),
  Object.freeze({ value: "tone_out", label: "Tone-Out" }),
  Object.freeze({ value: "auto", label: "Auto" }),
]);

const SDS200_DISPLAY_SCAN_LAYOUTS = Object.freeze([
  Object.freeze({ value: "simple", label: "Simple" }),
  Object.freeze({ value: "detail", label: "Detail" }),
]);

const SDS200_DISPLAY_SYSTEM_PALETTES = Object.freeze({
  "ansi-dark": Object.freeze(["#0c0c0c", "#1a1a1a", "#242424", "#cccccc", "#858585", "#3b8eea", "#3b8eea", "#29b8db", "#e5e510", "#e74856", "#16c60c", "#16c60c"]),
  "ansi-light": Object.freeze(["#f5f5f5", "#ffffff", "#e5e5e5", "#0c0c0c", "#767676", "#0037da", "#0037da", "#3a96dd", "#e74856", "#c50f1f", "#13a10e", "#881798"]),
  "atom-one-dark": Object.freeze(["#282C34", "#3B414D", "#4F5666", "#ABB2BF", "#ABB2BF99", "#61AFEF", "#61AFEF", "#C678DD", "#DDB25B", "#EF6262", "#62F062", "#A378C2"]),
  "atom-one-light": Object.freeze(["#FAFAFA", "#E0E0E0", "#CCCCCC", "#383A42", "#383A4299", "#4078F2", "#4078F2", "#A626A4", "#D7D938", "#F13F3F", "#6BF23F", "#BE9232"]),
  "catppuccin-frappe": Object.freeze(["#303446", "#414559", "#51576D", "#C6D0F5", "#C6D0F599", "#BABBF1", "#CA9EE6", "#EE9F76", "#E4C890", "#E68284", "#A6D189", "#F4B8E4"]),
  "catppuccin-latte": Object.freeze(["#EFF1F5", "#E6E9EF", "#CCD0DA", "#4C4F69", "#4C4F6999", "#8839EF", "#8839EF", "#DB8A78", "#DE8E1D", "#D10F39", "#40A02B", "#FD640B"]),
  "catppuccin-macchiato": Object.freeze(["#24273A", "#363A4F", "#494D64", "#CAD3F5", "#CAD3F599", "#B7BDF8", "#C6A0F6", "#F4A97F", "#EED49F", "#ED8796", "#A6DA95", "#F5BDE6"]),
  "catppuccin-mocha": Object.freeze(["#181825", "#313244", "#45475A", "#CDD6F4", "#CDD6F499", "#b4befe", "#F5C2E7", "#CBA6F7", "#FAE3B0", "#F28FAD", "#ABE9B3", "#F9B387"]),
  dracula: Object.freeze(["#282A36", "#2B2E3B", "#313442", "#F8F8F2", "#F8F8F299", "#BD93F9", "#BD93F9", "#6272A4", "#FEB86C", "#FE5555", "#50FA7B", "#FF79C6"]),
  flexoki: Object.freeze(["#100F0F", "#1C1B1A", "#282726", "#FFFCF0", "#FFFCF099", "#205EA6", "#205EA6", "#24837B", "#AC8301", "#AE3029", "#65800B", "#9B76C8"]),
  gruvbox: Object.freeze(["#282828", "#3C3836", "#504945", "#FBF1C7", "#FBF1C799", "#85A598", "#85A598", "#A89A85", "#FD8019", "#FA4934", "#B7BB26", "#F9BD2F"]),
  monokai: Object.freeze(["#272822", "#2E2E2E", "#3E3D32", "#D6D6D6", "#797979", "#AE81FF", "#AE81FF", "#F82672", "#FC971F", "#F82672", "#A5E22E", "#66D9EF"]),
  nord: Object.freeze(["#2E3440", "#3B4252", "#434C5E", "#D8DEE9", "#D8DEE999", "#88C0D0", "#88C0D0", "#81A1C1", "#EACB8B", "#BE616A", "#A3BE8C", "#B48EAD"]),
  "rose-pine": Object.freeze(["#191724", "#1F1D2E", "#26233A", "#E0DEF4", "#E0DEF499", "#524f67", "#C4A7E7", "#31748F", "#F5C177", "#EA6F92", "#9CCFD8", "#EBBCBA"]),
  "rose-pine-dawn": Object.freeze(["#FAF4ED", "#FFFAF3", "#F2E9E1", "#575279", "#57527999", "#cecacd", "#907AA9", "#286983", "#E99D34", "#B4637A", "#56949F", "#D6827E"]),
  "rose-pine-moon": Object.freeze(["#232136", "#2A273F", "#393552", "#E0DEF4", "#E0DEF499", "#56526e", "#C4A7E7", "#3E8FB0", "#F5C177", "#EA6F92", "#9CCFD8", "#EA9A97"]),
  "solarized-dark": Object.freeze(["#002B36", "#073642", "#073642", "#839496", "#83949699", "#268BD2", "#268BD2", "#2AA198", "#CA4B16", "#DB322F", "#849900", "#6C71C4"]),
  "solarized-light": Object.freeze(["#FDF6E3", "#EEE8D5", "#EEE8D5", "#586E75", "#586E7599", "#268BD2", "#268BD2", "#2AA198", "#CA4B16", "#DB322F", "#849900", "#6C71C4"]),
  "textual-dark": Object.freeze(["#121212", "#1E1E1E", "#242F38", "#E0E0E0", "#E0E0E099", "#0178D4", "#0178D4", "#004578", "#FEA62B", "#B93C5B", "#4EBF71", "#FEA62B"]),
  "textual-light": Object.freeze(["#E0E0E0", "#D8D8D8", "#D0D0D0", "#1F1F1F", "#1F1F1F99", "#004578", "#004578", "#0178D4", "#FEA62B", "#B93C5B", "#4EBF71", "#FEA62B"]),
  "tokyo-night": Object.freeze(["#1A1B26", "#24283B", "#414868", "#A9B1D6", "#A9B1D699", "#BB9AF7", "#BB9AF7", "#7AA2F7", "#DFAF68", "#F6768E", "#9ECE6A", "#FE9E64"]),
});

const SDS200_DISPLAY_PALETTES = Object.freeze([
  Object.freeze({ value: "color", label: "Color" }),
  Object.freeze({ value: "black_on_white", label: "Black on White" }),
  Object.freeze({ value: "white_on_black", label: "White on Black" }),
  ...Object.keys(SDS200_DISPLAY_SYSTEM_PALETTES).map((value) =>
    Object.freeze({ value, label: value }),
  ),
]);

const SDS200_DISPLAY_FITS = Object.freeze([
  Object.freeze({ value: "card", label: "Card" }),
  Object.freeze({ value: "viewport", label: "Viewport" }),
]);

const SDS200_DISPLAY_ENTITY_FIELDS = Object.freeze([
  Object.freeze({
    key: "scanner_connected",
    label: "Scanner connection",
    domain: "binary_sensor",
  }),
  Object.freeze({
    key: "screen_kind",
    label: "Screen kind",
    domain: "sensor",
  }),
  Object.freeze({ key: "system", label: "System", domain: "sensor" }),
  Object.freeze({
    key: "department",
    label: "Department",
    domain: "sensor",
  }),
  Object.freeze({ key: "site", label: "Site", domain: "sensor" }),
  Object.freeze({ key: "channel", label: "Channel", domain: "sensor" }),
  Object.freeze({
    key: "frequency",
    label: "Frequency",
    domain: "sensor",
  }),
  Object.freeze({
    key: "modulation",
    label: "Modulation",
    domain: "sensor",
  }),
  Object.freeze({
    key: "service_type",
    label: "Service type",
    domain: "sensor",
  }),
  Object.freeze({
    key: "tone_out_tone_a",
    label: "Tone-Out Tone A",
    domain: "sensor",
  }),
  Object.freeze({
    key: "tone_out_tone_b",
    label: "Tone-Out Tone B",
    domain: "sensor",
  }),
  Object.freeze({ key: "signal", label: "Signal", domain: "sensor" }),
  Object.freeze({ key: "rssi", label: "RSSI", domain: "sensor" }),
  Object.freeze({
    key: "audio_running",
    label: "Audio",
    domain: "binary_sensor",
  }),
  Object.freeze({
    key: "recording_active",
    label: "Recording",
    domain: "binary_sensor",
  }),
  Object.freeze({
    key: "recording_status",
    label: "Recording status",
    domain: "sensor",
  }),
  Object.freeze({
    key: "daemon_state",
    label: "Daemon state",
    domain: "sensor",
  }),
]);

function optionLabel(options, value) {
  return options.find((option) => option.value === value)?.label;
}

function applyDisplaySystemPalette(element, palette) {
  const colors = SDS200_DISPLAY_SYSTEM_PALETTES[palette];
  if (colors === undefined) {
    return;
  }
  const [
    background, surface, panel, foreground, muted, border,
    primary, secondary, warning, error, success, accent,
  ] = colors;
  const properties = {
    "--frame-bg": background,
    "--frame-fg": foreground,
    "--frame-muted": muted,
    "--frame-line": border,
    "--top-bg": surface,
    "--system-bg": surface,
    "--department-bg": panel,
    "--channel-bg": background,
    "--detail-bg": panel,
    "--active": success,
    "--inactive": muted,
    "--sds200-display-primary": primary,
    "--sds200-display-secondary": secondary,
    "--sds200-display-warning": warning,
    "--sds200-display-error": error,
    "--sds200-display-accent": accent,
  };
  Object.entries(properties).forEach(([name, value]) => {
    element.style.setProperty(name, value);
  });
}

function fieldForName(name) {
  return SDS200_DISPLAY_ENTITY_FIELDS.find(
    (field) => field.key === name,
  );
}

function toneOutDisplay(value) {
  const match = /^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:hz)?$/i.exec(
    value.trim(),
  );
  return match !== null && Number(match[1]) === 0 ? "Detect" : value;
}

function requireDisplayCardConfig(config) {
  if (
    config === null ||
    typeof config !== "object" ||
    Array.isArray(config)
  ) {
    throw new Error(
      "SDS200 display card configuration must be an object.",
    );
  }

  if (
    config.title !== undefined &&
    typeof config.title !== "string"
  ) {
    throw new Error(
      "SDS200 display card title must be text.",
    );
  }

  const title =
    typeof config.title === "string" && config.title.trim()
      ? config.title.trim()
      : "SDS200 Display";
  const layout = config.layout ?? "simple";
  const scanLayout = config.scan_layout ?? "detail";
  const palette = config.palette ?? "color";
  const fit = config.fit ?? "card";

  if (!optionLabel(SDS200_DISPLAY_LAYOUTS, layout)) {
    throw new Error(
      `SDS200 display card layout "${layout}" is not supported.`,
    );
  }
  if (!optionLabel(SDS200_DISPLAY_SCAN_LAYOUTS, scanLayout)) {
    throw new Error(
      `SDS200 display card scan layout "${scanLayout}" is not supported.`,
    );
  }
  if (!optionLabel(SDS200_DISPLAY_PALETTES, palette)) {
    throw new Error(
      `SDS200 display card palette "${palette}" is not supported.`,
    );
  }
  if (!optionLabel(SDS200_DISPLAY_FITS, fit)) {
    throw new Error(
      `SDS200 display card fit "${fit}" is not supported.`,
    );
  }

  const source = config.entities ?? {};

  if (
    source === null ||
    typeof source !== "object" ||
    Array.isArray(source)
  ) {
    throw new Error(
      "SDS200 display card entities must be an object.",
    );
  }

  const entities = {};
  const supportedFields = new Set(
    SDS200_DISPLAY_ENTITY_FIELDS.map(({ key }) => key),
  );

  for (const key of Object.keys(source)) {
    if (!supportedFields.has(key)) {
      throw new Error(
        `SDS200 display card entity field "${key}" is not supported.`,
      );
    }
  }

  for (const { key, domain } of SDS200_DISPLAY_ENTITY_FIELDS) {
    const value = source[key];

    if (
      value === undefined ||
      value === null ||
      value === ""
    ) {
      continue;
    }

    if (
      typeof value !== "string" ||
      value.trim() !== value ||
      !/^[a-z0-9_]+\.[a-z0-9_]+$/.test(value)
    ) {
      throw new Error(
        `SDS200 display card entity "${key}" must be a Home Assistant entity ID.`,
      );
    }

    if (!value.startsWith(`${domain}.`)) {
      throw new Error(
        `SDS200 display card entity "${key}" must be in the ${domain} domain.`,
      );
    }

    entities[key] = value;
  }

  return Object.freeze({
    title,
    layout,
    scan_layout: scanLayout,
    palette,
    fit,
    entities: Object.freeze(entities),
  });
}

function textElement(documentObject, tagName, className, text) {
  const node = documentObject.createElement(tagName);
  node.className = className;
  node.textContent = text;
  return node;
}

class Sds200DisplayCard extends HTMLElement {
  static getStubConfig() {
    return {
      layout: "simple",
      scan_layout: "detail",
      palette: "color",
      fit: "card",
      entities: {},
    };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        {
          name: "layout",
          required: true,
          selector: { select: { options: SDS200_DISPLAY_LAYOUTS } },
        },
        {
          name: "scan_layout",
          required: true,
          selector: { select: { options: SDS200_DISPLAY_SCAN_LAYOUTS } },
        },
        {
          name: "palette",
          required: true,
          selector: { select: { options: SDS200_DISPLAY_PALETTES } },
        },
        {
          name: "fit",
          required: true,
          selector: { select: { options: SDS200_DISPLAY_FITS } },
        },
        {
          type: "expandable",
          name: "entities",
          title: "SDS200 entities",
          schema: SDS200_DISPLAY_ENTITY_FIELDS.map(
            ({ key, domain }) => ({
              name: key,
              selector: {
                entity: {
                  filter: [{ domain }],
                },
              },
            }),
          ),
        },
      ],
      computeLabel: (schema) => {
        const labels = {
          title: "Title",
          layout: "Display layout",
          scan_layout: "Automatic scan layout",
          palette: "Display palette",
          fit: "Fit",
        };
        return labels[schema.name] ?? fieldForName(schema.name)?.label;
      },
      computeHelper: (schema) => {
        const helpers = {
          layout: "Choose a scanner-inspired information layout.",
          scan_layout: (
            "Choose Simple or Detail for scanning and automatic fallback."
          ),
          palette: (
            "Choose a scanner palette or one System web palette."
          ),
          fit: "Viewport fit grows without exceeding the visible screen.",
          entities: (
            "Select the entities created by the SDS200 MQTT " +
            "Discovery device."
          ),
        };
        return helpers[schema.name];
      },
      assertConfig: (config) => {
        requireDisplayCardConfig(config);
      },
    };
  }

  constructor() {
    super();
    this._config = null;
    this._states = {};
    this._unsubscribe = null;
    this._updateStates = this._updateStates.bind(this);
    this.attachShadow({ mode: "open" });
  }

  connectedCallback() {
    if (this._unsubscribe !== null) {
      return;
    }

    const event = new CustomEvent("context-request", {
      bubbles: true,
      composed: true,
      cancelable: true,
    });

    event.context = "states";
    event.subscribe = true;
    event.callback = this._updateStates;
    this.dispatchEvent(event);
    this._render();
  }

  disconnectedCallback() {
    if (typeof this._unsubscribe === "function") {
      this._unsubscribe();
    }
    this._unsubscribe = null;
  }

  setConfig(config) {
    this._config = requireDisplayCardConfig(config);
    this._render();
  }

  getCardSize() {
    return 6;
  }

  getGridOptions() {
    return {
      rows: 6,
      columns: 12,
      min_rows: 4,
      min_columns: 4,
    };
  }

  _updateStates(states, unsubscribe) {
    this._states =
      states !== null && typeof states === "object"
        ? states
        : {};
    this._unsubscribe =
      typeof unsubscribe === "function"
        ? unsubscribe
        : null;
    this._render();
  }

  _state(field) {
    const entityId = this._config?.entities[field];
    return entityId ? this._states[entityId] ?? null : null;
  }

  _stateText(field, fallback = "—") {
    const value = this._state(field)?.state;

    if (
      typeof value !== "string" ||
      value === "" ||
      value === "unknown" ||
      value === "unavailable"
    ) {
      return fallback;
    }
    return ["tone_out_tone_a", "tone_out_tone_b"].includes(field)
      ? toneOutDisplay(value)
      : value;
  }

  _binaryActive(field) {
    const value = this._stateText(field, "").toLowerCase();
    return value === "on" || value === "true" || value === "running";
  }

  _resolvedLayout() {
    if (this._config.layout !== "auto") {
      return this._config.layout;
    }

    const screenKind = this._stateText("screen_kind", "unknown").toLowerCase();
    const automaticLayouts = {
      search: "search",
      close_call: "search",
      weather: "weather",
      tone_out: "tone_out",
    };
    return automaticLayouts[screenKind] ?? this._config.scan_layout;
  }

  _cell(documentObject, label, field, options = {}) {
    const cell = documentObject.createElement("div");
    cell.className = `display-cell ${options.className ?? ""}`.trim();
    cell.dataset.field = field;

    const value =
      options.value ?? this._stateText(field, options.fallback ?? "—");
    cell.setAttribute("aria-label", `${label}: ${value}`);

    cell.append(
      textElement(documentObject, "span", "cell-label", label),
      textElement(documentObject, "span", "cell-value", value),
    );
    return cell;
  }

  _hierarchyPanel(documentObject, label, field, secondary) {
    const panel = documentObject.createElement("section");
    panel.className = `hierarchy-panel hierarchy-${field}`;
    panel.setAttribute("aria-label", label);
    panel.append(
      textElement(documentObject, "span", "hierarchy-label", label),
      textElement(
        documentObject,
        "strong",
        "hierarchy-value",
        this._stateText(field),
      ),
    );

    const secondaryRow = documentObject.createElement("div");
    secondaryRow.className = "hierarchy-secondary";
    for (const [secondaryLabel, secondaryField] of secondary) {
      secondaryRow.append(
        this._cell(
          documentObject,
          secondaryLabel,
          secondaryField,
          { className: "secondary-cell" },
        ),
      );
    }
    panel.append(secondaryRow);
    return panel;
  }

  _renderTop(documentObject) {
    const top = documentObject.createElement("header");
    top.className = "top-grid";
    const connected = this._binaryActive("scanner_connected");

    for (const [label, field, value, active] of [
      ["Link", "scanner_connected", connected ? "ONLINE" : "OFFLINE", connected],
      ["Signal", "signal", null, null],
      ["RSSI", "rssi", null, null],
      ["Mod", "modulation", null, null],
      ["Audio", "audio_running", this._binaryActive("audio_running") ? "ON" : "OFF", this._binaryActive("audio_running")],
      ["Rec", "recording_active", this._binaryActive("recording_active") ? "ON" : "OFF", this._binaryActive("recording_active")],
      ["Capture", "recording_status", null, null],
      ["Daemon", "daemon_state", null, null],
    ]) {
      const cell = this._cell(documentObject, label, field, {
        className: "top-cell",
        value: value ?? undefined,
      });
      if (active !== null) {
        cell.dataset.active = String(active);
      }
      top.append(cell);
    }
    return top;
  }

  _renderSimple(documentObject) {
    const content = documentObject.createElement("div");
    content.className = "display-content simple-layout";
    content.append(
      this._hierarchyPanel(documentObject, "System", "system", [
        ["Site", "site"],
        ["Frequency", "frequency"],
      ]),
      this._hierarchyPanel(documentObject, "Department", "department", [
        ["Service", "service_type"],
        ["Signal", "signal"],
      ]),
      this._hierarchyPanel(documentObject, "Channel", "channel", [
        ["Modulation", "modulation"],
        ["RSSI", "rssi"],
      ]),
    );
    return content;
  }

  _renderDetail(documentObject) {
    const content = documentObject.createElement("div");
    content.className = "display-content detail-layout";
    const hierarchy = documentObject.createElement("div");
    hierarchy.className = "detail-hierarchy";

    for (const [label, field] of [
      ["System", "system"],
      ["Department", "department"],
      ["Channel", "channel"],
    ]) {
      hierarchy.append(
        this._cell(documentObject, label, field, {
          className: `detail-primary detail-${field}`,
        }),
      );
    }

    const details = documentObject.createElement("div");
    details.className = "detail-grid";
    for (const [label, field] of [
      ["Site", "site"],
      ["Frequency", "frequency"],
      ["Service", "service_type"],
      ["Modulation", "modulation"],
      ["Signal", "signal"],
      ["RSSI", "rssi"],
      ["Audio", "audio_running"],
      ["Recording", "recording_active"],
      ["Capture", "recording_status"],
      ["Daemon", "daemon_state"],
    ]) {
      details.append(
        this._cell(documentObject, label, field, {
          className: "detail-cell",
        }),
      );
    }

    content.append(hierarchy, details);
    return content;
  }

  _specialFields(layout) {
    const mappings = {
      search: [
        ["Current channel", "channel"],
        ["Search frequency", "frequency"],
        ["Service type", "service_type"],
      ],
      weather: [
        ["Weather channel", "channel"],
        ["Weather frequency", "frequency"],
        ["Weather service", "service_type"],
      ],
      tone_out: [
        ["Tone-Out channel", "channel"],
        ["Tone-Out frequency", "frequency"],
        ["Tone-Out modulation", "modulation"],
        ["Tone A", "tone_out_tone_a"],
        ["Tone B", "tone_out_tone_b"],
      ],
    };
    return mappings[layout];
  }

  _renderSpecial(documentObject, layout) {
    const content = documentObject.createElement("div");
    content.className = (
      `display-content special-layout special-layout-${layout}`
    );

    const primary = documentObject.createElement("div");
    primary.className = "special-primary";
    for (const [label, field] of this._specialFields(layout)) {
      primary.append(
        this._cell(documentObject, label, field, {
          className: "special-primary-cell",
        }),
      );
    }

    const details = documentObject.createElement("div");
    details.className = "special-grid";
    for (const [label, field] of [
      ["System", "system"],
      ["Department", "department"],
      ["Site", "site"],
      ["Modulation", "modulation"],
      ["Signal", "signal"],
      ["RSSI", "rssi"],
    ]) {
      details.append(
        this._cell(documentObject, label, field, {
          className: "special-cell",
        }),
      );
    }

    content.append(primary, details);
    return content;
  }

  _renderFooter(documentObject, layout) {
    const footer = documentObject.createElement("footer");
    footer.className = "display-footer";
    footer.append(
      textElement(documentObject, "span", "footer-item", this._config.title),
      textElement(
        documentObject,
        "span",
        "footer-item",
        this._config.layout === "auto"
          ? `Auto / ${optionLabel(SDS200_DISPLAY_LAYOUTS, layout)}`
          : optionLabel(SDS200_DISPLAY_LAYOUTS, layout),
      ),
      textElement(
        documentObject,
        "span",
        "footer-item",
        optionLabel(SDS200_DISPLAY_PALETTES, this._config.palette),
      ),
    );
    return footer;
  }

  _render() {
    if (!this.shadowRoot || !this._config) {
      return;
    }

    const documentObject = this.ownerDocument;
    const layout = this._resolvedLayout();
    const style = documentObject.createElement("style");
    style.textContent = `
      :host {
        display: block;
        width: 100%;
        min-width: 0;
      }

      .viewport {
        box-sizing: border-box;
        width: 100%;
        max-width: 100%;
        margin: 0 auto;
        container-type: inline-size;
      }

      .viewport[data-fit="viewport"] {
        width: min(100%, calc((100dvh - 4rem) * 4 / 3));
        max-width: calc((100dvh - 4rem) * 4 / 3);
      }

      .scanner-frame {
        --frame-bg: #07111f;
        --frame-fg: #f4f8ff;
        --frame-muted: #b8c5d8;
        --frame-line: #6f849f;
        --top-bg: #142c49;
        --system-bg: #173564;
        --department-bg: #20451f;
        --channel-bg: #66320f;
        --detail-bg: #111d2c;
        --active: #69ef88;
        --inactive: #aeb8c5;
        box-sizing: border-box;
        display: grid;
        grid-template-rows: 13% minmax(0, 1fr) 10%;
        width: 100%;
        aspect-ratio: 4 / 3;
        overflow: hidden;
        border: max(1px, 0.2cqi) solid var(--frame-line);
        border-radius: min(1.4cqi, 14px);
        background: var(--frame-bg);
        color: var(--frame-fg);
        font-family: "Roboto Mono", "SFMono-Regular", Consolas, monospace;
        font-size: clamp(7px, 2.05cqi, 24px);
        line-height: 1.05;
        box-shadow: var(--ha-card-box-shadow, none);
      }

      .scanner-frame[data-palette="black_on_white"] {
        --frame-bg: #fff;
        --frame-fg: #050505;
        --frame-muted: #333;
        --frame-line: #111;
        --top-bg: #eee;
        --system-bg: #fff;
        --department-bg: #f3f3f3;
        --channel-bg: #fff;
        --detail-bg: #f3f3f3;
        --active: #050505;
        --inactive: #666;
      }

      .scanner-frame[data-palette="white_on_black"] {
        --frame-bg: #000;
        --frame-fg: #fff;
        --frame-muted: #d5d5d5;
        --frame-line: #fff;
        --top-bg: #111;
        --system-bg: #000;
        --department-bg: #111;
        --channel-bg: #000;
        --detail-bg: #111;
        --active: #fff;
        --inactive: #aaa;
      }

      .top-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        grid-template-rows: repeat(2, minmax(0, 1fr));
        min-height: 0;
        background: var(--top-bg);
        border-bottom: 1px solid var(--frame-line);
      }

      .display-cell {
        box-sizing: border-box;
        display: flex;
        min-width: 0;
        align-items: baseline;
        gap: 0.35em;
        padding: 0.18em 0.42em;
        overflow: hidden;
        border-right: 1px solid color-mix(in srgb, var(--frame-line) 65%, transparent);
        border-bottom: 1px solid color-mix(in srgb, var(--frame-line) 65%, transparent);
      }

      .cell-label,
      .cell-value,
      .hierarchy-label,
      .hierarchy-value,
      .footer-item {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .cell-label,
      .hierarchy-label {
        flex: 0 0 auto;
        color: var(--frame-muted);
        font-size: 0.72em;
        letter-spacing: 0.04em;
        text-transform: uppercase;
      }

      .cell-value {
        flex: 1 1 auto;
        font-weight: 700;
      }

      .top-cell[data-active="true"] .cell-value {
        color: var(--active);
      }

      .top-cell[data-active="false"] .cell-value {
        color: var(--inactive);
      }

      .display-content {
        min-width: 0;
        min-height: 0;
        overflow: hidden;
      }

      .simple-layout {
        display: grid;
        grid-template-rows: repeat(3, minmax(0, 1fr));
      }

      .hierarchy-panel {
        box-sizing: border-box;
        display: grid;
        grid-template-rows: 18% minmax(0, 1fr) 28%;
        min-width: 0;
        min-height: 0;
        overflow: hidden;
        border-bottom: 1px solid var(--frame-line);
      }

      .hierarchy-system { background: var(--system-bg); }
      .hierarchy-department { background: var(--department-bg); }
      .hierarchy-channel { background: var(--channel-bg); }

      .hierarchy-label {
        padding: 0.28em 0.6em 0;
      }

      .hierarchy-value {
        align-self: center;
        padding: 0 0.5em;
        font-size: 1.55em;
        text-align: center;
      }

      .hierarchy-secondary {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        min-height: 0;
        background: color-mix(in srgb, var(--frame-bg) 72%, transparent);
        border-top: 1px solid var(--frame-line);
      }

      .secondary-cell {
        border-bottom: 0;
      }

      .detail-layout {
        display: grid;
        grid-template-rows: 42% minmax(0, 1fr);
      }

      .detail-hierarchy {
        display: grid;
        grid-template-rows: repeat(3, minmax(0, 1fr));
        min-height: 0;
      }

      .detail-primary {
        align-items: center;
        border-bottom: 1px solid var(--frame-line);
      }

      .detail-primary .cell-value {
        font-size: 1.35em;
        text-align: center;
      }

      .detail-system { background: var(--system-bg); }
      .detail-department { background: var(--department-bg); }
      .detail-channel { background: var(--channel-bg); }

      .detail-grid,
      .special-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        grid-template-rows: repeat(5, minmax(0, 1fr));
        min-height: 0;
        background: var(--detail-bg);
      }

      .detail-cell,
      .special-cell {
        align-items: center;
      }

      .special-layout {
        display: grid;
        grid-template-rows: 61% minmax(0, 1fr);
      }

      .special-layout-tone_out {
        grid-template-rows: 72% minmax(0, 1fr);
      }

      .special-primary {
        display: grid;
        grid-template-rows: repeat(3, minmax(0, 1fr));
        min-height: 0;
      }

      .special-layout-tone_out .special-primary {
        grid-template-rows: repeat(5, minmax(0, 1fr));
      }

      .special-layout-tone_out .special-grid {
        grid-template-rows: repeat(3, minmax(0, 1fr));
      }

      .special-primary-cell {
        display: grid;
        grid-template-rows: 30% minmax(0, 1fr);
        align-items: center;
        background: var(--frame-bg);
        border-bottom: 1px solid var(--frame-line);
      }

      .special-primary-cell .cell-value {
        padding: 0 0.25em;
        font-size: 1.65em;
        text-align: center;
      }

      .special-grid {
        grid-template-columns: repeat(3, minmax(0, 1fr));
        grid-template-rows: repeat(2, minmax(0, 1fr));
      }

      .display-footer {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        min-height: 0;
        background: var(--top-bg);
        border-top: 1px solid var(--frame-line);
      }

      .footer-item {
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0 0.45em;
        border-right: 1px solid var(--frame-line);
        font-size: 0.8em;
        font-weight: 700;
        text-transform: uppercase;
      }

      .configuration {
        margin: 0;
        padding: 0.6rem 0.8rem;
        color: var(--secondary-text-color, #687078);
        font-size: 0.85rem;
        text-align: center;
      }
    `;

    const viewport = documentObject.createElement("div");
    viewport.className = "viewport";
    viewport.dataset.fit = this._config.fit;

    const frame = documentObject.createElement("article");
    frame.className = "scanner-frame";
    frame.dataset.layout = layout;
    frame.dataset.layoutMode = this._config.layout;
    frame.dataset.screenKind = this._stateText("screen_kind", "unknown");
    frame.dataset.palette = this._config.palette;
    applyDisplaySystemPalette(frame, this._config.palette);
    frame.setAttribute(
      "aria-label",
      `${this._config.title}: ${optionLabel(
        SDS200_DISPLAY_LAYOUTS,
        layout,
      )} display`,
    );

    const content =
      layout === "simple"
        ? this._renderSimple(documentObject)
        : layout === "detail"
          ? this._renderDetail(documentObject)
          : this._renderSpecial(documentObject, layout);

    frame.append(
      this._renderTop(documentObject),
      content,
      this._renderFooter(documentObject, layout),
    );
    viewport.append(frame);

    const children = [style, viewport];
    if (Object.keys(this._config.entities).length === 0) {
      children.push(
        textElement(
          documentObject,
          "p",
          "configuration",
          "Select the SDS200 entities in the card editor.",
        ),
      );
    }
    this.shadowRoot.replaceChildren(...children);
  }
}

if (!customElements.get(SDS200_DISPLAY_CARD_TAG)) {
  customElements.define(
    SDS200_DISPLAY_CARD_TAG,
    Sds200DisplayCard,
  );
}

window.customCards = window.customCards || [];

if (
  !window.customCards.some(
    (card) => card.type === SDS200_DISPLAY_CARD_TYPE,
  )
) {
  window.customCards.push({
    type: SDS200_DISPLAY_CARD_TYPE,
    name: "SDS200 Display",
    description: (
      "Responsive scanner-inspired Simple, Detail, Search, " +
      "Weather, and Tone-Out layouts."
    ),
    preview: true,
    documentationURL: (
      "https://github.com/stevenboyd78/sdsctl/blob/main/" +
      "docs/home-assistant-app.md"
    ),
  });
}
