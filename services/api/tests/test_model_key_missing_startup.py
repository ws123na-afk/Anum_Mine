"""A hosted provider with a blank key no longer stops the API at import: it starts with the
placeholder model, logs an error and the model status says so (docs/model-gateway.md#placeholder-answers)."""

from __future__ import annotations

import logging

import pytest

from anum_api import model_gateway, model_status
from anum_api.anthropic_gateway import AnthropicGateway
from anum_api.model_gateway import (
    MockModelGateway,
    build_default_model_gateway,
    build_model_gateway,
)
from anum_api.schemas import TenantContext
from anum_api.settings import Settings, settings


@pytest.fixture
def gateway_logs(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> pytest.LogCaptureFixture:
    monkeypatch.setattr(logging.getLogger("anum.model_gateway"), "disabled", False)
    caplog.set_level(logging.DEBUG, logger="anum.model_gateway")
    return caplog


@pytest.mark.parametrize("provider", ["anthropic", "openai-compatible", "openai_compatible"])
@pytest.mark.parametrize("key", [None, "", "   "])
def test_a_blank_key_starts_with_the_placeholder_model_and_logs_an_error(
    provider: str, key: str | None, gateway_logs: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_gateway, "_key_missing_logged", set())
    config = Settings(environment="test", model_provider=provider, model_api_key=key)
    gateway = build_default_model_gateway(config)
    assert isinstance(gateway, MockModelGateway)
    assert isinstance(build_default_model_gateway(config), MockModelGateway)  # logged once, not per call
    errors = [r for r in gateway_logs.records if r.levelno == logging.ERROR and r.getMessage().startswith("model_api_key_missing")]
    assert len(errors) == 1
    assert "ANUM_MODEL_API_KEY" in errors[0].getMessage()


def test_with_a_key_the_real_gateway_is_built() -> None:
    gateway = build_default_model_gateway(Settings(environment="test", model_provider="anthropic", model_api_key="example"))
    assert isinstance(gateway, AnthropicGateway)


def test_an_explicit_gateway_without_its_key_is_still_refused() -> None:
    # Workspace configurations and direct construction keep failing closed.
    with pytest.raises(ValueError, match="API key is required"):
        build_model_gateway("anthropic")
    with pytest.raises(ValueError, match="API key is required"):
        AnthropicGateway(api_key=" ")


def test_the_model_status_reports_placeholder_answers_while_the_server_key_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "model_provider", "anthropic")
    monkeypatch.setattr(settings, "model_api_key", "")
    monkeypatch.setattr(model_gateway, "_server_key_missing", False)
    assert isinstance(build_default_model_gateway(), MockModelGateway)
    assert model_gateway.server_key_missing() is True
    context = TenantContext(tenant_id="tenant_keyless", workspace_id="workspace_keyless", user_id="u", roles=["owner"])
    status = model_status.workspace_model_status(context)
    assert (status.placeholder, status.provider, status.source) == (True, "anthropic", "server")
    assert model_status.server_model_state()["key_missing"] is True

    monkeypatch.setattr(settings, "model_api_key", "example")
    assert isinstance(build_default_model_gateway(), AnthropicGateway)
    assert model_gateway.server_key_missing() is False
    assert model_status.workspace_model_status(context).placeholder is False


def test_another_settings_object_never_changes_the_server_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_gateway, "_server_key_missing", False)
    build_default_model_gateway(Settings(environment="test", model_provider="anthropic", model_api_key=""))
    assert model_gateway.server_key_missing() is False
