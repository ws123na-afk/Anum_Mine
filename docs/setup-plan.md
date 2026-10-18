# Setup plan

A setup plan is a checked-in business blueprint that ANUM applies to a workspace in one confirmed action, instead of the owner clicking through hundreds of settings. The first plan is the owner's "Shahzeb Business Group" plan (12 agents, 24 skills, 8 department assistants, governance, knowledge folders, 30 saved commands, 25 automations, an AI budget and 18 Week 1 pilot checks).

Code: `services/api/anum_api/setup_plans.py` (blueprint model, planner, apply engine, routes), `setup_plan_ledger.py` and `db/setup_plan_repository.py` (what was applied), `setup_plan_checks.py` (pilot check results), the blueprint `services/api/anum_api/blueprints/shahzeb_business_group.json`, migration `0052_setup_plans`. Web: `apps/web/src/SetupPlanView.tsx`, `src/lib/setupPlan.ts`, `src/lib/setupPlanApi.ts`, messages `src/lib/messages/setupPlan.{en,ar}.ts`. Contracts: `packages/contracts/src/setupPlans.ts`.

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
| Automations | `automation.create_workflow`, `disable_workflow`, `create_schedule`, `create_trigger` |

A step the caller may not take is skipped with the reason (an owner without org_admin skips governance and skill publishing; an org_admin who is not an owner here skips assistants, installations and the budget). Applying again as the other role adds what was skipped. ANUM does not require an approval for governance changes (they need the org_admin role and are audited), so the plan applies them directly through the governance routes.

## API

| Method and path | Who | What |
|---|---|---|
| `GET /api/v1/setup-plans` | owner or org_admin | The checked-in plans, with how many items are applied here. |
| `GET /api/v1/setup-plans/{id}/preview?phase=pilot\|all&overwrite_edited=` | owner or org_admin | Read-only: per item `action` (`create`, `update`, `present`, `kept`, `skipped`, `later`, `manual`, `builtin`, `unsupported`), `reason_code`, `reason`, `facts`, `week` and a `link`; section counts; the caller's access notes; the budget; the approval thresholds and memory rules with their support level. Nothing is created or audited. |
| `POST /api/v1/setup-plans/{id}/apply` `{phase, overwrite_edited, plan_version}` | owner or org_admin | Applies; answers the preview plus each item's `outcome` (`created`, `updated`, `present`, `kept`, `skipped`, `failed` with `error`, `busy`, `not_applied`) and `outcomes` counts. A `plan_version` other than the checked-in one answers `409` (review the new preview). Writes one `setup_plan.applied` audit record (phase, plan version, outcome counts; `outcome: partial` when an item failed). |
| `GET /api/v1/setup-plans/{id}/checks` | owner or org_admin | The pilot checks with what was recorded, `passed`, `failed`, `not_run`, the gate text and `done`. |
| `PUT /api/v1/setup-plans/{id}/checks/{check_id}` `{status: pass\|fail\|not_run, note, evidence}` | owner or org_admin | Records one result (note at most 1000 characters, evidence at most 300: a task or approval id, or an `https://` link; control characters refused). Audited as `setup_plan.check_recorded` (status and whether a note or evidence was given, never their text). |

Members and viewers get `403` with who can apply the plan. Unknown plan or check: `404`.

## Phases

- **Pilot** (`phase: pilot`): A1 Chief of Staff, A7 Customer Care and A8 Finance Controller are active; the other agents are created **paused**, the assistants **turned off**; the budget is the pilot's AED 340. Workspaces wait for the whole plan.
- **Whole plan** (`phase: all`): every agent active and every assistant on, at their starting levels; the 5 business workspaces created; the budget AED 1,200.
- In both, **automations are created turned off** with their schedules and triggers off. The owner runs each by hand, checks the result, then turns it on.
- The page shows the plan's 8-week rollout; Week 1's gate is the pilot checks below.

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

