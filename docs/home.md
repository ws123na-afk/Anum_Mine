# Home and proactive insights

Home is the first screen of the ANUM 360 app (mockups `mobile-flow-overview.jpg` screens 2 and 8, `mobile-14-screens.jpg` screen 05, `mobile-command-flow-12.jpg` screen 2, `mobile-command-flow-16.jpg` first screen; see [design/anum360.md](design/anum360.md)). It greets the user, takes a request (typed or spoken), offers quick commands and shortcuts, shows Today's Focus and Recent Activity from the workspace's real modules, and keeps the user's pinned apps. "ANUM Proactive" lists what ANUM noticed, each with one action.

API: `services/api/anum_api/home.py` (routes, stores, source registry), `home_sources.py` (built-in sources), `db/home_repository.py` (PostgreSQL). Flutter: `apps/mobile/lib/features/home/` (`HomeScreen`, `ProactiveInsightsScreen`, `HomeController`).

## Endpoints

All under `/api/v1/home`. Every route needs an active workspace membership (`task:read`, so viewers too). Pins, insight states and the greeting name belong to the caller only: no path or body names a user.

| Route | What it does |
|---|---|
| `GET /summary` | One call for the Home screen: `profile.display_name`, `focus[]`, `counts{source: n}`, `progress{completed, open}` (tasks finished since midnight UTC against the ones still open), `activity[]` (newest task changes and automation runs) and `sources[]` (the status of every part). |
| `PUT /profile` | `{"display_name": "..."}` sets the name Home greets the caller by (blank clears it). Tenant-wide, per user. |
| `GET /pins`, `POST /pins`, `PATCH /pins/{id}`, `DELETE /pins/{id}` | The caller's pinned apps in this workspace, in their order. A pin is `kind` `link` (an `https://` address without credentials, such as TyrePulse or VoiceQuote), `workspace` (a workspace the caller is an active member of, such as PMV Operations) or `feature` (an ANUM feature key, below), with a `label` and an optional `icon` name. Up to 24; the same target twice is refused (409). |
| `PUT /pins/order` | `{"ids": [...]}`: every pin id once, in the new order. |
| `GET /commands` | Quick commands: the built-in ones (stable `key`, `built_in: true`) for the modules the caller can use, then the caller's own from the command module. `can_edit` is false for viewers (they cannot run commands). |
| `/api/v1/quick-commands` (command module) | The caller's own quick commands (`category` `work`, `personal` or `custom`, `title`, `text`) are created, changed and deleted there (`docs/command.md`); it is their only store. Home only reads them. |
| `GET /insights?include_hidden=` | What ANUM noticed (below): `items[]`, `hidden` (dismissed or snoozed count) and `sources[]`. |
| `POST /insights/{key}/dismiss` | Hide the insight for the caller until they restore it. |
| `POST /insights/{key}/snooze` | `{"hours": 1..168}` (default 24): "Later". It comes back after that if it is still true. |
| `DELETE /insights/{key}/state` | Restore a dismissed or snoozed insight. |

Dismiss and snooze accept only keys of insights that are current for the caller (404 otherwise), so nothing can be stored for an insight the caller cannot see. Each user keeps at most 500 states; the oldest go first.

Every change is audited with the caller as actor: `home.profile_updated`, `home.pin_added`, `home.pin_updated`, `home.pin_removed`, `home.pins_reordered`, `home.insight_dismissed`, `home.insight_snoozed`, `home.insight_restored`.

### Targets

Focus items, activity, pins and insight actions say what they open as `{"feature": ..., "id": ...}`. Feature keys: `command`, `chat`, `agents`, `voice`, `automations`, `tasks`, `calendar`, `reports`, `documents`, `integrations`, `knowledge`, `approvals`, `email`, `whatsapp`, `autonomy`, `settings`, `insights`, `commerce`, `team`, `favorites`, `tyres`, `marketing`. The app's shell maps each key to a screen; Home hides tiles and pin choices for features the shell cannot open (`availableFeatures`), so there is no dead button.

## Sources

