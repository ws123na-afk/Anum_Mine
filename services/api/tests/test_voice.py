from fastapi import FastAPI
from fastapi.testclient import TestClient

from anum_api.dependencies import memory_repository
from anum_api.voice import router, voice_store


app = FastAPI()
app.include_router(router)
client = TestClient(app)
headers = {
    "x-tenant-id": "tenant_voice",
    "x-workspace-id": "workspace_voice",
    "x-user-id": "user_voice",
    "x-user-roles": "member",
}


def setup_function() -> None:
    voice_store.clear()
    memory_repository.store.tasks.clear()


def test_voice_command_creates_modality_neutral_task() -> None:
    session = client.post(
        "/api/v1/voice/sessions",
        headers=headers,
        json={"locale": "en-US", "retention": "30_days"},
    ).json()
    segment = client.post(
        f"/api/v1/voice/sessions/{session['id']}/transcript",
        headers=headers,
        json={
            "role": "user",
            "text": "Summarize the release notes",
            "is_final": True,
            "client_sequence": 0,
        },
    ).json()

    response = client.post(
        f"/api/v1/voice/sessions/{session['id']}/commands",
        headers=headers,
        json={"transcript_segment_id": segment["id"]},
    )

    assert response.status_code == 200
    assert response.json()["task"]["prompt"] == "Summarize the release notes"
    assert response.json()["task"]["status"] == "created"


def test_interim_or_replayed_transcript_cannot_be_a_command() -> None:
    session_id = client.post("/api/v1/voice/sessions", headers=headers, json={}).json()["id"]
    segment_id = client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript",
        headers=headers,
        json={"text": "draft", "is_final": False, "client_sequence": 0},
    ).json()["id"]
    command = {"transcript_segment_id": segment_id}

    assert client.post(
        f"/api/v1/voice/sessions/{session_id}/commands", headers=headers, json=command
    ).status_code == 422

    final_id = client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript",
        headers=headers,
        json={"text": "final", "is_final": True, "client_sequence": 1},
    ).json()["id"]
    final_command = {"transcript_segment_id": final_id}
    assert client.post(
        f"/api/v1/voice/sessions/{session_id}/commands", headers=headers, json=final_command
    ).status_code == 200
    assert client.post(
        f"/api/v1/voice/sessions/{session_id}/commands", headers=headers, json=final_command
    ).status_code == 409


def test_voice_session_is_private_to_originating_user_and_workspace() -> None:
    session_id = client.post("/api/v1/voice/sessions", headers=headers, json={}).json()["id"]
    other_user = {**headers, "x-user-id": "user_other"}
    other_workspace = {**headers, "x-workspace-id": "workspace_other"}

    assert client.get(f"/api/v1/voice/sessions/{session_id}", headers=other_user).status_code == 404
    assert client.get(
        f"/api/v1/voice/sessions/{session_id}", headers=other_workspace
    ).status_code == 404


def test_session_retention_erases_transcript_when_completed() -> None:
    session_id = client.post(
        "/api/v1/voice/sessions", headers=headers, json={"retention": "session"}
    ).json()["id"]
    client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript",
        headers=headers,
        json={"text": "sensitive note", "client_sequence": 0},
    )

    completed = client.post(
        f"/api/v1/voice/sessions/{session_id}/complete", headers=headers
    )

    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert client.get(
        f"/api/v1/voice/sessions/{session_id}/transcript", headers=headers
    ).json() == []


def test_voice_api_exposes_no_approval_decision_route() -> None:
    paths = {route.path for route in app.routes}
    assert all("approv" not in path for path in paths if path.startswith("/api/v1/voice"))
