# ANUM MINE: Staff my campus

Status: built in this change (API, contract, web, one migration, tests). Web and API only.

The owner asked for every desk of the campus to have an agent seated. **Staff my campus** is a reviewed, click-confirmed action on top of [governed agent provisioning](ANUM_MINE_AGENT_PROVISIONING.md) and the [starter campus](ANUM_MINE_STARTER_CAMPUS.md). It lists, for every empty desk, the agent that would be created for it, and creates them only after the owner confirms. Every agent starts paused. Nothing runs, and nothing costs anything, until the owner turns the agents on.

## Code

| Part | File |
|---|---|
| Eight extra roles, and the room to role rules (pure) | `services/api/anum_api/office_staff_templates.py` |
| The planner: states, 200 limit, run cap, order, hashes (pure) | `services/api/anum_api/office_staff_desks.py` |
| Routes, claims, agent build, caps, seating | `services/api/anum_api/office_staff_desks_api.py` |
| Agent create path used per desk | `agent_profiles.create_agent_governed` (the body of `POST /api/v1/agents`) |
| Migration | `0109_staff_desks` (table `office_desk_staffing`; numbers are renumbered at merge) |
| Contract | `packages/contracts/src/officeStaffDesks.ts` |
| Web | `apps/web/src/hq-staff-desks/` (`StaffDesksDialog`, `TurnOnPanel`, client, css), messages `hqStaffDesks.en.ts` / `.ar.ts` |
| Entries | HQ **Office tools**, **Staff my campus**; the **Organisation gaps** panel |
| Tests | `tests/test_office_staff_desks.py`, `tests/test_postgres_office_staff_desks.py`, `test/hqStaffDesks.test.ts`, `e2e/hq-staff-desks.spec.ts` with `e2e/support/hqStaffDesksApi.ts` |

## Which role a desk gets

Per room, in this order (the first match wins):

1. The room kind: a reception gets the Receptionist.
2. A whole word in the room's stored name that names a role (HR, Procurement, Suppliers, Marketing, Security, Legal, Compliance, Governance, Audit, Executive, Personal, Fleet, Tyres, Inspections, Orders, Marketplace). A "Procurement" room under the operations department is procurement.
3. The room's stored team department: `tyre_engineering` to Fleet Maintenance Coordinator, `finance` to Finance Reconciliation Clerk, `support` to Customer Support Triage, `sales` to Sales Follow-up, `operations` to Operations Dispatcher.
4. Other words in the name (support, sales, finance, operations, research, data, development, deployments, incidents, automation, AI, labs).
5. Nothing matches: the desks stay empty (`no_role`) and the plan says so. Setting the room's team department in the office builder fixes it.

Names are matched in English as the builder stores them. A room the owner renamed into Arabic only will not match a name rule and falls back to its department.

## Roles

The seven governed templates are reused unchanged. Seven roles are new (Research Analyst now comes from the governed list) (`EXTRA_TEMPLATES`). They are kept out of `office_provisioning.TEMPLATES` on purpose, because that list drives Organisation gaps coverage and a role with no work signal there would only add noise.

| Role | Tools that exist | Skills that exist | Missing today (marks the agent Limited) |
|---|---|---|---|
| HR and People Assistant (`hr_people`) | `search_email`, `read_email`, `summarize_mail`, `draft_reply`, `team.assign_task` | `anum.email`, `anum.team-assign`, `anum.document-drafting` | employee records, leave and attendance; recruiting pipeline |
| Procurement Assistant (`procurement`) | `commerce.low_stock`, `commerce.sales_summary`, `finance.bills.upcoming`, `search_email`, `read_email`, `draft_reply`, `team.assign_task` | `anum.commerce`, `anum.email`, `anum.team-assign`, `anum.document-drafting` | supplier and purchase order records |
| Marketing Content Assistant (`marketing_content`) | `marketing.draft_social_post`, `marketing.summary`, `brevo.list_lists`, `brevo.list_campaigns`, `brevo.campaign_report`, `brevo.draft_campaign` | `anum.brevo`, `anum.document-drafting` | website and advertising analytics |
| Engineering and DevOps Assistant (`engineering_devops`) | `web.fetch_page`, `search_email`, `read_email`, `summarize_mail`, `team.assign_task` | `anum.email`, `anum.team-assign`, `anum.document-drafting` | code repositories and pull requests; deployments, logs and incident feeds |
| Security Reviewer (`security_reviewer`) | `finance.guardian.rules`, `finance.guardian.check`, `classify_mail`, `search_email`, `read_email` | `anum.email`, `anum.document-drafting` | audit log reader; access and sign-in review feed |
| Executive Assistant (`executive_assistant`) | `search_email`, `read_email`, `summarize_mail`, `draft_reply`, WhatsApp read and draft tools, `team.assign_task` | `anum.email`, `anum.whatsapp-inbox`, `anum.team-assign`, `anum.document-drafting` | calendar read and booking |
| Receptionist (`receptionist`) | `call.search_log`, `call.search_contacts`, `call.get`, WhatsApp read and draft tools, `team.assign_task` | `anum.whatsapp-inbox`, `anum.team-assign` | visitor log and appointments |

