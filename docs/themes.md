# Theme package management

Milestone 26.13 provides a local managed lifecycle for third-party theme
packages. Milestones 26.14 and 26.15 consume that inventory for web CSS and
terminal palettes and TCSS respectively. Milestone 26.16 adds explicit,
digest-pinned deployment for executable Home Assistant JavaScript.

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

## Validate and inspect

Validate one unpacked local package without writing:

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
collisions are rejected. The package is copied to a private same-filesystem
staging directory, validated again, assigned private directory and file modes,
and published by rename.

Installation opens the source directory and every declared entry
descriptor-relatively without following symlinks, requires stable file identity
across inspection and copy, and compares the complete private-stage digest with
the validated source digest before publication. These checks are the
source-replacement race boundary. A pathname-only recursive directory copy is
deliberately not substituted because it would not provide the same identity and
digest guarantees.

Replacing an existing managed identity is always explicit:

```bash
sdsctl themes install --replace /absolute/path/to/themes/web/my-theme
```

The previous directory remains as a private rollback until the replacement and
post-publication inventory validate. An interrupted later lifecycle operation
recovers a saved rollback and removes abandoned staging or removal tombstones
while holding the lifecycle lock.

Home Assistant theme modules are executable JavaScript in the browser. Schema
validation cannot make third-party code safe. Review the complete source and
then provide the separate trust acknowledgement:

```bash
sdsctl themes install \
  --trust-home-assistant-code \
  /absolute/path/to/themes/home-assistant/my-card
```

Installation makes the package discoverable but does not deploy or approve its
module. Replacing an active package leaves the deployed bytes unchanged and
marks its activation stale until the new complete package digest is approved.

## Remove and recover

Removal requires the exact interface and identity twice so an accidental broad
target cannot be inferred:

```bash
sdsctl themes remove web my-theme --confirm web/my-theme
```

The operation first renames only that managed directory to a private tombstone,
then deletes it. Built-ins are never valid removal targets. An invalid managed
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
Home Assistant App continues to install only the two bundled first-party cards;
it does not scan or activate the user-writable managed theme directory.

The lifecycle does not download themes, extract archives, execute scripts, or
install Home Assistant resources automatically. GUI theming remains reserved
for the future GUI design.
