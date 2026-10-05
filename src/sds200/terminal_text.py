"""Bounded literal text for human terminal renderers."""

from __future__ import annotations

import unicodedata

from rich.text import Text

TERMINAL_VALUE_MAXIMUM_CELLS = 128
_TERMINAL_VALUE_SOURCE_LIMIT = 256


def bounded_terminal_value(
    value: object | None,
    *,
    unavailable: str,
) -> str:
    """Return one literal value without allowing terminal row injection.

    Scanner-provided prefixes, leading zeroes and ordinary Unicode are retained.
    Controls, bidi formatting and line separators become visible question marks,
    and the resulting value is bounded by terminal cell width.
    """

    if value is None:
        return unavailable
    source = value if isinstance(value, str) else str(value)
    if not source.strip():
        return unavailable
    safe = "".join(
        "?"
        if unicodedata.category(character).startswith("C") or character in "\u2028\u2029"
        else character
        for character in source[:_TERMINAL_VALUE_SOURCE_LIMIT]
    )
    text = Text(safe)
    text.truncate(TERMINAL_VALUE_MAXIMUM_CELLS, overflow="ellipsis")
    return text.plain
