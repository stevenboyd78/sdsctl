"use strict";

/*
 * The raw dashboard.html file is a server template, not the deployed Web UI.
 * When a reviewer explicitly opens that source file with file://, provide a
 * truthful, inert visual fixture instead of an unstyled collection of
 * placeholders.  The production renderer removes this script tag entirely.
 */
(() => {
  if (globalThis.location?.protocol !== "file:") return;

  const themes = Object.freeze({
    "cinematic-carrier-air-wing": "Carrier Air Wing",
    "cinematic-tactical-field": "Tactical Field",
    "cinematic-wizarding-archive": "Wizarding Archive",
    "cinematic-galactic-operations": "Galactic Operations",
    "cinematic-mission-control": "Mission Control",
    "cinematic-cyberpunk-night": "Cyberpunk Night",
  });
  const requested = new URLSearchParams(globalThis.location.search).get("theme");
  const theme = Object.hasOwn(themes, requested)
    ? requested
    : "cinematic-carrier-air-wing";

  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.previewMode = "static";
  document.documentElement.dataset.connectionState = "preview";

  for (const href of [
    "dashboard.css",
    `../themes/web/${theme}/theme.css`,
    "dashboard-viewport.css",
    "mimic-sds.css",
    "lcars-v2.css",
    "theme-typography.css",
  ]) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.append(link);
  }

  const sample = Object.freeze({
    "scanner-connected": "Static fixture",
    "scanner-model": "SDS200 (sample)",
    "scanner-firmware": "Sample firmware",
    "scanner-endpoint": "No live endpoint",
    "daemon-state": "Not connected",
    "psi-state": "Static preview",
    "audio-state": "Not connected",
    "router-state": "Not connected",
    "transition-sequence": "—",
    "radio-system": "Demo Metro Public Safety",
    "radio-department": "Central Dispatch",
    "radio-channel": "Dispatch 1 (Demo)",
    "radio-site": "Metro Simulcast",
    "radio-mode": "P25",
    "radio-screen": "Scanning",
    "radio-screen-raw": "TGID",
    "radio-signal": "5 / 5",
    "radio-rssi": "-67 dBm",
    "radio-system-index": "120",
    "radio-system-hold": "Off",
    "radio-department-index": "240",
    "radio-department-hold": "Off",
    "radio-site-index": "12",
    "radio-site-hold": "Off",
    "radio-channel-index": "400",
    "radio-channel-number": "101",
    "radio-channel-kind": "TGID",
    "radio-channel-hold": "On",
  });

  document.addEventListener("DOMContentLoaded", () => {
    document.title = "sdsctl cinematic static preview — sample data only";

    const badge = document.getElementById("status-badge");
    badge.dataset.state = "preview";
    badge.textContent = "Static preview";

    const message = document.getElementById("dashboard-message");
    message.dataset.state = "preview";
    message.textContent =
      "LOCAL SOURCE PREVIEW — sample data only; no daemon or scanner is connected.";

    const activityTitle = document.getElementById("activity-title");
    activityTitle.textContent = "Sample scanner activity";

    for (const [identifier, value] of Object.entries(sample)) {
      const node = document.getElementById(identifier);
      if (node !== null) node.textContent = value;
    }
    for (const node of document.querySelectorAll("[data-hold-scope]")) {
      node.dataset.holdState = "off";
    }

    const updated = document.getElementById("last-update");
    updated.removeAttribute("datetime");
    updated.textContent = "Static sample — not live";

    const picker = document.getElementById("theme-select");
    picker.replaceChildren(
      ...Object.entries(themes).map(([value, label]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        option.selected = value === theme;
        return option;
      }),
    );
    picker.addEventListener("change", () => {
      const target = new URL(globalThis.location.href);
      target.searchParams.set("theme", picker.value);
      globalThis.location.assign(target.href);
    });
  });
})();
