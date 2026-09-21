#!/usr/bin/env python3
"""Stage/verify a finite native shared-reader App; never deploy or contact HA.

This is distinct from the older runtime-subclass research staging modes. A case
is chosen once at staging time and persists under /data across container starts.
An independent host restoration guard is still required before deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tarfile
import uuid
from pathlib import Path

import stage_mimic_app as normal

ROOT = Path(__file__).resolve().parents[1]
SLUG = "sds200_supplemental_acceptance"
NAME = "sds200 finite shared-reader acceptance"
DRIVERS = ("stage_mimic_app.py", "stage_supplemental_acceptance.py")
LAUNCHERS = (
    "accept_supplemental_daemon.py",
    "guard_supplemental_acceptance.py",
    "accept_supplemental_web.py",
)
DAEMON_ENTRY = "/usr/local/bin/sdsctl-supplemental-acceptance"
WEB_ENTRY = "/usr/local/bin/sdsctl-supplemental-acceptance-web"
IMAGE_SCRIPTS = "/opt/sdsctl-supplemental-acceptance"
RUNTIME = "src/sds200/home_assistant_app_runtime.py"


def validate(revision: str, case_id: str, firmware: str) -> None:
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Use an exact full source commit.")
    if (
        not isinstance(case_id, str)
        or re.fullmatch(r"[0-9a-f]{32}", case_id) is None
        or uuid.UUID(hex=case_id).version != 4
    ):
        raise ValueError("Use a new lowercase UUIDv4 hex case ID.")
    if (
        not isinstance(firmware, str)
        or re.fullmatch(r"[A-Za-z0-9._ -]{1,64}", firmware) is None
        or firmware != firmware.strip()
    ):
        raise ValueError("Review the exact firmware pin.")


def launcher(source: str, function: str, executable: str) -> str:
    """Change one default executable in one reviewed command-builder signature."""
    boundary = f"def {function}("
    if source.count(boundary) != 1:
        raise ValueError("Review the App launcher boundary.")
    before, delimiter, after = source.partition(boundary)
    signature, end, body = after.partition(") -> tuple[str, ...]:")
    if not end or "\ndef " in signature:
        raise ValueError("Review the App launcher signature.")
    signature = normal.replace_once(
        signature,
        "executable: str = HOME_ASSISTANT_APP_EXECUTABLE",
        f"executable: str = {executable!r}",
    )
    return before + delimiter + signature + end + body


def render(
    snapshot: dict[str, bytes], revision: str, *, case_id: str, firmware: str
) -> dict[str, bytes]:
    validate(revision, case_id, firmware)
    # No old research options are accepted by this adapter.
    result = normal.render(snapshot, revision)
    report = json.loads(result.pop("candidate-source.json"))
    source = result[RUNTIME].decode()
    for function, executable in (
        ("build_home_assistant_daemon_command", DAEMON_ENTRY),
        ("build_home_assistant_web_command", WEB_ENTRY),
        ("build_home_assistant_native_web_command", WEB_ENTRY),
    ):
        source = launcher(source, function, executable)
    result[RUNTIME] = source.encode()
    for name in LAUNCHERS:
        result[name] = snapshot["scripts/" + name]
    guard_directory = f"/data/sdsctl-supplemental-acceptance-{case_id}"
    result["supplemental-daemon-entry.py"] = (
        "#!/usr/local/bin/python\n"
        "import sys\n"
        f"sys.path.insert(0, {IMAGE_SCRIPTS!r})\n"
        "from guard_supplemental_acceptance import main\n"
        "raise SystemExit(main(["
        f"'--guard-directory', {guard_directory!r}, "
        f"'--source-revision', {revision!r}, '--expected-firmware', {firmware!r}, "
        "'--ready-timeout', '600', '--window-seconds', '64', "
        "'--max-read-attempts', '60', '--', *sys.argv[1:]]))\n"
    ).encode()
    result["supplemental-web-entry.py"] = (
        "#!/usr/local/bin/python\n"
        "import sys\n"
        f"sys.path.insert(0, {IMAGE_SCRIPTS!r})\n"
        "from accept_supplemental_web import main\n"
        "raise SystemExit(main(sys.argv[1:]))\n"
    ).encode()
    result["Dockerfile"] += (
        "\n# One persistent finite acceptance case; not a release or startup feature.\n"
        f"COPY {' '.join(LAUNCHERS)} {IMAGE_SCRIPTS}/\n"
        f"COPY --chmod=0555 supplemental-daemon-entry.py {DAEMON_ENTRY}\n"
        f"COPY --chmod=0555 supplemental-web-entry.py {WEB_ENTRY}\n"
    ).encode()
    version = report["package_version"]
    app_version = f"{version}-mimic-{revision[:12]}-demand-{case_id}"
    manifest = result["config.yaml"].decode()
    for old, new in (
        (f'version: "{version}-mimic-{revision[:12]}"', f'version: "{app_version}"'),
        (f'slug: "{normal.SLUG}"', f'slug: "{SLUG}"'),
        ('name: "sds200 Mimic-SDS acceptance"', f'name: "{NAME}"'),
        ('panel_title: "sds200 Mimic-SDS acceptance"', f'panel_title: "{NAME}"'),
        (
            'mqtt_topic_prefix: "sdsctl-mimic-acceptance"',
            'mqtt_topic_prefix: "sdsctl-supplemental-acceptance"',
        ),
        (
            'recording_directory: "sdsctl-mimic-acceptance/recordings"',
            'recording_directory: "sdsctl-supplemental-acceptance/recordings"',
        ),
    ):
        manifest = normal.replace_once(manifest, old, new)
    result["config.yaml"] = manifest.encode()
    result["DOCS.md"] += (
        "\n## Finite native shared-reader acceptance\n\n"
        f"Separate local App slug: `{SLUG}`. Keep the normal acceptance App installed "
        "at its verified image/source; do not replace its build context or update it "
        "as part of this trial. The separate slot has independent MQTT/recording defaults "
        "and requires reviewed profile provisioning; do not blindly clone App data. "
        "Only one App may own the scanner at any time.\n\n"
        f"Case: `{case_id}`. Persistent guard: `{guard_directory}`. "
        f"Firmware: `{firmware}`. Both guardian and native daemon are source-pinned. "
        "The case is consumed on first launch; restart/rebuild cannot generate a new case. "
        "Never remove, replace, or adopt a consumed case directory. Startup and passive "
        "reads do not arm acquisition. Verify ready identity inside the exact container "
        "PID namespace, obtain fresh physical readiness, then use the reviewed one-shot "
        "arm helper followed by one fresh authenticated WebUI consumer. "
        "No scoped SQK/DQK, AST, automatic retry, Pi, TUI or HA-card change is selected.\n\n"
        "The child guardian only proves process exit. BEFORE candidate startup install "
        "and verify a distinct host-side restoration deadline bound to this case and a "
        "freshly verified normal acceptance image/context. Stop only this candidate, "
        "confirm exit, then start the unchanged normal acceptance App. Do not rebuild "
        "the baseline during recovery. Unknown outcomes require reconciliation, not replay. Keep "
        "the published App stopped to preserve single scanner ownership. Preserve all "
        "profile, credential, recording and case evidence. This staging command does "
        "not install that restoration guard or prove App recovery.\n"
    ).encode()
    report.update(
        purpose="local-mimic-finite-native-supplemental-acceptance-only",
        app_slug=SLUG,
        app_version=app_version,
        normal_acceptance_slug=normal.SLUG,
        restoration_strategy="separate-app-stop-start",
        case_id=case_id,
        guard_directory=guard_directory,
        expected_firmware=firmware,
        operator_wait_seconds=600,
        acquisition_window_seconds=64,
        max_read_attempts=60,
        guardian_deadline_seconds=684,
        explicit_arm_required=True,
        explicit_authenticated_demand_required=True,
        automatic_rearm=False,
        host_restoration_guard_required=True,
        restoration_verified=False,
        files={name: hashlib.sha256(data).hexdigest() for name, data in sorted(result.items())},
    )
    result["candidate-source.json"] = (json.dumps(report, indent=2) + "\n").encode()
    return result


def from_revision(revision: str, *, case_id: str, firmware: str) -> dict[str, bytes]:
    validate(revision, case_id, firmware)
    archive = normal.git(
        "archive",
        "--format=tar",
        revision,
        "--",
        "src",
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "home-assistant/sds200",
        *("scripts/" + n for n in (*DRIVERS, *LAUNCHERS)),
    )
    snapshot = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source:
            if member.isdir():
                continue
            if (
                not member.isfile()
                or Path(member.name).is_absolute()
                or any(part in {"", ".", ".."} for part in member.name.split("/"))
                or member.name in snapshot
            ):
                raise ValueError("Unsupported source archive member.")
            stream = source.extractfile(member)
            assert stream is not None
            snapshot[member.name] = stream.read()
    # Verification must not silently use a newer adapter against an older pin.
    # The manifest itself is never trusted as the expected file inventory.
    for name in DRIVERS:
        if snapshot.get("scripts/" + name) != (ROOT / "scripts" / name).read_bytes():
            raise ValueError("Use the staging adapters from the selected source commit.")
    return render(snapshot, revision, case_id=case_id, firmware=firmware)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--expected-firmware", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    if args.verify and args.case_id is None:
        parser.error("Verification requires the separately recorded case ID.")
    case_id = args.case_id if args.case_id is not None else uuid.uuid4().hex
    files = from_revision(args.source_revision, case_id=case_id, firmware=args.expected_firmware)
    if not args.verify:
        if normal.git("rev-parse", "HEAD").decode().strip() != args.source_revision or normal.git(
            "status", "--porcelain"
        ):
            raise ValueError("Stage only the clean current committed checkout.")
        normal.stage(args.destination, files)
    normal.verify(args.destination, files)
    print(f"Verified offline case {case_id}, source {args.source_revision}; no App started.")


if __name__ == "__main__":
    main()
