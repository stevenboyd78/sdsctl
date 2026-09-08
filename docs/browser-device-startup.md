# Experimental managed-browser startup

Status: **unreleased, isolated acceptance only—not a production installation
procedure**. Use a matching reviewed development build. The released
[manual-login kiosk](browser-kiosk.md), TUI services and Home Assistant defaults
remain unchanged. Do not replace either production display with this experiment.

This step adds a foreground launcher for an already registered experimental
browser. It opens a startup screen, waits for a verified short-lived device
session, then opens the server's display-only entry. It does not save or type a
dashboard password. The per-device credential remains in the private native
profile, outside page scripts and browser storage.

## Before starting

Complete the [private profile](browser-device-profile.md),
[canonical bundle](browser-device-bundle.md) and
[new-directory registration](browser-device-first-run.md) steps first, using
fictional credentials in an isolated lab. Keep their exact absolute paths,
Python installation and reviewed public key. An existing registered profile may
be used or paused; starting it must never reset that state.

Run as the same non-root Linux user in an existing graphical session. Select an
absolute Chromium executable, such as `/usr/bin/chromium`. The launcher requires
Chromium version 120 or newer; that is a minimum capability check, not a promise
that every distribution works. The exact headed first-start fixture has passed
with Chromium 151.0.7922.173 and 152.0.7977.75 on Debian 13/Linux/aarch64.

