"""What the voice assistant can *do* (docs/voice.md#actions).

A spoken request that asks for work ("ask the Sales agent to follow up with yesterday's
leads", "create a task to draft the weekly report", "cancel the stock count task", "run
my morning check quick command", "what's the status of the weekly report task?") is
carried out through the same governed path as the Command Center and the Tasks view:

* a task is created like ``POST /api/v1/tasks`` (``agent_id`` when an agent is named) and
  started with :func:`anum_api.main.start_task_run`, the body of ``POST /tasks/{id}/run``
  (permission, run lock, budget, agent profile, ToolPolicy, governance, autonomy and
  approvals). A risky tool call stops at ``waiting_approval`` and its approval appears in
  Approvals like any other; voice never decides it.
* cancel is ``POST /tasks/{id}/cancel`` (:func:`anum_api.main.cancel_task_record`), retry
  ``POST /tasks/{id}/retry`` and a quick command ``POST /commands``
  (:func:`anum_api.commands.create_command_record`) followed by a run of every step.
* a teammate ("ask Ahmed to call the supplier by Thursday") becomes a task whose prompt is
  the user's sentence; the runtime plans it as ``team.assign_task`` (docs/team-work.md).

What to do, which agent and which task are read from the user's own words by the rules
below, never from model output or retrieved text (threat model G5). Every lookup runs in
the caller's tenant, workspace and user scope (RLS in PostgreSQL); an agent or task the
caller cannot see is "not found", exactly like a missing one.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .chat_tools import ChatCardApproval
from .schemas import TaskStatus, TenantContext

if TYPE_CHECKING:
    from .agent_profiles import AgentProfile
    from .agent_tools import ToolRegistry
    from .schemas import RunTaskResponse, Task

logger = logging.getLogger("anum.voice_actions")

MAX_ACTION_CHARS = 2000
TITLE_CHARS = 160
RESULT_CHARS = 220
MAX_CANDIDATES = 3


class ActionKind(StrEnum):
    CREATE_TASK = "create_task"
    ASSIGN = "assign"
    RUN_TASK = "run_task"
    CANCEL_TASK = "cancel_task"
    TASK_STATUS = "task_status"
    RUN_QUICK_COMMAND = "run_quick_command"


# The task the user means when they say "the last task" or "that task".
LAST = "\x00last"


@dataclass
class ParsedAction:
    """A request for work, as said. ``rest`` is the text after the lead-in (assignments),
    ``name`` the task or quick command named, ``work`` the task's instructions."""

    kind: ActionKind
    work: str | None = None
    name: str | None = None
    rest: str | None = None
    start: bool = True
    # Set by :func:`resolve_assignment` (the caller's visible agents).
    agent_id: str | None = None
    agent_name: str | None = None
    agent_active: bool = True
    team: bool = False
    agent_not_found: bool = False
    candidates: list[str] = field(default_factory=list)


# -- parsing (pure, from the user's own words) -----------------------------------------------

_POLITE = re.compile(
    r"^(?:(?:hey|hi|please|kindly|ok(?:ay)?|now|and|so|then|can you|could you|would you|will you|"
    r"i want you to|i need you to|i'?d like you to|go ahead and|من فضلك|لو سمحت|رجاء)(?: ?[,،])? ?)+",
    re.IGNORECASE,
)
_TRAIL = " .!?؟،,;:"
_ARTICLE = re.compile(r"^(?:the|my|our|a|an|this|that) ", re.IGNORECASE)

_CREATE = re.compile(
    r"^(?:(?:create|make|add|open|set up|start) (?:me )?(?:a |an )?new task|"
    r"(?:create|make|add|open|set up) (?:me )?(?:a |an )?task|start (?:a|an) task|"
    r"new task|add (?:this|it) as a task|remind me to|"
    r"أنشئ مهمة جديدة|أنشئ مهمة|انشئ مهمة|مهمة جديدة|أضف مهمة|اضف مهمة|ذكرني أن|ذكرني ان|ذكرني ب)"
    r"(?: |$|:|,|-)",
    re.IGNORECASE,
)
_ASK_LEAD = re.compile(r"^(?:ask|tell|have|get) ", re.IGNORECASE)
_ASSIGN_LEAD = re.compile(r"^(?:assign|give|pass|send|hand(?: over)?|delegate|forward|route) ", re.IGNORECASE)
_ARABIC_ASSIGN_LEAD = re.compile(
    r"^(?:اطلب من|اطلبي من|كلف|كلّف|أسند إلى|اسند الى|أسند الى|أرسل إلى|ارسل الى|حول إلى|حوّل إلى|حول الى) "
)
# "pass a task to <agent>", "give this to <agent>", "hand it over to <agent>"
_THING_TO = re.compile(
    r"^(?:(?:this|that|a|an|the|my|it|one) )?(?:new )?(?:(?:task|job|piece of work|request|work|one) )?"
    r"(?:over )?to ",
    re.IGNORECASE,
)
_CANCEL_LEAD = re.compile(r"^(?:cancel|stop|abort|halt|kill) ", re.IGNORECASE)
_ARABIC_CANCEL_LEAD = re.compile(r"^(?:ألغ|الغ|ألغِ|أوقف|اوقف) ")
_RUN_LEAD = re.compile(
    r"^(?:start|run|resume|retry|restart|re-run|rerun|kick off|launch|execute|do) ", re.IGNORECASE
)
_ARABIC_RUN_LEAD = re.compile(r"^(?:شغل|شغّل|ابدأ|نفذ|نفّذ|أعد تشغيل) ")
_STATUS_LEADS = (
    "what's the status of ", "whats the status of ", "what is the status of ", "status of ",
    "status on ", "what's happening with ", "what is happening with ", "what happened to ",
    "what happened with ", "any update on ", "any news on ", "check on ", "check the status of ",
    "how's ", "how is ", "is ", "did ", "has ",
)
_STATUS_TAILS = (
    " going", " doing", " getting on", " coming along", " done", " finished", " complete", " completed",
    " ready", " finish", " run", " work", " succeed", " go", " been done", " started", " running",
)
_ARABIC_STATUS_LEADS = ("ما حالة مهمة ", "ما حالة المهمة ", "حالة مهمة ", "حالة المهمة ", "ماذا حدث في مهمة ")
_ARABIC_TASK_PREFIX = ("مهمة ", "المهمة ")
_QUICK_PREFIX = ("quick command ", "saved command ", "command ", "shortcut ")
_QUICK_SUFFIX = (" quick command", " saved command", " command", " shortcut")
_ARABIC_QUICK_PREFIX = ("الأمر السريع ", "الامر السريع ", "الأمر ", "الامر ")
_LAST_WORDS = frozenset({"last task", "latest task", "previous task", "that task", "this task", "my last task", "آخر مهمة", "المهمة الأخيرة"})


