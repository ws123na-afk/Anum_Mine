"""Developer mode: the owner asks for a change and a coding agent builds it (docs/developer-mode.md).

Flow: an owner creates a request (typed or spoken). The API dispatches it to the
``anum-developer.yml`` GitHub Actions workflow, which runs the coding agent on a branch
``anum-dev/<request id>``, runs the repository's checks and opens a draft pull request.
The API never runs code; it only reads progress back from GitHub (workflow run, pull
request, CI) and shows it. Merging is a high-risk tool call
(``developer.merge_pull_request``) that goes through ANUM's approvals: an owner asks for
the merge, the runtime pauses for approval bound to the pull request's head commit, and
only after ``POST /api/v1/approvals/{id}/approve`` with the displayed payload hash does
the tool squash-merge, and only if every check on that commit passed.

Configuration is deployment-level (``ANUM_DEVELOPER_*``): one repository, one
fine-grained token for it, and the one tenant (optionally one workspace) allowed to use
it. Requests are stored per tenant and workspace under forced RLS (migration
``0022_developer_requests``); the in-memory store serves ``ANUM_REPOSITORY_BACKEND=memory``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any, Literal, Protocol

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import _unit_of_work, repository_context, require_permission, tenant_context
from .developer_github import ChecksState, GitHubClient, GitHubError, PullRequest, WorkflowRun
from .events import CanonicalEventName, create_event
from .model_budget import check_model_budget, record_model_usage
from .model_gateway import MockModelGateway, ModelUsage
from .repository import AnumRepository
from .runtime import AgentRuntime
from .schemas import (
    AgentRunStep,
    Approval,
    ApprovalStatus,
    RiskLevel,
    RunPhase,
    Task,
    TaskStatus,
    TenantContext,
    new_id,
    utc_now,
)
from .scoped_store import open_scoped_store

logger = logging.getLogger("anum.developer")

MERGE_TOOL = "developer.merge_pull_request"
BRANCH_PREFIX = "anum-dev/"
MIN_TEXT = 10
MAX_TEXT = 4000
MAX_LISTED = 100
# A dispatched request whose workflow run never appears is reported as failed after this.
RUN_START_GRACE = timedelta(minutes=30)
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
WORKFLOW_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}\.ya?ml$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
# Requests that ask to change CI workflows or secret handling are refused unless the
# owner allows it explicitly here *and* the repository variable allows it in the workflow.
# The workflow repeats this check and, after the agent ran, refuses protected paths.
PROTECTED_REQUEST = re.compile(
    r"\.github|github[ -]actions?|\bci[ -]workflows?\b|workflow[ -]files?|\bsecrets?\b|credentials?"
    r"|passwords?|api[ _-]?keys?|access[ _-]?tokens?|personal access token|gitleaks|claude\.md",
    re.IGNORECASE,
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class DeveloperSettings(BaseSettings):
    """``ANUM_DEVELOPER_*``. Nothing here is required: missing values show the setup state."""

    github_repo: str = ""
    github_token: SecretStr | None = None
    # Only this tenant (and, when set, this workspace) may use developer mode: the
    # repository is the operator's own, never a customer's.
    tenant_id: str = ""
    workspace_id: str = ""
    base_branch: str = "main"
    workflow_file: str = "anum-developer.yml"
    github_api_url: str = "https://api.github.com"
    max_requests_per_day: int = Field(default=10, ge=1, le=200)
    # Each request is counted as one budgeted model action of this estimated cost.
    request_cost_usd: float = Field(default=1.0, ge=0, le=1000)
    status_cache_seconds: float = Field(default=15.0, ge=0, le=3600)

    model_config = SettingsConfigDict(
        env_prefix="ANUM_DEVELOPER_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )


developer_settings = DeveloperSettings()
# Tests replace this with an httpx.MockTransport; production uses the network.
github_transport: httpx.AsyncBaseTransport | None = None


class DevRequestSource(StrEnum):
    TEXT = "text"
    VOICE = "voice"


class DevRequestStatus(StrEnum):
    QUEUED = "queued"
    CODING = "coding"
    CI_RUNNING = "ci_running"
    CI_FAILED = "ci_failed"
    WAITING_APPROVAL = "waiting_approval"
    MERGED = "merged"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = frozenset({DevRequestStatus.MERGED, DevRequestStatus.FAILED, DevRequestStatus.CANCELLED})
ACTIVE = frozenset({DevRequestStatus.QUEUED, DevRequestStatus.CODING})


class ChecksSummary(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0
    pending: int = 0
    failing: list[str] = Field(default_factory=list)


class DeveloperRequest(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    created_by: str
    text: str
    source: DevRequestSource
    status: DevRequestStatus
    allow_protected_paths: bool = False
    branch: str
    run_id: int | None = None
    run_url: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    pr_title: str | None = None
    pr_draft: bool | None = None
    head_sha: str | None = None
    checks: ChecksSummary | None = None
    failure_reason: str | None = None
    merge_error: str | None = None
    approval_id: str | None = None
    merge_task_id: str | None = None
    merge_commit_sha: str | None = None
    created_at: datetime
    updated_at: datetime
    merged_at: datetime | None = None
    synced_at: datetime | None = None


StageState = Literal["done", "active", "failed", "pending"]


class TimelineStage(BaseModel):
    id: Literal["queued", "coding", "pr_opened", "ci", "approval", "merged"]
    label: str
    state: StageState


class DeveloperRequestView(DeveloperRequest):
    timeline: list[TimelineStage]
    # The pending merge approval, when one is waiting (approve it with its payload hash).
    approval: Approval | None = None
    summary: str


class DeveloperRequestCreate(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    source: DevRequestSource = DevRequestSource.TEXT
    allow_protected_paths: bool = False


class DeveloperSetup(BaseModel):
    configured: bool
    enabled_here: bool
    repository: str | None
    workflow: str
    base_branch: str
    missing: list[str]
    message: str
    max_requests_per_day: int
    requests_today: int = 0


class MergeApprovalResponse(BaseModel):
    request: DeveloperRequestView
    approval: Approval


# -- configuration ------------------------------------------------------------------------


def setup_problems(cfg: DeveloperSettings | None = None) -> list[str]:
    """Names of the settings that are missing or invalid (empty when configured)."""
    cfg = cfg or developer_settings
    problems: list[str] = []
    if not REPOSITORY_PATTERN.match(cfg.github_repo.strip()):
        problems.append("ANUM_DEVELOPER_GITHUB_REPO")
    if cfg.github_token is None or not cfg.github_token.get_secret_value().strip():
        problems.append("ANUM_DEVELOPER_GITHUB_TOKEN")
    if not cfg.tenant_id.strip():
        problems.append("ANUM_DEVELOPER_TENANT_ID")
    if not WORKFLOW_PATTERN.match(cfg.workflow_file):
        problems.append("ANUM_DEVELOPER_WORKFLOW_FILE")
    if not BRANCH_PATTERN.match(cfg.base_branch):
        problems.append("ANUM_DEVELOPER_BASE_BRANCH")
    if not cfg.github_api_url.startswith("https://"):
        problems.append("ANUM_DEVELOPER_GITHUB_API_URL")
    return problems


def enabled_for(context: TenantContext, cfg: DeveloperSettings | None = None) -> bool:
    cfg = cfg or developer_settings
    if context.tenant_id != cfg.tenant_id.strip():
        return False
    return not cfg.workspace_id.strip() or context.workspace_id == cfg.workspace_id.strip()


def github_client(cfg: DeveloperSettings | None = None) -> GitHubClient:
    cfg = cfg or developer_settings
    problems = setup_problems(cfg)
    if problems or cfg.github_token is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Developer mode is not set up: " + ", ".join(problems),
        )
    return GitHubClient(
        cfg.github_repo.strip(),
        cfg.github_token,
        api_url=cfg.github_api_url,
        workflow_file=cfg.workflow_file,
        transport=github_transport,
    )


def api_host(cfg: DeveloperSettings | None = None) -> str:
    """The integration target shown on merge approvals (host only)."""
    url = (cfg or developer_settings).github_api_url
    return httpx.URL(url).host or "api.github.com"


def clean_text(value: str) -> str:
    text = _CONTROL.sub(" ", value.replace("\r\n", "\n")).strip()
    return re.sub(r"[ \t]{2,}", " ", text)


def mentions_protected(text: str) -> bool:
    return bool(PROTECTED_REQUEST.search(text))


# -- storage ------------------------------------------------------------------------------


class DeveloperStoreProtocol(Protocol):
    def add(self, request: DeveloperRequest) -> DeveloperRequest: ...
    def get(self, request_id: str, context: TenantContext) -> DeveloperRequest | None: ...
    def save(self, request: DeveloperRequest) -> DeveloperRequest: ...
    def list(
        self, context: TenantContext, *, limit: int, before: DeveloperRequest | None, query: str | None
    ) -> list[DeveloperRequest]: ...
    def count_since(self, context: TenantContext, since: datetime) -> int: ...
    def active(self, context: TenantContext) -> list[DeveloperRequest]: ...


def _in_scope(request: DeveloperRequest, context: TenantContext) -> bool:
    return (request.tenant_id, request.workspace_id) == (context.tenant_id, context.workspace_id)


def _sort_key(request: DeveloperRequest) -> tuple[datetime, str]:
    return (request.created_at, request.id)


class InMemoryDeveloperStore:
    def __init__(self) -> None:
        self._items: dict[str, DeveloperRequest] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def add(self, request: DeveloperRequest) -> DeveloperRequest:
        with self._lock:
            self._items[request.id] = request.model_copy(deep=True)
        return request

    def get(self, request_id: str, context: TenantContext) -> DeveloperRequest | None:
        with self._lock:
            found = self._items.get(request_id)
        return found.model_copy(deep=True) if found is not None and _in_scope(found, context) else None

    def save(self, request: DeveloperRequest) -> DeveloperRequest:
        with self._lock:
            if request.id not in self._items:
                raise KeyError(request.id)
            self._items[request.id] = request.model_copy(deep=True)
        return request

    def list(
        self, context: TenantContext, *, limit: int, before: DeveloperRequest | None, query: str | None
    ) -> list[DeveloperRequest]:
        with self._lock:
            items = [item for item in self._items.values() if _in_scope(item, context)]
        if before is not None:
            items = [item for item in items if _sort_key(item) < _sort_key(before)]
        if query:
            needle = query.lower()
            items = [item for item in items if needle in item.text.lower() or needle in (item.pr_title or "").lower()]
        items.sort(key=_sort_key, reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def count_since(self, context: TenantContext, since: datetime) -> int:
        with self._lock:
            return sum(1 for item in self._items.values() if _in_scope(item, context) and item.created_at >= since)

    def active(self, context: TenantContext) -> list[DeveloperRequest]:
        with self._lock:
            return [
                item.model_copy(deep=True)
                for item in self._items.values()
                if _in_scope(item, context) and item.status in ACTIVE
            ]


developer_store = InMemoryDeveloperStore()


@contextmanager
def open_developer_store(context: TenantContext) -> Iterator[DeveloperStoreProtocol]:
    """One unit of work in the caller's tenant and workspace scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.developer_repository import SqlAlchemyDeveloperStore

        return SqlAlchemyDeveloperStore(session, context)

    with open_scoped_store(context, developer_store, sql_store) as store:
        yield store


