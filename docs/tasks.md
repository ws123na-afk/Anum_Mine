# Tasks and agent runs

A task is the unit of work a person asks the agent to do; an agent run is one attempt at it ([Agent runtime](agent-runtime.md)). This page is the contract for the Tasks module: the API, who may do what, and what the web Tasks view and the Flutter Tasks screens offer. Everything is scoped to the caller's tenant and workspace (row-level security in PostgreSQL, explicit scope checks in the in-memory backend); a task in another workspace answers `404`, never `403`.

## API

All paths are under `/api/v1`. Lists are newest first and keyset-paged: `cursor` is the `id` of the last row already shown, and a cursor that names no row in the caller's scope answers `400` rather than an empty page.

| Method and path | Permission | What it does |
| --- | --- | --- |
| `POST /tasks` | `task:create` | Create a task (`title` 1-160, `prompt` 1-8000 characters; optional `priority` `low` / `medium` / `high`, default `medium`, and `due_at`; optional `agent_id` to run it as that [agent](agents.md#running-tasks-as-an-agent); `404` if the caller cannot see the agent, `409` if it is paused). |
| `GET /tasks` | `task:read` | List tasks. Filters below (and `agent_id`: only tasks run as that agent); `limit` 1-100 (default 50). |
| `GET /tasks/summary` | `task:read` | `{total, by_status, archived, creators}` for the same filters except `status`. |
| `GET /tasks/{id}` | `task:read` | One task. |
| `PUT /tasks/{id}` | creator or owner | Rename (`title`), change `priority` or `due_at` (`"due_at": null` removes it) and, while the task has never run, change its `prompt`. |
| `POST /tasks/{id}/run` | `task:run` | Plan and run a task that has not run yet (`created` or `queued`). |
| `POST /tasks/{id}/retry` | `task:run` | Start a new run of a `failed` or `cancelled` task. |
| `POST /tasks/{id}/cancel` | `task:cancel` | Cancel a task that is not finished; its pending approvals expire. |
| `POST /tasks/{id}/archive` | creator or owner | Hide a finished (or never run) task from the default list. |
| `POST /tasks/{id}/unarchive` | creator or owner | Return an archived task to the default list. |
| `POST /tasks/bulk` | per task, as its single route | `{action: archive \| unarchive \| cancel, task_ids: [...]}`, 1-100 unique ids. |
| `GET /tasks/{id}/runs` | `task:read` | Every run with its steps; `limit` 1-100 (default 20). |
| `GET /tasks/{id}/latest-run` | `task:read` | The newest run; `404` when the task never ran. |
| `GET /tasks/{id}/events` | `task:read` and `event:read` | The task's domain events (correlation id = task id); `limit` 1-200 (default 50). |
| `GET /tasks/{id}/export?format=json\|markdown` | `task:read` | Download the run log (below). |
| `GET /agent-runs/{run_id}` | `task:read` | One run. |
| `POST /agent-runs/{run_id}/resume` | `task:run` | Resume a run stranded at an executable checkpoint (a worker stopped mid-run). |

### List filters

`GET /tasks` and `GET /tasks/summary` take:

- `status`: repeatable (`status=failed&status=cancelled`); empty means every status.
- `q`: up to 200 characters, matched case-insensitively and literally (`%` and `_` are not wildcards) against the title, instructions and id.
- `created_by`: a user id, or `me` for the caller.
- `created_after` (inclusive) and `created_before` (exclusive): ISO 8601 date-times; a value without an offset is UTC. `created_after` must be earlier than `created_before` or the API answers `400`.
- `archived`: `false` (default) lists the active list; `true` lists only archived tasks.

PostgreSQL pages on `(created_at, id)` with `ix_tasks_scope_created` (0017); the search is an `ILIKE` over the filtered, keyset-limited scan. A trigram index is the next step if workspaces grow past what that serves.

### Permissions

Roles map to permissions in `services/api/anum_api/authorization.py`:

- Viewers read tasks, runs, events and exports.
- Members also create, run and retry tasks, and rename, edit and archive **tasks they created**.
- Owners also cancel any task and rename, edit and archive **any** task (`task:manage`).

A refusal is `403` with a sentence that names who can act, for example "Only the task's creator or a workspace owner can change this task." Bulk actions check each task exactly as its single route does and report per task (`status_code`, `message`); one refused task never fails the others.

### Editing

A task's title, priority and due date can change at any time while it is not archived. Its instructions are fixed once it has run (`409`): the run history must stay true to what was asked, and retry runs the same instructions. Edits record `task.updated` with the changed field names only (never the instructions) and an audit record.

### Priority and due date

`priority` (`low`, `medium`, `high`) and `due_at` (UTC; a date-time without an offset is read as UTC) are planning fields from migration `0048_task_planning` (two nullable columns, expand only; a task stored before it reads as `medium` with no due date). They are shown on the lists and edited with `PUT`; they never change what an agent may do, when it runs or which approvals it needs, and the list stays newest first. Changes record `task.updated` with the field names `priority` / `due_at`.

### Archive

Archiving sets `archived_at` and `archived_by` (migration `0026_task_archive`, expand-only) and never deletes runs, approvals, events or memories; the privacy and retention jobs remain the only deletion paths ([Privacy](privacy.md)). Only finished tasks, or tasks that never ran, can be archived; an active task answers `409` ("Cancel the task or let it finish before archiving it"). An archived task cannot be edited, run, retried or cancelled until it is restored. Both directions record an event (`task.archived`, `task.unarchived`) and an audit record.

### Retry

Retry creates a new run for a `failed` or `cancelled` task; earlier runs, their steps and approvals stay in the history (`GET /tasks/{id}/runs`). It records `task.retried` with `previous_run_id`, is refused while the monthly model budget is used up (`402`), and holds the task's run lock like `run`. With `ANUM_RUNTIME_BACKEND=temporal` the new run is queued and its workflow started with `TERMINATE_EXISTING`: any workflow still open for the task can only be finishing the previous run. Approving an approval resumes that approval's own run (`approval.run_id`), so a retried task never resumes the wrong run.

`resume` is different: it continues a run stranded at `tool_ready` (a worker stopped between planning and execution). It refuses failed, cancelled, completed and waiting runs.

### Run-log export

`GET /tasks/{id}/export` is an attachment (`{task_id}-run-log.json` or `.md`, `Cache-Control: no-store`) holding the task, every run oldest first with its steps and checkpoint, the task's approvals (as shown to approvers, secret-looking arguments redacted) and its events. The checkpoint's planned tool-call arguments are redacted with the same rule as approvals. At most 500 runs and 2000 events are included; `truncated` says when a history was cut. Markdown puts every model or user value in a fenced block or code span, so exported text cannot inject Markdown structure. Each export records a `task.exported` audit record.

## Web (Tasks view)

`apps/web/src/TasksView.tsx`, helpers in `src/lib/tasks.ts` and `src/lib/tasksApi.ts`:

- Metrics from `GET /tasks/summary` (active, waiting approval, completed, failed or cancelled, archived).
- History with server-side search (debounced), status, creator, date range and "Show archived"; "Clear filters"; keyset "Load more tasks"; per-row checkboxes with "Select all loaded" (at most 100) and a bulk bar (Archive, Restore, and for owners Cancel with a confirm step).
- Task detail: Run (never run), Retry (failed or cancelled), Resume (stranded run), Cancel, Edit (title, and instructions while never run), Archive or Restore, Export JSON and Export Markdown. Actions the caller's role does not allow are disabled and a line says who can; a refusal shows the API's sentence.
- Answer, approvals for the task (with the run each belongs to), run history (newest first, "Load older runs"), the selected run's step timeline with tool, risk, policy outcome, approval and token facts, Sources used, and Activity (task events, "Load older events").
- Live: the shell's event stream reloads the task list and approvals; the view refetches its list, summary, the selected task, its runs and events on every change, so a run's steps, retries and decisions appear without reloading.
- States: loading, empty workspace (focuses the composer), no matches (clear filters), no archived tasks, list error with "Try again", task not found, history or activity errors, and read-only roles.

Every string is in the English and Arabic catalogs (`src/lib/messages/en.ts` and `ar.ts`, `tasks.*` keys, [Web localization](web-localization.md)); user and model text is `dir="auto"`, ids and tool names stay left to right, and the layout uses logical CSS properties.

Tests: `apps/web/test/tasks.test.ts`, `apps/web/test/tasksQuery.test.ts`, `apps/web/e2e/tasks.spec.ts` (stateful mock in `e2e/tasks-mock.ts`) `e2e/core-journey.spec.ts`, and the shared `e2e/accessibility.spec.ts` and `e2e/localization.spec.ts`.

## Flutter (Tasks screens)

`apps/mobile/lib/features/workspace/tasks_screen.dart` with `task_management.dart` (the ANUM 360 list, mockup 10):

- Tabs **All**, **My Tasks** (`created_by=me`), **Agents** (what agents are doing now: `queued`, `running` and `waiting_approval`) and **Completed**, fetched with the debounced search and paged with "Load more"; an archive toggle in the header shows archived tasks. Each row has the status icon and word, the **High / Medium / Low** pill and the due label (Today, Tomorrow, a short date, or Overdue in red; a finished task shows its date, never Overdue). A failed load says what failed and offers "Try again"; each tab's empty state says what appears there.
- The **+** button and the floating button open the create sheet: what to do, an optional title, priority, a due date and time, and **Start now** (on: create and run; off: saved as a to-do that the detail starts with **Start now**).
- Task detail: priority and due date with **Change priority**, **Change due date** and **Remove due date**; Cancel, Retry (failed or cancelled), Rename, Archive or Restore, and "Copy run log" (the JSON export on the clipboard); run history chips switch the step timeline between runs. A `403` or `409` shows the API's sentence on the screen and keeps the workspace usable.
- Layout is covered from 320 to 1440 wide at 200 percent text, in English and Arabic (`test/task_management_test.dart`, `test/lists/tasks_list_test.dart`).

## Voice

The voice assistant creates, starts, retries, cancels and reports on tasks by name with the same permissions and code as these routes (`main.start_task_run`, `start_task_retry`, `cancel_task_record`); tasks it creates record `task.created` with `source: "voice"` ([Voice actions](voice.md#actions)).

## Known limits

- Search is a substring scan inside the filtered keyset page; no relevance ranking or trigram index yet.
- The creator filter lists user ids (the membership directory is owner-only); display names need a directory read that members may use.
- Tasks are never hard-deleted from the Tasks view; deletion stays with privacy deletion and retention.
- The Flutter list has no date-range filter and no bulk selection; both are on the web.
