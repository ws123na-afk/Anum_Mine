"""Connection setup: which owner-facing integrations this server has, and what is missing
(docs/integrations-setup.md, docs/tools-and-integrations.md#connection-setup).

``GET /api/v1/integrations/setup`` (any member with ``integration:read``) lists every
integration an owner can connect: AI model, server speech, recording transcription, web
search, the three mail providers, WhatsApp, Zoom and Teams, phone push, notification email,
Google and Apple sign-in, error reporting, file storage, the outbound webhook, the five
marketing apps, sales channels, the fleet data source and developer mode. Each entry has a
``status``:

* ``connected``: set up on the server and, where a workspace connects an account, at
  least one is connected here and none is failing;
* ``ready``: the server is set up; someone still connects an account on ``connect_view``;
* ``not_configured``: the server lacks settings (``missing`` names them);
* ``error``: a setting is half-done or a connected account is failing.

Only setting *names* are ever returned, never a value. ``missing`` and ``redirect_urls``
(the exact callback URLs to register with each provider) are filled for workspace owners
and organization administrators only; everyone else sees the status and where to connect.
Workspace connection counts are read in the caller's tenant scope (RLS applies) and only
for modules the caller can already read.

``POST /api/v1/integrations/setup/{id}/check`` (organization administrators only) opens a
TCP connection to the host the *operator* configured for that integration, records an
``integration.setup_checked`` audit record and returns whether it answered. Nothing a
tenant sends chooses the host (SSRF), and nothing is posted.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import (
    has_permission,
    is_organization_admin,
    repository_context,
    require_permission,
    tenant_context,
)
from .repository import AnumRepository
from .schemas import TenantContext, new_id, utc_now
from .settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/integrations/setup", tags=["integrations"])

CHECK_TIMEOUT_SECONDS = 2.5
CHECK_DENIED = (
    "Only an organization administrator can run a connection check. "
    "Ask your organization's administrator, or open the runbook to check it on the server."
)
_SETTING_NAME = re.compile(r"\bANUM_[A-Z0-9_]+\b")


class SetupStatus(StrEnum):
    CONNECTED = "connected"
    READY = "ready"
    NOT_CONFIGURED = "not_configured"
    ERROR = "error"


class SetupItem(BaseModel):
    id: str
    group: str
    status: SetupStatus
    # The web view where the account is connected or used (None: server settings only).
    connect_view: str | None = None
    # Accounts connected in this workspace (None: not applicable, or not readable by the caller).
    connections: int | None = None
    # Of those, how many are failing or need signing in again.
    failing: int = 0
    # Names of the server settings to add or fix (owners and organization administrators only).
    missing: list[str] = Field(default_factory=list)
    # Exact callback URLs to register with the provider (owners and organization administrators only).
    redirect_urls: list[str] = Field(default_factory=list)
    # The anchor of this integration's section in docs/integrations-setup.md.
    doc: str
    # An organization administrator can run a reachability check now.
    checkable: bool = False


class IntegrationSetup(BaseModel):
    items: list[SetupItem]
    can_view_setup: bool
    can_check: bool


class SetupCheck(BaseModel):
    id: str
    reachable: bool
    latency_ms: int | None = None
    # Only an exception class or a fixed phrase: never a host, user or message from the network.
    detail: str
    checked_at: str


# -- one entry per integration -------------------------------------------------------------


class _Entry:
    """What one integration needs from the server and where it is checked."""

    def __init__(
        self,
        item_id: str,
        group: str,
        doc: str,
        evaluate: Callable[[TenantContext], dict[str, Any]],
        *,
        connect_view: str | None = None,
        target: Callable[[], tuple[str, int] | None] | None = None,
    ) -> None:
        self.id = item_id
        self.group = group
        self.doc = doc
        self.evaluate = evaluate
        self.connect_view = connect_view
        self.target = target


def _secret(value: Any) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    return (getter() if getter else str(value)).strip()


def _url_target(url: str, default_https: bool = True) -> tuple[str, int] | None:
    try:
        parsed = httpx.URL(url.strip())
    except (httpx.InvalidURL, TypeError):
        return None
    if not parsed.host or parsed.scheme not in {"http", "https"}:
        return None
    return parsed.host, parsed.port or (443 if parsed.scheme == "https" or (default_https and not parsed.scheme) else 80)


def _names(text: str | None) -> list[str]:
    seen: list[str] = []
    for name in _SETTING_NAME.findall(text or ""):
        if name not in seen:
            seen.append(name)
    return seen


def _from_accounts(total: int, failing: int) -> dict[str, Any]:
    if failing:
        return {"status": SetupStatus.ERROR, "connections": total, "failing": failing}
    if total:
        return {"status": SetupStatus.CONNECTED, "connections": total}
    return {"status": SetupStatus.READY, "connections": 0}


def _counted(context: TenantContext, permission: Permission, count: Callable[[], tuple[int, int]]) -> dict[str, Any]:
    """Server ready: count the workspace's accounts when the caller may read that module."""
    if not has_permission(context, permission):
        return {"status": SetupStatus.READY}
    try:
        total, failing = count()
    except Exception as exc:  # noqa: BLE001 - the page still answers; the count is unknown
        logger.warning("integration_setup_count_failed error_class=%s", type(exc).__name__)
        return {"status": SetupStatus.READY}
    return _from_accounts(total, failing)


