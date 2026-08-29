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
