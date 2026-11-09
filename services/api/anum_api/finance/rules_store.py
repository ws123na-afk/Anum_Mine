"""Storage for the owner's rules, merchant aliases, subcategories, suggestion decisions, rule batches and automation
settings (docs/finance.md#rules-and-learning).

With PostgreSQL the store is ``anum_api.db.finance_rules_repository.SqlAlchemyRulesStore`` on the same session as the
finance store (``rules_store_for``), whose tenant, workspace and user RLS context is set: the six tables (migration
``0152_finance_rules_learning``) have forced RLS with the tenant policy, a restrictive owner-only policy and the purge
policy, and every query repeats the scope and the owner. The in-memory store serves local development and tests with
the same rules. Counted, exported and deleted with the rest of Finance.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from .models import Category, CategorySource, TransactionType, TypeSource, _Owned
from .rules import ActiveRule, AliasBook, AutomationSettings, FinanceRule, MerchantAlias, Subcategory, active_rules, from_legacy

DecisionStatus = Literal["accepted", "rejected", "auto_applied"]


class SuggestionDecision(_Owned):
    """The owner's (or a policy's) decision on one suggestion: one per suggestion key, so a second accept loses and a
    rejected suggestion stays dismissed (the dismissal memory)."""

    id: str
    key: str = Field(max_length=200)
    producer: str = Field(max_length=40)
    status: DecisionStatus
    payload_hash: str
    decided_by: str
    decided_at: datetime
    batch_id: str | None = None


class RowState(BaseModel):
    """What a batch changed on one row (never an amount, a currency or a date)."""

    category: Category
    category_source: CategorySource
    subcategory_id: str | None = None
    transaction_type: TransactionType | None = None
    type_source: TypeSource | None = None
    type_confidence: Decimal | None = None
    type_reason: str | None = None


class BatchChange(BaseModel):
    transaction_id: str
    before: RowState
    after: RowState


class RuleBatch(_Owned):
    """One retroactive apply (by the owner, or by a policy the owner turned on), kept so it can be undone as one."""

    id: str
    origin: Literal["owner", "policy"]
    rule_id: str | None = None
    rule_version: int | None = None
    suggestion_key: str | None = None
    changes: list[BatchChange] = Field(default_factory=list)
    status: Literal["applied", "undone"] = "applied"
    created_by: str
    created_at: datetime
    undone_by: str | None = None
    undone_at: datetime | None = None


class RulesStore(Protocol):
    def list_versions(self, context: TenantContext, rule_id: str | None = None) -> list[FinanceRule]: ...
    def add_version(self, rule: FinanceRule) -> bool: ...
    def decide_version(self, context: TenantContext, rule_id: str, version: int, status: str, by: str, at: datetime) -> bool: ...
    def count_rule_ids(self, context: TenantContext) -> int: ...
    def list_aliases(self, context: TenantContext) -> list[MerchantAlias]: ...
    def save_alias(self, alias: MerchantAlias) -> None: ...
    def delete_alias(self, context: TenantContext, alias_id: str) -> bool: ...
    def list_subcategories(self, context: TenantContext) -> list[Subcategory]: ...
    def save_subcategory(self, item: Subcategory) -> None: ...
    def get_decision(self, context: TenantContext, key: str) -> SuggestionDecision | None: ...
    def list_decisions(self, context: TenantContext, keys: list[str] | None = None) -> list[SuggestionDecision]: ...
    def claim_decision(self, decision: SuggestionDecision) -> bool: ...
    def delete_decision(self, context: TenantContext, key: str) -> bool: ...
    def save_batch(self, batch: RuleBatch) -> None: ...
    def get_batch(self, context: TenantContext, batch_id: str) -> RuleBatch | None: ...
    def list_batches(self, context: TenantContext, *, limit: int, offset: int) -> tuple[list[RuleBatch], int]: ...
    def claim_undo(self, context: TenantContext, batch_id: str, by: str, at: datetime) -> bool: ...
    def get_automation(self, context: TenantContext) -> AutomationSettings | None: ...
    def save_automation(self, settings: AutomationSettings) -> None: ...
    def claim_notice(self, context: TenantContext, day: str) -> bool: ...
    def release_notice(self, context: TenantContext, day: str) -> None: ...
    def count_everything(self, context: TenantContext) -> int: ...
    def delete_everything(self, context: TenantContext) -> int: ...


def _mine(item: object, context: TenantContext) -> bool:
    return (item.tenant_id, item.workspace_id, item.owner_user_id) == (context.tenant_id, context.workspace_id, context.user_id)  # type: ignore[attr-defined]


def _check(item: Any) -> None:
    for name in ("tenant_id", "workspace_id", "owner_user_id"):
        if not getattr(item, name, None):
            raise PermissionError("finance rules belong to one person in one workspace")


class InMemoryRulesStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.clear()

    def clear(self) -> None:
        self.versions: dict[str, FinanceRule] = {}
        self.aliases: dict[str, MerchantAlias] = {}
        self.subcategories: dict[str, Subcategory] = {}
        self.decisions: dict[str, SuggestionDecision] = {}
        self.batches: dict[str, RuleBatch] = {}
        self.automation: dict[tuple[str, str, str], AutomationSettings] = {}

    @staticmethod
    def _key(context: TenantContext) -> tuple[str, str, str]:
        return context.tenant_id, context.workspace_id, context.user_id

    def list_versions(self, context: TenantContext, rule_id: str | None = None) -> list[FinanceRule]:
        with self._lock:
            rows = [v.model_copy(deep=True) for v in self.versions.values() if _mine(v, context) and (rule_id is None or v.rule_id == rule_id)]
        return sorted(rows, key=lambda v: (v.rule_id, v.version))

    def add_version(self, rule: FinanceRule) -> bool:
        _check(rule)
        with self._lock:
            ctx = TenantContext(tenant_id=rule.tenant_id, workspace_id=rule.workspace_id, user_id=rule.owner_user_id)
            if any(v.rule_id == rule.rule_id and v.version == rule.version and _mine(v, ctx) for v in self.versions.values()):
                return False  # another change took this version number first
            self.versions[rule.id] = rule.model_copy(deep=True)
            return True

    def decide_version(self, context: TenantContext, rule_id: str, version: int, status: str, by: str, at: datetime) -> bool:
        with self._lock:
            found = next((v for v in self.versions.values() if _mine(v, context) and v.rule_id == rule_id and v.version == version), None)
            if found is None or found.status != "proposed":
                return False
            found.status, found.decided_by, found.decided_at = status, by, at  # type: ignore[assignment]
            return True

    def count_rule_ids(self, context: TenantContext) -> int:
        with self._lock:
            return len({v.rule_id for v in self.versions.values() if _mine(v, context)})

    def list_aliases(self, context: TenantContext) -> list[MerchantAlias]:
        with self._lock:
            return sorted((a.model_copy() for a in self.aliases.values() if _mine(a, context)), key=lambda a: (a.merchant.casefold(), a.alias.casefold()))

    def save_alias(self, alias: MerchantAlias) -> None:
        _check(alias)
        with self._lock:
            ctx = TenantContext(tenant_id=alias.tenant_id, workspace_id=alias.workspace_id, user_id=alias.owner_user_id)
            from .rules import fold

            clash = next((a for a in self.aliases.values() if _mine(a, ctx) and a.id != alias.id and fold(a.alias) == fold(alias.alias)), None)
            if clash is not None:
                raise ValueError("duplicate alias")
            self.aliases[alias.id] = alias.model_copy()

    def delete_alias(self, context: TenantContext, alias_id: str) -> bool:
        with self._lock:
            found = self.aliases.get(alias_id)
            if found is None or not _mine(found, context):
                return False
            del self.aliases[alias_id]
            return True

    def list_subcategories(self, context: TenantContext) -> list[Subcategory]:
        with self._lock:
            return sorted((s.model_copy() for s in self.subcategories.values() if _mine(s, context)), key=lambda s: (s.parent.value, s.name.casefold()))

    def save_subcategory(self, item: Subcategory) -> None:
        _check(item)
        with self._lock:
            ctx = TenantContext(tenant_id=item.tenant_id, workspace_id=item.workspace_id, user_id=item.owner_user_id)
            clash = next((s for s in self.subcategories.values() if _mine(s, ctx) and s.id != item.id and s.parent == item.parent
                          and s.name.casefold() == item.name.casefold()), None)
            if clash is not None:
                raise ValueError("duplicate subcategory")
            self.subcategories[item.id] = item.model_copy()

    def get_decision(self, context: TenantContext, key: str) -> SuggestionDecision | None:
        with self._lock:
            found = next((d for d in self.decisions.values() if _mine(d, context) and d.key == key), None)
            return found.model_copy() if found else None

    def list_decisions(self, context: TenantContext, keys: list[str] | None = None) -> list[SuggestionDecision]:
        wanted = set(keys) if keys is not None else None
        with self._lock:
            return [d.model_copy() for d in self.decisions.values() if _mine(d, context) and (wanted is None or d.key in wanted)]

    def claim_decision(self, decision: SuggestionDecision) -> bool:
        """Record the decision unless one exists for this key (insert-if-absent: of two accepts, one wins)."""
        _check(decision)
        with self._lock:
            ctx = TenantContext(tenant_id=decision.tenant_id, workspace_id=decision.workspace_id, user_id=decision.owner_user_id)
            if any(_mine(d, ctx) and d.key == decision.key for d in self.decisions.values()):
                return False
            self.decisions[decision.id] = decision.model_copy()
            return True

    def delete_decision(self, context: TenantContext, key: str) -> bool:
        with self._lock:
            found = next((k for k, d in self.decisions.items() if _mine(d, context) and d.key == key), None)
            if found is None:
                return False
            del self.decisions[found]
            return True

    def save_batch(self, batch: RuleBatch) -> None:
        _check(batch)
        with self._lock:
            self.batches[batch.id] = batch.model_copy(deep=True)

    def get_batch(self, context: TenantContext, batch_id: str) -> RuleBatch | None:
        with self._lock:
            found = self.batches.get(batch_id)
            return found.model_copy(deep=True) if found is not None and _mine(found, context) else None

    def list_batches(self, context: TenantContext, *, limit: int, offset: int) -> tuple[list[RuleBatch], int]:
        with self._lock:
            rows = sorted((b.model_copy(deep=True) for b in self.batches.values() if _mine(b, context)), key=lambda b: b.created_at, reverse=True)
        return rows[offset:offset + limit], len(rows)

    def claim_undo(self, context: TenantContext, batch_id: str, by: str, at: datetime) -> bool:
        with self._lock:
            found = self.batches.get(batch_id)
            if found is None or not _mine(found, context) or found.status != "applied":
                return False
            found.status, found.undone_by, found.undone_at = "undone", by, at
            return True

    def get_automation(self, context: TenantContext) -> AutomationSettings | None:
        with self._lock:
            found = self.automation.get(self._key(context))
            return found.model_copy(deep=True) if found else None

    def save_automation(self, settings: AutomationSettings) -> None:
        _check(settings)
        with self._lock:
            key = (settings.tenant_id, settings.workspace_id, settings.owner_user_id)
            existing = self.automation.get(key)
            stored = settings.model_copy(deep=True)
            if existing is not None:
                stored.notified_on = existing.notified_on  # the job's claim is kept apart from the owner's switches
            self.automation[key] = stored

    def claim_notice(self, context: TenantContext, day: str) -> bool:
        with self._lock:
            found = self.automation.get(self._key(context))
            if found is None or found.notified_on == day:
                return False
            found.notified_on = day
            return True

    def release_notice(self, context: TenantContext, day: str) -> None:
        with self._lock:
            found = self.automation.get(self._key(context))
            if found is not None and found.notified_on == day:
                found.notified_on = None

    def _tables(self) -> tuple[dict[Any, Any], ...]:
        return (self.versions, self.aliases, self.subcategories, self.decisions, self.batches, self.automation)

    def count_everything(self, context: TenantContext) -> int:
        with self._lock:
            return sum(1 for table in self._tables() for v in table.values() if _mine(v, context))

    def delete_everything(self, context: TenantContext) -> int:
        with self._lock:
            removed = 0
            for table in self._tables():
                for key in [k for k, v in table.items() if _mine(v, context)]:
                    del table[key]
                    removed += 1
            return removed


rules_memory_store = InMemoryRulesStore()


def rules_store_for(finance: Any) -> RulesStore:
    """The rules store on the same unit of work as a finance store (the same SQL session)."""
    session = getattr(finance, "session", None)
    if session is not None:
        from ..db.finance_rules_repository import SqlAlchemyRulesStore

        return SqlAlchemyRulesStore(session)
    return rules_memory_store


@contextmanager
def open_rule_stores(context: TenantContext) -> Iterator[tuple[Any, RulesStore]]:
    """The finance store and the rules store in one owner-scoped unit of work."""
    from .store import open_finance_store

    with open_finance_store(context) as finance:
        yield finance, rules_store_for(finance)


def owner_rules(finance: Any, context: TenantContext) -> list[Any]:
    """The rules ``classify_text`` runs for this person: the approved versioned rules once any rule was saved, else the
    plain phrase list in ``FinanceSettings.category_rules`` (the older model, read until the first save copies it in)."""
    store = rules_store_for(finance)
    versions = store.list_versions(context)
    if versions:
        book = AliasBook.of(store.list_aliases(context))
        return [replace(rule, book=book) for rule in active_rules(versions)]
    from .summary import settings_for

    legacy = settings_for(finance, context).category_rules
    return [ActiveRule.from_content(f"legacy-{i}", 1, from_legacy(rule), "imported") for i, rule in enumerate(legacy)]


def alias_book(finance: Any, context: TenantContext) -> AliasBook:
    return AliasBook.of(rules_store_for(finance).list_aliases(context))


__all__ = [
    "BatchChange", "InMemoryRulesStore", "RowState", "RuleBatch", "RulesStore", "SuggestionDecision", "alias_book", "open_rule_stores", "owner_rules",
    "rules_memory_store", "rules_store_for",
]
