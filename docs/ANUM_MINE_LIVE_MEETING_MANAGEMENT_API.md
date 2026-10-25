# ANUM MINE: Persistent Live Meeting Management API

This increment closes the missing HTTP/database integration for the *management*
part of live agent meetings. It uses migration `0098_agent_live_meetings`, now
present in `main`, and intentionally keeps imported Zoom/Teams recordings separate.

## Available authenticated endpoints
- `POST /api/v1/agent-meetings`: workspace manager creates a durable scheduled meeting with validated cost/turn/time/token limits
- `GET /api/v1/agent-meetings`: authorized workspace reader lists actual PostgreSQL records, cursor-paginated
- `GET /api/v1/agent-meetings/{meeting_id}`: record and scoped participant list
- `POST /api/v1/agent-meetings/{meeting_id}/join-self`: meeting owner adds themselves as a human participant, revision protected
- `POST /api/v1/agent-meetings/{meeting_id}/state`: owner transitions scheduled/active/paused/terminal state, revision protected
- `GET /api/v1/agent-meetings/{meeting_id}/turns`: read actual stored turns in sequence, no synthetic transcripts

All requests use existing identity and authorization dependencies. Writes
require `workspace:manage`; reads require `task:read`. Every query applies
tenant+workspace filters and PostgreSQL forced RLS provides another boundary.
The in-memory backend fails closed with HTTP 503. The service is deliberately
unavailable until database `0098` is applied.

## Why this is not yet autonomous meeting execution
Only the meeting owner can register **their own** human participation. There is
no API accepting arbitrary agent IDs because we still need server-side profile
visibility, tool governance, cost policy, approval checks and durable reservation.
The room coordinator from PR #197 remains a pure policy/execution boundary.
Neither the frontend nor this management API can execute paid model calls.

## Release gates
1. Run and pass API unit tests and migrated PostgreSQL application-role tests.
2. Wire the coordinator to `agent_meeting_turn_leases` with transactional CAS,
   timeout/recovery, usage reconciliation, audit and event outbox.
3. Authorize agent enrollment using the same scoped agent-profile visibility and
   emergency-stop/approval policy enforced by ANUM's existing agent runtime.
4. Add server-sent events with cursor replay, reconnection and authorization.
5. Build the live meeting-room React component and integrate it with the
   HQ room navigation only after real message events are available.
6. Perform DB backup, feature-gated staging migration and release checks prior
   to enabling the production route.

## Integration with other Codex work
This branch starts from current GitHub `main`, not from the four local agent
worktrees. It modifies only the new meeting store/API/tests and adds one route
registration in `main.py`, avoiding changes to the office 3D/frontend modules.


## Room link (migration 0108)

`POST /api/v1/agent-meetings` accepts an optional `room_id`: an office room of kind `meeting` in the same workspace (404 for a room that is not visible in the workspace, 422 for another kind). `room_id` is returned by create, get, list and the room bundle, is carried by the `agent_meeting.create` and `agent_meeting.state_change` audit records and the `state` meeting event, and `GET /agent-meetings` accepts `status=` and `room_id=` filters. Deleting the room clears only the link. Details: [departments, modules and meeting rooms](ANUM_MINE_DEPARTMENTS_AND_MEETING_ROOMS.md).

## Budget, transcript access and turn audit

`GET|PUT /api/v1/agent-meetings/budget`; transcript reads (`turns`, `events`, `events/stream`, `export`) need the owner role; each turn is audited as `agent_meeting.turn`; a reached cap answers 402 `agent_meeting_budget_exceeded`. See [budget and transcripts](ANUM_MINE_LIVE_MEETING_BUDGET_AND_TRANSCRIPTS.md).


Summary, decision to work links and the JSON record (`POST /{id}/summary`, `POST /{id}/outputs/{output_id}/links`, `DELETE /{id}/links/{link_id}`, `GET /work-links`): see [summary and work links](ANUM_MINE_MEETING_SUMMARY_AND_WORK_LINKS.md).
