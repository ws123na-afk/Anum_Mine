# ANUM MINE headquarters: history panel and room history slider

Web only. Code: `apps/web/src/HqHistoryPanel.tsx` (drawer or sheet, tabs, filters, paging), `HqHistoryParts.tsx` (filter form, rows, infinite scroll), `HqHistoryDetail.tsx` (one record, sources, same request), `HqRoomSlider.tsx` (the scrubber), `useHqHistory.ts`, and the pure libraries `lib/hqHistory.ts` (queries, paging, scopes, source references, export filters), `lib/hqRoomHistory.ts` (steps, gaps, `stateAt`), `lib/hqHistoryApi.ts`, `lib/hqHistoryText.ts`. Contract: `packages/contracts/src/officeHistory.ts`. Server: `services/api/anum_api/office_history_api.py` ([F09 spec](ANUM_MINE_F09_AUDITED_HISTORY_API.md)). Help: [English](help/office-history.md), [Arabic](help/office-history.ar.md).

## What the person sees

- **Opening it**: Office tools, History (whole office); the room inspector's "Room history"; the agent card's "Agent history"; a "History of NAME" link on each Team card (also in the Team dialog). It opens as a right-hand drawer (left in Arabic) from 761 px and as a bottom sheet below, with a focus trap, Escape (closes a record first, then the panel) and focus returned to the opener.
- **Timeline** tab: records newest first, grouped by day, each with the action in words, who, the target, the event type and a failed outcome. Search, event type, room, agent, exact actor id and a start and end day (end day included, local time). Infinite scroll plus a "Load older records" button for keyboards; a count that is announced; "End of the recorded history for these filters" only when the server has no cursor left.
- **A record** opens inside the panel (no navigation): who, when, action, target, outcome, record id, correlation id; for office changes a Before and After table (floor layout records list each room and desk that differs); **Source records** (task, run, approval, meeting, room, desk, floor, building, agent) with a one-record summary read on demand from the existing read routes (`GET /tasks/{id}`, `/agent-runs/{id}`, `/approvals/{id}`, `/agent-meetings/{id}`; only title, status, dates, agent, risk and counts are read, never a prompt, a result, approval arguments or meeting turns) and a "Show this room in the office" action; **Show records of the same request** reads `GET /office-history/correlations/{id}`. The summary uses each record's own permission, so a person who cannot read it sees who to ask.
- **Room changes** tab (the room history slider): see below.
- **CSV**: "Export these records (CSV)" is offered in every view. Office, a named event type and one request call the audited audit export (`GET /api/v1/audit/export?format=csv`, permission `audit:export`) with the same filters (event type becomes the action prefix). Room views, agent views and the "Other" type cannot be expressed by that export, so the panel reads the same audited export as JSON for the broad filters (text, actor, dates, type prefix), keeps only the rows the view shows (same rule as the timeline) and saves them as CSV (`history-room-DATE.csv`). That is every stored matching record, not only the loaded ones, and never more than the permission already allows. Metadata keys that could hold secrets or conversation text (prompt, transcript, turns, message, content, result, body, token, password and similar) are dropped. The button is disabled with an explanation when the workspace permissions say the viewer cannot export; if that is unknown the server decides and a 403 explains who can grant it. No matching record: no file and a plain message.
- **Requests and sources**: a record can open its whole request (same correlation id) as the timeline, which is exportable; the same-request list is paged and says how many records share the id; a record with no correlation id says so. A task, run, approval or meeting source offers "Show records that name this task" (a text search for its id).

## Data truth

| On screen | Source | Without a source |
|---|---|---|
| Every row | `GET /api/v1/office-history` (stored audit records of the open workspace, same permission gate as the API: workspace owners and organisation administrators) | Empty state with the next action; a 403 explains who can read |
| Event type | the server's category (decided from the action prefix) | none |
| Search, dates, actor | server filters `q`, `since`, `until`, `actor` | none |
| Room and agent views | the loaded records that name the room, its desks or the agent as actor, target or anywhere in their recorded values (client side) | "No loaded record names..." and an offer to load more |
| Before and after | `metadata.before` / `metadata.after` written by the office registry API in the same transaction as the change | The row says the record has no values |
| Past presence, who sat where | not stored | never shown ("not stored and not shown") |

The server pages stored records first and applies the event type after, so a page can be empty while a cursor is set. The panel keeps following the cursor (up to six pages per load, then "Load more") instead of showing a false end.

## Room history slider

A scrubber over the stored records that changed one room (rename, update, move or resize, create, copy, delete, desk changes in that room, floor layouts that touched it). Rules, all in `lib/hqRoomHistory.ts` and unit tested:

1. A step is one record. Steps are evenly spaced because they are records, not time; the track never implies timing.
2. The room values at a step are that record's own `after` snapshot. Nothing is interpolated and no movement is drawn between steps.
3. The first step is the room "before the oldest loaded record": that record's own `before` values, or "created by this record". If older records exist but are not loaded, the panel says earlier changes may be missing and offers to load them.
4. If a record's `before` does not match the previous record's `after` (some audited field differs), a **gap** step is inserted: "Cannot be reconstructed for this time. The room changed (fields) without a record explaining it."
5. "Show the room at" a date and time resolves to a step only when records cover it. Before the oldest loaded record, inside a gap, or in the future it answers "Cannot be reconstructed for this time" with the reason. Before a loaded creating record it answers that the room did not exist yet.
6. Desks appear only when a record gives their state ("Only desks that have a record appear"); occupancy and presence are never rebuilt. Owner decision O8 (time-indexed snapshots) is open, so this change stores nothing new.

## States

Loading (spinner and status text), empty (plain, filtered, scoped, each with a next action), error (what failed, Try again, previous rows stay), denied (who can read), invalid filters (refused before any request), large histories (infinite scroll, button, bounded work per load), no WebGL (the panel does not use WebGL), English and Arabic (right to left, logical CSS properties, mirrored chevrons, Western digits), keyboard (tabs with arrow keys, the slider is a native range input with `aria-valuetext`, Escape, focus return), screen readers (live regions for counts, steps and results, dialog with `aria-modal`).

## API change (read only)

`GET /api/v1/office-history` gains optional `since` (inclusive), `until` (exclusive) and `q` (text in actor, action or target), mapped to the existing `AuditFilter`; a reversed range answers 422. No new storage, role or migration. Tests: `services/api/tests/test_office_history_api.py` (forwarding) and `test_office_history_http.py` (filters, 403 for members, workspace isolation). The tenant and workspace still come from the request context and PostgreSQL row level security stays authoritative (`list_audit_page`).

## Not finished here

- Department and project filters: audit records carry neither, and the scene projection does not expose a room's department. Room and agent views stand in.
- A server-side room or agent filter: room and agent views filter loaded pages on the device, so an old record needs more loading. A small read-only `subject` filter on the server would remove that (needs a JSON index to stay cheap).
- A true replay of past presence and layout needs time-indexed snapshots (owner decision O8). None is stored, so the retention job (O5) has no snapshot to purge.
- A narrowed export (room, agent, Other) reads the whole matching audit export before narrowing; a server-side `subject` filter would make it cheaper. The export's own audit record lists the broad filters, not the room or agent.
- Task, meeting and approval screens do not link back to their correlation view; full transcript text is not exported (transcripts are kept 90 days by default, O5) and no time-indexed snapshot is stored (O8).