def _normalise(text: str) -> str:
    return " ".join(text[:MAX_ACTION_CHARS].split()).strip(_TRAIL)


def _strip_articles(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = _ARTICLE.sub("", text, count=1)
    return text


def _task_name(text: str) -> str | None:
    """``task X`` / ``X task`` / ``the task called X`` -> X; None when no task is named."""
    lowered = text.lower()
    if lowered in _LAST_WORDS or _strip_articles(lowered) in {"last task", "latest task", "previous task"}:
        return LAST
    stripped = _strip_articles(text)
    low = stripped.lower()
    for prefix in ("task called ", "task named ", "task ", *_ARABIC_TASK_PREFIX):
        if low.startswith(prefix) and len(stripped) > len(prefix):
            return _strip_articles(stripped[len(prefix):]).strip(_TRAIL + "\"'“”") or None
    if low.endswith(" task") and len(stripped) > 5:
        return stripped[:-5].strip(_TRAIL + "\"'“”") or None
    return None


def _quick_name(text: str) -> str | None:
    stripped = _strip_articles(text)
    low = stripped.lower()
    for prefix in (*(f"{p}called " for p in _QUICK_PREFIX), *(f"{p}named " for p in _QUICK_PREFIX), *_QUICK_PREFIX, *_ARABIC_QUICK_PREFIX):
        if low.startswith(prefix) and len(stripped) > len(prefix):
            return stripped[len(prefix):].strip(_TRAIL + "\"'“”") or None
    for suffix in _QUICK_SUFFIX:
        if low.endswith(suffix) and len(stripped) > len(suffix):
            return _strip_articles(stripped[: -len(suffix)]).strip(_TRAIL + "\"'“”") or None
    return None


def _status(sentence: str) -> ParsedAction | None:
    low = sentence.lower()
    for lead in _STATUS_LEADS:
        if low.startswith(lead):
            rest = sentence[len(lead):]
            for tail in _STATUS_TAILS:
                if rest.lower().endswith(tail):
                    rest = rest[: -len(tail)]
                    break
            name = _task_name(rest.strip(_TRAIL))
            if name:
                return ParsedAction(ActionKind.TASK_STATUS, name=name)
            return None
    for lead in _ARABIC_STATUS_LEADS:
        if sentence.startswith(lead) and len(sentence) > len(lead):
            return ParsedAction(ActionKind.TASK_STATUS, name=sentence[len(lead):].strip(_TRAIL))
    return None


def parse_action(text: str) -> ParsedAction | None:
    """The work ``text`` asks for, or None for anything else (questions, small talk).

    Bounded input, lead-ins matched with anchored patterns and the rest split with plain
    string operations, so matching stays linear on hostile input.
    """
    sentence = _POLITE.sub("", _normalise(text)).strip(_TRAIL)
    if not sentence:
        return None
    status = _status(sentence)
    if status is not None:
        return status

    match = _CREATE.match(sentence)
    if match:
        lead = match.group(0).lower()
        rest = sentence[match.end():].strip(_TRAIL + " -")
        start = not (lead.startswith("remind me") or lead.startswith("ذكرني"))
        low = rest.lower()
        for prefix in ("to ", "called ", "named ", "that ", "for me to ", "for me: ", "لـ", "ل "):
            if low.startswith(prefix):
                rest, low = rest[len(prefix):].strip(_TRAIL), rest[len(prefix):].strip(_TRAIL).lower()
                break
        if low.startswith("for ") and start:
            # "create a task for the Sales agent to ..." is an assignment.
            return ParsedAction(ActionKind.ASSIGN, rest=rest[4:])
        return ParsedAction(ActionKind.CREATE_TASK, work=rest or None, start=start)

    lead = _ASK_LEAD.match(sentence)
    if lead:
        rest = sentence[lead.end():]
        if rest.lower().startswith(("me ", "us ", "you ", "yourself ")):
            return None
        return ParsedAction(ActionKind.ASSIGN, rest=rest)
    lead = _ARABIC_ASSIGN_LEAD.match(sentence)
    if lead:
        return ParsedAction(ActionKind.ASSIGN, rest=sentence[lead.end():])
    lead = _ASSIGN_LEAD.match(sentence)
    if lead:
        rest = sentence[lead.end():]
        if rest.lower().startswith(("me ", "us ")):
            return None
        return ParsedAction(ActionKind.ASSIGN, rest=rest)

    for pattern in (_CANCEL_LEAD, _ARABIC_CANCEL_LEAD):
        lead = pattern.match(sentence)
        if lead:
            name = _task_name(sentence[lead.end():])
            return ParsedAction(ActionKind.CANCEL_TASK, name=name) if name else None

    for pattern in (_RUN_LEAD, _ARABIC_RUN_LEAD):
        lead = pattern.match(sentence)
        if lead:
            rest = sentence[lead.end():]
            quick = _quick_name(rest)
            if quick:
                return ParsedAction(ActionKind.RUN_QUICK_COMMAND, name=quick)
            name = _task_name(rest)
            if name:
                return ParsedAction(ActionKind.RUN_TASK, name=name)
            return None
    return None


# -- resolving agents ------------------------------------------------------------------------


def _key(text: str) -> str:
    return " ".join(text.casefold().split()).strip(_TRAIL)


def agent_aliases(profile: "AgentProfile") -> set[str]:
    """How a person may say the agent's name: "Sales agent", "sales", "the sales assistant"."""
    names = {_key(profile.name)}
    if profile.department:
        names.add(_key(profile.department))
    aliases: set[str] = set()
    for name in names:
        base = name
        for suffix in (" agent", " assistant", " bot"):
            if base.endswith(suffix) and len(base) > len(suffix):
                base = base[: -len(suffix)].strip()
                break
        if base.startswith("وكيل ") and len(base) > 5:
            base = base[5:].strip()
        aliases |= {name, base, f"{base} agent", f"{base} assistant", f"وكيل {base}", f"مساعد {base}"}
    return {alias for alias in aliases if alias}


@dataclass
class AgentChoice:
    agent: "AgentProfile"
    work: str


def _after_name(text: str) -> str:
    low = text.lower()
    for joiner in ("to ", "and ", "please ", "that ", "أن ", "ان "):
        if low.startswith(joiner):
            return text[len(joiner):].strip(_TRAIL)
    return text.strip(_TRAIL)


def match_agent(rest: str, agents: list["AgentProfile"]) -> tuple[AgentChoice | None, bool]:
    """(agent and the work for it, whether an agent was named at all) for ``rest``.

    Shapes: ``<agent> to <work>`` (ask/tell), ``<thing> to <agent>[:] <work>`` and
    ``<work> to <agent>`` (assign/pass/give). The longest alias wins, so "Sales agent"
    beats "Sales".
    """
    named: list[tuple[str, "AgentProfile"]] = sorted(
        ((alias, agent) for agent in agents for alias in agent_aliases(agent)),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    body = rest.strip(_TRAIL)
    thing = _THING_TO.match(body)
    candidates = [body]
    if thing:
        candidates.insert(0, body[thing.end():])
    for candidate in candidates:
        stripped = _strip_articles(candidate.strip())
        low = stripped.casefold()
        for alias, agent in named:
            if low == alias or low.startswith(alias + " ") or any(low.startswith(alias + mark) for mark in (",", ":", ";", " -", "،")):
                work = stripped[len(alias):].lstrip(" ,:;-،")
                return AgentChoice(agent=agent, work=_after_name(work)), True
    # "<work> to <agent>": the last " to " in the sentence.
    low = body.casefold()
    cut = low.rfind(" to ")
    if cut > 0:
        target = _strip_articles(low[cut + 4:].strip(_TRAIL))
        for alias, agent in named:
            if target == alias:
                return AgentChoice(agent=agent, work=_strip_articles(body[:cut]).strip(_TRAIL)), True
    # "assistant" is left to team work: department assistants are assigned there.
    mentioned = bool(re.search(r"\bagents?\b|وكيل", low))
    return None, mentioned


# -- the card --------------------------------------------------------------------------------

CardState = Literal[
    "running", "completed", "approval", "failed", "cancelled", "created", "status",
    "not_found", "ambiguous", "denied", "needs_detail",
]


class VoiceActionAgent(BaseModel):
    id: str
    name: str


class VoiceActionCard(BaseModel):
    """What a spoken request did, shown under the reply (``action`` stream event)."""

    kind: ActionKind
    state: CardState
    title: str | None = None
    task_id: str | None = None
    task_status: TaskStatus | None = None
    agent: VoiceActionAgent | None = None
    command_id: str | None = None
    # Steps of a quick command, and how many of its runs wait for approval.
    steps: int = 0
    approvals_pending: int = 0
    approval: ChatCardApproval | None = None
    summary: str = ""
    candidates: list[str] = Field(default_factory=list)
    error_code: str | None = None


@dataclass
class ActionStep:
    """One update of an action: the card and, on the last one, what is said aloud."""

    card: VoiceActionCard
    reply: str | None = None


# -- configuration (set by main.py) ----------------------------------------------------------


@dataclass
class VoiceTaskRuntime:
    """The task routes' own bodies, handed over by ``main.py`` (no second code path)."""

    tools: "ToolRegistry"
    run: Callable[[TenantContext, str], Awaitable["RunTaskResponse"]]
    retry: Callable[[TenantContext, str], Awaitable["RunTaskResponse"]]
    cancel: Callable[[TenantContext, str], Awaitable["Task"]]


_configured: VoiceTaskRuntime | None = None


def configure(runtime: VoiceTaskRuntime | None) -> None:
    global _configured
    _configured = runtime


def configured() -> VoiceTaskRuntime | None:
    return _configured


# -- words -----------------------------------------------------------------------------------


def _q(text: str, arabic: bool) -> str:
    return f"«{text}»" if arabic else f"\"{text}\""


def _clip(text: str | None, limit: int) -> str:
    words = " ".join((text or "").split())
    return words if len(words) <= limit else words[: limit - 1].rstrip() + "…"


def _first_sentence(text: str | None) -> str:
    words = " ".join((text or "").split())
    match = re.search(r"(?<=[.!?؟])\s", words)
    return _clip(words[: match.start()] if match else words, RESULT_CHARS)


def _title(work: str) -> str:
    work = " ".join(work.split()).strip(_TRAIL)
    return _clip(work[:1].upper() + work[1:], TITLE_CHARS) if work else ""


DENIED = {
    "create": (
        "Your role can talk with me but not create tasks in this workspace. A workspace owner can give you the member role in Settings, Members.",
        "دورك يسمح بالحديث معي لكن ليس بإنشاء مهام في مساحة العمل هذه. يستطيع مالك المساحة منحك دور العضو من الإعدادات، الأعضاء.",
    ),
    "run": (
        "Your role can't start tasks in this workspace. A workspace owner can give you the member role in Settings, Members.",
        "دورك لا يسمح بتشغيل المهام في مساحة العمل هذه. يستطيع مالك المساحة منحك دور العضو من الإعدادات، الأعضاء.",
    ),
    "cancel": (
        "Only workspace owners can cancel tasks. Ask an owner, or open the task in Tasks.",
        "إلغاء المهام لمالكي مساحة العمل فقط. اطلب ذلك من المالك، أو افتح المهمة في المهام.",
    ),
}


def _say(arabic: bool, en: str, ar: str) -> str:
    return ar if arabic else en


def outcome_sentence(task: "Task", run_result: str | None, approval: object | None, failure: str | None, arabic: bool) -> str:
    """The second sentence of a reply: what the run did."""
    if task.status == TaskStatus.WAITING_APPROVAL or approval is not None and task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        return _say(arabic, "It needs an approval first, so I've sent it to Approvals.", "تحتاج إلى موافقة أولًا، فأرسلتها إلى الموافقات.")
    if task.status == TaskStatus.COMPLETED:
        result = _first_sentence(run_result)
        return _say(arabic, f"It's done: {result}" if result else "It's done.", f"اكتملت: {result}" if result else "اكتملت.")
    if task.status == TaskStatus.FAILED:
        reason = _first_sentence(failure)
        return _say(arabic, f"It failed: {reason}" if reason else "It failed. Open it in Tasks to see why.", f"فشلت: {reason}" if reason else "فشلت. افتحها في المهام لمعرفة السبب.")
    if task.status == TaskStatus.CANCELLED:
        return _say(arabic, "It was cancelled.", "أُلغيت.")
    if task.status == TaskStatus.CREATED:
        return _say(arabic, "It hasn't started yet.", "لم تبدأ بعد.")
    return _say(arabic, "It's running now; you can follow it in Tasks.", "تعمل الآن، ويمكنك متابعتها في المهام.")


def _state_for(task: "Task") -> CardState:
    return {
        TaskStatus.COMPLETED: "completed",
        TaskStatus.WAITING_APPROVAL: "approval",
        TaskStatus.FAILED: "failed",
        TaskStatus.CANCELLED: "cancelled",
        TaskStatus.CREATED: "created",
    }.get(task.status, "running")  # type: ignore[return-value]


# -- storage helpers (each a short unit of work in the caller's scope, off the event loop) ----


def _units(context: TenantContext):  # type: ignore[no-untyped-def]
    from .dependencies import _unit_of_work

    return _unit_of_work(context, require_membership=True)


def _visible_agents(context: TenantContext) -> list["AgentProfile"]:
    from .agent_profiles import open_agent_store

    with open_agent_store(context) as store:
        return store.list(context)


def _create_task(context: TenantContext, title: str, prompt: str, agent_id: str | None) -> "Task":
    """``POST /api/v1/tasks`` (with ``agent_id`` when an agent was named)."""
    from .events import CanonicalEventName, create_event
    from .schemas import Task, new_id, utc_now

    now = utc_now()
    task = Task(
        id=new_id("task"), title=title, prompt=prompt, status=TaskStatus.CREATED,
        tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        created_at=now, updated_at=now, created_by=context.user_id, agent_id=agent_id,
    )
    with _units(context) as repository:
        repository.create_task(task)
        repository.record_event(
            create_event(
                CanonicalEventName.TASK_CREATED, context, task.id,
                {"title": task.title, "source": "voice"},
                correlation_id=task.id, created_at=now,
            ).event
        )
    return task


def _find_tasks(context: TenantContext, name: str, statuses: frozenset[TaskStatus] | None) -> list["Task"]:
    """Tasks the caller can see whose title fits ``name``, best match first."""
    from .tasks import TaskFilters

    with _units(context) as repository:
        if name == LAST:
            rows = repository.list_tasks(context, limit=20, filters=TaskFilters(created_by=context.user_id))
            rows = [row for row in rows if statuses is None or row.status in statuses]
            return rows[:1]
        rows = repository.list_tasks(context, limit=50, filters=TaskFilters(query=name[:200]))
    if statuses is not None:
        rows = [row for row in rows if row.status in statuses]
    needle = _key(name)
    for test in (
        lambda title: title == needle,
        lambda title: title.startswith(needle),
        lambda title: needle in title,
    ):
        found = [row for row in rows if test(_key(row.title))]
        if found:
            return found
    return rows


def _run_view(context: TenantContext, task_id: str) -> tuple["Task | None", str | None, str | None, object | None]:
    """The task now, its run's result, failure reason and pending approval (status answers)."""
    from .chat_tools import _failure_reason

    with _units(context) as repository:
        task = repository.get_task(task_id, context)
        if task is None:
            return None, None, None, None
        run = repository.find_run_for_task(task.id, context)
        approval = None
        if run is not None and run.checkpoint.approval_id:
            approval = repository.get_approval(run.checkpoint.approval_id, context)
        failure = _failure_reason(run) if task.status == TaskStatus.FAILED else None
        return task, run.result if run else None, failure, approval


def _approval_view(approval: object | None, context: TenantContext) -> ChatCardApproval | None:
    if approval is None:
        return None
    from .chat_tools import approval_view
    from .schemas import utc_now

    return approval_view(approval, context, utc_now())  # type: ignore[arg-type]


# -- running ---------------------------------------------------------------------------------


def _describe(exc: BaseException, arabic: bool) -> tuple[str, str]:
    """(error code, sentence) for a refusal or failure while acting."""
    from fastapi import HTTPException

    from .model_budget import ModelBudgetExceededError

    if isinstance(exc, ModelBudgetExceededError):
        return "model_budget_exceeded", exc.spoken(arabic)
    if isinstance(exc, HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "The request was refused."
        code = {403: "forbidden", 404: "not_found", 409: "conflict", 422: "refused", 503: "runtime_unavailable"}.get(exc.status_code, "failed")
        return code, detail
    if isinstance(exc, TimeoutError):
        return "task_timeout", _say(arabic, "The task took too long and was stopped. It's saved in Tasks.", "استغرقت المهمة وقتًا طويلًا فأُوقفت. هي محفوظة في المهام.")
    return "task_failed", _say(arabic, "The task could not run. It's saved in Tasks; try it again there.", "تعذر تشغيل المهمة. هي محفوظة في المهام؛ جرّبها مرة أخرى من هناك.")


async def _start(runtime: VoiceTaskRuntime, context: TenantContext, task: "Task", *, retry: bool = False):  # type: ignore[no-untyped-def]
    """Start ``task`` like ``POST /tasks/{id}/run`` (or ``/retry``); never left half-claimed."""
    from .chat_tools import _reset_unrun

    try:
        return await (runtime.retry if retry else runtime.run)(context, task.id)
    except (asyncio.CancelledError, GeneratorExit):
        # The listener went away mid-run: a task that never got past planning stays runnable.
        asyncio.get_running_loop().run_in_executor(None, _reset_unrun, task, context)
        raise


def _denied(kind: ActionKind, which: str, arabic: bool, title: str | None = None) -> ActionStep:
    reply = DENIED[which][1 if arabic else 0]
    return ActionStep(VoiceActionCard(kind=kind, state="denied", title=title, summary=reply, error_code="forbidden"), reply)


async def perform(action: ParsedAction, context: TenantContext, arabic: bool) -> AsyncIterator[ActionStep]:
    """Carry out ``action``; yields a card as soon as a task exists, then the final one with
    the spoken reply. ``text`` is the request without the wake word (the teammate path's
    task prompt)."""
    from .authorization import Permission
    from .dependencies import has_permission

    runtime = configured()
    kind = action.kind
    if kind == ActionKind.TASK_STATUS:
        async for step in _status_of(action, context, arabic):
            yield step
        return
    if runtime is None:
        reply = _say(arabic, "I can't run tasks on this server. Open Tasks to do it there.", "لا أستطيع تشغيل المهام على هذا الخادم. افتح المهام للقيام بذلك.")
        yield ActionStep(VoiceActionCard(kind=kind, state="failed", summary=reply, error_code="runtime_unavailable"), reply)
        return

    if kind in {ActionKind.CREATE_TASK, ActionKind.ASSIGN, ActionKind.RUN_QUICK_COMMAND}:
        if not has_permission(context, Permission.TASK_CREATE):
            yield _denied(kind, "create", arabic)
            return
        if (action.start or kind != ActionKind.CREATE_TASK) and not has_permission(context, Permission.TASK_RUN):
            yield _denied(kind, "run", arabic)
            return
    if kind == ActionKind.RUN_TASK and not has_permission(context, Permission.TASK_RUN):
        yield _denied(kind, "run", arabic)
        return
    if kind == ActionKind.CANCEL_TASK and not has_permission(context, Permission.TASK_CANCEL):
        yield _denied(kind, "cancel", arabic)
        return

    try:
        if kind == ActionKind.CREATE_TASK:
            steps = _create_and_start(runtime, action, context, arabic)
        elif kind == ActionKind.ASSIGN:
            steps = _assign(runtime, action, context, arabic)
        elif kind == ActionKind.CANCEL_TASK:
            steps = _cancel(runtime, action, context, arabic)
        elif kind == ActionKind.RUN_TASK:
            steps = _run_existing(runtime, action, context, arabic)
        else:
            steps = _quick_command(runtime, action, context, arabic)
        async for step in steps:
            yield step
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception as exc:  # said aloud; only the class name is logged
        logger.warning("voice_action_failed kind=%s error_class=%s", kind.value, type(exc).__name__)
        code, reply = _describe(exc, arabic)
        yield ActionStep(VoiceActionCard(kind=kind, state="failed", summary=reply, error_code=code), reply)


async def _started(
    runtime: VoiceTaskRuntime,
    kind: ActionKind,
    task: "Task",
    context: TenantContext,
    arabic: bool,
    lead: str,
    agent: VoiceActionAgent | None = None,
    *,
    retry: bool = False,
) -> AsyncIterator[ActionStep]:
    """Show the task, run it through the runtime, then say what happened."""
    card = VoiceActionCard(kind=kind, state="running", title=task.title, task_id=task.id, task_status=TaskStatus.RUNNING, agent=agent)
    yield ActionStep(card)
    try:
        response = await _start(runtime, context, task, retry=retry)
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception as exc:
        code, reason = _describe(exc, arabic)
        logger.warning("voice_action_run_failed error_class=%s", type(exc).__name__)
        failed = card.model_copy(update={"state": "failed", "task_status": None, "summary": reason, "error_code": code})
        yield ActionStep(failed, f"{lead} {_say(arabic, 'But it could not start:', 'لكن تعذر تشغيلها:')} {reason}")
        return
    from .chat_tools import _failure_reason

    done = response.task
    failure = _failure_reason(response.run) if done.status == TaskStatus.FAILED else None
    approval = _approval_view(response.approval, context)
    final = card.model_copy(
        update={
            "state": _state_for(done),
            "task_status": done.status,
            "approval": approval,
            "approvals_pending": 1 if approval is not None and approval.status == "pending" else 0,
            "summary": _clip(failure or response.run.result or "", 600),
        }
    )
    yield ActionStep(final, f"{lead} {outcome_sentence(done, response.run.result, response.approval, failure, arabic)}")


async def _create_and_start(runtime: VoiceTaskRuntime, action: ParsedAction, context: TenantContext, arabic: bool) -> AsyncIterator[ActionStep]:
    if not action.work:
        reply = _say(arabic, "Sure. What should the task be?", "بالتأكيد. ما المهمة؟")
        yield ActionStep(VoiceActionCard(kind=action.kind, state="needs_detail", summary=reply), reply)
        return
    task = await asyncio.to_thread(_create_task, context, _title(action.work), action.work, None)
    if not action.start:
        reply = _say(
            arabic,
            f"Created task {_q(task.title, False)}. It's in Tasks; say \"start the {task.title} task\" when you want it run.",
            f"أنشأت المهمة {_q(task.title, True)}. هي في المهام؛ قل «شغّل المهمة {task.title}» عندما تريد تشغيلها.",
        )
        yield ActionStep(VoiceActionCard(kind=action.kind, state="created", title=task.title, task_id=task.id, task_status=task.status), reply)
        return
    lead = _say(arabic, f"Created task {_q(task.title, False)} and started it.", f"أنشأت المهمة {_q(task.title, True)} وبدأت تشغيلها.")
    async for step in _started(runtime, action.kind, task, context, arabic, lead):
        yield step


def resolve_assignment(action: ParsedAction, text: str, context: TenantContext) -> ParsedAction | None:
    """Who an assignment is for, from the caller's visible agents (blocking: run it in a
    worker thread). None when the sentence is not an assignment after all ("have a nice
    day", "send the report to the bank": the latter is still an action, see voice.py)."""
    from .team_commands import looks_like_assignment

    agents = _visible_agents(context)
    choice, mentioned = match_agent(action.rest or "", agents)
    if choice is not None:
        from .agent_profiles import AgentStatus

        return ParsedAction(
            ActionKind.ASSIGN, work=choice.work or None, agent_id=choice.agent.id, agent_name=choice.agent.name,
            agent_active=choice.agent.status == AgentStatus.ACTIVE,
        )
    if mentioned:
        # "the HR agent" when there is none: say which agents exist instead of guessing.
        return ParsedAction(ActionKind.ASSIGN, agent_not_found=True, candidates=sorted(agent.name for agent in agents)[:5])
    if looks_like_assignment(text):
        # A teammate, a department assistant or ANUM (docs/team-work.md#assigning-in-words).
        return ParsedAction(ActionKind.ASSIGN, work=" ".join(text.split()), team=True)
    return None


async def _assign(runtime: VoiceTaskRuntime, action: ParsedAction, context: TenantContext, arabic: bool) -> AsyncIterator[ActionStep]:
    if action.agent_id is not None and action.agent_name is not None:
        view = VoiceActionAgent(id=action.agent_id, name=action.agent_name)
        name = action.agent_name
        if not action.work:
            reply = _say(arabic, f"What should {name} do?", f"ما الذي تريد أن يقوم به {name}؟")
            yield ActionStep(VoiceActionCard(kind=action.kind, state="needs_detail", agent=view, summary=reply), reply)
            return
        if not action.agent_active:
            reply = _say(arabic, f"{name} is paused. Resume it in Agents, then ask again.", f"{name} متوقف مؤقتًا. استأنفه من الوكلاء ثم اطلب مرة أخرى.")
            yield ActionStep(VoiceActionCard(kind=action.kind, state="failed", agent=view, summary=reply, error_code="agent_paused"), reply)
            return
        task = await asyncio.to_thread(_create_task, context, _title(action.work), action.work, action.agent_id)
        lead = _say(
            arabic,
            f"Created task {_q(task.title, False)} and assigned it to {name}.",
            f"أنشأت المهمة {_q(task.title, True)} وأسندتها إلى {name}.",
        )
        async for step in _started(runtime, action.kind, task, context, arabic, lead, view):
            yield step
        return
    if action.team and action.work:
        task = await asyncio.to_thread(_create_task, context, _title(action.work), action.work, None)
        lead = _say(arabic, f"Created task {_q(task.title, False)} to assign that.", f"أنشأت المهمة {_q(task.title, True)} لإسناد ذلك.")
        async for step in _started(runtime, action.kind, task, context, arabic, lead):
            yield step
        return
    names = action.candidates
    if names:
        reply = _say(arabic, f"I couldn't find that agent. Your agents are: {', '.join(names)}.", f"لم أجد هذا الوكيل. وكلاؤك هم: {'، '.join(names)}.")
    else:
        reply = _say(arabic, "You don't have any agents yet. Create one in Agents, then ask again.", "ليس لديك وكلاء بعد. أنشئ وكيلًا من الوكلاء ثم اطلب مرة أخرى.")
    yield ActionStep(VoiceActionCard(kind=action.kind, state="not_found", summary=reply, candidates=names, error_code="agent_not_found"), reply)


def _one_task(
    kind: ActionKind, name: str, found: list["Task"], arabic: bool
) -> tuple["Task | None", ActionStep | None]:
    if not found:
        label = _say(arabic, "your last task", "آخر مهمة") if name == LAST else _q(name, arabic)
        reply = _say(arabic, f"I couldn't find {label} in Tasks. Say the task's name as it appears there.", f"لم أجد {label} في المهام. قل اسم المهمة كما يظهر هناك.")
        return None, ActionStep(VoiceActionCard(kind=kind, state="not_found", summary=reply, error_code="task_not_found"), reply)
    if len(found) > 1:
        titles = [task.title for task in found[:MAX_CANDIDATES]]
        joined = _say(arabic, " and ".join([", ".join(titles[:-1]), titles[-1]]) if len(titles) > 1 else titles[0], " و".join(titles))
        reply = _say(arabic, f"{len(found)} tasks match: {joined}. Which one?", f"تطابق {len(found)} مهام: {joined}. أيها تقصد؟")
        return None, ActionStep(VoiceActionCard(kind=kind, state="ambiguous", summary=reply, candidates=titles, error_code="ambiguous"), reply)
    return found[0], None


async def _status_of(action: ParsedAction, context: TenantContext, arabic: bool) -> AsyncIterator[ActionStep]:
    from .authorization import Permission
    from .dependencies import require_permission

    require_permission(context, Permission.TASK_READ)
    found = await asyncio.to_thread(_find_tasks, context, action.name or LAST, None)
    task, problem = _one_task(action.kind, action.name or LAST, found, arabic)
    if problem is not None or task is None:
        yield problem  # type: ignore[misc]
        return
    current, result, failure, approval = await asyncio.to_thread(_run_view, context, task.id)
    current = current or task
    view = _approval_view(approval, context)
    pending = view is not None and view.status == "pending"
    reply = f"{_say(arabic, f'Task {_q(current.title, False)}:', f'المهمة {_q(current.title, True)}:')} {outcome_sentence(current, result, approval if pending else None, failure, arabic)}"
    yield ActionStep(
        VoiceActionCard(
            kind=action.kind, state="status", title=current.title, task_id=current.id, task_status=current.status,
            approval=view, approvals_pending=1 if pending else 0, summary=_clip(failure or result or "", 600),
        ),
        reply,
    )


async def _cancel(runtime: VoiceTaskRuntime, action: ParsedAction, context: TenantContext, arabic: bool) -> AsyncIterator[ActionStep]:
    from .tasks import ACTIVE_STATUSES

    found = await asyncio.to_thread(_find_tasks, context, action.name or LAST, frozenset(ACTIVE_STATUSES))
    task, problem = _one_task(action.kind, action.name or LAST, found, arabic)
    if problem is not None or task is None:
        yield problem  # type: ignore[misc]
        return
    cancelled = await runtime.cancel(context, task.id)
    reply = _say(arabic, f"Cancelled task {_q(cancelled.title, False)}. Its pending approvals were withdrawn.", f"ألغيت المهمة {_q(cancelled.title, True)} وسُحبت موافقاتها المعلقة.")
    yield ActionStep(VoiceActionCard(kind=action.kind, state="cancelled", title=cancelled.title, task_id=cancelled.id, task_status=cancelled.status), reply)


async def _run_existing(runtime: VoiceTaskRuntime, action: ParsedAction, context: TenantContext, arabic: bool) -> AsyncIterator[ActionStep]:
    from .tasks import RETRYABLE_STATUSES

    startable = frozenset({TaskStatus.CREATED, TaskStatus.QUEUED, *RETRYABLE_STATUSES})
    found = await asyncio.to_thread(_find_tasks, context, action.name or LAST, startable)
    if not found and action.name and action.name != LAST:
        # Nothing waiting by that name: say where it stands if it exists at all.
        others = await asyncio.to_thread(_find_tasks, context, action.name, None)
        if len(others) == 1:
            async for step in _status_of(ParsedAction(ActionKind.TASK_STATUS, name=others[0].title), context, arabic):
                yield step
            return
    task, problem = _one_task(action.kind, action.name or LAST, found, arabic)
    if problem is not None or task is None:
        yield problem  # type: ignore[misc]
        return
    retry = task.status in RETRYABLE_STATUSES
    lead = _say(
        arabic,
        f"{'Started task' if not retry else 'Started a new run of task'} {_q(task.title, False)}.",
        f"{'بدأت تشغيل المهمة' if not retry else 'أعدت تشغيل المهمة'} {_q(task.title, True)}.",
    )
    async for step in _started(runtime, action.kind, task, context, arabic, lead, retry=retry):
        yield step


def _create_quick_command(runtime: VoiceTaskRuntime, context: TenantContext, name: str):  # type: ignore[no-untyped-def]
    """The quick command named ``name`` created as a command (``POST /commands``), or the
    candidates when it is missing or ambiguous."""
    from .commands import CommandCreate, CommandSource, Planner, command_store, create_command_record, language_of

    with _units(context) as repository:
        store = command_store(repository, context)
        quick = store.list_quick()
        needle = _key(name)
        found = []
        for test in (
            lambda q: _key(q.title) == needle,
            lambda q: _key(q.title).startswith(needle),
            lambda q: needle in _key(q.title) or needle in _key(q.text),
        ):
            found = [q for q in quick if test(q)]
            if found:
                break
        if len(found) != 1:
            return None, found, quick
        chosen = found[0]
        locale = language_of(chosen.text)
        plan = Planner(runtime.tools, repository, context, locale).plan(chosen.text)
        payload = CommandCreate(
            text=chosen.text,
            steps=[{"instruction": step.instruction} for step in plan.steps],  # type: ignore[misc]
            source=CommandSource.VOICE,
            locale=locale,
            quick_command_id=chosen.id,
        )
        view = create_command_record(payload, runtime.tools, repository, context)
        return (chosen, view), found, quick


async def _quick_command(runtime: VoiceTaskRuntime, action: ParsedAction, context: TenantContext, arabic: bool) -> AsyncIterator[ActionStep]:
    name = action.name or ""
    created, found, every = await asyncio.to_thread(_create_quick_command, runtime, context, name)
    if created is None:
        titles = [q.title for q in (found or every)][:MAX_CANDIDATES]
        state: CardState = "ambiguous" if len(found) > 1 else "not_found"
        if state == "ambiguous":
            reply = _say(arabic, f"More than one quick command matches: {', '.join(titles)}. Which one?", f"أكثر من أمر سريع يطابق: {'، '.join(titles)}. أيها تقصد؟")
        elif titles:
            reply = _say(arabic, f"I couldn't find a quick command called {_q(name, False)}. You have: {', '.join(titles)}.", f"لم أجد أمرًا سريعًا باسم {_q(name, True)}. لديك: {'، '.join(titles)}.")
        else:
            reply = _say(arabic, "You have no quick commands yet. Save one in the Command Center, then ask again.", "ليس لديك أوامر سريعة بعد. احفظ أمرًا في مركز الأوامر ثم اطلب مرة أخرى.")
        yield ActionStep(VoiceActionCard(kind=action.kind, state=state, summary=reply, candidates=titles, error_code="quick_command_not_found"), reply)
        return
    quick, view = created
    task_ids = [step.task_id for step in view.steps if step.task_id]
    card = VoiceActionCard(
        kind=action.kind, state="running", title=quick.title, command_id=view.id,
        task_id=task_ids[0] if task_ids else None, steps=len(task_ids), task_status=TaskStatus.RUNNING,
    )
    yield ActionStep(card)

    async def start(task_id: str):  # type: ignore[no-untyped-def]
        return await runtime.run(context, task_id)

    # Every step starts in parallel, as the Command Center does after "Start execution".
    results = await asyncio.gather(*(start(task_id) for task_id in task_ids), return_exceptions=True)
    done = sum(1 for r in results if not isinstance(r, BaseException) and r.task.status == TaskStatus.COMPLETED)
    waiting = sum(1 for r in results if not isinstance(r, BaseException) and r.task.status == TaskStatus.WAITING_APPROVAL)
    failed = sum(1 for r in results if isinstance(r, BaseException) or r.task.status == TaskStatus.FAILED)
    running = len(results) - done - waiting - failed
    parts_en = [f"{n} {word}" for n, word in ((done, "done"), (running, "running"), (waiting, "waiting for approval"), (failed, "failed")) if n]
    parts_ar = [f"{n} {word}" for n, word in ((done, "مكتملة"), (running, "قيد التشغيل"), (waiting, "بانتظار الموافقة"), (failed, "فشلت")) if n]
    steps_word = "step" if len(task_ids) == 1 else "steps"
    reply = _say(
        arabic,
        f"Started your quick command {_q(quick.title, False)} with {len(task_ids)} {steps_word}: {', '.join(parts_en)}."
        + (" I've sent the approvals to Approvals." if waiting else ""),
        f"بدأت أمرك السريع {_q(quick.title, True)} بعدد {len(task_ids)} من الخطوات: {'، '.join(parts_ar)}."
        + (" أرسلت الموافقات إلى الموافقات." if waiting else ""),
    )
    state_now: CardState = "approval" if waiting else "failed" if failed == len(results) else "completed" if done == len(results) else "running"
    yield ActionStep(card.model_copy(update={"state": state_now, "task_status": None, "approvals_pending": waiting, "summary": reply}), reply)


@dataclass
class PerformedAction:
    """The final card and reply of :func:`perform` (the non-streamed ``/ask``)."""

    card: VoiceActionCard | None = None
    reply: str = ""
    cards: list[VoiceActionCard] = field(default_factory=list)


async def perform_all(action: ParsedAction, context: TenantContext, arabic: bool) -> PerformedAction:
    done = PerformedAction()
    async for step in perform(action, context, arabic):
        done.cards.append(step.card)
        done.card = step.card
        if step.reply is not None:
            done.reply = step.reply
    return done

