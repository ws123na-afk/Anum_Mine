"""Routes and service for Finance stage 9, wider live use (docs/finance.md#wider-live). OFF.

* ``GET /execution/wider``: the graduation checklist computed from the stage 7 and 8
  records, the current tier, its limits and action types, the hard ceilings, proposals,
  incidents, and whether widening is frozen (``finance:read``).
* ``POST /execution/wider/widenings``: propose the next tier (``finance:manage``); an
  assistant proposes through ``finance.live.widening.propose``. Refused while
  ``ANUM_FINANCE_WIDER_LIVE_ENABLED`` is off, which it always is in this release.
* ``POST .../{id}/approve`` with the payload hash shown: the owner only, once, before it
  expires; it takes effect after the cooling-off. ``.../reject`` declines it.
* ``POST /execution/wider/review``: the owner's review after an incident, which unfreezes.

Nothing here executes anything: the execution gateway refuses every live intent whatever
the tier. Audit records carry ids, tiers and codes, never a limit.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..repository import AnumRepository
from ..dependencies import repository_context, tenant_context
from ..schemas import TenantContext, new_id, utc_now
from . import gateway
from .api import _audit, _clean, _invalid, _manage, _not_found, _read
from .config import finance_config
from .execution import ExecSettings, GlobalSwitch
from .live import LiveLimits
from .store import FinanceStore, open_finance_store
from .wider import (
    LIVE_KINDS,
    WIDENING_TTL,
    Criterion,
    Incident,
    LiveRecord,
    Widening,
    added_kinds,
    ceiling_breaches,
    graduated,
    graduation,
    incident_codes,
    step_down,
    widening_hash,
)

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])
WIDER_OFF = "Wider live use is not enabled. Live execution is not enabled either: nothing real runs, whatever the tier."
SCOPE = {"tenant_id", "workspace_id", "owner_user_id"}


class WiderRefusal(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _records(store: FinanceStore, context: TenantContext) -> list[LiveRecord]:
    out: list[LiveRecord] = []
    while len(out) < 5000:
        page, total = store.list_intents(context, status=None, mode=None, limit=200, offset=len(out))
        out += [LiveRecord(mode=i.mode.value, status=i.status.value, outcome=i.decision.get("outcome"), created_at=i.created_at) for i in page]
        if not page or len(out) >= total:
            break
    return out


def limits_at(store: FinanceStore, context: TenantContext, settings: ExecSettings, tier: int) -> LiveLimits:
    if tier <= 0:
        return settings.live_limits
    approved = [w for w in store.list_widenings(context, status="approved", limit=200) if w.tier == tier]
    return approved[0].limits if approved else settings.live_limits


def refresh(store: FinanceStore, context: TenantContext, now: datetime) -> tuple[ExecSettings, list[Incident]]:
    """Expire lapsed proposals, apply incidents (step down and freeze) and widenings whose
    cooling-off has passed. Runs whenever wider live is read, proposed or decided."""
    settings = store.get_exec_settings(context) or ExecSettings()
    wider = settings.wider
    halt = store.get_global_switch(context) or GlobalSwitch()
    for widening in store.list_widenings(context, status="pending", limit=50):
        if widening.lapsed(now):
            widening.status, widening.decided_at = "expired", now
            store.save_widening(widening)
    since = wider.checked_at or now - timedelta(days=finance_config.wider_window_days)
    new: list[Incident] = []
    for code in incident_codes(since=since, records=_records(store, context), person_switch_at=settings.kill_switch_at,
                               person_switch_on=settings.kill_switch, global_switch_at=halt.changed_at, global_switch_on=halt.engaged):
        before, after = step_down(wider, code, now)
        incident = Incident(id=new_id("fin_inc"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                            code=code, tier_before=before, tier_after=after, created_at=now)  # type: ignore[arg-type]
        store.save_incident(incident)
        new.append(incident)
    if new:
        # An approved step still cooling off never takes effect after an incident.
        for widening in store.list_widenings(context, status="approved", limit=50):
            if widening.effective_at and widening.effective_at > now:
                widening.status, widening.decided_at = "cancelled", now
                store.save_widening(widening)
    if not wider.frozen:
        for widening in sorted(store.list_widenings(context, status="approved", limit=50), key=lambda w: w.tier):
            if widening.effective_at and widening.effective_at <= now and widening.base_tier == wider.tier:
                wider.tier, wider.kinds = widening.tier, sorted(widening.kinds)
    wider.checked_at = now
    settings.updated_at = settings.updated_at or now
    store.save_exec_settings(context, settings)
    return settings, new


def criteria(store: FinanceStore, context: TenantContext, settings: ExecSettings, now: datetime) -> list[Criterion]:
    halt = store.get_global_switch(context) or GlobalSwitch()
    trips = [at for on, at in ((settings.kill_switch, settings.kill_switch_at), (halt.engaged, halt.changed_at)) if at is not None and on]
    trips += [i.created_at for i in store.list_incidents(context, limit=200) if i.code.startswith("kill_switch")]
    ready = all(p.met for p in gateway.preconditions(store, context))
    return graduation(now=now, opted_in_at=settings.live_opt_in_at, records=_records(store, context), kill_switch_at=trips,
                      frozen=settings.wider.frozen, preconditions_met=ready, window_days=finance_config.wider_window_days,
                      min_days=finance_config.wider_min_days, min_actions=finance_config.wider_min_actions)


def propose(
    store: FinanceStore, context: TenantContext, limits: LiveLimits, kinds: list[str], *, proposed_by: Literal["person", "agent"],
    proposer: str, reason: str | None, now: datetime,
) -> Widening:
    if not finance_config.wider_live_enabled:
        raise WiderRefusal("wider_not_enabled", WIDER_OFF)
    settings, _ = refresh(store, context, now)
    wider = settings.wider
    if wider.frozen:
        raise WiderRefusal("frozen", "Widening is frozen after an incident until you review it.")
    if not graduated(criteria(store, context, settings, now)):
        raise WiderRefusal("not_graduated", "The proof for widening is not there yet: see the checklist.")
    tier = wider.tier + 1
    if tier > finance_config.wider_max_tier:
        raise WiderRefusal("max_tier", f"Tier {finance_config.wider_max_tier} is the ceiling.")
    if set(kinds) - set(LIVE_KINDS):
        raise WiderRefusal("unknown_kind", "An action type is trade, transfer, spend or bill_payment.", 422)
    if len(added_kinds(wider.kinds, kinds)) > 1 or set(wider.kinds) - set(kinds):
        raise WiderRefusal("one_kind", "A step adds at most one action type and removes none.", 422)
    breaches = ceiling_breaches(settings.live_limits, limits, tier, max_tier=finance_config.wider_max_tier, step_percent=finance_config.wider_step_percent)
    if breaches:
        raise WiderRefusal("over_ceiling", "Over the ceiling for the next tier: " + ", ".join(breaches) + ".", 422)
    if store.list_widenings(context, status="pending", limit=1):
        raise WiderRefusal("pending", "A step up already waits for your decision.")
    widening = Widening(
        id=new_id("fin_wide"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        base_tier=wider.tier, tier=tier, limits=limits, kinds=sorted(set(kinds)), proposed_by=proposed_by, proposer=proposer[:160],
        reason=reason, payload_hash="0" * 64, created_at=now, expires_at=now + WIDENING_TTL,
    )
    widening.payload_hash = widening_hash(widening)
    return store.save_widening(widening)


def approve(store: FinanceStore, context: TenantContext, widening_id: str, shown_hash: str, now: datetime) -> Widening:
    if not finance_config.wider_live_enabled:
        raise WiderRefusal("wider_not_enabled", WIDER_OFF)
    settings, _ = refresh(store, context, now)
    widening = store.get_widening(context, widening_id)
    if widening is None:
        raise WiderRefusal("not_found", "Step not found.", 404)
    if widening.status != "pending":
        raise WiderRefusal("not_pending", f"This step is {widening.status}. Propose it again.")
    if shown_hash != widening.payload_hash or shown_hash != widening_hash(widening):
        raise WiderRefusal("hash_mismatch", "The approval does not match the step shown. Reload it and approve again.")
    if settings.wider.frozen:
        raise WiderRefusal("frozen", "Widening is frozen after an incident until you review it.")
    if widening.base_tier != settings.wider.tier:
        widening.status, widening.decided_at = "superseded", now
        store.save_widening(widening)
        raise WiderRefusal("stale", "Your tier changed since this was proposed. Review it again.")
    if not graduated(criteria(store, context, settings, now)):
        raise WiderRefusal("not_graduated", "The proof for widening is not there now: see the checklist.")
    effective = now + timedelta(hours=finance_config.wider_cooling_hours)
    if not store.claim_widening(context, widening.id, now, context.user_id, effective):
        raise WiderRefusal("already_used", "This approval was used already.")
    widening.status, widening.decided_at, widening.decided_by, widening.effective_at = "approved", now, context.user_id, effective
    return widening


# -- views and routes -----------------------------------------------------------------------------------


class WideningView(BaseModel):
    id: str
    base_tier: int
    tier: int
    limits: LiveLimits
    kinds: list[str]
    proposed_by: Literal["person", "agent"]
    reason: str | None
    payload_hash: str
    status: str
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    effective_at: datetime | None


class IncidentView(BaseModel):
    id: str
    code: str
    tier_before: int
    tier_after: int
    created_at: datetime
    reviewed_at: datetime | None


class Ceilings(BaseModel):
    max_tier: int
    step_percent: int
    cooling_hours: int
    window_days: int


class WiderView(BaseModel):
    enabled: bool
    live_enabled: bool
    tier: int
    kinds: list[str]
    limits: LiveLimits
    frozen: bool
    frozen_at: datetime | None
    frozen_reason: str | None
    graduated: bool
    criteria: list[Criterion]
    ceilings: Ceilings
    widenings: list[WideningView]
    incidents: list[IncidentView]
    note: str


class WideningInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limits: dict[str, Any]
    kinds: list[str] = Field(default_factory=lambda: ["trade"], max_length=4)
    reason: str | None = Field(default=None, max_length=300)


class ApproveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def _widening_view(w: Widening) -> WideningView:
    return WideningView(**w.model_dump(exclude=SCOPE | {"proposer", "decided_by"}))


def wider_view(store: FinanceStore, context: TenantContext, now: datetime) -> WiderView:
    settings, _ = refresh(store, context, now)
    found = criteria(store, context, settings, now)
    wider = settings.wider
    return WiderView(
        enabled=finance_config.wider_live_enabled, live_enabled=False, tier=wider.tier, kinds=wider.kinds,
        limits=limits_at(store, context, settings, wider.tier), frozen=wider.frozen, frozen_at=wider.frozen_at, frozen_reason=wider.frozen_reason,
        graduated=graduated(found), criteria=found,
        ceilings=Ceilings(max_tier=finance_config.wider_max_tier, step_percent=finance_config.wider_step_percent,
                          cooling_hours=finance_config.wider_cooling_hours, window_days=finance_config.wider_window_days),
        widenings=[_widening_view(w) for w in store.list_widenings(context, status=None, limit=20)],
        incidents=[IncidentView(**i.model_dump(exclude=SCOPE)) for i in store.list_incidents(context, limit=20)], note=WIDER_OFF,
    )


def _refused(exc: WiderRefusal) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/execution/wider", response_model=WiderView)
def get_wider(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> WiderView:
    """Graduation checklist, tier, ceilings, proposals and incidents. Wider live use is off."""
    _read(context)
    now = utc_now()
    with open_finance_store(context) as store:
        settings_before = store.get_exec_settings(context) or ExecSettings()
        view = wider_view(store, context, now)
    if view.frozen and not settings_before.wider.frozen and view.incidents:
        _audit(repository, context, "finance.wider.step_down", context.user_id, {"code": view.incidents[0].code, "tier": view.tier})
    return view


@router.post("/execution/wider/widenings", response_model=WideningView, status_code=status.HTTP_201_CREATED)
def propose_widening(body: WideningInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> WideningView:
    _manage(context)
    try:
        limits = LiveLimits.model_validate(body.limits)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise _invalid(f"Limits: {first.get('msg', 'not valid')}".replace("Value error, ", "")) from None
    with open_finance_store(context) as store:
        try:
            widening = propose(store, context, limits, body.kinds, proposed_by="person", proposer=context.user_id,
                               reason=_clean(body.reason, 300) if body.reason else None, now=utc_now())
        except WiderRefusal as exc:
            refusal: WiderRefusal | None = exc
        else:
            refusal = None
    if refusal is not None:
        _audit(repository, context, "finance.wider.propose.refused", context.user_id, {"code": refusal.code})
        raise _refused(refusal)
    _audit(repository, context, "finance.wider.propose", widening.id, {"tier": widening.tier, "by": "person"})
    return _widening_view(widening)


@router.post("/execution/wider/widenings/{widening_id}/approve", response_model=WideningView)
def approve_widening(widening_id: str, body: ApproveInput, context: TenantContext = Depends(tenant_context),
                     repository: AnumRepository = Depends(repository_context)) -> WideningView:
    """The owner's approval with the code shown: used once; takes effect after the cooling-off."""
    _manage(context)
    with open_finance_store(context) as store:
        try:
            widening = approve(store, context, widening_id, body.payload_hash, utc_now())
        except WiderRefusal as exc:
            refusal: WiderRefusal | None = exc
        else:
            refusal = None
    if refusal is not None:
        _audit(repository, context, "finance.wider.approve.refused", widening_id, {"code": refusal.code})
        raise _refused(refusal)
    _audit(repository, context, "finance.wider.approve", widening.id, {"tier": widening.tier})
    return _widening_view(widening)


