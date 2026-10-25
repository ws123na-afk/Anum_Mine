"""Propose and confirm "add a building" (docs/ANUM_MINE_EXECUTIVE_PA.md#add-a-building).

* ``GET  /api/v1/office/building-proposals?name=&floors=&lang=``: read only (``task:read``). Returns exactly what
  confirming would create (building, floors, template rooms, bounds) and a ``payload_hash`` bound to the tenant,
  workspace, user and content. Nothing is stored. The assistant tool ``propose_add_building`` returns the same
  proposal; no assistant tool can create.
* ``POST /api/v1/office/building-proposals/confirm``: needs ``workspace:manage``, the hash the owner was shown and
  ``input_mode: "click"``. Voice or typed input is refused with 403 before anything runs. Everything is created in
  one transaction by the builder store (all or nothing), audited like the builder's own routes, and an optional
  ``Idempotency-Key`` makes a retry return the building already made.
"""
from __future__ import annotations

import hmac
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import office_layout as layout
from . import office_building_plan as bp
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .office_space_api import _audit, _error, _snap, _store
from .office_space_store import SpaceStoreError
from .schemas import TenantContext

router = APIRouter(prefix="/api/v1/office/building-proposals", tags=["office building proposals"])
VERSION = 1
CreateKey = Annotated[str | None, Header(alias="Idempotency-Key", min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")]


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=bp.MAX_NAME)
    floors: int = Field(ge=1, le=bp.MAX_FLOORS)
    lang: Literal["en", "ar"] = "en"
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_mode: Literal["click", "typed", "voice"] = "typed"


def _existing(context: TenantContext, store: Any) -> tuple[list[str], list[str], str]:
    ids: list[str] = []
    names: list[str] = []
    zone = bp.DEFAULT_TIMEZONE
    cursor: str | None = None
    for _ in range(100):
        page = store.list(context, "buildings", None, cursor, 100)
        for b in page["items"]:
            ids.append(b["id"])
            names.append(b["name"])
            zone = b.get("timezone") or zone
        cursor = page["next_cursor"]
        if not cursor:
            break
    return ids, names, zone


def build_proposal(context: TenantContext, store: Any, name: str | None, floors: int | None, lang: str) -> dict[str, Any]:
    """The proposal view for the caller. Raises ``bp.PlanError`` when the request cannot become a plan."""
    ids, names, zone = _existing(context, store)
    content = bp.plan_building(name, floors, "ar" if lang == "ar" else "en", ids, names, zone)
    return {
        "version": VERSION, "content": content,
        "payload_hash": bp.plan_hash(context.tenant_id, context.workspace_id, context.user_id, content),
        "bounds": {"width_m": bp.FLOOR_W, "depth_m": bp.FLOOR_D},
        "creates": content["totals"],
        "can_confirm": has_permission(context, Permission.WORKSPACE_MANAGE),
        "governed_by": "builder API (workspace:manage, one transaction, audited)",
        "note": "Nothing is created until a workspace owner clicks Confirm. No desks or agents are added.",
    }


def propose(context: TenantContext, name: str | None, floors: int | None, lang: str) -> dict[str, Any]:
    """Read-only proposal for the PA tool and the GET route: ``{"status": "proposal", ...}`` or a question/refusal."""
    try:
        with _store(context) as store:
            return {"status": "proposal", **build_proposal(context, store, name, floors, lang)}
    except bp.PlanError as exc:
        if exc.code in ("name_required", "floors_required"):
            return {"status": "needs_info", "missing": "name" if exc.code == "name_required" else "floors", "code": exc.code, "question": bp.message_for(exc.code)}
        return {"status": "invalid", "code": exc.code, "note": bp.message_for(exc.code)}


@router.get("")
def get_proposal(
    name: Annotated[str, Query(min_length=1, max_length=400)],
    floors: Annotated[int, Query(ge=1, le=bp.MAX_FLOORS)],
    lang: Annotated[Literal["en", "ar"], Query()] = "en",
    context: TenantContext = Depends(tenant_context),
) -> dict:
    require_permission(context, Permission.TASK_READ)
    result = propose(context, name, floors, lang)
    if result["status"] != "proposal":
        raise HTTPException(422, result.get("note") or result.get("question") or "Invalid request")
    return result


@router.post("/confirm", status_code=status.HTTP_201_CREATED)
def confirm_proposal(body: ConfirmBody, response: Response, idempotency_key: CreateKey = None,
                     context: TenantContext = Depends(tenant_context)) -> dict:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    if body.input_mode != "click":
        raise HTTPException(403, "Confirm this on screen with a click. A spoken or typed answer cannot confirm it.")
    try:
        with _store(context) as store:
            if idempotency_key:
                # A retry of a confirm that already built: answer with that building, never plan again
                # (the new building's own name would now be "taken").
                prior = store.session.execute(text("""
                    select resource_kind, resource_id from office_create_keys
                    where tenant_id = :tenant and workspace_id = :workspace and key = :key
                """), {"tenant": context.tenant_id, "workspace": context.workspace_id, "key": idempotency_key}).mappings().first()
                if prior is not None:
                    existing = store.get(context, "buildings", prior["resource_id"]) if prior["resource_kind"] == "buildings" else None
                    if existing is None or bp.clean_name(existing["name"]).casefold() != bp.clean_name(body.name).casefold():
                        raise HTTPException(409, "This key was already used for a different request. Start a new one.")
                    response.status_code = status.HTTP_200_OK
                    return {"version": VERSION, "replayed": True, "building": {"id": existing["id"], "name": existing["name"]}}
            view = build_proposal(context, store, body.name, body.floors, body.lang)
            if not hmac.compare_digest(view["payload_hash"], body.payload_hash):
                raise HTTPException(409, "The proposal changed. Review it again.")
            content = view["content"]
            b = content["building"]
            row, created = store.create_with_key(
                context, "buildings", b["id"],
                {"name": b["name"], "timezone": b["timezone"], "latitude": None, "longitude": None}, idempotency_key)
            if not created:
                response.status_code = status.HTTP_200_OK
                return {"version": VERSION, "replayed": True, "building": {"id": row["id"], "name": row["name"]}, "creates": content["totals"]}
            _audit(store, context, "office.building.create", b["id"], kind="buildings", after=_snap("buildings", row),
                   ids=[b["id"]])
            room_ids: list[str] = []
            for f in content["floors"]:
                floor = store.create(context, "floors", f["id"], {"building_id": b["id"], "level": f["level"], "name": f["name"],
                                                                 "width_m": f["width_m"], "depth_m": f["depth_m"]})
                _audit(store, context, "office.floor.create", f["id"], kind="floors", after=_snap("floors", floor), floor_id=f["id"])
                for r in f["rooms"]:
                    template = layout.template_by_id(r["template_id"])
                    room, _desks = store.create_room_from_template(context, template, r["id"], f["id"], None, r["x"], r["z"], False)
                    room_ids.append(room["id"])
                    _audit(store, context, "office.room.create", r["id"], kind="rooms", floor_id=f["id"],
                           after={**_snap("rooms", room), "template_id": r["template_id"], "desks": []})
            _audit(store, context, "office.building.create_from_proposal", b["id"], kind="buildings", ids=[b["id"], *room_ids],
                   after={"input_mode": "click", "payload_hash": body.payload_hash, "floors": len(content["floors"]), "rooms": len(room_ids)})
            return {"version": VERSION, "replayed": False, "building": {"id": b["id"], "name": b["name"]},
                    "floor_ids": [f["id"] for f in content["floors"]], "room_ids": room_ids, "creates": content["totals"]}
    except bp.PlanError as exc:
        raise HTTPException(409 if exc.code == "name_exists" else 422, bp.message_for(exc.code)) from exc
    except SpaceStoreError as exc:
        raise _error(exc) from exc
