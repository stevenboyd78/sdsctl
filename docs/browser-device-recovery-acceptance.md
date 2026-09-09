# Experimental browser recovery acceptance record

This is a development evidence record for [PR #250](https://github.com/stevenboyd78/sdsctl/pull/250),
not an installation procedure or permission to reset a display profile. The
browser-device recovery workflow remains unreleased. Preserve uncertain profiles,
one-use operation records and prior failed attempts; do not repeat initialization
or recovery confirmation to manufacture a passing result.

## Scope and provenance

The September 9, 2026 checkpoint used the small Raspberry Pi's physical 800×480
display and Chromium 151.0.7922.173. A dedicated non-login test account, private
browser directory, private D-Bus session and encrypted fictional keyring separated
the test from the user's desktop. Its endpoint was a fictional loopback listener
that counted and closed connections, not a Home Assistant or authentication server.
Both Pis are bench-test devices; historical service names containing
`production` do not describe their deployment status.

The installed candidate was verified against all 289 packaged runtime files and
the same worker build used by branch head
`fe7b3e98d3d9f03f6b0ba71095f8250ae968a6ac`:

- Private wheel SHA-256:
  `da173eedafb56f75e30aa56350a0544d33d0d3146761188fea48673fc3cc6560`.
- Worker build:
  `ee851c8a66578ad3b1db194a16f1be7c2bdd71e444a8bff9366b6072c6f74351`.

The wheel carries development metadata `0.29.5`; it is **not** the published
PyPI 0.29.5 artifact. Subsequent CI/test/documentation commits did not change
these runtime bytes. Public release acceptance is a separate decision.

## Completed checkpoint

| Check | Observed result | Evidence boundary |
| --- | --- | --- |
| Physical recovery review after worker idle | User confirmation produced a matching durable paused acknowledgement; the bounded browser supervisor exited successfully. | Initial pending state was deliberately seeded in this fictional fixture. The final success sentence disappeared before the user could read it; acknowledgement success is established by the durable record, not a claim that the sentence was read. |
| Host restoration and paused-only guard release | Original canonical host restored; exact release committed; native state remained paused at revision 4. | The retained guard file is expected even after a committed release. Its existence alone does not mean release failed. |
| First ordinary managed browser start | User confirmed the settled paused page, without login prompt or manual reload. | Used the ordinary installed foreground CLI, not the recovery launcher or a storage-seeding extension. |
| Separate ordinary browser restart | A second user confirmation verified the same paused page without manual reload or login. | Recorded only after the first confirmation, graceful browser stop, unchanged proof and a new browser start. |
| Persisted browser readback after shutdown | Exact clean paused state, no device-session cookie and no recovery alarm. | Separate read-only browser API observer on an authenticated virtual display; not another physical startup test. |
| Final shutdown and preservation | Test services exited successfully, no test-account processes or Chromium Singleton markers remained, and the original TUI was visible with an established daemon connection. | Home Assistant, the HDMI Pi and their configuration were not changed by this checkpoint. All test profiles and evidence were retained. |

Both ordinary starts and the final observer recorded **zero endpoint connections**.
Original normal/native/archive/handoff/release evidence bytes and metadata stayed
unchanged, including the native paused revision. Recovery approval, host restoration,
guard release and native suspension were not repeated to obtain the two start passes.

The observation result is `PASS_ORDINARY_PAUSED_STARTS`, with `starts: 2`,
`authentication_connections: 0`, `recovery_approval_repeated: false` and
`native_pause_reapplied: false`. It explicitly records
`browser_storage_readback_tested: false`; that check belongs to the later,
separate `PASS_D_PERSISTED_READBACK` result. The latter confirms clean paused
storage, absent cookie/alarm, unchanged evidence and clean browser shutdown.

The readback extension used the same public extension identity and only storage,
cookie and alarm read APIs. It had no native-messaging permission, background
worker, authentication request or storage-write operation. Its first navigation
needed a fixture-only reload after extension replacement. This is recorded as
`observer_navigation_reloaded: true`; neither accepted ordinary start used a
reload. Browser extension-installation/cache metadata is not claimed byte-identical
after this observer; protected native and recovery evidence is.

## Retained failures and observation corrections

Earlier attempts and their original results remain preserved, not retrospectively
marked successful. Some timed physical attempts expired without an acknowledgement.
The successful recovery attempt then encountered a missing adjacent test-helper
import **after** acknowledgement, host restoration and paused-only release.
Independent read-only checks established those completed boundaries. A separately
packaged continuation added the missing test dependency without changing or
replaying the original recovery attempt.

An initial continuation was stopped after a five-second screenshot showed
`ERR_BLOCKED_BY_CLIENT`. Isolated ordinary Chromium startup subsequently reproduced
that early frame followed automatically by the correct page at 15 seconds. The
single early screenshot did not establish persistent product failure. The stopped
run remains incomplete; it was not relabeled a pass or restarted over its output.

The new continuation captured each start at 5, 15, 30 and 45 seconds. A recent
settled image was inspected before requesting each separate physical confirmation.
Elapsed time or an assistant screenshot alone never supplied a human-pass marker.
Each wait was bounded; expired or failed attempts retained their state without
automatic replay. No product security deadline, browser sandbox, TLS validation
or consent check was weakened. The continuation helpers passed 50 focused tests;
the final readback helper passed 25 tests, including wrong/missing/extra state,
cookie/alarm refusal and populated-output refusal. These are private fixture
tests, not additions to the repository-wide coverage measurement.

## CI evidence and remaining review gates

At the exact head `fe7b3e98d3d9f03f6b0ba71095f8250ae968a6ac`, all 27 executed
remote checks passed; three publication jobs skipped as intended. Python
3.11–3.14 full suites ran for both push and pull-request events, with 7,024 passes,
91 explicit namespace-prerequisite skips and one upstream warning per job.
The separate namespace matrix ran all 99 cases without skips on all eight jobs;
its JUnit gate confirmed 41 launch and 58 guard-release cases. Statement coverage
was 86.57–86.59%, above the unchanged 86% floor. Exact-head CodeQL analyses for
Python, JavaScript/TypeScript and Actions reported zero findings and no analysis
errors; the repository had no open code-scanning alerts when checked.

Those checks belong to that exact head, not to any later documentation commit.
See [the CI run](https://github.com/stevenboyd78/sdsctl/actions/runs/34324509915)
and [CodeQL run](https://github.com/stevenboyd78/sdsctl/actions/runs/34324507532).
This checkpoint adds no runtime changes, release tag, production enrollment,
credential operation, port exposure or Home Assistant catalog change.

The physical recovery/paused-restart evidence gap is now closed **for this
same-build fictional checkpoint**. It does not qualify:

- How a genuinely interrupted ordinary resume creates pending state, or its
  recovery through real server authority and session establishment.
- A coordinated credential-file replacement workflow or cross-release worker,
  browser cache and profile migration.
- Automatic boot, actual power loss or a combined server/display outage.
- Physical HDMI recovery handoff, Firefox/WPE unattended enrollment, or secure
  unattended keyring handling in other deployments.

The next bounded qualification should use a **new isolated profile**, obtain its
state through ordinary setup/sign-out/resume, and interrupt only test-owned work
at a measured boundary rather than injecting pending browser storage. It should
prove pause persists, stale consent cannot authenticate, reviewed recovery ends
clean-but-paused, and later sign-in still requires separate fresh permission.
Design and review that fault boundary before running it; do not repurpose any
retained uncertain profile. Passing this record alone does not mark PR #250 ready
for merge or make the broader unattended-browser feature production accepted.
