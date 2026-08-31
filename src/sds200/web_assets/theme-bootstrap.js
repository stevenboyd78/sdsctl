"use strict";

(() => {
  const STORAGE_KEY = "sdsctl.web.theme";
  const SYSTEM_PALETTE_STORAGE_KEY = "sdsctl.web.system-palette";
  const SYSTEM_PALETTE_DOCUMENTS = Object.freeze(
    __SDSCTL_SYSTEM_PALETTES__,
  );
  const SYSTEM_PALETTES = Object.freeze([
    "auto",
    ...SYSTEM_PALETTE_DOCUMENTS.map((palette) => palette.id),
  ]);
  const SYSTEM_PALETTES_BY_ID = new Map(
    SYSTEM_PALETTE_DOCUMENTS.map((palette) => [palette.id, palette]),
  );
  const THEME_DOCUMENTS = Object.freeze(__SDSCTL_WEB_THEME_MANIFESTS__);
  const THEMES = Object.freeze(THEME_DOCUMENTS.map((theme) => theme.id));
  const MANAGED_THEMES = new Set(__SDSCTL_MANAGED_WEB_THEME_IDS__);
  const THEMES_BY_ID = new Map(
    THEME_DOCUMENTS.map((theme) => [theme.id, theme]),
  );
  const MANAGED_THEME_LINKS = new Map(
    Array.from(
      document.querySelectorAll("link[data-sdsctl-managed-theme]"),
    ).map((link) => [link.dataset.sdsctlManagedTheme, link]),
  );
  const systemColorQuery =
    typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
  let activeTheme = "system";
  let activeSystemPalette = "auto";

  function normalizeTheme(value) {
    if (!THEMES.includes(value)) {
      return "system";
    }
    if (MANAGED_THEMES.has(value)) {
      const link = MANAGED_THEME_LINKS.get(value);
      if (
        link === undefined ||
        typeof link.dataset.sdsctlManagedThemeHref !== "string" ||
        link.dataset.sdsctlManagedThemeHref.length === 0
      ) {
        return "system";
      }
    }
    return value;
  }

  function readStoredTheme() {
    try {
      const storedTheme = window.localStorage.getItem(STORAGE_KEY);
      const theme = normalizeTheme(storedTheme);
      return Object.freeze({
        theme,
        repair: storedTheme !== null && storedTheme !== theme,
      });
    } catch {
      return Object.freeze({ theme: "system", repair: false });
    }
  }

  function normalizeSystemPalette(value) {
    return SYSTEM_PALETTES.includes(value) ? value : "auto";
  }

  function readStoredSystemPalette() {
    try {
      const storedPalette = window.localStorage.getItem(
        SYSTEM_PALETTE_STORAGE_KEY,
      );
      const palette = normalizeSystemPalette(storedPalette);
      return Object.freeze({
        palette,
        repair: storedPalette !== null && storedPalette !== palette,
      });
    } catch {
      return Object.freeze({palette: "auto", repair: false});
    }
  }

  function updateMetadata(theme) {
    const colorScheme = document.querySelector('meta[name="color-scheme"]');
    const themeColor = document.querySelector('meta[name="theme-color"]');
    const documentTheme = THEMES_BY_ID.get(theme);

    if (documentTheme === undefined) {
      return;
    }

    const paletteDocument = SYSTEM_PALETTES_BY_ID.get(activeSystemPalette);
    if (colorScheme !== null) {
      colorScheme.content =
        theme === "system" && paletteDocument !== undefined
          ? paletteDocument.dark
            ? "dark"
            : "light"
          : documentTheme.colorScheme;
    }
    if (themeColor !== null) {
      const deviceUsesDark =
        systemColorQuery !== null && systemColorQuery.matches;
      const useDark =
        theme === "system"
          ? paletteDocument?.dark ?? deviceUsesDark
          : deviceUsesDark;
      themeColor.content = useDark
        ? documentTheme.themeColors.dark
        : documentTheme.themeColors.light;
    }
  }

  function updatePicker(theme) {
    const picker = document.querySelector("#theme-select");
    if (picker !== null && picker.value !== theme) {
      picker.value = theme;
    }
  }

  function updateSystemPalettePicker() {
    const wrapper = document.querySelector("#system-palette-picker");
    const picker = document.querySelector("#system-palette-select");
    if (wrapper !== null) {
      wrapper.hidden = activeTheme !== "system";
    }
    if (picker !== null && picker.value !== activeSystemPalette) {
      picker.value = activeSystemPalette;
    }
  }

  function updateManagedStylesheet(theme) {
    MANAGED_THEME_LINKS.forEach((link, identifier) => {
      if (identifier === theme) {
        if (!link.hasAttribute("href")) {
          link.setAttribute(
            "href",
            link.dataset.sdsctlManagedThemeHref,
          );
        }
        link.media = "all";
      } else {
        link.media = "not all";
      }
    });
  }

  function applyTheme(value, persist) {
    const theme = normalizeTheme(value);
    activeTheme = theme;
    updateManagedStylesheet(theme);
    document.documentElement.dataset.theme = theme;
    updateMetadata(theme);
    updatePicker(theme);
    updateSystemPalettePicker();

    if (persist) {
      try {
        window.localStorage.setItem(STORAGE_KEY, theme);
      } catch {
        // Browser-local persistence is optional; applying the theme still succeeds.
      }
    }

    return theme;
  }

  function applySystemPalette(value, persist) {
    const palette = normalizeSystemPalette(value);
    activeSystemPalette = palette;
    document.documentElement.dataset.systemPalette = palette;
    updateMetadata(activeTheme);
    updateSystemPalettePicker();

    if (persist) {
      try {
        window.localStorage.setItem(SYSTEM_PALETTE_STORAGE_KEY, palette);
      } catch {
        // Browser-local persistence is optional; applying the palette still succeeds.
      }
    }

    return palette;
  }

  MANAGED_THEME_LINKS.forEach((link, identifier) => {
    link.addEventListener("error", () => {
      link.removeAttribute("href");
      if (activeTheme === identifier) {
        applyTheme("system", true);
      }
    });
  });

  const storedSystemPalette = readStoredSystemPalette();
  const storedSelection = readStoredTheme();
  activeSystemPalette = applySystemPalette(
    storedSystemPalette.palette,
    storedSystemPalette.repair,
  );
  activeTheme = applyTheme(storedSelection.theme, storedSelection.repair);

  if (
    systemColorQuery !== null &&
    typeof systemColorQuery.addEventListener === "function"
  ) {
    systemColorQuery.addEventListener("change", () => {
      if (activeTheme === "system") {
        updateMetadata(activeTheme);
      }
    });
  }

  window.sdsctlTheme = Object.freeze({
    choices: THEMES,
    current: () => activeTheme,
    currentSystemPalette: () => activeSystemPalette,
    select: (value) => applyTheme(value, true),
    selectSystemPalette: (value) => applySystemPalette(value, true),
    systemPaletteChoices: SYSTEM_PALETTES,
  });
})();
