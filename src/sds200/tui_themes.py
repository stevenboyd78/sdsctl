from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import TYPE_CHECKING, Final

from .exceptions import SDS200Error

if TYPE_CHECKING:
    from .theme import ThemePalette

TUI_THEME_MANIFEST_SCHEMA_VERSION: Final = 1
TUI_THEME_PALETTE_SCHEMA_VERSION: Final = 1
TUI_THEME_INTERFACE: Final = "tui"
TUI_THEME_MANIFEST_FILENAME: Final = "manifest.json"
BUILT_IN_TUI_THEME_IDS: Final = ("dark", "light")

_MANIFEST_FIELDS: Final = frozenset(
    {
        "schema_version",
        "interface",
        "id",
        "label",
        "order",
        "palette",
        "palette_name",
        "stylesheet",
        "screen_class",
    }
)
_PALETTE_FIELDS: Final = frozenset({"schema_version", "name", "styles"})
_STYLE_FIELDS: Final = frozenset(
    {"foreground", "background", "bold", "dim", "underline"}
)
_IDENTIFIER_PATTERN: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_PALETTE_NAME_PATTERN: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_PALETTE_FILENAME_PATTERN: Final = re.compile(
    r"[a-z0-9]+(?:-[a-z0-9]+)*\.json\Z"
)
_STYLESHEET_FILENAME_PATTERN: Final = re.compile(
    r"[a-z0-9]+(?:-[a-z0-9]+)*\.tcss\Z"
)
_COLOR_PATTERN: Final = re.compile(r"#[0-9a-fA-F]{6}\Z")
_MANAGED_STYLESHEET_PROPERTIES: Final = frozenset(
    {
        "background",
        "border",
        "border-bottom",
        "border-left",
        "border-right",
        "border-top",
        "color",
    }
)
_MANAGED_BORDER_STYLES: Final = frozenset(
    {
        "ascii",
        "blank",
        "dashed",
        "double",
        "heavy",
        "hidden",
        "hkey",
        "inner",
        "none",
        "outer",
        "panel",
        "round",
        "solid",
        "tall",
        "thick",
        "vkey",
        "wide",
    }
)


class TuiThemeError(SDS200Error):
    """Raised when a TUI theme package is invalid."""


@dataclass(frozen=True, slots=True)
class TuiThemeManifest:
    """Validated metadata and semantic palette for one packaged TUI theme."""

    identifier: str
    label: str
    order: int
    palette_filename: str
    palette: ThemePalette
    stylesheet: str
    screen_class: str | None


@dataclass(frozen=True, slots=True)
class TuiThemeRegistry:
    """Ordered immutable registry of validated packaged TUI themes."""

    themes: tuple[TuiThemeManifest, ...]

    def __post_init__(self) -> None:
        if not self.themes:
            raise TuiThemeError("TUI theme registry must not be empty")

        identifiers = tuple(theme.identifier for theme in self.themes)
        orders = tuple(theme.order for theme in self.themes)
        palette_names = tuple(theme.palette.name for theme in self.themes)
        screen_classes = tuple(
            theme.screen_class
            for theme in self.themes
            if theme.screen_class is not None
        )
        for values, label in (
            (identifiers, "identities"),
            (orders, "order values"),
            (palette_names, "palette names"),
            (screen_classes, "screen classes"),
        ):
            if len(set(values)) != len(values):
                raise TuiThemeError(f"TUI theme registry contains duplicate {label}")

        if orders != tuple(sorted(orders)):
            raise TuiThemeError("TUI theme registry must use deterministic order")

    @property
    def identifiers(self) -> tuple[str, ...]:
        """Return stable package identifiers in theme order."""
        return tuple(theme.identifier for theme in self.themes)

    def require(self, identifier: str) -> TuiThemeManifest:
        """Return one registered package or reject an unknown identity."""
        for theme in self.themes:
            if theme.identifier == identifier:
                return theme
        raise TuiThemeError(f"unknown TUI theme: {identifier}")

    def require_palette_name(self, name: str) -> TuiThemeManifest:
        """Return one package by its stable compatibility palette name."""
        for theme in self.themes:
            if theme.palette.name == name:
                return theme
        raise TuiThemeError(f"unknown TUI palette name: {name}")


def _read_json_object(path: Traversable, description: str) -> dict[str, object]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TuiThemeError(f"{description} is invalid JSON") from exc
    if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
        raise TuiThemeError(f"{description} must be a JSON object with text keys")
    return parsed


