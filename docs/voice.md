# Voice

Voice is an implemented ANUM surface built on the same identity, task, approval, memory, and event boundaries as typed commands.

## Voice Use Cases

- Start and manage tasks hands-free.
- Hear concise task status updates.
- Approve or reject low-friction actions when policy allows.
- Dictate notes and instructions into memory or tasks.
- Use desktop or mobile context while moving between devices.

## Architecture

Voice clients should connect to the same backend task APIs and realtime streams as other clients. Speech-to-text, text-to-speech, and realtime audio models should be provider adapters behind the model gateway when possible.

## Safety

Voice approval requires extra care. The system should confirm high-impact decisions using clear summaries and may require visual confirmation or device authentication for sensitive actions. Voice transcripts should be treated as sensitive memory sources.

## Implemented

- Tenant-scoped voice sessions and configurable session, 30-day, or permanent transcript retention.
- Flutter push-to-talk with device speech recognition, English and Arabic locale selection, partial transcription, stop/cancel, and editable review.
- Flutter Ask view: a named assistant ("Anum" by default, renamed in the app) that wakes when you say its name, with hands-free listening, a 3D orb that shows listening, thinking and speaking, spoken replies through the device voice, and a governance signal for answers that need confirmation or visual approval. Questions go to `POST /api/v1/voice/sessions/{id}/ask` and are answered by the workspace's saved model (for example Ollama).
- Explicit command confirmation before task creation and governed execution.
- Visual approval escalation for sensitive actions; spoken approval cannot bypass policy.
- Permission-denied recovery and a keyboard fallback.
- Optional text-to-speech confirmation of the created task status.
- Seven approved Figma screens, a six-state Voice Capture component, and an eight-step voice safety workflow.

## Storage and Retention

Voice sessions and transcripts are private to the user who started them, inside one tenant and workspace.

- **Where they live:**
  - With `ANUM_REPOSITORY_BACKEND=postgresql`, sessions are rows in `voice_sessions` and segments in `voice_transcript_segments` (migration `0011_voice_automation`).
  - Both tables have forced RLS. The policy checks `anum.tenant_id`, `anum.workspace_id` and `anum.user_id`, so another user of the same workspace sees no rows even if a query forgets a filter.
  - Writes need an onboarded workspace (`409` otherwise).
  - With `memory` (local and tests), the in-process `VoiceStore` keeps the same contract.

Retention is chosen per session:

