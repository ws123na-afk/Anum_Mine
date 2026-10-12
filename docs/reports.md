# Reports

A report is one generic record, whatever produced it: a type, a period, key metrics
with their change against the previous period, highlights, a data snapshot (columns and
rows) and a Markdown rendering. The report centre lists them, opens one, downloads it,
shares it with the workspace or keeps it private, and generates new ones now or on a
schedule. The Assistant's structured answers become reports too, shown in the chat as a
card with **View Full Report**.

Code: `services/api/anum_api/reports.py`, `db/library_repository.py`, migration
`0042_library`; web `apps/web/src/ReportsView.tsx`, `ReportAnswerCard.tsx`; Flutter
`apps/mobile/lib/features/library/`. Wire types: `packages/contracts/src/library.ts`.
Favorites and the knowledge base are in [Knowledge base and favorites](knowledge-base.md).

## The record

| Field | |
| --- | --- |
| `type` | lowercase slug: `tyre`, `commerce`, `task_summary`, `automation_summary`, `marketing`, `custom`, or any other a producer names (shown humanised) |
| `title`, `summary` | |
| `period_start`, `period_end` | dates, optional; start on or before end |
| `metrics` | up to 24 of `{label, value, unit?, change_percent?}`; numbers are formatted to text |
| `highlights` | up to 20 short lines |
| `data` | `{columns, rows}`: at most 40 columns and 5,000 rows, every row as wide as the columns |
| `body_markdown` | the producer's Markdown, or one rendered from the fields above |
| `visibility` | `workspace`, or `private` to its creator |
| `created_by`, `created_by_kind` | `user`, `agent` (the Assistant) or `automation` |
| `source` | `conversation_id` and `turn_id` for a chat answer; `automation_run_id` for an automation |

`reports` is under forced RLS on tenant and workspace with a `RESTRICTIVE` owner policy:
a private report is invisible to everyone but its owner (404, not 403), in SQL as much as
in the API.

## Who produces reports

- **People**: `POST /api/v1/reports` saves any structured analysis (an integration, a
  script or a person); `POST /api/v1/reports/generate {"type", "period_days", "visibility"}`
  runs a built-in generator over the workspace's own data:
  - `task_summary`: tasks created in the period by status (completed, failed, waiting for
    approval), change against the previous period, failures named, the task list as data;
  - `automation_summary`: automation runs in the period by workflow and outcome;
  - `commerce_summary`: orders synced from the connected sales channels
    ([Commerce](commerce.md)) in the period: orders, units and revenue per currency with
    the change against the previous period, orders per channel and the best-selling SKUs.
    Cancelled and refunded orders are not counted (`COUNTED_ORDER_STATUSES`). The data
    table holds channel, order number, status, time, items, total and currency, never
    buyer names or addresses. Generating it also needs `commerce:read`;
  - `tyre_summary`: the synced tyre data ([Tyre planning](tyre-planning.md)), read through
    `tyre.service.forecasts_for` and `tyre.analytics.fleet_summary` / `scrap_reasons`:
    tyres fitted, removals in the period (with the change against the previous period)
    and their reasons, tread below the minimum, overdue inspections, tyres due within 30
    days, average tread, pressure compliance and cost per 1,000 km; the data table lists
    the tyres by projected removal date. Without connected and synced tyre data it answers
    409 with the step to take ("Connect your tyre data first."). Generating it also needs
    `tyre:read`. The tyre module's own reports (`tyre_reports`, PDF and delivery) stay
    where they are; this is the workspace-wide summary.

  `GET /api/v1/reports/generators` lists them.
