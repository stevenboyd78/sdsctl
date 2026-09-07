# Managed-display enrollment and unattended recovery

Status: **approved direction; isolated experimental browser/helper proof, not a production
implementation or release**.

## The problem this solves

A display without a keyboard should recover after a power outage without someone
visiting it to type a password. Starting Chromium automatically is not enough:
the dashboard's current browser sessions are process-local and are lost when
the web service restarts. Normal session expiry also still applies.

The intended choice is explicit: enroll a display for unattended authentication,
or leave it in the existing manual-login mode. Do not silently save a password
when someone signs in. Enrollment is a separate administrator action.

## Preserve the existing installations

| Installation | Current behavior | Planned work |
| --- | --- | --- |
| Remote managed TUI | Named profile, saved per-client credential, server-verified observe scope, temporary connection retries | Review outage recovery and improve setup/status consistency without replacing its protocol |
| Browser kiosk | Separate shared display password, manual login, bounded browser sessions | Add independently revocable browser-device enrollment and automatic reauthentication |
| Direct USB TUI | Direct local scanner connection | No server credential or enrollment required |

Browser and TUI credentials must remain separate even when installed on the same
Pi. An operator password, Home Assistant token, TUI credential or copied cookie
must not be accepted as a browser-device credential. Browser display access must
not acquire the TUI's optional audio capabilities merely because both are called
managed displays.

## Administrator experience

Support both direct private-IP installations (no DNS or proxy) and optional
private/split-DNS installations. Enrollment and unattended recovery must not
depend on a domain name. Preserve exact TLS verification: IP URLs need matching
IP SANs; DNS URLs need matching DNS identities. The user's selected deployment
hostname is not a product-wide requirement.

1. Choose **TUI display** or **Browser display** and give the installation a name.
2. Create that installation's credential through an authenticated administrator
   workflow. The enrollment secret is delivered once through a private channel.
3. Install the profile, public certificate trust and protected credential on the
   selected Pi. Configuration contains a credential-file reference, not its value.
4. Run preflight and an interactive display check before enabling managed startup.
5. Show credential state separately from current connections: enrolled, revoked,
   connected, disconnected and last seen are not interchangeable facts.
6. Replace a lost/revoked credential remotely through the authorized administration
   connection. Do not require a keyboard on the physical display for replacement.

Home Assistant should identify the display type and allow individual rotation or
revocation. The existing remote-daemon client inventory is not browser telemetry;
do not present it as proof that a browser is connected. Ordinary standalone
servers need an equivalent supported enrollment path without Home Assistant.

## Authentication and storage boundaries

- Use a cryptographically random, per-installation browser credential with only
  the existing server-enforced display permissions. Names are labels, not secrets.
- Persist the server's enrollment/revocation authority across web restarts;
  ordinary browser sessions remain bounded and may remain process-local.
- Bind enrollment and authentication to the configured HTTPS server identity.
  Validate certificate trust and hostname/IP; reject redirects to other origins.
- Keep the long-lived credential out of dashboard JavaScript, URLs, browser
  launch arguments, service environment files, logs, diagnostics and exports.
- Validate secret-file ownership, mode, type, size and replacement behavior.
  Reject unsafe files rather than silently changing permissions on unrelated data.
- Evaluate systemd credential loading for managed installations; keep a supported
  private-file path for other installations. Neither permissions nor local
  encryption should be described as protection from a fully compromised host.
- Use atomic credential replacement. Define rotation and revocation behavior for
  active sessions and streams, not merely subsequent login attempts.
- Never disable Chromium's sandbox or expose an unauthenticated local credential
  service to make automatic login work.

The browser integration is a design gate before implementation: a launcher can
read a protected file, but that alone does not safely establish a Chromium
session. Select and review how the trusted native component authenticates and
delivers a short-lived browser session through the private native pipe without
exposing the long-lived credential. Review same-user local attackers, replay, CSRF, origin
confusion, grant leakage, and any local IPC endpoint. Do not ship a page that
reads a secret file or a debugging-port shortcut as an enrollment mechanism.

## Proposed native-to-browser boundary

**Selected for a local proof of concept, not approved as a production deployment:**
a dedicated Manifest V3 extension and a native-messaging helper. Chromium starts
native helpers over standard-input/output pipes, and a host manifest restricts
the permitted extension IDs. Native messaging is available to extension pages
and their service worker, not directly to content scripts.
See [Chromium native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging).

The proposed flow is:

1. The extension requests a fixed action from the helper. No caller-supplied URL,
   origin, credential path, cookie name, token or authorization role is accepted.
2. The helper reads only its protected installation configuration and secret.
   It authenticates to the configured HTTPS origin through a fixed endpoint,
   with verified TLS, no redirects/proxy inheritance and bounded I/O.
3. The server creates a short-lived session bound to the enrolled device and its
   credential generation, enforcing display-only permissions server-side.
4. Only that limited session result crosses the native pipe. The long-lived
   device secret never crosses into extension or dashboard JavaScript.
5. The trusted extension installs a Secure, HttpOnly, host-only, SameSite=Strict
   device-session cookie using the exact configured origin, then opens the display.
   Chrome's [cookies API](https://developer.chrome.com/docs/extensions/reference/api/cookies)
   requires cookie and host permissions; those permissions make the extension
   security-sensitive. HttpOnly protects against page scripts, not the extension.

Cookie scope is **host-wide, not port-isolated**. Setting a cookie with an HTTPS
URL containing a port does not restrict that cookie to that port. Exact-origin
validation is still required, and an unrelated HTTPS service on the same host
can receive a host cookie if visited. A dedicated hostname/profile and trusted
same-host services are deployment considerations, not protections provided by
HttpOnly or SameSite. Do not claim exact-port credential isolation.

