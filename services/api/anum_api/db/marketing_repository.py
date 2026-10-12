"""PostgreSQL store for social media marketing (migration ``0040_marketing``).

The session carries the tenant and workspace RLS context (``open_scoped_store`` or
``maintenance.scoped_unit``); every query also filters on the scope explicitly. Tokens
are only ever stored as ciphertexts (``anum_api.secret_box``).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.marketing.models import (
    Campaign,
    CampaignStatus,
    MarketingPreferences,
    Platform,
    Post,
    SocialAccount,
)
from anum_api.marketing.store import PostQuery
from anum_api.schemas import TenantContext

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace


class MarketingAccountRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "marketing_accounts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    handle: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    scopes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    access_token_ciphertext: Mapped[str | None] = mapped_column(Text)
    refresh_token_ciphertext: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    connected_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("marketing_accounts"),)


class MarketingCampaignRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "marketing_campaigns"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    product: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    brief: Mapped[str] = mapped_column(Text, nullable=False, default="")
    languages: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    platforms: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    utm_campaign: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    auto_post: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_post_accounts: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    auto_post_approved_by: Mapped[str | None] = mapped_column(String(160))
    auto_post_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_post_approval_id: Mapped[str | None] = mapped_column(String(80))
    auto_post_task_id: Mapped[str | None] = mapped_column(String(80))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("marketing_campaigns"),)


class MarketingPostRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "marketing_posts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    campaign_id: Mapped[str | None] = mapped_column(String(80))
    group_id: Mapped[str] = mapped_column(String(80), nullable=False)
    account_id: Mapped[str] = mapped_column(String(80), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    language: Mapped[str] = mapped_column(String(5), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    hashtags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    link_url: Mapped[str | None] = mapped_column(Text)
    media: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_id: Mapped[str | None] = mapped_column(String(300))
    external_url: Mapped[str | None] = mapped_column(String(1000))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    approval_id: Mapped[str | None] = mapped_column(String(80))
    approval_task_id: Mapped[str | None] = mapped_column(String(80))
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(160))
    authorised_by: Mapped[str | None] = mapped_column(String(20))
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    metrics_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    untrusted_source: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("marketing_posts"),)


class MarketingPreferencesRecord(Base):
    __tablename__ = "marketing_preferences"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    brand_voice: Mapped[str] = mapped_column(Text, nullable=False, default="")
    timezone: Mapped[str] = mapped_column(String(80), nullable=False, default="Asia/Riyadh")
    default_hashtags_en: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    default_hashtags_ar: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    updated_by: Mapped[str | None] = mapped_column(String(160))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("marketing_preferences"),)


def _columns(record_type: type[Base]) -> list[str]:
    return [column.key for column in record_type.__table__.columns]  # type: ignore[attr-defined]


def _to_model(record: Base, model: type[Any]) -> Any:
    return model.model_validate({name: getattr(record, name) for name in _columns(type(record))})


def _copy(model: Any, record: Base) -> None:
    data = model.model_dump(mode="json")
    native = model.model_dump()
    for name in _columns(type(record)):
        if name not in data:
            continue
        value = native[name]
        # JSON columns take the JSON form (enums, nested models); scalar columns keep native types.
        setattr(record, name, data[name] if isinstance(value, (list, dict)) or hasattr(value, "model_dump") else (value.value if hasattr(value, "value") else value))


class SqlAlchemyMarketingStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self, record_type: Any) -> tuple[Any, Any]:
        return (record_type.tenant_id == self.context.tenant_id, record_type.workspace_id == self.context.workspace_id)

    def _one(self, record_type: Any, item_id: str, *, for_update: bool = False) -> Any:
        statement = select(record_type).where(*self._scope(record_type), record_type.id == item_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def _add(self, record_type: Any, model: Any) -> Any:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = record_type(id=model.id, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id)
        _copy(model, record)
        record.tenant_id, record.workspace_id = self.context.tenant_id, self.context.workspace_id
        self.session.add(record)
        self.session.flush()
        return model

    def _save(self, record_type: Any, model: Any) -> Any:
        record = self._one(record_type, model.id, for_update=True)
        if record is None:
            raise KeyError(model.id)
        _copy(model, record)
        record.tenant_id, record.workspace_id = self.context.tenant_id, self.context.workspace_id
        self.session.flush()
        return model

    # accounts
    def list_accounts(self, context: TenantContext) -> list[SocialAccount]:
        records = self.session.scalars(
            select(MarketingAccountRecord).where(*self._scope(MarketingAccountRecord)).order_by(
                MarketingAccountRecord.platform, func.lower(MarketingAccountRecord.display_name), MarketingAccountRecord.id
            )
        ).all()
        return [_to_model(record, SocialAccount) for record in records]

    def get_account(self, context: TenantContext, account_id: str, *, for_update: bool = False) -> SocialAccount | None:
        record = self._one(MarketingAccountRecord, account_id, for_update=for_update)
        return _to_model(record, SocialAccount) if record is not None else None

    def find_account(self, context: TenantContext, platform: Platform, external_id: str) -> SocialAccount | None:
        record = self.session.scalar(
            select(MarketingAccountRecord).where(
                *self._scope(MarketingAccountRecord),
                MarketingAccountRecord.platform == platform.value,
                MarketingAccountRecord.external_id == external_id,
            )
        )
        return _to_model(record, SocialAccount) if record is not None else None

    def add_account(self, account: SocialAccount) -> SocialAccount:
        return self._add(MarketingAccountRecord, account)

    def save_account(self, account: SocialAccount) -> SocialAccount:
        return self._save(MarketingAccountRecord, account)

    # campaigns
    def list_campaigns(self, context: TenantContext, *, status: CampaignStatus | None, text: str | None, limit: int, before: Campaign | None) -> list[Campaign]:
        record = MarketingCampaignRecord
        statement = select(record).where(*self._scope(record))
        if status:
            statement = statement.where(record.status == status.value)
        if text:
            pattern = _like(text)
            statement = statement.where(or_(record.name.ilike(pattern, escape="\\"), record.product.ilike(pattern, escape="\\"), record.brief.ilike(pattern, escape="\\")))
        if before is not None:
            statement = statement.where(or_(record.created_at < before.created_at, and_(record.created_at == before.created_at, record.id < before.id)))
        records = self.session.scalars(statement.order_by(record.created_at.desc(), record.id.desc()).limit(limit)).all()
        return [_to_model(item, Campaign) for item in records]

    def get_campaign(self, context: TenantContext, campaign_id: str, *, for_update: bool = False) -> Campaign | None:
        record = self._one(MarketingCampaignRecord, campaign_id, for_update=for_update)
        return _to_model(record, Campaign) if record is not None else None

    def add_campaign(self, campaign: Campaign) -> Campaign:
        return self._add(MarketingCampaignRecord, campaign)

    def save_campaign(self, campaign: Campaign) -> Campaign:
        return self._save(MarketingCampaignRecord, campaign)

    def delete_campaign(self, context: TenantContext, campaign_id: str) -> bool:
        record = self._one(MarketingCampaignRecord, campaign_id, for_update=True)
        if record is None:
            return False
        self.session.execute(
            update(MarketingPostRecord)
            .where(*self._scope(MarketingPostRecord), MarketingPostRecord.campaign_id == campaign_id)
            .values(campaign_id=None)
        )
        self.session.delete(record)
        self.session.flush()
        return True

    # posts
    def add_post(self, post: Post) -> Post:
        return self._add(MarketingPostRecord, post)

    def get_post(self, context: TenantContext, post_id: str, *, for_update: bool = False) -> Post | None:
        record = self._one(MarketingPostRecord, post_id, for_update=for_update)
        return _to_model(record, Post) if record is not None else None

    def save_post(self, post: Post) -> Post:
        return self._save(MarketingPostRecord, post)

    def delete_post(self, context: TenantContext, post_id: str) -> bool:
        record = self._one(MarketingPostRecord, post_id, for_update=True)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    def list_posts(self, context: TenantContext, query: PostQuery) -> list[Post]:
        record = MarketingPostRecord
        statement = select(record).where(*self._scope(record))
        if query.statuses:
            statement = statement.where(record.status.in_([status.value for status in query.statuses]))
        if query.campaign_id:
            statement = statement.where(record.campaign_id == query.campaign_id)
        if query.platform:
            statement = statement.where(record.platform == query.platform.value)
        if query.account_id:
            statement = statement.where(record.account_id == query.account_id)
        if query.group_id:
            statement = statement.where(record.group_id == query.group_id)
        if query.start:
            statement = statement.where(record.scheduled_at >= query.start)
        if query.end:
            statement = statement.where(record.scheduled_at < query.end)
        if query.published_since:
            statement = statement.where(record.published_at >= query.published_since)
        if query.text:
            pattern = _like(query.text)
            statement = statement.where(or_(record.text.ilike(pattern, escape="\\"), record.title.ilike(pattern, escape="\\")))
        if query.order == "schedule":
            statement = statement.order_by(func.coalesce(record.scheduled_at, record.created_at), record.id)
        else:
            if query.before is not None:
                before = query.before
                statement = statement.where(or_(record.created_at < before.created_at, and_(record.created_at == before.created_at, record.id < before.id)))
            statement = statement.order_by(record.created_at.desc(), record.id.desc())
        return [_to_model(item, Post) for item in self.session.scalars(statement.limit(query.limit)).all()]

    def count_posts(self, context: TenantContext, campaign_id: str) -> dict[str, int]:
        rows = self.session.execute(
            select(MarketingPostRecord.status, func.count())
            .where(*self._scope(MarketingPostRecord), MarketingPostRecord.campaign_id == campaign_id)
            .group_by(MarketingPostRecord.status)
        ).all()
        return {status: int(count) for status, count in rows}

    # preferences
    def get_preferences(self, context: TenantContext) -> MarketingPreferences | None:
        record = self.session.get(MarketingPreferencesRecord, (self.context.tenant_id, self.context.workspace_id))
        if record is None:
            return None
        return MarketingPreferences(
            brand_voice=record.brand_voice, timezone=record.timezone, default_hashtags_en=list(record.default_hashtags_en),
            default_hashtags_ar=list(record.default_hashtags_ar), updated_by=record.updated_by, updated_at=record.updated_at,
        )

    def save_preferences(self, context: TenantContext, preferences: MarketingPreferences) -> MarketingPreferences:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = self.session.get(MarketingPreferencesRecord, (self.context.tenant_id, self.context.workspace_id), with_for_update=True)
        if record is None:
            record = MarketingPreferencesRecord(tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id)
            self.session.add(record)
        record.brand_voice = preferences.brand_voice
        record.timezone = preferences.timezone
        record.default_hashtags_en = list(preferences.default_hashtags_en)
        record.default_hashtags_ar = list(preferences.default_hashtags_ar)
        record.updated_by = preferences.updated_by
        record.updated_at = preferences.updated_at or datetime.now().astimezone()
        self.session.flush()
        return preferences


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
