# Privacy policy template

> **This is a template, not legal advice.** It describes what the ANUM software stores and sends so that the owner of a deployment can write their own policy. Replace every `<placeholder>`, delete what your deployment does not use, and have it reviewed by someone qualified for your jurisdiction (for example the GDPR, UK GDPR or CCPA) before you publish it. Then set `ANUM_PRIVACY_POLICY_URL` to its public address so the apps link to it ([Privacy](privacy.md#configuration)), and use the same address in the store listings ([Store data safety](store-data-safety.md)).

---

**Privacy policy for `<service name>`**

Last updated: `<date>`

`<organization name>` ("we") runs `<service name>`, built on the ANUM agent workbench, for `<who may use it: our employees / our customers>`. This policy explains what we store, why, where, for how long, who it is shared with and what you can do about it.

Contact: `<owner contact>` (email or postal address). `<Data protection officer or EU/UK representative, if you need one.>`

## 1. What we collect

| Data | What it is | Where it comes from |
|---|---|---|
| Account | Your user id, name and email address, sign-in method and two-factor settings. | You, through our sign-in service (`<Keycloak hosted by us / identity provider name>`). |
| Workspace membership | Which organization and workspaces you belong to and your role. | Your administrator. |
| Assistant conversations | The questions you type and the assistant's answers. | You. |
| Memories | Facts the assistant remembers from your chats (private to you) and notes you add to tasks (shared with your workspace). | You, and an automatic step after each reply that picks out facts you stated. |
| Voice | Transcripts of what you say to the voice assistant and its replies. Audio is turned into text on your device or by your device's speech service; ANUM receives text, not recordings. | You. |
| Tasks, runs and approvals | Tasks you create, what the agent did, approvals you decide. | You and the agent. |
| Files | Files you upload and their metadata. | You. |
| Settings | Notification preferences and your own preferences, such as whether the assistant remembers things from your chats. | You. |
| Technical data | Request logs with ids, times and error codes, security audit records (who did what, with ids and counts), and `<error reports, if ANUM_SENTRY_DSN is set>`. They do not contain your messages. | Our servers. |

We do not use advertising identifiers, do not track you across other apps or websites and do not sell your data.

## 2. Why we use it (legal bases)

- To provide the service you or your organization asked for: answering questions, running tasks, keeping your history (`<contract / legitimate interests>`).
- To keep it secure and auditable: sign-in, permissions, approvals, audit records and error reports (`<legitimate interests / legal obligation>`).
- `<Anything else you do, such as support or billing.>`

## 3. Where it is stored

On servers we control at `<hosting provider and region, for example: Oracle Cloud, eu-frankfurt-1>`: a PostgreSQL database, object storage for files `<S3-compatible store and region>`, and `<backup location>`. Data is encrypted in transit (HTTPS). Stored model-provider keys are encrypted. `<Describe disk or backup encryption you use.>`

## 4. Who it is sent to

To answer you, the text of your messages, voice questions, task prompts and the context the assistant uses (recent turns, relevant memories and workspace files) is sent to the language model configured for your workspace. Keep the line that matches your deployment:

- **Anthropic** (Claude), `<Anthropic's region and terms>`, under `<their commercial terms / DPA>`.
- **An OpenAI-compatible service**, `<provider name and region>`, under `<their terms / DPA>`.
- **Ollama on our own servers**: your text does not leave our infrastructure for answers.

Also, only if we turn them on:

- web search through our self-hosted search service, which forwards search queries to public search engines `<list>`;
- integrations your workspace connects (for example `<ticketing or chat tools>`), which receive the data of an action only after a person approves it;
- error reports to `<GlitchTip or Sentry host>`, with user identity and request content removed.

We do not share your data with anyone else except when the law requires it.

## 5. How long we keep it

| Data | Kept |
|---|---|
| Assistant conversations | Deleted after `<ANUM_CONVERSATION_RETENTION_DAYS, default 30>` days without activity, or earlier when you delete them. |
| Voice transcripts | You choose per session: deleted when the session ends, after 30 days, or kept until you delete them. |
| Private memories | Until you delete them. |
| Task notes, tasks, approvals and files | For as long as your workspace keeps them; `<your workspace retention>`. |
| Audit records | `<period>`; they contain ids and counts, not content. |
| Account | Until your administrator closes it, then `<period>`. |
| Backups | Up to `<backup retention>` after deletion from the live system. |

## 6. Your rights

You can, at any time, in the app under **Settings → Privacy**:

- **see** what is stored about you;
- **download** your data as a file (access and portability);
- **delete** your conversations, private memories, voice transcripts, notification preferences and other settings.

Records you created that your whole workspace relies on (tasks, approvals, shared notes and files) are kept; ask a workspace owner to remove them. To close your account, correct your details or object to processing, contact `<owner contact>`. `<If the GDPR applies: you may also complain to your data protection authority, <authority>.>` We answer within `<30 days>`.

## 7. Children

The service is not meant for children under `<13 / 16>`.

## 8. Changes

We will post changes here and update the date above. `<How you notify users of significant changes.>`
