"""One sync of one finance connection, and the background loop (docs/finance.md#sync).

A sync never holds a database transaction across the network call: it reads the connection
in one short unit of work, fetches with none open, then writes the results and the
connection's health in a second unit of work, all in the person's own tenant, workspace
and user scope (row policies on ``anum.user_id``).

The background loop (``ANUM_FINANCE_SYNC_ENABLED=true``) follows the commerce sync and the
cross-tenant maintenance pattern (``anum_api.maintenance``): due connections are found as
``anum_maintenance``, which may read only the id, scope, owner, ``enabled``, ``status`` and
``next_sync_at`` of due connections (migration 0057), never credentials or data. Each one
is claimed with a compare-and-set update of ``next_sync_at`` and synced as the application
role in its owner's scope.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from ..schemas import TenantContext, new_id, utc_now
from ..secret_box import SecretDecryptionError
from ..settings import settings
from . import csv_import
from .categories import categorize
from .config import finance_config
from .connectors import (
    ADAPTERS,
    PROVIDERS,
    Connection,
    ConnectionStatus,
    ConnectorError,
    FetchResult,
    decrypt_credentials,
)
from .models import FxRate, Transaction
from .store import FinanceStore, finance_store, open_finance_store
from .summary import settings_for, today_in

logger = logging.getLogger("anum.finance")
CLAIM_FOR = timedelta(minutes=15)


class SyncReport(BaseModel):
    connection_id: str
    status: str  # ok, error, skipped
    message: str
    counts: dict[str, int] = Field(default_factory=dict)


def import_transactions(store: FinanceStore, context: TenantContext, connection: Connection, result: FetchResult) -> dict[str, int]:
    """Provider transactions into the ledger: mapped accounts only, deduplicated, cleaned."""
    counts = {"transactions_new": 0, "transactions_duplicate": 0, "transactions_unmapped": 0}
    if not result.transactions:
        return counts
    mapping = connection.settings.get("accounts") or {}
    rules = settings_for(store, context).category_rules
    now = utc_now()
    rows: list[Transaction] = []
    for remote in result.transactions:
        account = store.get_account(context, str(mapping.get(remote.account_ref) or "")) if mapping.get(remote.account_ref) else None
        if account is None or remote.currency != account.currency:
            counts["transactions_unmapped"] += 1
            continue
        description = csv_import.clean_text(remote.description, 500) or "Transaction"
        category, source = categorize(description, remote.amount, rules)
        rows.append(Transaction(
            id=new_id("fin_txn"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            account_id=account.id, posted_on=remote.posted_on, amount=remote.amount, currency=account.currency,
            description=description, category=category, category_source=source, import_id=None,
            dedupe_hash=csv_import.dedupe_hash(account.id, remote.posted_on, remote.amount, account.currency,
                                               f"{connection.provider}:{remote.external_id}", 0),
            created_at=now, updated_at=now,
        ))
    added = store.add_transactions(rows) if rows else 0
    counts["transactions_new"] = added
    counts["transactions_duplicate"] = len(rows) - added
    return counts


def _save_rates(store: FinanceStore, context: TenantContext, connection: Connection, result: FetchResult) -> dict[str, int]:
    now = utc_now()
    rates = [
        FxRate(
            id=new_id("fin_fx"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            base=r.base, quote=r.quote, rate=r.rate, as_of=r.as_of, source=r.source[:120], source_url=r.source_url,
            connection_id=connection.id, fetched_at=now,
        )
        for r in result.rates
    ]
    added = store.save_fx_rates(rates) if rates else 0
    return {"rates_new": added, "rates_seen": len(rates)}


async def sync_connection(context: TenantContext, connection_id: str) -> SyncReport:
    """Fetch and store one connection's data. Errors are recorded on the connection."""

    def load() -> tuple[Connection | None, Any]:
        with open_finance_store(context) as store:
            return store.get_connection(context, connection_id), today_in(settings_for(store, context))

    connection, today = await asyncio.to_thread(load)
    if connection is None:
        return SyncReport(connection_id=connection_id, status="skipped", message="Connection not found.")
    if connection.status == ConnectionStatus.DISCONNECTED or not connection.enabled:
        return SyncReport(connection_id=connection_id, status="skipped", message="The connection is disconnected.")
    adapter = ADAPTERS.get(connection.provider)
    spec = PROVIDERS.get(connection.provider)
    error: str | None = None
    result = FetchResult()
    if adapter is None or spec is None:
        error = "This source has no adapter yet, so it cannot sync."
    else:
        try:
            credentials = decrypt_credentials(connection.credentials_encrypted)
            result = await adapter(connection, credentials, today)
        except SecretDecryptionError:
            error = "The saved credentials could not be read (was ANUM_SECRETS_KEY changed?). Disconnect and connect again."
        except ConnectorError as exc:
            error = exc.message
        except Exception:  # noqa: BLE001 - any adapter failure is the same for the person
            logger.exception("finance_sync_failed", extra={"anum_finance": {"connection_id": connection_id, "provider": connection.provider}})
            error = "The sync failed. ANUM will try again later."

    def save() -> SyncReport:
        now = utc_now()
        with open_finance_store(context) as store:
            current = store.get_connection(context, connection_id)
            if current is None:
                return SyncReport(connection_id=connection_id, status="skipped", message="Connection not found.")
            counts: dict[str, int] = {}
            if error is None:
                counts = {**_save_rates(store, context, current, result), **import_transactions(store, context, current, result)}
                current.status, current.last_error, current.last_success_at = ConnectionStatus.ACTIVE, None, now
                hours = spec.sync_hours if spec else 12
                current.next_sync_at = now + timedelta(hours=hours)
            else:
                current.status, current.last_error = ConnectionStatus.ERROR, error[:500]
                current.next_sync_at = now + timedelta(minutes=finance_config.retry_minutes)
            current.last_sync_at, current.last_counts, current.updated_at = now, counts, now
            store.save_connection(current)
        if error is not None:
            return SyncReport(connection_id=connection_id, status="error", message=error)
        return SyncReport(connection_id=connection_id, status="ok", message="Synced.", counts=counts)

    return await asyncio.to_thread(save)


