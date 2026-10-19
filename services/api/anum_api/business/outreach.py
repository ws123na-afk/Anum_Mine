"""The outreach outbox: gate, approval, send, follow-ups, stop-on-reply (docs/business.md#outreach).

Harvey's outbox ladder and deterministic gate (docs/business/HARVEY_PARITY.md), written as
ANUM code on ANUM's own approval path:

* **Queue**: an approved draft becomes an outbox row only after the deterministic pre-send gate
  passes (recipient matches the contact, consent basis, the opt-out list and do-not-contact,
  a bounced address, subject and body limits, merge tags, banned phrases, at most one link, plain
  text, an opt-out line). The row then asks for an approval of ``business.send_outreach`` (high
  risk, always asks: autonomy can never lower it), bound by the payload hash to the exact channel,
  recipient, subject and text.
* **Send**: the tool runs only after an owner approves with the payload hash they were shown, and
  re-checks everything at send time: sending is on (it is **off by default**), outreach is not
  paused (kill switch), today's per-channel quota is not used up, the lead has not replied or
  been closed (stop-on-reply), the draft and contact did not change, and the gate passes again.
  Only then does it hand the message to the connected channel tool (e-mail or WhatsApp), whose
  own policy decision and the contact suppression check still apply. Anything else holds or
  cancels the row and sends nothing; a failed send is recorded as failed, never as a contact.
* **Follow-ups**: a follow-up is its own draft and its own approval, scheduled a number of days
  after the previous step; at most ``follow_up_max`` per lead.
* **Replies**: a reply, opt-out or bounce (recorded by a person, or noticed in the e-mail inbox
  or WhatsApp) cancels the lead's open rows. Opt-out phrases are honoured in code whatever any
  model says: the contact goes on the tenant's opt-out list and is marked do not contact. A bounce
  blocks that address, and above the bounce threshold outreach pauses itself (kill switch).

No LinkedIn or browser automation, no call automation, no autonomous sending.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from .. import suppression
from ..agent_tools import ToolCall, ToolDefinition, ToolPolicyOutcome, ToolResult
from ..repository import AnumRepository
from ..schemas import ApprovalStatus, RiskLevel, TenantContext, new_id, utc_now
from . import assist
from .models import CONTACTABLE_BASES, Contact, Draft, DraftChannel, DraftStatus, Lead, LeadState
from .outreach_models import OPEN_OUTBOX, SENDABLE_CHANNELS, OutboxItem, OutboxStatus, ReplyEvent, ReplyKind

# Rows that hold their draft's and their step's place (the unique indexes of migration 0079).
LIVE_OUTBOX = frozenset(set(OPEN_OUTBOX) | {OutboxStatus.SENDING, OutboxStatus.SENT, OutboxStatus.NEEDS_REVIEW})
# A claim older than this with no provider id is an interrupted send: reviewed by a person, never resent.
STALE_SENDING = timedelta(minutes=10)
# Serializes claims inside this process (the memory store has no row locks); PostgreSQL also locks
# the workspace's settings row and the outbox row FOR UPDATE in the same short transaction.
_claim_lock = threading.Lock()
from .pipeline import load_settings, save_settings
from .service import audit, contact_block, draft_hash, http_error, record_activity, stamp_state
from .store import BusinessStore, Query, open_business_store

logger = logging.getLogger("anum.business")

SEND_TOOL = "business.send_outreach"
MAX_SUBJECT = 90
MAX_EMAIL_WORDS = 220
MAX_LINKS = 1
# Leads in these states get no more outreach: a person is in a conversation, or it is closed.
STOP_STATES = frozenset({LeadState.REPLIED, LeadState.MEETING, LeadState.OFFERED, LeadState.WON, LeadState.LOST, LeadState.SUPPRESSED})
SENDABLE_STATES = frozenset({LeadState.QUALIFIED, LeadState.CONTACTED})
MIN_SENDS_FOR_KILL_SWITCH = 10
BOUNCE_WINDOW_DAYS = 30

# Spam triggers and machine-writing tells that never go out (enforced here, not only in a prompt).
BANNED_PHRASES = (
    "act now", "buy now", "click here", "click below", "limited time", "limited offer", "exclusive deal", "special offer",
    "risk-free", "risk free", "no obligation", "100% free", "make money", "double your", "guaranteed results", "winner",
    "congratulations", "i hope this finds you well", "hope this email finds you well", "picture this", "what if i told you",
    "in today's fast-paced world", "game-changer", "game changer", "cutting-edge solution", "revolutionize your",
    "unlock your", "supercharge your", "as an ai", "i am an ai", "i'm an ai", "as a language model",
)
MERGE_TAG = re.compile(r"\{\{\s*[\w.]+\s*\}\}|\{\s*\w+\s*\}|\[\s*(first[ _]?name|name|company|company[ _]?name)\s*\]", re.IGNORECASE)
URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
HTML = re.compile(r"<\s*(html|body|div|table|img|a|p|br|span)\b", re.IGNORECASE)
EMAIL_SHAPE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
# Opt-out wording honoured in code (a whole-message keyword is checked by suppression.keyword_of too).
OPT_OUT_PHRASES = (
    "unsubscribe", "opt out", "opt-out", "remove me", "take me off", "stop emailing", "stop contacting", "stop sending",
    "stop messaging", "do not contact", "do not email", "don't contact", "don't email", "dont contact", "never contact",
    "delete my info", "delete my data", "not interested, stop", "الغاء الاشتراك", "إلغاء الاشتراك", "لا تتواصل", "لا تراسل",
    "توقف عن", "احذف بياناتي", "لا ترسل",
)
ESCALATION_PHRASES = ("lawyer", "attorney", "legal action", "lawsuit", "cease and desist", "harassment", "harassing", "report you",
                      "spam complaint", "محامي", "شكوى", "قضية")


# -- the deterministic gate -------------------------------------------------------------------------


def recipient_of(contact: Contact | None, channel: str) -> str | None:
    if contact is None:
        return None
    if channel == "email":
        value = (contact.email or "").strip().lower()
        return value or None
    if channel == "whatsapp":
        normalized = suppression.normalize(contact.phone or "") if contact.phone else None
        return normalized[1] if normalized and normalized[0] == "phone" else None
    return None


def recipient_hash(context: TenantContext, value: str | None) -> str | None:
    normalized = suppression.normalize(value or "") if value else None
    return suppression.recipient_hash(context.tenant_id, normalized[1]) if normalized else None


def content_issues(channel: str, subject: str, body: str) -> list[str]:
    """Checks on the text alone (the gate's content half)."""
    issues: list[str] = []
    subject, body = subject.strip(), body.strip()
    if not body:
        issues.append("The message is empty.")
    if channel == "email":
        if not subject:
            issues.append("An e-mail needs a subject.")
        if len(subject) > MAX_SUBJECT:
            issues.append(f"The subject is {len(subject)} characters; keep it to {MAX_SUBJECT}.")
        words = len(body.split())
        if words > MAX_EMAIL_WORDS:
            issues.append(f"The e-mail is {words} words; a first message stays under {MAX_EMAIL_WORDS}.")
    leftover = MERGE_TAG.findall(subject + " " + body)
    if leftover:
        issues.append("Fill in the merge tag left in the text.")
    lowered = (subject + " " + body).casefold()
    hits = [phrase for phrase in BANNED_PHRASES if phrase in lowered]
    if hits:
        issues.append(f"Remove spam-like wording: {', '.join(hits[:3])}.")
    links = URL.findall(body)
    if len(links) > MAX_LINKS:
        issues.append(f"The message has {len(links)} links; at most {MAX_LINKS} goes out.")
    if HTML.search(body):
        issues.append("Plain text only: remove the HTML markup.")
    if not assist.OPT_OUT_WORDS.search(body):
        issues.append("Say how to opt out (for example: reply STOP).")
    if chr(0x2014) in subject + body or chr(0x2013) in subject + body:
        issues.append("Use plain punctuation (no long dashes).")
    return issues


def gate(context: TenantContext, store: BusinessStore, draft: Draft, lead: Lead, contact: Contact | None, channel: str, recipient: str | None) -> list[str]:
    """Every deterministic pre-send check; an empty list means the message may ask for approval."""
    issues: list[str] = []
    if channel not in SENDABLE_CHANNELS:
        issues.append("Only e-mail and WhatsApp drafts can be sent from ANUM; send LinkedIn messages and call scripts yourself.")
        return issues
    if draft.status != DraftStatus.APPROVED or draft.approved_hash != draft_hash(draft.subject, draft.body, draft.channel.value, draft.contact_id):
        issues.append("Approve the draft's exact text first.")
    if contact is None or draft.contact_id != contact.id or lead.contact_id != contact.id:
        issues.append("The draft must be for the lead's contact.")
    expected = recipient_of(contact, channel)
    if expected is None:
        issues.append("The contact has no e-mail address." if channel == "email" else "The contact has no WhatsApp number.")
    elif recipient is not None and recipient != expected:
        issues.append("The recipient no longer matches the contact record.")
    elif channel == "email" and not EMAIL_SHAPE.match(expected):
        issues.append("The contact's e-mail address is not valid.")
    if contact is not None and contact.consent_basis not in CONTACTABLE_BASES:
        issues.append(f"Record how ANUM may contact {contact.name} (consent basis) first; unknown stays unknown.")
    account = store.get("accounts", lead.account_id)
    block = contact_block(context, contact, bool(account and account.do_not_contact))
    if block:
        issues.append(block)
    if lead.state in STOP_STATES:
        issues.append(f"The lead is {lead.state.value}: outreach to it has stopped.")
    elif lead.state not in SENDABLE_STATES:
        issues.append("Qualify the lead before any outreach.")
    digest = recipient_hash(context, expected)
    if digest and store.count("replies", Query(where={"recipient_hash": digest, "kind": ReplyKind.BOUNCE})):
        issues.append("Messages to this address bounced before; fix the contact record first.")
    issues.extend(content_issues(channel, draft.subject, draft.body))
    figures = assist.check_draft(draft.channel, draft.language, draft.subject, draft.body, _facts(store, lead))
    issues.extend(item for item in figures if item not in issues and "opt out" not in item.lower() and "long dashes" not in item)
    return issues


def _facts(store: BusinessStore, lead: Lead) -> str:
    account = store.get("accounts", lead.account_id)
    product = store.get("products", lead.product_id) if lead.product_id else None
    offer = store.first("offers", product_id=product.id, status="active") if product else None
    evidence, _ = store.find("evidence", Query(where={"lead_id": lead.id}, limit=50))
    from .pipeline import value_proposition

    return assist.facts_text(product, offer, evidence, account, value_proposition(store, lead.product_id)) if account else ""


# -- quotas, pause and metrics ----------------------------------------------------------------------


def sent_today(store: BusinessStore, channel: str, now: datetime) -> int:
    """Today's (UTC) used quota slots on a channel: messages sent, plus those claimed and sending
    (a claim reserves its slot; a failed send releases it)."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    used = {OutboxStatus.SENT, OutboxStatus.SENDING, OutboxStatus.NEEDS_REVIEW}
    items, _ = store.find("outbox", Query(where={"channel": channel, "status": used}, limit=2000, order_by="updated_at"))
    return sum(1 for item in items if (item.sent_at or item.updated_at) >= start)


def bounce_rate(store: BusinessStore, now: datetime) -> tuple[int, int]:
    """(bounces, sends) in the last 30 days."""
    since = now - timedelta(days=BOUNCE_WINDOW_DAYS)
    bounces = store.count("replies", Query(where={"kind": ReplyKind.BOUNCE}, created_from=since))
    items, _ = store.find("outbox", Query(where={"status": OutboxStatus.SENT}, limit=5000, order_by="updated_at"))
    sends = sum(1 for item in items if item.sent_at is not None and item.sent_at >= since)
    return bounces, sends


def check_kill_switch(store: BusinessStore, context: TenantContext) -> str | None:
    """Pause outreach when the bounce rate passes the threshold (after at least 10 sends)."""
    settings = load_settings(store, context)
    bounces, sends = bounce_rate(store, utc_now())
    if settings.paused or settings.max_bounce_rate_pct <= 0 or sends < MIN_SENDS_FOR_KILL_SWITCH:
        return None
    if bounces * 100.0 / sends > settings.max_bounce_rate_pct:
        reason = f"Bounce rate {bounces} of {sends} in {BOUNCE_WINDOW_DAYS} days passed {settings.max_bounce_rate_pct:g}%: check the list before resuming."
        settings.paused, settings.paused_reason, settings.paused_at, settings.updated_by = True, reason[:300], utc_now(), "system:bounce_kill_switch"
        save_settings(store, settings)
        return reason
    return None


def metrics(store: BusinessStore, days: int, now: datetime) -> dict[str, Any]:
    """Outreach numbers for a window, every rate with its denominator (no fabricated figures)."""
    since = now - timedelta(days=days)
    items, total_items = store.find("outbox", Query(created_from=since, limit=5000, order_by="created_at"))
    sent = [item for item in items if item.status == OutboxStatus.SENT]
    replies, _ = store.find("replies", Query(created_from=since, limit=5000, order_by="created_at"))
    count = {kind: sum(1 for item in replies if item.kind == kind) for kind in ReplyKind}

    def rate(part: int, whole: int) -> dict[str, Any]:
        return {"count": part, "of": whole, "rate_pct": round(part * 100.0 / whole, 1) if whole else None}

    by_status = {status.value: sum(1 for item in items if item.status == status) for status in OutboxStatus}
    opt_out = rate(count[ReplyKind.OPT_OUT], len(sent))
    return {
        "days": days,
        "sent": {channel: sum(1 for item in sent if item.channel == channel) for channel in SENDABLE_CHANNELS},
        "sent_total": len(sent),
        "by_status": by_status,
        "replies": rate(count[ReplyKind.REPLY] + count[ReplyKind.ESCALATE], len(sent)),
        "opt_outs": opt_out,
        "bounces": rate(count[ReplyKind.BOUNCE], len(sent)),
        "escalations": count[ReplyKind.ESCALATE],
        "opt_out_alarm": bool(opt_out["rate_pct"] is not None and opt_out["rate_pct"] > 2.0 and len(sent) >= MIN_SENDS_FOR_KILL_SWITCH),
        "truncated": total_items > len(items),
    }


# -- queue and approval -----------------------------------------------------------------------------


def send_definition() -> ToolDefinition:
    return ToolDefinition(
        name=SEND_TOOL,
        description="Send one approved outreach message (e-mail or WhatsApp) for a Business lead, after the send-time checks.",
        risk_level=RiskLevel.HIGH,
        required_roles=frozenset({"owner", "member"}),
        timeout_seconds=60,
        idempotent=True,
        category="marketing",
        action_kind="send",
        always_ask=True,
    )


def send_arguments(item: OutboxItem, company: str) -> dict[str, Any]:
    """What the approver sees, and the payload hash binds."""
    return {
        "outbox_id": item.id,
        "lead_id": item.lead_id,
        "company": company,
        "channel": item.channel,
        "to": item.recipient,
        "subject": item.subject,
        "text": item.body,
        "step": item.step,
        "content_hash": item.content_hash,
        "purpose": "marketing",
    }


def item_hash(item: OutboxItem) -> str:
    return hashlib.sha256("\x1f".join([item.channel, item.recipient, item.subject, item.body]).encode("utf-8")).hexdigest()


def _company(store: BusinessStore, lead: Lead) -> str:
    account = store.get("accounts", lead.account_id)
    return account.name if account else ""


def ask_approval(repository: AnumRepository, context: TenantContext, store: BusinessStore, item: OutboxItem, lead: Lead) -> OutboxItem:
    from .tools import request_approval

    company = _company(store, lead)
    task, approval = request_approval(repository, context, send_arguments(item, company), f"Outreach to {company} ({item.channel}, step {item.step})",
                                      tool=SEND_TOOL, prompt=f"Send the approved {item.channel} message to {company}.")
    item.approval_id, item.approval_task_id = approval.id, task.id
    item.status = OutboxStatus.PENDING_APPROVAL
    item.note = "Waiting for an owner's approval in Approvals."
    item.updated_at = utc_now()
    return item


def queue_draft(context: TenantContext, repository: AnumRepository, draft_id: str, *, follow_up_of: str | None, send_after_days: int | None) -> OutboxItem:
    now = utc_now()
    with open_business_store(context) as store:
        settings = load_settings(store, context)
        draft = store.get("drafts", draft_id, for_update=True)
        if draft is None:
            raise http_error(404, "Draft not found")
        lead = store.get("leads", draft.lead_id, for_update=True)
        if lead is None:  # pragma: no cover - foreign key
            raise http_error(404, "Lead not found")
        if settings.paused:
            raise http_error(409, f"Outreach is paused: {settings.paused_reason or 'an owner paused it'}.")
        if store.first("replies", lead_id=lead.id) is not None:
            raise http_error(409, "The lead has replied, so outreach and follow-ups have stopped. Continue the conversation from your inbox.")
        existing, _ = store.find("outbox", Query(where={"draft_id": draft.id, "status": LIVE_OUTBOX}, limit=5))
        if existing:
            raise http_error(409, "This draft is already in the outbox.")
        contact = store.get("contacts", draft.contact_id) if draft.contact_id else None
        channel = draft.channel.value
        recipient = recipient_of(contact, channel)
        issues = gate(context, store, draft, lead, contact, channel, recipient)
        if issues:
            raise http_error(422, "Not queued: " + " ".join(issues))
        step, previous, send_after = 1, None, None
        if follow_up_of:
            previous = store.get("outbox", follow_up_of)
            if previous is None or previous.lead_id != lead.id:
                raise http_error(404, "The message this follows up was not found for this lead.")
            if previous.status not in {OutboxStatus.HELD, OutboxStatus.SENT}:
                raise http_error(409, "Queue a follow-up after the previous message was approved.")
            if previous.channel != channel or previous.contact_id != draft.contact_id:
                raise http_error(422, "A follow-up goes to the same contact on the same channel.")
            # The cap counts every live follow-up of the sequence, whichever step it was built from.
            sequence = previous.sequence_id or previous.id
            live, _ = store.find("outbox", Query(where={"sequence_id": sequence, "status": LIVE_OUTBOX}, limit=50))
            follow_ups = [row for row in live if row.step > 1]
            if len(follow_ups) >= settings.follow_up_max:
                raise http_error(409, f"At most {settings.follow_up_max} follow-up(s) per lead (Business settings); this sequence has {len(follow_ups)}.")
            step = max([previous.step, *(row.step for row in live)]) + 1
            days = max(settings.follow_up_min_days, int(send_after_days or settings.follow_up_min_days))
            send_after = (previous.sent_at or now) + timedelta(days=days)
        item = OutboxItem(
            id=new_id("bout"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead.id, contact_id=contact.id,  # type: ignore[union-attr]
            draft_id=draft.id, channel=channel, step=step, follow_up_of=previous.id if previous else None,
            sequence_id=(previous.sequence_id or previous.id) if previous else "", recipient=recipient or "",
            subject=draft.subject, body=draft.body, content_hash=draft.approved_hash or "", status=OutboxStatus.SCHEDULED,
            send_after=send_after, created_by=context.user_id, created_at=now, updated_at=now,
        )
        item.sequence_id = item.sequence_id or item.id
        if send_after is not None and send_after > now:
            item.note = f"Scheduled: ask for approval on or after {send_after.date().isoformat()}."
        else:
            ask_approval(repository, context, store, item, lead)
        store.add("outbox", item)
        record_activity(store, context, "outreach_queued", f"Outreach step {step} queued ({channel}).", lead_id=lead.id, account_id=lead.account_id,
                        metadata={"outbox_id": item.id, "step": step, "status": item.status.value})
    audit(repository, context, "business.outreach.queued", item.id, metadata={"lead_id": item.lead_id, "channel": channel, "step": step, "status": item.status.value})
    return item


def request_again(context: TenantContext, repository: AnumRepository, item_id: str) -> OutboxItem:
    """Ask for approval of a due follow-up, or again for a held or rejected row (the gate runs again)."""
    now = utc_now()
    with open_business_store(context) as store:
        item = store.get("outbox", item_id, for_update=True)
        if item is None:
            raise http_error(404, "Outbox message not found")
        settings = load_settings(store, context)
        if settings.paused:
            raise http_error(409, f"Outreach is paused: {settings.paused_reason or 'an owner paused it'}.")
        if item.status not in {OutboxStatus.SCHEDULED, OutboxStatus.HELD, OutboxStatus.REJECTED}:
            raise http_error(409, f"This message is {item.status.value.replace('_', ' ')}.")
        if item.send_after is not None and item.send_after > now:
            raise http_error(409, f"This follow-up is due on {item.send_after.date().isoformat()}.")
        lead = store.get("leads", item.lead_id)
        draft = store.get("drafts", item.draft_id)
        contact = store.get("contacts", item.contact_id)
        if lead is None or draft is None:
            raise http_error(404, "The lead or draft is gone.")
        if store.first("replies", lead_id=lead.id) is not None:
            raise http_error(409, "The lead has replied, so outreach and follow-ups have stopped.")
        if draft.approved_hash != item.content_hash:
            raise http_error(409, "The draft changed after it was queued. Approve the new text and queue it again.")
        issues = gate(context, store, draft, lead, contact, item.channel, item.recipient)
        if issues:
            item.gate_issues, item.updated_at = issues, now
            store.save("outbox", item)
            raise http_error(422, "Not sent for approval: " + " ".join(issues))
        ask_approval(repository, context, store, item, lead)
        item.gate_issues = []
        store.save("outbox", item)
    audit(repository, context, "business.outreach.approval_requested", item.id, metadata={"lead_id": item.lead_id, "step": item.step})
    return item


def sync_approval(store: BusinessStore, repository: AnumRepository, context: TenantContext, item: OutboxItem) -> tuple[OutboxItem, Any]:
    """The row with its approval; a rejected or lapsed request is shown as rejected here."""
    approval = repository.get_approval(item.approval_id, context) if item.approval_id else None
    item = reconcile_sending(store, item)
    if item.status != OutboxStatus.PENDING_APPROVAL or approval is None:
        return item, approval
    lapsed = approval.status == ApprovalStatus.EXPIRED or (
        approval.status == ApprovalStatus.PENDING and approval.expires_at is not None and approval.expires_at <= utc_now()
    )
    if approval.status == ApprovalStatus.APPROVED:
        # Approved, but the tool never updated the row: the runtime refused the call before it ran
        # (the contact suppression check or a policy). Nothing was sent.
        item = item.model_copy(update={"status": OutboxStatus.HELD, "updated_at": utc_now(),
                                       "note": "Approved, but the send was refused before it ran (the contact opt-out list or a policy). Nothing was sent."})
        store.save("outbox", item)
    elif approval.status == ApprovalStatus.REJECTED or lapsed:
        note = "The approval request lapsed before anyone decided." if lapsed else (
            f"Rejected by {approval.decided_by or 'an approver'}" + (f": {approval.decision_reason}" if approval.decision_reason else "."))
        item = item.model_copy(update={"status": OutboxStatus.REJECTED, "note": note[:500], "updated_at": utc_now()})
        store.save("outbox", item)
    return item, approval


def cancel_open(store: BusinessStore, repository: AnumRepository | None, context: TenantContext, lead_id: str, reason: str) -> int:
    """Stop-on-reply: cancel every open row of the lead and retire its waiting approvals."""
    items, _ = store.find("outbox", Query(where={"lead_id": lead_id, "status": set(OPEN_OUTBOX)}, limit=100))
    for item in items:
        if item.status == OutboxStatus.PENDING_APPROVAL and repository is not None:
            from .tools import retire_approval

            retire_approval(repository, context, item.approval_task_id, item.approval_id)
        store.save("outbox", item.model_copy(update={"status": OutboxStatus.CANCELLED, "note": reason[:500], "updated_at": utc_now()}))
    return len(items)


def cancel_item(context: TenantContext, repository: AnumRepository, item_id: str, reason: str) -> OutboxItem:
    with open_business_store(context) as store:
        item = store.get("outbox", item_id, for_update=True)
        if item is None:
            raise http_error(404, "Outbox message not found")
        if item.status not in OPEN_OUTBOX:
            raise http_error(409, f"This message is {item.status.value.replace('_', ' ')}; only waiting messages can be cancelled.")
        if item.status == OutboxStatus.PENDING_APPROVAL:
            from .tools import retire_approval

            retire_approval(repository, context, item.approval_task_id, item.approval_id)
        item = item.model_copy(update={"status": OutboxStatus.CANCELLED, "note": f"Cancelled: {reason}"[:500], "updated_at": utc_now()})
        store.save("outbox", item)
        record_activity(store, context, "outreach_cancelled", f"Outreach step {item.step} cancelled.", lead_id=item.lead_id, metadata={"outbox_id": item.id})
    audit(repository, context, "business.outreach.cancelled", item.id, metadata={"lead_id": item.lead_id})
    return item


# -- follow-up drafts -------------------------------------------------------------------------------


def follow_up_text(language: str, channel: str, first_name: str | None, company: str, offer: str | None) -> tuple[str, str]:
    if language == "ar":
        lines = [f"مرحبًا {first_name}،" if first_name else "مرحبًا،",
                 f"أتابع رسالتي السابقة بخصوص {company}" + (f" و{offer}." if offer else "."),
                 "هل يناسبكم اتصال قصير هذا الأسبوع؟", assist.OPT_OUT["ar"]]
        subject = f"متابعة: {company}" if channel == "email" else ""
    else:
        lines = [f"Hello {first_name}," if first_name else "Hello,",
                 f"I am following up on my earlier note about {company}" + (f" and {offer}." if offer else "."),
                 "Would a short call this week be useful?", assist.OPT_OUT["en"]]
        subject = f"Following up: {company}"[:MAX_SUBJECT] if channel == "email" else ""
    return subject, "\n\n".join(lines)


def follow_up_draft(context: TenantContext, repository: AnumRepository, item_id: str) -> Draft:
    now = utc_now()
    with open_business_store(context) as store:
        item = store.get("outbox", item_id)
        if item is None:
            raise http_error(404, "Outbox message not found")
        if item.status not in {OutboxStatus.HELD, OutboxStatus.SENT}:
            raise http_error(409, "Write a follow-up after the previous message was approved.")
        if store.first("replies", lead_id=item.lead_id) is not None:
            raise http_error(409, "The lead has replied, so follow-ups have stopped.")
        lead = store.get("leads", item.lead_id)
        original = store.get("drafts", item.draft_id)
        contact = store.get("contacts", item.contact_id)
        if lead is None or original is None:
            raise http_error(404, "The lead or draft is gone.")
        product = store.get("products", lead.product_id) if lead.product_id else None
        company = _company(store, lead)
        first = contact.name.split()[0] if contact and contact.name.strip() else None
        offer = (product.name_ar or product.name) if product and original.language == "ar" else (product.name if product else None)
        subject, body = follow_up_text(original.language, item.channel, first, company, offer)
        issues = assist.check_draft(original.channel, original.language, subject, body, _facts(store, lead))
        draft = Draft(
            id=new_id("bdra"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead.id, contact_id=item.contact_id,
            channel=DraftChannel(item.channel), language=original.language, subject=subject, body=body, generator="template",
            issues=issues, created_by=context.user_id, created_at=now, updated_at=now,
        )
        store.add("drafts", draft)
        record_activity(store, context, "draft_created", f"Follow-up draft for step {item.step + 1}.", lead_id=lead.id, metadata={"draft_id": draft.id, "follow_up_of": item.id})
    audit(repository, context, "business.outreach.follow_up_drafted", draft.id, metadata={"lead_id": draft.lead_id, "follow_up_of": item_id})
    return draft


# -- replies, opt-outs and bounces ----------------------------------------------------------------


def classify(text: str, bounce: bool) -> ReplyKind:
    """Hard rules in code: an opt-out is always an opt-out, whatever a model might say."""
    if bounce:
        return ReplyKind.BOUNCE
    folded = suppression.normalize_keyword(text or "")
    if suppression.keyword_of(text or "") == "stop" or any(suppression.normalize_keyword(phrase) in folded for phrase in OPT_OUT_PHRASES):
        return ReplyKind.OPT_OUT
    lowered = (text or "").casefold()
    if any(phrase in lowered for phrase in ESCALATION_PHRASES):
        return ReplyKind.ESCALATE
    return ReplyKind.REPLY


def _opt_out(store: BusinessStore, context: TenantContext, lead: Lead, contact: Contact | None, now: datetime) -> None:
    if contact is not None:
        store.save("contacts", contact.model_copy(update={"do_not_contact": True, "dnc_reason": "Asked to stop in a reply.", "updated_at": now}))
        with suppression.open_suppression_store(context) as entries:
            for value in (contact.email, contact.phone):
                normalized = suppression.normalize(value) if value else None
                if normalized is None:
                    continue
                kind, text = normalized
                entries.add(context, suppression.SuppressionEntry(
                    id=new_id("supp"), tenant_id=context.tenant_id, recipient_hash=suppression.recipient_hash(context.tenant_id, text),
                    masked=suppression.mask(kind, text), kind=kind, channel=suppression.SuppressionChannel.ALL,
                    purpose=suppression.SuppressionPurpose.ALL, source=suppression.SuppressionSource.MANUAL,
                    reason="Business: the contact asked to stop in a reply.", created_by=context.user_id, created_at=now,
                ))
    if lead.state != LeadState.SUPPRESSED:
        stamp_state(lead, LeadState.SUPPRESSED, now)
        lead.suppressed_reason = "The contact asked to stop in a reply."


def record_reply(context: TenantContext, repository: AnumRepository | None, lead_id: str, *, channel: str, text: str, bounce: bool,
                 source: str = "manual", outbox_id: str | None = None) -> ReplyEvent:
    """Record a reply, opt-out, escalation or bounce: stop the lead's outreach, honour opt-outs, watch bounces."""
    now = utc_now()
    kind = classify(text, bounce)
    with open_business_store(context) as store:
        lead = store.get("leads", lead_id, for_update=True)
        if lead is None:
            raise http_error(404, "Lead not found")
        contact = store.get("contacts", lead.contact_id) if lead.contact_id else None
        reason = {ReplyKind.REPLY: "The lead replied (stop-on-reply).", ReplyKind.OPT_OUT: "The contact asked to stop.",
                  ReplyKind.BOUNCE: "The message bounced.", ReplyKind.ESCALATE: "The reply needs a person (legal or complaint wording)."}[kind]
        cancelled = cancel_open(store, repository, context, lead.id, reason)
        if kind == ReplyKind.OPT_OUT:
            _opt_out(store, context, lead, contact, now)
        elif kind in {ReplyKind.REPLY, ReplyKind.ESCALATE} and lead.state == LeadState.CONTACTED:
            stamp_state(lead, LeadState.REPLIED, now)
        lead.updated_at = now
        store.save("leads", lead)
        address = recipient_of(contact, channel) if contact else None
        event = ReplyEvent(
            id=new_id("brep"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead.id, contact_id=contact.id if contact else None,
            outbox_id=outbox_id, channel=channel[:20], kind=kind, recipient_hash=recipient_hash(context, address), cancelled=cancelled,
            source=source, note=reason, created_by=context.user_id, created_at=now, updated_at=now,
        )
        store.add("replies", event)
        record_activity(store, context, f"outreach_{kind.value}", f"{reason} {cancelled} waiting message(s) cancelled.", lead_id=lead.id,
                        account_id=lead.account_id, metadata={"reply_id": event.id, "kind": kind.value, "cancelled": cancelled, "source": source})
        tripped = check_kill_switch(store, context) if kind == ReplyKind.BOUNCE else None
    audit(repository, context, "business.outreach.reply", event.id, metadata={"lead_id": lead_id, "kind": kind.value, "cancelled": cancelled,
                                                                             "source": source, "kill_switch": bool(tripped)})
    return event


def note_inbound(context: TenantContext, channel: str, address: str, text: str = "", *, bounce_hint: bool = False, source: str = "inbound") -> int:
    """An inbound message from the e-mail inbox or WhatsApp: a reply from a contact ANUM reached out to
    stops that lead's outreach (and honours an opt-out). Returns how many leads it matched."""
    normalized = suppression.normalize(address or "")
    if normalized is None:
        return 0
    kind, value = normalized
    matched = 0
    with open_business_store(context) as store:
        contacts, _ = store.find("contacts", Query(where={"email": value} if kind == "email" else {"phone": value}, limit=20))
        leads: list[str] = []
        for contact in contacts:
            for lead in store.find("leads", Query(where={"contact_id": contact.id}, limit=20))[0]:
                if store.first("outbox", lead_id=lead.id) is not None:
                    leads.append(lead.id)
    for lead_id in leads:
        record_reply(context, None, lead_id, channel=channel, text=text, bounce=bounce_hint, source=source)
        matched += 1
    return matched


BOUNCE_SENDERS = re.compile(r"^(mailer-daemon|postmaster|mail-daemon|bounce[s]?)@", re.IGNORECASE)


def inbound_candidates(context: TenantContext, addresses: list[str]) -> set[str]:
    """The senders (lowercase) worth reading: bounce senders, and contacts of leads that have outbox rows.
    The inbox reads bodies for these only."""
    found: set[str] = set()
    with open_business_store(context) as store:
        for address in {value.strip().lower() for value in addresses if value}:
            if BOUNCE_SENDERS.match(address):
                found.add(address)
                continue
            normalized = suppression.normalize(address)
            if normalized is None or normalized[0] != "email":
                continue
            for contact in store.find("contacts", Query(where={"email": normalized[1]}, limit=20))[0]:
                if any(store.first("outbox", lead_id=lead.id) is not None for lead in store.find("leads", Query(where={"contact_id": contact.id}, limit=20))[0]):
                    found.add(address)
                    break
    return found


def note_inbound_emails(context: TenantContext, messages: list[Any]) -> int:
    """Hook for the e-mail inbox sync: replies from contacts, and bounces naming a sent recipient."""
    matched = 0
    for message in messages:
        sender = str(getattr(message, "from_address", "") or "")
        # Subject, preview and (when the inbox read it) the body: classified in memory, never stored.
        text = f"{getattr(message, 'subject', '') or ''}\n{getattr(message, 'snippet', '') or ''}\n{getattr(message, 'body', '') or ''}"
        if BOUNCE_SENDERS.match(sender):
            for address in set(re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)):
                with open_business_store(context) as store:
                    sent = store.first("outbox", recipient=address.lower(), status=OutboxStatus.SENT)
                if sent is not None:
                    record_reply(context, None, sent.lead_id, channel="email", text="", bounce=True, source="email_inbox", outbox_id=sent.id)
                    matched += 1
            continue
        matched += note_inbound(context, "email", sender, text, source="email_inbox")
    return matched


# -- the send tool ----------------------------------------------------------------------------------


def _hold(store: BusinessStore, item: OutboxItem, status: OutboxStatus, note: str, issues: list[str] | None = None) -> ToolResult:
    store.save("outbox", item.model_copy(update={"status": status, "note": note[:500], "gate_issues": issues or [], "updated_at": utc_now()}))
    return ToolResult(status="failed", summary=f"Not sent: {note}"[:500], output={"outbox_id": item.id, "status": status.value},
                      audit_metadata={"outbox_id": item.id, "outcome": status.value})


def _send_checks(context: TenantContext, call: ToolCall) -> tuple[ToolResult | None, OutboxItem | None, str | None]:
    """Everything checked before anything leaves: (refusal, row, channel tool name)."""
    from .. import delivery

    args = call.arguments
    now = utc_now()
    # One short transaction claims the row: the workspace's settings row is locked first (it serializes
    # every claim and quota reservation of the workspace), then the outbox row. The claim moves the row
    # to ``sending`` and reserves the quota slot; the provider is called after commit, with no
    # transaction open, and a second run finds ``sending`` and sends nothing.
    with _claim_lock, open_business_store(context) as store:
        settings = load_settings(store, context)
        if store.get("outreach_settings", settings.id) is None:
            save_settings(store, settings)
        settings = store.get("outreach_settings", settings.id, for_update=True) or settings
        item = store.get("outbox", str(args.get("outbox_id", "")), for_update=True)
        if item is None:
            return ToolResult(status="failed", summary="The outbox message was deleted; nothing was sent."), None, None
        if item.status == OutboxStatus.SENT:
            same = item.content_hash == args.get("content_hash")
            return ToolResult(status="succeeded" if same else "failed", summary="Already sent." if same else "Another text was already sent; nothing changed."), None, None
        if item.status == OutboxStatus.SENDING:
            return ToolResult(status="failed", summary="This message is already being sent; it is not sent again."), None, None
        if item.status not in {OutboxStatus.PENDING_APPROVAL, OutboxStatus.HELD}:
            return ToolResult(status="failed", summary=f"The message is {item.status.value.replace('_', ' ')}; nothing was sent."), None, None
        bound = (args.get("content_hash") == item.content_hash and args.get("to") == item.recipient and args.get("text") == item.body
                 and args.get("subject") == item.subject and args.get("channel") == item.channel)
        if not bound:
            return _hold(store, item, OutboxStatus.CANCELLED, "The message changed after it was approved."), None, None
        if not settings.sending_enabled:
            return _hold(store, item, OutboxStatus.HELD, "Approved, but sending is turned off (Business, Outreach settings). Nothing was sent."), None, None
        if settings.paused:
            return _hold(store, item, OutboxStatus.HELD, f"Approved, but outreach is paused: {settings.paused_reason or 'an owner paused it'}."), None, None
        quota = int(settings.daily_quota.get(item.channel, 0))
        if sent_today(store, item.channel, now) >= quota:
            return _hold(store, item, OutboxStatus.HELD, f"Approved, but today's {item.channel} quota ({quota}) is used up."), None, None
        if item.send_after is not None and item.send_after > now:
            return _hold(store, item, OutboxStatus.HELD, f"Approved, but not due until {item.send_after.date().isoformat()}."), None, None
        lead = store.get("leads", item.lead_id)
        draft = store.get("drafts", item.draft_id)
        contact = store.get("contacts", item.contact_id)
        if lead is None or draft is None:
            return _hold(store, item, OutboxStatus.CANCELLED, "The lead or draft is gone."), None, None
        if lead.state in STOP_STATES or store.first("replies", lead_id=lead.id) is not None:
            return _hold(store, item, OutboxStatus.CANCELLED, "The lead replied or was closed (stop-on-reply)."), None, None
        if draft.approved_hash != item.content_hash:
            return _hold(store, item, OutboxStatus.CANCELLED, "The draft changed after it was approved."), None, None
        issues = gate(context, store, draft, lead, contact, item.channel, item.recipient)
        if issues:
            return _hold(store, item, OutboxStatus.FAILED, "The send-time check failed: " + " ".join(issues), issues), None, None
        tool = delivery.channels().get(item.channel)
        if tool is None:
            return _hold(store, item, OutboxStatus.HELD, f"Approved, but no {item.channel} integration is connected. Connect one in Integrations."), None, None
        item.status = OutboxStatus.SENDING  # the claim, and the reserved quota slot
        item.note = "Sending."
        item.updated_at = now
        store.save("outbox", item)
    return None, item, tool


def reconcile_sending(store: BusinessStore, item: OutboxItem, now: datetime | None = None) -> OutboxItem:
    """A claim whose send never finished (an API restart) is never resent: with a provider message id it
    was sent; without one a person checks the sent mail and decides (``needs_review``)."""
    now = now or utc_now()
    if item.status != OutboxStatus.SENDING or now - item.updated_at < STALE_SENDING:
        return item
    if item.provider_ref:
        item = item.model_copy(update={"status": OutboxStatus.SENT, "sent_at": item.sent_at or item.updated_at,
                                       "note": "Sent (confirmed by the provider id).", "updated_at": now})
    else:
        item = item.model_copy(update={"status": OutboxStatus.NEEDS_REVIEW, "updated_at": now,
                                       "note": "The send was interrupted and ANUM cannot tell whether it left. Check the sent mail before sending it again."})
    store.save("outbox", item)
    return item


async def send_outreach(call: ToolCall, context: TenantContext) -> ToolResult:
    """The approved send. Re-checks everything, then hands the message to the channel's tool."""
    from .. import delivery
    from ..dependencies import _unit_of_work
    from ..model_gateway import MockModelGateway
    from ..runtime import AgentRuntime

    refusal, item, tool = await asyncio.to_thread(_send_checks, context, call)
    if refusal is not None or item is None or tool is None:
        return refusal or ToolResult(status="failed", summary="Nothing was sent.")
    registry = delivery.registry()
    # The outbox id is a stable idempotency key for providers that accept one.
    inner = ToolCall(name=tool, arguments={"to": item.recipient, "subject": item.subject, "text": item.body, "attachments": [], "purpose": "marketing",
                                           "idempotency_key": item.id})

    def policy() -> str | None:
        with _unit_of_work(context, require_membership=False) as repository:
            decision = AgentRuntime(MockModelGateway(), repository, tools=registry).evaluate(inner, context)
        # The person approved this exact message above; a governance deny still wins.
        return decision.reason if decision.outcome == ToolPolicyOutcome.BLOCK else None

    blocked = await asyncio.to_thread(policy)
    result = ToolResult(status="blocked", summary=f"Blocked by policy: {blocked}") if blocked else await registry.execute(inner, context)  # type: ignore[union-attr]

    def finish() -> ToolResult:
        now = utc_now()
        with open_business_store(context) as store:
            current = store.get("outbox", item.id, for_update=True)
            if current is None:  # pragma: no cover - deleted while sending
                return ToolResult(status="failed", summary="The outbox message was deleted while sending.")
            if result.status != "succeeded":
                # Failed: the row leaves ``sending``, which releases its quota slot.
                store.save("outbox", current.model_copy(update={"status": OutboxStatus.FAILED, "note": f"The send failed: {result.summary}"[:500], "updated_at": now}))
                record_activity(store, context, "outreach_failed", f"Outreach step {current.step} failed to send ({current.channel}).", lead_id=current.lead_id,
                                metadata={"outbox_id": current.id}, idempotency_key=f"send-failed:{current.id}")
                return ToolResult(status="failed", summary=f"Not sent: {result.summary}"[:500], audit_metadata={"outbox_id": current.id, "outcome": "failed"})
            ref = str((result.output or {}).get("message_id") or (result.output or {}).get("id") or "")[:200]
            store.save("outbox", current.model_copy(update={"status": OutboxStatus.SENT, "sent_at": now, "provider_ref": ref, "note": "Sent.", "updated_at": now}))
            lead = store.get("leads", current.lead_id, for_update=True)
            if lead is not None and lead.state == LeadState.QUALIFIED:
                stamp_state(lead, LeadState.CONTACTED, now)
                lead.updated_at = now
                store.save("leads", lead)
            record_activity(store, context, "outreach_sent", f"Outreach step {current.step} sent ({current.channel}).", lead_id=current.lead_id,
                            metadata={"outbox_id": current.id, "step": current.step}, idempotency_key=f"send:{current.id}")
        return ToolResult(status="succeeded", summary=f"Sent step {item.step} by {item.channel}.", output={"outbox_id": item.id},
                          audit_metadata={"outbox_id": item.id, "outcome": "sent", "channel": item.channel})

    return await asyncio.to_thread(finish)


# -- settings view helpers --------------------------------------------------------------------------


def pause(context: TenantContext, repository: AnumRepository, on: bool, reason: str) -> Any:
    with open_business_store(context) as store:
        settings = load_settings(store, context)
        settings.paused = on
        settings.paused_reason = reason.strip()[:300] if on else ""
        settings.paused_at = utc_now() if on else None
        settings.updated_by = context.user_id
        save_settings(store, settings)
    audit(repository, context, "business.outreach.pause", settings.id, metadata={"on": on})
    return settings
