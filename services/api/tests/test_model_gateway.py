import asyncio

import httpx
from pydantic import BaseModel

from anum_api.model_gateway import OpenAICompatibleGateway, build_model_gateway


def test_openai_compatible_gateway_normalizes_text_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer provider-secret"
        return httpx.Response(
            200,
            headers={"x-request-id": "provider-request-1"},
            json={
                "model": "provider-model-v1",
                "choices": [{"message": {"content": "Completed safely."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    gateway = OpenAICompatibleGateway(
        api_key="provider-secret",
        model="configured-model",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    response = asyncio.run(gateway.generate_text("Complete the task"))

    assert response.text == "Completed safely."
    assert response.usage.input_tokens == 7
    assert response.usage.output_tokens == 3
    assert response.usage.model == "provider-model-v1"
    assert response.metadata is not None
    assert response.metadata.request_id == "provider-request-1"


def test_openai_compatible_gateway_validates_structured_output() -> None:
    class Answer(BaseModel):
        result: str

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "structured-model",
                "choices": [{"message": {"content": '{"result":"ready"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )

    gateway = OpenAICompatibleGateway(
        api_key="provider-secret",
        model="structured-model",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    structured, _ = asyncio.run(gateway.generate_structured("Return JSON", Answer))

    assert structured == Answer(result="ready")


def test_gateway_factory_rejects_unknown_provider() -> None:
    try:
        build_model_gateway("unknown")
    except ValueError as exc:
        assert "Unsupported model provider" in str(exc)
    else:
        raise AssertionError("unknown provider should fail closed")
