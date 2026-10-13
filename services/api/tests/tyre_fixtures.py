"""A synthetic, clearly fake fleet shaped like TyrePulse's Supabase tables.

Table and column names follow TyrePulse's migrations (vehicle_fleet, tyre_records,
inspections with per-position ``tyre_conditions``, tyre_service_events, tpms_readings,
suppliers, and the operations tables: inspection_schedules, checklist_submissions,
work_orders, work_order_line_items, parts_consumption, odometer_logs, engine_hours_logs,
sites); every value is invented: "fake-" ids,
"brandalpha"/"brandbeta" brands, round numbers. Readings are dated relative to ``AS_OF``
so forecasts can be checked exactly.

Fleet (6 trucks x 4 positions = 24 fitted tyres):

* brandalpha 315/80r22.5 tyres wear 1.0 mm per 30 days; brandbeta 385/65r22.5 wear 0.8.
* ``fake-sn-0101`` (truck 01, position 1) wears 2.4 mm per 30 days: abnormal wear.
* ``fake-sn-0102`` has a mis-typed reading of 25 mm (robust fit ignores it).
* ``fake-sn-0103`` is at 2.8 mm: at or below the 3 mm minimum.
* ``fake-sn-0201`` reads 85 psi against a 110 psi target: under-inflated (TPMS).
* ``fake-sn-0202`` reads 130 psi against 110: over-inflated (TPMS).
* ``fake-sn-0604`` was never inspected and was fitted 200 days ago: overdue.
* Four removed and two scrapped tyres give removal reasons and km; two in stock.
* One repair cost of 4000 against ~300 typical: a cost anomaly.
* Tread and pressure readings are TyrePulse inspections: one per truck and date, with an
  entry per position carrying the serial (as strings, as TyrePulse stores them). Truck 01's
  inspections also read a rear position ("R1Lo") that no tyre record names: kept as a
  reading of that vehicle position.
* Operations: 3 inspection schedules (upcoming, overdue, completed), 3 checklists (one
  failed), 4 work orders (2 open, one 200 days old: outside the 90-day sync window), line
  items and parts for work order fake-wo-1, odometer and engine-hour logs, 1 site.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

AS_OF = date(2026, 10, 1)
SIZE_A, SIZE_B = "315/80r22.5", "385/65r22.5"
TARGET_PSI = 110.0

TABLES: dict[str, list[str]] = {
    "vehicle_fleet": ["id", "asset_no", "fleet_number", "make", "model", "vehicle_type", "site", "country",
                      "current_km", "registration_no", "status", "is_active", "organisation_id"],
    "tyre_records": ["id", "serial_no", "brand", "size", "description", "asset_no", "tyre_position", "status",
                     "issue_date", "fitment_date", "km_at_fitment", "km_at_removal", "hrs_at_fitment",
                     "hrs_at_removal", "removal_date", "removal_reason", "total_km", "total_hrs",
                     "cost_per_tyre", "supplier", "tread_depth", "pressure_reading", "country", "organisation_id"],
    "tyre_service_events": ["id", "organisation_id", "country", "tyre_serial", "asset_no", "position",
                            "event_type", "event_date", "tread_depth", "pressure", "cost", "technician", "site",
                            "notes"],
    "tpms_readings": ["id", "organisation_id", "country", "asset_no", "tyre_position", "tyre_serial", "pressure",
                      "temperature", "target_pressure", "status", "recorded_at"],
    "suppliers": ["id", "supplier_name", "supplier_code", "supplier_type", "status"],
    "profiles": ["id", "username", "full_name"],
    "inspections": ["id", "asset_no", "site", "inspection_date", "inspection_type", "status", "severity",
                    "approval_status", "odometer_km", "hour_meter", "tyre_conditions", "inspector", "country",
                    "organisation_id"],
    "inspection_schedules": ["id", "asset_no", "site", "scheduled_date", "inspection_type", "priority", "status",
                             "completed_inspection_id", "inspector_name", "country", "organisation_id"],
    "checklist_submissions": ["id", "template_name", "asset_no", "site", "title", "status", "submitted_at",
                              "score_pct", "score_passed", "approval_status", "printed_name", "country",
                              "organisation_id"],
    "work_orders": ["id", "work_order_no", "asset_no", "site", "status", "priority", "work_type", "opened_at",
                    "completed_at", "tyre_serial", "labour_hours", "labour_cost", "parts_cost", "tyre_cost",
                    "total_cost", "technician_name", "country", "organisation_id"],
    "work_order_line_items": ["id", "work_order_no", "asset_no", "site", "opened_date", "work_type", "task", "action",
                              "detail", "qty", "country", "organisation_id"],
    "parts_consumption": ["id", "work_order_no", "asset_code", "site", "event_date", "item_code", "item_description",
                          "qty_n", "cost_category", "line_cost", "tyre_cost", "spare_cost", "oil_cost", "currency",
                          "country", "organisation_id"],
    "odometer_logs": ["id", "asset_no", "reading_date", "odometer_km", "site", "country", "organisation_id"],
    "engine_hours_logs": ["id", "asset_no", "reading_date", "engine_hours", "site", "country", "organisation_id"],
    "sites": ["id", "name", "region", "city", "active", "country", "organisation_id"],
}

ORG = "00000000-0000-0000-0000-000000000000"


def _day(days_ago: int) -> str:
    return (AS_OF - timedelta(days=days_ago)).isoformat()


def build_rows() -> dict[str, list[dict[str, Any]]]:
    vehicles, tyres, events, tpms = [], [], [], []
    for v in range(1, 7):
        asset = f"fake-truck-{v:02d}"
        km_now = 100_000 + v * 10_000
        vehicles.append({
            "id": f"00000000-0000-0000-0001-{v:012d}", "asset_no": asset, "fleet_number": f"fake-fleet-{v:02d}",
            "make": "fakemake", "model": "fakemodel", "vehicle_type": "truck", "site": "fake-site-a",
            "country": "KSA", "current_km": km_now, "registration_no": f"fake-plate-{v:02d}",
            "status": "active", "is_active": True, "organisation_id": ORG,
        })
        for p in range(1, 5):
            serial = f"fake-sn-{v:02d}{p:02d}"
            alpha = p <= 2
            brand, size = ("brandalpha", SIZE_A) if alpha else ("brandbeta", SIZE_B)
            rate = 1.0 if alpha else 0.8
            if serial == "fake-sn-0101":
                rate = 2.4
            tyres.append({
                "id": f"00000000-0000-0000-0002-{v:06d}{p:06d}", "serial_no": serial, "brand": brand, "size": size,
                "description": f"{size} {brand}", "asset_no": asset, "tyre_position": f"P{p}", "status": "Active",
                "issue_date": _day(200), "fitment_date": _day(200), "km_at_fitment": km_now - 40_000,
                "km_at_removal": None, "hrs_at_fitment": None, "hrs_at_removal": None, "removal_date": None,
                "removal_reason": None, "total_km": None, "total_hrs": None,
                "cost_per_tyre": 1200.0 if alpha else 1500.0, "supplier": "fake-supplier-a",
                "tread_depth": None, "pressure_reading": None, "country": "KSA", "organisation_id": ORG,
            })
            if serial == "fake-sn-0604":
                continue  # never inspected
            start = 14.0 if serial != "fake-sn-0103" else 4.6
            if serial == "fake-sn-0103":
                rate = 0.6
            for index, days_ago in enumerate((120, 90, 60, 30)):
                tread = round(start - rate * index, 2)
                if serial == "fake-sn-0102" and index == 2:
                    tread = 25.0  # mis-typed reading
                events.append({
                    "id": f"ev-{serial}-{index}", "organisation_id": ORG, "country": "KSA", "tyre_serial": serial,
                    "asset_no": asset, "position": f"P{p}", "event_type": "inspection", "event_date": _day(days_ago),
                    "tread_depth": tread, "pressure": TARGET_PSI, "cost": None, "technician": "fake-tech",
                    "site": "fake-site-a", "notes": None,
                })
            psi = 85.0 if serial == "fake-sn-0201" else 130.0 if serial == "fake-sn-0202" else 108.0
            tpms.append({
                "id": f"tp-{serial}", "organisation_id": ORG, "country": "KSA", "asset_no": asset,
                "tyre_position": f"P{p}", "tyre_serial": serial, "pressure": psi, "temperature": 40,
                "target_pressure": TARGET_PSI, "status": "optimal", "recorded_at": f"{_day(5)}T08:00:00+00:00",
            })
    removed = [
        ("fake-sn-9001", "worn out", 95_000, "Removed", SIZE_A, "brandalpha"),
        ("fake-sn-9002", "worn out", 105_000, "Removed", SIZE_A, "brandalpha"),
        ("fake-sn-9003", "cut / sidewall damage", 40_000, "Scrapped", SIZE_B, "brandbeta"),
        ("fake-sn-9004", "puncture", 20_000, "Removed", SIZE_B, "brandbeta"),
        ("fake-sn-9005", "worn out", 90_000, "Scrapped", SIZE_B, "brandbeta"),
        ("fake-sn-9006", "irregular wear", 60_000, "Removed", SIZE_A, "brandalpha"),
    ]
    for index, (serial, reason, km, status, size, brand) in enumerate(removed):
        tyres.append({
            "id": f"00000000-0000-0000-0003-{index:012d}", "serial_no": serial, "brand": brand, "size": size,
            "description": None, "asset_no": "fake-truck-01", "tyre_position": "P1", "status": status,
            "issue_date": _day(700), "fitment_date": _day(700), "km_at_fitment": 10_000,
            "km_at_removal": 10_000 + km, "hrs_at_fitment": None, "hrs_at_removal": None,
            "removal_date": _day(10 + index * 20), "removal_reason": reason, "total_km": km, "total_hrs": None,
            "cost_per_tyre": 1200.0 if brand == "brandalpha" else 1500.0, "supplier": "fake-supplier-b",
            "tread_depth": None, "pressure_reading": None, "country": "KSA", "organisation_id": ORG,
        })
    for index in range(2):
        tyres.append({
            "id": f"00000000-0000-0000-0004-{index:012d}", "serial_no": f"fake-sn-80{index:02d}", "brand": "brandalpha",
            "size": SIZE_A, "description": None, "asset_no": None, "tyre_position": None, "status": "Store",
            "issue_date": None, "fitment_date": None, "km_at_fitment": None, "km_at_removal": None,
            "hrs_at_fitment": None, "hrs_at_removal": None, "removal_date": None, "removal_reason": None,
            "total_km": None, "total_hrs": None, "cost_per_tyre": 1200.0, "supplier": "fake-supplier-a",
            "tread_depth": None, "pressure_reading": None, "country": "KSA", "organisation_id": ORG,
        })
    for index in range(6):
        events.append({
            "id": f"cost-{index}", "organisation_id": ORG, "country": "KSA", "tyre_serial": f"fake-sn-0{index + 1}03",
            "asset_no": f"fake-truck-0{index + 1}", "position": "P3", "event_type": "repair",
            "event_date": _day(20 + index), "tread_depth": None, "pressure": None,
            "cost": 4000.0 if index == 0 else 300.0, "technician": "fake-tech", "site": "fake-site-a", "notes": None,
        })
    suppliers = [
        {"id": "sup-1", "supplier_name": "fake-supplier-a", "supplier_code": "fa", "supplier_type": "tyres", "status": "active"},
        {"id": "sup-2", "supplier_name": "fake-supplier-b", "supplier_code": "fb", "supplier_type": "tyres", "status": "active"},
    ]
    return {
        "vehicle_fleet": vehicles, "tyre_records": tyres, "tyre_service_events": events,
        "tpms_readings": tpms, "suppliers": suppliers,
        "profiles": [{"id": "p1", "username": "example", "full_name": "example user"}],
        "inspections": inspections_from(events),
        **operations_rows(),
    }


def inspections_from(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """TyrePulse's shape: one inspection per truck and date, an entry per wheel position."""
    by_visit: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        if event["event_type"] != "inspection":
            continue
        key = (event["asset_no"], event["event_date"])
        visit = by_visit.setdefault(key, {
            "id": f"00000000-0000-0000-0005-{len(by_visit):012d}", "asset_no": event["asset_no"], "site": "fake-site-a",
            "inspection_date": event["event_date"], "inspection_type": "Routine", "status": "Done", "severity": None,
            "approval_status": "approved", "odometer_km": None, "hour_meter": None, "tyre_conditions": {},
            "inspector": "example", "country": "KSA", "organisation_id": ORG,
        })
        visit["tyre_conditions"][event["position"]] = {
            "position": event["position"], "serial_number": event["tyre_serial"],
            "tread_depth_mm": f"{event['tread_depth']}", "pressure_psi": f"{event['pressure']:g}",
            "condition": "Good", "checked": True, "photo_url": None, "notes": "",
        }
        if event["asset_no"] == "fake-truck-01":
            visit["tyre_conditions"]["R1Lo"] = {"position": "R1Lo", "serial_number": "", "tread_depth_mm": "",
                                                "pressure_psi": "104", "condition": "Good", "photo_url": None}
    return list(by_visit.values())


