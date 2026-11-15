"""Running a client fleet (docs/vehicles.md#fleet): the import, fleet cost analytics and the monthly report, in-memory stores.

``test_postgres_vehicles_fleet.py`` repeats the import's all-or-nothing write and the costs under RLS.
"""

from __future__ import annotations

import base64
import io
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main, xlsx_writer
from anum_api.dependencies import memory_repository
from anum_api.schemas import WorkspaceMembership, utc_now
from anum_api.vehicles import fleet, fleet_import
from anum_api.vehicles.models import PaymentRef
from anum_api.vehicles.store import vehicle_memory_store

TENANT, WORKSPACE = "tenant_fleet", "ws_fleet"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_fleet", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_fleet", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_fleet", "x-user-roles": "viewer"}
API = "/api/v1/vehicles"
FLEET = "/api/v1/vehicles/fleet"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    vehicle_memory_store.clear()
    memberships = dict(memory_repository.store.memberships)
    now = utc_now()
    for user, role in (("owner_fleet", "owner"), ("member_fleet", "member"), ("viewer_fleet", "viewer")):
        memory_repository.store.memberships[(TENANT, WORKSPACE, user)] = WorkspaceMembership(
            tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, role=role, active=True, created_at=now, updated_at=now)
    yield
    memory_repository.store.memberships.clear()
    memory_repository.store.memberships.update(memberships)
    vehicle_memory_store.clear()


def month_ago(months: int, day: int = 10) -> date:
    first = utc_now().date().replace(day=1)
    for _ in range(months):
        first = (first - timedelta(days=1)).replace(day=1)
    return first.replace(day=day)


def preview(kind: str, text: str, headers: dict = OWNER, **extra: Any):  # type: ignore[no-untyped-def]
    return client.post(f"{FLEET}/import/preview", headers=headers, json={"kind": kind, "csv": text, **extra})


def apply(kind: str, text: str, shown: dict, decisions: list[dict] | None = None, headers: dict = OWNER, **extra: Any):  # type: ignore[no-untyped-def]
    return client.post(f"{FLEET}/import/apply", headers=headers, json={"kind": kind, "csv": text, "mapping": shown["mapping"],
                                                                        "fingerprint": shown["fingerprint"], "decisions": decisions or [], **extra})


def imported(kind: str, text: str, **extra: Any) -> dict:
    shown = preview(kind, text, **extra)
    assert shown.status_code == 200, shown.text
    done = apply(kind, text, shown.json(), **extra)
    assert done.status_code == 200, done.text
    return done.json()


VEHICLES = ("Plate No,Fleet No,Make,Model,Year,Type,Fuel,Insurance expiry,Registration expiry,Odometer,Odometer date\n"
            f"A 1001,T-01,Volvo,FH,2019,Truck,Diesel,{(utc_now().date() + timedelta(days=20)).strftime('%d/%m/%Y')},2027-01-31,120500,{month_ago(13, 1).isoformat()}\n"
            "B 2002,T-02,Isuzu,NPR,2021,Pickup,Diesel,,,\n")


def fleet_vehicles() -> dict[str, dict]:
    imported("vehicles", VEHICLES)
    return {v["registration"]: v for v in client.get(API, headers=OWNER).json()["items"]}


# -- reading values --------------------------------------------------------------------------------


def test_numbers_and_dates_in_the_ways_a_gulf_sheet_writes_them() -> None:
    assert fleet_import.parse_number("1,234.50") == Decimal("1234.50")
    assert fleet_import.parse_number("AED 1,234") == Decimal("1234")
    assert fleet_import.parse_number("12,5") == Decimal("12.5")
    assert fleet_import.parse_number("١٬٢٣٤٫٥") == Decimal("1234.5")
    assert fleet_import.parse_number("n/a") is None
    assert fleet_import.parse_moment("31/03/2026", "dmy").date() == date(2026, 3, 31)
    assert fleet_import.parse_moment("03/04/2026", "mdy").date() == date(2026, 3, 4)
    assert fleet_import.parse_moment("2026-03-31 14:20", "dmy") == datetime(2026, 3, 31, 14, 20, tzinfo=UTC)
    assert fleet_import.parse_moment("46112", "dmy").date() == date(2026, 3, 31)  # an Excel day number
    assert fleet_import.parse_moment("٣١/٠٣/٢٠٢٦", "dmy").date() == date(2026, 3, 31)
    assert fleet_import.parse_moment("31/02/2026", "dmy") is None
    assert fleet_import.parse_moment("next week", "dmy") is None


