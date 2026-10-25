"""The executive PA's daily briefing: structured facts from real records, rendered by a template.

Nothing here is invented and no model is called. Each section reads one governed source with
the caller's own permissions in the caller's tenant and workspace (the same stores the
screens use), and answers one of four states:

* ``ok``: the source has records; the section lists them (at most :data:`MAX_ITEMS`).
* ``empty``: the source was read and holds nothing to report ("Nothing to report").
* ``not_connected``: the source could not be read on this server ("Not connected").
* ``restricted``: the caller's role may not read the source ("Not available for your role").

Every item names its source and carries a link (``view`` plus the record id) so the screen can
open the task, approval or meeting. The headline of a section is a fixed template (English or
Arabic) filled from the section's counts, and :func:`figures_match` checks that every figure in
the rendered text is one of the facts (the pattern of ``voice_figures``: no number is spoken
that the data does not hold). Days are UTC days (the workspace has no time zone setting yet).

See docs/ANUM_MINE_EXECUTIVE_PA.md#daily-briefing.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException

from .authorization import Permission
from .dependencies import has_permission
from .repository import AnumRepository
from .schemas import TaskStatus, TenantContext
from .tasks import TaskFilters

Lang = Literal["en", "ar"]
MAX_ITEMS = 5
READ_LIMIT = 200

SECTION_KEYS = ("approvals", "blocked", "due", "running", "meetings", "agents", "cost")

_ACTIVE = frozenset({TaskStatus.CREATED, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL})
_BLOCKED = frozenset({TaskStatus.WAITING_APPROVAL, TaskStatus.FAILED})

# -- templates (the only wording the briefing has) ------------------------------------------

TEXT: dict[str, dict[str, str]] = {
    "en": {
        "title": "Daily briefing",
        "empty": "Nothing to report.",
        "not_connected": "Not connected.",
        "restricted": "Not available for your role.",
        "approvals": "Approvals",
        "blocked": "Blocked work",
        "due": "Tasks due",
        "running": "Agent runs",
        "meetings": "Meetings today",
        "agents": "Agents needing attention",
        "cost": "Cost against budget",
        "approvals.ok": "{count} approvals are waiting for a decision.",
        "approvals.ok.one": "1 approval is waiting for a decision.",
        "blocked.ok": "{count} tasks are blocked (waiting for approval or failed).",
        "blocked.ok.one": "1 task is blocked (waiting for approval or failed).",
        "due.ok": "{overdue} overdue and {today} due today.",
        "running.ok": "{count} agent runs are running now.",
        "running.ok.one": "1 agent run is running now.",
        "meetings.ok": "{count} meetings today.",
        "meetings.ok.one": "1 meeting today.",
        "agents.ok": "{count} agents need attention.",
        "agents.ok.one": "1 agent needs attention.",
        "cost.limit": "USD {used} spent this month of a USD {limit} limit.",
        "cost.nolimit": "USD {used} spent this month. No monthly limit is set.",
        "truncated": "Shows the first {count} records only.",
        "agent.paused": "paused",
        "agent.failed": "has a failed task",
        "overdue": "overdue",
        "today": "due today",
        "calendar": "calendar",
        "live": "live meeting, in progress",
        "spoken.none": "Nothing to report today.",
        "note.live_meetings": "Live meeting sessions: not connected.",
    },
    "ar": {
        "title": "الإحاطة اليومية",
        "empty": "لا شيء للإبلاغ عنه.",
        "not_connected": "غير متصل.",
        "restricted": "غير متاح لدورك.",
        "approvals": "الموافقات",
        "blocked": "العمل المعطل",
        "due": "المهام المستحقة",
        "running": "تشغيلات الوكلاء",
        "meetings": "اجتماعات اليوم",
        "agents": "وكلاء يحتاجون إلى انتباه",
        "cost": "التكلفة مقابل الميزانية",
        "approvals.ok": "{count} موافقات بانتظار قرار.",
        "approvals.ok.one": "موافقة واحدة بانتظار قرار.",
        "blocked.ok": "{count} مهام معطلة (بانتظار الموافقة أو فاشلة).",
        "blocked.ok.one": "مهمة واحدة معطلة (بانتظار الموافقة أو فاشلة).",
        "due.ok": "{overdue} متأخرة و{today} مستحقة اليوم.",
        "running.ok": "{count} تشغيلات للوكلاء قيد العمل الآن.",
        "running.ok.one": "تشغيل واحد للوكلاء قيد العمل الآن.",
        "meetings.ok": "{count} اجتماعات اليوم.",
        "meetings.ok.one": "اجتماع واحد اليوم.",
        "agents.ok": "{count} وكلاء يحتاجون إلى انتباه.",
        "agents.ok.one": "وكيل واحد يحتاج إلى انتباه.",
        "cost.limit": "أُنفق {used} دولارا هذا الشهر من حد قدره {limit} دولارا.",
        "cost.nolimit": "أُنفق {used} دولارا هذا الشهر. لا يوجد حد شهري.",
        "truncated": "تُعرض أول {count} سجلات فقط.",
        "agent.paused": "متوقف مؤقتا",
        "agent.failed": "لديه مهمة فاشلة",
        "overdue": "متأخرة",
        "today": "مستحقة اليوم",
        "calendar": "التقويم",
        "live": "اجتماع مباشر قيد التنفيذ",
        "spoken.none": "لا شيء للإبلاغ عنه اليوم.",
        "note.live_meetings": "جلسات الاجتماعات المباشرة: غير متصلة.",
    },
}

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _money(value: float) -> str:
    return f"{value:.2f}"


def _item(kind: str, record_id: str, label: str, view: str, detail: str | None = None, at: datetime | None = None, source: str | None = None) -> dict[str, Any]:
    return {
        "kind": kind, "id": record_id, "label": label, "detail": detail,
        "at": at.isoformat() if at else None,
        "source": source or kind,
        "link": {"view": view, "id": record_id},
    }


def _section(key: str, state: str, count: int | None = None, items: list[dict[str, Any]] | None = None,
             facts: dict[str, Any] | None = None, truncated: bool = False) -> dict[str, Any]:
    return {"key": key, "state": state, "count": count, "items": items or [], "facts": facts or {}, "truncated": truncated, "notes": []}


def _guard(key: str, load: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """A source that cannot be read is "not connected": its error text is never shown."""
    try:
        return load()
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            return _section(key, "restricted")
        return _section(key, "not_connected")
    except Exception:  # noqa: BLE001 - a missing source is stated as such, never guessed or leaked
        return _section(key, "not_connected")


# -- the sources ---------------------------------------------------------------------------


def _approvals(context: TenantContext, repository: AnumRepository, now: datetime) -> dict[str, Any]:
    if not has_permission(context, Permission.APPROVAL_READ):
        return _section("approvals", "restricted")
    from .approval_paging import ApprovalFilter

    rows = repository.list_approvals_page(context, now=now, status=ApprovalFilter.PENDING, query=None, task_id=None, cursor=None, limit=READ_LIMIT)[0]
    if not rows:
        return _section("approvals", "empty", 0)
    items = [_item("approval", a.id, a.action, "approvals", a.risk_level.value) for a in rows[:MAX_ITEMS]]
    return _section("approvals", "ok", len(rows), items, truncated=len(rows) >= READ_LIMIT)


def _tasks(context: TenantContext, repository: AnumRepository, statuses: frozenset[TaskStatus]) -> list[Any]:
    return list(repository.list_tasks(context, limit=READ_LIMIT, filters=TaskFilters(statuses=statuses)))


def _blocked(context: TenantContext, repository: AnumRepository, now: datetime) -> dict[str, Any]:
    tasks = _tasks(context, repository, _BLOCKED)
    if not tasks:
        return _section("blocked", "empty", 0)
    items = [_item("task", t.id, t.title, "tasks", t.status.value) for t in tasks[:MAX_ITEMS]]
    return _section("blocked", "ok", len(tasks), items, truncated=len(tasks) >= READ_LIMIT)


def _due(context: TenantContext, repository: AnumRepository, now: datetime) -> dict[str, Any]:
    tasks = [t for t in _tasks(context, repository, _ACTIVE) if t.due_at is not None]
    end_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    overdue = sorted((t for t in tasks if t.due_at < now), key=lambda t: t.due_at)
    today = sorted((t for t in tasks if now <= t.due_at < end_of_day), key=lambda t: t.due_at)
    if not overdue and not today:
        return _section("due", "empty", 0)
    ordered = [(t, "overdue") for t in overdue] + [(t, "today") for t in today]
    items = [_item("task", t.id, t.title, "tasks", tag, t.due_at) for t, tag in ordered[:MAX_ITEMS]]
    return _section("due", "ok", len(ordered), items, {"overdue": len(overdue), "today": len(today)})


def _running(context: TenantContext, repository: AnumRepository, now: datetime) -> dict[str, Any]:
    tasks = _tasks(context, repository, frozenset({TaskStatus.RUNNING}))
    if not tasks:
        return _section("running", "empty", 0)
    items = [_item("task", t.id, t.title, "tasks", t.agent_id, t.updated_at) for t in tasks[:MAX_ITEMS]]
    return _section("running", "ok", len(tasks), items, truncated=len(tasks) >= READ_LIMIT)


def _meetings(context: TenantContext, repository: AnumRepository, now: datetime) -> dict[str, Any]:
    if not has_permission(context, Permission.CALENDAR_READ):
        return _section("meetings", "restricted")
    from .calendar_api import open_calendar_store
    from .live_agent_meeting_api import _store as live_store

    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with open_calendar_store(context) as calendar:
        events = list(calendar.between(start, start + timedelta(days=1), include_cancelled=False, limit=50))
    notes: list[str] = []
    live: list[dict[str, Any]] = []
    try:
        with live_store(context) as meetings:
            live = [r for r in meetings.list(context, cursor=None, limit=100)["items"] if r.get("status") in {"active", "paused"}]
    except HTTPException:
        # The calendar was read; the live meeting sessions were not, and the briefing says so.
        notes.append("live_meetings")
    items = [_item("event", e.id, e.title, "calendar", None, e.starts_at, "calendar") for e in events]
    items += [_item("meeting", str(r["id"]), str(r.get("title") or r["id"]), "meetings", str(r.get("status")), None, "live") for r in live]
    total = len(items)
    if total == 0 and not notes:
        return _section("meetings", "empty", 0)
    section = _section("meetings", "ok" if total else "empty", total, items[:MAX_ITEMS])
    section["notes"] = notes
    return section


def _agents(context: TenantContext, repository: AnumRepository, now: datetime) -> dict[str, Any]:
    from .agent_profiles import AgentStatus, open_agent_store

    with open_agent_store(context) as store:
        profiles = {p.id: p for p in store.list(context)}
    failed_agents = {t.agent_id for t in _tasks(context, repository, frozenset({TaskStatus.FAILED})) if t.agent_id}
    flagged: list[tuple[str, str, str]] = []
    for profile in sorted(profiles.values(), key=lambda p: (p.name.lower(), p.id)):
        if profile.status == AgentStatus.PAUSED:
            flagged.append((profile.id, profile.name, "paused"))
        elif profile.id in failed_agents:
            flagged.append((profile.id, profile.name, "failed"))
    if not flagged:
        return _section("agents", "empty", 0)
    items = [_item("agent", i, n, "agents", reason) for i, n, reason in flagged[:MAX_ITEMS]]
    return _section("agents", "ok", len(flagged), items)


def _cost(context: TenantContext, repository: AnumRepository, now: datetime) -> dict[str, Any]:
    if not has_permission(context, Permission.ORGANIZATION_MANAGE):
        return _section("cost", "restricted")
    from .model_budget import _overview

    scope = _overview(context, now).workspace
    used = round(scope.usage.estimated_cost_usd, 2)
    limit = scope.budget.monthly_cost_limit_usd if scope.budget else None
    if used == 0 and limit is None:
        return _section("cost", "empty")
    facts: dict[str, Any] = {"used": _money(used), "limit": None if limit is None else _money(limit), "exceeded": scope.exceeded}
    item = _item("budget", "workspace", "workspace", "budgets", None, None, "budget")
    return _section("cost", "ok", None, [item], facts)


_LOADERS: dict[str, Callable[[TenantContext, AnumRepository, datetime], dict[str, Any]]] = {
    "approvals": _approvals, "blocked": _blocked, "due": _due, "running": _running,
    "meetings": _meetings, "agents": _agents, "cost": _cost,
}


# -- rendering -----------------------------------------------------------------------------


def headline(section: dict[str, Any], lang: Lang) -> str:
    text = TEXT[lang]
    state, key, count = section["state"], section["key"], section["count"]
    if state != "ok":
        return text[state]
    if key == "due":
        return text["due.ok"].format(**section["facts"])
    if key == "cost":
        facts = section["facts"]
        return text["cost.nolimit" if facts["limit"] is None else "cost.limit"].format(used=facts["used"], limit=facts["limit"])
    return text[f"{key}.ok.one" if count == 1 else f"{key}.ok"].format(count=count)


def _detail(section: dict[str, Any], item: dict[str, Any], lang: Lang) -> str | None:
    text = TEXT[lang]
    key, detail = section["key"], item.get("detail")
    if key == "due":
        return text["overdue" if detail == "overdue" else "today"]
    if key == "agents":
        return text["agent.paused" if detail == "paused" else "agent.failed"]
    if key == "meetings":
        return text["calendar" if item["source"] == "calendar" else "live"]
    if key == "running":
        return None
    return detail


def figures_match(rendered: str, facts: Iterable[Any]) -> bool:
    """True when every figure in ``rendered`` is one of ``facts`` (the guard of ``voice_figures``)."""
    allowed = {n for fact in facts for n in _NUMBER.findall(str(fact).translate(_DIGITS))}
    found = _NUMBER.findall(rendered.translate(_DIGITS))
    return all(n in allowed for n in found)


def _section_figures(section: dict[str, Any]) -> list[Any]:
    out: list[Any] = [section["count"] if section["count"] is not None else ""]
    out += [v for v in section["facts"].values() if isinstance(v, (int, str)) and not isinstance(v, bool)]
    if section["truncated"]:
        out.append(READ_LIMIT)
    return out


def build_briefing(context: TenantContext, repository: AnumRepository, now: datetime, lang: Lang = "en") -> dict[str, Any]:
    """The whole briefing: ``sections`` in a fixed order, each with its state, count, items and
    rendered ``headline``; ``text`` is the same briefing as plain lines (used for speech)."""
    text = TEXT[lang]
    sections: list[dict[str, Any]] = []
    for key in SECTION_KEYS:
        section = _guard(key, lambda k=key: _LOADERS[k](context, repository, now))
        section["title"] = text[key]
        section["headline"] = headline(section, lang)
        # A headline whose figures are not in the section's facts is never shown.
        if not figures_match(section["headline"], _section_figures(section)):
            section.update(state="not_connected", count=None, items=[], facts={}, headline=text["not_connected"])
        if section["truncated"]:
            section["headline"] += " " + text["truncated"].format(count=READ_LIMIT)
        section["note_texts"] = [text[f"note.{n}"] for n in section["notes"]]
        for item in section["items"]:
            item["detail_text"] = _detail(section, item, lang)
        sections.append(section)
    reportable = [s for s in sections if s["state"] == "ok"]
    lines = [f"{s['title']}: {s['headline']}" for s in reportable] or [text["spoken.none"]]
    return {
        "version": 1, "lang": lang, "generated_at": now.isoformat(), "date": now.date().isoformat(),
        "title": text["title"], "sections": sections, "text": " ".join(lines),
        "reportable": len(reportable),
    }
