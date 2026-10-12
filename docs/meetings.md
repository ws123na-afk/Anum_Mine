# Calls & Meetings

Summaries from call recordings and meeting transcripts. ANUM never joins a meeting: there is no live meeting bot. You bring the meeting to ANUM after it happened, and ANUM writes notes for you to check:

- a summary,
- the decisions,
- action items with owners and due dates,
- a follow-up email draft,
- a searchable transcript with speakers and timestamps.

Everything works in English and Arabic.

Code: `services/api/anum_api/meetings.py` (API and processing), `meeting_transcripts.py` (file parsers), `transcription.py` (speech-to-text), `meeting_connectors.py` (Zoom and Teams), `meeting_analysis.py` (the model call), `db/meetings_repository.py`. Web: `apps/web/src/MeetingsView.tsx`. Mobile: `apps/mobile/lib/features/meetings/`.

## Importing

| Way in | Endpoint | Notes |
|---|---|---|
| Transcript file | `POST /api/v1/meetings/transcripts?name=<file>` (raw body) | `.vtt` (Zoom and Teams exports), `.srt`, `.txt`, `.docx`. At most `ANUM_MEETING_TRANSCRIPT_MAX_BYTES` (5 MB). Speakers come from VTT voice tags (`<v Jane>`), `Name: text` lines and Teams' "Name 0:03" blocks. |
| Recording | `POST /api/v1/meetings/recordings?name=<file>` (raw body) | Audio or video (`.mp3 .m4a .wav .webm .mp4 .mpeg .mpga .ogg .oga .flac .mov`). At most `ANUM_MEETING_RECORDING_MAX_BYTES` (25 MB). Stored as a workspace file ([Files](files.md), counts against the workspace quota), then transcribed. Refused with `409` and setup instructions when no transcription provider is configured. |
| Zoom | `GET /api/v1/meetings/connectors/zoom/meetings`, `POST .../zoom/import {"external_id"}` | Cloud recordings of the last 30 days that have an audio transcript. |
| Microsoft Teams | `GET /api/v1/meetings/connectors/teams/meetings`, `POST .../teams/import` | Transcripts of meetings the connected user organised (Graph `getAllTranscripts`). |

Every import takes the optional:

- `title`;
- `language`: `auto`, `en` or `ar` (`auto` detects Arabic from the text);
- `agent_skill_id`: links the meeting to an installed agent, for example "Call summary: PMV meeting" under the PMV planner;
- `occurred_at`.

The file name can also be sent as the `X-File-Name` header, percent-encoded for non-ASCII names.

A Zoom or Teams transcript can be imported once per workspace (a partial unique index on `(tenant, workspace, source, external_id)`); the listing marks imported ones.

## Transcription

`ANUM_TRANSCRIPTION_PROVIDER` chooses one provider. ANUM never invents a transcript: with `none` (the default) recordings are refused, and the web and mobile apps show these setup steps instead of an upload button.

| Provider | Settings | What you get |
|---|---|---|
| Local Whisper server (free, audio stays on your machines) | `ANUM_TRANSCRIPTION_PROVIDER=openai-compatible`, `ANUM_TRANSCRIPTION_BASE_URL=http://whisper:8000/v1`, optionally `ANUM_TRANSCRIPTION_MODEL` | Any server with the OpenAI audio API: faster-whisper server / Speaches, whisper.cpp `server`, LocalAI. Timed segments; no speaker labels. |
| Hosted, OpenAI-compatible | same provider, `ANUM_TRANSCRIPTION_BASE_URL=https://api.openai.com/v1` (or `https://api.groq.com/openai/v1`), `ANUM_TRANSCRIPTION_API_KEY`, `ANUM_TRANSCRIPTION_MODEL=whisper-1` (or `whisper-large-v3`) | Timed segments; no speaker labels. Hosted Whisper APIs accept at most 25 MB. |
| Deepgram (hosted) | `ANUM_TRANSCRIPTION_PROVIDER=deepgram`, `ANUM_TRANSCRIPTION_API_KEY`, optionally `ANUM_TRANSCRIPTION_MODEL` (default `nova-3`) | Speaker labels ("Speaker 1", "Speaker 2") through diarization. |

`ANUM_TRANSCRIPTION_TIMEOUT_SECONDS` (600) bounds one call. The key stays in the server environment and is never stored with a meeting or returned to a client. Base URLs are operator configuration; workspaces cannot change them.

