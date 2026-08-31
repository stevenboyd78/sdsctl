# Home Assistant live scanner audio

Milestone 29.2 adds one first-party Home Assistant custom integration and one
private App service. Together they expose the daemon-owned scanner audio as the
browsable, playable media-source item:

```text
media-source://sdsctl/live
```

The media browser source and its live child use the repository's canonical
`docs/assets/sdsctl-logo.svg` artwork. The packaged integration carries an exact
byte-for-byte SVG copy plus deterministic PNG derivatives under `brand/`.
Home Assistant 2026.3 and newer serves the local square icon and full logo
through its authenticated Brands Proxy API; the top-level Media source therefore
uses the same local brand asset as integration configuration surfaces. The
playable child also carries a bounded one-day signed path to the packaged SVG so
thumbnail requests do not require an exposed bearer token. Browsing never
depends on an external image host.

The integration does not create a `media_player` entity. Select an existing
Home Assistant media-player target that can fetch Home Assistant URLs and decode
a continuous MP3 stream. Output volume and mute remain properties of that target.
Starting or stopping this item does not change scanner volume, squelch, mute,
holds, or tuning.

## Representation and lifecycle

The exact representation is constant-bit-rate MP3 (`audio/mpeg`), 44.1 kHz,
mono, 64 kbit/s. It is live, non-seekable, and has no finite duration. Home
Assistant does not transcode it. A target that cannot consume an indefinite MP3
stream is unsupported and receives no disguised raw PCMU, headerless PCM,
indefinite WAV, or fallback profile.

Each resolution creates a one-time Home Assistant playback identifier that
expires after 30 seconds if it is not used. The player receives only the
resulting Home Assistant-owned URL. Home Assistant authenticates that URL with
its normal bounded signed-path mechanism. The URL contains no App hostname,
Ingress identifier, Supervisor token, scanner address, bridge key, or App
capability.

When the signed URL is redeemed, Core requests a separate one-time 30-second App
capability that is bound to the exact Core origin, peer, method, and stream path.
The App and integration require protocol version 1 and the exact MP3 format
above. Redirects, version or format mismatches, stopped or missing Apps, invalid
credentials, and capacity exhaustion fail closed. Neither side scans the LAN or
falls back to a public listener.

The App allows at most four simultaneous playback leases. They share one
daemon-owned encoder and decoded-PCM subscription. Each lease has a bounded
128,000-byte queue; a slow client drops old encoded data rather than blocking
RTP reception or another consumer. A lease closes after 15 seconds without its
first encoded byte, 15 seconds of encoded-audio inactivity, four hours of total
duration, client disconnect, App/Core restart, integration unload, or pipeline
failure. Releasing the last lease stops the shared encoder while leaving the
daemon and its other audio consumers running.

Initial playback latency includes scanner audio availability, MP3 encoder
startup, Home Assistant proxying, target connection, and target buffering. It
is target-dependent and is not a whole-home synchronization guarantee. Physical
HAOS acceptance records representative latency rather than promising a fixed
value before target evidence exists.

Finalized scanner recordings remain ordinary WAV files in Home Assistant local
media. They are separate finite artifacts and do not use this live source.
Browser audio also remains a separately started App Ingress client.

## Artifact identity and deliberate installation

The custom integration is packaged at version `0.1.5`. The App never installs,
updates, activates, reloads, restarts, or removes it during normal startup. Every
filesystem mutation below is an explicit authenticated Ingress action with an
exact SHA-256 confirmation. The panel reports that Home Assistant Core was not
restarted or reloaded.

Home Assistant Core may create regular `__pycache__/*.pyc` files after loading
the integration. The lifecycle validates and size-bounds that runtime cache but
excludes it from the source-artifact digest, so a normal Core import does not
make an exact installation appear modified. Symlinks, nested cache directories,
and non-bytecode cache files remain invalid.

On Home Assistant OS, open the one exact published or deliberately named Local
sds200 App and choose **Web UI > Home Assistant > Live-audio integration**. Do not
install a Docker client, disable Advanced SSH protection, or give another App
host-container access. The protected Advanced SSH App does not provide the
host's general-purpose Docker CLI, and this workflow does not require it.

