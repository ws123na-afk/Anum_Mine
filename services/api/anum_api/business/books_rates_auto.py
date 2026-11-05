"""Automatic exchange rates for the books (docs/business-books.md#automatic-exchange-rates).

An owner opts a business in and chooses the currencies it follows. Once a day the job reads the
European Central Bank's euro reference rates (the same free source and strict parser as Finance,
``finance/fx.py``) and stores, in the business's own book rates, ``1 EUR = rate X`` for each followed
currency with the ECB's date and the source ``ECB``. The UAE dirham is not in the ECB list; it is
derived from EUR to USD and the Central Bank of the UAE's fixed peg (3.6725 dirhams a dollar), as
``fx.with_aed_peg`` does, and its source says so. Any other currency the ECB does not publish (for
example SAR or KWD) is listed as unavailable and gets no rate: ANUM never invents one.

* A rate an owner typed always wins: the job never stores a rate for a pair and day that already has
  one (either way round), and an owner's rate for a pair and day replaces a fetched one.
* A failed fetch changes no rate. The feed keeps when it last succeeded and the rates' date, and
  says what failed; the report goes on using the last rates while they are within the business's
  maximum rate age.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from ..finance import fx
from ..schemas import TenantContext, new_id
from .books_models import Rate, RateFeed, RateOrigin
from .books_store import BooksStore, DuplicateError

logger = logging.getLogger("anum_api.business.books_rates_auto")

SOURCE = "ECB"
PEG_SOURCE = "ECB, AED from EUR/USD at the CBUAE peg 3.6725 AED per USD"
MAX_CURRENCIES = 12
REFRESH_EVERY_SECONDS = 600  # an owner's "Fetch now" at most every ten minutes per business


@dataclass(frozen=True)
class Published:
    """The ECB's rates of one day, as quotes per euro, with the source of each (AED derived from the peg)."""

    as_of: date
    quotes: dict[str, tuple[Decimal, str]]


class FeedError(Exception):
    """The rates could not be read (a safe message, shown to owners)."""


def fetch(today: date) -> Published:
    """One read of the ECB daily file through Finance's connector (no redirects, capped size, strict parser)."""
    from ..finance import connectors
    from ..finance.config import finance_config

    try:
        text = asyncio.run(connectors.http_get(fx.ECB_URL, timeout=finance_config.request_timeout_seconds))
        published = fx.parse_ecb(text, today)
    except (connectors.ConnectorError, fx.FxError) as exc:
        raise FeedError(str(exc)) from None
    return Published(as_of=published.as_of, quotes={code: (rate, PEG_SOURCE if source == fx.PEG_SOURCE else SOURCE)
                                                     for code, (rate, source) in fx.with_aed_peg(published).items()})


def followed(feed: RateFeed, base_currency: str | None) -> list[str]:
    """The currencies a business follows: the ones chosen and its base currency, never EUR (the rates are per euro)."""
    codes = {code for code in feed.currencies if fx.is_code(code)}
    if base_currency:
        codes.add(base_currency)
    codes.discard("EUR")
    return sorted(codes)


def apply(context: TenantContext, store: BooksStore, feed: RateFeed, published: Published, *, base_currency: str | None, now: datetime,
          actor: str) -> int:
    """Store the day's rates for the followed currencies, skipping any pair and day that already has a rate. Returns how many."""
    added = 0
    unavailable = []
    for code in followed(feed, base_currency):
        found = published.quotes.get(code)
        if found is None:
            unavailable.append(code)
            continue
        rate, source = found
        if store.first("rates", base="EUR", quote=code, as_of=published.as_of) or store.first("rates", base=code, quote="EUR", as_of=published.as_of):
            continue  # already stored, or typed by an owner for that day: a typed rate always wins
        try:
            store.add("rates", Rate(id=new_id("brate"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, base="EUR",
                                    quote=code, rate=rate, as_of=published.as_of, source=source, created_by=actor, origin=RateOrigin.ECB))
            added += 1
        except DuplicateError:
            continue  # an owner recorded it at the same moment
    store.save("rate_feeds", feed.model_copy(update={"last_attempt_at": now, "last_success_at": now, "last_as_of": published.as_of, "last_error": "",
                                                     "unavailable": unavailable, "version": feed.version + 1}), expected_version=feed.version)
    return added


def record_failure(store: BooksStore, feed: RateFeed, message: str, now: datetime) -> None:
    """The fetch failed: the rates stay as they were; the feed says when and why."""
    store.save("rate_feeds", feed.model_copy(update={"last_attempt_at": now, "last_error": message[:300], "version": feed.version + 1}),
               expected_version=feed.version)


__all__ = ["FeedError", "MAX_CURRENCIES", "PEG_SOURCE", "Published", "REFRESH_EVERY_SECONDS", "SOURCE", "apply", "fetch", "followed", "record_failure"]
