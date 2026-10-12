"""GitHub REST calls for developer mode (docs/developer-mode.md).

Everything developer mode asks of GitHub goes through :class:`GitHubClient`: dispatch a
coding request to the ``anum-developer.yml`` workflow, find that request's workflow run
and pull request, read the CI state of the pull request's head commit, and, only from
the approved ``developer.merge_pull_request`` tool, squash-merge it.

One client serves one project's repository (``docs/developer-mode.md#projects``): it also
checks that the repository is reachable and has the workflow (Test connection) and opens
the pull request that installs the workflow (Install workflow).

The token is a fine-grained personal access token (one per project, or one covering the
selected repositories). It is held as a ``SecretStr`` and only ever placed in the
``Authorization`` header: errors carry the HTTP status and GitHub's short ``message``
field, never the request headers or body.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from pydantic import SecretStr

API_VERSION = "2022-11-28"
DISPATCH_EVENT = "anum-dev-request"
RUN_TITLE_PREFIX = "anum-dev "
TIMEOUT_SECONDS = 15.0
# Conclusions that count as a passing CI run or status.
PASSING = frozenset({"success", "neutral", "skipped"})


class GitHubError(Exception):
    """A GitHub call failed. ``message`` is safe to show: status and GitHub's own message."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class WorkflowRun:
    id: int
    status: str  # queued, in_progress, completed, waiting, requested, pending
    conclusion: str | None  # success, failure, cancelled, timed_out, ...
    html_url: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class PullRequest:
    number: int
    html_url: str
    title: str
    state: str  # open, closed
    merged: bool
    draft: bool
    head_sha: str
    node_id: str


@dataclass(frozen=True)
class ChecksState:
    total: int = 0
    passed: int = 0
    failed: int = 0
    pending: int = 0
    failing: list[str] = field(default_factory=list)

    @property
    def green(self) -> bool:
        return self.total > 0 and self.failed == 0 and self.pending == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pending": self.pending,
            "failing": list(self.failing),
        }


@dataclass(frozen=True)
class RepositoryInfo:
    full_name: str
    default_branch: str
    private: bool
    html_url: str


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_title(request_id: str) -> str:
    """The workflow's ``run-name`` for one request; how a run is found again."""
    return f"{RUN_TITLE_PREFIX}{request_id}"