## Processing

Imports return at once (`202` for recordings, `201` for transcripts). The work runs after the response:

1. **Transcription** (recordings only): status `transcribing`.
2. **Analysis**: status `analyzing`. One model call through the workspace's *budgeted* gateway: the monthly [model budget](model-gateway.md) is checked before it and the usage recorded after it.
   - The transcript enters the prompt as a provenance-labelled untrusted block (`source=meeting_transcript`, [threat model](threat-model.md)), after the rules that say its text is data, never instructions.
   - At most `ANUM_MEETING_ANALYSIS_MAX_CHARS` (60,000) characters go in; a longer transcript is marked truncated. The answer is at most `ANUM_MEETING_ANALYSIS_MAX_TOKENS` (2,000) tokens.
   - The model answers JSON only, in the meeting's language.
3. **Ready**: the notes carry `provenance`: `label: ai_generated`, provider, model, time, transcript length and whether it was truncated. Clients show "AI-generated · <model>" and ask the reader to check the notes against the transcript.

Statuses and what clients show:

| Status | Meaning | Next action |
|---|---|---|
| `queued`, `transcribing`, `analyzing` | Working; clients poll. | Wait. A run stuck for 15 minutes (the API restarted) can be retried. |
| `needs_model` | The workspace has no real model (the `mock` provider). The transcript is saved; no notes are invented. | Connect a model in Settings, then **Summarize**. |
| `failed` | `error_code`: `transcription_failed`, `transcription_not_configured`, `recording_missing`, `empty_transcript`, `analysis_failed`, `model_failed`, `budget_exceeded` or `processing_failed`. `error` says what failed and how to fix it. | **Retry** (`POST /api/v1/meetings/{id}/process`). |
| `ready` | Notes are written. | **Summarize again** re-runs the analysis. |

Summarizing again keeps action items that became tasks or were marked done, and keeps a follow-up draft the user edited.

## Acting on the notes

Nothing the model writes acts on its own.

- **Action items** (`PATCH /api/v1/meetings/{id}/action-items/{item}`): text, owner, due date and done can be edited. **Create task** (`POST .../action-items/{item}/task`, needs `task:create`) turns one item into a workspace task, once; the prompt carries the meeting, owner and date. Running the task follows the workspace approval policy, as every task does. There is no team assignment yet: tasks have no assignee field, so the owner is written into the task.
- **Follow-up email** (`PUT /api/v1/meetings/{id}/follow-up`): recipients (validated, at most 20), subject and body are edited and saved. **Copy** and **Open in mail app** (`mailto:`) always work. **Send with approval** (`POST .../follow-up/send` with `{"account_id"}`) uses the [email module](email.md)'s own path: it creates a reply draft in the chosen connected mailbox (`GET /api/v1/meetings/setup` → `email_accounts`, the caller's visible connected mailboxes; needs `email:act`) and asks to send it exactly as the draft's send-approval does, so a paused `send_email` call waits in Approvals with its payload hash. Nothing is sent until it is approved. The draft is marked as written by the model unless the user edited it. Without a connected mailbox the API answers `409` and the apps say to connect one under Inbox.

## Lists, search and activity

- `GET /api/v1/meetings?q=&status=&offset=&limit=` lists newest first, at most 100 per page, with `total`. `q` searches titles, summaries and transcript text (case-insensitive; `%` and `_` are literal).
- `GET /api/v1/meetings/{id}/transcript?q=&speaker=&offset=&limit=` pages the transcript (at most 500 lines) with the list of speakers.
- **Recent activity** in the apps lists "Call summary: <title>" for the newest ready meetings, with their agent. Call mode's own end-of-call summary ([Voice](voice.md#call-mode)) is a talk with the assistant and stays an Assistant conversation; it is never listed here.
- Events `meeting.imported`, `meeting.summarized` and `meeting.failed` feed the [event stream](events.md); their payloads carry the id, title, source, agent and counts, never transcript text.

## Roles

Meetings belong to the workspace.

| Role | Can |
|---|---|
| Viewer | Read meetings, notes and transcripts (`meeting:read`). |
| Member | Also import and connect Zoom/Teams (`meeting:create`), and edit, summarise again or delete the meetings they imported. |
| Owner | Also edit and delete anyone's meetings (`meeting:manage`). |

