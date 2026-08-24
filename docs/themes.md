# Theme package management

Milestone 26.13 provides a local managed lifecycle for third-party theme
packages. It validates and inventories packages without loading their assets
into a renderer. The separation is intentional: web CSS, Textual CSS and
semantic palettes, and executable Home Assistant JavaScript need different
activation and fallback rules.

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

## Current activation boundary

Successful installation means **managed and discoverable**, not active. The web
picker and routes, Rich CLI `--theme`, Textual theme toggle, and Home Assistant
App installer continue using built-ins only. Milestone 26.13 does not download
themes, extract archives, execute scripts, install Home Assistant resources, or
load third-party CSS, TCSS, palettes, or JavaScript. Future renderer-specific
milestones can consume this managed inventory without reopening its filesystem
mutation and recovery boundary.
