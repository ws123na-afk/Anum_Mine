"""The daily job of the business books (docs/business-books.md#daily-job): automatic exchange rates and recurring bills.

Run by the daily retention job (``python -m anum_api.voice_retention``, reported under ``business_books``)
and testable on its own. Businesses are discovered as ``anum_maintenance``, which may read only the
scope, switch and schedule columns of enabled rate feeds and active recurring bills (migration
``0138_books_v2``); the work then runs as the application role inside each business's own tenant and
workspace RLS context. Nothing bypasses row level security. The output is counts only.

* **Rates:** the ECB file is read once per run, and only when at least one business opted in. A
  failed read is recorded on every feed (the rates stay as they were).
* **Recurring bills:** each due template makes its bills under a lock on its row; one bill per
  template and period (``books_recurring.materialize``).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from ..schemas import TenantContext, utc_now
from ..settings import settings
from . import books_rates_auto as auto
from .books_recurring import SYSTEM_ACTOR, materialize
from .books_store import After, AtMost, books_memory_store, open_books_store

PAGE = 200  # due recurring bills read per page

logger = logging.getLogger("anum_api.business.books_jobs")

_DISCOVER_FEEDS = text("select tenant_id, workspace_id from business_book_rate_feeds where enabled order by 1, 2")
_DISCOVER_BOOKS = text("select tenant_id, workspace_id from business_book_settings order by 1, 2")
_DISCOVER_TEMPLATES = text(
    "select distinct tenant_id, workspace_id from business_book_bill_templates "
    "where status = 'active' and next_date is not null and next_date <= :today order by 1, 2"
)


@dataclass
class BooksJobResult:
    dry_run: bool = False
    feeds: int = 0
    rates_added: int = 0
    fetch_failed: bool = False
    businesses: int = 0
    bills_made: int = 0
    failed: int = 0
    # The posting run (docs/business-books.md#the-journal): every business with books, after its rates and recurring bills.
    ledgers: int = 0
    entries_posted: int = 0
    entries_reversed: int = 0
    not_posted: int = 0


def _context(tenant_id: str, workspace_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=SYSTEM_ACTOR, roles=[])


def _archived(context: TenantContext) -> bool:
    from ..dependencies import workspace_archived

    return bool(workspace_archived(context))


def discover_feeds() -> list[tuple[str, str]]:
    if settings.repository_backend == "memory":
        return sorted({(f.tenant_id, f.workspace_id) for f in books_memory_store.rows["rate_feeds"].values() if f.enabled})  # type: ignore[attr-defined]
    from ..maintenance import default_session_factory, discover

    return [(row[0], row[1]) for row in discover(default_session_factory, _DISCOVER_FEEDS)]


def discover_templates(today: date) -> list[tuple[str, str]]:
    if settings.repository_backend == "memory":
        return sorted({(t.tenant_id, t.workspace_id) for t in books_memory_store.rows["templates"].values()  # type: ignore[attr-defined]
                       if t.status == "active" and t.next_date is not None and t.next_date <= today})
    from ..maintenance import default_session_factory, discover

    return [(row[0], row[1]) for row in discover(default_session_factory, _DISCOVER_TEMPLATES, {"today": today})]


def discover_books() -> list[tuple[str, str]]:
    """Every business with book settings (``anum_maintenance`` reads only their scope columns, migration 0148)."""
    if settings.repository_backend == "memory":
        return sorted(books_memory_store.settings)
    from ..maintenance import default_session_factory, discover

    return [(row[0], row[1]) for row in discover(default_session_factory, _DISCOVER_BOOKS)]


def run_ledgers(result: BooksJobResult, *, now: datetime, dry_run: bool) -> None:
    """Post every business's source documents to its journal: the daily run and the backfill of businesses that had books before
    the journal existed. Re-runnable: an event is posted once; what cannot be posted is counted (``not_posted``) with its reason
    kept in the business's posting issues."""
    from .books_ledger import post

    for tenant_id, workspace_id in discover_books():
        context = _context(tenant_id, workspace_id)
        try:
            if _archived(context):
                continue
            outcome = post(context, actor=SYSTEM_ACTOR, now=now, dry_run=dry_run)
            result.ledgers += 1
            result.entries_posted += outcome.posted
            result.entries_reversed += outcome.reversed
            result.not_posted += outcome.issues
        except Exception as exc:  # noqa: BLE001 - one business's failure never stops the run
            logger.warning("books_ledger_business_failed error_class=%s", type(exc).__name__)
            result.failed += 1


def refresh_feed(context: TenantContext, published: auto.Published | None, error: str | None, *, now: datetime, actor: str) -> int:
    """Apply one read of the ECB file (or its failure) to one business's feed. Returns the rates added."""
    with open_books_store(context) as store:
        feed = store.first("rate_feeds")
        if feed is None or not feed.enabled:
            return 0
        if published is None:
            auto.record_failure(store, feed, error or "The rates could not be read.", now)
            return 0
        settings_row = store.get_settings()
        return auto.apply(context, store, feed, published, base_currency=settings_row.base_currency if settings_row else None, now=now, actor=actor)


def run_rates(result: BooksJobResult, *, now: datetime, dry_run: bool) -> None:
    feeds = discover_feeds()
    result.feeds = len(feeds)
    if not feeds or dry_run:
        return
    published, error = None, None
    try:
        published = auto.fetch(now.date())
    except auto.FeedError as exc:
        error = str(exc)
        result.fetch_failed = True
        logger.warning("books_rates_fetch_failed")
    for tenant_id, workspace_id in feeds:
        context = _context(tenant_id, workspace_id)
        try:
            if _archived(context):
                continue
            result.rates_added += refresh_feed(context, published, error, now=now, actor=SYSTEM_ACTOR)
        except Exception as exc:  # noqa: BLE001 - one business's failure never stops the run
            logger.warning("books_rates_business_failed error_class=%s", type(exc).__name__)
            result.failed += 1


def run_templates(result: BooksJobResult, *, now: datetime, dry_run: bool) -> None:
    today = now.date()
    for tenant_id, workspace_id in discover_templates(today):
        context = _context(tenant_id, workspace_id)
        try:
            if _archived(context):
                continue
            result.businesses += 1
            # Every due template, in pages keyed by id (a page never starves the ones after it).
            last = ""
            while True:
                with open_books_store(context) as store:
                    page, _ = store.find("templates", where={"status": "active", "next_date": AtMost(today), "id": After(last)}, order_by="id",
                                         descending=False, limit=PAGE)
                for template in page:
                    if dry_run:
                        continue
                    # One short unit of work per template: its row is locked only while its bills are made.
                    with open_books_store(context) as store:
                        _, made = materialize(store, template.id, today=today, now=now)
                    result.bills_made += len(made)
                if len(page) < PAGE:
                    break
                last = page[-1].id
        except Exception as exc:  # noqa: BLE001 - counts only; the next run tries again
            logger.warning("books_recurring_business_failed error_class=%s", type(exc).__name__)
            result.failed += 1


def run(*, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    moment = now or utc_now()
    result = BooksJobResult(dry_run=dry_run)
    run_rates(result, now=moment, dry_run=dry_run)
    run_templates(result, now=moment, dry_run=dry_run)
    run_ledgers(result, now=moment, dry_run=dry_run)
    return asdict(result)


__all__ = ["BooksJobResult", "discover_books", "discover_feeds", "run_ledgers", "discover_templates", "refresh_feed", "run"]
