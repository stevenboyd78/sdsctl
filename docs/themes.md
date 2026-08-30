# Theme package management

Milestone 26.13 provides a local managed lifecycle for third-party theme
packages. Milestones 26.14 and 26.15 consume that inventory for web CSS and
terminal palettes and TCSS respectively. Milestone 26.16 adds explicit,
digest-pinned deployment for executable Home Assistant JavaScript. Milestone
27.3.1 hardens validation and installation around one consistent source
snapshot without changing any package schema, identity, or activation behavior.
The external review finding that led to this work and every other finding from
that review are tracked in the
[implementation review disposition ledger](implementation-review-ledger.md).

## Managed directory

The default root follows the normal XDG user configuration path:

```text
${XDG_CONFIG_HOME:-~/.config}/sdsctl/themes/
├── web/
│   └── <theme-name>/
├── home-assistant/
│   └── <theme-name>/
└── tui/
    └── <theme-name>/
```

Use `sdsctl themes --root /absolute/path ...` to operate on an explicit root.
The lifecycle never scans elsewhere automatically. Built-in themes remain in
the installed Python distribution and cannot be shadowed, replaced, or removed.

The web dashboard's **System palette** selector is a separate presentation
control, not another managed-theme interface. Its Follow-device option and the
21 built-in Textual color schemes all remain inside the stable `system` web
theme package and therefore do not appear as installable, replaceable, or
removable directories. Selecting one does not change theme discovery, package
identity, lifecycle validation, or the `sdsctl.web.theme` value; it only updates
the browser-local `sdsctl.web.system-palette` choice while System is active.

## Validate and inspect

Validate one unpacked local package without changing its source or managed
inventory:

```bash
sdsctl themes validate /absolute/path/to/themes/web/my-theme
```

List built-ins, valid managed packages, and isolated invalid entries:

```bash
sdsctl themes list
sdsctl themes list --json
```

Copying a package directory manually into the exact managed hierarchy makes it
discoverable on the next inventory. Manual placement does not bypass schema,
path, size, collision, or asset validation. One malformed package is reported
independently and does not hide built-ins or other valid packages. `list` exits
with status `1` when invalid managed entries need attention.

## Install and replace

Install one explicit local directory:

```bash
sdsctl themes install /absolute/path/to/themes/web/my-theme
```

The source directory name must exactly match the manifest `id`. Sources inside
the managed root, URLs, archives, nested directories, symlinks, special files,
undeclared files, unsupported schemas, oversized packages, and registry
collisions are rejected. A package may contain at most eight top-level regular
files and at most 4 MiB across the complete package.

Every managed-package validation, including explicit `validate`, `install`, and
inventory discovery, opens the selected source directory once and retains that
descriptor and its identity. Entries are enumerated, inspected, and opened
relative to that descriptor with no-follow semantics. The lifecycle then reads
in bounded chunks and copies the exact bytes into a new private validation
snapshot before it parses `manifest.json` or invokes a web, Home Assistant, or
TUI schema validator. The 4 MiB allowance is one package-wide budget charged
from the actual bytes read; it is not reset for each file or reused as an
independent write allowance. A size seen during initial inventory does not
authorize later growth.

Source directory identity, membership, file identity, regular-file type, link
count, size, modification time, metadata-change time, actual read total, and
private-snapshot contents are checked across acquisition. Replacement,
same-name substitution, same-size mutation, truncation, growth, a symlink swap,
or a membership change therefore rejects the operation and removes only the
private snapshot. Interface parsing and the reported SHA-256 digest consume the
private snapshot, never a fresh pathname read from the concurrently mutable
source tree. `validate` removes that snapshot when it returns and does not write
to the managed theme root.

Installation uses a second private stage inside the destination interface
directory. It copies the already validated snapshot into that same-filesystem
publication stage with the same descriptor-relative no-follow and aggregate-
byte checks, verifies that its files, manifest, and digest are identical, assigns
private directory and file modes, and only then publishes through an atomic
no-replace rename. Before recovery or staging begins, mutation qualifies both
the collision and success behavior of that primitive on the managed interface's
actual filesystem. A destination entry that appears concurrently is therefore
never overwritten, even when it is an empty directory. The published directory
is reopened and compared with the exact staged image before replacement rollback
is discarded. These two stages separate consistent source validation from
atomic same-filesystem publication. A pathname-only recursive directory copy is
deliberately not substituted because it would not provide the same identity,
byte-budget, digest, or destination-collision guarantees.

When the configured managed root or interface does not yet exist, the lifecycle
first qualifies no-replace directory renames on its parent filesystem. It then
creates the directory under a fresh 128-bit-token candidate name, retains and
verifies that exact empty directory, and publishes it to the configured name
with no-replace semantics. Root, interface, stage, and package bindings are
rechecked through the transaction. Operator diagnostics always report the
configured path; retained `/proc/self/fd` paths are an internal Linux mechanism
and are not reconciliation instructions.

