from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .schemas import RiskLevel, TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store


class SkillVersionCreate(BaseModel):
    skill_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,127}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=2000)
    instructions: str = Field(min_length=1, max_length=50_000)
    required_tools: list[str] = Field(default_factory=list, max_length=100)
    risk_level: RiskLevel = RiskLevel.LOW


class SkillVersion(SkillVersionCreate):
    id: str
    publisher_tenant_id: str
    created_by: str
    created_at: datetime


class SkillInstallCreate(BaseModel):
    skill_id: str
    version: str
    approved_tools: list[str] = Field(default_factory=list, max_length=100)


class SkillInstallation(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    skill_version_id: str
    skill_id: str
    version: str
    approved_tools: list[str]
    enabled: bool
    installed_by: str
    installed_at: datetime


class SkillResolveRequest(BaseModel):
    skill_id: str
    available_tools: list[str] = Field(default_factory=list, max_length=200)
    maximum_risk: RiskLevel = RiskLevel.HIGH


class SkillInstallationUpdate(BaseModel):
    enabled: bool


class SkillResolution(BaseModel):
    installation_id: str
    skill_version_id: str
    skill_id: str
    version: str
    instructions: str
    granted_tools: list[str]
    risk_level: RiskLevel


class SkillVersionExistsError(ValueError):
    """A tenant can publish a given skill version only once (versions are immutable)."""


class SkillRegistryStore(Protocol):
    """Skill versions (tenant-owned) and installations (per workspace).

    Every call is scoped by an explicit ``TenantContext``. The PostgreSQL store
    (``anum_api.db.skills_repository``) additionally runs inside the tenant's RLS scope.
    """

    def add_version(self, version: SkillVersion) -> SkillVersion: ...
    def get_version(self, context: TenantContext, skill_id: str, version: str) -> SkillVersion | None: ...
    def list_versions(self, context: TenantContext) -> list[SkillVersion]: ...
    def save_installation(self, installation: SkillInstallation) -> SkillInstallation: ...
    def get_installation(self, context: TenantContext, skill_id: str) -> SkillInstallation | None: ...
    def list_installations(self, context: TenantContext) -> list[SkillInstallation]: ...
    def delete_installation(self, context: TenantContext, skill_id: str) -> bool: ...


class SkillStore:
    """In-memory skill registry for ``ANUM_REPOSITORY_BACKEND=memory`` (local and tests)."""

    def __init__(self) -> None:
        self.versions: dict[tuple[str, str, str], SkillVersion] = {}
        self.installations: dict[tuple[str, str, str], SkillInstallation] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.versions.clear()
            self.installations.clear()

    def add_version(self, version: SkillVersion) -> SkillVersion:
        key = (version.publisher_tenant_id, version.skill_id, version.version)
        with self._lock:
            if key in self.versions:
                raise SkillVersionExistsError(version.skill_id)
            self.versions[key] = version
        return version

    def get_version(self, context: TenantContext, skill_id: str, version: str) -> SkillVersion | None:
        return self.versions.get((context.tenant_id, skill_id, version))

    def list_versions(self, context: TenantContext) -> list[SkillVersion]:
        with self._lock:
            return [v for (tenant, _, _), v in self.versions.items() if tenant == context.tenant_id]

    def save_installation(self, installation: SkillInstallation) -> SkillInstallation:
        with self._lock:
            self.installations[(installation.tenant_id, installation.workspace_id, installation.skill_id)] = installation
        return installation

    def get_installation(self, context: TenantContext, skill_id: str) -> SkillInstallation | None:
        return self.installations.get((context.tenant_id, context.workspace_id, skill_id))

    def list_installations(self, context: TenantContext) -> list[SkillInstallation]:
        with self._lock:
            return [i for (tenant, workspace, _), i in self.installations.items()
                    if tenant == context.tenant_id and workspace == context.workspace_id]

    def delete_installation(self, context: TenantContext, skill_id: str) -> bool:
        with self._lock:
            return self.installations.pop((context.tenant_id, context.workspace_id, skill_id), None) is not None


skill_store = SkillStore()


@contextmanager
def open_skill_store(context: TenantContext) -> Iterator[SkillRegistryStore]:
    """The skill registry for one unit of work, chosen by ``ANUM_REPOSITORY_BACKEND``."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.skills_repository import SqlAlchemySkillStore

        return SqlAlchemySkillStore(session)

    with open_scoped_store(context, skill_store, sql_store) as store:
        yield store


router = APIRouter(prefix="/api/v1", tags=["skills"])
_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.BLOCKED: 3}


@router.post("/skills/versions", response_model=SkillVersion, status_code=status.HTTP_201_CREATED)
def publish_skill(payload: SkillVersionCreate, context: TenantContext = Depends(tenant_context)) -> SkillVersion:
    require_permission(context, Permission.POLICY_MANAGE)
    version = SkillVersion(id=new_id("skill_version"), publisher_tenant_id=context.tenant_id,
                           created_by=context.user_id, created_at=utc_now(),
                           **payload.model_dump())
    try:
        with open_skill_store(context) as store:
            return store.add_version(version)
    except SkillVersionExistsError as exc:
        raise HTTPException(409, "Skill version already exists") from exc


@router.get("/skills/versions", response_model=list[SkillVersion])
def list_skill_versions(context: TenantContext = Depends(tenant_context)) -> list[SkillVersion]:
    require_permission(context, Permission.POLICY_READ)
    with open_skill_store(context) as store:
        return store.list_versions(context)


@router.post("/skills/installations", response_model=SkillInstallation, status_code=201)
def install_skill(payload: SkillInstallCreate, context: TenantContext = Depends(tenant_context)) -> SkillInstallation:
    require_permission(context, Permission.POLICY_MANAGE)
    with open_skill_store(context) as store:
        version = store.get_version(context, payload.skill_id, payload.version)
        if version is None:
            raise HTTPException(404, "Skill version not found")
        approved = sorted(set(payload.approved_tools))
        if not set(approved) <= set(version.required_tools):
            raise HTTPException(422, "Approved tools must be declared by the skill")
        return store.save_installation(SkillInstallation(
            id=new_id("skill_install"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            skill_version_id=version.id, skill_id=version.skill_id, version=version.version,
            approved_tools=approved, enabled=True, installed_by=context.user_id, installed_at=utc_now(),
        ))


@router.get("/skills/installations", response_model=list[SkillInstallation])
def list_installations(context: TenantContext = Depends(tenant_context)) -> list[SkillInstallation]:
    require_permission(context, Permission.POLICY_READ)
    with open_skill_store(context) as store:
        return store.list_installations(context)


@router.patch("/skills/installations/{skill_id}", response_model=SkillInstallation)
def update_installation(skill_id: str, payload: SkillInstallationUpdate, context: TenantContext = Depends(tenant_context)) -> SkillInstallation:
    require_permission(context, Permission.POLICY_MANAGE)
    with open_skill_store(context) as store:
        installation = store.get_installation(context, skill_id)
        if installation is None:
            raise HTTPException(404, "Skill installation not found")
        return store.save_installation(installation.model_copy(update={"enabled": payload.enabled}))


@router.delete("/skills/installations/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def uninstall_skill(skill_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.POLICY_MANAGE)
    with open_skill_store(context) as store:
        if not store.delete_installation(context, skill_id):
            raise HTTPException(404, "Skill installation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/skills/resolve", response_model=SkillResolution)
def resolve_skill(payload: SkillResolveRequest, context: TenantContext = Depends(tenant_context)) -> SkillResolution:
    require_permission(context, Permission.TASK_RUN)
    with open_skill_store(context) as store:
        installation = store.get_installation(context, payload.skill_id)
        if installation is None or not installation.enabled:
            raise HTTPException(404, "Enabled skill installation not found")
        version = store.get_version(context, payload.skill_id, installation.version)
    if version is None:
        raise HTTPException(404, "Enabled skill installation not found")
    if _RISK_ORDER[version.risk_level] > _RISK_ORDER[payload.maximum_risk]:
        raise HTTPException(403, "Skill exceeds the execution risk boundary")
    granted = set(installation.approved_tools) & set(payload.available_tools)
    if set(version.required_tools) - granted:
        raise HTTPException(409, "Required tools are not available and approved")
    return SkillResolution(
        installation_id=installation.id, skill_version_id=version.id, skill_id=version.skill_id,
        version=version.version, instructions=version.instructions,
        granted_tools=sorted(granted), risk_level=version.risk_level,
    )