def test_columns_are_suggested_from_english_and_arabic_headers() -> None:
    english = fleet_import.suggest_mapping("fuel", ["Plate No", "Date", "Liters", "Amount", "Odometer", "Station", "Vehicle type"])
    assert english == {"registration": "Plate No", "date": "Date", "litres": "Liters", "total": "Amount", "odometer_km": "Odometer", "station": "Station"}
    arabic = fleet_import.suggest_mapping("service", ["رقم اللوحة", "التاريخ", "نوع الخدمة", "المبلغ", "قراءة العداد", "الورشة"])
    assert arabic == {"registration": "رقم اللوحة", "date": "التاريخ", "kind": "نوع الخدمة", "total": "المبلغ", "odometer_km": "قراءة العداد",
                      "workshop": "الورشة"}
    vehicles = fleet_import.suggest_mapping("vehicles", ["Registration", "Registration expiry", "Insurance expiry", "Chassis No"])
    assert vehicles == {"registration": "Registration", "vin": "Chassis No", "insurance_expires_on": "Insurance expiry",
                        "registration_expires_on": "Registration expiry"}


# -- import --------------------------------------------------------------------------------------------


def test_vehicles_import_previews_then_adds_business_vehicles_and_finds_them_again() -> None:
    shown = preview("vehicles", VEHICLES).json()
    assert shown["suggested"] is True and shown["counts"] == {"rows": 2, "new": 2, "exists": 0, "duplicate_in_file": 0, "imported": 0, "invalid": 0}
    assert [r["action"] for r in shown["rows"]] == ["create", "create"]
    assert shown["rows"][0]["summary"]["insurance_expires_on"] == (utc_now().date() + timedelta(days=20)).isoformat()
    result = apply("vehicles", VEHICLES, shown).json()
    assert result == {"kind": "vehicles", "rows": 2, "created": 2, "updated": 0, "skipped": 0, "readings": 1}
    listed = {v["registration"]: v for v in client.get(API, headers=OWNER).json()["items"]}
    truck = listed["A 1001"]
    assert truck["ownership"] == "business" and truck["vehicle_type"] == "truck" and truck["fuel_type"] == "diesel" and truck["country"] == "AE"
    assert truck["currency"] == "AED" and truck["nickname"] == "T-01" and truck["odometer_km"] == "120500.0" and truck["registration_expires_on"] == "2027-01-31"
    assert listed["B 2002"]["vehicle_type"] == "pickup"
    # The same file again: both exist; updating one fills what the file gives.
    again = preview("vehicles", VEHICLES.replace("2027-01-31", "2027-02-28")).json()
    assert again["counts"]["exists"] == 2 and again["rows"][0]["actions"] == ["skip", "update"]
    text = VEHICLES.replace("2027-01-31", "2027-02-28")
    updated = apply("vehicles", text, again, [{"row": 2, "action": "update"}]).json()
    assert updated["updated"] == 1 and updated["skipped"] == 1
    assert client.get(f"{API}/{truck['id']}", headers=OWNER).json()["registration_expires_on"] == "2027-02-28"
    audit = [r for r in memory_repository.store.audit_records if r.action == "vehicle.fleet_import"]
    assert audit and audit[-1].metadata["created"] == 0 and "A 1001" not in str(audit[-1].metadata)


def test_a_vehicles_file_with_problems_says_which_row_and_why() -> None:
    text = "Registration,Country,Year,VIN\nA 1001,UAE,2019,\nA 1001,AE,2020,\n,AE,2020,\nC 3,Oman,2020,\nD 4,SA,twenty,\nE 5,KSA,2020,IOQ1234567\n"
    shown = preview("vehicles", text).json()
    rows = {r["row"]: r for r in shown["rows"]}
    assert rows[2]["status"] == "new" and rows[3]["status"] == "duplicate_in_file" and rows[3]["duplicate_of"] == 2
    assert rows[4]["status"] == "invalid" and rows[4]["issues"][0]["code"] == "required"
    assert rows[5]["issues"][0]["code"] == "choice" and rows[6]["issues"][0]["code"] == "number"
    assert rows[7]["issues"][0]["code"] == "vin"
    # Asking to add an invalid row refuses the whole file: nothing is written.
    refused = apply("vehicles", text, shown, [{"row": 5, "action": "create"}])
    assert refused.status_code == 409 and refused.json()["code"] == "rows_refused" and "Row 5" in refused.json()["detail"]
    assert client.get(API, headers=OWNER).json()["total"] == 0
    # A changed file or mapping is refused too.
    stale = apply("vehicles", text + "F 6,AE,2020,\n", shown)
    assert stale.status_code == 409 and stale.json()["code"] == "stale_preview"


