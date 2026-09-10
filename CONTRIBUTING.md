# Contributing

Thank you for helping improve `sdsctl`.

The project is hardware-facing alpha software, so contributions should preserve
a clear separation between transport behavior, protocol parsing, scanner state,
and user interfaces.

## Before opening an issue

Search existing issues and review [SUPPORT.md](SUPPORT.md). Include the project
version, Python version, operating system, scanner firmware, connection type,
and a minimal reproduction.

Remove private or location-sensitive information from traces before posting.
Scanner output can contain system names, channel names, IP addresses, and unit
identifiers.

Security vulnerabilities should follow [SECURITY.md](SECURITY.md), not a public
issue.

## Development setup

```bash
git clone https://github.com/stevenboyd78/sdsctl.git
cd sdsctl

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Required checks

Run these before submitting a pull request:

```bash
ruff check .
mypy src/sds200
pytest
python scripts/check_docs.py
python -m build
python -m twine check dist/*
```

The automated test suite must run without scanner hardware.

### Linux browser process-isolation checks

The browser recovery-launch, paused-guard-release, continuation-intent and retained-history
tests use real Linux PID
namespaces with a fictional browser executable. They require a non-root test
user, Linux `pidfd` support, and working `bubblewrap` (`bwrap`). They do not
authenticate to a dashboard or replace real Chromium/display acceptance.

On a compatible Debian/Ubuntu development machine, install `bubblewrap` with
the system package manager. The tests may skip when these platform prerequisites
are unavailable locally. Separate GitHub namespace jobs for Python 3.11–3.14
install `bubblewrap` and require all four modules to execute without skips; a green
result must not depend on silently omitting these checks. Reproduce this gate with:

```bash
namespace_evidence=$(mktemp -d /tmp/sdsctl-namespace-check.XXXXXX)
python scripts/run_browser_namespace_tests.py --report="$namespace_evidence/results.xml"
python scripts/check_browser_namespace_results.py "$namespace_evidence/results.xml"
```

The runner launches two independent pytest processes on the same machine: the
launch, guard-release and continuation-intent modules form one batch, and retained
history forms the other. Every module still runs in full. Both batches must exit
successfully, with no skipped, failed, duplicate or missing-module results, before
the runner publishes the combined report. It preserves individual logs and reports
in the printed evidence directory and never overwrites an earlier report. Leave
`PYTEST_ADDOPTS` unset; hidden selection options are refused.

This scheduling keeps the existing 25-minute CI job limit, with a 23-minute limit
for the two batches together. It does not extend test deadlines or weaken the
namespace prerequisite checks. The separate result check must follow a successful
runner invocation and use that invocation's report.
If namespace creation is blocked by host policy, report the prerequisite failure;
do not disable AppArmor, relax kernel settings, run the tests as root, or bypass
browser sandboxing to make the gate pass. This execution requirement is separate
from the project's coverage-percentage target.

The full-suite/coverage jobs remain on `ubuntu-latest`. Its current hosted image
refuses unprivileged `bwrap` UID mapping even after the package is installed, so
the dedicated namespace gate uses `ubuntu-22.04` without policy overrides. This is
a bounded runner choice, not a production OS recommendation: [GitHub's runner
notice](https://github.com/actions/runner-images/issues/14254) schedules brownouts
from 2027-03-23 and removal on 2027-04-17. Qualify a replacement before the first
brownout rather than removing the gate or weakening the runner's security policy.

## Project structure

- `src/sds200/transport.py`: transport contract and USB serial transport
- `src/sds200/network.py`: UDP transport and network XML datagram handling
- `src/sds200/fallback.py`: ordered control-transport activation and failover
- `src/sds200/audio.py`: control-independent audio lifecycle contracts
- `src/sds200/scanner.py`: model names, aliases, capabilities, and limits
- `src/sds200/commands.py`: typed command objects
- `src/sds200/parser.py`: CR-delimited response parsing
- `src/sds200/xml_protocol.py`: scanner-information XML parsing
- `src/sds200/state.py`: synchronized state and change detection
- `src/sds200/cli.py`: command-line interface
- `tests/`: hardware-independent regression tests
- `examples/`: focused usage examples
- `docs/`: architecture and operational guidance

## Adding protocol support

When adding or changing a scanner command:

1. Preserve the raw protocol response in a trace or sanitized fixture.
2. Add a typed command or response model when practical.
3. Keep transport-specific framing out of high-level command code.
4. Add positive, malformed-response, timeout, and validation tests.
5. Document hardware validation separately from simulated tests.
6. Avoid assuming that USB and UDP return identical framing.

Do not add tests that require a live scanner or a specific LAN.

## Hardware validation

A pull request may include optional manual validation notes:

```text
Scanner: SDS100 / SDS150 / SDS200
Firmware: ...
Python: 3.14.x
Transport: USB / UDP (SDS200 only)
Commands tested: ...
Observed result: ...
```

Sanitize system, department, channel, unit, and network information before
sharing logs publicly.

## Pull requests

Keep pull requests focused. Describe:

- What changed
- Why it changed
- Tests added or updated
- Local check results
- Hardware validation, when applicable
- Compatibility or security implications

Update documentation and `CHANGELOG.md` when behavior or public APIs change.

By participating, you agree to follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
