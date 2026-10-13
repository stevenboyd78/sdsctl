# Favorites FTP research

This document records transport, security, and storage evidence for the
Favorites FTP read backend and the separate verified additive write executor.
Constructing a write plan or configuration does not authorize scanner-side
mutation; execution remains one explicit call with a private host artifact
destination.

## Scope

The first FTP slice adds a renderer-neutral read-only Favorites storage source
that produces the existing `FavoritesStorageSnapshot` representation. The
transport preserves exact catalog and HPD bytes and does not introduce
FTP-specific semantics into workspace, hierarchy, comparison, editing, or
write-plan layers.

FTP access is intended only for trusted local networks or trusted VPNs. Plain
FTP is not treated as a transport-secure remote-access boundary.

## Credential separation

The SDS200 may be configured with separate read-only and writable FTP accounts.
That separation is part of the storage-safety model.

For the read-only FTP source:

- only an explicitly configured read credential may be resolved;
- a missing or rejected read credential fails closed;
- a writable credential is not accepted by the read configuration and is never
  tried as a fallback;
- password values are referenced by environment-variable name rather than
  stored in ordinary Favorites configuration objects; and
- passwords do not appear in exceptions, logs, diagnostics, snapshots, reports,
  or public API values.

Writable credential resolution belongs only inside the explicit write
preflight and execution workflow. The write password environment-variable
reference must differ from the read reference. The write path never resolves
the read secret and neither credential is a fallback for the other.

## Read-only transport contract

A read-only FTP backend:

1. connects to one explicitly configured scanner host and bounded FTP endpoint;
2. authenticates with the read-only credential only;
3. enters one explicitly configured Favorites directory whose path rejects
   traversal and command-control characters;
4. obtains a bounded deterministic immediate listing;
5. requires one listed `f_list.cfg`;
6. validates every listed name as one immediate non-command-injecting child;
7. retrieves `f_list.cfg` and every immediate `.hpd` candidate as exact bytes;
8. enforces per-file and complete-snapshot byte limits;
9. repeats the complete listing and retrieval pass and requires exact snapshot
   equality; and
10. closes the FTP session deterministically on success or failure.

The backend feeds the existing storage projection unchanged after snapshot
capture. FTP transport failures are not reinterpreted as Favorites schema or
workspace diagnostics.

## Remote-change evidence

FTP does not provide the inode, mount, or filesystem identity available to the
copied-tree and Linux USB backends. Milestone 22.6 therefore does not invent
local-filesystem identity for remote files.

The initial stability rule is intentionally conservative: two consecutive
complete snapshots must have the same safe listing and exact catalog/document
bytes. A mismatch is a stale/changing remote observation and is refused.

Later physical research may establish useful scanner FTP metadata, but a future
write executor must define its own stronger stale-target and concurrency
contract before any mutation.

## Fake transport boundary

Normal automated tests do not require a physical scanner or a network FTP
server. The production adapter sits behind a narrow fixture-friendly session
protocol that can model authentication, listing, exact binary retrieval,
transport interruption, changing remote content, limits, and deterministic
cleanup.

Tests must also verify that the production adapter uses only connect, login,
`cwd`, `nlst`, `RETR`, `quit`, and local close behavior. No upload, delete,
rename, directory creation, permission change, or other mutating FTP command is
part of this milestone.

## Privacy and diagnostics

Favorites programming content is private scanner data. Synthetic fixtures
remain the normal automated-test substrate. Stable failure classes are exposed
without embedding passwords or arbitrary FTP server response text.

## Physical validation

Guarded physical SDS200 validation completed successfully after the automated
suite was clean. The validation used read-side FTP operations only and did not
mutate scanner storage.

Observed evidence:

- the read-only FTP account authenticated successfully on the scanner;
- the Favorites directory exposed by FTP is `/favorites_lists`, not the
  mass-storage path `/BCDx36HP/favorites_lists`;
- the FTP login directory exposed four immediate names but neither
  `f_list.cfg` nor an immediate `favorites_lists` or `BCDx36HP` child;
- `/favorites_lists` exposed exactly one `f_list.cfg` plus 14 immediate `.hpd`
  documents, with no path-bearing or control-character names;
- the production `FavoritesFtpStorageSource` completed its internal two-pass
  exact snapshot verification against the physical scanner;
