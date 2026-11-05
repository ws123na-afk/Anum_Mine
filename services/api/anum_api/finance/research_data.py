"""Symbol research: real data, its sources, and the figures computed from it (docs/finance-research.md).

Every figure the research screens show is a :class:`Fact`: a value with the source it came from
and the date it describes, or ``None`` with the plain reason it is not available. Nothing here
invents, fills or estimates a number: a gap in the data stays a gap, a period the history does
not cover says so, and a source that is not reachable says which one and why.

Sources (each documented in docs/finance-research.md#sources, reached only on fixed hosts):

* **IBKR historical bars** through the owner's own IBKR connection (``ibkr_client.history``):
  weekly closes for up to 10 years. Split-adjusted by IBKR, **not** dividend-adjusted.
* **Stooq daily CSV** (``stooq.com``, free, no key, personal use only): used when IBKR is not
  connected or has no history. Stooq does not state whether its closes include dividends, so
  ANUM labels them "adjustment not stated by the source".
* **SEC EDGAR N-PORT** (``www.sec.gov``, ``data.sec.gov``, free, no key; SEC asks every automated
  reader for a contact e-mail, ``ANUM_FINANCE_RESEARCH_CONTACT``): the fund's latest monthly
  portfolio report: holdings count, top holdings, net assets, the registrant (issuer) and the
  report date.
* **Recent news** from the server's own private web search (SearXNG, ``ANUM_SEARXNG_URL``), news
  category. Titles and links only; they are untrusted text and never reach the model prompt.
* **Shariah screening**: the fund's own index and issuer, from ``SHARIAH_FUNDS``; ANUM does not
  certify any fund.

What is **not available** from these free sources, said plainly on screen: the expense ratio and
the fund's own dividend (distribution) history and yield. The owner's own dividends from a fund
come from their IBKR Flex statement when it is connected.

Outside data is untrusted: parsed with strict rules and size caps, never evaluated; XML through
``defusedxml``; no redirects; fixed hosts only (``ALLOWED_HOSTS``) through the SSRF-guarded,
DNS-pinned transport (``model_egress.PinnedEgressTransport``) outside local and test.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

STOOQ_HOST = "stooq.com"
SEC_HOSTS = ("www.sec.gov", "data.sec.gov")
ALLOWED_HOSTS = frozenset({STOOQ_HOST, *SEC_HOSTS})
MAX_CSV_BYTES = 2_000_000
MAX_JSON_BYTES = 8_000_000
MAX_XML_BYTES = 12_000_000
MAX_NPORT_TRIES = 12  # a trust files one N-PORT per fund; at most this many are opened to find ours
PERIODS = (1, 3, 5, 10)
TOP_HOLDINGS = 10
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")

# Tests put an ``httpx.MockTransport`` here; production uses the pinned egress transport.
TRANSPORT: httpx.AsyncBaseTransport | None = None

# The Shariah-screened funds on the default list (owner decision, 2 Nov 2026). Index and issuer as
# the fund names state them; the issuer's own Shariah adviser is named in its prospectus. ANUM
# shows the issuer's label and never certifies a fund. Source: the fund names and issuers as
# listed at IBKR and in published comparisons (docs/finance-live-trading.md#shariah-screening).
SHARIAH_FUNDS: dict[str, dict[str, str]] = {
    "SPUS": {"issuer": "SP Funds", "screen": "S&P 500 Shariah Industry Exclusions index (S&P Dow Jones Indices) and the issuer's Shariah adviser"},
    "SPRE": {"issuer": "SP Funds", "screen": "S&P Global REIT Shariah index (S&P Dow Jones Indices) and the issuer's Shariah adviser"},
    "SPSK": {"issuer": "SP Funds", "screen": "Dow Jones Sukuk index (S&P Dow Jones Indices) and the issuer's Shariah adviser"},
    "HLAL": {"issuer": "Wahed Invest", "screen": "FTSE USA Shariah index (FTSE Russell) and the issuer's Shariah board"},
    "UMMA": {"issuer": "Wahed Invest", "screen": "Dow Jones Islamic Market index (S&P Dow Jones Indices) and the issuer's Shariah board"},
}
SHARIAH_SOURCE = "Fund name and issuer as published (the issuer's prospectus names its Shariah adviser)"


class SourceError(RuntimeError):
    """A source that could not be read. ``message`` is safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


