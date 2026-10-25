# ANUM MINE headquarters: governed agent provisioning

Status: built in this change (API, contract, web, one migration, tests).

When a kind of work has nobody to handle it (orders have no agent and no office is the first case), the headquarters can build the missing agent: add it to the organisation under Agents, give it the skills and tools it needs, seat it in the right office and record that it owns that work. It never does this on its own. The flow is always: detect the gap from stored records, propose an agent, let a workspace owner review exactly what will be created, and create it only when that owner confirms with a click.

## Code

| Part | File |
|---|---|
| Template catalogue, gap rules, placement, hash (pure) | `services/api/anum_api/office_provisioning.py` |
| Routes and the confirm steps | `services/api/anum_api/office_provisioning_api.py` |
| Agent create path used by confirm | `agent_profiles.create_agent_governed` (the body of `POST /api/v1/agents`) |
| Orders and leads placed through the handler | `services/api/anum_api/office_flow.py` (`FlowEvidence.handlers`) |
| Migration | `0106_agent_provisioning` (table `office_agent_provisions`; numbers are renumbered at merge) |
| Contract | `packages/contracts/src/officeProvisioning.ts` |
| Web | `apps/web/src/hq-provisioning/` (`OrgGapsPanel`, `ProposalDialog`, client, hook, css), messages `hqProvisioning.en.ts` / `.ar.ts` |
| Tests | `tests/test_office_provisioning.py`, `tests/test_postgres_office_provisioning.py`, `test/hqProvisioning.test.ts`, `e2e/hq-agent-provisioning.spec.ts` |

## What counts as a gap

A gap exists only because a stored record backs it. A source that is not connected is shown as such and never counted.

| Evidence | Role proposed | Needs |
|---|---|---|
| Open orders in the connected store that sit in no office | Orders Desk | `commerce:read`, connector present |
| Open leads with no owner | Sales Follow-up | `whatsapp:read` |
| Stuck tasks that sit in no office | Operations Dispatcher | (none) |
| Stuck approvals (waiting, expired or missing) | Compliance Reviewer | `approval:read` for the text |
| A room linked to a department (`team_department`) with no active agent in that department | the role of that department (sales, support, operations, finance, tyre engineering, marketing, technology, research, legal, compliance) | (none) |

A confirmed handler clears the work gaps of its role. A department gap clears when any active agent has that department (matched without case). A handler whose agent was deleted is a gap again.

## Templates

All data in `office_provisioning.TEMPLATES`, English and Arabic names and descriptions. Tools are named by their registry id. `Missing` capabilities have no tool or skill in the platform today: they are listed in the proposal and the agent is created without them and marked limited. A test fails if a registered tool later matches one of them, so the list cannot go stale.

