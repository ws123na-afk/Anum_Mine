"""PostgreSQL store for the owner's rules, merchant aliases, subcategories, suggestion decisions, rule batches and
automation settings (migration ``0152_finance_rules_learning``, docs/finance.md#rules-and-learning).

The session carries the tenant, workspace and user RLS context (``rules_store_for`` reuses the finance store's
session); the tables also have a restrictive ``owner_user_id = anum.user_id`` policy, and every query repeats the
scope and the owner. Claims (a version decision, a suggestion decision, a batch undo, the daily notice) are single
``UPDATE ... WHERE`` or ``INSERT ... ON CONFLICT DO NOTHING`` statements, so of two concurrent requests one wins.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.finance.rules import (
    AutomationPolicy,
    AutomationSettings,
    FinanceRule,
    MerchantAlias,
    RuleContent,
    Subcategory,
    fold,
)
from anum_api.finance.rules_store import BatchChange, RuleBatch, SuggestionDecision
from anum_api.schemas import TenantContext

from .models import Base

TS = DateTime(timezone=True)


class _Owned:
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)


class FinanceRuleRecord(Base, _Owned):
    __tablename__ = "finance_rules"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    provenance: Mapped[str] = mapped_column(String(10))
    source_transaction_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(160))
    decided_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)
    decided_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    reverted_from: Mapped[int | None] = mapped_column(Integer, nullable=True)


class FinanceMerchantAliasRecord(Base, _Owned):
    __tablename__ = "finance_merchant_aliases"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    alias: Mapped[str] = mapped_column(String(80))
    alias_key: Mapped[str] = mapped_column(String(160))
    merchant: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceSubcategoryRecord(Base, _Owned):
    __tablename__ = "finance_subcategories"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    parent: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(60))
    name_key: Mapped[str] = mapped_column(String(120))
    archived: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceSuggestionDecisionRecord(Base, _Owned):
    __tablename__ = "finance_suggestion_decisions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(200))
    producer: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    payload_hash: Mapped[str] = mapped_column(String(64))
    decided_by: Mapped[str] = mapped_column(String(160))
    decided_at: Mapped[datetime] = mapped_column(TS)
    batch_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class FinanceRuleBatchRecord(Base, _Owned):
    __tablename__ = "finance_rule_batches"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    origin: Mapped[str] = mapped_column(String(10))
    rule_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggestion_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(10))
    created_by: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(TS)
    undone_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    undone_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class FinanceAutomationSettingsRecord(Base, _Owned):
    __tablename__ = "finance_automation_settings"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB)
    notified_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(160), nullable=True)


class FinanceNoticeClaimRecord(Base, _Owned):
    """Migration 0157_finance_evidence: one exception notice per person, kind, item and day (docs/finance.md#exception-notices)."""

    __tablename__ = "finance_notice_claims"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    item_key: Mapped[str] = mapped_column(String(200))
    day: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(TS)


RECORDS = (FinanceRuleBatchRecord, FinanceSuggestionDecisionRecord, FinanceRuleRecord, FinanceMerchantAliasRecord, FinanceSubcategoryRecord,
           FinanceAutomationSettingsRecord, FinanceNoticeClaimRecord)


def _mine(record: Any, context: TenantContext) -> list[Any]:
    return [record.tenant_id == context.tenant_id, record.workspace_id == context.workspace_id, record.owner_user_id == context.user_id]


def _owner(item: Any) -> dict[str, str]:
    return {"tenant_id": item.tenant_id, "workspace_id": item.workspace_id, "owner_user_id": item.owner_user_id}


def _ctx(item: Any) -> TenantContext:
    return TenantContext(tenant_id=item.tenant_id, workspace_id=item.workspace_id, user_id=item.owner_user_id)


class SqlAlchemyRulesStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    # rules ---------------------------------------------------------------------------------------

    @staticmethod
    def _rule(row: FinanceRuleRecord) -> FinanceRule:
        return FinanceRule(
            id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, owner_user_id=row.owner_user_id, rule_id=row.rule_id,
            version=row.version, status=row.status, content=RuleContent.model_validate(row.content), provenance=row.provenance,  # type: ignore[arg-type]
            source_transaction_id=row.source_transaction_id, confidence=row.confidence, payload_hash=row.payload_hash, created_by=row.created_by,
            decided_by=row.decided_by, created_at=row.created_at, decided_at=row.decided_at, reverted_from=row.reverted_from,
        )

    def list_versions(self, context: TenantContext, rule_id: str | None = None) -> list[FinanceRule]:
        record = FinanceRuleRecord
        clauses = _mine(record, context) + ([record.rule_id == rule_id] if rule_id is not None else [])
        return [self._rule(r) for r in self.session.scalars(select(record).where(*clauses).order_by(record.rule_id, record.version))]

    def add_version(self, rule: FinanceRule) -> bool:
        values = {**_owner(rule), "id": rule.id, "rule_id": rule.rule_id, "version": rule.version, "status": rule.status,
                  "content": rule.content.model_dump(mode="json"), "provenance": rule.provenance, "source_transaction_id": rule.source_transaction_id,
                  "confidence": rule.confidence, "payload_hash": rule.payload_hash, "created_by": rule.created_by, "decided_by": rule.decided_by,
                  "created_at": rule.created_at, "decided_at": rule.decided_at, "reverted_from": rule.reverted_from}
        result = self.session.execute(insert(FinanceRuleRecord).values(**values).on_conflict_do_nothing().returning(FinanceRuleRecord.id))
        return result.first() is not None

    def decide_version(self, context: TenantContext, rule_id: str, version: int, status: str, by: str, at: datetime) -> bool:
        record = FinanceRuleRecord
        result = self.session.execute(update(record).where(*_mine(record, context), record.rule_id == rule_id, record.version == version,
                                                            record.status == "proposed").values(status=status, decided_by=by, decided_at=at))
        return bool(result.rowcount)

    def count_rule_ids(self, context: TenantContext) -> int:
        record = FinanceRuleRecord
        return int(self.session.scalar(select(func.count(func.distinct(record.rule_id))).where(*_mine(record, context))) or 0)

    # aliases and subcategories ---------------------------------------------------------------------

    def list_aliases(self, context: TenantContext) -> list[MerchantAlias]:
        record = FinanceMerchantAliasRecord
        rows = self.session.scalars(select(record).where(*_mine(record, context)).order_by(func.lower(record.merchant), func.lower(record.alias)))
        return [MerchantAlias(id=r.id, tenant_id=r.tenant_id, workspace_id=r.workspace_id, owner_user_id=r.owner_user_id, alias=r.alias,
                              merchant=r.merchant, created_at=r.created_at, updated_at=r.updated_at) for r in rows]

    def save_alias(self, alias: MerchantAlias) -> None:
        record = FinanceMerchantAliasRecord
        values = {"alias": alias.alias, "alias_key": fold(alias.alias)[:160], "merchant": alias.merchant, "updated_at": alias.updated_at}
        try:
            with self.session.begin_nested():
                existing = self.session.scalar(select(record).where(*_mine(record, _ctx(alias)), record.id == alias.id))
                if existing is None:
                    self.session.add(record(id=alias.id, **_owner(alias), created_at=alias.created_at, **values))
                else:
                    for key, value in values.items():
                        setattr(existing, key, value)
                self.session.flush()
        except IntegrityError:
            raise ValueError("duplicate alias") from None

    def delete_alias(self, context: TenantContext, alias_id: str) -> bool:
        record = FinanceMerchantAliasRecord
        return bool(self.session.execute(delete(record).where(*_mine(record, context), record.id == alias_id)).rowcount)

    def list_subcategories(self, context: TenantContext) -> list[Subcategory]:
        record = FinanceSubcategoryRecord
        rows = self.session.scalars(select(record).where(*_mine(record, context)).order_by(record.parent, func.lower(record.name)))
        return [Subcategory(id=r.id, tenant_id=r.tenant_id, workspace_id=r.workspace_id, owner_user_id=r.owner_user_id, parent=r.parent,  # type: ignore[arg-type]
                            name=r.name, archived=r.archived, created_at=r.created_at, updated_at=r.updated_at) for r in rows]

    def save_subcategory(self, item: Subcategory) -> None:
        record = FinanceSubcategoryRecord
        values = {"parent": item.parent.value, "name": item.name, "name_key": item.name.casefold()[:120], "archived": item.archived,
                  "updated_at": item.updated_at}
        try:
            with self.session.begin_nested():
                existing = self.session.scalar(select(record).where(*_mine(record, _ctx(item)), record.id == item.id))
                if existing is None:
                    self.session.add(record(id=item.id, **_owner(item), created_at=item.created_at, **values))
                else:
                    for key, value in values.items():
                        setattr(existing, key, value)
                self.session.flush()
        except IntegrityError:
            raise ValueError("duplicate subcategory") from None

    # suggestion decisions ------------------------------------------------------------------------

    @staticmethod
    def _decision(r: FinanceSuggestionDecisionRecord) -> SuggestionDecision:
        return SuggestionDecision(id=r.id, tenant_id=r.tenant_id, workspace_id=r.workspace_id, owner_user_id=r.owner_user_id, key=r.key,
                                  producer=r.producer, status=r.status, payload_hash=r.payload_hash, decided_by=r.decided_by,  # type: ignore[arg-type]
                                  decided_at=r.decided_at, batch_id=r.batch_id)

    def get_decision(self, context: TenantContext, key: str) -> SuggestionDecision | None:
        record = FinanceSuggestionDecisionRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.key == key))
        return self._decision(row) if row is not None else None

    def list_decisions(self, context: TenantContext, keys: list[str] | None = None) -> list[SuggestionDecision]:
        record = FinanceSuggestionDecisionRecord
        clauses = _mine(record, context)
        if keys is not None:
            if not keys:
                return []
            clauses.append(record.key.in_(keys))
        return [self._decision(r) for r in self.session.scalars(select(record).where(*clauses))]

    def claim_decision(self, decision: SuggestionDecision) -> bool:
        values = {**_owner(decision), "id": decision.id, "key": decision.key, "producer": decision.producer, "status": decision.status,
                  "payload_hash": decision.payload_hash, "decided_by": decision.decided_by, "decided_at": decision.decided_at, "batch_id": decision.batch_id}
        statement = insert(FinanceSuggestionDecisionRecord).values(**values).on_conflict_do_nothing(
            index_elements=["tenant_id", "workspace_id", "owner_user_id", "key"])
        return self.session.execute(statement.returning(FinanceSuggestionDecisionRecord.id)).first() is not None

    def delete_decision(self, context: TenantContext, key: str) -> bool:
        record = FinanceSuggestionDecisionRecord
        return bool(self.session.execute(delete(record).where(*_mine(record, context), record.key == key)).rowcount)

    # batches -------------------------------------------------------------------------------------

    @staticmethod
    def _batch(r: FinanceRuleBatchRecord) -> RuleBatch:
        return RuleBatch(id=r.id, tenant_id=r.tenant_id, workspace_id=r.workspace_id, owner_user_id=r.owner_user_id, origin=r.origin,  # type: ignore[arg-type]
                         rule_id=r.rule_id, rule_version=r.rule_version, suggestion_key=r.suggestion_key,
                         changes=[BatchChange.model_validate(c) for c in r.changes], status=r.status, created_by=r.created_by,  # type: ignore[arg-type]
                         created_at=r.created_at, undone_by=r.undone_by, undone_at=r.undone_at)

    def save_batch(self, batch: RuleBatch) -> None:
        values = {**_owner(batch), "id": batch.id, "origin": batch.origin, "rule_id": batch.rule_id, "rule_version": batch.rule_version,
                  "suggestion_key": batch.suggestion_key, "changes": [c.model_dump(mode="json") for c in batch.changes], "status": batch.status,
                  "created_by": batch.created_by, "created_at": batch.created_at, "undone_by": batch.undone_by, "undone_at": batch.undone_at}
        self.session.execute(insert(FinanceRuleBatchRecord).values(**values).on_conflict_do_nothing())

    def get_batch(self, context: TenantContext, batch_id: str) -> RuleBatch | None:
        record = FinanceRuleBatchRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.id == batch_id))
        return self._batch(row) if row is not None else None

    def list_batches(self, context: TenantContext, *, limit: int, offset: int) -> tuple[list[RuleBatch], int]:
        record = FinanceRuleBatchRecord
        total = int(self.session.scalar(select(func.count()).select_from(record).where(*_mine(record, context))) or 0)
        rows = self.session.scalars(select(record).where(*_mine(record, context)).order_by(record.created_at.desc(), record.id).limit(limit).offset(offset))
        return [self._batch(r) for r in rows], total

    def claim_undo(self, context: TenantContext, batch_id: str, by: str, at: datetime) -> bool:
        record = FinanceRuleBatchRecord
        result = self.session.execute(update(record).where(*_mine(record, context), record.id == batch_id, record.status == "applied")
                                      .values(status="undone", undone_by=by, undone_at=at))
        return bool(result.rowcount)

    # automation ----------------------------------------------------------------------------------

    def get_automation(self, context: TenantContext) -> AutomationSettings | None:
        record = FinanceAutomationSettingsRecord
        row = self.session.scalar(select(record).where(*_mine(record, context)))
        if row is None:
            return None
        return AutomationSettings(tenant_id=row.tenant_id, workspace_id=row.workspace_id, owner_user_id=row.owner_user_id,
                                  policy=AutomationPolicy.model_validate(row.policy), notified_on=row.notified_on, updated_at=row.updated_at,
                                  updated_by=row.updated_by)

    def save_automation(self, settings: AutomationSettings) -> None:
        values = {"policy": settings.policy.model_dump(mode="json"), "updated_at": settings.updated_at, "updated_by": settings.updated_by}
        statement = insert(FinanceAutomationSettingsRecord).values(id=f"fin_auto_{settings.owner_user_id}"[:80], **_owner(settings), notified_on=None, **values)
        self.session.execute(statement.on_conflict_do_update(index_elements=["tenant_id", "workspace_id", "owner_user_id"], set_=values))

    def claim_notice(self, context: TenantContext, day: str) -> bool:
        record = FinanceAutomationSettingsRecord
        result = self.session.execute(update(record).where(*_mine(record, context), (record.notified_on.is_(None)) | (record.notified_on != day))
                                      .values(notified_on=day))
        return bool(result.rowcount)

    def release_notice(self, context: TenantContext, day: str) -> None:
        record = FinanceAutomationSettingsRecord
        self.session.execute(update(record).where(*_mine(record, context), record.notified_on == day).values(notified_on=None))

    def claim_item_notices(self, context: TenantContext, kind: str, keys: list[str], day: date, now: datetime) -> list[str]:
        from anum_api.schemas import new_id

        claimed = []
        for key in keys:
            statement = insert(FinanceNoticeClaimRecord).values(
                id=new_id("fin_ntc"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id, kind=kind,
                item_key=key[:200], day=day, created_at=now,
            ).on_conflict_do_nothing(index_elements=["tenant_id", "workspace_id", "owner_user_id", "kind", "item_key", "day"])
            if self.session.execute(statement.returning(FinanceNoticeClaimRecord.id)).first() is not None:
                claimed.append(key)
        return claimed

    def release_item_notices(self, context: TenantContext, kind: str, keys: list[str], day: date) -> None:
        record = FinanceNoticeClaimRecord
        if keys:
            self.session.execute(delete(record).where(*_mine(record, context), record.kind == kind, record.day == day,
                                                      record.item_key.in_([k[:200] for k in keys])))

    def prune_item_notices(self, context: TenantContext, before: date) -> int:
        record = FinanceNoticeClaimRecord
        return int(self.session.execute(delete(record).where(*_mine(record, context), record.day < before)).rowcount or 0)

    # privacy -------------------------------------------------------------------------------------

    def count_everything(self, context: TenantContext) -> int:
        return sum(int(self.session.scalar(select(func.count()).select_from(r).where(*_mine(r, context))) or 0) for r in RECORDS)

    def delete_everything(self, context: TenantContext) -> int:
        return sum(int(self.session.execute(delete(r).where(*_mine(r, context))).rowcount or 0) for r in RECORDS)


__all__ = ["RECORDS", "SqlAlchemyRulesStore"]
