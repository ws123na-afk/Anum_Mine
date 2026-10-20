"""Model-assisted text for Business: research inferences and outbound drafts (docs/business.md).

Every model call goes through the workspace's budgeted gateway (checked against the
monthly model budget before, metered after). Search results, page excerpts and any other
external text reach the model only inside labelled untrusted blocks: they are data, never
instructions, and nothing the model writes can grant a tool or change a record by itself.
Inferences come back as *proposed* (a person reviews them); drafts come back as drafts
with the deterministic checks below. Without a connected model, templates are used and
say so.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ..model_budget import ModelBudgetExceededError
from ..model_gateway import ModelGateway, ModelRoute, route_gateway
from ..prompt_provenance import Provenance, label_untrusted, with_untrusted_rules
from ..punctuation import PLAIN_PUNCTUATION_RULE, plain_dashes
from ..schemas import TenantContext
from .models import CONVERSATION_CRITERIA, Account, Confidence, Contact, Criterion, DraftChannel, Evidence, Lead, Offer, Product

logger = logging.getLogger("anum.business")

_gateway: ModelGateway | None = None

CHANNEL_LIMITS = {DraftChannel.EMAIL: 3000, DraftChannel.WHATSAPP: 1000, DraftChannel.LINKEDIN: 1900, DraftChannel.CALL_SCRIPT: 3000}
OPT_OUT = {
    "en": "If you would rather not hear from me, reply STOP and I will not contact you again.",
    "ar": "إذا كنت لا ترغب في تلقي رسائل مني، فأرسل كلمة إيقاف ولن أتواصل معك مرة أخرى.",
}
OPT_OUT_WORDS = re.compile(r"\bSTOP\b|إيقاف|ايقاف", re.IGNORECASE)
PROMISE_WORDS = re.compile(r"\bguarantee[ds]?\b|\b100\s?%|\bno risk\b|\brisk[- ]free\b|نضمن|ضمان|مضمون", re.IGNORECASE)
NUMBER = re.compile(r"\d[\d,.]*")
PLACEHOLDER = re.compile(r"\[[^\]]{1,40}\]|\{[^}]{1,40}\}|<[^>]{1,40}>")


def default_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        from ..model_gateway import build_default_model_gateway

        _gateway = build_default_model_gateway()
    return _gateway


def workspace_gateway(context: TenantContext) -> ModelGateway:
    from ..onboarding import budgeted_model_gateway

    return budgeted_model_gateway(context, default_gateway())


def model_connected(context: TenantContext) -> bool:
    return getattr(workspace_gateway(context), "provider", "mock") != "mock"


def _json_object(text: str) -> Any:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object")
    return json.loads(text[start : end + 1])


async def _ask(context: TenantContext, instructions: str, prompt: str, blocks: Sequence[str]) -> str:
    gateway = route_gateway(workspace_gateway(context), ModelRoute.FAST)
    full = with_untrusted_rules(instructions, *blocks) if blocks else instructions
    generate = getattr(gateway, "generate_with_instructions", None)
    response = await generate(full, prompt) if callable(generate) else await gateway.generate_text(f"{full}\n\n{prompt}")
    return response.text


def evidence_blocks(evidence: Sequence[Evidence]) -> list[str]:
    return [
        label_untrusted(
            f"[{index}] {item.title}\nURL: {item.url}\nExcerpt: {item.excerpt}",
            source=Provenance.WEB,
            origin=item.url[:200],
            max_chars=1500,
        )
        for index, item in enumerate(evidence)
    ]


# -- research inferences ------------------------------------------------------------------------


class ProposedInference(BaseModel):
    statement: str
    criterion: Criterion | None = None
    confidence: Confidence = Confidence.LOW
    evidence: list[int] = Field(default_factory=list)


class _ModelInference(BaseModel):
    statement: str = ""
    criterion: str | None = None
    confidence: str = "low"
    evidence: list[int] = Field(default_factory=list)


class _ModelInferences(BaseModel):
    inferences: list[_ModelInference] = Field(default_factory=list)


RESEARCH_INSTRUCTIONS = (
    "You help a small business qualify a sales lead from public sources only. You get the lead's company and the "
    "seller's offer, then numbered public search results. Write at most four short inferences a person will review. "
    "Each one must rest on the numbered results you cite; if the results do not support a point, leave it out: "
    "unknown stays unknown. Never guess budgets, private details, people's phone numbers or anything not in the "
    "results. Criteria you may name: industry_fit, problem_evidence, reachable_channel, project_scale (never urgency "
    "or the buyer's authority: only a conversation confirms those). Answer with JSON only: {\"inferences\": "
    "[{\"statement\": str, \"criterion\": str|null, \"confidence\": \"low\"|\"medium\", \"evidence\": [int]}]}. "
    + PLAIN_PUNCTUATION_RULE
)


def template_inferences(account: Account, evidence: Sequence[Evidence]) -> list[ProposedInference]:
    """What the results show without a model: whether the official site appeared."""
    official = [index for index, item in enumerate(evidence) if account.domain and item.url_key.startswith(account.domain)]
    found: list[ProposedInference] = []
    if official:
        found.append(ProposedInference(
            statement=f"The company's own website ({account.domain}) appears in public search results; check it for an official business contact channel.",
            criterion=Criterion.REACHABLE_CHANNEL, confidence=Confidence.MEDIUM, evidence=official[:3],
        ))
    elif evidence:
        found.append(ProposedInference(
            statement=f"{len(evidence)} public result(s) mention {account.name}, but none is on the company's own website; its official channel is unknown.",
            criterion=None, confidence=Confidence.LOW, evidence=list(range(min(3, len(evidence)))),
        ))
    return found


async def research_inferences(
    context: TenantContext, account: Account, lead: Lead, product: Product | None, evidence: Sequence[Evidence]
) -> tuple[list[ProposedInference], Literal["model", "template"], str | None]:
    if not evidence:
        return [], "template", None
    if model_connected(context):
        prompt = "\n".join(line for line in (
            f"Company: {account.name}",
            f"Industry (as recorded): {account.industry or 'unknown'}",
            f"Location (as recorded): {', '.join(part for part in (account.city, account.country) if part) or 'unknown'}",
            f"Seller's offer: {product.name}: {product.summary}" if product else "Seller's offer: not chosen yet",
            f"Lead: {lead.title}",
            f"There are {len(evidence)} numbered results in the untrusted blocks.",
        ) if line)
        try:
            text = await _ask(context, RESEARCH_INSTRUCTIONS, prompt, evidence_blocks(evidence))
            parsed = _ModelInferences.model_validate(_json_object(text))
            found: list[ProposedInference] = []
            for item in parsed.inferences[:4]:
                statement = plain_dashes(" ".join(item.statement.split()))[:1000]
                cited = sorted({index for index in item.evidence if 0 <= index < len(evidence)})
                if len(statement) < 3 or not cited:
                    continue  # an inference without a source is not kept
                try:
                    criterion = Criterion(item.criterion) if item.criterion else None
                except ValueError:
                    criterion = None
                if criterion in CONVERSATION_CRITERIA:
                    criterion = None
                confidence = Confidence.MEDIUM if item.confidence == "medium" else Confidence.LOW
                found.append(ProposedInference(statement=statement, criterion=criterion, confidence=confidence, evidence=cited))
            return found, "model", None
        except ModelBudgetExceededError:
            raise
        except (ValueError, ValidationError, json.JSONDecodeError):
            logger.info("business_research_unreadable", extra={"anum_business": {"event": "research_unreadable"}})
            note = "The model's answer could not be used, so only the template inference was added."
        except Exception as exc:  # the model is down: the evidence is still saved
            logger.warning("business_research_model_failed error_class=%s", type(exc).__name__, extra={"anum_business": {"event": "research_model_failed", "error_class": type(exc).__name__}})
            note = "The model could not be reached, so only the template inference was added."
        return template_inferences(account, evidence), "template", note
    return template_inferences(account, evidence), "template", "No model is connected, so inferences come from a template."


def fit_summary(account: Account, lead: Lead, product: Product | None, evidence_count: int) -> str:
    """The research skill's draft fit explanation: recorded facts and what is still unknown."""
    unknown = [criterion.value.replace("_", " ") for criterion in Criterion if (lead.score.get(criterion.value) or None) is None or lead.score[criterion.value].score is None]
    parts = [
        f"Draft fit for review: {account.name}",
        f"({account.industry})" if account.industry else "(industry unknown)",
        f"and the offer {product.name}." if product else "with no offer chosen yet.",
        f"{evidence_count} public source(s) on file.",
        f"Still unknown: {', '.join(unknown)}." if unknown else "Every criterion is scored.",
    ]
    return " ".join(parts)