def test_fuel_import_checks_vehicles_duplicates_and_the_odometer_then_writes_everything() -> None:
    cars = fleet_vehicles()
    d1, d2, d3 = month_ago(2, 5), month_ago(1, 5), month_ago(1, 20)
    text = ("Plate,Date,Litres,Amount,Odometer,Station\n"
            f"A 1001,{d1.isoformat()},300,900.50,121000,ADNOC\n"
            f"A 1001,{d2.isoformat()},280,840,122000,ADNOC\n"
            f"A 1001,{d2.isoformat()},280,840,122000,ADNOC\n"
            f"A 1001,{d3.isoformat()},250,750,121500,ENOC\n"
            f"Z 9,{d3.isoformat()},10,30,,\n"
            f"B 2002,{d3.isoformat()},-5,30,,\n")
    shown = preview("fuel", text).json()
    rows = {r["row"]: r for r in shown["rows"]}
    assert rows[2]["status"] == "new" and rows[3]["status"] == "new"
    assert rows[4]["status"] == "duplicate_in_file" and rows[4]["actions"] == ["skip", "create"]
    assert rows[5]["status"] == "invalid" and rows[5]["issues"][0]["code"] == "odometer" and "122000" in rows[5]["issues"][0]["text"]
    assert rows[6]["issues"][0]["code"] == "unknown_vehicle"
    assert rows[7]["issues"][0]["code"] == "range"
    member = apply("fuel", text, shown, headers=MEMBER)
    assert member.status_code == 403 and member.json()["code"] == "forbidden" and "owners" in member.json()["detail"]
    result = apply("fuel", text, shown).json()
    assert result["created"] == 2 and result["skipped"] == 4 and result["readings"] == 2
    entries = client.get(f"{API}/{cars['A 1001']['id']}/fuel", headers=OWNER).json()
    assert entries["total"] == 2 and {e["currency"] for e in entries["items"]} == {"AED"} and entries["items"][0]["payment"] is None
    assert {e["price_per_litre"] for e in entries["items"]} == {"3.002", "3.000"}
    # Imported once: the same rows from another file are recognised, never added twice.
    again = preview("fuel", text.replace("Station", "Site")).json()
    assert [r["status"] for r in again["rows"]][:3] == ["imported", "imported", "duplicate_in_file"]


def test_service_and_odometer_imports_read_kinds_and_readings_in_arabic() -> None:
    cars = fleet_vehicles()
    day = month_ago(1, 8)
    text = ("رقم اللوحة,التاريخ,نوع الخدمة,المبلغ,قراءة العداد,الورشة\n"
            f"A 1001,{day.strftime('%d/%m/%Y')},تغيير زيت,450,121800,ورشة النور\n"
            f"A 1001,{day.strftime('%d/%m/%Y')},تبديل إطارات,2400,,\n"
            f"A 1001,{day.strftime('%d/%m/%Y')},مخالفة مرورية,500,,\n")
    done = imported("service", text)
    assert done["created"] == 3 and done["readings"] == 1
    kinds = sorted(r["kind"] for r in client.get(f"{API}/{cars['A 1001']['id']}/service", headers=OWNER).json()["items"])
    assert kinds == ["fine", "oil", "tyres"]
    readings = f"Plate,Date,KM\nB 2002,{month_ago(1, 1).isoformat()},5000\nB 2002,{month_ago(1, 28).isoformat()},7400\nB 2002,{month_ago(1, 28).isoformat()},7400\n"
    shown = preview("odometer", readings).json()
    assert [r["status"] for r in shown["rows"]] == ["new", "new", "duplicate_in_file"]
    assert apply("odometer", readings, shown).json()["readings"] == 2
    assert [r["status"] for r in preview("odometer", readings).json()["rows"]] == ["exists", "exists", "exists"]


