# Team Work

ANUM lets a workspace's people assign work to each other, to ANUM and to department assistants, and keeps everyone told: who has what, what is due, what is late. It is the place to say "ANUM, ask Ahmed to call the supplier by Thursday" and have Ahmed find it on his phone.

Implemented in `services/api/anum_api/team_work.py` (work items, people, activity, commands, reminders), `assistants.py` (department assistants), `notifications.py` (in-app inbox and email), `team_commands.py` (the sentence parser), `team_store.py` and `db/team_repository.py` (storage), migration `0039_team_work`. Web: `apps/web/src/TeamView.tsx`, `AssistantsView.tsx`, `NotificationBell.tsx`. Flutter: Settings › Team work (`apps/mobile/lib/features/team/`). Contracts: `packages/contracts/src/team.ts`.

## Work items

A work item has a title, details, status (`todo`, `in_progress`, `blocked`, `done`, `cancelled`), priority (`urgent`, `high`, `normal`, `low`), an optional assignee, an optional due date, an optional **ANUM follow-up** time, an estimate in hours, a checklist, comments and attachments (files from [Files](files.md), linked by id; deleting the file from Files shows the attachment as unavailable). Every change bumps `version`; `PATCH` must send the version it read, and a stale one answers `409` ("Someone changed this work item after you opened it. Reload it...").

The assignee is one of:

| `assignee.type` | `assignee.id` | Notes |
|---|---|---|
| `user` | the member's user id | Must be an active member who is not a viewer (`422` otherwise, naming why). |
| `anum` | always `anum` | ANUM itself. |
| `assistant` | a department assistant's id | Must exist and be turned on. |

| Route | Who | What |
|---|---|---|
| `GET /api/v1/work-items?scope=all\|mine\|created\|anum&status=open\|all\|<status>&priority=&assignee_type=&assignee_id=&overdue=true&q=&sort=due\|priority\|updated&limit=&offset=` | every member | Paged list (`items`, `total`, `next_offset`); `mine` is "My tasks", `anum` is work for ANUM and assistants; `q` searches title and details. |
| `POST /api/v1/work-items` | members, owners | Create (optionally with checklist lines); notifies the assignee. |
| `GET /api/v1/work-items/{id}` | every member | Detail with checklist, comments and attachments; `can_edit`/`can_delete` say what the server will allow this caller. |
| `PATCH /api/v1/work-items/{id}` | creator, assignee, owners | Change fields; `clear_assignee`, `clear_due`, `clear_follow_up` clear them. A new due date earns a new overdue reminder. |
| `DELETE /api/v1/work-items/{id}` | creator, owners | Deletes it with its comments, checklist and attachment links (not the files). |
| `POST/DELETE .../comments[/{id}]` | members comment on any item; authors and owners delete | Commenting notifies the creator and the assignee. |
| `POST/PATCH/DELETE .../checklist[/{id}]` | creator, assignee, owners | At most 100 entries. |
| `POST/DELETE .../attachments[/{id}]` `{file_id}` | creator, assignee, owners | At most 50; the same file once (`409`). |
| `POST /api/v1/work-items/{id}/hand-off` | creator, assignee, owners with `task:create` | For work assigned to ANUM or an assistant: creates the agent task (with `assistant_id` for an assistant) and links it; the client then runs it with `POST /api/v1/tasks/{id}/run`. |
| `GET /api/v1/work-items/workload` | every member | Per person, ANUM and assistant: open, in progress, blocked, overdue, due in seven days, estimated hours against weekly capacity. |
| `GET /api/v1/team/activity?limit=&cursor=&work_item_id=` | every member | The activity feed, newest first (people, ANUM and assistants). |
| `GET /api/v1/team/people` | every member | Active members with their team profile and whether they can be assigned. |
| `PUT /api/v1/team/people/{user_id}` | yourself; owners for anyone | Display name, email for notifications, department, job title, weekly capacity, IANA time zone. |

### Permissions

`work:read` is held by every role (viewers included); `work:manage` by members and owners; `work:admin` and `assistant:manage` by owners only (`anum_api/authorization.py`). On top of the permission, editing needs the caller to be the creator or the user assignee (or an owner), deleting needs the creator (or an owner). Every refusal says who can do it. Every change writes an append-only `audit_records` row, a `team_activity` row and a canonical event in the same transaction.

## Assigning in words

`POST /api/v1/team/commands/parse {text, timezone?}` shows what ANUM understood (person, work, due date, candidates) without creating anything; `POST /api/v1/team/commands {text, timezone?, assignee?, priority?}` creates it. The web Team page and the Flutter Ask ANUM screen do exactly that: check, show, then assign.

