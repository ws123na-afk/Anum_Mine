"""PostgreSQL repository for workspace-scoped live agent meeting sessions.

No in-memory fallback: ANUM MINE sessions must be durable. All writes must use
an existing scoped database transaction and pessimistic row locks. SQL identifiers
are fixed literals; caller-controlled values are parameters.
"""
from __future__ import annotations

from typing import Any
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from .schemas import TenantContext


class LiveMeetingStoreError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SqlLiveMeetingStore:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def args(context: TenantContext, **kwargs: Any) -> dict[str, Any]:
        return {"tenant": context.tenant_id, "workspace": context.workspace_id, **kwargs}

    def create(self, context: TenantContext, data: dict[str, Any]) -> dict[str, Any]:
        params = self.args(context, **data, owner=context.user_id)
        try:
            row = self.session.execute(text("""
                insert into agent_meeting_sessions (
                    id, tenant_id, workspace_id, title, created_by,
                    max_turns, max_duration_seconds, max_tokens,
                    max_cost_usd, max_participants
                ) values (
                    :id, :tenant, :workspace, :title, :owner,
                    :max_turns, :max_duration_seconds, :max_tokens,
                    :max_cost_usd, :max_participants
                ) returning *
            """), params).mappings().one()
            return dict(row)
        except IntegrityError as exc:
            raise LiveMeetingStoreError("conflict", "Meeting already exists or workspace is unavailable") from exc

    def get(self, context: TenantContext, meeting_id: str, *, lock: bool = False) -> dict[str, Any] | None:
        suffix = " for update" if lock else ""
        return self.session.execute(text("""
            select * from agent_meeting_sessions
            where tenant_id=:tenant and workspace_id=:workspace and id=:id
        """ + suffix), self.args(context, id=meeting_id)).mappings().first()

    def list(self, context: TenantContext, *, cursor: str | None, limit: int) -> dict[str, Any]:
        params = self.args(context, cursor=cursor, limit=limit + 1)
        results = self.session.execute(text("""
            select * from agent_meeting_sessions
            where tenant_id=:tenant and workspace_id=:workspace
              and (cast(:cursor as varchar) is null or id > :cursor)
            order by id asc limit :limit
        """), params).mappings().all()
        items = [dict(row) for row in results[:limit]]
        return {"items": items, "next_cursor": items[-1]["id"] if len(results) > limit else None}

    def participants(self, context: TenantContext, meeting_id: str) -> list[dict[str, Any]]:
        rows = self.session.execute(text("""
            select id, session_id, actor_id, kind, added_by, created_at
            from agent_meeting_participants
            where tenant_id=:tenant and workspace_id=:workspace and session_id=:id
            order by created_at, id
        """), self.args(context, id=meeting_id)).mappings().all()
        return [dict(row) for row in rows]

    def turns(self, context: TenantContext, meeting_id: str, *, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.session.execute(text("""
            select id, session_id, sequence, speaker_id, role, body, request_key,
                   input_tokens, output_tokens, estimated_cost_usd, provenance, created_at
            from agent_meeting_turns
            where tenant_id=:tenant and workspace_id=:workspace
              and session_id=:id and sequence > :after
            order by sequence limit :limit
        """), self.args(context, id=meeting_id, after=after, limit=limit)).mappings().all()
        return [dict(row) for row in rows]

    def add_self(self, context: TenantContext, meeting_id: str, participant_id: str, revision: int) -> dict[str, Any]:
        meeting = self.get(context, meeting_id, lock=True)
        if meeting is None:
            raise LiveMeetingStoreError("not_found", "Meeting not found")
        if meeting["created_by"] != context.user_id:
            raise LiveMeetingStoreError("forbidden", "Only the meeting owner can manage participants")
        if meeting["revision"] != revision:
            raise LiveMeetingStoreError("conflict", "Meeting changed; refresh before retrying")
        if meeting["status"] != "scheduled":
            raise LiveMeetingStoreError("conflict", "Participants cannot change after starting")
        current = self.participants(context, meeting_id)
        if any(p["actor_id"] == context.user_id for p in current):
            return dict(meeting)
        if len(current) >= meeting["max_participants"]:
            raise LiveMeetingStoreError("conflict", "Meeting participant limit reached")
        self.session.execute(text("""
            insert into agent_meeting_participants
            (id, tenant_id, workspace_id, session_id, kind, actor_id, added_by)
            values (:participant, :tenant, :workspace, :id, 'human', :owner, :owner)
        """), self.args(context, id=meeting_id, participant=participant_id, owner=context.user_id))
        return self._revise(context, meeting_id, revision, {"new_status": meeting["status"]}, change_status=False)

    def transition(self, context: TenantContext, meeting_id: str, revision: int, target: str) -> dict[str, Any]:
        meeting = self.get(context, meeting_id, lock=True)
        if meeting is None:
            raise LiveMeetingStoreError("not_found", "Meeting not found")
        if meeting["created_by"] != context.user_id:
            raise LiveMeetingStoreError("forbidden", "Only the meeting owner can change its state")
        if meeting["revision"] != revision:
            raise LiveMeetingStoreError("conflict", "Meeting changed; refresh before retrying")
        edges = {
            "scheduled": {"active", "cancelled"},
            "active": {"paused", "finished", "cancelled", "failed"},
            "paused": {"active", "finished", "cancelled"},
        }
        if target not in edges.get(meeting["status"], set()):
            raise LiveMeetingStoreError("conflict", "Invalid meeting state transition")
        if target == "active" and meeting["status"] == "scheduled" and not self.participants(context, meeting_id):
            raise LiveMeetingStoreError("conflict", "Add a participant before starting")
        return self._revise(context, meeting_id, revision, {"new_status": target}, change_status=True)

    def _revise(self, context: TenantContext, meeting_id: str, revision: int, kwargs: dict[str, Any], *, change_status: bool) -> dict[str, Any]:
        sql = """
            update agent_meeting_sessions set revision=revision+1, updated_at=now()
        """
        if change_status:
            sql += """, status=cast(:new_status as varchar),
                started_at=case when cast(:new_status as varchar)='active' and started_at is null then now() else started_at end,
                ended_at=case when cast(:new_status as varchar) in ('finished','cancelled','failed') then now() else ended_at end
            """
        sql += """ where tenant_id=:tenant and workspace_id=:workspace
                and id=:id and revision=:revision returning *"""
        row = self.session.execute(text(sql), self.args(context, id=meeting_id, revision=revision, **(kwargs if change_status else {}))).mappings().first()
        if row is None:
            raise LiveMeetingStoreError("conflict", "Meeting changed; refresh before retrying")
        return dict(row)
