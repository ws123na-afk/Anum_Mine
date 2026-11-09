"""The fixed asset register: depreciation, net book value, impairment and disposal, pure (docs/business-books.md#fixed-asset-register).

An asset is registered from the capital bill or the capital-item adjustment that bought it, so its cost, currency and
acquisition day are that source's. The register decides how it is depreciated:

* **Straight line:** cost less residual value in equal monthly parts over the useful life (the parts add up exactly, the
  last one takes the rounding), from the depreciation start month. With no residual value and the start month of the
  source this is exactly the depreciation the source posted before the register existed, so registering an existing
  capital bill or item changes no posted figure (the event keys are the source's, ``bill:<id>:dep:<month>`` and
  ``adjustment:<id>:dep:<month>``, and so are the facts).
* **Reducing balance:** each month the net book value times the annual rate over twelve, never below the residual value;
  the last month of the useful life takes the net book value down to the residual value.
* **Impairment:** an approved impairment lowers the net book value on its day. Straight-line depreciation of the months
  after the impairment's month spreads what is left (net book value less residual value) over the months that remain.
* **Disposal** (a sale or a write-off): no depreciation from the disposal's month on. The cost and the accumulated
  depreciation and impairment leave the books; proceeds go to the bank; the difference is a gain or a loss on disposal.

Depreciation is dated on the first day of its month and converted at the acquisition day's rate (a non-monetary item at
its historical rate, IAS 21).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ..finance.money import quantize
from .books_report import _spread, add_months, month_start

ZERO = Decimal(0)


@dataclass(frozen=True)
class Month:
    month: date
    amount: Decimal


def _value(item: Any) -> str:
    value = getattr(item, "value", item)
    return str(value)


def approved(impairments: Iterable[Any], asset_id: str) -> list[Any]:
    return sorted((row for row in impairments if row.asset_id == asset_id and row.status == "approved"), key=lambda row: (row.entry_date, row.id))


def schedule(asset: Any, impairments: Iterable[Any] = ()) -> list[Month]:
    """Every month's depreciation of ``asset`` (amounts in its currency), its whole life, stopping at a disposal."""
    life = asset.useful_life_months
    if not life or life <= 0 or asset.cost <= 0:
        return []
    currency = asset.currency
    cost, residual = Decimal(asset.cost), Decimal(asset.residual_value or 0)
    start = month_start(asset.depreciation_start)
    stop = month_start(asset.disposed_on) if asset.status == "disposed" and asset.disposed_on else None
    losses = approved(impairments, asset.id)
    months = [add_months(start, index) for index in range(life)]
    out: list[Month] = []
    accumulated = ZERO
    impaired = ZERO
    reducing = _value(asset.method) == "reducing_balance"
    rate = Decimal(asset.rate_pct or 0) / Decimal(100) / Decimal(12)
    parts = _spread(max(cost - residual, ZERO), currency, life) if not reducing else []
    pending = list(losses)
    for index, month in enumerate(months):
        if stop is not None and month >= stop:
            break
        # Impairments dated before this month lower the value it is depreciated from.
        while pending and month_start(pending[0].entry_date) < month:
            impaired += Decimal(pending.pop(0).amount)
            remaining = life - index
            left = max(cost - accumulated - impaired - residual, ZERO)
            if not reducing:
                parts = [ZERO] * index + _spread(left, currency, remaining) if remaining > 0 else parts
        nbv = cost - accumulated - impaired
        room = max(nbv - residual, ZERO)
        if reducing:
            amount = room if index == life - 1 else min(quantize(nbv * rate, currency), room)
        else:
            amount = min(parts[index], room)
        if amount > 0:
            out.append(Month(month, amount))
            accumulated += amount
    return out


def depreciated_through(asset: Any, impairments: Iterable[Any], day: date) -> Decimal:
    """Depreciation posted up to ``day`` (a month counts from its first day)."""
    return sum((row.amount for row in schedule(asset, impairments) if row.month <= day), ZERO)