Today's Focus and the insights come from *sources*. Each source reads one module through that module's own store or repository, inside the caller's tenant and workspace (RLS applies), and declares the permission its data needs. For every source the response says its `status`:

- `ok`: read.
- `not_permitted`: the caller lacks the permission (viewers see no email, for example). Left out silently.
- `not_installed`: the module is not part of this deployment. `meetings` is reported this way until the meetings or calendar module registers a focus source, so Home shows no meetings before then.
- `not_connected`: the module is installed but nothing is connected (no mailbox).
- `unavailable`: the module failed. It is logged with its error class only; the rest of Home still answers and the app says which part could not load.

Built-in Focus sources: `approvals` (pending approvals, high severity when one is high risk), `email` (unread high-priority mail across the mailboxes the caller can see), `tasks` (queued, running and waiting tasks with their run stage as progress: planning 20 %, tool ready 40 %, waiting for approval 50 %, executing 75 %), `automations` (runs failed in the last 24 hours).

Built-in insight sources and their action:

| Source | Insight | Action |
|---|---|---|
| `autonomy` | A pending autonomy proposal ("you approve this every time") | Review (opens Autonomy) |
| `approvals` | An approval pending for more than 24 hours | Review |
| `tasks` | A task that failed in the last 7 days and was not retried | Analyze (a command) |
| `tasks` | A queued or running task that has not moved for 2 hours | Check now |
| `automations` | An active workflow whose last run failed (the key includes the failure time, so a new failure comes back after a dismiss) | Analyze (a command) |
| `integrations` | An integration whose last recorded health check is degraded (no probe is made) | Check now |
| `email` | A mailbox whose sign-in expired; unread high-priority mail | Check now; Analyze (a command) |

Tasks have no due date, so "overdue" means failed and not retried, or stalled. An action is either `open` (a target) or `command` (a request handed to ANUM Command, which shows its plan before anything runs; nothing is executed from the insight itself).

### Adding a source

Other modules add their findings without changing Home:

```python
from anum_api.home import FocusSource, InsightSource, register_focus_source, register_insight_source

register_insight_source(InsightSource("learning", collect_learning_insights, Permission.TASK_READ))
```

A collector takes a `HomeScope` (context, the request's repository, the app state, `now`) and returns a `FocusResult` or a list of `Insight`s, sync or async. Insight keys must be `<source name>:<id>` and stable while the finding is the same; keys of another source are rejected. Raise `SourceNotInstalled` or `SourceNotConnected` to report those states. Give each insight `kind`, `params` and English `title`/`finding`/`question`: the apps translate known kinds and show the English text for new ones. The learning-patterns module (being built separately) registers its insights this way; a calendar module should register a `meetings` focus source, which also stops it reading as not installed.

## Arrange Home

Each person chooses which parts of the web Home are shown and in what order (**Arrange Home** in the header, `apps/web/src/HomeArrangeDialog.tsx`, a lazy chunk; move up and down with buttons, show or hide, back to the default). Parts: `glance`, `decisions`, `focus`, `activity`, `meetings`, `alerts`, `insights`, `pinned`, `goto` (the default order). `GET /api/v1/home/layout` answers `{sections, hidden, default, customized}`; `PUT /api/v1/home/layout` `{sections}` (known parts, each once, at least one; audited as `home.layout_updated`); `DELETE` goes back to the default. Stored per person and tenant in `home_profiles.sections` (migration `0129_setup_plan_gaps`); changing the greeting name keeps it. With a chosen order Home lays the parts out in two columns in that order, the glance, pins and quick links across the width; with none it keeps the default page. The setup plan sets the applier's own order to put decisions first; it never changes anyone else's Home.

## Storage and isolation

Migration `0046_home` adds `home_profiles` (tenant and user), `home_pins` and `home_insight_states` (tenant, workspace and user). All three are under forced RLS whose policies check `anum.user_id` as well as the scope, and the stores repeat the same filters. Expand only.

"Download my data" includes a `home` section (name, pins, insight states) and "Delete my data" removes them, counted as `home_items` ([privacy.md](privacy.md)).

## The app

`HomeScreen` is the shell's Home tab (`WorkspaceHome`). It takes the controller and callbacks: `onCommand(text)` opens ANUM Command (with the typed request or a quick command's prompt, or empty; without a command flow the request goes to the Assistant chat), `onVoice` opens the orb's surface, `onOpen(target)` opens a feature (tasks open the task itself when it is loaded), `onSwitchWorkspace(id)` switches to a pinned workspace after the API confirms the membership, and `onOpenLink` opens a pinned web app in the browser. `availableFeatures` lists what the shell can open; Commerce appears as a tile when the shell has it; Calendar opens Calls & Meetings, Reports and Knowledge open the library screens (when the library is available), and pins can also open Team, Favorites, Tyre planning and Marketing. The shell adds its own cards after Today's Focus (`sections`): favorites and recent reports, and Tyre planning. The shell's greeting header (avatar, name, date, bell) replaces Home's own greeting; ANUM Proactive is then reached from the line under it. On a phone (one column, below 840 px) Home leads with what needs a decision, as the web does: the greeting, the ask box with mic, Today's Focus with progress (approvals waiting first), the shell's cards, Recent Activity, then the Work/Personal/Custom quick commands, the shortcut tiles, "Your AI agent is ready" with the ANUM orb and Pinned, so the shortcut tiles never push the approvals below the fold. From 840 px wide it uses two columns as in the mockups: the ask box, quick commands, shortcut tiles and "Your AI agent is ready" on the left; Today's Focus, the shell's cards, Recent Activity and Pinned on the right.

