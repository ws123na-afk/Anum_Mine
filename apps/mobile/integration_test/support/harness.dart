import 'package:anum_mobile/features/push/push_messaging.dart';
import 'package:anum_mobile/src/anum_app.dart';
import 'package:anum_mobile/src/app_environment.dart';
import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'fake_api.dart';
import 'fixtures.dart';

/// A phone screen (390 x 844 logical pixels), the size the journeys run at.
const phone = Size(390, 844);

/// Starts the whole app against [api] on a phone-sized screen, signed out,
/// with the onboarding pages already seen. Device stores start empty.
Future<void> launchApp(WidgetTester tester, FakeAnumApi api,
    {Map<String, Object> preferences = const {}}) async {
  SharedPreferences.setMockInitialValues(
      {'anum.onboarding.seen': true, ...preferences});
  FlutterSecureStorage.setMockInitialValues({});
  tester.view
    ..physicalSize = phone * 3
    ..devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  addTearDown(api.close);
  await tester.pumpWidget(AnumApp(
    environment: AnumEnvironment(
      apiUrl: 'http://localhost:8000/',
      httpClient: () => api.client,
      pushMessaging: DisabledPushMessaging(),
    ),
  ));
  await pumpFor(tester);
}

/// Pumps frames for [duration] so requests answer and animations move on;
/// unlike `pumpAndSettle` it ends even while a spinner or the orb animates.
Future<void> pumpFor(WidgetTester tester,
    [Duration duration = const Duration(milliseconds: 600)]) async {
  final end = DateTime.now().add(duration);
  do {
    await _tick(tester);
  } while (DateTime.now().isBefore(end));
}

/// One frame, after giving real I/O (file reads for uploads) a moment: the
/// test binding's clock is fake, so real asynchronous work only moves on
/// inside `runAsync`.
Future<void> _tick(WidgetTester tester) async {
  await tester
      .runAsync(() => Future<void>.delayed(const Duration(milliseconds: 10)));
  await tester.pump(const Duration(milliseconds: 50));
}

/// Pumps until [finder] finds something, failing with what is on screen and
/// which requests had no fixture after [timeout].
Future<void> waitFor(WidgetTester tester, Finder finder,
    {FakeAnumApi? api, Duration timeout = const Duration(seconds: 10)}) async {
  final end = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(end)) {
    await _tick(tester);
    if (finder.evaluate().isNotEmpty) return;
  }
  final texts = <String>{
    for (final e in find.byType(Text).evaluate())
      if ((e.widget as Text).data case final String t) t
  }.take(60);
  fail('Timed out waiting for $finder.\n'
      'On screen: ${texts.join(' | ')}\n'
      '${api == null ? '' : 'Unmatched requests: ${api.unmatched.toSet()}'}');
}

/// Pumps until [condition] holds, failing after [timeout] with [what].
Future<void> waitUntil(
    WidgetTester tester, String what, bool Function() condition,
    {FakeAnumApi? api, Duration timeout = const Duration(seconds: 10)}) async {
  final end = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(end)) {
    await _tick(tester);
    if (condition()) return;
  }
  fail('Timed out waiting for $what.'
      '${api == null ? '' : ' Unmatched requests: ${api.unmatched.toSet()}'}');
}

/// Taps [finder] after scrolling it into view, then lets the app react.
Future<void> tapOn(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder.first);
  await tester.pump();
  await tester.tap(finder.first);
  await pumpFor(tester, const Duration(milliseconds: 400));
}

/// Opens the bottom navigation's Menu and then the entry named [entry].
Future<void> openFromMenu(WidgetTester tester, FakeAnumApi api, String entry,
    {String menu = 'Menu'}) async {
  await waitFor(tester, find.text(menu), api: api);
  await tapOn(tester, find.text(menu).last);
  // The menu is a lazy list: scroll it until the entry is built.
  await waitFor(tester, find.text('Settings'), api: api);
  if (find.text(entry).evaluate().isEmpty) {
    await tester.scrollUntilVisible(find.text(entry), 200,
        scrollable: find
            .ancestor(
                of: find.text('Settings').last,
                matching: find.byType(Scrollable))
            .first);
  }
  await tapOn(tester, find.text(entry).last);
}

/// Signs in with the local development sign-in (the build has no OIDC
/// issuer) and waits for the signed-in workspace.
Future<void> signIn(WidgetTester tester, FakeAnumApi api) async {
  await waitFor(tester, find.text('Sign in to ANUM'), api: api);
  await tester.enterText(
      find.widgetWithText(TextFormField, 'Organization ID'), Fx.tenantId);
  await tester.enterText(
      find.widgetWithText(TextFormField, 'Workspace ID'), Fx.workspaceId);
  await tester.enterText(
      find.widgetWithText(TextFormField, 'User ID'), Fx.userId);
  await tapOn(tester, find.text('Continue'));
  await pumpFor(tester, const Duration(seconds: 1));
}
