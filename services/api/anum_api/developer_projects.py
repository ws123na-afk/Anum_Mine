"""Developer-mode projects: the repositories an owner lets the developer change (docs/developer-mode.md#projects).

A project names one GitHub repository (``owner/repo``), the branch requests build on, a
short description that routes spoken and typed requests ("build X in TyrePulse"), an
enabled switch and optional extra protected paths. Projects belong to a tenant and
workspace (forced RLS, migration ``0031_developer_projects``) and only owners manage them.

The GitHub token is per project (stored Fernet-encrypted with ``ANUM_SECRETS_KEY``, the
same cipher as model provider keys) or the deployment's ``ANUM_DEVELOPER_GITHUB_TOKEN``
(one token covering the selected repositories). A token is never returned, logged or
audited; views only say whether one is configured.

This module holds the models, the stores, validation, the request-to-project resolver,
the coding-workflow template and the GitHub checks behind Test connection and Install
workflow. The HTTP routes are in ``developer.py``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Literal, Protocol

from pydantic import BaseModel, Field, SecretStr

from .developer_github import GitHubClient, GitHubError, PullRequest
from .schemas import TenantContext, utc_now
from .scoped_store import open_scoped_store

MAX_NAME = 60
MAX_DESCRIPTION = 500
MAX_PROTECTED_PATHS = 50
DEFAULT_WORKFLOW = "anum-developer.yml"
WORKFLOW_DIRECTORY = ".github/workflows"
# The branch Install workflow opens its pull request from.
INSTALL_BRANCH = "anum-dev-setup/workflow"
TEMPLATE_PATH = Path(__file__).parent / "workflow_templates" / "anum-developer.yml"
# Names only: what each target repository needs (docs/developer-mode.md#set-up-a-repository).
REPOSITORY_SECRETS = ("ANTHROPIC_API_KEY", "ANUM_DEV_PUSH_TOKEN")
REPOSITORY_VARIABLES = ("ANUM_DEV_ALLOW_PROTECTED_PATHS", "ANUM_DEV_PROTECTED_PATHS")

OWNER_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]{0,99}$")
WORKFLOW_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}\.ya?ml$")
PROTECTED_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._-][A-Za-z0-9._/-]{0,199}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class TokenSource(StrEnum):
    # A token stored (encrypted) on this project.
    PROJECT = "project"
    # The deployment's ANUM_DEVELOPER_GITHUB_TOKEN, one token covering selected repositories.
    ENVIRONMENT = "environment"


class ProjectCheck(BaseModel):
    """What Test connection found (stored on the project, shown until the next check)."""

    ok: bool
    checked_at: datetime
    repository_reachable: bool = False
    default_branch_exists: bool = False
    workflow_present: bool = False
    actions_readable: bool = False
    github_default_branch: str | None = None
    message: str


class DeveloperProject(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    name: str
    github_owner: str
    github_repo: str
    default_branch: str = "main"
    description: str = ""
    enabled: bool = True
    protected_paths: list[str] = Field(default_factory=list)
    workflow_file: str = DEFAULT_WORKFLOW
    token_source: TokenSource = TokenSource.PROJECT
    # Fernet ciphertext; never leaves the API (DeveloperProjectView has no such field).
    token_ciphertext: str | None = None
    token_updated_at: datetime | None = None
    last_check: ProjectCheck | None = None
    install_pr_number: int | None = None
    install_pr_url: str | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime

    @property
    def repository(self) -> str:
        return f"{self.github_owner}/{self.github_repo}"

    @property
    def workflow_path(self) -> str:
        return f"{WORKFLOW_DIRECTORY}/{self.workflow_file}"


class DeveloperProjectView(BaseModel):
    """A project as the API returns it: never the token, only whether one is configured."""

    id: str
    name: str
    github_owner: str
    github_repo: str
    repository: str
    default_branch: str
    description: str
    enabled: bool
    protected_paths: list[str]
    workflow_file: str
    token_source: TokenSource
    token_configured: bool
    token_updated_at: datetime | None
    last_check: ProjectCheck | None
    install_pr_number: int | None
    install_pr_url: str | None
    ready: bool
    # What stops requests to this project, as sentences that say how to fix it.
    problems: list[str]
    created_by: str
    created_at: datetime
    updated_at: datetime


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    github_owner: str = Field(min_length=1, max_length=39)
    github_repo: str = Field(min_length=1, max_length=100)
    default_branch: str = Field(default="main", min_length=1, max_length=100)
    description: str = Field(default="", max_length=MAX_DESCRIPTION)
    enabled: bool = True
    protected_paths: list[str] = Field(default_factory=list, max_length=MAX_PROTECTED_PATHS)
    workflow_file: str = Field(default=DEFAULT_WORKFLOW, max_length=110)
    token_source: TokenSource = TokenSource.PROJECT
    # Write-only. Never echoed back; stored encrypted.
    token: SecretStr | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    github_owner: str | None = Field(default=None, min_length=1, max_length=39)
    github_repo: str | None = Field(default=None, min_length=1, max_length=100)
    default_branch: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    enabled: bool | None = None
    protected_paths: list[str] | None = Field(default=None, max_length=MAX_PROTECTED_PATHS)
    workflow_file: str | None = Field(default=None, max_length=110)
    token_source: TokenSource | None = None
    token: SecretStr | None = None
    clear_token: bool = False


class ProjectRef(BaseModel):
    id: str
    name: str
    repository: str
    description: str


class Resolution(BaseModel):
    """Which project a spoken or typed request is for (``POST /api/v1/developer/resolve``).

    ``resolved``: one project (by its name, its description, or because it is the only
    ready one); clients still confirm "I'll ask the developer to: … in <project>. Start?".
    ``ambiguous``: ask which of ``candidates``. ``none``: no ready project to send it to.
    """

    status: Literal["resolved", "ambiguous", "none"]
    text: str
    project: ProjectRef | None = None
    candidates: list[ProjectRef] = Field(default_factory=list)
    matched_by: Literal["name", "description", "only_project"] | None = None


class ProjectValidationError(ValueError):
    """A field is invalid; the message says which and how to fix it."""


class ProjectConflictError(Exception):
    """Another project in the workspace already has this ``field`` (``name`` or ``repository``)."""

    def __init__(self, field: Literal["name", "repository"]) -> None:
        self.field = field
        super().__init__(field)


# -- validation ---------------------------------------------------------------------------


def clean_name(value: str) -> str:
    name = " ".join(_CONTROL.sub(" ", value).split())
    if not name:
        raise ProjectValidationError("Give the project a name.")
    if len(name) > MAX_NAME:
        raise ProjectValidationError(f"Keep the project name under {MAX_NAME} characters.")
    return name


def clean_description(value: str) -> str:
    return " ".join(_CONTROL.sub(" ", value).split())[:MAX_DESCRIPTION]


def check_owner(value: str) -> str:
    owner = value.strip()
    if not OWNER_PATTERN.match(owner):
        raise ProjectValidationError("The GitHub owner is a user or organization name: letters, digits and hyphens.")
    return owner


def check_repo(value: str) -> str:
    repo = value.strip()
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    if not REPO_PATTERN.match(repo) or repo in {".", ".."}:
        raise ProjectValidationError("The repository name is letters, digits, '.', '_' and '-' (as in owner/repo).")
    return repo


def check_branch(value: str) -> str:
    branch = value.strip()
    if not BRANCH_PATTERN.match(branch) or ".." in branch or branch.endswith(("/", ".lock")) or "//" in branch:
        raise ProjectValidationError("The branch name is not valid (for example main or release/2026).")
    return branch


def check_workflow(value: str) -> str:
    workflow = value.strip()
    if not WORKFLOW_PATTERN.match(workflow):
        raise ProjectValidationError("The workflow file is a file name under .github/workflows ending in .yml.")
    return workflow


def clean_protected_paths(values: list[str]) -> list[str]:
    """Repository-relative files or directories ("docs/legal/", "billing.py"); no wildcards."""
    paths: list[str] = []
    for raw in values:
        path = raw.strip()
        if path.startswith("./"):
            path = path[2:]
        if not path:
            continue
        if not PROTECTED_PATH_PATTERN.match(path) or ".." in path.split("/") or "//" in path:
            raise ProjectValidationError(
                f"Protected path {path[:60]!r} is not valid: use a repository-relative file or directory "
                "such as docs/legal/ or src/billing.py (no wildcards, no '..')."
            )
        if path not in paths:
            paths.append(path)
    if len(paths) > MAX_PROTECTED_PATHS:
        raise ProjectValidationError(f"At most {MAX_PROTECTED_PATHS} protected paths.")
    return paths


def check_token(token: SecretStr) -> str:
    value = token.get_secret_value().strip()
    if len(value) < 20 or len(value) > 400 or any(character.isspace() for character in value):
        # Never repeat the value.
        raise ProjectValidationError("That does not look like a GitHub token. Paste the whole fine-grained token.")
    return value


# -- stores -------------------------------------------------------------------------------


class ProjectStoreProtocol(Protocol):
    def list(self, context: TenantContext, *, query: str | None = None) -> list[DeveloperProject]: ...
    def get(self, project_id: str, context: TenantContext) -> DeveloperProject | None: ...
    def add(self, project: DeveloperProject) -> DeveloperProject: ...
    def save(self, project: DeveloperProject) -> DeveloperProject: ...
    def delete(self, project_id: str, context: TenantContext) -> bool: ...
    def count(self, context: TenantContext) -> int: ...


def _in_scope(project: DeveloperProject, context: TenantContext) -> bool:
    return (project.tenant_id, project.workspace_id) == (context.tenant_id, context.workspace_id)


def matches_query(project: DeveloperProject, query: str | None) -> bool:
    if not query:
        return True
    needle = query.lower()
    return any(needle in value.lower() for value in (project.name, project.repository, project.description))


class InMemoryProjectStore:
    def __init__(self) -> None:
        self._items: dict[str, DeveloperProject] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _conflict(self, project: DeveloperProject) -> None:
        for other in self._items.values():
            if other.id == project.id or (other.tenant_id, other.workspace_id) != (project.tenant_id, project.workspace_id):
                continue
            if other.name.lower() == project.name.lower():
                raise ProjectConflictError("name")
            if other.repository.lower() == project.repository.lower():
                raise ProjectConflictError("repository")

    def list(self, context: TenantContext, *, query: str | None = None) -> list[DeveloperProject]:
        with self._lock:
            items = [item for item in self._items.values() if _in_scope(item, context) and matches_query(item, query)]
        items.sort(key=lambda item: (item.name.lower(), item.id))
        return [item.model_copy(deep=True) for item in items]

    def get(self, project_id: str, context: TenantContext) -> DeveloperProject | None:
        with self._lock:
            found = self._items.get(project_id)
        return found.model_copy(deep=True) if found is not None and _in_scope(found, context) else None

    def add(self, project: DeveloperProject) -> DeveloperProject:
        with self._lock:
            self._conflict(project)
            self._items[project.id] = project.model_copy(deep=True)
        return project

    def save(self, project: DeveloperProject) -> DeveloperProject:
        with self._lock:
            if project.id not in self._items:
                raise KeyError(project.id)
            self._conflict(project)
            self._items[project.id] = project.model_copy(deep=True)
        return project

    def delete(self, project_id: str, context: TenantContext) -> bool:
        with self._lock:
            found = self._items.get(project_id)
            if found is None or not _in_scope(found, context):
                return False
            del self._items[project_id]
            return True

    def count(self, context: TenantContext) -> int:
        with self._lock:
            return sum(1 for item in self._items.values() if _in_scope(item, context))


project_store = InMemoryProjectStore()


@contextmanager
def open_project_store(context: TenantContext) -> Iterator[ProjectStoreProtocol]:
    """One unit of work in the caller's tenant and workspace scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.developer_repository import SqlAlchemyProjectStore

        return SqlAlchemyProjectStore(session, context)

    with open_scoped_store(context, project_store, sql_store) as store:
        yield store


