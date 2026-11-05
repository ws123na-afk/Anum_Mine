"""The AI summary of one fund's research (docs/finance-research.md#ai-summary).

The workspace's own model (through the model gateway, inside the workspace's monthly model budget,
and at most ``ANUM_FINANCE_RESEARCH_SUMMARY_DAILY_LIMIT`` a day per person) gets the gathered figures
as a data block and writes: the good, the bad, the history, a realistic range of outcomes stated as
uncertainty, and a suggestion (buy, wait or avoid) with reasons.

Checks on every answer before anyone sees it (``check``):

* It must be the asked JSON shape with a suggestion of ``buy``, ``wait`` or ``avoid``.
* **Every number it states must be in the data it was given** (``numbers_in``): a summary with any
  other number is withheld, never shown or stored as text.
* No promise or prediction: "guaranteed", "risk-free", "will rise", a "target price" and the like
  withhold it too.

The model is asked with **no database session open**: the caller reads, closes, asks, then writes
the result in a new short unit of work (``research_service.summarise``). Recent news never reaches
the prompt (it is unchecked outside text). The answer can never act: no tool is offered to the model.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from decimal import Decimal, InvalidOperation
from typing import Any

from ..prompt_provenance import Provenance, label_untrusted, with_untrusted_rules
from ..punctuation import plain_dashes
from ..schemas import TenantContext
from . import config as _config

SUGGESTIONS = ("buy", "wait", "avoid")
LABEL = ("AI summary written by a model from the figures on this page only. It may be wrong. It is not financial advice "
         "and promises no return.")
MAX_PROMPT_CHARS = 12_000
MAX_FIELD_CHARS = 700
MAX_ITEMS = 5

INSTRUCTIONS = (
    "You help a long-term, halal-first investor who buys a small fixed amount and holds. Using ONLY the figures in the data "
    "block, reply with one JSON object and nothing else: "
    '{"good": [short sentences], "bad": [short sentences], "history": "one or two sentences", '
    '"range": "one or two sentences on the realistic range of outcomes, stated as uncertainty", '
    '"suggestion": "buy" or "wait" or "avoid", "reasons": [short sentences]}. '
    "Rules: copy every number exactly as it appears in the data (same digits and decimals); never compute, round or invent a "
    "number; if a figure is marked not available, say it is not available. Never predict a price, never give a target price, "
    "never promise or imply a return. Past results do not predict the future: say so in range. The suggestion is a plain "
    "opinion for a buy and hold investor, not advice. Plain short words, no long dashes."
)

FORBIDDEN = re.compile(
    r"\b(guarantee[ds]?|risk[- ]free|sure (?:profit|win|thing)|can(?:no|')t lose|will (?:rise|go up|increase|double|grow|make money|profit|recover)"
    r"|certain (?:profit|gain|return)s?|easy money|get rich|price target|target price)\b",
    re.IGNORECASE,
)
NUMBER = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")


def _norm(token: str) -> str | None:
    text = token.replace(",", "")
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    normal = value.normalize()
    return format(normal, "f")


def numbers_in(text: str) -> set[str]:
    """Every number written in ``text``, normalised (commas, leading and trailing zeros dropped)."""
    out = set()
    for match in NUMBER.findall(text or ""):
        value = _norm(match)
        if value is not None:
            out.add(value)
    return out


def unknown_numbers(answer_text: str, data_text: str) -> list[str]:
    """Numbers the answer states that the data does not contain (empty: the answer only cites the data)."""
    allowed = numbers_in(data_text)
    return sorted(n for n in numbers_in(answer_text) if n not in allowed)


def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int, float))][:MAX_ITEMS]
    return []


def _clip(text: str) -> str:
    text = plain_dashes(" ".join(text.split()))
    return text if len(text) <= MAX_FIELD_CHARS else text[:MAX_FIELD_CHARS].rsplit(" ", 1)[0] + "..."


def parse(answer: str) -> dict[str, Any] | None:
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        raw = json.loads(answer[start:end + 1])
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    suggestion = str(raw.get("suggestion", "")).strip().lower()
    if suggestion not in SUGGESTIONS:
        return None
    out = {
        "good": [_clip(t) for t in _texts(raw.get("good"))], "bad": [_clip(t) for t in _texts(raw.get("bad"))],
        "history": _clip(" ".join(_texts(raw.get("history")))), "range": _clip(" ".join(_texts(raw.get("range")))),
        "suggestion": suggestion, "reasons": [_clip(t) for t in _texts(raw.get("reasons"))],
    }
    if not (out["good"] or out["bad"]) or not out["history"] or not out["range"] or not out["reasons"]:
        return None
    return out


def check(answer: str, data_text: str) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """(summary, None, []) or (None, why it is withheld, the numbers not in the data)."""
    summary = parse(answer)
    if summary is None:
        return None, "unreadable", []
    every = " ".join([*summary["good"], *summary["bad"], summary["history"], summary["range"], *summary["reasons"]])
    if FORBIDDEN.search(every):
        return None, "promise", []
    unknown = unknown_numbers(every, data_text)
    if unknown:
        return None, "number_not_in_data", unknown[:10]
    return summary, None, []


def data_text(report: dict[str, Any]) -> str:
    """The figures the model may use, as compact JSON (news and links left out)."""
    keep = {k: report.get(k) for k in ("symbol", "name", "shariah", "prices", "returns", "drawdowns", "volatility", "fund", "dividends")}
    if isinstance(keep.get("prices"), dict):
        keep["prices"] = {k: v for k, v in keep["prices"].items() if k != "url"}
    return json.dumps(keep, sort_keys=True, separators=(",", ":"), default=str)[:MAX_PROMPT_CHARS]


def prompt(report: dict[str, Any]) -> tuple[str, str]:
    text = data_text(report)
    block = label_untrusted(text, source=Provenance.TOOL_OUTPUT, origin="finance.research", max_chars=MAX_PROMPT_CHARS)
    return with_untrusted_rules(INSTRUCTIONS, block), text


# -- the model call (replaced in tests) ------------------------------------------------------------

_default_gateway: Any = None


def _base_gateway() -> Any:
    global _default_gateway
    if _default_gateway is None:
        from ..model_gateway import build_default_model_gateway

        _default_gateway = build_default_model_gateway()
    return _default_gateway


def unavailable_reason(context: TenantContext) -> str | None:
    """Why there is no AI summary here, or None when the workspace has a real model."""
    if not _config.finance_config.research_summary_enabled:
        return "disabled"
    from ..onboarding import workspace_model_gateway

    if str(getattr(workspace_model_gateway(context, _base_gateway()), "provider", "")) == "mock":
        return "no_model"
    return None


def ask(context: TenantContext, text: str) -> tuple[str, str]:
    """One budgeted model call on its own event loop in its own thread. Never called with a DB session open."""
    from ..current_time import language_of, time_line_for, with_time_line
    from ..model_gateway import ModelRoute, route_gateway
    from ..onboarding import budgeted_model_gateway

    # The current date and time in the user's zone (current_time.py); blocking, like this call.
    text = with_time_line(text, time_line_for(context, language_of(text)))
    gateway = route_gateway(budgeted_model_gateway(context, _base_gateway()), ModelRoute.FAST)
    timeout = _config.finance_config.research_timeout_seconds
    result: dict[str, Any] = {}

    def work() -> None:
        try:
            response = asyncio.run(asyncio.wait_for(gateway.generate_text(text), timeout))
            result["text"] = response.text or ""
            result["model"] = f"{response.usage.provider}/{response.usage.model}"[:160]
        except BaseException as exc:  # handed back to the caller's thread
            result["error"] = exc

    worker = threading.Thread(target=work, name="finance-research-summary", daemon=True)
    worker.start()
    worker.join(timeout + 5)
    if worker.is_alive():
        raise TimeoutError("the model did not answer in time")
    if "error" in result:
        raise result["error"]
    return result["text"], result["model"]
