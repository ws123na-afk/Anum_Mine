# Web design system: foundation and shared components

The working brief for the November 2026 web UI and UX upgrade, and the rules that came out of it. It sits on top of [Visual identity: graphite and jade](design/visual-identity.md), which still owns the palette, the fonts and the contrast table. This page owns the system around them: the token layers, the shell, the shared components and the states every page shows.

## Design read

ANUM is an operations command center for a small company's owner and staff: a dense, daily product UI, not a landing page. The upgrade is a **redesign that preserves**: keep graphite and jade, Inter and Noto Sans Arabic, both themes, every route, label and test id. Raise the quality floor with a stricter token system and one look for each shared component, instead of a new visual language.

Dials (from `design-taste-frontend`, which also says dashboards sit outside its landing-page rules, so only its token, colour lock and anti-slop parts apply): variance 3, motion 3, density 6.

## What each skill settled

| Skill | Decision |
|---|---|
| `design-taste-frontend` | Mode is "redesign, preserve". One accent (jade) locked across every page; one radius scale; no new library; pressed state is a 1 px settle. |
| `redesign-existing-projects` | Work in its fix order: type and weights first, then colour and surfaces, then hover and pressed states, then spacing, then states. No framework or styling migration. |
| `high-end-visual-design` | Shadows tinted to the graphite and soft; nested radii are concentric (an inner radius is the outer radius minus the padding); blur only on fixed chrome. Its large-whitespace and double-bezel cards were rejected: they fight a dense daily tool. |
| `frontend-design` (project) and `frontend-design:frontend-design` | Spend boldness in one place: the jade current-page marker and the primary action. Everything else stays quiet. No uppercase tracked labels, no middle-dot chains added. |
| `ui-ux-pro-max` (SaaS operations command center query) | Took its checklist (hover 150 to 300 ms, focus visible, 4.5:1 text, reduced motion, 375 to 1440 checks). Rejected its blue plus orange palette, Plus Jakarta Sans (a new font needs the owner's approval) and glassmorphism (fails `anti-slop` for data surfaces). |
| `21st:21st-ui` | Not usable: the 21st.dev service is blocked by the sandbox network, so no component was taken from it. Patterns came from the other skills. |
| `vectorlab-ux-skills:anti-slop` | Popovers and the sidebar lose `backdrop-filter`: opaque surface with a hairline instead. No decorative pills on static metadata were added. |
| `:spacing` | Spacing tokens stay on the 4 px base (4, 8, 12, 16, 24, 32, 48); control heights are 32, 36 and 40 px, 44 px on touch screens. |
| `:typography` | Shell and shared components use three weights (400, 500, 600). The wordmark keeps 700 as the one documented exception. Text in the shell and shared components is never under 12 px. |
| `:colour-palette` | Light theme shadows stay under 8 % alpha; dark theme depth comes from a top edge highlight and a hairline, not glow. Lists and tables sit on an L1 surface with hairline rows. |
| `:surfaces` | Destructive confirmations focus Cancel first; Escape closes; focus returns to the opener (already in `Dialog.tsx`). |
| `:empty-states`, `:loaders`, `:errors` | Empty states keep a title, one sentence and one action; skeletons fade in after 100 ms so fast loads never flash; error text says what failed and the next step. |
| `:forms`, `:labels` | Fields show the error style only after the person has used the field (`:user-invalid`), never while typing into an empty field. Every field keeps a visible label. |
| `:keyboard` | One focus ring for every control: 2 px jade outline at 2 px offset, and a two-tone ring on filled buttons so it shows on jade and red. |
| `:motion`, `:reduced-motion` | Duration tokens 100, 150, 200 and 300 ms; ease-out to enter, ease-in to leave; only `transform` and `opacity` move. Under reduced motion every transition and animation stops. |
| `:verbs` | New or changed button labels start with a verb. |
| `ui-consistency` (finding-patterns, planning, implementing, verifying) | The shared pieces already exist (`PageHeader` in 25 files, `LoadingSkeleton` in 7, `PaneProblem`, `PaneDenied`, `ConfirmDialog` in 34, `Drawer` in 23, `Modal` in 31): the upgrade restyles them in one place instead of writing new page-level markup. |
| `frontend-visual-qa`, `design-critique`, `design-accessibility-review`, `accessibility-audit`, `performance-audit`, `design-ux-copy` | The checks run on the after screenshots and the axe specs; findings and fixes are in the pull request. |

## Token layers

All tokens live in the first two blocks of `apps/web/src/styles.css` (dark in `:root`, light in `:root[data-theme="light"]`); the type scale lives in `apps/web/src/typography.css`. A light token must also exist in dark (`test/theme.test.ts`).

| Layer | Tokens |
|---|---|
| Colour | `--d-bg`, `--d-surface-1` to `-3`, `--d-line`, `--d-line-strong`, `--d-line-hover`, `--d-text`, `--d-muted`, `--d-faint`, `--d-accent`, `--d-primary`, `--d-ok`, `--d-warn`, `--d-stop`, `--d-danger` (unchanged, see the visual identity) |
| Controls | `--d-control-bg` and `--d-control-bg-hover` (secondary buttons, icon buttons, segmented controls), `--d-control-h` 40 px, `--d-control-h-sm` 36 px, `--d-control-h-xs` 32 px; 44 px on coarse pointers |
| Radius | `--d-r-sm` 6 px (badges inside controls, kbd), `--d-r-control` 8 px, `--d-r-panel` 12 px, `--d-r-pill` 999 px |
| Elevation | `--d-elev-0` (hairline only), `--d-elev-1` (panels), `--d-elev-2` (menus, dialogs, drawers) |
| Focus | `--d-focus-ring`: a 2 px outline in the accent at 2 px offset; filled buttons add a surface coloured gap |
| Motion | `--d-instant` 100 ms, `--d-fast` 150 ms, `--d-base` 200 ms, `--d-slow` 300 ms, `--d-ease` (out), `--d-ease-in` |
| Layers | `--z-sticky` 20, `--z-popover` 40, `--z-dialog` 60, `--z-toast` 70 |

## Shell

- **Sidebar**: an opaque column (no blur), 36 px rows on desktop, the current page a raised row with the 3 px jade marker at its start. Under 761 px it becomes the scrolling pill strip.
- **Header**: one row of 40 px controls: search, workspace switcher, refresh, help, notifications and the account menu. The notification count uses the danger token, not a raw red.
- **Popovers** (notifications, account, workspace): opaque raised surface, hairline, `--d-elev-2`; no blur.
- **Command palette**: the shared `Modal`, the current option marked by a jade bar at its start (logical in Arabic).

## Shared components

| Component | Rule |
|---|---|
| Buttons | Primary: flat jade. Secondary: the control surface with a hairline (white on light, graphite on dark), never a grey fill. Danger: the danger token. 40 px, 600 weight, 8 px radius, 1 px settle on press. |
| Fields | 40 px, text 14 px on desktop and 16 px on phones (so phones never zoom), hairline that darkens on hover, jade ring on focus, red edge only after use (`:user-invalid`). Selects keep the native arrow: more than twenty component rules set the `background` shorthand on their selects, which would erase a drawn one. |
| Cards and panels | L1 surface, hairline, `--d-elev-1`, 12 px radius; inner blocks 8 px. |
| Tables | L1 surface, sticky header in the faint text colour, hairline rows, tabular figures. |
| Tabs | The segmented style: a well with the current tab raised. Pills stay for filters. |
| Dialogs and drawers | Raised surface, `--d-elev-2`, 200 ms enter with ease-out; Cancel first focus for destructive confirmations. |
| Toasts | Raised surface, enter 200 ms, readable for the time set in code, a live region. |
| States | `LoadingSkeleton` (fades in after 100 ms), `PaneEmpty` (icon tile, title, sentence, action), `PaneProblem` (what failed, Try again), `PaneDenied` (who can do it). |
| Badges | Pill, 12 % tint of the status colour, the status in words, 12 px, 600. |
| Avatars | Jade disc with initials in 600 weight; no white ring. |

## Pages

The second pass recomposed the most used pages on these tokens. Each keeps its routes, test ids and API calls.

| Page | Layout now | Skill that decided it |
|---|---|---|
| Home | One header row; the glance is one metric strip (seven cells on one row on wide screens, hairlines between cells, a status bar on the top edge of a cell that needs attention, no icon tiles); panels with today's decisions first, rows on hairlines instead of boxed rows, approvals and insights on a coloured start rail. | `ui-ux-pro-max` (dashboard pattern: KPI strip, then the work queue), `design-taste-frontend` (no equal card wall), `vectorlab-ux-skills:surfaces` (one surface per panel) |
| Tasks | The composer is a command bar above everything (instructions and Run task on one row); figures under it; the list and the selected task side by side. Run task stays clickable: with nothing written it says what to write and focuses the field. On phones and tablets the bar gives way while a task is open. | `ui-ux-pro-max` (list and detail), `vectorlab-ux-skills:forms` (no disabled submit), `:page-patterns` |
| Approvals | A review queue: from 1100 px an index of what waits (action, risk, age) sits beside the requests and opens one in place with focus; each request reads top to bottom and ends in one decision bar; its start edge carries the risk colour (the words stay in its meta line). Figures are one strip. | `ui-ux-pro-max` (approval queue), `high-end-visual-design` (one decision moment per card), `vectorlab-ux-skills:surfaces` |
| Agents | A roster: one surface, a row per agent on hairlines, who it is and what it does on the start side, category, sharing and tools on the end side. | `redesign-existing-projects` (replace the card wall), `design-taste-frontend` (density for a daily tool) |
| Agent controls | The page title is the only visible title; Refresh is quiet so the emergency stop is the page's main action; the stop card's start edge says its state. | `frontend-design` (one bold element), `vectorlab-ux-skills:verbs` |
| Email | Three full panes (folders, messages, reader) up to 1680 px: the mailbox list becomes one row above the page instead of a fourth column. | `ui-ux-pro-max` (inbox pattern) |
| ANUM Command | A smaller orb stage so the composer (the page's job) sits higher and is the raised surface; the capability tiles become one row of labelled shortcuts. | `frontend-design` (the order first), `vectorlab-ux-skills:typography` |
| Business invoices | The money figures are one strip with hairlines; the table leads with the invoice number in the text colour, amounts in tabular figures. | `vectorlab-ux-skills:spacing`, `:colour-palette` |
| Settings | From 1100 px the sections are a vertical list beside the open section; narrower screens keep the underlined row. | `ui-ux-pro-max` (settings pattern), `vectorlab-ux-skills:page-patterns` |
| Summary figures everywhere (`.metrics`) | One strip with hairlines instead of separate cards; three to a row on phones. | `vectorlab-ux-skills:surfaces` |

## Motion

Only state changes move: hover, press, focus, opening and closing. Entering uses ease-out at 200 ms, leaving ease-in at 150 ms. Only `transform` and `opacity` animate. Nothing loops except loading skeletons and live voice states, and `prefers-reduced-motion: reduce` turns transitions and animations off. The headquarters scene is untouched: no shell motion runs while it renders.

## Follow-ups

What this pass did not finish, in order of value:

- **Disabled submit buttons** remain on other forms (Run task no longer has one); each form needs its own inline validation before the button can stay enabled.
- **Agents** show no current work or cost in the roster: the agents list API does not return them, and a call per agent would be slow. A list field for both is an API change.
- **Billing console** keeps the foundation pass only (tabs, strip, buttons); no recomposition.
- **Text under 12 px**: the shell and shared components no longer use `--text-2xs`; about a hundred page and headquarters rules still do. Raising the token itself would change the headquarters labels, which this pass does not touch.
- **Tablet width**: at 768 px the 248 px sidebar leaves about 520 px for the page; a narrower or collapsible sidebar between 761 and 1024 px is worth a separate change with its own tests.
- **Selects** keep the browser's arrow (see Shared components).
