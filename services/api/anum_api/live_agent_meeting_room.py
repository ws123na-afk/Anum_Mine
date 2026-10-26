"""Meeting room persistence: agenda, draft outputs, attendance, files, messages, export (F06/F07/F08).

Every function takes the scoped store of the caller and runs in its transaction. A
message is always written before the event that announces it. Outputs (decisions and
action items) stay drafts until the meeting owner confirms them; an action item becomes
a workspace task only through the existing governed task path (meetings._create_task),
never through a model.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from .live_agent_meeting_redaction import redact_secrets
from .live_agent_meeting_store import LiveMeetingStoreError, SqlLiveMeetingStore
from .schemas import TenantContext, new_id

SPEAKING_WINDOW = timedelta(seconds=150)  # lease is 90 s and the model call 75 s
TERMINAL = ("finished", "cancelled", "failed")
OUTPUT_COLUMNS = ("id, session_id, kind, body, state, owner_label, source_sequences, task_id, created_by, decided_by, "
                  "origin, drafted_with, created_at, updated_at")
WORK_KINDS = ("task", "approval", "run")
LINK_COLUMNS = "id, session_id, output_id, target_kind, target_id, created_by, created_at"


def _meeting(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, *, lock: bool = False) -> dict[str, Any]:
    row = store.get(ctx, meeting_id, lock=lock)
    if row is None:
        raise LiveMeetingStoreError("not_found", "Meeting not found")
    return dict(row)


def require_owner(meeting: dict[str, Any], ctx: TenantContext, what: str) -> None:
    if meeting["created_by"] != ctx.user_id:
        raise LiveMeetingStoreError("forbidden", f"Only the meeting owner can {what}")


def _rows(store: SqlLiveMeetingStore, sql: str, ctx: TenantContext, meeting_id: str, **extra: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in store.session.execute(text(sql), store.args(ctx, id=meeting_id, **extra)).mappings().all()]


# ----------------------------------------------------------------------------- room bundle

def speaking_now(events: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    """Agents whose turn is really in flight: a turn_started with no message or failure after it.

    Pure: unit tested. A start older than the lease window is ignored (a crashed worker).
    """
    now = now or datetime.now(timezone.utc)
    open_starts: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = event.get("payload") or {}
        if event["kind"] == "turn_started":
            open_starts[str(payload.get("request_key") or payload.get("sequence"))] = event
        elif event["kind"] in ("message", "turn_failed"):
            key = payload.get("request_key")
            if key is not None:
                open_starts.pop(str(key), None)
            else:  # turn_failed carries the claimed sequence
                for k, v in list(open_starts.items()):
                    if (v.get("payload") or {}).get("sequence") == payload.get("sequence"):
                        open_starts.pop(k, None)
        elif event["kind"] == "state" and (payload.get("status") in TERMINAL or payload.get("status") == "paused"):
            open_starts.clear()
    out = []
    for event in open_starts.values():
        created = event["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if now - created <= SPEAKING_WINDOW:
            out.append({"agent_id": (event.get("payload") or {}).get("agent_id"), "sequence": (event.get("payload") or {}).get("sequence"),
                        "since": created.isoformat()})
    return out


def _with_hash(output: dict[str, Any]) -> dict[str, Any]:
    """The hash of what the owner is shown (id, kind, text, state); confirming a model draft sends it back."""
    from .live_agent_meeting_summary import content_hash
    return {**output, "payload_hash": content_hash(output)}


def room(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str) -> dict[str, Any]:
    meeting = _meeting(store, ctx, meeting_id)
    recent = _rows(store, """
        select seq, kind, actor_id, payload, created_at from agent_meeting_events
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and seq > (
          select coalesce(max(seq),0)-40 from agent_meeting_events
          where tenant_id=:tenant and workspace_id=:workspace and session_id=:id)
        order by seq""", ctx, meeting_id)
    last = _rows(store, """select coalesce(max(seq),0) as last from agent_meeting_events
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id""", ctx, meeting_id)[0]["last"]
    files = _rows(store, """
        select f.id, f.file_id, f.added_by, f.created_at, w.name, w.content_type
        from agent_meeting_files f left join workspace_files w
          on w.tenant_id=f.tenant_id and w.workspace_id=f.workspace_id and w.id=f.file_id
        where f.tenant_id=:tenant and f.workspace_id=:workspace and f.session_id=:id order by f.created_at""", ctx, meeting_id)
    return {
        "meeting": meeting,
        "participants": store.participants(ctx, meeting_id),
        "attendance": _rows(store, """select actor_id, joined_at, left_at from agent_meeting_attendance
            where tenant_id=:tenant and workspace_id=:workspace and session_id=:id order by joined_at""", ctx, meeting_id),
        "agenda": _rows(store, """select id, position, title, state from agent_meeting_agenda
            where tenant_id=:tenant and workspace_id=:workspace and session_id=:id order by position""", ctx, meeting_id),
        "outputs": [_with_hash(o) for o in _rows(store, f"select {OUTPUT_COLUMNS} from agent_meeting_outputs "  # nosec B608: fixed column list
            "where tenant_id=:tenant and workspace_id=:workspace and session_id=:id order by created_at, id", ctx, meeting_id)],
        "links": links_for_meeting(store, ctx, meeting_id),
        "files": files,
        "speaking": speaking_now(recent) if meeting["status"] == "active" else [],
        "last_event_seq": int(last),
        "transcript_deleted": meeting.get("transcript_deleted_at") is not None,
        "viewer": {"user_id": ctx.user_id, "is_owner": meeting["created_by"] == ctx.user_id},
    }


# ----------------------------------------------------------------------------- events

def events_after(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, after: int, limit: int = 200) -> list[dict[str, Any]]:
    return _rows(store, """select seq, kind, actor_id, payload, created_at from agent_meeting_events
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and seq > :after
        order by seq limit :limit""", ctx, meeting_id, after=after, limit=limit)


def transcript(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    after = 0
    while True:
        page = store.turns(ctx, meeting_id, after=after, limit=100)
        out.extend(page)
        if len(page) < 100:
            return out
        after = page[-1]["sequence"]


# ----------------------------------------------------------------------------- attendance, hand, messages

def presence(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, action: str) -> dict[str, Any]:
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    if meeting["status"] in TERMINAL:
        raise LiveMeetingStoreError("conflict", "This meeting has ended")
    if action == "join":
        joined = store.session.execute(text("""
            insert into agent_meeting_attendance (id,tenant_id,workspace_id,session_id,actor_id)
            values (:row,:tenant,:workspace,:id,:actor)
            on conflict (tenant_id,workspace_id,session_id,actor_id) do update set left_at=null, joined_at=now()
              where agent_meeting_attendance.left_at is not null returning id"""),
            store.args(ctx, id=meeting_id, row=new_id("attendance"), actor=ctx.user_id)).first()
        if joined is not None:  # a repeat join of a present viewer is not an event
            store.emit(ctx, meeting_id, "joined", {"actor_id": ctx.user_id}, actor=ctx.user_id)
    else:
        updated = store.session.execute(text("""
            update agent_meeting_attendance set left_at=now()
            where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and actor_id=:actor and left_at is null
            returning id"""), store.args(ctx, id=meeting_id, actor=ctx.user_id)).first()
        if updated is not None:
            store.emit(ctx, meeting_id, "left", {"actor_id": ctx.user_id}, actor=ctx.user_id)
    return {"ok": True, "action": action}


def hand(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, action: str) -> dict[str, Any]:
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    if meeting["status"] not in ("active", "paused"):
        raise LiveMeetingStoreError("conflict", "Hands can only be raised in a running meeting")
    if action == "raise":
        present = store.session.execute(text("""select 1 from agent_meeting_attendance
            where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and actor_id=:actor and left_at is null"""),
            store.args(ctx, id=meeting_id, actor=ctx.user_id)).first()
        if present is None:
            raise LiveMeetingStoreError("forbidden", "Join the meeting before raising a hand")
        if meeting.get("hand_raised_by"):
            return {"hand_raised_by": meeting["hand_raised_by"]}
        store.session.execute(text("""update agent_meeting_sessions set hand_raised_by=:actor, hand_raised_at=now()
            where tenant_id=:tenant and workspace_id=:workspace and id=:id"""), store.args(ctx, id=meeting_id, actor=ctx.user_id))
        store.emit(ctx, meeting_id, "hand_raised", {"actor_id": ctx.user_id}, actor=ctx.user_id)
        return {"hand_raised_by": ctx.user_id}
    require_owner(meeting, ctx, "lower a hand")
    if meeting.get("hand_raised_by"):
        _clear_hand(store, ctx, meeting_id)
    return {"hand_raised_by": None}


def _clear_hand(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str) -> None:
    store.session.execute(text("""update agent_meeting_sessions set hand_raised_by=null, hand_raised_at=null
        where tenant_id=:tenant and workspace_id=:workspace and id=:id"""), store.args(ctx, id=meeting_id))
    store.emit(ctx, meeting_id, "hand_lowered", {}, actor=ctx.user_id)


def post_message(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, request_key: str, body: str) -> dict[str, Any]:
    """The owner speaks (an interrupt). Idempotent per request key; numbered under the row lock."""
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    require_owner(meeting, ctx, "speak in this meeting")
    existing = store.session.execute(text("""select sequence,speaker_id,role,body,request_key,created_at,redacted
        from agent_meeting_turns where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and request_key=:key"""),
        store.args(ctx, id=meeting_id, key=request_key)).mappings().first()
    if existing is not None:
        return dict(existing)
    if meeting["status"] not in ("active", "paused"):
        raise LiveMeetingStoreError("conflict", "Messages can only be sent while the meeting is running or paused")
    clean, was_redacted = redact_secrets(body.strip())
    if not clean:
        raise LiveMeetingStoreError("conflict", "Message is empty")
    seq = int(store.session.execute(text("""select coalesce(max(sequence),0)+1 from agent_meeting_turns
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id"""), store.args(ctx, id=meeting_id)).scalar_one())
    if seq > meeting["max_turns"]:
        raise LiveMeetingStoreError("conflict", "The meeting turn limit is reached")
    humans = [p for p in store.participants(ctx, meeting_id) if p["actor_id"] == ctx.user_id]
    if not humans:
        count = len(store.participants(ctx, meeting_id))
        if count >= meeting["max_participants"]:
            raise LiveMeetingStoreError("conflict", "Participant limit reached")
        store.session.execute(text("""insert into agent_meeting_participants
            (id,tenant_id,workspace_id,session_id,kind,actor_id,added_by) values (:row,:tenant,:workspace,:id,'human',:actor,:actor)"""),
            store.args(ctx, id=meeting_id, row=new_id("participant"), actor=ctx.user_id))
    row = store.session.execute(text("""insert into agent_meeting_turns
        (id,tenant_id,workspace_id,session_id,sequence,speaker_id,role,body,request_key,provenance,redacted)
        values (:turn,:tenant,:workspace,:id,:seq,:actor,'human',:body,:key,'human',:redacted)
        returning sequence,speaker_id,role,body,request_key,created_at,redacted"""),
        store.args(ctx, id=meeting_id, turn=new_id("meeting_turn"), seq=seq, actor=ctx.user_id, body=clean,
                   key=request_key, redacted=was_redacted)).mappings().one()
    store.emit(ctx, meeting_id, "message", {
        "sequence": row["sequence"], "speaker_id": row["speaker_id"], "role": "human", "body": row["body"],
        "request_key": request_key, "created_at": row["created_at"].isoformat(), "redacted": bool(row["redacted"]),
        "provenance": "human"}, actor=ctx.user_id)
    if meeting.get("hand_raised_by"):
        _clear_hand(store, ctx, meeting_id)
    return dict(row)


# ----------------------------------------------------------------------------- agenda

def agenda_add(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, title: str) -> dict[str, Any]:
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    require_owner(meeting, ctx, "edit the agenda")
    if meeting["status"] in TERMINAL:
        raise LiveMeetingStoreError("conflict", "This meeting has ended")
    count, top = store.session.execute(text("""select count(*), coalesce(max(position),0) from agent_meeting_agenda
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id"""), store.args(ctx, id=meeting_id)).one()
    if count >= 30:
        raise LiveMeetingStoreError("conflict", "An agenda can have at most 30 items")
    row = store.session.execute(text("""insert into agent_meeting_agenda
        (id,tenant_id,workspace_id,session_id,position,title,created_by)
        values (:row,:tenant,:workspace,:id,:pos,:title,:actor) returning id,position,title,state"""),
        store.args(ctx, id=meeting_id, row=new_id("agenda"), pos=int(top) + 1, title=title.strip()[:200], actor=ctx.user_id)).mappings().one()
    store.emit(ctx, meeting_id, "agenda", {"op": "add", "item": dict(row)}, actor=ctx.user_id)
    return dict(row)


def agenda_update(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, item_id: str, *,
                  title: str | None, state: str | None) -> dict[str, Any]:
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    require_owner(meeting, ctx, "edit the agenda")
    if state == "current":  # one current item at a time
        store.session.execute(text("""update agent_meeting_agenda set state='open'
            where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and state='current'"""), store.args(ctx, id=meeting_id))
    row = store.session.execute(text("""update agent_meeting_agenda set title=coalesce(:title,title), state=coalesce(:state,state)
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and id=:item returning id,position,title,state"""),
        store.args(ctx, id=meeting_id, item=item_id, title=title.strip()[:200] if title else None, state=state)).mappings().first()
    if row is None:
        raise LiveMeetingStoreError("not_found", "Agenda item not found")
    store.emit(ctx, meeting_id, "agenda", {"op": "update", "item": dict(row)}, actor=ctx.user_id)
    return dict(row)


def agenda_delete(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, item_id: str) -> None:
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    require_owner(meeting, ctx, "edit the agenda")
    gone = store.session.execute(text("""delete from agent_meeting_agenda
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and id=:item returning id"""),
        store.args(ctx, id=meeting_id, item=item_id)).first()
    if gone is None:
        raise LiveMeetingStoreError("not_found", "Agenda item not found")
    store.emit(ctx, meeting_id, "agenda", {"op": "delete", "item": {"id": item_id}}, actor=ctx.user_id)


# ----------------------------------------------------------------------------- outputs

def output_create(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, *, kind: str, body: str,
                  owner_label: str | None, source_sequences: list[int]) -> dict[str, Any]:
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    require_owner(meeting, ctx, "draft decisions and action items")
    if meeting.get("transcript_deleted_at") is not None:
        raise LiveMeetingStoreError("conflict", "The transcript was deleted")
    clean, _ = redact_secrets(body.strip())
    if not clean:
        raise LiveMeetingStoreError("conflict", "Text is empty")
    sources = sorted(set(source_sequences))
    if sources:
        found = store.session.execute(text("""select count(*) from agent_meeting_turns
            where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and sequence = any(:seqs)"""),
            store.args(ctx, id=meeting_id, seqs=sources)).scalar_one()
        if found != len(sources):
            raise LiveMeetingStoreError("conflict", "A linked source message does not exist in this meeting")
    row = store.session.execute(text(f"""insert into agent_meeting_outputs
        (id,tenant_id,workspace_id,session_id,kind,body,owner_label,source_sequences,created_by)
        values (:row,:tenant,:workspace,:id,:kind,:body,:label,cast(:sources as jsonb),:actor)
        returning {OUTPUT_COLUMNS}"""),  # nosec B608: fixed column list
        store.args(ctx, id=meeting_id, row=new_id("meeting_output"), kind=kind, body=clean[:2000],
                   label=(owner_label or None) and owner_label.strip()[:160], sources=json.dumps(sources),
                   actor=ctx.user_id)).mappings().one()
    store.emit(ctx, meeting_id, "output", {"op": "create", "output": _jsonable(row)}, actor=ctx.user_id)
    return dict(row)


CLICK_ONLY = "Confirm this on screen with a click. A spoken or typed answer cannot confirm it."


def output_decide(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, output_id: str, *,
                  state: str | None, body: str | None, owner_label: str | None,
                  input_mode: str = "typed", payload_hash: str | None = None,
                  before: dict[str, Any] | None = None) -> dict[str, Any]:
    """Confirm, dismiss or edit a draft. A summary or a model-drafted item can only be confirmed with a click and the
    hash of the text that was shown, so what is confirmed is what the owner read. ``before`` receives the previous state."""
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    require_owner(meeting, ctx, "confirm decisions and action items")
    current = store.session.execute(text("""select id, kind, body, state, task_id, origin from agent_meeting_outputs
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and id=:out for update"""),
        store.args(ctx, id=meeting_id, out=output_id)).mappings().first()
    if current is None:
        raise LiveMeetingStoreError("not_found", "Item not found")
    if before is not None:
        before.update(output_id=output_id, output_kind=current["kind"], previous_state=current["state"])
    guarded = current["kind"] == "summary" or current["origin"] == "model"
    if state == "confirmed" and guarded:
        from .live_agent_meeting_summary import content_hash
        if input_mode != "click":
            raise LiveMeetingStoreError("forbidden", CLICK_ONLY)
        if payload_hash != content_hash(dict(current)):
            raise LiveMeetingStoreError("conflict", "This draft changed since you read it. Reload and review it again before confirming.")
    if current["task_id"]:
        raise LiveMeetingStoreError("conflict", "This item already has a task and can no longer change")
    if body is not None:
        body, _ = redact_secrets(body.strip())
        if not body:
            raise LiveMeetingStoreError("conflict", "Text is empty")
    if guarded and current["state"] == "confirmed" and body is not None and state in (None, "confirmed"):
        raise LiveMeetingStoreError("conflict", "Move a confirmed summary or model draft back to draft before editing its text")
    if state == "confirmed" and body is not None and body != current["body"] and guarded:
        raise LiveMeetingStoreError("conflict", "Edit the draft and confirm it in two steps, so the text you read is the text you confirm")
    row = store.session.execute(text(f"""update agent_meeting_outputs set
          state=coalesce(:state,state), body=coalesce(:body,body),
          owner_label=case when :set_label then :label else owner_label end,
          decided_by=case when :state is not null then :actor else decided_by end, updated_at=now()
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and id=:out
        returning {OUTPUT_COLUMNS}"""),  # nosec B608: fixed column list
        store.args(ctx, id=meeting_id, out=output_id, state=state, body=body and body[:2000], set_label=owner_label is not None,
                   label=owner_label and owner_label.strip()[:160], actor=ctx.user_id)).mappings().one()
    store.emit(ctx, meeting_id, "output", {"op": "update", "output": _jsonable(row)}, actor=ctx.user_id)
    if before is not None:
        before["new_state"] = row["state"]
    return _with_hash(dict(row))


def output_for_task(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, output_id: str) -> dict[str, Any]:
    """Lock and validate an action item before the task is created. Owner only, confirmed, no task yet."""
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    require_owner(meeting, ctx, "create tasks from this meeting")
    row = store.session.execute(text(f"""select {OUTPUT_COLUMNS} from agent_meeting_outputs
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and id=:out for update"""),  # nosec B608
        store.args(ctx, id=meeting_id, out=output_id)).mappings().first()
    if row is None:
        raise LiveMeetingStoreError("not_found", "Item not found")
    if row["kind"] != "action_item" or row["state"] != "confirmed":
        raise LiveMeetingStoreError("conflict", "Confirm the action item before creating a task")
    if row["task_id"]:
        raise LiveMeetingStoreError("conflict", "This action item already has a task")
    return {**dict(row), "meeting_title": meeting["title"]}


def output_attach_task(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, output_id: str, task_id: str) -> dict[str, Any]:
    row = store.session.execute(text(f"""update agent_meeting_outputs set task_id=:task, updated_at=now()
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and id=:out and task_id is null
        returning {OUTPUT_COLUMNS}"""),  # nosec B608
        store.args(ctx, id=meeting_id, out=output_id, task=task_id)).mappings().first()
    if row is None:
        raise LiveMeetingStoreError("conflict", "This action item already has a task")
    store.emit(ctx, meeting_id, "output", {"op": "task", "output": _jsonable(row)}, actor=ctx.user_id)
    return dict(row)


# ----------------------------------------------------------------------------- decision to work links

LINK_SELECT = """select l.id, l.session_id, l.output_id, l.target_kind, l.target_id, l.created_by, l.created_at,
    coalesce(t.title, a.action, r.id) as target_label, coalesce(t.status, a.status, r.status) as target_status
  from agent_meeting_output_links l
  left join tasks t on l.target_kind = 'task' and t.tenant_id = l.tenant_id and t.workspace_id = l.workspace_id and t.id = l.target_id
  left join approvals a on l.target_kind = 'approval' and a.tenant_id = l.tenant_id and a.workspace_id = l.workspace_id and a.id = l.target_id
  left join agent_runs r on l.target_kind = 'run' and r.tenant_id = l.tenant_id and r.workspace_id = l.workspace_id and r.id = l.target_id
