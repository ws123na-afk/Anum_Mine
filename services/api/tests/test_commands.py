"""ANUM Command (docs/command.md) against the in-memory backend: plan preview, command
records, the live view, approvals, report, next steps, suggestions and quick commands."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from anum_api.agent_skills import default_skill_registry
from anum_api.agent_tools import ToolPolicy, ToolPolicyDecision, ToolPolicyOutcome
from anum_api.commands import (
    MAX_QUICK_COMMANDS,
    CommandStatus,
    derive_status,
    language_of,
    memory_command_store,
    split_request,
)
from anum_api.main import app, store
from anum_api.schemas import RiskLevel

client = TestClient(app)
owner = {
    "x-tenant-id": "tenant_a",
    "x-workspace-id": "workspace_a",
    "x-user-id": "user_owner",
    "x-user-roles": "owner,member",
}
member = {**owner, "x-user-id": "user_member", "x-user-roles": "member"}
viewer = {**owner, "x-user-id": "user_viewer", "x-user-roles": "viewer"}
other_tenant = {**owner, "x-tenant-id": "tenant_b", "x-workspace-id": "workspace_b"}

MOCKUP = (
    "Check my emails, find today's urgent items, prepare a summary, check PMV tyre report, identify high cost "
    "vehicles, and schedule a meeting with the team tomorrow at 10 a.m. Then send the summary to the team."
)

NO_TYRE = (
    "Check my emails, prepare a summary, and schedule a meeting with the team tomorrow at 10 a.m. "
    "Then send the summary to the team."
)


def setup_function() -> None:
    store.tasks.clear()
    store.runs.clear()
    store.approvals.clear()
    store.events.clear()
    store.audit_records.clear()
    memory_command_store.clear()


def _preview(text: str, headers: dict[str, str] = owner, **extra: object) -> dict:
    response = client.post("/api/v1/commands/preview", headers=headers, json={"text": text, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _create(text: str = MOCKUP, headers: dict[str, str] = owner, *, independent: bool = False, **extra: object) -> dict:
    """As the clients do: the confirmed steps with the dependencies the preview showed. With
    ``independent`` every step is declared to wait for nothing (``depends_on: []``), so a test
    about something else can run the MOCKUP's send step although its tyre step fails."""
    steps = [
        {"instruction": step["instruction"], "depends_on": [] if independent else step["depends_on"]}
        for step in _preview(text, headers)["steps"]
    ]
    response = client.post("/api/v1/commands", headers=headers, json={"text": text, "steps": steps, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _run_all(command: dict, headers: dict[str, str] = owner) -> None:
    for step in command["steps"]:
        response = client.post(f"/api/v1/tasks/{step['task_id']}/run", headers=headers)
        assert response.status_code == 200, response.text


def _view(command_id: str, headers: dict[str, str] = owner) -> dict:
    response = client.get(f"/api/v1/commands/{command_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# --- splitting and planning ---------------------------------------------------------------


def test_split_request_keeps_times_and_numbers_and_splits_on_new_verbs() -> None:
    assert split_request(MOCKUP) == [
        "Check my emails",
        "find today's urgent items",
        "prepare a summary",
        "check PMV tyre report",
        "identify high cost vehicles",
        "schedule a meeting with the team tomorrow at 10 a.m",
        "send the summary to the team",
    ]
    assert split_request("Order 10,000 tyres and send the invoice") == ["Order 10,000 tyres", "send the invoice"]
    # "and" between nouns is not a new step.
    assert split_request("Find high cost vehicles and issues") == ["Find high cost vehicles and issues"]
    assert split_request("تحقق من بريدي وجهز ملخصا، ثم أرسل التقرير") == ["تحقق من بريدي", "جهز ملخصا", "أرسل التقرير"]
    assert language_of("تحقق من بريدي") == "ar" and language_of("Check PMV tyres") == "en"


def test_preview_groups_clauses_into_agent_steps_with_the_runtime_tools_and_approvals() -> None:
    plan = _preview(MOCKUP)
    assert [(step["agent"]["id"], step["tool"]["name"], step["approval"]) for step in plan["steps"]] == [
        ("email", "search_email", "none"),
        ("tyre", "tyre_fleet_summary", "none"),
        ("calendar", "anum.respond", "none"),
        ("action", "external.action", "required"),
    ]
    first, tyre, calendar, send = plan["steps"]
    assert first["instruction"] == "Check my emails, find today's urgent items, prepare a summary"
    assert [skill["id"] for skill in first["skills"]] == ["anum.document-drafting", "anum.email"]
    # The tyre tools exist but no TyrePulse connection is saved in this workspace.
    assert tyre["connected"] is False and "Connect TyrePulse under Tyre planning" in tyre["note"]
    assert calendar["connected"] is False and "calendar" in calendar["note"]
    assert send["risk_level"] == "high" and send["approval_reason"]
    assert plan["approvals_required"] == 1 and plan["blocked"] == 0
    assert [agent["id"] for agent in plan["agents"]] == ["email", "tyre", "calendar", "action"]
    # Read-only: nothing was created and no audit record written.
    assert store.tasks == {} and store.audit_records == []


def test_a_saved_tyre_connection_makes_the_tyre_agent_connected() -> None:
    from anum_api.schemas import TenantContext, utc_now
    from anum_api.tyre.model import SourceKind
    from anum_api.tyre.store import TyreConnection, memory_tyre_store

    now = utc_now()
    memory_tyre_store.save_connection(
        TyreConnection(
            tenant_id="tenant_a", workspace_id="workspace_a", kind=SourceKind.SUPABASE_REST,
            endpoint="https://example.supabase.co", created_by="user_owner", updated_by="user_owner",
            created_at=now, updated_at=now,
        )
    )
    try:
        (step,) = _preview("Check the PMV tyre report")["steps"]
        assert step["agent"]["id"] == "tyre" and step["tool"]["name"] == "tyre_fleet_summary"
        assert step["connected"] is True and step["note"] is None
        # Another workspace has no connection.
        other = {**owner, "x-workspace-id": "workspace_other"}
        (elsewhere,) = _preview("Check the PMV tyre report", other)["steps"]
        assert elsewhere["connected"] is False
    finally:
        memory_tyre_store.delete_connection(
            TenantContext(tenant_id="tenant_a", workspace_id="workspace_a", user_id="user_owner")
        )


def test_preview_in_arabic_names_agents_in_arabic() -> None:
    plan = _preview("تحقق من بريدي وجهز ملخصا، ثم أرسل التقرير إلى الفريق")
    assert plan["language"] == "ar"
    assert [(step["agent"]["id"], step["approval"]) for step in plan["steps"]] == [
        ("email", "none"),
        ("action", "required"),
    ]
    assert plan["steps"][0]["agent"]["name"] == "وكيل البريد"


def test_preview_and_runtime_agree_on_which_steps_pause_for_approval() -> None:
    command = _create(independent=True)
    _run_all(command)
    view = _view(command["id"])
    for step in view["steps"]:
        # A tyre step without a saved connection fails, as its plan warned.
        expected = (
            "waiting_approval"
            if step["approval"] == "required"
            else "failed"
            if step["agent"]["id"] == "tyre"
            else "completed"
        )
        assert step["status"] == expected, step


def test_a_policy_block_is_reflected_in_the_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    original = ToolPolicy.evaluate

    def stricter(self, call, definition, context, workspace_policy=None, governance=None):  # type: ignore[no-untyped-def]
        if call.name == "search_email":
            return ToolPolicyDecision(
                outcome=ToolPolicyOutcome.BLOCK, reason="An organization policy pack denies this action.",
                risk_level=RiskLevel.BLOCKED,
            )
        return original(self, call, definition, context, workspace_policy, governance)

    monkeypatch.setattr(ToolPolicy, "evaluate", stricter)
    plan = _preview("Check my emails")
    assert plan["steps"][0]["approval"] == "blocked" and plan["blocked"] == 1
    # A blocked step cannot become a task.
    response = client.post(
        "/api/v1/commands", headers=owner, json={"text": "Check my emails", "steps": [{"instruction": "Check my emails"}]}
    )
    assert response.status_code == 422 and "blocked by policy" in response.text
    assert store.tasks == {}


def test_preview_and_create_need_task_create() -> None:
    assert client.post("/api/v1/commands/preview", headers=viewer, json={"text": "Check my emails"}).status_code == 403
    denied = client.post(
        "/api/v1/commands", headers=viewer, json={"text": "Check", "steps": [{"instruction": "Check my emails"}]}
    )
    assert denied.status_code == 403
    assert client.post("/api/v1/commands/preview", headers=owner, json={"text": "   "}).status_code == 422


# --- creating and following a command -----------------------------------------------------


def test_create_makes_one_task_per_confirmed_step_in_the_confirmed_order() -> None:
    plan = _preview(MOCKUP)
    # Edited: the tyre step removed and the send step moved first.
    steps = [plan["steps"][3], plan["steps"][0], plan["steps"][2]]
    response = client.post(
        "/api/v1/commands",
        headers=member,
        json={"text": MOCKUP, "steps": [{"instruction": step["instruction"]} for step in steps], "source": "voice"},
    )
    assert response.status_code == 201, response.text
    command = response.json()
    assert command["status"] == "ready" and command["source"] == "voice" and command["step_count"] == 3
    assert [step["agent"]["id"] for step in command["steps"]] == ["action", "email", "calendar"]
    assert [step["position"] for step in command["steps"]] == [1, 2, 3]
    # The server plans each step again: the send step needs approval whatever the client says.
    assert command["steps"][0]["approval"] == "required"
    for step in command["steps"]:
        task = store.tasks[step["task_id"]]
        assert task.prompt == step["instruction"] and task.created_by == "user_member"
        assert task.status == "created"
    created = [event for event in store.events if event.type == "task.created"]
    assert {event.payload["command_id"] for event in created} == {command["id"]}
    audit = [record for record in store.audit_records if record.action == "command.created"]
    assert len(audit) == 1 and audit[0].metadata["steps"] == 3 and audit[0].metadata["approvals_required"] == 1
    # Counts and kinds only: the request text is never audited.
    assert "emails" not in str(audit[0].metadata)


def test_live_view_approve_all_and_completion() -> None:
    command = _create(NO_TYRE)
    _run_all(command)
    view = _view(command["id"])
    assert view["status"] == "waiting_approval" and 0 < view["progress"] < 1
    assert view["completed_steps"] == 2 and view["pending_approvals"] == 1
    (pending,) = view["pending"]
    assert len(pending["payload_hash"]) == 64
    # Timeline: the tasks' events, oldest first, each tied to its step and agent.
    times = [entry["at"] for entry in view["timeline"]]
    assert times == sorted(times) and {entry["position"] for entry in view["timeline"]} == {1, 2, 3}
    assert {"task.created", "agent_run.completed", "approval.requested"} <= {entry["type"] for entry in view["timeline"]}
    assert [item["kind"] for item in view["files"]] == ["run_log"] * 3
    assert view["files"][0]["path"].endswith("/export?format=markdown")
    # Approve all: one approval at a time, each with the hash the client displayed.
    approved = client.post(
        f"/api/v1/approvals/{pending['id']}/approve", headers=owner, json={"payload_hash": pending["payload_hash"]}
    )
    assert approved.status_code == 200, approved.text
    done = _view(command["id"])
    assert done["status"] == "completed" and done["progress"] == 1 and done["pending"] == []
    assert done["finished_at"] and done["duration_seconds"] is not None
    assert all(step["result"] for step in done["steps"])


def test_stop_cancels_the_command_tasks_through_bulk_cancel() -> None:
    command = _create("Send the weekly update to the team")
    client.post(f"/api/v1/tasks/{command['steps'][0]['task_id']}/run", headers=owner)
    stopped = client.post(
        "/api/v1/tasks/bulk",
        headers=owner,
        json={"action": "cancel", "task_ids": [step["task_id"] for step in command["steps"]]},
    )
    assert stopped.status_code == 200 and stopped.json()["succeeded"] == 1
    view = _view(command["id"])
    assert view["status"] == "cancelled" and view["pending"] == []


def test_commands_are_private_to_their_user_and_tenant() -> None:
    command = _create()
    assert client.get(f"/api/v1/commands/{command['id']}", headers=member).status_code == 404
    assert client.get(f"/api/v1/commands/{command['id']}", headers=other_tenant).status_code == 404
    assert client.get("/api/v1/commands", headers=member).json() == []
    assert client.delete(f"/api/v1/commands/{command['id']}", headers=member).status_code == 404
    assert client.get(f"/api/v1/commands/{command['id']}/report", headers=member).status_code == 404


def test_list_pages_newest_first_with_a_cursor() -> None:
    ids = [_create(f"Prepare a summary number {index}")["id"] for index in range(3)]
    first = client.get("/api/v1/commands", headers=owner, params={"limit": 2}).json()
    assert [item["id"] for item in first] == [ids[2], ids[1]]
    assert first[0]["status"] == "ready" and first[0]["step_count"] == 1
    rest = client.get("/api/v1/commands", headers=owner, params={"limit": 2, "cursor": ids[1]}).json()
    assert [item["id"] for item in rest] == [ids[0]]
    assert client.get("/api/v1/commands", headers=owner, params={"cursor": "command_unknown"}).status_code == 400


def test_delete_removes_history_only_after_the_command_stopped() -> None:
    command = _create("Send the weekly update to the team")
    task_id = command["steps"][0]["task_id"]
    client.post(f"/api/v1/tasks/{task_id}/run", headers=owner)
    assert client.delete(f"/api/v1/commands/{command['id']}", headers=owner).status_code == 409
    client.post(f"/api/v1/tasks/{task_id}/cancel", headers=owner)
    assert client.delete(f"/api/v1/commands/{command['id']}", headers=owner).status_code == 204
    assert client.get(f"/api/v1/commands/{command['id']}", headers=owner).status_code == 404
    assert task_id in store.tasks  # the task stays in the workspace
    assert [record.action for record in store.audit_records if record.target == command["id"]] == [
        "command.created",
        "command.deleted",
    ]


def test_report_in_markdown_and_json_is_audited() -> None:
    command = _create(independent=True)
    _run_all(command)
    markdown = client.get(f"/api/v1/commands/{command['id']}/report", headers=owner, params={"format": "markdown"})
    assert markdown.status_code == 200 and markdown.headers["content-type"].startswith("text/markdown")
    assert "## 1. Check my emails" in markdown.text and "Approval external.action: pending" in markdown.text
    as_json = client.get(f"/api/v1/commands/{command['id']}/report", headers=owner)
    assert as_json.json()["id"] == command["id"]
    assert [record.metadata["format"] for record in store.audit_records if record.action == "command.exported"] == [
        "markdown",
        "json",
    ]


def test_attachments_must_be_workspace_files() -> None:
    response = client.post(
        "/api/v1/commands",
        headers=owner,
        json={"text": "Check", "steps": [{"instruction": "Check the photo"}], "attachments": ["file_missing"]},
    )
    assert response.status_code == 422 and "Attached file not found" in response.text


# --- next steps and suggestions -----------------------------------------------------------


def test_next_steps_follow_the_results() -> None:
    command = _create(independent=True)
    _run_all(command)
    steps = client.get(f"/api/v1/commands/{command['id']}/next-steps", headers=owner).json()
    kinds = [item["kind"] for item in steps]
    assert kinds[0] == "review_approvals" and "Review 1 pending approval" == steps[0]["label"]
    assert "Draft replies to the most urgent emails" in [item["text"] for item in steps]
    assert all(item["text"] for item in steps if item["kind"] == "command")

    failed = _create("Send the weekly update to the team")
    run = client.post(f"/api/v1/tasks/{failed['steps'][0]['task_id']}/run", headers=owner).json()
    client.post(f"/api/v1/approvals/{run['approval']['id']}/reject", headers=owner)
    retry = client.get(f"/api/v1/commands/{failed['id']}/next-steps", headers=owner).json()
    assert retry[0]["id"] == "retry_1" and retry[0]["text"] == "Send the weekly update to the team"


def test_completed_command_offers_to_be_saved_as_a_quick_command_in_arabic() -> None:
    command = _create("جهز ملخصا لملاحظاتي")
    _run_all(command)
    steps = client.get(f"/api/v1/commands/{command['id']}/next-steps", headers=owner).json()
    assert steps[-1]["kind"] == "save_quick_command" and steps[-1]["label"] == "احفظ هذا الأمر كأمر سريع"
    client.post("/api/v1/quick-commands", headers=owner, json={"title": "ملخص", "text": "جهز ملخصا لملاحظاتي"})
    again = client.get(f"/api/v1/commands/{command['id']}/next-steps", headers=owner).json()
    assert "save_quick_command" not in [item["kind"] for item in again]


def test_suggestions_put_quick_commands_then_most_used_then_starters() -> None:
    empty = client.get("/api/v1/commands/suggestions", headers=owner).json()
    assert empty and {item["kind"] for item in empty} == {"starter"}
    assert empty[0]["text"] == "Check my emails and summarize the urgent ones"
    # A starter never needs a tool this deployment lacks (web search is not configured).
    assert all("Search the web" not in item["text"] for item in empty)
    arabic = client.get("/api/v1/commands/suggestions", headers=owner, params={"locale": "ar"}).json()
    assert arabic[0]["text"] == "تحقق من بريدي ولخص الرسائل العاجلة"

    for text in ("Prepare a summary of sales", "Draft the weekly plan", "Prepare a summary of sales"):
        _create(text)
    quick = client.post(
        "/api/v1/quick-commands", headers=owner, json={"title": "PMV report", "text": "Check PMV tyre report", "category": "work"}
    ).json()
    items = client.get("/api/v1/commands/suggestions", headers=owner).json()
    assert items[0] == {**items[0], "kind": "quick", "quick_command_id": quick["id"], "title": "PMV report"}
    recent = [item for item in items if item["kind"] == "recent"]
    assert [(item["text"], item["use_count"]) for item in recent] == [
        ("Prepare a summary of sales", 2),
        ("Draft the weekly plan", 1),
    ]
    assert len(items) <= 8
    assert client.get("/api/v1/commands/suggestions", headers=member).json()[0]["kind"] == "starter"


# --- quick commands -----------------------------------------------------------------------


def test_quick_commands_crud_per_user() -> None:
    created = client.post(
        "/api/v1/quick-commands",
        headers=member,
        json={"title": " Morning  mail ", "text": "Check my emails", "category": "work"},
    )
    assert created.status_code == 201, created.text
    quick = created.json()
    assert quick["title"] == "Morning mail" and quick["position"] == 0 and quick["use_count"] == 0
    second = client.post(
        "/api/v1/quick-commands", headers=member, json={"title": "Plan", "text": "Draft the plan", "category": "work"}
    ).json()
    assert second["position"] == 1
    personal = client.post("/api/v1/quick-commands", headers=member, json={"title": "Gym", "text": "Remind me"}).json()
    assert personal["category"] == "custom"

    work = client.get("/api/v1/quick-commands", headers=member, params={"category": "work"}).json()
    assert [item["id"] for item in work] == [quick["id"], second["id"]]
    moved = client.put(f"/api/v1/quick-commands/{second['id']}", headers=member, json={"position": 0, "category": "personal"})
    assert moved.status_code == 200 and moved.json()["category"] == "personal"
    assert client.put(f"/api/v1/quick-commands/{second['id']}", headers=member, json={}).status_code == 422
    assert client.put(f"/api/v1/quick-commands/{second['id']}", headers=member, json={"title": "  "}).status_code == 422

    # Private: another user, even an owner, neither sees nor changes them.
    assert client.get("/api/v1/quick-commands", headers=owner).json() == []
    assert client.put(f"/api/v1/quick-commands/{quick['id']}", headers=owner, json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/v1/quick-commands/{quick['id']}", headers=owner).status_code == 404
    assert client.post("/api/v1/quick-commands", headers=viewer, json={"title": "x", "text": "y"}).status_code == 403
    assert client.get("/api/v1/quick-commands", headers=viewer).status_code == 200

    assert client.delete(f"/api/v1/quick-commands/{quick['id']}", headers=member).status_code == 204
    assert client.delete(f"/api/v1/quick-commands/{quick['id']}", headers=member).status_code == 404
    actions = [record.action for record in store.audit_records if record.actor == "user_member"]
    assert actions == [
        "quick_command.created",
        "quick_command.created",
        "quick_command.created",
        "quick_command.updated",
        "quick_command.deleted",
    ]


def test_running_a_quick_command_counts_its_use() -> None:
    quick = client.post("/api/v1/quick-commands", headers=owner, json={"title": "Mail", "text": "Check my emails"}).json()
    command = _create("Check my emails", source="quick_command", quick_command_id=quick["id"])
    assert command["quick_command_id"] == quick["id"]
    (used,) = client.get("/api/v1/quick-commands", headers=owner).json()
    assert used["use_count"] == 1 and used["last_used_at"]
    missing = client.post(
        "/api/v1/commands",
        headers=owner,
        json={"text": "x", "steps": [{"instruction": "Check"}], "quick_command_id": "quick_missing"},
    )
    assert missing.status_code == 404


def test_quick_commands_are_capped_per_user() -> None:
    for index in range(MAX_QUICK_COMMANDS):
        response = client.post("/api/v1/quick-commands", headers=owner, json={"title": f"q{index}", "text": "Check"})
        assert response.status_code == 201
    over = client.post("/api/v1/quick-commands", headers=owner, json={"title": "one more", "text": "Check"})
    assert over.status_code == 409 and "up to 100" in over.text


def test_derive_status_covers_every_mix() -> None:
    assert derive_status([]) == CommandStatus.READY
    assert derive_status(["created", "created"]) == CommandStatus.READY
    assert derive_status(["created", "completed"]) == CommandStatus.RUNNING
    assert derive_status(["running", "waiting_approval"]) == CommandStatus.RUNNING
    assert derive_status(["completed", "waiting_approval"]) == CommandStatus.WAITING_APPROVAL
    assert derive_status(["completed", "completed"]) == CommandStatus.COMPLETED
    assert derive_status(["completed", "failed"]) == CommandStatus.PARTIAL
    assert derive_status(["failed", "cancelled"]) == CommandStatus.FAILED
    assert derive_status(["cancelled"]) == CommandStatus.CANCELLED


def test_arabic_triggers_select_the_same_skills_as_english() -> None:
    registry = default_skill_registry()
    tools = {"search_email", "external.action", "anum.respond", "list_whatsapp_conversations"}
    assert "anum.email" in {skill.id for skill in registry.select("راجع البريد", tools)}
    assert "anum.external-action" in {skill.id for skill in registry.select("وأرسل التقرير", tools)}
    assert "anum.whatsapp-inbox" in {skill.id for skill in registry.select("محادثات واتساب", tools)}
    assert "anum.email" not in {skill.id for skill in registry.select("Check the tyre report", tools)}


def test_download_and_delete_my_data_cover_commands_and_quick_commands() -> None:
    command = _create("Prepare a summary of sales", member)
    client.post("/api/v1/quick-commands", headers=member, json={"title": "Sales", "text": "Prepare a summary of sales"})
    _create("Prepare a summary of notes", owner)  # another user's: untouched

    summary = client.get("/api/v1/privacy", headers=member).json()
    assert summary["private"]["commands"] == 1 and summary["private"]["quick_commands"] == 1
    exported = client.get("/api/v1/privacy/export", headers=member)
    assert exported.status_code == 200, exported.text
    section = exported.json()["commands"]
    assert [item["text"] for item in section["commands"]] == ["Prepare a summary of sales"]
    assert section["commands"][0]["steps"][0]["task_id"] == command["steps"][0]["task_id"]
    assert [item["title"] for item in section["quick_commands"]] == ["Sales"]

    deleted = client.post("/api/v1/privacy/delete", headers=member, json={"confirm": "DELETE MY DATA"})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"]["commands"] == 1 and deleted.json()["deleted"]["quick_commands"] == 1
    assert client.get("/api/v1/commands", headers=member).json() == []
    assert client.get("/api/v1/quick-commands", headers=member).json() == []
    # The tasks stay with the workspace; other users keep their history.
    assert command["steps"][0]["task_id"] in store.tasks
    assert len(client.get("/api/v1/commands", headers=owner).json()) == 1


# --- steps that build on each other ---------------------------------------------------------


def test_then_and_earlier_results_become_dependencies() -> None:
    assert [step["depends_on"] for step in _preview(MOCKUP)["steps"]] == [[], [], [], [1, 2, 3]]
    assert [step["depends_on"] for step in _preview(NO_TYRE)["steps"]] == [[], [], [1, 2]]
    # A clause that uses an earlier result waits for the step before it, in English and Arabic.
    plan = _preview("Search the web for tyre prices in Dubai and send the results to the team")
    assert [step["depends_on"] for step in plan["steps"]] == [[], [1]]
    arabic = _preview("ابحث في الإنترنت عن أسعار الإطارات ثم أرسل النتائج إلى الفريق")
    assert [step["depends_on"] for step in arabic["steps"]] == [[], [1]]
    # Independent steps stay independent.
    assert [step["depends_on"] for step in _preview("Check my emails and search the web for tyre prices")["steps"]] == [[], []]


def test_create_keeps_declared_dependencies_or_derives_them() -> None:
    derived = client.post("/api/v1/commands", headers=owner, json={"text": NO_TYRE, "steps": [
        {"instruction": "Check my emails, prepare a summary"}, {"instruction": "send the summary to the team"},
    ]})
    assert derived.status_code == 201, derived.text
    assert [step["depends_on"] for step in derived.json()["steps"]] == [[], [1]]
    declared = client.post("/api/v1/commands", headers=owner, json={"text": NO_TYRE, "steps": [
        {"instruction": "Check my emails, prepare a summary", "depends_on": []},
        {"instruction": "schedule a meeting with the team tomorrow at 10 a.m", "depends_on": []},
        {"instruction": "send the summary to the team", "depends_on": [1]},
    ]})
    assert [step["depends_on"] for step in declared.json()["steps"]] == [[], [], [1]]
    forward = client.post("/api/v1/commands", headers=owner, json={"text": NO_TYRE, "steps": [
        {"instruction": "Check my emails", "depends_on": [2]}, {"instruction": "send the summary to the team"},
    ]})
    assert forward.status_code == 422 and "only wait for steps that come before it" in forward.text


def test_a_dependent_step_cannot_run_before_the_steps_it_waits_for() -> None:
    command = _create(NO_TYRE)
    last = command["steps"][2]
    early = client.post(f"/api/v1/tasks/{last['task_id']}/run", headers=owner)
    assert early.status_code == 409
    assert early.json()["error"]["message"] == 'Step 3 waits for step 1 ("Check my emails, prepare a summary") to complete; it has not started yet.'
    assert store.tasks[last["task_id"]].status == "created"
    view = _view(command["id"])
    assert view["steps"][2]["depends_on"] == [1, 2] and view["steps"][2]["waiting_for"] == [1, 2]
    assert view["steps"][2]["blocked_by"] == []


def test_start_runs_steps_in_dependency_order_and_passes_results_forward() -> None:
    command = _create(NO_TYRE)
    response = client.post(f"/api/v1/commands/{command['id']}/start", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    ids = [step["task_id"] for step in command["steps"]]
    assert body["refused"] == {} and sorted(body["started"]) == sorted(ids)
    # The first two started together; the third only after both completed.
    assert set(body["started"][:2]) == set(ids[:2]) and body["started"][2] == ids[2]
    view = body["command"]
    assert [step["status"] for step in view["steps"]] == ["completed", "completed", "waiting_approval"]
    assert view["steps"][2]["waiting_for"] == []
    first_done = max(store.runs[step["run_id"]].updated_at for step in view["steps"][:2])
    assert store.runs[view["steps"][2]["run_id"]].created_at >= first_done
    # The dependent step's run received the earlier results as labeled, untrusted context: its
    # instruction is unchanged, so the confirmed plan (tool, approval) is what ran.
    assert store.tasks[ids[2]].prompt == "send the summary to the team"
    used = [step for step in store.runs[view["steps"][2]["run_id"]].steps if step.type == "command_input"]
    assert len(used) == 1 and used[0].metadata == {"status": "used", "steps": [1, 2]}
    from anum_api.commands import step_inputs
    from anum_api.schemas import TenantContext

    context = TenantContext(tenant_id="tenant_a", workspace_id="workspace_a", user_id="user_owner", roles=["owner", "member"])
    blocks, positions = step_inputs(store.tasks[ids[2]], context)
    assert positions == [1, 2] and "source=tool_output" in blocks[0] and "origin=command-step-1" in blocks[0]
    assert "Result of step 1 (Check my emails, prepare a summary):" in blocks[0]
    assert view["steps"][0]["result"].strip()[:40] in blocks[0]
    # Another user's runs never read this user's command.
    assert step_inputs(store.tasks[ids[2]], context.model_copy(update={"user_id": "user_member"})) == ([], [])
    # Starting again starts nothing new: everything that could start has.
    again = client.post(f"/api/v1/commands/{command['id']}/start", headers=owner).json()
    assert again["started"] == [] and again["refused"] == {}


def test_a_failed_earlier_step_blocks_the_steps_that_need_it() -> None:
    command = _create("Send the weekly update to the team, then search the web for tyre prices")
    assert [step["depends_on"] for step in command["steps"]] == [[], [1]]
    started = client.post(f"/api/v1/commands/{command['id']}/start", headers=owner).json()
    assert started["started"] == [command["steps"][0]["task_id"]]
    (pending,) = started["command"]["pending"]
    client.post(f"/api/v1/approvals/{pending['id']}/reject", headers=owner)
    view = _view(command["id"])
    assert view["steps"][1]["status"] == "created" and view["steps"][1]["blocked_by"] == [1]
    # The blocked step cannot finish on its own, so the command no longer reads as running.
    assert view["status"] == "failed"
    again = client.post(f"/api/v1/commands/{command['id']}/start", headers=owner).json()
    assert again["started"] == []
    refused = client.post(f"/api/v1/tasks/{command['steps'][1]['task_id']}/run", headers=owner)
    assert refused.status_code == 409 and "Run step 1 again first" in refused.json()["error"]["message"]


def test_start_is_private_and_needs_task_run() -> None:
    command = _create(NO_TYRE)
    assert client.post(f"/api/v1/commands/{command['id']}/start", headers=member).status_code == 404
    assert client.post(f"/api/v1/commands/{command['id']}/start", headers=viewer).status_code == 403


# --- calendar steps follow the real calendar connection --------------------------------------


def test_calendar_steps_say_what_the_connected_calendar_does() -> None:
    from anum_api.calendar_providers import CalendarProvider
    from anum_api.calendar_sync import CalendarConnection, ConnectionStatus, sync_backend
    from anum_api.schemas import utc_now

    meeting = "Schedule a meeting with the team tomorrow at 10 a.m"
    (none,) = _preview(meeting)["steps"]
    assert none["agent"]["id"] == "calendar" and none["connected"] is False
    assert none["note"].startswith("No calendar is connected to ANUM yet")
    now = utc_now()
    connection = CalendarConnection(
        id="calconn_1", tenant_id="tenant_a", workspace_id="workspace_a", provider=CalendarProvider.GOOGLE,
        email="owner@example.com", connected_by="user_owner", created_at=now, updated_at=now,
    )
    sync_backend.connections[connection.id] = connection
    try:
        (live,) = _preview(meeting)["steps"]
        assert live["connected"] is True and "No calendar" not in live["note"]
        assert live["note"].startswith("Google Calendar is connected.") and "Ask to send invitations" in live["note"]
        (arabic,) = _preview("رتب اجتماعا مع الفريق غدا", locale="ar")["steps"]
        assert arabic["agent"]["id"] == "calendar" and "Google Calendar متصل" in arabic["note"]
        # Another workspace's calendar is never this workspace's.
        (other,) = _preview(meeting, headers=other_tenant)["steps"]
        assert other["connected"] is False
        sync_backend.connections[connection.id] = connection.model_copy(update={"status": ConnectionStatus.REAUTH_REQUIRED})
        (reauth,) = _preview(meeting)["steps"]
        assert reauth["connected"] is False and reauth["note"].startswith("Google Calendar needs to be connected again")
    finally:
        sync_backend.connections.pop(connection.id, None)
