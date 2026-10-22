"""Executive PA tools: authorisation, confirmation gate, audit, isolation (memory backend)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import executive_pa, main
from anum_api.store import store

T1, W1 = "tenant_pa_one", "workspace_pa_one"


def hdr(user: str = "owner_pa", role: str = "owner", tenant: str = T1, workspace: str = W1) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER, VIEWER, MEMBER = hdr(), hdr("viewer_pa", "viewer"), hdr("member_pa", "member")
OTHER = hdr("owner_other", "owner", "tenant_pa_two", "workspace_pa_two")
client = TestClient(main.app)

OFFICE = {
    "buildings": [{"id": "hq", "name": "HQ"}],
    "floors": [{"id": "f1", "building_id": "hq", "level": 1, "name": "First"}, {"id": "f0", "building_id": "hq", "level": 0, "name": "Ground"}],
    "rooms": [
        {"id": "r_exec", "floor_id": "f1", "name": "Executive office", "kind": "office", "capacity": 4},
        {"id": "r_rec", "floor_id": "f0", "name": "Reception", "kind": "reception", "capacity": 6},
    ],
    "desks": [{"id": "d1", "room_id": "r_exec", "label": "D1", "agent_id": "agent_a"}],
}


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Any:
    for c in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
        c.clear()
    monkeypatch.setattr(executive_pa, "_load_office", lambda ctx: OFFICE if ctx.tenant_id == T1 else {"buildings": [], "floors": [], "rooms": [], "desks": []})
    yield


def call(tool: str, args: dict | None = None, h: dict = OWNER, mode: str = "typed", confirm: str | None = None) -> dict:
    body: dict = {"tool": tool, "arguments": args or {}, "input_mode": mode}
    if confirm:
        body["confirm_hash"] = confirm
    r = client.post("/api/v1/executive-pa/invoke", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def audits(action: str = "executive_pa.invoke") -> list:
    return [a for a in store.audit_records if a.action == action]


def test_catalogue_declares_risk_and_has_no_approval_tool() -> None:
    tools = client.get("/api/v1/executive-pa/tools", headers=OWNER).json()["tools"]
    names = {t["name"] for t in tools}
    assert not {n for n in names if "approve" in n or "reject" in n or "decide" in n}
    for t in tools:
        assert t["risk"] in {"low", "medium", "high"} and t["autonomy"] in {"automatic", "confirm_each"}
        assert t["governed_by"]
        assert (t["autonomy"] == "confirm_each") == (t["kind"] == "write")
    viewer = {t["name"]: t["allowed"] for t in client.get("/api/v1/executive-pa/tools", headers=VIEWER).json()["tools"]}
    assert viewer["start_task"] is False and viewer["navigate"] is True


def test_unknown_tool_such_as_approve_is_refused_and_audited() -> None:
    r = client.post("/api/v1/executive-pa/invoke", headers=OWNER, json={"tool": "approve", "arguments": {}})
    assert r.status_code == 422
    assert audits()[-1].outcome == "denied"


def test_navigate_room_agent_and_missing() -> None:
    assert call("navigate", {"target_kind": "room", "target_id": "r_exec"})["navigate"] == {"building_id": "hq", "floor_id": "f1", "room_id": "r_exec"}
    assert call("navigate", {"target_kind": "agent", "target_id": "agent_a"})["navigate"]["room_id"] == "r_exec"
    assert call("navigate", {"target_kind": "room", "target_id": "nope"})["status"] == "not_found"
    # another tenant sees none of the first tenant's rooms
    assert call("navigate", {"target_kind": "room", "target_id": "r_exec"}, h=OTHER)["status"] == "not_found"


def test_open_panel_checks_the_panels_own_permission() -> None:
    assert call("open_panel", {"panel": "approvals"}, h=VIEWER)["open_panel"] == {"view": "approvals"}
    assert call("open_panel", {"panel": "audit"}, h=MEMBER)["status"] == "denied"


def test_tour_is_generated_from_accessible_rooms_only() -> None:
    stops = client.get("/api/v1/executive-pa/tour", headers=OWNER).json()
    assert stops["status"] == "ready"
    assert [s["room_id"] for s in stops["stops"]] == ["r_rec", "r_exec"]
    other = client.get("/api/v1/executive-pa/tour", headers=OTHER).json()
    assert other == {"status": "empty", "stops": []}


def test_summarise_other_workspace_needs_a_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    # Memory backend: the executive API needs PostgreSQL, so the answer is "not connected".
    assert call("summarise_workspace", {"workspace_id": "workspace_pa_two"})["status"] == "not_connected"
    monkeypatch.setattr(executive_pa, "_granted_summary", lambda ctx, target: ("not_granted", None))
    assert call("summarise_workspace", {"workspace_id": "workspace_pa_two"})["status"] == "not_granted"
    monkeypatch.setattr(executive_pa, "_granted_summary", lambda ctx, target: ("ok", {"approvals": {"pending": 1}}) if target == "workspace_granted" else ("not_granted", None))
    granted = call("summarise_workspace", {"workspace_id": "workspace_granted"})
    assert granted["scope"] == "granted" and granted["summary"] == {"approvals": {"pending": 1}}
    assert call("summarise_workspace", {"workspace_id": "workspace_other"})["status"] == "not_granted"


def test_summarise_current_workspace_counts_only_this_scope() -> None:
    call("start_task", {"title": "x", "prompt": "y"})  # proposal only, creates nothing
    assert call("summarise_workspace")["summary"]["tasks"]["by_status"] == {} or sum(call("summarise_workspace")["summary"]["tasks"]["by_status"].values()) == 0
    assert sum(call("summarise_workspace", h=OTHER)["summary"]["tasks"]["by_status"].values()) == 0


def test_write_needs_confirmation_and_voice_cannot_confirm() -> None:
    args = {"title": "Weekly report", "prompt": "Draft the weekly report"}
    proposal = call("start_task", args, mode="voice")
    assert proposal["status"] == "needs_confirmation" and proposal["risk"] == "medium"
    digest = proposal["payload_hash"]
    assert not store.tasks
    spoken = call("start_task", args, mode="voice", confirm=digest)
    assert spoken["status"] == "needs_visual_confirmation"
    typed = call("start_task", args, mode="typed", confirm=digest)
    assert typed["status"] == "needs_visual_confirmation"
    assert not store.tasks


def test_confirmed_write_runs_the_governed_path_and_is_audited() -> None:
    args = {"title": "Weekly report", "prompt": "Draft the weekly report"}
    digest = call("start_task", args)["payload_hash"]
    done = call("start_task", args, mode="click", confirm=digest)
    assert done["status"] in {"done", "approval_pending"}
    tasks = list(store.tasks.values()) if isinstance(store.tasks, dict) else list(store.tasks)
    assert [t.tenant_id for t in tasks if t.title == "Weekly report"] == [T1]
    last = audits()[-1]
    assert last.metadata["tool"] == "start_task" and last.metadata["input_mode"] == "click" and last.outcome == "success"


def test_changed_arguments_invalidate_the_confirmation() -> None:
    digest = call("start_task", {"title": "A", "prompt": "B"})["payload_hash"]
    r = call("start_task", {"title": "A", "prompt": "B changed"}, mode="click", confirm=digest)
    assert r["status"] == "stale_confirmation"
    other_user = call("start_task", {"title": "A", "prompt": "B"}, h=hdr("owner_two"), mode="click", confirm=digest)
    assert other_user["status"] == "stale_confirmation"


def test_viewer_cannot_start_tasks_and_denial_is_audited() -> None:
    r = call("start_task", {"title": "A", "prompt": "B"}, h=VIEWER)
    assert r["status"] == "denied"
    assert audits()[-1].outcome == "denied"
    assert not store.tasks


def test_delegate_to_unknown_agent_fails_without_creating_work() -> None:
    args = {"title": "A", "prompt": "B", "agent_id": "agent_missing"}
    digest = call("delegate", args)["payload_hash"]
    r = call("delegate", args, mode="click", confirm=digest)
    assert r["status"] == "failed" and r["http_status"] == 404
    assert not store.tasks


def test_schedule_meeting_is_not_faked_without_postgresql() -> None:
    args = {"title": "Board sync"}
    digest = call("schedule_meeting", args)["payload_hash"]
    r = call("schedule_meeting", args, mode="click", confirm=digest)
    assert r["status"] == "not_connected"


def test_lists_are_scoped() -> None:
    assert call("list_blocked_work")["items"] == []
    assert call("list_pending_approvals", h=VIEWER)["status"] == "ok"
    assert call("list_pending_approvals", h=hdr("x", "none"))["status"] in {"denied", "ok"}


def test_extra_arguments_are_rejected() -> None:
    r = client.post("/api/v1/executive-pa/invoke", headers=OWNER, json={"tool": "navigate", "arguments": {"target_kind": "room", "target_id": "r", "tenant_id": "other"}})
    assert r.status_code == 422
