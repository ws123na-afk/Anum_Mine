# Work attendance and daily reports (phase A)

A person's own work log for office and field work: work profile, workplace and sites, schedule, arrivals and
departures, breaks, site visits, remote work, corrections with a reason, daily reports, hours by day, week and month,
site history, an overtime estimate, missed check alerts and CSV and PDF exports. iPhone Shortcuts can send arrivals
and departures. It is a **personal log kept by the person, never an employer-certified timesheet**, and every export
says so.

Code: `services/api/anum_api/attendance/` (engine, store, service, API, device seam, AI drafts, daily job),
`services/api/anum_api/db/attendance_repository.py`, migration `0132_work_attendance`, contracts
`packages/contracts/src/attendance.ts`, web `apps/web/src/AttendanceView.tsx`, `AttendanceParts.tsx`,
`lib/attendance*.ts`, `lib/messages/attendance.{en,ar}.ts`, `attendance.css`.

This is not HQ presence: `office_presence` is agent presence on the campus, `office_desks` work hours are seat
hours and `office_visitors` is reception. None of them is reused as people's attendance; Attendance reuses their
patterns (events replayed into state, the daily retention job, the maintenance discovery role).

## Owner defaults (decided for the owner, changeable)

| Decision | Default | How to change it |
|---|---|---|
| Who sees a record | The person only. In any workspace (personal or business) a manager or colleague sees someone's attendance only while that person shares it with them, read only. | Sharing is per person; a workspace-wide manager view would need a new permission and an owner decision. |
| Raw location events | Kept 90 days, then reduced to daily totals and deleted. | `RAW_RETENTION_DAYS` in `attendance/service.py` and the matching interval in the migration's maintenance policy. |
| Coordinates | Never stored. The phone's own geofence decides; the server learns only which named site matched and, optionally, a quality flag from the reported accuracy. | Not configurable. |
| Pause | Location check in can be paused, resumed or turned off at any time; while paused or off, phone events are refused and nothing is stored. | Settings tab. |
| Certification | Never labelled employer-certified. Exports carry "Personal work log kept by the person. Not certified by an employer." | Not configurable. |
| Roles | Owners and members use Attendance (`attendance:use`); viewers do not. | `authorization.py` role map. |
| AI drafts | Off. Each person turns them on for themselves. | Settings tab. |
| Missed check alerts | On, in app only (no email or push). | Settings tab. |

## Permissions

`attendance:use` (owners and members). Every route checks it on the server. A person reads and writes only their own
records. With `?person=<user id>` a caller reads a colleague's records only while that colleague's active share names
them; daily reports only when the share includes them. A share never allows a write. A person with no share answers
404, the same as someone with no records.

## Records

- **Work profile**: organisation, department, role, main workplace, time zone (IANA, default `Asia/Dubai`), week
  start, schedule (weekday, start, end; an end at or before the start ends the next day), daily and weekly overtime
  thresholds (empty means no overtime estimate), location consent and pause, AI drafts switch, missed check alerts.
  Saved with a version: a stale save answers 409.
- **Sites**: name (unique per person, case-insensitive), kind (workplace, site, client, home), an optional address note
  for the person, a radius hint for the Shortcut (50 to 2000 m). Deleting archives: past records keep the name, new
  events cannot use it.
- **Events**: `arrive`, `leave` (with a site: site visits and multiple sites a day), `break_start`, `break_end`,
  `remote_start`, `remote_end`. Each has the time it happened, the server's receive time, the time zone in effect,
  its source (`web`, `device`, `manual`), quality (`ok`, `poor`), an idempotency key and a status (`active`,
  `voided`, `superseded`).
- **Corrections**: add, change or remove an event, each with a required reason (3 to 500 characters). The old event is
  kept as `superseded` or `voided`, the replacement is a `manual` event, and the correction row keeps before and after.
  Days older than 90 days are totals only and cannot be corrected.
