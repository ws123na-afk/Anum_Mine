"""S-05: the per-person and per-workspace turn rate on ``execute-turn`` and ``run``. No database, no model."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from anum_api import live_agent_meeting_api as meeting_api
from anum_api import live_agent_meeting_rate as rate
from anum_api.live_agent_meeting_audit import safe_metadata
from anum_api.localization import localize_message
from anum_api.main import app
from anum_api.schemas import TenantContext
from anum_api.settings import settings

client = TestClient(app)


def ctx(user="u1", tenant="t1", workspace="w1"):
    return TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=user, roles=["owner"])


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(clock=None):
    return rate.TurnRateLimiter(clock=clock or Clock())


def test_admits_up_to_the_actor_limit_then_refuses_with_the_wait():
    clock = Clock()
    lim = make(clock)
    for _ in range(6):
        assert lim.acquire(ctx(), 1, actor_limit=6, workspace_limit=20).allowed
        clock.now += 1
    refused = lim.acquire(ctx(), 1, actor_limit=6, workspace_limit=20)
    assert (refused.allowed, refused.scope, refused.limit) == (False, "actor", 6)
    assert refused.retry_after_seconds == 54       # the oldest hit was 6 s ago, window is 60 s
    clock.now += 54
    assert lim.acquire(ctx(), 1, actor_limit=6, workspace_limit=20).allowed


def test_run_takes_all_or_nothing_and_a_refusal_consumes_nothing():
    lim = make()
    assert lim.acquire(ctx(), 3, actor_limit=4, workspace_limit=20).allowed
    assert not lim.acquire(ctx(), 3, actor_limit=4, workspace_limit=20).allowed   # 3 + 3 > 4
    assert lim.acquire(ctx(), 1, actor_limit=4, workspace_limit=20).allowed       # still one left


def test_the_workspace_window_is_shared_by_actors_but_not_by_other_workspaces_or_tenants():
    lim = make()
    for user in ("a", "b", "c", "d"):
        assert lim.acquire(ctx(user), 1, actor_limit=6, workspace_limit=4).allowed
    refused = lim.acquire(ctx("e"), 1, actor_limit=6, workspace_limit=4)
    assert (refused.allowed, refused.scope) == (False, "workspace")
    assert lim.acquire(ctx("e", workspace="w2"), 1, actor_limit=6, workspace_limit=4).allowed
    assert lim.acquire(ctx("e", tenant="t2"), 1, actor_limit=6, workspace_limit=4).allowed


def test_the_same_user_id_in_another_tenant_has_its_own_actor_window():
    lim = make()
    for _ in range(3):
        assert lim.acquire(ctx("same"), 1, actor_limit=3, workspace_limit=20).allowed
    assert not lim.acquire(ctx("same"), 1, actor_limit=3, workspace_limit=20).allowed
    assert lim.acquire(ctx("same", tenant="t2"), 1, actor_limit=3, workspace_limit=20).allowed


def test_keys_are_bounded():
    lim = rate.TurnRateLimiter(max_keys=5)
    for n in range(50):
        lim.acquire(ctx(f"u{n}"), 1, actor_limit=6, workspace_limit=20)
    assert len(lim._hits) <= 5


def test_messages_say_what_happened_and_when_to_retry_in_both_languages():
    message = rate.refusal_message(rate.TurnRateDecision(False, "actor", 6, 42))
    assert "42 seconds" in message and "(6)" in message
    arabic = localize_message(message, "ar")
    assert arabic != message and "42" in arabic and "6" in arabic
    workspace = localize_message(rate.refusal_message(rate.TurnRateDecision(False, "workspace", 20, 5)), "ar")
    assert "مساحة العمل" in workspace


def test_the_audit_metadata_keeps_counts_and_drops_text():
    meta = safe_metadata(code="rate_limited", scope="actor", requested_turns=3, limit=6, retry_after_seconds=12,
                         message="secret words", text="more secret words")
    assert meta["requested_turns"] == 3 and meta["retry_after_seconds"] == 12
    assert "message" not in meta and "text" not in meta


def test_defaults_are_safe():
    assert settings.meeting_turns_per_minute_actor == 6 and settings.meeting_turns_per_minute_workspace == 20


@pytest.fixture
def audited(monkeypatch):
    rate.limiter.reset()
    calls = []
    monkeypatch.setattr(meeting_api, "audit_in_new_transaction", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(settings, "meeting_turns_per_minute_actor", 3)
    yield calls
    rate.limiter.reset()


def hdr(role="owner", user="rate_owner"):
    return {"x-tenant-id": "rate_tenant", "x-workspace-id": "rate_workspace", "x-user-id": user, "x-user-roles": role}


def test_run_answers_429_with_retry_after_and_audits_ids_and_counts_only(audited):
    assert rate.limiter.acquire(ctx("rate_owner", "rate_tenant", "rate_workspace"), 3, actor_limit=3, workspace_limit=20).allowed
    response = client.post("/api/v1/agent-meetings/m1/run", json={"run_key": "key-12345678", "turns": 2}, headers=hdr())
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1
    body = response.json()["error"]
    assert body["code"] == "rate_limited" and "seconds" in body["message"]
    (args, kwargs), = audited
    assert args[1] == "agent_meeting.turn_rate_limited" and kwargs["outcome"] == "denied"
    assert kwargs["requested_turns"] == 2 and kwargs["scope"] == "actor"
    assert "key-12345678" not in repr(kwargs)


def test_execute_turn_is_limited_too_and_arabic_follows_accept_language(audited):
    rate.limiter.acquire(ctx("rate_owner", "rate_tenant", "rate_workspace"), 3, actor_limit=3, workspace_limit=20)
    response = client.post("/api/v1/agent-meetings/m1/execute-turn", json={"request_key": "key-12345678"},
                           headers={**hdr(), "accept-language": "ar"})
    assert response.status_code == 429
    assert "ثانية" in response.json()["error"]["message"]


def test_a_member_without_manage_is_refused_before_the_limiter(audited):
    response = client.post("/api/v1/agent-meetings/m1/run", json={"run_key": "key-12345678", "turns": 1},
                           headers=hdr(role="viewer", user="rate_viewer"))
    assert response.status_code == 403
    assert rate.limiter._hits == {}
