# Developer mode

The owner says or types a request such as "add a dark/light toggle to settings in TyrePulse" and ANUM gets that app changed: a coding agent makes the change on a branch of the project's GitHub repository, runs the checks, opens a draft pull request and reports progress. Nothing merges until an owner approves it in ANUM, and only while every CI check on the pull request's head commit passes.

Developer mode works on **projects**: the GitHub repositories (ANUM itself, TyrePulse, any other app) the workspace's owners let the developer change. Each project has its own repository, branch, token and protected paths; requests, status, cancel and merge all follow the request's project.

No code runs on the ANUM servers (or the Oracle VM). Coding runs in each repository's GitHub Actions with the official [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action), pinned to a commit SHA (v1.0.248).

## Projects

A project (`developer_projects`, migration `0032_developer_projects`) holds:

| Field | Meaning |
|---|---|
| Name | What the owner calls it ("TyrePulse"); unique per workspace, case-insensitive. Chat and voice requests are routed by it. |
| GitHub owner / repository | `owner/repo`; unique per workspace. The form also accepts the repository's `github.com` address. |
| Branch to build on | The base branch requests start from and pull requests target (`main`). |
| Short description | One sentence about the app. A request that names no project is routed by it (always confirmed first). |
| Enabled | A turned-off project takes no requests and no merges; its status still refreshes and Cancel still works. |
| Protected paths | Extra files or directories (`docs/legal/`, `src/billing.py`) the developer never changes, added to the built-in list. A path protects itself and everything under it; no wildcards, no `..`. |
| Workflow file | `anum-developer.yml` unless the repository uses another name. |
| Token | Either **a token for this repository** (a fine-grained token, stored Fernet-encrypted with `ANUM_SECRETS_KEY`, never returned, logged or audited; the API only says whether one is set) or **the API's token** (`ANUM_DEVELOPER_GITHUB_TOKEN`, one fine-grained token covering selected repositories, kept in the platform's secret store). |

Owners manage projects in the web Developer view (**Projects** section) and the Flutter Developer screen (**Projects**): add, edit, **Test connection**, **Install workflow**, turn off, delete. Every change is audited (`developer.project.created`, `.updated`, `.deleted`, `.tested`, `.workflow_install_requested`, `.workflow_install_failed`); audit metadata names the project and repository and only says whether the token changed.

- **Test connection** checks, with the project's token, that the repository is reachable, the branch exists, the workflow file is on that branch and the token can read Actions. The result is stored on the project and shown until the next test; a failing result marks the project "Needs attention". Sending a request to a project whose last test failed tests it again first, so a fixed repository works without an extra click.
- **Install workflow** opens a pull request in the project's repository that adds the coding workflow (branch `anum-dev-setup/workflow`, file `.github/workflows/<workflow file>`), with a body listing the secrets to add. The owner reviews and merges it in GitHub like any other change to CI; ANUM never merges it. Asking again returns the pull request that is already open. It needs the token's Workflows permission.
- **Delete** is refused while the project has unfinished requests (cancel or merge them, or turn the project off). Finished requests stay listed under the project's name and repository, which every request keeps from when it was sent.
- A workspace has at most `ANUM_DEVELOPER_MAX_PROJECTS` (100) projects; the list has a search.

### The coding workflow

Each repository needs a workflow that answers `repository_dispatch` events of type `anum-dev-request`. ANUM ships a self-contained template, `services/api/anum_api/workflow_templates/anum-developer.yml` (also served to owners by `GET /api/v1/developer/workflow-template`, and copied by **Copy the workflow file** in the web Projects section). The ANUM repository keeps its own variant, `.github/workflows/anum-developer.yml`, which installs ANUM's toolchain and follows `CLAUDE.md`. Both have the same safety model:

- Every action is pinned to a commit SHA.
- `code` job: validates the request, refuses protected topics, checks out the base branch, installs dependencies (the template detects `pnpm-lock.yaml`, `yarn.lock`, `package-lock.json`, `requirements*.txt`; adjust that step for other stacks), and runs the agent with a **read-only** `GITHUB_TOKEN`, a command allowlist and no web fetch. The agent commits locally; its commits leave the job only as a git bundle.
- `publish` job: runs no repository code. It loads the bundle, refuses more than 300 files and protected paths, then pushes with `ANUM_DEV_PUSH_TOKEN` and opens a **draft** pull request.
- Protected paths: `.github/`, `.claude/`, `CLAUDE.md`, `.gitleaks.toml`, `.env` files (not `.env.example`) (the ANUM variant also `secret_box.py` and `rotate_secrets.py`), plus the project's protected paths (read by the publish job from the dispatch event, never from the coding job) and the repository variable `ANUM_DEV_PROTECTED_PATHS`. They can be changed only when the owner allows it on the request **and** the repository variable `ANUM_DEV_ALLOW_PROTECTED_PATHS=true` is set.

