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

The latest successful, current refresh retains its exact result and lifecycle
for assisted planning. A later successful refresh replaces and closes that
owner. A failed or cancelled replacement keeps the previous successful result
unless it is already stale. Rename, duplicate, delete, undo, reset, successful
write/reload, and source changes invalidate the result, discard its dependent
decisions, and close its retained lifecycle. Refresh remains disabled while
unreviewed editor changes exist. Errors use stable redacted classes without
provider response text, request bodies, environment values, or credential
material. Closing the app requests cancellation and closes the retained owner;
an already-running bounded transport still owns and closes its ephemeral session
deterministically.

## Make an assisted synchronization plan

Explicit decisions beneath the current preview compose an exact aggregate plan
in memory. The panel labels every newly composed result `UNEXECUTED` and reports
the number of decisions, unresolved supported choices, exact Favorites-byte
changes, intended provenance changes, and blockers.

Enter the preview record index shown by the current result. For a linked
conventional `C-Freq` or trunked `TGID` record, enter one of the reviewed mapped
fields and choose:

- `Use external` to accept the displayed external value and external ownership;
- `Keep local` to preserve the exact local bytes and make that field locally
  owned; or
- `Detach field` to preserve the exact local bytes and remove existing external
  ownership for that field.

The only accepted field names are `name`, `frequency`, and
`talkgroup_decimal`. `frequency` applies only to `C-Freq`; `talkgroup_decimal`
applies only to `TGID`. No arbitrary field, tone, mode, hierarchy, or fallback
mapping is inferred.

For an unbound added provider record, choose `Ignore added`, or first select a
compatible existing `C-Freq` or `TGID` row in the hierarchy and choose `Prepare
import after selected template`. Preparation shows the provider record, exact
insertion anchor and derived target, template command, reviewed bindings, and
complete resulting raw record while keeping the proposal explicitly `NOT
ADOPTED`. Only `Adopt prepared import` adds the reviewed proposal to the decision
set. The selected row is the insertion anchor and exact record template, while
only its reviewed Name and frequency or decimal fields are replaced with the
provider values and bound to the provider observation. The planner does not
invent Favorites hierarchy.

For a removed or otherwise linked record, choose the applicable explicit record
action: `Delete removed`, `Keep local record`, or `Detach record`. Keep-local and
detach preserve the Favorites bytes while changing intended ownership;
deletion changes the intended Favorites snapshot. `Clear assisted decisions`
returns to the unchanged refresh baseline.

Each action recomputes one renderer-neutral result from the immutable refresh
baseline and the complete current decision set. Duplicate, contradictory,
foreign, or stale evidence is rejected. Compatible decisions can be combined,
and record insertion/deletion rebinding is reflected in the complete intended
provenance. The resulting `FavoritesWritePlan` remains inert until the separate
assisted review and confirmation described below. Planning never calls a
copied-tree or USB executor, replaces provenance, emits a durable operation
report, or rereads RadioReference.

## Review, confirm, and execute

`Review exact plan` recomputes `plan_favorites_write()` from the ordinary manual
editor session's immutable baseline and intended snapshots. The screen reports
exact record changes, comparison ambiguities, schema/write blockers, and a
deterministic confirmation token bound to the source, baseline, and intended
bytes. It does not consume or execute the separate assisted synchronization
plan.

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

The assisted path is deliberately separate. Choose `Review exact assisted plan`
only after all supported decisions are complete. Its full SHA-256 confirmation
token binds the explicit storage kind and requested path, retained refresh and
lifecycle, decision order, baseline and intended Favorites, baseline and
intended provenance (including absent versus empty), and blockers. The token
field is not prefilled. A token from the ordinary editor, another target,
another refresh, a reordered plan, or any changed evidence is refused.

Paste that complete token into the assisted confirmation field and choose
`Execute confirmed assisted plan`. Before mutation, the controller requires the
same retained refresh object, an active unchanged lifecycle, no ordinary
in-memory edits, a complete non-no-op plan, exact current provenance, and a
fresh target snapshot equal to the reviewed baseline. Review and execution do
not contact RadioReference again.

A Favorites-changing plan delegates exactly once to the selected editor storage
adapter and then independently reads the target back. A provenance-only plan
does not invent a storage operation. Intended provenance is published
conditionally against the exact baseline. If publication returned uncertainly
but exact intended Favorites and provenance verify, execution succeeds with the
reconciliation noted. If provenance remains at baseline after a Favorites
write, the application derives an exact reverse plan, executes it through the
same backend, and requires baseline readback. Any mixed, unknown, or failed
recovery state is reported as incomplete while preserving available primary and
recovery operation evidence.

Success adopts the verified state into both the provenance lifecycle and editor
session. Success or failure consumes the refresh and clears the plan; retry
requires a fresh local inspection and provider refresh. Only one assisted
attempt can run at a time. Closing during its non-cancellable storage
transaction waits for terminal evidence before releasing the retained
lifecycle. The screen shows the target, operation ID, backup, staging, rollback
manifest, operation report, provenance path, reconciliation outcome, and
recovery evidence without writing tokens, provenance, provider payloads, or
local programming values to logs.

## Deliberate limits

The editor does not expose arbitrary positional fields, hierarchy or catalog
container creation/deletion/reordering, writable FTP, live-scanner GLT/FQK
mutation, daemon or web execution, Home Assistant Ingress execution, or
automatic/background synchronization. Assisted execution remains a local,
explicit, one-shot copied-tree or freshly qualified USB workflow. Use only
copied data you can recover or scanner media for which the verified backup and
rollback artifacts are acceptable. Physical reversible USB execution acceptance
remains part of Milestone 28.4 release closure.
