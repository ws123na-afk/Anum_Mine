"""Storage of the Contacts directory (docs/contacts.md#data-model), memory and PostgreSQL.

The store also reads the three source lists it links (Phone calls contacts, Business contacts,
WhatsApp contacts) as ``SourcePerson`` rows, inside the same tenant and workspace scope. With
PostgreSQL every statement runs under the caller's RLS context; the memory backend filters the
module stores by scope the same way their own APIs do.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import Any, Literal, Protocol

from ..audit import AuditRecord
from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .models import (
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

SortKey = Literal["name", "updated", "created"]


class PeopleQuery:
    """The list's filters: all applied before paging."""

    def __init__(
        self,
        *,
        q: str | None = None,
        source: PersonSource | None = None,
        dnc: Literal["any", "phone", "email", "whatsapp", "none"] | None = None,
        sort: SortKey = "name",
        offset: int = 0,
        limit: int = 25,
    ) -> None:
        self.q = (q or "").strip().casefold() or None
        self.source = source
        self.dnc = dnc
        self.sort = sort
        self.offset = offset
        self.limit = limit


class ContactsStore(Protocol):
    # people
    def add_person(self, person: Person) -> Person: ...
    def get_person(self, contact_id: str) -> Person | None: ...
    def save_person(self, person: Person) -> Person: ...
    def delete_person(self, contact_id: str) -> bool: ...
    def list_people(self, query: PeopleQuery) -> tuple[list[Person], int]: ...
    def all_people(self) -> list[Person]: ...
    def count_people(self) -> int: ...
    def count_by_source(self) -> dict[str, int]: ...
    # identifiers
    def identifiers_of(self, contact_ids: list[str]) -> dict[str, list[Identifier]]: ...
    def find_by_identifier(self, kind: IdentifierKind, value: str) -> list[Identifier]: ...
    def add_identifier(self, identifier: Identifier) -> Identifier: ...
    def remove_identifiers(self, contact_id: str) -> int: ...
    def move_identifiers(self, from_id: str, to_id: str) -> int: ...
    # links
    def links_of(self, contact_ids: list[str]) -> dict[str, list[Link]]: ...
    def link_for_source(self, kind: LinkKind, source_id: str) -> Link | None: ...
    def add_link(self, link: Link) -> Link: ...
    def move_links(self, from_id: str, to_id: str) -> int: ...
    # do not contact
    def dnc_of(self, contact_ids: list[str]) -> dict[str, list[DoNotContact]]: ...
    def set_dnc(self, record: DoNotContact) -> tuple[DoNotContact, bool]: ...
    def clear_dnc(self, contact_id: str, channel: Channel) -> DoNotContact | None: ...
    def move_dnc(self, from_id: str, to_id: str) -> int: ...
    def count_dnc_people(self) -> int: ...
    # proposals
    def add_proposal(self, proposal: MergeProposal) -> tuple[MergeProposal, bool]: ...
    def get_proposal(self, proposal_id: str) -> MergeProposal | None: ...
    def list_proposals(self, status: ProposalStatus | None, *, offset: int, limit: int) -> tuple[list[MergeProposal], int]: ...
    def proposals_of(self, contact_ids: list[str], status: ProposalStatus | None = ProposalStatus.OPEN) -> list[MergeProposal]: ...
    def decide_proposal(self, proposal_id: str, status: ProposalStatus, by: str, at: datetime) -> MergeProposal | None: ...
    def count_open_proposals(self) -> int: ...
    # sources
    def unlinked_sources(self, kind: LinkKind, limit: int) -> tuple[list[SourcePerson], bool]: ...
    def read_source(self, kind: LinkKind, source_id: str) -> SourcePerson | None: ...
    # audit
    def record_audit(self, record: AuditRecord) -> AuditRecord: ...


def person_matches(person: Person, identifiers: list[Identifier], needle: str) -> bool:
    if needle in person.name.casefold() or needle in person.organisation.casefold():
        return True
    bare = needle.replace(" ", "").replace("-", "")
    return any(needle in i.value.casefold() or (bare and bare in i.value.replace(" ", "")) for i in identifiers)


def dnc_matches(records: list[DoNotContact], wanted: str | None) -> bool:
    if wanted is None:
        return True
    channels = {r.channel for r in records}
    if wanted == "none":
        return not channels
    if wanted == "any":
        return bool(channels)
    return Channel(wanted) in channels or Channel.ALL in channels


