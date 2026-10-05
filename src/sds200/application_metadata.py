"""Small, passive validators for optional reported application metadata."""

from __future__ import annotations

from re import fullmatch


def reported_application_version(value: object) -> str | None:
    """Bound a display-only version; old or invalid reports stay unavailable.

    This is not protocol negotiation, a compatibility guarantee or a fallback
    to the client's build. Short ASCII tokens cannot inject terminal controls
    or markup. Invalid optional metadata does not break an older daemon session.
    """
    if type(value) is str and fullmatch(r"[0-9][0-9A-Za-z.!+_-]{0,63}", value):
        return value
    return None
