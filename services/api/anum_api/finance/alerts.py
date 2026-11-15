"""Financial alerts: one notice per kind and channel a day, each item told once per channel, only from evidence
(docs/finance.md#financial-alerts).

Run by the daily job with the other finance notices (``suggestion_jobs.run_person``, counted under ``alerts``). Per kind the
person picks the channels (Settings, Notifications, Financial alerts: ``AlertSettings``, migration 0186_pf_debts; details in
``alert_channels``): in the app (the inbox row ``finance_alerts`` with the full wording, every kind on until turned off), on
the phone (``finance.alert.raised``, fixed words only) and by email (opt in, fixed words only). A push or an email never
carries an amount, a name or an account number.

**Never "paid" without evidence.** The words follow the evidence policy: a payment is called *paid* only when an active link
matches it (``verified``); a link waiting for review is *needs your check*; a provider's own report is *reported paid by
the provider*, never paid; a scheduled date alone is *due*.

Kinds (each item claimed once through ``finance_notice_claims``, the exception notices' table, before it is told; a failed
notice releases its claims for the next run):

* ``installment_due_tomorrow``: a buy now, pay later installment due tomorrow and not verified paid.
* ``installment_paid``: an installment whose bank payment was matched in the last 3 days.
* ``unverified_payment``: payments linked but waiting for review, months that need a pick, provider reports without a matched
  bank payment (told once a week).
* ``card_payment_due``: a card statement due within 3 days (or past due) not paid to its minimum by verified payments.
* ``loan_due_soon``: a loan payment you owe (a bank loan instalment, a debt, money borrowed from a person) due within 3 days
  and not paid. Money borrowed from a person whose own reminder is on is told by that reminder only (``personal_jobs``),
  never twice; the same holds for ``loan_installment_missed``.
* ``loan_installment_missed``: a bank loan instalment marked missed, or money borrowed from a person overdue.
* ``loan_payment_linked``: a payment linked to a loan in the last 3 days by anyone but the person (automation, a policy).
* ``source_stopped``: an account that had activity and has reported nothing for 10 days (told once a week).
* ``capture_failed``: a bank message (iPhone SMS, Apple Pay) ANUM could not read or process, or a statement import with rows
  the reader could not read, in the last 3 days: each message or import once. Read from the records capture already keeps
  (the message's ``failed`` status or parse reason, the import's ``error_count``); nothing new is captured here.
* ``budget_nearly_exhausted``: a budget at its alert line or over it this month: a Guardian cap, judged against the cap plus
  what its paired rollover budget carried in, and each other spending budget, judged against its ``available`` for the
  period (docs/finance.md#budget-rollover).
* ``unexpected_bank_charge``: a bank fee or interest charged in the last 3 days.
* ``suspected_duplicate``: a possible duplicate the suggestion queue found in the last 7 days.
* ``refund_received``: money given back in the last 3 days.
* ``new_recurring_charge``: a recurring charge the suggestion queue found that is not a saved bill yet (last 7 days).
* ``salary_received``, ``salary_differs``: this month's salary found, and when it differs from the expected net.
* ``lender_balance_differs``: a lender's statement balance that differs from the one worked out (told once a week).

``compose`` is pure; ``gather`` reads in short owner-scoped units of work.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..schemas import TenantContext, new_id, utc_now
from .models import Account, AccountKind, Transaction, TransactionType

logger = logging.getLogger("anum_api.finance.alerts")
NOTICE_KIND = "finance_alerts"
RECENT_DAYS = 3
STOPPED_DAYS = 10
KINDS: tuple[str, ...] = (
    "installment_due_tomorrow", "installment_paid", "unverified_payment", "card_payment_due", "loan_due_soon", "loan_installment_missed",
    "loan_payment_linked", "source_stopped", "capture_failed", "budget_nearly_exhausted", "unexpected_bank_charge", "suspected_duplicate", "refund_received", "new_recurring_charge",
    "salary_received", "salary_differs", "lender_balance_differs",
)
DEFAULTS: dict[str, bool] = dict.fromkeys(KINDS, True)  # in the app; the other channels' defaults are in ``alert_channels``
# Parse reasons of a held bank message the reader could not read (``sms_parser``): no amount, or not which way money went.
UNREAD_REASONS = frozenset({"missing_amount", "unknown_kind", "ambiguous_kind"})
CAPTURE_SOURCES = frozenset({"device", "wallet", "paste"})
LOAN_TYPES = frozenset({"personal_loan", "vehicle_finance", "mortgage", "employer_loan", "borrowed_person", "other", "informal"})
TITLES: dict[str, str] = {
    "installment_due_tomorrow": "Installment due tomorrow",
    "installment_paid": "Installment paid and matched",
    "unverified_payment": "A payment needs your check",
    "card_payment_due": "Card payment due",
    "loan_due_soon": "Loan payment due soon",
    "loan_installment_missed": "A loan payment was missed",
    "loan_payment_linked": "A loan payment was linked",
    "source_stopped": "An account stopped reporting",
    "capture_failed": "A bank message or statement could not be read",
    "budget_nearly_exhausted": "A budget is nearly used up",
    "unexpected_bank_charge": "A bank charge was taken",
    "suspected_duplicate": "A possible duplicate payment",
    "refund_received": "Refund received",
    "new_recurring_charge": "A new recurring charge",
    "salary_received": "Salary received",
    "salary_differs": "Salary differs from expected",
    "lender_balance_differs": "A lender's balance differs",
}


@dataclass(frozen=True)
class Alert:
    kind: str
    key: str  # unique per item, stable across runs
    on: date  # the day the claim is for: the event's date, or the week's first day for a standing condition
    body: str
    evidence: str = "none"  # verified, provisional, reported, none


@dataclass
class AlertData:
    today: date
    overview: Any  # debt_overview.Overview
    cards: list[Any]  # credit_cards.CardView
    accounts: list[Account]
    rows: list[Transaction]  # the last 70 days
    recent_links: list[Any] = field(default_factory=list)  # debt and person_loan links created in the last RECENT_DAYS days
    budgets: list[Any] = field(default_factory=list)  # commitments.BudgetLine of this month
    suggestions: list[Any] = field(default_factory=list)  # duplicate and subscription suggestions
    salary: list[Any] = field(default_factory=list)  # salary.SalaryMonth, oldest first
    user_id: str = ""
    names: dict[str, str] = field(default_factory=dict)  # obligation or debt id: name
    plans: list[Any] = field(default_factory=list)  # commitments.PlanBudgetLine of the current period
    own_reminders: frozenset[str] = frozenset()  # obligation ids (``person_loan:<id>``) whose own reminder tells them
    messages: list[Any] = field(default_factory=list)  # sms_store.SmsMessage failed or held unread (no body is read here)
    imports: list[Any] = field(default_factory=list)  # models.ImportRecord of the last days


def _week(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _money(amount: Decimal, currency: str) -> str:
    return f"{currency} {amount:,}"


def compose(data: AlertData) -> list[Alert]:
    """Every alert the records support today, in the words the evidence allows."""
    from . import txn_types as TT

    today = data.today
    out: list[Alert] = []
    ov = data.overview
    # Installments due tomorrow, and installments whose bank payment was matched.
    loans = {ob.id for ob in ov.items if ob.direction == "owe" and ob.type in LOAN_TYPES}
    for item in ov.calendar:
        if item.obligation_id.startswith("installment_order:") and item.on == today + timedelta(days=1) and item.status in ("due", "review"):
            out.append(Alert("installment_due_tomorrow", f"{item.obligation_id}:{item.on.isoformat()}", item.on,
                             f"{item.name}: {_money(item.amount, item.currency)} is due on {item.on.isoformat()}. It is not paid yet."))
        elif (item.obligation_id in loans and item.direction == "owe" and item.status == "due" and item.amount > 0
              and item.obligation_id not in data.own_reminders and today <= item.on <= today + timedelta(days=RECENT_DAYS)):
            out.append(Alert("loan_due_soon", f"{item.obligation_id}:{item.on.isoformat()}", item.on,
                             f"{item.name}: {_money(item.amount, item.currency)} is due on {item.on.isoformat()}. It is not paid yet."))
    for ob in ov.items:
        for h in ob.history:
            if ob.type == "bnpl" and h.evidence == "verified" and h.on is not None and today - timedelta(days=RECENT_DAYS) <= h.on <= today:
                out.append(Alert("installment_paid", f"{ob.id}:{h.ref_id}", h.on,
                                 f"{ob.name}: the installment of {_money(h.amount, h.currency)} due {h.on.isoformat()} is paid: a bank payment is matched to it.",
                                 "verified"))
        if ob.unverified_amount > 0:
            what = ("reported paid by the provider, with no bank payment matched yet" if ob.type == "bnpl"
                    else "linked or found, but waiting for your check")
            out.append(Alert("unverified_payment", f"{ob.id}:{ob.unverified_amount}", _week(today),
                             f"{ob.name}: {_money(ob.unverified_amount, ob.currency)} is {what}. It is not counted as paid.", "provisional"))
        if ob.discrepancy and ob.verified_balance is not None:
            out.append(Alert("lender_balance_differs", f"{ob.id}:{ob.verified_on}:{ob.verified_balance}", _week(today),
                             f"{ob.name}: the lender's balance of {_money(ob.verified_balance, ob.currency)} on {ob.verified_on} differs from "
                             f"{_money(ob.remaining, ob.currency)} worked out from your records by {_money(ob.difference or Decimal(0), ob.currency)}."))
        if ob.direction == "owe" and ob.overdue_amount > 0 and ob.type in LOAN_TYPES and ob.id not in data.own_reminders:
            out.append(Alert("loan_installment_missed", f"{ob.id}:{ob.overdue_amount}", _week(today),
                             f"{ob.name}: {_money(ob.overdue_amount, ob.currency)} that was due is not matched to a payment."))
    # Cards: a statement due within 3 days, or past due, not paid to its minimum by verified payments.
    for card in data.cards:
        if card.due_on is None or card.payment_state not in ("due", "part_paid", "not_paid"):
            continue
        days = (card.due_on - today).days
        if days > 3 or days < -10:
            continue
        minimum = card.statement_minimum
        need = f"the minimum {_money(minimum, card.currency)}" if minimum is not None else "the statement"
        late = "was due" if days < 0 else "is due"
        provisional = f" {_money(card.paid_provisional, card.currency)} paid is not matched to a bank debit yet." if card.paid_provisional > 0 else ""
        out.append(Alert("card_payment_due", f"card:{card.account_id}:{card.due_on.isoformat()}", card.due_on,
                         f"{card.name}: {need} {late} on {card.due_on.isoformat()}; verified payments since the statement are "
                         f"{_money(card.paid_since_statement, card.currency)}.{provisional}"))
    # Loan payments linked by anyone but the person.
    for link in data.recent_links:
        made = link.created_at.date() if hasattr(link.created_at, "date") else link.created_at
        if link.created_by == data.user_id or getattr(link.status, "value", link.status) != "active":
            continue
        name = data.names.get(link.target_id, "a loan")
        out.append(Alert("loan_payment_linked", f"link:{link.id}", made,
                         f"{_money(link.amount or Decimal(0), link.currency or '')} was linked to {name} automatically. Undo it on the loan if it is wrong.",
                         "verified"))
    # Accounts that stopped reporting.
    for account in data.accounts:
        if account.archived or account.kind not in (AccountKind.BANK, AccountKind.CARD):
            continue
        mine = [t for t in data.rows if t.account_id == account.id]
        before = [t for t in mine if today - timedelta(days=70) <= t.posted_on < today - timedelta(days=STOPPED_DAYS)]
        recent = [t for t in mine if t.posted_on >= today - timedelta(days=STOPPED_DAYS)]
        if len(before) >= 4 and not recent:
            last = max(t.posted_on for t in before)
            out.append(Alert("source_stopped", f"source:{account.id}:{last.isoformat()}", _week(today),
                             f"{account.name} has reported nothing since {last.isoformat()}. Check the bank messages or import a statement."))
    # Failed data capture: a bank message not read or not processed, a statement import with unreadable rows.
    since = today - timedelta(days=RECENT_DAYS)
    for m in data.messages:
        received = m.received_at.date() if hasattr(m.received_at, "date") else m.received_at
        status = getattr(m.status, "value", m.status)
        if received < since or m.source not in CAPTURE_SOURCES:
            continue
        if status == "failed":
            what = "could not be processed"
        elif status == "review" and m.reason in UNREAD_REASONS:
            what = "could not be read"
        else:
            continue
        where = "An Apple Pay payment" if m.source == "wallet" else "A bank message"
        out.append(Alert("capture_failed", f"sms:{m.id}", received,
                         f"{where} received on {received.isoformat()} {what}, so it is not in your records. Open Finance, iPhone SMS, Review "
                         "to read it again or enter it by hand."))
    for record in data.imports:
        made = record.created_at.date() if hasattr(record.created_at, "date") else record.created_at
        if made < since or record.error_count <= 0:
            continue
        out.append(Alert("capture_failed", f"import:{record.id}", made,
                         f"{record.error_count} rows of the statement imported on {made.isoformat()} could not be read and are not in your "
                         "records. Check the column mapping and the date format, then import those rows again."))
    # Budgets this month: the Guardian caps (against the cap plus what a paired rollover budget carried in) and every other
    # spending budget (against its available for the period).
    paired: set[str] = set()
    for line in data.budgets:
        if getattr(line, "rollover_budget_id", None):
            paired.add(line.rollover_budget_id)
        state = getattr(line, "state", "ok")
        if state not in ("near", "over"):
            continue
        if state == "near" and not getattr(line, "alert_near", True) or state == "over" and not getattr(line, "alert_over", True):
            continue
        category = getattr(line.category, "value", line.category)
        limit = getattr(line, "available", None)
        of = (f"{_money(line.cap, line.currency)}" if limit is None or not getattr(line, "rollover_budget_id", None)
              else f"{_money(limit, line.currency)} available (the cap plus {_money(line.carried_in, line.currency)} carried in)")
        out.append(Alert("budget_nearly_exhausted", f"budget:{category}:{line.currency}:{state}:{today.strftime('%Y-%m')}", _week(today),
                         f"{category}: {_money(line.spent, line.currency)} of {of} is spent this month ({line.percent} percent)."))
    for plan in data.plans:
        if plan.kind != "spending" or not plan.active or plan.id in paired or plan.spent is None or plan.available is None:
            continue
        if plan.risk == "over" or plan.available <= 0:
            state = "over"
        elif plan.percent is not None and plan.percent >= plan.alert_percent:
            state = "near"
        else:
            continue
        carried = f" (the amount plus {_money(plan.carried_in, plan.currency)} carried in)" if plan.carried_in != 0 else ""
        out.append(Alert("budget_nearly_exhausted", f"plan:{plan.id}:{state}:{plan.period_start.isoformat()}", _week(today),
                         f"{plan.name}: {_money(plan.spent, plan.currency)} of {_money(plan.available, plan.currency)} available{carried} is "
                         f"spent this period ({plan.percent} percent)."))
    # Charges, refunds.
    for t in data.rows:
        if not (today - timedelta(days=RECENT_DAYS) <= t.posted_on <= today):
            continue
        kind = TT.typed(t, next((a.kind for a in data.accounts if a.id == t.account_id), None)).type
        if t.amount < 0 and kind in (TransactionType.BANK_FEE, TransactionType.INTEREST):
            out.append(Alert("unexpected_bank_charge", f"charge:{t.id}", t.posted_on,
                             f"{_money(-t.amount, t.currency)} was charged on {t.posted_on.isoformat()}: {t.description[:80]}."))
        if t.amount > 0 and kind == TransactionType.REFUND:
            out.append(Alert("refund_received", f"refund:{t.id}", t.posted_on,
                             f"{_money(t.amount, t.currency)} came back on {t.posted_on.isoformat()}: {t.description[:80]}."))
    for s in data.suggestions:
        on = s.occurred_on or today
        if on < today - timedelta(days=7):
            continue
        if s.kind == "duplicate":
            out.append(Alert("suspected_duplicate", s.key, on, f"{s.detected} Check it under Finance, Transactions."))
        elif s.kind == "subscription":
            out.append(Alert("new_recurring_charge", s.key, on, f"{s.detected} Save it as a bill if it is yours."))
    # Salary this month.
    for month in data.salary[-1:]:
        if month.status not in ("received", "late", "partial") or month.transaction is None:
            continue
        posted = month.transaction.posted_on
        if posted < today - timedelta(days=RECENT_DAYS):
            continue
        c = month.expected.currency
        out.append(Alert("salary_received", f"salary:{month.month}:{month.transaction.transaction_id}", posted,
                         f"{_money(month.received, c)} arrived on {posted.isoformat()} as the salary of {month.month}.", "verified"))
        net = month.expected.net
        if net > 0 and abs(month.received - net) > max(net * Decimal("0.01"), Decimal(1)):
            out.append(Alert("salary_differs", f"salary_differs:{month.month}:{month.received}", posted,
                             f"The salary of {month.month} is {_money(month.received, c)}; {_money(net, c)} was expected "
                             f"({_money(month.received - net, c)} difference)."))
    unique: dict[tuple[str, str], Alert] = {}
    for alert in out:
        unique.setdefault((alert.kind, alert.key[:200]), alert)
    return list(unique.values())


def _stored(context: TenantContext) -> Any:
    from .personal import AlertSettings
    from .personal_store import open_personal_store

    with open_personal_store(context) as store:
        rows = [s for s in store.list(context, "alert_settings") if isinstance(s, AlertSettings)]
    return rows[0] if rows else None


def channel_settings(context: TenantContext) -> dict[str, dict[str, bool]]:
    """Every kind's channels (``in_app``, ``push``, ``email``): the person's choice, else the default."""
    from .alert_channels import resolve

    current = _stored(context)
    return resolve(KINDS, current.kinds if current else {})


def settings(context: TenantContext) -> dict[str, bool]:
    """Which kinds are on in the app."""
    return {k: v["in_app"] for k, v in channel_settings(context).items()}


def save_settings(context: TenantContext, kinds: dict[str, bool], channels: dict[str, dict[str, bool]] | None = None) -> dict[str, dict[str, bool]]:
    """Save the in-app switches (``kinds``) and any channel choices; unknown kinds and channels are ignored. Answers every kind's channels."""
    from .alert_channels import CHANNELS, resolve, stored_key
    from .personal import AlertSettings
    from .personal_store import open_personal_store

    now = utc_now()
    chosen = {k: bool(v) for k, v in kinds.items() if k in DEFAULTS}
    for kind, picks in (channels or {}).items():
        if kind in DEFAULTS:
            chosen.update({stored_key(kind, c): bool(on) for c, on in picks.items() if c in CHANNELS})
    with open_personal_store(context) as store:
        rows = [s for s in store.list(context, "alert_settings") if isinstance(s, AlertSettings)]
        current = rows[0] if rows else None
        merged = {**(current.kinds if current else {}), **chosen}
        store.save("alert_settings", AlertSettings(
            id=current.id if current else new_id("fin_alerts"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            owner_user_id=context.user_id, kinds=merged, created_at=current.created_at if current else now, updated_at=now))
    return resolve(KINDS, merged)


def gather(context: TenantContext, now: datetime) -> AlertData:
    """What the alerts read, each part in its own short unit of work. A part that cannot be read is left out, never fatal."""
    from . import debt_overview as D
    from . import suggestions as SG
    from .commitments import DEBT_DIMENSION
    from .links_store import open_link_stores
    from .personal import PERSON_LOAN_DIMENSION
    from .store import open_finance_store
    from .summary import settings_for, today_in

    with open_finance_store(context) as store:
        today = today_in(settings_for(store, context), now)
        accounts = store.list_accounts(context)
        rows = store.transactions_between(context, today - timedelta(days=70), today)
        imports = [i for i in store.list_imports(context, limit=50, offset=0)[0] if i.error_count > 0]
    inputs = D.gather(context, today)
    ov = D.build(inputs)
    names = {i.ref_id: i.name for i in ov.items}
    recent: list[Any] = []
    messages: list[Any] = []
    with open_link_stores(context) as (_finance, sms, links):
        for status in ("failed", "review"):
            found_messages, _ = sms.list_messages(context, status=status, limit=200, offset=0)
            messages.extend(m for m in found_messages if status == "failed" or m.reason in UNREAD_REASONS)
        for dimension in (DEBT_DIMENSION, PERSON_LOAN_DIMENSION):
            found, _ = links.list_links(context, canonical_id=None, dimension=dimension, target_type=None, target_id=None, status="active",
                                        limit=2_000, offset=0)
            recent.extend(l for l in found if (l.created_at.date() if hasattr(l.created_at, "date") else l.created_at) >= today - timedelta(days=RECENT_DAYS))
    budgets: list[Any] = []
    try:
        from .commitments_api import get_budgets

        budgets = list(get_budgets(month=None, context=context).budgets)
    except Exception as exc:  # noqa: BLE001 - budgets are optional input
        logger.warning("finance_alerts_budgets_unavailable error_class=%s", type(exc).__name__)
    plans: list[Any] = []
    try:
        from .commitments_api import list_plan_budgets

        plans = list(list_plan_budgets(on=None, include_inactive=False, context=context).budgets)
    except Exception as exc:  # noqa: BLE001 - planned budgets are optional input
        logger.warning("finance_alerts_plans_unavailable error_class=%s", type(exc).__name__)
    from .personal import PersonLoan

    own_reminders = frozenset(f"person_loan:{loan.id}" for loan in inputs.links.person_loans
                              if isinstance(loan, PersonLoan) and loan.remind and loan.due_on is not None and loan.closed_on is None)
    suggestions: list[Any] = []
    try:
        _queue, items = SG.build(context, now, limit=1)
        suggestions = [s for s in items if s.kind in ("duplicate", "subscription")]
    except Exception as exc:  # noqa: BLE001
        logger.warning("finance_alerts_suggestions_unavailable error_class=%s", type(exc).__name__)
    salary: list[Any] = []
    links_data = inputs.links
    if links_data.salary_profile is not None:
        from . import salary as S

        salary = S.salary_months(links_data.salary_profile, rows, accounts, today, links_data.payslips, lending_ids=links_data.lending_ids,  # type: ignore[arg-type]
                                 payslip_credits=links_data.payslip_credits)
    return AlertData(today=today, overview=ov, cards=inputs.cards, accounts=accounts, rows=rows, recent_links=recent, budgets=budgets,
                     suggestions=suggestions, salary=salary, user_id=context.user_id, names=names, plans=plans, own_reminders=own_reminders,
                     messages=messages, imports=imports)


def preview(context: TenantContext, now: datetime | None = None) -> tuple[date, list[Alert], dict[str, bool]]:
    """What would be told today (claims ignored) and which kinds are on in at least one channel."""
    data = gather(context, now or utc_now())
    return data.today, compose(data), {k: any(v.values()) for k, v in channel_settings(context).items()}


def run(context: TenantContext, *, now: datetime, dry_run: bool = False) -> tuple[int, int]:
    """Send today's alerts: per kind and channel one notice for the items that channel has not told yet. Answers (notices sent,
    notices failed), every channel counted."""
    from . import alert_channels as C

    chosen = channel_settings(context)
    if not any(any(v.values()) for v in chosen.values()):
        return 0, 0
    data = gather(context, now)
    found = [a for a in compose(data) if any(chosen[a.kind].values())]
    if dry_run:
        return len({a.kind for a in found}), 0
    wanted = any(on for a in found for c, on in chosen[a.kind].items() if c != "in_app")
    # Read once per run, off any transaction: whether a phone and an address can be reached at all.
    reach = C.reach(context) if wanted else C.Reach(False, None, None, None)
    email_paused = False
    sent = failed = 0
    for kind in KINDS:
        items = [a for a in found if a.kind == kind]
        if not items:
            continue
        for channel in C.CHANNELS:
            if not chosen[kind][channel] or not reach.available(channel) or (channel == "email" and email_paused):
                continue  # nothing is claimed for a channel that is off or cannot be reached
            claim_kind, keys = _claim_keys(kind, channel, items)
            claimed = _claim(context, claim_kind, keys, items, now)
            if not claimed:
                continue
            keep = False
            if channel == "in_app":
                ok = _send(context, *_in_app_text(kind, claimed))
            elif channel == "push":
                ok = C.send_push(context, kind, len(claimed))
            else:
                result = C.send_email(reach.email or "", kind, len(claimed), reach.email_language)
                ok, keep = result == "sent", result == "permanent"
                email_paused = not ok  # one failed email: this person's other emails wait for the next run, no storm
            if ok:
                sent += 1
                continue
            failed += 1
            if not keep:  # a transient failure: released, so the next daily run tries once more
                _release(context, claim_kind, keys, claimed)
    with _claims(context) as store:
        store.prune_item_notices(context, data.today - timedelta(days=14))
    return sent, failed


def _claim_keys(kind: str, channel: str, items: list[Alert]) -> tuple[str, dict[str, str]]:
    """The claim kind and each item's claim key: in the app as before (``alert:<kind>``); on the phone and by email the
    channel's own claim kind with the item key behind its alert kind."""
    from .alert_channels import EMAIL_CLAIM, PUSH_CLAIM

    if channel == "in_app":
        return f"alert:{kind}", {a.key: a.key[:200] for a in items}
    return (PUSH_CLAIM if channel == "push" else EMAIL_CLAIM), {a.key: f"{kind}:{a.key}"[:200] for a in items}


@contextmanager
def _claims(context: TenantContext) -> Iterator[Any]:
    from .rules_store import open_rule_stores

    with open_rule_stores(context) as (_finance, store):
        yield store


def _claim(context: TenantContext, claim_kind: str, keys: dict[str, str], items: list[Alert], now: datetime) -> list[Alert]:
    claimed: list[Alert] = []
    for alert in items:
        with _claims(context) as store:
            if store.claim_item_notices(context, claim_kind, [keys[alert.key]], alert.on, now):
                claimed.append(alert)
    return claimed


def _release(context: TenantContext, claim_kind: str, keys: dict[str, str], claimed: list[Alert]) -> None:
    for alert in claimed:
        with _claims(context) as store:
            store.release_item_notices(context, claim_kind, [keys[alert.key]], alert.on)


def _in_app_text(kind: str, claimed: list[Alert]) -> tuple[str, str]:
    body = " ".join(a.body for a in claimed[:5])
    if len(claimed) > 5:
        body += f" And {len(claimed) - 5} more under Finance."
    return (TITLES[kind] if len(claimed) == 1 else f"{TITLES[kind]} ({len(claimed)})"), body


def _send(context: TenantContext, title: str, body: str) -> bool:
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
        logger.warning("finance_alert_failed error_class=%s", type(exc).__name__)
        return False


__all__ = ["DEFAULTS", "KINDS", "LOAN_TYPES", "NOTICE_KIND", "TITLES", "Alert", "AlertData", "channel_settings", "compose", "gather", "preview",
           "run", "save_settings", "settings"]
