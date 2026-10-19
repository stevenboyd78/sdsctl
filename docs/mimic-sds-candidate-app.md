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

### Accepted-main smoke identity

A separately approved accepted-main smoke can use a source-bound identity when
the default acceptance slug would collide with a preserved Local App. Derive
the slug from the first 12 characters of the exact payload source revision and
pass the same slug when creating and verifying the context:

```bash
source_revision=<full-40-character-accepted-source-commit>
candidate_slug="sds200_main_${source_revision:0:12}"

python scripts/stage_mimic_app.py \
  --source-revision "$source_revision" \
  --candidate-slug "$candidate_slug" \
  --destination /absolute/private/path/new-accepted-main-candidate

python scripts/stage_mimic_app.py \
  --source-revision "$source_revision" \
  --candidate-slug "$candidate_slug" \
  --destination /absolute/private/path/new-accepted-main-candidate --verify
```

For source `4e9202ba3df5ac5c478e71426bbb3cfe14574901`, this produces
slug `sds200_main_4e9202ba3df5`, name and panel title
`sds200 accepted-main smoke 4e9202ba`, MQTT prefix
`sdsctl-main-4e9202ba3df5`, and recording directory
`sdsctl-main-4e9202ba3df5/recordings`. Home Assistant adds its normal `local_`
prefix to the installed identity, yielding
`local_sds200_main_4e9202ba3df5`. These values do not change the payload
version suffix or any public runtime behavior.

This path records two exact revisions in `candidate-source.json`:

- `source_revision` identifies every archived payload byte.
- `stager_revision` identifies the accepted commit containing the staging
  adapter.

The stager revision is inferred from the checkout, never supplied by the
operator. It must be a clean descendant of the payload source, distinct from
that source, and its complete source-to-stager diff must modify in place only
`scripts/stage_mimic_app.py`, `tests/test_stage_mimic_app.py`, and this guide.
Create and verify both re-attest the same checkout. Stage only after the adapter
PR has been normally merged and all exact-main gates pass, using the accepted
merge commit as the stager revision; do not stage from the PR head. The normal
fixed-slug path remains unchanged and still requires clean `HEAD` to equal the
payload source for creation.

This identity support only prepares and verifies an offline build context. It
does not authorize copying into `/addons`, refreshing or installing a Local
App, changing options or ports, starting or stopping an App, accessing a
scanner or Pi, or any other live-state mutation.

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
   example `/media/sdsctl-mimic-acceptance/profiles/profile.cfg` for the legacy
   fixed-slug candidate or `/media/sdsctl-main-<source12>/profiles/profile.cfg`
   for an accepted-main candidate. Derive the latter from its exact manifest
   state namespace. The original scanner profile remains read-only; any managed
   copy is explicit.
4. For browser Upload/Refresh, specify the exact trusted HTTPS Home Assistant
   origin and authorized HA user IDs in the deployment TOML. DNS or a literal
   IP can be used with valid HTTPS. Do not weaken origin/user checks to fit a
   convenient HTTP tab. For production, configure `scanner_display_config` only
   on the matching published v0.31.0 or later App, never on an older strict-
   parser image. Use this candidate field only during controlled development
   acceptance.
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
