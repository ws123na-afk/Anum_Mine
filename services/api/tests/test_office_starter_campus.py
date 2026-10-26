"""Pure rules of the starter campus: master list honesty, geometry, idempotent planning, hash binding."""
from __future__ import annotations

import re

from anum_api import office_layout as layout
from anum_api import office_starter_campus as sc

ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
ZONE = "Asia/Dubai"


def facts_from(plans: list[dict], skip_rooms: set[str] = frozenset()) -> sc.Facts:
    """The records a full confirm would leave behind."""
    facts = sc.Facts()
    for p in plans:
        facts.buildings[p["id"]] = {"id": p["id"], "name": p["name"]["en"], "timezone": ZONE}
        for f in p["floors"]:
            facts.floors[f["id"]] = {"id": f["id"], "building_id": p["id"], "level": f["level"], "name": f["name"]["en"],
                                     "width_m": f["width_m"], "depth_m": f["depth_m"]}
            for r in f["rooms"]:
                if r["id"] in skip_rooms:
                    continue
                facts.rooms[r["id"]] = {"id": r["id"], "floor_id": f["id"], "name": r["name"]["en"], "scene_x": r["x"],
                                        "scene_z": r["z"], "scene_width": r["width"], "scene_depth": r["depth"],
                                        "team_department": r["team_department"]}
                facts.desks.update(d["id"] for d in r["desks"])
    return facts


def test_master_list_is_complete_and_valid() -> None:
    assert [b.en for b in sc.CAMPUS] == [
        "ANUM HQ", "ANUM 360 Commerce", "Tyre Pulse", "Corporate Services", "Knowledge & AI", "New Ventures",
        "Personal Office", "Career & Employment"]
    # Every department of the owner's master list lives somewhere inside the six business buildings.
    rooms = {r.en for b in sc.CAMPUS for f in b.floors for r in f.rooms}
    floors = " ".join(f.en for b in sc.CAMPUS for f in b.floors)
    for room in ("Reception", "Command Center", "AI Organization", "Executive Office", "Boardroom", "Strategy Room", "Fleet", "Maintenance",
                 "Tyre Engineering", "Inspections", "Workshop", "Warehouse", "Sales Office", "Orders", "Customer Service", "Logistics",
                 "Finance Office", "Assets", "Procurement", "Suppliers", "Support", "Messages", "Tickets", "Development", "Deployments",
                 "Incidents", "Research Room", "Legal Office", "Compliance Office", "Security Operations", "Governance Office",
                 "Personal Office", "HR", "AI Labs", "Data", "Automation", "Investments", "R&D", "Future Businesses", "E-commerce", "Retail", "Marketplace"):
        assert room in rooms, room
    for label in ("Basement", "Rooftop", "Floor 3: Executive and Strategy", "Sales and Marketing", "Customer Experience", "Research and Intelligence"):
        assert label in floors, label
    ids: set[str] = set()
    for b in sc.CAMPUS:
        assert b.en and b.ar and ID.match(b.id)
        levels = [f.level for f in b.floors]
        assert len(set(levels)) == len(levels)
        for f in b.floors:
            assert ID.match(sc.floor_id(b, f)) and f.en and f.ar
            names = [r.en for r in f.rooms]
            assert len(set(names)) == len(names), "room names are unique per floor"
            for r in f.rooms:
                rid = sc.room_id(b, r)
                assert ID.match(rid) and rid not in ids
                ids.add(rid)
                assert r.en and r.ar and re.search(r"[؀-ۿ]", r.ar)
                assert r.kind in layout.ROOM_KINDS and r.color in layout.ROOM_COLORS and r.door_side in layout.DOOR_SIDES
                assert r.department is None or r.department in layout.TEAM_DEPARTMENTS
                assert {k for k, _ in r.equipment} <= set(layout.EQUIPMENT_KINDS)
                assert len({k for k, _ in r.equipment}) == len(r.equipment)
                assert r.desks == 0 or r.kind in layout.DESK_ROOM_KINDS
                assert r.desks <= r.capacity, "desks never exceed capacity"
                assert all(g is not None for g in layout.desk_grid(r.width, r.depth, r.desks)), "every desk fits"


