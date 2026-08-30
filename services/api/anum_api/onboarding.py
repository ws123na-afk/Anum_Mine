from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from threading import RLock
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field, SecretStr, field_validator

from .authorization import Permission
from .dependencies import provisioning_repository_context, require_permission, tenant_context
from .identity import local_sessions
from .model_gateway import build_model_gateway
from .repository import AnumRepository
from .schemas import Tenant, TenantContext, Workspace, WorkspaceMembership, utc_now
from .settings import settings

router = APIRouter(prefix="/api/v1", tags=["onboarding"])


class LocalSessionCreate(BaseModel):
    tenant_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,80}$")
    workspace_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,80}$")
    user_id: str = Field(pattern=r"^[a-zA-Z0-9@._-]{3,160}$")
    password: SecretStr | None = None


class LocalSessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    context: TenantContext


class LocalAccountScope(BaseModel):
    tenant_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,80}$")
    workspace_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,80}$")
    user_id: str = Field(pattern=r"^[a-zA-Z0-9@._-]{3,160}$")


class ChallengeResponse(BaseModel):
    challenge_id: str
    expires_at: datetime
    delivery_hint: str
    debug_secret: str | None = None


class OtpVerify(BaseModel):
    challenge_id: str
    code: str = Field(pattern=r"^\d{6}$")


class PasswordReset(BaseModel):
    challenge_id: str
    token: str = Field(min_length=20, max_length=300)
    new_password: SecretStr

    @field_validator("new_password")
    @classmethod
    def strong_password(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) < 12 or raw.isalpha() or raw.isdigit():
            raise ValueError("password must be at least 12 characters and contain mixed character types")
        return value


class WorkspaceSwitch(BaseModel):
    workspace_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,80}$")


class OnboardingCreate(BaseModel):
    organization_name: str = Field(min_length=1, max_length=160)
    workspace_name: str = Field(min_length=1, max_length=160)


class OnboardingStatus(BaseModel):
    complete: bool
    tenant: Tenant | None = None
    workspace: Workspace | None = None
    membership: WorkspaceMembership | None = None
    model_configured: bool = False


class ModelConfigWrite(BaseModel):
    provider: str = Field(pattern=r"^(mock|openai_compatible)$")
    model: str = Field(min_length=1, max_length=160)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: SecretStr | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            raise ValueError("base_url must be an absolute HTTP(S) URL without credentials")
        if settings.environment != "local" and parsed.scheme != "https":
            raise ValueError("base_url must use HTTPS outside local development")
        return value.rstrip("/")


class ModelConfigView(BaseModel):
    provider: str
    model: str
    base_url: str
    credential_configured: bool
    credential_hint: str | None = None
    updated_at: datetime


class ModelConnectionTest(BaseModel):
    provider: str
    model: str
    latency_ms: int = Field(ge=0)
    status: str = "connected"


class NotificationPreferences(BaseModel):
    task_completed: bool = True
    approval_required: bool = True
    run_failed: bool = True
    automation_failed: bool = True
    email_enabled: bool = False
    desktop_enabled: bool = True


class _StoredModelConfig(BaseModel):
    provider: str
    model: str
    base_url: str
    api_key: SecretStr | None
    updated_at: datetime

    def view(self) -> ModelConfigView:
        secret = self.api_key.get_secret_value() if self.api_key else None
        return ModelConfigView(
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            credential_configured=bool(secret),
            credential_hint=f"...{secret[-4:]}" if secret else None,
            updated_at=self.updated_at,
        )


_lock = RLock()
_model_configs: dict[tuple[str, str], _StoredModelConfig] = {}
_notifications: dict[tuple[str, str, str], NotificationPreferences] = {}


class _Challenge:
    def __init__(self, scope: LocalAccountScope, secret: str, minutes: int) -> None:
        self.scope = scope
        self.salt = secrets.token_bytes(16)
        self.digest = hashlib.sha256(self.salt + secret.encode()).digest()
        self.expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        self.attempts = 0

    def verify(self, value: str) -> bool:
        self.attempts += 1
        return self.attempts <= 5 and self.expires_at > datetime.now(timezone.utc) and hmac.compare_digest(
            self.digest, hashlib.sha256(self.salt + value.encode()).digest()
        )


