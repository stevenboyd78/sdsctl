# Python 3.15 qualification

Python 3.15 support is **not yet claimed**. The package metadata, README badge,
CI matrix and release containers continue to name Python 3.11 through 3.14.
This record defines the bounded evidence required before those public contracts
may change.

## Upstream baseline

Python 3.15.0 was released on October 9, 2026. The current stable maintenance
release used for this plan is Python 3.15.2. The authoritative sources are:

- [Python 3.15 What's New](https://docs.python.org/3.15/whatsnew/3.15.html)
- [PEP 790: Python 3.15 release schedule](https://peps.python.org/pep-0790/)
- [Python documentation by version](https://www.python.org/doc/versions/)

Qualification must use an exact stable CPython 3.15 maintenance release and
record the full `python --version` output. A release candidate, development
snapshot or free-threaded build does not qualify the ordinary CPython runtime.
Free-threaded (`3.15t`) support remains a separate decision and test matrix.

Python 3.15 changes several assumptions that deserve explicit coverage:

- UTF-8 is now the default encoding. Tests must still cover explicit scanner,
  Favorites, configuration and recording encodings instead of relying on the
  process default.
- Package startup configuration adds `.start` files and deprecates executable
  import lines in `.pth` files. Installed-wheel and editable-install startup
  must both be checked.
- `argparse` output can use color and suggestions. CLI snapshot and terminal
  behavior must be exercised with color enabled and disabled.
- Lazy imports are opt-in. Qualification must use the normal eager-import mode;
  a future lazy-import claim needs its own compatibility review.

## November 21 dependency preflight

A download-only, no-install probe evaluated the declared dependency ranges for
CPython 3.15 on both `x86_64` and `aarch64`. The probe used pip's foreign-target
resolver with the `cp315` ABI and the forward-compatible
`manylinux_2_17_<architecture>` platform tag. It did not execute downloaded
code or change a host environment.

| Scope | x86_64 | aarch64 | Resolved files |
| --- | --- | --- | ---: |
| Core package dependencies | Pass | Pass | 6 per architecture |
| Runtime extras excluding playback | Pass | Pass | 25 per architecture |
| Playback extra | Pass | Pass | 3 per architecture |
| Complete development extra | Pass | Pass | 62 per architecture |

The runtime scopes cover the declared base dependencies plus the TUI, web,
MQTT and playback extras. The development scope covers build, test, type,
format, packaging and documentation tools declared by the project.

An initial probe using the exact synthetic `manylinux_2_28` platform string
could not select Ruff's and CFFI's older-compatible wheel tags. Repeating the
probe with the wheels' `manylinux_2_17` compatibility floor resolved every
scope on both architectures. This is retained as resolver-model evidence, not
reported as a project or dependency failure.

This preflight establishes that compatible published artifacts can currently
be selected. It does **not** prove imports, runtime behavior, native library
availability, tests, browser behavior, packaging, containers or hardware.

## Runtime acceptance gates

Use a clean CPython 3.15.2 environment with no packages inherited from another
interpreter. Preserve machine-readable logs and exact dependency versions.

1. Confirm runtime identity and encoding behavior:

   ```bash
   python --version
   python -c 'import json, locale, platform, sys; print(json.dumps({"executable": sys.executable, "implementation": platform.python_implementation(), "version": sys.version, "default_encoding": sys.getdefaultencoding(), "filesystem_encoding": sys.getfilesystemencoding(), "preferred_encoding": locale.getpreferredencoding(False)}, sort_keys=True))'
   ```

2. Install the checkout with every runtime and development extra in a new
   virtual environment:

   ```bash
   python -m pip install --upgrade pip
   python -m pip install -e ".[all,dev]"
   python -m pip check
   ```

3. Run static, documentation and complete serial test gates:

   ```bash
   ruff check .
   ruff format --check .
   mypy src/sds200
   python scripts/check_docs.py
   pytest --cov=sds200 --cov-report=term-missing
   ```

4. Run the complete browser namespace and deterministic screenshot gates with
   the same supported Node.js and real-Chrome requirements used by CI:

   ```bash
   python scripts/generate_web_dashboard_screenshots.py --verify-gallery
   node scripts/audit_web_dashboard_browser.mjs --startup-timeout-ms 90000 --timeout-ms 30000
   python scripts/generate_web_dashboard_screenshots.py --verify-repeatability \
     --only theme-system-1920x1080.png \
     --only theme-system-waterfall-1920x1080.png \
     --only theme-pip-boy-inspired-waterfall-800x480.png
   ```

5. Build and inspect isolated release artifacts:

   ```bash
   python -m build
   python -m twine check dist/*
   python scripts/generate_web_dashboard_screenshots.py --verify-sdist dist
   python -m zipfile -l dist/sds200-*.whl
   ```

6. Install the built wheel, not the source tree, into a second clean Python
   3.15.2 environment. Re-run CLI version/help, representative import, daemon,
   configuration, web, TUI and playback smoke tests from outside the checkout.

7. Build the generic and Home Assistant containers for both `linux/amd64` and
   `linux/arm64`. Verify imports and startup in the resulting images without
   publishing them. Changing a base image requires the normal exact-digest and
   multi-architecture review; a readable `python:3.15-slim` tag alone is not
   sufficient evidence.

8. Add Python 3.15 to the pull-request and push CI matrices and require a clean
   automatically created run. Keep Python 3.11 through 3.14 until a separately
   reviewed support-policy decision removes one.

## Support-declaration boundary

Only after every gate above is green on the exact candidate may one reviewed
change update all of these surfaces together:

- `pyproject.toml` classifiers;
- README support badge and prose;
- CI Python matrices;
- release and installation documentation;
- container base tags and exact digests, if the release containers move; and
- changelog/release notes.

`requires-python = ">=3.11"` already permits installation on newer Python
versions. That broad lower bound is not evidence that Python 3.15 has been
qualified. Until the coordinated support change lands, failures on 3.15 are
useful qualification findings but not regressions against the published
3.11–3.14 support contract.

No production service, Raspberry Pi, Home Assistant instance, scanner, package
index, container registry or release artifact is changed by this plan.
