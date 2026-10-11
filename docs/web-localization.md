# Web localization and accessibility

The web client (`apps/web`), and therefore the desktop app that renders its build ([Desktop](desktop.md)), runs in English or Arabic. Arabic lays the whole interface out right to left. The Flutter app has its own catalogs with the same terms (`apps/mobile/lib/src/localization/anum_localizations.dart`, [Flutter mobile](mobile.md)).

## How the language is chosen

1. A choice made in **Settings > Language** wins. It is saved in this browser's `localStorage` under `anum.locale` (the key the Flutter app also uses for its own choice). Other browsers, the desktop app and the phone keep their own choice.
2. Without a saved choice, the first language in `navigator.languages` that has a catalog is used. This is the same list the browser sends as `Accept-Language`, so a browser set to Arabic opens ANUM in Arabic.
3. Otherwise English.

"Match my browser" in Settings > Language forgets the saved choice and goes back to step 2. If the browser blocks storage (some private modes), the switch still applies to the open page and is not remembered. The page sets `<html lang dir>` before its first render (`initLocale` in `apps/web/src/main.tsx`), so there is no flash of the wrong direction. `#language` deep-links to the Language section of Settings.

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

## Right to left

Layout uses logical CSS properties (`margin-inline-start`, `padding-inline-end`, `border-inline-end`, `inset-inline-start`, `text-align: start`), so `dir="rtl"` mirrors the shell (the sidebar moves to the right), forms, lists and cards with no per-component RTL CSS. The few direction-bearing pieces are handled explicitly in `styles.css`: icons that point (chevrons, the external-link arrow) carry `flipRtl`; the switch knob and the title rule's gradient are mirrored. Arabic headings drop the negative letter-spacing used for Latin text, because tracking breaks Arabic letter joining.

Text people or the model wrote keeps its own direction with `dir="auto"`: chat messages and each block of an answer, task titles and instructions, memory contents, approval reasons, and server-written notes. An English answer therefore reads left to right inside the Arabic interface, and an Arabic one right to left inside the English interface. Ids, hashes, tool names and URLs are `dir="ltr"` (`code` is also `unicode-bidi: isolate`).

## What is translated

Translated: the shell (navigation, header, event-stream status, workspace switcher, sign-in), Assistant chat, Settings (Workspace onboarding, Model gateway, Notifications, Chat memory, Language), Privacy, Memory, Tasks (including Sources used), and Approvals.

Not yet translated (they render in English inside the Arabic layout, which still mirrors): Workbench (automation), Agents, Files, Skills, Integrations, Voice, Governance, Enterprise, Members, Budgets, Approval policy, Developer mode and the developer-mode confirmation in chat. Messages written by the API (error details such as a budget's reset date, the retention and account notes on the Privacy page, refusals) are English; the API does not localize them yet. These are listed in the [production plan](production-plan.md).

## Accessibility

`apps/web/e2e/accessibility.spec.ts` runs axe-core (`@axe-core/playwright`, pinned exactly) over Assistant, Tasks, Approvals, Memory, Settings, Language and Privacy, in English and in Arabic, on the desktop and mobile Playwright projects, with the WCAG 2.0/2.1 A and AA rules plus best practices. A serious or critical violation fails CI and the failure lists the rule and the offending selectors. The same spec checks:

- a "Skip to content" link is the first Tab stop and moves focus into the page, past the navigation;
- there is exactly one `main` landmark, named by the page title, and a labelled primary navigation (the sidebar is a sibling of `main`, not inside it).

Other basics the views follow: decorative icons are `aria-hidden`; loading and result notices are `role="status"` (errors `role="alert"`); every form control has a visible label; the pending-approvals count in the navigation has a spoken label ("1 pending approval"); the queue of pending approvals has a heading for screen-reader navigation; contrast comes from the theme tokens in `styles.css`, which are documented with their ratios.

`apps/web/e2e/localization.spec.ts` covers the language choice end to end: a browser asking for Arabic opens in Arabic and right to left, Settings > Language switches and persists across a reload, "Match my browser" goes back, and the main views read in Arabic with Arabic plural forms and no horizontal overflow.