Same rules as the first seven: tools are named by registry id, no high risk tool is ever attached (no sending, no money), and whether a tool counts as present is decided at request time against the live registry and the confirming person's roles. A capability the platform has no tool for is a `Need`; a test fails if a registered tool later matches one, so the list cannot go stale. The Marketing role does not get the `anum.marketing` skill because that skill needs `marketing.schedule_post`, a high risk tool. Posts and campaigns are drafted by the agent and published by a person.

Autonomy is `read: auto`, `write: ask` (`off` for security, compliance and finance), `external: off`. Sharing is `workspace`. The monthly cost cap is the template's own (`monthly_cost_limit_usd`): USD 3 Operations Dispatcher, 4 Finance Reconciliation and Compliance Reviewer, 5 for most roles, 6 Fleet Maintenance, 8 Research Analyst.

## Names

`<role>, <room name> <number>`, for example `Orders Desk, Orders 2`, in Arabic `مكتب الطلبات، الطلبات 2`. The number is the desk's place in its room (natural order of the desk id), so it is the same on every run. No invented human names. No avatar style is set: the avatar style feature is not on `main` yet, so the field is left unset.

## Preview

`POST /api/v1/office/staff-desks/preview` (`task:read`, a POST only because the list of unticked rooms can be long; it stores nothing). Body: `buildings` (default all), `exclude_rooms`, `max_agents` (cap for this run), `lang`.

Every desk gets one state:

| State | Meaning |
|---|---|
| `plan` | A paused agent will be created and seated here |
| `resume` | An earlier interrupted run already built this desk's agent; it will be seated, nothing new is created |
| `occupied` | The desk already has an agent. Never touched |
| `keep_empty` | The owner left it empty on purpose. Never touched |
| `over_limit` | The workspace is at its 200 agent limit; the desk stays empty |
| `over_cap` | The owner's cap for this run was reached |
| `no_role` | No role matches the room |
| `excluded` | The building or room is unticked |
| `agent_elsewhere` | The agent this desk would resume already sits at another desk |

