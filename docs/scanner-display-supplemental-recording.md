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
case's reserved prefix, including an older timestamp, sidecar or collision
suffix. The baseline is an immutable in-memory result for a trusted caller; its
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

## Required live ownership and recovery contract

The following are design gates, not implemented host-service permissions:

| Phase | Required evidence and constraint |
| --- | --- |
| Before handoff | A new source-pinned case and explicit recording-capable plan; unchanged normal-App protection; separate candidate recording root; idle recording, fresh physical readiness, qualified RTP mapping and independent restoration supervision. |
| Before start | Fresh matching native/container identities; bounded old-file baseline; exact reserved template and root; one durably recorded start intent before dispatch. Existing browser playback must already have increasing counters and audible confirmation. |
| Start acknowledgment | Bind the one successful start to its generation, exact filename, start timestamp and endpoint. Unknown, late or malformed acknowledgment is not permission to start again. |
| Active window | One existing RTP/PCM owner fans out to the browser and recorder. The supplemental window/read cap cannot be extended. Recording progress, PSI/scanner freshness and stream faults remain distinct observations. |
| Stop/finalization | A bounded owner-controlled stop must drain the writer and publish a generation-bound stopped receipt before claiming finalization. Stop intent/acknowledgment uncertainty is preserved; no second writer or header-repair process is substituted. |
| After owner exit | Independently prove exact scanner-owning process exit, then inspect the new pair and every old file. Candidate shutdown must not depend on a working browser, desktop connection or a successful file-validation result. |
| Restoration and acceptance | Verify normal-App identity/health and its unchanged protections separately from the recording verdict. Preserve candidate evidence, including failed/partial files. Only after these checks may one bounded saved-file playback/listening test be offered. |

An active WAV changes while it is written, and the metadata writer uses a
short-lived temporary file plus an exclusive link publication. Therefore a final
inventory rule is **not** an active-recording monitor. A future live contract must
qualify those intermediate states explicitly, bind them to the exact writer/case,
and retain the independent termination deadline. A blanket wildcard exclusion or
an always-true recording-health flag would defeat the protection.

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

No live recording case is staged or authorized by this component. Activation
requires the separate reviewed ownership/recovery implementation above, a new
sealed case, installed-source/platform qualification and fresh user readiness.
All earlier hardware cases remain consumed and closed.
