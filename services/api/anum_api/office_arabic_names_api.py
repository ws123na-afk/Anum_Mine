"""Owner-triggered "Add Arabic names" for campuses that were set up before names had an Arabic form.

* ``GET  /api/v1/office/starter-campus/arabic-names/preview``: every building, floor and room that would get an
  Arabic name, with a ``payload_hash``. Nothing is stored. ``task:read``.
* ``POST /api/v1/office/starter-campus/arabic-names/confirm``: needs ``workspace:manage``, the hash you were shown
  and ``input_mode: "click"``. A spoken or typed confirmation can never confirm it.

The rule is narrow on purpose (docs/ANUM_MINE_STARTER_CAMPUS.md "Add Arabic names"): a record is filled ONLY when its
Arabic name is empty AND its English name is a name of the starter master list. Nothing is ever overwritten, renamed,
moved or deleted, and the write re-checks both conditions under a row lock. One transaction, one audit record that
carries ids and counts only (never the names).
"""
from __future__ import annotations

import hmac
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import office_starter_campus as sc
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .office_scope import require_postgres, workspace_session
from .office_space_api import _audit as office_audit
from .office_space_api import _store
from .office_space_store import SpaceStoreError
from .schemas import TenantContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/office/starter-campus/arabic-names", tags=["office starter campus"])
VERSION = 1
_SELECTS = {
    "buildings": text("select id, name, name_ar from office_buildings where tenant_id = :t and workspace_id = :w "
                      "and (name_ar is null or btrim(name_ar) = '') order by id limit :n"),
    "floors": text("select id, name, name_ar from office_floors where tenant_id = :t and workspace_id = :w "
                   "and (name_ar is null or btrim(name_ar) = '') order by id limit :n"),
    "rooms": text("select id, name, name_ar from office_rooms where tenant_id = :t and workspace_id = :w "
                  "and (name_ar is null or btrim(name_ar) = '') order by id limit :n"),
}
_LIMIT = 5000


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Only a click on the review dialog confirms. Voice and typed sentences are refused.
    input_mode: Literal["click", "typed", "voice"] = "typed"


def _rows(session: Any, context: TenantContext) -> dict[str, list[dict[str, Any]]]:
    """Records of this workspace that still have no Arabic name (RLS and the explicit scope both apply)."""
    params = {"t": context.tenant_id, "w": context.workspace_id, "n": _LIMIT}
    out = {kind: [dict(r) for r in session.execute(query, params).mappings()] for kind, query in _SELECTS.items()}
    return out


def _view(context: TenantContext, plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": VERSION, "catalog_version": sc.CATALOG_VERSION, "workspace_id": context.workspace_id,
        "payload_hash": sc.arabic_proposal_hash(context.tenant_id, context.workspace_id, context.user_id, plan),
        "can_confirm": has_permission(context, Permission.WORKSPACE_MANAGE),
        "items": plan["items"], "counts": plan["counts"], "total": plan["total"],
        "rules": {"overwrites": False, "fills_empty_only": True, "matches_master_names_only": True,
                  "changes_other_fields": False},
    }


@router.get("/preview")
def preview(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    require_postgres()
    with workspace_session(context) as session:
        plan = sc.plan_arabic_names(_rows(session, context))
    return _view(context, plan)


@router.post("/confirm")
def confirm(body: ConfirmBody, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_postgres()
    if body.input_mode != "click":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Confirm this on screen with a click. A spoken or typed answer cannot confirm it.")
    try:
        with _store(context) as store:
            # The same per-workspace writer lock as the starter campus: the plan is then read under it.
            store.session.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                                  {"k": f"{context.tenant_id}:{context.workspace_id}:starter-campus"})
            plan = sc.plan_arabic_names(_rows(store.session, context))
            digest = sc.arabic_proposal_hash(context.tenant_id, context.workspace_id, context.user_id, plan)
            if plan["total"] == 0:
                # A retry or a double click after it all has Arabic names: say so, write nothing.
                return {"version": VERSION, "outcome": "nothing_to_add", "payload_hash": digest, "filled": 0,
                        "counts": dict.fromkeys(sc.ARABIC_KINDS, 0), "skipped": 0}
            if not hmac.compare_digest(body.payload_hash, digest):
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "The records changed since you opened this. Review the list again.")
            counts = dict.fromkeys(sc.ARABIC_KINDS, 0)
            ids: list[str] = []
            skipped = 0
            for item in plan["items"]:
                done = store.fill_arabic_name(context, item["kind"], item["id"], item["name"], item["name_ar"])
                if done is None:
                    skipped += 1
                    continue
                counts[item["kind"]] += 1
                ids.append(item["id"])
                if item["kind"] == "rooms":
                    # The room's snapshot holds its Arabic name: record the floor's new head like any room change.
                    store.touched_floors[done[1]["floor_id"]] = "office.room.update"  # type: ignore[attr-defined]
            if ids:
                office_audit(store, context, "office.starter_campus.arabic_names", f"arabic-names:{context.workspace_id}"[:80],
                             kind="buildings", ids=ids,
                             after={"filled": counts, "skipped": skipped, "payload_hash": body.payload_hash,
                                    "input_mode": "click", "catalog_version": sc.CATALOG_VERSION})
            return {"version": VERSION, "outcome": "filled" if ids else "nothing_to_add", "payload_hash": digest,
                    "filled": len(ids), "counts": counts, "skipped": skipped}
    except SpaceStoreError as exc:
        # A stable code and a fixed sentence: the exception text itself never reaches the response.
        logger.warning("arabic names refused", extra={"anum_reason": exc.reason or exc.code})
        raise HTTPException(status.HTTP_409_CONFLICT, "The names could not be added. Nothing was changed. Try again.") from None
