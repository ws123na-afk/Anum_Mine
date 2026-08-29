# Flutter Mobile

`apps/mobile` is ANUM's cross-platform Flutter client for Android, iOS, and tablets. It implements the approved Figma foundations, authentication journey, governed workbench, responsive states, and platform-safe storage boundary.

## Implemented

- Semantic Light and Dark themes, 8 px spacing, 8 px cards, and 48 px minimum controls.
- Session restore and encrypted local token persistence.
- Local development sign-in, workspace onboarding, and model-provider configuration.
- API-backed tasks, task execution, cancellation, and resumption.
- Approval decisions, automation controls, workspace files, and durable memory.
- Push-to-talk voice commands with English/Arabic locales, editable transcript review, explicit retention, governed execution, permission recovery, and spoken status confirmation.
- Loading, empty, error, offline, and responsive phone/tablet components.
- Widget and architecture tests for compact layout, accessibility semantics, route coverage, and embedded-secret detection.

## Configuration

Supply the API origin at build or run time. The Android emulator defaults to `10.0.2.2`:

```bash
flutter run --dart-define=ANUM_API_URL=http://10.0.2.2:8000/
```

Production builds must use HTTPS and OIDC. The local opaque-session screen exists for development and must be disabled or replaced in release configuration before distribution.

## Verification

```bash
cd apps/mobile
flutter pub get
flutter analyze
flutter test
flutter build apk --debug
```

Flutter and Dart are not installed in the current workstation environment, so source implementation exists but analyzer, widget-test, and native-build execution remain environment gates. File transfer uses authenticated binary HTTP and the platform save picker; it still requires physical-device validation before release.
