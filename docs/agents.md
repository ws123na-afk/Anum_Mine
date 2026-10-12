# Agents

People build their own agents in a workspace: a Fleet agent that plans tyre rotations, a Research agent that searches the web, an Email agent that drafts replies. An agent is a named configuration on top of the [agent runtime](agent-runtime.md): who it is, how it works, what it may use and who may use it. It never widens what the runtime, the workspace or the organization allows.

The same model serves **department assistants**: a department assistant is an agent profile with `category: "work"` and a `department` (for example `Logistics`). There is one agent-profile model, one API and one set of tables; the department-assistant work builds on it rather than adding a second model.

## What an agent is

| Field | Meaning |
|---|---|
| `name`, `icon`, `color`, `description` | Identity. Icons come from a fixed set (`GET /api/v1/agents/catalog` lists them); the color is `#rrggbb`. |
| `category` | `work`, `personal` or `custom` (the list's filters). |
| `department` | Optional; set for department assistants. |
| `instructions` | How the agent works (at most 8000 characters). They go into the model prompt before the task; they shape answers and never select tools, skills or approvals. |
| `model_route` | `capable` (planning, multi-step work) or `fast` (short replies), the routes of the [model gateway](model-gateway.md#model-routing). |
| `allowed_skills` | Built-in runtime skills whose tools exist in this deployment, or skills installed and enabled in the workspace ([Skills](skills.md)). |
| `allowed_tools` | Tools registered in this deployment that the person saving the profile may use (a tool whose required role they lack is refused). `anum.respond` (answer in text, no side effects) is always on. |
| `autonomy` | Per tool category, `off` (never used), `ask` (approval before every use) or `auto` (as workspace policy allows). A tool's category is the stricter of its risk (low `read`, medium `write`, high `external`) and its [autonomy](autonomy.md) action kind (`read` is reading; `draft`, `create`, `update`, `archive`, `pause` are `write`; replies, sends, deletes, money, price and stock changes, merges and unknown kinds are `external`). Tools declare `category` and `action_kind` on their `ToolDefinition`. `external` can only be `off` or `ask`. |
| `knowledge_mode`, `knowledge_sources` | `none`, `workspace` (indexed memories and files the caller can see) or `selected` (only the listed memory and file ids). Folders do not exist in [Files](files.md) yet, so they cannot be selected. |
| `status` | `active` or `paused`. A paused agent does not chat or run tasks. |
| `sharing` | `private` (only the owner sees and uses it) or `workspace` (everyone in the workspace sees it and members can run it). |
| `owner_user_id`, `version`, `template_id` | The creator, an edit counter for optimistic concurrency, and the template it started from. |

## Templates

`GET /api/v1/agents/catalog` returns the templates, resolved against this deployment: a template only turns on tools that exist and that the caller may use, and lists what it is missing in `missing_capabilities`.

| Template | Uses when present | Without it |
|---|---|---|
| Fleet Agent | The [tyre-planning](tyre-planning.md) tools (`tyre_fleet_summary`, `tyres_due`, `purchase_plan`, `tyre_alerts`, read-only) and the tyre-planning skill once the fleet data is connected, plus any `fleet.*`, `vehicle.*`, `maintenance.*` tools; workspace knowledge | Plans in text from workspace knowledge; says the TyrePulse connector is missing |
| Research Agent | `web_search` (set `ANUM_SEARXNG_URL`) and the web-search skill | Drafts from its own knowledge; says web search is missing |
| Email Agent | The `anum.email` skill and the [email inbox](email.md) tools `search_email`, `read_email`, `draft_reply`, `send_email` (and `mark_email_read`, `archive_email`, `unarchive_email`) once a mailbox is connected; `send_email` is an outward action, so it always asks | Drafts and summarizes only |
| Calendar Agent | `calendar.*` | Plans in text only |
| Document Agent | Document drafting, workspace knowledge | (nothing missing) |
| Call Agent | `telephony.*`, `call.*` | Summarizes calls from pasted notes or transcripts (telephony is not set up) |

Template names and descriptions are translated in the clients by template id.

## Permissions

| Action | Who |
|---|---|
| See agents | Everyone with `task:read`: their own agents and the workspace's shared ones. Another user's private agent answers `404`, exactly like a missing one. |
| Create | `task:create` (members and owners). |
| Change, pause, delete | The agent's creator, or a workspace owner for a shared agent. Only the creator changes `sharing`. Others get `403` with a sentence saying who can. |
| Run tasks, chat | `task:run` / `task:create` on an active agent the caller can see. |
| Agent memories | Read with `memory:read`; add and delete with `memory:create` / `memory:delete` plus the right to change the agent. |

A workspace holds at most 200 agents and an agent at most 500 memories. `PATCH` takes an optional `version` and answers `409` when the agent changed since it was read.

Every change is audited in the same transaction: `agent.created`, `agent.updated` (which fields changed and the resulting configuration), `agent.deleted`, `agent_memory.created`, `agent_memory.deleted`. Audit records never contain instruction or memory text.

## API

| Method and path | Purpose |
|---|---|
| `GET /api/v1/agents/catalog` | Tools (with category and whether the caller may use them), skills, templates, icons and the workspace's `autonomy_floor`. |
| `GET /api/v1/agents?category=&status=&q=&limit=&cursor=` | `{items, next_cursor, counts}`; newest change first; `q` searches name, description and department; `counts` per category (after search and status filters). |
| `POST /api/v1/agents` | Create (`AgentProfileCreate`). |
| `GET /api/v1/agents/{id}` | One agent, with `can_edit`, `can_run` and the `unavailable_tools` / `unavailable_skills` it allows but this deployment or workspace no longer provides. |
| `PATCH /api/v1/agents/{id}` | Change any field, `status`, and optionally `version`. |
| `DELETE /api/v1/agents/{id}` | Delete the agent and its memories; its tasks and chats stay in the history. |
| `GET /api/v1/agents/{id}/memories?q=&limit=&cursor=` | `{items, next_cursor, total}`. |
| `POST /api/v1/agents/{id}/memories` | `{content, task_id?}`; a `task_id` must be one of this agent's tasks. |
| `DELETE /api/v1/agents/{id}/memories/{memory_id}` | Delete one memory. |
| `POST /api/v1/tasks` with `agent_id` | A task that runs as the agent. |
| `GET /api/v1/tasks?agent_id=` | The agent's recent tasks (keyset-paged like every task list). |
| `POST /api/v1/conversations` with `agent_id`, `GET /api/v1/conversations?agent_id=` | Chat threads with the agent. Without `agent_id` the list is the Assistant's own threads. |

The wire types are in `packages/contracts/src/agents.ts`.

## Running tasks as an agent

`POST /api/v1/tasks/{id}/run` (and retry) for a task with an `agent_id` loads the agent with the caller's identity: a deleted or invisible agent answers `404`, a paused one `409`. The run's checkpoint then carries a snapshot of the agent (`anum_api.agent_policy.AgentRunProfile`), and every decision for that run applies it, inline, on resume, at an approval decision and on a durable [Temporal](agent-runtime.md#durable-execution) worker:

1. **Planning.** Only the agent's skills (plus planning) can be selected, only from its tools; the model prompt is the agent's persona and instructions, then the task; the agent's model route is used. Skills and tools are still chosen from the user's prompt only, never from retrieved text or instructions (threat model G5).
2. **Memory and knowledge.** The agent's newest memories are added as labeled, untrusted blocks (the `retrieval` step records their ids, never their text). Workspace retrieval is skipped for `none`, limited to the chosen sources for `selected`, and unchanged for `workspace`.
3. **Policy.** The runtime's [tool policy](approvals-and-risk.md) decides first (roles, governance deny rules, high risk, approval rules, the workspace's medium-risk policy), then the workspace's [autonomy](autonomy.md) (with the connector's facts, which may let a call act on its own). The agent comes last and can only make the result stricter: a tool outside the agent's tools is blocked, `off` blocks its category, `ask` turns a call autonomy would let act into an approval. Anything the agent tightens is marked not `lowerable` and its recorded autonomy mode becomes `ask` or `block`, so nothing downstream acts on its own. `auto` never removes an approval the platform or autonomy requires.

The snapshot is what the run uses; editing the agent later does not change a run that already started. Organization governance and the workspace approval policy are read live at every decision.

## Chat

A conversation with an `agent_id` answers with the Assistant's system prompt followed by the agent's persona and instructions, the agent's model route, and the agent's memories as untrusted blocks, alongside the caller's own recalled memories. Chat still has no tools, and an agent thread never turns a question into a chat task card (those run without the agent's narrowing): anything that acts goes through a task as the agent. A paused agent's thread answers `409` until it is resumed.

