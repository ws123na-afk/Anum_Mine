"""F09 work history: real audit evidence, safe pagination and honest replay semantics."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from anum_api.audit import AuditRecord
from anum_api.office_history_api import (
    EVIDENCE_NOTICE, HistoryCategory, HistoricalOfficeState, classify_action,
    evidence, normalized_time, read_page,
)
from anum_api.schemas import TenantContext


BASE = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)


def context(tenant: str = "tenant_a", workspace: str = "workspace_a") -> TenantContext:
    return TenantContext(tenant_id=tenant, workspace_id=workspace, user_id="owner", roles=["owner"])


def audit(number: int, action: str, *, workspace: str = "workspace_a") -> AuditRecord:
    return AuditRecord(
        id=f"audit_{number:03}", tenant_id="tenant_a", workspace_id=workspace,
        actor="owner", action=action, target=f"task_{number}",
        outcome="success", correlation_id=f"request_{number // 2}",
        created_at=BASE - timedelta(minutes=number),
        metadata={"task_id": f"task_{number}", "api_token": "confidential"},
    )


class FakeRepository:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def list_audit_page(self, ctx, filters, *, limit, after=None):
        self.calls.append((ctx, filters, limit, after))
        candidates = [
            r for r in self.rows if r.tenant_id == ctx.tenant_id
            and r.workspace_id == ctx.workspace_id
            and (filters.actor is None or r.actor == filters.actor)
            and (filters.target is None or r.target == filters.target)
            and (filters.correlation_id is None or r.correlation_id == filters.correlation_id)
            and (filters.until is None or r.created_at < filters.until)
            and (after is None or (r.created_at, r.id) < after)
        ]
        return sorted(candidates, key=lambda r: (r.created_at, r.id), reverse=True)[:limit]


@pytest.mark.parametrize(("action", "expected"), [
    ("task.created", "task"),
    ("agent_run.started", "agent_run"),
    ("approval.approved", "approval"),
    ("agent_meeting.state_change", "meeting"),
    ("office.room.create", "office"),
    ("work_item.assigned", "team_work"),
    ("workspace.archived", "workspace"),
    ("workspace_member.added", "membership"),
    ("automation.run", "automation"),
    ("autonomy.action_taken", "autonomy"),
    ("random.unknown", "other"),
    ("", "other"),
])
def test_classification_never_guesses_runtime_state(action, expected):
    assert classify_action(action).value == expected


def test_history_uses_verified_records_and_redacts_secrets():
    record = audit(0, "task.created")
    result = evidence(record)
    assert result.id == record.id
    assert result.timestamp == BASE
    assert result.source == "workspace_audit"
    assert result.verified
    assert result.metadata["api_token"] == "[REDACTED]"
    assert result.category == HistoryCategory.TASK


def test_cursor_pages_are_stable_without_overlap():
    rows = [audit(i, "task.created") for i in range(12)]
    repo = FakeRepository(rows)
    ids = []
    cursor = None
    for _ in range(4):
        response = read_page(repo, context(), limit=4, cursor=cursor, category=HistoryCategory.ALL)
        ids.extend(item.id for item in response.items)
        cursor = response.next_cursor
    assert ids == [r.id for r in rows]
    assert cursor is None


def test_filtered_page_still_advances_underlying_cursor():
    repo = FakeRepository([
        audit(0, "approval.requested"),
        audit(1, "task.created"),
        audit(2, "approval.approved"),
        audit(3, "task.completed"),
        audit(4, "task.failed"),
    ])
    first = read_page(repo, context(), limit=2, cursor=None, category=HistoryCategory.TASK)
    assert [item.action for item in first.items] == ["task.created"]
    assert first.next_cursor is not None
    second = read_page(repo, context(), limit=2, cursor=first.next_cursor, category=HistoryCategory.TASK)
    assert [item.action for item in second.items] == ["task.completed"]
    assert second.next_cursor is not None
    third = read_page(repo, context(), limit=2, cursor=second.next_cursor, category=HistoryCategory.TASK)
    assert [item.action for item in third.items] == ["task.failed"]


def test_tenant_and_workspace_context_are_never_widened():
    records = [audit(0, "task.created"), audit(1, "task.created", workspace="workspace_b")]
    repo = FakeRepository(records)
    result = read_page(repo, context(), limit=20, cursor=None, category=HistoryCategory.ALL)
    assert len(result.items) == 1
    assert result.items[0].id == "audit_000"
    assert repo.calls[0][0].workspace_id == "workspace_a"


def test_correlation_filter_is_forwarded_without_client_scope():
    repo = FakeRepository([audit(i, "task.updated") for i in range(5)])
    response = read_page(repo, context(), limit=5, cursor=None,
                         category=HistoryCategory.ALL, correlation_id="request_1")
    assert [r.id for r in response.items] == ["audit_002", "audit_003"]
    assert repo.calls[0][1].correlation_id == "request_1"


def test_until_is_exclusive():
    repo = FakeRepository([audit(0, "task.created"), audit(1, "task.completed")])
    result = read_page(repo, context(), limit=5, cursor=None,
                       category=HistoryCategory.ALL, until=BASE)
    assert [r.id for r in result.items] == ["audit_001"]


def test_replay_declares_unknown_occupancy_and_agent_presence():
    snapshot = HistoricalOfficeState(
        requested_at=BASE, workspace_id="workspace_a", known_events=[],
        action_counts={},
    )
    assert snapshot.historical_room_occupancy == "unknown"
    assert snapshot.historical_agent_presence == "unknown"
    assert snapshot.historical_layout == "unknown"
    assert snapshot.evidence_limited
    assert "missing record" in snapshot.evidence_notice.lower()


def test_no_naive_timestamp_is_accepted():
    with pytest.raises(ValueError):
        normalized_time(datetime(2026, 10, 20, 10, 0))


def test_limits_reject_invalid_sizes():
    for count in (-1, 0, 201, 99999):
        with pytest.raises(ValueError):
            read_page(FakeRepository([]), context(), limit=count,
                      cursor=None, category=HistoryCategory.ALL)


def test_invalid_cursor_is_rejected():
    with pytest.raises(HTTPException) as exc:
        read_page(FakeRepository([]), context(), limit=5,
                  cursor="not_a_valid_cursor", category=HistoryCategory.ALL)
    assert exc.value.status_code == 400


def test_extra_pydantic_fields_rejected():
    with pytest.raises(ValidationError):
        HistoricalOfficeState.model_validate({
            "requested_at": BASE, "workspace_id": "workspace_a",
            "known_events": [], "action_counts": {}, "invented_status": "working",
        })