- **Daily reports**: one per person and day: project, tasks done and pending, site visits, issues, time spent, priority,
  follow-up date, notes, attachments (ids of files in Files, uploaded with Files' own checks, limits and malware scan),
  and whether an AI draft was used. Saved with a version.

## Time engine

`attendance/engine.py` is pure (no storage, no clock) and replays events in time order on every read, so late events,
duplicates and corrections need no repair.

- Durations are measured on UTC instants: a 22:00 to 06:00 shift lasts 7 hours on the spring-forward night and 9 on
  the fall-back night (tested in Europe/London and America/New_York; Asia/Riyadh and Asia/Dubai have no DST).
- A segment belongs to the **shift day**: the local date of its start in the zone it was recorded in. Overnight shifts
  count on the day they began; after a time zone change each day keeps its own zone.
- **Presence** is time on a site, breaks included. **Worked** is site and remote time minus breaks. **Verified** is the
  part of worked time whose start and end were both recorded with good quality: never an estimate, never an open
  interval, never a weak location.
- Missing checkout: an open interval younger than 16 hours is ongoing (counted to now, not verified). Older, it ends at
  the scheduled end when there is one (estimated, never verified), else at the arrival, and is flagged.
- Duplicates (an arrival where the person already is, a second break start) are ignored and flagged; arriving at
  another site ends the current visit at that moment; a departure with no arrival is ignored and flagged.
- Late events (received more than 15 minutes after they happened) count at their own time and are flagged.
- A scheduled day with no arrival is flagged once past. The overtime estimate is the larger of the daily overtime sum
  and, per week, worked time above the weekly threshold; it is shown only when configured and labelled an estimate.

Nothing infers work from location: segments are only what the events say.

## API

All under `/api/v1/attendance`, signed in, `attendance:use`:

| Route | What |
|---|---|
| `GET/PUT /profile`, `POST /tracking` (`consent`, `withdraw`, `pause`, `resume`) | Profile and location check in |
| `GET/POST /sites`, `PATCH /sites/{id}`, `DELETE /sites/{id}` (archives) | Sites |
| `POST /check` | Web buttons: check in or out, break, remote, now; idempotent |
| `POST /device/events` | iPhone Shortcut arrive or leave (below) |
| `GET /today`, `/events`, `/days`, `/summary?period=day|week|month`, `/site-history` | Reads; `?person=` for a share |
| `GET /export.csv`, `/export.pdf` | Reports (CSV is formula-safe; PDF is bilingual and says it is not certified) |
| `POST/GET /corrections` | Corrections with reason, newest first, paged |
| `GET /daily-reports` (search, paging), `GET/PUT/DELETE /daily-reports/{day}`, `POST /daily-reports/draft` | Daily reports and AI drafts |
| `GET/POST /shares`, `DELETE /shares/{id}` | Sharing |
| `GET /limits` | The fixed rules the screens explain |

Idempotency: every write that a client may retry carries an idempotency key (8 to 128 characters). The same key
records the event once and answers 200 with `duplicate: true`; the same key for a different event is 409. The
database enforces it with a unique key per person.

## iPhone Shortcuts

`POST /api/v1/attendance/device/events` with `Authorization: Bearer <token>` and JSON
`{"event": "arrive" | "leave", "site": "<site name>" | "site_id": "...", "idempotency_key": "...", "occurred_at": "<ISO 8601 with offset, optional>", "accuracy_m": <optional>}`.
Unknown fields (for example latitude) are refused with 422. The server stamps the receive time; `occurred_at` keeps the
phone's time for a late delivery (at most 7 days back, at most 5 minutes ahead). An `accuracy_m` above 200 m marks
the event `poor` (counted, not verified). Consent must be on and tracking not paused, or the answer is 409 and nothing
is stored.

**Token.** One device enrollment for every iPhone integration: make a token with the **Attendance** scope on
Settings, Integrations, iPhone and turn the "Attendance arrive and leave" integration on there
([iPhone integrations](iphone-integrations.md)). The API registers its scoped `DeviceAuthority` here when it starts. `attendance/devices.py` is the seam: `register_device_authority()` takes a `DeviceAuthority` that resolves a scoped
device token (scope `attendance`) to its owner. Any other bearer token falls back to the ordinary ANUM sign-in token, which expires. **Real-device testing is
still to do.** `X-Anum-Device` may carry a short device label; it never grants access.

Steps on the iPhone (one automation per site for arriving and one for leaving):

1. In ANUM, Attendance, Settings: add the site and turn on location check in.
2. Shortcuts app, Automation, New Automation, **Arrive**. Choose the place, set the radius to the site's radius,
   choose **Run Immediately** (turn off Notify When Run if you prefer).
3. Add **Get Contents of URL**: URL `https://api.anum360os.com/api/v1/attendance/device/events`, Method POST,
   Headers `Authorization: Bearer <token>` and `Content-Type: application/json`, Request Body JSON with
   `event` = `arrive`, `site` = the site's name, `idempotency_key` = **Current Date** formatted ISO 8601 followed by
   `-arrive`, and `occurred_at` = **Current Date** ISO 8601.
4. Repeat with **Leave** and `event` = `leave`.
5. Run the shortcut once by hand and check the Today tab. If the phone was offline, Shortcuts may retry: the same
   key is stored once.

Real-device behaviour (locked phone, Low Power Mode, iOS delaying automations, geofence accuracy) needs testing on a
phone.

## AI drafts

Off by default. When a person turns them on, `POST /daily-reports/draft` (`day` or `week`) writes a short summary from
**only that person's own report entries** (tasks, issues, notes, project, time noted). Arrival and departure times,
sites and locations are never sent, so a draft cannot invent work from where someone was. With no entries the model is
not called. The call goes through the workspace's budgeted model gateway (budget checked before, metered after); the
entries are a labelled untrusted block; long dashes are removed. The draft is returned for the person to edit and is
saved only if they add it to their notes. Database work runs in its own short unit in a worker thread; no transaction
is open during the model call.

## Storage, isolation and retention

Migration `0132_work_attendance` (expand only; chained after `0131_finance_sms_ledger`):
`attendance_profiles`, `attendance_sites`, `attendance_events`, `attendance_corrections`, `attendance_day_totals`,
`attendance_daily_reports`, `attendance_shares`. Every table has forced RLS: the tenant and workspace policy, and
restrictive per-command owner policies. SELECT admits the owner, a viewer named in the owner's active share (reports
only when included), the tenant purger and the maintenance role; INSERT and UPDATE only the owner; DELETE the owner and
the purger. Every table has the tenant purge policy and is listed in `tenant_lifecycle_tables.py` (export and purge
with the organisation or workspace).