The answer also has the totals, the agent limit used and free, the largest possible monthly cost (the sum of the caps of every agent in the plan, per role, next to the workspace's own monthly budget when the caller may read it), the roles with their Limited flag and missing capabilities, and one batch per building with its own `payload_hash`.

Order is fixed (building id, floor level, room name, desk number), so "the first N" means the same in the preview and the confirm. The limit is counted against the agents that exist now: 200 minus existing agents is the number of new agents, in that order. For the default empty campus (220 desks, no agents) that is **200 agents and 20 desks left empty**. Buildings are filled in id order, so ANUM HQ (39), ANUM 360 Commerce (58), Tyre Pulse (29), Corporate Services (28) and Knowledge & AI (44) are staffed completely (198), the first 2 desks of New Ventures get agents, and the other 12 desks of New Ventures, both desks of Personal Office and the 6 of Career & Employment stay empty. The owner can untick buildings or rooms, or set a lower run cap, to choose which desks use the places. The role mix of all 220 desks is: Engineering and DevOps 36, Operations Dispatcher 31, Research Analyst 26, Fleet Maintenance 21, Customer Support Triage 20, Sales Follow-up 18, Finance Reconciliation 13, Orders Desk 11, Compliance Reviewer 11, HR and People 10, Procurement 8, Security Reviewer 5, Executive Assistant 5, Marketing Content 5. With each role's own cap the largest possible monthly cost is USD 993 for the 200 agents of the first run (USD 1,113 if all 220 desks could be staffed).

## Confirm

`POST /api/v1/office/staff-desks/confirm`, one building per call. Needs `workspace:manage`, `input_mode: "click"` (anything else is 403 and no registered tool or Executive Assistant tool calls the route), the building's `payload_hash` and the `quota` and `exclude_rooms` the preview used. The hash is a SHA-256 over tenant, workspace, **confirming user**, building and the exact list of desks, roles, names and caps. Any difference (another owner, a desk taken, a new agent that changes the limit) answers 409 and the dialog asks the owner to review again. The web sends one call per building in order, so a long run is not one long request and a stop leaves finished buildings finished.

Per room, atomically:

1. **Claim** the ledger row of every desk (`office_desk_staffing`, primary key tenant, workspace, desk) in a short transaction. A second confirm at the same moment sees the claims and skips those desks as `being_built`. A claim older than 120 seconds, or whose run ended, is taken over.
2. **Build** each agent with `create_agent_governed` (permission, capability validation, the 200 limit, `agent.created` audit), status `paused`, the desk marker `hq:<role>:<12 hex of the desk id hash>` as its template id. An agent that carries this marker, or that the ledger remembers, is adopted and never built twice. The ledger row remembers the agent id at once.
3. **Cap**: one agent controls transaction sets the monthly cost cap of every agent of the room, with an `agent_controls.agent_cap.update` audit record each.
4. **Seat**: one office transaction assigns every agent to its desk, closes the ledger rows (`active`), writes ONE audit record `office.staff_desks.room` with before (the desks, vacant) and after (desk, agent role, cap, `payload_hash`, `input_mode: "click"`, paused), the `office.layout_changed` event, and one `office.agent_provisioned` event per agent (ids only). If any desk was taken meanwhile, the whole room's seating rolls back.

The answer lists every desk: `created`, `seated_existing`, `skipped` (`kept_empty`, `already_staffed`, `being_built`) or `failed` (`agent_limit`, `agent_refused`, `agent_failed`, `seat_conflict`, `cap_failed`, `room_failed`) with a plain reason. A retry never creates a second agent for a desk: a desk whose agent was built but not seated becomes `resume` in the next preview.

## Keep empty

`PUT /api/v1/office/staff-desks/keep-empty` (`workspace:manage`) with `{desk_ids, keep_empty}` stores or clears a `keep_empty` ledger row, audited as `office.staff_desks.keep_empty`. A desk that has an agent or is being staffed cannot be marked (409). The mark is remembered, so a re-run never fills the desk.

## Turn on

Agents stay paused. Per agent, **Turn on** uses the same code path as the agent edit (`status: active`). For several: `GET /staff-desks/paused` pages the agents this feature seated that are still paused (with their cap), `POST /staff-desks/turn-on/preview` returns the cap total of a selection, the workspace budget and a hash, and `POST /staff-desks/turn-on` (click only, `workspace:manage`, the hash) turns them on and writes one `office.staff_desks.turn_on` audit record plus the usual `agent.updated` record per agent. The dialog shows the cap total before the button that turns them on.

## Tenant isolation

`office_desk_staffing` has forced row level security on tenant and workspace and a purge policy for tenant deletion, and is registered in the tenant lifecycle table list. Every read and write is one workspace scoped session as the app role. Another workspace's records neither count as existing nor appear. Private agents of other users stay invisible, as everywhere.

## Web

HQ **Office tools**, **Staff my campus**, and a button in **Organisation gaps**. The dialog has two tabs: **Staff desks** (totals, limit and cost, roles with Limited badges and the list of what they cannot do yet, buildings with checkboxes and expandable rooms with checkboxes, a run cap, and a searchable, filterable, paged list of every desk with **Keep empty**) and **Paused agents** (paged, select a page or all, review the cost, turn on). After the run it shows the result per building and per desk. English and Arabic (right to left), focus trap, Escape and focus return, labelled controls, polite live regions; loading, error with retry, denied, stale (409), partial failure, not an owner (review only), nothing to staff, and no building ticked. Responsive down to 390 pixels.

## Not done, said plainly

- A room whose name and department match no role keeps its desks empty; there is no per-room role override in the dialog.
- Role rules read the stored English room name. Arabic-only names fall back to the department.
- Building names have no Arabic column; the Arabic names of the eight starter buildings are in the dialog's messages and other names show as stored.
- An agent whose seating failed is built, paused and unseated until the next review seats it; it is not deleted automatically.
- Agents built by this feature are not Organisation gaps handlers, so they do not take over order or lead routing, and they are paused anyway.
- Agent creation is not in the office transaction (the agent store has its own unit of work), so "atomic per room" means seating, caps and the ledger; the built agents survive a rollback and are adopted on retry.
- No avatar style is set (the feature is not on `main`).
- The mobile help copies (`apps/mobile/assets/help`) were not synced (web and API only); run `node scripts/sync-help.mjs` when merging.
