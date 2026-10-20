"""WhatsApp Business inbox through Meta's WhatsApp Cloud API (docs/whatsapp.md).

* **Account** (per workspace, owners only): phone number id, WhatsApp Business Account
  id, a permanent system-user access token and the Meta app secret (both encrypted at
  rest), the webhook verify token (stored as its SHA-256), the owner's own number for a
  test message, the auto-send template list and the triage switch.
* **Webhook** (``/api/v1/whatsapp/webhook``, public): Meta's ``GET`` verification
  challenge, and ``POST`` deliveries verified with ``X-Hub-Signature-256`` (HMAC-SHA256
  of the raw body with the app secret) before anything is stored. The owning workspace
  is found by phone number id (``whatsapp_store.route_phone_numbers``). Processing is
  idempotent (message ids are unique per workspace, delivery statuses only move
  forward), old deliveries are dropped (``ANUM_WHATSAPP_REPLAY_WINDOW_HOURS``) and each
  phone number has its own rate limit.
* **Inbox**: conversations, threads, a 24-hour customer-service window per conversation,
  rule triage on arrival, model triage and suggested replies through the workspace's
  budgeted model gateway with provenance labels (customer text is untrusted data).
* **Sending** is a governed tool call: ``send_whatsapp`` (free-form text, high risk,
  always approved by a person, only inside the 24-hour window) and
  ``send_whatsapp_template`` (medium risk; sent at once only for templates the owner put
  on the auto-send list and when no workspace or organization rule asks for approval).
  Both re-check the window and the template at execution and explain a refusal.
* **Agent tools**: ``list_whatsapp_conversations``, ``read_whatsapp`` and
  ``draft_whatsapp_reply`` (low risk) plus the two send tools, registered in the API's
  and the worker's tool registry.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from datetime import datetime, timedelta, timezone
from threading import RLock
from time import monotonic
from typing import Any, Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent_tools import ToolCall, ToolDefinition, ToolPolicyDecision, ToolPolicyOutcome, ToolRegistry, ToolResult
from .audit import AuditRecord
from .authorization import Permission
from .autonomy import AutonomyFacts, normalize_recipient
from .dependencies import _unit_of_work, has_permission, repository_context, require_permission, tenant_context
from .events import CanonicalEventName, create_event
from .hardening import InMemoryTokenBucket, client_key
from .model_budget import ModelBudgetExceededError
from .model_gateway import MockModelGateway, ModelGateway, build_default_model_gateway
from .repository import AnumRepository
from .runtime import AgentRuntime
from .suppression import SuppressionChannel, apply_inbound_keyword, keyword_of
from .schemas import (
    AgentRunStep,
    Approval,
    ApprovalStatus,
    RiskLevel,
    RunPhase,
    Task,
    TaskStatus,
    TenantContext,
    new_id,
    utc_now,
)
from .whatsapp_graph import (
    ERROR_EXPLANATIONS,
    MessageTemplate,
    WhatsAppGraphClient,
    WhatsAppGraphError,
    fill_template,
    valid_graph_id,
)
from .whatsapp_store import (
    AccountStatus,
    Category,
    Conversation,
    ConversationFilter,
    Direction,
    MediaInfo,
    Message,
    MessageStatus,
    PhoneNumberTakenError,
    STATUS_RANK,
    Suggestion,
    TemplateUse,
    WhatsAppAccount,
    WhatsAppStoreProtocol,
    open_whatsapp_store,
    preview,
    route_phone_numbers,
    verify_token_hash,
    verify_token_known,
)
from .whatsapp_triage import has_arabic, rule_category, suggest

logger = logging.getLogger("anum.whatsapp")

LIST_TOOL = "list_whatsapp_conversations"
READ_TOOL = "read_whatsapp"
DRAFT_TOOL = "draft_whatsapp_reply"
SEND_TOOL = "send_whatsapp"
TEMPLATE_TOOL = "send_whatsapp_template"
TOOLS = (LIST_TOOL, READ_TOOL, DRAFT_TOOL, SEND_TOOL, TEMPLATE_TOOL)

WEBHOOK_PATH = "/api/v1/whatsapp/webhook"
SYSTEM_ACTOR = "system:whatsapp"
MAX_TEXT = 4096
MAX_LISTED = 100
MAX_TEMPLATE_PARAMETERS = 20
MAX_AUTO_SEND = 50
MIN_VERIFY_TOKEN = 16
# A send request whose task never got an approval (the request died half way).
ORPHAN_AFTER = timedelta(minutes=5)
TEMPLATE_NAME = re.compile(r"^[a-z0-9_]{1,512}$")
LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(_[A-Za-z]{2,4})?$")
PHONE_DIGITS = re.compile(r"^[0-9]{6,20}$")
SIGNATURE = re.compile(r"^sha256=([0-9a-fA-F]{64})$")
CHALLENGE = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
INLINE_MEDIA = re.compile(r"^(image/(jpeg|png|webp|gif)|audio/[a-z0-9.+-]+|video/(mp4|3gpp))$")

CANNED_REPLIES: dict[Category, dict[str, str]] = {
    Category.ORDER_STATUS: {
        "en": "Thank you for your message. Your order [order number] is [status]. We will let you know as soon as it ships.",
        "ar": "شكراً لتواصلك. طلبك [رقم الطلب] حالته [الحالة]. سنبلغك فور شحنه.",
    },
    Category.PRICE_STOCK: {
        "en": "Thank you for asking. [Tyre size] costs [price] and is [in stock / available on date].",
        "ar": "شكراً لسؤالك. سعر [مقاس الإطار] هو [السعر] وهو [متوفر / يتوفر بتاريخ].",
    },
    Category.COMPLAINT: {
        "en": "We are sorry about this. Please send your order number and a photo so we can sort it out today.",
        "ar": "نعتذر عن ذلك. أرسل لنا رقم الطلب وصورة للمشكلة لنحلها اليوم.",
    },
    Category.SUPPLIER: {
        "en": "Thank you for reaching out. Please send your price list and terms to [email] and we will get back to you.",
        "ar": "شكراً لتواصلك. أرسل قائمة الأسعار والشروط إلى [البريد الإلكتروني] وسنعود إليك.",
    },
    Category.OTHER: {
        "en": "Thank you for your message. We will get back to you shortly.",
        "ar": "شكراً لرسالتك، سنعود إليك قريباً.",
    },
}


class WhatsAppSettings(BaseSettings):
    """``ANUM_WHATSAPP_*``: deployment-wide limits. Credentials are per workspace, never here."""

    graph_api_url: str = "https://graph.facebook.com"
    graph_api_version: str = Field(default="v23.0", pattern=r"^v[0-9]{1,3}\.[0-9]{1,2}$")
    webhook_max_body_bytes: int = Field(default=262_144, ge=1_024, le=1_048_576)
    media_max_bytes: int = Field(default=16 * 1024 * 1024, ge=1_024, le=100 * 1024 * 1024)
    # Inbound messages older than this are dropped (Meta retries for up to 7 days).
    replay_window_hours: int = Field(default=168, ge=1, le=720)
    webhook_rate_per_second: float = Field(default=20.0, gt=0, le=1000)
    webhook_burst: int = Field(default=200, ge=1, le=10_000)
    template_cache_seconds: float = Field(default=300.0, ge=0, le=86_400)
    # Model triage of new inbound messages (deployment kill switch; owners also have one).
    triage_on_arrival: bool = True
    # Message history retention for workspaces that did not choose one (and for history
    # left after a number is disconnected): 0 keeps it until deleted. The daily retention
    # job (python -m anum_api.voice_retention) deletes older messages (docs/whatsapp.md#retention).
    message_retention_days: int = Field(default=0, ge=0, le=3650)

    model_config = SettingsConfigDict(
        env_prefix="ANUM_WHATSAPP_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )


whatsapp_settings = WhatsAppSettings()
# Tests replace this with an httpx.MockTransport; production uses the network.
graph_transport: httpx.AsyncBaseTransport | None = None
_webhook_buckets = InMemoryTokenBucket(rate_per_second=whatsapp_settings.webhook_rate_per_second, burst=whatsapp_settings.webhook_burst)
# Bad signatures per caller address: enough for Meta's retries, not for guessing.
_bad_signature_buckets = InMemoryTokenBucket(rate_per_second=0.2, burst=20)
_template_cache: dict[tuple[str, str], tuple[float, list[MessageTemplate]]] = {}
_template_lock = RLock()


def reset_limits() -> None:
    """Tests: fresh rate-limit buckets and template cache."""
    global _webhook_buckets, _bad_signature_buckets
    _webhook_buckets = InMemoryTokenBucket(rate_per_second=whatsapp_settings.webhook_rate_per_second, burst=whatsapp_settings.webhook_burst)
    _bad_signature_buckets = InMemoryTokenBucket(rate_per_second=0.2, burst=20)
    with _template_lock:
        _template_cache.clear()


def setup_problems(cfg: WhatsAppSettings | None = None) -> list[str]:
    cfg = cfg or whatsapp_settings
    problems: list[str] = []
    try:
        url = httpx.URL(cfg.graph_api_url)
        if url.scheme != "https" or not url.host:
            problems.append("ANUM_WHATSAPP_GRAPH_API_URL")
    except (httpx.InvalidURL, TypeError):
        problems.append("ANUM_WHATSAPP_GRAPH_API_URL")
    return problems


def graph_host(cfg: WhatsAppSettings | None = None) -> str:
    """The integration target shown on send approvals (host only)."""
    try:
        return httpx.URL((cfg or whatsapp_settings).graph_api_url).host or "graph.facebook.com"
    except (httpx.InvalidURL, TypeError):
        return "graph.facebook.com"


def graph_client(account: WhatsAppAccount) -> WhatsAppGraphClient:
    if setup_problems() or account.access_token is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="WhatsApp is not usable here: " + ", ".join(setup_problems() or ["access token missing"]),
        )
    return WhatsAppGraphClient(
        access_token=account.access_token,
        api_url=whatsapp_settings.graph_api_url,
        api_version=whatsapp_settings.graph_api_version,
        transport=graph_transport,
    )


def system_context(tenant_id: str, workspace_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=SYSTEM_ACTOR, roles=[])


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _ago(value: datetime, now: datetime) -> str:
    hours = int((now - value).total_seconds() // 3600)
    if hours < 48:
        return f"{hours} hours ago"
    return f"{hours // 24} days ago"


def window_refusal(conversation: Conversation, now: datetime) -> str:
    last = conversation.last_inbound_at
    since = f"The customer last wrote {_ago(_aware(last), now)}. " if last else "The customer has not written yet. "
    return (
        since
        + "WhatsApp allows free-form replies only within 24 hours of the customer's last message. "
        "Send an approved message template instead (it opens a new window when the customer answers)."
    )


# -- views ----------------------------------------------------------------------------------


class AccountView(BaseModel):
    configured: bool
    can_manage: bool
    webhook_path: str = WEBHOOK_PATH
    deployment_problems: list[str] = Field(default_factory=list)
    phone_number_id: str | None = None
    business_account_id: str | None = None
    display_phone_number: str | None = None
    verified_name: str | None = None
    quality_rating: str | None = None
    access_token_hint: str | None = None
    app_secret_set: bool = False
    verify_token_set: bool = False
    owner_test_number: str | None = None
    auto_send_templates: list[str] = Field(default_factory=list)
    triage_enabled: bool = True
    # The workspace's choice (None: the deployment default), what applies, and that default.
    # 0 keeps messages until they are deleted.
    message_retention_days: int | None = None
    effective_message_retention_days: int = 0
    default_message_retention_days: int = 0
    status: AccountStatus | None = None
    last_error: str | None = None
    last_checked_at: datetime | None = None
    last_webhook_at: datetime | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None


class AccountUpdate(BaseModel):
    phone_number_id: str = Field(min_length=5, max_length=20)
    business_account_id: str = Field(min_length=5, max_length=20)
    access_token: SecretStr | None = None
    app_secret: SecretStr | None = None
    verify_token: SecretStr | None = None
    owner_test_number: str | None = Field(default=None, max_length=20)
    auto_send_templates: list[str] = Field(default_factory=list, max_length=MAX_AUTO_SEND)
    triage_enabled: bool = True
    # Omitted: keep the saved choice. null: follow the deployment default. 0: keep forever.
    message_retention_days: int | None = Field(default=None, ge=0, le=3650)

    @field_validator("phone_number_id", "business_account_id")
    @classmethod
    def _digits(cls, value: str) -> str:
        value = value.strip()
        if not valid_graph_id(value):
            raise ValueError("must be the numeric id shown in Meta's WhatsApp > API Setup")
        return value

    @field_validator("owner_test_number")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        digits = re.sub(r"[\s+()-]", "", value)
        if not PHONE_DIGITS.match(digits):
            raise ValueError("must be a phone number in international format, digits only (for example 9665XXXXXXXX)")
        return digits

    @field_validator("auto_send_templates")
    @classmethod
    def _templates(cls, value: list[str]) -> list[str]:
        names = sorted({item.strip() for item in value if item.strip()})
        bad = [name for name in names if not TEMPLATE_NAME.match(name)]
        if bad:
            raise ValueError(f"template names use lowercase letters, digits and underscores: {', '.join(bad[:3])}")
        return names


class TestSend(BaseModel):
    template: str = Field(default="hello_world", max_length=512)
    language: str = Field(default="en_US", max_length=20)


class TemplateView(BaseModel):
    name: str
    language: str
    status: str
    category: str
    body: str
    parameter_count: int
    header: str | None = None
    footer: str | None = None
    buttons: list[str] = Field(default_factory=list)
    auto_send: bool = False


class TemplateList(BaseModel):
    templates: list[TemplateView]
    fetched_at: datetime


class ConversationView(Conversation):
    window_open: bool
    window_closes_at: datetime | None
    awaiting_approval: int = 0


class MessageView(Message):
    approval: Approval | None = None


class MessagePage(BaseModel):
    conversation: ConversationView
    # Oldest first within the page; ``has_more`` means older messages exist (pass ``before``).
    messages: list[MessageView]
    has_more: bool


class DraftRequest(BaseModel):
    instructions: str | None = Field(default=None, max_length=500)


class TemplateSend(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    language: str = Field(min_length=2, max_length=20)
    parameters: list[str] = Field(default_factory=list, max_length=MAX_TEMPLATE_PARAMETERS)

    @field_validator("parameters")
    @classmethod
    def _parameters(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(item.split()) for item in value]
        if any(not item or len(item) > 1024 for item in cleaned):
            raise ValueError("every template parameter needs text (at most 1024 characters, no line breaks)")
        return cleaned


class SendRequest(BaseModel):
    text: str | None = Field(default=None, max_length=MAX_TEXT)
    reply_to_message_id: str | None = Field(default=None, max_length=80)
    template: TemplateSend | None = None


class SendResponse(BaseModel):
    outcome: Literal["sent", "awaiting_approval", "failed"]
    message: MessageView
    approval: Approval | None = None
    detail: str


# -- storage helpers ------------------------------------------------------------------------


def _account_view(account: WhatsAppAccount | None, context: TenantContext) -> AccountView:
    can_manage = has_permission(context, Permission.WHATSAPP_MANAGE)
    problems = setup_problems()
    default_days = whatsapp_settings.message_retention_days
    if account is None:
        return AccountView(
            configured=False, can_manage=can_manage, deployment_problems=problems,
            effective_message_retention_days=default_days, default_message_retention_days=default_days,
        )
    return AccountView(
        configured=True,
        can_manage=can_manage,
        deployment_problems=problems,
        phone_number_id=account.phone_number_id,
        business_account_id=account.business_account_id,
        display_phone_number=account.display_phone_number,
        verified_name=account.verified_name,
        quality_rating=account.quality_rating,
        access_token_hint=account.access_token_hint,
        app_secret_set=True,
        verify_token_set=True,
        owner_test_number=account.owner_test_number,
        auto_send_templates=account.auto_send_templates,
        triage_enabled=account.triage_enabled,
        message_retention_days=account.message_retention_days,
        effective_message_retention_days=(
            default_days if account.message_retention_days is None else account.message_retention_days
        ),
        default_message_retention_days=default_days,
        status=account.status,
        last_error=account.last_error,
        last_checked_at=account.last_checked_at,
        last_webhook_at=account.last_webhook_at,
        updated_at=account.updated_at,
        updated_by=account.updated_by,
    )


def _require_account(store: WhatsAppStoreProtocol, *, include_secrets: bool = False) -> WhatsAppAccount:
    account = store.get_account(include_secrets=include_secrets)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="WhatsApp is not connected for this workspace. An owner connects it under WhatsApp > Setup.",
        )
    return account


def _conversation_view(conversation: Conversation, store: WhatsAppStoreProtocol, now: datetime) -> ConversationView:
    return ConversationView(
        **conversation.model_dump(),
        window_open=conversation.is_window_open(now),
        window_closes_at=conversation.service_window_closes_at(),
        awaiting_approval=len(store.awaiting(conversation.id)),
    )


def _audit(
    repository: AnumRepository,
    context: TenantContext,
    action: str,
    target: str,
    outcome: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=target,
            outcome=outcome,
            correlation_id=target,
            created_at=utc_now(),
            metadata=metadata or {},
        )
    )


def _audit_detached(context: TenantContext, action: str, target: str, outcome: str, metadata: dict[str, Any]) -> None:
    """Audit from a tool or the webhook, outside a request's repository session."""
    with _unit_of_work(context, require_membership=False) as repository:
        _audit(repository, context, action, target, outcome, metadata)


