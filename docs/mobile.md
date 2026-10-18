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
- Keycloak sign-in (authorization code + PKCE through `flutter_appauth`) with token refresh and RP-initiated sign-out when `ANUM_OIDC_ISSUER` is defined; see [Identity and sign-in](identity.md#flutter). "Continue with Google / Apple", "Create account" (privacy policy acceptance first) and the pending-account screen when the API offers them ([Google and Apple sign-in](identity.md#google-and-apple-sign-in)); on iOS Google is shown only together with Apple (App Store Guideline 4.8).
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

Supply the API origin at build or run time. Debug and profile builds default to the Android emulator's `10.0.2.2`; release builds default to the production API `https://api.anum360os.com/` ([Release builds](#release-builds)):

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

When `ANUM_OIDC_ISSUER` is not defined, a debug or profile build keeps the local development sign-in (password, one-time code, password recovery), which requires an API in `ANUM_AUTH_MODE=headers`. A release build never shows it: without the define it signs in through the production issuer `https://auth.anum360os.com/realms/anum`. The local sign-in screen is not shown when an issuer is configured.

The CI artifact `anum-mobile-debug` (from `ci.yml`) is such a debug build, made for a USB-connected phone with `adb reverse` (`ANUM_API_URL=http://127.0.0.1:8000/`, no issuer): installed on its own it opens the development sign-in and cannot reach production. Phones use the [installable APK](#installable-apk) or a store build.

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
- English and Arabic: the Developer screen, the request detail with its merge approval, Projects and the project form, and the developer card in the Assistant and Voice read their strings from `AnumLocalizations` (`dev.*`, `da.*` keys in `lib/src/localization/developer_autonomy_messages.dart`, spread into the catalogs). `DeveloperController` writes its notices in the screen's language (the screens set `controller.l`). Counts use the Arabic plural forms through `AnumLocalizations.p` (CLDR: zero, one, two, few, many, other). Repository, branch, path, workflow and token fields keep left-to-right text while their labels follow the page direction. Still English: the timeline's stage labels and the request summaries, which the API writes, and the spoken developer conversation in `developer_assist.dart`. `test/developer/developer_arabic_test.dart` covers the list at 320 to 1440 px (200 percent text at 320), the detail and merge in Arabic (the payload hash stays left to right), Projects at 200 percent text, and tap targets.
- Making the voice header and the Assistant's notices fit a short phone at 200 percent text (the voice controls wrap under the name; the notices, including the developer card, get only the height the header and composer leave) came with this change.

## Tyre planning

**Home > Tyre planning** (route `/tyres`, `lib/features/tyre/`) shows the fleet tyre dashboard, the tyres due in a month (previous and next month, search, paging) and the open alerts, which members and owners acknowledge ([Tyre planning](tyre-planning.md)). Connecting the data source, mapping and reports stay on the web; with nothing synced the screen says who does that. A tapped "Tyre alerts" push opens `/tyres/alerts`. Tests: `test/tyre/tyre_test.dart` (every state, Arabic, 320 to 1440 wide at 200% text).
## Calls & Meetings

The **Meetings** tab under Resources ([Calls & Meetings](meetings.md), code `lib/features/meetings/`):
- the list, with search, a status filter and paging;
- "Call summary" recent activity;
- import of a transcript file or recording through the streamed upload (`HttpWorkspaceFileTransfer.uploadTo`), with progress and cancel;
- import from an already connected Zoom or Teams account; connecting happens in the web app;
- a detail page: AI-labelled summary, decisions, action items (done, **Create task**), the follow-up draft (save, copy, send with approval from a connected mailbox) and the searchable transcript, right to left for Arabic meetings.

Every state is handled: loading, empty with an import action, error with retry, viewer and permission notes. Tests: `test/meetings_test.dart` (API client, list and detail, Arabic RTL, 320 to 1440 widths at 200% text).

## ANUM Command

`lib/features/command/` implements [ANUM Command](command.md) over `/api/v1/commands`, `/api/v1/quick-commands`, the task run and bulk-cancel routes, approvals and the event stream:

- `CommandController` (one per app, so a command keeps being followed after "Keep working in background") drives the stages: Command (orb, suggestion chips from `GET /api/v1/commands/suggestions`, keyboard, attach a photo or file, Tap to speak), Listening (device speech recognition in English or Arabic, timer, Stop, the transcript stays editable and nothing is planned until Continue), Understanding and Plan (`POST /api/v1/commands/preview`; edit the request, edit, reorder and remove steps; blocked steps stop the start), Executing (`POST /api/v1/commands`, then every step's `POST /api/v1/tasks/{id}/run` in parallel; live from `GET /api/v1/events/stream`, polled while the stream is down; Stop cancels the unfinished steps), Results (Summary, Details, Files; Approve all sends each pending approval with its own payload hash; Share saves the Markdown report or copies the summary; View full report) and Next steps.
- `QuickCommandsScreen` is the Work, Personal and Custom quick commands (create, edit, move, delete, run). The home orb and ANUM Command tile open `/command` (`WorkspaceHome.onOpenCommand`); the orb and primary buttons reuse the shell's `AnumOrb` and `AnumGradientButton`. `CommandProgressCard` is the home card for a command working in the background; `AnumApp` shows a notice with Open when a followed command finishes or needs approval while its screen is closed. Routes `/command` and `/quick-commands`.
- Copy is in `command_strings.dart` (English and Arabic, read through the app's locale). A tyre step that has the tyre tools but no saved TyrePulse connection shows "Not connected" with a "Connect TyrePulse under Tyre planning" button that opens `/tyres`. The camera button attaches a photo as a workspace file; the model does not read images yet (docs/command.md, Not done yet).
- Tests: `test/command/` covers parsing, the controller (plan, edit, start, live refresh, polling fallback, approve all, listening, attachments, quick commands, report) and the screens at 320, 390, 768, 1024 and 1440 wide, at 200 percent text and right to left, with the tap-target guidelines.

## Autonomy

`lib/features/autonomy/` (Settings › Autonomy › Activity and Autonomy levels, [Autonomy](autonomy.md)) is in English and Arabic: the feed, suggestions, filters, Undo, "Always ask for this", levels, thresholds and every load, empty, error and owner-only state read from `AnumLocalizations` (`auto.*` keys). `levelLabel`, `categoryLabel`, `patternLabel` and a suggestion's sentence take the catalog (English by default), and the controllers write their notices and validation messages in the screen's language. A suggestion says how many times it was approved with the Arabic plural forms ("وافقت عليه 3 مرات"). `test/autonomy_test.dart` checks both screens in Arabic, right to left, at 320 to 1440 px and 200 percent text, the Arabic notices, and tap targets.

## Agents

Home opens **Agents** ([Agents](agents.md#clients)): the agents the caller can see with All, Work, Personal and Custom filters, search and paging; an agent's page with Chat (the Assistant on that agent's threads), Call (the voice call screen; a call summary from pasted notes is under More), Edit and More (pause or resume, delete), and the Overview (give it a task, recent tasks), Skills, Memory and Settings tabs; and an editor that starts from a template or from scratch. The server decides every permission; the app shows its sentence when it refuses. Selected knowledge sources are picked on the web. Text is in English and Arabic (`lib/features/agents/agents_strings.dart`); `test/agents/agents_test.dart` covers the states, the requests and layouts from 320 px at 200 percent text to 1440x900.

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
5. **CI release builds.** The release workflow (`release-clients.yml`) never reads the files above from a checkout: for CI builds, add the file contents as GitHub repository secrets instead of copying the files. `ANUM_ANDROID_GOOGLE_SERVICES_JSON` holds the whole `google-services.json` (`Get-Content -Raw "<path>\google-services.json" | gh secret set ANUM_ANDROID_GOOGLE_SERVICES_JSON` in PowerShell) and `ANUM_IOS_GOOGLE_SERVICE_INFO_PLIST` the whole `GoogleService-Info.plist` (same command with that file and name). Before building, the Android bundle job, the installable APK job and the iOS job write them with `.github/scripts/mobile-firebase-config.sh`; the Android value must be JSON with a client whose package name is `com.anum.app`, or the job fails saying so (the value is never printed). Both secrets are optional: without one, that build has no phone push and the run shows a notice. The run summary says for each build whether push config was included.
6. Check on a real phone (the Real-Device Test Checklist below): sign in, **Turn on notifications**, ask for something that needs approval from another device, lock the phone, and tap the push.
## Calendar, email summary and report sections (ANUM 360)

`lib/features/reports/workspace_tool_routes.dart` registers `/reports` and `/reports/<id>` (the library's report screens, which draw a report's typed sections with `ReportSectionsView`: tabs, metric tiles, bar and donut charts drawn with `CustomPainter`, ranked list, Save to Files, Ask ANUM; [Reports](reports.md)), `/calendar` and `/calendar/events/<id>` ([Calendar](calendar.md): day strip, timeline with Join and Call, suggested next steps, attendees and replies, add, edit, cancel, delete) and `/email/summary` ([Email](email.md): All / Urgent / Replied / Other). The voice call of the mockups is the Voice tab's Call ([Voice](voice.md#call-mode)). Tests: `test/workspace_tools/` (320 to 1440 px, 200% text, Arabic RTL, tap-target guidelines).

## ANUM 360 lists: Tasks, Automations, Integrations, Chat

The owner's mockups (`docs/design/anum360-mockups/mobile-14-screens.jpg`, screens 07 and 10-12; [ANUM 360](design/anum360.md)) built on the existing APIs. Shared pieces are in `lib/features/workspace/list_kit.dart` (title row with the gradient **+**, pill tabs with counts that keep 48 dp targets, rows in one raised card that put the trailing control under the text on a narrow screen at large text, priority pills, load more); every value comes from `AnumPalette` and `AnumSpacing`, and every string is in the English and Arabic catalogs.

- **Tasks** ([Tasks](tasks.md#flutter-tasks-screens)): All / My Tasks / Agents / Completed, priority pills, due labels, the create sheet with priority, due date and Start now, and planning edits on the detail. Priority and due date are new API fields (`0048_task_planning`).
- **Automations** ([Automation](automation.md)): All / Active / Paused / Templates. Each row shows its schedule in words (from `GET /automation/schedules`, all of the workspace's schedules in one call) or its next run, the last run, and a switch that pauses or resumes it for real (`POST /workflows/{id}/disable|enable`; a refusal says why and the switch stays where the server left it; people who may not change it see a status pill). **Custom Automation** opens the editor; **Templates** (daily business report, meeting summary, email follow-up, price monitoring, weekly review, monthly reminder) fill the editor with steps that use only `task.create` and `notify`, and after saving the detail offers the suggested schedule, which is added only if saved. The API has no draft state, so the mockup's **Draft** tab is **Paused** (the automations that do not fire).
- **Integrations** (`lib/features/integrations/`, Menu › Integrations; Resources › Services when the app has no API repository): every integration `GET /integrations` reports, All / Connected / Available with counts, search when the list is long, Connected / Needs attention pills and **Connect**. Connect does the real thing for each kind: an owner turns a configured service on or off for the workspace, tests it, and stores or removes a workspace key (never shown again; 12-4096 printable characters checked before sending); an email provider opens the inbox, where each person connects their own mailbox; a service the server is not configured for names the operator setting (for example `ANUM_SEARXNG_URL`); members are told only owners can change integrations.
- **Chat with ANUM**: the header reads "ANUM 360" with Online or Answering…, questions sit in the accent gradient, **Attach** adds text files (`.txt`, `.md`, `.csv`, `.json`, `.log`, at most 6 KB each and 8,000 characters for the whole question; the text travels inside the question, fenced and named, since chat has no file tools) and **Dictate** puts device speech to text into the draft in the app's language ([Conversations](conversations.md#mobile-flutter)). Result cards come from server-side tool results, not from the model's text.
- **Tests** (`test/lists/`): models and repositories against scripted API responses, every state, the main paths end to end with fakes, tap-target and label guidelines, and layouts at 320, 390, 768, 1024 and 1440 wide at 200 percent text in English and Arabic (light and dark); `lists_localization_test.dart` checks that every new string has real Arabic with the same placeholders.
- **Not finished.** The web Tasks view does not show priority or due dates yet. AI Agents and Agent Detail are the custom-agents work's screens.

## Home and ANUM Proactive

The ANUM 360 Home (`lib/features/home/`, [home.md](home.md)) reads one summary call (`GET /api/v1/home/summary`), the caller's pins and quick commands, and the insights list. Pins open a web app in the browser (https only), switch to a pinned workspace or open a feature; quick commands and insight commands are handed to ANUM Command, which shows the plan before anything runs. Tests: `test/home/home_test.dart` (parsing, controller states, main paths, 320-1440 px at 200 % text in English and Arabic, tap-target and label guidelines).

## Email Center

`lib/features/mail_center/` is the Email Center on the workspace's own mail server for owners and members ([Email: Email Center](email.md#the-email-center-on-the-phone)), opened from Menu, More, **Email Center** (named route `/mail`, registered in `workspace_tool_routes.dart`). Mailboxes (own and shared with you), folders, messages, writing with send through approval, attachments (add to drafts, download received ones), send later with the Scheduled list, delete for good through approval, From identities, sharing and send as settings, contacts with recipient suggestions, and AI (summarize, label, Label all, draft a reply). Files: `mail_center_models.dart`, `mail_directory_models.dart`, `mail_center_repository.dart`, `mail_center_controller.dart`, `mail_center_screens.dart`, `mail_outgoing.dart` (attachments, the send time picker, Scheduled), `mail_directory.dart` (contacts, sharing, identities, the recipient field), `mail_assist.dart` (AI) and `mail_center_transfer.dart` (the byte transfer interface; `HttpMailFileTransfer` in `src/infrastructure/mobile_adapters.dart`). No new plugin: files are picked and saved with `file_picker` (already used by Files) and uploaded with the Files streaming upload; mail is shown as plain text, never as HTML. Tests: `test/mail_center/mail_center_test.dart` (every new flow with a fake repository and transfer, plus 320 to 1440 px, 200% text, English and Arabic).

## WhatsApp inbox

`lib/features/whatsapp/` mirrors the web inbox for owners and members ([WhatsApp](whatsapp.md)): conversations with filters, search and paging; a thread with the 24-hour window, the suggested reply with its provenance label, a reply sent for approval, and Approve & send / Reject for owners (the payload hash is sent back). It opens from the app bar on wide layouts and from More on phones. Connecting the number and template replies stay on the web. Tests: `test/whatsapp/whatsapp_test.dart` (320 to 1440 px, 200% text, English and Arabic).

## Dependencies

`firebase_core` 4.15 and `firebase_messaging` 16.7 (phone push) were checked against `android.builtInKotlin=false` before adding them: `firebase_messaging`'s Android code is Java only, and `firebase_core` applies `kotlin-android` itself when built-in Kotlin is off, so neither needs a pin.

`url_launcher` opens pull request and run links in the browser. `url_launcher_android` is held at 6.3.29, the last release written in Java: 6.3.30 and later rely on AGP 9 built-in Kotlin like `file_picker` 11 (below) and would not compile while `android.builtInKotlin=false`. `AndroidManifest.xml` declares the `https` VIEW intent in `<queries>` for Android 11 package visibility.

`file_picker` is held at 10.3.10. Version 11 applies no Kotlin plugin under AGP 9 and relies on AGP's built-in Kotlin, but `android/gradle.properties` sets `android.builtInKotlin=false` because `flutter_tts` still applies the Kotlin Gradle Plugin itself; with both, 11's Kotlin sources are never compiled and the debug build fails with `cannot find symbol FilePickerPlugin`. Upgrade once every plugin builds with built-in Kotlin (then set `android.builtInKotlin=true`). 11.0.2 fixes an Android path traversal (CWE-22) when resolving paths from external content providers, so this is the first follow-up once `flutter_tts` supports built-in Kotlin.

Voice calls add no plugin. `camera` (its default `camera_android_camerax`) and `image_picker_android` declare only `com.android.library` and keep Kotlin sources, so like `file_picker` 11 they need AGP built-in Kotlin and would compile nothing here; live camera video and server speech (recorder and audio player) in calls wait for `android.builtInKotlin=true`. Until then **Video** picks a photo with `file_picker` (already a dependency).

## Fonts

Owner decision of 12 October 2026: the app bundles the theme's typefaces instead of falling back to the system font. `assets/fonts` holds Inter (cut to Latin) and Noto Sans Arabic as static instances at weights 400, 500, 600, 700 and 800 (about 110 KB and 146 KB each, about 1.3 MB in all), built with fontTools from the variable Google Fonts sources, the same sources as the web's variable files ([Web localization](web-localization.md#fonts)). Static files are used because Flutter picks a font file per `FontWeight`; with one variable file it would synthesise bold instead of using the weight axis. The app uses no weight above 800 except one `w900`, which falls back to 800.

The theme's stack is `Inter` with `Noto Sans Arabic`, `Noto Naskh Arabic` and `Arial` as fallbacks, so Arabic uses the bundled face and anything missing still falls back to the system. Both fonts are under the SIL Open Font License; the licence texts ship as assets (`assets/fonts/OFL-*.txt`) and `registerFontLicenses()` (`lib/src/font_licenses.dart`, called in `main`) adds them to the licence page (Profile, About, View licenses). `test/fonts_test.dart` checks the declared files, the fallback order and the licence registration.

## Release Builds

Production configuration is passed at build time; there are no flavours. Keep the values in a JSON file outside the repository (or generate it in CI) and pass it with `--dart-define-from-file`:

```json
{
  "ANUM_API_URL": "https://api.example.com/",
  "ANUM_OIDC_ISSUER": "https://id.example.com/realms/anum",
  "ANUM_WEB_APP_URL": "https://app.example.com/"
}
```

Without defines a release build uses the owner's production hosts (`productionApiUrl` in `lib/src/anum_app.dart`, `productionOidcIssuer` in `lib/features/auth/oidc.dart`):

| Define | Release default | Debug and profile default |
| --- | --- | --- |
| `ANUM_API_URL` | `https://api.anum360os.com/` | `http://10.0.2.2:8000/` |
| `ANUM_OIDC_ISSUER` | `https://auth.anum360os.com/realms/anum` | none: the local development sign-in |
| `ANUM_OIDC_CLIENT_ID` / `ANUM_OIDC_REDIRECT_URL` | `anum-flutter` / `com.anum.app:/oauth2redirect` | the same |

A define always wins, so another deployment builds with its own values. A release build whose API is not an `https://` address, points at a development machine (`localhost`, `127.0.0.1`, `10.0.2.2`), or whose issuer is not an `https://` production address starts on an error screen naming the problem and creates no API client (`releaseConfigError`, `test/release_config_test.dart`); it can never fall back to the development sign-in.

```bash
flutter build appbundle --release --dart-define-from-file=production.json
flutter build ipa --release --dart-define-from-file=production.json
```

The issuer must use HTTPS (plain HTTP is refused in release builds) and equal the API's `ANUM_KEYCLOAK_ISSUER`. Without `ANUM_OIDC_ISSUER` a release build uses the production issuer above, never the development sign-in.

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
- Android installable APK (`android-apk`, every run): builds `flutter build apk --release` with the same production values and uploads `anum-<version>-<build>.apk` and its SHA-256 as the artifact `anum-android-apk` ([Installable APK](#installable-apk)). Signed with the upload key when the four signing secrets are set, otherwise with the debug key and a notice. It is never sent to Play.
- iOS (`macos-latest`): with the Apple secrets, `flutter build ios --config-only` writes the production configuration and `xcodebuild archive` and `-exportArchive` sign with automatic, cloud-managed signing through the API key (`flutter build ipa` cannot pass an API key to Xcode). The export options (`method app-store-connect`, `manageAppVersionAndBuildNumber false`) keep the pipeline's build number. The IPA and its SHA-256 are uploaded as `anum-ios-release` and, on tags or with "upload", to TestFlight with `xcrun altool`. Without the secrets the job runs `flutter build ios --release --no-codesign` as a compile check. CI's `Flutter iOS compile check` job runs the cheaper debug variant on every pull request.

All mobile builds get the same generated defines file (`ANUM_API_URL`, `ANUM_OIDC_ISSUER` and `ANUM_WEB_APP_URL`). Each mobile job falls back to the owner's hosts when a variable is not set: `https://api.anum360os.com/`, `https://auth.anum360os.com/realms/anum` and `https://app.anum360os.com/` (the desktop jobs do not). The version name comes from the tag (`v1.2.3` becomes `1.2.3`; manual runs use the `X.Y.Z` of `version:` in `pubspec.yaml`) and the build number is the workflow run number plus `ANUM_BUILD_NUMBER_OFFSET`, so every upload has a higher Play version code and iOS build number than the last.

Repository variables:

| Variable | Value |
| --- | --- |
| `ANUM_PRODUCTION_API_URL` | Production API origin, HTTPS (becomes `ANUM_API_URL`). Mobile default `https://api.anum360os.com/`. Shared with the desktop build. |
| `ANUM_PRODUCTION_OIDC_ISSUER` | Production Keycloak issuer, HTTPS, equal to the API's `ANUM_KEYCLOAK_ISSUER` (becomes `ANUM_OIDC_ISSUER`). Mobile default `https://auth.anum360os.com/realms/anum`. |
| `ANUM_PRODUCTION_WEB_APP_URL` | Optional. Production web app address (becomes `ANUM_WEB_APP_URL`, adds the web invitation link). Mobile default `https://app.anum360os.com/`. |
| `ANUM_BUILD_NUMBER_OFFSET` | Optional. A whole number added to the run number, for example to continue above a build uploaded by hand. |
| `ANUM_PLAY_RELEASE_STATUS` | Optional. `completed` by default; set `draft` while the Play app has never been reviewed (Play refuses `completed` releases for a draft app). |
| `ANUM_RELEASE_DESKTOP` | Optional. `true` also builds the desktop installers on tags. Leave unset while desktop releases are deferred. |

Repository secrets (names only here; values never go into the repository, an issue, a pull request, a chat with an assistant or a log; type them only into GitHub's secret form or pipe them to `gh secret set`):

| Secret | Value |
| --- | --- |
| `ANUM_ANDROID_KEYSTORE_BASE64` | The upload keystore in base64 (`[Convert]::ToBase64String([IO.File]::ReadAllBytes("<path>\anum-upload.jks"))` in PowerShell, `base64 -w0 anum-upload.jks` on Linux). |
| `ANUM_ANDROID_KEYSTORE_PASSWORD`, `ANUM_ANDROID_KEY_ALIAS`, `ANUM_ANDROID_KEY_PASSWORD` | Keystore password, key alias and key password. |
| `PLAY_SERVICE_ACCOUNT_JSON` | Optional. JSON key of a Google Cloud service account invited in Play Console (Users and permissions) with release rights for `com.anum.app`. Without it the bundle is built but not uploaded. |
| `ANUM_ANDROID_GOOGLE_SERVICES_JSON` | Optional. The whole `google-services.json` of the Firebase Android app `com.anum.app` ([Owner steps: Firebase project](#owner-steps-firebase-project)). Without it the Android builds have no phone push. |
| `ANUM_IOS_GOOGLE_SERVICE_INFO_PLIST` | Optional. The whole `GoogleService-Info.plist` of the Firebase iOS app `com.anum.app`. Without it the iOS build has no phone push. |
| `APPLE_TEAM_ID` | The ten-character Apple Developer team ID. |
| `APP_STORE_CONNECT_API_KEY_ID`, `APP_STORE_CONNECT_API_ISSUER_ID` | Key ID and issuer ID from App Store Connect, Users and Access, Integrations. |
| `APP_STORE_CONNECT_API_KEY_P8` | The downloaded `AuthKey_<id>.p8` file's contents. The key needs the Admin role so Xcode may create the cloud-managed distribution certificate and profiles. |

Before the first upload: create the app `com.anum.app` in Play Console and upload the first bundle by hand (the Play API cannot create an app), enrol in Play App Signing, and create the app record for `com.anum.app` in App Store Connect. The step-by-step version is the [Owner checklist](#owner-checklist-windows).

Cutting a release: set the version (see [Desktop](desktop.md#release-pipeline) for all three files), then tag `vX.Y.Z` and push the tag. The run uploads to the Play internal track and TestFlight; promote from there in Play Console (internal to closed or production, with a staged rollout percentage) and App Store Connect (TestFlight groups, then submit for review).

Rolling back a store release:

- Play: halt the staged rollout in Play Console (Release, the track, Halt rollout), which stops new installs of that version. Play cannot downgrade installed users, so fix forward: tag a new patch version, which gets a higher version code, and roll it out. Internal-track builds can simply be superseded.
- App Store: before release, remove the build from review or from the TestFlight groups. After release, pause the phased release if it is still running, then ship a fixed build through an expedited review; an approved version cannot be replaced by an older one.

The pipeline has not run yet: it needs the keys and secrets above, and no signed build or macOS job can be run from a Linux development container. `services/api/tests/test_mobile_release.py` checks that the deployed realm's `anum-flutter` client registers exactly `com.anum.app:/oauth2redirect` (no wildcards) as its redirect and post-logout redirect, that the app's defaults, the Android `appAuthRedirectScheme` and applicationId, and the iOS URL scheme and bundle identifier agree with it, and that the workflow uploads `com.anum.app` and keeps the desktop jobs gated.

### Installable APK

For the owner's phone and testers who are not on a Play track. Every "Release clients" run builds it with the production addresses, so it signs in through `https://auth.anum360os.com/realms/anum` and talks to `https://api.anum360os.com/`.

0. GitHub, Settings, Secrets and variables, Actions, Variables: if `ANUM_PRODUCTION_API_URL`, `ANUM_PRODUCTION_OIDC_ISSUER` or `ANUM_PRODUCTION_WEB_APP_URL` still hold the old DuckDNS addresses, set them to `https://api.anum360os.com/`, `https://auth.anum360os.com/realms/anum` and `https://app.anum360os.com/` (or delete them: the mobile jobs then use these defaults). A set variable always wins over the default. The API's `ANUM_KEYCLOAK_ISSUER` must be `https://auth.anum360os.com/realms/anum` too (`/readyz` and a web sign-in prove it).
1. GitHub, Actions, Release clients, Run workflow: branch `main`, leave "upload" and "desktop" unticked. The Android installable APK job needs no secret; without the signing secrets the APK is signed with the debug key (a notice says so).
2. When the run is green, open it, download the artifact `anum-android-apk` and unzip it: `anum-<version>-<build>.apk` and its `.sha256`. The run summary lists the API and sign-in addresses the APK was built with.
3. On the phone, uninstall any earlier ANUM build first (Settings, Apps, ANUM, Uninstall), in particular a CI `anum-mobile-debug` build or one built from DuckDNS addresses: Android refuses to update an app signed with a different key, and the old build keeps its old addresses.
4. Copy the APK to the phone (USB, Google Drive or e-mail to yourself), open it and allow "Install unknown apps" for the app that opens it when asked; or `adb install anum-<version>-<build>.apk`.
5. Open ANUM and tap Sign in: the system browser opens the Keycloak page at `auth.anum360os.com`. After signing in it returns to the app (`com.anum.app:/oauth2redirect`).

An APK signed with the debug key cannot later be replaced by the Play build (different signature): uninstall it before installing from Play. With the signing secrets set, the APK is signed with the upload key, which still differs from Play's app signing key, so the same applies.

If sign-in fails in the browser with "Invalid parameter: redirect_uri", check the Keycloak client below; if the app says the build is misconfigured, a repository variable holds a non-https or development address.

### Keycloak Client for the Apps

The apps use the public client `anum-flutter` in realm `anum` (Android and iOS, authorization code + PKCE). Its redirect is a private-use URI scheme, not a web address, so it does not change when the domain changes:

| Setting (Keycloak admin console, realm `anum`, Clients, `anum-flutter`) | Value |
| --- | --- |
| Client authentication | Off (public client) |
| Standard flow | On; Direct access grants off |
| Valid redirect URIs | `com.anum.app:/oauth2redirect` (exactly; no wildcard) |
| Valid post logout redirect URIs | `com.anum.app:/oauth2redirect` |
| Web origins | empty (the app makes no browser requests to Keycloak) |
| Advanced, Proof Key for Code Exchange Code Challenge Method | `S256` |

The realm import (`infra/keycloak/anum-realm.json`, `services/api/tests/test_mobile_release.py`) already contains exactly these values, so a moved Keycloak host needs no change here. Check them once in the console (the realm was imported once, so an older import may differ): if `anum-flutter` is missing or the redirect differs, set the values above and Save. What does change with the host is the token issuer: the API's `ANUM_KEYCLOAK_ISSUER` and the app's issuer must both be `https://auth.anum360os.com/realms/anum`, so builds made for the DuckDNS hosts stop working and must be replaced by a new build ([Changing an address](deploy-oracle.md#changing-an-address-later)).

### Crash Symbols

Store builds compile obfuscated Dart (`--obfuscate --split-debug-info=build/symbols`, owner decision of 12 October 2026, [Full skills audit](audits/2026-10-12-full-skills-audit.md)): class and function names in the binary are replaced, which makes the app harder to reverse engineer, and a Dart stack trace from a release build shows obfuscated names and addresses. CI's release app bundle uses the same flags, so a pull request proves an obfuscated build compiles.

The release workflow uploads each build's symbol files as an artifact named `anum-android-symbols-<build number>` or `anum-ios-symbols-<build number>` (for example `app.android-arm64.symbols`, `app.ios-arm64.symbols`), kept for 90 days. Keep the symbols of every build that reaches testers or a store longer than that: download the artifact from the run and store it with the release evidence. To read a crash:

1. Find the build number of the crashing app (Play Console or App Store Connect shows it with the crash; it is also the version code / `CFBundleVersion`).
2. Download that build's symbols artifact from the "Release clients" run and unzip it, for example to `symbols/`.
3. Save the stack trace to `trace.txt` and run, from `apps/mobile`: `flutter symbolize -i trace.txt -d symbols/app.android-arm64.symbols` (use the file for the device's architecture; `app.ios-arm64.symbols` for iPhones).

Native (Java, Kotlin, Swift) frames are not affected by Dart obfuscation; Play Console and Xcode Organizer symbolicate those as before.

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
| Firebase app config (push) | Firebase console, Project settings, Your apps, download `google-services.json` and `GoogleService-Info.plist` | Secrets `ANUM_ANDROID_GOOGLE_SERVICES_JSON` and `ANUM_IOS_GOOGLE_SERVICE_INFO_PLIST` (file contents) |
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

- GitHub: the run's summary lists each build's SHA-256, and its Artifacts hold `anum-android-release`, `anum-android-apk` ([Installable APK](#installable-apk)) and `anum-ios-release`.
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

### Storage, Network and Links

From the October 2026 skills audit ([report](audits/2026-10-12-full-skills-audit.md)):

- The session (with the OIDC refresh token) and the remembered workspace choice are iOS keychain items of class `AfterFirstUnlockThisDeviceOnly` (`deviceOnlyKeychain` in `lib/src/infrastructure/mobile_adapters.dart`): readable for background refresh and push after the first unlock, and never restored to another device from an encrypted backup. Items written by earlier builds with the plugin default (`WhenUnlocked`) move to the new class on first read; sign-out deletes either class. Android is unchanged (keystore-bound, backups off). `test/secure_storage_test.dart` covers it with a keychain stand-in whose queries match only the item's class.
- JSON calls (`HttpApiTransport`) give up after five minutes, longer than a model call with its server retries; a hung or unreachable server becomes the app's "could not be reached" error (`ApiException` with status 0) instead of an endless spinner, and a proxy's HTML 502/503 keeps its status code. Streams keep their own timeouts (`HttpEventStream`). `test/http_api_transport_test.dart`.
- Links from the server or from social platforms (a marketing post's public link, a connect page) open in the browser only when they are absolute `https` URLs (`mkHttpsLink`); anything else (`intent:`, `file:`, `tel:`, plain `http:`) is not launched and the screen says the link could not be opened.

### Screen Privacy

Owner decision of 12 October 2026: screenshots of the app are blocked where the platform allows it.

- Android: `MainActivity` sets `FLAG_SECURE` on its window before Flutter starts. Screenshots and screen recordings come out black, the recent-apps thumbnail is blank, and the app does not show on insecure external displays or casts. Sign-in runs in a Custom Tab (the browser's own activity), which this does not cover.
- iOS: iOS has no supported way to block a screenshot. `PrivacyCover` (`ios/Runner/AppDelegate.swift`) puts a plain cover in the app's background colour over every window while the scene is inactive (so the app-switcher snapshot shows nothing) and while the screen is captured (recording, AirPlay mirroring or screen sharing: `sceneCaptureState` on iOS 17 and later, `UIScreen.isCaptured` before). A screenshot taken while the app is active still shows the screen. The "secure text field layer" trick that blanks iOS screenshots was not used: it relies on the private internal layout of `UITextField`, which has changed between iOS versions, and wrapping Flutter's view in it can break touch and accessibility, so it is too fragile to ship.
- Web and desktop: a browser page or a webview cannot block screenshots or screen recording; nothing is attempted there ([Security](security.md)).

Device checks are in the [checklist](#real-device-test-checklist).

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
- [ ] On iOS, install the new build over one that stored a session before the device-only keychain class: the session survives the first launch, and a restore of an encrypted backup onto another device asks for sign-in.
- [ ] Android: a screenshot and a screen recording of any screen come out black, and the recent-apps card is blank.
- [ ] iOS: the app-switcher card shows the plain cover, not the workspace; starting a screen recording or AirPlay mirroring covers the app until it stops; returning to the app removes the cover at once.
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
