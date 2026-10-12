"""Calls & Meetings API, memory backend (docs/meetings.md).

Models, speech-to-text and Zoom/Microsoft are replaced by fakes: a gateway object,
and ``httpx.MockTransport`` for every HTTP call. Background processing runs inside the
TestClient request, so each import is fully processed when the call returns.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from anum_api import meeting_connectors, meetings, transcription
from anum_api.files import InMemoryObjectStorage, file_store
from anum_api.meeting_connectors import connection_store
from anum_api.meetings import meeting_store
from anum_api.model_budget import BudgetKind, BudgetScope, ModelBudgetExceededError
from anum_api.model_gateway import ModelResponse, ModelUsage
from anum_api.schemas import utc_now
from anum_api.settings import settings
from anum_api.skills_api import skill_store

TENANT, WORKSPACE = "tenant_meet", "workspace_meet"


def headers(user: str = "user_meet", role: str = "member", workspace: str = WORKSPACE) -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER = headers("owner_meet", "owner")
VIEWER = headers("viewer_meet", "viewer")
OTHER_MEMBER = headers("other_meet", "member")

VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
<v Jane Doe>Welcome to the PMV planning call.</v>

2
00:00:05.000 --> 00:00:09.000
<v Omar Ali>I will send the tyre stock report by Friday.</v>

3
00:00:10.000 --> 00:00:12.000
<v Jane Doe>We decided to restock Noon UAE first.</v>
"""

ANSWER = {
    "summary": "The team reviewed the PMV plan and agreed on restocking.",
    "decisions": ["Restock Noon UAE first"],
    "action_items": [
        {"text": "Send the tyre stock report", "owner": "Omar Ali", "due_date": "2026-10-16"},
        {"text": "Confirm supplier prices", "owner": None, "due_date": None},
    ],
    "follow_up_email": {"subject": "PMV call recap", "body": "Hello all,\n\nThanks for joining."},
}


