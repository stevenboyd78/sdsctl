# Home Assistant live scanner audio

Milestone 29.2 adds one first-party Home Assistant custom integration and one
private App service. Together they expose the daemon-owned scanner audio as the
browsable, playable media-source item:

```text
media-source://sdsctl/live
```

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

The custom integration is packaged at version `0.1.0`. The App never installs,
updates, activates, reloads, restarts, or removes it during normal startup. Every
filesystem mutation below is an explicit terminal action with exact SHA-256
confirmation. The command reports that Home Assistant Core was not restarted or
reloaded.

On Home Assistant OS, first open an Advanced SSH terminal and list the running
containers so you can select the one exact published or deliberately named Local
sds200 App:

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
```

Set `APP_CONTAINER` to that exact container name. Do not use a broad match if
more than one sds200 App exists.

```bash
APP_CONTAINER='REPLACE_WITH_EXACT_CONTAINER_NAME'
docker exec "$APP_CONTAINER" \
  python -m sds200.home_assistant_integration_lifecycle artifact
docker exec "$APP_CONTAINER" \
  python -m sds200.home_assistant_integration_lifecycle status
```

Copy the displayed packaged `digest=` value exactly, then install:

```bash
INTEGRATION_DIGEST='REPLACE_WITH_EXACT_PACKAGED_SHA256'
docker exec "$APP_CONTAINER" \
  python -m sds200.home_assistant_integration_lifecycle install \
  --confirm "$INTEGRATION_DIGEST"
```

The destination is
`/homeassistant/custom_components/sdsctl`, the App's existing mapped Home
Assistant configuration directory. Installation uses a private same-filesystem
stage, complete readback, and atomic publication. It refuses symlinks, an
unexpected destination shape, an existing integration, or an unconfirmed
digest.

Restart Home Assistant Core explicitly after installation. Then open
**Settings > Devices & services > Add integration**, choose
**sdsctl live scanner audio**, and enter:

- the exact internal App DNS alias;
- private port `8100`;
- the bridge key printed by the explicit command below.

```bash
docker exec "$APP_CONTAINER" \
  python -m sds200.home_assistant_integration_lifecycle bridge-key
```

The bridge-key command prints the credential only to the invoking terminal. Do
not paste it into logs, MQTT, dashboard YAML, entity state, filenames, issue
reports, screenshots, or diagnostics.

### Bridge-key rotation

The same command also prints a non-secret `digest=` of the current key. To rotate
the credential, copy that digest exactly and run:

```bash
CURRENT_KEY_DIGEST='REPLACE_WITH_REPORTED_CURRENT_SHA256'
docker exec "$APP_CONTAINER" \
  python -m sds200.home_assistant_integration_lifecycle rotate-bridge-key \
  --confirm "$CURRENT_KEY_DIGEST"
```

The rotation command atomically replaces the persistent mode-`0600` key and
prints the new key once to the invoking terminal. It deliberately does not
reload either running process. Restart the sdsctl App immediately so its private
service loads the replacement, then complete the Home Assistant integration's
reauthentication prompt with the new key. Existing outstanding App capabilities
remain bounded to at most 30 seconds, and an App restart closes active streams.

Home Assistant App network aliases use `{REPO}_{SLUG}` with underscores changed
to hyphens for DNS. For example, a Local App named `local_sds200` uses
`local-sds200`. A published repository uses its Supervisor-assigned repository
identifier. Read the installed App/container identity and convert it; do not
copy a repository hash from documentation or another installation. The config
flow contacts only the exact alias entered by the operator.

## Update, rollback, and removal

Inspect before every lifecycle action:

```bash
docker exec "$APP_CONTAINER" \
  python -m sds200.home_assistant_integration_lifecycle status
```

An update requires the new packaged digest and retains the previous complete
integration as one private same-filesystem rollback image:

```bash
NEW_DIGEST='REPLACE_WITH_NEW_PACKAGED_SHA256'
docker exec "$APP_CONTAINER" \
  python -m sds200.home_assistant_integration_lifecycle update \
  --confirm "$NEW_DIGEST"
```

Restart Core explicitly. If validation fails, copy the exact
`rollback_digest=` from `status` and swap the retained version back:

```bash
ROLLBACK_DIGEST='REPLACE_WITH_REPORTED_ROLLBACK_SHA256'
docker exec "$APP_CONTAINER" \
  python -m sds200.home_assistant_integration_lifecycle rollback \
  --confirm "$ROLLBACK_DIGEST"
```

Restart Core again after rollback. The displaced current image becomes the new
rollback image, so the swap is reversible.

For removal, first delete the **sdsctl live scanner audio** config entry through
Home Assistant. Then copy the exact `current_digest=` from `status` and perform
the recoverable removal:

```bash
CURRENT_DIGEST='REPLACE_WITH_REPORTED_CURRENT_SHA256'
docker exec "$APP_CONTAINER" \
  python -m sds200.home_assistant_integration_lifecycle remove \
  --confirm "$CURRENT_DIGEST"
```

Restart Core. `remove` moves the exact current directory into the rollback slot;
it does not permanently delete it. Restore it with `rollback`, or permanently
delete only that retained image with a separate exact confirmation:

```bash
ROLLBACK_DIGEST='REPLACE_WITH_REPORTED_ROLLBACK_SHA256'
docker exec "$APP_CONTAINER" \
  python -m sds200.home_assistant_integration_lifecycle discard-rollback \
  --confirm "$ROLLBACK_DIGEST"
```

Only `discard-rollback` is permanent. It never targets the Home Assistant
configuration root or any component other than the retained sdsctl rollback
directory.

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
