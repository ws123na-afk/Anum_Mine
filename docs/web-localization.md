# Web localization and accessibility

The web client (`apps/web`), and therefore the desktop app that renders its build ([Desktop](desktop.md)), runs in English or Arabic. Arabic lays the whole interface out right to left. The Flutter app has its own catalogs with the same terms (`apps/mobile/lib/src/localization/anum_localizations.dart`, [Flutter mobile](mobile.md)).

## How the language is chosen

1. A choice made in **Settings > Language** wins. It is saved in this browser's `localStorage` under `anum.locale` (the key the Flutter app also uses for its own choice). Other browsers, the desktop app and the phone keep their own choice.
2. Without a saved choice, the first language in `navigator.languages` that has a catalog is used. This is the same list the browser sends as `Accept-Language`, so a browser set to Arabic opens ANUM in Arabic.
3. Otherwise English.

"Match my browser" in Settings > Language forgets the saved choice and goes back to step 2. If the browser blocks storage (some private modes), the switch still applies to the open page and is not remembered. The sidebar's names follow the ANUM 360 mockup: Assistant is **AI Chat** (الدردشة الذكية), Memory is **Knowledge Base** (قاعدة المعرفة), Files is **Documents** (المستندات) and Skills is **ANUM Store** (متجر ANUM). The page sets `<html lang dir>` before its first render (`initLocale` in `apps/web/src/main.tsx`), so there is no flash of the wrong direction. `#language` deep-links to the Language section of Settings (and `#notifications` to Notifications).

## The i18n layer

No i18n dependency; the code is in `apps/web/src/lib/i18n.ts` (pure, unit tested in Node):

