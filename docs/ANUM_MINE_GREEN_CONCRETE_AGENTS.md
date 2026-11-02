# ANUM MINE: Green Concrete agents, their skills, tools, memory, knowledge and routines

Status: built (API, migration `0127_agent_memory_facts`, contract, web, tests). Web and API only; no mobile work. Owner request of 2 November 2026: "give skills to Green Concrete, add more plugins and the things required, the memory, and many more things that need to be added there."

Green Concrete is the owner's company in the headquarters ([Green Concrete](ANUM_MINE_GREEN_CONCRETE.md)): a Command Center with a seat for every real person, each with their own working hours, and a PMV manager. Its agents are the five roles of the office staffing blueprint ([Office staffing](ANUM_MINE_OFFICE_STAFFING.md)): PMV manager, Business analyst, Tyre planning engineer, Data monitoring and Data collector. This change gives those roles real skills, the read tools those skills need, working memory, a shared knowledge collection and routines. Real data only: nothing here invents a person, a number or a fleet record, and every skill says **Not connected** when its data is missing.

## What is built

| Part | File |
|---|---|
| Kit catalog (pure: skills, tools, role kits, data gaps, routines, English and Arabic) | `services/api/anum_api/green_concrete_kit.py` |
| Read tools (sections with a state, pure figures over the synced data) | `services/api/anum_api/green_concrete_ops.py` |
| Owner routes: the kit, setup, the routine switch | `services/api/anum_api/green_concrete_kit_api.py` |
| Role kits in the blueprint (only add), the collection gap and its Apply | `office_staffing_blueprint.py`, `office_staffing_api.py` |
| Role skills load their instructions; collections expand at run start | `agent_skills.py`, `agent_skill_loadout.py`, `agent_policy.py`, `agent_planning.py` |
| Agent memory: kind, source, date, edit, retention | `agent_profiles.py`, `db/agent_profiles_repository.py`, migration `0127_agent_memory_facts.py` |
| Automation step that runs as an agent | `automation.py` (`task.create` `input.agent_id`), `main.run_task_for_automation` |
| Contract | `packages/contracts/src/greenConcreteKit.ts`, `agents.ts` (memory, collection source), `officeStaffing.ts` |
| Web: the kit in the Green Concrete office drawer (lazy chunk) | `apps/web/src/hq-staffing/GreenConcreteKitPanel.tsx`, `src/lib/greenConcreteKit.ts`, messages `greenConcreteKit.en.ts` / `.ar.ts` |
| Web: agent memory (agent page and the drawer) | `apps/web/src/AgentMemoryPanel.tsx`, `src/agentMemory.css`, `src/lib/agents.ts` |
| Tests | `tests/test_green_concrete_agents.py`, `tests/test_postgres_green_concrete_agents.py`, `test/greenConcreteKit.test.ts`, `e2e/green-concrete-agents.spec.ts` |

## Skills per role

Each skill is a first-party, built-in runtime skill (`gc.*`, version 1.0.0) with reviewed instructions, inputs, an output, guardrails and English and Arabic names. It names exactly one read tool and the data sources it needs. It is **agent only**: the plain Assistant never selects it; an agent that allows it loads its instructions into every task and chat and the planner picks its tool from the user's own words (threat model G5).

| Skill | Tool | Data it needs | Roles |
|---|---|---|---|
| PMV fleet health review (`gc.pmv-fleet-health`) | `gc.fleet_health` | Tyre Pulse fleet sync (vehicles, work orders, inspection schedules, odometer and hour readings) | PMV manager, Business analyst |
| Tyre planning (`gc.tyre-plan`) | `gc.tyre_plan` | Tyre Pulse sync and the tyre planning module (forecasts, purchase plan, alerts) | Tyre planning engineer |
| Daily operations brief (`gc.daily-ops-brief`) | `gc.ops_brief` | Tasks and approvals, Green Concrete seats and working hours, Tyre Pulse sync, the workspace calendar | PMV manager |
| Data quality check (`gc.data-quality-check`) | `gc.data_quality` | Tyre Pulse sync and its per table sync report | Data collector, Data monitoring |
| Shift handover note (`gc.shift-handover`) | `gc.shift_handover` | Green Concrete seats and each person's own hours, tasks, Tyre Pulse alerts | PMV manager, Data monitoring, Data collector |
| Maintenance cost review (`gc.maintenance-cost-review`) | `gc.maintenance_costs` | Synced work order and tyre costs. Fuel: Not connected (no source) | PMV manager, Business analyst |
| Fleet alerts watch (`gc.fleet-alerts-watch`) | `gc.fleet_watch` | Tyre alerts, inspection schedules, workshop checklists | Data monitoring, Tyre planning engineer |

