# Experimental browser-device profile setup

Status: **unreleased, explicit local setup only**. Use the matching reviewed
development build, not the published v0.29.4 command set. This is not yet a
supported unattended-browser installation guide. For the released manual-login
kiosk, use [the browser kiosk guide](browser-kiosk.md).

This command replaces hand-assembling the native helper's four private files.
It imports a **fresh browser-device enrollment attachment**, the chosen HTTPS
origin, and explicit certificate trust into a new directory. It does not enroll
a device on the server, install an extension or native host, initialize browser
storage, change browser policy or system trust, launch a browser, or enable a
service. Production Home Assistant and standalone launchers still do not enable
the experimental enrollment authority.

The profile is for a browser display only. Do not supply a dashboard password,
Home Assistant token, remote-TUI credential, or browser cookie. Existing managed
TUI and direct USB installations do not use this command.

## Before importing

Use a non-root Linux account that will own the eventual native helper. The
following must already have been reviewed and supplied through private channels:

- A fresh enrollment attachment for this exact device ID. Only the initial
  version-1, generation-1 issuance with `status: issued` and `completed: false`
  is accepted. Here `completed: false` means this is an issuance handoff, not
  evidence of installed or connected clients. Rotation/replacement downloads,
  including pending cleanup outcomes, are deliberately not accepted.
- The exact HTTPS origin: for example `https://192.168.1.20:8443`,
  `https://[fd00::20]:8443`, or `https://display.example:8443`. Do not append `/`,
  a page path, query, fragment, or credentials. DNS is optional; a proxy is not
  required. Use canonical lowercase DNS names and bracketed IPv6 addresses.
- A PEM certificate trust bundle obtained from the verified intended server or
  administrator. Keep private keys out of it. The helper will require a server
  certificate whose DNS name or IP subject alternative name matches the origin.
  Merely parsing this file does not verify that identity, certificate expiry,
  server availability or the device credential.
- The reviewed Chromium extension's exact 32-letter ID, using letters `a` to
  `p`. There is no production extension ID to discover or invent in this step.
  The test fixture's ID must not be substituted for a reviewed deployment ID.

Paths must be absolute and free of symlinks. Input files must be regular,
single-link files owned by this account, with mode `0600`. Their immediate
directories, and the new profile's existing parent directory, must be owned by
the account and have mode `0700`. Do not import directly from a shared Downloads
directory. A browser download does not guarantee those protections.

Choose a new, private staging directory, such as
`/home/display/sdsctl-browser-lab`, and privately transfer the reviewed attachment
and public trust bundle into it as `enrollment.json` and `ca.pem`. This example
assumes the account is named `display`; substitute the intended account's real
absolute paths. Do not print the attachment or put its credential in shell
arguments, environment variables, logs, chat, or diagnostic exports.

## Create the native profile

The destination must **not exist**, even as an empty directory. This directory is
not Chromium's user-data directory. Replace all example paths, origin, device ID
and the extension-ID placeholder before running:

```sh
sdsctl browser-device-profile --experimental create \
  --directory /home/display/sdsctl-browser-lab/native-profile \
  --enrollment-file /home/display/sdsctl-browser-lab/enrollment.json \
  --ca-file /home/display/sdsctl-browser-lab/ca.pem \
  --origin https://192.168.1.20:8443 \
  --device-id hallway-display \
  --extension-id REPLACE_WITH_REVIEWED_EXTENSION_ID
```

All input validation precedes creation. The command creates the private
directory exclusively, writes the credential and trust, explicitly initializes
the recovery ledger, then writes `client.json` last. It flushes file and
directory updates before reporting success. Its fixed files are:

| File | Contents |
| --- | --- |
| `client.json` | Version, exact origin, device ID and permitted extension origin; no credential |
| `device.secret` | The private browser-device credential |
| `ca.pem` | The explicit public certificate trust bundle |
| `recovery.sqlite` | Local mode, revision and retry state bound to the configured identity; no token or credential |

The new directory uses `0700`; its four files use `0600`. No existing files are
overwritten or permissions repaired. Concurrent importers cannot replace each
other's profile. An interrupted or failed write leaves any partial directory in
place for deliberate inspection. It is not automatically deleted or retried.

The original enrollment attachment and trust file are **preserved**, not consumed.
The attachment remains another copy of a long-lived credential even though the
server delivered it only once. After an independently reviewed installation and
acceptance, handle extra copies through a deliberate private cleanup process.
This command neither deletes them nor proves secure erasure.

## Run the offline check

```sh
sdsctl browser-device-profile --experimental check \
  --directory /home/display/sdsctl-browser-lab/native-profile
```

The check validates file protections, configuration, credential syntax, PEM
parsing and the existing ledger's format, state and identity binding. It does
not contact the network, authenticate, resume, renew, repair, change permissions
or initialize missing state. It does not persist clock correction. Unexpected
SQLite WAL format is rejected before opening it, avoiding auxiliary-file writes;
the created recovery ledger uses rollback journaling.

Success reports the **local recovery mode**, not a connection status:

- `active`: local automatic attempts are permitted; it does not mean logged in.
- `paused`: saved sign-out intent remains intact.
- `credential_rejected`, `tls_error`, `setup_error` or `protocol_error`: the
  saved terminal failure remains intact and needs deliberate investigation.

Exit status `0` means the local files and saved state passed inspection, even
when that saved mode is paused or terminal. Exit status `78` means the profile
or creation could not be validated. Argument errors, including omitting the
explicit `--experimental` acknowledgement, use exit status `2`. Diagnostics do
not include the credential or source-file contents.

Do not delete a ledger, recreate a profile, edit identity fields, or import a
rotation attachment to bypass a saved pause or error. An explicit, reviewed
credential replacement/resume workflow remains separate work. The file checks
do not defend against another process running as the same user or as root.

## What still needs acceptance

Local tests cover the actual initial administrator attachment, DNS/IPv4/IPv6
configuration, native-message status/pause, verified loopback HTTPS exchange,
TLS identity failure, malformed and unsafe inputs, concurrent creation, partial
writes and unchanged ledger contents during offline checks. All credentials and
certificates in those tests are fictional. They are not physical Pi, cold-boot,
production Home Assistant, browser-extension or combined-outage acceptance.

Next steps remain reviewed extension packaging/identity, native-host registration,
browser coordinator storage initialization, launcher/service wiring, explicit
replacement/resume, server configuration and the full outage matrix. Follow the
[enrollment and recovery design](managed-display-enrollment-design.md) for those
gates. The concise production installation wiki remains unchanged until that
workflow is supported and accepted.
