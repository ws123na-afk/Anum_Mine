# Flutter Mobile

`apps/mobile` is ANUM's cross-platform Flutter client for Android, iOS, and tablets, and the app ANUM ships to the Play Store and the App Store (applicationId and bundle identifier `com.anum.app`). It implements the approved Figma foundations, authentication journey, governed workbench, responsive states, and platform-safe storage boundary.

For a plain-language list of everything the app does, what is partial and what is coming next, see [Mobile app features](mobile-features.md).

## Shipping App Decision

Flutter is the shipping Android and iOS app; the Kotlin client in `apps/android` is frozen (kept building in CI, no new features). Flutter is the only mobile client with Keycloak sign-in, the voice assistant and the real-data screens, and it covers iOS from the same code. The details and what "frozen" allows are in [Android](android.md#status-frozen).

## Implemented

- The ANUM 360 look ([design](design/anum360.md)): midnight navy with a blue-to-violet accent gradient in Dark and Light themes (Profile & Settings > Theme: dark by default, light or the phone's setting, kept on the device), the ANUM mark and orb drawn in code, gradient primary buttons, raised cards and status pills, 8 px spacing and 48 px minimum controls. The shell is a bottom bar (Home, Chat, the orb for ANUM Command, Tasks, Menu) on phones and a rail with the orb from 840 px; the Menu drawer reaches every destination ([apps/mobile/README.md](../apps/mobile/README.md#anum-360-shell)).
- Splash, onboarding (skippable, once per device), Login with Email/Mobile, Forgot password, Continue with Google and Continue with Apple, and Create account, all through Keycloak without a password field in the app ([Identity: Flutter](identity.md#flutter)).
- Profile & Settings: profile card, plan (Free plan; ANUM has no billing yet, so there is nothing to buy), and the way into My Profile, Security, Notifications, Language, Theme, Data & Privacy, Help & Support and About. The header's bell counts the approvals waiting in the workspace and opens Approvals.
- Session restore and encrypted local token persistence.
- Keycloak sign-in (authorization code + PKCE through `flutter_appauth`) with token refresh and RP-initiated sign-out when `ANUM_OIDC_ISSUER` is defined; see [Identity and sign-in](identity.md#flutter).
- Local development password/OTP sign-in, password recovery, a workspace picker that lists your memberships from the API and checks the membership before switching ([Identity](identity.md#flutter)), onboarding, and model-provider configuration.
- Model-provider connection verification through the backend without returning provider credentials.
- Profile/session security, confirmed sign-out, and user-scoped notification preferences.
- API-backed tasks, task execution, cancellation, retry with run history, rename, archive and restore, server-side status, search and archived filters with keyset paging, and "Copy run log" (the JSON export) ([Tasks](tasks.md#flutter-tasks-screens)).
- Approval decisions, automation controls, workspace files, and durable memory.
- Approval chain progress ("1 of 3 approvals", who approved) on approval cards and, while a task waits for approval, on its detail screen. Right after Run task or Resume, the approval in the run response (`approval`, with `required_approvals` and `approvers` when the API sends them; 1 and none when it does not) shows at once; once the approvals list reloads and returns that approval, the list entry replaces it, and if the list does not return it (for example offline) the run response's copy stays (`test/run_approval_test.dart`, [Approvals and risk](approvals-and-risk.md#approval-chains)).
- "Sources used" on a task's run, matching the web: shown only when the run has a `retrieval` step, one row per memory or file with its type, the file's name or the memory's text when the loaded workspace lists it (else the id), each passage's number (chunk index + 1), match score and whether it was shortened, plus the step's note for no results ("No indexed memory or file matched this task."), skipped or unavailable retrieval ([Memory](memory.md#at-run-time)). A listed file opens its detail sheet; a listed memory opens the Memory tab of Resources on its own page, scrolled to that memory and marked "Used by the run you came from". A source the workspace no longer lists (deleted, or another user's) shows its id and is not a button. While the task detail fetches the latest run the panel shows a loading line; if the fetch fails it names the error and offers "Try again", and a 401 or 403 says that members with task access can see the sources and to ask a workspace owner, with no retry. A run without a retrieval step shows no panel once loaded. The panel's strings are in the English and Arabic catalogs (`AnumLocalizations`, `sources*` keys); each row has one spoken label, ids are isolated left-to-right inside Arabic text, and the count pill moves below the title on narrow screens or at large text so it never squeezes the title. `test/sources_used_test.dart` covers every state, the memory and file links, the Arabic catalog, and layout at 320x568, 390x844, 768x1024 and 1440x900 at 100 and 200 percent text in both directions.
- The **Assistant**, first in the navigation: a streamed chat that remembers the conversation, with the conversation list, Stop, Retry, starter questions, Jump to latest and every loading, empty, error, `403` and `402` state, matching the web ([Conversations](conversations.md#mobile-flutter)). Voice replies stream too and are spoken sentence by sentence ([Voice](voice.md)).
- Push-to-talk voice commands with English/Arabic locales, editable transcript review, explicit retention, governed execution, permission recovery, and spoken status confirmation.
- Voice calls from the Voice tab's **Call** button (`lib/features/voice/call_screen.dart`, `call_controller.dart`): the dark call screen with timer, status, orb, Mute, Video, Speaker, Chat, More and End, barge-in, the transcript with Create task and Open Approvals, and the save-summary choice at the end ([Voice call mode](voice.md#call-mode)). Device speech only; **Video** sends a chosen photo, since live camera needs a plugin (see Dependencies). `test/voice/call_test.dart` covers the loop and the screen at 320, 390, 768, 1024 and 1440 wide at 100 and 200 percent text.
- A wake-by-name voice assistant with a 3D orb, and Home, Tasks, Approvals, Automations, Resources and Governance screens that show only live workspace data, with honest empty states instead of samples. The Governance (Organization) screen reads `can_manage_organization_policy` from `GET /organization/governance`: without it, policy packs and routing targets are shown read-only with a notice that only an organization administrator can change them, and package installs stay with workspace owners ([Governance](governance-and-scale.md#who-may-change-what)). Resources > Skills installs, enables, disables and uninstalls skills (uninstall asks first), shows which version is installed, and a refused change says that only workspace owners can make it; Resources > Services shows each integration's status under its text, how to fix it and its last error ([Skills](skills.md), [Tools and integrations](tools-and-integrations.md#workspace-configuration)). `test/skills_resources_test.dart` covers them and their layout at 320, 390, 768, 1024 and 1440 wide at 200 percent text. Skill details, rollback, publishing, test connection and integration tokens are web only.
- Automations has the web page's depth ([Automation](automation.md)): search and "Show more" paging, an editor with per-action step fields and overlap policy, schedules with a live server preview of the cron expression in words and its next times, pause, resume, delete, and run history with resume, retry, cancel and links to the tasks steps created. Viewers and members who did not create an automation see why a control is missing.
- Owner screens under Settings › Workspace administration: members and invitations (roles, deactivation, a one-time invitation token with copy, plus the web invitation link when `ANUM_WEB_APP_URL` is defined), accepting an invitation by token or link, monthly model budgets with usage, and the workspace approval policy (two-person rule, approval for medium risk); non-owners see the API's `403` explanation, workspace owners who are not organization administrators see the organization budget read-only (`can_manage_tenant_budget`), and members see the approval policy read-only ([Identity](identity.md#client-screens), [Model gateway](model-gateway.md#monthly-budgets), [Approvals and risk](approvals-and-risk.md#policy-screens)). Each of these screens owns its controller and disposes it when closed. A task run refused for a used-up budget (`402`) shows the budget message on Tasks; an approval refused with `403` (for example the two-person rule) shows the API's sentence on that approval's card.
- Settings › Chat memory: the user's own **Remember things from my chats** switch (saved at once; a failed save keeps the previous choice and says why), what the current state means and who can change it when it is off for the server, the organization or the role, and **Delete memories from my chats** behind a confirmation ([Memory](memory.md#per-user-switch)).
- **Developer mode** for owners ([Developer mode](developer-mode.md#clients)), at parity with the web; see [Developer mode](#developer-mode) below.
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

`ANUM_TERMS_URL` and `ANUM_PRIVACY_POLICY_URL` (optional) link the deployment's terms and privacy policy from Create account; `ANUM_APP_VERSION` sets the version shown in About. `ANUM_OIDC_CLIENT_ID`, `ANUM_OIDC_REDIRECT_URL` and `ANUM_WORKSPACE_ID` override the client, redirect and fallback workspace; `ANUM_WEB_APP_URL` (for example `http://localhost:5173/`) adds the web invitation link to new invitations ([Release builds](#release-builds)). The issuer must match the API's `ANUM_KEYCLOAK_ISSUER` exactly, which is why the emulator reaches Keycloak through `adb reverse` on `localhost` instead of `10.0.2.2`. Plain-HTTP issuers work only in debug and profile builds.

When `ANUM_OIDC_ISSUER` is not defined the app keeps the local development sign-in (password, one-time code, password recovery), which requires an API in `ANUM_AUTH_MODE=headers`. Production builds must use HTTPS and OIDC; the local sign-in screen is not shown when an issuer is configured.

### First Run

After Keycloak sign-in the app reads `GET /api/v1/onboarding` and routes by its answer:

- No tenant, workspace or membership yet: the onboarding screen calls `PUT /api/v1/onboarding`, which creates them and makes the caller the first owner. A workspace that already has members answers 403 "Ask a workspace owner to invite you"; the screen shows that message. An inactive membership answers 403 "Active workspace membership required" and shows as "Access denied".
- No workspace model saved: the model step first checks whether a model would answer anyway: the workspace's saved one (`model_configured` of `GET /api/v1/onboarding`, for example saved meanwhile from another device) or the server's configured model (the `configured-model` target of `GET /api/v1/routing/targets`; the built-in mock planner does not count). Only then does it offer "Skip and use the server's model (<model>)" (or "Continue with this workspace's model"). While it checks it says so; if the check fails it names the error with "Check again". Otherwise there is no skip and the screen says why.
  - Owners (the only role that may save the model) see the form: "Test, save, and continue" saves and tests one. Without a model to fall back on they must finish this step.
  - Providers (`lib/features/settings/model_choice.dart`, shared with Settings › Model connection › Change model): Anthropic (Claude), OpenAI compatible, Ollama and the local mock. Choosing Anthropic (Claude) shows an "Anthropic API key" field (required, write-only), an optional base URL (empty means `https://api.anthropic.com`) and [model routing](model-gateway.md#model-routing): "Quick replies model" (chat, voice and memory; default `claude-haiku-5-5`, effort Low) and "Planning model" (tasks and agent runs, also the workspace's default model; default `claude-sonnet-5-5`, the model's default effort). Settings then shows both routed models on the Model connection card. Saving always tests the connection; a failure keeps the form open with the API's message.
  - Members and viewers cannot save one, so they see no form: the screen says a workspace owner must connect a model (Settings, Model connection), with "Check again" and "Sign in with another account".
  - A workspace created on this screen whose `PUT /api/v1/onboarding` answer already reports a model goes straight to the workbench.
- The Keycloak user needs the admin-set `tenant_id` attribute ([Identity](identity.md)); without it the app says the account has no ANUM organization. The workspace comes from the token's `workspace_id` claim (the `default_workspace_id` attribute) or, failing that, the build's `ANUM_WORKSPACE_ID`. When neither names one, the app lists the caller's workspaces with `GET /api/v1/me/workspace-memberships` (which needs no selected workspace, [Identity](identity.md#my-workspaces)):
  - exactly one: it is selected without asking;
  - several: "Where do you want to work?" lists them (name, id, role). A tap confirms the membership (`GET /api/v1/workspace-memberships/current`) and opens it; a refusal shows "Access denied" and "Try again" returns to the list. The choice is remembered in secure storage per organization and user (`SecureWorkspacePreferenceStore`; it survives sign-out because it is an id, not a credential), so the next sign-in opens it directly while it is still listed; Settings, Switch workspace updates it;
  - none: "You are not in a workspace" explains that a workspace owner must invite the person or an administrator must set `default_workspace_id`, with "Check again" and "Sign in with another account";
  - a failed list shows the error with "Try again".
- Tests: `test/first_run_test.dart` (every workspace branch, the remembered choice, the skip only when a model answers, loading and error states, members, connecting Claude with its default routing, layouts from 360 pixels wide at 2x text to 1024 pixels); `test/settings_model_test.dart` (changing the model from Settings, including Claude from the sheet at 360x640 and 1024x768, and the routing fields sent only for Anthropic).

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
- `android:allowBackup="false"` and `android:fullBackupContent="false"`, so sessions and tokens in app storage stay out of Android backups.
- The release signing configuration (see [Release builds](#release-builds)) and `compileSdk = 37`.

To regenerate (for example after a Flutter template change), delete the folders, run the two commands above, and commit the result. `test/configure_native_test.dart` fails when a committed file differs from what the tool would write, so hand edits to these files belong in the tool. Build outputs, `local.properties`, Gradle wrapper binaries, generated plugin registrants, keystores, `key.properties`, `google-services.json` and `GoogleService-Info.plist` are gitignored.

## Developer mode

`lib/features/developer/` implements [Developer mode](developer-mode.md) over the same `/api/v1/developer` routes as the web:

- `DeveloperController` checks the caller's role once per workspace (`GET /api/v1/workspace-memberships/current`; it checks again after a workspace switch) and drives the Developer screen: setup, the request list (20 per page, `before` paging, `q` search), create (with the project picker), Check now, Cancel, Approve & merge and Confirm merge, and the projects (`GET/POST/PATCH/DELETE /api/v1/developer/projects`, Test connection, Install workflow) shown by `DeveloperProjectsScreen` and `DeveloperProjectFormScreen`. `WorkspaceHome` lists Developer (`WorkspaceDestination.developer`, last, so the other positions never move) only for an owner; `/developer` opens it by link, where anyone else gets the explanation.
- `DeveloperAssist` is the chat and voice side (one instance each, `source` `text` and `voice`): it intercepts the developer commands, resolves the project (`POST /api/v1/developer/resolve`), asks "Which project …?" when it is not clear (buttons or a spoken name) and always confirms "I'll ask the developer to: … in <project>. Start?", refuses non-owners, polls while its screen is on screen and announces ready pull requests. "approve" only opens the merge approval; `confirmMerge` (a tap) is the only call that approves. `VoiceController.developer` and `VoiceController.sayLocal` speak these replies and listen once for the answer.
- Every state is covered: checking the role, owner-only (and `403`), role check failed, loading, load failed with Try again, setup incomplete with the setting names, empty with example requests, no search matches with Clear search, a failed refresh that keeps the list, `402` with Open Settings, `409`/`429`/`422`/`502` with the API's sentence, a refused merge (two-person rule) and a link that cannot be opened.
- Tests: `test/developer/` covers the helpers, the controller, the chat and voice confirmation flows (voice never approves), the navigation (owners only, More on phones, Arabic label, deep link), and layouts at 320x568, 390x844, 768x1024, 1024x768 and 1440x900, at 200 percent text and right to left, including the open merge approval and the confirmation card in the Assistant and Voice.
- Making the voice header and the Assistant's notices fit a short phone at 200 percent text (the voice controls wrap under the name; the notices, including the developer card, get only the height the header and composer leave) came with this change.

## Tyre planning

**Home > Tyre planning** (route `/tyres`, `lib/features/tyre/`) shows the fleet tyre dashboard, the tyres due in a month (previous and next month, search, paging) and the open alerts, which members and owners acknowledge ([Tyre planning](tyre-planning.md)). Connecting the data source, mapping and reports stay on the web; with nothing synced the screen says who does that. A tapped "Tyre alerts" push opens `/tyres/alerts`. Tests: `test/tyre/tyre_test.dart` (every state, Arabic, 320 to 1440 wide at 200% text).

## Phone push

Push notifications for approvals waiting, tasks finished or failed and developer pull requests ready ([Notifications](notifications.md)). Code: `lib/features/push/` (the `PushMessaging` interface, its Firebase implementation in `firebase_push_messaging.dart`, the controller and the Settings card).

- **When the app asks.** Never on first launch. **Settings › Notifications › Turn on notifications** asks for the system permission (Android 13+ and iOS), and after the first approval decision the app offers it once (a notice with **Turn on**). A refused permission shows **Open phone settings**.
- **Registration.** After sign-in, a phone that already allows notifications registers its FCM token for the current workspace (`POST /api/v1/me/devices`, re-encrypting the token with the server's current key) and again when Firebase rotates it. Before sign-out and before a workspace switch it removes its registration while the session still works; after sign-out it deletes the Firebase token so the next person on the phone gets a new one; after a switch it registers in the new workspace.
- **Taps and foreground.** A tapped push opens the approval queue, the task or the Developer screen, also from a cold start (kept until sign-in finishes). A push for another workspace says so instead of opening. A push that arrives while the app is open shows as an in-app notice with **Open**; the system does not show it.
- **Without Firebase files** the app builds and runs; Settings says push is not set up in this build. The server side is off until `ANUM_FCM_SERVICE_ACCOUNT_JSON` is set, and the app then says that instead.
- **Tests.** `test/push/` covers the destinations, the controller with a fake push channel and API (never prompting on its own, registering, unregistering on sign-out and switches, the one-time offer), the Settings card states and its layout at 360x640 with 200 percent text, 390x844 and 1024x768. `test/configure_native_test.dart` checks the native wiring below.

### Owner steps: Firebase project

Do these once; nothing here is committed. File and secret names only.

1. In the Firebase console, create a project (or use the Google Cloud project you already have) and add two apps: **Android** with package name `com.anum.app`, and **iOS** with bundle id `com.anum.app`.
2. Download **`google-services.json`** to `apps/mobile/android/app/google-services.json` and **`GoogleService-Info.plist`** to `apps/mobile/ios/Runner/GoogleService-Info.plist`. Both paths are in `apps/mobile/.gitignore`. The Android build applies the Google services plugin (`com.google.gms.google-services`, declared in `android/settings.gradle.kts`) only when the file exists; the iOS build copies the plist in the **Copy Firebase config** build phase only when it exists. If an AGP upgrade breaks the plugin, bump its version in `settings.gradle.kts`.
3. **iOS only.** In the Apple Developer account create an **APNs authentication key** (`.p8`) and upload it in Firebase under *Project settings › Cloud Messaging › Apple app configuration* with its key id and team id. In Xcode, open `ios/Runner.xcworkspace`, select the Runner target and add the **Push Notifications** capability (it writes `Runner/Runner.entitlements` with `aps-environment`; commit that file once the App ID has the capability, since CI signs the release with that App ID). `Info.plist` already has `UIBackgroundModes` `remote-notification`.
4. **Server.** In Google Cloud IAM, create a service account with the **Firebase Cloud Messaging API Admin** role, create a JSON key and put the whole file on one line into the API's `ANUM_FCM_SERVICE_ACCOUNT_JSON` secret (Helm: the `anum-app` Secret; Render: the dashboard; Oracle: `infra/oracle/.env`, [Phone push](deploy-oracle.md#phone-push)). Delete the downloaded key file afterwards.
5. **CI release builds.** To ship push in store builds, the release workflow must write the two files before building, from repository secrets you create (suggested names `ANUM_GOOGLE_SERVICES_JSON` and `ANUM_GOOGLE_SERVICE_INFO_PLIST`). That workflow step is not written yet: until it is, release builds from CI have push off.
6. Check on a real phone (the Real-Device Test Checklist below): sign in, **Turn on notifications**, ask for something that needs approval from another device, lock the phone, and tap the push.
## WhatsApp inbox

`lib/features/whatsapp/` mirrors the web inbox for owners and members ([WhatsApp](whatsapp.md)): conversations with filters, search and paging; a thread with the 24-hour window, the suggested reply with its provenance label, a reply sent for approval, and Approve & send / Reject for owners (the payload hash is sent back). It opens from the app bar on wide layouts and from More on phones. Connecting the number and template replies stay on the web. Tests: `test/whatsapp/whatsapp_test.dart` (320 to 1440 px, 200% text, English and Arabic).

## Dependencies

`firebase_core` 4.15 and `firebase_messaging` 16.7 (phone push) were checked against `android.builtInKotlin=false` before adding them: `firebase_messaging`'s Android code is Java only, and `firebase_core` applies `kotlin-android` itself when built-in Kotlin is off, so neither needs a pin.

`url_launcher` opens pull request and run links in the browser. `url_launcher_android` is held at 6.3.29, the last release written in Java: 6.3.30 and later rely on AGP 9 built-in Kotlin like `file_picker` 11 (below) and would not compile while `android.builtInKotlin=false`. `AndroidManifest.xml` declares the `https` VIEW intent in `<queries>` for Android 11 package visibility.

`file_picker` is held at 10.3.10. Version 11 applies no Kotlin plugin under AGP 9 and relies on AGP's built-in Kotlin, but `android/gradle.properties` sets `android.builtInKotlin=false` because `flutter_tts` still applies the Kotlin Gradle Plugin itself; with both, 11's Kotlin sources are never compiled and the debug build fails with `cannot find symbol FilePickerPlugin`. Upgrade once every plugin builds with built-in Kotlin (then set `android.builtInKotlin=true`). 11.0.2 fixes an Android path traversal (CWE-22) when resolving paths from external content providers, so this is the first follow-up once `flutter_tts` supports built-in Kotlin.

Voice calls add no plugin. `camera` (its default `camera_android_camerax`) and `image_picker_android` declare only `com.android.library` and keep Kotlin sources, so like `file_picker` 11 they need AGP built-in Kotlin and would compile nothing here; live camera video and server speech (recorder and audio player) in calls wait for `android.builtInKotlin=true`. Until then **Video** picks a photo with `file_picker` (already a dependency).

## Release Builds

Production configuration is passed at build time; there are no flavours. Keep the values in a JSON file outside the repository (or generate it in CI) and pass it with `--dart-define-from-file`:

```json
{
  "ANUM_API_URL": "https://api.example.com/",
  "ANUM_OIDC_ISSUER": "https://id.example.com/realms/anum",
  "ANUM_WEB_APP_URL": "https://app.example.com/"
}
```

A release build whose `ANUM_API_URL` is not an `https://` address starts on an error screen naming the problem and creates no API client.

```bash
flutter build appbundle --release --dart-define-from-file=production.json
flutter build ipa --release --dart-define-from-file=production.json
```

The issuer must use HTTPS (plain HTTP is refused in release builds) and equal the API's `ANUM_KEYCLOAK_ISSUER`. Without `ANUM_OIDC_ISSUER` a release build would show the development sign-in, so a production build must always set it.

`ANUM_WEB_APP_URL` is optional: the address of the web app (absolute `http(s)` URL, path allowed). With it, a newly created invitation also shows and copies the web client's link `<ANUM_WEB_APP_URL>#invitation=<token>&workspace=<workspace_id>`, which opens the web app's accept form pre-filled ([Identity](identity.md#client-screens)). Without it, or with a value that is not an absolute `http(s)` URL, the app shows the token and the workspace id only. The release workflow adds it to the generated defines file when the optional repository variable `ANUM_PRODUCTION_WEB_APP_URL` is set.

Android signing (`android/app/build.gradle.kts`). The `release` build type uses the upload key when all four values are set, from the environment or from `android/key.properties` (gitignored; same property names as Flutter's guide):

| Environment variable | `key.properties` | Value |
| --- | --- | --- |
| `ANUM_ANDROID_KEYSTORE_PATH` | `storeFile` | Keystore path, relative to `android/` or absolute. |
| `ANUM_ANDROID_KEYSTORE_PASSWORD` | `storePassword` | Keystore password. |
| `ANUM_ANDROID_KEY_ALIAS` | `keyAlias` | Upload key alias. |
| `ANUM_ANDROID_KEY_PASSWORD` | `keyPassword` | Upload key password. |

- With none set, release builds are signed with the debug key and Gradle prints a warning; that is what CI does to prove `flutter build appbundle --release` works. Such a bundle must never be uploaded.
- Setting only some of the four fails the build. `ANUM_ANDROID_REQUIRE_RELEASE_SIGNING=true` makes a missing key fail the build; the release pipeline sets it.
- Enrol in Play App Signing: Google holds the app signing key and the keystore above is only the upload key. In CI the keystore is a base64 secret decoded to a temporary path ([Release pipeline](#release-pipeline)).

iOS. The bundle identifier is `com.anum.app`; the redirect scheme and the microphone and speech-recognition usage strings are in `ios/Runner/Info.plist`. The project keeps automatic signing with no team; the release pipeline supplies the team and an App Store Connect API key, and Xcode creates the cloud-managed distribution certificate and the App Store profile for `com.anum.app`. Locally, `flutter build ipa` works once a team is selected in Xcode; the pipeline needs no Mac of the owner's ([Owner checklist](#owner-checklist-windows)). App Transport Security stays at its HTTPS-only default (no `NSAppTransportSecurity` exceptions), and the Android release manifest turns cleartext off, so a store build cannot reach a plain-HTTP API or issuer.

## Release Pipeline

`.github/workflows/release-clients.yml` builds the store apps on every `v*` tag and on manual runs (Actions, Release clients, Run workflow; tick "upload" to send a manual build to the stores). The desktop half is deferred by the owner: its jobs and the draft GitHub release are skipped unless the `ANUM_RELEASE_DESKTOP` variable is `true` (tags) or the "desktop" input is ticked (manual runs), see [Desktop](desktop.md#release-pipeline). Both mobile jobs decide what they can do with `.github/scripts/mobile-release-config.sh`, which only tests whether each secret and variable is empty and never prints a value:

| Run | Missing secrets or variables | Result |
| --- | --- | --- |
| Manual, "upload" unticked (build only) | all signing secrets, or a production URL | Passes with a notice: Android builds nothing, iOS runs an unsigned compile check. Lets the workflow be tried before the secrets exist. |
| Release tag, or manual with "upload" | anything the store upload needs: the signing secrets, `PLAY_SERVICE_ACCOUNT_JSON` (Android), `ANUM_PRODUCTION_API_URL`, `ANUM_PRODUCTION_OIDC_ISSUER` | Fails with "Android release not configured" or "iOS release not configured", naming every missing name. |
| Any | some but not all of one platform's four signing secrets | Fails with "signing incomplete", naming the missing ones. |
| Any | a production URL variable set without `https://` | Fails: that is a mistake, not a missing credential. |

`services/api/tests/test_mobile_release.py` runs the script for each row.

- Android (`ubuntu-latest`): decodes the upload keystore into `$RUNNER_TEMP`, builds `flutter build appbundle --release` with `ANUM_ANDROID_REQUIRE_RELEASE_SIGNING=true` and the production values, refuses a debug-signed bundle, uploads `anum-<version>-<build>.aab` and its SHA-256 as the artifact `anum-android-release`, then (on tags, or manual runs with "upload") sends it to the Play internal track with `r0adkll/upload-google-play` pinned by commit. The keystore file is deleted at the end of the job.
- iOS (`macos-latest`): with the Apple secrets, `flutter build ios --config-only` writes the production configuration and `xcodebuild archive` and `-exportArchive` sign with automatic, cloud-managed signing through the API key (`flutter build ipa` cannot pass an API key to Xcode). The export options (`method app-store-connect`, `manageAppVersionAndBuildNumber false`) keep the pipeline's build number. The IPA and its SHA-256 are uploaded as `anum-ios-release` and, on tags or with "upload", to TestFlight with `xcrun altool`. Without the secrets the job runs `flutter build ios --release --no-codesign` as a compile check. CI's `Flutter iOS compile check` job runs the cheaper debug variant on every pull request.

Both builds get the same generated defines file (`ANUM_API_URL`, `ANUM_OIDC_ISSUER` and, when set, `ANUM_WEB_APP_URL`). The version name comes from the tag (`v1.2.3` becomes `1.2.3`; manual runs use the `X.Y.Z` of `version:` in `pubspec.yaml`) and the build number is the workflow run number plus `ANUM_BUILD_NUMBER_OFFSET`, so every upload has a higher Play version code and iOS build number than the last.

Repository variables:

| Variable | Value |
| --- | --- |
| `ANUM_PRODUCTION_API_URL` | Production API origin, HTTPS (becomes `ANUM_API_URL`). Shared with the desktop build. |
| `ANUM_PRODUCTION_OIDC_ISSUER` | Production Keycloak issuer, HTTPS, equal to the API's `ANUM_KEYCLOAK_ISSUER` (becomes `ANUM_OIDC_ISSUER`). |
| `ANUM_PRODUCTION_WEB_APP_URL` | Optional. Production web app address (becomes `ANUM_WEB_APP_URL`, adds the web invitation link). |
| `ANUM_BUILD_NUMBER_OFFSET` | Optional. A whole number added to the run number, for example to continue above a build uploaded by hand. |
| `ANUM_PLAY_RELEASE_STATUS` | Optional. `completed` by default; set `draft` while the Play app has never been reviewed (Play refuses `completed` releases for a draft app). |
| `ANUM_RELEASE_DESKTOP` | Optional. `true` also builds the desktop installers on tags. Leave unset while desktop releases are deferred. |

Repository secrets (names only here; values never go into the repository, an issue, a pull request, a chat with an assistant or a log; type them only into GitHub's secret form or pipe them to `gh secret set`):

| Secret | Value |
| --- | --- |
| `ANUM_ANDROID_KEYSTORE_BASE64` | The upload keystore in base64 (`[Convert]::ToBase64String([IO.File]::ReadAllBytes("<path>\anum-upload.jks"))` in PowerShell, `base64 -w0 anum-upload.jks` on Linux). |
| `ANUM_ANDROID_KEYSTORE_PASSWORD`, `ANUM_ANDROID_KEY_ALIAS`, `ANUM_ANDROID_KEY_PASSWORD` | Keystore password, key alias and key password. |
| `PLAY_SERVICE_ACCOUNT_JSON` | Optional. JSON key of a Google Cloud service account invited in Play Console (Users and permissions) with release rights for `com.anum.app`. Without it the bundle is built but not uploaded. |
| `APPLE_TEAM_ID` | The ten-character Apple Developer team ID. |
| `APP_STORE_CONNECT_API_KEY_ID`, `APP_STORE_CONNECT_API_ISSUER_ID` | Key ID and issuer ID from App Store Connect, Users and Access, Integrations. |
| `APP_STORE_CONNECT_API_KEY_P8` | The downloaded `AuthKey_<id>.p8` file's contents. The key needs the Admin role so Xcode may create the cloud-managed distribution certificate and profiles. |

Before the first upload: create the app `com.anum.app` in Play Console and upload the first bundle by hand (the Play API cannot create an app), enrol in Play App Signing, and create the app record for `com.anum.app` in App Store Connect. The step-by-step version is the [Owner checklist](#owner-checklist-windows).

Cutting a release: set the version (see [Desktop](desktop.md#release-pipeline) for all three files), then tag `vX.Y.Z` and push the tag. The run uploads to the Play internal track and TestFlight; promote from there in Play Console (internal to closed or production, with a staged rollout percentage) and App Store Connect (TestFlight groups, then submit for review).

Rolling back a store release:

- Play: halt the staged rollout in Play Console (Release, the track, Halt rollout), which stops new installs of that version. Play cannot downgrade installed users, so fix forward: tag a new patch version, which gets a higher version code, and roll it out. Internal-track builds can simply be superseded.
- App Store: before release, remove the build from review or from the TestFlight groups. After release, pause the phased release if it is still running, then ship a fixed build through an expedited review; an approved version cannot be replaced by an older one.

The pipeline has not run yet: it needs the keys and secrets above, and no signed build or macOS job can be run from a Linux development container. `services/api/tests/test_mobile_release.py` checks that the deployed realm's `anum-flutter` client registers exactly `com.anum.app:/oauth2redirect` (no wildcards) as its redirect and post-logout redirect, that the app's defaults, the Android `appAuthRedirectScheme` and applicationId, and the iOS URL scheme and bundle identifier agree with it, and that the workflow uploads `com.anum.app` and keeps the desktop jobs gated.

### Owner Checklist (Windows)

The owner already has the Google Play developer account and the Apple Developer account. Everything below runs in a browser and Windows PowerShell. No Mac is needed: the iOS job runs on GitHub's macOS runner and signs with Apple's cloud-managed certificate through the App Store Connect API key. The examples use `https://app.example.com` (web), `https://api.example.com` (API) and `https://auth.example.com` (Keycloak, issuer `https://auth.example.com/realms/anum`); use the real hostnames. The deployed realm already contains the `anum-flutter` client with the app's exact redirect, so Keycloak needs no change for the apps.

Secrets and variables go in GitHub, Settings, Secrets and variables, Actions (the Secrets tab or the Variables tab). The `gh` commands below do the same from PowerShell after `gh auth login`, run inside a clone of the repository; `gh secret set NAME` without `--body` prompts for the value or reads it from the pipe.

| Item | How to get it (Windows) | Where it goes |
| --- | --- | --- |
| Upload keystore | `keytool -genkeypair` (Android step 1) | Secret `ANUM_ANDROID_KEYSTORE_BASE64` (base64 of the file) |
| Keystore password | Chosen at `keytool` | Secrets `ANUM_ANDROID_KEYSTORE_PASSWORD` and `ANUM_ANDROID_KEY_PASSWORD` (same value) |
| Key alias | `upload` (the `-alias` given to `keytool`) | Secret `ANUM_ANDROID_KEY_ALIAS` |
| Play app | Play Console, Create app; first `.aab` uploaded by hand | Play Console (package `com.anum.app`, Play App Signing) |
| Play service account key | Google Cloud console, service account, JSON key; invited in Play Console | Secret `PLAY_SERVICE_ACCOUNT_JSON` (file contents) |
| Apple Team ID | developer.apple.com, Membership details | Secret `APPLE_TEAM_ID` |
| Bundle ID | Certificates, Identifiers and Profiles, Identifiers | Apple Developer (`com.anum.app`) |
| App record | App Store Connect, Apps, New App | App Store Connect (bundle ID `com.anum.app`) |
| API key Issuer ID | App Store Connect, Users and Access, Integrations | Secret `APP_STORE_CONNECT_API_ISSUER_ID` |
| API key Key ID | Same page, the key's row | Secret `APP_STORE_CONNECT_API_KEY_ID` |
| API key `.p8` | Same page, Download (once) | Secret `APP_STORE_CONNECT_API_KEY_P8` (file contents) |
| API address | The API's public HTTPS origin, with a trailing slash | Variable `ANUM_PRODUCTION_API_URL` |
| Keycloak issuer | `https://<Keycloak host>/realms/anum`, equal to the API's `ANUM_KEYCLOAK_ISSUER` | Variable `ANUM_PRODUCTION_OIDC_ISSUER` |
| Web app address | The web app's public HTTPS origin | Variable `ANUM_PRODUCTION_WEB_APP_URL` (optional) |
| Play release status | `draft` until the first internal release is rolled out, then delete | Variable `ANUM_PLAY_RELEASE_STATUS` |

Android, once:

1. Create the upload keystore outside the repository. `keytool` ships with Android Studio's bundled JDK:

   ```powershell
   $keytool = "C:\Program Files\Android\Android Studio\jbr\bin\keytool.exe"
   New-Item -ItemType Directory -Force "$HOME\anum-keys" | Out-Null
   & $keytool -genkeypair -v -keystore "$HOME\anum-keys\anum-upload.jks" -alias upload `
     -keyalg RSA -keysize 2048 -validity 10000
   ```

   On Linux or macOS (any JDK 17's `keytool`), the same keystore and secrets:

   ```bash
   mkdir -p ~/anum-keys && chmod 700 ~/anum-keys
   keytool -genkeypair -v -keystore ~/anum-keys/anum-upload.jks -alias upload \
     -keyalg RSA -keysize 2048 -validity 10000
   base64 -w0 ~/anum-keys/anum-upload.jks | gh secret set ANUM_ANDROID_KEYSTORE_BASE64   # macOS: base64 -i <file>
   gh secret set ANUM_ANDROID_KEYSTORE_PASSWORD   # prompts; nothing is echoed or kept in shell history
   gh secret set ANUM_ANDROID_KEY_ALIAS --body upload
   gh secret set ANUM_ANDROID_KEY_PASSWORD
   ```

   It asks for a keystore password and the certificate names. The keystore is PKCS12, so the key password is the keystore password. Keep the file and the password in a password manager: Play accepts only bundles signed with this upload key (a lost key can be reset through Play support, not recovered).
2. Store it as secrets:

   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("$HOME\anum-keys\anum-upload.jks")) | gh secret set ANUM_ANDROID_KEYSTORE_BASE64
   gh secret set ANUM_ANDROID_KEYSTORE_PASSWORD   # paste the keystore password
   gh secret set ANUM_ANDROID_KEY_ALIAS --body upload
   gh secret set ANUM_ANDROID_KEY_PASSWORD        # the same password again
   ```
3. Play Console: Create app (name `ANUM`, App, Free, accept the declarations). The package name is fixed by the first bundle uploaded, which is `com.anum.app`. Play App Signing is on by default for new apps: keep the Google-generated app signing key when the first release asks.
4. Google Cloud console (any project): enable the Google Play Android Developer API, then IAM and admin, Service accounts, Create service account (for example `anum-play-release`, no project roles), Keys, Add key, JSON. In Play Console, Users and permissions, Invite new users: the service account's e-mail, the ANUM app, "Release apps to testing tracks" (add "Release to production" later if the pipeline should do that), Invite. Then `Get-Content -Raw "<path>\<key>.json" | gh secret set PLAY_SERVICE_ACCOUNT_JSON` and delete the downloaded file. Set this secret only at step 3 of the first release below; the permissions can take up to a day to apply.

iOS, once:

1. developer.apple.com, Account, Membership details: copy the Team ID, then `gh secret set APPLE_TEAM_ID --body <team id>`.
2. Certificates, Identifiers and Profiles, Identifiers, +, App IDs, App: explicit bundle ID `com.anum.app`, description `ANUM`, no extra capabilities.
3. App Store Connect, Apps, +, New App: iOS, name `ANUM` (or another free store name), bundle ID `com.anum.app`, SKU `anum-ios`.
4. App Store Connect, Users and Access, Integrations, App Store Connect API (the Account Holder requests access the first time), Team Keys, +: name `anum-release`, access Admin (cloud-managed signing needs it to create the distribution certificate and the App Store profile). Copy the Issuer ID shown above the list and the key's Key ID, and download the `.p8` (possible only once):

   ```powershell
   gh secret set APP_STORE_CONNECT_API_ISSUER_ID --body <issuer id>
   gh secret set APP_STORE_CONNECT_API_KEY_ID --body <key id>
   Get-Content -Raw "$HOME\Downloads\AuthKey_<key id>.p8" | gh secret set APP_STORE_CONNECT_API_KEY_P8
   ```

   Keep the `.p8` in the password manager and delete the download. No certificate, provisioning profile or `match` repository is made by hand: the first signed run creates them. The Account Holder must have accepted the current agreements in App Store Connect (Business), or the upload is refused.

Production addresses:

```powershell
gh variable set ANUM_PRODUCTION_API_URL --body "https://api.example.com/"
gh variable set ANUM_PRODUCTION_OIDC_ISSUER --body "https://auth.example.com/realms/anum"
gh variable set ANUM_PRODUCTION_WEB_APP_URL --body "https://app.example.com/"
gh variable set ANUM_PLAY_RELEASE_STATUS --body draft
```

The API URL keeps its trailing slash; the issuer has none and must equal the API's `ANUM_KEYCLOAK_ISSUER` exactly. Leave `ANUM_RELEASE_DESKTOP` unset.

First release:

1. Actions, Release clients, Run workflow on `main` with "upload" unticked. It builds the signed bundle and the signed IPA without sending them anywhere; a red Android or iOS job here points at a wrong secret.
2. Open the run, download the `anum-android-release` artifact and unzip it (`Expand-Archive anum-android-release.zip`). In Play Console, Testing, Internal testing, Create new release, upload the `.aab`, add a tester list, then Save, Review and Roll out. This first manual upload is required: the Play API cannot upload to an app that has no bundle yet.
3. Set `PLAY_SERVICE_ACCOUNT_JSON` (Android step 4).
4. Tag and push from PowerShell: `git tag v0.1.0`, then `git push origin v0.1.0`. Tags must be plain `vX.Y.Z` and higher than the last release; keep `version:` in `apps/mobile/pubspec.yaml` (and the desktop files, [Desktop](desktop.md#release-pipeline)) at the same `X.Y.Z` so manual runs match. The tag run uploads the bundle to the Play internal track and the IPA to TestFlight; the build number is the run number, so it is always above the manual run's.

Where the builds land:

- GitHub: the run's summary lists each build's SHA-256, and its Artifacts hold `anum-android-release` and `anum-ios-release`.
- Play Console, Testing, Internal testing: while `ANUM_PLAY_RELEASE_STATUS` is `draft` the release waits as a draft; open it and Roll out. Once one internal release is out, delete that variable so later tags roll out by themselves.
- App Store Connect, TestFlight: the build appears after Apple's processing (usually 5 to 30 minutes). Answer the export compliance question for each build (the owner's declaration; the app itself uses only HTTPS and the operating system's keychain), then add the build to a tester group.

Promoting past internal testing needs the store listing, privacy policy and data-safety forms ([Production plan](production-plan.md#stage-6-client-release)). Write the policy from the [privacy policy template](privacy-policy.md) and answer Google Play's Data safety and Apple's App Privacy forms from [Store data safety](store-data-safety.md). The in-app account-data deletion path both stores require is Settings → Privacy → Delete my data ([Privacy](privacy.md)).

## Verification

```bash
cd apps/mobile
flutter pub get
dart format --output=none --set-exit-if-changed lib test tool
flutter analyze --fatal-infos --fatal-warnings
flutter test
flutter build apk --debug
```

Run `dart format` after `flutter pub get`: the formatter takes its language version, and so its style, from the pubspec's SDK constraint (`>=3.4.0`, the short style). Without resolved packages it falls back to the newest language version and rewrites almost every file. CI's "Flutter mobile" job runs the same check.

`analysis_options.yaml` turns on the analyzer's `strict-casts`, `strict-inference` and `strict-raw-types` modes, so `flutter analyze` rejects implicit downcasts from `dynamic` (decoded JSON is read through the [typed helpers](#typed-json-parsing), not casts), generic calls whose type arguments cannot be inferred (page routes are `MaterialPageRoute<void>`, empty literals carry their element types) and raw generic types. Keep them on; do not silence a finding with `as dynamic` or an `// ignore:`.

`test/oidc_session_test.dart` covers token-to-session mapping, refresh (including rotation, rejection and offline failures), workspace headers, sign-out and the controller flow with a fake authenticator; `test/configure_native_test.dart` covers the native configuration and checks that the committed projects match it. CI runs format, analyze and the tests, builds a debug APK and a (debug-signed) release app bundle, and compiles the iOS app (debug, unsigned) on macOS. A real browser round trip against Keycloak, secure storage, notifications, microphone handling and file transfer still need the device checklist below before release.

### Typed JSON Parsing

Models and repositories read API responses and the stored session through `JsonReader` (`lib/data/json_read.dart`, exported by `api_models.dart`): `string`, `integer`, `number`, `boolean`, `date`, `object`, `objects` and `strings`, each with an `opt…` variant that returns null (or an empty list for `optObjects`) when the field is missing or null. A field that is missing or has the wrong type fails with one `JsonShapeException`, a `FormatException` whose message names the field path and the expected type, for example `Unexpected response: "data[1].approvers[0]" should be an object, found a string.` Valid payloads read exactly as the casts they replaced did (numbers accept ints and doubles; optional fields keep their defaults). Every caller that handled the old cast errors catches all errors (`catch (e)` or `on Object`), so a malformed response still lands in the screen's error state, now with that sentence as its message; the stored-session reader still clears an unreadable session. Tolerant reads that never fail (budget usage numbers, invitation timestamps, the approval policy's `updated_at`, the retrieval step's metadata) keep checking types themselves. `test/json_parsing_test.dart` covers missing fields, wrong types and wrong list elements on the main models and repositories.

## Real-Device Test Checklist

Run on at least one physical Android phone (Android 10 or later, plus the oldest supported version), one Android tablet, one iPhone and one iPad, with a release-configured build (`--dart-define-from-file` pointing at staging, signed with the upload key or a development profile). Record device, OS version, build number and result for each line in the release evidence ([Production readiness gates](production-readiness.md)).

Install and identity
- [ ] A fresh install shows `ANUM` under the icon and installs next to the frozen Kotlin app (`com.anum.mobile`) without conflict.
- [ ] Sign-in opens the system browser (Custom Tab on Android, `ASWebAuthenticationSession` on iOS), not a webview, and returns to the app through `com.anum.app:/oauth2redirect` with the user signed in.
- [ ] Cancelling the browser returns to the sign-in screen with a message and no stuck spinner.
- [ ] A second app claiming `com.anum.app` cannot complete the sign-in (PKCE): the code is useless without the verifier.
- [ ] Kill the app after sign-in, reopen: the session restores without the browser. Leave it past the access-token lifetime: the next API call refreshes silently.
- [ ] Disable the user or revoke the session in Keycloak: the next refresh signs the app out.
- [ ] Sign-out ends the Keycloak session (a new sign-in asks for credentials) and clears the session and tokens from secure storage even when the browser step is cancelled (only the remembered workspace choice stays).
- [ ] A Keycloak user without `default_workspace_id` and with several workspaces gets the workspace list after sign-in; after a choice, sign-out and sign-in open the same workspace.
- [ ] On Android, install the new build over one that stored a session with `flutter_secure_storage` 9 (any build before the 10.x upgrade): the session survives (the plugin migrates it to its new ciphers) or, if migration fails, the app shows the sign-in screen instead of crashing.
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
- [ ] Phone push with the owner's Firebase files: Settings › Notifications asks only when **Turn on notifications** is tapped; an approval from another device shows "Approval needed" on the locked phone and tapping it opens Approvals; a finished task opens its detail; a push while the app is open shows the in-app notice; after sign-out the phone gets nothing; denying the permission shows **Open phone settings**.
- [ ] Wake-by-name, push-to-talk and spoken confirmation work with the phone speaker, wired headphones and a Bluetooth headset (on Android 12+ check whether the nearby-devices permission is requested and what happens when it is denied).
- [ ] An incoming call or another app taking audio focus stops listening cleanly.
- [ ] Backgrounding the app or pressing back out of the workspace stops listening immediately (the microphone indicator goes off).

Platform behaviour
- [ ] Deep links to task URLs open the right task after sign-in; process death and restore keep the current destination.
- [ ] As an owner, Developer › a request › Pull request and Coding run open in the system browser (not inside the app); as a member, Developer is not in the navigation.
- [ ] File upload uses the system picker; download uses the save picker; both work with large files on mobile data.
- [ ] Tablets switch to the navigation rail at 840 logical pixels in both orientations; iPad split view and Android multi-window do not break layout.
- [ ] Safe areas: nothing is hidden under the notch, Dynamic Island, gesture bar or keyboard.