class FakeGateway:
    def __init__(self, text: str | None = None, *, provider: str = "test", error: Exception | None = None) -> None:
        self.text = text if text is not None else json.dumps(ANSWER)
        self.provider = provider
        self.model = "test-model"
        self.error = error
        self.calls: list[list[dict[str, str]]] = []

    async def complete_chat(self, messages: list[dict[str, str]], *, max_tokens: int | None = None) -> ModelResponse:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return ModelResponse(
            text=self.text,
            usage=ModelUsage(input_tokens=100, output_tokens=50, provider=self.provider, model=self.model),
        )


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    meeting_store.clear()
    connection_store.clear()
    skill_store.clear()
    monkeypatch.setattr(file_store, "storage", InMemoryObjectStorage())
    file_store.records.clear()
    monkeypatch.setattr(settings, "transcription_provider", "none")
    monkeypatch.setattr(settings, "transcription_base_url", "")
    yield
    meeting_store.clear()
    connection_store.clear()
    file_store.records.clear()


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    fake = FakeGateway()
    monkeypatch.setattr(meetings, "analysis_gateway", lambda context: fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    from anum_api.main import app

    return TestClient(app)


def _upload(client: TestClient, content: str | bytes = VTT, *, name: str = "pmv.vtt", hdrs: dict[str, str] | None = None, **params: str):
    body = content.encode() if isinstance(content, str) else content
    return client.post(
        "/api/v1/meetings/transcripts",
        content=body,
        headers={**(hdrs or headers()), "x-file-name": name, "content-type": "text/vtt"},
        params=params,
    )


# --------------------------------------------------------------------------- import and processing


def test_transcript_import_is_summarised_with_provenance(client: TestClient, gateway: FakeGateway) -> None:
    created = _upload(client, title="PMV planning", occurred_at="2026-10-11T09:00:00Z")
    assert created.status_code == 201, created.text
    meeting_id = created.json()["id"]
    assert created.json()["status"] == "analyzing"

    meeting = client.get(f"/api/v1/meetings/{meeting_id}", headers=headers()).json()
    assert meeting["status"] == "ready"
    assert meeting["title"] == "PMV planning" and meeting["source"] == "transcript"
    assert meeting["language"] == "en" and meeting["segment_count"] == 3 and meeting["duration_seconds"] == 12
    assert meeting["summary"] == ANSWER["summary"]
    assert meeting["decisions"] == ["Restock Noon UAE first"]
    assert [(item["text"], item["owner"], item["due_date"]) for item in meeting["action_items"]] == [
        ("Send the tyre stock report", "Omar Ali", "2026-10-16"),
        ("Confirm supplier prices", None, None),
    ]
    assert meeting["follow_up"]["subject"] == "PMV call recap" and meeting["follow_up"]["to"] == []
    provenance = meeting["provenance"]
    assert provenance["label"] == "ai_generated" and provenance["model"] == "test-model" and provenance["truncated"] is False

    # The transcript reached the model only as a labelled untrusted block.
    user_message = gateway.calls[0][1]["content"]
    assert "source=meeting_transcript" in user_message and "Omar Ali: I will send" in user_message
    assert "2026-10-11" in gateway.calls[0][0]["content"]


def test_mock_model_waits_for_a_real_one_and_summarize_retries(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    mock = FakeGateway(provider="mock")
    monkeypatch.setattr(meetings, "analysis_gateway", lambda context: mock)
    meeting_id = _upload(client).json()["id"]
    waiting = client.get(f"/api/v1/meetings/{meeting_id}", headers=headers()).json()
    assert waiting["status"] == "needs_model" and waiting["error_code"] == "no_model" and waiting["summary"] is None
    assert "Settings > Model" in waiting["error"]
    assert mock.calls == []
    assert client.get("/api/v1/meetings/setup", headers=headers()).json()["model_ready"] is False

    real = FakeGateway()
    monkeypatch.setattr(meetings, "analysis_gateway", lambda context: real)
    retried = client.post(f"/api/v1/meetings/{meeting_id}/process", headers=headers())
    assert retried.status_code == 202
    assert client.get(f"/api/v1/meetings/{meeting_id}", headers=headers()).json()["status"] == "ready"


@pytest.mark.parametrize(
    ("gateway_factory", "code", "text"),
    [
        (lambda: FakeGateway(text="I could not do it."), "analysis_failed", "could not be read"),
        (lambda: FakeGateway(error=RuntimeError("boom")), "model_failed", "could not be reached"),
        (
            lambda: FakeGateway(error=ModelBudgetExceededError(BudgetScope.WORKSPACE, BudgetKind.COST, date(2026, 11, 1))),
            "budget_exceeded",
            "budget",
        ),
    ],
)
def test_model_failures_end_in_a_visible_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, gateway_factory: Callable[[], FakeGateway], code: str, text: str
) -> None:
    fake = gateway_factory()
    monkeypatch.setattr(meetings, "analysis_gateway", lambda context: fake)
    meeting_id = _upload(client).json()["id"]
    meeting = client.get(f"/api/v1/meetings/{meeting_id}", headers=headers()).json()
    assert meeting["status"] == "failed" and meeting["error_code"] == code
    assert text in meeting["error"]
    # The transcript is kept either way.
    assert client.get(f"/api/v1/meetings/{meeting_id}/transcript", headers=headers()).json()["total"] == 3


def test_arabic_transcript_is_detected_and_summarised_in_arabic(client: TestClient, gateway: FakeGateway) -> None:
    text = "سارة: صباح الخير، نراجع خطة الإطارات.\nأحمد: سأرسل تقرير المخزون يوم الخميس.\n"
    meeting_id = _upload(client, text, name=quote("اجتماع.txt")).json()["id"]
    meeting = client.get(f"/api/v1/meetings/{meeting_id}", headers=headers()).json()
    assert meeting["language"] == "ar" and meeting["title"] == "اجتماع"
    assert "Write every value in Arabic" in gateway.calls[0][0]["content"]
    page = client.get(f"/api/v1/meetings/{meeting_id}/transcript", params={"q": "المخزون"}, headers=headers()).json()
    assert [row["speaker"] for row in page["items"]] == ["أحمد"]


def test_bad_transcript_files_are_refused_with_the_reason(client: TestClient, gateway: FakeGateway) -> None:
    assert _upload(client, b"WEBVTT\n\nnothing", name="a.vtt").json()["error"]["message"].startswith("No timed captions")
    assert _upload(client, b"%PDF", name="a.pdf").status_code == 422
    assert _upload(client, name="../evil.vtt").status_code == 422
    assert _upload(client, language="fr").status_code == 422
    huge = _upload(client, b"x" * (settings.meeting_transcript_max_bytes + 10), name="a.txt")
    assert huge.status_code == 413
    assert client.get("/api/v1/meetings", headers=headers()).json()["total"] == 0


# --------------------------------------------------------------------------- list, search, transcript


def test_list_search_paging_and_transcript_search(client: TestClient, gateway: FakeGateway) -> None:
    ids = [_upload(client, title=f"Call {index}").json()["id"] for index in range(5)]
    other = _upload(client, "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nZed: completely different topic\n", title="Unrelated")
    page = client.get("/api/v1/meetings", params={"limit": 2}, headers=headers()).json()
    assert page["total"] == 6 and len(page["items"]) == 2 and page["items"][0]["id"] == other.json()["id"]
    second = client.get("/api/v1/meetings", params={"limit": 2, "offset": 2}, headers=headers()).json()
    assert [item["id"] for item in second["items"]] == [ids[3], ids[2]]

    by_transcript = client.get("/api/v1/meetings", params={"q": "TYRE stock"}, headers=headers()).json()
    assert by_transcript["total"] == 5
    by_title = client.get("/api/v1/meetings", params={"q": "unrelated"}, headers=headers()).json()
    assert [item["title"] for item in by_title["items"]] == ["Unrelated"]
    assert client.get("/api/v1/meetings", params={"status": "failed"}, headers=headers()).json()["total"] == 0
    # Other workspaces see nothing.
    assert client.get("/api/v1/meetings", headers=headers(workspace="workspace_other")).json()["total"] == 0
    assert client.get(f"/api/v1/meetings/{ids[0]}", headers=headers(workspace="workspace_other")).status_code == 404

    transcript = client.get(f"/api/v1/meetings/{ids[0]}/transcript", headers=headers()).json()
    assert transcript["total"] == 3 and transcript["speakers"] == ["Jane Doe", "Omar Ali"]
    assert [row["start_ms"] for row in transcript["items"]] == [1000, 5000, 10000]
    jane = client.get(f"/api/v1/meetings/{ids[0]}/transcript", params={"speaker": "Jane Doe"}, headers=headers()).json()
    assert jane["total"] == 2
    found = client.get(f"/api/v1/meetings/{ids[0]}/transcript", params={"q": "omar"}, headers=headers()).json()
    assert found["total"] == 1
    paged = client.get(f"/api/v1/meetings/{ids[0]}/transcript", params={"limit": 1, "offset": 2}, headers=headers()).json()
    assert [row["seq"] for row in paged["items"]] == [2]


# --------------------------------------------------------------------------- permissions


def test_viewers_read_but_cannot_import_and_only_importer_or_owner_edit(client: TestClient, gateway: FakeGateway) -> None:
    meeting_id = _upload(client).json()["id"]
    assert client.get(f"/api/v1/meetings/{meeting_id}", headers=VIEWER).status_code == 200
    refused = _upload(client, hdrs=VIEWER)
    assert refused.status_code == 403 and "Viewers can read meetings" in refused.json()["error"]["message"]
    setup = client.get("/api/v1/meetings/setup", headers=VIEWER).json()
    assert setup["can_create"] is False and setup["can_manage_all"] is False

    for hdrs in (VIEWER, OTHER_MEMBER):
        denied = client.patch(f"/api/v1/meetings/{meeting_id}", headers=hdrs, json={"title": "Mine now"})
        assert denied.status_code == 403 and "imported this meeting or a workspace owner" in denied.json()["error"]["message"]
        assert client.delete(f"/api/v1/meetings/{meeting_id}", headers=hdrs).status_code == 403
        assert client.post(f"/api/v1/meetings/{meeting_id}/process", headers=hdrs).status_code == 403

    assert client.patch(f"/api/v1/meetings/{meeting_id}", headers=OWNER, json={"title": "Renamed by owner"}).status_code == 200
    assert client.patch(f"/api/v1/meetings/{meeting_id}", headers=headers(), json={"title": "  PMV   call "}).json()["title"] == "PMV call"


def test_agent_link_must_be_an_installed_agent(client: TestClient, gateway: FakeGateway) -> None:
    org_admin = {**OWNER, "x-user-roles": "owner,org_admin"}
    version = {
        "skill_id": "pmv.planner", "version": "1.0.0", "name": "PMV planner", "description": "Plans PMV",
        "instructions": "Plan.", "required_tools": [], "risk_level": "low",
    }
    assert client.post("/api/v1/skills/versions", headers=org_admin, json=version).status_code == 201
    assert client.post("/api/v1/skills/installations", headers=OWNER, json={"skill_id": "pmv.planner", "version": "1.0.0", "approved_tools": []}).status_code == 201

    assert _upload(client, agent_skill_id="unknown.agent").status_code == 422
    linked = _upload(client, agent_skill_id="pmv.planner")
    assert linked.status_code == 201 and linked.json()["agent_skill_id"] == "pmv.planner"
    meeting_id = linked.json()["id"]
    cleared = client.patch(f"/api/v1/meetings/{meeting_id}", headers=headers(), json={"clear_agent": True})
    assert cleared.json()["agent_skill_id"] is None
    relinked = client.patch(f"/api/v1/meetings/{meeting_id}", headers=headers(), json={"agent_skill_id": "pmv.planner"})
    assert relinked.json()["agent_skill_id"] == "pmv.planner"


# --------------------------------------------------------------------------- action items, tasks, follow-up


def test_action_items_are_edited_and_become_tasks_once(client: TestClient, gateway: FakeGateway) -> None:
    meeting_id = _upload(client, title="PMV planning").json()["id"]
    item = client.get(f"/api/v1/meetings/{meeting_id}", headers=headers()).json()["action_items"][1]

    edited = client.patch(
        f"/api/v1/meetings/{meeting_id}/action-items/{item['id']}",
        headers=headers(),
        json={"owner": "Sara", "due_date": "2026-10-20", "text": "Confirm supplier prices for Noon"},
    )
    assert edited.status_code == 200
    changed = next(value for value in edited.json()["action_items"] if value["id"] == item["id"])
    assert (changed["owner"], changed["due_date"], changed["text"]) == ("Sara", "2026-10-20", "Confirm supplier prices for Noon")

    assert client.post(f"/api/v1/meetings/{meeting_id}/action-items/{item['id']}/task", headers=VIEWER).status_code == 403
    created = client.post(f"/api/v1/meetings/{meeting_id}/action-items/{item['id']}/task", headers=headers())
    assert created.status_code == 200, created.text
    task_id = next(value for value in created.json()["action_items"] if value["id"] == item["id"])["task_id"]
    task = client.get(f"/api/v1/tasks/{task_id}", headers=headers()).json()
    assert task["title"] == "Confirm supplier prices for Noon" and task["status"] == "created"
    assert "Owner: Sara" in task["prompt"] and "Due: 2026-10-20" in task["prompt"] and '"PMV planning"' in task["prompt"]
    again = client.post(f"/api/v1/meetings/{meeting_id}/action-items/{item['id']}/task", headers=headers())
    assert again.status_code == 409

    done = client.patch(f"/api/v1/meetings/{meeting_id}/action-items/{item['id']}", headers=headers(), json={"done": True, "clear_owner": True})
    assert next(value for value in done.json()["action_items"] if value["id"] == item["id"])["owner"] is None
    assert client.patch(f"/api/v1/meetings/{meeting_id}/action-items/act_missing", headers=headers(), json={"done": True}).status_code == 404

    # Summarizing again keeps items that became tasks and the user's edited email.
    client.put(f"/api/v1/meetings/{meeting_id}/follow-up", headers=headers(), json={"to": [], "subject": "Mine", "body": "My text"})
    assert client.post(f"/api/v1/meetings/{meeting_id}/process", headers=headers()).status_code == 202
    after = client.get(f"/api/v1/meetings/{meeting_id}", headers=headers()).json()
    assert any(value["task_id"] == task_id for value in after["action_items"])
    assert after["follow_up"]["subject"] == "Mine"


def _mailbox(user: str = "user_meet", visibility: str = "workspace") -> str:
    from anum_api.email_inbox import AccountStatus, EmailAccount, EmailProvider, Visibility, open_email_store
    from anum_api.schemas import TenantContext

    now = utc_now()
    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, roles=["member"])
    account = EmailAccount(
        id=f"emb_{user}", tenant_id=TENANT, workspace_id=WORKSPACE, provider=EmailProvider.GMAIL,
        address=f"{user}@example.com", connected_by=user, visibility=Visibility(visibility),
        status=AccountStatus.CONNECTED, created_at=now, updated_at=now,
    )
    with open_email_store(context) as store:
        store.add_account(account)
    return account.id


