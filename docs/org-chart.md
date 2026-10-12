# AI team org chart

The **Org chart** tab on the [Agents](agents.md) page shows the workspace as a company: the owner at the top, ANUM under them as chief of staff, then the departments with their agents, [department assistants](team-work.md#department-assistants) and the people the Team directory places there. Every node is live data from existing APIs; nothing is invented, and something the API does not provide is shown as missing rather than guessed. It is a view, not a new model: there is no new endpoint, table or permission.

Code: `apps/web/src/OrgChartView.tsx` (chart, list, export, print), `apps/web/src/OrgMemberDrawer.tsx` (a member's full profile), `apps/web/src/orgChart.css`, and the pure helpers `apps/web/src/lib/orgChart.ts` (loading, grouping, status, tools to connections, filters, approval rules) and `apps/web/src/lib/orgChartImage.ts` (the exported picture). Messages: `org.*` in `lib/messages/orgChart.en.ts` and `orgChart.ar.ts`.

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
| Approval policy | `GET /api/v1/approval-policy` | Workspace rules in the drawer | The drawer says it could not read them |
| Workspace | `GET /api/v1/workspaces/current` | The workspace name under the owner | Left out |

The chart refreshes every 30 seconds while the page is visible and when it becomes visible again; Refresh does it at once. A failed refresh keeps the last numbers and says so.

## The tree

- **Owner**: you when you own the workspace (your sign-in name, else your Team profile); otherwise the owner from the Team directory, with "You are viewing as member".
- **You approve risky actions**: pending approvals in the workspace, linking to Approvals.
- **ANUM, Chief of Staff, one center**: how many agents and assistants, departments, tasks running, and tasks with no agent (ANUM's own work).
- **Departments**: known departments in a fixed order (Sales, Support, Operations and warehouse, Tyre engineering, Finance, Other), then the free-text departments agents were given, then "No department yet". An agent's free-text department joins a known one when it matches its English or Arabic name or a common synonym ("Customer Care" is Support, "Sales & Outreach" is Sales, "Finance & Approvals" is Finance); anything else, such as "Marketing & Content", is its own department. A department card shows its counts and first four members; "Show all N" shows the rest. On phones every department folds (an accordion).
- **Members**: agents first (those needing a person at the top), then assistants, then people. An agent node shows its name, what it does, its status, its work now (running, waiting for approval, failed today), up to three skills and one chip per connection its tools need.

### Status

One status per agent, the most urgent that applies: **Needs approval** (a pending approval on one of its tasks), **Needs attention** (a task failed in the last 24 hours, or it allows a tool or skill the workspace no longer provides), **Running**, **Paused**, **Idle**. Assistants are Idle or Off. The status is always written next to the dot.

### Tools

An agent's tools are its `allowed_tools` plus the tools its skills need. Built-in tools (`anum.respond`, `team.assign_task`) need no connection and are counted, not shown. The rest map to [connection setup](integrations-setup.md) items: email tools to the Email group (connected when any mailbox is), WhatsApp tools to WhatsApp Business, `commerce.*` to Sales channels, `marketing.*` to the Social media group, the tyre tools and `fleet.*`/`vehicle.*`/`maintenance.*` to Fleet and tyre data, `meetings.*` to Meetings, `developer.*` to Developer mode, `web_search` to Private web search, `external.action` to the outbound webhook and `files.*` to File storage. Any other family shows by its prefix with "Status unknown". A chip opens Integrations.

## Member drawer

Clicking a member opens a drawer (the shared `Drawer`: focus moves in, Tab stays inside, Escape closes and focus returns to the node).

For an **agent**: Open agent page, Edit and Pause or Resume (only when `can_edit`; a `PATCH` with the version read), and these sections, each with its own loading, empty, error and permission states:

- **What it does**: description, instructions, owner, sharing, status and work now, model route, knowledge, last change.
- **Assign a task**: runs a task as the agent through `POST /api/v1/tasks` and `/run`, the same governed path as the agent page; anything risky waits in Approvals; budget stops say so.
- **Skills**: every skill with its description and whether it still loads.
- **Tools and connections**: every connection with its tools, its status and what to do about it.
- **Alone or with your approval**: per tool category, the agent's level never looser than the workspace floor, the agent's tools in that category, and the workspace approval policy (two-person rule, medium-risk approvals).
- **Current and recent tasks**: `GET /api/v1/tasks?agent_id=` ten at a time with Load more; each opens Tasks.
- **Latest result** and **Last error**: the latest run of the newest finished and newest failed task (`GET /api/v1/tasks/{id}/latest-run`), with how to fix it.
- **Schedules and automations**: automations are not linked to a single agent in the API yet, so this says so and links to Automation.

For an **assistant**: instructions, routing keywords, inbound channels, tools, and a link to Assistants and Team (owners change assistants). For a **person**: job title, role, email, time zone, capacity and whether they take assignments, with a link to Team.

## List view

"List" shows every member as a table (name, department, role, status, work now, skills, tools) with search (name, description, job title, department, skill or tool name), and filters for department, status, tool and kind. Below 1100 px each row becomes a card, so nothing scrolls sideways on phones or tablets. The choice is remembered per browser (`anum.orgChart.layout`); the tab choice too (`anum.agents.tab`).

## Export image

"Export image" draws the whole chart, every member with status, work, role, skills and tools, as a self-contained SVG (`orgChartSvg`; no outside fonts, images or links, so the canvas is not tainted), turns it into a PNG at twice the size (kept under the browser's canvas limit) and downloads `anum-ai-team-YYYY-MM-DD.png`. It uses the current theme's colours and right-to-left text in Arabic. No library is added.

## Print or save PDF

"Print or save PDF" opens the browser's print dialog. While the org chart is open, printing shows a document of every member's full profile (instructions, owner, sharing, skills, tools with their connections, approval levels; assistants' instructions and keywords), one card per member, never split across pages, instead of the app.

## Accessibility and languages

The tree is nested lists with headings (owner and ANUM, departments, members); every node's name is a button, department headers are disclosure buttons (`aria-expanded`), tool chips have names that include their status, and status is never colour alone. English and Arabic, right to left. Dark and light themes use the shared tokens.

## Tests

`apps/web/test/orgChart.test.ts` (grouping, aliases, status, tools to connections, counts, filters, approval rules, the loader's paging and partial failures, the SVG and its wrapping) and `apps/web/e2e/org-chart.spec.ts` with `e2e/support/orgChartApi.ts` (tree, drawer and its actions, Escape and focus, tool and approvals links, list filters, PNG download, print, empty, error, denied, partial data, Arabic, phones, axe in dark and light).

## Not yet

- Per-agent KPIs (success rate, time saved, cost per agent): the API has no per-agent metrics endpoint; only task statuses are counted.
- Automations and schedules per agent: triggers and automations do not carry an agent id.
- A link straight to one task: the Agents view can open Tasks but not a given task without a change to the shell's navigation (`main.tsx`), and the Integrations, Assistants, Team and Automation links reload the page on that view's URL hash until the shell passes `onGo` to `AgentsView`.
- Department assistants have no live task counts: their work is routed into work items, which do not carry the assistant on the task list.
- People are placed only when their Team profile has a department.
