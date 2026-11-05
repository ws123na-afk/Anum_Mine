"""Company details and the facts that fill the legal pages (docs/selling-readiness.md#company-and-legal).

The platform owner's company (the billing issuer) enters its legal name, registration, address,
contacts, data protection officer, server location and governing law once, plus the other facts
the legal templates in ``docs/legal`` need (``legal_terms``: only the keys in :data:`TERM_KEYS`).
The public legal pages read them through ``GET /api/v1/public/legal`` and show "Not set yet"
for anything missing; invoices print the legal name, address, registration and VAT number.

Some facts are not typed by anyone: they come from what this deployment actually runs
(:func:`generated_values`): the service address, retention periods, the deletion cooling-off,
and which subprocessors are in use (the model provider, file storage, SMTP, push, speech,
Moyasar and Stripe). A generated value always wins over a typed one, so the pages cannot
claim a service that is turned off.

:data:`DOC_KEYS` lists the placeholders of each document; ``tests/test_selling_readiness.py``
reads ``docs/legal/*.md`` and fails when a document gains a placeholder that is not here.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

from ..settings import settings
from .rows import memory_db, reader_rows

DOC_IDS = ("terms", "privacy", "dpa", "aup", "subprocessors")
COUNTRY_NAMES = {"SA": "Saudi Arabia", "AE": "United Arab Emirates"}
NOT_USED = "Not used"
PER_WORKSPACE = "Only when a workspace connects its own account"

# Company fields: (name, max length). Typed by the platform owner in the console.
COMPANY_FIELDS: tuple[tuple[str, int], ...] = (
    ("legal_name", 200), ("trading_name", 200), ("registration_number", 80), ("registration_country", 2),
    ("registration_authority", 200), ("address", 600), ("contact_email", 254), ("support_contact", 254),
    ("phone", 40), ("vat_number", 40), ("dpo_name", 160), ("dpo_email", 254), ("security_email", 254),
    ("server_location", 200), ("hosting_provider", 20), ("governing_law", 300),
)
# What "company details set" means on the Ready to sell checklist.
REQUIRED_COMPANY = ("legal_name", "registration_number", "registration_country", "address", "contact_email", "dpo_email", "server_location")
HOSTING = ("", "oracle", "render", "other")

# Facts only the owner (with a lawyer) can give, by key: (English label, help). The legal
# documents are English only, so these labels are too (the console says so in Arabic).
TERM_KEYS: dict[str, tuple[str, str]] = {
    "EFFECTIVE_DATE": ("Effective date", "The date the reviewed documents take effect, for example 1 December 2026."),
    "APPLICABLE_LAW_CONFIRMATION": ("Which data protection law applies", "Counsel's statement of which law (Saudi PDPL, UAE PDPL, a free zone regime) applies to whom."),
    "LEGAL_BASIS_CONTRACT": ("Lawful basis: providing the Service", "For example: performance of the contract with the customer."),
    "LEGAL_BASIS_SECURITY": ("Lawful basis: security", "For example: our legitimate interest in keeping the Service secure."),
    "LEGAL_BASIS_SUPPORT": ("Lawful basis: support", "For example: performance of the contract, or consent."),
    "LEGAL_BASIS_BILLING": ("Lawful basis: billing", "For example: performance of the contract and the legal obligation to keep accounting records."),
    "BILLING_RECORD_RETENTION": ("How long billing records are kept", "For example: 10 years after the end of the financial year, as Saudi and UAE accounting and tax law require."),
    "CROSS_BORDER_TRANSFER_STATEMENT": ("Cross-border transfer statement", "How transfers outside Saudi Arabia or the UAE are made lawful, for example adequacy or standard contractual clauses."),
    "TRANSFER_MECHANISM": ("Transfer mechanism (DPA)", "The same, in the words the Data Processing Addendum uses."),
    "RESPONSE_DAYS": ("Days to answer a privacy request", "A number of days, for example 30."),
    "DSR_ASSISTANCE_DAYS": ("Days to help a customer with a request", "A number of days, for example 10."),
    "BREACH_NOTICE_HOURS": ("Hours to tell a customer about a breach", "A number of hours, for example 48. The Saudi PDPL gives SDAIA 72 hours; counsel confirms."),
    "SUPERVISORY_AUTHORITY": ("Supervisory authority", "For example: the Saudi Data and Artificial Intelligence Authority (SDAIA), sdaia.gov.sa."),
    "MINIMUM_AGE": ("Minimum age", "For example: 18."),
    "CHANGE_NOTICE_METHOD": ("How changes are announced", "For example: by email to each organization's billing contact."),
    "CHANGE_NOTICE_PERIOD": ("Notice before material changes", "For example: 30 days."),
    "ERROR_REPORT_RETENTION_DAYS": ("Days error reports are kept", "A number of days; the self-hosted default is 30."),
    "LOCAL_BACKUP_DAYS": ("Days backups stay on the server", "Filled from the last backup when the server records it; the default is 7."),
    "OFFSITE_BACKUP_DAYS": ("Days off-site backups are kept", "The bucket's lifecycle rule; the configured rule is 35."),
    "FILE_STORAGE_REGION": ("Where files and backups are stored", "Filled from the storage settings when they name Backblaze B2."),
    "B2_REGION": ("Backblaze B2 region", "Filled from the storage settings when they name Backblaze B2."),
    "HOST_DISK_ENCRYPTION_CONFIRMATION": ("Server disk encryption", "Confirm the hosting provider encrypts the boot and block volumes (Oracle does by default)."),
    "STAFF_ACCESS_CONTROLS": ("Staff access controls", "Who on your side can reach customer data and how that access is limited and logged."),
    "AUDIT_RIGHTS": ("Customer audit rights", "What a customer may ask for to check compliance."),
    "SUBPROCESSOR_NOTICE_DAYS": ("Days of notice before a new subprocessor", "A number of days, for example 30."),
    "SUBPROCESSOR_NOTICE_METHOD": ("How a new subprocessor is announced", "For example: on this page and by email to each billing contact."),
    "EXPORT_WINDOW": ("Export window after the end", "For example: 30 days."),
    "DELETION_DAYS": ("Days to delete after the end", "A number of days, for example 30."),
    "ENFORCEMENT_DETAILS": ("Enforcement details (acceptable use)", "What happens after a breach of the acceptable use policy."),
    "DISPUTE_FORUM": ("Disputes", "For example: the courts of Riyadh, or arbitration at the Saudi Center for Commercial Arbitration."),
    "FEATURE_REMOVAL_NOTICE": ("Notice before removing a paid feature", "For example: 90 days."),
    "FEES_AND_PAYMENT_TERMS": ("Fees and payment terms", "How fees, taxes, invoices, late payment and refunds work."),
    "SLA_REFERENCE": ("Service level", "For example: none, or a link to a separate service level agreement."),
    "CONFIDENTIALITY_DETAILS": ("Confidentiality details", "Any exceptions and how long confidentiality lasts."),
    "WARRANTY_AND_DISCLAIMER_TEXT": ("Warranties and disclaimers", "The clause counsel writes."),
    "LIABILITY_CAP_AND_EXCLUSIONS": ("Limitation of liability", "The clause counsel writes."),
    "TERMINATION_NOTICE": ("Notice to end the agreement", "For example: 30 days."),
    "CURE_PERIOD": ("Time to fix a breach", "For example: 30 days."),
    "WORKSPACE_EXPORT_COMMITMENT": ("Organisation export commitment", "For example: organisation administrators can export everything under Settings > Workspace."),
    "ANTHROPIC_REGION": ("Anthropic location", "Only when the model provider is Anthropic."),
    "OPENAI_COMPATIBLE_REGION": ("Model provider location", "Only when an OpenAI-compatible provider is used."),
    "SPEECH_REGION": ("Speech provider location", "Only when server-side speech is turned on."),
    "FCM_REGION": ("Firebase Cloud Messaging location", "Only when phone push is turned on."),
    "MOYASAR_REGION": ("Moyasar location", "Only when Moyasar card payments are turned on; confirm with Moyasar."),
    "STRIPE_REGION": ("Stripe location", "Only when Stripe card payments are turned on."),
    "TWILIO_REGION": ("Twilio location", "The region of the Twilio accounts workspaces connect."),
    "META_REGION": ("WhatsApp (Meta) location", "The region of the WhatsApp Business accounts workspaces connect."),
    "BREVO_REGION": ("Brevo location", "The region of the Brevo accounts workspaces connect."),
}
TERM_MAX = 2000

DOC_KEYS: dict[str, tuple[str, ...]] = {
    "terms": (
        "CHANGE_NOTICE_METHOD", "CHANGE_NOTICE_PERIOD", "CONFIDENTIALITY_DETAILS", "CONTACT_EMAIL", "CURE_PERIOD", "DISPUTE_FORUM",
        "EFFECTIVE_DATE", "EXPORT_WINDOW", "FEATURE_REMOVAL_NOTICE", "FEES_AND_PAYMENT_TERMS", "GOVERNING_LAW", "LEGAL_ENTITY_NAME",
        "LIABILITY_CAP_AND_EXCLUSIONS", "LICENSING_AUTHORITY", "ORDER_FORM_OR_PLAN_PAGE", "REGISTERED_ADDRESS", "SECURITY_CONTACT_EMAIL",
        "SERVICE_URL", "SLA_REFERENCE", "SUPPORT_CONTACT", "TERMINATION_NOTICE", "TRADE_LICENCE_NUMBER", "WARRANTY_AND_DISCLAIMER_TEXT",
        "WEB_HOST", "WORKSPACE_EXPORT_COMMITMENT",
    ),
    "privacy": (
        "APPLICABLE_LAW_CONFIRMATION", "BILLING_RECORD_RETENTION", "BREACH_NOTICE_HOURS", "CALLS_RETENTION_DAYS", "CHANGE_NOTICE_METHOD",
        "CONVERSATION_RETENTION_DAYS", "CROSS_BORDER_TRANSFER_STATEMENT", "DELETION_COOLING_OFF_HOURS", "DPO_OR_PRIVACY_LEAD",
        "EFFECTIVE_DATE", "ERROR_REPORT_RETENTION_DAYS", "FILE_STORAGE_REGION", "HOSTING_PROVIDER_AND_REGION",
        "LEARNING_SIGNAL_RETENTION_DAYS", "LEGAL_BASIS_BILLING", "LEGAL_BASIS_CONTRACT", "LEGAL_BASIS_SECURITY", "LEGAL_BASIS_SUPPORT",
        "LEGAL_ENTITY_NAME", "LICENSING_AUTHORITY", "LOCAL_BACKUP_DAYS", "MINIMUM_AGE", "OFFSITE_BACKUP_DAYS", "PRIVACY_CONTACT_EMAIL",
        "REGISTERED_ADDRESS", "RESPONSE_DAYS", "SERVICE_URL", "SUPERVISORY_AUTHORITY", "TRADE_LICENCE_NUMBER", "WEB_HOST",
    ),
    "dpa": (
        "AUDIT_RIGHTS", "BREACH_NOTICE_HOURS", "DELETION_COOLING_OFF_HOURS", "DELETION_DAYS", "DSR_ASSISTANCE_DAYS", "EFFECTIVE_DATE",
        "EXPORT_WINDOW", "HOST_DISK_ENCRYPTION_CONFIRMATION", "LEGAL_ENTITY_NAME", "STAFF_ACCESS_CONTROLS", "SUBPROCESSOR_NOTICE_DAYS",
        "SUBPROCESSOR_NOTICE_METHOD", "TRANSFER_MECHANISM",
    ),
    "aup": ("ABUSE_CONTACT_EMAIL", "EFFECTIVE_DATE", "ENFORCEMENT_DETAILS", "LEGAL_ENTITY_NAME", "SECURITY_CONTACT_EMAIL", "WEB_HOST"),
    "subprocessors": (
        "ANTHROPIC_REGION", "B2_REGION", "BREVO_REGION", "DUCKDNS_LOCATION", "EFFECTIVE_DATE", "FCM_REGION", "HOSTING_REGION",
        "IN_USE_ANTHROPIC", "IN_USE_B2", "IN_USE_BREVO", "IN_USE_DUCKDNS", "IN_USE_FCM", "IN_USE_LETSENCRYPT", "IN_USE_MOYASAR",
        "IN_USE_OPENAI_COMPATIBLE", "IN_USE_ORACLE", "IN_USE_RENDER", "IN_USE_SMTP", "IN_USE_SPEECH", "IN_USE_STRIPE", "IN_USE_TWILIO",
        "IN_USE_WHATSAPP", "LEGAL_ENTITY_NAME", "META_REGION", "MOYASAR_REGION", "OPENAI_COMPATIBLE_PROVIDER", "OPENAI_COMPATIBLE_REGION",
        "RENDER_REGION", "SPEECH_PROVIDER", "SPEECH_REGION", "STRIPE_REGION", "SUBPROCESSOR_NOTICE_DAYS", "TWILIO_REGION",
    ),
}
ALL_KEYS = tuple(sorted({key for keys in DOC_KEYS.values() for key in keys}))

EMPTY_PROFILE: dict[str, Any] = {**{name: "" for name, _ in COMPANY_FIELDS}, "legal_terms": {}, "published_docs": []}


def _host(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def _yes(flag: bool) -> str:
    return "Yes" if flag else "No"


def _b2() -> bool:
    return "backblazeb2.com" in (settings.s3_endpoint or "")


def backup_status() -> dict[str, Any]:
    """The last backups as ``infra/oracle/backup.sh`` records them, or ``recorded: False``."""
    import json
    from pathlib import Path

    folder = (settings.backup_status_dir or "").strip()
    out: dict[str, Any] = {"recorded": False, "local": None, "offsite": None}
    if not folder:
        return out
    for kind in ("local", "offsite"):
        path = Path(folder) / f"last-{kind}.json"
        try:
            if path.is_file() and path.stat().st_size <= 4096:
                data = json.loads(path.read_text("utf-8"))
                if isinstance(data, dict):
                    out[kind] = {
                        "ok": bool(data.get("ok")),
                        "finished_at": str(data.get("finished_at") or "")[:40] or None,
                        "last_success_at": str(data.get("last_success_at") or "")[:40] or None,
                        "keep_days": int(data["keep_days"]) if str(data.get("keep_days") or "").isdigit() else None,
                    }
                    out["recorded"] = True
        except (OSError, ValueError):
            continue
    return out


def generated_values(provider_flags: dict[str, bool]) -> dict[str, str | None]:
    """Facts that come from this deployment's configuration; ``None`` means "ask the owner"."""
    web = (settings.web_app_url or "").rstrip("/")
    web_host = _host(web)
    model = settings.model_provider
    anthropic = model == "anthropic"
    openai = model == "openai-compatible"
    speech_url = settings.speech_stt_base_url or settings.speech_tts_base_url
    stripe, moyasar = bool(provider_flags.get("stripe")), bool(provider_flags.get("moyasar"))
    backups = backup_status()
    local_days = (backups.get("local") or {}).get("keep_days")
    b2 = _b2()
    region = settings.s3_region if b2 else None
    out: dict[str, str | None] = {
        "SERVICE_URL": web or None,
        "WEB_HOST": web_host or None,
        "ORDER_FORM_OR_PLAN_PAGE": f"the plan page at {web}/#pricing or the order form we agree with you" if web else None,
        "CONVERSATION_RETENTION_DAYS": str(settings.conversation_retention_days),
        "LEARNING_SIGNAL_RETENTION_DAYS": str(settings.learning_signal_retention_days),
        "CALLS_RETENTION_DAYS": str(settings.calls_retention_days),
        "DELETION_COOLING_OFF_HOURS": str(settings.tenant_deletion_cooling_off_hours),
        "LOCAL_BACKUP_DAYS": str(local_days) if local_days else None,
        "IN_USE_ANTHROPIC": _yes(anthropic),
        "ANTHROPIC_REGION": None if anthropic else NOT_USED,
        "IN_USE_OPENAI_COMPATIBLE": _yes(openai),
        "OPENAI_COMPATIBLE_PROVIDER": _host(settings.model_base_url) if openai else NOT_USED,
        "OPENAI_COMPATIBLE_REGION": None if openai else NOT_USED,
        "IN_USE_SPEECH": _yes(bool(speech_url)),
        "SPEECH_PROVIDER": _host(speech_url) if speech_url else NOT_USED,
        "SPEECH_REGION": None if speech_url else NOT_USED,
        "IN_USE_SMTP": _yes(bool(settings.smtp_host)),
        "IN_USE_FCM": _yes(settings.fcm_service_account_json is not None),
        "FCM_REGION": None if settings.fcm_service_account_json is not None else NOT_USED,
        "IN_USE_B2": _yes(b2),
        "B2_REGION": f"United States ({region})" if region and region.startswith("us-") else (region or (None if b2 else NOT_USED)),
        "FILE_STORAGE_REGION": f"United States ({region})" if region and region.startswith("us-") else region,
        "IN_USE_LETSENCRYPT": _yes(web.startswith("https://")),
        "IN_USE_DUCKDNS": _yes(web_host.endswith("duckdns.org")),
        "DUCKDNS_LOCATION": None if web_host.endswith("duckdns.org") else NOT_USED,
        "IN_USE_MOYASAR": _yes(moyasar),
        "MOYASAR_REGION": None if moyasar else NOT_USED,
        "IN_USE_STRIPE": _yes(stripe),
        "STRIPE_REGION": None if stripe else NOT_USED,
        "IN_USE_TWILIO": PER_WORKSPACE,
        "IN_USE_WHATSAPP": PER_WORKSPACE,
        "IN_USE_BREVO": PER_WORKSPACE,
    }
    return out


