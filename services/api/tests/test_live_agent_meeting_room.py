"""Pure and fail-closed tests for the live meeting room (F06/F07/F08). No database, no model."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from anum_api import live_agent_meeting_room as room
from anum_api.live_agent_meeting_redaction import MARK, redact_secrets
from anum_api.live_agent_meeting_room_api import (
    AgendaPatch, Message, OutputCreate, OutputPatch, event_source, frame, parse_last_event_id,
)
from anum_api.main import app
from anum_api.settings import settings

client = TestClient(app)
MIGRATION = Path(__file__).parents[1] / "migrations" / "versions" / "0102_meeting_room.py"


def headers(role="owner"):
    return {"x-tenant-id": "room_tenant", "x-workspace-id": "room_workspace", "x-user-id": "room_owner", "x-user-roles": role}


# ----------------------------------------------------------------------------- redaction

# Built at run time and plainly fake, so repository secret scanners have nothing to match.
FAKE_SECRETS = [
    "sk" + "-ant-" + "a" * 20, "gh" + "p_" + "a" * 30, "AK" + "IA" + "A" * 16, "xo" + "xb-" + "1" * 12 + "-aaaa",
    "Bearer " + "a" * 20, "ey" + "J" + "a" * 10 + "." + "b" * 10 + "." + "c" * 6,
    "-" * 5 + "BEGIN PRIVATE KEY" + "-" * 5 + "\nexample\n" + "-" * 5 + "END PRIVATE KEY" + "-" * 5,
]


@pytest.mark.parametrize("secret", FAKE_SECRETS)
def test_redaction_removes_credentials(secret):
    text, changed = redact_secrets(f"please use {secret} for the call")
    assert changed and secret not in text and MARK in text


def test_redaction_assignments_and_url_credentials():
    text, changed = redact_secrets("password: examples and api_key=examplez at https://user:examplepass@host/x")
    assert changed
    assert "examples" not in text and "examplez" not in text and "examplepass" not in text
    assert "https://" + MARK + "@host/x" in text


def test_redaction_leaves_ordinary_text_alone():
    value = "We agreed to ship the report on Tuesday. The token budget is fine."
    assert redact_secrets(value) == (value, False)


# ----------------------------------------------------------------------------- speaking indicator

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def ev(seq, kind, payload, ago=5):
    return {"seq": seq, "kind": kind, "actor_id": "a", "payload": payload, "created_at": NOW - timedelta(seconds=ago)}


def test_speaking_only_while_a_real_turn_is_in_flight():
    started = ev(1, "turn_started", {"agent_id": "agent_a", "sequence": 1, "request_key": "k1"})
    assert [s["agent_id"] for s in room.speaking_now([started], NOW)] == ["agent_a"]
    done = ev(2, "message", {"sequence": 1, "request_key": "k1"})
    assert room.speaking_now([started, done], NOW) == []
    failed = ev(2, "turn_failed", {"sequence": 1, "code": "conflict"})
    assert room.speaking_now([started, failed], NOW) == []


def test_speaking_ignores_stale_starts_and_clears_on_pause_or_end():
    stale = ev(1, "turn_started", {"agent_id": "agent_a", "sequence": 1, "request_key": "k1"}, ago=600)
    assert room.speaking_now([stale], NOW) == []
    live = ev(1, "turn_started", {"agent_id": "agent_a", "sequence": 1, "request_key": "k1"})
    assert room.speaking_now([live, ev(2, "state", {"status": "paused"})], NOW) == []
    assert room.speaking_now([live, ev(2, "state", {"status": "finished"})], NOW) == []


# ----------------------------------------------------------------------------- SSE framing and replay

def test_frame_carries_the_event_number_as_the_sse_id():
    out = frame(ev(7, "message", {"body": "hello"}))
    assert out.startswith("id: 7\nevent: message\n")
    assert json.loads(out.split("data: ", 1)[1])["payload"]["body"] == "hello"


def test_last_event_id_parsing():
    assert parse_last_event_id("12", 0) == 12
    assert parse_last_event_id(None, 5) == 5
    assert parse_last_event_id("not-a-number", 3) == 3
    assert parse_last_event_id("-4", 0) == 0


def collect(poll, start, **kw):
    async def never():
        return False

    async def run():
        return [chunk async for chunk in event_source(poll, start, is_disconnected=never, poll_seconds=0, **kw)]
    return asyncio.run(run())


def test_stream_replays_from_last_event_id_without_repeats_then_ends():
    log = [ev(n, "message", {"sequence": n}) for n in range(1, 6)]

    def poll(after):
        return [e for e in log if e["seq"] > after][:2], True
    chunks = collect(poll, 2)
    ids = [line for chunk in chunks for line in chunk.splitlines() if line.startswith("id: ")]
    assert ids == ["id: 3", "id: 4", "id: 5"]
    assert chunks[-1].startswith("event: end")


def test_stream_asks_the_client_to_reconnect_after_its_time_budget():
    ticks = iter(range(0, 1000, 50))
    chunks = collect(lambda after: ([], False), 9, max_seconds=100, clock=lambda: next(ticks))
    assert chunks[-1].startswith("event: reconnect") and '"seq": 9' in chunks[-1]


def test_stream_stops_when_the_client_disconnects():
    async def gone():
        return True

    async def run():
        return [c async for c in event_source(lambda a: ([ev(1, "message", {})], False), 0, is_disconnected=gone)]
    assert asyncio.run(run()) == ["retry: 3000\n\n"]


# ----------------------------------------------------------------------------- schemas and gates

def test_bodies_reject_unknown_fields_and_bad_values():
    for model, payload in (
        (Message, {"request_key": "short", "body": "x"}),
        (Message, {"request_key": "request-key-one", "body": "x", "tenant_id": "other"}),
        (AgendaPatch, {"state": "later"}),
        (OutputCreate, {"kind": "idea", "body": "x"}),
        (OutputCreate, {"kind": "decision", "body": "x", "source_sequences": list(range(30))}),
        (OutputPatch, {"state": "done"}),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(payload)


def test_room_endpoints_fail_closed_without_postgresql(monkeypatch):
    monkeypatch.setattr(settings, "repository_backend", "memory")
    base = "/api/v1/agent-meetings/m1"
    assert client.get(base + "/room", headers=headers()).status_code == 503
    assert client.get(base + "/events", headers=headers()).status_code == 503
    assert client.post(base + "/presence", headers=headers(), json={"action": "join"}).status_code == 503


def test_viewers_cannot_write_to_a_room(monkeypatch):
    monkeypatch.setattr(settings, "repository_backend", "memory")
    base = "/api/v1/agent-meetings/m1"
    h = headers("viewer")
    assert client.post(base + "/messages", headers=h, json={"request_key": "request-key-one", "body": "hi"}).status_code == 403
    assert client.post(base + "/agenda", headers=h, json={"title": "x"}).status_code == 403
    assert client.post(base + "/outputs", headers=h, json={"kind": "decision", "body": "x"}).status_code == 403
    assert client.post(base + "/outputs/o1/task", headers=h).status_code == 403
    assert client.delete(base + "/transcript", headers=h).status_code == 403
    assert client.get(base + "/export", headers=h).status_code == 403


def test_export_markdown_lists_sources_and_skips_dismissed():
    bundle = {"meeting": {"title": "Plan", "status": "finished"}, "agenda": [{"position": 1, "title": "Budget", "state": "done"}],
              "outputs": [
                  {"kind": "decision", "state": "confirmed", "body": "Ship Tuesday", "owner_label": None, "source_sequences": [2]},
                  {"kind": "action_item", "state": "dismissed", "body": "Dropped", "owner_label": None, "source_sequences": []}]}
    text = room.export_markdown(bundle, [{"speaker_id": "agent_a", "role": "agent", "sequence": 2, "body": "We ship Tuesday"}])
    assert "Ship Tuesday" in text and "#2" in text and "Dropped" not in text and "## Agenda" in text


# ----------------------------------------------------------------------------- migration contract

def _migration():
    spec = importlib.util.spec_from_file_location("meeting_room_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_is_expand_only_scoped_and_forced_rls():
    module = _migration()
    assert module.revision == "0102_meeting_room" and len(module.revision) <= 32
    assert module.down_revision == "0101_executive_grants"
    sql = MIGRATION.read_text(encoding="utf-8")
    upgrade = sql.split("def upgrade() -> None:", 1)[1].split("def downgrade() -> None:", 1)[0]
    assert "drop_" not in upgrade and "alter column" not in upgrade
    assert "force row level security" in sql and 'ondelete="RESTRICT"' in sql
    assert "lifecycle_purge_" in sql
    for table in module.TABLES:
        assert f'"{table}"' in sql
