(() => {
  "use strict";

  const ENDPOINT = "api/v1/favorites/profiles";
  const PROTOCOL = "sdsctl.favorites-profiles";
  const MAX_PROFILES = 64;
  const PROFILE_KEYS = Object.freeze([
    "compatibility",
    "created_at_ns",
    "document_count",
    "name",
    "profile_id",
    "revision",
    "revision_count",
    "snapshot_bytes",
    "snapshot_sha256",
    "updated_at_ns",
  ]);
  const COMPATIBILITY = new Set(["sds100", "sds200", "shared"]);
  const root = document.documentElement;
  const picker = document.querySelector("#favorites-profile-select");
  const count = document.querySelector("#favorites-profile-count");
  const sourceState = document.querySelector("#favorites-source-state");
  const selectionState = document.querySelector("#favorites-selection-state");
  const selectionMessage = document.querySelector("#favorites-selection-message");
  const selectedName = document.querySelector("#favorites-selected-name");
  const selectedRevision = document.querySelector("#favorites-selected-revision");
  const metrics = Object.freeze({
    compatibility: document.querySelector("#favorites-metric-compatibility"),
    documents: document.querySelector("#favorites-metric-documents"),
    revision: document.querySelector("#favorites-metric-revision"),
    storage: document.querySelector("#favorites-metric-storage"),
  });
  let profiles = Object.freeze([]);

  function exactKeys(value) {
    return Object.keys(value).sort().join("\u0000") === PROFILE_KEYS.join("\u0000");
  }

  function positiveInteger(value) {
    return Number.isSafeInteger(value) && value > 0;
  }

  function nonnegativeInteger(value) {
    return Number.isSafeInteger(value) && value >= 0;
  }

  function nonnegativeTimestamp(value) {
    return Number.isInteger(value) && value >= 0;
  }

  function decodeProfile(value) {
    if (value === null || typeof value !== "object" || Array.isArray(value) || !exactKeys(value)) {
      throw new Error("invalid profile summary");
    }
    if (typeof value.profile_id !== "string"
        || !/^[A-Za-z0-9_-]{24,128}$/.test(value.profile_id)
        || typeof value.name !== "string"
        || value.name.length < 1
        || value.name.length > 80
        || value.name.trim() !== value.name
        || !COMPATIBILITY.has(value.compatibility)
        || !positiveInteger(value.revision)
        || !positiveInteger(value.revision_count)
        || value.revision_count < value.revision
        || !nonnegativeInteger(value.document_count)
        || !nonnegativeInteger(value.snapshot_bytes)
        || typeof value.snapshot_sha256 !== "string"
        || !/^[0-9a-f]{64}$/.test(value.snapshot_sha256)
        || !nonnegativeTimestamp(value.created_at_ns)
        || !nonnegativeTimestamp(value.updated_at_ns)) {
      throw new Error("invalid profile summary");
    }
    return Object.freeze({...value});
  }

  function status(element, state, text) {
    if (element === null) return;
    element.dataset.state = state;
    element.textContent = text;
  }

  function text(element, value) {
    if (element !== null) element.textContent = value;
  }

  function formatBytes(value) {
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
  }

  function renderSelection(profile) {
    root.dataset.workspaceState = "profile-summary";
    status(selectionState, "clean", "Summary selected");
    text(selectedName, profile.name);
    text(selectedRevision, `Revision ${profile.revision}`);
    text(metrics.compatibility, profile.compatibility.toUpperCase());
    text(metrics.documents, String(profile.document_count));
    text(metrics.revision, `${profile.revision} of ${profile.revision_count}`);
    text(metrics.storage, formatBytes(profile.snapshot_bytes));
    text(
      selectionMessage,
      "Current server-side metadata only. Exact Favorites bytes and editor actions remain unavailable.",
    );
  }

  function renderEmpty() {
    root.dataset.workspaceState = "profiles-empty";
    status(selectionState, "unavailable", "No profiles");
    text(selectedName, "No saved profile selected");
    text(selectedRevision, "Revision —");
    Object.values(metrics).forEach((element) => text(element, "—"));
    text(sourceState, "No saved profiles are available on this server.");
    text(selectionMessage, "Create or import remains deliberately unavailable in this read-only slice.");
  }

  function renderFailure() {
    root.dataset.workspaceState = "profiles-unavailable";
    status(selectionState, "unavailable", "Unavailable");
    status(count, "unavailable", "—");
    text(sourceState, "Saved profiles are unavailable. Try again later.");
    text(selectionMessage, "No profile data was retained in this browser.");
    if (picker instanceof HTMLSelectElement) {
      picker.replaceChildren();
      const option = document.createElement("option");
      option.textContent = "Profiles unavailable";
      picker.append(option);
      picker.disabled = true;
    }
  }

  function renderProfiles(values) {
    profiles = Object.freeze(values);
    text(count, String(profiles.length));
    if (!(picker instanceof HTMLSelectElement)) throw new Error("profile picker unavailable");
    picker.replaceChildren();
    if (profiles.length === 0) {
      const option = document.createElement("option");
      option.textContent = "No saved profiles";
      picker.append(option);
      picker.disabled = true;
      renderEmpty();
      return;
    }
    profiles.forEach((profile, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `${profile.name} — ${profile.compatibility.toUpperCase()}`;
      picker.append(option);
    });
    picker.disabled = false;
    text(sourceState, "Select a saved server-side profile to review its current summary.");
    renderSelection(profiles[0]);
  }

  async function loadProfiles() {
    try {
      const response = await fetch(ENDPOINT, {
        cache: "no-store",
        credentials: "same-origin",
        headers: {Accept: "application/json"},
      });
      if (!response.ok) throw new Error("profile request failed");
      const payload = await response.json();
      if (payload === null
          || typeof payload !== "object"
          || Array.isArray(payload)
          || Object.keys(payload).sort().join("\u0000") !== "profiles\u0000protocol\u0000version"
          || payload.protocol !== PROTOCOL
          || payload.version !== 1
          || !Array.isArray(payload.profiles)
          || payload.profiles.length > MAX_PROFILES) {
        throw new Error("invalid profile response");
      }
      renderProfiles(payload.profiles.map(decodeProfile));
    } catch {
      renderFailure();
    }
  }

  if (picker instanceof HTMLSelectElement) {
    picker.addEventListener("change", () => {
      const index = Number(picker.value);
      if (Number.isSafeInteger(index) && index >= 0 && index < profiles.length) {
        renderSelection(profiles[index]);
      }
    });
  }

  void loadProfiles();
})();
