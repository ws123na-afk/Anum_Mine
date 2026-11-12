"""Learning from the person's reviews of bank messages (docs/finance-sms.md#learning).

Pure functions over ``SmsSettings`` (its ``learning``, ``auto_post_limits`` and ``memory``): no store, clock or
network. The caller reads the settings in its own unit of work, calls these, and saves them.

What is learned, and only from what the person did in Review:

* **A sender's format.** Every reading recorded with no reading field changed (the kind, amount, currency, merchant,
  date, reference, card) counts once; ``confirm_after`` of them in a row (default 2, 1 to 5) confirm the sender like
  ticking "This reading is right". A changed reading starts the count again and holds that sender's messages for
  review until it is clean again or ``HOLD_DAYS`` pass.
* **What money from or to a party is** (``KindMemory``): salary, a transfer from the person's own account, a refund.
  The same choice twice for the same sender and party is applied to the next message of the same direction.
* **A merchant's category** (``CategoryMemory``): the same category twice makes an approved ``learned`` finance rule,
  so the rules engine (docs/finance.md#rules-and-learning) files the next one, from any source.
* **An unmapped card ending**: the same account picked twice is *proposed* as a mapping; it is never mapped silently.

Nothing here makes a doubtful message post: currency doubts, ambiguous matches, possible duplicates, amounts above
the person's limit and recently corrected senders keep waiting (``hold_reason``), with the reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from ..schemas import new_id
from . import sms_parser as P
from .models import Account
from .rules import merchant_key
from .sms_store import (
    MAX_CARD_MEMORY,
    MAX_CHOICES,
    MAX_NOTICES,
    MAX_SENDER_TRACKS,
    CardMemory,
    CategoryMemory,
    KindMemory,
    LearningNotice,
    SenderMapping,
    SenderTrack,
    SmsMessage,
    SmsSettings,
)

REMEMBER_AFTER = 2  # the same choice this many times is applied to the next message
HOLD_DAYS = 14  # how long a corrected sender's messages wait, unless its readings are clean again first
# The fields that make up a reading of the message's text. The account and the category are the person's books,
# not the bank's format, so changing them does not count against the sender.
READING_FIELDS = ("kind", "amount", "currency", "converted_amount", "merchant", "occurred_on", "reference", "last4", "interest", "fee")
# Reasons that mean the reading itself was incomplete: recording such a message is not a right reading.
READING_PROBLEMS = frozenset({"missing_amount", "unknown_kind", "ambiguous_kind", "needs_converted_amount", "currency_mismatch",
                              "ambiguous_amount", "wallet_currency_missing"})
# Kinds a message names only roughly; a remembered kind may replace them. Money in, money out.
ROUGH_IN = frozenset({P.Kind.DEPOSIT, P.Kind.TRANSFER_IN})
ROUGH_OUT = frozenset({P.Kind.TRANSFER_OUT})
# Kinds worth remembering for a party, and the directions they apply to.
MEMORABLE = {P.Kind.SALARY: ("in",), P.Kind.REFUND: ("in",), P.Kind.OWN_TRANSFER: ("in", "out")}
# Reasons nothing learned may ever lift (docs/finance-sms.md#learning, "never automatic").
NEVER_AUTOMATIC = frozenset({"ambiguous_match", "needs_converted_amount", "currency_mismatch", "ambiguous_amount", "wallet_currency_missing",
                             "already_recorded", "above_auto_limit"})


def _text(value: object) -> str:
    return " ".join(str(value).split()).casefold() if value not in (None, "") else ""


def _same(field: str, before: object, after: object) -> bool:
    if field in ("amount", "converted_amount", "interest", "fee"):
        try:
            return before not in (None, "") and Decimal(str(before)) == Decimal(str(after))
        except (InvalidOperation, ValueError):
            return False
    if field == "merchant":
        return merchant_key(str(before or "")) == merchant_key(str(after or "")) and bool(before)
    return _text(before) == _text(after)


def reading_changed(message: SmsMessage, corrected: dict[str, Any]) -> bool:
    """Whether the person changed what ANUM read from the text (an empty field keeps the reading, so only sent fields count)."""
    if message.reason in READING_PROBLEMS:
        return True
    return any(field in corrected and not _same(field, message.parsed.get(field), corrected[field]) and not _named(field, message, corrected)
               for field in READING_FIELDS)


def _named(field: str, message: SmsMessage, corrected: dict[str, Any]) -> bool:
    """Naming what a rough credit or transfer was (salary, an own transfer) says what the money is, not that the text was misread."""
    if field != "kind":
        return False
    try:
        before, after = P.Kind(str(message.parsed.get("kind"))), P.Kind(str(corrected["kind"]))
    except ValueError:
        return False
    return before in ROUGH_IN | ROUGH_OUT and after in MEMORABLE and direction_of(before) in MEMORABLE[after]


def learns_from(message: SmsMessage) -> bool:
    """Only forwarded or pasted bank messages teach a sender's format (not Apple Pay or statement lines)."""
    return message.source in ("device", "paste") and bool(message.sender)


