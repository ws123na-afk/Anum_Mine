"""``/api/v1/learning``: what ANUM learned from the caller (docs/learning.md).

Every route acts on the authenticated caller's own learning data in the request's
workspace: there is no user id in any path or body, and the store is bound to the
caller's tenant, workspace and user (forced RLS with PostgreSQL). Any active member may
read, edit and delete their own; changes that reach beyond it are checked here, on the
server: accepting a routine creates an automation (``automation:manage``) and applying
an autonomy suggestion changes the workspace approval policy (``policy:manage``, owners).
Settings changes, resets, routine decisions and applied suggestions are audited.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import cron
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, repository_context, require_permission, tenant_context
from .learning import (
    CLIENT_SURFACES,
    DRAFT_KINDS,
    MAX_DRAFT_CHARS,
    MAX_PREFERENCE_CHARS,
    Activity,
    ImprovementNote,
    LearnedPreference,
    LearningProfile,
    LearningSignal,
    LearningState,
    Routine,
    RoutineStatus,
    RunFailure,
    SignalKind,
    StyleOverrides,
    StyleProfile,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    Surface,
    activity_key,
    detect_routines,
    effective_style,
    improvement_suggestions,
    iso_week,
    note_summary,
    summarize_edit,
)
from .learning_store import LearningStore, learning_state, new_signal, open_learning_store, prune, rederive
from .repository import AnumRepository
from .request_context import current_correlation_id
from .schemas import TaskStatus, TenantContext, WorkspaceApprovalPolicy, new_id, utc_now

router = APIRouter(prefix="/api/v1/learning", tags=["learning"])
logger = logging.getLogger("anum.learning")

PREVIEW_CHARS = 500
MAX_PAGE = 100
ACTIVITY_LOOKBACK_WEEKS = 8
MAX_ACTIVITY_TASKS = 2000
MAX_FAILED_RUNS = 100

STATE_MESSAGES = {
    LearningState.OFF_BY_USER: "Learning is off. Turn on Learn from my activity to use this.",
    LearningState.OFF_CHAT_MEMORY: (
        "Learning follows Remember things from my chats, which is off. Turn it on in Settings to use this."
    ),
    LearningState.OFF_ON_SERVER: "Learning is turned off on this server (ANUM_LEARNING_ENABLED).",
}


# --------------------------------------------------------------------------- views


class ProfileView(BaseModel):
    enabled: bool
    derived: StyleProfile
    overrides: StyleOverrides
    effective: StyleProfile
    derived_at: datetime | None = None
    updated_at: datetime | None = None


class LearningCounts(BaseModel):
    signals: dict[str, int] = Field(default_factory=dict)
    preferences: int = 0
    routines_proposed: int = 0
    suggestions_open: int = 0


class LearningOverview(BaseModel):
    state: LearningState = Field(
        description="on; off_by_user (Learn from my activity is off); off_chat_memory (Remember things from my chats is off); "
        "off_on_server (ANUM_LEARNING_ENABLED=false)"
    )
    profile: ProfileView
    counts: LearningCounts
    can_manage_automations: bool
    can_apply_autonomy: bool


class LearningSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(description="Learn from my activity")


class SignalView(LearningSignal):
    truncated: bool = False


class SignalPage(BaseModel):
    items: list[SignalView]
    total: int
    limit: int
    offset: int


class FeedbackCreate(BaseModel):
    """A rating, or a draft with what the user actually sent (the edit is the signal)."""

    model_config = ConfigDict(extra="forbid")

    surface: Surface
    subject_id: str | None = Field(default=None, max_length=120)
    rating: Literal["up", "down"] | None = None
    original: str | None = Field(default=None, max_length=MAX_DRAFT_CHARS)
    final: str | None = Field(default=None, max_length=MAX_DRAFT_CHARS)
    contact: str | None = Field(default=None, max_length=160)
    skill: str | None = Field(default=None, max_length=120)
    tool: str | None = Field(default=None, max_length=120)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _one_kind(self) -> FeedbackCreate:
        if self.surface not in CLIENT_SURFACES:
            raise ValueError("Feedback is for chat, email, whatsapp or task drafts")
        is_draft = self.original is not None or self.final is not None
        if (self.rating is None) == (not is_draft):
            raise ValueError("Send either a rating, or original and final text")
        if is_draft and (not (self.original or "").strip() or not (self.final or "").strip()):
            raise ValueError("A draft needs both the original and the final text")
        if self.rating is not None and not self.subject_id:
            raise ValueError("A rating needs the subject_id of what was rated")
        return self


class FeedbackResult(BaseModel):
    recorded: bool
    state: LearningState
    signal: SignalView | None = None
    message: str | None = None


class RatingView(BaseModel):
    subject_id: str
    rating: Literal["up", "down"]
    signal_id: str
    reason: str | None = None


class RatingsView(BaseModel):
    ratings: list[RatingView]


class PreferenceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = Field(default="all", min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=MAX_PREFERENCE_CHARS)

    @field_validator("text")
    @classmethod
    def _one_line(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Write the preference")
        return cleaned

    @field_validator("scope")
    @classmethod
    def _known_scope(cls, value: str) -> str:
        value = value.strip()
        if value == "all":
            return value
        kind, _, target = value.partition(":")
        if kind not in {"surface", "skill", "tool", "contact"} or not target.strip():
            raise ValueError("Scope is all, surface:<name>, skill:<id>, tool:<name> or contact:<key>")
        if kind == "surface" and target not in {"chat", "email", "whatsapp", "task"}:
            raise ValueError("Surface is chat, email, whatsapp or task")
        return f"{kind}:{target.strip()}"


class PreferencePage(BaseModel):
    items: list[LearnedPreference]
    total: int
    limit: int
    offset: int


class DetectRoutines(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = Field(default="UTC", min_length=1, max_length=80)

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        try:
            cron.zone(value)
        except cron.CronError as exc:
            raise ValueError(str(exc)) from None
        return value


class RoutineList(BaseModel):
    items: list[Routine]
    detected: int = 0
    state: LearningState


class SuggestionView(Suggestion):
    needs_permission: str | None = Field(
        default=None, description="The permission applying needs when the caller lacks it (policy:manage)"
    )


class NoteView(BaseModel):
    note: ImprovementNote
    suggestions: list[SuggestionView]


class NotePage(BaseModel):
    items: list[NoteView]
    total: int
    state: LearningState


class ResetLearning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]


class ResetResult(BaseModel):
    profile: ProfileView
    signals_deleted: int


# --------------------------------------------------------------------------- helpers


def _member(context: TenantContext) -> None:
    # Every active workspace role (viewer included) holds task:read.
    require_permission(context, Permission.TASK_READ)


def _profile_view(profile: LearningProfile | None) -> ProfileView:
    profile = profile or LearningProfile()
    return ProfileView(
        enabled=profile.enabled,
        derived=profile.derived,
        overrides=profile.overrides,
        effective=effective_style(profile),
        derived_at=profile.derived_at,
        updated_at=profile.updated_at,
    )


def _signal_view(signal: LearningSignal, *, preview: bool) -> SignalView:
    data = signal.model_dump()
    truncated = False
    if preview:
        for name in ("original_text", "final_text"):
            value = data.get(name)
            if value and len(value) > PREVIEW_CHARS:
                data[name] = value[:PREVIEW_CHARS]
                truncated = True
    return SignalView(**data, truncated=truncated)


def _audit(repository: AnumRepository, context: TenantContext, action: str, target: str, metadata: dict[str, Any]) -> None:
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=target,
            outcome="success",
            correlation_id=current_correlation_id() or new_id("learning"),
            created_at=utc_now(),
            metadata=metadata,
        )
    )


def _require_on(context: TenantContext, store: LearningStore) -> None:
    state = learning_state(context, store.get_profile())
    if state != LearningState.ON:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=STATE_MESSAGES[state])


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


# --------------------------------------------------------------------------- overview and switch


def _overview(context: TenantContext) -> LearningOverview:
    with open_learning_store(context) as store:
        profile = store.get_profile()
        counts = LearningCounts(
            signals=store.signal_counts(),
            preferences=store.count_preferences(),
            routines_proposed=len(store.list_routines(status=RoutineStatus.PROPOSED)),
            suggestions_open=len(store.list_suggestions(status=SuggestionStatus.OPEN)),
        )
    return LearningOverview(
        state=learning_state(context, profile),
        profile=_profile_view(profile),
        counts=counts,
        can_manage_automations=has_permission(context, Permission.AUTOMATION_MANAGE),
        can_apply_autonomy=has_permission(context, Permission.POLICY_MANAGE),
    )


@router.get("", response_model=LearningOverview)
async def learning_overview(context: TenantContext = Depends(tenant_context)) -> LearningOverview:
    """The caller's learning state, style profile and counts."""
    _member(context)
    return _overview(context)


