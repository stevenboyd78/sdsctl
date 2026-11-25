# Favorites web workspace foundation

The Favorites web workspace is an experimental server-side foundation for a
future browser editor. It reuses the existing lossless Favorites parser,
navigation model, in-memory editor, validation, exact write plan, and
confirmation token. It does not add an HTTP route, a browser UI, a write
executor, scanner discovery, or target selection.

The first slice intentionally stops at exact-plan review. It is safe to use in
tests and future server adapters with copied Favorites data, but it does not
authorize a scanner, USB, FTP, or production-tree write.

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

- an HTTP or WebSocket endpoint;
- HTML, CSS, or JavaScript;
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

## Acceptance before browser integration

Before a route or UI is added, retain focused proof for owner isolation,
expiry, logout/revocation cleanup, capacity, collision handling, concurrent
same-revision requests, stale-revision refusal, exact review binding, path and
secret non-disclosure, and existing Favorites regression behavior. Browser
acceptance must then cover CSRF/same-origin enforcement, operator-only
authorization, keyboard and screen-reader behavior, responsive layouts,
restart/logout loss of drafts, Light/Dark/Auto behavior without a first-paint
flash, and explicit reviewed execution confirmation.
