"""Explicit authenticated HTTP-to-daemon bridge for cached supplemental reads."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .exceptions import DaemonRequestError, SDS200Error
from .scanner_display_supplemental_transport import (
    decode_context_response,
    decode_demand_response,
    validate_bundle,
    validate_renewal_id,
)
from .scanner_display_supplemental_wire import decode_supplemental_context

CONTEXT_PATH = "/api/v1/display-supplemental/context"
FRAME_PATH = "/api/v1/display-supplemental/frame"
DEMAND_PATH = "/api/v1/display-supplemental/demand"
RENEWAL_HEADER = "x-sdsctl-supplemental-renewal"
VERSION_HEADER = "x-sdsctl-supplemental-version"
CONTEXT_HEADER = "x-sdsctl-supplemental-context"
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
OPERATIONS = {"display.supplemental.context", "display.supplemental.frame"}
DEMAND_OPERATION = "display.supplemental.demand"


class _Client(Protocol):
    def hello(self) -> Mapping[str, object]: ...
    def display_supplemental_context(self) -> Mapping[str, object]: ...
    def display_supplemental_frame(self, context: object) -> Mapping[str, object]: ...
    def display_supplemental_demand(
        self, context: object, renewal_id: str
    ) -> Mapping[str, object]: ...


class _ClientContext(Protocol):
    def __enter__(self) -> _Client: ...
    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None: ...


def _error(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail, headers=dict(HEADERS))


def attach_supplemental_routes(
    app: FastAPI, factory: Callable[[], _ClientContext], *, demand: bool = False
) -> None:
    """Caller MUST have installed native-auth or trusted HA Ingress middleware.

    Only create_web_dashboard_app's explicit authenticated option calls this.
    There is no CLI/config activation, upload, scanner target or arming API.
    """

    def query(request: Request, *, frame: bool, renew: bool = False) -> JSONResponse:
        if request.query_params or request.headers.getlist(VERSION_HEADER) != ["1"]:
            raise _error(422, "Explicit supplemental protocol version 1 is required.")
        supplied = request.headers.getlist(CONTEXT_HEADER)
        nonces = request.headers.getlist(RENEWAL_HEADER)
        nonce = None
        if renew:
            try:
                if len(nonces) != 1:
                    raise ValueError
                nonce = validate_renewal_id(nonces[0])
            except ValueError:
                raise _error(
                    422, "One valid supplemental renewal identifier is required."
                ) from None
        elif nonces:
            raise _error(422, "Cached reads do not accept a renewal identifier.")
        context = None
        if frame:
            try:
                if len(supplied) != 1 or len(supplied[0]) > 1024:
                    raise ValueError

                # Forbid duplicate JSON keys, including alternate spellings.
                def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
                    result = dict(pairs)
                    if len(result) != len(pairs):
                        raise ValueError
                    return result

                context = decode_supplemental_context(
                    json.loads(supplied[0], object_pairs_hook=unique)
                )
            except (ValueError, RecursionError):
                raise _error(
                    422, "One valid negotiated supplemental context is required."
                ) from None
        elif supplied:
            raise _error(422, "Context negotiation does not accept a context header.")
        try:
            with factory() as client:
                operations = client.hello().get("operations")
                if (
                    type(operations) is not list
                    or not all(type(op) is str for op in operations)
                    or not OPERATIONS.issubset(operations)
                    or renew
                    and DEMAND_OPERATION not in operations
                ):
                    raise _error(503, "Supplemental display delivery is unavailable.")
                if renew:
                    assert context is not None and nonce is not None
                    # Any failure after dispatch may follow an accepted renewal.
                    try:
                        result = dict(client.display_supplemental_demand(asdict(context), nonce))
                        decode_demand_response(result, context, nonce)
                    except DaemonRequestError as error:
                        if error.code in {
                            "supplemental_context_changed",
                            "authorization_denied",
                            "unsupported_operation",
                            "invalid_parameters",
                        }:
                            raise
                        raise _error(
                            503,
                            "Supplemental demand was not confirmed. Stop renewal; "
                            "reads may have occurred.",
                        ) from None
                    except (SDS200Error, OSError, ValueError, TypeError):
                        raise _error(
                            503,
                            "Supplemental demand was not confirmed. Stop renewal; "
                            "reads may have occurred.",
                        ) from None
                elif context is None:
                    result = dict(client.display_supplemental_context())
                    decode_context_response(result)
                else:
                    result = validate_bundle(
                        dict(client.display_supplemental_frame(asdict(context))), context
                    )
                return JSONResponse(result, headers=dict(HEADERS))
        except DaemonRequestError as error:
            if error.code == "supplemental_context_changed":
                raise _error(409, "Supplemental context changed. Negotiate again.") from None
            raise _error(503, "Supplemental display delivery is unavailable.") from None
        except (SDS200Error, OSError, ValueError, TypeError):
            raise _error(503, "Supplemental display delivery is unavailable.") from None

    @app.get(CONTEXT_PATH, include_in_schema=False)
    def supplemental_context(request: Request) -> JSONResponse:
        return query(request, frame=False)

    @app.get(FRAME_PATH, include_in_schema=False)
    def supplemental_frame(request: Request) -> JSONResponse:
        return query(request, frame=True)

    if demand:

        @app.post(DEMAND_PATH, include_in_schema=False)
        async def supplemental_demand(request: Request) -> JSONResponse:
            # Headers carry the fixed-size protocol. Reject all body content
            # before dispatch; never buffer an unbounded or stalled body.
            try:
                async with asyncio.timeout(1):
                    async for chunk in request.stream():
                        if chunk:
                            raise ValueError
            except (ValueError, TimeoutError, ClientDisconnect):
                raise _error(422, "Supplemental demand requires an empty body.") from None
            return await run_in_threadpool(query, request, frame=True, renew=True)
