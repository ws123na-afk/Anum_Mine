// Finance, iPhone SMS settings on the phone (docs/finance-sms.md#on-the-phone):
// the web's Settings form read from and saved to the same route with its
// version, card numbers checked before sending, AI help needing the consent,
// the API's 409 in its own words, a viewer, and Arabic at 320 px and 200%
// text. Fixtures are plainly fake.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/finance/finance_more_data.dart';
import 'package:anum_mobile/features/finance/finance_sms_settings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';

const fin = '/api/v1/finance';

class SettingsFake {
  int version = 5;
  bool viewer = false, conflict = false;
  Map<String, Object?> settings = {
    'enabled': true,
    'ledger_kind': 'personal',
    'auto_post': false,
    'body_retention_days': 30,
    'verified_senders': <String>[],
    'extra_senders': [
      {'sender': 'ExampleBank', 'bank': 'examplebank'}
    ],
    'account_map': [
      {'last4': '1111', 'account_id': 'acc_card', 'credit_limit': '5000.00'}
    ],
    'wallet_cards': <Object?>[],
    'default_account_id': null,
    'cash_account_id': null,
    'ai': {'enabled': false, 'daily_cap': 20, 'used': 0},
    'learning': {'enabled': true, 'confirm_after': 2},
    'auto_post_limits': <Object?>[],
  };
  late final FakeApi api = FakeApi(route);

  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path.replaceFirst(fin, '');
    switch ((r.method, path)) {
      case ('GET', '/sms/settings'):
        return ok({...settings, 'version': version});
      case ('GET', '/accounts'):
        return okList([
          {
            'id': 'acc_card',
            'name': 'Example card',
            'kind': 'credit_card',
            'currency': 'AED'
          },
          {
            'id': 'acc_bank',
            'name': 'Example bank',
            'kind': 'bank',
            'currency': 'AED'
          },
          {'id': 'acc_cash', 'name': 'Wallet cash', 'kind': 'cash', 'currency': 'AED'},
        ]);
      case ('PUT', '/sms/settings'):
        if (viewer) {
          return refuse(403,
              'Only the owner of this Finance changes it. A viewer reads only.');
        }
        if (conflict || r.body?['version'] != version) {
          return refuse(409,
              'These settings changed in another window. Reload and try again.');
        }
        version++;
        settings = {
          ...settings,
          'body_retention_days': r.body!['body_retention_days'],
          'account_map': r.body!['account_map'],
        };
        return ok({...settings, 'version': version});
    }
    return null;
  }
}

Future<SettingsFake> open(WidgetTester tester,
    {SettingsFake? fake,
    Size size = const Size(800, 4000),
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  final f = fake ?? SettingsFake();
  await pumpModule(tester,
      SmsSettingsScreen(repository: FinanceMoreRepository(f.api.client())),
      size: size, textScale: textScale, locale: locale);
  return f;
}

Future<void> save(WidgetTester tester) async {
  await tester.ensureVisible(find.byKey(const Key('sms-settings-save')));
  await tester.tap(find.byKey(const Key('sms-settings-save')));
  await tester.pumpAndSettle();
}

void main() {
  test('the strings match in English and Arabic and have no long dashes', () {
    expect(SmsSettingsStrings.catalogsMatch, isTrue);
    for (final text in SmsSettingsStrings.allTexts) {
      expect(text.contains('–') || text.contains('—'), isFalse, reason: text);
    }
  });

  testWidgets('reads the settings and saves a new card number with the version',
      (tester) async {
    final fake = await open(tester);
    expect(find.text('1111'), findsOneWidget);
    expect(find.text('5000.00'), findsOneWidget);
    expect(find.text('ExampleBank'), findsOneWidget);
    await tester.enterText(
        find.byKey(const ValueKey('sms-settings-retention-1')), '60');
    await tester.tap(find.byKey(const Key('sms-map-add')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('sms-map-last4-1')), '22');
    await save(tester);
    // A card ending must be four digits: nothing is sent.
    expect(find.text('Last four digits must be exactly 4 digits.'),
        findsOneWidget);
    expect(fake.api.sent('PUT', '$fin/sms/settings'), isEmpty);
    await tester.enterText(find.byKey(const Key('sms-map-last4-1')), '2222');
    await tester.tap(find.byKey(const Key('sms-map-account-1')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Example bank (AED)').last);
    await tester.pumpAndSettle();
    await save(tester);
    final body = fake.api.sent('PUT', '$fin/sms/settings').single.body!;
    expect(body['version'], 5);
    expect(body['body_retention_days'], 60);
    expect(body['account_map'], [
      {'last4': '1111', 'account_id': 'acc_card', 'credit_limit': '5000.00'},
      {'last4': '2222', 'account_id': 'acc_bank', 'credit_limit': null},
    ]);
    expect(body['extra_senders'], [
      {'sender': 'ExampleBank', 'bank': 'examplebank'}
    ]);
    expect(body.containsKey('ai_consent'), isFalse);
    expect(find.text('Saved.'), findsOneWidget);
    expect(find.text('2222'), findsOneWidget);
  });

  testWidgets('AI help needs the consent; a 409 is shown in the API words',
      (tester) async {
    final fake = await open(tester)..conflict = true;
    await tester.ensureVisible(find.byKey(const Key('sms-settings-ai-on')));
    await tester.tap(find.byKey(const Key('sms-settings-ai-on')));
    await tester.pumpAndSettle();
    await save(tester);
    expect(
        find.text('Tick the agreement to turn on AI help, or turn AI help off.'),
        findsOneWidget);
    expect(fake.api.sent('PUT', '$fin/sms/settings'), isEmpty);
    await tester.tap(find.byKey(const Key('sms-settings-ai-consent')));
    await tester.pumpAndSettle();
    await save(tester);
    final body = fake.api.sent('PUT', '$fin/sms/settings').single.body!;
    expect(body['ai_enabled'], isTrue);
    expect(body['ai_consent'], isTrue);
    expect(
        find.text(
            'These settings changed in another window. Reload and try again.'),
        findsOneWidget);
  });

  testWidgets('a viewer is refused in the API words', (tester) async {
    final fake = await open(tester)..viewer = true;
    await save(tester);
    expect(find.textContaining('A viewer reads only.'), findsOneWidget);
    expect(fake.version, 5);
  });

  testWidgets('a failed read offers Try again', (tester) async {
    final fake = SettingsFake();
    fake.api.failWith = 500;
    await open(tester, fake: fake);
    expect(find.byKey(const Key('sms-settings-failed')), findsOneWidget);
    expect(find.text('Try again'), findsOneWidget);
  });

  testWidgets('Arabic at 320 px and 200% text lays out without overflow',
      (tester) async {
    await open(tester,
        size: const Size(320, 6000),
        textScale: 2,
        locale: const Locale('ar'));
    expect(tester.takeException(), isNull);
    expect(find.text('أرقام البطاقات والحسابات'), findsWidgets);
    expect(find.text('حفظ'), findsOneWidget);
  });
}
