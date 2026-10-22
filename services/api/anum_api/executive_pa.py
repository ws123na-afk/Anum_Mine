"""Executive PA: a permissioned tool interface for the ANUM MINE headquarters.

It replaces the old demonstration speech commands of the office preview (a client side
parser that only moved the camera) with a server side tool catalogue. The PA is a thin
layer over APIs that already govern everything:

* **Read tools** (navigate, open a panel, summarise a workspace, list blocked work, list
  pending approvals) run with the caller's own permissions in the caller's tenant and
  workspace scope. A summary of any *other* workspace is possible only through an explicit
  executive grant, read by ``executive_api`` (reader role, then the workspace's own RLS
  context, audited there). Without PostgreSQL the answer is "not connected"; without a
  grant it is "not granted".
* **Write tools** (start a task, schedule a live meeting, delegate to an agent) never do
  the work themselves. They call the same code as ``POST /tasks`` plus ``POST
  /tasks/{id}/run`` and ``POST /agent-meetings``, so permission, run lock, budget, agent
  profile, ToolPolicy, autonomy and approvals all apply. A risky tool call stops at
  ``waiting_approval`` and its approval appears in Approvals. There is deliberately **no**
  tool that approves or rejects anything: the PA can never decide an approval.
* Every write is two steps. The first call returns a proposal and a ``payload_hash``; only a
  second call with that hash and ``input_mode: "click"`` (a visual confirmation) executes
  it. A call with ``input_mode: "voice"`` can never confirm, so a spoken sentence cannot
  bypass the visual gate.
* Every invocation, allowed or refused, writes one audit record (``executive_pa.invoke``).

See docs/ANUM_MINE_EXECUTIVE_PA.md.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, repository_context, tenant_context, units_of_work
from .repository import AnumRepository
from .schemas import TaskCreate, TaskStatus, TenantContext, new_id, utc_now
from .tasks import TaskFilters

router = APIRouter(prefix="/api/v1/executive-pa", tags=["executive pa"])

Identifier = str
InputMode = Literal["typed", "voice", "click"]
MAX_LIST = 20


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- the tool catalogue -------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: Literal["read", "write"]
    label: str
    permissions: tuple[Permission, ...]
    risk: Literal["low", "medium", "high"]
    # "automatic": runs when asked. "confirm_each": needs a visual confirmation every time.
    autonomy: Literal["automatic", "confirm_each"]
    # The existing API or path whose rules apply to this tool.
    governed_by: str


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec("navigate", "read", "Go to a building, floor, room or agent", (Permission.TASK_READ,), "low", "automatic", "GET /office-spaces"),
        ToolSpec("open_panel", "read", "Open a panel", (Permission.TASK_READ,), "low", "automatic", "the panel's own API"),
        ToolSpec("summarise_workspace", "read", "Summarise a workspace you may see", (Permission.TASK_READ,), "low", "automatic", "executive grants (GET /executive/overview rules) and the workspace's own APIs"),
        ToolSpec("list_blocked_work", "read", "List blocked work", (Permission.TASK_READ,), "low", "automatic", "GET /tasks"),
        ToolSpec("list_pending_approvals", "read", "List pending approvals", (Permission.APPROVAL_READ,), "low", "automatic", "GET /approvals"),
        ToolSpec("start_task", "write", "Start a task", (Permission.TASK_CREATE, Permission.TASK_RUN), "medium", "confirm_each", "POST /tasks then POST /tasks/{id}/run (ToolPolicy, autonomy, approvals)"),
        ToolSpec("delegate", "write", "Delegate work to an agent", (Permission.TASK_CREATE, Permission.TASK_RUN), "medium", "confirm_each", "POST /tasks with agent_id then run (agent profile, ToolPolicy, approvals)"),
        ToolSpec("schedule_meeting", "write", "Create a live agent meeting", (Permission.WORKSPACE_MANAGE,), "medium", "confirm_each", "POST /agent-meetings (meeting limits, audit)"),
    )
}

PANELS: dict[str, tuple[str, Permission]] = {
    "executive": ("executive", Permission.TASK_READ),
    "command": ("command", Permission.TASK_READ),
    "tasks": ("tasks", Permission.TASK_READ),
    "approvals": ("approvals", Permission.APPROVAL_READ),
    "meetings": ("meetings", Permission.TASK_READ),
    "calendar": ("calendar", Permission.TASK_READ),
    "audit": ("audit", Permission.AUDIT_READ),
}


class NavigateArgs(StrictBody):
    target_kind: Literal["building", "floor", "room", "agent"]
    target_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class OpenPanelArgs(StrictBody):
    panel: Literal["executive", "command", "tasks", "approvals", "meetings", "calendar", "audit"]


class SummariseArgs(StrictBody):
    workspace_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class NoArgs(StrictBody):
    pass


class StartTaskArgs(StrictBody):
    title: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=8000)


class DelegateArgs(StartTaskArgs):
    agent_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class ScheduleMeetingArgs(StrictBody):
    title: str = Field(min_length=1, max_length=200)


ARGS: dict[str, type[StrictBody]] = {
    "navigate": NavigateArgs,
    "open_panel": OpenPanelArgs,
    "summarise_workspace": SummariseArgs,
    "list_blocked_work": NoArgs,
    "list_pending_approvals": NoArgs,
    "start_task": StartTaskArgs,
    "delegate": DelegateArgs,
    "schedule_meeting": ScheduleMeetingArgs,
}


# -- reading the office -------------------------------------------------------------------


def _load_office(context: TenantContext) -> dict[str, list[dict[str, Any]]]:
    """Buildings, floors, rooms and desks visible to the caller (RLS scoped). Raises
    ``HTTPException(503)`` when the backend has no office persistence."""
    from .office_space_api import _store

    out: dict[str, list[dict[str, Any]]] = {}
    with _store(context) as store:
        for kind in ("buildings", "floors", "rooms", "desks"):
            items: list[dict[str, Any]] = []
            cursor: str | None = None
            for _ in range(100):
                page = store.list(context, kind, None, cursor, 100)
                items.extend(page["items"])
                cursor = page["next_cursor"]
                if not cursor:
                    break
            out[kind] = items
    return out


def build_tour(office: Mapping[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Stops generated from the buildings, floors and rooms the person can access, in
    building, floor level, room name order. Nothing is invented: a stop is a stored room."""
    floors = {f["id"]: f for f in office.get("floors", [])}
    buildings = {b["id"]: b for b in office.get("buildings", [])}
    desks_by_room: dict[str, int] = {}
    for desk in office.get("desks", []):
        desks_by_room[desk["room_id"]] = desks_by_room.get(desk["room_id"], 0) + 1
    stops: list[dict[str, Any]] = []
    for room in office.get("rooms", []):
        floor = floors.get(room["floor_id"])
        building = buildings.get(floor["building_id"]) if floor else None
        if floor is None or building is None:
            continue
        stops.append({
            "building_id": building["id"], "building_name": building["name"],
            "floor_id": floor["id"], "floor_name": floor["name"], "level": floor["level"],
            "room_id": room["id"], "room_name": room["name"], "room_kind": room["kind"],
            "capacity": room["capacity"], "desks": desks_by_room.get(room["id"], 0),
        })
    stops.sort(key=lambda s: (s["building_name"].lower(), s["level"], s["room_name"].lower(), s["room_id"]))
    return stops