| Role | Department | Tools that exist | Skills that exist | Missing today |
|---|---|---|---|---|
| Orders Desk (`orders_desk`) | operations | `commerce.list_orders`, `commerce.get_order`, `commerce.sales_summary`, `draft_reply`, `draft_whatsapp_reply` | `anum.commerce`, `anum.document-drafting` | change an order's status in the store, carrier tracking lookup |
| Sales Follow-up (`sales_followup`) | sales | `list_whatsapp_conversations`, `read_whatsapp`, `draft_whatsapp_reply`, `search_email`, `read_email`, `draft_reply`, `team.assign_task` | `anum.whatsapp-inbox`, `anum.email`, `anum.team-assign` | read and update sales leads and quotes |
| Customer Support Triage (`support_triage`) | support | `classify_mail`, `summarize_mail`, `search_email`, `read_email`, `business.draft_support_reply`, WhatsApp read and draft tools, `team.assign_task` | `anum.email`, `anum.whatsapp-inbox`, `anum.team-assign` | change a support ticket's status |
| Finance Reconciliation Clerk (`finance_reconciliation`) | finance | `finance.summary`, `finance.transactions.search`, `finance.bills.upcoming`, `finance.safe_to_spend` | `anum.finance`, `anum.document-drafting` | bank statement matching |
| Fleet Maintenance Coordinator (`fleet_maintenance`) | tyre_engineering | the ten read only `tyre_*` tools and `purchase_plan` | `anum.tyre-planning`, `anum.document-drafting` | nothing |
| Operations Dispatcher (`operations_dispatcher`) | operations | `team.assign_task`, `call.search_log`, `search_email` | `anum.team-assign`, `anum.document-drafting` | reassign an existing task, route planning |
| Compliance Reviewer (`compliance_reviewer`) | compliance | `finance.guardian.rules`, `finance.guardian.check`, `classify_mail` | `anum.document-drafting` | audit log reader, policy register |
| Marketing Specialist (`marketing_specialist`) | marketing | `marketing.summary`, `marketing.draft_social_post`, the four read and draft `brevo.*` tools, `web.fetch_page` | `anum.brevo`, `anum.document-drafting` | advertising reports, website analytics |
| Technology Coordinator (`technology_coordinator`) | technology | `web.fetch_page`, `search_email`, `read_email`, `classify_mail`, `summarize_mail`, `team.assign_task` | `anum.email`, `anum.team-assign`, `anum.document-drafting` | repository and pull request reading, build and deployment status, monitoring feed |
| Research Analyst (`research_analyst`) | research | `web.fetch_page`, `search_email`, `read_email`, `summarize_mail` (and the workspace knowledge base) | `anum.email`, `anum.document-drafting` | web search, reading files from Documents |
| Legal Reviewer (`legal_reviewer`) | legal | `search_email`, `read_email`, `summarize_mail`, `classify_mail`, `draft_reply`, `team.assign_task` (and the workspace knowledge base) | `anum.email`, `anum.document-drafting`, `anum.team-assign` | reading contracts from Documents, contract register, sending for signature |

The four newest roles, their cost caps and why some skills are not attached are explained in [departments, modules and meeting rooms](ANUM_MINE_DEPARTMENTS_AND_MEETING_ROOMS.md). Every template's instructions now state who the role is, what it does, what it hands back and what it never does; the review dialog shows them, the knowledge access, the cost cap and the Limited list.

Whether a tool "exists" is decided at request time against the live tool registry and the confirming person's roles. A tool the registry lacks, or that the person's role may not hand out, is listed as missing and not attached. A skill is attached only when it exists and every tool it needs is attached.

## Least privilege

- No high risk tool is ever in a template (no sending email, WhatsApp, money or price changes).
- Autonomy is `read: auto`, `write: ask` (or `off` for finance and compliance), `external: off`.
- Sharing is `workspace` so the agent is visible to the people who see the office, and the office desk assignment can name it.
- A monthly cost limit (USD 3 to 8 depending on the template, 5 by default) is set through the agent controls store and audited. It only tightens; an owner can raise it under Agent controls.

## The flow and the API

All under `/api/v1/office/provisioning`. PostgreSQL only (503 otherwise, never fake data). The route functions are synchronous so database work runs off the event loop.

| Route | Who | What |
|---|---|---|
| `GET /gaps` | `task:read` | `{gaps, coverage, sources, limits, can_confirm}`. Coverage states: `gap`, `covered`, `no_signal`, `source_not_connected`, `source_no_access`. |
| `GET /templates` | `task:read` | The catalogue resolved against this deployment and the caller (tools and skills present or missing). |
| `GET /proposals/{template}?lang=en\|ar&room_id=` | `task:read` | Everything confirm would create, the evidence, the desk plan with room candidates, and `payload_hash`. Stores nothing. |
| `POST /proposals/{template}/confirm` | `workspace:manage` | Body `{payload_hash, input_mode: "click", lang, room_id?}`. |

The proposal is a pure function of the stored records and the caller, so there is no proposal row to approve and no way for a tool to approve its own. `payload_hash` is a SHA-256 over the tenant, workspace, **confirming user** and the exact content (agent fields, missing capabilities, budget, status, desk plan). Confirm recomputes the proposal and compares in constant time; any difference (a desk was taken, a tool appeared, another user) answers 409 and the dialog asks the owner to review again.

Voice or typed input can never confirm: `input_mode` other than `click` answers 403 before anything runs. No registered tool and no executive assistant tool calls these routes (a test checks both lists).

### Confirm steps

