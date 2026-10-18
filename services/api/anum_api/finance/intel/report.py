"""Reproducible evaluation reports (docs/finance-trading-intel.md#evaluation-reports).

A report restates everything needed to repeat an evaluation and judge it: the rule and its
version, hypothesis, instruments and exact entry and exit rules, the parameters chosen in
each fold, the data source and the SHA-256 of the bars, the cost assumptions, the
out-of-sample metrics against buy and hold over the same span, the worst drawdown and the
worst test window, the verdict with every reason, and the warnings.

Wording rules: a report never says "profitable", "approved" or "recommended". A pass says
the rule held up on these past bars with these costs; the label is always SIMULATED.
"""

from __future__ import annotations

from typing import Any

from .strategies import CANDIDATES, spec
from .validation import Validation

LABEL = "SIMULATED"
DISCLAIMER = (
    "SIMULATED research on past bars with the stated costs. It is not a forecast, a recommendation or a promise of returns; "
    "a rule that held up in the past can fail in the future."
)
VERDICT_TEXT = {
    "passed": "Held up out of sample on these bars and costs. Not a promise of future results.",
    "rejected": "Rejected: see the reasons.",
    "insufficient_data": "Not enough data for a walk-forward evaluation.",
}
REASON_TEXT = {
    "data_issues": "The bars had a blocking problem (duplicates, future dates or bad order).",
    "look_ahead": "A signal changed when later bars were removed: the rule used data it could not have had.",
    "too_few_folds": "Fewer than two test windows.",
    "too_few_trades": "Too few closed trades out of sample to judge.",
    "overfit": "Much weaker out of sample than in the training windows.",
    "no_edge": "Risk-adjusted return out of sample at or below zero.",
    "unstable": "The best parameters changed in most windows.",
}
WARNING_TEXT = {
    "survivorship_unknown": "Listing dates unknown: a universe chosen from today's listings overstates past results.",
}


def evaluation_report(v: Validation, *, data_source: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    rule = spec(v.strategy)
    oos, bench = v.out_of_sample, v.benchmark
    worst_fold = min(v.folds, key=lambda f: f.out_of_sample.total_return) if v.folds else None
    return {
        "label": LABEL,
        "disclaimer": DISCLAIMER,
        "strategy": {
            "key": rule.key, "name": rule.name, "code_version": rule.version, "candidate": rule.candidate,
            "candidate_name": CANDIDATES.get(rule.candidate, "Benchmark"), "hypothesis": rule.hypothesis, "instruments": rule.instruments,
            "timeframe": rule.timeframe, "entry": rule.entry, "exit": rule.exit, "params": params if params is not None else v.chosen_params,
        },
        "data": {"source": data_source, "sha256": v.data_hash, "symbols": list(v.symbols)},
        "costs": v.costs,
        "method": "Walk-forward: parameters chosen on each training window, measured on the next unseen window; next-open fills.",
        "verdict": v.verdict,
        "verdict_text": VERDICT_TEXT[v.verdict],
        "reasons": [{"code": r, "text": REASON_TEXT.get(r, r)} for r in v.reasons],
        "warnings": [{"code": w, "text": WARNING_TEXT.get(w, w)} for w in v.warnings],
        "out_of_sample": oos.as_dict() if oos else None,
        "benchmark": bench.as_dict() if bench else None,
        "beats_benchmark": v.beats_benchmark,
        "worst_drawdown": round(oos.max_drawdown, 6) if oos else None,
        "worst_window": {
            "test": [worst_fold.test[0].isoformat(), worst_fold.test[1].isoformat()],
            "return": round(worst_fold.out_of_sample.total_return, 6),
            "drawdown": round(worst_fold.out_of_sample.max_drawdown, 6),
        } if worst_fold else None,
        "folds": v.as_dict()["folds"],
    }


def _pct(value: Any) -> str:
    return f"{value * 100:.1f}%" if isinstance(value, (int, float)) else "n/a"


def _num(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "n/a"


def report_markdown(report: dict[str, Any]) -> str:
    """The report as plain Markdown (no long dashes), for the daily report and exports."""
    s, oos, bench = report["strategy"], report["out_of_sample"] or {}, report["benchmark"] or {}
    lines = [
        f"# {s['name']} v{s['code_version']} ({report['label']})", "", report["disclaimer"], "",
        f"**Candidate:** {s['candidate_name']}", f"**Hypothesis:** {s['hypothesis']}", f"**Instruments:** {s['instruments']}",
        f"**Timeframe:** {s['timeframe']}", f"**Entry:** {s['entry']}", f"**Exit:** {s['exit']}", f"**Parameters:** {s['params']}", "",
        f"**Data:** {report['data']['source']}, symbols {', '.join(report['data']['symbols'])}, SHA-256 `{report['data']['sha256']}`",
        f"**Costs:** {', '.join(f'{k} {v}' for k, v in report['costs'].items())}", f"**Method:** {report['method']}", "",
        f"**Verdict:** {report['verdict']}. {report['verdict_text']}",
    ]
    lines += [f"- {r['text']} (`{r['code']}`)" for r in report["reasons"]]
    lines += [f"- Warning: {w['text']}" for w in report["warnings"]]
    lines += ["", "| Measure | Strategy | Buy and hold |", "|---|---|---|"]
    for name, key, fmt in (("Total return", "total_return", _pct), ("CAGR", "cagr", _pct), ("Volatility", "volatility", _pct),
                           ("Sharpe", "sharpe", _num), ("Sortino", "sortino", _num), ("Max drawdown", "max_drawdown", _pct),
                           ("Trades", "trades", str), ("Fees", "fees", _num)):
        lines.append(f"| {name} | {fmt(oos.get(key))} | {fmt(bench.get(key))} |")
    if report["worst_window"]:
        w = report["worst_window"]
        lines += ["", f"**Worst test window:** {w['test'][0]} to {w['test'][1]}, return {_pct(w['return'])}, drawdown {_pct(w['drawdown'])}."]
    return "\n".join(lines) + "\n"