# AI ------------------------------------------------------------------------------------------

_MODEL_PROVIDERS = {"mock", "openai-compatible", "anthropic", "ollama"}
_OPENAI_DEFAULT = "https://api.openai.com/v1"


def _model(_: TenantContext) -> dict[str, Any]:
    provider = settings.model_provider.strip().lower()
    if provider not in _MODEL_PROVIDERS:
        return {"status": SetupStatus.ERROR, "missing": ["ANUM_MODEL_PROVIDER"]}
    if provider == "mock":
        return {"status": SetupStatus.NOT_CONFIGURED, "missing": ["ANUM_MODEL_PROVIDER", "ANUM_MODEL_API_KEY"]}
    if provider in {"openai-compatible", "anthropic"} and not _secret(settings.model_api_key):
        return {"status": SetupStatus.NOT_CONFIGURED, "missing": ["ANUM_MODEL_API_KEY"]}
    return {"status": SetupStatus.CONNECTED}


def _model_target() -> tuple[str, int] | None:
    provider = settings.model_provider.strip().lower()
    if provider == "mock":
        return None
    base = settings.model_base_url.strip()
    if provider == "anthropic" and base.rstrip("/") == _OPENAI_DEFAULT:
        return ("api.anthropic.com", 443)
    return _url_target(base)


def _speech(_: TenantContext) -> dict[str, Any]:
    stt = (settings.speech_stt_base_url.strip(), settings.speech_stt_model.strip())
    tts = (settings.speech_tts_base_url.strip(), settings.speech_tts_model.strip())
    if not any(stt) and not any(tts):
        return {
            "status": SetupStatus.NOT_CONFIGURED,
            "missing": ["ANUM_SPEECH_STT_BASE_URL", "ANUM_SPEECH_STT_MODEL", "ANUM_SPEECH_TTS_BASE_URL", "ANUM_SPEECH_TTS_MODEL"],
        }
    missing: list[str] = []
    for prefix, (base, model) in (("STT", stt), ("TTS", tts)):
        if any((base, model)) and not base:
            missing.append(f"ANUM_SPEECH_{prefix}_BASE_URL")
        if any((base, model)) and not model:
            missing.append(f"ANUM_SPEECH_{prefix}_MODEL")
    if all(tts) and not (settings.speech_tts_voice_en.strip() or settings.speech_tts_voice_ar.strip()):
        missing.append("ANUM_SPEECH_TTS_VOICE_EN")
    return {"status": SetupStatus.ERROR, "missing": missing} if missing else {"status": SetupStatus.CONNECTED}


