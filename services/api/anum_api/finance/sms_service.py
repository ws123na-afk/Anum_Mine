"""The iPhone SMS pipeline: device credentials, ingest, posting, review and reconciliation.

docs/finance-sms.md. Every function here is synchronous and runs in FastAPI's thread pool or
a worker thread, never on the event loop; each opens its own short owner-scoped unit of work
(``open_sms_stores``) and never keeps one open across anything slow.

**Device credentials** (``mint``, ``resolve_device``) follow the customer portal's pattern
(``business/portal_auth.py``): the token carries the tenant, workspace, owner and device ids
(ids, not secrets) and a 256-bit secret; only the secret's SHA-256 is stored. A request is
resolved inside that owner's own RLS scope, so a token can only ever reach the rows of the
person and workspace it was issued for, and the secret is compared in constant time. The ids
in the token are not proof of anything: a token whose secret does not match the device row
stored under that owner is refused like an unknown one. Nothing the phone sends in the body
(a user id, a workspace) is read as ownership.

**Never twice.** A retried delivery (same device, sender, text and minute) finds its first
row by ``idem_key`` and gets the same answer. A message that reports a transaction already in
the journal (same kind of movement, amount, currency and account, inside the time window, no
conflicting reference) is stored as ``duplicate`` and posts nothing.

**Never invented.** An unknown sender, a bank format the person has not confirmed, a missing
amount or account, an ambiguous kind or a foreign amount without the billed amount all wait in
the review queue. Only a confident reading of a confirmed format (or an owner-taught rule)
posts on its own, and only while ``auto_post`` is on.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from ..schemas import TenantContext, new_id, utc_now
from . import sms_parser as P
from .categories import categorize
from .ledger import (
    SUSPENSE,
    EntrySource,
    EntryStatus,
    JournalEntry,
    Leg,
    PlanError,
    Posting,
    balanced,
    plan,
    reversal_legs,
    transaction_category,
    unusual,
)
from .models import Account, AccountKind, Category, CategorySource, Transaction
from .money import MoneyError, normalize_currency, quantize
from .sms_store import (
    DeviceStatus,
    MessageStatus,
    RuleStatus,
    SmsDevice,
    SmsMessage,
    SmsRule,
    SmsSettings,
    SmsStore,
    open_sms_stores,
)
from .store import FinanceStore
from .summary import today_in, zone

logger = logging.getLogger("anum_api.finance.sms")

TOKEN_PREFIX = "anum_sms"
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
PER_MINUTE = 30  # messages a device may send per minute
PER_DAY = 1_000
DUPLICATE_WINDOW = timedelta(minutes=10)  # same moment, when both messages give the time
DUPLICATE_DAYS = 2  # otherwise posted within this many days of each other
REFUND_LOOKBACK_DAYS = 120
INGEST_PATH = "/api/v1/finance/sms/ingest"
TEST_SENDER = "anumtest"
TEST_TEXT = "anum test"
NOTIFY_EVERY = timedelta(hours=1)

UNKNOWN = "This device link is not valid. Create a new device token in ANUM under Finance, iPhone SMS."


class IngestError(Exception):
    """A refusal with an HTTP status and a plain message (never the message text)."""

    def __init__(self, status_code: int, message: str, code: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.retry_after = retry_after


# -- device tokens ------------------------------------------------------------------------------


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(value: str) -> str | None:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def mint(context: TenantContext, device_id: str) -> tuple[str, str, str]:
    """``(token, hash to store, hint)``. The token is shown once and never stored."""
    if not SAFE_ID.match(device_id):
        raise ValueError("a device id that cannot be part of a token")
    secret = secrets.token_urlsafe(32)
    token = ".".join((TOKEN_PREFIX, _b64(context.tenant_id), _b64(context.workspace_id), _b64(context.user_id), device_id, secret))
    return token, secret_hash(secret), token[-4:]


def parse_token(token: str) -> tuple[TenantContext, str, str] | None:
    parts = (token or "").strip().split(".")
    if len(parts) != 6 or parts[0] != TOKEN_PREFIX or not SAFE_ID.match(parts[4]) or not (30 <= len(parts[5]) <= 100):
        return None
    tenant, workspace, user = (_unb64(part) for part in parts[1:4])
    if not tenant or not workspace or not user or len(tenant) > 80 or len(workspace) > 80 or len(user) > 160:
        return None
    return TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=user, roles=[]), parts[4], parts[5]


def _membership_roles(context: TenantContext) -> list[str]:
    """The owner's current workspace role: a device stops working when its owner leaves or becomes a viewer."""
    from ..dependencies import lookup_membership
    from ..settings import settings

    membership = lookup_membership(context)
    if membership is not None:
        if not membership.active:
            raise IngestError(401, "The person this device belongs to is no longer a member of the workspace.", "not_member")
        return [membership.role.lower()]
    if settings.auth_mode == "oidc":
        raise IngestError(401, "The person this device belongs to is no longer a member of the workspace.", "not_member")
    return ["member"]  # header sign-in: local and test environments only