class _LocalAuthStore:
    def __init__(self) -> None:
        self.otp: dict[str, _Challenge] = {}
        self.resets: dict[str, _Challenge] = {}
        self.passwords: dict[tuple[str, str], tuple[bytes, bytes]] = {}
        self.lock = RLock()

    def challenge(self, target: dict[str, _Challenge], scope: LocalAccountScope, secret: str, minutes: int) -> tuple[str, _Challenge]:
        challenge_id = f"challenge_{secrets.token_urlsafe(18)}"
        item = _Challenge(scope, secret, minutes)
        with self.lock:
            target[challenge_id] = item
        return challenge_id, item

    def consume(self, target: dict[str, _Challenge], challenge_id: str, secret: str) -> LocalAccountScope | None:
        with self.lock:
            item = target.get(challenge_id)
            if item is None or not item.verify(secret):
                return None
            target.pop(challenge_id, None)
            return item.scope

    def set_password(self, tenant_id: str, user_id: str, password: str) -> None:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
        with self.lock:
            self.passwords[(tenant_id, user_id)] = (salt, digest)

    def password_valid(self, tenant_id: str, user_id: str, password: str | None) -> bool:
        stored = self.passwords.get((tenant_id, user_id))
        if stored is None:
            return True
        if password is None:
            return False
        salt, expected = stored
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
        return hmac.compare_digest(actual, expected)

    def clear(self) -> None:
        with self.lock:
            self.otp.clear(); self.resets.clear(); self.passwords.clear()


_local_auth = _LocalAuthStore()


def _local_enabled() -> None:
    if settings.environment != "local" or settings.auth_mode not in {"headers", "local"}:
        raise HTTPException(status_code=404, detail="Local authentication is unavailable")


def _session_response(context: TenantContext) -> LocalSessionResponse:
    token, expires_at = local_sessions.create(context)
    return LocalSessionResponse(access_token=token, expires_at=expires_at, context=context)


def _delivery_hint(user_id: str) -> str:
    if "@" in user_id:
        name, domain = user_id.split("@", 1)
        return f"{name[:1]}***@{domain}"
    return f"{user_id[:2]}***"


def _model_is_configured(context: TenantContext) -> bool:
    config = _model_configs.get((context.tenant_id, context.workspace_id))
    return bool(config and (config.provider == "mock" or config.api_key))


@router.post("/auth/local/session", response_model=LocalSessionResponse, status_code=201)
async def create_local_session(payload: LocalSessionCreate) -> LocalSessionResponse:
    _local_enabled()
    password = payload.password.get_secret_value() if payload.password else None
    if not _local_auth.password_valid(payload.tenant_id, payload.user_id, password):
        raise HTTPException(status_code=401, detail="Invalid local credentials")
    context = TenantContext(
        tenant_id=payload.tenant_id,
        workspace_id=payload.workspace_id,
        user_id=payload.user_id,
        roles=["owner"],
    )
    return _session_response(context)


@router.post("/auth/local/otp/request", response_model=ChallengeResponse, status_code=202)
async def request_local_otp(payload: LocalAccountScope) -> ChallengeResponse:
    _local_enabled()
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge_id, challenge = _local_auth.challenge(_local_auth.otp, payload, code, 10)
    return ChallengeResponse(challenge_id=challenge_id, expires_at=challenge.expires_at, delivery_hint=_delivery_hint(payload.user_id), debug_secret=code)


@router.post("/auth/local/otp/verify", response_model=LocalSessionResponse)
async def verify_local_otp(payload: OtpVerify) -> LocalSessionResponse:
    _local_enabled()
    scope = _local_auth.consume(_local_auth.otp, payload.challenge_id, payload.code)
    if scope is None:
        raise HTTPException(status_code=401, detail="Invalid or expired verification code")
    return _session_response(TenantContext(tenant_id=scope.tenant_id, workspace_id=scope.workspace_id, user_id=scope.user_id, roles=["owner"]))


@router.post("/auth/local/password/forgot", response_model=ChallengeResponse, status_code=202)
async def forgot_local_password(payload: LocalAccountScope) -> ChallengeResponse:
    _local_enabled()
    token = secrets.token_urlsafe(32)
    challenge_id, challenge = _local_auth.challenge(_local_auth.resets, payload, token, 15)
    return ChallengeResponse(challenge_id=challenge_id, expires_at=challenge.expires_at, delivery_hint=_delivery_hint(payload.user_id), debug_secret=token)


@router.post("/auth/local/password/reset", response_model=LocalSessionResponse)
async def reset_local_password(payload: PasswordReset) -> LocalSessionResponse:
    _local_enabled()
    scope = _local_auth.consume(_local_auth.resets, payload.challenge_id, payload.token)
    if scope is None:
        raise HTTPException(status_code=401, detail="Invalid or expired password reset")
    _local_auth.set_password(scope.tenant_id, scope.user_id, payload.new_password.get_secret_value())
    return _session_response(TenantContext(tenant_id=scope.tenant_id, workspace_id=scope.workspace_id, user_id=scope.user_id, roles=["owner"]))


@router.post("/auth/local/workspace/switch", response_model=LocalSessionResponse)
async def switch_local_workspace(
    payload: WorkspaceSwitch,
    authorization: str | None = Header(default=None),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(provisioning_repository_context),
) -> LocalSessionResponse:
    _local_enabled()
    target = context.model_copy(update={"workspace_id": payload.workspace_id})
    workspace = repository.get_workspace(payload.workspace_id, target)
    membership = repository.get_membership(target)
    if workspace is None or membership is None or not membership.active:
        raise HTTPException(status_code=403, detail="Active target workspace membership required")
    target.roles = [membership.role]
    if authorization and authorization.lower().startswith("bearer anum_local_"):
        local_sessions.revoke(authorization.split(" ", 1)[1])
    return _session_response(target)


