# ANUM MINE live agent meetings: monthly budget, owner-only transcripts, turn audit

Closes security findings S-02, S-03 and S-04 (owner decisions of 25 October 2026). Code:
`live_agent_meeting_budget.py`, `live_agent_meeting_audit.py`, `meeting_execution.py`, the two meeting API
modules, migration `0107_agent_meeting_budget` (renumbered at merge if branches collide). Tests:
`test_live_agent_meeting_security.py`, `test_postgres_live_agent_meeting_security.py`, web
`test/meetingBudget.test.ts`, `e2e/meeting-room.spec.ts`, `e2e/admin.spec.ts`.

## Monthly cost cap (S-02)

* Default USD 50 per workspace per calendar month (UTC), shared by every meeting of the workspace.
  A workspace owner changes it in Budgets, "Agent meeting budget", or with `PUT /api/v1/agent-meetings/budget`
  `{"monthly_cost_limit_usd": 20}` (whole cents, 0 to 100000; 0 stops all agent meetings; `null` returns to the default).
  Every change is audited as `agent_meeting.budget_update` with the old and new limit.
* `GET /api/v1/agent-meetings/budget` (any member with `task:read`) returns `limit_usd`, `used_usd`, `remaining_usd`,
  `percent`, `turns`, `exceeded`, `period_start`, `resets_on`, `can_manage`. The room and Budgets show it.
* Metering: the cost of each committed turn is the gateway's own estimate (the figure the per-meeting limit uses),
  added to `agent_meeting_usage_monthly` in the same transaction as the turn. Usage is the larger of that ledger and
  the sum of stored turns this month, so turns from before the ledger count and deleting a transcript does not hand the
  budget back.
* Enforcement is on the server, before the model is called: `execute_meeting_turn` calls `gate` in the transaction that
  claims the turn. The browser only mirrors it (buttons disabled, notice shown). Starting or resuming a meeting
  (`POST /state` to `active`) is refused the same way.
* Concurrency: `gate` first takes a PostgreSQL advisory lock for the workspace, held until the turn lease is committed, so
  parallel meetings are decided one after another and each sees the others' running leases. A turn may start only if
  `used + running_turns * reserve < limit`, where `reserve` is the largest cost among the workspace's last 20 turns (at least
  USD 0.10). So the month can pass the cap by about one turn (the last turn started under the cap), not by one per meeting.
  If a turn costs more than its reserve the difference is also stored and shown, never hidden.
* When the cap is reached the API answers 402, code `agent_meeting_budget_exceeded`, "Monthly agent meeting budget reached: ..."
  (Arabic through `Accept-Language`). The turn that uses the last of the month pauses its meeting; a refused turn pauses its
  meeting too and adds a `budget_reached` event (limits and amounts only). The unattended worker stops with `budget`.
  The room shows the reason and disables Start, Resume and Run next turn.

## Transcripts are for workspace owners only (S-03)

"Owner" is the workspace owner role (`workspace:manage`), not only the person who created the meeting. Members and viewers
get a clear 403 ("Only a workspace owner can read meeting transcripts. ...") on every path that carries text, and the refusal
is audited as `agent_meeting.transcript_denied` (path and role, no text).

| Path | Owner | Member or viewer |
|---|---|---|
| `GET /agent-meetings` (list), `GET /agent-meetings/{id}` | yes | yes (title, status, times, limits, participants; no text) |
| `GET /agent-meetings/budget` | yes | yes |
| `GET /{id}/room` | full bundle | metadata, attendance, agenda; `outputs` and `files` empty with `outputs_count`, `files_count`, `transcript_hidden: true` |
| `GET /{id}/turns` | yes | 403 |
| `GET /{id}/events` | yes | 403 |
| `GET /{id}/events/stream` (SSE) | yes | 403 |
| `GET /{id}/export` (markdown, json) | yes | 403 |

Agents' own use of the history during a meeting is unaffected: the turn executor reads the store directly, the gate is on the
HTTP paths. Tenant isolation and RLS are unchanged (another tenant still gets 404). The web room hides the transcript, decisions
and files for members, shows counts and a notice, does not open the stream (it polls the room every 10 seconds) and still shows
that the meeting happened.

## Audit of every turn (S-04)

Each executed turn writes `agent_meeting.turn` (outcome `success`) in the same transaction: actor (the person), meeting id,
turn number, agent id, request key, provenance (provider and model), input and output counts, cost, redacted flag, month used
and limit, and a correlation id that is also in the `turn_started` event. Failures (`failure`, with an error code), cap hits
(`blocked`, reason `monthly_budget` or `monthly_budget_start`) and a turn attempted by someone other than the owner (`denied`)
are recorded in their own transaction so they survive the refusal. Metadata is an allow list of short scalar fields
(`live_agent_meeting_audit.safe_metadata`); no message text, prompt or error string can enter it. A replayed request key is not
audited twice.

## Summaries count too

The owner-triggered meeting summary is a model call and is metered like a turn: gated under the workspace lock, refused with 402 at the cap, its cost added to the monthly ledger, audited as `agent_meeting.summary`. Details: [summary and work links](ANUM_MINE_MEETING_SUMMARY_AND_WORK_LINKS.md).

## Not done, and owner decisions still open

* O5, retention windows for meeting transcripts, was answered on 28 October 2026: the daily retention job deletes a transcript 90 days after the meeting ended (owner editable, 7 to 1825 days), with the same helper as the manual `DELETE /transcript`. See [record retention](record-retention.md).
* The cap counts the gateway's cost estimate, not an invoice. A model without a price estimate reports no cost and the meeting
  refuses the turn already ("Model must report a cost estimate"), so no unpriced spend enters.
* The rate of turns per minute (S-05) is not part of this change; see [turn rate](ANUM_MINE_AUTONOMOUS_MEETINGS.md#turn-rate-s-05).
* An organisation-wide cap across workspaces is not built; the cap is per workspace as decided.
