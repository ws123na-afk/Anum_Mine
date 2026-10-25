"""Audit records for live agent meetings that never carry transcript text (finding S-04).

A record names who acted, which agent spoke, the meeting, the turn number, the usage and
cost, the outcome and a correlation id. Metadata is built from an allow list of short
scalar fields, so a message body, a model prompt or an error string with user text cannot
slip in, whatever the caller passes. Records are written in the caller's scoped
transaction (success) or in their own short one (denials and cap hits, which must survive
the refusal that rolls the request back).
"""
from __future__ import annotations

from typing import Any

from .audit import AuditRecord
from .schemas import TenantContext, new_id, utc_now

# Every field a meeting audit record may carry. Text-like fields are short codes or ids.
ALLOWED_FIELDS = frozenset({
    "meeting_id", "turn_number", "agent_id", "request_key", "model", "input_count", "output_count",
    "cost_usd", "redacted", "reason", "read_path", "code", "limit_usd", "used_usd", "period_start",
    "previous_limit_usd", "new_limit_usd", "role",
})
MAX_TEXT = 80


def safe_metadata(**fields: Any) -> dict[str, Any]:
    """Only allowed fields, only scalars, strings cut to a short length."""
    out: dict[str, Any] = {"resource": "agent_meeting"}
    for key, value in fields.items():
        if key not in ALLOWED_FIELDS or value is None:
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            out[key] = value
        else:
            out[key] = str(value)[:MAX_TEXT]
    return out


def audit_event(session: Any, context: TenantContext, action: str, target: str, *, outcome: str = "success",
                correlation_id: str | None = None, **fields: Any) -> str:
    """Write one record through ``session`` (a SQLAlchemy session already scoped to the tenant)."""
    from .db.repository import SqlAlchemyRepository

    correlation = correlation_id or new_id("correlation")
    SqlAlchemyRepository(session).record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        actor=context.user_id, action=action, target=target, outcome=outcome,
        correlation_id=correlation, created_at=utc_now(),
        metadata=safe_metadata(**fields),
    ))
    return correlation


def audit_in_new_transaction(context: TenantContext, action: str, target: str, *, outcome: str,
                             correlation_id: str | None = None, **fields: Any) -> None:
    """Record a denial or a cap hit in its own transaction, so it survives the refusal. Best effort:
    a failure to audit is logged by the caller's logger and never replaces the refusal."""
    from .live_agent_meeting_store import SqlLiveMeetingStore
    from .scoped_store import open_scoped_store

    with open_scoped_store(context, None, SqlLiveMeetingStore, user_scoped=True) as store:
        audit_event(store.session, context, action, target, outcome=outcome, correlation_id=correlation_id, **fields)
