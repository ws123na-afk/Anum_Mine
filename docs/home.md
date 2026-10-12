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

## Storage and isolation

Migration `0046_home` adds `home_profiles` (tenant and user), `home_pins` and `home_insight_states` (tenant, workspace and user). All three are under forced RLS whose policies check `anum.user_id` as well as the scope, and the stores repeat the same filters. Expand only.

"Download my data" includes a `home` section (name, pins, insight states) and "Delete my data" removes them, counted as `home_items` ([privacy.md](privacy.md)).

## The app

`HomeScreen` is the shell's Home tab (`WorkspaceHome`). It takes the controller and callbacks: `onCommand(text)` opens ANUM Command (with the typed request or a quick command's prompt, or empty; without a command flow the request goes to the Assistant chat), `onVoice` opens the orb's surface, `onOpen(target)` opens a feature (tasks open the task itself when it is loaded), `onSwitchWorkspace(id)` switches to a pinned workspace after the API confirms the membership, and `onOpenLink` opens a pinned web app in the browser. `availableFeatures` lists what the shell can open; Commerce appears as a tile when the shell has it; Calendar opens Calls & Meetings, Reports and Knowledge open the library screens (when the library is available), and pins can also open Team, Favorites, Tyre planning and Marketing. The shell adds its own cards after Today's Focus (`sections`): favorites and recent reports, and Tyre planning. The shell's greeting header (avatar, name, date, bell) replaces Home's own greeting; ANUM Proactive is then reached from the line under it. Layout follows the mockups: "What can I help you with today?", ask box with mic, Work/Personal/Custom quick commands, the shortcut tiles, "Your AI agent is ready" with the ANUM orb, Today's Focus with progress, Recent Activity and Pinned. From 840 px wide it uses two columns.

States: loading, the summary failing (what failed, Try again), no workspace access (who can fix it), empty focus/activity/pins (each with its next action), a part that could not load (named, with Try again), viewers without quick commands (why), and a failed save said inside the sheet. Pins are reordered with Move up/Move down buttons, so no drag gesture is needed.

Differences from the mockups: the numbers and names are the workspace's own; there are no meetings until a calendar module exists; the header avatar shows the greeting name's initial (there is no profile photo in ANUM); "Send reminder" and "Compare" are supported verbs but no built-in source uses them yet (no reminder API exists).
