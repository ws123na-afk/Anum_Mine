# Flutter Mobile

`apps/mobile` is ANUM's cross-platform Flutter client for Android, iOS, and tablets. It implements the approved Figma foundations, authentication journey, governed workbench, responsive states, and platform-safe storage boundary.

## Implemented

- Semantic Light and Dark themes, 8 px spacing, 8 px cards, and 48 px minimum controls.
- Session restore and encrypted local token persistence.
- Local development password/OTP sign-in, password recovery, workspace session switching, onboarding, and model-provider configuration.
- Model-provider connection verification through the backend without returning provider credentials.
- Profile/session security, confirmed sign-out, and user-scoped notification preferences.
- API-backed tasks, task execution, cancellation, and resumption.
- Approval decisions, automation controls, workspace files, and durable memory.
- Push-to-talk voice commands with English/Arabic locales, editable transcript review, explicit retention, governed execution, permission recovery, and spoken status confirmation.
- Loading, empty, error, offline, permission-denied, expired-session, and responsive phone/tablet components.
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

Flutter and Dart are not installed in the current workstation environment, so source implementation exists but analyzer, widget-test, and native-build execution remain environment gates. The repository-level documentation and backend tests do not substitute for Flutter compilation. File transfer uses authenticated binary HTTP and the platform save picker; secure storage, notifications, microphone handling, and file transfer still require physical-device validation before release.
