"""Skill registry: tenant-published versions, workspace installations, details and history.

docs/skills.md. Publishing and rolling back an installation to an earlier version need the
organization administrator role; installing, enabling, disabling and uninstalling are
workspace-owner actions; every member can read. Every change is audited in the same
transaction (``audit_records``), and a skill's history is read back from those records.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .schemas import RiskLevel, TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store

if TYPE_CHECKING:
    from .agent_tools import ToolDefinition


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


class SkillRollbackRequest(BaseModel):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


class SkillToolView(BaseModel):
    """One tool a skill version requires, as this deployment's runtime would treat it."""

    name: str
    registered: bool
    description: str | None = None
    risk_level: RiskLevel | None = None
    # always | policy | rule | blocked | unavailable: when a call needs a human approval.
    approval: str
    approval_text: str
    # The integration host the tool contacts, if any (threat model G2).
    target: str | None = None
    # Granted to agents in this workspace by the installation.
    granted: bool = False


class SkillHistoryEntry(BaseModel):
    id: str
    action: str
    actor: str
    outcome: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillDetail(BaseModel):
    skill_id: str
    name: str
    description: str
    latest_version: str
    # Newest first.
    versions: list[SkillVersion]
    installation: SkillInstallation | None
    # The tools of the installed version, or of the latest one when not installed.
    tools_version: str
    tools: list[SkillToolView]
    history: list[SkillHistoryEntry]
    can_manage: bool
    can_rollback: bool


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
    def record_audit(self, record: AuditRecord) -> AuditRecord: ...
    def skill_history(self, context: TenantContext, skill_id: str, limit: int) -> list[AuditRecord]: ...


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

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        # The same in-memory recorder the audit export reads.
        from .governance import governance_store

        return governance_store.record_audit(record)

    def skill_history(self, context: TenantContext, skill_id: str, limit: int) -> list[AuditRecord]:
        from .governance import governance_store

        target = skill_target(skill_id)
        records = [record for record in governance_store.query_audit(context) if record.target == target]
        records.sort(key=lambda record: (record.created_at, record.id), reverse=True)
        return records[:limit]


skill_store = SkillStore()


def skill_target(skill_id: str) -> str:
    return f"skill:{skill_id}"


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


# The runtime's tool registry, set by the API at startup (anum_api.main). Skill details use
# it to say which required tools exist here, their risk and when they need approval.
_tool_lookup: Callable[[str], ToolDefinition | None] | None = None


def configure_tool_catalog(lookup: Callable[[str], ToolDefinition | None] | None) -> None:
    global _tool_lookup
    _tool_lookup = lookup


def describe_tool(name: str, granted: bool) -> SkillToolView:
    definition = _tool_lookup(name) if _tool_lookup is not None else None
    if definition is None:
        return SkillToolView(
            name=name, registered=False, approval="unavailable", granted=granted,
            approval_text="Not available in this deployment: a skill that requires it cannot run here until the operator adds the tool.",
        )
    risk = definition.risk_level
    roles = f" Only {', '.join(sorted(definition.required_roles))} roles may run it." if definition.required_roles else ""
    if risk == RiskLevel.BLOCKED:
        approval, text = "blocked", "Blocked: the runtime never runs it."
    elif risk == RiskLevel.HIGH:
        approval, text = "always", "Every call waits for a human approval."
    elif risk == RiskLevel.MEDIUM:
        approval, text = "policy", "Needs approval when this workspace's approval policy covers medium risk."
    else:
        approval, text = "rule", "Runs without approval unless an organization approval rule or policy pack asks for one."
    return SkillToolView(
        name=name, registered=True, description=definition.description, risk_level=risk,
        approval=approval, approval_text=text + roles, target=definition.target, granted=granted,
    )


def _audit(
    store: SkillRegistryStore, context: TenantContext, action: str, skill_id: str, metadata: dict[str, Any]
) -> None:
    store.record_audit(
        AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            actor=context.user_id, action=action, target=skill_target(skill_id), outcome="success",
            correlation_id=new_id("correlation"), created_at=utc_now(), metadata=metadata,
        )
    )


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
    # A published version is tenant-owned and installable in every workspace, so publishing
    # needs the organization administrator role; installing stays a workspace-owner action.
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    version = SkillVersion(id=new_id("skill_version"), publisher_tenant_id=context.tenant_id,
                           created_by=context.user_id, created_at=utc_now(),
                           **payload.model_dump())
    try:
        with open_skill_store(context) as store:
            result = store.add_version(version)
            _audit(store, context, "skill.published", version.skill_id, {
                "version": version.version, "risk_level": version.risk_level.value,
                "required_tools": list(version.required_tools),
            })
            return result
    except SkillVersionExistsError as exc:
        raise HTTPException(409, "Skill version already exists") from exc


