"""AI assist for posts: ideas, English and Arabic copy, hashtags (docs/marketing.md#ai-assist).

Every model call goes through the workspace's budgeted gateway (``budgeted_model_gateway``:
checked against the monthly budget before, metered after). The brief, product and
feature are the owner's own words; ``source_text`` (a store listing, a web page, a
competitor's post) is external data and reaches the model only inside a labelled
untrusted block, and everything built from it is flagged ``untrusted_source`` so the
composer asks for a review before it is scheduled.

Without a connected model (the ``mock`` provider) or when the model's answer cannot be
read, the copy comes from templates and says so (``generator: "template"``).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ..model_budget import ModelBudgetExceededError
from ..model_gateway import ModelGateway, ModelRoute, route_gateway
from ..prompt_provenance import Provenance, label_untrusted, with_untrusted_rules
from ..schemas import TenantContext
from .analytics import TimeSlot, best_times
from .clients import PLATFORM_LABEL
from .models import AssistHashtags, AssistIdeas, Language, Platform
from .platforms import SPECS, HASHTAG_PATTERN

logger = logging.getLogger("anum.marketing")

def learned_style(context: TenantContext) -> str | None:
    """The caller's learned writing style and preferences (docs/learning.md), when learning
    is on for them; ``None`` otherwise. Used with Settings' brand voice for drafts."""
    from ..learning_store import learned_instructions

    return learned_instructions(context, surface="task", tools=("marketing.draft_social_post",))


# Replaceable in tests; ``None`` turns the learned style off.
style_profile_lookup: Callable[[TenantContext], str | None] | None = learned_style

_gateway: ModelGateway | None = None


def default_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        from ..model_gateway import build_default_model_gateway

        _gateway = build_default_model_gateway()
    return _gateway


def workspace_gateway(context: TenantContext) -> ModelGateway:
    from ..onboarding import budgeted_model_gateway

    return budgeted_model_gateway(context, default_gateway())


class Idea(BaseModel):
    title: str
    angle: str
    platform: Platform
    en: str | None = None
    ar: str | None = None
    hashtags_en: list[str] = Field(default_factory=list)
    hashtags_ar: list[str] = Field(default_factory=list)


class IdeasResult(BaseModel):
    ideas: list[Idea]
    generator: Literal["model", "template"]
    untrusted_source: bool
    brand_voice_used: bool
    note: str | None = None
    best_times: list[TimeSlot] = Field(default_factory=list)


class HashtagResult(BaseModel):
    hashtags: list[str]
    generator: Literal["model", "template"]
    note: str | None = None


class _ModelIdea(BaseModel):
    title: str = ""
    angle: str = ""
    platform: str = ""
    en: str | None = None
    ar: str | None = None
    hashtags_en: list[str] = Field(default_factory=list)
    hashtags_ar: list[str] = Field(default_factory=list)


class _ModelIdeas(BaseModel):
    ideas: list[_ModelIdea]


INSTRUCTIONS = (
    "You write social media posts for a business in Saudi Arabia and the UAE (tyre e-commerce on Amazon and Noon, "
    "and the owner's apps such as TyrePulse and ANUM). Write for a Gulf audience. English: clear, friendly, no hype. "
    "Arabic: natural Modern Standard Arabic that reads well to Saudi and Emirati readers, written fresh rather than "
    "translated word for word, right to left, no English words except brand names. Respect each platform's limits. "
    "Never invent prices, discounts, ratings or claims that are not in the brief. "
    "Answer with JSON only, no prose: {\"ideas\": [{\"title\": str, \"angle\": str, \"platform\": str, \"en\": str|null, "
    "\"ar\": str|null, \"hashtags_en\": [str], \"hashtags_ar\": [str]}]}. Hashtags without the # sign."
)


def _brand_voice(context: TenantContext, explicit: str) -> str:
    voice = explicit.strip()
    if style_profile_lookup is not None:
        try:
            profile = (style_profile_lookup(context) or "").strip()
        except Exception:  # a missing profile never stops a draft
            profile = ""
        if profile:
            voice = f"{voice}\nWriting style learned from the owner: {profile}".strip()
    return voice


def _json_object(text: str) -> Any:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object")
    return json.loads(text[start : end + 1])


def _clean_tags(values: list[str], limit: int) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        tag = re.sub(r"[^\w؀-ۿ]", "", value.strip().lstrip("#").replace(" ", "_"))
        if tag and tag.lower() not in {item.lower() for item in cleaned}:
            cleaned.append(tag[:60])
    return cleaned[:limit]


