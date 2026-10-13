"""Tyre planning operations shared by the HTTP API, scheduled reports and agent tools.

Every function takes the caller's :class:`TenantContext`; permission checks happen in
the callers (HTTP routes check the role, agent tools check ``tyre:read``, scheduled
reports run as the schedule's creator after their membership is re-checked).
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..schemas import TenantContext, new_id, utc_now
from ..secret_box import SecretDecryptionError, secret_cipher
from . import sources as source_module
from .analytics import evaluate_alerts, forecast_tyres
from .mapping import SourceTable, apply_mapping, mapped_columns, suggest_mapping
from .model import Entity, EntityStats, SourceKind, SyncStats, TyreDataset, TyreMapping
from .reports import TITLES, build_report, summary_text
from .sources import TyreSourceError
from .store import (
    ConnectionStatus,
    ReportKind,
    ReportSource,
    TyreAlert,
    TyreConnection,
    TyreReport,
    TyreSnapshot,
    open_tyre_store,
    reconcile_alerts,
)

logger = logging.getLogger("anum.tyre")


class NotConnectedError(LookupError):
    """The workspace has no tyre data connection yet."""


class NotSyncedError(LookupError):
    """The workspace is connected but has no synced data yet."""


def secret_hint(kind: SourceKind, secret: str) -> str:
    """What the UI may show about a stored secret: never the secret itself."""
    if kind == SourceKind.SUPABASE_POSTGRES:
        try:
            target = source_module.parse_postgres_url(secret)
        except TyreSourceError:
            return "connection string"
        return f"{target.user}@{target.host}"
    if kind == SourceKind.SUPABASE_AUTH:
        try:
            login = source_module.parse_reader_login(secret)
        except TyreSourceError:
            return "reader sign-in"
        return f"{login.email} · key …{login.api_key[-4:]}"
    if kind == SourceKind.FIRESTORE:
        try:
            return source_module.parse_service_account(secret).client_email
        except TyreSourceError:
            return "service account"
    tail = secret.strip()[-4:]
    return f"…{tail}" if len(secret.strip()) > 12 else "key saved"


def encrypt_secret(secret: str) -> str:
    return secret_cipher().encrypt(secret)


def open_connection_source(connection: TyreConnection) -> source_module.TyreSource:
    if not connection.secret_ciphertext:
        raise TyreSourceError("No credential is saved for this connection.")
    try:
        secret = secret_cipher().decrypt(connection.secret_ciphertext)
    except SecretDecryptionError:
        raise TyreSourceError(
            "The saved credential could not be decrypted (the server's secrets key changed). Enter it again."
        ) from None
    endpoint = connection.endpoint
    if connection.kind == SourceKind.SUPABASE_POSTGRES:
        endpoint = ""
    return source_module.open_source(connection.kind, endpoint=endpoint, secret=secret, schema=connection.schema_name)


def get_connection(context: TenantContext) -> TyreConnection:
    with open_tyre_store(context) as store:
        connection = store.get_connection(context)
    if connection is None:
        raise NotConnectedError("Connect your tyre data first.")
    return connection


def save_connection(connection: TyreConnection) -> TyreConnection:
    context = TenantContext(tenant_id=connection.tenant_id, workspace_id=connection.workspace_id,
                            user_id=connection.updated_by, roles=[])
    with open_tyre_store(context) as store:
        return store.save_connection(connection)


async def test_connection(context: TenantContext) -> tuple[TyreConnection, str]:
    """Try the saved credential; the connection records the outcome either way."""
    connection = get_connection(context)
    try:
        message = await open_connection_source(connection).test()
        connection.status, connection.last_error = ConnectionStatus.OK, None
    except TyreSourceError as exc:
        message = exc.message
        connection.status, connection.last_error = ConnectionStatus.FAILED, exc.message
    connection.last_tested_at = utc_now()
    save_connection(connection)
    return connection, message


@dataclass
class DiscoveryResult:
    tables: list[SourceTable]
    suggestion: TyreMapping
    discovered_at: datetime


async def discover(context: TenantContext) -> DiscoveryResult:
    connection = get_connection(context)
    try:
        tables = await open_connection_source(connection).discover()
    except TyreSourceError as exc:
        connection.status, connection.last_error, connection.last_tested_at = ConnectionStatus.FAILED, exc.message, utc_now()
        save_connection(connection)
        raise
    now = utc_now()
    connection.discovery, connection.discovered_at = tables, now
    connection.status, connection.last_error, connection.last_tested_at = ConnectionStatus.OK, None, now
    save_connection(connection)
    return DiscoveryResult(tables=tables, suggestion=suggest_mapping(tables), discovered_at=now)


async def read_source(
    connection: TyreConnection, mapping: TyreMapping, *, sample: int | None = None
) -> tuple[dict[Entity, list[dict[str, Any]]], dict[str, EntityStats]]:
    """Fetch each mapped table once (its mapped columns) and hand rows to its entities."""
    source = open_connection_source(connection)
    by_table: dict[str, list[dict[str, Any]]] = {}
    cut: dict[str, bool] = {}
    for table, columns in mapped_columns(mapping).items():
        try:
            result = await source.fetch(table, columns)
        except TyreSourceError as exc:
            raise TyreSourceError(f"Reading {table} failed: {exc.message}", hint=exc.hint) from None
        by_table[table] = result.rows[:sample] if sample else result.rows
        cut[table] = result.cut
    rows: dict[Entity, list[dict[str, Any]]] = {}
    stats: dict[str, EntityStats] = {}
    for entity, entity_mapping in mapping.entities.items():
        rows[entity] = by_table.get(entity_mapping.source, [])
        stats[entity.value] = EntityStats(read=len(rows[entity]), cut=cut.get(entity_mapping.source, False))
    return rows, stats


async def preview(context: TenantContext, mapping: TyreMapping, rows_per_entity: int = 20) -> dict[str, Any]:
    """Apply a draft mapping to a small sample, without saving anything."""
    connection = get_connection(context)
    rows, stats = await read_source(connection, mapping, sample=rows_per_entity)
    dataset, stats = apply_mapping(mapping, rows, stats=stats)
    return {
        "problems": mapping.problems(),
        "stats": {key: value.model_dump() for key, value in stats.items()},
        "sample": {entity.value: [item.model_dump(mode="json") for item in getattr(dataset, entity.value)[:5]]
                   for entity in Entity},
    }


@dataclass
class SyncOutcome:
    stats: SyncStats
    counts: dict[str, int]
    raised: list[TyreAlert] = field(default_factory=list)


async def sync(context: TenantContext, actor: str) -> SyncOutcome:
    connection = get_connection(context)
    mapping = connection.mapping
    if mapping is None or mapping.problems():
        problems = mapping.problems() if mapping else ["Save a mapping first."]
        raise TyreSourceError("The mapping is not complete: " + " ".join(problems))
    started = time.monotonic()
    try:
        rows, stats = await read_source(connection, mapping)
    except TyreSourceError as exc:
        connection.status, connection.last_error, connection.last_tested_at = ConnectionStatus.FAILED, exc.message, utc_now()
        save_connection(connection)
        raise
    dataset, stats = apply_mapping(mapping, rows, stats=stats)
    now = utc_now()
    sync_stats = SyncStats(synced_at=now, entities=stats, duration_ms=int((time.monotonic() - started) * 1000))
    snapshot = TyreSnapshot(tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                            dataset=dataset, stats=sync_stats, synced_by=actor)
    candidates = evaluate_alerts(dataset, connection.assumptions, now.date())
    with open_tyre_store(context) as store:
        store.save_snapshot(snapshot)
        raised = reconcile_alerts(store, context, candidates, now)
        current = store.get_connection(context)
        if current is not None:
            current.status, current.last_error, current.last_tested_at = ConnectionStatus.OK, None, now
            store.save_connection(current)
    announce(context, raised)
    counts = {entity.value: len(getattr(dataset, entity.value)) for entity in Entity}
    return SyncOutcome(stats=sync_stats, counts=counts, raised=raised)


@dataclass
class Loaded:
    connection: TyreConnection
    snapshot: TyreSnapshot


def load(context: TenantContext) -> Loaded:
    with open_tyre_store(context) as store:
        connection = store.get_connection(context)
        snapshot = store.get_snapshot(context) if connection else None
    if connection is None:
        raise NotConnectedError("Connect your tyre data first.")
    if snapshot is None:
        raise NotSyncedError("Sync your tyre data first.")
    return Loaded(connection=connection, snapshot=snapshot)


def announce(context: TenantContext, raised: list[TyreAlert]) -> None:
    """Record one ``tyre_alerts.raised`` event for new critical or warning alerts.

    Committed events reach phone push (docs/notifications.md) like any other: members
    whose ``tyre_alerts`` preference is on get one push per workspace (it replaces the
    previous one). The event carries counts only, never tyre data.
    """
    counts = {"critical": 0, "warning": 0}
    for alert in raised:
        if alert.severity.value in counts:
            counts[alert.severity.value] += 1
    if not any(counts.values()):
        return
    from ..dependencies import _unit_of_work
    from ..events import CanonicalEventName, create_event

    with _unit_of_work(context, require_membership=False) as repository:
        repository.record_event(
            create_event(CanonicalEventName.TYRE_ALERTS_RAISED, context, "tyre_alerts", counts,
                         correlation_id=new_id("tyre_sync"), created_at=utc_now()).event
        )


def reevaluate_alerts(context: TenantContext, dataset: TyreDataset, connection: TyreConnection) -> list[TyreAlert]:
    """After the assumptions change: the same data, new thresholds."""
    now = utc_now()
    candidates = evaluate_alerts(dataset, connection.assumptions, now.date())
    with open_tyre_store(context) as store:
        raised = reconcile_alerts(store, context, candidates, now)
    announce(context, raised)
    return raised


def generate_report(
    context: TenantContext,
    kind: ReportKind,
    *,
    period_from: date | None = None,
    period_to: date | None = None,
    source: ReportSource = ReportSource.MANUAL,
    schedule_id: str | None = None,
    idempotency_key: str | None = None,
) -> TyreReport:
    if period_from and period_to and period_from > period_to:
        raise ValueError("The period starts after it ends.")
    loaded = load(context)
    with open_tyre_store(context) as store:
        if idempotency_key:
            existing = store.find_report(context, idempotency_key)
            if existing is not None:
                return existing
        counts = store.alert_counts(context)
    now = utc_now()
    content = build_report(
        kind, loaded.snapshot.dataset, loaded.connection.assumptions, as_of=now.date(),
        period_from=period_from, period_to=period_to, data_as_of=loaded.snapshot.stats.synced_at,
        open_alerts=counts,
    )
    report = TyreReport(
        id=new_id("tyre_report"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        kind=kind,
        title=TITLES[kind],
        period_from=date.fromisoformat(content["period_from"]),
        period_to=date.fromisoformat(content["period_to"]),
        data_as_of=loaded.snapshot.stats.synced_at,
        created_by=context.user_id,
        created_at=now,
        source=source,
        schedule_id=schedule_id,
        idempotency_key=idempotency_key,
        content=content,
    )
    with open_tyre_store(context) as store:
        store.save_report(report)
    share_report(context, report)
    return report


def shared_report_payload(report: TyreReport) -> Any:
    """The tyre report as a shared report (docs/reports.md): numbers as metrics and as a
    metrics section (tiles in the app), the first table as the data snapshot, notes as
    highlights. The tyre report itself (exports, deliveries) stays in ``tyre_reports``."""
    from ..reports import MetricsSection, ReportCreate, ReportMetric, ReportTable, SectionMetric

    content = report.content
    highlights = [h for h in content.get("highlights", []) if h.get("value") is not None and str(h.get("label", "")).strip()]
    metrics = [ReportMetric(label=str(h["label"])[:80], value=h["value"]) for h in highlights[:24]]
    numeric = [h for h in highlights if isinstance(h["value"], int | float) and not isinstance(h["value"], bool)]
    sections = (
        [MetricsSection(id="headline", items=[
            SectionMetric(label=str(h["label"])[:80], value=float(h["value"]),
                          decimals=0 if float(h["value"]).is_integer() else 2)
            for h in numeric[:12]
        ])]
        if numeric else []
    )
    table: dict[str, Any] = {"columns": [], "rows": []}
    for candidate in content.get("tables", []):
        columns = [str(c)[:120] for c in candidate.get("columns", [])][:40]
        if columns:
            rows = [
                [cell if cell is None or isinstance(cell, str | int | float) and not isinstance(cell, bool) else str(cell)
                 for cell in row[: len(columns)]] + [None] * max(0, len(columns) - len(row))
                for row in candidate.get("rows", [])[:5000]
            ]
            table = {"columns": columns, "rows": rows}
            break
    return ReportCreate(
        type="tyre",
        title=report.title,
        period_start=report.period_from,
        period_end=report.period_to,
        summary=summary_text(content)[:4000],
        metrics=metrics,
        highlights=[str(note) for note in content.get("notes", [])][:20],
        data=ReportTable.model_validate(table),
        sections=sections,
    )


def share_report(context: TenantContext, report: TyreReport) -> None:
    """Hands a new tyre report to the shared reports module, best effort: a failure there
    is logged and never fails the tyre report."""
    from ..reports import CreatedByKind, create_report

    kind = CreatedByKind.AUTOMATION if report.source == ReportSource.SCHEDULE else CreatedByKind.USER
    try:
        create_report(context, shared_report_payload(report), kind=kind)
    except Exception:  # noqa: BLE001 - the tyre report is already stored
        logger.warning("Tyre report %s was not copied to the shared reports", report.id, exc_info=True)


def forecasts_for(context: TenantContext) -> tuple[Loaded, list]:  # type: ignore[type-arg]
    loaded = load(context)
    return loaded, forecast_tyres(loaded.snapshot.dataset, loaded.connection.assumptions, utc_now().date())


def group_counts(items: list[Any], key: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[str(getattr(item, key))] += 1
    return dict(counts)
