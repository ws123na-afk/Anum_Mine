"""The fleet pilot gaps (docs/vehicles.md#fleet): trips and workbook sheets in the import, the report's notes kept per
fleet and month, the AI summary paragraph built only from computed figures, and sending the report through the delivery
and approval path. In-memory stores; ``test_postgres_vehicles_fleet.py`` repeats the saved draft under RLS.
"""

from __future__ import annotations

import base64
import io
import re
import zipfile
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from test_vehicles_fleet import API, FLEET, MEMBER, OWNER, VIEWER, add_costs, apply, clean, client, fleet_vehicles, month_ago, preview  # noqa: F401

from anum_api import delivery, main, xlsx_writer
from anum_api.agent_tools import ToolCall, ToolDefinition, ToolResult
from anum_api.dependencies import memory_repository
from anum_api.model_budget import BudgetKind, BudgetScope, ModelBudgetExceededError
from anum_api.schemas import RiskLevel, TenantContext
from anum_api.vehicles import fleet_api, fleet_import, fleet_summary

MONTH = month_ago(1, 1).strftime("%Y-%m")


# -- trips in the import -----------------------------------------------------------------------------------------------


def test_trips_import_with_odometer_or_distance_and_finds_them_again() -> None:
    fleet_vehicles()
    d1, d2, d3 = (month_ago(1, day).strftime("%d/%m/%Y") for day in (5, 6, 7))
    trips = ("Plate No,Trip date,End date,Start km,End km,Distance,Purpose,Reason\n"
             f"A 1001,{d1},,121000,121150,,Business,Site visit\n"
             f"A 1001,{d2},,121150,,80,عمل,\n"
             f"A 1001,{d3} 09:30,{d3} 14:00,121230,121300,,Personal,\n")
    shown = preview("trips", trips)
    assert shown.status_code == 200, shown.text
    body = shown.json()
    assert body["mapping"] == {"registration": "Plate No", "date": "Trip date", "end_date": "End date", "start_odometer_km": "Start km",
                               "end_odometer_km": "End km", "distance_km": "Distance", "purpose": "Purpose", "purpose_note": "Reason"}
    assert [r["status"] for r in body["rows"]] == ["new", "new", "new"]
    assert body["rows"][1]["summary"]["end_odometer_km"] == "121230.0" and body["rows"][2]["summary"]["purpose"] == "personal"
    result = apply("trips", trips, body).json()
    assert result == {"kind": "trips", "rows": 3, "created": 3, "updated": 0, "skipped": 0, "readings": 6}
    truck = next(v for v in client.get(API, headers=OWNER).json()["items"] if v["registration"] == "A 1001")
    listed = client.get(f"{API}/{truck['id']}/trips", headers=OWNER).json()["items"]
    assert sorted(t["distance_km"] for t in listed) == ["150.0", "70.0", "80.0"]
    assert all(t["status"] == "closed" for t in listed)
    assert [r["status"] for r in preview("trips", trips).json()["rows"]] == ["imported", "imported", "imported"]


def test_trip_rows_without_an_odometer_or_with_a_wrong_distance_are_refused_by_row() -> None:
    fleet_vehicles()
    day = month_ago(1, 8).isoformat()
    trips = ("Registration,Date,Start km,End km,Distance,Purpose\n"
             f"A 1001,{day},,,40,business\n"
             f"A 1001,{day},121000,121100,30,business\n"
             f"A 1001,{day},121000,121100,,holiday\n"
             f"Z 9999,{day},1,2,,business\n")
    rows = preview("trips", trips).json()["rows"]
    assert [r["status"] for r in rows] == ["invalid"] * 4
    assert [r["issues"][0]["code"] for r in rows] == ["required", "range", "choice", "unknown_vehicle"]


# -- workbook sheets ---------------------------------------------------------------------------------------------------


