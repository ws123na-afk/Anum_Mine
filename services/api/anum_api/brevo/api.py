"""HTTP routes for Brevo email campaigns: ``/api/v1/brevo`` (docs/brevo.md).

Permissions (``anum_api/authorization.py``, the marketing keys): every member reads the
connection state, lists, senders, campaigns, reports and drafts (``marketing:read``);
members and owners write drafts and ask to send them (``marketing:write``); owners paste,
check and remove the API key and send a test to the Brevo account's own address
(``marketing:manage``). Sending or scheduling to contacts always waits for an owner's
approval (``brevo.send_campaign``, ``always_ask``).

Routes that call Brevo are ``async`` and do their database work in short units of work off
the event loop; no transaction stays open across a Brevo call (docs/realtime.md#event-loop).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel

from ..authorization import Permission
from ..demo import refuse_send_in_demo
from ..dependencies import (
    _unit_of_work,
    has_permission,
    repository_context,
    require_permission,
    tenant_context,
)
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from .client import MAX_LIMIT, BrevoError, valid_key
from .models import (
    DELETABLE,
    EDITABLE,
    BrevoConnection,
    CampaignDraft,
    ConnectionSave,
    ConnectionStatus,
    DraftInput,
    DraftStatus,
    SendRequest,
)
from .service import (
    DraftView,
    StatusView,
    audit,
    brevo_failure,
    campaign_tag,
    client_for,
    connection_view,
    content_hash,
    draft_view,
    encrypt_key,
    http_error,
    last4,
    load_connection,
    mark_connection,
    reconcile,
    request_approval,
    require_connection,
    retire_approval,
    send_arguments,
)
from .store import MAX_PAGE, open_brevo_store
from .tools import register_brevo_tools

router = APIRouter(prefix="/api/v1/brevo", tags=["brevo"])

READ_DENIED = "Email campaigns are for members of this workspace. Ask a workspace owner to add you."
WRITE_DENIED = "Only workspace owners and members write campaign drafts and ask to send them."
MANAGE_DENIED = "Only a workspace owner connects Brevo, checks or removes the key and sends a test."

# One test send per draft at a time in this process (two quick clicks never make two campaigns).
_test_locks: dict[str, asyncio.Lock] = {}


def _read(context: TenantContext) -> None:
    if not has_permission(context, Permission.MARKETING_READ):
        raise http_error(403, READ_DENIED)
    require_permission(context, Permission.MARKETING_READ)


def _write(context: TenantContext) -> None:
    _read(context)
    if not has_permission(context, Permission.MARKETING_WRITE):
        raise http_error(403, WRITE_DENIED)


def _manage(context: TenantContext) -> None:
    _read(context)
    if not has_permission(context, Permission.MARKETING_MANAGE):
        raise http_error(403, MANAGE_DENIED)


def _status(context: TenantContext, connection: BrevoConnection | None) -> StatusView:
    return StatusView(
        connected=connection is not None and connection.status != ConnectionStatus.NEEDS_KEY, connection=connection_view(connection),
        can_read=True, can_write=has_permission(context, Permission.MARKETING_WRITE), can_manage=has_permission(context, Permission.MARKETING_MANAGE),
    )


async def _connected_client(context: TenantContext):  # type: ignore[no-untyped-def]
    connection = await asyncio.to_thread(require_connection, context)
    if connection.status == ConnectionStatus.NEEDS_KEY:
        raise http_error(409, "Brevo refused the saved API key. An owner saves a new key on the Connection tab.")
    return connection, client_for(connection)


async def _brevo(context: TenantContext, call):  # type: ignore[no-untyped-def]
    """One Brevo call; a refused key also marks the connection so the page says what to fix."""
    try:
        return await call
    except BrevoError as error:
        if error.auth_failed:
            await asyncio.to_thread(mark_connection, context, error)
        raise brevo_failure(error) from error


# -- connection ---------------------------------------------------------------------------------


@router.get("/status", response_model=StatusView)
def get_status(context: TenantContext = Depends(tenant_context)) -> StatusView:
    _read(context)
    return _status(context, load_connection(context))


@router.put("/connection", response_model=StatusView)
async def save_connection(payload: ConnectionSave, context: TenantContext = Depends(tenant_context)) -> StatusView:
    """The owner pastes the API key; it is checked with Brevo (GET /account) before it is kept."""
    _manage(context)
    key = payload.api_key.strip()
    if key.startswith("xsmtpsib-"):
        raise http_error(422, "That is an SMTP key. Create an API key in Brevo (SMTP & API, API keys, Generate a new API key) and paste that.")
    if not valid_key(key):
        raise http_error(422, "That does not look like a Brevo API key. It starts with xkeysib-; copy it again from Brevo (SMTP & API, API keys).")
    from pydantic import SecretStr

    from .client import BrevoClient

    try:
        account = await BrevoClient(SecretStr(key)).account()
    except BrevoError as error:
        raise brevo_failure(error) from error
    now = utc_now()

    def save() -> BrevoConnection:
        with open_brevo_store(context) as store:
            existing = store.get_connection(for_update=True)
            connection = BrevoConnection(
                tenant_id=context.tenant_id, workspace_id=context.workspace_id, api_key_ciphertext=encrypt_key(key), key_last4=last4(key),
                status=ConnectionStatus.CONNECTED, account_email=account.email,
                account_name=" ".join(part for part in (account.first_name, account.last_name) if part), company=account.company,
                plan=account.plan, last_checked_at=now, last_error=None, connected_by=context.user_id,
                created_at=existing.created_at if existing else now, updated_at=now,
            )
            store.save_connection(connection)
            return connection

    connection = await asyncio.to_thread(save)
    await asyncio.to_thread(audit, context, "brevo.connection.saved", "brevo_connection", metadata={"key_last4": connection.key_last4, "plan": connection.plan})
    return _status(context, connection)


@router.post("/connection/check", response_model=StatusView)
async def check_connection(context: TenantContext = Depends(tenant_context)) -> StatusView:
    _manage(context)
    connection = await asyncio.to_thread(require_connection, context)
    error: BrevoError | None = None
    account = None
    try:
        account = await client_for(connection).account()
    except BrevoError as caught:
        error = caught
    updated = await asyncio.to_thread(mark_connection, context, error, account=account)
    await asyncio.to_thread(audit, context, "brevo.connection.checked", "brevo_connection", outcome="success" if error is None else "failure",
                            metadata={"status": updated.status.value if updated else "missing"})
    return _status(context, updated)


@router.delete("/connection", response_model=StatusView)
def delete_connection(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> StatusView:
    """Forget the key. Drafts stay; campaigns already in Brevo are untouched."""
    _manage(context)
    with open_brevo_store(context) as store:
        removed = store.delete_connection()
    if not removed:
        raise http_error(404, "Brevo is not connected.")
    audit(context, "brevo.connection.removed", "brevo_connection", repository=repository)
    return _status(context, None)


# -- Brevo reads --------------------------------------------------------------------------------


class ListPage(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


@router.get("/lists", response_model=ListPage)
async def get_lists(limit: int = Query(default=50, ge=1, le=MAX_LIMIT), offset: int = Query(default=0, ge=0),
                    context: TenantContext = Depends(tenant_context)) -> ListPage:
    _read(context)
    _, client = await _connected_client(context)
    items, total = await _brevo(context, client.lists(limit=limit, offset=offset))
    return ListPage(items=[item.__dict__ for item in items], total=total, limit=limit, offset=offset)


@router.get("/senders")
async def get_senders(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _read(context)
    _, client = await _connected_client(context)
    senders = await _brevo(context, client.senders())
    return {"items": [item.__dict__ for item in senders]}


@router.get("/campaigns", response_model=ListPage)
async def get_campaigns(limit: int = Query(default=20, ge=1, le=MAX_LIMIT), offset: int = Query(default=0, ge=0),
                        status_filter: str | None = Query(default=None, alias="status"),
                        context: TenantContext = Depends(tenant_context)) -> ListPage:
    _read(context)
    _, client = await _connected_client(context)
    items, total = await _brevo(context, client.campaigns(limit=limit, offset=offset, status=status_filter))
    return ListPage(items=[item.as_dict() for item in items], total=total, limit=limit, offset=offset)


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _read(context)
    if campaign_id < 1:
        raise http_error(404, "Campaign not found")
    _, client = await _connected_client(context)
    campaign = await _brevo(context, client.campaign(campaign_id))
    return campaign.as_dict()


# -- drafts -------------------------------------------------------------------------------------


class DraftPage(BaseModel):
    items: list[DraftView]
    total: int
    limit: int
    offset: int


def _views(context: TenantContext, repository: AnumRepository, drafts: list[CampaignDraft], store: Any, *, detail: bool = False) -> list[DraftView]:
    result = []
    for draft in drafts:
        approval = repository.get_approval(draft.approval_id, context) if draft.approval_id else None
        if reconcile(draft, approval):
            store.save_draft(draft)
        result.append(draft_view(draft, approval, detail=detail))
    return result


def _get_or_404(store: Any, draft_id: str, *, for_update: bool = False) -> CampaignDraft:
    found = store.get_draft(draft_id, for_update=for_update)
    if found is None:
        raise http_error(404, "Campaign draft not found")
    return found


@router.get("/drafts", response_model=DraftPage)
def list_drafts(q: str | None = Query(default=None, max_length=200), status_filter: DraftStatus | None = Query(default=None, alias="status"),
                limit: int = Query(default=20, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> DraftPage:
    _read(context)
    with open_brevo_store(context) as store:
        drafts, total = store.list_drafts(text=q, status=status_filter, limit=limit, offset=offset)
        items = _views(context, repository, drafts, store)
    return DraftPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/drafts/{draft_id}", response_model=DraftView)
def get_draft(draft_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> DraftView:
    _read(context)
    with open_brevo_store(context) as store:
        draft = _get_or_404(store, draft_id)
        return _views(context, repository, [draft], store, detail=True)[0]


async def _checked_content(context: TenantContext, payload: DraftInput) -> tuple[Any, list[str]]:
    """The sender must be one of the account's verified, active senders; the lists must exist."""
    _, client = await _connected_client(context)
    senders = await _brevo(context, client.senders())
    sender = next((item for item in senders if item.id == payload.sender_id), None)
    if sender is None or not sender.active:
        raise http_error(422, "Pick one of the Brevo account's verified, active senders (Brevo: Senders, domains and IPs).")
    names = []
    for list_id in payload.list_ids:
        try:
            names.append((await client.contact_list(list_id)).name)
        except BrevoError as error:
            if error.status == 404:
                raise http_error(422, f"Contact list {list_id} does not exist in Brevo. Refresh the lists and pick again.") from error
            raise brevo_failure(error) from error
    return sender, names


