"""Shared mailboxes, sender identities and contacts of the Email Center (docs/email.md#email-center).

Routes under ``/api/v1/mail`` next to ``api.py``:

* **Shares** (``/accounts/{id}/grants``, ``/grants/{id}``): the owner of a mailbox lets another
  active owner or member of the workspace read it (``read``) or also write and send from it
  (``send``). Only the owner lists, adds, changes or removes shares; the person it was shared
  with may remove their own (leave). Sending from a shared mailbox still waits for an owner's
  approval of the exact message.
* **Identities** (``/accounts/{id}/identities``, ``/identities/{id}``): the addresses and names
  a mailbox sends as (aliases the mail server lets that mailbox send as), with a signature.
  The owner manages them; people with ``send`` access pick one in the composer.
* **Contacts** (``/contacts``): each person's own address book, with search, paging and the
  composer's suggestions.

Every state-changing route writes an audit record with ids and counts, never content.
"""

from __future__ import annotations

import base64
import binascii
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from ..authorization import Permission
from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import InvitationStatus, TenantContext, new_id, utc_now
from .api import ACT_DENIED, READ_DENIED, _audit, _available, _load_account, _require, load_access
from .config import mail_settings
from .service import MailError, clean_addresses, domain_allowed
from .store import (
    GrantAccess,
    MailContact,
    MailGrant,
    MailIdentity,
    contact_sort_key,
    open_mail_store,
)

router = APIRouter(prefix="/api/v1/mail", tags=["mail"])

MAX_IDENTITIES = 20
MAX_CONTACTS = 5000
MAX_SIGNATURE = 2000
SHARE_ROLES = frozenset({"owner", "member"})
PHONE = re.compile(r"^[0-9+()./ -]{0,60}$")


# -- schemas ------------------------------------------------------------------------------------


class GrantView(BaseModel):
    id: str
    account_id: str
    grantee_user_id: str
    grantee_email: str | None
    access: GrantAccess
    created_at: str
    updated_at: str


class GrantCreate(BaseModel):
    grantee_user_id: str = Field(min_length=1, max_length=160)
    access: GrantAccess = "read"


class GrantUpdate(BaseModel):
    access: GrantAccess


class Candidate(BaseModel):
    user_id: str
    email: str | None
    role: str
    # The share this person already has, if any.
    grant_id: str | None
    access: GrantAccess | None


class IdentityView(BaseModel):
    id: str
    account_id: str
    address: str
    display_name: str | None
    signature: str
    is_default: bool
    created_at: str
    updated_at: str


class IdentityCreate(BaseModel):
    address: str = Field(min_length=3, max_length=320)
    display_name: str | None = Field(default=None, max_length=200)
    signature: str = Field(default="", max_length=MAX_SIGNATURE)
    is_default: bool = False


class IdentityUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    signature: str | None = Field(default=None, max_length=MAX_SIGNATURE)
    is_default: bool | None = None


class ContactView(BaseModel):
    id: str
    name: str
    email: str
    company: str
    phone: str
    notes: str
    created_at: str
    updated_at: str


class ContactPage(BaseModel):
    items: list[ContactView]
    total: int
    next_cursor: str | None


class ContactBody(BaseModel):
    name: str = Field(default="", max_length=200)
    email: str = Field(min_length=3, max_length=320)
    company: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=60)
    notes: str = Field(default="", max_length=2000)


class ContactUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    company: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=2000)


# -- helpers ------------------------------------------------------------------------------------


def _grant_view(grant: MailGrant, emails: dict[str, str]) -> GrantView:
    return GrantView(
        id=grant.id, account_id=grant.account_id, grantee_user_id=grant.grantee_user_id, grantee_email=emails.get(grant.grantee_user_id),
        access=grant.access, created_at=grant.created_at.isoformat(), updated_at=grant.updated_at.isoformat(),
    )


def _identity_view(identity: MailIdentity) -> IdentityView:
    return IdentityView(
        id=identity.id, account_id=identity.account_id, address=identity.address, display_name=identity.display_name,
        signature=identity.signature, is_default=identity.is_default, created_at=identity.created_at.isoformat(), updated_at=identity.updated_at.isoformat(),
    )


def _contact_view(contact: MailContact) -> ContactView:
    return ContactView(
        id=contact.id, name=contact.name, email=contact.email, company=contact.company, phone=contact.phone, notes=contact.notes,
        created_at=contact.created_at.isoformat(), updated_at=contact.updated_at.isoformat(),
    )


