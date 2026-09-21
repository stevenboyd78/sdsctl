# Bounded supplemental-read media observer

Development qualification only. This is not normal polling, a playback client,
a recorder, a scanner command, or a hardware test trigger. Do not run it merely
because the local tests pass. A fresh reviewed case and operator readiness are
required before its use against a real acceptance App.

The [observer](../scripts/observe_supplemental_media.py) samples the existing
daemon over one **local Unix socket**. Its exact operation allowlist is:

- `hello`, once, to validate the existing protocol and advertised operations;
- `runtime.snapshot`, for cached runtime/audio progress; and
- `recording.status`, for cached recording/sink/reliability progress.

Even other read APIs are excluded. In particular, `display.frame` can create
optional-reader demand and must not be called by the observer. There are no
scanner GET/SET/KEY requests, recording start/stop calls, playback connections,
new audio owners, signals, retries, or reconnects in this helper.

## Binding and finite observation

The operator prepares a small private regular JSON file, owned by the invoking
account, without group/other access. It has exactly four nonempty string fields:

- `endpoint`: the preflight runtime's scanner endpoint;
- `runtime_started`: that runtime's start timestamp;
- `recording`: the exact already-started test recording's relative identity; and
- `audio_endpoint`: the existing runtime audio transport endpoint.

Obtain these from the reviewed case's cached baseline. Do not infer a new binding
after a mismatch, copy credentials into it, select an arbitrary active recording,
or silently use a later runtime after recovery. Do not commit live bindings.

The containing evidence directory must already exist and be private to the
invoking account. The evidence file must not exist. In the actual acceptance
container, with the source-qualified script and installed package available:

```sh
python /absolute/reviewed/observe_supplemental_media.py \
  --observe-75s \
  --socket /absolute/reviewed/daemon.sock \
  --binding /absolute/private/new-case/media-binding.json \
  --evidence /absolute/private/new-case/media.ndjson
```

This example is a procedure, not authorization to contact a device. Missing
arguments and `--help` do not connect. An exclusive mode-0600 evidence file
records and syncs intent **before connecting**. Existing or uncertain output
must be preserved for review, never erased or reused to obtain a passing result.

There is one at-most-75-second observation, at most 75 checkpoint pairs and
151 API requests. Each request has one 400 ms deadline shared by connect, send,
all received fragments and response validation; slow partial replies do not
reset that deadline. Frames are capped at 256 KiB. Calls and snapshots are
serialized, with up to one second between samples. This is an OS-scheduled
bounded diagnostic, not a hard-real-time guarantee; observed overruns fail.

Two valid checkpoints must show progressing counters without new faults before
the evidence emits `media_ready`. That marker is informational: it never arms
or starts a scanner trial. The separate orchestrator must verify the observer
is still current and running, the correct source/image/session, and current PSI
before using its own single-use trigger. A failed observer never retries.

## Evidence interpretation and privacy

Retained checkpoints contain only monotonic times and fixed-order numeric
counters. The summary names those counters, reports their deltas and the largest
unsampled gap, and distinguishes normal completion from interrupted observation.
Identities are compared privately but not copied into output. No raw PSI, voice
payloads, credentials, private endpoint strings or arbitrary exception text are
retained. A missing final summary is incomplete evidence, not success.

Monotonic comparisons require the same time namespace as the supplemental
reader's evidence. Cached API calls are not atomic; exact instantaneous equality
between audio and recording sample counts is not required. Missing fields,
counter resets, identity changes, inactive/error states, invalid timestamps and
unbounded acquisitions are refused. Fault deltas or stalled total progress
require review. Old transport-session faults remain baseline counters rather
than being mislabeled as faults introduced by the trial.

`progress_observed` does **not** establish:

- uninterrupted audible browser output or the number of browser consumers;
- the recorded audio's audible quality or finalized-file consistency;
- current qualified PSI throughout the interval;
- physical scanner continuity; or
- the separate supplemental-read window's result.

The WebUI uses daemon PCMU delivery, not the separate HA integration's MP3
encoder. Observe the selected page's visible packet/queue/RTP diagnostics and
user-heard playback without opening a second stream. Unless a separately reviewed
source exposes the global consumer count, leave that count unobserved and narrow
the claim to the selected page. Inspect finalized WAV/metadata separately,
preserving all old recordings and retaining the one new test recording.

## Offline qualification

The [tests](../tests/test_supplemental_media_observer.py) cover projection,
resets/faults/privacy, read-only operation limits, fragmented/late/malformed
replies, exclusive evidence, and a complete real local Unix-server exchange.
An additional fixture uses the actual runtime, localhost RTP receiver, PCM
fanout and recording manager, verifies a byte-exact finalized WAV, and proves
that observing does not create optional scanner-read demand. Its RTSP setup,
radio peer and observation clock are synthetic; it is not physical acceptance.

Use this alongside the separate
[receive-only timing observer](../scripts/observe_supplemental_timing.py), never
as a replacement for its PSI or command-reply evidence. Ordinary supplemental
acquisition, public fields, deployments and released configuration stay unchanged.
