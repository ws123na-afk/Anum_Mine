"""The Green Concrete planner: people, seats, L table geometry, shift windows and the hash (no database)."""
from __future__ import annotations

import pytest

from anum_api import office_green_concrete as gc


def test_people_and_roles_are_the_owners_words() -> None:
    assert [(p.name, p.role_en, p.manager) for p in gc.PEOPLE] == [
        ("Raed", "Business analyst", False), ("Hazrat", "Data monitoring", False), ("Mai", "Data monitoring", False),
        ("Shahzeb", "Tyre planning engineer", False), ("Ijaz", "Data collector", False), ("Badea", "Data monitoring", False),
        ("Ali Zeehan", "Data monitoring", False), ("Amjad", "Data collector", False), ("Rana", "Data collector", False),
        ("Ahmed Elbendari", "PMV manager", True)]
    assert all(p.role_ar for p in gc.PEOPLE)


def test_working_hours_are_the_owners_answer_and_there_is_no_night_shift() -> None:
    hours = {p.key: p.hours for p in gc.PEOPLE}
    assert hours["amjad"] == ("13:00", "22:00")
    assert hours["ijaz"] == hours["ali-zeehan"] == hours["rana"] == ("06:00", "14:00"), "the morning shift"
    for key in ("raed", "hazrat", "mai", "shahzeb", "badea", "ahmed-elbendari"):
        assert hours[key] == ("08:00", "17:00"), "everyone not named works 08:00 to 17:00 until the owner names the 09:00 to 18:00 people"
    assert gc.NINE_TO_SIX == () and gc.NINE_TO_SIX_HOURS == ("09:00", "18:00")
    seats = gc.plan(gc.Facts(), "Asia/Dubai")["seats"]
    assert all(s["shift"] is None for s in seats), "no seat belongs to a day or night shift any more"
    assert [(s["work_start"], s["work_end"]) for s in seats] == [p.hours for p in gc.PEOPLE]


def test_naming_the_nine_to_six_people_is_one_data_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gc, "NINE_TO_SIX", ("mai", "badea"))
    assert gc.hours_for("mai") == gc.hours_for("badea") == ("09:00", "18:00")
    assert gc.hours_for("raed") == ("08:00", "17:00") and gc.hours_for("amjad") == ("13:00", "22:00")


def test_everyone_has_their_own_seat_in_the_long_leg_order_then_the_short_legs() -> None:
    team = [gc.seat_of(p) for p in gc.PEOPLE if not p.manager]
    assert [s.slot for s in team] == list(range(1, 10))
    assert len({(s.x, s.z) for s in team}) == 9, "nobody shares a physical seat"
    long = team[:4]
    assert [p.name for p in gc.PEOPLE[:4]] == ["Raed", "Hazrat", "Mai", "Shahzeb"]
    assert [s.x for s in long] == sorted(s.x for s in long) and len({s.z for s in long}) == 1 and {s.facing for s in long} == {"north"}
    right, left = team[4:6], team[6:9]
    assert {s.facing for s in right} == {"east"} and right[0].z < right[1].z and min(s.x for s in right) > max(s.x for s in long)
    assert [p.name for p in gc.PEOPLE[6:9]] == ["Ali Zeehan", "Amjad", "Rana"], "the former night people take the next places"
    assert {s.facing for s in left} == {"west"} and [s.z for s in left] == sorted(s.z for s in left) and max(s.x for s in left) < min(s.x for s in long)
    assert {s.group for s in team} == {gc.GROUP_L}, "one table"
    manager = gc.seat_of(gc.PEOPLE[-1])
    assert manager.group == gc.GROUP_BACK and manager.z > long[0].z, "the manager sits behind the team"
    assert [gc.desk_label(p) for p in gc.PEOPLE] == [*(f"Seat {i}" for i in range(1, 10)), "Manager desk"]


def test_every_seat_is_inside_the_room_and_persons_do_not_overlap() -> None:
    def person_at(s: gc.Seat) -> tuple[float, float]:
        return {"north": (s.x, s.z + 0.82), "east": (s.x - 0.82, s.z), "west": (s.x + 0.82, s.z), "south": (s.x, s.z - 0.82)}[s.facing]

    for s in [*gc.L_SEATS, gc.MANAGER_SEAT]:
        assert 0.8 < s.x < gc.ROOM_W - 0.8 and 0.8 < s.z < gc.ROOM_D - 0.8
    people = [person_at(s) for s in [*gc.L_SEATS, gc.MANAGER_SEAT]]
    for i, a in enumerate(people):
        for b in people[i + 1:]:
            assert ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 >= 1.0
    assert gc.ROOM_X + gc.ROOM_W <= gc.FLOOR_W and gc.ROOM_Z + gc.ROOM_D <= gc.FLOOR_D
    assert len(gc.PEOPLE) <= gc.ROOM_CAPACITY


