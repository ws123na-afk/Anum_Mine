"""Routes for what ANUM learned from the person's reviews of bank messages (docs/finance-sms.md#learning).

Under ``/api/v1/finance``. ``finance:read`` reads; ``finance:manage`` changes. Everything is the caller's own (the SMS
settings row is owner-only under RLS). Each one-tap action is audited with codes and ids only: never a sender's
message, an amount, a merchant or a card number beyond what the person mapped.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, utc_now
from . import sms_learning as SL
from . import sms_parser as P
from . import sms_service as svc
from .api import _audit, _manage, _read
from .ledger import EntryStatus
from .sms_store import AccountMapping, AutoPostLimit, MessageStatus, SmsSettings, open_sms_stores

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

SUMMARY_SCAN = 2_000  # waiting messages the summary reads at most (newest first); beyond it the answer says so


class FixView(BaseModel):
    action: str
    sender: str | None = None
    last4: str | None = None
    account_id: str | None = None
    account_name: str | None = None
    party: str | None = None
    kind: str | None = None
    ref: str | None = None


class SenderView(BaseModel):
    sender: str
    trusted: bool
    clean: int
    needed: int  # readings recorded unchanged still needed before ANUM trusts it
    auto_confirmed_at: Any = None
    corrected_at: Any = None
    held_until: Any = None


class KindView(BaseModel):
    id: str
    sender: str
    direction: str
    party: str
    kind: str
    counter_account_id: str | None
    count: int
    active: bool
    since: Any = None


class CategoryView(BaseModel):
    party: str
    category: str
    count: int
    rule_id: str | None


class CardView(BaseModel):
    last4: str
    account_id: str
    account_name: str | None
    count: int


class NoticeView(BaseModel):
    id: str
    kind: str
    sender: str | None = None
    party: str | None = None
    value: str | None = None
    last4: str | None = None
    account_id: str | None = None
    account_name: str | None = None
    ref: str | None = None
    created_at: Any


class LearningView(BaseModel):
    enabled: bool
    confirm_after: int
    auto_post: bool
    auto_post_limits: list[AutoPostLimit]
    senders: list[SenderView]
    kinds: list[KindView]
    categories: list[CategoryView]
    card_proposals: list[CardView]
    notices: list[NoticeView]
    version: int


class ReasonItem(BaseModel):
    key: str  # the sender or card ending the messages share, or "-"
    count: int
    fix: FixView | None


class ReasonGroup(BaseModel):
    reason: str
    count: int
    items: list[ReasonItem]


class ReviewSummary(BaseModel):
    total: int
    scanned: int
    complete: bool
    groups: list[ReasonGroup]


def _names(accounts: list[Any]) -> dict[str, str]:
    return {a.id: a.name for a in accounts}


def fix_view(fix: SL.Fix | None, names: dict[str, str]) -> FixView | None:
    if fix is None:
        return None
    return FixView(**fix.__dict__, account_name=names.get(fix.account_id or ""))


def _view(settings: SmsSettings, accounts: list[Any], now: Any) -> LearningView:
    names = _names(accounts)
    memory, learning = settings.memory, settings.learning
    senders = []
    for track in memory.senders:
        until = SL.held_until(settings, track.sender)
        senders.append(SenderView(sender=track.sender, trusted=track.sender in settings.verified_senders, clean=track.clean,
                                  needed=max(0, learning.confirm_after - track.clean), auto_confirmed_at=track.auto_confirmed_at,
                                  corrected_at=track.corrected_at, held_until=until if until is not None and until > now else None))
    return LearningView(
        enabled=learning.enabled, confirm_after=learning.confirm_after, auto_post=settings.auto_post, auto_post_limits=settings.auto_post_limits,
        senders=senders,
        kinds=[KindView(**k.model_dump(include={"id", "sender", "direction", "kind", "counter_account_id", "count", "active", "since"}),
                        party=k.party_label) for k in memory.kinds],
        categories=[CategoryView(party=c.party_label, category=c.category, count=c.count, rule_id=c.rule_id) for c in memory.categories],
        card_proposals=[CardView(last4=c.last4, account_id=c.account_id, account_name=names.get(c.account_id), count=c.count)
                        for c in memory.cards if SL.card_proposal(settings, c.last4) is not None
                        and SL.card_is_unmapped(settings, c.last4, accounts)],
        notices=[NoticeView(**n.model_dump(), account_name=names.get(n.account_id or "")) for n in reversed(memory.notices)],
        version=settings.version,
    )


@router.get("/sms/learning", response_model=LearningView)
def get_learning(context: TenantContext = Depends(tenant_context)) -> LearningView:
    _read(context)
    with open_sms_stores(context) as (finance, store):
        return _view(svc.settings_for(store, context), finance.list_accounts(context), utc_now())


@router.get("/sms/review/summary", response_model=ReviewSummary)
def review_summary(context: TenantContext = Depends(tenant_context)) -> ReviewSummary:
    """Why messages are waiting: counts per reason, split by the sender or card they share, with the fix for each."""
    _read(context)
    with open_sms_stores(context) as (finance, store):
        settings = svc.settings_for(store, context)
        accounts = finance.list_accounts(context)
        waiting, total = store.list_messages(context, status=MessageStatus.REVIEW.value, limit=SUMMARY_SCAN, offset=0)
    names = _names(accounts)
    by_reason: dict[str, Counter[str]] = {}
    fixes: dict[tuple[str, str], FixView | None] = {}
    for message in waiting:
        reason = message.reason or "review"
        fix = SL.fix_for(message, settings, accounts)
        key = (fix.sender or fix.last4) if fix is not None and (fix.sender or fix.last4) else "-"
        by_reason.setdefault(reason, Counter())[key] += 1
        fixes.setdefault((reason, key), fix_view(fix, names))
    groups = [ReasonGroup(reason=reason, count=sum(keys.values()),
                          items=[ReasonItem(key=key, count=count, fix=fixes[(reason, key)]) for key, count in keys.most_common(10)])
              for reason, keys in by_reason.items()]
    groups.sort(key=lambda g: (-g.count, g.reason))
    return ReviewSummary(total=total, scanned=len(waiting), complete=len(waiting) >= total, groups=groups)


def _change(context: TenantContext, change: Any, *, bump: bool = True) -> tuple[SmsSettings, list[Any], Any]:
    """Read, change and save the person's SMS settings under their lock. ``change`` returns a result or raises."""
    now = utc_now()
    with svc.owner_lock(context), open_sms_stores(context) as (finance, store):
        store.lock_owner(context)
        settings = svc.settings_for(store, context)
        accounts = finance.list_accounts(context)
        updated = settings.model_copy(deep=True)
        result = change(updated, accounts, now)
        if bump:
            updated.version, updated.updated_at = settings.version + 1, now
        if not store.save_settings(context, updated, expected_version=settings.version):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Your SMS settings changed at the same moment. Try again.")
    return updated, accounts, result


