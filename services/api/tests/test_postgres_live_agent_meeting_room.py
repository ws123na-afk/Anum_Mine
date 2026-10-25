"""PostgreSQL application-role tests for the meeting room: durable turns, events, interrupts,
budgets, crash and resume, draft outputs, export, delete and workspace isolation.

The model is a deterministic fake gateway; nothing here calls a provider.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from uuid import uuid4

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api import live_agent_meeting_room as room
from anum_api.db import session as db_session
from anum_api.db.session import set_tenant_context
from anum_api.live_agent_meeting_store import LiveMeetingStoreError, SqlLiveMeetingStore
from anum_api.meeting_execution import execute_meeting_turn
from anum_api.model_gateway import ModelResponse, ModelUsage
from anum_api.schemas import TenantContext
from anum_api.settings import settings

pytestmark = pytest.mark.database


@pytest.fixture
def factory(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator:
    maker = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(maker, "after_begin")
    def _app_role(session, transaction, connection):
        connection.execute(text(f"set local role {APP_ROLE}"))
    monkeypatch.setattr(db_session, "SessionLocal", maker)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    yield maker


class FakeGateway:
    """Deterministic and offline. ``provider`` is not 'mock', so the executor accepts it."""
    provider = "fake"

    def __init__(self, texts=None, cost=0.001, tokens=(10, 20), during=None):
        self.texts = list(texts or ["We should review the budget first."])
        self.cost, self.tokens, self.during, self.calls = cost, tokens, during, 0

    async def generate_text(self, prompt: str) -> ModelResponse:
        self.calls += 1
        if self.during:
            self.during()
        body = self.texts[min(self.calls, len(self.texts)) - 1]
        return ModelResponse(text=body, usage=ModelUsage(input_tokens=self.tokens[0], output_tokens=self.tokens[1],
                                                         provider="fake", model="fake-1", estimated_cost_usd=self.cost))


def ctx(tenant=TENANT_A, workspace=WORKSPACE_A, user="room_owner"):
    return TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=user, roles=["owner"])


def scoped(factory, context):
    session = factory()
    set_tenant_context(session, context.tenant_id, context.workspace_id, user_id=context.user_id)
    return session


def make_meeting(factory, context=None, *, agents=2, start=True, **limits):
    context = context or ctx()
    meeting_id = "room-" + uuid4().hex[:12]
    with scoped(factory, context) as session:
        store = SqlLiveMeetingStore(session)
        store.create(context, {"id": meeting_id, "title": "Weekly plan", "max_turns": limits.get("max_turns", 8),
                               "max_duration_seconds": 600, "max_tokens": limits.get("max_tokens", 5000),
                               "max_cost_usd": limits.get("max_cost_usd", 1), "max_participants": 6})
        for n in range(agents):
            agent = f"agent-{n}-{uuid4().hex[:6]}"
            session.execute(text("""insert into agent_profiles (id,tenant_id,workspace_id,owner_user_id,name,icon,color,category,
                instructions,model_route,knowledge_mode,status,sharing,created_at,updated_at)
                values (:a,:t,:w,:o,:n,'bot','#112233','work','Be brief.','fast','none','active','workspace',now(),now())"""),
                {"a": agent, "t": context.tenant_id, "w": context.workspace_id, "o": context.user_id, "n": f"Agent {n}"})
            session.execute(text("""insert into agent_meeting_participants (id,tenant_id,workspace_id,session_id,kind,actor_id,added_by)
                values (:id,:t,:w,:s,'agent',:a,:o)"""), {"id": f"p-{uuid4().hex[:8]}", "t": context.tenant_id,
                                                          "w": context.workspace_id, "s": meeting_id, "a": agent, "o": context.user_id})
        session.execute(text("update agent_meeting_sessions set revision=revision+1 where id=:s"), {"s": meeting_id})
        if start:
            store.transition(context, meeting_id, store.get(context, meeting_id)["revision"], "active")
        session.commit()
    return meeting_id


def run_turn(context, meeting_id, key, gateway):
    return asyncio.run(execute_meeting_turn(context, meeting_id, key, gateway))


def read(factory, context, fn):
    with scoped(factory, context) as session:
        result = fn(SqlLiveMeetingStore(session))
        session.commit()
        return result


def events(factory, context, meeting_id):
    return read(factory, context, lambda s: room.events_after(s, context, meeting_id, 0, 500))


def kinds(factory, context, meeting_id):
    return [e["kind"] for e in events(factory, context, meeting_id)]


def turns(factory, context, meeting_id):
    return read(factory, context, lambda s: room.transcript(s, context, meeting_id))


def revision(factory, context, meeting_id):
    return read(factory, context, lambda s: s.get(context, meeting_id)["revision"])


# ----------------------------------------------------------------------------- turns and events

def test_each_message_is_stored_before_its_event_and_events_are_gapless(factory):
    c = ctx()
    mid = make_meeting(factory)
    gateway = FakeGateway()
    first = run_turn(c, mid, "unattended:one:first", gateway)
    second = run_turn(c, mid, "unattended:one:second", gateway)
    assert (first["sequence"], second["sequence"]) == (1, 2)
    assert first["speaker_id"] != second["speaker_id"]  # fair rotation
    log = events(factory, c, mid)
    assert [e["seq"] for e in log] == list(range(1, len(log) + 1))
    assert [e["kind"] for e in log] == ["state", "turn_started", "message", "turn_started", "message"]
    stored = {t["sequence"]: t for t in turns(factory, c, mid)}
    for e in log:
        if e["kind"] == "message":
            assert stored[e["payload"]["sequence"]]["body"] == e["payload"]["body"]
    bundle = read(factory, c, lambda s: room.room(s, c, mid))
    assert bundle["speaking"] == [] and bundle["last_event_seq"] == len(log)


def test_retry_with_the_same_key_does_not_call_the_model_or_duplicate(factory):
    c = ctx()
    mid = make_meeting(factory)
    gateway = FakeGateway()
    one = run_turn(c, mid, "retry-key-one", gateway)
    again = run_turn(c, mid, "retry-key-one", gateway)
    assert one["id"] == again["id"] and gateway.calls == 1
    assert len(turns(factory, c, mid)) == 1 and kinds(factory, c, mid).count("message") == 1


def test_secrets_in_model_text_are_redacted_before_storage_and_events(factory):
    c = ctx()
    mid = make_meeting(factory)
    leaked = "ghp" + "_" + "a" * 30
    run_turn(c, mid, "redact-key-one", FakeGateway([f"Use {leaked} and password: examples to deploy."]))
    body = turns(factory, c, mid)[0]
    assert leaked not in body["body"] and "examples" not in body["body"] and body["redacted"] is True
    assert leaked not in str(events(factory, c, mid))


def test_expired_claim_is_recovered_and_a_live_claim_blocks(factory):
    c = ctx()
    mid = make_meeting(factory)
    claim = """insert into agent_meeting_turn_leases (id,tenant_id,workspace_id,session_id,sequence,agent_id,request_key,expected_revision,expires_at)
        values (:id,:t,:w,:s,1,'ghost','ghost-claim-001',1,now() + cast(:delta as interval))"""
    args = {"id": "lease-" + uuid4().hex[:8], "t": c.tenant_id, "w": c.workspace_id, "s": mid}
    with scoped(factory, c) as session:
        session.execute(text(claim), {**args, "delta": "60 seconds"})
        session.commit()
    with pytest.raises(LiveMeetingStoreError, match="already running"):
        run_turn(c, mid, "after-crash-0001", FakeGateway())
    with scoped(factory, c) as session:  # the worker died and the lease timed out
        session.execute(text("update agent_meeting_turn_leases set expires_at=now()-interval '1 second' where session_id=:s"), {"s": mid})
        session.commit()
    assert run_turn(c, mid, "after-crash-0001", FakeGateway())["sequence"] == 1


def test_budget_caps_stop_turns_and_failed_commits_leave_no_message_or_lease(factory):
    c = ctx()
    capped = make_meeting(factory, max_turns=1)
    run_turn(c, capped, "cap-key-one", FakeGateway())
    with pytest.raises(LiveMeetingStoreError, match="limit"):
        run_turn(c, capped, "cap-key-two", FakeGateway())
    costly = make_meeting(factory, max_cost_usd=0.01)
    gateway = FakeGateway(cost=0.5)
    with pytest.raises(LiveMeetingStoreError, match="cost cap"):
        run_turn(c, costly, "cost-key-one", gateway)
    assert turns(factory, c, costly) == []
    assert kinds(factory, c, costly)[-2:] == ["turn_started", "turn_failed"]
    with scoped(factory, c) as session:
        assert session.execute(text("select count(*) from agent_meeting_turn_leases where session_id=:s"), {"s": costly}).scalar_one() == 0
    tokens = make_meeting(factory, max_tokens=100)
    with pytest.raises(LiveMeetingStoreError, match="token"):
        run_turn(c, tokens, "token-key-one", FakeGateway(tokens=(80, 80)))


def test_pause_during_a_turn_drops_it_and_no_message_appears(factory):
    c = ctx()
    mid = make_meeting(factory)

    def pause():
        with scoped(factory, c) as session:
            store = SqlLiveMeetingStore(session)
            store.get(c, mid, lock=True)
            store.transition(c, mid, store.get(c, mid)["revision"], "paused")
            session.commit()
    with pytest.raises(LiveMeetingStoreError, match="active"):
        run_turn(c, mid, "pause-key-one", FakeGateway(during=pause))
    assert turns(factory, c, mid) == []
    assert kinds(factory, c, mid)[-3:] == ["turn_started", "state", "turn_failed"]
    with pytest.raises(LiveMeetingStoreError, match="active"):
        run_turn(c, mid, "pause-key-two", FakeGateway())


def test_emergency_stop_blocks_agent_turns(factory, database_engine):
    c = ctx()
    mid = make_meeting(factory)
    with database_engine.begin() as connection:
        connection.execute(text("insert into agent_emergency_stops (tenant_id, engaged, changed_by, reason) values (:t, true, 'owner', 'test') "
                                "on conflict (tenant_id) do update set engaged=true"), {"t": c.tenant_id})
    gateway = FakeGateway()
    with pytest.raises(LiveMeetingStoreError, match="emergency stop"):
        run_turn(c, mid, "stop-key-one", gateway)
    assert gateway.calls == 0


def test_a_disabled_or_foreign_agent_cannot_speak(factory, database_engine):
    c = ctx()
    mid = make_meeting(factory, agents=1)
    with database_engine.begin() as connection:
        connection.execute(text("update agent_profiles set status='paused' where tenant_id=:t and workspace_id=:w"),
                           {"t": c.tenant_id, "w": c.workspace_id})
    gateway = FakeGateway()
    with pytest.raises(LiveMeetingStoreError, match="disabled"):
        run_turn(c, mid, "paused-agent-one", gateway)
    assert gateway.calls == 0
    with pytest.raises(LiveMeetingStoreError, match="not found"):
        run_turn(ctx(TENANT_B, WORKSPACE_B), mid, "foreign-key-one", gateway)


# ----------------------------------------------------------------------------- interrupt, hand, presence

def test_hand_requires_presence_blocks_agents_and_an_owner_message_clears_it(factory):
    c = ctx()
    guest = ctx(user="room_guest")
    mid = make_meeting(factory)
    with pytest.raises(LiveMeetingStoreError, match="Join"):
        read(factory, guest, lambda s: room.hand(s, guest, mid, "raise"))
    read(factory, guest, lambda s: room.presence(s, guest, mid, "join"))
    read(factory, guest, lambda s: room.hand(s, guest, mid, "raise"))
    gateway = FakeGateway()
    with pytest.raises(LiveMeetingStoreError, match="hand is raised"):
        run_turn(c, mid, "hand-key-one", gateway)
    assert gateway.calls == 0
    with pytest.raises(LiveMeetingStoreError, match="owner"):
        read(factory, guest, lambda s: room.hand(s, guest, mid, "lower"))
    message = read(factory, c, lambda s: room.post_message(s, c, mid, "owner-msg-one", "Please cover the risks."))
    assert message["role"] == "human" and message["sequence"] == 1
    assert "hand_lowered" in kinds(factory, c, mid)
    assert run_turn(c, mid, "hand-key-two", gateway)["sequence"] == 2
    kinds_seen = kinds(factory, c, mid)
    assert kinds_seen.index("hand_raised") < kinds_seen.index("hand_lowered")


def test_owner_message_is_idempotent_redacted_owner_only_and_needs_a_running_meeting(factory):
    c = ctx()
    mid = make_meeting(factory)
    leaked = "ghp" + "_" + "b" * 30
    one = read(factory, c, lambda s: room.post_message(s, c, mid, "owner-msg-two", f"key {leaked}"))
    two = read(factory, c, lambda s: room.post_message(s, c, mid, "owner-msg-two", "different text"))
    assert one["sequence"] == two["sequence"] and leaked not in one["body"] and one["redacted"] is True
    assert kinds(factory, c, mid).count("message") == 1
    other = ctx(user="room_guest")
    with pytest.raises(LiveMeetingStoreError, match="owner"):
        read(factory, other, lambda s: room.post_message(s, other, mid, "guest-msg-one", "hi"))
    ended = make_meeting(factory, start=False)
    with pytest.raises(LiveMeetingStoreError, match="running"):
        read(factory, c, lambda s: room.post_message(s, c, ended, "owner-msg-three", "hi"))


def test_human_message_takes_a_number_so_a_slow_agent_turn_is_renumbered(factory):
    c = ctx()
    mid = make_meeting(factory)

    def interrupt():
        read(factory, c, lambda s: room.post_message(s, c, mid, "owner-msg-four", "One moment."))
    done = run_turn(c, mid, "race-key-one", FakeGateway(during=interrupt))
    assert done["sequence"] == 2
    assert [t["role"] for t in turns(factory, c, mid)] == ["human", "agent"]


def test_presence_join_leave_and_ended_meetings(factory):
    c = ctx(user="room_guest")
    mid = make_meeting(factory)
    read(factory, c, lambda s: room.presence(s, c, mid, "join"))
    read(factory, c, lambda s: room.presence(s, c, mid, "join"))
    read(factory, c, lambda s: room.presence(s, c, mid, "leave"))
    read(factory, c, lambda s: room.presence(s, c, mid, "leave"))
    assert kinds(factory, c, mid).count("joined") == 1 and kinds(factory, c, mid).count("left") == 1
    attendance = read(factory, c, lambda s: room.room(s, c, mid))["attendance"]
    assert attendance[0]["left_at"] is not None
    owner = ctx()
    read(factory, owner, lambda s: s.transition(owner, mid, s.get(owner, mid)["revision"], "finished"))
    with pytest.raises(LiveMeetingStoreError, match="ended"):
        read(factory, c, lambda s: room.presence(s, c, mid, "join"))


# ----------------------------------------------------------------------------- agenda, outputs, files

def test_agenda_owner_only_one_current_item(factory):
    c = ctx()
    guest = ctx(user="room_guest")
    mid = make_meeting(factory)
    a = read(factory, c, lambda s: room.agenda_add(s, c, mid, "Budget"))
    b = read(factory, c, lambda s: room.agenda_add(s, c, mid, "Hiring"))
    assert (a["position"], b["position"]) == (1, 2)
    read(factory, c, lambda s: room.agenda_update(s, c, mid, a["id"], title=None, state="current"))
    read(factory, c, lambda s: room.agenda_update(s, c, mid, b["id"], title=None, state="current"))
    states = {i["title"]: i["state"] for i in read(factory, c, lambda s: room.room(s, c, mid))["agenda"]}
    assert states == {"Budget": "open", "Hiring": "current"}
    with pytest.raises(LiveMeetingStoreError, match="owner"):
        read(factory, guest, lambda s: room.agenda_add(s, guest, mid, "Sneaky"))
    read(factory, c, lambda s: room.agenda_delete(s, c, mid, a["id"]))


def test_outputs_are_drafts_with_real_sources_until_the_owner_confirms(factory):
    c = ctx()
    guest = ctx(user="room_guest")
    mid = make_meeting(factory)
    run_turn(c, mid, "out-key-one", FakeGateway())
    with pytest.raises(LiveMeetingStoreError, match="source"):
        read(factory, c, lambda s: room.output_create(s, c, mid, kind="decision", body="x", owner_label=None, source_sequences=[99]))
    with pytest.raises(LiveMeetingStoreError, match="owner"):
        read(factory, guest, lambda s: room.output_create(s, guest, mid, kind="decision", body="x", owner_label=None, source_sequences=[1]))
    item = read(factory, c, lambda s: room.output_create(s, c, mid, kind="action_item", body="Send the budget",
                                                         owner_label="Sam", source_sequences=[1, 1]))
    assert item["state"] == "draft" and item["source_sequences"] == [1] and item["task_id"] is None
    with pytest.raises(LiveMeetingStoreError, match="Confirm"):
        read(factory, c, lambda s: room.output_for_task(s, c, mid, item["id"]))
    done = read(factory, c, lambda s: room.output_decide(s, c, mid, item["id"], state="confirmed", body=None, owner_label=None))
    assert done["state"] == "confirmed" and done["decided_by"] == c.user_id
    assert read(factory, c, lambda s: room.output_for_task(s, c, mid, item["id"]))["meeting_title"] == "Weekly plan"
    read(factory, c, lambda s: room.output_attach_task(s, c, mid, item["id"], "task-1"))
    with pytest.raises(LiveMeetingStoreError, match="already"):
        read(factory, c, lambda s: room.output_attach_task(s, c, mid, item["id"], "task-2"))
    with pytest.raises(LiveMeetingStoreError, match="no longer"):
        read(factory, c, lambda s: room.output_decide(s, c, mid, item["id"], state="dismissed", body=None, owner_label=None))


def test_file_links_need_a_real_workspace_file(factory, database_engine):
    c = ctx()
    mid = make_meeting(factory)
    with pytest.raises(LiveMeetingStoreError, match="File not found"):
        read(factory, c, lambda s: room.file_link(s, c, mid, "no-such-file"))
    file_id = "file-" + uuid4().hex[:8]
    with database_engine.begin() as connection:
        connection.execute(text("""insert into workspace_files (id,tenant_id,workspace_id,name,content_type,size_bytes,sha256,storage_key,created_by,created_at)
            values (:id,:t,:w,'plan.pdf','application/pdf',3,:h,:k,'room_owner',now())"""),
            {"id": file_id, "t": c.tenant_id, "w": c.workspace_id, "h": "0" * 64, "k": "k/" + file_id})
    read(factory, c, lambda s: room.file_link(s, c, mid, file_id))
    files = read(factory, c, lambda s: room.room(s, c, mid))["files"]
    assert [f["name"] for f in files] == ["plan.pdf"]
    read(factory, c, lambda s: room.file_unlink(s, c, mid, file_id))
    assert read(factory, c, lambda s: room.room(s, c, mid))["files"] == []


# ----------------------------------------------------------------------------- export, delete, isolation

def test_export_then_delete_transcript_after_the_meeting_ends(factory):
    c = ctx()
    mid = make_meeting(factory)
    run_turn(c, mid, "exp-key-one", FakeGateway(["Ship on Tuesday."]))
    read(factory, c, lambda s: room.output_create(s, c, mid, kind="decision", body="Ship Tuesday", owner_label=None, source_sequences=[1]))
    with pytest.raises(LiveMeetingStoreError, match="End the meeting"):
        read(factory, c, lambda s: room.delete_transcript(s, c, mid))
    text_out = read(factory, c, lambda s: room.export_markdown(room.room(s, c, mid), room.transcript(s, c, mid)))
    assert "Ship on Tuesday." in text_out and "Ship Tuesday" in text_out
    read(factory, c, lambda s: s.transition(c, mid, s.get(c, mid)["revision"], "finished", reason="limit_reached"))
    assert read(factory, c, lambda s: s.get(c, mid))["ended_reason"] == "limit_reached"
    deleted = read(factory, c, lambda s: room.delete_transcript(s, c, mid))
    assert deleted["turns"] == 1 and deleted["events"] >= 3
    bundle = read(factory, c, lambda s: room.room(s, c, mid))
    assert bundle["transcript_deleted"] is True and bundle["last_event_seq"] == 0
    assert turns(factory, c, mid) == [] and bundle["outputs"][0]["source_sequences"] == []
    assert bundle["meeting"]["total_cost_usd"] > 0  # usage totals stay
    with pytest.raises(LiveMeetingStoreError, match="deleted"):
        read(factory, c, lambda s: room.output_create(s, c, mid, kind="decision", body="late", owner_label=None, source_sequences=[]))


def test_rooms_are_invisible_from_another_workspace_and_tenant(factory):
    c = ctx()
    mid = make_meeting(factory)
    run_turn(c, mid, "iso-key-one", FakeGateway())
    for other in (ctx(TENANT_B, WORKSPACE_B), ctx(TENANT_A, "workspace_a2")):
        with scoped(factory, other) as session:
            store = SqlLiveMeetingStore(session)
            with pytest.raises(LiveMeetingStoreError, match="not found"):
                room.room(store, other, mid)
            assert room.events_after(store, other, mid, 0) == []
            session.rollback()
    with scoped(factory, ctx(TENANT_B, WORKSPACE_B)) as session:  # RLS blocks a forged insert
        with pytest.raises(Exception):
            session.execute(text("""insert into agent_meeting_events (id,tenant_id,workspace_id,session_id,seq,kind)
                values ('forged',:t,:w,:s,99,'message')"""), {"t": TENANT_A, "w": WORKSPACE_A, "s": mid})
        session.rollback()


# ----------------------------------------------------------------------------- HTTP journey

def test_http_journey_join_interrupt_decide_task_replay_export_delete(factory, database_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from anum_api import live_agent_meeting_api
    from anum_api.main import app

    c = ctx()
    with database_engine.begin() as connection:
        connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) "
                                "values (:u,:t,:w,'owner',true) on conflict do nothing"),
                           {"u": c.user_id, "t": c.tenant_id, "w": c.workspace_id})
    fake = FakeGateway(["Let us fix the launch date.", "Agreed, the date is Tuesday."])
    monkeypatch.setattr(live_agent_meeting_api, "build_default_model_gateway", lambda: fake)
    client = TestClient(app)
    h = {"x-tenant-id": c.tenant_id, "x-workspace-id": c.workspace_id, "x-user-id": c.user_id, "x-user-roles": "owner"}
    mid = make_meeting(factory, start=False)
    base = f"/api/v1/agent-meetings/{mid}"

    assert client.post(base + "/state", headers=h, json={"status": "active", "expected_revision": client.get(base + "/room", headers=h).json()["meeting"]["revision"]}).status_code == 200
    assert client.post(base + "/presence", headers=h, json={"action": "join"}).status_code == 200
    assert client.post(base + "/agenda", headers=h, json={"title": "Launch date"}).status_code == 201
    run = client.post(base + "/run", headers=h, json={"run_key": "journey-run-one", "turns": 2})
    assert run.status_code == 200 and run.json()["completed"] == 2, run.text
    msg = client.post(base + "/messages", headers=h, json={"request_key": "journey-msg-one", "body": "Make it Tuesday."})
    assert msg.status_code == 201 and msg.json()["sequence"] == 3

    draft = client.post(base + "/outputs", headers=h, json={"kind": "action_item", "body": "Announce Tuesday launch",
                                                            "owner_label": "Sam", "source_sequences": [2, 3]})
    assert draft.status_code == 201 and draft.json()["state"] == "draft"
    oid = draft.json()["id"]
    assert client.post(f"{base}/outputs/{oid}/task", headers=h).status_code == 409  # not confirmed yet
    assert client.patch(f"{base}/outputs/{oid}", headers=h, json={"state": "confirmed"}).status_code == 200
    task = client.post(f"{base}/outputs/{oid}/task", headers=h)
    assert task.status_code == 201 and task.json()["task_id"], task.text
    assert client.post(f"{base}/outputs/{oid}/task", headers=h).status_code == 409  # only once
    with database_engine.begin() as connection:
        row = connection.execute(text("select status, prompt from tasks where id=:i"), {"i": task.json()["task_id"]}).mappings().one()
    assert row["status"] == "created" and "Announce Tuesday launch" in row["prompt"] and "#2, #3" in row["prompt"]

    # Replay: the JSON log and the SSE stream resume exactly after Last-Event-ID.
    full = client.get(base + "/events?after=0", headers=h).json()["items"]
    cut = full[3]["seq"]
    assert client.get(base + "/events?after=" + str(cut), headers=h).json()["items"][0]["seq"] == cut + 1
    assert client.post(base + "/state", headers=h, json={"status": "finished", "expected_revision": client.get(base + "/room", headers=h).json()["meeting"]["revision"]}).status_code == 200
    with client.stream("GET", base + "/events/stream", headers={**h, "Last-Event-ID": str(cut)}) as response:
        body = "".join(response.iter_text())
    ids = [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]
    assert ids and ids[0] == cut + 1 and ids == sorted(set(ids)) and "event: end" in body

    export = client.get(base + "/export", headers=h)
    assert export.status_code == 200 and "Let us fix the launch date." in export.text and "Announce Tuesday launch" in export.text
    assert client.delete(base + "/transcript", headers=h).status_code == 200
    gone = client.get(base + "/room", headers=h).json()
    assert gone["transcript_deleted"] is True and client.get(base + "/turns", headers=h).json()["items"] == []
    assert gone["outputs"][0]["task_id"]

    # A viewer reads but cannot moderate; another workspace gets 404.
    viewer = {**h, "x-user-id": "room_viewer", "x-user-roles": "viewer"}
    assert client.get(base + "/room", headers=viewer).status_code == 200
    assert client.post(base + "/messages", headers=viewer, json={"request_key": "viewer-msg-one", "body": "hi"}).status_code == 403
    foreign = {**h, "x-tenant-id": TENANT_B, "x-workspace-id": WORKSPACE_B}
    assert client.get(base + "/room", headers=foreign).status_code in (403, 404)


def test_agents_from_another_workspace_cannot_join_a_meeting(factory, database_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from anum_api.main import app

    c = ctx()
    other = ctx(TENANT_B, WORKSPACE_B)
    foreign_agent = make_meeting(factory, other, agents=1, start=False)  # creates an agent in tenant B
    with scoped(factory, other) as session:
        agent_id = session.execute(text("select actor_id from agent_meeting_participants where session_id=:s"), {"s": foreign_agent}).scalar_one()
    mid = make_meeting(factory, c, agents=0, start=False)
    client = TestClient(app)
    h = {"x-tenant-id": c.tenant_id, "x-workspace-id": c.workspace_id, "x-user-id": c.user_id, "x-user-roles": "owner"}
    revision_now = client.get(f"/api/v1/agent-meetings/{mid}", headers=h).json()["revision"]
    denied = client.post(f"/api/v1/agent-meetings/{mid}/agents", headers=h, json={"agent_id": agent_id, "expected_revision": revision_now})
    assert denied.status_code == 404


def test_worker_settles_with_a_reason_that_reaches_the_event_log(factory):
    from anum_api.unattended_meeting_worker import settle_meeting

    c = ctx()
    mid = make_meeting(factory)
    assert settle_meeting(c, mid, "finished", "limit_reached") is True
    assert settle_meeting(c, mid, "finished", "limit_reached") is False  # already ended: no second event
    last = events(factory, c, mid)[-1]
    assert last["kind"] == "state" and last["payload"]["reason"] == "limit_reached" and last["payload"]["status"] == "finished"