# -- tokens and views ---------------------------------------------------------------------


def encrypt_token(token: SecretStr) -> str:
    from .secret_box import secret_cipher

    return secret_cipher().encrypt(check_token(token))


def project_token(project: DeveloperProject, environment_token: SecretStr | None) -> SecretStr | None:
    """The token GitHub calls for this project use, or None when none is configured."""
    if project.token_source == TokenSource.ENVIRONMENT:
        if environment_token is None or not environment_token.get_secret_value().strip():
            return None
        return environment_token
    if not project.token_ciphertext:
        return None
    from .secret_box import secret_cipher

    return SecretStr(secret_cipher().decrypt(project.token_ciphertext))


def token_configured(project: DeveloperProject, environment_token: SecretStr | None) -> bool:
    if project.token_source == TokenSource.ENVIRONMENT:
        return environment_token is not None and bool(environment_token.get_secret_value().strip())
    return bool(project.token_ciphertext)


def problems(project: DeveloperProject, environment_token: SecretStr | None) -> list[str]:
    """Why requests cannot go to this project now (empty when it is ready)."""
    found: list[str] = []
    if not project.enabled:
        found.append("The project is turned off. Turn it on to send requests to it.")
    if not token_configured(project, environment_token):
        found.append(
            "No GitHub token: add one for this project, or set ANUM_DEVELOPER_GITHUB_TOKEN in the API's environment."
            if project.token_source == TokenSource.ENVIRONMENT
            else "No GitHub token: add a fine-grained token for this repository."
        )
    if project.last_check is not None and not project.last_check.ok:
        found.append(project.last_check.message)
    return found


