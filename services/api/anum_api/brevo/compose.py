"""The full email for a planned Brevo campaign draft (docs/brevo.md#planner).

``compose`` turns the planner's subject, preview text, outline and the user's own brief
into a complete HTML email for a person to review and approve:

* **Brand.** The organisation's display name (Settings, Branding) signs the email and
  heads the built-in layout; Marketing's brand voice and the user's learned style steer
  the model's wording.
* **The owner's Brevo template, when there is one.** The newest active Brevo template that
  carries the marker ``{{ params.anum_content }}`` (preferring one tagged ``anum``) is the
  layout: the body replaces the marker and the rest of the template is kept as written.
  Without one, a plain, table-based layout with the brand name, the preview text, the
  sections and Brevo's ``{{ unsubscribe }}`` link is used.
* **The words.** With a connected model (through the workspace's budgeted gateway) the
  model writes each section from the brief and the brand voice; its answer is plain text,
  escaped before it goes into the HTML. Without a model, or when its answer cannot be
  read, the sections come from templates in the request's language and say so.

Nothing here sends anything: the draft still needs an owner's approval of the exact
content (``brevo.send_campaign``). Template HTML from Brevo never reaches a model.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from html import escape
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ..model_budget import ModelBudgetExceededError
from ..model_gateway import ModelRoute, route_gateway
from ..punctuation import PLAIN_PUNCTUATION_RULE, plain_dashes
from ..schemas import TenantContext
from .client import BrevoClient, BrevoError, Template
from .models import MAX_HTML_CHARS

logger = logging.getLogger("anum.brevo")

MARKER = "{{ params.anum_content }}"
# Optional: where an owner's template wants the organisation logo.
LOGO_MARKER = "{{ params.anum_logo }}"
MARKER_TAG = "anum"
# A handful of templates keeps the answer well under the client's response cap.
TEMPLATE_PAGE = 10

INSTRUCTIONS = (
    "You write marketing email newsletters for a small business. Write the body of one email from the brief, "
    "following the outline section by section. Plain text only, no HTML or Markdown. Friendly, clear, no hype. "
    "Never invent prices, discounts, dates, ratings or claims that are not in the brief. "
    "Write in the language requested; Arabic is natural Modern Standard Arabic written fresh, not translated. "
    "Answer with JSON only: {\"sections\": [{\"heading\": str, \"body\": str}], \"button\": str}. "
    + PLAIN_PUNCTUATION_RULE
)


@dataclass(frozen=True)
class Composed:
    html: str
    generator: Literal["model", "template"]
    template_id: int | None
    brand_name: str
    logo_url: str | None = None
    note: str | None = None


class _Section(BaseModel):
    heading: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=2000)


class _Answer(BaseModel):
    sections: list[_Section]
    button: str = Field(default="", max_length=60)


# -- brand --------------------------------------------------------------------------------------


def _brand(context: TenantContext) -> tuple[str, str, str | None]:
    """The organisation's display name, the brand voice (with the learned style) and a
    long-lived public link to its logo, when it has one."""
    from ..marketing.assist import _brand_voice
    from ..marketing.service import preferences
    from ..organisations import branding_for, logo_link

    try:
        name = branding_for(context).display_name
    except Exception:  # noqa: BLE001 - a missing branding row never stops a draft
        name = ""
    try:
        voice = _brand_voice(context, preferences(context).brand_voice)
    except Exception:  # noqa: BLE001 - nor do missing marketing preferences
        voice = ""
    try:
        logo = logo_link(context)
    except Exception:  # noqa: BLE001 - nor does a logo that cannot be linked: the name heads the email
        logo = None
    return " ".join(name.split())[:160], voice[:2000], logo


def logo_html(logo_url: str | None, brand: str, *, rtl: bool) -> str:
    """The header logo with alt text, or "" without one (the brand name then heads the email)."""
    if not logo_url:
        return ""
    alt = (f"شعار {brand}" if rtl else f"{brand} logo") if brand else ("الشعار" if rtl else "Logo")
    return (f'<img src="{escape(logo_url, quote=True)}" alt="{escape(alt, quote=True)}" width="160" '
            f'style="display:block;max-width:160px;height:auto;border:0;outline:none;text-decoration:none">')


def pick_template(templates: list[Template]) -> Template | None:
    """The newest active template with the content marker, one tagged ``anum`` first."""
    usable = [item for item in templates if item.active and MARKER in item.html and len(item.html) <= MAX_HTML_CHARS]
    tagged = [item for item in usable if item.tag.strip().lower() == MARKER_TAG]
    return (tagged or usable or [None])[0]


async def _template(client: BrevoClient) -> Template | None:
    try:
        return pick_template(await client.templates(limit=TEMPLATE_PAGE))
    except BrevoError as error:
        # No template permission or none at all: the built-in layout is used.
        logger.info("brevo_templates_unavailable", extra={"anum_brevo": {"event": "templates_unavailable", "status": error.status}})
        return None


# -- words --------------------------------------------------------------------------------------


_TEMPLATE_TEXT = {
    "en": {
        "opening": "Hello from {brand}. Here is our {about} newsletter, with the news and offers we picked for you.",
        "main": "This {about} edition brings the news and offers that matter most to you. Read on for the details below.",
        "offer": "Reply to this email or visit us to take advantage of it while it lasts.",
        "closing": "Thank you for reading. If you have a question, just reply to this email and our team will help.",
        "button": "Find out more",
    },
    "ar": {
        "opening": "مرحبا من {brand}. هذه نشرة {about}، وفيها أخبار وعروض اخترناها لك.",
        "main": "يجمع هذا العدد من نشرة {about} أهم الأخبار والعروض التي تهمك. تابع القراءة لمعرفة التفاصيل.",
        "offer": "رد على هذه الرسالة أو تواصل معنا للاستفادة من العرض قبل انتهائه.",
        "closing": "شكرا لقراءتك. إذا كان لديك سؤال فرد على هذه الرسالة وسيساعدك فريقنا.",
        "button": "اعرف المزيد",
    },
}
_KINDS = ("opening", "main", "offer", "closing")


def template_sections(outline: list[str], *, brand: str, about: str, brief: str, language: str) -> tuple[list[_Section], str]:
    words = _TEMPLATE_TEXT["ar" if language == "ar" else "en"]
    sections: list[_Section] = []
    for index, item in enumerate(outline[:12]):
        heading, _, note = item.partition(":")
        kind = _KINDS[index] if index < len(_KINDS) else "main"
        body = words[kind].format(brand=brand or ("فريقنا" if language == "ar" else "our team"), about=about, brief=brief or note.strip())
        sections.append(_Section(heading=heading.strip()[:200], body=body[:2000]))
    return sections, words["button"]


async def _model_sections(context: TenantContext, *, subject: str, outline: list[str], brief: str, brand: str, voice: str,
                          language: str) -> tuple[list[_Section], str] | None:
    from ..marketing.assist import workspace_gateway

    gateway = workspace_gateway(context)
    if getattr(gateway, "provider", "mock") == "mock":
        return None
    request = "\n".join(line for line in (
        f"Brief (the user's own words): {brief}",
        f"Subject: {subject}",
        "Outline: " + " | ".join(outline[:12]),
        f"Business name: {brand}" if brand else "",
        f"Brand voice: {voice}" if voice else "",
        f"Language: {'Arabic' if language == 'ar' else 'English'}",
    ) if line)
    try:
        routed = route_gateway(gateway, ModelRoute.FAST)
        generate = getattr(routed, "generate_with_instructions", None)
        response = await generate(INSTRUCTIONS, request) if callable(generate) else await routed.generate_text(f"{INSTRUCTIONS}\n\n{request}")
        text = response.text
        parsed = _Answer.model_validate(json.loads(text[text.find("{"): text.rfind("}") + 1]))
        sections = [item for item in parsed.sections[:12] if item.heading.strip() or item.body.strip()]
        return (sections, parsed.button) if sections else None
    except ModelBudgetExceededError:
        raise
    except (ValueError, ValidationError, json.JSONDecodeError):
        logger.info("brevo_compose_unreadable", extra={"anum_brevo": {"event": "compose_unreadable"}})
    except Exception as exc:  # noqa: BLE001 - the model is down: templates still give a full draft
        logger.warning("brevo_compose_failed", extra={"anum_brevo": {"event": "compose_failed", "error_class": type(exc).__name__}})
    return None


# -- HTML ---------------------------------------------------------------------------------------


def _paragraphs(text: str) -> str:
    parts = [escape(plain_dashes(part.strip())) for part in text.split("\n") if part.strip()]
    return "".join(f'<p style="margin:0 0 12px;font-size:16px;line-height:1.5">{part}</p>' for part in parts)


def body_html(sections: list[_Section], button: str, *, rtl: bool) -> str:
    align = "right" if rtl else "left"
    parts = []
    for section in sections:
        if section.heading.strip():
            parts.append(f'<h2 style="margin:24px 0 8px;font-size:20px;text-align:{align}">{escape(plain_dashes(section.heading.strip()))}</h2>')
        parts.append(_paragraphs(section.body))
    if button.strip():
        # No link is guessed: the reviewer sets the button's address before asking to send.
        parts.append(f'<p style="margin:24px 0"><a href="#" style="display:inline-block;padding:12px 20px;background:#1f2937;color:#ffffff;'
                     f'text-decoration:none;border-radius:6px">{escape(plain_dashes(button.strip()))}</a></p>')
    return "\n".join(parts)


def layout(*, subject: str, preview_text: str, body: str, brand: str, rtl: bool, logo_url: str | None = None) -> str:
    direction = "rtl" if rtl else "ltr"
    lang = "ar" if rtl else "en"
    footer = "تصلك هذه الرسالة لأنك مشترك في قائمتنا." if rtl else "You receive this email because you subscribed to our list."
    leave = "إلغاء الاشتراك" if rtl else "Unsubscribe"
    return (
        f'<!doctype html><html lang="{lang}" dir="{direction}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(subject)}</title></head>'
        f'<body style="margin:0;padding:0;background:#f4f4f5;font-family:Arial,Helvetica,sans-serif;color:#111827">'
        f'<div style="display:none;max-height:0;overflow:hidden">{escape(preview_text)}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:8px">'
        f'<tr><td style="padding:24px 24px 0;font-size:14px;font-weight:bold;color:#374151">{logo_html(logo_url, brand, rtl=rtl) or escape(brand)}</td></tr>'
        f'<tr><td style="padding:8px 24px 24px" dir="{direction}"><h1 style="margin:0 0 16px;font-size:26px">{escape(subject)}</h1>\n{body}\n</td></tr>'
        f'<tr><td style="padding:16px 24px;font-size:12px;color:#6b7280;border-top:1px solid #e5e7eb">{escape(brand)}. {footer} '
        f'<a href="{{{{ unsubscribe }}}}" style="color:#6b7280">{leave}</a></td></tr>'
        f'</table></td></tr></table></body></html>'
    )


async def compose(context: TenantContext, client: BrevoClient, *, subject: str, preview_text: str, outline: list[str], brief: str,
                  about: str, language: str) -> Composed:
    """The full HTML email for a planned draft."""
    brand, voice, logo_url = await asyncio.to_thread(_brand, context)
    rtl = language == "ar"
    note: str | None = None
    written = await _model_sections(context, subject=subject, outline=outline, brief=brief, brand=brand, voice=voice, language=language)
    generator: Literal["model", "template"] = "model"
    if written is None:
        generator = "template"
        written = template_sections(outline, brand=brand, about=about, brief=brief, language=language)
        note = "The email text comes from templates; edit it before asking to send."
    sections, button = written
    body = body_html(sections, button, rtl=rtl)
    template = await _template(client)
    if template is not None:
        html = template.html.replace(MARKER, f'<div dir="{"rtl" if rtl else "ltr"}">{body}</div>', 1)
        html = html.replace(LOGO_MARKER, logo_html(logo_url, brand, rtl=rtl))
    else:
        html = layout(subject=subject, preview_text=preview_text, body=body, brand=brand, rtl=rtl, logo_url=logo_url)
    return Composed(html=html[:MAX_HTML_CHARS], generator=generator, template_id=template.id if template else None, brand_name=brand, note=note,
                    logo_url=logo_url)


def describe(composed: Composed) -> dict[str, Any]:
    return {"generator": composed.generator, "brevo_template_id": composed.template_id, "brand": composed.brand_name, "logo": composed.logo_url is not None}