@router.put("/settings", response_model=LearningOverview)
async def set_learning(
    payload: LearningSettingsUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> LearningOverview:
    """Learn from my activity: off stops new signals, detection, notes and the learned block."""
    _member(context)
    now = utc_now()
    with open_learning_store(context) as store:
        profile = store.get_profile() or LearningProfile()
        before = profile.enabled
        profile.enabled = payload.enabled
        profile.updated_at = now
        store.save_profile(profile)
    _audit(repository, context, "learning.settings_changed", context.user_id, {"before": before, "after": payload.enabled})
    return _overview(context)


# --------------------------------------------------------------------------- feedback and signals


@router.post("/feedback", response_model=FeedbackResult)
async def record_feedback(payload: FeedbackCreate, context: TenantContext = Depends(tenant_context)) -> FeedbackResult:
    """Thumbs up or down on an answer or draft, or a draft and what the user sent instead.

    A new rating of the same subject replaces the previous one. Nothing is stored while
    learning is off (``recorded: false`` with the reason).
    """
    _member(context)
    with open_learning_store(context) as store:
        state = learning_state(context, store.get_profile())
        if state != LearningState.ON:
            return FeedbackResult(recorded=False, state=state, message=STATE_MESSAGES[state])
        prune(store)
        if payload.rating is not None:
            kind = SignalKind.RATING_UP if payload.rating == "up" else SignalKind.RATING_DOWN
            store.delete_signals(
                kinds=frozenset({SignalKind.RATING_UP, SignalKind.RATING_DOWN}),
                surface=payload.surface,
                subject_id=payload.subject_id,
            )
            signal = new_signal(
                kind, payload.surface, subject_id=payload.subject_id, skill=payload.skill, tool=payload.tool,
                contact=payload.contact, reason=payload.reason,
            )
        else:
            original, final = payload.original or "", payload.final or ""
            edit = summarize_edit(original, final)
            kind = SignalKind.DRAFT_ACCEPTED if original.strip() == final.strip() else SignalKind.DRAFT_EDITED
            signal = new_signal(
                kind, payload.surface, subject_id=payload.subject_id, skill=payload.skill, tool=payload.tool,
                contact=payload.contact, original=original, final=final, reason=payload.reason,
                metadata={"edit": edit.model_dump()},
            )
        store.add_signal(signal)
        if signal.kind in DRAFT_KINDS:
            rederive(store)
    return FeedbackResult(recorded=True, state=state, signal=_signal_view(signal, preview=True))


@router.get("/ratings", response_model=RatingsView)
async def my_ratings(
    surface: Surface,
    subject_id: list[str] = Query(default_factory=list, max_length=100),
    context: TenantContext = Depends(tenant_context),
) -> RatingsView:
    """The caller's current rating of each subject (for showing thumbs state after a reload)."""
    _member(context)
    with open_learning_store(context) as store:
        found = store.ratings_for(surface, [item for item in subject_id if item][:100])
    latest: dict[str, LearningSignal] = {}
    for signal in found:
        if signal.subject_id and signal.subject_id not in latest:
            latest[signal.subject_id] = signal
    return RatingsView(
        ratings=[
            RatingView(
                subject_id=subject,
                rating="up" if signal.kind == SignalKind.RATING_UP else "down",
                signal_id=signal.id,
                reason=signal.reason,
            )
            for subject, signal in latest.items()
        ]
    )


@router.get("/signals", response_model=SignalPage)
async def list_signals(
    kind: SignalKind | None = None,
    surface: Surface | None = None,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> SignalPage:
    """The caller's signals, newest first; draft texts are previews (open one for all of it)."""
    _member(context)
    kinds = frozenset({kind}) if kind else None
    query = (q or "").strip() or None
    with open_learning_store(context) as store:
        items = store.list_signals(kinds=kinds, surface=surface, query=query, limit=limit, offset=offset)
        total = store.count_signals(kinds=kinds, surface=surface, query=query)
    return SignalPage(items=[_signal_view(item, preview=True) for item in items], total=total, limit=limit, offset=offset)


@router.get("/signals/{signal_id}", response_model=SignalView)
async def get_signal(signal_id: str, context: TenantContext = Depends(tenant_context)) -> SignalView:
    _member(context)
    with open_learning_store(context) as store:
        signal = store.get_signal(signal_id)
    if signal is None:
        raise _not_found("Signal")
    return _signal_view(signal, preview=False)


@router.delete("/signals/{signal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_signal(signal_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """Forget one signal; deleting a draft re-derives the style without it."""
    _member(context)
    with open_learning_store(context) as store:
        signal = store.get_signal(signal_id)
        if signal is None or not store.delete_signal(signal_id):
            raise _not_found("Signal")
        if signal.kind in DRAFT_KINDS:
            rederive(store)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- style profile


@router.get("/profile", response_model=ProfileView)
async def get_profile(context: TenantContext = Depends(tenant_context)) -> ProfileView:
    _member(context)
    with open_learning_store(context) as store:
        return _profile_view(store.get_profile())


@router.put("/profile", response_model=ProfileView)
async def set_profile(payload: StyleOverrides, context: TenantContext = Depends(tenant_context)) -> ProfileView:
    """Replace the caller's overrides; a field left out (or null) keeps what was learned."""
    _member(context)
    with open_learning_store(context) as store:
        profile = store.get_profile() or LearningProfile()
        profile.overrides = payload
        profile.updated_at = utc_now()
        store.save_profile(profile)
        return _profile_view(profile)


@router.post("/profile/reset", response_model=ResetResult)
async def reset_profile(
    payload: ResetLearning,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ResetResult:
    """Forget the learned style: the drafts it came from and the overrides. The switch stays."""
    _member(context)
    with open_learning_store(context) as store:
        enabled = (store.get_profile() or LearningProfile()).enabled
        deleted = store.delete_signals(kinds=DRAFT_KINDS)
        profile = LearningProfile(enabled=enabled, updated_at=utc_now())
        store.save_profile(profile)
    _audit(repository, context, "learning.profile_reset", context.user_id, {"draft_signals_deleted": deleted})
    return ResetResult(profile=_profile_view(profile), signals_deleted=deleted)


# --------------------------------------------------------------------------- learned preferences


@router.get("/preferences", response_model=PreferencePage)
async def list_preferences(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> PreferencePage:
    _member(context)
    query = (q or "").strip() or None
    with open_learning_store(context) as store:
        items = store.list_preferences(query=query, limit=limit, offset=offset)
        total = store.count_preferences(query=query)
    return PreferencePage(items=items, total=total, limit=limit, offset=offset)


@router.post("/preferences", response_model=LearnedPreference, status_code=status.HTTP_201_CREATED)
async def create_preference(payload: PreferenceWrite, context: TenantContext = Depends(tenant_context)) -> LearnedPreference:
    _member(context)
    now = utc_now()
    preference = LearnedPreference(id=new_id("pref"), scope=payload.scope, text=payload.text, created_at=now, updated_at=now)
    with open_learning_store(context) as store:
        if store.count_preferences() >= 200:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You have 200 preferences. Delete some first.")
        return store.save_preference(preference)


@router.put("/preferences/{preference_id}", response_model=LearnedPreference)
async def update_preference(
    preference_id: str, payload: PreferenceWrite, context: TenantContext = Depends(tenant_context)
) -> LearnedPreference:
    _member(context)
    with open_learning_store(context) as store:
        preference = store.get_preference(preference_id)
        if preference is None:
            raise _not_found("Preference")
        preference.scope, preference.text, preference.updated_at = payload.scope, payload.text, utc_now()
        return store.save_preference(preference)


@router.delete("/preferences/{preference_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_preference(preference_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _member(context)
    with open_learning_store(context) as store:
        if not store.delete_preference(preference_id):
            raise _not_found("Preference")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- routines


def _activities(context: TenantContext, repository: AnumRepository, since: datetime) -> list[Activity]:
    """The caller's own tasks and manually started automation runs since ``since``."""
    activities: list[Activity] = []
    cursor: str | None = None
    seen = 0
    while seen < MAX_ACTIVITY_TASKS:
        page = repository.list_tasks(context, limit=200, after=cursor)
        if not page:
            break
        for task in page:
            if task.created_by == context.user_id and task.created_at >= since:
                key = activity_key(task.prompt)
                if key:
                    activities.append(Activity(key=key, label=task.title or task.prompt, prompt=task.prompt, at=task.created_at))
        seen += len(page)
        if page[-1].created_at < since or len(page) < 200:
            break
        cursor = page[-1].id
    from .automation import current_engine

    try:
        engine = current_engine()
        names = {workflow.id: workflow.name for workflow in engine.list_workflows(context)}
        for run in engine.list_runs(context):
            if run.created_by == context.user_id and run.schedule_id is None and run.created_at >= since:
                name = names.get(run.workflow_id, run.workflow_id)
                activities.append(
                    Activity(key=f"automation {run.workflow_id}", label=f"Run {name}", prompt=f"Run the {name} automation",
                             at=run.created_at, source="automation")
                )
    except Exception as exc:  # automation history is optional evidence; tasks alone still count
        logger.warning(
            "learning_failed stage=automation_history error_class=%s",
            type(exc).__name__,
            extra={"anum_learning": {"event": "failed", "stage": "automation_history", "error_class": type(exc).__name__}},
        )
    return activities


@router.get("/routines", response_model=RoutineList)
async def list_routines(
    routine_status: RoutineStatus | None = Query(default=None, alias="status"),
    context: TenantContext = Depends(tenant_context),
) -> RoutineList:
    _member(context)
    with open_learning_store(context) as store:
        items = store.list_routines(status=routine_status)
        state = learning_state(context, store.get_profile())
    return RoutineList(items=items, state=state)


@router.post("/routines/detect", response_model=RoutineList)
async def detect_my_routines(
    payload: DetectRoutines,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> RoutineList:
    """Mine the caller's last eight weeks of tasks and automation runs for repeated routines.

    New ones are proposed; a routine already proposed is refreshed; one the caller accepted
    or dismissed is never proposed again.
    """
    _member(context)
    now = utc_now()
    with open_learning_store(context) as store:
        _require_on(context, store)
    activities = _activities(context, repository, now - timedelta(weeks=ACTIVITY_LOOKBACK_WEEKS))
    candidates = detect_routines(
        activities, now=now, zone=cron.zone(payload.timezone), timezone_name=payload.timezone,
        lookback_weeks=ACTIVITY_LOOKBACK_WEEKS,
    )
    detected = 0
    with open_learning_store(context) as store:
        for candidate in candidates:
            existing = store.get_routine_by_fingerprint(candidate.fingerprint)
            if existing is not None and existing.status != RoutineStatus.PROPOSED:
                continue
            routine = existing or Routine(
                id=new_id("routine"), fingerprint=candidate.fingerprint, title=candidate.title, cadence=candidate.cadence,
                cron=candidate.cron, steps=candidate.steps, detected_at=now,
            )
            routine.title, routine.cron, routine.timezone = candidate.title, candidate.cron, candidate.timezone
            routine.steps, routine.occurrences = candidate.steps, candidate.occurrences
            routine.weeks_observed, routine.confidence = candidate.weeks_observed, candidate.confidence
            routine.last_seen_at = candidate.last_seen_at
            store.save_routine(routine)
            detected += existing is None
        items = store.list_routines()
    return RoutineList(items=items, detected=detected, state=LearningState.ON)


def _routine_or_404(store: LearningStore, routine_id: str) -> Routine:
    routine = store.get_routine(routine_id)
    if routine is None:
        raise _not_found("Routine")
    return routine


@router.post("/routines/{routine_id}/accept", response_model=Routine)
async def accept_routine(
    routine_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Routine:
    """One tap: create the automation (workflow and a *paused* schedule) through the engine."""
    require_permission(context, Permission.AUTOMATION_MANAGE)
    from .automation import ScheduleCreate, WorkflowCreate, WorkflowStep, current_engine
    from .scoped_store import ScopeNotProvisionedError

    with open_learning_store(context) as store:
        routine = _routine_or_404(store, routine_id)
    if routine.status != RoutineStatus.PROPOSED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"This routine was already {routine.status.value}")
    workflow_payload = WorkflowCreate(
        name=routine.title[:160],
        description=(
            "Proposed by ANUM from your repeated activity (What ANUM learned). "
            "Its schedule stays paused until you confirm it."
        ),
        steps=[
            WorkflowStep(id=f"step-{index + 1}", name=step.label[:160], action="task.create", input={"title": step.label[:160], "prompt": step.prompt})
            for index, step in enumerate(routine.steps)
        ],
    )
    engine = current_engine()
    try:
        workflow = engine.create_workflow(context, workflow_payload)
        schedule = engine.create_schedule(
            context,
            ScheduleCreate(workflow_id=workflow.id, name=routine.title[:160], cron=routine.cron, timezone=routine.timezone, enabled=False),
        )
    except ScopeNotProvisionedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Complete onboarding for this workspace first") from exc
    now = utc_now()
    routine.status, routine.workflow_id, routine.schedule_id, routine.decided_at = (
        RoutineStatus.ACCEPTED, workflow.id, schedule.id, now,
    )
    with open_learning_store(context) as store:
        store.save_routine(routine)
    _audit(
        repository, context, "learning.routine_accepted", routine.id,
        {"workflow_id": workflow.id, "schedule_id": schedule.id, "cron": routine.cron, "timezone": routine.timezone, "steps": len(routine.steps)},
    )
    return routine


@router.post("/routines/{routine_id}/confirm", response_model=Routine)
async def confirm_routine(
    routine_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Routine:
    """Enable the paused schedule of an accepted routine."""
    require_permission(context, Permission.AUTOMATION_MANAGE)
    from .automation import ScheduleUpdate, current_engine

    with open_learning_store(context) as store:
        routine = _routine_or_404(store, routine_id)
    if routine.status != RoutineStatus.ACCEPTED or not routine.schedule_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Accept the routine first")
    try:
        current_engine().update_schedule(context, routine.schedule_id, ScheduleUpdate(enabled=True))
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Its schedule was deleted in the Workbench. Delete this routine, then detect routines again.",
        ) from exc
    routine.status, routine.decided_at = RoutineStatus.CONFIRMED, utc_now()
    with open_learning_store(context) as store:
        store.save_routine(routine)
    _audit(repository, context, "learning.routine_confirmed", routine.id, {"schedule_id": routine.schedule_id})
    return routine


@router.post("/routines/{routine_id}/dismiss", response_model=Routine)
async def dismiss_routine(
    routine_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Routine:
    """Not a routine: never propose it again (an automation already created stays)."""
    _member(context)
    with open_learning_store(context) as store:
        routine = _routine_or_404(store, routine_id)
        routine.status, routine.decided_at = RoutineStatus.DISMISSED, utc_now()
        store.save_routine(routine)
    _audit(repository, context, "learning.routine_dismissed", routine.id, {})
    return routine


@router.delete("/routines/{routine_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_routine(routine_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """Forget a routine; it may be proposed again if the pattern continues."""
    _member(context)
    with open_learning_store(context) as store:
        if not store.delete_routine(routine_id):
            raise _not_found("Routine")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- improvement notes


def _failures(context: TenantContext, repository: AnumRepository, since: datetime) -> list[RunFailure]:
    failures: list[RunFailure] = []
    cursor: str | None = None
    while len(failures) < MAX_FAILED_RUNS:
        page = repository.list_tasks(context, limit=200, after=cursor)
        if not page:
            break
        for task in page:
            if task.created_by != context.user_id or task.status != TaskStatus.FAILED or task.updated_at < since:
                continue
            run = repository.find_run_for_task(task.id, context)
            if run is None:
                continue
            call = run.checkpoint.tool_call or {}
            proposal = next((step for step in run.steps if step.type == "tool_proposal"), None)
            reason = run.result or next((step.summary for step in reversed(run.steps) if step.type == "tool_result"), "")
            failures.append(
                RunFailure(
                    tool=call.get("name") if isinstance(call.get("name"), str) else None,
                    skill_ids=tuple(run.checkpoint.selected_skills),
                    reason=reason,
                    risk_level=str(proposal.metadata.get("risk_level")) if proposal else None,
                    approved=run.checkpoint.approval_id is not None,
                )
            )
        if page[-1].created_at < since - timedelta(days=28) or len(page) < 200:
            break
        cursor = page[-1].id
    return failures


def _suggestion_view(suggestion: Suggestion, context: TenantContext) -> SuggestionView:
    needs = None
    if suggestion.kind == SuggestionKind.AUTONOMY and not has_permission(context, Permission.POLICY_MANAGE):
        needs = Permission.POLICY_MANAGE.value
    return SuggestionView(**suggestion.model_dump(), needs_permission=needs)


def _generate_note(context: TenantContext, repository: AnumRepository, now: datetime) -> NoteView:
    week = iso_week(now)
    period_start = now - timedelta(days=7)
    failures = _failures(context, repository, period_start)
    policy: WorkspaceApprovalPolicy = repository.get_approval_policy(context)
    with open_learning_store(context) as store:
        prune(store, now)
        week_signals = store.list_signals(since=period_start, limit=2000)
        month_signals = store.list_signals(since=now - timedelta(days=28), limit=5000)
        drafts = improvement_suggestions(
            week_signals, month_signals, failures, medium_risk_requires_approval=policy.medium_risk_requires_approval
        )
        for draft in drafts:
            fingerprint = draft.fingerprint(week)
            existing = store.get_suggestion_by_fingerprint(fingerprint)
            if existing is not None and existing.status != SuggestionStatus.OPEN:
                continue
            suggestion = existing or Suggestion(
                id=new_id("suggestion"), week=week, fingerprint=fingerprint, rule=draft.rule, kind=draft.kind,
                title=draft.title, body=draft.body, created_at=now, updated_at=now,
            )
            suggestion.title, suggestion.body, suggestion.target = draft.title[:200], draft.body[:1000], draft.target
            suggestion.change, suggestion.evidence, suggestion.updated_at = draft.change, draft.evidence, now
            store.save_suggestion(suggestion)
        note = store.save_note(
            ImprovementNote(
                week=week, period_start=period_start, period_end=now,
                summary=note_summary(week_signals, failures), generated_at=now,
            )
        )
        suggestions = store.list_suggestions(week=week)
    return NoteView(note=note, suggestions=[_suggestion_view(item, context) for item in suggestions])


@router.get("/notes", response_model=NotePage)
async def list_notes(
    limit: int = Query(default=4, ge=1, le=52),
    offset: int = Query(default=0, ge=0, le=10_000),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> NotePage:
    """Weekly improvement notes, newest first. This week's is written on the first visit of the week."""
    _member(context)
    now = utc_now()
    with open_learning_store(context) as store:
        state = learning_state(context, store.get_profile())
        missing = store.get_note(iso_week(now)) is None
    if state == LearningState.ON and missing and offset == 0:
        _generate_note(context, repository, now)
    with open_learning_store(context) as store:
        notes = store.list_notes(limit=limit, offset=offset)
        total = store.count_notes()
        views = [
            NoteView(note=note, suggestions=[_suggestion_view(item, context) for item in store.list_suggestions(week=note.week)])
            for note in notes
        ]
    return NotePage(items=views, total=total, state=state)


@router.post("/notes/refresh", response_model=NoteView)
async def refresh_note(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> NoteView:
    """Rewrite this week's note from the latest signals (decided suggestions are kept)."""
    _member(context)
    with open_learning_store(context) as store:
        _require_on(context, store)
    return _generate_note(context, repository, utc_now())


def _suggestion_or_404(store: LearningStore, suggestion_id: str) -> Suggestion:
    suggestion = store.get_suggestion(suggestion_id)
    if suggestion is None:
        raise _not_found("Suggestion")
    if suggestion.status != SuggestionStatus.OPEN:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"This suggestion was already {suggestion.status.value}")
    return suggestion


@router.post("/suggestions/{suggestion_id}/apply", response_model=SuggestionView)
async def apply_suggestion(
    suggestion_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> SuggestionView:
    """Apply one suggestion, audited. Code and permissions are never changed by ANUM itself:
    an instruction or template becomes one of the caller's learned preferences, and an
    autonomy change sets the workspace approval policy only for an owner."""
    _member(context)
    with open_learning_store(context) as store:
        suggestion = _suggestion_or_404(store, suggestion_id)
    change = suggestion.change
    now = utc_now()
    applied: dict[str, Any] = {"suggestion_id": suggestion.id, "rule": suggestion.rule, "kind": suggestion.kind.value}
    if change.get("type") == "approval_policy":
        require_permission(context, Permission.POLICY_MANAGE)
        previous = repository.get_approval_policy(context)
        wanted = bool(change.get("medium_risk_requires_approval"))
        saved = repository.save_approval_policy(
            WorkspaceApprovalPolicy(
                two_person_rule=previous.two_person_rule,
                medium_risk_requires_approval=wanted,
                updated_by=context.user_id,
                updated_at=now,
            ),
            context,
        )
        _audit(
            repository, context, "approval_policy.updated", context.workspace_id,
            {
                "before": {"two_person_rule": previous.two_person_rule, "medium_risk_requires_approval": previous.medium_risk_requires_approval},
                "after": {"two_person_rule": saved.two_person_rule, "medium_risk_requires_approval": saved.medium_risk_requires_approval},
                "source": "learning_suggestion",
                "suggestion_id": suggestion.id,
            },
        )
        applied["medium_risk_requires_approval"] = wanted
    elif change.get("type") == "preference":
        scope = str(change.get("scope") or "all")
        text = str(change.get("text") or "")[:MAX_PREFERENCE_CHARS]
        with open_learning_store(context) as store:
            store.save_preference(
                LearnedPreference(
                    id=new_id("pref"), scope=scope, text=text, source="suggestion", suggestion_id=suggestion.id,
                    created_at=now, updated_at=now,
                )
            )
        applied["scope"] = scope
    else:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="This suggestion cannot be applied")
    with open_learning_store(context) as store:
        suggestion.status, suggestion.decided_at, suggestion.updated_at = SuggestionStatus.APPLIED, now, now
        store.save_suggestion(suggestion)
    _audit(repository, context, "learning.suggestion_applied", suggestion.id, applied)
    return _suggestion_view(suggestion, context)


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=SuggestionView)
async def dismiss_suggestion(suggestion_id: str, context: TenantContext = Depends(tenant_context)) -> SuggestionView:
    _member(context)
    with open_learning_store(context) as store:
        suggestion = _suggestion_or_404(store, suggestion_id)
        now = utc_now()
        suggestion.status, suggestion.decided_at, suggestion.updated_at = SuggestionStatus.DISMISSED, now, now
        store.save_suggestion(suggestion)
    return _suggestion_view(suggestion, context)
