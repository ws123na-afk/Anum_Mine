# ANUM MINE: live meeting room (F06, F07, F08)

Live text meetings between governed agents, with the owner moderating. Everything on screen is a stored row or a stored event; nothing is simulated.

## Component contract (stable)
`MeetingRoomPanel` in `apps/web/src/MeetingRoomPanel.tsx`:

| Prop | Meaning |
| --- | --- |
| `workspaceId` | Workspace the room belongs to. Checked against the signed-in scope; a mismatch shows "switch workspace". |
| `meetingId` | The live agent meeting to open (`/api/v1/agent-meetings/{id}`). |
| `roomId` | An office room id. Meetings now store their room (`room_id`, see [departments, modules and meeting rooms](ANUM_MINE_DEPARTMENTS_AND_MEETING_ROOMS.md)); the office opens the live meeting of a room by its id (`#office/meeting/<id>`). Without `meetingId` the panel still shows a picker of this workspace's meetings. |
| `onClose` | Called by the Close button. |

The panel is container-query responsive (one column under 760 px of panel width, transcript plus side column above), so a narrow side-panel slot and a full page both work. Deep link: `#office/meeting/<id>` renders `MeetingRoomPage` (full page). `MeetingsView` lists live agent meetings above the imported ones with "Open room" links; imported meetings are unchanged.

## What the room shows
Live transcript (`role="log"`, escaped Markdown), agent speakers with a speaking indicator only between a stored `turn_started` and its `message` or `turn_failed` (ignored after 150 s), attendance with join and leave, raise hand, pause, resume, end, owner messages (interrupt), budget meters (turns, tokens, cost), agenda, draft decisions and action items with links to source messages, create task, linked workspace files, export, delete transcript, replay of finished meetings (stored transcript plus a play back control). English and Arabic (`lib/messages/meetingRoom.*.ts`).

## API (all under `/api/v1/agent-meetings/{id}`)
Reads need `task:read`; writes need `workspace:manage` and, for moderation, ownership of the meeting.
- `GET /room` bundle, `GET /events?after=` JSON replay, `GET /events/stream` SSE (`id:` is the event number; resumes from `Last-Event-ID`; ends with `event: end` when the meeting is over, or `event: reconnect` after 240 s).
- `POST /presence`, `POST /hand` (raise needs presence; lower is owner only), `POST /messages` (owner, idempotent by `request_key`).
- `POST|PATCH|DELETE /agenda`, `POST /outputs`, `PATCH /outputs/{id}` (confirm or dismiss), `POST /outputs/{id}/task`, `POST|DELETE /files`.
- `GET /export?format=markdown|json` (owner), `DELETE /transcript` (owner, ended meetings only).
- Pause, resume and end use the existing `POST /state`. Turns come from `POST /run` or `POST /execute-turn` or the worker.

## Removing an agent
Before 29 October 2026 nothing recorded an AI agent leaving a meeting: pausing it, or ending its part, was not stored, so it kept speaking and stayed seated in the headquarters until the meeting ended. Now the meeting owner can remove an agent from an active or paused meeting in the room: each agent in the Agents list has **Remove**, which asks first ("Remove Maya? It stops speaking now and cannot rejoin this meeting.", **Remove from meeting** or **Cancel**). The API (`POST /agents/{agent_id}/remove`, owner and `workspace:manage`, the shown revision) stores the server time and the owner on the agent's participant row (`left_at`, `removed_by`, migration `0121_meeting_agent_leave`) and appends the room event `agent_left` (`{"agent_id"}`); the audit record `agent_meeting.agent_remove` carries ids only. From then on:
- the agent takes no further turn (`meeting_execution.ACTIVE_AGENT_SQL` reads only participants without `left_at`; with every agent removed, Run next turn is disabled),
- its desk no longer says it is in a meeting (`office_presence`),
- the meeting detail gives it `left_at`, so the headquarters no longer seats or counts it and the room inspector lists it as "Left" (`lib/hqPresence.ts` `loadMeetingEvidence`, as for people since #316),
- the room shows it as "Left the meeting" with no buttons: at once for owners (the room's event stream), within the 10 s room read for everyone else.
The participant row stays, so the transcript keeps its speaker. A removed agent cannot rejoin the meeting (agents are added only to scheduled meetings). A leave is only ever this owner click: pausing an agent profile or a meeting records nothing. Tests: `services/api/tests/test_postgres_meeting_agent_leave.py`, `apps/web/test/liveMeeting.test.ts`, `e2e/meeting-room.spec.ts` (English and Arabic).

## Guarantees
- A message is stored before its event; events are numbered without gaps under the meeting row lock; a client that reconnects with the last event number gets exactly the missed events.
- Turn claims are durable leases; a crashed worker's lease expires and the turn is retried with the same key; duplicate requests return the stored turn without a second model call.
- Limits: max turns, time and cost per meeting, token budget; a failed or over budget commit leaves no message and no lease and writes `turn_failed`.
- A raised hand, a pause, an ended meeting or the tenant emergency stop blocks new agent turns. Agents are text only: no tools, no approvals bypassed.
- Secrets are redacted before storage (agents and people). Cross-workspace agents are refused (404); RLS isolates every table.
- Decisions and action items are drafts the owner confirms. A confirmed action item becomes a task through the existing task path (status `created`; running it follows approval rules).

## Retention
`docs/meetings.md` states meetings have no automatic expiry. The owner can export, and delete the transcript of an ended meeting (messages, events, leases); usage totals, confirmed decisions and created tasks remain. An automatic retention window needs an owner decision and is not built.

## Not built
Automatic periodic running of turns from the room (the room starts one governed turn at a time; the unattended worker remains a managed service).

## Owner-only transcript and monthly budget

Only workspace owners read messages, decisions, files and the export; members and viewers see title, status, attendance and agenda, with counts and a notice, and the room polls instead of streaming. The room shows the workspace's monthly agent meeting budget and disables Start, Resume and Run next turn when it is used up. Details: [budget and transcripts](ANUM_MINE_LIVE_MEETING_BUDGET_AND_TRANSCRIPTS.md).

## Summary, model drafts, work links and the JSON record

An ended meeting can be summarised by the owner (one metered model call, drafts only, confirmed with a click), decisions can be linked to tasks, approvals and runs, and the record downloads as Markdown or JSON. Details: [summary and work links](ANUM_MINE_MEETING_SUMMARY_AND_WORK_LINKS.md).
