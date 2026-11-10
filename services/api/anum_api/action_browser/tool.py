"""The ``web.act`` tool (docs/action-browser.md).

An ordinary registered tool: every call goes through the runtime's mediation (``ToolPolicy``
with roles and governance rules, the emergency stop, approvals with the payload hash, run
steps, events and the ``tool.call`` audit record). High risk and ``always_ask``: autonomy can
never lower it, so the scope and every binding step wait for the owner.

One task is a chain of calls. The first carries the scope the owner approves (goal, start
address, sites, logins and cards). Each later call is a *continuation* the tool asked for with
``ToolResult(status="paused", continuation=...)``; the runtime checkpoints it and pauses for an
approval bound to exactly that call (``runtime._continue``). Between two pauses the tool runs a
*segment*: it asks the planner model for one step at a time, validates it against the scope,
lets ``classify`` decide, and has the browser service act only when the page is still the one
classified. Only sockets are awaited; every database read and write is its own short unit of
work in a worker thread, never open across a browser or model call.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from ..integration_tools import read_capped
from ..model_egress import EgressPolicy, UnsafeModelEndpointError, check_endpoint
from ..schemas import RiskLevel, TenantContext, new_id, utc_now
from . import ACT_TOOL
from .card_code import CVC_MARKER, NO_CODE_AT_RUN, take_card_code
from .classify import (
    ALLOW,
    GATE,
    HANDOFF,
    REFUSE,
    Decision,
    classify,
    handoff_reason,
    signed_in_grant,
    signed_in_sites,
)
from .handles import (
    CODE_PART,
    Handle,
    host_in_sites,
    masked_handle,
    normalize_site,
    parse_handle,
    site_of,
    valid_handle_name,
)
from .planner import ActionPlanner, PlannedStep
from .store import SessionRecord, StepRecord, open_action_store

EnabledCheck = Callable[[TenantContext], bool]
PlannerFactory = Callable[[TenantContext], ActionPlanner]
MAX_JSON_BYTES = 512 * 1024
MAX_PICTURE_BYTES = 3 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 20_000_000
# What a task may save into Files from a site: never executed, checked again by Files.
DOWNLOAD_EXTENSIONS = frozenset({".pdf", ".csv", ".xlsx", ".docx", ".png", ".jpg", ".jpeg", ".txt", ".json"})
MAX_COLUMNS, MAX_ROWS, MAX_FIELDS, MAX_CELL = 20, 200, 40, 300
URL_POLICY = EgressPolicy(enforce=True)
UNAVAILABLE_SERVICE = (
    "Not available on this server: the browser service is not set up (the operator sets ANUM_BROWSER_RESEARCH_URL and "
    "ANUM_BROWSER_RESEARCH_TOKEN; docs/action-browser.md)."
)
UNAVAILABLE_SWITCH = (
    "Not available on this server: the operator has not turned on the action browser "
    "(ANUM_ACTION_BROWSER_ENABLED=true; docs/action-browser.md)."
)
UNAVAILABLE_MODE = (
    "Not available on this server: the browser service does not run in action mode "
    "(the operator sets ANUM_BROWSER_ACTION_MODE=1; docs/action-browser.md)."
)
DISABLED_TEXT = "The action browser is off for this workspace. A workspace owner turns it on under Integrations, Action browser."
SESSION_GONE = "The browser session has ended (it closes after a while without use, or when the task stops). Start the task again."


class ServiceAnswer(Exception):
    """The browser service answered with an error status. ``status`` and ``body`` say which."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(str(body.get("message") or body.get("detail") or status))
        self.status = status
        self.body = body


class ActionServiceClient:
    """The API's side of the session routes. Tests pass ``client`` on a mock transport (a fake service)."""

    def __init__(self, base_url: str, token: str, *, timeout_seconds: float = 60.0, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None, *, picture: bool = False) -> tuple[int, Any]:
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False, trust_env=False)
        owns = self._client is None
        headers = {"authorization": f"Bearer {self._token}", "accept-encoding": "identity"}
        content = None
        if body is not None:
            headers["content-type"] = "application/json"
            content = json.dumps(body).encode()
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with client.stream(method, f"{self.base_url}{path}", content=content, headers=headers) as response:
                    raw, truncated = await read_capped(response, MAX_PICTURE_BYTES if picture else MAX_JSON_BYTES)
                    if truncated:
                        raise ValueError("browser service answer too large")
                    status = response.status_code
                    kind = response.headers.get("content-type", "")
        finally:
            if owns:
                await client.aclose()
        if picture and status == 200 and kind.startswith("image/"):
            return status, raw
        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError):
            raise ValueError("not json") from None
        return status, value if isinstance(value, dict) else {}

    async def _json(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        status, value = await self._request(method, path, body)
        if status != 200:
            raise ServiceAnswer(status, value if isinstance(value, dict) else {})
        return value

    async def health(self) -> dict[str, Any]:
        status, value = await self._request("GET", "/healthz")
        return {**(value if isinstance(value, dict) else {}), "http_status": status}

    async def create(self, sites: list[str]) -> dict[str, Any]:
        return await self._json("POST", "/v1/sessions", {"sites": sites})

    async def snapshot(self, browser_id: str) -> dict[str, Any]:
        return await self._json("GET", f"/v1/sessions/{browser_id}/snapshot")

    async def inspect(self, browser_id: str, action: dict[str, Any]) -> dict[str, Any]:
        return await self._json("POST", f"/v1/sessions/{browser_id}/inspect", {"action": action})

    async def act(self, browser_id: str, action: dict[str, Any], expect: dict[str, Any] | None, secret: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"action": action, "expect": expect}
        if secret is not None:
            body["secret"] = secret
        try:
            return await self._json("POST", f"/v1/sessions/{browser_id}/act", body)
        finally:
            body.pop("secret", None)

    async def screenshot(self, browser_id: str) -> bytes | None:
        status, value = await self._request("GET", f"/v1/sessions/{browser_id}/screenshot", picture=True)
        return value if status == 200 and isinstance(value, bytes) else None

    async def download(self, browser_id: str, download_id: str) -> tuple[str, bytes] | None:
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False, trust_env=False)
        owns = self._client is None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with client.stream("GET", f"{self.base_url}/v1/sessions/{browser_id}/downloads/{download_id}",
                                         headers={"authorization": f"Bearer {self._token}", "accept-encoding": "identity"}) as response:
                    raw, truncated = await read_capped(response, MAX_DOWNLOAD_BYTES)
                    if response.status_code != 200 or truncated:
                        return None
                    return response.headers.get("x-download-name", "download"), raw
        finally:
            if owns:
                await client.aclose()

    async def owner_input(self, browser_id: str, event: dict[str, Any]) -> dict[str, Any]:
        return await self._json("POST", f"/v1/sessions/{browser_id}/input", event)

    async def add_sites(self, browser_id: str, sites: list[str]) -> dict[str, Any]:
        return await self._json("POST", f"/v1/sessions/{browser_id}/domains", {"sites": sites})

    async def close(self, browser_id: str) -> None:
        try:
            await self._request("DELETE", f"/v1/sessions/{browser_id}")
        except (httpx.HTTPError, ValueError, TimeoutError):
            pass