async def templates_for(account: WhatsAppAccount, *, refresh: bool = False) -> list[MessageTemplate]:
    key = (account.tenant_id, account.workspace_id)
    with _template_lock:
        cached = _template_cache.get(key)
    if not refresh and cached is not None and monotonic() - cached[0] < whatsapp_settings.template_cache_seconds:
        return cached[1]
    found = await graph_client(account).templates(account.business_account_id)
    with _template_lock:
        _template_cache[key] = (monotonic(), found)
    return found


def find_template(templates: list[MessageTemplate], name: str, language: str) -> MessageTemplate | None:
    return next((item for item in templates if item.name == name and item.language == language), None)


def template_problem(template: MessageTemplate | None, name: str, language: str, parameters: list[str]) -> str | None:
    if template is None:
        return f"The template {name} ({language}) does not exist in this WhatsApp account. Refresh the template list."
    if template.status != "APPROVED":
        return f"The template {name} ({language}) is {template.status.lower()}; only approved templates can be sent."
    if template.parameter_count != len(parameters):
        return (
            f"The template {name} needs {template.parameter_count} parameter(s); "
            f"{len(parameters)} given."
        )
    return None


# -- inbound processing (webhook) -----------------------------------------------------------


def _timestamp(raw: Any, now: datetime) -> datetime:
    try:
        value = datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return now
    return min(value, now)


