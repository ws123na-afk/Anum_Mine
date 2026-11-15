"""HTTP routes for running a client fleet: import, fleet costs and the monthly report (docs/vehicles.md#fleet).

* ``GET /api/v1/vehicles/fleet/costs?month=YYYY-MM`` (``vehicle:read``): the fleet cost summary of the workspace's
  business vehicles (``fleet.py``).
* ``POST /api/v1/vehicles/fleet/report`` (``vehicle:read``): the monthly client report as a PDF or Excel file, in
  English or Arabic, made for the request and never stored or sent (``fleet_report.py``). Audited with the month,
  format and language only.
* ``POST /api/v1/vehicles/fleet/import/preview`` and ``/apply`` (``vehicle:write`` and ``vehicle:manage``, workspace
  owners): a client's vehicles, fuel, service records or odometer readings from a CSV or Excel file
  (``fleet_import.py``). Apply is all or nothing and audited with the kind and counts only.

Refusals answer ``{"detail": "<plain words>", "code": "<code>"}`` like the rest of Vehicles.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..authorization import Permission
from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, utc_now
from . import fleet, fleet_import, fleet_report
from . import service as S
from .api import _audit, _need, _read, _write, refusals
from .store import open_vehicle_store

router = APIRouter(prefix="/api/v1/vehicles/fleet", tags=["vehicles"])

IMPORT_DENIED = ("Importing a fleet is for people who manage this workspace's vehicles (workspace owners). "
                 "Ask a workspace owner to import the file.")


def _month(value: str | None) -> Any:
    try:
        return fleet.parse_month(value, utc_now().date())
    except ValueError as exc:
        if str(exc) == "future":
            raise S.VehicleError(422, "Choose this month or an earlier one.", "invalid_month") from None
        raise S.VehicleError(422, "The month is written as YYYY-MM, for example 2026-10.", "invalid_month") from None


@router.get("/costs", response_model=None)
@refusals
def costs(month: str | None = Query(default=None, max_length=7), context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    return fleet.load_summary(context, _month(month))


class ReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: str = Field(min_length=7, max_length=7)
    language: Literal["en", "ar"] = "en"
    format: Literal["pdf", "xlsx"] = "pdf"
    company_name: str = Field(min_length=1, max_length=fleet_report.MAX_COMPANY)
    notes: str = Field(default="", max_length=fleet_report.MAX_NOTES)

    @field_validator("company_name")
    @classmethod
    def _company(cls, value: str) -> str:
        text = " ".join(value.split())
        if not text:
            raise ValueError("The company name is empty.")
        return text

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str) -> str:
        return "".join(ch for ch in value.replace("\r\n", "\n") if ch == "\n" or ch == "\t" or ord(ch) >= 32)


@router.post("/report", response_model=None)
@refusals
def report(body: ReportInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _read(context)
    month = _month(body.month)
    summary = fleet.load_summary(context, month)
    make = fleet_report.pdf if body.format == "pdf" else fleet_report.xlsx
    content = make(summary, company=body.company_name, notes=body.notes, language=body.language, prepared_on=utc_now().date())
    _audit(repository, context, "vehicle.fleet_report", f"workspace:{context.workspace_id}",
           {"month": summary["month"], "format": body.format, "language": body.language})
    name = fleet_report.file_name(summary["month"], body.language, body.format)
    return Response(content=content, media_type=fleet_report.MEDIA_TYPES[body.format], headers={
        "Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


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
