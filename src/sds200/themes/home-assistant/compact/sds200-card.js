const SDS200_CARD_TYPE = "sds200-card";
const SDS200_CARD_TAG = "sds200-card";

const SDS200_CARD_SYSTEM_PALETTES = Object.freeze({
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
const SDS200_CARD_PALETTES = Object.freeze([
  Object.freeze({ value: "theme", label: "Home Assistant theme" }),
  ...Object.keys(SDS200_CARD_SYSTEM_PALETTES).map((value) =>
    Object.freeze({ value, label: value }),
  ),
]);

const SDS200_ENTITY_FIELDS = Object.freeze([
  Object.freeze({
    key: "scanner_connected",
    label: "Scanner connection",
    domain: "binary_sensor",
  }),
  Object.freeze({
    key: "system",
    label: "System",
    domain: "sensor",
  }),
  Object.freeze({
    key: "department",
    label: "Department",
    domain: "sensor",
  }),
  Object.freeze({
    key: "site",
    label: "Site",
    domain: "sensor",
  }),
  Object.freeze({
    key: "channel",
    label: "Channel",
    domain: "sensor",
  }),
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
  Object.freeze({
    key: "signal",
    label: "Signal",
    domain: "sensor",
  }),
  Object.freeze({
    key: "rssi",
    label: "RSSI",
    domain: "sensor",
  }),
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

function fieldForName(name) {
  return SDS200_ENTITY_FIELDS.find(
    (field) => field.key === name,
  );
}

function cardOptionLabel(options, value) {
  return options.find((option) => option.value === value)?.label;
}

function applyCardSystemPalette(element, palette) {
  const colors = SDS200_CARD_SYSTEM_PALETTES[palette];
  if (colors === undefined) {
    return;
  }
  const names = [
    "background", "surface", "panel", "foreground", "muted", "border",
    "primary", "secondary", "warning", "error", "success", "accent",
  ];
  names.forEach((name, index) => {
    element.style.setProperty(`--sds200-card-${name}`, colors[index]);
  });
}

function toneOutDisplay(value) {
  const match = /^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:hz)?$/i.exec(
    value.trim(),
  );
  return match !== null && Number(match[1]) === 0 ? "Detect" : value;
}

function requireCardConfig(config) {
  if (
    config === null ||
    typeof config !== "object" ||
    Array.isArray(config)
  ) {
    throw new Error(
      "SDS200 card configuration must be an object.",
    );
  }

  const title =
    typeof config.title === "string" && config.title.trim()
      ? config.title.trim()
      : "SDS200 Scanner";
  const palette = config.palette ?? "theme";
  if (!cardOptionLabel(SDS200_CARD_PALETTES, palette)) {
    throw new Error(
      `SDS200 card palette "${palette}" is not supported.`,
    );
  }

  const source =
    config.entities === undefined ? {} : config.entities;

  if (
    source === null ||
    typeof source !== "object" ||
    Array.isArray(source)
  ) {
    throw new Error(
      "SDS200 card entities must be an object.",
    );
  }

  const entities = {};

  for (const { key, domain } of SDS200_ENTITY_FIELDS) {
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
      !value.includes(".")
    ) {
      throw new Error(
        `SDS200 card entity "${key}" must be a Home Assistant entity ID.`,
      );
    }

    if (!value.startsWith(`${domain}.`)) {
      throw new Error(
        `SDS200 card entity "${key}" must be in the ${domain} domain.`,
      );
    }

    entities[key] = value;
  }

  return Object.freeze({
    title,
    palette,
    entities: Object.freeze(entities),
  });
}

function textElement(
  documentObject,
  tagName,
  className,
  text,
) {
  const node = documentObject.createElement(tagName);
  node.className = className;
  node.textContent = text;
  return node;
}

class Sds200Card extends HTMLElement {
  static getStubConfig() {
    return {
      palette: "theme",
      entities: {},
    };
  }

  static getConfigForm() {
    return {
      schema: [
        {
          name: "title",
          selector: {
            text: {},
          },
        },
        {
          name: "palette",
          required: true,
          selector: {
            select: { options: SDS200_CARD_PALETTES },
          },
        },
        {
          type: "expandable",
          name: "entities",
          title: "SDS200 entities",
          schema: SDS200_ENTITY_FIELDS.map(
            ({ key, domain }) => ({
              name: key,
              selector: {
                entity: {
                  filter: [
                    {
                      domain,
                    },
                  ],
                },
              },
            }),
          ),
        },
      ],
      computeLabel: (schema) => {
        if (schema.name === "title") {
          return "Title";
        }
        if (schema.name === "palette") {
          return "Palette";
        }

        const field = fieldForName(schema.name);
        return field?.label;
      },
      computeHelper: (schema) => {
        if (schema.name === "palette") {
          return "Follow Home Assistant or choose a System web palette.";
        }
        if (schema.name === "entities") {
          return (
            "Select the entities created by the SDS200 " +
            "MQTT Discovery device."
          );
        }

        return undefined;
      },
      assertConfig: (config) => {
        requireCardConfig(config);
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

    const event = new CustomEvent(
      "context-request",
      {
        bubbles: true,
        composed: true,
        cancelable: true,
      },
    );

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
    this._config = requireCardConfig(config);
    this._render();
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return {
      rows: 4,
      columns: 6,
      min_rows: 3,
      min_columns: 3,
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

    if (!entityId) {
      return null;
    }

    return this._states[entityId] ?? null;
  }

  _stateText(field, fallback = "—") {
    const stateObject = this._state(field);

    if (!stateObject) {
      return fallback;
    }

    const value = stateObject.state;

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
    const value =
      this._stateText(field, "").toLowerCase();

    return (
      value === "on" ||
      value === "true" ||
      value === "running"
    );
  }

  _renderStatus(
    documentObject,
    field,
    label,
  ) {
    const wrapper =
      documentObject.createElement("div");

    wrapper.className = "status";

    const indicator =
      documentObject.createElement("span");

    indicator.className = "status-indicator";
    indicator.dataset.active = String(
      this._binaryActive(field),
    );
    indicator.setAttribute(
      "aria-hidden",
      "true",
    );

    wrapper.append(
      indicator,
      textElement(
        documentObject,
        "span",
        "status-label",
        label,
      ),
    );

    return wrapper;
  }

  _renderRow(
    documentObject,
    field,
    label,
  ) {
    const row =
      documentObject.createElement("div");

    row.className = "row";

    row.append(
      textElement(
        documentObject,
        "dt",
        "label",
        label,
      ),
      textElement(
        documentObject,
        "dd",
        "value",
        this._stateText(field),
      ),
    );

    return row;
  }

  _render() {
    if (!this.shadowRoot || !this._config) {
      return;
    }

    const documentObject = this.ownerDocument;

    const style =
      documentObject.createElement("style");

    style.textContent = `
      :host {
        display: block;
        color: var(--sds200-card-foreground, var(--primary-text-color, #1f2933));
      }

      .card {
        overflow: hidden;
        border: 1px solid var(--sds200-card-border, var(--divider-color, rgb(127 127 127 / 0.25)));
        border-radius: var(--ha-card-border-radius, 12px);
        background: var(--sds200-card-background, var(--ha-card-background, var(--card-background-color, #fff)));
        box-shadow: var(--ha-card-box-shadow, none);
      }

      .header {
        padding: 1rem 1rem 0.75rem;
        border-bottom: 1px solid var(--sds200-card-border, var(--divider-color, rgb(127 127 127 / 0.2)));
        background: var(--sds200-card-surface, transparent);
      }

      .title {
        margin: 0;
        font-size: 1.1rem;
        font-weight: 600;
      }

      .channel {
        margin: 0.4rem 0 0;
        font-size: 1.35rem;
        font-weight: 700;
        overflow-wrap: anywhere;
      }

      .hierarchy {
        margin: 0.25rem 0 0;
        color: var(--sds200-card-muted, var(--secondary-text-color, #687078));
        font-size: 0.9rem;
        overflow-wrap: anywhere;
      }

      .statuses {
        display: flex;
        flex-wrap: wrap;
        gap: 0.55rem 1rem;
        padding: 0.75rem 1rem;
        border-bottom: 1px solid var(--sds200-card-border, var(--divider-color, rgb(127 127 127 / 0.2)));
        background: var(--sds200-card-panel, transparent);
      }

      .status {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        font-size: 0.82rem;
      }

      .status-indicator {
        width: 0.55rem;
        height: 0.55rem;
        border-radius: 50%;
        background: var(--sds200-card-muted, var(--disabled-text-color, #9aa0a6));
      }

      .status-indicator[data-active="true"] {
        background: var(--sds200-card-success, var(--success-color, #43a047));
      }

      .details {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        margin: 0;
        padding: 0.75rem 1rem 1rem;
        gap: 0.6rem 1rem;
        background: var(--sds200-card-surface, transparent);
      }

      .row {
        min-width: 0;
      }

      .label {
        margin: 0;
        color: var(--sds200-card-muted, var(--secondary-text-color, #687078));
        font-size: 0.72rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }

      .value {
        margin: 0.16rem 0 0;
        font-size: 0.95rem;
        overflow-wrap: anywhere;
      }

      .configuration {
        margin: 0;
        padding: 0.8rem 1rem 1rem;
        color: var(--sds200-card-muted, var(--secondary-text-color, #687078));
        font-size: 0.85rem;
      }

      @media (max-width: 28rem) {
        .details {
          grid-template-columns: 1fr;
        }
      }
    `;

    const article =
      documentObject.createElement("article");

    article.className = "card";
    article.dataset.palette = this._config.palette;
    applyCardSystemPalette(article, this._config.palette);

    const header =
      documentObject.createElement("header");

    header.className = "header";

    header.append(
      textElement(
        documentObject,
        "h2",
        "title",
        this._config.title,
      ),
      textElement(
        documentObject,
        "p",
        "channel",
        this._stateText(
          "channel",
          "No channel configured",
        ),
      ),
    );

    const hierarchy = [
      this._stateText("system", ""),
      this._stateText("department", ""),
      this._stateText("site", ""),
    ].filter(Boolean);

    if (hierarchy.length) {
      header.append(
        textElement(
          documentObject,
          "p",
          "hierarchy",
          hierarchy.join(" · "),
        ),
      );
    }

    article.append(header);

    const statuses =
      documentObject.createElement("div");

    statuses.className = "statuses";

    statuses.append(
      this._renderStatus(
        documentObject,
        "scanner_connected",
        "Scanner",
      ),
      this._renderStatus(
        documentObject,
        "audio_running",
        "Audio",
      ),
      this._renderStatus(
        documentObject,
        "recording_active",
        "Recording",
      ),
    );

    article.append(statuses);

    const details =
      documentObject.createElement("dl");

    details.className = "details";

    for (const [field, label] of [
      ["signal", "Signal"],
      ["rssi", "RSSI"],
      ["frequency", "Frequency"],
      ["modulation", "Modulation"],
      ["service_type", "Service type"],
      ["tone_out_tone_a", "Tone A"],
      ["tone_out_tone_b", "Tone B"],
      ["recording_status", "Recording status"],
      ["daemon_state", "Daemon"],
    ]) {
      if (
        [
          "frequency",
          "modulation",
          "service_type",
          "tone_out_tone_a",
          "tone_out_tone_b",
        ].includes(field) &&
        !this._config.entities[field]
      ) {
        continue;
      }

      details.append(
        this._renderRow(
          documentObject,
          field,
          label,
        ),
      );
    }

    article.append(details);

    if (
      Object.keys(
        this._config.entities,
      ).length === 0
    ) {
      article.append(
        textElement(
          documentObject,
          "p",
          "configuration",
          "Select the SDS200 entities in the card editor.",
        ),
      );
    }

    this.shadowRoot.replaceChildren(
      style,
      article,
    );
  }
}

if (!customElements.get(SDS200_CARD_TAG)) {
  customElements.define(
    SDS200_CARD_TAG,
    Sds200Card,
  );
}

window.customCards =
  window.customCards || [];

if (
  !window.customCards.some(
    (card) =>
      card.type === SDS200_CARD_TYPE,
  )
) {
  window.customCards.push({
    type: SDS200_CARD_TYPE,
    name: "SDS200 Scanner",
    description:
      "Read-only scanner status from the sds200 Home Assistant entities.",
    preview: true,
    documentationURL:
      "https://github.com/stevenboyd78/sdsctl/blob/main/docs/home-assistant-app.md",
  });
}
