import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from anum_api.errors import ApplicationError, ErrorCode, register_exception_handlers
from anum_api.request_context import (
    CORRELATION_ID_HEADER,
    CorrelationIdMiddleware,
    get_correlation_id,
    is_valid_correlation_id,
)


class ExamplePayload(BaseModel):
    name: str = Field(min_length=3)


def build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)

    @app.get("/context")
    async def context(request: Request) -> dict[str, str]:
        return {
            "from_context": get_correlation_id(),
            "from_request": get_correlation_id(request),
        }

    @app.post("/validate")
    async def validate(payload: ExamplePayload) -> ExamplePayload:
        return payload

    @app.get("/known-error")
    async def known_error() -> None:
        raise ApplicationError(
            ErrorCode.CONFLICT,
            "The operation is already complete",
            status_code=409,
        )

    @app.get("/unexpected-error")
    async def unexpected_error() -> None:
        raise RuntimeError("database password: secret-value")

    return app


client = TestClient(build_app(), raise_server_exceptions=False)


def test_validation_uses_stable_error_envelope() -> None:
    response = client.post("/validate", json={"name": "x"})

    assert response.status_code == 422
    payload = response.json()["error"]
    assert payload["code"] == "validation_error"
    assert payload["message"] == "Request validation failed"
    assert payload["details"] == [
        {
            "location": "body.name",
            "message": "String should have at least 3 characters",
            "type": "string_too_short",
        }
    ]
    assert response.headers[CORRELATION_ID_HEADER] == payload["correlation_id"]


def test_known_application_error_preserves_public_message_and_code() -> None:
    response = client.get("/known-error", headers={CORRELATION_ID_HEADER: "request-123"})

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "conflict",
            "message": "The operation is already complete",
            "correlation_id": "request-123",
            "details": [],
        }
    }
    assert response.headers[CORRELATION_ID_HEADER] == "request-123"


def test_unexpected_error_does_not_leak_internal_details() -> None:
    response = client.get("/unexpected-error")

    assert response.status_code == 500
    payload = response.json()["error"]
    assert payload["code"] == "internal_error"
    assert payload["message"] == "An unexpected error occurred"
    assert "secret-value" not in response.text
    assert response.headers[CORRELATION_ID_HEADER] == payload["correlation_id"]


def test_valid_correlation_id_is_available_to_handler_and_response() -> None:
    response = client.get("/context", headers={CORRELATION_ID_HEADER: "client.trace-42"})

    assert response.status_code == 200
    assert response.json() == {
        "from_context": "client.trace-42",
        "from_request": "client.trace-42",
    }
    assert response.headers[CORRELATION_ID_HEADER] == "client.trace-42"


def test_missing_or_invalid_correlation_id_is_replaced() -> None:
    missing = client.get("/context")
    invalid = client.get("/context", headers={CORRELATION_ID_HEADER: "invalid value\n"})

    for response in (missing, invalid):
        generated = response.headers[CORRELATION_ID_HEADER]
        assert is_valid_correlation_id(generated)
        assert generated.startswith("corr_")
        assert response.json()["from_context"] == generated


def test_streaming_handler_sees_client_disconnect_through_correlation_middleware() -> None:
    """A long-lived stream (SSE) must notice its client left, or it runs forever.

    The server may drop writes to a closed connection silently (uvicorn 0.30 does), so
    the stream's only exit is `request.is_disconnected()`; the middleware must not hide it.
    """

    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)
    observed: dict[str, object] = {}

    @app.get("/stream")
    async def stream(request: Request) -> StreamingResponse:
        async def body():
            observed["correlation_id"] = get_correlation_id()
            yield ": open\n\n"
            while not await request.is_disconnected():
                await asyncio.sleep(0.01)
            observed["disconnected"] = True

        return StreamingResponse(body(), media_type="text/event-stream")

    async def scenario() -> list[dict]:
        sent: list[dict] = []
        requested = False

        async def receive() -> dict:
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/stream",
            "raw_path": b"/stream",
            "root_path": "",
            "query_string": b"",
            "headers": [(CORRELATION_ID_HEADER.lower().encode(), b"stream-1")],
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 8000),
        }
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
        return sent

    sent = asyncio.run(scenario())

    assert observed == {"correlation_id": "stream-1", "disconnected": True}
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert (CORRELATION_ID_HEADER.lower().encode(), b"stream-1") in start["headers"]