def test_an_excel_workbook_is_read_through_the_safe_reader() -> None:
    fleet_vehicles()
    sheet = xlsx_writer.Sheet("Fuel")
    sheet.header(["Registration", "Date", "Litres", "Total"])
    sheet.row(["A 1001", month_ago(1, 3).isoformat(), Decimal("120.5"), Decimal("361.50")])
    sheet.row(["=HYPERLINK(\"x\")", month_ago(1, 3).isoformat(), Decimal("10"), Decimal("30")])
    data = base64.b64encode(xlsx_writer.workbook([sheet])).decode()
    shown = client.post(f"{FLEET}/import/preview", headers=OWNER, json={"kind": "fuel", "format": "xlsx", "file_base64": data})
    assert shown.status_code == 200, shown.text
    rows = shown.json()["rows"]
    assert rows[0]["status"] == "new" and rows[0]["summary"]["litres"] == "120.5"
    assert rows[1]["status"] == "invalid"  # a formula-looking cell is text, never run: no such registration
    broken = client.post(f"{FLEET}/import/preview", headers=OWNER, json={"kind": "fuel", "format": "xlsx", "file_base64": base64.b64encode(b"not a zip").decode()})
    assert broken.status_code == 422 and broken.json()["code"] == "invalid_file"


def test_a_mapping_the_file_cannot_satisfy_is_refused_and_an_unknown_header_waits_for_the_caller() -> None:
    unknown = preview("fuel", "Col A,Col B\nx,y\n").json()
    assert unknown["suggested"] is True and unknown["rows"] == [] and unknown["counts"]["rows"] == 1
    missing = preview("fuel", "Col A,Col B\nx,y\n", mapping={"registration": "Col A"})
    assert missing.status_code == 422 and missing.json()["code"] == "invalid_mapping" and missing.json()["fields"] == ["date", "litres"]
    wrong = preview("fuel", "Col A,Col B\nx,y\n", mapping={"registration": "Col C", "date": "Col A", "litres": "Col B"})
    assert wrong.status_code == 422 and "Col C" in wrong.json()["detail"]
    assert preview("fuel", "Col A\nx\n", headers=VIEWER).status_code == 403


# -- fleet costs ---------------------------------------------------------------------------------------


def add_costs(cars: dict[str, dict]) -> None:
    truck, pickup = cars["A 1001"]["id"], cars["B 2002"]["id"]
    month = month_ago(1, 1)
    fuel = (f"Plate,Date,Litres,Amount,Odometer\nA 1001,{month_ago(2, 25).isoformat()},200,600,121000\n"
            f"A 1001,{month.replace(day=10).isoformat()},300,900,122000\nB 2002,{month.replace(day=12).isoformat()},50,150,\n")
    imported("fuel", fuel)
    service = (f"Plate,Date,Type,Amount\nA 1001,{month.replace(day=11).isoformat()},Tyres,2400\nA 1001,{month.replace(day=11).isoformat()},Oil change,450\n"
               f"A 1001,{month.replace(day=11).isoformat()},Salik,50\n")
    imported("service", service)
    imported("odometer", f"Plate,Date,KM\nB 2002,{month.replace(day=2).isoformat()},5000\nB 2002,{month.replace(day=20).isoformat()},5050\n")
    assert truck and pickup


