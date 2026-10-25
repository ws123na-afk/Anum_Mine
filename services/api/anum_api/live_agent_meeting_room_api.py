"""Meeting room HTTP API: bundle, replayable events and SSE, presence, hand, messages, agenda,
draft outputs, files, export and transcript delete (F06/F07/F08).

Reads need ``task:read``, but transcript text (messages, the event log and its stream, decisions and
action items, the export) is for workspace owners only (S-03): everyone else gets 403 with a clear
message, and the room bundle they can read carries no text. Every write needs ``workspace:manage`` except presence and raising
a hand (``task:read``, the viewer must have joined). Moderation is owner-only. Model turns
are still produced only by ``execute-turn``/``run``/the worker; nothing here calls a model.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import live_agent_meeting_room as room
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .live_agent_meeting_api import MeetingId, _audit, _error, _store, can_read_transcript, require_transcript_reader
from .live_agent_meeting_store import LiveMeetingStoreError
from .schemas import TenantContext
from .sse import SSE_HEADERS, sse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agent-meetings", tags=["agent meeting room"])

KEY = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
STREAM_MAX_SECONDS = 240
POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 15


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Presence(Body):
    action: Literal["join", "leave"]


class Hand(Body):
    action: Literal["raise", "lower"]


class Message(Body):
    request_key: str = Field(min_length=8, max_length=120, pattern=KEY)
    body: str = Field(min_length=1, max_length=4000)


class AgendaCreate(Body):
    title: str = Field(min_length=1, max_length=200)


class AgendaPatch(Body):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    state: Literal["open", "current", "done"] | None = None


class OutputCreate(Body):
    kind: Literal["decision", "action_item"]
    body: str = Field(min_length=1, max_length=2000)
    owner_label: str | None = Field(default=None, max_length=160)
    source_sequences: list[int] = Field(default_factory=list, max_length=20)


class OutputPatch(Body):
    state: Literal["confirmed", "dismissed", "draft"] | None = None
    body: str | None = Field(default=None, min_length=1, max_length=2000)
    owner_label: str | None = Field(default=None, max_length=160)


class FileLink(Body):
    file_id: str = Field(min_length=1, max_length=80, pattern=KEY)


def _read(context: TenantContext) -> None:
    require_permission(context, Permission.TASK_READ)


def _write(context: TenantContext) -> None:
    require_permission(context, Permission.WORKSPACE_MANAGE)


def _run(context: TenantContext, action: str, meeting_id: str, fn: Callable[[Any], Any]) -> Any:
    with _store(context) as store:
        try:
            result = fn(store)
            if action:
                _audit(store, context, f"agent_meeting.{action}", meeting_id)
            return result
        except LiveMeetingStoreError as exc:
            raise _error(exc) from exc


@router.get("/{meeting_id}/room")
def get_room(meeting_id: MeetingId, context: TenantContext = Depends(tenant_context)) -> dict:
    _read(context)
    bundle = _run(context, "", meeting_id, lambda s: room.room(s, context, meeting_id))
    return bundle_for(bundle, can_read_transcript(context))


def bundle_for(bundle: dict, owner: bool) -> dict:
    """The room bundle a caller may see. Non-owners keep who, when and status, and lose the text:
    decisions and action items (summaries of the discussion) and linked file names become counts."""
    bundle["viewer"] = {**bundle["viewer"], "can_read_transcript": owner}
    if owner:
        return bundle
    return {**bundle, "outputs": [], "outputs_count": len(bundle["outputs"]),
            "files": [], "files_count": len(bundle["files"]), "transcript_hidden": True}


@router.get("/{meeting_id}/events")
def list_events(meeting_id: MeetingId, after: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=500),
                context: TenantContext = Depends(tenant_context)) -> dict:
    """Plain JSON replay of the durable event log, for finished meetings and tests. Owners only."""
    _read(context)
    require_transcript_reader(context, meeting_id, "events")

    def read(store: Any) -> dict:
        if store.get(context, meeting_id) is None:
            raise LiveMeetingStoreError("not_found", "Meeting not found")
        items = room.events_after(store, context, meeting_id, after, limit)
        return {"items": items, "next_after": items[-1]["seq"] if len(items) == limit else None}
    return _run(context, "", meeting_id, read)


def frame(event: dict[str, Any]) -> str:
    """One SSE frame whose id is the event number, so Last-Event-ID resumes exactly."""
    return f"id: {event['seq']}\n" + sse(event["kind"], {"seq": event["seq"], "kind": event["kind"], "actor_id": event["actor_id"],
                                                         "payload": event["payload"], "created_at": event["created_at"]})


def parse_last_event_id(value: str | None, after: int) -> int:
    try:
        return max(0, int(value)) if value not in (None, "") else after
    except ValueError:
        return after


async def event_source(
    poll: Callable[[int], tuple[list[dict[str, Any]], bool]], start: int, *, is_disconnected: Callable[[], Any],
    max_seconds: float = STREAM_MAX_SECONDS, poll_seconds: float = POLL_SECONDS,
    heartbeat_seconds: float = HEARTBEAT_SECONDS, clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[str]:
    """Replay from ``start`` then follow. ``poll(after)`` returns (events, meeting_is_over).

    Each poll uses its own short database session in a thread (a stream outlives the
    request's dependencies). The stream ends after ``max_seconds`` or once the meeting is
    over and drained; the client reconnects with Last-Event-ID, so nothing is lost or repeated.
    """
    after = start
    began = last_beat = clock()
    yield "retry: 3000\n\n"
    while True:
        if await is_disconnected():
            return
        events, over = await asyncio.to_thread(poll, after)
        for event in events:
            after = event["seq"]
            yield frame(event)
            last_beat = clock()
        if over and not events:
            yield sse("end", {"seq": after})
            return
        if clock() - began >= max_seconds:
            yield sse("reconnect", {"seq": after})
            return
        if not events and clock() - last_beat >= heartbeat_seconds:
            yield ": keep-alive\n\n"
            last_beat = clock()
        if not events:
            await asyncio.sleep(poll_seconds)


@router.get("/{meeting_id}/events/stream")
async def stream_events(request: Request, meeting_id: MeetingId, after: int = Query(default=0, ge=0),
                        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
                        context: TenantContext = Depends(tenant_context, scope="function")) -> StreamingResponse:
    _read(context)
    require_transcript_reader(context, meeting_id, "stream")
    start = parse_last_event_id(last_event_id, after)
    # Check access once before streaming so a wrong id is a clean 404, not an empty stream.
    _run(context, "", meeting_id, lambda s: room._meeting(s, context, meeting_id))

    def poll(cursor: int) -> tuple[list[dict[str, Any]], bool]:
        with _store(context) as store:
            meeting = store.get(context, meeting_id)
            if meeting is None:
                return [], True
            return (room.events_after(store, context, meeting_id, cursor, 100),
                    meeting["status"] in room.TERMINAL)
    return StreamingResponse(event_source(poll, start, is_disconnected=request.is_disconnected),
                             media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/{meeting_id}/presence")
def set_presence(meeting_id: MeetingId, body: Presence, context: TenantContext = Depends(tenant_context)) -> dict:
    _read(context)
    return _run(context, "presence", meeting_id, lambda s: room.presence(s, context, meeting_id, body.action))


@router.post("/{meeting_id}/hand")
def set_hand(meeting_id: MeetingId, body: Hand, context: TenantContext = Depends(tenant_context)) -> dict:
    _read(context)
    if body.action == "lower":
        _write(context)
    return _run(context, "hand_" + body.action, meeting_id, lambda s: room.hand(s, context, meeting_id, body.action))


@router.post("/{meeting_id}/messages", status_code=201)
def send_message(meeting_id: MeetingId, body: Message, context: TenantContext = Depends(tenant_context)) -> dict:
    _write(context)
    return _run(context, "message", meeting_id, lambda s: room.post_message(s, context, meeting_id, body.request_key, body.body))


@router.post("/{meeting_id}/agenda", status_code=201)
def add_agenda(meeting_id: MeetingId, body: AgendaCreate, context: TenantContext = Depends(tenant_context)) -> dict:
    _write(context)
    return _run(context, "agenda_add", meeting_id, lambda s: room.agenda_add(s, context, meeting_id, body.title))


@router.patch("/{meeting_id}/agenda/{item_id}")
def patch_agenda(meeting_id: MeetingId, item_id: MeetingId, body: AgendaPatch, context: TenantContext = Depends(tenant_context)) -> dict:
    _write(context)
    return _run(context, "agenda_update", meeting_id,
                lambda s: room.agenda_update(s, context, meeting_id, item_id, title=body.title, state=body.state))


@router.delete("/{meeting_id}/agenda/{item_id}", status_code=204)
def delete_agenda(meeting_id: MeetingId, item_id: MeetingId, context: TenantContext = Depends(tenant_context)) -> None:
    _write(context)
    _run(context, "agenda_delete", meeting_id, lambda s: room.agenda_delete(s, context, meeting_id, item_id))


@router.post("/{meeting_id}/outputs", status_code=201)
def create_output(meeting_id: MeetingId, body: OutputCreate, context: TenantContext = Depends(tenant_context)) -> dict:
    _write(context)
    return _run(context, "output_draft", meeting_id, lambda s: room.output_create(
        s, context, meeting_id, kind=body.kind, body=body.body, owner_label=body.owner_label, source_sequences=body.source_sequences))


@router.patch("/{meeting_id}/outputs/{output_id}")
def patch_output(meeting_id: MeetingId, output_id: MeetingId, body: OutputPatch, context: TenantContext = Depends(tenant_context)) -> dict:
    _write(context)
    return _run(context, "output_decide", meeting_id, lambda s: room.output_decide(
        s, context, meeting_id, output_id, state=body.state, body=body.body, owner_label=body.owner_label))


@router.post("/{meeting_id}/outputs/{output_id}/task", status_code=201)
def output_to_task(meeting_id: MeetingId, output_id: MeetingId, context: TenantContext = Depends(tenant_context)) -> dict:
    """Turn a confirmed action item into a workspace task through the existing governed task path.

    The task is only created (status ``created``); running it follows the normal approval
    policy. Needs the ``task:create`` permission like the imported-meeting action items.
    """
    _write(context)
    if not has_permission(context, Permission.TASK_CREATE):
        raise HTTPException(403, "Creating tasks needs the owner or member role in this workspace.")
    from .meetings import _create_task
    with _store(context) as store:
        try:
            item = room.output_for_task(store, context, meeting_id, output_id)
            lines = [f'Action item from the live meeting "{item["meeting_title"]}".',
                     f"Owner: {item['owner_label']}" if item["owner_label"] else "Owner: not assigned",
                     "Source messages: " + ", ".join("#" + str(n) for n in item["source_sequences"]) if item["source_sequences"] else "",
                     "", item["body"]]
            task_id = _create_task(context, item["body"], "\n".join(line for line in lines if line is not None))
            updated = room.output_attach_task(store, context, meeting_id, output_id, task_id)
            _audit(store, context, "agent_meeting.output_task", meeting_id)
            return updated
        except LiveMeetingStoreError as exc:
            raise _error(exc) from exc


@router.post("/{meeting_id}/files", status_code=201)
def link_file(meeting_id: MeetingId, body: FileLink, context: TenantContext = Depends(tenant_context)) -> dict:
    _write(context)
    require_permission(context, Permission.FILE_READ)
    return _run(context, "file_link", meeting_id, lambda s: room.file_link(s, context, meeting_id, body.file_id))


@router.delete("/{meeting_id}/files/{file_id}", status_code=204)
def unlink_file(meeting_id: MeetingId, file_id: MeetingId, context: TenantContext = Depends(tenant_context)) -> None:
    _write(context)
    _run(context, "file_unlink", meeting_id, lambda s: room.file_unlink(s, context, meeting_id, file_id))


@router.get("/{meeting_id}/export")
def export_meeting(meeting_id: MeetingId, format: Literal["markdown", "json"] = "markdown",
                   context: TenantContext = Depends(tenant_context)):
    """Owner download of the stored (already redacted) transcript, agenda and outputs."""
    require_transcript_reader(context, meeting_id, "export")
    _write(context)

    def build(store: Any) -> dict:
        bundle = room.room(store, context, meeting_id)
        return {"bundle": bundle, "turns": room.transcript(store, context, meeting_id)}
    data = _run(context, "export", meeting_id, build)
    if format == "json":
        payload = {k: data["bundle"][k] for k in ("meeting", "participants", "agenda", "outputs", "files")}
        payload["turns"] = data["turns"]
        return PlainTextResponse(json.dumps(payload, default=str, ensure_ascii=False), media_type="application/json",
                                 headers={"Content-Disposition": f'attachment; filename="meeting-{meeting_id}.json"'})
    return PlainTextResponse(room.export_markdown(data["bundle"], data["turns"]), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="meeting-{meeting_id}.md"'})


@router.delete("/{meeting_id}/transcript")
def delete_transcript(meeting_id: MeetingId, context: TenantContext = Depends(tenant_context)) -> dict:
    """Delete the transcript and event log of an ended meeting. Totals, confirmed outputs and tasks stay."""
    _write(context)
    return _run(context, "transcript_delete", meeting_id, lambda s: room.delete_transcript(s, context, meeting_id))