def track(settings: SmsSettings, sender: str) -> SenderTrack | None:
    return next((t for t in settings.memory.senders if t.sender == sender), None)


def _track_for(settings: SmsSettings, sender: str) -> SenderTrack:
    found = track(settings, sender)
    if found is None:
        found = SenderTrack(sender=sender)
        settings.memory.senders = [*settings.memory.senders, found][-MAX_SENDER_TRACKS:]
    return found


def _notice(settings: SmsSettings, now: datetime, **fields: Any) -> LearningNotice:
    notice = LearningNotice(id=new_id("fin_smslearn"), created_at=now, **fields)
    settings.memory.notices = [*settings.memory.notices, notice][-MAX_NOTICES:]
    return notice


def trust_sender(settings: SmsSettings, sender: str, bank_key: str | None) -> bool:
    """Confirm a sender's format (as the "This reading is right" tick does). True when it changed anything."""
    if not sender or sender in settings.verified_senders:
        return False
    settings.verified_senders = sorted({*settings.verified_senders, sender})
    if bank_key is None and not any(m.sender == sender for m in settings.extra_senders):
        settings.extra_senders = [*settings.extra_senders, SenderMapping(sender=sender, bank=sender[:40])]
    return True


def untrust_sender(settings: SmsSettings, sender: str) -> bool:
    """Undo a confirmation: the sender's messages wait for review again and the count starts over."""
    changed = sender in settings.verified_senders
    settings.verified_senders = [s for s in settings.verified_senders if s != sender]
    found = track(settings, sender)
    if found is not None:
        found.clean, found.auto_confirmed_at = 0, None
        changed = True
    settings.memory.notices = [n for n in settings.memory.notices if not (n.kind == "sender_trusted" and n.sender == sender)]
    return changed


def held_until(settings: SmsSettings, sender: str) -> datetime | None:
    """When a corrected sender's hold ends, or None when it is not held."""
    found = track(settings, sender)
    if found is None or found.corrected_at is None or found.clean >= settings.learning.confirm_after:
        return None
    return found.corrected_at + timedelta(days=HOLD_DAYS)


def limit_for(settings: SmsSettings, currency: str | None) -> Decimal | None:
    return next((item.amount for item in settings.auto_post_limits if item.currency == currency), None)


def hold_reason(settings: SmsSettings, sender: str | None, amount: Decimal | None, currency: str | None, now: datetime) -> str | None:
    """Why a message that could post on its own must wait anyway: above the person's limit, or a sender corrected lately."""
    limit = limit_for(settings, currency)
    if limit is not None and amount is not None and amount > limit:
        return "above_auto_limit"
    if sender:
        until = held_until(settings, sender)
        if until is not None and now < until:
            return "sender_corrected_recently"
    return None


# -- remembered kinds ------------------------------------------------------------------------------------


def direction_of(kind: P.Kind) -> str:
    return "in" if kind in P.MONEY_IN else "out"


def find_kind(settings: SmsSettings, sender: str, direction: str, party: str) -> KindMemory | None:
    return next((m for m in settings.memory.kinds if m.sender == sender and m.direction == direction and m.party == party), None)


