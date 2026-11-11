// Ready to sell on the phone (docs/selling-readiness.md#mobile): the
// checklist from the API's real state, missing first, each item's lines and
// what to do next (and where on the web), the SPF and DMARC check, every
// state, Arabic, and 320 to 1024 px at 200 percent.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/selling/selling_data.dart';
import 'package:anum_mobile/features/selling/selling_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'pump.dart';

Map<String, Object?> _readiness() => {
      'items': [
        {
          'key': 'payment_provider',
          'state': 'warning',
          'fix': 'payments',
          'detail': {
            'moyasar': 'off',
            'stripe': 'ready',
            'active': 'stripe',
            'currencies': ['AED'],
            'moyasar_optional': true,
            'live_keys': false,
            'bank_transfer': false
          }
        },
        {
          'key': 'company_details',
          'state': 'missing',
          'fix': 'company',
          'detail': {
            'missing': ['legal_name', 'contact_email']
          }
        },
        {
          'key': 'mail_sending',
          'state': 'done',
          'fix': 'mail',
          'detail': {'mailbox_connected': true, 'smtp_configured': false}
        },
        {
          'key': 'backups_local',
          'state': 'done',
          'fix': 'backups',
          'detail': {
            'recorded': true,
            'ok': true,
            'finished_at': '2026-11-10T02:00:00Z'
          }
        },
        {
          'key': 'backups_offsite',
          'state': 'missing',
          'fix': 'backups',
          'detail': {
            'recorded': true,
            'ok': false,
            'finished_at': '2026-11-10T02:10:00Z',
            'last_success_at': null
          }
        },
        {
          'key': 'legal_pages',
          'state': 'missing',
          'fix': 'legal',
          'detail': {
            'missing': {'terms': 2, 'privacy': 1},
            'published': <String>[]
          }
        },
        {
          'key': 'plans',
          'state': 'done',
          'fix': 'plans',
          'detail': {'active': 2, 'published': 1}
        },
        {
          'key': 'ai_model',
          'state': 'missing',
          'fix': 'env',
          'detail': {
            'provider': 'mock',
            'placeholder': true,
            'key_missing': false
          }
        },
        {
          'key': 'public_issuer',
          'state': 'unknown',
          'fix': 'env',
          'detail': {'configured': false, 'is_you': false, 'web_app_url': true}
        },
      ],
      'done': 3,
      'total': 9,
      'ready': false,
      'mail_domain': 'anum360os.com',
      'checked_at': '2026-11-10T09:00:00Z',
    };

class FakeSelling implements SellingRepository {
  int status = 0;
  final domains = <String>[];

  @override
  Future<Readiness> readiness() async {
    if (status != 0) throw ApiException(status, 'Refused');
    return Readiness.fromJson(JsonReader(_readiness()));
  }

  @override
  Future<DnsResult> checkDns(String domain) async {
    domains.add(domain);
    return DnsResult.fromJson(JsonReader({
      'domain': domain,
      'checked_at': '2026-11-10T09:05:00Z',
      'spf': {
        'record': 'v=spf1 ~all',
        'status': 'no_senders',
        'senders': <String>[],
        'all': '~all',
        'error': null
      },
      'dmarc': {
        'record': 'v=DMARC1; p=none',
        'policy': 'none',
        'status': 'monitor',
        'error': null
      },
    }));
  }
}

void main() {
  test('missing first, a domain as the server takes it', () {
    final r = Readiness.fromJson(JsonReader(_readiness()));
    expect(r.sorted.map((i) => i.state).toList(), [
      'missing',
      'missing',
      'missing',
      'missing',
      'warning',
      'unknown',
      'done',
      'done',
      'done'
    ]);
    expect(r.sorted.first.key, 'company_details');
    expect(cleanDomain(' Anum360OS.com. '), 'anum360os.com');
    expect(cleanDomain('10.0.0.1'), isNull);
    expect(cleanDomain('localhost'), isNull);
    expect(r.items.first.webTab, 'settings');
    expect(r.items.last.webTab, isNull);
  });

  testWidgets('the owner reads each item and checks the mail domain',
      (tester) async {
    final fake = FakeSelling();
    await pumpDesk(tester, SellingScreen(controller: SellingController(fake)));
    expect(find.text('3 of 9 done'), findsOneWidget);
    expect(find.text('Empty: Legal name, Contact email'), findsOneWidget);
    expect(find.text('Card payments go through Stripe (AED).'), findsOneWidget);
    expect(
        find.text(
            'Only test keys are saved, so no real money can be taken yet.'),
        findsOneWidget);
    expect(find.text('It has never succeeded.'), findsOneWidget);
    expect(find.text('3 facts are not set yet.'), findsOneWidget);
    expect(
        find.text(
            'The server uses the placeholder model, so every answer is sample text.'),
        findsOneWidget);
    expect(find.text('On the web: Platform billing, Company and legal.'),
        findsNWidgets(2));
    expect(
        find.text('On the web: Platform billing, Settings.'), findsOneWidget);
    // A done item says no fix.
    expect(
        find.descendant(
            of: find.byKey(const Key('rts-item-plans')),
            matching: find.textContaining('To fix:', findRichText: true)),
        findsNothing);

    // The domain comes from the checklist; a bad one is refused locally.
    await tester.enterText(find.byKey(const Key('rts-domain')), 'not a domain');
    await tapDesk(tester, find.byKey(const Key('rts-dns-check')));
    expect(find.text('Enter a domain such as example.com.'), findsOneWidget);
    expect(fake.domains, isEmpty);
    await tester.enterText(
        find.byKey(const Key('rts-domain')), 'anum360os.com');
    await tapDesk(tester, find.byKey(const Key('rts-dns-check')));
    expect(fake.domains, ['anum360os.com']);
    expect(find.text('Allows no sender'), findsOneWidget);
    expect(find.text('Monitoring only'), findsOneWidget);
    expect(find.text('v=spf1 ~all'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('who may see it, and errors with a retry', (tester) async {
    final fake = FakeSelling()..status = 403;
    final controller = SellingController(fake);
    await pumpDesk(tester, SellingScreen(controller: controller));
    expect(find.byKey(const Key('rts-denied')), findsOneWidget);
    expect(
        find.text(
            'Only the platform owner (realm role platform_operator) can see this checklist.'),
        findsOneWidget);
    fake.status = 500;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('rts-error')), findsOneWidget);
    fake.status = 0;
    await tapDesk(tester, find.text('Try again'));
    expect(find.text('3 of 9 done'), findsOneWidget);
  });

  testWidgets('reads in Arabic, right to left', (tester) async {
    await pumpDesk(
        tester, SellingScreen(controller: SellingController(FakeSelling())),
        locale: const Locale('ar'));
    expect(find.text('جاهز للبيع'), findsWidgets);
    expect(Directionality.of(tester.element(find.byType(SellingScreen))),
        TextDirection.rtl);
  });

  for (final (size, scale, locale) in deskSizes) {
    testWidgets(
        'fits at ${size.width} px, text x$scale, ${locale.languageCode}',
        (tester) async {
      final fake = FakeSelling();
      final controller = SellingController(fake);
      await pumpDesk(tester, SellingScreen(controller: controller),
          size: size, textScale: scale, locale: locale);
      await controller.check('anum360os.com');
      await tester.pumpAndSettle();
      await tester.drag(find.byType(Scrollable).first, const Offset(0, -40000));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }
}
