# Subprocessor List

> **DRAFT, owner and a lawyer must review before publishing.** Built from the code and deployment configuration on 18 October 2026. The "In use" column must reflect what the production deployment actually has turned on when this page is published; services marked "Optional" are only used once an operator or a workspace owner configures them.

Last updated: {{EFFECTIVE_DATE}}

{{LEGAL_ENTITY_NAME}} uses the service providers below to run ANUM. Each receives only what its job needs. We will announce a new subprocessor on this page {{SUBPROCESSOR_NOTICE_DAYS}} days before it starts processing customer personal data, as described in the [Data Processing Addendum](data-processing-addendum.md).

## Infrastructure we run the Service on

| Provider | What it does for ANUM | Personal data it handles | Location | In use |
|---|---|---|---|---|
| Oracle Cloud Infrastructure (Oracle Corporation) | Hosts the virtual machine that runs the web app, the API, the database, the sign-in service and the background jobs. | All Service data, including the database and local backups. | {{HOSTING_REGION}} | {{IN_USE_ORACLE}} |
| Backblaze, Inc. (Backblaze B2) | Stores workspace files (with versioning) and the off-site database backups, which are encrypted on our server before upload. | File contents and metadata; encrypted backups of all Service data. | {{B2_REGION}} | {{IN_USE_B2}} |
| Let's Encrypt (Internet Security Research Group) | Issues the HTTPS certificates for our host names. | Host names only; no customer data. | United States | {{IN_USE_LETSENCRYPT}} |
| DuckDNS | Points our host names at the server, if the deployment uses DuckDNS names instead of its own domain. | The server's IP address only; no customer data. | {{DUCKDNS_LOCATION}} | {{IN_USE_DUCKDNS}} |
| Render Services, Inc. | Alternative hosting for the API, web app, sign-in service and database. | All Service data, if this path is used. | {{RENDER_REGION}} | {{IN_USE_RENDER}} |

## AI model providers

The workspace's model configuration decides which one receives prompts. With Ollama on our own server, prompts do not leave our infrastructure.

| Provider | What it does | Personal data it handles | Location | In use |
|---|---|---|---|---|
| Anthropic, PBC (Claude API) | Answers assistant questions, plans and runs tasks, summarizes and drafts. | The text of each request: questions, conversation context, relevant memories and file excerpts, tool results. | {{ANTHROPIC_REGION}} | {{IN_USE_ANTHROPIC}} |
| An OpenAI-compatible provider: {{OPENAI_COMPATIBLE_PROVIDER}} | The same, when configured instead of Anthropic. | The same. | {{OPENAI_COMPATIBLE_REGION}} | {{IN_USE_OPENAI_COMPATIBLE}} |
| Speech services: {{SPEECH_PROVIDER}} | Server-side speech to text and text to speech for voice calls and meeting transcription, if configured. Without it, devices do their own speech. | Audio and transcripts of those calls and meetings. | {{SPEECH_REGION}} | {{IN_USE_SPEECH}} |

## Optional services a workspace or operator turns on

These are used only after an operator sets them up or a workspace owner connects an account. When a workspace connects its own account with a provider (for example its own Brevo, Twilio or WhatsApp Business account), that provider usually acts for the customer under the customer's own agreement with it.

| Provider | What it does | Personal data it handles | Location | In use |
|---|---|---|---|---|
| Brevo (Sendinblue SAS) | Email campaigns sent from the workspace's own Brevo account, each send approved by an owner. | Contact list names and counts, campaign content and reports; recipients' details are held in the customer's Brevo account. | {{BREVO_REGION}} | {{IN_USE_BREVO}} |
| Twilio Inc. | Phone calls placed from the browser, recordings and AI calls, using the workspace's own Twilio account. | Phone numbers, call audio and recordings, call metadata. | {{TWILIO_REGION}} | {{IN_USE_TWILIO}} |
| Meta Platforms (WhatsApp Business Cloud API) | Sends and receives the workspace's WhatsApp messages. | Phone numbers, names and message content. | {{META_REGION}} | {{IN_USE_WHATSAPP}} |
| Google (Firebase Cloud Messaging), with Apple Push Notification service for iPhones | Delivers push notifications to registered phones. | Push tokens and a fixed notification title and sentence (such as "Approval needed"); never workspace content. | {{FCM_REGION}} | {{IN_USE_FCM}} |
| Google, Microsoft, Zoho (email) | Reads mailbox metadata and sends replies for mailboxes a user connects. | Email metadata, drafts and sent replies. | The provider's own locations | Per mailbox |
| Zoom, Microsoft Teams | Imports meeting transcripts and recordings the user connects. | Meeting transcripts and recordings. | The provider's own locations | Per user |
| Facebook, Instagram, LinkedIn, X, TikTok, YouTube | Publishes approved posts and reads post statistics for connected accounts. | Post content and statistics. | The provider's own locations | Per workspace |
| Amazon, Noon, the customer's own store | Reads orders and listings for connected sales channels. | Order and customer details the channel returns. | The provider's own locations | Per workspace |
| GitHub | Developer mode works on the customer's repositories. | Repository content the customer chooses. | United States | Per workspace |
| The customer's SMTP mailbox | Sends notification emails from the mailbox the operator configures. | Recipient address and notification text. | The mailbox provider's locations | {{IN_USE_SMTP}} |

## Run on our own server (not subprocessors)

These run inside our deployment and do not send customer data to a third party: PostgreSQL (database), Keycloak (sign-in), Valkey (cache and rate limits), Temporal and NATS (background work and events, if enabled), SearXNG (private web search; it forwards search queries, not workspace content, to public search engines), coturn (relay for in-app calls, if enabled), Beszel and GlitchTip (monitoring and error reports, if enabled), Ollama (local AI model, if enabled) and Caddy (HTTPS).

## Loaded by your browser

The in-browser voice downloads its model files from Hugging Face and a pinned runtime from the jsDelivr CDN. These requests go from the user's browser directly to those services; ANUM sends them no customer data.
