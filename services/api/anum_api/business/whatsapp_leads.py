"""One lead list: WhatsApp Sales CRM leads as Business leads (docs/business.md#whatsapp-leads).

The WhatsApp Sales CRM (``sales_leads``, migration 0102) keeps working as the store its quotes,
follow-ups and consent records point at. Business is the one lead list: a WhatsApp lead is
*mapped* into a Business lead with ``channel = whatsapp``, the conversation it came from, its
normalised phone number and the ``sales_lead_id`` of the row it came from (migration 0125). The
WhatsApp ingestion now writes the Business lead itself (``join_from_ingestion``, the contract
step); readers still read both, so rows from before it (and enquiries that wait for the owner's
answer about a possible same person) show as WhatsApp leads that are not mapped yet.

Mapping never merges two people on its own. A WhatsApp lead whose normalised phone number is
already on a Business lead (its contact's phone or another mapped WhatsApp lead) is shown to the
owner as a suggestion; the owner links it to that lead or keeps it separate. The answer is kept
(``business_lead_pair_decisions``) so the pair is not asked again. Lead scoring and the state
machine stay Business's: a mapped lead starts as ``discovered`` (or ``lost`` when the CRM had
closed it) and moves only through Business's own transitions.

Everything runs in the caller's tenant and workspace: the WhatsApp rows are read in their own
short unit of work under the tables' row policies, the Business rows in another.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Any, Protocol

from ..schemas import TenantContext, new_id
from ..scoped_store import open_scoped_store
from ..sales_lead_lifecycle import Lead as SalesLead
from ..sales_lead_lifecycle import LeadStatus
from .models import OPEN_LEAD_STATES, Account, Lead, LeadPairDecision, LeadState, RecordSource
from .service import can_move, http_error, name_key, normal_email, normal_phone, record_activity, stamp_state
from .store import BusinessStore, Query

MAX_SOURCE = 500
MAX_SUGGESTIONS = 5
# Leads read at most to look for duplicates (open ones only).
MAX_OPEN_SCAN = 2000


class WhatsAppLeadSource(Protocol):
    def list_for_mapping(self, *, limit: int = MAX_SOURCE) -> list[SalesLead]: ...
    def get(self, lead_id: str) -> SalesLead | None: ...


class InMemoryWhatsAppLeads:
    """The WhatsApp Sales CRM exists only with PostgreSQL; locally and in tests this holds the
    rows a test puts in (empty otherwise), scoped to the tenant and workspace like the table."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], dict[str, SalesLead]] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()

    def put(self, lead: SalesLead) -> None:
        with self._lock:
            self._rows.setdefault((lead.tenant_id, lead.workspace_id), {})[lead.id] = lead

    def scoped(self, context: TenantContext) -> "_ScopedMemory":
        return _ScopedMemory(self, context)


class _ScopedMemory:
    def __init__(self, root: InMemoryWhatsAppLeads, context: TenantContext) -> None:
        self.root, self.context = root, context

    def _mine(self) -> dict[str, SalesLead]:
        with self.root._lock:
            return dict(self.root._rows.get((self.context.tenant_id, self.context.workspace_id), {}))

    def list_for_mapping(self, *, limit: int = MAX_SOURCE) -> list[SalesLead]:
        return sorted(self._mine().values(), key=lambda item: (item.created_at, item.id))[:limit]

    def get(self, lead_id: str) -> SalesLead | None:
        return self._mine().get(lead_id)


memory_whatsapp_leads = InMemoryWhatsAppLeads()