| Retention | Transcript lifetime | How it is enforced |
|---|---|---|
| `session` (default) | Until the session is completed or cancelled | The segments are deleted in the same transaction that closes the session, and `transcript_purged_at` is set. A row lock orders this against concurrent appends and replies, so no segment is written after the erase. |
| `30_days` | 30 days from session start (`expires_at`) | From `expires_at` on, every read (transcript, commands, ask) treats the transcript as gone, even before a purge runs. `python -m anum_api.voice_retention` deletes the rows; run it at least daily ([Runbooks](runbooks.md#voice-transcript-retention)). |
| `permanent` | Until the workspace's data is deleted | No expiry. |

The session row itself (locale, assistant name, status, timestamps, no transcript text) stays after its transcript is gone.

- **Purge job:** `python -m anum_api.voice_retention [--dry-run]` crosses tenants without bypassing RLS.
  - It discovers expired sessions as the `anum_maintenance` role. That role can read only `id`, `tenant_id`, `workspace_id`, `user_id` and `expires_at`, and only of sessions whose transcript expired and was not purged yet.
  - It then deletes each user's segments as the application role, inside that tenant, workspace and user's RLS context ([Multi-tenancy](multi-tenancy.md#maintenance-role)).
  - It prints counts only, never transcript text.
- **Question limit across replicas:** the 60-question limit per session is the `ask_count` column, incremented with one `UPDATE ... RETURNING`. Every API replica shares it, with no Valkey dependency.
- **One task per transcript:** a segment becomes a task at most once. The command sets `consumed_at` with an `UPDATE` that only matches while it is null, so the same segment submitted to two replicas creates one task.
- **No transaction during model calls:** an ask commits its checks and counter first, calls the model, then stores the reply in a second short transaction. If the session was closed in between, the reply is not stored and the request answers `409`.

## Web Voice Assistant

The web and desktop Voice view is a spoken conversation with a named assistant, "Anum" by default, renamed under Voice settings.

- **Talking:**
  - **Tap the orb and talk.** The browser ends the turn when you pause, and the message is sent automatically.
  - Holding the space bar works as push-to-talk.
  - Typing works everywhere.
- **Wake by name (on by default):**
  - While the Voice view is open, saying the name wakes the assistant with a soft chime. No tap is needed.
  - "Layla, what's running?" answers straight away.
  - "Layla" on its own starts listening.
  - After an answer it listens once for a follow-up, so the conversation continues without repeating the name.
  - Listening pauses while the assistant is speaking, and the pill in the header switches it off.
  - With browser cloud recognition, the UI warns that the browser's speech service hears the room while it is on.
  - If the browser blocks the microphone, an **Allow microphone** button retries.
- **Name and small talk:**
  - "What's your name?", greetings and thanks get friendly replies.
  - A leading "Hey <name>," is stripped before the request is understood.
- **Endpoint:** `POST /api/v1/voice/sessions/{id}/ask` with a final user transcript segment.
  - Sessions carry `assistant_name`: letters, digits, spaces and `.'-`; at most 40 characters.
  - The endpoint is read-only: it never creates, approves or deletes anything.
- **Intents:**
  - **Question:** answered by the model gateway. With the default `mock` provider it reports workspace facts and suggests connecting a free local model.
  - **Status:** answered from workspace counts in plain sentences.
  - **Create task:** proposed only. The user must tap **Create task**, which then calls the existing `/commands` endpoint.
  - **Visual only:** approve, reject, delete, pay, credentials and similar. The assistant declines and offers **Open Approvals**.
  - **Identity, greeting, thanks:** short, warm replies.
- **Arabic:** `ar-*` sessions get Arabic replies.
- **Rate limit:** 60 messages per session, counted in the database and shared by every API replica; then HTTP 429.
- **Prompt injection:** spoken text is passed to the model as untrusted content. The answer is text only and has no path to tools or approvals.

### Free voice stack

| Piece | Default (free) | How it is chosen |
|---|---|---|
| Speech recognition | Browser Web Speech API. On-device mode (Chrome 139+) is probed on the first press and used when the language pack is installed. | Badge shows **On-device**, **Browser cloud** or **Typing only**. |
| Spoken replies | **Natural (default for English):** Kokoro-82M (Apache-2.0) runs in the browser. It downloads about 90 MB once and the browser caches it. **Arabic and fallback:** the least robotic system voice, ranked by "Natural/Neural/Online/Premium" names. | "Reply voice" setting. Any failure falls back to the system voice. |
| Answers | Any OpenAI-compatible endpoint. For free, run [Ollama](https://ollama.com) locally and set `ANUM_MODEL_PROVIDER=openai-compatible`, `ANUM_MODEL_BASE_URL=http://localhost:11434/v1`, `ANUM_MODEL_NAME=<local model>`. | Backend settings. |
| Cloned voice (optional) | Self-hosted TTS server, e.g. Chatterbox (MIT). Set `VITE_ANUM_TTS_URL`; the client POSTs `{text, language}` and plays the returned audio. | Appears as "Self-hosted voice (consented clone)". |

Firefox and the Tauri desktop webviews (WebView2, WKWebView) have no usable speech recognition, so the console falls back to typing there; spoken replies still work. The desktop CSP blocks the Kokoro model download, so desktop replies use system voices. Native Whisper and a bundled natural voice in the desktop shell are the planned upgrade.

### Cloning rules

- Clone only your own voice or a voice whose owner gave recorded consent.
- Keep the model's watermark on, and label replies as synthetic.
- A voice, cloned or real, is never accepted as approval or identity.
- Do not use non-commercial weights (XTTS-v2, F5-TTS, Fish-Speech) in a commercial deployment.

See [Voice research](voice-research.md) for the options compared, their drawbacks, and how each drawback is handled.

## Remaining Release Gates

- A `session`-retention session that is never completed or cancelled keeps its transcript until it is closed. An expiry for abandoned sessions (for example 24 hours after the last segment) needs a product decision.
- Physical Android and iOS microphone, Bluetooth headset, interruption, and background lifecycle testing.
- OIDC release authentication, notification deep links, and device-authenticated approval where policy requires it.
- Provider-backed streaming audio only if short device speech recognition is insufficient; the current implementation is intentionally push-to-talk for concise commands.
