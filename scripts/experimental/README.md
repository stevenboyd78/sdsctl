# Browser-device local experiment

These files are **test fixtures, not production enrollment software**. Do not
register this helper on a Pi or point it at Home Assistant. The fixture creates
its own loopback HTTPS service and fictional credentials; it does not load user
passwords, TUI profiles or Home Assistant keys.

The proposed MV3 extension sends a fixed native request. The helper authenticates
over verified TLS and returns a limited session, not the device credential. The
extension—not the browser automation driver—would install the HttpOnly cookie.
The harness's browser assertions are testing instrumentation, not a CDP login
implementation.

## Run prerequisites

- Linux with a working Chromium sandbox and `bwrap`.
- Python 3.11+, modern Node, OpenSSL and NSS `certutil`.
- An existing Playwright module and its matching full test Chromium download.
- A private staging directory created using `mktemp -d`.
- If the legacy `~/.pki/nssdb` exists, the harness overlays it only inside the
  child process's mount namespace. Otherwise it uses an isolated XDG data path.
  It does not add the fictional CA to the user's actual database.
- `SDSCTL_PROBE_CHROMIUM=/absolute/path/to/chromium` selects an already installed
  Chromium (used for the ARM64 Pi test), without installing another package.

## Integrated recovery acceptance

`audit_browser_recovery.mjs` uses the actual `create_web_dashboard_app`, device
authority/session middleware and native helper with a sandboxed Chromium
extension. Its Python driver has no scanner daemon and generates only fictional
credentials. It uses real clocks and alarms; the driver never installs cookies
or supplies authentication to the page. This is still a test harness, not a
production enrollment installer or an on-screen Pi acceptance result.

```sh
node scripts/experimental/audit_browser_recovery.mjs \
  /absolute/private/staging \
  /absolute/path/to/playwright/index.mjs \
  /absolute/path/to/chromium \
  /absolute/path/to/certutil \
  /absolute/path/to/python \
  deadline ip
```

The Python runtime needs the project's web dependencies. The five paths must
be absolute and the existing staging directory must be owned by the current
user with mode 0700. Choose `deadline`, `truncated`, `tls-eof`, `server-restart`,
`worker-restart` or `revoke`, followed by `ip` or `dns` (loopback `localhost`).
Each run retains a uniquely named fixture and a redacted `result.json` on success.
It closes its own browser/server; it does not alter an installed TUI or service.
Allow several minutes for actual renewal/retry alarms. A sandbox launch failure
is a blocked test, not acceptance: do not disable sandboxing or TLS verification.