def workbook() -> str:
    vehicles = xlsx_writer.Sheet("Vehicles")
    vehicles.header(["Plate No", "Make", "Model", "Insurance expiry"])
    vehicles.row(["C 3003", "Volvo", "FMX", ""])
    fuel = xlsx_writer.Sheet("Diesel")
    fuel.header(["Plate No", "Date", "Litres", "Amount"])
    fuel.row(["C 3003", month_ago(1, 3).isoformat(), Decimal("100"), Decimal("300")])
    trips = xlsx_writer.Sheet("Trips log")
    trips.header(["Plate No", "Trip date", "Start km", "End km"])
    trips.row(["C 3003", month_ago(1, 4).isoformat(), Decimal("1000"), Decimal("1100")])
    notes = xlsx_writer.Sheet("Read me")
    notes.row(["Fleet data for October"])
    return base64.b64encode(xlsx_writer.workbook([notes, vehicles, fuel, trips])).decode()


def sheet_preview(kind: str, data: str, **extra: Any):  # type: ignore[no-untyped-def]
    return client.post(f"{FLEET}/import/preview", headers=OWNER, json={"kind": kind, "format": "xlsx", "file_base64": data, **extra})


def test_a_multi_sheet_workbook_reads_the_sheet_that_fits_each_kind_or_the_one_chosen() -> None:
    data = workbook()
    first = sheet_preview("vehicles", data)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["sheet"] == "Vehicles"
    assert [(s["name"], s["kind"]) for s in body["sheets"]] == [("Read me", None), ("Vehicles", "vehicles"), ("Diesel", "fuel"), ("Trips log", "trips")]
    done = client.post(f"{FLEET}/import/apply", headers=OWNER, json={"kind": "vehicles", "format": "xlsx", "file_base64": data, "sheet": body["sheet"],
                                                                     "mapping": body["mapping"], "fingerprint": body["fingerprint"]})
    assert done.status_code == 200 and done.json()["created"] == 1, done.text
    # Applied without naming the sheet, the same detected sheet is read (the fingerprint names the sheet read).
    fuel = sheet_preview("fuel", data).json()
    assert fuel["sheet"] == "Diesel" and fuel["rows"][0]["status"] == "new"
    applied = client.post(f"{FLEET}/import/apply", headers=OWNER, json={"kind": "fuel", "format": "xlsx", "file_base64": data, "mapping": fuel["mapping"],
                                                                        "fingerprint": fuel["fingerprint"]})
    assert applied.status_code == 200 and applied.json()["created"] == 1, applied.text
    trips = sheet_preview("trips", data).json()
    assert trips["sheet"] == "Trips log" and trips["rows"][0]["summary"]["distance_km"] == "100.0"
    # A sheet the person chose that does not fit the kind is read as they asked: its rows say what is missing.
    chosen = sheet_preview("trips", data, sheet="Diesel").json()
    assert chosen["sheet"] == "Diesel" and "end_odometer_km" not in chosen["mapping"]
    assert [r["issues"][0]["code"] for r in chosen["rows"]] == ["required"]
    stale = client.post(f"{FLEET}/import/apply", headers=OWNER, json={"kind": "trips", "format": "xlsx", "file_base64": data, "sheet": "Diesel",
                                                                      "mapping": trips["mapping"], "fingerprint": trips["fingerprint"]})
    assert stale.status_code == 409 and stale.json()["code"] == "stale_preview"
    missing = sheet_preview("fuel", data, sheet="Nope")
    assert missing.status_code == 422 and missing.json()["code"] == "unknown_sheet" and len(missing.json()["sheets"]) == 4
    empty = sheet_preview("fuel", data, sheet="Read me")
    assert empty.status_code == 422 and empty.json()["sheet"] == "Read me" and "Choose another sheet" in empty.json()["detail"]
    csv_sheet = preview("fuel", "Plate,Date,Litres\nA,2026-01-01,1\n", sheet="Diesel")
    assert csv_sheet.status_code == 422


def test_sheet_kinds_are_detected_from_headers() -> None:
    assert fleet_import.detect_kind(["رقم اللوحة", "التاريخ", "اللترات", "المبلغ"]) == "fuel"
    assert fleet_import.detect_kind(["Registration", "Date", "Odometer"]) == "odometer"
    assert fleet_import.detect_kind(["Plate", "Service type", "Date", "Workshop", "Amount"]) == "service"
    assert fleet_import.detect_kind(["Plate", "Make", "Model", "Year", "VIN"]) == "vehicles"
    assert fleet_import.detect_kind(["Something", "Else"]) is None