@pytest.fixture
def mail() -> Iterator[None]:
    from anum_api.email_inbox import email_backend

    email_backend.clear()
    yield
    email_backend.clear()


def test_follow_up_is_sent_through_an_email_reply_draft_and_approval(client: TestClient, gateway: FakeGateway, mail: None) -> None:
    meeting_id = _upload(client, title="PMV planning").json()["id"]
    bad = client.put(f"/api/v1/meetings/{meeting_id}/follow-up", headers=headers(), json={"to": ["not-an-email"], "subject": "s", "body": "b"})
    assert bad.status_code == 422
    saved = client.put(
        f"/api/v1/meetings/{meeting_id}/follow-up",
        headers=headers(),
        json={"to": ["jane@example.com", "jane@example.com", " omar@example.com "], "subject": "Recap", "body": "Thanks all."},
    )
    assert saved.status_code == 200
    follow_up = saved.json()["follow_up"]
    assert (follow_up["to"], follow_up["edited"], follow_up["approval_id"]) == (["jane@example.com", "omar@example.com"], True, None)

    # No mailbox yet: the setup offers none and sending says what to connect.
    assert client.get("/api/v1/meetings/setup", headers=headers()).json()["email_accounts"] == []
    refused = client.post(f"/api/v1/meetings/{meeting_id}/follow-up/send", headers=headers(), json={"account_id": "emb_none"})
    assert refused.status_code == 409 and "Connect a mailbox under Inbox" in refused.json()["error"]["message"]

    account_id = _mailbox()
    _mailbox("other_meet", visibility="private")
    choices = client.get("/api/v1/meetings/setup", headers=headers()).json()["email_accounts"]
    assert choices == [{"id": account_id, "address": "user_meet@example.com", "provider": "gmail"}]
    # Another member's private mailbox cannot be used.
    assert client.post(f"/api/v1/meetings/{meeting_id}/follow-up/send", headers=headers(), json={"account_id": "emb_other_meet"}).status_code == 409
    assert client.post(f"/api/v1/meetings/{meeting_id}/follow-up/send", headers=VIEWER, json={"account_id": account_id}).status_code == 403

    sent = client.post(f"/api/v1/meetings/{meeting_id}/follow-up/send", headers=headers(), json={"account_id": account_id})
    assert sent.status_code == 200, sent.text
    follow_up = sent.json()["follow_up"]
    assert follow_up["sent_from"] == "user_meet@example.com" and follow_up["email_draft_id"] and follow_up["send_task_id"]
    approval = client.get(f"/api/v1/approvals/{follow_up['approval_id']}", headers=headers()).json()
    assert approval["status"] == "pending" and approval["action"] == "send_email"
    assert approval["arguments"]["to"] == ["jane@example.com", "omar@example.com"]
    assert approval["arguments"]["body"] == "Thanks all." and approval["arguments"]["draft_id"] == follow_up["email_draft_id"]
    assert approval["arguments"]["drafted_by"] == "person"  # the user edited the AI draft
    again = client.post(f"/api/v1/meetings/{meeting_id}/follow-up/send", headers=headers(), json={"account_id": account_id})
    assert again.status_code == 409


