# Advanced Home Assistant App access

The Home Assistant App can optionally serve authenticated `sdsctl` clients and
a native HTTPS dashboard to devices on a trusted private LAN. This is intended
for arrangements such as one Home Assistant-hosted daemon feeding several
Raspberry Pi displays without opening another scanner connection.

This mode is optional. A normal Home Assistant installation should leave both
advanced switches off and both optional TCP Network mappings disabled. Home
Assistant Ingress remains the recommended default dashboard.

## What each port is for

The two advanced services are independent:

| App container port | Use | Intended client | Authentication |
| --- | --- | --- | --- |
| `50443/tcp` | encrypted daemon-client protocol | `sdsctl daemon-client`, TUI, or a future compatible client | one client ID and secret per device, server certificate, and observe/control scopes |
| `8443/tcp` | native HTTPS dashboard | an ordinary browser or kiosk | server certificate and a dedicated dashboard password |

UDP `50000` has a different purpose: it carries RTP audio from the physical
SDS200 to the App. It is not a dashboard or remote-client port.

One daemon still owns the scanner, PSI stream, Waterfall session, audio session,
MQTT state, and recordings. Remote clients consume the daemon's bounded shared
services. Closing or disconnecting one display does not stop another display or
an active recording.

## Security boundary

Home Assistant Supervisor publishes an App Network mapping on the Home Assistant
host; this App cannot restrict that publication to one selected host interface.
Before enabling either mapping:

- use a trusted private LAN, not a guest, public, or untrusted shared network;
- allow the chosen port only from the intended client addresses or subnet in the
  host or upstream firewall;
- do not create a router port-forward or expose either service to the Internet;
- do not place either service behind an unreviewed reverse proxy; and
- give each daemon client a unique identity, starting with `observe` access.

Use the [native Docker deployment](remote-container-deployment.md) instead when
an exact private host-interface bind is required. Public access, wildcard trust,
and trusted reverse-proxy operation are not supported by this Home Assistant
mode.

The advanced credential workspace is available only through authenticated Home
Assistant Ingress. A directly opened native dashboard deliberately has no Home
Assistant tab, bridge-key controls, Core-integration lifecycle routes, or
advanced credential routes.

## Choose the private addresses

Two values have deliberately different jobs:

- **Advanced access host address** is the literal private or link-local IP
  address of the Home Assistant host. Downloaded daemon-client profiles connect
  to this address. Do not enter the scanner address, a hostname, a public IP, or
  `0.0.0.0`.
- **Advanced access server name** is the private identity encoded in the TLS
  certificate. Use the same private IP address, a single-label local name, a
  `.local` name, or a `.home.arpa` name. A name must resolve to the Home Assistant
  host on every client.

For the simplest initial test, use the same literal private Home Assistant IP in
both fields. A stable private DNS name is more convenient when correctly
configured on the whole LAN.

Never put a real private address, certificate, key, client secret, or password
in an issue, screenshot, log excerpt, or source-controlled file.

## Enable a service safely

The first setup intentionally takes two App restarts. This prevents a usable
listener from appearing before its private credentials exist.

### 1. Configure the App and Network mapping

Open the App's **Configuration** page.

For remote CLI or TUI clients:

1. set **Advanced remote daemon clients** to on;
2. enter the literal **Advanced access host address**;
3. enter the **Advanced access server name**; and
4. assign a host port to `50443/tcp` in the **Network** section. Keeping host
   port `50443` makes client instructions easiest, but another unused TCP port is
   valid.

For a native browser or kiosk dashboard:

1. set **Advanced native HTTPS dashboard** to on;
2. enter the **Advanced access server name**; and
3. assign a different host port to `8443/tcp` in the **Network** section.

The two enabled services must use distinct host ports. A disabled service must
also have its Network mapping disabled. Save and restart the App. On this first
restart the normal daemon and Ingress dashboard remain available, while an
advanced listener with incomplete credentials is explicitly withheld.

If the App rejects startup, correct the reported option/mapping mismatch. Do not
work around it by opening a wildcard listener or weakening authentication.

### 2. Create the private material in Ingress

Open the App with **Web UI**, select **Home Assistant**, and find **Advanced
remote access**.

