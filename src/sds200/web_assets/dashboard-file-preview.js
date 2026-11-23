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
  document.documentElement.dataset.themeEffects = "full";
  document.documentElement.dataset.themeTelemetry = "preview";
  document.documentElement.dataset.themeSignal = "strong";
  document.documentElement.dataset.themeRssi = "good";
  document.documentElement.dataset.themeSignalState = "available";
  document.documentElement.dataset.themeRssiState = "available";
  document.documentElement.style.setProperty("--theme-signal-normalized", "1");
  document.documentElement.style.setProperty("--theme-rssi-normalized", "0.6625");

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

  function initializeStaticPreviewMenu() {
    const make = (tag, text, className) => {
      const node = document.createElement(tag);
      if (text) node.textContent = text;
      if (className) node.className = className;
      return node;
    };
    const heading = document.querySelector(".brand h1");
    const headingRow = make("div", null, "native-menu-heading");
    heading.before(headingRow);
    const trigger = make("button");
    trigger.id = "native-menu-toggle";
    trigger.type = "button";
    trigger.setAttribute("aria-label", "Open dashboard menu");
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-controls", "native-menu");
    trigger.setAttribute("aria-expanded", "false");
    const triggerLabel = make("span", "Menu", "native-menu-label");
    const triggerIcon = make("span", "☰", "native-menu-icon");
    triggerIcon.setAttribute("aria-hidden", "true");
    trigger.append(triggerLabel, triggerIcon);
    headingRow.append(trigger, heading);

    const dialog = make("dialog", null, "native-menu");
    dialog.id = "native-menu";
    dialog.tabIndex = -1;
    dialog.setAttribute("aria-labelledby", "native-menu-title");
    const menuHeader = make("div", null, "native-menu-header");
    const title = make("h2", "Menu");
    title.id = "native-menu-title";
    const closeButton = make("button", "Close");
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", "Close dashboard menu");
    menuHeader.append(title, closeButton);
    const appearance = make("section", null, "native-menu-appearance");
    appearance.setAttribute("aria-label", "Appearance");
    appearance.append(make("h3", "Appearance"));
    const page = make("section", null, "native-menu-page");
    page.setAttribute("aria-label", "Page information");
    page.append(make("h3", "Page"));
    const details = make("section", null, "native-menu-status-details");
    details.setAttribute("aria-label", "Status details");
    details.append(make("h3", "Status details"));
    dialog.append(menuHeader, appearance, page, details);
    document.body.append(dialog);

    for (const node of [
      document.querySelector(".theme-picker"),
      document.getElementById("system-palette-picker"),
      document.getElementById("lcars-v2-appearance-pickers"),
      document.getElementById("theme-typography-pickers"),
      document.getElementById("theme-effects-controls"),
      document.getElementById("matrix-rain-controls"),
    ]) {
      if (!(node instanceof HTMLElement)) throw new Error("Static preview appearance control is unavailable.");
      appearance.append(node);
    }
    page.append(document.getElementById("dashboard-page-identity"));
    details.append(document.getElementById("dashboard-status-details"));
    document.querySelector(".header-actions").append(
      document.getElementById("dashboard-message"),
    );
    document.querySelector(".overview").hidden = true;

    const focusableControls = () => [...dialog.querySelectorAll(
      "button, input, select, summary, [tabindex]:not([tabindex='-1'])",
    )].filter(node => !node.disabled && node.getClientRects().length > 0 &&
      node.closest("[hidden], [inert], [aria-hidden='true']") === null);
    const close = () => {
      trigger.setAttribute("aria-expanded", "false");
      if (dialog.open) dialog.close();
      if (trigger.getClientRects().length > 0) trigger.focus();
    };
    const open = () => {
      if (dialog.open) return;
      dialog.showModal();
      trigger.setAttribute("aria-expanded", "true");
      (focusableControls()[0] ?? dialog).focus();
    };
    trigger.addEventListener("click", () => dialog.open ? close() : open());
    closeButton.addEventListener("click", close);
    dialog.addEventListener("cancel", event => {
      event.preventDefault();
      close();
    });
    dialog.addEventListener("close", () => {
      trigger.setAttribute("aria-expanded", "false");
    });
    dialog.addEventListener("keydown", event => {
      if (event.key !== "Tab") return;
      const controls = focusableControls();
      if (controls.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = controls[0];
      const last = controls.at(-1);
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog)) {
        event.preventDefault();
        first.focus();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.title = "sdsctl cinematic static preview — sample data only";

    const badge = document.getElementById("status-badge");
    badge.dataset.state = "preview";
    badge.textContent = "Static preview";

    const message = document.getElementById("dashboard-message");
    message.dataset.state = "preview";
    message.textContent = "SAMPLE DATA — no live scanner connected.";

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
    const effects = document.getElementById("theme-effects-select");
    effects.value = "full";
    effects.addEventListener("change", () => {
      document.documentElement.dataset.themeEffects = effects.value;
    });
    initializeStaticPreviewMenu();
  });
})();
