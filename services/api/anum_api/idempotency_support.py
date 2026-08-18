from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from .dependencies import idempotency_repository
from .idempotency import (
    BeginOutcome,
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyScope,
    StoredResponse,
    canonical_request_fingerprint,
)
from .schemas import TenantContext


def _to_response(status_code: int, body: Any) -> Response:
    if body is None:
        return Response(status_code=status_code)
    return JSONResponse(status_code=status_code, content=body)


async def run_idempotently(
    *,
    context: TenantContext,
    action: str,
    key: str | None,
    payload: Any,
    execute: Callable[[], Awaitable[tuple[int, Any]]],
) -> Response:
    """Run a mutating action at most once per idempotency key.

    A retried request that reuses the same key and request body replays the
    stored response instead of re-executing `execute`. Requests without an
    Idempotency-Key header run normally with no dedup guarantee.
    """

    if key is None:
        status_code, body = await execute()
        return _to_response(status_code, jsonable_encoder(body) if body is not None else None)

    scope = IdempotencyScope(context.tenant_id, context.workspace_id, action)
    fingerprint = canonical_request_fingerprint(action, jsonable_encoder(payload))

    try:
        begin = idempotency_repository.begin(scope, key, fingerprint)
    except (IdempotencyConflict, IdempotencyInProgress) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if begin.outcome is BeginOutcome.REPLAYED:
        response = begin.response
        assert response is not None
        return _to_response(response.status_code, response.body)

    if begin.outcome is BeginOutcome.PREVIOUSLY_FAILED:
        try:
            begin = idempotency_repository.begin(scope, key, fingerprint, retry_failed=True)
        except IdempotencyInProgress as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    try:
        status_code, body = await execute()
    except Exception as exc:
        idempotency_repository.fail(scope, key, fingerprint, str(exc)[:500] or "request failed")
        raise

    encoded_body = jsonable_encoder(body) if body is not None else None
    idempotency_repository.complete(
        scope, key, fingerprint, StoredResponse(status_code, encoded_body, {})
    )
    return _to_response(status_code, encoded_body)
