"""Developer mode (docs/developer-mode.md) against a fake GitHub (httpx.MockTransport)."""

from __future__ import annotations

import json
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
    DeveloperRequest,
    DevRequestSource,
    DevRequestStatus,
    derive_status,
    developer_settings,
    developer_store,
    mentions_protected,
    timeline,
)
from anum_api.developer_github import ChecksState, PullRequest, WorkflowRun, run_title
from anum_api.model_budget import BudgetScope, ModelBudgetLimits, memory_model_budget_store
from anum_api.schemas import TenantContext, utc_now
from anum_api.store import store

REPO = "acme/anum"
PAT = "example-fine-grained-value"
TENANT = "tenant_dev"
WORKSPACE = "workspace_dev"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_dev", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_dev", "x-user-roles": "member"}
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_dev", roles=["owner"])
ROOT = Path(__file__).parents[3]

client = TestClient(main.app)


class FakeGitHub:
    """Just enough of the GitHub REST and GraphQL APIs for developer mode."""

    def __init__(self) -> None:
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

    # helpers for tests
    def start_run(self, request_id: str, status: str = "in_progress", conclusion: str | None = None) -> dict[str, Any]:
        run = {
            "id": 1000 + len(self.runs),
            "display_title": run_title(request_id),
            "status": status,
            "conclusion": conclusion,
            "html_url": f"https://github.com/{REPO}/actions/runs/{1000 + len(self.runs)}",
            "created_at": "2026-10-11T10:00:00Z",
        }
        self.runs.insert(0, run)
        return run

    def open_pull(self, request_id: str, sha: str = "a" * 40, number: int = 7) -> dict[str, Any]:
        pull = {
            "number": number,
            "html_url": f"https://github.com/{REPO}/pull/{number}",
            "title": "Add a dark/light toggle to settings",
            "state": "open",
            "merged_at": None,
            "draft": True,
            "node_id": "PR_node",
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

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {PAT}"
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        prefix = f"/repos/{REPO}"
        if request.method == "POST" and path == "/graphql":
            self.ready_for_review.append(json.loads(request.content)["variables"]["id"])
            for pull in self.pulls.values():
                pull["draft"] = False
            return httpx.Response(200, json={"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": False}}}})
        assert path.startswith(prefix), path
        rest = path[len(prefix):]
        if request.method == "POST" and rest == "/dispatches":
            if self.fail_dispatch:
                return httpx.Response(404, json={"message": "Not Found"})
            self.dispatches.append(json.loads(request.content))
            return httpx.Response(204)
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
            return httpx.Response(200, json=[pull] if pull else [])
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


@pytest.fixture(autouse=True)
def github(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGitHub]:
    fake = FakeGitHub()
    monkeypatch.setattr(developer, "github_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(developer_settings, "github_repo", REPO)
    monkeypatch.setattr(developer_settings, "github_token", SecretStr(PAT))
    monkeypatch.setattr(developer_settings, "tenant_id", TENANT)
    monkeypatch.setattr(developer_settings, "workspace_id", "")
    monkeypatch.setattr(developer_settings, "status_cache_seconds", 0.0)
    monkeypatch.setattr(developer_settings, "max_requests_per_day", 10)
    developer_store.clear()
    memory_model_budget_store.clear()
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
        collection.clear()
    yield fake
    developer_store.clear()


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


def test_developer_permission_is_owner_only() -> None:
    assert Permission.DEVELOPER_MANAGE in ROLE_PERMISSIONS[Role.OWNER]
    assert Permission.DEVELOPER_MANAGE not in ROLE_PERMISSIONS[Role.MEMBER]
    assert Permission.DEVELOPER_MANAGE not in ROLE_PERMISSIONS[Role.VIEWER]
    for method, path in (
        ("get", "/api/v1/developer/setup"),
        ("get", "/api/v1/developer/requests"),
        ("post", "/api/v1/developer/requests"),
        ("post", "/api/v1/developer/requests/dev_x/merge-approval"),
        ("post", "/api/v1/developer/requests/dev_x/cancel"),
    ):
        response = getattr(client, method)(path, headers=MEMBER, **({"json": {"text": "Add a toggle please"}} if method == "post" else {}))
        assert response.status_code == 403, path


def test_setup_reports_missing_settings_by_name_and_never_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    ready = client.get("/api/v1/developer/setup", headers=OWNER).json()
    assert ready["configured"] and ready["enabled_here"] and ready["repository"] == REPO
    assert PAT not in json.dumps(ready)
    monkeypatch.setattr(developer_settings, "github_token", None)
    monkeypatch.setattr(developer_settings, "github_repo", "not a repo")
    missing = client.get("/api/v1/developer/setup", headers=OWNER).json()
    assert not missing["configured"]
    assert missing["missing"] == ["ANUM_DEVELOPER_GITHUB_REPO", "ANUM_DEVELOPER_GITHUB_TOKEN"]
    refused = _create()
    assert refused.status_code == 409 and "ANUM_DEVELOPER_GITHUB_TOKEN" in refused.json()["error"]["message"]


def test_other_tenants_cannot_use_developer_mode() -> None:
    other = {**OWNER, "x-tenant-id": "tenant_other"}
    assert client.get("/api/v1/developer/setup", headers=other).json()["enabled_here"] is False
    assert client.post("/api/v1/developer/requests", headers=other, json={"text": "Add a dark mode toggle"}).status_code == 403


def test_create_dispatches_to_github_audits_and_counts_one_budgeted_action(github: FakeGitHub) -> None:
    response = _create(source="voice")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued" and body["source"] == "voice"
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
                "base_branch": "main",
            },
        }
    ]
    assert "developer.request.created" in _audit_actions()
    usage = memory_model_budget_store.usage(CONTEXT, BudgetScope.WORKSPACE, utc_now().date().replace(day=1))
    assert usage.calls == 1 and usage.estimated_cost_usd == pytest.approx(developer_settings.request_cost_usd)