Managed mutation currently requires Linux `renameat2(RENAME_NOREPLACE)` plus
descriptor-relative open, stat, rename, unlink, rmdir, and directory enumeration
support. An unsupported kernel or target filesystem fails before publishing a
new configured root or interface, staging, or moving a public package. The
lifecycle attempts to remove each exact verified-empty capability probe; if the
required detach primitive is itself unavailable, or an artifact is no longer
exact and empty, the artifact may remain and requires reconciliation. Read-only
theme validation and inventory discovery retain their separate descriptor-
support gate and do not require the mutation probe.

If an unrecognized concurrent entry replaces the exact publication target,
recovery does not recursively delete it. The entry is detached without following
links and preserved at `.sdsctl-conflict-<id>` while the previous valid package
is restored. Further mutation of that identity is blocked until the operator
inspects the conflict and explicitly removes or relocates it.

Publication stages use
`.sdsctl-stage-<id>--<128-bit-token>/.sdsctl-stage.json`; the record binds the
interface, identity, token, device, and inode to that exact directory. Before
retaining a package for removal, the lifecycle creates a separately randomized
`.sdsctl-removal-record-<id>--<128-bit-token>` directory. Its record binds the
interface, identity, filename token, and exact target device and inode; the
record directory's observed identity is retained and rechecked during the
current operation. Recovery deletes a removal tombstone—including an
intentionally invalid package—only when the matching record and target identity
are complete and exact.

Recovery is artifact-specific. A valid recorded stage is an authenticated
abandoned publication and is removed. A correctly token-shaped stage that is
still completely empty may be removed as an interruption before record creation;
a populated unrecorded stage or malformed/mismatched stage record is preserved.
An empty incomplete removal-record directory is removed only when no tombstone
exists and the original target still exists. Multiple removal records for one
identity, an orphaned tombstone, a mismatched recorded tombstone, incompatible
stage/rollback/removal state, and ambiguous target-plus-rollback state are all
preserved and block mutation for explicit operator reconciliation.

Managed-root private transaction trees are removed one entry at a time through
iterative, retained descriptor-relative traversal rather than a fresh recursive
pathname. Cleanup first detaches the exact verified identity to a fresh
`.sdsctl-purge-<128-bit-token>` name. A purge entry has no transaction record,
so any purge discovered by a later mutation is deliberately treated as
unauthenticated: it is preserved, reported at its configured operator path, and
blocks automatic recovery. When the public target is absent, recovery validates
a saved rollback package's complete schema, interface, identity, image, and
digest before promotion. If a valid public target and a valid rollback both
exist, recovery preserves both rather than guessing which copy to delete.

The lifecycle lock serializes cooperating `sdsctl` mutation commands and rejects
symbolic-link or hard-link substitution of the lock. This boundary assumes that
the managed root is not concurrently renamed or rewritten by another process
with the same operating-system account and filesystem permissions. The identity
gates detect observed accidental or non-cooperating changes and avoid
overwriting unknown entries, but they are not a sandbox against hostile
same-account code that can monitor and replace configured or freshly randomized
private names between individual filesystem syscalls. Keep the managed root
private to the trusted account that runs `sdsctl`; use a separate account or
stronger operating-system isolation for mutually untrusted local code.

Replacing an existing managed identity is always explicit:

```bash
sdsctl themes install --replace /absolute/path/to/themes/web/my-theme
```

The previous directory remains as a private rollback until the replacement and
post-publication inventory validate. A later lifecycle operation automatically
recovers a lone validated rollback when its public target is absent, removes a
valid authenticated stage or recorded removal tombstone, and applies only the
artifact-specific empty pre-record cleanup described above, all while holding
the lifecycle lock. Ambiguous states and unauthenticated purge entries remain
preserved and require explicit operator reconciliation.

Home Assistant theme modules are executable JavaScript in the browser. Schema
and snapshot validation cannot make third-party code safe. Review the complete
source and then provide the separate trust acknowledgement:

```bash
sdsctl themes install \
  --trust-home-assistant-code \
  /absolute/path/to/themes/home-assistant/my-card
```

Installation makes the package discoverable but does not deploy or approve its
module. Replacing an active package leaves the deployed bytes unchanged and
marks its activation stale until the new complete package digest is approved.
The install or replace acknowledgement and the later exact-digest activation or
reapproval are distinct gates. A valid snapshot never supplies, infers, or
weakens either gate.

## Remove and recover

Removal requires the exact interface and identity twice so an accidental broad
target cannot be inferred:

```bash
sdsctl themes remove web my-theme --confirm web/my-theme
```

