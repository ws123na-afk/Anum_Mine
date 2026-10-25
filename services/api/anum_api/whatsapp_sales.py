"""Bilingual WhatsApp sales-lead qualification without guessing commercial terms.

The classifier is intentionally deterministic and requires no extra LLM call.
It is safe to use for inbox suggestions and CRM prefill, not as proof that a
customer has agreed to a proposal or opted in to marketing.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode
from dataclasses import dataclass
from enum import StrEnum


class SalesIntent(StrEnum):
    WEBSITE = "website"
    MOBILE_APP = "mobile_app"
    BUSINESS_SOFTWARE = "business_software"
    ECOMMERCE = "ecommerce"
    OTHER = "other"


_TERMS: tuple[tuple[SalesIntent, tuple[str, ...]], ...] = (
    (SalesIntent.ECOMMERCE, ("online store", "ecommerce", "e-commerce", "shopify", "متجر", "تجارة إلكترونية", "متجر إلكتروني")),
    (SalesIntent.MOBILE_APP, ("mobile app", "iphone app", "android app", "ios app", "تطبيق جوال", "تطبيق ايفون", "تطبيق آيفون", "تطبيق اندرويد", "تطبيق أندرويد")),
    (SalesIntent.BUSINESS_SOFTWARE, ("erp", "crm", "fleet management", "inventory system", "custom software", "نظام إدارة", "برنامج محاسبة", "إدارة المخزون", "نظام مخزون", "برمجيات", "نظام موارد")),
    (SalesIntent.WEBSITE, ("website", "web site", "landing page", "company site", "موقع", "موقع إلكتروني", "موقع الكتروني", "صفحة هبوط")),
)
_ARABIC = re.compile(r"[\u0600-\u06ff]")
_URGENT = ("urgent", "asap", "today", "immediately", "عاجل", "اليوم", "بأسرع", "مستعجل")
_HUMAN = ("human", "person", "agent", "manager", "call me", "موظف", "شخص", "مدير", "اتصل", "كلمني")
_SENSITIVE = ("refund", "complaint", "legal", "lawsuit", "payment dispute", "استرجاع", "شكوى", "محكمة", "محامي", "نزاع", "احتيال")


@dataclass(frozen=True)
class LeadQualification:
    intent: SalesIntent
    language: str
    urgent: bool
    requires_human: bool
    follow_up_question: str


_QUESTIONS: dict[SalesIntent, dict[str, str]] = {
    SalesIntent.WEBSITE: {
        "ar": "هل تحتاج موقعاً تعريفياً للشركة أم موقعاً بخدمات خاصة؟ وما مجال نشاطك؟",
        "en": "Do you need a company website or a website with custom features? What industry are you in?",
    },
    SalesIntent.MOBILE_APP: {
        "ar": "هل التطبيق مطلوب للآيفون والأندرويد؟ وما أهم وظيفة تريدها؟",
        "en": "Do you need iPhone, Android or both? What is the main feature?",
    },
    SalesIntent.BUSINESS_SOFTWARE: {
        "ar": "ما العمليات التي تريد إدارتها بالنظام؟ وكم عدد المستخدمين المتوقع؟",
        "en": "Which business processes should the system manage, and how many users do you expect?",
    },
    SalesIntent.ECOMMERCE: {
        "ar": "ما نوع المنتجات وكم عددها تقريباً؟ وهل تحتاج الدفع والتوصيل داخل السعودية؟",
        "en": "What do you sell and about how many products? Do you need Saudi payment and delivery integration?",
    },
    SalesIntent.OTHER: {
        "ar": "ما نوع المشروع أو الخدمة التي تحتاجها؟",
        "en": "What kind of project or service are you looking for?",
    },
}


def qualify_lead(message: str) -> LeadQualification:
    """Give a safe first follow-up for an inbound enquiry, without personal data."""
    text = (message or "").casefold()[:4000]
    language = "ar" if _ARABIC.search(text) else "en"
    intent = next((kind for kind, terms in _TERMS if any(term in text for term in terms)), SalesIntent.OTHER)
    urgent = any(term in text for term in _URGENT)
    requires_human = any(term in text for term in _HUMAN + _SENSITIVE)
    return LeadQualification(
        intent=intent,
        language=language,
        urgent=urgent,
        requires_human=requires_human,
        follow_up_question=_QUESTIONS[intent][language],
    )


# Approved campaign identifiers travel in a click-to-WhatsApp prefilled message.
# This links Marketing campaign attribution to inbound WhatsApp conversations
# without creating contacts or sending unsolicited messages.
_CAMPAIGN = re.compile(r"(?<![a-z0-9_])ANUM-CAMPAIGN:([a-z0-9][a-z0-9_-]{0,63})(?![a-z0-9_-])", re.IGNORECASE)
_SAUDI_NUMBER = "966507676416"


def campaign_marker(campaign_slug: str) -> str:
    """Validate a marketing slug so customer-provided text cannot inject a marker."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", campaign_slug):
        raise ValueError("Campaign slug must contain 1-64 lowercase letters, numbers, _ or -")
    return f"ANUM-CAMPAIGN:{campaign_slug}"


def whatsapp_campaign_link(campaign_slug: str, *, language: str = "ar", phone: str = _SAUDI_NUMBER) -> str:
    """Build an opt-in click-to-chat link for approved Marketing campaigns."""
    marker = campaign_marker(campaign_slug)
    if language not in {"ar", "en"}:
        raise ValueError("Unsupported language")
    if not re.fullmatch(r"[1-9][0-9]{7,14}", phone):
        raise ValueError("Invalid international phone number")
    introduction = (
        "مرحباً، أريد الاستفسار عن خدمات المواقع والتطبيقات من ANUM 360"
        if language == "ar"
        else "Hello, I would like to learn about ANUM 360 websites and apps"
    )
    return f"https://wa.me/{phone}?{urlencode({'text': introduction + chr(10) + marker})}"


def inbound_campaign_marker(message: str) -> str | None:
    """Read the explicit marker from inbound text; never trust it as verified attribution."""
    match = _CAMPAIGN.search((message or "")[:4000])
    return match.group(1).lower() if match else None
