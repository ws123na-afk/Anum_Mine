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
- Explicit command confirmation before task creation and governed execution.
- Visual approval escalation for sensitive actions; spoken approval cannot bypass policy.
- Permission-denied recovery and a keyboard fallback.
- Optional text-to-speech confirmation of the created task status.
- Seven approved Figma screens, a six-state Voice Capture component, and an eight-step voice safety workflow.

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
- **Rate limit:** 60 messages per session; then HTTP 429.
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

- Physical Android and iOS microphone, Bluetooth headset, interruption, and background lifecycle testing.
- OIDC release authentication, notification deep links, and device-authenticated approval where policy requires it.
- Provider-backed streaming audio only if short device speech recognition is insufficient; the current implementation is intentionally push-to-talk for concise commands.
