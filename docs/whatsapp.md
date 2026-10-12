# WhatsApp Business inbox

ANUM connects a workspace to one WhatsApp Business number through Meta's official **WhatsApp Cloud API** (the Graph API). Customers' messages arrive in the WhatsApp inbox (web and Flutter), are sorted by topic, get a suggested reply, and every reply goes out as a governed tool call with an approval. Nothing here uses unofficial WhatsApp Web automation.

Code: `services/api/anum_api/whatsapp.py` (routes, webhook, tools), `whatsapp_graph.py` (Meta calls), `whatsapp_store.py` and `db/whatsapp_repository.py` (storage), `whatsapp_triage.py` (sorting and drafts), migration `0033_whatsapp`; web `apps/web/src/WhatsAppView.tsx`; Flutter `apps/mobile/lib/features/whatsapp/`.

## Owner setup

Values are entered once, by a workspace owner, in **WhatsApp > Setup** in the web app. Never paste them into a chat. The API stores the access token and the app secret encrypted with `ANUM_SECRETS_KEY` and the verify token only as a SHA-256 hash; none of them is ever shown again.

1. **Meta Business portfolio.** In [Meta Business Suite](https://business.facebook.com) create (or use) the business portfolio of the company and verify the business (Business settings > Security center). Verification raises the messaging limits.
2. **App.** In [Meta for Developers](https://developers.facebook.com/apps) create an app of type *Business*, add the **WhatsApp** product and attach it to the business portfolio. This creates a WhatsApp Business Account (WABA).
3. **Phone number.** In the app, WhatsApp > API Setup, add the business phone number and verify it by SMS or call. Copy the **Phone number ID** and the **WhatsApp Business Account ID** shown there. See [Your number today](#your-number-today) before adding a number that is on the WhatsApp Business phone app.
4. **Permanent token.** In Business settings > Users > System users, create a system user (Admin), assign it the app and the WhatsApp account with full control, and generate a token with the permissions `whatsapp_business_messaging` and `whatsapp_business_management` and no expiry. This is the **Permanent access token**. Temporary tokens from API Setup expire after 24 hours and are not suitable.
5. **App secret.** In the app, App settings > Basic, reveal and copy the **App secret**. ANUM uses it to verify that webhook calls really come from Meta.
6. **Connect in ANUM.** In WhatsApp > Setup enter the phone number ID, the WABA ID, the token, the app secret, a **verify token** (press *Generate* for a random one) and your own WhatsApp number for the test message. *Connect* saves and immediately asks Meta for the number's profile: the status turns *Connected* with the business name, or shows Meta's reason (for example an expired token) and how to fix it.
7. **Webhook.** In the app, WhatsApp > Configuration > Webhook, set the **Callback URL** to the URL shown in Setup (`https://<API host>/api/v1/whatsapp/webhook`) and the **Verify token** to the same value as in ANUM, then *Verify and save* (Meta calls the URL; ANUM answers the challenge only for a verify token some workspace saved). Under *Webhook fields* subscribe to **messages** (and to **smb_message_echoes** if you use coexistence).
8. **Test.** *Send a test to my number* sends Meta's sample template `hello_world` to your own number. Then write to the business number from another phone: the message appears in the inbox within seconds and *Last message from Meta* updates.
9. **Templates.** Create message templates in WhatsApp Manager (Account tools > Message templates), for example an order-status template `order_update` with body `Hi {{1}}, your order {{2}} is on its way.` Meta reviews them; approved ones appear under WhatsApp > Templates. Tick *Send without asking* on low-risk ones (see [Sending](#sending)).

To change the token or app secret later, enter only the new value; empty fields keep the saved ones. *Disconnect* forgets the number and its credentials and stops receiving messages; past conversations stay readable.

### Your number today

The business number is on the WhatsApp Business **phone app** today. There are two ways to bring it to the Cloud API:

- **Migrate the number to the Cloud API.** The number leaves the phone app: delete the WhatsApp account in the phone app (Settings > Account > Delete my account), then add the number in step 3. After the move, messages are only read and answered in ANUM (and any other Cloud API client), not on the phone; chat history on the phone is not transferred. Use a new number instead if the phone app must keep working unchanged.
- **Coexistence (keep using the phone app).** Meta's *WhatsApp Business app coexistence* lets the same number stay on the WhatsApp Business app and be connected to the Cloud API. It is onboarded through Meta's Embedded Signup flow with the option to connect an existing WhatsApp Business app number (scan a QR code from the phone app), is available in most but not all countries, and requires a recent version of the app. Messages sent from the phone arrive in ANUM through the `smb_message_echoes` webhook field and are shown as "Sent from the WhatsApp Business app". Some features behave differently under coexistence (for example throughput limits, and some app features such as broadcast lists are turned off); check Meta's current coexistence documentation before choosing it.

Without coexistence a number cannot be on the phone app and the Cloud API at the same time.

## Webhook

`GET /api/v1/whatsapp/webhook` answers Meta's subscription check: with `hub.mode=subscribe`, a `hub.verify_token` that some workspace saved (compared by SHA-256) and a short `hub.challenge`, it returns the challenge as text; otherwise 403 or 400.

`POST /api/v1/whatsapp/webhook` is public on the API host and needs no ANUM credentials. It:

1. refuses bodies over `ANUM_WHATSAPP_WEBHOOK_MAX_BODY_BYTES` (256 KiB; Caddy caps the path at 256 KB as well) and requests without a well-formed `X-Hub-Signature-256`;
2. reads the phone number ids from `entry[].changes[].value.metadata` and applies a per-number rate limit (`ANUM_WHATSAPP_WEBHOOK_RATE_PER_SECOND`, default 20, burst `ANUM_WHATSAPP_WEBHOOK_BURST` 200; 429 with `Retry-After`, Meta retries);
3. finds the owning workspace of each number as `anum_maintenance` ([Multi-tenancy](multi-tenancy.md#maintenance-role)): that role may read only `tenant_id`, `workspace_id`, `phone_number_id` and `verify_token_hash` of `whatsapp_accounts`;
4. inside that workspace's RLS context, decrypts the app secret and checks the HMAC-SHA256 of the raw body in constant time. Only changes of numbers whose signature matches are stored. Nothing matching: 401 (bad signatures are rate limited per address);
5. stores contacts, conversations and messages **idempotently**: a message id (`wamid`) is unique per workspace (`uq_whatsapp_messages_wamid`), so a retried or replayed delivery stores nothing new; delivery statuses only move forward (sent < delivered < read; failed is recorded with Meta's reason); messages older than `ANUM_WHATSAPP_REPLAY_WINDOW_HOURS` (168) are dropped; and
6. answers 200 quickly; model triage runs afterwards in the background.

A number not connected in any workspace cannot be verified and gets 401, so connect it in ANUM before subscribing the webhook. Errors Meta reports in a delivery are shown as the account's *last error*.

Deployment: the API host already routes every path to the API (Caddy on Oracle, the Ingress on Kubernetes, Render's web service), so the webhook needs no extra route; `infra/oracle/Caddyfile` only adds the body cap. The API login must be a member of `anum_maintenance` (`WITH INHERIT FALSE, SET TRUE`, as `infra/helm/bootstrap-database.sql` and the Render bootstrap already grant). Egress to `graph.facebook.com` and Meta's media hosts on 443 must be allowed (the chart's `appEgress` allows 443).

## Storage

Migration `0033_whatsapp` adds four forced-RLS tables scoped to tenant and workspace:

| Table | Holds |
|---|---|
| `whatsapp_accounts` | one row per workspace: ids, display number and verified name, encrypted token and app secret, verify token hash, the owner's test number, the auto-send template list, the triage switch, status and last error |
| `whatsapp_contacts` | customers by WhatsApp id and profile name |
| `whatsapp_conversations` | one per customer: last message preview and time, last inbound time (the 24-hour window), unread count, category, summary and suggestion |
| `whatsapp_messages` | every message in and out: kind, text, media metadata (id, type, file name, caption), template use, delivery status and error, the approval and task of a sent reply |

`phone_number_id` is unique across tenants so a delivery maps to exactly one workspace. Media bytes are never stored: `GET /api/v1/whatsapp/messages/{id}/media` fetches the file from Meta on demand, only from `https` URLs on Meta's media hosts, without redirects, up to `ANUM_WHATSAPP_MEDIA_MAX_BYTES` (16 MiB; 413 above), and serves it `no-store` with `nosniff` (only images, audio and video inline). Re-encrypt stored tokens after rotating `ANUM_SECRETS_KEY` by re-saving them in Setup (`rotate_secrets` covers model keys only today).

## Who can do what

| | Owner | Member | Viewer |
|---|---|---|---|
| Read conversations, templates, media (`whatsapp:read`) | yes | yes | no |
| Draft and ask to send (`whatsapp:reply`) | yes | yes | no |
| Connect, check, test, disconnect, auto-send list (the whatsapp approved templates) (`whatsapp:manage`) | yes | no | no |
| Approve a send (`approval:decide`) | yes | no | no |

Customer conversations are personal data, so viewers do not see them; the view tells them who can.

## The 24-hour window

WhatsApp allows free-form replies only within 24 hours of the customer's last message (the customer-service window); after that only Meta-approved **templates** can be sent. Each conversation carries `window_open` and `window_closes_at`. The inbox shows the time left and, when closed, switches the composer to templates and explains why. The send request refuses a free-form reply outside the window with the reason ("The customer last wrote 30 hours ago. WhatsApp allows free-form replies only within 24 hours ... Send an approved message template instead"), and the send tool checks the window again when the approved call runs; Meta's own refusal (code 131047) is explained the same way.

## Triage

Every inbound message is sorted by keyword rules on arrival (English and Arabic, plus tyre sizes such as `205/55R16`): **order question**, **price or stock**, **complaint**, **supplier**, or **other**. When the workspace has a real model (Settings > Model) and triage is on (owner switch in Setup, deployment switch `ANUM_WHATSAPP_TRIAGE_ON_ARRIVAL`), the model then reads the latest messages and writes a category, a one-line summary and a suggested reply. *Suggest a reply* does the same on demand; without a model it uses a rule-based reply in the customer's language with [placeholders].

Customer text is **untrusted data** (threat model G3/G5): each message is wrapped by `label_untrusted` (source `whatsapp`, origin the message id) and the prompt carries `UNTRUSTED_DATA_RULES`. The model is told never to invent prices, stock, order numbers or dates but to leave [placeholders]. Its output is only a draft labelled with where it came from ("AI draft by anthropic / claude-haiku-5-5 from 3 customer messages, read as untrusted text" or "Rule-based draft"); it never sends, never picks a tool and never changes a setting. Model calls go through the workspace's budgeted gateway (fast route); a used-up budget leaves the rule category and returns 402 to *Suggest a reply*.

## Routing to department assistants

After a delivery is stored, new inbound texts are offered to department assistants that opted into WhatsApp; a keyword match becomes a work item for that assistant, once per open conversation. Best effort in a background task, never part of the webhook answer ([Team work](team-work.md#inbound-routing)).

## Sending

`POST /api/v1/whatsapp/conversations/{id}/send` with `{"text": ...}` or `{"template": {"name", "language", "parameters"}}` creates a task whose single tool call goes through `ToolPolicy`:

- **Free-form text** is `send_whatsapp`, risk **high**: always paused for approval. The approval shows the recipient, the text, the target `graph.facebook.com` and the payload hash; an owner approves in the inbox (web or phone) or under Approvals with that hash (`POST /api/v1/approvals/{id}/approve {"payload_hash": ...}`), and only then is it sent. Two-person and organization approval rules apply as for every approval.
- **A template** is `send_whatsapp_template`, risk **medium**, a `reply` in the whatsapp category of the [autonomy policy](autonomy.md#connector-facts-who-wrote-first-and-whose-templates). The owner's **auto-send list** (WhatsApp > Templates, *Send without asking*: meant for low-risk replies such as order status) *is* the whatsapp category's list of approved templates; there is no second list on the Autonomy page to keep in sync. A template is sent at once, and announced in Activity ("ANUM acted on its own"), only when all of these hold:
  - it is on the auto-send list (no autonomy rule can add a template beside it; an "Always ask for this" rule makes a listed one ask);
  - the customer has written to the business in this conversation (first contact always asks);
  - neither the workspace's approval policy (medium-risk actions need approval, two-person rule) nor an organization approval rule or policy pack asks;
  - the workspace's autonomy level for WhatsApp is not *Always ask* (organization ceilings apply), it is not inside quiet hours, the daily cap is not reached and no recipient is blocked (a blocked number is refused, even with an approval).

  Anything else waits for approval like free-form text, and the approval says why. Before anything is queued the API checks that the template exists in that language, is approved and gets the right number of parameters; the tool checks again when it runs, and the runtime evaluates the policy again just before sending.

A rejected or expired approval marks the message *not sent*. Sent messages then follow Meta's delivery statuses. Each request and send is audited (`whatsapp.send.requested`, `tool.call` with counts only, `whatsapp.account.saved`, `whatsapp.test.sent`, ...); message text is not copied into audit metadata.

## Agent tools

Registered in the API's and the worker's tool registry:

| Tool | Risk | Does |
|---|---|---|
| `list_whatsapp_conversations` | low | conversations with filter (`all`, `unread`, `needs_reply`, `awaiting_approval`) and search |
| `read_whatsapp` | low | one conversation's recent messages and whether the window is open |
| `draft_whatsapp_reply` | low | a suggested reply (budgeted), sends nothing |
| `send_whatsapp` | high | free-form reply inside the window, after approval |
| `send_whatsapp_template` | medium | approved template, as above |

Each declares its autonomy category (`whatsapp`) and kind (`read`, `read`, `draft`, `reply`, `reply`); none is undoable, since a sent message cannot be taken back.

All need the `owner` or `member` role. A task that mentions WhatsApp ("Which WhatsApp customers are waiting for a reply?") selects the `anum.whatsapp-inbox` skill and lists the conversations; replies are drafted and sent from the inbox, where the person sees what goes out. Tool output (customer text) is untrusted data and reaches prompts only through `tool_output_prompt_block`.

## API

| Route | Permission |
|---|---|
| `GET /api/v1/whatsapp/account` | `whatsapp:read` (never returns secrets; shows the token's last four characters) |
| `PUT /api/v1/whatsapp/account` | `whatsapp:manage` (omitted secrets keep the saved ones; 409 when another workspace has the number) |
| `POST /api/v1/whatsapp/account/check`, `/account/test`, `DELETE /account` | `whatsapp:manage` |
| `GET /api/v1/whatsapp/templates[?refresh=true]` | `whatsapp:read` (cached 5 minutes) |
| `GET /api/v1/whatsapp/conversations?limit&before&q&filter` | `whatsapp:read` (newest activity first, keyset paging) |
| `GET /api/v1/whatsapp/conversations/{id}?limit&before` | `whatsapp:read` (a page of messages, oldest first, `has_more`) |
| `POST /api/v1/whatsapp/conversations/{id}/read` | `whatsapp:read` (in ANUM only; no read receipt is sent) |
| `POST /api/v1/whatsapp/conversations/{id}/draft` | `whatsapp:reply` |
| `POST /api/v1/whatsapp/conversations/{id}/send` | `whatsapp:reply` |
| `GET /api/v1/whatsapp/messages/{id}/media` | `whatsapp:read` |
| `GET, POST /api/v1/whatsapp/webhook` | public, signature verified |

Deployment settings (`ANUM_WHATSAPP_*`, all optional): `GRAPH_API_URL` (https, default `https://graph.facebook.com`), `GRAPH_API_VERSION` (`v23.0`), `WEBHOOK_MAX_BODY_BYTES`, `MEDIA_MAX_BYTES`, `REPLAY_WINDOW_HOURS`, `WEBHOOK_RATE_PER_SECOND`, `WEBHOOK_BURST`, `TEMPLATE_CACHE_SECONDS`, `TRIAGE_ON_ARRIVAL`. They are in the Helm chart's `config` and in `render.yaml`.

## Clients

- **Web** (`#whatsapp`): Inbox (list with search, filters and paging; thread with the window indicator, the suggested reply and its provenance, message bubbles that keep their own text direction, media download, inline approval cards with the payload hash, text and template composers), Templates (status, body, auto-send switch for owners) and Setup (status, callback URL to copy, the connection form, check, test, disconnect). English and Arabic, right to left. Tests: `apps/web/test/whatsapp.test.ts`, `apps/web/e2e/whatsapp.spec.ts` (including axe in both languages).
- **Flutter**: conversations and thread with approve-to-send ([Flutter mobile](mobile.md#whatsapp-inbox)). Setup and templates are on the web.

## Tests

`services/api/tests/test_whatsapp.py` runs everything against a fake Graph API (`httpx.MockTransport`): setup and secrets, the verification challenge, signature checks, idempotent and stale deliveries, rate and size limits, media limits and host allowlist, rules and model triage with untrusted labels, the window, approve-to-send, templates and the auto-send list as the autonomy policy's approved templates (first contact, quiet hours, caps, blocked numbers, the medium-risk switch and the category level still ask or block), rejections and Meta errors, the agent tools and the planner. `tests/test_postgres_whatsapp.py` checks encryption at rest, persistence, tenant and workspace isolation, the cross-tenant phone number rule, webhook routing through `anum_maintenance` and that role's narrow grants. No test calls Meta.

## Not done yet

- Sending media (images, documents) and interactive messages: the inbox receives them, replies are text or templates.
- Template parameters for headers and buttons: only body parameters are sent.
- Template creation from ANUM: templates are created and approved in WhatsApp Manager.
- Read receipts and typing indicators are not sent to customers.
- `python -m anum_api.rotate_secrets` does not yet re-encrypt WhatsApp tokens; re-save them after a key rotation.
- Template sending and media download from the Flutter app.
