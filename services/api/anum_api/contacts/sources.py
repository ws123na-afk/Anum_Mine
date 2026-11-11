"""Reading the three source lists the directory links (docs/contacts.md#sources).

Each reader returns ``SourcePerson`` rows: the record's names, phone numbers and e-mail addresses
as the module stores them, the organisation where the module knows one, and the module's own
contact facts (AI call consent, the Business consent basis and do-not-contact mark, the WhatsApp
conversation). Nothing here copies notes, call logs or message text.

Memory: the module stores are filtered by the caller's scope, as their own APIs do. PostgreSQL:
plain selects in the caller's session, so RLS applies to every row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..schemas import TenantContext
from .models import LinkKind, SourcePerson

MAX_SCAN = 5000


def whatsapp_phone(wa_id: str) -> str:
    digits = "".join(ch for ch in wa_id if ch.isdigit())
    return f"+{digits}" if digits else ""


def _call_row(contact: Any) -> SourcePerson:
    consent = getattr(contact, "ai_consent", None)
    return SourcePerson(
        kind=LinkKind.CALL_CONTACT, source_id=contact.id, name=contact.name or "", phones=[contact.phone or contact.phone_normalized],
        emails=[contact.email] if contact.email else [], organisation=contact.company or "", updated_at=contact.updated_at,
        facts={
            "ai_consent": consent is not None,
            "ai_consent_at": consent.granted_at.isoformat() if consent is not None else None,
            "favourite": bool(getattr(contact, "favourite", False)),
        },
    )


def _business_row(contact: Any, account: Any | None) -> SourcePerson:
    basis = getattr(contact, "consent_basis", "unknown")
    basis = basis.value if hasattr(basis, "value") else str(basis)
    return SourcePerson(
        kind=LinkKind.BUSINESS_CONTACT, source_id=contact.id, name=contact.name or "", phones=[contact.phone] if contact.phone else [],
        emails=[contact.email] if contact.email else [], organisation=(account.name if account is not None else ""), updated_at=contact.updated_at,
        facts={
            "consent_basis": basis,
            "consent_recorded_at": contact.consent_recorded_at.isoformat() if getattr(contact, "consent_recorded_at", None) else None,
            "do_not_contact": bool(contact.do_not_contact),
            "dnc_reason": contact.dnc_reason or "",
            "account_id": contact.account_id,
            "account_name": account.name if account is not None else "",
            "account_do_not_contact": bool(account.do_not_contact) if account is not None else False,
            "role_title": getattr(contact, "role_title", "") or "",
        },
    )


def _whatsapp_row(contact: Any, conversation: Any | None) -> SourcePerson:
    phone = whatsapp_phone(contact.wa_id)
    return SourcePerson(
        kind=LinkKind.WHATSAPP_CONTACT, source_id=contact.id, name=contact.profile_name or "", phones=[phone] if phone else [],
        updated_at=contact.updated_at,
        facts={
            "conversation_id": conversation.id if conversation is not None else None,
            "last_inbound_at": conversation.last_inbound_at.isoformat() if conversation is not None and conversation.last_inbound_at else None,
            "last_message_at": conversation.last_message_at.isoformat() if conversation is not None else None,
            "unread_count": conversation.unread_count if conversation is not None else 0,
        },
    )


# -- memory ------------------------------------------------------------------------------


def memory_rows(kind: LinkKind, context: TenantContext) -> list[SourcePerson]:
    scope = (context.tenant_id, context.workspace_id)
    if kind == LinkKind.CALL_CONTACT:
        from ..calls.store import memory_data

        with memory_data.lock:
            return [_call_row(c) for c in memory_data.contacts.values() if (c.tenant_id, c.workspace_id) == scope]
    if kind == LinkKind.BUSINESS_CONTACT:
        from ..business.store import business_store

        with business_store._lock:
            accounts = {a.id: a for a in business_store._rows["accounts"].values() if (a.tenant_id, a.workspace_id) == scope}
            return [_business_row(c, accounts.get(c.account_id)) for c in business_store._rows["contacts"].values() if (c.tenant_id, c.workspace_id) == scope]
    from ..whatsapp_store import whatsapp_store

    with whatsapp_store._lock:
        conversations = {c.contact_id: c for c in whatsapp_store._conversations.values() if (c.tenant_id, c.workspace_id) == scope}
        return [_whatsapp_row(c, conversations.get(c.id)) for c in whatsapp_store._contacts.values() if (c.tenant_id, c.workspace_id) == scope]


def memory_scopes() -> set[tuple[str, str]]:
    """Every workspace with at least one source record (the memory backend's discovery)."""
    from ..business.store import business_store
    from ..calls.store import memory_data
    from ..whatsapp_store import whatsapp_store

    scopes: set[tuple[str, str]] = set()
    with memory_data.lock:
        scopes |= {(c.tenant_id, c.workspace_id) for c in memory_data.contacts.values()}
    with business_store._lock:
        scopes |= {(c.tenant_id, c.workspace_id) for c in business_store._rows["contacts"].values()}
    with whatsapp_store._lock:
        scopes |= {(c.tenant_id, c.workspace_id) for c in whatsapp_store._contacts.values()}
    return scopes


# -- PostgreSQL --------------------------------------------------------------------------

_CALLS = text(
    """
    select c.id, c.name, c.phone, c.phone_normalized, c.company, c.email, c.favourite, c.ai_consent, c.updated_at
    from call_contacts c
    where c.tenant_id = :t and c.workspace_id = :w
      and (cast(:source_id as varchar) is null or c.id = cast(:source_id as varchar))
      and (cast(:unlinked as boolean) = false or not exists (
          select 1 from contact_links l where l.tenant_id = c.tenant_id and l.workspace_id = c.workspace_id
            and l.kind = 'call_contact' and l.source_id = c.id))
    order by c.updated_at desc, c.id
    limit :limit
    """
)
_BUSINESS = text(
    """
    select c.id, c.name, c.phone, c.email, c.account_id, c.consent_basis, c.consent_recorded_at, c.do_not_contact, c.dnc_reason,
           c.role_title, c.updated_at, a.name as account_name, a.do_not_contact as account_dnc
    from business_contacts c
    left join business_accounts a on a.tenant_id = c.tenant_id and a.workspace_id = c.workspace_id and a.id = c.account_id
    where c.tenant_id = :t and c.workspace_id = :w
      and (cast(:source_id as varchar) is null or c.id = cast(:source_id as varchar))
      and (cast(:unlinked as boolean) = false or not exists (
          select 1 from contact_links l where l.tenant_id = c.tenant_id and l.workspace_id = c.workspace_id
            and l.kind = 'business_contact' and l.source_id = c.id))
    order by c.updated_at desc, c.id
    limit :limit
    """
)
_WHATSAPP = text(
    """
    select c.id, c.wa_id, c.profile_name, c.updated_at, v.id as conversation_id, v.last_inbound_at, v.last_message_at, v.unread_count
    from whatsapp_contacts c
    left join whatsapp_conversations v on v.tenant_id = c.tenant_id and v.workspace_id = c.workspace_id and v.contact_id = c.id
    where c.tenant_id = :t and c.workspace_id = :w
      and (cast(:source_id as varchar) is null or c.id = cast(:source_id as varchar))
      and (cast(:unlinked as boolean) = false or not exists (
          select 1 from contact_links l where l.tenant_id = c.tenant_id and l.workspace_id = c.workspace_id
            and l.kind = 'whatsapp_contact' and l.source_id = c.id))
    order by c.updated_at desc, c.id
    limit :limit
    """
)


class _Obj:
    """A row as attributes, for the shared row builders."""

    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


def sql_rows(session: Session, context: TenantContext, kind: LinkKind, *, source_id: str | None, unlinked: bool, limit: int) -> list[SourcePerson]:
    params = {"t": context.tenant_id, "w": context.workspace_id, "source_id": source_id, "unlinked": unlinked, "limit": limit}
    if kind == LinkKind.CALL_CONTACT:
        rows = session.execute(_CALLS, params).all()
        out = []
        for r in rows:
            consent = r.ai_consent
            granted = None
            if isinstance(consent, dict) and consent.get("granted_at"):
                granted = _Obj(granted_at=datetime.fromisoformat(str(consent["granted_at"])))
            out.append(_call_row(_Obj(id=r.id, name=r.name, phone=r.phone, phone_normalized=r.phone_normalized, company=r.company, email=r.email,
                                      favourite=r.favourite, ai_consent=granted, updated_at=r.updated_at)))
        return out
    if kind == LinkKind.BUSINESS_CONTACT:
        rows = session.execute(_BUSINESS, params).all()
        return [
            _business_row(
                _Obj(id=r.id, name=r.name, phone=r.phone, email=r.email, account_id=r.account_id, consent_basis=r.consent_basis,
                     consent_recorded_at=r.consent_recorded_at, do_not_contact=r.do_not_contact, dnc_reason=r.dnc_reason, role_title=r.role_title,
                     updated_at=r.updated_at),
                _Obj(name=r.account_name or "", do_not_contact=bool(r.account_dnc)) if r.account_name is not None else None,
            )
            for r in rows
        ]
    rows = session.execute(_WHATSAPP, params).all()
    return [
        _whatsapp_row(
            _Obj(id=r.id, wa_id=r.wa_id, profile_name=r.profile_name, updated_at=r.updated_at),
            _Obj(id=r.conversation_id, last_inbound_at=r.last_inbound_at, last_message_at=r.last_message_at, unread_count=r.unread_count or 0)
            if r.conversation_id is not None else None,
        )
        for r in rows
    ]
