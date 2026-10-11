"""Developer mode: the owner asks for a change and a coding agent builds it (docs/developer-mode.md).

Flow: an owner creates a request (typed or spoken) for one of the workspace's projects
(``developer_projects.py``: a GitHub repository with its branch, token and protected
paths). The API dispatches it to that repository's ``anum-developer.yml`` GitHub Actions
workflow, which runs the coding agent on a branch ``anum-dev/<request id>``, runs the
repository's checks and opens a draft pull request. The API never runs code; it only
reads progress back from GitHub (workflow run, pull request, CI) and shows it. Merging is
a high-risk tool call (``developer.merge_pull_request``) that goes through ANUM's
approvals: an owner asks for the merge, the runtime pauses for approval bound to the
project's repository and the pull request's head commit, and only after
``POST /api/v1/approvals/{id}/approve`` with the displayed payload hash does the tool
squash-merge, and only if every check on that commit passed.

Deployment-level configuration (``ANUM_DEVELOPER_*``) names the one tenant (optionally
one workspace) allowed to use developer mode, the GitHub API address, the limits, and
optionally a token covering selected repositories (``ANUM_DEVELOPER_GITHUB_TOKEN``).
``ANUM_DEVELOPER_GITHUB_REPO`` is the pre-projects single repository: migration
``0032_developer_projects`` turned it into the first project, and requests stored
without a project still resolve to it. Requests are stored per tenant and workspace
under forced RLS (migration ``0022_developer_requests``); the in-memory stores serve
``ANUM_REPOSITORY_BACKEND=memory``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any, Literal, Protocol

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import _unit_of_work, repository_context, require_permission, tenant_context
from .developer_github import ChecksState, GitHubClient, GitHubError, PullRequest, WorkflowRun
from .developer_projects import (
    DEFAULT_WORKFLOW,
    REPOSITORY_SECRETS,
    REPOSITORY_VARIABLES,
    DeveloperProject,
    DeveloperProjectView,
    ProjectCheck,
    ProjectConflictError,
    ProjectCreate,
    ProjectUpdate,
    ProjectValidationError,
    Resolution,
    TokenSource,
    check_branch,
    check_owner,
    check_project,
    check_repo,
    check_workflow,
    clean_description,
    clean_name,
    clean_protected_paths,
    encrypt_token,
    install_workflow,
    open_project_store,
    project_token,
    project_view,
    resolve_project,
    token_configured,
    workflow_template,
)
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

    # Before projects: the single repository (now the first project, made by migration 0032)
    # and the token used by projects whose token source is "environment".
    github_repo: str = ""
    github_token: SecretStr | None = None
    # Only this tenant (and, when set, this workspace) may use developer mode.
    tenant_id: str = ""
    workspace_id: str = ""
    # Defaults for the pre-projects repository; projects carry their own.
    base_branch: str = "main"
    workflow_file: str = DEFAULT_WORKFLOW
    github_api_url: str = "https://api.github.com"
    max_requests_per_day: int = Field(default=10, ge=1, le=200)
    max_projects: int = Field(default=100, ge=1, le=500)
    # Each request is counted as one budgeted model action of this estimated cost.
    request_cost_usd: float = Field(default=1.0, ge=0, le=1000)
    status_cache_seconds: float = Field(default=15.0, ge=0, le=3600)
    # With phone push on (ANUM_FCM_SERVICE_ACCOUNT_JSON), the API refreshes requests still
    # in progress from GitHub this often, so the owner hears that a pull request is ready
    # while the app is closed (docs/notifications.md#developer-mode). 0 turns it off.
    watch_seconds: float = Field(default=120.0, ge=0, le=86_400)

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
    # The project the request went to, and its name and repository when it was sent (kept
    # when the project is renamed or removed). None for requests from before projects.
    project_id: str | None = None
    project_name: str | None = None
    repository: str | None = None
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
    # Required when the workspace has more than one ready project.
    project_id: str | None = Field(default=None, max_length=80)


class ResolveRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class DeveloperSetup(BaseModel):
    configured: bool
    enabled_here: bool
    # The only ready project's repository, workflow and branch (clients from before projects).
    repository: str | None
    workflow: str
    base_branch: str
    missing: list[str]
    message: str
    max_requests_per_day: int
    requests_today: int = 0
    projects: int = 0
    ready_projects: int = 0
    max_projects: int = 100
    # Whether ANUM_DEVELOPER_GITHUB_TOKEN is set (never its value).
    environment_token_configured: bool = False


class MergeApprovalResponse(BaseModel):
    request: DeveloperRequestView
    approval: Approval


class WorkflowTemplate(BaseModel):
    file_name: str
    path: str
    content: str
    secrets: list[str]
    variables: list[str]


class InstallWorkflowResult(BaseModel):
    project: DeveloperProjectView
    pull_request_number: int
    pull_request_url: str
    created: bool


# -- configuration ------------------------------------------------------------------------


def setup_problems(cfg: DeveloperSettings | None = None) -> list[str]:
    """Names of the deployment settings that are missing or invalid (empty when configured).

    Repositories and their tokens are projects now; ``ANUM_DEVELOPER_GITHUB_REPO`` is only
    checked when it is set (it names the pre-projects repository)."""
    cfg = cfg or developer_settings
    problems: list[str] = []
    if cfg.github_repo.strip() and not REPOSITORY_PATTERN.match(cfg.github_repo.strip()):
        problems.append("ANUM_DEVELOPER_GITHUB_REPO")
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


def environment_token() -> SecretStr | None:
    token = developer_settings.github_token
    return token if token is not None and token.get_secret_value().strip() else None


def legacy_project(context: TenantContext) -> DeveloperProject | None:
    """The pre-projects repository (``ANUM_DEVELOPER_GITHUB_REPO``) for requests stored without
    a project; it uses ``ANUM_DEVELOPER_GITHUB_TOKEN``. None when that setting is not used."""
    repo = developer_settings.github_repo.strip()
    if not REPOSITORY_PATTERN.match(repo):
        return None
    owner, name = repo.split("/", 1)
    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    return DeveloperProject(
        id="",
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        name=name,
        github_owner=owner,
        github_repo=name,
        default_branch=developer_settings.base_branch,
        workflow_file=developer_settings.workflow_file,
        token_source=TokenSource.ENVIRONMENT,
        created_by="system",
        created_at=epoch,
        updated_at=epoch,
    )


def github_client(project: DeveloperProject) -> GitHubClient:
    """A client for one project's repository; 409 when it has no token."""
    token = project_token(project, environment_token())
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The project {project.name} has no GitHub token. Add one under Developer > Projects.",
        )
    return GitHubClient(
        project.repository,
        token,
        api_url=developer_settings.github_api_url,
        workflow_file=project.workflow_file,
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


class DeveloperAdmissionError(Exception):
    """``admit`` refused a new request: ``reason`` is ``"active"`` or ``"daily_limit"``."""

    def __init__(self, reason: Literal["active", "daily_limit"]) -> None:
        self.reason = reason
        super().__init__(reason)


class DeveloperStoreProtocol(Protocol):
    def add(self, request: DeveloperRequest) -> DeveloperRequest: ...
    def admit(self, request: DeveloperRequest, *, since: datetime, limit: int) -> DeveloperRequest:
        """Store ``request`` only if its project has no queued or coding request and its
        workspace created fewer than ``limit`` requests since ``since``. The checks and the
        insert are one atomic step; a refusal raises ``DeveloperAdmissionError``."""
        ...
    def get(self, request_id: str, context: TenantContext) -> DeveloperRequest | None: ...
    def save(self, request: DeveloperRequest) -> DeveloperRequest: ...
    def list(
        self,
        context: TenantContext,
        *,
        limit: int,
        before: DeveloperRequest | None,
        query: str | None,
        project_id: str | None = None,
    ) -> list[DeveloperRequest]: ...
    def count_since(self, context: TenantContext, since: datetime) -> int: ...
    def active(self, context: TenantContext) -> list[DeveloperRequest]: ...
    def unfinished(self, context: TenantContext, project_id: str) -> int:
        """Requests of ``project_id`` that are not merged, failed or cancelled."""
        ...


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

    def admit(self, request: DeveloperRequest, *, since: datetime, limit: int) -> DeveloperRequest:
        scope = (request.tenant_id, request.workspace_id)
        with self._lock:
            scoped = [item for item in self._items.values() if (item.tenant_id, item.workspace_id) == scope]
            if any(item.status in ACTIVE and (item.project_id or "") == (request.project_id or "") for item in scoped):
                raise DeveloperAdmissionError("active")
            if sum(1 for item in scoped if item.created_at >= since) >= limit:
                raise DeveloperAdmissionError("daily_limit")
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
        self,
        context: TenantContext,
        *,
        limit: int,
        before: DeveloperRequest | None,
        query: str | None,
        project_id: str | None = None,
    ) -> list[DeveloperRequest]:
        with self._lock:
            items = [item for item in self._items.values() if _in_scope(item, context)]
        if project_id is not None:
            items = [item for item in items if item.project_id == project_id]
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

    def unfinished(self, context: TenantContext, project_id: str) -> int:
        with self._lock:
            return sum(
                1
                for item in self._items.values()
                if _in_scope(item, context) and item.project_id == project_id and item.status not in TERMINAL
            )