The 2026-09-05 real-browser fixture confirmed this explicitly: navigation to a
second loopback HTTPS port delivered the fictional session cookie to that second
service. A dedicated deployment hostname versus accepting shared-host cookie
trust must be an explicit deployment choice before production configuration.
The current installation has selected an existing Home Assistant hostname with
shared-host trust. Keep real hostname/certificate details in private acceptance
notes, not repository examples. No automatic DNS, proxy, certificate or port
changes follow from selecting a hostname.

### Local proof checkpoint (2026-09-05)

The development-only harness in `scripts/experimental/audit_browser_device.mjs`
and its helper/extension templates use a loopback HTTPS fixture, fictional
credentials, a private profile and a temporary certificate database. They are
not installed by the package or registered in a normal browser profile.

The initial seven helper cases passed: valid authentication, rejected extra URL field,
rejected unexpected caller, rejected permissive secret-file mode, rejected
incorrect credential, rejected unrelated CA, and valid authentication after CA
restoration. All responses excluded the long-lived secret, stderr stayed empty,
and a deliberately unusable proxy environment did not affect verified HTTPS.

The helper matrix was expanded to 18 passing cases, including redirect refusal,
response bounds/schema/expiry, certificate hostname mismatch, symlink rejection
and a ten-second stalled-input deadline. No sandbox/certificate bypass or system
policy change was used.

The downloaded browser could not start its sandbox under workstation policy.
A separate, temporary headless fixture then passed on the HDMI Pi using its
installed Chromium 152.0.7977.75 with the sandbox and TLS verification enabled.
Extension lookup, cookie installation/flags, page-cookie/storage isolation,
fixture display-only authorization and native-host allowlist rejection of a
second extension passed. The production TUI was not stopped or replaced.

A browser close/relaunch followed by a fixed extension startup entry obtained a
new session after fixture invalidation. Relying solely on `onStartup` was not
reliable in this harness, so the proposed launcher must open that fixed entry.
The browser test driver navigated the entry; it did not inject credentials or
cookies. Managed-service/Pi reboot, concurrent service recovery, continuous
renewal and production revocation are not proven by this fixture. See the
experimental harness README for the remaining implementation and test limits.

This deliberately revises the earlier single-use-grant idea: the first prototype
will evaluate a short-lived session delivered through a private browser pipe,
avoiding a URL grant, disk handoff file, additional redemption route or local HTTP
listener. The session token is still a bearer secret. It must never be logged,
written to extension storage, sent to content scripts or placed in navigation.
If the prototype cannot maintain these boundaries, do not ship it.

### Cookie, role and revocation rules

- Device sessions need a separate cookie namespace from operator/manual-login
  sessions. Do not overwrite an operator cookie, infer privilege from a cookie
  name, or silently select operator access when cookies conflict. Specify and
  test fail-closed conflict handling before connecting the new middleware.
- Native credential authentication is separate from browser cookie/form login.
  It must not weaken existing Origin, Host, Fetch Metadata or CSRF protections.
  No permissive CORS or acceptance of credentials through GET/query parameters.
- The enrollment registry stores a verifier for a high-entropy generated secret,
  not an operator password. Use standard secure randomness and constant-time
  verification; do not invent a new challenge/signature protocol without need.
- Session records must include device identity and credential generation. Rotation
  or revocation invalidates associated sessions and signals every live stream.
  An update must not race with issuance of a session for an obsolete generation.
- Persist enrollment and suspension across App restart, while keeping session
  tokens short-lived. A malformed/missing authority file fails closed rather
  than silently accepting cached enrollment or reverting to shared passwords.
- Home Assistant's administrative process and native web process are separate:
  define an acknowledged invalidation path. Do not report revocation completed
  merely because a registry file was written while old streams remain valid.

### Explicit sign-out proposal

An enrolled display's Sign out should be labeled **Sign out and pause automatic
login**. The server persists a suspension for that device before acknowledging
sign-out, revokes its sessions and stops its streams. This pause survives browser
restart, Pi reboot and App restart. An administrator can resume that device from
Home Assistant or the standalone management workflow. A manual display-password
login does not clear the device suspension. Ordinary manual/operator logout
retains its existing behavior.

The helper/extension must also honor a local persistent pause, including when
the server is unreachable. Recovery after a lost logout response must reconcile
the server's suspension state before any new automatic session is issued.
Local resume and user-gesture verification are deliberately not in the initial
native request protocol; they need a separate reviewed interaction.

### Remaining prototype gates

- Register a stable extension identity and narrow native-host `allowed_origins`.
  Check the caller origin too, but do not claim that an argument is authentication
  against a malicious process running as the same Unix user.
- Use a dedicated account/profile, no page content scripts, no external-message
  forwarding and no `<all_urls>` permission. Validate full scheme/host/port in
  code as well as browser permissions; permissions alone are not exact-origin
  validation. Missing extension or host permission stops automatic login.
