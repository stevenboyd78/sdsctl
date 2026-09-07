# Experimental managed-browser server configuration

Status: **unreleased candidate, isolated validation only—not a production setup
procedure**. Use a reviewed development build, not the published v0.29.4 command
set. The ordinary TUI, USB connection, manual-login kiosk and Home Assistant App
defaults are unchanged. Do not enable this on an existing production App as part
of following the released installation guide.

This step connects the existing browser-device authority to the actual web
command and App launch plan. An **authority** is the private database that records
which browser displays may sign in. It is separate from dashboard passwords,
Home Assistant accounts and remote-TUI credentials.

## What must exist first

A reviewer must prepare an isolated Linux server account, a **precreated** browser
authority and a working native HTTPS dashboard. Authority creation is a separate,
explicit lab preparation step using the candidate command below; web/App startup
has no initialize, repair, import, reset or migration option. A missing database
is an error, never an invitation to start over. Keep every existing authority
and its owner lock for review.

The native dashboard still requires its existing operator-password file and TLS
certificate/key. Managed devices do not use that password. A separate manual
display password remains optional. No new TCP listener or port mapping is added:
managed sessions use the same explicitly enabled native HTTPS listener.

Both server processes must run as the same Linux account and see the same
authority path. The Home Assistant App already provides that shared filesystem
and account. This boundary trusts that account and root; it is not protection
against a compromised server account.

## Prepare a new lab server without starting it

The unreleased `browser-device-server --experimental create` command prepares
only an **empty** authority and its private configuration. It does not enroll a
display, generate a password/certificate, contact Home Assistant, publish a port,
start a web process or install a service. Both origins below are configuration
choices, not proof that HTTPS is working at those addresses.

Run as the Linux account that will run the server. Files belong to that account;
the command does not change owners. Unlike the browser client, a server running
as root inside an App container may prepare its files as root there. Do not use
`sudo` on an ordinary server if its web processes run as another account.

First select a **new** lab parent directory and create it with private permissions.
For the example account `scanner`:

```sh
mkdir -m 700 /home/scanner/browser-server-lab
```

If that directory already exists, stop and inspect it or select a different new
lab location. Do not delete an existing setup or loosen file permissions.

For an isolated Ingress-capable server, substitute your reviewed origins and
administrator ID in this example. The repeated `0123...` value is a placeholder,
not an account to authorize:

```sh
sdsctl browser-device-server --experimental create \
  --directory /home/scanner/browser-server-lab/prepared \
  --native-origin https://192.168.1.10:8443 \
  --ingress-origin https://ha.example.test \
  --admin-user-id 0123456789abcdef0123456789abcdef
```

Repeat `--admin-user-id` for each explicitly authorized Home Assistant user
(1–32 unique IDs). For a standalone native-only lab, replace `--ingress-origin`
and **all** `--admin-user-id` options with `--native-only`. One of those two
administration choices is required; neither is inferred. Native-only preparation
does not supply a standalone enrollment workflow and cannot enable the App's
Ingress administration. No shared password or TUI credential belongs in these
arguments.

The command creates a new mode-`0700` `prepared` directory with just two
mode-`0600` files: `authority.sqlite` and `server.json`. Every existing destination
is refused, including an empty directory or this command's own previous output.
It validates input before writing, creates files exclusively and publishes
`server.json` last. There is no overwrite, repair, resume or cleanup option.

An interrupted or failed preparation retains any created files and reports that
completion could not be confirmed. Do not rerun creation over that directory or
delete it to suppress an error. A final write/sync failure can leave readable
files despite an unconfirmed result; retain them for review. A read-only check
does not establish that an interrupted filesystem write survived a power loss.

## Check an existing server offline

```sh
sdsctl browser-device-server --experimental check \
  --server-config /home/scanner/browser-server-lab/prepared/server.json
```

