"""AI help for one bank message in the review queue (docs/finance-sms.md#ai-help). Off by default.

It runs only when the person asks for it on one message and only when all of these hold:

* the person turned it on with the explicit consent switch (``SmsSettings.ai.enabled`` and
  ``opted_in_at``), which says that the masked text is sent to their workspace's own model;
* the workspace has a real model (the server's mock gateway does not count);
* the person's daily cap (``ai.daily_cap``, default 20) is not used up, inside the workspace's
  monthly model budget (``onboarding.budgeted_model_gateway``).

The model sees the masked text only (account and card numbers cut to their last four digits,
``sms_parser.mask``), inside an untrusted-data block. Its answer is a suggestion of fields
that fills the correction form: it never posts, never teaches a rule and is checked like any
typed correction. No database transaction is open while the model is asked.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from typing import Any

from ..prompt_provenance import Provenance, label_untrusted, with_untrusted_rules
from ..schemas import TenantContext
from .sms_parser import Kind, mask

TIMEOUT_SECONDS = 30
INSTRUCTIONS = (
    "You read one bank SMS and return only a JSON object with these keys, each null when the message does not say: "
    "kind (one of: " + ", ".join(k.value for k in Kind) + "), amount (a decimal string), currency (ISO code), "
    "converted_amount, converted_currency, merchant, last4, reference, occurred_on (YYYY-MM-DD), balance. "
    "Use only the message below. Never guess a value the message does not contain."
)
FIELDS = ("kind", "amount", "currency", "converted_amount", "converted_currency", "merchant", "last4", "reference", "occurred_on", "balance")

_default_gateway: Any = None


def _base_gateway() -> Any:
    global _default_gateway
    if _default_gateway is None:
        from ..model_gateway import build_default_model_gateway

        _default_gateway = build_default_model_gateway()
    return _default_gateway


def set_base_gateway(gateway: Any) -> None:
    global _default_gateway
    _default_gateway = gateway


def has_model(context: TenantContext) -> bool:
    from ..onboarding import workspace_model_gateway

    return str(getattr(workspace_model_gateway(context, _base_gateway()), "provider", "")) != "mock"


def _ask(context: TenantContext, prompt: str) -> str:
    from ..model_gateway import ModelRoute, route_gateway
    from ..onboarding import budgeted_model_gateway

    gateway = route_gateway(budgeted_model_gateway(context, _base_gateway()), ModelRoute.FAST)
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            response = asyncio.run(asyncio.wait_for(gateway.generate_text(prompt), TIMEOUT_SECONDS))
            result["text"] = response.text or ""
        except BaseException as exc:  # handed back to the caller's thread
            result["error"] = exc

    worker = threading.Thread(target=run, name="finance-sms-ai", daemon=True)
    worker.start()
    worker.join(TIMEOUT_SECONDS + 5)
    if worker.is_alive():
        raise TimeoutError("the model did not answer in time")
    if "error" in result:
        raise result["error"]
    return str(result["text"])


def suggest(context: TenantContext, masked_text: str) -> dict[str, str]:
    """Suggested fields for one masked message. Raises when the model fails."""
    block = label_untrusted(mask(masked_text), source=Provenance.TOOL_OUTPUT, origin="finance.sms.message", truncated=False, max_chars=1_200)
    answer = _ask(context, with_untrusted_rules(INSTRUCTIONS, block))
    found = re.search(r"\{.*\}", answer, re.DOTALL)
    if found is None:
        return {}
    try:
        data = json.loads(found.group(0))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key in FIELDS:
        value = data.get(key)
        if value is None or isinstance(value, (dict, list, bool)):
            continue
        text = str(value).strip()[:80]
        if text:
            out[key] = text
    if "kind" in out and out["kind"] not in {k.value for k in Kind}:
        out.pop("kind")
    if "last4" in out and not re.fullmatch(r"\d{4}", out["last4"]):
        out.pop("last4")
    return out
