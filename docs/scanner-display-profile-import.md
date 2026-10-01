# Scanner display profiles: local administrator workflow

Status: **development candidate, not available in the published release yet**.
These commands connect the Mimic-SDS import engine to the standalone daemon.
They do not install a Mimic theme/card. A separate, disabled-by-default browser
Upload/Refresh adapter is implemented in this development branch, as described
below. Explicit App/launcher wiring is implemented in the local candidate; use
the [paired candidate procedure](mimic-sds-candidate-app.md), not guessed fields
in the installed App's options.

## What is stored where?

There are three separate things:

- **`profile.cfg`:** your original scanner display settings. Copy it to an
  administrator-managed location. These commands read it but never rewrite it.
- **The display manifest:** a small TOML file that explicitly selects that source,
  one scanner endpoint and a private state directory. It is not the scanner's
  `profile.cfg`, a connection profile or a systemd unit.
- **Accepted state:** a private, durable record of the last reviewed import. A bad
  edit to the source file does not replace the accepted settings.

The daemon shares a validated display-only projection with clients. Do not copy
the scanner profile or private accepted-state file onto each Pi. A new Pi is a
consumer of the daemon, not another owner of the profile or scanner connection.

## 1. Prepare an explicit manifest

Use absolute paths appropriate to the service account. The configuration/source
can sit beside the service's application configuration; accepted state needs its
own persistent writable directory. Keep both outside recording inventory and
retention directories. The daemon refuses overlapping profile/recording paths.

This example uses reserved documentation IP space and placeholder UUIDs. Replace
them with your selected scanner target and two UUIDs generated **once** for that
endpoint and manual source, for example using `python3 -c 'import uuid; print(uuid.uuid4())'`
twice. Keep the identities across imports and restarts; do not generate new ones
on every launch or reuse one endpoint's private state for another scanner.

```toml
version = 1
endpoint_id = "00000000-0000-0000-0000-000000000001"
source_id = "00000000-0000-0000-0000-000000000002"
scanner_target = "udp://192.0.2.25:50536"
source_path = "/srv/sdsctl/config/profile.cfg"
state_directory = "/srv/sdsctl/state/display-profile"
```

Save this, for example, as `/srv/sdsctl/config/scanner-display.toml`.
`scanner_target` must exactly match the daemon's selected transport endpoint:
the complete `udp://host:port` spelling for network connections, or the selected
serial device path such as `/dev/ttyACM0`. An IP address works; DNS, a proxy and
an additional exposed port are not required. Matching a configured target is a
selection guard, not proof that a manually copied file came from that scanner.

Run management commands as the daemon's service account. The manifest must be a
regular file owned by that account or root and not group/world writable; mode
`0600` is a suitable choice for an account-owned manifest. A root-managed,
read-only manifest/source is also possible if the service account can read it.
Do not grant the daemon root or broaden configuration-directory permissions.

The parent of the selected state directory must already exist and be writable
by the service account. The next command creates only the final directory with
mode `0700`; accepted records use `0600`. Symlinked path components, hard-linked
files and unsafe private permissions are refused, not repaired. Resolve an
intended alias to its real absolute path when configuring it.

## 2. Initialize, preview and import

Initialize **once**, not on each startup:

```bash
sdsctl scanner-display-profile --manifest /srv/sdsctl/config/scanner-display.toml init
```

Confirm the prompt. This creates empty accepted state but does not import a file.
It refuses existing state, including a partially initialized directory; do not
delete or recreate that state to get past an error without investigating it.

Preview the copied profile without changing anything:

```bash
sdsctl scanner-display-profile --manifest /srv/sdsctl/config/scanner-display.toml preview
```

The JSON review contains the validated display descriptor, revision, previous
revision and whether the configured source identity changed. It omits unrelated
scanner settings and filesystem paths. Unknown or incomplete screen fields still
need the separate display-mapping qualification; a successful import is not a
promise that every physical scanner field can already be rendered.

To review and accept the file in one guarded operation:

```bash
sdsctl scanner-display-profile --manifest /srv/sdsctl/config/scanner-display.toml import
```

The command shows a fresh preview and asks for confirmation. If the file changes
while that prompt is open, it refuses the import. The separate `preview` command
does not create a reusable authorization token. For deliberate noninteractive
administration, `init` and `import` support `--yes`; no prompt is silently approved.
A changed source identity also requires `import --confirm-source-change` after
review. A changed descriptor alone does not change the source identity.

## 3. Enable the profile in the daemon

For an approved development installation, add this opt-in argument to the
daemon's existing launch command:

```text
--scanner-display-profile-config /srv/sdsctl/config/scanner-display.toml
```

There is no automatic manifest search. Omitting the option preserves existing
daemon behavior. An opted-in invalid configuration, mismatched target, missing
accepted-state directory or corrupt accepted state fails before scanner startup.
Initialized-but-empty state is valid and reports that nothing has been imported.

The daemon reads the accepted record at startup and caches an immutable snapshot.
Normal client reads perform no profile-file I/O. If the selected transport target
changes, it stops serving the old profile until an explicit successful reload
revalidates the configured target. It does not guess which profile should apply.

## 4. Refresh after changing the source

After replacing `profile.cfg` through your existing administrator file access,
run `import` again. The daemon does not hot-reload an incomplete file copy.
To accept it and then update an already-running local daemon in one workflow:

```bash
sdsctl scanner-display-profile --manifest /srv/sdsctl/config/scanner-display.toml import --daemon-socket-path /run/sdsctl/daemon.sock
```

Use the actual local API socket path configured for your daemon. This is a Unix
socket, not its TCP remote-client port. The command checks the daemon's opaque
endpoint identity before requesting a reload. The combined operation confirms
persistence before notifying the daemon; a notification failure cannot undo the
already accepted import.

To reload an **already accepted** revision without re-importing the source:

```bash
sdsctl scanner-display-profile --manifest /srv/sdsctl/config/scanner-display.toml reload --daemon-socket-path /run/sdsctl/daemon.sock
```

