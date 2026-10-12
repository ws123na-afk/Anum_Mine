# Social media marketing

ANUM plans, approves, publishes and measures social media posts for the owner's apps (TyrePulse, ANUM and others) and the tyre e-commerce business, in English and Arabic for a Gulf audience. Posts go only to accounts connected to the workspace through each platform's official API; nothing is published without an approval (or an owner-approved campaign auto-post).

Code: `services/api/anum_api/marketing/` (API), `services/api/anum_api/db/marketing_repository.py` (PostgreSQL), migration `0040_marketing`, `apps/web/src/MarketingView.tsx` (web), `apps/mobile/lib/features/marketing/` (Flutter).

## Platforms

| Platform | API | What ANUM posts | Notes |
| --- | --- | --- | --- |
| Facebook Pages | Graph API (`/{page}/feed`, `/photos`, `/videos`) | text, link, up to 10 images, one video | Page tokens from a long-lived user token do not expire. |
| Instagram professional accounts | Graph API content publishing (`/{ig-user}/media`, `/media_publish`) | one image, a carousel of up to 10 images, a Reel | Media is fetched by Instagram from a signed link on the API (`ANUM_MARKETING_PUBLIC_API_URL`); images must be JPEG, 4:5 to 1.91:1. |
| LinkedIn | Posts API (`/rest/posts`, `/rest/images`, `/rest/videos`) | member and organization (Company Page) posts, images, one video, an article link | Commentary is escaped as "little text"; `#tags` become hashtag elements. Post statistics exist for organizations only. |
| X | API v2 (`/2/tweets`, `/2/media/upload`) with OAuth 2.0 user context (PKCE) | text up to 280 weighted characters, up to 4 images or one video | **Posting needs a paid API access tier** for the project (Basic or higher, or pay-per-use credits). A refusal says so (`x_access_tier`). |
| TikTok | Content Posting API (`/v2/post/publish/...`), FILE_UPLOAD | one vertical video | **Until TikTok's app audit approves Direct Post, videos are uploaded to the creator's TikTok inbox as drafts** (status `sent_to_inbox`); the creator finishes in TikTok. Set `ANUM_MARKETING_TIKTOK_DIRECT_POST=true` once audited. |
| YouTube | Data API v3 resumable upload | one video (a vertical video up to 3 minutes is a Short) | An upload costs 1,600 of the default 10,000 daily quota units. Videos from an unaudited API project stay private until the project passes YouTube's audit. |

Each platform's limits (text length and how it is counted, hashtags, media count, types, sizes, aspect ratios, video length, titles) live in `marketing/platforms.py`; every post carries its `issues`, the composer shows them live (`apps/web/src/lib/marketing.ts` mirrors the table) and a post with an error cannot be scheduled.

## Platform apps (owner setup)

Create one app per platform and set these on the API (names only; values are secrets in the deployment's secret store, never in code or chat). Register `ANUM_MARKETING_OAUTH_REDIRECT_URL` (the web app's origin plus `/marketing/callback`, or any path of the web app) as the redirect URL in every app.

