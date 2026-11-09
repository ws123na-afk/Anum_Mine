"""Card billing differences, FX fees and own transfers across currencies (docs/finance.md#card-fx-and-fees).

Pure and deterministic: no clock, no store. For one ledger row it works out what the bank's figures say:

* the **original** amount and currency (from the statement's columns or words, or the bank SMS), the **billed**
  amount in the account's currency, the **rate** (printed by the bank, from a mapped column, or implied by the two
  amounts and labelled so), the rate's **date** (the day the bank posted the row) and its **source**;
* the **FX fee or markup**: a separate fee row of the statement (``fee_row``), a markup percentage stated on the
  purchase row (``markup_stated``) or a fee amount stated on it with a printed rate that shows it is included
  (``fee_stated``). The purchase is the billed amount less a fee that is included in it, so the purchase is not
  inflated by the bank's charge;
* whether the fee can be **separated** into its own ``bank_fee`` line (a split the owner approves) or **linked**
  (a separate fee row already exists and only needs to point at its purchase).

**Never a guessed rate.** Without an original amount there is no rate: the view says "rate not stated". A fee
stated on the row without a printed rate that proves it is part of the billed amount is shown, never split.

**Own transfers in two currencies** (AED out of one account, SAR into another): the realised rate is what arrived
over what left. When the owner's exchange rates hold a rate on or before the transfer day (``finance.fx``, within
the age limit), the money that left is valued at it and the **realised FX difference** (what arrived less that
value, in the arriving currency) is its own line. Policy: the difference is worked out on read, never posted, and
counts as neither spending nor income (both sides stay an internal transfer); with no earlier rate there is no
difference, only the realised rate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from . import fx_text
from .money import MoneyError, minor_digits, quantize

FEE_ROW_DAYS = 3  # a separate fee row posts on the purchase's day or up to this many days later
RATE_PLACES = Decimal("0.000001")
MAX_FEE_SHARE = Decimal("0.1")  # a fee row above 10 % of the purchase is not taken for its FX fee
_FEE_WORDS = re.compile(
    r"(?:\bfx\b|foreign|international|intl|int'?l|overseas|cross[\s-]?border|currency|conversion|mark[\s-]?up|margin|"
    r"رسوم\s*(?:تحويل|عملة|صرف|دولية|خارجية)|عمولة\s*(?:تحويل|عملة|صرف)|هامش)",
    re.IGNORECASE,
)
_NOTE_ORIGINAL = re.compile(r"Original:\s*(?P<amount>[\d.,]+)\s*(?P<currency>[A-Z]{3})")
_NOTE_RATE = re.compile(r"Bank FX rate:\s*(?P<rate>[\d.]+)")
_NOTE_FEE = re.compile(r"FX/transaction fee reported:\s*(?P<amount>[\d.,]+)\s*(?P<currency>[A-Z]{3})")
_NOTE_MARKUP = re.compile(r"FX markup reported:\s*(?P<pct>[\d.]+)%")
_NOTE_SPLIT = re.compile(r"FX fee separated: (?P<amount>[\d.,]+) (?P<currency>[A-Z]{3})")


@dataclass(frozen=True)
class Row:
    """What the reader needs of one ledger row."""

    id: str
    account_id: str
    posted_on: date
    amount: Decimal  # signed, the account's currency
    currency: str
    description: str
    notes: str | None = None
    source: str = "statement"  # statement, sms, manual
    original_amount: Decimal | None = None  # from the SMS reading
    original_currency: str | None = None
    sms_rate: Decimal | None = None
    sms_fee: Decimal | None = None
    transaction_type: str | None = None
    related_transaction_id: str | None = None


@dataclass
class Fee:
    amount: Decimal
    currency: str
    source: str  # fee_row, markup_stated, fee_stated
    transaction_id: str | None = None  # the separate fee row, or the bank_fee line made by a split
    markup_percent: Decimal | None = None
    included_in_billed: bool = True  # an inline fee or markup the billed amount includes (a fee row never is)
    linked: bool = False  # the fee row already points at this purchase (bank_fee, related)


@dataclass
class Details:
    transaction_id: str
    foreign: bool
    original_amount: Decimal | None = None
    original_currency: str | None = None
    billed_amount: Decimal | None = None  # what the bank charged in the account's currency, fee included when it is
    billed_currency: str | None = None
    purchase_amount: Decimal | None = None  # the billed amount less an included fee
    rate: Decimal | None = None
    rate_kind: str = "not_stated"  # printed, column, implied, not_stated
    rate_date: date | None = None
    rate_source: str | None = None  # statement, sms, manual
    fee: Fee | None = None
    split: str = "none"  # none, available (separate an inline fee), link (point a fee row at it), done
    notes: list[str] = field(default_factory=list)  # codes: rate_not_stated, fee_not_provably_included, fee_already_separated


def parse_notes(notes: str | None) -> dict[str, Any]:
    """The FX facts the importers wrote into a row's notes (``csv_import`` and ``pdf_api``)."""
    text = notes or ""
    out: dict[str, Any] = {}
    if m := _NOTE_ORIGINAL.search(text):
        out["original_amount"], out["original_currency"] = Decimal(m.group("amount").replace(",", "")), m.group("currency")
    if m := _NOTE_RATE.search(text):
        out["rate"] = Decimal(m.group("rate"))
    if m := _NOTE_FEE.search(text):
        out["fee"], out["fee_currency"] = Decimal(m.group("amount").replace(",", "")), m.group("currency")
    if m := _NOTE_MARKUP.search(text):
        out["markup_percent"] = Decimal(m.group("pct"))
    if m := _NOTE_SPLIT.search(text):
        out["split_fee"], out["split_currency"] = Decimal(m.group("amount").replace(",", "")), m.group("currency")
    return out


