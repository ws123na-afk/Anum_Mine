"""Group calls, adding people and video mid-call (docs/in-app-calls.md#group-calls), memory backend.

A group call is a room of up to four people; every two of them have their own pair call
(a mesh), so signaling and the participants-only rules stay those of a call between two.
The PostgreSQL row policies of ``app_call_rooms`` are in test_postgres_app_calls.py.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_app_calls import (  # noqa: F401  (the autouse fixture resets the stores)
    DEVICE_A,
    DEVICE_B,
    DEVICE_B2,
    OFFER,
    _clean,
    _collect,
    client,
    ctx,
    headers,
    member,
    place,
)

from anum_api import app_calls
from anum_api.app_calls import (
    ROOM_MAX_PEOPLE,
    memory_call_store,
    open_call_unit,
    read_snapshot,
    settle_stale,
)
from anum_api.schemas import utc_now
from anum_api.settings import settings
from anum_api.store import store

DEVICE_C = "device-carol-0000001"
DEVICE_D = "device-dan-000000001"
DEVICE_E = "device-eve-000000001"
DEVICE_F = "device-fay-000000001"


@pytest.fixture(autouse=True)
def _more_people() -> None:
    for user in ("dan", "eve", "fay"):
        member(user)


def group(caller: str = "alice", people: tuple[str, ...] = ("bob", "carol"), media: str = "audio", device: str = DEVICE_A) -> dict:
    response = client.post(
        "/api/v1/app-calls/rooms", headers=headers(caller), json={"user_ids": list(people), "media": media, "client_id": device}
    )
    assert response.status_code == 201, response.text
    return response.json()


def ring_for(user: str, device: str) -> dict:
    calls = [c for c in read_snapshot(ctx(user), device).calls if c.status.value == "ringing" and c.callee_user_id == user]
    assert len(calls) == 1, calls
    return app_calls.view_of(calls[0], ctx(user), device).model_dump(mode="json")


def answer(call_id: str, user: str, device: str, *, group_capable: bool = True) -> dict:
    body: dict[str, object] = {"client_id": device}
    if group_capable:
        body["group"] = True
    response = client.post(f"/api/v1/app-calls/{call_id}/accept", headers=headers(user), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def states(room: dict) -> dict[str, str]:
    return {m["user_id"]: m["state"] for m in room["members"]}


def room_of(user: str, room_id: str) -> dict:
    response = client.get(f"/api/v1/app-calls/rooms/{room_id}", headers=headers(user))
    assert response.status_code == 200, response.text
    return response.json()


def pairs(user: str, device: str) -> list[tuple[str, str, str]]:
    """This device's connected pair calls: (caller, callee, status)."""
    return sorted(
        (c.caller_user_id, c.callee_user_id, c.status.value)
        for c in read_snapshot(ctx(user), device).calls
        if c.status.value == "accepted"
    )


def test_a_group_call_rings_everyone_and_the_answers_form_a_mesh() -> None:
    room = group()
    assert room["status"] == "live" and room["max_people"] == ROOM_MAX_PEOPLE == 4
    assert states(room) == {"alice": "joined", "bob": "invited", "carol": "invited"}
    assert [m["is_you"] for m in room["members"]] == [True, False, False]
    # Each invitee is rung by the host, on every one of their devices.
    bob_ring, carol_ring = ring_for("bob", DEVICE_B), ring_for("carol", DEVICE_C)
    assert bob_ring["room_id"] == carol_ring["room_id"] == room["id"]
    assert ring_for("bob", DEVICE_B2)["id"] == bob_ring["id"]
    answer(bob_ring["id"], "bob", DEVICE_B)
    answer(carol_ring["id"], "carol", DEVICE_C)
    # Carol answered second, so she has a connected pair to Bob and sends its offer.
    assert pairs("carol", DEVICE_C) == [("alice", "carol", "accepted"), ("carol", "bob", "accepted")]
    assert pairs("bob", DEVICE_B) == [("alice", "bob", "accepted"), ("carol", "bob", "accepted")]
    # Bob's other phone is not part of the mesh.
    assert pairs("bob", DEVICE_B2) == [("alice", "bob", "accepted"), ("carol", "bob", "accepted")]
    carol_to_bob = next(c for c in read_snapshot(ctx("carol"), DEVICE_C).calls if c.caller_user_id == "carol")
    assert app_calls.view_of(carol_to_bob, ctx("bob"), DEVICE_B2).on_this_device is False
    sent = client.post(f"/api/v1/app-calls/{carol_to_bob.id}/signals", headers=headers("carol"), json={"kind": "offer", "sdp": OFFER})
    assert sent.status_code == 202
    assert [s.kind.value for s in read_snapshot(ctx("bob"), DEVICE_B).signals] == ["offer"]
    assert read_snapshot(ctx("bob"), DEVICE_B2).signals == []
    assert states(room_of("bob", room["id"])) == {"alice": "joined", "bob": "joined", "carol": "joined"}
    actions = [r.action for r in store.audit_records]
    assert actions.count("app_call.placed") == 2 and "app_call.joined" in actions