# -- outbound drafts ----------------------------------------------------------------------------


class DraftText(BaseModel):
    subject: str = ""
    body: str


def facts_text(product: Product | None, offer: Offer | None, evidence: Sequence[Evidence], account: Account, value_proposition: str = "") -> str:
    """Everything a draft may quote figures from: the catalog, the product's value proposition and the recorded sources."""
    parts = [account.name, account.city, account.country, value_proposition]
    if product:
        parts += [product.name, product.name_ar, product.summary]
    if offer:
        parts += [str(offer.price_min or ""), str(offer.price_max or ""), *offer.deliverables]
        parts += [f"{value:,}" for value in (offer.price_min, offer.price_max) if value is not None]
    parts += [item.title + " " + item.excerpt + " " + item.url for item in evidence]
    return " ".join(part for part in parts if part)


def check_draft(channel: DraftChannel, language: str, subject: str, body: str, facts: str) -> list[str]:
    """Deterministic checks a draft must pass before it can be approved."""
    issues: list[str] = []
    limit = CHANNEL_LIMITS[channel]
    if len(body) > limit:
        issues.append(f"The message is {len(body)} characters; the limit for this channel is {limit}.")
    if channel == DraftChannel.EMAIL and not subject.strip():
        issues.append("An e-mail needs a subject.")
    if channel != DraftChannel.CALL_SCRIPT and not OPT_OUT_WORDS.search(body):
        issues.append("Say how to opt out (for example: reply STOP).")
    if PROMISE_WORDS.search(subject + " " + body):
        issues.append("Remove guarantees and promises the business has not made.")
    if PLACEHOLDER.search(subject + " " + body):
        issues.append("Fill in or remove the placeholder in brackets.")
    known = {value.replace(",", "") for value in NUMBER.findall(facts)}
    for value in NUMBER.findall(subject + " " + body):
        cleaned = value.rstrip(".,").replace(",", "")
        if cleaned and cleaned not in known:
            issues.append(f"The figure {cleaned} is not in the catalog or the recorded sources.")
            break
    if chr(0x2014) in body or chr(0x2013) in body:
        issues.append("Use plain punctuation (no long dashes).")
    return issues