def _inbound_content(message: dict[str, Any]) -> tuple[str, str, MediaInfo | None, str | None]:
    """(kind, body, media, reply_to) of one inbound webhook message."""
    kind = str(message.get("type") or "unsupported")[:20]
    reply_to = (message.get("context") or {}).get("id") if isinstance(message.get("context"), dict) else None
    part = message.get(kind) if isinstance(message.get(kind), dict) else {}
    body = ""
    media: MediaInfo | None = None
    if kind == "text":
        body = str(part.get("body") or "")
    elif kind in {"image", "video", "audio", "document", "sticker"}:
        media_id = part.get("id")
        if isinstance(media_id, str) and valid_graph_id(media_id):
            media = MediaInfo(
                id=media_id,
                mime_type=str(part.get("mime_type") or "")[:100] or None,
                sha256=str(part.get("sha256") or "")[:100] or None,
                filename=str(part.get("filename") or "")[:200] or None,
                caption=str(part.get("caption") or "")[:1024] or None,
                voice=bool(part.get("voice")),
            )
        body = str(part.get("caption") or "")
    elif kind == "location":
        name = " ".join(str(part.get(key) or "") for key in ("name", "address")).strip()
        body = f"{name} ({part.get('latitude')}, {part.get('longitude')})".strip()
    elif kind == "contacts":
        cards = message.get("contacts") if isinstance(message.get("contacts"), list) else []
        body = ", ".join(
            str((card.get("name") or {}).get("formatted_name") or "") for card in cards if isinstance(card, dict)
        )
    elif kind == "interactive":
        reply = part.get("button_reply") or part.get("list_reply") or {}
        body = str(reply.get("title") or "") if isinstance(reply, dict) else ""
    elif kind == "button":
        body = str(part.get("text") or "")
    elif kind == "reaction":
        body = str(part.get("emoji") or "")
        reply_to = part.get("message_id") if isinstance(part.get("message_id"), str) else reply_to
    elif kind not in {"unsupported", "unknown", "system", "order"}:
        kind = "unsupported"
    return kind, body[:MAX_TEXT], media, reply_to if isinstance(reply_to, str) else None


class WebhookCounts(BaseModel):
    messages: int = 0
    duplicates: int = 0
    stale: int = 0
    statuses: int = 0
    echoes: int = 0
    conversations: list[str] = Field(default_factory=list)
    # New inbound texts for assistant auto-routing (never logged or returned).
    arrived: list[tuple[str, str, str]] = Field(default_factory=list, exclude=True)
    # New inbound messages, announced as ``whatsapp.message_received`` after the store commits.
    received: list[dict[str, Any]] = Field(default_factory=list)
    # Opt-out / opt-in keywords (STOP, إيقاف, START, ...): (wa_id, keyword, message id),
    # applied to the contact suppression list right after the store commits.
    keywords: list[tuple[str, str, str]] = Field(default_factory=list, exclude=True)


def _record_message(
    store: WhatsAppStoreProtocol,
    *,
    wa_id: str,
    profile_name: str | None,
    message: Message,
    now: datetime,
    counts: WebhookCounts,
) -> None:
    contact = store.upsert_contact(wa_id, profile_name, now, new_id("wac"))
    conversation = store.conversation_for(wa_id)
    if conversation is None:
        conversation = Conversation(
            id=new_id("wacv"),
            tenant_id=contact.tenant_id,
            workspace_id=contact.workspace_id,
            contact_id=contact.id,
            wa_id=wa_id,
            contact_name=contact.profile_name,
            last_message_at=message.timestamp,
            last_direction=message.direction,
            created_at=now,
            updated_at=now,
        )
        store.add_conversation(conversation)
    message.conversation_id = conversation.id
    if not store.add_message(message):
        counts.duplicates += 1
        return
    if contact.profile_name:
        conversation.contact_name = contact.profile_name
    if message.timestamp >= conversation.last_message_at:
        conversation.last_message_at = message.timestamp
        conversation.last_message_preview = preview(message)
        conversation.last_direction = message.direction
    if message.direction == Direction.INBOUND:
        counts.messages += 1
        counts.received.append(
            {
                "conversation_id": conversation.id,
                "message_id": message.id,
                "from": wa_id,
                "contact_name": contact.profile_name,
                "kind": str(getattr(message.kind, "value", message.kind)),
                "category": str(getattr(conversation.category, "value", conversation.category) or ""),
            }
        )
        conversation.unread_count += 1
        if conversation.last_inbound_at is None or message.timestamp > conversation.last_inbound_at:
            conversation.last_inbound_at = message.timestamp
        if conversation.category_source != "model" and (message.body or message.media):
            conversation.category = rule_category(message.body or (message.media.caption if message.media else "") or "")
            conversation.category_source = "rules"
        conversation.needs_triage = True
        if conversation.id not in counts.conversations:
            counts.conversations.append(conversation.id)
        text = message.body or (message.media.caption if message.media else "") or ""
        keyword = keyword_of(message.body or "") if message.kind == "text" else None
        if keyword is not None:
            counts.keywords.append((wa_id, keyword, message.id))
        if text.strip():
            sender = f"{contact.profile_name} (+{wa_id})" if contact.profile_name else f"+{wa_id}"
            counts.arrived.append((sender, text, conversation.id))
    else:
        counts.echoes += 1
    conversation.updated_at = now
    store.save_conversation(conversation)


def _apply_status(store: WhatsAppStoreProtocol, item: dict[str, Any], now: datetime, counts: WebhookCounts) -> None:
    wamid, raw = item.get("id"), str(item.get("status") or "")
    if not isinstance(wamid, str):
        return
    message = store.message_by_wamid(wamid)
    if message is None or message.direction != Direction.OUTBOUND:
        return  # sent from elsewhere (another app on the number) or not ours
    try:
        new = MessageStatus(raw)
    except ValueError:
        return
    if new == MessageStatus.FAILED:
        if message.status == MessageStatus.FAILED:
            return
        errors = item.get("errors") if isinstance(item.get("errors"), list) else []
        first = errors[0] if errors and isinstance(errors[0], dict) else {}
        code = first.get("code") if isinstance(first.get("code"), int) else None
        message.status = MessageStatus.FAILED
        message.error = ERROR_EXPLANATIONS.get(code or -1) or str(first.get("title") or first.get("message") or "WhatsApp could not deliver the message.")[:300]
    elif new in STATUS_RANK and message.status in STATUS_RANK and STATUS_RANK[new] > STATUS_RANK[message.status]:
        message.status = new
    else:
        return
    message.updated_at = now
    store.save_message(message)
    counts.statuses += 1


