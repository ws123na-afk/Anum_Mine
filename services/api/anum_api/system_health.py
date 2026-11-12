"""System health for the owner of this deployment (docs/system-health.md).

``GET /api/v1/platform/health`` (platform operators only, read-only) answers, from real state on this
server, the questions an owner of a one-VM deployment has to ask every day:

* **Backups**: the last successful local and off-site backup and its age, and the last restore drill,
  as ``infra/oracle/backup.sh`` records them in its status folder (``ANUM_BACKUP_STATUS_DIR``,
  mounted read-only; times and ok only).
* **AI model**: whether answers come from a real model or the ``mock`` placeholder
  (``ANUM_MODEL_PROVIDER``), and whether a hosted provider has its key. The key is never shown.
* **Disk and memory headroom**: ``shutil.disk_usage`` of the backup folder (the VM's disk) or ``/``,
  ``/proc/meminfo`` and the container's cgroup limit when it has one. Read-only files, no command.
* **Email sending**: whether ``ANUM_SMTP_HOST`` and ``ANUM_SMTP_FROM`` are set (never the password).
* **Background jobs**: whether the scheduler is on and its loops ran recently (heartbeats).
* **Errors**: how many error log records this API process wrote in the last 24 hours.
* **Restarts**: when the API last restarted without a clean shutdown (an audit record).

Each item is green, amber, red or unknown, with the numbers behind it; the web page and the phone
say what to do for each. Nothing here changes anything, and no secret, path or log text is returned.

**Notices.** With ``ANUM_SYSTEM_HEALTH_TENANT_ID`` and ``ANUM_SYSTEM_HEALTH_WORKSPACE_ID`` set (the
owner's own organisation and workspace), a monitor checks every ``ANUM_SYSTEM_HEALTH_CHECK_SECONDS``
(default an hour) and, when something is red, gives every active owner of that workspace one in-app
notice a UTC day (kind ``system_health``) listing the red items and their fixes. At start it also
compares the audit log: when the previous start has no clean stop after it, the API stopped without
shutting down (a crash, the kernel's out-of-memory killer, a power cut), so the owners get a
``system_restart`` notice at once. This assumes one API replica, as on the Oracle VM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends

from .audit import AuditFilter, AuditRecord
from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .schemas import TenantContext, new_id, utc_now
from .settings import settings

logger = logging.getLogger("anum.system_health")

State = Literal["green", "amber", "red", "unknown"]
RANK: dict[str, int] = {"green": 0, "unknown": 1, "amber": 2, "red": 3}

BACKUP_MAX_AGE = timedelta(hours=26)
DRILL_MAX_AGE = timedelta(days=35)
ERRORS_AMBER = 1
ERRORS_RED = 50
DISK_RED_PCT = 10.0
DISK_AMBER_PCT = 20.0
MEMORY_RED_PCT = 10.0
MEMORY_AMBER_PCT = 20.0
RESTART_RED = timedelta(hours=24)
RESTART_AMBER = timedelta(days=7)
STARTING_GRACE = timedelta(minutes=5)

SYSTEM_ACTOR = "system:health"
API_TARGET = "system:api"
STARTED = "system.api_started"
STOPPED = "system.api_stopped"
RESTARTED = "system.api_restarted_unexpectedly"
NOTICE_SENT = "system.health_notice_sent"
NOTICE_KIND = "system_health"
RESTART_KIND = "system_restart"

STARTED_AT = utc_now()

# --------------------------------------------------------------------------- process signals

_beats: dict[str, datetime] = {}
_beats_lock = threading.Lock()


def heartbeat(name: str, when: datetime | None = None) -> None:
    """A background loop finished a pass (the scheduler check reads these)."""
    with _beats_lock:
        _beats[name] = when or utc_now()


def last_beat(name: str) -> datetime | None:
    with _beats_lock:
        return _beats.get(name)


def clear_beats() -> None:
    with _beats_lock:
        _beats.clear()


class ErrorCounter(logging.Handler):
    """Counts ERROR and CRITICAL log records of this process (times only, never the text)."""

    def __init__(self, keep: int = 10_000) -> None:
        super().__init__(level=logging.ERROR)
        self._times: deque[datetime] = deque(maxlen=keep)
        self._lock_times = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock_times:
            self._times.append(datetime.fromtimestamp(record.created, tz=UTC))

    def count_since(self, since: datetime) -> int:
        with self._lock_times:
            return sum(1 for when in self._times if when >= since)

    def last(self) -> datetime | None:
        with self._lock_times:
            return self._times[-1] if self._times else None

    def reset(self) -> None:
        with self._lock_times:
            self._times.clear()


error_counter = ErrorCounter()


def install_error_counter() -> None:
    root = logging.getLogger()
    if error_counter not in root.handlers:
        root.addHandler(error_counter)


# --------------------------------------------------------------------------- readers


def _read_status(kind: str) -> dict[str, Any] | None:
    """``last-<kind>.json`` from backup.sh's status folder: ok and times only."""
    folder = (settings.backup_status_dir or "").strip()
    if not folder:
        return None
    path = Path(folder) / f"last-{kind}.json"
    try:
        if not path.is_file() or path.stat().st_size > 4096:
            return None
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        "ok": bool(data.get("ok")),
        "finished_at": _when(data.get("finished_at")),
        "last_success_at": _when(data.get("last_success_at")),
    }


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip()[:40].replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _age_hours(value: datetime | None, now: datetime) -> float | None:
    return round((now - value).total_seconds() / 3600, 1) if value else None


