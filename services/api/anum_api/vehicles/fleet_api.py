"""HTTP routes for running a client fleet: import, fleet costs and the monthly report (docs/vehicles.md#fleet).

* ``GET /api/v1/vehicles/fleet/costs?month=YYYY-MM`` (``vehicle:read``): the fleet cost summary of the workspace's
  business vehicles (``fleet.py``).
* ``POST /api/v1/vehicles/fleet/report`` (``vehicle:read``): the monthly client report as a PDF or Excel file, in
  English or Arabic (``fleet_report.py``), with the month's AI summary paragraph when one is saved and still current.
  Audited with the month, format and language only.
* ``GET|PUT /api/v1/vehicles/fleet/report/draft`` (read ``vehicle:read``, save ``vehicle:write``): the report's company
  name and notes kept per fleet and month (``fleet_reports.py``), the AI paragraphs, the delivery channels and the
  deliveries so far.
* ``POST|DELETE /api/v1/vehicles/fleet/report/summary`` (``vehicle:write``): write, through the workspace's budgeted
  model, or remove the AI summary paragraph (``fleet_summary.py``).
* ``POST /api/v1/vehicles/fleet/report/send`` (``vehicle:write``): send the report by email or WhatsApp through the
  generic delivery path (``delivery.py``): a high-risk tool call that waits for an approval bound to the payload hash.
  The file attached is rendered from the frozen content stored with the delivery.
* ``POST /api/v1/vehicles/fleet/import/preview`` and ``/apply`` (``vehicle:write`` and ``vehicle:manage``, workspace
  owners): a client's vehicles, fuel, service records, trips or odometer readings from a CSV file or one sheet of an
  Excel workbook (``fleet_import.py``). Apply is all or nothing and audited with the kind and counts only.

Refusals answer ``{"detail": "<plain words>", "code": "<code>"}`` like the rest of Vehicles.
"""

from __future__ import annotations

import asyncio
import functools
import re
from collections.abc import Callable
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .. import delivery
from ..authorization import Permission
from ..dependencies import UnitsOfWork, has_permission, repository_context, run_in_unit, tenant_context, units_of_work
from ..model_budget import ModelBudgetExceededError
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import fleet, fleet_import, fleet_report, fleet_summary
from . import service as S
from .api import _audit, _need, _read, _write, refusals
from .fleet_reports import MAX_DELIVERIES, FleetReportDraft, open_fleet_report_store
from .store import open_vehicle_store

router = APIRouter(prefix="/api/v1/vehicles/fleet", tags=["vehicles"])

IMPORT_DENIED = ("Importing a fleet is for people who manage this workspace's vehicles (workspace owners). "
                 "Ask a workspace owner to import the file.")
WRITE_DENIED = ("Saving the report's notes, writing its AI summary and sending it are for people who log this workspace's vehicles "
                "(workspace owners and members). Ask one of them.")
ATTACHMENT_KIND = "fleet_report"
LANGUAGES = ("en", "ar")


def _month(value: str | None) -> Any:
    try:
        return fleet.parse_month(value, utc_now().date())
    except ValueError as exc:
        if str(exc) == "future":
            raise S.VehicleError(422, "Choose this month or an earlier one.", "invalid_month") from None
        raise S.VehicleError(422, "The month is written as YYYY-MM, for example 2026-10.", "invalid_month") from None


def _label(month: date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


def _clean_notes(value: str) -> str:
    return "".join(ch for ch in value.replace("\r\n", "\n") if ch == "\n" or ch == "\t" or ord(ch) >= 32)


def _clean_company(value: str) -> str:
    return " ".join(value.split())


def _async_refusals(route: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(route)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await route(*args, **kwargs)
        except S.VehicleError as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code, **exc.extra})

    return wrapper


def _writer(context: TenantContext) -> None:
    _need(context, Permission.VEHICLE_WRITE, WRITE_DENIED)