def process_value(
    store: WhatsAppStoreProtocol, context: TenantContext, field: str, value: dict[str, Any], now: datetime, counts: WebhookCounts
) -> str | None:
    """Store one ``changes[].value`` for this workspace. Returns a Meta-reported error, if any."""
    oldest = now - timedelta(hours=whatsapp_settings.replay_window_hours)
    names = {
        str(contact.get("wa_id")): (contact.get("profile") or {}).get("name")
        for contact in value.get("contacts") or []
        if isinstance(contact, dict)
    }
    if field == "messages":
        for raw in value.get("messages") or []:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                continue
            wa_id = str(raw.get("from") or "")
            if not PHONE_DIGITS.match(wa_id):
                continue
            timestamp = _timestamp(raw.get("timestamp"), now)
            if timestamp < oldest:
                counts.stale += 1
                continue
            kind, body, media, reply_to = _inbound_content(raw)
            name = names.get(wa_id)
            _record_message(
                store,
                wa_id=wa_id,
                profile_name=str(name)[:200] if isinstance(name, str) and name.strip() else None,
                message=Message(
                    id=new_id("wam"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                    conversation_id="", wamid=raw["id"][:200], direction=Direction.INBOUND, kind=kind, body=body,
                    media=media, status=MessageStatus.RECEIVED, reply_to=reply_to, timestamp=timestamp,
                    created_at=now, updated_at=now,
                ),
                now=now,
                counts=counts,
            )
        for item in value.get("statuses") or []:
            if isinstance(item, dict):
                _apply_status(store, item, now, counts)
    elif field == "smb_message_echoes":
        # Coexistence: messages the owner sent from the WhatsApp Business app on the phone.
        for raw in value.get("message_echoes") or []:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                continue
            wa_id = str(raw.get("to") or "")
            timestamp = _timestamp(raw.get("timestamp"), now)
            if not PHONE_DIGITS.match(wa_id) or timestamp < oldest:
                continue
            kind, body, media, reply_to = _inbound_content(raw)
            _record_message(
                store,
                wa_id=wa_id,
                profile_name=None,
                message=Message(
                    id=new_id("wam"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                    conversation_id="", wamid=raw["id"][:200], direction=Direction.OUTBOUND, kind=kind, body=body,
                    media=media, status=MessageStatus.SENT, reply_to=reply_to, sent_by="whatsapp_business_app",
                    timestamp=timestamp, created_at=now, updated_at=now,
                ),
                now=now,
                counts=counts,
            )
    errors = value.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("title") or errors[0].get("message") or "Meta reported an error.")[:300]
    return None


def _note_business_replies(context: TenantContext, senders: list[str]) -> int:
    """A WhatsApp message from a Business lead ANUM reached out to stops that lead's follow-ups
    (docs/business.md#outreach). Never fails the webhook."""
    matched = 0
    try:
        from .business.outreach import note_inbound

        for wa_id in senders:
            matched += note_inbound(context, "whatsapp", "+" + wa_id.lstrip("+"), source="whatsapp")
    except Exception as exc:
        logger.warning("business_reply_hook_failed error_class=%s", type(exc).__name__)
    return matched


def _route_to_assistants(context: TenantContext, arrived: list[tuple[str, str, str]]) -> int:
    """Hand new inbound messages to department assistants that opted into WhatsApp.

    Best effort, after the webhook stored them: never raises (docs/team-work.md#inbound-routing).
    """
    from .assistants import ArrivedMessage, auto_route_inbound

    return auto_route_inbound(
        context.tenant_id, context.workspace_id, "whatsapp",
        [ArrivedMessage(sender=sender, body=body, external_ref=f"whatsapp:{conversation_id}") for sender, body, conversation_id in arrived],
    )


# -- triage ---------------------------------------------------------------------------------

_fallback_gateway: ModelGateway | None = None


def default_gateway() -> ModelGateway:
    global _fallback_gateway
    if _fallback_gateway is None:
        _fallback_gateway = build_default_model_gateway()
    return _fallback_gateway


def workspace_gateway(context: TenantContext) -> ModelGateway:
    from .onboarding import budgeted_model_gateway

    return budgeted_model_gateway(context, default_gateway())


def rules_suggestion(messages: list[Message], actor: str, now: datetime) -> Suggestion:
    last = next((m for m in reversed(messages) if m.direction == Direction.INBOUND), None)
    text = (last.body if last else "") or ""
    category = rule_category(text)
    language = "ar" if has_arabic(text) else "en"
    return Suggestion(
        text=CANNED_REPLIES[category][language],
        category=category,
        summary=" ".join(text.split())[:200] or "No text in the last message.",
        language=language,
        source="rules",
        generated_at=now,
        generated_by=actor,
        untrusted_messages=0,
    )


async def make_suggestion(
    context: TenantContext, conversation: Conversation, messages: list[Message], *, instructions: str | None = None
) -> Suggestion:
    """A suggested reply: the workspace's model (budgeted) or, without a real model, the rules."""
    now = utc_now()
    gateway = workspace_gateway(context)
    if getattr(gateway, "provider", None) == "mock":
        return rules_suggestion(messages, context.user_id, now)
    result, provider, model, customer = await suggest(gateway, messages, instructions=instructions)
    if not result.reply:
        fallback = rules_suggestion(messages, context.user_id, now)
        return fallback.model_copy(update={"category": result.category, "summary": result.summary or fallback.summary})
    return Suggestion(
        text=result.reply,
        category=result.category,
        summary=result.summary,
        language=result.language,
        source="model",
        provider=provider,
        model=model,
        generated_at=now,
        generated_by=context.user_id,
        untrusted_messages=customer,
    )


def _apply_suggestion(conversation: Conversation, suggestion: Suggestion, now: datetime) -> None:
    conversation.suggestion = suggestion
    conversation.summary = suggestion.summary
    conversation.category = suggestion.category
    conversation.category_source = suggestion.source
    conversation.needs_triage = False
    conversation.triaged_at = now
    conversation.updated_at = now


async def triage_conversations(context: TenantContext, conversation_ids: list[str]) -> int:
    """Model triage after a webhook delivery (a background task). Returns how many were triaged.

    Skipped without a real model (the rule category stays), when the owner or the
    deployment turned it off, and when the budget is used up. Never raises.
    """
    if not whatsapp_settings.triage_on_arrival or not conversation_ids:
        return 0
    done = 0
    try:
        with open_whatsapp_store(context) as store:
            account = store.get_account()
            if account is None or not account.triage_enabled:
                return 0
        if getattr(workspace_gateway(context), "provider", None) == "mock":
            return 0
        for conversation_id in conversation_ids[:20]:
            with open_whatsapp_store(context) as store:
                conversation = store.get_conversation(conversation_id)
                if conversation is None or not conversation.needs_triage:
                    continue
                messages = list(reversed(store.list_messages(conversation_id, limit=12, before=None)))
            suggestion = await make_suggestion(context, conversation, messages)
            with open_whatsapp_store(context) as store:
                current = store.get_conversation(conversation_id)
                if current is None:
                    continue
                _apply_suggestion(current, suggestion, utc_now())
                store.save_conversation(current)
            done += 1
    except ModelBudgetExceededError:
        logger.info("whatsapp_triage_budget_exhausted", extra={"anum_whatsapp": {"event": "triage_budget"}})
    except Exception as exc:  # a background task: log and keep the rule category
        logger.warning(
            "whatsapp_triage_failed error_class=%s",
            type(exc).__name__,
            extra={"anum_whatsapp": {"event": "triage_failed", "error_class": type(exc).__name__}},
        )
    return done


# -- tools ----------------------------------------------------------------------------------


def _bounded_int(value: Any, default: int, maximum: int) -> int:
    text = str(value if value is not None else default)
    return max(1, min(int(text) if text.isdigit() else default, maximum))


def _failed(summary: str, **output: Any) -> ToolResult:
    return ToolResult(status="failed", summary=summary, output=output, audit_metadata={"outcome": "refused"})


def _conversation_summary(conversation: Conversation, now: datetime) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "contact": conversation.contact_name or conversation.wa_id,
        "wa_id": conversation.wa_id,
        "last_message_at": conversation.last_message_at.isoformat(),
        "last_message": conversation.last_message_preview,
        "last_from": "customer" if conversation.last_direction == Direction.INBOUND else "business",
        "unread": conversation.unread_count,
        "category": conversation.category.value if conversation.category else None,
        "window_open": conversation.is_window_open(now),
    }


async def list_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    args = call.arguments
    raw_filter = str(args.get("filter") or "all")
    chosen: ConversationFilter = raw_filter if raw_filter in {"all", "unread", "needs_reply", "awaiting_approval"} else "all"  # type: ignore[assignment]
    limit = _bounded_int(args.get("limit"), 20, 50)
    query = str(args.get("query") or "").strip()[:200] or None
    now = utc_now()
    with open_whatsapp_store(context) as store:
        if store.get_account() is None:
            return _failed("WhatsApp is not connected for this workspace.")
        items = store.list_conversations(limit=limit, before=None, query=query, filter=chosen)
    rows = [_conversation_summary(item, now) for item in items]
    unread = sum(1 for item in items if item.unread_count)
    waiting = sum(1 for item in items if item.last_direction == Direction.INBOUND)
    return ToolResult(
        status="succeeded",
        summary=f"{len(rows)} WhatsApp conversation(s): {unread} unread, {waiting} waiting for a reply.",
        output={"conversations": rows},
        audit_metadata={"result_count": len(rows), "filter": chosen},
    )


async def read_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    conversation_id = str(call.arguments.get("conversation_id") or "")
    limit = _bounded_int(call.arguments.get("limit"), 20, 100)
    now = utc_now()
    with open_whatsapp_store(context) as store:
        conversation = store.get_conversation(conversation_id)
        if conversation is None:
            return _failed("That WhatsApp conversation was not found.")
        messages = list(reversed(store.list_messages(conversation_id, limit=limit, before=None)))
    rows = [
        {
            "id": message.id,
            "from": "customer" if message.direction == Direction.INBOUND else "business",
            "at": message.timestamp.isoformat(),
            "kind": message.kind,
            "text": message.body,
            "status": message.status.value,
        }
        for message in messages
    ]
    return ToolResult(
        status="succeeded",
        summary=f"{len(rows)} message(s) with {conversation.contact_name or conversation.wa_id}; "
        f"the 24-hour reply window is {'open' if conversation.is_window_open(now) else 'closed'}.",
        output={"conversation": _conversation_summary(conversation, now), "messages": rows},
        audit_metadata={"result_count": len(rows)},
    )


async def draft_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    conversation_id = str(call.arguments.get("conversation_id") or "")
    instructions = str(call.arguments.get("instructions") or "").strip()[:500] or None
    with open_whatsapp_store(context) as store:
        conversation = store.get_conversation(conversation_id)
        if conversation is None:
            return _failed("That WhatsApp conversation was not found.")
        messages = list(reversed(store.list_messages(conversation_id, limit=12, before=None)))
    suggestion = await make_suggestion(context, conversation, messages, instructions=instructions)
    with open_whatsapp_store(context) as store:
        current = store.get_conversation(conversation_id)
        if current is not None:
            _apply_suggestion(current, suggestion, utc_now())
            store.save_conversation(current)
    return ToolResult(
        status="succeeded",
        summary=f"Drafted a reply ({suggestion.category.value}); nothing was sent.",
        output={"draft": suggestion.text, "category": suggestion.category.value, "summary": suggestion.summary, "source": suggestion.source},
        audit_metadata={"source": suggestion.source, "untrusted_messages": suggestion.untrusted_messages},
    )


