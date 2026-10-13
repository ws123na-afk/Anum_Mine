# ANUM Command

ANUM Command turns one spoken or typed request ("Check my emails, find today's urgent items, prepare a summary, check the PMV tyre report and schedule a meeting with the team tomorrow at 10 AM") into a plan of steps, shows which agents and tools each step will use and which steps will pause for approval, and, once the person confirms, runs the steps in parallel as ordinary tasks. The person can follow the work live, approve what needs approval, read the results and start a suggested next step.

The API is `anum_api/commands.py` (router `/api/v1`), the PostgreSQL store `anum_api/db/command_repository.py` and the migration `0044_commands`. The Flutter screens are in `apps/mobile/lib/features/command/` ([Flutter mobile](mobile.md)); the web Command Center (below) uses the same endpoints and the TypeScript types in `packages/contracts/src/command.ts`.

## The flow

| Screen | Endpoint |
|---|---|
| Command: orb, suggestion chips, keyboard, attach, tap to speak | `GET /api/v1/commands/suggestions`; files: `POST /api/v1/files` |
| Listening: live transcript, timer, stop, English/Arabic | On-device speech recognition ([Voice](voice.md)); nothing is sent until the person confirms |
| Understanding: "Here is what I understood", Edit | `POST /api/v1/commands/preview` |
| Plan and agent selection: steps, agents, risk, approvals; remove and reorder; Start execution | `POST /api/v1/commands/preview`, then `POST /api/v1/commands` |
| Executing and live activity; Stop; keep working in background | `POST /api/v1/tasks/{id}/run` for each step, `GET /api/v1/events/stream`, `GET /api/v1/commands/{id}`; Stop: `POST /api/v1/tasks/bulk {"action": "cancel"}` |
| Results: Summary, Details, Files; Approve all; Share; View full report | `GET /api/v1/commands/{id}`, `POST /api/v1/approvals/{id}/approve` per approval, `GET /api/v1/commands/{id}/report` |
| Next steps | `GET /api/v1/commands/{id}/next-steps` |
| Quick commands and templates (Work, Personal, Custom) | `/api/v1/quick-commands` |

## Endpoints

Every endpoint takes the usual identity headers and works in the request's tenant and workspace. Commands and quick commands belong to the user who made them: another member of the workspace, an owner included, gets `404` for them and never sees them in a list. The tasks a command creates are ordinary workspace tasks ([Tasks](tasks.md)).

### `POST /api/v1/commands/preview`

The plan a request would run. Read-only: nothing is created, run or audited, and no model is called.

Request: `{"text": "...", "locale": "en" | "ar"}` (`text` 1-4000 characters; `locale` optional, guessed from the text). Needs `task:create` (owners and members).

Response `CommandPlan`:

```json
{
  "text": "Check my emails, find today's urgent items, prepare a summary, ...",
  "language": "en",
  "steps": [
    {
      "position": 1,
      "title": "Check my emails, find today's urgent items, prepare a summary",
      "instruction": "Check my emails, find today's urgent items, prepare a summary",
      "agent": {"id": "email", "name": "Email agent"},
      "skills": [{"id": "anum.document-drafting", "name": "Document drafting"}, {"id": "anum.email", "name": "Email inbox"}],
      "tool": {"name": "search_email", "description": "...", "category": "email", "action_kind": "read", "target": null},
      "risk_level": "low",
      "approval": "none",
      "approval_reason": "The tool is low risk and within the actor's scope.",
      "connected": true,
      "note": null
    }
  ],
  "agents": [{"id": "email", "name": "Email agent"}],
  "approvals_required": 0,
  "blocked": 0
}
```

- `approval` is `none`, `required` (a run of this step will pause for approval) or `blocked` (policy refuses the tool; the step cannot be confirmed). It is the runtime's own decision: the tool policy, the workspace's approval policy, the organization's governance rules and the workspace's autonomy policy ([Approvals and risk](approvals-and-risk.md), [Autonomy](autonomy.md)).
- `agent.id` is one of `email`, `whatsapp`, `commerce`, `research`, `action`, `calendar`, `tyre`, `writer` and `general`; `name` is in the request's language.
- `connected: false` marks a step that cannot reach live data, and `note` says what happens instead:
  - `calendar`: no calendar is connected to ANUM yet, so the step drafts the meeting for the person to send.
  - `tyre`: the step uses the tyre planning tools (`tyre_fleet_summary`, `tyres_due`, `purchase_plan`, `tyre_alerts`, [Tyre planning](tyre-planning.md)). It is `connected: true` once the workspace has saved a TyrePulse connection; without one the note says "Connect TyrePulse under Tyre planning first" (the run would fail with "Connect your tyre data first"). On a server without the tyre tools, the step answers from the workspace's files and notes.