"""
TARGET_SQL = {
    "task": "select id from tasks where tenant_id=:tenant and workspace_id=:workspace and id=:target",
    "approval": "select id from approvals where tenant_id=:tenant and workspace_id=:workspace and id=:target",
    "run": "select id from agent_runs where tenant_id=:tenant and workspace_id=:workspace and id=:target",
}


def links_for_meeting(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str) -> list[dict[str, Any]]:
    return _rows(store, LINK_SELECT + """ where l.tenant_id=:tenant and l.workspace_id=:workspace and l.session_id=:id
        order by l.created_at, l.id""", ctx, meeting_id)  # nosec B608: fixed SQL text


def link_create(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, output_id: str,
                target_kind: str, target_id: str) -> tuple[dict[str, Any], bool]:
    """Link a confirmed decision to a task, approval or run of this workspace. Idempotent: a repeat returns the same link.

    The target is read through the caller's scoped session, so another workspace's or tenant's id is 'not found'."""
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    require_owner(meeting, ctx, "link decisions to work")
    output = store.session.execute(text("""select kind, state from agent_meeting_outputs
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and id=:out for update"""),
        store.args(ctx, id=meeting_id, out=output_id)).mappings().first()
    if output is None:
        raise LiveMeetingStoreError("not_found", "Item not found")
    if output["kind"] != "decision" or output["state"] != "confirmed":
        raise LiveMeetingStoreError("conflict", "Only a confirmed decision can be linked to work")
    if target_kind not in WORK_KINDS:
        raise LiveMeetingStoreError("conflict", "Link a task, an approval or a run")
    found = store.session.execute(text(TARGET_SQL[target_kind]), store.args(ctx, target=target_id)).first()
    if found is None:
        raise LiveMeetingStoreError("not_found", f"No {target_kind} with that id in this workspace")
    link_id = new_id("meeting_link")
    inserted = store.session.execute(text("""insert into agent_meeting_output_links
        (id,tenant_id,workspace_id,session_id,output_id,target_kind,target_id,created_by)
        values (:row,:tenant,:workspace,:id,:out,:kind,:target,:actor)
        on conflict (tenant_id,workspace_id,output_id,target_kind,target_id) do nothing returning id"""),
        store.args(ctx, id=meeting_id, row=link_id, out=output_id, kind=target_kind, target=target_id, actor=ctx.user_id)).first()
    row = _rows(store, LINK_SELECT + """ where l.tenant_id=:tenant and l.workspace_id=:workspace and l.output_id=:out
        and l.target_kind=:kind and l.target_id=:target""", ctx, meeting_id, out=output_id, kind=target_kind, target=target_id)[0]  # nosec B608
    if inserted is not None:
        store.emit(ctx, meeting_id, "output", {"op": "link", "link": _jsonable(row)}, actor=ctx.user_id)
    return row, inserted is not None