def _outbound(context: TenantContext, conversation: Conversation, *, body: str, kind: str, status_: MessageStatus,
              template: TemplateUse | None = None, reply_to: str | None = None) -> Message:
    now = utc_now()
    return Message(
        id=new_id("wam"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        direction=Direction.OUTBOUND,
        kind=kind,
        body=body,
        template=template,
        status=status_,
        reply_to=reply_to,
        sent_by=context.user_id,
        timestamp=now,
        created_at=now,
        updated_at=now,
    )


def _settle_message(context: TenantContext, message_id: str | None, conversation: Conversation, *, wamid: str | None,
                    error: str | None, fallback: Message) -> Message:
    """Record the send's outcome on the outbound row (created by the send request, or now)."""
    now = utc_now()
    with open_whatsapp_store(context) as store:
        message = store.get_message(message_id) if message_id else None
        if message is None:
            message = fallback
            store.add_message(message)
        if wamid:
            message.wamid = wamid
            message.status = MessageStatus.SENT
            message.error = None
            message.timestamp = now
        else:
            message.status = MessageStatus.NOT_SENT if error and "24 hours" in error else MessageStatus.FAILED
            message.error = error
        message.updated_at = now
        store.save_message(message)
        if wamid:
            current = store.get_conversation(conversation.id)
            if current is not None:
                current.last_message_at = max(current.last_message_at, now)
                current.last_message_preview = preview(message)
                current.last_direction = Direction.OUTBOUND
                current.unread_count = 0
                current.updated_at = now
                store.save_conversation(current)
    return message


async def send_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    """Send an approved free-form reply, only inside the 24-hour customer-service window."""
    args = call.arguments
    conversation_id = str(args.get("conversation_id") or "")
    text = str(args.get("text") or "").strip()
    message_id = str(args.get("message_id") or "") or None
    reply_to = str(args.get("reply_to") or "") or None
    if not text or len(text) > MAX_TEXT:
        return _failed(f"Not sent: the reply needs text (at most {MAX_TEXT} characters).")
    with open_whatsapp_store(context) as store:
        account = store.get_account(include_secrets=True)
        conversation = store.get_conversation(conversation_id)
    if account is None or setup_problems():
        return _failed("Not sent: WhatsApp is not connected for this workspace.")
    if conversation is None or args.get("to") != conversation.wa_id:
        return _failed("Not sent: the approved recipient does not match the conversation.")
    now = utc_now()
    fallback = _outbound(context, conversation, body=text, kind="text", status_=MessageStatus.SENDING, reply_to=reply_to)
    if not conversation.is_window_open(now):
        reason = "Not sent: " + window_refusal(conversation, now)
        _settle_message(context, message_id, conversation, wamid=None, error=reason, fallback=fallback)
        return _failed(reason, conversation_id=conversation.id)
    try:
        wamid = await graph_client(account).send_text(account.phone_number_id, conversation.wa_id, text, reply_to=reply_to)
    except WhatsAppGraphError as exc:
        reason = "Not sent: " + (window_refusal(conversation, now) if exc.outside_window else exc.message)
        _settle_message(context, message_id, conversation, wamid=None, error=reason, fallback=fallback)
        return _failed(reason, conversation_id=conversation.id, meta_code=exc.code)
    message = _settle_message(context, message_id, conversation, wamid=wamid, error=None, fallback=fallback)
    return ToolResult(
        status="succeeded",
        summary=f"Sent the WhatsApp reply to {conversation.contact_name or conversation.wa_id}.",
        output={"conversation_id": conversation.id, "message_id": message.id, "wamid": wamid},
        audit_metadata={"outcome": "sent", "kind": "text", "characters": len(text)},
    )


async def template_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    """Send an approved message template (allowed outside the 24-hour window)."""
    args = call.arguments
    conversation_id = str(args.get("conversation_id") or "")
    name, language = str(args.get("template") or ""), str(args.get("language") or "")
    raw_parameters = args.get("parameters") or []
    parameters = [str(item) for item in raw_parameters] if isinstance(raw_parameters, list) else []
    message_id = str(args.get("message_id") or "") or None
    with open_whatsapp_store(context) as store:
        account = store.get_account(include_secrets=True)
        conversation = store.get_conversation(conversation_id)
    if account is None or setup_problems():
        return _failed("Not sent: WhatsApp is not connected for this workspace.")
    if conversation is None or args.get("to") != conversation.wa_id:
        return _failed("Not sent: the approved recipient does not match the conversation.")
    use = TemplateUse(name=name, language=language, parameters=parameters)
    fallback = _outbound(context, conversation, body="", kind="template", status_=MessageStatus.SENDING, template=use)
    try:
        template = find_template(await templates_for(account), name, language)
        problem = template_problem(template, name, language, parameters)
        if problem:
            _settle_message(context, message_id, conversation, wamid=None, error="Not sent: " + problem, fallback=fallback)
            return _failed("Not sent: " + problem)
        wamid = await graph_client(account).send_template(account.phone_number_id, conversation.wa_id, name, language, parameters)
    except WhatsAppGraphError as exc:
        _settle_message(context, message_id, conversation, wamid=None, error="Not sent: " + exc.message, fallback=fallback)
        return _failed("Not sent: " + exc.message, meta_code=exc.code)
    fallback.body = fill_template(template.body if template else "", parameters)
    with open_whatsapp_store(context) as store:
        row = store.get_message(message_id) if message_id else None
        if row is not None and not row.body:
            row.body = fallback.body
            store.save_message(row)
    message = _settle_message(context, message_id, conversation, wamid=wamid, error=None, fallback=fallback)
    return ToolResult(
        status="succeeded",
        summary=f"Sent the WhatsApp template {name} to {conversation.contact_name or conversation.wa_id}.",
        output={"conversation_id": conversation.id, "message_id": message.id, "wamid": wamid},
        audit_metadata={"outcome": "sent", "kind": "template", "template": name, "parameters": len(parameters)},
    )


AUTO_SEND_SOURCE = (
    "WhatsApp's approved templates are the owner's auto-send list (WhatsApp > Templates, Send without asking)."
)


def autonomy_facts(call: ToolCall, context: TenantContext) -> AutonomyFacts:
    """What the inbox knows about a send, for the autonomy policy (docs/autonomy.md).

    * The customer is a **known recipient** when the call answers the conversation it
      names and that customer has written to the business in it (an inbound message, so
      ``last_inbound_at`` is set). A number that never wrote (or a conversation holding
      only echoes from the phone app) is first contact and asks.
    * The whatsapp category's **approved templates** are the owner's auto-send list: the
      one place they are kept. The Autonomy page's own template list does not apply here.
    """
    with open_whatsapp_store(context) as store:
        account = store.get_account()
        conversation = store.get_conversation(str(call.arguments.get("conversation_id") or ""))
    known: frozenset[str] = frozenset()
    to = call.arguments.get("to")
    if (
        conversation is not None
        and isinstance(to, str)
        and to == conversation.wa_id
        and conversation.last_inbound_at is not None
    ):
        known = frozenset({normalize_recipient(to)})
    templates = tuple(account.auto_send_templates) if account is not None else ()
    return AutonomyFacts(known_recipients=known, approved_templates=templates, templates_source=AUTO_SEND_SOURCE)


def tool_definitions(cfg: WhatsAppSettings | None = None) -> list[ToolDefinition]:
    """The five WhatsApp tools, each with its autonomy category and kind of action.

    Both sends go into an existing conversation, so they are replies: the free-form one
    never carries a template and always asks (high risk, and autonomy never lets free text
    go alone); the template one may act and notify under docs/autonomy.md.
    """
    people = frozenset({"owner", "member"})
    target = graph_host(cfg)
    wa = "whatsapp"
    return [
        ToolDefinition(name=LIST_TOOL, description="List the workspace's WhatsApp conversations (read-only).",
                       risk_level=RiskLevel.LOW, required_roles=people, idempotent=True, category=wa, action_kind="read"),
        ToolDefinition(name=READ_TOOL, description="Read one WhatsApp conversation's recent messages (read-only).",
                       risk_level=RiskLevel.LOW, required_roles=people, idempotent=True, category=wa, action_kind="read"),
        ToolDefinition(name=DRAFT_TOOL, description="Draft a reply to a WhatsApp conversation; sends nothing.",
                       risk_level=RiskLevel.LOW, required_roles=people, timeout_seconds=60, category=wa, action_kind="draft"),
        ToolDefinition(name=SEND_TOOL, description="Send a free-form WhatsApp reply inside the 24-hour customer-service window.",
                       risk_level=RiskLevel.HIGH, required_roles=people, target=target, category=wa, action_kind="reply"),
        ToolDefinition(name=TEMPLATE_TOOL, description="Send an approved WhatsApp message template.",
                       risk_level=RiskLevel.MEDIUM, required_roles=people, target=target, category=wa, action_kind="reply"),
    ]


HANDLERS = {LIST_TOOL: list_tool, READ_TOOL: read_tool, DRAFT_TOOL: draft_tool, SEND_TOOL: send_tool, TEMPLATE_TOOL: template_tool}
# Calls whose recipients and templates the inbox can vouch for.
FACTS = {SEND_TOOL: autonomy_facts, TEMPLATE_TOOL: autonomy_facts}


def register_whatsapp_tools(registry: ToolRegistry, cfg: WhatsAppSettings | None = None) -> ToolRegistry:
    """Add the five WhatsApp tools to a runtime's registry (the API's and the worker's)."""
    for definition in tool_definitions(cfg):
        if definition.name not in registry.names:
            registry.register(definition, HANDLERS[definition.name], autonomy_facts=FACTS.get(definition.name))
    return registry


# -- HTTP: account --------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/whatsapp", tags=["whatsapp"])


async def _check(account: WhatsAppAccount, now: datetime) -> WhatsAppAccount:
    """Ask Meta for the phone number's profile and record the result on the account."""
    try:
        profile = await graph_client(account).phone_number(account.phone_number_id)
    except WhatsAppGraphError as exc:
        account.status = AccountStatus.ERROR
        account.last_error = exc.message
    else:
        account.status = AccountStatus.CONNECTED
        account.last_error = None
        account.display_phone_number = profile.display_phone_number
        account.verified_name = profile.verified_name
        account.quality_rating = profile.quality_rating
    account.last_checked_at = now
    return account


def _save_checked(context: TenantContext, account: WhatsAppAccount) -> WhatsAppAccount:
    with open_whatsapp_store(context) as store:
        current = store.get_account()
        if current is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The WhatsApp account was disconnected meanwhile.")
        for name in ("status", "last_error", "last_checked_at", "display_phone_number", "verified_name", "quality_rating"):
            setattr(current, name, getattr(account, name))
        return store.save_account(current, access_token=None, app_secret=None)


@router.get("/account", response_model=AccountView)
async def get_account(context: TenantContext = Depends(tenant_context)) -> AccountView:
    require_permission(context, Permission.WHATSAPP_READ)
    with open_whatsapp_store(context) as store:
        return _account_view(store.get_account(), context)


@router.put("/account", response_model=AccountView)
async def put_account(
    payload: AccountUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AccountView:
    """Connect or update this workspace's WhatsApp number (owners). Omitted secrets keep the saved ones."""
    require_permission(context, Permission.WHATSAPP_MANAGE)
    secrets = {
        "access_token": payload.access_token.get_secret_value().strip() if payload.access_token else None,
        "app_secret": payload.app_secret.get_secret_value().strip() if payload.app_secret else None,
        "verify_token": payload.verify_token.get_secret_value().strip() if payload.verify_token else None,
    }
    secrets = {key: value or None for key, value in secrets.items()}
    if secrets["verify_token"] is not None and len(secrets["verify_token"]) < MIN_VERIFY_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Use a verify token of at least {MIN_VERIFY_TOKEN} random characters.",
        )
    now = utc_now()
    try:
        with open_whatsapp_store(context) as store:
            existing = store.get_account()
            if existing is None and None in secrets.values():
                missing = [label for key, label in (("access_token", "access token"), ("app_secret", "app secret"), ("verify_token", "verify token")) if secrets[key] is None]
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="To connect WhatsApp, enter the " + ", ".join(missing) + ".",
                )
            changed = existing is None or any(secrets.values()) or (
                existing.phone_number_id, existing.business_account_id
            ) != (payload.phone_number_id, payload.business_account_id)
            account = WhatsAppAccount(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                phone_number_id=payload.phone_number_id,
                business_account_id=payload.business_account_id,
                display_phone_number=None if changed else existing.display_phone_number if existing else None,
                verified_name=None if changed else existing.verified_name if existing else None,
                quality_rating=None if changed else existing.quality_rating if existing else None,
                verify_token_hash=verify_token_hash(secrets["verify_token"]) if secrets["verify_token"] else existing.verify_token_hash,  # type: ignore[union-attr]
                owner_test_number=payload.owner_test_number,
                auto_send_templates=payload.auto_send_templates,
                triage_enabled=payload.triage_enabled,
                message_retention_days=(
                    payload.message_retention_days
                    if "message_retention_days" in payload.model_fields_set
                    else existing.message_retention_days if existing else None
                ),
                status=AccountStatus.UNVERIFIED if changed else existing.status,  # type: ignore[union-attr]
                last_error=None if changed else existing.last_error if existing else None,
                last_checked_at=None if changed else existing.last_checked_at if existing else None,
                last_webhook_at=existing.last_webhook_at if existing else None,
                created_at=existing.created_at if existing else now,
                updated_at=now,
                updated_by=context.user_id,
            )
            store.save_account(account, access_token=secrets["access_token"], app_secret=secrets["app_secret"])
    except PhoneNumberTakenError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This phone number id is already connected to another ANUM workspace. Disconnect it there first.",
        ) from None
    with _template_lock:
        _template_cache.pop((context.tenant_id, context.workspace_id), None)
    _audit(
        repository, context, "whatsapp.account.saved", f"whatsapp:{payload.phone_number_id}",
        metadata={
            "created": existing is None,
            "secrets_changed": sorted(key for key, value in secrets.items() if value),
            "auto_send_templates": len(payload.auto_send_templates),
            "triage_enabled": payload.triage_enabled,
            "message_retention_days": account.message_retention_days,
        },
    )
    if changed and not setup_problems():
        with open_whatsapp_store(context) as store:
            full = store.get_account(include_secrets=True)
        if full is not None:
            _save_checked(context, await _check(full, utc_now()))
    with open_whatsapp_store(context) as store:
        return _account_view(store.get_account(), context)