def project_view(project: DeveloperProject, environment_token: SecretStr | None) -> DeveloperProjectView:
    found = problems(project, environment_token)
    return DeveloperProjectView(
        id=project.id,
        name=project.name,
        github_owner=project.github_owner,
        github_repo=project.github_repo,
        repository=project.repository,
        default_branch=project.default_branch,
        description=project.description,
        enabled=project.enabled,
        protected_paths=list(project.protected_paths),
        workflow_file=project.workflow_file,
        token_source=project.token_source,
        token_configured=token_configured(project, environment_token),
        token_updated_at=project.token_updated_at,
        last_check=project.last_check,
        install_pr_number=project.install_pr_number,
        install_pr_url=project.install_pr_url,
        ready=not found,
        problems=found,
        created_by=project.created_by,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def project_ref(project: DeveloperProject) -> ProjectRef:
    return ProjectRef(id=project.id, name=project.name, repository=project.repository, description=project.description)


# -- routing a request to a project -------------------------------------------------------

_STOPWORDS = frozenset(
    """
    a an and the to for in on of with at by from into onto it its this that these those my our your
    add adds make makes build builds create fix change update show let lets please new page button
    app apps project projects repo repository code feature screen settings can could would should
    so be is are was were will do does not no yes all any some more less than then when where which who
    """.split()
)
_SUFFIX = r"(?:\s+(?:project|app|repo|repository))?"


def _words(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if len(word) >= 3 and word not in _STOPWORDS}


def _aliases(project: DeveloperProject) -> list[str]:
    names = [project.name, project.github_repo, project.github_repo.replace("-", " ").replace("_", " ")]
    seen: list[str] = []
    for name in names:
        if name.strip() and name.lower() not in (item.lower() for item in seen):
            seen.append(name.strip())
    return seen


def _name_match(text: str, project: DeveloperProject) -> tuple[int, str]:
    """(score, text without the "in <project>" phrase). 3: "in <name>" at the start or the end; 2: named."""
    for alias in _aliases(project):
        escaped = re.escape(alias)
        leading = re.compile(rf"^\s*(?:in|for|on|inside)\s+(?:the\s+)?{escaped}{_SUFFIX}\s*[,:;-]?\s+", re.IGNORECASE)
        trailing = re.compile(
            rf"[\s,;-]+(?:in|for|on|to|inside)\s+(?:the\s+)?{escaped}{_SUFFIX}\s*[.!?]*\s*$", re.IGNORECASE
        )
        for pattern in (leading, trailing):
            stripped = pattern.sub(" " if pattern is trailing else "", text, count=1)
            if stripped != text:
                return 3, " ".join(stripped.split()).strip(" ,;:-")
    for alias in _aliases(project):
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, re.IGNORECASE):
            return 2, text
    return 0, text


