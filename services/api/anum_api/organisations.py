"""Customer organisations (docs/customer-organisations.md).

* **Branding**: an organisation's display name and logo, tenant-wide (``tenant_branding``).
  Every member reads it; the organisation administrator (``org_admin``) changes it.
* **My organisations**: the tenants where the caller has an active membership, for the
  tenant switcher. With PostgreSQL it is read through ``anum_tenant_directory`` (migration
  ``0039_team_work``), a NOLOGIN role whose policies admit only the caller's own
  memberships and those tenants' names; with OIDC the list is limited to the
  organisations the token allows (``tenant_id`` plus ``tenant_ids``).
* **Platform console**: the platform operator (realm role ``platform_operator``)
  onboards a customer company in one step (tenant, first workspace, branding name, plan
  placeholder and a single-use owner invitation for the first admin) and lists the
  organisations they run with counts and health only. Those counts are read through
  ``anum_platform_reader``, which can see ids, statuses and timestamps of the operator's
  organisations and nothing else: never titles, prompts, names, bodies or files.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import RLock
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .audit import AuditRecord
from .authorization import Permission, Role
from .dependencies import (
    AuthenticatedIdentity,
    directory_identity,
    has_permission,
    memory_repository,
    require_permission,
    tenant_context,
)
from .events import CanonicalEventName, create_event
from .schemas import InvitationStatus, Tenant, TenantContext, Workspace, WorkspaceInvitation, new_id, utc_now
from .settings import settings
from .team_models import OPEN_STATUSES, clean_email
from .team_store import memory_team_store

router = APIRouter(prefix="/api/v1", tags=["organisations"])

LOGO_MAX_BYTES = 256 * 1024
LOGO_TYPES = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF"}
PLANS = ("trial", "standard", "enterprise")
INVITATION_TTL_HOURS = 168
ACTIVE_DAYS = 14

# ----------------------------------------------------------------------------- models


class Branding(BaseModel):
    tenant_id: str
    display_name: str
    has_logo: bool = False
    logo_content_type: str | None = None
    updated_at: datetime | None = None


class BrandingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=160)

    @field_validator("display_name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("display_name must not be blank")
        return value


class OrganisationWorkspace(BaseModel):
    workspace_id: str
    workspace_name: str | None = None
    role: str


class MyOrganisation(BaseModel):
    tenant_id: str
    name: str | None = None
    display_name: str | None = None
    current: bool = False
    workspaces: list[OrganisationWorkspace]


class OrganisationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,79}$")
    display_name: str = Field(min_length=1, max_length=160)
    workspace_name: str = Field(default="Main", min_length=1, max_length=160)
    first_admin_email: str = Field(min_length=3, max_length=254)
    plan: str = Field(default="trial", pattern="^(trial|standard|enterprise)$")

    @field_validator("first_admin_email")
    @classmethod
    def _email(cls, value: str) -> str:
        cleaned = clean_email(value)
        if cleaned is None:
            raise ValueError("first_admin_email is required")
        return cleaned

    @field_validator("display_name", "workspace_name")
    @classmethod
    def _names(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("must not be blank")
        return value


class OrganisationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    plan: str | None = Field(default=None, pattern="^(trial|standard|enterprise)$")


class OrganisationSummary(BaseModel):
    """What the platform console shows: counts and health, never the organisation's data."""

    tenant_id: str
    display_name: str
    plan: str
    first_workspace_id: str
    created_at: datetime
    workspaces: int = 0
    active_members: int = 0
    pending_invitations: int = 0
    open_agent_tasks: int = 0
    open_work_items: int = 0
    overdue_work_items: int = 0
    last_activity_at: datetime | None = None
    # setting_up: nobody joined yet; active: activity in the last 14 days; idle: none;
    # attention: overdue work, or the owner invitation lapsed with nobody joined.
    health: str


class CreatedOrganisation(BaseModel):
    organisation: OrganisationSummary
    invitation_id: str
    # Single-use owner invitation token for the first admin, shown once.
    invitation_token: str
    invitation_expires_at: datetime
    workspace_id: str


