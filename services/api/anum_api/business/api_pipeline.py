"""Routes of the prospecting pipeline under ``/api/v1/business/pipeline`` (docs/business.md#prospecting-pipeline).

Signals (the owner confirms what is collected), the pipeline settings and today's usage,
discovery and profile runs (estimate, confirm, progress, stop) and a lead's profile: its
observations (facts), inferences and the deterministic score suggestions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query as Q
from pydantic import BaseModel, Field

from ..dependencies import tenant_context
from ..schemas import TenantContext, utc_now
from . import pipeline, signals
from .common import Page, get_or_404, manage, page_bounds, read, write
from .models import Criterion, Evidence, Inference
from .outreach_models import Estimate, Observation, OutreachSettings, PipelineRun, RunKind, RunStep, SignalDecision, SignalStatus, TargetProfile, Usage
from .service import audit, http_error, score_totals
from ..schemas import new_id
from .store import Query, open_business_store

router = APIRouter(prefix="/api/v1/business/pipeline", tags=["business"])


class SignalView(BaseModel):
    code: str
    label: str
    description: str
    category: str
    value_type: str
    cost_note: str
    criterion: Criterion | None
    status: SignalStatus
    decided_by: str | None = None
    decided_at: datetime | None = None


class PipelineSettingsView(BaseModel):
    fetches_per_day: int
    pages_per_lead: int
    sending_enabled: bool
    daily_quota: dict[str, int]
    max_bounce_rate_pct: float
    follow_up_max: int
    follow_up_min_days: int
    paused: bool
    paused_reason: str
    paused_at: datetime | None
    research_searches_per_day: int
    research_runs_per_day: int
    usage_today: dict[str, int]
    tools: dict[str, bool]
    model_connected: bool
    can_manage: bool
    excluded: list[str]


EXCLUDED = [
    "LinkedIn or any browser automation",
    "Paid data providers (listings, rankings, enrichment)",
    "Reading pages behind a login or a captcha",
    "Personal phone numbers, people scraped from team pages, guessed e-mail addresses",
    "Autonomous outbound: every message waits for an owner's approval",
]


def _signal_views(settings: OutreachSettings) -> list[SignalView]:
    out: list[SignalView] = []
    for spec in signals.CATALOG:
        decision = settings.signals.get(spec.code) or SignalDecision()
        out.append(SignalView(code=spec.code, label=spec.label, description=spec.description, category=spec.category, value_type=spec.value_type,
                              cost_note=spec.cost_note, criterion=spec.criterion, status=decision.status, decided_by=decision.decided_by,
                              decided_at=decision.decided_at))
    return out


def _settings_view(context: TenantContext) -> PipelineSettingsView:
    from ..authorization import Permission
    from ..dependencies import has_permission
    from . import assist

    with open_business_store(context) as store:
        settings = pipeline.load_settings(store, context)
        base = store.get_settings()
        used = pipeline.usage(store, utc_now())
    return PipelineSettingsView(
        fetches_per_day=settings.fetches_per_day, pages_per_lead=settings.pages_per_lead, sending_enabled=settings.sending_enabled,
        daily_quota=settings.daily_quota, max_bounce_rate_pct=settings.max_bounce_rate_pct, follow_up_max=settings.follow_up_max,
        follow_up_min_days=settings.follow_up_min_days, paused=settings.paused, paused_reason=settings.paused_reason, paused_at=settings.paused_at,
        research_searches_per_day=base.research_searches_per_day, research_runs_per_day=base.research_runs_per_day, usage_today=used,
        tools=pipeline.tools_available(), model_connected=assist.model_connected(context),
        can_manage=has_permission(context, Permission.BUSINESS_MANAGE), excluded=EXCLUDED,
    )


@router.get("/signals", response_model=list[SignalView])
def list_signals(context: TenantContext = Depends(tenant_context)) -> list[SignalView]:
    read(context)
    with open_business_store(context) as store:
        return _signal_views(pipeline.load_settings(store, context))


class SignalDecisions(BaseModel):
    decisions: dict[str, Literal["proposed", "confirmed", "rejected"]] = Field(min_length=1, max_length=40)


@router.put("/signals", response_model=list[SignalView])
def decide_signals(payload: SignalDecisions, context: TenantContext = Depends(tenant_context)) -> list[SignalView]:
    """An owner confirms or rejects what the pipeline collects; nothing unconfirmed is ever collected."""
    manage(context)
    unknown = sorted(set(payload.decisions) - signals.CODES)
    if unknown:
        raise http_error(422, f"Unknown signal(s): {', '.join(unknown)}.")
    now = utc_now()
    with open_business_store(context) as store:
        settings = pipeline.load_settings(store, context)
        for code, status in payload.decisions.items():
            settings.signals[code] = SignalDecision(status=SignalStatus(status), decided_by=context.user_id, decided_at=now)
        settings.updated_by = context.user_id
        pipeline.save_settings(store, settings)
        views = _signal_views(settings)
    audit(None, context, "business.pipeline.signals", settings.id, metadata={"confirmed": len(settings.confirmed()), "changed": len(payload.decisions)})
    return views


class TargetProfileInput(BaseModel):
    industries: list[str] = Field(default_factory=list, max_length=20)
    company_sizes: list[Literal["micro", "small", "medium", "large", "enterprise"]] = Field(default_factory=list, max_length=5)
    regions: list[str] = Field(default_factory=list, max_length=20)
    roles: list[str] = Field(default_factory=list, max_length=20)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    disqualifiers: list[str] = Field(default_factory=list, max_length=30)
    trigger_events: list[str] = Field(default_factory=list, max_length=20)
    company_size_note: str = Field(default="", max_length=300)
    value_proposition: str = Field(default="", max_length=1000)
    value_proposition_ar: str = Field(default="", max_length=1000)


class TargetProfileView(TargetProfileInput):
    product_id: str
    product_name: str
    saved: bool
    updated_by: str | None = None
    updated_at: datetime | None = None


def _profile_view(product: Any, profile: TargetProfile | None) -> TargetProfileView:
    if profile is None:
        default = TargetProfile(id="-", tenant_id="-", workspace_id="-", product_id=product.id, created_at=utc_now(), updated_at=utc_now())
        fields = {**TargetProfileInput.model_validate(default.model_dump()).model_dump(), "value_proposition": product.summary[:1000]}
        return TargetProfileView(**fields, product_id=product.id, product_name=product.name, saved=False)
    return TargetProfileView(**TargetProfileInput.model_validate(profile.model_dump()).model_dump(), product_id=product.id, product_name=product.name,
                             saved=True, updated_by=profile.updated_by, updated_at=profile.updated_at)


@router.get("/targets", response_model=list[TargetProfileView])
def list_targets(context: TenantContext = Depends(tenant_context)) -> list[TargetProfileView]:
    """Every product with its target customer profile (unsaved profiles show the GCC defaults)."""
    read(context)
    with open_business_store(context) as store:
        products, _ = store.find("products", Query(limit=100, order_by="name", descending=False))
        return [_profile_view(product, store.first("target_profiles", product_id=product.id)) for product in products]


@router.put("/products/{product_id}/target", response_model=TargetProfileView)
def save_target(product_id: str, payload: TargetProfileInput, context: TenantContext = Depends(tenant_context)) -> TargetProfileView:
    """Owners: who this product is for. Discovery, product fit and drafts read it."""
    manage(context)
    from .common import clean_list

    now = utc_now()
    with open_business_store(context) as store:
        product = get_or_404(store, "products", product_id, "Product")
        profile = store.first("target_profiles", product_id=product.id)
        values: dict[str, Any] = {name: clean_list(getattr(payload, name), 120) for name in ("industries", "regions", "roles", "keywords", "disqualifiers")}
        values["trigger_events"] = clean_list(payload.trigger_events, 300)
        values["company_size_note"] = " ".join(payload.company_size_note.split())
        values["company_sizes"] = list(dict.fromkeys(payload.company_sizes))
        values["value_proposition"] = " ".join(payload.value_proposition.split())
        values["value_proposition_ar"] = " ".join(payload.value_proposition_ar.split())
        if profile is None:
            profile = TargetProfile(id=new_id("btgt"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, product_id=product.id,
                                    created_at=now, updated_at=now, updated_by=context.user_id, **values)
            store.add("target_profiles", profile)
        else:
            profile = profile.model_copy(update={**values, "updated_by": context.user_id, "updated_at": now})
            store.save("target_profiles", profile)
        view = _profile_view(product, profile)
    audit(None, context, "business.pipeline.target", product_id, metadata={"industries": len(view.industries), "regions": len(view.regions),
                                                                         "keywords": len(view.keywords), "disqualifiers": len(view.disqualifiers)})
    return view


@router.get("/settings", response_model=PipelineSettingsView)
def get_settings(context: TenantContext = Depends(tenant_context)) -> PipelineSettingsView:
    read(context)
    return _settings_view(context)


class SettingsUpdate(BaseModel):
    fetches_per_day: int | None = Field(default=None, ge=0, le=2000)
    pages_per_lead: int | None = Field(default=None, ge=1, le=8)
    sending_enabled: bool | None = None
    daily_quota: dict[Literal["email", "whatsapp"], int] | None = None
    max_bounce_rate_pct: float | None = Field(default=None, ge=0, le=50)
    follow_up_max: int | None = Field(default=None, ge=0, le=5)
    follow_up_min_days: int | None = Field(default=None, ge=1, le=60)
    paused: bool | None = None
    paused_reason: str | None = Field(default=None, max_length=300)


@router.put("/settings", response_model=PipelineSettingsView)
def update_settings(payload: SettingsUpdate, context: TenantContext = Depends(tenant_context)) -> PipelineSettingsView:
    """Owners: caps, sending (off by default), quotas, the bounce threshold and the pause."""
    manage(context)
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        raise http_error(422, "Change at least one setting.")
    if payload.daily_quota is not None and any(value < 0 or value > 500 for value in payload.daily_quota.values()):
        raise http_error(422, "A daily quota is between 0 and 500 messages.")
    if payload.paused and len((payload.paused_reason or "").strip()) < 3:
        raise http_error(422, "Say why outreach and research are paused (at least 3 characters).")
    with open_business_store(context) as store:
        settings = pipeline.load_settings(store, context)
        for name, value in changes.items():
            if name == "daily_quota":
                settings.daily_quota = {**settings.daily_quota, **value}
            elif name == "paused":
                settings.paused = value
                settings.paused_at = utc_now() if value else None
                if not value:
                    settings.paused_reason = ""
            elif name == "paused_reason":
                if payload.paused is not False:
                    settings.paused_reason = value.strip()
            else:
                setattr(settings, name, value)
        settings.updated_by = context.user_id
        pipeline.save_settings(store, settings)
    audit(None, context, "business.pipeline.settings", settings.id, metadata={"changed": sorted(changes), "sending_enabled": settings.sending_enabled,
                                                                              "paused": settings.paused})
    return _settings_view(context)


class RunView(BaseModel):
    id: str
    kind: RunKind
    status: str
    product_id: str | None
    plan: dict[str, Any]
    estimate: Estimate
    used: Usage
    steps: list[RunStep]
    results: dict[str, Any]
    stop_requested: bool
    stop_reason: str
    confirmed_by: str | None
    confirmed_at: datetime | None
    finished_at: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime


def run_view(run: PipelineRun) -> RunView:
    data = run.model_dump()
    data["status"] = pipeline.view_status(run)
    return RunView(**{key: data[key] for key in RunView.model_fields})


class RunCreate(BaseModel):
    kind: RunKind
    product_id: str | None = None
    terms: list[str] = Field(default_factory=list, max_length=pipeline.MAX_TERMS)
    places: list[str] = Field(default_factory=list, max_length=pipeline.MAX_PLACES)
    max_queries: int = Field(default=5, ge=1, le=pipeline.MAX_QUERIES)
    max_new_leads: int = Field(default=10, ge=1, le=pipeline.MAX_NEW_LEADS)
    lead_ids: list[str] = Field(default_factory=list, max_length=pipeline.MAX_PROFILE_LEADS)


def _refused(exc: pipeline.PipelineRefused) -> Exception:
    return http_error(exc.status_code, exc.detail)


@router.post("/runs/estimate", response_model=RunView, status_code=201)
def estimate_run(payload: RunCreate, context: TenantContext = Depends(tenant_context)) -> RunView:
    """Plan and estimate a run; nothing is called until it is confirmed."""
    write(context)
    try:
        run = pipeline.plan_run(context, payload.kind, product_id=payload.product_id, terms=payload.terms, places=payload.places,
                                max_queries=payload.max_queries, max_new_leads=payload.max_new_leads, lead_ids=payload.lead_ids)
    except pipeline.PipelineRefused as exc:
        raise _refused(exc) from None
    return run_view(run)


@router.post("/runs/{run_id}/confirm", response_model=RunView)
def confirm_run(run_id: str, background: BackgroundTasks, context: TenantContext = Depends(tenant_context)) -> RunView:
    """Confirm the estimate and start the run (or resume an interrupted one); poll the run for progress."""
    write(context)
    try:
        run = pipeline.confirm(context, run_id)
    except pipeline.PipelineRefused as exc:
        raise _refused(exc) from None
    background.add_task(pipeline.execute, context, run.id)
    return run_view(run)


@router.post("/runs/{run_id}/stop", response_model=RunView)
def stop_run(run_id: str, context: TenantContext = Depends(tenant_context)) -> RunView:
    write(context)
    try:
        return run_view(pipeline.request_stop(context, run_id))
    except pipeline.PipelineRefused as exc:
        raise _refused(exc) from None


@router.get("/runs", response_model=Page[RunView])
def list_runs(kind: RunKind | None = None, limit: int = Q(20), offset: int = Q(0), context: TenantContext = Depends(tenant_context)) -> Page[RunView]:
    read(context)
    limit, offset = page_bounds(limit, offset)
    with open_business_store(context) as store:
        items, total = store.find("pipeline_runs", Query(where={"kind": kind} if kind else {}, order_by="created_at", limit=limit, offset=offset))
    return Page(items=[run_view(item) for item in items], total=total, limit=limit, offset=offset)


class LeadBrief(BaseModel):
    id: str
    title: str
    state: str
    account_name: str
    domain: str | None


class RunDetail(RunView):
    leads: list[LeadBrief]
    evidence: list[Evidence]
    observations: list[Observation]


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, context: TenantContext = Depends(tenant_context)) -> RunDetail:
    read(context)
    with open_business_store(context) as store:
        run = get_or_404(store, "pipeline_runs", run_id, "Run")
        lead_ids = list(run.results.get("lead_ids", [])) if run.kind == RunKind.DISCOVER else list(run.plan.get("lead_ids", []))
        leads: list[LeadBrief] = []
        for lead_id in lead_ids[:50]:
            lead = store.get("leads", lead_id)
            if lead is None:
                continue
            account = store.get("accounts", lead.account_id)
            leads.append(LeadBrief(id=lead.id, title=lead.title, state=lead.state.value, account_name=account.name if account else "", domain=account.domain if account else None))
        evidence, _ = store.find("evidence", Query(where={"research_run_id": run.id}, limit=200, order_by="created_at"))
        observations, _ = store.find("observations", Query(where={"run_id": run.id}, limit=500, order_by="created_at"))
    return RunDetail(**run_view(run).model_dump(), leads=leads, evidence=evidence, observations=observations)


class SuggestionView(BaseModel):
    criterion: Criterion
    score: int
    reason: str
    rule: str
    observation_ids: list[str]
    evidence_ids: list[str]


class CriterionLine(BaseModel):
    criterion: Criterion
    score: int | None
    reason: str
    evidence_ids: list[str]
    known: bool
    conversation_only: bool
    suggestion: SuggestionView | None


class FitView(BaseModel):
    product_id: str
    product_name: str
    score: int | None
    disqualified: bool
    reasons: list[str]
    current: bool = False


class ProfileView(BaseModel):
    lead_id: str
    score_total: int
    score_known: int
    product_fits: list[FitView]
    best_fit: FitView | None
    breakdown: list[CriterionLine]
    observations: list[Observation]
    facts: list[Evidence]
    inferences: list[Inference]
    signal_labels: dict[str, str]


@router.get("/leads/{lead_id}/profile", response_model=ProfileView)
def lead_profile(lead_id: str, context: TenantContext = Depends(tenant_context)) -> ProfileView:
    """Facts (evidence and observations) apart from inferences, and the score with deterministic suggestions."""
    read(context)
    from .models import CONVERSATION_CRITERIA

    with open_business_store(context) as store:
        lead = get_or_404(store, "leads", lead_id, "Lead")
        observations, _ = store.find("observations", Query(where={"lead_id": lead.id}, limit=500, order_by="created_at"))
        evidence, _ = store.find("evidence", Query(where={"lead_id": lead.id}, limit=200, order_by="created_at"))
        inferences, _ = store.find("inferences", Query(where={"lead_id": lead.id}, limit=200, order_by="created_at"))
        fits = [FitView(product_id=fit.product_id, product_name=fit.product_name, score=fit.score, disqualified=fit.disqualified,
                        reasons=fit.reasons, current=fit.product_id == lead.product_id) for fit in pipeline.product_fits(store, lead)]
    best = next((fit for fit in fits if fit.score is not None and not fit.disqualified and fit.score > 0), None)
    suggestions = {item.criterion: item for item in signals.suggest_scores(observations)}
    breakdown: list[CriterionLine] = []
    for criterion in Criterion:
        current = lead.score.get(criterion.value)
        suggestion = suggestions.get(criterion)
        breakdown.append(CriterionLine(
            criterion=criterion, score=current.score if current else None, reason=current.reason if current else "",
            evidence_ids=list(current.evidence_ids) if current else [], known=bool(current and current.score is not None),
            conversation_only=criterion in CONVERSATION_CRITERIA,
            suggestion=SuggestionView(criterion=suggestion.criterion, score=suggestion.score, reason=suggestion.reason, rule=suggestion.rule,
                                      observation_ids=suggestion.observation_ids, evidence_ids=suggestion.evidence_ids) if suggestion else None,
        ))
    total, known = score_totals(lead.score)
    return ProfileView(lead_id=lead.id, score_total=total, score_known=known, product_fits=fits, best_fit=best, breakdown=breakdown, observations=observations, facts=evidence,
                       inferences=inferences, signal_labels={spec.code: spec.label for spec in signals.CATALOG})
