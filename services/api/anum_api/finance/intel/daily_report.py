"""The post-close daily trading report (docs/finance-trading-intel.md#daily-report).

Written by the Trading Supervisor at the end of every ``post_close`` cycle from that cycle's
own recorded decisions (nothing is recomputed or invented), and stored as one more owner-only
``AgentDecision`` (``action = "daily_report"``) with the structured report and its Markdown.
SIMULATED throughout: proposed orders are requests waiting for the person's approval, and
figures come from recorded prices with the stated fee and slippage assumptions.

Agents' recorded reasons are quoted as data. The report never gives advice, never promises
returns and has no long dashes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...schemas import TenantContext
from ..store import FinanceStore
from .records import AgentDecision

DISCLAIMER = ("SIMULATED. Paper trading with recorded or imported data and stated costs; no real order was placed. "
              "Past or simulated results say nothing certain about the future.")


def _pct(value: Any) -> str:
    return f"{value * 100:+.1f}%" if isinstance(value, (int, float)) else "n/a"


def _amounts(rows: Any) -> str:
    if not rows:
        return "none"
    return ", ".join(f"{r.get('currency')} {r.get('amount')}" for r in rows if isinstance(r, dict))


def _clean(text: Any, limit: int = 160) -> str:
    """One line of recorded text, shortened, without characters that would break the Markdown table."""
    value = " ".join(str(text).split()).replace("|", "/")
    return value[:limit]


def build(store: FinanceStore, context: TenantContext, decisions: list[AgentDecision], *, phase: str, now: datetime) -> dict[str, Any]:
    """The report for one cycle's decisions (oldest first)."""
    names: dict[str, str] = {}

    def strategy_name(strategy_id: str | None) -> str:
        if not strategy_id:
            return "-"
        if strategy_id not in names:
            found = store.get_strategy(context, strategy_id)
            names[strategy_id] = f"{found.name} v{found.version}" if found else strategy_id
        return names[strategy_id]

    by = lambda **match: [d for d in decisions if all(getattr(d, k) == v for k, v in match.items())]  # noqa: E731
    data = [d for d in decisions if d.action == "data_check"]
    proposals = [d for d in decisions if d.outcome == "proposed"]
    refusals = [d for d in decisions if d.outcome in {"refused", "denied"}]
    suspensions = [d for d in decisions if d.outcome == "suspended"]
    reviews = [d for d in decisions if d.action == "portfolio_review" and d.outcome != "failed"]
    performance = [d for d in decisions if d.action in {"review", "weekly_review"} and "return" in d.details]
    problems = [d for d in decisions if d.outcome in {"failed", "degrading", "halted"}]
    closed = by(outcome="market_closed")
    _, waiting = store.list_intents(context, status="pending_approval", mode=None, limit=1, offset=0)
    active = store.list_strategies(context, state="PAPER_ACTIVE", limit=200, offset=0)[1]

    report: dict[str, Any] = {
        "label": "SIMULATED", "disclaimer": DISCLAIMER, "phase": phase, "date": now.date().isoformat(), "generated_at": now.isoformat(),
        "summary": {
            "active_strategies": active, "proposed_orders": len(proposals), "refused_signals": len(refusals), "suspended": len(suspensions),
            "waiting_for_approval": waiting, "problems": len(problems), "market_closed": len(closed),
        },
        "data_health": [
            {"symbol": d.details.get("symbol"), "venue": d.details.get("venue"), "last_bar": d.details.get("last_bar"),
             "sessions_behind": d.details.get("sessions_behind"), "source": d.details.get("source"), "status": d.outcome, "reasons": d.reasons}
            for d in data
        ],
        "orders": [
            {"strategy": strategy_name(d.strategy_id), "symbol": d.details.get("symbol"), "side": d.details.get("side"), "quantity": d.details.get("quantity"),
             "target_weight": d.details.get("target_weight"), "estimate": d.details.get("estimate"), "guardian": d.details.get("guardian_outcome"),
             "intent_id": d.intent_id, "bar_day": d.details.get("bar_day"), "data_source": d.details.get("data_source")}
            for d in proposals
        ],
        "refused": [{"strategy": strategy_name(d.strategy_id), "agent": d.agent.value, "action": d.action, "reasons": d.reasons} for d in refusals],
        "suspended": [{"strategy": strategy_name(d.strategy_id), "agent": d.agent.value, "reasons": d.reasons, "why": d.details.get("reason")}
                      for d in suspensions],
        "portfolios": [{"strategy": strategy_name(d.strategy_id), **{k: d.details.get(k) for k in ("cash", "realised", "unrealised", "fees", "unpriced")}}
                       for d in reviews],
        "performance": [{"strategy": strategy_name(d.strategy_id), **{k: d.details.get(k) for k in ("since", "return", "benchmark_return", "max_drawdown")},
                         "flags": d.reasons} for d in performance],
        "problems": [{"agent": d.agent.value, "action": d.action, "outcome": d.outcome, "reasons": d.reasons, "strategy": strategy_name(d.strategy_id)}
                     for d in problems],
    }
    steps: list[str] = []
    if waiting:
        steps.append(f"Review the {waiting} SIMULATED order request(s) waiting for your approval under Finance, Paper trading.")
    if suspensions:
        steps.append("Look at each suspended strategy's reason; resume it yourself only when the cause is fixed, or make a new version.")
    if any(d.outcome != "ok" for d in data):
        steps.append("Import fresh daily bars for the instruments marked stale or with problems.")
    if any("drawdown_beyond_validation" in p["flags"] or "behind_benchmark" in p["flags"] for p in report["performance"]):
        steps.append("Compare the flagged strategies with their validation and decide whether to keep them on paper.")
    report["next_steps"] = steps or ["Nothing needs your action today."]
    report["markdown"] = markdown(report)
    return report


def markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"# Trading report, {report['date']} (after the close, SIMULATED)", "", report["disclaimer"], "",
        f"- Active paper strategies: {s['active_strategies']}",
        f"- Orders proposed for your approval: {s['proposed_orders']} (waiting in total: {s['waiting_for_approval']})",
        f"- Signals refused: {s['refused_signals']}",
        f"- Strategies suspended: {s['suspended']}",
        f"- Problems recorded: {s['problems']}",
    ]
    if s["market_closed"]:
        lines.append(f"- Market closed for {s['market_closed']} strategy(ies): nothing traded")
    lines += ["", "## Data health", ""]
    if report["data_health"]:
        lines += ["| Symbol | Venue | Newest bar | Sessions behind | Source | Status |", "|---|---|---|---|---|---|"]
        lines += [f"| {_clean(d['symbol'], 20)} | {_clean(d['venue'], 10)} | {d['last_bar'] or 'none'} | {d['sessions_behind']} | {_clean(d['source'] or '-', 60)} | "
                  f"{d['status']}{' (' + _clean(', '.join(d['reasons']), 80) + ')' if d['reasons'] else ''} |" for d in report["data_health"]]
    else:
        lines.append("No instrument was checked in this cycle.")
    lines += ["", "## Orders proposed (waiting for your approval)", ""]
    if report["orders"]:
        lines += ["| Strategy | Order | Target | Estimated fill | Fee | Guardian |", "|---|---|---|---|---|---|"]
        for o in report["orders"]:
            est = o["estimate"] or {}
            lines.append(f"| {_clean(o['strategy'], 60)} | {o['side']} {o['quantity']} {_clean(o['symbol'], 20)} | {_pct(o['target_weight'])} | "
                         f"{est.get('fill_price', 'n/a')} ({_clean(est.get('price_source', '-'), 40)}) | {est.get('fee', 'n/a')} | {o['guardian'] or 'n/a'} |")
    else:
        lines.append("None.")
    lines += ["", "## Refused and suspended", ""]
    rows = [f"- Refused, {r['strategy']}: {_clean(', '.join(r['reasons']) or r['action'])}" for r in report["refused"]]
    rows += [f"- Suspended, {r['strategy']} by {r['agent'].replace('_', ' ')}: {_clean(r['why'] or ', '.join(r['reasons']))}" for r in report["suspended"]]
    lines += rows or ["None."]
    lines += ["", "## Paper portfolio", ""]
    if report["portfolios"]:
        for p in report["portfolios"]:
            lines.append(f"- {p['strategy']}: cash {_amounts(p['cash'])}; realised {_amounts(p['realised'])}; unrealised {_amounts(p['unrealised'])}; "
                         f"fees {_amounts(p['fees'])}" + (f"; not valued (no recent price): {', '.join(p['unpriced'])}" if p["unpriced"] else ""))
    else:
        lines.append("No portfolio was reviewed in this cycle.")
    lines += ["", "## Against buy and hold", ""]
    if report["performance"]:
        for p in report["performance"]:
            flags = f" Flags: {', '.join(p['flags'])}." if p["flags"] else ""
            lines.append(f"- {p['strategy']} since {p['since']}: {_pct(p['return'])} against {_pct(p['benchmark_return'])}, "
                         f"drawdown {_pct(p['max_drawdown'])}.{flags}")
    else:
        lines.append("No paper period long enough to compare yet.")
    lines += ["", "## Problems", ""]
    lines += [f"- {p['agent'].replace('_', ' ')}, {p['action'].replace('_', ' ')}: {p['outcome']} ({_clean(', '.join(p['reasons']) or '-')})"
              for p in report["problems"]] or ["None."]
    lines += ["", "## Next steps", ""] + [f"- {step}" for step in report["next_steps"]]
    return "\n".join(lines) + "\n"
