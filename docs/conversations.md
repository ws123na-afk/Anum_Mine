# Conversations

The assistant chat: ask a question, watch the answer stream in, ask a follow-up that
remembers what was said. It is the main Ask experience in the web console (the
**Assistant** view, the default) and voice uses the same streamed model call.

## What it is and is not

- **Governed tool use, never a tool call from the chat model.** Plain conversation stays
  text only: the chat model has no tools and cannot approve, delete, pay, send messages or
  change permissions. A question that needs workspace data or an action ("ANUM, any urgent
  customer emails?", "what's waiting on WhatsApp?", "send the report to the bank") is turned
  into a **task** and run through the runtime exactly like `POST /api/v1/tasks/{id}/run`
  (planner, skills, `ToolPolicy`, governance, autonomy, approvals, audit); the outcome
  streams back as a card ([Tools in chat](#tools-in-chat)).
- **Store and tyre data.** Sales, order and stock questions and tyre questions are tools
  in chat like the rest: they run as tasks and come back as `commerce` or `tyre` cards
  (table below). A tyre question the skill does not pick up (no tyre word the skill knows)
  is still answered from the read-only tyre grounding: ANUM (not the model) picks the
  tool from the user's words, `ToolPolicy` decides it, the call is audited as `tool.call`,
  and the result reaches the model as a labeled, untrusted data block
  ([Tyre planning](tyre-planning.md#assistant)).
- **Private.** A conversation belongs to the user who started it in one tenant and
  workspace. Other users of the same workspace cannot list, read, ask in or delete it.
- **Bounded.** Each question goes to the model with the system prompt and the most recent
  earlier turns: at most `ANUM_CONVERSATION_HISTORY_TURNS` turns (default 12) and at most
  `ANUM_CONVERSATION_HISTORY_CHARS` characters of them (default 24000), newest kept first,
  starting at a question. Failed replies (no text) are left out and consecutive turns of
  one role are joined, so roles always alternate. A conversation holds at most 400 turns;
  then the API asks for a new conversation (`409`).
- **Remembers.** Durable facts the user states ("my name is Sam", "I prefer short
  answers") are remembered as private memories after the reply, and the nearest memories
  are recalled into later questions in any conversation ([Long-term memory](#long-term-memory)).

## API

All under `/api/v1/conversations`, with the usual tenant context. Listing and reading need
`task:read`; creating, asking and deleting need `task:create` (as voice sessions).

| Method and path | Result |
| --- | --- |
| `POST /` `{"title"?}` | `201` conversation (title is set from the first question when empty) |
| `GET /` | the caller's 50 most recently active conversations |
| `GET /{id}` | the conversation with all its turns, oldest first |
| `DELETE /{id}` | `204`; its turns go with it |
| `POST /{id}/ask` `{"content"}` or `{"retry_turn_id"}` | `200` `text/event-stream` (below) |

A turn is `{id, conversation_id, role: user|assistant, content, status: complete|cancelled|failed, created_at, card}`; `card` is null for plain conversation.

`retry_turn_id` asks the latest question again (the **Retry** button) without storing a
second copy of it; any other turn id answers `409`. The monthly model budget is checked
before anything is stored: when it is used up the ask answers `402`
`model_budget_exceeded` like a task run ([Model gateway](model-gateway.md#monthly-budgets)).

### Streamed reply

`ask` answers with server-sent events:

| Event | Data |
| --- | --- |
| `turn` | `{"user_turn": turn, "memories_used": [memory id, ...]}` once the question is stored; the ids of the memories recalled into this question (empty when none) |
| `tool` | `{"card": card}` when the question ran as a task: first `running`, then the settled card ([Tools in chat](#tools-in-chat)) |
| `delta` | `{"text": "..."}` for each piece of the reply as the model produces it |
| `done` | `{"assistant_turn": turn, "report": card or null}` when the reply is complete and stored; `report` when the answer was a structured analysis ([Reports](reports.md#chat-answer-cards)) |
| `error` | `{"code", "message", "assistant_turn"}`: `model_unavailable` (generic message; only the error class is logged) or `model_budget_exceeded` |

The body never reads through the request's repository session, which is closed once
the response starts: the conversation, the history and the question are read and written
before streaming, and the reply is stored in its own unit of work at the end.

**Cancellation.** When the client disconnects (the web **Stop** button aborts the
request) Starlette cancels the body: the gateway closes the provider connection, which
stops generation and billing, and the partial reply is stored with status `cancelled`.
Usage up to that point is still metered (estimated when the provider reported none).

**Speed.** The reply streams token by token from the provider
([streaming](model-gateway.md#streaming)), every call is capped at
`ANUM_MODEL_MAX_OUTPUT_TOKENS` (default 1024), a provider that sends no first token within
`ANUM_MODEL_FIRST_TOKEN_TIMEOUT_SECONDS` (default 20) is retried or given up on, and the
whole stream ends after `ANUM_MODEL_STREAM_TIMEOUT_SECONDS` (default 120). Nothing is
retried once text has been shown, so a slow model can no longer hang the UI for three
minutes of hidden retries.

A structured answer ends with an `anum-report` block that the server removes from the stored turn and stores as a private report; `GET /api/v1/conversations/{id}` returns those cards in `reports` ([Reports](reports.md#chat-answer-cards)).

## Tools in chat

`anum_api/chat_tools.py`. Which questions become tasks is decided from the **user's own
words only**, with the runtime's own skill selection (`SkillRegistry.select`, Arabic
triggers included) plus a few file words; nothing in an email, a WhatsApp message, a web
page or a file can select a tool, change its arguments or approve anything (threat model
G3/G5).

| The question mentions | Task's planned tool | Card |
| --- | --- | --- |
| WhatsApp (`whatsapp`, `واتساب`) | `list_whatsapp_conversations` (`needs_reply` when it asks what is waiting) | results: conversations, links to WhatsApp |
| mail (`email`, `inbox`, `gmail`, `بريد`, `ايميل` …) | `search_email` (filters from the words, [Email](email.md#tools-for-tasks-chat-and-voice)) | results: messages, links to Inbox |
| store sales, orders or stock (`sales`, `orders`, `stock`, `amazon`, `noon`, `مبيعات`, `طلبات`, `مخزون` …), when `commerce.tools.plan_call` reads a request from the words | `commerce.sales_summary`, `commerce.list_orders`, `commerce.get_order`, `commerce.low_stock`; a price or stock change names channel, SKU and value and plans `commerce.<channel>.update_price` or `update_stock` | results: orders or low-stock listings, links to Commerce; or approval for a change |
| an action (`send`, `delete`, `pay`, `publish` …) | `external.action` (high risk) | approval |
| tyres (`tyre`, `tread`, `إطارات`, `كفرات` …) | `tyre.tools.choose_call`: `tyre_fleet_summary`, `tyres_due`, `purchase_plan` or `tyre_alerts` | results: tyres due, alerts (critical ones marked urgent) or purchase-plan lines, links to Tyre planning; failed with how to connect when tyre data is not connected |
| the web (`search`, `web`, `news`, `ابحث` …; only where SearXNG is configured) | `web_search` | results: links that open in a new tab |
| files (`file`, `document`, `contract`, `pdf`, `ملف`, `مستند` …) | `anum.respond` with workspace retrieval | results: the answer, the files and notes used |

Anything else is plain conversation and streams as before. How it runs:

1. The question is stored, then a task (title from the question, `created_by` the asker)
   is created in its own committed unit of work with its `task.created` event, so it is
   in Tasks whatever happens next. A `tool` event with a `running` card follows.
2. The task runs in a second unit of work through `AgentRuntime` with the API's tool
   registry, retriever and, with `ANUM_RUNTIME_BACKEND=temporal`, the durable dispatcher.
   Inline runs are bounded (150 s). Queued runs are followed for 45 s; after that the card
   stays `running` and is refreshed when the conversation is opened again.
3. The settled card streams as a second `tool` event. For results from mail, WhatsApp and
   the web the fast model writes a one to three sentence answer above the card, streamed as
   `delta`s: its prompt holds the untrusted-data rules, then the tool output serialized and
   wrapped by `tool_output_prompt_block` (provenance label, random-nonce markers, forged
   markers defanged), and the question is the only user message. If that call fails, the
   tool's own summary is the answer. Approval, failed and running cards get a fixed line.
4. `done` carries the assistant turn with its card; the card is stored on the turn
   (`conversation_turns.card`, migration `0047_chat_cards`, nullable JSONB, covered by the
   table's user-scoped forced RLS).

**Card** `{state, category, task_id, run_id, tool, summary, items, total, approval, error_code}`:
`state` is `running`, `results`, `approval`, `failed`, `cancelled` or `denied`; `category`
is `email`, `whatsapp`, `commerce`, `tyre`, `web`, `files` or `action`. `items` (at most 10 of `total`) are
`{kind: email|whatsapp|web|file|memory|order|listing|tyre, id, title, subtitle, detail, at, badges, url}`;
`url` is set for http(s) web results only. `approval` is
`{id, action, risk_level, status, reason, payload_hash, arguments, target, expires_at, can_decide}`
(`can_decide`: the asker holds `approval:decide`, i.e. is an owner).

- **Approvals.** Side effects pause exactly as in tasks. The card shows the action, risk,
  target, arguments (secrets redacted) and the **payload hash**; Approve and Reject call the
  ordinary `POST /api/v1/approvals/{id}/approve|reject` with that hash, so the two-person
  rule, approver chains, expiry and target checks all apply. Members see that a workspace
  owner decides, with a way to Approvals. `GET /conversations/{id}` refreshes cards that
  were `running` or `approval` from their task (approved, rejected, expired, finished), in
  a short read-only session in the caller's RLS scope.
- **Failures.** A model or service failure while planning leaves the task `created` (it
  is saved; run it from Tasks) and the card `failed` with `task_failed`; a used-up budget
  gives `model_budget_exceeded`; an unreachable durable runtime `runtime_unavailable`; a
  timeout `task_timeout`. Messages never carry provider details. A caller without
  `task:run` gets a `denied` card and no task. **Stop** while the task runs rolls the run
  back (the task stays `created`) and stores a `cancelled` card.
- **Memory and learning.** Tool turns are never mined for memories (the reply quotes
  mailbox, WhatsApp or web data, not the user). The user's learned chat style and
  preferences still shape the written answer; drafting tasks get them in the runtime as
  for any task ([What ANUM learned](learning.md)).
- **Clients.** Web (`ChatCardView.tsx`) and Flutter (`lib/features/chat/chat_card_view.dart`)
  render every state in English and Arabic; only a results card shows the written answer,
  the other cards speak for themselves. Item links open Inbox (that message), WhatsApp
  (that conversation), Commerce, Tyre planning, Files, Memory, the task or Approvals; on
  the phone app they open Inbox, WhatsApp, Tasks, Approvals or Resources, and store and
  tyre rows are shown without a link (the phone has no single Commerce or Tyre page to
  open from the Assistant yet). The one card format in chat is this one; the saved-report
  card (`anum-report`) is a separate thing. Mail and web text is rendered as text.
- **Tests.** `services/api/tests/test_chat_tools.py` (routing, results, WhatsApp, files,
  approval through the approvals route, rejection, member, failures, budget, Stop, and
  prompt injection from email and WhatsApp text staying data),
  `services/api/tests/test_commerce.py` and `test_tyre_api.py` (store and tyre cards);
  `apps/web/test/chatCards.test.ts`, `apps/web/e2e/assistant-chat-tools.spec.ts` (cards,
  Approve with the hash, members, Arabic, five viewport sizes);
  `apps/mobile/test/chat/chat_cards_test.dart` (models, every state, Approve, 403, Arabic,
  320 px at 200 percent text to 1440 px, tap targets).

## Long-term memory

Details, privacy rules and settings: [Memory: from conversations](memory.md#from-conversations).

- **Recall, before the reply.** The question is embedded (budgeted) and the nearest
  memories the user may read (their own private memories and the workspace's shared
  notes, never files or another user's) go into the system message as labeled,
  untrusted blocks, at most `ANUM_CONVERSATION_MEMORY_RECALL_K` (4) memories and
  `ANUM_CONVERSATION_MEMORY_RECALL_CHARS` (2000) characters. Recall that fails, finds
  the budget used up or takes longer than `ANUM_CONVERSATION_MEMORY_RECALL_TIMEOUT_SECONDS`
  (3) is skipped; the question is answered without it.
- **Remember, after the reply.** Once a reply is `complete` and stored, and after the
  whole stream has been sent, one small non-streaming model call
  (`ANUM_CONVERSATION_MEMORY_MAX_TOKENS`, 256) extracts the facts, which are added to or
  update (when similar enough) the user's memories with `source_type = conversation` and
  `source_id` = this conversation. It counts against the monthly model budget. A
  `cancelled` or `failed` reply is never mined. Nothing about this pass can fail or delay
  the chat: errors are logged with their class name only.
- **Off switches.** `ANUM_CONVERSATION_MEMORY_ENABLED=false` turns recall and remembering
  off for the server (default `true`); a tenant turns remembering off with memory
  governance (`allowed_source_types` without `conversation`). Viewers cannot ask, and a
  caller without memory create permission is never remembered from.
- Deleting a conversation keeps the memories drawn from it; the user deletes them in the
  Memory view.

## Storage

`ANUM_REPOSITORY_BACKEND=memory` keeps conversations in process memory. With PostgreSQL
they live in `conversations` and `conversation_turns` (migration `0018_conversations`),
under forced RLS whose policies check `anum.tenant_id`, `anum.workspace_id` and
`anum.user_id`, like voice sessions. Turns reference their conversation by
`(tenant_id, workspace_id, user_id, conversation_id)` with `ON DELETE CASCADE` and are
ordered by an identity column. A turn's task card is the nullable JSONB `card` column (migration `0047_chat_cards`, expand only). Only the retention purge looks across tenants, through
the discovery-only maintenance role (below); no other role is granted anything on these
tables.

## Retention

A conversation is deleted with all its turns once it has had no activity (its
`updated_at`: creation or latest turn) for `ANUM_CONVERSATION_RETENTION_DAYS` days
(default 30, the same as voice transcripts; `0` keeps conversations until their user
deletes them or the workspace's data is deleted; at most 3650). Any new turn resets the
clock, so a conversation in use is never purged. A user can also download every
conversation or delete all of them at once from Settings → Privacy ([Privacy](privacy.md)).

`python -m anum_api.conversation_retention [--dry-run]` runs the purge; the daily
retention job `python -m anum_api.voice_retention` (the chart's `voiceRetention`
CronJob, the Render cron job and the Oracle `retention` service) runs it too and reports
it under `conversations` ([Runbooks](runbooks.md#voice-transcript-retention)). It follows
the [maintenance pattern](multi-tenancy.md#maintenance-role):

- **Discover.** Migration `0019_conversation_retention` grants `anum_maintenance` the
  `id`, `tenant_id`, `workspace_id`, `user_id` and `updated_at` columns of
  `conversations`, under a select-only policy that admits only conversations idle for at
  least a day. It never sees a title or any turn, cannot write, and never sees a
  conversation active within the last day whatever retention is configured. Discovery
  pages by `(updated_at, id)`, so a dry run walks every batch.
- **Act.** Each user's idle conversations are deleted in their own transaction as the
  application role, with that tenant, workspace and user as RLS context; the idle check
  is repeated there, so a turn added after discovery keeps the conversation. Turns go
  with their conversation (`ON DELETE CASCADE`).

Output: the retention in days and counts of conversations and turns, never text. The
in-memory backend keeps nothing across restarts and has no purge; the command refuses
it. The chart refuses a `config.ANUM_CONVERSATION_RETENTION_DAYS` that is not a whole
number from 0 to 3650, as the API would at startup.

## Web

The **Assistant** view (default view, first in the navigation) is a chat panel:

- the conversation list with **New conversation**; on narrow screens the list collapses
  above the chat;
- the reply appears as it streams, rendered by the same safe renderer as task answers
  (React text nodes only, so model output can never inject markup);
- **Stop** aborts the request (`AbortController`); the partial reply stays, marked
  stopped; **Retry** asks the latest question again with `retry_turn_id`;
- the list scrolls with the reply only while the reader is at the bottom: scrolling up
  to read stops the auto-scroll, and a **Jump to latest** button brings it back;
- `Enter` sends, `Shift+Enter` adds a line; a `402` shows the budget message with a way
  to Budgets.

States: loading the list and the messages is announced; an empty workspace explains what
the assistant does and offers three starter questions; a list that fails to load says so
with **Try again**; a failed reply says what failed and offers **Retry**; a `403` explains
that a workspace owner or admin must give the member role; a question the server never
stored is marked "Not sent"; a stopped reply is marked "Stopped" (with "before any
answer" when nothing had arrived); a long history opens scrolled to the newest message.
The view loads on first visit like the other views and uses the shared theme tokens only.

## Mobile (Flutter)

The **Assistant** is the first destination of the Flutter app (`apps/mobile/lib/features/chat/`, route `/assistant`), with the same behaviour and states as the web view:

- **Client.** `HttpEventStreamClient` (`lib/src/infrastructure/http_event_stream.dart`) posts with the session's bearer token and `x-workspace-id`, like every other call, and parses the `text/event-stream` body incrementally (`SseParser` in `lib/data/sse.dart`: any chunking, CRLF, comments, multi-line data, UTF-8 split across chunks). Each request has its own `http` client; **Stop** cancels the subscription and closes that client, which closes the connection, so the API stores the partial reply as `cancelled` and stops the model. No response headers within 30 s, or 90 s without data while streaming, fail with a timeout message instead of hanging; an unreachable server reads as offline; a non-2xx answer is the API's error with its code (`402 model_budget_exceeded`, `403`).
- **Transcript rules** (`chat_models.dart`) mirror `apps/web/src/lib/chat.ts`: the question shows at once, the reply fills as `delta`s arrive, `done`/`error` settle it, a request that ends without either is marked stopped or failed, a question the server never stored is marked **Not sent**, and **Retry** (only when the last reply failed or was stopped, and its question was stored) sends `retry_turn_id`.
- **Screen.** At 720 px and wider the conversation list (newest first) sits beside the chat; on phones the chat fills the screen and the **Conversations** button opens the list in its place. System Back closes that list instead of leaving the workspace (the workspace's `PopScope`). The list has **New**, opens a conversation on tap, and deletes after a confirmation dialog; while a reply streams, other conversations and delete are locked. The reply text is selectable plain text (no markup is interpreted).
- **States.** Loading the list and the messages; an empty conversation explains what the assistant does and offers the three starter questions; a list that fails to load says why with **Try again**; a `403` explains that a workspace owner or admin must give the member role; a `402` shows the budget message with **Open Settings** (owners raise it under Settings › Model budgets); a failed reply says what failed with **Retry**; a stopped reply is marked "Stopped" ("Stopped before any answer" when nothing arrived). Scrolling up stops following the stream and **Jump to latest** brings it back; a long history opens at the newest message. A workspace switch or sign-out clears the chat.
- **Layout.** Fits 320 px wide phones at 200 percent text (on short screens the header keeps one line and notices take at most a third of the height, scrolling inside), RTL, and tablets; bottom navigation labels that cannot fit their slot at large text are hidden instead of clipped, keeping their tooltips and spoken labels.
- **Tests.** `test/chat/sse_test.dart` (parser, headers, cancel, refusal, timeout, offline), `chat_models_test.dart` (state transitions), `chat_controller_test.dart` (create on first ask, stream, Stop, Retry, 402, 403, delete, workspace switch) and `chat_screen_test.dart` / `assistant_navigation_test.dart` (every state, Back, sizes from 320x568 to 1440x900, 200 percent text, RTL).

Voice streams too: `POST /api/v1/voice/sessions/{id}/ask/stream` ([Voice](voice.md#web-voice-assistant))
and the Voice view (web and Flutter) speaks each sentence as soon as it is complete.