def test_sending_needs_recipients(client: TestClient, gateway: FakeGateway, mail: None) -> None:
    account_id = _mailbox()
    meeting_id = _upload(client).json()["id"]
    response = client.post(f"/api/v1/meetings/{meeting_id}/follow-up/send", headers=headers(), json={"account_id": account_id})
    assert response.status_code == 422


def test_reprocess_refuses_while_working_unless_stale(client: TestClient, gateway: FakeGateway) -> None:
    meeting_id = _upload(client).json()["id"]
    stored = meeting_store.meetings[meeting_id]
    meeting_store.meetings[meeting_id] = stored.model_copy(update={"status": meetings.MeetingStatus.ANALYZING, "updated_at": utc_now()})
    assert client.post(f"/api/v1/meetings/{meeting_id}/process", headers=headers()).status_code == 409
    meeting_store.meetings[meeting_id] = stored.model_copy(
        update={"status": meetings.MeetingStatus.ANALYZING, "updated_at": utc_now() - timedelta(hours=1)}
    )
    assert client.post(f"/api/v1/meetings/{meeting_id}/process", headers=headers()).status_code == 202


def test_events_feed_the_activity_stream(client: TestClient, gateway: FakeGateway) -> None:
    meeting_id = _upload(client, title="PMV meeting").json()["id"]
    events = client.get("/api/v1/events", headers=headers()).json()
    mine = [event for event in events if event["subject"] == meeting_id]
    assert [event["type"] for event in mine][-2:] == ["meeting.imported", "meeting.summarized"] or {
        event["type"] for event in mine
    } >= {"meeting.imported", "meeting.summarized"}
    summarized = next(event for event in mine if event["type"] == "meeting.summarized")
    assert summarized["payload"]["title"] == "PMV meeting" and summarized["payload"]["action_items"] == 2


