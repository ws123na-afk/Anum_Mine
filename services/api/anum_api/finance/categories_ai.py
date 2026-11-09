"""The optional AI category suggestion (docs/finance.md#rules-and-learning), the last layer of the engine.

Runs only when the owner asks for it on one row, only with AI help turned on (``SmsSettings.ai``, the same consent
as the SMS help) and a real model (``sms_ai.has_model``). The model sees the description with account and card
numbers masked, inside an untrusted-data block, and answers with a category and a type from the fixed lists. The
answer is shown to the owner only: it is never applied, stored or learned from. No unit of work is open while the
model is asked.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

from ..prompt_provenance import Provenance, label_untrusted, with_untrusted_rules
from ..schemas import TenantContext
from .models import Category, TransactionType
from .sms_parser import mask

INSTRUCTIONS = (
    "You read one bank transaction description and return only a JSON object with two keys: category (one of: "
    + ", ".join(c.value for c in Category) + ") and transaction_type (one of: " + ", ".join(t.value for t in TransactionType)
    + "), each null when the description does not say. Use only the description below."
)


def suggest_category(context: TenantContext, description: str, amount: Decimal) -> tuple[Category | None, TransactionType | None]:
    from . import sms_ai

    side = "money in" if amount > 0 else "money out"
    block = label_untrusted(f"{side}: {mask(description)}", source=Provenance.TOOL_OUTPUT, origin="finance.transaction.description",
                            truncated=False, max_chars=600)
    answer = sms_ai._ask(context, with_untrusted_rules(INSTRUCTIONS, block))
    found = re.search(r"\{.*\}", answer, re.DOTALL)
    if found is None:
        return None, None
    try:
        data = json.loads(found.group(0))
    except json.JSONDecodeError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    category = data.get("category") if data.get("category") in {c.value for c in Category} else None
    kind = data.get("transaction_type") if data.get("transaction_type") in {t.value for t in TransactionType} else None
    if kind == TransactionType.UNCATEGORIZED.value:
        kind = None
    return (Category(category) if category else None), (TransactionType(kind) if kind else None)


__all__ = ["suggest_category"]
