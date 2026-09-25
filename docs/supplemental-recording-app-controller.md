# Finite recording App controller — development status

These are **uninstalled development libraries**, not an available Home
Assistant App option or a supported manual launch recipe. No existing command,
source profile, service, or passive `--prepare-idle-service` path selects them.
Do not run them against a scanner as a substitute for the still-pending
independent supervision and recovery integration.

## Implemented boundaries

| Phase | Explicit owner | Evidence retained |
| --- | --- | --- |
| Native launch | `AppLaunch` | Original accepted Startup, candidate generation, published launch input, host journal, clock, init witness and full BootstrapHost |
| Native ready | `NativeReadyQualification` | Actual returned Ready and the one-time inventory of the four declared private sockets |
| Recording begin | `AppStart` | Original AppLaunch, fresh source/runtime/host/probe checks, returned durable host authorization and ledger intent, and the existing one-use Relay |
| Active inputs | `NativeActiveQualification` | Exact AppStart/Relay/PostBegin, original sockets and immutable input identities, and append-only validated receipt prefixes |
| Host and file observation | `AppRetainedHost` | Original authorization history, fixed metadata worker, owner-thread file read, and continuing original actor/lease evidence |
| Native status observation | `AppActiveSample` | One fresh cached native probe bracketed by complete App source/runtime/input inventories and host/file checks |
| Finalized recording and exit evidence | `AppAuthorizedFinalized` | Original successful AppStart, closed Relay and independently captured Operator; separate actual exit collection and returned journal publication |
| Finalized host observation | `AppFinalizedHost` | Original finalized files plus current complete ordinary metadata/static-file reads; native health and recording flags remain unknown |
| Successful-recording recovery | App-specific `recover_finalized` and `AppRestoredHost` | Original RecoverySession, journal, process/CLI owners and deadline; fresh normal-App status only after durable restoration intent and separate exit receipts |

The direct-policy `Launch`, `Start`, `RetainedHost`, `ActiveSample`, and
`IdleService` admission gates remain unchanged. They do not accept the App
classes just because they share implementation. The App-specific classes are
closed leaf policies; unreviewed subclasses are not admitted.

### One-way launch and begin

`AppLaunch` consumes one controller slot on the original native-idle candidate,
not on a replaceable passive wrapper. Its command hashes and profile pin come
from the already published original launch input. Construction does not dispatch
anything. The only supported action is the combined launch/confirmation path.

Original durable launch permission precedes Engine create/attach. Once the
actual Ready returns, the controller transitions to the socket-aware reader
before confirming Ready under a fresh complete source/runtime/host/probe
bracket. It never reuses the pre-launch assumption that the sockets directory
is empty. The original readiness timestamp and two-second confirmation bound
are not extended.

`AppStart` then consumes a distinct one-use slot on that original AppLaunch.
Fresh checks precede the durable recording authorization and ledger intent.
Only the returned intent permits the existing Relay to send begin. A sent
begin is not a received recording-start acknowledgment, completed recording,
verified file, process exit, or recovered App.

### Continuing observations

After begin, the original retained actor and journal checks apply. Expired
pre-begin Ready and idle reads are not polled again or renewed. Only the
original recording, stop, and recovery limits remain applicable to their
respective phases.

`NativeActiveQualification` now requires the explicit AppStart. The former
read-only synthetic direct-Start contract is not an alternate admission.
Receipt files are validated append-only output, never acknowledgments or a
replacement baseline. Previously seen bytes and inodes must remain unchanged.

`AppRetainedHost` leaves native health and recording flags unknown.
`AppActiveSample` obtains those flags from its fresh probe, not by inference
from recording files. Both preserve the original full collection bound. A
failed input, host, file, or probe observation consumes that path; another
sampler cannot repair it or reuse its result.

### Failure and ownership

A launch, begin, or active-observation failure closes its original recording transport, not a
substituted public handle, while retaining the original process descriptors for
separate recovery. Nested launch error wrappers do not retry a lost transport
close acknowledgment. Explicit final descriptor cleanup remains the owner's
responsibility and does not certify exit.

Startup, original clock, original journal, idle process witness, and input
readers remain borrowed. Nothing here signals the scanner, changes its mode,
stops Core, deletes recordings, reacquires a process identity, or silently
restarts a consumed case.

### Finalized files and independent exits

`AppAuthorizedFinalized` admits only the original successful AppStart and the
exact independent Operator captured before begin. Its constructor checks the
original recording authorization and closed ledger/Relay history. It delegates
the actual fourth-return, EOF, original-pidfd and Engine-exit checks to the
existing collector. The returned exit evidence must then be separately
published to the original host journal. A file or return code alone is not a
successful recording or permission to restore an App.

After those exits, live Ready and input qualification are not refreshed. The
reader instead checks original object identities, sealed authorization and
journal prefixes, immutable finalized files, and independent exit custody.
Lost returns cannot be adopted from a later disk entry or retried. A finalized
reader failure leaves the independent Operator and caller-owned journal intact.

`AppFinalizedHost` retains the full bounded ordinary host/static-file bracket.
It does not infer native health from a completed WAV, and does not dispatch
recovery. The existing direct finalized-host and recovery gates still refuse
these App-specific objects. Original-session restoration and failure branches
use a separate explicit App policy.

The App-specific `recover_finalized` routes the original session from candidate
shutdown observations to `AppRestoredHost` after normal-start intent. It retains
the original process and CLI trackers, executor, journal, and recovery deadline.
The normal status reader must observe a new generation with fresh healthy,
nonrecording state; old Ready health or a successful command reply is not enough.
An unavailable observation stays unavailable while the same session can still
expire by its independent clock ticks. There is no reader replacement, renewed
budget, repeated stop/start command, or fallback to pristine recording state.

This success-only path is not an installed service or independent supervisor.
The preserved/unconfirmed and never-authorized App recovery branches still need
explicit integration before live use.

## Validation scope

The App controller composition tests use actual private source/runtime files,
socket inodes, journal and ledger operations, an owned init pidfd, and bounded
worker threads. App/Engine metadata, Startup provenance, native Ready/Relay
transport, selected host routes, and recording-file results are explicitly
synthetic in those composition fixtures. Separate lower-level tests exercise
actual private transport, native receipt formats and process behavior.

Neither kind of test alone proves an installed end-to-end App lifetime or
real-hardware performance. No test permits extending deadlines or reducing
full source/runtime checks to make a run pass.

## Remaining gates before a human scanner/audio test

1. App-specific preserved/unconfirmed and never-authorized recovery branches,
   with independently retained worker/init handles. They cannot enter the
   success-only path above or treat a lost return as an absent recording.
2. Explicit App-aware service phase assembly, with no idle fallback after
   dispatch and no loss of original clock-only expiry on uncertainty.
3. A separately named source profile and independently supervised entrypoint;
   passive preparation must remain passive.
4. A fresh isolated fixed-command end-to-end lifetime with actual native
   returns, full source/runtime pins, and independently bounded termination.
5. Installed provenance and measured collection timing, plus a reviewed
   fresh-case staging procedure that preserves historical containers/evidence.
6. Fresh human readiness for a finite scanner/audio trial. Historical readiness
   or an earlier successful test is not permission to replay a consumed case.
