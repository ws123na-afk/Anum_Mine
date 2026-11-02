# ANUM MINE: office staffing blueprint, rules and the Offices and agents view

Status: built in this change (API, contract, web, tests). Web and API only. No migration: the existing agent tables, the agent controls store and the provisioning ledger hold everything.

The owner asked that every office gets the right kind of agents, each with its skills, tools (plugins), rules and data sources, and that the Command Center shows all of it. This document is the spec.

## What is built

| Part | File |
|---|---|
| The blueprint (pure, versioned data, English and Arabic) | `services/api/anum_api/office_staffing_blueprint.py` |
| Routes: blueprint, offices, one office, apply | `services/api/anum_api/office_staffing_api.py` |
| Governed edit path used by apply | `agent_profiles.update_agent_governed` (the body of `PATCH /api/v1/agents/{id}`) |
| Governed create path used by apply | `agent_profiles.create_agent_governed` (the body of `POST /api/v1/agents`) |
| Contract | `packages/contracts/src/officeStaffing.ts` |
| Web: command centre tab | `apps/web/src/hq-staffing/OfficeStaffingView.tsx`, `OfficeDetail.tsx` (lazy chunk), tabs in `ExecutiveCommandCenter.tsx` |
| Web: headquarters room card | `apps/web/src/hq-staffing/OfficeStaffingCard.tsx` (lazy chunk, loaded by `HeadquartersLive.tsx`) |
| Web: client and pure helpers | `apps/web/src/lib/officeStaffing.ts`, messages `officeStaffing.en.ts` / `.ar.ts` |
| Tests | `tests/test_office_staffing.py`, `tests/test_postgres_office_staffing.py`, `test/officeStaffing.test.ts`, `e2e/office-staffing.spec.ts` with `e2e/support/officeStaffingApi.ts` |

## The blueprint

The blueprint is reviewed data in code, version `BLUEPRINT_VERSION = 1`. It invents no tool, skill or connector: every role points at an existing governed role template (`office_provisioning.TEMPLATES` or `office_staff_templates.EXTRA_TEMPLATES`), so its tools are registry ids and its skills are catalog ids that already exist. The blueprint adds what a template does not say: the purpose in this office, the rules and the data sources.

### Data sources

Each source is marked from real state when an office is read, never guessed.

| Source | Kind | Connected when | Connect in |
|---|---|---|---|
| Store and marketplace orders | connector | a row in `commerce_connectors` | Commerce |
| Tyre Pulse fleet sync | connector | a row in `tyre_connections` | Tyre |
| Email mailboxes | connector | a mailbox you can see (`mail_accounts` or `email_accounts`) | Email |
| WhatsApp Business | connector | a row in `whatsapp_accounts` | WhatsApp |
| Calendar | connector | a row in `calendar_connections` | Calendar |
| Finance ledger | connector | a row in `finance_accounts` or `finance_transactions` | Finance |
| Brevo email campaigns | connector | a row in `brevo_connections` | Marketing |
| Social media accounts | connector | a row in `marketing_accounts` | Marketing |
| Phone calls provider | connector | a row in `call_provider_accounts` | Calls |
| Code projects (Developer mode) | connector | a row in `developer_projects` | Developer mode |
| Web search service | server setting | `ANUM_SEARXNG_URL` set | the server operator |
| Sales leads, Documents library, Knowledge base, Tasks and approvals, Visitor register, Audit log | built in module | always ("Built in") | the module |

A source shows **No access** when the reader lacks the permission that module needs (for example `finance:read`), so a viewer never learns more than the module would show. Mailboxes are private to their owner, so "Connected" for email means a mailbox the reader can open.

### Rules

Every role carries:

* **Autonomy**: reading `auto`; drafts and changes `ask` (or `off` for finance, compliance and security roles); sending, publishing and paying `off`.
* **Highest tool risk allowed**: `low` or `medium`, never `high`. A test fails if any tool of a role is above it, and apply never attaches one.
* **Approvals**: the plain sentences that follow from the autonomy ("Every draft or change waits for a person's approval").
* **Monthly cost cap**: the template's cap (USD 3 to 8), set through agent controls.
* **Never list**: four base rules for every role (send, publish or pay without approval; approve its own action; state a figure it did not read; copy personal data outside the workspace) plus role rules: no money movement, **live trading stays off** (every finance role), no legal advice, no decisions about people, no order, price or stock changes, no promises to customers, no publishing, no code changes, no access changes, no commitments in the owner's name, no vehicle record changes.