# -- the saved draft ---------------------------------------------------------------------------------------------------


def draft(headers: dict = OWNER, month: str = MONTH):  # type: ignore[no-untyped-def]
    return client.get(f"{FLEET}/report/draft", headers=headers, params={"month": month})


def save(body: dict, headers: dict = OWNER):  # type: ignore[no-untyped-def]
    return client.put(f"{FLEET}/report/draft", headers=headers, json={"month": MONTH, **body})


def test_the_reports_notes_are_kept_per_fleet_and_month() -> None:
    fresh = draft().json()
    assert fresh["saved"] is False and fresh["notes"] == "" and fresh["company_name"] is None and fresh["version"] == 0
    assert fresh["ai"]["available"] is False and fresh["ai"]["reason"] == "no_model" and fresh["can_edit"] is True
    saved = save({"company_name": "  Gulf   Mixers LLC ", "notes": "Two trucks idle.\r\nTyre checks next month.", "version": 0})
    assert saved.status_code == 200, saved.text
    assert saved.json()["company_name"] == "Gulf Mixers LLC" and saved.json()["notes"] == "Two trucks idle.\nTyre checks next month."
    again = draft(MEMBER).json()
    assert again["version"] == 1 and again["notes"].startswith("Two trucks idle.") and again["updated_by"] == "owner_fleet"
    stale = save({"company_name": "Other", "notes": "", "version": 0}, MEMBER)
    assert stale.status_code == 409 and stale.json()["code"] == "stale"
    assert save({"company_name": "Gulf Mixers LLC", "notes": "Member note", "version": 1}, MEMBER).status_code == 200
    assert save({"company_name": "x", "notes": "", "version": 2}, VIEWER).status_code == 403
    assert draft(VIEWER).status_code == 403
    assert save({"company_name": "x", "notes": "n" * 4001, "version": 2}).status_code == 422
    # The next month starts with the company name of the newest earlier month; its notes start empty.
    later = draft(month=month_ago(0, 1).strftime("%Y-%m")).json()
    assert later["company_name"] == "Gulf Mixers LLC" and later["company_from"] == MONTH and later["notes"] == "" and later["saved"] is False
    audit = [r for r in memory_repository.store.audit_records if r.action == "vehicle.fleet_report_draft"]
    assert audit[-1].metadata == {"month": MONTH}  # never the notes or the company name


# -- the AI summary ----------------------------------------------------------------------------------------------------


class FakeGateway:
    provider = "test"

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    async def generate_text(self, prompt: str):  # type: ignore[no-untyped-def]
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        text = self.answer(prompt) if callable(self.answer) else self.answer
        return type("R", (), {"text": text, "usage": type("U", (), {"provider": "test", "model": "fake-1"})()})()


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGateway]:
    def honest(prompt: str) -> str:
        total = re.search(r"Total cost in AED: ([\d,.]+)", prompt).group(1)  # type: ignore[union-attr]
        vehicles = re.search(r"Vehicles: (\d+)", prompt).group(1)  # type: ignore[union-attr]
        return f"The {vehicles} vehicles cost AED {total} this month — mostly tyres."

    gateway = FakeGateway(honest)
    monkeypatch.setattr(fleet_summary, "workspace_gateway", lambda context: gateway)
    monkeypatch.setattr(fleet_summary, "route_gateway", lambda gateway, route: gateway)
    yield gateway


def summarise(language: str = "en", headers: dict = OWNER):  # type: ignore[no-untyped-def]
    return client.post(f"{FLEET}/report/summary", headers=headers, json={"month": MONTH, "language": language})


def test_ai_summary_is_off_with_the_mock_model_and_for_an_empty_month() -> None:
    fleet_vehicles()
    assert summarise().json()["code"] == "no_data"
    add_costs(fleet_vehicles())
    off = summarise()
    assert off.status_code == 409 and off.json()["code"] == "ai_off"
    assert summarise(headers=VIEWER).status_code == 403


