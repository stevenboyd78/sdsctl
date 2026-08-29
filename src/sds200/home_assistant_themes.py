from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Final

from .exceptions import SDS200Error

HOME_ASSISTANT_THEME_MANIFEST_SCHEMA_VERSION: Final = 1
HOME_ASSISTANT_THEME_INTERFACE: Final = "home-assistant"
HOME_ASSISTANT_THEME_MANIFEST_FILENAME: Final = "manifest.json"
BUILT_IN_HOME_ASSISTANT_THEME_IDS: Final = (
    "compact",
    "sds200-display",
    "waterfall",
)

_MANIFEST_FIELDS: Final = frozenset(
    {
        "schema_version",
        "interface",
        "id",
        "label",
        "order",
        "module",
        "custom_element",
        "installed_filename",
        "resource_url",
    }
)
_IDENTIFIER_PATTERN: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_JAVASCRIPT_FILENAME_PATTERN: Final = re.compile(
    r"[a-z0-9]+(?:-[a-z0-9]+)*\.js\Z"
)


class HomeAssistantThemeError(SDS200Error):
    """Raised when a Home Assistant theme package is invalid."""


@dataclass(frozen=True, slots=True)
class HomeAssistantThemeManifest:
    """Validated declarative metadata for one packaged Lovelace presentation."""

    identifier: str
    label: str
    order: int
    module: str
    custom_element: str
    installed_filename: str
    resource_url: str


@dataclass(frozen=True, slots=True)
class HomeAssistantThemeRegistry:
    """Ordered immutable registry of validated packaged Lovelace presentations."""

    themes: tuple[HomeAssistantThemeManifest, ...]

    def __post_init__(self) -> None:
        if not self.themes:
            raise HomeAssistantThemeError("Home Assistant theme registry must not be empty")

        identifiers = tuple(theme.identifier for theme in self.themes)
        orders = tuple(theme.order for theme in self.themes)
        custom_elements = tuple(theme.custom_element for theme in self.themes)
        installed_filenames = tuple(theme.installed_filename for theme in self.themes)
        resource_urls = tuple(theme.resource_url for theme in self.themes)

        for values, label in (
            (identifiers, "identities"),
            (orders, "order values"),
            (custom_elements, "custom elements"),
            (installed_filenames, "installed filenames"),
            (resource_urls, "resource URLs"),
        ):
            if len(set(values)) != len(values):
                raise HomeAssistantThemeError(
                    f"Home Assistant theme registry contains duplicate {label}"
                )

        if orders != tuple(sorted(orders)):
            raise HomeAssistantThemeError(
                "Home Assistant theme registry must use deterministic order"
            )

    @property
    def identifiers(self) -> tuple[str, ...]:
        """Return stable package identifiers in installation order."""
        return tuple(theme.identifier for theme in self.themes)

    def require(self, identifier: str) -> HomeAssistantThemeManifest:
        """Return one registered package or reject an unknown identity."""
        for theme in self.themes:
            if theme.identifier == identifier:
                return theme
        raise HomeAssistantThemeError(f"unknown Home Assistant theme: {identifier}")