def _speech_target() -> tuple[str, int] | None:
    return _url_target(settings.speech_stt_base_url or settings.speech_tts_base_url) if (
        settings.speech_stt_base_url.strip() or settings.speech_tts_base_url.strip()
    ) else None


def _transcription(_: TenantContext) -> dict[str, Any]:
    from .transcription import setup_problem

    problem = setup_problem(settings)
    if problem is None:
        return {"status": SetupStatus.CONNECTED}
    if settings.transcription_provider == "none":
        return {"status": SetupStatus.NOT_CONFIGURED, "missing": ["ANUM_TRANSCRIPTION_PROVIDER", "ANUM_TRANSCRIPTION_BASE_URL"]}
    return {"status": SetupStatus.ERROR, "missing": _names(problem)}


def _transcription_target() -> tuple[str, int] | None:
    if settings.transcription_provider == "deepgram":
        return _url_target(settings.transcription_base_url) if settings.transcription_base_url.strip() else ("api.deepgram.com", 443)
    if settings.transcription_provider == "openai-compatible" and settings.transcription_base_url.strip():
        return _url_target(settings.transcription_base_url)
    return None


def _web_search(_: TenantContext) -> dict[str, Any]:
    from .web_search import SearxngUrlError, normalize_searxng_url

    raw = settings.searxng_url.strip()
    if not raw:
        return {"status": SetupStatus.NOT_CONFIGURED, "missing": ["ANUM_SEARXNG_URL"]}
    try:
        normalize_searxng_url(raw)
    except SearxngUrlError:
        return {"status": SetupStatus.ERROR, "missing": ["ANUM_SEARXNG_URL"]}
    return {"status": SetupStatus.CONNECTED}


def _web_search_target() -> tuple[str, int] | None:
    return _url_target(settings.searxng_url, default_https=False) if settings.searxng_url.strip() else None


# Email -----------------------------------------------------------------------------------------


def _email(provider_value: str) -> Callable[[TenantContext], dict[str, Any]]:
    def evaluate(context: TenantContext) -> dict[str, Any]:
        from .email_inbox import (
            AccountStatus,
            open_email_store,
            provider_problems,
            visible_accounts,
        )
        from .email_providers import EmailProvider

        provider = EmailProvider(provider_value)
        problems = provider_problems(provider)
        if problems:
            return {"status": SetupStatus.NOT_CONFIGURED, "missing": problems}

        def count() -> tuple[int, int]:
            with open_email_store(context) as store:
                mine = [account for account in visible_accounts(store, context) if account.provider == provider]
            return len(mine), sum(1 for account in mine if account.status != AccountStatus.CONNECTED)

        return _counted(context, Permission.EMAIL_READ, count)

    return evaluate


def _email_redirects() -> list[str]:
    from .email_inbox import email_settings

    return [email_settings.redirect_uri]


def _email_target(provider_value: str) -> Callable[[], tuple[str, int] | None]:
    def target() -> tuple[str, int] | None:
        from .email_inbox import email_settings

        hosts = {"gmail": "gmail.googleapis.com", "outlook": "graph.microsoft.com", "zoho": f"mail.zoho.{email_settings.zoho_data_centre}"}
        return (hosts[provider_value], 443)

    return target


# WhatsApp --------------------------------------------------------------------------------------


def _whatsapp(context: TenantContext) -> dict[str, Any]:
    from .whatsapp import setup_problems
    from .whatsapp_store import AccountStatus, open_whatsapp_store

    problems = setup_problems()
    if problems:
        return {"status": SetupStatus.ERROR, "missing": problems}

    def count() -> tuple[int, int]:
        with open_whatsapp_store(context) as store:
            account = store.get_account()
        if account is None:
            return 0, 0
        return 1, 1 if account.status == AccountStatus.ERROR else 0

    return _counted(context, Permission.WHATSAPP_READ, count)