def test_hours_windows_wrap_over_midnight() -> None:
    at = lambda hh, mm=0: hh * 60 + mm  # noqa: E731
    assert gc.in_window(at(8), "08:00", "17:00") and not gc.in_window(at(17), "08:00", "17:00") and not gc.in_window(at(7, 59), "08:00", "17:00")
    assert gc.in_window(at(23), "22:00", "06:00") and gc.in_window(at(2), "22:00", "06:00") and not gc.in_window(at(6), "22:00", "06:00")
    assert not gc.in_window(at(12), "22:00", "06:00")
    assert gc.seat_window({"work_start": "13:00", "work_end": "22:00", "shift": "day"}) == ("13:00", "22:00"), "own hours win"
    assert gc.seat_window({"shift": "night"}) == ("19:00", "07:00"), "an older night seat reads the default building hours"
    assert gc.seat_window({"shift": "day", "shift_day_start": "06:30", "shift_night_start": "18:30"}) == ("06:30", "18:30")
    assert gc.seat_window({"shift": None}) is None


def test_older_day_and_night_seats_still_read_the_building_hours() -> None:
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
    # A build made before the working hours keeps its seats: the action never moves or changes them, it lists them for the owner.
    assert done["older_seats"] == sorted(gc.desk_id(i + 1) for i in range(10))
    assert all(s["action"] == "skip" and s["reason"] == gc.EXISTS for s in done["seats"])
    hours_set = {k: {**v, "work_start": "08:00"} for k, v in (facts.desks or {}).items()}
    assert gc.plan(gc.Facts(**{**{f: getattr(facts, f) for f in facts.__slots__}, "desks": hours_set}), "Asia/Dubai")["older_seats"] == []
    assert gc.plan(gc.Facts(), "Asia/Dubai")["older_seats"] == []


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
    other_hours = gc.plan(gc.Facts(), "Asia/Dubai")
    other_hours["seats"][0] = {**other_hours["seats"][0], "work_end": "18:00"}
    assert a != gc.proposal_hash("t", "w", "u1", "Asia/Dubai", other_hours), "the hours are part of the hash"


def test_the_web_fixture_is_the_planners_own_output() -> None:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "apps/web/test/fixtures/greenConcrete.json"
    p = gc.plan(gc.Facts(), "Asia/Dubai")
    stored = json.loads(root.read_text(encoding="utf-8"))
    assert stored["room"]["id"] == gc.ROOM_ID and stored["room"]["kind"] == "command"
    fields = ("shift", "seat_order", "facing", "seat_kind", "table_group", "work_start", "work_end", "label")
    assert [(d["id"], d["pos_x"], d["pos_z"], *(d[f] for f in fields)) for d in stored["desks"]] == [
        (s["desk_id"], s["pos_x"], s["pos_z"], *(s[f] for f in fields)) for s in p["seats"]]
    assert [(x["name"], x["role"], x["role_ar"], x["seat_kind"], x["work_start"], x["work_end"]) for x in stored["people"]] == [
        (q.name, q.role_en, q.role_ar, "manager" if q.manager else "team", *q.hours) for q in gc.PEOPLE]


# ---------------------------------------------------------------- the look the owner picks (never read from a name)

def test_the_look_is_only_what_the_owner_chose() -> None:
    assert gc.clean_appearance(None, None) == {}, "nothing chosen, nothing stored"
    everyone = gc.clean_appearance("neutral", None)
    assert set(everyone) == {p.key for p in gc.PEOPLE} and set(everyone.values()) == {"neutral"}, "the default covers every person alike, whatever the name"
    mixed = gc.clean_appearance("neutral", {"raed": "masculine", "mai": "feminine"})
    assert mixed["raed"] == "masculine" and mixed["mai"] == "feminine" and mixed["hazrat"] == "neutral"
    for bad in (("neutral", {"nobody": "neutral"}), ("neutral", {"raed": "tall"}), ("robot", None)):
        with pytest.raises(ValueError):
            gc.clean_appearance(*bad)


