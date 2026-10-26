"""The Green Concrete planner: people, seats, L table geometry, shift windows and the hash (no database)."""
from __future__ import annotations

from anum_api import office_green_concrete as gc


def test_people_and_roles_are_the_owners_words() -> None:
    assert [(p.name, p.role_en, p.shift) for p in gc.PEOPLE] == [
        ("Raed", "Business analyst", "day"), ("Hazrat", "Data monitoring", "day"), ("Mai", "Data monitoring", "day"),
        ("Shahzeb", "Tyre planning engineer", "day"), ("Ijaz", "Data collector", "day"), ("Badea", "Data monitoring", "day"),
        ("Ali Zeehan", "Data monitoring", "night"), ("Amjad", "Data collector", "night"), ("Rana", "Data collector", "night"),
        ("Ahmed Elbendari", "PMV manager", None)]
    assert all(p.role_ar for p in gc.PEOPLE)


def test_day_seats_run_left_to_right_then_the_short_leg_and_night_shares_the_tables() -> None:
    day = [gc.seat_of(p) for p in gc.PEOPLE if p.shift == "day"]
    assert [s.slot for s in day] == [1, 2, 3, 4, 5, 6]
    assert [s.x for s in day[:4]] == sorted(s.x for s in day[:4]) and len({s.z for s in day[:4]}) == 1
    assert {s.facing for s in day[:4]} == {"north"} and {s.facing for s in day[4:]} == {"east"}
    assert day[4].z < day[5].z, "the short leg runs towards the door"
    night = [gc.seat_of(p) for p in gc.PEOPLE if p.shift == "night"]
    assert [(s.x, s.z) for s in night] == [(s.x, s.z) for s in day[:3]], "night people use the same tables"
    manager = gc.seat_of(gc.PEOPLE[-1])
    assert manager.group == gc.GROUP_BACK and manager.z > max(s.z for s in day), "the manager sits behind the team"


def test_every_seat_is_inside_the_room_and_persons_do_not_overlap() -> None:
    for s in [*gc.L_SEATS, gc.MANAGER_SEAT]:
        assert 0 < s.x < gc.ROOM_W and 0 < s.z < gc.ROOM_D
    people = [(s.x, s.z + 0.82) if s.facing == "north" else (s.x - 0.82, s.z) for s in gc.L_SEATS]
    for i, a in enumerate(people):
        for b in people[i + 1:]:
            assert ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 >= 1.0
    assert gc.ROOM_X + gc.ROOM_W <= gc.FLOOR_W and gc.ROOM_Z + gc.ROOM_D <= gc.FLOOR_D
    assert len(gc.PEOPLE) <= gc.ROOM_CAPACITY


def test_shift_windows_wrap_over_midnight_and_follow_edited_hours() -> None:
    at = lambda hh, mm=0: hh * 60 + mm  # noqa: E731
    assert gc.shift_at(at(6, 59), "07:00", "19:00") == "night"
    assert gc.shift_at(at(7), "07:00", "19:00") == "day"
    assert gc.shift_at(at(18, 59), "07:00", "19:00") == "day"
    assert gc.shift_at(at(19), "07:00", "19:00") == "night"
    assert gc.shift_at(at(0), "07:00", "19:00") == "night"
    assert gc.shift_at(at(5), "06:30", "18:30") == "night" and gc.shift_at(at(6, 30), "06:30", "18:30") == "day"
    assert gc.shift_at(at(2), "19:00", "07:00") == "day", "hours may be given the other way round"
    assert gc.valid_hours(None, None) == ("07:00", "19:00", True)
    assert gc.valid_hours("06:00", "18:00") == ("06:00", "18:00", False)


def test_plan_of_an_empty_workspace_and_idempotent_facts() -> None:
    p = gc.plan(gc.Facts(), "Asia/Dubai")
    assert p["counts"]["create"] == {"buildings": 1, "floors": 1, "rooms": 1, "desks": 10, "agents": 10} and p["status"] == "new"
    assert all(x["seat_action"] == "seat" for x in p["people"])
    facts = gc.Facts(building={"id": gc.BUILDING_ID}, floor={"id": gc.FLOOR_ID}, room={"id": gc.ROOM_ID},
                     desks={gc.desk_id(i + 1): {"id": gc.desk_id(i + 1), "agent_id": f"a{i}"} for i in range(10)},
                     agents={gc.marker(person): f"a{i}" for i, person in enumerate(gc.PEOPLE)},
                     agent_desk={f"a{i}": gc.desk_id(i + 1) for i in range(10)})
    done = gc.plan(facts, "Asia/Dubai")
    assert not gc.has_work(done) and done["status"] == "complete"


def test_never_overwrites_a_taken_desk_or_a_seated_agent() -> None:
    facts = gc.Facts(building={"id": gc.BUILDING_ID}, floor={"id": gc.FLOOR_ID}, room={"id": gc.ROOM_ID},
                     desks={gc.desk_id(1): {"id": gc.desk_id(1), "agent_id": "stranger"}},
                     agents={gc.marker(gc.PEOPLE[1]): "a2"}, agent_desk={"a2": "elsewhere"})
    p = gc.plan(facts, "Asia/Dubai")
    assert p["people"][0]["seat_reason"] == gc.DESK_TAKEN
    assert p["people"][1]["seat_reason"] == gc.AGENT_ELSEWHERE and p["people"][1]["agent_action"] == "exists"


def test_hash_binds_the_person_the_workspace_and_the_plan() -> None:
    p = gc.plan(gc.Facts(), "Asia/Dubai")
    a = gc.proposal_hash("t", "w", "u1", "Asia/Dubai", p)
    assert a != gc.proposal_hash("t", "w", "u2", "Asia/Dubai", p) and a != gc.proposal_hash("t", "w2", "u1", "Asia/Dubai", p)
    assert a != gc.proposal_hash("t", "w", "u1", "Asia/Dubai", gc.plan(gc.Facts(building={"id": gc.BUILDING_ID}), "Asia/Dubai"))
