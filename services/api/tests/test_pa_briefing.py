"""PA daily briefing: facts from real records only, honest states, tenant scope, no approvals."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main, pa_briefing
from anum_api.agent_profiles import agent_store
from anum_api.calendar_api import calendar_backend
from anum_api.model_budget import memory_model_budget_store
from anum_api.schemas import Approval, ApprovalStatus, RiskLevel, Task, TaskStatus, utc_now
from anum_api.store import store

T1, W1 = "tenant_brief_one", "workspace_brief_one"


def hdr(user: str = "owner_brief", role: str = "owner", tenant: str = T1, workspace: str = W1) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER, VIEWER, MEMBER = hdr(), hdr("viewer_brief", "viewer"), hdr("member_brief", "member")
OTHER = hdr("owner_other", "owner", "tenant_brief_two", "workspace_brief_two")
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Any:
    def wipe() -> None:
        for c in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
            c.clear()
        calendar_backend.clear()
        agent_store.clear()
        memory_model_budget_store.clear()

    wipe()
    yield
    wipe()


def add_task(title: str, status: TaskStatus, *, due_in: timedelta | None = None, agent_id: str | None = None, tenant: str = T1, workspace: str = W1) -> Task:
    now = utc_now()
    task = Task(id=f"task_{len(store.tasks) + 1}", title=title, prompt="p", status=status, tenant_id=tenant, workspace_id=workspace,
                created_at=now, updated_at=now, created_by="owner_brief", agent_id=agent_id, due_at=(now + due_in) if due_in is not None else None)
    store.tasks[task.id] = task
    return task


def briefing(h: dict[str, str] = OWNER, lang: str = "en") -> dict[str, Any]:
    r = client.get("/api/v1/executive-pa/briefing", headers=h, params={"lang": lang})
    assert r.status_code == 200, r.text
    return r.json()


def section(data: dict[str, Any], key: str) -> dict[str, Any]:
    return next(s for s in data["sections"] if s["key"] == key)


def test_empty_workspace_says_nothing_to_report_in_every_source() -> None:
    data = briefing()
    states = {s["key"]: s["state"] for s in data["sections"]}
    assert [s["key"] for s in data["sections"]] == list(pa_briefing.SECTION_KEYS)
    assert states["approvals"] == states["blocked"] == states["due"] == states["running"] == states["meetings"] == states["agents"] == "empty"
    assert section(data, "approvals")["headline"] == "Nothing to report."
    assert data["reportable"] == 0 and data["text"] == "Nothing to report today."


def test_tasks_blocked_due_and_running_come_from_real_records_with_links() -> None:
    add_task("Pay supplier", TaskStatus.WAITING_APPROVAL)
    add_task("Fix import", TaskStatus.FAILED)
    add_task("Late report", TaskStatus.QUEUED, due_in=-timedelta(hours=3))
    add_task("Review plan", TaskStatus.CREATED, due_in=timedelta(minutes=5))
    add_task("Far away", TaskStatus.CREATED, due_in=timedelta(days=9))
    add_task("Done long ago", TaskStatus.COMPLETED, due_in=-timedelta(days=3))
    running = add_task("Crunch numbers", TaskStatus.RUNNING)
    data = briefing()
    blocked = section(data, "blocked")
    assert blocked["count"] == 2 and blocked["headline"] == "2 tasks are blocked (waiting for approval or failed)."
    assert {i["label"] for i in blocked["items"]} == {"Pay supplier", "Fix import"}
    assert all(i["link"]["view"] == "tasks" and i["link"]["id"].startswith("task_") for i in blocked["items"])
    due = section(data, "due")
    assert due["facts"] == {"overdue": 1, "today": 1} or due["facts"]["overdue"] == 1
    assert due["items"][0]["label"] == "Late report" and due["items"][0]["detail_text"] == "overdue"
    run = section(data, "running")
    assert run["count"] == 1 and run["items"][0]["id"] == running.id and run["headline"] == "1 agent run is running now."


def test_approvals_are_listed_pending_only_with_links() -> None:
    task = add_task("Send offer", TaskStatus.WAITING_APPROVAL)
    now = utc_now()
    store.approvals["ap_1"] = Approval(id="ap_1", task_id=task.id, action="send_email", risk_level=RiskLevel.HIGH, status=ApprovalStatus.PENDING,
                                       reason="Sends mail", created_at=now, expires_at=now + timedelta(days=1), payload_hash="a" * 64, requested_by="owner_brief")
    data = briefing()
    approvals = section(data, "approvals")
    assert approvals["count"] == 1 and approvals["items"][0]["link"] == {"view": "approvals", "id": "ap_1"}
    assert approvals["headline"] == "1 approval is waiting for a decision."


def test_meetings_today_use_the_calendar_and_other_days_are_left_out() -> None:
    start = utc_now().replace(hour=0, minute=30, second=0, microsecond=0)
    base = {"kind": "meeting", "attendees": []}
    today = client.post("/api/v1/calendar/events", headers=OWNER, json={**base, "title": "Fleet review", "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat()})
    later = start + timedelta(days=3)
    client.post("/api/v1/calendar/events", headers=OWNER, json={**base, "title": "Next week", "starts_at": later.isoformat(), "ends_at": (later + timedelta(hours=1)).isoformat()})
    assert today.status_code == 201, today.text
    meetings = section(briefing(), "meetings")
    assert meetings["count"] == 1 and meetings["items"][0]["label"] == "Fleet review"
    assert meetings["items"][0]["link"]["view"] == "calendar" and meetings["items"][0]["link"]["id"] == today.json()["id"]
    assert section(briefing(OTHER), "meetings")["state"] == "empty"


def test_agents_needing_attention_are_paused_or_have_a_failed_task() -> None:
    created = client.post("/api/v1/agents", headers=OWNER, json={"name": "Scout"}).json()
    other = client.post("/api/v1/agents", headers=OWNER, json={"name": "Clerk"}).json()
    client.patch(f"/api/v1/agents/{created['id']}", headers=OWNER, json={"status": "paused"})
    add_task("Broke", TaskStatus.FAILED, agent_id=other["id"])
    agents = section(briefing(), "agents")
    assert agents["count"] == 2
    assert {(i["label"], i["detail_text"]) for i in agents["items"]} == {("Scout", "paused"), ("Clerk", "has a failed task")}
    assert section(briefing(OTHER), "agents")["state"] == "empty"


def test_cost_against_budget_is_owner_only_and_uses_the_stored_figures() -> None:
    assert section(briefing(MEMBER), "cost")["state"] == "restricted"
    assert section(briefing(OWNER), "cost")["state"] == "empty"
    client.put("/api/v1/model-budgets/workspace", headers=OWNER, json={"monthly_cost_limit_usd": 50})
    cost = section(briefing(OWNER), "cost")
    assert cost["state"] == "ok" and cost["facts"]["limit"] == "50.00" and cost["facts"]["used"] == "0.00"
    assert cost["headline"] == "USD 0.00 spent this month of a USD 50.00 limit."


def test_unreadable_source_is_not_connected_without_leaking_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any) -> Any:
        raise RuntimeError("secret connection string postgres://user:pw@host")

    monkeypatch.setitem(pa_briefing._LOADERS, "meetings", boom)
    r = client.get("/api/v1/executive-pa/briefing", headers=OWNER)
    meetings = section(r.json(), "meetings")
    assert meetings["state"] == "not_connected" and meetings["headline"] == "Not connected."
    assert "postgres" not in r.text and "secret" not in r.text


def test_viewer_may_read_but_a_user_without_task_read_is_refused() -> None:
    assert client.get("/api/v1/executive-pa/briefing", headers=VIEWER).status_code == 200
    assert client.get("/api/v1/executive-pa/briefing", headers=hdr("nobody", "none")).status_code == 403


def test_other_tenant_sees_none_of_the_first_tenants_records() -> None:
    add_task("Secret plan", TaskStatus.FAILED)
    mine = briefing(OWNER)
    theirs = briefing(OTHER)
    assert section(mine, "blocked")["count"] == 1
    assert section(theirs, "blocked")["state"] == "empty" and "Secret plan" not in str(theirs)


def test_arabic_briefing_uses_arabic_templates_and_the_same_figures() -> None:
    add_task("Fix import", TaskStatus.FAILED)
    data = briefing(lang="ar")
    blocked = section(data, "blocked")
    assert blocked["title"] == "العمل المعطل" and blocked["headline"] == "مهمة واحدة معطلة (بانتظار الموافقة أو فاشلة)."
    assert section(data, "approvals")["headline"] == "لا شيء للإبلاغ عنه."
    assert data["lang"] == "ar" and "Fix import" in str(blocked["items"])


def test_figure_guard_rejects_a_number_the_facts_do_not_hold() -> None:
    assert pa_briefing.figures_match("3 tasks are blocked.", [3])
    assert pa_briefing.figures_match("٣ مهام", [3])
    assert not pa_briefing.figures_match("4 tasks are blocked.", [3])
    assert pa_briefing.figures_match("Nothing to report.", [])
    assert not pa_briefing.figures_match("USD 12.50 spent", ["12.00"])


def test_a_headline_that_fails_the_guard_is_never_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    add_task("Fix import", TaskStatus.FAILED)
    monkeypatch.setitem(pa_briefing.TEXT["en"], "blocked.ok.one", "99 tasks are blocked.")
    blocked = section(briefing(), "blocked")
    assert blocked["state"] == "not_connected" and blocked["headline"] == "Not connected." and blocked["items"] == []


def test_tool_works_by_voice_and_typed_and_never_confirms_or_decides() -> None:
    add_task("Fix import", TaskStatus.FAILED)
    for mode in ("typed", "voice", "click"):
        r = client.post("/api/v1/executive-pa/invoke", headers=OWNER, json={"tool": "daily_briefing", "arguments": {"lang": "en"}, "input_mode": mode})
        assert r.status_code == 200 and r.json()["status"] == "ok"
        assert section(r.json()["briefing"], "blocked")["count"] == 1
    tools = {t["name"]: t for t in client.get("/api/v1/executive-pa/tools", headers=OWNER).json()["tools"]}
    assert tools["daily_briefing"]["kind"] == "read" and tools["daily_briefing"]["autonomy"] == "automatic"
    assert not [n for n in tools if "approve" in n or "reject" in n or "decide" in n]
    # No briefing item offers an approval decision: links only open a view.
    assert all(set(i["link"]) == {"view", "id"} for s in briefing()["sections"] for i in s["items"])
    assert any(a.metadata.get("tool") == "daily_briefing" and a.metadata.get("input_mode") == "voice" for a in store.audit_records)


def test_tool_rejects_unknown_arguments_and_languages() -> None:
    for args in ({"lang": "fr"}, {"tenant_id": "x"}):
        r = client.post("/api/v1/executive-pa/invoke", headers=OWNER, json={"tool": "daily_briefing", "arguments": args})
        assert r.status_code == 422
    assert client.get("/api/v1/executive-pa/briefing", headers=OWNER, params={"lang": "fr"}).status_code == 422


def test_truncated_sources_say_so() -> None:
    for i in range(pa_briefing.READ_LIMIT):
        add_task(f"t{i}", TaskStatus.FAILED)
    blocked = section(briefing(), "blocked")
    assert blocked["truncated"] is True and blocked["count"] == pa_briefing.READ_LIMIT and len(blocked["items"]) == pa_briefing.MAX_ITEMS
    assert "first 200 records" in blocked["headline"]