class ReissuedInvitation(BaseModel):
    invitation_id: str
    invitation_token: str
    invitation_expires_at: datetime
    workspace_id: str


# ----------------------------------------------------------------------------- memory stores


class _MemoryBranding:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.platform: dict[str, dict[str, Any]] = {}
        self.lock = RLock()

    def clear(self) -> None:
        with self.lock:
            self.items.clear()
            self.platform.clear()


memory_branding = _MemoryBranding()


@contextmanager
def _session(context: TenantContext) -> Iterator[Any]:
    """A PostgreSQL session in ``context``'s tenant (and workspace) RLS scope, or None in memory."""
    if settings.repository_backend == "memory":
        yield None
        return
    from .db import session as db_session

    session = db_session.SessionLocal()
    try:
        db_session.set_tenant_context(session, context.tenant_id, context.workspace_id or None, user_id=context.user_id)
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def _read_branding(session: Any, tenant_id: str) -> dict[str, Any] | None:
    if session is None:
        return memory_branding.items.get(tenant_id)
    from sqlalchemy import select

    from .db.team_repository import tenant_branding

    row = session.execute(select(tenant_branding).where(tenant_branding.c.tenant_id == tenant_id)).first()
    return dict(row._mapping) if row else None


def _write_branding(session: Any, tenant_id: str, values: dict[str, Any]) -> None:
    if session is None:
        with memory_branding.lock:
            current = memory_branding.items.get(tenant_id, {"tenant_id": tenant_id, "logo": None, "logo_content_type": None})
            memory_branding.items[tenant_id] = {**current, **values}
        return
    from sqlalchemy import insert, update

    from .db.team_repository import tenant_branding

    if _read_branding(session, tenant_id) is None:
        session.execute(insert(tenant_branding).values(tenant_id=tenant_id, **{"logo": None, "logo_content_type": None, **values}))
    else:
        session.execute(update(tenant_branding).where(tenant_branding.c.tenant_id == tenant_id).values(**values))


def _tenant_name(session: Any, tenant_id: str) -> str | None:
    if session is None:
        tenant = memory_repository.store.tenants.get(tenant_id)
        return tenant.name if tenant else None
    from sqlalchemy import select

    from .db.models import Tenant as TenantRecord

    return session.scalar(select(TenantRecord.name).where(TenantRecord.id == tenant_id))


def _branding_view(tenant_id: str, row: dict[str, Any] | None, fallback: str | None) -> Branding:
    if row is None:
        return Branding(tenant_id=tenant_id, display_name=fallback or tenant_id)
    return Branding(
        tenant_id=tenant_id, display_name=row["display_name"], has_logo=row.get("logo") is not None,
        logo_content_type=row.get("logo_content_type"), updated_at=row.get("updated_at"),
    )


def _audit(context: TenantContext, repository: Any, action: str, target: str, metadata: dict[str, Any]) -> None:
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action=action, target=target, outcome="success", correlation_id=new_id("corr"), created_at=utc_now(),
            metadata=metadata,
        )
    )


# ----------------------------------------------------------------------------- branding


@router.get("/organization/branding", response_model=Branding)
def get_branding(context: TenantContext = Depends(tenant_context)) -> Branding:
    """This organisation's name and whether it has a logo; every member may read it."""
    require_permission(context, Permission.ORGANIZATION_READ)
    with _session(context) as session:
        return _branding_view(context.tenant_id, _read_branding(session, context.tenant_id), _tenant_name(session, context.tenant_id))


def _require_branding_admin(context: TenantContext) -> None:
    if not has_permission(context, Permission.ORG_POLICY_MANAGE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the organisation administrator (realm role org_admin) can change the organisation's name and logo.",
        )


