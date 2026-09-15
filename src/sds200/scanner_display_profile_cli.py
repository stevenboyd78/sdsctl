"""Local administrator workflow; never a remote scanner control or browser login."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .daemon_api import DaemonApiOperation
from .daemon_client import DaemonApiClient
from .daemon_display_profile import display_profile_projection
from .daemon_ipc import resolve_daemon_socket_location
from .scanner_display_configuration import (
    ScannerDisplayConfiguration,
    load_scanner_display_configuration,
)
from .scanner_display_profile_storage import (
    DisplayProfileStorageError,
    initialize_display_profile_storage,
)


def add_display_profile_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "scanner-display-profile",
        help="Manage an explicitly selected Mimic-SDS profile locally; never changes the scanner",
    )
    parser.add_argument("--manifest", type=Path, required=True, metavar="PATH")
    actions = parser.add_subparsers(dest="display_profile_action", required=True)
    actions.add_parser("status", help="Inspect accepted state and current source-copy status")
    actions.add_parser(
        "preview", help="Validate the source and print a display-only review; no writes"
    )
    init = actions.add_parser("init", help="Create a new private state directory; do not import")
    init.add_argument("--yes", action="store_true", help="Explicitly approve creating new state")
    refresh = actions.add_parser("import", help="Review and accept the configured source file")
    refresh.add_argument(
        "--yes", action="store_true", help="Explicitly approve the prepared import"
    )
    refresh.add_argument(
        "--confirm-source-change",
        action="store_true",
        help="Explicitly approve a different configured source identity",
    )
    refresh.add_argument(
        "--daemon-socket-path",
        type=Path,
        help="After acceptance, explicitly reload this local daemon's cached accepted profile",
    )
    reload = actions.add_parser(
        "reload", help="Reload accepted state in the local daemon; no import"
    )
    reload.add_argument("--daemon-socket-path", type=Path, required=True)


def _approve(args: argparse.Namespace, question: str) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        raise ValueError("Interactive confirmation or explicit --yes is required. No change made.")
    try:
        return input(question + " [y/N] ").strip().casefold() in {"y", "yes"}
    except EOFError:
        return False


def _reload(configuration: ScannerDisplayConfiguration, socket_path: Path) -> dict[str, object]:
    location = resolve_daemon_socket_location(socket_path)
    with DaemonApiClient(location) as client:
        current = client.request(DaemonApiOperation.DISPLAY_PROFILE)
        if current.get("endpoint_id") != str(configuration.binding.endpoint_id):
            raise ValueError(
                "The local daemon does not match the selected display-profile endpoint."
            )
        result = client.request(DaemonApiOperation.DISPLAY_PROFILE_RELOAD)
        if result.get("endpoint_id") != str(configuration.binding.endpoint_id):
            raise ValueError("The daemon profile reload could not be confirmed. Review its status.")
        return dict(result)


def run_display_profile_command(args: argparse.Namespace) -> int:
    """OS account/file/socket permissions confer local administration, not web roles."""
    config = load_scanner_display_configuration(args.manifest)
    repository = config.repository()
    try:
        if args.display_profile_action == "init":
            if not _approve(args, "Create a new private accepted-profile state directory?"):
                print("Cancelled. No state created.")
                return 0
            initialize_display_profile_storage(config.state_directory, config.binding.endpoint_id)
            print("Private display-profile state initialized. No profile was imported.")
            return 0
        if args.display_profile_action == "status":
            print(json.dumps(display_profile_projection(repository.inspect()), sort_keys=True))
            return 0
        if args.display_profile_action == "reload":
            print(json.dumps(_reload(config, args.daemon_socket_path), sort_keys=True))
            return 0
        if args.display_profile_action not in {"preview", "import"}:
            raise ValueError("Unsupported scanner display-profile action.")
        preview = repository.prepare(config.binding, acquired_at=datetime.now(UTC))
        print(
            json.dumps(
                {
                    "revision": preview.profile.revision,
                    "previous_revision": preview.previous_revision,
                    "source_changed": preview.source_changed,
                    "descriptor": preview.profile.as_dict(),
                },
                sort_keys=True,
            )
        )
        if args.display_profile_action == "preview":
            repository.cancel(preview)
            return 0
        try:
            approved = _approve(args, "Accept this prepared scanner display profile?")
        except ValueError:
            repository.cancel(preview)
            raise
        if not approved:
            repository.cancel(preview)
            print("Cancelled. The accepted profile is unchanged.")
            return 0
        accepted = repository.commit(
            preview,
            imported_at=datetime.now(UTC),
            confirm_source_change=args.confirm_source_change,
        )
        print(f"Accepted display profile revision {accepted.profile.revision}. Scanner unchanged.")
        if args.daemon_socket_path is not None:
            # Persistence is already complete. Never imply a reload failure undid the import.
            try:
                _reload(config, args.daemon_socket_path)
            except Exception:
                raise ValueError(
                    "The profile was accepted, but daemon reload was not confirmed. "
                    "Inspect status, then use the local reload command; do not repeat the import."
                ) from None
            print("The local daemon's accepted display profile was reloaded.")
        else:
            print("A running daemon needs an explicit local reload to use this revision.")
        return 0
    except DisplayProfileStorageError as exc:
        raise ValueError(str(exc)) from None
