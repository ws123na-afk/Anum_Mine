// Finance, iPhone SMS set-up on the phone (docs/finance-sms.md#on-the-phone):
// the safety check and its results, forwarding on and off only after it
// passed, connecting an iPhone with its scopes and the token shown once on
// its own page (hidden until asked, hidden again when the app leaves the
// foreground, copied and cleared from the clipboard), pause, resume, a new
// token and disconnect after a confirm, the API's refusals in its own words,
// a viewer, and layouts from 320 px at 200% text in English and Arabic.
// Fixtures are plainly fake.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/finance/finance_more_data.dart';
import 'package:anum_mobile/features/finance/finance_sms_setup.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';

const fin = '/api/v1/finance';
const tokenValue = 'anum_smsdev_example_token';

class SmsSetupFake {
  bool passed = false, enabled = false, failCheck = false, viewer = false;
  int version = 3;
  final devices = <Map<String, Object?>>[];
  late final FakeApi api = FakeApi(route);

  Map<String, Object?> device(String id, String name,
          {String status = 'active'}) =>
      {
        'id': id,
        'name': name,
        'status': status,
        'token_hint': 'abcd',
        'scopes': ['finance.sms', 'finance.wallet'],
        'created_at': '2026-11-01T08:00:00Z',
        'last_ok_at': null,
        'last_error': null,
        'last_error_at': null,
        'message_count': 2,
      };

  Map<String, Object?> issued(Map<String, Object?> d) => {
        'device': d,
        'token': tokenValue,
        'ingest_path': '$fin/sms/ingest',
        'headers': {
          'Authorization': 'Bearer <token>',
          'Content-Type': 'application/json'
        },
        'json_fields': {
          'message': 'Shortcut Input, Content',
          'sender': 'Shortcut Input, Sender'
        },
      };

  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path.replaceFirst(fin, '');
    final manage = r.method != 'GET';
    if (viewer && manage) {
      return refuse(403,
          'Only the owner of this Finance changes it. A viewer reads only.');
    }
    switch ((r.method, path)) {
      case ('GET', '/sms/status'):
        return ok({
          'enabled': enabled,
          'ledger_kind': 'personal',
          'self_test_passed_at': passed ? '2026-11-10T08:00:00Z' : null,
          'ready': passed,
          'ingest_path': passed ? '$fin/sms/ingest' : null,
          'devices_active':
              devices.where((d) => d['status'] == 'active').length,
          'last_received_at': null,
          'last_ok_at': null,
          'last_error': null,
          'last_error_at': null,
          'counts': <String, int>{},
          'review_count': 0,
        });
      case ('GET', '/sms/settings'):
        return ok({'version': version, 'enabled': enabled});
      case ('POST', '/sms/self-test'):
        passed = !failCheck;
        if (passed) version++;
        return ok({
          'passed': passed,
          'checks': [
            {'name': 'parse_purchase', 'passed': true},
            {'name': 'storage_reachable', 'passed': !failCheck},
          ],
          'passed_at': passed ? '2026-11-10T08:00:00Z' : null,
        });
      case ('PUT', '/sms/settings'):
        if (r.body?['version'] != version) {
          return refuse(409,
              'These settings changed in another window. Reload and try again.');
        }
        if (r.body?['enabled'] == true && !passed) {
          return refuse(409,
              'Run the safety check first: it must pass before SMS forwarding can be turned on.');
        }
        enabled = r.body?['enabled'] == true;
        version++;
        return ok({'version': version, 'enabled': enabled});
      case ('GET', '/sms/devices'):
        return okList(devices);
      case ('POST', '/sms/devices'):
        if (!passed) {
          return refuse(409,
              'Run the safety check first: devices can be connected once it passes.');
        }
        final d =
            device('dev_${devices.length + 1}', r.body!['name']! as String);
        d['scopes'] = r.body!['scopes'];
        devices.add(d);
        return ok(issued(d), 201);
    }
    final change = RegExp(r'^/sms/devices/(\w+)/(\w+)$').firstMatch(path);
    if (change != null && r.method == 'POST') {
      final d = devices.firstWhere((d) => d['id'] == change.group(1));
      switch (change.group(2)) {
        case 'rotate':
          return ok(issued(d));
        case 'pause':
          d['status'] = 'paused';
        case 'resume':
          d['status'] = 'active';
        case 'revoke':
          d['status'] = 'revoked';
      }
      return ok(d);
    }
    return null;
  }
}

