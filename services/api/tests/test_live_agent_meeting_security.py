"""Pure and fail-closed tests for the meeting security slice: S-02 monthly cap, S-03 owner-only
transcripts, S-04 audit without text. No database and no model (the PostgreSQL side is in
``test_postgres_live_agent_meeting_security.py``)."""
from __future__ import annotations

import importlib.util
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from anum_api import live_agent_meeting_budget as budget
from anum_api.live_agent_meeting_api import BudgetUpdate, TRANSCRIPT_DENIED, _error
from anum_api.live_agent_meeting_audit import MAX_TEXT, safe_metadata
from anum_api.live_agent_meeting_room_api import bundle_for
from anum_api.live_agent_meeting_store import LiveMeetingStoreError
from anum_api.localization import localize_message
from anum_api.main import app
from anum_api.settings import settings

client = TestClient(app)
VERSIONS = Path(__file__).parents[1] / "migrations" / "versions"
BASE = "/api/v1/agent-meetings/m1"


def headers(role="owner", user="room_owner"):
    return {"x-tenant-id": "room_tenant", "x-workspace-id": "room_workspace", "x-user-id": user, "x-user-roles": role}


def state(used="0", limit="50", in_flight=0, reserve="0.10", default=True):
    return budget.BudgetState(
        limit_usd=Decimal(limit), is_default=default, used_usd=Decimal(used), turns=3, in_flight=in_flight,
        reserve_usd=Decimal(reserve), period_start=date(2026, 10, 1), resets_on=date(2026, 11, 1), updated_by=None, updated_at=None)


# ----------------------------------------------------------------------------- S-02: the cap

def test_the_default_cap_is_fifty_dollars():
    assert budget.DEFAULT_MONTHLY_CAP_USD == Decimal("50")
    assert state().view()["limit_usd"] == 50.0 and state().view()["default_limit_usd"] == 50.0


