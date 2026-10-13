# Calendar and next actions

The workspace calendar backs the ANUM 360 "Calendar & next actions" and "Meeting scheduled" screens (`docs/design/anum360.md`). It holds the workspace's events with their attendees and suggests next steps from the workspace's own state. The code is `services/api/anum_api/calendar_api.py`, `services/api/anum_api/db/calendar_repository.py`, `apps/web/src/CalendarView.tsx` and `apps/mobile/lib/features/calendar/`.

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

## Storage

Migration `0036_reports_calendar` adds `calendar_events` (attendees as a JSONB list) under forced RLS on `tenant_id` and `workspace_id`, with checks on kind, status and `ends_at > starts_at`. No other role is granted anything. With `ANUM_REPOSITORY_BACKEND=memory` events are lost on restart.

## Web page

`#calendar` (sidebar, main group, after Calls & Meetings; `apps/web/src/CalendarView.tsx`, helpers in `src/lib/calendar.ts` and `calendarApi.ts`, strings in `src/lib/messages/calendar.*.ts`):

- One header row with a status chip for the meeting tools connected in Calls & Meetings (Zoom, Teams; from `GET /meetings/setup`) and **New event** for members and owners (`can_write`). A line under it says events live in ANUM and are not synced with Google or Microsoft.
- A band: events today, events in the shown week or day (and how many were cancelled), replies still missing for events to come, and next steps. A figure whose request failed says Unavailable.
- A split view: the agenda (week from Monday, or one day; previous, Today, next; search over title, place, notes and people; Show cancelled) beside the selected event: when (in the browser's time zone) and how long, type, place, Join (https link) and Call (phone), notes, people with their replies, and for its creator or an owner Edit (dialog), Cancel event or Restore, Delete (with a confirmation) and a reply picker per person. Cancelling an event turns on Show cancelled, so the event stays selected with Restore and Delete. Others see a line saying who can change it. Below 1024px one pane shows at a time.
- **Next steps** tab: the `GET /calendar/next-steps` suggestions. A task suggestion starts the task (`POST /tasks` and run) and opens it in Tasks; email and approvals open their pages.
- States: loading, an empty week or day (with New event, or who can add events), no search match, a load error with Retry, 500 events in a range (says to use the day view), and the API's 403 explained.

Tests: `apps/web/test/pages2.test.ts` and `apps/web/e2e/calendar.spec.ts` (stateful mock in `e2e/support/calendarApi.ts`).

## Flutter app

Routes `/calendar` and `/calendar/events/<id>`. The calendar screen has a week day strip (previous and next week, Today), the selected day's timeline with Join and Call, Add event (members and owners), and the suggested next steps; on wide screens the timeline and the suggestions sit side by side. The event screen shows when and where, Join or Call, the attendees with their reply pills (the organiser taps one to record a reply), Edit, Cancel event, Delete and View in Calendar.

## Not done yet

- ANUM does not send invitations: an emailed invite is an outward action that goes through the email tools and their approval (docs/email.md). The screen says so under the attendees.
- No sync with Google or Microsoft calendars yet; events live in ANUM only (the web and app pages say so).
- No month view and no drag to reschedule on the web; the week and day agendas page by week or day.
- No recurring events.
