# Experimental browser-device review bundle

Status: **unreleased preparation tool, not a production installer**. Use the
matching reviewed development build, not the published v0.29.4 command set.
For a working manual-login installation, use [the kiosk guide](browser-kiosk.md).

This step packages the experimental recovery and sign-out modules with a fixed
server/device identity. It also stages a Linux native-host launcher and its
registration manifest. **Creating these files does not install the extension,
register the native host, initialize browser storage, authenticate, install
certificate trust, or change any service or production server.**

## 1. Choose and verify an extension identity

An extension needs a stable ID so the native host can restrict which extension
may call it. Obtain a reviewed **public** SubjectPublicKeyInfo (SPKI) PEM file
through your deployment administrator. Do not supply a private signing key,
certificate, device credential, dashboard password or downloaded enrollment file.
There is no product-wide production key/ID assigned by this tool.

The public key must be a single regular file, owned by the non-root Linux
account preparing the bundle, with mode `0600`, in a directory owned by that
account with mode `0700`. Paths must be absolute and free of symlinks. These
protections are required even though the key is public, to avoid accidentally
selecting a mutable shared input. The file is bounded to 4096 bytes. OpenSSL must
be installed in the system executable search path for preparation; the native
helper does not invoke it during use.

Assuming the account is `display` and the reviewed public key has been privately
placed in its existing lab directory:

```sh
sdsctl browser-device-bundle --experimental identity \
  --public-key /home/display/sdsctl-browser-lab/extension.pub.pem
```

The command prints only the public extension ID and public-key SHA-256
fingerprint. Use that ID when [creating the native profile](browser-device-profile.md).
Reusing this public key preserves the ID when preparing another bundle path.
The manifest includes the base64 public key using Chrome's
[stable development identity mechanism](https://developer.chrome.com/docs/extensions/reference/manifest/key).
The ID is derived from the first 128 bits of its SHA-256 digest, using Chromium's
letter encoding. A matching ID is **not** proof of private-key ownership, a signed
CRX, Web Store distribution or a trusted publisher. Those deployment decisions
remain separate review gates. The command does not generate or upload a key.

## 2. Prepare a new bundle

First run the profile's read-only check. The bundle command repeats that offline
inspection, including credential syntax and certificate parsing, but does not
copy the credential, certificate bundle or recovery ledger into the extension.
It requires the profile's extension ID to match the supplied public key.

```sh
sdsctl browser-device-profile --experimental check \
  --directory /home/display/sdsctl-browser-lab/native-profile

sdsctl browser-device-bundle --experimental create \
  --directory /home/display/sdsctl-browser-lab/review-bundle \
  --profile /home/display/sdsctl-browser-lab/native-profile \
  --public-key /home/display/sdsctl-browser-lab/extension.pub.pem
```

The bundle destination must not already exist, even as an empty directory. Its
parent must already be private (`0700`, owned by this account). All input checks
precede creation. The tool never overwrites, repairs permissions, deletes files,
clears a pause/error, or resets a recovery ledger. Existing source files stay in
place. Interrupted creation leaves any partial directory for deliberate review;
do not retry over it. Exit codes are `0` for completed preparation, `78` for fixed
redacted validation/write errors, and `2` for argument errors or missing opt-in.

## 3. Review the artifacts

| Artifact | Purpose |
| --- | --- |
| `extension/manifest.json` | MV3 public key, fixed HTTPS host/port scope and minimal required permissions |
| `extension/browser_device_*.mjs` | Canonical recovery and sign-out modules shipped in the Python package |
| `extension/worker.mjs` | Fixed identity configuration and top-level lifecycle listeners; no fixture globals |
| `extension/content.js` | Isolated, top-frame sign-out bridge guarded by the exact root-page URL |
| `extension/control.html` | Static review notice, not an enrollment/password UI |
| `native-host` and `native_host.py` | Fixed interpreter, profile path and expected configuration identity |
| `org.sdsctl.browser_device.json` | Staged stdio native-host registration, restricted to one exact extension origin |
| `bundle.json` | Completion receipt with public identity/trust fingerprints and artifact hashes |

Directories and the executable launcher use `0700`; other files use `0600`.
The receipt is written last and flushed. Its hashes help compare reviewed bytes;
they are not a signature or protection from another same-user/root process.
The bundle contains the selected origin/device identity and local profile and
interpreter paths, so do not publish it as a generic diagnostic attachment.

The launcher uses the Python interpreter that prepared the bundle, in isolated
mode (`-I`), and therefore needs that installation to remain available. It does
not search the current directory, `PYTHONPATH` or the user site for imports. A
dedicated candidate virtual environment is recommended. Keep its path intact;
moving the bundle or environment does not rewrite the staged absolute paths.
The native child rejects a changed origin/device/extension identity before ledger
or credential access. A matching identity does not bypass native caller checks,
private-file checks, certificate verification or existing recovery state.

The manifest requests only `nativeMessaging`, `storage`, `cookies` and `alarms`,
plus the configured HTTPS host and explicit port. DNS, IPv4 and bracketed IPv6
origins generate matching scopes; neither DNS nor a proxy is required. Chrome
[match patterns](https://developer.chrome.com/docs/extensions/develop/concepts/match-patterns)
and the content script's runtime exact-URL guard are separate checks. No wildcard
hosts, HTTP fallback, external-message entrypoint, web-accessible modules,
incognito support or browser-wide certificate exception is added.

**Cookies are host-scoped, not port-isolated.** A narrow extension permission
does not stop a cookie being sent to another HTTPS port on the same host. Use a
dedicated account/browser profile and a server host whose HTTPS services share
the intended trust boundary. Browser trust still requires separate verification.

## Stop here: deployment is a separate gate

The command deliberately does **not** write to any browser `NativeMessagingHosts`
directory. Chrome variants and profiles have different registration locations;
follow the reviewed browser's
[native-messaging rules](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging)
in a separate acceptance step, not by copying a guessed path from another host.

Do not load this bundle into a production browser yet. A fresh browser has no
trusted recovery state and therefore fails closed with `setup_error`; the worker
does not initialize or reset it. Loading into a previously provisioned test
profile is **not inert**: the worker reconciles its saved state and may contact
the native helper. Preparation alone never launches that worker. Existing pause
and terminal-error behavior remains part of the recovery contract.

Trusted browser-state initialization, controlled registration/update/removal,
launch/service and server wiring, explicit replacement/resume, browser-specific
DNS/IP and permission acceptance, and physical multi-display power-outage tests
remain required. A generated manifest and a passing native status call do not
establish successful login, renewal, sign-out, cold boot or production readiness.
See the [enrollment design](managed-display-enrollment-design.md) and the isolated
[experimental test harnesses](../scripts/experimental/README.md). Production wiki
installation guidance and existing TUI/manual-login defaults remain unchanged.