# --------------------------------------------------------------------------- recordings


def _whisper(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    monkeypatch.setattr(settings, "transcription_provider", "openai-compatible")
    monkeypatch.setattr(settings, "transcription_base_url", "http://whisper.local:8000/v1")
    monkeypatch.setattr(transcription, "http_transport", httpx.MockTransport(handler))


def _record(client: TestClient, content: bytes = b"ID3fakeaudio", name: str = "call.mp3", hdrs: dict[str, str] | None = None, **params: str):
    return client.post(
        "/api/v1/meetings/recordings",
        content=content,
        headers={**(hdrs or headers()), "x-file-name": name, "content-type": "audio/mpeg"},
        params=params,
    )


def test_recordings_need_transcription_setup(client: TestClient, gateway: FakeGateway) -> None:
    refused = _record(client)
    assert refused.status_code == 409 and "ANUM_TRANSCRIPTION_PROVIDER" in refused.json()["error"]["message"]
    setup = client.get("/api/v1/meetings/setup", headers=headers()).json()
    assert setup["transcription"]["configured"] is False and "Whisper" in setup["transcription"]["problem"]
    assert file_store.records == {}


def test_recording_is_stored_as_a_file_transcribed_and_deleted_with_the_meeting(
    client: TestClient, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"language": "english", "duration": 9.0, "segments": [
            {"start": 0, "end": 4, "text": "We will restock Noon UAE."}, {"start": 5, "end": 9, "text": "Omar sends the report."},
        ]})

    _whisper(monkeypatch, handler)
    assert _record(client, name="call.exe").status_code == 415
    assert _record(client, b"").status_code == 422
    accepted = _record(client, language="en")
    assert accepted.status_code == 202, accepted.text
    meeting = client.get(f"/api/v1/meetings/{accepted.json()['id']}", headers=headers()).json()
    assert meeting["status"] == "ready" and meeting["source"] == "recording" and meeting["segment_count"] == 2
    assert meeting["duration_seconds"] == 9 and meeting["language"] == "en"
    file_id = meeting["recording_file_id"]
    assert client.get(f"/api/v1/files/{file_id}", headers=headers()).json()["name"] == "call.mp3"
    assert "language" in seen[0].content.decode("latin-1")

    assert client.delete(f"/api/v1/meetings/{meeting['id']}", headers=headers()).status_code == 204
    assert client.get(f"/api/v1/meetings/{meeting['id']}", headers=headers()).status_code == 404
    assert client.get(f"/api/v1/files/{file_id}", headers=headers()).status_code == 404
    assert file_store.storage.objects == {}  # type: ignore[attr-defined]


def test_recording_limit_and_transcription_failure(client: TestClient, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch) -> None:
    _whisper(monkeypatch, lambda request: httpx.Response(401, json={}))
    monkeypatch.setattr(settings, "meeting_recording_max_bytes", 1024 * 1024)
    too_big = _record(client, b"x" * (1024 * 1024 + 1))
    assert too_big.status_code == 413 and "1 MB" in too_big.json()["error"]["message"]
    failed = client.get(f"/api/v1/meetings/{_record(client).json()['id']}", headers=headers()).json()
    assert failed["status"] == "failed" and failed["error_code"] == "transcription_failed" and "API key" in failed["error"]

    _whisper(monkeypatch, lambda request: httpx.Response(200, json={"text": "Short note.", "segments": []}))
    retried = client.post(f"/api/v1/meetings/{failed['id']}/process", headers=headers())
    assert retried.status_code == 202
    assert client.get(f"/api/v1/meetings/{failed['id']}", headers=headers()).json()["status"] == "ready"


