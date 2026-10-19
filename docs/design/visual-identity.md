# ANUM visual identity: graphite and jade

The web app's look (October 2026). The owner asked for a product that looks premium and ready to sell, not generic or robotic. This page explains the system so every page, including new ones, follows it without a designer in the loop. The tokens live in one block at the top of `apps/web/src/styles.css`; component rules use only those tokens.

## Direction

A calm enterprise tool in the spirit of Linear, Vercel and the Stripe dashboard: the content leads, the chrome stays quiet, and colour means something.

- **One neutral, one accent.** A cool graphite base for surfaces and text, and one brand accent, **jade**, for primary actions, links, focus, the current page and the logo mark. Jade was chosen over blue or violet because blue-to-violet gradients are the most common look of AI products; a deep green-teal is calm, reads as trustworthy in the Gulf markets ANUM serves, and is unlike its neighbours.
- **Status colours only for real status.** `ok`, `warn` and `stop` appear only on labelled status (a badge that says "Overdue", a dot beside a figure's label), never to decorate a number or a card.
- **Depth from hairlines.** Panels are flat surfaces with a hairline border and a soft, tinted shadow. No gradient washes, no glows, no gradient text, no rainbow icon tiles.
- **Two first-class themes.** Dark (the default) and Light share every rule; each has its own measured values.

## Colour

| Token | Role | Dark | Light |
|---|---|---|---|
| `--d-bg` | Page | `#0b0d12` | `#f7f8fa` |
| `--d-surface-1`, `-2`, `-3` | Panels, raised panels, wells and the selected tab | `#12151c`, `#171b24`, `#1f2430` | white, white, `#f1f3f6` |
| `--d-line`, `--d-line-strong`, `--d-line-hover` | Hairlines | white at 7.5, 13 and 22 % | graphite at 9, 16 and 28 % |
| `--d-text`, `--d-muted`, `--d-faint` | Text | `#eceef3`, `#a2a8b6`, `#868d9c` | `#12151c`, `#4a5162`, `#626a7b` |
| `--d-accent` | Links, focus, icons, the current page marker | `#45d0b5` | `#09685a` |
| `--d-primary`, `--d-primary-hover` | Filled primary buttons (white text) | `#0f7a69`, `#0c6a5b` | the same |
| `--d-ok`, `--d-warn`, `--d-stop` | Labelled status | `#6fd39a`, `#f2c46b`, `#f59aa6` | `#11652f`, `#94500c`, `#b42335` |
| `--d-danger` | Destructive buttons (white text) | `#c4253f` | the same |

Older token names stay so no page breaks: `--d-sky`, `--d-violet`, `--d-blue`, `--d-rose` and the channels `--c-sky`, `--c-violet`, `--c-blue`, `--c-indigo`, `--c-rose` all resolve to the accent, and `--d-primary-grad` is a flat fill of `--d-primary`. A page written for the old blue-violet palette therefore shows jade, never a second accent. `test/theme.test.ts` checks that every alias resolves to `--d-accent` in both themes.

### Contrast (WCAG 2.1 AA, measured)

All ratios come from the relative-luminance formula and are checked for every text token on every surface by `test/theme.test.ts`.

| Pair | Dark | Light |
|---|---|---|
| Text on the page | 16.7:1 | 17.2:1 |
| Muted on the page / on a panel | 8.2:1 / 7.2:1 | 7.5:1 / 7.9:1 |
| Faint on the page / on a panel | 5.8:1 / 5.2:1 | 5.1:1 / 5.4:1 |
| Accent text on the page / on a panel | 10.1:1 / 9.0:1 | 6.3:1 / 6.7:1 (5.4:1 on its own tint) |
| Ok, warn, stop on a panel | 9.4:1, 10.6:1, 8.3:1 | 7.2:1, 6.2:1, 6.5:1 (6.1:1, 5.2:1, 5.3:1 on their own badge tints) |
| White on primary (hover) | 5.2:1 (6.5:1) | the same |
| White on danger | 5.7:1 | the same |

## Type

Inter for Latin and Noto Sans Arabic for Arabic, both self-hosted variable fonts (`apps/web/public/fonts`, the owner's 12 October decision in [web localization](../web-localization.md#fonts)). Headings use a display setting of the same family rather than a second typeface: optical sizing, tighter tracking and balanced line breaks; Arabic keeps zero tracking and more leading so letters join.

| Role | Token (size) and weight |
|---|---|
| Page title (`h1`) | `--text-5xl` (28 px), 650, tracking -0.026 em (26 px on phones and in page headers) |
| Section title (`h2`) | `--text-xl` (18 px), 600 |
| Card title (`h3`) | `--text-base` (15 px), 600 |
| Body | `--text-md` to `--text-base` (14 to 15 px), 400, line height 1.5 |
| Labels and meta | `--text-xs` to `--text-sm` (12 to 13 px), 500 |
| Figures | `--text-3xl` to `--text-5xl` (22 to 28 px), 650, tabular numbers |

### Type tokens

`apps/web/src/typography.css` is the only place a font size is written. Twelve steps in rem (so browser text-size settings apply; the pixel values are at the default 16 px root): `--text-2xs` 11, `-xs` 12, `-sm` 13, `-md` 14, `-base` 15, `-lg` 16, `-xl` 18, `-2xl` 20, `-3xl` 22, `-4xl` 24, `-5xl` 28, `-6xl` 32; six fluid `--text-fluid-*` sizes for display headings and stat figures; `--leading-*` and `--weight-*` for line height and weight. Under `lang="ar"` the two smallest steps rise one step (Noto Sans Arabic sets smaller than Inter) and leading grows so letters join.

- Write `font-size: var(--text-sm)`, never `13px`. `em`, `%` and `pt` (print) stay allowed because they are relative or for paper.
- The 2026-10 migration snapped the old values to the nearest step (a tie rounds up): 11.5 to 12, 12.5 to 13, 13.5 to 14, 14.5 to 15, 17 to 18, 21 to 22, 26 to 28, 30 to 32. Everything else was already on a step.
- From `apps/web`, `node scripts/type-scale-codemod.mjs src/<file>.css` migrates a stylesheet, and it is safe to re-run after a merge conflict.
- `apps/web/test/typeScale.test.ts` fails on a raw size in any stylesheet not in its `PENDING` list (stylesheets still to migrate; the list only shrinks) and on an inline pixel `fontSize`. The standalone policy pages in `public/policies` cannot load the app CSS and keep their own sizes.

A distinct display face with Arabic support (for example IBM Plex Sans Arabic beside a Latin display face) would need new font files; that is a download the owner has to approve, so it is listed as a follow-up rather than done here.

## Shape, space and depth

- Radii by hierarchy: panels 12 px (`--d-r-panel`), controls 8 px (`--d-r-control`), pills 999 px for chips and badges.
- Spacing on a 4 px base: 4, 8, 12, 16, 24, 32, 48 (`--d-s1` to `--d-s7`).
- Depth: `--d-elev-1` (a hairline and a 1 to 2 px shadow) for panels, `--d-elev-2` (a 12 to 32 px soft shadow) for menus, dialogs and sheets. Shadows are tinted to the graphite, not black.

## Components

| Component | Rule |
|---|---|
| Primary button | Flat jade, white text, 40 px tall, 8 px radius. A button with no class is primary by default. |
| Secondary button | A quiet glass fill with a hairline; text colour. |
| Danger button | `--d-danger`, white text. |
| Pressed | Buttons settle 1 px on press. |
| Fields | `--d-field` with a strong hairline; hover darkens the hairline; focus turns it jade with a 3 px soft ring (`--d-ring`) and keeps a transparent outline for forced colours. Checkboxes and radios use the accent. |
| Focus | A 2 px jade outline, 2 px offset, on every focusable element. |
| Navigation | The current view is a raised row with a 3 px jade marker at its start (a raised pill in the phone strip); icons of the current view are jade. |
| Section tabs | Pills; the current tab is the raised surface with a hairline and a soft shadow. One scrolling row on phones. |
| Badges | Pills with a 12 % tint of their status colour and the status in words. |
| Summary figures | Text colour; a status figure marks its label with a small dot. |
| Empty states | The icon sits in a quiet 44 px tile, then a title and the next action. |
| Loading | `LoadingSkeleton` (`PageParts.tsx`): bars shaped like a panel's heading and lines with a slow pulse, and a screen-reader label, instead of a "Loading..." sentence. The shell's view loader and Governance use it. |
| Dialogs and drawers | Open with a 180 ms lift while the scrim fades in; the panel never fades, so its text is at full contrast from the first frame. |
| Toasts | The message toast slides and fades in; tokens give it the panel surface. |
| Tables | Dense rows on the panel surface, a sticky header, hairline row separators. |

## Motion

Only state changes move: hover, press, focus, opening and closing. 150 ms (`--d-fast`) or 180 ms (`--d-base`) with an ease-out curve (`--d-ease`). Nothing loops except loading skeletons and live voice states. `prefers-reduced-motion` turns transitions and animations off (the global rule in `styles.css` and per-component rules for dialogs and skeletons).

## The logo mark and the orb

The ANUM mark keeps its shape and is drawn in three steps of jade (`--d-mark-a` to `--d-mark-c`). The command orb and the voice scene are graphite objects with a jade rim and wave instead of the old midnight blue and violet glow.

## Screens

Before (main before this change) and after, at 1440x900 and 390x844, dark and light, in [`screens/`](screens/): Home, Business, Email, Settings and Governance. File names are `before-<page>-<size>-<theme>.jpg` and `after-<page>-<size>-<theme>.jpg`. They were rendered with `vite preview` against the e2e API fixtures.

## Follow-ups

- The Flutter app (`apps/mobile/lib/src/theme/anum_theme.dart`) still uses the earlier navy and blue-violet palette; it should take these tokens so web and mobile match.
- A distinct display typeface with Arabic support needs the owner's approval to add the font files.
- Pages that set their own colours for data (chart series, agent avatars, workspace dots) keep them; they are data colours, not decoration.