def resolve_project(text: str, projects: list[DeveloperProject]) -> Resolution:
    """Pick the project a request is for. ``projects`` are the ready ones (enabled, with a token)."""
    text = " ".join(text.split())
    if not projects:
        return Resolution(status="none", text=text)
    scored = [(project, *_name_match(text, project)) for project in projects]
    best = max(score for _, score, _ in scored)
    if best > 0:
        top = [(project, cleaned) for project, score, cleaned in scored if score == best]
        if len(top) == 1:
            project, cleaned = top[0]
            return Resolution(
                status="resolved", text=cleaned or text, project=project_ref(project), matched_by="name"
            )
        return Resolution(status="ambiguous", text=text, candidates=[project_ref(project) for project, _ in top])
    wanted = _words(text)
    overlaps = sorted(
        ((len(wanted & _words(f"{project.name} {project.description}")), project) for project in projects),
        key=lambda pair: -pair[0],
    )
    if overlaps[0][0] > 0 and (len(overlaps) == 1 or overlaps[0][0] > overlaps[1][0]):
        return Resolution(status="resolved", text=text, project=project_ref(overlaps[0][1]), matched_by="description")
    if len(projects) == 1:
        return Resolution(status="resolved", text=text, project=project_ref(projects[0]), matched_by="only_project")
    return Resolution(status="ambiguous", text=text, candidates=[project_ref(project) for project in projects])


