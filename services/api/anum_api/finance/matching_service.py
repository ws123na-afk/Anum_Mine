"""The matching engine's service: load, decide, link, record (docs/finance-matching.md).

``run`` reads the owner's payments of a period and their open obligations in **one short unit of work**, lets the pure
engine (``matching.decide``) decide, then makes each automatic link through ``links.create`` (its own unit of work under
the owner's lock: idempotency, the payment's currency, never more than the payment or than is due) and records every
decision (``finance_match_records``). Nothing here posts money, writes the journal or calls a model; a link is a view
of a posting and ``links.void`` removes it without touching the transaction.

Across currencies (docs/finance-matching.md#across-currencies) the loader reads each foreign purchase's figures with
``card_fx.details`` (the original amount and currency the bank printed, the billed amount, the rate and its kind, an FX
fee): an automatic link keeps the bank side (the purchase, fee left out) in the account's currency and settles the
obligation in its own currency; nothing is converted at a rate ANUM chose.

The same ``run`` serves the daily job (the recent weeks), a payment that just arrived (``match_payment``), the
owner's "Run now" and the historical backfill (``backfill.py``), one month at a time. Reruns are idempotent: a pair
has one record; a pair the owner decided (accepted, rejected, undone) is never touched again; an automatic link is
re-checked (upgraded from provisional to verified when a settling source arrives, flagged when a refund follows it or
when its payment or obligation changed) and never made twice.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..schemas import TenantContext, new_id, utc_now
from . import links as K
from . import matching as E
from .match_policy import MatchingPolicy
from .match_store import OWNER_OUTCOMES, MatchRecord
from .merchant_match import merchant_words

logger = logging.getLogger("anum_api.finance.matching")

RECENT_DAYS = 45  # what the daily job and "Run now" look at
OBLIGATION_MARGIN = 60  # obligations due this many days around the payments' period
UNDATED_CARD = date(9999, 12, 31)
PROVISIONAL = "provisional_match"


def policy_for(context: TenantContext) -> MatchingPolicy:
    from .rules_store import open_rule_stores

    with open_rule_stores(context) as (_finance, store):
        found = store.get_automation(context)
    return found.policy.matching if found is not None else MatchingPolicy()


# -- loading -------------------------------------------------------------------------------------------------------


@dataclass
class Snapshot:
    today: date
    payments: list[E.Payment]
    obligations: list[E.Obligation]
    credits: list[E.Payment]
    linked: list[E.Payment]
    records: dict[tuple[str, str], MatchRecord]
    live_links: dict[str, Any]  # link id to link
    missing_source: int = 0  # money out in the period with no canonical transaction (recorded before the journal)


def _installment_lookup(planning: Any) -> dict[tuple[str, int], Any]:
    return {(e.order_id.casefold(), e.installment): e for e in getattr(planning, "installment_entries", [])}


def load(context: TenantContext, start: date, end: date, *, now: datetime | None = None, only: set[str] | None = None) -> Snapshot:
    """Payments posted from ``start`` to ``end`` (or only the canonical ids in ``only``) and the open obligations around them."""
    from .bank_loans import has_terms
    from .commitments import BILL_DIMENSION, BILL_TARGET, DEBT_DIMENSION, DEBT_TARGET, bill_target_id, occurrences
    from .installment_refunds import bill_installment
    from .links_store import LinkStatus, open_link_stores
    from .models import Bill, Debt, DebtKind
    from .statement_ledger import paired_canonicals, records
    from .summary import settings_for, today_in

    with open_link_stores(context) as (finance, sms, links):
        today = today_in(settings_for(finance, context), now)
        accounts = {a.id: a for a in finance.list_accounts(context)}
        paired = paired_canonicals(links, context)
        rows = records(finance, sms, context, start, end, paired=paired)
        entries = {e.id: e for e in sms.list_entries(context, start=start, end=end)}
        txns = {t.id: t for t in finance.transactions_between(context, start, end + timedelta(days=1))}
        bills = [b for b in finance.list_items(context, "bill") if isinstance(b, Bill)]
        debts = [d for d in finance.list_items(context, "debt") if isinstance(d, Debt)]
        planning = finance.get_planning(context)
        bill_links, _ = links.list_links(context, canonical_id=None, dimension=BILL_DIMENSION, target_type=BILL_TARGET, target_id=None,
                                         status=None, limit=50_000, offset=0)
        debt_links, _ = links.list_links(context, canonical_id=None, dimension=DEBT_DIMENSION, target_type=DEBT_TARGET, target_id=None,
                                         status=None, limit=50_000, offset=0)
        stored, _ = links.list_matches(context, limit=50_000, offset=0)
    live = [l for l in (*bill_links, *debt_links) if l.status != LinkStatus.VOID]
    used: dict[tuple[str, str, str], Decimal] = {}  # (canonical, dimension, currency): the bank side
    by_target: dict[tuple[str, str], Decimal] = {}  # (target, currency): what is paid of it, in its own currency
    for l in live:
        used[(l.canonical_id, l.dimension, l.currency or "")] = used.get((l.canonical_id, l.dimension, l.currency or ""), Decimal(0)) + (l.amount or 0)
        ccy = K.settled_currency(l) or ""
        by_target[(l.target_id, ccy)] = by_target.get((l.target_id, ccy), Decimal(0)) + K.settled(l, ccy)
    linked_ids = {l.canonical_id for l in live}

    payments: list[E.Payment] = []
    credits: list[E.Payment] = []
    linked: list[E.Payment] = []
    texts: dict[str, str] = {}
    missing = 0
    for r in rows:
        if r.canonical_id is None:
            if r.amount < 0:
                missing += 1
            continue
        if only is not None and r.canonical_id not in only:
            continue
        entry = entries.get(r.entry_id or "")
        txn = txns.get(r.transaction_id or "")
        account = accounts.get(r.account_id)
        counter = None
        if entry is not None:
            counter = next((p.account_id for p in entry.postings if p.account_id and p.account_id != r.account_id and p.amount > 0), None)
        own = r.transfer_paired or (counter is not None and accounts.get(counter) is not None
                                    and getattr(accounts[counter].kind, "value", accounts[counter].kind) not in ("card", "loan"))
        kind = entry.kind if entry is not None else None
        txn_type = getattr(getattr(txn, "transaction_type", None), "value", None)
        cap = abs(r.amount)
        payment = E.Payment(
            canonical_id=r.canonical_id, posted_on=r.posted_on, account_id=r.account_id, amount=cap, currency=r.currency,
            direction="out" if r.amount < 0 else "in", text=" ".join(x for x in (r.description, entry.merchant if entry else None) if x)[:300],
            sources=frozenset(r.sources), reference=r.reference, kind=kind, txn_type=txn_type, counter_account_id=counter, own_transfer=own,
            original_currency=r.original_currency, original_amount=r.original_amount,
            left={dim: cap - used.get((r.canonical_id, dim, r.currency), Decimal(0)) for dim in ("bill", "debt")},
            fx=_fx_evidence(txn, entry, txns, r.currency) if r.amount < 0 and (r.original_currency or txn is not None) else None,
        )
        texts[r.canonical_id] = payment.text
        if account is not None and getattr(account.kind, "value", account.kind) in ("loan",) and payment.direction == "out":
            continue  # interest charged on a loan account is not a payment out of the owner's money
        if payment.direction == "in":
            credits.append(payment)
        elif r.canonical_id in linked_ids and all(v <= 0 for v in payment.left.values()):
            linked.append(payment)
        else:
            payments.append(payment)
            if r.canonical_id in linked_ids:
                linked.append(payment)

    lookup = _installment_lookup(planning)
    first, last = start - timedelta(days=OBLIGATION_MARGIN), end + timedelta(days=OBLIGATION_MARGIN)
    history: dict[str, set[str]] = {}
    for l in live:
        if l.dimension == "bill" and l.canonical_id in texts:
            history.setdefault(l.target_id.rpartition(":")[0], set()).update(merchant_words(texts[l.canonical_id]))
    obligations: list[E.Obligation] = []
    for bill in bills:
        part = bill_installment(bill)
        entry = lookup.get(part) if part else None
        provider = E.provider_key(entry.provider) if entry is not None else E.provider_in(f"{bill.name} {bill.notes or ''}")
        if part is not None and entry is not None and entry.source_status == "refunded":
            continue
        # A paused bill is not due, except an installment the provider reported paid: a bank debit can still verify it.
        if not bill.active and not (part is not None and entry is not None and entry.source_status == "paid"):
            continue
        for due in occurrences(bill, first, last):
            target = bill_target_id(bill.id, due)
            left = bill.amount - by_target.get((target, bill.currency), Decimal(0))
            if left <= 0:
                continue
            obligations.append(E.Obligation(
                kind="installment" if part is not None else "bill", dimension="bill", target_type=BILL_TARGET, target_id=target,
                label=(f"{entry.merchant} {part[1]}/{entry.total_installments}" if entry is not None and part is not None else bill.name)[:160],
                due_on=due, expected=left, currency=bill.currency, account_id=bill.account_id, provider=provider if part is not None else None,
                order_ref=part[0] if part is not None else None, installment_no=part[1] if part is not None else None,
                group=f"{provider or 'order'}:{part[0]}" if part is not None else None,
                history_words=frozenset(history.get(bill.id, set())),
            ))
    for debt in debts:
        kind = getattr(debt.kind, "value", debt.kind)
        if kind == DebtKind.CARD.value:
            if debt.account_id or debt.name:
                obligations.append(E.Obligation(
                    kind="card_repayment", dimension="debt", target_type=DEBT_TARGET, target_id=debt.id, label=debt.name[:160],
                    due_on=UNDATED_CARD, expected=Decimal("1e12"), currency=debt.currency, liability_account_id=debt.account_id))
            continue
        if kind not in (DebtKind.LOAN.value, DebtKind.MORTGAGE.value) or not debt.due_day:
            continue
        instalment = debt.loan_installment if debt.loan_installment else debt.minimum_payment
        if not instalment or instalment <= 0 or not (has_terms(debt) or debt.minimum_payment > 0):
            continue
        month = date(first.year, first.month, 1)
        while month <= last:

            due = month.replace(day=min(debt.due_day, calendar.monthrange(month.year, month.month)[1]))
            paid = any(l.target_id == debt.id and l.dimension == "debt" and _posted_near(l.canonical_id, texts, rows, due) for l in live)
            if not paid and (debt.loan_start_on is None or due >= debt.loan_start_on):
                obligations.append(E.Obligation(
                    kind="bank_loan", dimension="debt", target_type=DEBT_TARGET, target_id=debt.id, label=(debt.lender or debt.name)[:160],
                    due_on=due, expected=instalment, currency=debt.currency, account_id=None, liability_account_id=debt.account_id))
            month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return Snapshot(today=today, payments=payments, obligations=obligations, credits=credits, linked=linked,
                    records={(m.canonical_id, m.target_id): m for m in stored}, live_links={l.id: l for l in live}, missing_source=missing)


def _fx_evidence(txn: Any, entry: Any, txns: dict[str, Any], currency: str) -> E.FxEvidence | None:
    """The bank's own conversion figures for a purchase in another currency (``card_fx.details``), or None for a purchase in the
    account's own currency. Never a rate ANUM chose: without the original amount the evidence says so (``original_amount`` None)."""
    from . import card_fx as C

    if txn is None:
        if entry is None or not entry.original_currency or entry.original_currency == currency:
            return None
        return E.FxEvidence(original_currency=entry.original_currency, original_amount=entry.original_amount,
                            billed_amount=Decimal(0), purchase_amount=Decimal(0))
    neighbours = [C.row_for(n, None) for n in txns.values() if n.account_id == txn.account_id and n.id != txn.id
                  and abs((n.posted_on - txn.posted_on).days) <= C.FEE_ROW_DAYS]
    try:
        found = C.details(C.row_for(txn, entry), neighbours)
    except (ArithmeticError, ValueError):
        return None
    if not found.foreign or not found.original_currency:
        return None
    fee = found.fee
    # A fee row of its own is a separate transaction: the purchase is the whole billed amount. A fee the bank included in the
    # billed amount (a stated markup or fee) is left out of what the obligation was paid with.
    return E.FxEvidence(
        original_currency=found.original_currency, original_amount=found.original_amount, billed_amount=found.billed_amount or Decimal(0),
        purchase_amount=found.purchase_amount or Decimal(0), rate=found.rate if found.original_amount else None,
        rate_kind=found.rate_kind if found.original_amount else "not_stated", rate_source=found.rate_source, rate_date=found.rate_date,
        fee_amount=fee.amount if fee else None, fee_source=fee.source if fee else None, fee_transaction_id=fee.transaction_id if fee else None)


