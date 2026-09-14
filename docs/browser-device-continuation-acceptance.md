# Browser continuation candidate acceptance

Status: **unreleased, isolated acceptance only**. This record describes the
private candidate at commit `34f7871849edf89fe54991936a424761752f04f8` and its
September 13, 2026 checks. It is not an installation guide, a release receipt or
permission to modify an existing browser profile. See the
[operation boundary](browser-device-continuation-operation.md) and
[release process](releasing.md).

## Candidate identity

- Private wheel SHA-256:
  `c830be2f34b0aa2781b2c87e1e9e9a6acf4be1077915fdb9a0df910e9120e981`.
- Worker/native graph:
  `034f095b279d11b49470c63a8ae8a7a00431443034bd48de642543eafe292124`.
- 321 package files and 24 graph assets verified against source and installation.
- The wheel's development metadata reads `0.29.5`. It is **not** the published
  `0.29.5` artifact and must never replace it. A future publication needs a new
  reviewed version and matching release artifacts.

The candidate composes ordinary continuation selection, document-bound consent,
initial installation, fresh accepted verification, independent native pause and
durable browser Stop, same-origin logout, persistent result presentation and the
foreground public launcher. It also includes the separately tested LCARS field
clipping correction and deferred public API loading for focused native helpers.

## Automated verification

- Full isolated installed-wheel pytest run: **10,891 passed**, with exact
  collected test identities and no failures, errors or skips across four shards.
- Full browser JavaScript suite: **4,387 passed**. The 889 focused lifecycle/Stop
  tests are included in that number, not an additional population.
- Focused installed Python run: 215 passed; these overlap the full pytest suite.
- Eighteen supplementary cold-worker race/fault model checks passed, separately
  from the browser suite. These are models, not physical browser observations.
- Ruff and MyPy passed; MyPy checked 236 source files.

Counts describe this frozen candidate, not every future combined PR head. They
must not be added together as unique tests or presented as 100% code coverage.

## Fresh real-Chromium Pi cases

Each case used a new private profile, a fictional loopback TLS server, isolated
trust and the actual installed public CLI. Neither personal browser state nor
Home Assistant credentials were used. No debugger attachment or insecure
Chromium flag was used. These six cases used software-driven input on private
virtual displays, not human visual acceptance.

| Case | HDMI-class Pi | Small-display Pi |
| --- | --- | --- |
| Initial sign-in, 75-second idle, complete logout, stopped restart | Passed | Passed |
| Accepted startup, 75-second idle, complete logout, stopped restart | Passed | Passed |
| Delayed fault and stopped restart | Passed: actual server session drain / HTTP 202 | Passed: malformed acknowledgement after server pause |

The successful cases required exactly one logout POST, paused server generation
2, a persistent complete message, and no fresh authentication or retained-state
change on stopped restart. Fault cases retained their pending/unconfirmed
messages and did not retry. The malformed acknowledgement case explicitly
corrupted a response body; it is not evidence of a physical network outage.
The two fault modes were not each tested on both hosts.

The original delayed-logout failure and initial observer-failed attempts remain
retained. Fresh corrected attempts do not retroactively turn failures into passes.

## Focused physical HDMI acceptance

A separate fresh candidate profile used the actual public CLI, Chromium
152.0.7977.75 on Debian 13/aarch64, and the physical 1920x1080 Wayland seat.
The user explicitly confirmed all four stages:

1. Paused startup page, before any sign-in attempt.
2. Explicit review/consent, resume and dashboard delivery.
3. Persistent complete sign-out after 75.1175 seconds of idle time.
4. Browser restart remaining stopped instead of signing in again.

The controller required exactly one same-origin logout POST/HTTP 200, server
pause at generation 2, normal owned-browser shutdown, zero new authentication
after restart and unchanged retained state. It exited successfully. The test
service, owned processes and loopback listener were stopped, and both normal TUI
services were verified active on their normal consoles. The earlier failed
profile and all acceptance evidence were preserved. Audio was not started.

This human check qualifies initial resume and idle logout through the public
launcher. It does not extend the physical claim to accepted startup, all fault
modes, the small display, or a Home Assistant deployment of the candidate.

## Remaining boundaries

- Natural worker suspension was not independently observed. A cold-worker model
  and no-debugger idle tests corroborate the failure/fix without proving a
  particular browser lifecycle event.
- No abrupt power-loss, production keyring, Chromium upgrade, Firefox or
  WPE WebKit acceptance is claimed here.
- Native-helper TLS verification and browser trust remain separate requirements.
- Unknown issuance or lost acknowledgements remain terminal retained evidence,
  never permission to delete state, initialize again or retry sign-out.
- The combined source review, hosted CI and versioned release process remain
  separate gates. No package was published or normal installation upgraded by
  this acceptance work.