def impaired_through(asset: Any, impairments: Iterable[Any], day: date) -> Decimal:
    return sum((Decimal(row.amount) for row in approved(impairments, asset.id) if row.entry_date <= day), ZERO)


def net_book_value(asset: Any, impairments: Iterable[Any], day: date) -> Decimal:
    """Cost less depreciation and impairment up to ``day``; zero once disposed of; nothing before the acquisition."""
    if day < asset.acquired_on:
        return ZERO
    if asset.status == "disposed" and asset.disposed_on and asset.disposed_on <= day:
        return ZERO
    rows = list(impairments)
    return Decimal(asset.cost) - depreciated_through(asset, rows, day) - impaired_through(asset, rows, day)


def disposal(asset: Any, impairments: Iterable[Any]) -> dict[str, Decimal] | None:
    """What a disposal takes out of the books: the cost, depreciation and impairment to the disposal day, the proceeds and the
    gain (positive) or loss (negative), in the asset's currency."""
    if asset.status != "disposed" or asset.disposed_on is None:
        return None
    rows = list(impairments)
    day = asset.disposed_on
    depreciation = sum((row.amount for row in schedule(asset, rows)), ZERO)
    impairment = impaired_through(asset, rows, day)
    nbv = Decimal(asset.cost) - depreciation - impairment
    proceeds = Decimal(asset.proceeds or 0)
    return {"cost": Decimal(asset.cost), "depreciation": depreciation, "impairment": impairment, "net_book_value": nbv, "proceeds": proceeds,
            "gain": proceeds - nbv}


def roll_forward(assets: list[Any], impairments: list[Any], start: date, end: date) -> dict[str, dict[str, Decimal]]:
    """Per currency: opening cost, additions, disposals and closing cost; opening accumulated depreciation and impairment, the
    period's depreciation, impairments, what disposals took out and the closing figure; and the net book values."""
    out: dict[str, dict[str, Decimal]] = {}
    before = date.fromordinal(start.toordinal() - 1)
    for asset in assets:
        figures = out.setdefault(asset.currency, {key: ZERO for key in (
            "opening_cost", "additions", "disposals_cost", "closing_cost", "opening_depreciation", "depreciation", "impairment",
            "disposals_depreciation", "closing_depreciation", "opening_nbv", "closing_nbv")})
        cost = Decimal(asset.cost)
        gone = asset.status == "disposed" and asset.disposed_on is not None
        held_before = asset.acquired_on <= before and not (gone and asset.disposed_on <= before)
        if held_before:
            figures["opening_cost"] += cost
            figures["opening_depreciation"] += depreciated_through(asset, impairments, before) + impaired_through(asset, impairments, before)
        if start <= asset.acquired_on <= end:
            figures["additions"] += cost
        period = sum((row.amount for row in schedule(asset, impairments) if start <= row.month <= end), ZERO)
        figures["depreciation"] += period
        figures["impairment"] += sum((Decimal(row.amount) for row in approved(impairments, asset.id) if start <= row.entry_date <= end), ZERO)
        if gone and start <= asset.disposed_on <= end:  # type: ignore[operator]
            figures["disposals_cost"] += cost
            figures["disposals_depreciation"] += depreciated_through(asset, impairments, end) + impaired_through(asset, impairments, end)
    for figures in out.values():
        figures["closing_cost"] = figures["opening_cost"] + figures["additions"] - figures["disposals_cost"]
        figures["closing_depreciation"] = (figures["opening_depreciation"] + figures["depreciation"] + figures["impairment"]
                                           - figures["disposals_depreciation"])
        figures["opening_nbv"] = figures["opening_cost"] - figures["opening_depreciation"]
        figures["closing_nbv"] = figures["closing_cost"] - figures["closing_depreciation"]
    return out


__all__ = ["Month", "approved", "depreciated_through", "disposal", "impaired_through", "net_book_value", "roll_forward", "schedule"]
