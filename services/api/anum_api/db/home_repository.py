"""PostgreSQL store for Home (migration ``0036_home``, docs/home.md).

The session's RLS context names the tenant, the workspace and the user
(``open_scoped_store(..., user_scoped=True)``); every statement repeats them, so a row of
another user, workspace or tenant is neither read nor written even if a policy were missing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, PrimaryKeyConstraint, String, Text, UniqueConstraint, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.home import (
    MAX_INSIGHT_STATES,
    CommandMode,
    HomeProfile,
    InsightStateKind,
    InsightStateRecord,
    Pin,
    PinKind,
    QuickCommand,
)
from anum_api.schemas import TenantContext, utc_now

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_tenant, require_workspace


class HomeProfileRecord(Base):
    __tablename__ = "home_profiles"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HomePinRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "home_pins"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    target: Mapped[str] = mapped_column(String(500), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(40))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("home_pins"),
        CheckConstraint("kind in ('workspace', 'link', 'feature')", name="ck_home_pins_kind"),
        UniqueConstraint("tenant_id", "workspace_id", "user_id", "kind", "target", name="uq_home_pins_target"),
        Index("ix_home_pins_user", "tenant_id", "workspace_id", "user_id", "position"),
    )


class HomeCommandRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "home_commands"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("home_commands"),
        CheckConstraint("mode in ('work', 'personal', 'custom')", name="ck_home_commands_mode"),
        Index("ix_home_commands_user", "tenant_id", "workspace_id", "user_id", "position"),
    )


class HomeInsightStateRecord(Base):
    __tablename__ = "home_insight_states"

    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    insight_key: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "workspace_id", "user_id", "insight_key", name="pk_home_insight_states"),
        _workspace_fk("home_insight_states"),
        CheckConstraint("state in ('dismissed', 'snoozed')", name="ck_home_insight_states_state"),
    )


def _pin(record: HomePinRecord) -> Pin:
    return Pin(
        id=record.id, kind=PinKind(record.kind), label=record.label, target=record.target, icon=record.icon,
        position=record.position, created_at=record.created_at, updated_at=record.updated_at,
    )


def _command(record: HomeCommandRecord) -> QuickCommand:
    return QuickCommand(
        id=record.id, mode=CommandMode(record.mode), label=record.label, prompt=record.prompt,
        position=record.position, created_at=record.created_at, updated_at=record.updated_at,
    )


class SqlAlchemyHomeStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _mine(model: Any, context: TenantContext) -> tuple[Any, ...]:
        return (
            model.tenant_id == context.tenant_id,
            model.workspace_id == context.workspace_id,
            model.user_id == context.user_id,
        )

    # profile ---------------------------------------------------------------------------------

    def get_profile(self, context: TenantContext) -> HomeProfile | None:
        record = self.session.get(HomeProfileRecord, (context.tenant_id, context.user_id))
        if record is None:
            return None
        return HomeProfile(display_name=record.display_name, updated_at=record.updated_at)

    def save_profile(self, context: TenantContext, profile: HomeProfile) -> HomeProfile:
        require_tenant(self.session, context.tenant_id)
        record = self.session.get(HomeProfileRecord, (context.tenant_id, context.user_id))
        if record is None:
            record = HomeProfileRecord(tenant_id=context.tenant_id, user_id=context.user_id)
            self.session.add(record)
        record.display_name = profile.display_name
        record.updated_at = profile.updated_at or utc_now()
        self.session.flush()
        return HomeProfile(display_name=record.display_name, updated_at=record.updated_at)

    # pins --------------------------------------------------------------------------------------

    def list_pins(self, context: TenantContext) -> list[Pin]:
        records = self.session.scalars(
            select(HomePinRecord)
            .where(*self._mine(HomePinRecord, context))
            .order_by(HomePinRecord.position, HomePinRecord.created_at, HomePinRecord.id)
        ).all()
        return [_pin(r) for r in records]

    def save_pin(self, context: TenantContext, pin: Pin) -> Pin:
        record = self.session.scalar(select(HomePinRecord).where(*self._mine(HomePinRecord, context), HomePinRecord.id == pin.id))
        if record is None:
            require_workspace(self.session, context.tenant_id, context.workspace_id)
            record = HomePinRecord(
                id=pin.id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, user_id=context.user_id,
                created_at=pin.created_at,
            )
            self.session.add(record)
        record.kind, record.label, record.target, record.icon = pin.kind.value, pin.label, pin.target, pin.icon
        record.position, record.updated_at = pin.position, pin.updated_at
        self.session.flush()
        return _pin(record)

    def delete_pin(self, context: TenantContext, pin_id: str) -> bool:
        result = self.session.execute(delete(HomePinRecord).where(*self._mine(HomePinRecord, context), HomePinRecord.id == pin_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    # quick commands --------------------------------------------------------------------------------

    def list_commands(self, context: TenantContext) -> list[QuickCommand]:
        records = self.session.scalars(
            select(HomeCommandRecord)
            .where(*self._mine(HomeCommandRecord, context))
            .order_by(HomeCommandRecord.position, HomeCommandRecord.created_at, HomeCommandRecord.id)
        ).all()
        return [_command(r) for r in records]

    def save_command(self, context: TenantContext, command: QuickCommand) -> QuickCommand:
        record = self.session.scalar(
            select(HomeCommandRecord).where(*self._mine(HomeCommandRecord, context), HomeCommandRecord.id == command.id)
        )
        if record is None:
            require_workspace(self.session, context.tenant_id, context.workspace_id)
            record = HomeCommandRecord(
                id=command.id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, user_id=context.user_id,
                created_at=command.created_at or utc_now(),
            )
            self.session.add(record)
        record.mode, record.label, record.prompt = command.mode.value, command.label, command.prompt
        record.position, record.updated_at = command.position, command.updated_at or utc_now()
        self.session.flush()
        return _command(record)

    def delete_command(self, context: TenantContext, command_id: str) -> bool:
        result = self.session.execute(
            delete(HomeCommandRecord).where(*self._mine(HomeCommandRecord, context), HomeCommandRecord.id == command_id)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    # insight states ------------------------------------------------------------------------------------

    def list_insight_states(self, context: TenantContext) -> dict[str, InsightStateRecord]:
        records = self.session.scalars(select(HomeInsightStateRecord).where(*self._mine(HomeInsightStateRecord, context))).all()
        return {
            r.insight_key: InsightStateRecord(key=r.insight_key, state=InsightStateKind(r.state), until=r.until, updated_at=r.updated_at)
            for r in records
        }

    def save_insight_state(self, context: TenantContext, record: InsightStateRecord) -> InsightStateRecord:
        row = self.session.get(
            HomeInsightStateRecord, (context.tenant_id, context.workspace_id, context.user_id, record.key)
        )
        if row is None:
            require_workspace(self.session, context.tenant_id, context.workspace_id)
            row = HomeInsightStateRecord(
                tenant_id=context.tenant_id, workspace_id=context.workspace_id, user_id=context.user_id, insight_key=record.key
            )
            self.session.add(row)
        row.state, row.until, row.updated_at = record.state.value, record.until, record.updated_at
        self.session.flush()
        # Keep the newest MAX_INSIGHT_STATES per user; older dismissals are of insights long gone.
        stale = self.session.scalars(
            select(HomeInsightStateRecord.insight_key)
            .where(*self._mine(HomeInsightStateRecord, context))
            .order_by(HomeInsightStateRecord.updated_at.desc(), HomeInsightStateRecord.insight_key)
            .offset(MAX_INSIGHT_STATES)
        ).all()
        if stale:
            self.session.execute(
                delete(HomeInsightStateRecord).where(
                    *self._mine(HomeInsightStateRecord, context), HomeInsightStateRecord.insight_key.in_(list(stale))
                )
            )
        return record

    def delete_insight_state(self, context: TenantContext, key: str) -> bool:
        result = self.session.execute(
            delete(HomeInsightStateRecord).where(*self._mine(HomeInsightStateRecord, context), HomeInsightStateRecord.insight_key == key)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]
