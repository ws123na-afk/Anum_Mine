// The app lock in the whole app (docs/mobile.md#app-lock): Settings, a cold
// start, coming back, the lock screen's Sign out, a push tapped into a locked
// area and an approval decision.
import 'dart:convert';

import 'package:anum_mobile/features/app_lock/app_lock_controller.dart';
import 'package:anum_mobile/features/app_lock/device_auth.dart';
import 'package:anum_mobile/features/finance/finance_advisor_screens.dart';
import 'package:anum_mobile/features/finance/finance_screen.dart';
import 'package:anum_mobile/features/push/push_messaging.dart';
import 'package:anum_mobile/features/workspace/workspace_home.dart';
import 'package:anum_mobile/src/anum_app.dart';
import 'package:anum_mobile/src/app_environment.dart';
import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../integration_test/journeys/settings_language.dart'
    show installSettings;
import '../../integration_test/support/fake_api.dart';
import '../../integration_test/support/fixtures.dart';
import '../../integration_test/support/harness.dart';
import '../push/push_fakes.dart';
import 'app_lock_fakes.dart';

const home = "Today's Focus";
final lockScreen = find.byKey(const Key('app-lock-screen'));

String stored(AppLockSettings settings) => jsonEncode(settings.toJson());

class Phone {
  final auth = FakeDeviceAuth();
  final clock = FakeClock();
  final push = FakePushMessaging(current: PushPermission.denied);
  late FakeAnumApi api;
  late WorkspaceState workspace;
}

/// Starts the app. With [lock] the phone already has a session and that app
/// lock stored, as after the app was closed and opened again (a cold start).
Future<Phone> start(WidgetTester tester,
    {AppLockSettings? lock,
    DeviceAuthResult firstCheck = DeviceAuthResult.success}) async {
  final phone = Phone()..api = FakeAnumApi();
  phone.auth.answer = firstCheck;
  seedBaseline(phone.api);
  phone.workspace = WorkspaceState()..install(phone.api);
  installSettings(phone.api);
  SharedPreferences.setMockInitialValues({'anum.onboarding.seen': true});
  FlutterSecureStorage.setMockInitialValues({
    if (lock != null) ...{
      'anum.local-session': jsonEncode({...Fx.session(), 'source': 'local'}),
      'anum.app-lock': stored(lock),
    }
  });
  tester.view
    ..physicalSize = phone_ * 3
    ..devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  addTearDown(phone.api.close);
  await tester.pumpWidget(AnumApp(
    key: UniqueKey(),
    environment: AnumEnvironment(
      apiUrl: 'http://localhost:8000/',
      httpClient: () => phone.api.client,
      pushMessaging: phone.push,
      deviceAuth: phone.auth,
      now: phone.clock.call,
    ),
  ));
  await pumpFor(tester);
  return phone;
}

const phone_ = phone;

Future<void> away(WidgetTester tester, Phone phone, Duration time) async {
  tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
  tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
  tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
  phone.clock.advance(time);
  tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
  tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
  tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
  await pumpFor(tester);
}

Future<String?> storedLock() =>
    const FlutterSecureStorage().read(key: 'anum.app-lock');

const wholeApp = AppLockSettings(
    enabled: true,
    lockAfter: Duration(minutes: 5),
    scope: AppLockScope.wholeApp);
const sensitive =
    AppLockSettings(enabled: true, lockAfter: Duration(minutes: 5));

IncomingPush pushFor(String kind) => IncomingPush(data: {
      'anum_kind': kind,
      'workspace_id': Fx.workspaceId,
      'tenant_id': Fx.tenantId,
      'approval_id': 'approval_pending',
    });

