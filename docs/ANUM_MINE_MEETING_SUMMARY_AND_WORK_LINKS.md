# ANUM MINE live meetings: summary, model-drafted decisions and action items, decision to work links, JSON record

Closes matrix rows 9.6 (summary), 9.7 (model-drafted extraction), 9.9 (decision linked to work) and 9.10 (JSON export in the web), and with them row 14.8.
Code: `live_agent_meeting_summary.py`, `live_agent_meeting_room.py`, `live_agent_meeting_room_api.py`, migration `0111_meeting_summary_links`
(renumbered at merge if branches collide). Web: `MeetingSummaryParts.tsx`, `MeetingRoomParts.tsx`, `lib/liveMeeting.ts`, `lib/liveMeetingApi.ts`.
Tests: `test_live_agent_meeting_summary.py`, `test_postgres_meeting_summary_links.py`, web `test/meetingSummary.test.ts`, `e2e/meeting-summary.spec.ts`.

## What the owner gets

1. After a meeting has ended (finished, cancelled or failed, with messages and a transcript that was not deleted) the owner presses **Draft summary**.
   One model call reads the stored transcript and the server stores the answer as **drafts**: a `summary` output and, where the discussion supports them,
   `decision` and `action_item` outputs marked `origin: model`. Nothing is confirmed by the model.
2. The owner reads each draft against the messages it cites and confirms it **with a click**. A confirmed decision can be linked to a task, an approval or a run.
3. The room and the export show the summary, the decisions, the confirmed outputs and the work each decision led to.

## Rules (server side)

| Rule | Where |
|---|---|
| Owner only: the meeting owner (`created_by`) with `workspace:manage`. Members and viewers get 403 and see only counts. Transcripts stay owner only. | `POST /{id}/summary`, `require_owner`, `bundle_for` |
| Ended meeting only, at least one message, transcript not deleted, no live summary already (a dismissed one may be replaced), no other summary in flight (a lease on the meeting). | `_generate` |
| Counts against the workspace's monthly meeting budget (USD 50 by default). `gate` runs under the workspace lock in the transaction that takes the lease; at the cap the answer is 402 `agent_meeting_budget_exceeded` before any model call, audited as `blocked`. The reported cost is added to the monthly ledger and to the meeting's totals in the transaction that stores the drafts. A call whose text cannot be used is still paid for, recorded, and refused (409, nothing stored). | `live_agent_meeting_budget` |
| The emergency stop blocks it like it blocks a turn. A mock model cannot run it. | `_fresh_gateway`, `agents_stopped` |
| **Only what the transcript supports.** The prompt says the messages are data, to state nothing they do not support, to cite message numbers for every statement, and that an agent's suggestion is not a decision. The server then keeps a statement only if it cites at least one message that exists in this meeting; the rest are dropped and counted (`dropped`, shown to the owner). Citations are stored (`source_sequences`) and written into the summary as `[#3, #5]`. Secrets are redacted. A long transcript is cut at a whole message and the summary says which messages it covers. | `parse_extraction`, `summary_text` |
| **Click only to confirm.** Confirming a summary or any `origin: model` draft needs `input_mode: "click"` and the `payload_hash` (SHA-256 of output id, kind, text and state) of the text the owner was shown. Typed or voice input gets 403, a stale hash gets 409, and a draft cannot be edited and confirmed in one call. The personal assistant has no tool that calls it. A confirmed summary or model draft is not edited in place (move it back to draft). A decision or action item typed by a person confirms as before. | `output_decide` |
| Audit, ids and states only, never text: `agent_meeting.summary` (model, tokens, cost, drafts created, dropped, month used before and after, limit), `agent_meeting.output_decide` (output id and kind, previous and new state, input mode, whether edited), `agent_meeting.decision_link` and `decision_unlink` (target kind and id, previous and new state). A refusal at the cap and a failed call are audited too. | `live_agent_meeting_audit.ALLOWED_FIELDS` |
| Tenant scoped, forced RLS. Targets are read through the caller's scoped session, so a task, approval or run of another workspace or tenant is "not found". | migration 0111, `link_create` |

## API

* `POST /api/v1/agent-meetings/{id}/summary` returns 201 `{summary, decisions, action_items, dropped, covered_up_to, messages, cost_usd}`; 402 at the cap, 403 not the owner, 409 not ended, already summarised, no messages or unusable answer, 503 model unavailable.
* `PATCH /{id}/outputs/{output_id}` also takes `input_mode` and `payload_hash`. Room outputs carry `origin`, `drafted_with` and `payload_hash`.
* `POST /{id}/outputs/{output_id}/links` `{target_kind: task|approval|run, target_id, input_mode: "click"}`: only a **confirmed decision**; 201 on a new link, 200 with the same link on a repeat (idempotent, one audit record); 404 for an unknown target; 403 unless click. `DELETE /{id}/links/{link_id}` removes one.
* The room bundle has `links` (each with the target's title and status), empty with `links_count` for non-owners.
* `GET /api/v1/agent-meetings/work-links?target_kind=&target_id=` (owner only) answers from the work side: which confirmed decisions and meetings led to it.
* `GET /{id}/export?format=json` now also has `summary`, `decisions` (each with its `links`), `links` and `confirmed_outputs`; Markdown has a Summary section (state stated) and the linked work under each decision.

## Web

The meeting room (owner view) has a **Meeting summary** card: waiting while the meeting runs, empty with the next action, drafting (loading), the draft with one line per statement and message chips that jump to the cited message, confirm and dismiss, "Draft a new summary" after a dismissal, and plain messages for a deleted transcript, a used up budget and a failed call. Model drafts carry a "Model draft" chip. A confirmed decision has **Link to work** (task, approval, or the latest run of a task; the lists come from the real API) and shows its links with unlink; **Work from this meeting** shows the other direction, each task, approval or run with the decision behind it. The record card downloads Markdown and **JSON**. English and Arabic, right to left, five viewports checked. The new parts are in the lazy room chunk; nothing was added to the entry bundle.

## Not done, stated plainly

* Whether a cited message really supports a statement is not machine-checked. The server proves the citation exists; the owner judges the meaning when reviewing, which is why nothing counts until confirmed.
* No PDF export (not requested). No automatic summary when a meeting ends: the owner asks, because each one costs budget.
* The work list in the link form shows the newest 50 tasks and 25 approvals; a run is reached through its task's latest run. Older work cannot be picked from the form (the API accepts any id of the workspace).
* The reverse lookup `work-links` is built in the API; the Tasks and Approvals pages do not show it yet.
* Transcripts are kept 90 days after the meeting by default (O5, owner editable, [record retention](record-retention.md)); deleting a transcript, by hand or by retention, keeps confirmed outputs, links and the summary.