@contextmanager
def open_whatsapp_leads(context: TenantContext) -> Iterator[WhatsAppLeadSource]:
    """One read-only unit of work over the caller's WhatsApp Sales CRM leads."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.sales_repository import SalesCRMStore

        return SalesCRMStore(session, context.tenant_id, context.workspace_id)

    with open_scoped_store(context, memory_whatsapp_leads.scoped(context), sql_store) as store:
        yield store


# -- pairs and suggestions ------------------------------------------------------------------------


def lead_ref(lead_id: str) -> str:
    return f"lead:{lead_id}"


def whatsapp_ref(sales_lead_id: str) -> str:
    return f"whatsapp:{sales_lead_id}"


def pair_key(first: str, second: str) -> str:
    if first == second:
        raise http_error(422, "Choose two different leads.")
    return "|".join(sorted((first, second)))


@dataclass(frozen=True)
class Suggestion:
    lead_id: str
    title: str
    reason: str


def _decisions(store: BusinessStore) -> dict[str, str]:
    items, _ = store.find("lead_pair_decisions", Query(limit=MAX_OPEN_SCAN, order_by="created_at"))
    return {item.pair_key: item.decision for item in items}


def _open_leads(store: BusinessStore) -> list[Lead]:
    items, _ = store.find("leads", Query(where={"state": OPEN_LEAD_STATES}, order_by="created_at", descending=False, limit=MAX_OPEN_SCAN))
    return items


def _contact_keys(store: BusinessStore, leads: list[Lead]) -> dict[str, tuple[str | None, str | None]]:
    """Per lead: (normalised phone, normalised e-mail) from its contact or the channel it came from."""
    contacts: dict[str, Any] = {}
    keys: dict[str, tuple[str | None, str | None]] = {}
    for lead in leads:
        phone = lead.channel_phone
        email = None
        if lead.contact_id:
            if lead.contact_id not in contacts:
                contacts[lead.contact_id] = store.get("contacts", lead.contact_id)
            contact = contacts[lead.contact_id]
            if contact is not None:
                phone = phone or normal_phone(contact.phone)
                email = normal_email(contact.email)
        keys[lead.id] = (phone, email)
    return keys


def mapped_ids(store: BusinessStore, decisions: dict[str, str]) -> set[str]:
    """WhatsApp leads that are already in the list: linked by ``sales_lead_id`` or merged into a lead."""
    linked, _ = store.find("leads", Query(where={"channel": "whatsapp"}, limit=MAX_OPEN_SCAN * 5, order_by="created_at"))
    found = {lead.sales_lead_id for lead in linked if lead.sales_lead_id}
    for key, decision in decisions.items():
        if decision == "merged":
            for side in key.split("|"):
                if side.startswith("whatsapp:"):
                    found.add(side.removeprefix("whatsapp:"))
    return found


def suggestions_for(wa: SalesLead, leads: list[Lead], keys: dict[str, tuple[str | None, str | None]], decisions: dict[str, str]) -> list[Suggestion]:
    phone = normal_phone(wa.phone_e164)
    if not phone:
        return []
    found: list[Suggestion] = []
    for lead in leads:
        if pair_key(whatsapp_ref(wa.id), lead_ref(lead.id)) in decisions:
            continue
        if keys.get(lead.id, (None, None))[0] == phone:
            reason = "Same WhatsApp number." if lead.channel_phone == phone else "Same phone number as this lead's contact."
            found.append(Suggestion(lead_id=lead.id, title=lead.title, reason=reason))
        if len(found) >= MAX_SUGGESTIONS:
            break
    return found


@dataclass(frozen=True)
class LeadPair:
    first: Lead
    second: Lead
    reason: str


def duplicate_pairs(store: BusinessStore, limit: int = 50) -> list[LeadPair]:
    """Open Business leads that share a normalised phone number or e-mail address, minus the pairs
    the owner already answered. Different people are never merged here: these are suggestions."""
    leads = _open_leads(store)
    keys = _contact_keys(store, leads)
    decisions = _decisions(store)
    groups: dict[tuple[str, str], list[Lead]] = {}
    for lead in leads:
        phone, email = keys[lead.id]
        if phone:
            groups.setdefault(("phone", phone), []).append(lead)
        if email:
            groups.setdefault(("email", email), []).append(lead)
    pairs: list[LeadPair] = []
    seen: set[str] = set()
    for (kind, _value), members in groups.items():
        for index, first in enumerate(members):
            for second in members[index + 1 :]:
                key = pair_key(lead_ref(first.id), lead_ref(second.id))
                if key in decisions or key in seen:
                    continue
                seen.add(key)
                pairs.append(LeadPair(first, second, "Same phone number." if kind == "phone" else "Same e-mail address."))
                if len(pairs) >= limit:
                    return pairs
    return pairs


def record_decision(store: BusinessStore, context: TenantContext, key: str, decision: str, now: datetime) -> None:
    existing = store.first("lead_pair_decisions", pair_key=key)
    if existing is not None:
        existing.decision = decision
        existing.decided_by = context.user_id
        existing.updated_at = now
        store.save("lead_pair_decisions", existing)
        return
    store.add("lead_pair_decisions", LeadPairDecision(
        id=new_id("bpair"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, pair_key=key,
        decision=decision, decided_by=context.user_id, created_at=now, updated_at=now,  # type: ignore[arg-type]
    ))


# -- mapping --------------------------------------------------------------------------------------

CLOSED = {LeadStatus.LOST, LeadStatus.DISQUALIFIED}
STAGE_TEXT = {
    LeadStatus.NEW: "new", LeadStatus.QUALIFYING: "qualifying", LeadStatus.QUALIFIED: "qualified",
    LeadStatus.QUOTED: "quoted", LeadStatus.NEGOTIATING: "negotiating", LeadStatus.WON: "won",
    LeadStatus.LOST: "lost", LeadStatus.DISQUALIFIED: "disqualified",
}


def _label(wa: SalesLead) -> str:
    phone = normal_phone(wa.phone_e164)
    return " ".join((wa.company_name or wa.contact_name or (f"WhatsApp {phone}" if phone else "WhatsApp enquiry")).split())[:200]


def _title(wa: SalesLead) -> str:
    text = " ".join((wa.requirements or "").split())
    if len(text) >= 2:
        return text[:197] + "..." if len(text) > 200 else text
    who = wa.company_name or wa.contact_name
    return f"WhatsApp enquiry from {who}"[:200] if who else "WhatsApp enquiry"


def map_whatsapp_lead(
    store: BusinessStore, context: TenantContext, wa: SalesLead, now: datetime, *, link_to: str | None = None, owner_id: str | None = None,
) -> Lead:
    """Bring one WhatsApp lead into the Business list: as a new lead, or linked to ``link_to``
    (the owner confirmed it is the same person). Refuses a lead that is already mapped.

    ``owner_id`` is who owns a new lead when the CRM row names nobody (the WhatsApp ingestion
    passes the person who connected WhatsApp); without it the caller owns it."""
    decisions = _decisions(store)
    if wa.id in mapped_ids(store, decisions):
        raise http_error(409, "This WhatsApp lead is already in the lead list.")
    phone = normal_phone(wa.phone_e164)
    if link_to is not None:
        lead = store.get("leads", link_to, for_update=True)
        if lead is None:
            raise http_error(404, "Lead not found.")
        if lead.sales_lead_id is None:
            lead.channel = "whatsapp"
            lead.channel_conversation_id = wa.source_conversation_id
            lead.channel_phone = lead.channel_phone or phone
            lead.sales_lead_id = wa.id
            lead.updated_at = now
            store.save("leads", lead)
        record_decision(store, context, pair_key(whatsapp_ref(wa.id), lead_ref(lead.id)), "merged", now)
        record_activity(store, context, "whatsapp_linked", "WhatsApp enquiry linked to this lead (the owner confirmed it is the same person).",
                        lead_id=lead.id, account_id=lead.account_id, metadata={"sales_lead_id": wa.id, "conversation_id": wa.source_conversation_id})
        return lead

    label = _label(wa)
    owner = wa.assigned_to or owner_id or context.user_id
    account = Account(
        id=new_id("bacct"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, name=label, name_key=name_key(label),
        source=RecordSource.INBOUND, owner_id=owner, created_by=context.user_id, created_at=now, updated_at=now,
        notes="Added from a WhatsApp enquiry.",
    )
    store.add("accounts", account)
    stage = STAGE_TEXT.get(wa.status, str(wa.status))
    lead = Lead(
        id=new_id("blead"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, account_id=account.id,
        title=_title(wa), owner_id=owner, source=RecordSource.INBOUND,
        next_step=f"WhatsApp Sales CRM stage: {stage}.", created_by=context.user_id, created_at=wa.created_at, updated_at=now,
        channel="whatsapp", channel_conversation_id=wa.source_conversation_id, channel_phone=phone, sales_lead_id=wa.id,
    )
    if wa.status in CLOSED and can_move(lead.state, LeadState.LOST):
        stamp_state(lead, LeadState.LOST, now)
        lead.lost_reason = f"Closed in the WhatsApp Sales CRM as {stage}."
    try:
        store.add("leads", lead)
    except Exception as exc:  # the partial unique index on sales_lead_id: mapped by someone else meanwhile
        if "uq_business_leads_sales_lead" in str(exc):
            raise http_error(409, "This WhatsApp lead is already in the lead list.") from exc
        raise
    record_activity(store, context, "lead_created", f"Lead \"{lead.title}\" added from a WhatsApp enquiry (CRM stage {stage}).",
                    lead_id=lead.id, account_id=account.id, metadata={"sales_lead_id": wa.id, "conversation_id": wa.source_conversation_id})
    return lead


def join_from_ingestion(store: BusinessStore, context: TenantContext, wa: SalesLead, now: datetime, *, owner_id: str | None = None) -> str:
    """The contract step of the one lead list: the WhatsApp ingestion writes the Business lead itself.

    Called right after the WhatsApp Sales CRM recorded (or found) the enquiry's ``sales_leads`` row,
    in the conversation's tenant and workspace. Answers what happened:

    * ``created``: no open Business lead has the number, so the enquiry is a new lead
      (``channel = whatsapp``, the conversation, the phone and ``sales_lead_id``), exactly as
      **Add as a lead** would make it;
    * ``updated``: it was already in the list and its WhatsApp link was filled in;
    * ``unchanged``: it was already in the list (mapped, or merged into a lead by the owner);
    * ``waiting``: the number is already on an open lead the owner has not answered about. It is
      never merged on its own: it waits under **WhatsApp leads** for **Same person: link** or
      **Different people** (the existing duplicate rules).
    """
    decisions = _decisions(store)
    if wa.id in mapped_ids(store, decisions):
        linked = store.first("leads", sales_lead_id=wa.id)
        if linked is not None and not linked.channel_conversation_id and wa.source_conversation_id:
            linked.channel = "whatsapp"
            linked.channel_conversation_id = wa.source_conversation_id
            linked.channel_phone = linked.channel_phone or normal_phone(wa.phone_e164)
            linked.updated_at = now
            store.save("leads", linked)
            return "updated"
        return "unchanged"
    leads = _open_leads(store)
    if suggestions_for(wa, leads, _contact_keys(store, leads), decisions):
        return "waiting"
    map_whatsapp_lead(store, context, wa, now, owner_id=owner_id)
    return "created"


def keep_separate(store: BusinessStore, context: TenantContext, first: str, second: str, now: datetime) -> None:
    record_decision(store, context, pair_key(first, second), "separate", now)


def merge_leads(store: BusinessStore, context: TenantContext, keep_id: str, merge_id: str, now: datetime) -> tuple[Lead, Lead]:
    """The owner confirmed two Business leads are the same person: the merged one is closed as lost
    (Business's own transition, with the reason) and its WhatsApp link moves to the kept lead when
    that one has none. Nothing is deleted."""
    if keep_id == merge_id:
        raise http_error(422, "Choose two different leads.")
    keep = store.get("leads", keep_id, for_update=True)
    merged = store.get("leads", merge_id, for_update=True)
    if keep is None or merged is None:
        raise http_error(404, "Lead not found.")
    if not can_move(merged.state, LeadState.LOST):
        raise http_error(409, f"A {merged.state.value} lead cannot be closed as a duplicate. Close it by hand first.")
    if merged.sales_lead_id and keep.sales_lead_id is None:
        link = (merged.channel, merged.channel_conversation_id, merged.channel_phone, merged.sales_lead_id)
        merged.channel = merged.channel_conversation_id = merged.sales_lead_id = None
        merged.updated_at = now
        store.save("leads", merged)
        keep.channel, keep.channel_conversation_id, channel_phone, keep.sales_lead_id = link  # type: ignore[assignment]
        keep.channel_phone = keep.channel_phone or channel_phone
        keep.updated_at = now
        store.save("leads", keep)
    before = merged.state
    stamp_state(merged, LeadState.LOST, now)
    merged.lost_reason = f"Duplicate of \"{keep.title}\""[:300]
    merged.updated_at = now
    store.save("leads", merged)
    record_activity(store, context, "state_changed", f"{before.value.capitalize()} to lost: duplicate of \"{keep.title}\".",
                    lead_id=merged.id, account_id=merged.account_id, metadata={"from": before.value, "to": "lost", "kept": keep.id})
    record_activity(store, context, "lead_merged", f"Lead \"{merged.title}\" merged into this one (the owner confirmed it is the same person).",
                    lead_id=keep.id, account_id=keep.account_id, metadata={"merged": merged.id})
    record_decision(store, context, pair_key(lead_ref(keep.id), lead_ref(merged.id)), "merged", now)
    return keep, merged


# -- the WhatsApp figures -------------------------------------------------------------------------

# Reached qualified or later and still in play or won (the CRM's "qualified" figure counted the same).
QUALIFIED_OR_LATER = frozenset({
    LeadState.QUALIFIED, LeadState.CONTACTED, LeadState.REPLIED, LeadState.MEETING, LeadState.OFFERED, LeadState.WON,
})


def whatsapp_overview(context: TenantContext) -> dict[str, Any]:
    """The Sales overview figures, read from the one lead list: Business leads with the WhatsApp
    source (``channel = whatsapp``), counted by Business's own states. ``waiting`` counts the
    WhatsApp Sales CRM enquiries that are not in the list yet (older rows, and enquiries waiting
    for the owner's answer about a possible same person). ``by_intent`` is the service each lead's
    enquiry asked about, read from the CRM row it came from. Counts only, no personal data."""
    from .store import open_business_store

    whatsapp = {"channel": "whatsapp"}
    with open_business_store(context) as store:
        def count(**where: Any) -> int:
            return store.count("leads", Query(where={**whatsapp, **where}, limit=1))

        figures = {
            "leads": count(),
            "qualified": count(state=QUALIFIED_OR_LATER),
            "quoted": count(state=LeadState.OFFERED),
            "won": count(state=LeadState.WON),
            "lost": count(state=LeadState.LOST),
        }
        done = mapped_ids(store, _decisions(store))
        linked, _ = store.find("leads", Query(where=whatsapp, limit=MAX_OPEN_SCAN * 5, order_by="created_at"))
        in_list = {lead.sales_lead_id for lead in linked if lead.sales_lead_id}
    with open_whatsapp_leads(context) as source:
        rows = source.list_for_mapping(limit=MAX_SOURCE)
    by_intent: dict[str, int] = {}
    for wa in rows:
        if wa.id in in_list:
            key = getattr(wa.intent, "value", str(wa.intent))
            by_intent[key] = by_intent.get(key, 0) + 1
    return {
        **figures,
        "waiting": sum(1 for wa in rows if wa.id not in done),
        "by_intent": by_intent,
        "source": "business_leads",
        "scope": "all_whatsapp_leads",
        "revenue_estimate": None,
    }
