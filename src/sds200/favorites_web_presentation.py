"""Explicit JSON-safe presentation for future Favorites web routes.

The workspace owner deliberately returns typed path-free DTOs.  This module is
the only field-by-field conversion intended for a future HTTP adapter; it does
not accept arbitrary dataclasses, internal editor objects, or ``repr`` output.
"""

from __future__ import annotations

from .favorites_web_workspace import (
    FavoritesWebNode,
    FavoritesWebWorkspaceReview,
    FavoritesWebWorkspaceSummary,
)


def present_favorites_web_summary(
    summary: FavoritesWebWorkspaceSummary,
) -> dict[str, object]:
    """Return one path-free workspace summary made only of JSON primitives."""

    if not isinstance(summary, FavoritesWebWorkspaceSummary):
        raise TypeError("Favorites web presentation requires a workspace summary.")
    return {
        "workspace_id": summary.workspace_id,
        "source_label": summary.source_label,
        "revision": summary.revision,
        "dirty": summary.dirty,
        "undo_count": summary.undo_count,
        "mutation_count": summary.mutation_count,
        "document_count": summary.document_count,
        "record_count": summary.record_count,
        "snapshot_bytes": summary.snapshot_bytes,
        "diagnostic_count": summary.diagnostic_count,
        "error_count": summary.error_count,
        "created_at": summary.created_at,
        "idle_expires_at": summary.idle_expires_at,
        "absolute_expires_at": summary.absolute_expires_at,
    }


def present_favorites_web_node(node: FavoritesWebNode) -> dict[str, object]:
    """Return one opaque renderer node without internal source provenance."""

    if not isinstance(node, FavoritesWebNode):
        raise TypeError("Favorites web presentation requires a renderer node.")
    return {
        "node_id": node.node_id,
        "parent_node_id": node.parent_node_id,
        "kind": node.kind.value,
        "name": node.name,
        "child_count": node.child_count,
        "operations": {
            "rename": node.can_rename,
            "duplicate": node.can_duplicate,
            "delete": node.can_delete,
        },
    }


def present_favorites_web_nodes(
    nodes: tuple[FavoritesWebNode, ...],
) -> dict[str, object]:
    """Return one deterministic bounded node collection."""

    if type(nodes) is not tuple or any(not isinstance(node, FavoritesWebNode) for node in nodes):
        raise TypeError("Favorites web presentation requires an exact node tuple.")
    return {"nodes": [present_favorites_web_node(node) for node in nodes]}


def present_favorites_web_review(
    review: FavoritesWebWorkspaceReview,
) -> dict[str, object]:
    """Return a safe aggregate review plus its separate confirmation token."""

    if not isinstance(review, FavoritesWebWorkspaceReview):
        raise TypeError("Favorites web presentation requires a workspace review.")
    return {
        "workspace_id": review.workspace_id,
        "revision": review.revision,
        "confirmation_token": review.confirmation_token,
        "blocked": review.blocked,
        "blockers": [blocker.value for blocker in review.blockers],
        "changed_source_count": review.changed_source_count,
        "ambiguity_count": review.ambiguity_count,
        "changes": [
            {"kind": change.kind.value, "count": change.count} for change in review.changes
        ],
        "diagnostics": [
            {
                "rule": diagnostic.rule.value,
                "severity": diagnostic.severity.value,
                "count": diagnostic.count,
            }
            for diagnostic in review.diagnostics
        ],
    }


__all__ = [
    "present_favorites_web_node",
    "present_favorites_web_nodes",
    "present_favorites_web_review",
    "present_favorites_web_summary",
]
