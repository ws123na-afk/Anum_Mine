// Mail domains on the phone, read only (docs/email.md#mail-domains): the
// Mailcow domains, a live DNS check with a dangerous SPF flagged and the
// records to set, the empty, failed and denied states, from a 320 px phone
// at 200 percent text to 1440 px, in English and Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/mail_center/mail_domains.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/mail_center_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

String _en(String key) => mailCenterEn[key]!;

final _overview = MailDomainsOverview.fromJson(JsonReader({
  'mailcow_state': 'ok',
  'message': null,
  'pending': [
    {'domain': 'example.com'}
  ],
  'domains': [
    {
      'domain': 'example.com',
      'in_mailcow': true,
      'allowed': true,
      'mailboxes': 3,
      'aliases': [
        {'address': 'sales@example.com'}
      ],
    },
    {'domain': 'risky.example', 'in_mailcow': false, 'allowed': true},
    {'domain': 'legacy.example', 'in_mailcow': true, 'allowed': false},
  ],
}));

DomainDnsReport _report() => DomainDnsReport.fromJson(JsonReader({
      'mx': {
        'status': 'ok',
        'code': 'mx_ok',
        'params': {'host': 'mail.example.com'},
        'message': 'MX points to mail.example.com.',
        'found': ['10 mail.example.com']
      },
      'spf': {
        'status': 'danger',
        'code': 'spf_dangerous_all',
        'message': "Dangerous: '+all' lets any server send as this domain.",
        'found': ['v=spf1 +all']
      },
      'dkim': {
        'status': 'missing',
        'code': 'dkim_missing',
        'params': {'name': 'dkim._domainkey'},
        'message': 'No DKIM record.',
        'found': <String>[]
      },
      'dmarc': {
        'status': 'warning',
        'code': 'a_code_from_a_newer_api',
        'message': "DMARC policy 'none' only reports.",
        'found': <String>[]
      },
      'records': [
        {
          'kind': 'spf',
          'type': 'TXT',
          'name': 'risky.example',
          'value': 'v=spf1 mx ~all',
          'note': 'One SPF record only.',
          'note_code': 'record_spf_one'
        },
      ],
    }));

class FakeDomains implements MailDomainsRepository {
  Object? failure;
  MailDomainsOverview view = _overview;
  final checked = <String>[];

  @override
  Future<MailDomainsOverview> domains() async {
    if (failure != null) throw failure!;
    return view;
  }

  @override
  Future<DomainDnsReport> dns(String domain) async {
    checked.add(domain);
    return _report();
  }
}

Future<void> _pump(WidgetTester tester, FakeDomains repo,
    {Size size = const Size(1440, 900),
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    key: UniqueKey(),
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates
    ],
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context)
            .copyWith(textScaler: TextScaler.linear(textScale)),
        child: child!),
    home: MailDomainsScreen(repository: repo),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('lists domains, checks DNS and flags a dangerous SPF',
      (tester) async {
    final repo = FakeDomains();
    await _pump(tester, repo);
    expect(find.text('example.com'), findsOneWidget);
    expect(find.textContaining(_en('mc.dom.notInMailcow')), findsOneWidget);
    expect(find.textContaining('3 mailboxes · 1 aliases'), findsOneWidget);
    expect(find.textContaining('Not allowed'), findsOneWidget);
    expect(find.text('1 change(s) waiting for approval'), findsOneWidget);
    await tester.tap(find.text(_en('mc.dom.check')).at(1));
    await tester.pumpAndSettle();
    expect(repo.checked, ['risky.example']);
    expect(find.text('SPF: Dangerous'), findsOneWidget);
    expect(find.text('DKIM: Missing'), findsOneWidget);
    expect(find.text(_en('mc.dom.code.spf_dangerous_all')), findsOneWidget);
    expect(find.text(_en('mc.dom.records')), findsOneWidget);
    expect(find.textContaining('v=spf1 mx ~all'), findsOneWidget);
  });

  testWidgets('empty, failed and denied states', (tester) async {
    final empty = FakeDomains()
      ..view = MailDomainsOverview.fromJson(JsonReader({
        'mailcow_state': 'off',
        'domains': <Object>[],
        'pending': <Object>[]
      }));
    await _pump(tester, empty);
    expect(find.text(_en('mc.dom.none')), findsOneWidget);
    expect(find.text(_en('mc.dom.mailcowOff')), findsOneWidget);

    final denied = FakeDomains()
      ..failure = const ApiException(403, 'Platform operator role required.');
    await _pump(tester, denied);
    expect(find.text(_en('mc.dom.denied')), findsOneWidget);

    final down = FakeDomains()
      ..failure = const ApiException(503, 'The database did not answer.');
    await _pump(tester, down);
    expect(find.textContaining('The database did not answer.'), findsWidgets);
  });

  // Tall views so the whole list is built: the check is the width, at 200% text.
  testWidgets('DNS judgements are translated; DNS values are not',
      (tester) async {
    final repo = FakeDomains();
    await _pump(tester, repo,
        size: const Size(1440, 2400), locale: const Locale('ar'));
    await tester.tap(find.byIcon(Icons.travel_explore_outlined).first);
    await tester.pumpAndSettle();
    final ar = mailCenterAr;
    expect(
        find.text(
            ar['mc.dom.code.mx_ok']!.replaceAll('{host}', 'mail.example.com')),
        findsOneWidget);
    expect(find.text(ar['mc.dom.code.spf_dangerous_all']!), findsOneWidget);
    expect(
        find.text(ar['mc.dom.code.dkim_missing']!
            .replaceAll('{name}', 'dkim._domainkey')),
        findsOneWidget);
    // A code this app does not know falls back to the API's sentence.
    expect(find.text("DMARC policy 'none' only reports."), findsOneWidget);
    // The record value stays exactly as DNS needs it; its note is translated.
    expect(
        find.textContaining(
            'v=spf1 mx ~all\n${ar['mc.dom.code.record_spf_one']}'),
        findsOneWidget);
  });

  test('every DNS code has English and Arabic text with the same placeholders',
      () {
    final codes = mailCenterEn.keys.where((k) => k.startsWith('mc.dom.code.'));
    expect(codes.length, greaterThanOrEqualTo(29));
    final placeholder = RegExp(r'\{(\w+)\}');
    for (final key in codes) {
      expect(mailCenterAr.containsKey(key), isTrue, reason: key);
      Set<String> names(String text) =>
          {for (final m in placeholder.allMatches(text)) m.group(1)!};
      expect(names(mailCenterAr[key]!), names(mailCenterEn[key]!), reason: key);
    }
  });

  for (final size in const [
    Size(320, 3200),
    Size(390, 3200),
    Size(1440, 2400)
  ]) {
    for (final locale in const [Locale('en'), Locale('ar')]) {
      testWidgets(
          'fits ${size.width.toInt()} px in ${locale.languageCode} at 200%',
          (tester) async {
        final repo = FakeDomains();
        await _pump(tester, repo, size: size, textScale: 2, locale: locale);
        await tester.tap(find.byIcon(Icons.travel_explore_outlined).first);
        await tester.pumpAndSettle();
        expect(repo.checked, ['example.com']);
        expect(find.textContaining('v=spf1 mx ~all'), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }
  }
}