def _first_name(contact: Contact | None) -> str | None:
    if contact is None or not contact.name.strip():
        return None
    return contact.name.split()[0]


def template_draft(
    channel: DraftChannel, language: str, account: Account, contact: Contact | None, product: Product | None, evidence: Sequence[Evidence],
    value_proposition: str | None = None,
) -> DraftText:
    """``value_proposition``: the product's own pitch from its target customer profile (per language), else its summary."""
    name = _first_name(contact)
    source = evidence[0] if evidence else None
    pitch = (value_proposition or (product.summary if product else "") or "").strip().rstrip(".")
    if language == "ar":
        offer = (product.name_ar or product.name) if product else "خدماتنا"
        lines = [
            f"مرحبًا {name}،" if name else "مرحبًا،",
            f"أتواصل معكم بخصوص {account.name}. نقدّم {offer}" + (f": {pitch}." if pitch else "."),
        ]
        if source:
            lines.append(f"اطّلعت على ما نُشر هنا: {source.url}")
        lines.append("هل يناسبكم اتصال قصير لنعرف إن كان ذلك مفيدًا لكم؟")
        if channel != DraftChannel.CALL_SCRIPT:
            lines.append(OPT_OUT["ar"])
        subject = f"سؤال إلى {account.name}" if channel == DraftChannel.EMAIL else ""
    else:
        offer = product.name if product else "our services"
        lines = [
            f"Hello {name}," if name else "Hello,",
            f"I am getting in touch about {account.name}. We offer {offer}" + (f": {pitch}." if pitch else "."),
        ]
        if source:
            lines.append(f"I read what was published here: {source.url}")
        lines.append("Would a short call be useful to see whether this fits?")
        if channel != DraftChannel.CALL_SCRIPT:
            lines.append(OPT_OUT["en"])
        subject = f"A question for {account.name}" if channel == DraftChannel.EMAIL else ""
    if channel == DraftChannel.CALL_SCRIPT:
        intro = "ابدأ بالاستئذان: هل الوقت مناسب لدقيقتين؟" if language == "ar" else "Start by asking permission: is this a good time for two minutes?"
        lines.insert(0, intro)
    return DraftText(subject=subject, body="\n\n".join(plain_dashes(line) for line in lines))


