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

  A loan picked from Contacts (``contact_id``, docs/finance.md#people-from-contacts) is matched on what the directory says
  now, read in the owner's own scope (``loan_contacts.identities``): a phone number of the person in the transaction's
  words is the strongest evidence, then the person's current Contacts name, then the name typed on the loan. Only the
  strongest kind decides: when it fits more than one loan (and no split fits), the transaction waits for review instead of
  being proposed for one of them. Contacts keep no bank account or IBAN, so none is compared.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from decimal import Decimal
from typing import Any

from . import loan_contacts as LC
from . import personal as P
from . import suggestions as SG
from . import txn_types as TT
from .merchant_match import merchants_agree
from .models import TransactionType

logger = logging.getLogger("anum_api.finance.loan_suggestions")

# How strongly a transaction names a loan's person: a phone number from Contacts, the current Contacts name, the typed name.
PHONE, CONTACT, TYPED = 3, 2, 1
EVIDENCE_PARAM = {PHONE: "phone", CONTACT: "contact", TYPED: "name"}
# (named only, named and the exact next repayment) per evidence kind
CONFIDENCE = {PHONE: (Decimal("0.9"), Decimal("0.95")), CONTACT: (Decimal("0.8"), Decimal("0.9")), TYPED: (Decimal("0.7"), Decimal("0.85"))}
_DIGITS = re.compile(r"\+?\d[\d\s-]{7,}\d")

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


def _phone_tails(text: str) -> set[str]:
    return {tail for run in _DIGITS.findall(text or "") if (tail := LC.phone_tail(run))}


def _evidence(loan: P.PersonLoan, text: str, people: dict[str, LC.Identity]) -> tuple[int, bool | None]:
    """How strongly the words name this loan's person (``PHONE``, ``CONTACT``, ``TYPED`` or 0) and, when they do not, whether they
    name someone else (False) or no one (None)."""
    who = people.get(loan.contact_id) if loan.contact_id else None
    if who is not None and who.phone_tails and _phone_tails(text) & who.phone_tails:
        return PHONE, True
    contact = merchants_agree(who.name, text) if who is not None else None
    if contact:
        return CONTACT, True
    typed = merchants_agree(loan.person, text)
    if typed:
        return TYPED, True
    return 0, (False if contact is False or typed is False else None)


def _name(loan: P.PersonLoan, people: dict[str, LC.Identity]) -> str:
    """The person's name as shown: the current Contacts name when the loan is linked and readable, else the typed one."""
    who = people.get(loan.contact_id) if loan.contact_id else None
    return (who.name if who is not None else loan.person)[:80]


def _who(loan: P.PersonLoan) -> str:
    """One person: the contact when the loan has one, else the typed name (``loan_contacts.person_key``)."""
    return LC.person_key(loan.contact_id, loan.person)


def repayments(snap: SG.Snapshot, loans: list[P.PersonLoan], links: list[Any],
               people: dict[str, LC.Identity] | None = None) -> list[SG.Suggestion]:
    people = people or {}
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
    # Unnamed transactions that are exactly a loan's next repayment, per loan: when two or more fit the same loan (several people
    # sending the same amount), none is proposed as that loan's repayment; each waits for a review instead.
    exact_only: dict[str, list[str]] = {}
    plans: list[tuple[Any, str, Decimal, TransactionType, list, list, int]] = []
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
        named: list[tuple[P.PersonLoan, P.LoanFigures, int]] = []
        exact = []
        for loan, f in open_loans:
            wants_in = loan.direction == P.LoanDirection.LENT
            if money_in != wants_in or t.currency != loan.currency or t.posted_on < loan.lent_on or amount > f.outstanding:
                continue
            strength, agree = _evidence(loan, t.description, people)
            if strength:
                named.append((loan, f, strength))
            elif agree is None and kind in REPAYMENT_TYPES and f.next_due_amount > 0 and amount == f.next_due_amount:
                exact.append((loan, f))
        # Only the strongest evidence counts: a phone number from Contacts beats a name, a Contacts name beats a typed one.
        top = max((s for _l, _f, s in named), default=0)
        best = [(loan, f) for loan, f, s in named if s == top]
        plans.append((t, canonical, amount, kind, best, exact, top))
        if not best and len(exact) == 1:
            exact_only.setdefault(exact[0][0].id, []).append(t.id)
    for t, canonical, amount, kind, named, exact, strength in plans:
        whom = {_who(loan) for loan, _f in exact}
        if not named and (len(whom) > 1 or (len(exact) == 1 and len(exact_only.get(exact[0][0].id, [])) > 1)):
            out.append(_review(t, amount, exact, exact_only, people))
            continue
        choice = named if len(named) == 1 else (exact if len(exact) == 1 and not named else [])
        if not choice:
            split = _split(t, amount, kind, open_loans, people)
            if split is not None:
                out.append(_split_suggestion(t, canonical, amount, *split, people=people))
            elif len(named) > 1:
                out.append(_review(t, amount, named, {}, people, named=True))  # a tie on the strongest evidence: the owner picks
            continue
        loan, f = choice[0]
        is_named = bool(named)
        both = is_named and amount == f.next_due_amount
        lent = loan.direction == P.LoanDirection.LENT
        person = _name(loan, people)
        evidence = [SG._txn(t), SG.Evidence(label=person[:120], ref_type="other", ref_id=loan.id, value=f"{loan.currency} {f.outstanding}")]
        if strength == PHONE:
            evidence.append(SG.Evidence(label="Phone number of the person in Contacts", value="in the description"))
        elif strength == CONTACT:
            evidence.append(SG.Evidence(label="Name of the person in Contacts", value=person))
        why = {PHONE: "The description has the person's phone number from Contacts", CONTACT: "The description names the person as saved in Contacts",
               TYPED: "The description names the person"}.get(strength, "")
        out.append(SG.Suggestion(
            key=f"loan_repayment:{t.id}:{loan.id}"[:200], producer="person_loans", kind="loan_repayment",
            detected=(f"This money in may be {person} repaying you." if lent else f"This payment may repay what you borrowed from {person}."),
            why=(why + (" and the amount is the next repayment." if both else ".") if is_named
                 else "The amount is exactly the next repayment, though the description names no one."),
            params={"person": person, "direction": loan.direction.value, "currency": loan.currency, "amount": str(amount),
                    "outstanding": str(f.outstanding), "next": str(f.next_due_amount), "evidence": EVIDENCE_PARAM.get(strength, "amount")},
            evidence=evidence,
            confidence=CONFIDENCE[strength][1 if both else 0] if is_named else Decimal("0.5"), effect=SG._out(t),
            action=SG.ProposedAction(kind="link", transaction_ids=[t.id], link=SG.LinkProposal(
                canonical_id=canonical, dimension="person_loan", target_type=P.PERSON_LOAN_TARGET, target_id=loan.id, amount=amount,
                currency=loan.currency, role=P.repaid_role(loan.direction))),
            href="#finance-debts-people", occurred_on=t.posted_on,
        ))
    return out


def _review(t: Any, amount: Decimal, fits: list[tuple[P.PersonLoan, P.LoanFigures]], exact_only: dict[str, list[str]],
            people: dict[str, LC.Identity], *, named: bool = False) -> SG.Suggestion:
    """A repayment that cannot be told apart. Unnamed: the amount fits loans with more than one person, or more than one
    transaction fits the same loan. Named: the words fit more than one loan equally well (two people with the same name, or
    two loans with one person that no split explains). Nothing is proposed for linking: the owner picks on the loan's page."""
    names = sorted({_name(loan, people) for loan, _f in fits})
    whom = {_who(loan) for loan, _f in fits}
    others = sorted({tid for loan, _f in fits for tid in exact_only.get(loan.id, []) if tid != t.id})
    if named:
        why = (f"The description fits loans with {len(whom)} people ({', '.join(names)}) equally well." if len(whom) > 1
               else f"The description fits {len(fits)} loans with {names[0]}, and the amount is not what is due on them together.")
        why += " Link it to the right loan on the loan's page."
        count = len(whom) if len(whom) > 1 else len(fits)
    elif len(whom) > 1:
        why = f"The amount is exactly the next repayment from {len(whom)} people ({', '.join(names)}), and the description names no one."
        count = len(whom)
    else:
        why = (f"{len(others) + 1} transactions of this amount fit the next repayment from {names[0]}, and none names the person. "
               "Link the right one on the loan's page.")
        count = len(others) + 1
    first = fits[0][0]
    return SG.Suggestion(
        key=f"loan_repayment_review:{t.id}"[:200], producer="person_loans", kind="loan_repayment",
        detected=f"This {'money in' if t.amount > 0 else 'payment'} may be a loan repayment, but it cannot be told apart.",
        why=why, params={"person": ", ".join(names)[:80], "direction": first.direction.value, "currency": first.currency, "amount": str(amount),
                         "review": "1", "candidates": str(count)},
        evidence=[SG._txn(t)] + [SG.Evidence(label=_name(loan, people)[:120], ref_type="other", ref_id=loan.id, value=f"{loan.currency} {f.next_due_amount}")
                                 for loan, f in fits[:5]],
        confidence=None, effect=SG._out(t), action=SG.ProposedAction(kind="open", transaction_ids=[t.id]), href="#finance-debts-people",
        occurred_on=t.posted_on,
    )


def _due_now(f: P.LoanFigures) -> Decimal:
    """What is due on a loan now: what is overdue, else the next repayment."""
    return f.overdue_amount if f.overdue_amount > 0 else f.next_due_amount


MAX_SPLIT_PARTS = 10  # SG.ProposedAction.parts allows 10


def _split(t: Any, amount: Decimal, kind: TransactionType, open_loans: list[tuple[P.PersonLoan, P.LoanFigures]],
           people: dict[str, LC.Identity] | None = None) -> tuple[list[tuple[P.PersonLoan, Decimal]], bool] | None:
    """The loans with one person whose due repayments, earliest first, add up to exactly this transaction (two loans or more).
    One person is one contact when the loans are linked to Contacts, else one typed name. Answers each loan with its part and
    whether the words name the person; nothing when no person, or more than one, fits."""
    people = people or {}
    money_in = t.amount > 0
    groups: dict[str, list[tuple[P.PersonLoan, P.LoanFigures]]] = {}
    for loan, f in open_loans:
        if money_in != (loan.direction == P.LoanDirection.LENT) or t.currency != loan.currency or t.posted_on < loan.lent_on or _due_now(f) <= 0:
            continue
        groups.setdefault(_who(loan), []).append((loan, f))
    found = []
    for rows in groups.values():
        if len(rows) < 2:
            continue
        strength, agree = max((_evidence(loan, t.description, people) for loan, _f in rows), key=lambda e: (e[0], e[1] is None))
        if not strength and (agree is False or kind not in REPAYMENT_TYPES):
            continue
        rows.sort(key=lambda r: (r[1].next_due_on or r[0].due_on or r[0].lent_on, r[0].lent_on, r[0].id))
        total = Decimal(0)
        for size, (_loan, f) in enumerate(rows[:MAX_SPLIT_PARTS], start=1):  # a split action carries at most 10 parts
            total += _due_now(f)
            if total == amount and size >= 2:
                found.append(([(loan, _due_now(fig)) for loan, fig in rows[:size]], bool(strength)))
                break
            if total > amount:
                break
    return found[0] if len(found) == 1 else None


def _split_suggestion(t: Any, canonical: str, amount: Decimal, parts: list[tuple[P.PersonLoan, Decimal]], named: bool, *,
                      people: dict[str, LC.Identity] | None = None) -> SG.Suggestion:
    people = people or {}
    first = parts[0][0]
    person = _name(first, people)
    lent = first.direction == P.LoanDirection.LENT
    ids = ",".join(loan.id for loan, _part in parts)
    return SG.Suggestion(
        key=f"loan_repayment:{t.id}:split:{ids}"[:200], producer="person_loans", kind="loan_repayment",
        detected=(f"This money in may be {person} repaying {len(parts)} loans at once." if lent
                  else f"This payment may repay {len(parts)} loans from {person} at once."),
        why=(f"The amount is exactly the repayments due on {len(parts)} loans with {person}"
             + (", and the description names them." if named else ", though the description names no one.")),
        params={"person": person, "direction": first.direction.value, "currency": first.currency, "amount": str(amount),
                "outstanding": str(amount), "split": str(len(parts))},
        evidence=[SG._txn(t), *[SG.Evidence(label=f"{_name(loan, people)} {loan.lent_on.isoformat()}", ref_type="other", ref_id=loan.id,
                                            value=f"{loan.currency} {part}") for loan, part in parts]],
        confidence=Decimal("0.85") if named else Decimal("0.6"), effect=SG._out(t),
        action=SG.ProposedAction(kind="link", transaction_ids=[t.id], parts=[SG.LinkProposal(
            canonical_id=canonical, dimension="person_loan", target_type=P.PERSON_LOAN_TARGET, target_id=loan.id, amount=part,
            currency=loan.currency, role=P.repaid_role(loan.direction)) for loan, part in parts]),
        href="#finance-debts-people", occurred_on=t.posted_on,
    )


def _people(snap: SG.Snapshot, loans: list[P.PersonLoan]) -> dict[str, LC.Identity]:
    """The Contacts side of the loans picked from Contacts. A Contacts failure never hides repayments: the typed names still match."""
    try:
        return LC.identities(snap.context, [loan.contact_id for loan in loans])
    except Exception as exc:  # noqa: BLE001
        logger.warning("finance_loan_contacts_unavailable error_class=%s", type(exc).__name__)
        return {}


def produce(snap: SG.Snapshot) -> list[SG.Suggestion]:
    loans, links = _loans(snap)
    if not loans:
        return []
    return overdue(snap, loans, links) + repayments(snap, loans, links, _people(snap, loans))


SG.register_producer("person_loans", produce)

__all__ = ["REPAYMENT_TYPES", "overdue", "produce", "repayments"]