@router.get("/costs", response_model=None)
@refusals
def costs(month: str | None = Query(default=None, max_length=7), context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    return fleet.load_summary(context, _month(month))


# -- the saved draft -------------------------------------------------------------------------------------------------------


def _current_summary(draft: FleetReportDraft | None, language: str, facts_print: str) -> str | None:
    """The saved AI paragraph for the language, only when it was written from these exact figures."""
    saved = (draft.summaries if draft is not None else {}).get(language)
    if not saved or saved.get("fingerprint") != facts_print:
        return None
    return str(saved.get("text") or "") or None


def _delivery_view(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: entry.get(key) for key in ("id", "channel", "to", "format", "language", "status", "approval_id", "task_id", "requested_at",
                                            "requested_by")}


def _draft_view(context: TenantContext, month: date, summary: dict[str, Any]) -> dict[str, Any]:
    """Blocking: reads the draft, the model settings and the delivery channels (call it from a sync route or a thread)."""
    with open_fleet_report_store(context) as store:
        draft = store.get(context, month)
        earlier = store.latest_before(context, month) if draft is None or not draft.company_name else None
    facts_print = fleet_summary.fingerprint(fleet_summary.facts(summary))
    summaries = {}
    for language, saved in (draft.summaries if draft else {}).items():
        summaries[language] = {"text": saved.get("text"), "created_at": saved.get("created_at"), "model": saved.get("model"),
                               "current": saved.get("fingerprint") == facts_print}
    connected = fleet_summary.model_connected(context)
    return {
        "month": _label(month),
        "company_name": (draft.company_name if draft and draft.company_name else (earlier.company_name if earlier else None)),
        "company_from": _label(earlier.month) if earlier is not None else None,
        "notes": draft.notes if draft else "",
        "saved": draft is not None,
        "version": draft.version if draft else 0,
        "updated_at": draft.updated_at.isoformat() if draft else None,
        "updated_by": draft.updated_by if draft else None,
        "summaries": summaries,
        "ai": {"available": connected, "reason": None if connected else "no_model", "empty_month": fleet_summary.empty_month(summary),
               "label": fleet_summary.LABEL},
        "channels": sorted(delivery.channels(context=context)),
        "deliveries": [_delivery_view(entry) for entry in reversed(draft.deliveries)] if draft else [],
        "can_edit": has_permission(context, Permission.VEHICLE_WRITE),
    }


def _new_draft(context: TenantContext, month: date) -> FleetReportDraft:
    now = utc_now()
    return FleetReportDraft(id=new_id("vfrep"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, month=month,
                            updated_by=context.user_id, created_at=now, updated_at=now)


def _mutate(context: TenantContext, month: date, change: Callable[[FleetReportDraft], None], *, expected: int | None = None) -> FleetReportDraft:
    """Load (or start) the month's draft, apply ``change`` and save it. With ``expected``, the caller's version must still be
    the stored one (409 ``stale`` otherwise); without, a concurrent save is retried."""
    for _attempt in range(3):
        with open_fleet_report_store(context) as store:
            draft = store.get(context, month) or _new_draft(context, month)
            if expected is not None and draft.version != expected:
                raise S.VehicleError(409, "Someone saved this month's report a moment ago. Reload to see their changes, then save again.", "stale")
            version = draft.version
            change(draft)
            draft.updated_by, draft.updated_at = context.user_id, utc_now()
            if store.save(draft, version):
                draft.version = version + 1
                return draft
        if expected is not None:
            break
    raise S.VehicleError(409, "Someone saved this month's report a moment ago. Reload, then try again.", "stale")


@router.get("/report/draft", response_model=None)
@refusals
def get_draft(month: str | None = Query(default=None, max_length=7), context: TenantContext = Depends(tenant_context)) -> Any:
    """The month's saved company name and notes (the company name of the newest earlier month when this one has none), the
    AI paragraphs and whether they are current, whether AI summaries are on, the delivery channels and the deliveries."""
    _read(context)
    first = _month(month)
    return _draft_view(context, first, fleet.load_summary(context, first))


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: str = Field(min_length=7, max_length=7)
    company_name: str = Field(default="", max_length=fleet_report.MAX_COMPANY)
    notes: str = Field(default="", max_length=fleet_report.MAX_NOTES)
    version: int = Field(ge=0, description="The version the person loaded (0 when the month had no saved draft)")

    @field_validator("company_name")
    @classmethod
    def _company(cls, value: str) -> str:
        return _clean_company(value)

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str) -> str:
        return _clean_notes(value)


@router.put("/report/draft", response_model=None)
@refusals
def save_draft(body: DraftInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """Keep the month's company name and notes between visits (``409 stale`` when someone saved in between)."""
    _read(context)
    _writer(context)
    first = _month(body.month)

    def change(draft: FleetReportDraft) -> None:
        draft.company_name = body.company_name or None
        draft.notes = body.notes

    _mutate(context, first, change, expected=body.version)
    _audit(repository, context, "vehicle.fleet_report_draft", f"workspace:{context.workspace_id}", {"month": _label(first)})
    return _draft_view(context, first, fleet.load_summary(context, first))


# -- the AI summary ----------------------------------------------------------------------------------------------------------


class SummaryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: str = Field(min_length=7, max_length=7)
    language: Literal["en", "ar"] = "en"


@router.post("/report/summary", response_model=None)
@_async_refusals
async def write_summary(body: SummaryInput, context: TenantContext = Depends(tenant_context),
                        units: UnitsOfWork = Depends(units_of_work)) -> Any:
    """Ask the workspace's model for the summary paragraph, from the computed figures only. No transaction is open while
    the model answers; every store step runs in a worker thread (docs/realtime.md#event-loop)."""
    _read(context)
    _writer(context)
    first = _month(body.month)
    summary = await asyncio.to_thread(fleet.load_summary, context, first)
    if fleet_summary.empty_month(summary):
        raise S.VehicleError(409, "This month has no costs and no kilometres, so there is nothing to summarise.", "no_data")
    if not await asyncio.to_thread(fleet_summary.model_connected, context):
        raise S.VehicleError(409, "AI summaries need this workspace's AI model. A workspace owner connects one in Settings, AI model; "
                                  "until then, write the notes yourself.", "ai_off")
    try:
        text, model, facts_print = await fleet_summary.write(context, summary, body.language)
    except ModelBudgetExceededError:
        raise S.VehicleError(409, "This workspace's monthly AI budget is used up. A workspace owner can raise it in Settings, AI usage; "
                                  "the report works without the summary.", "model_budget") from None
    except fleet_summary.SummaryWithheld:
        raise S.VehicleError(422, "The AI paragraph used a figure that is not in the report, so it was not kept. Try again, or write the notes "
                                  "yourself.", "summary_withheld") from None
    except Exception:  # the model is down, slow or refused: the report stands without the paragraph
        raise S.VehicleError(502, "The AI model did not answer. Try again in a minute; the report works without the summary.",
                             "model_failed") from None

    def save(repository: AnumRepository) -> None:
        def change(draft: FleetReportDraft) -> None:
            draft.summaries = {**draft.summaries, body.language: {"text": text, "fingerprint": facts_print, "model": model,
                                                                  "created_at": utc_now().isoformat(), "created_by": context.user_id}}

        _mutate(context, first, change)
        _audit(repository, context, "vehicle.fleet_report_summary", f"workspace:{context.workspace_id}",
               {"month": _label(first), "language": body.language})

    await run_in_unit(units, context, save)
    return await asyncio.to_thread(_draft_view, context, first, summary)


@router.delete("/report/summary", response_model=None)
@refusals
def remove_summary(month: str = Query(min_length=7, max_length=7), language: Literal["en", "ar"] = Query(default="en"),
                   context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    _writer(context)
    first = _month(month)

    def change(draft: FleetReportDraft) -> None:
        draft.summaries = {k: v for k, v in draft.summaries.items() if k != language}

    _mutate(context, first, change)
    return _draft_view(context, first, fleet.load_summary(context, first))


# -- the file --------------------------------------------------------------------------------------------------------------


class ReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: str = Field(min_length=7, max_length=7)
    language: Literal["en", "ar"] = "en"
    format: Literal["pdf", "xlsx"] = "pdf"
    company_name: str = Field(min_length=1, max_length=fleet_report.MAX_COMPANY)
    notes: str = Field(default="", max_length=fleet_report.MAX_NOTES)
    include_summary: bool = Field(default=True, description="Put the month's saved AI paragraph in the report when it is current")

    @field_validator("company_name")
    @classmethod
    def _company(cls, value: str) -> str:
        text = _clean_company(value)
        if not text:
            raise ValueError("The company name is empty.")
        return text

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str) -> str:
        return _clean_notes(value)


def _ai_for(context: TenantContext, month: date, summary: dict[str, Any], language: str) -> str | None:
    with open_fleet_report_store(context) as store:
        draft = store.get(context, month)
    return _current_summary(draft, language, fleet_summary.fingerprint(fleet_summary.facts(summary)))


def _render(snapshot: dict[str, Any], fmt: str) -> bytes:
    make = fleet_report.pdf if fmt == "pdf" else fleet_report.xlsx
    return make(snapshot["summary"], company=snapshot["company"], notes=snapshot["notes"], language=snapshot["language"],
                prepared_on=date.fromisoformat(snapshot["prepared_on"]), ai_summary=snapshot.get("ai_summary"))


@router.post("/report", response_model=None)
@refusals
def report(body: ReportInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _read(context)
    month = _month(body.month)
    summary = fleet.load_summary(context, month)
    ai = _ai_for(context, month, summary, body.language) if body.include_summary else None
    snapshot = {"summary": summary, "company": body.company_name, "notes": body.notes, "language": body.language,
                "prepared_on": utc_now().date().isoformat(), "ai_summary": ai}
    content = _render(snapshot, body.format)
    _audit(repository, context, "vehicle.fleet_report", f"workspace:{context.workspace_id}",
           {"month": summary["month"], "format": body.format, "language": body.language, "ai_summary": ai is not None})
    name = fleet_report.file_name(summary["month"], body.language, body.format)
    return Response(content=content, media_type=fleet_report.MEDIA_TYPES[body.format], headers={
        "Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


# -- sending -----------------------------------------------------------------------------------------------------------------


class SendInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: str = Field(min_length=7, max_length=7)
    language: Literal["en", "ar"] = "en"
    format: Literal["pdf", "xlsx"] = "pdf"
    channel: str = Field(pattern=r"^[a-z_]{2,30}$")
    to: str = Field(min_length=3, max_length=200)

    @field_validator("to")
    @classmethod
    def _recipient(cls, value: str) -> str:
        value = value.strip()
        email = re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", value)
        phone = re.fullmatch(r"\+?[0-9 ]{7,20}", value)
        if not (email or phone):
            raise ValueError("Enter one email address or one phone number in international format.")
        return value

    @model_validator(mode="after")
    def _fits_channel(self) -> SendInput:
        if self.channel == "email" and "@" not in self.to:
            raise ValueError("Email delivery needs one email address.")
        if self.channel == "whatsapp" and "@" in self.to:
            raise ValueError("WhatsApp delivery needs one phone number in international format, such as +971501234567.")
        return self


SUBJECT = {"en": "{company}: monthly fleet cost report, {month}", "ar": "{company}: تقرير تكاليف الأسطول الشهري، {month}"}
MESSAGE = {
    "en": "Attached is the monthly fleet cost report for {company}, {month}.",
    "ar": "مرفق تقرير تكاليف الأسطول الشهري لـ {company} عن {month}.",
}
SENT_MESSAGE = {"waiting_approval": "Sending needs an approval: approve it in Approvals and it will be sent.", "sent": "Sent."}


def _prepare(context: TenantContext, month: date, body: SendInput) -> tuple[str, str, dict[str, Any]]:
    """Freeze the report as it is now into a new delivery of the month's draft: (draft id, delivery id, snapshot)."""
    summary = fleet.load_summary(context, month)
    entry_id = new_id("vfdel")
    holder: dict[str, Any] = {}

    def change(draft: FleetReportDraft) -> None:
        if not draft.company_name:
            raise S.VehicleError(409, "Save the company name for this month's report first, then send it.", "company_required")
        snapshot = {"summary": summary, "company": draft.company_name, "notes": draft.notes, "language": body.language,
                    "prepared_on": utc_now().date().isoformat(),
                    "ai_summary": _current_summary(draft, body.language, fleet_summary.fingerprint(fleet_summary.facts(summary)))}
        entry = {"id": entry_id, "channel": body.channel, "to": body.to, "format": body.format, "language": body.language, "status": "preparing",
                 "approval_id": None, "task_id": None, "requested_at": utc_now().isoformat(), "requested_by": context.user_id, "snapshot": snapshot}
        draft.deliveries = [*draft.deliveries, entry][-MAX_DELIVERIES:]
        holder["snapshot"] = snapshot

    draft = _mutate(context, month, change)
    return draft.id, entry_id, holder["snapshot"]


def _set_status(context: TenantContext, month: date, entry_id: str, values: dict[str, Any]) -> None:
    def change(draft: FleetReportDraft) -> None:
        draft.deliveries = [{**entry, **values} if entry.get("id") == entry_id else entry for entry in draft.deliveries]

    _mutate(context, month, change)


@router.post("/report/send", response_model=None)
@_async_refusals
async def send_report(body: SendInput, context: TenantContext = Depends(tenant_context), units: UnitsOfWork = Depends(units_of_work)) -> Any:
    """Send the month's report (the saved company name, notes and current AI paragraph) through a connected email or
    WhatsApp channel. The call waits for an approval bound to the payload hash; nothing leaves before it."""
    _read(context)
    _writer(context)
    first = _month(body.month)
    draft_id, entry_id, snapshot = await asyncio.to_thread(_prepare, context, first, body)
    reference = await asyncio.to_thread(delivery.attachment_reference, ATTACHMENT_KIND, f"{draft_id}:{entry_id}", body.format, context)
    month_text = fleet_report.month_name(_label(first), body.language)
    words = {"company": snapshot["company"], "month": month_text}
    try:
        outcome = await delivery.request_delivery(
            context, units, channel=body.channel, to=body.to, subject=SUBJECT[body.language].format(**words),
            text=MESSAGE[body.language].format(**words), attachments=[reference], title=f"Send fleet report: {snapshot['company']} ({_label(first)})",
        )
    except delivery.DeliveryUnavailableError as exc:
        await asyncio.to_thread(_set_status, context, first, entry_id, {"status": "unavailable"})
        raise S.VehicleError(409, str(exc), "channel_unavailable") from None

    def remember(repository: AnumRepository) -> None:
        _set_status(context, first, entry_id, {"status": outcome.status, "approval_id": outcome.approval_id, "task_id": outcome.task_id})
        _audit(repository, context, "vehicle.fleet_report_delivery", f"workspace:{context.workspace_id}",
               {"month": _label(first), "channel": body.channel, "format": body.format, "language": body.language, "status": outcome.status,
                "task_id": outcome.task_id})

    await run_in_unit(units, context, remember)
    view = await asyncio.to_thread(_draft_view, context, first, snapshot["summary"])
    return {"status": outcome.status, "approval_id": outcome.approval_id, "payload_hash": outcome.payload_hash, "task_id": outcome.task_id,
            "message": SENT_MESSAGE.get(outcome.status, outcome.summary or "Not sent."), "draft": view}


def _resolve_attachment(context: TenantContext, item_id: str, fmt: str) -> tuple[str, str, bytes] | None:
    """The file of one delivery, rendered from its frozen content (so it is what was approved)."""
    draft_id, _, entry_id = item_id.partition(":")
    if fmt not in fleet_report.MEDIA_TYPES or not entry_id:
        return None
    with open_fleet_report_store(context) as store:
        draft = store.get_by_id(context, draft_id)
    entry = next((e for e in (draft.deliveries if draft else []) if e.get("id") == entry_id), None)
    if entry is None or not entry.get("snapshot"):
        return None
    snapshot = entry["snapshot"]
    name = fleet_report.file_name(snapshot["summary"]["month"], snapshot["language"], fmt)  # type: ignore[arg-type]
    return name, fleet_report.MEDIA_TYPES[fmt], _render(snapshot, fmt)


delivery.register_attachment_resolver(ATTACHMENT_KIND, _resolve_attachment)


# -- import ------------------------------------------------------------------------------------------------------------------


def _manage(context: TenantContext) -> None:
    _write(context)
    _need(context, Permission.VEHICLE_MANAGE, IMPORT_DENIED)


@router.post("/import/preview", response_model=fleet_import.FleetPreview)
@refusals
def import_preview(payload: fleet_import.FleetImportFile, context: TenantContext = Depends(tenant_context)) -> Any:
    """What importing this file would do, row by row. Nothing is written."""
    _manage(context)
    with open_vehicle_store(context) as store:
        return fleet_import.preview(store, context, payload)


@router.post("/import/apply", response_model=fleet_import.FleetImportResult)
@refusals
def import_apply(payload: fleet_import.FleetImportApply, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> Any:
    """Write every row that is not skipped, in one unit of work, or nothing."""
    _manage(context)
    with open_vehicle_store(context) as store:
        result = fleet_import.apply(store, context, payload)
    _audit(repository, context, "vehicle.fleet_import", f"workspace:{context.workspace_id}", result.model_dump())
    return result


__all__ = ["router"]