def _missing(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found. It may have been changed already: reload the page.")


class SenderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender: str = Field(min_length=1, max_length=200)


def _sender(body: SenderInput) -> str:
    sender = P.normalize_sender(body.sender)
    if not sender:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Name the sender as the message shows it.")
    return sender


@router.post("/sms/learning/senders/trust", response_model=LearningView)
def trust_sender(body: SenderInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> LearningView:
    """Always record confident messages from this sender (the same as ticking "This reading is right")."""
    _manage(context)
    sender = _sender(body)

    def change(s: SmsSettings, _accounts: list[Any], _now: Any) -> None:
        bank = P.bank_for_sender(sender)  # a registry bank needs no extra sender line; any other sender gets one
        SL.trust_sender(s, sender, bank.key if bank is not None else None)
        found = SL.track(s, sender)
        if found is not None:
            found.corrected_at = None

    updated, accounts, _ = _change(context, change)
    _audit(repository, context, "finance.sms.learning.trust", "finance_sms_settings", {"how": "owner"})
    return _view(updated, accounts, utc_now())


@router.post("/sms/learning/senders/untrust", response_model=LearningView)
def untrust_sender(body: SenderInput, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> LearningView:
    """Undo a sender's confirmation: its messages wait for review again, and the count starts over."""
    _manage(context)
    sender = _sender(body)

    def change(s: SmsSettings, _accounts: list[Any], _now: Any) -> None:
        if not SL.untrust_sender(s, sender):
            raise _missing("That trusted sender")

    updated, accounts, _ = _change(context, change)
    _audit(repository, context, "finance.sms.learning.untrust", "finance_sms_settings")
    return _view(updated, accounts, utc_now())


@router.post("/sms/learning/senders/clear-hold", response_model=LearningView)
def clear_hold(body: SenderInput, context: TenantContext = Depends(tenant_context),
               repository: AnumRepository = Depends(repository_context)) -> LearningView:
    """Trust a corrected sender again now instead of waiting for clean readings or the end of the hold."""
    _manage(context)
    sender = _sender(body)

    def change(s: SmsSettings, _accounts: list[Any], _now: Any) -> None:
        found = SL.track(s, sender)
        if found is None or found.corrected_at is None:
            raise _missing("A hold on that sender")
        found.corrected_at = None

    updated, accounts, _ = _change(context, change, bump=False)
    _audit(repository, context, "finance.sms.learning.clear_hold", "finance_sms_settings")
    return _view(updated, accounts, utc_now())


class CardInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last4: str = Field(pattern=r"^\d{4}$")
    account_id: str | None = Field(default=None, max_length=80)


@router.post("/sms/learning/cards/map", response_model=LearningView)
def map_card(body: CardInput, context: TenantContext = Depends(tenant_context),
             repository: AnumRepository = Depends(repository_context)) -> LearningView:
    """Map a card or account ending to one of the person's accounts (the proposal ANUM made, or the account they pick)."""
    _manage(context)

    def change(s: SmsSettings, accounts: list[Any], _now: Any) -> None:
        live = {a.id for a in accounts if not a.archived}
        if body.account_id is None or body.account_id not in live:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Pick the account this card or account belongs to.")
        if any(m.last4 == body.last4 for m in s.account_map):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Ending {body.last4} is already mapped. Change it in Settings.")
        if len(s.account_map) >= 50:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You have 50 card and account numbers. Remove one in Settings first.")
        s.account_map = [*s.account_map, AccountMapping(last4=body.last4, account_id=body.account_id)]
        SL.drop_card(s, body.last4, declined=False)

    updated, accounts, _ = _change(context, change)
    _audit(repository, context, "finance.sms.learning.map_card", "finance_sms_settings", {"account": body.account_id})
    return _view(updated, accounts, utc_now())


@router.post("/sms/learning/cards/decline", response_model=LearningView)
def decline_card(body: CardInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> LearningView:
    """Not this mapping: ANUM stops proposing it until the person picks another account for that ending."""
    _manage(context)

    def change(s: SmsSettings, _accounts: list[Any], _now: Any) -> None:
        SL.drop_card(s, body.last4, declined=True)

    updated, accounts, _ = _change(context, change, bump=False)
    _audit(repository, context, "finance.sms.learning.decline_card", "finance_sms_settings")
    return _view(updated, accounts, utc_now())


@router.post("/sms/learning/kinds/{memory_id}/activate", response_model=LearningView)
def activate_kind(memory_id: str, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> LearningView:
    """Apply a choice made once to the next messages now ("Treat money from X as Salary")."""
    _manage(context)

    def change(s: SmsSettings, _accounts: list[Any], now: Any) -> None:
        if SL.activate_kind(s, memory_id, now) is None:
            raise _missing("That remembered choice")

    updated, accounts, _ = _change(context, change, bump=False)
    _audit(repository, context, "finance.sms.learning.remember_kind", memory_id)
    return _view(updated, accounts, utc_now())


@router.delete("/sms/learning/kinds/{memory_id}", response_model=LearningView)
def forget_kind(memory_id: str, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> LearningView:
    _manage(context)

    def change(s: SmsSettings, _accounts: list[Any], _now: Any) -> None:
        if not SL.forget_kind(s, memory_id):
            raise _missing("That remembered choice")

    updated, accounts, _ = _change(context, change, bump=False)
    _audit(repository, context, "finance.sms.learning.forget_kind", memory_id)
    return _view(updated, accounts, utc_now())


def _disable_rule(finance: Any, context: TenantContext, rule_id: str, now: Any) -> bool:
    """Switch a learned finance rule off as a new approved version (the rules engine keeps every version)."""
    from . import rules_service as RS
    from .rules import active_rules
    from .rules_store import rules_store_for

    rules = rules_store_for(finance)
    versions = rules.list_versions(context, rule_id)
    current = next((r for r in active_rules(versions) if r.rule_id == rule_id), None) if versions else None
    if current is None or not current.content.enabled:
        return False
    content = current.content.model_copy(update={"enabled": False})
    made = RS.propose(finance, rules, context, content, provenance="owner", by=context.user_id, now=now, rule_id=rule_id)
    RS.decide(rules, context, rule_id, made.version, approve=True, payload=made.payload_hash, by=context.user_id, now=now)
    return True


@router.delete("/sms/learning/categories/{rule_id}", response_model=LearningView)
def forget_category(rule_id: str, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> LearningView:
    """Stop filing that merchant automatically: the learned rule is switched off (a new version) and the count forgotten."""
    _manage(context)
    now = utc_now()
    with svc.owner_lock(context), open_sms_stores(context) as (finance, store):
        store.lock_owner(context)
        settings = svc.settings_for(store, context)
        updated = settings.model_copy(deep=True)
        if SL.forget_category(updated, rule_id) is None:
            raise _missing("That learned category")
        _disable_rule(finance, context, rule_id, now)
        if not store.save_settings(context, updated, expected_version=settings.version):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Your SMS settings changed at the same moment. Try again.")
        accounts = finance.list_accounts(context)
    _audit(repository, context, "finance.sms.learning.forget_category", rule_id)
    return _view(updated, accounts, utc_now())


@router.post("/sms/learning/notices/{notice_id}/dismiss", response_model=LearningView)
def dismiss_notice(notice_id: str, context: TenantContext = Depends(tenant_context)) -> LearningView:
    _manage(context)

    def change(s: SmsSettings, _accounts: list[Any], _now: Any) -> None:
        if not SL.dismiss_notice(s, notice_id):
            raise _missing("That notice")

    updated, accounts, _ = _change(context, change, bump=False)
    return _view(updated, accounts, utc_now())


class AutoPostInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: bool


@router.post("/sms/learning/auto-post", response_model=LearningView)
def set_auto_post(body: AutoPostInput, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> LearningView:
    """Automatic recording on or off in one tap (the same switch as in Settings)."""
    _manage(context)

    def change(s: SmsSettings, _accounts: list[Any], _now: Any) -> None:
        s.auto_post = body.on

    updated, accounts, _ = _change(context, change)
    _audit(repository, context, "finance.sms.learning.auto_post", "finance_sms_settings", {"on": body.on})
    return _view(updated, accounts, utc_now())


class UndoLearningView(BaseModel):
    message_id: str
    status: str
    reason: str | None
    reversed_entry_id: str
    learning: LearningView


@router.post("/sms/messages/{message_id}/undo-learning", response_model=UndoLearningView)
def undo_learning(message_id: str, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> UndoLearningView:
    """A message ANUM recorded from something it learned goes back to Review, and that memory is forgotten."""
    _manage(context)
    now = utc_now()
    with svc.owner_lock(context), open_sms_stores(context) as (finance, store):
        store.lock_owner(context)
        message = store.get_message(context, message_id)
        if message is None:
            raise _missing("Message")
        learned = message.parsed.get("learned")
        entry = store.get_entry(context, message.entry_id) if message.entry_id and message.status == MessageStatus.POSTED else None
        if not isinstance(learned, dict) or entry is None or entry.status != EntryStatus.POSTED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This message was not recorded from something ANUM learned.")
        settings = svc.settings_for(store, context)
        updated = settings.model_copy(deep=True)
        ref = str(learned.get("ref") or "")
        if learned.get("what") == "kind":
            SL.forget_kind(updated, ref)
        elif learned.get("what") == "category" and SL.forget_category(updated, ref) is not None:
            _disable_rule(finance, context, ref, now)
        reversal = svc.reverse(finance, store, context, entry, now=now, why="learned choice undone by the owner")
        message.status, message.reason, message.entry_id, message.updated_at = MessageStatus.REVIEW, "learning_undone", None, now
        message.parsed = {k: v for k, v in message.parsed.items() if k != "learned"}
        store.save_message(message)
        if not store.save_settings(context, updated, expected_version=settings.version):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Your SMS settings changed at the same moment. Try again.")
        accounts = finance.list_accounts(context)
    _audit(repository, context, "finance.sms.learning.undo", message_id, {"what": str(learned.get("what")), "reversal": reversal.id})
    return UndoLearningView(message_id=message.id, status=message.status.value, reason=message.reason, reversed_entry_id=reversal.id,
                            learning=_view(updated, accounts, now))