- Prove unattended extension installation/update on the supported Chromium
  packages without modifying personal-browser policy. Linux policy files can be
  installation-wide; inspect their scope rather than assuming a profile isolates
  them. See [Chromium Linux administration](https://www.chromium.org/administrators/linux-quick-start/).
- Extension service workers can be suspended: reconstruct state on restart,
  persist non-secret pause state and use bounded scheduled work rather than
  assuming an in-memory timer or connection remains alive. See
  [service worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle).
- Add native-pipe deadlines, message limits, concurrency bounds and clean EOF
  handling. Native stdout carries protocol frames only; stderr uses fixed safe
  failure classes. Neither response bodies nor secrets belong in diagnostics.
- Reject a TLS failure before issuing any device credential. Test invalid or
  rotated CA material, redirects, wrong ports and untrusted response fields.

### Local foundation status

`browser_device_protocol.py` currently provides only bounded native-order request
framing and strict validation for version1 actions `status`, `authenticate` and
`suspend`. Unknown fields/actions, duplicate keys, malformed JSON, oversized frames
and partial reads are rejected with fixed redacted errors. The parser does not
register a native host, read a credential, create a listener or authenticate.
Read deadlines remain a responsibility of the future helper process.

### Persistent authority foundation

`browser_device_store.py` is an experimental, unwired server-side authority.
It uses the standard-library SQLite implementation with serialized transactions
and a schema version, inside an existing owner-only directory. Creation is an
explicit operation that refuses to overwrite an existing database. Runtime
operations open an existing authority and fail closed when it is missing,
corrupt, has an unsupported version or has unsafe metadata. No cached credential
fallback or implicit empty-registry recreation is provided.

The store generates separate `sdsctl-browser-v1.` credentials from 32 random
bytes and persists only device-bound SHA-256 verifiers. Verification uses a
constant-time digest comparison. Inventory does not include credentials or
verifiers, and one-time issuance results redact credentials in their repr.
Callers must still keep issuance objects out of generic serialization/logging.

Each enrollment has an identifier, generation and active/paused/revoked state.
Rotation and state transitions advance the generation; rotating a paused device
does not resume it. Resume advances the generation again, so an older binding
does not become valid again. This foundation provides resume for paused records,
not an un-revoke operation; revoked identifiers cannot be reused or rotated.
Administrator UI semantics must be reviewed before exposing these primitives.
The initial registry is bounded to 256 enrollment records, including revoked
records. Removal/retention policy is not implemented.

**This is not session or stream authorization middleware.** An authentication
binding or `is_current` result is only a snapshot. It is not a lease guaranteeing
authorization after the transaction ends. The web process still needs atomic
session binding, expiry, rate limiting, cookie-conflict handling, stream watchers
and acknowledged cross-process invalidation. A committed registry change alone
must not be presented as completed active-stream revocation.

Tests cover private-file safeguards, missing/corrupt authority, persistence,
credential separation, per-device rotation, pause/resume, terminal revocation,
capacity rollback, generation bounds, concurrent rotations, a revocation race
and state visibility across a separate process. They do not constitute a power
failure, filesystem crash-consistency or malicious same-UID/root security test.

### Unwired session foundation

`src/sds200/browser_device_sessions.py` provides a separate experimental
display-device session registry. On its own it does not enable enrollment on an
installed App, add an HTTP route, set a browser cookie or alter manual/operator login.

- Fresh opaque session tokens use a distinct namespace; only SHA-256 digests
  remain in memory. Issuance representations redact the token. Session records
  bind to a device ID and credential generation, with no selectable operator role.
- Defaults are a five-minute absolute lifetime, a one-minute idle timeout,
  64 total sessions, two sessions per device and 16 attached requests per session.
  Capacity exhaustion denies new sessions without evicting another display.
- An attached stream is not idle, but still has a hard absolute deadline.
  A request lease supplies the remaining deadline and a cancellation event.
  Release is idempotent; repeated release cannot prolong an idle session.
- Every acquisition rechecks persistent state. Reconciliation signals old leases
  after pause, rotation or revocation, including changes from another process.
  Resuming a device cannot revive old session tokens. Missing/corrupt authority
  closes this manager permanently; repair requires a fresh manager, not cached
  session resurrection.
- The optional service-lifetime reconciler polls once per second by default.
  Cancellation or an unexpected monitor failure closes all local sessions.
  Detection latency also includes scheduling and database I/O. The interval is
  **not** a guaranteed revocation completion deadline.

These are authorization snapshots and cancellation signals, not yet an atomic
cross-process stream-termination acknowledgement. A change after an issuance
snapshot may return a token that the next acquisition rejects. A change after an
acquisition snapshot requires reconciliation and request cancellation. The HTTP
layer must consume cancellation/deadlines, await request cleanup, enforce the
display-only route allowlist and strict cookie/origin policy, bound blocking
database/authentication admission, and acknowledge administrative changes only
after the affected streams actually finish. Calling `revoke_session` alone is
not persistent sign-out: explicit device pause still needs that integration.

An experimental request guard consumes those lease signals and deadlines. It
cancels an attached cooperative operation, waits for its cleanup and releases the
lease on completion, failure or cancellation. Delaying request dispatch cannot
extend the hard deadline. The opt-in HTTP adapter below uses this guard, but no
production launcher enables it. It cannot forcibly terminate code that refuses
cooperative cancellation.

The session tests include per-device isolation, independent-process pause,
concurrent admission limits, idle/absolute expiry, generation changes during
issuance, registry failure, monitor failure/cancellation, idempotent release and
request-guard cleanup/expiry/cancellation, including delayed dispatch.
They do not claim live HTTP/SSE cancellation or physical outage acceptance.

### Opt-in HTTP adapter foundation

`src/sds200/browser_device_http.py` adds an experimental ASGI adapter selected
only when a caller explicitly passes `browser_device_sessions` to
`create_web_dashboard_app`. No CLI, Home Assistant option or production launcher
supplies this argument. It requires native HTTPS authentication and cannot be
combined with Home Assistant Ingress or an unauthenticated dashboard.

The candidate native-helper exchange is `POST /auth/device/session`, with one
Bearer authorization header and an exact JSON body containing only `device_id`.
It rejects query parameters, browser Origin/Fetch Metadata headers, all cookies,
duplicate fields/authorization headers, wrong content types and oversized bodies.
Responses are non-cacheable and return a limited token plus `expires_in`; they
do not install a browser cookie. The earlier loopback helper proof still uses a
fixture-only endpoint/response format and must be deliberately updated before
it can exercise this adapter.

Device browser requests use `__Host-sdsctl-device-session`, independent of the
existing manual/operator cookie. Duplicate or conflicting authentication cookies
are rejected, including when both tokens are individually valid. Device cookies
cannot fall back to operator access. The adapter reuses exact HTTPS-origin and
Fetch Metadata checks, permits only the display read allowlist, and marks the
request display-only before invoking the application. Manual/operator requests
without a device cookie continue through the existing middleware.

Exchange admission allows five attempts per raw peer per minute and 60 globally;
forwarded address headers cannot select a different budget. A shared reverse
proxy therefore shares its peer budget. Two body-reader slots have a 1 KiB limit
and three-second deadline. Two worker slots have no waiting queue; canceling a
request does not release a slot until the underlying work finishes. Abandoned
results are cleaned up even after the request event loop has closed. Rate limits
return 429 and worker/session/authority unavailability returns 503, with retry
guidance; rejected credentials return 401. These limits are candidate defaults,
not a denial-of-service availability guarantee or a deployment sizing promise.

ASGI streaming tests verify cancellation, cooperative cleanup and final response
termination after device revocation. This opt-in adapter is not ready for
production use; the pause and local completion contract is described below.

### Persistent sign-out and local completion barrier

Device-session `POST /auth/logout` now requires the exact Origin, acceptable
Fetch Metadata, a single non-conflicting device cookie and no query parameters.
The dashboard labels this action **Sign out and pause automatic login** when its
session response identifies an enrolled device. Ordinary manual/operator login
and logout retain their existing behavior.

The authority atomically compares the requesting session's credential generation
before saving `paused` with a new generation. A stale browser cannot pause a
newer rotated or resumed generation. The pause survives reopening the authority;
new device authentication is denied until administrative resume. Manual login
does not clear the pause, and resuming never revives old session tokens.

The web session manager retains bounded completion records even after invalidated
sessions have been removed. For this adapter, a request is complete only after
application cleanup **and the final ASGI response send** finish (or the request
exits on failure/disconnect). Unfinished requests continue to count against its
request bound. Invalidating a session cannot erase that accounting.

`acknowledge(record)` is a bounded-worker, **local web-owner barrier**, not a global
or durable receipt. It waits for this manager's older-generation requests and
checks that the requested authority record is still current before returning
success. It does not wait for unrelated devices. Failure, a changed record or
timeout cannot produce a successful acknowledgement. Polling and SQLite I/O
latency still apply; no hard real-time shutdown guarantee is claimed.

After confirmed local completion, logout returns a non-cacheable confirmation
page and clears only the device cookie. If the pause was saved but shutdown
could not be confirmed, it returns **202 with an explicit unconfirmed-shutdown
message**, not a completed-sign-out claim. The saved pause is not rolled back.
Authority/worker unavailability returns a retryable failure instead.

The private owner channel below connects that barrier across a process boundary.
Helper-side persistent pause for offline sign-out, renewal and lost-response
recovery, cookie installation/conflict recovery UI, production administrator wiring
and secure live acceptance still remain. No production launcher enables this
candidate, and these tests do not constitute browser/Pi outage acceptance.

### Private Linux web-owner channel

`browser_device_owner.py` provides a same-user Unix-domain control socket and a
persistent owner lock beside the private authority. The experimental HTTP adapter
must acquire the lock during its lifespan before serving requests. A second
adapter using that authority is refused. Multiple web workers and constructing an
owner before forking are unsupported; use one web process per authority.

The lock file is owner-only and is **never deleted during normal shutdown**.
Keeping its inode prevents cooperating workers from acquiring different locks
after an unlink/recreate race. Socket files are owner-only inside the private
directory. Both endpoints check Linux peer credentials against the current user.
This trusts same-UID/root processes; it is not isolation from a compromised
service account. No TCP port, TLS exception, external listener or new credential
is introduced. The App administrative caller must run in the appropriate service
account/filesystem namespace; do not broaden permissions to bypass that boundary.

Do not replace the authority directory or owner lock while the service is running,
or expose the same authority through alternative bind-mounted aliases. Stop the
owner before maintenance. Runtime validation rejects unsafe metadata, changed
lock/directory identity and inherited ownership in another PID. Those checks do
not defend against malicious same-UID/root filesystem replacement.

The sole control operation is acknowledgement of an already-committed record:
protocol version, action, random request correlation ID, device ID, generation
and state. Frames are length-prefixed, limited to 1 KiB, and served under a bounded
deadline with at most two active control handlers. They use the adapter's bounded
worker pool. Unknown/duplicate fields, malformed values, oversized frames and
stalled requests are refused. The channel cannot enroll devices, change authority,
retrieve secrets or send scanner commands.

The administrative client validates the peer and response correlation, returning
the committed record, owner incarnation ID, peer PID and completion result.
Unavailable/busy/disconnected owners are errors, not success; a false completion
result must not be presented as completed revocation. The receipt describes a
live-owner check, not a durable promise that an administrator cannot later resume
or rotate the device. The administrative UI/CLI workflow still needs to call this
client after committing a change and handle failure explicitly.

Shutdown stops admission, closes sessions/control connections, and waits for
outstanding HTTP requests before releasing ownership. The adapter delays ASGI
`shutdown.complete` until that work finishes. An unconfirmed drain reports shutdown
failure and retains the lock until process exit, preventing a new cooperating
owner from overlooking old streams. A crashed process loses its OS lock; a new
owner may remove only the validated stale socket under the acquired lock and
starts with a fresh incarnation ID and empty sessions.

Tests use separate spawned Linux processes for held-request acknowledgement,
second-owner rejection, stale-record refusal, crash recovery and new incarnation
identity. HTTP lifespan tests cover real adapter ownership, control access,
refusal without startup and retained ownership on unconfirmed shutdown. These
are local protocol/lifecycle tests, not production App deployment acceptance.

## Administrator action foundation (experimental, not exposed)

`browser_device_admin.py` now joins the private authority to the web-owner
acknowledgement channel. It is a synchronous internal service, intended for a
bounded worker behind an authenticated administrator boundary. It does **not**
add public HTTP routes, CLI commands, Home Assistant controls, or a production
enablement option. Existing TUI credential administration is unchanged.

An action supplies the exact device ID, generation and state the administrator
reviewed. The authority compares that record inside the SQLite write transaction
before transition or rotation. A concurrent change rejects the stale action
without mutating the device or contacting the owner. Repeating an old rotation
cannot silently invalidate the replacement credential. Low-level store callers
retain their existing API; the administrator service requires the reviewed record.

After the mutation commits, the service requests an owner acknowledgement and
rechecks authority. Its explicit redacted result distinguishes:

| Status | Meaning |
| --- | --- |
| `confirmed` | Owner confirmed old requests drained and the exact record was still current at the subsequent check |
| `pending` | Owner responded but could not confirm completion within its bound |
| `owner_unavailable` | No valid owner acknowledgement was obtained; absence is not evidence of drained requests |
| `superseded` | A newer administrator or browser action changed the reviewed/committed record |
| `authority_unavailable` | Current authority could not be verified; completion must not be claimed |

Only `confirmed` produces `completed: true`. This is point-in-time evidence, not
a promise that another administrator cannot subsequently resume the device.
Acknowledgement failure never undoes a committed pause, revocation or rotation.
`confirm(record)` retries only confirmation, not the mutation. It must not be
replaced by automatic replay of the original rotation or a newer-record mutation.

Rotation returns its replacement credential in a separate one-time private
handoff even when confirmation is pending/unavailable. The ordinary status
document and object representation exclude it; generic dataclass serialization
is prohibited. If the one-time handoff itself is lost, the verifier-only authority
cannot recover the credential. Inspect current state and explicitly authorize a
new rotation using its current record; do not retry the stale request. A storage
exception during commit also requires inspection rather than assuming rollback
or automatically replaying a mutation. New enrollment reports identity issuance,
not service readiness or successful device login.

Tests cover committed-state observation before ACK, unavailable owners, pending
request cleanup, separate-process acknowledgement, lost-response confirmation
retry, concurrent rotations, stale reviews, pause preservation, authority loss,
and a resume that supersedes a pause during acknowledgement. The opt-in Ingress
adapter below is the next local integration, not a production enablement.

### Explicit Ingress administration and one-time download candidate

`browser_device_ingress.py` supplies an experimental, script-free administration
page at `/api/v1/home-assistant/browser-devices`. The dashboard factory requires
an explicit `BrowserDeviceIngress` object **and** Home Assistant Ingress mode;
default Ingress and native dashboards have no such endpoint. No launcher passes
this configuration and no existing production administrator privileges change.

The boundary requires the raw Supervisor Ingress peer (`172.30.32.2`) and exactly
one `X-Remote-User-Id` in a private, explicitly configured administrator ID
allowlist. Home Assistant documents the [Ingress peer requirement](https://developers.home-assistant.io/docs/apps/presentation/)
and [authenticated user identity headers](https://developers.home-assistant.io/docs/apps/security/).
The candidate grants enrollment administration to those exact IDs; it does not
infer an administrator role from a username, panel visibility, a dashboard
operator cookie, or forwarded client address. This is a static grant, not a
live Home Assistant administrator-group lookup. Remove grants through private
configuration and restart; selecting and installing that configuration remains
a deployment gate. A compromised Supervisor/trusted peer is outside this boundary.

Configuration also fixes the exact external HTTPS origin, including a nondefault
port if needed. Both verified private-IP and DNS HTTPS origins are supported.
Forwarded host/scheme values do not choose it. POST requires that exact Origin,
an allowed Fetch Metadata site, strict bounded form fields, and a short-lived
one-use nonce bound to the authenticated user. GET pages are non-cacheable and
have a restrictive CSP; no dashboard JavaScript or third-party assets execute.
Nonces expire after five minutes with at most 32 outstanding per adapter.
Bodies are capped at 1 KiB and three seconds, with two body readers and two
database/action workers, no waiting worker queue. Cancellation cannot free a
worker that is still committing or awaiting acknowledgement.

The page provides enrollment, rotation, pause, resume, terminal revocation and
confirmation-only retry. State-changing forms carry the reviewed generation
and state and require the user to type the exact device ID. The nonce is consumed
before any mutation is scheduled, even if the response is lost. Refresh and
review inventory after each action/download or interrupted request; never
automatically resubmit it. Download responses leave the old form on screen,
but its nonce cannot be reused.

Enroll and Rotate submit a native browser form and return a **one-time JSON
attachment**, not an HTML secret field or a dashboard-JavaScript response. The
attachment contains a version, device ID, generation, credential and explicit
outcome. It is a private credential handoff, **not a complete installable helper
profile**; origin and certificate trust still require separate verified setup.
Enrolled-device, manual display and native operator authentication cannot fetch
it. There is no GET/re-download endpoint, response cache, temporary server-side
secret file, or credential in the status/confirmation pages. Configure proxies
and diagnostics not to log response bodies. A rotation with unconfirmed cleanup
still downloads its one-time replacement with `completed: false`; HTTP download
success alone never proves session cleanup.

A browser download cannot guarantee destination permissions or prevent download
history, backups, or extra local copies. The page explicitly requires private
transfer, mode 0600, and deliberate removal of extra copies. If delivery fails,
the committed verifier cannot recover the lost secret; refresh and explicitly
review a new rotation. No user files are automatically deleted or overwritten.

Local tests cover real factory gating, trusted-peer/user checks, cookie/header
spoofing, exact Origin/nonce checks, replay, one-time downloads, stale actions,
pending completion, lost delivery, bounded workers during cancellation and body
timeouts. Production configuration, standalone administrator CLI/private-file
delivery, UI polish, helper recovery and browser/Pi acceptance remain pending.

## Native-helper recovery engine (experimental, not registered)

`browser_device_recovery.py` now implements a private persistent recovery ledger
and dispatch for the existing fixed `status`, `authenticate` and `suspend` native
actions. It is not a registered native host, a transport, or a running extension
scheduler. The earlier `scripts/experimental` browser fixture is unchanged and
does not yet use this engine.

The installation explicitly initializes a private SQLite ledger under a current-
account-owned 0700 directory, with a regular, single-link, non-symlink 0600 file.
An installation identity fingerprint binds the ledger to the intended server,
device and extension identity; configuration provisioning must define that
fingerprint consistently. The ledger stores mode, revision, failure count and
attempt times—not credentials, cookies or session tokens. Missing, corrupt,
unsafe or mismatched state never silently initializes a replacement or clears
a saved pause. Same-account/root compromise is outside this protection.

Temporary exchange failures use persisted exponential backoff with jitter,
normally capped at 60 seconds. A bounded numeric `Retry-After` may extend that to
300 seconds. Restarting a helper does not reset the delay. The success result
advises renewal at 75% of the conservatively remaining absolute lifetime and
keeps a short admission cooldown of at most ten seconds. This allows an explicit
startup/recovery attempt without waiting for an old cookie's full lifetime, but
the extension still must honor renewal scheduling and server admission limits.
`active` means automatic attempts are permitted, not that a browser is connected.

The response parser uses the actual `/auth/device/session` shape (`token` and
`expires_in`), rejects extra/duplicate fields and malformed lifetimes, and does
not expose response bodies in errors. Credential rejection (401), TLS validation
failure, invalid setup and protocol errors stop automatic attempts persistently.
Server/network failures must be classified by the verified transport; no TLS
verification or network I/O is performed by the ledger itself.

Each attempt commits a 15-second claim before network work. Other helper
processes cannot start another exchange during that claim. A crashed helper's
claim can expire; its late result cannot overwrite a newer claim or pause.
The callback must have an independently enforced ten-second total deadline;
the engine rejects overlong returned results, but cannot forcibly interrupt a
callback that never returns. The native process prototype below supplies that
deadline, but is not installed or registered. Clock rollback invalidates in-flight claims and
replaces an active wait with a bounded ten-second delay; it never clears a
pause or terminal failure.

`suspend` persists local intent without waiting for the network, before a future
extension attempts server logout or cookie clearing. A separate in-flight helper
observes the changed revision and discards its result. This protects the native
handoff race, but does not yet prove browser cookie-installation ordering: the
extension must serialize suspend, status checks, cookie installation and removal
and reject old-generation completions. No token may be written to extension
storage as a shortcut.

Local pause is not a claim that server streams were revoked. If logout's response
is lost, local pause continues preventing new automatic sessions. Reconciliation
with server suspension and acknowledgement still remains before an explicit
resume. The ledger's compare-by-revision resume method is an internal trusted
administrator operation, deliberately absent from the native message protocol;
ordinary login, credential replacement and helper restart must not invoke it.

Tests cover durable backoff, pause, terminal failure, unsafe/corrupt state,
separate-process pause races, crashed claims, response validation and renewal
against the actual ASGI server adapter using fictional credentials. The ASGI
test is not a TLS or physical browser test. Native configuration/credential-file
loading and verified HTTPS exchange/deadlines are covered by the next local
prototype below. Extension alarms and cookie ordering, lost-logout reconciliation,
and physical Pi outage acceptance remain pending.

## Protected native helper prototype (not installed)

`browser_device_native.py` adds a Linux-only, one-request native runner. It has
no command-line entrypoint, native-host registration or production launcher.
A future trusted wrapper must fix its installation directory; browser messages
cannot choose a path, URL, credential or extension identity.

The private installation directory must be owned by the current user and mode
0700. Each input is a regular, single-link, current-user-owned mode-0600 file;
symlinks, FIFOs, oversized inputs and unsafe permissions are rejected, not repaired.
The fixed inputs are:

| File | Purpose |
| --- | --- |
| `client.json` | Exact version-1 configuration: `version`, `origin`, `device_id`, `extension_origin` |
| `device.secret` | Raw enrolled device credential, optionally followed by one newline |
| `ca.pem` | Explicit PEM trust bundle for the configured HTTPS server |
| `recovery.sqlite` | Explicitly initialized persistent recovery ledger |

The origin must be canonical HTTPS, without user information, a path, query or
fragment. DNS names, IPv4 and bracketed IPv6 addresses are supported; neither
internal DNS nor a proxy is required. The certificate must match the configured
name or IP address. The configuration fingerprint binds the ledger to the origin,
device ID and exact permitted Chromium extension origin. Replacing a credential
or trust file does not implicitly clear a paused or terminal recovery state.
The downloaded enrollment attachment is still not a complete installation profile.

Authentication makes one direct POST to `/auth/device/session`, using TLS 1.2 or
newer with issuer and hostname verification against the explicit trust bundle.
It ignores proxy and certificate environment settings, does not follow redirects,
and rejects unexpected cookies, content encoding and ambiguous response headers.
Only a bounded, validated session response can reach the native pipe. Status and
suspend work without reading credentials or contacting the server.

The dedicated, single-threaded Linux process forks a worker before loading any
secret. Its supervisor imposes a ten-second total deadline across input, DNS,
HTTPS and output, kills and reaps a timed-out worker, and arranges worker death
if the supervisor dies. Per-socket timeouts alone do not supply this guarantee.
This is a process-level bound on a functioning OS, not a hard-real-time guarantee
against uninterruptible kernel operations. Partial or absent output after failure
must be discarded by the future extension. Unbuffered native pipes are required.

Local tests use fictional credentials and temporary HTTPS certificates. They
exercise real native framing and HTTPS exchange, untrusted issuers, hostname
mismatch, unsafe files, redirect/cookie rejection, persistent failure modes,
stalled input, slow responses, blocked exchange/output and supervisor death.
They do not establish browser cookie-installation ordering, extension renewal,
logout reconciliation or physical Pi outage acceptance. No real device enrollment
or unattended production login is enabled by this module.

## Browser renewal coordinator prototype (not registered)

`scripts/experimental/browser_device_recovery.mjs` implements an opt-in MV3
coordinator and a Chrome API adapter for the new native-helper response contract.
It is separate from the earlier loopback probe extension. No manifest, host
registration, dashboard content script, launcher or production enrollment enables
it. Configuration is fixed by the trusted installation, not by page messages.
The browser installation fingerprint must match the native installation identity.

The coordinator serializes cookie operations, coalesces simultaneous wake requests,
and checks native mode/revision before and after installing a session. Session
tokens are held only during native handoff/cookie installation; they are never
written into extension storage or returned to a control page. Only the reserved
host-only, Secure, HttpOnly, SameSite=Strict device cookie is managed. The adapter
checks cookie flags and expiry, and verifies absence after removal. Operator and
manual display cookies are not manipulated. Cookies still do not isolate ports.

The explicitly provisioned browser record contains only version, installation
fingerprint, paused intent, installation phase and next renewal time. Missing,
corrupt or mismatched state fails closed; it is not silently initialized. Storage
access is restricted to trusted extension contexts, as supported by the
[Chrome storage API](https://developer.chrome.com/docs/extensions/reference/api/storage).
There is no browser-message resume operation. A native administrator resume alone
does not erase a browser's persisted intentional pause; a reviewed installation
recovery workflow is still required before shipping.

Renewal uses one named alarm, with a minimum scheduled delay of 30 seconds.
The worker must call the adapter once at top level on each incarnation to reconcile
state and repair missing alarms. Chrome may delay alarms and does not guarantee
precise timing; older versions can lose alarms across browser sessions. See the
[Chrome alarms contract](https://developer.chrome.com/docs/extensions/reference/api/alarms).
Late wakeups request a new session; they do not extend the old server lease.
Cookie expiry conservatively includes native round-trip time. Sessions with less
than 30 seconds remaining are not installed. Clock rollback correction is saved
so repeated wakes cannot continually postpone renewal. Native backoff remains
authoritative, even when the browser's minimum alarm delay is longer.

Intentional suspend invalidates in-flight results immediately and persists the
local pause before native I/O, without waiting for an outstanding authentication
request. Storage writes are ordered so an earlier installation write cannot
overwrite that pause. Cookie cleanup waits for any pending cookie set operation;
it never races removal against an unresolved installation and then declares
success. Interrupted installation is marked persistently and cleared on recovery.
Lost native suspend acknowledgements or cookie-removal failures retain the pause
and schedule cleanup retry. Responses separately report local persistence, native
pause and cookie cleanup; standalone suspend explicitly leaves server revocation
unconfirmed. The two-stage bridge below carries a separate server result.

Only exact messages from the extension's own fixed startup/control pages are
accepted. Dashboard URLs, other extensions, extra fields and resume messages are
rejected by the base adapter. The separate bridge prototype below handles dashboard
logout, but is **not connected in any installed build**. Calling the local suspend
primitive alone is not a server logout or stream-revocation guarantee.

Deterministic tests exercise the coordinator with mocked Chrome APIs, including
pending authentication/cookie writes, worker-object recreation, delayed alarms,
clock rollback, storage failures and secret-free results. A separate integration
test uses real Node-to-Python framed messages and the native persistent ledger
with no credential or CA file. These tests do not prove Chromium service-worker
termination behavior, browser-owned operations still pending after termination,
dashboard navigation after session renewal, idle-session expiry recovery, actual
cookie permissions, server logout reconciliation, or a physical Pi outage.
Those require separate integration evidence before registration or deployment;
the isolated logout and cookie-interruption evidence below closes only its stated
browser/version/scenario scope.

## Dashboard sign-out bridge prototype (not registered)

`scripts/experimental/browser_device_logout.mjs` adds opt-in worker and isolated
content-script adapters for a dedicated enrolled-device browser profile. It does
not edit the installed dashboard JavaScript, generate a manifest or register any
content script. Do not inject it into ordinary mixed-use browser profiles.

The content adapter captures the root dashboard's exact `/auth/logout` POST form,
requests durable local preparation, then sends a fixed same-origin logout fetch.
The browser supplies Origin, Fetch Metadata and the HttpOnly cookie; the extension
does not expose the cookie to page JavaScript or manufacture those headers. This
uses an [isolated content script](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts);
actual browser-origin/network semantics still require integration acceptance.

Preparation invalidates in-flight authentication, persists pause and a
`logout_pending` phase, then pauses the native helper while retaining the cookie.
Any pending cookie set finishes before preparation is acknowledged. A 60-second
logical hold deadline is saved and recovery alarms scheduled. Repeated begins
cannot extend it. After a lost document or worker, a wake retains the cookie only
until that deadline, then attempts cleanup and stays paused. Alarm delays mean
this is not a hard wall-clock removal guarantee. Clock rollback correction is
persisted rather than recalculated forever.

Messages require the extension's own exact-origin, active top-frame root document
in a non-incognito tab. A random short-lived completion ticket is bound to the
browser-supplied tab/document ID. Other documents and unsolicited completions are
rejected. The first completion is cached: replay cannot upgrade uncertainty to
confirmed shutdown. Worker loss discards the ticket, not the persistent pause.

For explicit `Accept: application/json`, the experimental device logout route
returns version, device marker, saved-pause and drain flags. HTTP 200 confirms
authority pause and request drain; HTTP 202 confirms saved pause but not drain.
Normal form callers retain HTML. Authorization, no-store headers and deletion of
only the device cookie are unchanged. The content adapter validates fixed URL,
content type, bounded body, unique fields and status consistency. Redirects,
errors and lost/malformed responses are unconfirmed, never successful shutdown.

Completion persists a cleanup-required phase before removing the local cookie.
Local persistence, native pause, cookie removal and server outcome remain separate.
No failed/lost response causes automatic login or another mutation attempt. The
page receives fixed status text, never server HTML, tokens or exception details.
Repeated captured submits are suppressed; this is not a security boundary against
same-origin page compromise or direct non-event form navigation.

Deterministic tests cover both stages, pending cookie installation, cold-start
races, recovery, wrong-document/replay messages and malformed/lost responses.
ASGI tests verify both actual JSON outcomes and persistent authority pause.
Those deterministic tests mock browser APIs and DOM events. A separate opt-in
`scripts/experimental/audit_browser_logout.mjs` now exercises real Chromium on an
isolated HDMI Pi fixture, with verified loopback TLS, sandboxing, fictional
credentials and the actual native helper. Confirmed/pending logout and lost
response checks passed: the real content-script fetch supplied the cookie and
same-origin headers, the page showed the appropriate fixed status text, the
cookie was removed and both native/browser state remained paused after restart.
The worker-stop scenario also passed actual termination during a pending server
POST, followed by persisted-intent recovery; per-run redacted results are retained.

The lost-response fixture observed Chromium replaying a POST after a connection
closed before response headers: two wire requests, but one authorized pause. The
paused server rejected the replay. Do not equate one content-script fetch with
exactly-once wire delivery, or retry an uncertain mutation as a resume operation.

The fixture's HTTPS server implements a fictional contract, not actual ASGI
stream drain. Production rendered feedback/stale-data handling, renewal/expiry navigation,
trusted packaging and physical Pi outage acceptance remain mandatory before
deployment. The fixture does not change production services or real trust stores.

### Pending browser cookie-operation interruption

Three additional HDMI Pi Chromium 152.0.7977.75 scenarios passed with the real
native helper, sandbox and TLS verification: interruption during cookie creation,
interruption during removal, and recovery starting before a blocked creation was
released. The test pauses only the verified temporary browser's network process,
dispatches the real cookie API, verifies the operation is pending with durable
local pause, and observes the worker stop. It resumes the network process even
on assertion failure. No browser cookies are injected by the driver.

The creation case observed the late cookie after worker death, then recovery
removed it. In the alternate ordering, recovery queued removal before the network
process resumed. All three ended with no cookie, a protected fixture read returning
401, native ledger mode paused and no new authentication after browser restart.
The experiments use local suspend, not server logout: they do not invalidate
copied tokens or prove immediate server revocation. In particular, the observed
late cookie means worker termination alone must not be presented as instantaneous
logout. See the experimental harness README for signal guards and exact limits.

## Recovery contract

| Condition | Required behavior |
| --- | --- |
| No enrollment configured | Browser shows display-only login; managed remote TUI reports missing setup without falling back to direct scanner access |
| Valid enrolled device starts | Authenticate automatically and obtain a bounded display session |
| Network/server not ready | Mark old data stale and retry with capped backoff and jitter, not a tight loop |
| Web service restarts | Reauthenticate using the enrolled identity once the server is ready |
| Browser session expires | Obtain a fresh session without disabling expiry or requiring manual password entry |
| Credential rejected or revoked | Stop automatic authentication attempts; show an actionable recovery state |
| Configured secret file unsafe/unreadable | Report setup failure; do not treat it as ordinary network loss |
| TLS identity/trust fails | Fail closed; no exception, alternate server or plaintext fallback |
| Explicit sign-out | Remain signed out; require an explicit resume of unattended mode or manual sign-in |
| Intentional browser/TUI close | Preserve the intentional-stop contract rather than relaunching indefinitely |

Define explicit sign-out persistence across browser restart and host reboot before
shipping. A manual sign-in must not silently replace or re-enable an enrolled
device. Deleted or absent optional configuration may permit manual login;
invalid enrollment must never downgrade to operator access.

For the TUI, preserve its existing temporary-versus-permanent error classification
and observe-only enforcement. A new persistent setup-required screen would be
an explicit behavioral change; it is not part of the currently released contract.

## Implementation sequence and acceptance gates

1. Close out the manual-login startup qualification and restore production before
   starting the new authentication deployment. Retain precise test evidence.
2. Review the browser-device threat model, enrollment persistence and native-to-
   browser session handoff. Define revocation and explicit sign-out semantics.
3. Implement and test server-side device identity, authentication, rate limits,
   session binding and immediate revocation/stream cleanup with fictional secrets.
4. Add administrator enrollment/rotation/revocation and redacted inventory, while
   preserving operator login, manual display login and TUI enrollment compatibility.
5. Add opt-in protected client configuration, unattended browser recovery and
   supported service integration. No production credential migration by default.
6. Review TUI startup/reconnect behavior against the same outage matrix. Make only
   evidence-backed changes; do not replace its authentication transport.
7. Test both Pi geometries, multiple independent devices and a combined server/
   display outage. Include server-late startup, expiry, revocation while streaming,
   one-device rotation, unsafe/missing files, TLS failure and explicit sign-out.
8. Document beginner-oriented browser and TUI setup, secret replacement, recovery,
   upgrade and exact removal. Publish only after the security and physical gates.

An HDMI manual-login reboot/cold-start pass does not prove unattended authentication,
the small Pi's browser cold startup, simultaneous whole-site outage recovery,
certificate failure handling or the remaining fault-injection cases.

## Related guides

- [Browser kiosk design](browser-kiosk-design.md)
- [Current manual-login kiosk candidate](browser-kiosk.md)
- [Managed remote TUI](managed-pi-display.md)
- [systemd service credentials](https://systemd.io/CREDENTIALS/)
