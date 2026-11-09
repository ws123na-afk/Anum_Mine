"""A refund finds the purchase it gives back (docs/finance.md#refunds-and-purchases).

A statement row, a hand-entered row or a synced row that is a refund with no purchase yet (category
``refunds``) looks for the person's own purchase it gives back: money out in the same currency, within
``REFUND_LOOKBACK_DAYS`` before it, whose merchant words agree with the refund's
(``statement_match.merchants_agree``, the words the statement matcher compares, without the refund words
themselves).

* **One purchase of the same amount**: a match. The refund is filed under the purchase's category, so it lowers
  that category's spending, with ``related_transaction_id`` naming the purchase (type ``refund``, source
  ``match``).
* **Anything less certain** (a partial refund, two purchases that fit): a suggestion only. The refund stays under
  ``refunds`` (its own line, never income) and ``related_transaction_id`` names the likeliest purchase with a low
  confidence (reason ``refund_suggested``) for the person to confirm.

A refund whose category or type the owner chose is never touched (audit C4). Runs inside the caller's unit of
work, before the rows are saved; it moves no money and changes no amount, date or currency.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from ..schemas import TenantContext
from . import txn_types as TT
from .models import AccountKind, Category, CategorySource, Transaction, TransactionType, TypeSource
from .statement_match import merchant_words
from .store import FinanceStore

REFUND_LOOKBACK_DAYS = 120  # the SMS path's window (sms_service.REFUND_LOOKBACK_DAYS)
# Words that say "refund" rather than name the merchant: never a merchant match on their own.
_REFUND_WORDS = frozenset({
    "refund", "refunded", "reversal", "reversed", "return", "returned", "credit", "merchant", "purchase", "of", "from", "to", "the",
    "استرجاع", "رد", "المبلغ",
})


def _words(text: str) -> set[str]:
    return {w for w in merchant_words(text) if w not in _REFUND_WORDS and len(w) >= 3}


def candidates(refund: Transaction, pool: list[Transaction]) -> list[Transaction]:
    """The person's purchases ``refund`` may give back, likeliest first (same amount, same account, newest)."""
    wanted = _words(refund.description)
    if not wanted or refund.amount <= 0:
        return []
    start = refund.posted_on - timedelta(days=REFUND_LOOKBACK_DAYS)
    found = [
        t for t in pool
        if t.id != refund.id and t.amount < 0 and t.currency == refund.currency and start <= t.posted_on <= refund.posted_on
        and -t.amount >= refund.amount and TT.effective(t) == TransactionType.EXPENSE and wanted & _words(t.description)
    ]
    return sorted(found, key=lambda t: (-t.amount != refund.amount, t.account_id != refund.account_id, -t.posted_on.toordinal(), t.id))


def link_refund(store: FinanceStore, context: TenantContext, refund: Transaction, account_kind: AccountKind | None,
                extra: list[Transaction] | None = None) -> bool:
    """File ``refund`` against its purchase when one fits (see the module docstring). True when it changed."""
    if TT.owner_locked(refund) or refund.amount <= 0 or refund.category != Category.REFUNDS:
        return False
    if TT.effective(refund, account_kind) != TransactionType.REFUND:
        return False
    pool = store.transactions_between(context, refund.posted_on - timedelta(days=REFUND_LOOKBACK_DAYS), refund.posted_on)
    pool = list({t.id: t for t in [*pool, *(extra or [])]}.values())
    found = candidates(refund, pool)
    if not found:
        return False
    best = found[0]
    exact = [t for t in found if -t.amount == refund.amount]
    if len(exact) == 1 and best is exact[0]:
        refund.category, refund.category_source = best.category, CategorySource.MATCH
        TT.apply(refund, TT.Typed(TransactionType.REFUND, TypeSource.MATCH, Decimal("0.9"), "refund_matched"))
    else:
        TT.apply(refund, TT.Typed(TransactionType.REFUND, TypeSource.MATCH, Decimal("0.4"), "refund_suggested"))
    refund.related_transaction_id = best.id
    return True


def link_refunds(store: FinanceStore, context: TenantContext, rows: list[Transaction], account_kind: AccountKind | None) -> int:
    """:func:`link_refund` for every refund among ``rows`` (a statement import), each also seeing the others."""
    return sum(link_refund(store, context, row, account_kind, extra=rows) for row in rows if row.amount > 0 and row.category == Category.REFUNDS)


__all__ = ["REFUND_LOOKBACK_DAYS", "candidates", "link_refund", "link_refunds"]