def _required_text(document: dict[str, object], field: str, description: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TuiThemeError(f"{description} field {field!r} must be nonblank text")
    return value


def _required_integer(document: dict[str, object], field: str, description: str) -> int:
    value = document.get(field)
    if type(value) is not int:
        raise TuiThemeError(f"{description} field {field!r} must be an integer")
    return value


def _optional_color(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _COLOR_PATTERN.fullmatch(value) is None:
        raise TuiThemeError(f"TUI palette style {field!r} must be null or #RRGGBB")
    return value


def _read_palette(
    path: Traversable,
    *,
    expected_name: str,
) -> ThemePalette:
    from .theme import ThemePalette, ThemeRole, ThemeStyle

    document = _read_json_object(path, "TUI palette")
    if set(document) != _PALETTE_FIELDS:
        raise TuiThemeError("TUI palette fields do not match schema version 1")
    schema_version = _required_integer(document, "schema_version", "TUI palette")
    if schema_version != TUI_THEME_PALETTE_SCHEMA_VERSION:
        raise TuiThemeError(f"unsupported TUI palette schema: {schema_version}")
    name = _required_text(document, "name", "TUI palette")
    if name != expected_name:
        raise TuiThemeError("TUI palette name does not match its manifest")

    raw_styles = document.get("styles")
    if not isinstance(raw_styles, dict) or any(
        not isinstance(key, str) for key in raw_styles
    ):
        raise TuiThemeError("TUI palette styles must be an object with text role keys")

    expected_roles = {role.value for role in ThemeRole}
    actual_roles = set(raw_styles)
    missing = sorted(expected_roles - actual_roles)
    unknown = sorted(actual_roles - expected_roles)
    if missing:
        raise TuiThemeError(f"TUI palette is missing roles: {', '.join(missing)}")
    if unknown:
        raise TuiThemeError(f"TUI palette has unknown roles: {', '.join(unknown)}")

    styles: dict[ThemeRole, ThemeStyle] = {}
    for role in ThemeRole:
        raw_style = raw_styles[role.value]
        if not isinstance(raw_style, dict) or any(
            not isinstance(key, str) for key in raw_style
        ):
            raise TuiThemeError(f"TUI palette role {role.value!r} must be an object")
        if not set(raw_style).issubset(_STYLE_FIELDS) or "foreground" not in raw_style:
            raise TuiThemeError(
                f"TUI palette role {role.value!r} has invalid style fields"
            )
        for flag in ("bold", "dim", "underline"):
            if flag in raw_style and type(raw_style[flag]) is not bool:
                raise TuiThemeError(
                    f"TUI palette role {role.value!r} field {flag!r} must be boolean"
                )
        styles[role] = ThemeStyle(
            foreground=_optional_color(raw_style.get("foreground"), "foreground"),
            background=_optional_color(raw_style.get("background"), "background"),
            bold=bool(raw_style.get("bold", False)),
            dim=bool(raw_style.get("dim", False)),
            underline=bool(raw_style.get("underline", False)),
        )
    return ThemePalette(name=name, styles=styles)


def _read_manifest(directory: Traversable) -> TuiThemeManifest:
    manifest_path = directory.joinpath(TUI_THEME_MANIFEST_FILENAME)
    if not manifest_path.is_file():
        raise TuiThemeError(f"TUI theme {directory.name!r} is missing manifest.json")
    document = _read_json_object(manifest_path, "TUI theme manifest")
    if set(document) != _MANIFEST_FIELDS:
        raise TuiThemeError("TUI theme manifest fields do not match schema version 1")

    schema_version = _required_integer(document, "schema_version", "TUI manifest")
    if schema_version != TUI_THEME_MANIFEST_SCHEMA_VERSION:
        raise TuiThemeError(f"unsupported TUI theme manifest schema: {schema_version}")
    interface = _required_text(document, "interface", "TUI manifest")
    if interface != TUI_THEME_INTERFACE:
        raise TuiThemeError(f"TUI theme {directory.name!r} has a cross-interface manifest")

    identifier = _required_text(document, "id", "TUI manifest")
    if _IDENTIFIER_PATTERN.fullmatch(identifier) is None:
        raise TuiThemeError(f"invalid TUI theme identifier: {identifier!r}")
    if identifier != directory.name:
        raise TuiThemeError(
            f"TUI theme identifier {identifier!r} does not match its directory"
        )
    label = _required_text(document, "label", "TUI manifest")
    order = _required_integer(document, "order", "TUI manifest")
    if order < 0:
        raise TuiThemeError("TUI theme order must not be negative")

    palette_filename = _required_text(document, "palette", "TUI manifest")
    if _PALETTE_FILENAME_PATTERN.fullmatch(palette_filename) is None:
        raise TuiThemeError("TUI palette must be one local JSON filename")
    palette_path = directory.joinpath(palette_filename)
    if not palette_path.is_file():
        raise TuiThemeError(f"TUI theme {identifier!r} palette is missing")
    palette_name = _required_text(document, "palette_name", "TUI manifest")
    if _PALETTE_NAME_PATTERN.fullmatch(palette_name) is None:
        raise TuiThemeError(f"invalid TUI palette name: {palette_name!r}")
    palette = _read_palette(palette_path, expected_name=palette_name)

    stylesheet = _required_text(document, "stylesheet", "TUI manifest")
    if _STYLESHEET_FILENAME_PATTERN.fullmatch(stylesheet) is None:
        raise TuiThemeError("TUI stylesheet must be one local TCSS filename")
    stylesheet_path = directory.joinpath(stylesheet)
    if not stylesheet_path.is_file():
        raise TuiThemeError(f"TUI theme {identifier!r} stylesheet is missing")
    try:
        stylesheet_text = stylesheet_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TuiThemeError(f"TUI theme {identifier!r} stylesheet is invalid") from exc
    lowered_stylesheet = stylesheet_text.casefold()
    if (
        not stylesheet_text.strip()
        or "@import" in lowered_stylesheet
        or "url(" in lowered_stylesheet
    ):
        raise TuiThemeError(
            f"TUI theme {identifier!r} stylesheet must be nonblank and local-only"
        )

    screen_class_value = document.get("screen_class")
    if screen_class_value is not None and (
        not isinstance(screen_class_value, str)
        or _IDENTIFIER_PATTERN.fullmatch(screen_class_value) is None
    ):
        raise TuiThemeError("TUI screen class must be null or lowercase kebab-case")

    declared_files = {
        TUI_THEME_MANIFEST_FILENAME,
        palette_filename,
        stylesheet,
    }
    actual_files = {child.name for child in directory.iterdir()}
    if actual_files != declared_files:
        raise TuiThemeError(f"TUI theme {identifier!r} contains undeclared files")

    return TuiThemeManifest(
        identifier=identifier,
        label=label,
        order=order,
        palette_filename=palette_filename,
        palette=palette,
        stylesheet=stylesheet,
        screen_class=screen_class_value,
    )


def load_tui_theme_registry(root: Traversable) -> TuiThemeRegistry:
    """Load and validate theme directories under one TUI root."""

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
    return TuiThemeRegistry(themes)


def load_tui_theme_package(directory: Traversable) -> TuiThemeManifest:
    """Load and validate one TUI theme package directory."""

    return _read_manifest(directory)


@lru_cache(maxsize=1)
def built_in_tui_theme_registry() -> TuiThemeRegistry:
    """Load the exact terminal themes distributed with sdsctl."""

    root = files("sds200.themes").joinpath(TUI_THEME_INTERFACE)
    registry = load_tui_theme_registry(root)
    if registry.identifiers != BUILT_IN_TUI_THEME_IDS:
        raise TuiThemeError("built-in TUI theme identities do not match compatibility set")
    return registry


def read_built_in_tui_theme_stylesheet(theme: TuiThemeManifest) -> str:
    """Read one validated canonical built-in Textual stylesheet."""

    canonical = built_in_tui_theme_registry().require(theme.identifier)
    if theme != canonical:
        raise TuiThemeError(f"theme is not a canonical built-in TUI theme: {theme.identifier}")
    return (
        files("sds200.themes")
        .joinpath(TUI_THEME_INTERFACE)
        .joinpath(theme.identifier)
        .joinpath(theme.stylesheet)
        .read_text(encoding="utf-8")
    )


def built_in_tui_theme_stylesheets() -> str:
    """Return deterministic Textual CSS for every validated built-in theme."""

    registry = built_in_tui_theme_registry()
    return "\n".join(
        read_built_in_tui_theme_stylesheet(theme).rstrip()
        for theme in registry.themes
    ) + "\n"


def validate_managed_tui_theme_stylesheet(
    theme: TuiThemeManifest,
    stylesheet: str,
) -> None:
    """Require managed TCSS to remain scoped and presentation-only."""

    screen_class = theme.screen_class
    if screen_class is None:
        raise TuiThemeError(
            f"managed TUI theme {theme.identifier!r} must declare a screen class"
        )
    if not isinstance(stylesheet, str):
        raise TypeError("Managed TUI stylesheet must be text")
    if "/*" in stylesheet and "*/" not in stylesheet:
        raise TuiThemeError("managed TUI stylesheet contains an unterminated comment")
    without_comments = re.sub(r"/\*.*?\*/", "", stylesheet, flags=re.DOTALL)
    if "/*" in without_comments or "*/" in without_comments:
        raise TuiThemeError("managed TUI stylesheet contains an invalid comment")
    if not without_comments.strip():
        raise TuiThemeError("managed TUI stylesheet must contain at least one rule")
    if any(token in without_comments.casefold() for token in ("@", "url(")):
        raise TuiThemeError("managed TUI stylesheet must not import external content")
    if "$" in without_comments:
        raise TuiThemeError("managed TUI stylesheet must not declare variables")

    selector_pattern = re.compile(
        rf"Screen\.{re.escape(screen_class)}"
        r"(?:\s+(?:[.#][A-Za-z_][A-Za-z0-9_-]*))*\Z"
    )
    position = 0
    rule_count = 0
    rule_pattern = re.compile(r"\s*([^{}]+)\{([^{}]*)\}", re.DOTALL)
    while position < len(without_comments):
        if not without_comments[position:].strip():
            break
        match = rule_pattern.match(without_comments, position)
        if match is None:
            raise TuiThemeError("managed TUI stylesheet must contain simple rules only")
        selectors, body = match.groups()
        for selector in selectors.split(","):
            normalized_selector = " ".join(selector.split())
            if selector_pattern.fullmatch(normalized_selector) is None:
                raise TuiThemeError(
                    "managed TUI stylesheet selectors must be scoped beneath "
                    f"Screen.{screen_class}"
                )

        declarations = [item.strip() for item in body.split(";") if item.strip()]
        if not declarations:
            raise TuiThemeError("managed TUI stylesheet rules must not be empty")
        for declaration in declarations:
            if ":" not in declaration:
                raise TuiThemeError("managed TUI stylesheet declaration is invalid")
            property_name, value = declaration.split(":", 1)
            normalized_property = property_name.strip().casefold()
            if normalized_property not in _MANAGED_STYLESHEET_PROPERTIES:
                raise TuiThemeError(
                    "managed TUI stylesheet property is not presentation-only: "
                    f"{normalized_property or '<empty>'}"
                )
            if not value.strip():
                raise TuiThemeError("managed TUI stylesheet value must not be empty")
            normalized_value = " ".join(value.split()).casefold()
            if normalized_property in {"color", "background"}:
                valid_value = _COLOR_PATTERN.fullmatch(normalized_value) is not None
            elif normalized_value == "none":
                valid_value = True
            else:
                border_parts = normalized_value.split()
                valid_value = (
                    len(border_parts) == 2
                    and border_parts[0] in _MANAGED_BORDER_STYLES
                    and _COLOR_PATTERN.fullmatch(border_parts[1]) is not None
                )
            if not valid_value:
                raise TuiThemeError(
                    "managed TUI stylesheet value is outside the safe color and "
                    f"border grammar: {normalized_value!r}"
                )
        position = match.end()
        rule_count += 1
    if rule_count == 0:
        raise TuiThemeError("managed TUI stylesheet must contain at least one rule")


__all__ = [
    "BUILT_IN_TUI_THEME_IDS",
    "TUI_THEME_INTERFACE",
    "TUI_THEME_MANIFEST_FILENAME",
    "TUI_THEME_MANIFEST_SCHEMA_VERSION",
    "TUI_THEME_PALETTE_SCHEMA_VERSION",
    "TuiThemeError",
    "TuiThemeManifest",
    "TuiThemeRegistry",
    "built_in_tui_theme_registry",
    "built_in_tui_theme_stylesheets",
    "load_tui_theme_package",
    "load_tui_theme_registry",
    "read_built_in_tui_theme_stylesheet",
    "validate_managed_tui_theme_stylesheet",
]
