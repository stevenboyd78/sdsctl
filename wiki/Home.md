# sdsctl Wiki

`sdsctl` is an alpha Python library and toolkit for controlling and monitoring
Uniden SDS100, SDS150, and SDS200 scanners. It provides a command-line tool,
terminal interface, responsive web dashboard, Home Assistant App, typed Python
API, Favorites Workspace, and daemon-based automation services.

The project is not affiliated with or endorsed by Uniden. It is not a
safety-critical or emergency-dispatch system.

## Start here

1. [Choose an installation](Installation).
2. Complete [First connection](First-Connection).
3. [Choose an interface or task](Using-sdsctl).
4. Open [Troubleshooting](Troubleshooting) if expected success evidence does
   not appear.

## Choose your target

| I want to… | Start with… |
| --- | --- |
| Run inside Home Assistant OS | [Home Assistant](Home-Assistant) |
| Use a terminal UI on Linux or Raspberry Pi OS | [Python installation](Installation#install-from-pypi), then [Using sdsctl](Using-sdsctl#terminal-interface) |
| Run a browser dashboard or MQTT service | [Server-oriented Python installation](Installation#choose-an-installation) |
| Run all optional Python interfaces | `python -m pip install "sds200[all]"` |
| Deploy with Docker or Podman | [Containers](Containers) |
| Use only the CLI or Python library | [Base Python installation](Installation#install-from-pypi) |
| Edit Favorites or import selected RadioReference data | [Favorites and RadioReference](Favorites-and-RadioReference) |
| Develop an integration | [Python API](Python-API) |

The `all` extra includes the TUI, web, MQTT, and local-playback Python
dependencies. It does not install operating-system packages, Home Assistant,
containers, audio services, or FFmpeg.

## Supported scanners

| Model | USB control | Native Ethernet control | RTSP/RTP audio |
| --- | --- | --- | --- |
| SDS100 | Yes | No | No |
| SDS150 | Yes | No | No |
| SDS200 | Yes | Yes | Yes |

SDS200 USB, Ethernet control, and network audio have been validated on physical
firmware 1.26.01. SDS100 core USB behavior has also been hardware-validated on
firmware 1.26.01. SDS150 support follows the shared SDS-series command
specification and still awaits physical validation. Read the
[supported-models matrix](https://github.com/stevenboyd78/sdsctl/blob/main/docs/supported-models.md)
for the exact evidence and limits.

## Common tasks

- [Discover and connect to a scanner](First-Connection)
- [Use the CLI, TUI, web dashboard, daemon, MQTT, controls, or themes](Using-sdsctl)
- [Play live SDS200 audio and make recordings](Audio-and-Recordings)
- [Install and operate the Home Assistant App](Home-Assistant)
- [Edit Favorites and review assisted RadioReference changes](Favorites-and-RadioReference)
- [Configure profiles, logging, health, capture, replay, and recovery](Operations-and-Diagnostics)
- [Use the Python API](Python-API)
- [Deploy with Docker or Podman](Containers)
- [View the web theme and Waterfall gallery](Web-Dashboard)

## Security boundaries

The SDS200 network-control protocol is unauthenticated and unencrypted. Keep it
on a trusted LAN or use a secured VPN. Do not forward scanner UDP port `50536`
to the Internet.

The default native web dashboard is loopback-only. Its supported remote-LAN
mode requires an explicit private-interface bind, password authentication,
native TLS, and a browser-trusted certificate. Home Assistant uses its separate
authenticated Ingress boundary. Reverse-proxy and public anonymous exposure are
not implied by either mode.

Favorites data, RadioReference credentials and payloads, scanner addresses,
recordings, capture files, Home Assistant bridge keys, and local diagnostics can
be private. Sanitize complete artifacts before sharing them.

## Advanced reference

- [Supported models](https://github.com/stevenboyd78/sdsctl/blob/main/docs/supported-models.md)
- [Control transports](https://github.com/stevenboyd78/sdsctl/blob/main/docs/transports.md)
- [Layered configuration](https://github.com/stevenboyd78/sdsctl/blob/main/docs/configuration.md)
- [Daemon deployment](https://github.com/stevenboyd78/sdsctl/blob/main/docs/daemon-deployment.md)
- [Daemon API](https://github.com/stevenboyd78/sdsctl/blob/main/docs/daemon-api.md)
- [Network audio](https://github.com/stevenboyd78/sdsctl/blob/main/docs/audio.md)
- [Web dashboard](https://github.com/stevenboyd78/sdsctl/blob/main/docs/web-dashboard.md)
- [Home Assistant App](https://github.com/stevenboyd78/sdsctl/blob/main/docs/home-assistant-app.md)
- [Favorites Workspace](https://github.com/stevenboyd78/sdsctl/blob/main/docs/favorites-workspace-editor.md)
- [Capability and field-parity audit](https://github.com/stevenboyd78/sdsctl/blob/main/docs/capability-field-parity-audit.md)
- [Advanced protocol research](https://github.com/stevenboyd78/sdsctl/blob/main/docs/advanced-protocol-research.md)

## Project information

- [Latest release](https://github.com/stevenboyd78/sdsctl/releases/latest)
- [Changelog](https://github.com/stevenboyd78/sdsctl/blob/main/CHANGELOG.md)
- [Roadmap](https://github.com/stevenboyd78/sdsctl/blob/main/ROADMAP.md)
- [Project vision](https://github.com/stevenboyd78/sdsctl/blob/main/docs/project-vision.md)
- [Contributing](https://github.com/stevenboyd78/sdsctl/blob/main/CONTRIBUTING.md)
- [Support policy](https://github.com/stevenboyd78/sdsctl/blob/main/SUPPORT.md)
- [Security policy](https://github.com/stevenboyd78/sdsctl/blob/main/SECURITY.md)

## Getting help

Review [Troubleshooting](Troubleshooting) first. Reproducible bugs and feature
requests belong in [GitHub Issues](https://github.com/stevenboyd78/sdsctl/issues).
Include the `sdsctl`, Python, and operating-system versions, scanner model and
firmware, connection type, exact command, expected result, and complete
sanitized error. Use the private reporting path in the security policy for a
suspected vulnerability.
