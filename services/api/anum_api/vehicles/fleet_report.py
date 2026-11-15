"""The monthly fleet report for a client: PDF or Excel, English or Arabic (docs/vehicles.md#monthly-fleet-report).

Built on the server from the fleet summary (``fleet.py``), so the figures are the fleet costs page's figures:
the company name and period, the month's totals per currency against the month before, cost per vehicle and
per km, the vehicles with the highest cost per km, the twelve months to the report month, the renewals due
(insurance, registration, inspection), the tyre planning snapshot when there is one (read only, never added to
the totals) and the notes the owner wrote. An Arabic report is right to left throughout (the PDF from the right
margin with mirrored tables, the Excel sheets opened right to left). The file is made for the request and never
stored or sent anywhere: the person downloads it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from .. import xlsx_writer
from ..pdf_writer import PdfDocument

Language = Literal["en", "ar"]
Format = Literal["pdf", "xlsx"]
MAX_NOTES = 4000
MAX_COMPANY = 120

MONTHS = {
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"),
    "ar": ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"),
}

TEXT: dict[str, dict[str, str]] = {
    "en": {
        "title": "Monthly fleet cost report", "company": "Company", "period": "Period", "prepared": "Prepared on", "summary": "Summary",
        "item": "Item", "value": "Value", "vehicles": "Vehicles", "distance": "Kilometres driven", "litres": "Fuel litres",
        "fuel": "Fuel", "service": "Service and maintenance", "tyres": "Tyres", "other": "Other", "total": "Total", "cost_per_km": "Cost per km",
        "previous": "Previous month", "change": "Change", "linked": "From linked payments", "recorded": "From record figures",
        "per_vehicle": "Cost per vehicle", "registration": "Registration", "vehicle": "Vehicle", "km": "Km", "l100": "L/100 km",
        "currency": "Currency", "worst": "Highest cost per km", "trend": "Last 12 months", "month": "Month", "renewals": "Renewals due",
        "kind": "Renewal", "due_on": "Due on", "days_left": "Days left", "overdue": "Overdue", "insurance": "Insurance",
        "registration_renewal": "Registration", "inspection": "Inspection",
        "tyre_title": "Tyres from Tyre planning (shown beside the totals, not added to them)", "tyre_vehicle": "Tyre planning vehicle",
        "tyre_cost": "Tyre cost", "tyres_due": "Tyres due within 60 days", "next_removal": "Next removal",
        "notes": "Notes", "no_notes": "No notes.", "none": "None.", "no_rows": "No rows.",
        "basis": ("Each record counts once: its linked payment when it has one, otherwise the figure on the record (the pump or receipt total). "
                  "Amounts in different currencies are never added together. Cost per km divides a currency's cost by the kilometres of the "
                  "vehicles kept in that currency."),
        "no_data": "No costs or kilometres were recorded for this month.", "page": "page {number} of {total}", "footer": "ANUM fleet report",
        "sheet_summary": "Summary", "sheet_vehicles": "Vehicles", "sheet_trend": "Last 12 months", "sheet_renewals": "Renewals",
        "sheet_tyres": "Tyres", "sheet_notes": "Notes",
    },
    "ar": {
        "title": "تقرير تكاليف الأسطول الشهري", "company": "الشركة", "period": "الفترة", "prepared": "تاريخ الإعداد", "summary": "الملخص",
        "item": "البند", "value": "القيمة", "vehicles": "المركبات", "distance": "الكيلومترات المقطوعة", "litres": "لترات الوقود",
        "fuel": "الوقود", "service": "الخدمة والصيانة", "tyres": "الإطارات", "other": "أخرى", "total": "الإجمالي", "cost_per_km": "التكلفة لكل كيلومتر",
        "previous": "الشهر السابق", "change": "التغير", "linked": "من المدفوعات المرتبطة", "recorded": "من مبالغ السجلات",
        "per_vehicle": "التكلفة لكل مركبة", "registration": "رقم اللوحة", "vehicle": "المركبة", "km": "كم", "l100": "لتر لكل 100 كم",
        "currency": "العملة", "worst": "الأعلى تكلفة لكل كيلومتر", "trend": "آخر 12 شهرا", "month": "الشهر", "renewals": "التجديدات المستحقة",
        "kind": "التجديد", "due_on": "تاريخ الاستحقاق", "days_left": "الأيام المتبقية", "overdue": "متأخر", "insurance": "التأمين",
        "registration_renewal": "تسجيل المركبة", "inspection": "الفحص الدوري",
        "tyre_title": "الإطارات من تخطيط الإطارات (معروضة بجانب الإجماليات وغير مضافة إليها)", "tyre_vehicle": "المركبة في تخطيط الإطارات",
        "tyre_cost": "تكلفة الإطارات", "tyres_due": "إطارات مستحقة خلال 60 يوما", "next_removal": "أقرب استبدال",
        "notes": "ملاحظات", "no_notes": "لا توجد ملاحظات.", "none": "لا يوجد.", "no_rows": "لا توجد صفوف.",
        "basis": ("يُحتسب كل سجل مرة واحدة: بالدفعة المرتبطة به إن وجدت، وإلا بالمبلغ المكتوب في السجل (إجمالي المضخة أو الإيصال). "
                  "لا تُجمع المبالغ بعملات مختلفة. التكلفة لكل كيلومتر هي تكلفة العملة مقسومة على كيلومترات المركبات المسجلة بتلك العملة."),
        "no_data": "لم تُسجل تكاليف أو كيلومترات لهذا الشهر.", "page": "صفحة {number} من {total}", "footer": "تقرير الأسطول من ANUM",
        "sheet_summary": "الملخص", "sheet_vehicles": "المركبات", "sheet_trend": "آخر 12 شهرا", "sheet_renewals": "التجديدات",
        "sheet_tyres": "الإطارات", "sheet_notes": "ملاحظات",
    },
}
CATEGORY_KEYS = ("fuel", "service", "tyres", "other")


def month_name(label: str, language: Language) -> str:
    year, month = label.split("-")
    return f"{MONTHS[language][int(month) - 1]} {year}"


def _amount(value: str | None) -> str:
    if value is None:
        return "-"
    number = Decimal(value)
    return f"{number:,.2f}"


def _km(value: str | None) -> str:
    return "-" if value is None else f"{Decimal(value):,.0f}"


def _change(current: str, previous: str) -> str:
    now, before = Decimal(current), Decimal(previous)
    if before == 0:
        return "-"
    pct = (now - before) / before * 100
    return f"{'+' if pct > 0 else ''}{pct:,.1f}%"


def _vehicle(row: dict[str, Any]) -> str:
    parts = [row.get("nickname"), row.get("make"), row.get("model")]
    return " ".join(p for p in parts if p) or "-"


def _renewal(kind: str, t: dict[str, str]) -> str:
    return t["registration_renewal"] if kind == "registration" else t[kind]


def _days(days: int, t: dict[str, str]) -> str:
    return t["overdue"] if days < 0 else str(days)


def _lines(summary: dict[str, Any], t: dict[str, str]) -> dict[str, list[list[Any]]]:
    """Every table of the report as rows of text, the same for the PDF and the Excel file."""
    totals = summary["totals"]
    summary_rows: list[list[Any]] = [[t["vehicles"], str(summary["vehicles"])], [t["distance"], _km(summary["distance_km"])],
                                     [t["litres"], f"{Decimal(summary['fuel_litres']):,.1f}"]]
    money: list[list[Any]] = []
    for item in totals:
        money.append([item["currency"], *(_amount(item[c]) for c in CATEGORY_KEYS), _amount(item["total"]), _amount(item["previous_total"]),
                      _change(item["total"], item["previous_total"]), _amount(item["cost_per_km"]) if item["cost_per_km"] else "-",
                      _amount(item["linked"]), _amount(item["recorded"])])
    vehicles: list[list[Any]] = []
    for row in summary["rows"]:
        costs = row["costs"] or [{"currency": row["currency"], **{c: "0.00" for c in CATEGORY_KEYS}, "total": "0.00", "cost_per_km": None}]
        for cost in costs:
            vehicles.append([row["registration"], _vehicle(row), _km(row["distance_km"]), row["litres_per_100km"] or "-", cost["currency"],
                             *(_amount(cost[c]) for c in CATEGORY_KEYS), _amount(cost["total"]),
                             _amount(cost["cost_per_km"]) if cost["cost_per_km"] else "-"])
    worst = [[w["registration"], w.get("nickname") or "-", w["currency"], _amount(w["cost_per_km"]), _amount(w["total"]), _km(w["distance_km"])]
             for w in summary["worst"]]
    trend = [[month_name(item["month"], t["_lang"]), item["currency"], *(_amount(item[c]) for c in CATEGORY_KEYS), _amount(item["total"]),  # type: ignore[arg-type]
              _km(item["distance_km"]), _amount(item["cost_per_km"]) if item["cost_per_km"] else "-"]
             for item in summary["trend"]]
    renewals = [[r["registration"], r.get("nickname") or "-", _renewal(r["kind"], t), r["due_on"], _days(r["days_left"], t)] for r in summary["renewals"]]
    tyres = summary.get("tyres") or {}
    tyre_rows = [[r["registration"], r["tyre_vehicle"], f"{_amount(r['tyre_cost'])} {tyres.get('currency', '')}".strip(), str(r["tyres_due"]),
                  r["next_removal"] or "-"] for r in tyres.get("vehicles", [])] if tyres.get("status") == "ok" else []
    return {"summary": summary_rows, "money": money, "vehicles": vehicles, "worst": worst, "trend": trend, "renewals": renewals, "tyres": tyre_rows}


def _columns(t: dict[str, str]) -> dict[str, list[str]]:
    categories = [t[c] for c in CATEGORY_KEYS]
    return {
        "summary": [t["item"], t["value"]],
        "money": [t["currency"], *categories, t["total"], t["previous"], t["change"], t["cost_per_km"], t["linked"], t["recorded"]],
        "vehicles": [t["registration"], t["vehicle"], t["km"], t["l100"], t["currency"], *categories, t["total"], t["cost_per_km"]],
        "worst": [t["registration"], t["vehicle"], t["currency"], t["cost_per_km"], t["total"], t["km"]],
        "trend": [t["month"], t["currency"], *categories, t["total"], t["km"], t["cost_per_km"]],
        "renewals": [t["registration"], t["vehicle"], t["kind"], t["due_on"], t["days_left"]],
        "tyres": [t["registration"], t["tyre_vehicle"], t["tyre_cost"], t["tyres_due"], t["next_removal"]],
    }


def _texts(language: Language) -> dict[str, str]:
    return {**TEXT[language], "_lang": language}


def _empty_month(summary: dict[str, Any]) -> bool:
    return Decimal(summary["distance_km"]) == 0 and all(Decimal(t["total"]) == 0 for t in summary["totals"])


def pdf(summary: dict[str, Any], *, company: str, notes: str, language: Language, prepared_on: date) -> bytes:
    t = _texts(language)
    rtl = language == "ar"
    doc = PdfDocument(f"{t['footer']}: {company}", rtl=rtl, page_label=t["page"], empty=t["no_rows"])
    lines, columns = _lines(summary, t), _columns(t)
    doc.heading(t["title"], 16)
    doc.table(t["summary"], columns["summary"], [[t["company"], company], [t["period"], f"{month_name(summary['month'], language)} ({summary['start']} - {summary['end']})"],
                                                  [t["prepared"], prepared_on.isoformat()], *lines["summary"]])
    if _empty_month(summary):
        doc.paragraph(t["no_data"])
    doc.table(t["total"], columns["money"], lines["money"])
    doc.paragraph(t["basis"], 8)
    doc.table(t["per_vehicle"], columns["vehicles"], lines["vehicles"])
    doc.table(t["worst"], columns["worst"], lines["worst"])
    doc.table(t["trend"], columns["trend"], lines["trend"])
    doc.table(t["renewals"], columns["renewals"], lines["renewals"])
    if lines["tyres"]:
        doc.table(t["tyre_title"], columns["tyres"], lines["tyres"])
    doc.heading(t["notes"], 12)
    for paragraph in (notes.strip() or t["no_notes"]).split("\n"):
        if paragraph.strip():
            doc.paragraph(paragraph.strip(), 9)
    return doc.render()


def xlsx(summary: dict[str, Any], *, company: str, notes: str, language: Language, prepared_on: date) -> bytes:
    t = _texts(language)
    rtl = language == "ar"
    lines, columns = _lines(summary, t), _columns(t)
    numbers = _numbers(summary)

    first = xlsx_writer.Sheet(t["sheet_summary"], rtl=rtl)
    first.title(t["title"])
    first.row([t["company"], company])
    first.row([t["period"], month_name(summary["month"], language), summary["start"], summary["end"]])
    first.row([t["prepared"], prepared_on.isoformat()])
    first.row([t["vehicles"], summary["vehicles"]])
    first.row([t["distance"], Decimal(summary["distance_km"])])
    first.row([t["litres"], Decimal(summary["fuel_litres"])])
    first.blank()
    first.header(columns["money"])
    for row in numbers["money"]:
        first.row(row)
    if _empty_month(summary):
        first.row([t["no_data"]])
    first.blank()
    first.row([t["basis"]])

    vehicles = xlsx_writer.Sheet(t["sheet_vehicles"], rtl=rtl)
    vehicles.header(columns["vehicles"])
    for row in numbers["vehicles"]:
        vehicles.row(row)
    vehicles.blank()
    vehicles.title(t["worst"])
    vehicles.header(columns["worst"])
    for row in numbers["worst"]:
        vehicles.row(row)

    trend = xlsx_writer.Sheet(t["sheet_trend"], rtl=rtl)
    trend.header(columns["trend"])
    for row in numbers["trend"]:
        trend.row(row)

    renewals = xlsx_writer.Sheet(t["sheet_renewals"], rtl=rtl)
    renewals.header(columns["renewals"])
    for row in lines["renewals"] or [[t["none"]]]:
        renewals.row(row)

    sheets = [first, vehicles, trend, renewals]
    if lines["tyres"]:
        tyres = xlsx_writer.Sheet(t["sheet_tyres"], rtl=rtl)
        tyres.title(t["tyre_title"])
        tyres.header(columns["tyres"])
        for row in lines["tyres"]:
            tyres.row(row)
        sheets.append(tyres)
    notes_sheet = xlsx_writer.Sheet(t["sheet_notes"], rtl=rtl)
    notes_sheet.title(t["notes"])
    for paragraph in (notes.strip() or t["no_notes"]).split("\n"):
        notes_sheet.row([paragraph.rstrip()])
    sheets.append(notes_sheet)
    return xlsx_writer.workbook(sheets)


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _numbers(summary: dict[str, Any]) -> dict[str, list[list[Any]]]:
    """The Excel tables with numbers as numbers (the PDF shows the same figures as text)."""
    money = [[item["currency"], *(Decimal(item[c]) for c in CATEGORY_KEYS), Decimal(item["total"]), Decimal(item["previous_total"]),
              _decimal(item["cost_per_km"]), Decimal(item["linked"]), Decimal(item["recorded"])] for item in summary["totals"]]
    vehicles: list[list[Any]] = []
    for row in summary["rows"]:
        for cost in row["costs"]:
            vehicles.append([row["registration"], _vehicle(row), Decimal(row["distance_km"]), _decimal(row["litres_per_100km"]), cost["currency"],
                             *(Decimal(cost[c]) for c in CATEGORY_KEYS), Decimal(cost["total"]), _decimal(cost["cost_per_km"])])
    worst = [[w["registration"], w.get("nickname") or "", w["currency"], _decimal(w["cost_per_km"]), Decimal(w["total"]), Decimal(w["distance_km"])]
             for w in summary["worst"]]
    trend = [[item["month"], item["currency"], *(Decimal(item[c]) for c in CATEGORY_KEYS), Decimal(item["total"]), Decimal(item["distance_km"]),
              _decimal(item["cost_per_km"])] for item in summary["trend"]]
    return {"money": money, "vehicles": vehicles, "worst": worst, "trend": trend}


def file_name(month: str, language: Language, kind: Format) -> str:
    return f"fleet-report-{month}-{language}.{kind}"


MEDIA_TYPES = {"pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}

__all__ = ["MAX_COMPANY", "MAX_NOTES", "MEDIA_TYPES", "file_name", "month_name", "pdf", "xlsx"]
