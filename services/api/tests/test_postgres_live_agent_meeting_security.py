"""PostgreSQL application-role tests for the meeting security slice (S-02, S-03, S-04).

Everything runs as the RLS-bound application role against real tables. The model is a
deterministic fake; nothing here calls a provider.
"""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api import live_agent_meeting_api as meeting_api
from anum_api import live_agent_meeting_budget as budget
from anum_api import live_agent_meeting_room as room
from anum_api.db import session as db_session
from anum_api.live_agent_meeting_store import LiveMeetingStoreError, SqlLiveMeetingStore
from anum_api.main import app
from anum_api.meeting_execution import execute_meeting_turn
from anum_api.model_gateway import ModelResponse, ModelUsage
from anum_api.schemas import TenantContext
from anum_api.settings import settings

from test_postgres_live_agent_meeting_room import make_meeting, read, scoped, turns

pytestmark = pytest.mark.database
BODY = "The board will approve the secret acquisition on Friday."


@pytest.fixture
def factory(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator:
    maker = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(maker, "after_begin")
    def _app_role(session, transaction, connection):
        connection.execute(text(f"set local role {APP_ROLE}"))
    monkeypatch.setattr(db_session, "SessionLocal", maker)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    yield maker


class Gateway:
    provider = "fake"

    def __init__(self, cost=0.001, delay=0.0, fail=False):
        self.cost, self.delay, self.fail, self.calls = cost, delay, fail, 0

    async def generate_text(self, prompt: str) -> ModelResponse:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError(BODY)
        return ModelResponse(text=BODY, usage=ModelUsage(input_tokens=10, output_tokens=20, provider="fake", model="fake-1",
                                                         estimated_cost_usd=self.cost))


def ctx(tenant=TENANT_A, workspace=WORKSPACE_A, user="room_owner", roles=("owner",)):
    return TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=user, roles=list(roles))


def run_turn(context, meeting_id, key, gateway):
    return asyncio.run(execute_meeting_turn(context, meeting_id, key, gateway))


def set_cap(factory, context, limit):
    return read(factory, context, lambda s: budget.set_limit(s, context, None if limit is None else Decimal(str(limit))))


def state_of(factory, context, now=None):
    return read(factory, context, lambda s: budget.read_state(s, context, now))


def audit_rows(factory, context, action):
    with scoped(factory, context) as session:
        rows = session.execute(text("""select actor, action, target, outcome, correlation_id, metadata from audit_records
            where tenant_id=:t and workspace_id=:w and action=:a order by created_at, id"""),
            {"t": context.tenant_id, "w": context.workspace_id, "a": action}).mappings().all()
        session.commit()
    return [dict(r) for r in rows]


def event_kinds(factory, context, meeting_id):
    return read(factory, context, lambda s: [e for e in room.events_after(s, context, meeting_id, 0, 500)])


# ----------------------------------------------------------------------------- S-02 cap

def test_default_cap_is_fifty_and_a_turn_adds_to_the_month(factory):
    c = ctx()
    before = state_of(factory, c)
    assert before.limit_usd == Decimal("50") and before.is_default and before.used_usd == 0
    mid = make_meeting(factory)
    run_turn(c, mid, "default-cap-one", Gateway(cost=0.25))
    after = state_of(factory, c)
    assert after.used_usd == Decimal("0.25") and after.turns == 1 and after.remaining_usd == Decimal("49.75")


def test_the_owner_changes_the_cap_and_it_resets_to_the_default(factory):
    c = ctx()
    before, after = set_cap(factory, c, 12.5)
    assert before.limit_usd == 50 and after.limit_usd == Decimal("12.5") and not after.is_default and after.updated_by == "room_owner"
    assert state_of(factory, c).limit_usd == Decimal("12.5")
    assert state_of(factory, ctx(workspace=WORKSPACE_A2)).limit_usd == 50  # per workspace
    set_cap(factory, c, None)
    assert state_of(factory, c).is_default


