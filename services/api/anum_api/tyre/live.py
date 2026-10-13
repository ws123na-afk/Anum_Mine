"""Bounded, on-demand reads of the large TyrePulse tables (docs/tyre-planning.md#fleet-operations-data).

A tool asks for one work order, one vehicle or one period; ANUM reads at most
``MAX_LIVE_ROWS`` rows of the listed columns through the workspace's read-only
connection, answers, and keeps nothing. The caller has already checked ``tyre:read``;
the connection is the workspace's own (tenant-scoped), so another workspace's source is
never reached.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from ..schemas import TenantContext
from . import service
from .live_tables import LIVE_TABLES, LiveTable
from .sources import ReadQuery, TyreSourceError

MAX_LIVE_ROWS = 10_000


@dataclass(frozen=True)
class LiveResult:
    table: LiveTable
    rows: list[dict[str, Any]]
    cut: bool


def available(context: TenantContext, table: str) -> bool:
    """The table was found when the owner last discovered the source (or discovery is unknown)."""
    try:
        connection = service.get_connection(context)
    except service.NotConnectedError:
        return False
    if connection.discovery is None:
        return True
    return any(found.name.split(".")[-1] == table for found in connection.discovery)


async def read(
    context: TenantContext, table: str, *, vehicle_id: str | None = None, work_order: str | None = None,
    site: str | None = None, date_from: date | None = None, date_to: date | None = None, limit: int = 200,
) -> LiveResult:
    spec = LIVE_TABLES.get(table)
    if spec is None:
        raise TyreSourceError("ANUM does not read that table on demand.")
    if not (vehicle_id or work_order or date_from):
        # Never the whole table: one vehicle, one work order or a period.
        raise TyreSourceError("Name a vehicle, a work order or a period to read.")
    filters: list[tuple[str, str, str]] = []
    if vehicle_id:
        filters.append((spec.vehicle_column, "eq", vehicle_id))
    if work_order:
        filters.append((spec.work_order_column, "eq", work_order))
    if site:
        filters.append((spec.site_column, "eq", site))
    if date_from:
        filters.append((spec.date_column, "gte", date_from.isoformat()))
    if date_to:
        filters.append((spec.date_column, "lte", date_to.isoformat()))
    connection = service.get_connection(context)
    source = service.open_connection_source(connection)
    query = ReadQuery(filters=tuple(filters), order=(spec.date_column, True), limit=max(1, min(limit, MAX_LIVE_ROWS)))
    try:
        result = await source.fetch(spec.name, list(spec.columns), query)
    except TyreSourceError as exc:
        raise TyreSourceError(f"Reading {spec.name} failed: {exc.message}", hint=exc.hint) from None
    return LiveResult(table=spec, rows=result.rows, cut=result.cut)