# -- facts -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    """One figure with its provenance, or ``value is None`` with the reason it is not available."""

    value: str | None
    source: str | None = None
    as_of: str | None = None  # the date the figure describes (ISO)
    unavailable: str | None = None
    unit: str | None = None  # "percent", "usd", "count", "months"

    @classmethod
    def missing(cls, reason: str) -> Fact:
        return cls(value=None, unavailable=reason)

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "source": self.source, "as_of": self.as_of, "unavailable": self.unavailable, "unit": self.unit}


@dataclass(frozen=True)
class PricePoint:
    day: date
    close: Decimal


@dataclass
class PriceHistory:
    source: str  # "ibkr_history" or "stooq"
    label: str
    adjusted: str  # what the closes include, in plain words
    fetched_at: datetime
    points: list[PricePoint] = field(default_factory=list)


def pct(value: Decimal) -> str:
    """A percentage with one decimal, as shown everywhere (``-33.8``)."""
    return str((value * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def _on_or_before(points: list[PricePoint], day: date) -> PricePoint | None:
    found = None
    for point in points:
        if point.day > day:
            break
        found = point
    return found


def monthly(points: list[PricePoint]) -> list[PricePoint]:
    """The last close of each calendar month, oldest first (the series the backtests use)."""
    by_month: dict[tuple[int, int], PricePoint] = {}
    for point in sorted(points, key=lambda p: p.day):
        by_month[(point.day.year, point.day.month)] = point
    return [by_month[key] for key in sorted(by_month)]


def returns(history: PriceHistory) -> dict[str, dict[str, Any]]:
    """Total and yearly (annualised) price change over 1, 3, 5 and 10 years, where the history covers it."""
    out: dict[str, dict[str, Any]] = {}
    points = history.points
    if len(points) < 2:
        for years in PERIODS:
            out[f"{years}y"] = {"total": Fact.missing("No price history.").as_dict(), "yearly": Fact.missing("No price history.").as_dict()}
        return out
    last = points[-1]
    first = points[0]
    for years in PERIODS:
        start_day = _years_before(last.day, years)
        start = _on_or_before(points, start_day)
        if start is None or first.day > start_day + timedelta(days=10):
            reason = f"The price history starts on {first.day.isoformat()}, less than {years} year{'s' if years > 1 else ''} before {last.day.isoformat()}."
            out[f"{years}y"] = {"total": Fact.missing(reason).as_dict(), "yearly": Fact.missing(reason).as_dict()}
            continue
        total = last.close / start.close - 1
        with localcontext() as ctx:
            ctx.prec = 28
            yearly = (last.close / start.close) ** (Decimal(1) / Decimal(years)) - 1 if years > 1 else total
        as_of = f"{start.day.isoformat()} to {last.day.isoformat()}"
        out[f"{years}y"] = {"total": Fact(pct(total), history.source, as_of, unit="percent").as_dict(),
                            "yearly": Fact(pct(yearly), history.source, as_of, unit="percent").as_dict()}
    return out


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def drawdowns(points: list[PricePoint], limit: int = 3) -> list[dict[str, Any]]:
    """The worst falls from a high: peak, low, fall in percent, and when (and if) the price came back."""
    episodes: list[dict[str, Any]] = []
    if len(points) < 2:
        return episodes
    peak = points[0]
    trough: PricePoint | None = None
    for point in points[1:]:
        if point.close >= peak.close:
            if trough is not None and trough.close < peak.close:
                episodes.append({"peak": peak, "trough": trough, "recovered": point})
            peak, trough = point, None
        elif trough is None or point.close < trough.close:
            trough = point
    if trough is not None and trough.close < peak.close:
        episodes.append({"peak": peak, "trough": trough, "recovered": None})
    episodes.sort(key=lambda e: (e["trough"].close / e["peak"].close, e["peak"].day))
    result = []
    for e in episodes[:limit]:
        peak, trough, back = e["peak"], e["trough"], e["recovered"]
        result.append({
            "peak_date": peak.day.isoformat(), "peak": str(peak.close), "trough_date": trough.day.isoformat(), "trough": str(trough.close),
            "fall_percent": pct(trough.close / peak.close - 1), "recovered_date": back.day.isoformat() if back else None,
            "months_to_recover": _months_between(trough.day, back.day) if back else None,
            "months_falling": _months_between(peak.day, trough.day),
        })
    return result


def volatility(points: list[PricePoint], source: str) -> dict[str, dict[str, Any]]:
    """Yearly volatility from monthly price changes, plus the worst and best month."""
    series = monthly(points)
    changes = [(series[i].close / series[i - 1].close - 1, series[i].day) for i in range(1, len(series))]
    if len(changes) < 12:
        reason = "Fewer than 12 months of prices."
        return {"yearly": Fact.missing(reason).as_dict(), "worst_month": Fact.missing(reason).as_dict(), "best_month": Fact.missing(reason).as_dict(),
                "months": len(changes)}  # type: ignore[dict-item]
    values = [c for c, _ in changes]
    mean = sum(values, Decimal(0)) / len(values)
    variance = sum(((v - mean) ** 2 for v in values), Decimal(0)) / (len(values) - 1)
    with localcontext() as ctx:
        ctx.prec = 28
        yearly = variance.sqrt() * Decimal(12).sqrt()
    span = f"{series[0].day.isoformat()} to {series[-1].day.isoformat()}"
    worst = min(changes, key=lambda c: c[0])
    best = max(changes, key=lambda c: c[0])
    return {
        "yearly": Fact(pct(yearly), source, span, unit="percent").as_dict(),
        "worst_month": Fact(pct(worst[0]), source, worst[1].strftime("%Y-%m"), unit="percent").as_dict(),
        "best_month": Fact(pct(best[0]), source, best[1].strftime("%Y-%m"), unit="percent").as_dict(),
        "months": len(changes),  # type: ignore[dict-item]
    }


def shariah(symbol: str) -> dict[str, Any]:
    fund = SHARIAH_FUNDS.get(symbol)
    if fund is None:
        return {"screened": False, "label": "Not Shariah-screened", "screen": None, "issuer": None, "source": None,
                "note": "This fund is not on the Shariah-screened list. ANUM does not certify any fund."}
    return {"screened": True, "label": "Shariah-screened", "screen": fund["screen"], "issuer": fund["issuer"], "source": SHARIAH_SOURCE,
            "note": "Screened by the fund's index provider and the issuer's own Shariah adviser. ANUM does not certify any fund."}


# -- parsers (untrusted input) ---------------------------------------------------------------------


def _price(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def parse_ibkr_history(raw: Any) -> list[PricePoint]:
    """IBKR ``/iserver/marketdata/history``: ``{"data": [{"t": ms, "c": close, ...}]}``."""
    rows = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise SourceError("bad_response", "IBKR's price history could not be read.")
    out: dict[date, PricePoint] = {}
    for row in rows[:2000]:
        if not isinstance(row, dict) or not isinstance(row.get("t"), (int, float)) or isinstance(row.get("t"), bool):
            continue
        close = _price(row.get("c"))
        if close is None:
            continue
        day = datetime.fromtimestamp(row["t"] / 1000, tz=UTC).date()
        out[day] = PricePoint(day, close)
    return [out[d] for d in sorted(out)]


def parse_stooq_csv(text: str) -> list[PricePoint]:
    """Stooq ``/q/d/l/?s=<symbol>.us&i=d``: ``Date,Open,High,Low,Close,Volume`` rows, oldest first."""
    if len(text) > MAX_CSV_BYTES:
        raise SourceError("too_large", "Stooq's answer was larger than ANUM accepts.")
    head = text.lstrip()[:200].lower()
    if not head.startswith("date,"):
        if "no data" in head:
            raise SourceError("no_data", "Stooq has no price history for this symbol.")
        raise SourceError("not_csv", "Stooq did not send a price file (it may limit free downloads or ask for a key).")
    reader = csv.DictReader(io.StringIO(text))
    out: dict[date, PricePoint] = {}
    for row in reader:
        try:
            day = date.fromisoformat(str(row.get("Date", "")).strip())
        except ValueError:
            continue
        close = _price(row.get("Close"))
        if close is not None:
            out[day] = PricePoint(day, close)
        if len(out) > 20_000:
            break
    return [out[d] for d in sorted(out)]


def parse_sec_series(raw: Any, symbol: str) -> tuple[int, str] | None:
    """``company_tickers_mf.json``: ``{"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[...]]}`` -> (cik, series id)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("fields"), list) or not isinstance(raw.get("data"), list):
        raise SourceError("bad_response", "The SEC fund list could not be read.")
    fields = [str(f) for f in raw["fields"]]
    try:
        i_cik, i_series, i_symbol = fields.index("cik"), fields.index("seriesId"), fields.index("symbol")
    except ValueError:
        raise SourceError("bad_response", "The SEC fund list has an unexpected shape.") from None
    for row in raw["data"]:
        if isinstance(row, list) and len(row) > max(i_cik, i_series, i_symbol) and str(row[i_symbol]).upper() == symbol:
            try:
                return int(row[i_cik]), str(row[i_series])
            except (TypeError, ValueError):
                return None
    return None


def parse_sec_nport_filings(raw: Any, limit: int = MAX_NPORT_TRIES) -> list[dict[str, str]]:
    """``submissions/CIK##########.json``: the newest N-PORT-P filings (accession, filing and report dates)."""
    recent = raw.get("filings", {}).get("recent") if isinstance(raw, dict) else None
    if not isinstance(recent, dict):
        raise SourceError("bad_response", "The SEC filing list could not be read.")
    forms, numbers = recent.get("form") or [], recent.get("accessionNumber") or []
    filed, reported, docs = recent.get("filingDate") or [], recent.get("reportDate") or [], recent.get("primaryDocument") or []
    out = []
    for i, form in enumerate(forms):
        if form == "NPORT-P" and i < len(numbers) and re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", str(numbers[i])):
            doc = str(docs[i]) if i < len(docs) else "primary_doc.xml"
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", doc):
                continue
            out.append({"accession": str(numbers[i]), "filed": str(filed[i]) if i < len(filed) else "",
                        "report_date": str(reported[i]) if i < len(reported) else "", "document": doc})
            if len(out) >= limit:
                break
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node: Any, name: str) -> Any:
    for item in list(node):
        if _local(item.tag) == name:
            return item
    return None


def _text(node: Any, *path: str) -> str | None:
    for name in path:
        if node is None:
            return None
        node = _child(node, name)
    return node.text.strip() if node is not None and node.text else None


def parse_nport(data: bytes) -> dict[str, Any]:
    """One N-PORT-P ``primary_doc.xml``: series id, registrant, report date, net assets and holdings."""
    from defusedxml import ElementTree
    from defusedxml.common import DefusedXmlException

    if len(data) > MAX_XML_BYTES:
        raise SourceError("too_large", "The SEC report was larger than ANUM accepts.")
    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, DefusedXmlException, ValueError):
        raise SourceError("bad_response", "The SEC report could not be read.") from None
    form = _child(root, "formData")
    gen = _child(form, "genInfo") if form is not None else None
    fund = _child(form, "fundInfo") if form is not None else None
    if gen is None:
        raise SourceError("bad_response", "The SEC report has no general section.")
    holdings: list[tuple[Decimal, str]] = []
    invest = _child(form, "invstOrSecs")
    count = 0
    for item in list(invest) if invest is not None else []:
        if _local(item.tag) != "invstOrSec":
            continue
        count += 1
        name = (_text(item, "name") or _text(item, "title") or "").strip()
        try:
            share = Decimal(_text(item, "pctVal") or "")
        except (InvalidOperation, ValueError):
            continue
        if name and share.is_finite():
            holdings.append((share, " ".join(name.split())[:120]))
    holdings.sort(key=lambda h: (-h[0], h[1]))
    net = _text(fund, "netAssets") if fund is not None else None
    try:
        net_assets = str(Decimal(net).quantize(Decimal(1))) if net else None
    except (InvalidOperation, ValueError):
        net_assets = None
    return {
        "series_id": _text(gen, "seriesId"), "registrant": (_text(gen, "regName") or "")[:160] or None,
        "series_name": (_text(gen, "seriesName") or "")[:160] or None, "report_date": _text(gen, "repPdDate"),
        "net_assets": net_assets, "holdings_count": count,
        "top_holdings": [{"name": n, "percent": str(s.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))} for s, n in holdings[:TOP_HOLDINGS]],
    }


