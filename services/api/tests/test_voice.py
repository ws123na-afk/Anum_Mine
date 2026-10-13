from fastapi import FastAPI
from fastapi.testclient import TestClient

import anum_api.main  # noqa: F401  (configures the voice task runtime, as in the app)
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
    # FastAPI keeps included routers nested in app.routes, so read the public schema;
    # no route in the API is excluded from it.
    paths = set(app.openapi()["paths"])
    voice_paths = {path for path in paths if path.startswith("/api/v1/voice")}
    assert voice_paths
    assert voice_paths == {route.path for route in router.routes}
    assert all("approv" not in path for path in voice_paths)


def _ask(
    text: str,
    locale: str = "en-US",
    session_id: str | None = None,
    sequence: int = 0,
    name: str = "Anum",
):
    if session_id is None:
        session_id = client.post(
            "/api/v1/voice/sessions", headers=headers, json={"locale": locale, "assistant_name": name}
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
    assert "no tasks yet" in response.json()["reply"]


def test_without_the_task_runtime_a_spoken_task_is_only_proposed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # An app that mounts only the voice router has no task runtime: the old proposal flow.
    # With the runtime (main.py) the task is created and run: tests/test_voice_actions.py.
    from anum_api import voice_actions

    monkeypatch.setattr(voice_actions, "_configured", None)
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
    assert "مهام" in response.json()["reply"]


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


def test_assistant_tells_its_name() -> None:
    _, response = _ask("What's your name?", name="Layla")

    assert response.json()["intent"] == "identity"
    assert "I'm Layla" in response.json()["reply"]


def test_calling_the_name_greets_and_wake_word_is_stripped() -> None:
    _, greeting = _ask("Layla", name="Layla")
    _, status = _ask("Hey Layla, what's the status of my workspace?", name="Layla")
    _, task = _ask("Layla create a task: book the review", name="Layla")

    assert greeting.json()["intent"] == "greeting"
    assert status.json()["intent"] == "status"
    # The name is stripped before the request is read: the task is "Book the review".
    assert task.json()["intent"] == "action"
    assert task.json()["action"]["title"] == "Book the review"


def test_status_reply_reads_naturally() -> None:
    from anum_api.voice_assistant import WorkspaceSnapshot, status_reply

    one = status_reply(WorkspaceSnapshot(tasks_total=1, running=1, waiting_approval=0, pending_approvals=1), False)
    many = status_reply(WorkspaceSnapshot(tasks_total=3, running=0, waiting_approval=1, pending_approvals=2), False)

    assert one == "You've got one task, and one is running. One approval is waiting for you."
    assert many == "You've got 3 tasks, and nothing is running right now. 2 approvals are waiting for you."


def test_assistant_name_is_validated() -> None:
    response = client.post(
        "/api/v1/voice/sessions", headers=headers, json={"assistant_name": "<script>"}
    )

    assert response.status_code == 422


def _session_with_transcript(retention: str) -> str:
    session_id = client.post(
        "/api/v1/voice/sessions", headers=headers, json={"retention": retention}
    ).json()["id"]
    client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript",
        headers=headers,
        json={"text": "remember the dentist", "client_sequence": 0},
    )
    return session_id


def test_cancelling_a_session_only_session_erases_its_transcript_too() -> None:
    session_id = _session_with_transcript("session")

    assert client.delete(f"/api/v1/voice/sessions/{session_id}", headers=headers).json()["status"] == "cancelled"
    assert client.get(f"/api/v1/voice/sessions/{session_id}/transcript", headers=headers).json() == []


def test_thirty_day_transcript_is_hidden_after_expiry_and_purged() -> None:
    from datetime import timedelta

    session_id = _session_with_transcript("30_days")
    session = voice_store.sessions[session_id]
    assert session.expires_at == session.created_at + timedelta(days=30)
    assert client.post(f"/api/v1/voice/sessions/{session_id}/complete", headers=headers).status_code == 200
    assert len(client.get(f"/api/v1/voice/sessions/{session_id}/transcript", headers=headers).json()) == 1

    session.expires_at = session.created_at  # thirty days later
    assert client.get(f"/api/v1/voice/sessions/{session_id}/transcript", headers=headers).json() == []
    assert voice_store.purge_expired() == 1
    assert voice_store.segments[session_id] == []


def test_permanent_transcript_outlives_completion() -> None:
    session_id = _session_with_transcript("permanent")

    completed = client.post(f"/api/v1/voice/sessions/{session_id}/complete", headers=headers).json()
    assert completed["expires_at"] is None
    assert voice_store.purge_expired() == 0
    assert [item["text"] for item in client.get(
        f"/api/v1/voice/sessions/{session_id}/transcript", headers=headers
    ).json()] == ["remember the dentist"]