# -- status -------------------------------------------------------------------------------

STAGES: list[tuple[str, str]] = [
    ("queued", "Queued"),
    ("coding", "Coding"),
    ("pr_opened", "Pull request opened"),
    ("ci", "CI checks"),
    ("approval", "Waiting for approval"),
    ("merged", "Merged"),
]

# Workflow step names (anum-developer.yml) that explain a failed run.
FAILED_STEP_REASONS = {
    "Validate the request": "The request was refused: its id or text is not valid.",
    "Refuse protected requests": (
        "Refused: the request asks to change CI workflows or secret handling. Allow it explicitly "
        "(and set the ANUM_DEV_ALLOW_PROTECTED_PATHS repository variable) if you really mean it."
    ),
    "Check that the developer changed something": "The developer finished without changing anything.",
    "Refuse protected paths": (
        "Refused: the change touched protected files (.github, CLAUDE.md, secret handling). Nothing was pushed."
    ),
    "Run the developer": "The coding agent stopped with an error. Open the run for details.",
    "Open the draft pull request": (
        "The branch could not be pushed or the pull request could not be opened. Check the "
        "ANUM_DEV_PUSH_TOKEN repository secret."
    ),
}


def _failed_stage(request: DeveloperRequest) -> str:
    if request.run_id is None:
        return "queued"
    if request.pr_number is None:
        return "coding"
    if request.checks is None or request.checks.failed or request.checks.pending or not request.checks.total:
        return "ci"
    return "approval"