def parse_news(raw: Any, limit: int = 5) -> list[dict[str, str | None]]:
    """SearXNG ``format=json`` news results: title, link, source host and published date only."""
    from ..web_search import parse_results

    results, _ = parse_results(raw, limit)
    dates: dict[str, str | None] = {}
    for item in raw.get("results", []) if isinstance(raw, dict) else []:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            published = item.get("publishedDate")
            dates[item["url"].strip()] = str(published)[:10] if isinstance(published, str) and re.match(r"\d{4}-\d{2}-\d{2}", published) else None
    return [{"title": r["title"], "url": r["url"], "source": urlsplit(r["url"]).hostname, "published": dates.get(r["url"])} for r in results]


# -- fetching (fixed hosts, no redirects, capped) --------------------------------------------------


def _client(timeout: float) -> httpx.AsyncClient:
    if TRANSPORT is not None:
        return httpx.AsyncClient(transport=TRANSPORT, timeout=timeout, follow_redirects=False)
    from ..model_egress import EgressPolicy, PinnedEgressTransport

    policy = EgressPolicy.from_settings()
    if policy.enforce:
        return httpx.AsyncClient(transport=PinnedEgressTransport(EgressPolicy(enforce=True)), timeout=timeout, follow_redirects=False, trust_env=False)
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