def is_fee_row(row: Row) -> bool:
    """A money-out row whose words say it is a foreign exchange or international fee."""
    return row.amount < 0 and bool(_FEE_WORDS.search(fx_text.fold(row.description))) and (
        bool(re.search(r"fee|charge|commission|mark[\s-]?up|margin|رسوم|عمولة|هامش", row.description, re.IGNORECASE))
        or row.transaction_type == "bank_fee")


def stated_markup(text: str) -> Decimal | None:
    """A markup percentage printed in a row's words ("FX MARKUP 2.5%", "هامش 2.5%")."""
    found = fx_text._MARKUP.search(fx_text.fold(text))
    return Decimal(found.group("pct")) if found else None


def _q(value: Decimal, currency: str) -> Decimal:
    return quantize(value, currency)


def _rate(amount: Decimal, original: Decimal) -> Decimal:
    return (amount / original).quantize(RATE_PLACES, rounding=ROUND_HALF_EVEN)


def fee_row_for(purchase: Row, neighbours: list[Row], billed: Decimal, markup: Decimal | None) -> Row | None:
    """The separate fee row that belongs to this purchase: same account and currency, money out, 0 to 3 days later, fee words,
    and an amount that fits (the stated markup on the billed amount within two minor units, else at most 10 %). One answer only."""
    fits: list[Row] = []
    tolerance = Decimal(2).scaleb(-minor_digits(purchase.currency))
    for other in neighbours:
        if other.id == purchase.id or other.account_id != purchase.account_id or other.currency != purchase.currency or not is_fee_row(other):
            continue
        if not 0 <= (other.posted_on - purchase.posted_on).days <= FEE_ROW_DAYS:
            continue
        if other.related_transaction_id not in (None, purchase.id):
            continue
        amount = -other.amount
        stated = markup if markup is not None else stated_markup(other.description)
        if stated is not None:
            if abs(amount - _q(billed * stated / 100, purchase.currency)) > tolerance:
                continue
        elif amount > billed * MAX_FEE_SHARE:
            continue
        fits.append(other)
    linked = [r for r in fits if r.related_transaction_id == purchase.id]
    if linked:
        return linked[0]
    return fits[0] if len(fits) == 1 else None


