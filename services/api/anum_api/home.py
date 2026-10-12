"""Home: the summary, pinned apps, quick commands and proactive insights (docs/home.md).

``GET /api/v1/home/summary`` answers the Home screen in one call: the greeting name,
Today's Focus (pending approvals, urgent email, today's meetings, tasks in progress,
failing automations, each from its own module), today's progress and Recent Activity.
Each part comes from a *source*. A source whose module is not installed, that the caller
may not read, or that fails is reported in ``sources`` with its status and left out; the
rest of the summary still answers.

``GET /api/v1/home/insights`` lists what ANUM noticed (autonomy proposals, approvals
waiting too long, failed or stalled tasks, failing automations, integration and mailbox
problems), each with a finding, a question and one primary action. Dismiss and snooze are
stored per user (``home_insight_states``).

Both lists are open registries: another module adds a focus or insight source with
``register_focus_source`` / ``register_insight_source`` (the learning-patterns module
registers its findings this way). A source declares the permission its data needs and is
skipped for callers without it.

Pinned apps and quick commands belong to the caller only: there is no user id in any path
or body, every store call is keyed by the caller's tenant, workspace and user, and with
PostgreSQL the rows are under forced RLS on ``anum.user_id`` (migration ``0036_home``).
Every change is audited (``home.*`` actions).
"""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, repository_context, require_permission, tenant_context
from .repository import AnumRepository
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store

logger = logging.getLogger("anum.home")

router = APIRouter(prefix="/api/v1/home", tags=["home"])

MAX_PINS = 24
MAX_COMMANDS = 30
MAX_INSIGHT_STATES = 500
MAX_FOCUS_PER_SOURCE = 5
MAX_INSIGHTS_PER_SOURCE = 5
MAX_ACTIVITY = 10

COMMAND_DENIED = "Viewers cannot run commands, so they cannot save quick commands. Ask a workspace owner for the member role."

# What a pin or an action may open in the apps. Clients map each key to a screen.
FEATURES = frozenset(
    {
        "command", "chat", "agents", "voice", "automations", "tasks", "calendar", "reports", "documents",
        "integrations", "knowledge", "approvals", "email", "whatsapp", "autonomy", "settings", "insights",
    }
)
_ICON = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
INSIGHT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,40}:[A-Za-z0-9_.:@-]{1,150}$")


# -- stored models ---------------------------------------------------------------------------


class HomeProfile(BaseModel):
    display_name: str | None = None
    updated_at: datetime | None = None


class PinKind(StrEnum):
    WORKSPACE = "workspace"
    LINK = "link"
    FEATURE = "feature"


class Pin(BaseModel):
    id: str
    kind: PinKind
    label: str
    target: str
    icon: str | None = None
    position: int = 0
    created_at: datetime
    updated_at: datetime


class CommandMode(StrEnum):
    WORK = "work"
    PERSONAL = "personal"
    CUSTOM = "custom"


class QuickCommand(BaseModel):
    id: str
    mode: CommandMode
    label: str
    prompt: str
    # Built-in commands carry a stable key the apps translate; the user's own have none.
    key: str | None = None
    built_in: bool = False
    position: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InsightStateKind(StrEnum):
    DISMISSED = "dismissed"
    SNOOZED = "snoozed"


class InsightStateRecord(BaseModel):
    key: str
    state: InsightStateKind
    until: datetime | None = None
    updated_at: datetime


# -- request bodies ----------------------------------------------------------------------------


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def _check_target(kind: PinKind, target: str) -> str:
    target = target.strip()
    if kind == PinKind.LINK:
        parts = urlsplit(target)
        if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
            raise ValueError("A link must be an https:// address without a user name or password.")
    elif kind == PinKind.FEATURE:
        if target not in FEATURES:
            raise ValueError(f"Unknown feature. Use one of: {', '.join(sorted(FEATURES))}.")
    elif not _WORKSPACE_ID.match(target):
        raise ValueError("A workspace pin needs the workspace id.")
    return target


class PinCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PinKind
    label: str = Field(min_length=1, max_length=60)
    target: str = Field(min_length=1, max_length=500)
    icon: str | None = Field(default=None, max_length=40)

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        cleaned = _clean(value)
        if cleaned is None:
            raise ValueError("The label must not be blank.")
        return cleaned

    @field_validator("icon")
    @classmethod
    def _icon(cls, value: str | None) -> str | None:
        if value is not None and not _ICON.match(value):
            raise ValueError("The icon is a lowercase name such as tyre or chart.")
        return value

    @model_validator(mode="after")
    def _target(self) -> "PinCreate":
        self.target = _check_target(self.kind, self.target)
        return self


class PinUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=60)
    target: str | None = Field(default=None, min_length=1, max_length=500)
    icon: str | None = Field(default=None, max_length=40)

    @field_validator("label")
    @classmethod
    def _label(cls, value: str | None) -> str | None:
        if value is not None and _clean(value) is None:
            raise ValueError("The label must not be blank.")
        return _clean(value)

    @field_validator("icon")
    @classmethod
    def _icon(cls, value: str | None) -> str | None:
        if value is not None and not _ICON.match(value):
            raise ValueError("The icon is a lowercase name such as tyre or chart.")
        return value


class PinOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1, max_length=MAX_PINS)


class CommandCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: CommandMode = CommandMode.CUSTOM
    label: str = Field(min_length=1, max_length=60)
    prompt: str = Field(min_length=1, max_length=1000)

    @field_validator("label", "prompt")
    @classmethod
    def _text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class CommandUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: CommandMode | None = None
    label: str | None = Field(default=None, min_length=1, max_length=60)
    prompt: str | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator("label", "prompt")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value.strip() if value is not None else None


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=80)


class SnoozeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hours: int = Field(default=24, ge=1, le=168)


# -- views -------------------------------------------------------------------------------------


class Target(BaseModel):
    """What an item opens in the apps: a feature key (``FEATURES``) and an optional id."""

    feature: str
    id: str | None = None


SourceState = Literal["ok", "not_installed", "not_permitted", "not_connected", "unavailable"]


class SourceStatus(BaseModel):
    name: str
    status: SourceState
    detail: str | None = None


Severity = Literal["high", "medium", "low"]


class FocusItem(BaseModel):
    id: str
    source: str
    kind: str
    title: str
    detail: str | None = None
    count: int | None = None
    # 0..1 where the module knows how far along the item is (a task run's stage).
    progress: float | None = Field(default=None, ge=0, le=1)
    severity: Severity = "medium"
    at: datetime | None = None
    target: Target
    params: dict[str, Any] = Field(default_factory=dict)


class ActivityItem(BaseModel):
    id: str
    kind: str
    title: str
    status: str
    at: datetime
    target: Target


class TodayProgress(BaseModel):
    completed: int
    open: int


class HomeSummary(BaseModel):
    generated_at: datetime
    profile: HomeProfile
    focus: list[FocusItem]
    counts: dict[str, int]
    progress: TodayProgress | None
    activity: list[ActivityItem]
    sources: list[SourceStatus]


ActionVerb = Literal["analyze", "compare", "send_reminder", "check_now", "review", "open"]


class InsightAction(BaseModel):
    """One primary action. ``type=open`` opens ``target``; ``type=command`` hands ``command``
    to ANUM Command (the apps show the plan before anything runs)."""

    type: Literal["open", "command"]
    verb: ActionVerb
    target: Target | None = None
    command: str | None = None


class Insight(BaseModel):
    key: str
    source: str
    kind: str
    severity: Severity = "medium"
    title: str
    finding: str
    question: str
    params: dict[str, Any] = Field(default_factory=dict)
    action: InsightAction
    created_at: datetime | None = None
    state: Literal["new", "snoozed", "dismissed"] = "new"
    snoozed_until: datetime | None = None


class InsightList(BaseModel):
    items: list[Insight]
    hidden: int
    sources: list[SourceStatus]


class CommandList(BaseModel):
    items: list[QuickCommand]
    can_edit: bool


# -- source registry -----------------------------------------------------------------------------


@dataclass(frozen=True)
class HomeScope:
    """What a source may use: the caller, the request's repository (RLS-scoped) and the
    application state (tool and integration registries)."""

    context: TenantContext
    repository: AnumRepository
    state: Any
    now: datetime


class SourceNotInstalled(LookupError):
    """The module behind a source is not part of this deployment."""


class SourceNotConnected(LookupError):
    """The module is installed but nothing is connected yet (no mailbox, no calendar)."""


@dataclass(frozen=True)
class FocusResult:
    items: list[FocusItem]
    count: int


FocusCollector = Callable[[HomeScope], "FocusResult | Awaitable[FocusResult]"]
InsightCollector = Callable[[HomeScope], "list[Insight] | Awaitable[list[Insight]]"]