async def fetch(client: httpx.AsyncClient, url: str, *, headers: dict[str, str] | None = None, cap: int = MAX_JSON_BYTES) -> bytes:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.port not in (None, 443) or parts.username or parts.fragment:
        raise SourceError("host_refused", "ANUM only reads its fixed research sources.")
    try:
        async with client.stream("GET", url, headers={"Accept-Encoding": "identity", **(headers or {})}) as response:
            if 300 <= response.status_code < 400:
                raise SourceError("redirect_refused", f"{parts.hostname} answered with a redirect. ANUM does not follow redirects.")
            if response.status_code == 429:
                raise SourceError("rate_limited", f"{parts.hostname} asked ANUM to slow down. Try again later.")
            if response.status_code != 200:
                raise SourceError("http_error", f"{parts.hostname} answered HTTP {response.status_code}.")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > cap:
                    raise SourceError("too_large", f"{parts.hostname} sent more data than ANUM accepts.")
            return bytes(body)
    except httpx.TimeoutException:
        raise SourceError("timeout", f"{parts.hostname} did not answer in time.") from None
    except httpx.HTTPError:
        raise SourceError("unreachable", f"ANUM could not reach {parts.hostname}.") from None


def stooq_url(symbol: str) -> str:
    return f"https://{STOOQ_HOST}/q/d/l/?" + urlencode({"s": symbol.lower() + ".us", "i": "d"})