@router.post("/account/check", response_model=AccountView)
async def check_account(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AccountView:
    """Health: ask Meta for the phone number's profile with the saved token."""
    require_permission(context, Permission.WHATSAPP_MANAGE)
    with open_whatsapp_store(context) as store:
        account = _require_account(store, include_secrets=True)
    saved = _save_checked(context, await _check(account, utc_now()))
    _audit(
        repository, context, "whatsapp.account.checked", f"whatsapp:{saved.phone_number_id}",
        "success" if saved.status == AccountStatus.CONNECTED else "failure", {"status": str(saved.status)},
    )
    return _account_view(saved, context)


@router.post("/account/test")
async def test_account(
    payload: TestSend | None = None,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> dict[str, str]:
    """Send a template to the owner's own number (Meta's sample ``hello_world`` by default)."""
    require_permission(context, Permission.WHATSAPP_MANAGE)
    from .demo import refuse_send_in_demo

    refuse_send_in_demo(context)
    payload = payload or TestSend()
    if not TEMPLATE_NAME.match(payload.template) or not LANGUAGE_CODE.match(payload.language):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Enter a template name and a language code such as en_US.")
    with open_whatsapp_store(context) as store:
        account = _require_account(store, include_secrets=True)
    if not account.owner_test_number:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Save your own WhatsApp number as the test number first (it receives the test message).",
        )
    try:
        wamid = await graph_client(account).send_template(
            account.phone_number_id, account.owner_test_number, payload.template, payload.language, []
        )
    except WhatsAppGraphError as exc:
        _audit(repository, context, "whatsapp.test.failed", f"whatsapp:{account.phone_number_id}", "failure", {"meta_code": exc.code})
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"The test message was not sent. {exc.message}") from None
    _audit(repository, context, "whatsapp.test.sent", f"whatsapp:{account.phone_number_id}", metadata={"template": payload.template})
    return {"status": "sent", "wamid": wamid, "to": account.owner_test_number}


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """Disconnect: forget the number and its credentials. Conversations stay readable."""
    require_permission(context, Permission.WHATSAPP_MANAGE)
    with open_whatsapp_store(context) as store:
        account = store.get_account()
        if account is None or not store.delete_account():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WhatsApp is not connected for this workspace.")
    with _template_lock:
        _template_cache.pop((context.tenant_id, context.workspace_id), None)
    _audit(repository, context, "whatsapp.account.disconnected", f"whatsapp:{account.phone_number_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/templates", response_model=TemplateList)
async def list_templates(
    refresh: bool = Query(default=False),
    context: TenantContext = Depends(tenant_context),
) -> TemplateList:
    require_permission(context, Permission.WHATSAPP_READ)
    with open_whatsapp_store(context) as store:
        account = _require_account(store, include_secrets=True)
    try:
        found = await templates_for(account, refresh=refresh)
    except WhatsAppGraphError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Templates could not be loaded from Meta. {exc.message}") from None
    auto = set(account.auto_send_templates)
    order = {"APPROVED": 0, "PENDING": 1}
    found = sorted(found, key=lambda item: (order.get(item.status, 2), item.name, item.language))
    return TemplateList(
        templates=[TemplateView(**item.as_dict(), auto_send=item.name in auto) for item in found],
        fetched_at=utc_now(),
    )


# -- HTTP: inbox ----------------------------------------------------------------------------


def _conversation_or_404(store: WhatsAppStoreProtocol, conversation_id: str) -> Conversation:
    conversation = store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WhatsApp conversation not found")
    return conversation


@router.get("/conversations", response_model=list[ConversationView])
async def list_conversations(
    limit: int = Query(default=30, ge=1, le=MAX_LISTED),
    before: str | None = Query(default=None, max_length=80),
    q: str | None = Query(default=None, max_length=200),
    filter: ConversationFilter = Query(default="all"),
    context: TenantContext = Depends(tenant_context),
) -> list[ConversationView]:
    """Newest activity first; ``before`` is the last conversation id of the previous page."""
    require_permission(context, Permission.WHATSAPP_READ)
    now = utc_now()
    with open_whatsapp_store(context) as store:
        cursor = store.get_conversation(before) if before else None
        if before and cursor is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown cursor")
        items = store.list_conversations(limit=limit, before=cursor, query=(q or "").strip() or None, filter=filter)
        return [_conversation_view(item, store, now) for item in items]


