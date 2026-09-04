# Advanced Home Assistant access

Use this optional workflow when one Home Assistant-hosted SDS200 daemon should
feed remote `sdsctl` clients or private-LAN browser displays. For an ordinary
Home Assistant installation, leave both advanced features and both optional TCP
Network mappings disabled and continue using the authenticated App Web UI.

## Choose the client type

| You want to run… | Enable… | App port | Authentication |
| --- | --- | --- | --- |
| `sdsctl daemon-client` or the TUI on a Pi or workstation | Advanced remote daemon clients | `50443/tcp` | unique client secret, certificate, and observe/control scope |
| The web dashboard in an ordinary browser or kiosk | Advanced native HTTPS dashboard | `8443/tcp` | certificate and dedicated dashboard password |

You may enable either or both. They are different protocols, so a TUI cannot use
the dashboard port and a browser cannot use the daemon-client port. UDP `50000`
is scanner-to-App audio and is not a client service.

## Before enabling a port

This mode is for a trusted private LAN. Home Assistant Supervisor publishes an
enabled App port on the Home Assistant host instead of limiting it to one
selected interface.

- Limit the host port to the intended client addresses or subnet with a
  firewall.
- Never port-forward it through the Internet router.
- Do not use a public address, a wildcard address, or an unreviewed reverse
  proxy.
- Start display devices with **Observe** access. Grant **Control** only where
  scanner control is intentional.

