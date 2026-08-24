from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import PurePosixPath
from types import MappingProxyType

from .exceptions import SDS200Error

WEB_THEME_MANIFEST_SCHEMA_VERSION = 1
WEB_THEME_INTERFACE = "web"
WEB_THEME_MANIFEST_FILENAME = "manifest.json"
BUILT_IN_WEB_THEME_IDS = (
    "system",
    "lcars",
    "matrix",
    "first-responder",
    "amateur-radio",
)

_IDENTIFIER_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}\Z")
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "interface",
        "id",
        "label",
        "order",
        "stylesheet",
        "color_scheme",
        "theme_colors",
    }
)
_COLOR_SCHEMES = frozenset({"light", "dark", "light dark"})


class WebThemeError(SDS200Error):
    """Raised when a web theme package is invalid."""


@dataclass(frozen=True, slots=True)
class WebThemeManifest:
    """Validated declarative metadata for one packaged web theme."""

    schema_version: int
    interface: str
    identifier: str
    label: str
    order: int
    stylesheet: str
    color_scheme: str
    light_theme_color: str
    dark_theme_color: str

    @property
    def stylesheet_url(self) -> str:
        """Return the same-origin public URL for this theme's stylesheet."""

        return f"assets/themes/{self.identifier}/{self.stylesheet}"

    def browser_document(self) -> Mapping[str, object]:
        """Return immutable browser bootstrap data for this theme."""

        return MappingProxyType(
            {
                "id": self.identifier,
                "label": self.label,
                "colorScheme": self.color_scheme,
                "themeColors": MappingProxyType(
                    {
                        "light": self.light_theme_color,
                        "dark": self.dark_theme_color,
                    }
                ),
            }
        )