@dataclass
class Applied:
    """A remembered choice applied to an incoming message (stored on the message as ``parsed.learned``)."""

    kind: P.Kind
    counter_account_id: str | None
    memory: KindMemory

    def record(self) -> dict[str, str]:
        out = {"what": "kind", "value": self.kind.value, "ref": self.memory.id, "party": self.memory.party_label}
        if self.counter_account_id:
            out["counter_account_id"] = self.counter_account_id
        return out


def remembered_kind(settings: SmsSettings, sender: str, parsed: P.Parsed, accounts: list[Account]) -> Applied | None:
    """The kind the person chose twice for this sender and party, for a message whose kind is only rough."""
    if not settings.learning.enabled or parsed.kind not in ROUGH_IN | ROUGH_OUT:
        return None
    party = merchant_key(parsed.merchant)
    if not party:
        return None
    found = find_kind(settings, sender, direction_of(parsed.kind), party)
    if found is None or not found.active:
        return None
    kind = P.Kind(found.kind)
    if kind == P.Kind.OWN_TRANSFER:
        live = {a.id for a in accounts if not a.archived}
        if found.counter_account_id not in live:
            return None  # the account was archived or deleted: wait for the person
    return Applied(kind, found.counter_account_id, found)


def _remember_kind(settings: SmsSettings, message: SmsMessage, parsed_kind: P.Kind | None, chosen: P.Kind, party_label: str | None,
                   counter_account_id: str | None, *, at_once: bool, now: datetime) -> LearningNotice | None:
    party = merchant_key(party_label)
    if not party or parsed_kind is None:
        return None
    directions = MEMORABLE.get(chosen)
    if chosen in ROUGH_IN | ROUGH_OUT:
        # The person kept or chose the rough kind: a memory for this party that said otherwise no longer holds.
        found = find_kind(settings, message.sender, direction_of(chosen), party)
        if found is not None:
            settings.memory.kinds = [m for m in settings.memory.kinds if m.id != found.id]
        return None
    if directions is None or parsed_kind not in ROUGH_IN | ROUGH_OUT:
        return None
    direction = direction_of(parsed_kind)
    if direction not in directions:
        return None
    counter = counter_account_id if chosen == P.Kind.OWN_TRANSFER else None
    found = find_kind(settings, message.sender, direction, party)
    if found is None:
        found = KindMemory(id=new_id("fin_smskind"), sender=message.sender, direction=direction, party=party, party_label=(party_label or party)[:80],
                           kind=chosen.value, counter_account_id=counter, updated_at=now)
        settings.memory.kinds = [*settings.memory.kinds, found][-MAX_CHOICES:]
    if found.kind != chosen.value or found.counter_account_id != counter:
        found.kind, found.counter_account_id, found.count, found.active, found.since = chosen.value, counter, 0, False, None
    found.count += 1
    found.updated_at = now
    if found.active or not settings.learning.enabled:
        return None
    if at_once or found.count >= REMEMBER_AFTER:
        found.active, found.since = True, now
        return _notice(settings, now, kind="kind_remembered", sender=message.sender, party=found.party_label, value=found.kind,
                       account_id=counter, ref=found.id)
    return None


def activate_kind(settings: SmsSettings, memory_id: str, now: datetime) -> KindMemory | None:
    found = next((m for m in settings.memory.kinds if m.id == memory_id), None)
    if found is not None and not found.active:
        found.active, found.since, found.updated_at = True, now, now
    return found


def forget_kind(settings: SmsSettings, memory_id: str) -> bool:
    before = len(settings.memory.kinds)
    settings.memory.kinds = [m for m in settings.memory.kinds if m.id != memory_id]
    settings.memory.notices = [n for n in settings.memory.notices if n.ref != memory_id]
    return len(settings.memory.kinds) != before


# -- categories ------------------------------------------------------------------------------------------


def count_category(settings: SmsSettings, merchant: str | None, category: str, now: datetime) -> CategoryMemory | None:
    """Count one explicit category choice for a merchant. Returns the memory when it reached ``REMEMBER_AFTER`` and has no rule yet."""
    party = merchant_key(merchant)
    if not party:
        return None
    found = next((m for m in settings.memory.categories if m.party == party), None)
    if found is None:
        found = CategoryMemory(party=party, party_label=(merchant or party)[:80], category=category, updated_at=now)
        settings.memory.categories = [*settings.memory.categories, found][-MAX_CHOICES:]
    if found.category != category:
        found.category, found.count, found.rule_id = category, 0, None
    found.count += 1
    found.updated_at = now
    if settings.learning.enabled and found.rule_id is None and found.count >= REMEMBER_AFTER:
        return found
    return None


