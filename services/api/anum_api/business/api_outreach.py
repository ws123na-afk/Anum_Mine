"""Routes of the outreach outbox under ``/api/v1/business/outreach`` (docs/business.md#outreach).

Queue an approved draft (the gate runs, then an approval bound to the payload hash is asked),
ask again for a due follow-up, cancel, write a follow-up draft, record a reply, opt-out or
bounce (stop-on-reply), and read the outbox and its metrics. Nothing here sends: the
``business.send_outreach`` tool does, after an owner approves and only while sending is on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query as Q
from pydantic import BaseModel, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import Approval, TenantContext, utc_now
from . import outreach, pipeline
from .common import Page, Reason, page_bounds, read, write
from .models import Draft
from .outreach_models import OPEN_OUTBOX, OutboxItem, OutboxStatus, ReplyEvent
from .service import http_error
from .store import Query, open_business_store

router = APIRouter(prefix="/api/v1/business/outreach", tags=["business"])


class OutboxView(OutboxItem):
    company: str = ""
    contact_name: str = ""
    lead_title: str = ""
    lead_state: str = ""
    due: bool = False
    approval: Approval | None = None


def _views(store: Any, repository: AnumRepository, context: TenantContext, items: list[OutboxItem]) -> list[OutboxView]:
    now = utc_now()
    out: list[OutboxView] = []
    for item in items:
        item, approval = outreach.sync_approval(store, repository, context, item)
        lead = store.get("leads", item.lead_id)
        account = store.get("accounts", lead.account_id) if lead else None
        contact = store.get("contacts", item.contact_id)
        out.append(OutboxView(
            **item.model_dump(), company=account.name if account else "", contact_name=contact.name if contact else "",
            lead_title=lead.title if lead else "", lead_state=lead.state.value if lead else "",
            due=item.status == OutboxStatus.SCHEDULED and (item.send_after is None or item.send_after <= now),
            approval=approval if item.status == OutboxStatus.PENDING_APPROVAL else None,
        ))
    return out


class OutreachOverview(BaseModel):
    sending_enabled: bool
    paused: bool
    paused_reason: str
    daily_quota: dict[str, int]
    sent_today: dict[str, int]
    waiting: dict[str, int]
    metrics: dict[str, Any]
    channels_connected: list[str]


@router.get("", response_model=OutreachOverview)
def overview(days: int = Q(30, ge=1, le=366), context: TenantContext = Depends(tenant_context)) -> OutreachOverview:
    read(context)
    from .. import delivery

    now = utc_now()
    with open_business_store(context) as store:
        settings = pipeline.load_settings(store, context)
        waiting = {status.value: store.count("outbox", Query(where={"status": status})) for status in OPEN_OUTBOX}
        sent = {channel: outreach.sent_today(store, channel, now) for channel in ("email", "whatsapp")}
        numbers = outreach.metrics(store, days, now)
    return OutreachOverview(sending_enabled=settings.sending_enabled, paused=settings.paused, paused_reason=settings.paused_reason,
                            daily_quota=settings.daily_quota, sent_today=sent, waiting=waiting, metrics=numbers,
                            channels_connected=sorted(channel for channel in delivery.channels() if channel in ("email", "whatsapp")))


@router.get("/items", response_model=Page[OutboxView])
def list_items(status: OutboxStatus | None = None, lead_id: str | None = None, limit: int = Q(50), offset: int = Q(0),
               context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Page[OutboxView]:
    read(context)
    limit, offset = page_bounds(limit, offset)
    where: dict[str, Any] = {}
    if status is not None:
        where["status"] = status
    if lead_id:
        where["lead_id"] = lead_id
    with open_business_store(context) as store:
        items, total = store.find("outbox", Query(where=where, limit=limit, offset=offset))
        views = _views(store, repository, context, items)
    return Page(items=views, total=total, limit=limit, offset=offset)


class ReadyDraft(BaseModel):
    draft: Draft
    lead_title: str
    company: str
    contact_name: str
    queued_for: list[str]


@router.get("/ready", response_model=Page[ReadyDraft])
def ready_drafts(limit: int = Q(50), offset: int = Q(0), context: TenantContext = Depends(tenant_context)) -> Page[ReadyDraft]:
    """Approved e-mail and WhatsApp drafts that are not in the outbox yet (the next thing to queue)."""
    read(context)
    limit, offset = page_bounds(limit, offset)
    with open_business_store(context) as store:
        drafts, _ = store.find("drafts", Query(where={"status": "approved", "channel": {"email", "whatsapp"}}, limit=500))
        ready: list[ReadyDraft] = []
        for draft in drafts:
            rows, _ = store.find("outbox", Query(where={"draft_id": draft.id, "status": set(OPEN_OUTBOX) | {OutboxStatus.SENT}}, limit=1))
            if rows:
                continue
            lead = store.get("leads", draft.lead_id)
            account = store.get("accounts", lead.account_id) if lead else None
            contact = store.get("contacts", draft.contact_id) if draft.contact_id else None
            earlier, _ = store.find("outbox", Query(where={"lead_id": draft.lead_id, "status": {OutboxStatus.HELD, OutboxStatus.SENT}}, limit=10))
            ready.append(ReadyDraft(draft=draft, lead_title=lead.title if lead else "", company=account.name if account else "",
                                    contact_name=contact.name if contact else "", queued_for=[item.id for item in earlier]))
    return Page(items=ready[offset:offset + limit], total=len(ready), limit=limit, offset=offset)


def _one(context: TenantContext, repository: AnumRepository, item_id: str) -> OutboxView:
    with open_business_store(context) as store:
        item = store.get("outbox", item_id)
        if item is None:
            raise http_error(404, "Outbox message not found")
        return _views(store, repository, context, [item])[0]


@router.get("/items/{item_id}", response_model=OutboxView)
def get_item(item_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> OutboxView:
    read(context)
    return _one(context, repository, item_id)


class QueueRequest(BaseModel):
    follow_up_of: str | None = None
    send_after_days: int | None = Field(default=None, ge=1, le=60)


@router.post("/drafts/{draft_id}/queue", response_model=OutboxView, status_code=201)
def queue(draft_id: str, payload: QueueRequest, context: TenantContext = Depends(tenant_context),
          repository: AnumRepository = Depends(repository_context)) -> OutboxView:
    """Run the pre-send gate on an approved draft and ask for an approval bound to its exact text (or schedule a follow-up)."""
    write(context)
    item = outreach.queue_draft(context, repository, draft_id, follow_up_of=payload.follow_up_of, send_after_days=payload.send_after_days)
    return _one(context, repository, item.id)


@router.post("/items/{item_id}/request-approval", response_model=OutboxView)
def request_approval(item_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> OutboxView:
    write(context)
    outreach.request_again(context, repository, item_id)
    return _one(context, repository, item_id)


@router.post("/items/{item_id}/cancel", response_model=OutboxView)
def cancel(item_id: str, payload: Reason, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> OutboxView:
    write(context)
    outreach.cancel_item(context, repository, item_id, payload.reason.strip())
    return _one(context, repository, item_id)


@router.post("/items/{item_id}/follow-up", response_model=Draft, status_code=201)
def follow_up(item_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Draft:
    """A follow-up draft for the next step; approve its text, then queue it with ``follow_up_of``."""
    write(context)
    return outreach.follow_up_draft(context, repository, item_id)


class ReplyCreate(BaseModel):
    channel: Literal["email", "whatsapp", "phone", "other"] = "email"
    kind: Literal["reply", "bounce"] = "reply"
    text: str = Field(default="", max_length=4000)


@router.post("/leads/{lead_id}/replies", response_model=ReplyEvent, status_code=201)
def record_reply(lead_id: str, payload: ReplyCreate, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> ReplyEvent:
    """A reply stops the lead's outreach; opt-out wording adds the contact to the opt-out list; a bounce counts toward the kill switch.
    The text is classified in code and never stored."""
    write(context)
    return outreach.record_reply(context, repository, lead_id, channel=payload.channel, text=payload.text, bounce=payload.kind == "bounce")


@router.get("/leads/{lead_id}/replies", response_model=list[ReplyEvent])
def list_replies(lead_id: str, context: TenantContext = Depends(tenant_context)) -> list[ReplyEvent]:
    read(context)
    with open_business_store(context) as store:
        if store.get("leads", lead_id) is None:
            raise http_error(404, "Lead not found")
        items, _ = store.find("replies", Query(where={"lead_id": lead_id}, limit=100, order_by="created_at"))
    return items


class PauseRequest(BaseModel):
    on: bool
    reason: str = Field(default="", max_length=300)


class PauseView(BaseModel):
    paused: bool
    paused_reason: str
    paused_at: datetime | None


@router.post("/pause", response_model=PauseView)
def set_pause(payload: PauseRequest, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> PauseView:
    """The kill switch. Members can pause; only owners resume (the bounce switch trips it on its own)."""
    write(context)
    if not payload.on:
        from .common import manage

        manage(context)
    elif len(payload.reason.strip()) < 3:
        raise http_error(422, "Say why outreach is paused (at least 3 characters).")
    settings = outreach.pause(context, repository, payload.on, payload.reason)
    return PauseView(paused=settings.paused, paused_reason=settings.paused_reason, paused_at=settings.paused_at)