def test_period_is_the_utc_calendar_month_and_rolls_over_the_year():
    assert budget.period_bounds(datetime(2026, 10, 25, 23, 59, tzinfo=UTC)) == (date(2026, 10, 1), date(2026, 11, 1))
    assert budget.period_bounds(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == (date(2026, 12, 1), date(2027, 1, 1))
    assert budget.period_bounds(datetime(2027, 1, 1, 0, 0, tzinfo=UTC))[0] == date(2027, 1, 1)


@pytest.mark.parametrize(("used", "limit", "flight", "blocked"), [
    ("0", "50", 0, False),
    ("49.99", "50", 0, False),       # the last turn may start, and may end above the cap by its own cost
    ("50", "50", 0, True),
    ("12", "0", 0, True),            # a zero cap blocks everything
    ("49.95", "50", 1, True),        # a running turn is reserved at USD 0.10, so no second one starts
    ("49.80", "50", 1, False),
    ("49.80", "50", 2, True),
])
def test_a_turn_starts_only_while_the_budget_covers_the_turns_already_running(used, limit, flight, blocked):
    assert state(used, limit, flight).exceeded is blocked


def test_the_reserve_follows_the_largest_recent_turn_but_never_drops_below_ten_cents():
    assert state("49", "50", 1, reserve="0.40").exceeded is False
    assert state("49.7", "50", 1, reserve="0.40").exceeded is True
    assert budget.RESERVE_FLOOR_USD == Decimal("0.10")


def test_the_view_says_what_was_used_and_why_nothing_runs():
    open_view = state("10").view()
    assert open_view["remaining_usd"] == 40.0 and open_view["percent"] == 20.0 and open_view["message"] is None
    full = state("50").view()
    assert full["exceeded"] is True and full["remaining_usd"] == 0.0 and full["resets_on"] == "2026-11-01"
    assert full["message"].startswith("Monthly agent meeting budget reached")
    assert "2026-11-01" in full["message"] and "Budgets" in full["message"]


def test_the_refusal_is_a_402_with_its_own_error_code_and_a_plain_arabic_form():
    refusal = _error(budget.MeetingBudgetReached(state("50")))
    assert refusal.status_code == 402 and refusal.code == "agent_meeting_budget_exceeded"
    arabic = localize_message(str(refusal), "ar")
    assert arabic != str(refusal) and "ميزانية اجتماعات الوكلاء" in arabic and "50.00" in arabic and "2026-11-01" in arabic


def test_transcript_denial_has_an_arabic_form():
    assert "مالك مساحة العمل" in localize_message(TRANSCRIPT_DENIED, "ar")


def test_other_store_errors_keep_their_codes():
    assert _error(LiveMeetingStoreError("conflict", "x")).status_code == 409
    assert _error(LiveMeetingStoreError("forbidden", "x")).status_code == 403


@pytest.mark.parametrize("value", [-1, 100001, "12.345", "abc"])
def test_the_cap_body_rejects_bad_numbers(value):
    with pytest.raises(ValidationError):
        BudgetUpdate.model_validate({"monthly_cost_limit_usd": value})
    with pytest.raises(ValidationError):
        BudgetUpdate.model_validate({"monthly_cost_limit_usd": 5, "extra": 1})


def test_the_cap_body_accepts_cents_zero_and_null_for_the_default():
    assert BudgetUpdate.model_validate({"monthly_cost_limit_usd": "12.50"}).monthly_cost_limit_usd == Decimal("12.50")
    assert BudgetUpdate.model_validate({"monthly_cost_limit_usd": 0}).monthly_cost_limit_usd == 0
    assert BudgetUpdate.model_validate({"monthly_cost_limit_usd": None}).monthly_cost_limit_usd is None


def test_only_owners_may_change_the_cap(monkeypatch):
    monkeypatch.setattr(settings, "repository_backend", "memory")
    body = {"monthly_cost_limit_usd": 10}
    for role in ("member", "viewer"):
        assert client.put("/api/v1/agent-meetings/budget", headers=headers(role), json=body).status_code == 403
    # An owner passes the permission check and then meets the missing PostgreSQL backend.
    assert client.put("/api/v1/agent-meetings/budget", headers=headers("owner"), json=body).status_code == 503


def test_any_member_may_read_the_budget_route_which_is_not_a_meeting_id(monkeypatch):
    monkeypatch.setattr(settings, "repository_backend", "memory")
    assert client.get("/api/v1/agent-meetings/budget", headers=headers("member")).status_code == 503  # not 404/403


# ----------------------------------------------------------------------------- S-03: owner-only text

READS = ("/turns", "/events", "/events/stream", "/export", "/export?format=json")


@pytest.mark.parametrize("path", READS)
@pytest.mark.parametrize("role", ["member", "viewer"])
def test_non_owners_get_403_with_a_clear_message_on_every_transcript_read(monkeypatch, path, role):
    monkeypatch.setattr(settings, "repository_backend", "memory")
    response = client.get(BASE + path, headers=headers(role))
    assert response.status_code == 403
    assert response.json()["error"]["message"] == TRANSCRIPT_DENIED


@pytest.mark.parametrize("path", READS)
def test_owners_pass_the_transcript_gate(monkeypatch, path):
    monkeypatch.setattr(settings, "repository_backend", "memory")
    assert client.get(BASE + path, headers=headers("owner")).status_code == 503  # past the gate, no PostgreSQL here


def test_a_denial_message_is_arabic_for_arabic_requests(monkeypatch):
    monkeypatch.setattr(settings, "repository_backend", "memory")
    response = client.get(BASE + "/turns", headers={**headers("member"), "accept-language": "ar"})
    assert response.status_code == 403 and "مالك مساحة العمل" in response.json()["error"]["message"]


def sample_bundle():
    return {"meeting": {"id": "m1", "title": "Plan", "status": "finished"}, "participants": [{"actor_id": "a"}],
            "attendance": [], "agenda": [{"title": "Budget"}], "speaking": [], "last_event_seq": 4, "transcript_deleted": False,
            "outputs": [{"id": "o1", "body": "Launch on Tuesday"}, {"id": "o2", "body": "Call the bank"}],
            "files": [{"file_id": "f1", "name": "plan.pdf"}], "viewer": {"user_id": "u", "is_owner": False}}


def test_the_room_bundle_keeps_who_and_when_but_drops_the_text_for_non_owners():
    seen = bundle_for(sample_bundle(), False)
    assert seen["transcript_hidden"] is True and seen["viewer"]["can_read_transcript"] is False
    assert seen["outputs"] == [] and seen["outputs_count"] == 2 and seen["files"] == [] and seen["files_count"] == 1
    assert seen["meeting"]["title"] == "Plan" and seen["participants"] and seen["last_event_seq"] == 4
    assert "Launch on Tuesday" not in json.dumps(seen) and "plan.pdf" not in json.dumps(seen)


def test_owners_get_the_whole_bundle():
    full = bundle_for(sample_bundle(), True)
    assert full["viewer"]["can_read_transcript"] is True and len(full["outputs"]) == 2 and "transcript_hidden" not in full


# ----------------------------------------------------------------------------- S-04: audit without text

def test_audit_metadata_is_an_allow_list_of_short_scalars():
    secret = "the board will fire the CFO on Friday"
    meta = safe_metadata(meeting_id="m1", turn_number=3, agent_id="a1", body=secret, text=secret, prompt=secret,
                         message=secret, content=secret, transcript=secret, cost_usd=Decimal("0.0021"),
                         input_count=10, output_count=20, redacted=False, reason="x" * 500, nested={"body": secret})
    assert secret not in json.dumps(meta, default=str)
    assert set(meta) == {"resource", "meeting_id", "turn_number", "agent_id", "cost_usd", "input_count", "output_count", "redacted", "reason"}
    assert len(meta["reason"]) == MAX_TEXT and meta["turn_number"] == 3 and meta["redacted"] is False
    assert meta["cost_usd"] == "0.0021"  # decimals travel as text so the JSON stays exact


def test_no_audit_field_name_looks_like_a_secret_key_that_would_be_redacted():
    from anum_api.audit import _is_secret_key
    from anum_api.live_agent_meeting_audit import ALLOWED_FIELDS
    assert [name for name in ALLOWED_FIELDS if _is_secret_key(name)] == []


# ----------------------------------------------------------------------------- migration

def test_migration_is_expand_only_chained_scoped_and_forced_rls():
    path = VERSIONS / "0107_agent_meeting_budget.py"
    spec = importlib.util.spec_from_file_location("m0107", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert len(module.revision) <= 32 and module.down_revision == "0106_agent_provisioning"
    source = path.read_text(encoding="utf-8")
    upgrade = source.split("def upgrade() -> None:", 1)[1].split("def downgrade() -> None:", 1)[0]
    for forbidden in ("drop_", "alter_column", "rename", "drop table"):
        assert forbidden not in upgrade
    for table in module.TABLES:
        assert table in upgrade
    rls = source.split("def _rls", 1)[1].split("def upgrade", 1)[0]
    assert "force row level security" in rls and "tenant_isolation_" in rls and "lifecycle_purge_" in rls
    heads = [p for p in VERSIONS.glob("*.py") if 'down_revision = "0106_agent_provisioning"' in p.read_text(encoding="utf-8")]
    assert heads == [path]  # one head: nothing else extends 0106