def test_an_app_that_cannot_hold_a_group_call_is_told_to_update() -> None:
    group()
    ring = ring_for("bob", DEVICE_B)
    refused = client.post(f"/api/v1/app-calls/{ring['id']}/accept", headers=headers("bob"), json={"client_id": DEVICE_B})
    assert refused.status_code == 409
    assert "Update ANUM" in refused.json()["error"]["message"]


def test_a_group_call_needs_two_or_three_members_of_this_workspace() -> None:
    def start(people: list[str]) -> int:
        return client.post(
            "/api/v1/app-calls/rooms", headers=headers("alice"), json={"user_ids": people, "client_id": DEVICE_A}
        ).status_code

    assert start(["bob"]) == 422  # one person is a normal call
    assert start(["bob", "carol", "dan", "eve"]) == 422  # at most four people with the host
    assert start(["bob", "bob"]) == 422
    assert start(["bob", "alice"]) == 422
    assert start(["bob", "dave"]) == 404  # dave is in another workspace
    assert start(["bob", "erin"]) == 404  # erin is in another tenant
    assert memory_call_store.all_mine(ctx("alice")) == []


def test_adding_a_person_turns_a_call_into_a_group_call_of_at_most_four() -> None:
    call = place()
    early = client.post(f"/api/v1/app-calls/{call['id']}/invite", headers=headers("alice"), json={"user_id": "carol", "client_id": DEVICE_A})
    assert early.status_code == 409  # not answered yet
    answer(call["id"], "bob", DEVICE_B, group_capable=False)
    # Only from the device that is in the call, and only teammates of this workspace.
    wrong_device = client.post(
        f"/api/v1/app-calls/{call['id']}/invite", headers=headers("bob"), json={"user_id": "carol", "client_id": DEVICE_B2}
    )
    assert wrong_device.status_code == 409
    outsider = client.post(f"/api/v1/app-calls/{call['id']}/invite", headers=headers("bob"), json={"user_id": "dave", "client_id": DEVICE_B})
    assert outsider.status_code == 404
    room = client.post(f"/api/v1/app-calls/{call['id']}/invite", headers=headers("bob"), json={"user_id": "carol", "client_id": DEVICE_B})
    assert room.status_code == 200, room.text
    room = room.json()
    assert states(room) == {"alice": "joined", "bob": "joined", "carol": "invited"}
    assert client.get(f"/api/v1/app-calls/{call['id']}", headers=headers("alice")).json()["room_id"] == room["id"]
    ring = ring_for("carol", DEVICE_C)
    assert ring["peer_name"] == "bob" and ring["room_id"] == room["id"]
    again = client.post(f"/api/v1/app-calls/{call['id']}/invite", headers=headers("alice"), json={"user_id": "carol", "client_id": DEVICE_A})
    assert again.status_code == 409  # already being rung
    answer(ring["id"], "carol", DEVICE_C)
    assert pairs("carol", DEVICE_C) == [("bob", "carol", "accepted"), ("carol", "alice", "accepted")]
    full = client.post(f"/api/v1/app-calls/{call['id']}/invite", headers=headers("alice"), json={"user_id": "dan", "client_id": DEVICE_A})
    assert full.status_code == 200
    fifth = client.post(f"/api/v1/app-calls/{call['id']}/invite", headers=headers("alice"), json={"user_id": "eve", "client_id": DEVICE_A})
    assert fifth.status_code == 409 and "at most 4 people" in fifth.json()["error"]["message"]