def test_ai_summary_is_written_from_the_computed_figures_and_labelled_in_the_report(model: FakeGateway) -> None:
    add_costs(fleet_vehicles())
    written = summarise()
    assert written.status_code == 200, written.text
    paragraph = written.json()["summaries"]["en"]
    assert paragraph["current"] is True and paragraph["model"] == "test/fake-1"
    assert paragraph["text"] == "The 2 vehicles cost AED 3,950.00 this month, mostly tyres."  # long dash replaced
    prompt = model.prompts[0]
    assert "untrusted" in prompt.lower() and "Total cost in AED: 3,950.00; previous month 600.00; change +558.3%" in prompt
    assert "Never calculate" in prompt and "A 1001" in prompt
    sheet = client.post(f"{FLEET}/report", headers=OWNER, json={"month": MONTH, "company_name": "Gulf Mixers LLC", "format": "xlsx"})
    with zipfile.ZipFile(io.BytesIO(sheet.content)) as archive:
        first = archive.read("xl/worksheets/sheet1.xml").decode()
    assert "Summary (written by AI)" in first and "AED 3,950.00 this month" in first and "adds no figures of its own" in first
    without = client.post(f"{FLEET}/report", headers=OWNER, json={"month": MONTH, "company_name": "Gulf Mixers LLC", "format": "xlsx",
                                                                  "include_summary": False})
    with zipfile.ZipFile(io.BytesIO(without.content)) as archive:
        assert "Summary (written by AI)" not in archive.read("xl/worksheets/sheet1.xml").decode()
    # New figures: the paragraph is no longer current and stays out of the report until it is written again.
    client.post(f"{API}/{fleet_vehicles()['A 1001']['id']}/service", headers=OWNER,
                json={"kind": "wash", "service_on": month_ago(1, 15).isoformat(), "receipt_total": "20"})
    assert draft().json()["summaries"]["en"]["current"] is False
    stale = client.post(f"{FLEET}/report", headers=OWNER, json={"month": MONTH, "company_name": "Gulf Mixers LLC", "format": "xlsx"})
    with zipfile.ZipFile(io.BytesIO(stale.content)) as archive:
        assert "Summary (written by AI)" not in archive.read("xl/worksheets/sheet1.xml").decode()
    removed = client.delete(f"{FLEET}/report/summary", headers=OWNER, params={"month": MONTH, "language": "en"})
    assert removed.status_code == 200 and removed.json()["summaries"] == {}
    audit = [r for r in memory_repository.store.audit_records if r.action == "vehicle.fleet_report_summary"]
    assert audit[-1].metadata == {"month": MONTH, "language": "en"}


def test_a_paragraph_with_a_figure_not_in_the_report_is_withheld(model: FakeGateway) -> None:
    add_costs(fleet_vehicles())
    model.answer = "The fleet cost AED 9,999.00 and will cost less next month."
    withheld = summarise("ar")
    assert withheld.status_code == 422 and withheld.json()["code"] == "summary_withheld"
    assert draft().json()["summaries"] == {}
    model.answer = ModelBudgetExceededError(list(BudgetScope)[0], list(BudgetKind)[0], month_ago(0, 1))
    assert summarise().json()["code"] == "model_budget"
    model.answer = TimeoutError("slow")
    assert summarise().status_code == 502


def test_the_figure_check_reads_arabic_digits_and_separators() -> None:
    facts = "Total cost in AED: 3,950.00\nVehicles: 2\nCost per km in AED: 3.800"
    assert fleet_summary.check("بلغت التكلفة ٣٬٩٥٠٫٠٠ درهم لمركبتين (٢)، أي 3.8 لكل كم", facts).startswith("بلغت")
    with pytest.raises(fleet_summary.SummaryWithheld):
        fleet_summary.check("Cost rose 12% to AED 3,950", facts)
    with pytest.raises(fleet_summary.SummaryWithheld):
        fleet_summary.check("   ", facts)


# -- sending -----------------------------------------------------------------------------------------------------------


