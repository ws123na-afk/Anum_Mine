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
- Spoken requests for work (create, start, cancel or check a task, hand work to an agent or a teammate, run a quick command) are carried out through the same governed task path as the Command Center ([Actions](#actions)).
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
  - Questions are answered read-only. A request for work is carried out through the governed task path ([Actions](#actions)) and the result is returned as `action` (the action card) with `action_kind`. Nothing is ever approved, rejected or deleted by voice.
- **Streamed endpoint (used by the web Voice view and the Flutter app):** `POST /api/v1/voice/sessions/{id}/ask/stream`, same body, same checks, intents, limit and stored reply, answered as server-sent events: `meta` (`intent`, `risk_tier`, `proposed_task`, `workspace`, `action_kind`), `delta` (`{"text"}` pieces of the reply), then `timing` (the turn's server timings, [Latency](#latency)) and `done` (`{"assistant_segment", "timing"}`), or `error` when the model failed before saying anything. `/ask` returns the same `timing` field. A request for work also sends `action` (`{"card"}`, the `VoiceActionCard` of `packages/contracts/src/voice.ts`) once when its task exists and again with the outcome, before the spoken `delta` ([Actions](#actions)).
  - Questions are answered from the workspace's own data ([Grounded answers](#grounded-answers)) and stream from the model with the session's recent final turns as context (at most 8 turns and 6000 characters, each spoken turn labeled as untrusted speech) and at most 400 output tokens; fixed replies (status, small talk, refusals, task proposals) arrive as one `delta`. The reply is cut at 1200 characters, as `/ask`.
  - The body reads nothing through the request's session; the reply is stored in its own unit of work, and a reply cut short because the client stopped listening is stored as far as it got.
  - The web Voice view speaks each complete sentence as soon as it arrives, so speech starts with the first sentence instead of after the whole answer ([Conversations](conversations.md) use the same streaming gateway call).
  - The Flutter voice screen does the same (`VoiceController._streamReply`, `lib/features/voice/sentences.dart`): the reply bubble grows as `delta`s arrive, complete sentences are queued and spoken one after another while the rest streams, muted or hidden screens show the text without speaking, an `error` before any text leaves no empty bubble and shows the message, and a read reply still listens once for a follow-up after the last sentence. Without a stream client (`VoiceRepository(api)`, as in older tests) it falls back to `/ask` in one piece (`test/voice/voice_stream_test.dart`).
- **Intents:**
  - **Question:** answered by the model gateway from the workspace's data, read just now through read-only tools ([Grounded answers](#grounded-answers)). With the default `mock` provider it reports workspace facts and suggests connecting a free local model.
  - **Status:** answered from workspace counts in plain sentences.
  - **Action** (`risk_tier: "action"`, or `read` for a task's status): a request for work, carried out through the governed task path ([Actions](#actions)).
  - **Create task (proposal):** only where the server has no task runtime (an app that mounts the voice router alone): the user taps **Create task**, which calls `/commands`. Commerce price and stock changes are still proposed this way ([Commerce](commerce.md)).
  - **Visual only:** approving, rejecting or denying, passwords, credentials, API keys and secrets, granting and revoking, even inside a request for work; and destructive or money words said on their own ("delete the finance workspace", "pay the supplier"). The assistant declines and offers **Open Approvals**. Inside a request for work ("ask the Finance agent to pay the supplier") those words become a task whose tool call the runtime's policy holds for an approval.
  - **Identity, greeting, thanks:** short, warm replies.
- **Arabic:** `ar-*` sessions get Arabic replies.
- **Rate limit:** 60 messages per session, counted in the database and shared by every API replica; then HTTP 429.
- **Prompt injection:** spoken text is passed to the model as untrusted content, and so is everything a lookup returns. The model's only path to tools is read-only lookups ([Grounded answers](#grounded-answers)); it has no path to write tools or approvals: actions are chosen from the user's own words by fixed rules before the model is asked anything, and run through ToolPolicy and approvals.
- **The model never says it cannot act.** Its rules say that ANUM has already carried out any action the user asked for, that it must never say it cannot assign work, run tasks or take actions, and that it should name the words that do it ("ask the Sales agent to ...", "create a task to ...", "cancel the ... task", "run my ... quick command") when a request was not understood as one.

### The space orb

The Voice page is a space scene drawn on a 2D canvas, and the Call page shows its orb (`apps/web/src/SpaceOrb.tsx`, renderer `apps/web/src/lib/spaceScene.ts`, pure state model and geometry `apps/web/src/lib/spaceMode.ts`, styles `apps/web/src/space.css`), ported from the owner's reference scene. It loads with the Voice and Call views (lazy chunks), so nothing is added to the main bundle, and its pictures come from one local atlas (`apps/web/src/assets/space-assets.webp`, about 320 KB, served from the app's own origin, so the CSP stays same-origin).

- **What is drawn.** A parallax star field and a faint nebula; photographic sprites cut from the atlas (astronaut, Earth, satellite; regions at 0, 27.5 %, 63.5 % and 100 % of its width, each cropped to its opaque pixels so no transparent square is drawn); the astronaut floats and turns, Earth drifts and tilts slightly, one satellite circles Earth on an ellipse and passes behind it on the far half and in front on the near half, a second satellite crosses the upper sky and a small ship crosses the bottom, each on its own timing; shooting stars from the first second and then every 2.5 to 4 s; and the orb, a perspective-projected particle sphere with three tilting rings, a glass shell, a glowing core and 50 bars around it. Cursor and touch tilt the orb and shift the stars and sprites (parallax); a tap leaves a fading signal ring. **Limits:** these are flat photographic sprites, not 3D models or an animated 3D character, and Earth drifts and tilts rather than rotating on its axis.
- **The Voice page is the scene.** The page fills the whole content area under the app header, edge to edge and down to the bottom of the window, with no page padding and no page header of its own (a hidden heading names the page). The page itself never scrolls; long conversations scroll inside the panel. Over the scene: a bar with the recognition mode, wake by name, mute, **Pause/Resume**, **Shooting star**, **Conversation**, **Full screen** and a **Voice settings** gear (name, language, reply voice, transcript retention; Escape closes it); the orb, large and central, which is the talk button (tap or click to talk, again to stop, or to cut a reply short and talk); and at the bottom either one line of what is happening or, with **Conversation** on, the conversation panel (see [The conversation panel](#the-conversation-panel)). Below 1100 px the bar's buttons show icons only (names in the tooltip and for screen readers); below 760 px they form one strip that scrolls sideways, with the settings gear outside it. The orb is half the scene's shorter side (220 px at least, 560 px at most) and never taller than the space between the bar and the panel: about 40 to 55 % of the shorter side from a 390 x 844 phone to a 1440 x 900 desktop. The scene draws the orb under the talk button (`orbAnchor`) and keeps the sprites above the panel (`floorAnchor`); on stages narrower than 600 px the astronaut sits off the orb's upper left and Earth off its lower right, so both stay visible at 320 to 390 px. Space is dark in both themes: inside the scene the dark theme's colour tokens apply, so text on the glass meets AA in the light theme too, while the app chrome keeps the light theme. On phones the bar's buttons show icons only (their names stay for screen readers), and on short screens (under 700 px tall) the greeting and the example phrases give way to the orb. The Call page keeps a *compact* round orb inside its button: about 45 % of the window's shorter side, 220 px at least and at most 38 % of its height (160 px beside the camera preview).
- **States follow the real voice lifecycle** (`voiceSpaceMode`, `callSpaceMode`): microphone stopped is **standby** (cyan; also after an error, a microphone denial, a silence timeout, a cancel or stopped audio, all of which return the console to idle); microphone active is **listening** (green; a call's "hearing you" too); waiting for the reply is **thinking** (purple, faster spin, a pulsing ring of bars); the reply being voiced is **speaking** (warm); and **alert** (amber) when an approval waits and nothing else is happening. There are no preview buttons: only real events change the state.
- **Amplitude.** While listening, the bars follow a Web Audio `AnalyserNode` on the microphone stream the user already started (the Voice view's meter, `CallEngine.micLevel()` in a call); the orb never starts the microphone. While speaking, they follow the reply's audio: Natural (Kokoro) and server voices play through an `<audio>` element that `trackOutputAudio` (`apps/web/src/lib/voice.ts`) routes through an analyser when an `AudioContext` is already running (a suspended one would silence the reply, so it then plays directly and the bars hold a steady level). System voices (`speechSynthesis`) expose no audio, so each word boundary is a pulse that fades (`boundaryPulse`): an approximation, not a measurement. The orb reads the level once per frame from its own loop; there is no second animation loop.
- **Contract.** One `requestAnimationFrame` loop per scene, a `ResizeObserver` (the canvas, and on the Voice page the talk button and the panel) and the pointer and `visibilitychange` listeners, all released by `destroy()` when the page is left. Time stops while the tab is hidden and while paused; Resume continues from the same moment (no jump). With `prefers-reduced-motion` the scene is still (no time, no shooting stars, the Shooting star button disabled) and only redraws when the state, level, tilt or layout changes. The device pixel ratio is capped at 2 and particles scale with the orb (320 to 850). Without a 2D canvas the CSS orb is shown in the talk button; if the atlas fails to load, the bar says so and the orb keeps working without the sprites.
- **No dialog.** The earlier full-screen dialog is gone; **Full screen** is focus mode on the page itself ([Full screen focus](#full-screen-focus)). Under browser automation (`navigator.webdriver`) the recognizer skips the on-device probe `SpeechRecognition.available()`, which crashes the renderer in headless Chromium, and uses the cloud recognizer (`apps/web/src/lib/recognitionMode.ts`, `test/voiceMode.test.ts`).
- **Tests:** `apps/web/test/voiceFocus.test.ts` (the recognition gate: hand-over, refused starts, the wake restart delay, error meanings, the reply's own echo; focus mode: full screen, the webkit prefix, the overlay fallback) and `apps/web/e2e/voice-focus.spec.ts` (the timings above, a whole turn, barge-in by tap and by name, leaving the tab and the window and the page, focus mode with and without element full screen, Esc, double tap and swipe down, a muted call, the conversation panel off by default and remembered, and the decision card), with `apps/web/test/spaceOrb.test.ts` (state mapping, levels, meteor cadence, occlusion, DPR cap, sprite crops, the orb under its anchor and the sprites above the panel, one loop per scene, cleanup after ten mounts, pause without a time jump, hidden tab, reduced motion, pointer tilt) and `apps/web/e2e/voice-orb.spec.ts` (in the real app: independent sprite motion and satellite occlusion over 12 s, at least three shooting stars and the trigger, cursor and touch tilt, every state from mocked voice events, the large Call orb, Pause/Resume, reduced motion, ten visits with route changes and no animation frame left running, the settings gear, the light theme, the CSS orb without a 2D canvas, and the full-page layout at nine sizes in English and Arabic: edge to edge, no page scroll, a large orb, every control and both sprites on screen).

### Listening quickly and reliably

The owner reported that the Voice page took time before it listened and did not always respond. Measured in the real app with the browser's speech APIs replaced by instrumented fakes (`e2e/voice-focus.spec.ts`; one recognition session per page, as in Chrome; the on-device probe set to 400 ms, a plausible value that this sandbox cannot measure on a real Chrome):

| Path | Before | After | Cause and fix |
|---|---|---|---|
| Tap the orb → shows "I'm listening" | 669 ms | 2 to 3 ms | The state changed only after `SpeechRecognition.available()` (the on-device probe) answered. The probe now runs when the page opens (it needs no gesture) and the state changes on the tap. |
| Tap the orb → microphone starts | 431 ms | 31 to 39 ms | Same probe, awaited on the first tap. If the tap comes before the probe answers, the start waits only for what is left of it. |
| Say the name alone ("Layla") → listening turn | never (refused start, page stuck) | 47 to 73 ms | The tap-to-talk recognizer started while the wake listener was still ending: the browser refused it, the error was lost and the wake listener stayed paused. A page-wide gate (`lib/recognitionGate.ts`) starts the next recognizer once the previous one has ended and retries a refused start. |
| Browser ends the wake session (silence, ~60 s limit) → listening again | 316 ms | 49 to 57 ms (40 ms is the fake's own end delay) | The wake listener waited 250 ms before restarting. It now restarts at once, and backs off (150 ms doubling to 2 s) only when sessions end almost immediately (no microphone, network trouble). |

Also fixed: a tap while the assistant thinks or speaks stops the reply and listens at once (it used to stop and go idle, and the orb was disabled while thinking); saying the name over a reply interrupts it, but the reply itself coming back through the speakers never does (`heardOwnReply`); recognition errors say what happened (`aborted` and `no-speech` are not errors, a blocked microphone, a missing microphone and a lost network each have their message); a turn that is cancelled while it is still starting never starts late (each turn has a number, and a recognizer that starts after its turn was cancelled is ended at once).

### Full screen focus

**Full screen** in the bar puts the scene itself into the browser's full screen (`lib/focusMode.ts`, with the `webkit` prefix where needed); where an element cannot go full screen (iPhone Safari) or the request is refused, the scene becomes a fixed layer over the whole window, the app's header and sidebar included. In focus mode only the scene and the orb show: no bar, no panel, no buttons, and a faint line of what was heard or answered (two lines at most). Everything keeps working: tap or click the orb (or Space/Enter, which focus starts on) to talk, wake by name, the orb's states and spoken replies. A hint ("Press Esc to exit", or "Double-tap or swipe down to exit" on touch screens) fades after about two seconds. Esc, the browser's own exit (`fullscreenchange`), a double tap or a swipe down outside the orb leave focus mode and restore the page; leaving the page leaves the browser's full screen too.

### Leaving the tab

Hiding the tab (`visibilitychange`), another window taking focus (`blur`, when the window is still without focus 150 ms later), the page closing (`pagehide`) and leaving the Voice page all stop listening at once: the reply and its speech stop, the recognizer and the wake listener end, and the microphone's tracks are stopped so the browser's microphone indicator goes off. Coming back does not start the microphone again: the page shows standby with "Listening stopped while you were away. Tap the orb to talk." and, when wake by name was on, a **Start listening for "…"** button; wake by name resumes only after that tap or a tap on the orb. A call is muted and its reply stopped when its tab is left ("Muted while you were away. Unmute to keep talking."); it stays muted until the caller unmutes, and leaving the Call page still hangs up. A muted call keeps its microphone track, disabled, so some browsers keep their indicator on until the call ends.

### The conversation panel

The panel is off by default: the page shows the scene, the orb and one line with the state and what was heard or answered. In the panel the words recognized so far show as a dashed turn while you speak, the question is a turn of its own as soon as it is sent, and each reply carries its timings ([Latency](#latency)). The bar has an **EN / عربي** switch for the speech language (what is listened for and spoken, kept with Voice settings' language, regions kept: English US/UK, Arabic Saudi/Emirates); recognition, the voice session and the replies use it. **Conversation** (`aria-pressed`) shows the panel with the turns, the approvals notice and the message box; the choice is kept in this browser (`anum.voice.chat`). With the panel hidden, voice works fully, and whatever needs a decision still shows above the line: the waiting-approvals chip (opens Approvals), the start-listening and allow-microphone buttons, and a compact card for the latest turn that needs a tap (create a proposed task / not now, or open Approvals for something voice may not do). Approvals are never decided by voice or from this card: they open in Approvals, which shows the payload hash the decision needs.

### Free voice stack

| Piece | Default (free) | How it is chosen |
|---|---|---|
| Speech recognition | Browser Web Speech API. On-device mode (Chrome 139+) is probed on the first press and used when the language pack is installed. | Badge shows **On-device**, **Browser cloud** or **Typing only**. |
| Spoken replies | **Automatic (default):** the natural voice, Kokoro-82M (Apache-2.0) in the browser, for every English sentence it has ready within 1.5 s; otherwise that sentence is spoken by the system voice. It downloads about 90 MB once (the browser caches it) and is warmed when the Voice page opens. **Natural** (chosen explicitly) always waits for Kokoro. **Arabic and fallback:** the least robotic system voice, ranked by "Natural/Neural/Online/Premium" names. | "Reply voice" setting. Any failure falls back to the system voice. See [Latency](#latency). |
| Answers | Any OpenAI-compatible endpoint. For free, run [Ollama](https://ollama.com) locally and set `ANUM_MODEL_PROVIDER=openai-compatible`, `ANUM_MODEL_BASE_URL=http://localhost:11434/v1`, `ANUM_MODEL_NAME=<local model>`. | Backend settings. |
| Cloned voice (optional) | Self-hosted TTS server, e.g. Chatterbox (MIT). Set `VITE_ANUM_TTS_URL`; the client POSTs `{text, language}` and plays the returned audio. | Appears as "Self-hosted voice (consented clone)". |

Firefox and the Tauri desktop webviews (WebView2, WKWebView) have no usable speech recognition, so the console falls back to typing there; spoken replies still work. The desktop CSP blocks the Kokoro model download, so desktop replies use system voices. Native Whisper and a bundled natural voice in the desktop shell are the planned upgrade.

### Cloning rules

- Clone only your own voice or a voice whose owner gave recorded consent.
- Keep the model's watermark on, and label replies as synthetic.
- A voice, cloned or real, is never accepted as approval or identity.
- Do not use non-commercial weights (XTTS-v2, F5-TTS, Fish-Speech) in a commercial deployment.

See [Voice research](voice-research.md) for the options compared, their drawbacks, and how each drawback is handled.

## Grounded answers

The owner reported that spoken answers were not accurate: questions went to the model with only the spoken words and the recent turns, so questions about tasks, approvals, agents, tyres or the knowledge base got guessed answers. Questions are now answered from the workspace's own data, read just now (`anum_api/voice_grounding.py`):

- **Snapshot first.** Before the model is asked, the open tasks, the pending approvals and the agents are read concurrently through three read-only tools (`workspace_tasks`, `workspace_approvals`, `workspace_agents`) and given to the model as labeled, untrusted data blocks. A tyre question also gets its tyre tool's answer, as before. When the provider has no client tool use (`openai-compatible`, `ollama`), the snapshot also carries the knowledge base hits for the question (`knowledge_search`: the knowledge base's own keyword and semantic search over the collections the caller can see) and that is the whole grounding.
- **Lookups.** With the `anthropic` provider the model may also call read-only tools itself ([Client tool rounds](model-gateway.md#client-tool-rounds-voice)): the four workspace tools above plus every read tool of the runtime's registry (tyre planning, commerce, email, WhatsApp and marketing reads today). A tool is offered when it is declared `action_kind: "read"`, low risk, internal (no integration target, no delivery channel, nothing to undo) and a data lookup (`anum.respond` and `web_search`, which would send the spoken words outside ANUM, are not), so read tools registered later are picked up with no change here. A tool may declare `input_schema` (JSON Schema) for its arguments; without one the model sees an open object and the description names the arguments.
- **Bounded.** At most 3 rounds of lookups, at most 4 calls in a round (more answer "too many lookups"), 4 s per call (a slow lookup answers "took too long"); the fourth request sends `tool_choice: none`, so the model must answer. Each round keeps the 400-token cap, and the reply is still cut at 1200 characters. Text the model says before a lookup is spoken at once, and the first sentence of the answer is spoken as soon as it streams.
- **Mediated like a task's tool call.** Every call, snapshot or lookup, is checked before it runs: the tool must be registered and read-only (a write tool, `external.action` or `team.assign_task` asked for by name is refused and never runs), ToolPolicy decides it with the workspace's approval policy and governance (anything but an outright allow, an approval included, is not run: voice never asks for approvals), it runs in the caller's tenant, workspace and user scope (RLS in PostgreSQL; each tool's own permission check: `task:read`, `approval:read`, `memory:read`, `tyre:read` ...), and it is audited as `tool.call` with `surface: "voice"` (outcome and counts only, never content). Approvals are listed by action, task, risk and reason; their arguments stay in Approvals.
- **No invented numbers.** The rules tell the model to answer only from the blocks and the lookups, never to invent names, counts, amounts or dates, to say plainly "I couldn't find that" (Arabic: "لم أجد ذلك") and where to look when the data does not have it, and to say in a few words where a number came from ("according to Tasks", "from the knowledge base", "from the tyre data synced on 3 October").
- **Off the event loop.** Every store read of a lookup (and policy and audit) runs in a worker thread, never on the request's event loop (`tests/test_ask_off_event_loop.py`); a registry tool runs on a loop of its own in a worker thread, so a tool that reads synchronously cannot block the request. The non-streamed `/ask` without tool use makes its one model call a plain completion, as before; lookup rounds always stream.
- **Requests for work are unchanged.** Actions are still chosen by the fixed rules from the user's own words ([Actions](#actions)); the model never has a path to a write tool.
- **Mock provider:** answers as before, without a model.
- **Chat is not changed.** The Assistant chat already routes data questions to tasks through the runtime ([Conversations](conversations.md#tools-in-chat)); it does not use these lookups.
- **Tests:** `services/api/tests/test_voice_grounding.py` (only registered read-only tools offered, a later read tool picked up and a write in the same registry not, a write tool asked for by the model refused without running, tasks read in the caller's workspace only and audited with `surface: "voice"` and no content, rounds bounded with the last one forced to answer, too many calls and broken arguments not run, a policy-blocked and a slow tool, the snapshot fallback with the "don't invent" rule, the stream's timings and the TypeScript contract, the adapter's tool round with thinking blocks kept, and the budget wrapper) and `services/api/tests/test_postgres_voice.py` (in PostgreSQL as the app role: a lookup reads only the caller's workspace under RLS and its audit row carries `surface: "voice"`).

## Latency

The owner reported that replies came very late. Every turn now reports where its time went, and two delays found that way are fixed.

- **Server timings.** `voice_turn_timing` is logged for every turn with numbers, tool names and the intent only (no words): `first_token_ms` (request to the first reply text), `tool_ms` (time in lookups, the snapshot included), `total_ms`, `tool_rounds`, `tool_calls`, `grounding` (`tools`, `snapshot` or `none`) and `sources` (the tools read). The same object is the stream's `timing` event, sent just before `done` and repeated in it (`VoiceTurnTiming` in `packages/contracts/src/voice.ts`), and the `timing` field of `/ask`.
- **On the Voice page.** With **Conversation** on, each reply shows a small line such as "heard 0.3 s, answer 1.2 s, voice 0.8 s": *heard* from the last words recognized to the question being sent (absent for a typed question), *answer* from sending to the first words of the reply arriving, *voice* from those words to the reply starting to sound (absent when muted). Its tooltip (and screen readers) give the server's side: the model's first words, the lookups with the tools read, and the server total (`src/lib/voiceTiming.ts`).
- **Speech starts with the first sentence**, also when the model looked something up first: text streams to the page as soon as the model writes it, in every round.
- **Natural voice no longer holds a sentence back.** In-browser Kokoro can take seconds per sentence on an ordinary laptop. The default reply voice is now **Automatic**: Kokoro is warmed when the Voice page opens (not on a data-saver connection), and a sentence it cannot have ready within 1.5 s is spoken by the system voice instead; once a warm Kokoro misses the deadline, the page stops handing it sentences until it is reloaded, so it does not keep the CPU busy. Choosing **Natural** in Voice settings keeps waiting for it, as before (`createNaturalSpeaker`).
- **The system voice no longer waits 1.5 s before every sentence** in a browser that reports no voices: the voice list is loaded once when the page opens and a sentence waits at most 0.25 s for it. Found by the *voice* timing in the end-to-end test: 1.6 s before the fix, 0.1 s after.
- **Owner setting that matters most:** the voice answers on the workspace's *fast* route. With the `anthropic` provider set the fast model and `fast_effort: low` in Settings › Model gateway; when no fast model is set, the main model answers with the model's default effort, which thinks longer before the first word ([Model routing](model-gateway.md#model-routing)).
- **Measured here** (in-memory store, scripted model, so model time is near zero): a turn with one lookup round took `first_token_ms` 24, `tool_ms` 21 (snapshot and lookup with policy and audit), `total_ms` 25. The browser test (`e2e/voice-focus.spec.ts`, fake recognizer and speech, mocked API) shows "heard 0.1 s, answer 0.4 s, voice 0.1 s" up to "heard 0.2 s, answer 0.6 s, voice 0.1 s". Real figures depend on the provider and need measuring in production with the `voice_turn_timing` log.
- **Tests:** `apps/web/test/voiceTiming.test.ts` (the timing line in English and Arabic, the server tooltip, the EN / AR switch keeping a region, and the natural voice: system voice when not ready in 1.5 s, a warm engine used, a slow engine skipped afterwards, an explicit choice that waits) and `apps/web/e2e/voice-focus.spec.ts` (a whole turn: the heard words in the panel, the timing line and its tooltip; the EN / عربي switch and recognition in Arabic), `apps/web/e2e/voice-actions.spec.ts` (the timing line under an action's reply).

## Actions

The voice assistant does the work it is asked for, through the same governed path as the [Command Center](command.md) and [Tasks](tasks.md). It never decides an approval.

| Say | What happens |
|---|---|
| "Ask the Sales agent to follow up with yesterday's leads", "Pass a task to the Sales agent: ...", "Assign the stock count to the Fleet agent", "Create a task for the Sales agent to ..." (Arabic: "اطلب من وكيل المبيعات أن ...", "كلف ...") | A task with the work as its prompt and `agent_id` set to that agent ([Agents](agents.md#running-tasks-as-an-agent)), started at once. Reply: "Created task "Follow up with yesterday's leads" and assigned it to Sales agent." plus the outcome. |
| "Ask Ahmed to call the supplier by Thursday" | A teammate, a department assistant or ANUM: a task whose prompt is the sentence, which the runtime plans as `team.assign_task` ([Team work](team-work.md#assigning-in-words)). |
| "Create a task to draft the weekly report", "New task: ..." | A task, started at once. "Remind me to ..." saves the task without starting it. |
| "Send the quarterly report to the bank" (any request the runtime plans as an outward action, `external.action`) | A task, started at once: the tool call waits for an approval. |
| "Start the weekly report task", "Run task weekly report" | Runs a task that has not run (`POST /tasks/{id}/run`) or starts a new run of a failed or cancelled one (`/retry`). A task already running or finished answers with its status. |
| "Cancel the weekly report task", "Cancel my last task" | `POST /tasks/{id}/cancel`: owners only, as in Tasks; pending approvals of the task are withdrawn. |
| "What's the status of the weekly report task?", "Is the weekly report task done?" (Arabic: "ما حالة مهمة ...") | Read-only: where it stands, the first sentence of its result, its failure reason or the approval it waits for. |
| "Run my morning check quick command" | The caller's [quick command](command.md#quick-commands) becomes a command (`source: "voice"`, `quick_command_id` set, the use count updated) planned and created like `POST /commands`, and every step is started in parallel, as **Start execution** does. A step blocked by policy refuses the whole command with the reason. |

How it works (`anum_api/voice_actions.py`):

- **Understanding.** What to do, which agent, which task and which quick command are read from the user's own words by fixed, bounded rules (English and Arabic lead-ins such as ask, tell, assign, pass, create a task, cancel, start, run, status of), never from model output or anything retrieved (threat model G5). A leading "Hey", "please", "can you" and the assistant's name are ignored. An agent is matched by its name or department, with or without "agent" or "assistant" ("the Sales agent", "sales"); the longest match wins. A task is matched by title among the caller's visible tasks: an exact title wins, then a title that starts with the words, then one that contains them. "My last task" is the caller's newest task, whatever its state: the action then checks that task, so "cancel my last task" when the newest task already finished answers with its status and never cancels an older one (the same for "start my last task").
- **Same path as the screens.** Tasks are created as `POST /api/v1/tasks` creates them (a `task.created` event with `source: "voice"`) and started with the body of `POST /api/v1/tasks/{id}/run` (`main.start_task_run`): the permission, the run lock, the model budget, the agent profile, ToolPolicy, governance, autonomy and the approval policy apply exactly as when a person presses Run. Cancel is the body of `POST /tasks/{id}/cancel` (`main.cancel_task_record`), retry of `/retry`, and quick commands go through `commands.create_command_record`, the body of `POST /commands`. With the durable runtime (`ANUM_RUNTIME_BACKEND=temporal`) runs are queued like any other and the reply says the task is running.
- **Approvals.** A step whose tool call needs approval stops at `waiting_approval`; its approval is listed in Approvals, the notifications menu and the task like any other, with its payload hash. The reply says "It needs an approval first, so I've sent it to Approvals." and the card links there. Approving still needs a tap and the payload hash; "approve", "reject", passwords, credentials, secrets and permission changes are refused by voice even inside a request.
- **Permissions and tenancy.** Every check is the route's own: creating needs `task:create`, starting `task:run`, cancelling `task:cancel` (owners). A refused action creates nothing and the card says who can do it. Agents, tasks and quick commands are read in the caller's tenant, workspace and user scope (RLS in PostgreSQL); another workspace's agent, another user's private agent or task they cannot see is "not found", and the reply lists the agents the caller can see instead of guessing. A paused agent is not given work. Viewers cannot open a voice session; a member whose role changes mid-session is checked against the role they have now.
- **Once per segment.** Before a request for work acts, `/ask` and `/ask/stream` claim the transcript segment with the same atomic `consume_segment` as `/commands` (one conditional `UPDATE` in PostgreSQL). A retried or concurrent submit of the same segment, through either path or `/commands`, acts at most once: the replay answers "I've already acted on that request. Check Tasks for its progress." without an action card. Questions and status answers do not claim the segment, so they can be asked again.
- **Not acting.** Several matching tasks or quick commands: the reply names them (at most three) and asks which one. No match: it says so. "Ask the HR agent to ..." without such an agent: the reply lists the agents. "Create a task" with nothing after it: "What should the task be?"
- **Replies** are short and spoken: what was done ("Created task "X" and assigned it to Sales agent."), then the outcome ("It's done: ...", "It needs an approval first, so I've sent it to Approvals.", "It failed: ...", "It's running now; you can follow it in Tasks."). Arabic sessions get Arabic replies. The reply is stored in the transcript like any other; the card is not stored (it is in the stream and the `/ask` answer), and the task, its runs, approvals and events are the record.
- **The action card** (web): `apps/web/src/VoiceActionCard.tsx` under the reply in the Voice view's conversation, with the kind ("Work handed over", "New task", ...), the state, the task title, the agent, the step and approval counts, the matches when the request was ambiguous, and **Open task** and **Open Approvals**. It has no approve button. Strings are in `src/lib/messages/en.voiceActions.ts` and `ar.voiceActions.ts`; the pure helpers are `src/lib/voiceActions.ts`.
- **Tests:** `services/api/tests/test_voice_actions.py` (the rules, the prompt, agent tasks run through the runtime, another workspace's and a private agent not found, a paused agent, a refused role, the approval path and its hash, approval words refused, create, start, status and cancel by name, members refused cancel, "my last task" resolving to the newest task before its state is checked, a resubmitted segment acting once, ambiguous and missing names, quick commands as commands, Arabic, the non-streamed `/ask`, the teammate path through `team.assign_task`, and the TypeScript contract matching the card), `services/api/tests/test_postgres_voice.py` (a segment acts once across replicas and under concurrent claims), `apps/web/test/voiceActions.test.ts` and `apps/web/e2e/voice-actions.spec.ts` (the card, its links and its fit at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844).

## Call Mode

A voice (and photo or camera) call with the assistant: web **Call** in the navigation, Flutter **Call** button on the Voice tab. It is the owner mockup's screen 06: dark, the assistant's name, the status (Listening, Hearing you, Thinking, Speaking, Muted) and the call timer, the orb, the controls **Mute**, **Video**, **Speaker**, **Chat** and **More**, and a red **End call**.

### How a call runs

- **One voice session per call** (the same sessions, retention and RLS as the Voice view). The call picks its language (English US/UK, Arabic Saudi/Emirates), its speech engines and its transcript retention before it starts (web lobby).
- **Turns end on silence.** Web: energy-based voice activity detection on the microphone (echo cancellation, noise suppression and gain control on), with a noise floor that adapts to the room (`createVad` in `apps/web/src/lib/call.ts`). A turn ends after 700 ms of silence; the recognizer's final words, when they come first, end it too. Flutter: device recognition with a 0.9 s pause, restarted after every turn so the call keeps listening.
- **Replies stream** from `/ask/stream` on the fast model route and are spoken sentence by sentence (the existing streamed voice answer).
- **Barge-in.** Recognition keeps running while a reply plays. The reply stops at once (speech cancelled, stream closed, the partial reply stored as far as it got) when the caller talks over it:
  - web: the input level stays above an echo-raised threshold (1.8 times the speech threshold) for 250 ms, or the recognizer hears three or more words that are not the reply's own sentence;
  - Flutter: the recognizer's level stays at or above 8 dB for three readings, or it hears words that are not the reply's echo.
  - Tapping the orb also stops the reply. **More** shows how often you cut in.
- **Mute** turns the microphone track off (web) or stops recognition (Flutter); nothing is heard until unmuted. **Speaker** off keeps replies as text in **Chat** without speaking them.
- **Chat** is the transcript panel: both sides of the call, typing (also the fallback when a browser has no recognition), **Create task** for a proposed task and **Open Approvals** for anything that needs a decision.
- **Approvals never happen by voice.** A call uses the same assistant: "approve", "reject", credentials and similar are refused with a pointer to Approvals, and requests for work are carried out through the governed task path ([Actions](#actions)), so a risky step waits in Approvals.
- **Leaving the page or screen hangs up**: microphone, camera and speech stop.

### Latency target

`GET /api/v1/voice/capabilities` reports the targets the clients are built against: **first audio within 1.5 s** after the caller stops talking, **barge-in silences the reply within 300 ms**, and **700 ms of silence ends a turn**. The client measures the first one for every reply (end of speech to the first sound) and **More** shows the last value against the target. What dominates it is the model's time to first token on the fast route plus 700 ms of end-of-turn silence: a hosted fast model (claude-haiku-5-5, gpt-4.1-mini) is usually inside the target; Ollama on a small CPU VM is not. The 1.5 s target was not measured end to end against a live provider from the build environment.

### Speech engines

Engines are pluggable and reported honestly by `GET /api/v1/voice/capabilities`; an engine that is not configured says why and is not offered.

| Engine | Recognition | Voice | Cost | Where |
|---|---|---|---|---|
| Device (default) | Browser Web Speech (on-device on Chrome 139+ when the language pack is installed, else the browser's cloud service) or the phone's recognizer | System voices; in the browser also the free natural Kokoro voice (English) | Free | Web and Flutter |
| Server, hosted | `POST /api/v1/voice/sessions/{id}/transcribe`: OpenAI `whisper-1` or `gpt-4o-mini-transcribe` | `POST /api/v1/voice/sessions/{id}/speech`: OpenAI `gpt-4o-mini-tts` or `tts-1` | At the time of writing about $0.003 to $0.006 per audio minute for recognition and about $15 per million characters (`tts-1`); check the provider's current prices | Web |
| Server, self-hosted open source | A Whisper-compatible server (for example Speaches with faster-whisper) | Piper or Kokoro voices behind the same server | No per-minute charge; the VM's CPU | Web |

- Both server engines speak the OpenAI audio API, so hosted and self-hosted are the same adapter (`anum_api/speech_engines.py`). Settings: `ANUM_SPEECH_STT_BASE_URL`, `ANUM_SPEECH_STT_MODEL`, `ANUM_SPEECH_STT_API_KEY` (secret), `ANUM_SPEECH_TTS_BASE_URL`, `ANUM_SPEECH_TTS_MODEL`, `ANUM_SPEECH_TTS_API_KEY` (secret), `ANUM_SPEECH_TTS_VOICE_EN`, `ANUM_SPEECH_TTS_VOICE_AR`, `ANUM_SPEECH_TIMEOUT_SECONDS`. Empty base URL or model means off.
- **Cost is metered.** `ANUM_SPEECH_STT_USD_PER_MINUTE` and `ANUM_SPEECH_TTS_USD_PER_MILLION_CHARS` price each call into the workspace's monthly model budget (zero tokens, the estimated cost); a used-up budget stops server speech with `402` like a model call. The billed length of a clip is what the client measured, never less than its size allows at 32 kbit/s, never more than 120 s.
- **Server recognition** takes one turn as the raw request body (`audio/webm`, `audio/ogg`, `audio/wav`, `audio/mp4` or `audio/mpeg`, at most 4 MB), stores the words as a final user segment and returns it; the audio is not kept or logged. The web client records each turn with `MediaRecorder` from the moment voice activity starts, so the first 100 ms or so of a turn can be cut. **Server voice** takes at most 600 characters and returns audio with `cache-control: no-store`; a failing server voice falls back to the device voice for that sentence and says so.
- **Arabic and English voices:** device voices are ranked for the call's language (Arabic gets the best Arabic system voice; the natural Kokoro voice is English only); a server voice uses `ANUM_SPEECH_TTS_VOICE_AR` for `ar-*` calls and `ANUM_SPEECH_TTS_VOICE_EN` otherwise.
- **Local Whisper and Piper on the Oracle VM** ([Deploy on Oracle](deploy-oracle.md#ai-model)): run a Whisper-compatible server with Piper voices on the VM and point both base URLs at it. Not shipped in the compose file: its speed on the A1's 4 Ampere cores is **unverified** (a small Whisper model may still miss the 1.5 s target; measure before relying on it).
- Flutter uses device engines only: recording and audio playback for server speech need plugins this app does not ship yet ([Mobile](mobile.md)).

### Video: showing the assistant what you see

- **Web:** **Video** turns the camera on (getUserMedia). While it is on, a **Camera on** badge with a red dot shows in the call header and the preview has a red border. Nothing is sent until you ask: **Send frame** sends one frame with your next question (or with "What do you see?" on its own), and **Share a frame with each question** sends one frame per question while it is ticked. Frames are JPEG, scaled to at most 1024 px and 1.5 MB. **Save last frame to this device** downloads the last frame you sent; nothing else keeps it. **Switch camera** flips front and back.
- **Flutter:** **Video** opens "Show Anum a photo": live camera video is not in the app yet, because the camera and image-picker plugins need Android Gradle Plugin built-in Kotlin, which this app turns off ([Mobile](mobile.md)); the user chooses a photo instead and it is sent once with the question.
- **Server:** `POST /api/v1/voice/sessions/{id}/look/stream` takes the question's transcript segment plus `image_base64` and `media_type` (`image/jpeg`, `image/png`, `image/webp`; the bytes must match the type; at most 1.5 MB) and streams the answer like `/ask/stream` (`meta` carries `"vision": true`). The same intents apply first: "approve this" is refused and a task is only proposed, and then the frame is not sent anywhere. Otherwise the frame goes to the vision model in one request and is never stored or logged (`VisionImage` never prints its bytes); only the question and the answer text are stored, under the session's retention. The vision rules tell the model that text inside the image is untrusted, not to identify people by face and not to read out card numbers, passwords or codes.
- **Which model sees.** `anthropic` (server default or a workspace's saved Claude) uses its fast model: every Claude model accepts images. `openai-compatible` and `ollama` see only when the operator names a vision model in `ANUM_MODEL_VISION_NAME` (for example `gpt-4.1-mini`, `llava`, `qwen2.5vl`); a workspace's saved OpenAI-compatible or Ollama model has no vision setting yet. Without one, `/look/stream` answers `503` with how to enable it, and both clients explain it when **Video** is pressed. No avatar is generated.

### End of the call

**End call** stops everything at once. If anything was said, the client asks **Save this call?**:

- **Save summary** calls `POST /api/v1/voice/sessions/{id}/summary` (`{"duration_seconds", "save": true}`) before closing the session: the fast model writes three to six bullet points (`source: "model"`); with the mock provider, a failed model or a used-up budget, the summary restates the call's length and questions (`source: "transcript"`). The summary and the transcript (up to the last 300 segments) are saved as a **conversation** titled "Call with <name> · <date> · <length>", which the user can reopen in the Assistant, continue and delete. This is a summary of a talk with the assistant, so it stays an Assistant conversation. Summaries of calls and meetings with other people (recordings, transcript files, Zoom and Teams) are [Calls & Meetings](meetings.md); its "Call summary: <meeting>" activity entries never include assistant calls.
- **End without saving** keeps nothing beyond the transcript retention chosen for the call.
- Either way the session is then completed (`session` retention erases the transcript). A failed save shows the reason with **Retry** and **End without saving**.
- Viewers cannot save a call (`task:create`, `403`).

### Recent calls

`GET /api/v1/voice/sessions?limit=&cursor=&status=` lists the caller's own calls and Voice sessions in the current workspace, newest first (`task:read`; viewers too). Rows are private to the user: the query filters on tenant, workspace and user, and the PostgreSQL policies on `voice_sessions` and `voice_transcript_segments` check `anum.user_id` as well, so another person in the same workspace never sees them. `limit` is 1 to 100 (default 20); `next_cursor` (an opaque keyset on `created_at` and `id`) fetches older rows and a cursor the API did not issue is `422`; `status` is `active`, `completed` or `cancelled`. Each row is the session plus `segment_count` (transcript lines still stored) and `first_line` (the caller's first final line, at most 160 characters). Both are `0` and `null` once the transcript is gone: `session` retention erases it when the call ends, and a `30_days` transcript is hidden from its `expires_at` on. The list reads the session rows plus two grouped reads of the segments, never one query per row.

The web Call page shows this list next to the start card ("Recent calls"): status, date, language, retention and the first line, with **Load older**. Opening a row reads its transcript (`GET /api/v1/voice/sessions/{id}/transcript`); a saved call's summary is in AI Chat as its own conversation.

### States

| State | What the caller sees |
|---|---|
| Microphone blocked | How to allow it in the browser's site settings or the phone's settings, **Try again**, and typing in Chat |
| No microphone | Connect one and try again |
| No speech recognition (Firefox, desktop webviews, phones without a recognizer) | Typing only in Chat; replies are still spoken |
| Offline | The call keeps the screen and answers again when the connection is back |
| Model or speech provider down | The provider's message, try again; an owner can check Model settings |
| Budget used up | The budget message with the reset date |
| 60-question limit, session closed | End the call and start a new one |
| No permission | Who can start calls |
| Camera blocked, no camera, no vision model | What to do, with the operator or owner setting named |

## Remaining Release Gates

- Voice actions in the Flutter app and the web Call page: the server acts for them too and the spoken reply says what happened, but they do not draw the action card yet (the web Voice view does).
- Grounding and timings were measured with a scripted model only; the production time to first token, lookup time and the Automatic voice's behaviour on the owner's laptop need checking against the `voice_turn_timing` log and the panel's timing line.
- The Flutter app and the web Call page use the same grounded `/ask/stream` but do not show the timing line yet; the Flutter app has no EN/AR switch outside its settings.
- Voice actions do not chain: "create a task and assign it to the Sales agent, then email me the result" is one request for one task. Multi-step requests belong in the Command Center, which plans steps.
- A `session`-retention session that is never completed or cancelled keeps its transcript until it is closed. An expiry for abandoned sessions (for example 24 hours after the last segment) needs a product decision.
- Physical Android and iOS microphone, Bluetooth headset, interruption, and background lifecycle testing.
- OIDC release authentication, notification deep links, and device-authenticated approval where policy requires it.
- Call mode: barge-in, echo handling and the 1.5 s first-audio target need measuring on physical Android and iOS phones, laptops without headphones and against the production model; the thresholds are the starting values above.
- Call mode on Flutter: live camera video and server speech engines wait for camera, recorder and audio-player plugins that build with this app's Android setup.
- Server speech uses one recording per turn, not a streaming socket: no partial words while the caller talks with the server recognizer.