Use the [container deployment](Containers#remote-daemon-and-thin-clients) when
you require exact private-interface binding on an ordinary Linux host.

## First setup

### 1. Set the App options

Open the sds200 App **Configuration** page.

For daemon clients, turn on **Advanced remote daemon clients**, set **Advanced
access host address** to the literal private IP of the Home Assistant host, and
enable the `50443/tcp` Network mapping with an unused host port.

For a browser dashboard, turn on **Advanced native HTTPS dashboard** and enable
the `8443/tcp` Network mapping with a different unused host port.

Set **Advanced access server name** for either service. The simplest value is
the same literal private Home Assistant IP. You may instead use a private
single-label, `.local`, or `.home.arpa` name that every client resolves to that
host.

Save and restart the App. It is normal for an enabled advanced listener to be
withheld on this first restart because credentials do not exist yet. The usual
Ingress Web UI remains available.

### 2. Create credentials inside the Web UI

Open **Web UI > Home Assistant > Advanced remote access**.

1. Confirm the displayed feature states and ports.
2. Initialize the server identity with `INITIALIZE`.
3. For a native dashboard, create its password and save the one-time value.
4. For a daemon client, enter a unique ID such as `pi-display`, select Observe
   or Control, and issue it.
5. Immediately download all three client files: `remote-client.toml`,
   `server.crt`, and `client.secret`.

The password and client secret disappear after 60 seconds, when cleared, or when
the page is hidden or left. The App never offers its server private key for
download.

### 3. Restart the App again

Restart the App to activate the first listener. Verify that the selected host
port is reachable from an intended private-LAN device and blocked from any
unintended network.

## See which remote clients are connected

Open the App's **Web UI > Diagnostics > Connected remote clients**. Each row
shows a client ID, its Observe/Control access, the services it is using, the
number of open connections, and the age of its oldest current connection.
For example, one Pi can use separate event and audio connections but appears as
one client. Give each display its own credential to identify it separately.

The list refreshes every five seconds while Diagnostics is visible. It lists
live authenticated remote-daemon sessions, not every enrolled credential. Browser
and Home Assistant Ingress sessions are not included. A lost network connection
may remain listed until transport failure is detected; an unavailable inventory
is shown as unavailable, not as an empty list.

This inventory is available only through authenticated Home Assistant Ingress
and the local daemon operator API. It is not available on the native dashboard
or to remote Observe/Control clients. It contains no secrets, certificates,
private paths, or peer addresses.

## Silent network interruptions

Remote TCP connections use keepalive on both ends. Where supported, probes start
after 10 idle seconds, repeat every 5 seconds, and allow 3 unanswered probes.
Linux also applies a 20-second TCP user timeout to unacknowledged data. These
per-connection settings let the existing reconnect policy respond to a silently
broken link; they do not require scanner activity or change global networking.
Other operating systems retain defaults for tuning options they do not support.

A healthy, quiet connection can remain open indefinitely. This is transport
liveness, not a promise of fresh scanner data or an exact end-to-end recovery
deadline. Detection, reconnect attempts, and the managed service's restart delay
all contribute to recovery time. See the [Linux TCP documentation](https://man7.org/linux/man-pages/man7/tcp.7.html)
for keepalive and user-timeout semantics.

## Raspberry Pi TUI display

On Raspberry Pi OS:

```bash
python3 -m venv ~/.venvs/sdsctl
~/.venvs/sdsctl/bin/python -m pip install --upgrade pip
~/.venvs/sdsctl/bin/python -m pip install "sds200[tui,playback]"
sudo apt update
sudo apt install libportaudio2
```

Install or merge `remote-client.toml` at:

```text
~/.config/sdsctl/daemon-remote-clients.toml
```

The profile contains absolute certificate and credential paths below
`/etc/sdsctl/remote/CLIENT_ID`. Put the files there, or edit both paths to
absolute locations owned by the display user. Protect the secret:

```bash
chmod 700 ~/.config/sdsctl
chmod 600 ~/.config/sdsctl/client.secret
```

Replace `CLIENT_ID` below with the exact issued ID:

```bash
sdsctl daemon-client --remote-profile CLIENT_ID status
sdsctl daemon-client --remote-profile CLIENT_ID events --count 2 --json
sdsctl tui --daemon-client --remote-profile CLIENT_ID --audio-playback
```

Give each Pi or workstation its own ID. Revoking one identity then leaves the
other displays working.

After this interactive command passes with an **Observe** identity, the
[managed Raspberry Pi display](Raspberry-Pi-Display) guide turns the same
profile into an opt-in `/dev/tty1` systemd service. Complete interactive
validation before enabling automatic startup.

## Browser or kiosk display

Open:

```text
https://ADVANCED_SERVER_NAME:HOST_PORT/
```

Trust the downloaded `server.crt` in that device's operating system or browser,
then sign in with the saved native-dashboard password. Do not bypass a browser
certificate warning as a permanent configuration.

The direct dashboard must not display the Home Assistant management tab or any
bridge-key, integration, or advanced-credential controls. Those controls exist
only inside Home Assistant-authenticated Ingress.

## Routine lifecycle

- Issuing, rotating, revoking, or restoring a client reloads a running remote
  credential registry without releasing scanner ownership. Each reload closes
  **all existing remote-client sessions**, including other clients' sessions.
- The first client, server identity changes, dashboard password changes, App
  option changes, and Network mapping changes require an App restart.
- Rotating one client requires replacing only that device's secret.
- Revoking one client denies that identity. Other identities remain authorized
  and can reconnect with their existing credentials, but their open connections
  are interrupted by the reload. Plan for a brief remote-display interruption.
- A revoked managed display stops after authentication fails. After restoring
  its access, run its preflight and explicitly restart its service; it does not
  retry permanent authentication failures indefinitely. Restoring an unrotated
  client does not require replacing its existing secret.
- To disable a service, turn off its option and disable its matching Network
  mapping, then save, restart, and verify the host port is closed.

If a test fails, roll back by disabling both advanced options and both optional
TCP mappings, then restart. Confirm the ports are closed and the normal Ingress
dashboard still works. Old identities and secrets are deliberately not restored
automatically.

Do not delete the App's `/data/advanced-access` directory manually. Permanently
erasing it requires the separate destructive Home Assistant App-data removal
workflow; review its exact target and retained recordings first. Keep real
addresses, passwords, client secrets, certificates, and private keys out of
screenshots, messages, issue reports, and repositories.

The complete security, setup, troubleshooting, and acceptance reference is the
[advanced Home Assistant App guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/home-assistant-advanced-access.md).
