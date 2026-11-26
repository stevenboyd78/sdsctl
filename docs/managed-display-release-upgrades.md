# Clean release replacement for managed displays

This guide defines a future release-upgrade contract for the managed Raspberry
Pi TUI display and browser kiosk. It replaces application code with a clean,
new versioned installation while preserving the exact host-specific runtime
configuration and identity. It does not upgrade the Home Assistant App, change
a scanner, rotate credentials, migrate a personal browser profile, or authorize
automatic deployment.

The word **uninstall** in this guide means retiring one exact application
release from service. It never means deleting configuration, credentials,
certificates, recordings, browser state, service identities, or unrelated
host data.

## Separate disposable releases from persistent state

Inventory each host before maintenance and classify every relevant path. A
path that has not been classified is not a cleanup target.

| Class | TUI display examples | Browser-kiosk examples | Upgrade treatment |
| --- | --- | --- | --- |
| Versioned application payload | `/opt/sdsctl-display-vVERSION` | `/opt/sdsctl-kiosk-vVERSION` | Install fresh; remove the superseded exact version only after acceptance |
| Stable release selector | `/opt/sdsctl-display` | `/opt/sdsctl-kiosk` | Required boot state; atomically select one accepted versioned payload |
| Portable service manifest | `/etc/sdsctl/display-client.toml` | `/etc/sdsctl/browser-kiosk.toml` | Preserve, validate with the new release, and change only through separate review |
| Application configuration | `/var/lib/sdsctl-display/.config/sdsctl` | `/var/lib/sdsctl-kiosk/.config/sdsctl` | Preserve owner, mode, content, and path |
| Client identity and trust | Remote-client profile, client secret, server certificate | Browser role/profile binding and public CA | Preserve exactly; do not clone to another host or rotate during a code-only update |
| Runtime state | Recordings, state and cache directories | Dedicated Chromium profile and browser-owned state | Preserve; cache may be reviewed separately but is not implicitly disposable |
| Service integration | Exact system unit, drop-ins, seat helpers and boot enablement | Exact system/user units, drop-ins, seat helpers and boot enablement | Preserve until the replacement passes syntax, start, functional and reboot checks |
| Evidence and rollback | Previous command, versions, hashes and acceptance report | Previous command, versions, hashes and acceptance report | Retain until the new release and cold boot are accepted |

The stable selector is not clutter. Units deliberately use it so one reviewed
path can select a versioned release without editing `ExecStart`. If the selector
is absent, `ConditionPathExists` may cause systemd to skip the service before it
attempts startup. Cleanup must therefore retain the selector whenever the
managed service remains installed.

## Transactional clean-replacement sequence

Upgrade one display host at a time. Keep the other accepted display and an
independent SSH or recovery-console path available.

### 1. Inventory and clean only transient artifacts

Record the active release selector, exact application version, package source
and digest where available, service and drop-in content, enablement, effective
command, manifest, protected file metadata, runtime directories, owned
processes, terminal or graphical seat, and recent service result. Remove only
identified temporary acceptance artifacts and already retired releases. Do not
remove the active selector or active payload during this phase.

### 2. Preserve and verify runtime state

Create a private, bounded backup or inventory receipt for the portable
manifest, application configuration, remote-client profile or browser-role
binding, credential references, public certificate, recording inventory,
dedicated browser profile, and service integration. Verify ownership and modes
as well as bytes. A copy that cannot be read back is not a backup.

Do not place secret bytes in logs or a release report. Record path identities,
safe metadata and digests only where that is already permitted by the relevant
deployment contract.

### 3. Build a clean candidate alongside the accepted release

Create a new, previously absent versioned application directory. Build a new
virtual environment and install only the exact reviewed release and required
extras. Never run `pip install --upgrade` inside the accepted environment.

Use the candidate binary directly to verify its version, imports, dependencies,
manifest parsing, effective command, terminal/browser prerequisites and
read-only service preflight. The stable selector must still reference the
accepted old release throughout candidate preparation.