def company_values(profile: dict[str, Any]) -> dict[str, str | None]:
    """Placeholders filled from the company fields."""
    def v(name: str) -> str:
        return str(profile.get(name) or "").strip()

    country = v("registration_country").upper()
    authority = ", ".join(x for x in (v("registration_authority"), COUNTRY_NAMES.get(country, country)) if x)
    privacy = v("dpo_email") or v("contact_email")
    dpo = f"{v('dpo_name')} ({v('dpo_email')})" if v("dpo_name") and v("dpo_email") else v("dpo_name") or v("dpo_email")
    hosting = v("hosting_provider")
    location = v("server_location")
    return {
        "LEGAL_ENTITY_NAME": v("legal_name") or None,
        "REGISTERED_ADDRESS": v("address") or None,
        "TRADE_LICENCE_NUMBER": v("registration_number") or None,
        "LICENSING_AUTHORITY": authority or None,
        "CONTACT_EMAIL": v("contact_email") or None,
        "PRIVACY_CONTACT_EMAIL": privacy or None,
        "DPO_OR_PRIVACY_LEAD": dpo or None,
        "SECURITY_CONTACT_EMAIL": v("security_email") or v("contact_email") or None,
        "ABUSE_CONTACT_EMAIL": v("security_email") or v("contact_email") or None,
        "SUPPORT_CONTACT": v("support_contact") or v("contact_email") or None,
        "GOVERNING_LAW": v("governing_law") or None,
        "HOSTING_PROVIDER_AND_REGION": location or None,
        "HOSTING_REGION": (location if hosting in ("", "oracle", "other") else NOT_USED) if location else None,
        "IN_USE_ORACLE": _yes(hosting == "oracle") if hosting else None,
        "IN_USE_RENDER": _yes(hosting == "render") if hosting else None,
        "RENDER_REGION": (location if hosting == "render" else NOT_USED) if hosting else None,
    }


