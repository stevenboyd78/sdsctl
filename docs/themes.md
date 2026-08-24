# Theme package management

Milestone 26.13 provides a local managed lifecycle for third-party theme
packages. Milestone 26.14 consumes that inventory for web CSS only. The
separation remains intentional: Textual CSS and semantic palettes and executable
Home Assistant JavaScript need different activation and fallback rules.

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

## Remove and recover

Removal requires the exact interface and identity twice so an accidental broad
target cannot be inferred:

```bash
sdsctl themes remove web my-theme --confirm web/my-theme
```

The operation first renames only that managed directory to a private tombstone,
then deletes it. Built-ins are never valid removal targets. An invalid managed
directory can still be removed by its valid directory identity, preserving a
recovery path when its manifest cannot be parsed.

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

Managed Rich CLI themes, Textual palettes and TCSS, and Home Assistant modules
remain discoverable but inactive. The lifecycle does not download themes,
extract archives, execute scripts, or install Home Assistant resources. Future
renderer-specific milestones can consume the inventory without reopening its
filesystem mutation and recovery boundary. GUI theming remains reserved for the
future GUI design.