def test_fleet_costs_per_vehicle_and_km_by_category_with_a_twelve_month_trend() -> None:
    cars = fleet_vehicles()
    add_costs(cars)
    personal = client.post(API, headers=OWNER, json={"registration": "MY 1", "country": "AE", "fuel_type": "petrol_95"}).json()
    client.post(f"{API}/{personal['id']}/fuel", headers=OWNER, json={"filled_at": month_ago(1, 10).isoformat() + "T10:00:00Z", "litres": "40", "total": "120"})
    month = month_ago(1, 1).strftime("%Y-%m")
    summary = client.get(f"{FLEET}/costs", headers=OWNER, params={"month": month}).json()
    assert summary["month"] == month and len(summary["months"]) == 12 and summary["vehicles"] == 2
    assert {r["registration"] for r in summary["rows"]} == {"A 1001", "B 2002"}  # never the owner's own car
    truck = next(r for r in summary["rows"] if r["registration"] == "A 1001")
    cost = truck["costs"][0]
    assert cost == {"currency": "AED", "fuel": "900.00", "service": "450.00", "tyres": "2400.00", "other": "50.00", "total": "3800.00",
                    "previous_total": "600.00", "cost_per_km": "3.800"}
    assert truck["distance_km"] == "1000.0" and truck["litres_per_100km"] == "30.00"
    total = summary["totals"][0]
    assert total["currency"] == "AED" and total["total"] == "3950.00" and total["distance_km"] == "1050.0" and total["recorded"] == "3950.00"
    assert total["linked"] == "0.00" and total["previous_total"] == "600.00"
    assert [w["registration"] for w in summary["worst"]] == ["A 1001"]  # the pickup drove 50 km: too little to rank
    assert len([t for t in summary["trend"] if t["currency"] == "AED"]) == 12
    assert summary["renewals"][0]["kind"] == "insurance" and summary["renewals"][0]["registration"] == "A 1001"
    assert summary["tyres"]["status"] in {"not_connected", "denied"}
    assert client.get(f"{FLEET}/costs", headers=MEMBER, params={"month": month}).status_code == 200
    assert client.get(f"{FLEET}/costs", headers=VIEWER).status_code == 403
    assert client.get(f"{FLEET}/costs", headers=OWNER, params={"month": "2026-13"}).json()["code"] == "invalid_month"
    future = (utc_now().date().replace(day=28) + timedelta(days=5)).strftime("%Y-%m")
    assert client.get(f"{FLEET}/costs", headers=OWNER, params={"month": future}).status_code == 422