The operation first creates and verifies the randomized transaction record that
binds the requested target identity. It then renames only that exact managed
directory to the private tombstone, deletes the tombstone, and finally removes
the record. Built-ins are never valid removal targets. An invalid managed
directory can still be removed by its valid directory identity, preserving a
recovery path when its manifest cannot be parsed. A Home Assistant package with
any activation record cannot be removed until every target is explicitly
deactivated.

## Web activation

A valid package under `themes/web/<id>/` is automatically added to the existing
dashboard picker the next time `sdsctl web` starts. There is no separate enable
command. Discovery occurs once during process construction: installing,
replacing, repairing, or removing a package requires a web-process restart to
change the picker. A stored selection that is no longer in the startup registry
falls back to **System**.

The dashboard emits managed stylesheets as disabled links and enables only the
currently selected managed package. Its same-origin theme route serves only the
single CSS filename declared by that startup-validated manifest. Each request
reopens the exact managed root, `web` interface, package directory, manifest,
and stylesheet without following symlinks; requires the original package
directory identity and complete package digest; and returns not found after any
removal, replacement, mutation, symlink substitution, special-file substitution,
or undeclared-file addition. Responses retain the dashboard's restrictive CSP,
`nosniff`, and no-store headers.

CSS can substantially alter presentation and can request same-origin resources
allowed by the dashboard CSP. Review every third-party stylesheet before
installing it. Schema, path, size, and digest validation prevent package-boundary
violations and unnoticed post-start changes; they are not a complete safety or
design review of CSS.

## Terminal activation

A valid package under `themes/tui/<id>/` is available to a newly started
terminal-rendering command when its ID is selected with `--theme`, the `theme`
configuration field, or `SDSCTL_THEME`. The Rich `scanner-info` adapter consumes
the complete semantic palette. The full-screen Textual interface also adds the
selected stylesheet to its in-memory startup CSS and applies the manifest's
declared `screen_class`. There is no filesystem watching or live reload.

Managed terminal packages must declare a non-null, unique lowercase kebab-case
`screen_class`. Every TCSS selector must be that exact `Screen.<screen-class>`
or a descendant class/ID selector. Declarations are restricted to `color`,
`background`, and whole or side-specific `border` properties with literal
`#RRGGBB` colors and supported border styles. Imports, URLs, variables, unscoped
selectors, nested rules, layout properties, and undeclared files are rejected.
Shared dimensions, visibility, ordering, labels, responsive behavior, and
controls cannot be supplied by a theme package.

The selected package is revalidated against its complete discovery digest and
read into immutable runtime values before scanner or daemon access. Later file
mutation, replacement, or removal cannot change the running terminal process.
An unavailable selected ID fails with the valid startup IDs; malformed managed
entries remain isolated. `T` from a managed Textual theme returns to built-in
dark, after which dark/light toggling remains unchanged.

## Home Assistant activation

First inspect the managed package and copy its complete `sha256` value:

```bash
sdsctl themes list
```

After reviewing all package files, approve that exact digest and deploy only its
declared module to an existing absolute Home Assistant `www/sds200` directory:

```bash
sdsctl themes activate home-assistant my-card \
  --target-directory /homeassistant/www/sds200 \
  --confirm-sha256 <sha256-from-the-current-inventory> \
  --trust-home-assistant-code
```

The activation command securely reopens the managed package without following
symlinks, verifies its complete digest, and never evaluates JavaScript. It
atomically installs the module with mode `0644` and records the exact package,
module, manifest fields, and target directory in a private ledger under the
managed root. A new activation never overwrites unrelated target content. A
replacement is deployed only when the current target still matches the prior
approved module digest, so an operator-modified file is left untouched.

Inspect all activation records without contacting Home Assistant:

```bash
sdsctl themes activations
sdsctl themes activations --json
```

Each record reports `current`, `stale-package`, `changed-target`, or
`missing-target`; an unsafe or malformed ledger reports `invalid-ledger`.
Package install, manual placement, replacement, and discovery never update the
deployed module automatically.

Deactivation requires the same exact target directory and identity confirmation:

```bash
sdsctl themes deactivate home-assistant my-card \
  --target-directory /homeassistant/www/sds200 \
  --confirm home-assistant/my-card
```

Only the ledger-pinned filename with the exact approved module digest is
removed. Modified, missing, symlinked, or substituted targets fail closed.
Unrelated files and activation records are preserved.

Register `/local/sds200/<declared-filename>.js` as a JavaScript module resource
in Home Assistant manually. These commands do not edit YAML, `.storage`,
dashboards, Lovelace resources, App options, or Home Assistant Core state. The
Home Assistant App installs only the three bundled first-party cards (compact,
display, and waterfall);
it does not scan or activate the user-writable managed theme directory.

The lifecycle does not download themes, extract archives, execute scripts, or
install Home Assistant resources automatically. GUI theming remains reserved
for the future GUI design.
