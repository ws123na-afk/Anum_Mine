"""Model-written commentary on the post-close daily report (docs/finance-trading-intel.md#model-commentary).

After the Supervisor writes a report, :func:`write_commentary` may ask the workspace's own
model, through the existing model gateway and the workspace's monthly model budget
(``onboarding.budgeted_model_gateway``), for a short plain reading of that report. It is
stored as one more owner-only decision (``action = "report_commentary"``, ``details.report_id``)
next to the report, never instead of it, and every view labels it as model commentary.

It does nothing (and records why, as ``skipped``) when:

* ``ANUM_FINANCE_COMMENTARY_ENABLED`` is off, or the workspace has no real model (the
  server's mock gateway answers): commentary is off, the report is unchanged;
* the person already had ``ANUM_FINANCE_COMMENTARY_MONTHLY_LIMIT`` commentaries this UTC
  month, or the workspace's model budget is used up: the budget cap.

The prompt holds only the report's own Markdown (cut to a bounded length) inside an
untrusted-data block: agent reasons and data sources are data, never instructions. The
answer is cut to ``ANUM_FINANCE_COMMENTARY_MAX_CHARS``, long dashes are replaced, and an
answer that promises or predicts returns ("guaranteed", "risk-free", "will rise", "sure
profit") is withheld rather than shown. The commentary can never act: it is text, and no
tool is offered to the model.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from datetime import datetime
from typing import Any

from ...model_budget import ModelBudgetExceededError
from ...prompt_provenance import Provenance, label_untrusted, with_untrusted_rules
from ...punctuation import plain_dashes
from ...schemas import TenantContext, new_id
from .. import config as _config
from ..store import FinanceStore
from .records import Agent, AgentDecision

logger = logging.getLogger("anum_api.finance.intel")

ACTION = "report_commentary"
LABEL = "Model commentary on SIMULATED results. Written by an AI model from this report only; it may be wrong and is not advice."
MAX_PROMPT_REPORT_CHARS = 8_000

INSTRUCTIONS = (
    "You write a short commentary on a SIMULATED paper-trading report for its owner. "
    "Use only the report below. In at most 120 words, in plain sentences: say what happened today, "
    "what needs the owner's attention, and which data problems limit the results. "
    "Never predict prices or returns, never promise or imply profit, never recommend buying or selling, "
    "never suggest live trading or changing risk limits. Say plainly that results are simulated."
)

# Claims the commentary must never make (checked on the answer, case-insensitive).
FORBIDDEN = re.compile(
    r"\b(guarantee[ds]?|risk[- ]free|sure (?:profit|win|thing)|can(?:no|')t lose|will (?:rise|go up|increase|double|make money|profit)"
    r"|certain (?:profit|gain|return)s?|easy money|get rich)\b",
    re.IGNORECASE,
)

# The workspace's gateway; replaced in tests.
_default_gateway: Any = None


def _base_gateway() -> Any:
    global _default_gateway
    if _default_gateway is None:
        from ...model_gateway import build_default_model_gateway

        _default_gateway = build_default_model_gateway()
    return _default_gateway


def set_base_gateway(gateway: Any) -> None:
    global _default_gateway
    _default_gateway = gateway


def unavailable_reason(context: TenantContext) -> str | None:
    """Why there is no commentary for this workspace, or None when a real model is set up."""
    if not _config.finance_config.commentary_enabled:
        return "disabled"
    from ...onboarding import workspace_model_gateway

    gateway = workspace_model_gateway(context, _base_gateway())
    if str(getattr(gateway, "provider", "")) == "mock":
        return "no_model"
    return None


def _used_this_month(store: FinanceStore, context: TenantContext, now: datetime) -> int:
    found, _ = store.list_agent_decisions(context, agent=Agent.SUPERVISOR.value, limit=2_000, offset=0)
    return sum(1 for d in found if d.action == ACTION and d.outcome == "ok"
               and (d.created_at.year, d.created_at.month) == (now.year, now.month))


def clean(text: str) -> tuple[str | None, str | None]:
    """(commentary, None) or (None, why it is withheld)."""
    text = plain_dashes(" ".join((text or "").split()))
    limit = _config.finance_config.commentary_max_chars
    if not text:
        return None, "empty_answer"
    if FORBIDDEN.search(text):
        return None, "profit_claim"
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        text = cut.rstrip(".,;: ") + "..."
    return text, None


def _ask(context: TenantContext, prompt: str) -> tuple[str, str]:
    """One budgeted model call, run on its own event loop (the caller may be inside one)."""
    from ...model_gateway import ModelRoute, route_gateway
    from ...onboarding import budgeted_model_gateway

    gateway = route_gateway(budgeted_model_gateway(context, _base_gateway()), ModelRoute.FAST)
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            response = asyncio.run(asyncio.wait_for(gateway.generate_text(prompt), _config.finance_config.commentary_timeout_seconds))
            result["text"] = response.text or ""
            result["model"] = f"{response.usage.provider}/{response.usage.model}"[:160]
        except BaseException as exc:  # handed back to the caller's thread
            result["error"] = exc

    worker = threading.Thread(target=run, name="finance-commentary", daemon=True)
    worker.start()
    worker.join(_config.finance_config.commentary_timeout_seconds + 5)
    if worker.is_alive():
        raise TimeoutError("the model did not answer in time")
    if "error" in result:
        raise result["error"]
    return result["text"], result["model"]


def _report(store: FinanceStore, context: TenantContext, report_id: str) -> AgentDecision | None:
    found, _ = store.list_agent_decisions(context, agent=Agent.SUPERVISOR.value, limit=2_000, offset=0)
    return next((d for d in found if d.id == report_id and d.action == "daily_report"), None)


def write_commentary(context: TenantContext, report_id: str, *, now: datetime) -> AgentDecision | None:
    """Ask for, check and store the commentary on one report. Returns the stored decision
    (``ok`` with the text, or ``skipped`` / ``withheld`` / ``failed`` with the reason), or None
    when the report is not the caller's. The model is asked with no database transaction open."""
    from ..store import open_finance_store

    with open_finance_store(context) as store:
        report = _report(store, context, report_id)
        if report is None:
            return None
        used = _used_this_month(store, context, now)

    def record(outcome: str, reasons: list[str], details: dict[str, Any] | None = None) -> AgentDecision:
        decision = AgentDecision(
            id=new_id("fin_dec"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            cycle_id=report.cycle_id, agent=Agent.SUPERVISOR, action=ACTION, outcome=outcome, reasons=reasons,
            details={"report_id": report.id, "label": LABEL, **(details or {})}, created_at=now,
        )
        with open_finance_store(context) as store:
            return store.save_agent_decision(decision)

    reason = unavailable_reason(context)
    if reason:
        return record("skipped", [reason])
    if used >= _config.finance_config.commentary_monthly_limit:
        return record("skipped", ["monthly_limit"])
    markdown = str(report.details.get("markdown", ""))
    block = label_untrusted(markdown, source=Provenance.TOOL_OUTPUT, origin="finance.intel.daily_report",
                            truncated=len(markdown) > MAX_PROMPT_REPORT_CHARS, max_chars=MAX_PROMPT_REPORT_CHARS)
    try:
        answer, model = _ask(context, with_untrusted_rules(INSTRUCTIONS, block))
    except ModelBudgetExceededError:
        return record("skipped", ["model_budget"])
    except Exception as exc:  # the model is down or slow: the report stands without commentary
        logger.warning("finance.commentary.failed", extra={"anum_error": type(exc).__name__})
        return record("failed", ["model_unavailable"])
    text, withheld = clean(answer)
    if text is None:
        return record("withheld", [withheld or "unusable"], {"model": model})
    return record("ok", [], {"text": text, "model": model})


def commentary_for(store: FinanceStore, context: TenantContext, report_id: str) -> dict[str, Any] | None:
    """The newest commentary decision for a report, as the views show it."""
    found, _ = store.list_agent_decisions(context, agent=Agent.SUPERVISOR.value, limit=2_000, offset=0)
    for d in found:
        if d.action == ACTION and d.details.get("report_id") == report_id:
            return {"status": d.outcome, "reason": d.reasons[0] if d.reasons else None, "text": d.details.get("text"),
                    "model": d.details.get("model"), "label": LABEL, "created_at": d.created_at.isoformat()}
    return None
