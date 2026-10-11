# Developer mode

The owner says or types a request such as "add a dark/light toggle to settings" and ANUM gets the app itself changed: a coding agent makes the change on a branch, runs the checks, opens a draft pull request and reports progress. Nothing merges until an owner approves it in ANUM, and only while every CI check on the pull request's head commit passes.

No code runs on the ANUM servers (or the Oracle VM). Coding runs in GitHub Actions with the official [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action), pinned to a commit SHA (v1.0.248) in `.github/workflows/anum-developer.yml`.

## Flow

1. **Request.** An owner sends text from the Developer view, or starts a chat message or a spoken sentence with `developer`, `build` or `Anum build`. Chat and voice first answer "I'll ask the developer to: … Start?" and create the request only after Start (or a spoken "yes"). Non-owners get a polite refusal.
2. **Dispatch.** `POST /api/v1/developer/requests` first admits the request and commits its `developer_requests` row (see [Admission](#admission)), then sends a `repository_dispatch` of type `anum-dev-request` with the request id and text. If GitHub refuses the dispatch, or the API stops while sending it, the row is marked `failed` with the reason (`Not sent to GitHub: …`) so it blocks nothing, and nothing is charged to the model budget.
3. **Coding.** The workflow's `code` job checks out the base branch, creates `anum-dev/<request id>`, installs dependencies and runs the agent with `CLAUDE.md`, the definition of done and the `anum-verify` checks as its instructions. The agent commits locally; it has a read-only `GITHUB_TOKEN` and cannot push.
4. **Publish.** The `publish` job loads the commits from a git bundle, refuses protected paths, pushes with `ANUM_DEV_PUSH_TOKEN` (a token from a person, so the pull request triggers CI) and opens a **draft** pull request that names the request id.
5. **Progress.** `GET /api/v1/developer/requests` reads the workflow run (by its `run-name`, `anum-dev <id>`), the pull request (by branch) and CI (every Actions run on the head commit except this workflow, plus commit statuses) from GitHub. Results are cached for `ANUM_DEVELOPER_STATUS_CACHE_SECONDS` (15 s) and stored on the row, so the list still shows the last known state when GitHub is unreachable.
6. **Approve and merge.** `POST /api/v1/developer/requests/{id}/merge-approval` (allowed only while CI is green) creates a task whose run is checkpointed at the high-risk tool call `developer.merge_pull_request` with the repository, pull request number and **head commit SHA** as arguments. The runtime pauses it for approval like any other high-risk call: the approval shows the tool, arguments, target (`api.github.com`) and payload hash, and workspace rules (two-person rule, approval chains, policy-pack denies) apply. Approving goes through `POST /api/v1/approvals/{id}/approve` with the displayed `payload_hash`. The tool then re-reads the pull request and refuses unless it is still open, its head is still the approved SHA and every check passed; it undrafts the pull request and squash-merges it with `sha` set, so GitHub refuses too if the head moved. Deployment follows the repository's normal pipeline for `main`.

Statuses: `queued`, `coding`, `ci_running`, `ci_failed`, `waiting_approval`, `merged`, `failed`, `cancelled`. Each response carries a `timeline` (Queued, Coding, Pull request opened, CI checks, Waiting for approval, Merged), a one-sentence `summary` and, while one waits, the pending merge `approval`. A failed run names the failed workflow step (refused request, protected paths, no changes, push failed).

`POST /api/v1/developer/requests/{id}/cancel` cancels a running workflow, closes the pull request, deletes the branch and cancels a waiting merge task (its approval expires).

## API

All routes need `developer:manage`, which only the `owner` role holds.

| Route | Purpose |
|---|---|
| `GET /api/v1/developer/setup` | What is configured (repository, workflow, base branch, daily use) and the names of missing settings. Never the token. |
| `POST /api/v1/developer/requests` | `{text, source: "text"\|"voice", allow_protected_paths}` → 201. 409 while another request is queued or coding, 429 over the daily limit, 402 when the model budget is used up, 422 for protected topics without the allow flag, 502 when GitHub refuses. |
| `GET /api/v1/developer/requests?limit&before&q` | Newest first, keyset paging (`before` = last id), text search. |
| `GET /api/v1/developer/requests/{id}?refresh=true` | One request, refreshed from GitHub. |
| `POST /api/v1/developer/requests/{id}/merge-approval` | `{request, approval}`; returns the same pending approval while the head is unchanged. |
| `POST /api/v1/developer/requests/{id}/cancel` | Stop and clean up. |

Every step is audited: `developer.request.created`, `.refused`, `.dispatch_failed`, `.status_changed` (actor `system`), `.cancelled`, `developer.merge.requested`, `.blocked`, `.completed`, `.refused`, plus the usual `approval.*` records.

**Budget and rate limits.** Each request sent to GitHub counts as one budgeted model action of `ANUM_DEVELOPER_REQUEST_COST_USD` (default 1.00) against the tenant and workspace monthly budgets, and is refused (402) once they are used up. At most one request per workspace is queued or coding at a time (the workflow also runs one at a time) and at most `ANUM_DEVELOPER_MAX_REQUESTS_PER_DAY` (10) are created per workspace per day; a request GitHub refused stays in the list as `failed` and still counts toward the day. The coding run itself is billed to the `ANTHROPIC_API_KEY` in GitHub.

### Admission

The "one queued or coding request" and daily-limit checks and the insert are one atomic step, committed before anything is sent to GitHub, so two concurrent creates (two tabs, a chat and a voice confirmation) cannot both pass and both dispatch:

- **PostgreSQL:** in the request's tenant and workspace RLS context, the transaction takes `pg_advisory_xact_lock` on a key derived from the tenant and workspace, then counts and inserts. A concurrent create for the same workspace waits for that commit and then sees the new row (409). The partial unique index `uq_developer_requests_one_active` on `(tenant_id, workspace_id) where status in ('queued', 'coding')` (migration `0023_developer_one_active`) enforces the same rule for any writer that skips the lock.
- **Memory backend:** the same checks and the insert run under the store's lock.

A request never returns to `queued` or `coding` once it left them (a pull request briefly missing from GitHub's answer keeps the last status). If the API dies between admitting a request and marking a failed dispatch, the request stays `queued` until the next status refresh after 30 minutes without a workflow run fails it ("The developer workflow never started").

## Clients

- **Web:** the Developer view (owners only; hidden in the navigation for everyone else, and an explanation if opened by link) lists requests with the status timeline, pull request and run links, CI summary, Approve & merge (opens the approval with its payload hash, then Confirm merge), Cancel, search and paging. It shows the setup state with the missing setting names, and an empty state with example requests. It refreshes every 20 s while something is moving.
- **Chat:** the Assistant intercepts developer commands before they reach the model and shows the confirmation card.
- **Voice:** the same commands, confirmed by "yes"/"start". When a pull request becomes ready ANUM says "The change is ready for review: …. Say approve to merge." Saying "approve" opens the merge approval with its hash on screen; the merge still needs a tap on Confirm merge. Voice never approves by itself.
- **Flutter (Android, iOS, tablets):** the same three surfaces, over the same routes ([Mobile](mobile.md#developer-mode)).
  - *Developer screen* (`lib/features/developer/`): listed in the navigation only for an active owner (`GET /api/v1/workspace-memberships/current` says `owner`; last in the rail, under More on phones); a deep link (`/developer`) for anyone else, or a `403` from the API, explains what it is and who can use it. It shows the setup state with the missing setting names (never values), the request form (the protected-topics tick, today's use, one request at a time), search (`q`) and keyset paging (Load older requests), and an empty state with example requests. A request opens a detail page with the timeline, the one-line summary, the CI summary, failure and merge errors, Pull request and Coding run links (opened in the browser through `url_launcher`; only `https` links), Check now, Cancel (after a confirmation) and Approve & merge. Approve & merge only calls `merge-approval` and shows the approval: tool, arguments, target and the full payload hash; the merge happens only on Confirm merge, which sends `POST /api/v1/approvals/{id}/approve` with `{"payload_hash": …}` exactly as the Approvals screen does, then refreshes the request. It refreshes every 20 s while something is moving and every 60 s while a merge waits, only while it is on screen.
  - *Assistant:* the composer sends "build …", "developer, …" and "Anum build …" to developer mode instead of the model and shows the "Start?" card above the composer (Start or Cancel). Non-owners get the polite refusal; nothing is sent.
  - *Voice:* the answer to "Start?" is spoken and, with wake by name on, the app listens once for "yes"/"no" without the name. A ready pull request is announced while Voice is on screen; "approve" opens the approval with its payload hash on screen, and only a tap on Confirm merge approves it.

## Setup (one time, owner)

In GitHub (repository → Settings → Secrets and variables → Actions):

- Secret `ANTHROPIC_API_KEY`: the Anthropic API key the coding agent uses.
- Secret `ANUM_DEV_PUSH_TOKEN`: a fine-grained personal access token, **this repository only**, with Contents and Pull requests read and write. The publish job pushes and opens the pull request with it (pushes with the workflow's own token would not trigger CI).
- Optional variable `ANUM_DEV_ALLOW_PROTECTED_PATHS=true` to allow requests that change CI workflows or secret handling at all (each such request must also opt in). Pushing workflow files additionally needs the token's Workflows permission.
- Optional: create the issue label `anum-dev`; labelling an issue with it starts the same workflow (GitHub only; such requests are not tracked in ANUM).

In the API's environment (Render dashboard environment, Helm values secret, or `infra/oracle/.env`):

- `ANUM_DEVELOPER_GITHUB_REPO` = `owner/repo`.
- `ANUM_DEVELOPER_GITHUB_TOKEN`: a second fine-grained token, **this repository only**, with Contents read and write (dispatch, branch delete), Pull requests read and write (status, undraft, merge, close), Actions read and write (run status, cancel) and Commit statuses read. Keep it in the platform's secret store; it is read from the environment and never returned by the API or logged.
- `ANUM_DEVELOPER_TENANT_ID` (and optionally `ANUM_DEVELOPER_WORKSPACE_ID`): the only organization (workspace) allowed to use developer mode. The repository is the operator's own; no other tenant can reach it.
- Optional: `ANUM_DEVELOPER_BASE_BRANCH` (`main`), `ANUM_DEVELOPER_WORKFLOW_FILE` (`anum-developer.yml`), `ANUM_DEVELOPER_GITHUB_API_URL`, `ANUM_DEVELOPER_MAX_REQUESTS_PER_DAY`, `ANUM_DEVELOPER_REQUEST_COST_USD`, `ANUM_DEVELOPER_STATUS_CACHE_SECONDS`.

Missing or invalid settings never stop the API; the Developer view shows the setup state with their names.

## Threat model

| Threat | Mitigation |
|---|---|
| Prompt injection in the request text (or an issue body) steering the agent | The text is data in a file the prompt names, never shell-interpolated. The agent runs with a read-only token, a limited tool allowlist (no web fetch, git limited to status/diff/add/commit/log/show) and no push credential; the publish job refuses protected paths (`.github/`, `.claude/`, `CLAUDE.md`, `.gitleaks.toml`, `.env` files except `.env.example`, `secret_box.py`, `rotate_secrets.py`) and more than 300 files. Everything lands as a draft pull request that CI and a person review. |
| Requests that target CI or secrets | Refused by the API (422) and again by the workflow unless the owner opts in per request **and** the repository variable allows it. |
| Leaking the GitHub token | Both tokens are scoped to one repository. The API token lives only in the environment as a secret string, goes only into the `Authorization` header and never into responses, audit metadata or logs (errors carry GitHub's status and short message). The push token exists only in the publish job, which runs no repository code. |
| Merging unreviewed or failing code | Merge only through the high-risk tool after an approval bound by payload hash to the head SHA; checks are re-read at merge time; GitHub's own `sha` check refuses a moved head. Governance rules and the two-person rule apply. |
| Other tenants using the operator's repository | `ANUM_DEVELOPER_TENANT_ID` (and workspace) gate every route; rows are tenant- and workspace-scoped under forced RLS (migration `0022_developer_requests`). |
| Runaway cost | One active request (admitted atomically, backed by a unique index), a daily cap, the monthly model budget, a 75-minute job timeout and a 150-turn agent limit. |

## Storage

`developer_requests` (migration `0022_developer_requests`, expand-only): request text and source, status, branch, run and pull request ids and links, head SHA, CI summary (JSONB), failure and merge errors, merge approval and task ids, merge commit, timestamps. Forced RLS on `anum.tenant_id` and `anum.workspace_id`; no other role is granted anything. The migration was written on a parallel branch chained after `0020_tenants_rls` and must be re-chained onto the newest migration when it lands.

`0023_developer_one_active` (expand-only) builds `uq_developer_requests_one_active` `CONCURRENTLY`. Before that, any workspace that already holds more than one queued or coding request (left by the admission race this revision closes) keeps its newest one and the others are marked `failed` ("Not started: another request for this workspace was already queued or coding."). The migration login sets no tenant context, so that one update runs with RLS not forced for the table owner inside the migration transaction, which forces it again, as `0007_event_outbox` does; no role is granted anything.

## Not covered yet

- Requests started from GitHub issues are not tracked in ANUM.
- Deploy after merge is the repository's existing pipeline; ANUM does not report deploy progress.