def _one_line(value: str | None) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", value or "").strip()


def _address(value: str) -> str:
    try:
        cleaned = clean_addresses([value])
    except MailError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    if not cleaned:
        raise HTTPException(status_code=422, detail="Type an email address.")
    return cleaned[0].lower()


def _member_emails(repository: AnumRepository, context: TenantContext) -> dict[str, str]:
    """The address each member joined with (their accepted invitation), for showing who is who."""
    emails: dict[str, str] = {}
    for invitation in reversed(repository.list_invitations(context)):
        if invitation.status == InvitationStatus.ACCEPTED and invitation.accepted_by_user_id and invitation.invitee_email:
            emails[invitation.accepted_by_user_id] = invitation.invitee_email
    return emails


def _shareable_members(repository: AnumRepository, context: TenantContext) -> dict[str, str]:
    """Active owners and members of this workspace other than the caller (user id to role)."""
    return {
        m.user_id: m.role
        for m in repository.list_memberships(context)
        if m.active and m.role.lower() in SHARE_ROLES and m.user_id != context.user_id
    }


def _encode_cursor(key: tuple[str, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(key)).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="That page link is no longer valid. Start from the first page.") from None
    if not (isinstance(value, list) and len(value) == 2 and all(isinstance(v, str) for v in value)):
        raise HTTPException(status_code=422, detail="That page link is no longer valid. Start from the first page.")
    return value[0], value[1]


# -- shares -------------------------------------------------------------------------------------


