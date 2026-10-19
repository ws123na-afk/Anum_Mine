"""GET /api/v1/auth/sign-in-options and its settings (docs/identity.md#google-and-apple-sign-in)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from anum_api.main import app
from anum_api.settings import Settings, settings


def test_nothing_is_offered_by_default() -> None:
    response = TestClient(app).get("/api/v1/auth/sign-in-options")
    assert response.status_code == 200
    assert response.json() == {"identity_providers": [], "self_sign_up": False, "privacy_policy_url": None}
    assert response.headers["cache-control"] == "public, max-age=60"


def test_configured_providers_sign_up_and_privacy_policy_are_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sign_in_providers", "google,apple")
    monkeypatch.setattr(settings, "self_sign_up", True)
    monkeypatch.setattr(settings, "privacy_policy_url", "https://example.com/privacy")
    body = TestClient(app).get("/api/v1/auth/sign-in-options").json()
    assert body == {
        "identity_providers": [{"id": "google", "label": "Google"}, {"id": "apple", "label": "Apple"}],
        "self_sign_up": True,
        "privacy_policy_url": "https://example.com/privacy",
    }


def test_public_without_a_token_or_workspace_even_in_oidc_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "sign_in_providers", "apple")
    response = TestClient(app).get("/api/v1/auth/sign-in-options")
    assert response.status_code == 200
    assert [p["id"] for p in response.json()["identity_providers"]] == ["apple"]
    # It names no tenant, workspace or user.
    assert set(response.json()) == {"identity_providers", "self_sign_up", "privacy_policy_url"}


def test_route_is_in_the_schema() -> None:
    assert "get" in app.openapi()["paths"]["/api/v1/auth/sign-in-options"]


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("", ""), ("google", "google"), (" Apple , google ,apple", "apple,google"), (",", "")],
)
def test_sign_in_providers_are_normalized(raw: str, normalized: str) -> None:
    assert Settings(sign_in_providers=raw).sign_in_providers == normalized


@pytest.mark.parametrize("raw", ["facebook", "google,github", "google apple"])
def test_unknown_sign_in_providers_refuse_startup(raw: str) -> None:
    with pytest.raises(ValidationError, match="ANUM_SIGN_IN_PROVIDERS"):
        Settings(sign_in_providers=raw)


def test_privacy_policy_defaults_to_the_web_apps_legal_page(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unset ANUM_PRIVACY_POLICY_URL: the web host's public /legal/privacy page (docs/legal-pages.md),
    # from the first public https origin the API allows. Local origins never count.
    monkeypatch.setattr(settings, "privacy_policy_url", "")
    monkeypatch.setattr(
        settings,
        "cors_origins",
        ["http://localhost:5173", "https://tauri.localhost", "https://app.example.com", "https://other.example.com"],
    )
    body = TestClient(app).get("/api/v1/auth/sign-in-options").json()
    assert body["privacy_policy_url"] == "https://app.example.com/legal/privacy"


def test_an_explicit_privacy_policy_wins_over_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "privacy_policy_url", " https://example.com/policy ")
    monkeypatch.setattr(settings, "cors_origins", ["https://app.example.com"])
    assert TestClient(app).get("/api/v1/auth/sign-in-options").json()["privacy_policy_url"] == "https://example.com/policy"


@pytest.mark.parametrize(
    "origins",
    [[], ["http://app.example.com"], ["https://localhost:5173", "https://127.0.0.1"], ["https://app.example.com/path"]],
)
def test_no_default_without_a_public_https_web_origin(monkeypatch: pytest.MonkeyPatch, origins: list[str]) -> None:
    monkeypatch.setattr(settings, "privacy_policy_url", "")
    monkeypatch.setattr(settings, "cors_origins", origins)
    assert TestClient(app).get("/api/v1/auth/sign-in-options").json()["privacy_policy_url"] is None
