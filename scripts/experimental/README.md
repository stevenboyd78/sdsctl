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

## Exact headed first-start qualification

`qualify_browser_first_start.py` checks the ordinary installed foreground CLI on
a private virtual display with a fictional profile. Unlike the server-connected
Playwright fixtures below, it uses the distribution Chromium directly: no extra
browser flags, debugging endpoint, seed extension or manual refresh. Do not run
it against a real browser directory or a user's graphical session.

Required: Linux, a candidate installed into its own virtual environment, Chromium
120+, OpenSSL, Xvfb with `xvfb-run`/Xauthority, `libX11`, `libXtst`, `dbus-run-session`,
`gdbus` and `gnome-keyring-daemon`. No Node, Playwright or NSS trust installation is
needed for this matrix. Keep `browser_recovery_x11.py` and
`qualify_browser_recovery.py` next to the script; they supply test helpers only.

Create a **new empty private directory for each run**, using absolute paths
without whitespace for this argument-capture fixture, then run:

```sh
xvfb-run -a -s '-screen 0 1280x1024x24' \
  /absolute/candidate-venv/bin/python -I \
  scripts/experimental/qualify_browser_first_start.py \
  /absolute/new-private-case-directory /usr/bin/chromium
```

The script creates private XDG paths, an isolated D-Bus session and an encrypted
disposable keyring. It captures visible fictional page text via the private X11
clipboard, without inspecting browser storage or exposing a debugging port.
Its loopback listener only counts and closes connections; it implements no TLS
or authentication server, and success requires zero connections.