def authorize(authorization: str | None) -> tuple[TenantContext, str, str]:
    """``Authorization: Bearer <device token>`` to ``(owner context with role, device id, secret)``."""
    from ..authorization import Permission
    from ..dependencies import has_permission, workspace_archived

    if not authorization or not authorization.lower().startswith("bearer "):
        raise IngestError(401, "Send the device token as Authorization: Bearer <token>.", "missing_token")
    parsed = parse_token(authorization.split(" ", 1)[1])
    if parsed is None:
        raise IngestError(401, UNKNOWN, "invalid_token")
    context, device_id, secret = parsed
    roles = _membership_roles(context)
    context = context.model_copy(update={"roles": roles})
    if not has_permission(context, Permission.FINANCE_MANAGE):
        raise IngestError(403, "The person this device belongs to can no longer change finance records (viewer role).", "not_allowed")
    if workspace_archived(context):
        raise IngestError(409, "This workspace is archived, so it accepts no new messages.", "archived")
    return context, device_id, secret


def check_device(device: SmsDevice | None, secret: str) -> SmsDevice:
    # The same answer for an unknown device and a wrong secret.
    if device is None or not hmac.compare_digest(device.token_hash, secret_hash(secret)):
        raise IngestError(401, UNKNOWN, "invalid_token")
    if device.status == DeviceStatus.REVOKED:
        raise IngestError(401, "This device was disconnected. Create a new device token in ANUM to connect it again.", "revoked")
    if device.status == DeviceStatus.PAUSED:
        raise IngestError(409, "This device is paused. Resume it in ANUM under Finance, iPhone SMS.", "paused")
    return device


# -- helpers ------------------------------------------------------------------------------------


def idem_key(device_id: str, sender_key: str, body: str, received: datetime) -> str:
    minute = received.replace(second=0, microsecond=0).isoformat()
    return hashlib.sha256("\x1f".join((device_id, sender_key, body, minute)).encode("utf-8")).hexdigest()


def settings_for(store: SmsStore, context: TenantContext) -> SmsSettings:
    return store.get_settings(context) or SmsSettings()


def learned_rules(store: SmsStore, context: TenantContext) -> list[P.LearnedRule]:
    out = []
    for rule in store.list_rules(context):
        if rule.status != RuleStatus.ACTIVE:
            continue
        try:
            kind = P.Kind(rule.kind)
        except ValueError:
            continue
        out.append(P.LearnedRule(id=rule.id, sender=rule.sender, pattern=rule.pattern, kind=kind, currency=rule.currency, category=rule.category))
    return out


def extra_senders(settings: SmsSettings) -> dict[str, str]:
    return {P.normalize_sender(item.sender): item.bank for item in settings.extra_senders}


@dataclass
class Resolved:
    account: Account | None
    counter: Account | None
    amount: Decimal | None  # in the account's currency
    original_amount: Decimal | None = None
    original_currency: str | None = None
    fx_rate: Decimal | None = None
    problem: str | None = None  # a review reason


def _finder(settings: SmsSettings, accounts: list[Account]):  # type: ignore[no-untyped-def]
    live = {a.id: a for a in accounts if not a.archived}
    mapped = {m.last4: live.get(m.account_id) for m in settings.account_map}

    def find(last4: str | None) -> Account | None:
        if not last4:
            return None
        if mapped.get(last4) is not None:
            return mapped[last4]
        matches = [a for a in live.values() if a.last4 == last4]
        return matches[0] if len(matches) == 1 else None

    return find, live


