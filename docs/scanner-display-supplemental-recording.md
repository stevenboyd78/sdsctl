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
durable generation-bound operator gate, recording-capable host policy,
container-init exit/restoration reconciliation, and isolated installed-container
qualification. Existing handoff plans and their idle/inventory checks remain
unchanged; none of these offline results activates live recording permissions.

## Required live ownership and recovery contract

The following remain design gates, not implemented host-service permissions.
The offline owner, monitor and API restriction above do not install this contract:

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
