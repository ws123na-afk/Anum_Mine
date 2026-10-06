"""Secrets encryption, settings validation and the in-memory model config store."""

import logging
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from anum_api import secret_box
from anum_api.model_config_store import InMemoryModelConfigStore, open_model_config_store
from anum_api.schemas import TenantContext
from anum_api.secret_box import (
    SecretCipher,
    SecretDecryptionError,
    reset_secret_cipher,
    secret_cipher,
)
from anum_api.settings import Settings, settings

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
CONTEXT_A = TenantContext(tenant_id="tenant_a", workspace_id="workspace_a", user_id="user_a", roles=["owner"])
CONTEXT_B = TenantContext(tenant_id="tenant_b", workspace_id="workspace_a", user_id="user_b", roles=["owner"])


@pytest.fixture(autouse=True)
def _fresh_cipher():
    reset_secret_cipher()
    yield
    reset_secret_cipher()


# --- encryption at rest -------------------------------------------------------


def test_cipher_round_trips_and_hides_plaintext() -> None:
    cipher = SecretCipher([Fernet.generate_key()])

    token = cipher.encrypt("sk-live-abcdef-1234")

    assert "sk-live" not in token and "1234" not in token
    assert cipher.decrypt(token) == "sk-live-abcdef-1234"


def test_cipher_supports_key_rotation() -> None:
    old_key, new_key = Fernet.generate_key(), Fernet.generate_key()
    token = SecretCipher([old_key]).encrypt("sk-rotate")

    rotated = SecretCipher([new_key, old_key])

    assert rotated.decrypt(token) == "sk-rotate"
    assert SecretCipher([new_key]).decrypt(rotated.encrypt("fresh")) == "fresh"


def test_wrong_key_fails_without_leaking_the_token() -> None:
    token = SecretCipher([Fernet.generate_key()]).encrypt("sk-other")

    with pytest.raises(SecretDecryptionError) as raised:
        SecretCipher([Fernet.generate_key()]).decrypt(token)

    assert token not in str(raised.value)


def test_cipher_uses_configured_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "secrets_key", SecretStr(f"{first}, {second}"))

    token = secret_cipher().encrypt("sk-configured")

    assert Fernet(first.encode()).decrypt(token.encode()) == b"sk-configured"


def test_local_without_key_derives_a_dev_key_and_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "secrets_key", None)
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(logging.getLogger("anum.secrets"), "disabled", False)
    caplog.set_level(logging.WARNING, logger="anum.secrets")

    token = secret_cipher().encrypt("sk-local")
    reset_secret_cipher()

    assert secret_cipher().decrypt(token) == "sk-local"  # stable across restarts
    assert any("ANUM_SECRETS_KEY is not set" in record.getMessage() for record in caplog.records)


def test_non_local_without_key_refuses_to_build_a_cipher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "secrets_key", None)
    monkeypatch.setattr(settings, "environment", "production")

    with pytest.raises(RuntimeError, match="ANUM_SECRETS_KEY is required"):
        secret_box.secret_cipher()


def test_settings_require_secrets_key_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANUM_MODEL_API_KEY", "sk-should-not-be-echoed")
    monkeypatch.delenv("ANUM_SECRETS_KEY", raising=False)

    with pytest.raises(ValidationError) as raised:
        Settings(environment="production")

    assert "ANUM_SECRETS_KEY is required" in str(raised.value)
    assert "sk-should-not-be-echoed" not in str(raised.value)
    assert Settings(environment="production", secrets_key=Fernet.generate_key().decode()).secrets_key
    assert Settings(environment="local").secrets_key is None


def test_settings_reject_malformed_keys_without_echoing_them() -> None:
    with pytest.raises(ValidationError) as raised:
        Settings(environment="production", secrets_key="not-a-real-fernet-key-value")

    assert "Fernet" in str(raised.value)
    assert "not-a-real-fernet-key-value" not in str(raised.value)


# --- in-memory store ----------------------------------------------------------


def test_memory_store_is_scoped_by_tenant_and_workspace() -> None:
    store = InMemoryModelConfigStore()
    store.save(CONTEXT_A, provider="openai_compatible", model="m", base_url="https://x/v1", api_key="sk-abcd-1234", updated_at=NOW)

    assert store.get(CONTEXT_B) is None
    saved = store.get(CONTEXT_A)
    assert saved is not None
    assert saved.api_key is not None and saved.api_key.get_secret_value() == "sk-abcd-1234"
    assert saved.credential_hint == "...1234"
    assert saved.credential_configured is True
    assert saved.updated_by_user_id == "user_a"


def test_memory_store_can_withhold_the_secret() -> None:
    store = InMemoryModelConfigStore()
    store.save(CONTEXT_A, provider="openai_compatible", model="m", base_url="https://x/v1", api_key="sk-abcd-1234", updated_at=NOW)

    summary = store.get(CONTEXT_A, include_secret=False)

    assert summary is not None
    assert summary.api_key is None
    assert summary.credential_configured is True
    assert summary.usable is True
    assert store.get(CONTEXT_A).api_key is not None  # the stored copy is untouched


def test_keyless_providers_are_usable_without_a_credential() -> None:
    store = InMemoryModelConfigStore()
    store.save(CONTEXT_A, provider="ollama", model="llama3.2", base_url="http://localhost:11434/v1", api_key=None, updated_at=NOW)

    config = store.get(CONTEXT_A)

    assert config is not None and config.usable and not config.credential_configured


def test_unsupported_backend_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "repository_backend", "sqlite")

    with pytest.raises(RuntimeError, match="Unsupported repository backend"):
        with open_model_config_store(CONTEXT_A):
            pass
