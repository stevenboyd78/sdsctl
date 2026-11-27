"""Authenticated, read-only HTTP presentation for Favorites profile summaries."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .favorites_profile_store import (
    FavoritesProfileNotFoundError,
    FavoritesProfileStore,
    FavoritesProfileStoreError,
    FavoritesProfileSummary,
)

FAVORITES_PROFILE_COLLECTION_PATH = "/api/v1/favorites/profiles"
FAVORITES_PROFILE_PROTOCOL = "sdsctl.favorites-profiles"
FAVORITES_PROFILE_PROTOCOL_VERSION = 1

_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=detail,
        headers=dict(_HEADERS),
    )


def _summary_payload(summary: FavoritesProfileSummary) -> dict[str, object]:
    """Serialize only the store's explicitly path-free summary contract."""

    return {
        "profile_id": summary.profile_id,
        "name": summary.name,
        "compatibility": summary.compatibility.value,
        "revision": summary.revision,
        "revision_count": summary.revision_count,
        "document_count": summary.document_count,
        "snapshot_bytes": summary.snapshot_bytes,
        "snapshot_sha256": summary.snapshot_sha256,
        "created_at_ns": summary.created_at_ns,
        "updated_at_ns": summary.updated_at_ns,
    }


def _response(content: dict[str, object]) -> JSONResponse:
    return JSONResponse(content=content, headers=dict(_HEADERS))


def _require_no_query(request: Request) -> None:
    if request.query_params:
        raise _error(422, "Favorites profile summary reads do not accept query parameters.")


def attach_favorites_profile_read_routes(
    app: FastAPI,
    store: FavoritesProfileStore,
) -> None:
    """Attach operator-only summary reads after authenticated admission is configured.

    The caller must install native operator authentication or trusted Home Assistant
    Ingress admission. This adapter has no create, save, delete, import, export, path,
    workspace, target-selection, scanner, USB, FTP, or write-execution route.
    """

    @app.get(FAVORITES_PROFILE_COLLECTION_PATH, include_in_schema=False)
    async def list_profiles(request: Request) -> JSONResponse:
        _require_no_query(request)
        try:
            summaries = await run_in_threadpool(store.list_profiles)
        except FavoritesProfileStoreError:
            raise _error(503, "Favorites profile store is unavailable.") from None
        return _response(
            {
                "protocol": FAVORITES_PROFILE_PROTOCOL,
                "version": FAVORITES_PROFILE_PROTOCOL_VERSION,
                "profiles": [_summary_payload(summary) for summary in summaries],
            }
        )

    @app.get(f"{FAVORITES_PROFILE_COLLECTION_PATH}/{{profile_id}}", include_in_schema=False)
    async def get_profile_summary(profile_id: str, request: Request) -> JSONResponse:
        _require_no_query(request)
        try:
            summary = await run_in_threadpool(store.get_profile_summary, profile_id)
        except FavoritesProfileNotFoundError:
            raise _error(404, "Favorites profile is unavailable.") from None
        except FavoritesProfileStoreError:
            raise _error(503, "Favorites profile store is unavailable.") from None
        return _response(
            {
                "protocol": FAVORITES_PROFILE_PROTOCOL,
                "version": FAVORITES_PROFILE_PROTOCOL_VERSION,
                "profile": _summary_payload(summary),
            }
        )


__all__ = [
    "FAVORITES_PROFILE_COLLECTION_PATH",
    "FAVORITES_PROFILE_PROTOCOL",
    "FAVORITES_PROFILE_PROTOCOL_VERSION",
    "attach_favorites_profile_read_routes",
]