def test_a_linked_payment_replaces_the_record_figure_and_currencies_never_mix() -> None:
    cars = fleet_vehicles()
    add_costs(cars)
    truck = cars["A 1001"]["id"]
    entry = next(e for e in client.get(f"{API}/{truck}/fuel", headers=OWNER).json()["items"] if e["litres"] == "300.000")
    context = SimpleNamespace(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_fleet")
    vehicle_memory_store.set_payment(context, "fuel", entry["id"], PaymentRef(  # type: ignore[arg-type]
        link_id="l1", canonical_id="c1", status="active", amount=Decimal("880"), currency="AED", paid_by="owner_fleet", linked_at=utc_now()))
    sar = client.post(API, headers=OWNER, json={"registration": "S 1", "country": "SA", "fuel_type": "diesel", "ownership": "business"}).json()
    client.post(f"{API}/{sar['id']}/service", headers=OWNER, json={"kind": "repair", "service_on": month_ago(1, 3).isoformat(), "receipt_total": "1000"})
    summary = client.get(f"{FLEET}/costs", headers=OWNER, params={"month": month_ago(1, 1).strftime("%Y-%m")}).json()
    totals = {t["currency"]: t for t in summary["totals"]}
    assert totals["AED"]["total"] == "3930.00" and totals["AED"]["linked"] == "880.00" and totals["AED"]["recorded"] == "3050.00"
    assert totals["SAR"]["total"] == "1000.00" and totals["SAR"]["service"] == "1000.00" and totals["SAR"]["cost_per_km"] is None


def test_the_tyre_planning_snapshot_is_shown_beside_matched_vehicles(monkeypatch: pytest.MonkeyPatch) -> None:
    cars = fleet_vehicles()
    month = month_ago(1, 1)
    dataset = SimpleNamespace(vehicles=[SimpleNamespace(id="tp1", label="T-01", fleet_number=None), SimpleNamespace(id="tp9", label="Other", fleet_number=None)],
                              costs=[SimpleNamespace(vehicle_id="tp1", date=month.replace(day=4), amount=1200.0),
                                     SimpleNamespace(vehicle_id="tp1", date=month_ago(3, 4), amount=99.0)], currency="AED")
    loaded = SimpleNamespace(snapshot=SimpleNamespace(dataset=dataset, stats=SimpleNamespace(synced_at=utc_now())))
    soon = utc_now().date() + timedelta(days=20)
    forecasts = [SimpleNamespace(vehicle_id="tp1", state=SimpleNamespace(value="projected"), projected_removal=soon),
                 SimpleNamespace(vehicle_id="tp1", state=SimpleNamespace(value="projected"), projected_removal=soon + timedelta(days=200))]
    monkeypatch.setattr("anum_api.tyre.service.forecasts_cached", lambda context: (loaded, forecasts))
    summary = client.get(f"{FLEET}/costs", headers=OWNER, params={"month": month.strftime("%Y-%m")}).json()
    tyres = summary["tyres"]
    assert tyres["status"] == "ok" and tyres["matched"] == 1 and tyres["tyre_cost"] == "1200.00" and tyres["tyres_due"] == 1
    assert tyres["vehicles"][0]["vehicle_id"] == cars["A 1001"]["id"] and tyres["vehicles"][0]["next_removal"] == soon.isoformat()
    assert summary["totals"] == [] or all(t["tyres"] == "0.00" for t in summary["totals"])  # never added into the fleet totals


def test_record_costs_and_categories_are_pure() -> None:
    assert [fleet.category_of(k) for k in ("fuel", "oil", "tyres", "toll", "repair", "insurance")] == ["fuel", "service", "tyres", "other", "service", "other"]
    assert fleet.months_ending(date(2026, 2, 1), 3) == [date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]
    assert fleet.month_end(date(2028, 2, 1)) == date(2028, 2, 29)
    with pytest.raises(ValueError):
        fleet.parse_month("2026-1", date(2026, 5, 1))


# -- the monthly report --------------------------------------------------------------------------------


def report(headers: dict = OWNER, **body: Any):  # type: ignore[no-untyped-def]
    return client.post(f"{FLEET}/report", headers=headers, json={"month": month_ago(1, 1).strftime("%Y-%m"), "company_name": "Gulf Mixers LLC",
                                                                 "notes": "Tyre pressure checks start next month.\nTwo trucks idle.", **body})


def test_the_monthly_report_as_pdf_and_excel_in_english_and_arabic() -> None:
    cars = fleet_vehicles()
    add_costs(cars)
    english = report()
    assert english.status_code == 200 and english.headers["content-type"] == "application/pdf"
    assert english.content.startswith(b"%PDF") and english.content.rstrip().endswith(b"%%EOF")
    assert f'filename="fleet-report-{month_ago(1, 1).strftime("%Y-%m")}-en.pdf"' in english.headers["content-disposition"]
    assert english.headers["cache-control"] == "no-store" and b"Gulf Mixers LLC" in english.content and b"Monthly fleet cost report" in english.content
    arabic = report(language="ar")
    assert arabic.status_code == 200 and b"FontFile2" in arabic.content  # the Arabic font is embedded
    sheet = report(format="xlsx", language="ar")
    assert sheet.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with zipfile.ZipFile(io.BytesIO(sheet.content)) as archive:
        names = archive.namelist()
        first = archive.read("xl/worksheets/sheet1.xml").decode()
        notes = archive.read(f"xl/worksheets/sheet{len([n for n in names if n.startswith('xl/worksheets/')])}.xml").decode()
        book = archive.read("xl/workbook.xml").decode()
    assert 'rightToLeft="1"' in first and "تقرير تكاليف الأسطول الشهري" in first and "Gulf Mixers LLC" in first
    assert "Two trucks idle." in notes and "التجديدات" in book
    english_sheet = report(format="xlsx")
    with zipfile.ZipFile(io.BytesIO(english_sheet.content)) as archive:
        vehicles = archive.read("xl/worksheets/sheet2.xml").decode()
    assert "rightToLeft" not in vehicles and "A 1001" in vehicles and "<v>3800.00</v>" in vehicles
    audit = [r for r in memory_repository.store.audit_records if r.action == "vehicle.fleet_report"]
    assert audit[-1].metadata == {"month": month_ago(1, 1).strftime("%Y-%m"), "format": "xlsx", "language": "en"}


def test_the_report_needs_a_company_and_a_valid_month_and_says_when_a_month_is_empty() -> None:
    assert report(company_name="   ").status_code == 422
    assert report(month="2026/10").status_code == 422
    assert report(VIEWER).status_code == 403
    empty = report(format="xlsx")
    with zipfile.ZipFile(io.BytesIO(empty.content)) as archive:
        assert "No costs or kilometres were recorded for this month." in archive.read("xl/worksheets/sheet1.xml").decode()
    assert report(MEMBER).status_code == 200