def resolve(parsed: P.Parsed, settings: SmsSettings, accounts: list[Account], *, account_id: str | None = None,
            counter_id: str | None = None) -> Resolved:
    """Which of the person's accounts a reading is about, and the amount in that account's currency."""
    find, live = _finder(settings, accounts)
    default = live.get(settings.default_account_id or "")
    named = [a for a in (find(parsed.from_last4), find(parsed.to_last4), find(parsed.last4), find(parsed.second_last4)) if a is not None]
    kind = parsed.kind
    account: Account | None
    counter: Account | None = None
    if account_id is not None or counter_id is not None:
        account, counter = live.get(account_id or ""), live.get(counter_id or "")
    elif kind == P.Kind.OWN_TRANSFER:
        account, counter = find(parsed.from_last4), find(parsed.to_last4)
        if account is None and counter is None:
            account = find(parsed.last4) or default
    elif kind == P.Kind.CARD_REPAYMENT:
        card = next((a for a in named if a.kind == AccountKind.CARD), None)
        payer = next((a for a in named if a.kind in (AccountKind.BANK, AccountKind.CASH)), None)
        if card is None:
            cards = [a for a in live.values() if a.kind == AccountKind.CARD]
            card = cards[0] if len(cards) == 1 and payer is not None else None
        if card is not None and payer is None and not named:
            payer = default if default is not None and default.kind == AccountKind.BANK else None
        account, counter = (payer, card) if payer is not None else (None, card)
    elif kind in (P.Kind.LOAN_DISBURSEMENT, P.Kind.LOAN_REPAYMENT):
        loan = next((a for a in named if a.kind == AccountKind.LOAN), None)
        if loan is None:
            loans = [a for a in live.values() if a.kind == AccountKind.LOAN]
            loan = loans[0] if len(loans) == 1 else None
        account = next((a for a in named if a.kind != AccountKind.LOAN), None) or default
        counter = loan
    else:
        account = (named[0] if named else None) or default
    target = account or counter
    if parsed.amount is None or parsed.currency is None:
        return Resolved(account, counter, None, problem="missing_amount")
    if target is None:
        return Resolved(account, counter, None, problem="account_unknown")
    if parsed.currency == target.currency:
        return Resolved(account, counter, parsed.amount)
    if parsed.converted_amount is not None and parsed.converted_currency == target.currency:
        rate = (parsed.converted_amount / parsed.amount).quantize(Decimal("0.0000000001"))
        return Resolved(account, counter, parsed.converted_amount, parsed.amount, parsed.currency, rate)
    return Resolved(account, counter, None, problem="needs_converted_amount")


def fingerprint(kind: P.Kind, amount: Decimal, currency: str, key_account: str) -> str:
    group = "in" if kind in P.MONEY_IN else "out"
    if kind in (P.Kind.CARD_REPAYMENT, P.Kind.OWN_TRANSFER):
        group = kind.value  # both sides' messages describe one movement
    return hashlib.sha256("\x1f".join((group, str(amount), currency, key_account)).encode("utf-8")).hexdigest()


def _key_account(kind: P.Kind, resolved: Resolved) -> str:
    if kind == P.Kind.CARD_REPAYMENT:
        card = resolved.counter if resolved.counter is not None and resolved.counter.kind == AccountKind.CARD else resolved.account
        return card.id if card is not None else "-"
    if kind == P.Kind.OWN_TRANSFER:
        return "|".join(sorted(a.id for a in (resolved.account, resolved.counter) if a is not None)) or "-"
    target = resolved.account or resolved.counter
    return target.id if target is not None else "-"


def find_duplicate(store: SmsStore, context: TenantContext, fp: str, when: datetime, has_time: bool, reference: str | None) -> JournalEntry | None:
    for entry in store.entries_with_fingerprint(context, fp):
        if entry.status != EntryStatus.POSTED:
            continue
        if reference and entry.reference and reference != entry.reference:
            continue
        if has_time and entry.occurred_at is not None and abs(entry.occurred_at - when) <= DUPLICATE_WINDOW:
            return entry
        if abs((entry.posted_on - when.date()).days) <= DUPLICATE_DAYS and (not has_time or entry.occurred_at is None or reference):
            return entry
        if reference and entry.reference == reference:
            return entry
    return None


def _description(kind: P.Kind, merchant: str | None) -> str:
    label = kind.value.replace("_", " ")
    text = f"{merchant} ({label}, SMS)" if merchant else f"{label.capitalize()} (SMS)"
    return text[:500]


def _refund_category(store: SmsStore, context: TenantContext, merchant: str | None, amount: Decimal, day: date) -> tuple[str, str | None]:
    """The category of the purchase a refund gives back, and that purchase's entry id."""
    if not merchant:
        return Category.OTHER.value, None
    wanted = merchant.strip().upper()
    for entry in store.list_entries(context, start=day - timedelta(days=REFUND_LOOKBACK_DAYS), end=day, limit=5_000):
        if entry.kind == P.Kind.CARD_PURCHASE.value and entry.status == EntryStatus.POSTED and (entry.merchant or "").strip().upper() == wanted:
            spent = sum((p.amount for p in entry.postings if p.ledger_account.startswith("expense:")), Decimal(0))
            if spent >= amount:
                return entry.category or Category.OTHER.value, entry.id
    return Category.OTHER.value, None


# -- posting ------------------------------------------------------------------------------------


@dataclass
class Draft:
    """Everything needed to post one message (from the parser or from the person's correction)."""

    kind: P.Kind
    resolved: Resolved
    merchant: str | None
    category: str | None
    occurred_at: datetime
    has_time: bool
    reference: str | None
    interest: Decimal | None
    fee: Decimal | None


