"""Safety and contract tests for the meeting executor.

Database integration and provider-based tests are still required before release.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from anum_api.live_agent_meeting_store import LiveMeetingStoreError
from anum_api.meeting_execution import _check, _request


def session(**changes):
    base = dict(created_by="owner", status="active",
                started_at=datetime.now(timezone.utc),
                max_duration_seconds=1800, total_cost_usd=Decimal("0.2"),
                max_cost_usd=Decimal("2"))
    base.update(changes)
    return base


class Context:
    tenant_id="org_1"
    workspace_id="workspace_1"
    user_id="owner"


def test_scope_is_server_supplied():
    assert _request(Context(), "meeting1", "requestkey", seq=4) == {
        "tenant":"org_1","workspace":"workspace_1","id":"meeting1",
        "request":"requestkey","seq":4,
    }


def test_owner_required():
    with pytest.raises(LiveMeetingStoreError) as exc:
        _check(session(), type("Other",(),{"user_id":"other"})())
    assert exc.value.code == "forbidden"


@pytest.mark.parametrize("state",["scheduled","paused","cancelled","failed","finished"])
def test_only_active_sessions_execute(state):
    with pytest.raises(LiveMeetingStoreError,match="active"):
        _check(session(status=state),Context())


def test_duration_limit():
    past=datetime.now(timezone.utc)-timedelta(hours=2)
    with pytest.raises(LiveMeetingStoreError,match="duration"):
        _check(session(started_at=past),Context())


def test_cost_limit():
    with pytest.raises(LiveMeetingStoreError,match="cost"):
        _check(session(total_cost_usd=Decimal("2")),Context())


def test_active_session_with_budget():
    _check(session(),Context())