The **Home Assistant** workspace and its lifecycle API are selected by the
authenticated request surface, not merely by where sdsctl is installed. They
exist only on the Supervisor-proxied Ingress listener. A separate directly
exposed or authenticated-LAN web listener—even when it runs on the same Home
Assistant host—must use the ordinary six-pane dashboard and does not register
the integration lifecycle or bridge-key routes. Direct clients are rejected by
the Ingress listener itself.

The panel displays the packaged, installed, rollback, and bridge-key identities
without displaying the private bridge key. Its controls use the same compact
geometry as the other dashboard workspaces. When the complete lifecycle form is
taller than the available Ingress viewport, only this pane scrolls; the final
Core-restart and reauthentication guidance remains reachable at its end. For a
first installation:

1. verify the displayed packaged version and digest;
2. choose **Use packaged** to place that exact digest in the confirmation field;
3. choose **Install**; and
4. verify that the same version and digest appear under **Installed**.

The destination is
`/homeassistant/custom_components/sdsctl`, the App's existing mapped Home
Assistant configuration directory. Installation uses a private same-filesystem
stage, complete readback, and atomic publication. It refuses symlinks, an
unexpected destination shape, an existing integration, or an unconfirmed
digest.

Restart Home Assistant Core explicitly after installation. From an Advanced SSH
terminal, the supported command is:

```bash
ha core restart
```

Then open
**Settings > Devices & services > Add integration**, choose
**sdsctl live scanner audio**, and enter:

- the exact internal App DNS alias;
- private port `8100`;
- the bridge key returned by the panel's explicit **Reveal key** action.

The key remains concealed by default. **Show** reveals it and **Copy** writes it
to the browser clipboard. The dashboard clears the key from its document after
60 seconds, when the page becomes hidden, or when it unloads. The response is
`no-store`. Do not paste the key into logs, MQTT, dashboard YAML, entity state,
filenames, issue reports, screenshots, or diagnostics.

### Bridge-key rotation

To rotate the credential, verify the displayed non-secret **Bridge-key digest**,
choose **Use bridge key**, and then choose **Rotate key**. Confirm the browser
warning by choosing the relabeled **Confirm rotate key** action before
continuing. This confirmation is rendered inside the App so it remains visible
when Home Assistant embeds the dashboard in its restricted Ingress frame.

The action atomically replaces the persistent mode-`0600` key and returns the
new key only in that authenticated, `no-store` response. It deliberately does
not reload either running process. Copy the replacement and restart the sdsctl
App immediately so its private service loads the replacement, then complete the
Home Assistant integration's reauthentication prompt with the new key. Existing
outstanding App capabilities remain bounded to at most 30 seconds, and an App
restart closes active streams.

Home Assistant App network aliases use `{REPO}_{SLUG}` with underscores changed
to hyphens for DNS. For example, a Local App named `local_sds200` uses
`local-sds200`. A published repository uses its Supervisor-assigned repository
identifier. Read the installed App/container identity and convert it; do not
copy a repository hash from documentation or another installation. The config
flow contacts only the exact alias entered by the operator.

## Update, rollback, and removal

Refresh and inspect the Ingress lifecycle panel before every action.

An update requires the new packaged digest and retains the previous complete
integration as one private same-filesystem rollback image. Choose **Use
packaged**, then **Update**, and verify both the new installed identity and the
retained rollback identity.

Restart Core explicitly. If validation fails, choose **Use rollback**, then
**Rollback**, and restart Core again. The displaced current image becomes the
new rollback image, so the swap is reversible.

For removal, first delete the **sdsctl live scanner audio** config entry through
Home Assistant. Then choose **Use installed**, choose **Remove**, and confirm the
in-page warning by choosing the relabeled **Confirm remove** action.

Restart Core. `remove` moves the exact current directory into the rollback slot;
it does not permanently delete it. Restore it with `rollback`, or permanently
delete only that retained image by choosing **Use rollback**, **Discard
rollback**, and then the separately relabeled **Confirm discard rollback**
action.

