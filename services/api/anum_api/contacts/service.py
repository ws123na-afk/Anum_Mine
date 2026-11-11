"""Rules of the Contacts directory (docs/contacts.md): matching, the directory build, merging,
do-not-contact and the consent summary read from the sources.

Matching is on normalised identifiers only (E.164 phone, lower-case e-mail). A source record
whose phone or e-mail is already a person's, under the same (or no) name, joins that person. The
same identifier under a different name makes a new person and an open merge proposal: nothing
merges on a name alone, and nothing merges without a person's decision.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status

from ..audit import AuditRecord
from ..schemas import TenantContext, new_id, utc_now
from ..suppression import normalize as normalize_recipient
from .models import (
    CONTACT_CHANNELS,
    SOURCE_OF_KIND,
    BuildReport,
    Channel,
    ChannelSummary,
    ConsentFact,
    Counts,
    DoNotContact,
    DoNotContactView,
    Identifier,
    IdentifierKind,
    IdentifierView,
    Link,
    LinkKind,
    LinkView,
    MailContactView,
    MergeProposal,
    Person,
    PersonDetail,
    PersonInput,
    PersonPatch,
    PersonSource,
    PersonView,
    ProposalStatus,
    ProposalView,
    SourcePerson,
)
from .store import ContactsStore, open_contacts_store

DEFAULT_BUILD_LIMIT = 500


def http_error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


# -- identifiers ---------------------------------------------------------------------------


def normalise_phone(raw: str) -> str | None:
    """``+`` and digits (E.164 form), or None when it is not a phone number."""
    found = normalize_recipient(raw)
    return found[1] if found is not None and found[0] == "phone" else None


def normalise_email(raw: str) -> str | None:
    found = normalize_recipient(raw)
    return found[1] if found is not None and found[0] == "email" else None


def normalise_value(kind: IdentifierKind, raw: str) -> str | None:
    return normalise_phone(raw) if kind == IdentifierKind.PHONE else normalise_email(raw)


def identifiers_from(phones: Iterable[str], emails: Iterable[str], *, strict: bool) -> list[tuple[IdentifierKind, str, str]]:
    """(kind, normalised value, label as typed), unique by kind and value.

    ``strict`` raises 422 on a value that is not a phone number or an address (a person typed
    it); a source record's unreadable value is skipped instead."""
    out: list[tuple[IdentifierKind, str, str]] = []
    seen: set[tuple[IdentifierKind, str]] = set()
    for kind, values in ((IdentifierKind.PHONE, phones), (IdentifierKind.EMAIL, emails)):
        for raw in values:
            label = " ".join(str(raw).split())
            if not label:
                continue
            value = normalise_value(kind, label)
            if value is None:
                if strict:
                    what = "phone number with its country code (+971...)" if kind == IdentifierKind.PHONE else "e-mail address"
                    raise http_error(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{label}' is not a {what}.")
                continue
            if (kind, value) in seen:
                continue
            seen.add((kind, value))
            out.append((kind, value, label))
    return out


def fold_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def same_name(a: str, b: str) -> bool:
    """Equal after folding, or one side has no name to contradict the other."""
    fa, fb = fold_name(a), fold_name(b)
    return not fa or not fb or fa == fb


def display_name(row: SourcePerson) -> str:
    if row.name.strip():
        return " ".join(row.name.split())[:200]
    if row.phones:
        value = normalise_phone(row.phones[0])
        if value:
            return value
    if row.emails:
        return row.emails[0].strip().lower()[:200]
    return "Unknown contact"


# -- matching and the build ----------------------------------------------------------------


def _candidates(store: ContactsStore, identifiers: list[tuple[IdentifierKind, str, str]]) -> dict[str, list[tuple[IdentifierKind, str]]]:
    """People already holding one of the identifiers: person id -> the shared (kind, value)s."""
    found: dict[str, list[tuple[IdentifierKind, str]]] = {}
    for kind, value, _label in identifiers:
        for existing in store.find_by_identifier(kind, value):
            found.setdefault(existing.contact_id, []).append((kind, value))
    return found


def _propose(store: ContactsStore, context: TenantContext, a: str, b: str, kind: IdentifierKind, value: str, now: datetime) -> bool:
    if a == b:
        return False
    first, second = sorted((a, b))
    _proposal, created = store.add_proposal(MergeProposal(
        id=new_id("ctprop"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, contact_a_id=first, contact_b_id=second,
        reason="same_phone" if kind == IdentifierKind.PHONE else "same_email", matched_value=value, created_at=now,
    ))
    return created


def _add_identifiers(store: ContactsStore, context: TenantContext, person: Person, identifiers: list[tuple[IdentifierKind, str, str]], now: datetime) -> int:
    """Add the identifiers to ``person`` and open a proposal for every other person already holding one."""
    proposals = 0
    for kind, value, label in identifiers:
        others = {i.contact_id for i in store.find_by_identifier(kind, value)} - {person.id}
        store.add_identifier(Identifier(
            id=new_id("ctid"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, contact_id=person.id, kind=kind, value=value,
            label=label, created_at=now,
        ))
        for other in others:
            if _propose(store, context, person.id, other, kind, value, now):
                proposals += 1
    return proposals


def attach_source(store: ContactsStore, context: TenantContext, row: SourcePerson, now: datetime, actor: str) -> tuple[Link, bool, int]:
    """Link one source record to the person it belongs to, creating the person when needed.

    Returns the link, whether a person was created and how many merge proposals were opened."""
    identifiers = identifiers_from(row.phones, row.emails, strict=False)
    candidates = _candidates(store, identifiers)
    # The person whose name agrees (or who has no name to disagree), the most shared identifiers first.
    person: Person | None = None
    for contact_id, _shared in sorted(candidates.items(), key=lambda item: (-len(item[1]), item[0])):
        existing = store.get_person(contact_id)
        if existing is not None and same_name(existing.name, row.name):
            person = existing
            break
    created = False
    if person is None:
        person = Person(
            id=new_id("contact"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, name=display_name(row),
            organisation=row.organisation[:200], source=SOURCE_OF_KIND[row.kind], created_by=actor, created_at=now, updated_at=now,
        )
        store.add_person(person)
        created = True
    else:
        changes: dict[str, Any] = {}
        if not person.name.strip() and row.name.strip():
            changes["name"] = display_name(row)
        if not person.organisation and row.organisation:
            changes["organisation"] = row.organisation[:200]
        if changes:
            person = store.save_person(person.model_copy(update={**changes, "updated_at": now}))
    proposals = _add_identifiers(store, context, person, identifiers, now)
    link = store.add_link(Link(
        id=new_id("ctlink"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, contact_id=person.id, kind=row.kind,
        source_id=row.source_id, created_at=now,
    ))
    return link, created, proposals


def build_directory(store: ContactsStore, context: TenantContext, *, limit: int = DEFAULT_BUILD_LIMIT, actor: str | None = None) -> BuildReport:
    """Link every source record not yet in the directory, at most ``limit`` per source.

    Idempotent: a record already linked is skipped (one link per source record), so running it
    again changes nothing until a source gains records."""
    now = utc_now()
    who = actor or context.user_id
    scanned = linked = people = proposals = 0
    complete = True
    for kind in LinkKind:
        rows, more = store.unlinked_sources(kind, limit)
        complete = complete and not more
        for row in rows:
            scanned += 1
            if store.link_for_source(kind, row.source_id) is not None:
                continue
            _link, created, opened = attach_source(store, context, row, now, who)
            linked += 1
            people += int(created)
            proposals += opened
    return BuildReport(scanned=scanned, linked=linked, people_created=people, proposals_created=proposals, complete=complete)


# -- people --------------------------------------------------------------------------------


def create_person(store: ContactsStore, context: TenantContext, payload: PersonInput) -> tuple[Person, int]:
    identifiers = identifiers_from(payload.phones, payload.emails, strict=True)
    now = utc_now()
    person = store.add_person(Person(
        id=new_id("contact"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, name=payload.name, organisation=payload.organisation,
        notes=payload.notes, source=PersonSource.MANUAL, created_by=context.user_id, created_at=now, updated_at=now,
    ))
    proposals = _add_identifiers(store, context, person, identifiers, now)
    return person, proposals


def update_person(store: ContactsStore, context: TenantContext, person: Person, payload: PersonPatch) -> tuple[Person, int]:
    now = utc_now()
    changes: dict[str, Any] = {"updated_at": now}
    for field in ("name", "organisation", "notes"):
        value = getattr(payload, field)
        if value is not None:
            changes[field] = value
    person = store.save_person(person.model_copy(update=changes))
    proposals = 0
    if payload.phones is not None or payload.emails is not None:
        current = store.identifiers_of([person.id]).get(person.id, [])
        phones = payload.phones if payload.phones is not None else [i.label or i.value for i in current if i.kind == IdentifierKind.PHONE]
        emails = payload.emails if payload.emails is not None else [i.label or i.value for i in current if i.kind == IdentifierKind.EMAIL]
        identifiers = identifiers_from(phones, emails, strict=True)
        store.remove_identifiers(person.id)
        proposals = _add_identifiers(store, context, person, identifiers, now)
    return person, proposals


def merge_people(store: ContactsStore, context: TenantContext, keep: Person, remove: Person) -> tuple[Person, dict[str, int]]:
    """Move the other person's identifiers, links and do-not-contact rows onto ``keep`` and delete it."""
    if keep.id == remove.id:
        raise http_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick two different people to merge.")
    now = utc_now()
    moved = {
        "identifiers": store.move_identifiers(remove.id, keep.id),
        "links": store.move_links(remove.id, keep.id),
        "do_not_contact": store.move_dnc(remove.id, keep.id),
    }
    notes = keep.notes
    if remove.notes and remove.notes not in notes:
        notes = f"{notes}\n\n{remove.notes}".strip()[:4000]
    organisation = keep.organisation or remove.organisation
    keep = store.save_person(keep.model_copy(update={"notes": notes, "organisation": organisation, "updated_at": now,
                                                    "source": keep.source if keep.source != PersonSource.MANUAL or remove.source == PersonSource.MANUAL else PersonSource.MERGE}))
    decided = 0
    for proposal in store.proposals_of([keep.id, remove.id]):
        pair = {proposal.contact_a_id, proposal.contact_b_id}
        if pair == {keep.id, remove.id}:
            store.decide_proposal(proposal.id, ProposalStatus.MERGED, context.user_id, now)
            decided += 1
    # Other open proposals that named the removed person now concern the kept one.
    for proposal in store.proposals_of([remove.id]):
        other = proposal.contact_b_id if proposal.contact_a_id == remove.id else proposal.contact_a_id
        store.decide_proposal(proposal.id, ProposalStatus.DISMISSED, context.user_id, now)
        if other != keep.id:
            kind = IdentifierKind.PHONE if proposal.reason == "same_phone" else IdentifierKind.EMAIL
            _propose(store, context, keep.id, other, kind, proposal.matched_value, now)
    store.delete_person(remove.id)
    moved["proposals_closed"] = decided
    return keep, moved


# -- do not contact ------------------------------------------------------------------------


def set_do_not_contact(store: ContactsStore, context: TenantContext, person: Person, channel: Channel, reason: str) -> tuple[DoNotContact, bool]:
    now = utc_now()
    record, created = store.set_dnc(DoNotContact(
        id=new_id("ctdnc"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, contact_id=person.id, channel=channel,
        reason=reason, set_by=context.user_id, set_at=now,
    ))
    store.save_person(person.model_copy(update={"updated_at": now}))
    return record, created


def blocked_channels(records: list[DoNotContact]) -> set[Channel]:
    channels = {r.channel for r in records}
    return set(CONTACT_CHANNELS) if Channel.ALL in channels else channels


def do_not_contact_block(context: TenantContext, channel: Channel, value: str) -> str | None:
    """The sentence a sending path shows when the directory marks ``value`` do not contact on ``channel``, else None.

    Read in the caller's scope; a failure to read propagates, so nothing is sent on a guess.
    The sending paths of Phone calls, the Email Center, WhatsApp and Business outreach call this
    (docs/contacts.md#enforcement)."""
    kind = IdentifierKind.EMAIL if channel == Channel.EMAIL else IdentifierKind.PHONE
    normalised = normalise_value(kind, value)
    if normalised is None:
        return None
    with open_contacts_store(context) as store:
        hits = store.find_by_identifier(kind, normalised)
        if not hits:
            return None
        ids = sorted({h.contact_id for h in hits})
        records = store.dnc_of(ids)
        for contact_id in ids:
            mine = records.get(contact_id, [])
            if channel in blocked_channels(mine):
                person = store.get_person(contact_id)
                record = next((r for r in mine if r.channel == channel), None) or next(r for r in mine if r.channel == Channel.ALL)
                name = person.name if person else "This person"
                where = {Channel.PHONE: "by phone", Channel.EMAIL: "by e-mail", Channel.WHATSAPP: "on WhatsApp"}[channel]
                store.record_audit(AuditRecord(
                    id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
                    action="contacts.send_refused", target=f"contact:{contact_id}", outcome="denied", correlation_id=new_id("correlation"),
                    created_at=utc_now(), metadata={"channel": channel.value, "dnc_channel": record.channel.value},
                ))
                return (f"{name} asked not to be contacted {where} ({record.reason}, recorded {record.set_at.date().isoformat()}), "
                        "so ANUM did not send this. Owners can review it under Contacts.")
    return None


def refuse_if_blocked(context: TenantContext, channel: Channel, value: str) -> None:
    """409 when the directory blocks ``value`` on ``channel``."""
    block = do_not_contact_block(context, channel, value)
    if block:
        raise http_error(status.HTTP_409_CONFLICT, block)


# -- views ---------------------------------------------------------------------------------


def person_views(store: ContactsStore, people: list[Person]) -> list[PersonView]:
    ids = [p.id for p in people]
    identifiers = store.identifiers_of(ids)
    links = store.links_of(ids)
    dnc = store.dnc_of(ids)
    open_counts: dict[str, int] = {}
    for proposal in store.proposals_of(ids):
        for contact_id in (proposal.contact_a_id, proposal.contact_b_id):
            if contact_id in ids:
                open_counts[contact_id] = open_counts.get(contact_id, 0) + 1
    out = []
    for person in people:
        mine = identifiers.get(person.id, [])
        out.append(PersonView(
            id=person.id, name=person.name, organisation=person.organisation, notes=person.notes, source=person.source,
            phones=[IdentifierView(kind=i.kind, value=i.value, label=i.label) for i in mine if i.kind == IdentifierKind.PHONE],
            emails=[IdentifierView(kind=i.kind, value=i.value, label=i.label) for i in mine if i.kind == IdentifierKind.EMAIL],
            sources=sorted({l.kind for l in links.get(person.id, [])}, key=lambda k: k.value),
            do_not_contact=[DoNotContactView(channel=d.channel, reason=d.reason, set_by=d.set_by, set_at=d.set_at) for d in dnc.get(person.id, [])],
            open_proposals=open_counts.get(person.id, 0), created_by=person.created_by, created_at=person.created_at, updated_at=person.updated_at,
        ))
    return out


def counts(store: ContactsStore) -> Counts:
    return Counts(people=store.count_people(), do_not_contact=store.count_dnc_people(), open_proposals=store.count_open_proposals(),
                  by_source=store.count_by_source())


def link_href(row: SourcePerson) -> str:
    if row.kind == LinkKind.CALL_CONTACT:
        return f"#calls-contacts?contact={row.source_id}"
    if row.kind == LinkKind.BUSINESS_CONTACT:
        account = row.facts.get("account_id")
        return f"#business-accounts?account={account}&contact={row.source_id}" if account else "#business-accounts"
    conversation = row.facts.get("conversation_id")
    return f"#whatsapp/{conversation}" if conversation else "#whatsapp"


def link_views(store: ContactsStore, links: list[Link]) -> tuple[list[LinkView], dict[str, SourcePerson]]:
    views: list[LinkView] = []
    rows: dict[str, SourcePerson] = {}
    for link in links:
        row = store.read_source(link.kind, link.source_id)
        if row is None:
            views.append(LinkView(id=link.id, kind=link.kind, source_id=link.source_id, found=False))
            continue
        rows[link.id] = row
        detail = row.organisation
        if link.kind == LinkKind.WHATSAPP_CONTACT:
            detail = row.phones[0] if row.phones else ""
        elif link.kind == LinkKind.BUSINESS_CONTACT and row.facts.get("role_title"):
            detail = f"{row.facts['role_title']}, {row.organisation}" if row.organisation else str(row.facts["role_title"])
        views.append(LinkView(id=link.id, kind=link.kind, source_id=link.source_id, found=True, title=display_name(row), detail=detail,
                              href=link_href(row), facts=row.facts))
    return views, rows


def _iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def channel_summaries(context: TenantContext, person: Person, identifiers: list[Identifier], dnc: list[DoNotContact], rows: Iterable[SourcePerson]) -> list[ChannelSummary]:
    """What every module says about contacting this person, per channel (docs/contacts.md#consent-per-channel)."""
    from ..calls.phone import opt_out_hash
    from ..calls.store import open_calls_store
    from ..contact_consent import ConsentChannel, open_consent_store
    from ..suppression import SuppressionChannel, SuppressionPurpose, open_suppression_store, recipient_hash

    phones = [i.value for i in identifiers if i.kind == IdentifierKind.PHONE]
    emails = [i.value for i in identifiers if i.kind == IdentifierKind.EMAIL]
    hashes = {recipient_hash(context.tenant_id, value): value for value in phones + emails}
    with open_suppression_store(context) as suppressions:
        entries = suppressions.find(context, list(hashes)) if hashes else []
    with open_consent_store(context) as consents:
        records = consents.find(context, list(hashes)) if hashes else []
    opt_outs: dict[str, Any] = {}
    if phones:
        with open_calls_store(context) as calls:
            for value in phones:
                found = calls.find_opt_out(opt_out_hash(context.tenant_id, context.workspace_id, value))
                if found is not None:
                    opt_outs[value] = found
    blocked = blocked_channels(dnc)
    by_kind: dict[LinkKind, list[SourcePerson]] = {}
    for row in rows:
        by_kind.setdefault(row.kind, []).append(row)
    out: list[ChannelSummary] = []
    for channel in CONTACT_CHANNELS:
        values = emails if channel == Channel.EMAIL else phones
        facts: list[ConsentFact] = []
        supp_channel = {Channel.PHONE: None, Channel.EMAIL: SuppressionChannel.EMAIL, Channel.WHATSAPP: SuppressionChannel.WHATSAPP}[channel]
        hard_block = False
        for entry in entries:
            if hashes.get(entry.recipient_hash) not in values:
                continue
            if entry.channel == SuppressionChannel.ALL or (supp_channel is not None and entry.channel == supp_channel):
                facts.append(ConsentFact(source="suppression", state="blocked", detail=f"{entry.purpose.value}:{entry.masked}", at=entry.created_at))
                hard_block = hard_block or entry.purpose == SuppressionPurpose.ALL
        consent_channel = {Channel.PHONE: None, Channel.EMAIL: ConsentChannel.EMAIL, Channel.WHATSAPP: ConsentChannel.WHATSAPP}[channel]
        if consent_channel is not None:
            for record in records:
                if hashes.get(record.recipient_hash) in values and record.channel in {ConsentChannel.ALL, consent_channel}:
                    facts.append(ConsentFact(source="consent_records", state="granted" if record.active else "withdrawn",
                                             detail=record.lawful_basis.value, at=record.withdrawn_at or record.consented_at))
        if channel == Channel.PHONE:
            for row in by_kind.get(LinkKind.CALL_CONTACT, []):
                granted = bool(row.facts.get("ai_consent"))
                facts.append(ConsentFact(source="calls", state="granted" if granted else "none", detail="ai_calls", at=_iso(row.facts.get("ai_consent_at"))))
            for value in values:
                found = opt_outs.get(value)
                if found is not None:
                    facts.append(ConsentFact(source="calls", state="blocked", detail="ai_call_opt_out", at=getattr(found, "created_at", None)))
        if channel in (Channel.EMAIL, Channel.WHATSAPP):
            for row in by_kind.get(LinkKind.BUSINESS_CONTACT, []):
                basis = str(row.facts.get("consent_basis") or "unknown")
                state = "unknown" if basis == "unknown" else "granted"
                if row.facts.get("do_not_contact") or row.facts.get("account_do_not_contact"):
                    state = "blocked"
                facts.append(ConsentFact(source="business", state=state, detail=basis if state != "blocked" else str(row.facts.get("dnc_reason") or "do_not_contact"),
                                         at=_iso(row.facts.get("consent_recorded_at"))))
        if channel == Channel.WHATSAPP:
            for row in by_kind.get(LinkKind.WHATSAPP_CONTACT, []):
                wrote = _iso(row.facts.get("last_inbound_at"))
                facts.append(ConsentFact(source="whatsapp", state="customer_wrote" if wrote else "no_message_yet", detail=str(row.facts.get("conversation_id") or ""), at=wrote))
        record = next((d for d in dnc if d.channel == channel), None) or next((d for d in dnc if d.channel == Channel.ALL), None)
        if record is not None:
            facts.append(ConsentFact(source="directory", state="blocked", detail=record.reason, at=record.set_at))
        out.append(ChannelSummary(
            channel=channel.value, identifiers=values,
            do_not_contact=DoNotContactView(channel=record.channel, reason=record.reason, set_by=record.set_by, set_at=record.set_at) if record else None,
            facts=facts, can_contact=channel not in blocked and not hard_block,
        ))
    return out


def own_mail_contacts(context: TenantContext, emails: list[str]) -> list[MailContactView]:
    """The caller's own Email Center contacts for these addresses: read in the caller's user scope, never stored."""
    if not emails:
        return []
    from ..mailbox.store import open_mail_store

    out: list[MailContactView] = []
    try:
        with open_mail_store(context) as mail:
            for value in emails:
                found = mail.find_contact(value)
                if found is not None:
                    out.append(MailContactView(id=found.id, email=found.email, name=found.name))
    except HTTPException:
        return []
    return out


def proposal_views(store: ContactsStore, proposals: list[MergeProposal]) -> list[ProposalView]:
    ids = sorted({c for p in proposals for c in (p.contact_a_id, p.contact_b_id)})
    people = {p.id: p for p in (store.get_person(i) for i in ids) if p is not None}
    views = {v.id: v for v in person_views(store, list(people.values()))}
    return [
        ProposalView(id=p.id, reason=p.reason, matched_value=p.matched_value, status=p.status, a=views.get(p.contact_a_id), b=views.get(p.contact_b_id),
                     created_at=p.created_at, decided_by=p.decided_by, decided_at=p.decided_at)
        for p in proposals
    ]


def person_detail(store: ContactsStore, context: TenantContext, person: Person, *, can_write: bool, can_manage: bool) -> PersonDetail:
    view = person_views(store, [person])[0]
    links, rows = link_views(store, store.links_of([person.id]).get(person.id, []))
    identifiers = store.identifiers_of([person.id]).get(person.id, [])
    dnc = store.dnc_of([person.id]).get(person.id, [])
    channels = channel_summaries(context, person, identifiers, dnc, rows.values())
    proposals = proposal_views(store, store.proposals_of([person.id]))
    mail = own_mail_contacts(context, [i.value for i in identifiers if i.kind == IdentifierKind.EMAIL])
    return PersonDetail(person=view, links=links, channels=channels, proposals=proposals, mail_contacts=mail, can_write=can_write, can_manage=can_manage)


# -- export ------------------------------------------------------------------------------


def sort_people_for_export(people: list[Person]) -> list[Person]:
    return sorted(people, key=lambda p: (p.name.casefold(), p.id))


def csv_safe(value: str) -> str:
    """Spreadsheet formula injection: a cell never starts with = + - @."""
    return "'" + value if value[:1] in {"=", "+", "-", "@", "\t", "\r"} else value


def export_csv(views: list[PersonView]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["name", "organisation", "phones", "emails", "sources", "do_not_contact", "created_at", "updated_at"])
    for view in views:
        writer.writerow([
            csv_safe(view.name), csv_safe(view.organisation), csv_safe(" ".join(i.value for i in view.phones)), csv_safe(" ".join(i.value for i in view.emails)),
            " ".join(s.value for s in view.sources) or view.source.value, " ".join(d.channel.value for d in view.do_not_contact),
            view.created_at.isoformat(), view.updated_at.isoformat(),
        ])
    return buffer.getvalue()