def _whatsapp_redirects() -> list[str]:
    from .email_inbox import email_settings
    from .whatsapp import WEBHOOK_PATH

    return [f"{email_settings.public_api_url.rstrip('/')}{WEBHOOK_PATH}"]


def _whatsapp_target() -> tuple[str, int] | None:
    from .whatsapp import whatsapp_settings

    return _url_target(whatsapp_settings.graph_api_url)


# Meetings --------------------------------------------------------------------------------------


def _meetings(provider_value: str) -> Callable[[TenantContext], dict[str, Any]]:
    def evaluate(context: TenantContext) -> dict[str, Any]:
        from . import meeting_connectors as connectors

        provider = connectors.ConnectorProvider(provider_value)
        problem = connectors.setup_problem(provider, settings)
        if problem:
            return {"status": SetupStatus.NOT_CONFIGURED, "missing": _names(problem)}

        def count() -> tuple[int, int]:
            # Meeting connections are the caller's own (one per person and provider).
            with connectors.open_connection_store(context) as store:
                return sum(1 for item in store.list_connections(context) if item.provider == provider), 0

        return _counted(context, Permission.MEETING_READ, count)

    return evaluate


def _meetings_redirects() -> list[str]:
    value = settings.meetings_oauth_redirect_uri.strip()
    return [value] if value else []


# Notifications, sign-in, monitoring, storage, webhook -------------------------------------------


def _push(_: TenantContext) -> dict[str, Any]:
    from .push import configured_account

    if configured_account() is None:
        return {"status": SetupStatus.NOT_CONFIGURED, "missing": ["ANUM_FCM_SERVICE_ACCOUNT_JSON"]}
    return {"status": SetupStatus.CONNECTED}


def _smtp(_: TenantContext) -> dict[str, Any]:
    host, sender = (settings.smtp_host or "").strip(), (settings.smtp_from or "").strip()
    if not host and not sender:
        return {"status": SetupStatus.NOT_CONFIGURED, "missing": ["ANUM_SMTP_HOST", "ANUM_SMTP_FROM"]}
    missing = [name for name, value in (("ANUM_SMTP_HOST", host), ("ANUM_SMTP_FROM", sender)) if not value]
    if settings.smtp_username and not _secret(settings.smtp_password):
        missing.append("ANUM_SMTP_PASSWORD")
    return {"status": SetupStatus.ERROR, "missing": missing} if missing else {"status": SetupStatus.CONNECTED}


def _smtp_target() -> tuple[str, int] | None:
    host = (settings.smtp_host or "").strip()
    return (host, settings.smtp_port) if host else None


def _sign_in(provider: str) -> Callable[[TenantContext], dict[str, Any]]:
    def evaluate(_: TenantContext) -> dict[str, Any]:
        if provider in settings.sign_in_providers.split(","):
            return {"status": SetupStatus.CONNECTED}
        return {"status": SetupStatus.NOT_CONFIGURED, "missing": ["ANUM_SIGN_IN_PROVIDERS"]}

    return evaluate


def _sign_in_redirects() -> list[str]:
    # Keycloak's broker endpoint for the identity provider alias (docs/identity.md).
    issuer = settings.keycloak_issuer.rstrip("/")
    return [f"{issuer}/broker/google/endpoint", f"{issuer}/broker/apple/endpoint"]


def _errors(_: TenantContext) -> dict[str, Any]:
    if _secret(settings.sentry_dsn):
        return {"status": SetupStatus.CONNECTED}
    return {"status": SetupStatus.NOT_CONFIGURED, "missing": ["ANUM_SENTRY_DSN"]}


def _errors_target() -> tuple[str, int] | None:
    dsn = _secret(settings.sentry_dsn)
    return _url_target(dsn) if dsn else None


