# Favorites Workspace editor

The optional local Textual editor is the first interactive surface over the
renderer-neutral Favorites Workspace and verified write contracts introduced in
Milestones 21 and 22. It never opens scanner control, a daemon connection, FTP,
GLT, or FQK. Install the TUI extra before launching it:

```bash
python -m pip install "sds200[tui]"
```

## Choose one explicit source

Open an offline copied `favorites_lists` directory:

```bash
sdsctl favorites edit --copied-tree /absolute/path/to/favorites_lists
```

On Linux, open an already-mounted scanner USB volume by naming its mount point,
`BCDx36HP` directory, or `favorites_lists` directory:

```bash
sdsctl favorites edit --usb /absolute/path/to/scanner-mount
```

The USB form performs the existing fresh mountinfo, writable-mount, block-device,
USB-ancestry, path, and exact-snapshot qualification. It does not discover or
select a target implicitly. By default, durable USB write artifacts use the
private `favorites-usb-writes` directory below the application's XDG state
directory. An operator may instead provide one canonical absolute host path:

```bash
sdsctl favorites edit \
  --usb /absolute/path/to/scanner-mount \
  --usb-host-state /absolute/private/host/path
```

The verified USB executor rejects state beneath scanner media and creates or
checks private host directories with its existing ownership and permission
rules. `--usb-host-state` is invalid with `--copied-tree`.

## Browse and edit

The left side contains the eight-kind hierarchy navigation and a complete exact
source-record browser. The search field filters hierarchy names. Selecting a
hierarchy or source record shows its kind or command, exact filename and source
index, provenance path where available, and preserved raw record text. Schema
diagnostics and exact-plan status remain visible throughout the session.

Only these operations are available:

- replace an evidence-backed Name Tag on a supported catalog or hierarchy
  record;
- delete a supported HPD leaf record;
- duplicate a supported HPD leaf after itself, using its complete current raw
  record as the exact template and optionally replacing its supported Name Tag.

The all-record browser makes supported site-frequency and band-plan leaf
operations available even though those records are not separate nodes in the
eight-kind hierarchy navigation. Unsupported commands, structural records,
ambiguous sources, stale selections, unsafe names, and schema-invalid created
records are rejected by the existing record-editing layer.

Every accepted edit produces another immutable in-memory intended snapshot.
`Undo` restores one prior snapshot. `Reset` discards the entire in-memory edit
history. Closing the application also discards unexecuted edits. There is no
autosave and the renderer never replaces files itself.

## Review, confirm, and execute

`Review exact plan` recomputes `plan_favorites_write()` from the immutable
baseline and intended snapshots. The screen reports exact record changes,
comparison ambiguities, schema/write blockers, and a deterministic confirmation
token bound to the source, baseline, and intended bytes.

Execution is a separate action. Copy the full token into the confirmation field
and choose `Execute confirmed plan`. Any intervening edit, undo, or reset changes
or removes the plan and makes the old token invalid. A blocked or no-op plan
cannot execute.

The renderer delegates execution only to
`execute_favorites_copied_tree_write()` or `execute_favorites_usb_write()`.
Those executors repeat stale-target and safety preflight, perform their existing
verified backup/staging/readback/rollback workflow, and emit durable operation
artifacts. The editor displays the target, operation ID, backup directory,
rollback manifest, operation report, recovery status on failure, and whether a
fresh exact reload matched the reviewed intended snapshot. The reloaded snapshot
becomes the new baseline only after exact equality is verified.

## Deliberate limits

The editor does not expose arbitrary positional fields, hierarchy or catalog
container creation/deletion/reordering, writable FTP, RadioReference
synchronization, live-scanner GLT/FQK mutation, daemon or web access, Home
Assistant Ingress, or background synchronization. Use only copied data you can
recover or scanner media for which the verified backup and rollback artifacts
are acceptable.
