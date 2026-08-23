"""Renderer-neutral interactive Favorites Workspace editor session."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeAlias

from .favorites_editing import (
    FavoritesRecordSourceKind,
    FavoritesRecordTarget,
    create_favorites_record_after,
    delete_favorites_record,
    rename_favorites_record,
    select_favorites_record_target,
)
from .favorites_file import FavoritesSourceRecord
from .favorites_navigation import (
    FavoritesNavigation,
    FavoritesNavigationNode,
    FavoritesNavigationPath,
    project_favorites_navigation,
)
from .favorites_query import FavoritesNavigationQuery, query_favorites_navigation
from .favorites_schema import FavoritesSchemaValidation, validate_favorites_workspace
from .favorites_storage import (
    FavoritesStorageSnapshot,
    project_favorites_storage_snapshot,
)
from .favorites_storage_local import FavoritesCopiedTreeStorageSource
from .favorites_storage_usb import (
    DEFAULT_LINUX_MOUNTINFO_PATH,
    DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY,
    FavoritesUsbStorageQualification,
    qualify_favorites_usb_storage_path,
)
from .favorites_workspace import FavoritesWorkspace
from .favorites_write_execution import (
    FavoritesCopiedTreeWriteExecutionResult,
    execute_favorites_copied_tree_write,
)
from .favorites_write_plan import FavoritesWritePlan, plan_favorites_write
from .favorites_write_usb import (
    FavoritesUsbWriteExecutionResult,
    execute_favorites_usb_write,
)


class FavoritesEditorSourceKind(StrEnum):
    """Identify the one explicit editor storage source."""

    COPIED_TREE = "copied_tree"
    USB = "usb"


FavoritesEditorWriteResult: TypeAlias = (
    FavoritesCopiedTreeWriteExecutionResult | FavoritesUsbWriteExecutionResult
)


class FavoritesEditorStorage(Protocol):
    """Narrow storage boundary consumed by the editor session."""

    @property
    def kind(self) -> FavoritesEditorSourceKind: ...

    @property
    def requested_path(self) -> Path: ...

    @property
    def favorites_directory(self) -> Path: ...

    def read_snapshot(self) -> FavoritesStorageSnapshot: ...

    def execute(self, plan: FavoritesWritePlan) -> FavoritesEditorWriteResult: ...


@dataclass(frozen=True, slots=True)
class FavoritesCopiedTreeEditorStorage:
    """Bind an editor session to one explicit copied Favorites directory."""

    requested_path: Path

    def __post_init__(self) -> None:
        if not isinstance(self.requested_path, Path):
            raise TypeError("Favorites copied-tree editor path must be pathlib.Path.")

    @property
    def kind(self) -> FavoritesEditorSourceKind:
        return FavoritesEditorSourceKind.COPIED_TREE

    @property
    def favorites_directory(self) -> Path:
        return self.requested_path.resolve(strict=True)

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        return FavoritesCopiedTreeStorageSource(self.requested_path).read_snapshot()

    def execute(self, plan: FavoritesWritePlan) -> FavoritesEditorWriteResult:
        return execute_favorites_copied_tree_write(plan, self.requested_path)


@dataclass(frozen=True, slots=True)
class FavoritesUsbEditorStorage:
    """Bind an editor session to one freshly qualified Linux USB target."""

    requested_path: Path
    host_state_directory: Path
    mountinfo_path: Path = DEFAULT_LINUX_MOUNTINFO_PATH
    sys_dev_block_directory: Path = DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY

    def __post_init__(self) -> None:
        for label, value in (
            ("path", self.requested_path),
            ("host-state directory", self.host_state_directory),
            ("mountinfo path", self.mountinfo_path),
            ("sysfs block directory", self.sys_dev_block_directory),
        ):
            if not isinstance(value, Path):
                raise TypeError(f"Favorites USB editor {label} must be pathlib.Path.")
            if not value.is_absolute():
                raise ValueError(f"Favorites USB editor {label} must be absolute.")

    @property
    def kind(self) -> FavoritesEditorSourceKind:
        return FavoritesEditorSourceKind.USB

    def _qualification(self) -> FavoritesUsbStorageQualification:
        return qualify_favorites_usb_storage_path(
            self.requested_path,
            self.mountinfo_path,
            sys_dev_block_directory=self.sys_dev_block_directory,
        )

    @property
    def favorites_directory(self) -> Path:
        return self._qualification().favorites_directory

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        return self._qualification().snapshot

    def execute(self, plan: FavoritesWritePlan) -> FavoritesEditorWriteResult:
        return execute_favorites_usb_write(
            plan,
            self.requested_path,
            self.mountinfo_path,
            sys_dev_block_directory=self.sys_dev_block_directory,
            host_state_directory=self.host_state_directory,
        )


@dataclass(frozen=True, slots=True)
class FavoritesEditorReview:
    """Exact reviewed plan and the token required for separate execution."""

    plan: FavoritesWritePlan
    confirmation_token: str


@dataclass(frozen=True, slots=True)
class FavoritesEditorRecordReference:
    """Address one current exact catalog or HPD source record for raw browsing."""

    source_kind: FavoritesRecordSourceKind
    source_index: int
    document_index: int | None
    filename: str
    record: FavoritesSourceRecord

    def __post_init__(self) -> None:
        if not isinstance(self.source_kind, FavoritesRecordSourceKind):
            raise TypeError("Favorites editor record source kind is invalid.")
        if type(self.source_index) is not int or self.source_index < 0:
            raise ValueError("Favorites editor record source index must be non-negative.")
        if not isinstance(self.filename, str) or not self.filename:
            raise ValueError("Favorites editor record filename must be non-empty.")
        if not isinstance(self.record, FavoritesSourceRecord):
            raise TypeError("Favorites editor record must preserve FavoritesSourceRecord.")
        if self.source_kind is FavoritesRecordSourceKind.CATALOG:
            if self.document_index is not None or self.filename != "f_list.cfg":
                raise ValueError("Favorites editor catalog record provenance is invalid.")
        elif type(self.document_index) is not int or self.document_index < 0:
            raise ValueError("Favorites editor HPD record document index must be non-negative.")


@dataclass(frozen=True, slots=True)
class FavoritesEditorExecution:
    """Completed execution plus exact post-write reload evidence."""

    result: FavoritesEditorWriteResult
    reloaded_snapshot: FavoritesStorageSnapshot


class FavoritesEditorError(RuntimeError):
    """Report an invalid editor action or failed editor safety condition."""


class FavoritesEditorReloadError(FavoritesEditorError):
    """Report post-write reload mismatch while retaining operation evidence."""

    def __init__(
        self,
        result: FavoritesEditorWriteResult,
        *,
        detail: str = "did not exactly match the reviewed intended snapshot",
    ) -> None:
        self.result = result
        super().__init__(
            f"Favorites write completed but fresh reload {detail}. "
            "Inspect durable operation artifacts."
        )


def _walk_nodes(
    roots: tuple[FavoritesNavigationNode, ...],
) -> tuple[FavoritesNavigationNode, ...]:
    nodes: list[FavoritesNavigationNode] = []

    def append(node: FavoritesNavigationNode) -> None:
        nodes.append(node)
        for child in node.children:
            append(child)

    for root in roots:
        append(root)
    return tuple(nodes)


def _snapshot_digest(snapshot: FavoritesStorageSnapshot) -> bytes:
    digest = hashlib.sha256()
    digest.update(len(snapshot.catalog_bytes).to_bytes(8, "big"))
    digest.update(snapshot.catalog_bytes)
    for document in snapshot.documents:
        filename = document.filename.encode("utf-8")
        digest.update(len(filename).to_bytes(8, "big"))
        digest.update(filename)
        digest.update(len(document.content).to_bytes(8, "big"))
        digest.update(document.content)
    return digest.digest()


class FavoritesEditorSession:
    """Own immutable snapshots and mediate every supported editor action."""

    def __init__(
        self,
        storage: FavoritesEditorStorage,
        baseline_snapshot: FavoritesStorageSnapshot,
    ) -> None:
        if not isinstance(baseline_snapshot, FavoritesStorageSnapshot):
            raise TypeError("Favorites editor baseline must be FavoritesStorageSnapshot.")
        self._storage = storage
        self._baseline_snapshot = baseline_snapshot
        self._intended_snapshot = baseline_snapshot
        self._history: tuple[FavoritesStorageSnapshot, ...] = ()

    @classmethod
    def open(cls, storage: FavoritesEditorStorage) -> FavoritesEditorSession:
        """Read the explicit source once and establish the immutable baseline."""

        return cls(storage, storage.read_snapshot())

    @property
    def storage(self) -> FavoritesEditorStorage:
        return self._storage

    @property
    def baseline_snapshot(self) -> FavoritesStorageSnapshot:
        return self._baseline_snapshot

    @property
    def intended_snapshot(self) -> FavoritesStorageSnapshot:
        return self._intended_snapshot

    @property
    def workspace(self) -> FavoritesWorkspace:
        return project_favorites_storage_snapshot(self._intended_snapshot)

    @property
    def navigation(self) -> FavoritesNavigation:
        return project_favorites_navigation(self.workspace)

    @property
    def validation(self) -> FavoritesSchemaValidation:
        return validate_favorites_workspace(self.workspace)

    @property
    def plan(self) -> FavoritesWritePlan:
        return plan_favorites_write(self._baseline_snapshot, self._intended_snapshot)

    @property
    def has_changes(self) -> bool:
        return self._baseline_snapshot != self._intended_snapshot

    @property
    def undo_count(self) -> int:
        return len(self._history)

    def nodes(self, text: str | None = None) -> tuple[FavoritesNavigationNode, ...]:
        """Return navigation preorder, optionally filtered by name text."""

        if text is None:
            return _walk_nodes(self.navigation.roots)
        return query_favorites_navigation(
            self.navigation,
            FavoritesNavigationQuery(text=text),
        )

    def node(self, path: FavoritesNavigationPath) -> FavoritesNavigationNode:
        """Resolve a path against the current intended snapshot."""

        for node in self.nodes():
            if node.path == path:
                return node
        raise FavoritesEditorError(
            f"Favorites navigation path is not present: {path.indexes!r}."
        )

    def target(self, path: FavoritesNavigationPath) -> FavoritesRecordTarget:
        """Resolve fresh exact record provenance for a current navigation node."""

        node = self.node(path)
        workspace = self.workspace
        if len(path.indexes) == 1:
            return select_favorites_record_target(
                self._intended_snapshot,
                node.source_index,
            )

        root_source_index = path.indexes[0]
        binding = next(
            (
                candidate
                for candidate in workspace.bindings
                if candidate.entry.source_index == root_source_index
            ),
            None,
        )
        if binding is None:
            raise FavoritesEditorError("Favorites navigation root is no longer bound.")
        document_index = next(
            (
                index
                for index, document in enumerate(workspace.documents)
                if document is binding.document
            ),
            None,
        )
        if document_index is None:
            raise FavoritesEditorError("Favorites bound document provenance was lost.")
        return select_favorites_record_target(
            self._intended_snapshot,
            node.source_index,
            document_index=document_index,
        )

    def raw_record(self, path: FavoritesNavigationPath) -> bytes:
        """Return the exact preserved record bytes for detail display."""

        target = self.target(path)
        return target.record.raw_bytes

    def records(self) -> tuple[FavoritesEditorRecordReference, ...]:
        """Return every exact source record in catalog/document order."""

        workspace = self.workspace
        records = [
            FavoritesEditorRecordReference(
                source_kind=FavoritesRecordSourceKind.CATALOG,
                source_index=source_index,
                document_index=None,
                filename="f_list.cfg",
                record=record,
            )
            for source_index, record in enumerate(workspace.catalog.source.records)
        ]
        for document_index, document in enumerate(workspace.documents):
            records.extend(
                FavoritesEditorRecordReference(
                    source_kind=FavoritesRecordSourceKind.HPD,
                    source_index=source_index,
                    document_index=document_index,
                    filename=document.filename,
                    record=record,
                )
                for source_index, record in enumerate(document.hierarchy.source.records)
            )
        return tuple(records)

    def record_target(
        self,
        reference: FavoritesEditorRecordReference,
    ) -> FavoritesRecordTarget:
        """Resolve a fresh raw-record reference against the intended snapshot."""

        if not isinstance(reference, FavoritesEditorRecordReference):
            raise TypeError(
                "Favorites editor raw selection requires FavoritesEditorRecordReference."
            )
        document_index = reference.document_index
        target = select_favorites_record_target(
            self._intended_snapshot,
            reference.source_index,
            document_index=document_index,
        )
        if target.filename != (None if document_index is None else reference.filename):
            raise FavoritesEditorError("Favorites editor raw record filename is stale.")
        if target.record != reference.record:
            raise FavoritesEditorError("Favorites editor raw record selection is stale.")
        return target

    def raw_source_record(self, reference: FavoritesEditorRecordReference) -> bytes:
        """Return exact bytes for one current raw source-record reference."""

        if not isinstance(reference, FavoritesEditorRecordReference):
            raise TypeError(
                "Favorites editor raw selection requires FavoritesEditorRecordReference."
            )
        workspace = self.workspace
        if reference.document_index is None:
            source = workspace.catalog.source
            if reference.filename != "f_list.cfg":
                raise FavoritesEditorError("Favorites editor raw record filename is stale.")
        else:
            if reference.document_index >= len(workspace.documents):
                raise FavoritesEditorError("Favorites editor raw record document is stale.")
            document = workspace.documents[reference.document_index]
            if document.filename != reference.filename:
                raise FavoritesEditorError("Favorites editor raw record filename is stale.")
            source = document.hierarchy.source
        if reference.source_index >= len(source.records):
            raise FavoritesEditorError("Favorites editor raw record position is stale.")
        record = source.records[reference.source_index]
        if record != reference.record:
            raise FavoritesEditorError("Favorites editor raw record selection is stale.")
        return record.raw_bytes

    def _replace_intended(self, snapshot: FavoritesStorageSnapshot) -> None:
        self._history = (*self._history, self._intended_snapshot)
        self._intended_snapshot = snapshot

    def rename(self, path: FavoritesNavigationPath, name: str) -> None:
        """Replace one supported evidence-backed Name Tag."""

        self._replace_intended(
            rename_favorites_record(self._intended_snapshot, self.target(path), name)
        )

    def rename_record(self, reference: FavoritesEditorRecordReference, name: str) -> None:
        """Replace a supported Name Tag selected from exact raw source records."""

        self._replace_intended(
            rename_favorites_record(
                self._intended_snapshot,
                self.record_target(reference),
                name,
            )
        )

    def delete(self, path: FavoritesNavigationPath) -> None:
        """Delete one supported HPD leaf."""

        self._replace_intended(
            delete_favorites_record(self._intended_snapshot, self.target(path))
        )

    def delete_record(self, reference: FavoritesEditorRecordReference) -> None:
        """Delete a supported HPD leaf selected by exact raw provenance."""

        self._replace_intended(
            delete_favorites_record(self._intended_snapshot, self.record_target(reference))
        )

    def duplicate(self, path: FavoritesNavigationPath, *, name: str | None = None) -> None:
        """Create one leaf after itself from its exact current record template."""

        target = self.target(path)
        self._replace_intended(
            create_favorites_record_after(
                self._intended_snapshot,
                target,
                target.record,
                name=name,
            )
        )

    def duplicate_record(
        self,
        reference: FavoritesEditorRecordReference,
        *,
        name: str | None = None,
    ) -> None:
        """Create a leaf from one exact raw source record used as anchor/template."""

        target = self.record_target(reference)
        self._replace_intended(
            create_favorites_record_after(
                self._intended_snapshot,
                target,
                target.record,
                name=name,
            )
        )

    def undo(self) -> bool:
        """Restore the preceding in-memory intended snapshot."""

        if not self._history:
            return False
        self._intended_snapshot = self._history[-1]
        self._history = self._history[:-1]
        return True

    def reset(self) -> bool:
        """Discard all in-memory edits and restore the baseline."""

        changed = self.has_changes
        self._intended_snapshot = self._baseline_snapshot
        self._history = ()
        return changed

    discard = reset

    def _confirmation_token(self) -> str:
        digest = hashlib.sha256()
        digest.update(self._storage.kind.value.encode("ascii"))
        digest.update(str(self._storage.requested_path).encode("utf-8"))
        digest.update(_snapshot_digest(self._baseline_snapshot))
        digest.update(_snapshot_digest(self._intended_snapshot))
        return digest.hexdigest()

    def review(self) -> FavoritesEditorReview:
        """Return the exact current plan and its separate confirmation token."""

        plan = self.plan
        if plan.is_noop:
            raise FavoritesEditorError("Favorites editor has no changes to review.")
        return FavoritesEditorReview(
            plan=plan,
            confirmation_token=self._confirmation_token(),
        )

    def execute(self, confirmation_token: str) -> FavoritesEditorExecution:
        """Execute only the unchanged reviewed plan, then verify a fresh reload."""

        if not isinstance(confirmation_token, str):
            raise TypeError("Favorites editor confirmation token must be a string.")
        review = self.review()
        if confirmation_token != review.confirmation_token:
            raise FavoritesEditorError(
                "Favorites editor confirmation is stale or does not match the exact plan."
            )
        if review.plan.is_blocked:
            blockers = ", ".join(blocker.value for blocker in review.plan.blockers)
            raise FavoritesEditorError(f"Favorites write plan is blocked: {blockers}.")

        result = self._storage.execute(review.plan)
        try:
            reloaded = self._storage.read_snapshot()
        except (OSError, RuntimeError, ValueError) as error:
            raise FavoritesEditorReloadError(
                result,
                detail=f"failed: {error}",
            ) from error
        if reloaded != review.plan.intended_snapshot:
            raise FavoritesEditorReloadError(result)
        self._baseline_snapshot = reloaded
        self._intended_snapshot = reloaded
        self._history = ()
        return FavoritesEditorExecution(result=result, reloaded_snapshot=reloaded)


def open_favorites_copied_tree_editor(path: Path) -> FavoritesEditorSession:
    """Open one explicit copied Favorites directory for local editing."""

    return FavoritesEditorSession.open(FavoritesCopiedTreeEditorStorage(path))


def open_favorites_usb_editor(
    path: Path,
    *,
    host_state_directory: Path,
    mountinfo_path: Path = DEFAULT_LINUX_MOUNTINFO_PATH,
    sys_dev_block_directory: Path = DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY,
) -> FavoritesEditorSession:
    """Open one explicit freshly qualified Linux USB target for local editing."""

    return FavoritesEditorSession.open(
        FavoritesUsbEditorStorage(
            requested_path=path,
            host_state_directory=host_state_directory,
            mountinfo_path=mountinfo_path,
            sys_dev_block_directory=sys_dev_block_directory,
        )
    )


__all__ = [
    "FavoritesCopiedTreeEditorStorage",
    "FavoritesEditorError",
    "FavoritesEditorExecution",
    "FavoritesEditorRecordReference",
    "FavoritesEditorReloadError",
    "FavoritesEditorReview",
    "FavoritesEditorSession",
    "FavoritesEditorSourceKind",
    "FavoritesEditorStorage",
    "FavoritesEditorWriteResult",
    "FavoritesUsbEditorStorage",
    "open_favorites_copied_tree_editor",
    "open_favorites_usb_editor",
]