# -- audit ----------------------------------------------------------------------------------


def args_hash(context: TenantContext, tool: str, arguments: Mapping[str, Any]) -> str:
    """The proposal a person confirms: bound to tenant, workspace, user, tool and arguments."""
    canonical = json.dumps(
        {"t": context.tenant_id, "w": context.workspace_id, "u": context.user_id, "tool": tool, "args": arguments},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _audit(repository: AnumRepository, context: TenantContext, tool: str, mode: str, outcome: str, **metadata: Any) -> None:
    repository.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        actor=context.user_id, action="executive_pa.invoke", target=tool, outcome=outcome,
        correlation_id=new_id("correlation"), created_at=utc_now(),
        metadata={"resource": "executive_pa", "tool": tool, "input_mode": mode, **metadata},
    ))


# -- the API ----------------------------------------------------------------------------------


class InvokeBody(StrictBody):
    tool: str = Field(min_length=1, max_length=60)
    arguments: dict[str, Any] = Field(default_factory=dict)
    input_mode: InputMode = "typed"
    # Sent back from a proposal to confirm it. Honoured only with input_mode "click".
    confirm_hash: str | None = Field(default=None, min_length=64, max_length=64)


def _spec_view(spec: ToolSpec, context: TenantContext) -> dict[str, Any]:
    return {
        "name": spec.name, "kind": spec.kind, "label": spec.label, "risk": spec.risk,
        "autonomy": spec.autonomy, "governed_by": spec.governed_by,
        "permissions": [p.value for p in spec.permissions],
        "allowed": all(has_permission(context, p) for p in spec.permissions),
    }


