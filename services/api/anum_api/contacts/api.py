"""Routes of the Contacts directory (docs/contacts.md#api), all under ``/api/v1/contacts``.

Who may do what mirrors the four source lists: reading needs any of ``call:read``,
``business:read``, ``whatsapp:read`` or ``email:read`` (members and owners; viewers hold none),
changing people, merging, marking do not contact and rebuilding need ``business:write``, and
clearing a do-not-contact mark, deleting a person and the CSV export need ``business:manage``
(owners), as Business's own do-not-contact clearing and the opt-out list's export do. Every
state-changing route writes an audit record with ids and counts only.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import has_permission, tenant_context
from ..schemas import TenantContext, new_id, utc_now
from . import service
from .models import (
    DEFAULT_PAGE,
    MAX_LIST,
    BuildReport,
    Channel,
    DoNotContactSet,
    DoNotContactView,
    MergeRequest,
    PeoplePage,
    PersonDetail,
    PersonInput,
    PersonPatch,
    PersonSource,
    ProposalPage,
    ProposalStatus,
    Reason,
)
from .store import ContactsStore, PeopleQuery, open_contacts_store

router = APIRouter(prefix="/api/v1/contacts", tags=["contacts"])

READ_DENIED = (
    "Contacts hold personal data from Phone calls, Business, WhatsApp and the Email Center, so viewers cannot see them. "
    "Ask a workspace owner to make you a member."
)
WRITE_DENIED = "Only workspace owners and members can change people in Contacts."
MANAGE_DENIED = "Only workspace owners can do this (clear a do-not-contact mark, delete a person, export the directory)."
READ_PERMISSIONS = (Permission.CALL_READ, Permission.BUSINESS_READ, Permission.WHATSAPP_READ, Permission.EMAIL_READ)


def can_read(context: TenantContext) -> bool:
    return any(has_permission(context, permission) for permission in READ_PERMISSIONS)


def can_write(context: TenantContext) -> bool:
    return has_permission(context, Permission.BUSINESS_WRITE)


def can_manage(context: TenantContext) -> bool:
    return has_permission(context, Permission.BUSINESS_MANAGE)


def _read(context: TenantContext) -> None:
    if not can_read(context):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=READ_DENIED)


def _write(context: TenantContext) -> None:
    _read(context)
    if not can_write(context):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=WRITE_DENIED)


def _manage(context: TenantContext) -> None:
    _read(context)
    if not can_manage(context):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=MANAGE_DENIED)


def _audit(store: ContactsStore, context: TenantContext, action: str, target: str, metadata: dict[str, Any]) -> None:
    store.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id, action=action,
        target=target, outcome="success", correlation_id=new_id("correlation"), created_at=utc_now(), metadata=metadata,
    ))


def _person_or_404(store: ContactsStore, contact_id: str):  # type: ignore[no-untyped-def]
    person = store.get_person(contact_id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")
    return person


@router.get("", response_model=PeoplePage)
def list_people(
    q: str | None = Query(default=None, max_length=320),
    source: PersonSource | None = None,
    dnc: Literal["any", "phone", "email", "whatsapp", "none"] | None = None,
    sort: Literal["name", "updated", "created"] = "name",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_LIST),
    context: TenantContext = Depends(tenant_context),
) -> PeoplePage:
    _read(context)
    with open_contacts_store(context) as store:
        people, total = store.list_people(PeopleQuery(q=q, source=source, dnc=dnc, sort=sort, offset=offset, limit=limit))
        return PeoplePage(items=service.person_views(store, people), total=total, counts=service.counts(store),
                          can_write=can_write(context), can_manage=can_manage(context), can_export=can_manage(context))


@router.get("/export", response_class=Response)
def export_people(context: TenantContext = Depends(tenant_context)) -> Response:
    """CSV of every person (owners only): name, organisation, numbers, addresses, sources, do-not-contact channels."""
    _manage(context)
    with open_contacts_store(context) as store:
        views = service.person_views(store, service.sort_people_for_export(store.all_people()))
        body = service.export_csv(views)
        _audit(store, context, "contacts.exported", f"workspace:{context.workspace_id}", {"rows": len(views)})
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="contacts.csv"', "Cache-Control": "no-store"})


@router.get("/proposals", response_model=ProposalPage)
def list_proposals(
    state: ProposalStatus | None = ProposalStatus.OPEN,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_LIST),
    context: TenantContext = Depends(tenant_context),
) -> ProposalPage:
    _read(context)
    with open_contacts_store(context) as store:
        proposals, total = store.list_proposals(state, offset=offset, limit=limit)
        return ProposalPage(items=service.proposal_views(store, proposals), total=total, can_write=can_write(context))


@router.post("/proposals/{proposal_id}/dismiss", response_model=ProposalPage)
def dismiss_proposal(proposal_id: str, context: TenantContext = Depends(tenant_context)) -> ProposalPage:
    """Different people: the proposal is closed and never opened again for this pair."""
    _write(context)
    with open_contacts_store(context) as store:
        proposal = store.get_proposal(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found")
        if proposal.status != ProposalStatus.OPEN:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"This proposal is already {proposal.status.value}.")
        decided = store.decide_proposal(proposal_id, ProposalStatus.DISMISSED, context.user_id, utc_now())
        if decided is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This proposal was decided meanwhile.")
        _audit(store, context, "contacts.proposal_dismissed", f"proposal:{proposal_id}", {"a": proposal.contact_a_id, "b": proposal.contact_b_id})
        return ProposalPage(items=service.proposal_views(store, [decided]), total=1, can_write=True)


@router.post("/directory/rebuild", response_model=BuildReport)
def rebuild_directory(context: TenantContext = Depends(tenant_context)) -> BuildReport:
    """Link every Phone calls, Business and WhatsApp contact not yet in the directory (bounded; run again when ``complete`` is false)."""
    _write(context)
    with open_contacts_store(context) as store:
        report = service.build_directory(store, context)
        _audit(store, context, "contacts.directory_built", f"workspace:{context.workspace_id}", report.model_dump())
    return report


@router.post("", response_model=PersonDetail, status_code=status.HTTP_201_CREATED)
def create_person(payload: PersonInput, context: TenantContext = Depends(tenant_context)) -> PersonDetail:
    _write(context)
    with open_contacts_store(context) as store:
        person, proposals = service.create_person(store, context, payload)
        _audit(store, context, "contacts.created", f"contact:{person.id}",
               {"phones": len(payload.phones), "emails": len(payload.emails), "proposals": proposals})
        return service.person_detail(store, context, person, can_write=True, can_manage=can_manage(context))


@router.get("/{contact_id}", response_model=PersonDetail)
def get_person(contact_id: str, context: TenantContext = Depends(tenant_context)) -> PersonDetail:
    _read(context)
    with open_contacts_store(context) as store:
        person = _person_or_404(store, contact_id)
        return service.person_detail(store, context, person, can_write=can_write(context), can_manage=can_manage(context))


@router.patch("/{contact_id}", response_model=PersonDetail)
def update_person(contact_id: str, payload: PersonPatch, context: TenantContext = Depends(tenant_context)) -> PersonDetail:
    _write(context)
    with open_contacts_store(context) as store:
        person = _person_or_404(store, contact_id)
        person, proposals = service.update_person(store, context, person, payload)
        _audit(store, context, "contacts.updated", f"contact:{person.id}",
               {"fields": sorted(k for k, v in payload.model_dump().items() if v is not None), "proposals": proposals})
        return service.person_detail(store, context, person, can_write=True, can_manage=can_manage(context))


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_person(contact_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """Removes the person, their identifiers, links and do-not-contact marks. The source records stay in their modules."""
    _manage(context)
    with open_contacts_store(context) as store:
        person = _person_or_404(store, contact_id)
        links = len(store.links_of([person.id]).get(person.id, []))
        store.delete_person(person.id)
        _audit(store, context, "contacts.deleted", f"contact:{person.id}", {"links": links})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{contact_id}/merge", response_model=PersonDetail)
def merge_people(contact_id: str, payload: MergeRequest, context: TenantContext = Depends(tenant_context)) -> PersonDetail:
    """Keep ``contact_id``; move the other person's identifiers, links and do-not-contact marks onto it and delete the other."""
    _write(context)
    with open_contacts_store(context) as store:
        keep = _person_or_404(store, contact_id)
        remove = store.get_person(payload.other_id)
        if remove is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The other person was not found; the list may be out of date.")
        keep, moved = service.merge_people(store, context, keep, remove)
        _audit(store, context, "contacts.merged", f"contact:{keep.id}", {"removed": remove.id, **moved})
        return service.person_detail(store, context, keep, can_write=True, can_manage=can_manage(context))


