"""The owner's automation policies, run by the daily job (docs/finance.md#automation-policies).

Part of ``python -m anum_api.voice_retention``, reported under ``finance_suggestions`` (counts only). People are found
the way the weekly money note finds them (``advisor_jobs.discover_people``); each is handled as the application role
in their own scope. Nothing runs unless the owner turned a policy on:

* ``auto_classify``: suggestions from historical reuse at or above the owner's ``min_confidence`` (at least 0.8), and
  rows the owner's rules marked ``auto_apply`` match, get the category and type, as one batch the owner can undo,
  each recorded as ``auto_applied`` and in the audit log. Rows the owner set by hand are never touched.
* ``notify_unresolved``: one in-app notice a day (kind ``finance_suggestions``, never an email or a phone push) when
  at least ``notify_min_items`` suggestions wait. The day is claimed before the notice is sent and released when it
  fails, so it is sent once and a failure is retried by the next run.

Classification and notifications are the only things a policy can do. Posting adjustments, deleting records,
changing bank mappings, approving payments and changing accounting periods are refused in code
(``rules.require_automatable``), whatever a stored policy says.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from ..schemas import TenantContext, new_id, utc_now
from .rules import ActiveRule, AliasBook, require_automatable

logger = logging.getLogger("anum_api.finance.suggestions")

NOTICE_KIND = "finance_suggestions"
AUTO_DAYS = 90  # how far back a rule marked auto-apply reaches


@dataclass
class SuggestionJobResult:
    dry_run: bool = False
    people: int = 0
    applied: int = 0  # rows changed by a policy
    batches: int = 0
    notices: int = 0
    exception_notices: int = 0  # docs/finance.md#exception-notices
    skipped: int = 0
    failed: int = 0


def automate(context: TenantContext, action: str, **kwargs: Any) -> Any:
    """The one door automation goes through: anything but classification or a notice is refused here."""
    require_automatable(action)
    if action == "classify":
        return _classify(context, **kwargs)
    return _notify(context, **kwargs)


def _audit(context: TenantContext, action: str, target: str, metadata: dict[str, Any]) -> None:
    from ..audit import AuditRecord
    from ..team_work import open_team_unit

    with open_team_unit(context) as unit:
        unit.repository.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor="anum.automation", action=action,
            target=target, outcome="success", correlation_id=target, created_at=utc_now(), metadata={**metadata, "on_behalf_of": context.user_id},
        ))


def _classify(context: TenantContext, *, now: datetime, dry_run: bool = False) -> tuple[int, int]:
    """Apply what the owner's policy allows. Returns (rows changed, batches)."""
    from . import rules_service as RS
    from . import suggestions as SG
    from .rules_api import classify_changes
    from .rules_store import SuggestionDecision, open_rule_stores, rules_store_for
    from .sms_service import owner_lock
    from .sms_store import open_sms_stores
    from .store import TransactionFilter

    with open_rule_stores(context) as (_finance, store):
        settings = store.get_automation(context)
    if settings is None or not settings.policy.auto_classify:
        return 0, 0
    policy = settings.policy
    applied = batches = 0
    if "history" in policy.sources:
        _queue, items = SG.build(context, now, limit=1)
        wanted = [s for s in items if s.producer == "classification" and s.action.kind == "classify" and s.confidence is not None
                  and s.confidence >= policy.min_confidence]
        for item in wanted:
            if dry_run:
                applied += len(item.action.transaction_ids)
                continue
            with owner_lock(context), open_sms_stores(context) as (finance, sms):
                sms.lock_owner(context)
                rules = rules_store_for(finance)
                batch_id = new_id("fin_batch")
                claimed = rules.claim_decision(SuggestionDecision(
                    id=new_id("fin_sdec"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                    key=item.key, producer=item.producer, status="auto_applied", payload_hash=item.payload_hash, decided_by="anum.automation",
                    decided_at=now, batch_id=batch_id))
                if not claimed:
                    continue
                batch = RS.apply_rows(finance, sms, rules, context, classify_changes(finance, context, item, by_owner=False), origin="policy",
                                      by="anum.automation", now=now, suggestion_key=item.key, batch_id=batch_id)
            applied += len(batch.changes)
            batches += 1
            _audit(context, "finance.suggestion.auto_apply", batch.id, {"rows": len(batch.changes), "kind": item.kind})
    if "rule" in policy.sources:
        with open_rule_stores(context) as (_finance, store):
            from .rules import current_versions

            auto = [v for v in current_versions(store.list_versions(context)).values() if v.content.enabled and v.content.auto_apply]
        for version in auto:
            with owner_lock(context), open_sms_stores(context) as (finance, sms):
                sms.lock_owner(context)
                rules = rules_store_for(finance)
                active = replace(ActiveRule.of(version), book=AliasBook.of(rules.list_aliases(context)))
                since = now.date() - timedelta(days=AUTO_DAYS)
                rows, _ = finance.list_transactions(context, TransactionFilter(start=since), limit=RS.SCAN_LIMIT, offset=0)
                found = RS.preview(finance, sms, context, active, rows=rows)
                if not found.rows:
                    continue
                if dry_run:
                    applied += len(found.rows)
                    continue
                batch = RS.apply_rows(finance, sms, rules, context, found.rows, origin="policy", by="anum.automation", now=now, rule=active)
            applied += len(batch.changes)
            batches += 1
            _audit(context, "finance.rule.auto_apply", version.rule_id, {"version": version.version, "batch_id": batch.id, "rows": len(batch.changes)})
    return applied, batches


def notice_text(count: int) -> tuple[str, str]:
    return ("Finance suggestions are waiting", f"{count} suggestion(s) wait for you under Finance, Suggestions. ANUM changes nothing until you accept.")


def _notify(context: TenantContext, *, now: datetime, dry_run: bool = False) -> int:
    from . import suggestions as SG
    from .personal_jobs import notify as send
    from .rules_store import open_rule_stores
    from .summary import settings_for, today_in

    with open_rule_stores(context) as (finance, store):
        settings = store.get_automation(context)
        day = today_in(settings_for(finance, context), now).isoformat()
    if settings is None or not settings.policy.notify_unresolved:
        return 0
    queue, _items = SG.build(context, now, limit=1)
    if queue.total < settings.policy.notify_min_items:
        return 0
    if dry_run:
        return 1
    with open_rule_stores(context) as (_finance, store):
        if not store.claim_notice(context, day):
            return 0  # already sent today
    title, body = notice_text(queue.total)
    if not send(context, title, body, kind=NOTICE_KIND):
        with open_rule_stores(context) as (_finance, store):
            store.release_notice(context, day)
        raise RuntimeError("notice failed")
    return 1


def run_person(tenant_id: str, workspace_id: str, user_id: str, *, now: datetime | None = None, dry_run: bool = False) -> SuggestionJobResult:
    from .advisor_jobs import _may_read_finance

    moment = now or utc_now()
    context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id)
    result = SuggestionJobResult(dry_run=dry_run, people=1)
    if not _may_read_finance(context):
        result.skipped += 1
        return result
    applied, batches = automate(context, "classify", now=moment, dry_run=dry_run)
    result.applied += applied
    result.batches += batches
    try:
        result.notices += automate(context, "notify", now=moment, dry_run=dry_run)
    except RuntimeError:
        result.failed += 1
    from . import exception_notices

    require_automatable("notify")  # exception notices are notices, nothing else
    sent, failed = exception_notices.run(context, now=moment, dry_run=dry_run)
    result.exception_notices += sent
    result.failed += failed
    return result


def run(*, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    from .advisor_jobs import discover_people

    total = SuggestionJobResult(dry_run=dry_run)
    for tenant_id, workspace_id, user_id in discover_people():
        try:
            part = run_person(tenant_id, workspace_id, user_id, now=now, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - counts only; the next run tries again
            logger.warning("finance_suggestions_person_failed error_class=%s", type(exc).__name__)
            total.failed += 1
            continue
        for name in ("people", "applied", "batches", "notices", "exception_notices", "skipped", "failed"):
            setattr(total, name, getattr(total, name) + getattr(part, name))
    return asdict(total)


__all__ = ["NOTICE_KIND", "SuggestionJobResult", "automate", "notice_text", "run", "run_person"]