1. Verify that the intended enable state and host ports are shown.
2. Initialize the server identity with the exact confirmation `INITIALIZE`.
   Later identity rotations require the displayed current certificate SHA-256
   as confirmation.
3. If the native dashboard is enabled, choose **Create or rotate** for its
   password and confirm the action. Save the password immediately.
4. If the remote daemon is enabled, enter a unique client ID. Use ASCII letters,
   digits, `.`, `_`, and `-`; start with a letter or digit.
5. Select **Observe** unless that exact device and operator should issue scanner
   controls. **Control** adds hold/release, navigation, volume, squelch, and
   reconnect authority.
6. Issue the client and immediately download or copy all three artifacts:
   `remote-client.toml`, `server.crt`, and `client.secret`.

One-time passwords and client secrets exist only in page memory. They clear
after 60 seconds, when **Clear** is selected, when the page is hidden, or when
the page is left. The server private key is never offered for download.

### 3. Restart the App to activate the listener

Restart the App once more. Confirm that it starts normally and that the enabled
advanced service is reachable only from an intended private-LAN client.

Creating, rotating, revoking, or restoring a client after the remote listener is
running reloads the credential registry without releasing scanner ownership.
Every reload closes **all existing remote-client sessions**, including sessions
belonging to unchanged clients. Their credentials remain valid and they can
reconnect; this is not an uninterrupted per-client connection change. Plan for
a brief interruption on other remote displays when managing any client.
The first client, a server identity rotation, a native-dashboard password
rotation, and option or Network mapping changes require an App restart.

## Configure a Raspberry Pi TUI client

Install the TUI and local playback support on Raspberry Pi OS or another Linux
display client:

```bash
python3 -m venv ~/.venvs/sdsctl
~/.venvs/sdsctl/bin/python -m pip install --upgrade pip
~/.venvs/sdsctl/bin/python -m pip install "sds200[tui,playback]"
```

Raspberry Pi OS and Debian normally also need the operating-system PortAudio
runtime for local speakers or headphones:

```bash
sudo apt update
sudo apt install libportaudio2
```

The downloaded profile contains safe placeholder paths below
`/etc/sdsctl/remote/CLIENT_ID`. Either install the files at those exact absolute
paths or edit both file paths in the profile to private absolute paths owned by
the display user. The credential must be readable only by that user:

```bash
chmod 700 ~/.config/sdsctl
chmod 600 ~/.config/sdsctl/client.secret
```

Install or merge the downloaded profile as:

```text
~/.config/sdsctl/daemon-remote-clients.toml
```

The selected profile name is the exact client ID. Verify status and a short
event stream before opening the TUI:

```bash
sdsctl daemon-client --remote-profile CLIENT_ID status
sdsctl daemon-client --remote-profile CLIENT_ID events --count 2 --json
sdsctl tui --daemon-client --remote-profile CLIENT_ID --audio-playback
```

Successful client output uses the fixed label `sdsctl-remote-daemon`; it does
not display the private endpoint, identity, file paths, credential, scanner
address, or private server error details.

Give every additional Pi, workstation, automation, or future GUI its own client
ID and secret. This makes one device independently revocable without interrupting
the others. An observe-only display cannot issue scanner controls even if its
local interface renders control affordances incorrectly.

Once this interactive workflow passes for a Raspberry Pi, continue with the
[managed Raspberry Pi display guide](managed-pi-display.md) for an opt-in,
boot-resilient `/dev/tty1` service. Managed displays enforce observe-only
authorization and do not enable this App's listener or Network mapping.

## Configure a native browser or kiosk

Open this URL from a private-LAN browser, substituting the certificate identity
and selected host port:

```text
https://ADVANCED_SERVER_NAME:HOST_PORT/
```

Trust the downloaded `server.crt` in the kiosk operating system or browser as a
local certificate before treating the deployment as accepted. A browser warning
is not an authentication substitute. Sign in with the one-time dashboard
password saved during setup.

The native dashboard is intentionally an ordinary `sdsctl` dashboard. It must
not expose Home Assistant-specific management even though the same App also
serves an Ingress dashboard.

