"""PostgreSQL rows of the prospecting pipeline and the outbox (migration ``0075_business_pipeline``).

The generic :class:`~anum_api.db.business_repository.SqlAlchemyBusinessStore` reads and writes
them like every other business kind, inside the caller's tenant and workspace (RLS).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base

TS = DateTime(timezone=True)


class _Row:
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class BusinessOutreachSettingsRecord(Base, _Row):
    __tablename__ = "business_outreach_settings"
    signals: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fetches_per_day: Mapped[int] = mapped_column(Integer)
    pages_per_lead: Mapped[int] = mapped_column(Integer)
    sending_enabled: Mapped[bool] = mapped_column(Boolean)
    daily_quota: Mapped[dict[str, Any]] = mapped_column(JSONB)
    max_bounce_rate_pct: Mapped[float] = mapped_column(Float)
    follow_up_max: Mapped[int] = mapped_column(Integer)
    follow_up_min_days: Mapped[int] = mapped_column(Integer)
    paused: Mapped[bool] = mapped_column(Boolean)
    paused_reason: Mapped[str] = mapped_column(String(300))
    paused_at: Mapped[datetime | None] = mapped_column(TS)
    updated_by: Mapped[str | None] = mapped_column(String(160))


class BusinessObservationRecord(Base, _Row):
    __tablename__ = "business_observations"
    lead_id: Mapped[str] = mapped_column(String(80))
    account_id: Mapped[str] = mapped_column(String(80))
    run_id: Mapped[str | None] = mapped_column(String(80))
    evidence_id: Mapped[str | None] = mapped_column(String(80))
    signal_code: Mapped[str] = mapped_column(String(60))
    value_num: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str] = mapped_column(String(600))
    observed_on: Mapped[date] = mapped_column(Date)
    confidence: Mapped[float] = mapped_column(Float)
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessPipelineRunRecord(Base, _Row):
    __tablename__ = "business_pipeline_runs"
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    product_id: Mapped[str | None] = mapped_column(String(80))
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB)
    estimate: Mapped[dict[str, Any]] = mapped_column(JSONB)
    used: Mapped[dict[str, Any]] = mapped_column(JSONB)
    steps: Mapped[list[Any]] = mapped_column(JSONB)
    results: Mapped[dict[str, Any]] = mapped_column(JSONB)
    stop_requested: Mapped[bool] = mapped_column(Boolean)
    stop_reason: Mapped[str] = mapped_column(String(300))
    confirmed_by: Mapped[str | None] = mapped_column(String(160))
    confirmed_at: Mapped[datetime | None] = mapped_column(TS)
    heartbeat_at: Mapped[datetime | None] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessOutboxRecord(Base, _Row):
    __tablename__ = "business_outbox"
    lead_id: Mapped[str] = mapped_column(String(80))
    contact_id: Mapped[str] = mapped_column(String(80))
    draft_id: Mapped[str] = mapped_column(String(80))
    channel: Mapped[str] = mapped_column(String(20))
    step: Mapped[int] = mapped_column(Integer)
    follow_up_of: Mapped[str | None] = mapped_column(String(80))
    recipient: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    gate_issues: Mapped[list[Any]] = mapped_column(JSONB)
    approval_id: Mapped[str | None] = mapped_column(String(80))
    approval_task_id: Mapped[str | None] = mapped_column(String(80))
    send_after: Mapped[datetime | None] = mapped_column(TS)
    sent_at: Mapped[datetime | None] = mapped_column(TS)
    provider_ref: Mapped[str] = mapped_column(String(200))
    note: Mapped[str] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessReplyRecord(Base, _Row):
    __tablename__ = "business_replies"
    lead_id: Mapped[str] = mapped_column(String(80))
    contact_id: Mapped[str | None] = mapped_column(String(80))
    outbox_id: Mapped[str | None] = mapped_column(String(80))
    channel: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20))
    recipient_hash: Mapped[str | None] = mapped_column(String(64))
    cancelled: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessTargetProfileRecord(Base, _Row):
    __tablename__ = "business_target_profiles"
    product_id: Mapped[str] = mapped_column(String(80))
    industries: Mapped[list[Any]] = mapped_column(JSONB)
    company_sizes: Mapped[list[Any]] = mapped_column(JSONB)
    regions: Mapped[list[Any]] = mapped_column(JSONB)
    roles: Mapped[list[Any]] = mapped_column(JSONB)
    keywords: Mapped[list[Any]] = mapped_column(JSONB)
    disqualifiers: Mapped[list[Any]] = mapped_column(JSONB)
    value_proposition: Mapped[str] = mapped_column(Text)
    value_proposition_ar: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(String(160))


PIPELINE_RECORDS: dict[str, Any] = {
    "target_profiles": BusinessTargetProfileRecord,
    "outreach_settings": BusinessOutreachSettingsRecord,
    "observations": BusinessObservationRecord,
    "pipeline_runs": BusinessPipelineRunRecord,
    "outbox": BusinessOutboxRecord,
    "replies": BusinessReplyRecord,
}
