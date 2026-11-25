# SDS100 and SDS200 firmware 1.27 qualification

Uniden SDS100/SDS200 main firmware 1.27.00 is **not yet qualified for sdsctl**.
The existing hardware-validation record remains pinned to firmware 1.26.01.
This plan is intentionally deferred until after the current sdsctl release and
does not authorize a firmware update, scanner write or production deployment.

## Authoritative release information

Uniden published main firmware 1.27.00 for both models on November 6, 2026:

- [SDS100 firmware update change log](https://info.uniden.com/twiki/bin/view/UnidenMan4/SDS100FirmwareUpdate)
- [SDS200 firmware update change log](https://info.uniden.com/twiki/bin/view/UnidenMan4/SDS200FirmwareUpdate)

The documented changes are identical for the two models:

- Motorola and Harris P25 over-the-air (OTA) alias support;
- DMR Tier 3 OTA alias support;
- a fix for Location Control while scanning a single site; and
- a fix for duplicate Unit ID registration in the Sentinel/scanner Unit ID
  menu.

Uniden requires the latest Sentinel, recommends backing up programming before
updating and says firmware updates are applied through Sentinel. The update is
main firmware only; the pages do not publish a new matching sub-firmware
requirement for 1.27.00.

OTA aliases are conditional evidence, not a universal pass/fail oracle. Uniden
notes that the active scanner display must include `Unit ID Name`, the system
must broadcast alias data, saved or explicitly alpha-tagged aliases are not
overwritten, and signal quality must be sufficient. Phase 2 aliases may arrive
more slowly than Phase 1 aliases because of the underlying air-interface data
path.

## Safety boundary

Qualification is manual, attended and one scanner at a time. Keep the second
model/reference unit on its already accepted firmware until the first updated
scanner completes every applicable gate. Do not update both devices together.

- Do not assume firmware downgrade is supported. Confirm an official recovery
  path with Uniden before relying on rollback.
- Do not use the production Favorites database as the only backup.
- Do not publish scanner programming, Unit IDs, aliases, system names,
  frequencies, network addresses, recordings or credentials in test evidence.
- Do not let sdsctl, Home Assistant or a kiosk own the scanner during Sentinel
  backup or firmware installation.
- Do not automatically discover and install scanner firmware. Firmware remains
  an explicit operator-controlled maintenance action.

## Pre-update evidence

Create a private, dated qualification record for each physical scanner. Before
opening Sentinel's update command:

1. Record model, serial identifier in a private log, main firmware, sub
   firmware, Sentinel version, sdsctl commit/package version and transport.
2. Read the scanner into the latest Sentinel and save a separate backup copy of
   its programming. Verify the backup can be opened and lists the expected
   Favorites Lists and profile.
3. Record the SD-card health/status and preserve a separate card image or known
   restorable card when practical.
4. Capture sanitized 1.26.01 baselines for model/firmware, scanner information,
   PSI updates, volume, squelch, active screen/mode and the applicable battery
   result.
5. For the SDS200, capture sanitized USB and native-LAN baselines plus a
   five-minute RTSP/RTP audio sample with packet, overflow and RTP-loss
   counters.
6. Confirm normal scanning, a known conventional channel, a known trunked
   system, Weather, Close Call and Tone-Out state reporting before the update.
7. Stop every scanner owner and verify Sentinel has exclusive access.

Any unexplained baseline failure blocks the update. Preserve the evidence and
diagnose it on 1.26.01 instead of attributing it to 1.27.00.

## Attended update

Use only the latest Sentinel and its `Update` > `Update Firmware` command.
Verify the selected model and exact 1.27.00 target before confirming. Maintain
stable power for the SDS200 and a charged battery/external power source for the
SDS100. Do not disconnect USB, remove the SD card or start another scanner
owner while Sentinel is writing.

After the scanner restarts, verify main firmware 1.27.00 on the scanner itself
before reconnecting sdsctl. A failed or ambiguous update stops qualification;
follow Uniden's official recovery process rather than repeating writes blindly.

## Core post-update matrix

Run every applicable check first with sdsctl directly, then through the daemon
and web dashboard. Record exact commands, timestamps, expected/restored state
and sanitized results.

| Area | SDS100 USB | SDS200 USB | SDS200 LAN |
| --- | --- | --- | --- |
| Model and firmware identity | Required | Required | Required |
| Scanner information and PSI stream | Required | Required | Required |
| Volume/squelch getter | Required | Required | Required |
| Attended set/read/restore cycle | Required | Required | Required |
| Hold/next/previous navigation | Required | Required | Required |
| Normal and special-mode dashboard fields | Required | Required | Required |
| Native discovery/control | Not applicable | Not applicable | Required |
| RTSP/RTP live audio | Not applicable | Not applicable | Required |
| Live Waterfall transport/display | Not applicable | Not applicable | Required |

The controlled setter test must capture the original value, make one bounded
change, confirm it, restore the original value and confirm the restoration.
Never infer restoration from a successful setter acknowledgement.

## Mode and presentation checks

For both models, compare the physical scanner with CLI/daemon/web state in:

- ordinary conventional scanning and an active analog transmission;
- P25 Phase 1 and Phase 2 traffic when locally available;
- a DMR system when locally available;
- Weather Scan/Monitor on a settled channel;
- Close Call on a detected transmission;
- Tone-Out standby and one visible tone event; and
- hold and resumed scanning.

The dashboard must identify special modes and their active channel/state rather
than reusing stale normal-scan fields. Mode exit must return to normal updates
without a daemon or browser restart.

For the SDS200, also verify automatic dashboard-to-Waterfall presentation,
continuous waterfall streaming, live browser audio start/stop, Menu state and
diagnostic counters. Run a five-minute audio soak and require zero unexpected
queue overflows and RTP loss under the same network conditions used for the
1.26.01 baseline.

## 1.27-specific checks

### Single-site Location Control

Use a private copied profile with one enabled site and Location Control on.
Compare expected site eligibility and scanning before and after the update.
The scanner must continue scanning the intended site without unexplained
dropout or exclusion. Restore the normal profile after the test.

### Unit ID registration

In a private copied system, exercise the reviewed Sentinel/scanner Unit ID
workflow and confirm a single logical Unit ID is not duplicated. Do not alter
the production profile for this check. Verify sdsctl continues to tolerate the
resulting Unit ID data without parse errors or stale dashboard state.

### OTA aliases

Use a system known to broadcast OTA aliases and an active scanner display that
includes `Unit ID Name`. Record whether the system is P25 Phase 1, P25 Phase 2,
Motorola, Harris or DMR Tier 3 and whether an alias was already saved or
alpha-tagged. Compare the physical scanner and sanitized sdsctl state.

Lack of an alias does not fail compatibility unless all of Uniden's documented
prerequisites are independently established. A parser error, daemon failure,
stale activity state or dashboard corruption triggered by alias-bearing traffic
is a qualification failure.

## Soak and acceptance

After focused tests, run each updated model for at least one representative
operating shift. For the SDS200, include native LAN control, dashboard updates,
audio and Waterfall use. Record disconnects, retries, parser failures, stale
state, audio loss and scanner-side anomalies. Reboot the scanner once after the
soak and repeat identity plus representative read-only checks.

Firmware 1.27.00 may be added to the hardware-validation record only when:

- every required applicable gate passes on the exact sdsctl candidate;
- private programming and operator settings survive or are restored;
- no transport, parser, mode, audio, Waterfall or dashboard regression remains;
- the exact model/main/sub firmware and transports are recorded; and
- the second scanner/model is independently qualified rather than inferred
  from the first.

A successful firmware update alone is not sdsctl qualification. Conditional
OTA alias availability is reported as observed or unavailable, not invented as
proof. No automatic deployment, firmware updater or unattended scanner write is
authorized by this document.