void main() {
  testWidgets('Settings, Security: turn the app lock on and choose its scope',
      (tester) async {
    final phone = await start(tester);
    await signIn(tester, phone.api);
    await waitFor(tester, find.text(home), api: phone.api);
    await openFromMenu(tester, phone.api, 'Settings');
    await waitFor(tester, find.text('Security'), api: phone.api);
    await tapOn(tester, find.text('Security'));
    await waitFor(tester, find.byKey(const Key('app-lock-switch')),
        api: phone.api);
    expect(await storedLock(), isNull);

    await tapOn(tester, find.byKey(const Key('app-lock-switch')));
    await waitFor(tester, find.text('App lock is on.'), api: phone.api);
    expect(phone.auth.prompts, ['Confirm it is you to turn on the app lock']);
    expect(await storedLock(), stored(const AppLockSettings(enabled: true)));

    await tapOn(tester, find.byKey(const Key('app-lock-scope')));
    await waitFor(tester, find.text('The whole app'), api: phone.api);
    await tapOn(tester, find.text('The whole app').last);
    await waitFor(tester, find.text('Saved.'), api: phone.api);
    expect(
        await storedLock(),
        stored(const AppLockSettings(
            enabled: true, scope: AppLockScope.wholeApp)));
    // Just unlocked: the app stays open.
    expect(lockScreen, findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'cold start with the whole app locked: nothing shows until unlock',
      (tester) async {
    final started = await start(tester,
        lock: wholeApp, firstCheck: DeviceAuthResult.cancelled);
    await waitFor(tester, lockScreen, api: started.api);
    expect(find.text('ANUM is locked'), findsOneWidget);
    expect(find.text(home), findsNothing);
    expect(find.text('Review the release'), findsNothing);

    started.auth.answer = DeviceAuthResult.success;
    await tapOn(tester, find.byKey(const Key('app-lock-unlock')));
    await waitFor(tester, find.text(home), api: started.api);
    expect(lockScreen, findsNothing);
  });

  testWidgets('coming back after "Lock after" locks again; sooner does not',
      (tester) async {
    final phone = await start(tester, lock: wholeApp);
    await waitFor(tester, find.text(home), api: phone.api);
    expect(lockScreen, findsNothing, reason: 'the first prompt passed');

    await away(tester, phone, const Duration(minutes: 4));
    expect(lockScreen, findsNothing);
    expect(find.text(home), findsOneWidget);

    phone.auth.answer = DeviceAuthResult.cancelled;
    await away(tester, phone, const Duration(minutes: 5));
    await waitFor(tester, lockScreen, api: phone.api);
    expect(find.text(home), findsNothing);
    expect(find.text('Still locked: the check was cancelled. Try again.'),
        findsOneWidget);
  });

  testWidgets('Sign out on the lock screen leaves sign-in and no lock behind',
      (tester) async {
    final phone = await start(tester, lock: wholeApp);
    phone.auth.answer = DeviceAuthResult.failed;
    await away(tester, phone, const Duration(minutes: 10));
    await waitFor(tester, lockScreen, api: phone.api);

    await tapOn(tester, find.byKey(const Key('app-lock-sign-out')));
    await waitFor(tester, find.text('Sign out?'), api: phone.api);
    await tapOn(tester, find.byKey(const Key('app-lock-sign-out-confirm')));
    await waitFor(tester, find.text('Sign in to ANUM'), api: phone.api);
    expect(lockScreen, findsNothing);
    expect(
        phone.api.sent('DELETE', '/api/v1/auth/local/session'), hasLength(1));
    expect(await storedLock(), isNull);

    // The next person signs in without a lock they never set.
    await signIn(tester, phone.api);
    await waitFor(tester, find.text(home), api: phone.api);
    expect(lockScreen, findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Finance and Books only: Home is open, Finance waits',
      (tester) async {
    final phone = await start(tester, lock: sensitive);
    phone.auth.answer = DeviceAuthResult.cancelled;
    await waitFor(tester, find.text(home), api: phone.api);
    expect(lockScreen, findsNothing);
    expect(phone.auth.prompts, isEmpty);

    await openFromMenu(tester, phone.api, 'Finance');
    await waitFor(tester, lockScreen, api: phone.api);
    expect(find.text('This part of ANUM is locked'), findsOneWidget);
    expect(find.byType(FinanceScreen), findsNothing);

    await tapOn(tester, find.byKey(const Key('app-lock-back')));
    await pumpFor(tester, const Duration(seconds: 1));
    expect(lockScreen, findsNothing);
    expect(find.byType(FinanceScreen, skipOffstage: false), findsNothing);

    phone.auth.answer = DeviceAuthResult.success;
    await openFromMenu(tester, phone.api, 'Finance');
    await waitFor(tester, find.byType(FinanceScreen), api: phone.api);
    expect(lockScreen, findsNothing);
  });

  testWidgets('a push tapped into locked Finance waits for the unlock',
      (tester) async {
    final phone = await start(tester, lock: sensitive);
    await waitFor(tester, find.text(home), api: phone.api);

    phone.auth.answer = DeviceAuthResult.cancelled;
    phone.push.opened.add(pushFor('finance_advice'));
    await waitFor(
        tester, find.text('Still locked: the check was cancelled. Try again.'),
        api: phone.api);
    expect(phone.auth.prompts, ['Unlock ANUM']);
    // Neither Finance nor its Money advisor was built, not even hidden.
    expect(find.byType(FinanceScreen, skipOffstage: false), findsNothing);
    expect(find.byType(AdvisorScreen, skipOffstage: false), findsNothing);
    expect(find.text(home), findsOneWidget);

    phone.auth.answer = DeviceAuthResult.success;
    phone.push.opened.add(pushFor('finance_advice'));
    // The weekly money note opens the Money advisor itself.
    await waitFor(tester, find.byType(AdvisorScreen), api: phone.api);
  });

  testWidgets('with the whole app locked a tapped push opens after the unlock',
      (tester) async {
    final phone = await start(tester, lock: wholeApp);
    await waitFor(tester, find.text(home), api: phone.api);
    phone.auth.answer = DeviceAuthResult.cancelled;
    await away(tester, phone, const Duration(minutes: 6));
    await waitFor(tester, lockScreen, api: phone.api);

    phone.push.opened.add(pushFor('approval_required'));
    await pumpFor(tester, const Duration(seconds: 1));
    expect(lockScreen, findsOneWidget);
    // Approvals was not opened behind the lock screen: the push waits.
    expect(find.byType(WorkspaceHome, skipOffstage: false), findsOneWidget);

    phone.auth.answer = DeviceAuthResult.success;
    await tapOn(tester, find.byKey(const Key('app-lock-unlock')));
    await waitFor(tester, find.text('Publish the release notes'),
        api: phone.api);
    expect(lockScreen, findsNothing);
    expect(find.byType(WorkspaceHome, skipOffstage: false), findsNWidgets(2));
  });

  testWidgets(
      'an approval decision asks again once the unlock is older than the delay',
      (tester) async {
    final phone = await start(tester, lock: sensitive);
    await waitFor(tester, find.text('Review the release'), api: phone.api);
    await tapOn(tester, find.text('Review the release'));
    await waitFor(tester, find.byKey(const Key('task-approval')),
        api: phone.api);

    Future<void> approve() async {
      await tapOn(
          tester,
          find.descendant(
              of: find.byKey(const Key('task-approval')),
              matching: find.widgetWithText(FilledButton, 'Approve')));
      await waitFor(tester, find.text('Approve this action?'), api: phone.api);
      await tapOn(
          tester,
          find.descendant(
              of: find.byType(AlertDialog),
              matching: find.widgetWithText(FilledButton, 'Approve')));
      await pumpFor(tester, const Duration(seconds: 1));
    }

    // The person cannot pass the check: nothing is sent.
    phone.auth.answer = DeviceAuthResult.cancelled;
    await approve();
    expect(phone.auth.prompts, ['Confirm it is you to approve or reject']);
    expect(phone.workspace.decision, isNull);
    expect(phone.api.sent('POST', '/api/v1/approvals/approval_pending/approve'),
        isEmpty);
    expect(find.textContaining('confirm it is you'), findsOneWidget);
    expect(find.byKey(const Key('task-approval')), findsOneWidget);

    // They pass: the decision goes out with its hash.
    phone.auth.answer = DeviceAuthResult.success;
    await approve();
    await waitFor(
        tester, find.text('Release notes published to #announcements.'),
        api: phone.api);
    expect(phone.workspace.decision?.body['payload_hash'], Fx.payloadHash);
    expect(phone.auth.prompts, hasLength(2));
  });

  testWidgets('with the lock off nothing asks and nothing is covered',
      (tester) async {
    final phone = await start(tester);
    await signIn(tester, phone.api);
    await waitFor(tester, find.text(home), api: phone.api);
    await away(tester, phone, const Duration(hours: 3));
    expect(lockScreen, findsNothing);
    await openFromMenu(tester, phone.api, 'Finance');
    await waitFor(tester, find.byType(FinanceScreen), api: phone.api);
    expect(phone.auth.prompts, isEmpty);
  });
}
