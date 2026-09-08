# Experimental device replacement and resume boundaries

Status: **experimental trusted-page/native-bridge candidate, not production accepted**.
Generated experimental bundles now include a two-step resume page and an
identity-bound native bridge. Isolated real-browser acceptance has passed on
both Raspberry Pis; this is not a supported production resume or credential-replacement
installer. Do not call internal Python methods, edit browser storage, delete a
ledger, or rerun first-time setup to work around a refused or uncertain operation.
The published manual-login kiosk and remote TUI are unchanged.

This guide builds on [first-run setup](browser-device-first-run.md),
[service lifecycle](browser-device-service.md) and the
[enrollment/recovery design](managed-display-enrollment-design.md). It describes
what the candidate components guarantee and what a supported workflow must add.
It does not enable production enrollment, accept rotation downloads, change
credentials, start services or deploy to either display.

## Three separate decisions

A display being allowed on the server is not the same as a user consenting to
automatic sign-in on that display. Three independently persisted states matter:

| State | What it controls | What changing another layer does not do |
| --- | --- | --- |
| Server device record and generation | Whether this device credential can receive a new display-only session | Server resume does not erase local pause or native terminal errors; old-generation sessions stay invalid |
| Native helper mode and revision | Whether this installation may attempt a verified authentication exchange | Replacing a credential or fixing trust does not reset the ledger; a native-only reset does not override a paused server |
| Browser recovery record | Whether this browser has intentionally stopped automatic sign-in | Native-only reset does not clear browser sign-out; the browser reasserts native pause on its next recovery attempt |

The native ledger has a trusted internal reset primitive with a revision check.
It is deliberately absent from the native-message protocol. It is not an
end-to-end resume operation or evidence that the server and browser agree.
Likewise, a native response marked `active` permits an attempt; it does not prove
that an authenticated browser session is installed or a screen is updating.

## Keep four operations distinct

**Code-only update:** use the existing stopped-browser maintenance path. Preserve
the device identity, credential, trust, browser state and native ledger. An update
must never become an implicit resume.

**Credential rotation for the same device:** the administrator issues a new
credential and advances the server generation. Old credentials and old sessions
are invalid. A paused device stays paused. The first-time profile importer still
refuses rotation handoffs: a checked, private replacement installer is separate
work. Merely writing a new secret must not reset either local recovery layer.

**Resume after intentional sign-out or a repaired terminal error:** the candidate
requires explicit consent and agreement across all three states. A server
resume by itself is insufficient. If an attempt was made while the server was
still paused, the resulting native credential-rejection state must remain stopped
even after the administrator subsequently resumes the server.

**Replacement of a revoked device:** revocation is terminal for that identifier.
The current authority refuses re-enrollment, rotation and resume under the revoked
ID. A replacement uses a separately enrolled new identity and new setup. It must
not inherit the old browser's saved state, reuse an already consumed first-run
claim, or erase retained failure evidence. Revoking or replacing one display must
not disturb another display's credential or valid session.

## Interrupted administration is not permission to retry a mutation

An administrator mutation can commit before its acknowledgement is received.
An unconfirmed rotation still has a one-time private credential handoff; it is not
safe to repeat the original rotation as a generic retry. Confirm the exact
committed record instead. A stale generation cannot authorize a later resume,
and a stale native revision cannot clear a newer local pause.

Owner acknowledgement is point-in-time evidence of the exact server record and
old-request drainage. It does not establish browser consent, protect against a
subsequent administrator change, or authorize clearing local state. Retain
uncertain outputs for review; do not mark the whole display resumed when only
one step succeeded.

## Requirements before accepting a coordinated workflow

The following remain development requirements, not runnable instructions:

1. **Review the exact installation.** Bind the operation to the canonical server
   origin, device, extension/native installation, current server generation,
   native revision, and browser recovery state. Validate private credential/trust
   inputs without displaying their contents. Retain DNS, private IPv4 and IPv6
   support; a proxy or internal DNS must not become mandatory.
2. **Separate replacement from consent.** Stage and validate a new same-device
   credential without clearing pause or terminal state. Reject wrong-device,
   revoked, stale and unconfirmed handoffs as authorization to resume. A reviewed
   confirmation-only retry must not rotate again or select a newer record.