**Daily job** (`python -m anum_api.attendance.jobs`, also run by the daily retention job and reported under
`attendance`): discovers people as `anum_maintenance` (scope columns of events older than 90 days and of profiles with
alerts due, nothing else, no write), then in each person's own context reduces old days to `attendance_day_totals`,
deletes the raw events and corrections, and sends at most one in-app alert a day for the previous local day (missing
checkout, no arrival on a scheduled day). The alert names the day, never a place.

## Privacy

The privacy export has an `attendance` section (profile, sites, events, corrections, day totals, daily reports, shares
given) and the summary counts `attendance_records`; deleting one's data deletes all of them. Audit records hold ids and
codes only (kind, source, quality, counts), never times, site names or report text.

## Web

Attendance in the sidebar (`#attendance`, tabs `#attendance-reports`, `-daily`, `-corrections`, `-settings`): Today
(status, check in and out, breaks, remote, attention flags, timeline), Reports (week and month, previous and next,
figures, day table, site visits, CSV and PDF), Daily report (editor with attachments and AI drafts, search and paging of
earlier reports), Corrections (add, change, remove with reason; history), Settings (profile and schedule, location
check in, sites, sharing, Shortcut steps). A colleague's shared record opens read only from the header picker. English
and Arabic (RTL), every state (loading, empty with a next action, error with retry, denied with who can help).

## Tests

- `tests/test_attendance_engine.py`: overnight, DST both ways, Riyadh and Dubai, zone change mid-week, missing checkout,
  ongoing, duplicates, late events, multiple sites, remote, corrections and poor location, no arrival, overtime.
- `tests/test_attendance_api.py`: permissions, profile versions, web and device idempotency and retries, consent and
  pause, no coordinates, device authority seam, corrections, summaries and exports, formula-safe CSV, daily reports,
  sharing, AI draft rules, retention reduction, alerts.
- `tests/test_postgres_attendance.py`: forced RLS and policies, owner-only rows, database refusal of other people's
  writes, sharing read only, idempotency under the unique key, versions, maintenance discovery, the daily job, purge.
- Web: `apps/web/test/attendance.test.ts`, `apps/web/e2e/attendance.spec.ts` (desktop and phone, axe).

## Not in phase A

- Flutter screens (`apps/mobile/lib/features/attendance/`).
- A productivity view and a workspace-wide manager view (needs an owner decision on who may see whom).
- Real-device testing of Shortcuts.
