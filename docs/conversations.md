# Conversations

The assistant chat: ask a question, watch the answer stream in, ask a follow-up that
remembers what was said. It is the main Ask experience in the web console (the
**Assistant** view, the default) and voice uses the same streamed model call.

## What it is and is not

- **Text only.** The chat assistant has no tools. It cannot approve, delete, pay, send
  messages or change permissions, so the runtime's tool mediation and approvals are never
  bypassed: anything that changes state is a task, which runs under the workspace's
  approval policy. The system prompt tells the model so, and it suggests creating a task
  when the user wants something done.
- **Private.** A conversation belongs to the user who started it in one tenant and
  workspace. Other users of the same workspace cannot list, read, ask in or delete it.
- **Bounded.** Each question goes to the model with the system prompt and the most recent
  earlier turns: at most `ANUM_CONVERSATION_HISTORY_TURNS` turns (default 12) and at most
  `ANUM_CONVERSATION_HISTORY_CHARS` characters of them (default 24000), newest kept first,
  starting at a question. Failed replies (no text) are left out and consecutive turns of
  one role are joined, so roles always alternate. A conversation holds at most 400 turns;
  then the API asks for a new conversation (`409`).

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

A turn is `{id, conversation_id, role: user|assistant, content, status: complete|cancelled|failed, created_at}`.

`retry_turn_id` asks the latest question again (the **Retry** button) without storing a
second copy of it; any other turn id answers `409`. The monthly model budget is checked
before anything is stored: when it is used up the ask answers `402`
`model_budget_exceeded` like a task run ([Model gateway](model-gateway.md#monthly-budgets)).

### Streamed reply

`ask` answers with server-sent events:

| Event | Data |
| --- | --- |
| `turn` | `{"user_turn": turn}` once the question is stored |
| `delta` | `{"text": "..."}` for each piece of the reply as the model produces it |
| `done` | `{"assistant_turn": turn}` when the reply is complete and stored |
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

## Storage

`ANUM_REPOSITORY_BACKEND=memory` keeps conversations in process memory. With PostgreSQL
they live in `conversations` and `conversation_turns` (migration `0018_conversations`),
under forced RLS whose policies check `anum.tenant_id`, `anum.workspace_id` and
`anum.user_id`, like voice sessions. Turns reference their conversation by
`(tenant_id, workspace_id, user_id, conversation_id)` with `ON DELETE CASCADE` and are
ordered by an identity column. No other role is granted anything on these tables:
nothing discovers conversations across tenants. Conversations are kept until the user
deletes them or the workspace's data is deleted; there is no retention purge yet.

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

Voice streams too: `POST /api/v1/voice/sessions/{id}/ask/stream` ([Voice](voice.md#web-voice-assistant))
and the Voice view speaks each sentence as soon as it is complete.