def timeline(request: DeveloperRequest) -> list[TimelineStage]:
    order = [stage for stage, _ in STAGES]
    current = {
        DevRequestStatus.QUEUED: "queued",
        DevRequestStatus.CODING: "coding",
        DevRequestStatus.CI_RUNNING: "ci",
        DevRequestStatus.CI_FAILED: "ci",
        DevRequestStatus.WAITING_APPROVAL: "approval",
        DevRequestStatus.MERGED: "merged",
    }.get(request.status) or _failed_stage(request)
    index = order.index(current)
    failed = request.status in {DevRequestStatus.CI_FAILED, DevRequestStatus.FAILED, DevRequestStatus.CANCELLED}
    stages: list[TimelineStage] = []
    for position, (stage, label) in enumerate(STAGES):
        if request.status == DevRequestStatus.MERGED or position < index:
            state: StageState = "done"
        elif position == index:
            state = "failed" if failed else "active"
        else:
            state = "pending"
        stages.append(TimelineStage(id=stage, label=label, state=state))  # type: ignore[arg-type]
    return stages


def summary(request: DeveloperRequest) -> str:
    """One sentence for the list, the chat and the voice view."""
    title = request.pr_title or _short(request.text)
    match request.status:
        case DevRequestStatus.QUEUED:
            return "Queued: waiting for the developer workflow to start."
        case DevRequestStatus.CODING:
            return "The developer is making the change and running the checks."
        case DevRequestStatus.CI_RUNNING:
            return f"Pull request opened: {title}. CI is running."
        case DevRequestStatus.CI_FAILED:
            failing = ", ".join(request.checks.failing[:3]) if request.checks else ""
            return f"CI failed on {title}{f' ({failing})' if failing else ''}."
        case DevRequestStatus.WAITING_APPROVAL:
            return f"The change is ready for review: {title}. Approve to merge."
        case DevRequestStatus.MERGED:
            return f"Merged: {title}."
        case DevRequestStatus.CANCELLED:
            return "Cancelled."
        case _:
            return request.failure_reason or "The developer run failed."