def _fx_record(c: E.Candidate) -> dict[str, str | None] | None:
    """The conversion evidence kept with a decision (and with its link): the bank side and the bank's figures."""
    p = c.payment
    if c.fx is not None:
        return c.fx.as_dict(p.currency, c.bank_amount)
    if c.fx_missing:
        known = p.fx
        return {"basis": "missing", "bank_amount": str(p.left.get(c.obligation.dimension, p.amount)), "bank_currency": p.currency,
                "original_currency": known.original_currency if known else None, "original_amount": None, "rate": None, "rate_kind": "not_stated"}
    return None


def _posted_near(canonical_id: str, texts: dict[str, str], rows: list[Any], due: date) -> bool:
    posted = next((r.posted_on for r in rows if r.canonical_id == canonical_id), None)
    return posted is not None and -7 <= (posted - due).days <= 10


# -- applying ------------------------------------------------------------------------------------------------------


@dataclass
class Counts:
    transactions: int = 0
    matched: int = 0
    auto_linked: int = 0
    verified: int = 0
    provisional: int = 0
    ambiguous: int = 0
    review: int = 0
    missing_source: int = 0
    duplicates: int = 0
    corrected: int = 0  # provisional links upgraded to verified, or flagged after their payment or bill changed
    differences: int = 0  # a link the ledger refused (over the payment, another currency, undone)
    already_linked: int = 0
    kept_owner_choice: int = 0
    errors: int = 0
    record_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {k: v for k, v in self.__dict__.items() if isinstance(v, int)}

    def add(self, other: dict[str, int]) -> dict[str, int]:
        return {k: other.get(k, 0) + v for k, v in self.as_dict().items()}