def _fit(text: str, platform: Platform) -> str:
    limit = SPECS[platform].text_limit
    if platform == Platform.X:
        limit = 250  # room for hashtags and the link
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def ideas(context: TenantContext, request: AssistIdeas, *, brand_voice: str, default_tags: dict[Language, list[str]], tz: str) -> IdeasResult:
    voice = _brand_voice(context, brand_voice)
    untrusted = bool(request.source_text.strip())
    slots = best_times(context, platform=request.platforms[0] if len(request.platforms) == 1 else None, tz=tz)
    gateway = workspace_gateway(context)
    if getattr(gateway, "provider", "mock") != "mock":
        brief = "\n".join(
            line for line in (
                f"Brief: {request.brief}" if request.brief else "",
                f"Product or app: {request.product}" if request.product else "",
                f"Feature to promote: {request.feature}" if request.feature else "",
                f"Platforms: {', '.join(PLATFORM_LABEL[platform] for platform in request.platforms)}",
                f"Languages: {', '.join('English' if language == Language.EN else 'Arabic' for language in request.languages)}",
                f"Number of ideas: {request.count}",
                f"Brand voice: {voice}" if voice else "",
            ) if line
        )
        blocks = [label_untrusted(request.source_text, source=Provenance.WEB, origin=request.source_label or "owner-supplied-source", max_chars=6000)] if untrusted else []
        instructions = with_untrusted_rules(INSTRUCTIONS, *blocks) if blocks else INSTRUCTIONS
        try:
            routed = route_gateway(gateway, ModelRoute.FAST)
            generate = getattr(routed, "generate_with_instructions", None)
            response = await generate(instructions, brief) if callable(generate) else await routed.generate_text(f"{instructions}\n\n{brief}")
            parsed = _ModelIdeas.model_validate(_json_object(response.text))
            found = [_from_model(item, request) for item in parsed.ideas[: request.count]]
            if found:
                return IdeasResult(ideas=found, generator="model", untrusted_source=untrusted, brand_voice_used=bool(voice), best_times=slots)
        except ModelBudgetExceededError:
            raise
        except (ValueError, ValidationError, json.JSONDecodeError):
            logger.info("marketing_assist_unreadable", extra={"anum_marketing": {"event": "assist_unreadable"}})
        except Exception as exc:  # the model is down: templates still give the owner a start
            logger.warning("marketing_assist_failed error_class=%s", type(exc).__name__, extra={"anum_marketing": {"event": "assist_failed", "error_class": type(exc).__name__}})
        note = "The model's answer could not be used, so these drafts come from templates. Try again, or edit them."
    else:
        note = "No model is connected, so these drafts come from templates. Connect a model in Settings for AI-written copy."
    return IdeasResult(
        ideas=template_ideas(request, default_tags), generator="template", untrusted_source=untrusted,
        brand_voice_used=False, note=note, best_times=slots,
    )


def _from_model(item: _ModelIdea, request: AssistIdeas) -> Idea:
    try:
        platform = Platform(item.platform.lower().strip())
    except ValueError:
        platform = request.platforms[0]
    if platform not in request.platforms:
        platform = request.platforms[0]
    return Idea(
        title=item.title.strip()[:120] or "Idea",
        angle=item.angle.strip()[:300],
        platform=platform,
        en=_fit(item.en.strip(), platform) if item.en and Language.EN in request.languages else None,
        ar=_fit(item.ar.strip(), platform) if item.ar and Language.AR in request.languages else None,
        hashtags_en=_clean_tags(item.hashtags_en, 10),
        hashtags_ar=_clean_tags(item.hashtags_ar, 10),
    )


# -- templates -------------------------------------------------------------------------------

_KEYWORDS: list[tuple[re.Pattern[str], list[str], list[str]]] = [
    (re.compile(r"\btyres?\b|\btires?\b|إطار", re.IGNORECASE), ["Tyres", "TyreSafety"], ["إطارات", "سلامة_الإطارات"]),
    (re.compile(r"\bapp\b|تطبيق", re.IGNORECASE), ["App"], ["تطبيق"]),
    (re.compile(r"\boffer\b|\bsale\b|\bdiscount\b|عرض|خصم", re.IGNORECASE), ["Offer"], ["عروض"]),
    (re.compile(r"\bcar\b|\bcars\b|سيار", re.IGNORECASE), ["CarCare"], ["العناية_بالسيارة"]),
    (re.compile(r"\bamazon\b|أمازون", re.IGNORECASE), ["Amazon"], ["أمازون"]),
    (re.compile(r"\bnoon\b|نون", re.IGNORECASE), ["Noon"], ["نون"]),
]


def template_hashtags(text: str, language: Language, defaults: list[str], limit: int = 6) -> list[str]:
    tags: list[str] = []
    for pattern, english, arabic in _KEYWORDS:
        if pattern.search(text):
            tags.extend(english if language == Language.EN else arabic)
    for word in re.findall(r"\b[A-Z][A-Za-z0-9]{2,40}\b", text):
        tags.append(word)
    tags.extend(defaults)
    tags.extend(["KSA", "UAE"] if language == Language.EN else ["السعودية", "الإمارات"])
    return _clean_tags(tags, limit)