def _message_views(store: WhatsAppStoreProtocol, repository: AnumRepository, context: TenantContext,
                   messages: list[Message], now: datetime) -> list[MessageView]:
    """Views with the pending approval of each message waiting for one. A message whose
    approval was rejected or lapsed (or never got one) is recorded as not sent."""
    views: list[MessageView] = []
    for message in messages:
        approval: Approval | None = None
        if message.status == MessageStatus.AWAITING_APPROVAL:
            found = repository.get_approval(message.approval_id, context) if message.approval_id else None
            lapsed = found is not None and found.expires_at is not None and found.expires_at <= now
            if found is not None and found.status == ApprovalStatus.PENDING and not lapsed:
                approval = found
            elif found is not None and found.status == ApprovalStatus.APPROVED:
                pass  # approved: the tool is running or about to
            elif found is None and now - _aware(message.created_at) < ORPHAN_AFTER:
                pass  # the send request is still being set up
            else:
                if found is None:
                    reason = "Not sent: the send request did not complete."
                elif found.status == ApprovalStatus.REJECTED:
                    reason = "Not sent: the approval was rejected."
                else:
                    reason = "Not sent: the approval expired."
                message.status = MessageStatus.NOT_SENT
                message.error = reason
                message.updated_at = now
                store.save_message(message)
        views.append(MessageView(**message.model_dump(), approval=approval))
    return views


@router.get("/conversations/{conversation_id}", response_model=MessagePage)
async def get_conversation(
    conversation_id: str,
    limit: int = Query(default=30, ge=1, le=MAX_LISTED),
    before: str | None = Query(default=None, max_length=80),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> MessagePage:
    """The conversation and a page of its messages (newest page first; ``before`` pages back)."""
    require_permission(context, Permission.WHATSAPP_READ)
    now = utc_now()
    with open_whatsapp_store(context) as store:
        conversation = _conversation_or_404(store, conversation_id)
        cursor = store.get_message(before) if before else None
        if before and (cursor is None or cursor.conversation_id != conversation_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown cursor")
        page = store.list_messages(conversation_id, limit=limit + 1, before=cursor)
        has_more = len(page) > limit
        messages = list(reversed(page[:limit]))
        views = _message_views(store, repository, context, messages, now)
        return MessagePage(conversation=_conversation_view(conversation, store, now), messages=views, has_more=has_more)


@router.post("/conversations/{conversation_id}/read", response_model=ConversationView)
async def mark_read(conversation_id: str, context: TenantContext = Depends(tenant_context)) -> ConversationView:
    """Mark the conversation read in ANUM (nothing is sent to the customer)."""
    require_permission(context, Permission.WHATSAPP_READ)
    now = utc_now()
    with open_whatsapp_store(context) as store:
        conversation = _conversation_or_404(store, conversation_id)
        if conversation.unread_count:
            conversation.unread_count = 0
            conversation.updated_at = now
            store.save_conversation(conversation)
        return _conversation_view(conversation, store, now)


@router.post("/conversations/{conversation_id}/draft", response_model=Suggestion)
async def draft_reply(
    conversation_id: str,
    payload: DraftRequest | None = None,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Suggestion:
    """A suggested reply (and the triage) for the conversation. Nothing is sent."""
    require_permission(context, Permission.WHATSAPP_REPLY)
    instructions = " ".join(((payload.instructions if payload else None) or "").split()) or None
    with open_whatsapp_store(context) as store:
        conversation = _conversation_or_404(store, conversation_id)
        messages = list(reversed(store.list_messages(conversation_id, limit=12, before=None)))
    if not messages:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The conversation has no messages to answer yet.")
    suggestion = await make_suggestion(context, conversation, messages, instructions=instructions)
    with open_whatsapp_store(context) as store:
        current = _conversation_or_404(store, conversation_id)
        _apply_suggestion(current, suggestion, utc_now())
        store.save_conversation(current)
    # Which conversation and how the suggestion was made; never its text or the instructions.
    _audit(
        repository, context, "whatsapp.draft.suggested", conversation_id,
        metadata={"category": str(suggestion.category), "source": suggestion.source, "instructions": instructions is not None},
    )
    return suggestion


@router.get("/messages/{message_id}/media")
async def get_media(message_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """Download a message's media from Meta on demand (size-capped; not stored by ANUM)."""
    require_permission(context, Permission.WHATSAPP_READ)
    with open_whatsapp_store(context) as store:
        message = store.get_message(message_id)
        if message is None or message.media is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This message has no media.")
        account = _require_account(store, include_secrets=True)
    try:
        media = await graph_client(account).media(message.media.id, max_bytes=whatsapp_settings.media_max_bytes)
    except WhatsAppGraphError as exc:
        code = status.HTTP_413_CONTENT_TOO_LARGE if exc.status == 413 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=code, detail=f"The file could not be downloaded. {exc.message}") from None
    mime = media.mime_type.split(";", 1)[0].strip().lower()
    inline = bool(INLINE_MEDIA.match(mime))
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", message.media.filename or f"{message.kind}-{message.id}")[:100]
    return Response(
        content=media.content,
        media_type=mime if inline else "application/octet-stream",
        headers={
            "content-disposition": f'{"inline" if inline else "attachment"}; filename="{filename}"',
            "x-content-type-options": "nosniff",
            "cache-control": "private, no-store",
        },
    )


# -- HTTP: sending (a governed tool call) ---------------------------------------------------


def _start_tool_run(
    repository: AnumRepository, context: TenantContext, call: ToolCall, title: str, prompt: str
) -> tuple[AgentRuntime, Task, Any]:
    now = utc_now()
    task = Task(
        id=new_id("task"),
        title=title[:160],
        prompt=prompt[:2000],
        status=TaskStatus.CREATED,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        created_at=now,
        updated_at=now,
        created_by=context.user_id,
    )
    repository.create_task(task)
    repository.record_event(
        create_event(CanonicalEventName.TASK_CREATED, context, task.id, {"title": task.title}, correlation_id=task.id, created_at=now).event
    )
    runtime = AgentRuntime(MockModelGateway(), repository, tools=register_whatsapp_tools(ToolRegistry()))
    run = runtime.new_run(task)
    return runtime, task, run


def _checkpoint(runtime: AgentRuntime, task: Task, run: Any, context: TenantContext, call: ToolCall) -> ToolPolicyDecision:
    decision = runtime.evaluate(call, context)
    proposal = AgentRunStep(
        id=new_id("step"),
        type="tool_proposal",
        summary=f"Proposed tool call: {call.name}",
        created_at=utc_now(),
        metadata={
            "tool": call.name,
            "risk_level": decision.risk_level.value,
            "policy_outcome": decision.outcome.value,
            "governance_rules": list(decision.governance_rules),
            "autonomy_mode": (decision.autonomy or {}).get("mode"),
            "autonomy_reason": (decision.autonomy or {}).get("reason"),
        },
    )
    run.steps.append(proposal)
    run.checkpoint.autonomy = decision.autonomy
    run.checkpoint.phase = RunPhase.TOOL_READY
    run.checkpoint.version += 1
    run.checkpoint.tool_call = call.model_dump(mode="json")
    run.checkpoint.last_step_id = proposal.id
    task.status = run.status = TaskStatus.RUNNING
    runtime.repository.save_task(task)
    runtime.repository.save_run(run)
    return decision


@router.post("/conversations/{conversation_id}/send", response_model=SendResponse)
async def send(
    conversation_id: str,
    payload: SendRequest,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> SendResponse:
    """Ask to send a reply. Free-form text always waits for approval (and needs the 24-hour
    window). Whether a template goes out at once is decided by the runtime's tool policy and
    autonomy policy together (docs/autonomy.md): the auto-send list is the whatsapp
    category's approved templates, the customer must have written in this conversation,
    and the workspace's approval switches, organization rules, autonomy level, quiet hours
    and daily caps all still apply."""
    require_permission(context, Permission.WHATSAPP_REPLY)
    text = (payload.text or "").strip()
    if bool(text) == bool(payload.template):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Send either a text reply or a template.")
    now = utc_now()
    with open_whatsapp_store(context) as store:
        account = _require_account(store, include_secrets=payload.template is not None)
        conversation = _conversation_or_404(store, conversation_id)
        reply_to = None
        if payload.reply_to_message_id:
            quoted = store.get_message(payload.reply_to_message_id)
            if quoted is None or quoted.conversation_id != conversation_id or not quoted.wamid:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The quoted message is not in this conversation.")
            reply_to = quoted.wamid
    if setup_problems():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="WhatsApp is not usable: " + ", ".join(setup_problems()))

    if text:
        if not conversation.is_window_open(now):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=window_refusal(conversation, now))
        message = _outbound(context, conversation, body=text, kind="text", status_=MessageStatus.AWAITING_APPROVAL, reply_to=reply_to)
        call = ToolCall(
            name=SEND_TOOL,
            arguments={"conversation_id": conversation.id, "to": conversation.wa_id, "text": text,
                       "reply_to": reply_to, "message_id": message.id},
        )
        title = f"WhatsApp reply to {conversation.contact_name or conversation.wa_id}"
        prompt = f"Send this WhatsApp reply to {conversation.contact_name or conversation.wa_id} (+{conversation.wa_id}): {text}"
    else:
        assert payload.template is not None  # nosec B101 - checked above
        chosen = payload.template
        if not TEMPLATE_NAME.match(chosen.name) or not LANGUAGE_CODE.match(chosen.language):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Choose a template from the list.")
        try:
            template = find_template(await templates_for(account), chosen.name, chosen.language)
        except WhatsAppGraphError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Templates could not be loaded from Meta. {exc.message}") from None
        problem = template_problem(template, chosen.name, chosen.language, chosen.parameters)
        if problem:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)
        use = TemplateUse(name=chosen.name, language=chosen.language, parameters=chosen.parameters)
        body = fill_template(template.body if template else "", chosen.parameters)
        message = _outbound(context, conversation, body=body, kind="template",
                            status_=MessageStatus.AWAITING_APPROVAL, template=use)
        call = ToolCall(
            name=TEMPLATE_TOOL,
            arguments={"conversation_id": conversation.id, "to": conversation.wa_id, "template": chosen.name,
                       "language": chosen.language, "parameters": chosen.parameters, "message_id": message.id},
        )
        title = f"WhatsApp template {chosen.name} to {conversation.contact_name or conversation.wa_id}"
        prompt = f"Send the WhatsApp template {chosen.name} ({chosen.language}) to {conversation.contact_name or conversation.wa_id} (+{conversation.wa_id}): {body}"

    with open_whatsapp_store(context) as store:
        store.add_message(message)
    runtime, task, run = _start_tool_run(repository, context, call, title, prompt)
    message.task_id = task.id
    decision = _checkpoint(runtime, task, run, context, call)

    approval: Approval | None = None
    detail: str
    if decision.outcome == ToolPolicyOutcome.BLOCK:
        runtime._fail(task, run, context, decision.reason)
        repository.save_task(task)
        repository.save_run(run)
        message.status, message.error = MessageStatus.NOT_SENT, f"Not sent: {decision.reason}"
        with open_whatsapp_store(context) as store:
            store.save_message(message)
        _audit(repository, context, "whatsapp.send.blocked", conversation.id, "denied", {"tool": call.name, "task_id": task.id})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Not sent: {decision.reason}")
    if decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
        runtime._pause_for_approval(task, run, context, call, decision)
        repository.save_task(task)
        repository.save_run(run)
        approval = repository.get_approval(run.checkpoint.approval_id, context) if run.checkpoint.approval_id else None
        message.status = MessageStatus.AWAITING_APPROVAL
        message.approval_id = approval.id if approval else None
        with open_whatsapp_store(context) as store:
            store.save_message(message)
        outcome: Literal["sent", "awaiting_approval", "failed"] = "awaiting_approval"
        detail = "Waiting for approval. An owner approves it here or under Approvals; then it is sent."
    else:
        message.status = MessageStatus.SENDING
        with open_whatsapp_store(context) as store:
            store.save_message(message)
        # The runtime evaluates again before running (quiet hours may have just begun).
        executed = runtime.begin_execution(task, run, context)
        if executed is not None:
            await runtime.finish_execution(task, run, context, executed)
        repository.save_task(task)
        repository.save_run(run)
        if executed is None and run.checkpoint.phase == RunPhase.WAITING_APPROVAL and run.checkpoint.approval_id:
            approval = repository.get_approval(run.checkpoint.approval_id, context)
            message.status = MessageStatus.AWAITING_APPROVAL
            message.approval_id = approval.id if approval else None
            with open_whatsapp_store(context) as store:
                store.save_message(message)
            outcome = "awaiting_approval"
            detail = "Waiting for approval. An owner approves it here or under Approvals; then it is sent."
        else:
            with open_whatsapp_store(context) as store:
                message = store.get_message(message.id) or message
                if executed is None and message.status == MessageStatus.SENDING:
                    message.status, message.error = MessageStatus.NOT_SENT, "Not sent: " + (run.steps[-1].summary if run.steps else "blocked by policy.")
                    store.save_message(message)
            outcome = "sent" if message.status == MessageStatus.SENT else "failed"
            detail = "Sent." if outcome == "sent" else (message.error or run.result or "Not sent.")
    _audit(
        repository, context, "whatsapp.send.requested", conversation.id,
        metadata={"tool": call.name, "task_id": task.id, "outcome": outcome,
                  "approval_id": approval.id if approval else None, "payload_hash": approval.payload_hash if approval else None},
    )
    return SendResponse(outcome=outcome, message=MessageView(**message.model_dump(), approval=approval), approval=approval, detail=detail)


# -- HTTP: webhook (public) -----------------------------------------------------------------


@router.get(
    "/webhook",
    response_class=PlainTextResponse,
    responses={403: {"description": "The verify token is not one any workspace saved"}},
)
async def verify_webhook(
    mode: str = Query(default="", alias="hub.mode", max_length=40),
    token: str = Query(default="", alias="hub.verify_token", max_length=500),
    challenge: str = Query(default="", alias="hub.challenge", max_length=200),
) -> PlainTextResponse:
    """Meta's subscription check: echo ``hub.challenge`` when the verify token is known."""
    if mode != "subscribe" or not token or not CHALLENGE.match(challenge):
        return PlainTextResponse("Bad verification request", status_code=status.HTTP_400_BAD_REQUEST)
    if not verify_token_known(token):
        return PlainTextResponse("Unknown verify token", status_code=status.HTTP_403_FORBIDDEN)
    return PlainTextResponse(challenge)


def _phone_ids(payload: dict[str, Any]) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """``changes[].value`` items by the phone number id they belong to."""
    found: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict) or not isinstance(change.get("value"), dict):
                continue
            value = change["value"]
            phone_id = str((value.get("metadata") or {}).get("phone_number_id") or "")
            if valid_graph_id(phone_id):
                found.setdefault(phone_id, []).append((str(change.get("field") or ""), value))
    return found


