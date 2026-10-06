# Flutter Mobile

`apps/mobile` is ANUM's cross-platform Flutter client for Android, iOS, and tablets. It implements the approved Figma foundations, authentication journey, governed workbench, responsive states, and platform-safe storage boundary.

## Implemented

- Semantic Light and Dark themes, 8 px spacing, 8 px cards, and 48 px minimum controls.
- Session restore and encrypted local token persistence.
- Keycloak sign-in (authorization code + PKCE through `flutter_appauth`) with token refresh and RP-initiated sign-out when `ANUM_OIDC_ISSUER` is defined; see [Identity and sign-in](identity.md#flutter).
- Local development password/OTP sign-in, password recovery, workspace session switching, onboarding, and model-provider configuration.
- Model-provider connection verification through the backend without returning provider credentials.
- Profile/session security, confirmed sign-out, and user-scoped notification preferences.
- API-backed tasks, task execution, cancellation, and resumption.
- Approval decisions, automation controls, workspace files, and durable memory.
- Push-to-talk voice commands with English/Arabic locales, editable transcript review, explicit retention, governed execution, permission recovery, and spoken status confirmation.
- A wake-by-name voice assistant with a 3D orb, and Home, Tasks, Approvals, Automations, Resources and Governance screens that show only live workspace data, with honest empty states instead of samples.
- Loading, empty, error, offline, permission-denied, expired-session, and responsive phone/tablet components.
- Widget and architecture tests for compact layout, accessibility semantics, route coverage, and embedded-secret detection.

## Configuration

Supply the API origin at build or run time. The Android emulator defaults to `10.0.2.2`:

```bash
flutter run --dart-define=ANUM_API_URL=http://10.0.2.2:8000/
```

Keycloak sign-in is enabled by naming the issuer. The client defaults to `anum-flutter` with redirect `com.anum.app:/oauth2redirect`:

```bash
adb reverse tcp:8080 tcp:8080
flutter run \
  --dart-define=ANUM_API_URL=http://10.0.2.2:8000/ \
  --dart-define=ANUM_OIDC_ISSUER=http://localhost:8080/realms/anum
```

`ANUM_OIDC_CLIENT_ID`, `ANUM_OIDC_REDIRECT_URL` and `ANUM_WORKSPACE_ID` override the client, redirect and fallback workspace. The issuer must match the API's `ANUM_KEYCLOAK_ISSUER` exactly, which is why the emulator reaches Keycloak through `adb reverse` on `localhost` instead of `10.0.2.2`. Plain-HTTP issuers work only in debug and profile builds.

When `ANUM_OIDC_ISSUER` is not defined the app keeps the local development sign-in (password, one-time code, password recovery), which requires an API in `ANUM_AUTH_MODE=headers`. Production builds must use HTTPS and OIDC; the local sign-in screen is not shown when an issuer is configured.

The native projects are generated (`flutter create`) and then configured by `dart run tool/configure_native.dart`, which also registers the `com.anum.app` redirect scheme: the `appAuthRedirectScheme` manifest placeholder on Android and `CFBundleURLTypes` on iOS.

## Verification

```bash
cd apps/mobile
flutter pub get
dart format --output=none --set-exit-if-changed lib test tool
flutter analyze
flutter test
flutter build apk --debug
```

`test/oidc_session_test.dart` covers token-to-session mapping, refresh (including rotation, rejection and offline failures), workspace headers, sign-out and the controller flow with a fake authenticator; `test/configure_native_test.dart` covers the native redirect registration. A real browser round trip against Keycloak still needs an emulator or device.

Flutter and Dart are not installed in the current workstation environment, so source implementation exists but analyzer, widget-test, and native-build execution remain environment gates. The repository-level documentation and backend tests do not substitute for Flutter compilation. File transfer uses authenticated binary HTTP and the platform save picker; secure storage, notifications, microphone handling, and file transfer still require physical-device validation before release.