3. **Make resumption explicit and race-safe.** Use an authenticated administrator
   boundary and a trusted browser/setup boundary; ordinary dashboard messages,
   helper starts and manual password login must not grant resume permission.
   Coordinate durable state changes so that a new sign-out wins over an older
   approval. A crash, lost response, concurrent operation or partial write must
   not silently resume the remaining layers or replay consumed approval.
4. **Prove a fresh session separately.** Recheck current server authorization and
   native revision, obtain a newly authorized session using verified TLS, and
   verify the browser's cookie and protected display access. Keep `allowed`,
   `pending`, `paused`, `rejected` and `session ready` distinct. Do not weaken
   ordinary rate limits, cookie protections or display-only route restrictions.
5. **Qualify recovery and retirement.** Test each interruption point and repeated
   request, preserve old-generation rejection and unaffected displays, and retain
   exact evidence for failed operations. Physical screens, service ordering,
   secure unattended keyring handling, boot and combined power loss remain
   separate acceptance gates.

The native transaction, verified transport, browser coordination, trusted consent
page and supervised native protocol bridge are implemented as candidates below.
The real-browser proof adapter passed the isolated two-Pi matrix described below.
Credential replacement, retained-operation maintenance and physical deployment
remain separate requirements.

## Native approval engine: internal candidate only

`browser_device_resume.py` provides a trusted internal `prepare`/`commit` engine.
There is no resume CLI or automatic caller. The generated experimental trusted
page reaches it through the identity-bound native bridge described below.
Existing first-run, ordinary recovery, service and credential import paths never
prepare or commit an approval. Do not invoke internal methods on a real
installation to bypass the pending coordinated acceptance.

Preparation requires an exact stopped native revision, a browser-intent
fingerprint and context-bound evidence of an already-active server record with
confirmed old-request drainage. The installation fingerprint binds origin, device
and extension identity. Preparation also fingerprints the validated private
credential and certificate bundle. It retains the native stopped mode, advances
its revision and returns one private, two-minute approval ticket. Only the ticket
digest is stored; the returned ticket must not be logged or generically serialized.
The browser-intent fingerprint is supplied by a trusted caller: this module does
not read browser storage or verify an actual user gesture.

Private-file checks are snapshots, not a transaction across external file writers,
server authority and browser storage. A future installer must serialize credential
and configuration changes with approval handling. Losing the prepare response
leaves an outstanding approval; neither its ticket nor a replacement approval is
silently recreated.

| Durable approval phase | Meaning |
| --- | --- |
| `prepared` | One exact review is outstanding; native recovery remains stopped |
| `claimed` | Approval was consumed before requesting fresh server evidence; another commit or automatic replay is refused |
| `complete` | Fresh evidence and unchanged inputs allowed an atomic native reset; this is not browser/session readiness |
| `cancelled` | A newer native pause, trusted reset or clock correction invalidated the approval |
| `failed` | Claimed verification failed, or a correctly identified approval was refused for expiry/clock rollback; a later retry cannot revive it |

Commit consumes the ticket before calling the proof adapter, without holding the
native SQLite transaction across external work. The adapter must authenticate its
own transport and confirm the exact reviewed active server generation and drain
result. A display credential must never grant authority to resume a server-side
administrator pause. The Python evidence object is not a signed receipt or a
network authentication mechanism. Returning a fabricated object is not verified
server authorization; same-account/root code and supplied adapters are trusted.

After proof, commit rechecks input fingerprints, native revision/mode and ticket
validity, then saves native permission and `complete` atomically. A new pause can
cancel an approval while proof is blocked, including when the native mode was
already paused. Failures retain their evidence rather than replaying or deleting
it. The engine rejects proof callbacks that return after ten seconds, but cannot
interrupt a callback that never returns. Each bridge action therefore runs under
the existing independent native process supervisor: it kills and reaps a stuck
child at ten seconds, without serializing private exception details.