def _signature_ok(secret: SecretStr | None, body: bytes, signature: str) -> bool:
    if secret is None:
        return False
    expected = hmac.new(secret.get_secret_value().encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


def _too_many(retry_after: int) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"error": {"code": "rate_limited", "message": "Too many webhook deliveries; retry later."}},
        headers={"Retry-After": str(max(1, retry_after))},
    )


@router.post("/webhook")
async def receive_webhook(request: Request, background: BackgroundTasks) -> Response:
    """Meta's deliveries: verified with the app secret before anything is stored."""
    body = await request.body()
    if len(body) > whatsapp_settings.webhook_max_body_bytes:
        return JSONResponse(status_code=status.HTTP_413_CONTENT_TOO_LARGE, content={"error": {"code": "payload_too_large", "message": "Webhook body too large."}})
    match = SIGNATURE.match(request.headers.get("x-hub-signature-256", "").strip())
    if match is None:
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"error": {"code": "unauthorized", "message": "Missing or malformed X-Hub-Signature-256."}})
    try:
        payload = await request.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": {"code": "bad_request", "message": "Not a WhatsApp Business Account webhook."}})
    by_phone = _phone_ids(payload)
    for phone_id in by_phone:
        decision = _webhook_buckets.acquire(f"phone:{phone_id}")
        if not decision.allowed:
            return _too_many(decision.retry_after_seconds)
    routes = route_phone_numbers(set(by_phone))
    now = utc_now()
    verified: list[tuple[TenantContext, list[tuple[str, dict[str, Any]]]]] = []
    for phone_id, (tenant_id, workspace_id) in routes.items():
        context = system_context(tenant_id, workspace_id)
        with open_whatsapp_store(context) as store:
            account = store.get_account(include_secrets=True)
        if account is not None and account.phone_number_id == phone_id and _signature_ok(account.app_secret, body, match.group(1)):
            verified.append((context, by_phone[phone_id]))
    if not verified:
        decision = _bad_signature_buckets.acquire(client_key(request.scope))
        logger.warning(
            "whatsapp_webhook_unverified phones=%d routed=%d",
            len(by_phone), len(routes),
            extra={"anum_whatsapp": {"event": "webhook_unverified", "phones": len(by_phone), "routed": len(routes)}},
        )
        if not decision.allowed:
            return _too_many(decision.retry_after_seconds)
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"error": {"code": "unauthorized", "message": "The signature does not match any connected WhatsApp number."}})
    total = WebhookCounts()
    for context, values in verified:
        counts = WebhookCounts()
        with open_whatsapp_store(context) as store:
            meta_error: str | None = None
            for field, value in values:
                meta_error = process_value(store, context, field, value, now, counts) or meta_error
            account = store.get_account()
            if account is not None:
                account.last_webhook_at = now
                if meta_error:
                    account.last_error = f"Meta reported: {meta_error}"
                store.save_account(account, access_token=None, app_secret=None)
        for wa_id, keyword, message_id in counts.keywords:
            # Before automations hear about the message, so nothing they start can send to
            # a customer who has just opted out (docs/privacy.md#contact-suppression).
            apply_inbound_keyword(context, SuppressionChannel.WHATSAPP, wa_id, keyword, f"WhatsApp message {message_id}")  # type: ignore[arg-type]
        if counts.conversations:
            background.add_task(triage_conversations, context, list(counts.conversations))
        if counts.arrived:
            background.add_task(_route_to_assistants, context, list(counts.arrived))
        if counts.received:
            background.add_task(_note_business_replies, context, sorted({str(item["from"]) for item in counts.received}))
            from .automation_triggers import record_inbound_events

            record_inbound_events(
                context, [("whatsapp.message_received", item["message_id"], item) for item in counts.received]
            )
        for name in ("messages", "duplicates", "stale", "statuses", "echoes"):
            setattr(total, name, getattr(total, name) + getattr(counts, name))
    logger.info(
        "whatsapp_webhook messages=%d duplicates=%d stale=%d statuses=%d",
        total.messages, total.duplicates, total.stale, total.statuses,
        extra={"anum_whatsapp": {"event": "webhook", **total.model_dump(exclude={"conversations", "received"})}},
    )
    return JSONResponse({"status": "ok", **total.model_dump(exclude={"conversations", "received"})})
