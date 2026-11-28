"""Read-only process-local previews for durable Favorites profiles.

This module deliberately owns no HTTP routes and exposes no mutation or write
executor.  A caller supplies an already-authorized server-side owner secret,
one opaque profile identifier, and the exact current profile revision it
reviewed.  The resulting bounded workspace exposes only path-free summaries
and navigation nodes.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .favorites_profile_store import (
    FavoritesProfileConflictError,
    FavoritesProfileStore,
)
from .favorites_web_workspace import (
    FavoritesWebNode,
    FavoritesWebWorkspaceLimits,
    FavoritesWebWorkspaces,
    FavoritesWebWorkspaceSummary,
)

_Clock = Callable[[], float]
_TokenFactory = Callable[[], str]


class FavoritesProfileWorkspacePreviews:
    """Own read-only profile previews for authorized server-side callers."""

    def __init__(
        self,
        store: FavoritesProfileStore,
        *,
        limits: FavoritesWebWorkspaceLimits | None = None,
        clock: _Clock | None = None,
        token_factory: _TokenFactory | None = None,
        node_token_factory: _TokenFactory | None = None,
    ) -> None:
        if not isinstance(store, FavoritesProfileStore):
            raise TypeError("Favorites profile workspace store is invalid.")
        self._store = store
        self._workspaces = FavoritesWebWorkspaces(
            (),
            allow_read_only_snapshots=True,
            limits=limits,
            clock=time.monotonic if clock is None else clock,
            token_factory=token_factory,
            node_token_factory=node_token_factory,
        )

    def open(
        self,
        owner_secret: bytes,
        profile_id: str,
        *,
        expected_profile_revision: int,
    ) -> FavoritesWebWorkspaceSummary:
        """Open only the exact current immutable profile revision."""

        if type(expected_profile_revision) is not int or expected_profile_revision <= 0:
            raise ValueError("Favorites profile preview revision must be positive.")
        profile = self._store.get_profile(profile_id)
        actual = profile.summary.revision
        if actual != expected_profile_revision:
            raise FavoritesProfileConflictError(
                expected=expected_profile_revision,
                actual=actual,
            )
        return self._workspaces.create_read_only_snapshot(
            owner_secret,
            profile.summary.profile_id,
            source_revision=actual,
            snapshot=profile.snapshot,
        )

    def summary(
        self,
        owner_secret: bytes,
        workspace_id: str,
        *,
        expected_revision: int | None = None,
    ) -> FavoritesWebWorkspaceSummary:
        """Return one authorized path-free preview summary."""

        return self._workspaces.summary(
            owner_secret,
            workspace_id,
            expected_revision=expected_revision,
        )

    def nodes(
        self,
        owner_secret: bytes,
        workspace_id: str,
        *,
        expected_revision: int,
        text: str | None = None,
    ) -> tuple[FavoritesWebNode, ...]:
        """Return path-free navigation with every mutation flag disabled."""

        return self._workspaces.nodes(
            owner_secret,
            workspace_id,
            expected_revision=expected_revision,
            text=text,
        )

    def purge_expired(self) -> int:
        """Discard expired previews."""

        return self._workspaces.purge_expired()

    def discard(self, owner_secret: bytes, workspace_id: str) -> None:
        """Discard one authorized preview."""

        self._workspaces.discard(owner_secret, workspace_id)

    def discard_owner(self, owner_secret: bytes) -> int:
        """Discard every preview for one revoked or logged-out owner."""

        return self._workspaces.discard_owner(owner_secret)

    def close(self) -> None:
        """Terminally discard previews without closing the shared profile store."""

        self._workspaces.close()


__all__ = ["FavoritesProfileWorkspacePreviews"]