Autonomy, the risk ceiling and the cost cap are enforced by the runtime and agent controls. The never list is also written into the agent's instructions under the line `Office rules (blueprint v1)`, so the model reads it on every run; where the platform has no tool for an action (sending is off, high risk tools are never attached) the rule is also structural.

### Roles per department (units)

| Unit | Roles (template) |
|---|---|
| Executive and command centre | Executive PA (`executive_assistant`), Command compliance (`compliance_reviewer`) |
| Reception | Receptionist (`receptionist`) |
| Operations | Dispatcher (`operations_dispatcher`) |
| Security | Security reviewer (`security_reviewer`) |
| Personal office | Personal assistant (`executive_assistant`), Personal finance (`finance_reconciliation`, live trading off) |
| Career and employment | Career HR (`hr_people`), Career research (`research_analyst`) |
| Green Concrete operations | PMV manager (`operations_dispatcher`), Business analyst (`research_analyst`), Tyre planning engineer, Data monitoring, Data collector (`fleet_maintenance`) |
| Tyre Pulse fleet | Fleet maintenance (`fleet_maintenance`), Fleet logistics (`operations_dispatcher`) |
| Commerce | Orders desk, Sales follow-up, Support triage |
| Marketing | Marketing specialist, Marketing content (drafts only, a person publishes) |
| Finance | Finance clerk (`finance_reconciliation`, live trading off) |
| Human resources | HR and people (`hr_people`) |
| Procurement | Procurement (`procurement`) |
| Legal and compliance | Legal reviewer, Compliance reviewer |
| Knowledge and research | Research analyst |
| Technology | Technology coordinator, Engineering and DevOps |
| New ventures | Venture scout (`research_analyst`), Venture finance (`finance_reconciliation`, live trading off) |

Each role's tools, their risk, skills and data sources are listed by `GET /api/v1/office/staffing/blueprint` and in the office view. A role whose template lacks a capability (for example calendar booking for the PA) shows as **Limited** with the missing items in plain words.

### Office kinds

| Office | Kind | Units |
|---|---|---|
| ANUM HQ (`campus-01-hq`) | Executive headquarters and command centre | executive, reception, operations, security |
| ANUM 360 Commerce | Commerce | commerce, marketing |
| Tyre Pulse | Tyre Pulse | tyre_pulse |
| Corporate Services | Corporate services | finance, hr, procurement, legal_compliance |
| Knowledge & AI | Knowledge and AI | research, technology |
| New Ventures | New ventures | new_ventures |
| Personal Office | Personal office | personal |
| Career & Employment | Career and employment | career |
| Green Concrete (`gc-green-concrete`) | Green Concrete operations | green_concrete |
| Any other building | Built from its rooms | one unit per stored room department (sales and support give commerce, legal and compliance give legal_compliance, and so on), reception for a reception room, operations for a command room |
| A building with no matching room | General office | operations |

Starter campus buildings and Green Concrete are known by their stable ids; a test keeps every starter building on a real blueprint.

## Which agents belong to an office

An agent belongs to an office when it is seated at a desk in one of the office's rooms, or when it was built by this feature for that office (its marker, see below). It counts for one role, in this order: the blueprint marker, then the template it was built from (`hq:<template>` from Organisation gaps, `hq:<template>:<desk>` from Staff my campus), then its stored department (the Green Concrete people match by the role the owner gave them, for example "Data monitoring"). Agents that match no role are listed apart as "Other agents"; nothing is changed for them. Another user's private agent is invisible here as everywhere (RLS).

For each agent of a role the view shows what it lacks: skills, tools (only those the role allows and the registry has), rules (`autonomy` looser than the role, `cost_cap` missing or higher, `never_list` not in its instructions) and tools above the role's risk. Coverage is "N of M roles staffed" (a role is staffed when at least one agent has it).

## Applying

Only a workspace owner (`workspace:manage`) applies, only with a click (`input_mode: "click"`; voice and typed answers are refused with 403), and only the actions they ticked. No registered tool and no executive assistant tool calls the route (tested).

`GET /offices/{building_id}?lang=` lists the actions. Each action has its own SHA-256 `hash` over the blueprint version, tenant, workspace, **confirming user**, office and the exact content, so the web can send them in groups of 20 and a later group is not refused because an earlier one changed the office.