@dataclass(frozen=True, slots=True)
class WebThemeRegistry:
    """Ordered immutable registry of validated packaged web themes."""

    themes: tuple[WebThemeManifest, ...]

    def __post_init__(self) -> None:
        if not self.themes:
            raise WebThemeError("web theme registry must not be empty")
        identifiers = tuple(theme.identifier for theme in self.themes)
        if len(set(identifiers)) != len(identifiers):
            raise WebThemeError("web theme registry contains duplicate identities")
        orders = tuple(theme.order for theme in self.themes)
        if len(set(orders)) != len(orders):
            raise WebThemeError("web theme registry contains duplicate order values")
        if orders != tuple(sorted(orders)):
            raise WebThemeError("web theme registry must use deterministic order")
        if identifiers[0] != "system":
            raise WebThemeError("System must be the first web theme")

    @property
    def identifiers(self) -> tuple[str, ...]:
        """Return stable theme identifiers in picker order."""

        return tuple(theme.identifier for theme in self.themes)

    def require(self, identifier: str) -> WebThemeManifest:
        """Return one registered theme or reject the unknown identity."""

        for theme in self.themes:
            if theme.identifier == identifier:
                return theme
        raise WebThemeError(f"unknown web theme: {identifier}")

    def browser_json(self) -> str:
        """Serialize deterministic safe browser bootstrap metadata."""

        payload = [
            {
                "id": theme.identifier,
                "label": theme.label,
                "colorScheme": theme.color_scheme,
                "themeColors": {
                    "light": theme.light_theme_color,
                    "dark": theme.dark_theme_color,
                },
            }
            for theme in self.themes
        ]
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _required_text(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise WebThemeError(f"web theme manifest field {field!r} must be nonblank text")
    return value


def _required_integer(document: Mapping[str, object], field: str) -> int:
    value = document.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise WebThemeError(f"web theme manifest field {field!r} must be an integer")
    return value


def _theme_colors(document: Mapping[str, object]) -> tuple[str, str]:
    value = document.get("theme_colors")
    if not isinstance(value, dict) or set(value) != {"light", "dark"}:
        raise WebThemeError(
            "web theme manifest field 'theme_colors' must contain light and dark"
        )
    light = value["light"]
    dark = value["dark"]
    if not isinstance(light, str) or _COLOR_PATTERN.fullmatch(light) is None:
        raise WebThemeError("web theme light color must use #RRGGBB")
    if not isinstance(dark, str) or _COLOR_PATTERN.fullmatch(dark) is None:
        raise WebThemeError("web theme dark color must use #RRGGBB")
    return light.lower(), dark.lower()


def _read_manifest(directory: Traversable) -> WebThemeManifest:
    manifest_path = directory.joinpath(WEB_THEME_MANIFEST_FILENAME)
    if not manifest_path.is_file():
        raise WebThemeError(f"web theme {directory.name!r} is missing manifest.json")
    try:
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WebThemeError(f"web theme {directory.name!r} has an invalid manifest") from exc
    if not isinstance(parsed, dict):
        raise WebThemeError("web theme manifest must be a JSON object")
    document: Mapping[str, object] = parsed
    if set(document) != _MANIFEST_FIELDS:
        raise WebThemeError("web theme manifest fields do not match schema version 1")

    schema_version = _required_integer(document, "schema_version")
    if schema_version != WEB_THEME_MANIFEST_SCHEMA_VERSION:
        raise WebThemeError(f"unsupported web theme manifest schema: {schema_version}")
    interface = _required_text(document, "interface")
    if interface != WEB_THEME_INTERFACE:
        raise WebThemeError(f"web theme {directory.name!r} has a cross-interface manifest")
    identifier = _required_text(document, "id")
    if _IDENTIFIER_PATTERN.fullmatch(identifier) is None:
        raise WebThemeError(f"invalid web theme identifier: {identifier!r}")
    if identifier != directory.name:
        raise WebThemeError(
            f"web theme identifier {identifier!r} does not match its directory"
        )
    label = _required_text(document, "label")
    order = _required_integer(document, "order")
    if order < 0:
        raise WebThemeError("web theme order must not be negative")
    stylesheet = _required_text(document, "stylesheet")
    stylesheet_path = PurePosixPath(stylesheet)
    if (
        stylesheet_path.is_absolute()
        or len(stylesheet_path.parts) != 1
        or stylesheet_path.name != stylesheet
        or stylesheet_path.suffix != ".css"
    ):
        raise WebThemeError("web theme stylesheet must be one local CSS filename")
    if "://" in stylesheet or stylesheet.startswith("//"):
        raise WebThemeError("web theme stylesheet must not be a remote URL")
    stylesheet_resource = directory.joinpath(stylesheet)
    if not stylesheet_resource.is_file():
        raise WebThemeError(f"web theme {identifier!r} stylesheet is missing")
    declared_files = {WEB_THEME_MANIFEST_FILENAME, stylesheet}
    actual_files = {
        child.name
        for child in directory.iterdir()
        if child.is_file() and child.name != "__init__.py"
    }
    if actual_files != declared_files:
        raise WebThemeError(f"web theme {identifier!r} contains undeclared files")
    color_scheme = _required_text(document, "color_scheme")
    if color_scheme not in _COLOR_SCHEMES:
        raise WebThemeError(f"unsupported web theme color scheme: {color_scheme!r}")
    light_theme_color, dark_theme_color = _theme_colors(document)
    return WebThemeManifest(
        schema_version=schema_version,
        interface=interface,
        identifier=identifier,
        label=label,
        order=order,
        stylesheet=stylesheet,
        color_scheme=color_scheme,
        light_theme_color=light_theme_color,
        dark_theme_color=dark_theme_color,
    )


def load_web_theme_registry(root: Traversable) -> WebThemeRegistry:
    """Load and validate built-in theme directories under one web root."""

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
    return WebThemeRegistry(themes)


def load_web_theme_package(directory: Traversable) -> WebThemeManifest:
    """Load and validate one web theme package directory."""

    return _read_manifest(directory)


def built_in_web_theme_registry() -> WebThemeRegistry:
    """Load the exact web themes distributed with sdsctl."""

    registry = load_web_theme_registry(files("sds200.themes.web"))
    if registry.identifiers != BUILT_IN_WEB_THEME_IDS:
        raise WebThemeError("built-in web theme identities do not match the compatibility set")
    return registry


def read_built_in_web_theme_stylesheet(theme: WebThemeManifest) -> bytes:
    """Read one validated built-in stylesheet from package resources."""

    if theme.identifier not in BUILT_IN_WEB_THEME_IDS:
        raise WebThemeError(f"theme is not a built-in web theme: {theme.identifier}")
    return (
        files("sds200.themes.web")
        .joinpath(theme.identifier)
        .joinpath(theme.stylesheet)
        .read_bytes()
    )
