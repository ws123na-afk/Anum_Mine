"""Developer mode (docs/developer-mode.md) against a fake GitHub (httpx.MockTransport).

Requests go to projects (one GitHub repository each); the fake serves several
repositories, each with its own token.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import SecretStr

from anum_api import developer, main
from anum_api.authorization import ROLE_PERMISSIONS, Permission, Role
from anum_api.developer import (
    DeveloperAdmissionError,
    DeveloperRequest,
    DevRequestSource,
    DevRequestStatus,
    InMemoryDeveloperStore,
    derive_status,
    developer_settings,
    developer_store,
    mentions_protected,
    sync,
    timeline,
)
from anum_api.developer_github import ChecksState, PullRequest, WorkflowRun, run_title
from anum_api.developer_projects import (
    INSTALL_BRANCH,
    DeveloperProject,
    ProjectValidationError,
    TokenSource,
    clean_protected_paths,
    project_store,
    resolve_project,
    workflow_template,
)
from anum_api.model_budget import BudgetScope, ModelBudgetLimits, memory_model_budget_store
from anum_api.schemas import TenantContext, utc_now
from anum_api.store import store

REPO = "acme/anum"
OTHER_REPO = "acme/tyrepulse"
# Plainly fake, low-entropy token values (gitleaks scans test fixtures).
PAT = "example-fine-grained-value"
OTHER_PAT = "example-other-repository-value"
ENV_PAT = "example-environment-token-value"
TENANT = "tenant_dev"
WORKSPACE = "workspace_dev"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_dev", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_dev", "x-user-roles": "member"}
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_dev", roles=["owner"])
ROOT = Path(__file__).parents[3]
WORKFLOW_PATH = ".github/workflows/anum-developer.yml"

client = TestClient(main.app)


class FakeGitHub:
    """Just enough of the GitHub REST and GraphQL APIs for one repository."""

    def __init__(self, repo: str = REPO, token: str = PAT) -> None:
        self.repo = repo
        self.token = token
        self.dispatches: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self.jobs: dict[int, list[dict[str, Any]]] = {}
        self.pulls: dict[str, dict[str, Any]] = {}
        self.ci: dict[str, list[dict[str, Any]]] = {}
        self.merges: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.ready_for_review: list[str] = []
        self.cancelled: list[int] = []
        self.deleted: list[str] = []
        self.fail_dispatch = False
        self.default_branch = "main"
        self.branches: dict[str, str] = {"main": "b" * 40}
        # (path, ref) -> blob sha; the workflow is installed on main unless a test removes it.
        self.files: dict[tuple[str, str], str] = {(WORKFLOW_PATH, "main"): "w" * 40}
        self.written: list[dict[str, Any]] = []
        self.repo_status = 200
        self.actions_status = 200
        self.write_status = 201

    # helpers for tests
    def start_run(self, request_id: str, status: str = "in_progress", conclusion: str | None = None) -> dict[str, Any]:
        run = {
            "id": 1000 + len(self.runs),
            "display_title": run_title(request_id),
            "status": status,
            "conclusion": conclusion,
            "html_url": f"https://github.com/{self.repo}/actions/runs/{1000 + len(self.runs)}",
            "created_at": "2026-10-11T10:00:00Z",
        }
        self.runs.insert(0, run)
        return run

    def open_pull(self, request_id: str, sha: str = "a" * 40, number: int = 7) -> dict[str, Any]:
        pull = {
            "number": number,
            "html_url": f"https://github.com/{self.repo}/pull/{number}",
            "title": "Add a dark/light toggle to settings",
            "state": "open",
            "merged_at": None,
            "draft": True,
            "node_id": f"PR_node_{self.repo}",
            "head": {"sha": sha},
        }
        self.pulls[f"anum-dev/{request_id}"] = pull
        return pull

    def set_ci(self, sha: str, *conclusions: str | None) -> None:
        self.ci[sha] = [
            {
                "id": index,
                "name": f"CI {index}",
                "path": f".github/workflows/ci{index}.yml",
                "status": "completed" if conclusion else "in_progress",
                "conclusion": conclusion,
                "created_at": "2026-10-11T10:05:00Z",
            }
            for index, conclusion in enumerate(conclusions)
        ]

    def _pull_by_number(self, number: int) -> dict[str, Any] | None:
        return next((pull for pull in self.pulls.values() if pull["number"] == number), None)

    def graphql(self, request: httpx.Request) -> httpx.Response:
        self.ready_for_review.append(json.loads(request.content)["variables"]["id"])
        for pull in self.pulls.values():
            pull["draft"] = False
        return httpx.Response(200, json={"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": False}}}})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] != f"Bearer {self.token}":
            return httpx.Response(401, json={"message": "Bad credentials"})
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if request.method == "POST" and path == "/graphql":
            return self.graphql(request)
        prefix = f"/repos/{self.repo}"
        assert path.startswith(prefix), path
        rest = path[len(prefix):]
        if request.method == "GET" and rest == "":
            if self.repo_status != 200:
                return httpx.Response(self.repo_status, json={"message": "Not Found"})
            return httpx.Response(
                200, json={"full_name": self.repo, "default_branch": self.default_branch, "private": True, "html_url": f"https://github.com/{self.repo}"}
            )
        if request.method == "POST" and rest == "/dispatches":
            if self.fail_dispatch:
                return httpx.Response(404, json={"message": "Not Found"})
            self.dispatches.append(json.loads(request.content))
            return httpx.Response(204)
        if request.method == "GET" and rest.startswith("/git/ref/heads/"):
            sha = self.branches.get(rest[len("/git/ref/heads/"):])
            return httpx.Response(200, json={"object": {"sha": sha}}) if sha else httpx.Response(404, json={"message": "Not Found"})
        if request.method == "POST" and rest == "/git/refs":
            body = json.loads(request.content)
            if self.write_status >= 400:
                return httpx.Response(self.write_status, json={"message": "Resource not accessible by personal access token"})
            self.branches[body["ref"][len("refs/heads/"):]] = body["sha"]
            return httpx.Response(201, json={"ref": body["ref"]})
        if rest.startswith("/contents/"):
            file_path = rest[len("/contents/"):]
            if request.method == "GET":
                sha = self.files.get((file_path, request.url.params.get("ref", self.default_branch)))
                return httpx.Response(200, json={"sha": sha}) if sha else httpx.Response(404, json={"message": "Not Found"})
            body = json.loads(request.content)
            self.written.append({"path": file_path, **body})
            self.files[(file_path, body["branch"])] = "n" * 40
            return httpx.Response(201, json={"content": {"sha": "n" * 40}})
        if request.method == "GET" and rest.startswith("/actions/workflows/") and not rest.endswith("/runs"):
            if self.actions_status != 200:
                return httpx.Response(self.actions_status, json={"message": "Resource not accessible"})
            return httpx.Response(200, json={"id": 1, "path": WORKFLOW_PATH})
        if request.method == "GET" and rest == "/actions/workflows/anum-developer.yml/runs":
            return httpx.Response(200, json={"workflow_runs": self.runs})
        if request.method == "GET" and rest.startswith("/actions/runs/") and rest.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": self.jobs.get(int(rest.split("/")[3]), [])})
        if request.method == "POST" and rest.startswith("/actions/runs/") and rest.endswith("/cancel"):
            self.cancelled.append(int(rest.split("/")[3]))
            return httpx.Response(202)
        if request.method == "GET" and rest == "/actions/runs":
            return httpx.Response(200, json={"workflow_runs": self.ci.get(request.url.params["head_sha"], [])})
        if request.method == "GET" and rest.startswith("/commits/"):
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET" and rest == "/pulls":
            branch = request.url.params["head"].split(":", 1)[1]
            pull = self.pulls.get(branch)
            if pull and request.url.params.get("state") == "open" and pull["state"] != "open":
                pull = None
            return httpx.Response(200, json=[pull] if pull else [])
        if request.method == "POST" and rest == "/pulls":
            body = json.loads(request.content)
            number = 50 + len(self.pulls)
            pull = {
                "number": number, "html_url": f"https://github.com/{self.repo}/pull/{number}", "title": body["title"],
                "state": "open", "merged_at": None, "draft": body["draft"], "node_id": f"PR_{number}",
                "head": {"sha": self.branches.get(body["head"], "")}, "body": body["body"], "base": body["base"],
            }
            self.pulls[body["head"]] = pull
            return httpx.Response(201, json=pull)
        if rest.startswith("/pulls/") and rest.endswith("/merge"):
            pull = self._pull_by_number(int(rest.split("/")[2]))
            body = json.loads(request.content)
            assert pull is not None
            if pull["draft"]:
                return httpx.Response(405, json={"message": "Pull Request is still a draft"})
            if body["sha"] != pull["head"]["sha"]:
                return httpx.Response(409, json={"message": "Head branch was modified"})
            self.merges.append(body)
            pull["merged_at"], pull["state"] = "2026-10-11T11:00:00Z", "closed"
            return httpx.Response(200, json={"sha": "m" * 40, "merged": True})
        if rest.startswith("/pulls/"):
            pull = self._pull_by_number(int(rest.split("/")[2]))
            if pull is None:
                return httpx.Response(404, json={"message": "Not Found"})
            if request.method == "PATCH":
                pull["state"] = json.loads(request.content)["state"]
            return httpx.Response(200, json={**pull, "merged": bool(pull["merged_at"])})
        if request.method == "DELETE" and rest.startswith("/git/refs/heads/"):
            self.deleted.append(rest[len("/git/refs/heads/"):])
            return httpx.Response(204)
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


class GitHubHub:
    """Routes each call to the fake of the repository it names (one transport, many repositories)."""

    def __init__(self, *fakes: FakeGitHub) -> None:
        self.fakes = {fake.repo: fake for fake in fakes}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/graphql":
            node = json.loads(request.content)["variables"]["id"]
            fake = next(fake for fake in self.fakes.values() if any(pull["node_id"] == node for pull in fake.pulls.values()))
            return fake(request)
        parts = path.split("/")
        repo = "/".join(parts[2:4]) if len(parts) >= 4 and parts[1] == "repos" else ""
        fake = self.fakes.get(repo)
        if fake is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return fake(request)


class SlowDispatch(httpx.AsyncBaseTransport):
    """FakeGitHub behind a transport that yields on the dispatch call, as a real network
    call does: concurrent creates interleave exactly where the admission race used to be."""

    def __init__(self, fake: Any, delay: float = 0.05) -> None:
        self.fake = fake
        self.delay = delay

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        if request.method == "POST" and request.url.path.endswith("/dispatches"):
            await asyncio.sleep(self.delay)
        return self.fake(request)


async def create_concurrently(
    app: Any, headers: dict[str, str], texts: list[str], project_id: str | None = None
) -> list[httpx.Response]:
    """POST several developer requests at once on one event loop (as one API process does)."""
    extra = {"project_id": project_id} if project_id else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://anum.test") as http:
        return list(
            await asyncio.gather(
                *(http.post("/api/v1/developer/requests", headers=headers, json={"text": text, **extra}) for text in texts)
            )
        )


def add_project(
    name: str = "ANUM", repo: str = REPO, token: str | None = PAT, headers: dict[str, str] = OWNER, **extra: Any
) -> httpx.Response:
    owner, repo_name = repo.split("/")
    body: dict[str, Any] = {"name": name, "github_owner": owner, "github_repo": repo_name, **extra}
    if token is not None:
        body["token"] = token
    return client.post("/api/v1/developer/projects", headers=headers, json=body)


@pytest.fixture(autouse=True)
def github(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGitHub]:
    fake = FakeGitHub()
    other = FakeGitHub(OTHER_REPO, OTHER_PAT)
    hub = GitHubHub(fake, other)
    fake.hub = hub  # type: ignore[attr-defined]
    fake.other = other  # type: ignore[attr-defined]
    monkeypatch.setattr(developer, "github_transport", httpx.MockTransport(hub))
    monkeypatch.setattr(developer_settings, "github_repo", "")
    monkeypatch.setattr(developer_settings, "github_token", None)
    monkeypatch.setattr(developer_settings, "tenant_id", TENANT)
    monkeypatch.setattr(developer_settings, "workspace_id", "")
    monkeypatch.setattr(developer_settings, "status_cache_seconds", 0.0)
    monkeypatch.setattr(developer_settings, "max_requests_per_day", 10)
    monkeypatch.setattr(developer_settings, "max_projects", 100)
    developer_store.clear()
    project_store.clear()
    memory_model_budget_store.clear()
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
        collection.clear()
    yield fake
    developer_store.clear()
    project_store.clear()


@pytest.fixture
def project(github: FakeGitHub) -> dict[str, Any]:
    created = add_project()
    assert created.status_code == 201, created.text
    return created.json()


@pytest.fixture
def other_project(github: FakeGitHub) -> dict[str, Any]:
    created = add_project("TyrePulse", OTHER_REPO, OTHER_PAT, description="Tyre pressure and tread monitoring for fleets")
    assert created.status_code == 201, created.text
    return created.json()


def _create(text: str = "Add a dark/light toggle to settings", **extra: Any) -> httpx.Response:
    return client.post("/api/v1/developer/requests", headers=OWNER, json={"text": text, **extra})


def _audit_actions() -> list[str]:
    return [record.action for record in store.audit_records]


def _ready(github: FakeGitHub) -> dict[str, Any]:
    """A request whose pull request is open with green CI."""
    created = _create().json()
    github.start_run(created["id"], status="completed", conclusion="success")
    github.open_pull(created["id"])
    github.set_ci("a" * 40, "success", "skipped")
    return client.get(f"/api/v1/developer/requests/{created['id']}", headers=OWNER).json()


# -- permissions and setup ------------------------------------------------------------------


def test_developer_permission_is_owner_only() -> None:
    assert Permission.DEVELOPER_MANAGE in ROLE_PERMISSIONS[Role.OWNER]
    assert Permission.DEVELOPER_MANAGE not in ROLE_PERMISSIONS[Role.MEMBER]
    assert Permission.DEVELOPER_MANAGE not in ROLE_PERMISSIONS[Role.VIEWER]
    body = {"text": "Add a toggle please", "name": "x", "github_owner": "acme", "github_repo": "x"}
    for method, path in (
        ("get", "/api/v1/developer/setup"),
        ("get", "/api/v1/developer/requests"),
        ("post", "/api/v1/developer/requests"),
        ("post", "/api/v1/developer/requests/dev_x/merge-approval"),
        ("post", "/api/v1/developer/requests/dev_x/cancel"),
        ("get", "/api/v1/developer/projects"),
        ("post", "/api/v1/developer/projects"),
        ("get", "/api/v1/developer/projects/devproj_x"),
        ("patch", "/api/v1/developer/projects/devproj_x"),
        ("delete", "/api/v1/developer/projects/devproj_x"),
        ("post", "/api/v1/developer/projects/devproj_x/test"),
        ("post", "/api/v1/developer/projects/devproj_x/install-workflow"),
        ("get", "/api/v1/developer/workflow-template"),
        ("post", "/api/v1/developer/resolve"),
    ):
        kwargs = {"json": body} if method in {"post", "patch"} else {}
        response = client.request(method.upper(), path, headers=MEMBER, **kwargs)
        assert response.status_code == 403, path


def test_setup_reports_missing_settings_and_projects_but_never_a_token(
    monkeypatch: pytest.MonkeyPatch, project: dict[str, Any]
) -> None:
    ready = client.get("/api/v1/developer/setup", headers=OWNER).json()
    assert ready["configured"] and ready["enabled_here"]
    assert (ready["projects"], ready["ready_projects"], ready["repository"]) == (1, 1, REPO)
    assert ready["environment_token_configured"] is False
    assert PAT not in json.dumps(ready)
    monkeypatch.setattr(developer_settings, "tenant_id", "")
    monkeypatch.setattr(developer_settings, "github_repo", "not a repo")
    missing = client.get("/api/v1/developer/setup", headers=OWNER).json()
    assert not missing["configured"]
    assert missing["missing"] == ["ANUM_DEVELOPER_GITHUB_REPO", "ANUM_DEVELOPER_TENANT_ID"]
    refused = _create()
    assert refused.status_code == 409 and "ANUM_DEVELOPER_TENANT_ID" in refused.json()["error"]["message"]


def test_setup_without_projects_asks_for_one_and_names_the_old_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    empty = client.get("/api/v1/developer/setup", headers=OWNER).json()
    assert empty["configured"] and empty["projects"] == 0 and "Add a project" in empty["message"]
    monkeypatch.setattr(developer_settings, "github_repo", REPO)
    legacy = client.get("/api/v1/developer/setup", headers=OWNER).json()
    assert REPO in legacy["message"]
    refused = _create()
    assert refused.status_code == 422 and "Add a project" in refused.json()["error"]["message"]


def test_other_tenants_cannot_use_developer_mode(project: dict[str, Any]) -> None:
    other = {**OWNER, "x-tenant-id": "tenant_other"}
    assert client.get("/api/v1/developer/setup", headers=other).json()["enabled_here"] is False
    assert client.post("/api/v1/developer/requests", headers=other, json={"text": "Add a dark mode toggle"}).status_code == 403
    assert client.get("/api/v1/developer/projects", headers=other).status_code == 403
    assert add_project("Mine", "other/repo", headers=other).status_code == 403


# -- projects -------------------------------------------------------------------------------


def test_projects_are_created_listed_updated_and_deleted_with_audit(github: FakeGitHub) -> None:
    created = add_project(
        "TyrePulse", OTHER_REPO, OTHER_PAT, default_branch="develop", description="Tyre monitoring for fleets",
        protected_paths=["docs/legal/", "./billing.py", "billing.py"],
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["repository"] == OTHER_REPO and body["default_branch"] == "develop"
    assert body["protected_paths"] == ["docs/legal/", "billing.py"]
    assert body["token_configured"] is True and body["ready"] is True and body["problems"] == []
    assert "token" not in body and "token_ciphertext" not in body and OTHER_PAT not in created.text
    stored = project_store.get(body["id"], CONTEXT)
    assert stored is not None and stored.token_ciphertext and OTHER_PAT not in stored.token_ciphertext

    add_project("ANUM", REPO)
    listed = client.get("/api/v1/developer/projects", headers=OWNER).json()
    assert [item["name"] for item in listed] == ["ANUM", "TyrePulse"]
    assert [item["name"] for item in client.get("/api/v1/developer/projects?q=fleets", headers=OWNER).json()] == ["TyrePulse"]

    updated = client.patch(
        f"/api/v1/developer/projects/{body['id']}", headers=OWNER, json={"enabled": False, "description": "Fleet tyres"}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["enabled"] is False and updated.json()["ready"] is False
    assert "turned off" in updated.json()["problems"][0]

    cleared = client.patch(f"/api/v1/developer/projects/{body['id']}", headers=OWNER, json={"clear_token": True}).json()
    assert cleared["token_configured"] is False
    replaced = client.patch(f"/api/v1/developer/projects/{body['id']}", headers=OWNER, json={"token": OTHER_PAT, "enabled": True}).json()
    assert replaced["token_configured"] is True and replaced["ready"] is True

    assert client.delete(f"/api/v1/developer/projects/{body['id']}", headers=OWNER).status_code == 204
    assert client.get(f"/api/v1/developer/projects/{body['id']}", headers=OWNER).status_code == 404
    actions = _audit_actions()
    assert actions.count("developer.project.created") == 2
    assert "developer.project.updated" in actions and "developer.project.deleted" in actions
    for record in store.audit_records:
        assert OTHER_PAT not in json.dumps(dict(record.metadata), default=str)


def test_project_validation_and_conflicts(project: dict[str, Any]) -> None:
    for bad in (
        {"github_owner": "-acme"},
        {"github_repo": "a/b"},
        {"default_branch": "main..x"},
        {"workflow_file": "../ci.yml"},
        {"protected_paths": ["../etc/passwd"]},
        {"protected_paths": ["src/*.py"]},
        {"token": "short"},
    ):
        response = add_project("Another", "acme/another", **{k: v for k, v in bad.items() if k != "token"}, token=bad.get("token", PAT))
        assert response.status_code == 422, bad
        assert "short" not in response.text or "token" in response.text
    assert add_project("anum", "acme/else").status_code == 409  # same name, any case
    clash = add_project("Else", "ACME/Anum.git")
    assert clash.status_code == 409 and "repository" in clash.json()["error"]["message"]
    assert add_project("Else", "acme/else").status_code == 201
    renamed = client.patch(f"/api/v1/developer/projects/{project['id']}", headers=OWNER, json={"name": "Else"})
    assert renamed.status_code == 409


def test_project_limit(monkeypatch: pytest.MonkeyPatch, project: dict[str, Any]) -> None:
    monkeypatch.setattr(developer_settings, "max_projects", 1)
    refused = add_project("Second", "acme/second")
    assert refused.status_code == 409 and "1 projects" in refused.json()["error"]["message"]


def test_protected_paths_cleaning() -> None:
    assert clean_protected_paths([" docs/legal/ ", "./src/billing.py", ".github/", ""]) == ["docs/legal/", "src/billing.py", ".github/"]
    with pytest.raises(ProjectValidationError):
        clean_protected_paths(["docs/../secrets"])
    with pytest.raises(ProjectValidationError):
        clean_protected_paths(["/etc"])


def test_environment_token_covers_selected_repositories(monkeypatch: pytest.MonkeyPatch, github: FakeGitHub) -> None:
    created = add_project("ANUM", REPO, token=None, token_source="environment").json()
    assert created["token_configured"] is False and "ANUM_DEVELOPER_GITHUB_TOKEN" in created["problems"][0]
    assert _create().status_code == 422  # nothing sendable yet
    github.token = ENV_PAT
    monkeypatch.setattr(developer_settings, "github_token", SecretStr(ENV_PAT))
    assert client.get(f"/api/v1/developer/projects/{created['id']}", headers=OWNER).json()["ready"] is True
    assert _create().status_code == 201
    assert client.get("/api/v1/developer/setup", headers=OWNER).json()["environment_token_configured"] is True


def test_test_connection_reports_what_is_missing(github: FakeGitHub, project: dict[str, Any]) -> None:
    url = f"/api/v1/developer/projects/{project['id']}/test"
    ok = client.post(url, headers=OWNER).json()
    assert ok["last_check"]["ok"] is True and ok["ready"] is True
    assert ok["last_check"]["workflow_present"] and ok["last_check"]["actions_readable"]

    del github.files[(WORKFLOW_PATH, "main")]
    missing = client.post(url, headers=OWNER).json()
    assert missing["last_check"]["ok"] is False and missing["last_check"]["workflow_present"] is False
    assert "Install workflow" in missing["last_check"]["message"] and missing["ready"] is False

    github.files[(WORKFLOW_PATH, "main")] = "w" * 40
    github.actions_status = 403
    assert "Actions" in client.post(url, headers=OWNER).json()["last_check"]["message"]
    github.actions_status = 200

    github.branches.pop("main")
    github.default_branch = "trunk"
    branch = client.post(url, headers=OWNER).json()["last_check"]
    assert branch["ok"] is False and "trunk" in branch["message"]

    github.repo_status = 404
    gone = client.post(url, headers=OWNER).json()["last_check"]
    assert gone["ok"] is False and "cannot find" in gone["message"]

    github.repo_status = 200
    github.token = "example-rotated-value"
    refused = client.post(url, headers=OWNER).json()["last_check"]
    assert "401" in refused["message"]
    assert "developer.project.tested" in _audit_actions()


def test_install_workflow_opens_one_pull_request_with_the_template(github: FakeGitHub, project: dict[str, Any]) -> None:
    del github.files[(WORKFLOW_PATH, "main")]
    url = f"/api/v1/developer/projects/{project['id']}/install-workflow"
    first = client.post(url, headers=OWNER)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["created"] is True and result["pull_request_url"].startswith(f"https://github.com/{REPO}/pull/")
    assert result["project"]["install_pr_url"] == result["pull_request_url"]
    assert github.branches[INSTALL_BRANCH] == github.branches["main"]
    [written] = github.written
    assert written["path"] == WORKFLOW_PATH and written["branch"] == INSTALL_BRANCH
    assert base64.b64decode(written["content"]).decode() == workflow_template()
    pull = github.pulls[INSTALL_BRANCH]
    assert pull["base"] == "main" and pull["draft"] is False
    assert "ANTHROPIC_API_KEY" in pull["body"] and "ANUM_DEV_PUSH_TOKEN" in pull["body"]
    # Asking again returns the same open pull request.
    again = client.post(url, headers=OWNER).json()
    assert again["created"] is False and again["pull_request_number"] == result["pull_request_number"]
    assert len(github.written) == 1
    assert "developer.project.workflow_install_requested" in _audit_actions()

    github.files[(WORKFLOW_PATH, "main")] = "w" * 40
    installed = client.post(url, headers=OWNER)
    assert installed.status_code == 409 and "already" in installed.json()["error"]["message"]


def test_install_workflow_explains_missing_permissions(github: FakeGitHub, project: dict[str, Any]) -> None:
    del github.files[(WORKFLOW_PATH, "main")]
    github.write_status = 403
    refused = client.post(f"/api/v1/developer/projects/{project['id']}/install-workflow", headers=OWNER)
    assert refused.status_code == 502
    assert "Workflows" in refused.json()["error"]["message"] and PAT not in refused.text
    assert "developer.project.workflow_install_failed" in _audit_actions()


def test_workflow_template_route(project: dict[str, Any]) -> None:
    body = client.get("/api/v1/developer/workflow-template", headers=OWNER).json()
    assert body["path"] == WORKFLOW_PATH and body["content"] == workflow_template()
    assert body["secrets"] == ["ANTHROPIC_API_KEY", "ANUM_DEV_PUSH_TOKEN"]


# -- routing requests to projects -------------------------------------------------------------


def _project(name: str, repo: str, description: str = "") -> DeveloperProject:
    now = utc_now()
    owner, repo_name = repo.split("/")
    return DeveloperProject(
        id=f"devproj_{name.lower()}", tenant_id=TENANT, workspace_id=WORKSPACE, name=name, github_owner=owner,
        github_repo=repo_name, description=description, created_by="o", created_at=now, updated_at=now,
    )


def test_resolve_project_by_name_description_or_the_only_one() -> None:
    anum = _project("ANUM", REPO, "The ANUM agent platform: web, API and mobile apps")
    tyre = _project("TyrePulse", OTHER_REPO, "Tyre pressure and tread monitoring for fleets")
    both = [anum, tyre]
    trailing = resolve_project("a login page in TyrePulse", both)
    assert (trailing.status, trailing.project and trailing.project.name, trailing.text, trailing.matched_by) == (
        "resolved", "TyrePulse", "a login page", "name",
    )
    leading = resolve_project("in tyrepulse, add a fleet export button", both)
    assert leading.project and leading.project.name == "TyrePulse" and leading.text == "add a fleet export button"
    leading_no_comma = resolve_project("in the TyrePulse app add a fleet export", both)
    assert leading_no_comma.project and leading_no_comma.text == "add a fleet export"
    named = resolve_project("add dark mode to ANUM settings", both)
    assert named.project and named.project.name == "ANUM" and named.text == "add dark mode to ANUM settings"
    described = resolve_project("show tread depth warnings on the dashboard", both)
    assert described.status == "resolved" and described.project and described.project.name == "TyrePulse"
    assert described.matched_by == "description"
    unclear = resolve_project("add a dark/light toggle", both)
    assert unclear.status == "ambiguous" and [item.name for item in unclear.candidates] == ["ANUM", "TyrePulse"]
    only = resolve_project("add a dark/light toggle", [anum])
    assert only.status == "resolved" and only.matched_by == "only_project"
    assert resolve_project("add a toggle", []).status == "none"


def test_resolve_route_uses_only_sendable_projects(project: dict[str, Any], other_project: dict[str, Any]) -> None:
    found = client.post("/api/v1/developer/resolve", headers=OWNER, json={"text": "a login page in TyrePulse"}).json()
    assert found["status"] == "resolved" and found["project"]["id"] == other_project["id"] and found["text"] == "a login page"
    client.patch(f"/api/v1/developer/projects/{other_project['id']}", headers=OWNER, json={"enabled": False})
    only = client.post("/api/v1/developer/resolve", headers=OWNER, json={"text": "add a dark/light toggle"}).json()
    assert only["status"] == "resolved" and only["project"]["id"] == project["id"]
    assert store.audit_records and not [r for r in store.audit_records if r.action.startswith("developer.request")]


# -- requests ---------------------------------------------------------------------------------


def test_create_dispatches_to_the_project_audits_and_counts_one_budgeted_action(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    created_project = add_project("ANUM", REPO, default_branch="develop", protected_paths=["docs/legal/"]).json()
    github.branches["develop"] = "c" * 40
    response = _create(source="voice")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued" and body["source"] == "voice"
    assert (body["project_id"], body["project_name"], body["repository"]) == (created_project["id"], "ANUM", REPO)
    assert body["branch"] == f"anum-dev/{body['id']}"
    assert [stage["state"] for stage in body["timeline"]] == ["active", "pending", "pending", "pending", "pending", "pending"]
    assert github.dispatches == [
        {
            "event_type": "anum-dev-request",
            "client_payload": {
                "request_id": body["id"],
                "text": "Add a dark/light toggle to settings",
                "source": "voice",
                "allow_protected_paths": False,
                "base_branch": "develop",
                "protected_paths": ["docs/legal/"],
            },
        }
    ]
    assert "developer.request.created" in _audit_actions()
    usage = memory_model_budget_store.usage(CONTEXT, BudgetScope.WORKSPACE, utc_now().date().replace(day=1))
    assert usage.calls == 1 and usage.estimated_cost_usd == pytest.approx(developer_settings.request_cost_usd)


def test_requests_need_a_project_choice_when_several_are_ready(
    github: FakeGitHub, project: dict[str, Any], other_project: dict[str, Any]
) -> None:
    unclear = _create()
    assert unclear.status_code == 422 and "ANUM, TyrePulse" in unclear.json()["error"]["message"]
    created = _create("Add a tread depth chart to the fleet view", project_id=other_project["id"])
    assert created.status_code == 201, created.text
    assert github.other.dispatches and not github.dispatches  # type: ignore[attr-defined]
    assert _create(project_id="devproj_missing").status_code == 422


def test_turned_off_project_and_failed_check_refuse_requests(github: FakeGitHub, project: dict[str, Any]) -> None:
    client.patch(f"/api/v1/developer/projects/{project['id']}", headers=OWNER, json={"enabled": False})
    off = _create(project_id=project["id"])
    assert off.status_code == 409 and "turned off" in off.json()["error"]["message"]
    client.patch(f"/api/v1/developer/projects/{project['id']}", headers=OWNER, json={"enabled": True})
    del github.files[(WORKFLOW_PATH, "main")]
    client.post(f"/api/v1/developer/projects/{project['id']}/test", headers=OWNER)
    refused = _create(project_id=project["id"])
    assert refused.status_code == 409 and "not ready" in refused.json()["error"]["message"]
    assert github.dispatches == []
    # Fixed in GitHub since: sending checks again and goes through.
    github.files[(WORKFLOW_PATH, "main")] = "w" * 40
    assert _create(project_id=project["id"]).status_code == 201


def test_requests_are_validated_and_protected_topics_refused(github: FakeGitHub, project: dict[str, Any]) -> None:
    assert _create("short").status_code == 422
    refused = _create("Rotate the GitHub Actions secrets used by deploys")
    assert refused.status_code == 422 and "explicitly" in refused.json()["error"]["message"]
    assert "developer.request.refused" in _audit_actions()
    assert github.dispatches == []
    allowed = _create("Rotate the GitHub Actions secrets used by deploys", allow_protected_paths=True)
    assert allowed.status_code == 201
    assert github.dispatches[0]["client_payload"]["allow_protected_paths"] is True


def test_one_active_request_per_project_and_a_daily_cap(
    github: FakeGitHub, project: dict[str, Any], other_project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _create(project_id=project["id"]).json()
    busy = _create("Add a second unrelated change", project_id=project["id"])
    assert busy.status_code == 409 and "ANUM" in busy.json()["error"]["message"]
    # Another project is not blocked by this one.
    assert _create("Add a tread depth chart", project_id=other_project["id"]).status_code == 201
    client.post(f"/api/v1/developer/requests/{first['id']}/cancel", headers=OWNER)
    monkeypatch.setattr(developer_settings, "max_requests_per_day", 2)
    capped = _create("Add a second unrelated change", project_id=project["id"])
    assert capped.status_code == 429 and "a day" in capped.json()["error"]["message"]


def test_budget_exhausted_refuses_before_dispatch(github: FakeGitHub, project: dict[str, Any]) -> None:
    memory_model_budget_store.set_budget(CONTEXT, BudgetScope.WORKSPACE, ModelBudgetLimits(monthly_cost_limit_usd=0), utc_now())
    response = _create()
    assert response.status_code == 402
    assert github.dispatches == []


def test_dispatch_failure_marks_the_request_failed_so_it_blocks_nothing(github: FakeGitHub, project: dict[str, Any]) -> None:
    github.fail_dispatch = True
    response = _create()
    assert response.status_code == 502
    message = response.json()["error"]["message"]
    assert "GitHub answered 404" in message and PAT not in message
    [stored] = client.get("/api/v1/developer/requests", headers=OWNER).json()
    assert stored["status"] == "failed" and stored["failure_reason"].startswith("Not sent to GitHub:")
    assert PAT not in stored["failure_reason"]
    assert "developer.request.dispatch_failed" in _audit_actions()
    assert "developer.request.created" not in _audit_actions()
    usage = memory_model_budget_store.usage(CONTEXT, BudgetScope.WORKSPACE, utc_now().date().replace(day=1))
    assert usage.calls == 0  # nothing was sent, nothing is charged
    github.fail_dispatch = False
    retried = _create()
    assert retried.status_code == 201, retried.text
    assert len(github.dispatches) == 1


def test_an_interrupted_dispatch_still_fails_the_admitted_request(
    github: FakeGitHub, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(self: Any, payload: Any) -> None:
        raise RuntimeError("connection dropped")

    monkeypatch.setattr(developer.GitHubClient, "dispatch", broken)
    with pytest.raises(RuntimeError):
        _create()
    [stored] = developer_store.list(CONTEXT, limit=5, before=None, query=None)
    assert stored.status == DevRequestStatus.FAILED and stored.failure_reason and "stopped" in stored.failure_reason
    assert developer_store.active(CONTEXT) == []


def test_concurrent_creates_admit_one_request_and_dispatch_once(
    github: FakeGitHub, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(developer, "github_transport", SlowDispatch(github.hub))  # type: ignore[attr-defined]
    responses = asyncio.run(
        create_concurrently(main.app, OWNER, ["Add a dark/light toggle to settings", "Add a compact table density option"])
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert len(github.dispatches) == 1
    [created] = [response.json() for response in responses if response.status_code == 201]
    assert github.dispatches[0]["client_payload"]["request_id"] == created["id"]
    assert [item.id for item in developer_store.list(CONTEXT, limit=5, before=None, query=None)] == [created["id"]]


def test_concurrent_creates_respect_the_daily_limit(
    github: FakeGitHub, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(developer, "github_transport", SlowDispatch(github.hub))  # type: ignore[attr-defined]
    monkeypatch.setattr(developer_settings, "max_requests_per_day", 1)
    first = _create().json()
    client.post(f"/api/v1/developer/requests/{first['id']}/cancel", headers=OWNER)
    responses = asyncio.run(
        create_concurrently(main.app, OWNER, ["Add a compact table density option", "Add a keyboard shortcut list"])
    )
    assert [response.status_code for response in responses] == [429, 429]
    assert len(github.dispatches) == 1


def _memory_request(index: int, project_id: str | None = "devproj_a", workspace: str = WORKSPACE, **extra: Any) -> DeveloperRequest:
    now = utc_now()
    return DeveloperRequest(
        id=f"dev_{index}", tenant_id=TENANT, workspace_id=workspace, created_by="o", text="t" * 20,
        source=DevRequestSource.TEXT, status=DevRequestStatus.QUEUED, branch=f"anum-dev/dev_{index}",
        project_id=project_id, created_at=now, updated_at=now, **extra,
    )


def test_memory_admission_is_atomic_across_threads_per_project() -> None:
    memory = InMemoryDeveloperStore()
    now = utc_now()
    barrier = threading.Barrier(8)
    outcomes: list[str] = []

    def attempt(index: int) -> None:
        request = _memory_request(index)
        barrier.wait()
        try:
            memory.admit(request, since=now - timedelta(days=1), limit=10)
            outcomes.append("admitted")
        except DeveloperAdmissionError as exc:
            outcomes.append(exc.reason)

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["active"] * 7 + ["admitted"]
    assert len(memory.active(CONTEXT)) == 1
    # Another project, and the pre-projects repository, are not blocked.
    memory.admit(_memory_request(20, "devproj_b"), since=now - timedelta(days=1), limit=10)
    memory.admit(_memory_request(21, None), since=now - timedelta(days=1), limit=10)
    with pytest.raises(DeveloperAdmissionError):
        memory.admit(_memory_request(22, None), since=now - timedelta(days=1), limit=10)
    other = CONTEXT.model_copy(update={"workspace_id": "workspace_other"})
    memory.admit(_memory_request(30, workspace="workspace_other"), since=now - timedelta(days=1), limit=10)
    assert len(memory.active(other)) == 1
    with pytest.raises(DeveloperAdmissionError) as refused:
        memory.admit(
            _memory_request(31, "devproj_c").model_copy(update={"status": DevRequestStatus.CANCELLED}),
            since=now - timedelta(days=1),
            limit=3,
        )
    assert refused.value.reason == "daily_limit"


def test_a_request_never_returns_to_coding_after_it_left(github: FakeGitHub, project: dict[str, Any]) -> None:
    created = _create().json()
    github.start_run(created["id"], status="completed", conclusion="success")
    github.open_pull(created["id"])
    github.set_ci("a" * 40, None)
    url = f"/api/v1/developer/requests/{created['id']}"
    assert client.get(url, headers=OWNER).json()["status"] == "ci_running"
    github.pulls.clear()  # GitHub briefly answers without the pull request
    [stored] = developer_store.list(CONTEXT, limit=5, before=None, query=None)
    refreshed, changed = asyncio.run(sync(stored, project_store.get(project["id"], CONTEXT), force=True))
    assert refreshed.status == DevRequestStatus.CI_RUNNING and not changed


def test_status_follows_the_run_pull_request_and_ci(github: FakeGitHub, project: dict[str, Any]) -> None:
    created = _create().json()
    url = f"/api/v1/developer/requests/{created['id']}"
    github.start_run(created["id"])
    assert client.get(url, headers=OWNER).json()["status"] == "coding"
    github.open_pull(created["id"])
    github.set_ci("a" * 40, "success", None)
    running = client.get(url, headers=OWNER).json()
    assert running["status"] == "ci_running" and running["pr_number"] == 7
    assert running["checks"] == {"total": 2, "passed": 1, "failed": 0, "pending": 1, "failing": []}
    github.set_ci("a" * 40, "success", "failure")
    failed = client.get(url, headers=OWNER).json()
    assert failed["status"] == "ci_failed" and failed["checks"]["failing"] == ["CI 1"]
    assert [stage["state"] for stage in failed["timeline"]][:4] == ["done", "done", "done", "failed"]
    github.set_ci("a" * 40, "success", "success")
    ready = client.get(url, headers=OWNER).json()
    assert ready["status"] == "waiting_approval"
    assert ready["summary"].startswith("The change is ready for review")
    listed = client.get("/api/v1/developer/requests", headers=OWNER).json()
    assert [item["id"] for item in listed] == [created["id"]]
    assert "developer.request.status_changed" in _audit_actions()


def test_each_request_follows_its_own_repository(
    github: FakeGitHub, project: dict[str, Any], other_project: dict[str, Any]
) -> None:
    other: FakeGitHub = github.other  # type: ignore[attr-defined]
    mine = _create(project_id=project["id"]).json()
    theirs = _create("Add a tread depth chart to the fleet view", project_id=other_project["id"]).json()
    other.start_run(theirs["id"])
    listed = {item["id"]: item for item in client.get("/api/v1/developer/requests", headers=OWNER).json()}
    assert listed[theirs["id"]]["status"] == "coding" and listed[mine["id"]]["status"] == "queued"
    assert listed[theirs["id"]]["run_url"].startswith(f"https://github.com/{OTHER_REPO}/")
    filtered = client.get(f"/api/v1/developer/requests?project_id={other_project['id']}", headers=OWNER).json()
    assert [item["id"] for item in filtered] == [theirs["id"]]


def test_failed_runs_explain_which_step_refused(github: FakeGitHub, project: dict[str, Any]) -> None:
    created = _create().json()
    run = github.start_run(created["id"], status="completed", conclusion="failure")
    github.jobs[run["id"]] = [{"steps": [{"name": "Set up job", "conclusion": "success"}, {"name": "Refuse protected paths", "conclusion": "failure"}]}]
    body = client.get(f"/api/v1/developer/requests/{created['id']}", headers=OWNER).json()
    assert body["status"] == "failed" and "protected" in body["failure_reason"]
    assert body["timeline"][1]["state"] == "failed"


def test_merge_requires_green_checks(github: FakeGitHub, project: dict[str, Any]) -> None:
    created = _create().json()
    github.start_run(created["id"], status="completed", conclusion="success")
    github.open_pull(created["id"])
    github.set_ci("a" * 40, "success", "failure")
    refused = client.post(f"/api/v1/developer/requests/{created['id']}/merge-approval", headers=OWNER)
    assert refused.status_code == 409 and "CI failed" in refused.json()["error"]["message"]
    assert github.merges == [] and store.approvals == {}


def test_merge_goes_through_an_approval_bound_to_the_repository_and_head(github: FakeGitHub, project: dict[str, Any]) -> None:
    ready = _ready(github)
    response = client.post(f"/api/v1/developer/requests/{ready['id']}/merge-approval", headers=OWNER)
    assert response.status_code == 200, response.text
    approval = response.json()["approval"]
    assert approval["action"] == "developer.merge_pull_request" and approval["risk_level"] == "high"
    assert approval["target"] == "api.github.com"
    assert approval["arguments"]["head_sha"] == "a" * 40 and approval["arguments"]["pull_number"] == 7
    assert approval["arguments"]["repository"] == REPO and approval["arguments"]["project_id"] == project["id"]
    assert response.json()["request"]["approval"]["id"] == approval["id"]
    # Asking again returns the same pending approval.
    again = client.post(f"/api/v1/developer/requests/{ready['id']}/merge-approval", headers=OWNER).json()
    assert again["approval"]["id"] == approval["id"]
    assert github.merges == []

    # Approving without the displayed hash, or with another one, is refused; nothing merges.
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={}).status_code == 422
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": "f" * 64}).status_code == 409
    assert github.merges == []

    decided = client.post(
        f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]}
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["run"]["status"] == "completed"
    assert github.ready_for_review == [f"PR_node_{REPO}"]
    assert github.merges == [{"merge_method": "squash", "sha": "a" * 40, "commit_title": "Add a dark/light toggle to settings (#7)"}]
    merged = client.get(f"/api/v1/developer/requests/{ready['id']}", headers=OWNER).json()
    assert merged["status"] == "merged" and merged["merge_commit_sha"] == "m" * 40
    assert all(stage["state"] == "done" for stage in merged["timeline"])
    actions = _audit_actions()
    assert {"developer.merge.requested", "approval.approved", "developer.merge.completed"} <= set(actions)


def test_merge_is_refused_when_the_project_moved_to_another_repository(github: FakeGitHub, project: dict[str, Any]) -> None:
    ready = _ready(github)
    approval = client.post(f"/api/v1/developer/requests/{ready['id']}/merge-approval", headers=OWNER).json()["approval"]
    moved = client.patch(
        f"/api/v1/developer/projects/{project['id']}", headers=OWNER, json={"github_repo": "tyrepulse", "token": OTHER_PAT}
    )
    assert moved.status_code == 200
    decided = client.post(
        f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]}
    ).json()
    assert "not the request's project" in decided["run"]["result"]
    assert github.merges == [] and github.other.merges == []  # type: ignore[attr-defined]


def test_turned_off_project_cannot_be_merged(github: FakeGitHub, project: dict[str, Any]) -> None:
    ready = _ready(github)
    approval = client.post(f"/api/v1/developer/requests/{ready['id']}/merge-approval", headers=OWNER).json()["approval"]
    client.patch(f"/api/v1/developer/projects/{project['id']}", headers=OWNER, json={"enabled": False})
    decided = client.post(
        f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]}
    ).json()
    assert "turned off" in decided["run"]["result"] and github.merges == []
    again = client.post(f"/api/v1/developer/requests/{ready['id']}/merge-approval", headers=OWNER)
    assert again.status_code == 409 and "turned off" in again.json()["error"]["message"]


def test_merge_is_refused_when_ci_turns_red_after_approval(github: FakeGitHub, project: dict[str, Any]) -> None:
    ready = _ready(github)
    approval = client.post(f"/api/v1/developer/requests/{ready['id']}/merge-approval", headers=OWNER).json()["approval"]
    github.set_ci("a" * 40, "success", "failure")
    decided = client.post(
        f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]}
    )
    assert decided.status_code == 200
    assert "Not merged: CI is not green" in decided.json()["run"]["result"]
    assert github.merges == []
    after = client.get(f"/api/v1/developer/requests/{ready['id']}", headers=OWNER).json()
    assert after["status"] == "ci_failed" and after["merge_error"].startswith("Not merged")
    assert "developer.merge.refused" in _audit_actions()


def test_merge_is_refused_when_the_pull_request_changed_after_approval(github: FakeGitHub, project: dict[str, Any]) -> None:
    ready = _ready(github)
    approval = client.post(f"/api/v1/developer/requests/{ready['id']}/merge-approval", headers=OWNER).json()["approval"]
    github.pulls[f"anum-dev/{ready['id']}"]["head"]["sha"] = "b" * 40
    github.set_ci("b" * 40, "success")
    decided = client.post(
        f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]}
    ).json()
    assert "changed after approval" in decided["run"]["result"]
    assert github.merges == []
    # A fresh request for approval retires the stale one and binds the new head.
    fresh = client.post(f"/api/v1/developer/requests/{ready['id']}/merge-approval", headers=OWNER).json()["approval"]
    assert fresh["id"] != approval["id"] and fresh["arguments"]["head_sha"] == "b" * 40


def test_a_member_cannot_approve_the_merge(github: FakeGitHub, project: dict[str, Any]) -> None:
    ready = _ready(github)
    approval = client.post(f"/api/v1/developer/requests/{ready['id']}/merge-approval", headers=OWNER).json()["approval"]
    denied = client.post(
        f"/api/v1/approvals/{approval['id']}/approve", headers=MEMBER, json={"payload_hash": approval["payload_hash"]}
    )
    assert denied.status_code == 403
    assert github.merges == []


def test_cancel_stops_the_run_closes_the_pull_request_and_retires_the_approval(github: FakeGitHub, project: dict[str, Any]) -> None:
    ready = _ready(github)
    approval = client.post(f"/api/v1/developer/requests/{ready['id']}/merge-approval", headers=OWNER).json()["approval"]
    cancelled = client.post(f"/api/v1/developer/requests/{ready['id']}/cancel", headers=OWNER)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert github.pulls[f"anum-dev/{ready['id']}"]["state"] == "closed"
    assert github.deleted == [f"anum-dev/{ready['id']}"]
    assert store.approvals[approval["id"]].status == "expired"
    assert client.post(f"/api/v1/developer/requests/{ready['id']}/cancel", headers=OWNER).status_code == 409
    assert "developer.request.cancelled" in _audit_actions()


def test_cancel_stops_a_running_workflow(github: FakeGitHub, project: dict[str, Any]) -> None:
    created = _create().json()
    run = github.start_run(created["id"])
    assert client.post(f"/api/v1/developer/requests/{created['id']}/cancel", headers=OWNER).status_code == 200
    assert github.cancelled == [run["id"]]


def test_a_project_with_unfinished_requests_cannot_be_deleted(github: FakeGitHub, project: dict[str, Any]) -> None:
    created = _create().json()
    blocked = client.delete(f"/api/v1/developer/projects/{project['id']}", headers=OWNER)
    assert blocked.status_code == 409 and "1 unfinished request" in blocked.json()["error"]["message"]
    client.post(f"/api/v1/developer/requests/{created['id']}/cancel", headers=OWNER)
    assert client.delete(f"/api/v1/developer/projects/{project['id']}", headers=OWNER).status_code == 204
    [kept] = client.get("/api/v1/developer/requests", headers=OWNER).json()
    assert (kept["project_name"], kept["repository"], kept["status"]) == ("ANUM", REPO, "cancelled")


def test_requests_from_before_projects_follow_the_environment_repository(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(developer_settings, "github_repo", REPO)
    monkeypatch.setattr(developer_settings, "github_token", SecretStr(PAT))
    old = _memory_request(1, None).model_copy(update={"id": "dev_" + "1" * 32, "branch": "anum-dev/dev_" + "1" * 32})
    developer_store.add(old)
    github.start_run(old.id, status="completed", conclusion="success")
    github.open_pull(old.id)
    github.set_ci("a" * 40, "success")
    [listed] = client.get("/api/v1/developer/requests", headers=OWNER).json()
    assert listed["status"] == "waiting_approval" and listed["project_id"] is None
    approval = client.post(f"/api/v1/developer/requests/{old.id}/merge-approval", headers=OWNER).json()["approval"]
    assert approval["arguments"]["repository"] == REPO and approval["arguments"]["project_id"] == ""
    decided = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]})
    assert decided.json()["run"]["status"] == "completed" and len(github.merges) == 1


def test_list_pages_and_searches(github: FakeGitHub, project: dict[str, Any]) -> None:
    now = utc_now()
    for index in range(5):
        developer_store.add(
            DeveloperRequest(
                id=f"dev_{index:032x}",
                tenant_id=TENANT,
                workspace_id=WORKSPACE,
                created_by="owner_dev",
                text=f"Change number {index} {'search me' if index == 2 else ''}",
                source=DevRequestSource.TEXT,
                status=DevRequestStatus.MERGED,
                branch=f"anum-dev/dev_{index:032x}",
                project_id=project["id"],
                created_at=now + timedelta(seconds=index),
                updated_at=now,
            )
        )
    first = client.get("/api/v1/developer/requests?limit=2", headers=OWNER).json()
    assert [item["id"][-1] for item in first] == ["4", "3"]
    second = client.get(f"/api/v1/developer/requests?limit=2&before={first[-1]['id']}", headers=OWNER).json()
    assert [item["id"][-1] for item in second] == ["2", "1"]
    found = client.get("/api/v1/developer/requests?q=SEARCH", headers=OWNER).json()
    assert [item["id"][-1] for item in found] == ["2"]
    other = {**OWNER, "x-workspace-id": "workspace_other"}
    assert client.get("/api/v1/developer/requests", headers=other).json() == []
    assert client.get("/api/v1/developer/projects", headers=other).json() == []
    assert client.get("/api/v1/developer/requests?before=dev_missing", headers=OWNER).status_code == 400


def test_derive_status_and_timeline_are_pure() -> None:
    now = utc_now()
    request = DeveloperRequest(
        id="dev_x", tenant_id=TENANT, workspace_id=WORKSPACE, created_by="o", text="t" * 20,
        source=DevRequestSource.TEXT, status=DevRequestStatus.QUEUED, branch="anum-dev/dev_x",
        created_at=now - timedelta(hours=1), updated_at=now,
    )
    never = derive_status(request, None, None, None, now, workflow_path=".github/workflows/custom.yml")
    assert never[0] == DevRequestStatus.FAILED and never[1] and "custom.yml" in never[1]
    run = WorkflowRun(id=1, status="completed", conclusion="cancelled", html_url="")
    assert derive_status(request, run, None, None, now)[0] == DevRequestStatus.CANCELLED
    pull = PullRequest(number=1, html_url="", title="x", state="closed", merged=False, draft=False, head_sha="a", node_id="n")
    assert derive_status(request, run, pull, None, now)[0] == DevRequestStatus.CANCELLED
    merged = PullRequest(number=1, html_url="", title="x", state="closed", merged=True, draft=False, head_sha="a", node_id="n")
    assert derive_status(request, run, merged, None, now)[0] == DevRequestStatus.MERGED
    assert not ChecksState().green and ChecksState(total=1, passed=1).green
    request.status = DevRequestStatus.CANCELLED
    assert [stage.state for stage in timeline(request)][0] == "failed"


def test_protected_request_detection() -> None:
    assert mentions_protected("Edit .github/workflows/ci.yml")
    assert mentions_protected("print the API key in the logs")
    assert not mentions_protected("Add a dark/light toggle to settings using the design tokens")
    assert not mentions_protected("Add a workflow template to the Workbench")


def test_merge_tool_is_registered_for_the_api_and_the_worker() -> None:
    assert "developer.merge_pull_request" in main.tool_registry.names
    definition = main.tool_registry.definition("developer.merge_pull_request")
    assert definition is not None and definition.risk_level == "high" and definition.required_roles == {"owner"}


def test_token_source_values_are_stable() -> None:
    assert [source.value for source in TokenSource] == ["project", "environment"]


# -- the workflows ------------------------------------------------------------------------------


def _guarded(path: Path, *, issues: bool) -> dict[str, Any]:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)
    assert triggers["repository_dispatch"]["types"] == ["anum-dev-request"]
    assert ("issues" in triggers) is issues
    assert workflow["concurrency"] == {"group": "anum-developer", "cancel-in-progress": False}
    assert workflow["permissions"] == {}
    jobs = workflow["jobs"]
    code, publish = jobs["code"], jobs["publish"]
    assert code["permissions"]["contents"] == "read" and "write" not in code["permissions"].values()
    assert code["timeout-minutes"] <= 90 and publish["timeout-minutes"] <= 20
    action = next(step for step in code["steps"] if str(step.get("uses", "")).startswith("anthropics/claude-code-action@"))
    assert len(action["uses"].split("@", 1)[1].split()[0]) == 40  # pinned to a commit SHA
    assert action["with"]["anthropic_api_key"] == "${{ secrets.ANTHROPIC_API_KEY }}"
    assert "WebFetch" in action["with"]["claude_args"]
    text = path.read_text(encoding="utf-8")
    # The coding job never sees the push credential; only the publish job does.
    code_text = text.split("\n  code:", 1)[1].split("\n  publish:", 1)[0]
    publish_text = text.split("\n  publish:", 1)[1]
    assert "ANUM_DEV_PUSH_TOKEN" not in code_text and "ANUM_DEV_PUSH_TOKEN" in publish_text
    assert "continue-on-error" not in text
    # The publish job reads the project's protected paths from the event, never from the coding job.
    assert "PROTECTED_PATHS_REQUEST: ${{ toJSON(github.event.client_payload.protected_paths) }}" in publish_text
    assert "vars.ANUM_DEV_PROTECTED_PATHS" in publish_text
    for step_name in ("Validate the request", "Refuse protected requests", "Check that the developer changed something",
                      "Refuse protected paths", "Run the developer", "Open the draft pull request"):
        assert f"name: {step_name}" in text, step_name
    return workflow


def test_developer_workflow_is_valid_and_guarded() -> None:
    workflow = _guarded(ROOT / ".github" / "workflows" / "anum-developer.yml", issues=True)
    assert workflow["jobs"]["code"]["permissions"] == {"contents": "read", "issues": "read", "actions": "read"}


def test_workflow_template_is_self_contained_pinned_and_guarded() -> None:
    path = Path(developer.__file__).parent / "workflow_templates" / "anum-developer.yml"
    workflow = _guarded(path, issues=False)
    assert workflow["jobs"]["code"]["permissions"] == {"contents": "read", "actions": "read"}
    assert workflow["jobs"]["publish"]["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                reference = step["uses"].split("@", 1)[1]
                assert len(reference) == 40 and all(c in "0123456789abcdef" for c in reference), step["uses"]
            assert "workflow_call" not in json.dumps(step)
    text = path.read_text(encoding="utf-8")
    for step_name in developer.FAILED_STEP_REASONS:
        if step_name != "Check the push credential":
            assert f"name: {step_name}" in text or step_name == "Install dependencies for the checks", step_name
    assert "ANUM_DEV_ALLOW_PROTECTED_PATHS" in text and ".anum-dev-protected.txt" in text
    assert developer.PROTECTED_REQUEST.pattern.replace("\n", "") in text.replace("'", "")
    assert text == workflow_template()


def test_the_template_ships_with_the_package() -> None:
    pyproject = (ROOT / "services" / "api" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'anum_api = ["workflow_templates/*.yml"]' in pyproject


# -- migrations -------------------------------------------------------------------------------


def test_one_active_migration_is_expand_only_and_built_concurrently() -> None:
    versions = ROOT / "services" / "api" / "migrations" / "versions"
    source = (versions / "0023_developer_one_active.py").read_text(encoding="utf-8")
    assert 'revision = "0023_developer_one_active"' in source and len("0023_developer_one_active") <= 32
    assert 'down_revision = "0022_developer_requests"' in source
    others = [path for path in versions.glob("*.py") if path.name != "0023_developer_one_active.py"]
    assert not [path for path in others if 'down_revision = "0022_developer_requests"' in path.read_text(encoding="utf-8")]
    upgrade = source.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert "create unique index concurrently if not exists {index}" in upgrade and "autocommit_block" in upgrade
    assert "where status in {active}" in upgrade
    assert "uq_developer_requests_one_active" in source and "('queued', 'coding')" in source
    # RLS stays forced after the one-off clean-up, and no role gets a way around it.
    assert upgrade.index("no force row level security") < upgrade.index("alter table {table} force row level security")
    assert "bypassrls" not in upgrade and "security definer" not in upgrade and "grant " not in upgrade
    assert "add_column" not in upgrade and "alter_column" not in upgrade and "drop table" not in upgrade
    assert "drop index concurrently if exists {index}" in upgrade  # only an invalid leftover


def test_migration_is_expand_only_with_forced_rls() -> None:
    path = ROOT / "services" / "api" / "migrations" / "versions" / "0022_developer_requests.py"
    source = path.read_text(encoding="utf-8")
    assert 'revision = "0022_developer_requests"' in source and len("0022_developer_requests") <= 32
    upgrade = source.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert "force row level security" in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade and "grant " not in upgrade
    assert "drop " not in upgrade


def test_projects_migration_chains_after_automation_policy_with_forced_rls_and_a_discovery_only_grant() -> None:
    versions = ROOT / "services" / "api" / "migrations" / "versions"
    source = (versions / "0032_developer_projects.py").read_text(encoding="utf-8")
    assert 'revision = "0032_developer_projects"' in source and len("0032_developer_projects") <= 32
    assert 'down_revision = "0031_automation_policy"' in source
    others = [path for path in versions.glob("*.py") if path.name != "0032_developer_projects.py"]
    assert not [path for path in others if 'down_revision = "0031_automation_policy"' in path.read_text(encoding="utf-8")]
    upgrade = source.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert "enable row level security" in upgrade and "force row level security" in upgrade
    assert "no force row level security" not in upgrade  # the data step runs inside RLS context
    assert "set_config('anum.tenant_id'" in upgrade and "set_config('anum.workspace_id'" in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade
    # The only grant: ids of rows with a ciphertext, to the discovery role.
    assert upgrade.count("grant ") == 1 and "grant select (id, tenant_id, workspace_id)" in upgrade
    assert "to anum_maintenance" in upgrade and "token_ciphertext is not null" in upgrade
    # Expand only: new nullable columns; the one dropped index is the per-workspace rule it replaces.
    assert upgrade.count("op.add_column(") == 3 and "nullable=false" not in upgrade.split("op.add_column(", 1)[1]
    assert "drop table" not in upgrade and "drop_column" not in upgrade and "alter_column" not in upgrade
    assert upgrade.index("create unique index concurrently if not exists {new_index}") < upgrade.index(
        "drop index concurrently if exists {old_index}"
    )
    assert "token" not in source.split("def _carry_over_repository", 1)[1].split("def downgrade", 1)[0].lower().replace(
        "token_source", ""
    ).replace("'environment'", "")


def test_every_state_changing_developer_route_is_audited_without_tokens(github: FakeGitHub) -> None:
    from audit_probe import AuditProbe

    probe = AuditProbe(client, "developer", secrets=[PAT, OTHER_PAT])
    owner, repo = REPO.split("/")
    created = probe.call(
        "POST", "/api/v1/developer/projects", OWNER,
        json={"name": "ANUM", "github_owner": owner, "github_repo": repo, "token": PAT},
    ).json()
    probe.call("PATCH", f"/api/v1/developer/projects/{created['id']}", OWNER, json={"description": "The ANUM app", "token": PAT})
    probe.call("POST", f"/api/v1/developer/projects/{created['id']}/test", OWNER)
    del github.files[(WORKFLOW_PATH, "main")]
    probe.call("POST", f"/api/v1/developer/projects/{created['id']}/install-workflow", OWNER)
    github.files[(WORKFLOW_PATH, "main")] = workflow_template()
    text = "Add a dark/light toggle to settings"
    request = probe.call("POST", "/api/v1/developer/requests", OWNER, sent=[text], json={"text": text}).json()
    github.start_run(request["id"], status="completed", conclusion="success")
    github.open_pull(request["id"])
    github.set_ci("a" * 40, "success", "skipped")
    probe.call("POST", f"/api/v1/developer/requests/{request['id']}/merge-approval", OWNER)
    second = _create("Fix the typo on the about page").json()
    probe.call("POST", f"/api/v1/developer/requests/{second['id']}/cancel", OWNER)
    spare = add_project("TyrePulse", OTHER_REPO, OTHER_PAT).json()
    probe.call("DELETE", f"/api/v1/developer/projects/{spare['id']}", OWNER)
    probe.assert_covered()
