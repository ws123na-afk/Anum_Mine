# Connecting Integrations: the Owner's Runbook

This page tells you, in plain steps, how to switch on every service ANUM can connect to on the Oracle VM ([Deploying on one Oracle Cloud VM](deploy-oracle.md)). Each section says what the integration does, whether it is ready, where you see it in ANUM, what to create in the provider's console, which lines to fill in `infra/oracle/.env`, how to restart and how to check it, what it costs and whether the provider must approve anything.

**Where to look in ANUM.** Open **Integrations** in the web app (`#connections` opens it too). Each integration below is one row with its live state from the server:

| State | Meaning | Row action |
|---|---|---|
| Connected | Set up and working; where accounts are connected, at least one is and none is failing. | **Manage** |
| Ready to connect | The server is set up; nobody has connected an account in this workspace yet. | **Connect**, then **Open ...** in the panel to connect the account there. |
| Not set up | The server is missing settings. Owners and organization administrators see the setting names. | **Set up**; follow the numbered section below. |
| Needs attention | A setting is half done, or a connected account is failing. | **Fix**: fix the named setting, or open the account and sign in again. |

Members who cannot change setup see **Details** instead of Set up or Fix. No key or secret is ever shown in the app. The list is built by `GET /api/v1/integrations/setup` ([Tools and integrations: Connection setup](tools-and-integrations.md#connection-setup)).

### The Integrations page

The page is built to scan, not to scroll:

- **Status and filters.** One bar at the top holds the filter chips **All**, **Connected**, **Needs setup** (not set up or ready to connect) and **Error** (needs attention, or a platform service that is degraded), each with its count, a search box (names, descriptions, categories and setting names, for example `google_client`) and a one-line summary. A platform service the deployment does not use counts as "not used" and shows only under All.
- **Categories.** Rows are grouped into collapsible sections: Messaging & email, Calls & meetings, AI & search, Marketing & social, Commerce & data, Developer & webhooks, Sign-in, files & monitoring, and last **Platform services** (the database, sign-in server, cache, event bus, workflow engine, storage, web search, mail providers and webhook this deployment runs on, checked live). Each header shows how many items it has and how many need setup or have errors. A section where nothing needs action starts collapsed; any filter or search opens every section with a match.
- **Rows.** Icon, name, one line on what it does, the status and one action. Problems sort first inside each section.
- **Details panel.** The action opens a side panel (Esc or Close returns focus to the row) with everything else: what to do next, the full description and cost, **Open <view>**, **Setup guide** (the section here), the missing setting names and callback URLs with **Copy** (owners and organization administrators), and **Check connection** (organization administrators; it asks the server whether it can reach the provider, never sends data, and every check is audited). For a platform service the panel shows its address, last check, last error, how to fix it and, for workspace owners, **Test connection**, the per-workspace switch and the webhook's workspace token.

## Before you start

- **Your three host names.** `ANUM_WEB_HOST`, `ANUM_API_HOST` and `ANUM_AUTH_HOST` in `~/anum/infra/oracle/.env` (for example `app.example.com`, `api.example.com`, `auth.example.com`, or DuckDNS names). Below they are written `<WEB>`, `<API>` and `<AUTH>`. Every callback URL must use them exactly, with `https://`.
- **Editing `.env`.** Connect to the VM over SSH, then `nano ~/anum/infra/oracle/.env`. Put each value after the `=` with no spaces around it. A value with spaces or `$` goes in single quotes. Never paste a key into a chat, a ticket or the ANUM assistant; keep a copy in your password manager.
- **Applying a change.** After editing `.env`: `cd ~/anum/infra/oracle && docker compose up -d api`. After pulling a new ANUM version: `cd ~/anum && sudo infra/oracle/update.sh` (it also adds any new setting lines to `.env`, empty). If the API does not come back, `docker compose logs api --tail 50` names the setting it refused (never its value).
- **Checking.** Reload **Integrations** in the web app and look at the row; an organization administrator can press **Check connection**. You are an organization administrator once `bash ~/anum/infra/oracle/keycloak-2fa.sh grant-org-admin <your username>` has run and you signed in again ([Organization administrators](deploy-oracle.md#organization-administrators)).

## Summary

| # | Integration | Status in the code | Connected in ANUM | Cost | Approval needed |
|---|---|---|---|---|---|
| 1 | AI model (Claude, OpenAI-compatible, Ollama) | Works end to end; needs a key (or Ollama) | Settings (per workspace model), Integrations | Paid per use; Ollama free | None |
| 2 | Server voice (speech-to-text, text-to-speech) | Built, needs keys; device speech works without | Voice, Call | Paid per minute or free self-hosted | None |
| 3 | Meeting recording transcription | Built, needs keys | Calls & Meetings | Paid per minute or free self-hosted | None |
| 4 | Private web search (SearXNG) | Works end to end on the VM | Integrations | Free | None |
| 5 | Email: Gmail, Outlook, Zoho | Built, needs OAuth apps | Inbox | Free | Google verification for the restricted Gmail scope; Microsoft admin consent |
| 6 | WhatsApp Business | Built, needs a Meta app (entered in the app) | WhatsApp > Setup | Meta per-conversation pricing | Meta business verification for higher limits |
| 7 | Zoom and Microsoft Teams transcripts | Built, needs OAuth apps | Calls & Meetings | Free apps; paid Zoom/Microsoft plans for recording | Microsoft admin consent |
| 8 | Phone push (FCM) | Built, needs a Firebase key | Settings > Notifications (phones register themselves) | Free | None |
| 9 | Notification email (SMTP) | Built, needs a mailbox | Automatic | Free with your mailbox | None |
| 10 | Sign in with Google and Apple | Built, needs Keycloak setup | Sign-in screens | Google free; Apple USD 99/year | Google app publishing |
| 11 | Error reporting (GlitchTip) | Built, opt-in on the VM | Integrations (status) | Free self-hosted | None |
| 12 | File storage (S3 / Backblaze B2) | Works end to end; set at install | Documents | B2: 10 GB free | None |
| 13 | Outbound REST webhook | Built, needs a URL | Integrations (workspace token) | Free | None |
| 14 | Social media (Meta, LinkedIn, X, TikTok, YouTube) | Built, needs platform apps | Marketing | Free apps; X posting is paid | Meta App Review, LinkedIn CMA, TikTok audit, Google verification |
| 15 | Sales channels (Amazon, Noon, own store) | Built, keys entered in the app | Commerce | Free on ANUM's side | Amazon SP-API developer roles |
| 16 | Fleet and tyre data (Supabase or Firebase) | Built, keys entered in the app | Tyre planning | Free on ANUM's side | None |
| 17 | Developer mode (GitHub) | Built, needs your organization id | Developer | GitHub free; uses model budget | None |
| 18 | Off-site backups (Backblaze B2) | Works end to end; set at install | Server only | B2: 10 GB free | None |
| 19 | Monitoring (Beszel and GlitchTip) | Built, opt-in on the VM | Server only | Free | None |
| 20 | DuckDNS | Built, optional | Server only | Free | None |
| 21 | Calendar | Built in; no outside calendar sync yet | Calls & Meetings, mobile Calendar | Free | None |
| 22 | MCP servers | Adapter in the code only; nothing to connect yet | None | None | None |

## 1. AI model

**What it does.** Writes chat answers, plans tasks, sorts email and WhatsApp messages, drafts replies and reads camera frames in calls. Without it (`ANUM_MODEL_PROVIDER=mock`) answers are placeholders and production refuses to ship.

**Options.**

- **Claude (recommended for quality).** console.anthropic.com → **Settings → API keys → Create key**. In `.env`: `ANUM_MODEL_PROVIDER=anthropic`, `ANUM_MODEL_API_KEY=<the key>`, keep `ANUM_MODEL_NAME` and `ANUM_MODEL_BASE_URL` as in the example (ANUM then uses `https://api.anthropic.com`, a fast model for chat and a capable one for planning; [Model routing](model-gateway.md#model-routing)).
- **An OpenAI-compatible provider.** `ANUM_MODEL_PROVIDER=openai-compatible`, `ANUM_MODEL_BASE_URL=https://<provider>/v1`, `ANUM_MODEL_NAME=<model>`, `ANUM_MODEL_API_KEY=<key>`.
- **Ollama on the VM (free, private, slow).** `COMPOSE_PROFILES=ai` plus the Ollama lines in [Deploying on Oracle: AI model](deploy-oracle.md#ai-model), then `docker compose up -d`.

**Apply and check.** `docker compose up -d api`. The **AI model** row turns **Connected**; an organization administrator's **Check connection** reaches the provider's host. Ask the assistant a question. A workspace owner can also choose a different model for one workspace under **Settings** ([Model gateway](model-gateway.md)).

**Cost and approval.** Claude and OpenAI bill per token; set a monthly budget under **Model budgets**. Ollama is free. No approval.

## 2. Voice: speech-to-text and text-to-speech

**What it does.** Server recognition and natural voices for voice calls. Optional: without it, calls use the browser's and phone's own speech.

**Steps.** For OpenAI: platform.openai.com → **API keys** → create a key. In `.env`: `ANUM_SPEECH_STT_BASE_URL=https://api.openai.com/v1`, `ANUM_SPEECH_STT_API_KEY=<key>`, `ANUM_SPEECH_STT_MODEL=whisper-1`, `ANUM_SPEECH_TTS_BASE_URL=https://api.openai.com/v1`, `ANUM_SPEECH_TTS_API_KEY=<key>`, `ANUM_SPEECH_TTS_MODEL=<speech model>`, `ANUM_SPEECH_TTS_VOICE_EN=<voice>`, `ANUM_SPEECH_TTS_VOICE_AR=<voice>`, and the prices `ANUM_SPEECH_STT_USD_PER_MINUTE` / `ANUM_SPEECH_TTS_USD_PER_MILLION_CHARS` from the provider's price page (they are charged to the model budget). A self-hosted Whisper/Piper server with the same API works too (prices `0`; [Voice: speech engines](voice.md#speech-engines)).

**Apply and check.** `docker compose up -d api`. The **Server voice** row turns **Connected**; a half-filled pair shows **Needs attention** and names the missing line. Start a call: the engine picker offers "Server".

**Cost.** Paid per minute or per character with hosted providers; free self-hosted.

## 3. Meeting recording transcription

**What it does.** Turns uploaded meeting recordings into transcripts for summaries and follow-ups. Without it, transcript files and Zoom/Teams transcripts still work.

**Steps.** Hosted Whisper: `ANUM_TRANSCRIPTION_PROVIDER=openai-compatible`, `ANUM_TRANSCRIPTION_BASE_URL=https://api.openai.com/v1` (or `https://api.groq.com/openai/v1`), `ANUM_TRANSCRIPTION_MODEL=whisper-1` (Groq: `whisper-large-v3`), `ANUM_TRANSCRIPTION_API_KEY=<key>`. Deepgram (with speaker labels): console.deepgram.com → **API Keys**, then `ANUM_TRANSCRIPTION_PROVIDER=deepgram` and `ANUM_TRANSCRIPTION_API_KEY=<key>` ([Meetings](meetings.md)).

**Apply and check.** `docker compose up -d api`. The **Recording transcription** row turns **Connected**; upload a short recording in **Calls & Meetings**.

**Cost.** Paid per audio minute; free with a self-hosted Whisper server.

## 4. Private web search (SearXNG)

**What it does.** Lets agents search the web with the `web_search` tool without any search API key. It runs on the VM and is on after install.

**Steps.** Nothing to create. If the row says **Not set up**, run `sudo infra/oracle/update.sh` (it adds `SEARXNG_SECRET` and the container). A workspace owner can switch it off for one workspace under **Platform services → SearXNG web search → Manage** ([Web search](tools-and-integrations.md#web-search)).

**Cost.** Free.

## 5. Email: Gmail, Outlook and Zoho

**What it does.** One inbox for your company mailboxes: sync, AI sorting, drafted replies; every send waits for an approval ([Email](email.md)).

**Callback URL for all three:** `https://<API>/api/v1/email/oauth/callback` (shown with a Copy button on each row).

**Gmail / Google Workspace** (console.cloud.google.com):

1. Create or pick a project → **APIs & Services → Library** → enable **Gmail API**.
2. **Google Auth Platform → Branding**: app name `ANUM`, support email, your domain. **Audience**: **Internal** if only your Google Workspace users connect (no Google review needed), otherwise **External**. **Data Access** → add the scope `https://www.googleapis.com/auth/gmail.modify`.
3. **Clients → Create client → Web application** → **Authorized redirect URIs**: the callback URL above → **Create**. Copy the client ID and secret.
4. `.env`: `ANUM_EMAIL_GOOGLE_CLIENT_ID=...`, `ANUM_EMAIL_GOOGLE_CLIENT_SECRET=...`.

**Outlook / Microsoft 365** (entra.microsoft.com or portal.azure.com):

1. **App registrations → New registration**; account types to match `ANUM_EMAIL_MICROSOFT_TENANT` (`common` = any work, school or personal account).
2. **Redirect URI**: platform **Web**, the callback URL above.
3. **API permissions → Microsoft Graph → Delegated**: `offline_access`, `User.Read`, `Mail.ReadWrite`, `Mail.Send` → **Grant admin consent** for your company.
4. **Certificates & secrets → New client secret**; copy the value (shown once) and note its expiry date.
5. `.env`: `ANUM_EMAIL_MICROSOFT_CLIENT_ID=<Application (client) ID>`, `ANUM_EMAIL_MICROSOFT_CLIENT_SECRET=...`, optionally `ANUM_EMAIL_MICROSOFT_TENANT=<your tenant id>`.

**Zoho Mail** (api-console.zoho.com, or the console of your data centre):

1. **Add Client → Server-based Applications**; Homepage URL `https://<WEB>`; Authorized Redirect URI the callback URL above.
2. `.env`: `ANUM_EMAIL_ZOHO_CLIENT_ID=...`, `ANUM_EMAIL_ZOHO_CLIENT_SECRET=...`, `ANUM_EMAIL_ZOHO_DATA_CENTRE=com` (or `eu`, `in`, `sa`, ...: the ending of your `accounts.zoho.<dc>` address).

**Apply and check.** `docker compose up -d api`. The row turns **Ready to connect**. Press **Open Inbox → Connect**, sign in to the mailbox, and the row turns **Connected** with the number of connected mailboxes. A mailbox that needs signing in again shows **Needs attention**.

**Cost and approval.** Free. Gmail's `gmail.modify` is a *restricted* scope: an External app stays in "Testing" (refresh tokens expire after 7 days, at most 100 test users) until Google's verification, which for restricted scopes includes a paid third-party security assessment. For your own company's Google Workspace, choose **Internal** and no verification is needed. Microsoft needs an administrator's consent in your tenant. Zoho needs nothing.

## 6. WhatsApp Business

**What it does.** Your business number's customer chats in ANUM, sorted, with suggested replies; replies and templates go out through approvals ([WhatsApp](whatsapp.md)).

**Steps.** Everything is entered in the app (encrypted in the database), nothing in `.env`:

1. business.facebook.com: create the business portfolio and start **Business verification** (Security center).
2. developers.facebook.com/apps → **Create app** → type **Business** → add **WhatsApp**; add and verify your number; copy the **Phone number ID** and **WhatsApp Business Account ID**.
3. **Business settings → System users** → create an Admin system user, assign the app and the WhatsApp account, **Generate token** with `whatsapp_business_messaging` and `whatsapp_business_management`, no expiry.
4. **App settings → Basic** → copy the **App secret**.
5. In ANUM: **WhatsApp → Setup**: enter the IDs, token, app secret, press **Generate** for a verify token, **Connect**.
6. In the Meta app: **WhatsApp → Configuration → Webhook**: Callback URL `https://<API>/api/v1/whatsapp/webhook` (Copy on the row), the same verify token → **Verify and save**; subscribe to **messages**.

**Check.** The row turns **Connected**; **Send a test to my number** in WhatsApp → Setup. A token Meta refuses shows **Needs attention**.

**Cost and approval.** Meta charges per conversation or message category outside the free customer-service window (current price list on Meta's site). Business verification raises sending limits. If the number is on the WhatsApp Business phone app today, read [Your number today](whatsapp.md#your-number-today) first.

## 7. Zoom and Microsoft Teams transcripts

**What it does.** Imports meeting transcripts from Zoom cloud recordings and Teams meetings for summaries, action items and follow-up emails ([Meetings: connectors](meetings.md#connectors)).

**Callback URL for both:** `https://<WEB>/meetings/connected` (set automatically from `ANUM_WEB_HOST`).

**Zoom** (marketplace.zoom.us → **Develop → Build App → General App**, user-managed): Redirect URL and allow list: the callback URL; scopes `cloud_recording:read:list_user_recordings`, `cloud_recording:read:list_recording_files`, `user:read:user`. Turn on cloud recording with **audio transcript** in Zoom. `.env`: `ANUM_ZOOM_CLIENT_ID=...`, `ANUM_ZOOM_CLIENT_SECRET=...`.

**Teams** (entra.microsoft.com → **App registrations → New registration**): Web redirect URI the callback URL; delegated Graph permissions `OnlineMeetings.Read`, `OnlineMeetingTranscript.Read.All`, `User.Read`, `offline_access` → **Grant admin consent**; new client secret. Turn on transcription in Teams. `.env`: `ANUM_MICROSOFT_CLIENT_ID=...`, `ANUM_MICROSOFT_CLIENT_SECRET=...`, `ANUM_MICROSOFT_TENANT=organizations` (or your tenant id). You may reuse the email app registration if you add both redirect URIs and all permissions.

**Apply and check.** `docker compose up -d api`. Rows turn **Ready to connect**; each person connects their own account in **Calls & Meetings → Connect**, and the row shows **Connected** for them.

**Cost.** Free apps; Zoom cloud recording needs a paid Zoom plan, Teams transcription a Microsoft 365 business plan.

## 8. Phone push (Firebase Cloud Messaging)

**What it does.** "Approval needed" and result alerts on Android and iPhone ([Notifications](notifications.md)).

**Steps.** console.firebase.google.com → your ANUM project → **Project settings → Service accounts → Generate new private key** (a JSON file). On the VM: `jq -c . key.json` prints it on one line; put it in `.env` as `ANUM_FCM_SERVICE_ACCOUNT_JSON='<that line>'` (single quotes), then delete the file.

**Apply and check.** `docker compose up -d api`. The row turns **Connected**; on a phone, allow notifications in the ANUM app; **Settings → Notifications** lists the phone.

**Cost.** Free.

## 9. Notification email (SMTP)

**What it does.** Emails reminders, assignments and follow-ups from your own mailbox. Without it, notifications stay in the app.

**Steps.** Create an app password for a mailbox you own (Google Workspace: **Security → App passwords** with 2-Step Verification on; Microsoft 365: SMTP AUTH must be allowed for the mailbox; Zoho: **Security → App Passwords**). `.env`: `ANUM_SMTP_HOST` (`smtp.gmail.com`, `smtp.office365.com`, `smtp.zoho.com`), `ANUM_SMTP_PORT=587`, `ANUM_SMTP_SECURITY=starttls`, `ANUM_SMTP_USERNAME=<mailbox>`, `ANUM_SMTP_PASSWORD=<app password>`, `ANUM_SMTP_FROM=<the sender address>`.

**Apply and check.** `docker compose up -d api`. The row turns **Connected**; **Check connection** reaches the mail server; assign yourself a task to receive an email. Oracle Cloud blocks outgoing port 25, so use 587 or 465.

**Cost.** Free with your mailbox.

## 10. Sign in with Google and Apple

**What it does.** "Continue with Google" and "Continue with Apple" on the sign-in screens, through Keycloak.

**Steps.** Follow [Deploying on Oracle: Google and Apple sign-in](deploy-oracle.md#google-and-apple-sign-in). Callback URLs: `https://<AUTH>/realms/anum/broker/google/endpoint` and `https://<AUTH>/realms/anum/broker/apple/endpoint` (on the rows). Then `bash ~/anum/infra/oracle/keycloak-2fa.sh google` (or `apple`), set the line `keycloak-2fa.sh status` prints (for example `ANUM_SIGN_IN_PROVIDERS=google,apple`) and `ANUM_PRIVACY_POLICY_URL`.

**Apply and check.** `docker compose up -d api`. The rows turn **Connected** and the sign-in screen shows the buttons.

**Cost and approval.** Google is free; publish the Google app (Audience → **Publish app**). Apple needs the Apple Developer Program (USD 99 a year).

## 11. Error reporting (GlitchTip or Sentry)

**What it does.** Collects API and web errors (types, routes and tenant id only, no personal data) in GlitchTip on the VM.

**Steps.** Two more host names pointing at the VM, then `bash ~/anum/infra/oracle/monitoring.sh enable`, then `monitoring.sh dsn` (API) and `monitoring.sh web-dsn` (web). These fill `ANUM_SENTRY_DSN` and `ANUM_WEB_SENTRY_DSN` ([Monitoring the Oracle VM](monitoring-oracle.md)).

**Check.** The **Error reporting** row turns **Connected**; GlitchTip shows events at `https://<ANUM_ERRORS_HOST>`.

**Cost.** Free (uses VM memory).

## 12. File storage (S3 or Backblaze B2)

**What it does.** Keeps uploaded documents in a versioned bucket instead of the VM disk. Set during install.

**Steps.** backblaze.com → **B2 Cloud Storage → Buckets → Create a Bucket** (private, versioning on); **Application Keys → Add a New Application Key** for that bucket only, Read and Write. `.env`: `ANUM_S3_ENDPOINT=https://s3.<region>.backblazeb2.com`, `ANUM_S3_REGION=<region>`, `ANUM_S3_BUCKET=...`, `ANUM_S3_ACCESS_KEY=<keyID>`, `ANUM_S3_SECRET_KEY=<applicationKey>` ([Deploying on Oracle: File storage](deploy-oracle.md#file-storage)).

**Check.** The **File storage** row is **Connected**; upload a file in **Documents**.

**Cost.** B2: the first 10 GB are free, then a few dollars per TB a month.

## 13. Outbound REST webhook

**What it does.** Lets an approved agent action (`external.action`, always high risk) call one system of yours, for example a Make, Zapier or n8n scenario that updates a spreadsheet or your fleet system ([External REST webhook](tools-and-integrations.md#external-rest-webhook)).

**Steps.** Create the receiving webhook in your tool and copy its https URL. `.env`: `ANUM_EXTERNAL_WEBHOOK_URL=https://...` and, if it wants a bearer token, `ANUM_EXTERNAL_WEBHOOK_API_KEY=...`. A workspace owner can store a workspace's own token instead under **Platform services → External REST webhook → Manage**.

**Apply and check.** `docker compose up -d api`. The row turns **Connected**; **Check connection** opens a connection to that host without posting anything.

**Cost.** Free on ANUM's side.

## 14. Social media marketing

**What it does.** Plan, approve and publish posts to Facebook, Instagram, LinkedIn, X, TikTok and YouTube, with a weekly report ([Marketing](marketing.md)).

**Callback URL for every platform:** `https://<WEB>/marketing/callback` (set automatically from `ANUM_WEB_HOST`; `ANUM_MARKETING_PUBLIC_API_URL` is set to `https://<API>` so Instagram and TikTok can fetch media).

| Platform | Console and what to create | `.env` lines | Approval before other businesses can connect |
|---|---|---|---|
| Facebook + Instagram | developers.facebook.com → app type **Business**, **Facebook Login for Business**, redirect the callback URL | `ANUM_MARKETING_META_APP_ID`, `ANUM_MARKETING_META_APP_SECRET` | App Review for the Pages and Instagram publishing permissions; Business Verification |
| LinkedIn | linkedin.com/developers → app linked to your Company Page; products "Sign In with LinkedIn using OpenID Connect" and "Share on LinkedIn" | `ANUM_MARKETING_LINKEDIN_CLIENT_ID`, `ANUM_MARKETING_LINKEDIN_CLIENT_SECRET` | "Community Management API" for company page posts |
| X | developer.x.com → project and app, OAuth 2.0 confidential client, read and write | `ANUM_MARKETING_X_CLIENT_ID`, `ANUM_MARKETING_X_CLIENT_SECRET` | A paid access tier for regular posting |
| TikTok | developers.tiktok.com → app with Login Kit and Content Posting API | `ANUM_MARKETING_TIKTOK_CLIENT_KEY`, `ANUM_MARKETING_TIKTOK_CLIENT_SECRET`; `ANUM_MARKETING_TIKTOK_DIRECT_POST=true` only after the audit | App audit for Direct Post (until then videos arrive as drafts) |
| YouTube | console.cloud.google.com → enable YouTube Data API v3, Web OAuth client, redirect the callback URL | `ANUM_MARKETING_GOOGLE_CLIENT_ID`, `ANUM_MARKETING_GOOGLE_CLIENT_SECRET` | Google verification for `youtube.upload`; YouTube API audit for public uploads |

**Apply and check.** `docker compose up -d api`. Each row turns **Ready to connect**; **Open Marketing → Connect** signs in to the platform, then the row shows **Connected**. An account that needs signing in again shows **Needs attention**.

**Cost.** The apps are free; X posting needs a paid tier.

## 15. Sales channels: Amazon, Noon and your own store

**What it does.** Orders, listings, stock, returns and fees in one place, with approvals for every price or stock change ([Commerce](commerce.md)).

**Steps.** Credentials are entered in the app (encrypted per connection), nothing in `.env`:

- **Amazon (Amazon.ae / Amazon.sa):** Seller Central → **Apps and Services → Develop Apps** → developer profile (private developer) with the roles you need → add an SP-API app client (LWA client ID and secret) → **Authorize app** (refresh token). In ANUM: **Commerce → Add connection → Amazon**.
- **Noon:** the API credentials from Noon's partner portal → **Commerce → Add connection → noon** ([Commerce: noon](commerce.md#noon)).
- **Your own store:** ANUM 360 store, a REST API, Supabase or a signed webhook → **Commerce → Add connection → Own store**.
- Optional: `ANUM_COMMERCE_SYNC_ENABLED=true` in `.env` syncs connections in the background (otherwise **Sync now**).

**Check.** The row turns **Connected** with the number of channels; a channel whose last sync failed shows **Needs attention**. **Test connection** on each channel in Commerce.

**Cost and approval.** Free on ANUM's side. Amazon approves the SP-API roles; buyer details need a restricted role.

## 16. Fleet and tyre data (Supabase or Firebase)

**What it does.** Reads vehicles, tyres, inspections and costs from your TyrePulse (Supabase) or Firebase project for tyre planning and reports ([Tyre planning](tyre-planning.md)).

**Steps.** In ANUM: **Tyre planning → Setup**: choose Supabase PostgreSQL (a read-only database user's connection string), Supabase REST or Firestore (a read-only service account), map the tables, **Sync**.

**Check.** The row turns **Connected** once a data source is saved.

**Cost.** Free on ANUM's side.

## 17. Developer mode (GitHub)

**What it does.** Ask for a change to one of your apps by voice or text; ANUM opens a GitHub pull request, runs the checks and waits for your approval to merge ([Developer mode](developer-mode.md)).

**Steps.** `.env`: `ANUM_DEVELOPER_TENANT_ID=<your organization id>` (shown in the workspace switcher at the top of the web app, before the `/`), optionally `ANUM_DEVELOPER_WORKSPACE_ID` (after the `/`) and `ANUM_DEVELOPER_GITHUB_TOKEN` (GitHub → **Settings → Developer settings → Fine-grained tokens**, only the repositories ANUM may change). Then `docker compose up -d api`, open **Developer → Projects** and add the repository (or store a token per project there).

**Check.** The row turns **Ready to connect**; **Test connection** on the project in Developer.

**Cost.** GitHub is free for this; each request uses the model budget.

## 18. Off-site backups (Backblaze B2)

Server only (not in the app). A second B2 bucket with its own key, encrypted on the VM before upload. `.env`: `ANUM_BACKUP_S3_ENDPOINT`, `ANUM_BACKUP_S3_REGION`, `ANUM_BACKUP_S3_BUCKET`, `ANUM_BACKUP_S3_ACCESS_KEY`, `ANUM_BACKUP_S3_SECRET_KEY`; keep `ANUM_BACKUP_ENCRYPTION_PASSPHRASE` in your password manager. Check with `sudo ./backup.sh backup` and `sudo ./backup.sh list` from `~/anum/infra/oracle`, and run `sudo ./backup.sh drill` monthly ([Deploying on Oracle: Backups](deploy-oracle.md#backups)). Free within B2's 10 GB.

## 19. Monitoring (Beszel and GlitchTip)

Server only. `bash ~/anum/infra/oracle/monitoring.sh enable` adds Beszel (CPU, memory, disk and containers) and GlitchTip (errors, see section 11) behind Caddy with a login ([Monitoring the Oracle VM](monitoring-oracle.md)). An external uptime check (for example a free Uptime Kuma or UptimeRobot monitor on `https://<API>/health`) tells you when the whole VM is down. Free.

## 20. DuckDNS

Server only, optional: free host names when you have no domain. duckdns.org → sign in → create the names → copy the token at the top. `.env`: `DUCKDNS_TOKEN=...`, `DUCKDNS_DOMAINS=anum-app-yourname,anum-api-yourname,anum-auth-yourname`, then run `install.sh` again so it adds the timer that keeps the addresses on the VM's IP. Check with `systemctl list-timers | grep duckdns`. Free.

## 21. Calendar

The workspace calendar is built in (events, attendees, next-step suggestions; [Calendar](calendar.md)). It does not sync with Google Calendar or Outlook yet: that is a proposed next integration.

## 22. MCP servers

The runtime has an MCP adapter in code (`McpToolAdapter`), but no MCP server can be configured yet: there is nothing to connect. Adding one would go through the same tool policy, approvals and audit as every tool ([MCP strategy](tools-and-integrations.md#mcp-strategy)).

## Security notes

- Keys live only in `infra/oracle/.env` (mode 600, never in git) or encrypted in the database with `ANUM_SECRETS_KEY`. The app shows setting names, never values.
- Callback URLs are fixed by the server from your host names; nothing a user types changes where ANUM connects.
- After rotating `ANUM_SECRETS_KEY`, run `docker compose run --rm retention python -m anum_api.rotate_secrets` ([Runbooks](runbooks.md#rotating-anum_secrets_key)).
- If a key leaks: revoke it in the provider's console first, create a new one, update `.env`, `docker compose up -d api`.
