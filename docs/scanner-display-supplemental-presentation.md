# Mimic-SDS clock and Favorites presentation candidate

Status: **internal, offline candidate**. This does not enable ordinary scanner
polling, change the public frame schema, install a theme, or authorize a device
test. The bounded shared-reader qualification and the existing
[live-frame contract](scanner-display-frame-api.md) remain separate.

## Implemented point-in-time view

`present_supplemental_capture()` consumes one immutable
`SupplementalDisplayFrameSet` from the existing owner. It has no scanner
connection, command lane, worker, demand renewal, wall clock, profile-file read,
or state mutation. It returns three profile-bound presentations and two
independently qualified auxiliary values.

All three layouts must share endpoint provenance, profile revision, PSI
sequence/age, indicators and scan family. Only already-qualified conventional
and trunk scan captures are supported. A retained capture is not live authority:
the owner must reacquire after context, session or profile changes.

| Input | Presentation | Missing/expired behavior |
| --- | --- | --- |
| Valid scanner-local DTM reading | Profile-selected small `Day` and `Time` slots only; `Sep21`, `04:12` | Clear those values; retain fresh PSI fields |
| Current global 100-key FQK state | Separate numbered 00–99 diagnostic rows, On/Off/Absent | Omit rows; unknown is not Off or Absent |
| Current PSI | Existing canonical layout, names, fields, colors and indicators | After five seconds, clear live data and indicators |

The fixed English short month is independent of the host locale. The time uses
24-hour HH:MM. No seconds are extrapolated between DTM samples, and no host
timezone or UTC offset is applied. The scanner RTC is a local calendar reading,
not a timestamp with a known timezone. The application-header clock is separate.
This does not claim exact physical scanner clock-format parity.

The clock cannot replace another configured field, an intentionally Empty/blank
slot, an unsupported placement or an invalid source. Accepted profile geometry
and colors remain unchanged. Auxiliary ages advance independently from the
capture's monotonic cutoff and expire at their five-second limits. Fresh PSI
does not freshen an old clock or Favorites sample.

Global FQK states do **not** identify which Favorites decade the physical LCD is
displaying. This implementation does not synthesize F0/S0/D0 rows, select a
Favorites bank from an active channel, issue scoped SQK/DQK reads, or enter AST
mode to retrieve trunk IDs.

## Synthetic preview and renderer tests

From a development checkout with its dependencies installed:

```sh
python scripts/render_scanner_display_supplemental.py \
  --output-dir /absolute/new-preview-directory
```

The script uses invented profile and scanner values only, writes one
`index.html`, and refuses to overwrite an existing file. It has no device,
socket, credentials or user-profile input. Seven scenarios cover current trunk
and conventional scans, independent clock/Favorites expiry, PSI expiry, invalid
RTC and disabled auxiliary data. Plain-text diagnostic notes remain outside the
scanner layout; HTML is escaped and the document's CSP blocks networking.

Offline tests join the real parser/cache/owner capture to the presentation,
check all three layout choices, render through the existing TUI and static HTML,
and use the actual WebUI decoder and generated HA card renderer with a synthetic
DOM. These are **point-in-time rendering checks**, not proof of live delivery,
browser visual appearance, physical continuity or unrestricted polling.

## Required before live consumer integration

The [delivery/expiry candidate](scanner-display-supplemental-delivery.md) now
implements independent sample IDs, a strict internal envelope, and equivalent
Python/JavaScript deadline guards. It is tested offline and not wired into a
public route or live renderer; transport/controller integration remains below.

Do not insert clock text into ordinary schema-1 responses just because the
existing decoders accept that text. Schema 1 only carries the PSI sequence and
age: a newly received PSI frame could otherwise keep an old auxiliary value
visible, or a late response could restore one after it expired.

The next implementation must preserve these requirements:

1. **Explicit acquisition policy.** Ordinary startup remains off. Integrate only
   through the existing owner/serialized command lane, with bounded GETs,
   demand expiry, context refusal and lifecycle invalidation. A read endpoint
   must never create a second radio owner or enable acquisition implicitly.
2. **One coherent capture.** Build the view from a new owner snapshot, not a
   retained frame or a client-provided endpoint. Profile/session changes,
   disconnect, shutdown and mode withholding invalidate affected values.
3. **Independent freshness on the wire.** Define bounded per-source identities,
   sample ordering and ages for clock and Favorites in a versioned opt-in
   contract. Do not reuse PSI sequence as their acquisition identity.
   Raw monotonic timestamps cannot be compared across processes.
4. **Independent consumer deadlines.** Repeated HTTP responses, newer PSI,
   layout changes and hidden/visible cycles must not renew old auxiliary
   samples. Expire locally when the server stops responding; reject delayed,
   out-of-order, wrong-session and conflicting samples. Clear only expired
   auxiliary values while PSI remains current.
5. **Compatibility and privacy.** Keep existing schema-1 consumers working
   unchanged. Update WebUI, TUI and generated HA resources together for any
   opt-in extension. No profile paths, raw XML, raw command packets or unrelated
   profile fields in public responses.
6. **One meaningful acceptance candidate.** First prove reconnect, mode change,
   profile replacement, contention, media coexistence and delayed-response
   behavior offline. Then stage a source-pinned candidate and request fresh
   operator readiness for the actual clock/layout/continuity checks. Never
   rearm or reuse a closed hardware trial.

There is intentionally no new CLI flag, App option, public route or enable
setting documented here. Those must follow their actual implementation and
qualification rather than being inferred from this internal preview.
