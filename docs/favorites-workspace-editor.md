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

## Optional RadioReference preview

The editor can be configured with one reviewed RadioReference observation
request. Configuration is passive: launching and browsing the editor does not
resolve credentials, contact RadioReference, or perform a refresh. The
application key and account password are accepted only through the names of
environment variables; never put their values on the command line.

For example, configure one subcategory-frequency dataset like this:

```bash
export RR_APP_KEY='private application key'
export RR_PASSWORD='private account password'

sdsctl favorites edit \
  --copied-tree /absolute/path/to/favorites_lists \
  --radioreference-preview \
  --radioreference-username account-name \
  --radioreference-application-key-env RR_APP_KEY \
  --radioreference-password-env RR_PASSWORD \
  --radioreference-provenance /absolute/private/path/provenance.json \
  --radioreference-dataset subcategory-123 \
  --radioreference-operation getSubcatFreqs \
  --radioreference-parameter scid=123
```

The four reviewed observation operations and their exact ordered integer
parameters are:

- `getSubcatFreqs`: `scid`;
- `getCountyFreqsByTag`: `ctid`, then `tag`;
- `getAgencyFreqsByTag`: `aid`, then `tag`;
- `getTrsTalkgroups`: `sid`, `tgCid`, `tgTag`, then `tgDec`.

The dataset identifier is an operator-selected stable identity for that exact
request. The provenance argument must name one canonical absolute private file.
Missing provenance is represented as missing; configuring or viewing a preview
does not create the file.

Choose `Refresh RadioReference preview`, or press `Ctrl+G`, to perform exactly
one bounded provider read. Only one refresh may run at a time. Each action first
re-reads the selected copied tree or freshly qualifies and reads the selected
USB source. The provider is not contacted unless that fresh snapshot exactly
matches the editor's durable baseline and no unreviewed in-memory edits exist.

A successful result shows the provider and dataset, request times, observation
times and revisions, all `added`, `replaced`, `removed`, `unchanged`,
`local_only`, and `conflict` counts, and every exact record and field preview.
Record details retain the local filename/index target or `unmapped`, opaque
external record ID, ownership, local value, external value, explicit absence,
and fields with no reviewed scanner mapping. Empty result sets remain valid and
still identify the configured provider and dataset.

The read-only production path was operator-qualified on 2026-08-28 with
`getTrsTalkgroups` for public RadioReference system `12042` and zero-valued
category, tag, and decimal filters. That statewide response required more than
the decoder's former 20,000-element ceiling, used content-free `xsi:nil` for
optional subfleet and slot evidence, and returned nested tag references without
descriptions. The decoder now preserves those live absences explicitly while
retaining independent document, element, reference, depth, DTD/entity, namespace,
and schema guards. This qualification does not claim live coverage for the
other three reviewed observation operations.

This surface is read-only. It never turns a preview into an acceptance decision,
write plan, Favorites write, or provenance publication. Rename, duplicate,
delete, undo, reset, successful write/reload, and source changes invalidate the
last result. Refresh is disabled while edits exist. A failed or cancelled read
retains the previous successful result unless it is already stale, and errors
use stable redacted classes without provider response text, request bodies,
environment values, or credential material. Closing the app requests
cancellation; an already-running bounded transport still owns and closes its
ephemeral session deterministically.

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
container creation/deletion/reordering, writable FTP, RadioReference acceptance
or synchronization, live-scanner GLT/FQK mutation, daemon or web access, Home
Assistant Ingress, or background synchronization. Use only copied data you can
recover or scanner media for which the verified backup and rollback artifacts
are acceptable.
