import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../support/fake_api.dart';
import '../support/fixtures.dart';
import '../support/harness.dart';

/// What Profile and Settings reads (docs/settings): notification
/// preferences, the chat memory switch and the workspace model.
void installSettings(FakeAnumApi api) {
  api.get(
      '/api/v1/notification-preferences',
      (_) => const FakeResponse.json({
            'task_completed': true,
            'approval_required': true,
            'run_failed': true,
            'automation_failed': true,
            'email_enabled': false,
            'desktop_enabled': false,
            'push_enabled': true,
            'commerce_alerts': true,
          }));
  api.get(
      '/api/v1/me/preferences',
      (_) => const FakeResponse.json({
            'remember_conversations': true,
            'conversation_memory': 'on',
          }));
  api.get(
      '/api/v1/model-config',
      (_) => FakeResponse.json({
            'provider': 'anthropic',
            'model': 'claude-sonnet',
            'base_url': '',
            'credential_configured': true,
            'credential_hint': '...abcd',
            'updated_at': Fx.iso(const Duration(days: -2)),
          }));
}

void settingsLanguageJourneys() => group('Settings language', () {
      testWidgets(
          'settings: switch the language to Arabic and the app turns RTL',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        installSettings(api);
        await launchApp(tester, api);
        await signIn(tester, api);

        await openFromMenu(tester, api, 'Settings');
        await waitFor(tester, find.text('user_owner'), api: api);
        await waitFor(tester, find.text('Language'), api: api);
        expect(Directionality.of(tester.element(find.text('Language'))),
            TextDirection.ltr);
        await tapOn(tester, find.text('Language'));
        await waitFor(tester, find.text('Arabic'), api: api);
        await tapOn(tester, find.text('Arabic'));

        // The whole app re-renders in Arabic, right to left.
        await waitFor(tester, find.text('اللغة'), api: api);
        expect(Directionality.of(tester.element(find.text('اللغة'))),
            TextDirection.rtl);
        expect(find.text('Language'), findsNothing);
        final prefs = await SharedPreferences.getInstance();
        expect(prefs.getString('anum.locale'), 'ar');

        // Back on Home the navigation is Arabic too and nothing overflowed.
        await tapOn(tester, find.byType(BackButton));
        await pumpFor(tester);
        await waitFor(tester, find.text('القائمة'), api: api);
        expect(Directionality.of(tester.element(find.text('القائمة'))),
            TextDirection.rtl);
        expect(tester.takeException(), isNull);
      });

      testWidgets(
          'settings: language stays reachable when the profile cannot load',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        api.get(
            '/api/v1/notification-preferences',
            (_) =>
                FakeResponse.error(503, 'unavailable', 'Database unavailable'));
        await launchApp(tester, api);
        await signIn(tester, api);

        await openFromMenu(tester, api, 'Settings');
        await waitFor(tester, find.textContaining('Database unavailable'),
            api: api);
        expect(find.textContaining('ApiException'), findsNothing);
        await tapOn(tester, find.text('Language'));
        await waitFor(tester, find.text('Arabic'), api: api);
        await tapOn(tester, find.text('Arabic'));
        await waitFor(tester, find.text('اللغة'), api: api);
        expect(Directionality.of(tester.element(find.text('اللغة'))),
            TextDirection.rtl);
      });

      testWidgets('settings: the chosen Arabic survives a restart',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        await launchApp(tester, api, preferences: {'anum.locale': 'ar'});
        // The sign-in page itself is in English today (development sign-in);
        // the signed-in app comes up right to left.
        await signIn(tester, api);
        await waitFor(tester, find.text('القائمة'), api: api);
        expect(Directionality.of(tester.element(find.text('القائمة'))),
            TextDirection.rtl);
        expect(Fx.workspaceId, isNotEmpty);
      });
    });
