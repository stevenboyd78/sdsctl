#!/usr/bin/env python3
"""Internal authenticated WebUI adapter for one staged supplemental trial.

No scanner ownership, arming, retries, credentials, or normal CLI opt-in is added.
The native dashboard factory retains all admission and request handling.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sds200 import cli, web_dashboard

_FACTORY = web_dashboard.create_web_dashboard_app
_SELECTIONS = frozenset({"supplemental_delivery", "supplemental_demand", "supplemental_consumer"})


class AcceptanceFactory:
    """One native factory invocation, without merging another adapter's policy."""

    def __init__(self) -> None:
        self.used = False

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.used:
            raise RuntimeError("The acceptance WebUI factory was already used.")
        self.used = True
        if _SELECTIONS.intersection(kwargs):
            raise ValueError("Supplemental WebUI policy was already selected.")
        # Only the two existing authenticated paths are in scope. The original
        # factory validates the authentication object and all other arguments.
        ingress = kwargs.get("home_assistant_ingress", False)
        native = kwargs.get("lan_authentication") is not None
        if type(ingress) is not bool or ingress == native:
            raise ValueError("Select exactly one authenticated WebUI admission path.")
        return _FACTORY(
            *args,
            **kwargs,
            supplemental_delivery=True,
            supplemental_demand=True,
            supplemental_consumer=True,
        )


@contextmanager
def installed() -> Iterator[AcceptanceFactory]:
    if web_dashboard.create_web_dashboard_app is not _FACTORY:
        raise RuntimeError("The dashboard factory is already patched.")
    selected = AcceptanceFactory()
    web_dashboard.create_web_dashboard_app = selected
    try:
        yield selected
    finally:
        web_dashboard.create_web_dashboard_app = _FACTORY


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = cli.build_parser(suppress_configuration_defaults=True).parse_args(arguments)
    if args.action != "web":
        raise ValueError("This acceptance adapter only launches the WebUI.")
    if (
        bool(args.home_assistant_ingress) == bool(args.authenticated_lan)
        or args.container_exposure
        or args.experimental_browser_devices
        or args.browser_device_config is not None
    ):
        raise ValueError("Use one normal authenticated WebUI path without browser-device trials.")
    with installed() as selected:
        result = cli.main(arguments)
        if result == 0 and not selected.used:
            raise RuntimeError("The acceptance WebUI factory was not constructed.")
        return result


if __name__ == "__main__":
    raise SystemExit(main())