def test_text_has_no_dashes_or_model_names() -> None:
    text = " ".join(f"{b.en} {b.ar} " + " ".join(f"{f.en} {f.ar} " + " ".join(f"{r.en} {r.ar}" for r in f.rooms) for f in b.floors)
                    for b in sc.CAMPUS)
    assert "–" not in text and "—" not in text
    assert not any(w in text.lower() for w in ("claude", "gpt", "opus", "sonnet", "haiku"))


def test_empty_workspace_plan_creates_everything_inside_bounds_without_overlap() -> None:
    plans = sc.plan_campus(sc.Facts(), ZONE)
    tot = sc.totals(plans)["create"]
    assert tot == {"buildings": 8, "floors": 18, "rooms": 62, "desks": 220}
    for p in plans:
        assert p["status"] == "new" and p["action"] == "create"
        for f in p["floors"]:
            rects = [layout.Rect(r["id"], r["x"], r["z"], r["width"], r["depth"]) for r in f["rooms"]]
            assert layout.validate_layout(rects, {r.id for r in rects}, (f["width_m"], f["depth_m"])) == []
            for r in f["rooms"]:
                assert r["action"] == "create" and len(r["desks"]) == r["desk_count"]
                for d in r["desks"]:
                    assert layout.point_in_room(layout.Rect(r["id"], r["x"], r["z"], r["width"], r["depth"]), d["pos_x"], d["pos_z"])


def test_plan_is_deterministic_and_a_full_run_leaves_nothing_to_add() -> None:
    first = sc.plan_campus(sc.Facts(), ZONE)
    assert first == sc.plan_campus(sc.Facts(), ZONE)
    again = sc.plan_campus(facts_from(first), ZONE)
    assert all(p["status"] == "complete" for p in again)
    assert not any(sc.totals(again)["create"].values())
    assert sc.totals(again)["skip"] == {"buildings": 8, "floors": 18, "rooms": 62, "desks": 0}


def test_rerun_adds_only_what_is_missing_and_keeps_what_exists() -> None:
    first = sc.plan_campus(sc.Facts(), ZONE)
    missing = "campus-02-commerce-orders"
    facts = facts_from(first, skip_rooms={missing})
    plans = sc.plan_campus(facts, ZONE)
    commerce = next(p for p in plans if p["key"] == "commerce")
    assert commerce["status"] == "partial" and commerce["action"] == "skip"
    created = [r for f in commerce["floors"] for r in f["rooms"] if r["action"] == "create"]
    assert [r["id"] for r in created] == [missing]
    assert (created[0]["x"], created[0]["z"]) == next((r["x"], r["z"]) for r in first[1]["floors"][0]["rooms"] if r["id"] == missing)
    assert sc.totals(plans)["create"] == {"buildings": 0, "floors": 0, "rooms": 1, "desks": 6}


def test_user_edited_records_are_skipped_never_changed() -> None:
    first = sc.plan_campus(sc.Facts(), ZONE)
    facts = facts_from(first)
    facts.rooms["campus-01-hq-reception"]["name"] = "Front desk"  # renamed by the owner: still the same record
    facts.rooms["campus-02-commerce-retail"]["scene_x"] = 30.0  # moved by the owner
    plans = sc.plan_campus(facts, ZONE)
    assert not any(sc.totals(plans)["create"].values())
    # A room the owner deleted whose name is now used by another room is not recreated on top of it.
    del facts.rooms["campus-02-commerce-orders"]
    facts.rooms["mine"] = {"id": "mine", "floor_id": "campus-02-commerce-l0", "name": "Orders", "scene_x": None,
                           "scene_z": None, "scene_width": None, "scene_depth": None}
    reasons = [r["reason"] for p in sc.plan_campus(facts, ZONE) if p["key"] == "commerce" for f in p["floors"] for r in f["rooms"] if r["id"].endswith("-orders")]
    assert reasons == [sc.NAME_IN_USE]


def test_a_level_the_owner_filled_is_skipped_with_its_rooms() -> None:
    facts = sc.Facts()
    facts.buildings["campus-07-personal"] = {"id": "campus-07-personal"}
    facts.floors["mine"] = {"id": "mine", "building_id": "campus-07-personal", "level": 0, "name": "Mine", "width_m": 20.0, "depth_m": 20.0}
    plan = next(p for p in sc.plan_campus(facts, ZONE) if p["key"] == "personal")
    floor = plan["floors"][0]
    assert floor["action"] == "skip" and floor["reason"] == sc.LEVEL_IN_USE
    assert {r["reason"] for r in floor["rooms"]} == {sc.FLOOR_UNAVAILABLE}
    assert plan["status"] == "complete"