@router.delete("/auth/local/session", status_code=204, response_model=None)
async def revoke_local_session(authorization: str | None = Header(default=None)) -> Response:
    if not authorization or not authorization.lower().startswith("bearer anum_local_"):
        raise HTTPException(status_code=401, detail="Local bearer token required")
    local_sessions.revoke(authorization.split(" ", 1)[1])
    return Response(status_code=204)


@router.put("/onboarding", response_model=OnboardingStatus)
async def complete_onboarding(
    payload: OnboardingCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(provisioning_repository_context),
) -> OnboardingStatus:
    require_permission(context, Permission.TENANT_CREATE)
    now = utc_now()
    tenant = repository.get_tenant(context.tenant_id)
    if tenant is None:
        tenant = repository.create_tenant(Tenant(id=context.tenant_id, name=payload.organization_name, created_at=now, updated_at=now))
    workspace = repository.get_workspace(context.workspace_id, context)
    if workspace is None:
        workspace = repository.create_workspace(Workspace(id=context.workspace_id, tenant_id=context.tenant_id, name=payload.workspace_name, created_at=now, updated_at=now))
    membership = repository.get_membership(context)
    if membership is None:
        membership = repository.save_membership(WorkspaceMembership(tenant_id=context.tenant_id, workspace_id=context.workspace_id, user_id=context.user_id, role="owner", created_at=now, updated_at=now))
    return OnboardingStatus(complete=True, tenant=tenant, workspace=workspace, membership=membership, model_configured=_model_is_configured(context))


@router.get("/onboarding", response_model=OnboardingStatus)
async def get_onboarding_status(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(provisioning_repository_context),
) -> OnboardingStatus:
    tenant = repository.get_tenant(context.tenant_id)
    workspace = repository.get_workspace(context.workspace_id, context)
    membership = repository.get_membership(context)
    return OnboardingStatus(complete=all((tenant, workspace, membership)), tenant=tenant, workspace=workspace, membership=membership, model_configured=_model_is_configured(context))


@router.put("/model-config", response_model=ModelConfigView)
async def set_model_config(payload: ModelConfigWrite, context: TenantContext = Depends(tenant_context)) -> ModelConfigView:
    require_permission(context, Permission.ORGANIZATION_MANAGE)
    if payload.provider != "mock" and payload.api_key is None:
        raise HTTPException(status_code=422, detail="api_key is required for external providers")
    config = _StoredModelConfig(**payload.model_dump(), updated_at=utc_now())
    with _lock:
        _model_configs[(context.tenant_id, context.workspace_id)] = config
    return config.view()


@router.get("/model-config", response_model=ModelConfigView)
async def get_model_config(context: TenantContext = Depends(tenant_context)) -> ModelConfigView:
    require_permission(context, Permission.ORGANIZATION_READ)
    config = _model_configs.get((context.tenant_id, context.workspace_id))
    if config is None:
        raise HTTPException(status_code=404, detail="Model configuration not found")
    return config.view()


@router.post("/model-config/test", response_model=ModelConnectionTest)
async def test_model_config(
    context: TenantContext = Depends(tenant_context),
) -> ModelConnectionTest:
    require_permission(context, Permission.ORGANIZATION_MANAGE)
    config = _model_configs.get((context.tenant_id, context.workspace_id))
    if config is None:
        raise HTTPException(status_code=404, detail="Model configuration not found")
    secret = config.api_key.get_secret_value() if config.api_key else None
    provider = "openai-compatible" if config.provider == "openai_compatible" else config.provider
    try:
        gateway = build_model_gateway(
            provider,
            api_key=secret,
            model=config.model,
            base_url=config.base_url,
        )
        response = await gateway.generate_text("Reply with ANUM_OK only.")
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Model provider connection failed") from exc
    return ModelConnectionTest(
        provider=config.provider,
        model=config.model,
        latency_ms=response.metadata.latency_ms if response.metadata else 0,
    )


@router.get("/notification-preferences", response_model=NotificationPreferences)
async def get_notification_preferences(context: TenantContext = Depends(tenant_context)) -> NotificationPreferences:
    return _notifications.get((context.tenant_id, context.workspace_id, context.user_id), NotificationPreferences())


@router.put("/notification-preferences", response_model=NotificationPreferences)
async def set_notification_preferences(payload: NotificationPreferences, context: TenantContext = Depends(tenant_context)) -> NotificationPreferences:
    with _lock:
        _notifications[(context.tenant_id, context.workspace_id, context.user_id)] = payload
    return payload