def _files(_: TenantContext) -> dict[str, Any]:
    if settings.object_storage_backend != "s3":
        return {
            "status": SetupStatus.NOT_CONFIGURED,
            "missing": [
                "ANUM_OBJECT_STORAGE_BACKEND",
                "ANUM_S3_ENDPOINT",
                "ANUM_S3_BUCKET",
                "ANUM_S3_ACCESS_KEY",
                "ANUM_S3_SECRET_KEY",
            ],
        }
    missing = [
        name
        for name, value in (
            ("ANUM_S3_ENDPOINT", settings.s3_endpoint.strip()),
            ("ANUM_S3_BUCKET", settings.s3_bucket.strip()),
            ("ANUM_S3_ACCESS_KEY", _secret(settings.s3_access_key)),
            ("ANUM_S3_SECRET_KEY", _secret(settings.s3_secret_key)),
        )
        if not value
    ]
    return {"status": SetupStatus.ERROR, "missing": missing} if missing else {"status": SetupStatus.CONNECTED}


def _files_target() -> tuple[str, int] | None:
    return _url_target(settings.s3_endpoint) if settings.object_storage_backend == "s3" else None


def _webhook(_: TenantContext) -> dict[str, Any]:
    url = (settings.external_webhook_url or "").strip()
    if not url:
        return {"status": SetupStatus.NOT_CONFIGURED, "missing": ["ANUM_EXTERNAL_WEBHOOK_URL"]}
    if _url_target(url) is None:
        return {"status": SetupStatus.ERROR, "missing": ["ANUM_EXTERNAL_WEBHOOK_URL"]}
    return {"status": SetupStatus.CONNECTED}


def _webhook_target() -> tuple[str, int] | None:
    url = (settings.external_webhook_url or "").strip()
    return _url_target(url) if url else None


# Marketing, commerce, fleet data, developer -----------------------------------------------------

_MARKETING_HOSTS = {
    "meta": "graph.facebook.com",
    "linkedin": "api.linkedin.com",
    "x": "api.x.com",
    "tiktok": "open.tiktokapis.com",
    "google": "www.googleapis.com",
}


def _marketing(provider_value: str) -> Callable[[TenantContext], dict[str, Any]]:
    def evaluate(context: TenantContext) -> dict[str, Any]:
        from .marketing.models import AccountStatus
        from .marketing.service import setup_status
        from .marketing.store import open_marketing_store

        row = next((item for item in setup_status() if item["provider"] == provider_value), None)
        if row is None or row["missing"]:
            return {"status": SetupStatus.NOT_CONFIGURED, "missing": list(row["missing"]) if row else []}

        def count() -> tuple[int, int]:
            with open_marketing_store(context) as store:
                mine = [item for item in store.list_accounts(context) if item.provider.value == provider_value]
            return len(mine), sum(1 for item in mine if item.status in {AccountStatus.ERROR, AccountStatus.NEEDS_REAUTH})

        return _counted(context, Permission.MARKETING_READ, count)

    return evaluate


def _marketing_redirects() -> list[str]:
    from .marketing.config import marketing_settings

    value = marketing_settings.oauth_redirect_url.strip()
    return [value] if value else []


def _commerce(context: TenantContext) -> dict[str, Any]:
    from .commerce.store import open_commerce_store

    def count() -> tuple[int, int]:
        with open_commerce_store(context) as store:
            items = store.list_connectors(context)
        return len(items), sum(1 for item in items if item.enabled and item.last_error)

    return _counted(context, Permission.COMMERCE_READ, count)


def _fleet_data(context: TenantContext) -> dict[str, Any]:
    from .tyre.store import open_tyre_store

    def count() -> tuple[int, int]:
        with open_tyre_store(context) as store:
            return (1 if store.get_connection(context) is not None else 0), 0

    return _counted(context, Permission.TYRE_READ, count)


