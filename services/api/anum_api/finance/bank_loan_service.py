"""Bank loans read from the records: each loan's schedule, payments and findings, the one suggestion per loan, and the
accepted batch with its undo (docs/finance.md#bank-loans).

* ``states``: every debt with loan terms, read in short owner-scoped units of work (the debts and accounts, the matched
  payments, payslips, salary profile and accepted batches through ``obligations.load``, and the journal entries of the
  accounts a loan is debited from). Nothing here writes.
* ``produce``: the Suggestions producer ``bank_loans`` (kind ``loan_payments``): one suggestion per loan, "N payments found
  for <loan>", listing every instalment due so far as found, missing or ambiguous. Accept links every found bank debit as
  a debt payment through the links API and records the salary months, in one batch (``make_batch``); nothing is linked
  before that accept, and no automation policy can accept it (``rules.NEVER_AUTOMATED``). As new monthly deductions
  arrive, a new suggestion lists them.
* ``make_batch`` and ``undo_batch``: the accepted batch (``LoanBatch``, ``finance_loan_batches``) and its undo, which voids
  every link it made and forgets its salary months.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..schemas import TenantContext, new_id, utc_now
from . import bank_loans as BL
from . import obligations as O
from . import personal as P
from . import suggestions as SG
from .models import Account, Debt


@dataclass
class LoanState:
    debt: Debt
    schedule: BL.Schedule
    view: BL.Figures
    findings: list[BL.Found]
    as_debt: Debt  # what every reader counts (``obligations.loan_debt``)
    batches: list[P.LoanBatch] = field(default_factory=list)


def _entries(context: TenantContext, debts: list[Debt], today: date) -> list[BL.Entry]:
    """Money out of the accounts bank loans are debited from, since the earliest start, not linked to a debt yet."""
    from . import links as K
    from .commitments import DEBT_DIMENSION, is_money_out
    from .links_store import LinkStatus, open_link_stores

    wanted = {d.loan_account_id for d in debts if d.loan_repayment == "bank_debit" and d.loan_account_id}
    starts = [d.loan_start_on for d in debts if d.loan_account_id in wanted and d.loan_start_on]
    if not wanted or not starts:
        return []
    with open_link_stores(context) as (_finance, store, links):
        rows = store.list_entries(context, start=min(starts) - timedelta(days=BL.WINDOW_DAYS), end=today + timedelta(days=1))
        taken, _ = links.list_links(context, canonical_id=None, dimension=DEBT_DIMENSION, target_type=None, target_id=None, status=None,
                                    limit=20_000, offset=0)
    linked = {l.canonical_id for l in taken if l.status != LinkStatus.VOID}
    chains: dict[str, list[Any]] = {}
    for entry in rows:
        chains.setdefault(entry.canonical_id or entry.id, []).append(entry)
    out: list[BL.Entry] = []
    for canonical, chain in chains.items():
        if canonical in linked:
            continue
        current = K.live_entry(chain)
        if current is None or not is_money_out(current):
            continue
        for posting in current.postings:
            if posting.account_id in wanted and posting.amount < 0:
                cap = K.capacity(chain).get(posting.currency, Decimal(0))
                out.append(BL.Entry(canonical_id=canonical, posted_on=current.posted_on, amount=min(-posting.amount, cap) if cap > 0 else -posting.amount,
                                    currency=posting.currency, text=f"{current.merchant or ''} {current.description}".strip()[:200],
                                    account_id=posting.account_id))
                break
    return out


def states(context: TenantContext, today: date, *, debt_id: str | None = None) -> list[LoanState]:
    """Every debt with loan terms (or just ``debt_id``) with its schedule, payments, findings and batches."""
    from .store import open_finance_store

    with open_finance_store(context) as store:
        debts = [d for d in store.list_items(context, "debt") if isinstance(d, Debt) and BL.has_terms(d) and (debt_id is None or d.id == debt_id)]
    if not debts:
        return []
    links = O.load(context)  # its own short units of work
    from .personal_store import open_personal_store

    with open_personal_store(context) as people:
        batches = [b for b in people.list(context, "loan_batch") if isinstance(b, P.LoanBatch)]
    entries = _entries(context, debts, today)
    out: list[LoanState] = []
    for debt in debts:
        schedule = BL.build(debt)
        if schedule is None:
            continue
        view = BL.figures(debt, schedule, O.loan_payments(debt, links), today)
        if debt.loan_repayment == "bank_debit":
            findings = BL.find_bank(debt, view, entries, today)
        elif debt.loan_repayment == "salary_deduction":
            findings = BL.find_salary(debt, view, links.payslips, links.salary_profile, today)
        else:
            findings = []
        out.append(LoanState(debt=debt, schedule=schedule, view=view, findings=findings, as_debt=O.loan_debt(debt, links, today),
                             batches=[b for b in batches if b.debt_id == debt.id]))
    return out


# -- the suggestion -----------------------------------------------------------------------------------------------


def _months(state: LoanState) -> list[SG.LoanMonth]:
    c = state.debt.currency
    return [SG.LoanMonth(number=f.number, due_on=f.due_on, state=f.state, expected=f.expected, currency=c, amount=f.amount, canonical_id=f.canonical_id,
                         source=f.source, source_id=f.source_id, candidates=len(f.candidates)) for f in state.findings]


def suggestion(state: LoanState) -> SG.Suggestion | None:
    """One suggestion per loan: every found payment to accept at once; ambiguous months wait for a pick on the loan page."""
    from .commitments import DEBT_TARGET

    debt = state.debt
    found = [f for f in state.findings if f.state == "found"]
    ambiguous = [f for f in state.findings if f.state == "ambiguous"]
    missing = [f for f in state.findings if f.state == "missing"]
    if not found and not ambiguous:
        return None
    c = debt.currency
    links = [SG.LinkProposal(canonical_id=f.canonical_id or "", dimension="debt", target_type=DEBT_TARGET, target_id=debt.id, amount=f.amount or f.expected,
                             currency=c) for f in found if f.source == "bank_debit" and f.canonical_id]
    salary = [m for m in _months(state) if m.state == "found" and m.source in ("payslip", "salary_rule")]
    refs = sorted([f"{l.canonical_id}" for l in links] + [f"{m.due_on.isoformat()}:{m.source}:{m.source_id}" for m in salary])
    digest = hashlib.sha256(json.dumps([debt.id, refs, [f.due_on.isoformat() for f in ambiguous]]).encode()).hexdigest()[:24]
    total = sum((f.amount or f.expected for f in found), Decimal(0))
    name = debt.name[:80]
    return SG.Suggestion(
        key=f"loan_payments:{debt.id}:{digest}", producer="bank_loans", kind="loan_payments",
        detected=f"{len(found)} payments found for {name}." if found else f"{len(ambiguous)} months of {name} need a pick.",
        why=("Each one is the instalment's amount (within 1 percent), on or near its date, from the account the loan is paid from."
             if debt.loan_repayment == "bank_debit" else "Each month's payslip or salary rule shows the loan deduction."),
        params={"debt_id": debt.id, "name": name, "lender": (debt.lender or "")[:80], "currency": c, "found": str(len(found)), "missing": str(len(missing)),
                "ambiguous": str(len(ambiguous)), "total": str(total), "repayment": debt.loan_repayment or "manual"},
        evidence=[SG.Evidence(label=name[:120], ref_type="debt", ref_id=debt.id, value=f"{c} {state.view.balance}")]
        + [SG.Evidence(label=f"Instalment {f.number}", ref_type="transaction" if f.canonical_id else "other", ref_id=f.canonical_id or f.source_id,
                       value=f"{f.due_on.isoformat()} {c} {f.amount}") for f in found[:19]],
        confidence=Decimal("0.85") if found else None,
        effect=[SG.Effect(amount=total, currency=c, direction="out", note="instalments")] if found else [],
        action=SG.ProposedAction(kind="link_batch", debt_id=debt.id, links=links, loan_months=salary) if found else SG.ProposedAction(kind="open", debt_id=debt.id),
        href="#finance-debts-loans", occurred_on=max(f.due_on for f in found + ambiguous), months=_months(state),
    )


def produce(snap: SG.Snapshot) -> list[SG.Suggestion]:
    out = []
    for state in states(snap.context, snap.today):
        item = suggestion(state)
        if item is not None:
            out.append(item)
    return out


SG.register_producer("bank_loans", produce)


# -- the accepted batch -------------------------------------------------------------------------------------------


def make_batch(context: TenantContext, item: SG.Suggestion, now: datetime) -> P.LoanBatch:
    """Link every found bank debit (one at a time through the links API, every link check applies) and record the salary
    months, as one batch. A refused link voids the links made so far and is raised."""
    from . import links as K
    from .personal_store import open_personal_store

    action = item.action
    debt_id = action.debt_id or ""
    made: list[P.LoanBatchItem] = []
    try:
        for proposal in action.links:
            created = K.create(context, K.LinkRequest(canonical_id=proposal.canonical_id, dimension="debt", role="payment", target_type=proposal.target_type,
                                                      target_id=proposal.target_id, amount=proposal.amount, currency=proposal.currency))
            month = next((m for m in item.months if m.canonical_id == proposal.canonical_id), None)
            made.append(P.LoanBatchItem(due_on=month.due_on if month else now.date(), amount=proposal.amount, currency=proposal.currency,
                                        canonical_id=proposal.canonical_id, link_id=created.link.id, source="bank_debit"))
    except K.LinkError:
        for done in made:
            if done.link_id:
                K.void(context, done.link_id, expected_version=None)
        raise
    for month in action.loan_months:
        made.append(P.LoanBatchItem(due_on=month.due_on, amount=month.amount or month.expected, currency=month.currency, source=month.source or "payslip",
                                    source_id=month.source_id))
    batch = P.LoanBatch(id=new_id("fin_lbatch"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                        debt_id=debt_id, suggestion_key=item.key, items=made, created_at=now)
    with open_personal_store(context) as people:
        people.save("loan_batch", batch)
    return batch


class BatchMissing(LookupError):
    pass


def undo_batch(context: TenantContext, batch_id: str, now: datetime | None = None) -> P.LoanBatch:
    """Void every link the batch made, forget its salary months and bring its suggestion back. Undoing twice answers the
    same batch."""
    from . import links as K
    from .personal_store import open_personal_store
    from .rules_store import open_rule_stores

    with open_personal_store(context) as people:
        found = people.get(context, "loan_batch", batch_id)
    if not isinstance(found, P.LoanBatch):
        raise BatchMissing(batch_id)
    if found.status == "undone":
        return found
    for item in found.items:
        if item.link_id:
            try:
                K.void(context, item.link_id, expected_version=None)
            except K.LinkError as exc:
                if exc.code != "link_not_found":
                    raise
    undone = found.model_copy(update={"status": "undone", "undone_at": now or utc_now()})
    with open_personal_store(context) as people:
        people.save("loan_batch", undone)
    with open_rule_stores(context) as (_finance, store):
        store.delete_decision(context, found.suggestion_key)
    return undone


def own_accounts(context: TenantContext) -> dict[str, Account]:
    from .store import open_finance_store

    with open_finance_store(context) as store:
        return {a.id: a for a in store.list_accounts(context)}


__all__ = ["BatchMissing", "LoanState", "make_batch", "produce", "states", "suggestion", "undo_batch"]