This reads the same private JSON/database as web startup. It accepts both an
empty authority and a used authority containing active, paused or revoked
devices. No records, credential generations, owner locks or saved state are
changed; unsafe/missing/corrupt files are refused, not repaired. It can also
check a previously prepared configuration at another absolute private path.

Success means **offline configuration validity**, not a running server, a
reachable origin, trusted browser/helper TLS or a successfully enrolled display.
These commands deliberately do not load the ordinary scanner configuration or
configure log files, even if global configuration/logging options were supplied.
No scanner, browser or network operation is performed. Output is redacted and
does not list administrator IDs, device records or private configuration values.
Exit status is `0` for success, `78` for a preparation/check failure and `2` for
invalid or incomplete command syntax.

## The private configuration file

Keep the JSON file and database as mode `0600` regular files, each in an existing
mode `0700` directory owned by the server account. Use absolute paths with no
symlinks or hard links. Do not loosen permissions to resolve a validation error.
The database must be the existing version-1 authority in its original SQLite
DELETE-journal format; other schemas and WAL mode are refused, not converted.

Example **shape only**, with illustrative addresses and a placeholder user ID:

```json
{
  "version": 1,
  "authority_path": "/data/browser-devices/authority.sqlite",
  "native_origin": "https://192.168.1.10:8443",
  "ingress_admin": {
    "origin": "https://192.168.1.10:8123",
    "user_ids": ["0123456789abcdef0123456789abcdef"]
  }
}
```

An **origin** is the scheme, host and optional port, without a page path. These
two origins often differ:

- `native_origin` is the HTTPS address the browser displays connect to. It must
  match the native listener's configured public origin, including any published
  nondefault port.
- `ingress_admin.origin` is the HTTPS Home Assistant frontend address used by the
  administrator. It is **not** the native dashboard address or the App's internal
  HTTP Ingress endpoint. Home Assistant must actually serve HTTPS at the example
  address for that value to work; adding `https://` to an HTTP address is not a
  TLS setup procedure.
- `user_ids` contains the exact Home Assistant user IDs explicitly authorized to
  administer enrollment—not display names, usernames, passwords or access
  tokens. Each ID is 32 lowercase hexadecimal characters. Supply 1–32 unique
  IDs. Do not copy the placeholder into a deployment.

Use canonical HTTPS origins: lowercase DNS names, no trailing slash, no path,
query, fragment or embedded credentials, and omit the default `:443` port.
Private IPv4, bracketed IPv6 (for example `https://[fd12::10]:8443`) and DNS names
are supported. Internal DNS and a proxy are **not required**. Both the browser
and native helper must independently trust the certificate for the exact host;
this feature neither installs trust nor bypasses certificate errors.

Unknown or duplicate JSON fields, partial settings, unsafe files and invalid
authority records are rejected with redacted errors. The file contains no
password or device credential, but remains private because it selects an
authorization boundary. Never put enrollment downloads into this configuration.

For a native-only lab without Ingress administration, use `"ingress_admin": null`.
That configuration cannot enable an administrator endpoint. The Home Assistant
App candidate requires the explicit administrator configuration above.

## Standalone web command

Append both options to the already reviewed native HTTPS web command:

```text
--experimental-browser-devices --browser-device-config /absolute/private/server.json
```

Neither option is accepted alone. The complete command must still use
`web --authenticated-lan` with its existing `--lan-*` password, address, public
origin and TLS settings. The new options do not enable ordinary loopback HTTP
or generic container-exposure mode for managed devices. They do not connect
directly to the scanner: the web process still uses the existing daemon sockets.

On the App's separate `web --home-assistant-ingress` process, the same pair of
options enables **only administration**. The App constructs these child commands;
do not expose a hand-started Ingress listener to the LAN or an unrelated proxy.
The raw TCP peer must be Supervisor's `172.30.32.2`, and its authenticated user-ID
header must be on the allowlist. Forwarded IP headers and operator cookies do
not confer administrator access.

