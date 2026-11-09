"""Rules, learning from corrections and retroactive apply (docs/finance.md#rules-and-learning).

Every function here runs inside one short unit of work the caller opened (``open_rule_stores`` for reads, the owner's
lock and ``open_sms_stores`` for writes that touch transactions), never across a model call. A retroactive apply
changes only the category, subcategory and type of rows the owner did not set by hand: never an amount, a currency or
a date, never a row recorded from an SMS or Apple Pay message (those are corrected under SMS review so the journal
records the change), and it keeps every row's values from before so the whole batch can be undone.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..schemas import TenantContext, new_id
from . import txn_types as TT
from .categories import classify_text
from .models import AccountKind, CategorySource, Transaction, TypeSource
from .rules import (
    MAX_RULES,
    MAX_VERSIONS,
    ActiveRule,
    AliasBook,
    FinanceRule,
    Provenance,
    RuleContent,
    RuleMatch,
    Row,
    canonical_json,
    from_legacy,
    merchant_key,
    payload_hash,
)
from .rules_store import BatchChange, RowState, RuleBatch, RulesStore

SCAN_LIMIT = 10_000  # rows a preview reads at most (newest first); beyond it the answer says so
PREVIEW_ROWS = 50  # rows a preview lists; the counts cover all of them


class RuleError(Exception):
    def __init__(self, status: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


# -- versions ------------------------------------------------------------------------------------------


def migrate_legacy(finance: Any, store: RulesStore, context: TenantContext, *, by: str, now: datetime) -> int:
    """Copy the plain phrase list (``FinanceSettings.category_rules``) in as approved ``imported`` rules, once: on the
    first save of a rule. The old list is left as it was (the previous release still reads it)."""
    if store.list_versions(context):
        return 0
    from .summary import settings_for

    copied = 0
    for index, legacy in enumerate(settings_for(finance, context).category_rules):
        content = from_legacy(legacy)
        content = content.model_copy(update={"priority": 100 + index})
        rule_id = new_id("fin_rule")
        digest = payload_hash(rule_id=rule_id, version=1, owner_user_id=context.user_id, content=content)
        store.add_version(FinanceRule(
            id=new_id("fin_rulev"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            rule_id=rule_id, version=1, status="approved", content=content, provenance="imported", confidence=Decimal("0.9"),
            payload_hash=digest, created_by=by, decided_by=by, created_at=now, decided_at=now,
        ))
        copied += 1
    return copied


def check_content(finance: Any, store: RulesStore, context: TenantContext, content: RuleContent) -> None:
    """The rule's references are the owner's own: accounts, and a subcategory under the rule's category."""
    if content.match.too_broad():
        raise RuleError(422, "These words are too common on their own. Add the merchant, an account or more specific words.", "too_broad")
    if content.match.account_ids:
        mine = {a.id for a in finance.list_accounts(context)}
        if any(a not in mine for a in content.match.account_ids):
            raise RuleError(422, "One of the accounts is not yours or no longer exists.", "unknown_account")
    if content.subcategory_id:
        sub = next((s for s in store.list_subcategories(context) if s.id == content.subcategory_id), None)
        if sub is None or sub.archived:
            raise RuleError(422, "That subcategory does not exist. Choose another or add it first.", "unknown_subcategory")
        if sub.parent != content.category:
            raise RuleError(422, "That subcategory belongs to another category.", "subcategory_parent")