def category_by_rule(settings: SmsSettings, rule_id: str | None) -> CategoryMemory | None:
    if not rule_id:
        return None
    return next((m for m in settings.memory.categories if m.rule_id == rule_id), None)


def forget_category(settings: SmsSettings, rule_id: str) -> CategoryMemory | None:
    found = category_by_rule(settings, rule_id)
    if found is not None:
        settings.memory.categories = [m for m in settings.memory.categories if m is not found]
    settings.memory.notices = [n for n in settings.memory.notices if n.ref != rule_id]
    return found


# -- cards -----------------------------------------------------------------------------------------------


def card_is_unmapped(settings: SmsSettings, last4: str | None, accounts: list[Account]) -> bool:
    if not last4:
        return False
    live = [a for a in accounts if not a.archived]
    if any(m.last4 == last4 for m in settings.account_map):
        return False
    return len([a for a in live if a.last4 == last4]) != 1


def _count_card(settings: SmsSettings, last4: str, account_id: str, now: datetime) -> LearningNotice | None:
    found = next((c for c in settings.memory.cards if c.last4 == last4), None)
    if found is None:
        found = CardMemory(last4=last4, account_id=account_id, updated_at=now)
        settings.memory.cards = [*settings.memory.cards, found][-MAX_CARD_MEMORY:]
    if found.account_id != account_id:
        found.account_id, found.count, found.declined = account_id, 0, False
    found.count += 1
    found.updated_at = now
    if found.count == REMEMBER_AFTER and not found.declined and settings.learning.enabled:
        return _notice(settings, now, kind="card_proposed", last4=last4, account_id=account_id)
    return None


def card_proposal(settings: SmsSettings, last4: str | None) -> CardMemory | None:
    found = next((c for c in settings.memory.cards if c.last4 == last4), None)
    if found is None or found.declined or found.count < REMEMBER_AFTER or not settings.learning.enabled:
        return None
    return found


def drop_card(settings: SmsSettings, last4: str, *, declined: bool) -> None:
    found = next((c for c in settings.memory.cards if c.last4 == last4), None)
    if found is not None:
        if declined:
            found.declined = True
        else:
            settings.memory.cards = [c for c in settings.memory.cards if c.last4 != last4]
    settings.memory.notices = [n for n in settings.memory.notices if not (n.kind == "card_proposed" and n.last4 == last4)]


# -- after the person recorded a message -----------------------------------------------------------------


@dataclass
class Learned:
    notices: list[LearningNotice]
    trusted_changed: bool  # verified_senders changed: the settings' version moves
    category_rule: CategoryMemory | None  # make an approved learned finance rule from this


def on_recorded(settings: SmsSettings, message: SmsMessage, corrected: dict[str, Any], *, chosen_kind: P.Kind, picked_account_id: str | None,
                counter_account_id: str | None, merchant: str | None, explicit_confirm: bool, remember: bool, accounts: list[Account],
                now: datetime) -> Learned:
    """Update the memory after the person recorded (or corrected) one message. Mutates ``settings``."""
    notices: list[LearningNotice] = []
    trusted_changed = False
    category_rule = None
    if learns_from(message):
        found = _track_for(settings, message.sender)
        if explicit_confirm:
            found.corrected_at = None  # the person said the format is right: no hold
        elif reading_changed(message, corrected):
            found.clean, found.corrected_at = 0, now
        else:
            found.clean += 1
            if (settings.learning.enabled and not explicit_confirm and message.sender not in settings.verified_senders
                    and found.clean >= settings.learning.confirm_after):
                trusted_changed = trust_sender(settings, message.sender, message.bank_key)
                if trusted_changed:
                    found.auto_confirmed_at = now
                    notices.append(_notice(settings, now, kind="sender_trusted", sender=message.sender))
        try:
            parsed_kind = P.Kind(str(message.parsed.get("kind") or "unknown"))
        except ValueError:
            parsed_kind = P.Kind.UNKNOWN
        made = _remember_kind(settings, message, parsed_kind, chosen_kind, merchant, counter_account_id if chosen_kind == P.Kind.OWN_TRANSFER else None,
                              at_once=remember, now=now)
        if made is not None:
            notices.append(made)
        last4 = str(message.parsed.get("last4") or "") or None
        if picked_account_id and last4 and card_is_unmapped(settings, last4, accounts):
            proposed = _count_card(settings, last4, picked_account_id, now)
            if proposed is not None:
                notices.append(proposed)
    category = corrected.get("category")
    if category and chosen_kind in (P.Kind.CARD_PURCHASE, P.Kind.TRANSFER_OUT):
        category_rule = count_category(settings, merchant, str(category), now)
    return Learned(notices, trusted_changed, category_rule)