- **Catalogs**: `src/lib/messages/en.ts` is the source of truth for keys; `ar.ts` is typed `Record<MessageKey, Message>`, so a missing key is a build error. `test/i18n.test.ts` also checks that every translation has the same `{placeholders}` as English, that no message is empty, and that every Arabic plural message has the `one`, `two`, `few`, `many` and `other` forms.
- **Interpolation**: `t('shell.crumb', { view })` replaces `{view}`. Numbers are formatted for the locale. In Arabic, a value containing Latin letters (an id, a person's user id, an English message from the server) is wrapped in Unicode isolates (U+2068 … U+2069) so its punctuation does not reorder the Arabic sentence around it.
- **Plurals**: a message can be `{ zero?, one, two?, few?, many?, other }`; `{ count }` picks the form with `Intl.PluralRules` (CLDR rules: English has one/other, Arabic has zero/one/two/few/many/other). An explicit `zero` form ("No tasks") applies in every language.
- **Elements inside sentences**: `rich(key, { phrase: <code>…</code> })` (`src/rich.tsx`) puts React elements where the translation places the placeholder, for example the confirmation phrase in "Type DELETE MY DATA to confirm".
- **Codes from the API**: task, run and approval statuses, run step types, risk levels and index states are labelled through `src/lib/labels.ts`; a code the catalogs do not know yet shows as itself with underscores as spaces.
- **Re-rendering**: the current locale is module state shared by components and pure helpers (`lib/privacy.ts`, `lib/approvals.ts`, `lib/chatMemory.ts` and the others call `t` directly). The root component subscribes with `useLocale()`, so a switch re-renders the whole tree in the new language and direction without a reload and without losing a draft.

### Numbers and dates

Dates use `Intl.DateTimeFormat` with the current locale (`formatDateTime`). Arabic uses Western digits and separators (`ar-u-nu-latn`), so counts, times and the ids, hashes and codes shown next to them read the same in both languages.

### Adding or changing a string

Add the key to `en.ts` and `ar.ts` in the same change, use it through `t(...)`, and run `pnpm --filter @anum/web test:unit`. Never build a sentence by concatenating translated fragments; give the translator the whole sentence with placeholders.

### Punctuation

No em dashes or en dashes in anything a person reads (owner rule, 13 Oct 2026). Use a colon ("Group: Chief of Staff"), a comma, "to" for a range ("1 to 20 of 45", Arabic "1 إلى 20 من 45") or a new sentence. An empty value shows a plain hyphen (`-`). `apps/web/test/plainPunctuation.test.ts` fails if a messages file contains either dash; the setup plan's blueprint is checked by `test_the_plan_names_carry_no_long_dashes`.

## Right to left

Layout uses logical CSS properties (`margin-inline-start`, `padding-inline-end`, `border-inline-end`, `inset-inline-start`, `text-align: start`), so `dir="rtl"` mirrors the shell (the sidebar moves to the right), forms, lists and cards with no per-component RTL CSS. The few direction-bearing pieces are handled explicitly in `styles.css`: icons that point (chevrons, the external-link arrow, the Command Center's send arrow and tile scroll buttons) carry `flipRtl`; the Command Center's progress bars fill from the right; the switch knob and the title rule's gradient are mirrored. Arabic headings drop the negative letter-spacing used for Latin text, because tracking breaks Arabic letter joining.

Text people or the model wrote keeps its own direction with `dir="auto"`: chat messages and each block of an answer, task titles and instructions, memory contents, approval reasons, and server-written notes. An English answer therefore reads left to right inside the Arabic interface, and an Arabic one right to left inside the English interface. Ids, hashes, tool names and URLs are `dir="ltr"` (`code` is also `unicode-bidi: isolate`).

## Fonts

Owner decision of 12 October 2026 ([Full skills audit](audits/2026-10-12-full-skills-audit.md)): the web app, the desktop app (which shows the web build) and the phone app use the same two typefaces, self-hosted, never from a font CDN.

- `public/fonts/inter-latin-var.woff2` (Inter cut to Latin, variable weight 100 to 900, about 76 KB) and `public/fonts/noto-sans-arabic-var.woff2` (Noto Sans Arabic, variable weight, about 180 KB), both under the SIL Open Font License (`OFL-Inter.txt`, `OFL-NotoSansArabic.txt` next to them). They are built from the Google Fonts variable sources with fontTools (subset, then the optical-size or width axis pinned).
- `public/fonts/fonts.css` declares them with `font-display: swap`; the Arabic face has an Arabic `unicode-range`, so it downloads only when a page shows Arabic. `index.html` preloads only the Latin face.
- The stack is `Inter, "Noto Sans Arabic"`, then the system fallbacks (`ui-sans-serif, system-ui, …, sans-serif`), so text still shows while the fonts load or if they fail.
- Same origin only: the CSP's `font-src 'self' data:` and `style-src 'self'` already cover them. The files are not content-hashed, so nginx serves them with `no-cache` (revalidated with an ETag).

`test/fonts.test.ts` checks the files, the preload, the stack and the CSP.

## What is translated

Translated: the shell (navigation with its More, Admin and Workspaces groups, the top bar's search and command palette, notifications bell and user menu, event-stream status, workspace switcher, Add workspace, sign-in, the archived-workspace banner), the Command Center home (`command.*` keys; its example requests are real Arabic, not transliterations, and its speaking-language pills offer English and Arabic only, because there is no Urdu catalog), Assistant chat, Settings (This workspace, Notifications with phone push and registered phones, Workspace onboarding, Model gateway, Notifications, Chat memory, Language), Privacy, Memory, Tasks (including Sources used), Approvals, the WhatsApp inbox (customer text keeps its own direction with `dir="auto"`), Developer mode (requests, projects, and the developer confirmation and project choice in chat and voice; `e2e/developer.spec.ts` checks it in Arabic with axe), and the Audit log (filters, table, detail drawer, export messages; `audit.*` and `workspaceAdmin.*` keys). The modal confirmation and drawer (`src/Dialog.tsx`) are translated too; Integrations, Skills, Agents, the Enterprise marketplace catalog, and Automations (list, detail, composer, schedules with their preview, run history and automation alerts; the cron description comes from the API in English).

Also translated: Voice (`voice.*`), Governance and Enterprise operations (`gov.*`, `ent.*`: policy packs, role templates, approval rules, routing targets, regions and failover, and the organization-administrator notice shared with Budgets and the marketplace), Members (the whole page: metrics, rows, invitations, the invite form, the token shown once and accepting an invitation; `members.*`, `memberPage.*`, `invite.*`, `accept.*`), Budgets (`budgets.*`, including the "model budget reached" notice) and the Approval policy (`policy.*`). These catalogs live in their own files, spread into `en.ts`/`ar.ts`: `messages/en.owner.ts`, `en.governance.ts`, `en.voice.ts` and their `ar.*` twins (typed against the English file, so a missing key is a build error). Dates in them use the page language (the budget reset date reads "1 نوفمبر 2026 (UTC)" in Arabic); invitation expiry ("تنتهي خلال يومين"), member, rule, region and waiting-approval counts use the Arabic plural forms.

Voice has two languages: the interface follows the page language, and the speech language (Voice settings > Speech language) picks what the browser listens for and how replies are spoken. Without a saved speech language, an Arabic page listens in Arabic (`ar-SA`). The "Try saying" examples are in the speech language, because they are meant to be said; the greeting is the assistant speaking, so it follows the speech language too.

What still renders in English: text people or the model wrote (names, pack descriptions, routing ids, the assistant's replies), the developer timeline's stage labels and summaries, which the API writes, and API sentences that have no Arabic catalog entry yet (see below).

## Messages written by the API

The API answers in the page language for the sentences the web shows: the web sends `Accept-Language` with the page language on every API request (`authHeaders` in `src/lib/api.ts`), and the API (`services/api/anum_api/localization.py`) negotiates English or Arabic from it (q-weights honoured; anything else is English). Translated on the API: the `message` of every error envelope that has a catalog entry (permission and membership refusals, the organization-administrator and archived-workspace refusals, the common not-found and conflict answers, validation, rate limiting and unexpected errors), the monthly model budget refusal with its reset date (also in a streamed chat answer), the Privacy page's retention notes and account and shared-records notes, and the Delete my data confirmation refusal (the phrase `DELETE MY DATA` stays as is). Error `code`s, `details`, field names, ids, numbers and dates never change, so clients keep matching on codes; error responses carry `Content-Language` and `Vary: Accept-Language`. A sentence without an Arabic entry is answered in English; `services/api/tests/test_localization.py` fails when a catalog entry no longer matches a sentence the API writes.

## Accessibility

`apps/web/e2e/accessibility.spec.ts` runs axe-core (`@axe-core/playwright`, pinned exactly) over the Command Center home, AI Chat (Assistant), Tasks, Approvals, Memory, Files, Settings, Language, Privacy, Notifications, the Audit log, Integrations, Skills, Agents, Enterprise, Automations, Activity, Autonomy, What ANUM learned, Members, Budgets, the Approval policy, Governance and Voice, in English and in Arabic, on the desktop and mobile Playwright projects, with the WCAG 2.0/2.1 A and AA rules plus best practices. A serious or critical violation fails CI and the failure lists the rule and the offending selectors. The same spec checks:

- a "Skip to content" link is the first Tab stop and moves focus into the page, past the navigation;
- there is exactly one `main` landmark, named by the page title, and a labelled primary navigation (the sidebar is a sibling of `main`, not inside it).

Other basics the views follow: decorative icons are `aria-hidden`; loading and result notices are `role="status"` (errors `role="alert"`); every form control has a visible label; the pending-approvals count in the navigation has a spoken label ("1 pending approval"); the queue of pending approvals has a heading for screen-reader navigation; contrast comes from the theme tokens in `styles.css`, which are documented with their ratios for both the dark and the light theme ([theme](design/anum360.md#theme)); the axe gate also runs every view in the light theme.

`apps/web/e2e/localization.spec.ts` covers the language choice end to end: a browser asking for Arabic opens in Arabic and right to left, Settings > Language switches and persists across a reload, "Match my browser" goes back, and the main views (Integrations, Skills, the marketplace, Members, Budgets, the Approval policy, Governance, Enterprise and Voice included) read in Arabic with Arabic plural forms and no horizontal overflow.
