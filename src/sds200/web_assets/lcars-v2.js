/* LCARS v2 presentation only: no scanner commands or network access.
 * Read preferences before paint; attach controls after the shell is parsed.
 * Move existing live nodes, preserving handlers and restoring other themes.
 */
(() => {
  "use strict";
  const root = document.documentElement;
  const preferences = [
    {key: "sdsctl.web.lcars-v2-palette", attribute: "lcarsV2Palette",
      id: "lcars-v2-palette-select", fallback: "classic",
      choices: ["classic", "nemesis-blue", "lower-decks", "lower-decks-padd", "voyager", "picard"]},
    {key: "sdsctl.web.lcars-v2-typography", attribute: "lcarsV2Type",
      id: "lcars-v2-type-select", fallback: "antonio", choices: ["antonio", "mixed", "system"]},
  ];
  root.dataset.lcarsV2Layout = "v2";
  root.dataset.lcarsV2SourceColors = "true";
  root.dataset.lcarsV2Bands = "segmented";

  function apply(preference, value, persist = false) {
    const selection = preference.choices.includes(value) ? value : preference.fallback;
    root.dataset[preference.attribute] = selection;
    const picker = document.getElementById(preference.id);
    if (picker) picker.value = selection;
    if (persist) {
      try { window.localStorage.setItem(preference.key, selection); } catch { /* Optional. */ }
    }
  }
  for (const preference of preferences) {
    let stored = null;
    try { stored = window.localStorage.getItem(preference.key); } catch { /* Optional. */ }
    apply(preference, stored, stored !== null && !preference.choices.includes(stored));
  }
  window.addEventListener("storage", event => {
    for (const preference of preferences) {
      if (event.key === preference.key || event.key === null) {
        apply(preference, event.key === null ? null : event.newValue);
      }
    }
  });

  function connect() {
    const wrapper = document.getElementById("lcars-v2-appearance-pickers");
    for (const preference of preferences) {
      const picker = document.getElementById(preference.id);
      if (!picker) continue;
      picker.value = root.dataset[preference.attribute];
      picker.addEventListener("change", () => apply(preference, picker.value, true));
    }
    const tabs = document.querySelector(".workspace-tabs");
    const panel = document.getElementById("radio-activity-panel");
    const controls = panel?.querySelector(".radio-view-controls");
    const hierarchy = panel?.querySelector(".scanner-display-hierarchy");
    const details = document.getElementById("radio-field-groups");
    const wide = window.matchMedia("(min-width: 64rem) and (min-height: 38rem)");
    const system = document.getElementById("radio-system");
    const siteRow = document.getElementById("radio-site")?.parentElement;
    const originalList = siteRow?.parentElement;
    const anchor = document.createComment("Original Site position");
    const context = document.createElement("dd");
    context.className = "lcars-v2-site-context";
    const list = document.createElement("dl");
    list.className = "lcars-v2-site-list";
    context.append(list);
    if (system && originalList) originalList.insertBefore(anchor, siteRow);

    function sync() {
      const active = root.dataset.theme === "lcars";
      if (wrapper) wrapper.hidden = !active;
      if (tabs) tabs.setAttribute("aria-orientation", active && wide.matches ? "vertical" : "horizontal");
      // Compact display navigation owns its disclosure content; do not pull
      // those controls out of it or insert before a non-child.
      if (panel && controls?.parentElement === panel && hierarchy?.parentElement === panel &&
          details?.parentElement === panel) {
        const next = active ? details : hierarchy;
        if (controls.nextElementSibling !== next) panel.insertBefore(controls, next);
      }
      root.dataset.lcarsV2Site = active ? "system" : "details";
      if (system && siteRow && originalList) {
        if (active && siteRow.parentElement !== list) {
          list.append(siteRow);
          system.after(context);
        } else if (!active && siteRow.parentElement !== originalList) {
          anchor.after(siteRow);
          context.remove();
        }
      }
    }
    new MutationObserver(sync).observe(root, {
      attributes: true, attributeFilter: ["data-theme", "data-kiosk-compact"],
    });
    wide.addEventListener("change", sync);
    sync();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", connect, {once: true});
  else connect();
})();