@router.put("/organization/branding", response_model=Branding)
def set_branding(payload: BrandingUpdate, context: TenantContext = Depends(tenant_context)) -> Branding:
    _require_branding_admin(context)
    from .team_work import open_team_unit

    with open_team_unit(context, require_membership=True) as unit:
        session = unit.session
        if session is not None:
            from .db.scope import require_tenant

            require_tenant(session, context.tenant_id)
        _write_branding(session, context.tenant_id, {"display_name": payload.display_name, "updated_by_user_id": context.user_id, "updated_at": utc_now()})
        _audit(context, unit.repository, "organization.branding.update", context.tenant_id, {"display_name": payload.display_name})
        return _branding_view(context.tenant_id, _read_branding(session, context.tenant_id), None)


def _sniff(content: bytes, content_type: str) -> bool:
    magic = LOGO_TYPES.get(content_type)
    if magic is None or not content.startswith(magic):
        return False
    return content_type != "image/webp" or content[8:12] == b"WEBP"


@router.put("/organization/branding/logo", response_model=Branding)
async def set_logo(request: Request, context: TenantContext = Depends(tenant_context)) -> Branding:
    """Upload the logo as the raw request body (PNG, JPEG or WebP, at most 256 KiB)."""
    _require_branding_admin(context)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    content = await request.body()
    if not content or len(content) > LOGO_MAX_BYTES:
        raise HTTPException(status_code=413 if content else 422, detail="The logo must be 1 byte to 256 KiB")
    if content_type not in LOGO_TYPES or not _sniff(content, content_type):
        raise HTTPException(status_code=415, detail="The logo must be a PNG, JPEG or WebP image")
    from .team_work import open_team_unit

    with open_team_unit(context, require_membership=True) as unit:
        session = unit.session
        current = _read_branding(session, context.tenant_id)
        name = current["display_name"] if current else (_tenant_name(session, context.tenant_id) or context.tenant_id)
        _write_branding(session, context.tenant_id, {
            "display_name": name, "logo": content, "logo_content_type": content_type,
            "updated_by_user_id": context.user_id, "updated_at": utc_now(),
        })
        _audit(context, unit.repository, "organization.logo.update", context.tenant_id, {"bytes": len(content), "content_type": content_type})
        return _branding_view(context.tenant_id, _read_branding(session, context.tenant_id), None)


@router.delete("/organization/branding/logo", response_model=Branding)
def delete_logo(context: TenantContext = Depends(tenant_context)) -> Branding:
    _require_branding_admin(context)
    from .team_work import open_team_unit

    with open_team_unit(context, require_membership=True) as unit:
        session = unit.session
        if _read_branding(session, context.tenant_id) is not None:
            _write_branding(session, context.tenant_id, {"logo": None, "logo_content_type": None, "updated_by_user_id": context.user_id, "updated_at": utc_now()})
            _audit(context, unit.repository, "organization.logo.delete", context.tenant_id, {})
        return _branding_view(context.tenant_id, _read_branding(session, context.tenant_id), _tenant_name(session, context.tenant_id))


@router.get("/organization/branding/logo")
def get_logo(context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.ORGANIZATION_READ)
    with _session(context) as session:
        row = _read_branding(session, context.tenant_id)
    if row is None or row.get("logo") is None:
        raise HTTPException(status_code=404, detail="This organisation has no logo")
    return Response(
        content=bytes(row["logo"]), media_type=row["logo_content_type"],
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"},
    )


# ----------------------------------------------------------------------------- my organisations


def _my_organisations_sql(user_id: str) -> list[tuple[str, str | None, str | None, str, str | None, str]]:
    from sqlalchemy import text

    from .db import session as db_session

    session = db_session.SessionLocal()
    try:
        session.execute(text("set local role anum_tenant_directory"))
        session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": user_id})
        rows = session.execute(
            text(
                "select m.tenant_id, t.name as tenant_name, b.display_name, m.workspace_id, w.name as workspace_name, m.role "
                "from workspace_memberships m "
                "left join tenants t on t.id = m.tenant_id "
                "left join tenant_branding b on b.tenant_id = m.tenant_id "
                "left join workspaces w on w.tenant_id = m.tenant_id and w.id = m.workspace_id "
                "where m.user_id = :user_id and m.active "
                "order by m.tenant_id, lower(coalesce(w.name, '')), m.workspace_id"
            ),
            {"user_id": user_id},
        ).all()
        return [(r.tenant_id, r.tenant_name, r.display_name, r.workspace_id, r.workspace_name, r.role) for r in rows]
    finally:
        session.rollback()
        session.close()