def _developer(context: TenantContext) -> dict[str, Any]:
    from .developer import enabled_for, setup_problems

    problems = setup_problems()
    if problems:
        return {"status": SetupStatus.NOT_CONFIGURED, "missing": problems}
    if not enabled_for(context):
        return {"status": SetupStatus.NOT_CONFIGURED, "missing": ["ANUM_DEVELOPER_TENANT_ID", "ANUM_DEVELOPER_WORKSPACE_ID"]}
    return {"status": SetupStatus.READY}


def _developer_target() -> tuple[str, int] | None:
    from .developer import developer_settings

    return _url_target(developer_settings.github_api_url)


def _constant(host: str) -> Callable[[], tuple[str, int] | None]:
    return lambda: (host, 443)


ENTRIES: list[_Entry] = [
    _Entry("model", "ai", "1-ai-model", _model, connect_view="settings", target=_model_target),
    _Entry("speech", "ai", "2-voice-speech-to-text-and-text-to-speech", _speech, connect_view="voice", target=_speech_target),
    _Entry("transcription", "ai", "3-meeting-recording-transcription", _transcription, connect_view="meetings", target=_transcription_target),
    _Entry("web_search", "ai", "4-private-web-search-searxng", _web_search, connect_view="integrations", target=_web_search_target),
    _Entry("email_gmail", "email", "5-email-gmail-outlook-and-zoho", _email("gmail"), connect_view="inbox", target=_email_target("gmail")),
    _Entry("email_outlook", "email", "5-email-gmail-outlook-and-zoho", _email("outlook"), connect_view="inbox", target=_email_target("outlook")),
    _Entry("email_zoho", "email", "5-email-gmail-outlook-and-zoho", _email("zoho"), connect_view="inbox", target=_email_target("zoho")),
    _Entry("whatsapp", "messaging", "6-whatsapp-business", _whatsapp, connect_view="whatsapp", target=_whatsapp_target),
    _Entry("meetings_zoom", "meetings", "7-zoom-and-microsoft-teams-transcripts", _meetings("zoom"), connect_view="meetings", target=_constant("zoom.us")),
    _Entry("meetings_teams", "meetings", "7-zoom-and-microsoft-teams-transcripts", _meetings("teams"), connect_view="meetings", target=_constant("graph.microsoft.com")),
    _Entry("push", "notifications", "8-phone-push-firebase-cloud-messaging", _push, target=_constant("fcm.googleapis.com")),
    _Entry("notification_email", "notifications", "9-notification-email-smtp", _smtp, target=_smtp_target),
    _Entry("sign_in_google", "sign_in", "10-sign-in-with-google-and-apple", _sign_in("google")),
    _Entry("sign_in_apple", "sign_in", "10-sign-in-with-google-and-apple", _sign_in("apple")),
    _Entry("error_reporting", "monitoring", "11-error-reporting-glitchtip-or-sentry", _errors, target=_errors_target),
    _Entry("files", "storage", "12-file-storage-s3-or-backblaze-b2", _files, connect_view="files", target=_files_target),
    _Entry("external_webhook", "automation", "13-outbound-rest-webhook", _webhook, connect_view="integrations", target=_webhook_target),
    *[
        _Entry(f"marketing_{name}", "marketing", "14-social-media-marketing", _marketing(name), connect_view="marketing", target=_constant(host))
        for name, host in _MARKETING_HOSTS.items()
    ],
    _Entry("commerce", "commerce", "15-sales-channels-amazon-noon-and-your-own-store", _commerce, connect_view="commerce"),
    _Entry("fleet_data", "operations", "16-fleet-and-tyre-data-supabase-or-firebase", _fleet_data, connect_view="tyre"),
    _Entry("developer", "developer", "17-developer-mode-github", _developer, connect_view="developer", target=_developer_target),
]
_BY_ID = {entry.id: entry for entry in ENTRIES}
_REDIRECTS: dict[str, Callable[[], list[str]]] = {
    "email_gmail": _email_redirects,
    "email_outlook": _email_redirects,
    "email_zoho": _email_redirects,
    "whatsapp": _whatsapp_redirects,
    "meetings_zoom": _meetings_redirects,
    "meetings_teams": _meetings_redirects,
    "sign_in_google": lambda: _sign_in_redirects()[:1],
    "sign_in_apple": lambda: _sign_in_redirects()[1:],
    **{f"marketing_{name}": _marketing_redirects for name in _MARKETING_HOSTS},
}