# -- the background loop ---------------------------------------------------------------------------

_DUE = text(
    "select tenant_id, workspace_id, owner_user_id, id from finance_connections "
    "where enabled and status <> 'disconnected' and next_sync_at <= now() order by next_sync_at limit :limit"
)


def due_connections(limit: int) -> list[tuple[str, str, str, str]]:
    if settings.repository_backend == "memory":
        return finance_store.due_connections(utc_now(), limit)
    from ..maintenance import default_session_factory, discover

    return [(row[0], row[1], row[2], row[3]) for row in discover(default_session_factory, _DUE, {"limit": limit})]


async def run_once(limit: int | None = None) -> int:
    """Sync every due connection once, each in its owner's scope; returns how many synced."""
    synced = 0
    for tenant_id, workspace_id, owner, connection_id in await asyncio.to_thread(due_connections, limit or finance_config.sync_batch_size):
        context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=owner, roles=[])
        now = utc_now()

        def claim(context: TenantContext = context, connection_id: str = connection_id) -> bool:
            with open_finance_store(context) as store:
                return store.claim_connection(context, connection_id, now, now + CLAIM_FOR)

        try:
            if not await asyncio.to_thread(claim):
                continue
            await sync_connection(context, connection_id)
            synced += 1
        except Exception:
            logger.exception("finance_scheduled_sync_failed", extra={"anum_finance": {"connection_id": connection_id}})
    return synced


class FinanceSyncScheduler:
    def __init__(self, poll_interval: float) -> None:
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    def start(self) -> None:
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="anum-finance-sync")

    async def stop(self, timeout: float = 30.0) -> None:
        task, self._task = self._task, None
        if self._stop is not None:
            self._stop.set()
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except Exception:
                logger.exception("Finance sync scheduler stopped with an error")

    async def _run(self) -> None:
        stop = self._stop
        if stop is None:
            raise RuntimeError("Finance sync scheduler started without a stop event")
        while not stop.is_set():
            try:
                await run_once()
            except Exception:
                logger.exception("Finance sync pass failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass


def build_finance_scheduler() -> FinanceSyncScheduler | None:
    if not finance_config.sync_enabled:
        return None
    return FinanceSyncScheduler(finance_config.sync_poll_seconds)
