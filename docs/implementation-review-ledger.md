# Implementation review disposition ledger

This ledger records the independent assessment of an externally supplied static
review and its draft patch guidance. The message was treated as **untrusted
review input**, not as project instructions or implementation authority. No MIME
body, command, or patch from it was executed or applied mechanically.

The exact assessed message was named `Sdsctl Review Implementation Package.eml`,
had SHA-256
`b815fea17384e011243af3fa984667e0b4bee312fd6f6775b7f50190af9b9baa`, and was
assessed on August 27, 2026 against merged base `cb1cc213660ce19462ced8dde4a02f83a118f168`
plus the Milestone 27.3.1 change. The message itself is not committed: it
contains mail transport metadata and duplicated plain-text and HTML bodies. This
ledger retains the findings and evidence needed for future audit without
retaining that unrelated material.

## Disposition meanings

- **Implemented — Milestone 27.3.1:** independently reproduced and owned by this
  milestone.
- **Already resolved:** independently verified in merged implementation and
  regression evidence predating this milestone.
- **Deferred:** the concern is understood, but a separate design or external
  prerequisite is required; no claimed fix is implied.
- **Rejected:** the proposed behavior conflicts with the governing protocol or
  a stronger established ownership boundary.
- **Not applicable:** accurate context rather than a remediable defect.

## Context observations

