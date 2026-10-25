"""PostgreSQL store for agent profiles and agent memories (migration ``0041_agent_profiles``).

The session's RLS context names the tenant, the workspace and the user
(``open_scoped_store(..., user_scoped=True)``): a private agent of another user, and its
memories, are invisible. Statements repeat the tenant and workspace scope explicitly.
Audit records are written in the same transaction as the change they describe.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from anum_api.agent_profiles import (
    AgentAutonomy,
    AgentCategory,
    AgentMemory,
    AgentMemorySource,
    AgentModelRoute,
    AgentProfile,
    AgentSharing,
    AgentStatus,
    KnowledgeMode,
    KnowledgeSource,
)
from anum_api.audit import AuditRecord
from anum_api.avatar_appearance import avatar_from_record
from anum_api.schemas import TenantContext

from .models import AgentMemoryRecord, AgentProfileRecord
from .scope import require_workspace


def _profile(record: AgentProfileRecord) -> AgentProfile:
    return AgentProfile(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        owner_user_id=record.owner_user_id,
        name=record.name,
        icon=record.icon,
        color=record.color,
        category=AgentCategory(record.category),
        department=record.department,
        description=record.description,
        instructions=record.instructions,
        model_route=AgentModelRoute(record.model_route),
        allowed_skills=list(record.allowed_skills or []),
        allowed_tools=list(record.allowed_tools or []),
        autonomy=AgentAutonomy.model_validate(record.autonomy or {}),
        knowledge_mode=KnowledgeMode(record.knowledge_mode),
        knowledge_sources=[KnowledgeSource.model_validate(item) for item in record.knowledge_sources or []],
        status=AgentStatus(record.status),
        sharing=AgentSharing(record.sharing),
        template_id=record.template_id,
        avatar=avatar_from_record(record.avatar),
        created_at=record.created_at,
        updated_at=record.updated_at,
        version=record.version,
    )


def _memory(record: AgentMemoryRecord) -> AgentMemory:
    return AgentMemory(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        agent_id=record.agent_id,
        content=record.content,
        source=AgentMemorySource(record.source),
        task_id=record.task_id,
        created_by=record.created_by,
        created_at=record.created_at,
    )


class SqlAlchemyAgentStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self):  # type: ignore[no-untyped-def]
        return (
            AgentProfileRecord.tenant_id == self.context.tenant_id,
            AgentProfileRecord.workspace_id == self.context.workspace_id,
        )

    def _memory_scope(self, agent_id: str):  # type: ignore[no-untyped-def]
        return (
            AgentMemoryRecord.tenant_id == self.context.tenant_id,
            AgentMemoryRecord.workspace_id == self.context.workspace_id,
            AgentMemoryRecord.agent_id == agent_id,
        )

    def list(self, context: TenantContext) -> list[AgentProfile]:
        records = self.session.scalars(
            select(AgentProfileRecord).where(*self._scope()).order_by(AgentProfileRecord.updated_at.desc())
        ).all()
        return [_profile(record) for record in records]

    def get(self, context: TenantContext, agent_id: str) -> AgentProfile | None:
        record = self._record(agent_id)
        return _profile(record) if record is not None else None

    def _record(self, agent_id: str) -> AgentProfileRecord | None:
        return self.session.scalar(select(AgentProfileRecord).where(*self._scope(), AgentProfileRecord.id == agent_id))

    def count(self, context: TenantContext) -> int:
        # Visible agents only (RLS): another user's private agents are not counted here,
        # so the cap is a guard against runaway creation, not an exact workspace total.
        return int(self.session.scalar(select(func.count()).select_from(AgentProfileRecord).where(*self._scope())) or 0)

    def save(self, profile: AgentProfile) -> AgentProfile:
        require_workspace(self.session, profile.tenant_id, profile.workspace_id)
        record = self._record(profile.id)
        if record is None:
            record = AgentProfileRecord(
                id=profile.id,
                tenant_id=profile.tenant_id,
                workspace_id=profile.workspace_id,
                owner_user_id=profile.owner_user_id,
                created_at=profile.created_at,
            )
            self.session.add(record)
        record.name = profile.name
        record.icon = profile.icon
        record.color = profile.color
        record.category = profile.category.value
        record.department = profile.department
        record.description = profile.description
        record.instructions = profile.instructions
        record.model_route = profile.model_route.value
        record.allowed_skills = list(profile.allowed_skills)
        record.allowed_tools = list(profile.allowed_tools)
        record.autonomy = profile.autonomy.model_dump(mode="json")
        record.knowledge_mode = profile.knowledge_mode.value
        record.knowledge_sources = [source.model_dump(mode="json") for source in profile.knowledge_sources]
        record.status = profile.status.value
        record.sharing = profile.sharing.value
        record.template_id = profile.template_id
        record.avatar = profile.avatar.stored() if profile.avatar else None
        record.version = profile.version
        record.updated_at = profile.updated_at
        self.session.flush()
        return profile

    def delete(self, context: TenantContext, agent_id: str) -> bool:
        result = self.session.execute(
            delete(AgentProfileRecord).where(*self._scope(), AgentProfileRecord.id == agent_id)
        )
        self.session.flush()
        return bool(result.rowcount)

    def list_memories(self, context: TenantContext, agent_id: str) -> list[AgentMemory]:
        records = self.session.scalars(
            select(AgentMemoryRecord)
            .where(*self._memory_scope(agent_id))
            .order_by(AgentMemoryRecord.created_at.desc(), AgentMemoryRecord.id.desc())
        ).all()
        return [_memory(record) for record in records]

    def add_memory(self, memory: AgentMemory) -> AgentMemory:
        self.session.add(
            AgentMemoryRecord(
                id=memory.id,
                tenant_id=memory.tenant_id,
                workspace_id=memory.workspace_id,
                agent_id=memory.agent_id,
                content=memory.content,
                source=memory.source.value,
                task_id=memory.task_id,
                created_by=memory.created_by,
                created_at=memory.created_at,
            )
        )
        self.session.flush()
        return memory

    def delete_memory(self, context: TenantContext, agent_id: str, memory_id: str) -> bool:
        result = self.session.execute(
            delete(AgentMemoryRecord).where(*self._memory_scope(agent_id), AgentMemoryRecord.id == memory_id)
        )
        self.session.flush()
        return bool(result.rowcount)

    def record_audit(self, record: AuditRecord) -> None:
        from .repository import SqlAlchemyRepository

        SqlAlchemyRepository(self.session, created_by_user_id=self.context.user_id).record_audit(record)