def test_recording_deleted_from_files_fails_clearly(client: TestClient, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch) -> None:
    _whisper(monkeypatch, lambda request: httpx.Response(500, json={}))
    meeting = _record(client).json()
    assert client.delete(f"/api/v1/files/{meeting['recording_file_id']}", headers=headers()).status_code == 204
    _whisper(monkeypatch, lambda request: httpx.Response(200, json={"text": "x"}))
    client.post(f"/api/v1/meetings/{meeting['id']}/process", headers=headers())
    after = client.get(f"/api/v1/meetings/{meeting['id']}", headers=headers()).json()
    assert after["error_code"] == "recording_missing"


# --------------------------------------------------------------------------- connectors


@pytest.fixture
def zoom(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setattr(settings, "zoom_client_id", "zoom-client")
    monkeypatch.setattr(settings, "zoom_client_secret", SecretStr("example"))
    monkeypatch.setattr(settings, "meetings_oauth_redirect_uri", "https://app.example.com/meetings/connected")
    state: dict[str, Any] = {"requests": [], "token_expires_in": 3600, "refreshes": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        url = str(request.url)
        if url == "https://zoom.us/oauth/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["refresh_token"]:
                state["refreshes"] += 1
                assert form["refresh_token"] == ["refresh-one"]
                return httpx.Response(200, json={"access_token": "access-two", "refresh_token": "refresh-two", "expires_in": 3600})
            assert form["code"] == ["code-one"] and form["redirect_uri"] == ["https://app.example.com/meetings/connected"]
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={
                "access_token": "access-one", "refresh_token": "refresh-one", "expires_in": state["token_expires_in"],
                "scope": "cloud_recording:read:list_user_recordings user:read:user",
            })
        if url == "https://api.zoom.us/v2/users/me":
            return httpx.Response(200, json={"id": "zoom-user", "email": "owner@example.com"})
        if url.startswith("https://api.zoom.us/v2/users/me/recordings"):
            return httpx.Response(200, json={"meetings": [
                {"uuid": "/abc==", "topic": "PMV weekly", "start_time": "2026-10-10T08:00:00Z", "duration": 30,
                 "recording_files": [{"file_type": "MP4"}, {"file_type": "TRANSCRIPT", "download_url": "https://zoom.us/rec/download/t1"}]},
                {"uuid": "no-transcript", "topic": "Video only", "start_time": "2026-10-09T08:00:00Z", "recording_files": [{"file_type": "MP4"}]},
            ]})
        if url.startswith("https://api.zoom.us/v2/meetings/"):
            assert "%252Fabc%253D%253D" in url  # a UUID with "/" is double-encoded
            return httpx.Response(200, json={"topic": "PMV weekly", "start_time": "2026-10-10T08:00:00Z", "duration": 30,
                                             "recording_files": [{"file_type": "TRANSCRIPT", "download_url": "https://zoom.us/rec/download/t1"}]})
        if url == "https://zoom.us/rec/download/t1":
            assert request.headers["authorization"] == "Bearer " + state.get("expected_token", "access-one")
            return httpx.Response(200, text=VTT)
        return httpx.Response(404, json={})

    monkeypatch.setattr(meeting_connectors, "http_transport", httpx.MockTransport(handler))
    yield state


def _connect(client: TestClient, provider: str = "zoom", hdrs: dict[str, str] | None = None) -> httpx.Response:
    link = client.get(f"/api/v1/meetings/connectors/{provider}/authorize", headers=hdrs or headers())
    assert link.status_code == 200, link.text
    query = parse_qs(urlsplit(link.json()["authorize_url"]).query)
    return client.post("/api/v1/meetings/connectors/callback", headers=hdrs or headers(), json={"code": "code-one", "state": query["state"][0]})


def test_connectors_report_what_the_owner_must_set_up(client: TestClient) -> None:
    setup = client.get("/api/v1/meetings/setup", headers=headers()).json()
    zoom_status = next(item for item in setup["connectors"] if item["provider"] == "zoom")
    teams_status = next(item for item in setup["connectors"] if item["provider"] == "teams")
    assert zoom_status["available"] is False and "ANUM_ZOOM_CLIENT_ID" in zoom_status["problem"]
    assert "ANUM_MICROSOFT_CLIENT_ID" in teams_status["problem"]
    assert client.get("/api/v1/meetings/connectors/zoom/authorize", headers=headers()).status_code == 409
    assert client.get("/api/v1/meetings/connectors/webex/authorize", headers=headers()).status_code == 404


def test_zoom_connect_list_import_and_dedupe(client: TestClient, gateway: FakeGateway, zoom: dict[str, Any]) -> None:
    link = client.get("/api/v1/meetings/connectors/zoom/authorize", headers=headers()).json()["authorize_url"]
    assert link.startswith("https://zoom.us/oauth/authorize?") and "client_id=zoom-client" in link
    connected = _connect(client)
    assert connected.status_code == 200, connected.text
    assert connected.json()["account_label"] == "owner@example.com"
    status = next(item for item in client.get("/api/v1/meetings/setup", headers=headers()).json()["connectors"] if item["provider"] == "zoom")
    assert status["connected"] is True
    stored = connection_store.get(_ctx(), meeting_connectors.ConnectorProvider.ZOOM)
    assert stored is not None and "access-one" not in stored.token_ciphertext

    listed = client.get("/api/v1/meetings/connectors/zoom/meetings", headers=headers())
    assert listed.status_code == 200, listed.text
    assert [(item["external_id"], item["title"], item["imported_meeting_id"]) for item in listed.json()] == [("/abc==", "PMV weekly", None)]

    imported = client.post("/api/v1/meetings/connectors/zoom/import", headers=headers(), json={"external_id": "/abc==", "language": "auto"})
    assert imported.status_code == 201, imported.text
    meeting = client.get(f"/api/v1/meetings/{imported.json()['id']}", headers=headers()).json()
    assert meeting["source"] == "zoom" and meeting["title"] == "PMV weekly" and meeting["status"] == "ready"
    assert meeting["duration_seconds"] == 1800 and meeting["occurred_at"].startswith("2026-10-10")

    again = client.post("/api/v1/meetings/connectors/zoom/import", headers=headers(), json={"external_id": "/abc=="})
    assert again.status_code == 409
    relisted = client.get("/api/v1/meetings/connectors/zoom/meetings", headers=headers()).json()
    assert relisted[0]["imported_meeting_id"] == meeting["id"]

    # Another member of the workspace has no connection of their own.
    assert client.get("/api/v1/meetings/connectors/zoom/meetings", headers=OTHER_MEMBER).status_code == 409

    assert client.delete("/api/v1/meetings/connectors/zoom", headers=headers()).status_code == 204
    assert client.delete("/api/v1/meetings/connectors/zoom", headers=headers()).status_code == 404


def _ctx() -> Any:
    from anum_api.schemas import TenantContext

    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="user_meet", roles=["member"])