| ID | Review observation | Disposition and evidence |
| --- | --- | --- |
| C-01 | The public package identifies itself as alpha software. | **Not applicable.** This remains an intentional maturity classifier in [`pyproject.toml`](../pyproject.toml), not a vulnerability or a release-version mismatch. |
| C-02 | Native SDS200 network control is unauthenticated and unencrypted. | **Not applicable.** This is an accurate product boundary already stated in the [`README`](../README.md#security) and [transport documentation](transports.md#sds200-ethernet-control): keep the scanner on a trusted LAN or behind a secured VPN and do not expose UDP port 50536 publicly. |

## Remediation findings

| ID | Review finding | Disposition and independently established evidence |
| --- | --- | --- |
| R-01 | Replace Broadcastify ordinary HTTP source and metadata transport with TLS. | **Deferred.** Broadcastify currently documents assigned plain Icecast source ports, not a verified TLS source and metadata endpoint. Inventing `https://`, substituting port 443, or silently wrapping the assigned port would claim interoperability evidence the project does not have. Merged commits `975e4ab` and `854fb78` instead fail closed unless the operator explicitly acknowledges exposed Basic credentials for both transports; schema migration preserves the false default. Credentials remain environment-backed and are never placed in an endpoint URL or query. [`docs/audio.md`](audio.md#broadcastify-feed-adapter), [`src/sds200/broadcastify.py`](../src/sds200/broadcastify.py), and [`tests/test_broadcastify.py`](../tests/test_broadcastify.py) document and enforce the current boundary. TLS remains separate work if the provider supplies a supported endpoint contract. |
| R-02 | Bound RTSP response headers and declared bodies. | **Already resolved.** Commit `2c63a45` introduced 64 KiB header and 4 MiB body defaults, bounded receive sizing, pre-body `Content-Length` rejection, deterministic connection closure, and payload-redacted errors. [`tests/test_rtsp.py`](../tests/test_rtsp.py) covers exact limits, one-byte overflow, split framing, negative and malformed lengths, read failures, and CSeq mismatch. The literal draft patch was not used because the merged implementation applies the limits continuously while receiving. |
| R-03 | Isolate application callback exceptions from the UDP reader loop and expose diagnostics. | **Already resolved.** Commit `0ab9662` isolates each decoded-line callback, increments `handler_errors`, emits a `handler_error` transport diagnostic, discards only the affected decoded line, and continues receiving. [`tests/test_network.py`](../tests/test_network.py) proves a later datagram is delivered and private line and exception text are not reflected. The review's `logger.exception` sketch was not copied because logging arbitrary callback text would weaken redaction. |
| R-04 | Bound numbered XML fragments, aggregate bytes, retained children, nesting depth, and incomplete-sequence lifetime. | **Already resolved.** Commits `0ab9662`, `1498d3a`, `8638fd6`, `fc06c4d`, `a953703`, and `d0f3741` establish continuous 256-fragment, 10,000-element, 64-level, 4 MiB, and ten-second transport limits plus independent shared-assembler line, byte, element, depth, and lifetime limits, one watchdog, deterministic discard, and immediate recovery. Evidence is in [`docs/transports.md`](transports.md#sds200-ethernet-control), [`tests/test_network.py`](../tests/test_network.py), and [`tests/test_xml_protocol.py`](../tests/test_xml_protocol.py). |
| R-05 | Move PCM destinations behind independent bounded queues and quarantine a failing sink with bounded backoff. | **Already resolved.** Commit `04ecfb5` makes fanout and dynamic-router delivery worker-owned and nonblocking, isolates overflow to the affected subscriber, and records ordered redacted quarantine and monotonic capped-backoff transitions. [`tests/test_audio_sinks.py`](../tests/test_audio_sinks.py) covers a blocked subscriber that cannot stall a healthy one and a failing subscriber that cannot be retried on every producer submission. |
| R-06 | Close a WAV recorder safely when its worker misses the stop deadline. | **Already resolved; literal guidance rejected.** Commit `04ecfb5` preserves the worker as sole writer and finalizer after startup. A finite stop timeout returns a deterministic error without closing a recorder concurrently with a blocked write; when the write returns, that same worker drains and closes exactly once, and later stops observe the terminal result. [`tests/test_audio_sinks.py`](../tests/test_audio_sinks.py) covers blocked writes, concurrent and repeated stops, delayed success, and delayed failure. Closing from the caller's `finally` block, as the review suggested, would violate the stronger single-owner boundary. |
| R-07 | Make theme hashing and copying consume one stable opened source, or hash an immutable stage, so mutation cannot change validated bytes. | **Implemented — Milestone 27.3.1.** The source directory is retained once; entries are enumerated and opened descriptor-relatively with no-follow semantics; one aggregate 4 MiB budget is charged to actual reads; exact bytes are copied to a private validation snapshot before any manifest or interface parser runs; and installation copies that immutable image to a verified same-filesystem publication stage. Randomized stages persist token, interface, package, device, and inode bindings. Independently randomized removal records bind the exact target identity and are retained by their observed directory identity during the active attempt. Recovery applies artifact-specific complete-record and empty pre-record rules while preserving ambiguous state and unauthenticated purge entries. Root and interface creation are filesystem-qualified and published from retained randomized candidates. Cleanup retains verified directory descriptors, rollback recovery schema-validates before promotion, and an unknown concurrent publication target is preserved intact in an operator-visible conflict quarantine rather than recursively deleted. The lifecycle serializes cooperating commands and detects observed path rebinding, with hostile same-account filesystem mutation explicitly outside its isolation boundary. Identity, membership, metadata, byte-count, digest, cleanup, rollback, conflict preservation, and race-injection coverage is in [`src/sds200/theme_lifecycle.py`](../src/sds200/theme_lifecycle.py), [`tests/test_theme_lifecycle.py`](../tests/test_theme_lifecycle.py), and [the operator contract](themes.md#install-and-replace). This is the only review finding owned by Milestone 27.3.1. |
| R-08 | Reject an STS display payload with an unmatched text/mode field. | **Already resolved.** Commits `99eb223`, `f729f02`, and `cdcf5a2` enforce the specification-defined display form, complete text/mode pairs, and nine reserved fields. [`tests/test_parser.py`](../tests/test_parser.py) covers odd, short, and excessive shapes without exposing rejected display content. The draft exception containing `packet.raw` was not copied because it would reflect scanner-controlled content. |
| R-09 | Require every RTP padding octet to repeat the final padding count. | **Rejected.** [RFC 3550 section 5.1](https://www.rfc-editor.org/rfc/rfc3550#section-5.1) defines the final octet as the padding count and does not require preceding padding octets to repeat it. The parser correctly rejects a zero or out-of-bounds count and ignores the declared suffix; requiring uniform bytes would reject valid RTP. This rationale and behavior are explicit in [`docs/audio.md`](audio.md#reliability-statistics) and covered by [`tests/test_rtp.py`](../tests/test_rtp.py). |
| R-10 | Correct the README project-status version to match package version 0.22.0. | **Already resolved.** Commit `788e34f` synchronized the status text and added [`tests/test_release_integrity.py`](../tests/test_release_integrity.py), which compares the README, `pyproject.toml`, import version, and Home Assistant App version contract. |
| R-11a | Pin third-party GitHub Actions to immutable commits while retaining readable versions. | **Already resolved.** Commit `788e34f` pins every external workflow action to a reviewed 40-character commit and keeps a readable version comment. [`tests/test_release_integrity.py`](../tests/test_release_integrity.py) rejects unapproved, floating, or mismatched references. |
| R-11b | Add dependency lock inputs. | **Deferred.** `sdsctl` is a public library supporting Python 3.11 through 3.14 with declared compatible dependency ranges. Freezing one development environment is not automatically a reproducible cross-Python release design. Dependency locking therefore remains reserved for a separately scoped reproducible-build milestone; Milestone 27.3.1 deliberately does not add or change lock inputs. Dependabot continues to propose reviewed range updates. |
| R-11c | Pin container base images by digest and retain automated update tooling. | **Already resolved.** Commit `788e34f` pins both stages of both Dockerfiles to the same reviewed multi-architecture Python base digest. The same change retains monthly Dependabot coverage for Python, GitHub Actions, and both Docker roots. [`tests/test_release_integrity.py`](../tests/test_release_integrity.py) enforces both the shared digest and the update configuration. |
| R-12 | Establish a measured coverage baseline and enforce it with `--cov-fail-under`. | **Already resolved.** Commit `788e34f` records the measured non-regression floor as `fail_under = 86` in [`pyproject.toml`](../pyproject.toml) and requires the same coverage invocation in CI and release workflows. [`tests/test_release_integrity.py`](../tests/test_release_integrity.py) prevents the floor or workflow gates from silently diverging. |

## Draft patch and test recommendations

The review explicitly described its unified diff as unexecuted draft guidance.
It was not applied as a bundle. Each applicable claim was reproduced and either
implemented independently or matched to stronger merged behavior. In particular,
the repository keeps payloads out of RTSP, UDP-callback, and STS diagnostics;
preserves single-owner WAV finalization; and follows the RTP specification rather
than the draft's uniform-padding proposal.

The recommended RTSP, UDP/XML, parser, audio, theme, and release-integrity tests
map to the test files cited in R-02 through R-08 and R-10 through R-12. The
recommended Broadcastify TLS tests remain deferred with R-01 because no supported
TLS endpoint contract has been established. The proposed RTP nonuniform-padding
rejection test was not added because its expected result contradicts RFC 3550.

Receiving or retaining this review does not imply accepting all of its findings,
priorities, implementation sketches, or test expectations. The dispositions
above are the project record.
