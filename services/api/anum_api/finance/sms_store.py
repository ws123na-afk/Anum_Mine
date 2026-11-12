"""Storage for the iPhone SMS integration and the journal (docs/finance-sms.md#storage).

Like ``ibkr_store``: a protocol, an in-memory store for local development and tests, and
``open_sms_stores`` which opens the finance store and this store in one owner-scoped unit of
work (``user_scoped=True``: tenant, workspace and user RLS context). With PostgreSQL the store
is ``anum_api.db.sms_repository.SqlAlchemySmsStore`` over the tables of migration
``0131_finance_sms_ledger``, each under forced RLS with the tenant policy and a restrictive
``owner_user_id = anum.user_id`` policy.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from threading import RLock
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .ledger import EntryStatus, JournalEntry, Posting
from .store import FinanceStore, finance_store

MAX_DEVICES = 5
MAX_RULES = 100
MAX_ACCOUNT_MAP = 50
MAX_SENDERS = 50


class LedgerKind(StrEnum):
    PERSONAL = "personal"
    BUSINESS = "business"


class AccountMapping(BaseModel):
    """A card or account the messages name by its last four digits."""

    last4: str = Field(pattern=r"^\d{4}$")
    account_id: str = Field(min_length=1, max_length=80)
    credit_limit: Decimal | None = None  # cards: for the utilization figure


class SenderMapping(BaseModel):
    """A sender name the person allowed, and the bank it is (a registry key or their own label)."""

    sender: str = Field(min_length=1, max_length=60)
    bank: str = Field(min_length=1, max_length=40)


class WalletCard(BaseModel):
    """An Apple Wallet card or pass (its name as Wallet shows it, and the virtual card's last four
    digits when known) and the account its payments hit. The owner sets these; ANUM never guesses."""

    card: str = Field(min_length=1, max_length=80)
    last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    account_id: str = Field(min_length=1, max_length=80)


class AiSettings(BaseModel):
    enabled: bool = False
    opted_in_at: datetime | None = None  # the person's explicit consent to send masked text to their model
    daily_cap: int = Field(default=20, ge=1, le=200)
    used_on: date | None = None
    used: int = 0


MAX_SENDER_TRACKS = 100
MAX_CHOICES = 300
MAX_CARD_MEMORY = 50
MAX_NOTICES = 20
MAX_LIMITS = 20


class AutoPostLimit(BaseModel):
    """The largest amount in one currency that may be recorded without the person's look (docs/finance-sms.md#learning)."""

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=3)


class LearningSettings(BaseModel):
    """Learning from the person's reviews. On by default; a sender is trusted after ``confirm_after`` readings recorded unchanged."""

    enabled: bool = True
    confirm_after: int = Field(default=2, ge=1, le=5)


class SenderTrack(BaseModel):
    """How the person's recent reviews of one sender went: readings recorded unchanged in a row, and the last correction."""

    sender: str = Field(min_length=1, max_length=60)
    clean: int = Field(default=0, ge=0)
    corrected_at: datetime | None = None
    auto_confirmed_at: datetime | None = None


class KindMemory(BaseModel):
    """What the person chose for money from or to one party of one sender: salary, a transfer from their own account..."""

    id: str
    sender: str = Field(max_length=60)
    direction: Literal["in", "out"]
    party: str = Field(max_length=80)  # ``rules.merchant_key`` of the merchant or other party
    party_label: str = Field(max_length=80)
    kind: str = Field(max_length=40)
    counter_account_id: str | None = None  # the person's own account an own transfer comes from or goes to
    count: int = Field(default=0, ge=0)
    active: bool = False
    since: datetime | None = None
    updated_at: datetime


class CategoryMemory(BaseModel):
    """The category the person chose for one merchant; at twice the same an approved ``learned`` finance rule does the work."""

    party: str = Field(max_length=80)
    party_label: str = Field(max_length=80)
    category: str = Field(max_length=40)
    count: int = Field(default=0, ge=0)
    rule_id: str | None = None  # the finance rule made from it (docs/finance.md#rules-and-learning)
    updated_at: datetime


