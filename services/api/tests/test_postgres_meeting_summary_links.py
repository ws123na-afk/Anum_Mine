"""PostgreSQL application-role tests for the meeting summary, model-drafted extraction and decision to work links
(rows 9.6, 9.7, 9.9, 9.10): drafts only, click and hash to confirm, the monthly budget, owner-only text, audit
before and after, idempotent tenant-scoped links under forced row level security. The model is a fake; nothing
calls a provider."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from anum_api import live_agent_meeting_room as room
from anum_api import model_gateway
from anum_api.main import app
from anum_api.model_gateway import ModelResponse, ModelUsage

from test_postgres_live_agent_meeting_room import ctx, factory, make_meeting, read, run_turn, scoped  # noqa: F401
from test_postgres_live_agent_meeting_security import Gateway, audit_rows, set_cap, state_of  # noqa: F401

pytestmark = pytest.mark.database
SECRET_WORDS = "confidential acquisition of Orchid Ltd"


class SummaryGateway:
    provider = "fake"

    def __init__(self, payload=None, cost=0.02, raw=None):
        self.payload, self.cost, self.raw, self.calls, self.prompts = payload, cost, raw, 0, []

    async def generate_text(self, prompt: str) -> ModelResponse:
        self.calls += 1
        self.prompts.append(prompt)
        body = self.raw if self.raw is not None else json.dumps(self.payload or GOOD)
        return ModelResponse(text=body, usage=ModelUsage(input_tokens=400, output_tokens=120, provider="fake", model="fake-sum-1", estimated_cost_usd=self.cost))


GOOD = {
    "summary": [{"text": "The group agreed to start with the budget review.", "sources": [1]}, {"text": "Invented claim.", "sources": [77]}],
    "decisions": [{"text": "Review the budget first.", "sources": [1]}],
    "action_items": [{"text": "Prepare the budget sheet.", "owner": "Finance agent", "sources": [2]}],
}


def headers(context, role="owner"):
    return {"x-tenant-id": context.tenant_id, "x-workspace-id": context.workspace_id, "x-user-id": context.user_id, "x-user-roles": role}


@pytest.fixture
def http():
    return TestClient(app)


def finished_meeting(factory, context, turns=2):
    mid = make_meeting(factory, context)
    for n in range(turns):
        run_turn(context, mid, f"sum-turn-{mid[-6:]}-{n}", Gateway(cost=0.001))
    read(factory, context, lambda s: s.transition(context, mid, s.get(context, mid)["revision"], "finished"))
    return mid


def draft(http, factory, monkeypatch, context, mid, gateway=None):
    gateway = gateway or SummaryGateway()
    monkeypatch.setattr(model_gateway, "build_default_model_gateway", lambda: gateway)
    return http.post(f"/api/v1/agent-meetings/{mid}/summary", headers=headers(context)), gateway


def outputs(factory, context, mid):
    return read(factory, context, lambda s: room.room(s, context, mid)["outputs"])


def confirm(http, context, mid, output, **over):
    body = {"state": "confirmed", "input_mode": "click", "payload_hash": output["payload_hash"], **over}
    return http.patch(f"/api/v1/agent-meetings/{mid}/outputs/{output['id']}", headers=headers(context), json=body)


def make_task(factory, context, task_id):
    with scoped(factory, context) as session:
        session.execute(text("""insert into tasks (id,tenant_id,workspace_id,created_by_user_id,title,prompt,status)
            values (:i,:t,:w,'room_owner','Budget sheet','Prepare it','created')"""), {"i": task_id, "t": context.tenant_id, "w": context.workspace_id})
        session.commit()


def make_approval_and_run(factory, context, suffix):
    task_id = "tsk-" + suffix
    make_task(factory, context, task_id)
    with scoped(factory, context) as session:
        session.execute(text("""insert into agent_runs (id,tenant_id,workspace_id,task_id,status) values (:r,:t,:w,:task,'completed')"""),
                        {"r": "run-" + suffix, "t": context.tenant_id, "w": context.workspace_id, "task": task_id})
        session.execute(text("""insert into approvals (id,tenant_id,workspace_id,task_id,action,risk_level,status,reason)
            values (:a,:t,:w,:task,'send_email','high','pending','Needs a person')"""),
                        {"a": "apr-" + suffix, "t": context.tenant_id, "w": context.workspace_id, "task": task_id})
        session.commit()
    return task_id, "apr-" + suffix, "run-" + suffix


# ----------------------------------------------------------------------------- summary and extraction

def test_the_owner_drafts_a_summary_with_extracted_drafts_that_cost_budget_and_stay_unconfirmed(http, factory, monkeypatch):
    c = ctx()
    mid = finished_meeting(factory, c)
    used_before = state_of(factory, c).used_usd
    response, gateway = draft(http, factory, monkeypatch, c, mid)
    assert response.status_code == 201, response.text
    data = response.json()
    assert gateway.calls == 1 and data["dropped"] == 1 and data["messages"] == 2
    assert data["summary"]["kind"] == "summary" and data["summary"]["state"] == "draft" and data["summary"]["origin"] == "model"
    assert data["summary"]["body"] == "- The group agreed to start with the budget review. [#1]" and data["summary"]["source_sequences"] == [1]
    assert data["summary"]["drafted_with"] == "fake/fake-sum-1"
    assert [d["state"] for d in data["decisions"] + data["action_items"]] == ["draft", "draft"]
    assert data["action_items"][0]["owner_label"] == "Finance agent" and data["action_items"][0]["source_sequences"] == [2]
    assert state_of(factory, c).used_usd == used_before + Decimal("0.02")
    rows = outputs(factory, c, mid)
    assert len(rows) == 3 and all(o["state"] == "draft" and o["origin"] == "model" and o["task_id"] is None for o in rows)
    assert "agent_meeting_budgets" and read(factory, c, lambda s: s.get(c, mid)["total_cost_usd"]) >= Decimal("0.022")
    # the model saw the stored transcript as data, once
    assert json.loads(gateway.prompts[0])["messages"][0]["number"] == 1
    kinds = [e["kind"] for e in read(factory, c, lambda s: room.events_after(s, c, mid, 0, 500))]
    assert kinds.count("output") == 3


def test_the_audit_record_has_cost_and_budget_before_and_after_but_no_transcript_text(http, factory, monkeypatch):
    c = ctx()
    mid = finished_meeting(factory, c)
    before = state_of(factory, c).used_usd
    draft(http, factory, monkeypatch, c, mid, SummaryGateway({**GOOD, "summary": [{"text": SECRET_WORDS, "sources": [1]}]}))
    rows = audit_rows(factory, c, "agent_meeting.summary")
    assert len(rows) == 1 and rows[0]["outcome"] == "success" and rows[0]["actor"] == c.user_id
    meta = rows[0]["metadata"]
    assert meta["meeting_id"] == mid and meta["model"] == "fake/fake-sum-1" and meta["drafts_created"] == 3 and meta["new_state"] == "draft"
    assert Decimal(str(meta["used_before_usd"])) == before and Decimal(str(meta["used_usd"])) == before + Decimal("0.02")
    assert "Orchid" not in json.dumps(rows[0], default=str)


def test_only_an_ended_meeting_with_messages_and_one_live_summary(http, factory, monkeypatch):
    c = ctx()
    live = make_meeting(factory, c)
    run_turn(c, live, "summary-live-0001", Gateway())
    refused, gateway = draft(http, factory, monkeypatch, c, live)
    assert refused.status_code == 409 and "End the meeting" in refused.json()["error"]["message"] and gateway.calls == 0
    empty = make_meeting(factory, c)
    read(factory, c, lambda s: s.transition(c, empty, s.get(c, empty)["revision"], "cancelled"))
    assert draft(http, factory, monkeypatch, c, empty)[0].status_code == 409
    mid = finished_meeting(factory, c)
    assert draft(http, factory, monkeypatch, c, mid)[0].status_code == 201
    again, gateway = draft(http, factory, monkeypatch, c, mid)
    assert again.status_code == 409 and "already has a summary" in again.json()["error"]["message"] and gateway.calls == 0
    first = [o for o in outputs(factory, c, mid) if o["kind"] == "summary"][0]
    assert http.patch(f"/api/v1/agent-meetings/{mid}/outputs/{first['id']}", headers=headers(c), json={"state": "dismissed"}).status_code == 200
    assert draft(http, factory, monkeypatch, c, mid)[0].status_code == 201  # a dismissed summary may be replaced
    assert len([o for o in outputs(factory, c, mid) if o["kind"] == "summary" and o["state"] == "draft"]) == 1


def test_a_deleted_transcript_has_nothing_to_summarise(http, factory, monkeypatch):
    c = ctx()
    mid = finished_meeting(factory, c)
    read(factory, c, lambda s: room.delete_transcript(s, c, mid))
    response, gateway = draft(http, factory, monkeypatch, c, mid)
    assert response.status_code == 409 and gateway.calls == 0


def test_the_monthly_cap_refuses_the_summary_before_the_model_is_called_and_audits_it(http, factory, monkeypatch):
    c = ctx()
    mid = finished_meeting(factory, c)
    set_cap(factory, c, 0.0005)
    response, gateway = draft(http, factory, monkeypatch, c, mid)
    assert response.status_code == 402 and response.json()["error"]["code"] == "agent_meeting_budget_exceeded" and gateway.calls == 0
    blocked = [r for r in audit_rows(factory, c, "agent_meeting.summary") if r["outcome"] == "blocked"]
    assert len(blocked) == 1 and blocked[0]["metadata"]["reason"] == "monthly_budget"
    assert not outputs(factory, c, mid)


def test_an_unusable_answer_is_paid_for_recorded_and_stores_nothing(http, factory, monkeypatch):
    c = ctx()
    mid = finished_meeting(factory, c)
    before = state_of(factory, c).used_usd
    response, _ = draft(http, factory, monkeypatch, c, mid, SummaryGateway(raw="Sorry, no JSON here.", cost=0.03))
    assert response.status_code == 409 and "could not be read" in response.json()["error"]["message"]
    assert state_of(factory, c).used_usd == before + Decimal("0.03") and not outputs(factory, c, mid)
    failed = [r for r in audit_rows(factory, c, "agent_meeting.summary") if r["outcome"] == "failure"]
    assert failed and failed[0]["metadata"]["code"] == "unusable_answer"
    # nothing is stuck: a retry works at once
    assert draft(http, factory, monkeypatch, c, mid)[0].status_code == 201


def test_members_cannot_draft_read_or_confirm_and_see_only_counts(http, factory, monkeypatch):
    c = ctx()
    mid = finished_meeting(factory, c)
    assert draft(http, factory, monkeypatch, c, mid)[0].status_code == 201
    member = ctx(user="a_member")
    assert http.post(f"/api/v1/agent-meetings/{mid}/summary", headers=headers(member, "member")).status_code == 403
    bundle = http.get(f"/api/v1/agent-meetings/{mid}/room", headers=headers(member, "member")).json()
    assert bundle["outputs"] == [] and bundle["outputs_count"] == 3 and bundle["links"] == [] and bundle["transcript_hidden"] is True
    assert "budget review" not in json.dumps(bundle)
    out = outputs(factory, c, mid)[0]
    assert http.patch(f"/api/v1/agent-meetings/{mid}/outputs/{out['id']}", headers=headers(member, "member"), json={"state": "confirmed"}).status_code == 403
    assert http.get(f"/api/v1/agent-meetings/{mid}/export?format=json", headers=headers(member, "member")).status_code == 403


def test_another_workspace_and_another_tenant_get_404(http, factory, monkeypatch):
    c = ctx()
    mid = finished_meeting(factory, c)
    monkeypatch.setattr(model_gateway, "build_default_model_gateway", lambda: SummaryGateway())
    for other in (ctx(workspace=WORKSPACE_A2), ctx(tenant=TENANT_B, workspace=WORKSPACE_B)):
        assert http.post(f"/api/v1/agent-meetings/{mid}/summary", headers=headers(other)).status_code == 404


# ----------------------------------------------------------------------------- confirmation by click and hash

def test_confirming_needs_a_click_and_the_hash_of_the_text_shown_and_is_audited_with_before_and_after(http, factory, monkeypatch):
    c = ctx()
    mid = finished_meeting(factory, c)
    draft(http, factory, monkeypatch, c, mid)
    summary = [o for o in outputs(factory, c, mid) if o["kind"] == "summary"][0]
    url = f"/api/v1/agent-meetings/{mid}/outputs/{summary['id']}"
    for mode in ("typed", "voice"):
        refused = http.patch(url, headers=headers(c), json={"state": "confirmed", "input_mode": mode, "payload_hash": summary["payload_hash"]})
        assert refused.status_code == 403 and "click" in refused.json()["error"]["message"]
    assert http.patch(url, headers=headers(c), json={"state": "confirmed", "input_mode": "click"}).status_code == 409  # no hash
    assert http.patch(url, headers=headers(c), json={"state": "confirmed", "input_mode": "click", "payload_hash": "0" * 64}).status_code == 409
    assert [o["state"] for o in outputs(factory, c, mid)] == ["draft"] * 3
    # the owner edits the draft: the old hash no longer confirms it
    edited = http.patch(url, headers=headers(c), json={"body": "- Corrected line [#1]"})
    assert edited.status_code == 200
    assert confirm(http, c, mid, summary).status_code == 409
    assert http.patch(url, headers=headers(c), json={"state": "confirmed", "input_mode": "click", "payload_hash": edited.json()["payload_hash"], "body": "Sneaky"}).status_code == 409
    done = confirm(http, c, mid, edited.json())
    assert done.status_code == 200 and done.json()["state"] == "confirmed" and done.json()["decided_by"] == c.user_id
    decide = [r for r in audit_rows(factory, c, "agent_meeting.output_decide") if r["metadata"].get("new_state") == "confirmed"]
    assert len(decide) == 1 and decide[0]["metadata"]["previous_state"] == "draft" and decide[0]["metadata"]["input_mode"] == "click"
    assert decide[0]["metadata"]["output_kind"] == "summary" and decide[0]["metadata"]["output_id"] == summary["id"]
    # a confirmed summary is not edited in place
    assert http.patch(url, headers=headers(c), json={"body": "Rewritten"}).status_code == 409
    # a model-drafted decision follows the same rule
    decision = [o for o in outputs(factory, c, mid) if o["kind"] == "decision"][0]
    assert http.patch(f"/api/v1/agent-meetings/{mid}/outputs/{decision['id']}", headers=headers(c), json={"state": "confirmed"}).status_code == 403
    assert confirm(http, c, mid, decision).status_code == 200


def test_a_person_drafted_decision_still_confirms_as_before(http, factory, monkeypatch):
    c = ctx()
    mid = finished_meeting(factory, c)
    created = http.post(f"/api/v1/agent-meetings/{mid}/outputs", headers=headers(c), json={"kind": "decision", "body": "Hire two.", "source_sequences": [1]})
    assert created.status_code == 201 and created.json()["origin"] == "person"
    assert http.patch(f"/api/v1/agent-meetings/{mid}/outputs/{created.json()['id']}", headers=headers(c), json={"state": "confirmed"}).json()["state"] == "confirmed"


# ----------------------------------------------------------------------------- decision to work links

def confirmed_decision(http, factory, c, mid):
    created = http.post(f"/api/v1/agent-meetings/{mid}/outputs", headers=headers(c), json={"kind": "decision", "body": "Review the budget first.", "source_sequences": [1]})
    http.patch(f"/api/v1/agent-meetings/{mid}/outputs/{created.json()['id']}", headers=headers(c), json={"state": "confirmed"})
    return created.json()["id"]


def link(http, c, mid, output, kind, target, mode="click"):
    return http.post(f"/api/v1/agent-meetings/{mid}/outputs/{output}/links", headers=headers(c), json={"target_kind": kind, "target_id": target, "input_mode": mode})


def test_a_confirmed_decision_links_to_a_task_an_approval_and_a_run_idempotently_and_both_ways(http, factory, monkeypatch):
    c = ctx()
    mid = finished_meeting(factory, c)
    decision = confirmed_decision(http, factory, c, mid)
    task, approval, run = make_approval_and_run(factory, c, "one")
    first = link(http, c, mid, decision, "task", task)
    assert first.status_code == 201 and first.json()["target_label"] == "Budget sheet" and first.json()["target_status"] == "created"
    again = link(http, c, mid, decision, "task", task)
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert link(http, c, mid, decision, "approval", approval).status_code == 201
    assert link(http, c, mid, decision, "run", run).status_code == 201
    bundle = http.get(f"/api/v1/agent-meetings/{mid}/room", headers=headers(c)).json()
    assert sorted(l["target_kind"] for l in bundle["links"]) == ["approval", "run", "task"]
    assert len(audit_rows(factory, c, "agent_meeting.decision_link")) == 3  # the repeat is not audited twice
    meta = audit_rows(factory, c, "agent_meeting.decision_link")[0]["metadata"]
    assert meta["previous_state"] == "unlinked" and meta["new_state"] == "linked" and meta["output_id"] == decision
    back = http.get(f"/api/v1/agent-meetings/work-links?target_kind=task&target_id={task}", headers=headers(c)).json()["items"]
    assert len(back) == 1 and back[0]["meeting_id"] == mid and back[0]["output_id"] == decision and back[0]["title"] == "Weekly plan"
    assert http.get(f"/api/v1/agent-meetings/work-links?target_kind=task&target_id={task}", headers=headers(ctx(user='m'), "member")).status_code == 403
    exported = json.loads(http.get(f"/api/v1/agent-meetings/{mid}/export?format=json", headers=headers(c)).text)
    assert len(exported["links"]) == 3 and exported["decisions"][0]["links"] and exported["confirmed_outputs"][0]["id"] == decision
    text_export = http.get(f"/api/v1/agent-meetings/{mid}/export?format=markdown", headers=headers(c)).text
    assert "linked task: Budget sheet" in text_export
    # unlink
    assert http.delete(f"/api/v1/agent-meetings/{mid}/links/{first.json()['id']}", headers=headers(c)).status_code == 204
    assert http.delete(f"/api/v1/agent-meetings/{mid}/links/{first.json()['id']}", headers=headers(c)).status_code == 404
    assert len(audit_rows(factory, c, "agent_meeting.decision_unlink")) == 1


def test_link_rules_click_confirmed_decision_and_a_real_target(http, factory, monkeypatch):
    c = ctx()
    mid = finished_meeting(factory, c)
    decision = confirmed_decision(http, factory, c, mid)
    task, _, _ = make_approval_and_run(factory, c, "two")
    assert link(http, c, mid, decision, "task", task, "typed").status_code == 403
    assert link(http, c, mid, decision, "task", "no-such-task").status_code == 404
    draft_one = http.post(f"/api/v1/agent-meetings/{mid}/outputs", headers=headers(c), json={"kind": "decision", "body": "Not yet."}).json()["id"]
    assert link(http, c, mid, draft_one, "task", task).status_code == 409
    action = http.post(f"/api/v1/agent-meetings/{mid}/outputs", headers=headers(c), json={"kind": "action_item", "body": "Do it."}).json()["id"]
    http.patch(f"/api/v1/agent-meetings/{mid}/outputs/{action}", headers=headers(c), json={"state": "confirmed"})
    assert link(http, c, mid, action, "task", task).status_code == 409
    assert link(http, c, mid, "missing", "task", task).status_code == 404
    assert not audit_rows(factory, c, "agent_meeting.decision_link")


def test_links_never_reach_work_of_another_workspace_or_tenant(http, factory, monkeypatch):
    c = ctx()
    mid = finished_meeting(factory, c)
    decision = confirmed_decision(http, factory, c, mid)
    elsewhere = ctx(workspace=WORKSPACE_A2)
    other_tenant = ctx(tenant=TENANT_B, workspace=WORKSPACE_B)
    make_task(factory, elsewhere, "task-other-ws")
    make_task(factory, other_tenant, "task-other-tenant")
    assert link(http, c, mid, decision, "task", "task-other-ws").status_code == 404
    assert link(http, c, mid, decision, "task", "task-other-tenant").status_code == 404
    assert link(http, elsewhere, mid, decision, "task", "task-other-ws").status_code == 404  # the meeting is not theirs either


def test_row_level_security_isolates_and_forces_the_link_table(http, factory, database_engine):
    c = ctx()
    mid = finished_meeting(factory, c)
    decision = confirmed_decision(http, factory, c, mid)
    task, _, _ = make_approval_and_run(factory, c, "three")
    assert link(http, c, mid, decision, "task", task).status_code == 201
    count = "select count(*) from agent_meeting_output_links"
    for other in (ctx(workspace=WORKSPACE_A2), ctx(tenant=TENANT_B, workspace=WORKSPACE_B)):
        with scoped(factory, other) as session:
            assert session.execute(text(count)).scalar_one() == 0
            with pytest.raises((DBAPIError, IntegrityError)):
                session.execute(text("""insert into agent_meeting_output_links (id,tenant_id,workspace_id,session_id,output_id,target_kind,target_id,created_by)
                    values ('x',:t,:w,:s,:o,'task','t','u')"""), {"t": c.tenant_id, "w": c.workspace_id, "s": mid, "o": decision})
            session.rollback()
    with scoped(factory, c) as session:
        assert session.execute(text(count)).scalar_one() == 1
        with pytest.raises((DBAPIError, IntegrityError)):
            session.execute(text("""insert into agent_meeting_output_links (id,tenant_id,workspace_id,session_id,output_id,target_kind,target_id,created_by)
                values ('y',:t,:w,:s,:o,'invoice','t','u')"""), {"t": c.tenant_id, "w": c.workspace_id, "s": mid, "o": decision})
        session.rollback()
    with database_engine.connect() as connection:
        flags = connection.execute(text("select relrowsecurity, relforcerowsecurity from pg_class where relname='agent_meeting_output_links'")).one()
        policies = {r[0] for r in connection.execute(text("select policyname from pg_policies where tablename='agent_meeting_output_links'"))}
    assert flags == (True, True)
    assert policies == {"tenant_isolation_agent_meeting_output_links", "lifecycle_purge_agent_meeting_output_links"}


def test_the_live_summary_index_allows_one_live_summary_and_the_output_kind_check_stays(database_engine, factory):
    c = ctx()
    mid = make_meeting(factory, c)
    insert = """insert into agent_meeting_outputs (id,tenant_id,workspace_id,session_id,kind,body,state,created_by)
        values (:i,:t,:w,:s,:k,'text',:st,'u')"""
    with scoped(factory, c) as session:
        for oid, state in (("s-1", "draft"), ("s-2", "dismissed"), ("s-3", "dismissed")):
            session.execute(text(insert), {"i": oid, "t": c.tenant_id, "w": c.workspace_id, "s": mid, "k": "summary", "st": state})
        with pytest.raises(IntegrityError):
            session.execute(text(insert), {"i": "s-4", "t": c.tenant_id, "w": c.workspace_id, "s": mid, "k": "summary", "st": "confirmed"})
        session.rollback()
    with scoped(factory, c) as session:
        with pytest.raises(IntegrityError):
            session.execute(text(insert), {"i": "k-1", "t": c.tenant_id, "w": c.workspace_id, "s": mid, "k": "note", "st": "draft"})
        session.rollback()
