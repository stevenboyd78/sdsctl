# Favorites web workspace foundation

The Favorites web workspace is an experimental server-side foundation for a
future browser editor. It reuses the existing lossless Favorites parser,
navigation model, in-memory editor, validation, exact write plan, and
confirmation token. The editor workspace still has no HTTP route, browser UI,
write executor, scanner discovery, or target selection. The separate durable
profile store has one explicitly opt-in summary-only read adapter described
below; that adapter does not connect the editor workspace or expose snapshots.

The first slice intentionally stops at exact-plan review. It is safe to use in
tests and future server adapters with copied Favorites data, but it does not
authorize a scanner, USB, FTP, or production-tree write.

## Persistent named profiles

`FavoritesProfileStore` is the separate durable foundation for browser profile
selection. It keeps exact `FavoritesStorageSnapshot` values in one private
owner-only SQLite database. Every profile has a user-facing name and one
explicit compatibility value: `sds100`, `sds200`, or `shared`. Names are
unique without case ambiguity, while opaque profile identifiers—not names or
filesystem paths—are the stable application identity.

Creation publishes immutable revision 1. A later save must provide the exact
current revision that its caller reviewed; a stale save is rejected before any
new bytes are committed. An accepted change appends one immutable revision and
atomically selects it as current. The exact catalog bytes, ordered HPD names and
bytes, compatibility, name, content digest, sizes, and timestamps for earlier
revisions remain readable. Repeating the exact current snapshot and metadata is
idempotent and does not invent another revision.

The configured directory must be canonical, non-symbolic, owned by the current
account, and mode `0700`; the database must be one owner-only mode-`0600`
regular file. Profile, revision, document, snapshot-byte, and name limits fail
closed without evicting or partly publishing another profile. The store uses a
full-synchronous transaction and serializes operations within one process;
SQLite write locking plus the exact-revision check also protects concurrent
store instances.

An opt-in read-only adapter may expose the current path-free summaries to an
existing authenticated dashboard operator. It lists summaries and retrieves
one current summary; it never returns catalog bytes, document names or bytes,
filesystem paths, older revisions, drafts, or confirmation tokens. Native-LAN
display identities cannot use it, and Home Assistant access still requires the
trusted Ingress peer. Every response is no-store, malformed and unknown
identifiers share one generic unavailable response, and store failures fail
closed. The adapter has no CLI or configuration wiring, so ordinary dashboard
construction does not create the routes.

The same opt-in also makes an authenticated `/favorites` shell and its three
fixed same-origin assets available. The shell lists and selects only those
summary objects and displays compatibility, document count, current/history
revision numbers, and aggregate snapshot size. It does not fetch a snapshot or
workspace, accept a mutation, store a profile or draft in browser storage, or
enable its review and execution controls. Native display identities cannot
load the shell or its assets. A strict no-store policy, same-origin resource
policy, restrictive CSP and permissions policy apply to both the page and
assets. When the store is not explicitly supplied, the page, assets, and
summary API do not exist.

The daemon-backed `sdsctl web` command can explicitly own this read-only
surface with `--favorites-profile-directory /absolute/private/directory`.
Activation requires either authenticated native HTTPS or trusted Home Assistant
Ingress; ordinary loopback mode and generic container exposure reject it. The
directory is opened through the same owner-only profile-store checks before the
web server starts, and the store is synchronously closed when server execution
returns or startup fails. Omitting the option preserves the existing dashboard
and creates no profile directory or routes. This option does not create,
import, mutate, export, or write a profile and does not add Home Assistant App
packaging configuration.

This layer still deliberately provides no draft autosave, source discovery,
scanner connection, target selection, import, export, or write executor. A
future mutation adapter must retain the existing no-store, CSRF, same-origin,
logout/revocation, worker-admission, and explicit write-review boundaries.
Profile persistence and summary visibility are not permission to mutate a
scanner.

## Ownership and source selection

`FavoritesWebWorkspaces` receives administrator-configured
`FavoritesWebWorkspaceSource` entries. Each entry maps a short public label to
one absolute copied-tree directory. Future browser requests may choose only a
configured label; they cannot submit or discover filesystem paths. Summaries
and errors do not expose the configured path.

Every operation requires both:

- the opaque workspace identifier; and
- a server-derived owner secret associated with an already-authorized session.

The workspace identifier alone is never authority. The manager retains only a
SHA-256 digest of the owner secret and compares it in constant time. A wrong
owner, malformed identifier, unknown workspace, expired workspace, or unknown
source label produces the same unavailable result so callers do not gain an
enumeration oracle.

The owner secret must stay on the server. It must not be placed in JavaScript,
URLs, browser storage, form fields, logs, or exported workspace data. A future
HTTP adapter should derive it from the existing authenticated session context
and discard that owner's workspaces on logout or revocation.

## Bounded lifetime and capacity

The process-local manager has explicit limits for:

- total active workspaces and workspaces per owner;
- documents, records, and immutable source bytes per workspace;
- accepted mutations per workspace;
- idle lifetime; and
- absolute lifetime.

Expired workspaces are discarded. Restarting the process also discards every
workspace. Capacity exhaustion fails closed and never evicts another active
editor. Authorized reads refresh only the idle deadline; no request extends the
absolute deadline.

The manager is synchronous and serialized. A future ASGI integration must use
the web service's existing bounded worker admission for copied-tree I/O and
parsing. It must not create unbounded background threads.

## Revisioned editing

Each mutation carries the exact revision the browser reviewed. A stale revision
is rejected before mutation. The initial foundation exposes renderer-neutral
operations already supported by `FavoritesEditorSession`:

