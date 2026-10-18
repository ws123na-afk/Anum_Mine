"""Market data: instruments, daily bars, validation, providers (docs/finance-trading-intel.md#market-data).

ANUM never invents a price or a bar. A bar comes from one of:

* a CSV file of daily bars the person imports (from their broker or a data service whose
  licence lets them keep the data); ``parse_bars_csv``;
* a recorded provider response, parsed by a provider adapter (``PROVIDERS``). No adapter
  fetches over the network in this release: every provider needs the owner to choose it and
  accept its terms first, and paid ones need a key (owner actions). Contract tests run the
  parsers against recorded fixtures;
* ``synthetic_bars``: generated test data for demonstrations and benchmarks, always labelled
  ``synthetic`` and never stored as a price a paper order can fill at.

Every bar keeps its provenance (source, provider, when it was fetched, whether the provider
calls it delayed). Bars are checked before they are stored: a bar that breaks a price rule
is refused, a gap is reported and never filled, an abnormal move is flagged.

Provider output is untrusted input: parsed by strict rules with size caps, never evaluated.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

from ..models import _Owned
from .indicators import Bar
from .markets import AssetClass, is_trading_day, venue

PRICE_PLACES = Decimal("0.0000000001")
VOLUME_PLACES = Decimal("0.00000001")
MAX_PRICE = Decimal("100000000")
MAX_BARS_PER_IMPORT = 10_000
MAX_IMPORT_BYTES = 2_000_000
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-/]{0,19}$")
# A close-to-close move above this is flagged as abnormal (not refused: real markets gap).
ABNORMAL_MOVE = {
    AssetClass.EQUITY: Decimal("0.25"), AssetClass.ETF: Decimal("0.15"), AssetClass.CRYPTO: Decimal("0.40"),
    AssetClass.FOREX: Decimal("0.08"), AssetClass.COMMODITY: Decimal("0.15"),
}


class MarketDataError(ValueError):
    """Data that cannot be used, with a message that says what to fix."""


class Instrument(_Owned):
    """One instrument in the person's instrument master."""

    id: str
    symbol: str
    name: str | None = None
    asset_class: AssetClass
    venue: str
    currency: str
    provider_symbols: dict[str, str] = Field(default_factory=dict)
    listed_on: date | None = None
    delisted_on: date | None = None
    created_at: datetime


class Candle(BaseModel):
    """One daily bar. Prices are ``Decimal``; ``volume`` is ``None`` when the source has none."""

    model_config = ConfigDict(frozen=True)

    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None

    def bar(self) -> Bar:
        return Bar(float(self.open), float(self.high), float(self.low), float(self.close),
                   float(self.volume) if self.volume is not None else None)


class StoredCandle(_Owned):
    id: str
    instrument_id: str
    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None
    source: str
    provider: str
    delayed: bool = True
    fetched_at: datetime

    def candle(self) -> Candle:
        return Candle(day=self.day, open=self.open, high=self.high, low=self.low, close=self.close, volume=self.volume)


def normalize_symbol(value: str) -> str:
    symbol = (value or "").strip().upper()
    if not SYMBOL.fullmatch(symbol):
        raise MarketDataError("A symbol is 1 to 20 letters, digits, dots, dashes or a slash, such as EXMPL or BTC/USD.")
    return symbol


def to_price(value: object, field: str = "price") -> Decimal:
    try:
        number = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise MarketDataError(f"The {field} {str(value)[:20]!r} is not a number.") from None
    if not number.is_finite() or number <= 0 or number > MAX_PRICE:
        raise MarketDataError(f"The {field} must be above 0 and at most {MAX_PRICE}.")
    return number.quantize(PRICE_PLACES)


def to_volume(value: object) -> Decimal | None:
    text = str(value if value is not None else "").strip().replace(",", "")
    if text in {"", "-", "null", "None"}:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        raise MarketDataError(f"The volume {text[:20]!r} is not a number.") from None
    if not number.is_finite() or number < 0:
        raise MarketDataError("A volume is 0 or more.")
    return number.quantize(VOLUME_PLACES)


def make_candle(day: object, o: object, h: object, low: object, c: object, v: object = None) -> Candle:
    try:
        when = day if isinstance(day, date) else date.fromisoformat(str(day).strip()[:10])
    except ValueError:
        raise MarketDataError(f"The date {str(day)[:20]!r} is not YYYY-MM-DD.") from None
    candle = Candle(day=when, open=to_price(o, "open"), high=to_price(h, "high"), low=to_price(low, "low"),
                    close=to_price(c, "close"), volume=to_volume(v))
    if candle.high < max(candle.open, candle.close, candle.low) or candle.low > min(candle.open, candle.close):
        raise MarketDataError(f"The bar on {when.isoformat()} is inconsistent: the high must be the highest price and the low the lowest.")
    return candle


