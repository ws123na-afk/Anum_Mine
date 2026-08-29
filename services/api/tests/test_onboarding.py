from fastapi.testclient import TestClient

from anum_api.identity import local_sessions
from anum_api.main import app, store
from anum_api.onboarding import _model_configs, _notifications


client = TestClient(app)


def setup_function() -> None:
    store.tenants.clear()
    store.workspaces.clear()
    store.memberships.clear()
    local_sessions.clear()
    _model_configs.clear()
    _notifications.clear()


def create_session(workspace_id: str = "workspace_local", user_id: str = "user_local") -> tuple[str, dict]:
    response = client.post(
        "/api/v1/auth/local/session",
        json={"tenant_id": "tenant_local", "workspace_id": workspace_id, "user_id": user_id},
    )
    assert response.status_code == 201
    payload = response.json()
    return payload["access_token"], {"authorization": f"Bearer {payload['access_token']}"}


def test_local_session_drives_idempotent_onboarding() -> None:
    token, headers = create_session()
    assert token.startswith("anum_local_")

    initial = client.get("/api/v1/onboarding", headers=headers)
    first = client.put(
        "/api/v1/onboarding",
        headers=headers,
        json={"organization_name": "Local Org", "workspace_name": "Agent Workspace"},
    )
    repeated = client.put(
        "/api/v1/onboarding",
        headers=headers,
        json={"organization_name": "Ignored Rename", "workspace_name": "Ignored Rename"},
    )

    assert initial.status_code == 200
    assert initial.json()["complete"] is False
    assert first.status_code == 200
    assert first.json()["complete"] is True
    assert first.json()["membership"]["role"] == "owner"
    assert repeated.status_code == 200
    assert repeated.json()["tenant"]["name"] == "Local Org"


def test_model_credentials_are_validated_and_never_returned() -> None:
    _, headers = create_session()
    client.put("/api/v1/onboarding", headers=headers, json={"organization_name": "Org", "workspace_name": "Workspace"})

    missing = client.put(
        "/api/v1/model-config",
        headers=headers,
        json={"provider": "openai_compatible", "model": "model-a", "base_url": "https://models.example/v1"},
    )
    configured = client.put(
        "/api/v1/model-config",
        headers=headers,
        json={"provider": "openai_compatible", "model": "model-a", "base_url": "https://models.example/v1", "api_key": "secret-example-1234"},
    )
    fetched = client.get("/api/v1/model-config", headers=headers)

    assert missing.status_code == 422
    assert configured.status_code == 200
    assert configured.json()["credential_configured"] is True
    assert configured.json()["credential_hint"] == "...1234"
    assert "secret-example" not in configured.text
    assert "secret-example" not in fetched.text


def test_model_configuration_is_workspace_scoped() -> None:
    _, first_headers = create_session("workspace_one")
    _, second_headers = create_session("workspace_two")
    configured = client.put(
        "/api/v1/model-config",
        headers=first_headers,
        json={"provider": "mock", "model": "anum-mock", "base_url": "http://localhost:8000"},
    )

    assert configured.status_code == 200
    assert client.get("/api/v1/model-config", headers=second_headers).status_code == 404


def test_notification_preferences_are_user_scoped() -> None:
    _, first_headers = create_session(user_id="first_user")
    _, second_headers = create_session(user_id="second_user")

    updated = client.put(
        "/api/v1/notification-preferences",
        headers=first_headers,
        json={"task_completed": False, "approval_required": True, "run_failed": True, "automation_failed": True, "email_enabled": True, "desktop_enabled": False},
    )

    assert updated.status_code == 200
    assert client.get("/api/v1/notification-preferences", headers=first_headers).json()["email_enabled"] is True
    assert client.get("/api/v1/notification-preferences", headers=second_headers).json()["email_enabled"] is False


def test_revoked_local_session_is_rejected() -> None:
    _, headers = create_session()
    assert client.delete("/api/v1/auth/local/session", headers=headers).status_code == 204
    assert client.get("/api/v1/onboarding", headers=headers).status_code == 401


def test_browser_can_preflight_onboarding_writes() -> None:
    response = client.options(
        "/api/v1/onboarding",
        headers={
            "origin": "http://127.0.0.1:5173",
            "access-control-request-method": "PUT",
            "access-control-request-headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]
