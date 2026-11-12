"""What the statement matching engine reads and writes (docs/finance.md#statement-matching).

``statement_match`` is pure; this module turns the person's stores into its inputs (records, rows waiting
in Review, bill due dates, accounts) and carries out its answers: attach a statement row to the record it
confirms, correct a record whose bank-billed amount differs, recategorize both sides of an own transfer and,
after the import's unit of work, record bill payments and transfer pairs as finance links.

Every function takes stores the caller opened (one owner-scoped unit of work under the owner's lock), except
``make_links``, which goes through ``links.create`` and so takes its own short unit of work per link.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..schemas import TenantContext, new_id
from . import sms_parser as P
from . import statement_match as M
from . import txn_types as TT
from .ledger import EntrySource, EntryStatus, JournalEntry
from .models import Account, AccountKind, Bill, Category, CategorySource, Transaction, TransactionType, TypeSource
from .money import quantize
from .sms_store import MessageStatus, SmsMessage, SmsStore, source_kind
from .store import FinanceStore

logger = logging.getLogger(__name__)

TRANSFER_DIMENSION = "transfer"
TRANSFER_TARGET = "finance_transfer"
MAX_REVIEW_SCAN = 2_000


def account_info(account: Account) -> M.AccountInfo:
    return M.AccountInfo(id=account.id, name=account.name, kind=getattr(account.kind, "value", str(account.kind)), currency=account.currency,
                         last4=account.last4)


def paired_canonicals(links: Any, context: TenantContext) -> set[str]:
    """Canonical transactions already paired as one side of an own transfer (live links only)."""
    rows, _ = links.list_links(context, canonical_id=None, dimension=TRANSFER_DIMENSION, target_type=None, target_id=None, status=None,
                               limit=10_000, offset=0)
    out: set[str] = set()
    for link in rows:
        if getattr(link.status, "value", link.status) != "void":
            out.update({link.canonical_id, link.target_id})
    return out


def records(finance: FinanceStore, sms: SmsStore, context: TenantContext, start: date, end: date, *, paired: set[str] | None = None,
            account_ids: set[str] | None = None) -> list[M.Recorded]:
    """Every live record on the person's accounts posted from ``start`` to ``end``: journal entries (one per
    account they touch) and rows that have no entry."""
    paired = paired or set()
    accounts = {a.id: a for a in finance.list_accounts(context)}
    wanted = account_ids or set(accounts)
    # Live entries only: a reversal mirrors an undone entry and is not a payment of its own.
    entries = [e for e in sms.list_entries(context, start=start, end=end) if e.status == EntryStatus.POSTED and e.source != EntrySource.REVERSAL]
    sources = sms.entry_sources(context, [e.id for e in entries])
    rows = {t.id: t for t in finance.transactions_between(context, start, end) if t.account_id in wanted}
    out: list[M.Recorded] = []
    seen_rows: set[str] = set()
    for entry in entries:
        legs = [p for p in entry.postings if p.account_id in wanted]
        by_account: dict[str, list[Any]] = {}
        for leg in legs:
            by_account.setdefault(leg.account_id, []).append(leg)
        messages = sources.get(entry.id, [])
        kinds = {source_kind(m.source) for m in messages if m.status in (MessageStatus.POSTED, MessageStatus.ATTACHED)}
        primary_message = next((m for m in messages if m.id == entry.source_ref), None)
        if entry.source in (EntrySource.MANUAL, EntrySource.STATEMENT):
            kinds.add(entry.source.value)
            primary = entry.source.value
        else:
            primary = source_kind(primary_message.source) if primary_message is not None else (next(iter(sorted(kinds)), "sms"))
        plain = entry.source in (EntrySource.MANUAL, EntrySource.STATEMENT) or (entry.source == EntrySource.CORRECTION and primary_message is None)
        hashes = {str(m.parsed.get("dedupe_hash")) for m in messages if m.source == "statement" and m.status == MessageStatus.ATTACHED
                  and m.parsed.get("dedupe_hash")}
        last4 = next((str(m.parsed["last4"]) for m in messages if m.parsed.get("last4")), None)
        canonical = entry.canonical_id or entry.id
        for account_id, account_legs in by_account.items():
            account = accounts.get(account_id)
            if account is None:
                continue
            amount = sum((leg.amount for leg in account_legs), Decimal(0))
            if amount == 0:
                continue
            txn_id = next((leg.transaction_id for leg in account_legs if leg.transaction_id), None)
            row = rows.get(txn_id) if txn_id else None
            seen_rows.add(txn_id or "")
            row_hashes = set(hashes)
            if row is not None and row.import_id is not None:
                row_hashes.add(row.dedupe_hash)
            out.append(M.Recorded(
                record_id=f"{entry.id}:{account_id}", entry_id=entry.id, canonical_id=canonical, transaction_id=txn_id, account_id=account_id,
                posted_on=entry.posted_on, amount=amount, currency=account.currency,
                description=(row.description if row is not None else None) or entry.merchant or entry.description, category=(row.category.value if row is not None else entry.category),
                sources=frozenset(kinds or {primary}), primary=primary, reference=entry.reference, last4=last4,
                original_amount=entry.original_amount, original_currency=entry.original_currency, occurred_at=entry.occurred_at,
                statement_hashes=frozenset(row_hashes), reconciled=entry.reconciled_at is not None or "statement" in kinds, plain=plain,
                transfer_paired=canonical in paired,
            ))
    for row in rows.values():
        if row.id in seen_rows:
            continue
        # A row recorded before the journal existed: its own hash is its statement identity.
        primary = "statement" if row.import_id else "manual"
        out.append(M.Recorded(
            record_id=f"txn:{row.id}", entry_id=None, canonical_id=None, transaction_id=row.id, account_id=row.account_id, posted_on=row.posted_on,
            amount=row.amount, currency=row.currency, description=row.description, category=row.category.value, sources=frozenset({primary}),
            primary=primary, statement_hashes=frozenset({row.dedupe_hash}) if row.import_id else frozenset(), reconciled=bool(row.import_id),
            plain=True, transfer_paired=row.id in paired,
        ))
    return out


def waiting(sms: SmsStore, context: TenantContext, account_id: str) -> list[M.Waiting]:
    """Statement rows of earlier imports that wait in Review (``ambiguous_match``)."""
    items, _ = sms.list_messages(context, status=MessageStatus.REVIEW.value, limit=MAX_REVIEW_SCAN, offset=0)
    return [M.Waiting(m.id, str(m.parsed["dedupe_hash"])) for m in items
            if m.source == "statement" and m.parsed.get("account_id") == account_id and m.parsed.get("dedupe_hash")]


def bill_dues(finance: FinanceStore, links: Any, context: TenantContext, start: date, end: date) -> list[M.BillDue]:
    """The due dates of the person's active bills from ``start`` to ``end`` that no payment is matched to yet."""
    from .commitments import BILL_DIMENSION, bill_target_id, occurrences

    bills = [b for b in finance.list_items(context, "bill") if isinstance(b, Bill) and b.active]
    if not bills:
        return []
    rows, _ = links.list_links(context, canonical_id=None, dimension=BILL_DIMENSION, target_type=None, target_id=None, status=None,
                               limit=10_000, offset=0)
    matched = {link.target_id for link in rows if getattr(link.status, "value", link.status) != "void"}
    out = []
    for bill in bills:
        for due in occurrences(bill, start, end):
            if bill_target_id(bill.id, due) not in matched:
                out.append(M.BillDue(bill.id, bill.name, bill.amount, bill.currency, due, bill.account_id))
    return out


