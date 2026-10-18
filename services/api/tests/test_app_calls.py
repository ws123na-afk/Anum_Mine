"""In-app calls between teammates (docs/in-app-calls.md), memory backend.

Covers who may ring whom (same workspace and tenant only), the lifecycle (ring, accept,
decline, cancel, busy, missed on timeout, dropped without heartbeats), signaling addressed
to the bound device only, the per-device stream, TURN credential minting and expiry, and
the phone push intents. The PostgreSQL row policies are in test_postgres_app_calls.py.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from anum_api import app_calls, main
from anum_api.app_calls import (
    CallMode,
    call_event_source,
    call_mode,
    memory_call_store,
    open_call_unit,
    read_snapshot,
    settle_stale,
    turn_credential_valid,
    turn_credentials,
)
from anum_api.dependencies import memory_repository
from anum_api.events import CanonicalEventName, create_event
from anum_api.onboarding import _notifications
from anum_api.push import PushKind, intent_for
from anum_api.schemas import TenantContext, WorkspaceMembership, utc_now
from anum_api.settings import settings
from anum_api.store import store
from anum_api.team_store import memory_team_store

TENANT, WORKSPACE, OTHER_WORKSPACE, OTHER_TENANT = "tenant_calls", "workspace_calls", "workspace_calls_2", "tenant_calls_2"
SECRET = "turn-secret-example-turn-secret-example"
DEVICE_A = "device-alice-0000001"
DEVICE_B = "device-bob-000000001"
DEVICE_B2 = "device-bob-000000002"
OFFER = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\n"

client = TestClient(main.app)


def headers(user: str, role: str = "member", workspace: str = WORKSPACE, tenant: str = TENANT) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


def ctx(user: str, workspace: str = WORKSPACE, tenant: str = TENANT) -> TenantContext:
    return TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=user, roles=["member"])


def member(user: str, role: str = "member", *, workspace: str = WORKSPACE, tenant: str = TENANT, active: bool = True) -> None:
    now = utc_now()
    memory_repository.save_membership(
        WorkspaceMembership(
            tenant_id=tenant, workspace_id=workspace, user_id=user, role=role, active=active, created_at=now, updated_at=now
        )
    )


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def reset() -> None:
        memory_call_store.clear()
        _notifications.clear()
        store.memberships.clear()
        store.events.clear()
        store.audit_records.clear()
        if hasattr(memory_team_store, "clear"):
            memory_team_store.clear()

    reset()
    monkeypatch.setattr(settings, "calls_turn_urls", "turn:turn.example.test:3478?transport=udp,turn:turn.example.test:3478?transport=tcp")
    monkeypatch.setattr(settings, "calls_turn_secret", SecretStr(SECRET))
    monkeypatch.setattr(settings, "calls_stun_urls", "")
    monkeypatch.setattr(settings, "calls_allow_stun_only", False)
    for user, role in (("alice", "member"), ("bob", "member"), ("carol", "viewer")):
        member(user, role)
    member("dave", workspace=OTHER_WORKSPACE)
    member("erin", tenant=OTHER_TENANT, workspace="workspace_elsewhere")
    yield
    reset()


def place(caller: str = "alice", callee: str = "bob", media: str = "audio", device: str = DEVICE_A) -> dict:
    response = client.post("/api/v1/calls", headers=headers(caller), json={"callee_user_id": callee, "media": media, "client_id": device})
    assert response.status_code == 201, response.text
    return response.json()


def accept(call_id: str, user: str = "bob", device: str = DEVICE_B) -> dict:
    response = client.post(f"/api/v1/calls/{call_id}/accept", headers=headers(user), json={"client_id": device})
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- configuration


def test_calls_are_off_until_a_relay_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "calls_turn_urls", "")
    monkeypatch.setattr(settings, "calls_turn_secret", None)
    config = client.get("/api/v1/calls/config", headers=headers("alice")).json()
    assert config["enabled"] is False and config["mode"] == "off" and "TURN" in config["reason"]
    assert client.get("/api/v1/calls/ice-servers", headers=headers("alice")).status_code == 503
    refused = client.post("/api/v1/calls", headers=headers("alice"), json={"callee_user_id": "bob", "client_id": DEVICE_A})
    assert refused.status_code == 503
    assert refused.json()["error"]["code"] == "service_unavailable"


def test_a_short_or_missing_secret_keeps_calls_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "calls_turn_secret", SecretStr("too-short"))
    mode = call_mode()
    assert mode.mode == CallMode.OFF and "ANUM_CALLS_TURN_SECRET" in (mode.reason or "")
    monkeypatch.setattr(settings, "calls_turn_secret", SecretStr(SECRET))
    monkeypatch.setattr(settings, "calls_turn_urls", "https://turn.example.test")
    assert call_mode().mode == CallMode.OFF


def test_stun_only_mode_is_opt_in_and_says_its_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "calls_turn_urls", "")
    monkeypatch.setattr(settings, "calls_allow_stun_only", True)
    monkeypatch.setattr(settings, "calls_stun_urls", "stun:stun.example.test:3478")
    config = client.get("/api/v1/calls/config", headers=headers("alice")).json()
    assert config["enabled"] is True and config["mode"] == "stun_only" and "strict firewalls" in config["reason"]
    servers = client.get("/api/v1/calls/ice-servers", headers=headers("alice")).json()
    assert servers["ice_servers"] == [{"urls": ["stun:stun.example.test:3478"], "username": None, "credential": None}]
    assert servers["expires_at"] is None


def test_turn_mode_mints_credentials_that_expire() -> None:
    servers = client.get("/api/v1/calls/ice-servers", headers=headers("alice")).json()
    assert servers["mode"] == "turn" and servers["ttl_seconds"] == settings.calls_turn_ttl_seconds
    stun, turn = servers["ice_servers"]
    assert stun["urls"] == ["stun:turn.example.test:3478"] and stun["credential"] is None
    assert turn["urls"][0].startswith("turn:")
    assert turn_credential_valid(turn["username"], turn["credential"])
    # The username is "<expiry>:<digest>": no user id, no tenant id.
    assert "alice" not in turn["username"] and TENANT not in turn["username"]
    # Random per issue: two credentials for the same person share nothing but the expiry format.
    assert turn_credentials(ctx("alice"))[0].split(":")[1] != turn_credentials(ctx("alice"))[0].split(":")[1]
    now = utc_now()
    username, credential, expires_at = turn_credentials(ctx("alice"), now=now, ttl=600)
    assert expires_at - now <= timedelta(seconds=600)
    assert turn_credential_valid(username, credential, now=now + timedelta(seconds=599))
    assert not turn_credential_valid(username, credential, now=now + timedelta(seconds=601))
    assert not turn_credential_valid(username, credential[:-2] + "AA", now=now)
    assert not turn_credential_valid("9999999999:forged", credential, now=now)


def test_the_secret_never_appears_in_any_answer() -> None:
    for path in ("/api/v1/calls/config", "/api/v1/calls/ice-servers"):
        assert SECRET not in client.get(path, headers=headers("alice")).text


# --------------------------------------------------------------------------- who may call whom


@pytest.mark.parametrize("callee", ["dave", "erin", "nobody"])
def test_only_active_members_of_the_same_workspace_can_be_rung(callee: str) -> None:
    response = client.post("/api/v1/calls", headers=headers("alice"), json={"callee_user_id": callee, "client_id": DEVICE_A})
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "This person is not an active member of this workspace"
    assert memory_call_store.live(ctx("alice")) == []


def test_a_deactivated_member_cannot_be_rung() -> None:
    member("bob", active=False)
    response = client.post("/api/v1/calls", headers=headers("alice"), json={"callee_user_id": "bob", "client_id": DEVICE_A})
    assert response.status_code == 404


def test_nobody_calls_themselves_and_viewers_may_call() -> None:
    response = client.post("/api/v1/calls", headers=headers("alice"), json={"callee_user_id": "alice", "client_id": DEVICE_A})
    assert response.status_code == 422
    call = place("carol", "alice")
    assert call["direction"] == "outgoing" and call["peer_user_id"] == "alice"


def test_a_device_id_must_look_like_one() -> None:
    response = client.post("/api/v1/calls", headers=headers("alice"), json={"callee_user_id": "bob", "client_id": "x"})
    assert response.status_code == 422


def test_the_callee_of_another_workspace_sees_nothing() -> None:
    call = place()
    assert client.get(f"/api/v1/calls/{call['id']}", headers=headers("bob", workspace=OTHER_WORKSPACE)).status_code == 404
    assert client.get(f"/api/v1/calls/{call['id']}", headers=headers("carol")).status_code == 404
    assert client.post(f"/api/v1/calls/{call['id']}/accept", headers=headers("carol"), json={"client_id": DEVICE_B}).status_code == 404
    assert client.get("/api/v1/calls", headers=headers("carol")).json()["items"] == []


# --------------------------------------------------------------------------- lifecycle


def test_ring_accept_signal_and_hang_up() -> None:
    call = place(media="video")
    assert call["status"] == "ringing" and call["media"] == "video" and call["peer_name"] == "bob"
    ringing = [event for event in store.events if event.type == CanonicalEventName.APP_CALL_RINGING.value]
    assert len(ringing) == 1 and ringing[0].payload == {"call_id": call["id"], "recipient": "bob"}
    # Both of bob's devices ring; only the one that answers is bound.
    for device in (DEVICE_B, DEVICE_B2):
        snapshot = read_snapshot(ctx("bob"), device)
        assert [c.id for c in snapshot.calls] == [call["id"]]
    # No signal before the call is answered.
    early = client.post(f"/api/v1/calls/{call['id']}/signals", headers=headers("alice"), json={"kind": "offer", "sdp": OFFER})
    assert early.status_code == 409
    accepted = accept(call["id"])
    assert accepted["status"] == "accepted" and accepted["on_this_device"] is True
    # The offer comes from the caller only, the answer from the callee only.
    assert client.post(f"/api/v1/calls/{call['id']}/signals", headers=headers("bob"), json={"kind": "offer", "sdp": OFFER}).status_code == 409
    assert client.post(f"/api/v1/calls/{call['id']}/signals", headers=headers("alice"), json={"kind": "answer", "sdp": OFFER}).status_code == 409
    sent = client.post(f"/api/v1/calls/{call['id']}/signals", headers=headers("alice"), json={"kind": "offer", "sdp": OFFER})
    assert sent.status_code == 202
    ice = {"kind": "ice", "candidates": [{"candidate": "candidate:1 1 udp 1 127.0.0.1 9 typ host", "sdpMid": "0", "sdpMLineIndex": 0}]}
    assert client.post(f"/api/v1/calls/{call['id']}/signals", headers=headers("bob"), json=ice).status_code == 202
    assert client.post(f"/api/v1/calls/{call['id']}/signals", headers=headers("bob"), json={"kind": "answer", "sdp": OFFER}).status_code == 202
    # Signals reach the bound device only, never the other phone or a non-participant.
    bob = read_snapshot(ctx("bob"), DEVICE_B)
    assert [s.kind.value for s in bob.signals] == ["offer"]
    assert read_snapshot(ctx("bob"), DEVICE_B2).signals == []
    alice = read_snapshot(ctx("alice"), DEVICE_A)
    assert [s.kind.value for s in alice.signals] == ["ice", "answer"]
    assert read_snapshot(ctx("carol"), DEVICE_B).calls == []
    # The other phone learns the call was answered elsewhere.
    other = app_calls.view_of(read_snapshot(ctx("bob"), DEVICE_B2).calls[0], ctx("bob"), DEVICE_B2)
    assert other.answered_elsewhere is True and other.on_this_device is False
    assert client.post(f"/api/v1/calls/{call['id']}/heartbeat", headers=headers("bob")).status_code == 204
    ended = client.post(f"/api/v1/calls/{call['id']}/end", headers=headers("alice"), json={"reason": "hangup"}).json()
    assert ended["status"] == "ended" and ended["outcome"] == "completed" and ended["duration_seconds"] >= 0
    # Signals are deleted with the end; the record stays for both.
    assert memory_call_store.signals_for(ctx("alice"), DEVICE_A, [call["id"]]) == []
    page = client.get("/api/v1/calls", headers=headers("bob")).json()
    assert page["items"][0]["direction"] == "incoming" and page["items"][0]["outcome"] == "completed"
    # Ending twice is harmless.
    again = client.post(f"/api/v1/calls/{call['id']}/end", headers=headers("bob"), json={"reason": "hangup"})
    assert again.status_code == 200 and again.json()["outcome"] == "completed"
    actions = [record.action for record in store.audit_records]
    assert {"app_call.placed", "app_call.answered", "app_call.ended"} <= set(actions)
    # The audit log never names the other participant.
    assert all("bob" not in json.dumps(dict(record.metadata)) for record in store.audit_records if record.actor == "alice")


def test_only_the_callee_answers_and_only_while_ringing() -> None:
    call = place()
    refused = client.post(f"/api/v1/calls/{call['id']}/accept", headers=headers("alice"), json={"client_id": DEVICE_A})
    assert refused.status_code == 403
    accept(call["id"])
    second = client.post(f"/api/v1/calls/{call['id']}/accept", headers=headers("bob"), json={"client_id": DEVICE_B2})
    assert second.status_code == 409


def test_decline_and_cancel_and_the_missed_call_notification() -> None:
    declined = place()
    result = client.post(f"/api/v1/calls/{declined['id']}/end", headers=headers("bob"), json={"reason": "decline"}).json()
    assert result["outcome"] == "declined"
    cancelled = place()
    result = client.post(f"/api/v1/calls/{cancelled['id']}/end", headers=headers("alice"), json={"reason": "hangup"}).json()
    assert result["outcome"] == "cancelled"
    notifications = client.get("/api/v1/notifications", headers=headers("bob")).json()["items"]
    assert [(n["kind"], n["title"]) for n in notifications] == [("missed_call", "Missed call")]
    assert "voice call" in notifications[0]["body"]
    missed = client.get("/api/v1/calls?missed=true", headers=headers("bob")).json()["items"]
    assert [item["id"] for item in missed] == [cancelled["id"]]
    # The caller's own list has both; alice gets no missed-call notification.
    assert len(client.get("/api/v1/calls", headers=headers("alice")).json()["items"]) == 2
    assert client.get("/api/v1/notifications", headers=headers("alice")).json()["items"] == []


def test_a_busy_callee_is_recorded_not_rung() -> None:
    first = place("carol", "bob")
    accept(first["id"])
    busy = place("alice", "bob")
    assert busy["status"] == "ended" and busy["outcome"] == "busy"
    ringing = [e for e in store.events if e.type == CanonicalEventName.APP_CALL_RINGING.value]
    assert [e.subject for e in ringing] == [first["id"]]
    # And the caller of a live call cannot start a second one.
    refused = client.post("/api/v1/calls", headers=headers("carol"), json={"callee_user_id": "alice", "client_id": DEVICE_A})
    assert refused.status_code == 409


def test_an_unanswered_ring_becomes_missed_on_the_next_read() -> None:
    call = place()
    later = utc_now() + timedelta(seconds=settings.calls_ring_timeout_seconds + 1)
    with open_call_unit(ctx("bob")) as unit:
        settled = settle_stale(unit, now=later)
    assert [c.outcome.value for c in settled if c.outcome] == ["missed"]
    view = client.get(f"/api/v1/calls/{call['id']}", headers=headers("alice")).json()
    assert view["outcome"] == "missed" and view["ended_at"] == view["ring_expires_at"]
    kinds = [n["kind"] for n in client.get("/api/v1/notifications", headers=headers("bob")).json()["items"]]
    assert kinds == ["missed_call"]


def test_a_connected_call_without_heartbeats_is_dropped() -> None:
    call = place()
    accept(call["id"])
    later = utc_now() + app_calls.HEARTBEAT_TIMEOUT + timedelta(seconds=1)
    with open_call_unit(ctx("alice")) as unit:
        settled = settle_stale(unit, now=later)
    assert [c.outcome.value for c in settled if c.outcome] == ["dropped"]
    assert client.post(f"/api/v1/calls/{call['id']}/heartbeat", headers=headers("bob")).status_code == 409


def test_a_device_failure_is_recorded_as_failed() -> None:
    call = place()
    accept(call["id"])
    result = client.post(f"/api/v1/calls/{call['id']}/end", headers=headers("bob"), json={"reason": "failed"}).json()
    assert result["outcome"] == "failed"


def test_signal_shapes_are_checked() -> None:
    call = place()
    accept(call["id"])
    url = f"/api/v1/calls/{call['id']}/signals"
    assert client.post(url, headers=headers("alice"), json={"kind": "offer"}).status_code == 422
    assert client.post(url, headers=headers("alice"), json={"kind": "offer", "sdp": "not sdp"}).status_code == 422
    assert client.post(url, headers=headers("alice"), json={"kind": "ice", "sdp": OFFER}).status_code == 422
    huge = {"kind": "offer", "sdp": "v=0" + "a" * 20_000}
    assert client.post(url, headers=headers("alice"), json=huge).status_code == 422
    assert client.post(url, headers=headers("carol"), json={"kind": "offer", "sdp": OFFER}).status_code == 404


def test_history_pages_with_a_cursor() -> None:
    ids = []
    for _ in range(3):
        call = place()
        ids.append(call["id"])
        client.post(f"/api/v1/calls/{call['id']}/end", headers=headers("alice"), json={"reason": "hangup"})
    first = client.get("/api/v1/calls?limit=2", headers=headers("alice")).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = client.get("/api/v1/calls", params={"limit": 2, "cursor": first["next_cursor"]}, headers=headers("alice")).json()
    assert [i["id"] for i in first["items"] + second["items"]] == list(reversed(ids)) and second["next_cursor"] is None
    assert client.get("/api/v1/calls?cursor=garbage|x", headers=headers("alice")).status_code == 422


# --------------------------------------------------------------------------- stream


def _collect(user: str, device: str) -> list[tuple[str, dict]]:
    async def run() -> list[str]:
        async def never() -> bool:
            return False

        return [frame async for frame in call_event_source(ctx(user), device, never, follow=False)]

    events = []
    for frame in asyncio.run(run()):
        lines = dict(line.split(": ", 1) for line in frame.strip().splitlines() if ": " in line and not line.startswith(":"))
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_the_stream_sends_this_devices_calls_and_signals() -> None:
    call = place()
    incoming = _collect("bob", DEVICE_B2)
    assert [(name, data["status"], data["direction"]) for name, data in incoming] == [("call", "ringing", "incoming")]
    assert incoming[0][1]["peer_name"] == "alice"
    accept(call["id"])
    client.post(f"/api/v1/calls/{call['id']}/signals", headers=headers("alice"), json={"kind": "offer", "sdp": OFFER})
    bob = _collect("bob", DEVICE_B)
    assert [name for name, _ in bob] == ["call", "signal"]
    assert bob[1][1]["kind"] == "offer" and bob[1][1]["payload"]["sdp"] == OFFER
    assert [name for name, _ in _collect("bob", DEVICE_B2)] == ["call"]
    assert _collect("carol", DEVICE_B) == []


def test_the_stream_route_needs_a_device_id_and_a_member() -> None:
    assert client.get("/api/v1/calls/stream", headers=headers("alice")).status_code == 422
    response = client.get("/api/v1/calls/stream", params={"client_id": DEVICE_A, "follow": "false"}, headers=headers("alice"))
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith("retry: 1000")


# --------------------------------------------------------------------------- push


def test_ringing_and_missed_calls_become_call_pushes() -> None:
    ring = create_event(CanonicalEventName.APP_CALL_RINGING, ctx("alice"), "call_1", {"call_id": "call_1", "recipient": "bob"}).event
    intent = intent_for(ring)
    assert intent is not None and intent.kind == PushKind.INCOMING_CALL and intent.recipient == "bob"
    assert intent.data()["app_call_id"] == "call_1" and intent.collapse_id() == "app_call:call_1:incoming_call"
    missed = create_event(
        CanonicalEventName.NOTIFICATION_CREATED, ctx("alice"), "ntf_1", {"recipient": "bob", "kind": "missed_call", "work_item_id": None}
    ).event
    missed_intent = intent_for(missed)
    assert missed_intent is not None and missed_intent.kind == PushKind.MISSED_CALL
    assert intent_for(create_event(CanonicalEventName.APP_CALL_RINGING, ctx("alice"), "x", {}).event) is None