class GitHubClient:
    def __init__(
        self,
        repository: str,
        token: SecretStr,
        *,
        api_url: str = "https://api.github.com",
        workflow_file: str = "anum-developer.yml",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.repository = repository
        self.owner = repository.split("/", 1)[0]
        self.api_url = api_url.rstrip("/")
        self.workflow_file = workflow_file
        self._token = token
        self._transport = transport

    # -- plumbing -------------------------------------------------------------------

    def _graphql_url(self) -> str:
        if self.api_url.endswith("/api/v3"):
            return self.api_url[: -len("/v3")] + "/graphql"
        return f"{self.api_url}/graphql"

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        url: str | None = None,
        allow: frozenset[int] = frozenset(),
    ) -> tuple[int, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            "User-Agent": "anum-developer-mode",
        }
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS, transport=self._transport, follow_redirects=False
            ) as client:
                response = await client.request(
                    method, url or f"{self.api_url}{path}", params=params, json=json, headers=headers
                )
        except httpx.HTTPError as exc:
            raise GitHubError(0, f"GitHub could not be reached ({type(exc).__name__}).") from None
        body: Any = None
        if response.content:
            try:
                body = response.json()
            except ValueError:
                body = None
        if response.status_code >= 400 and response.status_code not in allow:
            detail = body.get("message") if isinstance(body, dict) else None
            text = str(detail)[:200] if detail else response.reason_phrase
            raise GitHubError(response.status_code, f"GitHub answered {response.status_code}: {text}")
        return response.status_code, body

    def _repo(self, suffix: str) -> str:
        return f"/repos/{self.repository}{suffix}"

    # -- requests ---------------------------------------------------------------------

    async def dispatch(self, payload: Mapping[str, Any]) -> None:
        """Start the coding workflow: ``repository_dispatch`` of type ``anum-dev-request``."""
        await self._call(
            "POST",
            self._repo("/dispatches"),
            json={"event_type": DISPATCH_EVENT, "client_payload": dict(payload)},
        )

    async def find_run(self, request_id: str) -> WorkflowRun | None:
        """The newest run of the developer workflow whose title names ``request_id``."""
        _, body = await self._call(
            "GET",
            self._repo(f"/actions/workflows/{self.workflow_file}/runs"),
            params={"event": "repository_dispatch", "per_page": 50},
        )
        title = run_title(request_id)
        for item in (body or {}).get("workflow_runs", []):
            if item.get("display_title") == title or item.get("name") == title:
                return WorkflowRun(
                    id=int(item["id"]),
                    status=str(item.get("status") or "queued"),
                    conclusion=item.get("conclusion"),
                    html_url=str(item.get("html_url") or ""),
                    created_at=_parse_time(item.get("created_at")),
                )
        return None

    async def failed_step(self, run_id: int) -> str | None:
        """Name of the first failed step of the run (it says why: refused, no changes, ...)."""
        _, body = await self._call("GET", self._repo(f"/actions/runs/{run_id}/jobs"))
        for job in (body or {}).get("jobs", []):
            for step in job.get("steps") or []:
                if step.get("conclusion") == "failure":
                    return str(step.get("name") or "")
        return None

    async def find_pull(self, branch: str) -> PullRequest | None:
        _, body = await self._call(
            "GET",
            self._repo("/pulls"),
            params={"state": "all", "head": f"{self.owner}:{branch}", "per_page": 5},
        )
        items = body if isinstance(body, list) else []
        return _pull(items[0]) if items else None

    async def get_pull(self, number: int) -> PullRequest:
        _, body = await self._call("GET", self._repo(f"/pulls/{number}"))
        return _pull(body)

    async def checks(self, sha: str) -> ChecksState:
        """CI on ``sha``: every Actions run (except this workflow) and every commit status.

        Actions runs need the token's *Actions: read*; commit statuses need *Commit
        statuses: read* and are skipped when the token cannot read them.
        """
        _, body = await self._call(
            "GET", self._repo("/actions/runs"), params={"head_sha": sha, "per_page": 100}
        )
        latest: dict[str, dict[str, Any]] = {}
        for item in (body or {}).get("workflow_runs", []):
            path = str(item.get("path") or "")
            if path.endswith(f"/{self.workflow_file}") or path == self.workflow_file:
                continue
            key = path or str(item.get("name") or item.get("id"))
            # Re-runs of one workflow: the newest attempt decides.
            if key not in latest or str(item.get("created_at", "")) > str(latest[key].get("created_at", "")):
                latest[key] = item
        total = passed = failed = pending = 0
        failing: list[str] = []
        for item in latest.values():
            total += 1
            if item.get("status") != "completed":
                pending += 1
            elif item.get("conclusion") in PASSING:
                passed += 1
            else:
                failed += 1
                failing.append(str(item.get("name") or "workflow"))
        code, status_body = await self._call(
            "GET", self._repo(f"/commits/{sha}/status"), allow=frozenset({403, 404})
        )
        if code < 400:
            for item in (status_body or {}).get("statuses", []):
                total += 1
                state = item.get("state")
                if state == "success":
                    passed += 1
                elif state == "pending":
                    pending += 1
                else:
                    failed += 1
                    failing.append(str(item.get("context") or "status"))
        return ChecksState(total=total, passed=passed, failed=failed, pending=pending, failing=sorted(failing))

    # -- project setup (Test connection, Install workflow) --------------------------------

    async def repository_info(self) -> RepositoryInfo:
        """The repository as the token sees it; 404 when it does not exist or is not covered."""
        _, body = await self._call("GET", self._repo(""))
        body = body if isinstance(body, dict) else {}
        return RepositoryInfo(
            full_name=str(body.get("full_name") or self.repository),
            default_branch=str(body.get("default_branch") or ""),
            private=bool(body.get("private")),
            html_url=str(body.get("html_url") or ""),
        )

    async def branch_sha(self, branch: str) -> str | None:
        """The commit a branch points at, or None when there is no such branch."""
        code, body = await self._call("GET", self._repo(f"/git/ref/heads/{branch}"), allow=frozenset({404}))
        if code == 404 or not isinstance(body, dict):
            return None
        return str((body.get("object") or {}).get("sha") or "") or None

    async def file_sha(self, path: str, ref: str) -> str | None:
        """The blob sha of ``path`` on ``ref``, or None when the file is not there."""
        code, body = await self._call(
            "GET", self._repo(f"/contents/{path}"), params={"ref": ref}, allow=frozenset({404})
        )
        if code == 404 or not isinstance(body, dict):
            return None
        return str(body.get("sha") or "") or None

    async def workflow_readable(self) -> bool:
        """True when the token can read this workflow through the Actions API (Actions: read)."""
        code, _ = await self._call(
            "GET", self._repo(f"/actions/workflows/{self.workflow_file}"), allow=frozenset({403, 404})
        )
        return code < 400

    async def create_branch(self, branch: str, sha: str) -> None:
        await self._call("POST", self._repo("/git/refs"), json={"ref": f"refs/heads/{branch}", "sha": sha})

    async def put_file(self, path: str, content: str, *, message: str, branch: str, sha: str | None = None) -> None:
        """Create or replace one file on ``branch`` (Contents: write; Workflows: write for workflow files)."""
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha
        await self._call("PUT", self._repo(f"/contents/{path}"), json=body)

    async def open_pull(self, branch: str) -> PullRequest | None:
        """The open pull request from ``branch``, if there is one."""
        _, body = await self._call(
            "GET",
            self._repo("/pulls"),
            params={"state": "open", "head": f"{self.owner}:{branch}", "per_page": 5},
        )
        items = body if isinstance(body, list) else []
        return _pull(items[0]) if items else None

    async def create_pull(self, *, title: str, head: str, base: str, body: str) -> PullRequest:
        _, created = await self._call(
            "POST",
            self._repo("/pulls"),
            json={"title": title[:250], "head": head, "base": base, "body": body, "draft": False},
        )
        return _pull(created)

    async def mark_ready_for_review(self, pull: PullRequest) -> None:
        """Draft pull requests cannot be merged; the GraphQL API is the only way to undraft."""
        query = (
            "mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) "
            "{ pullRequest { isDraft } } }"
        )
        _, body = await self._call(
            "POST", "", url=self._graphql_url(), json={"query": query, "variables": {"id": pull.node_id}}
        )
        if isinstance(body, dict) and body.get("errors"):
            first = body["errors"][0] if isinstance(body["errors"], list) and body["errors"] else {}
            raise GitHubError(422, f"GitHub refused to mark the pull request ready: {str(first.get('message', ''))[:200]}")

    async def merge(self, number: int, sha: str, title: str) -> str:
        """Squash-merge, only if the head is still ``sha`` (GitHub answers 409 otherwise)."""
        _, body = await self._call(
            "PUT",
            self._repo(f"/pulls/{number}/merge"),
            json={"merge_method": "squash", "sha": sha, "commit_title": title[:250]},
        )
        return str((body or {}).get("sha") or "")

    async def cancel_run(self, run_id: int) -> None:
        # 409: the run already finished; nothing to cancel.
        await self._call("POST", self._repo(f"/actions/runs/{run_id}/cancel"), allow=frozenset({409}))

    async def close_pull(self, number: int) -> None:
        await self._call("PATCH", self._repo(f"/pulls/{number}"), json={"state": "closed"})

    async def delete_branch(self, branch: str) -> None:
        # 422/404: already gone.
        await self._call(
            "DELETE", self._repo(f"/git/refs/heads/{branch}"), allow=frozenset({404, 422})
        )


def _pull(item: Any) -> PullRequest:
    if not isinstance(item, dict):
        raise GitHubError(502, "GitHub returned an unexpected pull request.")
    return PullRequest(
        number=int(item["number"]),
        html_url=str(item.get("html_url") or ""),
        title=str(item.get("title") or ""),
        state=str(item.get("state") or "open"),
        merged=bool(item.get("merged") or item.get("merged_at")),
        draft=bool(item.get("draft")),
        head_sha=str((item.get("head") or {}).get("sha") or ""),
        node_id=str(item.get("node_id") or ""),
    )