The documented Chrome error `Native host has exited.` denotes an interrupted
native pipe. Only that error is mapped to a saved, one-minute retry. Missing or
forbidden hosts, protocol errors and invalid responses still fail closed. See
[Chrome native-messaging errors](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging#debug-native-messaging).
Native retry state survives worker recreation and clock rollback; explicit
suspend/sign-out always takes precedence.

## Earlier native-message proof

```sh
PLAYWRIGHT_BROWSERS_PATH=/absolute/private/browser-cache \
  node scripts/experimental/audit_browser_device.mjs \
  /absolute/private/staging \
  /absolute/path/to/playwright/index.mjs \
  /absolute/path/to/certutil
```

Append `--helper-only` to run the 18 native-helper cases without launching a
browser. That mode explicitly reports that browser checks were not run. It still
requires the tools above for fixture generation.

Each invocation retains a uniquely named `run-*` directory inside the supplied
stage. It contains fictional secrets, test certificate keys, generated helper
registration and an isolated browser profile. Never commit these outputs. They
are disposable but retained intentionally for inspection; the harness does not
recursively delete user-supplied paths. The fixture server and browser are closed
after assertions or failure.

## Evidence and remaining limits

On 2026-09-05, 18 native-helper cases passed on the workstation. Browser
startup failed because the downloaded Chromium had no usable sandbox under the
host policy. Sandbox disabling and global policy changes were not attempted.

The helper cases cover valid verified-TLS authentication, unexpected request
fields/caller, unsafe permissions, wrong credential, unrelated CA rejection and
successful CA restoration; redirects, oversized/duplicate/malformed response
fields, expired or excessive expiry, unexpected role, hostname mismatch,
symlinked secret, stalled pipe deadline and successful authentication afterwards.
Output checks reject the device credential in stdout and any stderr. They also
test that an invalid inherited HTTPS proxy is ignored.

The same helper cases and browser assertions subsequently passed in a temporary
headless fixture on the HDMI Pi with Chromium 152.0.7977.75, sandbox enabled and
TLS verification enabled. Native-host discovery inside the dedicated profile,
worker handoff, cookie flags, unauthenticated and display-only fixture access,
page cookie/storage visibility, fixture session invalidation and native-host
allowlist enforcement against a second extension passed. This is not a general
proof against credential leakage or production authorization acceptance.

Browser restart followed by navigation to a fixed extension startup entry also
obtained a new session after the fixture invalidated the old one. The test driver
navigates that entry, which a future launcher must open; it never installs the
cookie or provides a credential through browser automation. Chromium `onStartup`
alone did not reliably invoke authentication in this harness. Do not claim a
complete managed-service reboot/outage test or automatic renewal while an existing
dashboard remains open. Those still require implementation and acceptance.

The fixture has no real persistent enrollment store, device generation checks,
stream revocation, administrator API, sign-out pause, comprehensive service-worker
lifecycle testing or production packaging. It enforces fixture session expiry,
but has not tested scheduled renewal. Its caller argument
check is not security against another process running as the same Unix user.
The generated extension key is stable only within each fixture, not a release
identity. Configuration is generated and trusted by the harness; real config
validation and adversarial file-replacement tests remain required.

Cookies do not isolate TCP ports. The fixture's exact Host check does not prevent
a browser sending a cookie to another HTTPS port on the same host. This must be
addressed in the deployment threat model before production enrollment ships.
The final HDMI Pi run explicitly observed the fictional session cookie at a
second loopback HTTPS service and reported `cookie_port_isolation: false`.

## Persistent recovery coordinator (separate, not enabled)

`audit_browser_bundle.mjs` checks the generated review bundle using an **installed
candidate wheel**, fictional native-profile inputs and a new isolated Chromium
user-data directory. Pass an existing private stage, Playwright module path,
Chromium executable and installed Python interpreter as four absolute paths;
`--help` describes the prerequisites without creating anything. It requires a
working Chromium sandbox. It registers the generated native manifest only inside
its unique test profile, checks the key-derived extension ID, verifies empty
browser state stays rejected, and sends a native `status` request. It never
authenticates or visits a dashboard and retains the fixture/result. This is a
packaging smoke, not TLS, sign-out, renewal or physical-outage acceptance. The
other harnesses below cover distinct runtime boundaries with fictional servers.

`src/sds200/browser_assets/browser_device_recovery.mjs` is the coordinator for the actual
native-helper protocol, not a replacement silently installed into the earlier
fixture. The modules now ship as canonical Python package assets; the entries
here re-export them for Node tests. Browser fixtures copy the canonical assets.
The opt-in [review bundle](../../docs/browser-device-bundle.md) stages a manifest
and native wrapper but does not register or start them. Do not install it on
production displays yet.

Run the dependency-free deterministic JavaScript contract tests with:

```sh
node --test scripts/experimental/test_browser_device_recovery.mjs
node --test scripts/experimental/test_browser_device_logout.mjs
```

The pytest wrapper `tests/test_browser_device_extension.py` includes those tests
and a real Node-to-Python native-pipe/persistent-pause integration test. No real
browser, device credential or Home Assistant connection is used. The coordinator
tests cover alarms, ordered cookie installation/removal, persisted sign-out intent,
generation races, unsafe state and the Chrome adapter's message/cookie checks.

See the [enrollment design](../../docs/managed-display-enrollment-design.md) for
the exact limits. `browser_device_logout.mjs` adds an opt-in two-stage sign-out
bridge and document-bound completion tickets; it is not registered or deployed.
The existing dashboard sign-out form is not connected in any installed build.
Real-browser interruption evidence is recorded below. Local pause and cookie
removal are not proof of server-side stream revocation. No resume message exists.

## Real-browser logout harness (isolated fixture)

`audit_browser_logout.mjs` registers the recovery coordinator and logout bridge
only inside a uniquely named temporary fixture. Run
`node scripts/experimental/audit_browser_logout.mjs --help` for explicit runtime
paths and scenario selection. It requires a private mode-0700 stage, Playwright,
Chromium, certutil, Python, Linux bwrap and OpenSSL. Credentials and HTTPS services
are fictional and loopback-only. The driver never installs authentication cookies.
Native messaging invokes the actual Python helper, but the HTTPS server is a
fictional contract fixture, not the production ASGI application.

The intended scenarios are confirmed drain, pending drain, lost logout response
and worker termination during the logout POST. They check browser-generated
request headers, cookie cleanup, persisted pause and restart without reauthentication.
Cookie-operation interruption is exercised by the additional scenarios below.
A scenario is not accepted until its assertions run and
emit a passed summary.

On 2026-09-06, direct sandbox-enabled Playwright/Chromium launch passed on the
workstation, but the `drained` fixture failed at browser launch inside bwrap with
`No usable sandbox!`. None of its browser acceptance assertions ran. The extra
namespace isolates the fictional CA database from the user's normal NSS database;
the existing legacy NSS database takes precedence over `XDG_DATA_HOME`, so changing
that variable alone is not an isolation solution. No sandbox or TLS bypass, real
trust-store modification, Home Assistant access or production Pi change was made.
Private fixture outputs were retained for inspection. That local attempt needed
a compatible isolated host before real-browser acceptance could proceed.

The subsequent user-approved HDMI Pi run used Chromium 152.0.7977.75 with
sandboxing and TLS verification enabled. Confirmed drain, pending drain and lost
response passed, including real same-origin fetch metadata, HttpOnly cookie use
and removal, native-helper persistent pause and browser restart without another
authentication. The worker-stop case also passed with an observed stopped
worker while the server response was pending, followed by cookie cleanup and a
restart that remained paused. The test emits a redacted `result.json` only after all
assertions for that scenario pass.

The generated worker now registers handlers synchronously, without top-level
await. Trusted fixture provisioning is a separate step: an unprovisioned worker
must fail closed before the harness saves the fictional recovery record through
its own extension page and restarts the browser. No worker silently creates a
missing record. See Chrome's [service-worker migration guidance](https://developer.chrome.com/docs/extensions/develop/migrate/to-service-workers).

Chromium retried the lost-response POST when the socket closed before headers.
The fixture recorded two wire requests but only one authorized pause; the second
request was rejected after the first paused the device. The bridge still showed
an unconfirmed outcome, removed the cookie and stayed paused after restart. This
is not a guarantee of exactly-once network delivery. Assertions distinguish wire
requests from authorized mutations and forbid new requests after restart.

These logout checks do not by themselves prove actual ASGI stream drain,
cookie-operation interruption, production packaging, renewal/expiry
navigation, or a physical whole-site outage. No production service, display
configuration, real credential or browser trust store is changed by this fixture.

## Real cookie-operation interruption (isolated fixture)

The same harness supports `cookie-set-stop`, `cookie-remove-stop` and
`cookie-set-stop-queued-recovery`. All three passed on the HDMI Pi with sandboxed
Chromium 152.0.7977.75 and verified TLS. A test-only wrapper lets the driver choose
when the coordinator dispatches an actual `chrome.cookies` operation; it neither
supplies a cookie nor fabricates the API result. The fixture temporarily SIGSTOPs
only its own network subprocess, verifies the API remains pending and local pause
is durable, then stops the extension worker through CDP. The network subprocess
is resumed in a finally block, including on failed assertions.

Signal targets require exact private profile, same UID, parent chain, CDP process
identity and unchanged Linux process start time/arguments. Chromium rewrites some
process titles with spaces instead of NUL argument separators; both forms are
checked. Fixture profile paths with whitespace are rejected as ambiguous. The
driver uses the documented [CDP process inventory](https://chromedevtools.github.io/devtools-protocol/tot/SystemInfo/)
and [worker lifecycle events](https://chromedevtools.github.io/devtools-protocol/tot/ServiceWorker/).

The creation case observed a cookie completing after worker death, followed by
removal by recovery. The removal case recovered with no cookie. The queued-recovery
case restarted the worker and dispatched cleanup before the old write was
released. All cases rejected the protected fixture read after recovery (401),
kept native/browser state paused through a full browser restart and performed only
one authentication. No server logout was invoked: this is local cleanup evidence,
not server revocation. A cookie can exist in the interval before recovery, so do
not claim instantaneous invalidation merely because the worker stopped.

The six additional deterministic hook/parser tests verify fixed operation names,
real-API result/rejection forwarding and exact process-argument token matching.
They complement, rather than replace, the physical host's real-browser runs.
