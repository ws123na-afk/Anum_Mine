# Figma Mobile Design System

ANUM's development-ready mobile design source is [ANUM Mobile - Flutter Design System](https://www.figma.com/design/3OmLkcqS7QaWc0FGUyi6kc).

## Product Memory

The mobile product is the ANUM governed agent workbench. Its primary journey is:

`Local or OIDC sign-in -> organization/workspace onboarding -> model setup -> create task -> observe agent execution -> approve a governed action -> inspect results, files, and memory -> resume or automate work -> review audit history`

The design must remain a quiet, information-dense operational tool. It uses ANUM's teal and charcoal identity, restrained surfaces, explicit status labels, and visible governance. It must not become a decorative marketing interface.

## Figma Structure

The file contains `00 Cover`, `01 Foundations`, `02 Components`, `03 Auth`, `04 Main App`, `05 Workflows`, `06 States`, `07 Android`, `08 iOS`, `09 Tablet`, `10 Prototype`, and `11 Dev Handoff`.

## Foundations

- Primitive palette: 23 variables.
- Dimensions: 20 spacing, radius, size, and minimum-target variables.
- Semantic colors: 20 variables with Light and Dark modes.
- Typography: nine Inter LTR and nine Noto Sans Arabic RTL styles.
- Elevation: three restrained shadow styles.
- Minimum mobile touch target: 48 px.
- Layout spacing follows `4 / 8 / 12 / 16 / 24 / 32 / 40 / 48 / 64`.

Every variable has web and Android/Flutter-oriented code syntax. Primitive variables are intentionally unscoped; semantic variables use explicit fill, text, or stroke scopes.

## Platform Frames

- iOS: `393 x 852`
- Android: `412 x 915`
- Small Android: `360 x 800`
- Tablet portrait: `768 x 1024`
- Tablet landscape: `1024 x 768`

Use Auto Layout and constraints throughout. Keep approximately 90 percent of the product shared between Android and iOS. Platform pages cover only safe areas, back behavior, permissions, sheets, keyboard handling, and system insets.

## Component Contract

Components must use semantic variables and predictable Flutter-friendly names. Required families include buttons, inputs, search, dropdowns, selection controls, cards, chips, badges, avatars, app bars, bottom navigation, tabs, dialogs, sheets, toasts, skeletons, empty/error states, offline banners, menus, lists, and task timeline rows.

Material 3 is subscribed in the Figma file and may supply platform primitives and icons. ANUM-owned product components remain local so their API, token bindings, and Flutter mapping stay stable.

## Current Status

The development-ready Figma scope is complete. The file contains all 12 planned pages, 63 variables, 18 text styles, three elevation styles, 21 component sets, 97 component variants, and seven prototype transitions.

Completed screen coverage includes first launch, sign-in, workspace onboarding, model connection and verification, home, task queue, resumable task timeline, governed approvals, durable automations, files and memory, Android permissions, iOS approval sheets, and tablet portrait and landscape layouts. `06 States` defines loading, empty, error, offline, permission, success, partial-data, expired-auth, rate-limit, and cancelled behavior for data-backed screens.

The component library includes Button, Input, Checkbox, Switch, Radio, Bottom Navigation, App Bar, Status Badge, Task Card, Operational Row, Timeline Step, Feedback State, Dialog, Toast, Bottom Sheet, Skeleton, Search, Chip, Tab, Avatar, and Voice Capture. Dropdown and menu selection use the Input plus Bottom Sheet pattern to keep one selection model across platforms.

Voice design coverage includes idle, listening, editable review, running, visual approval escalation, completed/read-aloud, and permission-denied screens. The voice safety workflow explicitly prevents spoken commands from bypassing task persistence, policy checks, approvals, or audit history.

The later approved Figma coverage adds OTP and password recovery, profile/security, notifications, workspace switching, model providers, eight detailed-operations screens, eight enterprise screens, three Android treatments, two iOS treatments, and tablet portrait/landscape treatments. Their exact node IDs are recorded in the state ledger. This expanded design coverage does not mean every workflow is implemented in Flutter: OTP, forgot-password recovery, workspace switching, and the complete detailed enterprise/platform treatments remain implementation work.

The approved state section separately specifies loading, empty, offline, error, session-expired, microphone-permission, rate-limited, partial-result, cancelled, and success screens. Flutter has direct coverage for several of these states, but rate limiting, partial results, and consistent success/cancelled presentation are still incomplete across data-backed features.

Visual QA caught and fixed nested badge labels, timeline constraints, feedback glyph alignment, semantic binding on generated screens, Avatar bindings, and splash preview overflow. The final structural audit found no unbound white text. The resumable node ledger and exact audit counts are stored in `docs/figma-design-state.json`.

The Flutter implementation now exists under `apps/mobile` with semantic themes, secure session storage, authentication and onboarding, model setup with a backend connection test, task timelines and resumption, approvals, automations, files, memory, voice, organization operations, profile/session security, notification preferences, responsive components, and mobile tests. Authentication and settings distinguish loading, offline, permission-denied, expired-session, validation, and generic failures. Directional spacing and alignment are used on the new authentication and settings surfaces as an RTL layout foundation.

This is a source-implementation statement, not a device-verification claim. Remaining gates are Flutter SDK analysis and widget-test execution, native wrapper generation, Android/iOS builds, OIDC release configuration, signing, complete Arabic localization, adaptive tablet navigation validation, physical-device permission and secure-storage tests, and production service verification. Exact Figma page, collection, component, approved-screen, voice, and handoff node IDs remain recorded in `docs/figma-design-state.json`; IDs not returned by Figma are not inferred.