class CardMemory(BaseModel):
    """An unmapped card or account ending and the account the person picked for it. Proposed, never mapped silently."""

    last4: str = Field(pattern=r"^\d{4}$")
    account_id: str = Field(max_length=80)
    count: int = Field(default=0, ge=0)
    declined: bool = False
    updated_at: datetime


class LearningNotice(BaseModel):
    """What ANUM learned, shown once on Review with its undo."""

    id: str
    kind: Literal["sender_trusted", "kind_remembered", "category_remembered", "card_proposed", "rechecked"]
    sender: str | None = None
    party: str | None = None
    value: str | None = None  # the kind or category; for ``rechecked`` how many messages were recorded
    last4: str | None = None
    account_id: str | None = None
    ref: str | None = None  # the kind memory id or the finance rule id
    created_at: datetime


class SmsMemory(BaseModel):
    senders: list[SenderTrack] = Field(default_factory=list, max_length=MAX_SENDER_TRACKS)
    kinds: list[KindMemory] = Field(default_factory=list, max_length=MAX_CHOICES)
    categories: list[CategoryMemory] = Field(default_factory=list, max_length=MAX_CHOICES)
    cards: list[CardMemory] = Field(default_factory=list, max_length=MAX_CARD_MEMORY)
    notices: list[LearningNotice] = Field(default_factory=list, max_length=MAX_NOTICES)


class SmsSettings(BaseModel):
    """Per person and workspace. Off until the person turns it on."""

    enabled: bool = False
    ledger_kind: LedgerKind = LedgerKind.PERSONAL
    auto_post: bool = True  # post a confident message from a confirmed format without review
    body_retention_days: int = Field(default=30, ge=0, le=365)
    verified_senders: list[str] = Field(default_factory=list, max_length=MAX_SENDERS)
    extra_senders: list[SenderMapping] = Field(default_factory=list, max_length=MAX_SENDERS)
    account_map: list[AccountMapping] = Field(default_factory=list, max_length=MAX_ACCOUNT_MAP)
    wallet_cards: list[WalletCard] = Field(default_factory=list, max_length=MAX_ACCOUNT_MAP)
    default_account_id: str | None = None  # the account a message is about when it names none
    cash_account_id: str | None = None  # where an ATM withdrawal goes (else it is cash spending)
    ai: AiSettings = Field(default_factory=AiSettings)
    self_test_passed_at: datetime | None = None
    # Per-integration switches on Settings, Integrations, iPhone (docs/iphone-integrations.md); a missing key
    # takes the integration's default. SMS forwarding itself is ``enabled``.
    integration_switches: dict[str, bool] = Field(default_factory=dict, max_length=20)
    # Learning from reviews (docs/finance-sms.md#learning). Older rows have none of these and read the defaults.
    learning: LearningSettings = Field(default_factory=LearningSettings)
    auto_post_limits: list[AutoPostLimit] = Field(default_factory=list, max_length=MAX_LIMITS)
    memory: SmsMemory = Field(default_factory=SmsMemory)
    version: int = 0
    updated_at: datetime | None = None


class DeviceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    REVOKED = "revoked"