def category_notice(settings: SmsSettings, memory: CategoryMemory, rule_id: str, now: datetime) -> LearningNotice:
    memory.rule_id = rule_id
    return _notice(settings, now, kind="category_remembered", party=memory.party_label, value=memory.category, ref=rule_id)


def recheck_notice(settings: SmsSettings, count: int, now: datetime, *, sender: str | None = None, last4: str | None = None) -> LearningNotice | None:
    """'N waiting messages from X were recorded', when a re-check recorded any."""
    if count <= 0:
        return None
    return _notice(settings, now, kind="rechecked", value=str(count), sender=sender, last4=last4)


def dismiss_notice(settings: SmsSettings, notice_id: str) -> bool:
    before = len(settings.memory.notices)
    settings.memory.notices = [n for n in settings.memory.notices if n.id != notice_id]
    return len(settings.memory.notices) != before


# -- why a message waits, and the one action that makes it automatic next time ---------------------------------


@dataclass
class Fix:
    action: str  # trust_sender, map_card, auto_post_on, clear_hold, remember_kind, undo_learning, undo_recheck, open_settings, decide, record
    sender: str | None = None
    last4: str | None = None
    account_id: str | None = None
    party: str | None = None
    kind: str | None = None
    ref: str | None = None


def fix_for(message: SmsMessage, settings: SmsSettings, accounts: list[Account]) -> Fix | None:
    """The single action that stops this kind of message waiting, in the person's terms. None for settled messages."""
    reason = message.reason or ""
    learned = message.parsed.get("learned")
    if message.status.value == "posted" and isinstance(learned, dict) and learned.get("what") == "recheck":
        return Fix("undo_recheck", sender=message.sender)
    if message.status.value == "posted" and isinstance(learned, dict):
        return Fix("undo_learning", party=str(learned.get("party") or "") or None, kind=str(learned.get("value") or "") or None,
                   ref=str(learned.get("ref") or "") or None)
    if message.status.value == "posted":
        parsed_kind = str(message.parsed.get("kind") or "")
        party = merchant_key(str(message.parsed.get("merchant") or ""))
        if parsed_kind in (P.Kind.DEPOSIT.value, P.Kind.TRANSFER_IN.value) and party:
            found = find_kind(settings, message.sender, "in", party)
            if found is not None and not found.active:
                return Fix("remember_kind", sender=message.sender, party=found.party_label, kind=found.kind, ref=found.id)
            return Fix("record", party=str(message.parsed.get("merchant")))
        return None
    if message.status.value not in ("review", "failed", "duplicate"):
        return None
    if message.status.value == "duplicate" or reason in NEVER_AUTOMATIC:
        if reason == "above_auto_limit":
            return Fix("open_settings")
        return Fix("decide")
    if reason in ("format_unverified", "unknown_sender"):
        return Fix("trust_sender", sender=message.sender)
    if reason == "sender_corrected_recently":
        return Fix("clear_hold", sender=message.sender)
    if reason == "auto_post_off":
        return Fix("auto_post_on")
    if reason == "wallet_card_unknown":
        return Fix("open_settings")
    if reason in ("account_unknown", "loan_account_unknown"):
        last4 = str(message.parsed.get("last4") or "") or None
        if last4:
            proposal = card_proposal(settings, last4)
            return Fix("map_card", last4=last4, account_id=proposal.account_id if proposal else None)
        return Fix("open_settings")
    return Fix("record")