def post(finance: FinanceStore, store: SmsStore, context: TenantContext, settings: SmsSettings, draft: Draft,
         *, source: EntrySource, source_ref: str | None, now: datetime) -> tuple[JournalEntry, list[Transaction]]:
    """Write one balanced entry and the stage 2 transactions of its account legs."""
    from .summary import settings_for as finance_settings

    resolved = draft.resolved
    if resolved.amount is None:
        raise PlanError(resolved.problem or "missing_amount", "The amount is missing.")
    accounts = {a.id: a for a in finance.list_accounts(context)}
    cash = accounts.get(settings.cash_account_id or "")
    category = draft.category
    reverses = None
    if draft.kind in (P.Kind.REFUND, P.Kind.REVERSAL) and not category:
        category, reverses = _refund_category(store, context, draft.merchant, resolved.amount, draft.occurred_at.date())
    if draft.kind == P.Kind.CARD_PURCHASE and not category:
        found, _ = categorize(draft.merchant or "", -resolved.amount, finance_settings(finance, context).category_rules)
        category = Category.OTHER.value if found in (Category.INCOME, Category.TRANSFER) else found.value
    legs = plan(draft.kind, resolved.amount, account=resolved.account, counter=resolved.counter, category=category or Category.OTHER.value,
                cash_account=cash, interest=draft.interest, fee=draft.fee)
    if not balanced(legs):  # plan always balances; this guards a future change
        raise PlanError("unbalanced", "The entry does not balance.")
    entry_id = new_id("fin_je")
    description = _description(draft.kind, draft.merchant)
    transactions: list[Transaction] = []
    postings: list[Posting] = []
    for leg in legs:
        transaction_id = None
        if leg.account_id is not None:
            account = accounts[leg.account_id]
            txn_category = transaction_category(legs, leg)
            transaction = Transaction(
                id=new_id("fin_txn"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                account_id=account.id, posted_on=draft.occurred_at.date(), amount=leg.amount, currency=account.currency,
                description=description, category=txn_category, category_source=CategorySource.RULE, notes="From an iPhone SMS",
                import_id=None, dedupe_hash=hashlib.sha256(f"sms\x1f{entry_id}\x1f{account.id}".encode()).hexdigest(),
                created_at=now, updated_at=now,
            )
            transactions.append(transaction)
            transaction_id = transaction.id
        postings.append(Posting(
            id=new_id("fin_post"), entry_id=entry_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            ledger_account=leg.ledger_account, account_id=leg.account_id, transaction_id=transaction_id, amount=leg.amount, currency=leg.currency,
            created_at=now,
        ))
    if transactions:
        finance.add_transactions(transactions)
    entry = JournalEntry(
        id=entry_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        posted_on=draft.occurred_at.date(), occurred_at=draft.occurred_at if draft.has_time else None, kind=draft.kind.value,
        category=category, merchant=draft.merchant, description=description, source=source, source_ref=source_ref,
        reverses_id=reverses, original_amount=resolved.original_amount, original_currency=resolved.original_currency,
        fx_rate=resolved.fx_rate, fx_fee=draft.fee if resolved.fx_rate is not None else None,
        fingerprint=fingerprint(draft.kind, resolved.amount, (resolved.account or resolved.counter).currency, _key_account(draft.kind, resolved)),  # type: ignore[union-attr]
        reference=draft.reference, created_at=now, postings=postings,
    )
    store.add_entry(entry)
    return entry, transactions


def reverse(finance: FinanceStore, store: SmsStore, context: TenantContext, entry: JournalEntry, *, now: datetime, why: str) -> JournalEntry:
    """Undo ``entry`` with a mirror entry; its stage 2 transactions leave the account lists."""
    reversal_id = new_id("fin_je")
    legs = reversal_legs(entry)
    postings = [Posting(
        id=new_id("fin_post"), entry_id=reversal_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        ledger_account=leg.ledger_account, account_id=leg.account_id, transaction_id=None, amount=leg.amount, currency=leg.currency, created_at=now,
    ) for leg in legs]
    reversal = JournalEntry(
        id=reversal_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        posted_on=entry.posted_on, occurred_at=entry.occurred_at, kind=entry.kind, category=entry.category, merchant=entry.merchant,
        description=f"Reversal: {why}"[:500], source=EntrySource.REVERSAL, source_ref=entry.source_ref, status=EntryStatus.POSTED,
        reverses_id=entry.id, created_at=now, postings=postings,
    )
    store.add_entry(reversal)
    store.mark_entry(context, entry.id, status=EntryStatus.REVERSED, reversed_by_id=reversal.id)
    for posting in entry.postings:
        if posting.transaction_id:
            finance.delete_transaction(context, posting.transaction_id)
    return reversal


def clear_suspense(finance: FinanceStore, store: SmsStore, context: TenantContext, original: JournalEntry, resolved: Resolved, *, now: datetime,
                   source_ref: str) -> JournalEntry | None:
    """A second message named the side of an own transfer or repayment the first left in suspense."""
    suspense = [p for p in original.postings if p.ledger_account == SUSPENSE]
    if len(suspense) != 1:
        return None
    known = {p.account_id for p in original.postings if p.account_id}
    side = next((a for a in (resolved.account, resolved.counter) if a is not None and a.id not in known), None)
    if side is None or side.currency != suspense[0].currency:
        return None
    amount = suspense[0].amount  # positive: the suspense was debited, so this side was credited
    legs = [Leg(SUSPENSE, -amount, side.currency), Leg(("asset:" if side.kind in (AccountKind.BANK, AccountKind.CASH, AccountKind.INVESTMENT) else "liability:") + side.id, amount, side.currency, side.id)]
    entry_id = new_id("fin_je")
    transaction = Transaction(
        id=new_id("fin_txn"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id, account_id=side.id,
        posted_on=original.posted_on, amount=amount, currency=side.currency, description=original.description, category=Category.TRANSFER,
        category_source=CategorySource.RULE, notes="From an iPhone SMS", import_id=None,
        dedupe_hash=hashlib.sha256(f"sms\x1f{entry_id}\x1f{side.id}".encode()).hexdigest(), created_at=now, updated_at=now,
    )
    finance.add_transactions([transaction])
    entry = JournalEntry(
        id=entry_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id, posted_on=original.posted_on,
        occurred_at=original.occurred_at, kind=original.kind, category=None, merchant=original.merchant, description=original.description,
        source=EntrySource.CLEARING, source_ref=source_ref, reverses_id=None, created_at=now,
        postings=[Posting(id=new_id("fin_post"), entry_id=entry_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                          owner_user_id=context.user_id, ledger_account=leg.ledger_account, account_id=leg.account_id,
                          transaction_id=transaction.id if leg.account_id else None, amount=leg.amount, currency=leg.currency, created_at=now)
                  for leg in legs],
    )
    store.add_entry(entry)
    return entry


# -- ingest -------------------------------------------------------------------------------------


@dataclass
class Outcome:
    message: SmsMessage
    replayed: bool = False
    unusual: bool = False
    notify: str | None = None  # "review", "duplicate" or "unusual"


def _when(parsed: P.Parsed, received: datetime, tz_name: str) -> tuple[datetime, bool]:
    local = received.astimezone(zone(tz_name))
    return P.occurred(parsed, local), parsed.occurred_at_time is not None or parsed.occurred_on is None


def process(finance: FinanceStore, store: SmsStore, context: TenantContext, message: SmsMessage, body: str, *, now: datetime) -> Outcome:
    """Read, check and (when confident) post one stored message. The caller commits."""
    settings = settings_for(store, context)
    fsettings = finance.get_settings(context)
    tz_name = fsettings.timezone if fsettings else "Asia/Dubai"
    parsed = P.parse(message.sender, body, learned=learned_rules(store, context), extra_senders=extra_senders(settings),
                     verified_senders=set(settings.verified_senders))
    message.parsed = parsed.fields()
    message.rule = parsed.rule
    message.bank_key = parsed.bank_key
    if parsed.status == "rejected":
        message.status, message.reason, message.body_masked, message.body_purge_at, message.parsed = (
            MessageStatus.REJECTED, parsed.reason, "", None, {})
        return Outcome(message)
    accounts = finance.list_accounts(context)
    resolved = resolve(parsed, settings, accounts)
    target = resolved.account or resolved.counter
    if parsed.balance is not None and target is not None and parsed.balance_currency == target.currency and target.kind != AccountKind.LOAN:
        message.observed_balance, message.observed_currency, message.observed_account_id = parsed.balance, parsed.balance_currency, target.id
    if parsed.rule.startswith("learned:"):
        rule = store.get_rule(context, parsed.rule.split(":", 1)[1])
        if rule is not None:
            rule.hits += 1
            rule.updated_at = now
            store.save_rule(rule)
            if rule.account_id and resolved.account is None and resolved.counter is None:
                resolved = resolve(parsed, settings, accounts, account_id=rule.account_id)
    when, has_time = _when(parsed, message.received_at, tz_name)
    reason = parsed.reason or resolved.problem
    if reason is None and not settings.auto_post:
        reason = "auto_post_off"
    if reason is None and resolved.amount is not None:
        fp = fingerprint(parsed.kind, resolved.amount, target.currency, _key_account(parsed.kind, resolved))  # type: ignore[union-attr]
        message.fingerprint = fp
        duplicate = find_duplicate(store, context, fp, when, has_time, parsed.reference)
        if duplicate is not None:
            clear_suspense(finance, store, context, duplicate, resolved, now=now, source_ref=message.id)
            message.status, message.reason, message.duplicate_of = MessageStatus.DUPLICATE, "already_recorded", duplicate.source_ref
            message.entry_id = duplicate.id
            return Outcome(message, notify="duplicate")
    if reason is not None:
        message.status, message.reason = MessageStatus.REVIEW, reason
        return Outcome(message, notify="review")
    draft = Draft(kind=parsed.kind, resolved=resolved, merchant=parsed.merchant, category=None, occurred_at=when, has_time=has_time,
                  reference=parsed.reference, interest=parsed.interest, fee=parsed.fee if parsed.kind != P.Kind.BANK_FEE else None)
    try:
        entry, _ = post(finance, store, context, settings, draft, source=EntrySource.SMS, source_ref=message.id, now=now)
    except PlanError as exc:
        message.status, message.reason = MessageStatus.REVIEW, exc.code
        return Outcome(message, notify="review")
    message.status, message.reason, message.entry_id = MessageStatus.POSTED, None, entry.id
    flagged = _is_unusual(store, context, entry)
    return Outcome(message, unusual=flagged, notify="unusual" if flagged else None)


def _is_unusual(store: SmsStore, context: TenantContext, entry: JournalEntry) -> bool:
    spent = sum((p.amount for p in entry.postings if p.ledger_account.startswith("expense:")), Decimal(0))
    if spent <= 0 or not entry.category:
        return False
    history = [
        sum((p.amount for p in e.postings if p.ledger_account.startswith("expense:")), Decimal(0))
        for e in store.list_entries(context, start=entry.posted_on - timedelta(days=90), end=entry.posted_on, limit=2_000)
        if e.id != entry.id and e.category == entry.category and e.status == EntryStatus.POSTED and e.source != EntrySource.REVERSAL
    ]
    return unusual(spent, [h for h in history if h > 0])


def ingest(context: TenantContext, device_id: str | None, secret: str | None, sender: str, text: str, *,
           source: str = "device", now: datetime | None = None) -> Outcome:
    """The whole path for one message, in short units of work. Raises :class:`IngestError`."""
    now = now or utc_now()
    sender_key = P.normalize_sender(sender)
    body = P.normalize(text)
    if not body:
        raise IngestError(422, "The message is empty. Map Shortcut Input, Content to the message field.", "empty_message")
    if not sender_key:
        raise IngestError(422, "The sender is empty. Map Shortcut Input, Sender to the sender field.", "empty_sender")
    with open_sms_stores(context) as (finance, store):
        settings = settings_for(store, context)
        device = None
        if source == "device":
            device = check_device(store.get_device(context, device_id or ""), secret or "")
            device.last_seen_at = now
            store.save_device(device)
            if sender_key == TEST_SENDER and body.lower() == TEST_TEXT:
                # The safe test from the Shortcut: proves the URL, the token and the JSON, records nothing.
                device.last_ok_at, device.last_error, device.last_error_at = now, None, None
                store.save_device(device)
                test = SmsMessage(id="test", tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                                  device_id=device.id, source="test", idem_key="test", received_at=now, sender=sender_key,
                                  status=MessageStatus.REJECTED, reason="test_ok", created_at=now, updated_at=now)
                return Outcome(test)
        if not settings.enabled:
            raise IngestError(409, "SMS forwarding is off. Turn it on in ANUM under Finance, iPhone SMS.", "disabled")
        if device is not None:
            if store.count_since(context, device.id, now - timedelta(minutes=1)) >= PER_MINUTE:
                raise IngestError(429, "Too many messages from this device. Try again in a minute.", "rate_limited", retry_after=60)
            if store.count_since(context, device.id, now - timedelta(days=1)) >= PER_DAY:
                raise IngestError(429, "This device reached its daily limit of messages.", "rate_limited", retry_after=3600)
        key = idem_key(device.id if device else f"{source}:{new_id('p')}", sender_key, body, now)
        existing = store.message_by_key(context, key)
        if existing is not None and existing.status != MessageStatus.FAILED:
            return Outcome(existing, replayed=True)
        store.purge_bodies(context, now)
        retention = settings.body_retention_days
        masked = P.mask(body) if retention > 0 else ""
        message = existing or SmsMessage(
            id=new_id("fin_sms"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            device_id=device.id if device else None, source=source if source in ("device", "paste") else "paste",  # type: ignore[arg-type]
            idem_key=key, received_at=now, sender=sender_key, body_masked=masked,
            body_purge_at=now + timedelta(days=retention) if retention > 0 else None, status=MessageStatus.FAILED, created_at=now, updated_at=now,
        )
        if existing is not None:
            message.attempts += 1
        try:
            outcome = process(finance, store, context, message, body, now=now)
        except Exception:
            logger.exception("finance_sms_processing_failed", extra={"anum_message_id": message.id})
            raise _Failed(message, device.id if device else None) from None
        message = outcome.message
        message.updated_at = now
        if existing is None:
            store.add_message(message)
        else:
            store.save_message(message)
        if device is not None:
            device.message_count += 1
            device.last_ok_at, device.last_error, device.last_error_at = now, None, None
            store.save_device(device)
    return outcome


class _Failed(Exception):
    def __init__(self, message: SmsMessage, device_id: str | None) -> None:
        super().__init__("processing failed")
        self.message = message
        self.device_id = device_id


def record_failure(context: TenantContext, failed: _Failed, now: datetime) -> SmsMessage:
    """Keep a failed message (masked) so a retry of the same delivery processes it again."""
    with open_sms_stores(context) as (_finance, store):
        message = failed.message
        message.status, message.reason, message.updated_at = MessageStatus.FAILED, "processing_error", now
        if store.message_by_key(context, message.idem_key) is None:
            store.add_message(message)
        else:
            store.save_message(message)
        if failed.device_id:
            device = store.get_device(context, failed.device_id)
            if device is not None:
                device.last_error, device.last_error_at = "processing_error", now
                store.save_device(device)
        return message


def record_device_error(context: TenantContext, device_id: str, code: str, now: datetime) -> None:
    """A refusal after the token was accepted (rate limit, switched off): shown under Last error."""
    try:
        with open_sms_stores(context) as (_finance, store):
            device = store.get_device(context, device_id)
            if device is not None:
                device.last_error, device.last_error_at = code, now
                store.save_device(device)
    except Exception:
        logger.exception("finance_sms_device_error_not_recorded")


def notify(context: TenantContext, why: str, now: datetime) -> None:
    """At most one in-app notice an hour: counts and a pointer, never an amount or a message."""
    texts = {
        "review": ("A bank message needs your review", "A message from your bank could not be recorded on its own. Open Finance, iPhone SMS, Review to check it."),
        "duplicate": ("Possible duplicate bank message", "A bank message looked like a transaction already recorded, so it was not recorded again. Open Finance, iPhone SMS, Review to check it."),
        "unusual": ("Unusual spending recorded", "A bank message recorded spending well above your usual amount in that category. Open Finance, Ledger to check it."),
        "failed": ("A bank message could not be processed", "ANUM could not process a bank message. Your iPhone can retry it; open Finance, iPhone SMS to see the last error."),
    }
    if why not in texts:
        return
    key = (context.tenant_id, context.workspace_id, context.user_id)
    last = _last_notified.get(key)
    if last is not None and now - last < NOTIFY_EVERY:
        return
    _last_notified[key] = now
    try:
        from ..team_work import open_team_unit

        title, body = texts[why]
        with open_team_unit(context) as unit:
            unit.notify(context.user_id, "finance_sms", title, body, None)
    except Exception:  # the message is stored either way; the notice is best effort
        logger.exception("finance_sms_notice_failed")


_last_notified: dict[tuple[str, str, str], datetime] = {}


# -- corrections and review ----------------------------------------------------------------------


def money(value: object, currency: str, field: str) -> Decimal:
    if isinstance(value, float) or value is None or isinstance(value, bool):
        raise PlanError("invalid_amount", f"{field}: enter an amount such as 120.50.")
    try:
        amount = quantize(Decimal(str(value).strip()), currency)
    except (InvalidOperation, MoneyError) as exc:
        raise PlanError("invalid_amount", f"{field}: enter an amount such as 120.50.") from exc
    if amount < 0:
        raise PlanError("invalid_amount", f"{field}: enter a positive amount; the kind says the direction.")
    return amount


def correction_draft(finance: FinanceStore, context: TenantContext, message: SmsMessage, body: dict[str, Any], tz_name: str) -> Draft:
    """A posting draft from the person's corrected fields."""
    try:
        kind = P.Kind(body.get("kind") or message.parsed.get("kind") or "unknown")
    except ValueError as exc:
        raise PlanError("unknown_kind", "Pick what kind of transaction this is.") from exc
    if kind == P.Kind.UNKNOWN:
        raise PlanError("unknown_kind", "Pick what kind of transaction this is.")
    accounts = {a.id: a for a in finance.list_accounts(context) if not a.archived}
    account = accounts.get(body.get("account_id") or "")
    counter = accounts.get(body.get("counter_account_id") or "")
    if body.get("account_id") and account is None:
        raise PlanError("account_unknown", "That account was not found. It may have been archived or deleted.")
    if body.get("counter_account_id") and counter is None:
        raise PlanError("account_unknown", "The other account was not found. It may have been archived or deleted.")
    target = account or counter
    if target is None:
        raise PlanError("account_unknown", "Pick the account this message is about.")
    amount = money(body.get("amount") if body.get("amount") is not None else message.parsed.get("amount"), target.currency, "Amount")
    original_amount = original_currency = rate = None
    currency = body.get("currency") or message.parsed.get("currency") or target.currency
    try:
        currency = normalize_currency(str(currency))
    except MoneyError as exc:
        raise PlanError("invalid_currency", str(exc)) from exc
    if currency != target.currency:
        # The person entered the amount in the message's currency: the billed amount is needed too.
        converted = body.get("converted_amount")
        if converted in (None, ""):
            raise PlanError("needs_converted_amount", f"Enter the amount charged in {target.currency} as the statement or message shows it.")
        original_amount, original_currency = money(amount, currency, "Amount"), currency
        amount = money(converted, target.currency, "Amount charged")
        rate = (amount / original_amount).quantize(Decimal("0.0000000001")) if original_amount else None
    day = body.get("occurred_on") or message.parsed.get("occurred_on")
    try:
        occurred_on = date.fromisoformat(str(day)) if day else message.received_at.astimezone(zone(tz_name)).date()
    except ValueError as exc:
        raise PlanError("invalid_date", "Date: use YYYY-MM-DD.") from exc
    when = datetime.combine(occurred_on, datetime.min.time(), tzinfo=zone(tz_name)).replace(hour=12)
    merchant = (str(body.get("merchant") or message.parsed.get("merchant") or "").strip() or None)
    merchant = P.normalize(merchant)[:60] if merchant else None
    category = body.get("category")
    if category:
        try:
            category = Category(category).value
        except ValueError as exc:
            raise PlanError("invalid_category", "Pick a category from the list.") from exc
    resolved = Resolved(account, counter, amount, original_amount, original_currency, rate)
    interest = money(body["interest"], target.currency, "Interest") if body.get("interest") not in (None, "") else None
    fee = money(body["fee"], target.currency, "Fee") if body.get("fee") not in (None, "") else None
    return Draft(kind=kind, resolved=resolved, merchant=merchant, category=category, occurred_at=when, has_time=False,
                 reference=(str(body.get("reference") or message.parsed.get("reference") or "")[:30] or None), interest=interest, fee=fee)


def teach(store: SmsStore, context: TenantContext, message: SmsMessage, corrected: dict[str, Any], draft: Draft, now: datetime) -> SmsRule:
    """Propose an owner rule from a corrected message. It reads nothing until the person activates it."""
    if not message.body_masked:
        raise P.TeachError("The message text is no longer kept (retention), so ANUM cannot learn its format. Paste it again to teach a rule.")
    fields = {
        "amount": str(draft.resolved.original_amount or draft.resolved.amount) if draft.resolved.amount is not None else None,
        "merchant": draft.merchant,
        "last4": corrected.get("last4") or message.parsed.get("last4"),
        "reference": draft.reference,
        "balance": corrected.get("balance") or message.parsed.get("balance"),
    }
    pattern = P.build_pattern(message.body_masked, fields)
    if len([r for r in store.list_rules(context) if r.status != RuleStatus.DISABLED]) >= 100:
        raise P.TeachError("You have 100 rules already. Delete one you no longer need first.")
    rule = SmsRule(
        id=new_id("fin_smsrule"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        sender=message.sender, bank_key=message.bank_key, pattern=pattern, kind=draft.kind.value,
        currency=draft.resolved.original_currency or (draft.resolved.account or draft.resolved.counter).currency,  # type: ignore[union-attr]
        category=draft.category, account_id=(draft.resolved.account or draft.resolved.counter).id,  # type: ignore[union-attr]
        status=RuleStatus.PROPOSED, from_message_id=message.id, created_at=now, updated_at=now,
    )
    store.save_rule(rule)
    return rule


def today_for(finance: FinanceStore, context: TenantContext) -> date:
    from .summary import settings_for as finance_settings

    return today_in(finance_settings(finance, context))


__all__ = [
    "INGEST_PATH",
    "IngestError",
    "Outcome",
    "authorize",
    "check_device",
    "correction_draft",
    "fingerprint",
    "ingest",
    "mint",
    "parse_token",
    "post",
    "process",
    "record_failure",
    "resolve",
    "reverse",
    "secret_hash",
    "teach",
]