def _my_organisations_memory(user_id: str) -> list[tuple[str, str | None, str | None, str, str | None, str]]:
    store = memory_repository.store
    out = []
    for (tenant_id, workspace_id, member), membership in store.memberships.items():
        if member != user_id or not membership.active:
            continue
        tenant = store.tenants.get(tenant_id)
        workspace = store.workspaces.get(workspace_id)
        branding = memory_branding.items.get(tenant_id)
        out.append((
            tenant_id, tenant.name if tenant else None, branding["display_name"] if branding else None, workspace_id,
            workspace.name if workspace and workspace.tenant_id == tenant_id else None, membership.role,
        ))
    return sorted(out, key=lambda r: (r[0], (r[4] or "").casefold(), r[3]))


@router.get("/me/organizations", response_model=list[MyOrganisation])
def my_organisations(identity: AuthenticatedIdentity = Depends(directory_identity)) -> list[MyOrganisation]:
    """Every organisation where the caller has an active membership, with those workspaces.

    Open to every authenticated caller: it reveals only the caller's own memberships and
    those organisations' names. With OIDC only organisations the token allows are listed,
    because only those can be switched to.
    """
    context = identity.context
    rows = _my_organisations_sql(context.user_id) if settings.repository_backend == "postgresql" else _my_organisations_memory(context.user_id)
    allowed = identity.allowed_tenant_ids
    grouped: dict[str, MyOrganisation] = {}
    for tenant_id, tenant_name, display_name, workspace_id, workspace_name, role in rows:
        if allowed is not None and tenant_id not in allowed:
            continue
        org = grouped.setdefault(
            tenant_id,
            MyOrganisation(tenant_id=tenant_id, name=tenant_name, display_name=display_name, current=tenant_id == context.tenant_id, workspaces=[]),
        )
        org.workspaces.append(OrganisationWorkspace(workspace_id=workspace_id, workspace_name=workspace_name, role=role))
    return sorted(grouped.values(), key=lambda o: (not o.current, (o.display_name or o.name or o.tenant_id).casefold()))


# ----------------------------------------------------------------------------- platform console


def _require_operator(context: TenantContext) -> None:
    require_permission(context, Permission.PLATFORM_OPERATE)


def _health(summary: OrganisationSummary, now: datetime, invitation_lapsed: bool) -> str:
    if summary.active_members == 0:
        return "attention" if invitation_lapsed else "setting_up"
    if summary.overdue_work_items:
        return "attention"
    if summary.last_activity_at and summary.last_activity_at >= now - timedelta(days=ACTIVE_DAYS):
        return "active"
    return "idle"