class SmsDevice(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    name: str
    status: DeviceStatus
    token_hash: str  # SHA-256 of the secret part; the token is shown once and never stored
    token_hint: str  # the token's last four characters
    scopes: list[str] = Field(default_factory=lambda: list(DEVICE_SCOPES))  # the integrations this token may send to
    created_at: datetime
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_ok_at: datetime | None = None
    last_error: str | None = None  # a code, never a message body
    last_error_at: datetime | None = None
    message_count: int = 0


class MessageStatus(StrEnum):
    POSTED = "posted"
    REVIEW = "review"
    DUPLICATE = "duplicate"
    ATTACHED = "attached"  # another source of a transaction already recorded (Apple Wallet and SMS)
    REJECTED = "rejected"
    DISMISSED = "dismissed"
    FAILED = "failed"


MessageSource = Literal["device", "paste", "test", "wallet", "statement"]

# The scopes a device made on Finance, iPhone SMS gets. Every scope a token may hold, and the
# integrations behind them, are in ``finance.devices`` (Settings, Integrations, iPhone).
DEVICE_SCOPES = ("finance.sms", "finance.wallet")


def source_kind(source: str) -> str:
    """``wallet`` for Apple Wallet events, ``statement`` for statement lines, ``sms`` for messages (forwarded or pasted)."""
    if source in ("wallet", "statement"):
        return source
    return "sms"


class SmsMessage(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    device_id: str | None = None
    source: MessageSource
    idem_key: str  # SHA-256 of device, sender, text and minute: a retried delivery finds this row
    received_at: datetime
    sender: str  # normalized sender key
    bank_key: str | None = None
    body_masked: str = ""  # masked text, kept until ``body_purge_at``; never for refused messages
    body_purge_at: datetime | None = None
    status: MessageStatus
    reason: str | None = None
    parsed: dict[str, object] = Field(default_factory=dict)
    rule: str = "none"
    entry_id: str | None = None
    duplicate_of: str | None = None
    fingerprint: str | None = None
    observed_balance: Decimal | None = None
    observed_currency: str | None = None
    observed_account_id: str | None = None
    attempts: int = 1
    created_at: datetime
    updated_at: datetime


class RuleStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    DISABLED = "disabled"


class SmsRule(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    sender: str
    bank_key: str | None = None
    pattern: str
    kind: str
    currency: str | None = None
    category: str | None = None
    account_id: str | None = None
    status: RuleStatus
    from_message_id: str | None = None
    hits: int = 0
    created_at: datetime
    updated_at: datetime


class SmsStore(Protocol):
    def get_settings(self, context: TenantContext) -> SmsSettings | None: ...
    def save_settings(self, context: TenantContext, settings: SmsSettings, *, expected_version: int) -> bool: ...
    # devices
    def list_devices(self, context: TenantContext) -> list[SmsDevice]: ...
    def get_device(self, context: TenantContext, device_id: str) -> SmsDevice | None: ...
    def save_device(self, device: SmsDevice) -> SmsDevice: ...
    # messages
    def get_message(self, context: TenantContext, message_id: str) -> SmsMessage | None: ...
    def message_by_key(self, context: TenantContext, idem_key: str) -> SmsMessage | None: ...
    def add_message(self, message: SmsMessage) -> bool: ...
    def save_message(self, message: SmsMessage) -> SmsMessage: ...
    def list_messages(self, context: TenantContext, *, status: str | None, limit: int, offset: int) -> tuple[list[SmsMessage], int]: ...
    def count_since(self, context: TenantContext, device_id: str, since: datetime) -> int: ...
    def status_counts(self, context: TenantContext) -> dict[str, int]: ...
    def purge_bodies(self, context: TenantContext, now: datetime) -> int: ...
    def latest_observations(self, context: TenantContext) -> dict[str, tuple[Decimal, datetime]]: ...
    # rules
    def list_rules(self, context: TenantContext) -> list[SmsRule]: ...
    def get_rule(self, context: TenantContext, rule_id: str) -> SmsRule | None: ...
    def save_rule(self, rule: SmsRule) -> SmsRule: ...
    def delete_rule(self, context: TenantContext, rule_id: str) -> bool: ...
    # journal
    def add_entry(self, entry: JournalEntry) -> JournalEntry: ...
    def get_entry(self, context: TenantContext, entry_id: str) -> JournalEntry | None: ...
    def mark_entry(self, context: TenantContext, entry_id: str, *, status: EntryStatus | None = None, reversed_by_id: str | None = None,
                   reconciled_at: datetime | None = None, reconciled_ref: str | None = None, clear_reconciled: bool = False) -> None: ...
    def list_entries(self, context: TenantContext, *, start: date | None = None, end: date | None = None, limit: int = 50_000) -> list[JournalEntry]: ...
    def entries_with_fingerprint(self, context: TenantContext, fingerprint: str, *, start: date | None = None,
                                 end: date | None = None) -> list[JournalEntry]: ...
    def chain(self, context: TenantContext, canonical_id: str) -> list[JournalEntry]: ...
    def entry_for_transaction(self, context: TenantContext, transaction_id: str) -> JournalEntry | None: ...
    def linked_transaction_ids(self, context: TenantContext) -> set[str]: ...
    def suspense_totals(self, context: TenantContext) -> dict[str, Decimal]: ...
    def entry_sources(self, context: TenantContext, entry_ids: list[str]) -> dict[str, list[SmsMessage]]: ...
    def lock_owner(self, context: TenantContext) -> None: ...
    # privacy
    def count_everything(self, context: TenantContext) -> int: ...
    def delete_everything(self, context: TenantContext) -> int: ...


def _mine(item: object, context: TenantContext) -> bool:
    return (
        getattr(item, "tenant_id", None) == context.tenant_id
        and getattr(item, "workspace_id", None) == context.workspace_id
        and getattr(item, "owner_user_id", None) == context.user_id
    )


class InMemorySmsStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.clear()

    def clear(self) -> None:
        self.settings: dict[tuple[str, str, str], SmsSettings] = {}
        self.devices: dict[str, SmsDevice] = {}
        self.messages: dict[str, SmsMessage] = {}
        self.rules: dict[str, SmsRule] = {}
        self.entries: dict[str, JournalEntry] = {}

    @staticmethod
    def _key(context: TenantContext) -> tuple[str, str, str]:
        return (context.tenant_id, context.workspace_id, context.user_id)

    def get_settings(self, context: TenantContext) -> SmsSettings | None:
        found = self.settings.get(self._key(context))
        return found.model_copy(deep=True) if found else None

    def save_settings(self, context: TenantContext, settings: SmsSettings, *, expected_version: int) -> bool:
        with self._lock:
            current = self.settings.get(self._key(context))
            if (current.version if current else 0) != expected_version:
                return False
            self.settings[self._key(context)] = settings.model_copy(deep=True)
            return True

    def list_devices(self, context: TenantContext) -> list[SmsDevice]:
        return sorted((d.model_copy() for d in self.devices.values() if _mine(d, context)), key=lambda d: d.created_at, reverse=True)

    def get_device(self, context: TenantContext, device_id: str) -> SmsDevice | None:
        found = self.devices.get(device_id)
        return found.model_copy() if found and _mine(found, context) else None

    def save_device(self, device: SmsDevice) -> SmsDevice:
        with self._lock:
            self.devices[device.id] = device.model_copy()
            return device

    def get_message(self, context: TenantContext, message_id: str) -> SmsMessage | None:
        found = self.messages.get(message_id)
        return found.model_copy(deep=True) if found and _mine(found, context) else None

    def message_by_key(self, context: TenantContext, idem_key: str) -> SmsMessage | None:
        found = next((m for m in self.messages.values() if _mine(m, context) and m.idem_key == idem_key), None)
        return found.model_copy(deep=True) if found else None

    def add_message(self, message: SmsMessage) -> bool:
        with self._lock:
            context = TenantContext(tenant_id=message.tenant_id, workspace_id=message.workspace_id, user_id=message.owner_user_id)
            if self.message_by_key(context, message.idem_key) is not None:
                return False
            self.messages[message.id] = message.model_copy(deep=True)
            return True

    def save_message(self, message: SmsMessage) -> SmsMessage:
        with self._lock:
            self.messages[message.id] = message.model_copy(deep=True)
            return message

    def list_messages(self, context: TenantContext, *, status: str | None, limit: int, offset: int) -> tuple[list[SmsMessage], int]:
        rows = sorted((m for m in self.messages.values() if _mine(m, context) and (status is None or m.status == status)),
                      key=lambda m: (m.received_at, m.id), reverse=True)
        return [m.model_copy(deep=True) for m in rows[offset:offset + limit]], len(rows)

    def count_since(self, context: TenantContext, device_id: str, since: datetime) -> int:
        return sum(1 for m in self.messages.values() if _mine(m, context) and m.device_id == device_id and m.created_at >= since)

    def status_counts(self, context: TenantContext) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.messages.values():
            if _mine(m, context):
                counts[m.status.value] = counts.get(m.status.value, 0) + 1
        return counts

    def purge_bodies(self, context: TenantContext, now: datetime) -> int:
        with self._lock:
            count = 0
            for m in self.messages.values():
                if _mine(m, context) and m.body_masked and m.body_purge_at is not None and m.body_purge_at <= now:
                    m.body_masked, m.body_purge_at = "", None
                    count += 1
            return count

    def latest_observations(self, context: TenantContext) -> dict[str, tuple[Decimal, datetime]]:
        out: dict[str, tuple[Decimal, datetime]] = {}
        for m in sorted((m for m in self.messages.values() if _mine(m, context)), key=lambda m: m.received_at):
            if m.observed_balance is not None and m.observed_account_id and m.status in (MessageStatus.POSTED, MessageStatus.DUPLICATE):
                out[m.observed_account_id] = (m.observed_balance, m.received_at)
        return out

    def list_rules(self, context: TenantContext) -> list[SmsRule]:
        return sorted((r.model_copy() for r in self.rules.values() if _mine(r, context)), key=lambda r: r.created_at, reverse=True)

    def get_rule(self, context: TenantContext, rule_id: str) -> SmsRule | None:
        found = self.rules.get(rule_id)
        return found.model_copy() if found and _mine(found, context) else None

    def save_rule(self, rule: SmsRule) -> SmsRule:
        with self._lock:
            self.rules[rule.id] = rule.model_copy()
            return rule

    def delete_rule(self, context: TenantContext, rule_id: str) -> bool:
        with self._lock:
            found = self.rules.get(rule_id)
            if found is None or not _mine(found, context):
                return False
            del self.rules[rule_id]
            return True

    def add_entry(self, entry: JournalEntry) -> JournalEntry:
        from .ledger import balanced

        if not balanced(entry.postings):
            # The database refuses an unbalanced entry at commit; the memory store does too.
            raise ValueError("A journal entry must balance to zero in every currency")
        with self._lock:
            self.entries[entry.id] = entry.model_copy(deep=True)
            return entry

    def get_entry(self, context: TenantContext, entry_id: str) -> JournalEntry | None:
        found = self.entries.get(entry_id)
        return found.model_copy(deep=True) if found and _mine(found, context) else None

    def mark_entry(self, context: TenantContext, entry_id: str, *, status: EntryStatus | None = None, reversed_by_id: str | None = None,
                   reconciled_at: datetime | None = None, reconciled_ref: str | None = None, clear_reconciled: bool = False) -> None:
        with self._lock:
            found = self.entries.get(entry_id)
            if found is None or not _mine(found, context):
                return
            if status is not None:
                found.status = status
            if reversed_by_id is not None:
                found.reversed_by_id = reversed_by_id
            if reconciled_at is not None:
                found.reconciled_at, found.reconciled_ref = reconciled_at, reconciled_ref
            if clear_reconciled:
                found.reconciled_at, found.reconciled_ref = None, None

    def list_entries(self, context: TenantContext, *, start: date | None = None, end: date | None = None, limit: int = 50_000) -> list[JournalEntry]:
        rows = [e for e in self.entries.values() if _mine(e, context) and (start is None or e.posted_on >= start) and (end is None or e.posted_on <= end)]
        rows.sort(key=lambda e: (e.posted_on, e.created_at, e.id), reverse=True)
        return [e.model_copy(deep=True) for e in rows[:limit]]

    def chain(self, context: TenantContext, canonical_id: str) -> list[JournalEntry]:
        rows = [e for e in self.entries.values() if _mine(e, context) and e.canonical_id == canonical_id]
        return [e.model_copy(deep=True) for e in sorted(rows, key=lambda e: (e.created_at, e.id))]

    def entries_with_fingerprint(self, context: TenantContext, fingerprint: str, *, start: date | None = None,
                                 end: date | None = None) -> list[JournalEntry]:
        """Every entry with the fingerprint posted from ``start`` to ``end`` (both optional), newest first, ties by
        creation and id: complete for the window and in one fixed order."""
        rows = [e for e in self.entries.values() if _mine(e, context) and e.fingerprint == fingerprint
                and (start is None or e.posted_on >= start) and (end is None or e.posted_on <= end)]
        rows.sort(key=lambda e: (e.posted_on, e.created_at, e.id), reverse=True)
        return [e.model_copy(deep=True) for e in rows]

    def entry_for_transaction(self, context: TenantContext, transaction_id: str) -> JournalEntry | None:
        for entry in sorted(self.entries.values(), key=lambda e: (e.created_at, e.id), reverse=True):
            if _mine(entry, context) and entry.status == EntryStatus.POSTED and entry.source != "reversal" and any(
                    p.transaction_id == transaction_id for p in entry.postings):
                return entry.model_copy(deep=True)
        return None

    def linked_transaction_ids(self, context: TenantContext) -> set[str]:
        return {p.transaction_id for e in self.entries.values() if _mine(e, context) for p in e.postings if p.transaction_id}

    def suspense_totals(self, context: TenantContext) -> dict[str, Decimal]:
        from .ledger import SUSPENSE

        out: dict[str, Decimal] = {}
        for entry in self.entries.values():
            if _mine(entry, context):
                for p in entry.postings:
                    if p.ledger_account == SUSPENSE:
                        out[p.currency] = out.get(p.currency, Decimal(0)) + p.amount
        return out

    def entry_sources(self, context: TenantContext, entry_ids: list[str]) -> dict[str, list[SmsMessage]]:
        wanted = set(entry_ids)
        out: dict[str, list[SmsMessage]] = {}
        for m in sorted(self.messages.values(), key=lambda m: m.received_at):
            if _mine(m, context) and m.entry_id in wanted and m.status in (MessageStatus.POSTED, MessageStatus.ATTACHED, MessageStatus.DUPLICATE):
                out.setdefault(m.entry_id, []).append(m.model_copy(deep=True))  # type: ignore[arg-type]
        return out

    def lock_owner(self, context: TenantContext) -> None:
        """The service's per-owner lock already serializes this process (``sms_service.owner_lock``)."""

    def count_everything(self, context: TenantContext) -> int:
        tables = (self.devices, self.messages, self.rules)
        postings = sum(len(e.postings) for e in self.entries.values() if _mine(e, context))
        return (int(self._key(context) in self.settings) + sum(1 for t in tables for v in t.values() if _mine(v, context))
                + sum(1 for e in self.entries.values() if _mine(e, context)) + postings)

    def delete_everything(self, context: TenantContext) -> int:
        with self._lock:
            removed = self.count_everything(context)
            self.settings.pop(self._key(context), None)
            for table in (self.devices, self.messages, self.rules, self.entries):
                for key in [k for k, v in table.items() if _mine(v, context)]:
                    del table[key]
            return removed


sms_memory_store = InMemorySmsStore()


@contextmanager
def open_sms_stores(context: TenantContext) -> Iterator[tuple[FinanceStore, SmsStore]]:
    """The finance store and the SMS store in one owner-scoped unit of work."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.finance_repository import SqlAlchemyFinanceStore
        from ..db.sms_repository import SqlAlchemySmsStore

        return (SqlAlchemyFinanceStore(session, context), SqlAlchemySmsStore(session))

    from . import link_hooks

    with open_scoped_store(context, (finance_store, sms_memory_store), sql_store, user_scoped=True) as pair:
        token = link_hooks.begin()
        try:
            yield pair
            notes = link_hooks.take()
            if notes:
                # Reversals and reconciliations in this unit: links follow before it commits (docs/finance-links.md).
                from .links import settle

                settle(pair[0], pair[1], context, notes)
        finally:
            link_hooks.end(token)


__all__ = [
    "AccountMapping",
    "AiSettings",
    "DeviceStatus",
    "InMemorySmsStore",
    "LedgerKind",
    "MessageStatus",
    "RuleStatus",
    "SenderMapping",
    "SmsDevice",
    "SmsMessage",
    "SmsRule",
    "SmsSettings",
    "SmsStore",
    "DEVICE_SCOPES",
    "WalletCard",
    "source_kind",
    "open_sms_stores",
    "sms_memory_store",
]