def _item(key: str, state: State, **detail: Any) -> dict[str, Any]:
    return {"key": key, "state": state, "detail": detail}


def backup_item(kind: Literal["local", "offsite"], now: datetime) -> dict[str, Any]:
    key = f"backup_{kind}"
    recorded = bool((settings.backup_status_dir or "").strip())
    status = _read_status(kind)
    if status is None:
        return _item(key, "unknown" if not recorded else "red", recorded=recorded, ever=False)
    success = status["last_success_at"]
    age = _age_hours(success, now)
    detail = {"recorded": True, "ever": success is not None, "last_ok": status["ok"], "last_run_at": _iso(status["finished_at"]),
              "last_success_at": _iso(success), "age_hours": age, "max_age_hours": int(BACKUP_MAX_AGE.total_seconds() // 3600)}
    if success is None or now - success > BACKUP_MAX_AGE:
        return _item(key, "red", **detail)
    return _item(key, "green" if status["ok"] else "amber", **detail)


def drill_item(now: datetime) -> dict[str, Any]:
    recorded = bool((settings.backup_status_dir or "").strip())
    status = _read_status("drill")
    if status is None:
        return _item("restore_drill", "unknown" if not recorded else "amber", recorded=recorded, ever=False)
    success = status["last_success_at"]
    detail = {"recorded": True, "ever": success is not None, "last_ok": status["ok"], "last_run_at": _iso(status["finished_at"]),
              "last_success_at": _iso(success), "age_days": round((now - success).total_seconds() / 86400, 1) if success else None,
              "max_age_days": DRILL_MAX_AGE.days}
    if not status["ok"]:
        return _item("restore_drill", "red", **detail)
    if success is None or now - success > DRILL_MAX_AGE:
        return _item("restore_drill", "amber", **detail)
    return _item("restore_drill", "green", **detail)


def model_item() -> dict[str, Any]:
    from .model_status import server_model_state

    model = server_model_state()
    detail = {"provider": model["provider"], "placeholder": model["placeholder"], "key_missing": model["key_missing"]}
    return _item("ai_model", "red" if model["placeholder"] or model["key_missing"] else "green", **detail)


def disk_item() -> dict[str, Any]:
    folder = (settings.backup_status_dir or "").strip()
    target = folder if folder and Path(folder).is_dir() else "/"
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        return _item("disk", "unknown", readable=False)
    free_pct = round(usage.free * 100 / usage.total, 1) if usage.total else 0.0
    state: State = "red" if free_pct < DISK_RED_PCT else "amber" if free_pct < DISK_AMBER_PCT else "green"
    return _item("disk", state, readable=True, free_bytes=usage.free, total_bytes=usage.total, free_pct=free_pct,
                 scope="backups" if target != "/" else "root")


def _read_int(path: str) -> int | None:
    try:
        raw = Path(path).read_text("ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(raw) if raw.isdigit() else None


def _meminfo() -> tuple[int, int] | None:
    try:
        lines = Path("/proc/meminfo").read_text("ascii").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    values: dict[str, int] = {}
    for line in lines:
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[name.strip()] = int(parts[0]) * 1024
    if "MemTotal" in values and "MemAvailable" in values and values["MemTotal"] > 0:
        return values["MemAvailable"], values["MemTotal"]
    return None


def memory_item() -> dict[str, Any]:
    host = _meminfo()
    limit, used = _read_int("/sys/fs/cgroup/memory.max"), _read_int("/sys/fs/cgroup/memory.current")
    detail: dict[str, Any] = {"readable": host is not None}
    pcts: list[float] = []
    if host is not None:
        available, total = host
        detail.update(available_bytes=available, total_bytes=total, available_pct=round(available * 100 / total, 1))
        pcts.append(detail["available_pct"])
    if limit and used is not None and limit < (1 << 60):
        detail.update(api_limit_bytes=limit, api_used_bytes=used, api_free_pct=round(max(0, limit - used) * 100 / limit, 1))
        pcts.append(detail["api_free_pct"])
    if not pcts:
        return _item("memory", "unknown", **detail)
    low = min(pcts)
    return _item("memory", "red" if low < MEMORY_RED_PCT else "amber" if low < MEMORY_AMBER_PCT else "green", **detail)


def email_item() -> dict[str, Any]:
    configured = bool((settings.smtp_host or "").strip() and (settings.smtp_from or "").strip())
    return _item("email", "green" if configured else "amber", configured=configured)


def scheduler_item(now: datetime) -> dict[str, Any]:
    if not settings.automation_scheduler_enabled:
        return _item("scheduler", "amber", enabled=False)
    beat = last_beat("team_reminders")
    window = timedelta(seconds=max(300.0, settings.team_reminder_poll_seconds * 3))
    detail = {"enabled": True, "last_pass_at": _iso(beat), "sweep_last_pass_at": _iso(last_beat("command_sweep")),
              "window_minutes": int(window.total_seconds() // 60)}
    if beat is not None and now - beat <= window:
        return _item("scheduler", "green", **detail)
    if beat is None and now - STARTED_AT <= STARTING_GRACE:
        return _item("scheduler", "green", starting=True, **detail)
    return _item("scheduler", "red", **detail)


def errors_item(now: datetime) -> dict[str, Any]:
    count = error_counter.count_since(now - timedelta(hours=24))
    state: State = "red" if count >= ERRORS_RED else "amber" if count >= ERRORS_AMBER else "green"
    return _item("errors", state, count_24h=count, last_at=_iso(error_counter.last()), since=_iso(max(STARTED_AT, now - timedelta(hours=24))))


# --------------------------------------------------------------------------- the owner's scope and the audit record


def health_scope() -> TenantContext | None:
    tenant_id = (settings.system_health_tenant_id or "").strip()
    workspace_id = (settings.system_health_workspace_id or "").strip()
    if not tenant_id or not workspace_id:
        return None
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=SYSTEM_ACTOR, roles=[])


def _latest(context: TenantContext, *, action: str | None = None, target: str | None = None,
            since: datetime | None = None) -> AuditRecord | None:
    from .dependencies import _unit_of_work

    with _unit_of_work(context, require_membership=False) as repository:
        found = repository.list_audit_page(context, AuditFilter(action=action, target=target, since=since), limit=1)
    return found[0] if found else None


def _audit(context: TenantContext, action: str, metadata: dict[str, Any], when: datetime | None = None) -> None:
    from .dependencies import _unit_of_work

    with _unit_of_work(context, require_membership=False) as repository:
        repository.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=SYSTEM_ACTOR,
            action=action, target=API_TARGET, outcome="success", correlation_id=new_id("correlation"),
            created_at=when or utc_now(), metadata=metadata,
        ))


def restart_item(now: datetime) -> dict[str, Any]:
    context = health_scope()
    uptime = int((now - STARTED_AT).total_seconds())
    if context is None:
        return _item("restarts", "unknown", configured=False, started_at=_iso(STARTED_AT), uptime_seconds=uptime)
    try:
        last = _latest(context, action=RESTARTED, target=API_TARGET)
    except Exception:
        logger.exception("system_health_restart_read_failed")
        return _item("restarts", "unknown", configured=True, readable=False, started_at=_iso(STARTED_AT), uptime_seconds=uptime)
    when = last.created_at if last else None
    detail = {"configured": True, "readable": True, "started_at": _iso(STARTED_AT), "uptime_seconds": uptime,
              "last_unexpected_at": _iso(when)}
    if when is not None and now - when <= RESTART_RED:
        return _item("restarts", "red", **detail)
    if when is not None and now - when <= RESTART_AMBER:
        return _item("restarts", "amber", **detail)
    return _item("restarts", "green", **detail)


def notices_item() -> dict[str, Any]:
    context = health_scope()
    return _item("notices", "green" if context else "amber", configured=context is not None,
                 tenant_id=context.tenant_id if context else None, workspace_id=context.workspace_id if context else None)


def evaluate(now: datetime | None = None) -> dict[str, Any]:
    """Every check, read now. Blocking (files, and the audit log for restarts)."""
    now = now or utc_now()
    items = [
        backup_item("local", now), backup_item("offsite", now), drill_item(now), model_item(), disk_item(), memory_item(),
        email_item(), scheduler_item(now), errors_item(now), restart_item(now), notices_item(),
    ]
    worst = max((item["state"] for item in items), key=lambda state: RANK[state])
    return {
        "checked_at": now.isoformat(),
        "overall": "amber" if worst == "unknown" else worst,
        "started_at": STARTED_AT.isoformat(),
        "uptime_seconds": int((now - STARTED_AT).total_seconds()),
        "counts": {state: sum(1 for item in items if item["state"] == state) for state in ("green", "amber", "red", "unknown")},
        "items": items,
    }


# --------------------------------------------------------------------------- notices

# English fixes for the in-app notice (the web page and the phone carry their own wording in both languages).
FIX = {
    "backup_local": "On the VM run: sudo bash infra/oracle/backup.sh backup, then check its output (docs/deploy-oracle.md#backups).",
    "backup_offsite": "On the VM run: sudo bash infra/oracle/backup.sh offsite; check the B2 bucket and keys in infra/oracle/.env.",
    "restore_drill": "On the VM run: sudo bash infra/oracle/backup.sh drill (docs/deploy-oracle.md#restore-drill).",
    "ai_model": "Set ANUM_MODEL_PROVIDER and ANUM_MODEL_API_KEY in infra/oracle/.env, then run infra/oracle/update.sh.",
    "disk": "Free space on the VM: old backups under /var/lib/anum/backups, docker image prune -f, or grow the boot volume.",
    "memory": "Check docker stats on the VM; restart the heaviest service or lower its memory limit.",
    "email": "Set ANUM_SMTP_HOST, ANUM_SMTP_FROM and the SMTP login in infra/oracle/.env, then run update.sh.",
    "scheduler": "Set ANUM_AUTOMATION_SCHEDULER_ENABLED=true; if it is on, read the API log (docker compose logs api).",
    "errors": "Read the API log on the VM: docker compose logs --since 24h api | grep ERROR.",
    "restarts": "Read why the API stopped: docker compose logs --since 24h api, and dmesg for the out-of-memory killer.",
    "notices": "Set ANUM_SYSTEM_HEALTH_TENANT_ID and ANUM_SYSTEM_HEALTH_WORKSPACE_ID in infra/oracle/.env.",
}
TITLE = {
    "backup_local": "Local backup", "backup_offsite": "Off-site backup", "restore_drill": "Restore drill", "ai_model": "AI model",
    "disk": "Disk space", "memory": "Memory", "email": "Email sending", "scheduler": "Background jobs", "errors": "Errors",
    "restarts": "Unexpected restart", "notices": "Owner notices",
}


def _owners(repository: Any, context: TenantContext) -> list[str]:
    return sorted({m.user_id for m in repository.list_memberships(context) if m.active and m.role.lower() == "owner"})


def _notify(context: TenantContext, kind: str, title: str, body: str, action: str, metadata: dict[str, Any]) -> int:
    from .events import CanonicalEventName, create_event
    from .notifications import create_notification
    from .team_work import open_team_unit

    with open_team_unit(context) as unit:
        owners = _owners(unit.repository, context)
        for user_id in owners:
            notification = create_notification(unit.store, context, user_id=user_id, kind=kind, title=title, body=body,
                                               work_item_id=None, in_app_only=True)
            unit.repository.record_event(create_event(
                CanonicalEventName.NOTIFICATION_CREATED, context, notification.id,
                {"recipient": user_id, "kind": kind, "work_item_id": None},
            ).event)
        unit.repository.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=SYSTEM_ACTOR,
            action=action, target=API_TARGET, outcome="success", correlation_id=new_id("correlation"), created_at=utc_now(),
            metadata={**metadata, "recipients": len(owners)},
        ))
    return len(owners)