def legal_values(profile: dict[str, Any], provider_flags: dict[str, bool]) -> dict[str, str | None]:
    """Every placeholder key with its value, or ``None`` when it is not set yet."""
    terms = profile.get("legal_terms") or {}
    generated = generated_values(provider_flags)
    company = company_values(profile)
    out: dict[str, str | None] = {}
    for key in ALL_KEYS:
        value = generated.get(key) if key in generated else None
        if value is None and key in company:
            value = company[key]
        if value is None and key in TERM_KEYS:
            typed = str(terms.get(key) or "").strip()
            value = typed or None
        out[key] = value
    return out


def missing_by_doc(values: dict[str, str | None]) -> dict[str, list[str]]:
    return {doc: [key for key in keys if not values.get(key)] for doc, keys in DOC_KEYS.items()}


def clean_terms(raw: dict[str, Any]) -> dict[str, str]:
    """Keep only known term keys with non-empty text (trimmed, at most :data:`TERM_MAX` characters)."""
    out: dict[str, str] = {}
    for key, value in raw.items():
        if key not in TERM_KEYS:
            raise ValueError(f"{key} is not a legal fact ANUM knows; allowed keys are listed in the console.")
        text = str(value or "").strip()
        if len(text) > TERM_MAX:
            raise ValueError(f"{TERM_KEYS[key][0]} is longer than {TERM_MAX} characters.")
        if text:
            out[key] = text
    return out