def _summaries_sql(operator: str) -> list[dict[str, Any]]:
    from sqlalchemy import text

    from .db import session as db_session

    session = db_session.SessionLocal()
    try:
        session.execute(text("set local role anum_platform_reader"))
        session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": operator})
        # Correlated counts; every table admits only the operator's organisations and
        # only id, status and timestamp columns (migration 0039_team_work).
        rows = session.execute(
            text(
                """
                select p.tenant_id, p.display_name, p.plan, p.first_workspace_id, p.created_at,
                  (select count(*) from workspaces w where w.tenant_id = p.tenant_id) as workspaces,
                  (select count(*) from workspace_memberships m where m.tenant_id = p.tenant_id and m.active) as active_members,
                  (select count(*) from workspace_invitations i where i.tenant_id = p.tenant_id
                     and i.status = 'pending' and i.expires_at > now()) as pending_invitations,
                  (select count(*) from workspace_invitations i where i.tenant_id = p.tenant_id
                     and i.status = 'pending' and i.expires_at <= now()) as lapsed_invitations,
                  (select count(*) from tasks t where t.tenant_id = p.tenant_id
                     and t.status in ('created', 'queued', 'running', 'waiting_approval')) as open_agent_tasks,
                  (select count(*) from work_items x where x.tenant_id = p.tenant_id
                     and x.status in ('todo', 'in_progress', 'blocked')) as open_work_items,
                  (select count(*) from work_items x where x.tenant_id = p.tenant_id
                     and x.status in ('todo', 'in_progress', 'blocked') and x.due_at < now()) as overdue_work_items,
                  greatest(
                    (select max(t.updated_at) from tasks t where t.tenant_id = p.tenant_id),
                    (select max(x.updated_at) from work_items x where x.tenant_id = p.tenant_id)
                  ) as last_activity_at
                from platform_organizations p
                where p.operator_user_id = :operator
                order by lower(p.display_name), p.tenant_id
                """
            ),
            {"operator": operator},
        ).all()
        return [dict(r._mapping) for r in rows]
    finally:
        session.rollback()
        session.close()


def _summaries_memory(operator: str, now: datetime) -> list[dict[str, Any]]:
    store = memory_repository.store
    out = []
    for tenant_id, org in memory_branding.platform.items():
        if org["operator_user_id"] != operator:
            continue
        invitations = [i for i in store.invitations.values() if i.tenant_id == tenant_id and i.status == "pending"]
        items = memory_team_store.items_for_tenant(tenant_id)
        tasks = [t for t in store.tasks.values() if t.tenant_id == tenant_id]
        open_items = [i for i in items if i.status in OPEN_STATUSES]
        stamps = [t.updated_at for t in tasks] + [i.updated_at for i in items]
        out.append({
            **{k: org[k] for k in ("tenant_id", "display_name", "plan", "first_workspace_id", "created_at")},
            "workspaces": sum(1 for w in store.workspaces.values() if w.tenant_id == tenant_id),
            "active_members": sum(1 for (t, _, _), m in store.memberships.items() if t == tenant_id and m.active),
            "pending_invitations": sum(1 for i in invitations if i.expires_at > now),
            "lapsed_invitations": sum(1 for i in invitations if i.expires_at <= now),
            "open_agent_tasks": sum(1 for t in tasks if t.status.value in {"created", "queued", "running", "waiting_approval"}),
            "open_work_items": len(open_items),
            "overdue_work_items": sum(1 for i in open_items if i.due_at is not None and i.due_at < now),
            "last_activity_at": max(stamps) if stamps else None,
        })
    return sorted(out, key=lambda r: (r["display_name"].casefold(), r["tenant_id"]))


def _summaries(operator: str) -> list[OrganisationSummary]:
    now = utc_now()
    rows = _summaries_sql(operator) if settings.repository_backend == "postgresql" else _summaries_memory(operator, now)
    out = []
    for row in rows:
        lapsed = int(row.pop("lapsed_invitations") or 0)
        summary = OrganisationSummary(**{**row, "health": "setting_up"})
        summary.health = _health(summary, now, lapsed > 0 and summary.pending_invitations == 0)
        out.append(summary)
    return out


@router.get("/platform/organizations", response_model=list[OrganisationSummary])
def list_platform_organisations(context: TenantContext = Depends(tenant_context)) -> list[OrganisationSummary]:
    """The organisations this platform operator runs, with counts and health only."""
    _require_operator(context)
    return _summaries(context.user_id)