def test_oauth_state_is_bound_to_the_user_who_started_it(client: TestClient, zoom: dict[str, Any]) -> None:
    link = client.get("/api/v1/meetings/connectors/zoom/authorize", headers=headers()).json()["authorize_url"]
    state = parse_qs(urlsplit(link).query)["state"][0]
    stolen = client.post("/api/v1/meetings/connectors/callback", headers=OTHER_MEMBER, json={"code": "code-one", "state": state})
    assert stolen.status_code == 403 and "another user" in stolen.json()["error"]["message"]
    forged = client.post("/api/v1/meetings/connectors/callback", headers=headers(), json={"code": "code-one", "state": "forged"})
    assert forged.status_code == 400
    assert connection_store.items == {}
    viewer = client.get("/api/v1/meetings/connectors/zoom/authorize", headers=VIEWER)
    assert viewer.status_code == 403


def test_expired_tokens_are_refreshed_and_saved(client: TestClient, gateway: FakeGateway, zoom: dict[str, Any]) -> None:
    zoom["token_expires_in"] = 30  # inside the 60-second refresh margin
    assert _connect(client).status_code == 200
    zoom["expected_token"] = "access-two"
    imported = client.post("/api/v1/meetings/connectors/zoom/import", headers=headers(), json={"external_id": "/abc=="})
    assert imported.status_code == 201, imported.text
    assert zoom["refreshes"] == 1