def link_delete(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, link_id: str) -> dict[str, Any]:
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    require_owner(meeting, ctx, "unlink work")
    row = store.session.execute(text(f"""delete from agent_meeting_output_links
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and id=:link returning {LINK_COLUMNS}"""),  # nosec B608
        store.args(ctx, id=meeting_id, link=link_id)).mappings().first()
    if row is None:
        raise LiveMeetingStoreError("not_found", "Link not found")
    store.emit(ctx, meeting_id, "output", {"op": "unlink", "link": _jsonable(row)}, actor=ctx.user_id)
    return dict(row)


def meetings_for_work(store: SqlLiveMeetingStore, ctx: TenantContext, target_kind: str, target_id: str) -> list[dict[str, Any]]:
    """The other direction: which confirmed decisions (and meetings) led to this task, approval or run."""
    return [dict(r) for r in store.session.execute(text("""
        select l.id as link_id, l.created_at, o.id as output_id, o.body, o.state, s.id as meeting_id, s.title, s.status
        from agent_meeting_output_links l
        join agent_meeting_outputs o on o.tenant_id=l.tenant_id and o.workspace_id=l.workspace_id and o.id=l.output_id
        join agent_meeting_sessions s on s.tenant_id=l.tenant_id and s.workspace_id=l.workspace_id and s.id=l.session_id
        where l.tenant_id=:tenant and l.workspace_id=:workspace and l.target_kind=:kind and l.target_id=:target
        order by l.created_at desc limit 50"""), store.args(ctx, kind=target_kind, target=target_id)).mappings().all()]