def notice_text(report: dict[str, Any]) -> tuple[str, str] | None:
    red = [item for item in report["items"] if item["state"] == "red"]
    if not red:
        return None
    title = f"System health: {len(red)} " + ("problem needs" if len(red) == 1 else "problems need") + " you"
    lines = [f"{TITLE[item['key']]}: {FIX[item['key']]}" for item in red]
    body = " ".join(lines) + " Open the platform console, System health, for details."
    return title, body


def run_health_check(now: datetime | None = None) -> dict[str, Any]:
    """One check: when something is red and no notice went out today (UTC), notify the owners. Blocking."""
    now = now or utc_now()
    report = evaluate(now)
    heartbeat("system_health", now)
    context = health_scope()
    text = notice_text(report)
    if context is None or text is None:
        return {"overall": report["overall"], "notified": 0}
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if _latest(context, action=NOTICE_SENT, target=API_TARGET, since=day_start) is not None:
        return {"overall": report["overall"], "notified": 0}
    red = [item["key"] for item in report["items"] if item["state"] == "red"]
    told = _notify(context, NOTICE_KIND, text[0], text[1], NOTICE_SENT, {"red": red, "day": day_start.date().isoformat()})
    return {"overall": report["overall"], "notified": told}


def record_start(now: datetime | None = None) -> bool:
    """At start: notice the owners when the previous start has no clean stop after it, then record this
    start. Returns whether the previous run ended unexpectedly. Blocking."""
    context = health_scope()
    if context is None:
        return False
    now = now or utc_now()
    last = _latest(context, action="system.api_st*", target=API_TARGET)
    unexpected = last is not None and last.action == STARTED
    if unexpected and last is not None:
        previous = last.created_at
        _audit(context, RESTARTED, {"previous_start": previous.isoformat()}, now)
        _notify(context, RESTART_KIND, "ANUM restarted without a clean shutdown",
                f"The API that started at {previous:%Y-%m-%d %H:%M} UTC stopped without shutting down and started again at "
                f"{now:%Y-%m-%d %H:%M} UTC. {FIX['restarts']}", "system.restart_notice_sent", {"previous_start": previous.isoformat()})
    _audit(context, STARTED, {"started_at": STARTED_AT.isoformat()}, now)
    return unexpected