States: loading, the summary failing (what failed, Try again), no workspace access (who can fix it), empty focus/activity/pins (each with its next action), a part that could not load (named, with Try again), viewers without quick commands (why), and a failed save said inside the sheet. Pins are reordered with Move up/Move down buttons, so no drag gesture is needed.

Differences from the mockups: the numbers and names are the workspace's own; there are no meetings until a calendar module exists; the header avatar shows the greeting name's initial (there is no profile photo in ANUM); "Send reminder" and "Compare" are supported verbs but no built-in source uses them yet (no reminder API exists).

## The web home

On the web (`apps/web`) Home and ANUM Command are two pages with two jobs. **Home** (`#home`, the landing page) has the page head **Operations overview** and two tabs: **Overview** (`#home`, the business at a glance, [below](#operations-overview)) and **My day** (`#home-day`, what needs the person today, the parts in the table below; Arrange Home applies to it). **ANUM Command** (`#command`) is where orders are given ([command.md](command.md#the-web-command-center)). Home has no composer, plan or saved commands; its "Give ANUM an order" button and an insight's command action open ANUM Command, which plans the request (read-only) and runs nothing until the person presses Start.

Code: `src/HomeView.tsx` and `src/home.css` for the page, `src/lib/home.ts` for the pure rules (unit tested in `test/home.test.ts`), `src/lib/homeApi.ts` for every API call. The panel states (loading, empty with a next action, error with Retry, permission denied, "Show more") are the shared `src/Panels.tsx`, also used by ANUM Command.

| Part | What it shows | Backed by |
|---|---|---|
| Greeting | Good morning/afternoon/evening with the Home name (`profile.display_name`, else the sign-in name) and today's date, under the page head with Refresh and "Give ANUM an order" | `GET /home/summary` |
| Today at a glance | Approvals waiting; tasks running, failed, waiting and not started; unread email and the number of mailboxes; unread WhatsApp messages and chats (the first 50 unread chats, "50+" beyond); today's meetings and the next one; automation alerts (live alerts for this user plus runs failed in the last 24 hours); AI budget use this month (the workspace or organisation budget closest to its limit, cost and tokens). Each card opens its area | Shell tasks and approvals, `GET /email/accounts`, `GET /whatsapp/conversations?filter=unread`, `GET /calendar/events` (local day), automation events and `counts.automations`, `GET /model-budgets` |
| Waiting for your decision | Pending approvals, three at a time, with the exact tool, risk, the payload hash, **Approve** and **Deny**. They go through the shell's approval flow (`POST /approvals/{id}/approve` with `{"payload_hash"}`), so the two-person rule and approval chains apply; a refusal is shown on the card | `GET /approvals` |
| Today's focus | `focus[]` with severity and progress, five at a time; a part that failed is named with Retry | `GET /home/summary` |
| Recent activity | `activity[]`, six at a time | `GET /home/summary` |
| Today's meetings | Time, title, place and Join (the event's https link) for the local day, ended ones last | `GET /calendar/events` |
| Automation alerts | This user's alerts (opens the run) and the failed-run focus items | Event stream, `GET /home/summary` |
| ANUM noticed | Insights with their action, Later (snooze 24 h) and Dismiss | `GET /home/insights`, `POST /insights/{key}/dismiss`, `/snooze` |
| Pinned | The caller's pins: a part of ANUM opens its page, a link opens in a new tab, a workspace pin switches workspace. Edit pins adds a part or an https link, moves pins up and down and removes them (24 at most) | `/home/pins`, `PUT /home/pins/order` |
| Go to | Quick links to ANUM Command, Inbox, WhatsApp, Tasks, Approvals, Calls & Meetings, Automations, Reports, Documents, Agents, Voice and AI Chat | none |

Permissions are the API's: a part the role cannot read says so (for example "Only workspace owners see budget use", the calendar for viewers), and the rest of Home still loads. Workspace events refresh the summary and insights quietly.

Tests: `e2e/home.spec.ts` (the overview with workspace data, Home is not the command page, approving sends the payload hash, an insight hands its request to ANUM Command, pins, a failing part and a viewer, the palette, notifications and the sidebar, axe and no horizontal overflow at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844 in English and Arabic, dark and light). Mocks: `e2e/support/homeApi.ts`.

### Operations overview

The Overview tab (`src/HomeOperations.tsx`, figures in `src/lib/ops.ts`, unit tested in `test/ops.test.ts`; shared charts in `src/OpsParts.tsx`) reads each module's own API, so a module that is off or refused shows its own state and the rest of the page still loads. A month picker in the page head chooses the month (never past the current one). Every amount stays in its own currency.

| Part | What it shows | Backed by |
|---|---|---|
| Figures | Revenue, expenses and net profit for the month with the change against the month before (from the books' trend); the fleet (active vehicles, items due soon); kilometres driven this month against the month before; pending actions (everything the Attention center counts) | `GET /business/books/report`, `GET /vehicles/overview` |
| Income vs. expenses | Six months of revenue and costs as two lines, the month's figures labelled, a table view | books report `trend` |
| Spend by category | The month's cost lines largest first with their share; above five, the rest is one "Other" | books report `pnl` |
| Attention center | Most urgent first: approvals waiting, vehicle renewals and services due (with days left), expired documents and documents to review, late deliveries, open returns and refunds owed, purchases to review, overdue bills and allocations waiting for review. Each opens its page | shell approvals, vehicles `due`, `GET /documents/meta`, `GET /purchases/overview`, books report `balances` and `allocations_in_review` |
| Fleet status | Five vehicles: name, registration, odometer, fuel economy (km per litre, from full-to-full fills this month), the next service or renewal and the status | `GET /vehicles`, `GET /vehicles/overview` (`by_vehicle_usage`) |
| Recent activity | The My day activity list | `GET /home/summary` |
| Attendance | The person's week (scheduled days attended, or days worked without a schedule) and today's first arrival and status | `GET /attendance/summary?period=week`, `GET /attendance/today` |
| Document expiry | The next document to expire, with the counts expiring soon and expired | `GET /documents/meta`, `GET /documents?sort=expiry` |
| Purchases and delivery | Orders this month, on the way, awaiting delivery, open returns and how many are late | `GET /purchases/overview` |

Not built (no data behind them yet): a workspace location switch, a fleet "no change" comparison of vehicle counts, and a per-order delivery stepper on Home (the Purchases page has each order's timeline).

Tests: `e2e/home-operations.spec.ts` (the overview with every part, a refused module, Arabic, axe), and the `home` and `home-day` gate pages in `e2e/support/gatePages.ts`.

