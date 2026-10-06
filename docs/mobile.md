# Flutter Mobile

`apps/mobile` is ANUM's cross-platform Flutter client for Android, iOS, and tablets, and the app ANUM ships to the Play Store and the App Store (applicationId and bundle identifier `com.anum.app`). It implements the approved Figma foundations, authentication journey, governed workbench, responsive states, and platform-safe storage boundary.

## Shipping App Decision

Flutter is the shipping Android and iOS app; the Kotlin client in `apps/android` is frozen (kept building in CI, no new features). Flutter is the only mobile client with Keycloak sign-in, the voice assistant and the real-data screens, and it covers iOS from the same code. The details and what "frozen" allows are in [Android](android.md#status-frozen).

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

## Native Projects

`android/` and `ios/` are committed; CI no longer runs `flutter create`. They were generated with

```bash
flutter create --platforms=android,ios --org com.anum --project-name anum_mobile .
dart run tool/configure_native.dart
```

and `tool/configure_native.dart` applies everything ANUM changes, idempotently:

- applicationId and iOS bundle identifier `com.anum.app` (the Kotlin package stays `com.anum.anum_mobile`), and the display name `ANUM`.
- The `com.anum.app` OIDC redirect scheme: the `appAuthRedirectScheme` manifest placeholder on Android and `CFBundleURLTypes` on iOS.
- Microphone, Bluetooth and recognition-service declarations on Android; `NSMicrophoneUsageDescription` and `NSSpeechRecognitionUsageDescription` on iOS; `PERMISSION_MICROPHONE` and `PERMISSION_SPEECH_RECOGNIZER` in `ios/Podfile`, without which `permission_handler` compiles the microphone request out.
- Cleartext HTTP in debug and profile builds only; `android/app/src/release/AndroidManifest.xml` turns it off for release.
- The release signing configuration (see [Release builds](#release-builds)) and `compileSdk = 37`.

To regenerate (for example after a Flutter template change), delete the folders, run the two commands above, and commit the result. `test/configure_native_test.dart` fails when a committed file differs from what the tool would write, so hand edits to these files belong in the tool. Build outputs, `local.properties`, Gradle wrapper binaries, generated plugin registrants, keystores, `key.properties`, `google-services.json` and `GoogleService-Info.plist` are gitignored.

## Release Builds

Production configuration is passed at build time; there are no flavours. Keep the values in a JSON file outside the repository (or generate it in CI) and pass it with `--dart-define-from-file`:

```json
{
  "ANUM_API_URL": "https://api.example.com/",
  "ANUM_OIDC_ISSUER": "https://id.example.com/realms/anum"
}
```

```bash
flutter build appbundle --release --dart-define-from-file=production.json
flutter build ipa --release --dart-define-from-file=production.json
```

The issuer must use HTTPS (plain HTTP is refused in release builds) and equal the API's `ANUM_KEYCLOAK_ISSUER`. Without `ANUM_OIDC_ISSUER` a release build would show the development sign-in, so a production build must always set it.

Android signing (`android/app/build.gradle.kts`). The `release` build type uses the upload key when all four values are set, from the environment or from `android/key.properties` (gitignored; same property names as Flutter's guide):

| Environment variable | `key.properties` | Value |
| --- | --- | --- |
| `ANUM_ANDROID_KEYSTORE_PATH` | `storeFile` | Keystore path, relative to `android/` or absolute. |
| `ANUM_ANDROID_KEYSTORE_PASSWORD` | `storePassword` | Keystore password. |
| `ANUM_ANDROID_KEY_ALIAS` | `keyAlias` | Upload key alias. |
| `ANUM_ANDROID_KEY_PASSWORD` | `keyPassword` | Upload key password. |

- With none set, release builds are signed with the debug key and Gradle prints a warning; that is what CI does to prove `flutter build appbundle --release` works. Such a bundle must never be uploaded.
- Setting only some of the four fails the build. `ANUM_ANDROID_REQUIRE_RELEASE_SIGNING=true` makes a missing key fail the build; the release pipeline sets it.
- Enrol in Play App Signing: Google holds the app signing key and the keystore above is only the upload key. In CI, store the keystore as a base64 secret, decode it to a temporary path, and pass the passwords as secrets.

iOS. The bundle identifier is `com.anum.app`; the redirect scheme and the microphone and speech-recognition usage strings are in `ios/Runner/Info.plist`. Signing needs the Apple team, an App Store distribution certificate and a provisioning profile for `com.anum.app` (Xcode automatic signing or `flutter build ipa --export-options-plist=...`), then upload to TestFlight. None of these exist yet ([Production plan](production-plan.md#credentials-and-decisions-needed-from-the-owner)).

## Verification

```bash
cd apps/mobile
flutter pub get
dart format --output=none --set-exit-if-changed lib test tool
flutter analyze
flutter test
flutter build apk --debug
```

`test/oidc_session_test.dart` covers token-to-session mapping, refresh (including rotation, rejection and offline failures), workspace headers, sign-out and the controller flow with a fake authenticator; `test/configure_native_test.dart` covers the native configuration and checks that the committed projects match it. CI runs format, analyze and the tests, builds a debug APK and a (debug-signed) release app bundle. A real browser round trip against Keycloak, secure storage, notifications, microphone handling and file transfer still need the device checklist below before release.

## Real-Device Test Checklist

Run on at least one physical Android phone (Android 10 or later, plus the oldest supported version), one Android tablet, one iPhone and one iPad, with a release-configured build (`--dart-define-from-file` pointing at staging, signed with the upload key or a development profile). Record device, OS version, build number and result for each line in the release evidence ([Production readiness gates](production-readiness.md)).

Install and identity
- [ ] A fresh install shows `ANUM` under the icon and installs next to the frozen Kotlin app (`com.anum.mobile`) without conflict.
- [ ] Sign-in opens the system browser (Custom Tab on Android, `ASWebAuthenticationSession` on iOS), not a webview, and returns to the app through `com.anum.app:/oauth2redirect` with the user signed in.
- [ ] Cancelling the browser returns to the sign-in screen with a message and no stuck spinner.
- [ ] A second app claiming `com.anum.app` cannot complete the sign-in (PKCE): the code is useless without the verifier.
- [ ] Kill the app after sign-in, reopen: the session restores without the browser. Leave it past the access-token lifetime: the next API call refreshes silently.
- [ ] Disable the user or revoke the session in Keycloak: the next refresh signs the app out.
- [ ] Sign-out ends the Keycloak session (a new sign-in asks for credentials) and clears secure storage even when the browser step is cancelled.
- [ ] Airplane mode during refresh keeps the session and shows the offline state; reconnecting recovers.
- [ ] A release build refuses a plain-HTTP API or issuer.

Arabic and right-to-left
- [ ] With the system language set to Arabic, layout mirrors: navigation, back arrows, lists, chips and the voice sheet run right to left; numbers and Latin identifiers inside Arabic text read correctly.
- [ ] Switching the in-app language between English and Arabic persists across restarts.
- [ ] Arabic text uses a proper Arabic font (no tofu boxes or broken joining) on both platforms.
- [ ] Android system back and the iOS edge-swipe gesture work in RTL.

Text size and accessibility
- [ ] At 200 percent system font size (Android "Font size" plus "Display size" maximum; iOS Larger Accessibility Sizes), every screen in the five Figma frames scrolls without clipped or overlapping text, and all buttons stay tappable.
- [ ] TalkBack and VoiceOver read every control, status and the orb state; focus order follows reading order in English and Arabic.
- [ ] Light and dark themes follow the system setting.

Voice
- [ ] The first microphone use shows the system prompt with ANUM's usage text (iOS also asks for speech recognition). Denying it shows the permission-recovery screen, whose button opens system settings; granting there and returning works without a restart.
- [ ] "Don't ask again" (Android) and a later revoke in settings both land on the recovery screen, not a crash or silent failure.
- [ ] English and Arabic recognition each produce an editable transcript; nothing runs before the user confirms.
- [ ] Wake-by-name, push-to-talk and spoken confirmation work with the phone speaker, wired headphones and a Bluetooth headset (on Android 12+ check whether the nearby-devices permission is requested and what happens when it is denied).
- [ ] An incoming call or another app taking audio focus stops listening cleanly.

Platform behaviour
- [ ] Deep links to task URLs open the right task after sign-in; process death and restore keep the current destination.
- [ ] File upload uses the system picker; download uses the save picker; both work with large files on mobile data.
- [ ] Tablets switch to the navigation rail at 840 logical pixels in both orientations; iPad split view and Android multi-window do not break layout.
- [ ] Safe areas: nothing is hidden under the notch, Dynamic Island, gesture bar or keyboard.
