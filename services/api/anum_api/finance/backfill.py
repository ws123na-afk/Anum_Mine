"""Historical reprocessing with the same matching engine (docs/finance-matching.md#history).

A run covers **all** the owner's history, not a recent window: from the oldest recorded payment to today, one calendar
month per batch, oldest first. After each month the run saves its checkpoint (``cursor_on``) and its counts, so a run
that stops (a deploy, a timeout, an error) resumes after the last finished month and never redoes one. Each call works
for at most ``MAX_BATCHES`` months (bounded work in one request or one daily job pass); the client or the daily job
calls again until the run is done.

* **Preview** (``mode="preview"``) changes nothing: it counts what apply would do (matched, auto linked, ambiguous,
  missing source, duplicates, corrected, differences, errors).
* **Apply** (``mode="apply"``) needs the owner's approved policy with ``reprocess_history`` on. It runs the same
  ``matching_service.run`` as every day, so its links obey every rule, and it is idempotent: a pair has one record and
  one link, so a second run creates nothing new. It never rewrites an owner's choice or an uncertain fact (ambiguous
  pairs only wait for review).
* **Undo** voids every link the run made (``links.void``: the transactions stay) and marks those records undone, so a
  later run does not make them again.

One running run per person (a partial unique index). Every step is audited with counts only.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from ..schemas import TenantContext, new_id, utc_now
from . import links as K
from .match_store import COUNT_KEYS, BackfillRun

logger = logging.getLogger("anum_api.finance.backfill")

MAX_BATCHES = 6  # months per call
MAX_ATTEMPTS = 5  # a month that fails this many times fails the run (the owner can start a new one)


class BackfillError(K.LinkError):
    pass


def _month_end(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _span(context: TenantContext) -> tuple[date | None, date | None]:
    from .sms_store import open_sms_stores

    with open_sms_stores(context) as (_finance, sms):
        days = [e.posted_on for e in sms.list_entries(context, limit=200_000)]
    return (min(days), max(days)) if days else (None, None)


def start(context: TenantContext, mode: str, *, now: datetime | None = None) -> BackfillRun:
    from .links_store import open_link_stores
    from .matching_service import policy_for

    moment = now or utc_now()
    policy = policy_for(context)
    if mode == "apply" and not (policy.approved_at is not None and policy.reprocess_history and policy.match_transactions):
        raise BackfillError(409, "Turn on matching and history reprocessing in your matching policy, and approve it, before applying.",
                            "policy_off")
    from .summary import owner_today

    first, last = _span(context)
    today = owner_today(context, moment)  # the owner's local day: the window ends on today's posted transactions
    run = BackfillRun(id=new_id("fin_backfill"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                      mode=mode, status="running" if first is not None else "done",  # type: ignore[arg-type]
                      start_on=first, end_on=max(last or today, today) if first else None, counts={k: 0 for k in COUNT_KEYS},
                      policy_version=policy.version, created_by=context.user_id, started_at=moment, updated_at=moment,
                      finished_at=None if first is not None else moment)
    with open_link_stores(context) as (_finance, _sms, links):
        if not links.add_run(run):
            raise BackfillError(409, "A history run is already in progress. Let it finish or wait for it to stop.", "run_in_progress")
    return run


def step(context: TenantContext, run_id: str, *, now: datetime | None = None, max_batches: int = MAX_BATCHES) -> BackfillRun:
    """Process up to ``max_batches`` months after the checkpoint. Safe to call again after any failure."""
    from .links_store import open_link_stores
    from .matching_service import policy_for, run

    moment = now or utc_now()
    with open_link_stores(context) as (_finance, _sms, links):
        current = links.get_run(context, run_id)
    if current is None:
        raise BackfillError(404, "History run not found.", "run_not_found")
    if current.status != "running" or current.start_on is None or current.end_on is None:
        return current
    policy = policy_for(context)
    if current.mode == "apply" and not (policy.approved_at is not None and policy.reprocess_history and policy.match_transactions):
        return _save(context, current, status="failed", error_code="policy_off", finished_at=moment, updated_at=moment)
    for _ in range(max_batches):
        month = (current.cursor_on + timedelta(days=1)) if current.cursor_on else current.start_on.replace(day=1)
        if month > current.end_on:
            current = _save(context, current, status="done", finished_at=moment, updated_at=moment)
            break
        last = min(_month_end(month), current.end_on)
        try:
            counts = run(context, month, last, now=moment, run_id=current.id, dry_run=current.mode == "preview", policy=policy)
        except Exception as exc:  # noqa: BLE001 - the checkpoint stays; the next call retries this month
            logger.warning("finance_backfill_month_failed error_class=%s", type(exc).__name__)
            attempts = current.attempts + 1
            failed = attempts >= MAX_ATTEMPTS
            current = _save(context, current, attempts=attempts, error_code=type(exc).__name__[:40], updated_at=moment,
                            status="failed" if failed else "running", finished_at=moment if failed else None,
                            counts={**current.counts, "errors": current.counts.get("errors", 0) + 1})
            break
        current = _save(context, current, cursor_on=_month_end(month), batches=current.batches + 1, attempts=0, error_code=None,
                        counts=counts.add(current.counts), updated_at=moment)
    else:
        if current.cursor_on is not None and current.cursor_on >= current.end_on:
            current = _save(context, current, status="done", finished_at=moment, updated_at=moment)
    return current


def _save(context: TenantContext, run: BackfillRun, **changes: Any) -> BackfillRun:
    from .links_store import open_link_stores

    updated = run.model_copy(update={**changes, "version": run.version + 1})
    with open_link_stores(context) as (_finance, _sms, links):
        if not links.save_run(updated, expected_version=run.version):
            raise BackfillError(409, "This history run changed at the same time. Reload and try again.", "stale")
    return updated


def undo(context: TenantContext, run_id: str, *, now: datetime | None = None) -> tuple[BackfillRun, int]:
    """Void every link this apply run made; the transactions stay. Returns the run and how many links were removed."""
    from .links_store import open_link_stores
    from .matching_service import _save_existing

    moment = now or utc_now()
    with open_link_stores(context) as (_finance, _sms, links):
        current = links.get_run(context, run_id)
        made, _ = links.list_matches(context, outcome="auto_linked", run_id=run_id, limit=50_000, offset=0)
    if current is None:
        raise BackfillError(404, "History run not found.", "run_not_found")
    if current.mode != "apply" or current.status == "running" or current.status == "undone":
        raise BackfillError(409, "Only a finished apply run can be undone.", "not_undoable")
    removed = 0
    for record in made:
        if record.link_id:
            K.void(context, record.link_id, expected_version=None, now=moment)
            removed += 1
        _save_existing(context, record, outcome="undone", decided_by=context.user_id, decided_at=moment, updated_at=moment)
    return _save(context, current, status="undone", undone_at=moment, updated_at=moment), removed


def continue_running(context: TenantContext, *, now: datetime | None = None) -> int:
    """The daily job: carry on the person's running run, if any. Returns the months done."""
    from .links_store import open_link_stores

    with open_link_stores(context) as (_finance, _sms, links):
        runs, _ = links.list_runs(context, limit=5, offset=0)
    running = next((r for r in runs if r.status == "running"), None)
    if running is None:
        return 0
    before = running.batches
    return step(context, running.id, now=now).batches - before


__all__ = ["BackfillError", "MAX_BATCHES", "continue_running", "start", "step", "undo"]
