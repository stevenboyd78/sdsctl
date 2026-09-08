# Experimental device replacement and resume boundaries

Status: **internal native approval candidate, not a resume installer**.
There is currently no supported command or browser message that coordinates all
the steps needed to resume an intentionally signed-out managed browser. Do not
call internal Python methods, edit browser storage, delete a ledger, or rerun
first-time setup to work around that boundary. The published manual-login kiosk
and remote TUI are unchanged.

This guide builds on [first-run setup](browser-device-first-run.md),
[service lifecycle](browser-device-service.md) and the
[enrollment/recovery design](managed-display-enrollment-design.md). It describes
what existing components guarantee and what a future explicit workflow must add.
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

**Resume after intentional sign-out or a repaired terminal error:** a future
workflow needs explicit consent and agreement across all three states. A server
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

## Requirements before implementing a coordinated workflow

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

The native transaction portion is implemented as an unwired candidate below.
Browser coordination and the authenticated network evidence adapter are not
implemented. Directly connecting the existing reset to a browser-state write
would not satisfy these requirements.

## Native approval engine: internal candidate only

`browser_device_resume.py` provides a trusted internal `prepare`/`commit` engine.
There is no CLI entry, native-message action, browser form or automatic caller.
The existing first-run, ordinary recovery, service and credential import paths
never prepare or commit an approval. Do not invoke internal methods on a real
installation to bypass the missing coordinated workflow.

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
interrupt a callback that never returns; the future native supervisor still needs
an independent process deadline.

If the process dies after claiming, `claimed` remains consumed. If commit succeeds
but its acknowledgement is lost, the native ledger may already be active and the
approval `complete`; the caller receives no assurance of success and cannot replay
the reset. **The future browser adapter must retain its own durable pending pause
on that uncertainty.** Current browser pause recovery reasserts native pause; it
does not finish a resume automatically.

Explicit preparation atomically upgrades only the selected native ledger to
schema version 2, including its approval history. Ordinary operations do not
migrate version-1 ledgers. Version-2 state is validated on each open, including
read-only inspection. Older helpers that accept only version 1 refuse an upgraded
ledger; do not downgrade or strip the new table as a rollback method. History is
bounded at 128 approvals and retained, not silently pruned. A reviewed history
maintenance/retirement path is still required before production use.

These native guarantees are necessary but insufficient for a working display
resume. Before exposing the engine, implement a durable browser pending/consent
protocol, a verified network evidence adapter, and a generation-bound fresh
session exchange. Server authorization can change after proof; the browser must
not treat this point-in-time native result as permission to accept a session for
an unreviewed generation. Qualify restart, sign-out races, lost responses, schema
upgrade/rollback refusal and real Chromium together before any physical deployment.

## What the isolated tests establish

- `tests/test_browser_device_resume.py` covers one-use approvals, exact input and
  intent matching, schema integrity, cancellation, expiry/clock rollback,
  concurrent commits, retained uncertain outcomes and atomic write failure.
  Tests join the actual local server-owner acknowledgement for DNS/IPv4/IPv6
  contexts and run a native subprocess that exits immediately after claiming.
  The server-proof callbacks are local trusted test adapters, not verified HTTPS
  evidence delivery or browser consent. No display session is created by the core.
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