### `POST /api/v1/commands`

Creates the command and one task per confirmed step, in the confirmed order, in one transaction. Needs `task:create`. Answers `201` with the command's live view (below), status `ready`.

```json
{
  "text": "the request as understood and confirmed",
  "steps": [{"instruction": "..."}, {"instruction": "..."}],
  "source": "typed | voice | quick_command | suggestion | next_step",
  "locale": "en",
  "attachments": ["file_..."],
  "quick_command_id": "quick_..."
}
```

- `steps`: 1-8, each instruction 1-1000 characters. The client sends instructions only: the server plans every step again, so an edited or reordered plan can never change a step's agent, tool, risk or approval. A step whose tool is blocked by policy is refused with `422` ("Step N is blocked by policy: ... Remove it and try again.").
- `attachments`: up to 5 ids of workspace files ([Workspace files](files.md)); `422` if one does not exist. Needs `file:read`. They appear in the command's Files; the agents find the files' text through workspace retrieval like any other file.
- `quick_command_id`: the caller's quick command this request came from; its `use_count` and `last_used_at` are updated. `404` if it is not the caller's.
- Each task gets a `task.created` event whose payload carries `command_id` and `position`.

Then start every step: `POST /api/v1/tasks/{task_id}/run` for each `steps[].task_id`, in parallel (each needs `task:run`; with `ANUM_RUNTIME_BACKEND=temporal` they are queued for the worker). A step's run that needs approval stops at `waiting_approval` like any task run.

