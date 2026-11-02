"""Symbol research flows: gather, cache, AI summary, backtests, paper run, portfolio (docs/finance-research.md).

Repository rules kept here (CLAUDE.md): routes are plain ``def`` (FastAPI runs them in a worker thread);
every database read is one short unit of work that is **closed before** IBKR, a public source or the
model is called; results are written in a new short unit of work afterwards.

Cache: the gathered figures for one symbol are kept in this process per tenant, workspace and person
for ``ANUM_FINANCE_RESEARCH_CACHE_MINUTES`` (default 30). Finance is owner-only, so one person's
cache (which may hold figures from their own IBKR account) is never served to another, even in the
same workspace. "Refresh" gathers again.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from ..schemas import TenantContext, new_id
from . import research_backtest as bt
from . import research_data as rd
from . import research_summary as rs
from .config import finance_config
from .ibkr_client import IbkrError
from .ibkr_rules import SHARIAH_ETFS, US_ETFS, BrokerSettings, Environment
from .ibkr_service import BrokerRefusal, PlanInput, _client, _save_settings, _settings, save_plans
from .ibkr_store import open_ibkr_stores
from .intel.records import Agent, AgentDecision

DEFAULT_SYMBOLS = ("SPUS", "HLAL", "UMMA", "SPRE", "SPSK")
SUMMARY_ACTION = "symbol_research_summary"
DISCLAIMER = ("Research from public and broker data, for your own decisions. It is not financial advice and there are no "
              "guaranteed returns. Prices go down as well as up.")
US_WITHHOLDING_PERCENT = "30"
MAX_CACHE = 500
SOURCES = {
    "ibkr_history": {"name": "IBKR historical prices (your connection)", "url": "https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/"},
    "stooq": {"name": "Stooq daily prices (free, personal use)", "url": "https://stooq.com"},
    "sec_nport": {"name": "SEC EDGAR N-PORT fund reports", "url": "https://www.sec.gov/edgar/search/"},
    "ibkr_flex": {"name": "Your IBKR Flex statement", "url": None},
    "news": {"name": "Recent news from the server's private web search", "url": None},
    "shariah": {"name": rd.SHARIAH_SOURCE, "url": None},
}


@dataclass
class _Entry:
    expires: float
    report: dict[str, Any]
    monthly: list[rd.PricePoint]


_cache: dict[tuple[str, str, str, str], _Entry] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _key(context: TenantContext, symbol: str) -> tuple[str, str, str, str]:
    return (context.tenant_id, context.workspace_id, context.user_id, symbol)


def normalize(symbol: str, settings: BrokerSettings | None = None) -> str:
    value = (symbol or "").strip().upper()
    if not rd.SYMBOL.fullmatch(value):
        raise BrokerRefusal("symbol", "A fund symbol is letters, like SPUS.", status=422)
    known = value in US_ETFS or (settings is not None and settings.allowed_symbol(value) is not None)
    if not known:
        raise BrokerRefusal("symbol_unknown", f"{value} is not on ANUM's list of US ETFs. Research covers the listed ETFs.", status=422)
    return value


def _name(symbol: str, settings: BrokerSettings) -> str:
    allowed = settings.allowed_symbol(symbol)
    return (allowed.name if allowed and allowed.name else US_ETFS.get(symbol, symbol))


def symbols_view(context: TenantContext, now: datetime) -> dict[str, Any]:
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
    allowed = {a.symbol for a in settings.allowed}
    order = list(dict.fromkeys([*[a.symbol for a in settings.allowed], *DEFAULT_SYMBOLS]))
    items = [{"symbol": s, "name": _name(s, settings), "allowed": s in allowed, "shariah": rd.shariah(s)} for s in order]
    return {"mode": settings.mode(now).value, "symbols": items, "etfs": US_ETFS, "shariah_etfs": sorted(SHARIAH_ETFS),
            "limits": {"max_order": str(settings.limits.max_order), "max_capital": str(settings.limits.max_capital)},
            "summary": {"daily_limit": finance_config.research_summary_daily_limit}, "disclaimer": DISCLAIMER,
            "plans": [p.model_dump(mode="json") for p in settings.buy_plans]}


# -- gathering ---------------------------------------------------------------------------------------


def _ibkr_history(settings: BrokerSettings, connection: Any, symbol: str, now: datetime) -> rd.PriceHistory:
    if connection is None:
        raise rd.SourceError("not_connected", "IBKR is not connected, so its price history was not used.")
    if connection.verified_at is None:
        raise rd.SourceError("not_verified", "Your IBKR connection is not checked yet.")
    try:
        client = _client(connection)
    except BrokerRefusal as exc:
        raise rd.SourceError("not_connected", exc.message) from None
    try:
        allowed = settings.allowed_symbol(symbol)
        conid = allowed.conid if allowed else next((m.conid for m in client.find_stock(symbol) if m.is_us), None)
        if conid is None:
            raise rd.SourceError("no_contract", "IBKR has no US listing for this symbol.")
        points = rd.parse_ibkr_history(client.history(conid))
    except IbkrError as exc:
        raise rd.SourceError(exc.code, exc.message) from None
    if len(points) < 2:
        raise rd.SourceError("no_data", "IBKR sent no price history for this fund.")
    return rd.PriceHistory("ibkr_history", "IBKR weekly closes", "Split-adjusted by IBKR; dividends not included", now, points)


async def _remote(symbol: str, name: str, now: datetime, need_prices: bool) -> dict[str, Any]:
    """Stooq (when IBKR gave no prices), the SEC report and news, concurrently. Each failure is a reason."""
    timeout = finance_config.research_timeout_seconds
    from ..settings import settings as app_settings

    async def prices() -> Any:
        if not need_prices:
            return None
        async with rd._client(timeout) as client:
            return await rd.stooq_history(client, symbol, now)

    async def fund() -> Any:
        contact = finance_config.research_contact
        if not contact:
            raise rd.SourceError("no_contact", "The SEC asks automated readers for a contact e-mail; the operator has not set "
                                               "ANUM_FINANCE_RESEARCH_CONTACT, so SEC fund reports are not read.")
        async with rd._client(timeout) as client:
            return await rd.sec_fund_facts(client, symbol, contact)

    async def headlines() -> Any:
        base = app_settings.searxng_url.strip()
        if not base:
            raise rd.SourceError("no_search", "Private web search is not set up on this server (ANUM_SEARXNG_URL).")
        return await rd.news(base, symbol, name, min(timeout, 10.0))

    async def guard(job: Any) -> Any:
        try:
            return await asyncio.wait_for(job(), timeout + 5)
        except rd.SourceError as exc:
            return exc
        except TimeoutError:
            return rd.SourceError("timeout", "The source did not answer in time.")
        except Exception:  # an unexpected answer never breaks the page: it becomes a reason
            return rd.SourceError("bad_response", "The source's answer could not be read.")

    got = await asyncio.gather(guard(prices), guard(fund), guard(headlines))
    return {"prices": got[0], "fund": got[1], "news": got[2]}


def _run_async(coro: Any) -> Any:
    result: dict[str, Any] = {}

    def work() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            result["error"] = exc

    worker = threading.Thread(target=work, name="finance-research", daemon=True)
    worker.start()
    worker.join(finance_config.research_timeout_seconds * 3 + 10)
    if worker.is_alive():
        raise BrokerRefusal("timeout", "The research sources did not answer in time. Try again.", status=504)
    if "error" in result:
        raise result["error"]
    return result["value"]


def _gather(context: TenantContext, symbol: str, now: datetime) -> _Entry:
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
        symbol = normalize(symbol, settings)
        mode = settings.mode(now)
        connection = store.active_connection(context, mode)
        statement = dict(connection.statement) if connection else {}
    name = _name(symbol, settings)
    reasons: list[dict[str, str]] = []
    history: rd.PriceHistory | None = None
    try:
        history = _ibkr_history(settings, connection, symbol, now)
    except rd.SourceError as exc:
        reasons.append({"source": "ibkr_history", "reason": exc.message})
    remote = _run_async(_remote(symbol, name, now, history is None))
    if history is None:
        if isinstance(remote["prices"], rd.PriceHistory) and len(remote["prices"].points) >= 2:
            history = remote["prices"]
        elif isinstance(remote["prices"], rd.SourceError):
            reasons.append({"source": "stooq", "reason": remote["prices"].message})
    report: dict[str, Any] = {"symbol": symbol, "name": name, "mode": mode.value, "shariah": rd.shariah(symbol), "gathered_at": now.isoformat(),
                              "expires_at": (now + timedelta(minutes=finance_config.research_cache_minutes)).isoformat(), "disclaimer": DISCLAIMER}
    points = history.points if history else []
    if history:
        last = points[-1]
        report["prices"] = {"source": history.source, "label": history.label, "adjusted": history.adjusted, "from": points[0].day.isoformat(),
                            "to": last.day.isoformat(), "points": len(points), "fetched_at": history.fetched_at.isoformat(),
                            "last_close": rd.Fact(rd.money(last.close), history.source, last.day.isoformat(), unit="usd").as_dict(), "unavailable": None}
        report["returns"] = rd.returns(history)
        report["drawdowns"] = {"source": history.source, "items": rd.drawdowns(points), "unavailable": None if len(points) > 1 else "No price history."}
        report["volatility"] = rd.volatility(points, history.source)
    else:
        why = " ".join(r["reason"] for r in reasons) or "No price source answered."
        report["prices"] = {"source": None, "unavailable": why, "last_close": rd.Fact.missing(why).as_dict()}
        report["returns"] = rd.returns(rd.PriceHistory("none", "", "", now))
        report["drawdowns"] = {"source": None, "items": [], "unavailable": why}
        report["volatility"] = rd.volatility([], "none")
    fund = remote["fund"]
    missing_expense = "Not available: no free machine-readable source for the expense ratio is wired; the fund's prospectus states it."
    if isinstance(fund, dict):
        as_of = fund.get("report_date")
        report["fund"] = {
            "source": "sec_nport", "url": fund.get("url"), "filed": fund.get("filed"),
            "issuer": rd.Fact(fund.get("registrant"), "sec_nport", as_of).as_dict() if fund.get("registrant") else rd.Fact.missing("Not in the report.").as_dict(),
            "holdings_count": rd.Fact(str(fund["holdings_count"]), "sec_nport", as_of, unit="count").as_dict(),
            "net_assets": rd.Fact(fund["net_assets"], "sec_nport", as_of, unit="usd").as_dict() if fund.get("net_assets") else rd.Fact.missing("Not in the report.").as_dict(),
            "top_holdings": {"items": fund.get("top_holdings", []), "source": "sec_nport", "as_of": as_of},
            "expense_ratio": rd.Fact.missing(missing_expense).as_dict(), "unavailable": None,
        }
    else:
        why = fund.message if isinstance(fund, rd.SourceError) else "The SEC report could not be read."
        reasons.append({"source": "sec_nport", "reason": why})
        report["fund"] = {"source": None, "unavailable": why, "issuer": rd.Fact.missing(why).as_dict(), "holdings_count": rd.Fact.missing(why).as_dict(),
                          "net_assets": rd.Fact.missing(why).as_dict(), "top_holdings": {"items": [], "source": None, "as_of": None},
                          "expense_ratio": rd.Fact.missing(missing_expense).as_dict()}
    mine = [d for d in statement.get("dividends", []) if isinstance(d, dict) and str(d.get("symbol", "")).upper() == symbol] if statement.get("ready") else []
    no_history = "Not available: no free source for the fund's own dividend history is wired. Your own dividends appear below once IBKR pays them."
    report["dividends"] = {
        "fund_history": rd.Fact.missing(no_history).as_dict(), "yield": rd.Fact.missing(no_history).as_dict(),
        "yours": {"items": [{"date": d.get("date"), "amount": d.get("amount"), "kind": d.get("kind")} for d in mine[:40]], "source": "ibkr_flex",
                  "as_of": statement.get("to_date"), "unavailable": None if statement.get("ready") else "Your IBKR Flex statement is not connected or not read yet."},
        "us_withholding_percent": US_WITHHOLDING_PERCENT,
        "withholding_note": "For a Saudi tax resident, US dividends are normally taxed 30 percent at source (no US income tax treaty).",
    }
    found = remote["news"]
    if isinstance(found, list):
        report["news"] = {"items": found, "source": "news", "unavailable": None if found else "No recent news found.", "untrusted": True}
    else:
        why = found.message if isinstance(found, rd.SourceError) else "News could not be read."
        report["news"] = {"items": [], "source": None, "unavailable": why, "untrusted": True}
    used = {report["prices"].get("source"), report["fund"].get("source"), report["news"].get("source"), "shariah",
            "ibkr_flex" if statement.get("ready") else None}
    report["sources"] = [{"key": k, **v, "used": k in used} for k, v in SOURCES.items()]
    report["source_problems"] = reasons
    report["data_hash"] = hashlib.sha256(rs.data_text(report).encode()).hexdigest()
    return _Entry(time.monotonic() + finance_config.research_cache_minutes * 60, report, rd.monthly(points))


def gather(context: TenantContext, symbol: str, now: datetime, *, refresh: bool = False) -> _Entry:
    with open_ibkr_stores(context) as (_, store):
        clean = normalize(symbol, _settings(store, context))
    key = _key(context, clean)
    with _cache_lock:
        found = _cache.get(key)
        if found and not refresh and found.expires > time.monotonic():
            return found
    entry = _gather(context, clean, now)
    with _cache_lock:
        if len(_cache) >= MAX_CACHE:
            for stale in sorted(_cache, key=lambda k: _cache[k].expires)[: MAX_CACHE // 5]:
                _cache.pop(stale, None)
        _cache[key] = entry
    return entry


def research(context: TenantContext, symbol: str, now: datetime, *, refresh: bool = False) -> dict[str, Any]:
    entry = gather(context, symbol, now, refresh=refresh)
    return {**entry.report, "summary": latest_summary(context, entry.report["symbol"], entry.report["data_hash"])}


# -- AI summary --------------------------------------------------------------------------------------


def _decisions(context: TenantContext) -> list[AgentDecision]:
    from .store import open_finance_store

    with open_finance_store(context) as store:
        found, _ = store.list_agent_decisions(context, agent=Agent.MARKET_RESEARCH.value, limit=500, offset=0)
    return [d for d in found if d.action == SUMMARY_ACTION]


def _summary_view(decision: AgentDecision, data_hash: str | None) -> dict[str, Any]:
    d = decision.details
    return {"status": decision.outcome, "reason": decision.reasons[0] if decision.reasons else None, "summary": d.get("summary"),
            "unknown_numbers": d.get("unknown_numbers", []), "label": rs.LABEL, "created_at": decision.created_at.isoformat(),
            "current": data_hash is not None and d.get("data_hash") == data_hash}


def latest_summary(context: TenantContext, symbol: str, data_hash: str | None = None) -> dict[str, Any] | None:
    for decision in _decisions(context):
        if decision.details.get("symbol") == symbol:
            return _summary_view(decision, data_hash)
    return None


def summaries_today(context: TenantContext, now: datetime) -> int:
    return sum(1 for d in _decisions(context) if d.created_at.date() == now.date() and d.outcome in {"ok", "withheld"})


def summarise(context: TenantContext, symbol: str, now: datetime) -> dict[str, Any]:
    """Ask the model for a summary of the gathered figures, check every number, store the outcome."""
    entry = gather(context, symbol, now)
    report = entry.report

    def record(outcome: str, reasons: list[str], details: dict[str, Any]) -> dict[str, Any]:
        from .store import open_finance_store

        decision = AgentDecision(
            id=new_id("fin_dec"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            agent=Agent.MARKET_RESEARCH, action=SUMMARY_ACTION, outcome=outcome, reasons=reasons,
            details={"symbol": report["symbol"], "data_hash": report["data_hash"], "label": rs.LABEL, **details}, created_at=now,
        )
        with open_finance_store(context) as store:
            store.save_agent_decision(decision)
        return _summary_view(decision, report["data_hash"])

    reason = rs.unavailable_reason(context)
    if reason:
        raise BrokerRefusal(reason, "AI summaries are off on this server." if reason == "disabled"
                            else "This workspace has no AI model set up. An administrator sets one under Settings, AI model.", status=409)
    if summaries_today(context, now) >= finance_config.research_summary_daily_limit:
        raise BrokerRefusal("daily_limit", f"You have used today's {finance_config.research_summary_daily_limit} AI summaries. Try again tomorrow.", status=429)
    if report["prices"].get("source") is None and report["fund"].get("source") is None:
        raise BrokerRefusal("no_data", "There is no price or fund data to summarise yet. Refresh the research first.", status=409)
    text, data = rs.prompt(report)
    from ..model_budget import ModelBudgetExceededError

    try:
        answer, model = rs.ask(context, text)  # no database session is open here
    except ModelBudgetExceededError:
        raise BrokerRefusal("model_budget", "This workspace's monthly AI budget is used up. An administrator can raise it under Budgets.", status=429) from None
    except Exception:
        return record("failed", ["model_unavailable"], {})
    summary, withheld, unknown = rs.check(answer, data)
    if summary is None:
        return record("withheld", [withheld or "unusable"], {"model": model, "unknown_numbers": unknown})
    return record("ok", [], {"summary": summary, "model": model})


# -- backtests ---------------------------------------------------------------------------------------


def backtest(context: TenantContext, body: bt.BacktestInput, now: datetime) -> dict[str, Any]:
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
    limits = settings.limits
    if body.per_order_limit > limits.max_order:
        raise BrokerRefusal("max_order", f"The most per order is your limit of USD {limits.max_order}. Raise it under IBKR trading first.", status=422)
    if body.total_limit > limits.max_capital:
        raise BrokerRefusal("max_capital", f"The total is your limit of USD {limits.max_capital}. Raise it under IBKR trading first.", status=422)
    if body.monthly_amount > body.per_order_limit or body.dip_amount > body.per_order_limit:
        raise BrokerRefusal("max_order", "A monthly or extra amount cannot be more than the most per order.", status=422)
    entries = {s: gather(context, s, now) for s in body.symbols}
    missing = [s for s, e in entries.items() if len(e.monthly) < bt.MIN_MONTHS]
    if missing:
        why = "; ".join(f"{s}: {entries[s].report['prices'].get('unavailable') or 'fewer than 12 months of prices'}" for s in missing)
        raise BrokerRefusal("no_prices", f"Not enough price history to test: {why}", status=409)
    result = bt.run(body, {s: e.monthly for s, e in entries.items()}, None, dividends_included=False)
    result["prices"] = {s: {"source": e.report["prices"]["source"], "adjusted": e.report["prices"].get("adjusted"), "to": e.report["prices"].get("to")}
                        for s, e in entries.items()}
    result["dividends_note"] = ("Dividends are not included: the price sources give no dividend history. With dividends, a real result "
                                "would differ; US dividends are taxed 30 percent at source for a Saudi resident.")
    result["shariah"] = {s: rd.shariah(s)["screened"] for s in body.symbols}
    result["paper_run"] = {"available": body.rule == "monthly",
                           "reason": None if body.rule == "monthly" else "Only monthly buying can run as a paper buy plan today."}
    return result


def paper_run(context: TenantContext, symbol: str, amount: Decimal, day_of_month: int, now: datetime) -> dict[str, Any]:
    """Turn a tested monthly rule into a PAPER-only buy plan. It proposes into the approval queue; never live, never sends."""
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
    symbol = normalize(symbol, settings)
    if settings.allowed_symbol(symbol) is None:
        raise BrokerRefusal("symbol_not_allowed", f"Add {symbol} to your allowed ETFs under IBKR trading first.", status=422)
    if settings.mode(now) == Environment.LIVE:
        raise BrokerRefusal("live_on", "Live is on. A paper run is only made while new orders go to your paper account.")
    plans = [PlanInput(id=p.id, symbol=p.symbol, amount=p.amount, day_of_month=p.day_of_month, active=p.active, paper_only=p.paper_only)
             for p in settings.buy_plans]
    plans.append(PlanInput(symbol=symbol, amount=amount, day_of_month=day_of_month, paper_only=True))
    saved = save_plans(context, plans, settings.version, now)
    plan = saved.buy_plans[-1]
    with open_ibkr_stores(context) as (_, store):
        current = _settings(store, context)
        current.buy_plans = [p.model_copy(update={"origin": "research_backtest"}) if p.id == plan.id else p for p in current.buy_plans]
        _save_settings(store, context, current, now)
    return {"plan": {**plan.model_dump(mode="json"), "origin": "research_backtest"}, "mode": "paper"}


# -- portfolio (for the screen header and the assistant) --------------------------------------------


def portfolio(context: TenantContext, now: datetime) -> dict[str, Any]:
    """The owner's IBKR figures from the last read, exactly as IBKR reported them. No projection."""
    with open_ibkr_stores(context) as (_, store):
        settings = _settings(store, context)
        mode = settings.mode(now)
        connection = store.active_connection(context, mode)
    if connection is None:
        return {"mode": mode.value, "connected": False, "synced_at": None, "positions": []}
    snap, statement = connection.snapshot, connection.statement
    positions = [{"symbol": p.get("symbol"), "quantity": p.get("quantity"), "market_value": p.get("market_value"), "unrealized_pnl": p.get("unrealized_pnl"),
                  "shariah": rd.shariah(str(p.get("symbol") or "").upper())["screened"]} for p in snap.get("positions", [])[:50]]
    return {"mode": mode.value, "connected": True, "synced_at": snap.get("synced_at"), "net_liquidation": snap.get("net_liquidation"),
            "daily_pnl": snap.get("daily_pnl"), "unrealized_pnl": snap.get("unrealized_pnl"), "realized_pnl": snap.get("realized_pnl"),
            "cash": snap.get("cash", {}), "positions": positions,
            "dividends_received": statement.get("dividends_received") if statement.get("ready") else None,
            "tax_withheld": statement.get("withholding_tax") if statement.get("ready") else None}