def can_view_setup(context: TenantContext) -> bool:
    """Workspace owners and organization administrators see setting names and callback URLs."""
    if has_permission(context, Permission.ORGANIZATION_MANAGE):
        return True
    return is_organization_admin(context) and has_permission(context, Permission.ORGANIZATION_READ)


def can_check(context: TenantContext) -> bool:
    """Only organization administrators (with an active membership here) run checks."""
    return is_organization_admin(context) and has_permission(context, Permission.ORGANIZATION_READ)


def evaluate_item(entry: _Entry, context: TenantContext, *, details: bool) -> SetupItem:
    try:
        result = entry.evaluate(context)
    except Exception as exc:  # noqa: BLE001 - one broken module never hides the rest of the page
        logger.warning("integration_setup_failed item=%s error_class=%s", entry.id, type(exc).__name__)
        result = {"status": SetupStatus.ERROR}
    state = SetupStatus(result["status"])
    target = entry.target() if entry.target and state != SetupStatus.NOT_CONFIGURED else None
    return SetupItem(
        id=entry.id,
        group=entry.group,
        status=state,
        connect_view=entry.connect_view,
        connections=result.get("connections"),
        failing=result.get("failing", 0),
        missing=list(result.get("missing", [])) if details else [],
        redirect_urls=_REDIRECTS[entry.id]() if details and entry.id in _REDIRECTS else [],
        doc=entry.doc,
        checkable=target is not None,
    )


@router.get("", response_model=IntegrationSetup)
async def get_integration_setup(context: TenantContext = Depends(tenant_context)) -> IntegrationSetup:
    """Every owner-facing integration with its status; setting names only, never values."""
    require_permission(context, Permission.INTEGRATION_READ)
    details = can_view_setup(context)
    return IntegrationSetup(
        items=[evaluate_item(entry, context, details=details) for entry in ENTRIES],
        can_view_setup=details,
        can_check=can_check(context),
    )


async def _reach(host: str, port: int) -> None:
    from .integrations import tcp_probe

    await tcp_probe(host, port)


@router.post("/{item_id}/check", response_model=SetupCheck)
async def check_integration_setup(
    item_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> SetupCheck:
    """Can the API reach the host the operator configured for this integration? Audited."""
    require_permission(context, Permission.INTEGRATION_READ)
    if not can_check(context):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=CHECK_DENIED)
    entry = _BY_ID.get(item_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration not found")
    item = evaluate_item(entry, context, details=False)
    target = entry.target() if entry.target else None
    if item.status == SetupStatus.NOT_CONFIGURED or target is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="There is nothing to check yet: set this integration up on the server first (see the runbook).",
        )
    started = time.monotonic()
    reachable, detail = True, "Reachable"
    try:
        await asyncio.wait_for(_reach(*target), timeout=CHECK_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - report the class only, never a host or message
        reachable, detail = False, type(exc).__name__
    latency = int((time.monotonic() - started) * 1000)
    checked_at = utc_now()
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action="integration.setup_checked",
            target=f"integration:{entry.id}",
            outcome="success" if reachable else "failed",
            correlation_id=new_id("correlation"),
            created_at=checked_at,
            metadata={"reachable": reachable, "latency_ms": latency, "detail": detail},
        )
    )
    return SetupCheck(
        id=entry.id,
        reachable=reachable,
        latency_ms=latency if reachable else None,
        detail=detail,
        checked_at=checked_at.isoformat(),
    )