If the process dies after claiming, `claimed` remains consumed. If commit succeeds
but its acknowledgement is lost, the native ledger may already be active and the
approval `complete`; the caller receives no assurance of success and cannot replay
the reset. **The browser controller retains its own durable pending pause on that
uncertainty.** Browser pause recovery reasserts native pause; it does not finish
an interrupted resume automatically.

Explicit preparation atomically upgrades only the selected native ledger to
schema version 2, including its approval history. Ordinary operations do not
migrate version-1 ledgers. Version-2 state is validated on each open, including
read-only inspection. Older helpers that accept only version 1 refuse an upgraded
ledger; do not downgrade or strip the new table as a rollback method. History is
bounded at 128 approvals and retained, not silently pruned. A reviewed history
maintenance/retirement path is still required before production use.

These native guarantees are necessary but insufficient for a working display
resume. The connected UI/native components below have isolated real-Chromium
evidence; physical deployment and retained-operation maintenance remain gates.

## Verified server evidence and exact-generation sessions

The experimental server adapter adds native-only `POST /auth/device/verify`.
It accepts the fixed device identifier and an optional exact `generation`, using
the same device Bearer credential, origin validation, bounded body/workers and
shared rate budget as `/auth/device/session`. Cookies, browser Origin and Fetch
Metadata are refused. A device credential cannot select administrator operations.

Verification authenticates an active device, waits for older-generation requests
to finish, then reauthenticates the same binding. Only a confirmed result returns
the device, generation, active state and `drained: true`. It does not issue a
session, reveal other records, rotate credentials or resume paused authority.
Unknown/wrong credentials and stale generations are denied; unavailable or
unconfirmed drainage never becomes a successful proof.

The existing session endpoint additionally accepts an exact `generation`. If
supplied, issuance must match it and the response echoes it. Requests containing
only `device_id` retain the original response contract. A pause/resume cycle
between verification and exchange changes generation: the approved exchange is
refused, not silently redirected to the latest record. Every subsequent protected
request still rechecks server authority; verification is not a durable lease.

`browser_device_verification.py` uses only the fixed private native configuration,
credential and CA bundle. It shares verified TLS/hostname checks, bounded response
handling, no redirects, no proxy-environment handling and no cookie jar with the
ordinary transport. DNS names and IP addresses remain supported. Responses must
match the exact schema, device and requested generation. No credential or token
is inserted into the verification result or error text.

The native engine's internal `prepare_verified` checks the reviewed revision and
generation, obtains this verified proof and prepares the one-use approval.
`commit_session` consumes that approval using fresh proof, then separately obtains
a session for its bound generation under an exact native-revision check. It never
modifies browser storage/cookies. Both calls **require an independent process
deadline**; socket timeouts alone do not bound DNS or a slowly dripping peer.
The bridge supplies that deadline; no direct resume CLI is added.

## Durable browser consent: trusted controller

Only a controller explicitly constructed with the trusted resume adapter offers
internal review/resume methods. The generated experimental worker supplies that
adapter; the two-argument Chrome adapter remains unchanged. Dashboard messages,
the old control page, startup and alarms cannot submit resume approval. Do not
construct another controller or write browser storage to bypass this boundary.

One explicit reviewed attempt snapshots the native revision and server generation.
It saves a version-2, paused `resume_pending` record with a fresh intent fingerprint
**before** native approval or network work. Approval tickets and session tokens
remain process-local; browser storage contains neither. The same controller queue
owns approval and normal recovery, so a queued alarm cannot cancel approval
halfway through its intended operation. A newer sign-out synchronously invalidates
the old attempt and saves pause intent without waiting for outstanding I/O.

Only after a fresh generation-bound session, matching native revision, cookie
installation and a trusted adapter's protected-session verification may the
controller persist final browser consent and schedule ordinary recovery. A native
`active` response is insufficient. The Chrome adapter below implements
cookie/document/session verification; controlled-port tests alone do not
establish a working end-to-end browser flow.

Worker loss before the final browser consent commit leaves pending consent paused.
The next worker clears the cookie and reasserts native suspend, cancelling any
outstanding native approval. It retains the pending record rather than replaying
or repairing it; a reviewed recovery/retirement path is still required. Older
browser code rejects this version-2 pending record instead of interpreting it as
ordinary recovery. Corrupt/missing state remains refused.

