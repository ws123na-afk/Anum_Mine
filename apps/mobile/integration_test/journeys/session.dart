import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../support/fake_api.dart';
import '../support/fixtures.dart';
import '../support/harness.dart';
import 'settings_language.dart' show installSettings;

/// Opens Settings, Security and confirms "Sign out".
Future<void> signOutFromSettings(WidgetTester tester, FakeAnumApi api) async {
  await openFromMenu(tester, api, 'Settings');
  await waitFor(tester, find.text('Security'), api: api);
  await tapOn(tester, find.text('Security'));
  await waitFor(tester, find.text('Sign out'), api: api);
  await tapOn(tester, find.text('Sign out'));
  await waitFor(tester, find.text('Sign out?'), api: api);
  await tapOn(tester, find.widgetWithText(FilledButton, 'Sign out'));
  // Let the screens above Home finish closing.
  await pumpFor(tester, const Duration(seconds: 1));
}

/// Signing out and a session that ends on its own (docs/mobile.md#session-end).
void sessionJourneys() => group('Session', () {
      testWidgets('settings: Sign out leaves only the sign-in screen',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        WorkspaceState().install(api);
        installSettings(api);
        await launchApp(tester, api);
        await signIn(tester, api);
        await waitFor(tester, find.text("Today's Focus"), api: api);

        await signOutFromSettings(tester, api);

        await waitFor(tester, find.text('Sign in to ANUM'), api: api);
        expect(api.sent('DELETE', '/api/v1/auth/local/session'), hasLength(1));
        // Settings closed with the session and Home is gone, not just hidden.
        expect(find.text('Sign out'), findsNothing);
        expect(find.text("Today's Focus"), findsNothing);
        expect(find.text('Review the release'), findsNothing);
        expect(find.byKey(const Key('auth-session-ended')), findsNothing);
        expect(tester.takeException(), isNull);
      });

      testWidgets('settings: Sign out works when the server cannot be told',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        installSettings(api);
        api.delete('/api/v1/auth/local/session',
            (_) => FakeResponse.error(503, 'unavailable', 'Try again later'));
        await launchApp(tester, api);
        await signIn(tester, api);
        await waitFor(tester, find.text("Today's Focus"), api: api);

        await signOutFromSettings(tester, api);

        await waitFor(tester, find.text('Sign in to ANUM'), api: api);
        expect(find.text('Sign out'), findsNothing);
        expect(tester.takeException(), isNull);
      });

      testWidgets('the next person to sign in sees nothing of the previous one',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        installSettings(api);
        // Two people share the phone; the API answers for whoever signed in.
        var person = 'a';
        api.post('/api/v1/auth/local/session', (request) {
          final body = request.json as Map<String, Object?>;
          person = body['user_id'] == 'user_second' ? 'b' : 'a';
          return FakeResponse.json({
            ...Fx.session(),
            'access_token': 'anum_local_$person',
            'context': {
              'tenant_id': Fx.tenantId,
              'workspace_id': Fx.workspaceId,
              'user_id': person == 'a' ? Fx.userId : 'user_second',
              'roles': ['owner'],
            },
          }, status: 201);
        });
        Map<String, Object?> task() => person == 'a'
            ? Fx.task('task_a', 'Renew the trade licence', 'completed')
            : Fx.task('task_b', 'Order the new tyres', 'completed');
        api.get('/api/v1/tasks', (_) => FakeResponse.json([task()]));
        await launchApp(tester, api);
        await signIn(tester, api);
        await waitFor(tester, find.text("Today's Focus"), api: api);
        await tapOn(tester, find.text('Tasks').last);
        await waitFor(tester, find.text('Renew the trade licence'), api: api);

        await signOutFromSettings(tester, api);
        await waitFor(tester, find.text('Sign in to ANUM'), api: api);

        await tester.enterText(
            find.widgetWithText(TextFormField, 'User ID'), 'user_second');
        await tapOn(tester, find.text('Continue'));
        await waitFor(tester, find.text("Today's Focus"), api: api);
        await tapOn(tester, find.text('Tasks').last);
        await waitFor(tester, find.text('Order the new tyres'), api: api);
        expect(find.text('Renew the trade licence'), findsNothing);

        // Settings shows the second person's own profile and session.
        await openFromMenu(tester, api, 'Settings');
        await waitFor(tester, find.text('user_second'), api: api);
        expect(find.text(Fx.userId), findsNothing);
        expect(tester.takeException(), isNull);
      });

      testWidgets('a session that expires mid-use returns to sign-in',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        WorkspaceState().install(api);
        installSettings(api);
        // The server's session lasts three seconds.
        api.post(
            '/api/v1/auth/local/session',
            (_) => FakeResponse.json({
                  ...Fx.session(),
                  'expires_at': DateTime.now()
                      .toUtc()
                      .add(const Duration(seconds: 3))
                      .toIso8601String(),
                }, status: 201));
        await launchApp(tester, api);
        await signIn(tester, api);
        await waitFor(tester, find.text("Today's Focus"), api: api);
        await pumpFor(tester, const Duration(seconds: 3));

        // The next thing the person opens needs the session.
        await openFromMenu(tester, api, 'Settings');

        await waitFor(tester, find.text('Your session ended. Sign in again.'),
            api: api);
        expect(find.text('Sign in to ANUM'), findsOneWidget);
        expect(find.byKey(const Key('auth-session-ended')), findsOneWidget);
        expect(find.text('Review the release'), findsNothing);
        expect(find.textContaining('Authentication required'), findsNothing);
        expect(tester.takeException(), isNull);
      });
    });