| Platform | Where | Settings | Review before public use |
| --- | --- | --- | --- |
| Facebook + Instagram | Meta for Developers: an app of type Business, Facebook Login for Business | `ANUM_MARKETING_META_APP_ID`, `ANUM_MARKETING_META_APP_SECRET`, optional `ANUM_MARKETING_META_GRAPH_VERSION` | App Review for `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `read_insights`, `instagram_basic`, `instagram_content_publish`, `instagram_manage_insights`, `business_management`; Business Verification. |
| LinkedIn | LinkedIn Developer Portal: an app linked to the Company Page | `ANUM_MARKETING_LINKEDIN_CLIENT_ID`, `ANUM_MARKETING_LINKEDIN_CLIENT_SECRET`, optional `ANUM_MARKETING_LINKEDIN_VERSION` | Products "Sign In with LinkedIn using OpenID Connect" and "Share on LinkedIn" (member posts); "Community Management API" (organization posts and statistics, approved by LinkedIn). |
| X | X Developer Portal: a project and app with OAuth 2.0 (confidential client, read and write) | `ANUM_MARKETING_X_CLIENT_ID`, `ANUM_MARKETING_X_CLIENT_SECRET` | A paid access tier for posting. |
| TikTok | TikTok for Developers: an app with Login Kit and Content Posting API | `ANUM_MARKETING_TIKTOK_CLIENT_KEY`, `ANUM_MARKETING_TIKTOK_CLIENT_SECRET`, `ANUM_MARKETING_TIKTOK_DIRECT_POST` | App audit for Direct Post (until then: inbox drafts). |
| YouTube | Google Cloud console: YouTube Data API v3, an OAuth client (web application) and consent screen | `ANUM_MARKETING_GOOGLE_CLIENT_ID`, `ANUM_MARKETING_GOOGLE_CLIENT_SECRET` | OAuth verification for `youtube.upload`; the YouTube API audit for public uploads. |

Also set `ANUM_MARKETING_PUBLIC_API_URL` (the API's public HTTPS origin) for Instagram. Other settings: `ANUM_MARKETING_MAX_ATTEMPTS` (3), `ANUM_MARKETING_MAX_MEDIA_BYTES`, `ANUM_MARKETING_MEDIA_LINK_SECONDS` (3600), `ANUM_MARKETING_HTTP_TIMEOUT_SECONDS`. `GET /api/v1/marketing/setup` lists what is missing per platform (names only, never values) and each platform's review requirements.

## Connected accounts

- **Connect** (owners, `marketing:manage`): `POST /oauth/start {provider}` returns the platform's consent URL. The OAuth `state` is encrypted with `ANUM_SECRETS_KEY`, bound to the caller's tenant, workspace and user, carries the PKCE verifier (X, TikTok, Google) and expires after 15 minutes. The platform returns the browser to the web app, which calls `POST /oauth/callback {code, state}`; a state from another user or workspace is refused (`403`). One Meta login connects every Page the owner chose and each Page's linked Instagram account; one LinkedIn login connects the member and the organizations they administer. In Flutter, Connect opens the browser and the web app finishes the connection.
- **Tokens** are stored only as Fernet ciphertexts (`marketing_accounts.access_token_ciphertext`, `refresh_token_ciphertext`) and never returned. A token within 10 minutes of expiry is refreshed before use (LinkedIn, X, TikTok, Google). `python -m anum_api.rotate_secrets` re-encrypts them (reported under `marketing_accounts`).
- **Health**: `POST /accounts/{id}/check` refreshes if needed and calls the platform; a refusal marks the account `needs_reauth` (or `error`) with the platform's reason and the fix.
- **Revoke**: `DELETE /accounts/{id}` revokes the token at the platform where it offers that (LinkedIn, X, TikTok, Google; for Meta the note says to remove the app under Facebook Settings), forgets both tokens, keeps the row as `revoked` so history keeps its name, and returns every scheduled or pending post of that account to draft.

## Planner

- **Campaigns** (`/campaigns`, keyset paging, search, status filter): name, objective, product, brief, languages, platforms, dates, status and a UTM campaign name. Deleting is refused while posts of the campaign are queued; its posts stay without a campaign.
- **Posts** (`/posts`): one row per account and language; the composer creates the bilingual versions together (`group_id`). Search, status, platform and campaign filters with keyset paging; `from`/`to` returns a calendar window (at most 62 days). Media are files from Files (images and, now, `.mp4`, `.mov`, `.webm`); the API reads image sizes from the file, the client measures video size and length. The **UTM builder** adds `utm_source` (the platform), `utm_medium=social`, `utm_campaign` and `utm_content`; Google Play links keep them in `referrer`, App Store links get a `ct` campaign token (`POST /utm`, mirrored in the web composer).
- **Calendar** (web): a Sunday-first month grid; drag a post to another day, or use the move button and pick the day (keyboard), keeping its time of day. The approval binds the content, not the time, so moving an approved post keeps its approval (an auto-post outside the campaign window goes back to draft). On narrow screens the grid becomes an agenda list. Flutter shows the next 30 days as an agenda with a date and time picker.
- **Brand voice and defaults** (`/preferences`, owners): brand voice text, time zone (default `Asia/Riyadh`) and default English and Arabic hashtags.

## Web page layout

Marketing follows the shared [page layout](design/anum360.md#page-layout): one header row with **New post**, then a summary band over every tab from `GET /marketing/analytics/summary` for the last 30 days (published with the 30 days before, reach and link clicks, engagement rate and its change, scheduled with any failed, and posts waiting for approval). **Posts** is a dense table (post, account, status, when) with search and filters; a row opens the post in a drawer with its preview, approval, schedule, retry and **Delete** (a secondary danger button). Next to it (below on narrower screens) a planning rail shows the best times to post (`GET /marketing/analytics/best-times`) and this week's report (`GET /marketing/reports/weekly`). **Accounts** lists the platforms and the connected accounts as compact tables with their state and actions.

## AI assist

`POST /assist/ideas` and `POST /assist/hashtags` (members and owners) call the workspace's model through `budgeted_model_gateway`: checked against the monthly budget before (`402` when used up) and metered after. The prompt asks for English and natural Arabic for Saudi and Emirati readers, never inventing prices or claims, and uses the brand voice (plus the owner's learned writing style: `assist.style_profile_lookup` defaults to `learning_store.learned_instructions(surface="task", tools=("marketing.draft_social_post",))`, a labelled block that is empty while learning is off or nothing was learned; see [Learning](learning.md)). Text pasted as `source_text` (a listing, a web page) is external data: it reaches the model only inside a labelled untrusted block (`prompt_provenance.label_untrusted`) and every draft built from it carries `untrusted_source`, shown as "Built from external text: review it". Without a model (the `mock` provider) or when the answer cannot be read, ideas come from bilingual templates and say so (`generator: "template"`). The ideas also return the best times to post.

## Publishing

1. `POST /posts/{id}/schedule {scheduled_at}` checks the post against the platform, then either
   - pauses a `marketing.schedule_post` tool call (risk **high**) for approval, bound by its payload hash to the account, the time and the exact composed text (`content_hash` of the words, title, hashtags, link, media and account); the post is `pending_approval`; or
   - when the post's campaign has an approved **auto-post** for that account and the time is inside the campaign, schedules it directly (`authorised_by: auto_post`).
2. An owner approves in Approvals (or on the post) with the displayed payload hash (`POST /api/v1/approvals/{id}/approve`); the tool then marks the post `scheduled` with `approved_hash`. A rejected or lapsed approval puts the post back to draft with the reason. Editing the content of a pending or scheduled post retires its approval and returns it to draft.
3. The automation scheduler (`ANUM_AUTOMATION_SCHEDULER_ENABLED=true`) runs the publisher on every pass (`register_scheduler_pass`). With PostgreSQL, due posts are discovered as `anum_maintenance` (ids, scope, status and times of `scheduled` posts only) and each is published as the application role inside its tenant's RLS context; the row lock and the `scheduled` to `publishing` step stop two replicas publishing the same post. `POST /posts/{id}/publish` publishes an approved post now.
4. Before posting, the publisher checks again that the content still hashes to `approved_hash`, the account is this workspace's own, still connected and of the post's platform, the auto-post still covers it, and no policy pack now denies `marketing.schedule_post` (governance deny, re-read at publish time).
5. Failures: network errors, timeouts, `429` and `5xx` are retried with a growing wait (2, 4, 8 minutes) up to `ANUM_MARKETING_MAX_ATTEMPTS`; anything else fails with the platform's reason and a fix (`auth_expired`, `permission`, `x_access_tier`, `tiktok_unaudited`, `youtube_quota`, `media_fetch`, `rejected`, ...). An auth failure also marks the account `needs_reauth`. `POST /posts/{id}/retry` retries a failed post while its approval still covers the content. A post left `publishing` for 15 minutes is shown as interrupted (it may already be live) instead of being retried blindly.

**Auto-post** (`POST /campaigns/{id}/auto-post`, owners): turning it on pauses a `marketing.enable_auto_post` call (high risk) naming the campaign and the accounts; after approval the campaign's posts publish at their time without a per-post approval, to those accounts only. Turning it off, pausing, completing or ending the campaign stops it; posts already scheduled under it go back to draft at publish time.

Every step is audited (`marketing.account.*`, `marketing.campaign.*`, `marketing.post.*`, with the approval id, payload hash and platform post id).

## Analytics

`POST /analytics/refresh` pulls the numbers of posts published in the last 1-90 days from each platform (Facebook post fields and insights, Instagram media insights, LinkedIn organization share statistics, X public and non-public metrics, TikTok video query, YouTube statistics) into `posts.metrics`: impressions, reach, engagement, link clicks, likes, comments, shares, saves, views, with a note where a platform does not report a number. `GET /analytics/summary` gives totals with the change on the previous period, per platform, per campaign, the best posts, a per-day series and the queue (scheduled, waiting for approval, failed). `GET /analytics/best-times` ranks weekday and hour slots by engagement rate from the workspace's own posts, with Gulf defaults (Sunday 21:00, Wednesday 20:00, Thursday 22:00, Riyadh time) until a platform has 5 measured posts. `GET /reports/weekly?language=en|ar` builds the weekly report (Sunday-first week); its `delivery` says which channels can carry it (`available` or `not_connected`).

### Weekly report

`POST /reports/weekly/deliver?language=en|ar {email, email_account_id?, whatsapp_conversation_id?}` sends the report through the existing integrations, best effort and never directly:

- **Email** ([Email](email.md)): a draft addressed to the chosen (or first connected, visible) mailbox's own address, then `request_send_approval`: a paused `send_email` call with exactly that text. It is sent only after approval. Needs `email:act`.
- **WhatsApp** ([WhatsApp](whatsapp.md)): a text to the chosen conversation (usually the owner's own chat with the business number) through the WhatsApp send route: it needs the 24-hour window and always waits for approval. Needs `whatsapp:reply`.

Each channel answers `requested` (with the approval id), `not_connected`, `not_requested`, `not_allowed` or `failed` with the reason; one channel failing never stops the other. The web Analytics tab offers "Email it to me" and a WhatsApp conversation picker; Flutter offers "Email it to me". Without either integration the report can still be copied.

## Assistant (chat, voice, tasks)

- `marketing.draft_social_post` (low risk): a task such as "Draft a tweet about TyrePulse pressure alerts" writes a draft for the first connected account of that platform.
- `marketing.schedule_post` (high risk): "Schedule my latest tweet" (or naming a `mkpost_...` id and a `YYYY-MM-DD HH:MM` time) pauses for approval showing the account, time and exact text.
- Autonomy ([Autonomy](autonomy.md)): every marketing tool declares the `marketing` category; `marketing.schedule_post` and `marketing.enable_auto_post` are the protected `publish` kind, which always asks however much autonomy is granted; the draft tool is `draft` and the summary `read`.
- `marketing.summary` (low risk, read-only): "ANUM, how did last week's Instagram posts do?". Voice answers it directly (`VoiceIntent.MARKETING_SUMMARY`, English and Arabic); the Assistant chat adds the numbers to the prompt as a labelled data block; `POST /assistant/summary` serves other clients.

The tools are chosen from the user's own words (the `anum.marketing` skill), never from retrieved data, and registered in the API's and the Temporal worker's tool registries.

## Permissions

| Action | Viewer | Member | Owner |
| --- | --- | --- | --- |
| See setup, accounts, posts, campaigns, analytics, reports | yes | yes | yes |
| Create and edit campaigns and posts, AI assist, ask to publish, move, cancel, retry, pull numbers | no | yes | yes |
| Connect, check, disconnect accounts; brand voice; auto-post | no | no | yes |
| Approve publishing (`approval:decide`) | no | no | yes |

## Data

Migration `0040_marketing` (re-chained by the lead when parallel branches land): `marketing_accounts`, `marketing_campaigns`, `marketing_posts`, `marketing_preferences`, all with forced RLS on `anum.tenant_id` and `anum.workspace_id`; `anum_maintenance` may select only `id, tenant_id, workspace_id, status, scheduled_at, next_attempt_at` of `scheduled` posts and `id, tenant_id, workspace_id` of accounts holding a ciphertext. Expand only. Tests: `tests/test_marketing.py` (every platform against a mocked HTTP transport, `tests/marketing_fakes.py`), `tests/test_postgres_marketing.py` (RLS isolation, maintenance discovery, the publisher across tenants, key rotation), `apps/web/test/marketing.test.ts`, `apps/web/e2e/marketing.spec.ts` (English and Arabic, axe), `apps/mobile/test/marketing/marketing_test.dart` (320 to 1440 px, 200% text, Arabic).

## Not done yet

- The weekly report is delivered when someone asks (`POST /reports/weekly/deliver`), not on a weekly schedule: an unattended send would only queue an approval nobody requested.
- Flutter's composer writes text, hashtags and a link; media, AI assist, campaigns' auto-post and the month grid are in the web app.
- Platform-side deletion of a published post (ANUM removes its own record only).
- LinkedIn member post statistics and YouTube impressions/click-through (not offered by the APIs used).
