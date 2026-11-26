(() => {
  "use strict";

  const STORAGE_KEY = "sdsctl.favorites.appearance";
  const CHOICES = Object.freeze(["auto", "light", "dark"]);
  const root = document.documentElement;
  const colorQuery = typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;
  let selected = "auto";

  function normalize(value) {
    return CHOICES.includes(value) ? value : "auto";
  }

  function storedChoice() {
    try {
      return normalize(window.localStorage.getItem(STORAGE_KEY));
    } catch {
      return "auto";
    }
  }

  function effectivePalette(value = selected) {
    if (value === "dark") return "dark";
    if (value === "light") return "light";
    return colorQuery?.matches === true ? "dark" : "light";
  }

  function updateThemeColor(palette) {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta !== null) {
      meta.setAttribute("content", palette === "dark" ? "#10151d" : "#f3f6fa");
    }
  }

  function synchronizePicker() {
    const picker = document.querySelector("#favorites-appearance-select");
    if (picker instanceof HTMLSelectElement) picker.value = selected;
  }

  function apply(value, {persist = false} = {}) {
    selected = normalize(value);
    const palette = effectivePalette(selected);
    root.dataset.favoritesAppearance = selected;
    root.dataset.favoritesPalette = palette;
    root.style.colorScheme = selected === "auto" ? "light dark" : selected;
    updateThemeColor(palette);
    synchronizePicker();
    if (persist) {
      try {
        window.localStorage.setItem(STORAGE_KEY, selected);
      } catch {
        // Appearance is still applied when storage is unavailable.
      }
    }
    return selected;
  }

  function bindPicker() {
    const picker = document.querySelector("#favorites-appearance-select");
    if (!(picker instanceof HTMLSelectElement)) return;
    synchronizePicker();
    picker.addEventListener("change", () => apply(picker.value, {persist: true}));
  }

  selected = storedChoice();
  apply(selected);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindPicker, {once: true});
  } else {
    bindPicker();
  }

  if (colorQuery !== null && typeof colorQuery.addEventListener === "function") {
    colorQuery.addEventListener("change", () => {
      if (selected === "auto") apply("auto");
    });
  }

  window.sdsctlFavoritesAppearance = Object.freeze({
    choices: CHOICES,
    current: () => selected,
    effective: () => effectivePalette(selected),
    select: (value) => apply(value, {persist: true}),
  });
})();
