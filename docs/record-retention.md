# Record retention: meeting transcripts and visitor records

Owner decision **O5**, answered on 28 October 2026: "keep what is standard". Recorded in the [feature completion matrix](ANUM_MINE_FEATURE_COMPLETION_MATRIX.md) (rows 7.9, 9.11, 10.4, 10.5).

## What is kept, and for how long

| Record | Standard period | An owner may choose | Counted from | What is deleted | What stays |
|---|---|---|---|---|---|
| Meeting transcripts (live agent meetings) | 90 days | 7 to 1825 days | the meeting's end (finished, cancelled or failed) | the messages, turn leases and event log of the meeting | the meeting, its participants, usage totals, the summary, confirmed outputs (their message citations are cleared), their links to work and the tasks |
| Reception visitor records (`office_visitors`) | 365 days | 30 to 1825 days | the visit's latest time: check-out, cancellation, check-in (a visitor never checked out) or the planned time (a visitor who never came) | the whole visit record | nothing of the visit; the audit record keeps ids and counts only |
| Office history snapshots | not stored | | | nothing: no time-indexed snapshot is stored (owner decision O8) | |

Other stores already have their own retention: voice transcripts, assistant conversations, WhatsApp history, Email Center mail, call recordings and in-app calls ([conversations](conversations.md), [WhatsApp](whatsapp.md)). The matrix lists nothing else under O5.

A transcript removed by retention looks exactly like one the owner removed with `DELETE /api/v1/agent-meetings/{id}/transcript`: both run `live_agent_meeting_room.erase_transcript`, the room says the transcript was deleted, and a summary can no longer be drafted from it.

## The setting

Settings > Workspace, card **How long records are kept** (`apps/web/src/RecordRetentionCard.tsx`, in the lazy Data export and deletion chunk). It shows both periods, whether each is the standard one, what is deleted and what stays, that no office snapshot is stored, that the clean-up runs once a day, and who changed the periods last.

| State | What the card says |
|---|---|
| Loading | "Reading how long records are kept..." |
| Ready | Each period with its badge (Standard or Set by an owner) and its explanation |
| Owner | A days field per period with its range, Save, and "Use the standard (N days)" when changed |
| Not an owner | The periods, and "Only a workspace owner can change how long records are kept." |
| Bad input | Empty, not whole days, or outside the range, said before anything is sent |
| Stale | "Someone changed this setting a moment ago", then the stored periods are shown |
| Error | What failed and Try again |
| Permission denied | Who to ask |

English and Arabic (right to left, Western digits).

### API

`services/api/anum_api/record_retention_api.py`, PostgreSQL only (the memory backend answers 503, never a made-up period).

| Route | Permission | What it does |
|---|---|---|
| `GET /api/v1/workspace/record-retention` | `task:read` | `transcripts` and `visitors` (`days`, `is_default`, `default_days`, `min_days`, `max_days`), `revision` (0 while the standard periods apply), `updated_by`, `updated_at`, `snapshots_stored: false`, `can_manage` |
| `PUT /api/v1/workspace/record-retention` | `workspace:manage`, `input_mode: "click"` | `{transcript_days, visitor_days, expected_revision}`; `null` returns a period to the standard. 403 for a typed or spoken answer, 422 outside the range (with the range in the message), 409 for a stale revision |

Refusals are answered in Arabic for `Accept-Language: ar`. Each save leaves one audit record `record_retention.update` with the old and new periods.

### Storage

Migration `0120_record_retention` (expand only) adds `workspace_record_retention`: one row per workspace, only once an owner saved; null columns mean the standard period. Check constraints hold the ranges, forced row level security by tenant and workspace, the tenant purge policy, and an entry in `tenant_lifecycle_tables.py` (exported with the organisation, purged with it).

## The purge

`python -m anum_api.record_retention [--dry-run]`, and the daily retention job `python -m anum_api.voice_retention` runs it last and reports it under `record_retention` (no extra schedule in Helm, Render or the Oracle compose stack).

1. **Discover** as `anum_maintenance` (migration 0011's pattern, never BYPASSRLS, never SECURITY DEFINER). Migration 0120 grants it `tenant_id` and `workspace_id` only, of ended meetings that still hold a transcript and ended more than 7 days ago, and of visits whose latest time is more than 30 days ago (the shortest periods allowed). It cannot read a title, message, name, company or purpose and cannot write.
2. **Act** per workspace in one transaction as the application role inside that tenant and workspace's RLS context: read the workspace's periods, erase each old transcript (rows locked with `for update skip locked`, so an owner's delete in progress is never raced), delete old visits.
3. **Audit** one record per purged workspace, `record_retention.purge` by `system:retention`, with the periods and counts only. The history panel names both actions in English and Arabic.

The run is idempotent (a second run finds nothing) and prints counts only. On SIGTERM the workspace in progress finishes and commits, then the run stops and reports `stopped: true`.

## Checks

- API: `services/api/tests/test_record_retention.py` (routes, 503 without PostgreSQL, owner and click only, range refusals with clear and Arabic messages, the loop, dry run, stop on SIGTERM) and `test_postgres_record_retention.py` (as the app role: what `anum_maintenance` can and cannot read, standard and owner periods, what stays after a transcript goes, audit without content, idempotence, the daily job's report, the setting route, another tenant's context purges nothing). `test_postgres_hq_rls.py` checks that the discovery policies are read only and that `anum_maintenance` holds only scope columns.
- Web: `apps/web/test/recordRetention.test.ts` and `apps/web/e2e/record-retention.spec.ts` (owner journey, bad input, stale save, member, error and retry, denied, accessibility, five widths, Arabic).
