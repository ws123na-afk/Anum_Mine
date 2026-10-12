---
name: mockup-to-flutter
description: Turn a mockup image, screenshot or Figma export into a working ANUM Flutter screen in apps/mobile, built from the app's own theme tokens, components, English/Arabic catalog, responsive contract, accessibility rules and tests. Use when the owner shares a picture of a screen and asks for it in the mobile app.
---

# Mockup to Flutter (ANUM mobile)

A mockup shows intent, not a spec to copy pixel by pixel. The result must look like the rest of ANUM, do a real job against the API and pass the gates in `CLAUDE.md` ("Definition of done", "Responsive UI quality gate"). This skill is ANUM's own; no third-party image-to-code skill was trusted enough to vendor (see `.claude/skills/SOURCES.md`).

## 1. Read the image, then the repo

1. Open the image with the Read tool. Write down, before any code:
   - the screen's job (what the user creates, reads, changes or deletes) and the API endpoints it needs (`docs/`, `packages/contracts`, `apps/mobile/lib/data/api_client.dart`); a screen with no backing endpoint is out of scope until the API exists, so say so;
   - each region top to bottom: header, metrics, lists, forms, actions;
   - every state the mockup shows and every state it leaves out (loading, empty, error, permission denied, offline, long content).
2. If the mockup is a Figma link, use the Figma MCP design-to-code workflow instead of guessing from a screenshot, then continue from step 2 here.
3. Read `apps/mobile/README.md` (routes, responsive contract), `docs/mobile.md` and `docs/figma-mobile-design.md`, and one finished feature of the same shape (for example `lib/features/privacy/`: `*_models.dart`, `*_repository.dart`, `*_controller.dart`, `*_screen.dart`).

## 2. Map the image onto the design system

Never paste colours, sizes or fonts measured from the image. Map each one to the nearest token and say in the report where the mockup was adjusted.

| In the mockup | Use |
|---|---|
| Colours | `context.palette` (`AnumPalette` in `lib/src/theme/anum_theme.dart`: `background`, `surface1..3`, `line`, `text`, `muted`, `faint`, `sky`, `violet`, `rose`, `ok`, `warn`, `stop`) or `Theme.of(context).colorScheme`. Both light and dark palettes must work. |
| Spacing | `AnumSpacing` (4 / 8 / 12 / 16 / 24 / 32 / 40 / 48 / 64). No one-off values. |
| Breakpoints | `AnumBreakpoints.compact` (600) and `.expanded` (840). |
| Type | `Theme.of(context).textTheme` styles; the theme already sets Inter with Arabic fallbacks. |
| Page shell | `AnumPage` (backdrop, safe area, pull to refresh, 1100 max width). |
| Cards, headers, rows | `AnumSurface`, `AnumHeader`, `AnumRow`, `AnumPill`, `AnumIconChip`, `AnumMetric` + `AnumMetricGrid` (`lib/src/widgets/depth.dart`); `AnumOperationalCard`, `AnumStatusBadge` (`lib/src/widgets/anum_components.dart`). |
| States | `AnumFeedback` (`loading`, `empty`, `error`, `offline`, `permission`) and `AnumEmpty` (title, message, next action). |
| Buttons, inputs, chips, sheets, dialogs | Plain Material 3 widgets; the theme styles them (48x48 minimum targets, radii, colours). |

If the mockup needs something no component covers, add it to the shared widget files with a widget test, not inside the screen.

## 3. Build the feature in layers

Follow the existing layering (models -> repository -> controller -> screen); do not add `provider`, `get_it`, `freezed`, `go_router` or other packages for it.

- **Models**: read JSON only through `JsonReader` (`lib/data/json_read.dart`); no `as` casts on decoded JSON (`strict-casts` is on).
- **Repository**: an `abstract interface class` plus an `Api…Repository` that calls `ApiClient`. Tenant/workspace headers come from the session; never hard-code them.
- **Controller**: a `ChangeNotifier` that exposes loading, data, error and permission-denied states and the command methods.
- **Screen**: lean widgets that render the controller's state. Each state shows something useful: loading, empty with a next action, error that says what failed and how to fix it, permission denied that says who can do it, and paging or search when the list can grow.
- Outward actions (send, change a listing, price or stock) go through the API's tools, ToolPolicy and approvals; the app never calls a third-party service directly and never stores provider credentials.

## 4. Copy and languages

- Every visible or spoken string goes through `AnumLocalizations` (`lib/src/localization/anum_localizations.dart`): add the key to both `_en` and `_ar`, read it with `t(key)` or `f(key, {...})` (`maybeOf(context)` in widgets that may be pumped without the delegate).
- Write real Arabic, not a copy of the English. Use directional-neutral layout: `EdgeInsetsDirectional`, `AlignmentDirectional`, `start`/`end`; icons that point (back, next) must flip in RTL.

## 5. Responsive and accessibility rules

- Decide layout from available space (`LayoutBuilder` / `MediaQuery.sizeOf`), never from device type or orientation. Below 600: single column, compact navigation. 600-839: usable either orientation without horizontal scrolling. 840 and up: navigation rail; multi-column or master/detail where it helps.
- Content-driven heights: no fixed heights or full-screen min heights for normal sections, no stacked padding (the page already pads), long lists use `ListView.builder` or slivers.
- Text scaling to 200 percent must not clip commands, status values or approval context: let text wrap, put `Text` in `Expanded`/`Flexible` inside rows, avoid fixed-height text boxes.
- Every interactive control is at least 48x48 and has a meaningful label (`tooltip`, `semanticsLabel`, or `Semantics(button: true, label: …)`); headings use `Semantics(header: true)`; status is never conveyed by colour alone.
- Keyboard and switch access: focus order follows reading order; nothing is reachable only by gesture.

## 6. Tests

Add tests next to the feature's existing ones under `apps/mobile/test/` (`mocktail` is the mocking library; fakes like `test/developer/developer_fakes.dart` are fine):

- unit tests for model parsing (valid, missing field, wrong type) and the controller's state transitions, including error and permission denied;
- widget tests that pump the screen in `MaterialApp(theme: AnumTheme.dark())` (and light where colours matter) with the localization delegates, at widths 320, 390, 768, 1024 and 1440 (set `tester.view.physicalSize`, `devicePixelRatio = 1`, `addTearDown(tester.view.reset)`), at `TextScaler.linear(2)`, and in Arabic RTL; assert no overflow exceptions (`tester.takeException()` is null), the main path works end to end with a fake repository, and `meetsGuideline(androidTapTargetGuideline)` and `meetsGuideline(labeledTapTargetGuideline)` pass;
- the route and navigation contract tests in `test/architecture/` if the screen adds a destination.

## 7. Verify and report

From `apps/mobile`, after `flutter pub get`:

```sh
dart format --output=none --set-exit-if-changed lib test tool
flutter analyze --fatal-infos --fatal-warnings
flutter test
```

Do not silence findings with `// ignore:` or `as dynamic`. Update `apps/mobile/README.md` and `docs/mobile.md` (routes, audit list) in the same change. In the report, list where the build differs from the mockup and why, and anything not finished; never present a partial screen as done.