def record_stop(now: datetime | None = None) -> None:
    """At a clean shutdown: the stop the next start's restart check looks for. Blocking."""
    context = health_scope()
    if context is not None:
        _audit(context, STOPPED, {"started_at": STARTED_AT.isoformat()}, now)


class SystemHealthMonitor:
    """At start the restart check, then :func:`run_health_check` every ``interval`` seconds, in a worker
    thread; on stop the clean-stop record."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    def start(self) -> None:
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="anum-system-health")

    async def stop(self, *, timeout: float = 10.0) -> None:
        task, self._task = self._task, None
        if self._stop is not None:
            self._stop.set()
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except Exception:
                logger.exception("System health loop stopped with an error")
        try:
            await asyncio.to_thread(record_stop)
        except Exception:
            logger.exception("system_health_stop_record_failed")

    async def _run(self) -> None:
        stop = self._stop
        if stop is None:
            raise RuntimeError("System health monitor started without a stop event")
        try:
            await asyncio.to_thread(record_start)
        except Exception:
            logger.exception("system_health_start_record_failed")
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
            except TimeoutError:
                pass
            if stop.is_set():
                break
            try:
                await asyncio.to_thread(run_health_check)
            except Exception:
                logger.exception("system_health_check_failed")


def build_system_health_monitor(config: Any) -> SystemHealthMonitor | None:
    if not (config.system_health_tenant_id and config.system_health_workspace_id):
        return None
    return SystemHealthMonitor(config.system_health_check_seconds)


# --------------------------------------------------------------------------- route

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])


@router.get("/health")
def system_health(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    """The deployment's health for the platform operator (a plain ``def``: files and one audit read run in
    FastAPI's thread pool, never on the event loop)."""
    require_permission(context, Permission.PLATFORM_OPERATE)
    report = evaluate()
    # The ids the notices need, so the owner can copy them into infra/oracle/.env.
    report["you"] = {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id}
    return report


__all__ = [
    "FIX", "NOTICE_KIND", "RESTART_KIND", "SystemHealthMonitor", "build_system_health_monitor", "error_counter", "evaluate",
    "heartbeat", "install_error_counter", "record_start", "record_stop", "router", "run_health_check",
]
