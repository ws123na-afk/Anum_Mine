"""Flow and stuck derivation without a database: a record or no item, a rule or not stuck."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from anum_api import office_flow as of
from anum_api.main import app

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


def desk(agent: str | None = "a1", room: str = "r1") -> of.FlowDesk:
    return of.FlowDesk(f"d_{room}", room, f"Room {room}", "f0", "hq", agent)


def task(tid: str, status: str, agent: str | None = "a1", age: timedelta = timedelta(minutes=1)) -> of.FlowTask:
    return of.FlowTask(tid, f"Title {tid}", status, agent, NOW - age - timedelta(hours=1), NOW - age)


def run(tid: str, status: str, age: timedelta = timedelta(minutes=1), result: str | None = None) -> of.FlowRun:
    return of.FlowRun(f"run_{tid}", tid, status, NOW - age, result)


def flow(**parts) -> dict:
    parts.setdefault("agents", [of.FlowAgent("a1", "Ada", "active")])
    parts.setdefault("desks", [desk()])
    result = of.derive_flow(of.FlowEvidence(as_of=NOW, **parts))
    return {i["key"]: i for i in result["items"]} | {"_": result}


def test_nothing_recorded_means_nothing_shown() -> None:
    assert flow()["_"]["items"] == [] and flow()["_"]["totals"]["items"] == 0


def test_running_task_sits_in_the_office_of_its_agent() -> None:
    items = flow(tasks=[task("t1", "running")], runs=[run("t1", "running")])
    item = items["task:t1"]
    assert item["stage"] == "in_office" and item["stuck"] is None
    assert item["location"]["room_id"] == "r1" and item["location"]["agent_name"] == "Ada"


def test_running_task_without_running_run_proves_nothing() -> None:
    assert flow(tasks=[task("t1", "running")])["task:t1"]["stage"] == "arrived"


def test_silent_run_is_stuck_only_after_the_threshold() -> None:
    fresh = flow(tasks=[task("t1", "running")], runs=[run("t1", "running", timedelta(minutes=59))])
    assert fresh["task:t1"]["stuck"] is None
    silent = flow(tasks=[task("t1", "running")], runs=[run("t1", "running", timedelta(minutes=61))])
    assert silent["task:t1"]["stuck"]["code"] == "run_silent"
    assert silent["task:t1"]["stuck"]["threshold_seconds"] == 3600


def test_pending_approval_waits_for_a_human_then_becomes_stuck() -> None:
    def approval(age: timedelta) -> of.FlowApproval:
        return of.FlowApproval("ap1", "t1", "send_email", "pending", NOW + timedelta(hours=5), NOW - age)
    young = flow(tasks=[task("t1", "waiting_approval")], approvals=[approval(timedelta(minutes=10))], can_see_approval_text=True)
    item = young["task:t1"]
    assert item["stage"] == "waiting_human" and item["stuck"] is None
    assert item["waiting"]["action"] == "send_email" and item["waiting"]["approval_id"] == "ap1"
    old = flow(tasks=[task("t1", "waiting_approval")], approvals=[approval(timedelta(hours=5))])
    assert old["task:t1"]["stuck"]["code"] == "approval_waiting"
    assert old["task:t1"]["stuck"]["action"] == "decide_approval"
    assert old["task:t1"]["waiting"]["action"] is None  # no approval:read, no action text


def test_expired_or_missing_approval_is_blocked_and_critical() -> None:
    expired = of.FlowApproval("ap1", "t1", "x", "expired", ago(hours=1), ago(hours=30))
    item = flow(tasks=[task("t1", "waiting_approval")], approvals=[expired])["task:t1"]
    assert item["stage"] == "blocked" and item["stuck"]["code"] == "approval_expired" and item["stuck"]["severity"] == "critical"
    missing = flow(tasks=[task("t1", "waiting_approval")])["task:t1"]
    assert missing["stuck"]["code"] == "approval_missing"


def test_failed_task_is_blocked_with_the_recorded_error_inside_the_window_only() -> None:
    item = flow(tasks=[task("t1", "failed", age=timedelta(hours=2))], runs=[run("t1", "failed", result="boom")])["task:t1"]
    assert item["stage"] == "blocked" and item["stuck"]["code"] == "run_failed" and item["stuck"]["facts"]["error"] == "boom"
    assert "task:t1" not in flow(tasks=[task("t1", "failed", age=timedelta(hours=25))])


def test_queued_task_is_stuck_after_the_queue_threshold_and_paused_agent_overrides() -> None:
    assert flow(tasks=[task("t1", "queued", age=timedelta(minutes=29))])["task:t1"]["stuck"] is None
    assert flow(tasks=[task("t1", "queued", age=timedelta(minutes=31))])["task:t1"]["stuck"]["code"] == "queued_too_long"
    paused = flow(agents=[of.FlowAgent("a1", "Ada", "paused")], tasks=[task("t1", "queued")])["task:t1"]
    assert paused["stuck"]["code"] == "agent_paused" and paused["stage"] == "blocked"


def test_recent_completion_leaves_a_done_marker_then_disappears() -> None:
    assert flow(tasks=[task("t1", "completed", age=timedelta(minutes=5))])["task:t1"]["stage"] == "done"
    assert "task:t1" not in flow(tasks=[task("t1", "completed", age=timedelta(minutes=31))])
    assert "task:t1" not in flow(tasks=[task("t1", "cancelled")])


def test_unlocated_items_say_why() -> None:
    assert flow(tasks=[task("t1", "queued", agent=None)])["task:t1"]["location_reason"] == "plain_runtime"
    no_desk = flow(desks=[], tasks=[task("t1", "queued")])["task:t1"]
    assert no_desk["location"] is None and no_desk["location_reason"] == "agent_has_no_desk"


def test_task_of_a_hidden_agent_is_never_shown() -> None:
    assert "task:t1" not in flow(tasks=[task("t1", "queued", agent="private_agent")])


def lead(lid: str, status: str, owner: str | None, created: timedelta, updated: timedelta | None = None) -> of.FlowLead:
    return of.FlowLead(lid, "Acme", status, owner, NOW - created, NOW - (updated if updated is not None else created))


def test_new_lead_without_owner_is_stuck_after_threshold_and_has_no_office() -> None:
    items = flow(leads=[lead("l1", "new", None, timedelta(hours=5)), lead("l2", "new", None, timedelta(minutes=10))])
    assert items["lead:l1"]["stuck"]["code"] == "lead_unassigned" and items["lead:l1"]["location"] is None
    assert items["lead:l1"]["location_reason"] == "no_owner" and items["lead:l2"]["stuck"] is None


def test_lead_owned_by_an_agent_with_a_desk_sits_in_that_office() -> None:
    item = flow(leads=[lead("l1", "qualifying", "a1", timedelta(hours=1))])["lead:l1"]
    assert item["stage"] == "in_office" and item["location"]["room_id"] == "r1" and item["stuck"] is None
    person = flow(leads=[lead("l1", "qualifying", "user_9", timedelta(hours=1))])["lead:l1"]
    assert person["location"] is None and person["location_reason"] == "owner_is_person"


def test_idle_open_lead_is_stuck_and_closed_lead_is_only_a_recent_marker() -> None:
    idle = flow(leads=[lead("l1", "quoted", "a1", timedelta(days=9), timedelta(days=4))])["lead:l1"]
    assert idle["stuck"]["code"] == "lead_idle"
    assert "lead:l1" not in flow(leads=[lead("l1", "won", "a1", timedelta(days=9), timedelta(hours=2))])


def test_order_past_ship_by_is_critical_and_never_placed_in_an_office() -> None:
    order = of.FlowOrder("o1", "X-1", "unshipped", ago(days=2), ago(hours=1), None)
    item = flow(orders=[order])["order:o1"]
    assert item["stuck"]["code"] == "order_ship_overdue" and item["location"] is None
    assert item["location_reason"] == "no_order_office"
    ontime = of.FlowOrder("o2", "X-2", "unshipped", ago(hours=1), NOW + timedelta(days=1), None)
    assert flow(orders=[ontime])["order:o2"]["stuck"] is None


def test_rollup_totals_and_ordering_put_critical_first() -> None:
    items = [task("t1", "failed"), task("t2", "queued", age=timedelta(minutes=40)), task("t3", "running")]
    out = flow(tasks=items, runs=[run("t3", "running")])["_"]
    assert [i["key"] for i in out["items"]][:2] == ["task:t1", "task:t2"]
    room = out["rooms"]["r1"]
    assert room["items"] == 3 and room["stuck"] == 2 and room["worst"] == "critical"
    assert out["totals"]["stuck"] == 2 and out["totals"]["critical"] == 1


def test_diff_reports_new_changed_and_gone_items() -> None:
    before = {i["key"]: i for i in of.derive_flow(of.FlowEvidence(
        as_of=NOW, agents=[of.FlowAgent("a1", "Ada", "active")], desks=[desk()], tasks=[task("t1", "queued")]))["items"]}
    after = of.derive_flow(of.FlowEvidence(
        as_of=NOW, agents=[of.FlowAgent("a1", "Ada", "active")], desks=[desk()],
        tasks=[task("t1", "running"), task("t2", "queued")], runs=[run("t1", "running")]))["items"]
    changed, gone = of.diff_flow(before, after)
    assert sorted(i["key"] for i in changed) == ["task:t1", "task:t2"] and gone == []
    assert of.diff_flow({i["key"]: i for i in after}, [])[1] == ["task:t1", "task:t2"]


def test_build_team_lists_only_visible_agents_and_counts_hidden_and_vacant() -> None:
    profile = SimpleNamespace(
        id="a1", name="Ada", icon="bot", color="#112233", category="work", department="Sales", description="x" * 300,
        status="active", allowed_skills=["s1", "s2"], allowed_tools=["t1"])
    desks = [desk("a1", "r1"), desk("ghost", "r2"), desk(None, "r3")]
    presence = {"a1": {"state": "working", "provenance": {"reason": None}, "last_verified_at": NOW.isoformat(),
                       "stale": False, "task": {"id": "t1", "title": "T"}}}
    team = of.build_team(profiles=[profile], desks=desks, all_desks_in_scope=desks, presence=presence,
                         stuck_by_agent={}, skills={"a1": [{"id": "s1", "name": "S1", "state": "ready", "reason": None, "source": "builtin"}]})
    assert [m["name"] for m in team["members"]] == ["Ada"] and len(team["members"][0]["description"]) <= 240
    assert team["counts"]["hidden"] == 1 and team["counts"]["vacant_desks"] == 1 and team["counts"]["working"] == 1
    assert team["members"][0]["skills_total"] == 2 and team["members"][0]["skills"][0]["state"] == "ready"
    assert team["members"][0]["avatar"] is None  # nothing chosen: the scene draws the default from the id


def test_build_team_carries_the_chosen_avatar_style_and_nothing_derived_from_the_name() -> None:
    from anum_api.avatar_appearance import AvatarAppearance

    chosen = SimpleNamespace(
        id="a1", name="Ada", icon="bot", color="#112233", category="work", department="Sales", description="",
        status="active", allowed_skills=[], allowed_tools=[], avatar=AvatarAppearance(avatar_style="neutral", outfit="uniform"))
    desks = [desk("a1", "r1")]
    team = of.build_team(profiles=[chosen], desks=desks, all_desks_in_scope=desks, presence={}, stuck_by_agent={}, skills=None)
    assert team["members"][0]["avatar"] == {"avatar_style": "neutral", "outfit": "uniform"}


def test_memory_backend_fails_closed_for_every_flow_route() -> None:
    client = TestClient(app)
    h = {"x-tenant-id": "tenant_local", "x-workspace-id": "workspace_foundation", "x-user-id": "u", "x-user-roles": "owner"}
    for path in ("/api/v1/office/flow", "/api/v1/office/team", "/api/v1/office/flow/items/task/t1/trail"):
        assert client.get(path, headers=h).status_code == 503, path