def _short(text: str, limit: int = 80) -> str:
    words = " ".join(text.split())
    return words if len(words) <= limit else words[: limit - 1].rstrip() + "…"


def derive_status(
    request: DeveloperRequest,
    run: WorkflowRun | None,
    pull: PullRequest | None,
    checks: ChecksState | None,
    now: datetime,
) -> tuple[DevRequestStatus, str | None]:
    """The request's status from what GitHub reports (pure; the failure reason, if any)."""
    if pull is not None:
        if pull.merged:
            return DevRequestStatus.MERGED, None
        if pull.state == "closed":
            return DevRequestStatus.CANCELLED, "The pull request was closed without merging."
        if checks is None or checks.pending or not checks.total:
            return DevRequestStatus.CI_RUNNING, None
        if checks.failed:
            return DevRequestStatus.CI_FAILED, None
        return DevRequestStatus.WAITING_APPROVAL, None
    if run is None:
        if now - request.created_at > RUN_START_GRACE:
            return DevRequestStatus.FAILED, (
                "The developer workflow never started. Check that .github/workflows/anum-developer.yml "
                "is on the default branch and the ANTHROPIC_API_KEY secret is set."
            )
        return DevRequestStatus.QUEUED, None
    if run.status != "completed":
        return DevRequestStatus.CODING, None
    if run.conclusion == "cancelled":
        return DevRequestStatus.CANCELLED, "The developer run was cancelled."
    if run.conclusion == "success":
        # The run finished but the pull request is not visible yet: keep waiting a little.
        return DevRequestStatus.CODING, None
    return DevRequestStatus.FAILED, None


async def sync(request: DeveloperRequest, *, force: bool = False) -> tuple[DeveloperRequest, bool]:
    """Refresh a non-terminal request from GitHub. Returns the request and whether it changed status.

    A GitHub failure leaves the stored state as it was (the caller still shows it).
    """
    now = utc_now()
    if request.status in TERMINAL:
        return request, False
    if (
        not force
        and request.synced_at is not None
        and (now - request.synced_at).total_seconds() < developer_settings.status_cache_seconds
    ):
        return request, False
    client = github_client()
    previous = request.status
    try:
        run = await client.find_run(request.id)
        pull = await client.find_pull(request.branch)
        checks = await client.checks(pull.head_sha) if pull is not None and pull.state == "open" and pull.head_sha else None
        new_status, reason = derive_status(request, run, pull, checks, now)
        if new_status == DevRequestStatus.FAILED and run is not None and reason is None:
            step = await client.failed_step(run.id)
            reason = FAILED_STEP_REASONS.get(step or "", f"The developer run failed{f' at {step!r}' if step else ''}.")
    except GitHubError as exc:
        logger.warning(
            "developer_sync_failed status=%s",
            exc.status,
            extra={"anum_developer": {"event": "sync_failed", "status": exc.status}},
        )
        return request, False
    if run is not None:
        request.run_id, request.run_url = run.id, run.html_url
    if pull is not None:
        request.pr_number, request.pr_url, request.pr_title = pull.number, pull.html_url, pull.title
        request.pr_draft, request.head_sha = pull.draft, pull.head_sha
    if checks is not None:
        request.checks = ChecksSummary(**checks.as_dict())
    request.status = new_status
    if reason:
        request.failure_reason = reason
    if new_status == DevRequestStatus.MERGED and request.merged_at is None:
        request.merged_at = now
    request.synced_at = now
    request.updated_at = now if new_status != previous else request.updated_at
    return request, new_status != previous