@dataclass(frozen=True)
class FocusSource:
    name: str
    collect: FocusCollector
    permission: Permission | None = None
    order: int = 100


@dataclass(frozen=True)
class InsightSource:
    name: str
    collect: InsightCollector
    permission: Permission | None = None
    order: int = 100


@dataclass
class _Registry:
    focus: dict[str, FocusSource] = field(default_factory=dict)
    insights: dict[str, InsightSource] = field(default_factory=dict)
    # Sources Home knows about that no module has registered here: reported as not installed.
    expected_focus: tuple[str, ...] = ("meetings",)


registry = _Registry()


def register_focus_source(source: FocusSource) -> None:
    """Add (or replace) a Today's Focus source. Safe to call at import time."""
    registry.focus[source.name] = source


def register_insight_source(source: InsightSource) -> None:
    """Add (or replace) a proactive-insight source. Keys must be ``<source>:<id>``."""
    registry.insights[source.name] = source


def unregister_source(name: str) -> None:
    registry.focus.pop(name, None)
    registry.insights.pop(name, None)


async def _call(collect: Callable[[HomeScope], Any], scope: HomeScope) -> Any:
    result = collect(scope)
    if inspect.isawaitable(result):
        result = await result
    return result


async def _gather(
    sources: list[FocusSource] | list[InsightSource], scope: HomeScope
) -> tuple[list[tuple[str, Any]], list[SourceStatus]]:
    results: list[tuple[str, Any]] = []
    statuses: list[SourceStatus] = []
    for source in sorted(sources, key=lambda s: (s.order, s.name)):
        if source.permission is not None and not has_permission(scope.context, source.permission):
            statuses.append(SourceStatus(name=source.name, status="not_permitted"))
            continue
        try:
            results.append((source.name, await _call(source.collect, scope)))
        except SourceNotInstalled as exc:
            statuses.append(SourceStatus(name=source.name, status="not_installed", detail=str(exc) or None))
            continue
        except SourceNotConnected as exc:
            statuses.append(SourceStatus(name=source.name, status="not_connected", detail=str(exc) or None))
            continue
        except HTTPException as exc:
            state: SourceState = "not_permitted" if exc.status_code == status.HTTP_403_FORBIDDEN else "unavailable"
            statuses.append(SourceStatus(name=source.name, status=state, detail=str(exc.detail)))
            continue
        except Exception as exc:  # one module failing never takes Home down
            logger.warning(
                "home_source_failed source=%s error_class=%s",
                source.name,
                type(exc).__name__,
                extra={"anum_home": {"source": source.name, "error_class": type(exc).__name__}},
            )
            statuses.append(
                SourceStatus(name=source.name, status="unavailable", detail="This part of Home could not be loaded. Pull to refresh.")
            )
            continue
        statuses.append(SourceStatus(name=source.name, status="ok"))
    return results, statuses


# -- store ------------------------------------------------------------------------------------------


class HomeStore(Protocol):
    def get_profile(self, context: TenantContext) -> HomeProfile | None: ...
    def save_profile(self, context: TenantContext, profile: HomeProfile) -> HomeProfile: ...
    def list_pins(self, context: TenantContext) -> list[Pin]: ...
    def save_pin(self, context: TenantContext, pin: Pin) -> Pin: ...
    def delete_pin(self, context: TenantContext, pin_id: str) -> bool: ...
    def list_commands(self, context: TenantContext) -> list[QuickCommand]: ...
    def save_command(self, context: TenantContext, command: QuickCommand) -> QuickCommand: ...
    def delete_command(self, context: TenantContext, command_id: str) -> bool: ...
    def list_insight_states(self, context: TenantContext) -> dict[str, InsightStateRecord]: ...
    def save_insight_state(self, context: TenantContext, record: InsightStateRecord) -> InsightStateRecord: ...
    def delete_insight_state(self, context: TenantContext, key: str) -> bool: ...


def _ordered(items: list[Any]) -> list[Any]:
    return sorted(items, key=lambda item: (item.position, item.created_at or datetime.min, item.id))


class InMemoryHomeStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._profiles: dict[tuple[str, str], HomeProfile] = {}
        self._pins: dict[tuple[str, str, str], dict[str, Pin]] = {}
        self._commands: dict[tuple[str, str, str], dict[str, QuickCommand]] = {}
        self._states: dict[tuple[str, str, str], dict[str, InsightStateRecord]] = {}
        self._lock = RLock()

    @staticmethod
    def _user(context: TenantContext) -> tuple[str, str, str]:
        return (context.tenant_id, context.workspace_id, context.user_id)

    def get_profile(self, context: TenantContext) -> HomeProfile | None:
        with self._lock:
            return self._profiles.get((context.tenant_id, context.user_id))

    def save_profile(self, context: TenantContext, profile: HomeProfile) -> HomeProfile:
        with self._lock:
            self._profiles[(context.tenant_id, context.user_id)] = profile
        return profile

    def list_pins(self, context: TenantContext) -> list[Pin]:
        with self._lock:
            return _ordered(list(self._pins.get(self._user(context), {}).values()))

    def save_pin(self, context: TenantContext, pin: Pin) -> Pin:
        with self._lock:
            self._pins.setdefault(self._user(context), {})[pin.id] = pin
        return pin

    def delete_pin(self, context: TenantContext, pin_id: str) -> bool:
        with self._lock:
            return self._pins.get(self._user(context), {}).pop(pin_id, None) is not None

    def list_commands(self, context: TenantContext) -> list[QuickCommand]:
        with self._lock:
            return _ordered(list(self._commands.get(self._user(context), {}).values()))

    def save_command(self, context: TenantContext, command: QuickCommand) -> QuickCommand:
        with self._lock:
            self._commands.setdefault(self._user(context), {})[command.id] = command
        return command

    def delete_command(self, context: TenantContext, command_id: str) -> bool:
        with self._lock:
            return self._commands.get(self._user(context), {}).pop(command_id, None) is not None

    def list_insight_states(self, context: TenantContext) -> dict[str, InsightStateRecord]:
        with self._lock:
            return dict(self._states.get(self._user(context), {}))

    def save_insight_state(self, context: TenantContext, record: InsightStateRecord) -> InsightStateRecord:
        with self._lock:
            states = self._states.setdefault(self._user(context), {})
            states[record.key] = record
            if len(states) > MAX_INSIGHT_STATES:
                for old in sorted(states.values(), key=lambda r: r.updated_at)[: len(states) - MAX_INSIGHT_STATES]:
                    states.pop(old.key, None)
        return record

    def delete_insight_state(self, context: TenantContext, key: str) -> bool:
        with self._lock:
            return self._states.get(self._user(context), {}).pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._profiles.clear()
            self._pins.clear()
            self._commands.clear()
            self._states.clear()


home_store = InMemoryHomeStore()


