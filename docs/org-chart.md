# AI team org chart

The **Org chart** tab on the [Agents](agents.md) page shows the workspace as a company: the owner at the top, ANUM under them as chief of staff, then the departments with their agents, [department assistants](team-work.md#department-assistants) and the people the Team directory places there. Every node is live data from existing APIs; nothing is invented, and something the API does not provide is shown as missing rather than guessed. It is a view, not a new model: there is no new endpoint, table or permission.

Code: `apps/web/src/OrgChartView.tsx` (chart, list, export, print), `apps/web/src/OrgProfile.tsx` (a member's full profile page), `apps/web/src/OrgMemberDrawer.tsx` (the quick look), `apps/web/src/orgChart.css`, and the pure helpers `apps/web/src/lib/orgChart.ts` (loading, grouping, status, tools to connections, KPIs, department health, column balancing, profile addresses, performance, filters, approval rules) and `apps/web/src/lib/orgChartImage.ts` (the exported picture). Messages: `org.*` in `lib/messages/orgChart.en.ts` and `orgChart.ar.ts`. Addresses: `#agents/team` opens the tab and `#agents/team/<agent|assistant|person>/<id>[/<tab>]` a profile (`lib/shell.ts` sends every `#agents/...` hash to the Agents view).

## What it reads

All requests carry the caller's tenant and workspace like every other view, so the API's own permission checks and row-level security decide what appears.

| Source | Request | Used for | If it fails |
|---|---|---|---|
| Agents | `GET /api/v1/agents?limit=100&cursor=` (up to three pages; a workspace holds at most 200 agents) | Every agent node | The whole view shows the error with Try again; `403` shows the permission-denied state |
| Department assistants | `GET /api/v1/assistants` | Assistant nodes (`work:read`) | Shown without them, with a note |
| People | `GET /api/v1/team/people` | People in their department, the owner's name (`work:read`) | Shown without them, with a note |
| Open tasks | `GET /api/v1/tasks?status=created&status=queued&status=running&status=waiting_approval&limit=100` | Running counts per agent (`agent_id`), ANUM's own work | Counts read as zero, with a note |
| Failed tasks | `GET /api/v1/tasks?status=failed&created_after=<24 hours ago>&limit=100` | "Failed today" | As above |
| Approvals | `GET /api/v1/approvals` (pending only) | Approvals waiting per agent (through the task's `agent_id`) and the strip | Callers without `approval:read` see "Owners and approvers see what is waiting." |
| Connection setup | `GET /api/v1/integrations/setup` | Each tool's connection status | Tools show "Status unknown", with a note |
| Agent catalog | `GET /api/v1/agents/catalog` | Skill names, the tools skills need, tool categories, the autonomy floor | Skill ids instead of names |
| Approval policy | `GET /api/v1/approval-policy` | Workspace rules in the profile and quick look | They say they could not read them |
| Workspace | `GET /api/v1/workspaces/current` | The workspace name under the owner | Left out |

Open tasks are read 100 at a time, up to 500 (newest first). The chart refreshes every 30 seconds while the page is visible and when it becomes visible again; Refresh does it at once. A refresh re-reads agents, tasks and approvals; the catalog, connection setup, people, assistants and approval policy are reused for five minutes (`STATIC_TTL_MS`) unless the last read of one of them failed. A failed refresh keeps the last numbers and says so.

## The chart

- **Header band**: you (or the workspace owner from the Team directory) → **ANUM, Chief of Staff, one center** (plans the work, assigns agents and asks before anything risky) → **You approve risky actions** with the pending approvals and a link to Approvals. Under it a KPI strip: agents, running now, waiting for approval (opens Approvals), failed today, connections not working (distinct connections the team's tools need that are not connected; opens Integrations), and tasks without an agent (ANUM's own running work and approvals).
- **Departments** hang from one rail in balanced columns: as many columns as fit (at least ~320 px each, at most four), departments placed tallest first on the shortest column and kept in chart order inside a column (`balanceColumns`), so no tall column stands next to an empty one. Known departments come in a fixed order (Sales, Support, Operations and warehouse, Tyre engineering, Finance, Other), then the free-text departments agents were given, then "No department yet". An agent's free-text department joins a known one when it matches its English or Arabic name or a common synonym ("Customer Care" is Support, "Sales & Outreach" is Sales, "Finance & Approvals" is Finance); anything else, such as "Marketing & Content", is its own department.
- **A department card** shows its name, health (Healthy, N waiting for approval, or N need attention, with a coloured top edge only when it needs you), its agents, assistants, people and running counts, and its **lead**: the first enabled department assistant, else the first person, else "No assistant or person yet". Agent cards follow (four, then "Show all N"); assistants and people follow as compact rows. On phones (< 768 px) the chart is one indented column and every department folds (an accordion).
- **An agent card** has the same rows on every card: avatar, name and a quick-look button; status and role (kind, category, sharing); a one-line mission; running, waiting and failed-today counts; up to two skills and three connection chips (each opens Integrations). The name opens the full profile.

## Profile page

Every member opens a full-width profile inside the Agents page at a linkable address (`#agents/team/agent/<id>`, with the tab as a last part, for example `/performance`); reload keeps the profile and tab, and Back returns to the chart (Back to a profile reached from elsewhere goes to the chart). Each section loads on its own and has its own loading, empty, error (with Try again) and permission states; a refresh keeps the last answer on screen.

The header shows the avatar, name, status, role, department and owner, the mission, and for agents the actions the API already supports: Assign a task, Open agent page (chat, memory), Edit and Pause or Resume (only with `can_edit`; a `PATCH` with the version read), plus counts (running, waiting, failed today, skills, tools, connections not ready).

Agents have six tabs:

- **Overview**: the mission and the full instructions; owner, sharing, category, model route, knowledge, created, last changed and edit number; **alone or with your approval**: for each kind of action (reading, changes in ANUM, actions outside ANUM) the agent's level never looser than the workspace floor, what it means and the agent's tools of that kind, plus the workspace approval policy (two-person rule, medium-risk approvals); budget and usage (measured per workspace, not per agent, with a link to Budgets); schedules and automations (not linked to agents in the API, so it says so).
- **Work**: assign a task (through `POST /api/v1/tasks` and `/run`, the governed path; anything risky waits in Approvals; budget stops say so), then running now, waiting for approval, failed, and all tasks newest first, each from `GET /api/v1/tasks?agent_id=&status=` ten at a time with Load more.
- **Skills and tools**: every skill from `GET /api/v1/agents/{id}/skills` with its state, description, source and version, how it joins (always, or the words that call it), its tools in order with whether each is allowed and its connection, and the reason when it does not load; then every tool the agent may use with its description, kind of action, risk, connection and the fix (connect it in Integrations, or remove it from the agent when the workspace no longer has it).
- **Approvals**: approvals on the agent's latest 100 tasks from `GET /api/v1/approvals`, pending first, with task, action, risk, status, when asked and when decided.
- **Activity**: the latest result, the last error with how to fix it, and the latest run of the five newest tasks step by step (`GET /api/v1/tasks/{id}/latest-run`).
- **Performance**: for the last 7 and 30 days from `GET /api/v1/tasks/summary?agent_id=&created_after=`: tasks, completed, failed, cancelled, still open, success rate (completed of completed plus failed) and, for 30 days, the average time from creating a task to its last update for completed tasks (an upper bound of the run time, since the API stores no separate start and end; it includes time waiting for approval). Cost, tokens, time saved and customer outcomes per agent are not recorded, and the tab says so.

Department assistants have Overview (instructions, autonomy, routing keywords, channels, dates, link to Assistants), Work (work items assigned to the assistant, `GET /api/v1/work-items?assignee_type=assistant&assignee_id=&status=all`, 20 at a time) and Skills and tools (each tool's connection and fix). People have Overview (role, email, time zone, capacity, whether they take assignments, and their workload row from `GET /api/v1/work-items/workload`: open, in progress, blocked, overdue, load) and Work (their assigned work items).

## Quick look

The eye button on a card opens the same facts in a side drawer (the shared `Drawer`: focus moves in, Tab stays inside, Escape closes and focus returns), with assign a task, pause or resume, the latest tasks, result and error, and **Open full profile**.

## List view

"List" shows every member as a table (name, department, role, status, work now, skills, tools; a name opens the profile) with search (name, description, job title, department, skill or tool name), and filters for department, status, tool and kind. Below 1100 px each row becomes a card, so nothing scrolls sideways on phones or tablets. The choice is remembered per browser (`anum.orgChart.layout`); the tab choice too (`anum.agents.tab`).

## Export image

"Export image" draws the whole chart, every member with status, work, role, skills and tools, as a self-contained SVG (`orgChartSvg`; no outside fonts, images or links, so the canvas is not tainted), turns it into a PNG at twice the size (kept under the browser's canvas limit) and downloads `anum-ai-team-YYYY-MM-DD.png`. It uses the current theme's colours and right-to-left text in Arabic. No library is added.

## Print or save PDF

"Print or save PDF" opens the browser's print dialog. While the org chart is open, printing shows a document of every member's full profile (instructions, owner, sharing, skills, tools with their connections, approval levels; assistants' instructions and keywords), one card per member, never split across pages, instead of the app.

## Accessibility and languages

The tree is nested lists with headings (owner and ANUM, departments, members); every node's name is a button, department headers are disclosure buttons (`aria-expanded`), tool chips have names that include their status, and status is never colour alone. English and Arabic, right to left. Dark and light themes use the shared tokens.

## Flutter

Settings › Workspace administration › **Org chart** (`apps/mobile/lib/features/admin/org_chart_screen.dart`) is read-only. There is no org chart route; like the web it reads `GET /api/v1/agents` (100 a page, up to 500 agents, then says so), `GET /api/v1/assistants` and `GET /api/v1/team/people`, and groups members by department in the web's order (known departments, free-text ones, then "No department yet"). Each department lists agents (Active, Paused, or Needs a connection when a tool or skill is unavailable), assistants (Active or Off) and people (job title and role), 8 at a time with "Show more", with search by name, description, job title or department. A `403` on agents is the permission state; assistants or people that fail to load are named above the chart instead of hiding it. Two columns from 840 px, one on phones. Profiles, live task counts, approvals and the image and print exports are web only. Tests: `apps/mobile/test/admin/org_admin_test.dart`.

## Tests

`apps/web/test/orgChart.test.ts` (grouping, aliases, status, tools to connections, counts, KPIs, department health and lead, column balancing, profile addresses, performance from counts and timestamps, the agent's approvals, filters, approval rules, the loader's paging, partial failures and reuse, the SVG and its wrapping) and `apps/web/e2e/org-chart.spec.ts` with `e2e/support/orgChartApi.ts` (header band and KPIs, departments, cards, quick look and its actions, Escape and focus, every profile tab, linkable addresses, reload and Back, people and assistant profiles, a missing member, tool and approvals links, list filters, PNG download, print, empty, error, denied, partial data, Arabic, phones, axe in dark and light over the chart, list, drawer and every profile tab).

## Not yet

- Per-agent cost, tokens, time saved and outcomes: the API records them per workspace or not at all; the profile counts tasks by status and estimates time to finish from task timestamps.
- More than 500 open tasks: counts cover the newest 500; approvals on older ones count as ANUM's own.
- Browser Back after leaving the Agents page through a link (to Tasks, for example) changes the address without changing the view: the shell keeps one history entry per view and does not listen to Back.
- Automations and schedules per agent: triggers and automations do not carry an agent id.
- A link straight to one task: the Agents view can open Tasks but not a given task without a change to the shell's navigation (`main.tsx`), and the Integrations, Assistants, Team and Automation links reload the page on that view's URL hash until the shell passes `onGo` to `AgentsView`.
- Department assistants have no live task counts: their work is routed into work items, which do not carry the assistant on the task list.
- People are placed only when their Team profile has a department.
