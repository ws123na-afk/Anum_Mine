"""The Trading Supervisor and its specialist agents (docs/finance-trading-intel.md#trading-supervisor).

Deterministic code, not a language model: each agent reads stored data and writes one
structured ``AgentDecision`` per thing it decided, grouped by a cycle id. The agents:

* **Market Research**: data freshness against the venue calendar, blocking series problems,
  an abnormal last move. Never fetches anything and never reads instructions from data.
* **Technical Analysis**: the indicator snapshot, trend and volatility regime per instrument.
* **Strategy Research**: evaluations (backtest, walk-forward) of stored versions; monthly
  revalidation of active ones.
* **Portfolio**: the paper portfolio's simulated cash, positions and P&L.
* **Risk Guardian**: trading controls before an order (asset classes, data freshness,
  abnormal move and missing volume circuit breakers, position cap, daily simulated loss
  stop). Then the existing Guardian ``gate`` decides inside the execution gateway.
* **Execution Coordinator**: turns a target weight into a SIMULATED paper order request
  through the existing gateway (``create_trade_intent`` as ``requested_by = agent``), with a
  deterministic idempotency key so a repeated cycle never doubles an order. The person
  still approves every order with its payload hash; nothing here can approve.
* **Performance Reviewer**: compares the paper period with what validation showed and
  suspends a degrading strategy.

Hard limits: no agent changes risk limits (``RiskLimits`` are code defaults; a caller may only
pass stricter ones, checked), promotes or resumes a strategy, approves an order, places a
live order or bypasses Guardian. A kill switch halts the cycle. A cycle is idempotent: the
same phase on the same day for the same person runs once (restart safe).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from ...schemas import TenantContext, new_id
from ..execution import ExecMode, ExecSettings, GlobalSwitch, IntentStatus
from ..gateway import GatewayRefusal, TradeSpec, create_trade_intent
from ..store import FinanceStore
from ..trading import InstrumentKind, OrderType, PortfolioMode, PriceRecord, Side, replay, value_book
from . import backtest as bt
from . import lifecycle
from . import research
from .indicators import snapshot
from .marketdata import AssetClass, check_series
from .markets import freshness, is_trading_day, venue
from .records import Agent, AgentDecision, StrategyConfig, StrategyState
from .strategies import build

Phase = Literal["pre_open", "intraday", "post_close", "weekly", "monthly"]
PHASES: tuple[str, ...] = ("pre_open", "intraday", "post_close", "weekly", "monthly")
QTY = Decimal("0.00000001")


@dataclass(frozen=True)
class RiskLimits:
    """Trading controls. A caller may pass stricter values only (``tighter_than_default``)."""

    max_position_pct: float = 0.25  # of the portfolio's equity, per symbol
    max_stale_sessions: int = 1  # more sessions behind suspends the strategy; 1 skips the signal
    daily_loss_stop: float = 0.03  # a simulated one-day loss above this suspends the strategy
    degradation_factor: float = 1.5  # paper drawdown worse than validated drawdown times this suspends
    asset_classes: frozenset[str] = field(default_factory=lambda: frozenset({"equity", "etf", "crypto", "commodity"}))
    min_trade_pct: float = 0.02  # trades smaller than this share of equity are skipped

    def tighter_than_default(self) -> bool:
        d = RiskLimits()
        return (self.max_position_pct <= d.max_position_pct and self.max_stale_sessions <= d.max_stale_sessions
                and self.daily_loss_stop <= d.daily_loss_stop and self.degradation_factor <= d.degradation_factor
                and self.asset_classes <= d.asset_classes and self.min_trade_pct >= d.min_trade_pct)


def cycle_key(context: TenantContext, phase: str, day: date) -> str:
    raw = f"{context.tenant_id}|{context.workspace_id}|{context.user_id}|{phase}|{day.isoformat()}"
    return "fin_cyc_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


class Supervisor:
    def __init__(self, store: FinanceStore, context: TenantContext, now: datetime, limits: RiskLimits | None = None) -> None:
        limits = limits or RiskLimits()
        if not limits.tighter_than_default():
            raise ValueError("Risk limits can only be made stricter than the defaults.")
        self.store, self.context, self.now, self.limits = store, context, now, limits
        self.today = now.date()
        self.cycle_id: str | None = None
        self.decisions: list[AgentDecision] = []

    # -- plumbing ------------------------------------------------------------------------------

    def _decide(self, agent: Agent, action: str, outcome: str, reasons: list[str] | None = None, *, strategy: StrategyConfig | None = None,
                intent_id: str | None = None, details: dict[str, Any] | None = None) -> AgentDecision:
        decision = AgentDecision(
            id=new_id("fin_dec"), tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id,
            owner_user_id=self.context.user_id, cycle_id=self.cycle_id, agent=agent, action=action[:60], outcome=outcome[:30],
            reasons=list(reasons or []), strategy_id=strategy.id if strategy else None, intent_id=intent_id,
            details={"simulated": True, **(details or {})}, created_at=self.now,
        )
        self.store.save_agent_decision(decision)
        self.decisions.append(decision)
        return decision

    def _halted(self) -> str | None:
        if (self.store.get_global_switch(self.context) or GlobalSwitch()).engaged:
            return "kill_switch_global"
        if (self.store.get_exec_settings(self.context) or ExecSettings()).kill_switch:
            return "kill_switch_person"
        return None

    def _suspend(self, agent: Agent, strategy: StrategyConfig, reason_code: str, text: str) -> StrategyConfig:
        moved = lifecycle.transition(strategy, StrategyState.PAPER_SUSPENDED, actor=agent, reason=text, now=self.now)
        self.store.save_strategy(moved)
        self._decide(agent, "suspend_strategy", "suspended", [reason_code], strategy=moved, details={"reason": text})
        return moved

    def active(self) -> list[StrategyConfig]:
        return self.store.list_strategies(self.context, state=StrategyState.PAPER_ACTIVE.value, limit=200, offset=0)[0]

    # -- the cycle -----------------------------------------------------------------------------

    def run(self, phase: str) -> dict[str, Any]:
        if phase not in PHASES:
            raise ValueError(f"Unknown phase {phase!r}: use one of {', '.join(PHASES)}.")
        self.cycle_id = cycle_key(self.context, phase, self.today)
        if self.store.list_agent_decisions(self.context, cycle_id=self.cycle_id, limit=1, offset=0)[1]:
            return {"cycle_id": self.cycle_id, "phase": phase, "skipped": True, "reason": "already_ran", "decisions": 0, "simulated": True}
        halt = self._halted()
        if halt:
            self._decide(Agent.SUPERVISOR, "cycle", "halted", [halt], details={"phase": phase})
            if phase == "post_close":
                self.write_report(phase)
            return self._summary(phase)
        strategies = self.active()
        if phase in {"pre_open", "weekly"}:
            for strategy in strategies:
                self.market_research(strategy)
                self.technical_analysis(strategy)
        if phase in {"intraday", "post_close"}:
            for strategy in strategies:
                self.trade(strategy)
        if phase == "post_close":
            for strategy in self.active():
                self.portfolio_review(strategy)
                self.performance_review(strategy)
        if phase == "weekly":
            for strategy in self.active():
                self.performance_review(strategy, weekly=True)
        if phase == "monthly":
            for strategy in strategies:
                self.revalidate(strategy)
        summary = self._summary(phase)
        self._decide(Agent.SUPERVISOR, "cycle", "ok", details={k: v for k, v in summary.items() if k != "cycle_id"})
        if phase == "post_close":
            report = self.write_report(phase)
            summary["report_id"] = report.id
        return {**summary, "decisions": len(self.decisions)}

    def write_report(self, phase: str) -> AgentDecision:
        """The written post-close report from this cycle's decisions (docs/finance-trading-intel.md#daily-report)."""
        from .daily_report import build

        report = build(self.store, self.context, list(self.decisions), phase=phase, now=self.now)
        markdown = report.pop("markdown")
        return self._decide(Agent.SUPERVISOR, "daily_report", "ok", details={"report": report, "markdown": markdown})

    def _summary(self, phase: str) -> dict[str, Any]:
        def count(outcome: str) -> int:
            return sum(1 for d in self.decisions if d.outcome == outcome)

        return {
            "cycle_id": self.cycle_id, "phase": phase, "skipped": False, "simulated": True, "active_strategies": len(self.active()),
            "proposed_orders": count("proposed"), "refused_signals": count("refused") + count("denied"),
            "suspended": count("suspended"), "halted": count("halted"), "decisions": len(self.decisions),
        }

    # -- agents --------------------------------------------------------------------------------

    def market_research(self, strategy: StrategyConfig) -> dict[str, Any]:
        """Per instrument: freshness, blocking problems, an abnormal last move. Returns the worst state."""
        worst: dict[str, Any] = {"ok": True, "sessions_behind": 0, "blocking": False, "abnormal": False, "no_volume": False}
        for instrument_id in strategy.instrument_ids:
            instrument = self.store.get_instrument(self.context, instrument_id)
            if instrument is None:
                worst.update(ok=False, blocking=True)
                self._decide(Agent.MARKET_RESEARCH, "data_check", "failed", ["instrument_missing"], strategy=strategy)
                continue
            bars = self.store.list_bars(self.context, instrument_id, limit=400)
            fresh = freshness(instrument.venue, bars[-1].day if bars else None, self.now)
            candles = [b.candle() for b in bars]
            issues = check_series(candles, instrument.asset_class, instrument.venue, self.today)
            last = [i.code for i in issues if candles and i.day == candles[-1].day]
            blocking = [i.code for i in issues if i.blocking]
            no_volume = bool(candles) and instrument.asset_class != AssetClass.FOREX and not candles[-1].volume
            reasons = sorted(set(blocking + last)) + ([] if fresh.status == "fresh" else [f"data_{fresh.status}"])
            outcome = "ok" if fresh.status == "fresh" and not blocking and "abnormal_move" not in last and not no_volume else "attention"
            self._decide(Agent.MARKET_RESEARCH, "data_check", outcome, reasons, strategy=strategy, details={
                "symbol": instrument.symbol, "venue": instrument.venue, "last_bar": bars[-1].day.isoformat() if bars else None,
                "expected": fresh.expected.isoformat(), "sessions_behind": fresh.sessions_behind, "holidays_known": fresh.holidays_known,
                "source": bars[-1].source if bars else None, "provider": bars[-1].provider if bars else None,
            })
            behind = fresh.sessions_behind if fresh.status == "stale" else (10_000 if fresh.status == "none" else 0)
            worst["sessions_behind"] = max(worst["sessions_behind"], behind)
            worst["blocking"] = worst["blocking"] or bool(blocking)
            worst["abnormal"] = worst["abnormal"] or "abnormal_move" in last
            worst["no_volume"] = worst["no_volume"] or no_volume
            worst["ok"] = worst["ok"] and outcome == "ok"
        return worst

    def technical_analysis(self, strategy: StrategyConfig) -> None:
        for instrument_id in strategy.instrument_ids:
            bars = self.store.list_bars(self.context, instrument_id, limit=400)
            if not bars:
                continue
            snap = snapshot([b.candle().bar() for b in bars])
            self._decide(Agent.TECHNICAL_ANALYSIS, "analysis", "ok", list(snap.contradictions), strategy=strategy, details={
                "instrument_id": instrument_id, "trend": snap.trend, "volatility_regime": snap.volatility_regime,
                "rsi_14": snap.rsi_14, "adx_14": snap.adx_14, "bars": snap.bars,
            })

    def portfolio_review(self, strategy: StrategyConfig) -> dict[str, Any] | None:
        portfolio = self.store.get_portfolio(self.context, strategy.portfolio_id or "")
        if portfolio is None:
            self._decide(Agent.PORTFOLIO, "portfolio_review", "failed", ["portfolio_missing"], strategy=strategy)
            return None
        book = replay(portfolio, self.store.portfolio_orders(self.context, portfolio.id, portfolio.epoch))
        valuation = value_book(book, self.store.list_prices(self.context, symbol=None, limit=5000),
                               self.store.list_fx_rates(self.context, limit=5000), self.today, portfolio.max_price_age_days)
        details = {
            "portfolio_id": portfolio.id, "cash": [r.model_dump(mode="json") for r in valuation.cash],
            "positions": [{"symbol": p.symbol, "quantity": str(p.quantity), "market_value": str(p.market_value)} for p in valuation.positions],
            "realised": [r.model_dump(mode="json") for r in valuation.realised], "unrealised": [r.model_dump(mode="json") for r in valuation.unrealised],
            "fees": [r.model_dump(mode="json") for r in valuation.fees], "unpriced": valuation.unpriced,
        }
        self._decide(Agent.PORTFOLIO, "portfolio_review", "ok" if not valuation.unpriced else "attention",
                     ["unpriced_positions"] if valuation.unpriced else [], strategy=strategy, details=details)
        return details

    def revalidate(self, strategy: StrategyConfig) -> None:
        try:
            run = research.evaluate(self.store, self.context, strategy, kind="walk_forward", actor=Agent.STRATEGY_RESEARCH, now=self.now)
        except research.ResearchError as exc:
            self._decide(Agent.STRATEGY_RESEARCH, "revalidate", "failed", [exc.code], strategy=strategy)
            return
        self._decide(Agent.STRATEGY_RESEARCH, "revalidate", run.verdict, run.reasons, strategy=strategy,
                     details={"run_id": run.id, "reproduced": run.reproduced, "data_hash": run.data_hash})
        if run.verdict != "passed" or not run.reproduced:
            self._suspend(Agent.STRATEGY_RESEARCH, strategy, "revalidation_failed",
                          "Monthly out-of-sample revalidation did not pass: " + (", ".join(run.reasons) or run.verdict) + ".")

    def performance_review(self, strategy: StrategyConfig, *, weekly: bool = False) -> None:
        """The rule on the bars since it became active, against what validation showed."""
        runs = self.store.list_strategy_runs(self.context, strategy.id, limit=50, offset=0)[0]
        validated = next((r for r in runs if r.kind == "walk_forward" and r.verdict == "passed"), None)
        try:
            data = research.load(self.store, self.context, strategy, self.today)
        except research.ResearchError as exc:
            self._decide(Agent.PERFORMANCE_REVIEWER, "review", "failed", [exc.code], strategy=strategy)
            return
        aligned = bt.align(data.bars)
        days = [c.day for c in next(iter(aligned.values()))]
        since = strategy.state_changed_at.date()
        start = next((i for i, d in enumerate(days) if d >= since), len(days))
        if len(days) - start < 5:
            self._decide(Agent.PERFORMANCE_REVIEWER, "review", "skipped", ["too_little_paper_history"], strategy=strategy)
            return
        warm = max(0, start - 250)
        res = bt.run(aligned, build(strategy.key, tuple(strategy.symbols), strategy.params), start_index=warm)
        cut = start - warm
        window = bt.Result(days=res.days[cut:], equity=res.equity[cut:], trades=[t for t in res.trades if t.day >= days[start]], targets=[])
        bench = bt.run(aligned, build("buy_and_hold", tuple(strategy.symbols)), start_index=start)
        m, b = bt.metrics(window, data.periods_per_year), bt.metrics(bench, data.periods_per_year)
        limit = (validated.metrics.get("max_drawdown") if validated else None)
        reasons: list[str] = []
        if isinstance(limit, (int, float)) and m.max_drawdown < limit * self.limits.degradation_factor:
            reasons.append("drawdown_beyond_validation")
        if weekly and m.total_return < b.total_return:
            reasons.append("behind_benchmark")
        details = {"since": since.isoformat(), "bars": len(window.equity), "return": round(m.total_return, 6),
                   "max_drawdown": round(m.max_drawdown, 6), "benchmark_return": round(b.total_return, 6),
                   "validated_max_drawdown": limit}
        self._decide(Agent.PERFORMANCE_REVIEWER, "weekly_review" if weekly else "review",
                     "degrading" if "drawdown_beyond_validation" in reasons else ("attention" if reasons else "ok"), reasons,
                     strategy=strategy, details=details)
        if "drawdown_beyond_validation" in reasons:
            self._suspend(Agent.PERFORMANCE_REVIEWER, strategy, "drawdown_beyond_validation",
                          "The paper period's drawdown is worse than validation allowed: review it and make a new version if needed.")

    # -- signals to simulated orders -------------------------------------------------------------

    def _venues_open_today(self, strategy: StrategyConfig) -> bool | None:
        """Whether any of the strategy's venues trades on its own local date today (None: no instruments)."""
        venues = {i.venue for i in (self.store.get_instrument(self.context, x) for x in strategy.instrument_ids) if i is not None}
        if not venues:
            return None
        return any(is_trading_day(venue(code), self.now.astimezone(ZoneInfo(venue(code).timezone)).date()) for code in venues)

    def trade(self, strategy: StrategyConfig) -> None:
        if not lifecycle.can_trade(strategy):
            return
        if self._venues_open_today(strategy) is False:
            self._decide(Agent.SUPERVISOR, "trade", "market_closed", ["not_a_trading_day"], strategy=strategy)
            return
        portfolio = self.store.get_portfolio(self.context, strategy.portfolio_id or "")
        if portfolio is None or portfolio.mode != PortfolioMode.PAPER:
            self._suspend(Agent.RISK_GUARDIAN, strategy, "portfolio_missing", "Its paper portfolio is missing or is not a paper portfolio.")
            return
        data_state = self.market_research(strategy)
        if data_state["blocking"] or data_state["sessions_behind"] > self.limits.max_stale_sessions:
            self._suspend(Agent.RISK_GUARDIAN, strategy, "data_failed",
                          "Market data failed its checks (missing, corrupt or too old): no simulated order until it is fixed.")
            return
        if data_state["sessions_behind"]:
            self._decide(Agent.RISK_GUARDIAN, "signal", "refused", ["stale_price"], strategy=strategy)
            return
        try:
            data = research.load(self.store, self.context, strategy, self.today)
        except research.ResearchError as exc:
            self._suspend(Agent.RISK_GUARDIAN, strategy, exc.code, str(exc))
            return
        classes = {i.asset_class.value for i in data.instruments}
        if not classes <= self.limits.asset_classes or len({i.currency for i in data.instruments}) != 1:
            self._suspend(Agent.RISK_GUARDIAN, strategy, "instrument_not_allowed",
                          "Its instruments are outside the allowed asset classes or use more than one currency.")
            return
        currency = data.instruments[0].currency
        aligned = bt.align(data.bars)
        result = bt.run(aligned, build(strategy.key, tuple(strategy.symbols), strategy.params), final_signal=True)
        target = next((t for t in reversed(result.targets) if t is not None), None)
        if target is None:
            self._decide(Agent.STRATEGY_RESEARCH, "signal", "skipped", ["no_signal_yet"], strategy=strategy)
            return
        book = replay(portfolio, self.store.portfolio_orders(self.context, portfolio.id, portfolio.epoch))
        closes = {s: aligned[s][-1].close for s in strategy.symbols}
        previous = {s: aligned[s][-2].close for s in strategy.symbols}
        held = {s: book.held(InstrumentKind.SECURITY, s, currency) for s in strategy.symbols}
        cash = book.cash.get(currency, Decimal(0))
        equity = cash + sum(held[s] * closes[s] for s in strategy.symbols)
        equity_before = cash + sum(held[s] * previous[s] for s in strategy.symbols)
        if equity <= 0:
            self._decide(Agent.RISK_GUARDIAN, "signal", "refused", ["no_equity"], strategy=strategy)
            return
        if equity_before > 0 and float(1 - equity / equity_before) > self.limits.daily_loss_stop:
            self._suspend(Agent.RISK_GUARDIAN, strategy, "daily_loss_stop", "The simulated one-day loss passed the daily loss stop.")
            return
        if data_state["abnormal"] or data_state["no_volume"]:
            self._decide(Agent.RISK_GUARDIAN, "signal", "refused", ["abnormal_move" if data_state["abnormal"] else "no_volume"], strategy=strategy)
            return
        bar_day = aligned[strategy.symbols[0]][-1].day
        for symbol in sorted(strategy.symbols, key=lambda s: float(target.get(s, 0.0)) * float(equity) - float(held[s] * closes[s])):
            weight = min(float(target.get(symbol, 0.0)), self.limits.max_position_pct)
            capped = float(target.get(symbol, 0.0)) > self.limits.max_position_pct
            gap = Decimal(str(weight)) * equity - held[symbol] * closes[symbol]
            if abs(gap) < Decimal(str(self.limits.min_trade_pct)) * equity:
                continue
            side = Side.BUY if gap > 0 else Side.SELL
            quantity = (abs(gap) / closes[symbol]).quantize(QTY, rounding=ROUND_DOWN)
            if side == Side.SELL:
                quantity = min(quantity, held[symbol])
            if quantity <= 0:
                continue
            self._order(strategy, portfolio.id, symbol, currency, side, quantity, weight, capped, bar_day, closes[symbol], data.source)

    def _record_price(self, symbol: str, currency: str, price: Decimal, day: date, source: str) -> None:
        """The bar's close as a recorded price, so the gateway prices the order from stored data with its source."""
        label = f"intel: {source}"[:120]
        for existing in self.store.list_prices(self.context, symbol=symbol, limit=50):
            if existing.as_of == day and existing.currency == currency and existing.source == label:
                return
        self.store.save_price(PriceRecord(
            id=new_id("fin_price"), tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id,
            owner_user_id=self.context.user_id, symbol=symbol, currency=currency, price=price, as_of=day, source=label, recorded_at=self.now,
        ))

    def _order(self, strategy: StrategyConfig, portfolio_id: str, symbol: str, currency: str, side: Side, quantity: Decimal, weight: float,
               capped: bool, bar_day: date, close: Decimal, source: str) -> None:
        self._record_price(symbol, currency, close, bar_day, source)
        spec = TradeSpec(
            mode=ExecMode.PAPER, portfolio_id=portfolio_id, instrument_kind=InstrumentKind.SECURITY, symbol=symbol, currency=currency,
            side=side, order_type=OrderType.MARKET, quantity=quantity,
            reason=f"SIMULATED. {strategy.name} v{strategy.version}: target {weight:.0%} of the portfolio (bar {bar_day.isoformat()})."[:300],
            idempotency_key=f"intel:{strategy.id}:{bar_day.isoformat()}:{symbol}:{side.value}",
        )
        details = {"symbol": symbol, "side": side.value, "quantity": str(quantity), "target_weight": round(weight, 4), "capped": capped,
                   "bar_day": bar_day.isoformat(), "data_source": source, "strategy_version": strategy.version,
                   "code_version": strategy.code_version}
        try:
            intent = create_trade_intent(self.store, self.context, spec, requested_by="agent",
                                         proposer=Agent.EXECUTION_COORDINATOR.value, now=self.now)
        except GatewayRefusal as exc:
            self._decide(Agent.EXECUTION_COORDINATOR, "propose_order", "refused", [exc.code], strategy=strategy, details=details)
            return
        guardian = intent.decision or {}
        codes = [r.get("code", "") for r in guardian.get("reasons", [])] if isinstance(guardian, dict) else []
        details.update({"estimate": intent.estimate, "guardian_outcome": guardian.get("outcome") if isinstance(guardian, dict) else None,
                        "payload_hash": intent.payload_hash, "status": intent.status.value})
        if intent.status == IntentStatus.DENIED:
            self._decide(Agent.RISK_GUARDIAN, "guardian_gate", "denied", codes, strategy=strategy, intent_id=intent.id, details=details)
        elif intent.status == IntentStatus.REFUSED:
            self._decide(Agent.EXECUTION_COORDINATOR, "propose_order", "refused", [intent.refusal_code or "refused"], strategy=strategy,
                         intent_id=intent.id, details=details)
        else:
            self._decide(Agent.EXECUTION_COORDINATOR, "propose_order", "proposed", codes, strategy=strategy, intent_id=intent.id, details=details)


def pause_all(store: FinanceStore, context: TenantContext, *, now: datetime, reason: str) -> list[StrategyConfig]:
    """The person's emergency pause: every PAPER_ACTIVE version to PAPER_SUSPENDED, recorded."""
    paused: list[StrategyConfig] = []
    for strategy in store.list_strategies(context, state=StrategyState.PAPER_ACTIVE.value, limit=200, offset=0)[0]:
        moved = lifecycle.transition(strategy, StrategyState.PAPER_SUSPENDED, actor=Agent.PERSON, reason=reason, now=now)
        store.save_strategy(moved)
        store.save_agent_decision(AgentDecision(
            id=new_id("fin_dec"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            agent=Agent.PERSON, action="pause_all", outcome="suspended", reasons=["emergency_pause"], strategy_id=moved.id,
            details={"simulated": True, "reason": moved.reason}, created_at=now,
        ))
        paused.append(moved)
    return paused