@contextmanager
def open_home_store(context: TenantContext) -> Iterator[HomeStore]:
    """The caller's Home rows for one unit of work (``anum.user_id`` is set)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.home_repository import SqlAlchemyHomeStore

        return SqlAlchemyHomeStore(session)

    with open_scoped_store(context, home_store, sql_store, user_scoped=True) as store:
        yield store


# -- helpers ----------------------------------------------------------------------------------------


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
            correlation_id=new_id("home"),
            created_at=utc_now(),
            metadata=metadata,
        )
    )


def _scope(request: Request, context: TenantContext, repository: AnumRepository) -> HomeScope:
    return HomeScope(context=context, repository=repository, state=request.app.state, now=utc_now())


def _member_workspaces(context: TenantContext) -> dict[str, str | None]:
    from .membership_directory import list_caller_memberships

    return {m.workspace_id: m.workspace_name for m in list_caller_memberships(context)}


# -- built-in quick commands ----------------------------------------------------------------------------

# (key, mode, permission the command needs besides task:create, English label, English prompt)
_BUILT_IN_COMMANDS: tuple[tuple[str, CommandMode, Permission | None, str, str], ...] = (
    ("check_email", CommandMode.WORK, Permission.EMAIL_READ, "Check my emails",
     "Check my emails, find today's urgent items and summarize them."),
    ("review_approvals", CommandMode.WORK, Permission.APPROVAL_READ, "Review approvals",
     "List the approvals waiting for me and what each one will do."),
    ("failed_automations", CommandMode.WORK, Permission.AUTOMATION_READ, "Check automations",
     "Which automations failed recently, and why?"),
    ("summarize_day", CommandMode.WORK, None, "Summarize my day",
     "Summarize what was done today and what still needs me."),
    ("plan_day", CommandMode.PERSONAL, None, "Plan my day",
     "Plan my day from my open tasks and anything waiting for me."),
    ("my_tasks", CommandMode.PERSONAL, None, "Show today's tasks",
     "Show my tasks that are open or running today."),
    ("week_review", CommandMode.PERSONAL, None, "My week so far",
     "What did I finish this week, and what is still open?"),
)


def built_in_commands(context: TenantContext) -> list[QuickCommand]:
    if not has_permission(context, Permission.TASK_CREATE):
        return []
    return [
        QuickCommand(id=f"builtin:{key}", mode=mode, label=label, prompt=prompt, key=key, built_in=True, position=index)
        for index, (key, mode, permission, label, prompt) in enumerate(_BUILT_IN_COMMANDS)
        if permission is None or has_permission(context, permission)
    ]


# -- routes: summary --------------------------------------------------------------------------------------


@router.get("/summary", response_model=HomeSummary)
async def home_summary(
    request: Request,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> HomeSummary:
    """Everything the Home screen shows, in one call. Open to every member; each part needs
    the permission of the module it comes from and is left out (with its status) otherwise."""
    require_permission(context, Permission.TASK_READ)
    from . import home_sources  # noqa: F401  (registers the built-in sources)

    scope = _scope(request, context, repository)
    results, statuses = await _gather(list(registry.focus.values()), scope)
    for name in registry.expected_focus:
        if name not in registry.focus:
            statuses.append(SourceStatus(name=name, status="not_installed"))
    focus: list[FocusItem] = []
    counts: dict[str, int] = {}
    for name, result in results:
        focus.extend(result.items[:MAX_FOCUS_PER_SOURCE])
        counts[name] = result.count
    rank = {"high": 0, "medium": 1, "low": 2}
    focus.sort(key=lambda item: (rank[item.severity], -(item.at or scope.now).timestamp()))

    from .home_sources import recent_activity, today_progress

    progress = today_progress(scope) if has_permission(context, Permission.TASK_READ) else None
    activity = recent_activity(scope)[:MAX_ACTIVITY]
    with open_home_store(context) as store:
        profile = store.get_profile(context) or HomeProfile()
    return HomeSummary(
        generated_at=scope.now,
        profile=profile,
        focus=focus,
        counts=counts,
        progress=progress,
        activity=activity,
        sources=statuses,
    )


# -- routes: profile -------------------------------------------------------------------------------------


@router.put("/profile", response_model=HomeProfile)
async def set_profile(
    payload: ProfileUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> HomeProfile:
    """The name Home greets the caller by (blank clears it). Only the caller's own."""
    require_permission(context, Permission.TASK_READ)
    profile = HomeProfile(display_name=_clean(payload.display_name), updated_at=utc_now())
    with open_home_store(context) as store:
        saved = store.save_profile(context, profile)
    _audit(repository, context, "home.profile_updated", context.user_id, {"has_name": saved.display_name is not None})
    return saved


# -- routes: pins ------------------------------------------------------------------------------------------


@router.get("/pins", response_model=list[Pin])
async def list_pins(context: TenantContext = Depends(tenant_context)) -> list[Pin]:
    require_permission(context, Permission.TASK_READ)
    with open_home_store(context) as store:
        return store.list_pins(context)


def _check_workspace_pin(context: TenantContext, target: str) -> str | None:
    workspaces = _member_workspaces(context)
    if target not in workspaces:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="You can pin only a workspace you are an active member of.",
        )
    return workspaces[target]