# -- audit --------------------------------------------------------------------------------


def _audit(
    repository: AnumRepository,
    context: TenantContext,
    action: str,
    request: DeveloperRequest,
    outcome: str = "success",
    metadata: dict[str, Any] | None = None,
    *,
    actor: str | None = None,
) -> None:
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=actor or context.user_id,
            action=action,
            target=request.id,
            outcome=outcome,
            correlation_id=request.id,
            created_at=utc_now(),
            metadata={
                "status": request.status.value,
                "repository": developer_settings.github_repo,
                "pr_number": request.pr_number,
                **(metadata or {}),
            },
        )
    )


def _audit_detached(context: TenantContext, action: str, request: DeveloperRequest, outcome: str, metadata: dict[str, Any]) -> None:
    """Audit from the merge tool, which runs outside the request's repository session."""
    with _unit_of_work(context, require_membership=False) as repository:
        _audit(repository, context, action, request, outcome, metadata)


# -- the merge tool (executed only by the runtime, after approval) --------------------------


def merge_tool_definition(cfg: DeveloperSettings | None = None) -> ToolDefinition:
    return ToolDefinition(
        name=MERGE_TOOL,
        description="Squash-merge a developer-mode pull request whose CI checks all passed.",
        risk_level=RiskLevel.HIGH,
        required_roles=frozenset({"owner"}),
        timeout_seconds=60,
        target=api_host(cfg),
    )


def _failed(summary_text: str, **output: Any) -> ToolResult:
    return ToolResult(status="failed", summary=summary_text, output=output)


async def merge_pull_request(call: ToolCall, context: TenantContext) -> ToolResult:
    """Merge the approved pull request if it is unchanged and every check passed."""
    args = call.arguments
    request_id = str(args.get("request_id", ""))
    pull_number = args.get("pull_number")
    head_sha = str(args.get("head_sha", ""))
    if setup_problems() or not enabled_for(context):
        return _failed("Developer mode is not set up for this workspace; nothing was merged.")
    if args.get("repository") != developer_settings.github_repo.strip() or not isinstance(pull_number, int):
        return _failed("The approved repository is not the configured one; nothing was merged.")
    with open_developer_store(context) as store:
        request = store.get(request_id, context)
    if request is None or request.pr_number != pull_number:
        return _failed("The developer request was not found; nothing was merged.")
    client = github_client()

    def refuse(reason: str) -> ToolResult:
        request.merge_error = reason
        request.updated_at = utc_now()
        with open_developer_store(context) as store:
            store.save(request)
        _audit_detached(context, "developer.merge.refused", request, "denied", {"reason": reason, "head_sha": head_sha})
        return _failed(reason, request_id=request.id)

    try:
        pull = await client.get_pull(pull_number)
        if pull.merged:
            return ToolResult(status="succeeded", summary=f"Pull request #{pull_number} was already merged.")
        if pull.state != "open":
            return refuse("Not merged: the pull request is closed.")
        if pull.head_sha != head_sha:
            return refuse("Not merged: the pull request changed after approval. Request approval again.")
        checks = await client.checks(head_sha)
        if not checks.green:
            request.checks = ChecksSummary(**checks.as_dict())
            return refuse(
                f"Not merged: CI is not green ({checks.failed} failing, {checks.pending} running, {checks.passed} passed)."
            )
        if pull.draft:
            await client.mark_ready_for_review(pull)
        merge_sha = await client.merge(pull_number, head_sha, f"{pull.title} (#{pull_number})")
    except GitHubError as exc:
        return refuse(f"Not merged: {exc.message}")
    now = utc_now()
    request.status = DevRequestStatus.MERGED
    request.merge_commit_sha = merge_sha or None
    request.merged_at = request.updated_at = request.synced_at = now
    request.merge_error = None
    request.checks = ChecksSummary(**checks.as_dict())
    with open_developer_store(context) as store:
        store.save(request)
    _audit_detached(context, "developer.merge.completed", request, "success", {"merge_commit": merge_sha, "head_sha": head_sha})
    return ToolResult(
        status="succeeded",
        summary=f"Merged pull request #{pull_number} into {developer_settings.base_branch}.",
        output={"pull_number": pull_number, "merge_commit": merge_sha, "url": pull.html_url},
    )


def register_developer_tools(registry: ToolRegistry, cfg: DeveloperSettings | None = None) -> ToolRegistry:
    """Add the merge tool to a runtime's registry (the API's and the Temporal worker's)."""
    if MERGE_TOOL not in registry.names:
        registry.register(merge_tool_definition(cfg), merge_pull_request)
    return registry


