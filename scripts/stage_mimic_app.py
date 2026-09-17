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


def git(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, timeout=30
    ).stdout


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError("Candidate source contract changed; review the staging adapter.")
    return text.replace(old, new, 1)


def render(
    snapshot: dict[str, bytes], revision: str, *, research_firmware: str | None = None
) -> dict[str, bytes]:
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
    result["candidate-source.json"] = (json.dumps(report, indent=2) + "\n").encode()
    return result


def from_revision(revision: str, *, research_firmware: str | None = None) -> dict[str, bytes]:
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
        *(["scripts/research_system_status_daemon.py"] if research_firmware is not None else []),
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
    return render(snapshot, revision, research_firmware=research_firmware)


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
    args = parser.parse_args()
    files = from_revision(
        args.source_revision, research_firmware=args.system_status_research_firmware
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
