"""Explicit experimental server wiring; never provision or repair authority.

Native HTTPS and Supervisor Ingress run in separate processes. They read the
same private configuration, but grant disjoint session/administration powers.
Same-account processes and root remain inside the existing trust boundary.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .browser_device_native import _private_read
from .browser_device_recovery import _object
from .browser_device_store import BrowserDeviceStore
from .exceptions import ConfigurationError
from .web_auth import _normalize_origin


class BrowserDeviceServerError(ConfigurationError):
    """Fixed diagnostics only; never echo private configuration input."""


@dataclass(frozen=True, slots=True)
class BrowserDeviceServerConfiguration:
    authority_path: Path
    native_origin: str
    ingress_origin: str | None
    admin_user_ids: frozenset[str] = field(repr=False)

    def require_native_origin(self, origin: str) -> None:
        if _normalize_origin(origin) != self.native_origin:
            raise BrowserDeviceServerError(
                "Browser-device native origin does not match the HTTPS listener."
            )

    def require_ingress_admin(self) -> None:
        if self.ingress_origin is None:
            raise BrowserDeviceServerError(
                "Browser-device Ingress administration is not configured."
            )


def load_browser_device_server_configuration(path: Path) -> BrowserDeviceServerConfiguration:
    """Bounded private-file read and read-only existing-authority preflight.

    This is not a readiness claim: the HTTPS lifespan must still acquire its
    sole-owner lock and acknowledgement socket before it can serve requests.
    Configuration edits require a coordinated restart of both web processes.
    """
    try:
        if sys.platform != "linux" or not isinstance(path, Path) or not path.is_absolute():
            raise ValueError()
        value = json.loads(_private_read(path.parent, path.name, 16384), object_pairs_hook=_object)
        if (type(value) is not dict
                or set(value) != {"version", "authority_path", "native_origin", "ingress_admin"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["authority_path"]) is not str
                or _normalize_origin(value["native_origin"]) != value["native_origin"]):
            raise ValueError()
        ingress = value["ingress_admin"]
        origin = None
        users: frozenset[str] = frozenset()
        if ingress is not None:
            if (type(ingress) is not dict or set(ingress) != {"origin", "user_ids"}
                    or _normalize_origin(ingress["origin"]) != ingress["origin"]
                    or type(ingress["user_ids"]) is not list
                    or not 1 <= len(ingress["user_ids"]) <= 32
                    or any(type(uid) is not str or re.fullmatch(r"[a-f0-9]{32}", uid) is None
                           for uid in ingress["user_ids"])):
                raise ValueError()
            users = frozenset(ingress["user_ids"])
            if len(users) != len(ingress["user_ids"]):
                raise ValueError()
            origin = ingress["origin"]
        authority = Path(value["authority_path"])
        BrowserDeviceStore(authority).validate_existing()
        return BrowserDeviceServerConfiguration(authority, value["native_origin"], origin, users)
    except Exception:
        raise BrowserDeviceServerError(
            "Browser-device server configuration or existing authority is invalid or unsafe. "
            "Check the private files, version, exact HTTPS origins and explicit administrator "
            "IDs. No authority was created, repaired or replaced."
        ) from None