@dataclass
class Ledger:
    """Everything the engine compares one statement with."""

    account: M.AccountInfo
    recorded: list[M.Recorded]
    others: list[M.Recorded]
    accounts: list[M.AccountInfo]
    waiting: list[M.Waiting]
    bills: list[M.BillDue]


def gather(finance: FinanceStore, sms: SmsStore, links: Any, context: TenantContext, account: Account, dates: list[date]) -> Ledger:
    info = account_info(account)
    accounts = [account_info(a) for a in finance.list_accounts(context) if not a.archived]
    if not dates:
        return Ledger(info, [], [], accounts, [], [])
    first, last = min(dates), max(dates)
    paired = paired_canonicals(links, context)
    span = max(M.LAG_DAYS, M.TRANSFER_DAYS, M.BILL_LATE_DAYS)
    everything = records(finance, sms, context, first - timedelta(days=span), last + timedelta(days=M.TRANSFER_DAYS), paired=paired)
    recorded = [r for r in everything if r.account_id == account.id and r.posted_on <= last]
    others = [r for r in everything if r.account_id != account.id]
    dues = bill_dues(finance, links, context, first - timedelta(days=M.BILL_LATE_DAYS), last + timedelta(days=M.BILL_EARLY_DAYS))
    return Ledger(info, recorded, others, accounts, waiting(sms, context, account.id), dues)


