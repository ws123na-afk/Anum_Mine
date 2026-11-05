"""Fail-closed and schema tests for the live-agent meeting management API."""
from fastapi.testclient import TestClient
from pydantic import ValidationError

from anum_api.live_agent_meeting_api import CreateMeeting, StateChange
from anum_api.main import app
from anum_api.settings import settings

client = TestClient(app)


def _headers(role="owner"):
    return {
        "x-tenant-id": "meeting_api_test_tenant",
        "x-workspace-id": "meeting_api_test_workspace",
        "x-user-id": "meeting_api_test_owner",
        "x-user-roles": role,
    }


def test_migrated_postgresql_is_required_for_persistent_meetings(monkeypatch):
    monkeypatch.setattr(settings, "repository_backend", "memory")
    response = client.post("/api/v1/agent-meetings", headers=_headers(),
                           json={"title": "Monthly operations"})
    assert response.status_code == 503, response.text
    assert "PostgreSQL" in response.json()["error"]["message"]


def test_viewer_cannot_create_meeting_even_without_database(monkeypatch):
    monkeypatch.setattr(settings, "repository_backend", "memory")
    response = client.post("/api/v1/agent-meetings", headers=_headers("viewer"),
                           json={"title": "Unauthorized"})
    assert response.status_code == 403, response.text


def test_model_rejects_invalid_limits_and_client_scope():
    for payload in (
        {"title": "Valid", "tenant_id": "other"},
        {"title": "Valid", "limits": {"max_turns": 0}},
        {"title": "Valid", "limits": {"max_cost_usd": -1}},
        {"title": "Valid", "limits": {"max_participants": 51}},
    ):
        try:
            CreateMeeting.model_validate(payload)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"Expected strict schema rejection: {payload}")


def test_client_cannot_invent_agent_execution_states():
    for target in ("running_agents", "executing", "completed"):
        try:
            StateChange.model_validate({"expected_revision": 1, "status": target})
        except ValidationError:
            pass
        else:
            raise AssertionError(f"Unexpected state accepted: {target}")


def test_turns_are_not_faked_in_memory_backend(monkeypatch):
    monkeypatch.setattr(settings, "repository_backend", "memory")
    response = client.get("/api/v1/agent-meetings/nonexistent/turns", headers=_headers())
    assert response.status_code == 503, response.text


def test_meeting_detail_says_which_participants_have_left(monkeypatch):
    """A person who left the room carries the stored `left_at`; everyone else carries null. Nobody is added or dropped."""
    from contextlib import contextmanager

    import anum_api.live_agent_meeting_api as meeting_api

    class FakeStore:
        def get(self, context, meeting_id):
            return {"id": meeting_id, "status": "active", "title": "Weekly"}

        def participants(self, context, meeting_id):
            return [
                {"id": "p1", "actor_id": "agent_1", "kind": "agent"},
                {"id": "p2", "actor_id": "user_left", "kind": "human"},
                {"id": "p3", "actor_id": "user_here", "kind": "human"},
            ]

        def left_at_by_actor(self, context, meeting_id):
            return {"user_left": "2026-10-28T09:30:00+00:00"}

    @contextmanager
    def fake_store(context):
        yield FakeStore()

    monkeypatch.setattr(meeting_api, "_store", fake_store)
    response = client.get("/api/v1/agent-meetings/meeting_1", headers=_headers())
    assert response.status_code == 200, response.text
    by_actor = {p["actor_id"]: p for p in response.json()["participants"]}
    assert list(by_actor) == ["agent_1", "user_left", "user_here"]
    assert by_actor["user_left"]["left_at"] == "2026-10-28T09:30:00+00:00"
    assert by_actor["user_here"]["left_at"] is None
    assert by_actor["agent_1"]["left_at"] is None
