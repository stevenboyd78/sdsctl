# Home Assistant live scanner audio

Milestone 29.2 adds one first-party Home Assistant custom integration and one
private App service. Together they expose the daemon-owned scanner audio as the
browsable, playable media-source item:

```text
media-source://sdsctl/live
```

The media browser source and its live child use the repository's canonical
`docs/assets/sdsctl-logo.svg` artwork. The packaged integration carries an
exact byte-for-byte copy and serves it only through an authenticated Home
Assistant route; browsing never depends on an external image host.

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

The custom integration is packaged at version `0.1.1`. The App never installs,
updates, activates, reloads, restarts, or removes it during normal startup. Every
filesystem mutation below is an explicit authenticated Ingress action with an
exact SHA-256 confirmation. The panel reports that Home Assistant Core was not
restarted or reloaded.

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
warning before continuing.

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
browser warning.

Restart Core. `remove` moves the exact current directory into the rollback slot;
it does not permanently delete it. Restore it with `rollback`, or permanently
delete only that retained image by choosing **Use rollback**, **Discard
rollback**, and confirming the separate browser warning.

Only `discard-rollback` is permanent. It never targets the Home Assistant
configuration root or any component other than the retained sdsctl rollback
directory.

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

Stop with the target's normal `media_player.media_stop` action. Pause and resume
are target-dependent; this integration does not advertise seeking, duration, or
an unbounded pause buffer. When a target cannot pause a live MP3 stream safely,
use stop and start a new playback instead.

The target must be able to reach the Home Assistant internal or external URL
that Home Assistant selects for it. It does not need, and must not be given,
network access to the private App port. If URL selection fails, configure Home
Assistant's internal/external URL settings for the target network rather than
exposing port 8100.

Diagnostics contain only the configured App alias/port, negotiated application
and protocol versions, exact MIME type, and low-rate playback counts. They omit
the bridge key, App capability tokens, Home Assistant playback identifiers,
signed URLs, audio payloads, and packet-rate telemetry.
