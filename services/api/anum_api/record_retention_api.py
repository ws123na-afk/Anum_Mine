"""How long a workspace keeps meeting transcripts and visitor records (owner decision O5, docs/record-retention.md).

* ``GET /api/v1/workspace/record-retention``: the periods in force, the standard ones, the allowed range and
  ``can_manage``. ``task:read``: every member may know how long records about them are kept.
* ``PUT /api/v1/workspace/record-retention``: an owner sets either period (``null`` returns it to the standard).
  ``workspace:manage``, ``input_mode: "click"`` (a typed or spoken answer never shortens retention and no agent tool
  calls this route) and the ``expected_revision`` the owner was shown (0 while the standard periods apply).
  One audit record with the old and new periods. The daily retention job applies the new periods on its next run.

PostgreSQL only: the memory backend answers 503, never a made-up setting.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .office_scope import require_postgres, workspace_session
from .record_retention import (
    MAX_DAYS,
    TRANSCRIPT_DEFAULT_DAYS,
    TRANSCRIPT_MIN_DAYS,
    VISITOR_DEFAULT_DAYS,
    VISITOR_MIN_DAYS,
    RetentionSetting,
)
from .schemas import TenantContext, new_id, utc_now

router = APIRouter(prefix="/api/v1/workspace/record-retention", tags=["record retention"])

CLICK_ONLY = "Confirm this on screen with a click. A spoken or typed answer cannot change how long records are kept."
STALE = "The retention setting changed; reload and try again"
# Plain literals so the Arabic catalog can match them (tests/test_record_retention.py checks the numbers).
BAD_TRANSCRIPT = "Meeting transcripts can be kept for 7 to 1825 days"
BAD_VISITOR = "Visitor records can be kept for 30 to 1825 days"

_READ = text(
    "select transcript_days, visitor_days, revision, updated_by, updated_at from workspace_record_retention "
    "where tenant_id = :t and workspace_id = :w"
)


class RetentionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Whole days; null returns the period to the standard one. The range is checked below with a clear message.
    transcript_days: int | None = Field(default=None, ge=-100000, le=100000)
    visitor_days: int | None = Field(default=None, ge=-100000, le=100000)
    expected_revision: int = Field(ge=0)
    input_mode: Literal["click", "typed", "voice"] = "typed"


def _period(days: int | None, default: int, minimum: int) -> dict[str, Any]:
    return {"days": default if days is None else days, "is_default": days is None, "default_days": default,
            "min_days": minimum, "max_days": MAX_DAYS}


def _view(row: Any, context: TenantContext) -> dict[str, Any]:
    setting = RetentionSetting() if row is None else RetentionSetting(row.transcript_days, row.visitor_days)
    return {
        "transcripts": _period(setting.transcript_days, TRANSCRIPT_DEFAULT_DAYS, TRANSCRIPT_MIN_DAYS),
        "visitors": _period(setting.visitor_days, VISITOR_DEFAULT_DAYS, VISITOR_MIN_DAYS),
        "revision": 0 if row is None else row.revision,
        "updated_by": None if row is None else row.updated_by,
        "updated_at": None if row is None else row.updated_at.isoformat(),
        # Owner decision O8: no time-indexed office snapshot is stored, so none is kept or purged.
        "snapshots_stored": False,
        "can_manage": has_permission(context, Permission.WORKSPACE_MANAGE),
    }


def _scope(context: TenantContext) -> dict[str, str]:
    return {"t": context.tenant_id, "w": context.workspace_id}


@router.get("")
def get_retention(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    with workspace_session(context) as session:
        return _view(session.execute(_READ, _scope(context)).one_or_none(), context)


@router.put("")
def update_retention(body: RetentionUpdate, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    if body.input_mode != "click":
        raise HTTPException(status.HTTP_403_FORBIDDEN, CLICK_ONLY)
    if body.transcript_days is not None and not TRANSCRIPT_MIN_DAYS <= body.transcript_days <= MAX_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, BAD_TRANSCRIPT)
    if body.visitor_days is not None and not VISITOR_MIN_DAYS <= body.visitor_days <= MAX_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, BAD_VISITOR)
    require_postgres()
    with workspace_session(context, commit=True) as session:
        before = session.execute(text(f"{_READ.text} for update"), _scope(context)).one_or_none()  # nosec B608: fixed text
        current = 0 if before is None else before.revision
        if body.expected_revision != current:
            raise HTTPException(status.HTTP_409_CONFLICT, STALE)
        params = {**_scope(context), "td": body.transcript_days, "vd": body.visitor_days, "by": context.user_id}
        if before is None:
            # Two owners saving at once: the second insert finds the row and answers 409 like a stale revision.
            inserted = session.execute(text(
                "insert into workspace_record_retention (tenant_id, workspace_id, transcript_days, visitor_days, revision, "
                "updated_by, updated_at) values (:t, :w, :td, :vd, 1, :by, now()) on conflict do nothing"), params).rowcount
            if not inserted:
                raise HTTPException(status.HTTP_409_CONFLICT, STALE)
        else:
            session.execute(text(
                "update workspace_record_retention set transcript_days = :td, visitor_days = :vd, revision = revision + 1, "
                "updated_by = :by, updated_at = now() where tenant_id = :t and workspace_id = :w"), params)
        after = session.execute(_READ, _scope(context)).one()
        old = RetentionSetting() if before is None else RetentionSetting(before.transcript_days, before.visitor_days)
        new = RetentionSetting(after.transcript_days, after.visitor_days)
        from .db.repository import SqlAlchemyRepository

        SqlAlchemyRepository(session).record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action="record_retention.update", target=f"record_retention:{context.workspace_id}", outcome="success",
            correlation_id=new_id("correlation"), created_at=utc_now(),
            metadata={"previous_transcript_days": old.transcripts, "new_transcript_days": new.transcripts,
                      "previous_visitor_days": old.visitors, "new_visitor_days": new.visitors,
                      "transcripts_standard": new.transcript_days is None, "visitors_standard": new.visitor_days is None,
                      "revision": after.revision, "input_mode": "click"},
        ))
        return _view(after, context)