def _owner_invitation(repository: Any, target: TenantContext, email: str, now: datetime) -> tuple[WorkspaceInvitation, str]:
    from .workspace_members import hash_invitation_token, new_invitation_token

    token = new_invitation_token()
    invitation = repository.save_invitation(
        WorkspaceInvitation(
            id=new_id("invitation"), tenant_id=target.tenant_id, workspace_id=target.workspace_id, role=Role.OWNER.value,
            invitee_email=email, token_hash=hash_invitation_token(token), created_by_user_id=target.user_id,
            expires_at=now + timedelta(hours=INVITATION_TTL_HOURS), created_at=now, updated_at=now,
        )
    )
    repository.record_event(
        create_event(
            CanonicalEventName.WORKSPACE_INVITATION_CREATED, target, invitation.id,
            {"invitation_id": invitation.id, "role": invitation.role, "invitee_user_id": None, "email_bound": True,
             "expires_at": invitation.expires_at.isoformat()},
            created_at=now,
        ).event
    )
    _audit(target, repository, "workspace_invitation.create", invitation.id, {"role": invitation.role, "invitee_email": email, "via": "platform"})
    return invitation, token


def _operated(session: Any, tenant_id: str) -> dict[str, Any] | None:
    if session is None:
        return memory_branding.platform.get(tenant_id)
    from sqlalchemy import select

    from .db.team_repository import platform_organizations

    row = session.execute(select(platform_organizations).where(platform_organizations.c.tenant_id == tenant_id)).first()
    return dict(row._mapping) if row else None


@router.post("/platform/organizations", response_model=CreatedOrganisation, status_code=status.HTTP_201_CREATED)
def create_platform_organisation(payload: OrganisationCreate, context: TenantContext = Depends(tenant_context)) -> CreatedOrganisation:
    """Onboard a customer company: tenant, first workspace, branding, plan and owner invitation.

    Everything is written inside the *new* tenant's RLS context, in one transaction. The
    operator never becomes a member: the first admin accepts the invitation (token shown
    once) and becomes the workspace owner. The operator's own workspace gets an audit
    record of the onboarding.
    """
    _require_operator(context)
    from .dependencies import _unit_of_work

    now = utc_now()
    workspace_id = f"{payload.tenant_id}_main"[:80]
    target = TenantContext(tenant_id=payload.tenant_id, workspace_id=workspace_id, user_id=context.user_id, roles=[])
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"The organisation id {payload.tenant_id} is already taken; choose another.",
    )
    try:
        with _unit_of_work(target, require_membership=False) as repository:
            session = getattr(repository, "session", None)
            try:
                if session is not None:
                    with session.begin_nested():
                        repository.create_tenant(Tenant(id=payload.tenant_id, name=payload.display_name, created_at=now, updated_at=now))
                        repository.create_workspace(Workspace(id=workspace_id, tenant_id=payload.tenant_id, name=payload.workspace_name, created_at=now, updated_at=now))
                else:
                    if any(w == workspace_id for w in memory_repository.store.workspaces):
                        raise conflict
                    repository.create_tenant(Tenant(id=payload.tenant_id, name=payload.display_name, created_at=now, updated_at=now))
                    repository.create_workspace(Workspace(id=workspace_id, tenant_id=payload.tenant_id, name=payload.workspace_name, created_at=now, updated_at=now))
            except ValueError as exc:
                raise conflict from exc
            _write_branding(session, payload.tenant_id, {"display_name": payload.display_name, "updated_by_user_id": context.user_id, "updated_at": now})
            record = {
                "tenant_id": payload.tenant_id, "operator_user_id": context.user_id, "display_name": payload.display_name,
                "plan": payload.plan, "first_workspace_id": workspace_id, "first_admin_email": payload.first_admin_email,
                "created_at": now, "updated_at": now,
            }
            if session is None:
                memory_branding.platform[payload.tenant_id] = record
            else:
                from sqlalchemy import insert

                from .db.team_repository import platform_organizations

                session.execute(insert(platform_organizations).values(**record))
            invitation, token = _owner_invitation(repository, target, payload.first_admin_email, now)
            _audit(target, repository, "platform.organization.create", payload.tenant_id,
                   {"operator": context.user_id, "plan": payload.plan, "display_name": payload.display_name})
    except HTTPException:
        raise
    except Exception as exc:
        from sqlalchemy.exc import IntegrityError

        if isinstance(exc, IntegrityError):
            raise conflict from exc
        raise
    # The operator's own record of what they did (their tenant and workspace).
    with _unit_of_work(context, require_membership=False) as own:
        _audit(context, own, "platform.organization.create", payload.tenant_id,
               {"plan": payload.plan, "first_workspace_id": workspace_id})
    summary = next((s for s in _summaries(context.user_id) if s.tenant_id == payload.tenant_id), None)
    if summary is None:
        raise HTTPException(status_code=500, detail="The organisation was created but could not be read back")
    return CreatedOrganisation(
        organisation=summary, invitation_id=invitation.id, invitation_token=token,
        invitation_expires_at=invitation.expires_at, workspace_id=workspace_id,
    )