def details(row: Row, neighbours: list[Row] | None = None) -> Details:
    """What the bank's figures say about this row (see the module docstring). Nothing is invented."""
    neighbours = neighbours or []
    facts = parse_notes(row.notes)
    text = fx_text.read_fx(row.description, row.currency)
    original = facts.get("original_amount") or text.original_amount or row.original_amount
    original_currency = facts.get("original_currency") or text.original_currency or row.original_currency
    billed = abs(row.amount)
    out = Details(transaction_id=row.id, foreign=bool(original_currency and original_currency != row.currency),
                  billed_amount=billed, billed_currency=row.currency, purchase_amount=billed, rate_date=row.posted_on, rate_source=row.source)
    if facts.get("split_fee") is not None:
        # Already separated: the row now holds the purchase; the billed amount was the purchase plus the fee line.
        fee = _q(facts["split_fee"], row.currency)
        out.billed_amount = billed + fee
        out.fee = Fee(fee, row.currency, "markup_stated" if facts.get("markup_percent") is not None or text.markup_percent is not None else "fee_stated",
                      markup_percent=facts.get("markup_percent") or text.markup_percent, linked=True)
        linked = next((r for r in neighbours if r.related_transaction_id == row.id and r.transaction_type == "bank_fee"), None)
        out.fee.transaction_id = linked.id if linked else None
        out.split = "done"
        out.notes.append("fee_already_separated")
    if not out.foreign:
        out.original_amount = out.original_currency = None
        out.notes.append("rate_not_stated")
        out.rate_source = None
        if out.split != "done":
            out.rate_date = None
        return out
    out.original_currency = original_currency
    if not original:
        # A foreign purchase with only the billed amount: no rate can be worked out, and none is guessed.
        out.notes.append("rate_not_stated")
        return out
    out.original_amount = abs(Decimal(original))
    printed = facts.get("rate") or (text.rate if text.rate_source == "printed" else None) or row.sms_rate
    rate_kind = "printed" if printed is not None else "implied"
    if facts.get("rate") is not None and text.rate is None:
        rate_kind = "column"
    markup = facts.get("markup_percent") if facts.get("markup_percent") is not None else text.markup_percent
    inline_fee = facts.get("fee") if facts.get("fee") is not None else text.fee
    if inline_fee is None and row.sms_fee is not None:
        inline_fee = row.sms_fee
    fee_currency = facts.get("fee_currency") or text.fee_currency or row.currency
    if out.split != "done":
        if markup is not None:
            # A stated markup is part of what the bank billed: the purchase is the billed amount without it.
            purchase = _q(billed / (1 + markup / 100), row.currency) if printed is None else _q(out.original_amount * printed, row.currency)
            check = fx_text.check(billed, out.original_amount, printed, row.currency, markup_percent=markup) if printed is not None else None
            if printed is None or (check is not None and check.status == "ties" and check.includes == "rate_markup"):
                out.fee = Fee(billed - purchase, row.currency, "markup_stated", markup_percent=markup)
                out.purchase_amount = purchase
            else:
                # The markup row of the statement may carry it: look for it below.
                markup_row = fee_row_for(row, neighbours, billed, markup)
                if markup_row is not None:
                    out.fee = Fee(-markup_row.amount, row.currency, "fee_row", markup_row.id, markup, included_in_billed=False,
                                  linked=markup_row.related_transaction_id == row.id and markup_row.transaction_type == "bank_fee")
                else:
                    out.notes.append("fee_not_provably_included")
        elif inline_fee is not None and fee_currency == row.currency and printed is not None:
            check = fx_text.check(billed, out.original_amount, printed, row.currency, fee=inline_fee, fee_currency=fee_currency)
            if check is not None and check.status == "ties" and check.includes == "rate_fee":
                out.fee = Fee(_q(inline_fee, row.currency), row.currency, "fee_stated")
                out.purchase_amount = billed - out.fee.amount
            else:
                out.notes.append("fee_not_provably_included")
        elif inline_fee is not None:
            out.notes.append("fee_not_provably_included")
        if out.fee is None:
            fee_row = fee_row_for(row, neighbours, billed, None)
            if fee_row is not None:
                out.fee = Fee(-fee_row.amount, row.currency, "fee_row", fee_row.id, stated_markup(fee_row.description), included_in_billed=False,
                              linked=fee_row.related_transaction_id == row.id and fee_row.transaction_type == "bank_fee")
        if out.fee is not None:
            if out.fee.source == "fee_row":
                out.split = "done" if out.fee.linked else "link"
            elif out.fee.amount > 0 and row.source in ("statement", "manual"):
                out.split = "available"
    base = out.purchase_amount if out.purchase_amount is not None else billed
    if printed is not None:
        out.rate, out.rate_kind = Decimal(printed), rate_kind
    else:
        out.rate, out.rate_kind = _rate(base, out.original_amount), "implied"
    return out


def plan_hash(found: Details) -> str:
    """What the owner approves: the purchase, the fee and where it comes from."""
    import hashlib

    fee = found.fee
    seed = "|".join(str(v) for v in (found.transaction_id, found.billed_amount, found.purchase_amount, fee.amount if fee else "", fee.source if fee else "",
                                     fee.transaction_id if fee else "", found.split))
    return hashlib.sha256(seed.encode()).hexdigest()


@dataclass
class TransferFx:
    out_amount: Decimal
    out_currency: str
    in_amount: Decimal
    in_currency: str
    on: date
    realised_rate: Decimal  # in_currency per unit of out_currency
    valuation_rate: Decimal | None = None
    valuation_date: date | None = None
    valuation_source: str | None = None
    valued_at: Decimal | None = None  # the money that left, at the valuation rate, in in_currency
    difference: Decimal | None = None  # in_amount - valued_at: a gain when positive; never spending or income


def transfer_fx(out_amount: Decimal, out_currency: str, in_amount: Decimal, in_currency: str, on: date, rates: list[Any],
                max_age_days: int = 31) -> TransferFx | None:
    """An own transfer between accounts in two currencies (``None`` in one currency)."""
    from . import fx

    if out_currency == in_currency or out_amount <= 0 or in_amount <= 0:
        return None
    found = TransferFx(out_amount, out_currency, in_amount, in_currency, on, _rate(in_amount, out_amount))
    usable = [r for r in rates if getattr(r, "as_of", on) <= on]
    path = fx.find_rate(usable, out_currency, in_currency, on, max_age_days)
    if path is not None:
        try:
            valued = quantize(out_amount * path.rate, in_currency)
        except MoneyError:
            return found
        found.valuation_rate, found.valuation_date, found.valuation_source = path.rate, path.as_of, path.source
        found.valued_at, found.difference = valued, in_amount - valued
    return found


__all__ = ["Details", "Fee", "Row", "TransferFx", "details", "fee_row_for", "is_fee_row", "parse_notes", "plan_hash", "stated_markup", "transfer_fx"]
