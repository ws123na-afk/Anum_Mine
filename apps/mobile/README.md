# ANUM Mobile

ANUM Mobile is the Flutter client for Android, iOS, and tablet supervision. Agent execution remains server-side; the client owns onboarding, task capture, approvals, automation controls, files, and responsive operational views.

## Product Routes

Authentication and onboarding use `/splash`, `/sign-in`, `/workspace-setup`, and `/model-setup`. The authenticated shell exposes `/home`, `/tasks`, `/tasks/:id`, `/voice`, `/approvals`, `/automations`, and `/files` with task, voice, approval, automation, and resource destinations.

## Voice Commands

Voice uses push-to-talk for short commands. The app requests microphone access only after the user taps the microphone, shows partial recognition, requires editable visual review, records the selected transcript-retention policy, creates a normal task, and starts the governed runtime. High-impact actions still pause for visual approval. Spoken approval alone never bypasses policy.

## Local Checks

From `apps/mobile` with Flutter available:

```sh
flutter pub get
flutter analyze
flutter test
flutter build apk --debug
```

When native wrapper directories are not present yet, initialize them once with:

```sh
flutter create --platforms=android,ios .
dart run tool/configure_native.dart
```

CI performs wrapper generation, analysis, widget tests, and a debug APK build on every change.

Widget tests exercise compact phone and expanded tablet dimensions, status semantics, minimum action sizes, feedback states, and route-contract completeness. They do not require an emulator or native SDK. APK, permission, secure-storage, microphone, notification, deep-link, and lifecycle verification still require an Android toolchain and emulator or physical device.

## Responsive Contract

- Widths below `600` use compact navigation and single-column content.
- Widths from `600` through `839` must remain usable in either orientation without horizontal scrolling.
- Widths at or above `840` use expanded navigation and may introduce multi-column layouts.
- System text scaling up to 200 percent must not clip commands, status values, or approval context.
- Interactive controls keep at least a 48 by 48 logical-pixel target and a meaningful semantic label.

## Platform Boundaries

Tokens belong in platform secure storage. Microphone, notification, and file access are requested only when the corresponding action starts. Release builds require OIDC and API configuration supplied outside source, Android and iOS signing identities, and device-level validation. Provider credentials and agent tool secrets must never be stored in the mobile application.

## Figma Implementation Audit

Audited against `docs/figma-mobile-design.md`, `docs/figma-design-state.json`, the product vision, and the roadmap on 2026-08-30.

Implemented runtime surfaces:

- Local session restoration and sign-in, workspace onboarding, model configuration, and secure session storage.
- Backend model connection testing, profile/session security, notification preferences, and confirmed sign-out.
- Task capture, task list, execution trace, cancellation, resumption, result review, and empty trace handling.
- Governed approvals with pending count, approve, and reject actions.
- Automation list with start, resume, retry, and cancel actions.
- File upload/download/delete and memory create/delete in a shared resource surface.
- Voice idle, listening, editable transcript, review, running, completion/read-aloud, permission recovery, Arabic recognition selection, and transcript retention controls.
- Loading, empty, generic error, and offline behavior on the main workspace.

Open screen and workflow gaps:

- OIDC provider sign-in is not implemented; sign-in remains the development local-session flow.
- Local OTP verification and password recovery are connected end to end. Production identity-provider recovery still depends on the selected OIDC provider.
- Authenticated workspace switching rotates and atomically persists the local session; the complete visual workspace directory remains to be implemented.
- Model testing reports success or the backend error and supports retry, but detailed provider latency, quota, and capability diagnostics are not implemented.
- Named routes now cover onboarding and each workbench destination, task URLs resolve through an authentication-gated deep link, and selected navigation state is restorable. Platform intent-filter and universal-link association still require deployment-specific domains.
- Home is currently the task surface; the separate operational overview shown in the Figma main-app coverage is absent.
- Files and memory share one destination, while deep links and independent information architecture are absent.
- Audit history and provenance review from the primary journey are absent.
- Tablet layouts switch to a labeled navigation rail at `840` logical pixels. Tasks and policy governance use master/detail panes; automation, marketplace, routing, and resources remain single-pane.
- Arabic platform locales receive Material localization, automatic RTL direction, persisted English/Arabic selection, translated core navigation and onboarding labels, and system Noto/Arial Arabic font fallbacks. Secondary feature copy still needs full catalog extraction before localization can be considered complete.
- The Android permission education screen and iOS approval bottom-sheet presentation are not wired as platform-specific workflows.
- Figma's expanded detailed-operations and enterprise screen sets are only partially represented by the current workspace and organization-operation surfaces.
- Expired-session recovery and notification preferences are implemented. Return-to-intended-route behavior after authentication is absent.

Open state gaps from the Figma state matrix:

- Success confirmation, partial-data, expired-auth, rate-limit, and cancelled states do not have shared product components or consistent screen handling.
- Skeleton loading is absent; current loading uses indeterminate progress indicators.
- Permission-denied presentation is implemented for authentication/settings and voice. File permission recovery is not yet a shared end-to-end workflow.
- Offline state displays cached-state wording, but durable offline drafts, replay, and conflict handling are not implemented.

Release acceptance must include all five Figma frames (`360x800`, `393x852`, `412x915`, `768x1024`, and `1024x768`), light and dark themes, English LTR and Arabic RTL, 200 percent text scaling, keyboard traversal, screen-reader labels, Android back behavior, iOS safe areas, deep links, and process restoration. The widget suite currently enforces the frame matrix, component RTL direction, 200 percent text scaling, minimum touch targets, status semantics, route declarations, navigation labels, and voice safety copy. Full app localization and adaptive navigation remain implementation blockers, not passing capabilities.