def test_leaving_keeps_the_others_connected_and_the_last_one_ends_it() -> None:
    room = group()
    answer(ring_for("bob", DEVICE_B)["id"], "bob", DEVICE_B)
    answer(ring_for("carol", DEVICE_C)["id"], "carol", DEVICE_C)
    left = client.post(f"/api/v1/app-calls/rooms/{room['id']}/leave", headers=headers("alice"), json={})
    assert left.status_code == 200 and states(left.json())["alice"] == "left" and left.json()["status"] == "live"
    assert pairs("bob", DEVICE_B) == [("carol", "bob", "accepted")]
    history = client.get("/api/v1/app-calls", headers=headers("alice")).json()["items"]
    assert {(i["peer_name"], i["outcome"]) for i in history} == {("bob", "completed"), ("carol", "completed")}
    # Leaving again changes nothing; a teammate outside the group cannot see or leave it.
    assert client.post(f"/api/v1/app-calls/rooms/{room['id']}/leave", headers=headers("alice"), json={}).status_code == 200
    assert client.post(f"/api/v1/app-calls/rooms/{room['id']}/leave", headers=headers("dan"), json={}).status_code == 404
    assert client.get(f"/api/v1/app-calls/rooms/{room['id']}", headers=headers("dan")).status_code == 404
    done = client.post(f"/api/v1/app-calls/rooms/{room['id']}/leave", headers=headers("bob"), json={}).json()
    assert done["status"] == "ended" and states(done) == {"alice": "left", "bob": "left", "carol": "left"}
    assert pairs("carol", DEVICE_C) == []


def test_a_group_call_nobody_answers_ends_and_the_host_leaving_stops_the_rings() -> None:
    room = group()
    client.post(f"/api/v1/app-calls/{ring_for('bob', DEVICE_B)['id']}/end", headers=headers("bob"), json={"reason": "decline"})
    assert states(room_of("alice", room["id"])) == {"alice": "joined", "bob": "declined", "carol": "invited"}
    with open_call_unit(ctx("carol")) as unit:
        settle_stale(unit, now=utc_now() + timedelta(seconds=settings.calls_ring_timeout_seconds + 1))
    ended = room_of("alice", room["id"])
    assert ended["status"] == "ended" and states(ended)["carol"] == "missed"
    notes = client.get("/api/v1/notifications", headers=headers("carol")).json()["items"]
    assert [n["kind"] for n in notes] == ["missed_call"]

    second = group(people=("dan", "eve"))
    answer(ring_for("dan", DEVICE_D)["id"], "dan", DEVICE_D)
    client.post(f"/api/v1/app-calls/rooms/{second['id']}/leave", headers=headers("alice"), json={})
    # Eve was being rung by Alice; Dan is alone and nobody rings, so the call is over.
    after = room_of("dan", second["id"])
    assert after["status"] == "ended" and states(after)["eve"] == "cancelled"
    assert [c for c in read_snapshot(ctx("eve"), DEVICE_E).calls if c.status.value != "ended"] == []


def test_a_member_whose_devices_go_silent_leaves_the_group() -> None:
    room = group(people=("bob", "carol", "dan"))
    for user, device in (("bob", DEVICE_B), ("carol", DEVICE_C), ("dan", DEVICE_D)):
        answer(ring_for(user, device)["id"], user, device)
    # Bob's phone stopped sending heartbeats a minute and a half ago; the others' did not.
    long_ago = utc_now() - timedelta(seconds=90)
    for call in memory_call_store.live(ctx("bob")):
        memory_call_store.save(ctx("bob"), call.model_copy(update={"heartbeat_at": long_ago, "answered_at": long_ago}))
    stored = memory_call_store.get_room(ctx("bob"), room["id"])
    assert stored is not None
    for person in stored.members:
        if person.user_id == "bob":
            person.seen_at = person.joined_at = long_ago
    memory_call_store.save_room(ctx("bob"), stored)
    with open_call_unit(ctx("carol")) as unit:
        settle_stale(unit)
    after = room_of("alice", room["id"])
    assert after["status"] == "live"
    assert states(after) == {"alice": "joined", "bob": "left", "carol": "joined", "dan": "joined"}
    assert pairs("carol", DEVICE_C) == [("alice", "carol", "accepted"), ("dan", "carol", "accepted")]


