"""Executive 360 command centre backend (master plan F10).

Rules this module enforces:

* A workspace owner grants one person a *summary* of that owner's workspace: any of the
  scopes ``counts``, ``tasks``, ``approvals``, ``cost``, ``meetings``. Only counts, dates
  and totals leave the workspace; never titles, prompts, names, transcripts or documents.
* Creating a grant, or replacing one with a wider one, is a governed owner action: the grant
  starts ``pending`` with a SHA-256 ``payload_hash`` of its canonical content and an owner
  must approve the exact hash that was shown (same contract as ``POST /approvals/{id}/approve``).
  Narrowing (fewer scopes, earlier expiry) and revoking take effect at once.
* ``GET /api/v1/executive/overview`` finds the caller's grants through the narrow NOLOGIN role
  ``anum_executive_reader`` (own active, unexpired grants only), then reads each granted
  workspace in its OWN tenant and workspace context with the application role, re-checking
  the grant row there, and writes an audit record into that workspace before returning data.
  Personal, employer and business workspaces are never pooled: every workspace is its own item,
  workspaces without a grant show ``not_granted``, and the data of one tenant never mixes with
  another (the caller's token names one tenant).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .office_presence import derive_presence, load_evidence, totals
from .office_scope import require_postgres, workspace_session
from .schemas import TenantContext, new_id, utc_now

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/executive", tags=["executive"])

EXECUTIVE_VERSION = 1
SCOPES = ("counts", "tasks", "approvals", "cost", "meetings")
Scope = Literal["counts", "tasks", "approvals", "cost", "meetings"]
READER_ROLE = "anum_executive_reader"
REQUEST_TTL = timedelta(hours=72)
MAX_GRANT_LIFETIME = timedelta(days=366)
MAX_OVERVIEW_WORKSPACES = 50
GrantId = Annotated[str, Path(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
UserId = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[^\s\x00-\x1f]+$")]
PayloadHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GrantCreate(StrictBody):
    grantee_user_id: UserId
    scopes: Annotated[list[Scope], Field(min_length=1, max_length=5)]
    expires_at: datetime | None = None

    @field_validator("scopes")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("scopes must be unique")
        return value


class GrantApprove(StrictBody):
    payload_hash: PayloadHash
    reason: Annotated[str | None, Field(max_length=500)] = None


class GrantReject(StrictBody):
    reason: Annotated[str | None, Field(max_length=500)] = None


class GrantNarrow(StrictBody):
    expected_revision: Annotated[int, Field(ge=1)]
    scopes: Annotated[list[Scope] | None, Field(min_length=1, max_length=5)] = None
    expires_at: datetime | None = None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def grant_payload_hash(*, tenant_id: str, workspace_id: str, grantee_user_id: str,
                       scopes: list[str], expires_at: datetime | None) -> str:
    """SHA-256 of the canonical grant content: the hash an owner is shown and sends back."""
    document = {
        "action": "executive.grant",
        "expires_at": _aware(expires_at).astimezone(timezone.utc).isoformat() if expires_at else None,
        "grantee_user_id": grantee_user_id,
        "scopes": sorted(scopes),
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
    }
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def effective_status(row: Any, now: datetime) -> str:
    if row.status == "active" and row.expires_at is not None and row.expires_at <= now:
        return "expired"
    if row.status == "pending" and row.request_expires_at <= now:
        return "expired"
    return row.status


def _view(row: Any, now: datetime) -> dict[str, Any]:
    return {
        "id": row.id, "workspace_id": row.workspace_id, "grantee_user_id": row.grantee_user_id,
        "scopes": list(row.scopes), "status": effective_status(row, now), "stored_status": row.status,
        "payload_hash": row.payload_hash, "revision": row.revision,
        "requested_by": row.requested_by, "requested_at": row.requested_at.isoformat(),
        "request_expires_at": row.request_expires_at.isoformat(),
        "approved_by": row.approved_by, "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "revoked_by": row.revoked_by, "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "decision_reason": row.decision_reason,
    }


_COLUMNS = (
    "id, tenant_id, workspace_id, grantee_user_id, scopes, status, payload_hash, revision, requested_by, "
    "requested_at, request_expires_at, approved_by, approved_at, expires_at, revoked_by, revoked_at, decision_reason"
)


def _audit(session: Any, context: TenantContext, action: str, target: str, metadata: dict[str, Any],
           *, actor: str | None = None) -> None:
    from .db.repository import SqlAlchemyRepository

    SqlAlchemyRepository(session).record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        actor=actor or context.user_id, action=action, target=target, outcome="success",
        correlation_id=new_id("correlation"), created_at=utc_now(), metadata=metadata,
    ))


def _owner(context: TenantContext) -> None:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_postgres()


def _load(session: Any, context: TenantContext, grant_id: str, *, lock: bool = False) -> Any:
    row = session.execute(text(
        f"select {_COLUMNS} from executive_grants where tenant_id = :t and workspace_id = :w and id = :i"  # nosec B608
        + (" for update" if lock else "")),
        {"t": context.tenant_id, "w": context.workspace_id, "i": grant_id}).first()
    if row is None:
        raise HTTPException(404, "Grant not found")
    return row


@router.post("/grants", status_code=status.HTTP_201_CREATED)
def create_grant(body: GrantCreate, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    """Owner-only. Creates a PENDING grant; it gives no access until an owner approves the hash."""
    _owner(context)
    now = utc_now()
    expires = _aware(body.expires_at)
    if expires is not None and not (now < expires <= now + MAX_GRANT_LIFETIME):
        raise HTTPException(422, "expires_at must be in the future and within one year")
    scopes = sorted(body.scopes)
    digest = grant_payload_hash(tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                                grantee_user_id=body.grantee_user_id, scopes=scopes, expires_at=expires)
    grant_id = new_id("xgrant")
    p = {"t": context.tenant_id, "w": context.workspace_id}
    with workspace_session(context, commit=True) as session:
        live = session.execute(text(
            "select id from executive_grants where tenant_id = :t and workspace_id = :w and grantee_user_id = :g "
            "and status = 'pending' and request_expires_at > :now"), {**p, "g": body.grantee_user_id, "now": now}).first()
        if live:
            raise HTTPException(409, "A request for this person is already waiting for approval")
        session.execute(text(
            "insert into executive_grants (id, tenant_id, workspace_id, grantee_user_id, scopes, status, payload_hash, "
            "requested_by, requested_at, request_expires_at, expires_at, created_at, updated_at) "
            "values (:i, :t, :w, :g, :s, 'pending', :h, :u, :now, :rexp, :exp, :now, :now)"),
            {**p, "i": grant_id, "g": body.grantee_user_id, "s": scopes, "h": digest, "u": context.user_id,
             "now": now, "rexp": now + REQUEST_TTL, "exp": expires})
        _audit(session, context, "executive.grant.request", grant_id,
               {"grantee_user_id": body.grantee_user_id, "scopes": scopes, "expires_at": expires.isoformat() if expires else None,
                "payload_hash": digest})
        row = _load(session, context, grant_id)
        return {**_view(row, now), "approval_required": True}


@router.get("/grants")
def list_grants(
    limit: int = Query(default=50, ge=1, le=100),
    context: TenantContext = Depends(tenant_context),
) -> dict[str, Any]:
    _owner(context)
    now = utc_now()
    with workspace_session(context) as session:
        rows = session.execute(text(
            f"select {_COLUMNS} from executive_grants where tenant_id = :t and workspace_id = :w "  # nosec B608
            "order by created_at desc, id desc limit :n"),
            {"t": context.tenant_id, "w": context.workspace_id, "n": limit}).all()
    return {"version": EXECUTIVE_VERSION, "items": [_view(r, now) for r in rows]}


@router.post("/grants/{grant_id}/approve")
def approve_grant(grant_id: GrantId, body: GrantApprove, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _owner(context)
    now = utc_now()
    with workspace_session(context, commit=True) as session:
        row = _load(session, context, grant_id, lock=True)
        if row.status != "pending":
            raise HTTPException(409, f"Only a pending request can be approved (this one is {effective_status(row, now)})")
        if row.request_expires_at <= now:
            raise HTTPException(409, "This request expired; create a new one")
        recomputed = grant_payload_hash(tenant_id=row.tenant_id, workspace_id=row.workspace_id,
                                        grantee_user_id=row.grantee_user_id, scopes=list(row.scopes), expires_at=row.expires_at)
        if not hmac.compare_digest(recomputed, row.payload_hash) or not hmac.compare_digest(body.payload_hash, row.payload_hash):
            raise HTTPException(409, "The payload hash does not match the request; reload and review it again")
        if row.expires_at is not None and row.expires_at <= now:
            raise HTTPException(409, "The grant would already be expired; create a new request")
        if row.requested_by == context.user_id and _two_person_rule(session, context):
            raise HTTPException(403, "The two-person rule is on: another owner must approve your request")
        session.execute(text(
            "update executive_grants set status = 'superseded', revoked_by = :u, revoked_at = :now, updated_at = :now, "
            "revision = revision + 1, decision_reason = 'replaced by ' || :i "
            "where tenant_id = :t and workspace_id = :w and grantee_user_id = :g and status = 'active'"),
            {"t": row.tenant_id, "w": row.workspace_id, "g": row.grantee_user_id, "u": context.user_id, "now": now, "i": row.id})
        session.execute(text(
            "update executive_grants set status = 'active', approved_by = :u, approved_at = :now, updated_at = :now, "
            "revision = revision + 1, decision_reason = :r where tenant_id = :t and workspace_id = :w and id = :i"),
            {"t": row.tenant_id, "w": row.workspace_id, "i": row.id, "u": context.user_id, "now": now, "r": body.reason})
        _audit(session, context, "executive.grant.approve", row.id,
               {"grantee_user_id": row.grantee_user_id, "scopes": list(row.scopes), "payload_hash": row.payload_hash})
        return _view(_load(session, context, grant_id), now)


def _two_person_rule(session: Any, context: TenantContext) -> bool:
    value = session.execute(text(
        "select two_person_rule from workspace_approval_policies where tenant_id = :t and workspace_id = :w"),
        {"t": context.tenant_id, "w": context.workspace_id}).scalar()
    return bool(value)


@router.post("/grants/{grant_id}/reject")
def reject_grant(grant_id: GrantId, body: GrantReject | None = None, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _owner(context)
    now = utc_now()
    with workspace_session(context, commit=True) as session:
        row = _load(session, context, grant_id, lock=True)
        if row.status != "pending":
            raise HTTPException(409, "Only a pending request can be rejected")
        session.execute(text(
            "update executive_grants set status = 'rejected', revoked_by = :u, revoked_at = :now, updated_at = :now, "
            "revision = revision + 1, decision_reason = :r where tenant_id = :t and workspace_id = :w and id = :i"),
            {"t": row.tenant_id, "w": row.workspace_id, "i": row.id, "u": context.user_id, "now": now, "r": body.reason if body else None})
        _audit(session, context, "executive.grant.reject", row.id, {"grantee_user_id": row.grantee_user_id})
        return _view(_load(session, context, grant_id), now)


@router.patch("/grants/{grant_id}")
def narrow_grant(grant_id: GrantId, body: GrantNarrow, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    """Make an ACTIVE grant smaller at once: fewer scopes and/or an earlier expiry. Widening
    needs a new request (POST /grants) that an owner approves."""
    _owner(context)
    now = utc_now()
    with workspace_session(context, commit=True) as session:
        row = _load(session, context, grant_id, lock=True)
        if row.status != "active" or effective_status(row, now) != "active":
            raise HTTPException(409, "Only an active grant can be narrowed")
        if row.revision != body.expected_revision:
            raise HTTPException(409, "The grant changed; reload and try again")
        scopes = sorted(body.scopes) if body.scopes is not None else sorted(row.scopes)
        if not set(scopes) <= set(row.scopes):
            raise HTTPException(409, "That would widen the grant; create a new request instead")
        expires = _aware(body.expires_at) if body.expires_at is not None else row.expires_at
        if body.expires_at is not None:
            if expires <= now:
                raise HTTPException(422, "expires_at must be in the future (revoke the grant to end it now)")
            if row.expires_at is not None and expires > row.expires_at:
                raise HTTPException(409, "That would widen the grant; create a new request instead")
            if expires > now + MAX_GRANT_LIFETIME:
                raise HTTPException(422, "expires_at must be within one year")
        if scopes == sorted(row.scopes) and expires == row.expires_at:
            raise HTTPException(422, "Nothing to change")
        digest = grant_payload_hash(tenant_id=row.tenant_id, workspace_id=row.workspace_id,
                                    grantee_user_id=row.grantee_user_id, scopes=scopes, expires_at=expires)
        session.execute(text(
            "update executive_grants set scopes = :s, expires_at = :e, payload_hash = :h, updated_at = :now, "
            "revision = revision + 1 where tenant_id = :t and workspace_id = :w and id = :i"),
            {"t": row.tenant_id, "w": row.workspace_id, "i": row.id, "s": scopes, "e": expires, "h": digest, "now": now})
        _audit(session, context, "executive.grant.narrow", row.id,
               {"grantee_user_id": row.grantee_user_id, "from": sorted(row.scopes), "to": scopes})
        return _view(_load(session, context, grant_id), now)


@router.delete("/grants/{grant_id}", status_code=204)
def revoke_grant(grant_id: GrantId, context: TenantContext = Depends(tenant_context)) -> Response:
    _owner(context)
    now = utc_now()
    with workspace_session(context, commit=True) as session:
        row = _load(session, context, grant_id, lock=True)
        if row.status in ("active", "pending"):
            session.execute(text(
                "update executive_grants set status = 'revoked', revoked_by = :u, revoked_at = :now, updated_at = :now, "
                "revision = revision + 1 where tenant_id = :t and workspace_id = :w and id = :i"),
                {"t": row.tenant_id, "w": row.workspace_id, "i": row.id, "u": context.user_id, "now": now})
            _audit(session, context, "executive.grant.revoke", row.id, {"grantee_user_id": row.grantee_user_id})
    return Response(status_code=204)


# ---- overview ------------------------------------------------------------------------


def _granted_rows(context: TenantContext) -> list[Any]:
    """The caller's own active, unexpired grants in the token's tenant, via the narrow reader role."""
    from .db import session as db_session

    session = db_session.SessionLocal()
    try:
        session.execute(text(f"set local role {READER_ROLE}"))
        db_session.set_tenant_context(session, context.tenant_id, None, user_id=context.user_id)
        return session.execute(text(
            "select id, workspace_id, scopes, expires_at from executive_grants "
            "where tenant_id = :t and grantee_user_id = :u and status = 'active' and revoked_at is null "
            "and (expires_at is null or expires_at > now()) order by workspace_id, id limit :n"),
            {"t": context.tenant_id, "u": context.user_id, "n": MAX_OVERVIEW_WORKSPACES + 1}).all()
    finally:
        session.rollback()
        session.close()


def _summary(session: Any, ctx: TenantContext, scopes: list[str], now: datetime) -> dict[str, Any]:
    p = {"t": ctx.tenant_id, "w": ctx.workspace_id}
    out: dict[str, Any] = {}
    presence = None
    if "counts" in scopes or "tasks" in scopes:
        presence = totals(derive_presence(load_evidence(session, ctx, now)))
    if "counts" in scopes:
        c = session.execute(text(
            "select (select count(*) from office_buildings where tenant_id = :t and workspace_id = :w) as buildings, "
            "(select count(*) from office_floors where tenant_id = :t and workspace_id = :w) as floors, "
            "(select count(*) from office_rooms where tenant_id = :t and workspace_id = :w) as rooms, "
            "(select count(*) from office_desks where tenant_id = :t and workspace_id = :w) as desks"), p).one()
        out["counts"] = {"buildings": c.buildings, "floors": c.floors, "rooms": c.rooms, "desks": c.desks,
                         "agents": sum(presence.values()), "agents_by_state": presence,
                         "note": "Agents shared with the whole workspace only."}
    if "tasks" in scopes:
        by_status = {r.status: r.n for r in session.execute(text(
            "select status, count(*) as n from tasks where tenant_id = :t and workspace_id = :w and archived_at is null "
            "group by status"), p)}
        out["tasks"] = {"by_status": by_status, "blocked_agents": presence["blocked"] if presence else 0}
    if "approvals" in scopes:
        a = session.execute(text(
            "select count(*) as n, min(created_at) as oldest from approvals where tenant_id = :t and workspace_id = :w "
            "and status = 'pending' and (expires_at is null or expires_at > :now)"), {**p, "now": now}).one()
        out["approvals"] = {"pending": a.n, "oldest_pending_at": a.oldest.isoformat() if a.oldest else None}
    if "cost" in scopes:
        month = now.date().replace(day=1)
        c = session.execute(text(
            "select coalesce(sum(estimated_cost_usd), 0) as cost, coalesce(sum(calls), 0) as calls, "
            "coalesce(sum(unpriced_calls), 0) as unpriced from agent_usage_monthly "
            "where tenant_id = :t and workspace_id = :w and month = :m"), {**p, "m": month}).one()
        out["cost"] = {"month": month.strftime("%Y-%m"), "estimated_cost_usd": format(c.cost, "f"),
                       "calls": int(c.calls), "unpriced_calls": int(c.unpriced), "currency": "USD"}
    if "meetings" in scopes:
        by = {r.status: r.n for r in session.execute(text(
            "select status, count(*) as n from agent_meeting_sessions where tenant_id = :t and workspace_id = :w "
            "and status in ('scheduled', 'active', 'paused') group by status"), p)}
        out["meetings"] = {"active": by.get("active", 0), "scheduled": by.get("scheduled", 0), "paused": by.get("paused", 0)}
    return out


def _read_granted_workspace(context: TenantContext, grant: Any, now: datetime) -> dict[str, Any]:
    ctx = TenantContext(tenant_id=context.tenant_id, workspace_id=grant.workspace_id, user_id=context.user_id, roles=[])
    with workspace_session(ctx, commit=True) as session:
        p = {"t": ctx.tenant_id, "w": ctx.workspace_id}
        # Re-check the grant inside the workspace's own RLS context.
        row = session.execute(text(
            "select id, scopes from executive_grants where tenant_id = :t and workspace_id = :w and id = :i "
            "and grantee_user_id = :u and status = 'active' and revoked_at is null "
            "and (expires_at is null or expires_at > :now)"), {**p, "i": grant.id, "u": ctx.user_id, "now": now}).first()
        if row is None:
            return {"workspace_id": ctx.workspace_id, "access": "not_granted", "reason": "grant_ended"}
        name = session.execute(text("select name from workspaces where tenant_id = :t and id = :w"), p).scalar()
        scopes = [s for s in SCOPES if s in set(row.scopes)]
        summary = _summary(session, ctx, scopes, now)
        # Fail closed: no audit record, no data.
        _audit(session, ctx, "executive.overview.read", ctx.workspace_id,
               {"grant_id": row.id, "scopes": scopes, "home_workspace_id": context.workspace_id})
        return {"workspace_id": ctx.workspace_id, "name": name or ctx.workspace_id, "access": "granted",
                "grant_id": row.id, "scopes": scopes, "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
                "summary": summary, "as_of": now.isoformat()}


@router.get("/overview")
def overview(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    """Granted summaries only. Needs an active membership in the request's workspace; the
    granted workspaces themselves need no membership."""
    require_postgres()
    from .membership_directory import list_caller_memberships

    now = utc_now()
    grants = _granted_rows(context)
    truncated = len(grants) > MAX_OVERVIEW_WORKSPACES
    grants = grants[:MAX_OVERVIEW_WORKSPACES]
    granted_ids = {g.workspace_id for g in grants}
    items: list[dict[str, Any]] = []
    for grant in grants:
        try:
            items.append(_read_granted_workspace(context, grant, now))
        except Exception:
            logger.exception("executive_overview_workspace_failed")
            items.append({"workspace_id": grant.workspace_id, "access": "unavailable",
                          "reason": "read_failed_or_audit_failed"})
    for member in list_caller_memberships(context):
        if member.workspace_id not in granted_ids:
            items.append({"workspace_id": member.workspace_id, "name": member.workspace_name or member.workspace_id,
                          "access": "not_granted", "reason": "no_grant", "open_with": "workspace_switch"})
    items.sort(key=lambda i: (i["access"] != "granted", i["workspace_id"]))
    return {"version": EXECUTIVE_VERSION, "tenant_id": context.tenant_id,
            "home_workspace_id": context.workspace_id, "generated_at": now.isoformat(),
            "truncated": truncated, "workspaces": items}