def _required_text(document: dict[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise HomeAssistantThemeError(
            f"Home Assistant theme manifest field {field!r} must be nonblank text"
        )
    return value


def _required_integer(document: dict[str, object], field: str) -> int:
    value = document.get(field)
    if type(value) is not int:
        raise HomeAssistantThemeError(
            f"Home Assistant theme manifest field {field!r} must be an integer"
        )
    return value


def _read_manifest(directory: Traversable) -> HomeAssistantThemeManifest:
    manifest_path = directory.joinpath(HOME_ASSISTANT_THEME_MANIFEST_FILENAME)
    if not manifest_path.is_file():
        raise HomeAssistantThemeError(
            f"Home Assistant theme {directory.name!r} is missing manifest.json"
        )

    try:
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HomeAssistantThemeError(
            f"Home Assistant theme {directory.name!r} has an invalid manifest"
        ) from exc

    if not isinstance(parsed, dict):
        raise HomeAssistantThemeError("Home Assistant theme manifest must be a JSON object")
    document: dict[str, object] = parsed

    if set(document) != _MANIFEST_FIELDS:
        raise HomeAssistantThemeError(
            "Home Assistant theme manifest fields do not match schema version 1"
        )

    schema_version = _required_integer(document, "schema_version")
    if schema_version != HOME_ASSISTANT_THEME_MANIFEST_SCHEMA_VERSION:
        raise HomeAssistantThemeError(
            f"unsupported Home Assistant theme manifest schema: {schema_version}"
        )

    interface = _required_text(document, "interface")
    if interface != HOME_ASSISTANT_THEME_INTERFACE:
        raise HomeAssistantThemeError(
            f"Home Assistant theme {directory.name!r} has a cross-interface manifest"
        )

    identifier = _required_text(document, "id")
    if _IDENTIFIER_PATTERN.fullmatch(identifier) is None:
        raise HomeAssistantThemeError(
            f"invalid Home Assistant theme identifier: {identifier!r}"
        )
    if identifier != directory.name:
        raise HomeAssistantThemeError(
            f"Home Assistant theme identifier {identifier!r} does not match its directory"
        )

    label = _required_text(document, "label")
    order = _required_integer(document, "order")
    if order < 0:
        raise HomeAssistantThemeError("Home Assistant theme order must not be negative")

    module = _required_text(document, "module")
    if _JAVASCRIPT_FILENAME_PATTERN.fullmatch(module) is None:
        raise HomeAssistantThemeError(
            "Home Assistant theme module must be one local JavaScript filename"
        )
    module_path = directory.joinpath(module)
    if not module_path.is_file():
        raise HomeAssistantThemeError(
            f"Home Assistant theme {identifier!r} module is missing"
        )

    custom_element = _required_text(document, "custom_element")
    if _IDENTIFIER_PATTERN.fullmatch(custom_element) is None:
        raise HomeAssistantThemeError(
            f"invalid Home Assistant custom element: {custom_element!r}"
        )

    installed_filename = _required_text(document, "installed_filename")
    if _JAVASCRIPT_FILENAME_PATTERN.fullmatch(installed_filename) is None:
        raise HomeAssistantThemeError(
            "Home Assistant installed filename must be one local JavaScript filename"
        )
    if installed_filename != module:
        raise HomeAssistantThemeError(
            "Home Assistant packaged and installed module filenames must match"
        )

    resource_url = _required_text(document, "resource_url")
    expected_resource_url = f"/local/sds200/{installed_filename}"
    if resource_url != expected_resource_url:
        raise HomeAssistantThemeError(
            "Home Assistant theme resource URL must use its exact /local/sds200/ path"
        )

    declared_files = {HOME_ASSISTANT_THEME_MANIFEST_FILENAME, module}
    actual_files = {child.name for child in directory.iterdir()}
    if actual_files != declared_files:
        raise HomeAssistantThemeError(
            f"Home Assistant theme {identifier!r} contains undeclared files"
        )

    return HomeAssistantThemeManifest(
        identifier=identifier,
        label=label,
        order=order,
        module=module,
        custom_element=custom_element,
        installed_filename=installed_filename,
        resource_url=resource_url,
    )


def load_home_assistant_theme_registry(root: Traversable) -> HomeAssistantThemeRegistry:
    """Load and validate theme directories under one Home Assistant root."""

    directories = sorted(
        (
            child
            for child in root.iterdir()
            if child.is_dir() and not child.name.startswith("_")
        ),
        key=lambda child: child.name,
    )
    themes = tuple(
        sorted(
            (_read_manifest(item) for item in directories),
            key=lambda item: item.order,
        )
    )
    return HomeAssistantThemeRegistry(themes)


def load_home_assistant_theme_package(
    directory: Traversable,
) -> HomeAssistantThemeManifest:
    """Load and validate one Home Assistant theme package directory."""

    return _read_manifest(directory)


def built_in_home_assistant_theme_registry() -> HomeAssistantThemeRegistry:
    """Load the exact Home Assistant presentations distributed with sdsctl."""

    root = files("sds200.themes").joinpath(HOME_ASSISTANT_THEME_INTERFACE)
    registry = load_home_assistant_theme_registry(root)
    if registry.identifiers != BUILT_IN_HOME_ASSISTANT_THEME_IDS:
        raise HomeAssistantThemeError(
            "built-in Home Assistant theme identities do not match the compatibility set"
        )
    return registry


def read_built_in_home_assistant_theme_module(
    theme: HomeAssistantThemeManifest,
) -> bytes:
    """Read one validated built-in Lovelace JavaScript module."""

    canonical = built_in_home_assistant_theme_registry().require(theme.identifier)
    if theme != canonical:
        raise HomeAssistantThemeError(
            f"theme is not a canonical built-in Home Assistant theme: {theme.identifier}"
        )
    return (
        files("sds200.themes")
        .joinpath(HOME_ASSISTANT_THEME_INTERFACE)
        .joinpath(theme.identifier)
        .joinpath(theme.module)
        .read_bytes()
    )


__all__ = [
    "BUILT_IN_HOME_ASSISTANT_THEME_IDS",
    "HOME_ASSISTANT_THEME_INTERFACE",
    "HOME_ASSISTANT_THEME_MANIFEST_FILENAME",
    "HOME_ASSISTANT_THEME_MANIFEST_SCHEMA_VERSION",
    "HomeAssistantThemeError",
    "HomeAssistantThemeManifest",
    "HomeAssistantThemeRegistry",
    "built_in_home_assistant_theme_registry",
    "load_home_assistant_theme_package",
    "load_home_assistant_theme_registry",
    "read_built_in_home_assistant_theme_module",
]