def _stream_ask(session_id: str, text: str, sequence: int) -> list[tuple[str, dict]]:
    import json

    segment_id = client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript",
        headers=headers,
        json={"text": text, "is_final": True, "client_sequence": sequence},
    ).json()["id"]
    response = client.post(
        f"/api/v1/voice/sessions/{session_id}/ask/stream",
        headers=headers,
        json={"transcript_segment_id": segment_id},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = []
    for frame in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        frames.append((lines["event"], json.loads(lines["data"])))
    return frames


def test_streamed_ask_keeps_intents_and_stores_the_reply() -> None:
    session_id = client.post("/api/v1/voice/sessions", headers=headers, json={}).json()["id"]
    frames = _stream_ask(session_id, "Approve all pending actions", 0)
    assert [kind for kind, _ in frames] == ["meta", "delta", "timing", "done"]
    assert frames[0][1]["risk_tier"] == "visual_only"
    reply = frames[1][1]["text"]
    assert frames[3][1]["assistant_segment"]["text"] == reply

    frames = _stream_ask(session_id, "Why is the sky blue?", 1)
    assert frames[0][1]["intent"] == "question"
    assert "connect a free local model" in "".join(d["text"] for k, d in frames if k == "delta")
    transcript = client.get(f"/api/v1/voice/sessions/{session_id}/transcript", headers=headers).json()
    assert [item["role"] for item in transcript] == ["user", "assistant", "user", "assistant"]


def test_streamed_ask_sends_recent_spoken_turns_to_the_model() -> None:
    from anum_api.voice import voice_model_gateway

    seen: list[list[dict[str, str]]] = []

    class RecordingGateway:
        provider = "test"

        async def stream_chat(self, messages, *, max_tokens=None, on_usage=None):  # type: ignore[no-untyped-def]
            seen.append(messages)
            assert max_tokens == 400
            yield "It is "
            yield "Rayleigh scattering."

    app.dependency_overrides[voice_model_gateway] = lambda: RecordingGateway()
    try:
        session_id = client.post("/api/v1/voice/sessions", headers=headers, json={}).json()["id"]
        _stream_ask(session_id, "Why is the sky blue?", 0)
        frames = _stream_ask(session_id, "And at sunset?", 1)
    finally:
        app.dependency_overrides.clear()
    assert "".join(d["text"] for k, d in frames if k == "delta") == "It is Rayleigh scattering."
    roles = [m["role"] for m in seen[1]]
    assert roles == ["system", "user", "assistant", "user"]
    assert "Why is the sky blue?" in seen[1][1]["content"]
    assert "And at sunset?" in seen[1][-1]["content"]


def test_recent_sessions_list_is_own_newest_first_and_paged() -> None:
    first = client.post("/api/v1/voice/sessions", headers=headers, json={"retention": "permanent"}).json()["id"]
    client.post(
        f"/api/v1/voice/sessions/{first}/transcript",
        headers=headers,
        json={"text": "  How   many tyres are low on stock?  ", "client_sequence": 0},
    )
    client.post(f"/api/v1/voice/sessions/{first}/complete", headers=headers)
    second = client.post("/api/v1/voice/sessions", headers=headers, json={}).json()["id"]
    third = client.post("/api/v1/voice/sessions", headers=headers, json={}).json()["id"]
    client.post("/api/v1/voice/sessions", headers={**headers, "x-user-id": "user_other"}, json={})
    client.post("/api/v1/voice/sessions", headers={**headers, "x-workspace-id": "workspace_other"}, json={})

    page = client.get("/api/v1/voice/sessions?limit=2", headers=headers)

    assert page.status_code == 200
    body = page.json()
    assert [item["id"] for item in body["items"]] == [third, second]
    assert body["next_cursor"]
    older = client.get(f"/api/v1/voice/sessions?limit=2&cursor={body['next_cursor']}", headers=headers).json()
    assert [item["id"] for item in older["items"]] == [first]
    assert older["next_cursor"] is None
    assert older["items"][0]["first_line"] == "How many tyres are low on stock?"
    assert older["items"][0]["segment_count"] == 1
    assert older["items"][0]["status"] == "completed"

    completed = client.get("/api/v1/voice/sessions?status=completed", headers=headers).json()["items"]
    assert [item["id"] for item in completed] == [first]


def test_recent_sessions_list_rejects_a_forged_cursor_or_limit() -> None:
    assert client.get("/api/v1/voice/sessions?cursor=not-a-cursor", headers=headers).status_code == 422
    assert client.get("/api/v1/voice/sessions?limit=0", headers=headers).status_code == 422
    assert client.get("/api/v1/voice/sessions?limit=101", headers=headers).status_code == 422


def test_recent_sessions_list_hides_erased_and_expired_transcripts() -> None:
    from datetime import timedelta

    erased = client.post("/api/v1/voice/sessions", headers=headers, json={"retention": "session"}).json()["id"]
    client.post(f"/api/v1/voice/sessions/{erased}/transcript", headers=headers, json={"text": "secret", "client_sequence": 0})
    client.post(f"/api/v1/voice/sessions/{erased}/complete", headers=headers)
    expired = client.post("/api/v1/voice/sessions", headers=headers, json={"retention": "30_days"}).json()["id"]
    client.post(f"/api/v1/voice/sessions/{expired}/transcript", headers=headers, json={"text": "old", "client_sequence": 0})
    voice_store.sessions[expired].expires_at = voice_store.sessions[expired].created_at - timedelta(seconds=1)

    items = {item["id"]: item for item in client.get("/api/v1/voice/sessions", headers=headers).json()["items"]}

    assert items[erased]["segment_count"] == 0 and items[erased]["first_line"] is None
    assert items[expired]["segment_count"] == 0 and items[expired]["first_line"] is None
