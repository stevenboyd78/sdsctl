# Scanner display profiles: local administrator workflow

Status: **development candidate, not available in the published release yet**.
These commands connect the Mimic-SDS import engine to the standalone daemon.
They do not install a Mimic theme/card or provide a browser upload button.
Home Assistant App option wiring and administrator-only browser Upload/Refresh
remain separate work; do not add these fields to the installed App's options.

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
No new daemon event or automatic renderer refresh is implemented in this slice:
clients using the new read operation see the accepted revision on their next
request. Existing WebUI/TUI/HA renderers do not consume it yet.

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
administration. The browser Upload/Refresh interface must add an explicit
administrator boundary and guarded source staging before it can use this flow.
See the [Mimic-SDS work packet](mimic-sds-work-packet.md) for the remaining scope.