For an unattended Pi display, configure the operating system's normal kiosk or
service manager only after interactive access works. Keep the password outside
command lines, desktop launchers, repository files, screenshots, and logs. The
browser session cookie is memory-only and must not be copied between devices.

## Rotate, revoke, restore, or disable

- **Rotate one daemon client:** select its client ID, explicitly rotate it,
  replace only that device's `client.secret`, and keep the newly downloaded
  profile and certificate together. The prior credential stops working after
  the live reload.
- **Revoke one daemon client:** select **Revoke** for that ID. Other identities
  remain authorized but must reconnect after the registry reload. The App will
  not revoke the last active identity while the remote daemon is enabled.
- **Restore a client:** select **Restore**. Its retained credential becomes valid
  again after reload; rotate it instead when its secrecy is uncertain.
- **Rotate the server identity:** confirm with the exact current certificate
  SHA-256, download the new public certificate to every intended client, and
  restart the App. Old certificates no longer authenticate the server.
- **Rotate the native-dashboard password:** save the new one-time value and
  restart the App. Existing native sessions must not be treated as proof that a
  newly rotated password is active.
- **Disable a service:** turn off its App option, disable its exact Network
  mapping, save, and restart. Verify the host port is closed. Disabling a
  listener does not delete retained private lifecycle state.

Do not delete `/data/advanced-access` by hand. A future explicit removal flow
must enumerate exact retained targets and require separate approval before
permanent deletion.

### Roll back to the safe default

If advanced acceptance fails, disable each advanced App option and its exact
matching Network mapping, save, and restart the App. Confirm that both TCP host
ports are closed and that the Ingress dashboard, scanner state, MQTT entities,
audio, Waterfall, and recordings still work. This is the supported operational
rollback: it removes the listeners without restoring an expired password,
retired certificate, or replaced client secret.

Keep the disabled private lifecycle files for a later retry. Permanently erasing
them requires removal of the App's data and is a separate destructive Home
Assistant operation; review Home Assistant's exact removal target and retained
recordings before approving it. Never use a broad recursive shell command as a
shortcut.

## Troubleshooting

### The App will not start after configuration

Read the App log. The most common cause is an enabled switch without its matching
Network host port, or a disabled switch whose host port is still assigned. The
App also rejects identical remote and dashboard host ports.

### The App starts, but the listener is withheld

This is expected after the first configuration restart. Use the authenticated
Ingress lifecycle panel to create the identity plus at least one active daemon
client or the native-dashboard password, then restart the App.

### A daemon client reports TLS or identity failure

Confirm that `address` in the client profile is the literal private Home
Assistant host IP, `server_hostname` exactly matches the certificate identity,
the client trusts the current `server.crt`, and the DNS name resolves to that
same host when a name is used. Do not disable certificate checking.

### A client cannot connect

Confirm the App is running, the intended Network mapping has a host port, the
firewall permits the client, and no router or proxy rewrites the connection.
Then verify that the exact client ID is active and that `client.secret` is the
latest one-time value with mode `0600`.

### The native dashboard shows Home Assistant management

Stop using that endpoint and report a security issue. Home Assistant lifecycle
controls are valid only inside the authenticated Ingress context.

### One Pi should control while the others only display

Issue unique observe-only identities to the display Pis and a separate
observe-and-control identity to the operator station. Do not share a control
credential among displays.

## Acceptance checklist

Before relying on a multi-display deployment, verify all of the following:

- the default state publishes neither optional TCP port;
- each enabled port is reachable only from its intended private client scope;
- one daemon remains the only scanner owner;
- two simultaneous clients receive current state without opening another scanner
  connection;
- observe-only clients cannot issue controls;
- a control-authorized client can perform one harmless bounded control and leave
  the scanner in its original state;
- revoking one client blocks only that identity;
- browser audio, Waterfall, and recordings remain daemon-owned shared services;
- the native dashboard has no Home Assistant management surface; and
- App restart produces fresh client state and automatic bounded reconnection;
  and
- rollback closes both optional host ports while ordinary Ingress operation
  remains healthy.

See [Authenticated remote daemon clients](daemon-remote.md) for the protocol,
queue, disclosure, and failure contracts, and [Home Assistant App](home-assistant-app.md)
for the complete App architecture and normal Ingress workflow.
