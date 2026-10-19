# Mobile journey tests

The Flutter app (`apps/mobile`) has widget tests in `test/` and end to end
journey tests in `integration_test/`. The journey tests start the whole app
(`AnumApp`) on a phone-sized screen (390 x 844) and drive it the way a person
does: sign in, tap through Home and the Menu, type, approve.

## How they run

- `AnumApp` takes an `AnumEnvironment` (`lib/src/app_environment.dart`). A real
  build passes nothing. The tests pass a fake `package:http` client, so the
  app's own HTTP transport, server-sent event reader and streamed uploads all
  run unchanged.
- `integration_test/support/fake_api.dart` is that client: routes by method
  and path, a log of every request (with its JSON or raw body), the API's error
  envelope for a request with no fixture (recorded in `unmatched`), JSON,
  empty, open-stream and SSE answers.
- `integration_test/support/fixtures.dart` holds answers in the API's real
  shapes (`services/api` schemas), modelled on the web e2e fixtures in
  `apps/web/e2e/support`. Approvals check the payload hash as the API does.
- Device stores start empty: `SharedPreferences` and `FlutterSecureStorage`
  mocks, push off (`DisabledPushMessaging`), a fake system file picker for
  uploads.
- `integration_test/journeys_test.dart` is the single entry file: the
  flutter-tester device starts the app once per file, so every journey in
  `integration_test/journeys/` is registered there.

Run them headless, as CI does (job "Flutter mobile", step "Run Flutter journey
tests"):

```
cd apps/mobile
flutter pub get
flutter test integration_test -d flutter-tester
```

## Journeys covered

| Journey | File |
| --- | --- |
| Sign-in, Home, open the waiting task, approve with the payload hash; wrong password and refused one-time code say why | `journeys/task_approval.dart` |
| Assistant chat: ask, streamed answer | `journeys/chat.dart` |
| Email: list, open, AI draft, ask approval to send; drafting that itself waits for approval | `journeys/email.dart` |
| Business: lead list, lead detail, approve the drafted proposal in Approvals | `journeys/business.dart` |
| Phone calls: history, call detail | `journeys/calls.dart` |
| Settings: Arabic turns the app right to left, stays after a restart, reachable when the profile cannot load | `journeys/settings_language.dart` |
| Files: upload, then the file is in the list | `journeys/files.dart` |

## Broken journeys these tests found and fixed

- Task detail said "Decide in Approvals" with no way to decide: it now shows
  the same approval card as Approvals (arguments, payload hash, Approve with a
  confirm, Reject).
- The approval card overflowed on a 360 px phone with large text: its header
  and buttons now wrap.
- File uploads never finished: the streamed body was never closed (an
  `asFuture` replaced the `onDone` that closed it), so Files, meeting imports,
  mail attachments and Command uploads hung at 100%. Unit test:
  `test/file_upload_transfer_test.dart`.
- Email drafting that waits for an owner's approval (workspace policy) showed
  only an error. It now shows the approval with its payload hash and
  "Approve drafting"; the draft appears once approved.
- Profile and Settings showed only an error when one call failed, so Language
  and Theme were unreachable. They now stay available below the error, the
  error is the API's sentence (not `ApiException(...)`), and status 0 counts
  as offline.
- "Use one-time code" failed silently when the API refused: it now says why.

## Not covered yet

- Lead outreach drafts ("Write a draft", "Approve text") exist on the web but
  not in the Flutter Business screen, so the mobile journey approves the
  drafted proposal's acceptance instead.
- Keycloak (OIDC) sign-in needs the system browser and is not driven here; the
  journeys use the local development sign-in.
