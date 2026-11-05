# Setup plan

A setup plan is a checked-in business blueprint that ANUM applies to a workspace in one confirmed action, instead of the owner clicking through hundreds of settings. The first plan is the owner's "Shahzeb Business Group" plan (12 agents, 24 skills, 8 department assistants, governance, knowledge folders, 30 saved commands, 25 automations, an AI budget with a monthly cap and loop limits per agent, an approval reminder, a Home order, a marketing consent rule and 18 Week 1 pilot checks). Plan version 4 (2 Nov 2026) maps the parts the newer features made possible; see [What changed in plan version 4](#what-changed-in-plan-version-4).

Code: `services/api/anum_api/setup_plans.py` (blueprint model, planner, apply engine, routes), `setup_plan_extras.py` (agent caps and loop limits, the approval reminder, the Home order, the consent rule, office seats and agents in business workspaces), `setup_plan_checklist.py` (the live checklist), `setup_plan_ledger.py` and `db/setup_plan_repository.py` (what was applied), `setup_plan_checks.py` (pilot check results), the blueprint `services/api/anum_api/blueprints/shahzeb_business_group.json`, migration `0052_setup_plans`. Web: `apps/web/src/SetupPlanView.tsx`, `src/lib/setupPlan.ts`, `src/lib/setupPlanApi.ts`, messages `src/lib/messages/setupPlan.{en,ar}.ts`. Contracts: `packages/contracts/src/setupPlans.ts`.

## No side doors

Every write goes through the route function a person's click would call, with the caller's own tenant context, so each feature's permission checks, validation, limits and audit records apply unchanged:

| Plan part | ANUM feature and route function |
|---|---|
| Workspaces (5 business workspaces) | `workspace_admin.create_workspace_in_organization` (owner or org_admin; caller becomes first owner) |
| Memory rules | `governance.update_memory_governance` (org_admin) |
| Approval rules | `governance.create_approval_rule` (org_admin) |
| Policy packs | `governance.create_policy_pack` then `activate_policy_pack` (org_admin) |
| Role templates (with the pilot role cards) | `governance.create_role_template` (org_admin) |
| Knowledge folders | `knowledge_base.create_collection` (shared collections) |
| AI budget | `model_budget.set_model_budget` (workspace scope, owner) |
| Skills S01-S24 | `skills_api.publish_skill` (org_admin) and `install_skill` (owner) |
| Agents A1-A12 | `agent_profiles.create_agent` and `update_agent` |
| Department assistants | `assistants.create_assistant` and `update_assistant` (owner) |
| Saved commands | `commands.create_quick_command` (the applier's own, category Work) |
| Automations | `automation.create_workflow`, `disable_workflow`, `create_schedule`, `create_trigger`; `update_workflow` to run an untouched one as its agent |
| Per-agent monthly caps | `agent_controls.set_agent_cap` (workspace owner) |
| Per-agent loop and retry limits | `agent_loop_limits.set_loop_limit` (workspace owner) |
| Approval-ageing reminder (#4) | `approval_reminders.set_reminder_settings` (workspace owner) |
| Home priorities | `home.save_layout` (the applier's own Home) |
| Marketing consent rule | `contact_consent.set_consent_settings` (owner or org_admin) |
| Office seats | `office_staffing_api._load` (read) and `office_space_api.assign_desk` (workspace owner) |
| Agents in business workspaces | `agent_profiles.create_agent_with_status`, in the business workspace's own context |

A step the caller may not take is skipped with the reason (an owner without org_admin skips governance and skill publishing; an org_admin who is not an owner here skips assistants, installations and the budget). Applying again as the other role adds what was skipped. ANUM does not require an approval for governance changes (they need the org_admin role and are audited), so the plan applies them directly through the governance routes.

## API

| Method and path | Who | What |
|---|---|---|
| `GET /api/v1/setup-plans` | owner or org_admin | The checked-in plans, with how many items are applied here. |
| `GET /api/v1/setup-plans/{id}/preview?phase=pilot\|all&overwrite_edited=&business_workspaces=&run_automations_as_agents=&seat_in_offices=` | owner or org_admin | Read-only: per item `action` (`create`, `update`, `present`, `kept`, `skipped`, `later`, `manual`, `builtin`, `unsupported`), `reason_code`, `reason`, `facts`, `week` and a `link`; section counts; the caller's access notes; the budget; the approval thresholds and memory rules with their support level. Nothing is created or audited. |
| `POST /api/v1/setup-plans/{id}/apply` `{phase, overwrite_edited, plan_version, business_workspaces, run_automations_as_agents, seat_in_offices}` | owner or org_admin | Applies; answers the preview plus each item's `outcome` (`created`, `updated`, `present`, `kept`, `skipped`, `failed` with `error`, `busy`, `not_applied`) and `outcomes` counts. A `plan_version` other than the checked-in one answers `409` (review the new preview). Writes one `setup_plan.applied` audit record (phase, plan version, options, outcome counts; `outcome: partial` when an item failed). |
| `GET /api/v1/setup-plans/{id}/checklist` | owner or org_admin | The live checklist: each step for you to do, read from real state ([Your checklist](#your-checklist)). |
| `GET /api/v1/setup-plans/{id}/checks` | owner or org_admin | The pilot checks with what was recorded, `passed`, `failed`, `not_run`, the gate text and `done`. |
| `PUT /api/v1/setup-plans/{id}/checks/{check_id}` `{status: pass\|fail\|not_run, note, evidence}` | owner or org_admin | Records one result (note at most 1000 characters, evidence at most 300: a task or approval id, or an `https://` link; control characters refused). Audited as `setup_plan.check_recorded` (status and whether a note or evidence was given, never their text). |

Members and viewers get `403` with who can apply the plan. Unknown plan or check: `404`.

## Phases

- **Pilot** (`phase: pilot`): A1 Chief of Staff, A7 Customer Care and A8 Finance Controller are active; the other agents are created **paused**, the assistants **turned off**; the budget is the pilot's AED 340. Workspaces wait for the whole plan.
- **Whole plan** (`phase: all`): every agent active and every assistant on, at their starting levels; the 5 business workspaces created; the budget AED 1,200.
- In both, **automations are created turned off** with their schedules and triggers off. The owner runs each by hand, checks the result, then turns it on.
- The page shows the plan's 8-week rollout; Week 1's gate is the pilot checks below.
- In both, every agent gets its monthly cap (its share of the budget, AED at 3.6725 per USD) and loop limits (5 runs per task, 3 attempts per automation step); the approval reminder is set to 4 hours; the applier's own Home leads with decisions waiting; marketing sends need a consent record.

### Options on the page

The preview and the apply take the same three choices (they are part of what "the shown preview" means, so changing one loads a new preview before Apply is enabled):

- **Seat agents in their office** (on by default): each agent gets a free desk in its headquarters office and department, where one exists ([Offices](#offices)).
- **Run automations as their agent** (off by default): new plan automations, and plan automations nobody changed since, get `agent_id` on their task step, so the task runs as the plan's agent for it with that agent's tools, autonomy, cap and loop limits ([Automation: run as an agent](automation.md#run-as-an-agent)). They stay turned off. The owner can change it per automation in the automation's step afterwards.
- **Also add agents to each business workspace** (off by default, whole plan only): see [Agents in business workspaces](#agents-in-business-workspaces).

## Idempotency and the owner's changes

The ledger `setup_plan_items` keeps one row per blueprint item key: the object it created or linked, its version or a fingerprint of what the plan set, and who applied it. Workspace-level items use the request's workspace; tenant-level ones (governance, published skills, created workspaces) use `workspace_id = ''`, so a second workspace sees the organization's governance as already applied.

- Re-applying never duplicates: an item already applied is `present`.
- An object with the plan's name that the plan did not create is **linked**, never changed (`found_by_name`).
- An object the plan created and the owner changed since (agent version, assistant fingerprint, memory rules, budget) is **kept**; one the owner removed stays removed. `overwrite_edited: true` ("Replace my changes" on the page) applies the plan's values again or recreates it.
- An untouched item is updated when the plan asks for more: the whole plan turns on paused agents and assistants and raises the budget; skills an org_admin publishes later are attached to untouched agents. The plan never lowers a budget or pauses an agent.
- Each item is written in its own transaction(s). A failure is reported for that item; the next apply continues. Before creating, the apply **claims** the item (`INSERT ... ON CONFLICT DO NOTHING`), so two applies at once never create it twice; a claim older than 10 minutes (an apply that stopped) is retaken. An agent is created with its status in the same write (paused outside the phase), never active and paused afterwards. If a stopped apply had already created the agent, the next apply takes it over (`resume_stopped_apply`) only when it is still untouched (made by the person who claimed it, after the claim, version 1) and gives it the requested status; an agent someone changed or made is linked and left alone.
- PostgreSQL: forced RLS on `setup_plan_items` (`tenant_id` and `workspace_id` in the request's workspace or `''`) and on `setup_plan_checks` (`tenant_id`); no role is granted anything new.

## Renames

Plan version 2 (13 Oct 2026) renamed every item whose name had a long dash (the agent whose name joined "GROUP" and "Chief of Staff" with a long dash is now "Group: Chief of Staff"; the business workspaces are "Fleet Consulting", "Tyre Pulse", "ANUM 360 Gallery", "OpsBrain" and "VoiceQuote") and rewrote the page's notes in plain words. Each renamed item lists its old names in `former_names`; `Blueprint.former_text` gives back the old wording of the text the plan stores on objects (assistant instructions and automation prompts, where only the agent names changed).

Plan version 3 (13 Oct 2026) adds the approval rules with AED bands and approver steps ([Approval routing](approvals-and-risk.md#approval-routing)) and marks opt-out handling (CC04, automation #7, the memory rule) as supported by the [contact suppression list](privacy.md#contact-suppression). Applying it adds the new rules; nothing is renamed.

Applying again updates what an earlier apply made, matched through the ledger by the item's key, never by name:

- **Untouched** agents and assistants (version or fingerprint as the plan left them) get the plan's new values, as for any plan change. When only the name or wording changes the preview says `will_rename`.
- **Changed by hand** (the plan made it, the owner edited it since): only a field that still holds exactly the plan's old wording changes. An agent's name; an assistant's name and instructions; an automation's name, its task step (name and prompt), and its schedule and trigger names; a saved command's title. The ledger row is kept as it was, so the object still counts as the owner's and nothing else is ever overwritten. A name the owner chose stays.
- Objects the plan only linked (`found_by_name`) are never renamed. Finding by name also accepts the former names, so an object from version 1 without a ledger row is linked, not created twice.
- The preview shows these as **Will change**, reason code `will_rename`; automations stay turned off.
- **Not renamed by applying:** policy packs and approval rules (ANUM has no rename for them; a new name would publish a second pack) and workspaces the plan created (a workspace is renamed from inside it, in Settings > Workspace). Migration `0055_plain_dashes` renamed them once to the plan's current names ([Punctuation](web-localization.md#punctuation)); the plan still finds them by key or former name, so it never makes copies.

## Mapping: plan to ANUM

Every row was checked against the code on 2 Nov 2026.

| Plan item | ANUM | Support |
|---|---|---|
| Tenant "Shahzeb Business Group" | The tenant the plan is applied in | as-is |
| 6 workspaces | GROUP is the workspace you apply from; FLEET, TYRE, GALLERY, OPS, VOICE are created (whole plan). With the option on, the Fleet, Tyre and Gallery agents are also made in their own workspace ([Agents in business workspaces](#agents-in-business-workspaces)); OPS and VOICE have no plan agents | mapped |
| Departments | `department` on agents; assistants use ANUM's department enum (Command, Marketing, Systems & Knowledge are `custom`; Commerce & Care and Product Support are `support`; Fleet & Tyres is `tyre_engineering`). Each plan department maps to a headquarters office unit (`department_units`: Command to Executive, Sales and Commerce & Care to Commerce, Fleet & Tyres to Tyre Pulse, Finance, Marketing, Product Support to Technology, Systems & Knowledge to Knowledge and research), shown per agent and used to seat it ([Offices](#offices)) | mapped |
| 12 agents | Agent profiles, `category: work`, shared with the workspace, instructions = common + agent text + approvals, schedule, KPI, supervisor | as-is |
| Autonomy Observe | Agent autonomy read `auto`, write `ask`, external `off` (drafts only) | mapped |
| Autonomy Internal | read `auto`, write `auto`, external `ask` | mapped |
| Routine / Approval-gated | Routine sends only through WhatsApp's auto-send list of approved templates; every external action asks | mapped (A7 starts at Observe) |
| Agent tools | Real tools only, resolved per deployment: `team.assign_task`; email `search_email`, `read_email`, `draft_reply`, `send_email`; WhatsApp `list_whatsapp_conversations`, `read_whatsapp`, `draft_whatsapp_reply`, `send_whatsapp_template`, `send_whatsapp`; commerce reads `commerce.get_order`, `list_orders`, `low_stock`, `sales_summary`; tyre `tyre_fleet_summary`, `tyres_due`, `purchase_plan`, `tyre_alerts`; marketing `marketing.summary`, `draft_social_post`, `schedule_post`; `web_search` when SearXNG is set. No agent gets price, stock, payment, refund or discount tools. Command Center, calendar, Reports, notifications, meetings, documents, automations, audit and gateway are pages, not tools: listed per agent as not mapped. | mapped |
| 24 skills | Tenant skill versions `sbg.sNN-...` 1.0.0 with the plan's trigger, steps and output as instructions, installed with only their declared tools; plus ANUM's built-in skills where their tools exist | as-is |
| 8 department assistants | Assistant profiles, autonomy `suggest` (route and propose, no approval authority), routing keywords; no inbound channel until an owner ticks one | as-is |
| 9 policy packs | 6 packs of `require_approval` / `allow` rules on real tool patterns (they only add approvals). MEMORY maps to memory governance, RUNTIME to the budget, the per-agent caps and the per-agent loop limits, ADMIN to the org_admin requirement and audit | mapped |
| Role templates | 10 role templates with real permission names; the pilot role cards' "may approve / must escalate" are shown with them. ANUM enforces owner, member, viewer and org_admin; templates document the intended set | partial |
| AED approval thresholds | Approval rules with AED bands and approver steps ([Approval routing](approvals-and-risk.md#approval-routing)). Delegated rules start with owners only until the person is named (the checklist says which). Only a per-task cost estimate is unsupported | mapped |
| Approval expiry and ageing | Approvals expire after 24 hours; after the plan's 4 hours a pending approval reminds the workspace's owners once in the app ([Approval-ageing reminder](approvals-and-risk.md#approval-ageing-reminder)) | as-is |
| Memory rules | Memory governance: 90-day expiry of remembered chat facts, provenance required. Per-category retention of chat facts is not possible; meeting transcripts and visitor records have their own [record retention](record-retention.md) | partial |
| 9 knowledge folders | Shared knowledge collections 00-08; the checklist counts the documents in each | as-is |
| 30 saved commands | The applier's quick commands (Work), text = the voice phrase | as-is |
| 25 automations | 18 workflows (turned off) with a `task.create` step, optionally run as the plan's agent; schedules in Asia/Dubai (turned off) or event triggers (turned off) for WhatsApp, email, files and failed runs. #8, #9, #10, #16, #24 need a webhook trigger from the source system (the checklist shows which have one); #18 runs by hand. #2, #3, #19 are built in (Command plans, team reminders, Meetings); #4 is the approval reminder setting; #14 is part of #13; #6 is the WhatsApp auto-send list; #7 is the built-in [contact suppression list](privacy.md#contact-suppression) | mapped |
| Budget caps | Workspace monthly cost limit = sum of the active agents' caps in USD at the AED peg (3.6725), and each agent's own monthly cap in [Agent controls](agent-controls.md#per-agent-monthly-caps) | as-is |
| Budget alerts 50, 75, 90 percent | Built in: owners get an in-app notice at 50, 75, 90 and 100 percent of the workspace, organization and each agent's budget ([Budget alerts](model-gateway.md#budget-alerts)); no email | as-is |
| Loop limit 5 cycles, retry counts | Per-agent loop and retry limits in [Agent controls](agent-controls.md#loop-and-retry-limits): 5 runs per task, 3 attempts per automation step | as-is |
| Consent for promotional outreach | [Consent records](privacy.md#consent-records-for-campaigns) with lawful basis, source, date and withdrawal; the plan turns on "Marketing sends need a consent record"; every marketing send checks consent as well as the suppression list | as-is |
| Home priorities | The applier's Home order (decisions first); everyone can [arrange their own Home](home.md#arrange-home) | as-is |
| Integrations, people, 2FA, documents | The live checklist reads each one's real state ([Your checklist](#your-checklist)) | checked live, done by hand |

## Pilot checks

The 18 Week 1 cases (CC01-CC10 customer care, COD01-COD05 cash on delivery, OWN01-OWN03 owner commands) are in the blueprint with input, expected answer or action, approval gate, pass criteria and what ANUM does (`support`: supported, partial or unsupported). The **Pilot checks** tab lists them; an owner or org_admin records Pass, Fail or Not run with a note and an evidence reference. Results are per tenant with who and when, and audited. The summary reads "x of 18 passed"; Week 1 in the rollout shows the gate, which is done when all 18 pass.

ANUM never runs these cases against live data from the page. The parts it can check on its own governance are API tests (`services/api/tests/test_setup_plan_pilot_checks.py`), named in each case's `verified_by`:

- CC05 / OWN02: a payment-kind refund of AED 300 always needs an approval and can never act on its own, at any autonomy level; after the whole plan no agent holds a payment, price or stock tool.
- OWN03: a template send to 20 recipients always asks (bulk; ANUM's default threshold of 5 is stricter than the plan's 10).
- OWN01: "Show all COD orders awaiting remittance and group them by variance" plans read-only steps that need no approval.
- CC04: a WhatsApp STOP (or إيقاف, لا ترسلوا لي رسائل) puts the number on the [contact suppression list](privacy.md#contact-suppression), and every later send to it, by an agent, an automation or after an approval, is refused and audited; START lifts it.

The COD variance bands (COD02, COD03, COD05) are in the COD Reconciliation skill and the 17:00 automation's instructions, not enforced by code, so a person checks them.

## Not supported, and not planned

Not planned (said plainly; the plan lists them so nobody waits for them):

- Banking, bank reconciliation and any money movement. ANUM does not connect to a bank or move money; COD cash-count records stay outside ANUM.
- Refund and discount tools. Approval rules route the decision, a person makes the refund or discount in the store.
- Courier tracking and delivery proof.

Not supported yet:

- A per-task cost estimate before a run (the "task above AED 20" threshold). Each agent's monthly cap and the budget alerts limit spending instead.
- Different retention per kind of remembered chat fact.
- WhatsApp utility templates from the pilot pack: they need Meta submission and approval in the WhatsApp Business account; ANUM sends a template only after an owner ticks it on the auto-send list.
- Reading whether two-step sign-in is on: it is set in Keycloak, which ANUM cannot read, so the checklist says "Check it yourself".

## Your checklist

The plan's "For you to do by hand" steps are a live checklist (`GET /api/v1/setup-plans/{id}/checklist`, the **Your checklist** tab on the web). Nothing is ticked by hand: each step reads real state each time it is opened, says `done`, `to do`, `partly done`, `check it yourself` (ANUM cannot read it) or `no access` (the caller cannot read that module), and links to the page where it is fixed.

| Step | What is read | Done when | Opens |
|---|---|---|---|
| Invite people and set their roles | Active members by role and pending invitations of this workspace | 4 members and 1 viewer (the plan's people) | Members |
| Name the delegated approvers | The three delegated approval rules (refunds up to AED 250, standard quotes, commitments above AED 5,000) | Each names a person (`user:<id>`) | Governance |
| Two-step sign-in | Nothing: it is set in Keycloak | Check it yourself | - |
| Two-person rule | The workspace approval policy and the number of owners | The rule is on (it says to invite a second owner first) | Approval policy |
| AI model | The workspace's model configuration | A model is configured | Settings |
| Integrations in the plan's order | The integrations setup state: AI model, email alerts, phone alerts, a mailbox, WhatsApp, the online store, Zoom or Teams, tyre data, speech, web search, webhooks | Every one connected | Integrations |
| Knowledge documents | Documents in each of the 9 plan folders | Every folder has a document | Knowledge |
| Webhook triggers for #8, #9, #10, #16, #24 | Webhook triggers on those automations | Each has one | Automations |
| Test and turn on each automation | Each plan automation's status and enabled schedules or triggers | All turned on | Automations |
| Routine WhatsApp templates | The WhatsApp auto-send list | At least one template chosen | WhatsApp |
| Backups | The last `backup.sh` runs ([Backups](selling-readiness.md#backups)) | Local and off-site succeeded | - |
| Give agents more freedom | Nothing: a judgement after reviewing drafts | Check it yourself | - |

Still by hand, because only the owner can: the invitations themselves, Keycloak roles and two-step sign-in, signing in to each integration with the owner's own accounts, uploading documents, registering webhooks in the source systems, testing automations, choosing templates, entering the off-site backup key on the server, recording the 18 pilot checks on synthetic accounts and promoting agents.

## Offices

Each agent's department maps to a headquarters office unit. With PostgreSQL and an office in the workspace, the preview says which office and department the agent sits in, and Apply (with "Seat agents in their office" on) seats an agent with no desk at a free desk of that office, in a room of its department first, through `office_space_api.assign_desk` (a workspace owner; the office desk audit applies). Desks Staff my campus keeps empty or is filling are never used; an agent already at a desk is left there. Without an office (or without PostgreSQL) the section has one "by hand" item that says to set up the campus in Headquarters first. Office staffing's own blueprint ([Office staffing](ANUM_MINE_OFFICE_STAFFING.md)) is not changed: it may still list the plan's agents as "Other agents" when their tools do not match an office role.

## Agents in business workspaces

With "Also add agents to each business workspace" on and the whole plan, the agents of FLEET (Sales Pipeline, Proposal Consultant), TYRE (Fleet Planner, Tyre Analyst) and GALLERY (Commerce Coordinator, Customer Care) are also made in Fleet Consulting, Tyre Pulse and ANUM 360 Gallery. Each workspace is checked on its own, with the caller's own membership there: no membership is skipped with "You are not a member of ...", a role that cannot add agents is skipped with "Your role ... cannot add agents", a workspace that does not exist yet is skipped with "Applying the whole plan creates it; apply again after that". The agents are written in that workspace's context (its RLS scope, its tools and installed skills) and recorded in its ledger, so applying again finds them `present` and never duplicates them. The applying workspace keeps its own copy of all 12.

## What changed in plan version 4

Plan version 4 (2 Nov 2026) replaced stale "not supported" items with the features that landed since: per-agent caps and loop limits (agent controls), budget alerts at 50, 75 and 90 percent, the approval-ageing reminder (#4, a setting now), automations that run as their agent, the plan's agents in each business workspace, offices and departments, Home priorities and consent records for campaigns. Applying it adds the new items; nothing is renamed. The plan's manual steps gained `check` names for the live checklist, plus two steps: configure the AI model and check backups.

## Flutter

Settings › Workspace administration › **Setup plans** (`apps/mobile/lib/features/admin/setup_plans_screen.dart`) lists the plans with how much of each is applied here, and opens one plan's read-only preview: the pilot or whole plan, the counts and AI budget, the caller's access notes, the week-by-week rollout, every item (searched, filtered by section or to changes only, 12 at a time with "Show more", fixed reasons in the reader's language) and what ANUM cannot do yet. Applying a plan, the options, the live checklist and the pilot checks stay on the web, and the screen says so. Owners and organization administrators only: anyone else sees the API's `403` explained. Tests: `apps/mobile/test/admin/org_admin_test.dart`.

## Tests

`services/api/tests/test_setup_plans.py` (blueprint validity and no secrets, read-only preview, member refused, unknown plan and stale version, pilot apply, automations turned off, re-apply without duplicates and its audit, renames from plan version 1 and a rename that keeps the owner's edits, whole plan after pilot with owner edits kept and "Replace my changes", autonomy never above the plan, owner without org_admin, found-by-name linking, tenant and workspace isolation, claims, agents created paused in one write, a stopped apply taken over with the requested status, failure and retry, removed items), `test_setup_plan_pilot_checks.py` (the checklist and the governance cases above), `test_postgres_setup_plans.py` (the pilot through PostgreSQL as the app role, RLS on both tables, claims, checks per tenant), `test_setup_plan_gaps.py` (budget alerts, the approval reminder, loop limits, run as an agent, Home layout, consent records and their send check, the plan's caps, limits, reminder, Home and consent rule applied once and kept when the owner changes them, rights skipped with reasons, automations run as their agent, business workspaces with per-workspace checks and idempotent re-apply, offices without a headquarters, the live checklist, audit walks), `test_postgres_setup_plan_gaps.py` (migration `0129_setup_plan_gaps` under forced RLS, reminders discovered as `anum_maintenance`, the pilot through PostgreSQL), `test_migrations.py`; web `test/setupPlan.test.ts` and `e2e/setup-plan.spec.ts` (preview, confirm, apply, whole plan, failure, member, errors, empty, stale plan, Arabic, pilot checks, the options and the live checklist, and 1440x900, 1366x768, 1024x768, 768x1024, 390x844 on desktop and mobile).