def _operated_or_404(session: Any, tenant_id: str, operator: str) -> dict[str, Any]:
    record = _operated(session, tenant_id)
    if record is None or record["operator_user_id"] != operator:
        raise HTTPException(status_code=404, detail="You do not run an organisation with that id")
    return record


@router.patch("/platform/organizations/{tenant_id}", response_model=OrganisationSummary)
def update_platform_organisation(tenant_id: str, payload: OrganisationUpdate, context: TenantContext = Depends(tenant_context)) -> OrganisationSummary:
    """Change the plan placeholder or the console's display name (not the organisation's data)."""
    _require_operator(context)
    target = TenantContext(tenant_id=tenant_id, workspace_id="", user_id=context.user_id, roles=[])
    with _session(target) as session:
        record = _operated_or_404(session, tenant_id, context.user_id)
        changes = {k: v for k, v in payload.model_dump().items() if v is not None}
        if changes:
            changes["updated_at"] = utc_now()
            if session is None:
                record.update(changes)
            else:
                from sqlalchemy import update

                from .db.team_repository import platform_organizations

                session.execute(update(platform_organizations).where(platform_organizations.c.tenant_id == tenant_id).values(**changes))
    if changes:
        with _unit_of_work_for(context) as own:
            _audit(context, own, "platform.organization.update", tenant_id, {k: v for k, v in changes.items() if k != "updated_at"})
    summary = next((s for s in _summaries(context.user_id) if s.tenant_id == tenant_id), None)
    if summary is None:
        raise HTTPException(status_code=404, detail="You do not run an organisation with that id")
    return summary


@contextmanager
def _unit_of_work_for(context: TenantContext) -> Iterator[Any]:
    from .dependencies import _unit_of_work

    with _unit_of_work(context, require_membership=False) as repository:
        yield repository


@router.post("/platform/organizations/{tenant_id}/invitation", response_model=ReissuedInvitation, status_code=201)
def reissue_owner_invitation(tenant_id: str, context: TenantContext = Depends(tenant_context)) -> ReissuedInvitation:
    """A new single-use owner invitation for the first admin, while nobody has joined yet."""
    _require_operator(context)
    probe = TenantContext(tenant_id=tenant_id, workspace_id="", user_id=context.user_id, roles=[])
    with _session(probe) as session:
        record = _operated_or_404(session, tenant_id, context.user_id)
    target = TenantContext(tenant_id=tenant_id, workspace_id=record["first_workspace_id"], user_id=context.user_id, roles=[])
    now = utc_now()
    with _unit_of_work_for(target) as repository:
        if any(m.active for m in repository.list_memberships(target)):
            raise HTTPException(
                status_code=409,
                detail="Someone already joined this organisation; its owners invite further people themselves.",
            )
        for pending in repository.list_invitations(target):
            if pending.status == "pending" and pending.role == Role.OWNER.value:
                repository.save_invitation(
                    pending.model_copy(update={"status": InvitationStatus.REVOKED, "revoked_at": now, "updated_at": now})
                )
        invitation, token = _owner_invitation(repository, target, record["first_admin_email"], now)
    with _unit_of_work_for(context) as own:
        _audit(context, own, "platform.organization.reinvite", tenant_id, {"invitation_id": invitation.id})
    return ReissuedInvitation(
        invitation_id=invitation.id, invitation_token=token, invitation_expires_at=invitation.expires_at,
        workspace_id=record["first_workspace_id"],
    )
