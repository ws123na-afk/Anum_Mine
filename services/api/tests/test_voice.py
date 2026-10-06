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


def _ask(text: str, locale: str = "en-US", session_id: str | None = None, sequence: int = 0):
    if session_id is None:
        session_id = client.post(
            "/api/v1/voice/sessions", headers=headers, json={"locale": locale}
        ).json()["id"]
    segment_id = client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript",
        headers=headers,
        json={"text": text, "is_final": True, "client_sequence": sequence},
    ).json()["id"]
    response = client.post(
        f"/api/v1/voice/sessions/{session_id}/ask",
        headers=headers,
        json={"transcript_segment_id": segment_id},
    )
    return session_id, response


def test_spoken_question_is_answered_without_creating_tasks() -> None:
    _, response = _ask("What should I focus on today?")

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "question"
    assert body["risk_tier"] == "read"
    assert body["reply"]
    assert body["assistant_segment"]["role"] == "assistant"
    assert memory_repository.store.tasks == {}


def test_status_question_reports_workspace_counts() -> None:
    _, response = _ask("What's the status of my workspace?")

    assert response.json()["intent"] == "status"
    assert response.json()["workspace"]["tasks_total"] == 0
    assert "0 tasks" in response.json()["reply"]


def test_create_task_by_voice_only_proposes_and_needs_confirmation() -> None:
    _, response = _ask("Create a task: draft the quarterly review")

    body = response.json()
    assert body["intent"] == "create_task"
    assert body["risk_tier"] == "confirm"
    assert body["proposed_task"] == "draft the quarterly review"
    assert memory_repository.store.tasks == {}


def test_approvals_and_deletions_are_never_done_by_voice() -> None:
    for text in ["Approve all pending actions", "delete the finance workspace", "وافق على الطلب"]:
        _, response = _ask(text)
        assert response.json()["intent"] == "visual_only"
        assert response.json()["risk_tier"] == "visual_only"


def test_arabic_session_gets_arabic_status_reply() -> None:
    _, response = _ask("ما الحالة؟", locale="ar-SA")

    assert response.json()["intent"] == "status"
    assert "مهمة" in response.json()["reply"]


def test_ask_rejects_another_users_session() -> None:
    session_id, _ = _ask("hello")
    other = {**headers, "x-user-id": "someone_else"}
    response = client.post(
        f"/api/v1/voice/sessions/{session_id}/ask",
        headers=other,
        json={"transcript_segment_id": "transcript_x"},
    )

    assert response.status_code == 404


def test_ask_is_rate_limited_per_session(monkeypatch) -> None:
    import anum_api.voice as voice_module

    monkeypatch.setattr(voice_module, "MAX_ASKS_PER_SESSION", 2)
    session_id, first = _ask("one")
    _, second = _ask("two", session_id=session_id, sequence=1)
    _, third = _ask("three", session_id=session_id, sequence=2)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
