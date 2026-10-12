"""Connection setup page: status per integration, names only, admin-only checks (docs/integrations-setup.md)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from anum_api import integration_setup, main
from anum_api.email_inbox import email_settings
from anum_api.settings import settings
from anum_api.store import store

OWNER = {"x-tenant-id": "tenant_setup", "x-workspace-id": "workspace_setup", "x-user-id": "owner_setup", "x-user-roles": "owner"}
ADMIN = {**OWNER, "x-user-id": "admin_setup", "x-user-roles": "member,org_admin"}
MEMBER = {**OWNER, "x-user-id": "member_setup", "x-user-roles": "member"}
SECRET_VALUE = "example-secret-value"
RUNBOOK = Path(__file__).resolve().parents[3] / "docs" / "integrations-setup.md"


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def _items(client: TestClient, headers: dict[str, str]) -> tuple[dict, dict[str, dict]]:
    response = client.get("/api/v1/integrations/setup", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    return body, {item["id"]: item for item in body["items"]}


def test_lists_every_owner_facing_integration_with_a_documented_section(client: TestClient) -> None:
    body, items = _items(client, OWNER)
    assert {
        "model", "speech", "transcription", "web_search", "email_gmail", "email_outlook", "email_zoho", "whatsapp",
        "meetings_zoom", "meetings_teams", "push", "notification_email", "sign_in_google", "sign_in_apple",
        "error_reporting", "files", "external_webhook", "marketing_meta", "marketing_linkedin", "marketing_x",
        "marketing_tiktok", "marketing_google", "commerce", "fleet_data", "developer",
    } == set(items)
    headings = {
        re.sub(r"[^a-z0-9 -]", "", line[3:].strip().lower()).replace(" ", "-")
        for line in RUNBOOK.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    }
    for item in body["items"]:
        assert item["doc"] in headings, f"{item['id']} links to a missing runbook section {item['doc']}"
        assert item["status"] in {"connected", "ready", "not_configured", "error"}


def test_defaults_report_not_configured_with_setting_names_for_owners(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "model_provider", "mock")
    monkeypatch.setattr(settings, "fcm_service_account_json", None)
    monkeypatch.setattr(email_settings, "google_client_id", "")
    body, items = _items(client, OWNER)
    assert body["can_view_setup"] is True and body["can_check"] is False
    assert items["model"]["status"] == "not_configured"
    assert "ANUM_MODEL_API_KEY" in items["model"]["missing"]
    assert items["push"] == {**items["push"], "status": "not_configured", "missing": ["ANUM_FCM_SERVICE_ACCOUNT_JSON"]}
    assert items["email_gmail"]["status"] == "not_configured"
    assert "ANUM_EMAIL_GOOGLE_CLIENT_ID" in items["email_gmail"]["missing"]
    assert items["email_gmail"]["redirect_urls"] == [email_settings.redirect_uri]
    assert items["whatsapp"]["redirect_urls"][0].endswith("/api/v1/whatsapp/webhook")
    # Per-workspace connections with no server keys: ready to connect, nothing connected yet.
    assert items["commerce"]["status"] == "ready" and items["commerce"]["connections"] == 0
    assert items["whatsapp"]["status"] == "ready"


def test_members_see_status_but_not_setting_names_or_callback_urls(client: TestClient) -> None:
    body, items = _items(client, MEMBER)
    assert body["can_view_setup"] is False and body["can_check"] is False
    assert all(item["missing"] == [] and item["redirect_urls"] == [] for item in items.values())
    assert items["model"]["status"] in {"not_configured", "connected"}


def test_configured_values_are_never_returned(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "model_provider", "anthropic")
    monkeypatch.setattr(settings, "model_api_key", SECRET_VALUE)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "anum@example.com")
    monkeypatch.setattr(settings, "smtp_username", "anum")
    monkeypatch.setattr(settings, "smtp_password", SecretStr(SECRET_VALUE))
    monkeypatch.setattr(settings, "external_webhook_url", "https://hooks.example.com/anum")
    monkeypatch.setattr(settings, "external_webhook_api_key", SECRET_VALUE)
    monkeypatch.setattr(email_settings, "google_client_id", "client-id.example")
    monkeypatch.setattr(email_settings, "google_client_secret", SecretStr(SECRET_VALUE))
    for headers in (OWNER, ADMIN, MEMBER):
        response = client.get("/api/v1/integrations/setup", headers=headers)
        assert SECRET_VALUE not in response.text
        assert "client-id.example" not in response.text
    _, items = _items(client, OWNER)
    assert items["model"]["status"] == "connected" and items["model"]["missing"] == []
    assert items["notification_email"]["status"] == "connected"
    assert items["external_webhook"]["status"] == "connected"
    assert items["email_gmail"]["status"] == "ready" and items["email_gmail"]["connections"] == 0


def test_half_done_settings_are_errors_that_name_the_missing_setting(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "speech_stt_base_url", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "speech_stt_model", "")
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", None)
    monkeypatch.setattr(settings, "model_provider", "openai-compatible")
    monkeypatch.setattr(settings, "model_api_key", None)
    _, items = _items(client, OWNER)
    assert items["speech"] == {**items["speech"], "status": "error", "missing": ["ANUM_SPEECH_STT_MODEL"]}
    assert items["notification_email"]["status"] == "error"
    assert items["notification_email"]["missing"] == ["ANUM_SMTP_FROM"]
    assert items["model"]["missing"] == ["ANUM_MODEL_API_KEY"]


def test_a_failing_module_marks_only_its_own_item(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_: object) -> dict:
        raise RuntimeError("database down at 10.0.0.5")

    entry = integration_setup._BY_ID["commerce"]
    monkeypatch.setattr(entry, "evaluate", broken)
    response = client.get("/api/v1/integrations/setup", headers=OWNER)
    assert response.status_code == 200 and "10.0.0.5" not in response.text
    items = {item["id"]: item for item in response.json()["items"]}
    assert items["commerce"]["status"] == "error"
    assert items["model"]["status"] in {"connected", "not_configured"}


def test_connected_accounts_in_this_workspace_turn_ready_into_connected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.commerce import store as commerce_store_module

    class Connector:
        def __init__(self, error: str | None) -> None:
            self.enabled, self.last_error = True, error

    workspaces: dict[str, list[Connector]] = {"workspace_setup": [Connector(None), Connector(None)], "workspace_setup_failing": [Connector("HTTP 401")]}
    opened: list[str] = []

    class Store:
        def list_connectors(self, context: object) -> list[Connector]:
            return workspaces.get(context.workspace_id, [])  # type: ignore[attr-defined]

    @contextmanager
    def fake_open(context: object) -> Iterator[Store]:
        opened.append(context.workspace_id)  # type: ignore[attr-defined]
        yield Store()

    monkeypatch.setattr(commerce_store_module, "open_commerce_store", fake_open)
    _, items = _items(client, OWNER)
    assert items["commerce"] == {**items["commerce"], "status": "connected", "connections": 2, "failing": 0}
    _, failing = _items(client, {**OWNER, "x-workspace-id": "workspace_setup_failing"})
    assert failing["commerce"] == {**failing["commerce"], "status": "error", "connections": 1, "failing": 1}
    _, other = _items(client, {**OWNER, "x-workspace-id": "workspace_setup_other"})
    assert other["commerce"]["status"] == "ready" and other["commerce"]["connections"] == 0
    # Read in the caller's own tenant context, once per request.
    assert opened == ["workspace_setup", "workspace_setup_failing", "workspace_setup_other"]


def test_only_organization_admins_run_checks_and_each_check_is_audited(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "external_webhook_url", "https://hooks.example.com/anum")
    reached: list[tuple[str, int]] = []

    async def fake_reach(host: str, port: int) -> None:
        reached.append((host, port))

    monkeypatch.setattr(integration_setup, "_reach", fake_reach)
    assert client.post("/api/v1/integrations/setup/external_webhook/check", headers=MEMBER).status_code == 403
    denied = client.post("/api/v1/integrations/setup/external_webhook/check", headers=OWNER)
    assert denied.status_code == 403 and "organization administrator" in denied.json()["error"]["message"]
    body, _ = _items(client, ADMIN)
    assert body["can_check"] is True and body["can_view_setup"] is True
    ok = client.post("/api/v1/integrations/setup/external_webhook/check", headers=ADMIN)
    assert ok.status_code == 200, ok.text
    assert ok.json()["reachable"] is True and ok.json()["detail"] == "Reachable"
    assert reached == [("hooks.example.com", 443)]
    records = [r for r in store.audit_records if r.action == "integration.setup_checked" and r.workspace_id == "workspace_setup"]
    assert records and records[-1].target == "integration:external_webhook" and records[-1].outcome == "success"


def test_a_failed_check_reports_the_class_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "external_webhook_url", "https://hooks.example.com/anum")

    async def refused(host: str, port: int) -> None:
        raise ConnectionRefusedError(f"refused by {host} for user admin")

    monkeypatch.setattr(integration_setup, "_reach", refused)
    response = client.post("/api/v1/integrations/setup/external_webhook/check", headers=ADMIN)
    assert response.status_code == 200
    assert response.json() == {**response.json(), "reachable": False, "detail": "ConnectionRefusedError", "latency_ms": None}
    assert "hooks.example.com" not in response.text


def test_checks_refuse_unknown_and_unconfigured_integrations(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "external_webhook_url", None)
    assert client.post("/api/v1/integrations/setup/nope/check", headers=ADMIN).status_code == 404
    assert client.post("/api/v1/integrations/setup/external_webhook/check", headers=ADMIN).status_code == 409
    assert client.post("/api/v1/integrations/setup/commerce/check", headers=ADMIN).status_code == 409
