# Agent controls

Two owner controls sit on top of the [tool policy and approvals](approvals-and-risk.md), [autonomy](autonomy.md) and the per-organization and per-workspace [model budgets](model-gateway.md): one emergency stop for every agent and outbound send, and monthly cost and token caps per agent (the default assistant included). Code: `services/api/anum_api/agent_controls.py` (stores, enforcement, routes), `services/api/anum_api/db/agent_controls_repository.py` (PostgreSQL), migration `0093_agent_controls`; web `apps/web/src/AgentControlsView.tsx` (Admin, **Agent controls**, `#agentControls`); Flutter `apps/mobile/lib/features/agent_controls/` (Settings, **Agent controls**).

## Emergency stop

One switch per organization (tenant). While it is on:

| What | How it is refused |
|---|---|
| Model calls (task planning, chat, voice, retrieval embeddings, conversation memory, automations, the durable worker) | `check_model_budget` calls `require_agents_running` first, so every budgeted model call raises `AgentsStoppedError` before it reaches the provider. The API answers `423` with code `agents_stopped`; voice says a sentence; the worker fails the run instead of retrying; an automation step fails. |
| Tool calls | `ToolRegistry.execute` returns a `blocked` result with `audit_metadata.outcome = "emergency_stop"` for every tool, whichever path reached it (agent runs, chat cards, a person's click in the inbox, the Brevo and phone registries). Nothing leaves through a connector. |
| Held sends (email, WhatsApp, delivery, marketing, Brevo, AI calls) | `POST /api/v1/approvals/{id}/approve` answers `423`: the approval stays pending until the stop is off or it expires. Rejecting still works. |
| Scheduled automations | The schedule advances with outcome `emergency_stop` and no run starts (a missed fire does not catch up later). Event and webhook triggers answer the `emergency_stop` outcome (webhooks `409`). |
| Scheduled mail (Email Center) and scheduled social posts | Discovery (as `anum_maintenance`) skips a stopped tenant; a pass that still sees one returns `held` and leaves the item scheduled. Held items go out on the first pass after the stop is turned off unless someone withdraws them first. |
| In-flight runs | Turning the stop on cancels the running and queued tasks of every workspace of the organization the owner belongs to (each in its own short unit of work), expires their pending approvals and signals the durable dispatcher. An inline run sees the cancel at its next step (claim, apply and settle each re-check the task) and never holds a transaction or row lock while the model works ([Agent runtime](agent-runtime.md#inline-runs)). A tool that was already executing may have taken effect; the next tool call is refused. In workspaces the owner is not a member of, runs stop at their next model or tool call. |

Who may change it:

- **Turn on**: any workspace owner (`workspace:manage`) or the organization administrator (`org_admin`). It is an emergency brake, so the bar is low.
- **Turn off**: only the organization owner, the organization administrator role (`organization_policy:manage`). A workspace owner who is not the organization administrator gets `403` with that explanation.
- **Read**: every member (`task:read`), so everyone can see why agents are paused. The web shows a banner on every page while it is on.

Every change needs a reason (3 to 300 characters) and is audited in the same transaction with the actor and reason: `agent_controls.emergency_stop.engaged`, `agent_controls.emergency_stop.released`, and `agent_controls.emergency_stop.cancelled_tasks` (counts only). Turning it on twice, or off twice, is a `409`.

Every other active owner of each workspace the actor belongs to gets an in-app notification (kind `agent_controls`, "Emergency stop turned on/off", who and why). Its `notification.created` event refreshes their clients and, through the existing team-work push kind, reaches their phones where push is on; email follows their own email preference. A failed notice is logged and never undoes the change.

### Held while stopped

`GET /api/v1/agent-controls/held` lists what the stop holds in the caller's workspace, oldest first, paged (`limit` up to 100, `offset`): pending approvals (the tool, its target), the caller's own scheduled Email Center messages (mailboxes are private to their owner, so only the subject and recipient count) and scheduled social posts (title or text, platform). Owners and the organization administrator only. The web and Flutter screens show it while the stop is on, with an empty state when nothing waits.

## Per-agent monthly caps

A workspace owner sets a monthly estimated-cost cap in USD and/or a monthly token cap per agent profile ([Agents](agents.md)), and for the **default assistant** (`agent_id` `assistant`, shown as "ANUM assistant"). Every budgeted model call is attributed to exactly one agent:

- `budgeted_model_gateway` attributes calls to the default assistant: chat with the Assistant, voice answers, retrieval embeddings, conversation memory and runs without an agent;
- `AgentPlanner.plan` rebinds a run planned as an agent with `BudgetedModelGateway.for_agent(agent_id)`;
- a chat thread with an agent (`conversations.agent_id`) rebinds the ask route's gateway to that agent.

Before each call the agent's usage this UTC month is compared with its caps; once either is reached the call is refused with `AgentCapExceededError` (HTTP `402`, code `model_budget_exceeded`, a message naming the agent, the cap and the reset date; voice says a sentence). After the call its usage is added to `agent_usage_monthly` as well as to the workspace's monthly usage. The organization and workspace budgets still apply on top. Like the budgets, the check does not reserve spend, so concurrent calls admitted just under a cap can overshoot it by those calls. A cap of `0` stops the agent; nulls remove the caps.

Owners see the default assistant and every agent they can see (shared agents and their own private ones); another member's private agent is invisible to them and keeps only the workspace budget.

## API

| Route | Who | What |
|---|---|---|
| `GET /api/v1/agent-controls` | any member | `{emergency_stop: {engaged, reason, changed_by, changed_by_you, changed_at, engaged_at}, can_engage, can_release}` |
| `PUT /api/v1/agent-controls/emergency-stop` `{engaged, reason}` | on: owners and the organization administrator; off: the organization administrator | The new state and `cancelled_tasks` |
| `GET /api/v1/agent-controls/held?limit=&offset=` | owners and the organization administrator | `{engaged, total, limit, offset, items: [{kind, id, title, detail, at, by}]}` |
| `GET /api/v1/agent-controls/agent-caps?q=&limit=&offset=` | owners and the organization administrator | `{period_start, resets_on, can_manage, total, items: [{agent_id, name, is_default, status, sharing, monthly_cost_limit_usd, monthly_token_limit, usage, total_tokens, percent_used, token_percent_used, exceeded, updated_by, updated_at}]}` |
| `PUT /api/v1/agent-controls/agent-caps/{agent_id}` `{monthly_cost_limit_usd, monthly_token_limit}` | workspace owners | The agent's caps and usage; `assistant` is the default assistant; audited as `agent_controls.agent_cap.update` |

## Storage

Migration `0093_agent_controls` (expand only): `agent_emergency_stops` (one row per tenant, forced RLS on `anum.tenant_id`), `agent_cost_caps` (cost and token limits, at least one set) and `agent_usage_monthly` (forced RLS on `anum.tenant_id` and `anum.workspace_id`). `anum_maintenance` gets `select (tenant_id, engaged)` on `agent_emergency_stops` and a select policy, only so the scheduled senders' discovery queries can skip a stopped tenant; it never reads the reason or the actor. Organisation and workspace deletion remove these rows through the purge role's own policy (`lifecycle_purge_<table>`, [Organisation lifecycle](organisation-lifecycle.md)); the three tables are listed in `tenant_lifecycle_tables.py`, so the export includes them (agents folder). The in-memory backend keeps the same data in process.

## Clients

- Web: Admin, **Agent controls**. The stop card (state, what it stops, last change and reason, a reason field and a confirmation dialog; for people who may not change it, who can), the held list while the stop is on (paged, empty and error states), and the caps list (the default assistant first, search, paging, cost and token meters with the 80% and 100% thresholds, cost and token fields per agent for owners, read-only otherwise; empty, no-match, error and owner-only states). A banner on every page while the stop is on. English and Arabic.
- Flutter: Settings, **Agent controls**: the same switch, held list and caps (reason, confirmation, read-only notice, search, paging, cost and token caps per agent; loading, empty, no-match, error and owner-only states). English and Arabic (`lib/src/localization/agent_controls_strings.dart`, the app's two locales).

## Tests

`services/api/tests/test_agent_controls.py` (permissions, audit with actor and reason, tenant A cannot flip tenant B, model and tool refusal, a held approval staying pending, held mail and posts, discovery filters, schedules and triggers, in-flight cancel, caps set, listed, searched, paged and enforced, cost and token caps, the default assistant and chat threads attributed, the paged held list, owners' notices and their push intent, a run as an agent counting against its cap, the audit walk), `test_postgres_agent_controls.py` (RLS isolation of the stop and the caps, maintenance reading two columns only), `test_migrations.py`; web `test/agentControls.test.ts` and `e2e/agent-controls.spec.ts` (main path with the held list and token caps, permissions, Arabic, axe, the five responsive sizes); Flutter `test/agent_controls/agent_controls_test.dart` (English and Arabic, 320 to 1440 px, 200% text).

## Rollback

Code rollback is safe: the previous release ignores the three tables. The migration is expand only, so it stays in place. Turn the stop off before rolling back if it is on, so that held sends are not left waiting on a switch the old release cannot show.

## Not built yet

- The phone push uses the existing team-work kind and its fixed text ("Something in your team work needs you"); the in-app notification carries the specific words.
- Organization administrators who are not a workspace owner get the in-app notice only in workspaces where they are an owner.
- The held list shows the caller's own scheduled mail only, because mailboxes are private.
