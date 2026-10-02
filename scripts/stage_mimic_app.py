#!/usr/bin/env python3
"""Stage/verify a source-pinned, manually started LOCAL Mimic-SDS acceptance App.

Never edits the published catalog, installs/starts an App, copies user options,
initializes profiles, or contacts Home Assistant. The destination must be new.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLUG = "sds200_mimic_acceptance"
APP = "home-assistant/sds200/"
PUBLIC_OPTIONS = {
    "scanner_host",
    "mqtt_topic_prefix",
    "recording_directory",
    "remote_daemon_enabled",
    "native_dashboard_enabled",
    "advanced_access_server_name",
    "advanced_access_host_address",
}
CONFIGURATION_NOTE = """  scanner_display_config:
    name: Candidate display-profile deployment
    description: >-
      Unreleased acceptance only. Leave empty to keep Mimic-SDS disabled.
      Otherwise select an already provisioned private deployment TOML for
      this scanner. This does not initialize, import or repair profile state.
"""
FRONT_PANEL_KEY_NAMES = dict(
    zip(
        "MFL1234567890.E><^VQYABCZTR",
        (
            "menu",
            "function",
            "avoid",
            "digit-1",
            "digit-2",
            "digit-3",
            "digit-4",
            "digit-5",
            "digit-6",
            "digit-7",
            "digit-8",
            "digit-9",
            "digit-0",
            "dot-no",
            "enter-yes",
            "rotary-right",
            "rotary-left",
            "rotary-push",
            "volume-push",
            "squelch-push",
            "replay",
            "soft-1",
            "soft-2",
            "soft-3",
            "zip",
            "service-type",
            "range",
        ),
        strict=True,
    )
)


def git(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, timeout=30
    ).stdout


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError("Candidate source contract changed; review the staging adapter.")
    return text.replace(old, new, 1)


def render(
    snapshot: dict[str, bytes],
    revision: str,
    *,
    research_firmware: str | None = None,
    display_read_firmware: str | None = None,
    display_read_kind: str | None = None,
    front_panel_firmware: str | None = None,
    front_panel_key: str | None = None,
    front_panel_mode: str | None = None,
    front_panel_screen: str | None = None,
    supplemental_firmware: str | None = None,
    supplemental_continuity: bool = False,
    supplemental_timing: bool = False,
    supplemental_transition_wait: bool = False,
    supplemental_bounded_writes: bool = False,
) -> dict[str, bytes]:
    validate_research_choice(
        research_firmware,
        display_read_firmware,
        display_read_kind,
        front_panel_firmware,
        front_panel_key,
        front_panel_mode,
        front_panel_screen,
        supplemental_firmware,
        supplemental_continuity,
        supplemental_timing,
        supplemental_transition_wait,
        supplemental_bounded_writes,
    )
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("An exact source revision is required.")
    version = tomllib.loads(snapshot["pyproject.toml"].decode())["project"]["version"]
    if not isinstance(version, str) or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise ValueError("Review candidate version for this source baseline.")
    for name in (
        "daemon_display_frames.py",
        "scanner_display_deployment.py",
        "scanner_display_ingress.py",
        "themes/home-assistant/mimic-sds/sds200-mimic-card.js",
    ):
        if "src/sds200/" + name not in snapshot:
            raise ValueError("Selected runtime lacks the required Mimic-SDS components.")
    manifest = snapshot[APP + "config.yaml"].decode()
    schema = manifest.partition("schema:\n")[2]
    options = manifest.partition("options:\n")[2].partition("schema:\n")[0]
    translations = snapshot[APP + "translations/en.yaml"].decode()
    field_pattern = r"^  ([a-z][a-z0-9_]*):"
    if (
        set(re.findall(field_pattern, schema, re.MULTILINE)) != PUBLIC_OPTIONS
        or set(re.findall(field_pattern, options, re.MULTILINE))
        != PUBLIC_OPTIONS - {"scanner_host"}
        or set(re.findall(field_pattern, translations, re.MULTILINE)) != PUBLIC_OPTIONS
    ):
        raise ValueError("Published option contract changed; explicit review required.")
    for old, new in (
        ('name: "sds200"\n', 'name: "sds200 Mimic-SDS acceptance"\n'),
        (f'version: "{version}"\n', f'version: "{version}-mimic-{revision[:12]}"\n'),
        ('slug: "sds200"\n', f'slug: "{SLUG}"\n'),
        ('image: "ghcr.io/stevenboyd78/sds200-home-assistant"\n', ""),
        ("boot: auto\n", "boot: manual\n"),
        ("  50000/udp: 50000\n", "  50000/udp: null\n"),
        ('panel_title: "sds200"\n', 'panel_title: "sds200 Mimic-SDS acceptance"\n'),
        ('  mqtt_topic_prefix: "sdsctl"\n', '  mqtt_topic_prefix: "sdsctl-mimic-acceptance"\n'),
        (
            '  recording_directory: "sdsctl/recordings"\n',
            '  recording_directory: "sdsctl-mimic-acceptance/recordings"\n',
        ),
        ("schema:\n", '  scanner_display_config: ""\nschema:\n'),
    ):
        manifest = replace_once(manifest, old, new)
    manifest += '  scanner_display_config: "str?"\n'
    translations += CONFIGURATION_NOTE
    result = {
        name: data
        for name, data in snapshot.items()
        if name.startswith("src/") or name in {"pyproject.toml", "README.md", "LICENSE"}
    }
    for name in ("Dockerfile", "icon.png", "logo.png"):
        result[name] = snapshot[APP + name]
    # Keep the proven pinned monorepo Dockerfile and its source build context.
    result["config.yaml"] = manifest.encode()
    result["translations/en.yaml"] = translations.encode()
    result[".dockerignore"] = b".git\n__pycache__\n*.pyc\n"
    result["DOCS.md"] = (
        "# Local Mimic-SDS acceptance only\n\n"
        f"Source: `{revision}`. Package metadata: `{version}` (NOT a published upgrade).\n\n"
        "Manual start only. Before starting, stop the installed scanner-owning App. "
        "Keep separate MQTT/recording defaults; never run two scanner owners. "
        "All host ports default to unmapped. If testing audio, map UDP 50000 only "
        "during the reviewed switchover after stopping the installed App. "
        "Do not copy credentials or enable advanced listeners as an installation side effect.\n\n"
        "The empty scanner_display_config default leaves the feature disabled. "
        "Provision and review the private manifests and accepted state before opting in. "
        "Changing this option requires an App restart. Never add it to the published App.\n\n"
        "Rollback: stop this candidate, restore any explicitly changed card resources, "
        "then start the unchanged installed App. Preserve candidate profile/recordings "
        "for review. No automatic deletion or migration is performed.\n"
    ).encode()
    report = {
        "schema_version": 1,
        "purpose": "local-mimic-acceptance-only",
        "source_revision": revision,
        "package_version": version,
        "app_slug": SLUG,
        "automatic_start": False,
        "profile_enabled_by_default": False,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(result.items())},
    }
    if research_firmware is not None:
        if (
            re.fullmatch(r"[A-Za-z0-9._ -]{1,64}", research_firmware) is None
            or research_firmware != research_firmware.strip()
        ):
            raise ValueError("Review the exact research firmware pin.")
        runtime_name = "src/sds200/home_assistant_app_runtime.py"
        runtime_source = result[runtime_name].decode()
        before, delimiter, after = runtime_source.partition(
            "def build_home_assistant_daemon_command("
        )
        if not delimiter:
            raise ValueError("Review the daemon launcher boundary.")
        signature, end, body = after.partition(") -> tuple[str, ...]:")
        if not end:
            raise ValueError("Review the daemon launcher signature.")
        signature = replace_once(
            signature,
            "executable: str = HOME_ASSISTANT_APP_EXECUTABLE",
            'executable: str = "/usr/local/bin/sdsctl-system-status-research"',
        )
        result[runtime_name] = (before + delimiter + signature + end + body).encode()
        result["research_system_status_daemon.py"] = snapshot[
            "scripts/research_system_status_daemon.py"
        ]
        result["research-entry.py"] = (
            "#!/usr/local/bin/python\n"
            "import os, sys, uuid\n"
            "sys.path.insert(0, '/opt/sdsctl-research')\n"
            "from research_system_status_daemon import main\n"
            "directory = ('/run/sdsctl/system-status-research-' "
            "+ str(os.getpid()) + '-' + uuid.uuid4().hex)\n"
            f"raise SystemExit(main(['--expected-firmware', {research_firmware!r}, "
            "'--evidence-directory', directory, '--', *sys.argv[1:]]))\n"
        ).encode()
        result["Dockerfile"] += (
            b"\n# Temporary, explicit operator-triggered System Status research only.\n"
            b"COPY research_system_status_daemon.py /opt/sdsctl-research/\n"
            b"COPY --chmod=0555 research-entry.py /usr/local/bin/sdsctl-system-status-research\n"
        )
        result["config.yaml"] = replace_once(
            result["config.yaml"].decode(),
            f'version: "{version}-mimic-{revision[:12]}"',
            f'version: "{version}-mimic-{revision[:12]}-ast-research"',
        ).encode()
        result["DOCS.md"] += (
            b"\n## Temporary System Status research\n\n"
            b"This opt-in image pins one SDS200 firmware and requires direct UDP. "
            b"No research command runs on startup. The daemon waits ten minutes for "
            b"an administrator SIGUSR1 trigger while the operator is at the scanner. "
            b"Inspect fresh private ready.json and verify PID plus process start ticks "
            b"before signaling. One attempt only; never retry an uncertain start. "
            b"Use the physical to Scan soft key for return. No APR/remote stop is sent. "
            b"Preserve the private result and restore the normal acceptance image afterward.\n"
        )
        report["purpose"] = "local-mimic-system-status-research-only"
        report["research_firmware_pin"] = research_firmware
        report["research_automatic_start"] = False
        report["files"] = {
            name: hashlib.sha256(data).hexdigest() for name, data in sorted(result.items())
        }
    if display_read_firmware is not None:
        runtime_name = "src/sds200/home_assistant_app_runtime.py"
        before, delimiter, after = (
            result[runtime_name].decode().partition("def build_home_assistant_daemon_command(")
        )
        signature, end, body = after.partition(") -> tuple[str, ...]:")
        if not delimiter or not end:
            raise ValueError("Review the daemon launcher boundary.")
        signature = replace_once(
            signature,
            "executable: str = HOME_ASSISTANT_APP_EXECUTABLE",
            'executable: str = "/usr/local/bin/sdsctl-display-read-research"',
        )
        result[runtime_name] = (before + delimiter + signature + end + body).encode()
        for name in ("research_system_status_daemon.py", "research_display_read_daemon.py"):
            result[name] = snapshot["scripts/" + name]
        result["research-entry.py"] = (
            "#!/usr/local/bin/python\nimport os, sys, uuid\n"
            "sys.path.insert(0, '/opt/sdsctl-research')\n"
            "from research_display_read_daemon import main\n"
            "directory = ('/run/sdsctl/display-read-research-' "
            "+ str(os.getpid()) + '-' + uuid.uuid4().hex)\n"
            f"raise SystemExit(main(['--expected-firmware', {display_read_firmware!r}, "
            f"'--read-kind', {display_read_kind!r}, "
            "'--evidence-directory', directory, '--', *sys.argv[1:]]))\n"
        ).encode()
        result["Dockerfile"] += (
            b"\n# Explicit one-shot GET research, no automatic reads.\n"
            b"COPY research_system_status_daemon.py research_display_read_daemon.py "
            b"/opt/sdsctl-research/\n"
            b"COPY --chmod=0555 research-entry.py /usr/local/bin/sdsctl-display-read-research\n"
        )
        result["config.yaml"] = replace_once(
            result["config.yaml"].decode(),
            f'version: "{version}-mimic-{revision[:12]}"',
            f'version: "{version}-mimic-{revision[:12]}-{display_read_kind}-research"',
        ).encode()
        result["DOCS.md"] += (
            "\n## Temporary display GET qualification\n\n"
            f"Only {display_read_kind} is enabled, with firmware {display_read_firmware}. "
            "This image requires direct UDP and no active waterfall. It does not send "
            "research commands on startup. Verify fresh private ready.json, read_kind, "
            "PID and process start ticks before one administrator SIGUSR1 trigger with "
            "the operator at the scanner. It checks MDL/VER then sends at most one "
            "selected GET using the existing owner, never GSI/SET/KEY/AST/APR. "
            "No retry, second owner, automatic bank sweep, renderer data injection or "
            "polling is enabled. Preserve every result, especially unconfirmed reads. "
            "Do not restart/rearm a failed case; review before another qualification. "
            "Restore the prepared normal candidate after the bounded test.\n"
        ).encode()
        report.update(
            {
                "purpose": "local-mimic-display-read-research-only",
                "research_firmware_pin": display_read_firmware,
                "research_read_kind": display_read_kind,
                "research_automatic_start": False,
                "files": {
                    name: hashlib.sha256(data).hexdigest() for name, data in sorted(result.items())
                },
            }
        )
    if front_panel_firmware is not None:
        assert front_panel_key is not None
        assert front_panel_mode is not None
        assert front_panel_screen is not None
        runtime_name = "src/sds200/home_assistant_app_runtime.py"
        before, delimiter, after = (
            result[runtime_name].decode().partition("def build_home_assistant_daemon_command(")
        )
        signature, end, body = after.partition(") -> tuple[str, ...]:")
        if not delimiter or not end:
            raise ValueError("Review the daemon launcher boundary.")
        signature = replace_once(
            signature,
            "executable: str = HOME_ASSISTANT_APP_EXECUTABLE",
            'executable: str = "/usr/local/bin/sdsctl-front-panel-research"',
        )
        result[runtime_name] = (before + delimiter + signature + end + body).encode()
        for name in ("research_system_status_daemon.py", "research_front_panel_daemon.py"):
            result[name] = snapshot["scripts/" + name]
        result["research-entry.py"] = (
            "#!/usr/local/bin/python\nimport os, sys, uuid\n"
            "sys.path.insert(0, '/opt/sdsctl-research')\n"
            "from research_front_panel_daemon import main\n"
            "directory = ('/run/sdsctl/front-panel-research-' "
            "+ str(os.getpid()) + '-' + uuid.uuid4().hex)\n"
            f"raise SystemExit(main(['--expected-firmware', {front_panel_firmware!r}, "
            f"'--key-code', {front_panel_key!r}, "
            f"'--expected-mode', {front_panel_mode!r}, "
            f"'--expected-screen', {front_panel_screen!r}, "
            "'--evidence-directory', directory, '--', *sys.argv[1:]]))\n"
        ).encode()
        result["Dockerfile"] += (
            b"\n# Explicit one-press front-panel research; no automatic KEY command.\n"
            b"COPY research_system_status_daemon.py research_front_panel_daemon.py "
            b"/opt/sdsctl-research/\n"
            b"COPY --chmod=0555 research-entry.py /usr/local/bin/sdsctl-front-panel-research\n"
        )
        result["config.yaml"] = replace_once(
            result["config.yaml"].decode(),
            f'version: "{version}-mimic-{revision[:12]}"',
            f'version: "{version}-mimic-{revision[:12]}-'
            f'{FRONT_PANEL_KEY_NAMES[front_panel_key]}-key-research"',
        ).encode()
        result["DOCS.md"] += (
            "\n## Temporary front-panel key qualification\n\n"
            f"This image pins firmware {front_panel_firmware}, key code "
            f"{front_panel_key}, mode {front_panel_mode} and screen {front_panel_screen}. "
            "It requires the direct-UDP scanner owner, an idle Waterfall and two exact "
            "preflight display frames. No KEY command runs on startup. Verify fresh "
            "private ready.json, PID and process start ticks while physically at the "
            "scanner before one administrator SIGUSR1 trigger. The trigger sends at "
            "most one typed KEY press; it provides no sequence, held press, API route "
            "or retry. An acknowledgement is not state confirmation. Preserve every "
            "result, especially an unconfirmed press, and restore the normal candidate "
            "after the bounded supervised check.\n"
        ).encode()
        report.update(
            {
                "purpose": "local-mimic-front-panel-research-only",
                "research_firmware_pin": front_panel_firmware,
                "research_key_code": front_panel_key,
                "research_expected_mode": front_panel_mode,
                "research_expected_screen": front_panel_screen,
                "research_automatic_start": False,
                "files": {
                    name: hashlib.sha256(data).hexdigest() for name, data in sorted(result.items())
                },
            }
        )
    if supplemental_firmware is not None:
        read_kind = (
            "shared-clock-favorites-bounded-write"
            if supplemental_bounded_writes
            else "shared-clock-favorites-transition-wait"
            if supplemental_transition_wait
            else "shared-clock-favorites-timing"
            if supplemental_timing
            else "shared-clock-favorites-continuity"
            if supplemental_continuity
            else "shared-clock-favorites"
        )
        suffix = (
            "supplemental-bounded-write"
            if supplemental_bounded_writes
            else "supplemental-transition-wait"
            if supplemental_transition_wait
            else "supplemental-timing"
            if supplemental_timing
            else ("supplemental-continuity" if supplemental_continuity else "supplemental-research")
        )
        limit, duration = (60, 64) if supplemental_continuity else (6, 8)
        continuity_argument = "'--continuity', " if supplemental_continuity else ""
        if supplemental_timing:
            continuity_argument += "'--timing', "
        if supplemental_transition_wait:
            continuity_argument += "'--transition-wait', "
        if supplemental_bounded_writes:
            continuity_argument += "'--bounded-writes', "
        runtime_name = "src/sds200/home_assistant_app_runtime.py"
        before, delimiter, after = (
            result[runtime_name].decode().partition("def build_home_assistant_daemon_command(")
        )
        signature, end, body = after.partition(") -> tuple[str, ...]:")
        if not delimiter or not end:
            raise ValueError("Review the daemon launcher boundary.")
        signature = replace_once(
            signature,
            "executable: str = HOME_ASSISTANT_APP_EXECUTABLE",
            'executable: str = "/usr/local/bin/sdsctl-supplemental-research"',
        )
        result[runtime_name] = (before + delimiter + signature + end + body).encode()
        for name in ("research_system_status_daemon.py", "research_supplemental_daemon.py"):
            result[name] = snapshot["scripts/" + name]
        result["research-entry.py"] = (
            "#!/usr/local/bin/python\nimport os, sys, uuid\n"
            "sys.path.insert(0, '/opt/sdsctl-research')\n"
            "from research_supplemental_daemon import main\n"
            "directory = ('/run/sdsctl/supplemental-research-' "
            "+ str(os.getpid()) + '-' + uuid.uuid4().hex)\n"
            f"raise SystemExit(main(['--expected-firmware', {supplemental_firmware!r}, "
            f"'--evidence-directory', directory, {continuity_argument}'--', *sys.argv[1:]]))\n"
        ).encode()
        result["Dockerfile"] += (
            b"\n# Explicit bounded shared-reader qualification; no signal means no reads.\n"
            b"COPY research_system_status_daemon.py research_supplemental_daemon.py "
            b"/opt/sdsctl-research/\n"
            b"COPY --chmod=0555 research-entry.py /usr/local/bin/sdsctl-supplemental-research\n"
        )
        result["config.yaml"] = replace_once(
            result["config.yaml"].decode(),
            f'version: "{version}-mimic-{revision[:12]}"',
            f'version: "{version}-mimic-{revision[:12]}-{suffix}"',
        ).encode()
        result["DOCS.md"] += (
            "\n## Temporary shared clock/Favorites qualification\n\n"
            f"Exact startup identity must be SDS200 / {supplemental_firmware}, direct UDP. "
            "No supplemental reads occur before a verified administrator SIGUSR1 while "
            "the operator is watching normal scanning. One existing display worker gets "
            f"at most {limit} DTM/FQK read opportunities in {duration} seconds. No scoped queries, "
            "scanner writes, AST/APR, extra owner, rendered samples or automatic rearming. "
            "Runtime controls, reconnects, shutdown and Waterfall retain their guards. "
            "Archive private evidence; distinguish counted replies from wire commands and "
            "physical scanning acceptance. Do not rearm an unconfirmed case. Restore the "
            "matching normal candidate after the test.\n"
        ).encode()
        if supplemental_transition_wait:
            result["DOCS.md"] += (
                b"\nThis distinct case may withhold reads for the observed exact trunk_scan / "
                b"Scan Mode pair with complete trunk records. No read is admitted during "
                b"the mismatch; two strictly qualified PSI updates are required to resume. "
                b"The deadline is fixed at the previous qualified PSI plus two seconds, "
                b"within the unchanged 64-second trial. An overlapping read, unsafe "
                b"context or failed read is still terminal. No renderer exception becomes "
                b"read permission, and scoped queries remain disabled.\n"
            )
        if supplemental_bounded_writes:
            result["DOCS.md"] += (
                b"\nThis distinct candidate uses native POSIX nonblocking writes for "
                b"exact DTM/FQK GETs and one shared write/reply deadline. The existing "
                b"receive timeout is unchanged. Native trace ownership is retained; "
                b"software TX-intent and raw RX-line phases are NOT observed. Timing "
                b"schema 2 records scope, parsed-packet and cache events only, not "
                b"wire delivery. No unsupported-transport or file-trace fallback.\n"
            )
        report.update(
            {
                "purpose": "local-mimic-supplemental-research-only",
                "research_firmware_pin": supplemental_firmware,
                "research_read_kind": read_kind,
                "research_automatic_start": False,
                "research_max_opportunities": limit,
                "research_window_seconds": duration,
                "research_max_psi_gap_seconds": 2 if supplemental_continuity else None,
                "research_timing_event_limit": 512 if supplemental_timing else None,
                "research_scan_transition_wait": supplemental_transition_wait,
                "research_scan_transition_recovery_psi": 2
                if supplemental_transition_wait
                else None,
                **(
                    {
                        "research_write_policy": "native-posix-nonblocking",
                        "research_timing_schema": 2,
                        "research_unobserved_phases": ["tx_intent", "rx_line", "rx_rejection"],
                    }
                    if supplemental_bounded_writes
                    else {}
                ),
                "files": {
                    name: hashlib.sha256(data).hexdigest() for name, data in sorted(result.items())
                },
            }
        )
    result["candidate-source.json"] = (json.dumps(report, indent=2) + "\n").encode()
    return result


def validate_research_choice(
    ast_firmware: str | None,
    read_firmware: str | None,
    read_kind: str | None,
    front_panel_firmware: str | None = None,
    front_panel_key: str | None = None,
    front_panel_mode: str | None = None,
    front_panel_screen: str | None = None,
    supplemental_firmware: str | None = None,
    supplemental_continuity: bool = False,
    supplemental_timing: bool = False,
    supplemental_transition_wait: bool = False,
    supplemental_bounded_writes: bool = False,
) -> None:
    front_values = (
        front_panel_firmware,
        front_panel_key,
        front_panel_mode,
        front_panel_screen,
    )
    front_selected = any(value is not None for value in front_values)
    if front_selected and not all(value is not None for value in front_values):
        raise ValueError("Front-panel research requires firmware, key, mode and screen pins.")
    selected_modes = sum(
        (
            ast_firmware is not None,
            read_firmware is not None or read_kind is not None,
            front_selected,
            supplemental_firmware is not None,
        )
    )
    if selected_modes > 1:
        raise ValueError("Only one research mode can be staged.")
    if type(supplemental_bounded_writes) is not bool or (
        supplemental_bounded_writes and not supplemental_transition_wait
    ):
        raise ValueError("Bounded-write research requires an explicit transition-wait case.")
    if type(supplemental_transition_wait) is not bool or (
        supplemental_transition_wait and not (supplemental_continuity and supplemental_timing)
    ):
        raise ValueError("Transition research requires explicit continuity, timing and firmware.")
    if type(supplemental_timing) is not bool or (
        supplemental_timing and not supplemental_continuity
    ):
        raise ValueError("Timing research requires explicit continuity and firmware selection.")
    if type(supplemental_continuity) is not bool or (
        supplemental_continuity and supplemental_firmware is None
    ):
        raise ValueError("Continuity research requires an explicit shared-reader firmware pin.")
    if supplemental_firmware is not None and (
        re.fullmatch(r"[A-Za-z0-9._ -]{1,64}", supplemental_firmware) is None
        or supplemental_firmware != supplemental_firmware.strip()
    ):
        raise ValueError("Review the exact supplemental firmware pin.")
    if (read_firmware is None) != (read_kind is None):
        raise ValueError("Display-read research requires both firmware and GET kind.")
    if read_firmware is not None and (
        re.fullmatch(r"[A-Za-z0-9._ -]{1,64}", read_firmware) is None
        or read_firmware != read_firmware.strip()
        or read_kind not in {"clock", "favorites", "system", "department"}
    ):
        raise ValueError("Review the exact display-read firmware pin and GET kind.")
    if front_panel_firmware is not None and (
        re.fullmatch(r"[A-Za-z0-9._ -]{1,64}", front_panel_firmware) is None
        or front_panel_firmware != front_panel_firmware.strip()
        or front_panel_key not in FRONT_PANEL_KEY_NAMES
        or not isinstance(front_panel_mode, str)
        or re.fullmatch(r"[A-Za-z0-9._ /-]{1,64}", front_panel_mode) is None
        or front_panel_mode != front_panel_mode.strip()
        or not isinstance(front_panel_screen, str)
        or re.fullmatch(r"[A-Za-z0-9._ /-]{1,64}", front_panel_screen) is None
        or front_panel_screen != front_panel_screen.strip()
    ):
        raise ValueError("Review the exact front-panel firmware, key, mode and screen pins.")


def from_revision(
    revision: str,
    *,
    research_firmware: str | None = None,
    display_read_firmware: str | None = None,
    display_read_kind: str | None = None,
    front_panel_firmware: str | None = None,
    front_panel_key: str | None = None,
    front_panel_mode: str | None = None,
    front_panel_screen: str | None = None,
    supplemental_firmware: str | None = None,
    supplemental_continuity: bool = False,
    supplemental_timing: bool = False,
    supplemental_transition_wait: bool = False,
    supplemental_bounded_writes: bool = False,
) -> dict[str, bytes]:
    validate_research_choice(
        research_firmware,
        display_read_firmware,
        display_read_kind,
        front_panel_firmware,
        front_panel_key,
        front_panel_mode,
        front_panel_screen,
        supplemental_firmware,
        supplemental_continuity,
        supplemental_timing,
        supplemental_transition_wait,
        supplemental_bounded_writes,
    )
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Use a full 40-character commit ID, not a branch or tag.")
    archive = git(
        "archive",
        "--format=tar",
        revision,
        "--",
        "src",
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "home-assistant/sds200",
        *(
            ["scripts/research_system_status_daemon.py"]
            if any(
                value is not None
                for value in (
                    research_firmware,
                    display_read_firmware,
                    front_panel_firmware,
                    supplemental_firmware,
                )
            )
            else []
        ),
        *(["scripts/research_display_read_daemon.py"] if display_read_firmware is not None else []),
        *(["scripts/research_front_panel_daemon.py"] if front_panel_firmware is not None else []),
        *(["scripts/research_supplemental_daemon.py"] if supplemental_firmware is not None else []),
    )
    snapshot = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source:
            if member.isdir():
                continue
            if (
                not member.isfile()
                or Path(member.name).is_absolute()
                or ".." in Path(member.name).parts
            ):
                raise ValueError("Source snapshot contains unsupported archive members.")
            stream = source.extractfile(member)
            assert stream is not None
            snapshot[member.name] = stream.read()
    return render(
        snapshot,
        revision,
        research_firmware=research_firmware,
        display_read_firmware=display_read_firmware,
        display_read_kind=display_read_kind,
        front_panel_firmware=front_panel_firmware,
        front_panel_key=front_panel_key,
        front_panel_mode=front_panel_mode,
        front_panel_screen=front_panel_screen,
        supplemental_firmware=supplemental_firmware,
        supplemental_continuity=supplemental_continuity,
        supplemental_timing=supplemental_timing,
        supplemental_transition_wait=supplemental_transition_wait,
        supplemental_bounded_writes=supplemental_bounded_writes,
    )


def stage(destination: Path, files: dict[str, bytes]) -> None:
    if (
        not destination.is_absolute()
        or destination.parent.resolve(strict=True) != destination.parent
    ):
        raise ValueError("Use a new absolute destination under an existing real directory.")
    for name in files:
        if (
            not name
            or Path(name).is_absolute()
            or any(p in {"", ".", ".."} for p in name.split("/"))
        ):
            raise ValueError("Candidate files must have normalized relative paths.")
    destination.mkdir(mode=0o700, exist_ok=False)
    for name, data in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)


def verify(destination: Path, files: dict[str, bytes]) -> None:
    if not destination.is_absolute() or destination.resolve(strict=True) != destination:
        raise ValueError("Candidate directory must be an absolute real directory.")
    actual = set()
    for item in destination.rglob("*"):
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ValueError("Candidate contains an unsupported filesystem entry.")
        if item.is_file():
            name = item.relative_to(destination).as_posix()
            actual.add(name)
            info = item.stat()
            if (
                name not in files
                or info.st_nlink != 1
                or info.st_size != len(files[name])
                or item.read_bytes() != files[name]
            ):
                raise ValueError("Candidate differs from the exact committed source package.")
    if actual != set(files):
        raise ValueError("Candidate inventory is incomplete.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--system-status-research-firmware",
        help="Explicit temporary same-owner research launcher with this exact firmware pin.",
    )
    parser.add_argument("--display-read-research-firmware")
    parser.add_argument("--front-panel-research-firmware")
    parser.add_argument("--front-panel-key", choices=tuple(FRONT_PANEL_KEY_NAMES))
    parser.add_argument("--front-panel-mode")
    parser.add_argument("--front-panel-screen")
    parser.add_argument("--supplemental-research-firmware")
    parser.add_argument("--supplemental-continuity", action="store_true")
    parser.add_argument("--supplemental-timing", action="store_true")
    parser.add_argument("--supplemental-transition-wait", action="store_true")
    parser.add_argument("--supplemental-bounded-writes", action="store_true")
    parser.add_argument(
        "--display-read-kind", choices=("clock", "favorites", "system", "department")
    )
    args = parser.parse_args()
    files = from_revision(
        args.source_revision,
        research_firmware=args.system_status_research_firmware,
        display_read_firmware=args.display_read_research_firmware,
        display_read_kind=args.display_read_kind,
        front_panel_firmware=args.front_panel_research_firmware,
        front_panel_key=args.front_panel_key,
        front_panel_mode=args.front_panel_mode,
        front_panel_screen=args.front_panel_screen,
        supplemental_firmware=args.supplemental_research_firmware,
        supplemental_continuity=args.supplemental_continuity,
        supplemental_timing=args.supplemental_timing,
        supplemental_transition_wait=args.supplemental_transition_wait,
        supplemental_bounded_writes=args.supplemental_bounded_writes,
    )
    if not args.verify:
        if git("rev-parse", "HEAD").decode().strip() != args.source_revision or git(
            "status", "--porcelain"
        ):
            raise ValueError("Stage only the clean current committed checkout.")
        stage(args.destination, files)
    verify(args.destination, files)
    print(
        f"Verified local acceptance context for {args.source_revision}; "
        "no App was installed or started."
    )


if __name__ == "__main__":
    main()