@dataclass(frozen=True)
class Issue:
    code: str  # out_of_order, duplicate, future, gap, abnormal_move, zero_volume
    day: date
    message: str
    blocking: bool


def check_series(candles: Sequence[Candle], asset_class: AssetClass, venue_code: str, today: date) -> list[Issue]:
    """Problems in a series, oldest first. Blocking issues stop an import; others are reported."""
    issues: list[Issue] = []
    v = venue(venue_code)
    limit = ABNORMAL_MOVE[asset_class]
    seen: set[date] = set()
    for i, candle in enumerate(candles):
        if candle.day in seen:
            issues.append(Issue("duplicate", candle.day, "Two bars for the same day.", True))
        seen.add(candle.day)
        if candle.day > today:
            issues.append(Issue("future", candle.day, "A bar dated in the future.", True))
        if i == 0:
            continue
        prev = candles[i - 1]
        if candle.day <= prev.day:
            issues.append(Issue("out_of_order", candle.day, "Bars must be in date order, oldest first.", True))
            continue
        missing, day = 0, prev.day + timedelta(days=1)
        while day < candle.day and missing < 400:
            if is_trading_day(v, day):
                missing += 1
            day += timedelta(days=1)
        if missing:
            issues.append(Issue("gap", candle.day, f"{missing} trading day(s) missing before this bar (not filled in).", False))
        move = abs(candle.close / prev.close - 1)
        if move > limit:
            issues.append(Issue("abnormal_move", candle.day, f"Close moved {move * 100:.1f} percent in one bar: check for a split or a bad value.", False))
        if candle.volume is not None and candle.volume == 0 and asset_class != AssetClass.FOREX:
            issues.append(Issue("zero_volume", candle.day, "No volume traded: a simulated order cannot fill on this bar.", False))
    return issues


_HEADER = {
    "date": ("date", "day", "time", "timestamp", "التاريخ"),
    "open": ("open", "o", "افتتاح"),
    "high": ("high", "h", "أعلى", "اعلى"),
    "low": ("low", "l", "أدنى", "ادنى"),
    "close": ("close", "c", "adj close", "price", "last", "إغلاق", "اغلاق"),
    "volume": ("volume", "vol", "v", "الحجم"),
}


def parse_bars_csv(text: str) -> list[Candle]:
    """Daily bars from a CSV with a header row: date, open, high, low, close and optional volume."""
    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise MarketDataError("The file is over 2 MB: export a shorter period.")
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise MarketDataError("The file is empty.")
    header = [cell.strip().lower() for cell in rows[0]]
    columns: dict[str, int] = {}
    for key, names in _HEADER.items():
        for index, name in enumerate(header):
            if name in names and key not in columns:
                columns[key] = index
    missing = [k for k in ("date", "open", "high", "low", "close") if k not in columns]
    if missing:
        raise MarketDataError(f"Missing column(s): {', '.join(missing)}. The header needs date, open, high, low, close (and volume).")
    if len(rows) - 1 > MAX_BARS_PER_IMPORT:
        raise MarketDataError(f"At most {MAX_BARS_PER_IMPORT} bars per import.")
    out: list[Candle] = []
    for number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        try:
            cells = {k: row[i] for k, i in columns.items()}
        except IndexError:
            raise MarketDataError(f"Row {number} has too few cells.") from None
        try:
            out.append(make_candle(cells["date"], cells["open"], cells["high"], cells["low"], cells["close"], cells.get("volume")))
        except MarketDataError as exc:
            raise MarketDataError(f"Row {number}: {exc}") from None
    out.sort(key=lambda c: c.day)
    return out


# -- providers -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    name: str
    markets: tuple[str, ...]
    cost: str
    needs_key: bool
    delayed: bool
    terms: str
    built: bool  # a parser exists and is contract-tested; network fetch is never on in this release
    owner_action: str


PROVIDERS: dict[str, ProviderInfo] = {
    p.key: p
    for p in (
        ProviderInfo("csv_import", "Your own CSV file", ("any",), "Free", False, True,
                     "Data you are allowed to keep (your broker's export or a licensed source).", True, ""),
        ProviderInfo("binance_public", "Binance public market data (daily klines)", ("crypto",), "Free, no key", False, False,
                     "Public market data under Binance's terms of use; availability in the UAE and personal-use rights must be checked.",
                     True, "Accept the terms and allow HTTPS egress to api.binance.com before ANUM fetches it."),
        ProviderInfo("stooq", "Stooq daily history (CSV)", ("equity", "etf", "forex", "commodity"), "Free, no key", False, True,
                     "End-of-day data for personal use; redistribution is not allowed.", True,
                     "Accept the terms and allow HTTPS egress to stooq.com before ANUM fetches it."),
        ProviderInfo("alpha_vantage", "Alpha Vantage", ("equity", "etf", "forex", "crypto"), "Free tier 25 requests a day; paid plans",
                     True, True, "Key per person; free tier limits; commercial use needs a paid plan.", False,
                     "Sign up, store the key encrypted under Finance, Connections; ANUM then builds and tests the adapter."),
        ProviderInfo("twelve_data", "Twelve Data", ("equity", "etf", "forex", "crypto", "commodity"), "Free tier 800 requests a day; paid plans",
                     True, True, "Key per person; Gulf exchanges on paid plans.", False, "Choose a plan and provide the key."),
        ProviderInfo("tadawul_dfm_adx", "Saudi Exchange, DFM and ADX data", ("equity",), "Paid licence from the exchange or a vendor",
                     True, True, "Exchange data licences; no free source allows storing it.", False,
                     "Buy a data licence (the exchange or a vendor such as Refinitiv or ICE) and provide its API documentation."),
    )
}


