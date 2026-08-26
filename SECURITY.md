# Security Policy

## Supported versions

Until the first stable release, security fixes are applied to the default branch
and the newest published prerelease only.

| Version | Supported |
| --- | --- |
| Default branch | Yes |
| Latest prerelease | Yes |
| Older development snapshots | No |

## Scanner LAN protocol security

The SDS200-only virtual serial network protocol uses unauthenticated,
unencrypted UDP traffic. Anyone who can reach the scanner's control port may be
able to send commands or observe responses.

- Keep the scanner on a trusted LAN.
- Use firewall rules to limit access.
- Use a secured VPN for remote access.
- Do not forward UDP port `50536` directly from the public Internet.
- Treat traces and debug logs as potentially sensitive.
- Do not embed public scanner addresses or private network credentials in issues.

Implemented SDS200 network audio remains separate from control transport, but
its RTSP negotiation over TCP and RTP delivery over UDP are likewise
unauthenticated and unencrypted. Keep the default RTSP TCP port `554` and the
negotiated RTP receive port on the same trusted LAN or secured VPN. Do not expose
either protocol directly to the public Internet. The Home Assistant App's fixed
UDP `50000` mapping is a packaging-specific RTP receive-port boundary, not
protocol authentication or encryption.

## Remote-provider credentials

Broadcastify currently documents ordinary-HTTP Icecast source ports. Source and
metadata Basic credentials therefore cross the assigned provider endpoint
without transport encryption. `sdsctl` requires an explicit per-profile
acknowledgement before constructing either credential-bearing transport. That
acknowledgement records acceptance of the risk; it does not add TLS, verify a
TLS endpoint, or provide confidentiality.

Revoking the saved acknowledgement blocks future construction from that profile
but cannot mutate an already-running worker. Remove its daemon destination and
reload, or stop the daemon, to end an active source and metadata transport.

Keep the source password in an environment-backed secret reference, use only
the endpoint assigned by Broadcastify, and do not place the resolved credential
in application arguments, configuration, logs, traces, captures, or issues. See
the [saved remote-audio profile guidance](docs/audio.md#saved-remote-audio-destination-profiles)
for safe legacy migration, acknowledgement, and revocation.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose users or enable
unauthorized scanner control.

Use GitHub private vulnerability reporting when it is enabled for the
repository. Otherwise, contact the maintainer through the GitHub profile and
request a private reporting channel without posting exploit details publicly.

Please include:

- Affected version or commit
- Transport and platform
- Impact
- Reproduction steps or proof of concept
- Suggested mitigation, when known
- Whether the report may be credited publicly

You should receive an acknowledgment when the report is reviewed. Because this
is a volunteer project, no guaranteed response or remediation time is promised.

## Safety notice

This project is not designed or certified for emergency, life-safety, dispatch,
or public-warning use. Do not rely on it as the sole means of receiving urgent
communications.
