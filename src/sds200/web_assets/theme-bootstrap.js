"use strict";

(() => {
  const STORAGE_KEY = "sdsctl.web.theme";
  const THEME_DOCUMENTS = Object.freeze(__SDSCTL_WEB_THEME_MANIFESTS__);
  const THEMES = Object.freeze(THEME_DOCUMENTS.map((theme) => theme.id));
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
    return THEMES.includes(value) ? value : "system";
  }

  function readStoredTheme() {
    try {
      return normalizeTheme(window.localStorage.getItem(STORAGE_KEY));
    } catch {
      return "system";
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

  function updateManagedStylesheet(theme) {
    MANAGED_THEME_LINKS.forEach((link, identifier) => {
      link.media = identifier === theme ? "all" : "not all";
    });
  }

  function applyTheme(value, persist) {
    const theme = normalizeTheme(value);
    activeTheme = theme;
    updateManagedStylesheet(theme);
    document.documentElement.dataset.theme = theme;
    updateMetadata(theme);

    if (persist) {
      try {
        window.localStorage.setItem(STORAGE_KEY, theme);
      } catch {
        // Browser-local persistence is optional; applying the theme still succeeds.
      }
    }

    return theme;
  }

  activeTheme = applyTheme(readStoredTheme(), false);

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