This does not restart the scanner, daemon, Home Assistant Core or Pi displays.
No new daemon push event is introduced: clients using the read operation see
the accepted revision on their next request. The optional
[candidate Mimic-SDS WebUI](scanner-display-frame-api.md#candidate-webui-presentation)
uses the shared live-frame read, as do the local candidate's daemon-backed TUI
and [additional HA card](home-assistant-mimic-card.md). None of this candidate
support has been installed or published.

## 5. Check status and handle failures

```bash
sdsctl scanner-display-profile --manifest /srv/sdsctl/config/scanner-display.toml status
```

This checks disk state and separately reports source-copy status. A daemon client
reading `display.profile` instead gets the last explicitly checked cached view,
including `checked_at`; it must not present that as continuous file monitoring.
Timestamps are timezone-aware UTC in the data, ready for local-time presentation.

- `matches_import`: the source bytes match the accepted copy, not necessarily
  the scanner's current settings.
- Changed/invalid/missing source: accepted settings remain available; investigate
  the copy and explicitly import a valid file when ready.
- Import accepted, daemon reload unconfirmed: inspect status and use `reload`;
  do not repeat the import just to notify the daemon.
- Save outcome unconfirmed or damaged accepted state: preserve files for local
  administrator review. Do not blindly retry initialization or remove state.

The raw accepted copy is base64-encoded, **not encrypted**. Protect and back up
both the source and the private state. Neither is a recording, browser download,
diagnostic attachment or public client payload.

## Authorization boundary

`display.profile` exposes only the validated descriptor and opaque provenance to
authorized daemon observe/control clients when configured. It accepts no path,
upload bytes or other request parameters. `display.profile.reload` is confined
to the existing local API trust boundary: root/the service account and parties
already authorized to access its private Unix socket. Remote transports cannot
invoke it, even if it is accidentally included in a remote operation allowlist.
There is no profile write/import operation on the general daemon API.

Neither native-dashboard operator login nor display-only login grants profile
administration. See the [Mimic-SDS work packet](mimic-sds-work-packet.md) for the
remaining renderer, App configuration and acquisition scope.

## Browser Upload/Refresh development adapter

This is **not a published App option or a native-dashboard operator feature**.
The internal application factory accepts an explicit `ScannerDisplayIngress`
configuration through `scanner_display_admin_ingress`. Without it, the page and
all profile-administration routes do not exist. There is no new exposed port.

The adapter requires all of the following:

- The existing Home Assistant Ingress boundary, with the actual trusted
  Supervisor peer, not a client-supplied forwarded address.
- Exactly one Supervisor-provided user ID, listed in a private, explicitly
  configured administrator allowlist. Ordinary operator/display cookies, user
  names and an unlisted Ingress user do not authorize access.
- An exact HTTPS-facing Home Assistant origin, including its port when needed.
  A valid HTTPS IP origin works too; internal DNS is not required. This origin
  governs browser CSRF checks, not a new listener. Plain HTTP does not satisfy
  this private administrator adapter's origin validation.
- The explicit display manifest, actual recording directory (checked for
  overlap), and matching local daemon socket. No caller selects a path, endpoint
  or filename through HTTP.

Managed uploads additionally require `allow_upload=True` in this private
controller configuration. The default is false, leaving only read-only source
refresh available. For uploads, the selected source's parent must already be a
service-owned `0700` directory; an existing target must be a service-owned `0600`
regular single-link file. A missing target file is allowed. Permissions are
checked, not repaired. Read-only root-managed configuration remains suitable
for the local import/Refresh flow, not managed upload. Do not loosen an entire
configuration or media tree to enable uploads.

The current page is at the Ingress-relative route
`/api/v1/home-assistant/scanner-display-profile`. The factory's API index links
to it only when explicitly configured. The installed dashboard has no new
management tab yet. The local candidate's explicit App/launcher wiring is
described below; no published catalog or installed App has been changed.

### Candidate App and command-line wiring

The local runtime now recognizes one optional `scanner_display_config` App
option. Its empty/absent default leaves all current behavior unchanged. The
option points to a separate, administrator-managed deployment TOML file:

```toml
version = 1
profile_config = "/data/scanner-display.toml"
ingress_origin = "https://ha.example.test"
admin_user_ids = ["0123456789abcdef0123456789abcdef"]
allow_upload = false
```

The user ID above is a placeholder, not an account name or a dashboard
credential. Select the exact Home Assistant administrator IDs permitted to
manage this profile; no wildcard or automatic grant to other Ingress users is
supported. The origin is the HTTPS-facing **Home Assistant** origin, not the
separate native dashboard address. An HTTPS IP address and nondefault port are
also supported, for example `https://192.0.2.18:8123`. This does not provision a
certificate, DNS entry, proxy, listener or port mapping.

`profile_config` points to the existing six-field profile manifest documented
earlier, with its endpoint/source UUIDs, exact scanner target and source/state
paths. For the App, the scanner target must match the configured scanner host
and its default UDP command port. Use the planned managed source location
`/media/sdsctl/profiles/profile.cfg` and a separate private accepted-state
directory under `/data`; do not put either inside the recording inventory.
The deployment and profile manifests must be bounded regular single-link files,
owned by root or the service account and not writable by others. Keep them in
private administrator storage; do not distribute them to remote displays.

`allow_upload = false` permits review/acceptance of the already configured
source but not browser file replacement. Set it to true only when the source
is an explicitly managed upload copy with the private parent/file permissions
described above. The administrator must provision and initialize the private
state deliberately before enabling the candidate. Startup never initializes,
imports, repairs, or chmods profile files. A missing managed source file is
allowed for a first upload; a missing private parent or accepted-state directory
is not.

The App launch plan reads this deployment before creating its runtime files,
checks the target, state, recording separation and managed upload destination,
and passes the underlying profile manifest to its **one existing daemon**.
Only the Ingress web child receives the administrator deployment. The native
web, media and remote-client commands gain no administrator flags or listeners.
After the daemon is ready, the Ingress child verifies its selected local API's
endpoint before serving the page. It does not reload or import during startup.
Configuration changes require a coordinated App restart; this is not a watcher
or a promise of live scanner/profile synchronization.

For an explicitly staged Ingress web process, the equivalent command is:

```bash
sdsctl web --home-assistant-ingress \
  --scanner-display-admin-config /data/scanner-display-deployment.toml \
  --scanner-display-recording-directory /media/sdsctl/recordings \
  --daemon-socket-path /run/sdsctl/daemon.sock
```

The recording root must be the daemon's actual root. Both display-administration
flags are required together. Loopback-only, generic container and native HTTPS
web modes reject this administrator configuration; their normal operator/display
authentication is not Home Assistant administrator authorization.

**Release compatibility gate:** `scanner_display_config` is intentionally absent
from the published catalog's options, schema and translations. Do not add it to
an installed published App's options or publish a catalog-only change: older
images reject unknown fields. A future candidate Local App must pair its schema
with this runtime, and a release must update the runtime, catalog, documentation
and upgrade/rollback checks together. Hardware acceptance has not occurred yet.

### What the administrator does

1. **Check the selected scanner and file.** The page shows the configured target,
   source path, accepted revision and source-copy status.
2. **Preview a selected upload** or **Preview existing file**. A complete upload
   is limited to 1 MiB and held in memory, not written during preview. The file
   picker name is never used as a server path. Refresh reads the selected copy
   without replacing it.
3. **Review and confirm** the normalized display fields and colors. Changing
   source identity requires a separate checkbox. Accept uses the exact one-use
   review; Cancel changes no files. One pending review per endpoint is allowed,
   bound to the administrator who created it, with a five-minute expiry.
4. **Check the outcome.** Saving accepted state and reloading the daemon are
   separate results. If a concurrent change means the daemon loaded a different
   revision, that is reported rather than presented as confirmation of this one.

Closing or restarting the page never imports or resumes a pending action.
Returning to the original page permits its pending review to be accepted or
cancelled; if that page is lost, wait for expiry before beginning a new review.
Server shutdown drops uncommitted in-memory reviews. A request already committing
may finish after the browser disconnects, so a lost response is not a rollback.
The page stops further writes after an unconfirmed response and never retries
an action automatically. Use status and administrator inspection to determine
what happened. A failed daemon notification can be retried with **Reload accepted
state in daemon**, without re-uploading or importing the source again.

### Two-file recovery, not a false atomicity promise

An upload checks that the reviewed source and accepted state have not changed,
validates source-change confirmation, stages a complete private file and
atomically replaces the managed source copy. It then atomically publishes one
accepted document containing the exact bytes, normalized revision and provenance.
Both writes are synced and checked. These are **two file replacements**, not a
single atomic filesystem transaction spanning both paths.

If a failure occurs between them, the source copy may contain the new upload
while the old accepted profile remains authoritative and recoverable. It is
reported as an unconfirmed outcome; preserve both files and inspect status.
The daemon must not adopt the source copy merely because it changed. No automatic
rollback, repeated upload, reinitialization or deletion is performed. A later
explicitly reviewed Refresh can accept a valid copy after administrator review.

Raw content never appears in the page, normal daemon projection, static assets,
or a new download route. The administrator sees the normalized descriptor and
trusted configured path. Local share/backup exposure is still governed by the
deployment's existing filesystem access. The original file chosen on the
administrator's workstation is not changed.

Local tests cover authorization, body bounds, user-bound expiry, source/state
conflicts, private permissions, cancellation and loss of acknowledgement,
daemon-reload failure, and a process exit between source and accepted writes.
Synthetic browser checks cover the review flow and responsive layout. This is
not yet live Home Assistant/Pi acceptance or physical power-loss certification.