def parse_binance_klines(payload: str) -> list[Candle]:
    """Binance ``GET /api/v3/klines?interval=1d`` response: an array of arrays (open time in ms first)."""
    if len(payload) > 1_000_000:
        raise MarketDataError("The response is too large.")
    try:
        rows = json.loads(payload)
    except json.JSONDecodeError:
        raise MarketDataError("The response is not JSON.") from None
    if not isinstance(rows, list) or len(rows) > 1000:
        raise MarketDataError("Expected a list of at most 1000 bars.")
    out: list[Candle] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6 or not isinstance(row[0], int):
            raise MarketDataError("A bar has an unexpected shape.")
        day = date(1970, 1, 1) + timedelta(milliseconds=row[0])
        out.append(make_candle(day, row[1], row[2], row[3], row[4], row[5]))
    return out


def parse_provider(provider: str, payload: str) -> list[Candle]:
    if provider in {"csv_import", "stooq"}:
        return parse_bars_csv(payload)
    if provider == "binance_public":
        return parse_binance_klines(payload)
    info = PROVIDERS.get(provider)
    if info is None:
        raise MarketDataError(f"Unknown provider {provider!r}.")
    raise MarketDataError(f"{info.name} is not built: {info.owner_action}")


# -- synthetic data ---------------------------------------------------------------------------------

SYNTHETIC_SOURCE = "synthetic: generated test data, not market data"


class _SeededNormal:
    """Reproducible normal draws from SHA-256 of (seed, counter) via Box-Muller.

    Synthetic bars must be identical for a seed on every Python version, and they are never used
    for anything secret, so a hash counter is enough and keeps the stdlib PRNG out of the code.
    """

    def __init__(self, seed: int) -> None:
        self._seed = int(seed).to_bytes(16, "big", signed=True)
        self._counter = 0
        self._spare: float | None = None

    def _uniform(self) -> float:
        self._counter += 1
        digest = hashlib.sha256(self._seed + self._counter.to_bytes(8, "big")).digest()
        # 53 random bits, shifted into (0, 1] so log() never sees zero.
        return (int.from_bytes(digest[:7], "big") >> 3) / float(1 << 53) + 2.0 ** -54

    def normal(self, mu: float, sigma: float) -> float:
        if self._spare is not None:
            z, self._spare = self._spare, None
        else:
            radius = math.sqrt(-2.0 * math.log(self._uniform()))
            angle = 2.0 * math.pi * self._uniform()
            z, self._spare = radius * math.cos(angle), radius * math.sin(angle)
        return mu + sigma * z


def synthetic_bars(seed: int, start: date, count: int, *, start_price: float = 100.0, drift: float = 0.0003,
                   vol: float = 0.012, trading_days: Iterable[int] = (0, 1, 2, 3, 4), regime_every: int = 0) -> list[Candle]:
    """Deterministic geometric random walk bars for demonstrations and tests. Never market data.

    ``regime_every`` flips the drift sign every that many bars so trend and range periods alternate.
    """
    rng = _SeededNormal(seed)
    days = frozenset(trading_days)
    out: list[Candle] = []
    price, day, mu = start_price, start, drift
    while len(out) < count:
        if day.weekday() in days:
            if regime_every and len(out) and len(out) % regime_every == 0:
                mu = -mu
            open_ = price * math.exp(rng.normal(0, vol / 4))
            close = open_ * math.exp(rng.normal(mu, vol))
            high = max(open_, close) * math.exp(abs(rng.normal(0, vol / 2)))
            low = min(open_, close) * math.exp(-abs(rng.normal(0, vol / 2)))
            volume = int(1_000_000 * math.exp(rng.normal(0, 0.3)))
            out.append(make_candle(day, f"{open_:.4f}", f"{high:.4f}", f"{low:.4f}", f"{close:.4f}", volume))
            price = close
        day += timedelta(days=1)
    return out