- rename one supported record;
- duplicate one supported leaf;
- delete one supported leaf;
- undo one accepted mutation; and
- reset to the immutable baseline.

Accepted mutations increment the workspace revision and mutation count. A
no-op undo or reset does not. Search/navigation projections also require an
exact revision so a future renderer cannot present results from a different
editor state.

The manager returns path-free summaries containing bounded counts, validation
status, revision, dirty/undo state, and expiry deadlines. Navigation results
are separate safe DTOs containing an opaque revision-bound node identifier,
kind, display name, parent relationship, child count, and supported-operation
flags. They never contain a `FavoritesNavigationNode`, source record,
filesystem path, source filename, or raw record bytes. Mutation calls consume
the opaque node identifier instead of a browser-supplied navigation path. All
node identifiers are invalidated after an accepted mutation, and an unknown,
malformed, cross-owner, colliding, or stale node identifier fails closed.

## Review boundary

`review()` delegates to the existing exact Favorites review contract, but the
internal immutable `FavoritesWritePlan` never crosses the web presentation
boundary. The returned DTO contains only the current workspace and revision,
the separate full confirmation token, blocker values, changed-source and
ambiguity counts, aggregate record-change counts, and aggregate validation
rule/severity counts. The token is excluded from object representations and a
future HTTP adapter must additionally send it only in a no-store response,
never log it, and never persist it in browser storage. Review does not execute
the plan.

Future routes must use the explicit field-by-field functions in
`favorites_web_presentation.py`. Those functions return JSON primitives for
summaries, nodes, and aggregate reviews and reject arbitrary objects. Do not
replace them with `dataclasses.asdict()`, a generic JSON encoder, `repr()`, or
direct serialization of editor/navigation/comparison/schema objects; those
internal objects retain exact source provenance by design.

A later write packet must remain separately reviewed. At minimum it must bind
the selected configured source and exact workspace revision to an authorized
operator, requalify the target, preserve backup and recovery evidence, execute
through an existing verified executor, perform exact readback, and close the
workspace after success or logout. This foundation must not be expanded into a
generic PATCH endpoint or raw file-replacement API.

## Deliberate exclusions

This slice does not provide:

- a profile mutation, snapshot, import, export, workspace, or WebSocket
  endpoint;
- a reachable or data-connected browser editor (the packaged appearance shell
  remains disconnected and every workspace action is disabled);
- browser-supplied filesystem paths;
- autosave or durable draft persistence;
- implicit source or write-target discovery;
- generic record or raw-text replacement;
- RadioReference credential use or network access;
- FTP, USB, copied-tree, or scanner write execution; or
- display-session access.

The current interactive editor and its verified write boundaries remain
documented in [Favorites Workspace editor](favorites-workspace-editor.md).

## Browser appearance requirement

The future browser editor must support three explicit appearance modes:
`Auto`, `Light`, and `Dark`. `Auto` is the default and follows the browser's
`prefers-color-scheme` value, including a color-scheme change while the editor
is open. A user-selected Light or Dark override may be retained locally because
it contains no Favorites or authentication data; editor drafts, workspace
identifiers, confirmation tokens, and owner state still must not be placed in
browser storage.

The selected mode must be applied by a small same-origin bootstrap before the
first rendered paint so opening the editor at night cannot produce a bright
white flash. The document must declare the supported `color-scheme` values and
use semantic color tokens rather than one-off component colors. Both palettes
must preserve clearly distinguishable focus, selection, changed, warning,
error, disabled, and success states without relying on color alone. Native
form controls, scrollbars, dialogs, overlays, code/raw-value presentations, and
empty/loading/error screens are included in this requirement.

Dark mode should use a low-luminance neutral surface rather than forcing pure
black everywhere, while still providing WCAG AA contrast for ordinary text and
interactive controls. Light mode must receive equivalent contrast and state
testing. Browser acceptance must include both explicit modes, Auto under light
and dark system settings, a live Auto transition, reduced-motion and
forced-colors checks, and proof that no light background flashes before the
chosen palette is applied.

### Prepared route-free appearance shell

The package now contains a deliberately unwired browser shell and an exact
asset allowlist for the future editor. The blocking same-origin appearance
bootstrap runs before the stylesheet and supports only `Auto`, `Light`, and
`Dark`. `Auto` follows live `prefers-color-scheme` changes; the one selected
appearance value may use browser-local storage. Semantic tokens cover ordinary,
muted, selected, success, warning, error, disabled, and focus states across
responsive one-, two-, and three-panel layouts, reduced motion, and forced
colors.

The shell contains no route, API request, session owner, source path, workspace
identifier, draft, confirmation token, or execution control. Every workspace
and plan action is disabled and the page identifies itself as a foundation-only
preview. Packaging these assets does not make the editor reachable from the
dashboard or authorize an HTTP adapter. Session-derived ownership, operator-only
admission, logout/revocation cleanup, no-store DTO delivery, CSRF enforcement,
and browser acceptance remain required before a route can expose the shell.

## Acceptance before browser integration

Before a route or UI is added, retain focused proof for owner isolation,
expiry, logout/revocation cleanup, capacity, collision handling, concurrent
same-revision requests, stale-revision refusal, exact review binding, path and
secret non-disclosure, and existing Favorites regression behavior. Browser
acceptance must then cover CSRF/same-origin enforcement, operator-only
authorization, keyboard and screen-reader behavior, responsive layouts,
restart/logout loss of drafts, Light/Dark/Auto behavior without a first-paint
flash, and explicit reviewed execution confirmation.