@pytest.fixture
def email_tool() -> Iterator[list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    async def send(call: ToolCall, context: TenantContext) -> ToolResult:
        attachment = delivery.resolve_attachment(call.arguments["attachments"][0], context)
        sent.append({**call.arguments, "bytes": attachment.content, "filename": attachment.filename})
        return ToolResult(status="succeeded", summary=f"Emailed {call.arguments['to']}.")

    registry = main.tool_registry
    registry.register(ToolDefinition(name="test.fleet_email", description="fake email", risk_level=RiskLevel.HIGH, delivery_channel="email"), send)
    yield sent
    registry._definitions.pop("test.fleet_email")
    registry._handlers.pop("test.fleet_email")


def send(headers: dict = OWNER, **body: Any):  # type: ignore[no-untyped-def]
    return client.post(f"{FLEET}/report/send", headers=headers, json={"month": MONTH, "channel": "email", "to": "fleet@client.example", **body})


def test_sending_needs_a_saved_company_and_a_channel() -> None:
    add_costs(fleet_vehicles())
    assert send().json()["code"] == "company_required"
    save({"company_name": "Gulf Mixers LLC", "notes": "", "version": 0})
    unavailable = send()
    assert unavailable.status_code == 409 and unavailable.json()["code"] == "channel_unavailable"
    assert draft().json()["deliveries"][0]["status"] == "unavailable"
    assert send(to="+971501234567").status_code == 422
    assert send(VIEWER).status_code == 403


def test_the_report_is_sent_only_after_approval_and_exactly_as_approved(email_tool: list[dict[str, Any]]) -> None:
    add_costs(fleet_vehicles())
    save({"company_name": "Gulf Mixers LLC", "notes": "Two trucks idle.", "version": 0})
    assert draft().json()["channels"] == ["email"]
    requested = send(MEMBER, format="xlsx", language="ar")
    body = requested.json()
    assert requested.status_code == 200 and body["status"] == "waiting_approval" and body["approval_id"], requested.text
    assert email_tool == []  # nothing leaves before the approval
    deliveries = body["draft"]["deliveries"]
    assert deliveries[0]["status"] == "waiting_approval" and deliveries[0]["to"] == "fleet@client.example" and "snapshot" not in deliveries[0]
    # The notes change after the request: what was approved is still what is sent.
    save({"company_name": "Gulf Mixers LLC", "notes": "Changed later.", "version": 1})
    approved = client.post(f"/api/v1/approvals/{body['approval_id']}/approve", headers=OWNER, json={"payload_hash": body["payload_hash"]})
    assert approved.status_code == 200, approved.text
    assert len(email_tool) == 1
    sent = email_tool[0]
    assert sent["to"] == "fleet@client.example" and sent["subject"].startswith("Gulf Mixers LLC: تقرير تكاليف الأسطول الشهري")
    assert sent["filename"] == f"fleet-report-{MONTH}-ar.xlsx"
    with zipfile.ZipFile(io.BytesIO(sent["bytes"])) as archive:
        names = archive.namelist()
        notes = archive.read(f"xl/worksheets/sheet{len([n for n in names if n.startswith('xl/worksheets/')])}.xml").decode()
    assert "Two trucks idle." in notes and "Changed later." not in notes
    audit = [r for r in memory_repository.store.audit_records if r.action == "vehicle.fleet_report_delivery"]
    assert audit[-1].metadata["status"] == "waiting_approval" and "to" not in audit[-1].metadata


def test_a_delivery_attachment_is_rendered_from_its_frozen_content() -> None:
    add_costs(fleet_vehicles())
    save({"company_name": "Gulf Mixers LLC", "notes": "", "version": 0})
    ctx = TenantContext(tenant_id=OWNER["x-tenant-id"], workspace_id=OWNER["x-workspace-id"], user_id=OWNER["x-user-id"])
    month = fleet_api._month(MONTH)
    body = fleet_api.SendInput(month=MONTH, channel="email", to="a@b.example")
    draft_id, entry_id, _snapshot = fleet_api._prepare(ctx, month, body)
    first = delivery.attachment_reference("fleet_report", f"{draft_id}:{entry_id}", "pdf", ctx)
    again = delivery.attachment_reference("fleet_report", f"{draft_id}:{entry_id}", "pdf", ctx)
    assert first["sha256"] == again["sha256"] and first["content_type"] == "application/pdf"
    assert fleet_api._resolve_attachment(ctx, f"{draft_id}:missing", "pdf") is None
    assert fleet_api._resolve_attachment(ctx, f"{draft_id}:{entry_id}", "csv") is None