# -- HTTP ---------------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/developer", tags=["developer"])


def _require_owner(context: TenantContext) -> None:
    require_permission(context, Permission.DEVELOPER_MANAGE)


def _require_enabled(context: TenantContext) -> None:
    problems = setup_problems()
    if problems:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Developer mode is not set up. Missing or invalid: " + ", ".join(problems),
        )
    if not enabled_for(context):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Developer mode is not enabled for this organization or workspace.",
        )


def _get_or_404(store: DeveloperStoreProtocol, request_id: str, context: TenantContext) -> DeveloperRequest:
    request = store.get(request_id, context)
    if request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Developer request not found")
    return request


def _view(request: DeveloperRequest, repository: AnumRepository, context: TenantContext) -> DeveloperRequestView:
    approval = None
    if request.approval_id and request.status == DevRequestStatus.WAITING_APPROVAL:
        found = repository.get_approval(request.approval_id, context)
        if found is not None and found.status == ApprovalStatus.PENDING:
            approval = found
    return DeveloperRequestView(
        **request.model_dump(), timeline=timeline(request), approval=approval, summary=summary(request)
    )


async def _refresh(
    request: DeveloperRequest, repository: AnumRepository, context: TenantContext, *, force: bool = False
) -> DeveloperRequest:
    previous = request.status
    request, changed = await sync(request, force=force)
    if request.synced_at is not None:
        with open_developer_store(context) as store:
            store.save(request)
    if changed:
        _audit(
            repository,
            context,
            "developer.request.status_changed",
            request,
            metadata={"from": previous.value, "to": request.status.value, "reason": request.failure_reason},
            actor="system",
        )
    return request


def _retire_merge_task(repository: AnumRepository, context: TenantContext, request: DeveloperRequest) -> None:
    """Cancel an earlier merge task still waiting for approval (as ``POST /tasks/{id}/cancel`` does)."""
    if not request.merge_task_id:
        return
    task = repository.get_task_for_update(request.merge_task_id, context)
    if task is None or task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        return
    now = utc_now()
    task.status = TaskStatus.CANCELLED
    task.updated_at = now
    repository.save_task(task)
    run = repository.find_run_for_task(task.id, context)
    if run is not None and run.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        run.status = TaskStatus.CANCELLED
        run.checkpoint.phase = RunPhase.CANCELLED
        run.checkpoint.version += 1
        run.updated_at = now
        repository.save_run(run)
    if request.approval_id:
        approval = repository.get_approval_for_update(request.approval_id, context)
        if approval is not None and approval.status == ApprovalStatus.PENDING:
            approval.status = ApprovalStatus.EXPIRED
            approval.decided_at = now
            repository.save_approval(approval)
    repository.record_event(
        create_event(CanonicalEventName.TASK_CANCELLED, context, task.id, {"task_id": task.id}, correlation_id=task.id).event
    )


@router.get("/setup", response_model=DeveloperSetup)
async def get_setup(context: TenantContext = Depends(tenant_context)) -> DeveloperSetup:
    """What is configured (never the token itself) and what is missing."""
    _require_owner(context)
    problems = setup_problems()
    enabled = not problems and enabled_for(context)
    if problems:
        message = "Developer mode needs these API settings: " + ", ".join(problems) + "."
    elif not enabled:
        message = "Developer mode is configured for another organization or workspace."
    else:
        message = f"Ready: requests go to {developer_settings.github_repo} through {developer_settings.workflow_file}."
    today = 0
    if enabled:
        with open_developer_store(context) as store:
            today = store.count_since(context, utc_now() - timedelta(days=1))
    return DeveloperSetup(
        configured=not problems,
        enabled_here=enabled,
        repository=developer_settings.github_repo.strip() or None,
        workflow=developer_settings.workflow_file,
        base_branch=developer_settings.base_branch,
        missing=problems,
        message=message,
        max_requests_per_day=developer_settings.max_requests_per_day,
        requests_today=today,
    )


