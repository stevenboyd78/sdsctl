# Experimental browser registration and first-run setup

Status: **unreleased, isolated acceptance only—not a production installer**.
Use the matching reviewed development build, not the published v0.29.4 command
set. The existing [manual-login kiosk guide](browser-kiosk.md) and TUI setup are
unchanged. Do not use an everyday browser profile or a production credential for
this experiment.

This step joins two previously separate pieces: the reviewed extension/native
bundle and the browser's initial recovery state. It does not store a dashboard
password. The native helper retains the separate, per-device credential outside
browser storage; the browser receives a short-lived session only during a later,
verified authentication exchange.

## 1. Check the prerequisites

Use a non-root Linux account and an existing private lab directory owned by that
account with mode `0700`. Complete the [native profile](browser-device-profile.md)
and [review bundle](browser-device-bundle.md) steps first. Keep the exact Python
installation, bundle paths, reviewed public key, and native profile in place.

The native profile must still be pristine: active, revision 1, and never used,
paused, resumed or claimed by another browser. Inspection is offline; it does
not prove that a server is reachable, that credentials are accepted, or that the
browser trusts the certificate. DNS, IPv4 and bracketed IPv6 identities remain
supported. Neither a proxy nor internal DNS is required.

## 2. Register only a new dedicated browser directory

For the example account `display`, run:

```sh
sdsctl browser-device-register --experimental \
  --directory /home/display/sdsctl-browser-lab/chromium-data \
  --bundle /home/display/sdsctl-browser-lab/review-bundle \
  --profile /home/display/sdsctl-browser-lab/native-profile \
  --public-key /home/display/sdsctl-browser-lab/extension.pub.pem
```

Do **not** create `chromium-data` beforehand. Existing directories, including
empty ones and this command's prior partial output, are refused. Inputs must use
absolute, symlink-free paths and the private ownership/permissions required by
the earlier setup commands.

Before writing anything, registration compares every bundle file and its receipt
with canonical artifacts regenerated from this installed runtime. Editing code
and then updating receipt hashes does not authorize that code. Unexpected files,
different runtime paths, identity mismatches, unsafe files and used profiles are
refused. This comparison is not a publisher signature or protection against a
compromised same-user/root process.

Success creates only:

- A new mode-`0700` Chromium user-data directory.
- Its private `NativeMessagingHosts/org.sdsctl.browser_device.json` manifest,
  restricted to the one exact extension origin and fixed native wrapper.
- A private `.sdsctl-browser-registration.json` completion receipt, written last.

The command prints the public extension setup URL. It does not load an extension,
launch a browser, initialize browser storage, authenticate, change trust stores,
write system policy, enable a service, or modify the native profile. Exit codes
are `0` for confirmed registration, `78` for redacted validation/write failures,
and `2` for argument errors or missing opt-in. Retain partial output for review;
never retry over it or copy it into an existing browser profile.

## 3. Explicitly initialize in an isolated browser

Loading the reviewed unpacked extension is a separate acceptance action. The
browser must use the exact newly registered **user-data directory**, not just
another named profile in an everyday browser. Chromium variants differ; verify
the selected browser against its
[native-messaging rules](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging).
Keep the normal sandbox and certificate verification enabled. This command does
not supply a generic production browser launcher or an extension distribution
policy.

The separate [experimental startup candidate](browser-device-startup.md) adds an
offline check and foreground Chromium launch with an explicit `--setup` choice.
It remains an isolated lab path, not service or production server installation.

In that isolated browser, open the setup URL printed by registration. The page
shows the fixed server, device and extension identity, with no password field.
Verify those values, select the confirmation checkbox and choose **Initialize
automatic sign-in**. The confirmation authorizes future recovery for this
experimental profile; it is not merely a display preference.

Initialization requires all of the following:

1. A successful browser-storage read that finds the recovery key absent. A read
   error, corrupt value, saved pause, existing state or interrupted setup is not
   treated as a new browser.
2. A persisted, paused `setup_pending` marker before contacting the native helper.
3. A successful one-time `claim-browser` request. The native SQLite transaction
   accepts only pristine active revision 1, advances it to revision 2 and never
   reads credentials or contacts the server. Concurrent claims have one winner.
4. A final valid browser-state write, preserving any concurrent pause/sign-out.

The setup action itself does not authenticate or create a session cookie. Once
the final state is committed, a later browser/extension start can run ordinary
recovery and authenticate, subject to the native ledger, verified TLS and server
authorization. Chromium may restart a service worker without restarting the
whole browser; do not treat an open setup page as a durable authentication pause.

## Failure and restart behavior

| Situation | Result |
| --- | --- |
| Fresh directory and pristine native profile | Explicit initialization can commit once |
| Existing valid browser state | Setup refused without changing its state, alarms or cookies |
| Saved pause, native rejection/TLS error, or previously used ledger | Never cleared by setup |
| Interruption before final valid browser-state commit | Pending setup remains blocked for review |
| Final commit succeeds but the UI acknowledgement is lost | Later normal recovery may run; a second initialization is still refused |
| Browser storage is deleted after native claim | Claim cannot replay; setup remains blocked |
| Pause or sign-out races initialization | Pause wins; no setup authentication |

Do not delete browser state or the native ledger to fix a refusal. Do not copy a
used ledger or manually seed browser storage. Keep the files for administrator
review. Explicit replacement/resume, credential rotation and update/removal
workflows remain separate work; there is no page/native `resume` message.

## Acceptance and remaining limits

Two separate harnesses in [the experimental test guide](../scripts/experimental/README.md)
use an installed candidate wheel, fictional credentials, unique Chromium
directories and normal sandboxing:

- The bundle harness's `first-run` mode checks the real setup page, native
  registration/claim, pause across browser restart and refusal after deleted
  browser state. This is a no-login setup test.
- `audit_browser_generated_recovery.mjs` connects the installed profile import,
  bundle creation and registration commands to that same setup form, followed by
  actual ASGI/native/Chromium authentication. It does not seed recovery storage,
  inject cookies or substitute fixture-only extension code. Setup must complete
  with zero authentication exchanges; a later browser restart obtains a verified
  display-only session. The server is a fictional loopback authority with no
  scanner, not a production Home Assistant administrator session.

The integrated matrix covers healthy recovery, native deadlines, truncated
responses, interrupted TLS handshakes, server restart, real scheduled renewal
after worker stop, revocation, and issuer/name rejection. Ordinary success cases
also require actual page sign-out, server pause, cookie removal and no login after
browser restart. Terminal rejection must likewise survive restart. A retryable
native `active` result means recovery is allowed, not that a session exists;
interruption checks inspect the failure ledger, missing cookie and scheduled
retry instead. No status request is sent to wake a stopped worker during renewal.

On September 7, 2026, all **19 generated-path cases passed** with an installed
candidate wheel on Linux/aarch64 and Chromium 152.0.7977.75: all nine scenarios
above for both IPv4 and DNS, plus healthy IPv6 login/sign-out. The normal browser
sandbox and certificate verification remained enabled. Fictional certificate
trust was confined to a temporary browser mount namespace; real trust stores,
production credentials and display services were not changed.

Deterministic tests additionally cover setup interruptions, concurrent claims,
sender checks, fixed UI errors, and native/browser pause races. Neither harness
proves live scanner data, physical screen behavior or combined power-loss recovery.

Production distribution/update/removal, trusted browser launch and server wiring,
real administrator enrollment/delivery, replacement/resume and physical
multi-display/server outage tests remain required. No Home Assistant or display
service is changed by these preparation commands. No release, wiki production
instructions or unattended-production-login claim follows from this step.