def sort_people(people: list[Person], sort: SortKey) -> list[Person]:
    if sort == "updated":
        return sorted(people, key=lambda p: (p.updated_at, p.id), reverse=True)
    if sort == "created":
        return sorted(people, key=lambda p: (p.created_at, p.id), reverse=True)
    return sorted(people, key=lambda p: (p.name.casefold(), p.id))


# -- memory ------------------------------------------------------------------------------


class MemoryContactsData:
    """Every workspace's rows for the memory backend; lost on restart."""

    def __init__(self) -> None:
        self.people: dict[str, Person] = {}
        self.identifiers: dict[str, Identifier] = {}
        self.links: dict[str, Link] = {}
        self.dnc: dict[str, DoNotContact] = {}
        self.proposals: dict[str, MergeProposal] = {}
        self.lock = RLock()

    def clear(self) -> None:
        with self.lock:
            for rows in (self.people, self.identifiers, self.links, self.dnc, self.proposals):
                rows.clear()

    def scopes(self) -> set[tuple[str, str]]:
        with self.lock:
            return {(p.tenant_id, p.workspace_id) for p in self.people.values()}


memory_data = MemoryContactsData()


class MemoryContactsStore:
    def __init__(self, data: MemoryContactsData, context: TenantContext) -> None:
        self.data = data
        self.context = context
        self.scope = (context.tenant_id, context.workspace_id)

    def _mine(self, row: Any) -> bool:
        return (row.tenant_id, row.workspace_id) == self.scope

    # -- people

    def add_person(self, person: Person) -> Person:
        if not self._mine(person):
            raise PermissionError("record outside the caller's workspace")
        with self.data.lock:
            self.data.people[person.id] = person.model_copy()
        return person

    def get_person(self, contact_id: str) -> Person | None:
        with self.data.lock:
            found = self.data.people.get(contact_id)
        return found.model_copy() if found is not None and self._mine(found) else None

    def save_person(self, person: Person) -> Person:
        with self.data.lock:
            existing = self.data.people.get(person.id)
            if existing is None or not self._mine(existing) or not self._mine(person):
                raise KeyError(person.id)
            self.data.people[person.id] = person.model_copy()
        return person

    def delete_person(self, contact_id: str) -> bool:
        with self.data.lock:
            existing = self.data.people.get(contact_id)
            if existing is None or not self._mine(existing):
                return False
            del self.data.people[contact_id]
            for rows in (self.data.identifiers, self.data.links, self.data.dnc):
                for key in [k for k, v in rows.items() if v.contact_id == contact_id]:  # type: ignore[attr-defined]
                    del rows[key]
            for key in [k for k, p in self.data.proposals.items() if contact_id in (p.contact_a_id, p.contact_b_id)]:
                del self.data.proposals[key]
        return True

    def all_people(self) -> list[Person]:
        with self.data.lock:
            return [p.model_copy() for p in self.data.people.values() if self._mine(p)]

    def list_people(self, query: PeopleQuery) -> tuple[list[Person], int]:
        people = self.all_people()
        ids = [p.id for p in people]
        identifiers = self.identifiers_of(ids)
        dnc = self.dnc_of(ids)
        found = [
            p for p in people
            if (query.source is None or p.source == query.source)
            and (query.q is None or person_matches(p, identifiers.get(p.id, []), query.q))
            and dnc_matches(dnc.get(p.id, []), query.dnc)
        ]
        found = sort_people(found, query.sort)
        return found[query.offset: query.offset + query.limit], len(found)

    def count_people(self) -> int:
        return len(self.all_people())

    def count_by_source(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for person in self.all_people():
            counts[person.source.value] = counts.get(person.source.value, 0) + 1
        return counts

    # -- identifiers

    def identifiers_of(self, contact_ids: list[str]) -> dict[str, list[Identifier]]:
        wanted = set(contact_ids)
        out: dict[str, list[Identifier]] = {}
        with self.data.lock:
            rows = sorted((i for i in self.data.identifiers.values() if self._mine(i) and i.contact_id in wanted), key=lambda i: (i.created_at, i.id))
        for row in rows:
            out.setdefault(row.contact_id, []).append(row.model_copy())
        return out

    def find_by_identifier(self, kind: IdentifierKind, value: str) -> list[Identifier]:
        with self.data.lock:
            return [i.model_copy() for i in self.data.identifiers.values() if self._mine(i) and i.kind == kind and i.value == value]

    def add_identifier(self, identifier: Identifier) -> Identifier:
        if not self._mine(identifier):
            raise PermissionError("record outside the caller's workspace")
        with self.data.lock:
            for existing in self.data.identifiers.values():
                if self._mine(existing) and existing.contact_id == identifier.contact_id and existing.kind == identifier.kind and existing.value == identifier.value:
                    return existing.model_copy()
            self.data.identifiers[identifier.id] = identifier.model_copy()
        return identifier

    def remove_identifiers(self, contact_id: str) -> int:
        with self.data.lock:
            keys = [k for k, i in self.data.identifiers.items() if self._mine(i) and i.contact_id == contact_id]
            for key in keys:
                del self.data.identifiers[key]
        return len(keys)

    def move_identifiers(self, from_id: str, to_id: str) -> int:
        moved = 0
        with self.data.lock:
            have = {(i.kind, i.value) for i in self.data.identifiers.values() if self._mine(i) and i.contact_id == to_id}
            for key, row in list(self.data.identifiers.items()):
                if self._mine(row) and row.contact_id == from_id:
                    if (row.kind, row.value) in have:
                        del self.data.identifiers[key]
                    else:
                        self.data.identifiers[key] = row.model_copy(update={"contact_id": to_id})
                        have.add((row.kind, row.value))
                        moved += 1
        return moved

    # -- links

    def links_of(self, contact_ids: list[str]) -> dict[str, list[Link]]:
        wanted = set(contact_ids)
        out: dict[str, list[Link]] = {}
        with self.data.lock:
            rows = sorted((l for l in self.data.links.values() if self._mine(l) and l.contact_id in wanted), key=lambda l: (l.created_at, l.id))
        for row in rows:
            out.setdefault(row.contact_id, []).append(row.model_copy())
        return out

    def link_for_source(self, kind: LinkKind, source_id: str) -> Link | None:
        with self.data.lock:
            for row in self.data.links.values():
                if self._mine(row) and row.kind == kind and row.source_id == source_id:
                    return row.model_copy()
        return None

    def add_link(self, link: Link) -> Link:
        if not self._mine(link):
            raise PermissionError("record outside the caller's workspace")
        with self.data.lock:
            existing = self.link_for_source(link.kind, link.source_id)
            if existing is not None:
                return existing
            self.data.links[link.id] = link.model_copy()
        return link

    def move_links(self, from_id: str, to_id: str) -> int:
        moved = 0
        with self.data.lock:
            for key, row in list(self.data.links.items()):
                if self._mine(row) and row.contact_id == from_id:
                    self.data.links[key] = row.model_copy(update={"contact_id": to_id})
                    moved += 1
        return moved

    # -- do not contact

    def dnc_of(self, contact_ids: list[str]) -> dict[str, list[DoNotContact]]:
        wanted = set(contact_ids)
        out: dict[str, list[DoNotContact]] = {}
        with self.data.lock:
            rows = sorted((d for d in self.data.dnc.values() if self._mine(d) and d.contact_id in wanted), key=lambda d: (d.set_at, d.id))
        for row in rows:
            out.setdefault(row.contact_id, []).append(row.model_copy())
        return out

    def set_dnc(self, record: DoNotContact) -> tuple[DoNotContact, bool]:
        if not self._mine(record):
            raise PermissionError("record outside the caller's workspace")
        with self.data.lock:
            for key, existing in self.data.dnc.items():
                if self._mine(existing) and existing.contact_id == record.contact_id and existing.channel == record.channel:
                    updated = record.model_copy(update={"id": existing.id})
                    self.data.dnc[key] = updated
                    return updated.model_copy(), False
            self.data.dnc[record.id] = record.model_copy()
        return record, True

    def clear_dnc(self, contact_id: str, channel: Channel) -> DoNotContact | None:
        with self.data.lock:
            for key, existing in list(self.data.dnc.items()):
                if self._mine(existing) and existing.contact_id == contact_id and existing.channel == channel:
                    del self.data.dnc[key]
                    return existing.model_copy()
        return None

    def move_dnc(self, from_id: str, to_id: str) -> int:
        moved = 0
        with self.data.lock:
            have = {d.channel for d in self.data.dnc.values() if self._mine(d) and d.contact_id == to_id}
            for key, row in list(self.data.dnc.items()):
                if self._mine(row) and row.contact_id == from_id:
                    if row.channel in have:
                        del self.data.dnc[key]
                    else:
                        self.data.dnc[key] = row.model_copy(update={"contact_id": to_id})
                        have.add(row.channel)
                        moved += 1
        return moved

    def count_dnc_people(self) -> int:
        with self.data.lock:
            return len({d.contact_id for d in self.data.dnc.values() if self._mine(d)})

    # -- proposals

    def add_proposal(self, proposal: MergeProposal) -> tuple[MergeProposal, bool]:
        if not self._mine(proposal):
            raise PermissionError("record outside the caller's workspace")
        pair = {proposal.contact_a_id, proposal.contact_b_id}
        with self.data.lock:
            for existing in self.data.proposals.values():
                if self._mine(existing) and {existing.contact_a_id, existing.contact_b_id} == pair:
                    return existing.model_copy(), False
            self.data.proposals[proposal.id] = proposal.model_copy()
        return proposal, True

    def get_proposal(self, proposal_id: str) -> MergeProposal | None:
        with self.data.lock:
            found = self.data.proposals.get(proposal_id)
        return found.model_copy() if found is not None and self._mine(found) else None

    def list_proposals(self, status: ProposalStatus | None, *, offset: int, limit: int) -> tuple[list[MergeProposal], int]:
        with self.data.lock:
            rows = [p.model_copy() for p in self.data.proposals.values() if self._mine(p) and (status is None or p.status == status)]
        rows.sort(key=lambda p: (p.created_at, p.id), reverse=True)
        return rows[offset: offset + limit], len(rows)

    def proposals_of(self, contact_ids: list[str], status: ProposalStatus | None = ProposalStatus.OPEN) -> list[MergeProposal]:
        wanted = set(contact_ids)
        with self.data.lock:
            return [
                p.model_copy() for p in self.data.proposals.values()
                if self._mine(p) and (status is None or p.status == status) and (p.contact_a_id in wanted or p.contact_b_id in wanted)
            ]

    def decide_proposal(self, proposal_id: str, status: ProposalStatus, by: str, at: datetime) -> MergeProposal | None:
        with self.data.lock:
            found = self.data.proposals.get(proposal_id)
            if found is None or not self._mine(found) or found.status != ProposalStatus.OPEN:
                return None
            updated = found.model_copy(update={"status": status, "decided_by": by, "decided_at": at})
            self.data.proposals[proposal_id] = updated
        return updated.model_copy()

    def count_open_proposals(self) -> int:
        with self.data.lock:
            return sum(1 for p in self.data.proposals.values() if self._mine(p) and p.status == ProposalStatus.OPEN)

    # -- sources (the module memory stores, filtered by this scope)

    def _source_rows(self, kind: LinkKind) -> list[SourcePerson]:
        from . import sources

        return sources.memory_rows(kind, self.context)

    def unlinked_sources(self, kind: LinkKind, limit: int) -> tuple[list[SourcePerson], bool]:
        rows = [r for r in self._source_rows(kind) if self.link_for_source(kind, r.source_id) is None]
        rows.sort(key=lambda r: (r.updated_at is not None, r.updated_at.timestamp() if r.updated_at else 0.0, r.source_id), reverse=True)
        return rows[:limit], len(rows) > limit

    def read_source(self, kind: LinkKind, source_id: str) -> SourcePerson | None:
        for row in self._source_rows(kind):
            if row.source_id == source_id:
                return row
        return None

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        from ..dependencies import memory_repository

        return memory_repository.record_audit(record)


@contextmanager
def open_contacts_store(context: TenantContext) -> Iterator[ContactsStore]:
    """One unit of work in the caller's tenant and workspace scope (RLS with PostgreSQL)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.contacts_repository import SqlAlchemyContactsStore

        return SqlAlchemyContactsStore(session, context)

    with open_scoped_store(context, MemoryContactsStore(memory_data, context), sql_store) as store:
        yield store