## Storage and tenancy

With PostgreSQL (migration `0041_agent_profiles`):

- `agent_profiles` is under forced RLS on `anum.tenant_id` and `anum.workspace_id`, and `sharing = 'workspace' or owner_user_id = anum.user_id`. The API opens the store with the user's id set, so another user's private agent is neither read nor written, even by a workspace owner.
- `agent_memories` is under forced RLS on the tenant and workspace and on its agent being visible (the policy reads `agent_profiles`, whose own policy applies). Deleting an agent deletes its memories (`on delete cascade`).
- `tasks.agent_id` and `conversations.agent_id` are nullable references without a foreign key, so history survives a deleted agent; `ix_tasks_scope_agent` serves the per-agent task list.

No role is granted anything new and nothing reads agents across tenants. The migration is expand-only.

## Clients

- **Web** (`apps/web/src/AgentsView.tsx`): the list with All, Work, Personal and Custom (with counts), search, status filter and paging; a create and edit wizard (start from a template or from scratch, identity, instructions and model, skills and tools, autonomy and knowledge, review and sharing); and the agent's page with Chat, Call, Edit and More (pause or resume, delete), and the Overview (give it a task, recent tasks, instructions), Skills, Memory and Settings tabs. Call opens a call-summary panel: telephony is not set up, so the agent summarizes pasted notes or transcripts as a task. English and Arabic (`agents.*` keys), right to left. Covered by `test/agents.test.ts` and `e2e/agents.spec.ts`; the accessibility and localization specs include the Agents view.
- **Flutter** (`apps/mobile/lib/features/agents/`): opened from Home and from the menu drawer's Agents entry. The same list, the agent's page (Chat, Call, Edit, More; Overview, Skills, Memory, Settings) and an editor with templates. Call opens the [voice call screen](voice.md#call-mode) (the voice assistant, not yet the agent's persona); the call summary from pasted notes is under More. Selected knowledge sources are shown but picked on the web. Covered by `test/agents/agents_test.dart`, including layouts from 320 px at 200 percent text to 1440x900 and Arabic.

## Not yet

- Converging with [department assistants](team-work.md#department-assistants): team work landed its own `assistant_profiles` and `tasks.assistant_id`. A task can carry either; both narrow tool policy and neither widens it (the runtime applies autonomy, then the assistant, then the agent). Merging the two models into one is open.

- Folders as knowledge sources (Files has no folders).
- Placing calls: the Call Agent summarizes; telephony is a separate integration.
- Email, calendar and tyre-planning tools come from their connectors; the templates pick them up when those tools are registered.
- Privacy export and "Delete my data" do not yet include a user's private agents and their memories ([Privacy](privacy.md)); deleting the agent deletes them.
