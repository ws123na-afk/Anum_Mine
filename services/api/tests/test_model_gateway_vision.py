"""Camera frames for vision-capable models (docs/voice.md#call-mode): which gateways
can see, and how a frame is attached to one streamed request and nothing else."""

from __future__ import annotations

import base64

from anum_api.anthropic_gateway import ANTHROPIC_FAST_MODEL, AnthropicGateway
from anum_api.model_budget import BudgetedModelGateway
from anum_api.model_gateway import (
    MockModelGateway,
    ModelRoute,
    OpenAICompatibleGateway,
    VisionImage,
    build_default_model_gateway,
    build_model_gateway,
    vision_gateway,
    vision_model_name,
)
from anum_api.schemas import TenantContext
from anum_api.settings import Settings

FRAME = VisionImage(data=b"\xff\xd8\xffjpeg", media_type="image/jpeg")
MESSAGES = [
    {"role": "system", "content": "rules"},
    {"role": "user", "content": "earlier"},
    {"role": "assistant", "content": "reply"},
    {"role": "user", "content": "What is this?"},
]


def test_openai_compatible_and_ollama_see_only_with_a_named_vision_model() -> None:
    plain = build_model_gateway("openai-compatible", api_key="example", model="gpt-4.1-mini")
    assert vision_model_name(plain) == ""
    assert vision_gateway(plain, FRAME) is None
    assert vision_gateway(build_model_gateway("ollama", model="llama3.2"), FRAME) is None

    seeing = build_model_gateway("ollama", model="llama3.2", vision_model="llava")
    variant = vision_gateway(seeing, FRAME)
    assert isinstance(variant, OpenAICompatibleGateway)
    assert variant.model == "llava"
    # The original gateway is untouched: its next text call carries no frame.
    assert seeing.model == "llama3.2"
    _, body = seeing._stream_request(MESSAGES, 100)
    assert body["messages"] == MESSAGES


def test_the_frame_rides_on_the_last_user_message_as_an_image_url_part() -> None:
    gateway = build_model_gateway("openai-compatible", api_key="example", model="gpt-4.1-mini", vision_model="gpt-4.1-mini")
    variant = vision_gateway(gateway, FRAME)
    assert variant is not None

    _, body = variant._stream_request(MESSAGES, 400)

    messages = body["messages"]
    assert messages[:3] == MESSAGES[:3]
    assert messages[3]["content"] == [
        {"type": "text", "text": "What is this?"},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(FRAME.data).decode()}"}},
    ]
    assert MESSAGES[3]["content"] == "What is this?"


def test_anthropic_sees_with_its_fast_model_and_puts_the_image_first_without_caching_it() -> None:
    gateway = build_model_gateway(
        "anthropic",
        api_key="example",
        model="claude-sonnet-5-5",
        route_models={ModelRoute.FAST: ANTHROPIC_FAST_MODEL, ModelRoute.CAPABLE: "claude-sonnet-5-5"},
    )
    assert vision_model_name(gateway) == ANTHROPIC_FAST_MODEL
    variant = vision_gateway(gateway, FRAME)
    assert isinstance(variant, AnthropicGateway)

    _, body = variant._stream_request(MESSAGES, 400)

    assert body["model"] == ANTHROPIC_FAST_MODEL
    last = body["messages"][-1]
    assert last["content"][0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(FRAME.data).decode()},
    }
    assert last["content"][1] == {"type": "text", "text": "What is this?"}
    assert all("cache_control" not in block for block in last["content"])


def test_server_default_reads_the_vision_setting_and_mock_never_sees() -> None:
    config = Settings(model_provider="openai-compatible", model_api_key="example", model_vision_name="gpt-4.1-mini")
    assert vision_model_name(build_default_model_gateway(config)) == "gpt-4.1-mini"
    assert vision_model_name(MockModelGateway()) == ""
    assert vision_gateway(MockModelGateway(), FRAME) is None


def test_budgeted_gateways_keep_the_budget_on_the_vision_variant() -> None:
    context = TenantContext(tenant_id="t", workspace_id="w", user_id="u", roles=["member"])
    inner = build_model_gateway("ollama", model="llama3.2", vision_model="llava")
    budgeted = BudgetedModelGateway(inner, context)

    variant = vision_gateway(budgeted, FRAME)

    assert isinstance(variant, BudgetedModelGateway)
    assert variant.inner.model == "llava"
    assert vision_model_name(budgeted) == "llava"
    assert vision_gateway(BudgetedModelGateway(MockModelGateway(), context), FRAME) is None