@router.post("/requests", response_model=DeveloperRequestView, status_code=status.HTTP_201_CREATED)
async def create_request(
    payload: DeveloperRequestCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> DeveloperRequestView:
    _require_owner(context)
    _require_enabled(context)
    text = clean_text(payload.text)
    if len(text) < MIN_TEXT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Describe the change in at least {MIN_TEXT} characters.",
        )
    now = utc_now()
    request = DeveloperRequest(
        id=new_id("dev"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        created_by=context.user_id,
        text=text,
        source=payload.source,
        status=DevRequestStatus.QUEUED,
        allow_protected_paths=payload.allow_protected_paths,
        branch="",
        created_at=now,
        updated_at=now,
    )
    request.branch = f"{BRANCH_PREFIX}{request.id}"
    if mentions_protected(text) and not payload.allow_protected_paths:
        _audit(repository, context, "developer.request.refused", request, "denied", {"reason": "protected_paths"})
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "This request mentions CI workflows or secret handling, which the developer does not "
                "change unless you allow it explicitly."
            ),
        )
    with open_developer_store(context) as store:
        if store.active(context):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A developer request is already queued or coding. Wait for it to open its pull request, or cancel it.",
            )
        if store.count_since(context, now - timedelta(days=1)) >= developer_settings.max_requests_per_day:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Developer mode allows {developer_settings.max_requests_per_day} requests a day. "
                    "Try again tomorrow or raise ANUM_DEVELOPER_MAX_REQUESTS_PER_DAY."
                ),
            )
    # One budgeted model action: refused (402) when the monthly model budget is used up.
    check_model_budget(context)
    client = github_client()
    try:
        await client.dispatch(
            {
                "request_id": request.id,
                "text": text,
                "source": request.source.value,
                "allow_protected_paths": request.allow_protected_paths,
                "base_branch": developer_settings.base_branch,
            }
        )
    except GitHubError as exc:
        _audit(repository, context, "developer.request.dispatch_failed", request, "failure", {"github_status": exc.status})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The request could not be sent to GitHub. {exc.message}",
        ) from None
    record_model_usage(
        context,
        ModelUsage(
            input_tokens=0,
            output_tokens=0,
            provider="anum-developer",
            model="developer-request",
            estimated_cost_usd=developer_settings.request_cost_usd,
        ),
    )
    with open_developer_store(context) as store:
        store.add(request)
    _audit(
        repository,
        context,
        "developer.request.created",
        request,
        metadata={"source": request.source.value, "allow_protected_paths": request.allow_protected_paths, "branch": request.branch},
    )
    return _view(request, repository, context)