@router.put("/{contact_id}/do-not-contact", response_model=DoNotContactView)
def set_do_not_contact(contact_id: str, payload: DoNotContactSet, response: Response, context: TenantContext = Depends(tenant_context)) -> DoNotContactView:
    """Block a channel (or all) for this person: Phone calls, the Email Center, WhatsApp and Business outreach then refuse it. 201 new, 200 renewed."""
    _write(context)
    with open_contacts_store(context) as store:
        person = _person_or_404(store, contact_id)
        record, created = service.set_do_not_contact(store, context, person, payload.channel, payload.reason)
        _audit(store, context, "contacts.dnc_set", f"contact:{person.id}", {"channel": payload.channel.value, "renewed": not created})
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return DoNotContactView(channel=record.channel, reason=record.reason, set_by=record.set_by, set_at=record.set_at)


@router.delete("/{contact_id}/do-not-contact/{channel}", status_code=status.HTTP_204_NO_CONTENT)
def clear_do_not_contact(contact_id: str, channel: Channel, payload: Reason, context: TenantContext = Depends(tenant_context)) -> Response:
    """Owners only. The reason goes to the audit log. The modules' own flags and the tenant's opt-out list are not changed."""
    _manage(context)
    with open_contacts_store(context) as store:
        person = _person_or_404(store, contact_id)
        removed = store.clear_dnc(person.id, channel)
        if removed is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This person has no do-not-contact mark on that channel.")
        store.save_person(person.model_copy(update={"updated_at": utc_now()}))
        _audit(store, context, "contacts.dnc_cleared", f"contact:{person.id}", {"channel": channel.value, "reason": payload.reason})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