The parser (`team_commands.py`) is deterministic: the person, the work and the date come from the user's own sentence, never from model output or retrieved text (threat model G5).

The parser first trims a command to 2,000 characters and collapses whitespace to single spaces. Its patterns use one literal space as the separator, so no whitespace run overlaps a `.+` run and matching stays linear on hostile input. `tests/test_team_commands.py` checks this with 100 KB inputs.

- `ask|tell|remind|get|have <person> to <work> [by|before|on|due|until <when>]`
- `assign <work> to <person> [by <when>]`
- Arabic: `اطلب من|ذكّر|كلّف <first name> [أن] <work> [قبل|بحلول|يوم <when>]`
- `<when>`: `today`, `tonight`, `tomorrow`, a weekday (the nearest one, today included before 17:00; `next Thursday` is a week later), `in N days|weeks`, `end of week` (Thursday, the end of the Gulf work week), `next week` (Sunday), `2026-10-15`, `15/10` (day first), with an optional `at 5pm` / `at 17:30`. Without a time, 17:00 in the creator's time zone (their team profile, else the browser's, else UTC).

The person is matched against team profile names, user ids, enabled assistants and "ANUM": an exact name wins, then a first name or any word of a name, then a prefix. No match or several matches answer with the problem and the candidates; nothing is created until a person is picked.

**Through the governed runtime.** A task, voice command or automation whose prompt has that shape (`looks_like_assignment`) is planned as the `team.assign_task` tool with the user's sentence as its only argument. The tool is **medium risk** and needs the `owner` or `member` role: the workspace's [approval policy](approvals-and-risk.md) decides whether it waits for an approval, governance rules apply, and a department assistant's profile can narrow it further. For [autonomy](autonomy.md) it is category `tasks`, action `create`. The [ANUM Command](command.md) plan preview makes the same choice, so a previewed assignment step names `team.assign_task`. The tool parses, resolves in the caller's workspace, creates the item (source `command`, created by the caller, logged as "ANUM (for ...)") and notifies the assignee. If the person cannot be resolved the run fails with the reason and the candidates.

## Notifications

Assignments, re-assignments, comments, completion, "blocked", overdue reminders and ANUM follow-ups create an in-app notification for the people concerned (never the actor). Rows live in `user_notifications`: anyone in the workspace may insert one, but only the recipient (`anum.user_id`) can read, update or delete it.

| Route | What |
|---|---|
| `GET /api/v1/notifications?unread_only=&limit=&cursor=` | The caller's notifications, newest first, with the unread count. |
| `POST /api/v1/notifications/{id}/read`, `POST /api/v1/notifications/read-all` | Mark read. |

Delivery uses the existing channels and the recipient's [notification preferences](notifications.md):