_ANGLES = [
    ("Feature spotlight", "Show one thing it does and why it matters.", "ميزة اليوم"),
    ("Problem and fix", "Start from a problem the audience knows, then the fix.", "مشكلة وحلّها"),
    ("Quick tip", "A useful tip that earns trust.", "نصيحة سريعة"),
    ("Call to action", "A clear reason to act now.", "ادعُ للتجربة"),
    ("Behind the scenes", "How it is made or checked.", "من خلف الكواليس"),
    ("Ask the audience", "A question that invites replies.", "سؤال للمتابعين"),
]


def template_ideas(request: AssistIdeas, default_tags: dict[Language, list[str]]) -> list[Idea]:
    subject = request.product.strip() or request.feature.strip() or "our product"
    feature = request.feature.strip()
    brief = " ".join(request.brief.split())
    result: list[Idea] = []
    for index in range(request.count):
        title, angle, title_ar = _ANGLES[index % len(_ANGLES)]
        platform = request.platforms[index % len(request.platforms)]
        en = ar = None
        if Language.EN in request.languages:
            body = {
                0: f"{subject}: {feature or brief or 'built for drivers in the Gulf'}.",
                1: f"Not sure when your tyres need attention? {subject} {('helps with ' + feature) if feature else 'makes it simple'}.",
                2: f"Tip: {brief or 'check your tyre pressure every month, and before long drives'}.",
                3: f"Try {subject} today{(' - ' + feature) if feature else ''}.",
                4: f"How we build {subject}: {brief or 'tested on real roads in Saudi Arabia and the UAE'}.",
                5: f"What would you like {subject} to do next?",
            }[index % 6]
            en = _fit(body, platform)
        if Language.AR in request.languages:
            subject_ar = subject if subject != "our product" else "منتجنا"
            body_ar = {
                0: f"{subject_ar}: {feature or brief or 'مصمم للسائقين في الخليج'}.",
                1: f"لا تعرف متى تحتاج إطاراتك إلى فحص؟ {subject_ar} يجعل ذلك سهلاً.",
                2: f"نصيحة: {brief or 'افحص ضغط الإطارات كل شهر وقبل الرحلات الطويلة'}.",
                3: f"جرّب {subject_ar} اليوم.",
                4: f"كيف نطوّر {subject_ar}: {brief or 'نختبره على طرق السعودية والإمارات'}.",
                5: f"ما الذي تودّ أن يقدّمه {subject_ar} لاحقًا؟",
            }[index % 6]
            ar = _fit(body_ar, platform)
        source = " ".join(part for part in (request.product, request.feature, request.brief) if part)
        result.append(Idea(
            title=title if Language.EN in request.languages else title_ar,
            angle=angle,
            platform=platform,
            en=en,
            ar=ar,
            hashtags_en=template_hashtags(source, Language.EN, default_tags.get(Language.EN, [])) if en else [],
            hashtags_ar=template_hashtags(source, Language.AR, default_tags.get(Language.AR, [])) if ar else [],
        ))
    return result


async def hashtags(context: TenantContext, request: AssistHashtags, *, defaults: list[str]) -> HashtagResult:
    spec = SPECS[request.platform]
    limit = min(spec.hashtag_recommended or 10, spec.hashtag_limit or 30)
    present = {tag.lower() for tag in HASHTAG_PATTERN.findall(request.text)}
    gateway = workspace_gateway(context)
    if getattr(gateway, "provider", "mock") != "mock":
        language = "Arabic" if request.language == Language.AR else "English"
        prompt = (
            f"Suggest up to {limit} {language} hashtags for this {PLATFORM_LABEL[request.platform]} post for a Gulf audience. "
            "Mix broad and specific tags; no banned or misleading tags. Answer with JSON only: {\"hashtags\": [str]} without #.\n\n"
            f"Post:\n{request.text[:3000]}"
        )
        try:
            response = await route_gateway(gateway, ModelRoute.FAST).generate_text(prompt)
            found = _clean_tags(list(_json_object(response.text).get("hashtags") or []), limit)
            found = [tag for tag in found if tag.lower() not in present]
            if found:
                return HashtagResult(hashtags=found, generator="model")
        except ModelBudgetExceededError:
            raise
        except Exception as exc:
            logger.info("marketing_hashtags_fallback error_class=%s", type(exc).__name__, extra={"anum_marketing": {"event": "hashtags_fallback", "error_class": type(exc).__name__}})
        note = "The model's suggestions could not be used; these come from the post's own words."
    else:
        note = "No model is connected; these come from the post's own words."
    found = [tag for tag in template_hashtags(request.text, request.language, defaults, limit) if tag.lower() not in present]
    return HashtagResult(hashtags=found, generator="template", note=note)
