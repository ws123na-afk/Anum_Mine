from __future__ import annotations

from datetime import datetime
from threading import RLock
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field, SecretStr, field_validator

from .authorization import Permission
from .dependencies import provisioning_repository_context, require_permission, tenant_context
from .identity import local_sessions
from .repository import AnumRepository
from .schemas import Tenant, TenantContext, Workspace, WorkspaceMembership, utc_now
from .settings import settings

router = APIRouter(prefix="/api/v1", tags=["onboarding"])


class LocalSessionCreate(BaseModel):
    tenant_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,80}$")
    workspace_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,80}$")
    user_id: str = Field(pattern=r"^[a-zA-Z0-9@._-]{3,160}$")


class LocalSessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    context: TenantContext


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


def _model_is_configured(context: TenantContext) -> bool:
    config = _model_configs.get((context.tenant_id, context.workspace_id))
    return bool(config and (config.provider == "mock" or config.api_key))


@router.post("/auth/local/session", response_model=LocalSessionResponse, status_code=201)
async def create_local_session(payload: LocalSessionCreate) -> LocalSessionResponse:
    if settings.environment != "local" or settings.auth_mode not in {"headers", "local"}:
        raise HTTPException(status_code=404, detail="Local authentication is unavailable")
    context = TenantContext(
        tenant_id=payload.tenant_id,
        workspace_id=payload.workspace_id,
        user_id=payload.user_id,
        roles=["owner"],
    )
    token, expires_at = local_sessions.create(context)
    return LocalSessionResponse(access_token=token, expires_at=expires_at, context=context)


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


@router.get("/notification-preferences", response_model=NotificationPreferences)
async def get_notification_preferences(context: TenantContext = Depends(tenant_context)) -> NotificationPreferences:
    return _notifications.get((context.tenant_id, context.workspace_id, context.user_id), NotificationPreferences())


@router.put("/notification-preferences", response_model=NotificationPreferences)
async def set_notification_preferences(payload: NotificationPreferences, context: TenantContext = Depends(tenant_context)) -> NotificationPreferences:
    with _lock:
        _notifications[(context.tenant_id, context.workspace_id, context.user_id)] = payload
    return payload
