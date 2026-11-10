"""Suggestions for money lent and borrowed with people (docs/finance.md#lent-and-borrowed, #suggestions).

Two producers for the one Suggestions queue (``suggestions.register_producer``), both read only:

* ``loan_overdue``: a repayment whose date passed and that the linked repayments do not cover, with what is overdue
  in the loan's currency. Accept only records that the owner saw it; ANUM never asks the person for money and never
  pays anyone.
* ``loan_repayment``: an imported, SMS or hand-entered transaction that looks like a repayment of an open loan: money in
  for money lent, money out for money borrowed, in the loan's currency, on or after the day the money changed hands,
  not linked to the loan yet, and either naming the person (merchant words agree) or for exactly the next repayment's
  amount. Accept makes the ``person_loan`` link through the links API (``repayment`` role, every link check applies);
  nothing is linked until the owner accepts. A transaction in another currency is never proposed: a loan's balance is
  never reduced by money in another currency.

  When one transaction is exactly the repayments due on two or more loans with the same person (the earliest due first),
  the proposal is a **split**: one part per loan, each for what is due on it, all in the transaction's currency and
  together never more than the transaction. Accept makes every part or none (``links.create_many``).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from . import personal as P
from . import suggestions as SG
from . import txn_types as TT
from .merchant_match import merchants_agree
from .models import TransactionType

RECENT_DAYS = 120  # transactions a repayment proposal looks at
# Types a repayment can carry: money to or from someone else (read as spending or income until it is linked), a loan payment
# or receipt, a collection or settlement, or a row not filed yet. Never an own-account transfer, a card payment or interest;
# a refund only when its words name the person.
REPAYMENT_TYPES = frozenset({TransactionType.INCOME, TransactionType.EXPENSE, TransactionType.LOAN_RECEIVED, TransactionType.LOAN_PRINCIPAL,
                             TransactionType.RECEIVABLE_COLLECTION, TransactionType.PAYABLE_SETTLEMENT, TransactionType.REIMBURSEMENT,
                             TransactionType.UNCATEGORIZED})


def _loans(snap: SG.Snapshot) -> tuple[list[P.PersonLoan], list[Any]]:
    from .links_store import LinkStatus, open_link_stores
    from .personal_store import open_personal_store

    with open_personal_store(snap.context) as store:
        loans = [l for l in store.list(snap.context, "person_loan") if isinstance(l, P.PersonLoan)]
    if not loans:
        return [], []
    with open_link_stores(snap.context) as (_finance, _sms, links):
        rows, _ = links.list_links(snap.context, canonical_id=None, dimension=P.PERSON_LOAN_DIMENSION, target_type=None, target_id=None,
                                   status=None, limit=20_000, offset=0)
    return loans, [l for l in rows if l.status != LinkStatus.VOID]


def overdue(snap: SG.Snapshot, loans: list[P.PersonLoan], links: list[Any]) -> list[SG.Suggestion]:
    out = []
    for loan in loans:
        f = P.loan_figures(loan, links, snap.today)
        if f.status != "overdue" or f.overdue_amount <= 0:
            continue
        lent = loan.direction == P.LoanDirection.LENT
        since = next((i.due_on for i in f.schedule if i.number == f.paid_through + 1), f.next_due_on)
        out.append(SG.Suggestion(
            key=f"loan_overdue:{loan.id}:{since.isoformat() if since else '-'}:{f.overdue_amount}"[:200], producer="person_loans", kind="loan_overdue",
            detected=(f"{loan.person} has not repaid {loan.currency} {f.overdue_amount} that was due." if lent
                      else f"A repayment of {loan.currency} {f.overdue_amount} to {loan.person} is overdue."),
            why=("A repayment date passed and the repayments linked to this loan do not cover it. Link a repayment if it arrived, "
                 "or change the dates if you agreed new ones."),
            params={"person": loan.person[:80], "direction": loan.direction.value, "currency": loan.currency, "overdue": str(f.overdue_amount),
                    "since": since.isoformat() if since else "", "outstanding": str(f.outstanding)},
            evidence=[SG.Evidence(label=loan.person[:120], ref_type="other", ref_id=loan.id, value=f"{loan.currency} {f.outstanding}")],
            effect=[SG.Effect(amount=f.overdue_amount, currency=loan.currency, direction="in" if lent else "out", note="overdue")],
            action=SG.ProposedAction(kind="acknowledge"), href="#finance-debts-people", occurred_on=since,
        ))
    return out


def repayments(snap: SG.Snapshot, loans: list[P.PersonLoan], links: list[Any]) -> list[SG.Suggestion]:
    since = snap.today - timedelta(days=RECENT_DAYS)
    linked = {l.canonical_id for l in links}
    open_loans = []
    for loan in loans:
        f = P.loan_figures(loan, links, snap.today)
        if f.status in ("repaid", "closed") or f.outstanding <= 0:
            continue
        open_loans.append((loan, f))
    if not open_loans:
        return []
    out = []
    for t in snap.rows:
        canonical = snap.canon.get(t.id)
        if t.posted_on < since or canonical is None or canonical in linked or t.amount == 0:
            continue
        account = snap.accounts.get(t.account_id)
        kind = TT.typed(t, account.kind if account else None).type
        # Money in from a person is sometimes read as a refund: it may still be a repayment when the words name the person.
        if kind not in REPAYMENT_TYPES and kind != TransactionType.REFUND:
            continue
        amount = abs(t.amount)
        money_in = t.amount > 0
        named, exact = [], []
        for loan, f in open_loans:
            wants_in = loan.direction == P.LoanDirection.LENT
            if money_in != wants_in or t.currency != loan.currency or t.posted_on < loan.lent_on or amount > f.outstanding:
                continue
            agree = merchants_agree(loan.person, t.description)
            if agree:
                named.append((loan, f))
            elif agree is None and kind in REPAYMENT_TYPES and f.next_due_amount > 0 and amount == f.next_due_amount:
                exact.append((loan, f))
        choice = named if len(named) == 1 else (exact if len(exact) == 1 and not named else [])
        if not choice:
            split = _split(t, amount, kind, open_loans)
            if split is not None:
                out.append(_split_suggestion(t, canonical, amount, *split))
            continue
        loan, f = choice[0]
        both = loan in [n for n, _ in named] and amount == f.next_due_amount
        is_named = bool(named)
        lent = loan.direction == P.LoanDirection.LENT
        out.append(SG.Suggestion(
            key=f"loan_repayment:{t.id}:{loan.id}"[:200], producer="person_loans", kind="loan_repayment",
            detected=(f"This money in may be {loan.person} repaying you." if lent else f"This payment may repay what you borrowed from {loan.person}."),
            why=("The description names the person" + (" and the amount is the next repayment." if both else ".") if is_named
                 else "The amount is exactly the next repayment, though the description names no one."),
            params={"person": loan.person[:80], "direction": loan.direction.value, "currency": loan.currency, "amount": str(amount),
                    "outstanding": str(f.outstanding), "next": str(f.next_due_amount)},
            evidence=[SG._txn(t), SG.Evidence(label=loan.person[:120], ref_type="other", ref_id=loan.id, value=f"{loan.currency} {f.outstanding}")],
            confidence=Decimal("0.85") if both else Decimal("0.7") if is_named else Decimal("0.5"), effect=SG._out(t),
            action=SG.ProposedAction(kind="link", transaction_ids=[t.id], link=SG.LinkProposal(
                canonical_id=canonical, dimension="person_loan", target_type=P.PERSON_LOAN_TARGET, target_id=loan.id, amount=amount,
                currency=loan.currency, role=P.repaid_role(loan.direction))),
            href="#finance-debts-people", occurred_on=t.posted_on,
        ))
    return out


def _due_now(f: P.LoanFigures) -> Decimal:
    """What is due on a loan now: what is overdue, else the next repayment."""
    return f.overdue_amount if f.overdue_amount > 0 else f.next_due_amount


def _person_key(name: str) -> str:
    return " ".join(name.casefold().split())


def _split(t: Any, amount: Decimal, kind: TransactionType, open_loans: list[tuple[P.PersonLoan, P.LoanFigures]]
           ) -> tuple[list[tuple[P.PersonLoan, Decimal]], bool] | None:
    """The loans with one person whose due repayments, earliest first, add up to exactly this transaction (two loans or more).
    Answers each loan with its part and whether the words name the person; nothing when no person, or more than one, fits."""
    money_in = t.amount > 0
    groups: dict[str, list[tuple[P.PersonLoan, P.LoanFigures]]] = {}
    for loan, f in open_loans:
        if money_in != (loan.direction == P.LoanDirection.LENT) or t.currency != loan.currency or t.posted_on < loan.lent_on or _due_now(f) <= 0:
            continue
        groups.setdefault(_person_key(loan.person), []).append((loan, f))
    found = []
    for rows in groups.values():
        if len(rows) < 2:
            continue
        agree = merchants_agree(rows[0][0].person, t.description)
        if agree is False or (not agree and kind not in REPAYMENT_TYPES):
            continue
        rows.sort(key=lambda r: (r[1].next_due_on or r[0].due_on or r[0].lent_on, r[0].lent_on, r[0].id))
        total = Decimal(0)
        for size, (_loan, f) in enumerate(rows, start=1):
            total += _due_now(f)
            if total == amount and size >= 2:
                found.append(([(loan, _due_now(fig)) for loan, fig in rows[:size]], bool(agree)))
                break
            if total > amount:
                break
    return found[0] if len(found) == 1 else None


def _split_suggestion(t: Any, canonical: str, amount: Decimal, parts: list[tuple[P.PersonLoan, Decimal]], named: bool) -> SG.Suggestion:
    first = parts[0][0]
    lent = first.direction == P.LoanDirection.LENT
    ids = ",".join(loan.id for loan, _part in parts)
    return SG.Suggestion(
        key=f"loan_repayment:{t.id}:split:{ids}"[:200], producer="person_loans", kind="loan_repayment",
        detected=(f"This money in may be {first.person} repaying {len(parts)} loans at once." if lent
                  else f"This payment may repay {len(parts)} loans from {first.person} at once."),
        why=(f"The amount is exactly the repayments due on {len(parts)} loans with {first.person}"
             + (", and the description names them." if named else ", though the description names no one.")),
        params={"person": first.person[:80], "direction": first.direction.value, "currency": first.currency, "amount": str(amount),
                "outstanding": str(amount), "split": str(len(parts))},
        evidence=[SG._txn(t), *[SG.Evidence(label=f"{loan.person[:80]} {loan.lent_on.isoformat()}", ref_type="other", ref_id=loan.id,
                                            value=f"{loan.currency} {part}") for loan, part in parts]],
        confidence=Decimal("0.85") if named else Decimal("0.6"), effect=SG._out(t),
        action=SG.ProposedAction(kind="link", transaction_ids=[t.id], parts=[SG.LinkProposal(
            canonical_id=canonical, dimension="person_loan", target_type=P.PERSON_LOAN_TARGET, target_id=loan.id, amount=part,
            currency=loan.currency, role=P.repaid_role(loan.direction)) for loan, part in parts]),
        href="#finance-debts-people", occurred_on=t.posted_on,
    )


def produce(snap: SG.Snapshot) -> list[SG.Suggestion]:
    loans, links = _loans(snap)
    if not loans:
        return []
    return overdue(snap, loans, links) + repayments(snap, loans, links)


SG.register_producer("person_loans", produce)

__all__ = ["REPAYMENT_TYPES", "overdue", "produce", "repayments"]