Stop: `POST /api/v1/tasks/bulk` with `{"action": "cancel", "task_ids": [...]}` (the command's task ids). Finished tasks answer `409` per item and are left as they are.

### `GET /api/v1/commands/{id}`

The live view (`CommandView`): the summary fields below plus

- `steps[]`: `position`, `title`, `instruction`, `agent`, `tool`, `risk_level`, `approval` (as planned), `task_id`, `task` (the task, or null if it can no longer be read), `status` (the task's status), `progress` (0-1, from the run's checkpoint phase), `run_id`, `result` (the run's result text), `started_at`, `finished_at` and `approvals` (every approval of that task, with `payload_hash`).
- `pending[]`: the command's pending approvals, ready for "Approve all".
- `timeline[]`: the tasks' events oldest first (`id`, `at`, `type`, `task_id`, `position`, `agent`, `title`), at most 50 per step. `type` is a canonical event name ([Events](events.md)): `task.created`, `agent_run.started`, `approval.requested`, `agent_run.completed`, ...
- `files[]`: `{"kind": "attachment" | "run_log", "name", "path", "file_id", "task_id", "content_type"}`. `path` is an API path to download with the caller's headers: `/api/v1/files/{id}/content` for attachments, `/api/v1/tasks/{id}/export?format=markdown` for each started step's run log.
- `attachments[]`, `started_at` (first run), `finished_at` and `duration_seconds` (once nothing is running or waiting).

Clients refresh the view when `GET /api/v1/events/stream` delivers an event whose `subject` (or `payload.task_id`) is one of the command's task ids, and every few seconds while the stream is unavailable.

### Summary fields and status

`GET /api/v1/commands?limit=20&cursor=<last id>` lists the caller's commands newest first (`limit` 1-50; `400` for an unknown cursor) as `CommandSummary`: `id`, `text`, `source`, `locale`, `status`, `progress`, `step_count`, `completed_steps`, `pending_approvals`, `agents`, `created_at`, `updated_at`.

`status` is derived from the tasks on every read:

| Status | When |
|---|---|
| `ready` | No step has started |
| `running` | A step is queued or running, or some steps have not started yet |
| `waiting_approval` | Nothing is running and a step waits for approval |
| `completed` | Every step completed |
| `partial` | Every step finished and some failed or were cancelled |
| `failed` | Every step failed (or failed and was cancelled) |
| `cancelled` | Every step was cancelled |

### Approve all

The client approves the view's `pending` approvals one at a time with `POST /api/v1/approvals/{id}/approve {"payload_hash": "<the hash shown>"}` ([Approvals and risk](approvals-and-risk.md)): there is no bulk approval endpoint, so every approval is still bound to the exact call the person saw, the two-person rule and approval chains still apply, and one refusal does not stop the others.

### `GET /api/v1/commands/{id}/report?format=json|markdown`

The full report: every step with its agent, status, tool, approvals and result, and the attachments. Markdown is a download (`Content-Disposition`); JSON is the live view. Audited as `command.exported` with the format.

### `GET /api/v1/commands/{id}/next-steps`

Up to 6 suggested next actions derived from the results, in the command's language, without a model call:

- `review_approvals` when approvals are pending;
- `command` "Try again: ..." with the instruction of each failed or cancelled step;
- `command` follow-ups for each agent that completed (email: draft replies, send the summary to the team; tyre: maintenance tasks, compare with last month; calendar: send the agenda; ...);
- `save_quick_command` once a command completed and the caller has not saved its text yet.

Each item is `{"id", "kind", "label", "text"}`. A `command` item's `text` starts a new command: preview it first. Steps that would send or change something outside ANUM still pause for approval when run.

### `GET /api/v1/commands/suggestions?locale=en|ar`

The chips on the Command screen, at most 8: the caller's quick commands (most used first, at most 4, `kind: "quick"`), then their most frequent and most recent commands (`kind: "recent"`, with `use_count`), then starters (`kind: "starter"`) for what this deployment can actually do (the email starter only where the email tools are registered, web search only where SearXNG is configured).

### `DELETE /api/v1/commands/{id}`

Removes the command from the caller's history (`204`); its tasks stay in the workspace. `409` while it is running or waiting for approval. Audited as `command.deleted`.

### Quick commands

| Method and path | Does | Needs |
|---|---|---|
| `GET /api/v1/quick-commands?category=work\|personal\|custom` | The caller's quick commands by category and position | `task:read` |
| `POST /api/v1/quick-commands` | `{"title" (1-80), "text" (1-2000), "category" = "custom", "position"?}`; appended to its category without a position | `task:create` |
| `PUT /api/v1/quick-commands/{id}` | Any of `title`, `text`, `category`, `position` (at least one) | `task:create` |
| `DELETE /api/v1/quick-commands/{id}` | Deletes it (`204`) | `task:create` |

A quick command is `{"id", "title", "text", "category", "position", "use_count", "last_used_at", "created_at", "updated_at"}`. At most 100 per user (`409` beyond). Using one is creating a command with its `quick_command_id`. Saying "run my <title> quick command" to the voice assistant does exactly that (`create_command_record`, the body of `POST /commands`, with `source: "voice"`) and starts every step ([Voice actions](voice.md#actions)). Audited as `quick_command.created`, `quick_command.updated` (field names) and `quick_command.deleted`.

## The web Command Center

On the web, ANUM Command (`#command`) is the page for giving orders; it is not the home. The overview of the day (approvals, tasks, inbox, meetings, alerts, budget) is Home ([home.md](home.md#the-web-home)), a separate page with its own component. Code: `apps/web/src/CommandCenter.tsx` and `src/commandCenter.css`, `src/lib/commands.ts` for the pure rules (mapping, plan editing, statuses; unit tested in `test/commands.test.ts`) and `src/lib/commandApi.ts` for every call.

| Part | What it does | Backed by |
|---|---|---|
| Header | Title, the promise ("you see the plan first") and **Speak to the orb**, which opens the Voice page; the space orb lives there and is not repeated here | none |
| Your order | Typed request (Enter shows the plan), push-to-talk microphone that fills the box for checking (English or Arabic), attach up to 5 documents (uploaded to Documents), and chips for saved, frequent and starter commands | `POST /files`, `GET /commands/suggestions` |
| Here is what ANUM will do | The plan: each step's agent, tool, risk and whether it runs on its own, asks for approval or is blocked by policy, with the step's note (for example no calendar connected). Move up, move down, remove; Edit the request; **Start** (refused while a blocked step remains) | `POST /commands/preview`, then `POST /commands` and `POST /tasks/{id}/run` per step |
| Running and recent commands | The caller's commands with status, steps done and progress, 10 at a time with "Load older commands" (the API's cursor). The newest opens on arrival | `GET /commands?limit=10&cursor=` |
| Command | The selected command live: progress, every step with its status, agent, tool and result, **Approve all** for its pending approvals (one request each with the payload hash shown; a refusal does not stop the others), Review each, attached documents, next steps (a follow-up is planned, never run directly; "Save as a quick command"), **Stop**, Download report (Markdown) and Remove from history once nothing runs. It refreshes every 3 seconds while running and on workspace events | `GET /commands/{id}`, `POST /approvals/{id}/approve`, `GET /commands/{id}/next-steps`, `POST /tasks/bulk`, `GET /commands/{id}/report`, `DELETE /commands/{id}` |
| Saved commands | The caller's quick commands by Work, Personal and Custom: Plan it, edit, delete and New saved command | `/quick-commands` |

Viewers cannot create commands (`task:create`): the page says so and who can change it, and hides the composer, Stop, Approve all and the saved-command editing. Every list has loading, empty (with the next action) and error (what failed, Retry) states. A request handed over from Home (an insight's "Analyze") is planned on arrival and waits for Start.

Tests: `e2e/command-center.spec.ts` (its own page without the overview, a typed command planned, reordered, started and finished with results and next steps, a saved command planned and a new one saved, a viewer and a failing history, a past command's detail, axe and no horizontal overflow at the five viewports in English and Arabic, dark and light).

## How the plan is made

No model is involved, so a preview is instant, free and the same every time:

1. **Clauses.** The request is split at sentence ends (not inside "10 a.m."), commas (not inside "10,000"), the Arabic comma, "then"/"after that"/"ثم", and "and" or "و" followed by a new verb ("... and schedule a meeting", "... وأرسل التقرير"). Leading "and", "then" and "please" are dropped.
2. **Routing.** Each clause goes through the runtime's own skill selection (`SkillRegistry.select`, English and Arabic triggers) and tool choice (`agent_planning.choose_tool_call`), exactly as a task with that text would be planned when it runs. The tool decides the agent (`search_email` is the email agent, `list_whatsapp_conversations` WhatsApp, a `commerce.*` tool commerce, a tyre planning tool tyre data, `web_search` research, `external.action` action); a clause answered by the model (`anum.respond`) is a calendar, tyre data or report step by its words, otherwise the general assistant.
3. **Steps.** A clause that names no agent of its own (or only asks for a summary) joins the step before it, as does a clause for the same agent, so "check my emails, find today's urgent items, prepare a summary" is one email step. At most 8 steps; the rest of a longer request stays in the last one.
4. **Policy.** Each step's tool is evaluated with the runtime's tool policy and the workspace's autonomy policy (`runtime.with_workspace_autonomy`), so the preview says "needs approval" exactly when the run would pause.

Steps run in parallel and independently: a later step does not see an earlier step's result. Steps that build on each other belong in one step (the grouping above does that for summaries).

## Storage, privacy and audit

- "Download my data" exports both (section `commands`) and "Delete my data" deletes both; the tasks stay ([Privacy](privacy.md)).
- With `ANUM_REPOSITORY_BACKEND=postgresql`, commands are rows in `commands` (the request, its steps with their task ids, attachments) and quick commands in `quick_commands` (migration `0044_commands`). Both have forced RLS on `anum.tenant_id`, `anum.workspace_id` and `anum.user_id`; the store sets `anum.user_id` on the request's transaction, which also creates the command's tasks. With `memory` (local and tests) an in-process store keeps the same contract.
- Audit records (`command.created`, `command.deleted`, `command.exported`, `quick_command.*`) carry counts, kinds and field names only, never the request text.
- The tasks a command creates are ordinary workspace tasks: visible in Tasks, kept when the command is removed from history, and covered by [Tasks](tasks.md) and [Privacy](privacy.md) like any other task.

## Not done yet

- **Calendar.** There is no calendar integration: calendar steps are planned as drafts (`connected: false` with a note), never as invites sent.
- **Image understanding.** A photo attached from the Command screen is stored as a workspace file and listed in the command's Files, but the model gateway has no vision input, so its pixels are not read.
- **Steps that depend on each other** run in parallel without each other's results (see "How the plan is made").