Google Chrome-branded builds and Firefox are not supported by this experimental
launcher. Chrome removed the `--load-extension` flag from branded builds starting
with version 137; the Chromium team says Chromium retains it. Do not add policy,
feature overrides or certificate/sandbox bypasses to force an unsupported
browser to load the bundle. See the
[Chromium announcement](https://groups.google.com/a/chromium.org/g/chromium-extensions/c/1-g8EFx2BBY).

The bundle adds Chromium's `tabs` permission to recognize the exact requested
startup/setup tab if command-line navigation finishes before the unpacked
extension loads. Its retry code queries only those two fixed extension URLs,
checks that no valid extension document already exists, rechecks the selected
tab, and permits at most three retries per tab/worker, with bounded startup
rescans at one and five seconds. It does not open another tab or touch a
dashboard, password page or unrelated URL. This permission can technically read
tab metadata, which is another reason to use only a dedicated lab browser, never
a personal profile. See the [Tabs API permissions](https://developer.chrome.com/docs/extensions/reference/api/tabs#permissions).

Native-helper certificate validation and browser trust are **separate**. Both
must verify the exact HTTPS server identity. Correct private-IP certificates,
DNS names and bracketed IPv6 are supported; internal DNS and a reverse proxy are
not required. This launcher does not import a certificate or modify a trust
store. The acceptance fixture's temporary NSS mount is test isolation, not a
production trust-installation procedure.

## 1. Check an existing registration without launching

For the example account `display`:

```sh
sdsctl browser-device-start --experimental --check \
  --directory /home/display/sdsctl-browser-lab/chromium-data \
  --bundle /home/display/sdsctl-browser-lab/review-bundle \
  --profile /home/display/sdsctl-browser-lab/native-profile \
  --public-key /home/display/sdsctl-browser-lab/extension.pub.pem \
  --browser /usr/bin/chromium
```

The check validates the completion receipt, exact native-host manifest,
canonical bundle bytes, installed interpreter, identities and private file
protections. It accepts a structurally valid used ledger but does not change its
pause, error, retry or clock state. It neither starts/probes Chromium nor sends a
native authentication request. Public-key validation uses the existing bounded
OpenSSL check. Success is **offline validity, not server login or browser trust**.

The tool does not parse Chromium's internal storage to guess whether first-run
setup succeeded. The extension owns that decision. A changed runtime/bundle or
receipt is refused, not repaired. Retain the original files for review; do not
edit receipt hashes, copy browser state or delete the recovery ledger to bypass
a refusal. The current bundle version is `0.0.4` and must match this
runtime. Do not edit a bundle in place. The
[stopped-browser lifecycle candidate](browser-device-service.md) stages a new
bundle and switches its registration without copying or resetting browser state;
real-Chromium update acceptance is still required.

## 2. Choose setup or normal startup explicitly

Use the same arguments above, replacing `--check` with **`--setup`** only when you
intend to open the first-run confirmation page. Verify its server/device/extension
identity, select the checkbox and initialize once. Setup itself does not log in;
a later worker/browser start can perform ordinary recovery. Existing or
interrupted setup is never automatically overwritten.

For ordinary startup, omit both `--check` and `--setup`. The command stays in the
foreground while its own Chromium child is running. It opens only the fixed
extension startup page, not an arbitrary supplied URL or a password form.

| Startup condition | What the screen does |
| --- | --- |
| First-run state absent | Requests explicit setup; does not claim the profile |
| Recovery permitted but no verified session installed | Waits; `active` alone is not login proof |
| Valid installed session | Opens the fixed `/device-display` server entry |
| Server unavailable or a bounded retry pending | Shows waiting/recovery status |
| Saved pause or sign-out | Stays paused; never resumes automatically |
| Invalid setup, rejected credential or TLS error | Shows a fixed review message; no secret details |

Readiness is a process-local acknowledgement of completed cookie installation,
with an actual-cookie check. No token is returned to the startup page. After a
worker restart it waits for a new successful renewal, possibly until the saved
deadline; it does not treat an old cookie or persisted `active` mode as proof.
Status checks do not drive authentication or bypass backoff.

Cold startup can also wait for Chromium's cookie store before cleanup and status
reporting finish. The isolated Chromium qualification fixture measured an initial
cookie read of about 23 seconds; its terminal-startup checks allow at most 60
seconds and still require the exact saved state, absent cookie and visible
terminal message. This is not a universal browser startup-time guarantee. Do not
reset a profile or weaken browser security settings to shorten that wait.

The opt-in server factory checks device authority again at `/device-display`.
An extension-to-website navigation is cross-site: this exact top-level entry
serves only the public waiting shell on that first request, even with a cookie.
Its same-origin refresh must pass the ordinary device authorization checks.
Cross-site API reads, frames and mutation requests remain denied.
An absent/expired session yields a no-store waiting page with a five-second
retry, never a manual/operator login. An authorized request serves the display
shell under that URL. The dashboard checks renewed device sessions rather than
applying the original manual-login expiry timer. If authorization is lost it
returns to the guarded entry. Scanner control, audio, recordings and management
remain forbidden; manual/operator routes retain their existing behavior.

## Closing and failure behavior

Normal browser close and an interrupted foreground launcher return `0`; an
abnormal browser exit returns `75`. Unsafe configuration, unavailable graphical
session, unsupported browser or ownership failure returns `78`. Argument errors
or missing `--experimental` return `2`. Launcher errors are fixed/redacted;
browser output is not copied into application logs.

Only one launcher may own the dedicated directory. Its mode-`0600` lock file is
retained after close but the OS lock is released. Existing Chromium
`SingletonLock`, `SingletonSocket` or `SingletonCookie` markers are refused,
including stale ones after a crash. **No lock is deleted, another browser killed,
or profile repaired automatically.** This deliberately conservative behavior
still needs a separately reviewed abrupt-power-loss recovery policy.

A retained maintenance guard normally blocks registration/startup. The internal
[paused-only guard release](browser-device-resume.md#paused-only-guard-release)
candidate permits an ordinary **paused** start only when its separate committed
journal and all original supervised maintenance evidence validate. Neither the
guard nor its evidence is removed. Missing, changed, prepared or inconsistent
completion remains blocked; there is no ignore-guard flag and release does not
resume sign-in. This is not yet a public maintenance/deployment command.

No service is installed or enabled. The command does not restart itself after a
clean close, change ports/firewalls, enroll/rotate a device, install trust or
modify Home Assistant. A separate [server configuration candidate](browser-device-server.md)
wires the existing authority to the CLI/App behind an explicit experimental opt-in.
The [service lifecycle candidate](browser-device-service.md) adds inert user-unit
preparation, a bounded restart policy and explicit stopped-browser maintenance.
Accepted distribution/updates, explicit replacement/resume, live service behavior
and physical multi-display/server outage tests remain separate gates.

## Acceptance boundaries

### Exact headed first-start qualification

`scripts/experimental/qualify_browser_first_start.py` exercises the installed
foreground CLI with the selected distribution Chromium executable and its normal
arguments, on an authenticated private Xvfb display. It uses neither a browser
wrapper nor headless/CDP flags. The bounded extension-entry retry handles initial
registration timing; the fixture never manually refreshes or navigates the page.
It records the actual arguments of the CLI's own browser child as well as the
expected arguments, visible fixture text and screenshots.

The small and HDMI Pi qualification covers six consecutive browser launches:

1. A new registered profile shows that first-run setup is required.
2. Explicit `--setup` opens the confirmation form, but closing it without consent
   leaves the native profile unchanged and unclaimed.
3. Normal restart still requires setup; page visits are not authorization.
4. Trusted keyboard confirmation saves setup and claims the native profile once,
   without making an authentication connection.
5. After deliberate local suspension of the **fictional** native profile while
   stopped, ordinary startup stays paused without an authentication connection.
6. Another browser restart preserves that pause.

A fixture-owned loopback socket counts connections throughout; zero are required
for this matrix. No server implements authentication and no browser/native state
is injected through a debugger or seed extension. Browser initialization happens
only through the real generated setup form. Native suspension in step 5 is a
deliberate test operation, **not** evidence for dashboard sign-out or guard release.
Every close must return zero and leave no Chromium Singleton markers; none are
deleted. The canonical bundle remains byte-identical, and the native profile is
unchanged in the no-consent and paused cases.

The fixture has its own XDG directories, D-Bus session and encrypted disposable
keyring. Text copying occurs only within its private X server, never on a user's
desktop or password form. No trust store, sandbox setting, production TUI, Home
Assistant setting or service configuration is changed. See the
[fixture instructions](../scripts/experimental/README.md#exact-headed-first-start-qualification).
This establishes the first-start/consent/pause boundary on those tested versions;
it does not replace authentication/TLS, cold-boot, physical-layout or Firefox/WPE
qualification.

### Server-connected recovery qualification

The [experimental harness](../scripts/experimental/README.md) has a `startup`
mode that uses the installed foreground CLI, actual canonical extension/setup
form, native helper and ASGI server. Fixture-only headless/CDP flags are supplied
by its private test executable, never by the product command. Normal Chromium
sandboxing and verified loopback TLS remain enabled. No recovery state or cookie
is injected, and no production credential or service is used.

The startup qualification matrix runs normal startup plus slow/truncated
responses, an interrupted TLS handshake, server restart, worker restart,
revocation and native-helper CA/name rejection separately for IPv4 and DNS;
healthy IPv6 is a separate case. Successful recovery cases also require real
dashboard sign-out and a fresh launch that remains paused. TLS/revocation cases
must retain their terminal state without another authentication exchange.
An interrupted page-status read during managed navigation is not a pass: the
harness retries within a deadline until it observes the exact expected HTTP
status. Native-helper CA/name rejection does not substitute for an independent
browser-only trust-failure test.

Deterministic tests also cover offline inspection, unsafe/competing locks,
close/crash/signal handling, startup sender validation, pending authentication,
pause races, expired sessions, and managed renewal versus manual-login behavior.
These checks do not prove physical display appearance, live scanner data or
recovery after an actual power outage. Do not use them as release acceptance.
