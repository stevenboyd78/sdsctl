// Trusted extension page only. No passwords, credential reads or page forwarding.
export function connectBrowserSetupPage({document, window, runtime}) {
  if (window !== window.top || window.location.href !== runtime.getURL("setup.html"))
    throw new Error("setup");
  const form = document.getElementById("setup-form");
  const confirm = document.getElementById("confirm");
  const button = document.getElementById("initialize");
  const notice = document.getElementById("notice");
  let attempted = false;
  form.addEventListener("submit", event => {
    event.preventDefault();
    if (!event.isTrusted || !confirm.checked || attempted) return;
    attempted = true;
    button.disabled = true;
    notice.textContent = "Saving first-run setup…";
    void (async () => {
      try {
        const response = await runtime.sendMessage({action: "initialize"});
        notice.textContent = response?.mode === "ready"
          ? "Setup saved. No login was attempted. Automatic sign-in is enabled for the next browser or extension start."
          : response?.mode === "paused"
            ? "Automatic sign-in remains paused. Ask your administrator to review this display."
            : response?.mode === "setup_refused"
              ? "Setup refused: existing state or a prior attempt must be reviewed. Nothing was reset."
              : "Setup could not be confirmed. Keep this profile for administrator review; do not delete its state or retry initialization.";
      } catch {
        notice.textContent = "Setup acknowledgement was lost. Keep this profile for administrator review; do not delete its state or retry initialization.";
      }
    })();
  });
}