def check_start_url(url: str) -> str:
    """The start address the owner gave: https, a public host name in standard form, no credentials."""
    text = (url or "").strip()
    if not text or len(text) > 2048 or any(ch.isspace() or ord(ch) < 32 for ch in text):
        raise ValueError("The start address is empty, too long or has spaces.")
    parts = urlsplit(text)
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise ValueError("The start address must be an https address.")
    if parts.username or parts.password:
        raise ValueError("The start address cannot carry a user name or password.")
    try:
        check_endpoint(text, URL_POLICY)
    except UnsafeModelEndpointError:
        raise ValueError("That address is not a public internet site.") from None
    return urlunsplit(("https", parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _today(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


class ActionBrowserTool:
    def __init__(
        self,
        *,
        problem: str | None,
        client: ActionServiceClient | None,
        planner_factory: PlannerFactory,
        enabled: EnabledCheck | None = None,
        max_steps: int = 60,
        task_seconds: float = 1200.0,
        segment_seconds: float = 240.0,
        tasks_per_day: int = 20,
        approval_seconds: int = 900,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.problem = problem
        self.client = client
        self.planner_factory = planner_factory
        self._enabled = enabled
        self.max_steps = max_steps
        self.task_seconds = task_seconds
        self.segment_seconds = segment_seconds
        self.tasks_per_day = tasks_per_day
        self.approval_seconds = approval_seconds
        self._clock = clock

    # ------------------------------------------------------------------ entry

    async def __call__(self, call: ToolCall, context: TenantContext) -> ToolResult:
        args = call.arguments
        if self.problem is not None or self.client is None:
            return _done("failed", self.problem or UNAVAILABLE_SERVICE, {"outcome": "unavailable"})
        if self._enabled is not None and not await asyncio.to_thread(self._enabled, context):
            return _done("blocked", DISABLED_TEXT, {"outcome": "workspace_disabled"})
        if args.get("session_id"):
            return await self._resume(args, context)
        return await self._start(args, context)

    async def unavailable_reason(self, context: TenantContext, *, probe_seconds: float = 3.0) -> str | None:
        """The sentence naming the switch that is off, or None when ``web.act`` can run here.

        Checked by the planner before a task asks for the scope approval, so a request is answered
        with this sentence at once instead of after an approval for nothing. The browser service's
        action mode is asked from its ``/healthz`` (a short probe; an unreadable answer is not a
        reason: the run says what failed).
        """
        if self.problem is not None or self.client is None:
            return self.problem or UNAVAILABLE_SERVICE
        if self._enabled is not None and not await asyncio.to_thread(self._enabled, context):
            return DISABLED_TEXT
        try:
            async with asyncio.timeout(probe_seconds):
                health = await self.client.health()
        except (httpx.HTTPError, TimeoutError, ValueError):
            return None
        if health.get("http_status") == 200 and health.get("action_mode") is False:
            return UNAVAILABLE_MODE
        return None

    # ------------------------------------------------------------------ the scope approval

    async def _start(self, args: dict[str, Any], context: TenantContext) -> ToolResult:
        goal = " ".join(str(args.get("goal") or "").split())[:1000]
        if not goal:
            return _done("blocked", "The task needs a goal in the owner's own words.", {"outcome": "refused_goal"})
        try:
            start_url = check_start_url(str(args.get("start_url") or ""))
        except ValueError as exc:
            return _done("blocked", str(exc), {"outcome": "refused_url"})
        raw_sites = args.get("allowed_domains") if isinstance(args.get("allowed_domains"), list) else []
        sites = [site for site in (normalize_site(str(item)) for item in raw_sites[:10]) if site]
        start_host = urlsplit(start_url).hostname or ""
        if not sites or not host_in_sites(start_host, sites):
            return _done("blocked", "The start address must be on one of the approved sites.", {"outcome": "refused_scope"})
        logins_raw = args.get("logins") if isinstance(args.get("logins"), dict) else {}
        logins = {str(k): str(v) for k, v in list(logins_raw.items())[:10] if valid_handle_name(str(k)) and normalize_site(str(v))}
        cards = [str(item) for item in (args.get("cards") if isinstance(args.get("cards"), list) else [])[:5] if valid_handle_name(str(item))]
        if any(not host_in_sites(site, sites) for site in logins.values()):
            return _done("blocked", "A saved login in the scope is for a site outside the approved list.", {"outcome": "refused_scope"})
        audit = {"sites": sites, "logins": sorted(logins), "cards": sorted(cards)}
        current = _current_task()
        if current is None:
            # Only a task's run may drive a browser: it carries the approvals, the step log and Stop.
            return _done("blocked", "The action browser runs only as a task, never from a direct tool call.", {**audit, "outcome": "no_task"})

        now = utc_now()

        def daily_count() -> int:
            with open_action_store(context) as store:
                return store.count_since(context, _today(now))

        if await asyncio.to_thread(daily_count) >= self.tasks_per_day:
            return _done("failed", f"This workspace has started {self.tasks_per_day} action browser tasks today (UTC), the most allowed; try again tomorrow.",
                         {**audit, "outcome": "daily_cap"})
        try:
            created = await self.client.create(sites)
        except ServiceAnswer as exc:
            if exc.status == 404:
                return _done("failed", UNAVAILABLE_MODE, {**audit, "outcome": "unavailable"})
            if exc.status == 429:
                return _done("failed", "The browser is busy with another task; try again when it ends.", {**audit, "outcome": "busy"})
            if exc.status == 401:
                return _done("failed", "Not available on this server: the browser service refused ANUM's token (ANUM_BROWSER_RESEARCH_TOKEN).",
                             {**audit, "outcome": "service_unauthorised"})
            return _done("failed", str(exc.body.get("message") or "The browser could not start.")[:300], {**audit, "outcome": "browser_error"})
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            return _done("failed", f"Not available right now: the browser service could not be reached ({type(exc).__name__}).", {**audit, "outcome": "service_unreachable"})
        browser_id = str(created.get("session_id") or "")
        record = SessionRecord(
            id=new_id("abs"), task_id=current[0], run_id=current[1], created_by=context.user_id,
            goal=goal, start_url=start_url, sites=sites, logins=logins, cards=cards, browser_id=browser_id, created_at=now, updated_at=now,
        )

        def create() -> None:
            with open_action_store(context) as store:
                store.create_session(context, record)
                store.add_step(context, StepRecord(session_id=record.id, seq=0, kind="started", summary=f"Started: {goal}"[:300], url=start_url,
                                                   detail={"sites": sites}, created_at=now))

        try:
            await asyncio.to_thread(create)
        except Exception:
            await self.client.close(browser_id)
            raise
        segment = _Segment(self, context, record, self._clock())
        return await segment.run(first=PlannedStep(action="navigate", url=start_url, reason=f"Open {start_host}"))

    # ------------------------------------------------------------------ continuations

    async def _resume(self, args: dict[str, Any], context: TenantContext) -> ToolResult:
        session_id = str(args.get("session_id") or "")
        record = await asyncio.to_thread(_load, context, session_id)
        if record is None:
            return _done("blocked", "That browser session is not in this workspace.", {"outcome": "unknown_session"})
        if record.ended:
            return _done("failed", f"The browser session already ended ({record.outcome or record.status}).", {"session_id": record.id, "outcome": "session_ended"})
        segment = _Segment(self, context, record, self._clock())
        resume = str(args.get("resume") or "")
        pending = record.pending or {}
        if resume != pending.get("resume") or args.get("pending_id") != pending.get("id"):
            return await segment.end("failed", "The approved step is not the one this browser session is waiting for.", "pending_mismatch")
        await segment.note("approved", {"step": "Approved by the owner", "handoff": "The owner finished the check; continuing",
                                         "off_site": "The owner allowed another site", "continue": "The owner said keep going"}.get(resume, "Continuing"),
                           detail={"resume": resume})
        record.waiting = None
        record.pending = None
        record.status = "running"
        if resume == "step":
            return await segment.run(approved=args)
        if resume == "off_site":
            site = normalize_site(str(args.get("site") or ""))
            if site and site not in record.sites:
                record.sites = [*record.sites, site]
                try:
                    await self.client.add_sites(record.browser_id or "", [site])
                except ServiceAnswer as exc:
                    if exc.status == 404:
                        return await segment.end("failed", SESSION_GONE, "session_gone")
                    raise
            url = str(args.get("url") or "")
            return await segment.run(first=PlannedStep(action="navigate", url=url, reason=f"Open {site}") if url.startswith("https://") else None)
        return await segment.run()


def _current_task() -> tuple[str, str] | None:
    """The task and run this call belongs to (``agent_tools.CURRENT_RUN``, set by the runtime)."""
    from ..agent_tools import CURRENT_RUN

    return CURRENT_RUN.get()


def _load(context: TenantContext, session_id: str) -> SessionRecord | None:
    with open_action_store(context) as store:
        return store.get_session(context, session_id)


def _done(status: str, summary: str, audit: dict[str, Any], output: dict[str, Any] | None = None) -> ToolResult:
    return ToolResult(status=status, summary=summary[:1500], output=output or {}, audit_metadata={"tool": ACT_TOOL, **audit})


class _Segment:
    """The work between two pauses of one session."""

    def __init__(self, tool: ActionBrowserTool, context: TenantContext, record: SessionRecord, started: float) -> None:
        self.tool = tool
        self.client: ActionServiceClient = tool.client  # type: ignore[assignment]
        self.context = context
        self.record = record
        self.started = started
        self.history: list[str] = []
        self.refusals = 0

    # -------------------------------------------------------------- bookkeeping

    def _elapsed(self) -> float:
        return max(0.0, self.tool._clock() - self.started)

    async def note(self, kind: str, summary: str, *, url: str | None = None, detail: dict[str, Any] | None = None) -> None:
        step = StepRecord(session_id=self.record.id, seq=0, kind=kind, summary=summary[:300], url=(url or None) and url[:2048],
                          detail=detail or {}, created_at=utc_now())
        self.history.append(summary[:200])

        def write() -> None:
            with open_action_store(self.context) as store:
                store.add_step(self.context, step)

        await asyncio.to_thread(write)

    async def save(self) -> SessionRecord:
        record = self.record
        record.updated_at = utc_now()

        def write() -> SessionRecord:
            with open_action_store(self.context) as store:
                stored = store.get_session(self.context, record.id)
                if stored is not None and stored.status == "stopped":
                    # A stop wins over anything this segment wanted to write.
                    return stored
                store.save_session(self.context, record)
                return record

        saved = await asyncio.to_thread(write)
        if saved.status == "stopped":
            self.record = saved
        return saved

    async def _stopped(self) -> bool:
        stored = await asyncio.to_thread(_load, self.context, self.record.id)
        return stored is None or stored.status == "stopped"

    def _audit(self, outcome: str, **extra: Any) -> dict[str, Any]:
        return {"session_id": self.record.id, "outcome": outcome, "steps": self.record.step_count, "sites": self.record.sites,
                "host": site_of(urlsplit(self.record.current_url or self.record.start_url).hostname or ""), **extra}

    async def end(self, status: str, message: str, outcome: str, *, final: str | None = None) -> ToolResult:
        """Close the browser and settle the session (completed, failed or stopped)."""
        if self.record.browser_id:
            await self.client.close(self.record.browser_id)
        self.record.work_seconds += self._elapsed()
        self.started = self.tool._clock()
        stopped = await self._stopped()
        self.record.status = "stopped" if stopped else ("completed" if status == "succeeded" else "failed")
        self.record.outcome = "stopped" if stopped else outcome
        self.record.message = message[:500]
        self.record.waiting = None
        self.record.pending = None
        self.record.browser_id = None
        self.record.ended_at = utc_now()
        if not stopped:
            await self.note("done" if status == "succeeded" else "failed", message, url=self.record.current_url)
            await self.save()
        if stopped:
            return _done("failed", "Stopped by the owner.", self._audit("stopped"))
        output = {"session_id": self.record.id, "final_url": self.record.current_url, "steps": self.record.step_count,
                  **{key: self.record.result[key] for key in ("fields", "columns", "rows", "files") if self.record.result.get(key)}}
        if final is not None:
            output["answer"] = final
        return _done(status, message, self._audit(outcome), output)

    async def pause(self, waiting: str, reason: str, pending: dict[str, Any], args: dict[str, Any], *, detail: dict[str, Any] | None = None) -> ToolResult:
        """Wait for the owner: the continuation the runtime turns into an approval."""
        pending_id = new_id("abp")
        self.record.work_seconds += self._elapsed()
        self.started = self.tool._clock()
        self.record.status = "waiting"
        self.record.waiting = waiting
        self.record.pending = {**pending, "id": pending_id, "resume": args["resume"], "reason": reason}
        await self.note({"approval": "approval", "handoff": "needs_you", "off_site": "off_site", "continue": "continue"}[waiting], reason,
                        url=self.record.current_url, detail={**(detail or {}), "waiting": waiting})
        await self.save()
        if self.record.status == "stopped":
            return await self.end("failed", "Stopped by the owner.", "stopped")
        continuation = ToolCall(name=ACT_TOOL, arguments={**args, "session_id": self.record.id, "pending_id": pending_id})
        return ToolResult(
            status="paused", summary=reason, continuation=continuation, continuation_reason=reason,
            continuation_ttl_seconds=self.tool.approval_seconds,
            output={"session_id": self.record.id, "waiting": waiting},
            audit_metadata={"tool": ACT_TOOL, **self._audit(f"paused_{waiting}")},
        )

    # -------------------------------------------------------------- the loop

    async def run(self, *, first: PlannedStep | None = None, approved: dict[str, Any] | None = None) -> ToolResult:
        record = self.record
        await self.save()
        recent = await asyncio.to_thread(_recent_steps, self.context, record.id)
        self.history = recent + self.history
        browser_id = record.browser_id or ""
        try:
            if approved is not None:
                result = await self._approved_step(approved)
                if result is not None:
                    return result
            planned = first
            while True:
                if await self._stopped():
                    return await self.end("failed", "Stopped by the owner.", "stopped")
                if record.step_count >= self.tool.max_steps:
                    return await self.end("failed", f"Stopped after {self.tool.max_steps} steps, the most one task may take. The goal was not reached.", "step_cap")
                if record.work_seconds + self._elapsed() >= self.tool.task_seconds:
                    minutes = int(self.tool.task_seconds // 60)
                    return await self.end("failed", f"Stopped after {minutes} minutes of work, the most one task may take. The goal was not reached.", "time_cap")
                if self._elapsed() >= self.tool.segment_seconds:
                    reason = f"Still working on {site_of(urlsplit(record.current_url or record.start_url).hostname or '')}: approve to keep going."
                    return await self.pause("continue", reason, {"kind": "continue"}, {"resume": "continue"})
                if planned is None:
                    snapshot = await self.client.snapshot(browser_id)
                    self._observe(snapshot)
                    for item in (snapshot.get("downloads") or [])[:10]:
                        if isinstance(item, dict):
                            await self._keep_download(item)
                    waiting = await self._page_needs_owner(snapshot)
                    if waiting is not None:
                        return waiting
                    planner = self.tool.planner_factory(self.context)
                    scope = {"sites": record.sites, "logins": record.logins, "cards": record.cards}
                    planned = await planner.next_step(goal=record.goal, scope=scope, history=list(self.history), snapshot=snapshot)
                step, planned = planned, None
                outcome = await self._one(step)
                if outcome is not None:
                    return outcome
        except ServiceAnswer as exc:
            if exc.status == 404:
                return await self.end("failed", SESSION_GONE, "session_gone")
            return await self.end("failed", str(exc.body.get("message") or "The browser service failed.")[:300], "browser_error")
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            return await self.end("failed", f"The browser service stopped answering ({type(exc).__name__}).", "service_unreachable")

    def _observe(self, snapshot: dict[str, Any]) -> None:
        self.record.current_url = str(snapshot.get("url") or self.record.current_url or "")[:2048] or None
        self.record.current_title = str(snapshot.get("title") or "")[:300] or None

    async def _page_needs_owner(self, snapshot: dict[str, Any]) -> ToolResult | None:
        held = snapshot.get("held")
        if held:
            host = urlsplit(str(held)).hostname or ""
            site = site_of(host)
            return await self.pause(
                "off_site", f"Waiting for your approval: the page wants to open {site}, a site outside the approved list.",
                {"kind": "off_site", "site": site, "url": str(held)[:2048]}, {"resume": "off_site", "site": site, "url": str(held)[:2048]},
                detail={"host": host},
            )
        needs = handoff_reason(snapshot)
        if needs is not None:
            return await self._handoff(needs)
        return None

    async def _handoff(self, needs: Decision) -> ToolResult:
        reason = f"{needs.reason} Open the live view, complete it, then press Continue."
        return await self.pause("handoff", reason, {"kind": "handoff", "code": needs.code, "host": needs.host},
                                {"resume": "handoff", "check": needs.code, "site": site_of(needs.host)}, detail={"code": needs.code, "host": needs.host})

    async def _one(self, step: PlannedStep) -> ToolResult | None:
        """One planned step: validate, classify, then act or pause. None continues the loop."""
        record = self.record
        if step.action == "done":
            answer = step.answer or f"Done on {record.current_url or record.start_url}."
            if step.save_as == "csv" and record.result.get("rows"):
                await self._save_table()
            record.result["answer"] = answer[:1500]
            return await self.end("succeeded", result_summary(answer, record.result), "succeeded", final=answer)
        if step.action == "fail":
            return await self.end("failed", step.answer or "The goal could not be reached.", "planner_failed")
        if step.action == "extract":
            added = merge_result(record.result, step.data or {})
            record.step_count += 1
            if not added:
                self.refusals += 1
                await self.note("refused", "The model sent nothing to note; reading the page again.")
                return await self._too_many_refusals()
            await self.note("extracted", f"Noted {added} from {site_of(urlsplit(record.current_url or '').hostname or '') or 'the page'}",
                            url=record.current_url, detail={"count": added})
            await self.save()
            return None
        action = _action_of(step)
        if action is None:
            self.refusals += 1
            await self.note("refused", "The model proposed a step without an element or address; reading the page again.")
            return await self._too_many_refusals()
        described = await self._inspect(action)
        if described is None:
            return await self._too_many_refusals()
        decision = classify(described, sites=record.sites, scope_logins=record.logins, scope_cards=set(record.cards), card_grants=set(record.grants),
                            signed_in=signed_in_sites(record.grants))
        if decision.kind == REFUSE:
            self.refusals += 1
            await self.note("refused", f"Refused: {decision.reason}", url=record.current_url, detail={"code": decision.code})
            return await self._too_many_refusals()
        if decision.kind == HANDOFF:
            return await self._handoff(decision)
        if decision.kind == GATE:
            return await self._gate(action, described, decision, step)
        return await self._act(action, described, decision, step)

    async def _too_many_refusals(self) -> ToolResult | None:
        self.record.step_count += 1
        if self.refusals >= 5:
            return await self.end("failed", "Stopped: the model kept proposing steps the action browser refuses.", "refusals")
        return None

    async def _inspect(self, action: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return await self.client.inspect(self.record.browser_id or "", action)
        except ServiceAnswer as exc:
            if exc.status == 409:
                self.refusals += 1
                await self.note("refused", str(exc.body.get("message") or "That element is not on the page any more.")[:300], url=self.record.current_url)
                return None
            raise

    async def _gate(self, action: dict[str, Any], described: dict[str, Any], decision: Decision, step: PlannedStep) -> ToolResult:
        page = described.get("page") or {}
        target = described.get("target") or {}
        from .vault import hints_for

        hints = await asyncio.to_thread(hints_for, self.context)
        shown_action = dict(action)
        handle = parse_handle(str(action.get("value") or "")) if action.get("type") == "type" else None
        if handle is not None:
            shown_action["value"] = masked_handle(handle, hints)
        label = _target_label(target, action)
        fields = _fields_shown(target, hints)
        amount = decision.amount.as_dict() if decision.amount is not None else {}
        verb = {"card_entry": "enter", "card_security_code": "enter the card security code in", "payment_field": "type into", "off_site": "open", "binding_button": "press",
                "payment_form": "send", "form_submit": "send", "payment_frame": "press"}.get(decision.code, "do")
        what = f"{verb} {label}" if decision.code != "off_site" else f"open {site_of(decision.host)}"
        money = f" ({amount['amount_text']})" if amount.get("amount_text") and decision.code != "off_site" else ""
        host = str(page.get("host") or decision.host)
        reason = (f"Waiting for your approval: open {site_of(decision.host)}, a site outside the approved list." if decision.code == "off_site"
                  else f"Waiting for your approval: {what}{money} on {site_of(host)}.")
        args: dict[str, Any] = {
            "resume": "step" if decision.code != "off_site" else "off_site",
            "kind": decision.code,
            "site": site_of(host) if decision.code != "off_site" else site_of(decision.host),
            "url": str(page.get("url") or "")[:2048],
            "action": action,
            "shown_action": shown_action,
            "target": label,
            "fields": fields,
            "fingerprint": str(described.get("fingerprint") or ""),
            **amount,
        }
        if decision.code == "off_site":
            args["url"] = str(action.get("url") or target.get("href") or "")[:2048]
        if decision.extra.get("grant"):
            args["grant"] = decision.extra["grant"]
        if decision.code == "card_security_code":
            # Only this fixed marker is in the call and its payload hash; the digits come with the approve request.
            args["cvc"] = CVC_MARKER
            reason += " Type the card's security code on the approval card."
        pending = {"kind": decision.code, "label": label, "host": host, "url": args["url"], **amount}
        waiting = "off_site" if decision.code == "off_site" else "approval"
        return await self.pause(waiting, reason, pending, args, detail={"code": decision.code, "label": label, "host": host, **amount})

    async def _act(self, action: dict[str, Any], described: dict[str, Any], decision: Decision, step: PlannedStep) -> ToolResult | None:
        page = described.get("page") or {}
        expect = {"url": page.get("url"), "fingerprint": described.get("fingerprint")}
        secret = None
        if decision.handle is not None:
            secret = await self._secret(decision.handle)
            if secret is None:
                self.refusals += 1
                await self.note("refused", f"The saved {decision.handle.kind} {decision.handle.name} is not saved any more.")
                return await self._too_many_refusals()
        try:
            result = await self.client.act(self.record.browser_id or "", action, expect, secret)
        except ServiceAnswer as exc:
            if exc.status == 409:
                self.refusals += 1
                await self.note("error", str(exc.body.get("message") or "The browser could not do that.")[:300], url=self.record.current_url)
                return await self._too_many_refusals()
            raise
        finally:
            secret = None
        return await self._after(action, result, described, step, decision)

    async def _secret(self, handle: Handle) -> str | None:
        from .vault import resolve_secret

        # Decrypted in its own short unit of work in a worker thread, handed to the browser in one request.
        return await asyncio.to_thread(resolve_secret, self.context, handle)

    async def _after(self, action: dict[str, Any], result: dict[str, Any], described: dict[str, Any], step: PlannedStep, decision: Decision) -> ToolResult | None:
        outcome = str(result.get("outcome") or "")
        if outcome == "page_changed":
            await self.note("page_changed", "The page changed before the step; reading it again.", url=self.record.current_url)
            self.record.step_count += 1
            return None
        snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else {}
        self.record.step_count += 1
        typed = parse_handle(str(action.get("value") or "")) if action.get("type") == "type" else None
        if typed is not None and typed.kind == "login" and typed.part == "password" and self.record.logins.get(typed.name):
            # From here the session acts as the owner on that site: sending a form there stops
            # for approval (classify). Kept first so the cap never drops it.
            mark = signed_in_grant(str(self.record.logins[typed.name]))
            self.record.grants = list(dict.fromkeys([mark, *self.record.grants]))[:20]
        self._observe(snapshot)
        await self.note(_log_kind(action), _log_line(action, described, step), url=self.record.current_url,
                        detail=_log_detail(action, described))
        for item in (snapshot.get("downloads") or [])[:10]:
            if isinstance(item, dict):
                await self._keep_download(item)
        await self.save()
        if outcome == "held_navigation" or snapshot.get("held"):
            return await self._page_needs_owner({**snapshot, "held": result.get("held") or snapshot.get("held")})
        return None

    async def _keep_download(self, item: dict[str, Any]) -> None:
        """A file the page downloaded: checked again and stored in the workspace's Files (never opened)."""
        from pathlib import PurePosixPath

        name = str(item.get("name") or "download")[:120]
        if PurePosixPath(name.lower()).suffix not in DOWNLOAD_EXTENSIONS:
            await self.note("refused", f"Did not keep {name}: only PDF, CSV, Excel, Word, PNG, JPEG, text and JSON files are saved.")
            return
        fetched = await self.client.download(self.record.browser_id or "", str(item.get("id") or ""))
        if fetched is None:
            await self.note("error", f"Could not fetch {name} from the browser.")
            return
        stored = await store_in_files(self.context, name, fetched[1])
        if isinstance(stored, str):
            await self.note("refused", f"Did not keep {name}: {stored}"[:300])
            return
        files = list(self.record.result.get("files") or [])
        files.append({"id": stored["id"], "name": stored["name"], "size": stored["size"]})
        self.record.result["files"] = files[:20]
        await self.note("downloaded", f"Saved {stored['name']} to Files", url=self.record.current_url, detail={"file_id": stored["id"], "name": stored["name"]})

    async def _save_table(self) -> None:
        content = table_csv(self.record.result)
        slug = "".join(ch if ch.isalnum() else "-" for ch in self.record.goal.lower()[:40]).strip("-") or "results"
        stored = await store_in_files(self.context, f"browser-{slug}-{utc_now().strftime('%Y%m%d-%H%M')}.csv", content.encode("utf-8"))
        if isinstance(stored, str):
            await self.note("refused", f"Did not save the table: {stored}"[:300])
            return
        files = list(self.record.result.get("files") or [])
        files.append({"id": stored["id"], "name": stored["name"], "size": stored["size"]})
        self.record.result["files"] = files[:20]
        await self.note("downloaded", f"Saved the table as {stored['name']} in Files", detail={"file_id": stored["id"], "name": stored["name"]})

    async def _approved_step(self, approved: dict[str, Any]) -> ToolResult | None:
        """Run the step the owner approved, only if the page is still the one they saw; otherwise ask again."""
        action = approved.get("action") if isinstance(approved.get("action"), dict) else {}
        expect = {"url": approved.get("url"), "fingerprint": approved.get("fingerprint")}
        handle = parse_handle(str(action.get("value") or "")) if action.get("type") == "type" else None
        secret = None
        if handle is not None:
            if (handle.kind == "card" and handle.name not in self.record.cards) or (handle.kind == "login" and handle.name not in self.record.logins):
                return await self.end("failed", "The approved step names a saved secret outside this task's scope.", "handle_out_of_scope")
            if handle.kind == "card" and handle.part == CODE_PART:
                # Typed by the owner on this approval, taken once; never read from the vault, never stored.
                secret = take_card_code() if approved.get("cvc") == CVC_MARKER else None
                if secret is None:
                    return await self.end("failed", NO_CODE_AT_RUN, "card_code_missing")
            else:
                secret = await self._secret(handle)
            if secret is None:
                return await self.end("failed", f"The saved {handle.kind} {handle.name} is not saved any more.", "secret_missing")
        try:
            result = await self.client.act(self.record.browser_id or "", action, expect, secret)
        except ServiceAnswer as exc:
            if exc.status == 409:
                await self.note("error", str(exc.body.get("message") or "The browser could not do that.")[:300], url=self.record.current_url)
                self.record.step_count += 1
                return None
            raise
        finally:
            secret = None
        if result.get("outcome") == "page_changed":
            fresh = result.get("inspect") if isinstance(result.get("inspect"), dict) else None
            await self.note("page_changed", "The page changed after you approved; nothing was done. Asking again.", url=self.record.current_url)
            if fresh is None:
                return None
            decision = classify(fresh, sites=self.record.sites, scope_logins=self.record.logins, scope_cards=set(self.record.cards),
                                card_grants=set(self.record.grants), signed_in=signed_in_sites(self.record.grants))
            if decision.kind == GATE:
                return await self._gate(action, fresh, decision, PlannedStep(action=str(action.get("type")), reason="Asked again"))  # type: ignore[arg-type]
            if decision.kind == HANDOFF:
                return await self._handoff(decision)
            return None
        if approved.get("grant") and approved.get("kind") in {"card_entry", "card_security_code"}:
            self.record.grants = list(dict.fromkeys([*self.record.grants, str(approved["grant"])]))[:20]
        described = {"page": {"url": approved.get("url")}, "target": {"label": approved.get("target")}}
        return await self._after(action, result, described, PlannedStep(action="read", reason=str(approved.get("target") or "")), Decision(ALLOW, "approved", ""))


def merge_result(result: dict[str, Any], data: dict[str, Any]) -> int:
    """Add extracted fields and table rows to the task's result, capped. Returns how many were added."""
    added = 0
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    kept = dict(result.get("fields") or {})
    for key, value in list(fields.items())[:MAX_FIELDS]:
        if len(kept) >= MAX_FIELDS and str(key) not in kept:
            break
        kept[" ".join(str(key).split())[:80]] = " ".join(str(value).split())[:MAX_CELL]
        added += 1
    if kept:
        result["fields"] = kept
    columns = data.get("columns") if isinstance(data.get("columns"), list) else None
    rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    if columns and not result.get("columns"):
        result["columns"] = [" ".join(str(c).split())[:80] for c in columns[:MAX_COLUMNS]]
    width = len(result.get("columns") or []) or MAX_COLUMNS
    table = list(result.get("rows") or [])
    for row in rows:
        if len(table) >= MAX_ROWS:
            break
        if isinstance(row, list):
            table.append([" ".join(str(cell).split())[:MAX_CELL] for cell in row[:width]])
            added += 1
    if table:
        result["rows"] = table
    return added


def table_csv(result: dict[str, Any]) -> str:
    import csv
    import io

    def cell(value: str) -> str:
        # No formula injection when the file is opened in a spreadsheet.
        return f"'{value}" if value[:1] in {"=", "+", "-", "@"} else value

    out = io.StringIO()
    writer = csv.writer(out)
    if result.get("columns"):
        writer.writerow([cell(c) for c in result["columns"]])
    for row in result.get("rows") or []:
        writer.writerow([cell(str(c)) for c in row])
    return out.getvalue()


def result_summary(answer: str, result: dict[str, Any]) -> str:
    """The answer with what was collected, as plain text and a small Markdown table."""
    parts = [answer.strip()]
    if result.get("fields"):
        parts.append("\n".join(f"- {k}: {v}" for k, v in list(result["fields"].items())[:20]))
    if result.get("rows"):
        columns = result.get("columns") or [f"Column {i + 1}" for i in range(len(result["rows"][0]))]
        lines = ["| " + " | ".join(c.replace("|", "/") for c in columns) + " |", "|" + "---|" * len(columns)]
        for row in result["rows"][:20]:
            lines.append("| " + " | ".join(str(c).replace("|", "/") for c in row) + " |")
        if len(result["rows"]) > 20:
            lines.append(f"({len(result['rows']) - 20} more rows in the saved table)")
        parts.append("\n".join(lines))
    if result.get("files"):
        parts.append("Saved to Files: " + ", ".join(f["name"] for f in result["files"]))
    return "\n\n".join(part for part in parts if part)[:6000]


async def store_in_files(context: TenantContext, name: str, content: bytes) -> dict[str, Any] | str:
    """Store bytes in the workspace's Files with the upload checks (type signature, malware scan,
    quota, tenant-scoped key). Returns the file, or the reason it was not stored."""
    from fastapi import HTTPException

    from .. import file_policy
    from ..files import check_upload_content, save_generated_file

    if len(content) > MAX_DOWNLOAD_BYTES:
        return "it is larger than 20 MB."
    try:
        allowed = file_policy.resolve_content_type(name, None)
        await asyncio.to_thread(check_upload_content, name, allowed, content)
        saved = await save_generated_file(context, name, content)
    except file_policy.UnsupportedFileType as exc:
        return str(exc)[:200]
    except HTTPException as exc:
        return str(exc.detail)[:200]
    return {"id": saved.id, "name": saved.name, "size": saved.size_bytes}


def _recent_steps(context: TenantContext, session_id: str) -> list[str]:
    with open_action_store(context) as store:
        rows = store.steps(context, session_id, limit=500)
    return [row.summary for row in rows[-15:]]


def _action_of(step: PlannedStep) -> dict[str, Any] | None:
    kind = step.action
    if kind == "navigate":
        return {"type": "navigate", "url": step.url} if step.url else None
    if kind in {"click", "type", "select"}:
        if not step.ref:
            return None
        action: dict[str, Any] = {"type": kind, "ref": step.ref}
        if kind in {"type", "select"}:
            action["value"] = step.value or ""
        if kind == "type" and step.submit:
            action["submit"] = True
        return action
    if kind == "scroll":
        return {"type": "scroll", "value": "up" if (step.value or "").lower() == "up" else "down"}
    if kind == "wait":
        return {"type": "wait", "seconds": 2}
    if kind == "read":
        return {"type": "read"}
    return None  # extract, done and fail are handled before


def _target_label(target: dict[str, Any], action: dict[str, Any]) -> str:
    text = " ".join(str(target.get(key) or "") for key in ("text", "label", "aria", "name") if target.get(key)).strip()
    if action.get("type") == "navigate":
        return str(action.get("url") or "")[:200]
    label = " ".join(text.split())[:120]
    return f"“{label}”" if label else str(target.get("tag") or "the element")


def _fields_shown(target: dict[str, Any], hints: dict[str, str]) -> list[dict[str, str]]:
    form = target.get("form") if isinstance(target.get("form"), dict) else {}
    shown = []
    for item in (form.get("fields") or [])[:20]:
        if not isinstance(item, dict):
            continue
        shown.append({"label": str(item.get("label") or item.get("name") or item.get("type") or "")[:80], "type": str(item.get("type") or "")[:20]})
    return shown


def _log_kind(action: dict[str, Any]) -> str:
    return {"navigate": "opened", "click": "clicked", "type": "typed", "select": "selected", "scroll": "scrolled", "wait": "waited", "read": "read"}.get(
        str(action.get("type") or ""), "step")


def _log_line(action: dict[str, Any], described: dict[str, Any], step: PlannedStep) -> str:
    kind = str(action.get("type") or "")
    target = described.get("target") or {}
    label = _target_label(target, action)
    if kind == "navigate":
        return f"Opened {urlsplit(str(action.get('url') or '')).hostname or 'the page'}"
    if kind == "type":
        handle = parse_handle(str(action.get("value") or ""))
        what = f"the saved {handle.kind} {handle.name} ({handle.part.replace('_', ' ')})" if handle else "text"
        return f"Typed {what} into {label}"
    if kind == "click":
        return f"Clicked {label}"
    if kind == "select":
        return f"Chose {str(action.get('value') or '')[:60]} in {label}"
    if kind == "scroll":
        return "Scrolled the page"
    if kind == "wait":
        return "Waited for the page"
    return step.reason or "Read the page"


def _log_detail(action: dict[str, Any], described: dict[str, Any]) -> dict[str, Any]:
    target = described.get("target") or {}
    detail: dict[str, Any] = {"label": _target_label(target, action)[:120]}
    if action.get("type") == "navigate":
        detail["host"] = urlsplit(str(action.get("url") or "")).hostname or ""
    handle = parse_handle(str(action.get("value") or "")) if action.get("type") == "type" else None
    if handle is not None:
        detail["handle"] = handle.text
    return detail


def act_definition() -> ToolDefinition:
    return ToolDefinition(
        name=ACT_TOOL,
        description=(
            "Do a browser task for the owner in a sandboxed browser: sign in with a saved login, fill and submit forms, download "
            "files into Files, collect data from pages into a table, check statuses, update settings, buy or book. Stops for the "
            "owner's approval before paying, buying, booking, sending, submitting, signing, posting or deleting, and hands "
            "captchas and codes to the owner. Arguments: goal, start_url, allowed_domains, logins, cards."
        ),
        risk_level=RiskLevel.HIGH,
        required_roles=frozenset({"owner"}),
        timeout_seconds=300,
        idempotent=False,
        category="general",
        action_kind="other",
        always_ask=True,
        long_running=True,
        input_schema={
            "type": "object",
            "properties": {
                "goal": {"type": "string"}, "start_url": {"type": "string"},
                "allowed_domains": {"type": "array", "items": {"type": "string"}},
                "logins": {"type": "object"}, "cards": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["goal", "start_url", "allowed_domains"],
        },
    )


def configured_act_handler(settings: Any, enabled: EnabledCheck | None, planner_factory: PlannerFactory, client: ActionServiceClient | None = None) -> ActionBrowserTool:
    from ..browser_research.tool import normalize_service_url, service_problem

    problem = service_problem(settings.browser_research_url, settings.browser_research_token)
    if problem is not None:
        problem = UNAVAILABLE_SERVICE
    elif not settings.action_browser_enabled:
        problem = UNAVAILABLE_SWITCH
    if client is None and problem is None:
        client = ActionServiceClient(normalize_service_url(settings.browser_research_url), settings.browser_research_token.strip())
    return ActionBrowserTool(
        problem=problem, client=client, planner_factory=planner_factory, enabled=enabled,
        max_steps=settings.action_browser_max_steps, task_seconds=settings.action_browser_task_minutes * 60.0,
        segment_seconds=float(settings.action_browser_segment_seconds), tasks_per_day=settings.action_browser_tasks_per_day,
        approval_seconds=settings.action_browser_approval_seconds,
    )


def register_action_tools(registry: ToolRegistry, settings: Any, enabled: EnabledCheck | None, planner_factory: PlannerFactory) -> ToolRegistry:
    """Add ``web.act`` to a runtime's registry (the API's and the Temporal worker's).

    Always registered, so a request for it gets an honest "Not available on this server"
    instead of a different tool when the operator has not set it up."""
    if ACT_TOOL not in registry.names:
        registry.register(act_definition(), configured_act_handler(settings, enabled, planner_factory))
    return registry


def default_planner_factory(gateway: Any) -> PlannerFactory:
    """The workspace's budgeted model gateway (per-tenant budgets apply to every step)."""
    from .planner import ModelActionPlanner

    def factory(context: TenantContext) -> ActionPlanner:
        from ..current_time import time_line_for
        from ..onboarding import budgeted_model_gateway

        return ModelActionPlanner(
            lambda: budgeted_model_gateway(context, gateway), lambda language: time_line_for(context, language)
        )

    return factory


__all__ = [
    "ActionBrowserTool", "ActionServiceClient", "ServiceAnswer", "act_definition", "check_start_url", "configured_act_handler",
    "default_planner_factory", "register_action_tools",
]

