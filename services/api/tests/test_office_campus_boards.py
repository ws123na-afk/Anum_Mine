"""Campus boards without a database: the work rule, paging, refusals and the per-workspace isolation of the route."""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from anum_api import office_campus_boards as ocb
from anum_api.main import app
from anum_api.schemas import CallerMembership
from anum_api.settings import settings

TENANT = "tenant_a"
HEADERS = {"x-tenant-id": TENANT, "x-workspace-id": "ws_open", "x-user-id": "u1", "x-user-roles": "owner"}


def item(stage: str = "in_office", building: str | None = "b1", kind: str = "task", waiting: bool = False) -> dict:
    return {"kind": kind, "stage": stage, "location": {"building_id": building} if building else None,
            "waiting": {"code": "approval_pending"} if waiting else None}


def test_work_summary_counts_located_open_tasks_only() -> None:
    items = [item(), item(waiting=True, stage="waiting_human"), item(stage="blocked", building="b2"), item(stage="done"),
             item(building=None), item(kind="lead"), item(building="b9")]
    out = ocb.work_summary(items, ["b1", "b2"], source="connected", truncated=False)
    assert (out["open"], out["waiting"], out["blocked"]) == (4, 1, 1)
    assert out["by_building"] == {"b1": {"open": 2, "waiting": 1, "blocked": 0}, "b2": {"open": 1, "waiting": 0, "blocked": 1}}


def test_work_summary_zero_is_zero_and_a_missing_source_has_no_numbers() -> None:
    zero = ocb.work_summary([], ["b1"], source="connected", truncated=True)
    assert (zero["open"], zero["waiting"], zero["blocked"], zero["truncated"]) == (0, 0, 0, True)
    for source in ("not_connected", "no_access", "error"):
        none = ocb.work_summary([item()], ["b1"], source=source, truncated=False)
        assert none["open"] is None and none["by_building"] == {} and none["source"] == source


def member(ws: str, role: str = "member") -> CallerMembership:
    return CallerMembership(tenant_id=TENANT, workspace_id=ws, workspace_name=ws.upper(), role=role, archived=False)


@pytest.fixture
def route(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, list]]:
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    members = [member("ws_open", "owner"), member("ws_b"), member("ws_c", "viewer"), member("ws_d", "auditor"), member("ws_e")]
    monkeypatch.setattr(ocb, "_memberships", lambda ctx: members)
    reads: list = []

    def fake_read(ctx, now):
        reads.append((ctx.tenant_id, ctx.workspace_id, ctx.user_id, tuple(ctx.roles)))
        if ctx.workspace_id == "ws_e":
            raise RuntimeError("unreadable")
        return {"workspace_id": ctx.workspace_id, "accessible": True, "work": {"open": 1}}

    monkeypatch.setattr(ocb, "read_board", fake_read)
    ocb.clear_cache()
    yield TestClient(app), reads
    ocb.clear_cache()


def test_boards_read_each_workspace_in_its_own_context_and_skip_the_open_one(route) -> None:
    client, reads = route
    r = client.get("/api/v1/office/campus/boards", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1 and body["current_workspace_id"] == "ws_open" and body["next_cursor"] is None
    # ws_d: a role without task:read is not listed at all; ws_e failed: listed with no numbers.
    assert [b["workspace_id"] for b in body["boards"]] == ["ws_b", "ws_c", "ws_e"]
    assert body["boards"][2] == {"workspace_id": "ws_e", "accessible": False}
    # Every read is the caller, in that workspace, with the caller's own role there: never widened.
    assert reads == [(TENANT, "ws_b", "u1", ("member",)), (TENANT, "ws_c", "u1", ("viewer",)), (TENANT, "ws_e", "u1", ("member",))]


def test_boards_page_with_a_cursor_and_refuse_an_unknown_one(route) -> None:
    client, _ = route
    first = client.get("/api/v1/office/campus/boards?limit=2", headers=HEADERS).json()
    assert [b["workspace_id"] for b in first["boards"]] == ["ws_b", "ws_c"] and first["next_cursor"] == "ws_c"
    second = client.get("/api/v1/office/campus/boards?limit=2&cursor=ws_c", headers=HEADERS).json()
    assert [b["workspace_id"] for b in second["boards"]] == ["ws_e"] and second["next_cursor"] is None
    assert client.get("/api/v1/office/campus/boards?cursor=ws_zz", headers=HEADERS).status_code == 400
    assert client.get("/api/v1/office/campus/boards?limit=17", headers=HEADERS).status_code == 422
    assert client.get("/api/v1/office/campus/boards?limit=0", headers=HEADERS).status_code == 422


def test_boards_are_cached_briefly_per_user_and_role_and_failures_are_not(route) -> None:
    client, reads = route
    client.get("/api/v1/office/campus/boards", headers=HEADERS)
    client.get("/api/v1/office/campus/boards", headers=HEADERS)
    assert [r[1] for r in reads] == ["ws_b", "ws_c", "ws_e", "ws_e"]
    client.get("/api/v1/office/campus/boards", headers={**HEADERS, "x-user-id": "u2"})
    assert [r[1] for r in reads[4:]] == ["ws_b", "ws_c", "ws_e"]


def test_boards_follow_a_removed_membership_at_once(route, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = route
    assert "ws_b" in [b["workspace_id"] for b in client.get("/api/v1/office/campus/boards", headers=HEADERS).json()["boards"]]
    monkeypatch.setattr(ocb, "_memberships", lambda ctx: [member("ws_open", "owner"), member("ws_c", "viewer")])
    assert [b["workspace_id"] for b in client.get("/api/v1/office/campus/boards", headers=HEADERS).json()["boards"]] == ["ws_c"]


def test_boards_need_task_read_and_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr(settings, "repository_backend", "memory")
    assert client.get("/api/v1/office/campus/boards", headers=HEADERS).status_code == 503
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    assert client.get("/api/v1/office/campus/boards", headers={**HEADERS, "x-user-roles": "auditor"}).status_code == 403
