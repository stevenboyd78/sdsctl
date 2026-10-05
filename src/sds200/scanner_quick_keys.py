"""Bounded quick-key scope from an already-qualified scan observation.

No inference from object indices, cached selections, profile data or LCD rows.
"""

from dataclasses import dataclass

from .models import ScannerInfo


@dataclass(frozen=True, slots=True)
class QuickKeySelection:
    """Assigned Q_Key values, not scanner object indices or LCD decade choices."""

    favorites: int | None
    system: int | None

    def __post_init__(self) -> None:
        for value in (self.favorites, self.system):
            if value is not None and (type(value) is not int or not 0 <= value <= 99):
                raise ValueError("Quick-key selectors must be 0–99 or None.")
        if self.favorites is None and self.system is not None:
            raise ValueError("A system quick key requires an assigned Favorites scope.")


def quick_key_selection(info: ScannerInfo) -> QuickKeySelection | None:
    """Adapter has already rejected duplicate, foreign and obscured records."""
    if info.screen not in ("conventional_scan", "trunk_scan"):
        return None
    keys: list[int | None] = []
    for tag in ("MonitorList", "System"):
        node = info.node(tag)
        value = None if node is None else node.get("Q_Key")
        if value == "None":
            keys.append(None)
        elif (
            isinstance(value, str)
            and 1 <= len(value) <= 2
            and value.isascii()
            and value.isdecimal()
        ):
            keys.append(int(value))
        else:
            return None  # Missing is not explicitly unassigned.
    if keys[0] is None and keys[1] is not None:
        return None
    return QuickKeySelection(*keys)