def _jsonable(row: Any) -> dict[str, Any]:
    return json.loads(json.dumps(dict(row), default=str))


# ----------------------------------------------------------------------------- files

def file_link(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, file_id: str) -> dict[str, Any]:
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    require_owner(meeting, ctx, "link files")
    found = store.session.execute(text("""select name from workspace_files
        where tenant_id=:tenant and workspace_id=:workspace and id=:file"""), store.args(ctx, file=file_id)).mappings().first()
    if found is None:
        raise LiveMeetingStoreError("not_found", "File not found in this workspace")
    store.session.execute(text("""insert into agent_meeting_files (id,tenant_id,workspace_id,session_id,file_id,added_by)
        values (:row,:tenant,:workspace,:id,:file,:actor)
        on conflict (tenant_id,workspace_id,session_id,file_id) do nothing"""),
        store.args(ctx, id=meeting_id, row=new_id("meeting_file"), file=file_id, actor=ctx.user_id))
    store.emit(ctx, meeting_id, "file", {"op": "add", "file_id": file_id, "name": found["name"]}, actor=ctx.user_id)
    return {"file_id": file_id, "name": found["name"]}


def file_unlink(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str, file_id: str) -> None:
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    require_owner(meeting, ctx, "unlink files")
    store.session.execute(text("""delete from agent_meeting_files
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id and file_id=:file"""),
        store.args(ctx, id=meeting_id, file=file_id))
    store.emit(ctx, meeting_id, "file", {"op": "remove", "file_id": file_id}, actor=ctx.user_id)


