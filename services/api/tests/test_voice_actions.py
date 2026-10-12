"""The voice assistant acts (docs/voice.md#actions), memory backend.

A spoken request for work goes through the same governed path as the Command Center and
the Tasks view: tasks are created like ``POST /tasks`` and started with the body of
``POST /tasks/{id}/run`` (ToolPolicy, governance, autonomy, approvals); cancel, retry and
quick commands reuse their routes' bodies. Lookups stay in the caller's tenant,
workspace and user scope, and voice never decides an approval.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main, voice_actions
from anum_api.agent_profiles import agent_store
from anum_api.chat_tools import ChatCardApproval
from anum_api.commands import memory_command_store
from anum_api.store import store
from anum_api.voice import VoiceAskResult, voice_store
from anum_api.voice_actions import ActionKind, VoiceActionCard, parse_action
from anum_api.voice_assistant import WorkspaceSnapshot, voice_rules

TENANT, WORKSPACE = "tenant_voice_actions", "workspace_voice_actions"


def headers(user: str = "owner_voice", role: str = "owner", workspace: str = WORKSPACE, tenant: str = TENANT) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER = headers()
MEMBER = headers("member_voice", "member")
VIEWER = headers("viewer_voice", "viewer")
OTHER_TENANT = headers("owner_elsewhere", "owner", "workspace_elsewhere", "tenant_elsewhere")

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Any:
    def reset() -> None:
        voice_store.clear()
        agent_store.clear()
        memory_command_store.clear()
        main.app.dependency_overrides.clear()
        for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
            collection.clear()

    reset()
    yield
    reset()


def create_agent(name: str, h: dict[str, str] = OWNER, **fields: Any) -> dict[str, Any]:
    response = client.post("/api/v1/agents", headers=h, json={"name": name, "sharing": "workspace", **fields})
    assert response.status_code == 201, response.text
    return response.json()


def create_task(title: str, h: dict[str, str] = OWNER) -> dict[str, Any]:
    response = client.post("/api/v1/tasks", headers=h, json={"title": title, "prompt": title})
    assert response.status_code == 201, response.text
    return response.json()


def say(
    text: str, h: dict[str, str] = OWNER, locale: str = "en-US", as_headers: dict[str, str] | None = None
) -> list[tuple[str, dict[str, Any]]]:
    session = client.post("/api/v1/voice/sessions", headers=h, json={"locale": locale})
    assert session.status_code == 201, session.text
    session_id = session.json()["id"]
    segment = client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript", headers=h,
        json={"text": text, "is_final": True, "client_sequence": 0},
    ).json()
    response = client.post(
        f"/api/v1/voice/sessions/{session_id}/ask/stream", headers=as_headers or h,
        json={"transcript_segment_id": segment["id"]},
    )
    assert response.status_code == 200, response.text
    frames = []
    for frame in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in frame.splitlines())
        frames.append((fields["event"], json.loads(fields["data"])))
    return frames


def cards(frames: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [data["card"] for kind, data in frames if kind == "action"]


def spoken(frames: list[tuple[str, dict[str, Any]]]) -> str:
    return "".join(data["text"] for kind, data in frames if kind == "delta")


# -- understanding the request (pure) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind", "field", "value"),
    [
        ("Create a task to draft the weekly report", ActionKind.CREATE_TASK, "work", "draft the weekly report"),
        ("Hey, please create a new task: book the review", ActionKind.CREATE_TASK, "work", "book the review"),
        ("Pass a task to the Sales agent: follow up with yesterday's leads", ActionKind.ASSIGN, "rest", "a task to the Sales agent: follow up with yesterday's leads"),
        ("ask the sales agent to call Omar", ActionKind.ASSIGN, "rest", "the sales agent to call Omar"),
        ("cancel the weekly report task", ActionKind.CANCEL_TASK, "name", "weekly report"),
        ("start the weekly report task", ActionKind.RUN_TASK, "name", "weekly report"),
        ("run my morning check quick command", ActionKind.RUN_QUICK_COMMAND, "name", "morning check"),
        ("what's the status of the weekly report task?", ActionKind.TASK_STATUS, "name", "weekly report"),
        ("is the weekly report task done?", ActionKind.TASK_STATUS, "name", "weekly report"),
        ("ما حالة مهمة التقرير الأسبوعي", ActionKind.TASK_STATUS, "name", "التقرير الأسبوعي"),
    ],
)
def test_requests_for_work_are_read_from_the_users_own_words(text: str, kind: ActionKind, field: str, value: str) -> None:
    parsed = parse_action(text)
    assert parsed is not None and parsed.kind == kind
    assert getattr(parsed, field) == value


@pytest.mark.parametrize(
    "text",
    ["What should I focus on today?", "tell me a joke", "stop it", "how is the weather", "what's the status of my workspace", "x" * 5000],
)
def test_questions_and_small_talk_are_not_actions(text: str) -> None:
    assert parse_action(text) is None


def test_the_prompt_never_lets_the_model_claim_it_cannot_act() -> None:
    rules = voice_rules("Anum", WorkspaceSnapshot(tasks_total=0, running=0, waiting_approval=0, pending_approvals=0))
    assert "never say you cannot assign work, run tasks or take actions" in rules
    assert "ask the Sales agent to" in rules and "quick command" in rules
    assert "Approving or rejecting approvals" in rules


# -- assigning work to an agent ---------------------------------------------------------------


def test_asking_an_agent_by_name_creates_its_task_and_runs_it_through_the_runtime() -> None:
    agent = create_agent("Sales agent")
    frames = say("Anum, pass a task to the Sales agent: follow up with yesterday's leads")

    kinds = [kind for kind, _ in frames]
    assert kinds[0] == "meta" and kinds[-1] == "done" and kinds.count("action") == 2
    assert frames[0][1]["intent"] == "action" and frames[0][1]["action_kind"] == "assign"
    first, final = cards(frames)
    assert first["state"] == "running" and first["agent"] == {"id": agent["id"], "name": "Sales agent"}
    assert final["task_id"] == first["task_id"] and final["state"] == "completed"
    task = store.tasks[final["task_id"]]
    assert task.agent_id == agent["id"] and task.created_by == "owner_voice"
    assert task.title == "Follow up with yesterday's leads" and task.status.value == "completed"
    # Planned and executed by the runtime, as POST /tasks/{id}/run does (one run, with its steps).
    run = next(run for run in store.runs.values() if run.task_id == task.id)
    assert run.checkpoint.tool_call and run.checkpoint.tool_call["name"] == "anum.respond"
    reply = spoken(frames)
    assert reply.startswith('Created task "Follow up with yesterday\'s leads" and assigned it to Sales agent. It\'s done')
    stored = frames[-1][1]["assistant_segment"]
    assert stored["role"] == "assistant" and stored["text"] == reply.strip()
    # The task is an ordinary workspace task with a task.created event marked as voice.
    created = [e for e in store.events if e.subject == task.id and e.type == "task.created"]
    assert created and created[0].payload["source"] == "voice"


def test_an_agent_in_another_workspace_or_private_to_someone_else_is_not_found() -> None:
    create_agent("Sales agent", OTHER_TENANT)
    create_agent("Sales agent", MEMBER, sharing="private")
    create_agent("Fleet agent")

    final = cards(say("ask the Sales agent to call Omar"))[-1]
    assert final["state"] == "not_found" and final["error_code"] == "agent_not_found"
    assert final["candidates"] == ["Fleet agent"]
    assert store.tasks == {}


def test_a_paused_agent_is_not_given_work() -> None:
    agent = create_agent("Sales agent")
    assert client.patch(f"/api/v1/agents/{agent['id']}", headers=OWNER, json={"status": "paused"}).status_code == 200
    final = cards(say("ask the Sales agent to call Omar"))[-1]
    assert final["state"] == "failed" and final["error_code"] == "agent_paused"
    assert store.tasks == {}


def test_a_viewer_is_refused_and_nothing_is_created() -> None:
    # Viewers cannot open a voice session; a member made viewer while talking keeps the
    # session (it is theirs) but every action is checked against the role they have now.
    create_agent("Sales agent")
    demoted = headers("member_voice", "viewer")
    frames = say("ask the Sales agent to call Omar", MEMBER, as_headers=demoted)
    final = cards(frames)[-1]
    assert final["state"] == "denied" and final["error_code"] == "forbidden"
    assert "member role" in spoken(frames)
    assert store.tasks == {}


# -- approvals ---------------------------------------------------------------------------------


def test_a_risky_request_waits_for_an_approval_in_the_normal_places() -> None:
    frames = say("Send the quarterly report to the bank")
    assert frames[0][1]["intent"] == "action" and frames[0][1]["risk_tier"] == "action"
    final = cards(frames)[-1]
    assert final["state"] == "approval" and final["approvals_pending"] == 1
    approval = final["approval"]
    assert approval["action"] == "external.action" and approval["status"] == "pending"
    assert len(approval["payload_hash"]) == 64
    assert "I've sent it to Approvals" in spoken(frames)
    # The ordinary approvals list shows it, and voice approved nothing.
    listed = client.get("/api/v1/approvals", headers=OWNER).json()
    assert [item["id"] for item in listed if item["status"] == "pending"] == [approval["id"]]
    assert store.tasks[final["task_id"]].status.value == "waiting_approval"
    # Approving still needs the hash, through the approvals route.
    decided = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]})
    assert decided.status_code == 200, decided.text


def test_approval_decisions_stay_on_screen_even_inside_a_request() -> None:
    create_agent("Finance agent")
    for text in ("Approve all pending actions", "ask the Finance agent to approve the invoices", "delete the finance workspace"):
        frames = say(text)
        assert frames[0][1]["risk_tier"] == "visual_only", text
        assert cards(frames) == []
    assert store.tasks == {} and store.approvals == {}


# -- tasks by name -----------------------------------------------------------------------------


def test_create_start_status_and_cancel_by_name() -> None:
    frames = say("remind me to call the bank")
    created = cards(frames)[-1]
    assert created["state"] == "created" and created["title"] == "Call the bank"
    assert store.tasks[created["task_id"]].status.value == "created"

    status = cards(say("what's the status of the call the bank task?"))[-1]
    assert status["state"] == "status" and status["task_id"] == created["task_id"] and status["task_status"] == "created"

    started = cards(say("start the call the bank task"))[-1]
    assert started["task_id"] == created["task_id"] and started["state"] == "completed"

    other = create_task("Weekly report")
    member = cards(say("cancel the weekly report task", MEMBER))[-1]
    assert member["state"] == "denied" and store.tasks[other["id"]].status.value == "created"
    cancelled = say("cancel the weekly report task")
    assert cards(cancelled)[-1]["state"] == "cancelled"
    assert store.tasks[other["id"]].status.value == "cancelled"
    assert spoken(cancelled).startswith('Cancelled task "Weekly report"')


def test_ambiguous_and_missing_task_names_ask_instead_of_acting() -> None:
    create_task("Weekly report")
    create_task("Weekly report for sales")
    create_task("Weekly report", OTHER_TENANT)

    ambiguous = cards(say("cancel the weekly task"))[-1]
    assert ambiguous["state"] == "ambiguous" and set(ambiguous["candidates"]) == {"Weekly report", "Weekly report for sales"}
    missing = cards(say("cancel the payroll task"))[-1]
    assert missing["state"] == "not_found"
    assert all(task.status.value == "created" for task in store.tasks.values())
    # Exact title wins over a longer one.
    exact = cards(say("what's the status of the weekly report task"))[-1]
    assert exact["state"] == "status" and exact["title"] == "Weekly report"


def test_a_quick_command_runs_as_a_command_through_the_command_path() -> None:
    quick = client.post(
        "/api/v1/quick-commands", headers=OWNER,
        json={"title": "Morning check", "text": "Summarize what's due today, then send the summary to the bank"},
    )
    assert quick.status_code == 201, quick.text
    frames = say("run my morning check quick command")
    final = cards(frames)[-1]
    assert final["kind"] == "run_quick_command" and final["command_id"] and final["steps"] >= 1
    commands = client.get("/api/v1/commands", headers=OWNER).json()
    assert [c["id"] for c in commands] == [final["command_id"]] and commands[0]["source"] == "voice"
    assert client.get("/api/v1/quick-commands", headers=OWNER).json()[0]["use_count"] == 1
    assert 'Started your quick command "Morning check"' in spoken(frames)


def test_arabic_requests_get_arabic_replies() -> None:
    create_agent("وكيل المبيعات")
    frames = say("اطلب من وكيل المبيعات أن يتابع العملاء", locale="ar-SA")
    final = cards(frames)[-1]
    assert final["agent"]["name"] == "وكيل المبيعات" and final["state"] == "completed"
    assert spoken(frames).startswith("أنشأت المهمة")


def test_the_non_streamed_ask_acts_the_same_way() -> None:
    create_agent("Sales agent")
    session_id = client.post("/api/v1/voice/sessions", headers=OWNER, json={}).json()["id"]
    segment = client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript", headers=OWNER,
        json={"text": "ask the Sales agent to call Omar", "is_final": True, "client_sequence": 0},
    ).json()
    body = client.post(f"/api/v1/voice/sessions/{session_id}/ask", headers=OWNER, json={"transcript_segment_id": segment["id"]}).json()
    result = VoiceAskResult.model_validate(body)
    assert result.action_kind == ActionKind.ASSIGN and result.action is not None
    assert result.action.state == "completed" and result.reply.startswith('Created task "Call Omar"')


# -- the wire contract ---------------------------------------------------------------------------


def _ts_fields(source: str, interface: str) -> set[str]:
    body = re.search(rf"export interface {interface} \{{(.*?)\n\}}", source, re.S)
    assert body, interface
    return set(re.findall(r"^\s+(\w+)\??:", body.group(1), re.M))


def test_the_typescript_contract_matches_the_card() -> None:
    source = (Path(__file__).resolve().parents[3] / "packages" / "contracts" / "src" / "voice.ts").read_text()
    # Wire shapes are snake_case, as the stream sends them.
    assert _ts_fields(source, "VoiceActionCard") == set(VoiceActionCard.model_fields)
    assert _ts_fields(source, "VoiceActionApproval") == set(ChatCardApproval.model_fields)
    kinds = set(re.findall(r"'(\w+)'", re.search(r"export type VoiceActionKind =([^;]*);", source).group(1)))  # type: ignore[union-attr]
    assert kinds == {kind.value for kind in ActionKind}
    states = set(re.findall(r"'(\w+)'", re.search(r"export type VoiceActionState =([^;]*);", source).group(1)))  # type: ignore[union-attr]
    assert states == set(VoiceActionCard.model_fields["state"].annotation.__args__)  # type: ignore[union-attr]
    assert voice_actions.configured() is not None


def test_a_teammate_is_assigned_through_the_runtimes_team_tool() -> None:
    frames = say("ask Ahmed to call the supplier by Thursday")
    final = cards(frames)[-1]
    task = store.tasks[final["task_id"]]
    # The user's own sentence is the task; the runtime planned it as team.assign_task.
    assert task.prompt == "ask Ahmed to call the supplier by Thursday" and task.agent_id is None
    run = next(run for run in store.runs.values() if run.task_id == task.id)
    assert run.checkpoint.tool_call and run.checkpoint.tool_call["name"] == "team.assign_task"
    # Nobody is called Ahmed in this workspace: the run fails and says why.
    assert final["state"] == "failed" and "Ahmed" in final["summary"]
    assert "It failed" in spoken(frames)