def test_a_revoked_connection_asks_to_reconnect(client: TestClient, zoom: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    assert _connect(client).status_code == 200
    monkeypatch.setattr(meeting_connectors, "http_transport", httpx.MockTransport(lambda request: httpx.Response(401, json={})))
    refused = client.get("/api/v1/meetings/connectors/zoom/meetings", headers=headers())
    assert refused.status_code == 409 and "Connect again" in refused.json()["error"]["message"]


def test_connector_ids_and_download_hosts_are_checked(client: TestClient, gateway: FakeGateway, zoom: dict[str, Any]) -> None:
    # CodeQL py/partial-ssrf: user ids never shape the request path, foreign hosts are never fetched.
    assert _connect(client).status_code == 200
    for bad in ("../x?y", "a b", "x" * 300):
        refused = client.post("/api/v1/meetings/connectors/zoom/import", headers=headers(), json={"external_id": bad})
        assert refused.status_code == 422, refused.text
    assert meeting_connectors._zoom_download_url("https://evil.example/rec") is None
    assert meeting_connectors._zoom_download_url("https://zoom.us.evil.example/rec") is None
    assert meeting_connectors._zoom_download_url("http://zoom.us/rec") is None
    assert meeting_connectors._zoom_download_url("https://user:pw@zoom.us/rec") is None
    assert meeting_connectors._zoom_download_url("https://us02web.zoom.us/rec/download/t1?a=1") == "https://us02web.zoom.us/rec/download/t1?a=1"
    assert meeting_connectors._graph_next_params("https://evil.example/v1.0/x?$skiptoken=a") is None
    assert meeting_connectors._graph_next_params("https://graph.microsoft.com/v1.0/x?$skiptoken=a&b=c") == {"$skiptoken": "a"}
    hostile = [request for request in zoom["requests"] if request.url.host not in ("zoom.us", "api.zoom.us")]
    assert hostile == []


@pytest.fixture
def teams(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    monkeypatch.setattr(settings, "microsoft_client_id", "ms-client")
    monkeypatch.setattr(settings, "microsoft_client_secret", SecretStr("example"))
    monkeypatch.setattr(settings, "meetings_oauth_redirect_uri", "https://app.example.com/meetings/connected")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if url == "https://login.microsoftonline.com/organizations/oauth2/v2.0/token":
            form = parse_qs(request.content.decode())
            assert form["client_id"] == ["ms-client"] and "OnlineMeetingTranscript.Read.All" in form["scope"][0]
            return httpx.Response(200, json={"access_token": "ms-access", "refresh_token": "ms-refresh", "expires_in": 3600})
        if url.startswith("https://graph.microsoft.com/v1.0/me?"):
            return httpx.Response(200, json={"id": "00000000-0000-4000-8000-000000000001", "mail": "owner@example.com"})
        if "getAllTranscripts" in url:
            assert "meetingOrganizerUserId='00000000-0000-4000-8000-000000000001'" in request.url.path or "00000000-0000-4000-8000-000000000001" in url
            return httpx.Response(200, json={"value": [
                {"id": "tr1", "meetingId": "m1", "createdDateTime": "2026-10-10T10:30:00Z"},
            ]})
        if url.startswith("https://graph.microsoft.com/v1.0/users/00000000-0000-4000-8000-000000000001/onlineMeetings/m1?"):
            return httpx.Response(200, json={"subject": "Noon UAE review", "startDateTime": "2026-10-10T10:00:00Z", "endDateTime": "2026-10-10T10:45:00Z"})
        if url.startswith("https://graph.microsoft.com/v1.0/users/00000000-0000-4000-8000-000000000001/onlineMeetings/m1/transcripts/tr1/content"):
            assert request.url.params["$format"] == "text/vtt"
            return httpx.Response(200, text=VTT)
        return httpx.Response(404, json={})

    monkeypatch.setattr(meeting_connectors, "http_transport", httpx.MockTransport(handler))
    yield seen


def test_teams_connect_list_and_import(client: TestClient, gateway: FakeGateway, teams: list[httpx.Request]) -> None:
    link = client.get("/api/v1/meetings/connectors/teams/authorize", headers=headers()).json()["authorize_url"]
    assert link.startswith("https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize?")
    assert "offline_access" in parse_qs(urlsplit(link).query)["scope"][0]
    assert _connect(client, "teams").status_code == 200
    listed = client.get("/api/v1/meetings/connectors/teams/meetings", headers=headers())
    assert listed.status_code == 200, listed.text
    assert [(item["external_id"], item["title"], item["duration_minutes"]) for item in listed.json()] == [("m1::tr1", "Noon UAE review", 45)]
    imported = client.post("/api/v1/meetings/connectors/teams/import", headers=headers(), json={"external_id": "m1::tr1", "language": "en"})
    assert imported.status_code == 201, imported.text
    meeting = client.get(f"/api/v1/meetings/{imported.json()['id']}", headers=headers()).json()
    assert meeting["source"] == "teams" and meeting["title"] == "Noon UAE review" and meeting["duration_seconds"] == 2700
    assert meeting["status"] == "ready"


# --------------------------------------------------------------------------- privacy


def test_privacy_export_and_delete_include_meetings_recordings_and_connections(
    client: TestClient, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch, zoom: dict[str, Any]
) -> None:
    _whisper(monkeypatch, lambda request: httpx.Response(200, json={"text": "Hello team."}))
    mine = _record(client).json()
    _upload(client)
    others = _upload(client, hdrs=OTHER_MEMBER).json()
    assert _connect(client).status_code == 200

    summary = client.get("/api/v1/privacy", headers=headers()).json()["private"]
    assert (summary["meetings"], summary["meeting_recordings"], summary["meeting_connections"]) == (2, 1, 1)
    assert summary["meeting_transcript_segments"] == 4

    export = client.get("/api/v1/privacy/export", headers=headers())
    body = export.json()
    assert len(body["meetings_imported"]) == 2
    recorded = next(item for item in body["meetings_imported"] if item["id"] == mine["id"])
    assert recorded["recording"]["download_path"] == f"/api/v1/files/{mine['recording_file_id']}/content"
    assert recorded["transcript"][0]["text"] == "Hello team."
    assert body["meeting_connections"][0]["account_label"] == "owner@example.com"
    assert "token_ciphertext" not in export.text and "access-one" not in export.text

    deleted = client.post("/api/v1/privacy/delete", headers=headers(), json={"confirm": "DELETE MY DATA"}).json()["deleted"]
    assert (deleted["meetings"], deleted["meeting_recordings"], deleted["meeting_connections"]) == (2, 1, 1)
    assert client.get("/api/v1/meetings", headers=headers()).json()["total"] == 1  # the other member's
    assert client.get(f"/api/v1/meetings/{others['id']}", headers=headers()).status_code == 200
    assert client.get(f"/api/v1/files/{mine['recording_file_id']}", headers=headers()).status_code == 404
    assert file_store.storage.objects == {}  # type: ignore[attr-defined]
    assert connection_store.items == {}
