"""Fleet operations data from TyrePulse (docs/tyre-planning.md#fleet-operations-data).

Readings come from TyrePulse's ``inspections`` (one row per visit, an entry per wheel
position), vehicles, inspection schedules, workshop checklists, work orders (a 90-day
window), meter readings and sites are synced, and the large tables (work order line items,
parts issued) are read on demand. The assistant tools answer "how many vehicles had
inspections", a vehicle's tyres, open work orders and costs, read-only and tenant-scoped.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from test_tyre_api import (  # noqa: F401
    API,
    MEMBER,
    NOW,
    OWNER,
    TENANT,
    VIEWER,
    WORKSPACE,
    client,
    connect_and_sync,
    fake_source,
)
from test_tyre_planning import fleet as fleet_dataset
from tyre_fixtures import AS_OF, FakeSupabase

from anum_api import main
from anum_api.agent_tools import ToolCall
from anum_api.dependencies import memory_repository
from anum_api.schemas import RiskLevel, TenantContext
from anum_api.tyre import fleet, fleet_tools, sources, tools
from anum_api.tyre.analytics import overdue_tyres
from anum_api.tyre.mapping import apply_mapping, expand_rows
from anum_api.tyre.model import Assumptions, Entity, EntityMapping, TyreMapping

OWNER_CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_tyre", roles=["owner"])


@pytest.fixture(autouse=True)
def frozen_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fleet_tools, "utc_now", lambda: NOW)


def run(name: str, context: TenantContext = OWNER_CONTEXT, **arguments: Any):  # type: ignore[no-untyped-def]
    return asyncio.run(main.tool_registry.execute(ToolCall(name=name, arguments=arguments), context))


# --- mapping ----------------------------------------------------------------------------------


def test_inspection_entries_attach_by_serial_then_vehicle_position() -> None:
    mapping = TyreMapping(
        entities={
            Entity.TYRES: EntityMapping(source="t", fields={"id": "id", "vehicle_id": "asset", "position": "pos",
                                                             "fitted_at": "fitted", "serial": "sn"}),
            Entity.INSPECTIONS: EntityMapping(source="i", expand="cond", fields={
                "vehicle_id": "asset", "inspected_at": "day", "position": "cond.position",
                "tyre_serial": "cond.serial_number", "pressure": "cond.pressure_psi", "tread_depth": "cond.tread_depth_mm"}),
        },
        position_aliases={"F1L": "LHF1", "f1r ": "rhf1"},
    )
    rows = {
        Entity.TYRES: [
            {"id": "old", "asset": "T1", "pos": "LHF1", "fitted": "2025-01-01", "sn": None},
            {"id": "new", "asset": "T1", "pos": "LHF1 ", "fitted": "2026-06-01", "sn": None},
            {"id": "by-serial", "asset": "T9", "pos": "X", "fitted": "2026-01-01", "sn": "SN-7"},
        ],
        Entity.INSPECTIONS: [{"asset": "t1", "day": "2026-07-01", "cond": {
            "F1L": {"position": "F1L", "serial_number": "", "pressure_psi": "100", "tread_depth_mm": ""},
            "F1R": {"position": "F1R", "serial_number": "", "pressure_psi": "", "tread_depth_mm": ""},
            "R1Lo": {"position": "R1Lo", "serial_number": "", "pressure_psi": "98", "tread_depth_mm": "0"},
            "X": {"position": "X", "serial_number": "SN-7", "pressure_psi": "101", "tread_depth_mm": "9.5"},
        }}],
    }
    dataset, stats = apply_mapping(mapping, rows)
    by_position = {item.position: item for item in dataset.inspections}
    assert by_position["F1L"].tyre_id == "new"  # alias, vehicle (any case), and the tyre fitted on the day
    assert by_position["R1Lo"].tyre_id is None and by_position["R1Lo"].pressure_psi == 98  # kept for the position
    assert by_position["X"].tyre_id == "by-serial" and by_position["X"].tread_mm == 9.5
    assert "F1R" not in by_position  # an empty entry is no reading
    assert stats["inspections"].read == 4 and stats["inspections"].issues["no tread or pressure reading"] == 1
    # Unpacking keeps the entry key and caps nothing that is not an object.
    assert expand_rows([{"cond": [{"a": 1}, "x"]}, {"cond": None}], "cond") == [{"cond": {"a": 1, "__key__": "0"}}]


def test_columns_about_people_are_never_mapped() -> None:
    for column in ("inspector", "approver_email", "technician_name", "tyre_conditions.photo_url", "driver_name",
                   "user1_name", "gps_lat", "signature_data", "printed_name"):
        with pytest.raises(ValidationError, match="does not read"):
            EntityMapping(source="inspections", fields={"site": column})
    with pytest.raises(ValidationError, match="go together"):
        EntityMapping(source="work_orders", fields={"id": "work_order_no"}, window_days=30)


# --- the answers ------------------------------------------------------------------------------


def test_operations_summary_vehicle_rows_and_overdue_rule() -> None:
    dataset = fleet_dataset()
    summary = fleet.operations_summary(dataset, AS_OF, 90)
    assert summary.vehicles == 6 and summary.inspections_total == 24
    assert summary.vehicles_inspected_last_30_days == 6 and summary.inspections_last_30_days == 6
    assert summary.tyre_readings == 96 and summary.readings_on_tyre_records == 92
    assert summary.schedules_overdue == 1 and summary.schedules_upcoming_14_days == 1
    assert summary.checklists_last_30_days == 2 and summary.checklists_failed_last_30_days == 1
    # The fixture's work order 200 days old is included here (no window outside a sync).
    assert summary.work_orders_open == 2 and summary.work_orders_synced == 4
    assert summary.work_order_cost_last_30_days == 2250
    # A visit covers a tyre unless every reading matched a tyre: truck 06's fourth tyre was
    # never read, so it stays overdue while the others were inspected 30 days ago.
    overdue = {tyre.serial for tyre, _, _ in overdue_tyres(dataset, Assumptions(), AS_OF)}
    assert overdue == {"fake-sn-0604"}
    rows = fleet.vehicle_rows(dataset, [], AS_OF, 3.0, inspected="no")
    assert rows == []
    assert len(fleet.vehicle_rows(dataset, [], AS_OF, 3.0, site="fake-site-a")) == 6


def test_inspections_answer_how_many_vehicles_and_which() -> None:
    dataset = fleet_dataset()
    found = fleet.inspections_list(dataset, date_from=AS_OF - timedelta(days=31), date_to=AS_OF)
    assert (found.total, found.vehicles) == (6, 6) and found.by_site == {"fake-site-a": 6}
    assert found.vehicles_listed[0] == "fake-truck-01"
    assert (found.items[0].vehicle_id, found.items[0].positions_read) == ("fake-truck-06", 3)
    one = fleet.inspections_list(dataset, vehicle_ids={"fake-truck-01"})
    assert one.total == 4 and one.items[0].positions_read == 5 and one.items[0].lowest_pressure_psi == 104
    assert fleet.inspections_list(dataset, date_from=AS_OF + timedelta(days=1)).total == 0


def test_fleet_tools_answer_from_synced_data_and_read_large_tables_on_demand(fake_source: FakeSupabase) -> None:  # noqa: F811
    not_connected = run("tyre_inspections")
    assert not_connected.status == "failed" and "not connected" in not_connected.summary
    connect_and_sync()
    for name in fleet_tools.FLEET_TOOL_NAMES:
        definition = main.tool_registry.definition(name)
        assert definition.risk_level == RiskLevel.LOW and definition.action_kind == "read"

    seen = run("tyre_inspections", date_from=(AS_OF - timedelta(days=7)).isoformat(), date_to=AS_OF.isoformat())
    assert seen.summary.startswith("0 inspections of 0 vehicles")
    month = run("tyre_inspections", date_from=(AS_OF - timedelta(days=31)).isoformat(), date_to=AS_OF.isoformat())
    assert month.summary.startswith("6 inspections of 6 vehicles between 2026-08-31 and 2026-10-01")
    assert "fake-truck-06" in month.summary and "1 overdue" in month.summary and "data synced" in month.summary

    vehicles = run("tyre_vehicles")
    assert vehicles.summary.startswith("6 vehicles (") and "6 inspected in the last 90 days, 0 not" in vehicles.summary
    detail = run("tyre_vehicle_detail", vehicle="fake-plate-01")
    assert "fake-truck-01 (fake-plate-01)" in detail.summary and "4 tyres fitted" in detail.summary
    assert "R1Lo: 104 psi" in detail.summary and "1 open work orders" in detail.summary
    missing = run("tyre_vehicle_detail", vehicle="nope")
    assert missing.status == "failed" and "No vehicle matches 'nope'" in missing.summary

    checklists = run("tyre_checklists", failed=True)
    assert checklists.summary.startswith("1 failed workshop checklists") and "fake-truck-02" in checklists.summary
    open_orders = run("tyre_work_orders")
    assert open_orders.summary.startswith("2 open work orders") and "last 90 days are synced" in open_orders.summary
    assert "fake-wo-old" not in open_orders.summary

    # One work order: its line items and parts are read now, filtered at the source.
    before = len(fake_source.requests)
    one = run("tyre_work_orders", work_order="fake-wo-1")
    assert one.status == "succeeded" and "2 line items" in one.summary and "2 parts issued" in one.summary
    reads = [str(request.url) for request in fake_source.requests[before:]]
    assert all("work_order_no=eq.fake-wo-1" in url for url in reads) and all(r.method == "GET" for r in fake_source.requests)

    costs = run("tyre_maintenance_costs", date_from=(AS_OF - timedelta(days=30)).isoformat(), date_to=AS_OF.isoformat())
    assert "2,250 SAR over 3 work orders" in costs.summary and "Parts issued in the same period: 3 lines, 600 SAR" in costs.summary
    assert costs.output["groups"][0]["key"] == "fake-truck-01"

    # Viewers read; a role without tyre:read is refused; another workspace has no data.
    viewer = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="viewer_tyre", roles=["viewer"])
    assert run("tyre_vehicles", viewer).status == "succeeded"
    guest = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="x", roles=["guest"])
    assert "cannot read tyre planning" in run("tyre_work_orders", guest).summary
    other = TenantContext(tenant_id="tenant_other", workspace_id="workspace_other", user_id="o", roles=["owner"])
    assert "not connected" in run("tyre_vehicles", other).summary

    # The dashboard carries the operations counts.
    dashboard = client.get(f"{API}/dashboard", headers=VIEWER).json()
    assert dashboard["operations"]["inspections_total"] == 24 and dashboard["operations"]["work_orders_open"] == 2
    assert dashboard["operations"]["work_order_window_days"] == 90


def test_questions_route_to_the_fleet_tools_and_are_audited() -> None:
    today = date(2026, 10, 13)
    for question, name, arguments in (
        ("How many vehicles had inspections last week?", "tyre_inspections",
         {"date_from": "2026-10-06", "date_to": "2026-10-13"}),
        ("Which vehicles were not inspected?", "tyre_vehicles", {"inspected": "no"}),
        ("open work orders for truck T-12", "tyre_work_orders", {"vehicle": "T-12", "open": True}),
        ("show work order WO-1234", "tyre_work_orders", {"work_order": "WO-1234"}),
        ("failed checklists this month", "tyre_checklists", {"date_from": "2026-10-01", "date_to": "2026-10-13", "failed": True}),
        ("maintenance cost by site this month", "tyre_maintenance_costs",
         {"date_from": "2026-10-01", "date_to": "2026-10-13", "group_by": "site"}),
        ("tyres on vehicle ABC123", "tyre_vehicle_detail", {"vehicle": "ABC123"}),
        ("any tyre pressure alerts?", "tyre_alerts", {}),
    ):
        call = tools.choose_call(question, today)
        assert (call.name, call.arguments) == (name, arguments), question
        assert tools.mentions_tyres(question)
    connect_and_sync()
    grounding = asyncio.run(tools.tyre_grounding(OWNER_CONTEXT, "how many vehicles had inspections?"))
    assert grounding is not None and grounding.call.name == "tyre_inspections"
    assert "24 inspections of 6 vehicles" in grounding.result.summary
    audit = memory_repository.list_audit_records(OWNER_CONTEXT)
    assert any(record.action == "tool.call" and record.target == "tyre_inspections" for record in audit)


def test_work_order_questions_in_chat_are_not_sales_orders() -> None:
    from anum_api.agent_skills import default_skill_registry
    from anum_api.chat_tools import chat_intent

    skills, names = default_skill_registry(), main.tool_registry.names
    assert chat_intent("show open work orders", skills, names) == "tyre"
    assert chat_intent("which vehicles had inspections this week?", skills, names) == "tyre"


def test_read_query_refuses_unsafe_columns() -> None:
    with pytest.raises(sources.TyreSourceError):
        sources.ReadQuery(filters=(("asset_no; drop", "eq", "x"),))
    with pytest.raises(sources.TyreSourceError):
        sources.ReadQuery(filters=(("asset_no", "like", "x"),))
    rows = [{"d": "2026-01-02T08:00:00+00:00"}, {"d": "2026-01-05"}, {"d": None}]
    narrowed = sources.apply_query(rows, sources.ReadQuery(filters=(("d", "gte", "2026-01-02"),), order=("d", True)))
    assert [row["d"] for row in narrowed] == ["2026-01-05", "2026-01-02T08:00:00+00:00"]


def test_question_parsing_stays_linear_on_long_whitespace() -> None:
    # CodeQL py/polynomial-redos: "\s*(no|#)?\s*" backtracked on long runs of spaces.
    import time
    from datetime import date

    from anum_api.tyre.fleet_tools import choose_fleet_call

    started = time.perf_counter()
    choose_fleet_call("truck" + " " * 50_000 + "x", date(2026, 10, 13))
    choose_fleet_call("work order" + " " * 50_000 + "x", date(2026, 10, 13))
    assert time.perf_counter() - started < 0.5
    assert choose_fleet_call("work order   :   99A", date(2026, 10, 13)).arguments == {"work_order": "99A"}


# --- per-event times for the HQ campus motion (docs/ANUM_MINE_HQ_CAMPUS_MOTION.md) -----------


def test_parse_instant_keeps_only_real_times_with_a_zone() -> None:
    from datetime import datetime, timezone

    from anum_api.tyre.mapping import parse_instant

    utc = timezone.utc
    assert parse_instant("2026-10-26T14:05:00+04:00") == datetime(2026, 10, 26, 10, 5, tzinfo=utc)
    assert parse_instant("2026-10-26T14:05:00Z") == datetime(2026, 10, 26, 14, 5, tzinfo=utc)
    assert parse_instant(datetime(2026, 10, 26, 14, 5, tzinfo=utc)) == datetime(2026, 10, 26, 14, 5, tzinfo=utc)
    assert parse_instant(1_793_023_500) == datetime.fromtimestamp(1_793_023_500, tz=utc)
    # Dates, texts without a zone, midnight (a date stored as a timestamp) and junk are not instants.
    for value in (None, True, "", "2026-10-26", date(2026, 10, 26), "2026-10-26T14:05:00", datetime(2026, 10, 26, 14, 5),
                  "2026-10-26T00:00:00+00:00", datetime(2026, 10, 26, tzinfo=utc), "26/10/2026", "not a time", 0, -5,
                  float("nan")):
        assert parse_instant(value) is None, value


def test_operations_last_inspection_time_only_when_every_visit_of_that_day_has_one() -> None:
    from datetime import datetime, timezone

    from anum_api.tyre.model import InspectionVisit, TyreDataset

    utc = timezone.utc
    day = date(2026, 10, 26)
    visits = [
        InspectionVisit(id="1", vehicle_id="v1", inspected_at=day, inspected_time=datetime(2026, 10, 26, 6, 0, tzinfo=utc)),
        InspectionVisit(id="2", vehicle_id="v2", inspected_at=day, inspected_time=datetime(2026, 10, 26, 9, 30, tzinfo=utc)),
        InspectionVisit(id="3", vehicle_id="v3", inspected_at=day - timedelta(days=1)),
    ]
    summary = fleet.operations_summary(TyreDataset(inspection_visits=visits), day)
    assert summary.last_inspection == day and summary.last_inspection_at == datetime(2026, 10, 26, 9, 30, tzinfo=utc)
    # One inspection of the last day without a time: the time of the last inspection is unknown, not guessed.
    visits.append(InspectionVisit(id="4", vehicle_id="v4", inspected_at=day))
    summary = fleet.operations_summary(TyreDataset(inspection_visits=visits), day)
    assert summary.last_inspection == day and summary.last_inspection_at is None
    assert fleet.operations_summary(TyreDataset(), day).last_inspection_at is None


def test_dashboard_reports_the_stored_times_of_the_last_inspection_and_newest_alert(fake_source) -> None:  # type: ignore[no-untyped-def]
    connect_and_sync()
    dashboard = client.get(f"{API}/dashboard", headers=VIEWER).json()
    # The fixture's inspections store dates only: the day is known, the time is not.
    assert dashboard["operations"]["last_inspection"] is not None and dashboard["operations"]["last_inspection_at"] is None
    # Alerts are stored with the time ANUM first saw them (the sync ran at NOW).
    assert dashboard["latest_alert_at"] == NOW.isoformat().replace("+00:00", "Z")

    # The source starts storing a time with each inspection: the next sync keeps it.
    from datetime import datetime, timezone

    last_day = max(row["inspection_date"] for row in fake_source.rows["inspections"])
    stamped = []
    for minute, row in enumerate(r for r in fake_source.rows["inspections"] if r["inspection_date"] == last_day):
        row["inspection_date"] = f"{last_day}T09:{10 + minute:02d}:00+04:00"
        stamped.append(datetime.fromisoformat(row["inspection_date"]).astimezone(timezone.utc))
    assert stamped
    assert client.post(f"{API}/sync", headers=MEMBER).status_code == 200
    operations = client.get(f"{API}/dashboard", headers=VIEWER).json()["operations"]
    assert operations["last_inspection"] == last_day
    assert operations["last_inspection_at"] == max(stamped).isoformat().replace("+00:00", "Z")
    assert operations["last_inspection_at"].endswith(f"T05:{9 + len(stamped):02d}:00Z")