async def stooq_history(client: httpx.AsyncClient, symbol: str, now: datetime) -> PriceHistory:
    data = await fetch(client, stooq_url(symbol), cap=MAX_CSV_BYTES)
    points = parse_stooq_csv(data.decode("utf-8", errors="replace"))
    cutoff = _years_before(now.date(), 11)
    return PriceHistory("stooq", "Stooq daily closes", "Adjustment for dividends not stated by the source", now, [p for p in points if p.day >= cutoff])


async def sec_fund_facts(client: httpx.AsyncClient, symbol: str, contact: str) -> dict[str, Any]:
    """The fund's newest N-PORT-P: finds the fund's series by its ticker, then its own report."""
    headers = {"User-Agent": f"ANUM finance research {contact}", "Accept": "application/json"}
    listing = json.loads(await fetch(client, "https://www.sec.gov/files/company_tickers_mf.json", headers=headers))
    found = parse_sec_series(listing, symbol)
    if found is None:
        raise SourceError("not_listed", "The SEC fund list has no fund with this ticker.")
    cik, series = found
    submissions = json.loads(await fetch(client, f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=headers))
    for filing in parse_sec_nport_filings(submissions):
        folder = filing["accession"].replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{filing['document']}"
        report = parse_nport(await fetch(client, url, headers={**headers, "Accept": "application/xml"}, cap=MAX_XML_BYTES))
        if report["series_id"] == series:
            return {**report, "filed": filing["filed"], "url": url}
    raise SourceError("no_report", "No recent N-PORT report for this fund was found at the SEC.")


async def news(base_url: str, symbol: str, name: str, timeout: float) -> list[dict[str, str | None]]:
    """Recent news titles from the server's own SearXNG (operator-configured address only)."""
    from ..web_search import normalize_searxng_url

    base = normalize_searxng_url(base_url)
    params = {"q": f"{symbol} {name}".strip()[:200], "format": "json", "categories": "news", "time_range": "month", "safesearch": "1"}
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=False) if TRANSPORT is None else httpx.AsyncClient(transport=TRANSPORT, timeout=timeout)
    async with client:
        try:
            response = await client.get(f"{base}/search", params=params, headers={"accept": "application/json", "accept-encoding": "identity"})
        except httpx.HTTPError:
            raise SourceError("unreachable", "The private web search did not answer.") from None
    if response.status_code != 200 or len(response.content) > 1_000_000:
        raise SourceError("http_error", f"The private web search answered HTTP {response.status_code}.")
    try:
        return parse_news(response.json())
    except ValueError:
        raise SourceError("bad_response", "The private web search answer could not be read.") from None