developer_store = InMemoryDeveloperStore()


@contextmanager
def open_developer_store(context: TenantContext) -> Iterator[DeveloperStoreProtocol]:
    """One unit of work in the caller's tenant and workspace scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.developer_repository import SqlAlchemyDeveloperStore

        return SqlAlchemyDeveloperStore(session, context)

    with open_scoped_store(context, developer_store, sql_store) as store:
        yield store


def project_for(request: DeveloperRequest, context: TenantContext) -> DeveloperProject | None:
    """The project a stored request belongs to (the pre-projects repository for old rows)."""
    if not request.project_id:
        return legacy_project(context)
    with open_project_store(context) as projects:
        return projects.get(request.project_id, context)


# -- status -------------------------------------------------------------------------------

STAGES: list[tuple[str, str]] = [
    ("queued", "Queued"),
    ("coding", "Coding"),
    ("pr_opened", "Pull request opened"),
    ("ci", "CI checks"),
    ("approval", "Waiting for approval"),
    ("merged", "Merged"),
]

# Workflow step names (anum-developer.yml and the project template) that explain a failed run.
FAILED_STEP_REASONS = {
    "Validate the request": "The request was refused: its id or text is not valid.",
    "Refuse protected requests": (
        "Refused: the request asks to change CI workflows or secret handling. Allow it explicitly "
        "(and set the ANUM_DEV_ALLOW_PROTECTED_PATHS repository variable) if you really mean it."
    ),
    "Install dependencies for the checks": (
        "The repository's dependencies could not be installed. Open the run for details, then adjust the "
        "workflow's install step in that repository."
    ),
    "Check that the developer changed something": "The developer finished without changing anything.",
    "Refuse protected paths": (
        "Refused: the change touched protected files (.github, CLAUDE.md, secret handling or the project's "
        "protected paths). Nothing was pushed."
    ),
    "Run the developer": "The coding agent stopped with an error. Open the run for details.",
    "Open the draft pull request": (
        "The branch could not be pushed or the pull request could not be opened. Check the "
        "ANUM_DEV_PUSH_TOKEN repository secret."
    ),
    "Check the push credential": (
        "The repository has no ANUM_DEV_PUSH_TOKEN secret. Add it under Settings > Secrets and variables > Actions."
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
    *,
    workflow_path: str = f".github/workflows/{DEFAULT_WORKFLOW}",
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
                f"The developer workflow never started. Check that {workflow_path} is on the project's branch "
                "and the ANTHROPIC_API_KEY secret is set (Test connection under Developer > Projects checks the first)."
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


async def sync(
    request: DeveloperRequest, project: DeveloperProject | None = None, *, force: bool = False
) -> tuple[DeveloperRequest, bool]:
    """Refresh a non-terminal request from GitHub. Returns the request and whether it changed status.

    A GitHub failure, or a project without a token, leaves the stored state as it was (the
    caller still shows it).
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
    if project is None:
        return request, False
    token = project_token(project, environment_token())
    if token is None:
        return request, False
    client = github_client(project)
    previous = request.status
    try:
        run = await client.find_run(request.id)
        pull = await client.find_pull(request.branch)
        checks = await client.checks(pull.head_sha) if pull is not None and pull.state == "open" and pull.head_sha else None
        new_status, reason = derive_status(request, run, pull, checks, now, workflow_path=project.workflow_path)
        if new_status in ACTIVE and previous not in ACTIVE:
            # Never back to queued or coding once a request left them (a pull request briefly
            # missing from GitHub's answer): another request may have been admitted since, and
            # at most one per project is active (uq_developer_requests_one_active_project).
            new_status, reason = previous, None
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
                "project_id": request.project_id,
                "repository": request.repository or developer_settings.github_repo,
                "pr_number": request.pr_number,
                **(metadata or {}),
            },
        )
    )