- **In app:** the web bell (top bar) and Flutter Settings › Team work › Notifications. A `notification.created` event (payload: recipient, kind, work item id; no text) refreshes the recipient's client. With **Desktop notifications** on and browser permission granted, the web and desktop apps show an OS notification while the page is in the background.
- **Phone push:** the same event is a push kind (`team_work`) of the [push dispatcher](notifications.md#phone-push): fixed text ("Team work" / "Something in your team work needs you."), the work item id for the deep link, sent to the recipient while they are an active member with `work:read` and have phone push on. The row says `queued`, `not_requested` (push off for them) or `not_configured` (no FCM key on the server). The post-close trading report notice (`finance_daily_report`) uses the same row and event but pushes as its own kind, `finance_report` ([Notifications](notifications.md#phone-push)).
- **Email:** with the recipient's **Email** switch on and an email in their team profile, and SMTP configured (below), the email is sent after the request commits; failures are retried by the reminder sweep up to three attempts and the row records `pending`, `sent`, `failed` (with the reason) or `not_configured`. The message carries the notification's title and sentence only.

Why a separate SMTP sender rather than the [email inbox](email.md) module: the inbox connects mailboxes per workspace with each owner's OAuth grant and sends only through ToolPolicy and approvals as that mailbox. Team notifications are system mail to the workspace's own members, must work before (or without) any mailbox is connected, must not appear in a person's sent folder or need an approval per reminder, and must not borrow a member's private mailbox grant. The deployment-level SMTP sender below covers that; it never sends to anyone outside the workspace.

| Setting | Meaning |
|---|---|
| `ANUM_SMTP_HOST`, `ANUM_SMTP_PORT` (587) | SMTP server. Unset host: email is `not_configured`. |
| `ANUM_SMTP_SECURITY` | `starttls` (default; refuses to send if the server does not offer it), `tls` (implicit TLS, port 465), `none` (honoured only with `ANUM_ENVIRONMENT=local` or `test`). |
| `ANUM_SMTP_USERNAME`, `ANUM_SMTP_PASSWORD`, `ANUM_SMTP_FROM` | Credentials and sender; the password comes from the secret store. |
| `ANUM_SMTP_RETRY_BACKOFF_SECONDS` (1) | First wait before ANUM retries a system email that failed for a transient reason (unreachable server, 4xx); doubles each time, three attempts in all. |
| `ANUM_PUBLIC_APP_URL` | The web app's public address (for example `https://anum-app.example.com/`), used for the accept link in [invitation emails](identity.md#invitation-email). Empty: the link uses the inviting browser's address when it is in `ANUM_CORS_ORIGINS`, else the email carries the token to paste. The Oracle compose file sets it from `ANUM_WEB_HOST`. |

Owner setup with Gmail, Outlook or Zoho: create an app password (Google Account › Security › App passwords; Microsoft account › Security › App passwords, or an SMTP AUTH-enabled mailbox in Microsoft 365; Zoho Mail › Security › App passwords), then set `ANUM_SMTP_HOST` (`smtp.gmail.com`, `smtp.office365.com`, `smtp.zoho.com`), `ANUM_SMTP_USERNAME` (the mailbox), `ANUM_SMTP_PASSWORD` (the app password) and `ANUM_SMTP_FROM` in the deployment's secret store.

## Reminders

When `ANUM_AUTOMATION_SCHEDULER_ENABLED=true`, each API process runs a reminder sweep every `ANUM_TEAM_REMINDER_POLL_SECONDS` (60), at most `ANUM_TEAM_REMINDER_BATCH_SIZE` (200) items per pass:

- **Overdue:** an open item past its due date with no reminder yet notifies the assignee and the creator once ("Overdue: ..."), sets `reminder_sent_at` and logs "ANUM sent an overdue reminder". Changing the due date allows a new reminder.
- **ANUM follow-up:** at `follow_up_at` ANUM comments on the item ("Following up on ...: how is it going?"), notifies the assignee and creator, and clears the follow-up time.
- **Email retries** of notifications still `pending`.

Discovery follows the [maintenance role](multi-tenancy.md#maintenance-role) pattern: `anum_maintenance` reads only ids, statuses and timestamps of due work items and pending-email notifications; each item is then handled in its own transaction as the application role inside that tenant and workspace. A pass that is running when the process stops finishes its transactions first. Two replicas may both see a due item; the update is version-checked, so the second one changes nothing.

## Department assistants

An assistant is an agent profile for one department: name, department (`sales`, `support`, `operations`, `finance`, `tyre_engineering`, `custom`), instructions, allowed tools (names registered on this server; unknown names answer `422`), routing keywords (English or Arabic) and an autonomy level. Templates for the five departments come from `GET /api/v1/assistants/templates` (filtered to tools this server has).

| Route | Who |
|---|---|
| `GET /api/v1/assistants`, `GET /api/v1/assistants/{id}`, `GET /api/v1/assistants/templates` | every member |
| `POST`, `PUT /api/v1/assistants[/{id}]`, `DELETE /api/v1/assistants/{id}` | owners (`assistant:manage`); delete is refused while open work is assigned (turn it off instead) |
| `POST /api/v1/assistants/route {channel, sender, subject?, body, assistant_id?}` | members, owners |

### Autonomy

When work assigned to an assistant is handed off, its agent task carries `assistant_id` and the runtime narrows tool policy (`assistants.narrow_for_assistant`), on planning, on execution and after an approval:

| Autonomy | Effect |
|---|---|
| `observe` | Only low-risk tools; anything else is blocked. |
| `suggest` | Every allowed call except the draft-only `anum.respond` waits for a person's approval. |
| `act_low_risk` | Low-risk tools run; medium and high risk follow the workspace policy and approvals as for anyone. |

A tool outside the profile, a missing or turned-off assistant, are blocked with the reason. The profile never loosens anything: high-risk tools still need an approval, governance denials still apply.

### The Assistants page

The web page (`AssistantsView.tsx`, `assistantsPage.css`, helpers in `lib/team.ts`) uses the shared page layout: a head with New assistant and the **Overview** and **Work** tabs.

- **Overview**: four figures (switched on, in their queues, overdue, finished) from `GET /assistants`, `GET /work-items/workload` and one-row `GET /work-items` reads; the assistant directory as cards (department, autonomy, open, overdue and finished work) with search and a status filter; and the selected assistant's detail: Edit, Turn on or off, Delete, its purpose, allowed tools, what it never does on its own at its autonomy (`assistantGuards`: no sending, posting or calling outside ANUM without approval, no paying or committing, and no changes or only approved ones below `act_low_risk`), routing, its five most recent work items and the always-on safety controls. Routing an inbound message stays folded under the directory.
- **Work**: every work item assigned to an assistant (`GET /work-items?assignee_type=assistant&status=all&sort=updated`), filtered by assistant and status, paged, each opening in Team.
- **Create and edit** is a wizard: Start (department templates, new assistants only), Define, Configure (tools, keywords, channels), Safety and approval (autonomy with its effect, on or off) and Review and save. The API validates every field and the role.

Not built from the design mockups, because the API has no such data: per-assistant model, token budget, cost and usage charts, run traces and token breakdowns, versions and rollback, a test sandbox, approval rules by amount, data-access scopes and retention per assistant. The usage and cost mockup is not built at all; workspace model spending is in Budgets and per-agent caps in [Agent controls](agent-controls.md).

### Routing inbound messages

`POST /api/v1/assistants/route` turns a message (email, WhatsApp, marketplace, website, phone, other) into a work item (source `inbound`) for the named assistant or the enabled assistant whose keywords occur most often in the subject and body (whole words for Latin keywords, substrings for Arabic; ties alphabetical). With no match the item stays unassigned for a person to triage. The message is stored as the item's details: it is customer data, never instructions, and it cannot choose tools. The activity feed shows "Inbound email message routed to Sales assistant". Messages are routed by a person (web Assistants page, Flutter) or by any client of this route.

### Inbound routing

An assistant can also take new messages **on arrival** from the channels ticked under **Take new messages on arrival** (`inbound_channels`: `email`, `whatsapp`; empty by default, so nothing is routed until an owner opts in). On arrival:

- **WhatsApp:** after the webhook has stored a delivery, a background task hands each new inbound text (or media caption) with the contact's name and number to `assistants.auto_route_inbound`.
- **Email:** at the end of a mailbox sync, new messages are handed over with the subject and preview (the body stays at the provider). Only shared (`workspace`) mailboxes are used, never private ones; the first sync of a mailbox (history), messages more than an hour older than the previous sync, mail sent from the mailbox itself, drafts and bulk mail are skipped.

A message becomes a work item only when an enabled assistant that opted into the channel has a matching routing keyword (the best match wins, as above). Messages that match nothing stay in the inbox and create nothing. While an automatically created item for the same conversation (WhatsApp conversation or email thread, `source_detail.external_ref`) is still open for that assistant, further messages in it create nothing. Items carry `source_detail.automatic: true` and are created by ANUM in the workspace's RLS context, one transaction per message; the activity feed shows them like hand-routed ones and the assignee notifications follow [Notifications](#notifications).

Routing is best effort and never blocks ingestion: any failure is logged (error class and channel only, never message content) and the message stays in the inbox. Clients that send `PUT /api/v1/assistants/{id}` without `inbound_channels` keep the stored value.

## Activity feed

`team_activity` is append-only (select and insert policies only). It records who did what in words a person reads: people, ANUM ("ANUM sent an overdue reminder", "ANUM followed up") and assistants ("Sales assistant started on ..."). The audit log (`audit_records`) keeps the compliance record of the same changes.

## Events

`work_item.created`, `work_item.updated`, `work_item.assigned`, `work_item.completed`, `work_item.deleted`, `work_item.commented`, `work_item.reminded`, `notification.created`, `assistant.changed`, `assistant.routed` ([Events](events.md)). Payloads carry ids, statuses, priorities and assignee ids; never titles, details, comments or message text.

## Storage and isolation

Migration `0039_team_work` adds `team_member_profiles`, `work_items`, `work_item_comments`, `work_item_checklist`, `work_item_attachments`, `team_activity`, `user_notifications` and `assistant_profiles`, all with forced RLS on `anum.tenant_id` and `anum.workspace_id` (notifications also on `anum.user_id` for everything but insert), and the nullable `tasks.assistant_id`. Expand only. With `ANUM_REPOSITORY_BACKEND=memory` the same store runs in process.

## Clients

- **Web** (`#team`): the Team page below ([Team page](#team-page)). Its work tabs: My work (the Ask ANUM box, then search, status, sort, paging), **Team dashboard** (the workload table above the board; picking a name filters the board's four columns, each paged), Activity. The detail panel edits status, priority, assignee, due and follow-up, checklist, comments and attachments (upload or pick from Files), hands work to ANUM or an assistant, and deletes. `#assistants` is one table of assistants with each one's queue and overdue work (`GET /work-items/workload`) and finished work (`GET /work-items?assignee_type=assistant&assignee_id=…&status=done`, its `total`), a band adding them up (a count that failed says Unavailable), **New assistant** (a dialog: pick a department template or start blank), edit in a dialog, turn on or off, delete with a confirmation, and **Route an inbound message** folded away until opened. Tests: `e2e/assistants.spec.ts`. Team and Assistants sit in the sidebar's main group and Organisations under Admin for platform operators (`src/lib/shell.ts`); the top bar's notifications menu lists the person's team notifications (mark all read, load more, desktop alerts) and counts the unread ones. Every state is handled: loading, empty with a next action, errors with a retry, the API's 403 in place, version conflicts reloaded. English and Arabic (`src/lib/messages/team.*.ts`). Tests: `test/team.test.ts`, `e2e/team.spec.ts`, axe in `e2e/accessibility.spec.ts`.
- **Flutter** (menu drawer › Team work, also Settings › Team work): My tasks, Team work, a work item (status, checklist, comments), Ask ANUM, Notifications, Workload, Assistants (list, route a message) and the platform console. English and Arabic (`team_strings.dart`). Tests: `test/team_test.dart` (contract, states, conflict, layouts from 320 to 1440 wide at 200% text).

## Team page

The web Team page (`#team`, `apps/web/src/TeamView.tsx` with `TeamPeople.tsx`, helpers in `src/lib/teamPage.ts`, strings in `src/lib/messages/teamPage.*.ts`) follows the owner's mockups 32, 38, 39, 46 and 47 on the shared page shell: a head with **Invite member** (owners) and **New work item**, tabs, and a KPI row (members and how many can be given work, departments and people without one, pending invitations for owners from `GET /workspace-members/summary`, open and overdue work from `GET /work-items/workload`; a figure whose request failed says Unavailable).

- **Directory**: everyone from `GET /team/people` with role, department, job title, time zone, open work and status (can be assigned, read only, no profile yet); search (name, email, job title), department (or none) and role filters. Picking a person opens a panel with their contact, work numbers, what their role may change, **Edit** (their team profile, in a dialog), call buttons and **Manage access** (Members).
- **Org chart**: the AI team org chart ([AI team org chart](org-chart.md)): the owner, ANUM, departments with their agents, assistants and people. Profiles open on the Agents page.
- **Departments**: people grouped by the department on their profile, with headcount and the open, overdue and due-this-week work assigned to them; a department opens its people.
- **Roles and access**: what owners, members and viewers may do in each module, from `GET /api/v1/workspaces/current/roles` (`authorization.ROLE_PERMISSIONS`, the same in every workspace; any active member may read it, `403` otherwise). A role has **Full** access to a module when it holds every permission the owner has there, **Edit** for some of them, **Read** for read only, **None** otherwise. Owners change roles in Members.
- **Invitations**: the workspace invitations (`GET /workspace-invitations`) grouped pending, accepted and revoked or expired, with **Invite member** (the Members invite form, then the one-time token), **Resend** and **Revoke** (with a confirmation) for owners; others are told only owners see and send invitations.
- **Activity**: the team activity feed, and for owners and organisation administrators the workspace audit log ([Governance](governance-and-scale.md)); others are told who sees it.
- Then the work tabs: **My work**, **Team dashboard** and **Calls**.

Tests: `apps/web/test/teamPage.test.ts`, `apps/web/e2e/team-page.spec.ts`, `apps/web/e2e/team.spec.ts`; API `services/api/tests/test_audit_log.py::test_roles_list_what_each_role_may_do`.

Not built from the mockups: custom roles and per-person permission changes (ANUM has three fixed roles), access reviews, MFA and session policy cards, last active and location per member, department budgets, goals and open roles, creating or archiving departments (departments are the fixed list on profiles), suspend access and reset sessions from the panel, a draggable org chart, and per-event risk levels and hashes in the activity stream.

## Not done yet

- Choosing the auto-route channels on Flutter (the web does it; Flutter shows assistants read-only).
- Auto-routing from marketplace, website and phone channels (they post to `/api/v1/assistants/route`).
- Recurring work items and dependencies between items.
- Assistant creation and editing on Flutter (read-only there; the web does it).
- Drag and drop on the web board (status changes through the detail panel's select, which is keyboard accessible).
- Phone push and in-process email are best effort per process (a restart drops a queued push; pending emails are retried by the sweep).
