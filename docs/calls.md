# Phone calls

Phone calls give a workspace a shared phone book and call log, a dialpad on the web and on the phone, notes with an AI summary and a follow-up task, and, once a workspace owner connects a voice provider, calls placed from the browser, recordings, and AI calls that an owner approves one by one.

This page is about placing and logging phone calls. Summaries of meeting recordings and Zoom or Teams transcripts are [Calls & Meetings](meetings.md); in-app calls between ANUM users are a separate piece of work that logs into the same call log with `kind: "in_app"` (see [In-app calls](#in-app-calls)).

It comes in three parts:

| Part | What it needs | State without it |
| --- | --- | --- |
| 1. Free calling and the call log | Nothing. The phone or the computer places the call with a `tel:` link. | Always on. |
| 2. Calls through a voice provider | A Twilio account with a voice number, its credentials stored by an owner under **Calls > Provider**, and `ANUM_CALLS_PUBLIC_BASE_URL` set by the operator. | Off: the Provider tab says what is missing, browser calls and recordings are not offered. |
| 3. AI calls | Part 2, a configured model, AI calls turned on by an owner, and a contact with recorded consent. | Off: the AI calls tab explains which of these is missing. |

## Free calling and the call log

**Contacts** belong to the workspace: name, phone number, company, language (English or Arabic), favourite, and where the contact came from (`manual`, or `device` for one picked from the phone's address book). Search runs over name, company and number; lists are paged (25 a page, at most 100). Numbers are stored as typed and also normalised (digits and one leading `+`, Arabic and Persian digits converted) so the same number is found however it was written.

**The dialpad** (web and phone) takes 0 to 9, `*` and `#`; `+` only as the first character; a long press on 0 types `+`; pasting a number keeps only what can be dialled (`+971 (50) 123-4567` becomes `+971501234567`); delete removes the last character and a long press on delete clears the number. When the number belongs to a contact, the contact's name is shown. **Call** opens a `tel:` link: on a phone it opens the phone's own dialer; on a computer it opens whatever handles phone links (a softphone, a paired phone). The call itself is free and goes through the phone's carrier, not through ANUM.

**Logging the call.** When the person comes back to ANUM after dialling, a sheet asks how the call went: direction (outgoing or incoming), outcome (answered, no answer, busy, voicemail, missed, failed, cancelled), duration (prefilled from the time away, editable as `2:05`, `2m` or seconds) and notes. On the web the same sheet opens from the call button and from **Log a call** on the Recents tab. Nothing is logged unless the person saves it.

**The log** (Recents) lists calls newest first, with search over contact, number and notes, a filter by outcome, and paging. A call's page shows who, when, how long, notes, and, for provider calls, the recording and the transcript.

**AI summary and follow-up.** **Summarize** sends the call's notes (or transcript) to the workspace's model through the [budgeted model gateway](model-gateway.md): the text is labelled untrusted data with provenance (`call_notes`, `call_transcript`), capped at `ANUM_CALLS_SUMMARY_MAX_CHARS`, and the answer is a short summary plus a suggested follow-up with an optional due date. Nothing acts on its own: the person edits the follow-up, saves it, and may turn it into a [task](tasks.md) with **Create task**. Without a configured model the button says so and the rest of the page works.

**Picking a contact from the phone.** On Android and iOS, **Pick from phone** opens the system contact picker (Android `ACTION_PICK` on phone numbers, iOS `CNContactPickerViewController`). ANUM never asks for permission to read the address book and never reads it: the system hands over the one contact the person picked, and only its name and the chosen number are put into the contact form, which the person then saves (or cancels).

## Permissions

| Permission | Who has it | What it allows |
| --- | --- | --- |
| `call:read` | owners and members | Read contacts, the call log, opt-outs and AI call requests. |
| `call:log` | owners and members | Add contacts, log calls, summarise, save follow-ups, prepare a browser call, ask for an AI call. |
| `call:manage` | owners | Connect the provider, record or withdraw AI call consent, lift an opt-out, change or delete anyone's contact or call. |

The person who added a contact or a call, or a workspace owner, may change or delete it; anyone else gets `403` with that explanation. Viewers and guests get `403` with "Ask a workspace owner to make you a member", which the web and phone show on the page. Every check is on the server; the clients only hide buttons the server would refuse.

Contacts, calls, opt-outs, the provider account and AI call requests are scoped to tenant and workspace with forced row level security (migration `0071_calls`). Every state-changing route is audited with ids and counts only, never numbers, notes or transcripts (`tests/audit_probe.py` walks them).

## API

All routes are under `/api/v1/calls` and take the usual tenant context.

| Route | Permission | What it does |
| --- | --- | --- |
| `GET /setup` | `call:read` | What the caller may do and what is ready: `can_log`, `can_manage`, `can_create_tasks`, `model_ready`, `provider_connected`, and a `capabilities` block with the reason something is off. |
| `GET /contacts` `?q=&favourites=&offset=&limit=` | `call:read` | Paged contacts. |
| `POST /contacts`, `GET`, `PATCH`, `DELETE /contacts/{id}` | `call:log` (change: the adder or an owner) | Contact CRUD. Changing the number withdraws the AI call consent, since it was given for the old number. |
| `PUT`, `DELETE /contacts/{id}/ai-consent` | `call:manage` | Record (method `verbal`, `written`, `web_form` or `other`, a note, and `confirmed: true`) or withdraw consent to AI calls. |
| `GET /log` `?q=&outcome=&contact_id=&offset=&limit=` | `call:read` | Paged call log. |
| `POST /log`, `GET`, `PATCH`, `DELETE /log/{id}` | `call:log` (change: the person who logged it or an owner) | Log a call (`kind: "phone"` or `"in_app"`), read it, edit outcome, duration and notes, delete it (its recording file goes with it). |
| `POST /log/{id}/summarize` | `call:log` | AI summary and suggested follow-up. `409` without a model or without notes. |
| `PUT`, `DELETE /log/{id}/follow-up`, `POST /log/{id}/follow-up/task` | `call:log` | Save or clear the follow-up; create a task from it (once). |
| `GET`, `POST /opt-outs`, `DELETE /opt-outs/{id}` | read: `call:read`; add: `call:log`; lift: `call:manage` | Numbers that asked not to be called by the AI assistant. Stored as a keyed hash plus a masked number. |
| `GET`, `PUT`, `DELETE /provider`, `POST /provider/check` | `call:manage` | The voice provider account (see below). Secrets are write-only. Members see the provider's state in `GET /setup`. |
| `POST /provider/calls` | `call:log` | Prepare a browser call: a call log entry and a 10 minute access token for the provider's browser SDK. |
| `GET`, `POST /ai-calls`, `GET /ai-calls/{id}`, `POST /ai-calls/{id}/cancel` | read: `call:read`; ask and cancel: `call:log` | AI call requests and their approval state. |

Errors say what failed and how to fix it, for example "Provider calls need a full international number, for example +971501234567." or "Recording is off in this workspace. A workspace owner turns it on under Calls > Provider."

## Provider

ANUM talks to voice providers through one interface, `anum_api.calls.provider.VoiceProvider`: check the account, place a call, hang up, download a recording, issue a browser token, and verify a webhook signature. `TwilioVoice` implements it over Twilio's REST API at the fixed host `api.twilio.com` (no workspace setting can point ANUM at another server). A new provider is a new class with the same operations.

**A generic SIP trunk** (an own Asterisk or FreeSWITCH PBX, or a carrier's SIP trunk) is not an adapter of its own: connect it to the provider's SIP trunking (Twilio Elastic SIP Trunking) and calls still go through this interface, with the same webhooks and checks.

The provider account is stored per workspace. Account SID, API key SID, TwiML app SID and the caller ID number are plain; the auth token and the API key secret are encrypted with `ANUM_SECRETS_KEY` (Fernet), never returned (the page shows only a short hint) and never logged. `python -m anum_api.rotate_secrets` re-encrypts them (`call_providers`). An owner also sets: the business name used in spoken notices, an optional greeting for incoming calls, whether recording and transcription are on, how many days recordings are kept (1 to 365, default 30), whether AI calls and the AI answering incoming calls are on, the longest AI call and its cost cap, and the provider's cost per minute (used for the cap and shown on the page). **Check connection** asks Twilio for the account and stores the result.

**Costs.** Twilio charges per minute for every call it carries and for the phone number each month; recordings and transcription are charged on top. The Provider tab says this before anything is stored. Free calling (part 1) has no such cost.

### Owner steps

1. Create a Twilio account and add billing (a trial account can only call numbers verified in Twilio).
2. Buy a phone number with voice capability (Phone Numbers > Buy a number). Check the country's rules for caller ID and recording.
3. Create an API key (Account > API keys, standard key). Keep its SID and secret.
4. Create a TwiML app (Voice > TwiML apps). The Provider tab shows its **Voice URL** once the operator has set `ANUM_CALLS_PUBLIC_BASE_URL`; paste it, method POST.
5. On the phone number, set **A call comes in** to the same Voice URL and **Call status changes** to the Status URL shown on the tab, both POST.
6. In ANUM, Calls > Provider: enter the Account SID, the auth token, the API key SID and secret, the TwiML app SID and the number in international form, the business name, and the options above. Save, then **Check connection**.

Never paste these values into chat, an issue or a file in the repository; they belong only in that form.

**The operator** sets `ANUM_CALLS_PUBLIC_BASE_URL` to the API's public https origin (for example `https://api.example.com`). Without it nothing could reach ANUM, so provider calls stay off and the tab says so.

### Webhooks

Twilio calls `/api/v1/calls/webhooks/twilio/{route_id}/...` (`voice`, `dial/{call}`, `status`, `status/{call}`, `recording/{call}`, `ai/{call}`, `turn/{call}`). `route_id` is a random id per provider account, so the URL itself names the workspace without a session. Before anything in the request is read, ANUM checks `X-Twilio-Signature` (HMAC-SHA1 of the full URL and the sorted form parameters with the account's auth token, compared in constant time, with and without the default port as Twilio's own validator does); a missing or wrong signature, an unknown route or a body over the size cap answers `403` or `413` and is logged by event name only. Webhook routes then act as the application role inside that tenant's and workspace's RLS context.

A browser call reaches the provider with the call id ANUM prepared; ANUM dials only that number, only for the person who prepared it, and only within `ANUM_CALLS_DIAL_WINDOW_SECONDS`. Status callbacks update the log entry (ringing, answered, duration, outcome).

**Incoming calls** to the provider number are logged as missed with "Nobody can take your call right now" unless the owner turned on the AI answering incoming calls (see [AI calls](#ai-calls)).

### Browser calls

With a provider connected, the web dialpad and contact rows offer **Call through ANUM** next to the `tel:` link. It loads Twilio's Voice JavaScript SDK (`@twilio/voice-sdk`, bundled; the CSP allows `wss://*.twilio.com`, `https://eventgw.twilio.com` and `https://sdk.twilio.com` media), asks the browser for the microphone, and shows the call's state, a mute button and hang up. The phone app uses the `tel:` path. The desktop app runs the same web client: its release CSP adds exactly these Twilio hosts (`TWILIO_CSP` in `apps/desktop/scripts/release-config.mjs`), and the macOS build carries the microphone usage text and the `com.apple.security.device.audio-input` entitlement; Windows uses WebView2's own microphone prompt ([Desktop](desktop.md#microphone)). A plain development build of the desktop app keeps the local CSP, so calls through ANUM need a release build there.

## Recordings

Recording is off until an owner turns it on, and even then each call needs an explicit step: before a recorded browser call the person confirms that the other side will hear the recording notice and that recording this call is lawful (`recording_consent: true`, otherwise `422`). The notice ("Please note: this call is recorded by ...", or in Arabic) is spoken to the other side before the call connects, and the confirmation (who, when, notice text) is stored on the call.

When the provider reports the recording, ANUM downloads it (only from `api.twilio.com`, at most `ANUM_CALLS_RECORDING_MAX_BYTES`) into the workspace's [files](files.md) and writes its expiry on the call. With transcription on, it is transcribed by the configured [speech-to-text](meetings.md#transcription) server and the transcript is stored on the call; summarising then uses the transcript.

The daily retention job (`python -m anum_api.voice_retention`) deletes recordings past their expiry and reports `call_recordings` counts. Expired recordings are found through the narrow `anum_maintenance` role (id, scope and expiry columns only) and each one is cleared as the application role inside its tenant's context; the call, its notes, transcript and summary stay until someone deletes the call.

## AI calls

The AI assistant may phone a contact only when all of these hold, checked when the call is asked for and again when it is placed:

- The provider is connected, AI calls are turned on by an owner and a model is configured.
- The contact has **recorded consent** to calls from the AI assistant (who recorded it, when, how). ANUM never makes AI cold calls.
- The number is not on the workspace's opt-out list or the tenant's [contact suppression list](privacy.md#contact-suppression).
- The call fits the workspace's time cap and its estimated cost (minutes times the cost per minute) fits the cost cap; both are at or under the operator's `ANUM_CALLS_AI_MAX_SECONDS` and `ANUM_CALLS_AI_MAX_COST_CENTS`.

**Every AI call needs an owner's approval.** Asking for one (AI calls tab, or the Call Agent's `telephony.ai_call` tool) creates a paused task with an [approval](approvals-and-risk.md) that shows the exact number, purpose, script, language and length; the approval is bound to their payload hash. When an owner approves, ANUM compares the approved arguments with the records as they are then: a contact whose number changed, a request whose purpose or script was edited, a withdrawn consent or a new opt-out means no call, and the request is marked failed with the reason. The tool is high risk and `always_ask`, so no autonomy setting lets it run on its own.

**During the call** the assistant first says that it is an AI assistant calling on behalf of the business, that the call is transcribed, and that the person can press 9 or say stop at any time. It then follows the approved script; each turn is one budgeted model call that only chooses the next sentence (`ANUM_CALLS_AI_MAX_TURNS`, `ANUM_CALLS_AI_TURN_MAX_TOKENS`), and it never claims to be a person. Saying "stop", "do not call" or "remove me" (in English or Arabic) or pressing 9 records an opt-out at once and ends the call politely. The provider hangs up at the time cap. The full transcript and an AI summary are logged on the call, and the request shows the outcome.

**Incoming calls answered by the AI** (an owner setting): the owner's greeting, the same AI disclosure, then the assistant takes a message (name, reason) and the call is logged with its transcript and a summary for someone to follow up. Callers on the opt-out list are not answered by the AI.

**Opt-outs** are stored as a keyed hash of tenant, workspace and number plus a masked number (`+971•••67`), from the call, from a person in ANUM, or from an incoming call. Only an owner can lift one.

## Agent tools

The [Call Agent](agents.md) template uses:

| Tool | Risk | What it does |
| --- | --- | --- |
| `call.search_log` | low, read only | Search the call log (who, when, outcome, notes, summary). |
| `call.get` | low, read only | One call: notes, summary, follow-up, transcript. |
| `call.search_contacts` | low, read only | Search contacts, with whether each consented to AI calls. |
| `telephony.ai_call` | high, always asks | Ask for an AI call as above. |

Tool output is untrusted data with provenance before it reaches a prompt, and numbers are masked in tool output.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `ANUM_CALLS_PUBLIC_BASE_URL` | empty | Public https origin the provider calls back. Empty: provider calls off. |
| `ANUM_CALLS_AI_MAX_SECONDS` | 600 | Longest AI call any workspace may allow (30 to 900). |
| `ANUM_CALLS_AI_MAX_COST_CENTS` | 500 | Most an AI call may cost in any workspace. |
| `ANUM_CALLS_AI_MAX_TURNS` | 12 | Turns of one AI conversation. |
| `ANUM_CALLS_AI_TURN_MAX_TOKENS` | 300 | Answer size of one AI turn. |
| `ANUM_CALLS_SUMMARY_MAX_TOKENS` | 800 | Answer size of a summary. |
| `ANUM_CALLS_SUMMARY_MAX_CHARS` | 20000 | Notes or transcript placed in one summary prompt. |
| `ANUM_CALLS_RECORDING_MAX_BYTES` | 50 MB | Largest recording downloaded. |
| `ANUM_CALLS_PROVIDER_TIMEOUT_SECONDS` | 15 | Timeout of provider API calls. |
| `ANUM_CALLS_DIAL_WINDOW_SECONDS` | 120 | How soon a prepared browser call must reach the provider. |

None of these is a credential.

## Privacy

"Download my data" includes the calls the person logged or placed, the AI calls they asked for and the contacts they added (`calls`). "Delete my data" deletes the calls, with their notes, summaries, transcripts and recordings, and reports `calls_logged`; contacts are the workspace's shared address book and stay, and opt-outs stay because they protect the person who opted out. Provider secrets are covered by `rotate_secrets`. See [Privacy](privacy.md).

## Clients

- **Web** (`apps/web/src/CallsView.tsx`, `CallsLists.tsx`, `CallsAdmin.tsx`, `CallsParts.tsx`): the Phone calls page with Recents, Contacts, Dial, AI calls and Provider tabs (links `#calls-recents`, `#calls-contacts`, `#calls-dial`, `#calls-ai`, `#calls-provider`), the log sheet, call and contact pages, opt-outs and the provider form with the owner steps. Loading, empty with a next action, error with retry, permission denied and paging on every list. English and Arabic (`calls.*` keys), right to left. Covered by `test/calls.test.ts`, `e2e/calls.spec.ts` (with axe in English, Arabic and light theme) and the responsive layout spec at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844.
- **Flutter** (`apps/mobile/lib/features/calls/`): Phone calls in the menu (route `/calls`) with Contacts, Dial, Recents and AI calls; the `tel:` launch through `url_launcher`, the log sheet on return to the app, the system contact picker (MethodChannel `anum/contact_picker`), the call page with summary and follow-up task. The AI calls tab (`calls_ai_tab.dart`) shows the voice provider's status (owners: `GET /provider` with the number, AI calls and recording switches, the rate and the last check; members: what `GET /setup` reports) and asks for AI calls with the web's rules: only members (`call:log`), only contacts with recorded consent who did not opt out, only while `capabilities.ai_calls` is true (otherwise `ai_problem` is shown), purpose 5 to 500 and script 10 to 4000 characters, 30 to 900 seconds with the cost at the provider's rate; every request waits for an owner's approval, done under Approvals with the payload hash, and one still waiting can be cancelled. Owners also manage the provider on the phone (`calls_admin.dart`, **Manage provider** on the AI calls tab): connect, change, **Check again** and **Disconnect** (after a confirmation), the recording, transcription, retention and AI settings within the server's limits, the owner steps and the webhook URLs to copy. The API does not keep secret entry web only, so the phone sends the auth token and API key secret the same way the web does: typed into obscured fields with suggestions, autocorrect and keyboard learning off, sent once over HTTPS in the PUT body, cleared from the form after the request (also when it fails) and never logged, shown or read back; only the hint (`…abcd`) is displayed. On a contact's page an owner records consent to AI calls (how they agreed, a note and the owner's confirmation, without which the button stays off) and anyone who logs calls withdraws it. The AI calls tab lists the opt-outs: members add a number, only owners lift one, with a reason of at least 5 characters. Members see the provider's state and who to ask; a `403` on any of these shows the server's explanation. Loading, empty with a next action, error with retry, permission denied and not configured on every part. English and Arabic (`calls_strings.dart`; the phone keeps its catalogs in Dart, not ARB), right to left. Covered by `test/calls/calls_test.dart`, including the dialer launch (mocked), the AI call rules, request and cancel, the provider form (required fields, the key pair rule, secrets obscured and cleared, a refused save, check and disconnect), consent and opt-out permissions, every state, and layouts from 320 px at 200 percent text to 1440 px, in Arabic.
- **Desktop** (`apps/desktop`): the web client in Tauri. Calls through ANUM need the release CSP and microphone settings described under [Browser calls](#browser-calls).

## In-app calls

Calls between ANUM users inside the app are built separately. They log into the same call log with `kind: "in_app"` through `POST /api/v1/calls/log`, so they appear in Recents and can be summarised like any other call.