@router.get("/accounts/{account_id}/grants", response_model=list[GrantView])
async def list_grants(
    account_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> list[GrantView]:
    """Who the owner shared this mailbox with."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    account = _load_account(context, account_id, "owner")
    with open_mail_store(context) as store:
        grants = store.list_grants(account.id)
    emails = _member_emails(repository, context)
    return [_grant_view(g, emails) for g in grants]


@router.get("/accounts/{account_id}/share-candidates", response_model=list[Candidate])
async def share_candidates(
    account_id: str,
    q: str | None = Query(default=None, max_length=200),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[Candidate]:
    """Active owners and members of the workspace the mailbox can be shared with."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    account = _load_account(context, account_id, "owner")
    with open_mail_store(context) as store:
        grants = {g.grantee_user_id: g for g in store.list_grants(account.id)}
    emails = _member_emails(repository, context)
    needle = _one_line(q).casefold()
    found = [
        Candidate(user_id=user, email=emails.get(user), role=role.lower(), grant_id=grants[user].id if user in grants else None, access=grants[user].access if user in grants else None)
        for user, role in _shareable_members(repository, context).items()
        if not needle or needle in user.casefold() or needle in (emails.get(user) or "").casefold()
    ]
    return sorted(found, key=lambda c: ((c.email or c.user_id).casefold(), c.user_id))[:200]


@router.post("/accounts/{account_id}/grants", response_model=GrantView, status_code=status.HTTP_201_CREATED)
async def create_grant(
    account_id: str, body: GrantCreate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> GrantView:
    """Share the mailbox with a member (or change what they may do if it is already shared)."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    _available(context)
    account = _load_account(context, account_id, "owner")
    grantee = body.grantee_user_id.strip()
    if grantee == context.user_id:
        raise HTTPException(status_code=422, detail="This is your own mailbox already.")
    if grantee not in _shareable_members(repository, context):
        raise HTTPException(status_code=422, detail="Share a mailbox only with an active owner or member of this workspace. Viewers have no email.")
    now = utc_now()
    with open_mail_store(context) as store:
        existing = next((g for g in store.list_grants(account.id) if g.grantee_user_id == grantee), None)
        grant = existing or MailGrant(
            id=new_id("mailshare"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            account_id=account.id, grantee_user_id=grantee, created_at=now,
        )
        grant.access, grant.updated_at = body.access, now
        try:
            store.save_grant(grant)
        except ValueError:
            raise HTTPException(status_code=409, detail="This mailbox is already shared with that person.") from None
    _audit(repository, context, "mail.share.grant", account.id, metadata={"grant_id": grant.id, "grantee": grantee, "access": grant.access})
    return _grant_view(grant, _member_emails(repository, context))


@router.patch("/grants/{grant_id}", response_model=GrantView)
async def update_grant(
    grant_id: str, body: GrantUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> GrantView:
    """Let a person read only, or read and send (the mailbox's owner only)."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    with open_mail_store(context) as store:
        grant = store.get_grant(grant_id)
        if grant is None:
            raise HTTPException(status_code=404, detail="That share is gone.")
        if grant.owner_user_id != context.user_id:
            raise HTTPException(status_code=403, detail="Only the mailbox's owner can change what others may do with it.")
        grant.access, grant.updated_at = body.access, utc_now()
        store.save_grant(grant)
    _audit(repository, context, "mail.share.update", grant.account_id, metadata={"grant_id": grant.id, "access": grant.access})
    return _grant_view(grant, _member_emails(repository, context))


@router.delete("/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_grant(
    grant_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> Response:
    """Stop sharing (the owner), or leave a mailbox shared with you (the person it was shared with)."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    with open_mail_store(context) as store:
        grant = store.get_grant(grant_id)
        if grant is None or not store.delete_grant(grant_id):
            raise HTTPException(status_code=404, detail="That share is gone.")
    who = "owner" if grant.owner_user_id == context.user_id else "grantee"
    _audit(repository, context, "mail.share.revoke", grant.account_id, metadata={"grant_id": grant.id, "by": who})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- identities ---------------------------------------------------------------------------------


@router.get("/accounts/{account_id}/identities", response_model=list[IdentityView])
async def list_identities(account_id: str, context: TenantContext = Depends(tenant_context)) -> list[IdentityView]:
    """The addresses this mailbox sends as (default first). Empty: it sends as its own address."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    account, access = load_access(context, account_id, "read")
    if access == "read":
        return []
    with open_mail_store(context) as store:
        return [_identity_view(i) for i in store.list_identities(account.id)]


def _clear_default(store, account_id: str, keep: str) -> None:  # type: ignore[no-untyped-def]
    for other in store.list_identities(account_id):
        if other.is_default and other.id != keep:
            other.is_default, other.updated_at = False, utc_now()
            store.save_identity(other)


@router.post("/accounts/{account_id}/identities", response_model=IdentityView, status_code=status.HTTP_201_CREATED)
async def create_identity(
    account_id: str, body: IdentityCreate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> IdentityView:
    """Add an address this mailbox may send as (it must be an alias of it on the mail server)."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    _available(context)
    account = _load_account(context, account_id, "owner")
    address = _address(body.address)
    if not domain_allowed(address):
        raise HTTPException(status_code=422, detail=f"Send-as addresses must be on {', '.join(sorted(mail_settings.domains))}.")
    now = utc_now()
    identity = MailIdentity(
        id=new_id("mailident"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        account_id=account.id, address=address, display_name=_one_line(body.display_name)[:200] or None,
        signature=body.signature.replace("\r\n", "\n").strip("\n"), is_default=body.is_default, created_at=now, updated_at=now,
    )
    with open_mail_store(context) as store:
        current = store.list_identities(account.id)
        if len(current) >= MAX_IDENTITIES:
            raise HTTPException(status_code=409, detail=f"A mailbox can have at most {MAX_IDENTITIES} sender identities.")
        if any(i.address == address for i in current):
            raise HTTPException(status_code=409, detail="This mailbox already sends as that address.")
        if identity.is_default:
            _clear_default(store, account.id, identity.id)
        try:
            store.save_identity(identity)
        except ValueError:
            raise HTTPException(status_code=409, detail="This mailbox already sends as that address.") from None
    _audit(repository, context, "mail.identity.create", account.id, metadata={"identity_id": identity.id, "default": identity.is_default})
    return _identity_view(identity)


def _own_identity(store, context: TenantContext, identity_id: str) -> MailIdentity:  # type: ignore[no-untyped-def]
    identity = store.get_identity(identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="That sender identity is gone.")
    if identity.owner_user_id != context.user_id:
        raise HTTPException(status_code=403, detail="Only the mailbox's owner can change its sender identities.")
    return identity


@router.patch("/identities/{identity_id}", response_model=IdentityView)
async def update_identity(
    identity_id: str, body: IdentityUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> IdentityView:
    """Change the name, the signature or which identity is the default (the address stays)."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    with open_mail_store(context) as store:
        identity = _own_identity(store, context, identity_id)
        if "display_name" in body.model_fields_set:
            identity.display_name = _one_line(body.display_name)[:200] or None
        if body.signature is not None:
            identity.signature = body.signature.replace("\r\n", "\n").strip("\n")
        if body.is_default is not None:
            if body.is_default:
                _clear_default(store, identity.account_id, identity.id)
            identity.is_default = body.is_default
        identity.updated_at = utc_now()
        store.save_identity(identity)
    _audit(repository, context, "mail.identity.update", identity.account_id, metadata={"identity_id": identity.id, "default": identity.is_default})
    return _identity_view(identity)


@router.delete("/identities/{identity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity(
    identity_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> Response:
    """Remove a sender identity. Drafts that used it go back to the mailbox address."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    with open_mail_store(context) as store:
        identity = _own_identity(store, context, identity_id)
        store.delete_identity(identity.id)
    _audit(repository, context, "mail.identity.delete", identity.account_id, metadata={"identity_id": identity.id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- contacts -----------------------------------------------------------------------------------


@router.get("/contacts", response_model=ContactPage)
async def list_contacts(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=1200),
    context: TenantContext = Depends(tenant_context),
) -> ContactPage:
    """The caller's address book by name; ``q`` searches name, address and company."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    query = _one_line(q) or None
    after = _decode_cursor(cursor) if cursor else None
    with open_mail_store(context) as store:
        items, total = store.list_contacts(query, after, limit + 1)
    more = len(items) > limit
    items = items[:limit]
    return ContactPage(items=[_contact_view(c) for c in items], total=total, next_cursor=_encode_cursor(contact_sort_key(items[-1])) if more and items else None)


@router.get("/contacts/{contact_id}", response_model=ContactView)
async def get_contact(contact_id: str, context: TenantContext = Depends(tenant_context)) -> ContactView:
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    with open_mail_store(context) as store:
        contact = store.get_contact(contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="That contact is gone.")
    return _contact_view(contact)


def _apply(contact: MailContact, name: str | None, email: str | None, company: str | None, phone: str | None, notes: str | None) -> None:
    if name is not None:
        contact.name = _one_line(name)[:200]
    if email is not None:
        contact.email = _address(email)
    if company is not None:
        contact.company = _one_line(company)[:200]
    if phone is not None:
        cleaned = _one_line(phone)
        if not PHONE.match(cleaned):
            raise HTTPException(status_code=422, detail="A phone number can hold digits, spaces and + ( ) . / - only.")
        contact.phone = cleaned
    if notes is not None:
        contact.notes = notes.replace("\r\n", "\n").strip()[:2000]


@router.post("/contacts", response_model=ContactView, status_code=status.HTTP_201_CREATED)
async def create_contact(
    body: ContactBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> ContactView:
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    now = utc_now()
    contact = MailContact(id=new_id("mailcontact"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id, email="-", created_at=now, updated_at=now)
    _apply(contact, body.name, body.email, body.company, body.phone, body.notes)
    with open_mail_store(context) as store:
        if store.count_contacts() >= MAX_CONTACTS:
            raise HTTPException(status_code=409, detail=f"An address book holds at most {MAX_CONTACTS} contacts. Remove some first.")
        if store.find_contact(contact.email) is not None:
            raise HTTPException(status_code=409, detail="You already have a contact with this address.")
        try:
            store.save_contact(contact)
        except ValueError:
            raise HTTPException(status_code=409, detail="You already have a contact with this address.") from None
    _audit(repository, context, "mail.contact.create", contact.id)
    return _contact_view(contact)


@router.patch("/contacts/{contact_id}", response_model=ContactView)
async def update_contact(
    contact_id: str, body: ContactUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> ContactView:
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    with open_mail_store(context) as store:
        contact = store.get_contact(contact_id)
        if contact is None:
            raise HTTPException(status_code=404, detail="That contact is gone.")
        _apply(contact, body.name, body.email, body.company, body.phone, body.notes)
        clash = store.find_contact(contact.email)
        if clash is not None and clash.id != contact.id:
            raise HTTPException(status_code=409, detail="You already have a contact with this address.")
        contact.updated_at = utc_now()
        try:
            store.save_contact(contact)
        except ValueError:
            raise HTTPException(status_code=409, detail="You already have a contact with this address.") from None
    _audit(repository, context, "mail.contact.update", contact.id)
    return _contact_view(contact)


@router.delete("/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> Response:
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    with open_mail_store(context) as store:
        if not store.delete_contact(contact_id):
            raise HTTPException(status_code=404, detail="That contact is gone.")
    _audit(repository, context, "mail.contact.delete", contact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