@router.post("/execution/wider/widenings/{widening_id}/reject", response_model=WideningView)
def reject_widening(widening_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> WideningView:
    _manage(context)
    with open_finance_store(context) as store:
        widening = store.get_widening(context, widening_id)
        if widening is None:
            raise _not_found("Step")
        if widening.status != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, f"This step is {widening.status} already.")
        widening.status, widening.decided_at, widening.decided_by = "rejected", utc_now(), context.user_id
        store.save_widening(widening)
    _audit(repository, context, "finance.wider.reject", widening.id, {"tier": widening.tier})
    return _widening_view(widening)


@router.post("/execution/wider/review", response_model=WiderView)
def review_incidents(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> WiderView:
    """You reviewed the incidents: widening is no longer frozen (the tier stays where it dropped to)."""
    _manage(context)
    now = utc_now()
    with open_finance_store(context) as store:
        settings, _ = refresh(store, context, now)
        reviewed = 0
        for incident in store.list_incidents(context, limit=200):
            if incident.reviewed_at is None:
                incident.reviewed_at = now
                store.save_incident(incident)
                reviewed += 1
        settings.wider.frozen, settings.wider.frozen_at, settings.wider.frozen_reason, settings.wider.reviewed_at = False, None, None, now
        store.save_exec_settings(context, settings)
        view = wider_view(store, context, now)
    _audit(repository, context, "finance.wider.review", context.user_id, {"incidents": reviewed, "tier": view.tier})
    return view
