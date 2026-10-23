"""Triage and suggested replies for WhatsApp conversations (docs/whatsapp.md#triage).

Two layers:

* **Rules** (:func:`rule_category`): free, instant keyword rules in English and Arabic
  (and tyre sizes such as ``205/55R16``). Every inbound message is categorised this way
  when it arrives, so the inbox can be filtered and sorted with no model at all.
* **Model** (:func:`suggest`): the workspace's model, behind its monthly budget, reads the
  recent messages and returns a category, a one-line summary and a suggested reply.
  Customer messages are *untrusted data*: each one is wrapped by
  ``prompt_provenance.label_untrusted`` (source ``whatsapp``, origin the message id) and
  the prompt carries ``UNTRUSTED_DATA_RULES``. Whatever the model writes is a draft that a
  person reviews; it never sends anything, never selects a tool and never changes a
  setting. The result is labelled with where it came from (``source``, provider, model).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from pydantic import BaseModel, ValidationError

from .model_gateway import ModelGateway, ModelRoute, route_gateway
from .prompt_provenance import label_untrusted, with_untrusted_rules
from .whatsapp_store import Category, Direction, Message

MAX_PROMPT_MESSAGES = 12
MAX_MESSAGE_CHARS = 1_000
MAX_REPLY_CHARS = 1_000
MAX_SUMMARY_CHARS = 200
MAX_INSTRUCTIONS_CHARS = 1500

_TYRE_SIZE = re.compile(r"\b\d{3}\s*/\s*\d{2}\s*z?r?\s*\d{2}\b", re.IGNORECASE)

# Order matters: a complaint about an order is a complaint first.
RULES: list[tuple[Category, tuple[str, ...]]] = [
    (
        Category.COMPLAINT,
        (
            "complain", "complaint", "broken", "damaged", "defect", "wrong item", "refund", "return",
            "not happy", "bad service", "terrible", "late again", "cancel my order",
            "شكوى", "مشكلة", "تالف", "خربان", "سيء", "استرجاع", "ارجاع", "إرجاع", "استبدال", "غلط", "متأخر",
        ),
    ),
    (
        Category.SUPPLIER,
        (
            "supplier", "wholesale", "distributor", "quotation", "price list", "invoice", "purchase order",
            "container", "shipment from", "factory",
            "مورد", "توريد", "جملة", "موزع", "عرض سعر", "فاتورة", "حاوية", "مصنع", "وكيل",
        ),
    ),
    (
        Category.ORDER_STATUS,
        (
            "order", "tracking", "track", "delivery", "delivered", "shipped", "shipping", "where is my",
            "when will", "dispatch", "awb",
            "طلب", "طلبي", "الطلب", "طلبية", "شحن", "الشحنة", "توصيل", "التوصيل", "وصل", "متى يوصل", "وين",
            "رقم التتبع", "تتبع",
        ),
    ),
    (
        Category.PRICE_STOCK,
        (
            "price", "how much", "cost", "available", "availability", "in stock", "stock", "offer", "discount",
            "tyre", "tire", "tyres", "tires",
            "سعر", "السعر", "بكم", "كم", "متوفر", "متوفرة", "توفر", "مخزون", "عرض", "خصم", "كفر", "كفرات", "إطار",
            "اطار", "إطارات", "اطارات",
        ),
    ),
]


def rule_category(text: str) -> Category:
    lowered = (text or "").lower()
    for category, words in RULES:
        if any(word in lowered for word in words):
            return category
    if _TYRE_SIZE.search(lowered):
        return Category.PRICE_STOCK
    return Category.OTHER


def has_arabic(text: str) -> bool:
    return any("؀" <= character <= "ۿ" for character in text or "")


class ModelSuggestion(BaseModel):
    category: Category
    summary: str
    reply: str
    language: str | None = None


INSTRUCTIONS = (
    "You help businesses qualify sales enquiries and support customers on WhatsApp. Read the "
    "conversation below and answer with one JSON object and nothing else: "
    '{"category": one of "order_status", "price_stock", "complaint", "supplier", "other"; '
    '"summary": one short sentence about what the customer wants; '
    '"reply": a short, polite reply the business could send; '
    '"language": "ar" or "en", the customer\'s language}. '
    "Write the reply in the customer's language (Arabic when they write Arabic). Never "
    "invent prices, stock levels, order numbers, delivery dates or promises: when the "
    "reply needs one, write a placeholder in square brackets such as [price] or "
    "[delivery date] for the person to fill in. Ask up to two relevant sales questions at a time about the company, service needed, budget or deadline. Do not claim a quote, meeting or project is confirmed. Escalate complaints, payment issues and requests for a human. Never ask for card numbers, OTP codes, identity documents or passwords."
)


def build_prompt(messages: Sequence[Message], *, instructions: str | None = None) -> tuple[str, int]:
    """The suggestion prompt and how many customer messages it carries (as untrusted data)."""
    recent = list(messages)[-MAX_PROMPT_MESSAGES:]
    blocks: list[str] = []
    customer = 0
    for message in recent:
        who = "customer" if message.direction == Direction.INBOUND else "business"
        body = message.body or (f"[{message.kind}]" + (f" {message.media.caption}" if message.media and message.media.caption else ""))
        if message.direction == Direction.INBOUND:
            customer += 1
        blocks.append(
            label_untrusted(
                f"{who} ({message.timestamp.isoformat()}): {body}",
                source="whatsapp",
                origin=f"{who}:{message.id}",
                max_chars=MAX_MESSAGE_CHARS,
            )
        )
    guidance = INSTRUCTIONS
    if instructions:
        # The person's own guidance for this draft (trusted: typed by a workspace member).
        guidance += f"\n\nThe person asking for this draft adds: {instructions[:MAX_INSTRUCTIONS_CHARS]}"
    return with_untrusted_rules(guidance, *blocks), customer


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_suggestion(text: str, fallback_text: str) -> ModelSuggestion:
    """The model's JSON answer; anything unusable falls back to rules plus the raw text."""
    match = _JSON_OBJECT.search(text or "")
    if match:
        try:
            value = json.loads(match.group(0))
            if isinstance(value, dict):
                value.setdefault("language", None)
                parsed = ModelSuggestion.model_validate(value)
                return ModelSuggestion(
                    category=parsed.category,
                    summary=" ".join(parsed.summary.split())[:MAX_SUMMARY_CHARS],
                    reply=parsed.reply.strip()[:MAX_REPLY_CHARS],
                    language=parsed.language if parsed.language in {"ar", "en"} else None,
                )
        except (ValueError, ValidationError):
            pass
    reply = (text or "").strip()[:MAX_REPLY_CHARS]
    return ModelSuggestion(
        category=rule_category(fallback_text),
        summary=" ".join(fallback_text.split())[:MAX_SUMMARY_CHARS],
        reply=reply,
        language="ar" if has_arabic(fallback_text) else "en",
    )


async def suggest(
    gateway: ModelGateway, messages: Sequence[Message], *, instructions: str | None = None
) -> tuple[ModelSuggestion, str, str, int]:
    """One budgeted model call: (suggestion, provider, model, customer messages read).

    Drafting short replies is quick work, so it uses the fast route
    (docs/model-gateway.md#model-routing).
    """
    prompt, customer = build_prompt(messages, instructions=instructions)
    fast = route_gateway(gateway, ModelRoute.FAST)
    response = await fast.generate_text(prompt)
    last_inbound = next((m.body for m in reversed(list(messages)) if m.direction == Direction.INBOUND and m.body), "")
    return (
        parse_suggestion(response.text, last_inbound),
        response.usage.provider,
        response.usage.model,
        customer,
    )