Only `discard-rollback` is permanent. It never targets the Home Assistant
configuration root or any component other than the retained sdsctl rollback
directory. Editing the exact SHA-256 confirmation clears any armed destructive
action, so the operator must review the identity before beginning the two-step
confirmation again.

The `python -m sds200.home_assistant_integration_lifecycle` command remains
available for standalone development containers and direct diagnostic shells.
It provides the same `artifact`, `status`, `install`, `update`, `rollback`,
`remove`, `discard-rollback`, `bridge-key`, and `rotate-bridge-key` operations.
It is not the HAOS Advanced SSH workflow.

## Playback and automations

Browse **Media > sdsctl > Live scanner audio**, or call the standard media
player action:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.REPLACE_ME
data:
  media_content_id: media-source://sdsctl/live
  media_content_type: audio/mpeg
```

The physical VLC-TELNET acceptance target played this canonical `audio/mpeg`
action successfully. It also accepted `music` as a target-specific service media
type while Home Assistant continued to resolve the item to the integration's
exact `audio/mpeg` representation. Prefer the canonical example above unless a
target's integration documents a narrower service media type.

Stop with the target's normal `media_player.media_stop` action. Pause and resume
are target-dependent; this integration does not advertise seeking, duration, or
an unbounded pause buffer. When a target cannot pause a live MP3 stream safely,
use stop and start a new playback instead.

The target must be able to reach the Home Assistant internal or external URL
that Home Assistant selects for it. It does not need, and must not be given,
network access to the private App port. If URL selection fails, configure Home
Assistant's internal/external URL settings for the target network rather than
exposing port 8100. When those settings are declared under `homeassistant:` in
`configuration.yaml`, validate the configuration and restart Core before
retesting; changing the file alone does not update already-issued playback URLs.

Diagnostics contain only the configured App alias/port, negotiated application
and protocol versions, exact MIME type, and low-rate playback counts. They omit
the bridge key, App capability tokens, Home Assistant playback identifiers,
signed URLs, audio payloads, and packet-rate telemetry.

## Milestone 29.2 development acceptance

The first physical Home Assistant OS acceptance slice completed on August 30,
2026. The amd64 host ran Home Assistant OS 18.2, Core 2026.8.3, Supervisor
2026.07.5, Frontend 20260729.7, and Docker 29.7.2 with a physical SDS200 running
firmware 1.26.01. The deliberately named Local validation App ran build
`0.24.0-m29.2.2a1dbcd`; the installed custom integration was version `0.1.4`
with artifact SHA-256
`231ce7c58addb2c838512a2df1eddfbab870f580d0275520b3f33c69a2f863c6`.
The retained `0.1.3` integration remained available as the rollback image.

Home Assistant Core 2026.8 supplies `None` as the root media-source identifier.
The initial `0.1.3` integration rejected that valid root with `Unknown sdsctl
media item.` Version `0.1.4` accepts both the documented null root and the empty
root while continuing to reject null resolution and arbitrary child identities.
After the deliberate update and Core restart, the physical run confirmed:

- **Media > sdsctl** rendered the canonical repository artwork and browsed to
  **Live scanner audio** without the prior error;
- `media_player.play_media` resolved `media-source://sdsctl/live` as
  `audio/mpeg`, drove the existing VLC-TELNET target to `playing`, and produced
  audible scanner audio through the configured local output;
- the stream remained live while scanner state continued updating. App evidence
  contained only occasional isolated one-packet RTP gaps of 320 samples and no
  live-audio service, proxy, encoder, or authentication failure;
- the normal `media_player.media_stop` action returned the target to `idle` and
  reset its media position to zero, while the App remained `started` on the
  exact validation build with no update pending; and
- neither the Home Assistant playback action nor stop changed scanner volume,
  squelch, mute, hold, tuning, daemon ownership, or the single RTSP/RTP source.

This slice validates one representative target and clean target-side teardown.
Post-restart diagnostics then proved that a second playback URL was issued and
redeemed without rejection or expiry, but also exposed one remaining active
Core proxy lease after VLC-TELNET had returned to `idle`. The `0.1.5` candidate
therefore adds an explicit downstream-transport closing check between bounded
MP3 chunks; that path closes the upstream App response and releases the Core
lease exactly once in regression coverage.

