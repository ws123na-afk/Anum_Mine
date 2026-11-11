"""PostgreSQL Contacts directory (migration ``0166_contacts_directory``, docs/contacts.md#data-model).

Five tables under forced RLS on tenant and workspace; every statement also names the scope. The
source lists are read through ``contacts.sources.sql_rows`` in the same session, so the caller's
RLS context decides what the directory can see. Audit records go to ``audit_records`` in the same
transaction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from anum_api.audit import AuditRecord
from anum_api.contacts import sources
from anum_api.contacts.models import (
    Channel,
    DoNotContact,
    Identifier,
    IdentifierKind,
    Link,
    LinkKind,
    MergeProposal,
    Person,
    PersonSource,
    ProposalStatus,
    SourcePerson,
)
from anum_api.contacts.store import PeopleQuery, dnc_matches, person_matches, sort_people
from anum_api.schemas import TenantContext

from .repository import SqlAlchemyRepository
from .scope import require_workspace



def _person(row: Any) -> Person:
    return Person(id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, name=row.name, organisation=row.organisation, notes=row.notes,
                  source=PersonSource(row.source), created_by=row.created_by, created_at=row.created_at, updated_at=row.updated_at)


def _identifier(row: Any) -> Identifier:
    return Identifier(id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, contact_id=row.contact_id, kind=IdentifierKind(row.kind),
                      value=row.value, label=row.label, created_at=row.created_at)


def _link(row: Any) -> Link:
    return Link(id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, contact_id=row.contact_id, kind=LinkKind(row.kind),
                source_id=row.source_id, created_at=row.created_at)


def _dnc(row: Any) -> DoNotContact:
    return DoNotContact(id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, contact_id=row.contact_id, channel=Channel(row.channel),
                        reason=row.reason, set_by=row.set_by, set_at=row.set_at)


def _proposal(row: Any) -> MergeProposal:
    return MergeProposal(id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, contact_a_id=row.contact_a_id, contact_b_id=row.contact_b_id,
                         reason=row.reason, matched_value=row.matched_value, status=ProposalStatus(row.status), created_at=row.created_at,
                         decided_by=row.decided_by, decided_at=row.decided_at)


class SqlAlchemyContactsStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context
        self.scope = {"t": context.tenant_id, "w": context.workspace_id}
        self._audit = SqlAlchemyRepository(session)

    def _rows(self, sql: str, **params: Any) -> list[Any]:
        return list(self.session.execute(text(sql), {**self.scope, **params}).all())

    def _one(self, sql: str, **params: Any) -> Any | None:
        return self.session.execute(text(sql), {**self.scope, **params}).one_or_none()

    def _run(self, sql: str, **params: Any) -> int:
        return int(self.session.execute(text(sql), {**self.scope, **params}).rowcount or 0)

    # -- people

    def add_person(self, person: Person) -> Person:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        self._run(
            "insert into contacts (id, tenant_id, workspace_id, name, organisation, notes, source, created_by, created_at, updated_at) "
            "values (:id, :t, :w, :name, :org, :notes, :source, :by, :created, :updated)",
            id=person.id, name=person.name, org=person.organisation, notes=person.notes, source=person.source.value, by=person.created_by,
            created=person.created_at, updated=person.updated_at,
        )
        return person

    def get_person(self, contact_id: str) -> Person | None:
        row = self._one("select id, tenant_id, workspace_id, name, organisation, notes, source, created_by, created_at, updated_at from contacts where tenant_id = :t and workspace_id = :w and id = :id", id=contact_id)
        return _person(row) if row is not None else None

    def save_person(self, person: Person) -> Person:
        changed = self._run(
            "update contacts set name = :name, organisation = :org, notes = :notes, source = :source, updated_at = :updated "
            "where tenant_id = :t and workspace_id = :w and id = :id",
            id=person.id, name=person.name, org=person.organisation, notes=person.notes, source=person.source.value, updated=person.updated_at,
        )
        if not changed:
            raise KeyError(person.id)
        return person

    def delete_person(self, contact_id: str) -> bool:
        self._run("delete from contact_merge_proposals where tenant_id = :t and workspace_id = :w and (contact_a_id = :id or contact_b_id = :id)", id=contact_id)
        return self._run("delete from contacts where tenant_id = :t and workspace_id = :w and id = :id", id=contact_id) > 0

    def all_people(self) -> list[Person]:
        return [_person(r) for r in self._rows("select id, tenant_id, workspace_id, name, organisation, notes, source, created_by, created_at, updated_at from contacts where tenant_id = :t and workspace_id = :w order by lower(name), id")]

    def list_people(self, query: PeopleQuery) -> tuple[list[Person], int]:
        # Filters need the identifiers and marks of each person; a workspace directory fits in memory (the
        # list is bounded by the people the workspace holds), so the filter runs in Python over one read each.
        people = self.all_people()
        if query.source is not None:
            people = [p for p in people if p.source == query.source]
        ids = [p.id for p in people]
        identifiers = self.identifiers_of(ids) if query.q else {}
        marks = self.dnc_of(ids) if query.dnc else {}
        found = [p for p in people if (query.q is None or person_matches(p, identifiers.get(p.id, []), query.q)) and dnc_matches(marks.get(p.id, []), query.dnc)]
        found = sort_people(found, query.sort)
        return found[query.offset: query.offset + query.limit], len(found)

    def count_people(self) -> int:
        row = self._one("select count(*) as n from contacts where tenant_id = :t and workspace_id = :w")
        return int(row.n) if row else 0

    def count_by_source(self) -> dict[str, int]:
        return {r.source: int(r.n) for r in self._rows("select source, count(*) as n from contacts where tenant_id = :t and workspace_id = :w group by source")}

    # -- identifiers

    def identifiers_of(self, contact_ids: list[str]) -> dict[str, list[Identifier]]:
        if not contact_ids:
            return {}
        out: dict[str, list[Identifier]] = {}
        for row in self._rows("select id, tenant_id, workspace_id, contact_id, kind, value, label, created_at from contact_identifiers where tenant_id = :t and workspace_id = :w and contact_id = any(:ids) order by created_at, id", ids=list(contact_ids)):
            out.setdefault(row.contact_id, []).append(_identifier(row))
        return out

    def find_by_identifier(self, kind: IdentifierKind, value: str) -> list[Identifier]:
        return [_identifier(r) for r in self._rows("select id, tenant_id, workspace_id, contact_id, kind, value, label, created_at from contact_identifiers where tenant_id = :t and workspace_id = :w and kind = :kind and value = :value order by created_at, id", kind=kind.value, value=value)]

    def add_identifier(self, identifier: Identifier) -> Identifier:
        self._run(
            "insert into contact_identifiers (id, tenant_id, workspace_id, contact_id, kind, value, label, created_at) "
            "values (:id, :t, :w, :contact, :kind, :value, :label, :created) on conflict on constraint uq_contact_identifiers_value do nothing",
            id=identifier.id, contact=identifier.contact_id, kind=identifier.kind.value, value=identifier.value, label=identifier.label, created=identifier.created_at,
        )
        return identifier

    def remove_identifiers(self, contact_id: str) -> int:
        return self._run("delete from contact_identifiers where tenant_id = :t and workspace_id = :w and contact_id = :id", id=contact_id)

    def move_identifiers(self, from_id: str, to_id: str) -> int:
        self._run(
            "delete from contact_identifiers i where i.tenant_id = :t and i.workspace_id = :w and i.contact_id = :source and exists ("
            "select 1 from contact_identifiers k where k.tenant_id = i.tenant_id and k.workspace_id = i.workspace_id and k.contact_id = :target "
            "and k.kind = i.kind and k.value = i.value)",
            source=from_id, target=to_id,
        )
        return self._run("update contact_identifiers set contact_id = :target where tenant_id = :t and workspace_id = :w and contact_id = :source", source=from_id, target=to_id)

    # -- links

    def links_of(self, contact_ids: list[str]) -> dict[str, list[Link]]:
        if not contact_ids:
            return {}
        out: dict[str, list[Link]] = {}
        for row in self._rows("select id, tenant_id, workspace_id, contact_id, kind, source_id, created_at from contact_links where tenant_id = :t and workspace_id = :w and contact_id = any(:ids) order by created_at, id", ids=list(contact_ids)):
            out.setdefault(row.contact_id, []).append(_link(row))
        return out

    def link_for_source(self, kind: LinkKind, source_id: str) -> Link | None:
        row = self._one("select id, tenant_id, workspace_id, contact_id, kind, source_id, created_at from contact_links where tenant_id = :t and workspace_id = :w and kind = :kind and source_id = :source", kind=kind.value, source=source_id)
        return _link(row) if row is not None else None

    def add_link(self, link: Link) -> Link:
        self._run(
            "insert into contact_links (id, tenant_id, workspace_id, contact_id, kind, source_id, created_at) values (:id, :t, :w, :contact, :kind, :source, :created) "
            "on conflict on constraint uq_contact_links_source do nothing",
            id=link.id, contact=link.contact_id, kind=link.kind.value, source=link.source_id, created=link.created_at,
        )
        return self.link_for_source(link.kind, link.source_id) or link

    def move_links(self, from_id: str, to_id: str) -> int:
        return self._run("update contact_links set contact_id = :target where tenant_id = :t and workspace_id = :w and contact_id = :source", source=from_id, target=to_id)

    # -- do not contact

    def dnc_of(self, contact_ids: list[str]) -> dict[str, list[DoNotContact]]:
        if not contact_ids:
            return {}
        out: dict[str, list[DoNotContact]] = {}
        for row in self._rows("select id, tenant_id, workspace_id, contact_id, channel, reason, set_by, set_at from contact_do_not_contact where tenant_id = :t and workspace_id = :w and contact_id = any(:ids) order by set_at, id", ids=list(contact_ids)):
            out.setdefault(row.contact_id, []).append(_dnc(row))
        return out

    def set_dnc(self, record: DoNotContact) -> tuple[DoNotContact, bool]:
        row = self._one(
            "insert into contact_do_not_contact (id, tenant_id, workspace_id, contact_id, channel, reason, set_by, set_at) "
            "values (:id, :t, :w, :contact, :channel, :reason, :by, :at) on conflict on constraint uq_contact_dnc_channel do update set "
            "reason = excluded.reason, set_by = excluded.set_by, set_at = excluded.set_at returning id, tenant_id, workspace_id, contact_id, channel, reason, set_by, set_at, (xmax = 0) as inserted",
            id=record.id, contact=record.contact_id, channel=record.channel.value, reason=record.reason, by=record.set_by, at=record.set_at,
        )
        if row is None:  # insert ... returning always answers one row
            raise RuntimeError("do-not-contact upsert returned no row")
        return _dnc(row), bool(row.inserted)

    def clear_dnc(self, contact_id: str, channel: Channel) -> DoNotContact | None:
        row = self._one("delete from contact_do_not_contact where tenant_id = :t and workspace_id = :w and contact_id = :id and channel = :channel returning id, tenant_id, workspace_id, contact_id, channel, reason, set_by, set_at", id=contact_id, channel=channel.value)
        return _dnc(row) if row is not None else None

    def move_dnc(self, from_id: str, to_id: str) -> int:
        self._run(
            "delete from contact_do_not_contact d where d.tenant_id = :t and d.workspace_id = :w and d.contact_id = :source and exists ("
            "select 1 from contact_do_not_contact k where k.tenant_id = d.tenant_id and k.workspace_id = d.workspace_id and k.contact_id = :target and k.channel = d.channel)",
            source=from_id, target=to_id,
        )
        return self._run("update contact_do_not_contact set contact_id = :target where tenant_id = :t and workspace_id = :w and contact_id = :source", source=from_id, target=to_id)

    def count_dnc_people(self) -> int:
        row = self._one("select count(distinct contact_id) as n from contact_do_not_contact where tenant_id = :t and workspace_id = :w")
        return int(row.n) if row else 0

    # -- proposals

    def add_proposal(self, proposal: MergeProposal) -> tuple[MergeProposal, bool]:
        row = self._one(
            "insert into contact_merge_proposals (id, tenant_id, workspace_id, contact_a_id, contact_b_id, reason, matched_value, status, created_at) "
            "values (:id, :t, :w, :a, :b, :reason, :value, :status, :created) on conflict on constraint uq_contact_merge_pair do nothing returning id, tenant_id, workspace_id, contact_a_id, contact_b_id, reason, matched_value, status, created_at, decided_by, decided_at",
            id=proposal.id, a=proposal.contact_a_id, b=proposal.contact_b_id, reason=proposal.reason, value=proposal.matched_value, status=proposal.status.value,
            created=proposal.created_at,
        )
        if row is not None:
            return _proposal(row), True
        existing = self._one("select id, tenant_id, workspace_id, contact_a_id, contact_b_id, reason, matched_value, status, created_at, decided_by, decided_at from contact_merge_proposals where tenant_id = :t and workspace_id = :w and contact_a_id = :a and contact_b_id = :b",
                             a=proposal.contact_a_id, b=proposal.contact_b_id)
        return (_proposal(existing) if existing is not None else proposal), False

    def get_proposal(self, proposal_id: str) -> MergeProposal | None:
        row = self._one("select id, tenant_id, workspace_id, contact_a_id, contact_b_id, reason, matched_value, status, created_at, decided_by, decided_at from contact_merge_proposals where tenant_id = :t and workspace_id = :w and id = :id", id=proposal_id)
        return _proposal(row) if row is not None else None

    def list_proposals(self, status: ProposalStatus | None, *, offset: int, limit: int) -> tuple[list[MergeProposal], int]:
        rows = self._rows(
            "select id, tenant_id, workspace_id, contact_a_id, contact_b_id, reason, matched_value, status, created_at, decided_by, decided_at from contact_merge_proposals where tenant_id = :t and workspace_id = :w and (cast(:status as varchar) is null or status = cast(:status as varchar)) "
            "order by created_at desc, id", status=status.value if status else None,
        )
        return [_proposal(r) for r in rows[offset: offset + limit]], len(rows)

    def proposals_of(self, contact_ids: list[str], status: ProposalStatus | None = ProposalStatus.OPEN) -> list[MergeProposal]:
        if not contact_ids:
            return []
        rows = self._rows(
            "select id, tenant_id, workspace_id, contact_a_id, contact_b_id, reason, matched_value, status, created_at, decided_by, decided_at from contact_merge_proposals where tenant_id = :t and workspace_id = :w and (contact_a_id = any(:ids) or contact_b_id = any(:ids)) "
            "and (cast(:status as varchar) is null or status = cast(:status as varchar)) order by created_at desc, id",
            ids=list(contact_ids), status=status.value if status else None,
        )
        return [_proposal(r) for r in rows]

    def decide_proposal(self, proposal_id: str, status: ProposalStatus, by: str, at: datetime) -> MergeProposal | None:
        row = self._one(
            "update contact_merge_proposals set status = :status, decided_by = :by, decided_at = :at where tenant_id = :t and workspace_id = :w and id = :id and status = 'open' returning id, tenant_id, workspace_id, contact_a_id, contact_b_id, reason, matched_value, status, created_at, decided_by, decided_at",
            id=proposal_id, status=status.value, by=by, at=at,
        )
        return _proposal(row) if row is not None else None

    def count_open_proposals(self) -> int:
        row = self._one("select count(*) as n from contact_merge_proposals where tenant_id = :t and workspace_id = :w and status = 'open'")
        return int(row.n) if row else 0

    # -- sources

    def unlinked_sources(self, kind: LinkKind, limit: int) -> tuple[list[SourcePerson], bool]:
        rows = sources.sql_rows(self.session, self.context, kind, source_id=None, unlinked=True, limit=limit + 1)
        return rows[:limit], len(rows) > limit

    def read_source(self, kind: LinkKind, source_id: str) -> SourcePerson | None:
        rows = sources.sql_rows(self.session, self.context, kind, source_id=source_id, unlinked=False, limit=1)
        return rows[0] if rows else None

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        return self._audit.record_audit(record)