# -- the coding workflow template -----------------------------------------------------------


@lru_cache(maxsize=1)
def workflow_template() -> str:
    """The self-contained coding workflow a target repository needs (Install workflow adds it)."""
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def install_pull_body(project: DeveloperProject) -> str:
    secrets = "\n".join(f"- `{name}`" for name in REPOSITORY_SECRETS)
    return (
        f"This adds `{project.workflow_path}`, the coding workflow ANUM developer mode dispatches requests to "
        f"for the project **{project.name}**.\n\n"
        "How it stays safe: the coding job runs the agent with a read-only `GITHUB_TOKEN` and cannot push; a "
        "separate publish job refuses protected paths (`.github/`, `.claude/`, `CLAUDE.md`, `.gitleaks.toml`, "
        "`.env` files and the project's own protected paths), pushes the agent's commits to "
        "`anum-dev/<request id>` and opens a **draft** pull request. Every action is pinned to a commit SHA. "
        "Nothing merges until an owner approves the merge in ANUM with the payload hash shown, and only while "
        "every CI check on the pull request passes.\n\n"
        "Before merging, add these repository secrets (Settings > Secrets and variables > Actions):\n\n"
        f"{secrets}\n\n"
        "`ANTHROPIC_API_KEY` is the key the coding agent uses; `ANUM_DEV_PUSH_TOKEN` is a fine-grained token for "
        "this repository only with Contents and Pull requests read and write. Optional repository variables: "
        "`ANUM_DEV_PROTECTED_PATHS` (comma-separated paths the developer must never change) and "
        "`ANUM_DEV_ALLOW_PROTECTED_PATHS=true` (lets a request the owner explicitly allowed change them).\n\n"
        "After merging, open ANUM > Developer > Projects and press Test connection."
    )


