# Calendar and next actions

The workspace calendar backs the ANUM 360 "Calendar & next actions" and "Meeting scheduled" screens (`docs/design/anum360.md`). It holds the workspace's events with their attendees and suggests next steps from the workspace's own state. The code is `services/api/anum_api/calendar_api.py`, `services/api/anum_api/db/calendar_repository.py`, `apps/web/src/CalendarView.tsx` and `apps/mobile/lib/features/calendar/`. Sync with Google Calendar and Microsoft 365 is `calendar_sync.py`, `calendar_providers.py`, `calendar_sync_api.py` and `db/calendar_sync_repository.py` ([Sync](#sync)).

This module is calendar events only. Meeting transcripts and summaries are a separate meetings module.

## Events

An event has a `title`, `kind` (`meeting`, `call`, `review`, `reminder`, `focus`), `status` (`scheduled`, `cancelled`), `starts_at` and `ends_at` (stored in UTC; a time without an offset is taken as UTC; at most 14 days long and ending after it starts), optional `description`, `location`, `conference_url` (https only; the app's Join opens it) and `phone` (the app's Call dials it), and up to 100 `attendees`. An attendee has a `name`, optional `email` (validated, unique within the event) and `role`, and the reply the organiser recorded: `invited`, `accepted`, `tentative` or `declined`.

## API

| Route | Who | Does |
|---|---|---|
| `GET /api/v1/calendar/events?start=&end=&include_cancelled=&limit=` | `calendar:read` (every role) | Events overlapping the range, earliest first. Default: today (UTC) and the next 7 days; at most 92 days; `truncated` says when more exist; `can_write` tells the app whether to offer Add |
| `POST /api/v1/calendar/events` | `calendar:write` (members, owners) | Adds an event |
| `GET /api/v1/calendar/events/{id}` | `calendar:read` | One event and `can_manage` |
| `PATCH /api/v1/calendar/events/{id}` | the creator (with `calendar:write`) or an owner (`calendar:manage`) | Changes fields, cancels (`status: cancelled`) or replaces the attendee list (existing attendee ids are kept) |
| `PUT /api/v1/calendar/events/{id}/attendees/{attendee_id}/response` | the creator or an owner | Records an attendee's reply |
| `DELETE /api/v1/calendar/events/{id}` | the creator or an owner | Deletes it |
| `GET /api/v1/calendar/next-steps` | `calendar:read` | Suggestions, no model call: prepare for the next meetings in 24 hours, follow up with attendees who have not replied, send a summary of a meeting that ended in the last 24 hours (these three start a task, so they need `task:create`), reply to urgent unread email (with `email:read`) and review pending approvals (with `approval:read`) |

A task suggestion carries the task's `title` and `prompt`; the app creates it with `POST /api/v1/tasks` and opens it, so it runs and asks for approval like any other task. Event notes go into the prompt as data, not instructions.

Changes are audited (`calendar.event.created`, `calendar.event.updated`, `calendar.attendee.response`, `calendar.event.deleted`).

An event also carries its sync link (`connection_id`, `external_id`, `synced_at`, `sync_state`) and `invitations_pending`; a create takes `sync` (default `true`; `false` keeps the event in ANUM only). See [Sync](#sync).

## Storage

Migration `0045_reports_calendar` adds `calendar_events` (attendees as a JSONB list) under forced RLS on `tenant_id` and `workspace_id`, with checks on kind, status and `ends_at > starts_at`. No other role is granted anything. With `ANUM_REPOSITORY_BACKEND=memory` events are lost on restart.

## Sync

An owner connects the primary calendar of a Google (Calendar API v3) or Microsoft 365 / Outlook.com (Microsoft Graph) account to the workspace. Events then flow both ways. Everything a provider returns is untrusted data and goes through the same validation as an event typed by a person.

### Setup

Each provider needs an OAuth client the owner registers; nothing is required and a provider without one is shown as "not set up" with the names of the missing settings. Settings are read from the environment (`.env.example` files hold placeholders only; secrets go in the secret store, never in git).

| Setting | Meaning |
|---|---|
| `ANUM_CALENDAR_GOOGLE_CLIENT_ID`, `ANUM_CALENDAR_GOOGLE_CLIENT_SECRET` | Google OAuth client. Empty: the inbox's `ANUM_EMAIL_GOOGLE_*` client is used, so one Google app can serve both (add the scopes `openid`, `email` and `https://www.googleapis.com/auth/calendar.events`) |
| `ANUM_CALENDAR_MICROSOFT_CLIENT_ID`, `ANUM_CALENDAR_MICROSOFT_CLIENT_SECRET`, `ANUM_CALENDAR_MICROSOFT_TENANT` | Microsoft (Entra ID) client and tenant (`common` by default; the inbox's values when empty). Delegated permissions `offline_access`, `User.Read`, `Calendars.ReadWrite` |
| `ANUM_CALENDAR_PUBLIC_API_URL`, `ANUM_CALENDAR_WEB_URL` | Public URLs; empty uses `ANUM_EMAIL_PUBLIC_API_URL` and `ANUM_EMAIL_WEB_URL` |
| `ANUM_CALENDAR_SYNC_INTERVAL_SECONDS` | Background sync interval (default 300; `0` turns it off; PostgreSQL only) |
| `ANUM_CALENDAR_DAYS_BACK`, `ANUM_CALENDAR_DAYS_AHEAD` | The window that syncs (30 days back, 365 ahead) |

The redirect URI to register with the provider is `<public API URL>/api/v1/calendar/sync/oauth/callback` (the Sync tab shows it). On Kubernetes the client ids go in `config` and the secrets only in `secrets.app` (the chart refuses `ANUM_CALENDAR_*_CLIENT_SECRET` in config); on the Oracle VM they are in `infra/oracle/.env`.

### Connecting

`POST /calendar/sync/oauth/{google|microsoft}/start` (owners, `calendar:manage`) returns the consent URL with PKCE and an encrypted `state`. The provider sends the browser to the unauthenticated `GET /calendar/sync/oauth/callback`, which only stores the returned code (encrypted) on the pending request and redirects to `/?calendar_connect=<id>#calendar`. The web page then calls `POST /calendar/sync/oauth/complete` as the signed-in owner who started it: the code is exchanged once (the request is consumed first), the account is read (`email`), the access and refresh tokens are stored encrypted with `ANUM_SECRETS_KEY` and a first sync runs. A provider that returns no refresh token is refused with the steps to fix it. Connecting the same account again reconnects it.

| Route | Who | Does |
|---|---|---|
| `GET /api/v1/calendar/sync` | `calendar:read` | Providers (configured, missing settings, scopes), the redirect URI, `can_manage` and the connections with their health, `fix` text and last summary |
| `POST /api/v1/calendar/sync/connections/{id}/sync` | owners | One sync pass now |
| `DELETE /api/v1/calendar/sync/connections/{id}` | owners | Revokes ANUM's grant at the provider (where it has an endpoint), deletes the tokens and keeps the events in ANUM as ANUM-only events. Nothing is deleted at the provider |
| `POST /api/v1/calendar/events/{id}/invitations` | the event's creator or an owner | Asks for the event's invitations ([Invitations](#invitations)) |

Audited: `calendar.connect.started`, `calendar.connect.denied`, `calendar.connect.failed`, `calendar.connection.created`, `calendar.connection.reconnected`, `calendar.sync.run`, `calendar.connection.deleted`, `calendar.invitations.requested`.

### How events sync

* **Incremental.** Google uses `events.list` with the stored `nextSyncToken`; Graph uses `calendarView/delta` and the stored `@odata.deltaLink` (only its token is kept; requests always go to the fixed Graph path, and a link to another host is refused). A sync token or delta link only reports changes inside the window it started with (Google gets `timeMax`, Graph an end date), so the window is renewed with a full sync once it ends more than a margin (30 days) behind the moving horizon: an event that later comes into range is picked up. An expired token (HTTP 410) starts a full sync again. Recurring events arrive as their single occurrences. All-day events are imported as 24 hours from midnight UTC. Events longer than 14 days or without a valid end are skipped and counted.
* **External ids.** Each linked event stores the provider's event id (`external_id`), its ETag or change key, when ANUM and the provider last agreed (`synced_at`) and `sync_state`: `synced`, `pending_push` (waiting to be sent) or `local`. One provider event maps to one ANUM event per connection (a unique index).
* **ANUM to the provider.** A new event goes to the oldest working connection unless the person turned "Also add to ..." off (`sync: false`). The change is saved first, then sent at once; if the provider cannot be reached it stays `pending_push` and the next pass sends it (the Sync tab shows the error and what to do). Cancelling a synced event removes it at the provider and unlinks it; deleting one removes it at the provider first and is refused (with the reason) when the provider cannot, so nothing is lost silently.
* **Background job.** With PostgreSQL a scheduler syncs every connection each `ANUM_CALENDAR_SYNC_INTERVAL_SECONDS`. Discovery runs as the narrow `anum_maintenance` role (migration `0092_calendar_sync`: ids, scope and status of connections that do not need reconnecting; never a token or an address); each sync then runs as the application role inside that connection's tenant and workspace RLS context. A lease keeps two passes from running for one connection. A connection that needs reconnecting is skipped until an owner reconnects it.
* **Microsoft specifics.** Graph tells attendees about every change the organiser makes, so a change to an event that has people is held (`sync_state` stays `pending_push`, counted as `held` in the sync summary) until an owner approves the event as it now is under [Invitations](#invitations); Google changes go out silently because ANUM sends `sendUpdates=none`. Updating an event that carries a Teams meeting never replaces its body, so the join details survive (a description edit on such an event stays in ANUM).
* **Events organised by someone else** follow the provider: a local edit stays in ANUM until the provider changes the event again.

### Conflicts

When an event changed in both places since the last sync, the later change wins (ANUM's `updated_at` against the provider's update time) and the provider wins a tie. The losing side is overwritten. A provider deletion removes the ANUM event unless it has unsent edits, which win and are sent as a new event. Counts of conflicts won by each side are in the connection's last summary (Sync tab and the `calendar.sync.run` audit entry of a manual sync). Echoes of ANUM's own writes are recognised by the ETag and do not count as changes.

### Invitations

ANUM never gives a provider an attendee list on its own: a provider sends invitation emails to attendees, which is an outward action. An event is created and updated at the provider without attendees (and without notifying anyone). To invite people, a member or the creator presses **Ask to send invitations** (`POST /calendar/events/{id}/invitations`), which creates a task paused for approval on the high-risk tool `calendar.send_invitations` (always asks, whatever the autonomy level). The approval shows the calendar, title, times, place and the exact addresses, bound to a payload hash. Only an owner can approve, and only with the hash that was displayed (a wrong hash is refused with 409). On approval the tool re-checks that the event and attendees still hash to the approved content (any edit after the request makes it fail and send nothing), then sends the attendees with notification on. After that, edits keep sending to the approved invitees; adding or changing attendees, the time, title or place makes `invitations_pending` true again and needs a new approval. Cancelling or deleting an event whose invitations were approved cancels it at the provider with notification. Attendees that came from the provider already hold their invitation there, so an imported event is not "pending". Attendees stay in ANUM, with the replies the organiser records, and provider replies update them on the next sync.

### States and fixes

| State | What the person sees |
|---|---|
| Provider not set up | "Not set up" with the missing setting names; no Connect button |
| Nothing connected | "No calendar connected yet" and the Connect buttons (owners) |
| Connecting | The page finishes the connection after the redirect, with a status line, then lists the account; a denied or expired request says to press Connect again |
| Sync error | The provider's short error, "Press Sync now ..." and the Sync now button; events stay readable and edits stay queued |
| Reconnect needed | The grant was revoked or expired: the provider's text and "Connect ... again"; Sync now is disabled until then |
| Viewer or member | The status and each event's sync line, without Connect, Sync now or Disconnect |

Tokens never appear in responses, logs or audit records. `ANUM_SECRETS_KEY` rotation covers them (`python -m anum_api.rotate_secrets`, report key `calendar_connections`).

## Web page

`#calendar` (sidebar, main group, after Calls & Meetings; `apps/web/src/CalendarView.tsx`, helpers in `src/lib/calendar.ts` and `calendarApi.ts`, strings in `src/lib/messages/calendar.*.ts`):

- One header row with a status chip for the meeting tools connected in Calls & Meetings (Zoom, Teams; from `GET /meetings/setup`) and **New event** for members and owners (`can_write`). A line under it says which calendar the events are synced with, that sync needs attention (with a link to the Sync tab), or that events live in ANUM until one is connected.
- A band: events today, events in the shown week or day (and how many were cancelled), replies still missing for events to come, and next steps. A figure whose request failed says Unavailable.
- A split view: the agenda (week from Monday, or one day; previous, Today, next; search over title, place, notes and people; Show cancelled) beside the selected event: when (in the browser's time zone) and how long, type, place, Join (https link) and Call (phone), notes, people with their replies, and for its creator or an owner Edit (dialog), Cancel event or Restore, Delete (with a confirmation) and a reply picker per person. Cancelling an event turns on Show cancelled, so the event stays selected with Restore and Delete. Others see a line saying who can change it. Below 1024px one pane shows at a time.
- **Sync** tab ([Sync](#sync)): the providers (or what is missing), the connected calendars with last sync, counts, conflicts settled, the error and fix, **Connect**, **Sync now** and **Disconnect** (with a confirmation) for owners, and the conflict rule. An event's detail shows where it lives (synced, waiting to be sent, only in ANUM) and **Ask to send invitations**; **New event** has "Also add to ..." when a calendar is connected.
- **Next steps** tab: the `GET /calendar/next-steps` suggestions. A task suggestion starts the task (`POST /tasks` and run) and opens it in Tasks; email and approvals open their pages.
- States: loading, an empty week or day (with New event, or who can add events), no search match, a load error with Retry, 500 events in a range (says to use the day view), and the API's 403 explained.

Tests: `apps/web/test/pages2.test.ts`, `apps/web/test/calendarSync.test.ts` and `apps/web/e2e/calendar.spec.ts` (stateful mock in `e2e/support/calendarApi.ts`). API: `tests/test_calendar.py`, `tests/test_calendar_sync.py` (provider HTTP mocked with `httpx.MockTransport`) and the PostgreSQL tests `tests/test_postgres_report_sections_calendar.py` and `tests/test_postgres_calendar_sync.py`.

## Flutter app

Routes `/calendar` and `/calendar/events/<id>`. The calendar screen has a week day strip (previous and next week, Today), the selected day's timeline with Join and Call, Add event (members and owners), and the suggested next steps; on wide screens the timeline and the suggestions sit side by side. The event screen shows when and where, Join or Call, where the event lives (synced, waiting, only in ANUM) with **Ask to send invitations** for its creator or an owner, the attendees with their reply pills (the organiser taps one to record a reply), Edit, Cancel event, Delete and View in Calendar. A card above the timeline shows the sync status (which calendar and how long ago, what to do when it needs attention) with **Sync now** for owners. Connecting, reconnecting and disconnecting happen on the web (the Sync tab), because the provider's consent returns to the web app; events added in the app are sent to the connected calendar like any other (keep one in ANUM only from the web form).

## Not done yet

- Only each account's primary calendar syncs (no choice of calendars, no shared or room calendars); attendee free/busy and meeting-room booking are not read.
- Google conference links are read, but ANUM does not create Meet or Teams meetings for new events.
- Connecting from the Flutter app (the consent returns to the web app); the app shows status and syncs now.
- New events go to the oldest working connection of the workspace (no per-event choice of which calendar).
- No month view and no drag to reschedule on the web; the week and day agendas page by week or day.
- No recurring events of ANUM's own; recurring events from a provider arrive as single occurrences.