@router.get("/requests", response_model=list[DeveloperRequestView])
async def list_requests(
    limit: int = Query(default=20, ge=1, le=MAX_LISTED),
    before: str | None = Query(default=None, max_length=80),
    q: str | None = Query(default=None, max_length=200),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[DeveloperRequestView]:
    """Newest first. Unfinished requests on the page are refreshed from GitHub (cached briefly)."""
    _require_owner(context)
    with open_developer_store(context) as store:
        cursor = store.get(before, context) if before else None
        if before and cursor is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown cursor")
        items = store.list(context, limit=limit, before=cursor, query=(q or "").strip() or None)
    if not setup_problems() and enabled_for(context):
        items = [await _refresh(item, repository, context) for item in items]
    return [_view(item, repository, context) for item in items]


@router.get("/requests/{request_id}", response_model=DeveloperRequestView)
async def get_request(
    request_id: str,
    refresh: bool = Query(default=False),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> DeveloperRequestView:
    _require_owner(context)
    with open_developer_store(context) as store:
        request = _get_or_404(store, request_id, context)
    if not setup_problems() and enabled_for(context):
        request = await _refresh(request, repository, context, force=refresh)
    return _view(request, repository, context)


@router.post("/requests/{request_id}/merge-approval", response_model=MergeApprovalResponse)
async def request_merge_approval(
    request_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> MergeApprovalResponse:
    """Ask for the merge: a paused ``developer.merge_pull_request`` call waiting for approval.

    Allowed only while every check on the pull request's head commit passes. The approval
    is bound to that commit; approving it goes through ``POST /api/v1/approvals/{id}/approve``.
    """
    _require_owner(context)
    _require_enabled(context)
    with open_developer_store(context) as store:
        request = _get_or_404(store, request_id, context)
    request = await _refresh(request, repository, context, force=True)
    if request.status != DevRequestStatus.WAITING_APPROVAL or request.pr_number is None or not request.head_sha:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Not ready to merge: {summary(request)}",
        )
    if request.approval_id:
        existing = repository.get_approval(request.approval_id, context)
        if (
            existing is not None
            and existing.status == ApprovalStatus.PENDING
            and existing.arguments.get("head_sha") == request.head_sha
            and (existing.expires_at is None or existing.expires_at > utc_now())
        ):
            return MergeApprovalResponse(request=_view(request, repository, context), approval=existing)
        # The pull request changed (or the approval lapsed): the old merge task never runs.
        _retire_merge_task(repository, context, request)

    call = ToolCall(
        name=MERGE_TOOL,
        arguments={
            "request_id": request.id,
            "repository": developer_settings.github_repo.strip(),
            "pull_number": request.pr_number,
            "head_sha": request.head_sha,
            "base": developer_settings.base_branch,
            "merge_method": "squash",
            "title": request.pr_title or _short(request.text),
        },
    )
    now = utc_now()
    task = Task(
        id=new_id("task"),
        title=f"Merge developer change #{request.pr_number}"[:160],
        prompt=(
            f"Squash-merge pull request #{request.pr_number} ({request.pr_title or _short(request.text)}) into "
            f"{developer_settings.base_branch} of {developer_settings.github_repo} for developer request {request.id}."
        ),
        status=TaskStatus.CREATED,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        created_at=now,
        updated_at=now,
        created_by=context.user_id,
    )
    repository.create_task(task)
    repository.record_event(
        create_event(
            CanonicalEventName.TASK_CREATED, context, task.id, {"title": task.title}, correlation_id=task.id, created_at=now
        ).event
    )
    registry = register_developer_tools(ToolRegistry())
    runtime = AgentRuntime(MockModelGateway(), repository, tools=registry)
    run = runtime.new_run(task)
    decision = runtime.evaluate(call, context)
    proposal = AgentRunStep(
        id=new_id("step"),
        type="tool_proposal",
        summary=f"Proposed tool call: {MERGE_TOOL}",
        created_at=utc_now(),
        metadata={
            "tool": MERGE_TOOL,
            "risk_level": decision.risk_level.value,
            "policy_outcome": decision.outcome.value,
            "governance_rules": list(decision.governance_rules),
            "developer_request_id": request.id,
        },
    )
    run.steps.append(proposal)
    run.checkpoint.phase = RunPhase.TOOL_READY
    run.checkpoint.version += 1
    run.checkpoint.tool_call = call.model_dump(mode="json")
    run.checkpoint.last_step_id = proposal.id
    task.status = run.status = TaskStatus.RUNNING
    repository.save_task(task)
    repository.save_run(run)
    # Re-checks policy: high risk always pauses for approval; a governance deny fails the run.
    if runtime.begin_execution(task, run, context) is not None:  # pragma: no cover - high risk never runs unapproved
        raise RuntimeError("developer merge was not paused for approval")
    repository.save_task(task)
    repository.save_run(run)
    if run.checkpoint.phase != RunPhase.WAITING_APPROVAL or not run.checkpoint.approval_id:
        reason = run.steps[-1].summary if run.steps else "Blocked by policy."
        _audit(repository, context, "developer.merge.blocked", request, "denied", {"reason": reason, "task_id": task.id})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"The merge is blocked: {reason}")
    approval = repository.get_approval(run.checkpoint.approval_id, context)
    if approval is None:  # pragma: no cover - saved just above
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Approval was not stored")
    request.approval_id, request.merge_task_id, request.merge_error = approval.id, task.id, None
    request.updated_at = utc_now()
    with open_developer_store(context) as store:
        store.save(request)
    _audit(
        repository,
        context,
        "developer.merge.requested",
        request,
        metadata={"approval_id": approval.id, "task_id": task.id, "head_sha": request.head_sha, "payload_hash": approval.payload_hash},
    )
    return MergeApprovalResponse(request=_view(request, repository, context), approval=approval)


@router.post("/requests/{request_id}/cancel", response_model=DeveloperRequestView)
async def cancel_request(
    request_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> DeveloperRequestView:
    """Stop the coding run if it is still going, close the pull request and delete the branch."""
    _require_owner(context)
    _require_enabled(context)
    with open_developer_store(context) as store:
        request = _get_or_404(store, request_id, context)
    if request.status in TERMINAL:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Already {request.status.value}.")
    client = github_client()
    try:
        run = await client.find_run(request.id)
        if run is not None and run.status != "completed":
            await client.cancel_run(run.id)
        pull = await client.find_pull(request.branch)
        if pull is not None and pull.merged:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already merged.")
        if pull is not None and pull.state == "open":
            await client.close_pull(pull.number)
        await client.delete_branch(request.branch)
    except GitHubError as exc:
        _audit(repository, context, "developer.request.cancel_failed", request, "failure", {"github_status": exc.status})
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"GitHub refused the cancel. {exc.message}") from None
    _retire_merge_task(repository, context, request)
    now = utc_now()
    request.status = DevRequestStatus.CANCELLED
    request.failure_reason = "Cancelled by the owner."
    request.updated_at = request.synced_at = now
    with open_developer_store(context) as store:
        store.save(request)
    _audit(repository, context, "developer.request.cancelled", request)
    return _view(request, repository, context)
