# Mimic-SDS Local App acceptance preparation

Development-only procedure. This does not update the published App, offer a
released installation method, or authorize unsupervised scanner commands.
Use the [work packet](mimic-sds-work-packet.md) for scope and remaining limits.

## Why a separate App

Supervisor can read a newer repository catalog while an installed App still
runs an older image. Adding a new option to that catalog can therefore break
an older strict options loader. v0.31.0 pairs its nine-option image and schema;
older published images retain their seven-option contract. The candidate remains
a separate pre-release acceptance path, not an alternate production install.

`scripts/stage_mimic_app.py` creates a separate **sds200 Mimic-SDS acceptance**
Local App from one exact clean commit. It removes the published image reference
and builds the runtime from that same source, using the existing pinned App
Dockerfile. Its distinct version suffix contains the first 12 commit characters.
The underlying Python package still has development-baseline version metadata;
this build is **not** a published upgrade with that version number.

The candidate defaults to manual startup, a separate MQTT topic prefix and a
separate recording directory. All host ports are unmapped. Remote-daemon and
native HTTPS services remain disabled. `scanner_display_config` is empty, which
keeps the profile feature disabled. Installing the candidate does not create
scanner identity, credentials, profile storage or an accepted import.

## Stage and verify locally

Commit the reviewed changes first. Choose a new absolute output path below an
existing private directory, outside the checkout. Replace the sample commit
and path with the reviewed values; abbreviated revisions and branch names are
not accepted.

```bash
python scripts/stage_mimic_app.py \
  --source-revision <full-40-character-commit> \
  --destination /absolute/private/path/new-candidate

python scripts/stage_mimic_app.py \
  --source-revision <full-40-character-commit> \
  --destination /absolute/private/path/new-candidate --verify
```

Staging refuses an existing destination or a dirty/different current checkout.
It reads Git's committed source, not arbitrary workstation files. Verification
compares the complete file inventory and bytes against that committed snapshot,
not just the candidate's own hash report. Keep `candidate-source.json` with the
build evidence. Unexpected files, edits, missing files and filesystem links
fail verification. Failed staging is preserved for inspection, not deleted or
silently overwritten.

Build the context locally before transferring it to the Local Apps directory.
Record the source revision, platform, build arguments and resulting image ID.
Run isolated startup/import tests without networking or real user mounts. This
only qualifies the built image, not a Home Assistant installation or physical
scanner behavior. Copy the exact verified context, not a reassembled checkout.

## Controlled Home Assistant acceptance

Before switching the scanner owner:

1. Record the installed App version/state and save its current options, Network
   mappings and relevant card resources in a private backup. Do not print secrets.
2. Confirm that the candidate's Local App slug and source directory are distinct.
   Keep the existing App installed and unchanged. Do not enable candidate boot.
3. Prepare the [private display manifests and accepted-state directory](scanner-display-profile-import.md).
   Bind the exact scanner target and fresh opaque endpoint/source IDs. Use a
   separate candidate profile directory outside the recording inventory, for
   example `/media/sdsctl-mimic-acceptance/profiles/profile.cfg`. The original
   scanner profile remains read-only; any managed copy is explicit.
4. For browser Upload/Refresh, specify the exact trusted HTTPS Home Assistant
   origin and authorized HA user IDs in the deployment TOML. DNS or a literal
   IP can be used with valid HTTPS. Do not weaken origin/user checks to fit a
   convenient HTTP tab. Before v0.31.0 publication, configure
   `scanner_display_config` only in this candidate. After publication, configure
   it only on the matching v0.31.0 App, never on an older strict-parser image.
5. Stop the existing scanner-owning App before starting the candidate. For audio
   acceptance, explicitly map UDP 50000 during this switchover. Do not run two
   scanner owners or duplicate host-port bindings. Advanced listeners require
   their own reviewed identity/credential setup; do not copy secrets implicitly.

Test the empty-option baseline first, then opt in to the prepared deployment.
Review and accept a profile explicitly: startup never imports it for you.
Compare WebUI/TUI/card against selected physical scanner screens, including
individual holds and Simple/Detail choices. Check fresh reconnects, restart
persistence and disable/re-enable behavior. A changed or invalid managed source
must not silently replace the accepted profile. Keep unrelated recordings intact.
The [HA card guide](home-assistant-mimic-card.md) explains resource and YAML setup;
do not replace existing card resources without recording their prior state.

## Rollback and evidence

Stop the candidate before restoring the installed scanner owner. Restore any
explicitly changed card resources and temporary host-port assignments, then
start the unchanged installed App. Check both Pi connections and the installed
App health. Preserve candidate source, profile/state, recordings and private
backups for review; disabling an option is not deletion or credential revocation.

Record automated checks separately from user-observed physical passes. A
launch-plan test does not prove Supervisor rebuild/upgrade behavior, and a
synthetic frame does not prove a scanner firmware field. Do not publish private
profile contents, credentials, scanner names or endpoint evidence as fixtures.

For a separately reviewed bounded supplemental-read/audio trial, the
[cached media observer](supplemental-media-observer.md) provides read-only
progress/fault evidence without creating scanner-read demand or a second audio
consumer. Its local fixtures do not authorize a live trial or establish audible
playback, physical continuity, or full browser-consumer accounting.
