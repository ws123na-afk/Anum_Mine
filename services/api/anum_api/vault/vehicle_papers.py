"""An insurance or registration document in the vault and the vehicle it belongs to (docs/documents.md#vehicle-papers).

A vault document of category ``insurance`` or ``vehicle_registration`` with an expiry date is proposed to the caller's
vehicles they may change (docs/vehicles.md#who-can-do-what): first those whose registration or VIN appears in the
document's title, issuer, description or tags, then the rest. Nothing is linked by itself: the person picks one.

Confirming (``POST /documents/{id}/vehicle``) does two things, each in its own short unit of work (never nested):

* the vehicle's matching expiry (``insurance_expires_on`` or ``registration_expires_on``) becomes the document's expiry,
  through the vehicle's own version check (``409 stale`` when it changed meanwhile), so the vehicle's renewal reminder
  (``vehicles.jobs``) follows that date from the next daily run, at the person's reminder days;
* the document gets a ``vehicle`` link to it, shown in the vault and on the vehicle's papers.

Needs the document's edit right (``document:write``, owner or editor) and the vehicle's (``vehicle:write``, its owner or
a business vehicle's manager). Reads only otherwise.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from fastapi import HTTPException

from ..authorization import Permission
from ..dependencies import has_permission
from ..schemas import TenantContext

PAPER_FIELDS = {"insurance": "insurance_expires_on", "vehicle_registration": "registration_expires_on"}
MAX_VEHICLES = 200
_WORD = re.compile(r"[^A-Z0-9]")


def _squash(text: str) -> str:
    return _WORD.sub("", text.upper())


def _label(vehicle: Any) -> str:
    named = vehicle.nickname or " ".join(part for part in (vehicle.make, vehicle.model) if part)
    return f"{named} ({vehicle.registration})" if named else vehicle.registration


def proposals(context: TenantContext, document_id: str) -> dict[str, Any]:
    """The vehicles this document may be the insurance or registration of, for the person who asks."""
    from ..vehicles import service as VS
    from ..vehicles.store import open_vehicle_store
    from .service import can_edit, get_visible
    from .store import open_vault_unit

    with open_vault_unit(context) as unit:
        doc = get_visible(unit, context, document_id)
        links = unit.store.links(context, document_id)
    field = PAPER_FIELDS.get(doc.category)
    linked = sorted(link.target_id for link in links if link.target_type == "vehicle")
    out: dict[str, Any] = {"document_id": document_id, "category": doc.category, "field": field, "expiry_date": doc.expiry_date.isoformat() if doc.expiry_date else None,
                           "reason": None, "can_edit": can_edit(context, doc), "vehicles": [], "vehicles_unavailable": None, "linked_vehicles": linked}
    if field is None:
        out["reason"] = "not_a_vehicle_paper"
        return out
    if doc.expiry_date is None:
        out["reason"] = "no_expiry"
        return out
    if doc.status != "active":
        out["reason"] = "in_trash"
        return out
    if not has_permission(context, Permission.VEHICLE_WRITE):
        out["vehicles_unavailable"] = "vehicle_access"
        return out
    text = _squash(" ".join([doc.title, doc.issuer or "", doc.description, *doc.tags]))
    with open_vehicle_store(context) as store:
        found, _ = store.list_vehicles(context, query=None, archived=False, limit=MAX_VEHICLES, offset=0)
        prefs = VS.preferences(store, context)
    items = []
    for vehicle in found:
        if not VS.can_edit_vehicle(context, vehicle):
            continue
        keys = [_squash(vehicle.registration), _squash(vehicle.vin or "")]
        matched = any(len(key) >= 3 and key in text for key in keys)
        current = getattr(vehicle, field)
        items.append({"vehicle_id": vehicle.id, "label": _label(vehicle), "registration": vehicle.registration, "ownership": str(getattr(vehicle.ownership, "value", vehicle.ownership)), "current_expiry": current.isoformat() if current else None,
                      "would_change": current != doc.expiry_date, "matches_registration": matched, "linked": vehicle.id in linked})
    items.sort(key=lambda item: (not item["matches_registration"], not item["linked"], item["label"].casefold(), item["vehicle_id"]))
    out["vehicles"] = items
    out["reminder_from"] = (doc.expiry_date - timedelta(days=prefs.reminder_days)).isoformat()
    out["reminder_days"] = prefs.reminder_days
    return out


def _set_expiry(context: TenantContext, vehicle_id: str, field: str, expiry: date) -> None:
    from ..schemas import utc_now
    from ..vehicles import service as VS
    from ..vehicles.store import open_vehicle_store

    with open_vehicle_store(context) as store:
        vehicle = VS.get_vehicle(store, context, vehicle_id)
        VS.require_vehicle_edit(context, vehicle)
        if getattr(vehicle, field) == expiry:
            return
        updated = VS.apply_vehicle_changes(vehicle, {field: expiry}, utc_now())
        if not store.save_vehicle(updated, vehicle.version):
            raise VS.VehicleError(409, "The vehicle changed while you linked the document. Reload and try again.", "stale")


def confirm(context: TenantContext, document_id: str, vehicle_id: str) -> dict[str, Any]:
    """Link the document to one proposed vehicle and set that vehicle's expiry (its renewal reminder) from it."""
    from ..vehicles import service as VS
    from .service import EDIT_DENIED, add_link, log_event
    from .store import open_vault_unit

    current = proposals(context, document_id)
    if not current["can_edit"]:
        raise HTTPException(403, EDIT_DENIED)
    if current["reason"] is not None:
        raise HTTPException(409, {"not_a_vehicle_paper": "Only an insurance or vehicle registration document is linked to a vehicle this way.",
                                  "no_expiry": "Add the document's expiry date first: the vehicle's renewal reminder follows it.",
                                  "in_trash": "Restore the document from the trash first."}[current["reason"]])
    if current["vehicles_unavailable"]:
        raise HTTPException(403, "You cannot change vehicles in this workspace. Ask an owner for vehicle access.")
    chosen = next((item for item in current["vehicles"] if item["vehicle_id"] == vehicle_id), None)
    if chosen is None:
        raise HTTPException(404, "Vehicle not found among the vehicles you may change.")
    field = current["field"]
    try:
        _set_expiry(context, vehicle_id, field, date.fromisoformat(current["expiry_date"]))
    except VS.VehicleError as exc:
        raise HTTPException(exc.status_code, exc.message) from None
    if not chosen["linked"]:
        add_link(context, document_id, "vehicle", vehicle_id, chosen["registration"])
    with open_vault_unit(context) as unit:
        log_event(unit, context, document_id, "vehicle_paper_confirmed", {"field": field})
    return proposals(context, document_id)


__all__ = ["PAPER_FIELDS", "confirm", "proposals"]