* **`create:<role>`**: build a missing role. Steps: claim the ledger row in `office_agent_provisions` with the key `bp:<12 hex of the office id hash>:<role>` in a short transaction (a second click at the same moment answers `being_built`; a claim older than 120 seconds is taken over); adopt an agent an interrupted run already built with that marker, else create one with `create_agent_governed` (permission, capability checks, the 200 agent limit, `agent.created` audit) with status **paused**, sharing `workspace`, the role's tools and skills, the role's autonomy and the instructions plus the rules block; set the cost cap through agent controls (audited, only lowers); seat it at a vacant desk of the office (a room linked to the role's department first, then a room with no department; desks Staff my campus keeps empty or is filling are never used) with the office desk audit and the `office.agent_provisioned` event (ids only). With no vacant desk the agent is created without one and the confirm step says so.
* **`update:<agent>`**: give an existing agent what it lacks through `update_agent_governed` (edit right: its creator, or a workspace owner for a shared agent; `version` check, capability validation, `agent.updated` audit). Tools and skills are only added; autonomy is set to the stricter of the agent's and the role's per category; the rules block is appended once; the cap is set only when missing or higher. Nothing is removed: a tool above the role's risk is shown with "remove under Agents".

Idempotent: a retry of a finished action answers `already_done`; a changed action answers `stale` (409 when every sent action is stale). One audit record `office.staffing.applied` per call holds the office, blueprint version, `input_mode` and per action only its id, outcome, agent id and desk id.

The emergency stop and the monthly caps stay in force: new agents are paused, caps only tighten, and nothing here turns an agent on (Staff my campus, Paused agents, or the agent page does).

## API

All under `/api/v1/office/staffing`. PostgreSQL only for the office routes (503 otherwise, never fake data). Synchronous route functions, so the database work runs off the event loop.

| Route | Who | What |
|---|---|---|
| `GET /blueprint` | `task:read` | The whole blueprint resolved against this deployment and you (tools with risk and state, skills) |
| `GET /offices?q=&limit=&offset=` | `task:read` | Paged and searchable list: kind, units, coverage, agents, agents needing an update, sources and their states |
| `GET /offices/{building_id}?lang=en\|ar` | `task:read` | One office: roles with agents and gaps, sources, other agents, `actions` with hashes, `can_apply` |
| `POST /offices/{building_id}/apply` | `workspace:manage` | Body `{input_mode: "click", lang, actions: [{id, hash}]}` (1 to 20). Answers per action `created`, `resumed`, `updated`, `already_done`, `being_built`, `stale` or `failed` with a reason |

## Web

* **Command Center** (`#executive`): two tabs, **Workspaces** (the existing executive desk) and **Offices and agents**. The tab lists every office of the workspace as a card: kind, "N of M roles staffed" with a meter, agents and active agents, agents missing a skill, tool or rule, unconnected data sources, **Open** and **Show in the 3D office**. Search (debounced) and paging (12 per page). The office drawer lists the data sources with **Connect** where a module connects them, then every department and role: purpose, state (Staffed, Needs an update, No agent yet, Limited), rules, skills, tools with risk, and each agent with what it lacks. Owners tick changes, **Review** shows exactly what each will do (agent name, tool and skill counts, cap, desk or no desk, rules to set), and **Apply** runs them with progress and a result per change. Loading, empty with next action (open the headquarters to set up the campus), no match with clear, error with retry, permission denied, server without PostgreSQL, owner only notice, nothing to apply, stale. English and Arabic (right to left), keyboard tabs with arrow keys, focus trap in the drawer and the confirm dialog.
* **Headquarters**: selecting a room shows **Office staffing** for its building under the team panel: coverage, each role with its state and agent count, unconnected sources with **Connect**, and **Open in the command centre** (opens the Offices and agents tab). Lazy chunk, so the headquarters entry bundle does not grow.

## What stays manual, said plainly

* Turning new agents on (they are paused), raising a cap, removing a tool above a role's risk, and deleting an agent: under Agents, Agent controls or Staff my campus.
* Connecting a data source: the Connect button opens the module; the connection itself is that module's flow. The web search service is a server setting.
* Capabilities no tool exists for (calendar booking, contract register, bank statement matching and the others listed as Limited).
* The never list is reviewed text in the instructions plus the structural limits above; there is no separate runtime check per rule beyond autonomy, risk and caps.
* Offices of other workspaces: the view reads the open workspace; switch workspace on the Workspaces tab to see another.
* Agent names are stored in the language chosen at apply time; building names without an Arabic name show in English.
* Changing the blueprint is a code change reviewed like any other (bump `BLUEPRINT_VERSION`; the rules marker follows it).
