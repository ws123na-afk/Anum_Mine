"""Transactional PostgreSQL persistence for the Sales CRM domain.

Database RLS plus explicit scope predicates. Call inside the existing scoped
unit-of-work and commit together with its other changes. No autonomous sending.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKeyConstraint, Integer, Numeric, String, Text, UniqueConstraint, Index, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.sales_lead_lifecycle import (
    Lead, LeadEvent, QuoteDraft, FollowUp, LeadStatus, FollowUpStatus,
)
from anum_api.whatsapp_sales import SalesIntent
from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace


class SalesLeadRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "sales_leads"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    contact_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_conversation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_campaign_hint: Mapped[str | None] = mapped_column(String(64))
    preferred_language: Mapped[str] = mapped_column(String(5), nullable=False)
    intent: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    phone_e164: Mapped[str | None] = mapped_column(String(20))
    requirements: Mapped[str] = mapped_column(Text, nullable=False, default="")
    budget_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    budget_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(String(160))
    marketing_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        _workspace_fk("sales_leads"),
        UniqueConstraint("tenant_id","workspace_id","source_message_id",name="uq_sales_leads_inbound_message"),
        UniqueConstraint("tenant_id","workspace_id","id",name="uq_sales_leads_scope_id"),
        Index("ix_sales_leads_pipeline","tenant_id","workspace_id","status","updated_at"),
        Index("ix_sales_leads_owner","tenant_id","workspace_id","assigned_to"),
    )


class SalesLeadEventRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "sales_lead_events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    lead_id: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event: Mapped[str] = mapped_column(String(80), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str | None] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        _workspace_fk("sales_lead_events"),
        ForeignKeyConstraint(["tenant_id","workspace_id","lead_id"],["sales_leads.tenant_id","sales_leads.workspace_id","sales_leads.id"],name="fk_sales_lead_events_lead"),
        UniqueConstraint("tenant_id","workspace_id","lead_id","revision",name="uq_sales_lead_events_revision"),
        Index("ix_sales_lead_events_history","tenant_id","workspace_id","lead_id","occurred_at"),
    )


class SalesQuoteRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "sales_quotes"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    lead_id: Mapped[str] = mapped_column(String(80), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    line_items: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    terms: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    approval_id: Mapped[str | None] = mapped_column(String(80))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        _workspace_fk("sales_quotes"),
        ForeignKeyConstraint(["tenant_id","workspace_id","lead_id"],["sales_leads.tenant_id","sales_leads.workspace_id","sales_leads.id"],name="fk_sales_quotes_lead"),
        Index("ix_sales_quotes_lead","tenant_id","workspace_id","lead_id","created_at"),
    )


class SalesFollowUpRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "sales_followups"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    lead_id: Mapped[str] = mapped_column(String(80), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="proposed")
    approval_id: Mapped[str | None] = mapped_column(String(80))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    template_name: Mapped[str | None] = mapped_column(String(512))
    template_language: Mapped[str | None] = mapped_column(String(20))
    template_parameters: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(160))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_wamid: Mapped[str | None] = mapped_column(String(200))
    last_error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        _workspace_fk("sales_followups"),
        ForeignKeyConstraint(["tenant_id","workspace_id","lead_id"],["sales_leads.tenant_id","sales_leads.workspace_id","sales_leads.id"],name="fk_sales_followups_lead"),
        Index("ix_sales_followups_due","tenant_id","workspace_id","status","due_at"),
    )


class SalesAutomationSettingsRecord(Base):
    __tablename__ = "sales_automation_settings"
    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_sends_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (_workspace_fk("sales_automation_settings"),)


class SalesConsentEventRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "sales_consent_events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    lead_id: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_message_id: Mapped[str | None] = mapped_column(String(80))
    evidence_note: Mapped[str] = mapped_column(String(500), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recorded_by: Mapped[str] = mapped_column(String(160), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        _workspace_fk("sales_consent_events"),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "lead_id"],
            ["sales_leads.tenant_id", "sales_leads.workspace_id", "sales_leads.id"],
            name="fk_sales_consent_events_lead",
        ),
        Index("ix_sales_consent_history","tenant_id","workspace_id","lead_id","recorded_at"),
    )


def _record_to_lead(record: SalesLeadRecord) -> Lead:
    values = {field.name: getattr(record, field.name) for field in fields(Lead)}
    values["intent"] = SalesIntent(values["intent"])
    values["status"] = LeadStatus(values["status"])
    return Lead(**values)


class SalesCRMStore:
    def __init__(self, session: Session, tenant_id: str, workspace_id: str):
        self.session = session
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id

    def _check(self, tenant_id: str, workspace_id: str) -> None:
        if tenant_id != self.tenant_id or workspace_id != self.workspace_id:
            raise PermissionError("Cross-workspace sales operation refused")

    def get(self, lead_id: str) -> Lead | None:
        record = self.session.scalar(
            select(SalesLeadRecord).where(
                SalesLeadRecord.id == lead_id,
                SalesLeadRecord.tenant_id == self.tenant_id,
                SalesLeadRecord.workspace_id == self.workspace_id,
            )
        )
        return _record_to_lead(record) if record is not None else None

    def find_by_inbound_message(self, source_message_id: str) -> Lead | None:
        record = self.session.scalar(
            select(SalesLeadRecord).where(
                SalesLeadRecord.source_message_id == source_message_id,
                SalesLeadRecord.tenant_id == self.tenant_id,
                SalesLeadRecord.workspace_id == self.workspace_id,
            )
        )
        return _record_to_lead(record) if record else None

    def find_by_conversation(self, conversation_id: str) -> Lead | None:
        """Find the current sales opportunity for a verified WhatsApp conversation."""
        record = self.session.scalar(
            select(SalesLeadRecord).where(
                SalesLeadRecord.source_conversation_id == conversation_id,
                SalesLeadRecord.tenant_id == self.tenant_id,
                SalesLeadRecord.workspace_id == self.workspace_id,
            ).order_by(SalesLeadRecord.created_at.desc()).limit(1)
        )
        return _record_to_lead(record) if record is not None else None

    def list_leads(self, *, status: LeadStatus | None = None, limit: int = 50) -> list[Lead]:
        if not 1 <= limit <= 100:
            raise ValueError("Limit must be 1-100")
        query = select(SalesLeadRecord).where(
            SalesLeadRecord.tenant_id == self.tenant_id,
            SalesLeadRecord.workspace_id == self.workspace_id,
        )
        if status is not None:
            query = query.where(SalesLeadRecord.status == status.value)
        query = query.order_by(SalesLeadRecord.updated_at.desc(),SalesLeadRecord.id.desc()).limit(limit)
        return [_record_to_lead(item) for item in self.session.scalars(query).all()]

    def insert(self, lead: Lead, event: LeadEvent) -> None:
        self._check(lead.tenant_id, lead.workspace_id)
        self._check(event.tenant_id, event.workspace_id)
        if lead.id != event.lead_id or event.revision != lead.revision:
            raise ValueError("Lead/event mismatch")
        require_workspace(self.session, self.tenant_id, self.workspace_id)
        values = {field.name: getattr(lead, field.name) for field in fields(Lead)}
        values["intent"] = lead.intent.value
        values["status"] = lead.status.value
        self.session.add(SalesLeadRecord(**values))
        self._add_event(event)

    def update(self, previous: Lead, current: Lead, event: LeadEvent) -> None:
        self._check(current.tenant_id, current.workspace_id)
        self._check(previous.tenant_id, previous.workspace_id)
        if current.id != previous.id or current.revision != previous.revision + 1:
            raise ValueError("Invalid revision update")
        if event.revision != current.revision or event.lead_id != current.id:
            raise ValueError("Lead/event mismatch")
        values = {field.name: getattr(current, field.name) for field in fields(Lead)}
        for key in ("id", "tenant_id", "workspace_id", "created_at", "source_message_id", "source_conversation_id", "contact_id"):
            values.pop(key)
        values["intent"] = current.intent.value
        values["status"] = current.status.value
        result = self.session.execute(update(SalesLeadRecord).where(
            SalesLeadRecord.id == current.id,
            SalesLeadRecord.tenant_id == self.tenant_id,
            SalesLeadRecord.workspace_id == self.workspace_id,
            SalesLeadRecord.revision == previous.revision,
        ).values(**values))
        if result.rowcount != 1:
            raise ValueError("Sales lead revision conflict")
        self._add_event(event)

    def _add_event(self, event: LeadEvent) -> None:
        self._check(event.tenant_id, event.workspace_id)
        self.session.add(SalesLeadEventRecord(
            id=str(uuid4()), tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            lead_id=event.lead_id, actor_id=event.actor_id, event=event.event,
            occurred_at=event.occurred_at, from_status=event.from_status.value if event.from_status else None,
            to_status=event.to_status.value if event.to_status else None, revision=event.revision,
        ))

    def save_quote(self, draft: QuoteDraft, *, quote_id: str, created_by: str, now: datetime) -> None:
        self._check(draft.tenant_id, draft.workspace_id)
        if not draft.approval_required:
            raise ValueError("Quotation must require approval")
        self.session.add(SalesQuoteRecord(
            id=quote_id, tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            lead_id=draft.lead_id, currency=draft.currency,
            line_items=[{"description": desc, "amount": str(amount)} for desc, amount in draft.line_items],
            total=draft.total, terms=draft.terms, status="draft", created_by=created_by, created_at=now,
        ))

    def save_followup(self, followup: FollowUp, *, followup_id: str, created_by: str, now: datetime) -> None:
        self._check(followup.tenant_id, followup.workspace_id)
        if followup.status != FollowUpStatus.PROPOSED or not followup.approval_required:
            raise ValueError("Unapproved follow-up cannot be scheduled")
        self.session.add(SalesFollowUpRecord(
            id=followup_id, tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            lead_id=followup.lead_id, due_at=followup.due_at, channel=followup.channel,
            purpose=followup.purpose, status="proposed", created_by=created_by, created_at=now,
        ))

    def lead_history(self, lead_id: str) -> dict[str, list[dict]]:
        """Read quotes, proposals and audit trail only within the active workspace."""
        scope = (self.tenant_id, self.workspace_id, lead_id)
        events = self.session.scalars(select(SalesLeadEventRecord).where(
            SalesLeadEventRecord.tenant_id == scope[0],
            SalesLeadEventRecord.workspace_id == scope[1],
            SalesLeadEventRecord.lead_id == scope[2],
        ).order_by(SalesLeadEventRecord.occurred_at.desc()).limit(100)).all()
        quotes = self.session.scalars(select(SalesQuoteRecord).where(
            SalesQuoteRecord.tenant_id == scope[0],
            SalesQuoteRecord.workspace_id == scope[1],
            SalesQuoteRecord.lead_id == scope[2],
        ).order_by(SalesQuoteRecord.created_at.desc()).limit(100)).all()
        followups = self.session.scalars(select(SalesFollowUpRecord).where(
            SalesFollowUpRecord.tenant_id == scope[0],
            SalesFollowUpRecord.workspace_id == scope[1],
            SalesFollowUpRecord.lead_id == scope[2],
        ).order_by(SalesFollowUpRecord.due_at.desc()).limit(100)).all()
        return {
            "events": [{"id": row.id, "event": row.event, "actor_id": row.actor_id,
                        "status": row.to_status, "occurred_at": row.occurred_at.isoformat()} for row in events],
            "quotes": [{"id": row.id, "status": row.status, "currency": row.currency,
                        "total": str(row.total), "line_items": row.line_items,
                        "terms": row.terms, "created_at": row.created_at.isoformat()} for row in quotes],
            "followups": [{"id": row.id, "status": row.status, "channel": row.channel,
                           "purpose": row.purpose, "due_at": row.due_at.isoformat()} for row in followups],
        }

    def campaign_conversion_report(self) -> dict:
        """Aggregate every workspace lead; marker attribution remains unverified."""
        from sqlalchemy import func
        rows = self.session.execute(
            select(
                SalesLeadRecord.source_campaign_hint,
                SalesLeadRecord.status,
                func.count(SalesLeadRecord.id),
            ).where(
                SalesLeadRecord.tenant_id == self.tenant_id,
                SalesLeadRecord.workspace_id == self.workspace_id,
            ).group_by(
                SalesLeadRecord.source_campaign_hint, SalesLeadRecord.status
            )
        ).all()
        grouped: dict[str, dict[str, int | str]] = {}
        for campaign, status, count in rows:
            key = campaign or "unattributed"
            group = grouped.setdefault(key, {"campaign": key, "leads": 0, "qualified": 0, "won": 0, "lost": 0})
            group["leads"] += count
            if status in {"qualified", "quoted", "negotiating", "won"}:
                group["qualified"] += count
            if status == "won":
                group["won"] += count
            if status == "lost":
                group["lost"] += count
        return {"source": "self_reported_whatsapp_marker", "verified_attribution": False,
                "campaigns": sorted(grouped.values(), key=lambda row: (-row["leads"], row["campaign"]))}

    def sales_operations(self, *, limit: int = 100) -> dict:
        """Review queue only. An approval here never implies external delivery."""
        quotes = self.session.scalars(select(SalesQuoteRecord).where(
            SalesQuoteRecord.tenant_id == self.tenant_id,
            SalesQuoteRecord.workspace_id == self.workspace_id,
            SalesQuoteRecord.status.in_(["draft", "pending_approval", "approved"]),
        ).order_by(SalesQuoteRecord.created_at.desc()).limit(limit)).all()
        followups = self.session.scalars(select(SalesFollowUpRecord).where(
            SalesFollowUpRecord.tenant_id == self.tenant_id,
            SalesFollowUpRecord.workspace_id == self.workspace_id,
            SalesFollowUpRecord.status.in_(["proposed", "approved", "dispatching", "held", "unknown", "failed"]),
        ).order_by(SalesFollowUpRecord.due_at.asc()).limit(limit)).all()
        return {
            "quotes": [{"id": row.id, "lead_id": row.lead_id, "status": row.status,
                        "total": str(row.total), "currency": row.currency,
                        "approval_id": row.approval_id} for row in quotes],
            "followups": [{"id": row.id, "lead_id": row.lead_id, "status": row.status,
                           "due_at": row.due_at.isoformat(), "channel": row.channel,
                           "purpose": row.purpose, "approval_id": row.approval_id} for row in followups],
            "delivery_enabled": False,
            "execution_enabled": bool(self.get_automation_settings()["enabled"] and not self.get_automation_settings()["paused"]),
            "automation_policy": self.get_automation_settings(),
        }

    def get_quote_for_delivery(self, *, lead_id: str, quote_id: str) -> SalesQuoteRecord | None:
        return self.session.scalar(select(SalesQuoteRecord).where(
            SalesQuoteRecord.id == quote_id,
            SalesQuoteRecord.lead_id == lead_id,
            SalesQuoteRecord.tenant_id == self.tenant_id,
            SalesQuoteRecord.workspace_id == self.workspace_id,
        ))

    def reserve_quote_delivery(self, *, lead_id: str, quote_id: str) -> bool:
        """Single winner; reserve before making a governed WhatsApp send request."""
        result = self.session.execute(update(SalesQuoteRecord).where(
            SalesQuoteRecord.id == quote_id,
            SalesQuoteRecord.lead_id == lead_id,
            SalesQuoteRecord.tenant_id == self.tenant_id,
            SalesQuoteRecord.workspace_id == self.workspace_id,
            SalesQuoteRecord.status == "draft",
            SalesQuoteRecord.approval_id.is_(None),
        ).values(status="pending_approval"))
        return result.rowcount == 1

    def finish_quote_delivery_request(
        self, *, lead_id: str, quote_id: str, approval_id: str | None,
        status: str,
    ) -> bool:
        if status not in {"draft", "pending_approval", "sent"}:
            raise ValueError("Invalid quote request status")
        result = self.session.execute(update(SalesQuoteRecord).where(
            SalesQuoteRecord.id == quote_id,
            SalesQuoteRecord.lead_id == lead_id,
            SalesQuoteRecord.tenant_id == self.tenant_id,
            SalesQuoteRecord.workspace_id == self.workspace_id,
            SalesQuoteRecord.status == "pending_approval",
            SalesQuoteRecord.approval_id.is_(None),
        ).values(status=status, approval_id=approval_id))
        return result.rowcount == 1

    def get_automation_settings(self, *, lock: bool = False) -> dict:
        query = select(SalesAutomationSettingsRecord).where(
            SalesAutomationSettingsRecord.tenant_id == self.tenant_id,
            SalesAutomationSettingsRecord.workspace_id == self.workspace_id,
        )
        if lock:
            query = query.with_for_update()
        row = self.session.scalar(query)
        return {
            "enabled": bool(row.enabled) if row else False,
            "paused": bool(row.paused) if row else True,
            "max_sends_per_day": int(row.max_sends_per_day) if row else 0,
            "updated_by": row.updated_by if row else None,
            "updated_at": row.updated_at.isoformat() if row else None,
        }

    def set_automation_settings(
        self, *, enabled: bool, paused: bool, max_sends_per_day: int,
        actor: str, now: datetime,
    ) -> dict:
        if not 0 <= max_sends_per_day <= 100:
            raise ValueError("Daily sends must be between 0 and 100")
        if enabled and max_sends_per_day == 0:
            raise ValueError("Choose a nonzero daily cap before enabling")
        row = self.session.scalar(select(SalesAutomationSettingsRecord).where(
            SalesAutomationSettingsRecord.tenant_id == self.tenant_id,
            SalesAutomationSettingsRecord.workspace_id == self.workspace_id,
        ).with_for_update())
        if row is None:
            row = SalesAutomationSettingsRecord(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                enabled=enabled, paused=paused,
                max_sends_per_day=max_sends_per_day,
                updated_by=actor, updated_at=now,
            )
            self.session.add(row)
        else:
            row.enabled = enabled
            row.paused = paused
            row.max_sends_per_day = max_sends_per_day
            row.updated_by = actor
            row.updated_at = now
        return {
            "enabled": enabled, "paused": paused,
            "max_sends_per_day": max_sends_per_day,
            "updated_by": actor, "updated_at": now.isoformat(),
        }

    def record_consent(
        self, *, lead_id: str, granted: bool,
        evidence_message_id: str | None, evidence_note: str,
        actor: str, now: datetime,
    ) -> Lead:
        if not 3 <= len(evidence_note.strip()) <= 500:
            raise ValueError("Consent evidence explanation is required")
        current = self.get(lead_id)
        if current is None:
            raise ValueError("Lead not found")
        result = self.session.execute(update(SalesLeadRecord).where(
            SalesLeadRecord.id == lead_id,
            SalesLeadRecord.tenant_id == self.tenant_id,
            SalesLeadRecord.workspace_id == self.workspace_id,
            SalesLeadRecord.revision == current.revision,
        ).values(
            marketing_consent=granted,
            revision=current.revision + 1,
            updated_at=now,
        ))
        if result.rowcount != 1:
            raise ValueError("Lead revision conflict")
        self.session.add(SalesConsentEventRecord(
            id=str(uuid4()), tenant_id=self.tenant_id,
            workspace_id=self.workspace_id, lead_id=lead_id,
            evidence_message_id=evidence_message_id,
            evidence_note=evidence_note.strip(), granted=granted,
            recorded_by=actor, recorded_at=now,
        ))
        self._add_event(LeadEvent(
            lead_id=lead_id, tenant_id=self.tenant_id,
            workspace_id=self.workspace_id, actor_id=actor,
            event="lead.marketing_consent_granted" if granted else "lead.marketing_consent_revoked",
            occurred_at=now, from_status=current.status,
            to_status=current.status, revision=current.revision + 1,
        ))
        return self.get(lead_id) or current

    def get_followup(self, *, lead_id: str, followup_id: str) -> SalesFollowUpRecord | None:
        return self.session.scalar(select(SalesFollowUpRecord).where(
            SalesFollowUpRecord.id == followup_id,
            SalesFollowUpRecord.lead_id == lead_id,
            SalesFollowUpRecord.tenant_id == self.tenant_id,
            SalesFollowUpRecord.workspace_id == self.workspace_id,
        ))

    def approve_followup(
        self, *, lead_id: str, followup_id: str,
        template_name: str, language: str, parameters: list[str],
        approval_hash: str, actor: str, now: datetime,
    ) -> bool:
        result = self.session.execute(update(SalesFollowUpRecord).where(
            SalesFollowUpRecord.id == followup_id,
            SalesFollowUpRecord.lead_id == lead_id,
            SalesFollowUpRecord.tenant_id == self.tenant_id,
            SalesFollowUpRecord.workspace_id == self.workspace_id,
            SalesFollowUpRecord.status == "proposed",
            SalesFollowUpRecord.channel == "whatsapp",
        ).values(
            status="approved",
            template_name=template_name,
            template_language=language,
            template_parameters=parameters,
            payload_hash=approval_hash,
            approved_by=actor, approved_at=now,
            last_error=None,
        ))
        return result.rowcount == 1

    def cancel_followup(self, *, lead_id: str, followup_id: str) -> bool:
        result = self.session.execute(update(SalesFollowUpRecord).where(
            SalesFollowUpRecord.id == followup_id,
            SalesFollowUpRecord.lead_id == lead_id,
            SalesFollowUpRecord.tenant_id == self.tenant_id,
            SalesFollowUpRecord.workspace_id == self.workspace_id,
            SalesFollowUpRecord.status.in_(["proposed","approved","held"]),
        ).values(status="cancelled"))
        return result.rowcount == 1

    def consent_history(self, *, lead_id: str) -> list[dict]:
        rows = self.session.scalars(select(SalesConsentEventRecord).where(
            SalesConsentEventRecord.lead_id == lead_id,
            SalesConsentEventRecord.tenant_id == self.tenant_id,
            SalesConsentEventRecord.workspace_id == self.workspace_id,
        ).order_by(SalesConsentEventRecord.recorded_at.desc()).limit(100)).all()
        return [{
            "granted": row.granted, "recorded_by": row.recorded_by,
            "recorded_at": row.recorded_at.isoformat(),
            "evidence_note": row.evidence_note,
            "evidence_message_id": row.evidence_message_id,
        } for row in rows]
