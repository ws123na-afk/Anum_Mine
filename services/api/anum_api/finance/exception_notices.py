"""Exception notices: what is stuck, told once a day (docs/finance.md#exception-notices).

Part of the owner's automation policies, run by the daily job (``suggestion_jobs.run_person``, reported under
``finance_suggestions``). Each kind is off until the owner turns it on in Finance, Suggestions, Automation
(``AutomationPolicy.notices``); a notice is in-app only (kind ``finance_exceptions``: never an email, never a phone push):

* ``reconciliation_overdue``: an open exception of a reconciliation session older than ``notice_exception_days``;
* ``statement_missing``: a month with activity on an account and no statement (the reconciliation health check);
* ``uncategorized_money``: uncategorized money of the last 120 days at or above ``notice_uncategorized_min`` in a
  currency (each currency on its own, never added across);
* ``business_unposted``: a business where the person is an owner or an accountant with source documents the journal
  could not post (a missing rate, an amount too small) or draft bills waiting for approval;
* ``evidence_missing``: in such a business, bills and adjustments of the last 90 days at or above its evidence
  threshold with no supporting document.

**Claim, then notify.** Every item is claimed for the person's local day (``finance_notice_claims``, one row per
person, kind, item and day, insert-if-absent) before the notice is created; one notice per kind lists the items
claimed now, and when creating it fails the claims are released for the next run. An item already noticed today is
never noticed again that day. Reads are short units of work; no transaction is held across another module's reads.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from ..schemas import TenantContext
from .rules import AutomationPolicy

logger = logging.getLogger("anum_api.finance.suggestions")
NOTICE_KIND = "finance_exceptions"
KEEP_DAYS = 14  # claims older than this are pruned by the job


@dataclass
class Found:
    kind: str
    keys: list[str]
    title: str
    body: str


def _recon(context: TenantContext, today: date, policy: AutomationPolicy) -> tuple[list[str], list[str]]:
    """(overdue exception keys, missing statement month keys) from the reconciliation workspace."""
    from .reconciliation_api import list_open_exceptions

    page = list_open_exceptions(context, limit=500, offset=0)
    overdue, missing = [], []
    for item in page.items:
        exc = item.exception
        if item.reconciliation_id is None:
            if exc.kind == "missing_period" and exc.month:
                missing.append(f"{item.account_id}:{exc.month}")
            continue
        seen_on = exc.posted_on or item.period_end
        if seen_on is not None and (today - seen_on).days >= policy.notice_exception_days:
            overdue.append(f"{item.reconciliation_id}:{exc.id}")
    return overdue, missing


def _uncategorized(context: TenantContext, today: date, policy: AutomationPolicy) -> dict[str, Decimal]:
    from . import txn_types as TT
    from .models import TransactionType
    from .store import open_finance_store

    totals: dict[str, Decimal] = {}
    with open_finance_store(context) as store:
        kinds = {a.id: a.kind for a in store.list_accounts(context)}
        rows = store.transactions_between(context, today - timedelta(days=120), today)
    for t in rows:
        if TT.typed(t, kinds.get(t.account_id)).type == TransactionType.UNCATEGORIZED:
            totals[t.currency] = totals.get(t.currency, Decimal(0)) + abs(t.amount)
    return {c: v for c, v in totals.items() if v > 0 and v >= policy.notice_uncategorized_min}


def businesses(context: TenantContext) -> list[tuple[TenantContext, str]]:
    """Business workspaces with books where the person is an owner or a named accountant (each checked in its own scope)."""
    from ..authorization import ROLE_PERMISSIONS, Permission
    from ..business.books_store import open_books_store
    from ..membership_directory import list_caller_memberships

    out = []
    for row in list_caller_memberships(context):
        if row.workspace_id == context.workspace_id or row.archived:
            continue
        permissions = ROLE_PERMISSIONS.get(row.role, frozenset())  # type: ignore[call-overload]
        if Permission.BUSINESS_READ not in permissions:
            continue
        there = context.model_copy(update={"workspace_id": row.workspace_id})
        has_books = accountant = False
        try:
            with open_books_store(there) as store:
                has_books = store.get_settings() is not None
                accountant = has_books and store.first("accountants", user_id=context.user_id) is not None
        except Exception as exc:  # noqa: BLE001 - a business that cannot be read is skipped, never fatal
            logger.warning("finance_exception_notice_scope_failed error_class=%s", type(exc).__name__)
        if not has_books:
            continue
        if Permission.BUSINESS_MANAGE in permissions or (accountant and Permission.BUSINESS_WRITE in permissions):
            out.append((there, row.workspace_name or row.workspace_id))
    return out


def _business(context: TenantContext, today: date, wanted: set[str]) -> tuple[list[tuple[str, str, int]], list[tuple[str, str, list[str]]]]:
    """(unposted: workspace, name, count) and (evidence missing: workspace, name, item keys)."""
    from ..business import books_ledger as L
    from ..business.books_evidence import missing
    from ..business.books_store import open_books_store

    unposted: list[tuple[str, str, int]] = []
    evidence: list[tuple[str, str, list[str]]] = []
    for there, name in businesses(context):
        try:
            if "business_unposted" in wanted:
                status = L.status(there, today)
                with open_books_store(there) as store:
                    drafts = store.find("bills", where={"status": "draft"}, limit=1)[1]
                count = int(status["issues"]) + int(drafts)
                if count:
                    unposted.append((there.workspace_id, name, count))
            if "evidence_missing" in wanted:
                found = missing(there, today - timedelta(days=90), today)
                if found["count"]:
                    evidence.append((there.workspace_id, name, [f"{there.workspace_id}:{i['type']}:{i['id']}" for i in found["items"]]))
        except Exception as exc:  # noqa: BLE001 - one business never stops the others
            logger.warning("finance_exception_notice_business_failed error_class=%s", type(exc).__name__)
    return unposted, evidence


def find(context: TenantContext, policy: AutomationPolicy, today: date) -> list[Found]:
    """Every exception the owner's switched-on kinds report today (items and the words of each notice)."""
    wanted = set(policy.notices)
    out: list[Found] = []
    if wanted & {"reconciliation_overdue", "statement_missing"}:
        overdue, months = _recon(context, today, policy)
        if "reconciliation_overdue" in wanted and overdue:
            out.append(Found("reconciliation_overdue", overdue, "Reconciliation exceptions are waiting",
                             f"{{count}} reconciliation exception(s) have been open for {policy.notice_exception_days} days or more. "
                             "Resolve them under Finance, Reconciliation."))
        if "statement_missing" in wanted and months:
            out.append(Found("statement_missing", months, "Statements are missing",
                             "{count} month(s) with activity have no statement. Import them under Finance, Import, then reconcile."))
    if "uncategorized_money" in wanted:
        totals = _uncategorized(context, today, policy)
        if totals:
            words = ", ".join(f"{c} {v}" for c, v in sorted(totals.items()))
            out.append(Found("uncategorized_money", [f"uncategorized:{c}" for c in sorted(totals)], "Uncategorized money",
                             f"{words} of the last 120 days is uncategorized, so it is counted as neither income nor spending. "
                             "File it under Finance, Transactions."))
    if wanted & {"business_unposted", "evidence_missing"}:
        unposted, evidence = _business(context, today, wanted)
        for workspace, name, count in unposted:
            out.append(Found("business_unposted", [f"{workspace}:unposted"], "Business documents are not posted",
                             f"{count} source document(s) of {name} are not in its journal yet (a missing rate or a draft bill). "
                             "Open Business, Books, Ledger."))
        for workspace, name, keys in evidence:
            out.append(Found("evidence_missing", keys, "Supporting documents are missing",
                             f"{{count}} bill(s) or adjustment(s) of {name} above its evidence threshold have no document attached. "
                             "Attach them in Business, Books."))
    return out


