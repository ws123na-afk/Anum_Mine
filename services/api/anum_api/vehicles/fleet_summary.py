"""The AI summary paragraph of the monthly fleet report (docs/vehicles.md#ai-summary).

Built only from figures ANUM computed (``fleet.py``): :func:`facts` writes the month's figures as plain lines (vehicles,
kilometres, litres, each currency's total against the month before with the change, the cost per category and per
km, the vehicle with the highest cost per km, the renewals due, the Tyre planning tyres due). The workspace's model is
asked, through its budgeted gateway (``onboarding.budgeted_model_gateway``: checked against the monthly model budget
before, metered after), for one short paragraph that copies those figures and adds none. The facts reach the model
inside a labelled untrusted block (registrations and fleet numbers are the client's text).

The answer is checked before it is kept: every number in it (Arabic-Indic digits read as digits, thousands separators
ignored) must be one of the numbers in the facts. A paragraph with any other number is withheld, so the report never
carries a figure ANUM did not compute. Long dashes are replaced. The paragraph is stored with the fingerprint of the
facts it was written from; when the figures change (a record added, a payment linked), it is no longer current and is
left out of the report until it is written again.

Off when the workspace has no real model (the server's mock gateway answers), and never called for a month with no
costs and no kilometres. The model gets no tools: it writes text only.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from ..model_gateway import ModelGateway, ModelRoute, route_gateway
from ..prompt_provenance import Provenance, label_untrusted, with_untrusted_rules
from ..punctuation import plain_dashes
from ..schemas import TenantContext
from .fleet_report import CATEGORY_KEYS, month_name

MAX_CHARS = 1200
TIMEOUT_SECONDS = 60
LABEL = {
    "en": "Summary written by AI from the figures in this report. It adds no figures of its own; check it before sending.",
    "ar": "ملخص كتبه الذكاء الاصطناعي من أرقام هذا التقرير. لا يضيف أرقاما من عنده، راجعه قبل الإرسال.",
}
TITLE = {"en": "Summary (written by AI)", "ar": "الملخص (كتبه الذكاء الاصطناعي)"}

INSTRUCTIONS = (
    "You write one short paragraph for a monthly fleet cost report that a fleet manager sends to a client. "
    "Use only the facts in the block below. Copy every figure exactly as it is written there, with its currency. "
    "Never calculate, round, add up, average or estimate a figure that is not written there, never add amounts in different "
    "currencies together, and never guess causes, reasons or future costs. If the month has no costs and no kilometres, say so. "
    "At most 90 words, plain sentences, no headings, no lists, no greeting and no sign-off."
)

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

_gateway: ModelGateway | None = None


class SummaryWithheld(ValueError):
    """The model's paragraph used a figure that is not in the facts, or was empty."""


def default_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        from ..model_gateway import build_default_model_gateway

        _gateway = build_default_model_gateway()
    return _gateway


def workspace_gateway(context: TenantContext) -> ModelGateway:
    """The workspace's gateway behind its monthly model budget (reads the model settings: call it in a worker thread)."""
    from ..onboarding import budgeted_model_gateway

    return budgeted_model_gateway(context, default_gateway(), feature="vehicles")


def model_connected(context: TenantContext) -> bool:
    return str(getattr(workspace_gateway(context), "provider", "mock")) != "mock"


def _money(value: str | None) -> str:
    return "-" if value is None else f"{Decimal(value):,.2f}"


def _per_km(value: str | None) -> str:
    return "-" if value is None else f"{Decimal(value):,.3f}"


def _change(now: str, before: str) -> str | None:
    a, b = Decimal(now), Decimal(before)
    if b == 0:
        return None
    pct = (a - b) / b * 100
    return f"{'+' if pct > 0 else ''}{pct:,.1f}%"


def empty_month(summary: dict[str, Any]) -> bool:
    return Decimal(summary["distance_km"]) == 0 and all(Decimal(t["total"]) == 0 for t in summary["totals"])