1. Permission (`workspace:manage`, and `task:create` inside the agent path), click only, hash check.
2. **Claim** the ledger row `(workspace, template)` in its own short transaction (primary key, `on conflict do nothing`). A second confirm at the same moment sees the claim and answers 409 "already being built". A claim older than 120 seconds, or an active row whose agent was deleted, is taken over and finished.
3. **Create the agent** with `create_agent_governed`, the same function as `POST /agents` (permission, capability validation, the 200 agents limit, `agent.created` audit), with status `paused`. If an earlier interrupted confirm already built an agent with this template marker (`template_id = hq:<template>`), that agent is adopted, never duplicated.
4. **Budget cap** through the agent controls store, with an audit record.
5. **Seat** in one office transaction: create the desk if the plan says so (capacity checked under the room lock), assign the agent, mark the ledger row `active`, write the audit record with before (the gap evidence) and after (agent, tools, skills, missing, budget, desk) and emit `office.agent_provisioned` (ids only) plus the usual `office.layout_changed`.

Retrying confirm is safe at every point: a finished role answers `200 already_provisioned` with the existing agent; an interrupted one answers `200 resumed`.

### Desk placement

The plan prefers a vacant desk in an office room linked to the role's department, then a new desk in a linked room with capacity left, then nothing. Only work rooms (office, workshop, warehouse) get permanent desks. The owner may pick a different room in the dialog (`room_id`, part of the hash). With no available desk the agent is created without one and the dialog says so before confirming; it then holds no work in any office.

## Decision: created paused

The runtime does not require it, but the agent is created `paused`. A new agent that can read orders and customer messages should not start working before an owner has seen it exist. The dialog says so, and after confirming offers "Turn it on" (the normal agent PATCH). While paused, work routed to it shows as stuck with the existing `agent_paused` rule and the action "resume the agent", so the gap does not silently look solved.

## Orders in the live flow

Nothing links an order to an agent in the commerce tables, and this change does not invent a link there. The link is the ledger row: `office_agent_provisions.work_kinds` says which kinds of work the confirmed agent handles (`orders` for the Orders Desk, `leads` for Sales Follow-up). `load_flow_evidence` reads active rows whose agent the viewer can see, and `derive_flow` then:

- seats open orders in the office of the orders handler's desk (`location.via = "handler"`, stage `in_office`), keeping every real stuck rule (`order_ship_overdue` first, else `agent_paused` for a paused handler);
- seats open leads that have no `assigned_to` in the leads handler's office the same way; a lead with an owner keeps its own placement;
- keeps `no_order_office` for orders when there is no handler, and `agent_has_no_desk` when the handler has no desk;
- shows nothing when the commerce connector is missing (the source stays "Not connected" and no order exists to place).

No order data is created or changed.

## Web

HQ operations menu: **Organisation gaps (n)**, also shown under the Teams panel. The panel lists gaps (critical first) with their evidence sentences, then every role with its state (Handled by an agent, No gap found, Not connected, No access) and "Add this role". The dialog shows why, exactly what will be created, the access (autonomy, tools and skills present or missing, missing capabilities and the Limited badge), where it sits with a room selector, and what changes in the live flow. English and Arabic with right to left, focus trap, Escape and focus return, polite status text, loading, error with retry, denied, empty and stale states.

## Tenant isolation

`office_agent_provisions` has forced row level security on tenant and workspace, a purge policy for tenant deletion, and is registered in the tenant lifecycle table list. Every read and write is one workspace scoped session. Private agents of other users stay invisible to gap detection, coverage and the flow, as everywhere.

## Not done, said plainly

- No automatic creation, scheduling or notification of gaps. Detection runs when the panel opens and when the live flow changes.
- Support, finance and fleet gaps come only from a room linked to that department with no active agent in it. No record of support tickets or bank reconciliation exists to count as work.
- Tasks without an agent are normal (the assistant runs them) and are not a gap. Only stuck ones with no office are counted.
- Orders and leads are routed to one handler per kind, not balanced between several agents.
- Agent names are stored in the language chosen in the dialog; the agent model has one name field.
- Retiring or replacing a handler is done by deleting or editing the agent under Agents; the gap returns when the handler is gone. There is no "unprovision" button.
