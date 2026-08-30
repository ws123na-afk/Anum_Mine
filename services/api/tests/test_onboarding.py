from fastapi.testclient import TestClient

from anum_api.identity import local_sessions
from anum_api.main import app, store
from anum_api.onboarding import _local_auth, _model_configs, _notifications


client = TestClient(app)


def setup_function() -> None:
    store.tenants.clear()
    store.workspaces.clear()
    store.memberships.clear()
    local_sessions.clear()
    _model_configs.clear()
    _notifications.clear()
    _local_auth.clear()


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


def test_model_connection_can_be_verified() -> None:
    _, headers = create_session()
    configured = client.put(
        "/api/v1/model-config",
        headers=headers,
        json={"provider": "mock", "model": "anum-mock", "base_url": "http://localhost:8000"},
    )
    tested = client.post("/api/v1/model-config/test", headers=headers)

    assert configured.status_code == 200
    assert tested.status_code == 200
    assert tested.json() == {
        "provider": "mock",
        "model": "anum-mock",
        "latency_ms": 0,
        "status": "connected",
    }


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


def test_otp_is_one_time_and_returns_a_session() -> None:
    requested = client.post(
        "/api/v1/auth/local/otp/request",
        json={"tenant_id": "tenant_local", "workspace_id": "workspace_local", "user_id": "owner@example.test"},
    )
    assert requested.status_code == 202
    challenge = requested.json()
    assert challenge["delivery_hint"] == "o***@example.test"

    verified = client.post(
        "/api/v1/auth/local/otp/verify",
        json={"challenge_id": challenge["challenge_id"], "code": challenge["debug_secret"]},
    )
    replayed = client.post(
        "/api/v1/auth/local/otp/verify",
        json={"challenge_id": challenge["challenge_id"], "code": challenge["debug_secret"]},
    )
    assert verified.status_code == 200
    assert verified.json()["access_token"].startswith("anum_local_")
    assert replayed.status_code == 401


def test_otp_challenge_locks_after_five_failures() -> None:
    challenge = client.post(
        "/api/v1/auth/local/otp/request",
        json={"tenant_id": "tenant_local", "workspace_id": "workspace_local", "user_id": "user_local"},
    ).json()
    wrong_code = "999999" if challenge["debug_secret"] != "999999" else "888888"
    for _ in range(5):
        assert client.post("/api/v1/auth/local/otp/verify", json={"challenge_id": challenge["challenge_id"], "code": wrong_code}).status_code == 401
    assert client.post("/api/v1/auth/local/otp/verify", json={"challenge_id": challenge["challenge_id"], "code": challenge["debug_secret"]}).status_code == 401


def test_password_reset_enforces_new_password_on_direct_sign_in() -> None:
    scope = {"tenant_id": "tenant_local", "workspace_id": "workspace_local", "user_id": "user_local"}
    challenge = client.post("/api/v1/auth/local/password/forgot", json=scope).json()
    reset = client.post(
        "/api/v1/auth/local/password/reset",
        json={"challenge_id": challenge["challenge_id"], "token": challenge["debug_secret"], "new_password": "correct horse 42"},
    )
    assert reset.status_code == 200
    assert client.post("/api/v1/auth/local/session", json=scope).status_code == 401
    assert client.post("/api/v1/auth/local/session", json={**scope, "password": "wrong password 42"}).status_code == 401
    assert client.post("/api/v1/auth/local/session", json={**scope, "password": "correct horse 42"}).status_code == 201


def test_workspace_switch_requires_membership_and_rotates_session() -> None:
    old_token, headers = create_session("workspace_one")
    client.put("/api/v1/onboarding", headers=headers, json={"organization_name": "Org", "workspace_name": "One"})
    # Provision the second workspace with development headers, then give the same user membership.
    raw_headers = {"x-tenant-id": "tenant_local", "x-workspace-id": "workspace_two", "x-user-id": "user_local", "x-user-roles": "owner"}
    client.post("/api/v1/workspaces", headers=raw_headers, json={"name": "Two"})
    client.post("/api/v1/workspace-memberships/current", headers=raw_headers)

    switched = client.post("/api/v1/auth/local/workspace/switch", headers=headers, json={"workspace_id": "workspace_two"})
    assert switched.status_code == 200
    assert switched.json()["context"]["workspace_id"] == "workspace_two"
    assert client.get("/api/v1/onboarding", headers={"authorization": f"Bearer {old_token}"}).status_code == 401
    assert client.get("/api/v1/onboarding", headers={"authorization": f"Bearer {switched.json()['access_token']}"}).status_code == 200


def test_workspace_switch_rejects_missing_membership() -> None:
    _, headers = create_session("workspace_one")
    denied = client.post("/api/v1/auth/local/workspace/switch", headers=headers, json={"workspace_id": "workspace_other"})
    assert denied.status_code == 403
