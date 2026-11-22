"""Command-line lifecycle for strict portable service manifests."""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

from .exceptions import ConfigurationError
from .service_configuration import (
    configured_service_template,
    load_service_configuration,
    render_service_configuration_inventory,
    service_configuration_template,
    service_launch_plan,
    write_service_configuration_template,
)


def add_service_configuration_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add the isolated service-manifest command family."""

    parser = subparsers.add_parser(
        "service-config",
        help="Validate, inspect, export or run a portable service manifest",
    )
    actions = parser.add_subparsers(dest="service_config_action", required=True)
    for action, help_text in (
        ("validate", "Validate a private manifest without network or process changes"),
        ("show-sources", "Print effective values and provenance as deterministic JSON"),
        ("show-command", "Print the exact existing CLI command represented by a manifest"),
        ("preflight", "Replace this process with the existing bounded preflight"),
        ("run", "Replace this process with the configured long-running client"),
    ):
        command = actions.add_parser(action, help=help_text)
        command.add_argument(
            "--manifest",
            type=Path,
            required=True,
            help="Absolute existing private mode-0600 service manifest",
        )
        command.add_argument(
            "--set",
            dest="service_config_overrides",
            action="append",
            default=[],
            metavar="FIELD=VALUE",
            help=(
                "Temporary strict override for validation, inspection or recovery; "
                "repeatable and never written to the manifest"
            ),
        )

    template = actions.add_parser(
        "template",
        help="Print or atomically create a credential-free candidate manifest",
    )
    template.add_argument("--kind", choices=("display-client", "browser-kiosk"), required=True)
    template.add_argument(
        "--output",
        type=Path,
        help="Absolute new path; creates mode 0600 and never overwrites",
    )

    service = actions.add_parser(
        "service",
        help="Print the thin packaged systemd launcher for one manifest kind",
    )
    service.add_argument("--kind", choices=("display-client", "browser-kiosk"), required=True)


def run_service_configuration_command(args: argparse.Namespace) -> int:
    """Execute one offline action or replace the process with an existing client."""

    try:
        action = args.service_config_action
        if action == "template":
            if args.output is None:
                print(service_configuration_template(args.kind), end="")
            else:
                output = write_service_configuration_template(args.output, kind=args.kind)
                print(f"Private {args.kind} candidate created: {output}")
                print("No credential was copied and no service was changed or started.")
            return 0
        if action == "service":
            print(configured_service_template(args.kind), end="")
            return 0

        resolved = load_service_configuration(
            args.manifest,
            overrides=_parse_service_configuration_overrides(args.service_config_overrides),
        )
        if action == "validate":
            print(
                f"{resolved.kind} service manifest is valid; "
                "no credential bytes were read and no service was changed."
            )
            return 0
        if action == "show-sources":
            print(render_service_configuration_inventory(resolved), end="")
            return 0

        plan = service_launch_plan(
            resolved,
            executable=sys.argv[0],
            preflight=action == "preflight",
        )
        if action == "show-command":
            print(plan.command)
            return 0
        if action not in {"run", "preflight"}:
            raise AssertionError(f"Unsupported service-config action: {action!r}")
        os.execvpe(plan.argv[0], list(plan.argv), dict(plan.environ))
        raise AssertionError("Process replacement unexpectedly returned.")
    except (ConfigurationError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 78


def _parse_service_configuration_overrides(
    assignments: Sequence[str],
) -> dict[str, object]:
    """Parse repeatable FIELD=VALUE arguments as TOML scalars or bare strings."""

    overrides: dict[str, object] = {}
    for assignment in assignments:
        field, separator, raw_value = assignment.partition("=")
        field = field.strip()
        raw_value = raw_value.strip()
        if not separator or not field or not raw_value:
            raise ConfigurationError("Each --set value must use nonblank FIELD=VALUE syntax.")
        if field in overrides:
            raise ConfigurationError(f"Duplicate --set field {field!r}.")
        try:
            value = tomllib.loads(f"value = {raw_value}\n")["value"]
        except tomllib.TOMLDecodeError:
            value = raw_value
        overrides[field] = value
    return overrides


__all__ = [
    "add_service_configuration_parser",
    "run_service_configuration_command",
]