The matrix checks fresh startup, setup without consent, uninitialized restart,
explicit one-time setup confirmation, deliberate native pause and persisted
paused restart. Before setup confirmation it waits **55 seconds without browser
or native interaction**, allowing the worker to idle naturally. The first click
must succeed without a retry or manual reload; a warm worker alone is insufficient
acceptance. The [startup guide](../../docs/browser-device-startup.md#exact-headed-first-start-qualification)
defines the assertions and limits. Success writes `qualification-result.json`,
visible text, screenshots, and expected/observed browser arguments. An uncertain
or failed run is retained for review, never replayed over the same profile.
`private-runtime-path.txt` records the retained disposable runtime directory.
Only owned test processes are stopped; no production service is involved.

The separate September 9 small-Pi
[physical recovery and paused-restart acceptance record](../../docs/browser-device-recovery-acceptance.md)
documents two user-observed ordinary starts and a later persisted-state readback.
It keeps failed attempts, seeded pending state and the observer-only reload
distinct from normal startup acceptance. A five-second loading/error frame is
not sufficient evidence of persistent failure; inspect a settled bounded capture
before requesting physical confirmation. Do not replay a populated fixture,
preseed human-pass markers, or weaken product timers to obtain a result.

The same [acceptance record](../../docs/browser-device-recovery-acceptance.md#genuine-ordinary-resume-interruption-and-paused-recovery)
also records a later fresh ordinary setup/sign-out/resume case with a measured
lost-response fault, real paused recovery and two normal restarts on each side
of maintenance. That private qualification uses no seeded pending state; do not
substitute the synthetic harness above for it. Its readback uses GET-only APIs
in a read-only evidence sandbox, with full stopped-process/host/release proof in
the actual host namespace before and after. Later online session establishment
and credential replacement are still separate gates. Retain failed cases and
consumed operations; never replay them to obtain a pass.

## Installed server-command wiring

`audit_browser_server_wiring.py` tests the installed `sdsctl web` entry point,
not a replacement ASGI fixture. It requires Linux user/network namespaces,
`ip`, OpenSSL, and an installed candidate wheel with the web dependencies.
It deliberately has **no browser and no scanner daemon**; do not count it as
browser/Pi or scanner-data acceptance.

Build the wheel from the source distribution into a fresh private staging
directory. Install it into a separate target with `pip install --no-deps --target
/absolute/private/stage/installed /absolute/private/stage/dist/CANDIDATE.whl`,
using the development Python that already has the dependencies. Record the
outer network namespace inode with `stat -Lc '%i' /proc/self/ns/net`, then use
that number as the last argument below:

```sh
PYTHONPATH=/absolute/private/stage/installed \
  unshare --user --map-root-user --net /absolute/path/to/development/python \
  scripts/experimental/audit_browser_server_wiring.py \
  /absolute/private/fresh-audit-directory \
  /absolute/private/stage/installed OUTER_NETWORK_NAMESPACE_INODE
```

The audit directory must exist, have mode `0700`, and contain no prior `ip`,
`dns` or `ipv6` case directories. The script refuses the outer namespace or one
with configured/up interfaces before changing anything. It assigns fictional
private IPv4, Supervisor-peer and IPv6 addresses **only to its new namespace's
loopback interface**. No host interface, route, firewall, DNS or service changes
are made. If namespace creation fails, stop; do not run the fixture on the host
network instead.

The candidate's installed `browser-device-server --experimental create` command
prepares each case's empty authority and server JSON; the harness no longer
initializes those through an internal Python call. Its installed `check` command
must preserve bytes, modes, modification times and directory entries both before
enrollment and after revocation. A repeated `create` must fail without replacing
anything. The namespace maps the invoking account to root, also exercising the
server's support for same-account container-root preparation.

The test starts separate real Ingress/native processes, validates certificates
and hostnames normally, exercises one-time enrollment and disjoint origins,
rejects forwarded-peer impersonation, checks device-only permissions, confirms
duplicate-owner startup exits nonzero, restarts the native process, rejects its
old cookie, renews with the existing device credential, and confirms revocation
across the private owner socket. DNS routing is fixture-local while the TLS SNI,
hostname validation and Host header use the actual fictional DNS name.

Each case retains fictional private authority/TLS files and redacted child logs;
the success report contains no credentials. It stops only its own child processes
on success or failure. Rerun in a new audit directory, never overwrite a prior
authority. No global trust store or production Home Assistant/Pi is accessed.

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

## Document-bound page verification fixture

The same `audit_browser_recovery.mjs` command accepts `document-probe` with
`ip`, `dns` or `ipv6`. Each run creates a fresh fictional profile and private
loopback server; it never reuses a real display profile. It needs the existing
Playwright module, Chromium, certutil and Python paths shown above, with normal
browser sandboxing and verified TLS. Do not disable either if a prerequisite
fails. The fixture's temporary CA is confined to its browser mount namespace,
not imported into the user's trust database.

The existing native authentication fixture—not the automation driver—obtains
and installs the fictional session. The new probe then verifies the exact
Chromium-supplied document identity, refuses a replacement document, and closes
its owned tab when interrupted during a pending protected-page request. Success
requires unchanged native ledger bytes, browser storage, cookie and session
issuance count across the probe checks. The ordinary fixture sign-out/restart
checks still run afterwards. Results distinguish this browser I/O test from
new continuation-controller or installed-wheel acceptance. No new normal
worker role, Home Assistant access or physical display test is enabled.

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

`audit_browser_generated_recovery.mjs` exercises the **installed** profile import,
bundle creation and controlled registration commands, the real explicit setup
form, and then actual ASGI/native/Chromium authentication. It does not seed browser
recovery storage, inject cookies, build a fixture-only extension or import runtime
code from the checkout. Its adjacent `browser_recovery_server.py` uses the real
administrator issuance/attachment format with a fictional loopback authority.
This tests delivery format, not a real Home Assistant administrator session.

Run `node scripts/experimental/audit_browser_generated_recovery.mjs --help` for
the five absolute paths: a private existing stage, Playwright module, Chromium,
`certutil`, and the matching installed candidate Python (with `web` dependencies).
The server script must remain beside the harness. Optional scenario and identity
arguments select `healthy`, `deadline`, `truncated`, `tls-eof`, `server-restart`,
`worker-restart`, `revoke`, `bad-ca`, `bad-name`, `resume` or `resume-stale`,
and `ip`, `dns` or `ipv6`.
All network listeners bind only to loopback, including the IPv6 identity case.

The new resume scenarios use the actual generated two-step review page after
sign-out and confirmed administrator server resume. The happy case must verify a
fresh protected display-only session, retire the probe tab, and sign out again;
the stale case pauses server authority after review and must retain pending local
pause without a cookie or automatic retry. Eight isolated cases passed on both
ARM64 Pis using Chromium 152.0.7977.75 and 151.0.7922.173: `resume` over `ip`,
`dns` (`localhost`) and `ipv6`, plus `resume-stale` over `ip` on each host, all
through the installed foreground launcher (`startup`). Each case had zero setup
authentication exchanges and ended paused after browser restart. The happy cases
also verified protected access, exact probe-tab closure and a second sign-out.
Real-browser testing caught a harness that counted the original dashboard as a
probe, then a runtime bug retaining the first logout ticket after resume. The
final passes use page-identity checks and verified-resume logout-ticket retirement.
Earlier workstation sandbox failures remain blocked attempts, not passes.
These are headless fixture results, not physical-display or cold-boot acceptance.

The Linux browser sandbox and TLS verification stay enabled. `bwrap` mounts the
fictional NSS trust database only inside the browser's isolated mount namespace;
the real trust database is not modified. Every run uses a new private directory
and retains its files and redacted results. No production service, profile,
credential, browser policy or Home Assistant connection is involved.

Success requires zero authentication exchanges during explicit setup, followed by
verified recovery after browser restart, display-only permission checks and no
secret in page/browser storage. Ordinary scenarios then submit the real sign-out
form and verify server pause, cookie removal and no login after restart. Revoked
credentials and invalid issuer/name cases must stay rejected across restart.
The worker-stop scenario waits for server exchange counts without sending messages
to the stopped extension, so status polling cannot manufacture a successful
renewal wakeup. Deadlines and retry/renewal waits use real clocks and alarms.
For a retryable native failure, `active` means recovery remains allowed, not that
a session exists. The interruption assertions require persisted failure evidence,
no session cookie and a scheduled retry; the failure-ledger read is read-only.
This is authentication/recovery acceptance against a fictional no-scanner server,
not live scanner data, physical screen/cold-boot or combined power-outage proof.

`audit_browser_bundle.mjs` checks the generated review bundle using an **installed
candidate wheel**, fictional native-profile inputs and a new isolated Chromium
user-data directory. Pass an existing private stage, Playwright module path,
Chromium executable and installed Python interpreter as four absolute paths;
`--help` describes the prerequisites without creating anything. It requires a
working Chromium sandbox. It invokes the controlled registration command only for
its new unique test profile, checks the key-derived extension ID, verifies empty
browser state stays rejected, and sends a native `status` request. It never
authenticates or visits a dashboard and retains the fixture/result. This is a
packaging smoke, not TLS, sign-out, renewal or physical-outage acceptance. The
other harnesses below cover distinct runtime boundaries with fictional servers.

Append an identity selector (`ip`, `dns` or `ipv6`) and `first-run` to additionally
exercise the real password-free setup form and explicit confirmation. This mode
checks native revision 1-to-2 claiming without authentication, refuses a second
initialization, pauses before browser restart, and checks the pause survives.
It then deletes only the fictional fixture's recovery storage key as fault
injection, restarts, and confirms initialization remains blocked. This deletion
is not a recovery instruction. Desktop/small-page screenshots and all fixture
outputs are retained; no system/browser trust or production service is changed.
See [the registration and first-run guide](../../docs/browser-device-first-run.md).

The generated recovery harness also accepts a final `startup` argument after
the scenario and identity (`ip`, `dns` or `ipv6`). This runs the installed
`browser-device-start --experimental` CLI through a fixture-only headless/CDP
executable, verifies the setup-required page and explicit `--setup` launch, and
waits for automatic navigation to the guarded device-only entry. It then checks
sign-out or terminal rejection across another managed launch. The default
`review` mode retains the earlier direct Playwright launch. Both modes use
fictional loopback authority, normal sandboxing and isolated verified TLS;
neither changes a production display service or proves a physical power outage.
See [the experimental startup guide](../../docs/browser-device-startup.md).

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
node --test scripts/experimental/test_browser_device_setup.mjs
node --test scripts/experimental/test_browser_device_startup.mjs
node --test scripts/experimental/test_browser_device_worker.mjs scripts/experimental/test_browser_device_worker_gate.mjs
node --test scripts/experimental/test_browser_device_retirement.mjs
node --test scripts/experimental/test_browser_device_retirement_ui.mjs
```

The pytest wrapper `tests/test_browser_device_extension.py` includes those tests
and a real Node-to-Python native-pipe/persistent-pause integration test. No real
browser, device credential or Home Assistant connection is used. The coordinator
tests cover alarms, ordered cookie installation/removal, persisted sign-out intent,
generation races, unsafe state and the Chrome adapter's message/cookie checks.

The optional internal retired-intent coordinator has no installed Chrome/native
adapter or page binding. Its deterministic tests cover one-use explicit review,
fresh matching native evidence, unchanged pending state, newer sign-out and
clean-but-paused persistence. `tests/test_browser_device_retirement.py` also joins
real native history/archive confirmation through a test-only Python subprocess
adapter. DNS/IPv4/IPv6 are identity inputs there, not network/TLS acceptance.
Neither suite can establish physical-display or production recovery readiness.

The joined fixture also tests explicit no-matching-record reconciliation against
existing schema-1 and schema-2 native ledgers. It uses a separately reviewed,
archived stopped revision fence, not an approval borrowed from older history.
`tests/test_browser_device_resume_reconciliation.py` covers preserved history and
errors, unsafe/stale state, delayed prepares, competing reconciliations and real
process death before/after commit. All remain local synthetic tests; there is no
installed native action, browser adapter or automatic reconciliation caller.

`tests/test_browser_device_resume_boundary.py` adds fixed local profile/archive
selection, private-input change detection, native-process lock contention and
process-death ownership release. Its joined browser fixtures select only opaque
operation IDs through a test-owned adapter; browser requests never choose file
paths. Both maintenance paths remain paused and preserve evidence across lost
replies. Advisory locks coordinate only participating runtimes/writers; this is
not a supported credential replacement or installed browser maintenance flow.

`tests/test_browser_device_retirement_bridge.py` joins the confirmation-only
native endpoint to the candidate trusted page/worker, strict Chromium port and
paused acknowledgement coordinator. Native profile/archive/operation selection
is fixed by test-owned wrapper code, never page messages. Read-only confirmation
runs under the real process supervisor and verifies actual SQLite/archive
evidence; incomplete commits, changed inputs and lost browser write replies stay
paused. The Node UI tests cover document/gesture checks and one-use consent.
DOM/storage/cookies remain controlled fixtures. These adapters are not included
in generated extensions or registered as native hosts; this is not real-browser,
Firefox/WPE, Pi-screen or production acceptance.

`tests/test_browser_device_resume_workflow.py` covers a trusted local
stopped-browser maintenance session against real generated registration files,
Linux launch locks and native SQLite/archive evidence. Review holds launcher
ownership, exact per-session consent expires, and a durable marker blocks normal
startup before native execution. Real process-loss cases distinguish review,
guard-only and committed outcomes. Confirmation never replays execution or
releases that guard. This internal callback-based candidate has no CLI or
installed handoff, does not stop services, and never opens a real browser or
production display. Keep all markers/evidence for a future qualified handoff.

See the [enrollment design](../../docs/managed-display-enrollment-design.md) for
the exact limits. `browser_device_logout.mjs` adds an opt-in two-stage sign-out
bridge and document-bound completion tickets. The registered experimental bundle
connects the display-only dashboard sign-out form in the isolated installed-wheel
fixtures above; released manual kiosks and production services are unchanged.
Real-browser interruption evidence is recorded below. Local pause and cookie
removal are not proof of server-side stream revocation. Ordinary control messages
cannot resume sign-in. The separately gated
[trusted resume candidate](../../docs/browser-device-resume.md) now adds exact
document-bound review/confirmation and supervised native actions; it is not a
production-accepted workflow.

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