That physical revalidation completed on August 31, 2026 with integration
candidate `0.1.5`, artifact SHA-256
`3a9919a0701d5cf7e4e696b5a5de4b1eedd3b38a34b747908a8f105ced65fcec`, and
Local App build `0.24.0-m29.2.49463f3`. A remote VLC-TELNET target on the HAOS
LAN received the standard `media-source://sdsctl/live` action with both the
canonical `audio/mpeg` media type and the compatible `music` alternate, and
produced audible scanner audio through headphones. Its first silent attempt
exposed an incorrect Home Assistant `internal_url`; sanitized evidence records
the unreachable origin as `https://192.0.2.18` and the reachable HAOS listener
as `http://192.0.2.18:8123`. After that value was corrected and Core restarted,
the target retrieved the Home Assistant-owned URL and played normally without
access to the private App listener.

After `media_player.media_stop`, the target stopped and the App remained
`started`. Fresh redacted integration diagnostics recorded exactly one issued
and one redeemed playback after the first run, then exactly two issued and two
redeemed playbacks after the canonical `audio/mpeg` run, with zero active and
outstanding leases, zero rejected or expired playbacks, and an open integration
lifecycle. This closes the `0.1.5`
downstream-transport cleanup gate. The retained `0.1.4` rollback was left intact
until the remainder of Milestone 29.2 acceptance is complete.

The same validation run exercised the Ingress-safe destructive confirmation
before updating. The operator armed and confirmed **Discard rollback** against
the exact retained `0.1.3` digest, and the action completed without a
browser-native dialog. Updating to `0.1.5` then retained the exact `0.1.4` bytes
as the new rollback image. Readback found no integration symlinks and no change
outside the managed current and rollback directories.

Final regression then exercised the surrounding system rather than inferring
compatibility from media playback alone:

- browser audio played audibly and returned to its stopped state;
- daemon recording started, finalized, appeared in inventory, played audibly,
  downloaded, and remained available after an App restart;
- channel hold, next, previous, and release completed without losing the scanner
  connection;
- waterfall data advanced, paused, resumed, cleared, and repopulated. A few
  isolated scanner-command timeouts reached at most two consecutive failures and
  recovered; after the App restart the display reported 2.9 fps, zero queue
  loss, zero overflows, and zero current poll failures;
- Home Assistant retained the complete 24-component MQTT Discovery device: 17
  state/diagnostic entities and seven controls. Optional fields remained
  mode-dependent as documented;
- all nine verification-card articles rendered after the restart with zero
  configuration errors, seven online markers, and live daemon state; and
- the Local App restarted without restarting Core, returned to `started`,
  preserved its recording and bridge boundary, and served another audible
  canonical `audio/mpeg` playback.

The final redacted diagnostic reported three issued and three redeemed
playbacks, zero active and outstanding leases, zero rejected or expired
playbacks, and an open integration lifecycle. No authentication, live-audio,
proxy, encoder, recording, or MQTT failure appeared in the bounded post-restart
logs.

Only one VLC-TELNET target was reachable during the final run; the second
configured target was unavailable. The roadmap requires two simultaneous
physical playback leases only when the available target set permits, so the
test was not simulated against one target. Automated concurrent-consumer and
shared-encoder coverage remains the evidence for that boundary, and this run
does not claim physical two-target synchronization or concurrency.

With that explicit environmental limit, the Milestone 29.2 implementation and
physical acceptance gates are complete. Closure cleanup deleted the integration
config entry; discarded the managed current and rollback integration copies;
uninstalled the Local validation App; and removed the three exact validation
share artifacts. The published `sds200` App version 0.24.0 started before the
single required Core restart and resumed sole scanner ownership. Its Ingress
dashboard returned connected, all nine verification-card articles rendered
without configuration errors, and all 11 existing WAV recordings remained in
the persistent media directory. Supervisor's remaining local source-catalog
metadata reports no installed version and an unknown state, rather than an
installed or running Local App. No bridge key, playback identifier, signed URL,
scanner programming, audio payload, or private host identity is retained here.