# ----------------------------------------------------------------------------- the public read

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
CACHE_SECONDS = 60.0
PUBLIC_FIELDS = tuple(name for name, _ in COMPANY_FIELDS if name != "hosting_provider")


def clear_cache() -> None:
    _CACHE.clear()


def _issuer_rows(issuer: str) -> tuple[dict[str, Any] | None, dict[str, bool]]:
    if settings.repository_backend == "postgresql":
        columns = ", ".join(name for name, _ in COMPANY_FIELDS)
        found = reader_rows("anum_platform_reader", {"anum.billing_issuer": issuer},
                            f"select {columns}, legal_terms, published_docs from billing_company_profiles")  # nosec B608 - fixed columns
        flags = reader_rows("anum_platform_reader", {"anum.billing_issuer": issuer},
                            "select stripe_enabled, moyasar_enabled from billing_settings")
    else:
        found = [r for r in memory_db.all_rows("billing_company_profiles") if r["tenant_id"] == issuer]
        flags = [r for r in memory_db.all_rows("billing_settings") if r["tenant_id"] == issuer]
    flag = flags[0] if flags else {}
    return (found[0] if found else None), {"stripe": bool(flag.get("stripe_enabled")), "moyasar": bool(flag.get("moyasar_enabled"))}


def public_legal(issuer: str | None) -> dict[str, Any]:
    """What the public legal pages show; no tenant id, no one's name who changed it."""
    key = issuer or ""
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    profile, flags = _issuer_rows(issuer) if issuer else (None, {})
    merged = {**EMPTY_PROFILE, **(profile or {})}
    values = legal_values(merged, flags)
    published = [doc for doc in (merged.get("published_docs") or []) if doc in DOC_IDS]
    answer = {
        "configured": bool(issuer),
        "company": {name: merged.get(name) or "" for name in PUBLIC_FIELDS},
        "values": values,
        "published": published,
        "missing": missing_by_doc(values),
    }
    _CACHE[key] = (time.monotonic(), answer)
    return answer