@router.get("/tools")
def list_tools(context: TenantContext = Depends(tenant_context)) -> dict:
    """The catalogue, with each tool's declared risk and autonomy and whether *this person*
    may use it. There is no tool that decides an approval."""
    return {"tools": [_spec_view(spec, context) for spec in TOOLS.values()]}


@router.get("/tour")
def guided_tour(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> dict:
    """A tour of the rooms this person can access, generated from the office registry."""
    try:
        from .dependencies import require_permission

        require_permission(context, Permission.TASK_READ)
        stops = build_tour(_load_office(context))
        status = "ready" if stops else "empty"
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        stops, status = [], "not_connected"
    _audit(repository, context, "tour", "typed", "success", status=status, stops=len(stops))
    return {"status": status, "stops": stops}


def _result(status: str, **fields: Any) -> dict[str, Any]:
    return {"status": status, **fields}


async def _create_task_governed(context: TenantContext, args: StartTaskArgs, agent_id: str | None) -> Any:
    """``POST /tasks`` exactly (agent check, plan capacity, event) in its own unit of work."""
    from . import main
    from .dependencies import _unit_of_work

    payload = TaskCreate(title=args.title.strip(), prompt=args.prompt.strip(), agent_id=agent_id)
    with _unit_of_work(context, require_membership=True) as repository:
        return await main.create_task(payload, context, repository)


async def _run_governed(context: TenantContext, task_id: str, units: Any) -> Any:
    """``POST /tasks/{id}/run`` exactly: it may stop at ``waiting_approval``."""
    from . import main

    return await main.start_task_run(task_id, context, units)


async def _execute_write(spec: ToolSpec, args: StrictBody, context: TenantContext, units: Any) -> dict[str, Any]:
    if spec.name == "schedule_meeting":
        from .live_agent_meeting_api import CreateMeeting, create_meeting

        args = cast(ScheduleMeetingArgs, args)
        meeting = create_meeting(CreateMeeting(title=args.title), context)
        return _result("done", meeting_id=meeting["id"], note="A live meeting session was created. Add agents and start it from Meetings.")
    args = cast(StartTaskArgs, args)
    agent_id = args.agent_id if isinstance(args, DelegateArgs) else None
    task = await _create_task_governed(context, args, agent_id)
    run = await _run_governed(context, task.id, units)
    approval = getattr(run, "approval", None)
    if approval is not None:
        return _result("approval_pending", task_id=task.id, approval_id=approval.id,
                       note="The task stopped for approval. Decide it in Approvals; the assistant cannot.")
    return _result("done", task_id=task.id, task_status=run.task.status.value)


def _own_summary(context: TenantContext, repository: AnumRepository) -> dict[str, Any]:
    """The current workspace's own figures, in the shape of the executive contract
    (packages/contracts/src/executive.ts ``ExecutiveSummary``)."""
    summary = repository.summarize_tasks(context, TaskFilters())
    pending = repository.list_approvals_page(context, now=utc_now(), status=_pending_filter(), query=None, task_id=None, cursor=None, limit=200)[0]
    cost = _own_cost(context)
    meetings = _own_meetings(context)
    out: dict[str, Any] = {
        "tasks": {"by_status": dict(summary.by_status)},
        "approvals": {"pending": len(pending)},
    }
    if cost is not None:
        out["cost"] = {"estimated_cost_usd": str(cost), "currency": "USD"}
    if meetings is not None:
        out["meetings"] = {"active": meetings}
    return out


def _pending_filter() -> Any:
    from .approval_paging import ApprovalFilter

    return ApprovalFilter.PENDING


def _own_cost(context: TenantContext) -> float | None:
    try:
        from .model_budget import _overview

        return round(_overview(context, utc_now()).workspace.usage.estimated_cost_usd, 4)
    except Exception:  # noqa: BLE001 - a missing figure is shown as not available, never guessed
        return None


def _own_meetings(context: TenantContext) -> int | None:
    try:
        from .live_agent_meeting_api import _store

        with _store(context) as store:
            rows = store.list(context, cursor=None, limit=100)["items"]
        return sum(1 for row in rows if row.get("status") in {"active", "paused", "scheduled"})
    except Exception:  # noqa: BLE001
        return None


def _granted_summary(context: TenantContext, target: str) -> tuple[str, dict[str, Any] | None]:
    """The grant-checked summary of another workspace, read by the executive API itself
    (reader role, then the workspace's own RLS context, audited there): never by this module."""
    from . import executive_api

    try:
        executive_api.require_postgres()
        grants = executive_api._granted_rows(context)
    except HTTPException as exc:
        if exc.status_code == 503:
            return "not_connected", None
        raise
    grant = next((g for g in grants if g.workspace_id == target), None)
    if grant is None:
        return "not_granted", None
    item = executive_api._read_granted_workspace(context, grant, utc_now())
    if item.get("access") != "granted":
        return "not_granted", None
    return "ok", dict(item["summary"])


def _summarise(context: TenantContext, repository: AnumRepository, args: SummariseArgs) -> dict[str, Any]:
    target = args.workspace_id or context.workspace_id
    if target == context.workspace_id:
        return _result("ok", workspace_id=target, scope="current", summary=_own_summary(context, repository))
    state, summary = _granted_summary(context, target)
    if state == "not_connected":
        return _result("not_connected", workspace_id=target, note="Executive grants need the PostgreSQL backend.")
    if summary is None:
        return _result("not_granted", workspace_id=target, note="No active grant for this workspace. Ask its owner to grant it.")
    return _result("ok", workspace_id=target, scope="granted", summary=summary)


def _blocked(context: TenantContext, repository: AnumRepository) -> dict[str, Any]:
    tasks = repository.list_tasks(context, limit=MAX_LIST, filters=TaskFilters(statuses=frozenset({TaskStatus.WAITING_APPROVAL, TaskStatus.FAILED})))
    return _result("ok", items=[{"id": t.id, "title": t.title, "status": t.status.value, "agent_id": t.agent_id} for t in tasks], truncated=len(tasks) >= MAX_LIST)


def _approvals(context: TenantContext, repository: AnumRepository) -> dict[str, Any]:
    rows = repository.list_approvals_page(context, now=utc_now(), status=_pending_filter(), query=None, task_id=None, cursor=None, limit=MAX_LIST)[0]
    return _result("ok", items=[{"id": a.id, "task_id": a.task_id, "action": a.action, "risk_level": a.risk_level.value} for a in rows], truncated=len(rows) >= MAX_LIST)


def _navigate(context: TenantContext, args: NavigateArgs) -> dict[str, Any]:
    try:
        office = _load_office(context)
    except HTTPException as exc:
        if exc.status_code == 503:
            return _result("not_connected", note="The office registry is not available on this server.")
        raise
    floors = {f["id"]: f for f in office["floors"]}
    rooms = {r["id"]: r for r in office["rooms"]}
    location: dict[str, Any] | None = None
    if args.target_kind == "building":
        if any(b["id"] == args.target_id for b in office["buildings"]):
            location = {"building_id": args.target_id}
    elif args.target_kind == "floor":
        floor = floors.get(args.target_id)
        if floor:
            location = {"building_id": floor["building_id"], "floor_id": floor["id"]}
    else:
        room = rooms.get(args.target_id) if args.target_kind == "room" else None
        if args.target_kind == "agent":
            desk = next((d for d in office["desks"] if d.get("agent_id") == args.target_id), None)
            room = rooms.get(desk["room_id"]) if desk else None
        floor = floors.get(room["floor_id"]) if room else None
        if room and floor:
            location = {"building_id": floor["building_id"], "floor_id": floor["id"], "room_id": room["id"]}
            if args.target_kind == "agent":
                location["agent_id"] = args.target_id
    if location is None:
        return _result("not_found", note="That place is not in the office you can access.")
    return _result("ok", navigate=location)


@router.post("/invoke")
async def invoke_tool(
    body: InvokeBody,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
    units: Any = Depends(units_of_work),
) -> dict:
    spec = TOOLS.get(body.tool)
    if spec is None:
        _audit(repository, context, body.tool[:60], body.input_mode, "denied", reason="unknown_tool")
        raise HTTPException(422, "Unknown assistant tool")
    try:
        args = ARGS[spec.name].model_validate(body.arguments)
    except ValidationError as exc:
        raise HTTPException(422, "Invalid arguments for " + spec.name) from exc
    digest = args_hash(context, spec.name, args.model_dump())

    missing = [p.value for p in spec.permissions if not has_permission(context, p)]
    if spec.name == "open_panel":
        args = cast(OpenPanelArgs, args)
        panel_permission = PANELS[args.panel][1]
        if not has_permission(context, panel_permission):
            missing = [panel_permission.value]
    if missing:
        _audit(repository, context, spec.name, body.input_mode, "denied", reason="permission", missing=missing)
        return _result("denied", tool=spec.name, note="Your role does not allow this. Ask a workspace owner.", missing=missing)

    if spec.kind == "write":
        if body.confirm_hash is None:
            _audit(repository, context, spec.name, body.input_mode, "pending_confirmation", payload_hash=digest)
            return _result("needs_confirmation", tool=spec.name, payload_hash=digest, risk=spec.risk,
                           autonomy=spec.autonomy, governed_by=spec.governed_by,
                           preview=args.model_dump(), note="Review and confirm with a click. Voice cannot confirm.")
        if body.input_mode != "click":
            _audit(repository, context, spec.name, body.input_mode, "denied", reason="visual_confirmation_required", payload_hash=digest)
            return _result("needs_visual_confirmation", tool=spec.name,
                           note="Confirm this on screen with a click. A spoken or typed answer cannot confirm it.")
        if body.confirm_hash != digest:
            _audit(repository, context, spec.name, body.input_mode, "denied", reason="stale_confirmation", payload_hash=digest)
            return _result("stale_confirmation", tool=spec.name, note="The request changed. Review it again.")
        try:
            outcome = await _execute_write(spec, args, context, units)
        except HTTPException as exc:
            status = "not_connected" if exc.status_code == 503 else "failed"
            _audit(repository, context, spec.name, body.input_mode, "failure", reason=str(exc.detail)[:200], http_status=exc.status_code, payload_hash=digest)
            return _result(status, tool=spec.name, http_status=exc.status_code, note=str(exc.detail)[:300])
        _audit(repository, context, spec.name, body.input_mode, "success", payload_hash=digest, result=outcome["status"],
               **{k: v for k, v in outcome.items() if k in {"task_id", "approval_id", "meeting_id"}})
        return {"tool": spec.name, **outcome}

    if spec.name == "navigate":
        args = cast(NavigateArgs, args)
        outcome = _navigate(context, args)
    elif spec.name == "open_panel":
        args = cast(OpenPanelArgs, args)
        outcome = _result("ok", open_panel={"view": PANELS[args.panel][0]})
    elif spec.name == "summarise_workspace":
        args = cast(SummariseArgs, args)
        outcome = _summarise(context, repository, args)
    elif spec.name == "list_blocked_work":
        outcome = _blocked(context, repository)
    else:
        outcome = _approvals(context, repository)
    _audit(repository, context, spec.name, body.input_mode, "success", result=outcome["status"])
    return {"tool": spec.name, **outcome}


__all__ = ["router", "TOOLS", "build_tour", "args_hash"]
