"""Live presence and workstation work panel for the ANUM MINE office.

* ``GET /api/v1/office/presence``: snapshot of every visible agent's state, derived on the
  server from run, task, approval and meeting records (office_presence.py).
* ``GET /api/v1/office/presence/stream``: Server-Sent Events. The first frame is always a
  full ``snapshot`` (also after a reconnect with ``Last-Event-ID``), later frames are
  ``delta`` frames with only the changed items. Events from the existing domain event log
  only trigger a re-read; the state itself always comes from the records, so duplicate or
  out-of-order events cannot change it. No database session is held between polls.
* ``GET /api/v1/office/workstations/{desk_id}/work``: the real tasks, run checkpoints,
  errors and linked documents behind a desk's agent. Nothing is fabricated.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from .authorization import Permission
from .dependencies import has_permission, list_events_for_stream, require_permission, tenant_context
from .office_presence import (
    derive_presence, diff_presence, is_relevant_event, load_evidence, totals,
)
from .office_scope import require_postgres, workspace_session
from .schemas import TenantContext, utc_now

router = APIRouter(prefix="/api/v1/office", tags=["office presence"])
PRESENCE_VERSION = 1
POLL_SECONDS = 1.0
REFRESH_SECONDS = 10.0
KEEPALIVE_TICKS = 15
SEEN_EVENT_LIMIT = 1000
DeskId = Annotated[str, Path(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


def presence_snapshot(context: TenantContext) -> dict[str, Any]:
    """Blocking: one short session, scoped to this workspace and user."""
    now = utc_now()
    with workspace_session(context) as session:
        evidence = load_evidence(session, context, now)
    items = derive_presence(evidence)
    return {
        "version": PRESENCE_VERSION,
        "workspace_id": context.workspace_id,
        "as_of": now.isoformat(),
        "cursor": evidence.cursor,
        "totals": totals(items),
        "items": items,
    }


@router.get("/presence")
def get_presence(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    require_postgres()
    return presence_snapshot(context)


def _frame(event: str, data: dict[str, Any], event_id: str | None) -> str:
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def presence_frames(
    *,
    load_snapshot: Callable[[], dict[str, Any]],
    poll_events: Callable[[str | None], Sequence[Any]],
    is_disconnected: Callable[[], Awaitable[bool]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    last_event_id: str | None = None,
    tick: float = POLL_SECONDS,
    refresh_every: float = REFRESH_SECONDS,
) -> AsyncIterator[str]:
    """SSE frames: a snapshot, then deltas. Blocking readers run in worker threads and
    each opens and closes its own database session."""
    snapshot = await asyncio.to_thread(load_snapshot)
    cursor = snapshot["cursor"] or last_event_id
    known = {item["key"]: item for item in snapshot["items"]}
    sequence = 0
    yield _frame("snapshot", {**snapshot, "sequence": sequence}, cursor)
    seen: dict[str, None] = {}
    idle_ticks = 0
    since_refresh = 0.0
    while not await is_disconnected():
        await sleep(tick)
        since_refresh += tick
        events = await asyncio.to_thread(poll_events, cursor)
        new_relevant = False
        for event in events:
            cursor = event.id
            if event.id in seen:
                continue  # a duplicate delivery changes nothing
            seen[event.id] = None
            if len(seen) > SEEN_EVENT_LIMIT:
                seen.pop(next(iter(seen)))
            if is_relevant_event(event.type):
                new_relevant = True
        if not new_relevant and since_refresh < refresh_every:
            idle_ticks += 1
            if idle_ticks >= KEEPALIVE_TICKS:
                idle_ticks = 0
                yield ": keep-alive\n\n"
            continue
        since_refresh = 0.0
        idle_ticks = 0
        current = await asyncio.to_thread(load_snapshot)
        changed, removed = diff_presence(known, current["items"])
        known = {item["key"]: item for item in current["items"]}
        if changed or removed:
            sequence += 1
            yield _frame("delta", {
                "version": PRESENCE_VERSION, "sequence": sequence, "as_of": current["as_of"],
                "totals": current["totals"], "changed": changed, "removed": removed,
            }, cursor)


@router.get("/presence/stream")
async def stream_presence(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID", max_length=120),
    context: TenantContext = Depends(tenant_context),
) -> StreamingResponse:
    require_permission(context, Permission.TASK_READ)
    require_permission(context, Permission.EVENT_READ)
    require_postgres()
    return StreamingResponse(
        presence_frames(
            load_snapshot=lambda: presence_snapshot(context),
            poll_events=lambda cursor: list_events_for_stream(context, cursor),
            is_disconnected=request.is_disconnected,
            last_event_id=last_event_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _short(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"


@router.get("/workstations/{desk_id}/work")
def workstation_work(desk_id: DeskId, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    require_postgres()
    now = utc_now()
    can_approvals = has_permission(context, Permission.APPROVAL_READ)
    can_files = has_permission(context, Permission.FILE_READ)
    p = {"t": context.tenant_id, "w": context.workspace_id}
    with workspace_session(context) as session:
        desk = session.execute(text(
            "select id, room_id, label, agent_id from office_desks "
            "where tenant_id = :t and workspace_id = :w and id = :d"), {**p, "d": desk_id}).first()
        if desk is None:
            raise HTTPException(404, "Workstation not found")
        evidence = load_evidence(session, context, now)
        visible_agent = next((a for a in evidence.agents if a.agent_id == desk.agent_id), None) if desk.agent_id else None
        base = {
            "version": PRESENCE_VERSION,
            "workspace_id": context.workspace_id,
            "as_of": now.isoformat(),
            "desk": {"id": desk.id, "room_id": desk.room_id, "label": desk.label},
        }
        if desk.agent_id is None:
            return {**base, "agent": None, "presence": None, "reason": "unassigned",
                    "tasks": [], "approvals": [], "errors": [], "documents": []}
        if visible_agent is None:
            return {**base, "agent": None, "presence": None, "reason": "agent_not_visible",
                    "tasks": [], "approvals": [], "errors": [], "documents": []}
        presence = next((i for i in derive_presence(evidence) if i["agent_id"] == visible_agent.agent_id), None)
        task_rows = session.execute(text(
            "select id, title, status, priority, due_at, created_at, updated_at from tasks "
            "where tenant_id = :t and workspace_id = :w and agent_id = :a and archived_at is null "
            "order by updated_at desc, id desc limit 10"), {**p, "a": visible_agent.agent_id}).all()
        task_ids = [r.id for r in task_rows]
        runs: dict[str, Any] = {}
        steps: dict[str, list[dict[str, Any]]] = {}
        file_ids: dict[str, None] = {}
        if task_ids:
            for r in session.execute(text(
                "select distinct on (task_id) id, task_id, status, result, created_at, updated_at, "
                "checkpoint->>'phase' as phase, (checkpoint->>'version')::int as version, "
                "checkpoint->>'last_step_id' as last_step_id, checkpoint->>'approval_id' as approval_id "
                "from agent_runs where tenant_id = :t and workspace_id = :w and task_id = any(:ids) "
                "order by task_id, created_at desc, id desc"), {**p, "ids": task_ids}):
                runs[r.task_id] = r
            run_ids = [r.id for r in runs.values()]
            if run_ids:
                for s in session.execute(text(
                    "select id, run_id, type, summary, created_at, step_metadata from ("
                    "  select *, row_number() over (partition by run_id order by created_at desc, id desc) as rn "
                    "  from agent_run_steps where tenant_id = :t and workspace_id = :w and run_id = any(:ids)"
                    ") x where rn <= 15 order by run_id, created_at, id"), {**p, "ids": run_ids}):
                    steps.setdefault(s.run_id, []).append({
                        "id": s.id, "type": s.type, "summary": _short(s.summary, 300),
                        "created_at": s.created_at.isoformat(),
                    })
                    if s.type == "retrieval" and can_files:
                        for source in (s.step_metadata or {}).get("sources", []) or []:
                            if source.get("source_type") == "file" and source.get("source_id"):
                                file_ids[str(source["source_id"])] = None
        tasks: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for t in task_rows:
            run = runs.get(t.id)
            tasks.append({
                "id": t.id, "title": _short(t.title, 160), "status": t.status, "priority": t.priority or "medium",
                "due_at": t.due_at.isoformat() if t.due_at else None,
                "created_at": t.created_at.isoformat(), "updated_at": t.updated_at.isoformat(),
                "links": {"task": f"/api/v1/tasks/{t.id}", "runs": f"/api/v1/tasks/{t.id}/runs"},
                "run": None if run is None else {
                    "id": run.id, "status": run.status, "phase": run.phase, "checkpoint_version": run.version,
                    "last_step_id": run.last_step_id, "approval_id": run.approval_id,
                    "updated_at": run.updated_at.isoformat(), "steps": steps.get(run.id, []),
                },
            })
            if run is not None and run.status == "failed":
                errors.append({
                    "task_id": t.id, "run_id": run.id, "at": run.updated_at.isoformat(),
                    "message": _short(run.result, 500) or "The run failed without a recorded reason.",
                })
        approvals: list[dict[str, Any]] = []
        if can_approvals and task_ids:
            for a in session.execute(text(
                "select id, task_id, action, risk_level, status, expires_at, created_at from approvals "
                "where tenant_id = :t and workspace_id = :w and status = 'pending' and task_id = any(:ids) "
                "order by created_at desc, id desc limit 20"), {**p, "ids": task_ids}):
                approvals.append({
                    "id": a.id, "task_id": a.task_id, "action": _short(a.action, 160), "risk_level": a.risk_level,
                    "expires_at": a.expires_at.isoformat() if a.expires_at else None,
                    "expired": bool(a.expires_at and a.expires_at <= now),
                    "created_at": a.created_at.isoformat(),
                })
        documents: list[dict[str, Any]] = []
        if file_ids:
            for f in session.execute(text(
                "select id, name, content_type, size_bytes, scan_status from workspace_files "
                "where tenant_id = :t and workspace_id = :w and id = any(:ids) "
                "and coalesce(scan_status, 'clean') <> 'infected' order by name, id"),
                    {**p, "ids": list(file_ids)}):
                documents.append({"id": f.id, "name": _short(f.name, 255), "content_type": f.content_type,
                                  "size_bytes": f.size_bytes, "link": f"/api/v1/files/{f.id}"})
    return {
        **base,
        "agent": {"id": visible_agent.agent_id, "name": visible_agent.name, "status": visible_agent.status},
        "presence": presence,
        "reason": None,
        "tasks": tasks,
        "approvals": approvals,
        "errors": errors,
        "documents": documents,
    }
