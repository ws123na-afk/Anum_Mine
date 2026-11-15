"""Money lent or borrowed linked to a person in Contacts (docs/finance.md#people-from-contacts).

A loan keeps two things about its person: the name typed when it was saved (``person``, kept as history) and,
optionally, the id of the person in the Contacts directory (``contact_id``, migration ``0186_pf_debts``). Nothing
else from Contacts is copied into finance: the current name is read live from the directory, in the caller's own
RLS scope and only when the caller may read Contacts (any of ``call:read``, ``business:read``, ``whatsapp:read`` or
``email:read``, docs/contacts.md#api). A renamed person shows the new name; a deleted person leaves the loan as it is
with the typed name. There is no second contact store: picking and creating people go through ``/api/v1/contacts``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException

from ..contacts.api import can_read
from ..contacts.store import open_contacts_store
from ..schemas import TenantContext

ContactState = Literal["linked", "deleted", "hidden"]

CONTACT_UNREADABLE = ("You cannot read Contacts in this workspace, so a person from Contacts cannot be linked. "
                      "Type the name instead, or ask a workspace owner to make you a member.")
CONTACT_MISSING = "This person is not in Contacts any more, or belongs to another workspace. Pick them again or type the name."


@dataclass(frozen=True)
class Resolved:
    """What the directory says about a loan's contact for the caller: its current name, or why there is none."""

    state: ContactState
    name: str | None = None


def clean_id(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def check_contact(context: TenantContext, contact_id: str) -> str:
    """The person's current name, or a refusal: Contacts not readable (403), or not found in the caller's scope (422).

    The lookup runs in the caller's tenant and workspace (RLS with PostgreSQL), so another workspace's or tenant's
    person is "not found", never confirmed to exist.
    """
    if not can_read(context):
        raise HTTPException(status_code=403, detail=CONTACT_UNREADABLE)
    with open_contacts_store(context) as store:
        person = store.get_person(contact_id)
    if person is None:
        raise HTTPException(status_code=422, detail=CONTACT_MISSING)
    return person.name


def resolve(context: TenantContext, contact_ids: Iterable[str | None]) -> dict[str, Resolved]:
    """Each distinct contact id's current state for the caller, in one unit of work."""
    wanted = sorted({c for c in contact_ids if c})
    if not wanted:
        return {}
    if not can_read(context):
        return {c: Resolved("hidden") for c in wanted}
    out: dict[str, Resolved] = {}
    with open_contacts_store(context) as store:
        for contact_id in wanted:
            person = store.get_person(contact_id)
            out[contact_id] = Resolved("linked", person.name) if person is not None else Resolved("deleted")
    return out


def fold(name: str) -> str:
    """A typed name for grouping loans without a contact: case and spacing ignored."""
    return " ".join(name.casefold().split())


def person_key(contact_id: str | None, person: str) -> str:
    """The group a loan belongs to: its contact, else its typed name."""
    return f"contact:{contact_id}" if contact_id else f"name:{fold(person)}"
