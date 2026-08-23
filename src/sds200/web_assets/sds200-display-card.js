const SDS200_DISPLAY_CARD_TYPE = "sds200-display-card";
const SDS200_DISPLAY_CARD_TAG = "sds200-display-card";

const SDS200_DISPLAY_LAYOUTS = Object.freeze([
  Object.freeze({ value: "simple", label: "Simple" }),
  Object.freeze({ value: "detail", label: "Detail" }),
  Object.freeze({ value: "search", label: "Search / Close Call" }),
  Object.freeze({ value: "weather", label: "Weather" }),
  Object.freeze({ value: "tone_out", label: "Tone-Out" }),
]);

const SDS200_DISPLAY_PALETTES = Object.freeze([
  Object.freeze({ value: "color", label: "Color" }),
  Object.freeze({ value: "black_on_white", label: "Black on White" }),
  Object.freeze({ value: "white_on_black", label: "White on Black" }),
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

function fieldForName(name) {
  return SDS200_DISPLAY_ENTITY_FIELDS.find(
    (field) => field.key === name,
  );
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
  const palette = config.palette ?? "color";
  const fit = config.fit ?? "card";

  if (!optionLabel(SDS200_DISPLAY_LAYOUTS, layout)) {
    throw new Error(
      `SDS200 display card layout "${layout}" is not supported.`,
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
          palette: "Display palette",
          fit: "Fit",
        };
        return labels[schema.name] ?? fieldForName(schema.name)?.label;
      },
      computeHelper: (schema) => {
        const helpers = {
          layout: "Choose a scanner-inspired information layout.",
          palette: "Choose Color, Black on White, or White on Black.",
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
    return value;
  }

  _binaryActive(field) {
    const value = this._stateText(field, "").toLowerCase();
    return value === "on" || value === "true" || value === "running";
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

  _specialFields() {
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
      ],
    };
    return mappings[this._config.layout];
  }

  _renderSpecial(documentObject) {
    const content = documentObject.createElement("div");
    content.className = "display-content special-layout";

    const primary = documentObject.createElement("div");
    primary.className = "special-primary";
    for (const [label, field] of this._specialFields()) {
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

  _renderFooter(documentObject) {
    const footer = documentObject.createElement("footer");
    footer.className = "display-footer";
    footer.append(
      textElement(documentObject, "span", "footer-item", this._config.title),
      textElement(
        documentObject,
        "span",
        "footer-item",
        optionLabel(SDS200_DISPLAY_LAYOUTS, this._config.layout),
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

      .special-primary {
        display: grid;
        grid-template-rows: repeat(3, minmax(0, 1fr));
        min-height: 0;
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
    frame.dataset.layout = this._config.layout;
    frame.dataset.palette = this._config.palette;
    frame.setAttribute(
      "aria-label",
      `${this._config.title}: ${optionLabel(
        SDS200_DISPLAY_LAYOUTS,
        this._config.layout,
      )} display`,
    );

    const content =
      this._config.layout === "simple"
        ? this._renderSimple(documentObject)
        : this._config.layout === "detail"
          ? this._renderDetail(documentObject)
          : this._renderSpecial(documentObject);

    frame.append(
      this._renderTop(documentObject),
      content,
      this._renderFooter(documentObject),
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
