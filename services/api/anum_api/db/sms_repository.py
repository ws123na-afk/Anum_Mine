"""PostgreSQL store for the iPhone SMS integration and the journal (migration ``0131_finance_sms_ledger``).

The session carries the tenant, workspace and user RLS context (``open_sms_stores`` opens it with
``user_scoped=True``); every table also has a restrictive ``owner_user_id = anum.user_id`` policy,
and every query here repeats the scope and the owner. An entry's postings must balance per
currency when the transaction commits (a deferred constraint trigger), and postings are never
updated.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, Integer, Numeric, String, Text, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.finance.ledger import SUSPENSE, EntryStatus, JournalEntry, Posting
from anum_api.finance.sms_store import MessageStatus, SmsDevice, SmsMessage, SmsRule, SmsSettings
from anum_api.schemas import TenantContext

from .models import Base

TS = DateTime(timezone=True)


class _Owned:
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)


class FinanceSmsSettingsRecord(Base):
    __tablename__ = "finance_sms_settings"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceSmsDeviceRecord(Base, _Owned):
    __tablename__ = "finance_sms_devices"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10))
    token_hash: Mapped[str] = mapped_column(String(64))
    token_hint: Mapped[str] = mapped_column(String(8))
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS)
    rotated_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer)


class FinanceSmsMessageRecord(Base, _Owned):
    __tablename__ = "finance_sms_messages"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    device_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str] = mapped_column(String(10))
    idem_key: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(TS)
    sender: Mapped[str] = mapped_column(String(60))
    bank_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    body_masked: Mapped[str] = mapped_column(Text)
    body_purge_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    status: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rule: Mapped[str] = mapped_column(String(100))
    entry_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    duplicate_of: Mapped[str | None] = mapped_column(String(80), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    observed_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    observed_account_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceSmsRuleRecord(Base, _Owned):
    __tablename__ = "finance_sms_rules"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sender: Mapped[str] = mapped_column(String(60))
    bank_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pattern: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30))
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(10))
    from_message_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    hits: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceJournalEntryRecord(Base, _Owned):
    __tablename__ = "finance_journal_entries"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    canonical_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    posted_on: Mapped[date] = mapped_column(Date)
    occurred_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    kind: Mapped[str] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    merchant: Mapped[str | None] = mapped_column(String(60), nullable=True)
    description: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(12))
    source_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(10))
    reverses_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reversed_by_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    original_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    original_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    fx_fee: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    reconciled_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)


class FinancePostingRecord(Base, _Owned):
    __tablename__ = "finance_postings"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    entry_id: Mapped[str] = mapped_column(String(80))
    ledger_account: Mapped[str] = mapped_column(String(120))
    account_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(TS)


# Children first: privacy deletion removes postings before their entries.
RECORDS = (FinancePostingRecord, FinanceJournalEntryRecord, FinanceSmsRuleRecord, FinanceSmsMessageRecord, FinanceSmsDeviceRecord,
           FinanceSmsSettingsRecord)


def _mine(record: type[Any], context: TenantContext) -> tuple[Any, ...]:
    return (record.tenant_id == context.tenant_id, record.workspace_id == context.workspace_id, record.owner_user_id == context.user_id)


def _values(model: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    return {key: getattr(value, "value", value) for key, value in model.__dict__.items() if key not in (exclude or set())}


def _load(record: Any, model: Any) -> Any:
    return model.model_validate({name: getattr(record, name) for name in model.model_fields if hasattr(record, name)})


class SqlAlchemySmsStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    # settings
    def get_settings(self, context: TenantContext) -> SmsSettings | None:
        row = self.session.scalar(select(FinanceSmsSettingsRecord).where(*_mine(FinanceSmsSettingsRecord, context)))
        if row is None:
            return None
        return SmsSettings.model_validate({**row.data, "version": row.version})

    def save_settings(self, context: TenantContext, settings: SmsSettings, *, expected_version: int) -> bool:
        record = FinanceSmsSettingsRecord
        data = settings.model_dump(mode="json", exclude={"version"})
        now = settings.updated_at or datetime.now().astimezone()
        if expected_version == 0:
            inserted = self.session.execute(
                insert(record).values(tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                                      data=data, version=settings.version, updated_at=now).on_conflict_do_nothing().returning(record.version))
            if inserted.first() is not None:
                return True
        result = self.session.execute(update(record).where(*_mine(record, context), record.version == expected_version)
                                      .values(data=data, version=settings.version, updated_at=now))
        return bool(result.rowcount)

    # devices
    def list_devices(self, context: TenantContext) -> list[SmsDevice]:
        record = FinanceSmsDeviceRecord
        rows = self.session.scalars(select(record).where(*_mine(record, context)).order_by(record.created_at.desc()).limit(50))
        return [_load(r, SmsDevice) for r in rows]

    def get_device(self, context: TenantContext, device_id: str) -> SmsDevice | None:
        record = FinanceSmsDeviceRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.id == device_id))
        return _load(row, SmsDevice) if row else None

    def save_device(self, device: SmsDevice) -> SmsDevice:
        values = _values(device)
        self.session.execute(insert(FinanceSmsDeviceRecord).values(**values).on_conflict_do_update(
            index_elements=["id"], set_={k: v for k, v in values.items() if k not in ("id", "tenant_id", "workspace_id", "owner_user_id")}))
        return device

    # messages
    def get_message(self, context: TenantContext, message_id: str) -> SmsMessage | None:
        record = FinanceSmsMessageRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.id == message_id))
        return _load(row, SmsMessage) if row else None

    def message_by_key(self, context: TenantContext, idem_key: str) -> SmsMessage | None:
        record = FinanceSmsMessageRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.idem_key == idem_key))
        return _load(row, SmsMessage) if row else None

    def add_message(self, message: SmsMessage) -> bool:
        inserted = self.session.execute(insert(FinanceSmsMessageRecord).values(**_values(message)).on_conflict_do_nothing()
                                        .returning(FinanceSmsMessageRecord.id))
        return inserted.first() is not None

    def save_message(self, message: SmsMessage) -> SmsMessage:
        values = _values(message)
        self.session.execute(insert(FinanceSmsMessageRecord).values(**values).on_conflict_do_update(
            index_elements=["id"], set_={k: v for k, v in values.items() if k not in ("id", "tenant_id", "workspace_id", "owner_user_id")}))
        return message

    def list_messages(self, context: TenantContext, *, status: str | None, limit: int, offset: int) -> tuple[list[SmsMessage], int]:
        record = FinanceSmsMessageRecord
        where = [*_mine(record, context)]
        if status is not None:
            where.append(record.status == status)
        total = int(self.session.scalar(select(func.count()).select_from(record).where(*where)) or 0)
        rows = self.session.scalars(select(record).where(*where).order_by(record.received_at.desc(), record.id.desc()).offset(offset).limit(limit))
        return [_load(r, SmsMessage) for r in rows], total

    def count_since(self, context: TenantContext, device_id: str, since: datetime) -> int:
        record = FinanceSmsMessageRecord
        return int(self.session.scalar(select(func.count()).select_from(record).where(
            *_mine(record, context), record.device_id == device_id, record.created_at >= since)) or 0)

    def status_counts(self, context: TenantContext) -> dict[str, int]:
        record = FinanceSmsMessageRecord
        rows = self.session.execute(select(record.status, func.count()).where(*_mine(record, context)).group_by(record.status))
        return {str(status): int(count) for status, count in rows}

    def purge_bodies(self, context: TenantContext, now: datetime) -> int:
        record = FinanceSmsMessageRecord
        result = self.session.execute(update(record).where(*_mine(record, context), record.body_masked != "", record.body_purge_at <= now)
                                      .values(body_masked="", body_purge_at=None))
        return int(result.rowcount or 0)

    def latest_observations(self, context: TenantContext) -> dict[str, tuple[Decimal, datetime]]:
        record = FinanceSmsMessageRecord
        rows = self.session.execute(
            select(record.observed_account_id, record.observed_balance, record.received_at)
            .where(*_mine(record, context), record.observed_account_id.is_not(None), record.observed_balance.is_not(None),
                   record.status.in_((MessageStatus.POSTED.value, MessageStatus.DUPLICATE.value)))
            .order_by(record.received_at).limit(20_000))
        out: dict[str, tuple[Decimal, datetime]] = {}
        for account_id, balance, received in rows:
            out[str(account_id)] = (balance, received)
        return out

    # rules
    def list_rules(self, context: TenantContext) -> list[SmsRule]:
        record = FinanceSmsRuleRecord
        rows = self.session.scalars(select(record).where(*_mine(record, context)).order_by(record.created_at.desc()).limit(200))
        return [_load(r, SmsRule) for r in rows]

    def get_rule(self, context: TenantContext, rule_id: str) -> SmsRule | None:
        record = FinanceSmsRuleRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.id == rule_id))
        return _load(row, SmsRule) if row else None

    def save_rule(self, rule: SmsRule) -> SmsRule:
        values = _values(rule)
        self.session.execute(insert(FinanceSmsRuleRecord).values(**values).on_conflict_do_update(
            index_elements=["id"], set_={k: v for k, v in values.items() if k not in ("id", "tenant_id", "workspace_id", "owner_user_id")}))
        return rule

    def delete_rule(self, context: TenantContext, rule_id: str) -> bool:
        record = FinanceSmsRuleRecord
        return bool(self.session.execute(delete(record).where(*_mine(record, context), record.id == rule_id)).rowcount)

    # journal
    def add_entry(self, entry: JournalEntry) -> JournalEntry:
        self.session.execute(insert(FinanceJournalEntryRecord).values(**_values(entry, {"postings"})))
        if entry.postings:
            self.session.execute(insert(FinancePostingRecord).values([_values(p) for p in entry.postings]))
        return entry

    def _with_postings(self, rows: list[FinanceJournalEntryRecord]) -> list[JournalEntry]:
        if not rows:
            return []
        ids = [r.id for r in rows]
        postings: dict[str, list[Posting]] = {}
        for p in self.session.scalars(select(FinancePostingRecord).where(FinancePostingRecord.entry_id.in_(ids)).order_by(FinancePostingRecord.id)):
            postings.setdefault(p.entry_id, []).append(_load(p, Posting))
        out = []
        for r in rows:
            entry = _load(r, JournalEntry)
            entry.postings = postings.get(r.id, [])
            out.append(entry)
        return out

    def get_entry(self, context: TenantContext, entry_id: str) -> JournalEntry | None:
        record = FinanceJournalEntryRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.id == entry_id))
        found = self._with_postings([row] if row else [])
        return found[0] if found else None

    def mark_entry(self, context: TenantContext, entry_id: str, *, status: EntryStatus | None = None, reversed_by_id: str | None = None,
                   reconciled_at: datetime | None = None, reconciled_ref: str | None = None, clear_reconciled: bool = False) -> None:
        values: dict[str, Any] = {}
        if status is not None:
            values["status"] = status.value
        if reversed_by_id is not None:
            values["reversed_by_id"] = reversed_by_id
        if reconciled_at is not None:
            values["reconciled_at"], values["reconciled_ref"] = reconciled_at, reconciled_ref
        if clear_reconciled:
            values["reconciled_at"], values["reconciled_ref"] = None, None
        if values:
            record = FinanceJournalEntryRecord
            self.session.execute(update(record).where(*_mine(record, context), record.id == entry_id).values(**values))

    def list_entries(self, context: TenantContext, *, start: date | None = None, end: date | None = None, limit: int = 50_000) -> list[JournalEntry]:
        record = FinanceJournalEntryRecord
        where = [*_mine(record, context)]
        if start is not None:
            where.append(record.posted_on >= start)
        if end is not None:
            where.append(record.posted_on <= end)
        rows = list(self.session.scalars(select(record).where(*where).order_by(record.posted_on.desc(), record.created_at.desc(), record.id.desc()).limit(limit)))
        return self._with_postings(rows)

    def chain(self, context: TenantContext, canonical_id: str) -> list[JournalEntry]:
        record = FinanceJournalEntryRecord
        rows = list(self.session.scalars(select(record).where(*_mine(record, context), record.canonical_id == canonical_id)
                                        .order_by(record.created_at, record.id).limit(500)))
        return self._with_postings(rows)

    def entries_with_fingerprint(self, context: TenantContext, fingerprint: str, *, start: date | None = None,
                                 end: date | None = None) -> list[JournalEntry]:
        """Every entry with the fingerprint posted from ``start`` to ``end``, newest first (ties by creation and id):
        complete for the window and in one fixed order, never an arbitrary first page."""
        record = FinanceJournalEntryRecord
        clauses = [*_mine(record, context), record.fingerprint == fingerprint]
        if start is not None:
            clauses.append(record.posted_on >= start)
        if end is not None:
            clauses.append(record.posted_on <= end)
        rows = list(self.session.scalars(select(record).where(*clauses).order_by(record.posted_on.desc(), record.created_at.desc(), record.id.desc())))
        return self._with_postings(rows)

    def entry_for_transaction(self, context: TenantContext, transaction_id: str) -> JournalEntry | None:
        posting, record = FinancePostingRecord, FinanceJournalEntryRecord
        # The active entry behind the row: a corrected row also has reversed entries in its chain.
        entry_id = self.session.scalar(
            select(posting.entry_id).join(record, record.id == posting.entry_id)
            .where(*_mine(posting, context), posting.transaction_id == transaction_id, record.status == EntryStatus.POSTED.value,
                   record.source != "reversal")
            .order_by(record.created_at.desc(), record.id.desc()).limit(1))
        if entry_id is None:
            return None
        return self.get_entry(context, entry_id)

    def linked_transaction_ids(self, context: TenantContext) -> set[str]:
        posting = FinancePostingRecord
        return {str(t) for t in self.session.scalars(select(posting.transaction_id).where(*_mine(posting, context), posting.transaction_id.is_not(None)))}

    def suspense_totals(self, context: TenantContext) -> dict[str, Decimal]:
        posting = FinancePostingRecord
        rows = self.session.execute(select(posting.currency, func.sum(posting.amount)).where(*_mine(posting, context), posting.ledger_account == SUSPENSE)
                                    .group_by(posting.currency))
        return {str(c): Decimal(v) for c, v in rows}

    def entry_sources(self, context: TenantContext, entry_ids: list[str]) -> dict[str, list[SmsMessage]]:
        if not entry_ids:
            return {}
        record = FinanceSmsMessageRecord
        rows = self.session.scalars(select(record).where(
            *_mine(record, context), record.entry_id.in_(entry_ids),
            record.status.in_((MessageStatus.POSTED.value, MessageStatus.ATTACHED.value, MessageStatus.DUPLICATE.value))).order_by(record.received_at))
        out: dict[str, list[SmsMessage]] = {}
        for row in rows:
            out.setdefault(str(row.entry_id), []).append(_load(row, SmsMessage))
        return out

    def lock_owner(self, context: TenantContext) -> None:
        """One owner's ingest at a time across every API process: a transaction-level advisory lock,
        released at commit. The unit of work that takes it is short and calls no model or provider."""
        key = f"finance_sms:{context.tenant_id}:{context.workspace_id}:{context.user_id}"
        self.session.execute(text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})

    # privacy
    def count_everything(self, context: TenantContext) -> int:
        return sum(int(self.session.scalar(select(func.count()).select_from(r).where(*_mine(r, context))) or 0) for r in RECORDS)

    def delete_everything(self, context: TenantContext) -> int:
        return sum(int(self.session.execute(delete(r).where(*_mine(r, context))).rowcount or 0) for r in RECORDS)


__all__ = ["RECORDS", "SqlAlchemySmsStore"]
