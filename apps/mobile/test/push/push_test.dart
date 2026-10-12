import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/push/push_controller.dart';
import 'package:anum_mobile/features/push/push_messaging.dart';
import 'package:anum_mobile/features/push/push_models.dart';
import 'package:anum_mobile/features/push/push_repository.dart';
import 'package:anum_mobile/features/push/push_settings_card.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'push_fakes.dart';

/// Phone push on the device (docs/notifications.md): never asks on its own,
/// registers per workspace, unregisters before sign-out and switches, and
/// turns pushes into destinations.
Future<(PushController, FakePushApi, FakePushMessaging, MemoryPushLocalStore)>
    _controller({
  bool serverOn = true,
  bool available = true,
  PushPermission permission = PushPermission.notDetermined,
}) async {
  final api = FakePushApi(serverOn: serverOn);
  final (client, _) = await signedInApi(api);
  final messaging =
      FakePushMessaging(available: available, current: permission);
  final store = MemoryPushLocalStore();
  final controller = PushController(
      repository: PushRepository(client), messaging: messaging, store: store);
  return (controller, api, messaging, store);
}

void main() {
  group('push destinations', () {
    test('ids become routes; anything else is ignored', () {
      final approval = PushDestination.fromData({
        'anum_kind': 'approval_required',
        'workspace_id': 'workspace_test',
        'approval_id': 'approval_1',
        'task_id': 'task_1',
      })!;
      expect(approval.kind, PushDestinationKind.approval);
      expect(approval.route, '/approvals');
      final task = PushDestination.fromData({
        'anum_kind': 'run_failed',
        'workspace_id': 'workspace_test',
        'task_id': 'task 1/2',
      })!;
      expect(task.route, '/tasks/task%201%2F2');
      final developer = PushDestination.fromData({
        'anum_kind': 'developer_ready',
        'workspace_id': 'workspace_test',
        'developer_request_id': 'dev_1',
      })!;
      expect(developer.route, '/developer');
      expect(developer.requestId, 'dev_1');
      final automation = PushDestination.fromData({
        'anum_kind': 'automation_failed',
        'workspace_id': 'workspace_test',
        'automation_run_id': 'run_1',
      })!;
      expect(automation.kind, PushDestinationKind.automation);
      expect(automation.route, '/automations');
      expect(PushDestination.fromData({'anum_kind': 'task_completed'}), isNull);
      expect(
          PushDestination.fromData(
              {'anum_kind': 'task_completed', 'workspace_id': 'w'}),
          isNull,
          reason: 'a task push without a task id has nowhere to go');
      expect(
          PushDestination.fromData(
              {'anum_kind': 'something_new', 'workspace_id': 'w'}),
          isNull);
    });
  });

  group('PushController', () {
    test('a build without Firebase stays off and calls nothing', () async {
      final (push, api, messaging, _) = await _controller(available: false);
      await push.refresh();
      expect(push.phase, PushPhase.unavailableInBuild);
      expect(api.requests, isEmpty);
      expect(messaging.prompts, 0);
    });

    test('push off on the server says so and never prompts', () async {
      final (push, _, messaging, _) = await _controller(serverOn: false);
      await push.refresh();
      expect(push.phase, PushPhase.offOnServer);
      expect(push.server!.message, contains('ANUM_FCM_SERVICE_ACCOUNT_JSON'));
      expect(messaging.prompts, 0);
    });

    test('refresh never asks; enable asks once and registers', () async {
      final (push, api, messaging, store) = await _controller();
      await push.refresh();
      expect(push.phase, PushPhase.notAsked);
      expect(messaging.prompts, 0);
      expect(api.calls(), isNot(contains('POST /api/v1/me/devices')));

      await push.enable();
      expect(messaging.prompts, 1);
      expect(push.phase, PushPhase.on);
      final post = api.requests.firstWhere((r) => r.method == 'POST');
      expect(post.body, {
        'platform': 'android',
        'token': 'fcm-token-example-phone',
        'label': 'Android phone',
      });
      expect(push.devices.single.title, 'Android phone, ends in hone');
      expect(push.thisDeviceId, 'device_1');
      expect(store.devices['tenant_test/user_test/workspace_test'], 'device_1');
      expect(await store.offered(), isTrue);
    });

    test('a refused prompt is reported as blocked', () async {
      final (push, api, messaging, _) = await _controller();
      messaging.answer = PushPermission.denied;
      await push.enable();
      expect(push.phase, PushPhase.blocked);
      expect(api.calls(), isNot(contains('POST /api/v1/me/devices')));
    });

    test('a phone that already allows notifications registers on sign-in',
        () async {
      final (push, api, messaging, _) =
          await _controller(permission: PushPermission.granted);
      await push.refresh();
      expect(push.phase, PushPhase.on);
      expect(messaging.prompts, 0);
      expect(api.calls(), contains('POST /api/v1/me/devices'));
    });

    test('a failed device list says what failed', () async {
      final (push, api, _, _) = await _controller();
      api.failDevices = true;
      await push.refresh();
      expect(push.phase, PushPhase.error);
      expect(push.error,
          'Could not check phone push. Service unavailable. Try again.');
    });

    test('leaving a workspace unregisters; sign-out invalidates the token',
        () async {
      final (push, api, messaging, store) =
          await _controller(permission: PushPermission.granted);
      await push.refresh();
      await push.leavingWorkspace();
      expect(api.calls(), contains('DELETE /api/v1/me/devices/device_1'));
      expect(store.devices, isEmpty);
      expect(api.devices['workspace_test'], isEmpty);
      await push.signedOut();
      expect(messaging.tokenDeletes, 1);
      expect(push.phase, PushPhase.signedOut);
    });

    test('the offer after an approval comes once, only when it can work',
        () async {
      final (push, _, messaging, _) = await _controller();
      await push.start();
      expect(await push.shouldOfferAfterApproval(), isTrue);
      expect(await push.shouldOfferAfterApproval(), isFalse, reason: 'once');
      final (other, _, otherMessaging, _) = await _controller(serverOn: false);
      await other.start();
      expect(await other.shouldOfferAfterApproval(), isFalse);
      expect(messaging.prompts + otherMessaging.prompts, 0);
    });

    test('pushes become notices and destinations', () async {
      final (push, _, messaging, _) = await _controller();
      messaging.launch = const IncomingPush(data: {
        'anum_kind': 'approval_required',
        'workspace_id': 'workspace_test',
        'approval_id': 'approval_launch',
      });
      await push.start();
      // Launched from a tap before anyone could open it: kept for later.
      expect(push.takePendingDestination()?.approvalId, 'approval_launch');
      expect(push.takePendingDestination(), isNull);

      final opened = <PushDestination>[];
      final notices = <PushNotice>[];
      push
        ..onOpen = opened.add
        ..onNotice = notices.add;
      messaging.opened.add(const IncomingPush(data: {
        'anum_kind': 'task_completed',
        'workspace_id': 'workspace_test',
        'task_id': 'task_1',
      }));
      messaging.foreground.add(const IncomingPush(
          title: 'Approval needed',
          body: 'An action is waiting for your decision.',
          data: {
            'anum_kind': 'approval_required',
            'workspace_id': 'workspace_test',
            'approval_id': 'approval_2',
          }));
      await Future<void>.delayed(Duration.zero);
      expect(opened.single.route, '/tasks/task_1');
      expect(notices.single.title, 'Approval needed');
      expect(notices.single.destination!.approvalId, 'approval_2');
    });

    test('removing this phone turns it back to "not asked"', () async {
      final (push, api, _, store) =
          await _controller(permission: PushPermission.granted);
      await push.refresh();
      await push.removeDevice(push.devices.single);
      expect(push.devices, isEmpty);
      expect(push.thisDeviceId, isNull);
      expect(push.phase, PushPhase.notAsked);
      expect(store.devices, isEmpty);
      expect(push.notice, startsWith('Removed Android phone'));
      expect(api.devices['workspace_test'], isEmpty);
    });
  });

  group('AuthRepository tells push about workspace changes', () {
    test('sign-out unregisters first, while the session still works', () async {
      final api = FakePushApi();
      final (client, sessions) = await signedInApi(api);
      final push = PushController(
          repository: PushRepository(client),
          messaging: FakePushMessaging(current: PushPermission.granted),
          store: MemoryPushLocalStore());
      await push.refresh();
      final auth = AuthRepository(
          api: client, sessions: sessions, sessionListener: push);
      await auth.signOut();
      expect(api.calls().sublist(api.calls().length - 2), [
        'DELETE /api/v1/me/devices/device_1',
        'DELETE /api/v1/auth/local/session',
      ]);
      expect(await sessions.read(), isNull);
    });

    test('a workspace switch moves the registration', () async {
      final api = FakePushApi();
      final (client, sessions) = await signedInApi(api);
      final push = PushController(
          repository: PushRepository(client),
          messaging: FakePushMessaging(current: PushPermission.granted),
          store: MemoryPushLocalStore());
      await push.refresh();
      final auth = AuthRepository(
          api: client, sessions: sessions, sessionListener: push);
      await auth.switchWorkspace('workspace_two');
      expect(api.devices['workspace_test'], isEmpty);
      expect(api.devices['workspace_two'], hasLength(1));
      expect(push.phase, PushPhase.on);
    });
  });

  group('PhonePushCard', () {
    Future<void> pump(WidgetTester tester, PushController controller,
        {Future<void> Function()? openSettings}) async {
      await tester.pumpWidget(MaterialApp(
          theme: AnumTheme.dark(),
          home: Scaffold(
              body: SingleChildScrollView(
                  child: PhonePushCard(
                      controller: controller,
                      openPhoneSettings: openSettings)))));
      await tester.pumpAndSettle();
    }

    testWidgets('explains a build without Firebase', (tester) async {
      final (push, _, _, _) = await _controller(available: false);
      await pump(tester, push);
      expect(find.text('Phone push is not set up in this app'), findsOneWidget);
      expect(find.textContaining('google-services.json'), findsOneWidget);
      expect(find.byType(FilledButton), findsNothing);
    });

    testWidgets('turns notifications on and lists this phone', (tester) async {
      final (push, _, messaging, _) = await _controller();
      await pump(tester, push);
      expect(find.text('Get notified on this phone'), findsOneWidget);
      expect(messaging.prompts, 0, reason: 'opening Settings never prompts');
      await tester.tap(find.text('Turn on notifications'));
      await tester.pumpAndSettle();
      expect(messaging.prompts, 1);
      expect(find.text('This phone gets notifications'), findsOneWidget);
      expect(find.text('Android phone, ends in hone (this phone)'),
          findsOneWidget);
      expect(
          find.text('Notifications are on for this phone in this workspace.'),
          findsOneWidget);

      await tester.tap(find.byTooltip('Remove Android phone, ends in hone'));
      await tester.pumpAndSettle();
      expect(find.text('Get notified on this phone'), findsOneWidget);
      expect(find.text('Removed Android phone, ends in hone.'), findsOneWidget);
    });

    testWidgets('blocked notifications lead to the phone settings',
        (tester) async {
      final (push, _, _, _) =
          await _controller(permission: PushPermission.denied);
      var opened = 0;
      await pump(tester, push, openSettings: () async => opened++);
      expect(find.text('Notifications are blocked'), findsOneWidget);
      await tester.tap(find.text('Open phone settings'));
      expect(opened, 1);
    });

    for (final (size, scale) in [
      (const Size(360, 640), 2.0),
      (const Size(390, 844), 1.0),
      (const Size(1024, 768), 1.3),
    ]) {
      testWidgets(
          'fits ${size.width.toInt()}x${size.height.toInt()} at ${scale}x text',
          (tester) async {
        tester.view.physicalSize = size;
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.reset);
        final (push, _, _, _) =
            await _controller(permission: PushPermission.granted);
        await tester.pumpWidget(MaterialApp(
            theme: AnumTheme.dark(),
            home: MediaQuery(
                data: MediaQueryData(
                    size: size, textScaler: TextScaler.linear(scale)),
                child: Scaffold(
                    body: SingleChildScrollView(
                        child: PhonePushCard(controller: push))))));
        await tester.pumpAndSettle();
        expect(find.text('This phone gets notifications'), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('push off on the server says who can turn it on',
        (tester) async {
      final (push, _, _, _) = await _controller(serverOn: false);
      await pump(tester, push);
      expect(find.text('Phone push is off on the server'), findsOneWidget);
      expect(find.textContaining('Ask the person who runs this ANUM server'),
          findsOneWidget);
      expect(find.text('Check again'), findsOneWidget);
    });
  });
}