def facts(summary: dict[str, Any]) -> str:
    """The month's computed figures as plain English lines: everything the paragraph may say."""
    lines = [f"Month: {month_name(summary['month'], 'en')}", f"Vehicles: {summary['vehicles']}",
             f"Kilometres driven: {Decimal(summary['distance_km']):,.0f}", f"Fuel litres: {Decimal(summary['fuel_litres']):,.1f}"]
    if empty_month(summary):
        lines.append("No costs and no kilometres were recorded this month.")
    for total in summary["totals"]:
        currency = total["currency"]
        line = f"Total cost in {currency}: {_money(total['total'])}; previous month {_money(total['previous_total'])}"
        change = _change(total["total"], total["previous_total"])
        lines.append(line + (f"; change {change}" if change else "; no costs the previous month"))
        lines.append(f"Cost by category in {currency}: " + ", ".join(f"{key} {_money(total[key])}" for key in CATEGORY_KEYS))
        if total.get("cost_per_km"):
            lines.append(f"Cost per km in {currency}: {_per_km(total['cost_per_km'])}")
    if summary["worst"]:
        worst = summary["worst"][0]
        name = f"{worst['registration']}" + (f" ({worst['nickname']})" if worst.get("nickname") else "")
        lines.append(f"Highest cost per km: vehicle {name}, {worst['currency']} {_per_km(worst['cost_per_km'])} per km, "
                     f"{worst['currency']} {_money(worst['total'])} over {Decimal(worst['distance_km']):,.0f} km")
    renewals = summary["renewals"]
    if renewals:
        overdue = sum(1 for r in renewals if r["days_left"] < 0)
        lines.append(f"Renewals (insurance, registration, inspection) due within 60 days: {len(renewals)}, of which overdue: {overdue}")
    else:
        lines.append("Renewals (insurance, registration, inspection) due within 60 days: none")
    tyres = summary.get("tyres") or {}
    if tyres.get("status") == "ok" and tyres.get("vehicles"):
        lines.append(f"Tyres due for removal within 60 days (Tyre planning, not added to the totals): {tyres.get('tyres_due', 0)}")
    return "\n".join(lines)


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _normalise(token: str) -> str | None:
    try:
        number = Decimal(token.replace(",", ""))
    except InvalidOperation:
        return None
    return format(number.normalize(), "f") if number != number.to_integral_value() else str(int(number))


def numbers(text: str) -> set[str]:
    """Every number written in the text, normalised (1,234.50 and 1234.5 are the same number)."""
    return {n for n in (_normalise(t) for t in _NUMBER.findall(text.translate(_ARABIC_DIGITS))) if n is not None}


def check(answer: str, allowed_from: str) -> str:
    """The paragraph to keep, or :class:`SummaryWithheld` when it is empty or has a number the facts do not."""
    text = plain_dashes(" ".join((answer or "").split()))
    if not text:
        raise SummaryWithheld("empty")
    invented = numbers(text) - numbers(allowed_from)
    if invented:
        raise SummaryWithheld("invented_figure")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit(" ", 1)[0].rstrip(".,;: ") + "..."
    return text


async def write(context: TenantContext, summary: dict[str, Any], language: str) -> tuple[str, str, str]:
    """(paragraph, model, facts fingerprint). Raises ``ModelBudgetExceededError``, :class:`SummaryWithheld`, or the
    gateway's error. The gateway is built in a worker thread (it reads the model settings); the call itself awaits."""
    text = facts(summary)
    gateway = route_gateway(await asyncio.to_thread(workspace_gateway, context), ModelRoute.FAST)
    block = label_untrusted(text, source=Provenance.TOOL_OUTPUT, origin="vehicles fleet report figures", max_chars=8_000)
    full = with_untrusted_rules(INSTRUCTIONS, block)
    prompt = f"Write the paragraph in {'Arabic' if language == 'ar' else 'English'}."
    generate = getattr(gateway, "generate_with_instructions", None)
    call = generate(full, prompt) if callable(generate) else gateway.generate_text(f"{full}\n\n{prompt}")
    response = await asyncio.wait_for(call, TIMEOUT_SECONDS)
    usage = getattr(response, "usage", None)
    model = f"{getattr(usage, 'provider', '')}/{getattr(usage, 'model', '')}".strip("/")[:160] if usage is not None else ""
    return check(response.text, text), model, fingerprint(text)


__all__ = ["LABEL", "TITLE", "SummaryWithheld", "check", "empty_month", "facts", "fingerprint", "model_connected", "numbers", "write"]
