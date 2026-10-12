"""A synthetic, clearly fake fleet shaped like TyrePulse's Supabase tables.

Table and column names follow TyrePulse's migrations (vehicle_fleet, tyre_records,
tyre_service_events, tpms_readings, suppliers); every value is invented: "fake-" ids,
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
    }


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
        page = self.rows[table][offset: offset + limit]
        return httpx.Response(200, json=[{column: row.get(column) for column in columns} for row in page])

    def client_factory(self) -> Callable[[], httpx.AsyncClient]:
        return lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.handler), timeout=5)