- the validated snapshot contained 14 HPD documents and 1,392,933 total managed
  bytes, with opaque SHA-256 identity
  `8859af97c23498a7e73431520009af0a0f03e1a613a2734e1a2c7bec84182a50`;
- projecting that exact snapshot through the existing storage layer produced 14
  bindings with zero missing entries, zero ambiguous entries, and zero orphan
  documents; and
- a missing read-secret reference failed before FTP session construction, so no
  writable-credential fallback path was exercised or available.

During diagnostic work, the scanner accepted an initial read-only FTP login but
a rapid second connection attempt was rejected. A single authenticated session
successfully performed the bounded path probe, and the production source also
uses one session for both verification passes. Treat this as observed physical
behavior rather than a guaranteed scanner FTP concurrency contract.

No write credential was required or used for Milestone 22.6 validation. Real
scanner credential values are intentionally not recorded in repository
documentation or fixtures; automated tests continue to use synthetic credential
values.

## Verified additive write boundary

The write preflight accepts an already reviewed FavoritesWritePlan and a
FavoritesFtpWriteConfiguration. It uses only the explicit writable secret and
performs two complete bounded observations in one session. The exact managed
baseline and complete immediate-name listing must be stable and must match the
plan. All final and operation-owned names are checked case-insensitively
because scanner storage may not preserve case distinctions.

The FTP executor is intentionally additive-only:

- existing HPD documents cannot be modified or deleted;
- at most 16 new HPD documents may be added, with a lower configurable bound;
- the intended catalog, each added HPD, complete intended snapshot, staging
  listing, and existing target remain within the read configuration's bounds;
- blocked, ambiguous, invalid, stale, or colliding plans fail before mutation;
  and
- the catalog must change when documents are added.

This limit is a safety property, not a missing convenience feature. It makes
catalog-last activation useful: newly renamed HPDs are unreferenced until the
new catalog is installed, while every previously referenced HPD remains
byte-identical.

## Backup, staging, activation, and recovery

The write executor requires a new absolute operation directory beneath an
existing current-user-owned directory with no group or other access. Before
opening its mutation-capable session, it creates and verifies:

- a target-derived exclusive lock in that private host-state root, preventing
  another cooperating operation using the same root from overlapping;
- an owner-only complete managed baseline backup;
- a canonical rollback manifest binding the endpoint, exact listing,
  baseline/intended snapshot identities, additions, and reserved names; and
- an owner-only operation-report path that is atomically replaced at terminal
  transitions.

Execution then repeats two complete target observations in the same write
session. Only an exact match can proceed. Each HPD and the intended catalog is
uploaded under a collision-resistant operation-owned temporary name and retrieved
byte-for-byte. The executor verifies that the active target still equals the
baseline plus only those owned staging names.

Activation renames each new HPD to its final collision-checked name, preserves
the old catalog under an operation-owned backup name, and installs the staged
catalog last. The complete intended snapshot and exact expected listing must be
stable across two passes before the remote catalog backup is deleted. A second
two-pass observation then proves the final intended snapshot and clean listing.

Any failure after the first possible mutation starts automatic rollback in the
same session. The executor restores the catalog from the verified host backup,
removes every added final HPD and operation-owned temporary file, and requires
the exact baseline snapshot and listing across two passes. A verified rollback
terminates as recovered and still raises an execution error so the intended
change cannot be mistaken for success. If rollback cannot be verified, the
terminal state is recovery_required; the complete host backup and manifest
remain available for manual recovery. A verified final remote state followed
by session-close failure is reported separately as cleanup_required.

FTP cannot provide filesystem inodes, cross-file atomicity, durable server
flush acknowledgements, or a portable advisory lock. The executor therefore
does not claim those guarantees and does not support replacing or deleting
existing HPDs. It prevents silent last-writer-wins behavior with exact
same-session observations immediately before staging and before/after
activation. A concurrent writer that changes managed bytes or immediate names
causes refusal or recovery rather than acceptance.

## Write-side physical status

The automated suite exercises the production STOR, RETR, RNFR/RNTO, DELE, and
cleanup adapter behind a stateful failure-injection transport. It verifies
successful catalog-last activation, pre-mutation drift refusal,
case-insensitive collision refusal, automatic rollback, retained evidence when
rollback cannot be verified, and credential separation.

No physical scanner write is authorized merely by this implementation. A
physical run still requires an independently reviewed exact source, fresh
collision check and target snapshot, private host artifact destination, and an
explicit bounded operator-approved write plan.