def test_a_turn_over_the_cap_is_refused_before_the_model_is_called_and_the_meeting_pauses(factory):
    c = ctx()
    set_cap(factory, c, 0.003)
    first, second = make_meeting(factory), make_meeting(factory)
    gateway = Gateway(cost=0.002)
    run_turn(c, first, "cap-hit-0001", gateway)
    # 0.002 is under 0.003: this turn may start, and it ends above the cap by less than one turn.
    run_turn(c, second, "cap-hit-0002", gateway)
    assert state_of(factory, c).used_usd == Decimal("0.004")
    assert read(factory, c, lambda s: s.get(c, second)["status"]) == "paused"  # the turn that used the last of the month pauses it
    assert "budget_reached" in [e["kind"] for e in event_kinds(factory, c, second)]
    calls = gateway.calls
    with pytest.raises(budget.MeetingBudgetReached, match="Monthly agent meeting budget reached"):
        run_turn(c, first, "cap-hit-0003", gateway)
    assert gateway.calls == calls  # refused before any model spend
    assert read(factory, c, lambda s: s.get(c, first)["status"]) == "paused"
    blocked = [r for r in audit_rows(factory, c, "agent_meeting.turn") if r["outcome"] == "blocked"]
    assert len(blocked) == 1 and blocked[0]["metadata"]["reason"] == "monthly_budget" and blocked[0]["metadata"]["meeting_id"] == first


def test_a_paused_meeting_cannot_resume_at_the_cap_and_can_after_the_limit_is_raised(factory, monkeypatch):
    c = ctx()
    set_cap(factory, c, 0.001)
    mid = make_meeting(factory)
    run_turn(c, mid, "resume-cap-01", Gateway(cost=0.002))
    assert read(factory, c, lambda s: s.get(c, mid)["status"]) == "paused"
    http = TestClient(app)
    h = {"x-tenant-id": c.tenant_id, "x-workspace-id": c.workspace_id, "x-user-id": c.user_id, "x-user-roles": "owner"}
    revision = read(factory, c, lambda s: s.get(c, mid)["revision"])
    denied = http.post(f"/api/v1/agent-meetings/{mid}/state", headers={**h, "accept-language": "en"}, json={"status": "active", "expected_revision": revision})
    assert denied.status_code == 402 and denied.json()["error"]["code"] == "agent_meeting_budget_exceeded"
    assert denied.json()["error"]["message"].startswith("Monthly agent meeting budget reached")
    arabic = http.post(f"/api/v1/agent-meetings/{mid}/state", headers={**h, "accept-language": "ar"}, json={"status": "active", "expected_revision": revision})
    assert "ميزانية اجتماعات الوكلاء" in arabic.json()["error"]["message"]
    assert http.put("/api/v1/agent-meetings/budget", headers=h, json={"monthly_cost_limit_usd": 5}).status_code == 200
    ok = http.post(f"/api/v1/agent-meetings/{mid}/state", headers=h, json={"status": "active", "expected_revision": revision})
    assert ok.status_code == 200 and ok.json()["status"] == "active"


def test_deleting_a_transcript_does_not_hand_the_budget_back(factory):
    c = ctx()
    mid = make_meeting(factory)
    run_turn(c, mid, "delete-keeps-01", Gateway(cost=0.4))
    read(factory, c, lambda s: s.transition(c, mid, s.get(c, mid)["revision"], "finished"))
    read(factory, c, lambda s: room.delete_transcript(s, c, mid))
    assert turns(factory, c, mid) == []
    assert state_of(factory, c).used_usd == Decimal("0.4")


def test_a_new_month_starts_at_zero(factory):
    c = ctx()
    run_turn(c, make_meeting(factory), "new-month-0001", Gateway(cost=0.3))
    assert state_of(factory, c).used_usd == Decimal("0.3")
    future = datetime(2099, 1, 5, tzinfo=UTC)
    assert state_of(factory, c, future).used_usd == 0 and state_of(factory, c, future).period_start.isoformat() == "2099-01-01"


def test_a_running_turn_is_reserved_so_a_second_meeting_cannot_take_the_last_of_the_budget(factory):
    c = ctx()
    set_cap(factory, c, 0.05)  # under the USD 0.10 reserve: room for one turn at a time
    a, b = make_meeting(factory), make_meeting(factory)
    outcome: dict[str, object] = {}

    class Slow(Gateway):
        async def generate_text(self, prompt):
            # Meeting A's lease is committed and its model call is running: B asks now.
            def second():
                try:
                    run_turn(c, b, "reserve-b-001", Gateway())
                    outcome["b"] = "ran"
                except budget.MeetingBudgetReached:
                    outcome["b"] = "blocked"
            worker = threading.Thread(target=second)
            worker.start()
            worker.join(30)
            return await super().generate_text(prompt)
    run_turn(c, a, "reserve-a-001", Slow())
    assert outcome["b"] == "blocked"
    assert state_of(factory, c).used_usd == Decimal("0.001")  # only A was paid for


