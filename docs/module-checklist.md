# Module checklist: what a complete module page needs

A module page is done when it passes every item below, on the web and in the Flutter app. The list comes from the most complete modules today (Commerce, Inbox, Tasks, Team, Calls & Meetings, and Finance) and from the rules in `CLAUDE.md` (definition of done, responsive UI gate). Use it when building a module and when reviewing one; the [status table](#status) records where each module stands.

## Navigation

- **Title and active nav.** The page has one `h1` (`#view-title`, from `PageHeader` or the shell's page head) and its sidebar entry is marked current (`aria-current="page"`); a tab of another entry (Knowledge Base collections) lights up its parent.
- **Way back.** A sub-page or tab shows a breadcrumb (`Breadcrumbs` in `PageParts.tsx`): Home, the module, then the current page, as links. Pages without their own header get it from the shell. A detail pane or drawer has a Back or Close button (`paneBack`, `backButton`, `Drawer`).
- **Deep links.** Every tab and detail that can be shared has a URL hash (`#finance-transactions`, `#commerce-orders`, `#marketing-calendar`, `#reports/<id>`, `#agents/<id>`) and `viewFromHash` opens it.
- **Back and Forward.** Each view change and each tab change is a browser history entry (`history.pushState`), and the shell and the view listen for `popstate`, so the browser's buttons move between them.
- **Flutter.** A screen pushed on the navigator has an `AppBar`, whose back button returns to where it was opened (Home, the menu, a pin); top-level destinations sit in the shell's navigation instead.

## States

- **Loading:** a polite status line (`role="status"`, `aria-busy`), never an empty page.
- **Empty, with a next action:** says what is missing and offers the button that fixes it ("Add account", "Import statement", "Connect a channel").
- **Error, with a fix:** says what failed with the server's reason and offers a retry (`role="alert"`); a failed part never blanks the parts that loaded.
- **Permission denied:** says who can do it and how to get access ("Ask a workspace owner to change your role from viewer to member"), from the server's 403, never only a disabled button.
- **Writes:** every create, change and delete confirms what happened; a destructive one asks first (`ConfirmDialog`, `AlertDialog` in Flutter).

## Long and large content

- Search for any list that can grow past a screen; filters for its main dimensions; paging (`limit`/`offset` with a range label, or "Load more") instead of loading everything.
- Long text wraps (`overflow-wrap: anywhere`); tables scroll inside their panel or turn into cards on narrow screens; numbers never break mid-figure.

## Keyboard and screen reader

- Every control is reachable by Tab, has a visible focus ring and a name (visible label, `aria-label`, or `visuallyHidden` text for icon buttons such as edit and delete, naming the item).
- Regions have headings (`aria-labelledby`); charts have a text alternative (`role="img"` with a summary) and a table fallback.
- The axe gate (`e2e/ui-gate.spec.ts`) has no serious or critical violation in English, Arabic and the light theme. A new view or tab gets an entry in `e2e/support/gatePages.ts`, which feeds the axe, layout and dead-button gates.
- No dead buttons: `e2e/journey.spec.ts` presses every visible button on the page and fails on one that does nothing. A search box that needs text is `required`, so pressing Search on an empty box says why.

## Arabic, right to left

- Every string is in both catalogs (web `lib/messages/*.en.ts` and `*.ar.ts`, Flutter strings) with the same placeholders and Arabic plural forms.
- Logical CSS (`margin-inline-start`, `inset-inline`), mirrored arrows (`cmFlip`, `flipRtl`), and dates, amounts, codes and ids kept left to right inside Arabic sentences (`<bdi dir="ltr">`, Unicode isolates, `dir="auto"` for user text). Western digits.
- No em or en dashes in UI text, docs or replies (`test_no_long_dashes.py`, `plainPunctuation.test.ts`).

## Responsive

- Rendered at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844, English and Arabic, light and dark: no horizontal overflow, no clipped control, content-driven heights, no wasted columns next to a tall panel (`e2e/ui-gate.spec.ts` checks overflow and clipped controls on every page in `e2e/support/gatePages.ts`). A table that scrolls sideways is focusable (`tabIndex={0}`) so the keyboard can scroll it.

## Tests

- Unit tests of the view's pure logic (`apps/web/test/<module>.test.ts`) and of the API (`services/api/tests/test_<module>.py`, plus `test_postgres_<module>.py` for RLS).
- A Playwright spec of the main path with a mocked API (`apps/web/e2e/<module>.spec.ts`, `e2e/support/<module>Api.ts`) that also covers the empty, error and denied states.
- Flutter widget tests at widths from 320 to 1440 with 200% text and in Arabic (`apps/mobile/test/<module>_test.dart`).
- The module is in the accessibility, page-layout and localization e2e lists.

## Status

Checked in this change (October 2026). "ok": meets the item; "fixed": met by this change; "open": a gap left for its own change; "n/a": the item does not apply. Fixed for every module at once: views and tabs are now browser history entries (the shell pushes each view and follows Back and Forward), and every page without its own header shows a linked breadcrumb (Home, the parent entry, the page) instead of the plain "Workspace / page" line.

| Module | Title and nav | Way back | Deep links | Back/Forward | States | Search, filters, paging | Keyboard, labels | Arabic RTL | Flutter screen with AppBar back | Unit and e2e tests |
|---|---|---|---|---|---|---|---|---|---|---|
| Home | ok | n/a (root) | ok | fixed | ok | n/a | ok | ok | ok (shell destination) | ok |
| ANUM Command | ok | n/a | ok | fixed | ok | n/a | ok | ok | ok | ok |
| AI Chat | ok | ok (thread list) | ok | fixed | ok | ok | ok | ok | ok (shell destination) | ok |
| Agents | ok | ok (profile Back) | ok (`#agents/<id>`) | ok | ok | ok | ok | ok | ok | ok |
| Voice, Call | ok | n/a | ok | fixed | ok | n/a | ok | ok | ok | ok |
| Automations | ok | fixed (breadcrumb) | ok | fixed | ok | ok | ok | ok | ok (shell destination) | ok |
| Tasks | ok | ok (pane Back) | ok | fixed | ok | ok | ok | ok | ok | ok |
| Calls & Meetings | ok | ok | ok (`#meetings?meeting=`) | fixed | ok | ok | ok | ok | ok | ok |
| Phone calls | ok | ok (call and contact Back) | ok (`#calls-<tab>`, `#calls?call=`) | ok | ok | ok | ok | ok | ok | ok |
| Calendar | ok | ok (pane Back) | ok | fixed | ok | ok (no paging: a range at a time) | ok | ok | ok | ok |
| Team, Assistants | ok | ok (Back) | ok | fixed | ok | ok | ok | ok | ok | ok |
| Integrations | ok | fixed (breadcrumb) | ok (`#connections`) | fixed | ok | ok (search, no paging needed) | ok | ok | ok | ok |
| Tyre planning | ok | fixed (breadcrumb) | open: tabs are not in the URL | fixed | ok | ok | ok | ok | ok | ok |
| Commerce | ok | fixed (breadcrumb on tabs) | fixed (`#commerce-<tab>`) | fixed | ok | ok | ok | ok | ok | ok (tab history test added) |
| Finance | ok | ok (breadcrumb on tabs) | ok (`#finance-<tab>`) | ok | ok | ok | ok | ok | ok | ok |
| Marketing | ok | fixed (breadcrumb on tabs) | ok (`#marketing-<tab>`) | fixed | ok | ok | ok | ok | ok | ok (tab history test added) |
| Documents | ok | ok (detail Close) | ok | fixed | ok | ok | ok | ok | ok | ok |
| Knowledge Base | ok | fixed (breadcrumb names the tab) | ok (`#knowledge/<id>`) | fixed | ok | ok (no paging: newest 200) | ok | ok | ok | ok |
| Reports | open: the page title repeats as the intro heading | fixed (breadcrumb) | ok (`#reports/<id>`) | fixed | ok | ok | ok | ok | ok | ok |
| Skills | ok | n/a (root) | ok | fixed | ok | ok | ok | ok | ok | ok |
| Favorites | ok | ok | ok | fixed | ok | open: no search (paged, up to 200) | ok | ok | ok | ok |
| Inbox, WhatsApp | ok | ok (thread Back) | ok | fixed | ok | ok | ok | ok | ok | ok |
| Learning, Activity, Autonomy | ok | ok | ok | fixed | ok | ok | ok | ok | ok | ok |
| Approvals | ok | fixed (breadcrumb) | ok | fixed | ok | open: no search or paging of the queue | ok | ok | ok (shell destination) | ok |
| Settings and admin views | ok | fixed (breadcrumb) | ok (`#privacy`, `#language`) | fixed | ok | ok | ok | ok | ok | ok |

Open items above are listed for their own changes; none was widened into a redesign here. For Finance on the phone, statement import stays on the web ([Finance](finance.md#clients)).