def _record(context: TenantContext, d: E.Decision, c: E.Candidate, *, outcome: str, policy: MatchingPolicy, run_id: str | None, now: datetime,
            link_id: str | None = None, reason: str | None = None) -> MatchRecord:
    o = c.obligation
    return MatchRecord(
        id=new_id("fin_match"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        canonical_id=d.payment.canonical_id, kind=o.kind, dimension=o.dimension, target_type=o.target_type, target_id=o.target_id,
        label=o.label, due_on=None if o.due_on == UNDATED_CARD else o.due_on, posted_on=d.payment.posted_on, amount=c.amount,
        currency=o.currency, outcome=outcome,  # type: ignore[arg-type]
        evidence_state=d.state if outcome == "auto_linked" else "pending", reason_code=(reason or d.reason)[:40], confidence=d.confidence if c in d.parts else c.score,
        evidence=c.evidence[:30], flags=d.flags, competitors=d.competitors, policy_version=policy.version, run_id=run_id, link_id=link_id,
        fingerprint=E.fingerprint(d.payment.fingerprint, o.fingerprint), fx=_fx_record(c), created_at=now, updated_at=now,
    )


def _store_record(context: TenantContext, record: MatchRecord, existing: MatchRecord | None) -> MatchRecord:
    from .links_store import open_link_stores

    with open_link_stores(context) as (_finance, _sms, links):
        if existing is None:
            if links.add_match(record):
                return record
            existing = links.match_for_pair(context, record.canonical_id, record.target_id)
            if existing is None or existing.outcome in OWNER_OUTCOMES:
                return existing or record
        updated = record.model_copy(update={"id": existing.id, "created_at": existing.created_at, "version": existing.version + 1,
                                            "decided_by": existing.decided_by, "decided_at": existing.decided_at})
        links.save_match(updated, expected_version=existing.version)
        return updated


def _save_existing(context: TenantContext, record: MatchRecord, **changes: Any) -> MatchRecord:
    from .links_store import open_link_stores

    updated = record.model_copy(update={**changes, "version": record.version + 1})
    with open_link_stores(context) as (_finance, _sms, links):
        if not links.save_match(updated, expected_version=record.version):
            raise K.LinkError(409, "This match changed at the same time. Reload and try again.", "stale")
    return updated


def _recheck(context: TenantContext, d: E.Decision, existing: MatchRecord, snap: Snapshot, counts: Counts, now: datetime, dry_run: bool) -> None:
    """An automatic link made before: verify it when a settling source arrived, flag a refund after it."""
    link = snap.live_links.get(existing.link_id or "")
    if link is None:
        return
    changes: dict[str, Any] = {}
    if existing.evidence_state == "provisional" and d.state == "verified" and getattr(link.status, "value", link.status) == "needs_review" \
            and link.status_reason == PROVISIONAL:
        if not dry_run:
            K.confirm(context, link.id, expected_version=None, now=now)
        changes.update(evidence_state="verified", updated_at=now)
        counts.corrected += 1
    new_flags = [f for f in d.flags if f not in existing.flags]
    if new_flags:
        changes.update(flags=[*existing.flags, *new_flags][:10], updated_at=now)
    if changes and not dry_run:
        _save_existing(context, existing, **changes)


def apply(context: TenantContext, snap: Snapshot, result: E.Result, policy: MatchingPolicy, *, now: datetime, run_id: str | None = None,
          dry_run: bool = False, audit: bool = True) -> Counts:
    """Make the automatic links and record every decision. ``dry_run`` only counts."""
    counts = Counts(transactions=len(snap.payments), missing_source=snap.missing_source, duplicates=len(result.duplicates))
    seen_targets: set[tuple[str, str]] = set()
    for d in result.decisions:
        counts.matched += 1
        if d.reason.startswith("ambiguous") or d.reason == "combined_needs_review":
            counts.ambiguous += 1
        for c in [*d.parts, *d.alternatives]:
            pair = (d.payment.canonical_id, c.obligation.target_id)
            if pair in seen_targets:
                continue
            seen_targets.add(pair)
            existing = snap.records.get(pair)
            if existing is not None and existing.outcome in OWNER_OUTCOMES:
                counts.kept_owner_choice += 1
                continue
            if existing is not None and existing.outcome == "auto_linked":
                counts.already_linked += 1
                _recheck(context, d, existing, snap, counts, now, dry_run)
                continue
            if existing is not None and existing.outcome == "stale":
                continue
            if d.outcome != "auto":
                counts.review += 1
                if not dry_run:
                    stored = _store_record(context, _record(context, d, c, outcome="review", policy=policy, run_id=run_id, now=now), existing)
                    counts.record_ids.append(stored.id)
                continue
            if dry_run:
                counts.auto_linked += 1
                setattr(counts, d.state, getattr(counts, d.state) + 1)
                continue
            try:
                made = K.create(context, _link_request(d.payment.canonical_id, c.obligation.dimension, c.obligation.target_type, c.obligation.target_id,
                                                       c.amount, c.obligation.currency, _fx_record(c)), now=now,
                                review=d.state != "verified", review_reason=PROVISIONAL)
            except K.LinkError as exc:
                counts.differences += 1
                stored = _store_record(context, _record(context, d, c, outcome="review", policy=policy, run_id=run_id, now=now,
                                                        reason=f"link_refused_{exc.code}"), existing)
                counts.record_ids.append(stored.id)
                continue
            except Exception as exc:  # noqa: BLE001 - one failing link never stops the run; it is counted and retried next time
                logger.warning("finance_matching_link_failed error_class=%s", type(exc).__name__)
                counts.errors += 1
                continue
            counts.auto_linked += 1
            setattr(counts, d.state, getattr(counts, d.state) + 1)
            stored = _store_record(context, _record(context, d, c, outcome="auto_linked", policy=policy, run_id=run_id, now=now,
                                                    link_id=made.link.id), existing)
            counts.record_ids.append(stored.id)
            if audit:
                from .suggestion_jobs import _audit

                _audit(context, "finance.matching.auto_link", made.link.id, {"record": stored.id, "kind": c.obligation.kind, "reason": d.reason,
                                                                             "confidence": str(d.confidence), "policy": policy.version,
                                                                             "state": d.state, "run": run_id or ""})
    if not dry_run:
        counts.corrected += _mark_stale(context, snap, now)
    return counts


def _link_request(canonical_id: str, dimension: str, target_type: str, target_id: str, amount: Decimal, currency: str,
                  fx: dict[str, Any] | None) -> K.LinkRequest:
    """One payment link. Across currencies the link's own amount is the bank side (the account's currency, an FX fee left out)
    and ``settled_*`` is what it pays of the obligation in its currency; the evidence goes with it."""
    request = K.LinkRequest(canonical_id=canonical_id, dimension=dimension, role="payment", target_type=target_type, target_id=target_id,
                            amount=amount, currency=currency, idempotency_key=f"pfm:{canonical_id}:{target_id}")
    if fx and fx.get("bank_currency") and fx["bank_currency"] != currency:
        if not fx.get("bank_amount"):
            raise K.LinkError(422, "The bank's amount for this payment is not known. Link it by hand from the transaction.", "fx_evidence_missing")
        request.amount, request.currency = Decimal(str(fx["bank_amount"])), str(fx["bank_currency"])
        request.settled_amount, request.settled_currency, request.fx = amount, currency, fx
    return request


def _mark_stale(context: TenantContext, snap: Snapshot, now: datetime) -> int:
    """An automatic link whose payment or bill changed since it was made (the link itself waits in review, from ``links.settle``,
    or its bill was edited): the record says so, and the engine never links that pair again on its own."""
    changed = 0
    for record in snap.records.values():
        if record.outcome != "auto_linked" or record.link_id is None:
            continue
        link = snap.live_links.get(record.link_id)
        status = getattr(getattr(link, "status", None), "value", None)
        if link is None:
            flag = "link_removed"  # voided on another page, or the payment was undone
        elif status == "needs_review" and link.status_reason != PROVISIONAL:
            flag = "source_changed"  # links.settle: the payment was corrected under it
        else:
            continue
        _save_existing(context, record, outcome="stale", flags=[*record.flags, flag][:10], updated_at=now)
        changed += 1
    return changed


# -- entry points --------------------------------------------------------------------------------------------------


def run(context: TenantContext, start: date, end: date, *, now: datetime | None = None, run_id: str | None = None, dry_run: bool = False,
        policy: MatchingPolicy | None = None, only: set[str] | None = None) -> Counts:
    moment = now or utc_now()
    policy = policy or policy_for(context)
    snap = load(context, start, end, now=moment, only=only)
    result = E.decide(snap.payments, snap.obligations, policy, credits=snap.credits, linked=snap.linked)
    return apply(context, snap, result, policy, now=moment, run_id=run_id, dry_run=dry_run)


def run_recent(context: TenantContext, *, now: datetime | None = None, dry_run: bool = False) -> Counts:
    moment = now or utc_now()
    today = moment.date()
    return run(context, today - timedelta(days=RECENT_DAYS), today + timedelta(days=1), now=moment, dry_run=dry_run)


def match_payment(context: TenantContext, canonical_id: str, posted_on: date, *, now: datetime | None = None) -> Counts | None:
    """Right after a payment is recorded (an SMS, Apple Pay, a statement row): match it, when the owner's policy is on.
    Never raises: a failure here leaves the payment recorded and the daily job tries again."""
    try:
        policy = policy_for(context)
        if not policy.match_transactions or policy.approved_at is None:
            return None
        return run(context, posted_on - timedelta(days=3), posted_on + timedelta(days=1), now=now, policy=policy, only={canonical_id})
    except Exception as exc:  # noqa: BLE001
        logger.warning("finance_matching_payment_failed error_class=%s", type(exc).__name__)
        return None


# -- the owner's choices -------------------------------------------------------------------------------------------


def _own(context: TenantContext, record_id: str) -> MatchRecord:
    from .links_store import open_link_stores

    with open_link_stores(context) as (_finance, _sms, links):
        found = links.get_match(context, record_id)
    if found is None:
        raise K.LinkError(404, "Match not found. It may have been removed.", "match_not_found")
    return found


def _check_version(record: MatchRecord, version: int | None) -> None:
    if version is not None and record.version != version:
        raise K.LinkError(409, "This match changed since you opened it. Reload and try again.", "stale")


def accept(context: TenantContext, record_id: str, *, version: int | None, now: datetime | None = None) -> MatchRecord:
    """The owner links a match that waited for review: through ``links.create`` like every link."""
    moment = now or utc_now()
    record = _own(context, record_id)
    _check_version(record, version)
    if record.outcome not in ("review", "stale"):
        raise K.LinkError(409, "Only a match waiting for review can be accepted.", "not_in_review")
    fx = record.fx
    if fx and fx.get("basis") == "missing":
        # The owner confirms that this payment paid the obligation: the whole debit is the bank side, no rate is recorded.
        fx = {**fx, "basis": "owner_confirmed"}
    made = K.create(context, _link_request(record.canonical_id, record.dimension, record.target_type, record.target_id, record.amount,
                                           record.currency, fx), now=moment)
    return _save_existing(context, record, outcome="accepted", link_id=made.link.id, evidence_state="verified", decided_by=context.user_id,
                          decided_at=moment, updated_at=moment)


def reject(context: TenantContext, record_id: str, *, version: int | None, now: datetime | None = None) -> MatchRecord:
    """Not this one: the engine never proposes this pair again."""
    moment = now or utc_now()
    record = _own(context, record_id)
    _check_version(record, version)
    if record.outcome not in ("review", "stale"):
        raise K.LinkError(409, "Only a match waiting for review can be rejected. Undo an automatic link instead.", "not_in_review")
    return _save_existing(context, record, outcome="rejected", decided_by=context.user_id, decided_at=moment, updated_at=moment)


def undo(context: TenantContext, record_id: str, *, version: int | None, now: datetime | None = None) -> MatchRecord:
    """Remove an automatic (or accepted) link: the link is voided, the transaction stays, and the pair is never linked again on its own."""
    moment = now or utc_now()
    record = _own(context, record_id)
    _check_version(record, version)
    if record.outcome not in ("auto_linked", "accepted", "stale"):
        raise K.LinkError(409, "Only a linked match can be undone.", "not_linked")
    if record.link_id:
        K.void(context, record.link_id, expected_version=None, now=moment)
    return _save_existing(context, record, outcome="undone", decided_by=context.user_id, decided_at=moment, updated_at=moment)


__all__ = ["Counts", "RECENT_DAYS", "Snapshot", "accept", "apply", "load", "match_payment", "policy_for", "reject", "run", "run_recent", "undo"]
