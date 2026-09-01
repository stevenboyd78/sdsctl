# Audio and Recordings

SDS200 network audio is separate from USB or Ethernet scanner control. The
scanner supports one RTSP/RTP audio session, so use the daemon when multiple
local interfaces need to share audio.

## Prerequisites

- An SDS200 reachable on the trusted local network
- The `playback` extra for local speakers or headphones
- A working PortAudio runtime for default Python playback

On Debian or Raspberry Pi OS:

```bash
sudo apt update
sudo apt install libportaudio2
python -m pip install "sds200[playback]"
sdsctl audio-devices
```

## Play live audio

```bash
sdsctl --host SCANNER_IP audio --play
```

Stop with `Ctrl+C`. Select another output with `--device DEVICE`; use
`sdsctl audio-devices` to inspect available names and indexes.

## Record a WAV file

Record 30 seconds without FFmpeg:

```bash
sdsctl --host SCANNER_IP audio \
  --output scanner-audio.wav \
  --duration 30
```

Omit `--duration` to record until `Ctrl+C`. Existing files are protected. Only
use `--force` when replacing that exact output is intentional.

Playback and recording can share the same scanner audio session:

```bash
sdsctl --host SCANNER_IP audio \
  --play \
  --output scanner-audio.wav
```

A successful recording is an 8 kHz, mono, signed 16-bit PCM WAV file. Review
the completion summary for packet loss, duplicates, late or malformed packets,
and RTP timestamp discontinuities.

## Share daemon-owned audio

When the daemon owns the scanner, use daemon clients instead of starting a
second direct audio session:

```bash
sdsctl daemon-client audio --play
sdsctl daemon-client audio \
  --output scanner-audio.wav \
  --duration 30
```

The web dashboard, daemon-backed TUI, and Home Assistant App also share the
daemon-owned stream. Closing one client must not stop the daemon or another
client.

## Home Assistant audio

The App dashboard provides explicitly started browser audio and finalized
recordings. The optional Core integration separately provides the standard
`media-source://sdsctl/live` item for a compatible Home Assistant media player.
See [Home Assistant](Home-Assistant) and the
[live-audio guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/home-assistant-live-audio.md).

## Streaming adapters

Broadcastify-compatible streaming needs FFmpeg with `libmp3lame`. PipeWire,
PulseAudio, and ALSA command adapters need their corresponding `pw-cat`,
`pacat`, or `aplay` executable. These programs are operating-system packages;
the `sds200[all]` Python extra does not install them.

Read the [audio architecture and configuration guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/audio.md)
before enabling a persistent destination.

## If audio fails

Use [Troubleshooting](Troubleshooting#sds200-network-audio-will-not-start) for
RTSP reachability and [local playback failures](Troubleshooting#local-playback-fails)
for PortAudio or device problems. Local audio that works while a remote Home
Assistant player stays silent usually indicates a target-reachability or Home
Assistant URL configuration problem rather than an SDS200 audio failure.