@router.get("/skills/versions", response_model=list[SkillVersion])
def list_skill_versions(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=500, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(tenant_context),
) -> list[SkillVersion]:
    """Published versions, oldest first; ``q`` matches skill id, name or description."""
    require_permission(context, Permission.POLICY_READ)
    with open_skill_store(context) as store:
        versions = store.list_versions(context)
    needle = (q or "").strip().casefold()
    if needle:
        versions = [
            v for v in versions
            if needle in v.skill_id.casefold() or needle in v.name.casefold() or needle in v.description.casefold()
        ]
    return versions[offset:offset + limit]


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
        previous = store.get_installation(context, payload.skill_id)
        result = store.save_installation(SkillInstallation(
            id=new_id("skill_install"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            skill_version_id=version.id, skill_id=version.skill_id, version=version.version,
            approved_tools=approved, enabled=True, installed_by=context.user_id, installed_at=utc_now(),
        ))
        _audit(store, context, "skill.installed", version.skill_id, {
            "version": version.version, "approved_tools": approved,
            "previous_version": previous.version if previous else None,
        })
        return result


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
        result = store.save_installation(installation.model_copy(update={"enabled": payload.enabled}))
        _audit(store, context, "skill.enabled" if payload.enabled else "skill.disabled", skill_id,
               {"version": installation.version})
        return result


@router.post("/skills/installations/{skill_id}/rollback", response_model=SkillInstallation)
def rollback_installation(
    skill_id: str, payload: SkillRollbackRequest, context: TenantContext = Depends(tenant_context)
) -> SkillInstallation:
    """Move this workspace's installation back to an earlier published version.

    An organization administrator's action: it changes which reviewed instructions agents
    follow. Tool grants never widen: the installation keeps only the grants it already had
    that the earlier version also declares. The enabled switch is kept.
    """
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    with open_skill_store(context) as store:
        installation = store.get_installation(context, skill_id)
        if installation is None:
            raise HTTPException(404, "Skill installation not found")
        target = store.get_version(context, skill_id, payload.version)
        if target is None:
            raise HTTPException(404, "Skill version not found")
        if version_key(target.version) >= version_key(installation.version):
            raise HTTPException(422, "Roll back only to an earlier version; install a newer version from Skills")
        approved = sorted(set(installation.approved_tools) & set(target.required_tools))
        result = store.save_installation(installation.model_copy(update={
            "skill_version_id": target.id, "version": target.version, "approved_tools": approved,
            "installed_by": context.user_id, "installed_at": utc_now(),
        }))
        _audit(store, context, "skill.rolled_back", skill_id, {
            "from_version": installation.version, "to_version": target.version, "approved_tools": approved,
        })
        return result


@router.delete("/skills/installations/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def uninstall_skill(skill_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.POLICY_MANAGE)
    with open_skill_store(context) as store:
        installation = store.get_installation(context, skill_id)
        if installation is None or not store.delete_installation(context, skill_id):
            raise HTTPException(404, "Skill installation not found")
        _audit(store, context, "skill.uninstalled", skill_id, {"version": installation.version})
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


# Registered last so /skills/versions and /skills/installations keep their own routes.
@router.get("/skills/{skill_id}", response_model=SkillDetail)
def skill_detail(
    skill_id: str,
    version: str | None = Query(default=None, pattern=r"^\d+\.\d+\.\d+$"),
    context: TenantContext = Depends(tenant_context),
) -> SkillDetail:
    """Everything a reviewer needs: versions, the installation, tools with risk and approval, history.

    ``tools`` describe ``version`` when given (what installing it would grant), else the
    installed version, else the latest.
    """
    require_permission(context, Permission.POLICY_READ)
    with open_skill_store(context) as store:
        versions = [v for v in store.list_versions(context) if v.skill_id == skill_id]
        installation = store.get_installation(context, skill_id)
        history = store.skill_history(context, skill_id, 50)
    if not versions:
        raise HTTPException(404, "Skill not found")
    versions.sort(key=lambda v: version_key(v.version), reverse=True)
    latest = versions[0]
    wanted = version or (installation.version if installation else None)
    shown = next((v for v in versions if v.version == wanted), None)
    if shown is None:
        if version is not None:
            raise HTTPException(404, "Skill version not found")
        shown = latest
    # Granted only for the installed version's own tools.
    granted = set(installation.approved_tools) if installation and installation.version == shown.version else set()
    return SkillDetail(
        skill_id=skill_id, name=shown.name, description=shown.description,
        latest_version=latest.version, versions=versions, installation=installation,
        tools_version=shown.version,
        tools=[describe_tool(name, name in granted) for name in sorted(set(shown.required_tools))],
        history=[
            SkillHistoryEntry(
                id=record.id, action=record.action, actor=record.actor, outcome=record.outcome,
                created_at=record.created_at, metadata=_plain(record.metadata),
            )
            for record in history
        ],
        can_manage=has_permission(context, Permission.POLICY_MANAGE),
        can_rollback=has_permission(context, Permission.ORG_POLICY_MANAGE),
    )


def _plain(value: Any) -> Any:
    """Audit metadata is a read-only mapping; give the response plain JSON values."""
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v) for v in value]
    return value