This is a clean installation even though the old directory remains available
for rollback. Deleting the only working release before the candidate passes
offline preflight creates downtime without increasing cleanliness.

### 4. Stop and prove quiescence

Stop only the exact managed display service. For the TUI, confirm the process
released its terminal and waterfall/audio clients. For the kiosk, synchronously
stop the browser before the compositor and confirm the launcher, Chromium
children, user unit and profile-lock state meet the documented shutdown
contract. Do not kill unrelated browser, audio, daemon or scanner processes.

Record the stopped result before changing the selector. A service that is still
`deactivating`, owns a process, or has an uncertain browser shutdown is not
ready for replacement.

### 5. Atomically select the candidate

Create a new symlink beside the stable selector, verify that it resolves to the
exact candidate and that its `bin/sdsctl` is executable, then atomically rename
that symlink over the stable selector. Do not leave the stable path absent
between separate remove and create commands.

Re-read the selector and execute `sdsctl --version` through it before starting
the service. The service unit should not need an `ExecStart` edit for an ordinary
code-only release replacement.

### 6. Start and validate before cleanup

Start only the exact display service and require all applicable checks:

- systemd condition, active state, main PID and clean exit history;
- exact release version through the stable selector;
- expected TUI terminal or graphical seat ownership;
- configured daemon/API/event/display-frame/waterfall/audio negotiation;
- expected role and absence of newly granted scanner-control authority;
- retained theme, startup view, audio policy, recordings and browser profile;
- visible scanner state, Menu, Diagnostics and relevant automatic transitions;
- no duplicate display owner, stale release process, restart loop or new error;
- deliberate service restart followed by a physical cold-boot check.

Human visual or audible acceptance must be recorded as such; logs cannot infer
it. Do not exercise scanner controls, credentials, Home Assistant, recordings or
firmware merely because the application code changed.

### 7. Retire the old release only after acceptance

Once start, functional and cold-boot checks pass, the old version is no longer
the selected installation. Confirm no unit, drop-in, process, interpreter,
manifest or helper references its versioned path. Only then remove that exact
old application directory through a separately reviewed cleanup action.

Retain the previous release identity, command, configuration receipt and
acceptance evidence according to the release policy even after its executable
payload is removed. Never use a wildcard or a broad `/opt`, `/var/lib`, `/etc`,
home-directory or workspace target for release cleanup.

## Rollback

If candidate preflight fails, discard only the new candidate; the running
selector was never changed. If startup or functional validation fails after the
switch:

1. stop the exact affected service and prove it is quiescent;
2. atomically restore the stable selector to the retained accepted release;
3. re-read the selector and version through the stable path;
4. start the exact service and repeat its bounded health and functional checks;
5. retain the failed candidate and evidence until the cause is understood.

Do not roll back by copying selected files into either environment, editing a
virtual environment in place, restoring only part of a browser profile, or
silently reverting a changed configuration schema. Configuration migration,
credential rotation, operating-system change and browser-profile migration are
separate changes and need their own compatibility and rollback plans.

## Relationship to a literal uninstall-first procedure

A literal sequence that permanently deletes the active release before a new
candidate exists has three avoidable failure modes: no executable rollback,
systemd condition skips caused by a missing stable selector, and pressure to
reconstruct protected configuration during an outage. The transactional
sequence above still provides a full clean installation and a full retirement
of the old version, but moves permanent deletion behind acceptance.

If storage pressure prevents two temporary versioned environments, stop and
free unrelated, explicitly reviewed space first. Do not trade away the only
rollback or use runtime configuration as cleanup space.

## Automation boundary

A future installer may automate inventory, candidate installation, offline
preflight, atomic selection and read-only verification. It must fail closed on
unknown paths, missing backups, symlink surprises, unsafe ownership, an active
service during selection, version mismatch, or an unavailable rollback. It must
not automatically rotate credentials, migrate configuration, delete an old
payload, deploy to both displays together, or infer physical acceptance.

Automatic deployment remains deferred until this procedure has passed
repeatable one-host-at-a-time qualification on both managed displays.
