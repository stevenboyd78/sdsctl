# Finite supplemental recording qualification (internal, not activated)

The [finite browser-playback trial](scanner-display-supplemental-acceptance.md#finite-playback-only-result--2026-09-22)
passed on 2026-09-22. It deliberately did **not** start a recording. This document
defines the next recording/finalization qualification boundary; it is not a
runnable live procedure or a claim that recording has passed through that path.

The existing schema-1 and schema-2 host plans still require recording to be idle
and both recording inventories to remain unchanged. They must not be used to
start a recording. Do not report an active recorder as idle, ignore its root,
remove files from a protected inventory, or modify a sealed/consumed case.
The normal App, old recordings, profile inputs, credentials and Pi configurations
are not changed by this work.

## Offline final-artifact evidence

`scripts/supplemental_recording_evidence.py` is a read-only, standalone component.
It is not part of the sealed host-service bundle, package startup, a daemon API,
or a recording/playback client. Importing or invoking it does not connect to a
scanner, start an App, create a recording, or dispatch recovery.

Before a future recording start, `capture_baseline()` captures every existing
file under one explicit recording root. It rejects any artifact using the new
case's reserved prefix, including an older timestamp, sidecar, collision suffix
or dot-prefixed metadata temporary file in any subdirectory. The baseline is an
immutable in-memory result for a trusted caller; its
durable storage, authenticity and binding to the installed candidate are separate
requirements, not properties supplied by this component.

The proposed filename template is
`sdsctl-acceptance-<32-hex-case>-{timestamp}.wav`. This preserves the existing
recorder's required timestamp template. The final name is derived from the
accepted start receipt's timezone-aware timestamp, in that receipt's original
zone. The verifier does not use the current clock or rename the writer's output.
A different filename or automatic collision suffix fails verification.

After a separately confirmed stop/finalization, `verify_finalized()` requires:

- The same case and independently established daemon generation as the start
  receipt. The supplied endpoint hash binds metadata to the selected audio
  source without returning that private endpoint in the result.
- Exactly the baseline's files plus one expected WAV and its adjacent `.wav.json`
  sidecar. Every older file must retain its bytes, size, permissions and owners.
  No extra recording, leftover metadata temporary file or unrelated file is
  permitted. Nothing is deleted or repaired on failure.
- A complete stopped/inactive snapshot, exactly one completed recording, no error,
  positive bounded packets/samples/durations, and fully drained sink counters.
  Submitted and written byte counts must both equal twice the PCM sample count;
  drops, queued bytes, underflows, overflows and callback statuses must be zero.
- The recorder's canonical 44-byte RIFF/WAVE header, mono 8 kHz 16-bit PCM, exact
  data length, and no truncated or trailing bytes. This is intentionally not a
  permissive general-purpose WAV validator.
- A complete version-1 recording sidecar with matching filename/format, source,
  start/stop instants, statistics and reliability counters. Duplicate JSON keys,
  non-finite constants, unknown schema fields and coercions such as booleans in
  place of integer counters are rejected.
- Two matching complete file inventories around the payload validation, held
  no-follow ancestor descriptors, stable file reads, and unchanged selected-root
  identity during the observation. Links, hardlinks and special files are refused.

The overall observation has a five-second cooperative budget. The existing
inventory helper retains its two-second per-capture budget, 4,096-file,
8,192-entry, 16-level and 64 MiB aggregate limits. This component reserves room
for the two new files and allows older files up to 16 MiB each. The new WAV has
a maximum of 180 seconds of 8 kHz mono 16-bit samples; its sidecar is bounded to
64 KiB. Kernel I/O stalls still require an independent outer process deadline.
These are qualification limits, not new product limits or retention rules.

The successful immutable result contains only the case/generation, WAV and
sidecar hashes, sample/packet counts, audio duration and old-file count. Failure
returns a fixed sanitized exception and no partial success. It never rewrites a
header, synthesizes metadata, retries recording, removes a partial file or
promotes an unconfirmed result to a pass.

### What that evidence does not prove

This is stable observed **file-content evidence**, not an atomic filesystem
snapshot or protection against a privileged actor replacing data between reads.
It does not attest to directory topology or file-inode continuity between the
pre-start and final inventories. Existing empty directories are not inventory
entries. Directory identity protection during each read and the selected root's
identity across captures are separate checks.

A valid WAV cannot establish that its writer has exited. A supplied generation
or stopped snapshot is not self-authenticating. Transport reliability counters
may include earlier faults; exact agreement proves consistency, not a clean
trial. The result does not claim scanner continuity, browser playback, audible
recording quality, successful optional scanner reads, restart persistence, or
normal-App restoration. Each still requires its own evidence.

## Offline native start/stop owner

`scripts/supplemental_recording_owner.py` now provides an independently tested
native start/stop controller. It is **not connected** to the acceptance launcher,
host service, public daemon API, or normal App startup. Importing or invoking the
module starts nothing. Its explicit Python operations are exercised only by
local fixtures at this stage.

The controller binds one actual `DaemonRecordingManager` to the reserved
template, endpoint hash, recording root identity and pre-start file baseline.
It requires a fresh idle manager, disabled recording organization/overwrite,
and a separate empty private journal. The trusted caller must establish the
native process/generation and exclude all other recording mutators; a supplied
generation string is not independent process attestation.

One frozen monotonic plan fixes preparation, latest start, scheduled stop and
latest completion. Total planned time is at most 180 seconds. Start and stop
have cooperative two- and five-second acknowledgment budgets within those fixed
deadlines. There is no timer thread, I/O cancellation, boot-resume authority or
automatic extension. A qualified caller still has to schedule stop, and an
independent outer supervisor must terminate a blocked or abandoned process.

Before each native dispatch, the controller exclusively publishes a private
intent, flushes it, and fsyncs both file and journal directory. It then rechecks
identity, state and deadline. Successful acknowledgments are bound to the exact
case filename and start time, checked against current native state and retained
as private receipts. Paths and endpoints are hashed in preparation evidence;
arbitrary failure text is never surfaced by the controller.

Lost, late, malformed or failed acknowledgments consume the operation. So do
publication failures and changed preconditions. There is no retry, reopened-case
dispatch or implicit cleanup stop. A nonempty/partial journal is preserved and
refused, even if the previous attempt did not reach dispatch. Advisory locking
and a nonblocking local lock reject cooperating concurrent/reentrant owners.
Links, unexpected files, altered receipt contents and replaced directories fail
closed. These checks do not protect against a privileged actor discarding the
entire case and its evidence.

Receipts record observations, not final acceptance. A slow receipt publication
can finish after the deadline and still leave a receipt on disk while the
controller returns an unconfirmed result. A future host reconciler must not infer
timely success, writer exit or restoration from receipt presence alone. Likewise,
a stopped native receipt with zero samples is possible but fails the separate
artifact verifier. Closing the controller only releases journal descriptors;
native daemon shutdown and independent recovery remain responsible for a writer
left active by an uncertain start or stop.

Local tests cover native PCM finalization, durable-intent ordering, partial
publication/fsync failures, unknown/late acknowledgments, deadline boundaries,
schema faults, journal/root replacement, concurrent calls and descriptor cleanup.
Real localhost RTP/PCMU cases also exercise the controller beside successful or
timed-out FQK/DTM reads; the surviving audio consumer continues after recording
stop, and the resulting pair passes the separate file verifier. These are not
hardware or audible acceptance tests.

## Offline active-file monitor

`scripts/supplemental_recording_monitor.py` adds a read-only observation for the
interval between an acknowledged start and final artifact verification. It does
not replace the existing live host inventory guard. Each observation verifies
every old file, with only the following exact new root-level entries permitted:

| Caller-qualified stage | Permitted new entries |
| --- | --- |
| Recording | One case/start-bound WAV, which may still be zero bytes because native writes are buffered. |
| Finalizing, before publication | That WAV and optionally one adjacent native metadata temporary file. |
| Finalizing, link publication | That WAV and both metadata names, proven to be the same inode with exactly two links. |
| Finalizing, publication complete | That WAV and one adjacent metadata sidecar with exactly one link. |

The temporary name must be `.<expected-wav>.json.<8-character-native-token>.tmp`;
this is not a root or wildcard exclusion. Other-case files, nested new files,
multiple temporary files, wrong types/owners/modes, unexpected links, renamed old
files and changed old contents are refused. A new baseline also refuses a
leftover temporary file from the same case instead of accepting it as old data.

The caller supplies separately qualified writer UID/GID and the expected WAV
mode. Supported modes are 0600, 0640, 0644, 0660 and 0664; the native temporary
metadata remains 0600. This recognizes the real writer's umask-derived mode
without changing it or accepting executable/world-writable output. These checks
do not themselves prove which process opened a file.

One observation has a two-second cooperative budget and retains the inventory
entry/depth/byte limits. It reserves room for three new entries, including both
publication links, so a recording-capable preparation must limit the baseline to
4,093 files. Dynamic growth is included in the aggregate byte bound. WAV and
metadata retain their individual 180-second PCM and 64 KiB bounds.

A trusted previous in-process observation binds the same case, complete start
timestamp, endpoint, generation, root and writer. It rejects shrinking/replaced
new files, publication regression, changed published metadata and a return from
finalizing to recording. Intermediate states may be missed by sampling; they are
not invented. Directory changes during sampling can produce an unconfirmed
observation. A later read must stay within the **original** deadline and cannot
reset it. The monitor never retries, finalizes, cleans up or signals anything.

Successful observations are not atomic snapshots, durable restart receipts,
authenticated writer/process evidence, audio-quality checks or a finalization
verdict. Buffered WAV bytes and headers deliberately are not treated as stable
audio contents. Linked/published metadata is hashed but only the separate final
verifier validates its schema and agreement with the complete WAV and stopped
receipt. Native writer integration tests cover the actual temporary-file,
flush/fsync, link and unlink sequence; fault tests cover races and descriptor
cleanup. No live recording acceptance is implied.

## Offline candidate API restriction

`scripts/supplemental_recording_api.py` provides a permanently restricted native
API subclass for a future dedicated candidate. It keeps the same real recording
manager, so status and library reads remain truthful, including active and failed
states. It does not report an active recording as idle to satisfy a guard.

An explicit observation-only allowlist is applied at the native shared dispatch
boundary used by payload, JSON, control and authorized-JSON entry points. Peer
permissions are intersected with that list and redaction is preserved. Native
capabilities reflect the same restriction. Recording start/stop, scanner controls
(including hold-state wire queries) and profile reload are unavailable.
Supplemental demand is also denied by default. One explicit internal binding may
enable only that operation for the **same exact native finite acquisition owner**,
runtime, frame feed and delivery service. Binding neither arms acquisition nor
creates demand. Failed binding cannot be retried; changed owner/feed/service
bindings fail closed. Peer permissions are still intersected, and the native
arm/window/quota/context checks remain authoritative for every demand.
New product operations are not implicitly granted. Manager/runtime
replacement fails closed. Restriction stays in place after the finite owner is
closed or failed; a client cannot retry an uncertain recording operation.

The finite owner and normal daemon shutdown retain the original manager, not a
facade or second recorder. Local tests exercise all request entry points, real
Unix clients, successful native PCM finalization and failed start/metadata paths.
This component is not installed in the current launcher or normal product. It
does not authenticate clients or prevent trusted in-process code from calling a
manager directly. The narrow offline assembly below qualifies its supported
transport bindings; the installed launcher and independent host supervision
remain separate gates.

## Isolated process-failure qualification

An additional Linux-only fixture runs the actual recording manager and finite
owner in a parent-owned child process with synthetic PCM and test clocks. The
parent opens a pidfd before releasing the stop barrier. It checks orderly exit,
abrupt exit while active, blocked stop, stalled metadata publication, a lost stop
acknowledgment, and a lost return after a stopped receipt was already fsynced.
The blocked cases are terminated and reaped under the fixture's separate deadline.

Only orderly acknowledged finalization is submitted as a successful content
verification. Complete-looking files, receipt presence and independently proven
process exit are kept distinct from a confirmed operation result. Each failed
case preserves its files and consumed intents and refuses another owner attempt;
older recording bytes stay unchanged. These tests prove component behavior in
owned local subprocesses, **not** a recording-capable host supervisor, native
daemon shutdown integration, container/pidfd recovery or normal-App restoration.

## Offline fixed-deadline schedule

`scripts/supplemental_recording_schedule.py` composes the prepared native owner,
restricted API and file monitor into one explicit synchronous run. The caller
must already have independently qualified process supervision and readiness;
constructing the scheduler does not start anything. It checks that the same
manager, runtime, baseline and immutable plan remain bound throughout the run.

After one start, it polls cancellation and native state at most every quarter
second between calls and samples the full file monitor about once a second. At
the original scheduled stop time it invokes the owner's single stop, observes
publication completion and separately verifies the finalized pair. No wait,
observation or slow call extends the original stop/finish deadline. Expired or
cancelled observations do not begin another verification step. A successful
result means only that this schedule acknowledged start/stop and verified file
contents; the shared runtime intentionally remains running.

Cancellation, interruption, changed bindings, unexpected native state, a changed
old file, an unconfirmed observation or a late result consumes the run. The
controller is closed even if cancellation happened before start, preventing a
new scheduler from reusing that prepared controller. This only releases receipt
descriptors: it does not stop or repair the writer. Native shutdown and an
independent outer termination deadline remain mandatory, including when a call
blocks and cannot check cancellation. No automatic retry or recovery operation
is dispatched here.

Tests combine deterministic clock/fault cases with an actual monotonic/event-wait
run using the real recorder and synthetic PCM; the latter finalizes without a
manual stop. No launcher has installed this scheduler.

An optional exact native acquisition owner can now be supplied by the offline
assembly. Only **after acknowledged recording start**, a fresh cached-context
check and a remaining-time check may it be armed. The full read window must fit
before the original recording stop; a slow start or context check never shifts
that deadline. Arming still sends no GET requests. Reads require a separate
explicit consumer demand through the narrowly bound API. The scheduler checks
the native acquisition status on each loop; only ordinary window expiry or
quota exhaustion is an accepted ending. Cancellation, disconnect or another
ending remains unconfirmed. The optional window is ended on every consumed
terminal path, including failure. It cannot be rearmed by replaying the schedule.

Recorded read counts and end reason are separate from the file verdict. A valid
recording with zero requested reads is not a passed supplemental-read test, and
a counted optional read is not proof of a successful reply.

## Offline native daemon assembly

`scripts/supplemental_recording_assembly.py` now coordinates the actual
`DaemonProcess`, `DaemonRuntime`, native recording manager, shared frame owner,
restricted API and finite schedule. It does not replace the scanner/runtime,
install CLI hooks, start anything on import, or provide a live command. The
ready/request interface is an **in-process fixture gate**, not an authenticated
or durable host/operator protocol.

The supported graph requires the same manager for the API, native event stream,
recording-file server and daemon shutdown. The original PCM fanout must contain
only its matching, initially empty router. An optional native PCMU server must
subscribe to the same audio transport. Existing services, changed bindings,
pre-existing PCM sinks and an already-started runtime are refused. This first
assembly rejects destination coordinators/reloaders, MQTT, remote services,
live-audio encoders and waterfall services; it does not silently remove them
from a normal product configuration. Consequently it is not a drop-in wrapper
for the ordinary CLI's complete construction path.

Readiness requires native startup, the expected cached scanner identity and a
supported cached display context. Readiness alone starts no recording and arms
no reads. The one explicit request creates a fixed monotonic recording plan and
durable owner journal. Successful finalization precedes the request for ordinary
native shutdown. A pre-start timeout cannot lose its stop request when native
signal-handler installation resets the signal controller. Cancellation and
failures request shutdown, retain evidence and never retry the scheduled start
or stop. A two-second worker join is bounded, but native shutdown or kernel I/O
can still block; an independently qualified process deadline is mandatory.

Local tests exercise the real native process, loopback scanner UDP, Unix API and
recording-file/event servers, PCM fanout and WAV/sidecar finalization. Additional
cases use actual localhost RTP and a real PCMU Unix consumer with synthetic RTSP
negotiation: FQK/DTM replies and timeouts coexist with the same eight received
audio packets, exactly decoded WAV bytes, continuing PSI and one audio session.
Native cleanup closes servers, sockets, the optional reader and the audio source.
These are not physical-scanner, real-browser, audible or installed-App results.

Separate parent-owned subprocess tests pin a Linux pidfd **before** releasing the
start gate. They exercise successful native shutdown, blocked scheduled stop,
blocked metadata publication, lost stop return and lost receipt-publication
return. The real native SIGTERM handler may request cleanup without causing exit;
the parent independently escalates to SIGKILL and reaps the exact child under its
deadline. Ordinary native `close()` may finalize a recording even when the
scheduled worker never acknowledged stop. Such complete-looking files, a stopped
receipt left before a lost return, and proven process exit are **not** promoted
to a successful recording verdict. No partial artifacts are deleted or repaired.

Still required before live use: the separately reviewed CLI/source assembly,
durable generation-bound operator gate, recording-aware host collection and
sealed plan, and isolated installed-container qualification. The offline recovery
policy below is not an installed permission. Existing handoff plans and their
idle/inventory checks remain unchanged.

## Offline recording-aware recovery policy and dispatch

`scripts/supplemental_recording_handoff.py` defines a separate pure policy and
private journal. `scripts/supplemental_recording_recovery.py` connects it to the
existing fixed-command executor, tracked CLI executions and exact init-process
receipt machinery. Tests use an injected local fake host, not Home Assistant or
Docker. No CLI entry point, background loop, automatic recording start or new
live host-plan schema is installed by these modules.

The new journal requires `prepare_recording` and entry schema 2. This is a
**journal format**, not the existing schema-2 browser-audio host plan. Each
reader rejects the other journal format, including an attempted schema-only
downgrade. The base policy retains its recording-idle rules, default 24-event
bound and original entry format. The recording journal allows at most 48
8-KiB entries and retains exclusive locking, no-follow paths, hash chaining,
file/directory fsync and no replay of previously emitted actions.

The recording contract binds a new UUID case to hashes of its old-file baseline,
recording root, audio endpoint and writer identity. Normal-App protection remains
unchanged. A future recording-aware collector must separately verify the
candidate's immutable source/image/options/profile protections and its complete
changing recording inventory. The old collector's full-inventory pin cannot be
used by omitting the recording root or pretending it is unchanged.

After candidate readiness and live init-process binding, a separate
`authorize_recording` event requires a fresh, complete, healthy, recording-idle
observation and the original pristine inventory. It durably permits one
generation and sets a fixed maximum 180-second recording deadline within the
original candidate window. This event starts nothing and is not acknowledgment
that recording began. Failed publication consumes the controller; reopening
cannot reauthorize the attempt or extend its deadline. A future operator adapter
still must bind its one-use child trigger to this durable permission.

Qualified file observations distinguish:

- **Pristine:** every original file and the exact original inventory still match.
- **Active/finalizing:** unchanged old files plus only the bounded, exact
  case/start-bound WAV and native metadata publication states.
- **Finalized:** separately verified complete content and timely native success
  acknowledgment, not merely the presence of a WAV or stopped receipt.
- **Retained:** scoped failed/partial artifacts preserved after independently
  proven writer exit. This is not a successful artifact verdict or an exclusion.
- **Unknown:** no authority to advance or dispatch recovery.

These are qualified collector inputs, not assertions authenticated by the policy.
The offline collector and checkpoint chain below now provide file collection and
reconstruction; integration with a sealed live host plan is still required. The policy
rejects another generation, changed contract, disappearing new evidence,
regressing file stages, altered final evidence and changes to preserved evidence
during normal-App restoration. Terminal retained failure cannot become a later
invented finalized success.

The candidate's active-recording flag remains truthful throughout. Only the
authorized candidate can be stopped with recording active: at its fixed bound,
an explicit finish request or a confirmed unhealthy state. Native shutdown may
finalize the writer, but submitting a stop never proves that it did so. Invalid
or missing observations cannot extend the recording recovery deadline. An
independent process-level termination mechanism remains necessary if collection,
native cleanup or the host service itself stalls.

Before either next scanner owner starts, the policy requires the previous init
process's durable exact-exit receipt, a separately completed tracked CLI
execution, fresh App state, idle jobs and all protections. A nonzero/lost CLI
reply is reconciled from observed state without retrying the command. A missing
container or stopped-looking App is insufficient. A healthy restored normal App
also requires the corresponding CLI execution to have exited.

The normal App may be restored while a failed recording is retained, provided
its scoped preservation and independent owner exit are both verified. The
terminal phase records **restoration** separately from recording outcome
`not_attempted`, `unconfirmed` or `verified`. None means audible quality,
successful supplemental replies, scanner continuity or complete live acceptance.

Every emitted command is journaled before a second fresh observation. For active
and finalizing files, two independently qualified samples may have different
growth-evidence hashes: normal audio growth must not consume and withhold the
one shutdown intent. Contract, stage, generation, truthful recording state and
all other safety preconditions must still match. Changes or lost replies consume
the intent without retry. This does not permit skipping either full file check.

Offline tests cover the actual journal/executor/tracked-process bridge using
fake host I/O, natural and requested/deadline shutdown, incomplete CLI or init
exit, lost replies, restart reconciliation, publication failure, mismatched
generations, inventory uncertainty, source/protection drift, expiry and format
separation. Previously sealed installed bundles and consumed cases are untouched.
Shared base modules only gained explicit subclass hooks; their default behavior
continues to be regression-tested. New source hashes must be independently
qualified before any future bundle installation.

## Offline protected-file collector and restart evidence

`scripts/supplemental_recording_protected.py` connects actual bounded filesystem
observations to the recording-aware policy's `Files` inputs. It does not launch,
stop or signal anything. It distinguishes a pristine root, growing recording,
metadata finalization, preserved partial output and acknowledged final content.
It never excludes the recording directory from protection or recaptures an
in-progress recording as part of a new pristine baseline.

Before recording, the collector can exclusively save the original baseline in
one private `0600` manifest inside a separate `0700` directory. File and directory
are fsynced. Every old file is rechecked before and after publication; ancestors,
directory identity, manifest identity, ownership and permissions are held or
rechecked without following links. Any incomplete publication remains for review.
The manifest's hash and recording contract must be pinned separately in a future
host plan; the manifest cannot authenticate itself. Root paths and old filenames
remain private and do not appear in failure messages.

Reload validates that original manifest against both external pins, even while
the current recording grows. The manifest is limited to 2 MiB, 4,093 old files,
16 MiB per old file and 64 MiB total. Before start it reserves room within that
total for the entire permitted new WAV and both native metadata publication
names, rather than exhausting the observation budget halfway through recording.
The reserved case prefix is forbidden among
old files, including hidden native metadata temporaries. Decoding rejects
duplicate fields, unknown fields, noncanonical JSON, unsafe paths, numeric
coercions and unsupported permissions. Existing inventory/monitor limits still
apply independently.

Active/finalizing observations use the qualified native monitor. Preserved
partial output is hashed in full twice and bracketed by complete monitor checks:
empty or malformed WAVs, incomplete metadata and the native intermediate
two-link metadata publication can be retained, but unrelated files, replacements,
unsafe links, changed old files or unstable reads cannot. This is preservation
evidence only. The recovery policy separately requires exact writer exit before
accepting it, and records the recording outcome as unconfirmed.

Finalized content additionally requires the native stopped snapshot and a
**separately authenticated successful-return acknowledgment** bound to the case,
generation, contract and start timestamp. A `stopped.json` receipt is not that
acknowledgment. The host still must verify the source, fixed deadline and complete
successful return; this collector does not turn a caller-supplied hash into
authentication. Audio duration and elapsed recording time must both fit the
contract. Complete WAV/sidecar hashes are checked again after the last progress
monitor because a same-size WAV rewrite would pass size-only continuity checks.
The root remains held across final verification. A good file is still not proof
of OS process exit, audible quality or normal-App restoration.

`scripts/supplemental_recording_checkpoints.py` durably retains active/finalizing
observations in a separate append-only hash chain. Each entry contains the exact
qualified file-proof hash and strictly decoded progress, bound to the original
contract, root and start expectation. File and directory fsync precede a returned
tip. The host must independently retain that exact count/hash tip before restart
recovery may rely on it. The chain has no recording or dispatch authority.

Reload checks every entry and every transition, including file identity and
nondecreasing length, metadata publication order, frozen published metadata and
temporary-name continuity. Shrinkage, replacement, stage regression, stale tips,
missing/extra entries, partial writes and lost publication returns are refused.
An additional on-disk entry cannot be silently adopted as an acknowledged tip.
No entry is overwritten, truncated, deleted or repaired. The chain is limited
to 192 entries of at most 16 KiB; exceeding the bound is uncertainty, not permission
to discard history. Collection, manifest operations and checkpoint operations
have five-second cooperative budgets; blocked kernel I/O still requires the
independent outer process deadline.

All private evidence directories must be prepared before the native owner pins
its ancestor identities. Creating a new sibling directory later can change an
ancestor's link count and correctly invalidate that protection.
This also applies to automated qualification: overlapping test sessions that
create/remove directories beneath shared temporary ancestors can invalidate one
another's evidence. Serialize these filesystem/lifecycle runs or isolate their
mount ancestry; do not relax the identity guard to obtain a passing test.

Local integration tests now combine actual native PCM files, original baseline
reloads, durable progress checkpoints and the recording-aware recovery journal.
They distinguish successful finalization, lost native acknowledgment, partial
output and missing process-exit evidence. App/CLI lifecycle and init witnesses
in these integration tests remain fake; this is not installed-container testing.
The source bundle, live observer, independent tip binding, native acknowledgment
authentication and restricted operator launch path are still not installed.

## Offline recording-aware host observation

`scripts/supplemental_recording_host.py` joins the existing Docker/Supervisor
observations with the actual recording collector. Its candidate uses an explicit
`CandidateSeal`, containing the fixed context/package/profile hashes plus the
recording contract. It is deliberately incompatible with the ordinary `AppSeal`.
Growing recording proofs accompany every observation separately; they are never
put into an old full-inventory field or omitted from protection. The normal App
continues to use its original complete recording inventory and pin.

The joined observer retains the existing version, image, container-incarnation,
Core, additional-owner, jobs, restart-policy and elapsed-time checks. It requires
the audio-specific network policy and cannot downgrade to reader-only networking.
Each running candidate's recording evidence must match its observed generation
and sealed contract. Changed source/settings pins are reported without executing
the native probe inside that unqualified App. Missing or concurrent observations
cannot reuse evidence left over from a previous read.

Cached native recording flags remain truthful and separate from artifact stage.
For example, finalized-looking files plus a missing native reply do not become
healthy/idle evidence. A stopped Docker container or retained partial artifact
does not supply the independent init-exit receipt required by recovery policy.

The file join checks that candidate recordings cannot overlap either App's
profile, context or data trees, or the normal recording root. Its stage and
acknowledgment inputs must come from separately qualified durable host evidence;
this component does not authenticate assertions or publish checkpoint tips.
Tests use synthetic Docker/Supervisor responses, fixed-path routing fixtures and
actual temporary growing/retained files. No host installation is implied.

### Schema3 normal-App cached health

`supplemental_recording_normal_read.Sample` provides one fixed normal-App cache
read for the original recording plan. Deployment and recording paths are derived
from its normal protected layout; callers cannot select a candidate, command,
scanner operation or arbitrary path. It reuses the reviewed ordinary cached
probe and exact Engine create/start/exit checks, without manufacturing an
old-style candidate seal. The shared transport leaves the legacy two-App reader's
role and response checks unchanged.

The sample preserves the original plan bytes, clock domain and recovery deadline,
checks the current normal generation before and after execution, and consumes
each attempt once. An unhealthy, unknown or recording-active reply is retained
truthfully; a missing, malformed, late or wrong-profile reply cannot become idle
health. A separately restored normal App may have a new generation, but observing
that generation does not prove the original owners exited or authorize an
ownership transfer. Complete normal profile, package, options and recording-file
protection, plus all independent process/CLI recovery gates, remain mandatory.

The normal-read join expanded the prospective host bundle to 51 private modules, including
both reviewed code fragments embedded in this fixed probe. Earlier 47-module
source/image qualification does not authenticate the new bytes. Local tests join
the actual recording HostObserver to the fixed probe with explicitly synthetic
Engine/HA metadata and clocks. They cover normal-file drift, jobs, lost replies,
clock changes and at-most-once reads; they do not establish installed readiness,
normal restoration or live scanner behavior. No installed service selects this
path yet, and no existing idle-only plan gains recording authority.

### Original host permission to one private begin

`supplemental_recording_host_begin.Start` joins the original confirmed `Launch`
and pre-existing host `recording-ledger` to the existing `Relay`. Construction
only checks evidence; `start_once()` consumes the attempt before any new probe.
It requires the actual `BootstrapHost` and `CandidateQualification`, preserves
the original Ready proof and process handles, and obtains a new passive probe
inside fresh complete source/runtime and host-file observations. Earlier health
is not reused. The earliest contributing timestamp remains subject to the
two-second policy bound, including durable-publication delays.

The actual returned journal authorization must precede a returned ledger intent.
Its digest comes from that exact persisted event, not a caller assertion. The
native finish deadline is converted conservatively from the original policy
clock and clamped to original stop/watch bounds. Only then is the existing
`Relay` constructed; it performs the sole begin send. Sent, started, finalized,
worker-exited and restored remain distinct outcomes. A cancellation, late or lost
return, changed directory, mutated plan or failed source check closes transport
without retrying or releasing the original actor handles. The separate probe
handle remains available until explicitly closed.

This extends the prospective closed host source graph to 52 modules; prior
51-module image evidence does not qualify the new bytes. Tests use actual
journals, fsync, ledger files and an owned init pidfd, but explicitly synthetic
host/Engine/readiness/Relay responses for ordering and interruption cases. No
installed service selects this path. Phase-specific active-recording observation,
independent host supervision/recovery and fresh deployment qualification are
still required before a new physical-scanner recording trial.

### Original init and lease continuity after begin

`supplemental_recording_idle_observer.PostBegin` is a separate read-only path
for the actual original `Idle` and the `Retained` capability created after the
one private begin. It rechecks original init handles, namespace/clock evidence,
lease/claim bytes and directory identities. It does not call `Idle.read()`,
interpret an expired readiness lease as new permission, renew any deadline or
adopt a new file baseline. Startup readiness still expires at its original time;
post-begin checks stop at the already fixed attachment/stop bounds.

Returned `Continuity` retains the initial observation unchanged and timestamps
the earliest contributing current read. Original worker exits may be reported
only through `Retained`; an init exit, changed descriptor, source binding, claim,
clock or original plan refuses the check. These are not daemon-health flags,
recording completion, proof that all owners exited, or restoration authority.
The facade borrows its callers' descriptors; failure or close does not signal
processes, write files or release those handles. A blocked system read still
requires independently supervised host recovery.

Local tests use owned init processes and actual private files, with explicit
synthetic platform/projection and `Retained` inputs. Full phase-specific host
and recording-file observation, fresh source/runtime qualification, installed
service supervision and a new user-observed trial remain separate gates.

`RetainedQualification` now supplies the source/runtime portion for that
post-begin path. It requires the actual `PostBegin` object and uses the same
complete source, interpreter, environment, image, mount and original-init checks
as bootstrap qualification. It brackets each read-only observation with fresh
inventories, retains the two-second limit, and compares original continuity
before and after. It uses only the original stop bound; the exact bootstrap
qualifier remains readiness-limited and cannot be replaced with this class in
`Launch` or `Start`. A worker exit during a sample invalidates that sample; a
stable retained exit does not turn code qualification into native health or
recording success. Local tests join real file inventories and owned init handles
with explicit synthetic platform/continuity inputs. Installed qualification and
the full phase-aware recording collector remain outstanding.

The same original `Start` now exposes a separate read-only `retained_history()`
join for its own successfully returned Relay. It rechecks the whole original
policy journal, the immutable authorization bytes, the recording ledger and
its original start-intent prefix under the retained process capability. It
does not call startup readiness or grant another begin. The original recording
recovery, trial and stop deadlines remain binding, including after readiness
expires. Cancellation, replaced objects, rewritten history and late reads refuse
without discarding the original process handles. This supplies authorization
continuity only, not a recording observation, completion or restoration action.

`Start.read_files()` joins that retained host authorization to the same Relay's
read-only file evidence. There is no caller-selected stage or path: the original
Plan supplies the progress directory, and the actual received native schedule
selects active/finalizing progress. After an acknowledged completion, only the
Relay's original finalized-artifact recheck is used. Both complete host journals
are reread around the collection; phase, objects, generation, contract and journal
state must remain unchanged. The earliest two-second budget includes both host
history checks, and an active read may not cross the original stop boundary.
This remains an uninstalled file adapter, not a full host/source/runtime/native
health observation, checkpoint publication, exit witness or restoration action.
It cannot be used after the separate exit collector closes the Relay transport.

`RetainedHost` supplies the post-begin host/file portion without pretending that
the candidate is pristine. A fixed read-only worker performs the complete
ordinary host checks: Supervisor options and jobs, installed versions, Core,
other scanner owners, current container generation and audio publication, the
normal App's entire protected inventory, and the candidate's static files. Its
internal snapshot has no recording stage and no native-health verdict. The
owning thread brackets that work with the original `Start` authorization and
`PostBegin` continuity, and selects files only through `Start.read_files()`.

The earliest two-second budget includes every contributing read. A changed
phase, history, actor-exit set, clock, Docker route or original object refuses
the sample; active data may not cross the original recording-stop boundary.
A refused or discarded worker remains retained and cannot supply a later
result or a new readiness window. No retries, process signals, handle disposal,
policy writes or progress acknowledgments occur in this adapter. Blocked system
I/O still requires independent host supervision.

The caller must separately surround the whole read with actual
`RetainedQualification` and join an authenticated cached native probe before
claiming full host/source/runtime/health evidence. This uninstalled adapter is
not a replacement for either component or for post-exit restoration. Local
tests explicitly distinguish real original journals and owned pidfds from
synthetic platform, continuity and recording inputs; they do not qualify an
installed Home Assistant runtime.

`ActiveSample` composes those components for one active observation. It accepts
the exact original `RetainedHost` and `RetainedQualification`, not supplied
health flags or a callback. A complete fresh input check precedes creation of
one passive cached-status process. Another full source/runtime/environment
check surrounds the host/file observation and the authenticated native reply.
The final result retains the earliest timestamp from before probe preparation;
the entire contributing window must fit within two seconds and remain before
the original native recording-stop deadline.

The active-only guard requires live actors at both fresh checks already
surrounding the original authorization history. It does not use a cached exit
set or repeat a third process/journal traversal. Both complete checks, all
history validation, and the original two-second limit remain mandatory;
non-active/finalized history can still observe exited workers separately.

Only a current-generation native reply can fill the otherwise unknown health
and recording fields. Known `false` values remain false for policy evaluation;
they are not replaced with success merely because an active WAV exists. Unknown
flags, changed journals or objects, cancellation, stale or late results, and
reuse of the same sample refuse without discarding original actor handles.
The separately owned probe handle is retained until explicit close. This read
does not authorize recording, write policy or checkpoints, receive completion,
stop an App, or grant restoration. Finalized/post-exit observations use separate
mechanisms and must not call this active-only adapter. No installed service
selects it yet; local composition tests are not installed-host qualification.

One namespace constraint is explicit: a host path beneath
`/mnt/data/supervisor/media` is not the same path string as its native `/media`
alias. Their root-path hashes must not be compared as if equal, nor silently
rewritten. `scripts/supplemental_recording_projection.py` now prepares two explicit
canonical manifests from the same original baseline. It changes only the declared
root alias, preserving case, root inode/ownership, every original file, writer,
audio endpoint and duration bound. Both original manifest/contract hashes and the
projection hash remain distinct and must be independently pinned.

This is pure preparation, not a new filesystem capture or permission to adopt
new files. The original host manifest is never overwritten or resealed. The
mapping validator requires the exact running image/incarnation and a writable
bind from `/mnt/data/supervisor/media` to `/media`, refusing duplicate, parent,
nested or changed mounts. It does not collect a fresh inspect itself. A native
process must separately verify its complete baseline in its own namespace;
comparing declared manifest bytes on the host does not provide that observation.

The future sealed adapter still must qualify both namespace observations, native
success authentication and the independent host binding before a live start.
Projection tests use synthetic inspect data, not an installed container. The old
five-wrapper package inventory also does not qualify a future recording-specific
launch bundle by itself.

## Offline dedicated native construction

`scripts/supplemental_recording_construction.py` constructs the narrow candidate
directly from an explicit specification and independently pinned baseline/profile
inputs. It does **not** modify or wrap the ordinary daemon CLI, which constructs
destination/reload services even without configured destinations. The dedicated
specification cannot accept and silently discard ordinary daemon arguments.

Construction validates the endpoint, firmware/window/quota, writer identity,
maximum recording duration and complete original inventory. Socket and receipt
directories must already be private, empty and disjoint from recordings/profile
storage and each other. Explicit Unix socket paths do not inherit environment
defaults. DNS names and IPv4 addresses are supported without resolving them at
construction time; ephemeral RTP ports are restricted to loopback fixtures. A
future installed plan still must qualify its actual fixed RTP publication.

Both construction and read-only launch-plan validation require positive time
headroom beyond the full three-second preparation, configured read window and
ten-second finalization budget. An exact fit is rejected before services are
constructed: durable host intent and dispatch necessarily consume some of the
original contract. Static headroom is not a timing guarantee or a new deadline;
the native begin guard still requires the entire remaining schedule to fit the
original deadline at the time the actual request is received.

The constructed native services are the scanner/runtime, recording manager,
restricted API, events, saved-recording file server and same-source PCMU server.
There are no destination/reload, MQTT, remote-control, encoder or waterfall
services to disable after construction. No network sockets, threads, recording
or scanner commands start merely by constructing the object graph.

An explicit native `run()` starts the normal shared scanner/audio runtime;
readiness does not start recording or optional GET requests. The existing
assembly's separate one-use start request and explicit consumer demand remain
required. Pre-run construction failures close only objects already constructed.
Once native run is attempted, that lifecycle owns cleanup: this context does not
repeat an uncertain stop or finalization on exit. Independent process termination
and restoration remain mandatory.

New loopback integration tests construct the actual native network transports,
use synthetic RTSP negotiation plus real UDP RTP/Unix IPC, and verify exact
decoded WAV bytes with and without optional-read demand. They prove one shared
audio session, denied public recording mutations, preserved old recordings and
native cleanup. They do not qualify an installed CLI, authenticated operator
trigger, container guardian, physical scanning or audible playback.

## Offline independent host recording ledger

`scripts/supplemental_recording_binding.py` durably pins the original host/native
manifests, projection, separate source/plan hashes and host boot identity. This
ledger is independent of native receipt files and recording-progress storage. It
refuses Supervisor-managed paths; an eventual deployment must also prove the
host evidence directory is not mounted into either App or exposed to a client.

One exclusive, file-and-directory-fsynced start intent binds the exact candidate
generation, recovery-authorization evidence and fixed start/finish deadlines.
The start acknowledgment must arrive before the start deadline; completion must
arrive before the original finish deadline. Monotonic regressions, wrong cases,
changed generations/endpoints/contracts and duplicate intents are refused. A
publication failure or cancellation invalidates the live writer. Files are never
repaired, truncated, deleted or retried. Reload returns evidence only, never an
actionable controller—even if the ledger contains only preparation or a start
intent with no acknowledgment.

Returned progress tips are pinned only after complete checkpoint replay. Each
extension verifies both the newly returned tip and the earlier independently
pinned prefix, preventing a rehashed replacement history from superseding it.
An additional complete checkpoint not pinned by this ledger cannot be adopted
on restart. The host ledger is bounded to 197 entries of at most 8 KiB; the
existing progress chain retains its separate 192-entry bound. Reads and writes
have five-second cooperative budgets, not a substitute for outer termination.

Native start/completion success is still a separately authenticated input to
this component. A digest or `stopped.json` does not authenticate it. The ledger
does not dispatch recording, prove process exit, inspect finalized WAV content,
authorize App restoration or claim audible success. An acknowledged completion
and an abandoned case are distinct terminal records. A host reboot changes the
monotonic-clock domain and requires separate administrative recovery; it cannot
resume this ledger under a new boot ID.

Tests use actual private files and checkpoint chains, with synthetic namespace
bindings and native-success assertions. They exercise lost returns, partial
writes, fsync failure, cancellation, malformed chains, prior-history replacement,
unsafe links, changed directories, fixed deadlines and read-only restart. This
qualifies durable host binding offline; the native authentication channel and
installed source/guardian/operator integration remain the next live gates.

## Offline exact-child native-return channel

`scripts/supplemental_recording_channel.py` adds an anonymous Unix `SEQPACKET`
channel for the future native guardian. It has no listening pathname, public
API, process launcher, signal sender or recording command. The caller must pass
only the sender endpoint to its source-pinned child and close the unused endpoint
in each process. The receiver is bound while that exact owned child is alive,
using its parent relationship, start ticks and a retained pidfd.

Per-message kernel `SCM_CREDENTIALS` must match the child's PID, UID and GID.
Socketpair `SO_PEERCRED` alone would identify the creating parent after a fork,
so it is deliberately not used as the sender identity. A descendant with an
inherited descriptor cannot report as the expected child. Unexpected descriptor
passing is rejected and any installed descriptors are closed, including truncated
ancillary data. Messages are canonical, at most 8 KiB, and bound to the original
native manifest/contract, generation, projection and source hashes.

There are exactly two allowed success messages: started, then completed. Both
receipt and validation must finish before their fixed deadlines. Early, late,
duplicate, malformed, missing, wrong-peer or wrong-case reports consume that
receiver without retry. Sender operations have a maximum 200 ms cooperative
socket timeout; validation and publication do not extend the existing run window.
The channel cannot interrupt blocked kernel I/O or replace the outer guardian.

The optional **in-process** native assembly request now accepts a separately
constructed return sender. It validates the original baseline/writer/endpoint,
generation and sufficient remaining fixed window before accepting the request.
The scheduler reports start only after `FiniteRecordingOwner.start()` actually
returns. The assembly reports completion only after final content verification
and successful native cleanup. Normal callers that do not supply a sender retain
their existing behavior. Nothing is added to the public daemon API or CLI.

Isolated child tests now connect this path to the actual dedicated construction,
native lifecycle and localhost RTP writer. A lost return after writing
`started.json` produces no start acknowledgment. An exception after the native
process closes produces no completion acknowledgment even though `stopped.json`
and finalized output exist. Successful runs report the exact decoded sample
count and are separately reaped by the test parent. Kernel-credential identity
does **not** prove that the child executed the approved source; the installed
fixed launcher and full source inventory remain mandatory qualification gates.

This channel is not yet wired into an installed guardian, a durable operator
request or a recording-capable host plan. Receiving a report
is not proof of process exit, unchanged current files, audible quality or App
restoration. Tests do not contact the physical scanner.

## Offline projected return-to-host bridge

`scripts/supplemental_recording_bridge.py` joins the exact-child receiver to the
independent host ledger. It accepts only a new, already durable start intent with
the same source, projection, generation, native manifest and fixed deadlines.
Each operation replays the host evidence before consuming the next actual
kernel-authenticated receive. It does not accept a caller-supplied serialized
`Received` object as authentication or infer a lost return from native receipts.

The start's native plan must follow the host intent. The exact start expectation
has the same case, generation, endpoint and timestamp in both namespaces; only
the explicitly projected root/manifest/contract hashes differ. Completion maps
the acknowledgment to the original **host** contract, independently verifies
current host files and every old file, compares all native artifact fields to
the host result, and replays any independently pinned progress checkpoint.
An unpinned later checkpoint is not adopted. No fresh baseline is captured.

Only after those checks and a timely returned durable publication does the
completion result retain immutable canonical bytes of the actual stopped
payload. A later fresh file check can reuse that payload and its acknowledgment
without reading or trusting a native `stopped.json` receipt. The original relay,
ledger and process bindings still supply authority; a constructed completion
value or a previous good WAV check cannot authorize finalization or restoration.
Later file observations must also match the originally verified artifact.

The cross-container relay also retains the exact authenticated start schedule
and its returned durable ledger entry. `Relay.read_progress(directory)` selects
active versus finalizing from that original schedule, not a caller-supplied
stage or daemon status flag. The first read requires an empty private checkpoint
directory before any publication; later reads recheck the same directory and
only the ledger's acknowledged chain. In-memory file continuity is preserved
even between reads that have not yet been published. Changed start records,
unacknowledged tails, replaced files, late reads and reads crossing the scheduled
stop boundary refuse without renewing any deadline. The read does not publish a
checkpoint or prove successful finalization, health, process exit or restoration.
Once progress has been read this way, completion requires the same original
checkpoint directory and a returned durable chain reaching the last observation.
An unpublished observation cannot be discarded in favor of older evidence, nor
can a byte-for-byte copy in a replacement directory substitute for its identity.
Whole-host policy/source/runtime qualification remains a separate requirement.

The actual cross-container `Relay.recheck_completed()` performs that read-only
check before the separate exit collection. It requires its own original returned
completion and unchanged closed ledger, reloads the exact acknowledged progress
chain from its original directory, and verifies all old files plus the current
WAV and sidecar again. Even a valid same-size replacement must match the original
artifact and file identity. It consumes no new native frame and writes nothing.
The original attachment finish bound and a two-second read limit still apply;
a failed or cancelled check retains the process handles for independent recovery.
This is not native-health evidence, worker/init exit proof or restoration.

Receipt, verification and durable host publication must return before the fixed
deadline. A lost or late publication return consumes this bridge even if a
complete log entry exists. Neither that entry nor native `started.json` or
`stopped.json` enables another start/completion attempt. Cancellation also consumes
the bridge. These cooperative checks do not interrupt blocked I/O and still need
the independent outer guardian.

The qualification combines actual child processes, the dedicated native runtime,
localhost RTP, real WAV/metadata verification and the durable host log. Namespace
aliases are explicitly routed test fixtures, **not** installed mount-namespace
qualification. The bridge itself is a same-process adapter; the installed,
source-pinned cross-container relay/operator path remains a separate gate. It
does not grant authority to launch a scanner-owning process on the host.

If the start return is lost, the log remains start-intent-only and no successful
expectation is invented. Independent termination and conservative preservation
of unconfirmed output must still be qualified; missing acknowledgment must not
be mistaken for an idle recorder or permission to replay. Native/container-init
exit, restoration and audible quality remain separate from this bridge's result.

## Offline preservation after a lost start return

`scripts/supplemental_recording_preservation.py` provides a separate, read-only
failure path. The original `prepared.json`, `start-intent.json` and `started.json`
must agree with the sealed native baseline/projection, host intent, exact
generation, endpoint and fixed plan window. Bounded private files, no-follow
opens, strict canonical data, directory locking and two complete observations
reject unsafe or changing evidence. It does not scan for a convenient filename
or invent a timestamp if a required record is missing.

The result is a **preservation scope**, not a successful start acknowledgment.
It identifies the possible case-specific output for the existing retained-file
collector. Optional stop records may be incomplete, including empty files;
their bounded bytes are pinned but never interpreted as successful stop or
completion. The full original recording inventory and exact allowed new files
still require independent verification. No receipt is repaired or deleted.

The host log can close a start-intent-only case with
`preserve_unconfirmed_start`. Its successful-start and completion-acknowledgment
fields remain empty. The preservation scope has a distinct field, survives
read-only replay, and cannot be followed by start, progress or completion entries.
Lost publication returns and cancellation consume the writer without retry.

A real child-process failure test loses the return after native `started.json`
publication, independently reaps the child, scopes its unchanged receipts, and
verifies retained output through the original host projection. The recording
remains unconfirmed even if native shutdown finalized its files. This test does
not prove installed container-init exit or authorize restoration. Those remain
separate host-policy gates; missing/malformed required receipts still require
administrative review, never an inferred idle state or a fresh recording attempt.

## Offline explicit native launch preflight

`scripts/supplemental_recording_launch_plan.py` reads a bounded private
`launch.json` against independently supplied plan and source digests. Its closed
schema declares only the dedicated finite specification, original native baseline,
accepted profile pin, generation, projection and host-plan binding. Extra ordinary
daemon arguments, destination/reloader/MQTT/remote settings or environment input
are rejected, not silently removed or inherited.

Preflight verifies the original baseline and pristine files, the accepted profile's
deployment/configuration/source/state fingerprint, the exact scanner target and
the native read-window budget. It rechecks profile and plan bytes, requires existing
empty private socket/receipt directories and rejects overlap with immutable inputs
or recordings. No directory is created, no source is imported into the scanner's
profile storage, and no recording or runtime is started. Socket/DNS/runtime creation
is forbidden in the positive preflight fixture.

This is a launch-input reader, not an executable launcher or authorization scheme.
The fixed guardian must independently verify executable/source bytes and expected
plan digest before using it, then recheck relevant pins at the actual launch.
Source/plan hashes supplied by an untrusted caller do not authenticate themselves.
The operator protocol, cross-container relay, independent hard deadline and new
recording-capable host plan remain installation gates.

## Offline private native start gate

`scripts/supplemental_recording_control.py` connects the dedicated native
construction to a one-use ready/begin handshake. Two directional anonymous Unix
socket pairs carry per-message kernel credentials. Only receiving endpoints use
`SO_PASSCRED`, avoiding Linux auto-binding a name when such an endpoint sends.
Both sides pin live process identity and retain a pidfd; the guardian must bind
its child before releasing the child's initial launch gate.

Readiness means the native runtime is ready, the recorder is idle and supplemental
acquisition is unarmed. It does not create a recording or optional-read demand.
The separately source-authenticated host must durably record the intent first;
its hash alone is not authorization. Exactly one begin message must match the
original launch/profile/manifest/generation/source/projection/host-plan pins and
fit the unextended start, read and cleanup deadlines. Wrong peers, malformed or
late messages, cancellation and lost sends consume the attempt without retry.

The child installs and verifies parent-death `SIGKILL` on its main thread before
runtime construction. Its control worker requests recording only after the
authenticated begin. Native start and post-cleanup returns use the existing
exact-child return receiver. The execution path uses no ordinary CLI options,
implicit consumer or public operator API. Real fork/loopback tests verify native
PCM finalization, readiness-only cancellation, malformed begin and a delivered
begin whose guardian return is lost. Lost returns remain unconfirmed even when
the native receipts and recording files exist.

These are private offline functions, not an installed executable launcher. The
fixed source bundle, durable guardian launch guard, authenticated cross-container
relay and independent hard termination still require qualification. Parent-death
signals cannot bound a living but stuck guardian. A successful native return is
not child/container-init exit proof or permission to restore another owner.

## Offline independent process deadline

`scripts/supplemental_recording_watchdog.py` arms a separate watchdog process for
an actual owned `Popen`, with independently supplied start ticks and live-bound
native/guardian pidfds. Arming must happen in a single-threaded, source-qualified
guardian before it releases its child's launch gate. Invalid unbound inputs do
not signal anything; an arming failure after binding kills only that exact native
child. It does not start a process by a caller-supplied command or discover a
replacement owner by PID after failure.

The watchdog closes unrelated inherited descriptors before acknowledging that it
is armed. It watches native exit and guardian exit independently of the guardian's
Python execution. At the original deadline it sends `SIGTERM`, then `SIGKILL` if
needed at the original deadline plus the bounded grace period. Guardian death
triggers immediate `SIGKILL` of the bound native child. There is no disarm,
extension or restart operation; closing the guardian's observation handles does
not cancel the cutoff.

Actual process tests cover ordinary exit, graceful and forced termination,
zero grace, a blocked guardian, a guardian frozen with `SIGSTOP`, guardian death,
failed arming, lost wait returns, dropped inherited sockets and a killed watchdog.
The orphan-process tests use an isolated child subreaper and reap all synthetic
descendants. No live application, scanner or unrelated process is targeted.

A watchdog outcome records only its own action/exit mode. Its signal is not native
exit proof, and its death is not evidence that the native child stopped. The owner
must still independently wait/reap the native child; the host must separately
prove container-init exit and qualify restoration. Wiring this watchdog into an
installed fixed guardian, including fail-closed handling of watchdog death,
remains a source/host-plan/platform gate.

## Offline isolated child entrypoint and source inventory

`scripts/accept_supplemental_recording.py` is a private, uninstalled entrypoint
for the dedicated native runtime. It requires isolated Python (`-I -B`), a
closed set of pinned launch arguments, the exact guardian identity, two inherited
anonymous directional channels, and a one-use inherited pipe gate. It installs
and verifies parent-death termination before waiting at that gate. Extra
inherited descriptors, ordinary daemon arguments, environment configuration,
invalid pins and absent or malformed gate release cannot start the runtime.
Descriptors created after exec by Python's native library loader are not mistaken
for inherited capabilities. Errors expose only a fixed diagnostic.

Actual exec-isolated tests run the child under the separate watchdog against
synthetic TCP RTSP and UDP RTP peers. They cover recording and PCM finalization,
readiness-only cancellation, refusal paths, an extra inherited socket and an
unreleased gate. This proves the local executable boundary, not an installed
image, host permission, browser behavior or physical scanner result.

`scripts/supplemental_recording_source.py` supplies read-only source evidence for
the trusted host/guardian. It inventories the complete package, including assets
and existing bytecode, and a closed native helper bundle. Two matching bounded
observations are required; symlinks, hardlinks, special files, extra native files
or directories, unsafe file modes, overlapping roots and drift are refused.
Candidate code is never imported to calculate its fingerprint. Tests also check
the helper bundle's static and lazy private import closure.

The expected digest must be reconstructed independently from reviewed immutable
source. A matching self-supplied digest does not authenticate the image,
interpreter, third-party dependencies or this collector itself. The legacy
three-launcher/two-entry fingerprint and installed host-plan schemas remain
unchanged and do not grant recording permission. A fixed guardian must qualify
this complete source boundary and arm its watchdog **before** releasing the
child gate. Installed guardian/relay/probe, new host-plan and isolated recovery
qualification remain required before any new live case.

## Offline fixed guardian integration

`scripts/supplemental_recording_guardian.py` connects the private launch plan,
closed source inventory, child entrypoint, ready/begin protocol and independent
watchdog. It requires isolated Python and checks that its loaded private helpers
and installed package come from the exact inventoried locations. The guardian is
part of the closed helper inventory. Source/image/interpreter qualification and
expected pins still originate with the independent host, not the candidate.

Before creating a child, it exclusively publishes and synchronizes a one-use
claim in the existing private `guardian` directory beside the exact host-qualified
`launch.json`. The caller cannot choose an alternate claim namespace. Partial or
complete claims are preserved and refuse reuse. It launches only the fixed child with three inherited
descriptors, a minimal environment and no ordinary daemon options. It binds the
exact child and arms the watchdog before rechecking source and launch inputs and
releasing the gate. Readiness does not start a recording; begin still requires a
separately durable, authenticated host intent.

The guardian consumes ready/start/completion phases once, watches native and
watchdog exit alongside the report channel, and cancels its exact child when a
phase becomes unconfirmed. A dead watchdog while the native child remains alive
cannot be interpreted as normal completion. Native exit is independently waited
and reaped; after native exit, an unresponsive watchdog also has bounded cleanup.
Forced watchdog cleanup is unconfirmed, not a successful outcome. All failures
retain the original claim and recording evidence, without replaying stop or
finalization. A successful native return and actual child exit remain different
facts, and neither proves container-init exit or authorizes another scanner owner.

Isolated staged-Python tests exercise actual child/watchdog processes and real
loopback RTSP/RTP recording. Fault cases include wrong source/plan origins, unsafe
interpreter flags, lost claim synchronization, failed spawn or watchdog arming,
source drift before gate release, lost child identity, failed gate write,
duplicate ready/begin, invalid deadlines, killed and frozen watchers, native
termination and inherited-descriptor isolation. Each driver checks that its
owned children are reaped and descriptors closed. These local results do not
qualify an installed container, host relay, image pin or physical scanner.

## Offline fixed operator and bounded framing

`scripts/accept_supplemental_recording_operator.py` is a private, uninstalled
executable for the fixed guardian. It accepts only isolated Python, five exact
launch arguments, and private stdin/stdout pipes. It forwards the guardian's
actual ready, started and completed messages, then separately reports native
and watchdog exit facts. It does not accept a caller's serialized native report
as an acknowledgment. A single begin frame must match the original source,
profile, launch, generation and recording pins and original readiness deadline.
Readiness alone still performs no recording or supplemental read.

`scripts/supplemental_recording_wire.py` provides length-prefixed canonical JSON
with a 16 KiB limit, absolute deadlines and fixed directional frame counts. It
uses nonblocking private pipe/Unix-stream descriptors and rejects malformed,
duplicate-key, oversized, incomplete or late frames. A failed read or write
consumes the connection; there is no reconnect, replay or replacement timestamp.
Both helpers belong to the closed native source inventory. This framing is not
authentication: an independent host must qualify the source, image, interpreter,
exact execution and durable start intent before any use.

Exec-isolated tests use actual synthetic scanner/RTSP/RTP peers and check the
real recording, native reaping, readiness expiry, malformed begin and native
death before or after start. They also deliberately close the host's report
pipe after start: even when the WAV subsequently finalizes with the expected
samples, the operator returns unconfirmed, preserves evidence and does not
start again. Old files remain unchanged. Framing tests separately exercise
fragmentation, partial and failed writes, EOF, descriptor ownership and deadlines.

These local results do not qualify a Docker exec attachment, host-side durable
intent, current container namespace, cached health probe or installed image.
The host still needs an installed recording-capable plan, an authenticated
one-use attachment, and independent operator/container-init exit
observations. Legacy host-plan schemas and source fingerprints remain unchanged.
Neither a finalized WAV nor this operator's exit alone authorizes restoration of
another scanner owner or marks a live browser/scanner test accepted.

## Offline recording-aware host source collection

`scripts/supplemental_recording_static.py` gives the recording candidate its own
fixed source policy. Running candidates are checked against the complete native
bundle under `/opt/sdsctl-supplemental-recording` and the complete package,
including assets and bytecode. There is no fallback to the legacy idle-only
wrapper fingerprint. When the candidate container is absent, the collector uses
the separately qualified immutable-image pin; it does not claim to have inspected
a nonexistent running process.

The shared read-only mount/overlay validator rejects ambiguous mount paths,
duplicate or missing data/media mounts and mismatched overlay/container identity
before reading candidate code. The recording policy also rejects mounts over the
interpreter, dependencies and executable/helper trees. Image, interpreter and
dependency authentication still require separate host evidence; hashing the
package does not establish those facts on its own.

The recording host collector now joins these static checks to its original
stage-specific full old/new recording inventory. It does not omit that inventory
or classify active recording as idle. The normal App continues using its entire
original protection policy, including all recordings. Legacy source fingerprints
and installed host-plan schemas have not gained recording permission.

Local tests check the new routing, closed source graph without importing candidate
code, asset changes, missing operator source, unsafe/shadowing mounts, malformed
container identities, complete recording-stage collection and unchanged normal
protection. This closes the offline collector gap, not the installed host-plan,
authenticated relay, cached native/watchdog probe or recovery gates.

## Offline passive native/guardian/watchdog health binding

`scripts/supplemental_recording_probe.py` adds live process-tree checks around the
existing five cached IPC reads. Its expected identities, accepted profile and
original deadline must come from the independently authenticated host/operator
connection; matching caller-supplied facts is not authentication. The private
operator reports its actual armed watcher's identity and deadline, separately
from actual native return and exit facts.

The probe retains pidfds for the exact guardian, native child and watchdog and
checks their identity, credentials, PID namespace and parent relationships before
and after cached IPC. Frozen or exited processes, stale/replaced identities,
profile changes, wrong IPC peers or elapsed deadlines yield an unconfirmed result.
It preserves the actual cached health and recording flags, including active
recording, and makes no scanner requests, consumer demand, signals or ownership
changes. It does not reuse an old successful result after a failed read.

Disposable process-tree tests cover these failure paths. Separate tests query
the actual isolated native process over its real Unix IPC before and during a
synthetic recording, checking truthful idle/active flags and zero supplemental
scanner reads. These are local process/IPC results, not an installed probe wrapper
or authenticated cross-container execution. The new host plan, qualified private
attachment, namespace binding and independent exit/recovery gates remain pending.

### Fixed passive probe entrypoint — local qualification

`accept_supplemental_recording_probe.py` is a separate, **uninstalled** isolated
Python entrypoint. Its closed arguments pin the launch file, native source,
runtime package and short sampling deadline. The native source inventory now
explicitly includes the probe and entrypoint (24 files); an older source/image
qualification does not cover these new bytes. No existing App invokes them.

It accepts one bounded canonical request and returns at most one bounded result.
The request binds the original launch/profile/baseline/contract/projection/host
plan/generation context, guardian/native/watchdog identities and original
watchdog deadline. The result includes the request hash, sampling interval and
truthful cached flags. The independent host must still authenticate the actual
exec and original retained actor/clock witnesses around the sample. Neither a
matching request hash nor exit zero proves health, readiness, completion or exit.

`launch_plan.probe_inputs` reads the original sealed manifest and profile without
requiring empty output directories or collecting current recording files. It
returns read-only locations/pins, not a launchable plan or a preservation result.
Its output-directory inspection retains identity/permission/ancestry checks but
does not acquire the active recorder's exclusive receipt lock. A focused test
first reproduced that lock conflict; the non-locking read now passes while the
recorder continues to hold its lock. Launch preflight still requires the original
empty directories and pristine recording inventory, with no bypass option.

Isolated tests run the actual fixed probe against the actual finite native Unix
API both before and during a synthetic loopback recording. Idle/active flags stay
truthful, the recording finalizes normally, and the probe adds no supplemental
scanner reads. Additional cases cover malformed/expired requests, changed pins,
unsafe directories, concurrent writer locks and failed framing. These are local
process/IPC tests, not installed Engine authentication or live-radio acceptance.

The host-side `ProbeCommand` and `ProbeAttachment` are separate from operator
dispatch. Exact-type inspection refuses using either command as the other. The
probe attachment permits one request/reply, rejects unsolicited or extra output
and has a short absolute deadline; it cannot send a recording begin. Its clean
EOF is framing evidence only. The default four-return operator decoder and its
original deadlines remain unchanged. Synthetic Engine metadata and actual Unix
socket tests qualify this transport, not an installed probe dispatcher.

`scripts/supplemental_recording_probe_exec.py` joins that transport to the actual
original `Ready` or post-begin `Retained` instance. It derives the request from
their received context and mapped actors; callers cannot provide replacement
PIDs, a new generation, a request body or an extended recording deadline. One
fixed exec is created through the retained Engine peer. Two actual running
inspections surround a retained probe pidfd and the exact container namespace
mapping before the request is written. Every original recorder actor must remain
live, including when retained return processing would otherwise permit an exit.

The closed reply must match the request digest, original profile and native
identity, with bounded sampling timestamps in the original qualified clock
domain. Its real boolean health/recording flags are preserved. Clean framing,
that probe's actual pidfd exit and its separate exact Engine exit0 are all
required; none is recorder/guardian/init exit or recording completion. A failed
sample is consumed and keeps its probe ID/handle for explicit cleanup, without
closing the borrowed operator connection or retrying. No signal is exposed.

The probe join expanded the host source inventory to43 modules; the explicit
Engine sender profile below expands it to44. Local tests use actual private Unix
transport, owned processes/pidfds and original intent files, but synthetic Engine
and namespace metadata and cached replies. They do not establish installed
image/source/runtime authentication or live daemon health. The host adapter must
still establish and recheck those independent properties, full file protection
and whole-observation freshness around this mechanism before using the result.
No installed observer or service selects this path.

### Explicit Engine response-sender profile — local qualification

The default Engine endpoint continues to require a live root socket peer with
PID greater than1. It does not silently accept PID1. A local socket-activated
Engine exposed why a separate profile is needed: `SO_PEERCRED` identified the
listener creator (PID1), while actual reply `SCM_CREDENTIALS` identified Docker's
different live root process. Creator lifetime alone is not Engine lifetime.
These credential semantics follow the
[Linux Unix-socket API](https://www.man7.org/linux/man-pages/man7/unix.7.html);
[systemd socket activation](https://www.freedesktop.org/software/systemd/man/latest/systemd.socket.html)
can pass an already-created listener to a service.

Only explicit `Endpoint(sender_credentials=True)` selects the new local
mechanism. Each connection sends one fixed bounded read-only API v1.47 ping
before any operation. The witness retains both the original kernel-reported
creator and actual response writer by pidfd/start ticks. Every subsequent
JSON, operator-attachment and passive-probe read uses `recvmsg` and requires
the same live root writer. Later connections cannot silently adopt another
Engine. Malformed, truncated, missing or changed credentials consume the case;
received file descriptors are closed and refused, never adopted as evidence.

An empty orderly EOF may carry Linux's all-zero credential record. That exact
case is accepted only as framing EOF, never as a sender identity or process
exit, and both original process witnesses must still be live. Attachments
borrow this witness; closing an attachment does not close the endpoint's
original pidfds. No arbitrary receive callback or caller-provided PID is used.

Local tests cover an inherited listener and a distinct actual child writer,
changed writers, original-writer exit, framing/refusal and both attachment
types. A read-only local root Engine ping also distinguished creator from
writer. These results do not qualify an installed image, a complete host
adapter, scanner ownership, recording completion or recovery. The host source
inventory now contains44 modules; native source remains24. Older43-module
image qualification is historical, not proof of the expanded host bundle.
No installed plan enables the new profile.

A separate scanner-free local Docker qualification exercised the explicit
sender transport against the actual Engine: one new read-only/network-disabled
container, no mounts, and the already source-qualified fixed probe. Actual
running exec inspections surrounded its retained pidfd and container namespace
checks. The intentionally invalid request produced no health reply; the
probe's retained pidfd exited and its exact Engine inspection reported70.
The harmless container init stayed alive until its own finite lifetime ended;
its separate original pidfd and Engine exit0 were then checked. No signal,
recorder, scanner or installed App was involved. This confirms that narrow
transport/refusal boundary, not the still-pending complete host handoff.

A subsequent fresh local Docker case joined the actual operator's received
Ready envelope to its init, guardian, native reader and watchdog pidfds and
container namespaces. The fixed passive probe returned healthy=true and
recording=false, with its own separately verified process/Engine exit. Closing
the original attachment before any begin request was followed by independently
observed native/watchdog/guardian exits and operator code70. The synthetic
scanner saw only initial MDL, VER and PSI startup, and the original synthetic
recording files stayed unchanged. No supplemental demand or recording began.
The finite fixture init exited separately on its original deadline.

This case used a qualified read-only local image and loopback-only peers, not
Home Assistant or the physical scanner. Its declared host path aliases and
host-plan digest were synthetic; the fixture init was not the installed idle
entrypoint. It therefore qualifies the narrow actual Engine/Ready/probe join,
not installed file protection, the assembled schema3 adapter or restoration.

### Finite dashboard route scope — uninstalled

`scripts/supplemental_recording_web_scope.py` provides one explicit construction
of the existing dashboard behind a closed HTTP route gate. Restricting the
native daemon API is not sufficient by itself: the ordinary HA dashboard also
has web-process administration routes for integration setup and credentials.
The finite surface refuses those routes before reading a request body or
invoking any handler. It also refuses scanner controls, recording start/stop,
profile administration, experimental browser-device routes, waterfall, API
documentation and unknown future routes. No ordinary CLI services or global
dashboard factories are patched or silently removed.

Permitted traffic still passes through exactly one existing authentication
path: trusted HA Ingress admission or native HTTPS session/origin checks.
Display-only session restrictions are preserved, not widened to allow audio
or recordings. An authenticated operator can read status/frames, receive
events and existing PCMU audio, list finalized recordings and play/download
them through the native daemon file service. The only additional non-session
POST admitted is the existing validated supplemental-demand route. It neither
arms acquisition nor expands the native owner's window/read quota. Browser
login/logout affect only the existing bounded browser sessions.

Construction starts no listener or client and admits only built-in themes.
This module is not an executable or a deadline/peer-identity guard. The separate
web launcher still needs source/runtime qualification, binding of all four
native client factories to the original daemon actor, and enforcement of the
independent original lifetime. In-flight media and worker teardown require
separate qualification. The current native source inventory now includes the
five finite-web modules described below (29 modules total). The older host44 /
native24 image proofs do not qualify these new bytes.
The ordinary dashboard and previous acceptance wrapper remain unchanged.

### Original native peer and in-flight HTTP guards — uninstalled

`scripts/supplemental_recording_web_peer.py` supplies exactly the native API,
event, PCMU and recording-file clients. The recording-file client now accepts
the same explicit transport interface as the other three clients; its ordinary
Unix behavior, identifier protocol, inventory admission and content limits are
unchanged. A new connection checks the actual kernel peer PID/UID/GID before
sending a protocol byte. Correct socket permissions alone are not sufficient.

The finite peer component retains the original guardian/native/watch pidfds,
five namespace identities, private socket directory and all four socket inodes.
Its original deadline is copied once, not renewed by requests or reconnections.
Lost actor continuity, a frozen actor, changed socket/directory or expiry closes
its existing client streams and permanently refuses more connections. A bounded
monitor also wakes blocking native event/audio reads. Socket operations retain
the native error/cleanup path; no remote owner is signaled or restarted.

`scripts/supplemental_recording_web_service.py` joins those four factories to the
closed HTTP scope. It checks continuity at HTTP admission and before response
chunks, ends a stalled request body on original expiry, and refuses late success.
If streaming headers were already sent, failure closes the response rather than
manufacturing a successful final chunk. Already delivered bytes cannot be
withdrawn. An uncooperative task is retained as unconfirmed, not declared exited.

Local tests use real disposable process trees, Unix peer credentials and native
clients/HTTP handlers. They include replacement sockets, frozen actors, original
expiry, blocked native audio, stalled HTTP bodies and cancellation failures.
These guards do **not** authenticate caller-supplied actor facts or install a
listener. The fixed launcher/source graph, original authenticated Ready-to-web
join, independent process deadline, actual worker/process exits and installed
recovery still require separate qualification. In particular, task cancellation
is not proof that a synchronous HTTP worker has exited. The new web modules are
not included in the older qualified host/native image, and the product change
also requires a fresh source/image pin before use in any new case.

Each finite HTTP request now has its own connection scope, propagated through
the native worker-thread context. Closing or cancelling that request shuts down
its sockets before awaiting native cleanup; unrelated browser streams remain
open. A late worker carrying an ended request cannot establish a new connection
or report a successful response. A blocked saved-file read therefore cannot
make its native close/finalizer wait indefinitely on the read lock. The ordinary
five-second client timeouts remain unchanged and are still clamped to the
original overall deadline. Local tests cover two simultaneous quiet audio
streams and a deliberately stalled saved-file body; no user recording is used.

### Fixed finite ingress entrypoint — uninstalled

`scripts/supplemental_recording_web_plan.py` reads the existing sealed live
launch inputs without repeating the launch-only pristine-recording preflight
or recapturing any baseline. Its immutable request binds the original context,
actor facts, readiness cutoff and native watchdog cutoff. Startup must finish
before the original readiness cutoff. This input reader does not authenticate
caller-supplied PIDs, acquire readiness or permit a retry.

`scripts/accept_supplemental_recording_web.py` is a separate fixed isolated
Python entrypoint. It accepts only the original plan/source/request pins,
runtime root and readiness cutoff; there are no arbitrary command, listener,
authentication or service options. It requires the full closed native29 source
bundle, installed product origin and the same original inputs before opening
HTTP admission. The listener is fixed to Home Assistant ingress on port8099;
native ingress peer authentication remains active, with forwarded-header trust
and WebSockets disabled. It is never a third native-guardian child.

The distinct private `web` framing role allows one request and one listening
reply, not operator recording messages. Any extra input, attachment EOF, actor
failure, unexpected server return or original expiry ends admission and closes
owned native clients before bounded HTTP cleanup. The terminal executable uses
`os._exit` so Python executor joining cannot extend an unconfirmed shutdown.
This does **not** protect frozen/blocked process code by itself: the independently
armed original container/host recovery cutoff is still mandatory, as are exact
web-process and Engine exit observations. A listening reply is neither healthy
daemon evidence nor recording-start/finish/restore authority. Exit70 is a fixed
terminal/unconfirmed result, not a successful recording acknowledgment.

Local tests cover fixed configuration using a synthetic TCP server, actual
owned Unix peers and pipes, original expiry and attachment loss, plus actual
isolated executable rejection of malformed/expanded inputs before any listener
is created. Real listener/image/runtime qualification, the authenticated
Ready-to-web Engine join, durable one-use host dispatch and installed recovery
remain separate gates. Neither the ordinary WebUI nor Home Assistant/Pi
services are switched by this implementation.

The committed launcher has also been exercised in a read-only, network-none
local image with synthetic scanner/RTSP peers and fresh synthetic profile/media.
Actual HTTP requests from loopback, including a forged forwarded address, were
rejected by native ingress authentication; recording-start and HA administration
routes remained absent. Three fresh cases covered original attachment EOF, a
second input byte and native readiness expiry without begin. The original web
pidfd observed exit separately from listener closure; native/watch/operator and
container-init exits were checked independently. No recording or supplemental
demand was started. This does not qualify successful authenticated ingress/media
playback, a real Engine Ready-to-web join or installed failure/restoration.

## Offline private exec attachment I/O

`scripts/supplemental_recording_exec_stream.py` strictly decodes a bounded HTTP
upgrade, non-TTY Docker stdout segments and four canonical private operator
frames. Docker segments and application frames may have different boundaries.
Unexpected stderr, raw-TTY fallback, malformed/ambiguous headers, excessive or
partial data and extra frames make the connection unconfirmed.

`scripts/supplemental_recording_attachment.py` owns one already-connected private
Unix socket. It sends only the fixed API v1.47 interactive exec/start upgrade
request, receives the ordered ready/started/completed/exited frames and sends at
most one begin frame. It has no connection opener, default Docker endpoint,
exec-creation method or arbitrary command API. An independent host must qualify
that socket and exact exec, and durably save start intent before sending begin.
Neither input dictionaries nor correctly framed messages authenticate themselves.

All I/O is nonblocking with step deadlines constrained by the original ready and
final deadlines. A partial, interrupted, failed or late operation consumes and
closes the attachment without reconnect or replay. Four messages alone are not
clean EOF, and clean EOF is not proof of native, operator or container-init exit.
Those observations remain separate. The protocol follows the
[pinned Engine implementation](https://github.com/moby/moby/blob/v27.5.1/api/server/router/container/exec.go);
this reference does not establish the installed Engine version or compatibility.

Local tests use actual private Unix sockets and a synthetic Engine peer. They
exercise fragmentation, backpressure, lost send returns, deadline expiry, partial
messages and missing/invalid EOF. An integration test forwards the real isolated
operator through that byte transport to synthetic scanner/RTSP/RTP peers: actual
cached IPC observes idle then active recording, the native writer finalizes 1280
samples, original files remain unchanged and native/watcher exits are checked
separately. No Docker daemon or installed App is contacted by these tests.

This closes the local byte-transport gap only. The new host plan still needs
independently qualified image/source/interpreter, authenticated exact exec and
PID-namespace binding, durable dispatch and installed isolated recovery tests.
Legacy schemas and source policies remain idle-only; no live case is enabled.

### Separate finite-web exec transport — uninstalled

`WebCommand` and `inspect_web` describe and check only the fixed finite ingress
entrypoint with its original request fingerprint and readiness cutoff. The
operator and passive-probe inspectors reject this distinct command type. It
cannot supply arbitrary listener, authentication, environment or command options.
Created, starting, running and not-running states remain separate observations,
not proof that the listener is ready or a process has exited.

`WebAttachment` is a distinct one-request/one-listening-reply byte channel using
the existing bounded Engine upgrade and response-sender validation. After the
reply it must remain quiet and open within the original lifetime. Extra output,
partial trailing data or EOF is not continuing web health. It cannot send a
recording begin, request twice, reconnect or produce a success/finish receipt.
Closing it ends only that web attachment; original process/Engine exit still
requires independent observation. Durable one-use web dispatch and binding to
the actual original Ready are not supplied by these metadata/framing types.

### Original-Ready-bound web dispatch — uninstalled

`supplemental_recording_web_exec.Launch` now supplies the distinct local join.
It accepts only the actual received `Ready`, derives the fixed request from
that object's original context and actor report, and uses the unchanged original
readiness/watchdog deadlines. Its `web-exec` sibling directory must be fixed by
the qualified host plan and precreated before idle continuity begins; the join
does not create directories or choose a new case after a failure.

Distinct `WebPins` / `WebClaim` records use their own durable wire kind and exact
web-command inspection. Create intent precedes Engine create; the returned ID
is recorded before inspection; attach intent precedes start. Lost responses
consume the case. Operator `Claim`, `load`, and Engine `Client` remain exact-type
operator-only. Reopening history is read-only and never recreates or reattaches
a web process. Shared file-writing mechanics do not grant recording authority.

The join obtains two actual running Engine inspections around a retained web
pidfd and exact original container/PID-namespace binding. The web process must
be distinct from init/guardian/native/watchdog and outside the guardian's child
tree. Its one closed listening reply must name that process, match the original
request/deadline and have an observation time within this request's original
readiness window. It is not a daemon-health or recording-success receipt.

After the separately authorized single begin, only a `Retained` of the same
Ready can continue the web checks. Neither original deadline is renewed. Changed
identities, original journal/directory replacement, extra output, EOF or expired
time close the web attachment and preserve the retained web process handle.
Closing a channel is not exit proof. `observe_exit()` independently requires
the original web pidfd's exit and exact Engine terminal70 while original init
remains alive. That terminal code means the finite dashboard ended, never that
a recording succeeded. It must run before the operator exit collector closes
the borrowed Engine endpoint. Frozen/blocked code still requires independent
original container/host recovery; this join never signals or restores ownership.

This web join expanded the closed host source inventory to45 modules; native29
and product bytes were unchanged by it. Related local tests exercise durable
files, Unix HTTP, original owned pidfds, post-begin continuity and negative
returns. Their Engine/namespace/listener metadata are explicitly synthetic.
Separate local immutable-image/source/runtime qualification and an actual
Engine-to-native-Ready-to-web trial now pass. Actual kernel listener inode and
web70 exit were matched to the retained original web process. A second isolated
trial froze only that test web process: live/exit checks refused it, and the
independent original finite container lifetime later yielded separately
observed init0/web137 exits. That forced web termination is not recording
success. A host-side signal attempt was denied by the OS and retained as an
unconfirmed case; the fresh trial used an exact-identity helper inside its owned
test container without changing confinement settings.

These use synthetic loopback scanner peers, host path aliases and a finite test
init, not the installed idle service or full host adapter. There was no recording
begin, successful trusted-ingress media claim, user-data change or live scanner
action. The assembled host adapter and independently installed failure/recovery
checks remain separate requirements.
No installed idle-only schema or existing acceptance case gains this authority.

### Schema3 host bootstrap join — uninstalled

`supplemental_recording_host_launch.Launch` connects the original closed host
plan and bootstrap journal to one fixed operator execution. It rechecks the
actual journal bytes, original preparation and deadlines, retained idle/init
identity, and independently collected host preconditions. A durable
`authorize_operator` event must return before the distinct `operator-exec`
claim permits Engine create or attach. The fixed claim directory must already
exist before idle PID1 starts. Lost returns consume the attempt; journal replay
cannot return another launch authorization.

Receiving the actual bound `Ready` does not yet mark the daemon healthy.
`confirm_ready()` separately runs the fixed passive probe through the original
Engine peer, checks its actual retained process exit and exact Engine result,
and joins its reported flags to a fresh complete host observation. A malformed
reply or nonzero probe exit cannot supply health. Only then is `operator_ready`
durably recorded. This still does not authorize recording or send begin.

The original Ready receipt time is conservatively converted through the
original clock interval, never replaced by the time a later check succeeds.
The oldest contributing observation remains subject to the existing two-second
policy bound. Full qualification, probe and host-read latency must fit that
unchanged bound; installed timing has not been established. Failures close the
owned transport while retaining acquired actor/probe handles until explicit
descriptor release. That release proves neither process exit nor restoration.

The bootstrap join used a 46-module host inventory. A separately reconstructed immutable local
image passed read-only/network-none host46 and native29 import/runtime checks;
the earlier 45-module image remains historical. Local joined tests cover real Unix
transport, durable journals and original pidfds with synthetic Engine metadata,
host observations and native replies, under both supported sender profiles.
A separate local Engine/native trial confirmed readiness about 1.68s after the
original receipt without extending the two-second policy bound, then separately
observed original worker/Engine exits and the fixture's independent 65s init
expiry. It sent no recording begin. That trial still used declared synthetic
App/Core/CLI baseline, idle claim and host-path facts; it does not qualify
installed observation latency or restoration. A fresh local frozen-helper trial
also observed all three original worker exits and exact Engine70 while the
helper remained frozen and init was still alive, followed separately by the
original 65s init expiry0. Only the parent-owned helper was signaled for cleanup;
no container/scanner signal or recording begin was sent. The selected broader
regression group passed 8,402 tests; this is not all tests or 100% coverage.
Actual installed source/runtime qualification, the polling health selector,
separate recording authorization, and independently supervised exit/recovery
still need to be assembled and qualified. No live handoff is enabled.

## Offline exact execution and namespace binding

`scripts/supplemental_recording_execution.py` describes only the fixed isolated
operator command and checks exact Engine exec-inspection fields against its
original arguments, pins, container and execution IDs. It does not create an
exec. Created, starting with no PID yet, running with a PID and not-running with
an exit code are distinct; failed startup can produce an exit code without ever
becoming a ready operator. The unchanged original readiness deadline remains in
the command even when inspecting it after completion. Explicit PATH does not
sanitize inherited container environment; image/configuration and interpreter
qualification still apply.

`scripts/supplemental_recording_namespace.py` maps independently inspected host
PIDs to the operator's reported container PIDs. It admits only the qualified
two-level PID layout and exact host-relative Docker cgroup. Init, guardian,
native and watchdog must have distinct identities and matching PID, mount,
network, user and time namespaces. The container init must map to PID 1, native
and watcher must be children of the guardian, and the user/time domains must
match the host. Frozen or uninterruptible processes cannot supply a healthy
observation.

Its witness duplicates an already retained init pidfd, retains guardian/child
pidfds before reading their process details, then rechecks the entire mapping.
An uncertain refresh disables future live claims but retains exact handles for
separate exit observation. Closing releases only its own descriptors. It neither
signals nor finds replacement processes by name. Caller-supplied PID reports,
even when internally consistent, do not establish image/source/exec provenance.

Local tests use serialized proc fixtures for Docker namespace/cgroup semantics
and separately use owned disposable processes for actual pidfd retention, exit,
freeze and cleanup tests. The latter explicitly supply synthetic namespace facts;
they are not a claim of installed Docker authentication. The authenticated host
connection, durable dispatch, new host plan and installed recovery tests are
still required before a physical test.

## Offline durable exec dispatch intents

`scripts/supplemental_recording_dispatch.py` records three strictly ordered,
bounded host-private events: create intent before an exec/create attempt, the
actual returned execution ID before inspecting it, and attach intent after an
independent exact created-state inspection but before exec/start. Each event is
exclusively created, flushed and fsynced, together with its containing directory,
then reread before returning. The original source, launch command, host binding,
container generation, retained live init identity and readiness deadline remain
fixed. Directory replacement, changed history, malformed files, lost publication
confirmation, late returns and interruption permanently consume the controller.

Reopening is read-only reconciliation, never another create or attach capability.
The future qualified host plan must fix the unique directory for the case;
selecting some other empty directory is not evidence that no earlier create
occurred. The ledger does not send HTTP, authenticate caller-supplied source or
inspection values, authorize recording begin, or infer process exit. A separate
durable recording-start intent is still required before the one begin message.
Elapsed-time checks reject late filesystem results; independent outer supervision
must bound kernel I/O stalls.

Tests use actual private files, fsync calls and retained owned process pidfds,
with explicitly synthetic container identities. They cover all three lost-return
positions, actual init exit, closed/foreign witnesses, wrong-thread writes,
partial/corrupt or unsafe entries, replaced directories, altered history and
directory mutation during a read. No installed host service or live case is
enabled by these tests.

## Offline fixed Engine connection and dispatch

`scripts/supplemental_recording_engine.py` joins the durable intents to fixed
Engine create, inspect and upgraded start requests. Its endpoint has no path or
TCP option: the qualified host must expose `/run/docker.sock`. It verifies the
root-owned socket and non-writable root-owned parent, kernel peer credentials,
and the same retained live peer pidfd/start identity on later connections. Host
root is trusted. These checks authenticate the local transport, not candidate
source, container configuration, interpreter bytes or the native launch plan.

Create and inspection responses have bounded HTTP/1.1 Content-Length JSON
framing and a one-second absolute request deadline. Redirects, duplicate fields,
unknown framing (including chunked responses), private errors and late or lost
responses consume the client without retry. Installed Engine compatibility must
be checked independently; there is no fallback to a weaker parser. The returned
exec ID is durable before inspection; a matching created-state inspection and
durable attach intent precede the one upgraded start. Full original intent
directory identity and history are rechecked before each external request.

Local tests use a real Unix listener, kernel peer credentials/pidfds, actual
intent files and fsync, but substitute the fixture-owned socket and UID/GID for
the fixed root endpoint. A separate disposable process supplies a real peer-exit
test. These are synthetic Engine replies, not installed Docker/source evidence.
The client exposes neither arbitrary commands nor recording begin or restoration.
The returned attachment still supplies raw, unaccepted operator frames; independent
source/container/namespace checks and the recording-start ledger remain required.
No installed host plan invokes this component.

After the actual ready frame has been received and before any begin, the client's
one-use `bind_processes` operation performs two fixed, authenticated live-exec
inspections around namespace-witness construction. Both must identify the same
running guardian for the original execution, command and container. PID-zero
startup, changed or stopped execution, lost replies, changed intent history and
failed process mapping refuse without polling or replay. Successful witnesses
remain owned by the caller independently of transport closure; partial failures
close only newly opened handles. Tests join actual local connections and owned
pidfds with explicitly synthetic Engine and Docker namespace facts.

## Offline received-readiness binding

`scripts/supplemental_recording_ready.py` consumes the client's actual first
operator frame; it does not accept a caller-provided ready dictionary. Both the
outer envelope and canonical embedded native ready message must match the
original launch, source, profile, projection, host plan, manifests, contract,
container generation and readiness deadline. Embedded sender identities and
monotonic timestamps are checked, including their order after the durable attach
intent. The original watchdog deadline and three-second grace must fit inside
the already fixed attachment deadline; they cannot be shortened to make a late
case fit.

Only then does it join the message to independently obtained Engine inspections
and the exact live init/guardian/native/watchdog pidfds. The original clock window
must agree with both fresh host clock observations and the processes' time
namespace. Checks before a future begin repeat the original deadline, claim,
clock and process checks. Failure cannot renew readiness, retry attachment or
stand in for a native success. Once constructed, failed live checks retain exact
pidfds for separate exit observation until explicitly closed.

This remains a readiness mechanism, not permission to record. The separately
qualified image/interpreter/source and host plan are prerequisites, and no
installed host service invokes it. The durable recording-start intent, relayed
started/completed returns, complete original-file/checkpoint verification and
independent exit/recovery checks remain required. Tests use actual Unix framing,
intent files and owned processes, with synthetic Engine, namespace and clock
metadata; they do not qualify a live scanner or installed App.

## Offline one-use begin dispatch

`scripts/supplemental_recording_begin.py` joins the actual received-readiness
object to the live host recording ledger. A start intent must already have been
durably authorized and acknowledged, after the host received readiness, for the
same generation, source and projection. The full ledger chain and its original
directory identity are checked around the write. A byte-identical replacement
directory, partial publication, poisoned writer or changed history is refused.

Only the fixed private begin message is constructed, with the original intent
hash, timestamp and native binding. No caller-supplied request body, new deadline
or second begin is accepted. Its bounded send leaves the guardian's existing
preparation margin; the native guardian still checks its entire pinned read and
finalization budget. A lost write return or a failed post-write check closes the
transport and preserves exact process handles and intent evidence for recovery.
Neither a successful send nor durable intent is recorded as a native start
acknowledgment. Actual started/completed returns remain separate requirements,
joined by the private return adapter below.

Local tests join actual private framing, fsynced ledgers and owned process handles
with synthetic Engine/namespace metadata. They exercise replaced/corrupt ledgers,
lost file/directory fsync returns, stale readiness, partial/lost transport outcomes
and insufficient original time. No live App, scanner or recorder is started.

## Offline relayed recording returns and independent exit

`scripts/supplemental_recording_relay.py` joins an actual received-ready object
and returned durable host intent to one begin, then consumes the actual started
and completed frames on that same private attachment. It does not accept a
caller-supplied return dictionary or use receipt files as native acknowledgments.
The original same-process `Bridge` still requires an exact kernel-credential
`Receiver`; its type boundary is not weakened to accept serialized reports.

The relayed outer context and guardian/native/watchdog identities must match
the original readiness envelope. Embedded native-return bytes must be canonical,
match the original recording binding and deadlines, and have ordered receipt
timestamps. Original host ledger identity and history are reread around returns.
The shared completion algorithm still validates the entire original recording
root, independently pinned progress history, sidecar, WAV contents and the
native artifact digest before publishing a host completion acknowledgment.
Lost or late publication returns remain unconfirmed even if a complete entry
exists on disk. No new baseline, resend or second recorder is substituted.

`scripts/supplemental_recording_retained.py` separately retains the original
dispatch and process context after begin. Its read-only checks may outlive the
dispatch readiness window, but cannot extend that window or enable another
create, attach or begin. A worker can exit before its buffered completion is
read: only the original live-bound pidfd can establish that exit. Missing proc
data alone, a frozen process, changed namespace, changed intent chain or a
changed clock domain cannot do so. The original container init must remain live
during this return path. Failed checks retain the exact handles for separate
recovery observations until their owner closes them.

`scripts/supplemental_recording_exit.py` then handles a distinct fourth reap
report. It requires successful native and watchdog reap results, clean framing
and EOF, actual exits through all three retained guardian/native/watchdog
handles, and one independent fixed Engine inspection of the same command and
execution. The guardian cleanup tail is bounded by three seconds and the
original watchdog/grace and attachment deadlines. EOF, a successful recording,
or a not-running Engine flag is never sufficient alone. Successful collection
closes transport but retains the process handles. It does not stop container
init, signal any process, restore the normal App, or authorize restoration.

`scripts/supplemental_recording_reconcile.py` provides a separate failure-path
exit reader. It must capture the actual live original `Ready` before begin,
duplicate all four original pidfds, and authenticate a distinct Engine endpoint
against the same live Engine peer and original running execution. These handles
survive closure of Ready and its recording connection. There is no API that
accepts replacement PIDs, deserialized readiness or a later case deadline.

Polling checks the original host clock/recovery deadline, immutable dispatch
history, endpoint identity and process identities. It reports no result while
any worker remains live. A terminal result requires all three original worker
pidfds to report exit plus an exact terminal Engine inspection for the original
execution and command. Init exit is reported separately, never substituted for
worker exit. Even returncode0 is only an exit fact: it cannot recover a lost
recording-completion return, acknowledge a WAV or restore the normal App. An
uncertain read consumes this observer and retains its
handles until explicit cleanup. Partial capture failure releases only the new
duplicates, leaving caller-owned Ready evidence intact.

This reader is still a same-process object, not an independently supervised
recovery service or descriptor-transfer protocol. Its local tests use real Unix
transport, original Ready objects and owned process handles, with synthetic
Engine replies, namespaces and host facts. They cover closure of the original
handles, init-before-worker exit, frozen workers, changed histories/domains,
wrong execution identity, expired recovery and both Engine credential profiles.
The host inventory is now 47 modules. A fresh independently reconstructed local
47-module image passed read-only/network-none source/runtime/import checks;
the previous 46-module image remains historical. Installed assembly and
qualification remain required.

After polling returns genuine exit evidence, its separate one-use `publish()`
operation can persist that evidence in the original schema3 journal. It accepts
no caller-supplied receipt or digest. Original preparation, actual journal bytes,
case, command, candidate init/generation, launch intent and recovery clock are
rechecked. The receipt must remain at most two seconds old. The sole permitted
policy change is recording `operator_exit_sha256` and setting `finish_requested`;
authorization, file checkpoints, completion and phase are not promoted. No App
action is returned. Review/expired cases cannot be revived, and an uncertain
fsync acknowledgment consumes publication while preserving its on-disk evidence.
The independent recovery session must still qualify actual init/CLI exits,
recording preservation and every other restoration gate.

Original host-history reads now bracket their complete file checks with a pure
schema-3 journal replay. Every entry's schema, hash chain and policy transition
must be valid, and all cached policy values must match that replay, including
the original deadlines, authorization, recording outcome and last event time.
The same journal entries and machine must remain unchanged across the read.
This prevents a changed in-memory cache from standing in for durable authority,
even when individual file bytes still match the cached entries.

Replay uses the existing policy rules and discards their returned actions. It
does not reopen the journal, replace or repair the cache, publish an event,
renew a deadline or dispatch anything. The existing two-second and original
lifecycle bounds still apply. Replay alone is not on-disk evidence: Launch and
Operator retain their separate complete-file and directory-identity checks.

After this observer's actual terminal `poll()`, `recheck()` provides a separate
read-only custody check. It requires the identical original result and digest,
retained original pidfds, immutable dispatch history, original host clock and
recovery deadline, and a fresh terminal inspection from the same authenticated
Engine endpoint. The complete read must fit within two seconds. Copied/equal
receipts, changed process/command/Engine facts, lost handles, altered history or
clock drift fail closed and retain evidence until explicit cleanup.

During exit polling only, a disappeared fixed `/proc` path may precede readiness
of the already-retained original pidfd. That narrow interval returns no evidence
and remains pending under the original deadline. It neither proves exit nor
validates a still-live actor. No terminal Engine read is started from that
incomplete first bracket; an incomplete second bracket cannot publish a receipt.
Other actors, original handle identities and host domains are still checked.
Permission failures, malformed or changed identities and frozen live workers
still refuse. Capture and post-publication custody checks do not admit pending
actors, and no descriptor is rediscovered or deadline renewed.

Rechecking returns the original historical result; it does not change its
timestamp, publish it again or renew the two-second publication window. Init
may have exited since that result, but its original `init_exited` value is not
rewritten or treated as new recovery evidence. This does not verify recording
files or grant restoration. Post-exit file verification still needs the actual
original native completion and independent source/runtime/host qualification.

`supplemental_recording_exit.Finalized` supplies a lower-level post-exit file
check, not the full host-policy join. It must capture the original actual Relay
and pre-begin Operator while the completed recording can still be freshly
verified. It accepts no caller-provided completion, exit receipt, stopped payload
or replacement baseline. `collect_exit()` invokes the existing fourth-return,
clean-EOF, original-pidfd and Engine collector exactly once and privately retains
its actual return. A lost return stays unconfirmed even if the workers exited.

After the original Ready and transport close and the separate Operator has
polled successfully, `read()` brackets finalized-file collection with that
Operator's read-only custody checks. Original completion values, plan, closed
recording ledger, intent bytes, optional checkpoint chain and directory
identities remain pinned. Both old recordings and the complete new WAV/metadata
artifact must still match; a same-sized or same-byte replacement inode cannot
substitute. The whole read must fit within two seconds and the original recovery
deadline. It never receives another frame, publishes evidence, recaptures a
baseline, renews a deadline or dispatches an App action.

This reader borrows its inputs and owns no process handles. Failure consumes
the reader while leaving the caller's independent recovery handles and evidence
intact; closing it does not close the Operator. The live Start/Relay APIs remain
invalid after transport closure. This component does **not** verify the full
host authorization journal, source/runtime, init/CLI recovery or restoration
conditions. Those must still be joined and independently supervised before an
installed trial. Local tests use real private files, wire framing and owned
processes with explicitly synthetic Engine/platform facts, not a scanner test.

`supplemental_recording_host_begin.AuthorizedFinalized` adds the original host
authorization to that file reader. It captures the actual successful `Start`,
its original returned begin tuple, plan, authorization, intent, complete journal
prefix and pre-begin Operator while the completion is still freshly readable.
It constructs the existing `Finalized` reader itself; callers cannot supply
replacement completion, exit or publication receipts.

Its one-use `collect_exit()` delegates to the existing collector. After the
caller closes Ready and the independent Operator polls successfully,
`publish_exit()` delegates once to that Operator's existing publication and
requires exactly its permitted journal change. Publication does not depend on
a new successful WAV read: bad files must not block independent process-exit
reconciliation. A lost return consumes this adapter without adopting an event
that merely appears durable.

After publication, `read()` brackets the existing finalized-file check with
fresh replay-backed, on-disk authorization checks. Original generation,
deadlines, Ready proof, intent and publication prefix remain pinned. Later valid
events from the existing recovery policy may extend that prefix, but review
cannot be reopened as successful file evidence. Reads retain the whole
two-second and original recovery limits. Changed or copied returns, altered
cached policy, mid-read state changes and reentrant reads fail closed.

This adapter neither owns nor closes the caller's recovery handles, and it
does not introduce another recovery state machine. Its unit tests deliberately
use synthetic completion/exit/file facts to isolate ordering; lower-level
transport and file tests remain separate. It is still **not** a full current
host/source/runtime observation, an independently supervised recovery service,
proof of original init/CLI exit, or permission to restore an App. Those gates
remain required before an installed trial.

`supplemental_recording_host_begin.FinalizedHost` joins that original published
`AuthorizedFinalized` reader to fresh ordinary host metadata. It uses the
original fixed Docker/Supervisor routes, App seals, installed-version pins,
Core/CLI identities, protected-file layouts and other-owner inventory. The
original candidate container ID is checked before and after the complete read,
including when stopped; a same-name or same-image replacement is not accepted.
Running containers use the existing complete mounted-package collector. A
stopped candidate uses the existing sealed immutable-image check, which does
not pretend to observe a current process environment.

The whole composition must fit within two seconds and the original recovery
deadline. Current metadata and verified finalized files must agree on boot,
time ordering, original generation and recording contract, with no intervening
authorization-journal change. Both Apps' native health and recording flags stay
unknown. Changed App pins, busy jobs and other-owner state remain visible to
the existing recovery policy rather than being replaced with favorable facts.
No recovery event, signal, App action, deadline renewal or new baseline is
created. Failure or closure retires only this observer; the original file
reader, Start, journal and independent Operator remain caller-owned.

This is a current metadata/file observation, **not** installed host/runtime
qualification, independent recovery supervision or restoration authorization.
Original init/CLI exit evidence and a separately qualified normal-start health
observation remain necessary. Unit tests use real authorization/journal state
with explicit synthetic metadata/files and separately check the original
observer factory and collector bindings; they are not a scanner test.

A fresh local Engine/native check also verified capture and reconciliation after
closing the original Ready object and its own process handles, then separately
observed the original fixture init expiry. A separate fresh case exercised the
actual one-use exit publication and verified only the permitted journal changes,
with no recording authorization or App action. This is actual transport/worker
evidence, but its App/Core/idle facts and host paths are explicitly synthetic;
it does not qualify installed recovery or the new journal publication on HAOS.

Qualification uses actual local Unix framing, durable files, owned processes
and retained pidfds, with explicit synthetic Engine, namespace and operator
metadata. Native call-site/credential tests remain separate. Installed image,
interpreter/dependency and mount qualification, a new recording-capable host
plan, independent recovery supervision and a fresh live case are still required
before this mechanism can be used with the scanner.

A fresh settled local qualification of this exact composition then passed at
commit `fb7f651`: the actual candidate/init, native Ready/probe, complete
protected-file and recording inventories, host collector, positive active-file
growth, finalized-file custody, retained operator exit publication, and the
post-init-expiry stopped-candidate read all completed in their original bounds.
The read-only host UDP-table check used the real `/proc/1/net/{udp,udp6}` files;
the earlier fixture that omitted that declared alias correctly failed closed and
was not retried. HA/Supervisor/Core/CLI metadata, path aliases, and the installed
runtime remain explicit synthetic fixtures, so this evidence still does not
qualify an installed App, independent host recovery, restoration, or a physical
scanner case. No user data, installed service, Pi, HA instance, browser, or
scanner was changed.

The separate `RestoredHost` composition now joins that same original
authorization/finalized-file bracket to the fixed, single-use normal-App cache
reader. It is available only after pure replay of the original journal reaches
`starting_normal` (or `complete`), with both original init exits and the original
worker-exit publication retained. The ordinary host observer still checks full
settings, images, protected content, versions, Core, jobs and other owners before
using native health. The normal App must have a new Engine generation; the
candidate remains the exact stopped original container and is never probed.

Each complete sample constructs a fresh single-use cached reader inside the
original two-second/recovery deadline. Unknown, unhealthy and recording-active
results are not promoted to healthy/idle. An unavailable cache keeps the current
container identity visible with unknown native flags; it cannot reuse a prior
successful result or replay a lost exec. Journal drift, stale time, replacement
containers or retired original custody refuse the join. The metadata-only
`FinalizedHost` keeps its prior behavior: both native flags remain unknown.

Neither collector appends a completion event or dispatches an App command.
Tests using real original journals and explicitly synthetic host/IPC evidence
exercise the existing policy and actual `RecoverySession` tracking bridge through
restoration, including withholding completion until the separately tracked
normal-start CLI exit is recorded. Lost Engine replies never replay that command;
a failed file read cannot suppress the bridge's independent clock-only expiry.
Installed runtime/source qualification and independently supervised recovery
remain separate gates; this is not a claim of a live App restoration.

The success-only `recover_finalized` continuation now connects these readers to
the **existing** bootstrap `RecoverySession`. It consumes an original published
`AuthorizedFinalized` reader once, checks the same journal, Engine route, tracked
processes, CLI image/generation and executor callbacks, and preserves the original
dispatch tracker and all deadlines. It neither creates another session nor
reconstructs missing worker, recording or process authority.

The finalized metadata/file reader serves `candidate_running` and
`stopping_candidate`. Only the original journal's durable `starting_normal`
intent selects the restored-normal cache reader, including the existing fresh
pre-dispatch check. Selection and the complete read share a two-second bound.
The existing policy/dispatch bridge still decides every stop/start action and
requires actual tracked init and CLI exits plus healthy, non-recording normal
state before declaring restoration complete. A new observed generation can be
pinned before health is established; that identity alone is not restoration.

Lost command returns remain subject to read-only reconciliation, not replay.
A failed file/context read permanently retires this continuation's observation
route; the independent clock-only tick still expires the original policy. It
does not downgrade to a pristine or retained file stage, retry initialization,
or restore the earlier active reader. Loop exit closes its two observers and
the session-owned process handles, while the original reader, Start, Operator,
ledger and journal remain caller-owned for preservation and review. Failed or
lost recording completion requires the separate preservation path and is not
accepted by this success-only continuation.

The separate failure path now has an explicit original-lifetime join:
`supplemental_recording_reconcile.Preserved`. It requires the original retained
`Operator`, a closed and unpoisoned original recording ledger, and the original
replayed recovery journal. A confirmed start may have been abandoned after a
lost completion; a lost start requires the separately qualified, durably recorded
preservation scope. That scope names possible output only: it is not a start or
completion acknowledgment. No ledger is reopened and no missing custody is
reconstructed from disk.

Cancellation after native startup but **before recording authorization** has a
distinct read-only evidence join: `supplemental_recording_reconcile.NeverAuthorized`.
It requires the actual retained operator's published exit, all four original
process handles independently showing exit, and the journaled candidate-init
exit. The original recording ledger must still contain exactly its prepared
entry, with no intent, acknowledgment, abandonment or progress. The journal must
contain no recording authorization and retain the `not_attempted` outcome.
Complete current ledger/journal bytes, original directory identities, empty
progress and the exact unchanged recording baseline are checked on every read.
A lost tail, changed evidence, active process or elapsed budget refuses the join;
failure cannot be cleared by catching the exception. Reads hold the original
ledger's nonblocking writer lock and remain bounded by two seconds without
renewing the original recovery deadline. Closing releases no borrowed resources.

This returns only pristine-file evidence, never an artifact, completion,
current App health or a native `recording=False` assertion. It cannot justify
stopping a still-running native process, launch a recording, or restore an App.
It does not weaken `Preserved` or the separate never-launched cancellation path,
and it is not yet connected to the service's native-phase transition.

The separate `NeverAuthorizedHost` adapter brackets that evidence with full
fresh host metadata and requires the **exact original candidate container** to
remain stopped. `NeverAuthorizedRestoredHost` adds a fresh normal-App cache
read only after durable restoration intent and checks its new process generation.
`recover_never_authorized` consumes this continuation once using the original
session, journal, process tracker, dispatch tracker and deadlines. The existing
policy decides restoration; the outcome remains `not_attempted`, the prepared
ledger remains unchanged, and no native launch or recording intent is issued.
It can observe an already-exited run whose Ready publication was never completed;
it cannot reconstruct missing original operator custody. A failed read remains
failed while independent policy ticks can expire the original case. Lost command
returns are reconciled, never replayed. Finalized/preserved entrypoints still
refuse this reader, and this entrypoint refuses their evidence. Tests use actual
original child pidfds and private files with explicit synthetic Engine, namespace,
App metadata and launch bindings, not installed service or scanner qualification.

All four original process handles, including candidate init, must independently
show exit. The original operator-exit publication and candidate-init journal
receipt are also required. The original authorization hash, start/finish limits,
full ledger, pinned checkpoint chain and original recording baseline are
rechecked. An unacknowledged checkpoint tail, changed directory/history, poisoned
ledger or elapsed recovery budget refuses the join. The final bytes may extend
the last acknowledged active checkpoint, but a changed retained result cannot
be accepted as a new baseline. Constructor and each complete read have their own
two-second bounds; the original recording/recovery deadlines never move.

`PreservedHost` brackets those retained-file checks with full fresh host metadata
and requires the same candidate container to remain stopped. It never returns
an artifact or a finalized stage. `PreservedRestoredHost` adds the existing fixed
normal-App cache read only after the durable normal-start intent.
`recover_preserved` connects them to the **original** recovery session using the
same continuation mechanism as the success path, but with separate reader type,
custody and file-stage checks. It consumes this continuation once. Confirmed
normal-App restoration still leaves the recording outcome **unconfirmed**.
Lost CLI returns are inspected without replay; failed file reads remain failed
while the session's independent clock can expire. There is no automatic fallback
between finalized and preserved readers.

Local tests join actual disposable process pidfds, private journals/checkpoints
and partial recording bytes with explicitly synthetic Engine, HA health,
namespace routing and native-return inputs. They also exercise the existing
policy and tracked recovery dispatch. These are not installed service or live
Home Assistant restoration evidence. The installed entrypoint, independent
service supervision and full containerized/platform qualification remain
required before another physical/audio acceptance window.

A fresh local supervised preservation trial also passed on 2026-09-24. After
an actual native start and pinned active-file checkpoint, the host deliberately
closed completion transport and abandoned the original ledger without accepting
a completion reply. The original retained worker handles and exact Engine exec
confirmed exit; the original init lease subsequently expired and its pidfd exit
was separately journaled. Two full `PreservedHost` checks then passed in about
0.62 seconds each. Even though the operator exited with code zero, the result
remained retained files with no artifact or acknowledgment and an unconfirmed
recording outcome. No restart, deletion or retry was used.

This trial ran under an independent local systemd bound, using host-venv Python,
network-isolated synthetic scanner/audio peers and explicitly synthetic
installed metadata/path aliases. A separately built immutable helper image also
passed actual read-only/network-isolated source, import-origin and complete
interpreter verification. These are separate results, not a claim that the
recording/recovery service has been assembled or qualified inside that image.
No physical scanner, Home Assistant instance or Pi was changed by these checks.

A subsequent containerized local trial found that Docker can change the exited
exec's `CanRemove` metadata from `false` to `true` after the original container
exits. The retained-custody reader now permits exactly that one-way bookkeeping
change, only after the original init pidfd proves exit. Every other inspection
field, including unknown fields, remains pinned to the original terminal reply.
Missing/non-boolean flags, a reversal, a changed PID, command, container or result,
and eligibility reported while init is still live all refuse reconciliation.
The original raw reply and receipt hash remain unchanged; the flag is never
authority to remove anything, accept a recording, or bypass restoration checks.
This edge case is covered by a reproduced failing test and regression tests;
fresh image/platform qualification is a separate requirement.

Further local containerized runs exposed a narrow pre-begin timing margin:
complete observations around two seconds correctly refused rather than sending
recording begin. Reducing test-observer Engine traffic improved one run but did
not establish a reliable margin. These refusals are retained as failed cases,
not retried or counted as recovery passes.

The read-only runtime collector now checks its fixed, disjoint filesystem roots
with at most two workers. It still hashes every file in both complete snapshots,
checks ownership, modes, aliases, absence rules and descriptor/name identity,
and uses the same original absolute deadline. A shared reservation budget is
enforced before file reads; limits are not multiplied by worker count. Work is
queued only for the fixed roots, never for every discovered file. Failure cancels
remaining reads, and workers are joined before parent descriptors close or any
evidence can escape. Blocked kernel I/O still requires independent supervision.

The affected regression passed 375 tests, including concurrency, global-budget
and failed-worker cleanup checks. A read-only, network-isolated two-CPU local
measurement reduced the complete runtime check from about 1.06 to 0.90 seconds,
with the identical runtime fingerprint. That staged measurement is not an
installed-platform performance guarantee or a completed recording/recovery trial.
The two-second observation limit and all native/readiness/recovery deadlines
remain unchanged.

A subsequent one-CPU measurement compared both worker widths against the same
immutable runtime pin in alternating order. Two workers took about 1.30–1.36
seconds; one worker took 1.07–1.09 seconds. The collector therefore defaults to
one worker for the prospective CPU-limited helper. Trusted local callers can
explicitly select two workers; no automatic retry, deadline renewal or inventory
change follows that selection. Only integer widths one and two are accepted.
Both modes retain full before/after inventories and shared limits. The affected
regression passed 401 tests, including equal fingerprints, invalid selections,
both modes' mutation/timeout rejection, and the existing concurrent-budget and
worker-cleanup checks. This read-only timing result does not itself qualify
combined readiness, real Supervisor latency or the installed service.

An isolated check on the actual Home Assistant OS host exposed the hardware
limit more clearly. With one CPU, a complete native-source/runtime bracket took
about 1.98 seconds without added query delay; a sample with 430 ms of synthetic
delay correctly refused at the unchanged two-second bound. Profiling attributed
about one second to hashing alone across both complete runtime inventories.
The running Mimic App retained its original container, PID and start time.

A fresh isolated two-CPU/two-worker check kept the identical runtime fingerprint
and passed three predetermined samples: about 1.53 seconds without added delay,
and 1.99 seconds for each of two delayed samples. That still leaves almost no
margin in the delayed case and is **not** full service/readiness qualification.
The helper had no network, Engine socket, App data, recordings or credentials;
an independent finite systemd unit bounded its lifetime. No App resources were
changed. A profiling-driver failure was closed separately, not counted as a pass.

The candidate and post-begin qualification adapters now accept an explicit
`runtime_workers` choice of integer one or two from their trusted assembly.
The default remains one. Each collector retains its original choice, rejecting
later changes (including equal-valued floats or booleans), and never changes
width or retries after a timeout. Both ordinary and bracketed reads use that
same choice. CPU allocation, helper confinement and combined elapsed time must
still be qualified separately; this option does not configure Docker resources,
relax the two-second bound, cache observed bytes or enable a service.

A later check of the updated bundle refused the delayed sample at 2.003 seconds;
the earlier narrow pass was not stable timing margin. A separate read-only
profile showed uneven work between the two workers. The explicit two-worker
path now assigns the required `libpython3.14.so.1.0` file its own fixed task,
instead of hashing it in the same task as the entire Python package tree.
This is not an exclusion: the file is still read once in each full snapshot,
with the original shared byte/entry budgets, file identity checks, absolute
deadline and depth limit. Its selected parent descriptors remain open and fully
checked until every task finishes, including after the ordinary tree walk ends.
The default single-worker path is unchanged; no per-discovered-file work queue,
adaptive retries, cached bytes or renewed deadline is introduced. Full inventory
fingerprints must remain identical. Actual host timing and the complete service
remain separate qualification gates.

Both worker modes now bind every fixed task's parent directories before the
first worker is submitted. A full regression exposed an ordering gap: an early
`usr/local` read could mutate a later task's `etc` ancestor before that ancestor
was pinned, allowing both snapshots to agree despite the in-flight change.
Deterministic tests reproduce that gap for both `etc` and `usr/share` before the
fix, and require refusal afterward. Partial parent-binding failure closes all
earlier handles without starting a worker. The inventory, source scope, shared
budgets and deadlines are unchanged; the helper/native source pins must be
reconstructed for the changed verifier.

On the actual host, a later isolated check again refused the delayed bracket
at its original two-second limit. A read-only profile found no CPU-quota
throttling; the remaining `usr/local` package tree dominated the two-worker
path. That path now gives the fixed, required `site-packages` subtree its own
task, alongside the library and other fixed roots. Packages, assets and bytecode
are still read exactly once per snapshot, with the same global budgets and
original depth below `usr/local`. All parent chains remain bound before any
worker starts. The default one-worker traversal is unchanged. This is work
distribution, not an inventory exclusion, cached observation or renewed budget;
actual timing must be qualified independently for this updated source.

The host's repeated plan checks now retain a separately decoded canonical plan
and compare every field, nested type and original byte string against it. They
also require the same original plan object. This removes repeated JSON decoding
and cross-field reconstruction from the hot guard path without caching any
clock, filesystem, process, container or readiness observations. Changed raw
bytes, nested records, equal-but-differently-typed values and substituted plan
objects still refuse; no later plan becomes the baseline. The initial strict
decoder, source pins, full live observations and original deadlines remain in
place. A local pure-plan benchmark reduced 1,000 such checks from about
1.92–1.98 seconds to 0.049–0.052 seconds. That is an isolated CPU measurement,
not a passing combined-readiness or installed-service timing result.

The same boundary applies to repeated host/native manifest-projection checks.
Each new projection still strictly validates both complete original manifests
and their declared media aliases. Subsequent checks compare all original values
and exact types, including tuple contents, file metadata and declared aliases,
without repeatedly decoding the same JSON. This private derived state is not
part of the serialized plan or journal; creating a new projection revalidates
its inputs. Reinvoking validation cannot reseal a changed projection. Current
files, process identities, container mounts and deadlines are still independently
observed by their existing collectors. An isolated two-file prototype reduced
1,000 projection-hash checks from about 0.76–0.82 seconds to 0.077–0.084 seconds;
this does not establish live readiness or a larger observation budget.

### Retained service-plan input

`supplemental_recording_service_input.CasePlan` is the read-only input boundary
for the prospective recording service, not an installed entrypoint. It opens
only an existing private `plan.json`, requires the separately supplied reviewed
digest and the exact case path encoded by the canonical schema3 plan, and
retains the original file and no-follow ancestor descriptors. Schemas1/2 still
belong exclusively to the older idle-only service; they cannot enter this path.

Each recheck reads the complete original file again, compares its original
inode, ownership, mode, link count and metadata, checks every retained ancestor,
and verifies the original decoded plan object. Identical bytes in a replacement
file are insufficient. Unrelated case entries may grow as the separate journal
is written, but the plan cannot be replaced or adopted again. A failure closes
the input descriptors and permanently refuses that intake object; putting a
file back is not retry permission. Reads are bounded by the existing 64 KiB
plan limit and a two-second read window, with no deadline renewal within a read.
Independent supervision still covers blocked kernel I/O.

This boundary never creates a case, writes a receipt, acquires a process,
publishes readiness or sends an Engine/App/scanner command. It preserves the
plan's original clocks and deadlines rather than treating a read as renewed
consent. Expected-digest provenance, service UID, immutable source/runtime,
installed mounts, the original recovery session and fresh user readiness remain
separate requirements. The closed host source graph now includes this input
module (53 modules); prior 52-module image qualifications remain historical,
not qualifications of this expanded graph.

The 53-module immutable input bundle subsequently passed isolated local and
actual-HAOS checks. Both checked complete source/runtime inventories, exact
import origins, original retained plan bytes/object/deadlines, and refusal of
closed or wrong-digest inputs. On HAOS the input checks took about 14 ms. Only
the verifier and a synthetic private plan were mounted read-only, with no
Engine socket, App data, recordings or credentials. The normal Mimic App was
not restarted or reconfigured. This qualifies the packaged read-only input
boundary on that host, not an installed recording service or real plan authority.

#### Passive startup observation target

Direct execution of `supplemental_recording_service_input.py` is now a **read-only
probe**, not the recording service. It accepts only the existing case path and
independently supplied plan digest. The sealed-image invocation requires isolated,
no-bytecode Python, root UID/GID, working directory `/`, and the fixed helper
module path. It adds only that fixed helper directory for its private imports;
an arbitrary checkout, working directory or environment cannot select the code.
These guards do not replace independent image/source/runtime qualification.

The probe retains the original `CasePlan` and `ClockWitness`, repeatedly checks
the same bytes/object/time domain, and exits at the earlier of the plan's
original readiness deadline or 30 seconds after its original clock sample.
Starting later cannot renew this interval. A separate 301-poll ceiling also
limits the loop. It creates no journal, notice, Engine connection, listener,
service assembly or output file. It never starts audio or contacts an App or
scanner. Expiry returns exit 75, **not success or readiness**; refusal does not
permit another initialization attempt. External supervision is still required
for blocked kernel I/O, and the image's default command still refuses installed
execution.

This gives a trusted observer a real command/process to inspect without enabling
the recording service. The observer must independently retain the original
process/pidfd, verify the actual command/configuration/confinement and source/
runtime, and observe its exit. The process staying alive, its exit code or a
synthetic plan does not qualify an installed service or authorize restoration.
Docker and native host time domains must be checked explicitly; never relabel a
host clock sample to match a container or infer equivalence from nearby times.

An isolated HAOS combined attempt exposed why the original owner must remain
alive: its preparation container had exited before the helper and independent
observer started. The observer then correctly refused the plan's foreign clock
domain before the combined source/runtime check. A subsequently matching numeric
namespace inode is not evidence that the original namespace survived. That case
was preserved without retry; the original probe exited naturally with no App or
recording operation.

For **passive probes only**, the explicit `--zero-offset-probe` flag allows the
target to retain its own local `ClockWitness` and check its actual zero offsets
before and after each observation. It never changes a namespace ID in a clock
sample, validates the foreign plan's domain, or publishes readiness. Both the
original numeric lease cap and an additional local 30-second cap apply; expiry
still returns75. The ordinary invocation retains its strict same-domain rule.
The flag is not available to the recording service or any App command.

The independent `HelperQualification` accepts this seven-argument passive
command only when its continuing original-plan owner explicitly supplies a live
`ZeroDomain` for that exact helper process. It retains the object/digest, checks
the original plan clock, exact helper identity and both zero-offset domains on
every guard, and never closes caller-owned handles. Missing, serialized,
substituted, changed or expired evidence refuses before it can authorize anything.
No timeout or domain mismatch selects this path automatically. The original
two-second qualification limit remains unchanged. Separate containers need not
share time namespaces; Linux documents their offsets and when those offsets
become immutable in [time_namespaces(7)](https://man7.org/linux/man-pages/man7/time_namespaces.7.html).

This is an opt-in helper-observation harness, **not installed service startup**.
A real launcher still needs continuing plan/clock ownership, independent
supervision and a qualified service entrypoint. App plan fields in isolated
helper tests may be explicitly synthetic; they cannot authorize a live handoff.

#### Clock-free declaration groundwork

The eventual service cannot safely inherit a clock sample from a preparation
container that has already exited. Its original clock must belong to the same
continuing process that will own the service. This creates a startup dependency:
an independently reviewed command cannot already contain the final plan digest
before that process has captured its own original clock.

`scripts/supplemental_recording_service_template.py` adds a **pure, offline
declaration codec**, not a solution that activates the service. Its separate
versioned format pins every schema3 non-clock field, including boot identity,
case, source/runtime/image pins, generations, protected inventories and recording
contract. It also pins integer readiness and stop budgets within the existing
limits. Recovery remains fixed to the existing total budget; no recovery-duration
option is added. Old absolute clocks or deadlines cannot enter this format.

The codec can preview exact final schema3 bytes for a supplied original clock
and check a proposed final plan against those bytes. The complete existing plan
validator is reused: a private fixed clock sentinel is used only to validate
non-clock declarations and then discarded. It is never published as a clock
observation or retained in the template. Actual previews use the supplied clock
unchanged, and checking against the original clock refuses a later sample,
another namespace, altered protected fields or extended deadlines. Parsing
rejects duplicate keys, noncanonical bytes, unknown fields, invalid budgets and
an incorrect independently supplied digest. Errors contain no private values.

These operations read no clock or file and make no Engine request. They do not
retain a `ClockWitness`, establish clock provenance, publish a one-use offer,
authenticate acceptance, write a plan or journal, claim readiness, or authorize
recording. A caller can use a pure preview for review; it cannot use it to reset
an existing service lease. The prospective one-use offer/accept lifecycle,
original-owner custody, bounded private publication and independent supervision
still need implementation and qualification before installation.

This module is deliberately outside the currently qualified 54-module helper
image/command allowlist. Existing image checks do not certify it, and neither
the default helper command nor the passive startup probe invokes it.

#### Original-clock one-use offer model

`scripts/supplemental_recording_service_offer.py` adds an **uninstalled,
in-memory lifecycle model** for that declaration. A caller supplies an exact
template, its independently authenticated digest and its already retained
original `ClockWitness`. The model borrows that witness; it cannot construct,
replace or close it. The same process/thread and original objects must survive
every check. A fresh read of the original witness brackets checks of the exact
template, every final plan field and the nonextendable deadline.

The wait expires at the earlier of fifteen seconds after the original clock
sample or the plan's original readiness deadline. Constructing the model later
does not start a fresh fifteen-second budget. `inspect()` returns the same
proposal before acceptance without renewing clocks or publishing anything.
`accept()` consumes one independently supplied exact final-plan digest. Invalid
input, expiry, contention, lost clock evidence, changed objects or interruption
leave the model failed; no replacement digest or second attempt is accepted.
Changes during either acceptance clock read are checked again before return.
Closing the model leaves the original witness with its caller for subsequent
service custody and eventual cleanup.

This is **structural acceptance only**, not evidence of independent review,
readiness, recording permission or a durable acknowledgment. The caller still
must establish digest provenance. No file, Engine request, publication, journal,
App or recording operation is performed. A newly constructed model cannot
grant permission to retry a failed case. Bounded private publication/intake,
restart/lost-acknowledgment protection, independent supervision and integration
into the continuing service remain separate gates. Both new startup modules
remain outside the qualified helper image and its command allowlist.

#### Exclusive private plan publication

The uninstalled `supplemental_recording_service_publish.Publisher` connects an
original, still-unaccepted offer to the existing retained `CasePlan` reader.
The caller must independently create and authenticate the empty private case
directory. Publication walks every ancestor without following links and uses an
exclusive nonblocking directory lock. An existing entry, an unexpected directory
mode/owner, a replaced ancestor or lock contention refuses the attempt.

It first creates a single-link, mode0600 `startup-claim.json` with exclusive
creation, writes and verifies the exact claim, and synchronizes both file and
directory. Only then may it exclusively create and synchronize `plan.json`.
The original offer, clock, exact plan, directory identities and file metadata
are checked throughout. The operation has its own two-second bound inside the
offer's unchanged original limit. Independent outer supervision remains needed
for blocked kernel I/O. Success returns retained read-only plan custody; it
does not accept the proposal, prepare a journal or start a service.

Both files remain as evidence. Partial writes, file/directory synchronization
failures, missing close acknowledgments, expiry and interruptions do not trigger
cleanup, overwrite, repair or another publication attempt. A close whose outcome
is uncertain is not retried against a potentially reused descriptor. A failed
publication closes only the offer model, leaving its original clock with the
caller. Existing files also prevent a newly constructed publisher from replacing
the case. Neither a claim file nor returned plan custody is independent approval.
The external acceptance channel and continuing installed entrypoint still need
implementation and qualification; this module is outside the qualified helper
image and its command allowlist.

#### Original-owner startup acceptance intake

`supplemental_recording_service_acceptance` adds a closed canonical message
format containing the separately reviewed template and final-plan digests, plus
a bounded reader over the **original successful publisher**. Each publisher can
lend its original retained `CasePlan` and offer to only one acceptance reader.
Constructing another reader, changing the plan/owner objects or reopening a
previous case cannot replace that custody. The external launcher is still
responsible for independent review: neither message bytes nor a private file
authenticate that review by themselves.

The reader accepts only a mode0600, caller-owned, single-link regular
`startup-acceptance.json` of at most1024 bytes. It checks the original plan and
clock before and after the read, pins file identity/content, and rejects unknown,
duplicate or noncanonical fields and any wrong digest. Missing input or a real
cooperating directory-lock contention is pending within the original offer's
deadline, never a refreshed wait. Any pending-publication residue or unexpected
case entry refuses. Once a submission exists, malformed input, replacement,
expiry, interruption or an uncertain acknowledgment consumes the attempt and
withholds a successful return. The file remains untouched. An uncertain close
is not retried, and failure does not close the caller's original clock or plan.

Successful consumption calls the same original offer's one-use structural
acceptance. It performs no write, disk synchronization, journal transition,
Engine request, App command or recording action. There is no durable success
acknowledgment or restart/resume path. Qualification of the separate submission writer,
independent review/supervision and installed continuing-service assembly remain
required. The intake module remains outside the qualified helper image and
command allowlist; its private-file and real-local-clock tests are not installed
platform qualification.

`supplemental_recording_service_submit.Submission` provides that separate writer
as an uninstalled library. The external caller supplies a retained read-only
`CasePlan`, the independently authenticated template digest and the independently
reviewed final-plan digest. The writer checks the final pin against those exact
retained bytes, but cannot authenticate the caller's review or infer permission
from the claim file. It does not capture or relabel clocks: the continuing owner
enforces its own original domain and expiry when consuming the message.

Submission takes the cooperating directory lock exclusively, writes a new
mode0600 pending file, synchronizes and verifies its complete bytes, and links
it to the fixed acceptance filename without replacing anything. Only its own
verified temporary link is removed; the destination and every uncertain payload
remain preserved. Directory synchronization and final original-plan/file checks
precede a successful delivery return, under a separate two-second bound. An
initial, known directory-lock contention may wait inside that **same original
attempt and two-second bound**, before any file is created. Only the private
directory's actual pre-entry `DirectoryBusy` is eligible; other I/O errors and
anything after acquisition are not. A defensive poll ceiling also bounds this
pre-write wait. Expiry, interruption or refusal consumes the attempt and forbids
resubmission. No write, publication or acknowledgment is retried. The reader stays
pending only while the writer actually holds the lock, and refuses leftover
pending state after the lock is released.

A lost sender acknowledgment remains **uncertain delivery**. If the complete
message was already published, the original reader may still accept it once;
the sender cannot assume it was rejected or submit again. Delivery and acceptance
are distinct from service readiness and recording authorization. Temporary-file
tests cover both sides of this boundary, and a separate real local Python
process has submitted into the same original owner's retained clock/plan chain.
That test uses synthetic App/contract data and a temporary path alias, not an
installed helper or real App handoff. The writer also remains outside the
qualified helper image and its command allowlist.

#### Retained clock-free declaration input

`supplemental_recording_service_declaration.Declaration` reads a separately
prepared `template.json` from the fixed private sibling directory
`/mnt/data/sdsctl-recording-startup-<case>`. It must not be placed in the empty
writable case reserved for exclusive plan publication. The proposed path uses
the existing read-only data view, not a new writable mount; an installed command
and its exact source/configuration still require independent qualification.

The caller supplies an independently authenticated template digest. Intake opens
and retains every original ancestor and the file without following links. The
directory must be mode0700 and contain only the mode0600, single-link, bounded
regular template file, with the original effective uid/gid. Exact metadata,
pathname identity, canonical bytes, decoded object and case-to-directory binding
are checked around each two-second-bounded read. Changed input, a replacement
file/ancestor, an extra entry, uncertain I/O or expiry fails without adopting a
replacement. No file or directory is created, modified, synchronized or removed.

This reader owns only its original read-only descriptors. It releases them on
failure, attempts every original close once even after a close error, and never
retries an uncertain close. A foreign thread cannot close the original owner's
handles. Neither loading nor rechecking captures a clock or manufactures a
deadline, publishes an offer, contacts Engine, prepares a journal or grants
recording permission. It remains an uninstalled library outside the qualified
helper graph and command allowlist.

#### Finite original-owner startup assembly

The uninstalled `supplemental_recording_service_startup.Startup` connects the
retained declaration to the tested offer/publication/acceptance components.
Construction claims that original declaration in memory without capturing a
clock or writing. Its one explicit `prepare()` attempt captures the continuing
owner's actual original clock, retains its `ClockWitness`, and publishes the
original plan. `poll()` waits only within the offer's unchanged original bound;
it returns the same retained `CasePlan` only after separate acceptance.

Missing input does not refresh the offer. Invalid input, changes in the original
declaration, repeated preparation, premature use, substituted objects, lost
acceptance returns or interruption fail without repair/replay. Published files
remain preserved. A second startup object cannot claim the same declaration,
and existing claim/plan files prevent re-publication by a newly constructed
reader. These safeguards do not grant permission to restart a process or reuse
a case, including after a failure before publication.

The startup owner owns its clock/models/plan handles and borrows the original
declaration. After independently qualified assembly, an `IdleService` can borrow
the exact accepted plan and original clock; the startup owner must stay alive
until that service has released them. Cleanup attempts original callbacks once,
closes the original clock last, and never closes the borrowed declaration.
The retained `CasePlan` reader also now attempts all original descriptor closes
once when an earlier close is uncertain, rather than leaking the remaining
handles or retrying a possibly reused descriptor. An original interruption is
not hidden by an ordinary cleanup error.

Real local subprocess tests cover independent acceptance and rejection while
an observer holds the original process pidfd, clock and namespace evidence.
The observer refuses to continue after that owner exits. These tests use
temporary path aliases and explicitly synthetic Docker cgroup/App metadata;
they do not certify an installed container, command or mount configuration.

This assembly performs no Engine request, journal preparation, service/native
launch, scanner access or recording operation. Accepted input is not readiness
or operator approval. The full expanded source/runtime graph, finite installed
command, supervision and actual host qualification still gate installation;
none of the old helper commands implicitly selects this startup protocol.

The same module also exposes a distinct, action-free `--startup-probe` command
for later isolated qualification. Direct execution requires isolated Python,
bytecode disabled, root uid/gid, working directory `/` and the fixed sealed
helper pathname. The arguments identify the separate declaration directory and
its independently reviewed template digest, **not** a precomputed final plan.
It publishes one claim/plan, may consume one independent acceptance, and keeps
the original handles alive only within the original offer's at-most15-second
bound. Acceptance does not restart that bound. A defensive iteration ceiling
and unchanged inner I/O bounds apply; blocked kernel I/O still needs independent
outer supervision. Original clock closure is last; all case files are retained.

This is not a read-only file probe: it explicitly creates private startup
evidence in a fresh provisioned case. It never starts the service/native worker,
contacts Engine, creates a journal or operator notice, or controls the scanner.
Exit75 does not prove acceptance, readiness, recording, or restoration. Existing
commands and `HelperQualification` do not allow/select this entrypoint. Running
it in a synthetic local container is not installed App qualification.

The read-only `host_source.Layout` now offers an explicit `startup=True` source
inventory profile for the expanded **62-module** graph. The default still
requires exactly the original54 modules; it does not detect or adopt a different
graph from observed files. The startup profile uses a separate versioned hash
kind, so an original/native fingerprint cannot certify it. Both profiles retain
the full product package, exact flat helper inventory, two complete reads and
all original metadata, symlink, size and time checks. The eight startup modules
and their complete static import closure are covered by separate tests.

This profile does not change `HelperQualification`, its command allowlist,
clock policy or mount configuration, and an old image qualification does not
cover it. The caller must explicitly select and independently reconstruct the
new source digest, then separately qualify runtime, original process custody
and the actual startup command before it can participate in installed startup.

#### Observer-side clock comparison for a service-owned plan

The uninstalled `supplemental_recording_service_clock_link.ObserverClock`
models the reverse ownership direction needed by this startup protocol. The
continuing service owns the plan's original clock; an independent observer
retains its **own** original `ClockWitness`, the actual target process identity,
and a live `ZeroDomain` retaining both namespace descriptors and the target's
original pidfd. Equal numeric timestamps alone are never sufficient.

Each bounded read brackets two original observer-clock observations with live
kernel-domain checks. The exact original plan, clock, target and domain objects
and evidence digest are rechecked, including after the final I/O boundary. The
comparison requires the original boot, the separately verified namespace
identities, forward time, overlapping suspend-offset intervals and the plan's
unchanged readiness deadline. Zero namespace offsets do not mean that BOOTTIME
equals MONOTONIC: both still include the same accumulated suspend difference.
Linux describes those separate offsets in
[time_namespaces(7)](https://man7.org/linux/man-pages/man7/time_namespaces.7.html).

The returned sample remains explicitly in the **observer's** namespace. No
sample is relabeled, no namespace is entered, no clock is adjusted, and the
service's ordinary strict `Plan.check_clock` rule remains unchanged. Refusal is
sticky; closing this comparison model leaves both borrowed witnesses with their
caller. A real-local-child test exercises retained kernel handles on one host;
synthetic different-domain tests do not certify distinct container namespaces.

This comparison does not authenticate who produced a serialized service sample.
Trusted source/runtime/command/template provenance, original process custody,
independent supervision and actual distinct-container qualification remain
separate requirements. It is not selected by the existing helper qualifier,
command allowlist or installed service. The older observer-owned-clock probe
qualification cannot stand in for this new service-owned-clock protocol.

### Recovery routes inside the pinned helper

Two fresh local cases at `df78fd4` passed the recording/recovery assembly inside
the immutable helper image, rather than in the host virtual environment. Both
used network-isolated synthetic scanner/audio peers, actual native recording
processes and files, and the original recovery session. The helper ran under
independent systemd supervision; an outside observer retained the original
helper and container-init pidfds through exit. Neither case changed Home
Assistant, a Pi, the physical scanner or an existing user recording.

The lost-completion case abandoned its original completion transport after an
actual active checkpoint. Original process-exit evidence and two complete
preserved-file reads preceded the original-session restoration continuation.
The simulated normal App became healthy and idle, but the recording verdict
remained **unconfirmed**, without an artifact or acknowledgment.

The successful-completion case checked positive file growth, the original
finalization acknowledgment, original worker/init exits, complete settled-file
reads and exact artifact hashes. Its synthetic WAV contained 9.4 seconds,
470 packets and 75,200 samples. The same recovery session then confirmed the
simulated normal App and retained the verified artifact. Final source and
interpreter rechecks passed in both cases. This is file/process verification,
not an audible-quality check.

App/Core/Supervisor metadata and the normal-start command in these cases were
explicit fixtures. These results do **not** qualify an installed service
entrypoint, installed mounts/platform, real Supervisor latency or the installed
one-CPU helper. These earlier successful cases used a four-CPU helper; the finalized case
had no added synthetic status latency. Other cases still refused around the
unchanged two-second freshness bound. Those refusals are not retry permission
or evidence of a reliable installed timing margin.

After the pure plan/projection comparison changes at `f788b6d`, two fresh local
cases passed with a one-CPU helper, a two-CPU native container and 430 ms of
added synthetic status-query latency. The lost-completion route preserved its
unconfirmed recording; the finalized route verified a 9.42-second synthetic WAV
with 471 packets and 75,360 samples. Both retained the original outside process
handles through helper/native exit, rechecked source/runtime and continued the
same recovery session through exactly one simulated normal-App restoration.
The finalized case's Ready age was about 1.99 seconds and pre-begin checks took
1.91 seconds: passing, but still close to the unchanged two-second limit. This
does not establish dependable installed-platform timing or audible quality.

An intervening finalized-case observer refused an outdated four-CPU assertion
and closed its original process handles early. Its helper subsequently completed
under independent supervision, but that case is not a full custody pass. The
corrected observer ran only in a new case; the original evidence was retained.

A subsequent helper-loss injection was refused by the operating system before
signal delivery could be confirmed. Both disposable containers later stopped
under their original bounds, but the outside observer had already closed its
process handles. That attempt is retained as incomplete, not a host-loss or
through-exit custody pass. No alternative signal route or host-security change
was attempted. Every consumed case and driver remains closed without retry.

Integrated offline tests exercise the actual journal, policy, dispatch tracking
and loop across candidate stop, normal start and health verification, with
explicitly synthetic host, cached-state, Engine and init-exit evidence. They
also cover changed files on either side of candidate shutdown, unavailable
inspection, lost stop replies, foreign context, route replacement and stale
combined observations. These tests are not installed-service, native process,
scanner or audible-playback qualification; independent service supervision and
installed-platform checks are still required.

At `f2b067b`, the expanded affected-suite regression passed 805 tests, including
19 new continuation cases. A fresh helper image built offline from that immutable
source then passed a separately supervised, read-only/network-none source,
import-origin and full-interpreter check. Its 52 helper modules matched the
independently reconstructed Git-archive fingerprint. The unchanged interpreter
matched the previously independently reconstructed immutable-base fingerprint.
That check had only a read-only verifier mount: no Engine socket, App data,
profile, recordings, scanner connection or recovery-service entrypoint. It
therefore verifies packaged code and runtime, not containerized App restoration.

### Local independent supervision and host-loss qualification

Fresh scanner-free cases at `2d07cab` exercised the original native recording
and custody path inside a separately launched local systemd service. The
interactive launch returned while that service continued. Its original
`Restart=no`, runtime limit and bounded stop escalation were independently
inspected; a separate observer retained kernel process-exit handles. These
cases used only synthetic loopback scanner/audio peers in a network-none
container. No Home Assistant instance, Pi or physical scanner participated.

The normal-completion case verified positive recording growth, the original
completion/worker-exit evidence, and fresh finalized-file/host reads after the
original container's finite lease expired. The host-loss case deliberately
blocked the host after its actual recording-start return and ignored SIGTERM.
Systemd killed that host at its independent limit. All original recording
workers then exited within their unchanged native deadline, and the original
container exited on its own lease. No candidate stop/restart command or
replacement authority was used to obtain those exits.

The lost host never published a recording-completion acknowledgment. A separate
read-only postmortem used the existing naming-scope and retained-file collectors
to confirm preservation against the original manifest. It returned `retained`
with no verified artifact, left the three original ledger events unchanged, and
did not invent a completion or restoration receipt. An Engine return code of
zero was **not** promoted into recording success. Docker's historical exec PID
may remain positive after exit; the existing exact-command verifier accepts
that metadata, while independently retained pidfds provide process-exit proof.

Earlier qualification failures remain preserved: a system-service Git trust
check refused before container creation; another case refused before recording
because the observation exceeded the original two-second freshness limit; and
an observer incorrectly required an exited exec's historical PID to be zero.
Corrections were confined to the private harness and fresh cases. In particular,
the observer's heavy polling was reduced, not the product's timing requirements.
No consumed case was restarted and no product guard was relaxed.

A fresh immutable image of this commit also passed separate source/import and
full interpreter qualification under bounded local systemd services. Expected
source fingerprints were reconstructed from the Git archive; the interpreter
fingerprint was reconstructed from a never-started container export before the
image was executed. All 52 host and 29 native modules loaded from their expected
read-only paths with network/process actions forbidden during import. This
qualifies those image contents and local confinement checks, not installed
Home Assistant mounts, Supervisor identity, credentials, or App restoration.

The full recording-capable service assembly, failure-preservation integration,
and installed-platform qualification remain required before another live
acceptance window. The local host driver still used explicitly synthetic
App/Core/CLI/Supervisor metadata and path aliases; its interpreter was not the
new helper image. Those separate results must not be combined into a claim that
an installed, independently supervised recovery service has already passed.

The joined native-relay test also exercises the real isolated operator and
recorder: localhost RTP becomes a verified 1280-sample WAV, the actual private
native messages cross the framed relay, and the host checks its original file
baseline, ledger and retained process handles. Only the fixture's Engine,
container/root-namespace metadata and media alias mapping are synthetic. Losing
the completion leaves the ledger unconfirmed; losing just the exit report or
returning a failed final Engine status cannot qualify exit even when the actual
recording and all worker exits are genuine. Receipt hashes bind sender identity
and receipt time as well as raw message bytes. This does not qualify installed
namespaces, image bytes or the eventual host execution environment.

The prospective host-side mechanism has a separate source inventory in
`scripts/supplemental_recording_host_source.py`: initially 41 fixed private modules plus
the complete product package. The host return verifier imports recording types
from that package; copying the older 14-file idle helper is insufficient. Static
import-closure and isolated local import tests check the reviewed helper graph.
The collector itself uses only the standard library and trusted filesystem
helpers, hashes observations twice, includes package assets/bytecode, and refuses
extra helper files/directories, links, unsafe modes or changed observations.
Neither native-source nor legacy-helper digests substitute for its distinct
schema. This is not the future host service entrypoint, interpreter/stdlib or
third-party dependency attestation, and it does not start or install anything.

### Separate interpreter and startup-environment evidence

`scripts/supplemental_recording_runtime.py` is a separate read-only collector for
the prospective Linux/amd64 CPython3.14 image. It does not run the candidate
interpreter, `ldd`, a package manager, or any observed source. It inventories the
complete `/usr/local`, `/usr/lib`, `/usr/lib64`, TLS configuration/certificate
trees, loader cache/configuration, and the fixed `/lib` aliases. Files, bytecode,
assets, empty directories, modes, owners and link targets are included. Link
resolution is lexical within the observed image namespace, never through host
symlinks; missing, cyclic or out-of-inventory targets refuse qualification.

Two complete observations must agree within one bounded collection deadline.
Unsafe permissions, special files, hard-linked files, changed descriptors,
unbounded trees and unknown expected pins fail with a fixed sanitized error.
Every retained ancestor outside the selected image root is checked for the same
device, inode, type, permissions and ownership. Unrelated sibling activity in
those external ancestors (for example another image below a Docker directory)
does not invalidate the inventory through directory timestamps or link counts.
The selected root itself, including when it is `/`, and every directory and
file below it retain the full before/after metadata checks. Replacements and
symlink swaps still fail; no runtime content is excluded or deadline renewed.
Loader-preload, virtual-environment and zip overrides, Python startup hooks and
`._pth` path overrides are refused. A separate closed image/container `Config.Env`
check rejects duplicate or extra keys, including dynamic-loader injection. It
does not silently filter or rewrite the environment. The initial offline profile
does not admit Supervisor credentials or claim to qualify a general HA App.

Two explicitly selected, pure comparisons now cover a separate prospective
Supervisor environment profile. `supervised_environment()` requires exactly
the original five image keys plus `TZ`, `SUPERVISOR_TOKEN` and `HASSIO_TOKEN`.
It compares all image values with an independently reconstructed image pin and
requires the separately pinned timezone name. Credentials are bounded opaque
values: their exact bytes affect the fingerprint, including rotation, but are
not returned, logged or saved by the checker. Credential shape does not prove
authentication. The original image-only function still rejects these additions.

`supervised_process_environment()` separately compares bounded, NUL-delimited
startup environment bytes with the original Config.Env fingerprint. Only the
explicit derived `HOME=/root`, pinned container hostname, and existing fixed
Engine-exec PATH override are accepted. The idle/PID-1 and fixed-exec profiles
cannot be interchanged. Duplicate keys, unknown additions, loader hooks, changed
credentials, malformed bytes and mismatched pins refuse with a fixed error.
It does not read `os.environ`, collect a process, or execute anything. The host
still must collect original pidfd-bound startup bytes and check process/container
identity around that read. Neither function is selected by the installed helper
or changes any App, credential, command or default admission policy.

The corresponding `collect_supervised_process_environment()` performs two
matching bounded `/proc/<original-pid>/environ` reads through a no-follow file
descriptor, bracketed by the already-bound original `ProcessWitness`. It checks
the live pidfd, its descriptor identity, exact original start/cgroup identity,
and stable proc-file identity before returning only the environment fingerprint,
original process identity and observation start time. Its caller-supplied
absolute monotonic deadline has at most one second remaining and is never
refreshed. It neither discovers/rebinds a PID nor closes the caller's witness.
Unknown, replaced, exited or changed evidence remains unconfirmed. Original
container/image/namespace/command qualification and independently supervised
outer timeout still belong to the host adapter; this is not an installed service.

The prospective host helper has a **separate credential-free profile**.
`helper_environment()` accepts only the five original image keys and the
independently pinned `TZ`; it rejects Supervisor/Hassio tokens rather than
filtering them out. `helper_process_environment()` additionally requires exactly
`HOME=/root` and the pinned hostname in bounded startup bytes. It preserves the
image PATH and offers no Engine-exec override. Its configuration and process
digests are separately tagged; neither an image-only pin nor an App environment
pin qualifies this helper profile.

`collect_helper_process_environment()` uses the same bounded two-read/original
pidfd mechanism with that explicit helper comparator. It cannot discover a new
helper after losing its original handle, renew a deadline, or read `os.environ`
as proof of startup. Local tests use a harmless owned child and actual proc/pidfd
reads, with a synthetic container-cgroup identity. These functions do not yet
qualify a complete helper container, source mounts, confinement, launcher or
independent supervision. Existing schema3 fixtures with image-only helper
environment pins must not be described as passing this new profile.

Separate fresh local and HAOS-host containers have now passed complete source,
import and runtime checks plus actual original-pidfd startup-environment reads
for themselves and their harmless owned child. They rejected App credentials,
image-only pins, an exec PATH override and a subsequent read of the exited
original child. Both were network-disabled and root-read-only with only a
read-only verifier mount, no Engine socket, App data, media or credentials.
These bounded checks do not qualify an installed service or combined host timing.

`HelperQualification` in `scripts/supplemental_recording_host_launch.py` joins
the separate helper pins to an independently captured original process and
Engine incarnation. The trusted launcher supplies the exact command and a
separately reconstructed fingerprint of the complete container configuration;
the collector never learns expected values from the object it is checking.
It checks the original process command, credential-free startup bytes and
effective Linux privilege state before and after full helper/product source
and timezone-inclusive runtime reads.
Source is rechecked after runtime inspection. A successful call returns no
report or action authority, and subsequent calls cannot reuse its observations.

The Engine merged-root directory must also match the original process's actual
`/proc/PID/root`. The collector keeps both root descriptors and the original
mount-namespace descriptor open throughout hashing, and rechecks their identities
and fixed paths between source/runtime reads and before releasing them. A changed
root or mount namespace, substituted path, lost process, partial open or
interrupted hash refuses qualification. Cleanup closes only collector-owned
descriptors, never the caller's original pidfd. This binds the observed root to
that process; it does not by itself authenticate the outer launcher's proc view.

Within that bracket, the collector also retains the helper's actual proc-directory
descriptor. Its kernel `fdinfo` mount ID and filesystem device must match the
helper's bounded `mountinfo` table, read through the trusted outer process view.
Process, self-link and boot-ID paths cannot be overmounted; an optional read-only
`/proc/sys` remount must retain the original proc filesystem and `/sys` root.
Unrelated standard masks, binfmt mounts and namespace-handle mounts do not shadow
those paths. PID/cgroup/user namespaces must match the outer view, and reading
the original helper's identity through its proc mount must agree with the retained
original process. The complete mount table stays unchanged across source/runtime
hashing. This follows the kernel's [mountinfo and fdinfo formats](https://docs.kernel.org/filesystems/proc.html#proc-pid-mountinfo-information-about-mounts);
it does not authenticate the trusted outer launcher's own proc/PID view, prove
every non-proc mount safe or replace independent supervision.

The prospective helper profile requires a read-only root, no network, host PID
and cgroup views, private IPC, root user, no automatic restart/removal, one CPU,
512 MiB memory, 1 GiB memory-plus-swap limit and 64 PIDs. All capabilities are
dropped except the explicitly pinned `DAC_READ_SEARCH` and `SYS_PTRACE` needed
for host reads; Engine inspection must report their canonical `CAP_` names,
not the shorter CLI spellings. `no-new-privileges` is mandatory. HAOS's `label=disable` setting
is admitted only as an explicit part of the independent configuration pin.
Only five bind mounts are accepted: the Engine socket read-only, `/mnt/data`
read-only, the exact private case writable, and the two host UDP tables
read-only. No code/loader/timezone shadow mounts, devices, inherited volumes,
extra writable paths or credentials are accepted. **A read-only Engine socket
mount does not restrict Engine API authority.** The helper remains trusted
administrative code, never a browser-facing API.

The `/mnt/data` bind must report `rslave`, not `rprivate`: it contains HAOS's
Docker root `/mnt/data/docker`. Moby treats mounts containing the daemon root
[specially to avoid retaining private references to its submounts](https://github.com/moby/moby/blob/v28.3.3/daemon/volumes_linux.go).
The request may omit propagation (Engine's daemon-root default) or explicitly
request only `BindOptions: {Propagation: rslave}`; either complete configuration
must be independently pinned before startup. All four other binds remain exactly
`rprivate`; shared propagation, additional bind options and changed requests
refuse. This is a declared top-level read-only profile, not a guarantee that
future propagated submounts are read-only. Docker's [bind-mount documentation](https://docs.docker.com/engine/storage/bind-mounts/#configure-bind-propagation)
describes the incoming-only propagation and separate recursive read-only rules.
The original root/proc descriptors and unchanged complete mount table still
bracket source/runtime reads. A fresh helper-only HAOS case confirmed the live
zero-offset namespace proof, but correctly refused the formerly all-`rprivate`
policy at the data mount before source qualification. That failed case is
preserved and is not a passing combined-read or installed-service result.

The helper configuration fingerprint uses a separate version2 domain. It sorts
only the already validated observed `Mounts` list by unique destination; every
mount field, the requested `HostConfig.Mounts` order, and all other configuration
fields remain pinned. Docker may reorder observed mounts at startup. On this
cgroup-v2 profile, the running helper must explicitly report
`OomKillDisable: null`, consistent with Docker [discarding the unsupported option
on cgroup v2](https://docs.docker.com/engine/containers/runmetrics/#running-docker-on-cgroup-v2).
A launcher must independently seal that expected startup value before start;
the collector never converts an observed `false` or learns a replacement pin.
Missing, false or true runtime values refuse, even with a matching supplied hash.
The original version1 fingerprint cannot substitute for version2. A separate
fresh passive case exposed these startup transitions and was preserved as a
refusal, not counted as completed helper qualification.

`collect_helper_kernel()` retains the original pidfd and three bounded proc
file descriptors while reading two security snapshots. It requires exact root
UID/GID values, an unnested PID view, identity UID/GID maps, no tracer,
`NoNewPrivs=1`, only `DAC_READ_SEARCH`/`SYS_PTRACE` in the effective, permitted
and bounding capability sets, empty ambient/inheritable capabilities, and
seccomp filter mode with a bounded positive filter count. The count must remain
unchanged, but this is **not proof of the filter policy's contents**. Process
identity and proc-file identities bracket both reads; changing/exited processes,
replaced files or late reads refuse. Only collector-owned descriptors are closed;
the caller keeps its original pidfd. Nonsecurity counters and running/sleeping
transitions are allowed without masking required security fields. The collector
has one absolute one-second bound and cannot interrupt blocked kernel I/O itself.

The whole read keeps the original two-second maximum and readiness deadline.
Unexpected settings, changed bytes/identities, lost handles or late reads are
sticky failures. Tests use actual source/runtime/proc-environment reads and an
owned pidfd with explicitly synthetic Engine, kernel-profile, command-line and
host-path/root/namespace fixtures. Separate kernel-collector tests use actual descriptors and
an original owned pidfd with synthetic accepted proc bytes/cgroup routing; an
actual unprivileged child is correctly refused. These checks do not establish
namespace provenance, seccomp policy contents, independent supervision, or a
continuing helper clock-domain witness. Those remain separate gates; no installed
entrypoint or existing live service selects this collector. Its accepted command shape does
not make any library module a usable service or authorize its execution.

Separate frozen-image checks on the local Docker host and HAOS read actual
kernel privilege snapshots for the verifier and its own child. Both matched the
closed profile, then refused the original child after its confirmed exit while
retaining the caller's pidfd. Complete source, import-origin, environment and
one-/two-worker runtime checks also passed in those isolated cases. They mounted
only the verifier read-only: no Engine socket, App data, recordings or
credentials. Those checks validate constituent collectors, **not** the complete
two-second `HelperQualification` sequence or an installed service.

The explicit `Layout.observe_supervised(timezone)` / `verify_supervised()` runtime
profile also inventories the complete `/usr/share/zoneinfo` tree and the
`/etc/localtime` link, and requires `/etc/timezone` to be absent in this qualified
image layout. The selected timezone and localtime must resolve lexically to
inventoried files inside that tree. Unused zones, aliases and metadata are included,
not filtered out. This closes a dependency of the geographical `TZ` setting:
[glibc resolves relative geographical timezone names beneath its zoneinfo directory](https://sourceware.org/glibc/manual/2.42/html_node/TZ-Variable.html).
The original runtime profile/digest remains separate and unchanged; neither can
substitute for the other. Expected fingerprints still need independent image
reconstruction, and live mounts must not shadow any of these inputs.

The environment additions have local-only qualification: synthetic-credential
startup and fixed-PATH execs matched independently constructed fingerprints in
fresh root/read-only/network-disabled containers, including a real original
pidfd-bound startup read and refusal after that process exited. A new immutable
47-module host / 29-module native image was then reconstructed from the reviewed
source. Its complete timezone-inclusive runtime inventory matched an independent
never-started-container export: 6,576 entries, 5,536 files, 125,825,089 bytes.
Separate fresh container checks verified the complete helper/product source and
import origins without import-time network/process actions. These do not prove
installed Home Assistant qualification, recorded audio, recovery or restoration.

`CandidateQualification` in `scripts/supplemental_recording_host_launch.py` now
provides the read-only candidate side of the trusted launch qualifier. It retains
the original canonical schema3 plan, idle observer, init identity/pidfd, Engine
generation, image-environment pin, hostname, architecture and timezone. Each call
checks the actual container/image before and after fresh native/product source,
complete supervised runtime and original-init startup-environment reads. The
fixed idle command, root user, manual restart policy and exact protected data/media
mount mappings remain required. Mounts cannot shadow certificate/loader files,
timezone inputs or any executable tree. No candidate source is imported.

The complete call must finish inside its original two-second local bound and
the plan's original readiness deadline. Neither a late result nor failure can
refresh a deadline or replace a pin. A failed collector remains consumed; a
successful call returns no reusable report or authorization. Credentials are not
retained or printed. Original witness ownership stays with the caller. Independent
outer supervision is still required for blocked kernel or Engine I/O; a local
elapsed-time check cannot interrupt such a block.

Related local regression covers real file/proc reads and owned pidfds with
explicitly synthetic Engine, idle-claim and HAOS path fixtures. It checks source,
environment, identity, mount and claim drift, lost handles, and late observations.
This is not an installed host adapter qualification: complete host files/jobs/Core,
network/other-owner observations, helper provenance, actual cached native health,
independent recovery and a measured combined post-Ready timing window remain
separate gates. The installed idle-only helper does not select this class.

A bounded local one-CPU timing fixture measured the complete native/product plus
supervised-runtime input checks at 1.25–1.39 seconds per pair. This is not full
host/Engine/probe timing. In particular, repeating that pair three times in the
separate `start()` / `confirm_ready()` path cannot fit the unchanged two-second policy window on
that fixture. The individual component results must not be reported as a passing
assembled readiness path or used to enable a live handoff.

The passive probe's optional `Sample.prepare()` now separates its one-time fixed
Engine create/start/process binding from the one-time cached-state request. It
can let interpreter/import startup overlap fresh host qualification; it returns
no health, sends no cached request, and cannot renew its original deadline or
command. `read()` then uses that same attachment and original process handle,
rechecks the original actors, sends exactly one request and still requires the
fresh closed reply, actual probe exit and independent Engine exit status. The
existing immediate `read()` path retains its full checks. Failed or abandoned
preparation cannot be retried or counted as healthy. No installed launch path
selects this timing seam; combined sequencing and timing remain to be qualified.
The fixed native wrapper preloads its already source-qualified launch-plan reader
module before waiting for the request. It does not read any plan, profile, cache
or actor state during that preload. Both actual input reads and the cached-state
observation still occur inside the authenticated one-request sampling step.
Launch-plan validation imports the specification and its real policy validator,
but construction-only API/server/recording-assembly imports are deferred until
actual native construction. A separate isolated-interpreter test verifies that
passive validation does not load those services or start network/thread activity.
The native construction and lifecycle tests still exercise the same real classes;
no scanner, audio, recording or cleanup behavior is replaced by a lightweight stub.

The uninstalled `Launch.start_confirmed()` adds a combined, single-use path. It
requires the actual `CandidateQualification` bound to the same original plan,
idle observer and init witness. Pre-create/attach qualification remains in place.
Immediately after receiving Ready it prepares the passive probe, then uses
`CandidateQualification.during()` to bracket a fresh full host observation and
the probe's one current status read. Complete new native/product and supervised
runtime inventories are taken before and after those reads. Each first inventory
must match its independently supplied pin before the observation runs; no result
escapes until the second inventories, original startup-environment reads, metadata
and idle evidence agree. No previous successful hash is reused as current evidence.

This path includes probe preparation, all inventories, observation and publication
in the original two-second Ready-age check. It keeps the oldest contributing
observation time and does not reset the original clock, deadline or Ready receipt.
A failed preparation, changed file/environment, lost actor, late read or finish
request consumes the attempt without publishing readiness. Readiness still does
not authorize recording, send begin, start a browser listener or certify recovery.
The separate path remains available with its existing checks. Local tests cover
the real file/process brackets and synthetic host/Engine ordering independently;
assembled real-Engine timing and installed host integration remain separate gates.

Two isolated local trials have now joined actual Engine/Ready/probe traffic to
complete fresh file inventories and the original init environment, publishing
readiness at about 1.96 and 1.93 seconds. They used synthetic installed metadata,
idle claims and full host observations; the second used the actual bounded Unix
Engine reader. Both deliberately closed the attachment before recording began,
then separately verified original worker pidfd exits, Engine status and finite
init exit. The small timing margin is not an installed readiness pass. Read-only
timing on the existing Home Assistant host measured roughly 0.43 seconds for the
seven status commands alone, without the remaining protected-file/Engine work.
The original two-second bounds have not been enlarged to accommodate that cost.

`BootstrapHost` now supplies the full host-observation side of this join from
the same canonical schema3 plan, original media projection, idle observer and
retained init witness. It constructs the fixed Supervisor reader with the pinned
CLI image/generation, and the recording-aware `HostObserver` with the original
App seals, complete installed-version inventory, other scanner owners, Core
identity and required RTP network policy. Each read verifies all normal protected
files and the candidate static files plus its complete pristine recording root.
It cannot choose a different capture stage or adopt the current files as a new
baseline. Failed reads poison this collector without closing caller-owned handles.

This bootstrap-only reader does not execute a native health probe. It keeps
health/recording unknown, requires the normal App stopped and the original
candidate running, and brackets the full observation with original idle evidence
and process/clock checks. Changed protected pins and busy-job facts still reach
the policy; file uncertainty, changed incarnation, stale evidence or elapsed
time beyond two seconds return no usable sample. The oldest start timestamp is
preserved. Local tests run the full observer, real temporary recording inventories
and a retained owned pidfd with explicitly synthetic Supervisor/container/static
metadata and a declared path alias. Installed-source/runtime qualification,
assembled full-host timing, normal restoration, recording authorization and
independent host-service recovery remain separate gates. No installed service or
legacy plan selects this reader yet.

The complete bootstrap observer also passed an isolated real-Engine trial with
actual protected-file and recording inventories, at about 1.94 seconds from
the original Ready receipt. Installed metadata, idle claims and Supervisor
responses were explicitly synthetic. Adding simulated status-query latency then
correctly refused readiness at about 2.19 seconds, without recording permission.
The original two-second policy remains unchanged.

For the actual bound `BootstrapHost`, combined confirmation can now prepare only
its fixed read-only host I/O while the original thread hashes the complete
runtime. It joins that one complete result inside the qualification bracket,
before the separate native probe. Initial/final idle, process, namespace and
journal checks remain on the original owner thread. No native probe, App-control
command, journal write or recording action runs in the read worker. The oldest
observation start and original two-second join deadline cannot be renewed. A
changed context, worker failure, timeout or failed outer qualification consumes
the collector; late completion cannot make its result usable again.

Discarding a result does not cancel an already blocked kernel/Engine read or
prove process exit. The original worker handle is retained on failure, and the
independent outer host supervisor is still required for a stuck read. This is
not an installed background service. The ordinary synchronous collector and
separate launch/confirmation path retain their existing behavior. Local tests
cover original-thread ownership, cancellation, late completion, clock/context
changes, full inventories and the outer failure ordering; assembled timing and
installed service recovery remain separate qualification gates.

The expected runtime pin must be reconstructed independently from a reviewed
immutable image, not taken from the process being qualified. Local qualification
compares a no-extraction archive inventory from a never-started container with
actual read-only, network-disabled container observations. This proves only that
the selected runtime inputs matched in those tests. Product/helper source,
image provenance, unshadowed mounts, actual process environment, command,
namespace and process identity, installed host integration and independent
termination/recovery remain separate gates. This collector is not yet selected
by the installed host service or an existing host-plan schema.

## Offline finite idle-container bootstrap

`scripts/accept_supplemental_recording_idle.py` is a separate, uninstalled PID-1
entrypoint for the future recording candidate. It does not import scanner or
audio services, open a network socket, start an operator, or claim native health.
The old supplemental App entrypoint already starts its own scanner owner and
must not be reused as an idle container for another operator.

The new entrypoint accepts only a pinned, closed lease at
`/data/sdsctl-recording-<case>/idle/lease.json`. It requires isolated Python, root
and PID 1, the original host boot ID, private no-follow paths, and original bounded
ready/stop times. It exclusively publishes and fsyncs one consumed claim before
waiting. Existing or partially acknowledged claims refuse restart; the entrypoint
never deletes or resets them. TERM/INT exit normally; original lease expiration
returns75, and invalid/changed files or startup failures return70 with a fixed
message. The lease and its namespace are rechecked while waiting.
The TERM/INT handler only sets a main-thread boolean; it never acquires an event
or condition lock. This avoids reentrant signal deadlock during the wait. The
original 100 ms polling cadence and absolute lease deadline remain unchanged.
Regression tests force both real signals while a wait-condition lock is held;
the old implementation deadlocked, and the lock-free handler exits normally
without changing the consumed claim or permitting restart.

This is a local elapsed-time bound, not protection against SIGSTOP or blocked
kernel I/O; independent host termination is still required. Local subprocess
tests exercise actual files, deadlines, claim fsync, signals and refusal to
restart, with explicitly substituted root/PID-1 facts. The real CLI refuses a
normal non-PID-1 process. Installed namespace teardown and exact init/native exit
remain unqualified. The closed native source inventory includes this entrypoint,
but no existing image, App configuration or legacy host schema selects it.

Separate local Docker qualification has also exercised the actual root/PID-1
entrypoint, without substituted process identity: normal TERM, original lease
expiry, and a deliberately changed fixture lease. Each case used its own new
volume, network disabled and a read-only image/code mount, with no scanner or
user profile. A real retained host pidfd and independent exact-container status
both confirmed exit (respectively0,75,70); consumed claims and original lease
backups were preserved. These local tests do not qualify the installed HAOS
host adapter, native worker teardown, or normal-App restoration.

The future host plan must create all fixed case branches before pinning ancestor
identities, retain the actual idle init, and only then produce the native launch
plan for that observed container generation. Idle-container evidence must have
its own startup phase; it cannot be reported as a healthy native scanner runtime.
The lease explicitly declares `CLOCK_MONOTONIC`, matching native deadlines.
The legacy host recovery clock uses `CLOCK_BOOTTIME`; its numeric values must not
be copied into native leases. The new host adapter must explicitly bind the
domains and retain independent suspend-aware recovery deadlines.

`scripts/supplemental_recording_clock.py` provides a read-only, uninstalled
conversion component. It brackets a real `CLOCK_BOOTTIME` sample with
`CLOCK_MONOTONIC` samples, retains the boot and time-namespace identity, and
rejects sampling intervals longer than 5 ms. Conversion subtracts the maximum
possible offset and rounds down, so it cannot silently extend the original host
deadline. A later sample must retain the same domain and an overlapping offset
interval; a detected suspend-offset jump, reversed clock, or changed namespace
refuses further dispatch. No conversion renews readiness or replaces the
independent host recovery timer. Tests cover synthetic offsets and fault cases,
plus read-only observations of the real kernel clocks; they do not suspend the
host or qualify an installed container's time namespace.

The original service also retains a `ClockWitness` for its entire lifetime. It
holds the actual current time-namespace descriptor, verifies both current and
child time-namespace paths, and checks every service clock sample against both
the original plan sample and the previous sample. Losing or replacing the
descriptor, changing namespaces, reversing time or changing the suspend offset
refuses further use. The witness never changes a clock or renews a deadline.
It is owned by the original service thread and closes after the service's other
owned resources; a replacement object cannot supply time or take over cleanup.
This proves continuing local clock custody, not equivalence with a different
host/native namespace, proc-mount provenance or independent outer supervision.

### Separate idle-to-operator policy

`scripts/supplemental_recording_host_plan.py` now defines a distinct closed
schema3 with kind `finite-recording-host-plan-v1`. It is a **description and
decoder only**, not an installed host service. Existing host schemas1/2 refuse
it and it refuses their documents. It accepts no caller command, environment
override, restart permission or guessed future native generation.

The plan separately pins helper and candidate image/source/interpreter/environment,
the original CLI/Core/normal incarnations, installed versions and other scanner
owners. The normal App retains its entire `ProtectedFiles`, including recordings;
the candidate uses its distinct static seal and original recording contract.
Profile and recording paths remain disjoint. The original host/native manifest
projection and native baseline require a separate matching retained-projection
check; decoding does not recapture or adopt files.

Original clock-window and BOOTTIME deadlines are included in canonical plan
bytes. The fixed idle command and MONOTONIC lease derive conservatively from
that original sample, without a current-time renewal. Recovery remains bounded
by the original1500-second host budget. The subsequent bootstrap journal contract
binds the completed plan digest and derived lease, avoiding a self-referential
manifest. The native launch plan is intentionally deferred until real idle init
has been independently observed and retained.

Decoded fields use frozen records and immutable sequences. Their canonical
reconstruction must match the original bytes, so even an in-process field
replacement cannot retain an old plan digest. Duplicate keys, noncanonical
bytes, unknown fields, changed case/source/layout bindings and unqualified
deadline/clock inputs refuse decoding. These checks authenticate no external
fact and grant no permission by themselves. The future adapter must bind this
declaration to actual current source, runtime, environment, mount, process,
cached-probe and recovery evidence before any lifecycle operation.

`scripts/supplemental_recording_bootstrap.py` adds a distinct offline journal
format3. This is **not** an installed host-plan version and is not selected by
any existing service. Old journal formats1/2 and installed host plans1/2 retain
their existing meanings and refuse the new journal.

After the original normal-owner exit and candidate-start CLI exit are separately
proven, the retained candidate init may enter `candidate_idle`. Both native
health and recording remain unknown (`null`), because idle PID-1 runs neither
service. A running old daemon, an idle claim alone, or synthesized healthy/idle
flags cannot supply this transition.

One durable `authorize_operator` event binds the observed init generation,
fixed bootstrap/lease/source contract, immutable native launch-plan digest and
qualified idle evidence. The resulting permission is returned only after journal
publication; replay never returns it again. This is separate from the actual
Engine create/attach ledger and does not authorize recording. An ambiguous
publication keeps the case consumed even if its entry exists on disk.

The `starting_operator` phase advances only on separately qualified actual
readiness **and** a fresh truthful cached probe reporting healthy with recording
inactive. A healthy-looking observation alone cannot advance it. Only then may
the existing independent recording-start authorization be recorded, still before
the original begin deadline. These transitions never reset the original phase,
trial, readiness, lease-stop or total recovery bounds. Policy times are in the
host recovery clock domain; native timestamps require the qualified clock
binding, not direct numeric reuse.

If an operator launch was authorized, restoration additionally requires distinct
worker/exec-closure evidence, including failed or lost-return cases. Neither
good files nor candidate-init/CLI exit substitutes for it. A never-launched idle
candidate may follow the original init/CLI-exit restoration path. Unknown facts
withhold actions; expiry or conflicting identities require review and the
independent recovery supervisor. The pure policy authenticates none of its
observation inputs: the future host adapter must qualify the exact source,
environment, mount, process, lease, return and cached-probe facts separately.

## Read-only idle-init observation

`scripts/supplemental_recording_idle_observer.py` joins the closed host plan to
an already retained actual init pidfd, the fixed original lease and its one-use
claim. It opens the fixed host-mapped case directory without following links,
requires private root-owned regular files, checks the exact bounded command
line and root/PID1/user/time namespace facts, and preserves original directory
and file identities across refreshes. Both real clocks are checked against the
original plan; suspend, expiry, replacement or uncertainty poisons the collector.

Kernel process-start ticks use BOOTTIME while the native claim uses MONOTONIC.
Their comparison uses the original bounded offset and kernel tick resolution,
not an assumption that the clocks are identical. The kernel's implementation is
documented in [Linux proc array.c](https://github.com/torvalds/linux/blob/v6.18/fs/proc/array.c).
This coarse comparison supplements, rather than replaces, the retained pidfd
and separately qualified source/claim continuity.

Successful observation still reports **health and recording as unconfirmed**.
It is not daemon readiness, recording inactivity, an operator-launch permission,
an exit receipt, or proof of exclusive scanner ownership. The adapter must
authenticate Engine/image/generation, interpreter/environment, mounts and all
protected inputs around it. The collector has no Engine/network/scanner or
signal operation and cannot renew deadlines. Local fixtures explicitly map
HAOS paths and PID1 namespace/argv facts; those fixtures are not installed-host
qualification. An actual source-pinned idle image and isolated platform recovery
still require separate checks before physical testing.

### Explicit zero-offset time namespaces

Some container runtimes create a separate time namespace even with host PID
visibility. Matching numeric timestamps do not prove that two clock domains are
equivalent. `scripts/supplemental_recording_time_domain.py` therefore offers a
separate, read-only `ZeroDomain` witness: the continuing helper retains both
namespace descriptors and an already bound native-init pidfd, checks the actual
current and child time namespaces, and reads both kernel offsets twice. Only
exactly zero MONOTONIC and BOOTTIME offsets qualify. Missing, nonzero, changing
or unknown evidence, process exit, suspend or a changed original clock refuses
use and poisons the witness. No clock or namespace is entered or changed.

The current namespace must equal `time_for_children` on both sides because the
kernel's offset file describes the child namespace. With a live member, its
offsets are frozen; retained descriptors prevent namespace inode recycling.
See the [Linux time namespace implementation](https://github.com/torvalds/linux/blob/v6.18/kernel/time/namespace.c).

The idle observer may explicitly receive this live witness. It checks the
original plan clock and exact init identity, rechecks the witness on every
process observation, and retains its digest in the idle evidence. Serialized
evidence is not accepted. Without this option, the exact same-namespace rule
remains unchanged. The helper's original clock must be sampled inside that
continuing helper, not transplanted from a separate driver. No deadline is
renewed or converted for a nonzero namespace.

The standalone idle PID-1 also retains its own time-namespace descriptor and
checks both zero offsets before publishing its one-use claim and on each lease
continuity check. A nonzero, changed or unconfirmed clock domain cannot consume
the lease. This does not replace the host's independent BOOTTIME recovery
deadline or turn the idle process into a healthy daemon.

The private Engine/ready join can also explicitly carry this witness. Readiness
requires its original clock to match the plan clock and its retained init to
match the exact dispatch target. Every native actor must still share the exact
init namespaces; only the independently verified helper/init time-domain
relationship may differ. The original witness and digest remain required after
begin and while consuming buffered returns after child exit. Transport closure
does not close this caller-owned witness, renew a deadline or prove process exit.

No installed host service selects this option. Source/runtime, Engine incarnation,
mount/proc provenance and independent recovery checks remain separate. An
idle-init proof still reports both daemon health and recording as unconfirmed.
Synthetic Engine tests exercise argument propagation and fail-closed continuity;
they do not establish installed platform or real native-container readiness.

## Required live ownership and recovery contract

The uninstalled `PreHandoffHost` collector now provides one complete current
sample before ownership transfer. Its constructor is passive. Reading requires
the exact original normal-App generation, independently sealed source/options/
profile and unchanged recordings, a pristine candidate recording baseline,
the pinned Core/CLI/installed versions, idle Supervisor jobs, stopped other
scanner owners and the qualified RTP mapping. Only the fixed cached normal-App
health reader may run; its healthy and recording-idle result must be confirmed.

An existing candidate container, even exited, is refused before any cached App
read. It is not deleted, restarted or treated as a fresh case. Unknown health,
normal generation changes, lost reads, altered plan/projection objects and
late observations permanently consume the collector. Successful instances are
also single-use. The complete sample retains the earliest observation time,
with the unchanged two-second limit in both clock domains. No journal, operator
notice, App command, process handle or recovery permission is created.

This current preflight sample does **not** replace the original preparation
observation or reset `issued_at`. The original sealed baseline and bootstrap
journal, fresh local operator request, actual helper/runtime qualification and
independent recovery session still have to be assembled. The collector did not
change source-graph membership when introduced; helper source bytes/pins changed,
so earlier image results do not qualify the new bundle.

`TransferHost` supplies the successive, freshly collected observations between
the original prepared journal and `candidate_idle`. It retains the same plan,
projection, Docker object and journal descriptor. Each observation brackets the
full host/pristine-file read with the actual original journal bytes and pure
replay; it never substitutes a reconstructed journal or newer preparation
baseline. The original two-second freshness and ready bounds remain unchanged.

The normal App may receive only a fresh fixed cache read for its original
generation. Candidate native health and recording state remain unknown: this
reader never executes a candidate probe or claims that a running container is
a ready daemon. The original recovery session must join init and CLI receipts;
subsequent Idle/Launch qualification is still required. Existing candidates
before the start intent, changed generations, missing exits, altered journals,
concurrent reads and uncertain transport cannot create new dispatch authority.
Changed protected fingerprints and busy-job facts remain visible to policy.

This reader does not append journal events, submit operator notices or send App
commands. Only the existing executor consumes fresh policy intents, including
its second pre-dispatch observation. A failed read permanently consumes the
reader; successful reads may continue only within the initial transfer phases.
Operator authorization ends this reader's scope. Post-launch observation,
service entrypoint assembly and installed-host qualification remain separate
work. Local integration tests join one original
RecoverySession through normal stop and candidate idle with explicit synthetic
Engine/process receipts, including lost stop replies and missing exit proof;
they are not installed App or actual pidfd qualification.

`NeverLaunchedHost` supplies a separate pristine cancellation route. It retains
the original TransferHost, journal, recovery session, dispatcher, process tracker
and candidate init witness. Construction is limited to `candidate_idle`; reads
require a durable explicit finish and no operator/recording authorization.
The original protected files must remain pristine. Missing/replaced candidate
containers, changed generation or journal, replaced session members, uncertain
reads and unexpected recordings cannot qualify this route.

Only after the original normal-start intent and original init/CLI exit receipts
may it read the restored normal App's cached health. The normal generation must
be new, and the original candidate must remain stopped. Native uncertainty is
not converted into healthy/idle status. Every read keeps the two-second budget;
recovery uses the plan's original recovery deadline, not renewed readiness.
The existing launch/transfer journal checker retains its stricter stop bound.

`recover_never_launched()` is a one-use continuation of that same session, not a
new service or an automatic finish request. The existing executor independently
rechecks preconditions and sends each durable App command at most once. Lost
stop replies are reconciled, not retried; missing process or CLI exit prevents
restoration. Success means normal-App restoration with recording `not_attempted`,
never a successful recording. Synthetic integration covers the complete route,
including cancellation just before readiness expires and restoration afterward;
actual installed recovery/source/runtime and external custody are still gates.

The uninstalled `supplemental_recording_service_operator.Inbox` now consumes
explicit input for the initial request and never-launched cancellation. Its
distinct canonical `finite-recording-service-notice-v1` format binds the action,
case, boot, original plan digest, original preparation-event digest and issue
time. Old idle-helper notices are not accepted. `request.json` is eligible only
in `prepared`; `cancel_idle.json` is eligible only in `candidate_idle`, before
operator or recording authorization. A notice is not native-launch or recording
permission. No file is automatically submitted by constructing this reader.

The caller's original `CasePlan`, projection and journal remain in custody. Each
consumption rereads their actual bytes and checks the private inbox and notice
identity, single-link 0600 ownership, canonical bounded JSON, original readiness
deadline, maximum 30-second notice age and two-second observation window.
Symlinks, pending/unknown entries, replaced inputs, changed files, concurrency
and lost journal acknowledgements refuse without deleting or resubmitting
anything. The exact journal event is the durable consumption receipt. A refused
input reader cannot disable the separately owned expiry/recovery loop.

The inbox retains its directory identity but does not retain an advisory lock
between calls. Its separate `Publisher` accepts an explicit action, preparation
digest and original issue time, retains the original `CasePlan`, and permits
exactly one attempt. It uses an exclusive directory lock, exclusive temporary
creation, a file sync, no-overwrite hardlink publication and a directory sync.
Only its own verified temporary hardlink is removed on the successful path;
any partial or uncertain files remain for review, with no retry or reset.
Publication neither owns nor appends to the service journal and sends no App
command. The consumer must still independently qualify the journal and phase.

Actual directory-lock contention means no notice is available yet; it does not
permanently poison a consumer polling during publication. This exception is
classified only at the nonblocking `flock` call. A read/open error with the same
OS error number remains an unconfirmed failure, not a lock-busy result. The
caller still must run its independent deadline/recovery checks while input is
absent or busy. No waiting loop or renewed time budget is introduced.

`IdleCoordinator` joins these inputs to the original initial-transfer session.
It must be constructed from the same `CasePlan`, projection, preparation-only
journal, TransferHost and recovery session before any request or process binding.
It never installs a `consume_operator` callback. Each step consumes eligible
explicit input and calls the original session, retaining that session's independent
expiry tick and one-use dispatch. At candidate idle it retains a separate pristine
cancellation reader before accepting cancellation. A durable explicit finish
routes the same session through `recover_never_launched`, even if the consumer's
post-commit acknowledgement was lost; actual journal bytes are reverified first.

The coordinator never retries a refused input reader. Missing, busy or refused
input does not disable independent session expiry. Expiry enters review, not an
invented finish or automatic restoration. Replaced/lost phase objects, changed
plan/journal, a foreign thread or an interrupted continuation permanently refuse
reuse. The original caller retains closure responsibility and must enforce the
outer runtime deadline. There is no native-launch or recording branch here.
Synthetic integration covers request publication through initial transfer,
idle cancellation and normal restoration, lost acknowledgements, missing process
exit and refusal to reinterpret an already authorized native launch as pristine.

These are library boundaries, **not** an installed service entrypoint or
restored-process proof. The helper graph has 54 modules; changes to the shared
protection helper require rebuilding and independently pinning both host and
native bundles. Real notice/journal files are used in the coordinator tests,
but Engine and process outcomes remain explicitly synthetic.

`IdleService` assembles that idle-first graph from the original `CasePlan`,
projection, preparation-only journal and Docker adapter. Construction is passive
apart from retaining the Inbox and validating its original clock witness: it
neither observes the host nor publishes input or sends commands. Its single-use `run()` polls the same
coordinator/session, using the existing 0.25-second interval, original phase and
recovery deadlines, and a finite defensive iteration ceiling. The ceiling is an
unconfirmed outcome, not a successful restoration. Explicit request and
cancellation still come from the separate publisher; expiry never invents them.

Success, review, interruption and refusal release the assembly's original Inbox
and process handles. The caller's original plan and journal remain open, and all
files remain intact. Even replaced component attributes cannot redirect cleanup
away from the original owned handles. A foreign thread cannot run or close the
owner; partial-construction and closure failures are sanitized, preserve evidence
and cannot grant a successful result or retry. Creating another instance is not
permission to reopen or resume a consumed case.

The optional explicit `clock_witness` argument lets the continuing startup owner
lend its already retained original `ClockWitness` to this assembly. The service
checks the original window's exact values and types, same-process/thread ownership
and a fresh kernel clock read before assembling later resources. It never replaces
that witness or renews its sample. The default still acquires and owns its own
witness, while the explicit borrowed path leaves closure to the original caller
after all service cleanup. Construction failure, normal completion, expiry,
interruption and closure errors do not close a borrowed clock. A serialized,
failed, closed, foreign-owner or differently sampled witness refuses. This is a
library join only; acceptance, private startup publication and independently
supervised installed entrypoint qualification remain separate requirements.

While that original `run()` still retains custody, an explicit
`prepare_candidate()` call may prepare read-only `IdleCandidate` resources once.
It must be at pristine candidate idle, before cancellation or any native intent,
with valid input and the original candidate process witness still held by the
same coordinator/session. It creates an idle-evidence reader, full bootstrap
host reader and candidate source/runtime qualifier; it does not call the latter
two, change the session's reader, consume another notice, append an event,
launch native code or authorize recording. The caller supplies the independently
reviewed environment/host profile and fixed worker selection. None of these
values is inferred from a previous successful sample.

The new readers remain service-owned; cancellation and original expiry continue
unchanged. Partial construction, replaced objects, changed process descriptors,
profile changes, foreign threads and stale phases refuse reuse. Closure releases
the new idle duplicate and invalidates the owned host reader before the original
session's process handles are released. The caller's plan, journal and optional
clock-domain witness remain borrowed. A custody `recheck()` is not full host or
source/runtime qualification, native readiness, or permission to keep using the
resources after the idle owner exits. Tests cover this read-only preparation
inside the original request/cancellation loop with explicitly synthetic process
and idle observations; installed timing and the native phase join remain pending.

After preparation, an explicit `observe_candidate()` may join one complete
read-only host observation to that original owner's candidate source/runtime
qualification. It starts the fixed host reader before hashing, then joins that
same worker inside the source/runtime bracket. The worker keeps its original
start time and deadline, and the entire call—including custody checks and final
validation—must fit the unchanged two-second window. It neither grants a second
budget after preparation nor substitutes a later timestamp for older evidence.

The original journal, phase, session, profile and live witness must survive the
whole operation unchanged. Only the exact observation returned by the one fixed
reader is accepted; skipped, repeated, replaced, stale or native-health results
are refused. Every attempt is consumed, including interruptions. Refusal poisons
the original service and discards its original pending reader; catching that
error cannot resume the loop or dispatch another read. Successful results are
diagnostic observations, not reusable Ready evidence, an approved host state,
native intent or recording permission. No event is appended, no result is cached,
and explicit pristine cancellation still uses the same original recovery session.
Tests use the real host-reader worker and private files with explicitly synthetic
HA/process/source-runtime evidence; installed combined timing remains unqualified.

`prepare_launch()` now binds one passive `Launch` to the same still-running idle
owner. It takes independently supplied launch/profile digests and a caller-owned
Engine endpoint, and uses the original candidate idle reader, witness, bootstrap
host reader, source/runtime qualifier, plan and journal. Construction verifies
current custody and socket metadata within one two-second budget; it neither
connects to the Engine nor creates an exec, journal intent, Ready or recording
authorization. A previous successful observation is not required or reused as
authority. Preparation and the optional diagnostic observation remain distinct.

The service owns closure of that original prepared launch. The endpoint remains
caller-owned because no Engine Client has taken ownership. Explicit pristine
cancellation can still restore through the same session. Service cleanup closes
the prepared launch before its idle readers; completed recovery has already
closed its process tracker after verified exits and restoration. Preparation is
one-attempt, with sticky failure and preserved evidence on interruptions, changed
bindings, journal changes or expired budgets. A used, replaced or modified launch
without an explicit native handoff refuses the idle loop; it cannot silently
route native activity through never-launched recovery. Tests exercise the real
passive Launch and original private journal with synthetic Engine/init metadata,
not installed endpoint or native-process qualification.

An explicit `start_native()` now performs a one-way, **pre-recording** handoff
inside the original running owner. It requires that exact prepared launch, an
original still-prepared ledger checked against current bytes, and a distinct
caller-owned Engine observer endpoint. It retires the idle coordinator and
replaces the idle read callback **before** native dispatch. Request/cancel-idle
notices never select this route. The same plan, journal, process tracker,
dispatcher, executor, session, clock callbacks and absolute deadlines survive;
no session is rebuilt and no failed flag is cleared.

The handoff holds the original inbox's exclusive publication lock from its
final eligibility check through native dispatch and capture. An already
published idle cancellation, incomplete/unrecognized entry, or lock contention
refuses the start before native ownership or intent; none is interpreted as
absence of cancellation. That explicit start attempt is spent, its files remain
for review, and the independent outer lease still applies. Ordinary idle
polling retains its existing nonblocking behavior. Publication after the
handoff is not a consumption acknowledgement: the retired idle coordinator
cannot turn a late idle notice into native cancellation or pristine recovery.

After confirmed launch, the service captures independent duplicates of the
actual Ready actor pidfds in an original `Operator`. Recording authorization is
still absent: this route does not construct `Start`, write a start intent or
send a Relay begin. An explicit `cancel_native()` closes only the original
completion transport once. It neither sends an App stop nor claims process
exit, recording success, current health or `recording=False`. A lost close
acknowledgement cannot cause another cancellation attempt.

While waiting for actual exits, the original recovery session keeps its
clock-only expiry, but its host reader deliberately reports unavailable rather
than reusing idle or former Ready evidence. Launch/capture/exit-observation
uncertainty retires that native attempt without disabling those independent
ticks or retrying the operation. Interruption, changed ownership or corrupted
service bindings remain fatal to that owner; independent external supervision
must still enforce the original lease.

Only the retained Operator's actual worker-exit publication, separate original
init exit, fresh pristine files and original prepared ledger can enter the
distinct `NeverAuthorized` continuation. It uses the same recovery session and
existing current-host/restored-normal checks; its outcome remains
`not_attempted`, never an invented successful or abandoned recording. If capture
was lost, it cannot reopen pidfds or fall back to never-launched recovery.
Routing tests use real private files and policy journals, with explicitly
synthetic native/exit/continuation boundaries. A separate local process case
also completed this whole original-service pre-recording cancellation route:
actual native Ready and actor capture, cancellation, worker/init exits, unchanged
baseline and prepared ledger, and restoration of the disposable normal fixture.
Its Supervisor/Core/CLI/cache/network/source routing remained explicitly
synthetic. It is not installed Home Assistant qualification or scanner acceptance.

The distinct `RecordingPhase` now joins an explicit `start_recording()` to the
same original service. It requires an uncancelled, confirmed native phase and
its already captured Operator; the native phase retires **before** constructing
the original `Start`. The existing fresh pre-begin checks, durable policy
authorization, ledger intent and single Relay begin remain authoritative.
The method reports a confirmed start only after the actual `Relay.started()`
return. It cannot report recording success from permission or a sent command.
No idle notice automatically selects this route, and old native cancellation
cannot operate after the recording handoff.

An explicit `finish_recording()` consumes the original bounded completion and
exit collectors once. It keeps `AuthorizedFinalized` capture, original worker
exit collection and exit publication in one owner step, with no generic journal
tick inserted inside that exact-prefix evidence window. The independent outer
supervisor and original native/attachment deadlines still bound blocking I/O.
Successful completion publication is not restoration: original init exit and
fresh finalized/current-host/restored-normal evidence must still pass through
the **same** recovery session before its policy can report a verified recording.

Start or completion uncertainty is sticky. In particular, a failed `Start`
may have durably authorized recording while marking its Launch failed. The
retired native owner does not disable original clock-only expiry or allow
pristine/never-authorized fallback in that situation. No failed operation is
retried, no abandoned ledger is invented, and no former Ready data is presented
as active health. This join leaves uncertain recordings for review; the explicit
confirmed-start abandonment route below is not a fallback for them. Its routing
tests use real policy and intent journals with explicit synthetic
native/completion boundaries.

An explicit `observe_recording()` now joins `PostBegin`, `RetainedQualification`,
`RetainedHost` and a new one-use `ActiveSample` for each active read. It takes
the original prepared environment/timezone/hostname/architecture/worker profile,
not replacement caller settings, and retains the original init/worker resources.
Every sample rechecks current source/runtime, host/files and authenticated native
state. Closing its temporary probe and checking owner context must still fit
the original evidence freshness window; completion time never refreshes the
earliest contributing timestamp.

These explicit reads are diagnostic observations, not automatic service polling,
progress-checkpoint publication, policy events, finalized artifacts or audible
acceptance. They do not cache a result for the session to reuse. A failed read
returns no observation, consumes that active route and leaves the original
clock-only expiry/review available. No replacement sampler can retry after
uncertainty, and no active read can interleave the finalized exit-publication
step. A fresh read after a successful read uses a new one-use sampler over the
same original post-begin resources. Independent outer supervision remains
required for blocked I/O.

After an active observation, `finish_recording()` performs a separate fresh
intermediate file read and durably appends and acknowledges its progress tip
before consuming completion. The original Relay requires that checkpoint chain
to reach its last observed progress; omitting the directory or treating the
earlier host sample as a checkpoint is not allowed. A lost checkpoint or ledger
acknowledgment makes completion uncertain and is never retried or discarded.
The no-active-read route still finalizes without introducing a progress chain.

An explicit `abandon_recording()` is a separate one-use decision while the
original start is confirmed, before any failed observation or finish attempt.
It rechecks the original authorization and open ledger, retains any intermediate
active progress through a separate acknowledged checkpoint, then durably closes
the original ledger as abandoned. Only after that exact acknowledgment does it
close the original completion transport. It sends no native stop/completion,
App stop, signal or replacement launch. A lost abandonment acknowledgment is
sticky even if the ledger bytes reached disk; it cannot be reloaded as authority.

A returned transport close is not process-exit evidence, and a lost close return
cannot be retried. The independently captured original Operator must observe and
publish actual worker exits once, then the original init must exit separately.
Only then can the distinct `Preserved` reader join the unchanged ledger, retained
files and same recovery session. Restoration through this route leaves the
recording **unconfirmed**, with no completion acknowledgment or successful
artifact, even if the native process returned zero. Lost exit publication,
poisoned or replaced ledger state and failed retained-file checks require review.
Routing tests exercise the actual private host ledger and policy with explicitly
synthetic native, exit and recovery boundaries. A separate independently
supervised local whole-service process case also passed: actual start, active
read, durable progress, abandonment, original worker/init exits, retained-file
checks and same-session restoration of the disposable normal service. Its
recording remained unconfirmed with no completion acknowledgment or artifact.
The original outer process handles were retained through every bounded exit.
Synthetic audio and platform/source aliases remain distinct from installed
Home Assistant qualification or physical/audible acceptance.

This finite library owner is still **not** an installed schema3 entrypoint. It
does not authenticate its own input digests, qualify its own helper's installed
confinement/source/runtime, or establish independent external supervision.
The recording start/finalize join has completed a local whole-service process
case with synthetic scanner/RTP peers and disposable normal/candidate containers.
It verified the synthetic recording, unchanged older file, original worker/init
exits and restoration through the same session. A separate earlier case safely
refused stale evidence before begin; a later pass does not erase that timing
failure. Supervisor/Core/CLI/cache/network/source routing in both cases remained
explicitly synthetic. The active-observation join has also completed a separate
synthetic-audio process case through a fresh active read, acknowledged progress,
finalization, original exits and same-session restoration. None of these cases
qualifies an installed host launcher or a real scanner/audio trial.
Assembly tests use real private files and explicit synthetic
Engine/process/cache fixtures, not Home Assistant or scanner acceptance.

The private schema3 plan can construct an initial bootstrap-journal event only
from its original qualified observation and retained host/native projection. It
checks the normal App's full seal and original generation, the distinct candidate
seal, and the exact pristine recording baseline. Journal time is the plan's
original `issued_at`; a newer clock or file snapshot cannot extend its recovery
deadline. This pure join does not collect or authenticate those inputs itself.

The uninstalled bootstrap recovery bridge uses the new journal3 explicitly. It
retains independent ticks, exact init/CLI exit reconciliation and one-use fixed
App dispatch. Old recording executors still reject this journal, and the new
bridge rejects old journals. It never launches the native operator or grants a
recording start from polling alone. If an operator launch was authorized, its
separate exit proof is required before restoration, even when the container
init has exited and the recording artifact is finalized.

The following remain live design gates, not installed host-service permissions.
The offline owner, monitor, recovery policy and API restriction above do not
install this contract:

| Phase | Required evidence and constraint |
| --- | --- |
| Before handoff | A new source-pinned case and explicit recording-capable plan; unchanged normal-App protection; separate candidate recording root; idle recording, fresh physical readiness, qualified RTP mapping and independent restoration supervision. |
| Before start | Fresh matching native/container identities; bounded old-file baseline; exact reserved template and root; one durably recorded start intent before dispatch. Existing browser playback must already have increasing counters and audible confirmation. |
| Start acknowledgment | Bind the one successful start to its generation, exact filename, start timestamp and endpoint. Unknown, late or malformed acknowledgment is not permission to start again. |
| Active window | One existing RTP/PCM owner fans out to the browser and recorder. The supplemental window/read cap cannot be extended. Recording progress, PSI/scanner freshness and stream faults remain distinct observations. |
| Stop/finalization | A bounded owner-controlled stop must drain the writer and publish a generation-bound stopped receipt before claiming finalization. Stop intent/acknowledgment uncertainty is preserved; no second writer or header-repair process is substituted. |
| After owner exit | Independently prove exact scanner-owning process exit, then inspect the new pair and every old file. Candidate shutdown must not depend on a working browser, desktop connection or a successful file-validation result. |
| Restoration and acceptance | Verify normal-App identity/health and its unchanged protections separately from the recording verdict. Preserve candidate evidence, including failed/partial files. Only after these checks may one bounded saved-file playback/listening test be offered. |

The active-file component now qualifies the native writer's intermediate states
locally. A future live contract still must bind those observations to the exact
writer/case and retain the independent termination deadline. It cannot substitute
a final-only inventory rule, blanket wildcard exclusion or always-true
recording-health flag.

The next implementation must also distinguish a failed artifact verdict from
ownership/recovery evidence: a truncated WAV does not prove that a scanner owner
is still alive, and a clean process exit does not prove that the WAV finalized.
No invalid artifact is silently accepted as the restored baseline. Failure
handling, allowed intermediate inventory changes, durable at-most-once dispatch,
and stopped/failed-case reconciliation need local fault-injection and isolated
platform qualification before another live handoff.

## Automated coverage and remaining gate

Local tests exercise malformed receipts, missing/extra fields, filename
collisions, metadata/WAV disagreements, file mutation, symlinks/hardlinks/FIFOs,
bounded reads, descriptor cleanup and sanitized failures. They also run the real
daemon recording manager on synthetic localhost RTP, checking decoded PCM bytes
and preserving older files.

For failing supplemental tests, pytest also reports a bounded list of source
locations behind sanitized refusals. This test-only diagnostic includes no
exception messages, source text, local variables or absolute paths. It does not
change production error handling, deadlines or retry policy; a passing rerun
does not by itself explain or erase an earlier refusal.

Additional integration cases keep one real PCMU Unix consumer and the recorder
on the same RTP owner while native FQK or DTM acquisition succeeds or times out.
The finalized WAV/sidecar pass the content verifier in each case; subsequent
transport faults must not alter their frozen recording statistics or hashes.
These are local fixtures, not a real browser or physical scanner listening test.

The independent [manual audible saved-player check](web-dashboard.md#saved-player-browser-qualification)
also passed on 2026-09-22, using a private copy of an existing finalized recording.
It did not create a new recording or exercise this ownership path.

No live recording case is staged or authorized by these components. Activation
requires the separate reviewed ownership/recovery implementation above, a new
sealed case, installed-source/platform qualification and fresh user readiness.
All earlier hardware cases remain consumed and closed.
