// The Email switch in Settings › Notifications (docs/notifications.md#email):
// what it can do for the caller (a mail server, an address) and the test email.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/settings/settings_controller.dart';
import 'package:anum_mobile/features/settings/settings_screen.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _Transport implements ApiTransport {
  _Transport(
      {this.configured = true,
      this.addressKnown = true,
      this.testStatus = 'sent',
      this.channelsFail = false});
  final bool configured, addressKnown, channelsFail;
  final String testStatus;
  final requests = <ApiRequest>[];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    if (path.endsWith('/notification-channels/email-test')) {
      return ApiResponse(statusCode: 200, body: {
        'status': testStatus,
        'to': 'me@example.com',
        'attempts': testStatus == 'failed' ? 3 : 1,
        'error': testStatus == 'failed'
            ? 'The mail server could not be reached'
            : null,
      });
    }
    if (path.endsWith('/notification-channels')) {
      if (channelsFail) {
        return const ApiResponse(statusCode: 500, body: {'detail': 'down'});
      }
      return ApiResponse(statusCode: 200, body: {
        'email': {'configured': configured, 'address_known': addressKnown},
        'push_configured': true,
      });
    }
    if (path.endsWith('/notification-preferences')) {
      return const ApiResponse(statusCode: 200, body: {
        'task_completed': true,
        'approval_required': true,
        'run_failed': true,
        'automation_failed': true,
        'email_enabled': true,
        'desktop_enabled': true,
      });
    }
    return const ApiResponse(statusCode: 204);
  }
}

Future<SettingsController> _controller(_Transport transport) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession.fromJson({
    'access_token': 'anum_local_test',
    'token_type': 'bearer',
    'expires_at':
        DateTime.now().toUtc().add(const Duration(hours: 1)).toIso8601String(),
    'context': {
      'tenant_id': 'tenant_test',
      'workspace_id': 'workspace_test',
      'user_id': 'user_test',
      'roles': ['owner'],
    },
  }));
  final api = AnumApiClient(
      baseUri: Uri.parse('http://127.0.0.1:8000'),
      transport: transport,
      sessions: sessions);
  return SettingsController(AuthRepository(api: api, sessions: sessions));
}

Future<void> _open(WidgetTester tester, SettingsController controller) async {
  tester.view.physicalSize = const Size(1000, 2400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
      theme: AnumTheme.dark(), home: SettingsScreen(controller: controller)));
  await tester.pumpAndSettle();
}

void main() {
  test('the hint says why Email delivers nothing, or what it will do', () {
    String hint(bool configured, bool known, bool on) => NotificationChannels(
            emailConfigured: configured, emailAddressKnown: known)
        .emailHint(switchedOn: on);
    expect(hint(false, true, true), contains('no mail server'));
    expect(hint(true, false, true), contains('no email address for you'));
    expect(hint(true, true, true), startsWith('On.'));
    expect(hint(true, true, false), startsWith('Ready.'));
  });

  test('test email results cover sent, failed, not configured, no address', () {
    EmailTestResult result(String status) => EmailTestResult.fromJson({
          'status': status,
          'to': 'me@example.com',
          'attempts': 3,
          'error': 'down'
        });
    expect(result('sent').sent, isTrue);
    expect(result('sent').notice, contains('me@example.com'));
    expect(result('failed').notice, contains('after 3 tries: down'));
    expect(result('not_configured').notice, contains('no mail server'));
    expect(result('no_address').notice, contains('no email address'));
  });

  testWidgets('Email shows its state and the test email proves it',
      (tester) async {
    final transport = _Transport();
    final controller = await _controller(transport);
    await _open(tester, controller);
    final hint = find.byKey(const Key('email-hint'));
    await tester.ensureVisible(hint);
    expect(tester.widget<Text>(hint).data, startsWith('On.'));
    await tester.tap(find.byKey(const Key('email-test')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('email-test-result')), findsOneWidget);
    expect(
        transport.requests.any((r) =>
            r.method == 'POST' &&
            r.uri.path.endsWith('/notification-channels/email-test')),
        isTrue);
  });

  testWidgets('a failed test email says why', (tester) async {
    final controller = await _controller(_Transport(testStatus: 'failed'));
    await _open(tester, controller);
    final button = find.byKey(const Key('email-test'));
    await tester.ensureVisible(button);
    await tester.tap(button);
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('email-test-failed')), findsOneWidget);
    expect(find.textContaining('after 3 tries'), findsOneWidget);
  });

  testWidgets('no mail server: a hint and no test button', (tester) async {
    final controller = await _controller(_Transport(configured: false));
    await _open(tester, controller);
    final hint = find.byKey(const Key('email-hint'));
    await tester.ensureVisible(hint);
    expect(tester.widget<Text>(hint).data, contains('no mail server'));
    expect(find.byKey(const Key('email-test')), findsNothing);
  });

  testWidgets('no address: a hint and no test button', (tester) async {
    final controller = await _controller(_Transport(addressKnown: false));
    await _open(tester, controller);
    final hint = find.byKey(const Key('email-hint'));
    await tester.ensureVisible(hint);
    expect(tester.widget<Text>(hint).data, contains('no email address'));
    expect(find.byKey(const Key('email-test')), findsNothing);
  });

  testWidgets('when the channels cannot be read Settings still works',
      (tester) async {
    final controller = await _controller(_Transport(channelsFail: true));
    await _open(tester, controller);
    expect(find.byKey(const Key('email-hint')), findsNothing);
    expect(find.text('Email delivery'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