Final storage acknowledgement is a distinct boundary: if verified installation
and the final consent write actually succeed but its response is lost, saved
consent may already allow a later worker's ordinary recovery. The failed caller
does not report readiness or replay the approval. This is not an atomic transaction
across Chromium storage, native SQLite, the cookie service and server authority.
The browser adapters and maintenance tooling must preserve that distinction.

## Trusted review page and supervised native bridge

The generated bundle's startup page links to `resume.html`. Opening that page
does nothing to server permission, local consent or sessions. It displays the
fixed canonical server, device and extension identity; it never requests a
password or accepts a replacement credential.

1. **Review:** a trusted click requests a non-mutating server verification using
   the installation's saved credential and CA. Native mode must already be
   stopped and the server must already allow the device. The native revision
   must remain unchanged across verification. Only the revision and confirmed
   server generation return to the page.
2. **Confirm:** the user checks the consent box and submits within one minute.
   A volatile, one-use page review is bound to that exact top-frame extension
   document and tab. Caller-supplied revisions, URLs and native tickets are
   refused. A newer pause or changed/expired review cannot prepare approval.
3. **Verify completion:** durable pending consent precedes native prepare and
   commit. The worker installs the fresh Secure, HttpOnly, host-only, Strict
   cookie, checks actual protected access, rechecks native revision and only then
   saves final consent and reports a verified fresh display-only session.

The exact native actions are `review-resume`, `prepare-resume` and `commit-resume`.
Only a caller from the configured extension with the launcher-bound installation
identity can use them. Unknown fields, malformed fingerprints, unsafe numbers
and caller destinations are refused. Private approval tickets remain in the
worker/native exchange, not the page, persistent browser storage or diagnostics.
All three actions use the ten-second process supervisor. The raw `resume`
action remains invalid.

For protected-access verification, the worker opens one inactive tab at the fixed
`/device-display` route. An isolated, top-frame extension script makes a same-origin
`GET /auth/session`, allowing the server to check the actual HttpOnly cookie.
It requires an exact, bounded display-only/enrolled response with sufficient
remaining lifetime and returns only a request-bound Boolean verdict. The worker
rechecks the cookie and tab before accepting it, and closes only the temporary
tab it created. It uses a twenty-second overall deadline and bounded navigation
retries. No dashboard script receives the credential, cookie or response body.

There is one review attempt per worker lifetime, including a refused or lost
review. Reloading the page does not mint another approval. A lost confirmation
does not authorize retry or deletion: retain the pending browser/native records
for administrator review. A reviewed recovery/retirement path for these records
is still a prerequisite for production use.

A successful verified resume also retires the previous volatile logout ticket.
This permits a fresh sign-out in the same browser worker, while old completion
tickets cannot finish or upgrade the new logout operation. Only the trusted
resume path can perform that retirement, and it rechecks verified active
readiness first. A refused, uncertain or superseded resume does not reset the
logout bridge.

The end-to-end harness has `resume` and `resume-stale` cases, using generated wheel
artifacts, actual form clicks, loopback HTTPS and the real native process. Initial
workstation attempts stopped at sandbox initialization before the extension
loaded; those attempts remain blocked, not passes. The revised candidate then
passed eight isolated cases across both ARM64 Raspberry Pis: successful resume
over IPv4, DNS (`localhost`) and IPv6 on each host, plus refusal when server
permission changes between review and confirmation. Chromium 152.0.7977.75 and
151.0.7922.173 ran with sandboxing and TLS verification enabled.

Each successful case verified a fresh protected display-only session, closure of
only the temporary probe tab, a second sign-out in the same worker, and retained
pause after browser restart. The stale-review cases installed no session and
remained paused after restart. Setup performed no authentication exchanges.
The initial runs exposed a harness tab-counting mistake and then the real
same-worker logout-ticket lifecycle bug; neither failed run counts as acceptance.
All eight final passes used the corrected runtime and page-identity assertions.

These are headless, fictional loopback-authority tests through the installed
foreground launcher, not physical-screen, live-scanner, LAN/proxy, cold-boot or
power-outage acceptance. Production TUI services, credentials and Home Assistant
were unchanged. No sandbox, certificate or password-store protections were
disabled to obtain these results.