Future<SmsSetupFake> open(WidgetTester tester,
    {SmsSetupFake? fake,
    Size size = const Size(800, 2600),
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  final f = fake ?? SmsSetupFake();
  await pumpModule(
      tester, SmsSetupScreen(repository: FinanceMoreRepository(f.api.client())),
      size: size, textScale: textScale, locale: locale);
  return f;
}

void main() {
  test('the strings match in English and Arabic and have no long dashes', () {
    expect(SmsSetupStrings.catalogsMatch, isTrue);
    for (final text in SmsSetupStrings.allTexts) {
      expect(text.contains('–') || text.contains('—'), isFalse, reason: text);
    }
    expect(smsWalletUrl('https://api.example.test/api/v1/finance/sms/ingest'),
        'https://api.example.test/api/v1/finance/wallet/ingest');
  });

  testWidgets(
      'the safety check gates forwarding and devices; a failed check says so',
      (tester) async {
    final fake = SmsSetupFake()..failCheck = true;
    await open(tester, fake: fake);
    expect(find.text('Not run yet'), findsOneWidget);
    expect(find.text('The address appears once the safety check passes.'),
        findsOneWidget);
    expect(
        tester
            .widget<OutlinedButton>(find.byKey(const Key('sms-setup-toggle')))
            .onPressed,
        isNull);
    expect(
        tester
            .widget<FilledButton>(find.byKey(const Key('sms-setup-connect')))
            .onPressed,
        isNull);
    await tester.tap(find.byKey(const Key('sms-setup-check')));
    await tester.pumpAndSettle();
    expect(find.textContaining('The safety check failed.'), findsOneWidget);
    expect(find.text('Storage reachable: failed'), findsOneWidget);
    // Passing it opens forwarding and devices.
    fake.failCheck = false;
    await tester.tap(find.byKey(const Key('sms-setup-check')));
    await tester.pumpAndSettle();
    expect(find.text('The safety check passed.'), findsOneWidget);
    expect(find.text('https://api.example.test/api/v1/finance/sms/ingest'),
        findsOneWidget);
    await tester.tap(find.byKey(const Key('sms-setup-toggle')));
    await tester.pumpAndSettle();
    expect(find.text('Forwarding is on.'), findsOneWidget);
    expect(fake.enabled, isTrue);
    expect(fake.api.sent('PUT', '$fin/sms/settings').single.body,
        {'version': 4, 'enabled': true});
  });

  testWidgets(
      'connecting an iPhone shows its token once, protected, then manages the device',
      (tester) async {
    final fake = SmsSetupFake()..passed = true;
    final clipboard = <String?>[];
    tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, (call) async {
      if (call.method == 'Clipboard.setData') {
        clipboard.add((call.arguments as Map)['text'] as String?);
      }
      if (call.method == 'Clipboard.getData') {
        return {'text': clipboard.isEmpty ? null : clipboard.last};
      }
      return null;
    });
    addTearDown(() => tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, null));
    await open(tester, fake: fake);
    expect(find.text('No device connected'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('sms-setup-name')), 'Work iPhone');
    await tester.tap(find.byKey(const Key('sms-setup-scope-finance.wallet')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('sms-setup-connect')));
    await tester.pumpAndSettle();
    expect(fake.api.sent('POST', '$fin/sms/devices').single.body, {
      'name': 'Work iPhone',
      'scopes': ['finance.sms']
    });
    // The token page: hidden until asked.
    expect(find.text('Your device token'), findsOneWidget);
    expect(find.byKey(const Key('sms-token-hidden')), findsOneWidget);
    expect(find.text(tokenValue), findsNothing);
    await tester.tap(find.byKey(const Key('sms-token-show')));
    await tester.pumpAndSettle();
    expect(find.text(tokenValue), findsOneWidget);
    // Leaving the app hides it again.
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    await tester.pumpAndSettle();
    expect(find.text(tokenValue), findsNothing);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();
    // Copy, then the clipboard is cleared a minute later while it still holds the token.
    await tester.tap(find.byKey(const Key('sms-token-copy')));
    await tester.pumpAndSettle();
    expect(clipboard.last, tokenValue);
    expect(find.byKey(const Key('sms-token-copied')), findsOneWidget);
    await tester.pump(const Duration(minutes: 1, seconds: 1));
    await tester.pumpAndSettle();
    expect(clipboard.last, '');
    expect(
        find.textContaining(
            'Inside the If, add Get Contents of URL: https://api.example.test/api/v1/finance/sms/ingest'),
        findsOneWidget);
    await tester.ensureVisible(find.byKey(const Key('sms-token-done')));
    await tester.tap(find.byKey(const Key('sms-token-done')));
    await tester.pumpAndSettle();
    // Back on the set-up page: the token is nowhere.
    expect(find.text(tokenValue), findsNothing);
    expect(find.byKey(const Key('sms-device-dev_1')), findsOneWidget);
    await tester.tap(find.byKey(const Key('sms-device-pause-dev_1')));
    await tester.pumpAndSettle();
    expect(find.text('Work iPhone is paused.'), findsOneWidget);
    await tester.tap(find.byKey(const Key('sms-device-resume-dev_1')));
    await tester.pumpAndSettle();
    // A new token asks first, then shows the new one once.
    await tester.tap(find.byKey(const Key('sms-device-rotate-dev_1')));
    await tester.pumpAndSettle();
    expect(find.text('Make a new token for Work iPhone?'), findsOneWidget);
    await tester.tap(find.byKey(const Key('records-confirm')));
    await tester.pumpAndSettle();
    expect(find.text('Your device token'), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('sms-device-revoke-dev_1')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('records-confirm')));
    await tester.pumpAndSettle();
    expect(find.text('Work iPhone is disconnected.'), findsOneWidget);
    expect(find.byKey(const Key('sms-device-rotate-dev_1')), findsNothing);
  });

  testWidgets(
      'a viewer is refused in the API\'s words, and a failed load offers Try again',
      (tester) async {
    final fake = SmsSetupFake()
      ..passed = true
      ..viewer = true;
    await open(tester, fake: fake);
    await tester.tap(find.byKey(const Key('sms-setup-toggle')));
    await tester.pumpAndSettle();
    expect(
        find.text(
            'Only the owner of this Finance changes it. A viewer reads only.'),
        findsOneWidget);
    expect(fake.enabled, isFalse);

    final broken = SmsSetupFake()..api.failWith = 500;
    await open(tester, fake: broken);
    expect(find.byKey(const Key('sms-setup-failed')), findsOneWidget);
    expect(find.text('Try again'), findsOneWidget);
    broken.api.failWith = 403;
    await open(tester, fake: broken);
    expect(find.textContaining('A viewer of this workspace reads only.'),
        findsOneWidget);
  });

  testWidgets('layouts from 320 px at 200% text to 1440 px, English and Arabic',
      (tester) async {
    for (final (size, scale, locale) in [
      (const Size(320, 640), 2.0, const Locale('ar')),
      (const Size(390, 844), 1.0, const Locale('en')),
      (const Size(1440, 900), 1.0, const Locale('ar')),
    ]) {
      final fake = SmsSetupFake()..passed = true;
      fake.devices.add(fake.device('dev_1', 'Example iPhone'));
      await open(tester,
          fake: fake, size: size, textScale: scale, locale: locale);
      expect(tester.takeException(), isNull, reason: '$size $locale');
      await tester.scrollUntilVisible(
          find.byKey(const Key('sms-setup-steps')), 300,
          scrollable: find.byType(Scrollable).first);
      expect(tester.takeException(), isNull, reason: '$size $locale');
    }
  });
}