DRAFT_INSTRUCTIONS = (
    "You write one short, honest first message from a small business to a company. Use only the facts given: the "
    "offer as the owner describes it and the numbered public sources. Never invent prices, results, clients, "
    "figures or promises; never use the word guarantee. Do not pretend to know the person. Ask one clear question. "
    "End with an opt-out line that says the reader can reply STOP (Arabic: إيقاف). Arabic: natural Modern Standard "
    "Arabic for Gulf readers. Answer with JSON only: {\"subject\": str, \"body\": str}. " + PLAIN_PUNCTUATION_RULE
)


async def draft_message(
    context: TenantContext,
    channel: DraftChannel,
    language: str,
    account: Account,
    contact: Contact | None,
    product: Product | None,
    offer: Offer | None,
    evidence: Sequence[Evidence],
    value_proposition: str | None = None,
) -> tuple[DraftText, Literal["model", "template"], str | None]:
    pitch = value_proposition or (product.summary if product else "")
    if model_connected(context):
        prompt = "\n".join(line for line in (
            f"Channel: {channel.value}",
            f"Language: {'Arabic' if language == 'ar' else 'English'}",
            f"Company: {account.name}",
            f"Recipient first name: {_first_name(contact) or 'unknown, greet without a name'}",
            f"Offer: {product.name}: {pitch}" if product else "Offer: not chosen, describe the business's services generally",
            f"Catalog price: {offer.currency.value} {offer.price_min}" if offer and offer.price_min is not None else "",
            f"Limit: {CHANNEL_LIMITS[channel]} characters",
        ) if line)
        try:
            text = await _ask(context, DRAFT_INSTRUCTIONS, prompt, evidence_blocks(evidence[:5]))
            parsed = DraftText.model_validate(_json_object(text))
            body = plain_dashes(parsed.body.strip())[:4000]
            if body:
                return DraftText(subject=plain_dashes(parsed.subject.strip())[:200], body=body), "model", None
        except ModelBudgetExceededError:
            raise
        except (ValueError, ValidationError, json.JSONDecodeError):
            logger.info("business_draft_unreadable", extra={"anum_business": {"event": "draft_unreadable"}})
        except Exception as exc:
            logger.warning("business_draft_model_failed error_class=%s", type(exc).__name__, extra={"anum_business": {"event": "draft_model_failed", "error_class": type(exc).__name__}})
        note = "The model's answer could not be used, so this draft comes from a template. Edit it before you approve it."
    else:
        note = "No model is connected, so this draft comes from a template. Connect a model in Settings for AI-written drafts."
    return template_draft(channel, language, account, contact, product, evidence, value_proposition), "template", note