| Plan item | ANUM | Support |
|---|---|---|
| Tenant "Shahzeb Business Group" | The tenant the plan is applied in | as-is |
| 6 workspaces | GROUP is the workspace you apply from; FLEET, TYRE, GALLERY, OPS, VOICE are created empty (whole plan) | mapped; agents stay in the applying workspace |
| Departments | `department` on agents; assistants use ANUM's department enum (Command, Marketing, Systems & Knowledge are `custom`; Commerce & Care and Product Support are `support`; Fleet & Tyres is `tyre_engineering`) | mapped |
| 12 agents | Agent profiles, `category: work`, shared with the workspace, instructions = common + agent text + approvals, schedule, KPI, supervisor | as-is |
| Autonomy Observe | Agent autonomy read `auto`, write `ask`, external `off` (drafts only) | mapped |
| Autonomy Internal | read `auto`, write `auto`, external `ask` | mapped |
| Routine / Approval-gated | Routine sends only through WhatsApp's auto-send list of approved templates; every external action asks | mapped (A7 starts at Observe) |
| Agent tools | Real tools only, resolved per deployment: `team.assign_task`; email `search_email`, `read_email`, `draft_reply`, `send_email`; WhatsApp `list_whatsapp_conversations`, `read_whatsapp`, `draft_whatsapp_reply`, `send_whatsapp_template`, `send_whatsapp`; commerce reads `commerce.get_order`, `list_orders`, `low_stock`, `sales_summary`; tyre `tyre_fleet_summary`, `tyres_due`, `purchase_plan`, `tyre_alerts`; marketing `marketing.summary`, `draft_social_post`, `schedule_post`; `web_search` when SearXNG is set. No agent gets price, stock or payment tools. Command Center, calendar, Reports, notifications, meetings, documents, automations, audit and gateway are pages, not tools: listed per agent as not mapped. | mapped |
| 24 skills | Tenant skill versions `sbg.sNN-...` 1.0.0 with the plan's trigger, steps and output as instructions, installed with only their declared tools; plus ANUM's built-in skills where their tools exist | as-is |
| 8 department assistants | Assistant profiles, autonomy `suggest` (route and propose, no approval authority), routing keywords; no inbound channel until an owner ticks one | as-is |
| 9 policy packs | 6 packs of `require_approval` / `allow` rules on real tool patterns (they only add approvals). MEMORY maps to memory governance, RUNTIME to the budget and fixed retry rules, ADMIN to the org_admin requirement and audit | mapped |
| Role templates | 10 role templates with real permission names; the pilot role cards' "may approve / must escalate" (Operations and Care, Finance Reviewer, Technical Administrator = Governance Administrator) are shown with them. ANUM enforces owner, member, viewer and org_admin; templates document the intended set | partial |
| AED approval thresholds | Approval rules with AED bands and approver steps ([Approval routing](approvals-and-risk.md#approval-routing)): refunds up to AED 250 to the named Care Lead and above to owners, goodwill discounts up to AED 100 and 5%, quotes up to AED 5,000 delegated, commitments above AED 5,000 in two steps, plus prices and stock, publishing, auto-posting and unknown external actions (owner). Delegated rules start with owners only until the person is named. Only a per-task cost estimate is unsupported; each threshold's support is listed on the page | mapped |
| Memory rules | Memory governance: 90-day expiry of remembered chat facts, provenance required. Per-category retention is not possible | partial |
| 9 knowledge folders | Shared knowledge collections 00-08 | as-is |
| 30 saved commands | The applier's quick commands (Work), text = the voice phrase | as-is |
| 25 automations | 18 workflows (turned off) with a `task.create` step; schedules in Asia/Dubai (turned off) or event triggers (turned off) for WhatsApp, email, files and failed runs. #8, #9, #10, #16, #24 need a webhook trigger from the source system; #18 runs by hand. #2, #3, #19 are built in (Command plans, team reminders, Meetings); #14 is part of #13; #6 is the WhatsApp auto-send list; #7 is the built-in [contact suppression list](privacy.md#contact-suppression); #4 is not supported | mapped |
| Budget caps | Workspace monthly cost limit = sum of the active agents' caps in USD at the AED peg (3.6725) | partial |
| Home priorities | Home's fixed Today's Focus, approvals and activity | not configurable |
| Integrations, people, 2FA, documents | Listed under "For you to do" | manual |

## Pilot checks

The 18 Week 1 cases (CC01-CC10 customer care, COD01-COD05 cash on delivery, OWN01-OWN03 owner commands) are in the blueprint with input, expected answer or action, approval gate, pass criteria and what ANUM does (`support`: supported, partial or unsupported). The **Pilot checks** tab lists them; an owner or org_admin records Pass, Fail or Not run with a note and an evidence reference. Results are per tenant with who and when, and audited. The summary reads "x of 18 passed"; Week 1 in the rollout shows the gate, which is done when all 18 pass.

ANUM never runs these cases against live data from the page. The parts it can check on its own governance are API tests (`services/api/tests/test_setup_plan_pilot_checks.py`), named in each case's `verified_by`:

- CC05 / OWN02: a payment-kind refund of AED 300 always needs an approval and can never act on its own, at any autonomy level; after the whole plan no agent holds a payment, price or stock tool.
- OWN03: a template send to 20 recipients always asks (bulk; ANUM's default threshold of 5 is stricter than the plan's 10).
- OWN01: "Show all COD orders awaiting remittance and group them by variance" plans read-only steps that need no approval.
- CC04: a WhatsApp STOP (or إيقاف, لا ترسلوا لي رسائل) puts the number on the [contact suppression list](privacy.md#contact-suppression), and every later send to it, by an agent, an automation or after an approval, is refused and audited; START lifts it.

The COD variance bands (COD02, COD03, COD05) are in the COD Reconciliation skill and the 17:00 automation's instructions, not enforced by code, so a person checks them.

## Not supported yet

- Per-agent AI budget caps (ANUM budgets per workspace and organization), budget alerts at 50, 75 and 90 percent (ANUM records 80 and 100), per-task cost estimates.
- Banking, bank reconciliation and any money movement; refund and discount tools; COD cash-count records.
- Courier tracking and delivery proof.
- Consent records for campaigns (the opt-out list itself exists: [Contact suppression](privacy.md#contact-suppression)).
- Departments as objects; automations that run as a chosen agent (a scheduled task runs as the schedule's owner, with the agent's duty in the prompt); agents placed in each business workspace by one apply (apply the plan in each workspace instead).
- An approval-ageing reminder (#4); configurable Home priorities; per-agent loop and retry limits.
- WhatsApp utility templates from the pilot pack: they need Meta submission and approval in the WhatsApp Business account; ANUM sends a template only after an owner ticks it on the auto-send list.

## Flutter

Settings › Workspace administration › **Setup plans** (`apps/mobile/lib/features/admin/setup_plans_screen.dart`) lists the plans with how much of each is applied here, and opens one plan's read-only preview: the pilot or whole plan, the counts and AI budget, the caller's access notes, the week-by-week rollout, every item (searched, filtered by section or to changes only, 12 at a time with "Show more", fixed reasons in the reader's language) and what ANUM cannot do yet. Applying a plan and the pilot checks stay on the web, and the screen says so. Owners and organization administrators only: anyone else sees the API's `403` explained. Tests: `apps/mobile/test/admin/org_admin_test.dart`.

## For the owner to do by hand

Invite the real people and roles (and org_admin in Keycloak), turn on two-step sign-in, turn on the two-person rule once there are two owners, connect the integrations in the plan's order, upload the knowledge documents, add the webhook triggers for automations 8, 9, 10, 16 and 24, test and turn on each automation, choose the routine WhatsApp templates, run and record the 18 pilot checks on synthetic accounts, and promote agents one step at a time.

## Tests

`services/api/tests/test_setup_plans.py` (blueprint validity and no secrets, read-only preview, member refused, unknown plan and stale version, pilot apply, automations turned off, re-apply without duplicates and its audit, renames from plan version 1 and a rename that keeps the owner's edits, whole plan after pilot with owner edits kept and "Replace my changes", autonomy never above the plan, owner without org_admin, found-by-name linking, tenant and workspace isolation, claims, agents created paused in one write, a stopped apply taken over with the requested status, failure and retry, removed items), `test_setup_plan_pilot_checks.py` (the checklist and the governance cases above), `test_postgres_setup_plans.py` (the pilot through PostgreSQL as the app role, RLS on both tables, claims, checks per tenant), `test_migrations.py`; web `test/setupPlan.test.ts` and `e2e/setup-plan.spec.ts` (preview, confirm, apply, whole plan, failure, member, errors, empty, stale plan, Arabic, pilot checks, and 1440x900, 1366x768, 1024x768, 768x1024, 390x844 on desktop and mobile).
