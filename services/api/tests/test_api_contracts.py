from fastapi import FastAPI, Request
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