def run(ledger: Ledger, lines: list[M.Line], *, file_hashes: set[str] | None = None) -> list[M.Match]:
    """``file_hashes``: the dedupe hashes of every row of the statement (also rows left out), so a record imported from
    one of them is never taken for another identical row of the same statement."""
    return M.match_lines(lines, ledger.recorded, waiting=ledger.waiting, bills=ledger.bills, account=ledger.account, others=ledger.others,
                         accounts=ledger.accounts, file_hashes=file_hashes)


# -- writing ---------------------------------------------------------------------------------------------


def statement_message(context: TenantContext, account: Account, row: Transaction, import_id: str, *, now: datetime,
                      reference: str | None) -> SmsMessage:
    """The statement row kept as a source of a record it confirms (or waiting in Review)."""
    from .sms_service import plain_fingerprint

    key = hashlib.sha256(f"statement\x1f{import_id}\x1f{row.dedupe_hash}".encode()).hexdigest()
    parsed: dict[str, object] = {"kind": (P.Kind.DEPOSIT if row.amount > 0 else P.Kind.CARD_PURCHASE).value, "amount": str(abs(row.amount)),
                                 "currency": row.currency, "occurred_on": row.posted_on.isoformat(), "merchant": row.description[:60],
                                 "account_id": account.id, "import_id": import_id, "dedupe_hash": row.dedupe_hash}
    if reference:
        parsed["reference"] = reference[:60]
    return SmsMessage(
        id=new_id("fin_sms"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        device_id=None, source="statement", idem_key=key, received_at=now, sender="statement", body_masked="", status=MessageStatus.REVIEW,
        parsed=parsed, rule="statement", fingerprint=plain_fingerprint(row.amount, row.currency, account.id), created_at=now, updated_at=now,
    )


def attach(sms: SmsStore, context: TenantContext, account: Account, row: Transaction, entry_id: str, import_id: str, *, now: datetime,
           reference: str | None) -> None:
    message = statement_message(context, account, row, import_id, now=now, reference=reference)
    message.status, message.reason, message.entry_id = MessageStatus.ATTACHED, "attached_to_existing", entry_id
    sms.mark_entry(context, entry_id, reconciled_at=now, reconciled_ref=f"{import_id}:{row.dedupe_hash[:16]}")
    sms.add_message(message)


def attach_split(sms: SmsStore, context: TenantContext, account: Account, row: Transaction, group: list[M.Recorded], import_id: str, *,
                 group_id: str, line: int, now: datetime, reference: str | None) -> None:
    """One statement row that stands for several recorded payments (docs/finance.md#one-row-for-several-recorded-payments):
    the row is kept as a statement source of each of them (its part's amount on each), and each is marked reconciled.
    Every part carries the group, the row's line and the row itself, so the match can be undone as a whole."""
    for part, record in enumerate(group, start=1):
        message = statement_message(context, account, row, import_id, now=now, reference=reference)
        message.idem_key = hashlib.sha256(f"statement-split\x1f{import_id}\x1f{row.dedupe_hash}\x1f{record.entry_id}".encode()).hexdigest()
        message.status, message.reason, message.entry_id = MessageStatus.ATTACHED, "split_match", record.entry_id
        message.parsed = {**message.parsed, "amount": str(abs(record.amount)), "statement_amount": str(row.amount), "statement_description": row.description[:200],
                          "split_group": group_id, "split_line": line, "split_part": part, "split_of": len(group)}
        sms.mark_entry(context, record.entry_id or "", reconciled_at=now, reconciled_ref=f"{import_id}:{row.dedupe_hash[:16]}")
        sms.add_message(message)


def split_parts(sms: SmsStore, context: TenantContext, import_id: str, group_id: str) -> list[SmsMessage]:
    """The attached parts of one split match of an import (empty once undone)."""
    items, _ = sms.list_messages(context, status=MessageStatus.ATTACHED.value, limit=MAX_REVIEW_SCAN, offset=0)
    return [m for m in items if m.source == "statement" and m.parsed.get("import_id") == import_id and m.parsed.get("split_group") == group_id]


def undo_split(finance: FinanceStore, sms: SmsStore, context: TenantContext, parts: list[SmsMessage], *, now: datetime) -> Transaction | None:
    """Detach every part of a split match: each record is no longer confirmed by the statement (unless another statement
    row confirms it too), and the row waits in Review (``ambiguous_match``) for the person to import or skip it there.
    Returns the row as it was, or None when there was nothing to undo."""
    if not parts:
        return None
    first = parts[0]
    account = finance.get_account(context, str(first.parsed.get("account_id") or ""))
    attached, _ = sms.list_messages(context, status=MessageStatus.ATTACHED.value, limit=MAX_REVIEW_SCAN, offset=0)
    ids = {m.id for m in parts}
    for message in parts:
        message.status, message.reason, message.updated_at = MessageStatus.DISMISSED, "split_undone", now
        sms.save_message(message)
        others = [m for m in attached if m.entry_id == message.entry_id and m.id not in ids and m.source == "statement"]
        if message.entry_id and not others:
            sms.mark_entry(context, message.entry_id, clear_reconciled=True)
    row = Transaction(
        id=new_id("fin_txn"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        account_id=str(first.parsed.get("account_id") or ""), posted_on=date.fromisoformat(str(first.parsed["occurred_on"])),
        amount=Decimal(str(first.parsed["statement_amount"])), currency=str(first.parsed.get("currency") or (account.currency if account else "")),
        description=str(first.parsed.get("statement_description") or first.parsed.get("merchant") or ""), category=Category.OTHER, category_source=CategorySource.NONE,
        import_id=str(first.parsed["import_id"]), dedupe_hash=str(first.parsed["dedupe_hash"]), created_at=now, updated_at=now,
    )
    if account is not None:
        hold(sms, context, account, row, str(first.parsed["import_id"]), now=now, reference=first.parsed.get("reference"))
    return row


def _statement_messages(sms: SmsStore, context: TenantContext, status: MessageStatus) -> list[SmsMessage]:
    """Every statement source in ``status``, paged so a long history is read whole."""
    out: list[SmsMessage] = []
    offset = 0
    while True:
        items, total = sms.list_messages(context, status=status.value, limit=MAX_REVIEW_SCAN, offset=offset)
        out.extend(m for m in items if m.source == "statement")
        offset += len(items)
        if not items or offset >= total:
            return out


def detach_import(sms: SmsStore, context: TenantContext, import_id: str, *, now: datetime) -> int:
    """Undo everything an import attached or held outside its own rows (docs/finance.md#undoing-an-import).

    Every statement source of the import is dismissed (``import_undone``): rows attached to one recorded payment, every
    part of a split match and rows waiting in Review. A record loses its reconciled mark unless another statement row,
    of another import, still confirms it. Safe to run again: a second pass finds nothing left. Returns how many
    sources were detached."""
    attached = _statement_messages(sms, context, MessageStatus.ATTACHED)
    mine = [m for m in attached if m.parsed.get("import_id") == import_id]
    held = [m for m in _statement_messages(sms, context, MessageStatus.REVIEW) if m.parsed.get("import_id") == import_id]
    ids = {m.id for m in mine}
    for message in [*mine, *held]:
        message.status, message.reason, message.updated_at = MessageStatus.DISMISSED, "import_undone", now
        sms.save_message(message)
    for entry_id in {m.entry_id for m in mine if m.entry_id}:
        still = [m for m in attached if m.entry_id == entry_id and m.id not in ids]
        if not still:
            sms.mark_entry(context, entry_id, clear_reconciled=True)
    return len(mine) + len(held)


def hold(sms: SmsStore, context: TenantContext, account: Account, row: Transaction, import_id: str, *, now: datetime, reference: str | None) -> None:
    message = statement_message(context, account, row, import_id, now=now, reference=reference)
    message.reason = "ambiguous_match"
    sms.add_message(message)


def correct_amount(finance: FinanceStore, sms: SmsStore, context: TenantContext, account: Account, record: M.Recorded, billed: Decimal, *,
                   now: datetime, original: tuple[Decimal, str] | None) -> str | None:
    """Correct a record to the bank-billed amount through the path a person's correction takes: a hand-entered or
    statement record is reposted in its canonical chain, an SMS or Apple Pay one is reversed and posted again from
    its message. Returns the live entry id afterwards, or None when it could not be corrected (it stays as it was)."""
    from .sms_service import PlanError, correction_draft, post, repost_plain, reverse
    from .sms_service import settings_for as sms_settings
    from .summary import settings_for

    if record.entry_id is None and record.transaction_id is not None:
        row = finance.get_transaction(context, record.transaction_id)
        if row is None:
            return None
        row.amount, row.updated_at = quantize(billed, account.currency), now
        row.notes = _note(row.notes, "Amount corrected to the bank-billed figure from a statement")
        finance.save_transaction(row)
        return None
    entry = sms.get_entry(context, record.entry_id or "")
    if entry is None or entry.status != EntryStatus.POSTED:
        return None
    if record.plain and record.transaction_id:
        row = finance.get_transaction(context, record.transaction_id)
        if row is None:
            return None
        row.amount, row.updated_at = quantize(billed, account.currency), now
        row.notes = _note(row.notes, "Amount corrected to the bank-billed figure from a statement")
        finance.save_transaction(row)
        new = repost_plain(finance, sms, context, entry, row, now=now)
        return new.id if new is not None else None
    message = sms.get_message(context, entry.source_ref or "")
    if message is None:
        return None
    corrected: dict[str, Any] = {"account_id": account.id}
    if original is not None:
        corrected.update(amount=str(abs(original[0])), currency=original[1], converted_amount=str(abs(billed)))
    else:
        corrected.update(amount=str(abs(billed)), currency=account.currency)
    try:
        draft = correction_draft(finance, context, message, corrected, settings_for(finance, context).timezone)
        reverse(finance, sms, context, entry, now=now, why="corrected to the bank-billed amount by a statement import")
        new, _ = post(finance, sms, context, sms_settings(sms, context), draft, source=EntrySource.CORRECTION, source_ref=message.id, now=now,
                      canonical_id=entry.canonical_id or entry.id)
    except PlanError:
        logger.info("finance statement correction skipped outcome=plan_error")
        return None
    message.entry_id, message.updated_at = new.id, now
    message.parsed = {**message.parsed, **corrected}
    sms.save_message(message)
    return new.id


def mark_transfer(row: Transaction, account_kind: AccountKind | None, *, by_owner: bool = False, card: bool = False) -> bool:
    """File ``row`` as an own transfer (a card payment when a card or loan is on either side) because it was paired with
    its other side. A match never overwrites the owner's own choice (audit C4): a category or type the owner picked
    stays, and the answer is False. Paired by a match the source is ``match``; ticked by the owner it is ``manual``."""
    if TT.owner_locked(row) and not by_owner:
        return False
    kind = TransactionType.CARD_PAYMENT if card or account_kind in TT.LIABILITY_KINDS else TransactionType.INTERNAL_TRANSFER
    if by_owner:
        row.category, row.category_source = Category.TRANSFER, CategorySource.MANUAL
        TT.apply(row, TT.Typed(kind, TypeSource.OWNER, Decimal(1), "owner_paired_transfer"))
    else:
        row.category, row.category_source = Category.TRANSFER, CategorySource.MATCH
        TT.apply(row, TT.Typed(kind, TypeSource.MATCH, Decimal("0.9"), "paired_own_transfer"))
    return True


def recategorize_transfer(finance: FinanceStore, sms: SmsStore, context: TenantContext, record: M.Recorded, *, now: datetime,
                          by_owner: bool = False) -> bool:
    """Mark a statement or hand-entered record as an own transfer (out of spend and income). An SMS or Apple Pay
    record keeps its reading; its category is corrected under Review. A record whose category or type the owner
    chose keeps it (audit C4) unless ``by_owner`` (the owner paired it themselves). True when the record is now a transfer."""
    from .sms_service import repost_plain

    if record.category == Category.TRANSFER.value:
        return True
    if not record.plain or record.transaction_id is None:
        return False
    row = finance.get_transaction(context, record.transaction_id)
    if row is None:
        return False
    account = finance.get_account(context, row.account_id)
    if not mark_transfer(row, account.kind if account is not None else None, by_owner=by_owner):
        return False
    row.updated_at = now
    finance.save_transaction(row)
    if record.entry_id:
        entry = sms.get_entry(context, record.entry_id)
        if entry is not None and entry.status == EntryStatus.POSTED:
            repost_plain(finance, sms, context, entry, row, now=now)
    return True


def canonical_for(sms: SmsStore, context: TenantContext, transaction_id: str) -> str | None:
    entry: JournalEntry | None = sms.entry_for_transaction(context, transaction_id)
    return (entry.canonical_id or entry.id) if entry is not None else None


def _note(existing: str | None, text: str) -> str:
    return text if not existing else f"{existing} | {text}"[:1000]


# -- links after the unit of work ------------------------------------------------------------------------


@dataclass
class LinkJob:
    kind: str  # "bill" or "transfer"
    canonical_id: str
    target_id: str
    amount: Decimal
    currency: str
    review: bool = False  # a weak bill match: the due date waits for the person ("review"), never "paid"


@dataclass
class LinkOutcome:
    bills_paid: int = 0
    bills_review: int = 0
    transfers: int = 0
    failed: list[str] = field(default_factory=list)  # error codes, never amounts or text


def make_links(context: TenantContext, jobs: list[LinkJob]) -> LinkOutcome:
    """Record bill payments and transfer pairs through ``links.create``, the path the Bills page and the links API use."""
    from . import links as K
    from .commitments import BILL_DIMENSION, BILL_TARGET

    outcome = LinkOutcome()
    for job in jobs:
        dimension, target = (BILL_DIMENSION, BILL_TARGET) if job.kind == "bill" else (TRANSFER_DIMENSION, TRANSFER_TARGET)
        try:
            made = K.create(context, K.LinkRequest(canonical_id=job.canonical_id, dimension=dimension, role="payment", target_type=target,
                                                   target_id=job.target_id, amount=job.amount, currency=job.currency), review=job.review)
        except K.LinkError as exc:
            outcome.failed.append(exc.code)
            continue
        if made.replayed:
            continue
        if job.kind == "bill":
            if job.review:
                outcome.bills_review += 1
            else:
                outcome.bills_paid += 1
        else:
            outcome.transfers += 1
    return outcome


__all__ = [
    "TRANSFER_DIMENSION",
    "TRANSFER_TARGET",
    "Ledger",
    "LinkJob",
    "LinkOutcome",
    "account_info",
    "attach",
    "attach_split",
    "bill_dues",
    "canonical_for",
    "correct_amount",
    "detach_import",
    "gather",
    "hold",
    "make_links",
    "mark_transfer",
    "paired_canonicals",
    "recategorize_transfer",
    "records",
    "run",
    "split_parts",
    "undo_split",
    "waiting",
]