def operations_rows() -> dict[str, list[dict[str, Any]]]:
    common = {"country": "KSA", "organisation_id": ORG}
    return {
        "inspection_schedules": [
            {"id": "00000000-0000-0000-0006-000000000001", "asset_no": "fake-truck-02", "site": "fake-site-a",
             "scheduled_date": _day(-5), "inspection_type": "Routine", "priority": "normal", "status": "Scheduled",
             "completed_inspection_id": None, "inspector_name": "example", **common},
            {"id": "00000000-0000-0000-0006-000000000002", "asset_no": "fake-truck-03", "site": "fake-site-a",
             "scheduled_date": _day(3), "inspection_type": "Routine", "priority": "high", "status": "Scheduled",
             "completed_inspection_id": None, "inspector_name": "example", **common},
            {"id": "00000000-0000-0000-0006-000000000003", "asset_no": "fake-truck-01", "site": "fake-site-a",
             "scheduled_date": _day(30), "inspection_type": "Routine", "priority": "normal", "status": "Completed",
             "completed_inspection_id": None, "inspector_name": "example", **common},
        ],
        "checklist_submissions": [
            {"id": "00000000-0000-0000-0007-000000000001", "template_name": "fake daily check", "asset_no": "fake-truck-01",
             "site": "fake-site-a", "title": "Daily", "status": "submitted", "submitted_at": f"{_day(2)}T07:00:00+00:00",
             "score_pct": 95, "score_passed": True, "approval_status": "approved", "printed_name": "example", **common},
            {"id": "00000000-0000-0000-0007-000000000002", "template_name": "fake daily check", "asset_no": "fake-truck-02",
             "site": "fake-site-a", "title": "Daily", "status": "submitted", "submitted_at": f"{_day(4)}T07:00:00+00:00",
             "score_pct": 60, "score_passed": False, "approval_status": "pending", "printed_name": "example", **common},
            {"id": "00000000-0000-0000-0007-000000000003", "template_name": "fake workshop exit", "asset_no": "fake-truck-03",
             "site": "fake-site-a", "title": "Exit", "status": "submitted", "submitted_at": f"{_day(40)}T07:00:00+00:00",
             "score_pct": None, "score_passed": None, "approval_status": None, "printed_name": "example", **common},
        ],
        "work_orders": [
            {"id": "00000000-0000-0000-0008-000000000001", "work_order_no": "fake-wo-1", "asset_no": "fake-truck-01",
             "site": "fake-site-a", "status": "Open", "priority": "high", "work_type": "Tyre", "opened_at": f"{_day(3)}T08:00:00+00:00",
             "completed_at": None, "tyre_serial": "fake-sn-0103", "labour_hours": 2, "labour_cost": 100, "parts_cost": 50,
             "tyre_cost": 1200, "total_cost": 1350, "technician_name": "example", **common},
            {"id": "00000000-0000-0000-0008-000000000002", "work_order_no": "fake-wo-2", "asset_no": "fake-truck-02",
             "site": "fake-site-a", "status": "In Progress", "priority": "normal", "work_type": "Brakes",
             "opened_at": f"{_day(10)}T08:00:00+00:00", "completed_at": None, "tyre_serial": None, "labour_hours": 4,
             "labour_cost": 200, "parts_cost": 300, "tyre_cost": 0, "total_cost": 500, "technician_name": "example", **common},
            {"id": "00000000-0000-0000-0008-000000000003", "work_order_no": "fake-wo-3", "asset_no": "fake-truck-01",
             "site": "fake-site-a", "status": "Closed", "priority": "normal", "work_type": "Service",
             "opened_at": f"{_day(20)}T08:00:00+00:00", "completed_at": f"{_day(19)}T08:00:00+00:00", "tyre_serial": None,
             "labour_hours": 3, "labour_cost": 150, "parts_cost": 250, "tyre_cost": 0, "total_cost": 400,
             "technician_name": "example", **common},
            {"id": "00000000-0000-0000-0008-000000000004", "work_order_no": "fake-wo-old", "asset_no": "fake-truck-04",
             "site": "fake-site-a", "status": "Closed", "priority": "normal", "work_type": "Service",
             "opened_at": f"{_day(200)}T08:00:00+00:00", "completed_at": f"{_day(199)}T08:00:00+00:00", "tyre_serial": None,
             "labour_hours": 3, "labour_cost": 150, "parts_cost": 250, "tyre_cost": 0, "total_cost": 400,
             "technician_name": "example", **common},
        ],
        "work_order_line_items": [
            {"id": f"00000000-0000-0000-0009-00000000000{i}", "work_order_no": "fake-wo-1", "asset_no": "fake-truck-01",
             "site": "fake-site-a", "opened_date": _day(3), "work_type": "Tyre", "task": f"fake task {i}",
             "action": "replace", "detail": "example", "qty": 1, **common}
            for i in range(1, 3)
        ],
        "parts_consumption": [
            {"id": f"00000000-0000-0000-0010-00000000000{i}", "work_order_no": "fake-wo-1" if i < 3 else "fake-wo-3",
             "asset_code": "fake-truck-01", "site": "fake-site-a", "event_date": _day(3 if i < 3 else 20),
             "item_code": f"fake-item-{i}", "item_description": "fake part", "qty_n": 1,
             "cost_category": "tyre" if i == 1 else "spare", "line_cost": 100.0 * i, "tyre_cost": 100.0 if i == 1 else 0,
             "spare_cost": 0 if i == 1 else 100.0 * i, "oil_cost": 0, "currency": "SAR", **common}
            for i in range(1, 4)
        ],
        "odometer_logs": [
            {"id": f"00000000-0000-0000-0011-00000000000{i}", "asset_no": "fake-truck-01", "reading_date": _day(60 - 30 * i),
             "odometer_km": 105_000 + 6_000 * i, "site": "fake-site-a", **common}
            for i in range(0, 3)
        ],
        "engine_hours_logs": [
            {"id": "00000000-0000-0000-0012-000000000001", "asset_no": "fake-truck-01", "reading_date": _day(1),
             "engine_hours": 5_000, "site": "fake-site-a", **common},
        ],
        "sites": [
            {"id": "00000000-0000-0000-0013-000000000001", "name": "fake-site-a", "region": "fake-region",
             "city": "fake-city", "active": True, **common},
        ],
    }