# -- GitHub checks --------------------------------------------------------------------------


async def check_project(client: GitHubClient, project: DeveloperProject) -> ProjectCheck:
    """Test connection: the repository is reachable with the token, the branch exists, the
    workflow file is on it and the token can read Actions (needed to follow runs)."""
    now = utc_now()
    try:
        info = await client.repository_info()
    except GitHubError as exc:
        if exc.status == 401:
            message = "GitHub refused the token (401): it expired or was revoked. Replace it."
        elif exc.status in {403, 404}:
            message = (
                f"GitHub cannot find {project.repository} with this token. Check the owner and repository name, "
                "and that the token covers this repository."
            )
        else:
            message = f"{project.repository} could not be checked: {exc.message}"
        return ProjectCheck(ok=False, checked_at=now, message=message)
    try:
        branch = await client.branch_sha(project.default_branch)
        workflow = await client.file_sha(project.workflow_path, project.default_branch) if branch else None
        actions = await client.workflow_readable() if workflow else False
    except GitHubError as exc:
        return ProjectCheck(
            ok=False,
            checked_at=now,
            repository_reachable=True,
            github_default_branch=info.default_branch or None,
            message=f"{project.repository} could not be checked: {exc.message}",
        )
    base = dict(
        checked_at=now,
        repository_reachable=True,
        default_branch_exists=branch is not None,
        workflow_present=workflow is not None,
        actions_readable=actions,
        github_default_branch=info.default_branch or None,
    )
    if branch is None:
        hint = f" GitHub's default branch is {info.default_branch}." if info.default_branch else ""
        return ProjectCheck(ok=False, message=f"The branch {project.default_branch} does not exist in {project.repository}.{hint}", **base)
    if workflow is None:
        return ProjectCheck(
            ok=False,
            message=(
                f"{project.workflow_path} is not on {project.default_branch} yet. Use Install workflow, merge that "
                "pull request in GitHub, then test again."
            ),
            **base,
        )
    if not actions:
        return ProjectCheck(
            ok=False,
            message="The token cannot read GitHub Actions for this repository. Give it Actions: read and write.",
            **base,
        )
    return ProjectCheck(
        ok=True,
        message=f"Ready: {project.repository} is reachable and has {project.workflow_path} on {project.default_branch}.",
        **base,
    )


async def install_workflow(client: GitHubClient, project: DeveloperProject) -> tuple[PullRequest, bool]:
    """Open (or find) the pull request that adds the workflow to the project's branch.

    Returns the pull request and whether this call created it. The owner reviews and merges
    it in GitHub; nothing here merges. Needs the token's Contents, Pull requests and
    Workflows write permissions.
    """
    existing = await client.open_pull(INSTALL_BRANCH)
    if existing is not None:
        return existing, False
    base_sha = await client.branch_sha(project.default_branch)
    if base_sha is None:
        raise GitHubError(404, f"The branch {project.default_branch} does not exist in {project.repository}.")
    if await client.branch_sha(INSTALL_BRANCH) is None:
        await client.create_branch(INSTALL_BRANCH, base_sha)
    current = await client.file_sha(project.workflow_path, INSTALL_BRANCH)
    await client.put_file(
        project.workflow_path,
        workflow_template(),
        message="Add the ANUM developer workflow",
        branch=INSTALL_BRANCH,
        sha=current,
    )
    pull = await client.create_pull(
        title="Add the ANUM developer workflow",
        head=INSTALL_BRANCH,
        base=project.default_branch,
        body=install_pull_body(project),
    )
    return pull, True
