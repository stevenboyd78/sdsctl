# Bounded supplemental acquisition candidate

Status: **internal, offline-tested, default-off**. This is not continuous
production activation or a new Home Assistant configuration option.

The [browser](scanner-display-supplemental-browser-negotiation.md),
[TUI](scanner-display-supplemental-tui.md) and
[HA card](scanner-display-supplemental-ha.md) candidates can consume cached data.
Acquiring that data is a separate operation. This module establishes that
boundary without changing any installed App, display, credentials or scanner.

## Explicit demand instead of ordinary display polling

`DaemonDisplayFrames` accepts the internal `legacy_snapshot_demand` policy.
Its default preserves existing research fixtures/launchers; ordinary daemon
startup still supplies no supplemental cache and therefore remains passive.
The new acquisition owner always selects `legacy_snapshot_demand=False`.

In that mode, ordinary `snapshot()`, supplemental snapshots, context negotiation
and frame delivery neither renew a read lease nor cancel another consumer's
lease. No API polling rate can independently multiply scanner commands.

`renew_supplemental_demand()` requires an existing capture from the same endpoint,
stream, connection, accepted profile, profile-invalidation and cache context.
It rechecks current PSI, profile/source health and quarantine before atomically
granting one shared five-second lease. Only global Favorites plus clock caches
qualify; scoped SQK/DQK acquisition is refused.

The first lease, or renewal after expiry, retires old samples and advances the
context revision. The returned revision must be used for the next context/frame
exchange. Concurrent callers using the previous revision cannot all recreate
that lease; callers using the current revision coalesce into the existing lease.
Renewal does not reset per-source scheduling, minimum gaps or error backoff.
None of these methods performs scanner I/O on the calling thread.

This is an internal boundary, **not authorization**. The public cached-delivery
service still has no demand operation. Future exposure must be an explicitly
advertised, authenticated mutation, never an implicit side effect of a GET.
An acknowledgment lost after lease creation would mean an uncertain but bounded
outcome, not proof that no read occurred; it must not permit blind rearming.

## One finite acquisition owner

`DaemonSupplementalAcquisition` constructs one feed/cache/worker on the existing
native `DaemonRuntime` and exact `SDS200` owner. It requires direct POSIX UDP,
refuses substituted/wrapped transports and file tracing, and uses the existing
bounded native command path. It creates no scanner connection or audio session.

The lifecycle is deliberately separate:

1. **Construct:** validate policy and claim the runtime. No subscriptions or I/O.
2. **Start:** subscribe to existing events and start the one optional worker.
   No read is possible yet.
3. **Arm once:** reserve the current runtime briefly and verify its already
   probed model/firmware, connection, PSI and idle Waterfall state. No extra
   identity probe or scanner command is sent.
4. **Renew explicit demand:** require a same-context capture and current profile.
   The worker may now attempt only global `FQK` GET and `DTM` GET.
5. **End/close:** expire or invalidate demand; close removes owned subscriptions
   and stops the optional worker without stopping the scanner/runtime.

Policy requires an exact firmware string, a window greater than zero and no
longer than 75 seconds, and a quota of 1–150 read opportunities. These are
implementation ceilings for a supervised candidate, **not hardware acceptance
of every duration/quota or firmware value**. A test case must choose its own
reviewed limits; offline fixtures use six opportunities. The window starts at
arming and cannot be extended by client demand.

The existing 250 ms command budget, 500 ms minimum read gap, two-second source
refresh interval and failure/backoff rules remain in force. Each reserved read
opportunity spends quota even if the native command lane ultimately declines
the write. Busy runtime/Waterfall reservations yield before spending quota.
Foreground scanner commands are never queued by this reader; existing busy
behavior applies while its one bounded read is already in progress.

Model/firmware, exact owner/transport and tracing policy are checked again for
each opportunity. State-lock contention yields instead of pretending the
identity changed. Disconnect, qualification loss, quota exhaustion and window
expiry end the window; none can automatically rearm it. A delayed renewal or
reply cannot restore data after the window ends.

Only one acquisition object may ever claim a given runtime object. Closing it
releases active ownership but retains the used marker, preventing a new cache
on the same runtime from evading a timed-out reader's connection quarantine.
A future trial requires a newly constructed runtime and a separately reviewed
case. Closed hardware cases must not be reused.

## Evidence and remaining work

Offline tests cover concurrent renewals, replayed contexts, profile failure and
repair, source mismatch, stale/missing PSI, quarantine, lease expiry, finite
quota/window, startup and shutdown. Native localhost UDP tests execute the real
bounded write path and runtime reservations, including reconnects, controls,
synthetic PCM delivery and continuing PSI while a reply is withheld. They are
not evidence of physical scanner behavior or audible browser/recording quality.

Remaining: wire the explicit authenticated demand operation and matching client
behavior, then prepare an opt-in source-pinned launcher with independent cleanup
and restoration. Public startup/schema/client activation remains disabled.
User-facing continuous operation needs its own qualification; a bounded trial
must not silently become a permanent polling loop. The separate saved-recording
Pause/browser timeout issue remains open.
