"use strict";

(() => {
  const STORAGE_KEY = "sdsctl.web.theme";
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

  function updateMetadata(theme) {
    const colorScheme = document.querySelector('meta[name="color-scheme"]');
    const themeColor = document.querySelector('meta[name="theme-color"]');
    const documentTheme = THEMES_BY_ID.get(theme);

    if (documentTheme === undefined) {
      return;
    }

    if (colorScheme !== null) {
      colorScheme.content = documentTheme.colorScheme;
    }
    if (themeColor !== null) {
      themeColor.content =
        systemColorQuery !== null && systemColorQuery.matches
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

    if (persist) {
      try {
        window.localStorage.setItem(STORAGE_KEY, theme);
      } catch {
        // Browser-local persistence is optional; applying the theme still succeeds.
      }
    }

    return theme;
  }

  MANAGED_THEME_LINKS.forEach((link, identifier) => {
    link.addEventListener("error", () => {
      link.removeAttribute("href");
      if (activeTheme === identifier) {
        applyTheme("system", true);
      }
    });
  });

  const storedSelection = readStoredTheme();
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
    select: (value) => applyTheme(value, true),
  });
})();