- **The Assistant**: see [chat answer cards](#chat-answer-cards).
- **Automations**: a workflow step with `action: "report.generate"` and
  `input: {"type": "task_summary" | "automation_summary" | "commerce_summary" | "tyre_summary", "period_days": 1-366}` stores a
  shared report each time it runs ([Automation](automation.md)). A bad input fails the step
  with a message that names the accepted values. "Schedule it" in the report centre
  creates such a workflow and a daily, weekly or monthly schedule in the browser's time
  zone.

Marketing reports have no built-in generator yet: that module does not exist in this
change. Their producers (connectors, agents or the Assistant) save them with
`POST /api/v1/reports` or an `anum-report` block, and the report centre shows them like any
other type.

## Chat answer cards

The Assistant's system prompt asks it, when an answer is a structured analysis with
figures (totals, changes, top reasons), to end the reply with one fenced block tagged
`anum-report` holding JSON:

```json
{"title": "Tyre cost, September", "type": "tyre",
 "period_start": "2026-09-01", "period_end": "2026-09-30",
 "summary": "...", "metrics": [{"label": "Total cost", "value": "12,400", "unit": "SAR", "change_percent": 8.5}],
 "highlights": ["Puncture repairs doubled"], "data": {"columns": ["reason", "cost"], "rows": [["Puncture", 4200]]}}
```

and never to invent numbers. When the reply completes, the server
(`conversations.stream_reply`):

1. removes the block from the stored turn;
2. validates it with the same limits as `POST /reports` (model output is untrusted: an
   invalid block, or one without metrics or highlights, is dropped and no report is made);
3. stores a **private** report (`created_by_kind=agent`, `source` = conversation and turn);
4. sends the card in the stream's `done` event: `{"assistant_turn", "report": ReportCard}`.

`GET /api/v1/conversations/{id}` returns `reports` (the cards of that conversation by turn),
so cards survive a reload. While the reply streams, the web and Flutter clients hide the
block from its first line on. The card shows up to four metrics with their change, three
highlights and **View Full Report**, which opens the stored report (`#reports/<id>` on the
web, the report screen on the phone). The asker can share it with the workspace from there.

## Report centre

| Route | |
| --- | --- |
| `GET /api/v1/reports?type=&q=&created_by_kind=&limit=&offset=` | newest first, without data and body; `total` and `types` (with counts) for the filters |
| `GET /api/v1/reports/{id}` | the full report and `can_manage` |
| `PATCH /api/v1/reports/{id} {"title"?, "visibility"?}` | rename, share or make private |
| `DELETE /api/v1/reports/{id}` | delete (and the deleting user's star of it) |
| `GET /api/v1/reports/{id}/export?format=csv` or `format=md` | download the data snapshot as CSV (UTF-8 with BOM; formula-like cells quoted) or the Markdown |

Permissions: reading and exporting need `task:read`; saving and generating `task:create`
(generating an automation summary also `automation:read`, a commerce summary also
`commerce:read`, a tyre summary also `tyre:read`). A `report.generate` automation step runs
as the workflow's creator inside the workspace, like the other engine steps; it does not
check those extra read permissions again at run time. Renaming, sharing and deleting
are for the report's creator, or a workspace owner for a shared report; only the creator
can make a report private.

**PDF**: the web's "Print or save as PDF" prints only the open report (a print stylesheet),
so the browser renders Arabic and every script correctly; there is no server-side PDF.
The phone saves CSV or Markdown.

## States the clients handle

Loading; empty with the next action (generate, schedule, or ask the Assistant); errors
with Try again; permission denied that says who can (403 on list, generate or manage);
a deleted or someone else's private report says so; long lists paged and filtered by
type, creator and words; tables over 100 rows (web) or 50 (phone) say how many are
shown and point at the CSV. English and Arabic, right to left. Tests:
`services/api/tests/test_library.py`, `test_postgres_library.py`,
`apps/web/test/library.test.ts`, `apps/web/e2e/library.spec.ts`,
`apps/mobile/test/library_test.dart`.

## Sharing

A report goes out by email or WhatsApp only as a governed send that waits for an owner's
approval with exactly the text that will leave. This API never sends anything itself.

| Route | |
| --- | --- |
| `POST /api/v1/reports/{id}/share/email {"account_id", "to", "cc"?, "note"?, "include_table"?}` | writes a draft in that connected mailbox (subject "Report: <title>", body = note, title, period, summary, metrics and highlights, plus the report's Markdown with its data table when `include_table` is on) and asks to send it: the same paused `send_email` tool call the Inbox's "Send" makes ([Email](email.md)) |
| `POST /api/v1/reports/{id}/share/whatsapp {"conversation_id", "note"?}` | the key figures as a free-form reply in an open WhatsApp conversation: the same `send_whatsapp` call as a reply typed in the WhatsApp inbox, so it always waits for approval and needs the 24-hour customer-service window (409 outside it) ([WhatsApp](whatsapp.md)) |

Both answer `{channel, status, approval, detail, draft_id?, message_id?}`; `status` is
`awaiting_approval`. Reading the report needs `task:read`; the send needs the channel's own
permission (`email:act` for email; WhatsApp's reply permission), and approving follows
that channel's rules (high risk for email: an owner approves with the payload hash). An
unknown or hidden mailbox is a 404 that says to connect one in Inbox; bad addresses are a
422. The web report drawer has a Share panel with Email and WhatsApp tabs (connected
mailboxes, conversations whose window is open) and links to Approvals.

The CSV is not attached to the email: the body carries the text and the Markdown table.

## Typed sections (the dynamic workspace)

A report may also carry `sections` and optional `tabs` (up to 8 names), stored in the same `reports` row (migration `0045_reports_calendar` adds the two JSONB columns, empty by default). The ANUM 360 app draws them as the "dynamic workspace" of the mockups ([redesign](design/anum360.md), report preview). A section has an `id` (unique, lowercase), an optional `title` and an optional `tab` (one of `tabs`; none means the first tab). Section types:

| `type` | Holds | Shown as |
|---|---|---|
| `metrics` | 1 to 12 metrics: `label`, numeric `value`, `prefix`/`suffix` unit, `decimals`, `change_pct`, `higher_is_better`, `caption` | Metric tiles with the change and an arrow, green when the change is good news and red when it is not (a cost going up is bad) |
| `series` | 1 to 60 ordered points (`label`, `value`) and a unit | Bars; the latest is selected and its value printed; tapping a bar selects it; each bar keeps a 48 px target and many bars scroll sideways |
| `breakdown` | 1 to 12 non-negative parts | A donut with a legend that prints every share; more than six parts fold into "Other" |
| `ranked` | 1 to 100 items with `value`, `change_pct`, `detail` | Bars against the largest value with the change; the first five, then "Show all" |
| `text` | `body` up to 4,000 characters | Plain text |

Invalid layouts (a tab that does not exist, duplicate tab names or section ids, a negative breakdown part, an unknown type) are refused with 422. An older app that meets a newer section type shows "this app cannot show it yet" instead of dropping it. Producers send sections through `create_report(context, ReportCreate(..., tabs=[...], sections=[...]), kind=...)`; the Markdown body gets a "Sections" table and, when the report has no data table, the CSV export is the sections flattened to `tab, section, type, label, value, unit, change_pct, detail` (text cells that a spreadsheet would run as a formula are prefixed with `'`; numbers are left alone).

Two more routes:

| Route | Who | Does |
|---|---|---|
| `POST /api/v1/reports/{id}/files` | readers with `file:upload` (members, owners) | Saves the CSV in the workspace's files (same quota, type rules, RLS and retrieval indexing as an upload; `files.save_generated_file`) and returns the file and a short-lived download link (`files.file_link`) |
| `POST /api/v1/reports/{id}/ask` | readers with `task:create` | "Ask ANUM to analyze further": creates a task whose prompt is the question plus the report (summary, metrics, highlights, sections, the first 40 data rows) as labelled data, not instructions, capped at 8,000 characters. The task runs and asks for approvals like any other |

Both are audited (`report.saved_to_files`, `report.asked`).

Tyre reports: every new report from the tyre module (`POST /api/v1/tyre/reports` or a schedule) is also handed to this module through `create_report` (`anum_api.tyre.service.share_report`): type `tyre`, its numbers as metrics and as a metrics section (tiles in the app), its first table as the data snapshot and its notes as highlights. The tyre report keeps its own record, exports and deliveries in `tyre_reports`; a failure in the hand-off is logged and never fails the tyre report.

In the Flutter app the report screen (`ReportDetailScreen`, also at the route `/reports/<id>`; the list at `/reports`) shows the sections with `ReportSectionsView` (`lib/features/reports/`): tab chips, metric tiles (two per row on phones, four from 760 px), bar and donut charts drawn with `CustomPainter` (no chart package was added; `fl_chart` is not a dependency), the ranked list, **Save to Files and copy a link**, and the "Ask ANUM to analyze further" bar, which opens the new task. Chart colours (`ReportChartColors`) are the fixed categorical order of the dataviz reference palette, checked with its validator against the app's dark (`#111829`) and light surfaces: lightness band, chroma, colour-blind and normal-vision separation pass in both; three light-mode slots are below 3:1 contrast, so every chart prints its values as text.

## Not in this change

- A built-in marketing generator (that module does not exist yet).
- Attaching the CSV file to a shared email (the email tool sends text only).
- Sharing from the phone app: the Flutter report screen downloads and stars; sharing is on
  the web.
- A server-side PDF (see above).
- Typed sections: no agent tool fills them yet, and export to Files is CSV only (a PDF needs a PDF library on the API).