# ----------------------------------------------------------------------------- export and delete

def export_summary(outputs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The meeting's live summary (confirmed, else the draft), or None. A dismissed one is not part of the record."""
    live = [o for o in outputs if o["kind"] == "summary" and o["state"] != "dismissed"]
    live.sort(key=lambda o: o["state"] != "confirmed")
    return live[0] if live else None


def export_markdown(bundle: dict[str, Any], turns: list[dict[str, Any]]) -> str:
    meeting = bundle["meeting"]
    outputs = bundle["outputs"]
    links = bundle.get("links", [])
    lines = [f"# {meeting['title']}", "", f"Status: {meeting['status']}", ""]
    summary = export_summary(outputs)
    if summary:
        note = "confirmed by the owner" if summary["state"] == "confirmed" else "draft, not yet confirmed by the owner"
        lines += ["## Summary", f"Drafted by a model; {note}.", "", summary["body"], ""]
    if bundle["agenda"]:
        lines += ["## Agenda", *[f"{a['position']}. {a['title']} ({a['state']})" for a in bundle["agenda"]], ""]
    lines += ["## Transcript", ""]
    lines += [f"**{t['speaker_id']}** ({t['role']}, #{t['sequence']}): {t['body']}" for t in turns] or ["The transcript was deleted or has no messages."]
    for kind, title in (("decision", "Decisions"), ("action_item", "Action items")):
        items = [o for o in outputs if o["kind"] == kind and o["state"] != "dismissed"]
        if items:
            lines += ["", f"## {title}"]
            for o in items:
                lines.append(f"- [{o['state']}] {o['body']}" + (f" (owner: {o['owner_label']})" if o["owner_label"] else "")
                             + (f" (from messages {', '.join('#' + str(n) for n in o['source_sequences'])})" if o["source_sequences"] else "")
                             + (" (drafted by a model)" if o.get("origin") == "model" else ""))
                lines += [f"  - linked {link['target_kind']}: {link.get('target_label') or link['target_id']} ({link['target_id']})"
                          for link in links if link["output_id"] == o["id"]]
    return "\n".join(lines) + "\n"


def export_json(bundle: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    """The JSON export: the record of the meeting with its summary, decisions (each with its linked work) and every confirmed output."""
    outputs = bundle["outputs"]
    links = bundle.get("links", [])
    by_output: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        by_output.setdefault(link["output_id"], []).append(link)
    payload = {k: bundle[k] for k in ("meeting", "participants", "agenda", "outputs", "files")}
    payload["turns"] = turns
    payload["links"] = links
    payload["summary"] = export_summary(outputs)
    payload["decisions"] = [{**o, "links": by_output.get(o["id"], [])} for o in outputs if o["kind"] == "decision" and o["state"] != "dismissed"]
    payload["confirmed_outputs"] = [{**o, "links": by_output.get(o["id"], [])} for o in outputs if o["state"] == "confirmed"]
    return payload


def delete_transcript(store: SqlLiveMeetingStore, ctx: TenantContext, meeting_id: str) -> dict[str, int]:
    """Owner deletes the transcript of an ended meeting. Metadata, usage totals, confirmed outputs and tasks stay."""
    meeting = _meeting(store, ctx, meeting_id, lock=True)
    require_owner(meeting, ctx, "delete the transcript")
    if meeting["status"] not in TERMINAL:
        raise LiveMeetingStoreError("conflict", "End the meeting before deleting its transcript")
    if meeting.get("transcript_deleted_at") is not None:
        return {"turns": 0, "events": 0}
    args = store.args(ctx, id=meeting_id)
    turns = store.session.execute(text("""delete from agent_meeting_turns
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id"""), args).rowcount
    store.session.execute(text("""delete from agent_meeting_turn_leases
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id"""), args)
    events = store.session.execute(text("""delete from agent_meeting_events
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id"""), args).rowcount
    store.session.execute(text("""update agent_meeting_outputs set source_sequences='[]'::jsonb
        where tenant_id=:tenant and workspace_id=:workspace and session_id=:id"""), args)
    store.session.execute(text("""update agent_meeting_sessions set transcript_deleted_at=now(), updated_at=now()
        where tenant_id=:tenant and workspace_id=:workspace and id=:id"""), args)
    return {"turns": int(turns or 0), "events": int(events or 0)}