Every skill carries the common rules: use only figures the tool or a cited knowledge passage returned, write **Not connected** (never zero) for a section that is not connected or not synced, say when the role cannot see a section, state the last sync time, cite a procedure by its title, and never change a vehicle record, close a work order, send anything or approve anything. Instructions never grant tools or approvals.

## Tools

Seven tools, registered in the API's and the worker's tool registry like every other tool, so ToolPolicy, governance deny rules, workspace autonomy, the agent's own autonomy, the monthly budgets, the emergency stop and the `tool.call` audit all apply. Each is **low risk, read only** (`category: general`, `action_kind: read`, idempotent), reaches nothing outside ANUM, and runs its database reads in a worker thread in the caller's tenant context under RLS. A test keeps their names from matching any capability the role templates list as missing (no tool is called something it is not).

Each tool returns **sections**, each with a state: `ok`, `empty`, `not_connected`, `not_synced`, `no_access` (the person the run acts for lacks `tyre:read`, `task:read` or `calendar:read`) or `failed`. The audit records only the section states.

| Tool | Sections |
|---|---|
| `gc.fleet_health` | fleet by status and site; maintenance due (open work orders, open more than 7 days, inspections overdue and in the next 14 days); breakdowns (work orders whose work type says breakdown; Not connected when the sync has no work types); use (distance and hours per vehicle only from two or more odometer or hour readings; Not connected without readings); cost; tyre alerts |
| `gc.tyre_plan` | tyre health, tyres due this month and next, the purchase plan (1 to 36 months), alerts (the existing tyre tools, each as a section) |
| `gc.ops_brief` | alerts, tasks (open, overdue, waiting for approval), team (who is on shift now, who ends and starts next), open work orders, data freshness, today's workspace calendar |
| `gc.data_quality` | sync (age, stale after 24 hours, rows read, kept and skipped per table with reasons), missing (vehicles not inspected in 30 days, fitted tyres without a tread reading, vehicles without readings, work orders without a vehicle, closed work orders without a cost; counts and ids only) |
| `gc.shift_handover` | team (from each seat's own hours), tasks changed since the latest shift began, alerts |
| `gc.maintenance_costs` | work order cost by work type, site and vehicle in a period, tyre costs, fuel (always Not connected) |
| `gc.fleet_watch` | alerts, inspections overdue and due in 14 days, failed checklists in 7 days, tyres due this month |

Anything that writes or sends is not part of the kit: the agents keep the write and send tools their template has (for example `team.assign_task` for the PMV manager), which stay approval gated by the role's autonomy (`write: ask`, `external: off`).

## Data gaps

No tool is made up for data ANUM does not have. The kit lists these as gaps, shown in the drawer as Not connected:

| Gap | Why |
|---|---|
| Fuel use and fuel cost | No fuel source (fuel cards, telematics or a fuel log connector) exists in ANUM |
| Planned service intervals for plant and machinery | The fleet sync has work orders and inspection schedules, not hours or km between services |
| Figures inside spreadsheets in Documents | Documents are read as cited text passages through the knowledge collection, not computed over as rows |
| Each person's own calendar | The brief reads the shared workspace calendar only |

## Working memory

Every agent keeps its own memory of facts and decisions (`agent_memories`, now with migration 0127):

* **Kind**: `fact` (a recurring fact) or `decision` (a decision a person made).
* **Source and date**: `source_label` in plain words ("SOP: tyre rotation, version 3"), `source_date` the day it holds from; a memory from a task names the task; a memory from a document names the file.
* **View, edit and delete**: `GET`, `POST`, `PATCH /api/v1/agents/{id}/memories[/{memory_id}]` and `DELETE`. Reading needs `memory:read` and the agent being visible; adding and editing need `memory:create`, deleting `memory:delete`, plus the right to change the agent (its creator, or a workspace owner for a shared agent). Audited as `agent_memory.created`, `agent_memory.updated`, `agent_memory.deleted`, never with the text.
* **Retention** follows the organisation's memory governance, as remembered chat facts do: kept until deleted unless governance forbids permanent retention, then `expires_at` is `default_retention_days` from the save or the last edit. An expired memory is hidden at once (lists, runs, chats) and deleted by the next change to that agent's memories. When governance requires provenance, a memory without a source is refused.
* **Nothing from data the agent may not read**: a memory from a task must be one of this agent's tasks; a memory from a file is accepted only when the agent's own knowledge sources include that file (directly or through its collection) and the person saving it can see the file. Agents never write memories by themselves.
* A run reads each memory as a labeled, untrusted block that starts with its kind, source and date, for example `[Decision, source: SOP tyres v3, as of 2026-10-30] Rotate at 6 mm`.

## Shared knowledge

A shared knowledge base collection, **Green Concrete knowledge**, holds procedures, SOPs, price lists and supplier contacts as notes or documents. An agent's knowledge source may now be a collection (`{"type": "collection", "id": ...}`, only a collection shared with the workspace): when a run starts, the collection is read as the notes and documents it holds then, so an SOP added today is used by the next run, and the run's retrieval step records the passages it used (the Sources used panel names them, with citations). Apply adds the collection to Green Concrete agents (only adds: an agent with `none` becomes `selected` with the collection, an agent with `selected` keeps every source and gains it, an agent with `workspace` already reads it).

## Routines

| Routine | Agent | When (building time zone) |
|---|---|---|
| Daily operations brief | PMV manager | every day at 06:00, the start of the morning shift |
| Weekly PMV health report | PMV manager | every Monday at 08:00, the last 7 days |
| Morning shift handover | Data collector | every day at 13:45, before the morning shift ends at 14:00 |

A routine is an automation with one `task.create` step whose `input.agent_id` names the agent, so the task runs as that agent with its tools, skills, autonomy and approvals. It is created **turned off** with its schedule turned off, runs as the owner who set it up (their access, budget and approvals) and fails with a plain sentence while the agent is paused.

## API

Under `/api/v1/office/green-concrete/kit`. PostgreSQL only (503 otherwise). Plain `def` routes.

| Route | Who | What |
|---|---|---|
| `GET ?lang=en\|ar` | `task:read` | Skills with the state of the data they need, tools with risk, roles and their kits, Green Concrete agents (kit skills and tools, memory count, whether they read the collection), data sources, gaps, the collection, routines, setup `items` each with its own hash, `can_manage`, `can_manage_automations` |
| `POST /setup` | `workspace:manage` (+ `automation:manage` for routines), `input_mode: "click"` | Items `knowledge` and `routine:<key>` with their hashes. Created through the knowledge base and automation route functions, claimed in the setup plan ledger (`plan_id: gc-agents-kit`), so a retry answers `already_done` and two clicks never build two. Outcomes `created`, `already_done`, `being_built`, `stale`, `failed`; 409 when every item is stale. Audit `office.green_concrete.kit_setup` |
| `POST /routines/{key}/state` | `workspace:manage` and `automation:manage`, `input_mode: "click"` | `{enabled}` turns the routine's workflow and schedule on or off. Audit `office.green_concrete.routine_state` |

A spoken or typed confirmation is refused with 403 and no registered tool calls these routes.

## Web

The Command Center, **Offices and agents**, Green Concrete card, **Open**: under the roles, the **Agents kit** section (a lazy chunk) shows the skills with their state (Data connected, Not connected, No access), what each needs, its tool and risk, its roles and its inputs, output and rules; the data gaps; the shared knowledge (create it, or open Knowledge); the routines with their schedule in words, agent, state, Add, Turn on and Turn off (each confirmed); and every Green Concrete agent with its kit skills and tools, memory count, whether it reads the collection, and **Show memory**, which opens the agent's memory inline (view, search, filter by kind, add, edit and delete). The role cards above say when an agent does not read the collection yet, and Apply adds it. The agent's own page (Agents, the agent, Memory) has the same memory panel. Loading, error with retry, permission denied, no PostgreSQL, Green Concrete not built, nothing to set up, owner only, stale, empty collection and paused agent states are handled. English and Arabic (right to left).

## Owner guide

**Add the kit to your agents**

1. Command Center, **Offices and agents**, Green Concrete, **Open**.
2. Under the roles, tick each agent's update (it lists the skills, tools and knowledge it gains), **Review**, **Apply**. Nothing is removed and nothing is turned on.

**Add SOPs, price lists and supplier contacts**

1. In the drawer, **Agents kit**, tick **Create the Green Concrete knowledge collection**, **Review**, **Set up**.
2. Apply the agents' updates again so they read the collection.
3. Open **Knowledge**, open **Green Concrete knowledge**, **Add**: write a note (for example "Tyre rotation SOP, version 3") or upload a document. The agents use it from their next run and name its title when they cite it.

**Turn a routine on**

1. Make sure its agent is on (Agents, the agent, Settings, Active).
2. In **Agents kit**, **Routines**, tick **Add this routine (turned off)**, **Review**, **Set up**.
3. Optional: in Automations, run it once by hand and read the result.
4. Press **Turn on** and confirm. **Turn off** stops it the same way.

**Keep an agent's memory right**

In **Agents kit**, **Show memory** on the agent (or Agents, the agent, Memory): add a fact or a decision with where it comes from and the date, edit it when it changes, delete it when it no longer holds.

## Tests

API unit `tests/test_green_concrete_agents.py`: catalog integrity (each skill names one registered low risk read tool, agent only, reviewed instructions with Not connected, every source known), no tool matches a template's missing capability, role kits only add and stay under each role's risk ceiling, Arabic for every string and no long dashes, routines' schedules, the planner (words to tool and period), every section from a synthetic dataset (breakdowns, use, cost, data quality, fuel Not connected, overdue inspections), the team from each person's own hours at 07:00, 12:00 and 21:00 Dubai, tasks overdue and changed this shift, tools answering Not connected and No access, memory CRUD with kind, source and date, permissions and isolation, retention from governance with expired memories hidden and removed, provenance, file sources, the prompt form, collections as agent sources, role skill instructions loading, the kit routes' permissions and the automation step's agent. PostgreSQL as the app role `tests/test_postgres_green_concrete_agents.py`: migration 0127's checks, memory CRUD and expiry under RLS, the kit view, setup once (collection, routines off with their schedule off in the building's time zone and the agent in the step), the routine switch, Apply adding the kit and the collection only, the handover tool from the real seats, and another tenant seeing nothing. Web unit `test/greenConcreteKit.test.ts`; browser `e2e/green-concrete-agents.spec.ts` (every state, setup, routine switch, memory add, edit and delete, Arabic, five viewports, axe).

## Not done, said plainly

* Fuel, service intervals, spreadsheet rows and personal calendars are gaps (above): connecting them needs a data source that does not exist in ANUM yet.
* Agents do not write memories by themselves; people add them (from a task, a document or by hand). Automatic remembering from runs is not built.
* A routine's result is the task's answer in Tasks and Automations; it is not e-mailed or pushed by itself (a person can add a `notify` step in Automations).
* The kit lives in the web drawer; the Flutter app shows the agents' memory as before, without kind, source and date editing.
* Changing a skill's text is a code change (bump its version); first-party skills are not published to the skill registry.