Only the native web process owns managed sessions. It must acquire the exclusive
owner lock and start the private acknowledgement socket before accepting HTTP
requests. A second owner fails startup. Use one native process per authority,
not a multi-worker deployment. Shutdown drains requests before releasing
ownership; it retains the lock file. Do not delete that file while troubleshooting.

## Home Assistant App candidate

The two new App options default to:

```yaml
experimental_browser_devices_enabled: false
browser_device_server_config: ""
```

For an **approved isolated candidate**, enable the boolean and set the path to
the precreated private server JSON inside the App container. The existing native
dashboard option, explicit Network mapping, native identity and operator password
must already be prepared. A config file on a workstation is not automatically
available at that path inside the App.

App preflight validates the browser configuration, existing authority, native
public origin, administrator settings and prepared native identity/password
before runtime writes, MQTT credential discovery or child startup. Each web
child also validates the file independently. Invalid configuration fails the App
launch; it does not silently start with weaker authentication or a new authority.
The options are reconciled with Supervisor state. No configuration parser adds
port mappings, firewall rules, DNS, proxies, trust, credentials or services.

Configuration is a startup snapshot. Stop and restart both web processes together
(an App restart for the packaged case) after an approved configuration change;
there is no hot reload of origins or administrator IDs. Do not replace or move
the authority while either process is running. To disable the experiment, clear
the path and disable the boolean together, retaining the authority for review.
This disables managed-browser access; it does not disable separately configured
manual login, TUI access or native port publication.

## Enrollment and client handoff

An allowed administrator can open the browser-device link in the Ingress API
index at `/api/v1`. Enrollment still requires a same-origin POST, one-time form
nonce and explicit device-name confirmation. Its credential is returned only as
a private, non-cacheable download—not in the inventory or page HTML. A repeated
POST cannot redownload it. Protect that attachment and use the matching
[first-time profile import](browser-device-profile.md),
[bundle/registration](browser-device-first-run.md) and
[managed startup](browser-device-startup.md) review steps.

Pausing or revoking authority is not the same as confirming all old requests have
closed. The existing administration page reports the native owner's bounded
acknowledgement; unavailable, pending or superseded results are not a completed
shutdown. Retry confirmation of the exact record, not enrollment or rotation.
Automatic replacement and explicit client-resume installation workflows remain
separate work. Do not reimport a fresh profile to erase a saved pause/error.

## Validation and remaining gates

Tests cover disabled defaults, paired options, safe file handling, schema and
verifier corruption, read-only preflight, native/Ingress origin separation,
raw-peer enforcement, one-time issuance, device-only routes, revocation and
duplicate-owner refusal through the CLI-created applications. App tests cover
both generated child commands, Supervisor reconciliation and early failure.

The installed-command namespace audit also passed IPv4, DNS and IPv6 on the
workstation, with ordinary certificate verification, separate native/Ingress
processes, a confirmed cross-process revoke, native restart/session invalidation
and duplicate-owner refusal. It exposed an automatic-lifespan fallback; the
candidate now requires ASGI lifespan and returns a nonzero startup-failure exit.
See the [repeatable audit](../scripts/experimental/README.md#installed-server-command-wiring).

The preparation candidate extends that audit to create the empty authority
through the installed CLI instead of an internal Python call, check it before
enrollment and after revocation without modification, and refuse repeated
creation without replacing its contents. Unit tests additionally cover competing
creators, interrupted preparation, unsafe targets, shared parser rejection and
read-only preservation of all three device states.

These tests use synthetic authority and no live scanner. They are not physical
Pi, power-outage, browser-only missing-trust or production App acceptance.
Distribution/update/removal, explicit replacement/resume, managed browser service
policy, conservative crash-lock handling and combined server/display cold-start
acceptance remain gates in the
[enrollment design](managed-display-enrollment-design.md). Do not upgrade the
production TUI displays or publish a release based on this wiring alone.