def postgrest_filter(rows: list[dict[str, Any]], query: dict[str, list[str]]) -> list[dict[str, Any]]:
    """PostgREST's ``column=eq|gte|lte.value`` filters and ``order=column.desc``, enough for ANUM's reads."""
    kept = rows
    for column, values in query.items():
        if column in {"select", "limit", "offset", "order"}:
            continue
        for value in values:
            op, _, wanted = value.partition(".")
            if op == "eq":
                kept = [row for row in kept if str(row.get(column)) == wanted]
            elif op == "gte":
                kept = [row for row in kept if row.get(column) is not None and str(row.get(column))[: len(wanted)] >= wanted]
            elif op == "lte":
                kept = [row for row in kept if row.get(column) is not None and str(row.get(column))[: len(wanted)] <= wanted]
    if "order" in query:
        column, _, rest = query["order"][0].partition(".")
        present = [row for row in kept if row.get(column) is not None]
        present.sort(key=lambda row: str(row.get(column)), reverse=rest.startswith("desc"))
        kept = present + [row for row in kept if row.get(column) is None]
    return kept


class FakeSupabase:
    """Just enough PostgREST: the OpenAPI table list and paged table reads."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]] | None = None, key: str = "example-key") -> None:
        self.rows = rows if rows is not None else build_rows()
        self.key = key
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("apikey") != self.key or request.headers.get("authorization") != f"Bearer {self.key}":
            return httpx.Response(401, json={"message": "invalid key"})
        path = urlsplit(str(request.url)).path
        if path == "/rest/v1/":
            definitions = {
                table: {"properties": {column: {"type": "string"} for column in columns}}
                for table, columns in TABLES.items()
            }
            return httpx.Response(200, json={"swagger": "2.0", "definitions": definitions})
        table = path.removeprefix("/rest/v1/")
        if table not in self.rows:
            return httpx.Response(404, json={"message": "relation does not exist"})
        query = parse_qs(urlsplit(str(request.url)).query)
        columns = query["select"][0].split(",")
        limit, offset = int(query["limit"][0]), int(query["offset"][0])
        rows = postgrest_filter(self.rows[table], query)
        page = rows[offset: offset + limit]
        return httpx.Response(200, json=[{column: row.get(column) for column in columns} for row in page])

    def client_factory(self) -> Callable[[], httpx.AsyncClient]:
        return lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.handler), timeout=5)
