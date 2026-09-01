# Favorites and RadioReference

The Favorites Workspace editor is an optional local Textual workflow for
reviewing and changing supported scanner Favorites fields. Its write boundary
is deliberately stricter than ordinary scanner control.

## Start with a copied tree

Copy the scanner's Favorites directory to a working location and open only that
copy:

```bash
sdsctl favorites edit \
  --copied-tree /absolute/path/to/favorites_lists
```

Browsing, search, diagnostics, provenance, and raw detail are read-only. Edits
remain immutable and in memory until the exact write plan is reviewed and its
full target-bound confirmation token is entered in a separate step.

Success evidence includes the primary operation, backup, staging evidence,
rollback manifest, operation report, and exact readback. Keep those artifacts
until the resulting Favorites tree has been independently verified.

## Work with a mounted scanner

On Linux, use a freshly mounted and explicitly selected scanner target only
after validating the copied-tree workflow:

```bash
sdsctl favorites edit --usb /absolute/path/to/scanner-mount
```

The editor qualifies the exact mount and supported storage shape before it can
execute a reviewed plan. Never point it at `/`, a home directory, or an
unverified removable volume. Safely eject the scanner after the operating
system has completed writes.

## RadioReference-assisted import

RadioReference refresh is explicit and credentialed. Credentials are supplied
only through the named environment variables requested by the command; do not
put them in a command line, document, screenshot, Favorites tree, or repository.

The classified preview does not write. Every supported observation needs an
explicit decision, prepared-import evidence, separate exact-plan review, and a
full confirmation token before the existing verified executor can run.
Unresolved decisions or conflicts remain blockers.

Automatic background synchronization, silent conflict resolution, scraping,
and unsupported-field guessing are not implemented.

## Detailed reference

Read the
[Favorites Workspace editor guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/favorites-workspace-editor.md)
for supported fields, keyboard operation, backup and rollback, USB host-state
rules, warnings, provenance, and recovery. The
[RadioReference research](https://github.com/stevenboyd78/sdsctl/blob/main/docs/radioreference-interface-research.md)
and [external-data research](https://github.com/stevenboyd78/sdsctl/blob/main/docs/favorites-external-data-research.md)
record the provider and mapping boundaries.

