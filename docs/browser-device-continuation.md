# Post-recovery continuation: design boundary

Status: **development design, read-only preflight and internal intent journal;
not an online resume implementation or an administrator runbook**. PR #250 remains experimental.
Do not invoke internal methods on a real profile, delete guards, edit Chromium
storage, replay setup or replace credentials to make a blocked display sign in.
The published manual-login kiosk and remote TUI are unchanged.

This follows the [paused-only guard release](browser-device-resume.md#paused-only-guard-release).
The release allows an ordinary browser start with sign-in still paused. It is
deliberately bound to the exact paused native revision and completed recovery
evidence. A subsequent ordinary resume would change that revision and invalidate
the release; merely exposing a resume button or allowing a native action cannot
safely implement the next transition.

## Implemented now

The native context selects a distinct paused-only worker for this installation.
It does not construct the normal authentication or resume controllers. A trusted
startup request reads the exact clean paused browser record, a read-only native
status, the fixed-origin device-session cookie and the fixed recovery alarm.
Only matching paused state with neither cookie nor alarm produces the explicit
administrator-required message. The worker does not clear unexpected state,
create a session, schedule authentication or initialize a missing record.

Startup hides the ordinary resume link in this role. A directly opened resume
page is inert; an explicit review explains the restriction and leaves its
confirmation disabled. Native checks remain authoritative on every request, so
UI hiding is not a permission boundary. Unguarded installations keep their
existing two-step resume flow.

The internal `BrowserContinuationInspection` provides a **read-only advisory
checkpoint** for a future stopped-installation review. It has no CLI, browser
message, durable journal writer, grant, apply, execute or resume operation.

- Review acquires the managed launcher's stopped ownership and shared private
  profile ownership, then validates the existing canonical release, guard,
  handoff, archive, restored host, runtime and paused native state twice.
- The checkpoint identifies the exact installation and native revision without
  exposing credentials. Its representation omits identity and evidence fields.
- Confirmation accepts only the same in-memory selection in the same inspector,
  within two minutes on both wall and monotonic clocks, and revalidates stopped
  ownership and the same evidence. Copied, expired or changed selections fail.
- Review and confirmation do not change the ledger or recovery evidence, contact
  a server, read or repair Chromium's opaque storage, or permit sign-in.

This checkpoint is not durable consent and must not be serialized or accepted as
authorization by a future mutation. Historical browser acknowledgement is not a
new browser-state observation. Cookie absence is not proof that an undelivered
server session was revoked. Advisory locks coordinate trusted cooperating
processes; they are not protection against root or arbitrary same-account edits.

## Administrator intent journal (internal, not activation)

`BrowserContinuationIntent` adds the first durable step after preflight. It
accepts a fresh trusted **local** confirmation callback, not an advisory
checkpoint, browser message or saved approval. There is no CLI or deployed UI.
The callback displays the exact installation, release, native revision and a
one-use confirmation phrase. Its recorded purpose is narrowly fixed to enabling
a future **fresh browser resume review**, not automatic sign-in or credential
replacement; a broader or different purpose cannot confirm. Cancellation creates nothing; wrong, expired or
failed consent does not create an intent. Both wall and monotonic clocks bound
consent to two minutes, including a final recheck before committing.

The writer holds stopped managed-launcher ownership, shared private-profile
ownership and an **unchanged** native SQLite write transaction. Together these
exclude cooperating launchers, private-input writers and native revision changes
through the consent and commit window. They do not make browser storage or a
remote server part of that transaction and do not protect against arbitrary
root/same-account changes.

The fixed, private `.sdsctl-browser-continuation-intent.sqlite` records the exact
prior release fingerprint, targets, identity, native revision, consent hash and
its own inode. The original guard, release, handoff, archive, native ledger,
credentials, host registration and Chromium-owned storage remain unchanged.
The plaintext confirmation phrase and credentials are not journaled.

| Durable point | Exact read-only confirmation | Normal launch and native requests |
| --- | --- | --- |
| No intent; consent cancelled or refused | No completed intent | Existing paused-only release rules |
| Empty name, prepared row, partial write or any SQLite sidecar | Refused; retain all evidence | Blocked |
| Complete intent commit with unchanged evidence | Confirms only that intent, including after a lost reply | Still blocked |
| Changed evidence, replaced inode, conflicting schema or later native revision | Refused; retain all evidence | Blocked |

The name is created exclusively. Preparation is committed and its parent directory
synced before completion is possible. SQLite rollback-journal mode with
`synchronous=EXTRA` supplies the completion commit/directory synchronization.
An interrupted commit is never retried, rolled forward by the application or
treated as consent merely because a file exists. Confirmation opens read-only,
refuses sidecars before SQLite access, checks exact schema/canonical bytes and
revalidates the original recovery chain. It does not repair an uncertain database.
These durability claims depend on the operating system/filesystem honoring sync;
process-exit tests are not a physical power-loss qualification.

**A complete intent is deliberately not a runnable grant.** Any intent name or
sidecar, including a dangling link, blocks ordinary startup and every normal
worker request. An already-running worker cannot bypass that check. The earlier
advisory inspector also refuses further reviews. Internal exact historical
release confirmation remains available, but cannot authorize launching. No native
revision changes, server request, browser start, service change or sign-in occurs.
Do not invoke this writer on real retained profiles until the successor path has
been implemented and qualified: it intentionally leaves the installation stopped.

## Remaining successor activation, not implemented

The next candidate must preserve the existing identity for same-device
continuation and use a separately reviewed durable authorization epoch. A new
profile or identity is not an interchangeable workaround: enrollment, credential
generation, extension identity and old-session invalidation have different
semantics. Revoked-device replacement remains its own enrollment operation.

| Checkpoint | Required evidence | Does not yet establish |
| --- | --- | --- |
| Recovered and paused | Existing complete release and unchanged historical chain | Permission to change the bound ledger |
| Administrator intent recorded | Fresh exact consent and a durable intent bound to that chain | Native activation, browser consent or server authorization |
| Successor activated (not implemented) | Exact completed intent plus a separately validated durable successor epoch | Browser consent or an installed session |
| Browser resume reviewed | Current clean pause, trusted-page gesture and exact native revision/generation | An installed or usable session |
| Fresh session confirmed | Verified current server authority, completed native/browser exchange and protected-page access | Earlier lost sessions revoked or a power-outage qualification |

Before any new native revision is written, the design must answer all of these:

1. **Preserve history while describing the successor.** Never rewrite the old
   receipt to match current state. Historical recovery validation and current
   runtime permission must be distinct, without weakening the default guard.
   Every normal launch and native request must select the same exact completed
   successor. Missing, partial, stale or conflicting successor evidence blocks
   mutation, including through cached workers.
2. **Specify activation crash states before adding its writer.** Bind selected old evidence,
   expected revision, runtime identity, consent and the intended next state.
   Define synchronization order, commit points and exact confirmation after every
   interruption. A lost reply must never automatically repeat a mutation, issue
   replacement consent or reinterpret a prepared operation as complete. The
   intent journal implements preparation/confirmation only; it does not implement
   the successor's native activation or a consumable runtime permission.
3. **Serialize private-input changes.** Coordinate credential/configuration
   replacement with this transition and native approval handling. Shared read
   locks alone are not a transaction across credential files, SQLite, Chromium
   and server administration. Rotation stays separate, uses a checked private
   handoff and never clears pause just because a new file exists.
4. **Require fresh authority and consent.** Restored local host registration does
   not resume a server-side pause. A display credential can verify current
   authority but cannot perform administrator resume. Browser permission still
   requires a fresh trusted gesture; a later sign-out or native pause invalidates
   an older approval. No ordinary startup, manual login or recovery ACK implies
   this consent.
5. **Keep uncertain exchanges stopped.** Verify the exact active generation and
   old-request drainage, then separately establish a new session over verified
   TLS. An issued session with a lost response remains a server-side concern;
   cleaning browser storage does not revoke it. No credential, approval ticket
   or session token belongs in the continuation journal or public diagnostics.

Canonical HTTPS DNS names, private IPv4 and IPv6 origins remain supported. Neither
internal DNS nor a reverse proxy should become a requirement, and certificate
verification must not be bypassed for an IP-address installation.

## Acceptance gates

Use only fresh isolated fixtures with fictional device identities until the
transition above is implemented and reviewed. Retain previous completed and
uncertain profiles; do not reuse them to manufacture a passing result.

Tests must cover every durable interruption boundary, exact confirmation without
replay, competing launchers/private-input writers, changed revisions/generations,
newer sign-out, stale workers, server rejection and session-response loss. A new
real-browser run must independently prove protected display access after explicit
consent, plus the unchanged behavior of another display. Stopped preflight and
paused startup are necessary but are not that online acceptance.

Physical layout, service ordering, boot and actual combined power loss remain
separate checks. See the [versioned acceptance record](browser-device-recovery-acceptance.md)
for the precise scope of completed qualifications.