def test_a_new_room_on_a_crowded_existing_floor_goes_to_a_free_spot_or_says_no_space() -> None:
    first = sc.plan_campus(sc.Facts(), ZONE)
    facts = facts_from(first, skip_rooms={"campus-04-corporate-services-wealth-office"})
    facts.rooms["blocker"] = {"id": "blocker", "floor_id": "campus-04-corporate-services-l0", "name": "Blocker",
                              "scene_x": 2.0, "scene_z": 12.0, "scene_width": 10.0, "scene_depth": 8.0}
    fin = next(p for p in sc.plan_campus(facts, ZONE) if p["key"] == "corporate-services")
    room = next(r for r in fin["floors"][0]["rooms"] if r["id"].endswith("wealth-office"))
    assert room["action"] == "create" and (room["x"], room["z"]) != (2.0, 12.0), "moved off the owner's room"
    placed = [layout.Rect(r["id"], r["scene_x"], r["scene_z"], r["scene_width"], r["scene_depth"]) for r in facts.floor_rooms(fin["floors"][0]["id"])]
    new = layout.Rect("new", room["x"], room["z"], room["width"], room["depth"])
    assert layout.within_bounds(new, 34.0, 22.0) and not any(layout.overlaps(new, o) for o in placed)
    facts.floors["campus-04-corporate-services-l0"]["width_m"] = 12.0
    facts.floors["campus-04-corporate-services-l0"]["depth_m"] = 12.0
    full = next(p for p in sc.plan_campus(facts, ZONE) if p["key"] == "corporate-services")
    assert [r["reason"] for r in full["floors"][0]["rooms"] if r["id"].endswith("wealth-office")] == [sc.NO_SPACE]


def test_hash_binds_person_workspace_selection_zone_and_records() -> None:
    plans = sc.plan_campus(sc.Facts(), ZONE)
    base = sc.proposal_hash("t", "w", "u", ZONE, plans)
    assert base != sc.proposal_hash("t", "w", "other", ZONE, plans)
    assert base != sc.proposal_hash("t", "w2", "u", ZONE, plans)
    assert base != sc.proposal_hash("t", "w", "u", "Asia/Riyadh", sc.plan_campus(sc.Facts(), "Asia/Riyadh"))
    assert base != sc.proposal_hash("t", "w", "u", ZONE, sc.plan_campus(sc.Facts(), ZONE, ["hq"]))
    assert base != sc.proposal_hash("t", "w", "u", ZONE, sc.plan_campus(facts_from(plans[:1]), ZONE))
    assert re.fullmatch(r"[0-9a-f]{64}", base)


def test_master_list_departments_use_the_new_department_set() -> None:
    by_room = {r.key: r.department for b in sc.CAMPUS for f in b.floors for r in f.rooms}
    assert by_room["marketing-studio"] == "marketing" and by_room["legal-office"] == "legal"
    assert by_room["compliance-office"] == "compliance" and by_room["governance-office"] == "compliance"
    assert by_room["security-operations"] == "security" and by_room["hr"] == "hr" and by_room["procurement"] == "procurement"
    assert {by_room[k] for k in ("development", "deployments", "incidents")} == {"technology"}
    assert {by_room[k] for k in ("research-room", "intelligence-desk", "research-development")} == {"research"}
    # Rooms with no matching department stay unset: no guessing.
    assert by_room["facilities"] is None and by_room["command-center"] is None and by_room["ai-labs"] is None