@router.post("/pins", response_model=Pin, status_code=status.HTTP_201_CREATED)
async def add_pin(
    payload: PinCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Pin:
    require_permission(context, Permission.TASK_READ)
    if payload.kind == PinKind.WORKSPACE:
        _check_workspace_pin(context, payload.target)
    now = utc_now()
    with open_home_store(context) as store:
        pins = store.list_pins(context)
        if len(pins) >= MAX_PINS:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Home holds up to {MAX_PINS} pins. Remove one first.")
        if any(p.kind == payload.kind and p.target == payload.target for p in pins):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This is already pinned.")
        pin = store.save_pin(
            context,
            Pin(
                id=new_id("pin"), kind=payload.kind, label=payload.label, target=payload.target, icon=payload.icon,
                position=(max((p.position for p in pins), default=-1) + 1), created_at=now, updated_at=now,
            ),
        )
    _audit(repository, context, "home.pin_added", pin.id, {"kind": pin.kind.value})
    return pin


@router.patch("/pins/{pin_id}", response_model=Pin)
async def update_pin(
    pin_id: str,
    payload: PinUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Pin:
    require_permission(context, Permission.TASK_READ)
    with open_home_store(context) as store:
        pins = store.list_pins(context)
        pin = next((p for p in pins if p.id == pin_id), None)
        if pin is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pin not found")
        changes: dict[str, Any] = {"updated_at": utc_now()}
        if payload.label is not None:
            changes["label"] = payload.label
        if "icon" in payload.model_fields_set:
            changes["icon"] = payload.icon
        if payload.target is not None:
            try:
                target = _check_target(pin.kind, payload.target)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
            if pin.kind == PinKind.WORKSPACE:
                _check_workspace_pin(context, target)
            if any(p.id != pin.id and p.kind == pin.kind and p.target == target for p in pins):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This is already pinned.")
            changes["target"] = target
        saved = store.save_pin(context, pin.model_copy(update=changes))
    _audit(repository, context, "home.pin_updated", saved.id, {"fields": sorted(payload.model_fields_set)})
    return saved


@router.delete("/pins/{pin_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_pin(
    pin_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    require_permission(context, Permission.TASK_READ)
    with open_home_store(context) as store:
        if not store.delete_pin(context, pin_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pin not found")
    _audit(repository, context, "home.pin_removed", pin_id, {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/pins/order", response_model=list[Pin])
async def reorder_pins(
    payload: PinOrder,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[Pin]:
    """The full new order: every pin id exactly once."""
    require_permission(context, Permission.TASK_READ)
    with open_home_store(context) as store:
        pins = {p.id: p for p in store.list_pins(context)}
        if len(payload.ids) != len(set(payload.ids)) or set(payload.ids) != set(pins):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Send every pin id exactly once, in the new order. Reload Home and try again.",
            )
        now = utc_now()
        for position, pin_id in enumerate(payload.ids):
            if pins[pin_id].position != position:
                store.save_pin(context, pins[pin_id].model_copy(update={"position": position, "updated_at": now}))
        ordered = store.list_pins(context)
    _audit(repository, context, "home.pins_reordered", context.user_id, {"count": len(ordered)})
    return ordered


# -- routes: quick commands ----------------------------------------------------------------------------------


@router.get("/commands", response_model=CommandList)
async def list_commands(context: TenantContext = Depends(tenant_context)) -> CommandList:
    """Built-in commands for the modules the caller can use, then the caller's own."""
    require_permission(context, Permission.TASK_READ)
    with open_home_store(context) as store:
        own = store.list_commands(context)
    return CommandList(items=[*built_in_commands(context), *own], can_edit=has_permission(context, Permission.TASK_CREATE))


def _require_command_editor(context: TenantContext) -> None:
    if not has_permission(context, Permission.TASK_CREATE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=COMMAND_DENIED)


@router.post("/commands", response_model=QuickCommand, status_code=status.HTTP_201_CREATED)
async def add_command(
    payload: CommandCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> QuickCommand:
    _require_command_editor(context)
    now = utc_now()
    with open_home_store(context) as store:
        own = store.list_commands(context)
        if len(own) >= MAX_COMMANDS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"Home holds up to {MAX_COMMANDS} quick commands. Remove one first."
            )
        command = store.save_command(
            context,
            QuickCommand(
                id=new_id("cmd"), mode=payload.mode, label=payload.label, prompt=payload.prompt,
                position=(max((c.position for c in own), default=-1) + 1), created_at=now, updated_at=now,
            ),
        )
    _audit(repository, context, "home.command_added", command.id, {"mode": command.mode.value})
    return command


@router.patch("/commands/{command_id}", response_model=QuickCommand)
async def update_command(
    command_id: str,
    payload: CommandUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> QuickCommand:
    _require_command_editor(context)
    with open_home_store(context) as store:
        command = next((c for c in store.list_commands(context) if c.id == command_id), None)
        if command is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quick command not found")
        changes = {k: v for k, v in payload.model_dump().items() if v is not None}
        saved = store.save_command(context, command.model_copy(update={**changes, "updated_at": utc_now()}))
    _audit(repository, context, "home.command_updated", saved.id, {"fields": sorted(changes)})
    return saved


@router.delete("/commands/{command_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_command(
    command_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    _require_command_editor(context)
    with open_home_store(context) as store:
        if not store.delete_command(context, command_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quick command not found")
    _audit(repository, context, "home.command_removed", command_id, {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- routes: insights ----------------------------------------------------------------------------------------


async def collect_insights(scope: HomeScope) -> tuple[list[Insight], list[SourceStatus]]:
    from . import home_sources  # noqa: F401  (registers the built-in sources)

    results, statuses = await _gather(list(registry.insights.values()), scope)
    insights: list[Insight] = []
    seen: set[str] = set()
    for name, items in results:
        for insight in list(items)[:MAX_INSIGHTS_PER_SOURCE]:
            if insight.key in seen or not INSIGHT_KEY.match(insight.key) or not insight.key.startswith(f"{name}:"):
                logger.warning("home_insight_rejected source=%s", name, extra={"anum_home": {"source": name}})
                continue
            seen.add(insight.key)
            insights.append(insight)
    return insights, statuses


def _with_state(insight: Insight, record: InsightStateRecord | None, now: datetime) -> Insight:
    if record is None:
        return insight
    if record.state == InsightStateKind.DISMISSED:
        return insight.model_copy(update={"state": "dismissed"})
    if record.until is not None and record.until > now:
        return insight.model_copy(update={"state": "snoozed", "snoozed_until": record.until})
    return insight


@router.get("/insights", response_model=InsightList)
async def list_insights(
    request: Request,
    include_hidden: bool = Query(default=False, description="Also list dismissed and snoozed insights"),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> InsightList:
    require_permission(context, Permission.TASK_READ)
    scope = _scope(request, context, repository)
    insights, statuses = await collect_insights(scope)
    with open_home_store(context) as store:
        states = store.list_insight_states(context)
    viewed = [_with_state(i, states.get(i.key), scope.now) for i in insights]
    rank = {"high": 0, "medium": 1, "low": 2}
    viewed.sort(key=lambda i: (rank[i.severity], -(i.created_at or scope.now).timestamp()))
    visible = [i for i in viewed if include_hidden or i.state == "new"]
    return InsightList(items=visible, hidden=sum(1 for i in viewed if i.state != "new"), sources=statuses)


async def _current_insight(request: Request, context: TenantContext, repository: AnumRepository, key: str) -> Insight:
    if not INSIGHT_KEY.match(key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insight not found")
    insights, _ = await collect_insights(_scope(request, context, repository))
    insight = next((i for i in insights if i.key == key), None)
    if insight is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This insight is no longer current. Refresh the list.")
    return insight


@router.post("/insights/{key}/dismiss", response_model=Insight)
async def dismiss_insight(
    key: str,
    request: Request,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Insight:
    """Hide this insight for the caller for good (until they restore it)."""
    require_permission(context, Permission.TASK_READ)
    insight = await _current_insight(request, context, repository, key)
    now = utc_now()
    with open_home_store(context) as store:
        record = store.save_insight_state(
            context, InsightStateRecord(key=key, state=InsightStateKind.DISMISSED, until=None, updated_at=now)
        )
    _audit(repository, context, "home.insight_dismissed", key, {"source": insight.source, "kind": insight.kind})
    return _with_state(insight, record, now)


@router.post("/insights/{key}/snooze", response_model=Insight)
async def snooze_insight(
    key: str,
    request: Request,
    payload: SnoozeBody | None = None,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Insight:
    """"Later": hide it for ``hours`` (default 24); it comes back if it is still true then."""
    require_permission(context, Permission.TASK_READ)
    insight = await _current_insight(request, context, repository, key)
    now = utc_now()
    hours = (payload or SnoozeBody()).hours
    with open_home_store(context) as store:
        record = store.save_insight_state(
            context,
            InsightStateRecord(key=key, state=InsightStateKind.SNOOZED, until=now + timedelta(hours=hours), updated_at=now),
        )
    _audit(repository, context, "home.insight_snoozed", key, {"source": insight.source, "hours": hours})
    return _with_state(insight, record, now)


@router.delete("/insights/{key}/state", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def restore_insight(
    key: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """Undo a dismiss or snooze."""
    require_permission(context, Permission.TASK_READ)
    if not INSIGHT_KEY.match(key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insight not found")
    with open_home_store(context) as store:
        if not store.delete_insight_state(context, key):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This insight was not dismissed or snoozed")
    _audit(repository, context, "home.insight_restored", key, {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