def _audit_project(
    repository: AnumRepository,
    context: TenantContext,
    action: str,
    project: DeveloperProject,
    outcome: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Project changes are audited by name and repository; tokens never appear, only whether one changed."""
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=project.id,
            outcome=outcome,
            correlation_id=project.id,
            created_at=utc_now(),
            metadata={"name": project.name, "repository": project.repository, **(metadata or {})},
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
    """Merge the approved pull request if it is unchanged, in the approved repository, and every check passed."""
    args = call.arguments
    request_id = str(args.get("request_id", ""))
    pull_number = args.get("pull_number")
    head_sha = str(args.get("head_sha", ""))
    if setup_problems() or not enabled_for(context):
        return _failed("Developer mode is not set up for this workspace; nothing was merged.")
    with open_developer_store(context) as store:
        request = store.get(request_id, context)
    if request is None or request.pr_number != pull_number or not isinstance(pull_number, int):
        return _failed("The developer request was not found; nothing was merged.")
    project = project_for(request, context)
    if project is None:
        return _failed("The request's project was removed; nothing was merged.")
    if args.get("repository") != project.repository or str(args.get("project_id") or "") != (request.project_id or ""):
        return _failed("The approved repository is not the request's project; nothing was merged.")
    if not project.enabled:
        return _failed(f"The project {project.name} is turned off; nothing was merged.")
    if project_token(project, environment_token()) is None:
        return _failed(f"The project {project.name} has no GitHub token; nothing was merged.")
    client = github_client(project)

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
        summary=f"Merged pull request #{pull_number} into {project.default_branch} of {project.repository}.",
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
    request: DeveloperRequest,
    repository: AnumRepository,
    context: TenantContext,
    *,
    force: bool = False,
    project: DeveloperProject | None = None,
) -> DeveloperRequest:
    if request.status in TERMINAL:
        return request
    previous = request.status
    request, changed = await sync(request, project if project is not None else project_for(request, context), force=force)
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
        event_type = STATUS_EVENTS.get(request.status)
        if event_type is not None:
            # Ids and statuses only (docs/events.md): phone push tells the owner, even with
            # the app closed, without the request text or the pull request title.
            repository.record_event(
                create_event(
                    event_type,
                    context,
                    request.id,
                    {"status": request.status.value, "previous_status": previous.value, "pr_number": request.pr_number},
                    actor_id=request.created_by,
                    correlation_id=request.id,
                ).event
            )
    return request


# A refresh that moves a request into one of these statuses announces it (once per change).
STATUS_EVENTS: dict[DevRequestStatus, CanonicalEventName] = {
    DevRequestStatus.WAITING_APPROVAL: CanonicalEventName.DEVELOPER_REQUEST_READY_FOR_REVIEW,
    DevRequestStatus.CI_FAILED: CanonicalEventName.DEVELOPER_REQUEST_FAILED,
    DevRequestStatus.FAILED: CanonicalEventName.DEVELOPER_REQUEST_FAILED,
}


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


def _mark_not_sent(context: TenantContext, request: DeveloperRequest, reason: str) -> None:
    """Fail an admitted request whose dispatch did not go through, so it blocks nothing.

    If even this write fails, ``sync`` fails the queued request once ``RUN_START_GRACE``
    passes without a workflow run.
    """
    request.status = DevRequestStatus.FAILED
    request.failure_reason = reason
    request.updated_at = utc_now()
    try:
        with open_developer_store(context) as store:
            store.save(request)
    except Exception:
        logger.exception(
            "developer_mark_not_sent_failed",
            extra={"anum_developer": {"event": "mark_not_sent_failed", "request_id": request.id}},
        )


def _all_projects(context: TenantContext, query: str | None = None) -> list[DeveloperProject]:
    with open_project_store(context) as projects:
        return projects.list(context, query=query)


def _sendable(project: DeveloperProject) -> bool:
    """Enabled with a token: requests may be routed to it (a failing check is re-run on send)."""
    return project.enabled and token_configured(project, environment_token())


@router.get("/setup", response_model=DeveloperSetup)
async def get_setup(context: TenantContext = Depends(tenant_context)) -> DeveloperSetup:
    """What is configured (never a token) and what is missing."""
    _require_owner(context)
    problems = setup_problems()
    enabled = not problems and enabled_for(context)
    today = 0
    projects: list[DeveloperProject] = []
    if enabled:
        with open_developer_store(context) as store:
            today = store.count_since(context, utc_now() - timedelta(days=1))
        projects = _all_projects(context)
    ready = [project for project in projects if project_view(project, environment_token()).ready]
    only = ready[0] if len(ready) == 1 else None
    if problems:
        message = "Developer mode needs these API settings: " + ", ".join(problems) + "."
    elif not enabled:
        message = "Developer mode is configured for another organization or workspace."
    elif not projects:
        legacy = developer_settings.github_repo.strip()
        message = (
            f"Add a project for {legacy} (ANUM_DEVELOPER_GITHUB_REPO), or any other GitHub repository, to send requests."
            if legacy
            else "Add a project: the GitHub repository the developer should change."
        )
    elif not ready:
        message = "No project is ready: open Projects to add a token, test the connection or turn a project on."
    else:
        message = f"Ready: {len(ready)} of {len(projects)} projects can take requests."
    return DeveloperSetup(
        configured=not problems,
        enabled_here=enabled,
        repository=only.repository if only else None,
        workflow=only.workflow_file if only else developer_settings.workflow_file,
        base_branch=only.default_branch if only else developer_settings.base_branch,
        missing=problems,
        message=message,
        max_requests_per_day=developer_settings.max_requests_per_day,
        requests_today=today,
        projects=len(projects),
        ready_projects=len(ready),
        max_projects=developer_settings.max_projects,
        environment_token_configured=environment_token() is not None,
    )


# -- projects -----------------------------------------------------------------------------


def _project_or_404(context: TenantContext, project_id: str) -> DeveloperProject:
    with open_project_store(context) as projects:
        project = projects.get(project_id, context)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _invalid(exc: ProjectValidationError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


def _conflict(exc: ProjectConflictError) -> HTTPException:
    detail = (
        "Another project already has this name. Choose another name."
        if exc.field == "name"
        else "Another project already uses this repository."
    )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@router.get("/projects", response_model=list[DeveloperProjectView])
async def list_projects(
    q: str | None = Query(default=None, max_length=200),
    context: TenantContext = Depends(tenant_context),
) -> list[DeveloperProjectView]:
    """The workspace's projects by name (at most ``ANUM_DEVELOPER_MAX_PROJECTS``); ``q`` searches
    the name, repository and description."""
    _require_owner(context)
    _require_enabled(context)
    token = environment_token()
    return [project_view(project, token) for project in _all_projects(context, (q or "").strip() or None)]


@router.post("/projects", response_model=DeveloperProjectView, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> DeveloperProjectView:
    _require_owner(context)
    _require_enabled(context)
    now = utc_now()
    try:
        project = DeveloperProject(
            id=new_id("devproj"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            name=clean_name(payload.name),
            github_owner=check_owner(payload.github_owner),
            github_repo=check_repo(payload.github_repo),
            default_branch=check_branch(payload.default_branch),
            description=clean_description(payload.description),
            enabled=payload.enabled,
            protected_paths=clean_protected_paths(payload.protected_paths),
            workflow_file=check_workflow(payload.workflow_file),
            token_source=payload.token_source,
            created_by=context.user_id,
            created_at=now,
            updated_at=now,
        )
        if payload.token is not None and payload.token.get_secret_value().strip():
            project.token_ciphertext = encrypt_token(payload.token)
            project.token_updated_at = now
    except ProjectValidationError as exc:
        raise _invalid(exc) from None
    try:
        with open_project_store(context) as projects:
            if projects.count(context) >= developer_settings.max_projects:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A workspace can have {developer_settings.max_projects} projects. Remove one first.",
                )
            projects.add(project)
    except ProjectConflictError as exc:
        raise _conflict(exc) from None
    _audit_project(
        repository,
        context,
        "developer.project.created",
        project,
        metadata={"enabled": project.enabled, "token_source": project.token_source.value, "token_set": bool(project.token_ciphertext)},
    )
    return project_view(project, environment_token())


@router.get("/projects/{project_id}", response_model=DeveloperProjectView)
async def get_project(project_id: str, context: TenantContext = Depends(tenant_context)) -> DeveloperProjectView:
    _require_owner(context)
    _require_enabled(context)
    return project_view(_project_or_404(context, project_id), environment_token())


@router.patch("/projects/{project_id}", response_model=DeveloperProjectView)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> DeveloperProjectView:
    """Change any field. A new token replaces the stored one; ``clear_token`` removes it.
    Changing the repository, branch or workflow file forgets the last connection test."""
    _require_owner(context)
    _require_enabled(context)
    project = _project_or_404(context, project_id)
    changed: list[str] = []
    try:
        if payload.name is not None and clean_name(payload.name) != project.name:
            project.name = clean_name(payload.name)
            changed.append("name")
        target = {
            "github_owner": check_owner(payload.github_owner) if payload.github_owner is not None else project.github_owner,
            "github_repo": check_repo(payload.github_repo) if payload.github_repo is not None else project.github_repo,
            "default_branch": check_branch(payload.default_branch) if payload.default_branch is not None else project.default_branch,
            "workflow_file": check_workflow(payload.workflow_file) if payload.workflow_file is not None else project.workflow_file,
        }
        moved = [name for name, value in target.items() if getattr(project, name) != value]
        for name in moved:
            setattr(project, name, target[name])
        changed.extend(moved)
        if moved:
            project.last_check = None
        if {"github_owner", "github_repo"} & set(moved):
            project.install_pr_number = project.install_pr_url = None
        if payload.description is not None and clean_description(payload.description) != project.description:
            project.description = clean_description(payload.description)
            changed.append("description")
        if payload.enabled is not None and payload.enabled != project.enabled:
            project.enabled = payload.enabled
            changed.append("enabled")
        if payload.protected_paths is not None:
            paths = clean_protected_paths(payload.protected_paths)
            if paths != project.protected_paths:
                project.protected_paths = paths
                changed.append("protected_paths")
        if payload.token_source is not None and payload.token_source != project.token_source:
            project.token_source = payload.token_source
            project.last_check = None
            changed.append("token_source")
        token_changed = False
        if payload.clear_token and project.token_ciphertext:
            project.token_ciphertext = None
            project.token_updated_at = utc_now()
            token_changed = True
        if payload.token is not None and payload.token.get_secret_value().strip():
            project.token_ciphertext = encrypt_token(payload.token)
            project.token_updated_at = utc_now()
            token_changed = True
        if token_changed:
            project.last_check = None
            changed.append("token")
    except ProjectValidationError as exc:
        raise _invalid(exc) from None
    if not changed:
        return project_view(project, environment_token())
    project.updated_at = utc_now()
    try:
        with open_project_store(context) as projects:
            projects.save(project)
    except ProjectConflictError as exc:
        raise _conflict(exc) from None
    _audit_project(repository, context, "developer.project.updated", project, metadata={"changed": changed, "enabled": project.enabled})
    return project_view(project, environment_token())


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """Remove a project. Its finished requests stay listed under its name; unfinished ones block this."""
    _require_owner(context)
    _require_enabled(context)
    project = _project_or_404(context, project_id)
    with open_developer_store(context) as store:
        unfinished = store.unfinished(context, project.id)
    if unfinished:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{project.name} has {unfinished} unfinished request{'s' if unfinished != 1 else ''}. Cancel or merge "
                "them first, or turn the project off instead."
            ),
        )
    with open_project_store(context) as projects:
        projects.delete(project.id, context)
    _audit_project(repository, context, "developer.project.deleted", project)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/test", response_model=DeveloperProjectView)
async def test_project(
    project_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> DeveloperProjectView:
    """Test connection: the repository is reachable with the token and has the workflow."""
    _require_owner(context)
    _require_enabled(context)
    project = _project_or_404(context, project_id)
    project = await _run_check(project, context)
    ok = project.last_check is not None and project.last_check.ok
    _audit_project(
        repository,
        context,
        "developer.project.tested",
        project,
        "success" if ok else "failure",
        {"ok": ok, "workflow_present": bool(project.last_check and project.last_check.workflow_present)},
    )
    return project_view(project, environment_token())


async def _run_check(project: DeveloperProject, context: TenantContext) -> DeveloperProject:
    """Run Test connection and store the result (a project without a token fails it plainly)."""
    if project_token(project, environment_token()) is None:
        project.last_check = ProjectCheck(
            ok=False, checked_at=utc_now(), message="Add a GitHub token for this project first."
        )
    else:
        project.last_check = await check_project(github_client(project), project)
    with open_project_store(context) as projects:
        current = projects.get(project.id, context)
        if current is not None:
            current.last_check = project.last_check
            projects.save(current)
            project = current
    return project


@router.post("/projects/{project_id}/install-workflow", response_model=InstallWorkflowResult)
async def install_project_workflow(
    project_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> InstallWorkflowResult:
    """Open a pull request in the project's repository that adds the coding workflow.

    The owner reviews and merges it in GitHub; nothing here merges. Returns the open pull
    request if one is already waiting."""
    _require_owner(context)
    _require_enabled(context)
    project = _project_or_404(context, project_id)
    client = github_client(project)
    try:
        if await client.file_sha(project.workflow_path, project.default_branch) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{project.workflow_path} is already on {project.default_branch}. Press Test connection.",
            )
        pull, created = await install_workflow(client, project)
    except GitHubError as exc:
        hint = (
            " The token needs Contents, Pull requests and Workflows read and write on this repository."
            if exc.status in {403, 404, 422}
            else ""
        )
        _audit_project(repository, context, "developer.project.workflow_install_failed", project, "failure", {"github_status": exc.status})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"The workflow could not be proposed. {exc.message}{hint}"
        ) from None
    with open_project_store(context) as projects:
        current = projects.get(project.id, context) or project
        current.install_pr_number, current.install_pr_url = pull.number, pull.html_url
        current.updated_at = utc_now()
        projects.save(current)
    _audit_project(
        repository,
        context,
        "developer.project.workflow_install_requested",
        current,
        metadata={"pr_number": pull.number, "created": created},
    )
    return InstallWorkflowResult(
        project=project_view(current, environment_token()),
        pull_request_number=pull.number,
        pull_request_url=pull.html_url,
        created=created,
    )


@router.get("/workflow-template", response_model=WorkflowTemplate)
async def get_workflow_template(context: TenantContext = Depends(tenant_context)) -> WorkflowTemplate:
    """The coding workflow a project's repository needs, and the secret and variable names it uses."""
    _require_owner(context)
    return WorkflowTemplate(
        file_name=DEFAULT_WORKFLOW,
        path=f".github/workflows/{DEFAULT_WORKFLOW}",
        content=workflow_template(),
        secrets=list(REPOSITORY_SECRETS),
        variables=list(REPOSITORY_VARIABLES),
    )


@router.post("/resolve", response_model=Resolution)
async def resolve(payload: ResolveRequest, context: TenantContext = Depends(tenant_context)) -> Resolution:
    """Which project a chat or voice request is for ("build X in TyrePulse"). Creates nothing:
    clients confirm "I'll ask the developer to: … in <project>. Start?" before sending."""
    _require_owner(context)
    _require_enabled(context)
    return resolve_project(clean_text(payload.text), [project for project in _all_projects(context) if _sendable(project)])


# -- requests -----------------------------------------------------------------------------


async def _project_for_new_request(payload: DeveloperRequestCreate, context: TenantContext) -> DeveloperProject:
    if payload.project_id:
        with open_project_store(context) as projects:
            project = projects.get(payload.project_id, context)
        if project is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="That project does not exist any more.")
    else:
        sendable = [project for project in _all_projects(context) if _sendable(project)]
        if len(sendable) != 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "Add a project first: the GitHub repository the developer should change."
                    if not sendable
                    else "Pick the project this request is for: " + ", ".join(project.name for project in sendable) + "."
                ),
            )
        project = sendable[0]
    if not project.enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"The project {project.name} is turned off.")
    if project_token(project, environment_token()) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The project {project.name} has no GitHub token. Add one under Developer > Projects.",
        )
    if project.last_check is not None and not project.last_check.ok:
        # The owner may have fixed it since (merged the install pull request, added a secret).
        project = await _run_check(project, context)
        if project.last_check is not None and not project.last_check.ok:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{project.name} is not ready: {project.last_check.message}",
            )
    return project


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
    project = await _project_for_new_request(payload, context)
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
        project_id=project.id or None,
        project_name=project.name,
        repository=project.repository,
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
    # One budgeted model action: refused (402) when the monthly model budget is used up.
    check_model_budget(context)
    # Admission is atomic with the insert and committed before anything is sent to GitHub:
    # two concurrent creates cannot both pass the "nothing active" and daily-limit checks.
    try:
        with open_developer_store(context) as store:
            store.admit(request, since=now - timedelta(days=1), limit=developer_settings.max_requests_per_day)
    except DeveloperAdmissionError as exc:
        if exc.reason == "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"A request for {project.name} is already queued or coding. Wait for it to open its pull "
                    "request, or cancel it."
                ),
            ) from None
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Developer mode allows {developer_settings.max_requests_per_day} requests a day. "
                "Try again tomorrow or raise ANUM_DEVELOPER_MAX_REQUESTS_PER_DAY."
            ),
        ) from None
    client = github_client(project)
    try:
        await client.dispatch(
            {
                "request_id": request.id,
                "text": text,
                "source": request.source.value,
                "allow_protected_paths": request.allow_protected_paths,
                "base_branch": project.default_branch,
                "protected_paths": list(project.protected_paths),
            }
        )
    except GitHubError as exc:
        _mark_not_sent(context, request, f"Not sent to GitHub: {exc.message}")
        _audit(repository, context, "developer.request.dispatch_failed", request, "failure", {"github_status": exc.status})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The request could not be sent to GitHub. {exc.message}",
        ) from None
    except BaseException:
        _mark_not_sent(context, request, "Not sent: the API stopped while sending the request to GitHub.")
        raise
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
    project_id: str | None = Query(default=None, max_length=80),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[DeveloperRequestView]:
    """Newest first. Unfinished requests on the page are refreshed from GitHub (cached briefly)."""
    _require_owner(context)
    with open_developer_store(context) as store:
        cursor = store.get(before, context) if before else None
        if before and cursor is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown cursor")
        items = store.list(context, limit=limit, before=cursor, query=(q or "").strip() or None, project_id=project_id or None)
    if not setup_problems() and enabled_for(context) and any(item.status not in TERMINAL for item in items):
        # One read of the projects for the whole page, not one per request.
        projects = {project.id: project for project in _all_projects(context)}
        legacy = legacy_project(context)
        refreshed: list[DeveloperRequest] = []
        for item in items:
            project = projects.get(item.project_id) if item.project_id else legacy
            if item.status not in TERMINAL and project is not None:
                item = await _refresh(item, repository, context, project=project)
            refreshed.append(item)
        items = refreshed
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
    is bound to the project's repository and that commit; approving it goes through
    ``POST /api/v1/approvals/{id}/approve``.
    """
    _require_owner(context)
    _require_enabled(context)
    with open_developer_store(context) as store:
        request = _get_or_404(store, request_id, context)
    project = project_for(request, context)
    if project is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The request's project was removed; it cannot be merged from ANUM.")
    if not project.enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"The project {project.name} is turned off. Turn it on to merge.")
    request = await _refresh(request, repository, context, force=True, project=project)
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
            and existing.arguments.get("repository") == project.repository
            and (existing.expires_at is None or existing.expires_at > utc_now())
        ):
            return MergeApprovalResponse(request=_view(request, repository, context), approval=existing)
        # The pull request changed (or the approval lapsed): the old merge task never runs.
        _retire_merge_task(repository, context, request)

    call = ToolCall(
        name=MERGE_TOOL,
        arguments={
            "request_id": request.id,
            "project_id": request.project_id or "",
            "repository": project.repository,
            "pull_number": request.pr_number,
            "head_sha": request.head_sha,
            "base": project.default_branch,
            "merge_method": "squash",
            "title": request.pr_title or _short(request.text),
        },
    )
    now = utc_now()
    task = Task(
        id=new_id("task"),
        title=f"Merge developer change #{request.pr_number} in {project.name}"[:160],
        prompt=(
            f"Squash-merge pull request #{request.pr_number} ({request.pr_title or _short(request.text)}) into "
            f"{project.default_branch} of {project.repository} for developer request {request.id}."
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
    project = project_for(request, context)
    if project is not None and project_token(project, environment_token()) is not None:
        client = github_client(project)
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
    request.failure_reason = (
        "Cancelled by the owner."
        if project is not None and project_token(project, environment_token()) is not None
        else "Cancelled by the owner. Its project has no token any more, so close the branch and pull request in GitHub."
    )
    request.updated_at = request.synced_at = now
    with open_developer_store(context) as store:
        store.save(request)
    _audit(repository, context, "developer.request.cancelled", request)
    return _view(request, repository, context)
