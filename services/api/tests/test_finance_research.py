"""Symbol research (docs/finance-research.md): figures with sources, the number check on the AI
summary, Shariah labels, backtest math on fixed price series, the paper run, permissions, tenant
isolation, budgets and the assistant tool. Every outside source is a fake (an ``httpx.MockTransport``
and the fake IBKR gateway) and the model is a fake: real services are never called.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from ibkr_fake import PIN, FakeIbkr
from pydantic import ValidationError

from anum_api import main
from anum_api.agent_tools import ToolCall
from anum_api.dependencies import memory_repository
from anum_api.finance import ibkr_api, research_data, research_service, research_summary
from anum_api.finance import ibkr_service as svc
from anum_api.finance import research_backtest as bt
from anum_api.finance.config import finance_config
from anum_api.finance.ibkr_rules import Environment
from anum_api.finance.ibkr_store import ibkr_memory_store
from anum_api.finance.store import finance_store
from anum_api.finance.tools import plan_call
from anum_api.model_budget import ModelBudgetExceededError
from anum_api.schemas import TenantContext
from anum_api.settings import settings as app_settings

TENANT, WORKSPACE = "tenant_research", "workspace_research"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_r", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_r", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_r", "x-user-roles": "viewer"}
OTHER = {**OWNER, "x-tenant-id": "tenant_other", "x-workspace-id": "workspace_other", "x-user-id": "owner_other"}
CTX = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_r", roles=["owner"])
API = "/api/v1/finance/research"
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
client = TestClient(main.app)
NO_FEES = bt.Commission(per_share=Decimal(0), minimum=Decimal(0))


def _series(closes: list[str], start: date = date(2020, 1, 31)) -> list[research_data.PricePoint]:
    out, year, month = [], start.year, start.month
    for close in closes:
        out.append(research_data.PricePoint(date(year, month, 28), Decimal(close)))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def _stooq_csv(years: int = 11) -> str:
    """Weekly-ish rows with a known fall and recovery: 100 to 50 (2020-03) and back to 100 (2021-01)."""
    rows, day = ["Date,Open,High,Low,Close,Volume"], date(2015, 11, 2)
    price = Decimal(60)
    while day <= date(2026, 10, 30):
        if date(2020, 1, 1) <= day < date(2020, 3, 20):
            price = Decimal(100) - Decimal((day - date(2020, 1, 1)).days) * Decimal("0.633")
        elif date(2020, 3, 20) <= day < date(2021, 1, 15):
            price = Decimal(50) + Decimal((day - date(2020, 3, 20)).days) * Decimal("0.17")
        elif day >= date(2021, 1, 15):
            price = Decimal(100) + Decimal((day - date(2021, 1, 15)).days) * Decimal("0.01")
        else:
            price = Decimal(60) + Decimal((day - date(2015, 11, 2)).days) * Decimal("0.025")
        p = price.quantize(Decimal("0.01"))
        rows.append(f"{day.isoformat()},{p},{p},{p},{p},1000")
        day += timedelta(days=7)
    return "\n".join(rows)


NPORT = """<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport"><formData>
<genInfo><regName>SP Funds Trust</regName><seriesName>SP Funds S&amp;P 500 Sharia Industry Exclusions ETF</seriesName>
<seriesId>S000064000</seriesId><repPdDate>2026-09-30</repPdDate></genInfo>
<fundInfo><totAssets>1000</totAssets><netAssets>987654321.4</netAssets></fundInfo>
<invstOrSecs>
<invstOrSec><name>Alpha Corp</name><pctVal>7.5</pctVal></invstOrSec>
<invstOrSec><name>Beta Inc</name><pctVal>12.25</pctVal></invstOrSec>
<invstOrSec><name>Gamma Ltd</name><pctVal>0.5</pctVal></invstOrSec>
</invstOrSecs></formData></edgarSubmission>"""
OTHER_NPORT = NPORT.replace("S000064000", "S000099999")


class Sources:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.stooq: str | None = _stooq_csv()
        self.sec_ok = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        host = request.url.host
        if host == "stooq.com":
            return httpx.Response(200, text=self.stooq) if self.stooq is not None else httpx.Response(200, text="Exceeded the daily hits limit")
        if host in {"www.sec.gov", "data.sec.gov"}:
            assert "research@example.com" in request.headers["user-agent"]
            if not self.sec_ok:
                return httpx.Response(503)
            if url.endswith("company_tickers_mf.json"):
                return httpx.Response(200, json={"fields": ["cik", "seriesId", "classId", "symbol"],
                                                 "data": [[1801234, "S000064000", "C000200000", "SPUS"], [1801234, "S000099999", "C000200001", "SPSK"]]})
            if "/submissions/" in url:
                return httpx.Response(200, json={"filings": {"recent": {
                    "form": ["NPORT-P", "N-CSR", "NPORT-P"], "accessionNumber": ["0001801234-26-000002", "0001801234-26-000003", "0001801234-26-000001"],
                    "filingDate": ["2026-10-20", "2026-10-01", "2026-10-19"], "reportDate": ["2026-09-30", "2026-08-31", "2026-09-30"],
                    "primaryDocument": ["primary_doc.xml", "x.htm", "primary_doc.xml"]}}})
            if url.endswith("000180123426000002/primary_doc.xml"):
                return httpx.Response(200, text=OTHER_NPORT)
            if url.endswith("000180123426000001/primary_doc.xml"):
                return httpx.Response(200, text=NPORT)
        if host == "searxng":
            return httpx.Response(200, json={"results": [
                {"title": "SPUS adds holdings", "url": "https://news.example.com/a", "content": "x", "publishedDate": "2026-10-30T10:00:00"},
                {"title": "bad", "url": "javascript:alert(1)"}]})
        return httpx.Response(404)


@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch) -> Iterator[Sources]:
    fake = Sources()
    monkeypatch.setattr(research_data, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(finance_config, "research_contact", "research@example.com")
    monkeypatch.setattr(app_settings, "searxng_url", "http://searxng:8080")
    monkeypatch.setattr(ibkr_api, "utc_now", lambda: NOW)
    gateway = FakeIbkr(now_ts=NOW.timestamp())
    monkeypatch.setattr(svc, "TRANSPORT", gateway)
    finance_store.clear()
    ibkr_memory_store.clear()
    research_service.clear_cache()
    memory_repository.store.audit_records.clear()
    fake.gateway = gateway  # type: ignore[attr-defined]
    yield fake
    finance_store.clear()
    ibkr_memory_store.clear()
    research_service.clear_cache()


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> dict:
    state: dict = {"answer": "", "calls": 0}

    def ask(context: TenantContext, text: str) -> tuple[str, str]:
        state["calls"] += 1
        state["prompt"] = text
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return state["answer"], "fake/model"

    monkeypatch.setattr(research_summary, "unavailable_reason", lambda context: None)
    monkeypatch.setattr(research_summary, "ask", ask)
    return state


# -- pure figures ------------------------------------------------------------------------------------


def test_returns_drawdowns_and_volatility_come_from_the_series_with_dates() -> None:
    points = research_data.parse_stooq_csv(_stooq_csv())
    history = research_data.PriceHistory("stooq", "Stooq", "not stated", NOW, points)
    out = research_data.returns(history)
    assert out["1y"]["total"]["source"] == "stooq" and out["10y"]["total"]["value"] is not None
    assert out["1y"]["total"]["as_of"].endswith(points[-1].day.isoformat())
    worst = research_data.drawdowns(points)[0]
    assert worst["peak_date"].startswith("20") and Decimal(worst["fall_percent"]) < Decimal(-45)
    assert worst["recovered_date"] is not None and worst["months_to_recover"] >= 9
    vol = research_data.volatility(points, "stooq")
    assert vol["yearly"]["unit"] == "percent" and vol["worst_month"]["as_of"] and vol["months"] > 100
    short = research_data.PriceHistory("stooq", "Stooq", "not stated", NOW, points[-60:])
    assert "less than 3 years" in research_data.returns(short)["3y"]["total"]["unavailable"]
    assert research_data.volatility(points[:20], "stooq")["yearly"]["unavailable"] == "Fewer than 12 months of prices."


def test_a_fall_that_has_not_recovered_says_so() -> None:
    points = _series(["10", "12", "6", "8"])
    worst = research_data.drawdowns(points)[0]
    assert worst == {"peak_date": "2020-02-28", "peak": "12", "trough_date": "2020-03-28", "trough": "6", "fall_percent": "-50.0",
                     "recovered_date": None, "months_to_recover": None, "months_falling": 1}


def test_shariah_labels_on_every_symbol() -> None:
    for symbol in ("SPUS", "HLAL", "UMMA", "SPRE", "SPSK"):
        label = research_data.shariah(symbol)
        assert label["screened"] and label["label"] == "Shariah-screened" and label["issuer"] and "does not certify" in label["note"]
    plain = research_data.shariah("SPY")
    assert not plain["screened"] and plain["label"] == "Not Shariah-screened" and plain["screen"] is None


def test_parsers_refuse_bad_input() -> None:
    with pytest.raises(research_data.SourceError):
        research_data.parse_stooq_csv("<html>captcha</html>")
    with pytest.raises(research_data.SourceError):
        research_data.parse_ibkr_history({"oops": 1})
    with pytest.raises(research_data.SourceError):
        research_data.parse_nport(b"<!DOCTYPE x [<!ENTITY a 'b'>]><x>&a;</x>")
    bars = research_data.parse_ibkr_history({"data": [{"t": 1700000000000, "c": 10.5}, {"t": "x", "c": 1}, {"t": 1700600000000, "c": -1}]})
    assert [str(b.close) for b in bars] == ["10.5"]
    report = research_data.parse_nport(NPORT.encode())
    assert report["holdings_count"] == 3 and report["top_holdings"][0] == {"name": "Beta Inc", "percent": "12.25"}
    assert report["net_assets"] == "987654321" and report["registrant"] == "SP Funds Trust"


# -- the number check --------------------------------------------------------------------------------


def _answer(**overrides: object) -> str:
    body = {"good": ["Fell 50.0 percent in 2020 and came back by 2021."], "bad": ["The expense ratio is not available."],
            "history": "Over 10 years the price rose 66.7 percent.", "range": "Past results do not predict the future; a fall like 50.0 percent can happen again.",
            "suggestion": "wait", "reasons": ["A small fixed amount each month spreads the risk."]}
    body.update(overrides)
    return json.dumps(body)


def test_the_number_check_rejects_any_number_not_in_the_data() -> None:
    data = json.dumps({"fall": "-50.0", "years": [2020, 2021], "total": "66.7", "periods": ["10y"]})
    summary, why, unknown = research_summary.check(_answer(), data)
    assert why is None and summary and summary["suggestion"] == "wait"
    _, why, unknown = research_summary.check(_answer(history="Over 10 years the price rose 66.8 percent."), data)
    assert why == "number_not_in_data" and unknown == ["66.8"]
    _, why, unknown = research_summary.check(_answer(reasons=["Expect 1,200 dollars."]), data)
    assert why == "number_not_in_data" and unknown == ["1200"]
    assert research_summary.check(_answer(range="It will rise."), data)[1] == "promise"
    assert research_summary.check(_answer(reasons=["A target price is near."]), data)[1] == "promise"
    assert research_summary.check(_answer(suggestion="sell everything"), data)[1] == "unreadable"
    assert research_summary.check("no json here", data)[1] == "unreadable"
    assert research_summary.numbers_in("1,000.50 and 07 and 3.10") == {"1000.5", "7", "3.1"}


# -- backtests on fixed series -----------------------------------------------------------------------


def test_monthly_buying_whole_shares_limits_and_the_hold_comparison() -> None:
    prices = {"SPUS": _series(["10", "9", "8", "9", "10", "11", "12", "11", "10", "12", "13", "14", "15"])}
    body = bt.BacktestInput(rule="monthly", symbols=["SPUS"], commission=NO_FEES)
    out = bt.run(body, prices, None, dividends_included=False)
    r = out["result"]
    assert (r["final_value"], r["put_in"], r["cash_left"], r["orders"], r["shares"]) == ("79.00", "50.00", "4.00", 5, {"SPUS": 5})
    assert (r["total_return_percent"], r["worst_fall_percent"], r["months_below_put_in"]) == ("58.0", "-19.5", 2)
    assert out["simply_holding"] == {"final_value": "75.00", "put_in": "50.00", "total_return_percent": "50.0", "worst_fall_percent": "-20.0",
                                     "months_below_put_in": 3, "fees": "0.00"}
    assert "Past results do not predict the future" in out["warning"]
    assert bt.run(body, prices, None, dividends_included=False) == out  # deterministic


def test_commission_minimum_and_whole_shares_can_skip_a_buy() -> None:
    fits = bt.run(bt.BacktestInput(rule="monthly", symbols=["SPUS"]), {"SPUS": _series(["9.50"] * 13)}, None, dividends_included=False)["result"]
    assert fits["fees"] == "0.50" and fits["shares"] == {"SPUS": 5} and fits["skipped_buys"] == 0  # 1 percent cap: 0.10 a share
    # A share plus its fee above the USD 10 per order never fits: every month with new money is a skipped buy, nothing is bought.
    never = bt.run(bt.BacktestInput(rule="monthly", symbols=["SPUS"]), {"SPUS": _series(["9.95"] * 13)}, None, dividends_included=False)["result"]
    assert never["skipped_buys"] == 5 and never["shares"] == {} and never["cash_left"] == "50.00" and never["orders"] == 0
    assert bt.Commission().fee(1, Decimal("9.50")) == Decimal("0.10")  # capped at 1 percent of the order
    assert bt.Commission().fee(300, Decimal(100)) == Decimal("1.50")


def test_dividends_pay_cash_with_the_us_withholding_taken_off() -> None:
    prices = {"SPUS": _series(["10"] * 13)}
    dividends = {"SPUS": [(date(2020, 6, 15), Decimal("1.00"))]}
    out = bt.run(bt.BacktestInput(rule="buy_hold", symbols=["SPUS"], commission=NO_FEES), prices, dividends, dividends_included=True)
    assert out["result"]["dividends_net"] == "3.50" and out["result"]["tax_withheld"] == "1.50" and out["dividends_included"]


def test_dip_buying_and_rebalance_follow_their_rules() -> None:
    prices = {"SPUS": _series(["10", "10", "8", "8", "10", "10", "10", "10", "10", "10", "10", "10", "10"])}
    dip = bt.run(bt.BacktestInput(rule="dip", symbols=["SPUS"], total_limit=Decimal(50), dip_percent=10, commission=NO_FEES),
                 prices, None, dividends_included=False)["result"]
    plain = bt.run(bt.BacktestInput(rule="monthly", symbols=["SPUS"], commission=NO_FEES), prices, None, dividends_included=False)["result"]
    assert dip["put_in"] == "50.00" and dip["orders"] >= plain["orders"]
    two = {"SPUS": _series(["10"] * 13), "HLAL": _series(["5"] * 13)}
    reb = bt.run(bt.BacktestInput(rule="rebalance", symbols=["SPUS", "HLAL"], weight_percent=50, rebalance_months=3, commission=NO_FEES),
                 two, None, dividends_included=False)
    assert reb["ok"] and set(reb["result"]["shares"]) == {"SPUS", "HLAL"}
    with pytest.raises(ValidationError):
        bt.BacktestInput(rule="rebalance", symbols=["SPUS"])
    short = bt.run(bt.BacktestInput(rule="monthly", symbols=["SPUS"]), {"SPUS": _series(["10"] * 5)}, None, dividends_included=False)
    assert not short["ok"] and "at least 12" in short["reason"]


# -- the API -----------------------------------------------------------------------------------------


def test_research_shows_every_figure_with_source_and_date(sources: Sources) -> None:
    response = client.get(f"{API}/symbols/spus", headers=OWNER)
    assert response.status_code == 200, response.text
    r = response.json()
    assert r["symbol"] == "SPUS" and r["shariah"]["screened"] and r["mode"] == "paper"
    assert r["prices"]["source"] == "stooq" and r["prices"]["last_close"]["as_of"] and r["prices"]["last_close"]["source"] == "stooq"
    assert {"1y", "3y", "5y", "10y"} == set(r["returns"]) and r["drawdowns"]["items"]
    assert r["fund"]["holdings_count"] == {"value": "3", "source": "sec_nport", "as_of": "2026-09-30", "unavailable": None, "unit": "count"}
    assert r["fund"]["expense_ratio"]["value"] is None and "Not available" in r["fund"]["expense_ratio"]["unavailable"]
    assert r["dividends"]["fund_history"]["value"] is None and r["dividends"]["us_withholding_percent"] == "30"
    assert [n["url"] for n in r["news"]["items"]] == ["https://news.example.com/a"] and r["news"]["items"][0]["published"] == "2026-10-30"
    assert any(p["source"] == "ibkr_history" for p in r["source_problems"])  # IBKR not connected: said, not hidden
    # Cached: a second read does not call the sources again; refresh does.
    before = len(sources.calls)
    client.get(f"{API}/symbols/SPUS", headers=OWNER)
    assert len(sources.calls) == before
    client.get(f"{API}/symbols/SPUS?refresh=true", headers=OWNER)
    assert len(sources.calls) > before


def test_ibkr_history_is_used_when_connected_and_missing_sources_say_why(sources: Sources, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway: FakeIbkr = sources.gateway  # type: ignore[attr-defined]
    start = datetime(2016, 11, 4, tzinfo=UTC)
    gateway.history = [{"t": int((start + timedelta(weeks=i)).timestamp() * 1000), "c": 20 + i * 0.05} for i in range(520)]
    body = {"environment": "paper", "gateway_host": "ibkr-gateway", "tls_fingerprint": PIN, "account_id": "DU1234567", "cash_account_confirmed": True}
    assert client.post("/api/v1/finance/ibkr/connections", json=body, headers=OWNER).status_code == 201
    monkeypatch.setattr(finance_config, "research_contact", "")
    monkeypatch.setattr(app_settings, "searxng_url", "")
    r = client.get(f"{API}/symbols/SPUS", headers=OWNER).json()
    assert r["prices"]["source"] == "ibkr_history" and "dividends not included" in r["prices"]["adjusted"]
    assert not any("stooq.com" in c for c in sources.calls)
    assert "ANUM_FINANCE_RESEARCH_CONTACT" in r["fund"]["unavailable"] and "ANUM_SEARXNG_URL" in r["news"]["unavailable"]


def test_no_price_source_gives_reasons_not_numbers(sources: Sources) -> None:
    sources.stooq = None
    sources.sec_ok = False
    r = client.get(f"{API}/symbols/HLAL", headers=OWNER).json()
    assert r["prices"]["source"] is None and "Stooq did not send a price file" in r["prices"]["unavailable"]
    assert all(v["total"]["value"] is None for v in r["returns"].values()) and r["drawdowns"]["items"] == []
    assert "HTTP 503" in r["fund"]["unavailable"]


def test_unknown_symbols_and_viewers_are_refused(sources: Sources) -> None:
    assert client.get(f"{API}/symbols/ZZZZ", headers=OWNER).status_code == 422
    assert client.get(f"{API}/symbols/..%2Fetc", headers=OWNER).status_code in {404, 422}
    for path in ("/symbols", "/symbols/SPUS", "/portfolio"):
        assert client.get(f"{API}{path}", headers=VIEWER).status_code == 403
    assert client.post(f"{API}/symbols/SPUS/summary", headers=VIEWER).status_code == 403
    assert client.get(f"{API}/symbols/SPUS", headers=MEMBER).status_code == 200


def test_summary_is_stored_only_when_every_number_is_in_the_data(sources: Sources, model: dict) -> None:
    r = client.get(f"{API}/symbols/SPUS", headers=OWNER).json()
    fall = r["drawdowns"]["items"][0]["fall_percent"]
    model["answer"] = _answer(good=[f"It fell {fall} percent and came back."], history="Holdings were 3 at the last report.",
                              range="Past results do not predict the future.", reasons=["Spread buys over months."])
    ok = client.post(f"{API}/symbols/SPUS/summary", headers=OWNER)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "ok" and ok.json()["summary"]["suggestion"] == "wait" and ok.json()["current"]
    assert "news.example.com" not in model["prompt"] and "SPUS adds holdings" not in model["prompt"]  # news never reaches the model
    model["answer"] = _answer(history="It will return 87.3 percent a year.")
    bad = client.post(f"{API}/symbols/SPUS/summary", headers=OWNER).json()
    assert bad["status"] == "withheld" and bad["summary"] is None and "87.3" in bad["unknown_numbers"]
    shown = client.get(f"{API}/symbols/SPUS", headers=OWNER).json()["summary"]
    assert shown["status"] == "withheld"
    audits = [a for a in memory_repository.store.audit_records if a.action == "finance.research.summary"]
    assert audits and all("SPUS" not in json.dumps(dict(a.metadata)) for a in audits)


def test_summary_budgets_daily_limit_and_no_model(sources: Sources, model: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    model["answer"] = ModelBudgetExceededError("workspace", "monthly", date(2026, 12, 1))
    assert client.post(f"{API}/symbols/SPUS/summary", headers=OWNER).status_code == 429
    model["answer"] = _answer(history="No figures.", good=["Shariah-screened."], range="Past results do not predict the future.", reasons=["Wait."])
    monkeypatch.setattr(finance_config, "research_summary_daily_limit", 1)
    assert client.post(f"{API}/symbols/SPUS/summary", headers=OWNER).status_code == 200
    limited = client.post(f"{API}/symbols/SPUS/summary", headers=OWNER)
    assert limited.status_code == 429 and "today's 1" in limited.json()["error"]["message"]
    monkeypatch.setattr(research_summary, "unavailable_reason", lambda context: "no_model")
    assert client.post(f"{API}/symbols/HLAL/summary", headers=OWNER).status_code == 409
    model["answer"] = RuntimeError("down")
    monkeypatch.setattr(research_summary, "unavailable_reason", lambda context: None)
    monkeypatch.setattr(finance_config, "research_summary_daily_limit", 10)
    assert client.post(f"{API}/symbols/HLAL/summary", headers=OWNER).json()["status"] == "failed"


def test_cache_and_summaries_stay_with_their_owner(sources: Sources, model: dict) -> None:
    model["answer"] = _answer(history="No figures.", good=["Shariah-screened."], range="Past results do not predict the future.", reasons=["Wait."])
    assert client.post(f"{API}/symbols/SPUS/summary", headers=OWNER).json()["status"] == "ok"
    other = client.get(f"{API}/symbols/SPUS", headers=OTHER).json()
    assert other["summary"] is None
    member = client.get(f"{API}/symbols/SPUS", headers=MEMBER).json()
    assert member["summary"] is None
    keys = {k[:3] for k in research_service._cache}
    assert (TENANT, WORKSPACE, "owner_r") in keys and ("tenant_other", "workspace_other", "owner_other") in keys


def test_backtest_api_applies_the_owners_limits(sources: Sources) -> None:
    response = client.post(f"{API}/backtests", json={"rule": "monthly", "symbols": ["SPUS"], "years": 5}, headers=OWNER)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["ok"] and out["result"]["put_in"] == "50.00" and out["paper_run"]["available"] and out["shariah"] == {"SPUS": True}
    assert out["dividends_included"] is False and "30 percent" in out["dividends_note"]
    over = client.post(f"{API}/backtests", json={"rule": "monthly", "symbols": ["SPUS"], "per_order_limit": "20", "monthly_amount": "20"}, headers=OWNER)
    assert over.status_code == 422 and "limit" in over.json()["error"]["message"]
    hold = client.post(f"{API}/backtests", json={"rule": "buy_hold", "symbols": ["SPUS"]}, headers=OWNER).json()
    assert hold["paper_run"]["available"] is False and hold["paper_run"]["reason"]


def test_paper_run_makes_a_paper_only_plan_for_the_owner_only(sources: Sources) -> None:
    body = {"symbol": "SPUS", "amount": "10", "day_of_month": 5}
    assert client.post(f"{API}/paper-run", json=body, headers=MEMBER).status_code == 403
    assert client.post(f"{API}/paper-run", json=body, headers=OWNER).status_code == 422  # not on the allowed list yet
    gateway: FakeIbkr = sources.gateway  # type: ignore[attr-defined]
    assert gateway is not None
    conn = {"environment": "paper", "gateway_host": "ibkr-gateway", "tls_fingerprint": PIN, "account_id": "DU1234567", "cash_account_confirmed": True}
    assert client.post("/api/v1/finance/ibkr/connections", json=conn, headers=OWNER).status_code == 201
    assert client.post("/api/v1/finance/ibkr/symbols", json={"symbol": "SPUS"}, headers=OWNER).status_code == 200
    made = client.post(f"{API}/paper-run", json=body, headers=OWNER)
    assert made.status_code == 201, made.text
    plan = made.json()["plan"]
    assert plan["paper_only"] and plan["origin"] == "research_backtest" and made.json()["mode"] == "paper"
    # Editing the plans in the IBKR tab never drops paper-only.
    settings = client.get("/api/v1/finance/ibkr/overview", headers=OWNER).json()["settings"]
    edited = [{"id": p["id"], "symbol": p["symbol"], "amount": p["amount"], "day_of_month": 6, "active": True} for p in settings["buy_plans"]]
    saved = client.put("/api/v1/finance/ibkr/plans", json={"plans": edited, "version": settings["version"]}, headers=OWNER).json()
    assert saved["buy_plans"][0]["paper_only"] is True
    # While live is on, a paper-only plan proposes nothing and a live order is never made from it.
    stored = ibkr_memory_store.settings[next(iter(ibkr_memory_store.settings))]
    stored.live.on = True
    with pytest.raises(svc.BrokerRefusal) as refused:
        svc.create_order(CTX, svc.OrderInput(symbol="SPUS", side="buy", quantity=Decimal(1), limit_price=Decimal("9.5")), NOW, paper_only=True)
    assert refused.value.code == "paper_only"
    assert client.post(f"{API}/paper-run", json=body, headers=OWNER).status_code == 409
    assert Environment.LIVE == stored.mode(NOW)


def test_the_assistant_answers_from_the_data_only(sources: Sources) -> None:
    from anum_api.agent_skills import default_skill_registry

    tools = set(main.tool_registry.names)
    for prompt in ("research SPUS", "how is my portfolio doing?"):
        assert "anum.finance" in {m.id for m in default_skill_registry().select(prompt, tools)}, prompt
    assert plan_call("research SPUS").arguments == {"symbol": "SPUS"}
    assert plan_call("how is my portfolio doing?").name == "finance.research"
    assert plan_call("how is my portfolio doing?").arguments == {}
    assert plan_call("show my paper portfolio").name == "finance.paper.portfolios"
    assert plan_call("research zzzz") is None or plan_call("research zzzz").name != "finance.research"
    result = asyncio.run(main.tool_registry.execute(ToolCall(name="finance.research", arguments={"symbol": "SPUS"}), CTX))
    assert result.status == "succeeded" and "Shariah-screened" in result.summary and "not financial advice" in result.summary
    data = json.dumps(result.output)
    assert not research_summary.unknown_numbers(result.summary.replace("not financial advice", ""), data + " 10y 5y 3y 1y")
    mine = asyncio.run(main.tool_registry.execute(ToolCall(name="finance.research", arguments={}), CTX))
    assert mine.status == "succeeded" and "not connected" in mine.summary
    from anum_api import chat_tools

    assert "finance.research" in chat_tools.FINANCE_READ_TOOLS and "finance.research" in chat_tools.DATA_ONLY_TOOLS