def test_a_voice_call_becomes_a_video_call_without_hanging_up(monkeypatch: pytest.MonkeyPatch) -> None:
    call = place()
    not_yet = client.post(f"/api/v1/app-calls/{call['id']}/media", headers=headers("alice"), json={"media": "video"})
    assert not_yet.status_code == 409
    answer(call["id"], "bob", DEVICE_B, group_capable=False)
    assert client.post(f"/api/v1/app-calls/{call['id']}/media", headers=headers("dan"), json={}).status_code == 404
    switched = client.post(f"/api/v1/app-calls/{call['id']}/media", headers=headers("bob"), json={"media": "video", "source": "camera"})
    assert switched.status_code == 200 and switched.json()["media"] == "video"
    assert client.get(f"/api/v1/app-calls/{call['id']}", headers=headers("alice")).json()["media"] == "video"
    # The other side's stream shows the change.
    assert [d["media"] for name, d in _collect("alice", DEVICE_A) if name == "call"] == ["video"]
    audit = [r for r in store.audit_records if r.action == "app_call.video_on"]
    assert len(audit) == 1 and audit[0].metadata["source"] == "camera" and audit[0].actor == "bob"
    monkeypatch.setattr(settings, "calls_video_enabled", False)
    refused = client.post(f"/api/v1/app-calls/{call['id']}/media", headers=headers("alice"), json={"source": "screen"})
    assert refused.status_code == 422 and "Screen sharing" in refused.json()["error"]["message"]


def test_sharing_a_screen_in_a_group_call_marks_this_persons_connections_as_video() -> None:
    room = group()
    answer(ring_for("bob", DEVICE_B)["id"], "bob", DEVICE_B)
    carol = answer(ring_for("carol", DEVICE_C)["id"], "carol", DEVICE_C)
    shared = client.post(f"/api/v1/app-calls/{carol['id']}/media", headers=headers("carol"), json={"source": "screen"})
    assert shared.status_code == 200
    media = {(c.caller_user_id, c.callee_user_id): c.media.value for c in read_snapshot(ctx("bob"), DEVICE_B).calls}
    assert media == {("alice", "bob"): "audio", ("carol", "bob"): "video"}
    assert room_of("alice", room["id"])["media"] == "video"


def test_the_stream_sends_the_group_with_its_calls() -> None:
    room = group()
    frames = _collect("bob", DEVICE_B)
    assert [name for name, _ in frames] == ["call", "room"]
    assert frames[1][1]["id"] == room["id"] and states(frames[1][1])["bob"] == "invited"
    assert frames[1][1]["members"][1]["is_you"] is True
    assert _collect("dan", DEVICE_D) == []


def test_delete_my_data_removes_me_from_group_calls() -> None:
    room = group()
    answer(ring_for("bob", DEVICE_B)["id"], "bob", DEVICE_B)
    deleted = client.post("/api/v1/privacy/delete", headers=headers("alice"), json={"confirm": "DELETE MY DATA"})
    assert deleted.status_code == 200, deleted.text
    seen = room_of("bob", room["id"])
    host = seen["members"][0]
    assert host["user_id"].startswith("deleted:") and host["name"] == "Deleted user" and host["state"] == "left"
    assert seen["host_user_id"] == host["user_id"]
    assert "alice" not in str(seen)
    assert memory_call_store.my_rooms(ctx("alice")) == []
    exported = client.get("/api/v1/privacy/export", headers=headers("bob")).json()["app_calls"]
    assert exported and all("room_id" not in item for item in exported)