## What the isolated tests establish

- `tests/test_browser_device_verification.py` covers authenticated non-mutating
  proof, real old-request drain waiting, generation comparison, shared admission,
  malformed responses, certificate/hostname failure and DNS/IPv4 loopback TLS.
- `tests/test_browser_device_resume_transport.py` joins verified loopback HTTPS
  to the real ASGI/owner, authority, native approval and generation-bound session
  code. It verifies display-only access and refusal after server/native changes.
  Its HTTPS fixture forwards to ASGI TestClient; it is not a deployed server.
- `scripts/experimental/test_browser_device_resume.mjs` tests durable pending
  consent, single-use attempts, exact review snapshots, sign-out races, worker
  restart, malformed approvals, cookie/proof failure and lost final storage ACK.
  Storage, cookie and native ports are controlled doubles, not real Chromium.
- `scripts/experimental/test_browser_device_resume_ui.mjs` covers trusted-page
  gestures, document-bound one-use reviews, expiry, strict native wrappers,
  isolated protected-session verdicts, cookie changes and exact probe-tab cleanup.
  Paired logout tests require verified active readiness before retiring the old
  one-use ticket; refused or uncertain resume cannot acknowledge that retirement.
  Browser APIs are controlled doubles; this does not qualify actual UI rendering.
- `scripts/experimental/audit_browser_generated_recovery.mjs` exercises the real
  installed launcher, setup/resume forms, native helper, ASGI server, cookie and
  protected display access in sandboxed Chromium. Its eight two-Pi passes cover
  loopback DNS/IPv4/IPv6, a second sign-out, stale-review refusal and browser
  restart. It does not seed state or inject cookies, and is not a physical test.
- `tests/test_browser_device_resume_bridge.py` sends real native frames through
  identity-bound subprocesses, joins the verified loopback TLS/ASGI fixture,
  checks single-use prepare/commit and wrong-caller refusal, and demonstrates
  each action's independent ten-second kill-and-reap deadline.
- `tests/test_browser_device_resume.py` covers one-use approvals, exact input and
  intent matching, schema integrity, cancellation, expiry/clock rollback,
  concurrent commits, retained uncertain outcomes and atomic write failure.
  Tests join the actual local server-owner acknowledgement for DNS/IPv4/IPv6
  contexts and run a native subprocess that exits immediately after claiming.
  The server-proof callbacks are local trusted test adapters, not verified HTTPS
  evidence delivery or browser consent. These low-level approval tests do not
  create a display session.
- `tests/test_browser_device_resume_boundaries.py` joins the real SQLite authority,
  owner acknowledgement, session middleware and native recovery ledger. It covers
  server-only and native-only resume, all five stopped native modes, paused
  rotation, stale reviews, lost rotation acknowledgement, terminal revocation,
  old-session rejection and an unaffected second device. The in-process ASGI
  requests use exact DNS, IPv4 and bracketed-IPv6 Host identities. They do not
  establish IPv6 socket routing, TLS, browser cookies or stream-drain races.
- `tests/test_browser_device_native.py` replaces a fictional private credential
  file under each stopped native mode and invokes the real native process twice.
  No exchange occurs and the ledger remains byte-identical. A separately invoked
  trusted test-only reset then allows verified loopback HTTPS with the new secret.
  This synthetic HTTP server records the credential; it is not the real device
  authority and does not qualify a credential-file installer.
- The Node recovery tests preserve browser sign-out after native-only reset and
  refuse a different installation identity without rewriting the saved record.
  `tests/test_browser_device_extension.py` additionally joins the coordinator to
  real native framing and a persistent ledger in subprocesses. It demonstrates
  that browser pause reasserts native pause after a trusted test-only reset.
  Browser storage and cookie APIs in these tests are controlled doubles, not
  headful Chromium or physical-display acceptance.

These tests intentionally do **not** claim that coordinated resume or credential
replacement is shipped. They establish the protections that implementation must
preserve. No production password, device credential, Home Assistant connection,
scanner, trust store or display service is used.
