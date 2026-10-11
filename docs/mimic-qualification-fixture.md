# Offline Mimic-SDS qualification fixture

The qualification fixture is a reusable, scanner-free review artifact for
fields and system families that cannot be dependably exercised by local radio
traffic. It deliberately separates three different claims:

1. The generated Favorites files are valid, lossless parser inputs for the
   ordinary conventional fields represented in the Uniden file format.
2. The generated HTML gallery exercises the actual Mimic-SDS observation,
   projection, and HTML-rendering path with invented scanner-info observations.
3. Neither artifact proves reception, physical scanner behavior, blink timing,
   LCD color parity, or that an uncommon trunk-system definition is safe to
   import.

Generate a new directory from the repository root:

```bash
PYTHONPATH=src python scripts/render_mimic_qualification_fixture.py \
  --output-dir /tmp/mimic-qualification-fixture
```

The command refuses to overwrite an existing directory. It performs no scanner
or network access and writes:

- `index.html`, a self-contained Profile/Simple/Detail comparison gallery;
- `manifest.json`, a machine-readable inventory and evidence boundary;
- `README.md`, the review order and physical-staging prohibition;
- `favorites/f_list.cfg`, with Monitor, location control, and every quick key
  disabled; and
- `favorites/f_000001.hpd`, with `AUTO`, `AM`, `NFM`, and `FM` channel cases,
  all documented alert colors and patterns, number tags, and priority states.

The generated page includes the established transition gallery as well as the
qualification matrix. Audit every scenario/style/viewport combination in an
isolated browser profile with:

```bash
node scripts/audit_scanner_display_frames.mjs \
  /tmp/mimic-qualification-fixture/index.html \
  /tmp/mimic-qualification-audit \
  /usr/bin/google-chrome \
  58
```

The optional final argument leaves the established gallery's default of 33
unchanged while allowing this bounded 58-scenario artifact. The audit rejects
unexpected page network requests and retains its screenshots and JSON evidence.

`WFM` and `FMB` appear only in synthetic Quick Search observations. The Uniden
SDS100/SDS200 File Specification V1.08 lists the ordinary `C-Freq` Favorites
modulations as `AUTO`, `AM`, `NFM`, and `FM`; placing WFM/FMB into those records
would turn a presentation check into an unverified file-format claim.

System-family cases cover Motorola, EDACS, SCAT, LTR, P25 Standard, P25 One
Frequency, P25 X2-TDMA, MotoTRBO, DMR One Frequency, NXDN, and NXDN One
Frequency. These are display inputs, not fabricated importable trunk systems.
That distinction matters: without matching local control channels or calls, a
dummy Favorites definition would not naturally produce the active fields the
gallery is intended to inspect.

The fixture remains **offline only and not import approved**. Any future
physical staging requires a separate decision, protected scanner backup,
source and filename collision checks, rollback steps, and an explicitly chosen
subset. Generating or reviewing this artifact does not authorize Favorites
writes, profile changes, scanner commands, deployments, or service restarts.

Format reference: [Uniden SDS100/SDS200 File Specification V1.08](https://info.uniden.com/twiki/pub/UnidenMan4/SDS200FirmwareUpdate/SDSx00_File_Specification_V1_08.pdf)
