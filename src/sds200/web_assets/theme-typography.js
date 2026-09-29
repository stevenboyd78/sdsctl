/* Built-in theme appearance only. No network, scanner commands, or font install. */
(() => {
  "use strict";
  const root = document.documentElement;
  const key = "sdsctl.web.theme-typography.v1";
  const themes = {
    "first-responder": [["barlow", "Barlow Condensed + IBM Plex Mono"]],
    "amateur-radio": [["orbitron", "Orbitron + Atkinson Mono"],
      ["audiowide", "Audiowide + Atkinson Mono"], ["atkinson", "Atkinson readability-first"]],
    "pip-boy-inspired": [["share-tech", "Share Tech Mono"]],
    "matrix": [["plex", "IBM Plex Mono"], ["jetbrains", "JetBrains Mono"],
      ["source-code", "Source Code Pro"]],
  };
  const modes = ["theme", "mixed", "system"];
  let saved = {};
  function restore(value) {
    saved = {};
    try {
      const parsed = JSON.parse(value);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return;
      for (const [theme, choices] of Object.entries(themes)) {
        const entry = Object.hasOwn(parsed, theme) ? parsed[theme] : null;
        if (!entry || typeof entry !== "object" || Array.isArray(entry)) continue;
        saved[theme] = {
          font: choices.some(([id]) => id === entry.font) ? entry.font : choices[0][0],
          mode: modes.includes(entry.mode) ? entry.mode : "theme",
        };
      }
    } catch { /* Invalid or unavailable storage uses safe defaults. */ }
  }
  try { restore(window.localStorage.getItem(key)); } catch { /* Optional storage. */ }
  function selection() {
    const theme = root.dataset.theme;
    if (!Object.hasOwn(themes, theme)) return null;
    return saved[theme] || {font: themes[theme][0][0], mode: "theme"};
  }
  function paint() {
    const selected = selection();
    if (selected) {
      root.dataset.themeFont = selected.font;
      root.dataset.themeTypography = selected.mode;
    } else {
      delete root.dataset.themeFont;
      delete root.dataset.themeTypography;
    }
  }
  paint();
  function connect() {
    const wrapper = document.getElementById("theme-typography-pickers");
    const fontControl = document.getElementById("theme-font-picker");
    const fontPicker = document.getElementById("theme-font-select");
    const modePicker = document.getElementById("theme-typography-select");
    let displayedTheme = null;
    function sync() {
      paint();
      const selected = selection();
      if (wrapper) wrapper.hidden = !selected;
      if (!selected || !fontPicker || !modePicker || !fontControl) return;
      const theme = root.dataset.theme;
      if (displayedTheme !== theme) {
        fontPicker.replaceChildren(...themes[theme].map(([id, label]) => {
          const option = document.createElement("option");
          option.value = id; option.textContent = label; return option;
        }));
        displayedTheme = theme;
      }
      fontPicker.value = selected.font;
      modePicker.value = selected.mode;
      fontControl.hidden = themes[theme].length < 2;
    }
    function change() {
      if (!selection() || !fontPicker || !modePicker) return;
      const theme = root.dataset.theme;
      saved[theme] = {
        font: themes[theme].some(([id]) => id === fontPicker.value)
          ? fontPicker.value : themes[theme][0][0],
        mode: modes.includes(modePicker.value) ? modePicker.value : "theme",
      };
      sync();
      try { window.localStorage.setItem(key, JSON.stringify(saved)); } catch { /* Optional. */ }
    }
    fontPicker?.addEventListener("change", change);
    modePicker?.addEventListener("change", change);
    new MutationObserver(sync).observe(root, {attributes: true, attributeFilter: ["data-theme"]});
    window.addEventListener("storage", event => {
      if (event.key === key || event.key === null) { restore(event.newValue); sync(); }
    });
    sync();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", connect, {once: true});
  else connect();
})();