def test_requests_are_validated_and_protected_topics_refused(github: FakeGitHub) -> None:
    assert _create("short").status_code == 422
    refused = _create("Rotate the GitHub Actions secrets used by deploys")
    assert refused.status_code == 422 and "explicitly" in refused.json()["error"]["message"]
    assert "developer.request.refused" in _audit_actions()
    assert github.dispatches == []
    allowed = _create("Rotate the GitHub Actions secrets used by deploys", allow_protected_paths=True)
    assert allowed.status_code == 201
    assert github.dispatches[0]["client_payload"]["allow_protected_paths"] is True


def test_one_active_request_at_a_time_and_a_daily_cap(github: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _create().json()
    busy = _create("Add a second unrelated change")
    assert busy.status_code == 409
    client.post(f"/api/v1/developer/requests/{first['id']}/cancel", headers=OWNER)
    monkeypatch.setattr(developer_settings, "max_requests_per_day", 1)
    capped = _create("Add a second unrelated change")
    assert capped.status_code == 429 and "a day" in capped.json()["error"]["message"]


def test_budget_exhausted_refuses_before_dispatch(github: FakeGitHub) -> None:
    memory_model_budget_store.set_budget(CONTEXT, BudgetScope.WORKSPACE, ModelBudgetLimits(monthly_cost_limit_usd=0), utc_now())
    response = _create()
    assert response.status_code == 402
    assert github.dispatches == []


def test_dispatch_failure_is_reported_without_storing_a_request(github: FakeGitHub) -> None:
    github.fail_dispatch = True
    response = _create()
    assert response.status_code == 502
    message = response.json()["error"]["message"]
    assert "GitHub answered 404" in message and PAT not in message
    assert client.get("/api/v1/developer/requests", headers=OWNER).json() == []
    assert "developer.request.dispatch_failed" in _audit_actions()


def test_status_follows_the_run_pull_request_and_ci(github: FakeGitHub) -> None:
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


def test_failed_runs_explain_which_step_refused(github: FakeGitHub) -> None:
    created = _create().json()
    run = github.start_run(created["id"], status="completed", conclusion="failure")
    github.jobs[run["id"]] = [{"steps": [{"name": "Set up job", "conclusion": "success"}, {"name": "Refuse protected paths", "conclusion": "failure"}]}]
    body = client.get(f"/api/v1/developer/requests/{created['id']}", headers=OWNER).json()
    assert body["status"] == "failed" and "protected files" in body["failure_reason"]
    assert body["timeline"][1]["state"] == "failed"


def test_merge_requires_green_checks(github: FakeGitHub) -> None:
    created = _create().json()
    github.start_run(created["id"], status="completed", conclusion="success")
    github.open_pull(created["id"])
    github.set_ci("a" * 40, "success", "failure")
    refused = client.post(f"/api/v1/developer/requests/{created['id']}/merge-approval", headers=OWNER)
    assert refused.status_code == 409 and "CI failed" in refused.json()["error"]["message"]
    assert github.merges == [] and store.approvals == {}


def test_merge_goes_through_an_approval_bound_to_the_head_commit(github: FakeGitHub) -> None:
    ready = _ready(github)
    response = client.post(f"/api/v1/developer/requests/{ready['id']}/merge-approval", headers=OWNER)
    assert response.status_code == 200, response.text
    approval = response.json()["approval"]
    assert approval["action"] == "developer.merge_pull_request" and approval["risk_level"] == "high"
    assert approval["target"] == "api.github.com"
    assert approval["arguments"]["head_sha"] == "a" * 40 and approval["arguments"]["pull_number"] == 7
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
    assert github.ready_for_review == ["PR_node"]
    assert github.merges == [{"merge_method": "squash", "sha": "a" * 40, "commit_title": "Add a dark/light toggle to settings (#7)"}]
    merged = client.get(f"/api/v1/developer/requests/{ready['id']}", headers=OWNER).json()
    assert merged["status"] == "merged" and merged["merge_commit_sha"] == "m" * 40
    assert all(stage["state"] == "done" for stage in merged["timeline"])
    actions = _audit_actions()
    assert {"developer.merge.requested", "approval.approved", "developer.merge.completed"} <= set(actions)


def test_merge_is_refused_when_ci_turns_red_after_approval(github: FakeGitHub) -> None:
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


def test_merge_is_refused_when_the_pull_request_changed_after_approval(github: FakeGitHub) -> None:
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


def test_a_member_cannot_approve_the_merge(github: FakeGitHub) -> None:
    ready = _ready(github)
    approval = client.post(f"/api/v1/developer/requests/{ready['id']}/merge-approval", headers=OWNER).json()["approval"]
    denied = client.post(
        f"/api/v1/approvals/{approval['id']}/approve", headers=MEMBER, json={"payload_hash": approval["payload_hash"]}
    )
    assert denied.status_code == 403
    assert github.merges == []


def test_cancel_stops_the_run_closes_the_pull_request_and_retires_the_approval(github: FakeGitHub) -> None:
    ready = _ready(github)
    approval = client.post(f"/api/v1/developer/requests/{ready['id']}/merge-approval", headers=OWNER).json()["approval"]
    cancelled = client.post(f"/api/v1/developer/requests/{ready['id']}/cancel", headers=OWNER)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert github.pulls[f"anum-dev/{ready['id']}"]["state"] == "closed"
    assert github.deleted == [f"anum-dev/{ready['id']}"]
    assert store.approvals[approval["id"]].status == "expired"
    assert client.post(f"/api/v1/developer/requests/{ready['id']}/cancel", headers=OWNER).status_code == 409
    assert "developer.request.cancelled" in _audit_actions()


def test_cancel_stops_a_running_workflow(github: FakeGitHub) -> None:
    created = _create().json()
    run = github.start_run(created["id"])
    assert client.post(f"/api/v1/developer/requests/{created['id']}/cancel", headers=OWNER).status_code == 200
    assert github.cancelled == [run["id"]]


def test_list_pages_and_searches(github: FakeGitHub) -> None:
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
    assert client.get("/api/v1/developer/requests?before=dev_missing", headers=OWNER).status_code == 400


def test_derive_status_and_timeline_are_pure() -> None:
    now = utc_now()
    request = DeveloperRequest(
        id="dev_x", tenant_id=TENANT, workspace_id=WORKSPACE, created_by="o", text="t" * 20,
        source=DevRequestSource.TEXT, status=DevRequestStatus.QUEUED, branch="anum-dev/dev_x",
        created_at=now - timedelta(hours=1), updated_at=now,
    )
    assert derive_status(request, None, None, None, now)[0] == DevRequestStatus.FAILED
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


def test_developer_workflow_is_valid_and_guarded() -> None:
    path = ROOT / ".github" / "workflows" / "anum-developer.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)
    assert triggers["repository_dispatch"]["types"] == ["anum-dev-request"]
    assert triggers["issues"]["types"] == ["labeled"]
    assert workflow["concurrency"] == {"group": "anum-developer", "cancel-in-progress": False}
    assert workflow["permissions"] == {}
    jobs = workflow["jobs"]
    code, publish = jobs["code"], jobs["publish"]
    assert code["permissions"] == {"contents": "read", "issues": "read", "actions": "read"}
    assert code["timeout-minutes"] <= 90 and publish["timeout-minutes"] <= 20
    action = next(step for step in code["steps"] if str(step.get("uses", "")).startswith("anthropics/claude-code-action@"))
    assert len(action["uses"].split("@", 1)[1].split()[0]) == 40  # pinned to a commit SHA
    assert action["with"]["anthropic_api_key"] == "${{ secrets.ANTHROPIC_API_KEY }}"
    text = path.read_text(encoding="utf-8")
    # The coding job never sees the push credential; only the publish job does.
    code_text = text.split("\n  code:", 1)[1].split("\n  publish:", 1)[0]
    assert "ANUM_DEV_PUSH_TOKEN" not in code_text and "ANUM_DEV_PUSH_TOKEN" in text.split("\n  publish:", 1)[1]
    assert "continue-on-error" not in text
    for step_name in developer.FAILED_STEP_REASONS:
        assert f"name: {step_name}" in text, step_name


def test_migration_is_expand_only_with_forced_rls() -> None:
    path = ROOT / "services" / "api" / "migrations" / "versions" / "0022_developer_requests.py"
    source = path.read_text(encoding="utf-8")
    assert 'revision = "0022_developer_requests"' in source and len("0022_developer_requests") <= 32
    upgrade = source.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert "force row level security" in upgrade
    assert "bypassrls" not in upgrade and "security definer" not in upgrade and "grant " not in upgrade
    assert "drop " not in upgrade