### Set up a repository (owner, once per project)

1. **Create the tokens** (GitHub → Settings → Developer settings → Fine-grained tokens):
   - *ANUM's token* for this repository (or add this repository to the token behind `ANUM_DEVELOPER_GITHUB_TOKEN`): Contents read and write, Pull requests read and write, Actions read and write, Commit statuses read, and Workflows read and write (only needed for Install workflow).
   - *Push token* for this repository only: Contents and Pull requests read and write.
2. **Add the project** in ANUM → Developer → Projects: name, repository, branch, a one-sentence description, optional protected paths, and the token (paste it once; it is never shown again) or "The API's token".
3. **Install the workflow**: press Install workflow (or copy the workflow file into `.github/workflows/anum-developer.yml` yourself).
4. **Add the repository secrets** (repository → Settings → Secrets and variables → Actions), names only:
   - `ANTHROPIC_API_KEY`: the Anthropic API key the coding agent uses (the coding run is billed to it).
   - `ANUM_DEV_PUSH_TOKEN`: the push token from step 1 (pushes with the workflow's own token would not trigger CI).
   - Optional variables: `ANUM_DEV_PROTECTED_PATHS` (comma-separated paths), `ANUM_DEV_ALLOW_PROTECTED_PATHS=true`.
5. **Merge the workflow pull request** in GitHub, then press **Test connection**. The project shows "Ready".

## Flow

1. **Request.** An owner picks the project and types the request in the Developer view, or starts a chat message or a spoken sentence with `developer`, `build`, `Anum build` or "ANUM, in <project> …". Chat and voice first ask the API which project it is (`POST /api/v1/developer/resolve`: "in TyrePulse" at the start or the end, the project's name anywhere, else the description, else the only ready project). They **always** confirm "I'll ask the developer to: … in <project>. Start?" and create the request only after Start (or a spoken "yes"). When the project is not clear they ask "Which project is this for: ANUM or TyrePulse?" (buttons, or a spoken name), then confirm. Non-owners get a polite refusal.
2. **Dispatch.** `POST /api/v1/developer/requests` checks the project (enabled, a token, a passing or fresh connection test), admits the request and commits its row (see [Admission](#admission)), then sends a `repository_dispatch` of type `anum-dev-request` to the project's repository with the request id, text, the project's branch and its protected paths. If GitHub refuses the dispatch, or the API stops while sending it, the row is marked `failed` with the reason (`Not sent to GitHub: …`) so it blocks nothing, and nothing is charged to the model budget.
3. **Coding.** The workflow's `code` job creates `anum-dev/<request id>` and runs the agent. 
4. **Publish.** The `publish` job checks the commits and opens a **draft** pull request that names the request id.
5. **Progress.** `GET /api/v1/developer/requests` reads the workflow run (by its `run-name`, `anum-dev <id>`), the pull request (by branch) and CI (every Actions run on the head commit except this workflow, plus commit statuses) from the request's repository with the project's token. Results are cached for `ANUM_DEVELOPER_STATUS_CACHE_SECONDS` (15 s) and stored on the row, so the list still shows the last known state when GitHub is unreachable.
6. **Approve and merge.** `POST /api/v1/developer/requests/{id}/merge-approval` (allowed only while CI is green and the project is turned on) creates a task whose run is checkpointed at the high-risk tool call `developer.merge_pull_request` with the project id, **repository**, pull request number and **head commit SHA** as arguments. The runtime pauses it for approval like any other high-risk call: the approval shows the tool, arguments, target (`api.github.com`) and payload hash, and workspace rules (two-person rule, approval chains, policy-pack denies) apply. Approving goes through `POST /api/v1/approvals/{id}/approve` with the displayed `payload_hash`. The tool then refuses unless the request's project still points at the approved repository and is turned on, the pull request is still open, its head is still the approved SHA and every check passed; it undrafts the pull request and squash-merges it with `sha` set, so GitHub refuses too if the head moved. Deployment follows the repository's own pipeline.

Statuses: `queued`, `coding`, `ci_running`, `ci_failed`, `waiting_approval`, `merged`, `failed`, `cancelled`. Each response carries a `timeline` (Queued, Coding, Pull request opened, CI checks, Waiting for approval, Merged), a one-sentence `summary`, the project (`project_id`, `project_name`, `repository`) and, while one waits, the pending merge `approval`. A failed run names the failed workflow step (refused request, dependencies, protected paths, no changes, push failed).

`POST /api/v1/developer/requests/{id}/cancel` cancels a running workflow, closes the pull request, deletes the branch and cancels a waiting merge task (its approval expires).

## API

All routes need `developer:manage`, which only the `owner` role holds.

| Route | Purpose |
|---|---|
| `GET /api/v1/developer/setup` | What is configured (tenant gate, projects and ready projects, daily use, whether `ANUM_DEVELOPER_GITHUB_TOKEN` is set) and the names of missing settings. Never a token. |
| `GET /api/v1/developer/projects?q` | Projects by name; `q` searches name, repository and description. |
| `POST /api/v1/developer/projects` | `{name, github_owner, github_repo, default_branch, description, enabled, protected_paths, workflow_file, token_source: "project"\|"environment", token?}` → 201. 409 for a duplicate name or repository or over the project limit, 422 for invalid fields. |
| `GET /api/v1/developer/projects/{id}` | One project. |
| `PATCH /api/v1/developer/projects/{id}` | Any field; `token` replaces the stored token, `clear_token` removes it. Changing the repository, branch, workflow or token forgets the last test. |
| `DELETE /api/v1/developer/projects/{id}` | 204; 409 while the project has unfinished requests. |
| `POST /api/v1/developer/projects/{id}/test` | Test connection; returns the project with `last_check`. |
| `POST /api/v1/developer/projects/{id}/install-workflow` | `{project, pull_request_number, pull_request_url, created}`; 409 when the workflow is already on the branch, 502 with the missing permissions when GitHub refuses. |
| `GET /api/v1/developer/workflow-template` | The template's content and the secret and variable names. |
| `POST /api/v1/developer/resolve` | `{text}` → `{status: "resolved"\|"ambiguous"\|"none", text, project, candidates, matched_by}`. Creates nothing. |
| `POST /api/v1/developer/requests` | `{text, source: "text"\|"voice", allow_protected_paths, project_id}` → 201. Without `project_id` only when exactly one project is ready (422 otherwise). 409 while the project has a queued or coding request, or the project is off, has no token or fails its test; 429 over the daily limit, 402 when the model budget is used up, 422 for protected topics without the allow flag, 502 when GitHub refuses. |
| `GET /api/v1/developer/requests?limit&before&q&project_id` | Newest first, keyset paging (`before` = last id), text search, one project. |
| `GET /api/v1/developer/requests/{id}?refresh=true` | One request, refreshed from GitHub. |
| `POST /api/v1/developer/requests/{id}/merge-approval` | `{request, approval}`; returns the same pending approval while the head and repository are unchanged. |
| `POST /api/v1/developer/requests/{id}/cancel` | Stop and clean up. |

Every step is audited: `developer.request.created`, `.refused`, `.dispatch_failed`, `.status_changed` (actor `system`), `.cancelled`, `.cancel_failed`, `developer.merge.requested`, `.blocked`, `.completed`, `.refused`, the `developer.project.*` records above, plus the usual `approval.*` records.

**Budget and rate limits.** Each request sent to GitHub counts as one budgeted model action of `ANUM_DEVELOPER_REQUEST_COST_USD` (default 1.00) against the tenant and workspace monthly budgets, and is refused (402) once they are used up. At most one request **per project** is queued or coding at a time (each repository's workflow also runs one at a time) and at most `ANUM_DEVELOPER_MAX_REQUESTS_PER_DAY` (10) are created per workspace per day across its projects; a request GitHub refused stays in the list as `failed` and still counts toward the day.

### Admission

The "one queued or coding request per project" and daily-limit checks and the insert are one atomic step, committed before anything is sent to GitHub, so two concurrent creates (two tabs, a chat and a voice confirmation) cannot both pass and both dispatch:

- **PostgreSQL:** in the request's tenant and workspace RLS context, the transaction takes `pg_advisory_xact_lock` on a key derived from the tenant and workspace (the same key as before projects, so an older release and this one wait for each other during a rollout), then checks the project's active request and the workspace's daily count and inserts. The partial unique index `uq_developer_requests_one_active_project` on `(tenant_id, workspace_id, coalesce(project_id, ''))` where status is `queued` or `coding` (migration `0031`) enforces the same rule for any writer that skips the lock; it replaced the per-workspace `uq_developer_requests_one_active` (`0023`).
- **Memory backend:** the same checks and the insert run under the store's lock.

A request never returns to `queued` or `coding` once it left them. If the API dies between admitting a request and marking a failed dispatch, the request stays `queued` until the next status refresh after 30 minutes without a workflow run fails it ("The developer workflow never started").

## Clients

- **Web:** the Developer view (owners only; hidden in the navigation for everyone else, and an explanation if opened by link) has two sections. **Requests**: the request form with the project picker (preselected when only one project is ready, else the one used last in this browser), today's use, one request at a time per project; the list with the project on each card, status timeline, pull request and run links, CI summary, Approve & merge (opens the approval with its payload hash, then Confirm merge), Cancel, search, a project filter and paging. **Projects**: the cards with state (Ready, Not tested, Needs attention, Turned off), token kind, protected paths, last test and its problems, and the actions above; the add/edit form with field-level errors; what each repository needs and Copy the workflow file. Without a ready project the Requests section leads to Projects. It is in English and Arabic ([Web localization](web-localization.md)).
- **Chat:** the Assistant intercepts developer commands before they reach the model, resolves the project and shows the confirmation card (or the project choice).
- **Voice:** the same commands, confirmed by "yes"/"start", the project chosen by saying its name. When a pull request becomes ready ANUM says "The change is ready for review in <project>: …. Say approve to merge." Saying "approve" opens the merge approval with its hash on screen; the merge still needs a tap on Confirm merge. Voice never approves by itself.
- **Flutter (Android, iOS, tablets):** the same surfaces over the same routes ([Mobile](mobile.md#developer-mode)).
  - *Developer screen* (`lib/features/developer/`): listed in the navigation only for an active owner; a deep link (`/developer`) for anyone else, or a `403` from the API, explains what it is and who can use it. It shows the setup state with the missing setting names (never values), a Projects row (count and ready count) that opens the **Projects** screen, the request form with the project picker, search (`q`) and keyset paging, and an empty state. Without a ready project it shows "Add a project first" leading to Projects. A request opens a detail page with its project and repository, the timeline, the summary, CI, failure and merge errors, links (opened in the browser; only `https`), Check now, Cancel (after a confirmation) and Approve & merge (the approval with its full payload hash; the merge happens only on Confirm merge, which sends `POST /api/v1/approvals/{id}/approve` with `{"payload_hash": …}`). It refreshes every 20 s while something is moving and every 60 s while a merge waits, only while on screen.
  - *Projects screen* (`developer_projects_screen.dart`): the project cards with Test connection, Install workflow, Edit, Turn off/on and Delete (after a confirmation), what each repository needs, a search when there are many, and the add/edit form page (write-only token field, "Remove the stored token").
  - *Assistant and Voice:* the same resolution and confirmation as the web; the choice is offered as buttons, and by voice the app listens once for the project name or "yes"/"no".

## Deployment settings

In the API's environment (Render dashboard environment, Helm values secret, or `infra/oracle/.env`):

- `ANUM_DEVELOPER_TENANT_ID` (and optionally `ANUM_DEVELOPER_WORKSPACE_ID`): the only organization (workspace) allowed to use developer mode. Required.
- Optional `ANUM_DEVELOPER_GITHUB_TOKEN`: the token for projects that use "The API's token": one fine-grained token covering the selected repositories, with the permissions in [Set up a repository](#set-up-a-repository-owner-once-per-project). Kept in the platform's secret store; read from the environment and never returned by the API or logged.
- Optional `ANUM_DEVELOPER_GITHUB_REPO` = `owner/repo`: the single repository from before projects. Migration `0031` turns it into the first project (below); requests stored without a project still resolve to it.
- Optional: `ANUM_DEVELOPER_BASE_BRANCH` (`main`) and `ANUM_DEVELOPER_WORKFLOW_FILE` (`anum-developer.yml`) for that repository, `ANUM_DEVELOPER_GITHUB_API_URL`, `ANUM_DEVELOPER_MAX_REQUESTS_PER_DAY`, `ANUM_DEVELOPER_MAX_PROJECTS`, `ANUM_DEVELOPER_REQUEST_COST_USD`, `ANUM_DEVELOPER_STATUS_CACHE_SECONDS`.
- `ANUM_SECRETS_KEY` encrypts per-project tokens; `python -m anum_api.rotate_secrets` re-encrypts them with the model provider keys ([Runbooks](runbooks.md#rotating-anum_secrets_key)).

Missing or invalid settings never stop the API; the Developer view shows the setup state with their names.

### Upgrading from one repository

When migration `0032_developer_projects` runs with `ANUM_DEVELOPER_GITHUB_REPO` and `ANUM_DEVELOPER_TENANT_ID` in its environment (the migration runs with the API's environment on Render, Helm and the Oracle stack), that repository becomes a project in every workspace of that tenant (only `ANUM_DEVELOPER_WORKSPACE_ID` when set), named after the repository, using the API's token (no token is copied), and existing requests are attached to it. It runs inside each tenant and workspace's RLS context and inserts nothing twice. If the migration ran without those settings, add the project in Projects and choose "The API's token". The ANUM repository's own workflow keeps working unchanged.

## Threat model

| Threat | Mitigation |
|---|---|
| Prompt injection in the request text steering the agent | The text is data in a file the prompt names, never shell-interpolated. The agent runs with a read-only token, a limited command allowlist (no web fetch, git limited to status/diff/add/commit/log/show) and no push credential; the publish job refuses protected paths (built-in, the project's, the repository variable) and more than 300 files. Everything lands as a draft pull request that CI and a person review. |
| Requests that target CI or secrets | Refused by the API (422) and again by the workflow unless the owner opts in per request **and** the repository variable allows it. |
| Leaking a GitHub token | Per-project tokens are stored only as Fernet ciphertext under forced RLS, decrypted only to build the `Authorization` header, never returned, logged or audited; key rotation covers them and the maintenance role sees only the ids of rows that hold one. Errors carry GitHub's status and short message only. Tokens should cover only the selected repositories. The push token exists only in the publish job, which runs no repository code. |
| Sending a request or a merge to the wrong repository | The confirmation always names the project; the approval's arguments (and so its payload hash) include the repository and project; the merge tool refuses if the request's project no longer points at that repository or is turned off. |
| Merging unreviewed or failing code | Merge only through the high-risk tool after an approval bound by payload hash to the head SHA; checks are re-read at merge time; GitHub's own `sha` check refuses a moved head. Governance rules and the two-person rule apply. The workflow installed by Install workflow is merged by a person in GitHub. |
| Other tenants using developer mode | `ANUM_DEVELOPER_TENANT_ID` (and workspace) gate every route; projects and requests are tenant- and workspace-scoped under forced RLS (migrations `0022`, `0031`). |
| Runaway cost | One active request per project (admitted atomically, backed by a unique index), a daily cap per workspace, the monthly model budget, a 75-minute job timeout and a 150-turn agent limit. |

## Storage

- `developer_requests` (migration `0022_developer_requests`, expand-only): request text and source, status, branch, run and pull request ids and links, head SHA, CI summary (JSONB), failure and merge errors, merge approval and task ids, merge commit, timestamps; since `0031`, `project_id`, `project_name` and `repository`. Forced RLS on `anum.tenant_id` and `anum.workspace_id`.
- `0023_developer_one_active` built the per-workspace one-active index after failing all but the newest active request per workspace; `0031` replaced it with the per-project index (built and dropped `CONCURRENTLY`). The previous release keeps working: its admission takes the same lock and is stricter.
- `developer_projects` (migration `0032_developer_projects`): the fields above, `token_ciphertext`, `last_check` (JSONB), the install pull request; unique name and repository per workspace; forced RLS; `anum_maintenance` may select `id`, `tenant_id` and `workspace_id` of rows with a ciphertext (key rotation discovery) and nothing else.

## Not covered yet

- Requests started from GitHub issues (the ANUM workflow's `anum-dev` label) are not tracked in ANUM.
- Deploy after merge is each repository's own pipeline; ANUM does not report deploy progress.
- The daily cap is per workspace, not across the tenant's workspaces (rows are workspace-scoped under RLS). Set `ANUM_DEVELOPER_WORKSPACE_ID` to keep developer mode in one workspace.
- The Install workflow pull request is merged in GitHub, not through ANUM's approvals.
- The Flutter developer screens are in English only (the web is English and Arabic).