def propose(finance: Any, store: RulesStore, context: TenantContext, content: RuleContent, *, provenance: Provenance, by: str, now: datetime,
            rule_id: str | None = None, source_transaction_id: str | None = None, reverted_from: int | None = None,
            confidence: Decimal | None = None) -> FinanceRule:
    """A new version (``proposed``) of a rule, or a new rule. Nothing runs until the owner approves it."""
    check_content(finance, store, context, content)
    migrate_legacy(finance, store, context, by=by, now=now)
    if rule_id is None:
        if store.count_rule_ids(context) >= MAX_RULES:
            raise RuleError(409, f"You have {MAX_RULES} rules. Remove or merge some first.", "too_many_rules")
        rule_id, version = new_id("fin_rule"), 1
    else:
        versions = store.list_versions(context, rule_id)
        if not versions:
            raise RuleError(404, "Rule not found. It may have been removed.", "rule_not_found")
        if len(versions) >= MAX_VERSIONS:
            raise RuleError(409, f"This rule has {MAX_VERSIONS} versions. Make a new rule instead.", "too_many_versions")
        version = max(v.version for v in versions) + 1
    if source_transaction_id is not None and finance.get_transaction(context, source_transaction_id) is None:
        raise RuleError(404, "Transaction not found. It may have been deleted.", "transaction_not_found")
    rule = FinanceRule(
        id=new_id("fin_rulev"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        rule_id=rule_id, version=version, status="proposed", content=content, provenance=provenance,
        source_transaction_id=source_transaction_id, confidence=confidence if confidence is not None else Decimal("0.9"),
        payload_hash=payload_hash(rule_id=rule_id, version=version, owner_user_id=context.user_id, content=content),
        created_by=by, created_at=now, reverted_from=reverted_from,
    )
    if not store.add_version(rule):
        raise RuleError(409, "Someone changed this rule at the same moment. Open it again and retry.", "version_taken")
    return rule


def _version(store: RulesStore, context: TenantContext, rule_id: str, version: int) -> FinanceRule:
    found = next((v for v in store.list_versions(context, rule_id) if v.version == version), None)
    if found is None:
        raise RuleError(404, "That version of the rule was not found.", "version_not_found")
    return found


def decide(store: RulesStore, context: TenantContext, rule_id: str, version: int, *, approve: bool, payload: str | None, by: str,
           now: datetime) -> FinanceRule:
    """Approve (with the payload hash the owner was shown) or reject a proposed version. Of two decisions, one wins."""
    found = _version(store, context, rule_id, version)
    if found.status != "proposed":
        raise RuleError(409, "This version was already decided.", "already_decided")
    if approve and not (payload and hmac.compare_digest(payload, found.payload_hash)):
        raise RuleError(409, "The rule changed since you looked at it. Open it again and check it before you approve.", "payload_mismatch")
    if not store.decide_version(context, rule_id, version, "approved" if approve else "rejected", by, now):
        raise RuleError(409, "This version was already decided.", "already_decided")
    return _version(store, context, rule_id, version)


def revert(finance: Any, store: RulesStore, context: TenantContext, rule_id: str, to_version: int, *, by: str, now: datetime) -> FinanceRule:
    old = _version(store, context, rule_id, to_version)
    if old.status != "approved":
        raise RuleError(409, "Only an approved version can be restored.", "not_approved")
    return propose(finance, store, context, old.content, provenance="owner", by=by, now=now, rule_id=rule_id, reverted_from=to_version,
                   confidence=old.confidence)


# -- learning from a correction ------------------------------------------------------------------------


def from_correction(row: Transaction, aliases: AliasBook, *, scope_account: bool) -> RuleContent:
    """The rule a correction suggests: the merchant (as the owner named it, or the description's merchant), the same
    side (money in or out), the category, subcategory and type the owner chose, optionally only on this account."""
    named = aliases.name(row.description)
    merchant = named or merchant_key(row.description)
    if not merchant:
        raise RuleError(422, "This description names no merchant to learn from. Add a rule by hand under Finance, Suggestions, Rules.",
                        "no_merchant")
    owner_type = row.type_source == TypeSource.OWNER and (row.type_reason or "").startswith("owner_type")
    return RuleContent(
        match=RuleMatch(merchant=merchant if named else None, words=[] if named else [merchant], direction="out" if row.amount < 0 else "in",
                        account_ids=[row.account_id] if scope_account else []),
        category=row.category, subcategory_id=row.subcategory_id, transaction_type=row.transaction_type if owner_type else None,
    )


# -- preview and apply -----------------------------------------------------------------------------------


def _state(t: Transaction) -> RowState:
    return RowState(category=t.category, category_source=t.category_source, subcategory_id=t.subcategory_id, transaction_type=t.transaction_type,
                    type_source=t.type_source, type_confidence=t.type_confidence, type_reason=t.type_reason)


@dataclass
class PreviewRow:
    transaction: Transaction
    after: RowState


@dataclass
class Preview:
    rows: list[PreviewRow] = field(default_factory=list)
    matched: int = 0  # rows the rule matches
    unchanged: int = 0  # already filed this way
    manual_kept: int = 0  # set by the owner by hand: never changed
    sms_kept: int = 0  # recorded from an SMS or Apple Pay message: corrected under SMS review instead
    truncated: bool = False
    digest: str = ""


def _after(rule: ActiveRule, t: Transaction, kind: AccountKind | None) -> RowState:
    found = classify_text(t.description, t.amount, [rule], kind, account_id=t.account_id, currency=t.currency)
    return RowState(category=found.category, category_source=CategorySource.OWNER_RULE, subcategory_id=found.subcategory_id,
                    transaction_type=found.type, type_source=found.typed.source, type_confidence=found.typed.confidence,
                    type_reason=found.typed.reason[:40])


def preview(finance: Any, sms: Any, context: TenantContext, rule: ActiveRule, *, rows: list[Transaction] | None = None) -> Preview:
    """Which of the owner's rows a rule would change, and which it leaves alone and why. Nothing is saved.

    ``sms`` (the journal store) tells rows recorded from a message apart; ``None`` reads every row as plain."""
    from .store import TransactionFilter

    if rows is None:
        found, total = finance.list_transactions(context, TransactionFilter(), limit=SCAN_LIMIT, offset=0)
        truncated = total > SCAN_LIMIT
    else:
        found, truncated = rows, False
    kinds = {a.id: a.kind for a in finance.list_accounts(context)}
    out = Preview(truncated=truncated)
    for t in found:
        if not rule.matches(Row(description=t.description, amount=t.amount, currency=t.currency, account_id=t.account_id)):
            continue
        out.matched += 1
        if TT.owner_locked(t):
            out.manual_kept += 1
            continue
        after = _after(rule, TT.fill(t.model_copy(), kinds.get(t.account_id)), kinds.get(t.account_id))
        current = TT.typed(t, kinds.get(t.account_id))
        if (t.category, t.subcategory_id, current.type) == (after.category, after.subcategory_id, after.transaction_type):
            out.unchanged += 1
            continue
        if sms is not None:
            from .ledger import EntrySource

            entry = sms.entry_for_transaction(context, t.id)
            if entry is not None and entry.source not in (EntrySource.MANUAL, EntrySource.STATEMENT):
                out.sms_kept += 1
                continue
        out.rows.append(PreviewRow(t, after))
    out.rows.sort(key=lambda r: (r.transaction.posted_on, r.transaction.id), reverse=True)
    out.digest = hashlib.sha256(canonical_json({
        "rule": [rule.rule_id, rule.version, rule.content.model_dump(mode="json")],
        "rows": [[r.transaction.id, r.transaction.category.value, r.transaction.subcategory_id, r.after.category.value, r.after.subcategory_id,
                  getattr(r.after.transaction_type, "value", None)] for r in out.rows],
    })).hexdigest()
    return out


def apply_rows(finance: Any, sms: Any, store: RulesStore, context: TenantContext, changes: list[PreviewRow], *, origin: str, by: str, now: datetime,
               rule: ActiveRule | None = None, suggestion_key: str | None = None, batch_id: str | None = None) -> RuleBatch:
    """Write the new category, subcategory and type on each row, repost a plain row's journal entry, and keep the batch."""
    from .ledger import EntrySource
    from .sms_service import repost_plain

    batch = RuleBatch(id=batch_id or new_id("fin_batch"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                      origin=origin, rule_id=rule.rule_id if rule and not rule.rule_id.startswith("legacy-") else None,  # type: ignore[arg-type]
                      rule_version=rule.version if rule else None, suggestion_key=suggestion_key, created_by=by, created_at=now)
    for change in changes:
        current = finance.get_transaction(context, change.transaction.id)
        if current is None or TT.owner_locked(current):
            continue  # deleted, or the owner set it by hand meanwhile: never overwritten
        if (current.category, current.subcategory_id, current.transaction_type) != (
                change.transaction.category, change.transaction.subcategory_id, change.transaction.transaction_type):
            continue  # changed since the preview
        before = _state(current)
        entry = sms.entry_for_transaction(context, current.id) if sms is not None else None
        if entry is not None and entry.source not in (EntrySource.MANUAL, EntrySource.STATEMENT):
            continue
        a = change.after
        current.category, current.category_source, current.subcategory_id = a.category, a.category_source, a.subcategory_id
        current.transaction_type, current.type_source, current.type_confidence, current.type_reason = (
            a.transaction_type, a.type_source, a.type_confidence, a.type_reason)
        current.updated_at = now
        finance.save_transaction(current)
        if entry is not None:
            repost_plain(finance, sms, context, entry, current, now=now)
        batch.changes.append(BatchChange(transaction_id=current.id, before=before, after=_state(current)))
    store.save_batch(batch)
    return batch


def undo_batch(finance: Any, sms: Any, store: RulesStore, context: TenantContext, batch_id: str, *, by: str, now: datetime) -> tuple[int, int]:
    """Put every row of the batch back as it was, unless the owner changed it since. Returns (restored, kept)."""
    from .ledger import EntrySource
    from .sms_service import repost_plain

    batch = store.get_batch(context, batch_id)
    if batch is None:
        raise RuleError(404, "That change was not found.", "batch_not_found")
    if not store.claim_undo(context, batch_id, by, now):
        raise RuleError(409, "This change was already undone.", "already_undone")
    restored = kept = 0
    for change in batch.changes:
        current = finance.get_transaction(context, change.transaction_id)
        if current is None or _state(current) != change.after:
            kept += 1  # deleted or changed since (by the owner or a later rule): left as it is now
            continue
        b = change.before
        current.category, current.category_source, current.subcategory_id = b.category, b.category_source, b.subcategory_id
        current.transaction_type, current.type_source, current.type_confidence, current.type_reason = (
            b.transaction_type, b.type_source, b.type_confidence, b.type_reason)
        current.updated_at = now
        finance.save_transaction(current)
        entry = sms.entry_for_transaction(context, current.id) if sms is not None else None
        if entry is not None and entry.source in (EntrySource.MANUAL, EntrySource.STATEMENT):
            repost_plain(finance, sms, context, entry, current, now=now)
        restored += 1
    return restored, kept


__all__ = [
    "PREVIEW_ROWS", "Preview", "PreviewRow", "RuleError", "apply_rows", "check_content", "decide", "from_correction", "migrate_legacy", "preview",
    "propose", "revert", "undo_batch",
]