def test_the_look_applies_to_new_agents_only_and_is_part_of_the_hash() -> None:
    looks = gc.clean_appearance("neutral", {"raed": "masculine"})
    facts = gc.Facts(agents={gc.marker(gc.PEOPLE_BY_KEY["hazrat"]): "a2"})
    p = gc.plan(facts, "Asia/Dubai", looks)
    by = {x["key"]: x for x in p["people"]}
    assert by["raed"]["appearance"] == "masculine" and by["mai"]["appearance"] == "neutral"
    assert by["hazrat"]["agent_action"] == "exists" and by["hazrat"]["appearance"] is None, "an existing agent is never changed"
    a = gc.proposal_hash("t", "w", "u", "Asia/Dubai", p)
    b = gc.proposal_hash("t", "w", "u", "Asia/Dubai", gc.plan(facts, "Asia/Dubai", gc.clean_appearance("neutral", {"raed": "feminine"})))
    c = gc.proposal_hash("t", "w", "u", "Asia/Dubai", gc.plan(facts, "Asia/Dubai"))
    assert len({a, b, c}) == 3, "another look, or none, is another plan"
    # Without any look the plan still stores none (the previous behaviour).
    assert all(x["appearance"] is None for x in gc.plan(gc.Facts(), "Asia/Dubai")["people"])


def test_off_shift_desks_follow_each_persons_hours_in_the_building_time_zone() -> None:
    from datetime import datetime, timezone

    def own(desk: str, start: str, end: str, tz: str = "Asia/Dubai") -> dict:
        return {"desk_id": desk, "shift": None, "work_start": start, "work_end": end, "timezone": tz,
                "shift_day_start": None, "shift_night_start": None}

    seats = [own("raed", "08:00", "17:00"), own("ijaz", "06:00", "14:00"), own("amjad", "13:00", "22:00"), own("late", "22:00", "06:00"),
             {"desk_id": "plain", "shift": None, "work_start": None, "work_end": None, "timezone": "Asia/Dubai"}]
    dubai = lambda hh, mm=0: datetime(2026, 10, 28, (hh - 4) % 24, mm, tzinfo=timezone.utc)  # noqa: E731
    assert gc.off_shift_desks(seats, dubai(7)) == {"raed", "amjad", "late"}
    assert gc.off_shift_desks(seats, dubai(12)) == {"amjad", "late"}
    assert gc.off_shift_desks(seats, dubai(16)) == {"ijaz", "late"}
    assert gc.off_shift_desks(seats, dubai(18, 30)) == {"raed", "ijaz", "late"}
    assert gc.off_shift_desks(seats, dubai(21)) == {"raed", "ijaz", "late"}
    assert gc.off_shift_desks(seats, dubai(23)) == {"raed", "ijaz", "amjad"}, "a window over midnight works"
    assert gc.off_shift_desks([own("x", "08:00", "17:00", tz="Not/AZone")], datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)) == {"x"}, "an unknown zone reads UTC"


def test_older_day_and_night_seats_count_off_shift_by_the_building_hours() -> None:
    from datetime import datetime, timezone

    def seat(desk: str, shift: str | None, tz: str = "Asia/Dubai", day: str | None = None, night: str | None = None) -> dict:
        return {"desk_id": desk, "shift": shift, "timezone": tz, "shift_day_start": day, "shift_night_start": night}

    seats = [seat("d1", "day"), seat("d7", "night"), seat("m", None)]
    noon_dubai = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)  # 12:00 in Dubai
    assert gc.off_shift_desks(seats, noon_dubai) == {"d7"}
    late_dubai = datetime(2026, 10, 28, 16, 30, tzinfo=timezone.utc)  # 20:30 in Dubai: night, the manager stays on
    assert gc.off_shift_desks(seats, late_dubai) == {"d1"}
    # Edited hours move the edge: day from 09:00, so 08:30 in Dubai is still night.
    edited = [seat("d1", "day", day="09:00", night="21:00"), seat("d7", "night", day="09:00", night="21:00")]
    assert gc.off_shift_desks(edited, datetime(2026, 10, 28, 4, 30, tzinfo=timezone.utc)) == {"d1"}
    # An unknown zone reads UTC, never raises.
    assert gc.off_shift_desks([seat("d1", "day", tz="Not/AZone")], datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)) == {"d1"}
    assert gc.active_shift(noon_dubai, None, None, None) == "day"