def send(context: TenantContext, title: str, body: str) -> bool:
    """One in-app notification: never an email, never a phone push (``finance_exceptions`` is an in-app only kind)."""
    from ..events import CanonicalEventName, create_event
    from ..notifications import create_notification
    from ..team_work import open_team_unit

    try:
        with open_team_unit(context) as unit:
            item = create_notification(unit.store, context, user_id=context.user_id, kind=NOTICE_KIND, title=title, body=body, work_item_id=None,
                                       email=None, in_app_only=True)
            unit.repository.record_event(create_event(
                CanonicalEventName.NOTIFICATION_CREATED, context, item.id, {"recipient": context.user_id, "kind": NOTICE_KIND, "work_item_id": None},
            ).event)
        return True
    except Exception as exc:  # noqa: BLE001 - the claims are released and the next run tries again
        logger.warning("finance_exception_notice_failed error_class=%s", type(exc).__name__)
        return False


def run(context: TenantContext, *, now: datetime, dry_run: bool = False) -> tuple[int, int]:
    """Send today's exception notices. Answers (notices sent, notices that failed)."""
    from .rules_store import open_rule_stores
    from .summary import settings_for, today_in

    with open_rule_stores(context) as (finance, store):
        settings = store.get_automation(context)
        today = today_in(settings_for(finance, context), now)
    if settings is None or not settings.policy.notices:
        return 0, 0
    found = find(context, settings.policy, today)
    if dry_run:
        return len(found), 0
    sent = failed = 0
    for item in found:
        with open_rule_stores(context) as (_finance, store):
            claimed = store.claim_item_notices(context, item.kind, item.keys, today, now)
        if not claimed:
            continue  # every item of this kind was noticed today already
        if send(context, item.title, item.body.replace("{count}", str(len(claimed)))):
            sent += 1
        else:
            with open_rule_stores(context) as (_finance, store):
                store.release_item_notices(context, item.kind, claimed, today)
            failed += 1
    with open_rule_stores(context) as (_finance, store):
        store.prune_item_notices(context, today - timedelta(days=KEEP_DAYS))
    return sent, failed


__all__ = ["NOTICE_KIND", "Found", "businesses", "find", "run", "send"]