def test_a_rerun_fills_only_an_empty_or_custom_department_of_a_campus_room() -> None:
    plans = sc.plan_campus(sc.Facts(), ZONE)
    facts = facts_from(plans)
    marketing = "campus-02-commerce-marketing-studio"
    legal = "campus-04-corporate-services-legal-office"
    hr = "campus-04-corporate-services-hr"
    sales = "campus-02-commerce-sales-office"
    facts.rooms[marketing]["team_department"] = None
    facts.rooms[legal]["team_department"] = "custom"
    facts.rooms[hr]["team_department"] = "sales"        # the owner chose another department: kept
    facts.rooms[sales]["team_department"] = None        # the master list says sales: filled
    again = sc.plan_campus(facts, ZONE)
    rooms = {r["id"]: r for p in again for f in p["floors"] for r in f["rooms"]}
    assert rooms[marketing]["department_set"] == {"from": None, "to": "marketing"}
    assert rooms[legal]["department_set"] == {"from": "custom", "to": "legal"}
    assert rooms[sales]["department_set"] == {"from": None, "to": "sales"}
    assert "department_set" not in rooms[hr]
    assert all(r["action"] == "skip" for r in rooms.values()), "nothing is created or moved"
    assert sc.totals(again)["update"] == {"room_departments": 3}
    assert sc.totals(again)["create"] == {"buildings": 0, "floors": 0, "rooms": 0, "desks": 0}
    assert all(p["status"] == "partial" for p in again if sc.has_work(p))
    # A room the master list has no department for is never touched, even when empty.
    assert "department_set" not in rooms["campus-01-hq-facilities"]
    # Once filled the plan is complete again (idempotent).
    facts.rooms[marketing]["team_department"] = "marketing"
    facts.rooms[legal]["team_department"] = "legal"
    facts.rooms[sales]["team_department"] = "sales"
    assert all(p["status"] == "complete" for p in sc.plan_campus(facts, ZONE))


def test_command_center_is_the_command_room_kind() -> None:
    """The operations command centre is a room kind of its own (opens the live flow), not an office named by words."""
    rooms = [r for b in sc.CAMPUS for f in b.floors for r in f.rooms if r.kind == "command"]
    assert [r.en for r in rooms] == ["Command Center"]
    room = rooms[0]
    assert room.desks == 6 and "command" in layout.DESK_ROOM_KINDS and room.color == "crimson"
    assert ("display", 4) in room.equipment
    # Nothing else in the master list is decorated as a command room by its name only.
    assert all(r.kind != "office" or "command" not in r.en.lower() for b in sc.CAMPUS for f in b.floors for r in f.rooms)


def test_web_fixture_of_the_master_list_matches() -> None:
    """The web unit tests audit every starter room (kind and department) from a fixture; it must equal the master list."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "apps" / "web" / "test" / "fixtures" / "starterCampusRooms.json"
    expected = [
        {"building": b.key, "building_id": b.id, "floor": f.level, "room": r.key, "name": r.en, "name_ar": r.ar, "kind": r.kind, "department": r.department}
        for b in sc.CAMPUS for f in b.floors for r in f.rooms
    ]
    assert json.loads(path.read_text(encoding="utf-8")) == expected, "regenerate apps/web/test/fixtures/starterCampusRooms.json from CAMPUS"


def test_web_registry_fixture_has_the_planner_names_in_both_languages() -> None:
    """The registry the web and browser tests share (apps/web/test/fixtures/starterCampus.json) equals the master list's names."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "apps" / "web" / "test" / "fixtures" / "starterCampus.json"
    fx = json.loads(path.read_text(encoding="utf-8"))
    assert [(b["id"], b["name"], b["name_ar"]) for b in fx["buildings"]] == [(b.id, b.en, b.ar) for b in sc.CAMPUS]
    assert [(f["id"], f["name"], f["name_ar"]) for f in fx["floors"]] == [(sc.floor_id(b, f), f.en, f.ar) for b in sc.CAMPUS for f in b.floors]
    assert [(r["id"], r["name"], r["name_ar"]) for r in fx["rooms"]] == [
        (sc.room_id(b, r), r.en, r.ar) for b in sc.CAMPUS for f in b.floors for r in f.rooms]


def test_web_browser_master_fixture_matches() -> None:
    """The browser tests' fake server (e2e/support/starterCampusMaster.json) is generated from the master list; keep it equal."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "apps" / "web" / "e2e" / "support" / "starterCampusMaster.json"
    master = json.loads(path.read_text(encoding="utf-8"))
    assert [(b["key"], b["en"], b["ar"]) for b in master] == [(b.key, b.en, b.ar) for b in sc.CAMPUS]
    assert [(f["en"], f["ar"]) for b in master for f in b["floors"]] == [(f.en, f.ar) for b in sc.CAMPUS for f in b.floors]
    assert [(r["key"], r["en"], r["ar"], r["kind"]) for b in master for f in b["floors"] for r in f["rooms"]] == [
        (r.key, r.en, r.ar, r.kind) for b in sc.CAMPUS for f in b.floors for r in f.rooms]