@router.post("/drafts", response_model=DraftView, status_code=status.HTTP_201_CREATED)
async def create_draft(payload: DraftInput, context: TenantContext = Depends(tenant_context)) -> DraftView:
    _write(context)
    sender, names = await _checked_content(context, payload)
    now = utc_now()
    draft = CampaignDraft(
        id=new_id("brevodraft"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, name=payload.name, subject=payload.subject,
        preview_text=payload.preview_text, sender_id=sender.id, sender_name=sender.name, sender_email=sender.email, html=payload.html,
        content_hash=content_hash(name=payload.name, subject=payload.subject, preview_text=payload.preview_text, sender_id=sender.id,
                                  sender_email=sender.email, html=payload.html, list_ids=payload.list_ids),
        list_ids=payload.list_ids, list_names=names, created_by=context.user_id, created_at=now, updated_at=now,
    )

    def save() -> None:
        with open_brevo_store(context) as store:
            store.add_draft(draft)

    await asyncio.to_thread(save)
    await asyncio.to_thread(audit, context, "brevo.draft.created", draft.id, metadata={"lists": len(draft.list_ids), "html_chars": len(draft.html)})
    return draft_view(draft, detail=True)


@router.put("/drafts/{draft_id}", response_model=DraftView)
async def update_draft(draft_id: str, payload: DraftInput, context: TenantContext = Depends(tenant_context)) -> DraftView:
    """Edit a draft. Editing one that waits for approval retires that approval first."""
    _write(context)
    sender, names = await _checked_content(context, payload)

    def save() -> CampaignDraft:
        with _unit_of_work(context, require_membership=True) as repository, open_brevo_store(context) as store:
            draft = _get_or_404(store, draft_id, for_update=True)
            if draft.status not in EDITABLE:
                raise http_error(409, f"The campaign is {draft.status.value.replace('_', ' ')} and can no longer be edited.")
            if draft.status == DraftStatus.PENDING_APPROVAL:
                retire_approval(repository, context, draft.approval_task_id, draft.approval_id)
            draft = draft.model_copy(update={
                "name": payload.name, "subject": payload.subject, "preview_text": payload.preview_text, "sender_id": sender.id,
                "sender_name": sender.name, "sender_email": sender.email, "html": payload.html, "list_ids": payload.list_ids, "list_names": names,
                "content_hash": content_hash(name=payload.name, subject=payload.subject, preview_text=payload.preview_text, sender_id=sender.id,
                                             sender_email=sender.email, html=payload.html, list_ids=payload.list_ids),
                "status": DraftStatus.DRAFT, "approval_id": None, "approval_task_id": None, "last_error": None, "updated_at": utc_now(),
            })
            store.save_draft(draft)
            audit(context, "brevo.draft.updated", draft.id, repository=repository, metadata={"lists": len(draft.list_ids), "html_chars": len(draft.html)})
            return draft

    return draft_view(await asyncio.to_thread(save), detail=True)


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(draft_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Delete a draft from ANUM. A campaign already in Brevo stays there."""
    _write(context)
    with open_brevo_store(context) as store:
        draft = _get_or_404(store, draft_id, for_update=True)
        if draft.status not in DELETABLE:
            raise http_error(409, f"The campaign is {draft.status.value.replace('_', ' ')}; wait until it finishes before deleting it.")
        if draft.status == DraftStatus.PENDING_APPROVAL:
            retire_approval(repository, context, draft.approval_task_id, draft.approval_id)
        store.delete_draft(draft.id)
    audit(context, "brevo.draft.deleted", draft_id, repository=repository, metadata={"status": draft.status.value})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/drafts/{draft_id}/test", response_model=DraftView)
async def send_test(draft_id: str, context: TenantContext = Depends(tenant_context)) -> DraftView:
    """Send the draft to the Brevo account's own address only (no approval: it reaches no contact).

    The draft's Brevo campaign is created on the first test (as a Brevo draft, which sends
    nothing) and kept up to date; the approved send later updates and sends that same campaign."""
    _manage(context)
    refuse_send_in_demo(context)
    lock = _test_locks.setdefault(f"{context.tenant_id}/{context.workspace_id}/{draft_id}", asyncio.Lock())
    async with lock:
        def load() -> CampaignDraft:
            with open_brevo_store(context) as store:
                return _get_or_404(store, draft_id)

        draft = await asyncio.to_thread(load)
        if draft.status not in EDITABLE:
            raise http_error(409, f"The campaign is {draft.status.value.replace('_', ' ')}; a test is for drafts.")
        connection, client = await _connected_client(context)
        if not connection.account_email:
            raise http_error(409, "The Brevo account's own address is unknown. Check the connection first.")
        content = {"name": draft.name, "subject": draft.subject, "sender_id": draft.sender_id, "html": draft.html,
                   "list_ids": draft.list_ids, "tag": campaign_tag(draft.id), "preview_text": draft.preview_text}
        campaign_id = draft.brevo_campaign_id
        if campaign_id is None:
            campaign_id = await _brevo(context, client.create_campaign(**content))
            created = campaign_id

            def keep() -> None:
                # Recorded before the test is sent: a failed sendTest never orphans the campaign.
                with open_brevo_store(context) as store:
                    fresh = _get_or_404(store, draft_id, for_update=True)
                    if fresh.brevo_campaign_id is None:
                        fresh.brevo_campaign_id = created
                        store.save_draft(fresh)

            await asyncio.to_thread(keep)
        else:
            await _brevo(context, client.update_campaign(campaign_id, **content))
        await _brevo(context, client.send_test(campaign_id, connection.account_email))

        def save() -> CampaignDraft:
            with open_brevo_store(context) as store:
                fresh = _get_or_404(store, draft_id, for_update=True)
                if fresh.brevo_campaign_id is None:
                    fresh.brevo_campaign_id = campaign_id
                fresh.test_sent_at = utc_now()
                store.save_draft(fresh)
                return fresh

        saved = await asyncio.to_thread(save)
    await asyncio.to_thread(audit, context, "brevo.draft.test_sent", draft_id, metadata={"brevo_campaign_id": campaign_id, "to": "brevo_account_owner"})
    return draft_view(saved, detail=True)


@router.post("/drafts/{draft_id}/send", response_model=DraftView)
async def request_send(draft_id: str, payload: SendRequest, context: TenantContext = Depends(tenant_context)) -> DraftView:
    """Ask to send (or schedule) the draft: a paused ``brevo.send_campaign`` for an owner to approve."""
    _write(context)

    def load() -> CampaignDraft:
        with open_brevo_store(context) as store:
            return _get_or_404(store, draft_id)

    draft = await asyncio.to_thread(load)
    # A request that names no time reuses the draft's saved one (the planner's "tomorrow at 9");
    # an explicit null sends as soon as it is approved.
    when = payload.scheduled_at if "scheduled_at" in payload.model_fields_set else draft.scheduled_at
    if when is not None:
        when = when if when.tzinfo else when.replace(tzinfo=UTC)
        when = when.astimezone(UTC).replace(microsecond=0)
        if when < utc_now() + timedelta(minutes=10):
            raise http_error(422, "Pick a time at least 10 minutes from now, so there is time to approve it.")
        if when > utc_now() + timedelta(days=365):
            raise http_error(422, "Pick a time within the next year.")
    if draft.status not in {DraftStatus.DRAFT, DraftStatus.FAILED}:
        raise http_error(409, f"The campaign is {draft.status.value.replace('_', ' ')}; only a draft can be sent for approval.")
    connection, client = await _connected_client(context)
    # Shown to the approver: the lists as Brevo has them now, and how many people they hold.
    names: list[str] = []
    recipients = 0
    for list_id in draft.list_ids:
        try:
            found = await client.contact_list(list_id)
        except BrevoError as error:
            if error.status == 404:
                raise http_error(422, f"Contact list {list_id} no longer exists in Brevo. Edit the draft and pick its lists again.") from error
            raise brevo_failure(error) from error
        names.append(found.name)
        recipients += found.subscribers
    senders = await _brevo(context, client.senders())
    sender = next((item for item in senders if item.id == draft.sender_id), None)
    if sender is None or not sender.active or sender.email.lower() != draft.sender_email.lower():
        raise http_error(422, "The draft's sender is no longer a verified, active sender in Brevo. Edit the draft and pick a sender.")

    def ask() -> CampaignDraft:
        with _unit_of_work(context, require_membership=True) as repository, open_brevo_store(context) as store:
            fresh = _get_or_404(store, draft_id, for_update=True)
            if fresh.status not in {DraftStatus.DRAFT, DraftStatus.FAILED} or fresh.content_hash != draft.content_hash:
                raise http_error(409, "The draft changed while ANUM was checking it. Open it again and retry.")
            fresh.scheduled_at = when
            arguments = send_arguments(fresh, recipients=recipients, list_names=names, account_email=connection.account_email)
            task, approval = request_approval(repository, context, arguments, f"\"{fresh.name}\" to {', '.join(names)[:80]}", register_brevo_tools)
            fresh.status = DraftStatus.PENDING_APPROVAL
            fresh.approval_id = approval.id
            fresh.approval_task_id = task.id
            fresh.list_names = names
            fresh.last_error = None
            fresh.updated_at = utc_now()
            store.save_draft(fresh)
            audit(context, "brevo.campaign.send_requested", fresh.id, repository=repository,
                  metadata={"approval_id": approval.id, "content_hash": fresh.content_hash, "lists": len(fresh.list_ids),
                            "recipients": recipients, "scheduled": when is not None})
            return draft_view(fresh, approval, detail=True)  # type: ignore[return-value]

    return await asyncio.to_thread(ask)  # type: ignore[return-value]


@router.post("/drafts/{draft_id}/cancel", response_model=DraftView)
def cancel_send(draft_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> DraftView:
    """Withdraw a send request that waits for approval; the campaign goes back to draft."""
    _write(context)
    with open_brevo_store(context) as store:
        draft = _get_or_404(store, draft_id, for_update=True)
        if draft.status != DraftStatus.PENDING_APPROVAL:
            raise http_error(409, "Only a campaign waiting for approval can be withdrawn.")
        retire_approval(repository, context, draft.approval_task_id, draft.approval_id)
        draft.status = DraftStatus.DRAFT
        draft.approval_id = None
        draft.approval_task_id = None
        # The chosen time stays on the draft, so asking again reuses it.
        draft.updated_at = utc_now()
        store.save_draft(draft)
    audit(context, "brevo.campaign.send_withdrawn", draft_id, repository=repository)
    return draft_view(draft, detail=True)