def test_parallel_meetings_cannot_both_claim_the_last_of_the_budget(factory):
    """Two meetings ask at the same instant with room for one turn: the workspace lock makes
    exactly one win and the other see its lease."""
    c = ctx()
    set_cap(factory, c, 0.10)
    ids = [make_meeting(factory), make_meeting(factory)]
    results: list[str] = []
    guard = threading.Lock()

    def go(mid, key):
        try:
            run_turn(c, mid, key, Gateway(delay=0.8))
            outcome = "ran"
        except budget.MeetingBudgetReached:
            outcome = "blocked"
        with guard:
            results.append(outcome)
    workers = [threading.Thread(target=go, args=(mid, f"race-key-{n:04d}")) for n, mid in enumerate(ids)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(60)
    assert sorted(results) == ["blocked", "ran"]
    assert state_of(factory, c).used_usd == Decimal("0.001")


def test_with_room_for_both_parallel_meetings_run(factory):
    c = ctx()
    ids = [make_meeting(factory), make_meeting(factory)]
    results: list[str] = []

    def go(mid, key):
        run_turn(c, mid, key, Gateway(delay=0.5))
        results.append("ran")
    workers = [threading.Thread(target=go, args=(mid, f"both-key-{n:04d}")) for n, mid in enumerate(ids)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(60)
    assert results == ["ran", "ran"] and state_of(factory, c).turns == 2


def test_workspaces_and_tenants_have_their_own_budgets(factory):
    a, a2, b = ctx(), ctx(workspace=WORKSPACE_A2), ctx(TENANT_B, WORKSPACE_B)
    set_cap(factory, a, 1)
    run_turn(a, make_meeting(factory, a), "own-budget-01", Gateway(cost=0.5))
    assert state_of(factory, a).used_usd == Decimal("0.5")
    assert state_of(factory, a2).used_usd == 0 and state_of(factory, b).used_usd == 0
    assert state_of(factory, b).limit_usd == 50


def test_rls_hides_budget_rows_from_other_scopes(factory):
    set_cap(factory, ctx(), 7)
    run_turn(ctx(), make_meeting(factory), "rls-budget-001", Gateway())
    for other in (ctx(workspace=WORKSPACE_A2), ctx(TENANT_B, WORKSPACE_B)):
        with scoped(factory, other) as session:
            for table in ("agent_meeting_budgets", "agent_meeting_usage_monthly"):
                assert session.execute(text(f"select count(*) from {table}")).scalar_one() == 0  # noqa: S608
            session.rollback()


# ----------------------------------------------------------------------------- S-04 audit

def test_every_executed_turn_is_audited_with_usage_and_no_text(factory):
    c = ctx()
    mid = make_meeting(factory)
    one = run_turn(c, mid, "audit-turn-001", Gateway(cost=0.0021))
    run_turn(c, mid, "audit-turn-002", Gateway(cost=0.0013))
    rows = audit_rows(factory, c, "agent_meeting.turn")
    assert len(rows) == 2 and all(r["outcome"] == "success" and r["target"] == mid and r["actor"] == "room_owner" for r in rows)
    first = rows[0]["metadata"]
    assert first["meeting_id"] == mid and first["turn_number"] == 1 and first["agent_id"] == one["speaker_id"]
    assert first["input_count"] == 10 and first["output_count"] == 20 and first["cost_usd"] == "0.0021" and first["model"] == "fake/fake-1"
    assert first["request_key"] == "audit-turn-001" and first["limit_usd"].startswith("50")
    assert rows[1]["metadata"]["turn_number"] == 2
    assert BODY not in json.dumps([dict(r, created_at=None) for r in rows], default=str) and "acquisition" not in str(rows)
    started = [e for e in event_kinds(factory, c, mid) if e["kind"] == "turn_started"]
    assert started[0]["payload"]["correlation_id"] == rows[0]["correlation_id"]  # one id ties the event to its audit record


def test_a_replayed_turn_is_not_audited_twice(factory):
    c = ctx()
    mid = make_meeting(factory)
    run_turn(c, mid, "audit-replay-01", Gateway())
    run_turn(c, mid, "audit-replay-01", Gateway())
    assert len(audit_rows(factory, c, "agent_meeting.turn")) == 1


def test_a_failed_turn_is_audited_without_the_error_text(factory):
    c = ctx()
    mid = make_meeting(factory)
    with pytest.raises(RuntimeError):
        run_turn(c, mid, "audit-fail-0001", Gateway(fail=True))
    rows = audit_rows(factory, c, "agent_meeting.turn")
    assert [r["outcome"] for r in rows] == ["failure"] and rows[0]["metadata"]["code"] == "error" and rows[0]["metadata"]["turn_number"] == 1
    assert "acquisition" not in str(rows)


def test_a_turn_by_someone_else_is_denied_and_audited(factory):
    owner, other = ctx(), ctx(user="someone_else")
    mid = make_meeting(factory)
    with pytest.raises(LiveMeetingStoreError, match="owner"):
        run_turn(other, mid, "audit-denied-01", Gateway())
    rows = audit_rows(factory, other, "agent_meeting.turn")
    assert [r["outcome"] for r in rows] == ["denied"] and rows[0]["actor"] == "someone_else" and rows[0]["metadata"]["reason"] == "not_meeting_owner"
    assert turns(factory, owner, mid) == []


# ----------------------------------------------------------------------------- S-03 over HTTP, as the app role

def headers(context, role):
    return {"x-tenant-id": context.tenant_id, "x-workspace-id": context.workspace_id, "x-user-id": context.user_id, "x-user-roles": role}


@pytest.fixture
def seeded(factory):
    c = ctx()
    mid = make_meeting(factory)
    run_turn(c, mid, "seeded-turn-001", Gateway())
    read(factory, c, lambda s: room.output_create(s, c, mid, kind="decision", body="Approve the acquisition", owner_label=None, source_sequences=[1]))
    return c, mid


def test_a_member_sees_that_the_meeting_happened_but_never_its_text(factory, seeded):
    c, mid = seeded
    http = TestClient(app)
    member = headers(ctx(user="a_member"), "member")
    listing = http.get("/api/v1/agent-meetings", headers=member)
    assert listing.status_code == 200 and [m["id"] for m in listing.json()["items"]] == [mid]
    detail = http.get(f"/api/v1/agent-meetings/{mid}", headers=member)
    assert detail.status_code == 200 and detail.json()["title"] == "Weekly plan" and detail.json()["participants"]
    bundle = http.get(f"/api/v1/agent-meetings/{mid}/room", headers=member)
    assert bundle.status_code == 200
    seen = bundle.json()
    assert seen["transcript_hidden"] is True and seen["viewer"]["can_read_transcript"] is False and seen["outputs"] == [] and seen["outputs_count"] == 1
    assert seen["meeting"]["status"] == "active" and seen["participants"]
    for response in (listing, detail, bundle):
        assert "acquisition" not in response.text
    for path in ("turns", "events", "events/stream", "export", "export?format=json"):
        denied = http.get(f"/api/v1/agent-meetings/{mid}/{path}", headers=member)
        assert denied.status_code == 403, path
        assert denied.json()["error"]["message"].startswith("Only a workspace owner can read meeting transcripts"), path
        assert "acquisition" not in denied.text


def test_viewers_are_refused_too_and_the_denials_are_audited_without_text(factory, seeded):
    c, mid = seeded
    http = TestClient(app)
    viewer = ctx(user="a_viewer")
    for path in ("turns", "events", "events/stream", "export"):
        assert http.get(f"/api/v1/agent-meetings/{mid}/{path}", headers=headers(viewer, "viewer")).status_code == 403
    rows = audit_rows(factory, viewer, "agent_meeting.transcript_denied")
    assert sorted(r["metadata"]["read_path"] for r in rows) == ["events", "export", "stream", "turns"]
    assert all(r["outcome"] == "denied" and r["actor"] == "a_viewer" and r["target"] == mid for r in rows)
    assert "acquisition" not in str(rows) and BODY not in str(rows)


def test_a_workspace_owner_reads_every_path(factory, seeded):
    c, mid = seeded
    http = TestClient(app)
    owner = headers(ctx(user="second_owner"), "owner")  # any owner, not only the one who created the meeting
    turns_response = http.get(f"/api/v1/agent-meetings/{mid}/turns", headers=owner)
    assert turns_response.status_code == 200 and turns_response.json()["items"][0]["body"] == BODY
    assert http.get(f"/api/v1/agent-meetings/{mid}/events", headers=owner).status_code == 200
    assert "acquisition" in http.get(f"/api/v1/agent-meetings/{mid}/export", headers=owner).text
    assert "acquisition" in http.get(f"/api/v1/agent-meetings/{mid}/export?format=json", headers=owner).text
    full = http.get(f"/api/v1/agent-meetings/{mid}/room", headers=owner).json()
    assert full["viewer"]["can_read_transcript"] is True and full["outputs"][0]["body"] == "Approve the acquisition"


def test_the_event_stream_is_open_for_an_owner(factory, seeded):
    c, mid = seeded
    http = TestClient(app)
    with http.stream("GET", f"/api/v1/agent-meetings/{mid}/events/stream?after=0", headers=headers(c, "owner")) as response:
        assert response.status_code == 200
        first = next(response.iter_lines())
        assert first.startswith("retry:")


def test_another_tenant_owner_cannot_see_the_meeting_at_all(factory, seeded):
    c, mid = seeded
    http = TestClient(app)
    stranger = headers(ctx(TENANT_B, WORKSPACE_B, user="stranger"), "owner")
    for path in ("", "/turns", "/events", "/export", "/room"):
        assert http.get(f"/api/v1/agent-meetings/{mid}{path}", headers=stranger).status_code == 404, path


def test_budget_routes_as_the_app_role(factory, monkeypatch):
    c = ctx()
    http = TestClient(app)
    member = headers(ctx(user="a_member"), "member")
    seen = http.get("/api/v1/agent-meetings/budget", headers=member).json()
    assert seen["limit_usd"] == 50 and seen["is_default"] is True and seen["can_manage"] is False and seen["used_usd"] == 0
    assert http.put("/api/v1/agent-meetings/budget", headers=member, json={"monthly_cost_limit_usd": 1}).status_code == 403
    assert http.put("/api/v1/agent-meetings/budget", headers=headers(c, "owner"), json={"monthly_cost_limit_usd": 1_000_000}).status_code == 422
    changed = http.put("/api/v1/agent-meetings/budget", headers=headers(c, "owner"), json={"monthly_cost_limit_usd": "20.00"})
    assert changed.status_code == 200 and changed.json()["limit_usd"] == 20 and changed.json()["can_manage"] is True
    rows = audit_rows(factory, c, "agent_meeting.budget_update")
    assert len(rows) == 1 and rows[0]["metadata"]["previous_limit_usd"].startswith("50") and rows[0]["metadata"]["new_limit_usd"].startswith("20")
    assert rows[0]["metadata"]["reason"] == "owner_set"
    http.put("/api/v1/agent-meetings/budget", headers=headers(c, "owner"), json={"monthly_cost_limit_usd": None})
    assert http.get("/api/v1/agent-meetings/budget", headers=member).json()["is_default"] is True
    assert [r["metadata"]["reason"] for r in audit_rows(factory, c, "agent_meeting.budget_update")] == ["owner_set", "reset_to_default"]


def test_execute_turn_over_http_stops_at_the_cap_with_402(factory, monkeypatch):
    c = ctx()
    set_cap(factory, c, 0)
    mid = make_meeting(factory)
    monkeypatch.setattr(meeting_api, "build_default_model_gateway", lambda: Gateway())
    http = TestClient(app)
    response = http.post(f"/api/v1/agent-meetings/{mid}/execute-turn", headers=headers(c, "owner"), json={"request_key": "http-turn-0001"})
    assert response.status_code == 402 and response.json()["error"]["code"] == "agent_meeting_budget_exceeded"
    assert read(factory, c, lambda s: s.get(c, mid)["status"]) == "paused"
    assert turns(factory, c, mid) == []