Refusals say who can act ("Only the person who imported this meeting or a workspace owner…").

## Connectors

Each user connects their own account with OAuth (authorization code); ANUM only reads.

1. The owner registers the apps and sets the server settings below. `ANUM_MEETINGS_OAUTH_REDIRECT_URI` is the web app's callback page, for example `https://app.example.com/meetings/connected`, registered as the redirect URL of both apps.
2. **Connect** opens the provider's sign-in (`GET /api/v1/meetings/connectors/{provider}/authorize`).
3. The provider returns the browser to `/meetings/connected?code=…&state=…`. The signed-in web app posts both to `POST /api/v1/meetings/connectors/callback`.
4. The `state` is encrypted with `ANUM_SECRETS_KEY`, expires after 10 minutes and names the tenant, workspace and user who started the flow. A state that names anyone else is refused, so a link crafted by someone else cannot attach the victim's Zoom account to the attacker's workspace.

Tokens are encrypted at rest (Fernet, `ANUM_SECRETS_KEY`) in `meeting_connector_accounts` and refreshed (and re-encrypted with the current key) when they expire. They are never returned to a client or exported, and **Disconnect** deletes them; revoke ANUM in Zoom or Microsoft as well. A revoked or expired sign-in answers `409` "Connect again". `python -m anum_api.rotate_secrets` re-encrypts them with the first key like every other stored secret: it discovers connector accounts as `anum_maintenance` (ids only) and rewrites each user's row inside that tenant, workspace and user's RLS context.

**Zoom setup:**
- Zoom App Marketplace → Develop → Build App → **General App**, user-managed.
- Redirect URL: the callback page above.
- Scopes: `cloud_recording:read:list_user_recordings`, `cloud_recording:read:list_recording_files` and `user:read:user` (or the classic `recording:read` and `user:read`).
- Turn on cloud recording with **audio transcript** in Zoom's recording settings.
- Settings: `ANUM_ZOOM_CLIENT_ID`, `ANUM_ZOOM_CLIENT_SECRET`.

**Microsoft Teams setup:**
- Microsoft Entra admin center → App registrations → New registration.
- Web redirect URI: the callback page above.
- Delegated Microsoft Graph permissions: `OnlineMeetings.Read`, `OnlineMeetingTranscript.Read.All`, `User.Read`, `offline_access`. Grant admin consent where the tenant requires it.
- Teams meetings need transcription turned on.
- Settings: `ANUM_MICROSOFT_CLIENT_ID`, `ANUM_MICROSOFT_CLIENT_SECRET`, `ANUM_MICROSOFT_TENANT` (default `organizations`, or your tenant id).

Mobile lists and imports from accounts already connected; connecting happens in the web app.

## Storage and RLS

Migration `0043_meetings` (expand only):

- `meetings` and `meeting_transcript_segments` have forced RLS on `anum.tenant_id` and `anum.workspace_id`.
- `meeting_connector_accounts` also checks `anum.user_id`, so another member of the workspace sees no row.
- No other role is granted anything.

`tests/test_postgres_meetings.py` proves the isolation as the non-owner app role. The recording bytes live in object storage as a workspace file; deleting a meeting deletes its transcript and its recording file (tasks created from it stay).

## Privacy

Recordings and transcripts are personal data: the voices and words of everyone in the meeting. They are kept until the importer or an owner deletes the meeting; there is no automatic expiry yet.

**Download my data** ([Privacy](privacy.md)) includes:
- `meetings_imported`: each meeting the caller imported, with notes, transcript and a recording `download_path`;
- `meeting_connections`: provider, account and dates, never tokens.

**Delete my data** deletes:
- the meetings the caller imported, with their transcripts;
- their recording files (rows in the transaction, bytes after it commits);
- their Zoom and Teams connections.

Meetings other members imported are kept. Before uploading a recording, make sure the participants were told it would be recorded and processed.

## Not done yet

- Team assignment of action-item tasks: there is no team module or task assignee yet.
- Sending the follow-up needs a connected mailbox (Inbox); until one is connected, the draft can be copied or opened in a mail app.
- Recordings larger than the provider limit are refused rather than split into chunks.
- No retention expiry for meetings and recordings.
- Mobile cannot start the Zoom/Teams OAuth itself.
