"""Bounded process-local ownership for a future Favorites web workspace.

This module deliberately owns no HTTP routes and performs no write execution.
It binds an already-authorized opaque owner to one configured copied-tree
source, mediates revisioned in-memory edits, and expires all state on process
restart.  A browser-supplied filesystem path is never accepted or returned.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from hmac import compare_digest
from pathlib import Path

from .favorites_catalog import FavoritesCatalogError
from .favorites_comparison import (
    FavoritesComparisonAmbiguity,
    FavoritesComparisonChangeKind,
    FavoritesComparisonSource,
)
from .favorites_editor import (
    FavoritesEditorSession,
    open_favorites_copied_tree_editor,
)
from .favorites_file import FavoritesFileParseError
from .favorites_hierarchy import FavoritesHierarchyError
from .favorites_navigation import (
    FavoritesNavigationKind,
    FavoritesNavigationPath,
)
from .favorites_schema import (
    FavoritesSchemaRule,
    FavoritesSchemaSeverity,
)
from .favorites_storage import FavoritesStorageFilenameError
from .favorites_storage_local import FavoritesCopiedTreeStorageError
from .favorites_write_plan import FavoritesWriteBlocker

FAVORITES_WEB_DEFAULT_MAX_WORKSPACES = 8
FAVORITES_WEB_DEFAULT_MAX_WORKSPACES_PER_OWNER = 2
FAVORITES_WEB_DEFAULT_MAX_DOCUMENTS = 256
FAVORITES_WEB_DEFAULT_MAX_RECORDS = 100_000
FAVORITES_WEB_DEFAULT_MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
FAVORITES_WEB_DEFAULT_MAX_MUTATIONS = 256
FAVORITES_WEB_DEFAULT_IDLE_SECONDS = 30 * 60
FAVORITES_WEB_DEFAULT_ABSOLUTE_SECONDS = 2 * 60 * 60

_SOURCE_LABEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_WORKSPACE_ID = re.compile(r"[A-Za-z0-9_-]{24,128}")
_NODE_ID = re.compile(r"[A-Za-z0-9_-]{16,128}")
_TokenFactory = Callable[[], str]
_Clock = Callable[[], float]


class FavoritesWebWorkspaceError(RuntimeError):
    """Base failure for the bounded web-workspace owner."""


class FavoritesWebWorkspaceNotFoundError(FavoritesWebWorkspaceError):
    """Hide whether an identifier, owner, or expired workspace was wrong."""

    def __init__(self) -> None:
        super().__init__("Favorites web workspace is unavailable.")


class FavoritesWebWorkspaceConflictError(FavoritesWebWorkspaceError):
    """Reject a mutation or review against a stale workspace revision."""

    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Favorites web workspace revision is stale: expected {expected}, actual {actual}."
        )


class FavoritesWebWorkspaceLimitError(FavoritesWebWorkspaceError):
    """Reject work rather than evicting another active editor."""


@dataclass(frozen=True, slots=True)
class FavoritesWebWorkspaceSource:
    """One administrator-configured copied-tree source label and path."""

    label: str
    directory: Path = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or _SOURCE_LABEL.fullmatch(self.label) is None:
            raise ValueError("Favorites web source label is invalid.")
        if not isinstance(self.directory, Path) or not self.directory.is_absolute():
            raise ValueError("Favorites web source directory must be one absolute path.")


@dataclass(frozen=True, slots=True)
class FavoritesWebWorkspaceLimits:
    """Hard process-local resource and lifetime limits."""

    max_workspaces: int = FAVORITES_WEB_DEFAULT_MAX_WORKSPACES
    max_workspaces_per_owner: int = FAVORITES_WEB_DEFAULT_MAX_WORKSPACES_PER_OWNER
    max_documents: int = FAVORITES_WEB_DEFAULT_MAX_DOCUMENTS
    max_records: int = FAVORITES_WEB_DEFAULT_MAX_RECORDS
    max_snapshot_bytes: int = FAVORITES_WEB_DEFAULT_MAX_SNAPSHOT_BYTES
    max_mutations: int = FAVORITES_WEB_DEFAULT_MAX_MUTATIONS
    idle_seconds: float = FAVORITES_WEB_DEFAULT_IDLE_SECONDS
    absolute_seconds: float = FAVORITES_WEB_DEFAULT_ABSOLUTE_SECONDS

    def __post_init__(self) -> None:
        integers = (
            self.max_workspaces,
            self.max_workspaces_per_owner,
            self.max_documents,
            self.max_records,
            self.max_snapshot_bytes,
            self.max_mutations,
        )
        durations = (self.idle_seconds, self.absolute_seconds)
        if (
            any(type(value) is not int or value <= 0 for value in integers)
            or self.max_workspaces_per_owner > self.max_workspaces
            or any(
                type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                for value in durations
            )
            or self.idle_seconds > self.absolute_seconds
        ):
            raise ValueError("Favorites web workspace limits are invalid.")


@dataclass(frozen=True, slots=True)
class FavoritesWebWorkspaceSummary:
    """Path-free state safe for an authorized renderer to present."""

    workspace_id: str
    source_label: str
    revision: int
    dirty: bool
    undo_count: int
    mutation_count: int
    document_count: int
    record_count: int
    snapshot_bytes: int
    diagnostic_count: int
    error_count: int
    created_at: float
    idle_expires_at: float
    absolute_expires_at: float


@dataclass(frozen=True, slots=True)
class FavoritesWebNode:
    """One path-free navigation node safe for an authorized renderer."""

    node_id: str
    parent_node_id: str | None
    kind: FavoritesNavigationKind
    name: str | None
    child_count: int
    can_rename: bool
    can_duplicate: bool
    can_delete: bool


@dataclass(frozen=True, slots=True)
class FavoritesWebReviewChangeSummary:
    """A bounded aggregate of exact record changes without source records."""

    kind: FavoritesComparisonChangeKind
    count: int


@dataclass(frozen=True, slots=True)
class FavoritesWebReviewDiagnosticSummary:
    """A bounded aggregate of validation diagnostics without provenance."""

    rule: FavoritesSchemaRule
    severity: FavoritesSchemaSeverity
    count: int


@dataclass(frozen=True, slots=True)
class FavoritesWebWorkspaceReview:
    """Path-free reviewed state retaining only a separate confirmation token."""

    workspace_id: str
    revision: int
    confirmation_token: str = field(repr=False)
    blocked: bool
    blockers: tuple[FavoritesWriteBlocker, ...]
    changed_source_count: int
    ambiguity_count: int
    changes: tuple[FavoritesWebReviewChangeSummary, ...]
    diagnostics: tuple[FavoritesWebReviewDiagnosticSummary, ...]


@dataclass(slots=True)
class _Workspace:
    workspace_id: str
    owner_digest: bytes = field(repr=False)
    source_label: str
    editor: FavoritesEditorSession = field(repr=False)
    revision: int
    mutation_count: int
    document_count: int
    record_count: int
    snapshot_bytes: int
    created_at: float
    idle_expires_at: float
    absolute_expires_at: float
    node_revision: int = -1
    node_ids_by_path: dict[FavoritesNavigationPath, str] = field(
        default_factory=dict,
        repr=False,
    )
    node_paths_by_id: dict[str, FavoritesNavigationPath] = field(
        default_factory=dict,
        repr=False,
    )


def _owner_digest(owner_secret: bytes) -> bytes:
    if type(owner_secret) is not bytes:
        raise TypeError("Favorites web workspace owner secret must be bytes.")
    if not 16 <= len(owner_secret) <= 1024:
        raise ValueError("Favorites web workspace owner secret has an invalid length.")
    return hashlib.sha256(owner_secret).digest()


def _expected_revision(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Favorites web workspace revision must be non-negative.")
    return value


class FavoritesWebWorkspaces:
    """Own bounded revisioned editor sessions for authorized future routes.

    Callers must derive ``owner_secret`` from an already-authorized server-side
    session context.  A workspace identifier is never sufficient authority.
    All methods are synchronous and serialized; an ASGI adapter must use its
    existing bounded worker admission rather than unbounded ``to_thread`` work.
    """

    def __init__(
        self,
        sources: tuple[FavoritesWebWorkspaceSource, ...],
        *,
        limits: FavoritesWebWorkspaceLimits | None = None,
        clock: _Clock = time.monotonic,
        token_factory: _TokenFactory | None = None,
        node_token_factory: _TokenFactory | None = None,
    ) -> None:
        if type(sources) is not tuple or any(
            not isinstance(source, FavoritesWebWorkspaceSource) for source in sources
        ):
            raise TypeError("Favorites web workspace sources must be one exact tuple.")
        if not sources:
            raise ValueError("Favorites web workspace requires a configured source.")
        by_label = {source.label: source for source in sources}
        if len(by_label) != len(sources):
            raise ValueError("Favorites web workspace source labels must be unique.")
        if limits is None:
            limits = FavoritesWebWorkspaceLimits()
        elif not isinstance(limits, FavoritesWebWorkspaceLimits):
            raise TypeError("Favorites web workspace limits are invalid.")
        if (
            not callable(clock)
            or (token_factory is not None and not callable(token_factory))
            or (node_token_factory is not None and not callable(node_token_factory))
        ):
            raise TypeError("Favorites web workspace factories must be callable.")
        self._sources = by_label
        self._limits = limits
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._node_token_factory = node_token_factory or (lambda: secrets.token_urlsafe(24))
        self._lock = threading.RLock()
        self._workspaces: dict[str, _Workspace] = {}
        self._closed = False

    @property
    def source_labels(self) -> tuple[str, ...]:
        """Return configured public labels without exposing filesystem paths."""

        return tuple(self._sources)

    def _require_open_locked(self) -> None:
        if self._closed:
            raise FavoritesWebWorkspaceError("Favorites web workspaces are closed.")

    def _remove_locked(self, workspace_id: str) -> bool:
        return self._workspaces.pop(workspace_id, None) is not None

    def _purge_locked(self, now: float) -> int:
        expired = tuple(
            workspace_id
            for workspace_id, workspace in self._workspaces.items()
            if now >= workspace.absolute_expires_at or now >= workspace.idle_expires_at
        )
        for workspace_id in expired:
            self._remove_locked(workspace_id)
        return len(expired)

    def purge_expired(self) -> int:
        """Discard expired in-memory workspaces without exposing their owners."""

        with self._lock:
            self._require_open_locked()
            return self._purge_locked(self._clock())

    def close(self) -> None:
        """Terminally discard every workspace and reject later use."""

        with self._lock:
            self._closed = True
            self._workspaces.clear()

    def _new_id_locked(self) -> str:
        for _attempt in range(4):
            candidate = self._token_factory()
            if (
                isinstance(candidate, str)
                and _WORKSPACE_ID.fullmatch(candidate) is not None
                and candidate not in self._workspaces
            ):
                return candidate
        raise FavoritesWebWorkspaceLimitError(
            "Favorites web workspace identifier generation is unavailable."
        )

    def _snapshot_shape(self, editor: FavoritesEditorSession) -> tuple[int, int, int]:
        snapshot = editor.baseline_snapshot
        documents = len(snapshot.documents)
        snapshot_bytes = len(snapshot.catalog_bytes) + sum(
            len(document.content) for document in snapshot.documents
        )
        if documents > self._limits.max_documents:
            raise FavoritesWebWorkspaceLimitError(
                "Favorites web workspace document limit was exceeded."
            )
        if snapshot_bytes > self._limits.max_snapshot_bytes:
            raise FavoritesWebWorkspaceLimitError(
                "Favorites web workspace byte limit was exceeded."
            )
        records = len(editor.records())
        if records > self._limits.max_records:
            raise FavoritesWebWorkspaceLimitError(
                "Favorites web workspace record limit was exceeded."
            )
        return documents, records, snapshot_bytes

    def create(self, owner_secret: bytes, source_label: str) -> FavoritesWebWorkspaceSummary:
        """Open one configured source without accepting a browser path."""

        owner = _owner_digest(owner_secret)
        if not isinstance(source_label, str):
            raise TypeError("Favorites web source label must be text.")
        with self._lock:
            self._require_open_locked()
            now = self._clock()
            self._purge_locked(now)
            source = self._sources.get(source_label)
            if source is None:
                raise FavoritesWebWorkspaceNotFoundError()
            if len(self._workspaces) >= self._limits.max_workspaces:
                raise FavoritesWebWorkspaceLimitError("Favorites web workspace capacity is full.")
            if (
                sum(
                    compare_digest(workspace.owner_digest, owner)
                    for workspace in self._workspaces.values()
                )
                >= self._limits.max_workspaces_per_owner
            ):
                raise FavoritesWebWorkspaceLimitError(
                    "Favorites web workspace owner capacity is full."
                )
            try:
                editor = open_favorites_copied_tree_editor(source.directory)
                documents, records, snapshot_bytes = self._snapshot_shape(editor)
            except FavoritesWebWorkspaceLimitError:
                raise
            except (
                FavoritesCatalogError,
                FavoritesCopiedTreeStorageError,
                FavoritesFileParseError,
                FavoritesHierarchyError,
                FavoritesStorageFilenameError,
            ):
                raise FavoritesWebWorkspaceNotFoundError() from None
            workspace_id = self._new_id_locked()
            absolute_expires_at = now + self._limits.absolute_seconds
            workspace = _Workspace(
                workspace_id=workspace_id,
                owner_digest=owner,
                source_label=source.label,
                editor=editor,
                revision=0,
                mutation_count=0,
                document_count=documents,
                record_count=records,
                snapshot_bytes=snapshot_bytes,
                created_at=now,
                idle_expires_at=min(absolute_expires_at, now + self._limits.idle_seconds),
                absolute_expires_at=absolute_expires_at,
            )
            summary = self._summary_locked(workspace)
            self._workspaces[workspace_id] = workspace
            return summary

    def _find_locked(
        self,
        owner: bytes,
        workspace_id: str,
        *,
        expected_revision: int | None = None,
    ) -> _Workspace:
        self._require_open_locked()
        now = self._clock()
        self._purge_locked(now)
        if not isinstance(workspace_id, str) or _WORKSPACE_ID.fullmatch(workspace_id) is None:
            raise FavoritesWebWorkspaceNotFoundError()
        workspace = self._workspaces.get(workspace_id)
        if workspace is None or not compare_digest(workspace.owner_digest, owner):
            raise FavoritesWebWorkspaceNotFoundError()
        if expected_revision is not None:
            expected = _expected_revision(expected_revision)
            if workspace.revision != expected:
                raise FavoritesWebWorkspaceConflictError(
                    expected=expected, actual=workspace.revision
                )
        workspace.idle_expires_at = min(
            workspace.absolute_expires_at, now + self._limits.idle_seconds
        )
        return workspace

    def _summary_locked(self, workspace: _Workspace) -> FavoritesWebWorkspaceSummary:
        validation = workspace.editor.validation
        return FavoritesWebWorkspaceSummary(
            workspace_id=workspace.workspace_id,
            source_label=workspace.source_label,
            revision=workspace.revision,
            dirty=workspace.editor.has_changes,
            undo_count=workspace.editor.undo_count,
            mutation_count=workspace.mutation_count,
            document_count=workspace.document_count,
            record_count=workspace.record_count,
            snapshot_bytes=workspace.snapshot_bytes,
            diagnostic_count=len(validation.diagnostics),
            error_count=sum(
                diagnostic.severity is FavoritesSchemaSeverity.ERROR
                for diagnostic in validation.diagnostics
            ),
            created_at=workspace.created_at,
            idle_expires_at=workspace.idle_expires_at,
            absolute_expires_at=workspace.absolute_expires_at,
        )

    def summary(
        self,
        owner_secret: bytes,
        workspace_id: str,
        *,
        expected_revision: int | None = None,
    ) -> FavoritesWebWorkspaceSummary:
        """Return one authorized path-free summary and refresh idle expiry."""

        owner = _owner_digest(owner_secret)
        with self._lock:
            return self._summary_locked(
                self._find_locked(owner, workspace_id, expected_revision=expected_revision)
            )

    def _invalidate_node_ids_locked(self, workspace: _Workspace) -> None:
        workspace.node_revision = workspace.revision
        workspace.node_ids_by_path.clear()
        workspace.node_paths_by_id.clear()

    def _node_id_locked(
        self,
        workspace: _Workspace,
        path: FavoritesNavigationPath,
    ) -> str:
        if workspace.node_revision != workspace.revision:
            self._invalidate_node_ids_locked(workspace)
        existing = workspace.node_ids_by_path.get(path)
        if existing is not None:
            return existing
        for _attempt in range(4):
            candidate = self._node_token_factory()
            if (
                isinstance(candidate, str)
                and _NODE_ID.fullmatch(candidate) is not None
                and candidate not in workspace.node_paths_by_id
            ):
                workspace.node_ids_by_path[path] = candidate
                workspace.node_paths_by_id[candidate] = path
                return candidate
        raise FavoritesWebWorkspaceLimitError(
            "Favorites web node identifier generation is unavailable."
        )

    def _node_path_locked(
        self,
        workspace: _Workspace,
        node_id: str,
    ) -> FavoritesNavigationPath:
        if workspace.node_revision != workspace.revision:
            self._invalidate_node_ids_locked(workspace)
        if not isinstance(node_id, str) or _NODE_ID.fullmatch(node_id) is None:
            raise FavoritesWebWorkspaceNotFoundError()
        path = workspace.node_paths_by_id.get(node_id)
        if path is None:
            raise FavoritesWebWorkspaceNotFoundError()
        return path

    def nodes(
        self,
        owner_secret: bytes,
        workspace_id: str,
        *,
        expected_revision: int,
        text: str | None = None,
    ) -> tuple[FavoritesWebNode, ...]:
        """Return a path-free current authorized navigation projection."""

        owner = _owner_digest(owner_secret)
        with self._lock:
            workspace = self._find_locked(owner, workspace_id, expected_revision=expected_revision)
            nodes = workspace.editor.nodes(text)
            return tuple(
                FavoritesWebNode(
                    node_id=self._node_id_locked(workspace, node.path),
                    parent_node_id=(
                        None
                        if node.path.parent is None
                        else self._node_id_locked(workspace, node.path.parent)
                    ),
                    kind=node.kind,
                    name=node.name,
                    child_count=len(node.children),
                    can_rename=True,
                    can_duplicate=node.kind
                    in (
                        FavoritesNavigationKind.CONVENTIONAL_CHANNEL,
                        FavoritesNavigationKind.TRUNK_CHANNEL,
                    ),
                    can_delete=node.kind
                    in (
                        FavoritesNavigationKind.CONVENTIONAL_CHANNEL,
                        FavoritesNavigationKind.TRUNK_CHANNEL,
                    ),
                )
                for node in nodes
            )

    def _before_mutation_locked(self, workspace: _Workspace) -> None:
        if workspace.mutation_count >= self._limits.max_mutations:
            raise FavoritesWebWorkspaceLimitError(
                "Favorites web workspace mutation limit was reached."
            )

    def _after_mutation_locked(self, workspace: _Workspace) -> FavoritesWebWorkspaceSummary:
        record_count = len(workspace.editor.records())
        if record_count > self._limits.max_records:
            if not workspace.editor.undo():
                raise RuntimeError("Favorites web workspace mutation rollback failed.")
            raise FavoritesWebWorkspaceLimitError(
                "Favorites web workspace record limit was exceeded."
            )
        workspace.revision += 1
        workspace.mutation_count += 1
        workspace.record_count = record_count
        self._invalidate_node_ids_locked(workspace)
        return self._summary_locked(workspace)

    def rename(
        self,
        owner_secret: bytes,
        workspace_id: str,
        *,
        expected_revision: int,
        node_id: str,
        name: str,
    ) -> FavoritesWebWorkspaceSummary:
        """Apply one renderer-neutral rename at an exact revision."""

        owner = _owner_digest(owner_secret)
        with self._lock:
            workspace = self._find_locked(owner, workspace_id, expected_revision=expected_revision)
            self._before_mutation_locked(workspace)
            workspace.editor.rename(
                self._node_path_locked(workspace, node_id),
                name,
            )
            return self._after_mutation_locked(workspace)

    def duplicate(
        self,
        owner_secret: bytes,
        workspace_id: str,
        *,
        expected_revision: int,
        node_id: str,
        name: str | None = None,
    ) -> FavoritesWebWorkspaceSummary:
        """Apply one renderer-neutral leaf duplicate at an exact revision."""

        owner = _owner_digest(owner_secret)
        with self._lock:
            workspace = self._find_locked(owner, workspace_id, expected_revision=expected_revision)
            self._before_mutation_locked(workspace)
            if workspace.record_count >= self._limits.max_records:
                raise FavoritesWebWorkspaceLimitError(
                    "Favorites web workspace record limit was reached."
                )
            workspace.editor.duplicate(
                self._node_path_locked(workspace, node_id),
                name=name,
            )
            return self._after_mutation_locked(workspace)

    def delete(
        self,
        owner_secret: bytes,
        workspace_id: str,
        *,
        expected_revision: int,
        node_id: str,
    ) -> FavoritesWebWorkspaceSummary:
        """Apply one renderer-neutral leaf delete at an exact revision."""

        owner = _owner_digest(owner_secret)
        with self._lock:
            workspace = self._find_locked(owner, workspace_id, expected_revision=expected_revision)
            self._before_mutation_locked(workspace)
            workspace.editor.delete(self._node_path_locked(workspace, node_id))
            return self._after_mutation_locked(workspace)

    def undo(
        self,
        owner_secret: bytes,
        workspace_id: str,
        *,
        expected_revision: int,
    ) -> FavoritesWebWorkspaceSummary:
        """Undo once; a no-op does not consume a revision or mutation slot."""

        owner = _owner_digest(owner_secret)
        with self._lock:
            workspace = self._find_locked(owner, workspace_id, expected_revision=expected_revision)
            if workspace.editor.undo_count == 0:
                return self._summary_locked(workspace)
            self._before_mutation_locked(workspace)
            if not workspace.editor.undo():
                raise RuntimeError("Favorites web workspace undo state changed unexpectedly.")
            return self._after_mutation_locked(workspace)

    def reset(
        self,
        owner_secret: bytes,
        workspace_id: str,
        *,
        expected_revision: int,
    ) -> FavoritesWebWorkspaceSummary:
        """Reset to baseline; a no-op does not consume a revision."""

        owner = _owner_digest(owner_secret)
        with self._lock:
            workspace = self._find_locked(owner, workspace_id, expected_revision=expected_revision)
            if not workspace.editor.has_changes:
                return self._summary_locked(workspace)
            self._before_mutation_locked(workspace)
            if not workspace.editor.reset():
                raise RuntimeError("Favorites web workspace reset state changed unexpectedly.")
            return self._after_mutation_locked(workspace)

    def review(
        self,
        owner_secret: bytes,
        workspace_id: str,
        *,
        expected_revision: int,
    ) -> FavoritesWebWorkspaceReview:
        """Return the exact plan/token without executing or changing revision."""

        owner = _owner_digest(owner_secret)
        with self._lock:
            workspace = self._find_locked(owner, workspace_id, expected_revision=expected_revision)
            reviewed = workspace.editor.review()
            plan = reviewed.plan
            change_counts = {kind: 0 for kind in FavoritesComparisonChangeKind}
            changed_source_count = 0
            ambiguity_count = 0
            for item in plan.comparison.items:
                if isinstance(item, FavoritesComparisonAmbiguity):
                    ambiguity_count += 1
                    continue
                if not isinstance(item, FavoritesComparisonSource):
                    raise RuntimeError("Favorites web review comparison item is invalid.")
                if item.changed:
                    changed_source_count += 1
                for change in item.record_changes:
                    change_counts[change.kind] += 1

            diagnostic_counts: dict[
                tuple[FavoritesSchemaRule, FavoritesSchemaSeverity],
                int,
            ] = {}
            for diagnostic in plan.intended_validation.diagnostics:
                key = (diagnostic.rule, diagnostic.severity)
                diagnostic_counts[key] = diagnostic_counts.get(key, 0) + 1

            return FavoritesWebWorkspaceReview(
                workspace_id=workspace.workspace_id,
                revision=workspace.revision,
                confirmation_token=reviewed.confirmation_token,
                blocked=plan.is_blocked,
                blockers=plan.blockers,
                changed_source_count=changed_source_count,
                ambiguity_count=ambiguity_count,
                changes=tuple(
                    FavoritesWebReviewChangeSummary(kind=kind, count=count)
                    for kind, count in change_counts.items()
                    if count
                ),
                diagnostics=tuple(
                    FavoritesWebReviewDiagnosticSummary(
                        rule=rule,
                        severity=severity,
                        count=count,
                    )
                    for (rule, severity), count in diagnostic_counts.items()
                ),
            )

    def discard(self, owner_secret: bytes, workspace_id: str) -> None:
        """Discard one authorized workspace without executing its plan."""

        owner = _owner_digest(owner_secret)
        with self._lock:
            workspace = self._find_locked(owner, workspace_id)
            self._remove_locked(workspace.workspace_id)

    def discard_owner(self, owner_secret: bytes) -> int:
        """Discard every workspace for one revoked/logout owner."""

        owner = _owner_digest(owner_secret)
        with self._lock:
            self._require_open_locked()
            owned = tuple(
                workspace_id
                for workspace_id, workspace in self._workspaces.items()
                if compare_digest(workspace.owner_digest, owner)
            )
            for workspace_id in owned:
                self._remove_locked(workspace_id)
            return len(owned)


__all__ = [
    "FAVORITES_WEB_DEFAULT_ABSOLUTE_SECONDS",
    "FAVORITES_WEB_DEFAULT_IDLE_SECONDS",
    "FAVORITES_WEB_DEFAULT_MAX_DOCUMENTS",
    "FAVORITES_WEB_DEFAULT_MAX_MUTATIONS",
    "FAVORITES_WEB_DEFAULT_MAX_RECORDS",
    "FAVORITES_WEB_DEFAULT_MAX_SNAPSHOT_BYTES",
    "FAVORITES_WEB_DEFAULT_MAX_WORKSPACES",
    "FAVORITES_WEB_DEFAULT_MAX_WORKSPACES_PER_OWNER",
    "FavoritesWebWorkspaceConflictError",
    "FavoritesWebWorkspaceError",
    "FavoritesWebWorkspaceLimitError",
    "FavoritesWebWorkspaceLimits",
    "FavoritesWebWorkspaceNotFoundError",
    "FavoritesWebNode",
    "FavoritesWebReviewChangeSummary",
    "FavoritesWebReviewDiagnosticSummary",
    "FavoritesWebWorkspaceReview",
    "FavoritesWebWorkspaceSource",
    "FavoritesWebWorkspaceSummary",
    "FavoritesWebWorkspaces",
]
