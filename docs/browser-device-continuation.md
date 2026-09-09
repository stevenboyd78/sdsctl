# Post-recovery continuation: design boundary

Status: **development design and read-only preflight, not an online resume
implementation or an administrator runbook**. PR #250 remains experimental.
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

## Proposed next transition, not implemented

The next candidate should preserve the existing identity for same-device
continuation and use a separately reviewed durable authorization epoch. A new
profile or identity is not an interchangeable workaround: enrollment, credential
generation, extension identity and old-session invalidation have different
semantics. Revoked-device replacement remains its own enrollment operation.

| Checkpoint | Required evidence | Does not yet establish |
| --- | --- | --- |
| Recovered and paused | Existing complete release and unchanged historical chain | Permission to change the bound ledger |
| Administrator continuation recorded | Fresh exact consent and a durable successor transition bound to that chain | Browser consent or server authorization |
| Browser resume reviewed | Current clean pause, trusted-page gesture and exact native revision/generation | An installed or usable session |
| Fresh session confirmed | Verified current server authority, completed native/browser exchange and protected-page access | Earlier lost sessions revoked or a power-outage qualification |

Before any new native revision is written, the design must answer all of these:

1. **Preserve history while describing the successor.** Never rewrite the old
   receipt to match current state. Historical recovery validation and current
   runtime permission must be distinct, without weakening the default guard.
   Every normal launch and native request must select the same exact completed
   successor. Missing, partial, stale or conflicting successor evidence blocks
   mutation, including through cached workers.
2. **Specify crash states before adding a writer.** Record selected old evidence,
   expected revision, runtime identity, consent and the intended next state.
   Define synchronization order, commit points and exact confirmation after every
   interruption. A lost reply must never automatically repeat a mutation, issue
   replacement consent or reinterpret a prepared operation as complete.
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
