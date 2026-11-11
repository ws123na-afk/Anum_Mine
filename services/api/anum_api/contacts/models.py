"""Wire and storage models of the Contacts directory (docs/contacts.md#data-model).

A ``Person`` is one human in a workspace. Their phone numbers and e-mail addresses are
``Identifier`` rows (normalised: E.164 phones, lower-case e-mail), the source records they were
built from are ``Link`` rows (a Phone calls contact, a Business contact, a WhatsApp contact), and
a ``DoNotContact`` row per channel is the workspace-wide block the sending paths consult. A
``MergeProposal`` names two people who share a phone number or an e-mail address under different
names; a person confirms or dismisses it, nothing merges on its own.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_NAME = 200
MAX_ORGANISATION = 200
MAX_NOTES = 4000
MAX_REASON = 300
MAX_IDENTIFIERS = 10
MAX_LIST = 100
DEFAULT_PAGE = 25


class LinkKind(StrEnum):
    CALL_CONTACT = "call_contact"
    BUSINESS_CONTACT = "business_contact"
    WHATSAPP_CONTACT = "whatsapp_contact"


class PersonSource(StrEnum):
    MANUAL = "manual"
    CALLS = "calls"
    BUSINESS = "business"
    WHATSAPP = "whatsapp"
    MERGE = "merge"


SOURCE_OF_KIND = {
    LinkKind.CALL_CONTACT: PersonSource.CALLS,
    LinkKind.BUSINESS_CONTACT: PersonSource.BUSINESS,
    LinkKind.WHATSAPP_CONTACT: PersonSource.WHATSAPP,
}


class Channel(StrEnum):
    PHONE = "phone"
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    ALL = "all"


CONTACT_CHANNELS: tuple[Channel, ...] = (Channel.PHONE, Channel.EMAIL, Channel.WHATSAPP)


class IdentifierKind(StrEnum):
    PHONE = "phone"
    EMAIL = "email"


class ProposalStatus(StrEnum):
    OPEN = "open"
    DISMISSED = "dismissed"
    MERGED = "merged"


def _clean(value: str, low: int, high: int, what: str) -> str:
    text = " ".join(value.split())
    if any(ord(ch) < 32 for ch in value if ch not in "\t\n"):
        raise ValueError(f"The {what} has control characters.")
    if not low <= len(text) <= high:
        raise ValueError(f"The {what} must be {low} to {high} characters.")
    return text


def _clean_notes(value: str) -> str:
    text = value.strip()
    if any(ord(ch) < 32 for ch in text if ch not in "\n\t"):
        raise ValueError("The notes have control characters.")
    if len(text) > MAX_NOTES:
        raise ValueError(f"The notes must be at most {MAX_NOTES} characters.")
    return text


# -- storage -----------------------------------------------------------------------------


class Person(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    name: str
    organisation: str = ""
    notes: str = ""
    source: PersonSource = PersonSource.MANUAL
    created_by: str
    created_at: datetime
    updated_at: datetime


class Identifier(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    contact_id: str
    kind: IdentifierKind
    value: str
    label: str = ""
    created_at: datetime


class Link(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    contact_id: str
    kind: LinkKind
    source_id: str
    created_at: datetime


class DoNotContact(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    contact_id: str
    channel: Channel
    reason: str
    set_by: str
    set_at: datetime


class MergeProposal(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    contact_a_id: str
    contact_b_id: str
    reason: Literal["same_phone", "same_email"]
    matched_value: str
    status: ProposalStatus = ProposalStatus.OPEN
    created_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None


class SourcePerson(BaseModel):
    """What one source record says about a person, read live from its module."""

    kind: LinkKind
    source_id: str
    name: str = ""
    phones: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    organisation: str = ""
    updated_at: datetime | None = None
    # Module facts for the consent summary (docs/contacts.md#consent-per-channel).
    facts: dict[str, Any] = Field(default_factory=dict)


# -- requests ----------------------------------------------------------------------------


class PersonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME)
    organisation: str = Field(default="", max_length=MAX_ORGANISATION)
    notes: str = Field(default="", max_length=MAX_NOTES)
    phones: list[str] = Field(default_factory=list, max_length=MAX_IDENTIFIERS)
    emails: list[str] = Field(default_factory=list, max_length=MAX_IDENTIFIERS)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _clean(value, 1, MAX_NAME, "name")

    @field_validator("organisation")
    @classmethod
    def _organisation(cls, value: str) -> str:
        return _clean(value, 0, MAX_ORGANISATION, "organisation") if value.strip() else ""

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str) -> str:
        return _clean_notes(value)


class PersonPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    organisation: str | None = Field(default=None, max_length=MAX_ORGANISATION)
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    phones: list[str] | None = Field(default=None, max_length=MAX_IDENTIFIERS)
    emails: list[str] | None = Field(default=None, max_length=MAX_IDENTIFIERS)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else _clean(value, 1, MAX_NAME, "name")

    @field_validator("organisation")
    @classmethod
    def _organisation(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean(value, 0, MAX_ORGANISATION, "organisation") if value.strip() else ""

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str | None) -> str | None:
        return None if value is None else _clean_notes(value)


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    other_id: str = Field(min_length=1, max_length=80)


class DoNotContactSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Channel
    reason: str = Field(min_length=3, max_length=MAX_REASON)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _clean(value, 3, MAX_REASON, "reason")


class Reason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=MAX_REASON)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _clean(value, 3, MAX_REASON, "reason")


# -- views -------------------------------------------------------------------------------


class IdentifierView(BaseModel):
    kind: IdentifierKind
    value: str
    label: str = ""


class DoNotContactView(BaseModel):
    channel: Channel
    reason: str
    set_by: str
    set_at: datetime


class PersonView(BaseModel):
    id: str
    name: str
    organisation: str
    notes: str
    source: PersonSource
    phones: list[IdentifierView]
    emails: list[IdentifierView]
    sources: list[LinkKind]
    do_not_contact: list[DoNotContactView]
    open_proposals: int
    created_by: str
    created_at: datetime
    updated_at: datetime


class LinkView(BaseModel):
    id: str
    kind: LinkKind
    source_id: str
    # Read live from the source; ``found`` is false when the source record was deleted since.
    found: bool
    title: str = ""
    detail: str = ""
    href: str = ""
    facts: dict[str, Any] = Field(default_factory=dict)


class ConsentFact(BaseModel):
    """One thing a module records about contacting this person on a channel."""

    source: Literal["calls", "business", "whatsapp", "consent_records", "suppression", "directory"]
    state: Literal["granted", "withdrawn", "unknown", "none", "blocked", "customer_wrote", "no_message_yet"]
    detail: str = ""
    at: datetime | None = None


class ChannelSummary(BaseModel):
    channel: Literal["phone", "email", "whatsapp"]
    identifiers: list[str]
    do_not_contact: DoNotContactView | None = None
    facts: list[ConsentFact]
    # False when the directory blocks the channel or the tenant's opt-out list blocks every message to it.
    can_contact: bool


class MailContactView(BaseModel):
    """The caller's own Email Center contact for one of this person's addresses (never stored here)."""

    id: str
    email: str
    name: str = ""
    href: str = "#email/server"


class ProposalView(BaseModel):
    id: str
    reason: Literal["same_phone", "same_email"]
    matched_value: str
    status: ProposalStatus
    a: PersonView | None
    b: PersonView | None
    created_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None


class PersonDetail(BaseModel):
    person: PersonView
    links: list[LinkView]
    channels: list[ChannelSummary]
    proposals: list[ProposalView]
    mail_contacts: list[MailContactView]
    can_write: bool
    can_manage: bool


class Counts(BaseModel):
    people: int
    do_not_contact: int
    open_proposals: int
    by_source: dict[str, int]


class PeoplePage(BaseModel):
    items: list[PersonView]
    total: int
    counts: Counts
    can_write: bool
    can_manage: bool
    can_export: bool


class ProposalPage(BaseModel):
    items: list[ProposalView]
    total: int
    can_write: bool


class BuildReport(BaseModel):
    scanned: int
    linked: int
    people_created: int
    proposals_created: int
    # False when a source still has unlinked records after this bounded run: run again.
    complete: bool
